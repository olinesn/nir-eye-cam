// MIPI CSI-2 capture into a pool of PSRAM frame slots.
//
// RAW8 in, RAW8 out, ISP bypassed: the sensor is monochrome, so there is no
// Bayer data to demosaic. This is the path ESP-IDF's own CSI test exercises
// (test_csi_ov5647.c: RAW8 -> RAW8, 1 lane).

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

typedef struct {
    const uint8_t* data;
    uint32_t len;
    uint32_t seq;      // frame boundary count since capture_start(); gaps = dropped
    int64_t t_us;      // esp_timer time of the frame's FSYNC_OUT edge (or DMA end, see flags)
    uint16_t width, height;
    bool t_from_fsync; // false: no FSYNC edge seen, t_us is DMA completion time
} cap_frame_t;

// Allocate slots and create the CSI controller + ISP bypass for w x h RAW8.
esp_err_t capture_init(uint16_t w, uint16_t h, int lanes, int lane_mbps);
esp_err_t capture_deinit(void);

esp_err_t capture_start(void);
esp_err_t capture_stop(void);

bool capture_take(cap_frame_t* out, int* slot, TickType_t wait);
void capture_release(int slot);

// From the FSYNC GPIO interrupt.
void capture_on_fsync(int64_t t_us);

uint32_t capture_slots(void);
uint32_t capture_errors(void);  // CSI host error events since init
