#include "vd55g1.h"

#include <string.h>

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "vd55g1";

// Register addresses are 16-bit, sent MSB first; multi-byte VALUES are
// little-endian (the opposite of the Himax sensors).
#define REG_MODEL_ID              0x0000  // 32
#define REG_SYSTEM_FSM            0x001C  // 8
#define REG_BOOT                  0x0200  // 8
#define REG_STBY                  0x0201  // 8
#define REG_STREAMING             0x0202  // 8
#define REG_EXT_CLOCK             0x0220  // 32, Hz
#define REG_MIPI_DATA_RATE        0x0224  // 32, bit/s
#define REG_LINE_LENGTH           0x0300  // 16
#define REG_FORMAT_CTRL           0x030A  // 8, bits per pixel
#define REG_OIF_CTRL              0x030C  // 16, lane polarities
#define REG_OIF_IMG_CTRL          0x030F  // 8, CSI-2 data type
#define REG_EXPOSURE_USE_CASES    0x0312  // 8
#define REG_ISL_ENABLE            0x0326  // 16
#define REG_EXPOSURE_MAX_COARSE   0x0372  // 16
#define REG_DUSTER_CTRL           0x03AE  // 8, defect correction
#define REG_CTX_REPEAT_COUNT_CTX0 0x03DC  // 16
#define REG_NEXT_CTX              0x03E4  // 16
#define REG_MANUAL_ANALOG_GAIN    0x0501  // 8
#define REG_MANUAL_COARSE_EXP     0x0502  // 16, lines
#define REG_MANUAL_DGAIN_CH0      0x0504  // 16 (CH1-3 follow at +2)
#define REG_READOUT_CTRL          0x052E  // 8
// Context 0 registers (context n adds 0x50 * n).
#define REG_EXP_MODE              0x0500  // 8
#define REG_FRAME_LENGTH          0x050C  // 32
#define REG_Y_START               0x0510  // 16
#define REG_Y_HEIGHT              0x0512  // 16
#define REG_X_START               0x0514  // 16
#define REG_X_WIDTH               0x0516  // 16
#define REG_GPIO_0_CTRL           0x051D  // 8 (GPIOn at +n)
#define REG_VT_MODE               0x0536  // 8
#define REG_MASK_FRAME_CTRL       0x0537  // 8

#define FSM_READY_TO_BOOT 0x01
#define FSM_SW_STBY       0x02
#define FSM_STREAMING     0x03

#define MODEL_ID_REV2 0x53354731  // "S5G1": needs ST's firmware patch before boot
#define MODEL_ID_REV3 0x53354733  // "S5G3": boots as is

#define EXP_MODE_AUTO   0
#define EXP_MODE_MANUAL 2

#define CSI2_DT_RAW8     0x2A
#define LINE_LENGTH_MIN  1128   // ADC conversion time per line
#define MIPI_MARGIN      900    // bits of per-line MIPI overhead
#define VBLANK_MIN       86
#define EXPOSURE_MARGIN  64     // exposure <= frame_length - this
#define DUSTER_ON        0x13   // ring | dynamic | enable

// ---------------------------------------------------------------- I2C

static esp_err_t wr(vd55g1_t* s, uint16_t reg, uint32_t val, int bytes)
{
    uint8_t b[6] = {reg >> 8, reg & 0xff};
    for (int i = 0; i < bytes; i++) b[2 + i] = (val >> (8 * i)) & 0xff;  // little-endian value
    return i2c_master_transmit(s->dev, b, 2 + bytes, 50);
}

static esp_err_t rd(vd55g1_t* s, uint16_t reg, uint32_t* val, int bytes)
{
    uint8_t a[2] = {reg >> 8, reg & 0xff}, b[4] = {0};
    ESP_RETURN_ON_ERROR(i2c_master_transmit_receive(s->dev, a, 2, b, bytes, 50), TAG, "read 0x%04x", reg);
    *val = 0;
    for (int i = 0; i < bytes; i++) *val |= (uint32_t)b[i] << (8 * i);
    return ESP_OK;
}

