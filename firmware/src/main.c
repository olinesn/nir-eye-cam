// NIR eye camera: HM01B0 -> RP2040 -> USB CDC.
//
// Host -> device: newline-terminated ASCII commands. Every command gets
// exactly one text reply, "OK <CMD> ..." or "ERR <CMD> <reason>":
//
//   PING                     OK PING
//   INFO                     OK INFO {json}
//   INIT                     retry sensor detection
//   RES <W>x<H>              160x120 (2x2 binned) | 320x240 | 320x320; stopped only
//   EXP <lines>              integration time, in sensor lines
//   AGAIN <0-3>              analog gain 1x/2x/4x/8x
//   AE <0|1>                 sensor auto exposure (leave off for pupillometry)
//   FLL <lines>              frame length in lines; longer = lower frame rate
//   LED <0-1000>             IR LED PWM duty, per mille
//   SYNCGEN <0|1>            drive the shared sync line with a random pulse train
//   START | STOP             stream frames
//
// Device -> host: binary packets (protocol.h). Commands are handled only
// between packets, so a reply never lands inside a frame.
//
// If the host closes the port the device stops streaming and switches the
// IR LED off.

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/unique_id.h"
#include "tusb.h"

#include "board_pins.h"
#include "camera.h"
#include "nec_protocol.h"
#include "syncio.h"

#define FW_VERSION "0.1.0"

#define TEXT_SLOTS 4
#define TEXT_MAX   480
#define CMD_SLOTS  4
#define CMD_MAX    96

static int s_cam_rc;
static char s_uid[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];

// ------------------------------------------------------------- outgoing

static struct {
    bool active;
    nec_header_t hdr;
    const uint8_t* payload;
    uint32_t total, off;
    int slot;  // frame pool slot to release when sent, or -1
} tx;

static char tx_text[TEXT_MAX];
static char text_q[TEXT_SLOTS][TEXT_MAX];
static unsigned text_head, text_tail;
static uint32_t text_seq;

static void reply(const char* fmt, ...)
{
    if (text_head - text_tail >= TEXT_SLOTS) return;  // host is not reading; drop
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text_q[text_head % TEXT_SLOTS], TEXT_MAX, fmt, ap);
    va_end(ap);
    text_head++;
}

static void header_init(nec_header_t* h, uint8_t type, uint32_t payload_len)
{
    memset(h, 0, sizeof(*h));
    h->magic[0] = NEC_MAGIC0;
    h->magic[1] = NEC_MAGIC1;
    h->magic[2] = NEC_MAGIC2;
    h->magic[3] = NEC_MAGIC3;
    h->type = type;
    h->header_size = NEC_HEADER_SIZE;
    h->payload_len = payload_len;
}

static void tx_start(const uint8_t* payload, uint32_t len, int slot)
{
    tx.payload = payload;
    tx.total = NEC_HEADER_SIZE + len;
    tx.off = 0;
    tx.slot = slot;
    tx.active = true;
}

static void tx_pump(void)
{
    while (tx.active) {
        uint32_t avail = tud_cdc_write_available();
        if (!avail) break;
        const uint8_t* src;
        uint32_t n;
        if (tx.off < NEC_HEADER_SIZE) {
            src = (const uint8_t*)&tx.hdr + tx.off;
            n = NEC_HEADER_SIZE - tx.off;
        } else {
            src = tx.payload + (tx.off - NEC_HEADER_SIZE);
            n = tx.total - tx.off;
        }
        if (n > avail) n = avail;
        uint32_t w = tud_cdc_write(src, n);
        tx.off += w;
        if (tx.off >= tx.total) {
            tx.active = false;
            if (tx.slot >= 0) cam_release_frame(tx.slot);
        }
        if (w < n) break;
    }
    tud_cdc_write_flush();
}

static void tx_abort(void)
{
    if (tx.active && tx.slot >= 0) cam_release_frame(tx.slot);
    tx.active = false;
    tud_cdc_write_clear();
}

