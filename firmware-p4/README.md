# ESP32-P4 firmware: VD55G1 global-shutter camera

Skeleton firmware for the build in [../docs/vd55g1-esp32p4.md](../docs/vd55g1-esp32p4.md):
an ST VD55G1 promodule on MIPI CSI-2 → ESP32-P4 → USB 2.0 high speed. It
speaks the same packet protocol as the RP2040 build
([../common/nec_protocol.h](../common/nec_protocol.h)), so `nireyecam-record`
and `nireyecam-align` work with it unchanged.

**Status: compiles with ESP-IDF v6.1 and esp_tinyusb 2.4 with no warnings
(ESP-IDF builds with `-Wall -Werror`); not yet run on hardware.**
`dependencies.lock` pins the component versions that built.

## Layout

| File | Does |
|---|---|
| `main/board.h` | Pin map and hardware constants, set for the ESP32-P4X-Function-EV-Board (below); edit it for another board. |
| `main/vd55g1.c` | Sensor driver: reset, detect, boot, crop, exposure/gain, GPIO roles, start/stop. |
| `main/capture.c` | MIPI PHY LDO, CSI controller (RAW8 → RAW8, 1 lane), ISP in bypass, PSRAM frame pool. |
| `main/syncio.c` | Timestamps for FSYNC and sync edges (GPIO interrupts), random sync pulse train, frame-trigger and test-LED outputs (LEDC, off the crystal), IR enable. |
| `main/usb_link.c` | esp_tinyusb CDC on the high-speed port; DTR = host connected. |
| `main/main.c` | Commands (list at the top of the file) and the streaming task. |

## Build and flash

Needs ESP-IDF v6.1 ([install guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/get-started/)).

```bash
cd firmware-p4
idf.py set-target esp32p4
idf.py build
idf.py -p PORT flash monitor
```

`PORT` is the board's UART / USB-serial-JTAG port, not the high-speed USB port
the camera streams on. On the ESP32-P4X-Function-EV-Board, flash through the
Type-C port labelled **USB Serial/JTAG**, and connect the computer that records
to the Type-C port labelled **USB 2.0** (the high-speed one). That port and
the Type-A port share the high-speed USB, so leave the Type-A empty. Flashing a
CI build without ESP-IDF is covered in the main README's "Flash the firmware"
section.

`esp_tinyusb` is fetched automatically (see `main/idf_component.yml`). The
camera shows up as a USB serial device named "NIR Eye Cam" on the
high-speed port; logs go to the board's UART / USB-serial-JTAG console.

## Board: ESP32-P4X-Function-EV-Board

