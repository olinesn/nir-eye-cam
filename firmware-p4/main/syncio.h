// Timestamps (FSYNC from the sensor, the shared sync line), the random sync
// pulse train, the frame trigger for external-sync mode, and the IR enable.

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct {
    uint32_t seq;  // rising-edge count since boot
    int64_t t_us;  // esp_timer time of the edge
} sync_event_t;

esp_err_t syncio_init(uint32_t seed);

bool sync_take_event(sync_event_t* out);
uint32_t sync_edge_count(void);
uint32_t sync_overflows(void);

// SYNC_OUT: 10 ms pulses at random 0.4-1.0 s intervals (same as the RP2040).
void syncgen_set(bool on);
bool syncgen_on(void);

// TRIG_OUT: a square wave at `hz` for every sensor's GPIO0 (0 = off).
esp_err_t trig_set(uint32_t hz);
uint32_t trig_hz(void);

// BLINK: a 50% square wave at `hz` on PIN_BLINK (0 = off), for a visible test
// LED that every camera films during the sync check (nireyecam-synctest). Its
// own LEDC timer, so it runs alongside TRIG.
esp_err_t blink_set(uint32_t hz);
uint32_t blink_hz(void);

// IR master enable: the sensor's STROBE only reaches the LEDs while this is on.
void led_enable(bool on);
bool led_enabled(void);