static bool tx_next(void)
{
    if (text_tail != text_head) {
        strcpy(tx_text, text_q[text_tail % TEXT_SLOTS]);
        text_tail++;
        uint32_t len = strlen(tx_text);
        header_init(&tx.hdr, NEC_PKT_TEXT, len);
        tx.hdr.seq = ++text_seq;
        tx.hdr.t_us = time_us_64();
        tx_start((const uint8_t*)tx_text, len, -1);
        return true;
    }

    sync_event_t ev;
    if (sync_take_event(&ev)) {
        header_init(&tx.hdr, NEC_PKT_SYNC, 0);
        tx.hdr.seq = ev.seq;
        tx.hdr.t_us = ev.t_us;
        tx_start(NULL, 0, -1);
        return true;
    }

    cam_frame_t f;
    int slot;
    if (cam_take_frame(&f, &slot)) {
        header_init(&tx.hdr, NEC_PKT_FRAME, f.len);
        tx.hdr.flags = f.ae ? NEC_FLAG_AE : 0;
        tx.hdr.seq = f.seq;
        tx.hdr.t_us = f.t_us;
        tx.hdr.width = f.width;
        tx.hdr.height = f.height;
        tx.hdr.exposure = f.exposure;
        tx.hdr.again = f.again;
        tx_start(f.data, f.len, slot);
        return true;
    }
    return false;
}

// ------------------------------------------------------------- commands

static char cmd_q[CMD_SLOTS][CMD_MAX];
static unsigned cmd_head, cmd_tail;
static char line[CMD_MAX];
static unsigned line_len;
static bool line_overflow;

static void rx_poll(void)
{
    uint8_t buf[64];
    while (tud_cdc_available()) {
        uint32_t n = tud_cdc_read(buf, sizeof(buf));
        for (uint32_t i = 0; i < n; i++) {
            char c = (char)buf[i];
            if (c == '\n' || c == '\r') {
                if (line_len && !line_overflow && cmd_head - cmd_tail < CMD_SLOTS) {
                    line[line_len] = 0;
                    strcpy(cmd_q[cmd_head % CMD_SLOTS], line);
                    cmd_head++;
                } else if (line_len) {
                    reply("ERR ? command too long or queue full");
                }
                line_len = 0;
                line_overflow = false;
            } else if (line_len < CMD_MAX - 1) {
                line[line_len++] = c;
            } else {
                line_overflow = true;
            }
        }
    }
}

static const char* cam_error_text(int rc)
{
    switch (rc) {
    case 0: return "";
    case -2: return "sensor not found on I2C (check SDA/SCL/power)";
    case -3: return "sensor reset timed out";
    default: return "camera init failed";
    }
}

static void reply_info(void)
{
    bool ok = s_cam_rc == 0;
    reply("OK INFO {\"fw\":\"%s\",\"board\":\"%s\",\"uid\":\"%s\",\"cam_ok\":%d,"
          "\"cam_error\":\"%s\",\"width\":%u,\"height\":%u,\"exposure\":%u,"
          "\"frame_length\":%u,\"again\":%u,\"ae\":%d,\"led\":%u,\"syncgen\":%d,"
          "\"streaming\":%d,\"slots\":%lu,\"sync_edges\":%lu,\"sync_overflows\":%lu}",
          FW_VERSION, NEC_BOARD_NAME, s_uid, ok, cam_error_text(s_cam_rc),
          ok ? cam_width() : 0, ok ? cam_height() : 0, ok ? cam_exposure() : 0,
          ok ? cam_frame_length() : 0, ok ? cam_again() : 0, ok ? cam_ae() : 0,
          led_get(), syncgen_on(), ok ? cam_streaming() : 0,
          (unsigned long)(ok ? cam_slots() : 0), (unsigned long)sync_edge_count(),
          (unsigned long)sync_overflows());
}

static bool parse_uint(const char* s, unsigned long max, unsigned long* out)
{
    if (!s || !*s) return false;
    char* end;
    unsigned long v = strtoul(s, &end, 0);
    if (*end || v > max) return false;
    *out = v;
    return true;
}