static esp_err_t poll(vd55g1_t* s, uint16_t reg, uint32_t want, int timeout_ms)
{
    uint32_t v = 0;
    for (int t = 0; t < timeout_ms; t += 2) {
        ESP_RETURN_ON_ERROR(rd(s, reg, &v, 1), TAG, "poll");
        if (v == want) return ESP_OK;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    ESP_LOGE(TAG, "reg 0x%04x stuck at 0x%02lx (want 0x%02lx)", reg, (unsigned long)v, (unsigned long)want);
    return ESP_ERR_TIMEOUT;
}

// ---------------------------------------------------------------- timing

static void derive_clocks(vd55g1_t* s, uint32_t lane_mbps)
{
    // Linux driver's clock tree: MIPI rate -> system clock -> pixel clock.
    s->mipi_bps = lane_mbps * 1000000u;
    uint32_t mipi_div = lane_mbps <= 300 ? 4 : lane_mbps <= 600 ? 2 : 1;
    uint64_t sys_clk = (uint64_t)s->mipi_bps * mipi_div;
    uint32_t pixel_div = sys_clk <= 780000000ull ? 5 : sys_clk <= 900000000ull ? 6 : 8;
    s->pixel_clock_hz = (uint32_t)(sys_clk / pixel_div);
}

static uint16_t min_line_length(const vd55g1_t* s)
{
    // A line can be no shorter than the ADC time, nor than the time the MIPI
    // link needs to send it.
    // Rounded up: this is a minimum.
    uint64_t bits = (uint64_t)s->w * 8 + MIPI_MARGIN;
    uint64_t mipi_line_len = (bits * s->pixel_clock_hz + s->mipi_bps - 1) / s->mipi_bps;
    return mipi_line_len > LINE_LENGTH_MIN ? (uint16_t)mipi_line_len : LINE_LENGTH_MIN;
}

uint32_t vd55g1_line_time_ns(const vd55g1_t* s)
{
    return (uint32_t)((uint64_t)s->line_length * 1000000000ull / s->pixel_clock_hz);
}

uint32_t vd55g1_frame_period_us(const vd55g1_t* s)
{
    return (uint32_t)((uint64_t)vd55g1_line_time_ns(s) * s->frame_length / 1000u);
}

// ---------------------------------------------------------------- API

esp_err_t vd55g1_open(vd55g1_t* s, int sda, int scl, uint8_t addr, uint32_t i2c_hz,
                      int xshutdown_pin, uint32_t ext_clk_hz, uint32_t lane_mbps)
{
    memset(s, 0, sizeof(*s));
    s->ext_clk_hz = ext_clk_hz;
    derive_clocks(s, lane_mbps);

    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = -1,
        .sda_io_num = sda,
        .scl_io_num = scl,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &s->bus), TAG, "i2c bus");
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = addr,
        .scl_speed_hz = i2c_hz,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(s->bus, &dev_cfg, &s->dev), TAG, "i2c dev");

    if (xshutdown_pin >= 0) {
        gpio_set_direction(xshutdown_pin, GPIO_MODE_OUTPUT);
        gpio_set_level(xshutdown_pin, 0);
        vTaskDelay(pdMS_TO_TICKS(2));
        gpio_set_level(xshutdown_pin, 1);
    }
    vTaskDelay(pdMS_TO_TICKS(10));

    ESP_RETURN_ON_ERROR(poll(s, REG_SYSTEM_FSM, FSM_READY_TO_BOOT, 500), TAG, "sensor not ready to boot (I2C/power/clock?)");
    ESP_RETURN_ON_ERROR(rd(s, REG_MODEL_ID, &s->model_id, 4), TAG, "model id");
    ESP_LOGI(TAG, "model id 0x%08lx", (unsigned long)s->model_id);

    ESP_RETURN_ON_ERROR(wr(s, REG_EXT_CLOCK, ext_clk_hz, 4), TAG, "ext clock");

    if (s->model_id == MODEL_ID_REV2) {
        // Rev 2 silicon needs ST's firmware patch written at 0x2000 and then
        // BOOT = 2. The blob is not included (the only public copy is in the
        // GPL Linux driver); get it from ST.
        ESP_LOGE(TAG, "rev 2 sensor: needs ST firmware patch, not included");
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (s->model_id != MODEL_ID_REV3) {
        ESP_LOGE(TAG, "not a VD55G1");
        return ESP_ERR_NOT_FOUND;
    }
    ESP_RETURN_ON_ERROR(wr(s, REG_BOOT, 1, 1), TAG, "boot");
    ESP_RETURN_ON_ERROR(poll(s, REG_BOOT, 0, 500), TAG, "boot ack");
    ESP_RETURN_ON_ERROR(poll(s, REG_SYSTEM_FSM, FSM_SW_STBY, 500), TAG, "standby");

    // Single exposure context, no HDR, defect correction on.
    ESP_RETURN_ON_ERROR(wr(s, REG_EXPOSURE_MAX_COARSE, 0x7fff, 2), TAG, "init");
    wr(s, REG_EXPOSURE_USE_CASES, 0, 1);
    wr(s, REG_NEXT_CTX, 0, 2);
    wr(s, REG_CTX_REPEAT_COUNT_CTX0, 0, 2);
    wr(s, REG_VT_MODE, 0, 1);
    wr(s, REG_MASK_FRAME_CTRL, 0, 1);
    wr(s, REG_DUSTER_CTRL, DUSTER_ON, 1);

    s->dgain = 256;
    s->again = 0;
    s->exposure = 500;
    return vd55g1_set_crop(s, (VD55G1_FULL_W - 320) / 2, (VD55G1_FULL_H - 320) / 2, 320, 320);
}