`main/board.h` is set for Espressif's **ESP32-P4X-Function-EV-Board**: chip
revision v3.1 or later, which is what `sdkconfig` builds for, and a Type-C
port wired to the high-speed USB as a device. The older
ESP32-P4-Function-EV-Board (no X) has v1.x silicon and does not run this
build. The camera (ST's STEVAL-CAM-M0I P-Board) goes on the 15-pin MIPI CSI
connector; everything else on header J1:

| Signal | ESP32-P4 | J1 pin | Connects to |
|---|---|---|---|
| I2C SDA / SCL | GPIO7 / GPIO8 | 3 / 5 | the camera connector already; J1 too |
| FSYNC in | GPIO20 | 13 | sensor GPIO3 (FSYNC_OUT, the P-Board's TP2), through a level shifter |
| SYNC in | GPIO21 | 11 | the shared sync line |
| SYNC out | GPIO22 | 12 | the shared sync line (sync master only) |
| TRIG out | GPIO23 | 7 | every sensor's GPIO0 (EXTSYNC); on the P-Board only through the camera connector (below) |
| LED enable | GPIO32 | 26 | the gate that lets the sensor's STROBE (GPIO2) reach the LED driver |
| Test LED | GPIO33 | 29 | an LED through 330 Ω to GND, filmed by every camera in the sync check (`BLINK`) |

These pins are on J1 and the board itself does not use them: ESP-IDF's DVP
camera example puts an external camera on the same ones for v3 boards.
GPIO26/27 (the LCD adapter's default backlight and reset), GPIO37/38 (UART0)
and GPIO0/1/45 (not connected by default) are left alone.

## Before first power-up

What the P-Board's schematic (PCB4280D, from ST's STEVAL-CAM-M0I page)
settles, and what is still to check with a meter before connecting it:

- **Lane polarity: swapped, and the firmware swaps it back (VERIFY).** The
  schematic says the board's clock and data pairs are wired P/N swapped
  relative to the Raspberry Pi pinout ("MIPI Lane P/N shall be swap by SW"),
  and ST's and Raspberry Pi's overlays for it set `lane-polarities <1 1>`.
  ESP-IDF has no setting for this, but its D-PHY setup uses the same test
  codes as the Raspberry Pi 5's, which swaps the pairs with two more of them.
  `MIPI_LANE_PN_SWAP` in `board.h` (default 1) writes those, and `PNSWAP 0|1`
  changes it without reflashing. If frames never arrive, or `csi_errors`
  climbs, with either setting, the pairs need crossing in hardware instead.
- **The cable: a Raspberry Pi 5 camera cable (22-pin, 0.5 mm → 15-pin,
  1.0 mm).** The P-Board's 22-pin output uses the Raspberry Pi 5 pinout (an
  ST community thread, and ST's driver keeps its files for that connector
  with the Pi 5's). The ESP32-P4X board's 15-pin connector carries the
  Raspberry Pi 15-pin camera signals, numbered from the other end:
  Espressif's pin *n* is the Raspberry Pi's pin 16 − *n* (Espressif's camera
  adapter schematic). So the cable that connects a 15-pin camera to a Pi 5
  connects these two. Check that its contacts face the connector's contacts
  at both ends.
- **The enable pin: reset and power.** ST says the P-Board's sensor reset
  "is controlled by the GPIO": the connector's CAM_IO0 (Raspberry Pi pin 11),
  which a Raspberry Pi drives high to power the camera and release its
  reset. On the ESP32-P4X board that is connector pin 5, labelled RESET on
  Espressif's own camera adapter, which pulls it up to 1.8 V itself, so the
  main board may not drive it at all. One user found a host holding that
  line low, which kept the P-Board's sensor in reset. With the P-Board
  connected and powered, measure it at the P-Board: high is right. If it is
  low or floating, `INFO` reports `cam_ok` 0 and the sensor never answers at
  0x10. Pull the line up to 3.3 V (what a Raspberry Pi drives), or, if the
  ESP32-P4X schematic shows a GPIO on it, set `PIN_XSHUTDOWN` to that GPIO.
- **CLKIN.** The P-Board clocks the module from its own 12 MHz oscillator,
  which is what `SENSOR_EXT_CLK_HZ` says. There is no clock input.
- **Sensor GPIOs, as the P-Board brings them out:**

  | Sensor pin | Where | Role in this firmware |
  |---|---|---|
  | GPIO0 | Only the camera connector (CAM_IO1: pin 4 at the ESP32-P4X end), at 3.3 V through a one-bit buffer | EXTSYNC input |
  | GPIO1 | Nowhere: it sets that buffer's direction (high: the sensor drives the connector; low: the connector drives GPIO0) | Left an input, as ST's driver leaves it |
  | GPIO2 | Header J3 (1 = 1.8 V through 10 Ω, 2 = GPIO2 through 100 Ω, 3 = GND) and J4 | STROBE |
  | GPIO3 | Test point TP2 only: solder a wire | FSYNC_OUT |

  J4 (1.27 mm pitch: 1 GND, 2 SDA, 3 SCL, 4 GPIO2, 5 3.3 V) also carries the
  I2C on its 3.3 V side, handy for a logic analyzer. How EXTSYNC reaches
  GPIO0 is still open: what the ESP32-P4X does with its connector's pin 4,
  and whether the buffer's direction pin needs driving low.
- **I2C.** The bus is shared with the board's audio codec and touch
  controller; a scan should find the sensor alone at 0x10.
- **Level shifting and the IR LED driver:** the sensor's GPIOs run at 1.8 V.
  Parts and wiring are in
  [../docs/vd55g1-esp32p4.md](../docs/vd55g1-esp32p4.md) (IR LED driver and
  level shifting).

## Bring-up order

`nireyecam-bench --cam PORT` runs steps 1–4 and drafts a bench-test issue
from them; add `--extsync <fps>` for the first half of step 5, with TRIG_OUT
wired to the sensor's GPIO0. `nireyecam-synctest` does the second half (the
README's "Check that two cameras expose together").

1. `INFO` over the serial port. `model_id` should read `0x53354731` (every
   VD55G1) and `revision` `0x3030` (cut 3) or `0x2020` (cut 2, which boots
   with ST's firmware patch; the console log says "ST firmware patch 2.2
   applied").
2. `START`, then record a few seconds with `nireyecam-record --cam p4=PORT
   --preview`. Check `csi_errors` in `INFO` stays 0. Steps 1 and 2 test
   different things: the sensor answers over I2C whatever the lane polarity,
   so a good step 1 with no frames in step 2 points at the polarity. Try
   `STOP`, `PNSWAP 0`, `START` before anything else (`INFO` shows `pn_swap`).
3. Confirm the timestamps. `INFO` and `frames.csv` should show one FSYNC per
   frame; see the VERIFY note in `capture.c`.
4. Measure USB throughput at a few crop sizes. `send_all()` in `main.c` is
   the first knob.
5. External sync: `TRIG <fps>` on the master, then `EXTSYNC 1` and `START`
   on every camera (it is refused while streaming). Keep each camera's frame
   period (`FLL`, see `frame_period_us` in `INFO`) shorter than the trigger
   period, or frames are missed. In `frames.csv`, frame intervals should equal
   the trigger period with no gaps in `seq`. Then check two cameras together:
   `nireyecam-record --trigger HZ --blink HZ` and `nireyecam-synctest` film
   one blinking LED and measure how far apart the exposures are. Send `STOP`
   before turning the trigger off, until it is known that the sensor stops
   without pulses (`nireyecam-record` does).

## Known gaps

- `EXTSYNC` follows ST's own Linux driver (GPIO0 = VT slave, `VT_CTRL` = 1)
  but has not run on hardware. VERIFY which edge starts a frame, the delay to
  the start of exposure, and that `START` and `STOP` complete with no pulses
  arriving. With `EXTSYNC 0` (the default after `INIT`), cameras free-run and
  are aligned by timestamps.
- Cut 2 sensors boot with ST's firmware patch 2.2, from its STM32 camera
  middleware (`main/st/`). The mainline Linux driver uses a newer patch,
  2.9, which is GPL-2.0 and so not included; VERIFY that 2.2 streams
  correctly. Cut 3 boots without a patch.
- `LED` is on/off (brightness set by the resistor); the sensor's STROBE
  decides when the LEDs are actually lit.
- Changing `RES` re-creates the CSI controller; `ROI` only moves the crop.
- `PNSWAP` writes two D-PHY test codes that ESP-IDF does not use, taken from
  the Raspberry Pi 5's D-PHY driver because ESP-IDF's PHY setup matches it.
  VERIFY on hardware that the ESP32-P4's PHY honours them.
