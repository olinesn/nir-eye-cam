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

// Sensor reset (XSHUTDOWN, active low). The P-Board takes it from the camera
// connector's enable pin: CAM_IO0, Raspberry Pi pin 11, which a Raspberry Pi
// drives high to power the camera and release its reset (ST: "the reset is
// controlled by the GPIO"). The ESP32-P4X board's connector carries the
// Raspberry Pi signals numbered from the other end, so that is its pin 5,
// which Espressif's own camera adapter pulls up to 1.8 V by itself: the main
// board may not drive it at all, and no board GPIO is known to. On the
// P-Board (schematic PCB4280D) this line, NRST, holds XSHUTDOWN low through a
// diode, and may also switch the module's regulators on (which of its 0 ohm
// links R4 / R12 is fitted is not checked). VERIFY that it sits high with the
// P-Board connected (firmware-p4/README.md); if a board GPIO turns out to
// drive it, put that GPIO here.
#define PIN_XSHUTDOWN        -1

// Frequency on the sensor's CLKIN (6-27 MHz), from the camera board's own
// oscillator: on the P-Board a 12 MHz oscillator (Y1 in its schematic) wired
// straight to the module, as ST's Raspberry Pi overlay declares. The sensor's
// PLL is computed from this (a wrong value gives a wrong MIPI rate, or no
// boot).
#define SENSOR_EXT_CLK_HZ    12000000

// MIPI CSI-2: one data lane. 600 Mb/s gives a 150 MHz pixel clock (Linux
// driver's clock-tree rule), far more than a 320x320 crop needs. (ST's
// overlay runs the link faster, at 1200 Mb/s; the sensor allows 250-1200.)
#define MIPI_LANES           1
#define MIPI_LANE_MBPS       600

// The P-Board's clock and data pairs are wired P/N swapped relative to the
// Raspberry Pi pinout, which this board's connector shares (its schematic:
// "For use with RaspberryPi, MIPI Lane P/N shall be swap by SW"; ST's and
// Raspberry Pi's overlays for it set lane-polarities <1 1> on the sensor).
// 1 has the sensor invert both lanes at its own output (OIF_CTRL bits 3 and
// 6), exactly as ST's Linux and STM32 drivers do for that setting.
// `PNSWAP 0|1` switches it at run time.
#define MIPI_LANE_PN_SWAP    1

// The ESP32-P4's MIPI PHY is powered from internal LDO channel 3 at 2.5 V,
// as in ESP-IDF's camera examples for this board.
#define MIPI_LDO_CHAN        3
#define MIPI_LDO_MV          2500

// Sensor GPIO roles (inside the sensor; set over I2C), fitted to the P-Board's
// wiring (schematic PCB4280D):
// - GPIO0 reaches only the camera connector, as CAM_IO1 (Raspberry Pi 15-pin
//   pin 12, so pin 4 at the ESP32-P4X end, where nothing is known to use it),
//   through a 1.8 V <-> 3.3 V buffer whose direction is set by GPIO1.
// - GPIO1 is that buffer's direction pin (high: sensor drives the connector;
//   low: the connector drives GPIO0) and is brought out nowhere else. It is
//   left as an input, as ST's own driver leaves it.
// - GPIO2 is on the P-Board's J3 header (pin 2, beside 1.8 V and GND) and J4:
//   the strobe pin ST's overlays use.
// - GPIO3 is on test point TP2 only: solder a wire to it for FSYNC_OUT.
#define SENSOR_IO_EXTSYNC    0   // only GPIO0 can be EXTERNAL_SYNC (ST UM3224)
#define SENSOR_IO_STROBE     2   // -> IR LED driver, on during exposure (P-Board J3 pin 2)
#define SENSOR_IO_FSYNC_OUT  3   // -> PIN_FSYNC: frame timestamps (P-Board TP2)

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
#define PIN_BLINK            33  // J1-29  out: visible test LED for the two-camera sync check (BLINK)
