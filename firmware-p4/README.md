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
| `main/syncio.c` | Timestamps for FSYNC and sync edges (GPIO interrupts), random sync pulse train, frame-trigger output (LEDC), IR enable. |
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
| FSYNC in | GPIO20 | 13 | sensor GPIO2 (FSYNC_OUT), through a level shifter |
| SYNC in | GPIO21 | 11 | the shared sync line |
| SYNC out | GPIO22 | 12 | the shared sync line (sync master only) |
| TRIG out | GPIO23 | 7 | every sensor's GPIO0 (EXTSYNC), through a level shifter |
| LED enable | GPIO32 | 26 | the gate that lets the sensor's STROBE (GPIO1) reach the LED driver |

These pins are on J1 and the board itself does not use them: ESP-IDF's DVP
camera example puts an external camera on the same ones for v3 boards.
GPIO26/27 (the LCD adapter's default backlight and reset), GPIO37/38 (UART0)
and GPIO0/1/45 (not connected by default) are left alone.

## Before first power-up

Open questions about the P-Board, to check with a meter and its data brief
before connecting it:

- **Lane polarity.** ST's Raspberry Pi overlays set `lane-polarities <1 1>`
  for some of its VD55G1 boards, and ST warned that one of its adapter boards
  does not match the Raspberry Pi pinout. The ESP32-P4 cannot swap a lane's
  P and N in software, so a swapped board needs a cable that swaps them.
- **The cable.** The P-Board has a 22-pin FFC output; the ESP32-P4X board
  has a 15-pin, 1.0 mm Raspberry-Pi-style connector. Check the pinouts
  before buying a 22-to-15-pin cable.
- **Power enable.** ST's overlay powers the module's 2.8 V rail from a
  regulator the host switches through the camera connector's enable pin.
  Check what the ESP32-P4X board does with that pin.
- **XSHUTDOWN.** No board GPIO drives it (`PIN_XSHUTDOWN` is -1), so the
  P-Board must hold it high.
- **CLKIN.** `SENSOR_EXT_CLK_HZ` is 12 MHz, as in ST's overlay. The sensor's
  PLL is computed from it.
- **Sensor GPIOs.** ST's overlay drives the LED strobe from sensor GPIO2;
  this firmware uses GPIO1 for STROBE and GPIO2 for FSYNC_OUT. Check which
  sensor GPIOs the P-Board brings out, and swap the roles in `board.h` if
  needed.
- **I2C.** The bus is shared with the board's audio codec and touch
  controller; a scan should find the sensor alone at 0x10.
- Put level shifters between the sensor's 1.8 V GPIOs and the ESP32-P4.

## Bring-up order

`nireyecam-bench --cam PORT` runs steps 1–4 and drafts a bench-test issue
from them; add `--extsync <fps>` for the first half of step 5, with TRIG_OUT
wired to the sensor's GPIO0. The blinking-LED check is still by hand.

1. `INFO` over the serial port. `model_id` should read `0x53354731` (every
   VD55G1) and `revision` `0x3030` (cut 3). Cut 2 (`0x2020`) needs ST's
   firmware patch, which is not included.
2. `START`, then record a few seconds with `nireyecam-record --cam p4=PORT
   --preview`. Check `csi_errors` in `INFO` stays 0.
3. Confirm the timestamps. `INFO` and `frames.csv` should show one FSYNC per
   frame; see the VERIFY note in `capture.c`.
4. Measure USB throughput at a few crop sizes. `send_all()` in `main.c` is
   the first knob.
5. External sync: `TRIG <fps>` on the master, then `EXTSYNC 1` and `START`
   on every camera (it is refused while streaming). Keep each camera's frame
   period (`FLL`, see `frame_period_us` in `INFO`) shorter than the trigger
   period, or frames are missed. In `frames.csv`, frame intervals should equal
   the trigger period with no gaps in `seq`. Then film a fast-blinking LED:
   every camera must see it in the same state. Send `STOP` before turning the
   trigger off, until it is known that the sensor stops without pulses.

## Known gaps

- `EXTSYNC` follows ST's own Linux driver (GPIO0 = VT slave, `VT_CTRL` = 1)
  but has not run on hardware. VERIFY which edge starts a frame, the delay to
  the start of exposure, and that `START` and `STOP` complete with no pulses
  arriving. With `EXTSYNC 0` (the default after `INIT`), cameras free-run and
  are aligned by timestamps.
- Cut 2 sensors need ST's firmware patch, which is not included. Cut 3 boots
  without it, as in ST's STM32 middleware; VERIFY that it streams correctly
  (the mainline Linux driver patches every VD55G1).
- `LED` is on/off (brightness set by the resistor); the sensor's STROBE
  decides when the LEDs are actually lit.
- Changing `RES` re-creates the CSI controller; `ROI` only moves the crop.
