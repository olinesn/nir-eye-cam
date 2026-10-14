// Pin assignments, one profile per carrier board. Pick with
// `cmake -DNEC_BOARD=pico` (default) or `-DNEC_BOARD=xiao`.
//
// The Arducam HM01B0 Pico module exposes only VCC, GND, SCL, SDA, VSYNC,
// HREF, PCLK and D0: the sensor is clocked by the module's own oscillator and
// sends 1 bit per PCLK. So there is no MCLK or RESET pin to drive here, and
// no TRIG -- which is why sync is done by timestamping, not by triggering.

#pragma once

#include "hardware/i2c.h"

#if defined(NEC_BOARD_XIAO)

// Seeed XIAO RP2040 (21 x 17.5 mm). Silkscreen label in comments.
#define NEC_BOARD_NAME     "xiao_rp2040"
#define NEC_I2C            i2c1
#define NEC_PIN_SDA        6    // D4
#define NEC_PIN_SCL        7    // D5
#define NEC_PIN_VSYNC      26   // D0
#define NEC_PIN_HSYNC      27   // D1  (module label: HREF)
#define NEC_PIN_PCLK       28   // D2
#define NEC_PIN_D0         29   // D3
#define NEC_PIN_SYNC_IN    0    // D6
#define NEC_PIN_SYNC_OUT   1    // D7
#define NEC_PIN_LED        2    // D8  -> gate of the IR LED MOSFET

#else

// Raspberry Pi Pico / Pico H. Camera pins match Raspberry Pi's HM01B0 article.
#define NEC_BOARD_NAME     "pico"
#define NEC_I2C            i2c0
#define NEC_PIN_SDA        4    // GP4
#define NEC_PIN_SCL        5    // GP5
#define NEC_PIN_VSYNC      6    // GP6
#define NEC_PIN_HSYNC      7    // GP7 (module label: HREF)
#define NEC_PIN_PCLK       8    // GP8
#define NEC_PIN_D0         9    // GP9
#define NEC_PIN_SYNC_IN    14   // GP14
#define NEC_PIN_SYNC_OUT   15   // GP15
#define NEC_PIN_LED        16   // GP16 -> gate of the IR LED MOSFET

#endif

#define NEC_DATA_BITS 1
