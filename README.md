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
- **RP2040 firmware:** compiles with no warnings (`-Wall -Wextra`, Pico SDK
  2.3.1, Arm GNU Toolchain 14.3.rel1) for both boards, but has **not yet run
  on hardware**. Expect first-bring-up fixes. Points to check on the bench:
  - the measured frame rate (it depends on the module's oscillator);
  - USB throughput at 160×120;
  - the analog-gain register bits (`0x0205[6:4]`, from the Himax register
    description);
  - that VSYNC timestamps fire while the PIO reads the same pin.

## Build the firmware

GitHub Actions builds both firmwares on every push or pull request that
touches them ([.github/workflows/firmware.yml](.github/workflows/firmware.yml)),
fails on any compiler warning, and attaches the `.uf2` / `.bin` files to the
run as artifacts. To build locally:

One-time setup (what this repo was built with): CMake and Ninja from
Homebrew, Arm's GNU toolchain for macOS (the full one with newlib, unpacked
to `~/pico/toolchain`), and the Pico SDK.

```bash
brew install cmake ninja
mkdir -p ~/pico/toolchain && cd ~/pico
curl -LO https://developer.arm.com/-/media/Files/downloads/gnu/14.3.rel1/binrel/arm-gnu-toolchain-14.3.rel1-darwin-arm64-arm-none-eabi.tar.xz
tar -xJf arm-gnu-toolchain-14.3.rel1-darwin-arm64-arm-none-eabi.tar.xz -C toolchain
git clone --depth 1 --branch 2.3.1 --recurse-submodules --shallow-submodules https://github.com/raspberrypi/pico-sdk
```

Build (one folder per board):

```bash
export PICO_SDK_PATH=~/pico/pico-sdk
export PICO_TOOLCHAIN_PATH=~/pico/toolchain/arm-gnu-toolchain-14.3.rel1-darwin-arm64-arm-none-eabi
cd firmware
cmake -B build-xiao -G Ninja -DNEC_BOARD=xiao -DPICOTOOL_FETCH_FROM_GIT_PATH=$HOME/pico/picotool
cmake --build build-xiao        # -> build-xiao/nir_eye_cam_xiao.uf2
```

Use `-DNEC_BOARD=pico` (and `build-pico`) for a Raspberry Pi Pico. The SDK
downloads and builds `picotool` (its UF2 tool) on first use;
`PICOTOOL_FETCH_FROM_GIT_PATH` keeps one shared copy.

Flash: hold BOOT (the "B" button on the XIAO) while plugging in USB, then copy
the `.uf2` onto the `RPI-RP2` drive that appears.

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

## License

MIT; see [LICENSE](LICENSE). Third-party code included here keeps its own
license:

- `firmware/src/camera.c` adapts the sensor setup and PIO program from Arm's
  [hm01b0-library-for-pico](https://github.com/ArmDeveloperEcosystem/hm01b0-library-for-pico)
  (MIT, Copyright 2023 Arm Limited); its notice is in
  [firmware/LICENSE-hm01b0-library-for-pico](firmware/LICENSE-hm01b0-library-for-pico).
- `firmware/pico_sdk_import.cmake` is copied unchanged from the
  [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk) (BSD-3-Clause).

The VD55G1 driver in `firmware-p4/` was written from the sensor's register
map; the GPL Linux driver was consulted for register values, but no code was
copied from it.
