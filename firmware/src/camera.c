// HM01B0 continuous capture for RP2040.
//
// Sensor register values and the PIO program are adapted from Arm's
// hm01b0-library-for-pico (MIT, Copyright 2023 Arm Limited; see
// ../LICENSE-hm01b0-library-for-pico). The difference is the capture loop:
// that library starts the sensor, reads ONE frame and stops it again, which
// is fine for a demo but caps the frame rate and gives no timing. Here the
// sensor streams continuously and DMA completion immediately re-arms the PIO
// into the next free slot, so frames are captured back to back. Every frame
// carries the device time of its own VSYNC rising edge, which is what lets
// several free-running cameras be put on one timeline afterwards.

#include <string.h>

#include "hardware/dma.h"
#include "hardware/i2c.h"
#include "hardware/irq.h"
#include "hardware/pio.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"

#include "board_pins.h"
#include "camera.h"

#define HM01B0_ADDR   0x24
#define POOL_BYTES    (2u * 320u * 320u)   // two full-resolution frames
#define MAX_SLOTS     16u
#define BORDER_PX     2u
#define PCLK_PER_PX   (8u / NEC_DATA_BITS)

// Registers (Himax HM01B0 register map, as used by Arm's and SparkFun's drivers)
#define REG_MODEL_ID       0x0000
#define REG_MODE_SELECT    0x0100  // 0 standby, 1 streaming
#define REG_SW_RESET       0x0103
#define REG_GRP_PARAM_HOLD 0x0104  // write 1 to apply the registers below at the next frame
#define REG_INTEGRATION_H  0x0202  // coarse integration time, in lines (16-bit)
#define REG_ANALOG_GAIN    0x0205  // [6:4]: 0=1x 1=2x 2=4x 3=8x
#define REG_FRAME_LENGTH   0x0340  // frame length in lines (16-bit)
#define REG_LINE_LENGTH    0x0342  // line length in PCLKs (16-bit)
#define REG_READOUT_X      0x0383
#define REG_READOUT_Y      0x0387
#define REG_BINNING_MODE   0x0390
#define REG_QVGA_WIN_EN    0x3010
#define REG_BIT_CONTROL    0x3059  // 0x22 = 1-bit serial output
#define REG_OSC_CLK_DIV    0x3060
#define REG_AE_CTRL        0x2100  // [0]: sensor auto exposure

typedef struct {
    uint16_t w, h;
    uint8_t readout_x, readout_y, binning, qvga_win;
    uint16_t frame_length, line_length;
} res_cfg_t;

static const res_cfg_t RES_TABLE[] = {
    {320, 320, 0x01, 0x01, 0x00, 0x00, 0x0158, 0x0178},
    {320, 240, 0x01, 0x01, 0x00, 0x01, 0x0104, 0x0178},
    {160, 120, 0x03, 0x03, 0x03, 0x01, 0x0080, 0x00D7},  // 2x2 binned
};

// Single-producer / single-consumer ring of slot indices. Both ends run on
// core 0 (main loop vs DMA IRQ); the main loop masks the DMA IRQ whenever it
// does anything other than its own end of a ring.
typedef struct {
    uint8_t buf[MAX_SLOTS];
    volatile uint32_t head, tail;
} ring_t;

static bool ring_push(ring_t* r, uint8_t v)
{
    uint32_t h = r->head;
    if (h - r->tail >= MAX_SLOTS) return false;
    r->buf[h % MAX_SLOTS] = v;
    __dmb();
    r->head = h + 1;
    return true;
}

static bool ring_pop(ring_t* r, uint8_t* v)
{
    uint32_t t = r->tail;
    if (t == r->head) return false;
    *v = r->buf[t % MAX_SLOTS];
    __dmb();
    r->tail = t + 1;
    return true;
}

static uint8_t s_pool[POOL_BYTES] __attribute__((aligned(4)));
static cam_frame_t s_meta[MAX_SLOTS];
static ring_t s_free, s_filled;
static uint32_t s_nslots, s_frame_bytes;

static const res_cfg_t* s_res;
static uint16_t s_frame_length, s_exposure;
static uint8_t s_again;
static bool s_ae;

static PIO s_pio = pio0;
static uint s_sm, s_prog_offset;
static pio_sm_config s_smc;
static int s_dma = -1;
static dma_channel_config s_dcc;

static volatile bool s_streaming;
static volatile int s_armed = -1;
static volatile uint32_t s_vsync_count;
static volatile uint64_t s_vsync_t_us;