esp_err_t vd55g1_set_crop(vd55g1_t* s, uint16_t x, uint16_t y, uint16_t w, uint16_t h)
{
    ESP_RETURN_ON_FALSE(!s->streaming, ESP_ERR_INVALID_STATE, TAG, "stop first");
    ESP_RETURN_ON_FALSE(w && h && x + w <= VD55G1_FULL_W && y + h <= VD55G1_FULL_H,
                        ESP_ERR_INVALID_ARG, TAG, "crop outside sensor");
    ESP_RETURN_ON_FALSE(((uint32_t)w * h) % 8 == 0, ESP_ERR_INVALID_ARG, TAG, "w*h must be a multiple of 8");
    s->x = x, s->y = y, s->w = w, s->h = h;

    ESP_RETURN_ON_ERROR(wr(s, REG_FORMAT_CTRL, 8, 1), TAG, "format");
    wr(s, REG_OIF_IMG_CTRL, CSI2_DT_RAW8, 1);
    wr(s, REG_READOUT_CTRL, 0, 1);  // no binning
    wr(s, REG_X_START, x, 2);
    wr(s, REG_X_WIDTH, w, 2);
    wr(s, REG_Y_START, y, 2);
    wr(s, REG_Y_HEIGHT, h, 2);

    s->line_length = min_line_length(s);
    ESP_RETURN_ON_ERROR(wr(s, REG_LINE_LENGTH, s->line_length, 2), TAG, "line length");
    // Default to 100 fps (10 ms of lines), or the fastest this crop allows.
    uint32_t lines_100fps = 10000000u / vd55g1_line_time_ns(s);
    return vd55g1_set_frame_length(s, lines_100fps > 0xffff ? 0xffff : lines_100fps);
}

esp_err_t vd55g1_set_frame_length(vd55g1_t* s, uint16_t lines)
{
    uint16_t min = s->h + VBLANK_MIN;
    if (lines < min) lines = min;
    s->frame_length = lines;
    ESP_RETURN_ON_ERROR(wr(s, REG_FRAME_LENGTH, lines, 4), TAG, "frame length");
    if (s->exposure > lines - EXPOSURE_MARGIN) return vd55g1_set_exposure(s, s->exposure);
    return ESP_OK;
}

