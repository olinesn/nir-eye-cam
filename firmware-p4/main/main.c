// NIR eye camera, ESP32-P4 + ST VD55G1: MIPI CSI-2 -> PSRAM -> USB-HS CDC.
//
// Same packet protocol as the RP2040 build (common/nec_protocol.h), so
// host/nireyecam records and aligns it unchanged. Commands are the RP2040
// set, with sensor-specific ranges, plus four new ones:
//
//   PING | INFO | INIT | START | STOP
//   RES <W>x<H>      crop size, centred on the sensor (stopped only); W*H % 8 == 0
//   ROI <X> <Y>      crop position (stopped only)
//   EXP <lines>      manual exposure, in sensor lines
//   AGAIN <0-28>     analog gain code: gain = 32 / (32 - code)
//   DGAIN <q8>       digital gain, 256 = 1x
//   AE <0|1>         sensor auto exposure (leave off for pupillometry)
//   FLL <lines>      frame length: sets the frame rate
//   LED <0-1000>     IR enable: >0 lets the sensor's STROBE drive the LEDs
//   SYNCGEN <0|1>    drive SYNC_OUT with the random pulse train
//   TRIG <hz>        frame trigger on TRIG_OUT for every sensor's GPIO0 (0 = off)
//   EXTSYNC <0|1>    start each frame on a GPIO0 pulse, not the sensor's timer (stopped only)
//
// Every command gets one reply, "OK <CMD> ..." or "ERR <CMD> <reason>". If the
// host closes the port, streaming stops and the IR is disabled.

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "board.h"
#include "capture.h"
#include "nec_protocol.h"
#include "syncio.h"
#include "usb_link.h"
#include "vd55g1.h"

#define FW_VERSION "0.3.0-p4"
#define CMD_MAX    96
#define TEXT_MAX   640

static const char* TAG = "main";

static vd55g1_t s_sensor;
static esp_err_t s_cam_err = ESP_FAIL;
static char s_uid[13];
static QueueHandle_t s_cmd_q;
static uint32_t s_text_seq;

// ------------------------------------------------------------- outgoing

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

// Blocks until everything is queued; false if the host went away meanwhile.
static bool send_all(const void* data, size_t len)
{
    const uint8_t* p = data;
    while (len) {
        if (!usb_link_connected()) return false;
        size_t n = usb_link_write(p, len);
        p += n;
        len -= n;
        if (len) {
            usb_link_flush();
            vTaskDelay(1);  // FIFO full: let the endpoint drain. Throughput knob.
        }
    }
    return true;
}

// Returns 0 so command handlers can `return reply(...)`.
static int reply(const char* fmt, ...)
{
    static char text[TEXT_MAX];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    if (n < 0) return 0;
    if (n >= (int)sizeof(text)) n = sizeof(text) - 1;
    nec_header_t h;
    header_init(&h, NEC_PKT_TEXT, n);
    h.seq = ++s_text_seq;
    h.t_us = esp_timer_get_time();
    if (send_all(&h, sizeof(h))) send_all(text, n);
    return 0;
}

static void send_sync(const sync_event_t* ev)
{
    nec_header_t h;
    header_init(&h, NEC_PKT_SYNC, 0);
    h.seq = ev->seq;
    h.t_us = ev->t_us;
    send_all(&h, sizeof(h));
}

static void send_frame(const cap_frame_t* f)
{
    nec_header_t h;
    header_init(&h, NEC_PKT_FRAME, f->len);
    h.flags = s_sensor.ae ? NEC_FLAG_AE : 0;
    h.seq = f->seq;
    h.t_us = f->t_us;
    h.width = f->width;
    h.height = f->height;
    h.exposure = s_sensor.exposure;
    h.again = s_sensor.again;
    if (send_all(&h, sizeof(h))) send_all(f->data, f->len);
}

// ------------------------------------------------------------- commands

static bool cam_ok(void) { return s_cam_err == ESP_OK; }