// ---------------------------------------------------------------- I2C

static void write_reg8(uint16_t reg, uint8_t v)
{
    uint8_t d[3] = {reg >> 8, reg & 0xff, v};
    i2c_write_blocking(NEC_I2C, HM01B0_ADDR, d, 3, false);
}

static void write_reg16(uint16_t reg, uint16_t v)
{
    uint8_t d[4] = {reg >> 8, reg & 0xff, v >> 8, v & 0xff};
    i2c_write_blocking(NEC_I2C, HM01B0_ADDR, d, 4, false);
}

static int read_reg8(uint16_t reg)
{
    uint8_t a[2] = {reg >> 8, reg & 0xff}, v = 0;
    if (i2c_write_blocking(NEC_I2C, HM01B0_ADDR, a, 2, false) != 2) return -1;
    if (i2c_read_blocking(NEC_I2C, HM01B0_ADDR, &v, 1, false) != 1) return -1;
    return v;
}

// ---------------------------------------------------------------- slots

static void reset_pool(void)
{
    s_frame_bytes = (uint32_t)s_res->w * s_res->h;
    s_nslots = POOL_BYTES / s_frame_bytes;
    if (s_nslots > MAX_SLOTS) s_nslots = MAX_SLOTS;
    s_free.head = s_free.tail = 0;
    s_filled.head = s_filled.tail = 0;
    for (uint8_t i = 0; i < s_nslots; i++) ring_push(&s_free, i);
}

static void arm_slot(int slot)
{
    pio_sm_init(s_pio, s_sm, s_prog_offset, &s_smc);  // leaves the SM disabled, PC at the program start
    dma_channel_configure(s_dma, &s_dcc, s_pool + (uint32_t)slot * s_frame_bytes,
                          ((uint8_t*)&s_pio->rxf[s_sm]) + 3, s_frame_bytes, true);
    pio_sm_set_enabled(s_pio, s_sm, true);
    pio_sm_put(s_pio, s_sm, s_res->w * PCLK_PER_PX - 1);
    s_armed = slot;
}

static void dma_irq_handler(void)
{
    if (!dma_channel_get_irq0_status(s_dma)) return;
    dma_channel_acknowledge_irq0(s_dma);
    pio_sm_set_enabled(s_pio, s_sm, false);

    int slot = s_armed;
    s_armed = -1;
    if (!s_streaming || slot < 0) return;

    // The SM only started reading after this frame's VSYNC rose, and the
    // last pixel arrives before the next one rises, so the latest edge IS
    // this frame's start. (DMA_IRQ_0 also outranks IO_IRQ_BANK0 if both pend.)
    cam_frame_t* m = &s_meta[slot];
    m->data = s_pool + (uint32_t)slot * s_frame_bytes;
    m->len = s_frame_bytes;
    m->seq = s_vsync_count;
    m->t_us = s_vsync_t_us;
    m->width = s_res->w;
    m->height = s_res->h;
    m->exposure = s_exposure;
    m->again = s_again;
    m->ae = s_ae;
    ring_push(&s_filled, (uint8_t)slot);

    uint8_t next;
    if (ring_pop(&s_free, &next)) arm_slot(next);
    // else: starved -- cam_poll() re-arms once the host has drained a frame.
}

// ---------------------------------------------------------------- sensor

static void apply_resolution(const res_cfg_t* r)
{
    s_res = r;
    s_frame_length = r->frame_length;
    s_exposure = r->line_length / 2;  // Arm's default; set EXP explicitly for real use
    if (s_exposure > s_frame_length - 2) s_exposure = s_frame_length - 2;

    write_reg8(REG_BIT_CONTROL, 0x22);
    write_reg8(REG_READOUT_X, r->readout_x);
    write_reg8(REG_READOUT_Y, r->readout_y);
    write_reg8(REG_BINNING_MODE, r->binning);
    write_reg8(REG_QVGA_WIN_EN, r->qvga_win);
    write_reg16(REG_FRAME_LENGTH, s_frame_length);
    write_reg16(REG_LINE_LENGTH, r->line_length);
    write_reg8(REG_OSC_CLK_DIV, 0x08);
    write_reg8(REG_AE_CTRL, s_ae ? 0x01 : 0x00);
    write_reg16(REG_INTEGRATION_H, s_exposure);
    write_reg8(REG_ANALOG_GAIN, (uint8_t)(s_again << 4));
    write_reg8(REG_GRP_PARAM_HOLD, 0x01);

    reset_pool();
}

