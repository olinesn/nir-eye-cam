# ESP32-P4 firmware: VD55G1 global-shutter camera

Skeleton firmware for the build in [../docs/vd55g1-esp32p4.md](../docs/vd55g1-esp32p4.md):
an ST VD55G1 promodule on MIPI CSI-2 → ESP32-P4 → USB 2.0 high speed. It
speaks the same packet protocol as the RP2040 build
([../common/nec_protocol.h](../common/nec_protocol.h)), so `nireyecam-record`
and `nireyecam-align` work with it unchanged.

**Status: written against the ESP-IDF v6.1 and esp_tinyusb 2.4 sources, but
not yet compiled or run.**

## Layout

| File | Does |
|---|---|
| `main/board.h` | Pin map and hardware constants. **Placeholder GPIOs**; set them for your board. |
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

`esp_tinyusb` is fetched automatically (see `main/idf_component.yml`). The
camera shows up as a USB serial device named "NIR Eye Cam" on the
high-speed port; logs go to the board's UART / USB-serial-JTAG console.

## Before first power-up

- Set the GPIOs in `main/board.h` for your board and wiring.
- Set `SENSOR_EXT_CLK_HZ` to the frequency actually on the module's CLKIN.
  The sensor's PLL is computed from it.
- Check the adapter's MIPI lane polarity against the ESP32-P4 board's camera
  connector. ST warned that one of its adapter boards does not match the
  Raspberry Pi pinout.
- Put level shifters between the sensor's 1.8 V GPIOs and the ESP32-P4.

## Bring-up order

1. `INFO` over the serial port. `model_id` should read `0x53354733` (rev 3).
   Rev 2 (`0x53354731`) needs ST's firmware patch, which is not included.
2. `START`, then record a few seconds with `nireyecam-record --cam p4=PORT
   --preview`. Check `csi_errors` in `INFO` stays 0.
3. Confirm the timestamps. `INFO` and `frames.csv` should show one FSYNC per
   frame; see the VERIFY note in `capture.c`.
4. Measure USB throughput at a few crop sizes. `send_all()` in `main.c` is
   the first knob.
5. External sync: fill in `vd55g1_set_external_sync()` from ST UM3224, then
   `TRIG <fps>` on the master and `EXTSYNC 1` on every camera.

## Known gaps

- `EXTSYNC` returns an error until the UM3224 register sequence is
  implemented. Without it, cameras free-run and are aligned by timestamps.
- Rev 2 sensors need ST's firmware patch, which is not included.
- `LED` is on/off (brightness set by the resistor); the sensor's STROBE
  decides when the LEDs are actually lit.
- Changing `RES` re-creates the CSI controller; `ROI` only moves the crop.
