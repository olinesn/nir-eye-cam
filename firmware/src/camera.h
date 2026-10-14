// HM01B0 continuous capture: PIO + DMA into a pool of frame slots, each
// frame stamped with the device time of its VSYNC rising edge.

#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    const uint8_t* data;
    uint32_t len;
    uint32_t seq;       // VSYNC count since cam_start(); gaps are frames not captured
    uint64_t t_us;      // time_us_64() at this frame's VSYNC rising edge
    uint16_t width;
    uint16_t height;
    uint16_t exposure;  // lines
    uint8_t again;      // 0..3
    bool ae;
} cam_frame_t;

// Probe and configure the sensor. Returns 0, or a negative error:
// -1 bad resolution, -2 wrong/absent sensor (I2C model id), -3 reset timeout.
int cam_init(uint16_t width, uint16_t height);

// Supported: 160x120 (2x2 binned), 320x240, 320x320. Only while stopped.
int cam_set_resolution(uint16_t width, uint16_t height);

void cam_set_exposure(uint16_t lines);     // clamped to [2, frame_length - 2]
void cam_set_again(uint8_t index);         // 0=1x 1=2x 2=4x 3=8x
void cam_set_ae(bool on);                  // sensor auto exposure; keep OFF for pupillometry
void cam_set_frame_length(uint16_t lines); // >= height + 8; longer = slower frame rate

void cam_start(void);
void cam_stop(void);
bool cam_streaming(void);

// Call from the main loop: re-arms capture if it stalled for lack of a free slot.
void cam_poll(void);

// Main-loop side of the frame pool. A frame obtained here must be released.
bool cam_take_frame(cam_frame_t* out, int* slot);
void cam_release_frame(int slot);

// Called from the shared GPIO IRQ callback on a VSYNC rising edge.
void cam_on_vsync(uint64_t t_us);

uint16_t cam_width(void);
uint16_t cam_height(void);
uint16_t cam_exposure(void);
uint16_t cam_frame_length(void);
uint8_t cam_again(void);
bool cam_ae(void);
uint32_t cam_slots(void);