static const res_cfg_t* find_res(uint16_t w, uint16_t h)
{
    for (unsigned i = 0; i < sizeof(RES_TABLE) / sizeof(RES_TABLE[0]); i++) {
        if (RES_TABLE[i].w == w && RES_TABLE[i].h == h) return &RES_TABLE[i];
    }
    return NULL;
}

static void load_pio_program(void)
{
    // Same instruction sequence as Arm's library: skip BORDER_PX rows and
    // columns, then shift in width*PCLK_PER_PX bits per line, autopushing bytes.
    static uint16_t insns[18];
    insns[0]  = pio_encode_pull(false, true);
    insns[1]  = pio_encode_wait_gpio(false, NEC_PIN_VSYNC);
    insns[2]  = pio_encode_wait_gpio(true, NEC_PIN_VSYNC);
    insns[3]  = pio_encode_set(pio_y, BORDER_PX - 1);
    insns[4]  = pio_encode_wait_gpio(true, NEC_PIN_HSYNC);
    insns[5]  = pio_encode_wait_gpio(false, NEC_PIN_HSYNC);
    insns[6]  = pio_encode_jmp_y_dec(4);
    insns[7]  = pio_encode_mov(pio_x, pio_osr);                 // .wrap_target
    insns[8]  = pio_encode_wait_gpio(true, NEC_PIN_HSYNC);
    insns[9]  = pio_encode_set(pio_y, BORDER_PX * PCLK_PER_PX - 1);
    insns[10] = pio_encode_wait_gpio(true, NEC_PIN_PCLK);
    insns[11] = pio_encode_wait_gpio(false, NEC_PIN_PCLK);
    insns[12] = pio_encode_jmp_y_dec(10);
    insns[13] = pio_encode_wait_gpio(true, NEC_PIN_PCLK);
    insns[14] = pio_encode_in(pio_pins, NEC_DATA_BITS);
    insns[15] = pio_encode_wait_gpio(false, NEC_PIN_PCLK);
    insns[16] = pio_encode_jmp_x_dec(13);
    insns[17] = pio_encode_wait_gpio(false, NEC_PIN_HSYNC);     // .wrap

    pio_program_t prog = {.instructions = insns, .length = 18, .origin = -1};
    s_prog_offset = pio_add_program(s_pio, &prog);
    // The jmp targets above are program-relative; pio_add_program relocates them.

    s_smc = pio_get_default_sm_config();
    sm_config_set_in_pins(&s_smc, NEC_PIN_D0);
    sm_config_set_in_shift(&s_smc, true, true, 8);
    sm_config_set_wrap(&s_smc, s_prog_offset + 7, s_prog_offset + 17);
}

int cam_init(uint16_t width, uint16_t height)
{
    const res_cfg_t* r = find_res(width, height);
    if (!r) return -1;

    // Plain GPIO inputs: PIO `wait gpio`/`in` read the pad regardless of
    // function, and keeping VSYNC on SIO lets it also raise the timestamp IRQ.
    const uint inputs[] = {NEC_PIN_VSYNC, NEC_PIN_HSYNC, NEC_PIN_PCLK, NEC_PIN_D0};
    for (unsigned i = 0; i < 4; i++) {
        gpio_init(inputs[i]);
        gpio_set_dir(inputs[i], GPIO_IN);
    }

    i2c_init(NEC_I2C, 100 * 1000);
    gpio_set_function(NEC_PIN_SDA, GPIO_FUNC_I2C);
    gpio_set_function(NEC_PIN_SCL, GPIO_FUNC_I2C);
    gpio_pull_up(NEC_PIN_SDA);
    gpio_pull_up(NEC_PIN_SCL);
    sleep_ms(10);

    int hi = read_reg8(REG_MODEL_ID), lo = read_reg8(REG_MODEL_ID + 1);
    if (hi != 0x01 || lo != 0xB0) return -2;

    write_reg8(REG_SW_RESET, 0x01);
    bool ok = false;
    for (int i = 0; i < 10 && !ok; i++) {
        sleep_ms(100);
        ok = read_reg8(REG_MODE_SELECT) == 0x00;
    }
    if (!ok) return -3;

    s_again = 0;
    s_ae = false;
    apply_resolution(r);

    if (s_dma < 0) {
        s_sm = pio_claim_unused_sm(s_pio, true);
        load_pio_program();
        s_dma = dma_claim_unused_channel(true);
        s_dcc = dma_channel_get_default_config(s_dma);
        channel_config_set_read_increment(&s_dcc, false);
        channel_config_set_write_increment(&s_dcc, true);
        channel_config_set_dreq(&s_dcc, pio_get_dreq(s_pio, s_sm, false));
        channel_config_set_transfer_data_size(&s_dcc, DMA_SIZE_8);
        dma_channel_set_irq0_enabled(s_dma, true);
        irq_set_exclusive_handler(DMA_IRQ_0, dma_irq_handler);
        irq_set_enabled(DMA_IRQ_0, true);
    }
    return 0;
}

