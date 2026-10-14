// Pin map and fixed hardware facts.
//
// PROTOTYPE: an ESP32-P4 dev board + ST's STEVAL-CAM-M0I "P-Board" carrying a
// CAM-5G1 promodule. Every GPIO number below is a placeholder until the
// board is chosen and wired. Values marked VERIFY are not confirmed for this
// hardware.

#pragma once

#define NEC_BOARD_NAME "esp32p4"

// Sensor control (I2C). 0x10 is the VD55G1 address in the Linux device-tree
// binding example.
#define PIN_I2C_SDA          7
#define PIN_I2C_SCL          8
#define SENSOR_I2C_ADDR      0x10
#define SENSOR_I2C_HZ        400000

// Sensor reset (XSHUTDOWN, active low). -1 if the module board drives it.
#define PIN_XSHUTDOWN        -1

// Frequency on the sensor's CLKIN (6-27 MHz). It must be the frequency
// actually fed to the module: on the P-Board that is its own oscillator.
// VERIFY (the Linux binding example uses 12 MHz).
#define SENSOR_EXT_CLK_HZ    12000000

// MIPI CSI-2: one data lane. 600 Mb/s gives a 150 MHz pixel clock (Linux
// driver's clock-tree rule), far more than a 320x320 crop needs.
#define MIPI_LANES           1
#define MIPI_LANE_MBPS       600

// The ESP32-P4's MIPI PHY is powered from internal LDO channel 3 at 2.5 V
// (as in ESP-IDF's camera examples and tests).
#define MIPI_LDO_CHAN        3
#define MIPI_LDO_MV          2500

// Sensor GPIO roles (inside the sensor; set over I2C).
#define SENSOR_IO_EXTSYNC    0   // only GPIO0 can be EXTERNAL_SYNC (ST UM3224)
#define SENSOR_IO_STROBE     1   // -> IR LED driver, on during exposure
#define SENSOR_IO_FSYNC_OUT  2   // -> PIN_FSYNC: frame timestamps

// ESP32-P4 pins for sync and illumination. All need a 1.8 V <-> 3.3 V level
// shifter where they meet the sensor's GPIOs.
#define PIN_FSYNC            20  // in:  sensor FSYNC_OUT (frame start)
#define PIN_SYNC_IN          21  // in:  shared sync line (timestamped)
#define PIN_SYNC_OUT         22  // out: random pulse train (sync master only)
#define PIN_TRIG_OUT         23  // out: frame trigger to every sensor's GPIO0
#define PIN_LED_EN           24  // out: gates the STROBE -> LED path (IR master enable)
