// ST VD55G1 (804 x 704 mono global shutter) over I2C.
//
// Written from the register map and sequences in ST's documentation and the
// mainline Linux driver (drivers/media/i2c/vd55g1.c, used as a reference for
// register addresses and values; no code copied).

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "esp_err.h"

#define VD55G1_FULL_W 804
#define VD55G1_FULL_H 704

typedef enum {
    VD55G1_IO_FSYNC_OUT = 0x00,
    VD55G1_IO_INPUT     = 0x01,
    VD55G1_IO_STROBE    = 0x02,
} vd55g1_io_mode_t;

typedef struct {
    i2c_master_bus_handle_t bus;
    i2c_master_dev_handle_t dev;
    uint32_t model_id;
    uint32_t ext_clk_hz;
    uint32_t mipi_bps;
    uint32_t pixel_clock_hz;
    // Current settings (context 0).
    uint16_t x, y, w, h;      // crop on the sensor
    uint16_t line_length;     // pixel clocks per line
    uint16_t frame_length;    // lines per frame
    uint16_t exposure;        // lines
    uint8_t again;            // 0..28; gain = 32 / (32 - again)
    uint16_t dgain;           // 256 = 1x
    bool ae;
    bool streaming;
} vd55g1_t;

// Reset, detect, set the clock and boot to software standby.
esp_err_t vd55g1_open(vd55g1_t* s, int sda, int scl, uint8_t addr, uint32_t i2c_hz,
                      int xshutdown_pin, uint32_t ext_clk_hz, uint32_t lane_mbps);

// Output a w x h crop at (x, y), RAW8, 1 lane. Standby only. w*h must be a
// multiple of 8 (the ESP32-P4 CSI DMA moves 64-bit words).
esp_err_t vd55g1_set_crop(vd55g1_t* s, uint16_t x, uint16_t y, uint16_t w, uint16_t h);

esp_err_t vd55g1_set_frame_length(vd55g1_t* s, uint16_t lines);
esp_err_t vd55g1_set_exposure(vd55g1_t* s, uint16_t lines);
esp_err_t vd55g1_set_again(vd55g1_t* s, uint8_t code);
esp_err_t vd55g1_set_dgain(vd55g1_t* s, uint16_t gain_q8);
esp_err_t vd55g1_set_ae(vd55g1_t* s, bool on);
esp_err_t vd55g1_set_io(vd55g1_t* s, unsigned io, vd55g1_io_mode_t mode);

// Frames start on edges at GPIO0 instead of the internal timer.
// NOT IMPLEMENTED: the register sequence is in ST UM3224 and is not in the
// Linux driver. Returns ESP_ERR_NOT_SUPPORTED until it is filled in.
esp_err_t vd55g1_set_external_sync(vd55g1_t* s, bool on);

esp_err_t vd55g1_start(vd55g1_t* s);
esp_err_t vd55g1_stop(vd55g1_t* s);

uint32_t vd55g1_line_time_ns(const vd55g1_t* s);
uint32_t vd55g1_frame_period_us(const vd55g1_t* s);