int cam_set_resolution(uint16_t width, uint16_t height)
{
    const res_cfg_t* r = find_res(width, height);
    if (!r) return -1;
    if (s_streaming) return -4;
    apply_resolution(r);
    return 0;
}

void cam_set_exposure(uint16_t lines)
{
    if (lines < 2) lines = 2;
    if (lines > s_frame_length - 2) lines = s_frame_length - 2;
    s_exposure = lines;
    write_reg16(REG_INTEGRATION_H, lines);
    write_reg8(REG_GRP_PARAM_HOLD, 0x01);
}

void cam_set_again(uint8_t index)
{
    if (index > 3) index = 3;
    s_again = index;
    write_reg8(REG_ANALOG_GAIN, (uint8_t)(index << 4));
    write_reg8(REG_GRP_PARAM_HOLD, 0x01);
}

void cam_set_ae(bool on)
{
    s_ae = on;
    write_reg8(REG_AE_CTRL, on ? 0x01 : 0x00);
    write_reg8(REG_GRP_PARAM_HOLD, 0x01);
}

void cam_set_frame_length(uint16_t lines)
{
    uint16_t min = s_res->h + 8;
    if (lines < min) lines = min;
    s_frame_length = lines;
    write_reg16(REG_FRAME_LENGTH, lines);
    if (s_exposure > lines - 2) {
        s_exposure = lines - 2;
        write_reg16(REG_INTEGRATION_H, s_exposure);
    }
    write_reg8(REG_GRP_PARAM_HOLD, 0x01);
}

void cam_start(void)
{
    if (s_streaming) return;
    // Anything still queued belongs to the previous run: drop it.
    uint8_t slot;
    while (ring_pop(&s_filled, &slot)) ring_push(&s_free, slot);
    s_vsync_count = 0;
    s_streaming = true;
    write_reg8(REG_MODE_SELECT, 0x01);
    cam_poll();
}

void cam_stop(void)
{
    if (!s_streaming) return;
    s_streaming = false;
    irq_set_enabled(DMA_IRQ_0, false);
    // RP2040-E13: aborting a channel can still raise its completion IRQ.
    dma_channel_set_irq0_enabled(s_dma, false);
    dma_channel_abort(s_dma);
    dma_channel_acknowledge_irq0(s_dma);
    dma_channel_set_irq0_enabled(s_dma, true);
    pio_sm_set_enabled(s_pio, s_sm, false);
    if (s_armed >= 0) {
        ring_push(&s_free, (uint8_t)s_armed);
        s_armed = -1;
    }
    irq_set_enabled(DMA_IRQ_0, true);
    write_reg8(REG_MODE_SELECT, 0x00);
}

bool cam_streaming(void) { return s_streaming; }

void cam_poll(void)
{
    if (!s_streaming || s_armed >= 0) return;
    irq_set_enabled(DMA_IRQ_0, false);
    uint8_t slot;
    if (s_streaming && s_armed < 0 && ring_pop(&s_free, &slot)) arm_slot(slot);
    irq_set_enabled(DMA_IRQ_0, true);
}

bool cam_take_frame(cam_frame_t* out, int* slot)
{
    uint8_t s;
    if (!ring_pop(&s_filled, &s)) return false;
    *out = s_meta[s];
    *slot = s;
    return true;
}

void cam_release_frame(int slot)
{
    ring_push(&s_free, (uint8_t)slot);
}

void cam_on_vsync(uint64_t t_us)
{
    s_vsync_t_us = t_us;
    s_vsync_count++;
}

uint16_t cam_width(void) { return s_res->w; }
uint16_t cam_height(void) { return s_res->h; }
uint16_t cam_exposure(void) { return s_exposure; }
uint16_t cam_frame_length(void) { return s_frame_length; }
uint8_t cam_again(void) { return s_again; }
bool cam_ae(void) { return s_ae; }
uint32_t cam_slots(void) { return s_nslots; }
