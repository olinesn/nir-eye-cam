// Sync line (input timestamps + optional pulse generator) and IR LED PWM.

#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t seq;   // rising-edge count since boot
    uint64_t t_us;  // time_us_64() at the edge
} sync_event_t;

void syncio_init(uint32_t seed);

// Called from the shared GPIO IRQ callback on a SYNC_IN rising edge.
void sync_on_edge(uint64_t t_us);
bool sync_take_event(sync_event_t* out);
uint32_t sync_edge_count(void);
uint32_t sync_overflows(void);

// Pulse generator on SYNC_OUT: 10 ms pulses at pseudo-random 0.4-1.0 s
// intervals. Random so the host can match pulse trains between cameras that
// did not start at the same moment (a regular train would be ambiguous).
void syncgen_set(bool on);
bool syncgen_on(void);

void led_set(uint16_t permille);  // 0..1000
uint16_t led_get(void);
