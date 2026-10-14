# nir-eye-cam

[![Firmware](https://github.com/olinesn/nir-eye-cam/actions/workflows/firmware.yml/badge.svg?branch=main)](https://github.com/olinesn/nir-eye-cam/actions/workflows/firmware.yml)
[![Host tests](https://github.com/olinesn/nir-eye-cam/actions/workflows/host.yml/badge.svg?branch=main)](https://github.com/olinesn/nir-eye-cam/actions/workflows/host.yml)
[![pre-commit](https://github.com/olinesn/nir-eye-cam/actions/workflows/pre-commit.yml/badge.svg?branch=main)](https://github.com/olinesn/nir-eye-cam/actions/workflows/pre-commit.yml)
[![License: Princeton](https://img.shields.io/badge/license-Princeton-orange.svg)](LICENSE)
[![Code owners: @olinesn](https://img.shields.io/badge/code%20owners-%40olinesn-blue.svg)](.github/CODEOWNERS)

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
            nireyecam-bench, plus a simulated camera for testing without
            hardware
docs/       hardware.md (parts, wiring, optics, sync, safety, depth),
            protocol.md
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

## Flash the firmware

Use the files you built, or download them from the latest successful CI run
on `main` (GitHub keeps them for 90 days):

```bash
run=$(gh run list -R olinesn/nir-eye-cam -w firmware.yml -b main -s success -L 1 --json databaseId -q '.[0].databaseId')
gh run download "$run" -R olinesn/nir-eye-cam -n nir-eye-cam-xiao -D fw-xiao
```

For the other boards use `-n nir-eye-cam-pico -D fw-pico` or
`-n nir-eye-cam-esp32p4 -D fw-esp32p4`.

### RP2040 (XIAO or Pico): drag and drop

1. Put the board in its USB bootloader:
   - **XIAO RP2040:** hold **B** (BOOT) while plugging in USB. If it is
     already plugged in, hold **B**, press and release **R** (RESET), then
     release **B**.
   - **Pico:** hold **BOOTSEL** while plugging in USB (it has no reset button).
2. A drive named `RPI-RP2` appears. Copy the `.uf2` onto it:
   ```bash
   cp -X fw-xiao/nir_eye_cam_xiao.uf2 /Volumes/RPI-RP2/
   ```
   `-X` leaves out macOS extended attributes, which avoids an error some
   macOS versions report even when the copy succeeded. A Finder drag works too.
3. The drive disappears and the board restarts as the camera:
   `nireyecam-list` should show a port named "NIR Eye Cam".

Reflashing always goes through step 1: this firmware has no "reboot to
bootloader" USB command, so the button is the way in. From the command line
instead, `brew install picotool`, then with the board in bootloader mode
`picotool load -x fw-xiao/nir_eye_cam_xiao.uf2`. (The picotool the Pico SDK
builds for itself has no USB support and cannot flash.)

### ESP32-P4: esptool

ESP32-P4 boards usually have two USB connections:
- a **UART / USB-serial-JTAG** port, used for flashing and logs;
- the **USB 2.0 high-speed (OTG)** port this firmware streams frames on.

Flash through the first one. To find its name, compare `ls /dev/cu.*`
before and after plugging it in.

From your own build (ESP-IDF loaded):

```bash
cd firmware-p4 && . ~/esp/esp-idf/export.sh
idf.py -p /dev/cu.usbserial-XXXX flash monitor   # Ctrl-] leaves the monitor
```

From a CI download (no ESP-IDF needed: `uvx` runs esptool without
installing it, or use `pip install esptool`):

```bash
cd fw-esp32p4
uvx esptool --chip esp32p4 -p /dev/cu.usbserial-XXXX -b 460800 --before default-reset --after hard-reset write-flash @flash_args
```

`flash_args` lists the bootloader, partition table and app with their
addresses. If the board does not enter download mode by itself, hold
**BOOT**, press **RESET**, release **BOOT**, and run the command again. Then
plug the high-speed port into the computer: `nireyecam-list` should show
"NIR Eye Cam".

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

## Check a new camera on the bench

`nireyecam-bench` runs the checks the firmware still needs on real hardware
and drafts a [Bench test result](.github/ISSUE_TEMPLATE/bench_test.yml)
issue. Aim the camera at a still, evenly lit scene (white paper under the IR
LEDs works) and leave it alone for the minute or two the run takes.

```bash
nireyecam-bench --cam /dev/cu.usbmodem1101 --led 300     # writes bench-report.md
nireyecam-bench --cam /dev/cu.usbmodem1101 --led 300 --sync --json bench.json
```

It measures, from the camera's own timestamps:
- frame rate, jitter and dropped frames at each resolution, once at the
  default frame length and once at the shortest, which shows how much the
  USB link carries;
- exposure linearity and black level;
- the analog-gain steps against 1x / 2x / 4x / 8x;
- the IR LEDs' effect on the image (`--led`);
- with `--sync`, the sync pulses, with SYNC_OUT wired back to SYNC_IN.

On the ESP32-P4, it also checks the sensor's model ID and that `csi_errors`
stays 0. `--extsync HZ` tests external sync, with TRIG_OUT wired to the
sensor's GPIO0. The report suggests Confirms or Differs for each item, and
leaves the numbers-only items, such as frame rate, to you. The camera's
settings are restored at the end and the IR LEDs left off. Try it with
`--cam sim`.

## Test

```bash
cd host && pip install -e '.[test]' && pytest
```

GitHub Actions runs these on Python 3.9 and 3.14 whenever `host/` changes
([.github/workflows/host.yml](.github/workflows/host.yml)).

## Getting help

[SUPPORT.md](SUPPORT.md) says where each kind of request goes: questions to
Discussions > Q&A; bugs, ideas and bench-test results to the issue forms;
security problems privately.

## Citing

If you use nir-eye-cam in your work, please cite it. The citation details are
in [CITATION.cff](CITATION.cff); GitHub's "Cite this repository" button (in
the repository's sidebar) turns them into APA or BibTeX.

## Changes and contributing

See [CHANGELOG.md](CHANGELOG.md) for what has changed, and
[CONTRIBUTING.md](CONTRIBUTING.md) for how to set up, test, and submit changes.
Everyone taking part is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). To report a security problem, see
[SECURITY.md](SECURITY.md); please don't open a public issue for it.

## License

A Princeton University license, adapted from the license of Princeton's
WordNet (SPDX `WordNet`); see [LICENSE](LICENSE). In short:
- anyone may use, copy, modify and distribute the software for any purpose,
  without fee, provided the copyright notice and disclaimer stay on every copy;
- it comes with no warranty, and neither Princeton University nor the
  contributors are liable for damages arising from its use;
- Princeton's name may not be used in advertising;
- Princeton University holds the copyright.

Third-party code included here keeps its own license:

- `firmware/src/camera.c` adapts the sensor setup and PIO program from Arm's
  [hm01b0-library-for-pico](https://github.com/ArmDeveloperEcosystem/hm01b0-library-for-pico)
  (MIT, Copyright 2023 Arm Limited); its notice is in
  [firmware/LICENSE-hm01b0-library-for-pico](firmware/LICENSE-hm01b0-library-for-pico).
- `firmware/pico_sdk_import.cmake` is copied unchanged from the
  [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk) (BSD-3-Clause).

The VD55G1 driver in `firmware-p4/` was written from the sensor's register
map; the GPL Linux driver was consulted for register values, but no code was
copied from it.
