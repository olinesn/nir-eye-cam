// Pin map and fixed hardware facts.
//
// PROTOTYPE: Espressif's ESP32-P4X-Function-EV-Board (ESP32-P4 chip
// revision v3.1 or later, what sdkconfig builds for) + ST's STEVAL-CAM-M0I
// "P-Board" carrying a CAM-5G1 promodule on the board's 15-pin MIPI CSI
// connector. "J1-n" is pin n of the board's 40-pin header J1, numbered as in
// Espressif's ESP32-P4X-Function-EV-Board user guide (Header Block).
//
// Values marked VERIFY are not confirmed on this hardware. Sources: the
// board's user guide; ESP-IDF v6.1's camera examples
// (examples/peripherals/camera/*), which have v3-board pin sets; ST's VD55G1
// Linux driver overlay for Raspberry Pi (vd55g1-linux-driver,
// dts/rpi/vd55g1_vd65g4-overlay.dtsi).

#pragma once

#define NEC_BOARD_NAME "esp32p4"

// Sensor control (I2C) on GPIO7 (SDA) and GPIO8 (SCL): the camera
// connector's I2C, as in ESP-IDF's MIPI camera example (mipi_isp_dsi), and
// also on J1-3 / J1-5. The board's own I2C devices (audio codec, touch
// controller) share the bus; VERIFY nothing else answers at 0x10.
#define PIN_I2C_SDA          7   // J1-3  (and the camera connector)
#define PIN_I2C_SCL          8   // J1-5  (and the camera connector)
#define SENSOR_I2C_ADDR      0x10
#define SENSOR_I2C_HZ        400000

// Sensor reset (XSHUTDOWN, active low). The board drives no camera reset
// (ESP-IDF's camera example and Espressif's BSP leave it unconnected), so
// the P-Board must hold it. VERIFY on the P-Board.
#define PIN_XSHUTDOWN        -1

// Frequency on the sensor's CLKIN (6-27 MHz), from the camera board's own
// oscillator. ST's Raspberry Pi overlay for its VD55G1 boards declares
// 12 MHz. The sensor's PLL is computed from this, so VERIFY it on the
// P-Board (a wrong value gives a wrong MIPI rate, or no boot).
#define SENSOR_EXT_CLK_HZ    12000000

// MIPI CSI-2: one data lane. 600 Mb/s gives a 150 MHz pixel clock (Linux
// driver's clock-tree rule), far more than a 320x320 crop needs. (ST's
// overlay runs the link faster, at 1200 Mb/s; the sensor allows 250-1200.)
// The ESP32-P4 cannot swap a lane's P and N in software, so a P-Board wired
// with inverted lane polarity needs a cable that swaps them (ST's overlays
// set lane-polarities <1 1> for some of its boards on a Raspberry Pi).
#define MIPI_LANES           1
#define MIPI_LANE_MBPS       600

// The ESP32-P4's MIPI PHY is powered from internal LDO channel 3 at 2.5 V,
// as in ESP-IDF's camera examples for this board.
#define MIPI_LDO_CHAN        3
#define MIPI_LDO_MV          2500

// Sensor GPIO roles (inside the sensor; set over I2C). ST's overlay uses
// sensor GPIO2 as the LED strobe on its boards: VERIFY which sensor GPIOs the
// P-Board brings out, and swap STROBE and FSYNC_OUT here if only GPIO2 is.
#define SENSOR_IO_EXTSYNC    0   // only GPIO0 can be EXTERNAL_SYNC (ST UM3224)
#define SENSOR_IO_STROBE     1   // -> IR LED driver, on during exposure
#define SENSOR_IO_FSYNC_OUT  2   // -> PIN_FSYNC: frame timestamps

// ESP32-P4 pins for sync and illumination, on header J1. ESP-IDF's DVP camera
// example puts an external camera on these same pins for v3 boards, so the
// board itself does not use them. Kept clear of GPIO26/27 (the LCD adapter's
// default backlight and reset), GPIO37/38 (UART0) and GPIO0/1/45 (not
// connected by default). All need a 1.8 V <-> 3.3 V level shifter where they
// meet the sensor's GPIOs.
#define PIN_FSYNC            20  // J1-13  in:  sensor FSYNC_OUT (frame start)
#define PIN_SYNC_IN          21  // J1-11  in:  shared sync line (timestamped)
#define PIN_SYNC_OUT         22  // J1-12  out: random pulse train (sync master only)
#define PIN_TRIG_OUT         23  // J1-7   out: frame trigger to every sensor's GPIO0
#define PIN_LED_EN           32  // J1-26  out: gates the STROBE -> LED path (IR master enable)