esp_err_t vd55g1_set_exposure(vd55g1_t* s, uint16_t lines)
{
    uint16_t max = s->frame_length > EXPOSURE_MARGIN ? s->frame_length - EXPOSURE_MARGIN : 1;
    if (lines < 1) lines = 1;
    if (lines > max) lines = max;
    s->exposure = lines;
    return wr(s, REG_MANUAL_COARSE_EXP, lines, 2);
}

esp_err_t vd55g1_set_again(vd55g1_t* s, uint8_t code)
{
    if (code > 28) code = 28;
    s->again = code;
    return wr(s, REG_MANUAL_ANALOG_GAIN, code, 1);
}

esp_err_t vd55g1_set_dgain(vd55g1_t* s, uint16_t gain_q8)
{
    s->dgain = gain_q8;
    for (int ch = 0; ch < 4; ch++) ESP_RETURN_ON_ERROR(wr(s, REG_MANUAL_DGAIN_CH0 + 2 * ch, gain_q8, 2), TAG, "dgain");
    return ESP_OK;
}

esp_err_t vd55g1_set_ae(vd55g1_t* s, bool on)
{
    s->ae = on;
    return wr(s, REG_EXP_MODE, on ? EXP_MODE_AUTO : EXP_MODE_MANUAL, 1);
}

esp_err_t vd55g1_set_io(vd55g1_t* s, unsigned io, vd55g1_io_mode_t mode)
{
    ESP_RETURN_ON_FALSE(io < 4, ESP_ERR_INVALID_ARG, TAG, "io");
    return wr(s, REG_GPIO_0_CTRL + io, mode, 1);
}

esp_err_t vd55g1_set_external_sync(vd55g1_t* s, bool on)
{
    // TODO(UM3224): configure GPIO0 as EXTERNAL_SYNC and switch frame start
    // to it. Until then the sensor free-runs on its own timer.
    (void)s;
    return on ? ESP_ERR_NOT_SUPPORTED : ESP_OK;
}

esp_err_t vd55g1_start(vd55g1_t* s)
{
    if (s->streaming) return ESP_OK;
    ESP_RETURN_ON_ERROR(wr(s, REG_MIPI_DATA_RATE, s->mipi_bps, 4), TAG, "mipi rate");
    wr(s, REG_OIF_CTRL, 0, 2);  // no lane polarity swap
    wr(s, REG_ISL_ENABLE, 0, 2);
    // Re-apply the manual exposure state in case AE moved it.
    vd55g1_set_ae(s, s->ae);
    if (!s->ae) {
        vd55g1_set_exposure(s, s->exposure);
        vd55g1_set_again(s, s->again);
        vd55g1_set_dgain(s, s->dgain);
    }
    ESP_RETURN_ON_ERROR(wr(s, REG_STBY, 1, 1), TAG, "start");
    ESP_RETURN_ON_ERROR(poll(s, REG_STBY, 0, 500), TAG, "start ack");
    ESP_RETURN_ON_ERROR(poll(s, REG_SYSTEM_FSM, FSM_STREAMING, 500), TAG, "streaming");
    s->streaming = true;
    return ESP_OK;
}

esp_err_t vd55g1_stop(vd55g1_t* s)
{
    if (!s->streaming) return ESP_OK;
    ESP_RETURN_ON_ERROR(wr(s, REG_STREAMING, 1, 1), TAG, "stop");
    ESP_RETURN_ON_ERROR(poll(s, REG_STREAMING, 0, 500), TAG, "stop ack");
    ESP_RETURN_ON_ERROR(poll(s, REG_SYSTEM_FSM, FSM_SW_STBY, 500), TAG, "standby");
    s->streaming = false;
    return ESP_OK;
}