static int reply_info(void)
{
    bool ok = cam_ok();
    return reply("OK INFO {\"fw\":\"%s\",\"board\":\"%s\",\"uid\":\"%s\",\"sensor\":\"vd55g1\","
          "\"model_id\":\"0x%08lx\",\"revision\":\"0x%04x\",\"cam_ok\":%d,\"cam_error\":\"%s\","
          "\"width\":%u,\"height\":%u,\"x\":%u,\"y\":%u,\"exposure\":%u,\"frame_length\":%u,"
          "\"line_time_ns\":%lu,\"frame_period_us\":%lu,\"again\":%u,\"dgain\":%u,\"ae\":%d,"
          "\"led\":%d,\"syncgen\":%d,\"trig_hz\":%lu,\"extsync\":%d,\"streaming\":%d,"
          "\"slots\":%lu,\"csi_errors\":%lu,\"sync_edges\":%lu,\"sync_overflows\":%lu}",
          FW_VERSION, NEC_BOARD_NAME, s_uid, (unsigned long)s_sensor.model_id, (unsigned)s_sensor.revision, ok,
          ok ? "" : esp_err_to_name(s_cam_err), s_sensor.w, s_sensor.h, s_sensor.x, s_sensor.y,
          s_sensor.exposure, s_sensor.frame_length,
          (unsigned long)(ok ? vd55g1_line_time_ns(&s_sensor) : 0),
          (unsigned long)(ok ? vd55g1_frame_period_us(&s_sensor) : 0), s_sensor.again,
          s_sensor.dgain, s_sensor.ae, led_enabled(), syncgen_on(), (unsigned long)trig_hz(),
          s_sensor.extsync, s_sensor.streaming, (unsigned long)capture_slots(),
          (unsigned long)capture_errors(), (unsigned long)sync_edge_count(),
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

static void stop_streaming(void)
{
    if (!cam_ok()) return;
    vd55g1_stop(&s_sensor);
    capture_stop();
}

static esp_err_t open_camera(void)
{
    capture_deinit();
    esp_err_t err = vd55g1_open(&s_sensor, PIN_I2C_SDA, PIN_I2C_SCL, SENSOR_I2C_ADDR, SENSOR_I2C_HZ,
                                PIN_XSHUTDOWN, SENSOR_EXT_CLK_HZ, MIPI_LANE_MBPS);
    if (err == ESP_OK) err = vd55g1_set_io(&s_sensor, SENSOR_IO_STROBE, VD55G1_IO_STROBE);
    if (err == ESP_OK) err = vd55g1_set_io(&s_sensor, SENSOR_IO_FSYNC_OUT, VD55G1_IO_FSYNC_OUT);
    if (err == ESP_OK) err = vd55g1_set_external_sync(&s_sensor, false);  // free-run until EXTSYNC 1
    if (err == ESP_OK) err = vd55g1_set_ae(&s_sensor, false);
    if (err == ESP_OK) err = capture_init(s_sensor.w, s_sensor.h, MIPI_LANES, MIPI_LANE_MBPS);
    return err;
}

static int handle_command(char* cmdline)
{
    char* save;
    char* cmd = strtok_r(cmdline, " \t", &save);
    char* arg = strtok_r(NULL, " \t", &save);
    char* arg2 = strtok_r(NULL, " \t", &save);
    if (!cmd) return 0;
    for (char* p = cmd; *p; p++) *p = (char)toupper((unsigned char)*p);
    unsigned long v, v2;
    esp_err_t err;

    if (!strcmp(cmd, "PING")) return reply("OK PING");
    if (!strcmp(cmd, "INFO")) return reply_info();
    if (!strcmp(cmd, "LED")) {
        if (!parse_uint(arg, 1000, &v)) return reply("ERR LED expected 0-1000");
        led_enable(v > 0);
        return reply("OK LED %d", led_enabled() ? 1000 : 0);
    }
    if (!strcmp(cmd, "SYNCGEN")) {
        if (!parse_uint(arg, 1, &v)) return reply("ERR SYNCGEN expected 0 or 1");
        syncgen_set(v != 0);
        return reply("OK SYNCGEN %d", syncgen_on());
    }
    if (!strcmp(cmd, "TRIG")) {
        if (!parse_uint(arg, 1000, &v)) return reply("ERR TRIG expected 0-1000 Hz");
        err = trig_set(v);
        return err ? reply("ERR TRIG %s", esp_err_to_name(err)) : reply("OK TRIG %lu", (unsigned long)trig_hz());
    }
    if (!strcmp(cmd, "INIT")) {
        if (cam_ok() && s_sensor.streaming) return reply("ERR INIT stop first");
        s_cam_err = open_camera();
        return cam_ok() ? reply("OK INIT") : reply("ERR INIT %s", esp_err_to_name(s_cam_err));
    }

    // Everything below needs a working sensor.
    if (!cam_ok()) return reply("ERR %s camera not ready: %s", cmd, esp_err_to_name(s_cam_err));

    if (!strcmp(cmd, "START")) {
        if (s_sensor.streaming) return reply("OK START");
        err = capture_start();
        if (!err) err = vd55g1_start(&s_sensor);
        return err ? reply("ERR START %s", esp_err_to_name(err)) : reply("OK START");
    }
    if (!strcmp(cmd, "STOP")) {
        stop_streaming();
        return reply("OK STOP");
    }
    if (!strcmp(cmd, "RES")) {
        unsigned w, h;
        if (!arg || sscanf(arg, "%ux%u", &w, &h) != 2) return reply("ERR RES expected WxH");
        if (s_sensor.streaming) return reply("ERR RES stop first");
        if (w > VD55G1_FULL_W || h > VD55G1_FULL_H || (w * h) % 8)
            return reply("ERR RES max %ux%u, W*H a multiple of 8", VD55G1_FULL_W, VD55G1_FULL_H);
        capture_deinit();
        err = vd55g1_set_crop(&s_sensor, (VD55G1_FULL_W - w) / 2, (VD55G1_FULL_H - h) / 2, w, h);
        if (!err) err = capture_init(w, h, MIPI_LANES, MIPI_LANE_MBPS);
        return err ? reply("ERR RES %s", esp_err_to_name(err)) : reply("OK RES %ux%u", w, h);
    }
    if (!strcmp(cmd, "ROI")) {
        if (!parse_uint(arg, VD55G1_FULL_W, &v) || !parse_uint(arg2, VD55G1_FULL_H, &v2))
            return reply("ERR ROI expected X Y");
        if (s_sensor.streaming) return reply("ERR ROI stop first");
        err = vd55g1_set_crop(&s_sensor, v, v2, s_sensor.w, s_sensor.h);
        return err ? reply("ERR ROI %s", esp_err_to_name(err)) : reply("OK ROI %u %u", s_sensor.x, s_sensor.y);
    }
    if (!strcmp(cmd, "EXP")) {
        if (!parse_uint(arg, 0xffff, &v)) return reply("ERR EXP expected lines");
        vd55g1_set_exposure(&s_sensor, v);
        return reply("OK EXP %u", s_sensor.exposure);
    }
    if (!strcmp(cmd, "AGAIN")) {
        if (!parse_uint(arg, 28, &v)) return reply("ERR AGAIN expected 0-28");
        vd55g1_set_again(&s_sensor, v);
        return reply("OK AGAIN %u", s_sensor.again);
    }
    if (!strcmp(cmd, "DGAIN")) {
        if (!parse_uint(arg, 0xffff, &v)) return reply("ERR DGAIN expected q8 (256 = 1x)");
        vd55g1_set_dgain(&s_sensor, v);
        return reply("OK DGAIN %u", s_sensor.dgain);
    }
    if (!strcmp(cmd, "AE")) {
        if (!parse_uint(arg, 1, &v)) return reply("ERR AE expected 0 or 1");
        vd55g1_set_ae(&s_sensor, v != 0);
        return reply("OK AE %d", s_sensor.ae);
    }
    if (!strcmp(cmd, "FLL")) {
        if (!parse_uint(arg, 0xffff, &v)) return reply("ERR FLL expected lines");
        vd55g1_set_frame_length(&s_sensor, v);
        return reply("OK FLL %u EXP %u", s_sensor.frame_length, s_sensor.exposure);
    }
    if (!strcmp(cmd, "EXTSYNC")) {
        if (!parse_uint(arg, 1, &v)) return reply("ERR EXTSYNC expected 0 or 1");
        if (s_sensor.streaming) return reply("ERR EXTSYNC stop first");
        err = vd55g1_set_external_sync(&s_sensor, v != 0);
        return err ? reply("ERR EXTSYNC %s", esp_err_to_name(err)) : reply("OK EXTSYNC %d", s_sensor.extsync);
    }
    return reply("ERR %s unknown command", cmd);
}

// USB task context: assemble lines, hand them to the streaming task.
static void on_usb_rx(const uint8_t* data, size_t len)
{
    static char line[CMD_MAX];
    static size_t n;
    static bool overflow;
    for (size_t i = 0; i < len; i++) {
        char c = (char)data[i];
        if (c == '\n' || c == '\r') {
            if (n && !overflow) {
                line[n] = 0;
                xQueueSend(s_cmd_q, line, 0);  // dropped if 8 are already waiting
            }
            n = 0;
            overflow = false;
        } else if (n < CMD_MAX - 1) {
            line[n++] = c;
        } else {
            overflow = true;
        }
    }
}

// ------------------------------------------------------------- main loop

static void on_disconnect(void)
{
    stop_streaming();
    led_enable(false);
    cap_frame_t f;
    int slot;
    while (capture_take(&f, &slot, 0)) capture_release(slot);
    sync_event_t ev;
    while (sync_take_event(&ev)) {}
    xQueueReset(s_cmd_q);
}

static void stream_task(void* arg)
{
    bool connected = false;
    char line[CMD_MAX];
    for (;;) {
        bool now = usb_link_connected();
        if (connected && !now) on_disconnect();
        connected = now;
        if (!connected) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        // Commands and replies only between packets, so a reply never lands
        // inside a frame.
        while (xQueueReceive(s_cmd_q, line, 0) == pdTRUE) handle_command(line);

        sync_event_t ev;
        if (sync_take_event(&ev)) {
            send_sync(&ev);
            continue;
        }
        cap_frame_t f;
        int slot;
        if (capture_take(&f, &slot, pdMS_TO_TICKS(5))) {
            send_frame(&f);
            capture_release(slot);
        }
        usb_link_flush();
    }
}

void app_main(void)
{
    uint8_t mac[6] = {0};
    esp_efuse_mac_get_default(mac);
    snprintf(s_uid, sizeof(s_uid), "%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    uint32_t seed = 2166136261u;  // FNV-1a of the MAC: a different pulse train per board
    for (int i = 0; i < 6; i++) seed = (seed ^ mac[i]) * 16777619u;

    s_cmd_q = xQueueCreate(8, CMD_MAX);
    ESP_ERROR_CHECK(syncio_init(seed));
    s_cam_err = open_camera();
    if (s_cam_err) ESP_LOGE(TAG, "camera: %s (send INIT to retry)", esp_err_to_name(s_cam_err));
    ESP_ERROR_CHECK(usb_link_init(on_usb_rx));

    xTaskCreatePinnedToCore(stream_task, "stream", 6144, NULL, 10, NULL, 1);
}
