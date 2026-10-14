# nir-eye-cam

Small near-infrared cameras for mouse pupil and eye imaging. Each camera is a
Himax HM01B0 monochrome sensor (Arducam's Pico module) on a Seeed XIAO
RP2040 or Raspberry Pi Pico, streaming 8-bit frames over USB. Several cameras
record to one timeline through a shared sync wire.

```
firmware/   RP2040 firmware (Pico SDK, C): continuous capture, per-frame
            timestamps, sync in/out, IR LED PWM, USB serial streaming
firmware-p4/  ESP32-P4 firmware (ESP-IDF, C) for the VD55G1 global-shutter
            build: MIPI CSI-2 capture, same protocol (compiles; not yet run)
common/     nec_protocol.h, the packet format both firmwares share
host/       Python: nireyecam-record, nireyecam-align, nireyecam-list,
            plus a simulated camera for testing without hardware
docs/       hardware.md (parts, wiring, optics, sync, safety), protocol.md
```

Read [docs/hardware.md](docs/hardware.md) first. In particular, the cameras
are **time-aligned, not exposure-synchronized**. The Arducam module has no
trigger input, so each camera free-runs and every frame is timestamped
instead.

## Status

- **Host software:** tested against the simulator (`pytest` in `host/`):
  protocol, command replies, recording, and alignment against ground truth
  with clock drift and a missed sync pulse.
- **Firmware:** written but **not yet compiled or run on hardware**. Expect
  first-bring-up fixes. Points to check on the bench:
  - the measured frame rate (it depends on the module's oscillator);
  - USB throughput at 160×120;
  - the analog-gain register bits (`0x0205[6:4]`, from the Himax register
    description);
  - that VSYNC timestamps fire while the PIO reads the same pin.

## Build the firmware

```bash
brew install cmake
brew install --cask gcc-arm-embedded
git clone --recurse-submodules https://github.com/raspberrypi/pico-sdk ~/pico-sdk
export PICO_SDK_PATH=~/pico-sdk
cd firmware
cmake -B build -DNEC_BOARD=xiao      # or -DNEC_BOARD=pico
cmake --build build -j
```

Flash: hold BOOT (the "B" button on the XIAO) while plugging in USB, then copy
`build/nir_eye_cam_xiao.uf2` onto the `RPI-RP2` drive that appears.

## Record

```bash
cd host
pip install -e '.[preview]'        # or: uv pip install -e '.[preview]'
nireyecam-list                     # find the serial ports
nireyecam-record --out runs/test --cam eye=/dev/cu.usbmodem1101 --led 300 --preview
nireyecam-record --out runs/m01 --cam left=/dev/cu.usbmodem1101 --cam right=/dev/cu.usbmodem1201 \
    --sync-master left --led 300 --exp 100 --duration 600
```

Try everything without hardware with `--cam a=sim --cam b=sim`.

Each session folder holds `session.json` and `alignment.json`, plus one folder
per camera with:
- `video.mkv` (lossless FFV1);
- `frames.csv` (device timestamp and sensor frame counter per video frame);
- `sync.csv`;
- `frames_aligned.csv` (adds `t_s`, seconds on the shared timeline).

## Test

```bash
cd host && pip install -e '.[test]' && pytest
```