static void handle_command(char* cmdline)
{
    char* cmd = strtok(cmdline, " \t");
    char* arg = strtok(NULL, " \t");
    if (!cmd) return;
    for (char* p = cmd; *p; p++) *p = (char)toupper((unsigned char)*p);
    unsigned long v;

    if (!strcmp(cmd, "PING")) { reply("OK PING"); return; }
    if (!strcmp(cmd, "INFO")) { reply_info(); return; }
    if (!strcmp(cmd, "LED")) {
        if (!parse_uint(arg, 1000, &v)) { reply("ERR LED expected 0-1000"); return; }
        led_set((uint16_t)v);
        reply("OK LED %u", led_get());
        return;
    }
    if (!strcmp(cmd, "SYNCGEN")) {
        if (!parse_uint(arg, 1, &v)) { reply("ERR SYNCGEN expected 0 or 1"); return; }
        syncgen_set(v != 0);
        reply("OK SYNCGEN %d", syncgen_on());
        return;
    }
    if (!strcmp(cmd, "INIT")) {
        if (s_cam_rc == 0 && cam_streaming()) { reply("ERR INIT stop first"); return; }
        s_cam_rc = cam_init(160, 120);
        if (s_cam_rc) reply("ERR INIT %s", cam_error_text(s_cam_rc));
        else reply("OK INIT");
        return;
    }

    // Everything below needs a working sensor.
    if (s_cam_rc != 0) { reply("ERR %s %s", cmd, cam_error_text(s_cam_rc)); return; }

    if (!strcmp(cmd, "START")) { cam_start(); reply("OK START"); return; }
    if (!strcmp(cmd, "STOP")) { cam_stop(); reply("OK STOP"); return; }
    if (!strcmp(cmd, "RES")) {
        unsigned w, h;
        if (!arg || sscanf(arg, "%ux%u", &w, &h) != 2) { reply("ERR RES expected WxH"); return; }
        int rc = cam_set_resolution((uint16_t)w, (uint16_t)h);
        if (rc == -4) reply("ERR RES stop first");
        else if (rc) reply("ERR RES supported: 160x120 320x240 320x320");
        else reply("OK RES %ux%u", cam_width(), cam_height());
        return;
    }
    if (!strcmp(cmd, "EXP")) {
        if (!parse_uint(arg, 0xffff, &v)) { reply("ERR EXP expected lines"); return; }
        cam_set_exposure((uint16_t)v);
        reply("OK EXP %u", cam_exposure());
        return;
    }
    if (!strcmp(cmd, "AGAIN")) {
        if (!parse_uint(arg, 3, &v)) { reply("ERR AGAIN expected 0-3"); return; }
        cam_set_again((uint8_t)v);
        reply("OK AGAIN %u", cam_again());
        return;
    }
    if (!strcmp(cmd, "AE")) {
        if (!parse_uint(arg, 1, &v)) { reply("ERR AE expected 0 or 1"); return; }
        cam_set_ae(v != 0);
        reply("OK AE %d", cam_ae());
        return;
    }
    if (!strcmp(cmd, "FLL")) {
        if (!parse_uint(arg, 0xffff, &v)) { reply("ERR FLL expected lines"); return; }
        cam_set_frame_length((uint16_t)v);
        reply("OK FLL %u EXP %u", cam_frame_length(), cam_exposure());
        return;
    }
    reply("ERR %s unknown command", cmd);
}

// ------------------------------------------------------------- main

static void gpio_irq(uint gpio, uint32_t events)
{
    (void)events;
    uint64_t t = time_us_64();
    if (gpio == NEC_PIN_VSYNC) cam_on_vsync(t);
    else if (gpio == NEC_PIN_SYNC_IN) sync_on_edge(t);
}

static void on_disconnect(void)
{
    if (s_cam_rc == 0) cam_stop();
    led_set(0);
    tx_abort();
    cam_frame_t f;
    int slot;
    while (cam_take_frame(&f, &slot)) cam_release_frame(slot);
    sync_event_t ev;
    while (sync_take_event(&ev)) {}
    text_head = text_tail = 0;
    cmd_head = cmd_tail = 0;
    line_len = 0;
}

int main(void)
{
    pico_get_unique_board_id_string(s_uid, sizeof(s_uid));
    uint32_t seed = 2166136261u;  // FNV-1a of the board ID: a different pulse train per board
    for (const char* p = s_uid; *p; p++) seed = (seed ^ (uint8_t)*p) * 16777619u;

    tusb_init();
    syncio_init(seed);
    s_cam_rc = cam_init(160, 120);

    gpio_set_irq_enabled_with_callback(NEC_PIN_VSYNC, GPIO_IRQ_EDGE_RISE, true, gpio_irq);
    gpio_set_irq_enabled(NEC_PIN_SYNC_IN, GPIO_IRQ_EDGE_RISE, true);

    bool connected = false;
    while (true) {
        tud_task();
        if (s_cam_rc == 0) cam_poll();

        bool now = tud_cdc_connected();
        if (connected && !now) on_disconnect();
        connected = now;
        if (!connected) continue;

        rx_poll();
        while (!tx.active) {
            if (cmd_tail != cmd_head) {
                handle_command(cmd_q[cmd_tail % CMD_SLOTS]);
                cmd_tail++;
                continue;
            }
            if (!tx_next()) break;
        }
        tx_pump();
    }
}
