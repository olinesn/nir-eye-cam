# Changelog

Notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version is set in five places:
- `FW_VERSION` in `firmware/src/main.c` (`X.Y.Z`) and
  `firmware-p4/main/main.c` (`X.Y.Z-p4`);
- the host package, in `host/pyproject.toml` and `host/nireyecam/__init__.py`;
- `version` in `CITATION.cff`.

To release:
1. Rename **Unreleased** below to the version and date, and start a new,
   empty **Unreleased** section above it.
2. Update those five version strings to match, and set `date-released` in
   `CITATION.cff` to the same date.
3. Tag the commit `vX.Y.Z`.
4. Publish a GitHub release from the tag. Zenodo archives published
   releases, not tags.

## [Unreleased]

### Added

- Two-camera sync test. `nireyecam-record --trigger HZ` puts every camera on
  EXTSYNC and has the driving camera (`--sync-master`, default the first)
  output the frame trigger; `--blink HZ` has it blink a test LED on the new
  `BLINK` output (ESP32-P4 GPIO33, J1-29). `nireyecam-synctest SESSION`
  finds the LED in each camera's frames and measures how far apart the
  exposures are: from frames in which both cameras caught an edge of the
  blink when they are within one exposure, from the blink's phase otherwise,
  with a standard error. It also reports how far the timestamps misstate
  that offset, frames where one camera saw the LED clearly on and the other
  clearly off, and missed triggers, and passes when every 2-second stretch is
  within `--tolerance-us` (default 100). The simulator now models TRIG,
  EXTSYNC, BLINK and a global-shutter exposure window, and free-running
  simulated cameras now drift apart at their own clock rates, as real ones
  do.

- `docs/vd55g1-esp32p4.md`: an **IR LED driver and level shifting** section
  for the bench prototype. A TXB0104 breakout shifts FSYNC, TRIG and STROBE
  between the sensor's 1.8 V and the ESP32-P4's 3.3 V. A 74HCT08 on 5 V ANDs
  the sensor's STROBE with the firmware's LED enable and drives an IRLB8721
  MOSFET, so the IR LEDs (2 × Vishay TSAL6400, 940 nm) light only during
  exposure and only when the host asked; pull-downs keep them off at
  power-up. It gives a schematic, parts, resistor values, and the safety
  rules. The wiring diagram now names these parts.

### Fixed

- ESP32-P4 firmware: `TRIG` ran off the RC oscillator below ~38 Hz (an
  inaccurate frame rate, and no output at all below ~17 Hz), because the LEDC
  driver picked the clock source from the frequency. It now runs off the
  40 MHz crystal at every rate, choosing the PWM resolution to fit, so `TRIG`
  and `BLINK` can also run together (they share one clock selector).
- `nireyecam-record`: `exposure_ms` in `session.json` used the measured frame
  period to get the line time, which is wrong when a trigger sets the period.
  It now uses the device's own line time when it reports one.

### Changed

- ESP32-P4 build docs: the P-Board cable and reset are resolved as far as
  the documents allow. The cable is a Raspberry Pi 5 camera cable (22-pin →
  15-pin): the P-Board's output uses the Pi 5 pinout, and the ESP32-P4X
  board's 15-pin connector carries the Raspberry Pi signals numbered from the
  other end (Espressif's camera adapter schematic). The P-Board's sensor reset
  comes from that connector's CAM_IO0 (Espressif's pin 5, labelled RESET),
  which must be high; `firmware-p4/README.md` says how to check it and what
  to do if it is low. The wiring diagram shows both.

### Changed

- README: leads with the ESP32-P4 build, which is the one being built. The
  opening paragraph, the folder overview, the status list, and the Build and
  Flash sections now put it first; the HM01B0 + RP2040 build follows as the
  original, simpler build. Flashing on the ESP32-P4X-Function-EV-Board goes
  through its USB Serial/JTAG port (`/dev/cu.usbmodem…`), and the camera
  streams on its USB 2.0 port.

### Added

- README: a **Parts and wiring** section with a diagram and a parts table for
  the ESP32-P4 build (ESP32-P4X-Function-EV-Board + ST VD55G1); the original
  RP2040 build stays in `docs/hardware.md`. It is drawn by
  `scripts/wiring.py` from `docs/wiring.yml` and the firmware's pin headers,
  so a pin moved in the firmware moves in the diagram. A pre-commit check
  (run in CI) fails while the README is out of date, or when
  `docs/wiring.yml` names a pin the firmware does not define.

### Changed

- ESP32-P4 firmware: `board.h` is now the pin map for Espressif's
  ESP32-P4X-Function-EV-Board (chip revision v3.1+, which the build targets),
  instead of placeholders. Camera I2C on GPIO7/8 (its camera connector);
  FSYNC in, SYNC in, SYNC out and TRIG out on GPIO20-23, and the IR LED gate
  on GPIO32 (was GPIO24, which the board does not bring out), all on header
  J1. `firmware-p4/README.md` gives the wiring table, which USB port does
  what, and the P-Board questions to check before power-up (lane polarity,
  the 22-to-15-pin cable, power enable, XSHUTDOWN, CLKIN, which sensor GPIOs
  it brings out). `docs/vd55g1-esp32p4.md` recommends this board.

## [0.3.0] - 2026-10-14

### Added

- `nireyecam-bench`: measures one camera on the bench and drafts a **Bench
  test result** issue. It reports:
  - frame rate, timing jitter and dropped frames, at the default and the
    shortest frame length (the second shows what the USB link carries);
  - exposure linearity and black level;
  - the analog-gain steps;
  - the IR LEDs' effect on the image;
  - the sync loopback and, on the ESP32-P4, the sensor's model ID,
    `csi_errors` and external sync (`--extsync`).

  Each covered form item gets a suggested result. Works on the simulator.
- `nireyecam-sensortest`: analyses the sensor bench test from recorded
  frames, EMVA 1288-style. `ptc`: conversion gain, read noise, saturation
  capacity, linearity, QE per emission band, dark current, DSNU / PRNU (after
  removing the shading) and the corner-to-centre response, from a manifest of
  light and dark captures. `snr`: signal-to-noise per cell-sized region over
  the same piece of sample for every sensor, the excitation power that
  reaches a target SNR, drift, and the detectability of a small LED step.
  `alternation`: frame colour labels against content, dropped frames, and
  leakage into the other colour's frames. Tested against synthetic sensors
  with known parameters.
- `docs/sensor-bench-test.md`: the bench test plan for choosing between the
  VD55G1 and the Mira050. Lensless EMVA 1288-style measurements (photon
  transfer, QE through the real emission filters, dark current, fixed-pattern
  noise, corner fall-off), signal-to-noise per cell on fluorescent samples
  behind identical optics, two-LED alternation on each sensor, power and heat,
  a results table and decision rules. The winner's ESP32-P4 build is then
  checked with `nireyecam-bench`. The questions for ST and ams are listed with
  the step each answer affects, and what happens without one.
- `docs/hardware.md`: a **Tether and commutator** section for freely moving
  animals. The Neuropixels cable CBL_1000 cannot carry the ESP32-P4 build's
  USB at any speed: it is one twisted pair, and USB 2.0 needs four wires (a
  USB-C-to-USB-C cable, five plus a shield). It compares the cable with the
  USB 2.0 and USB-C limits, estimates the camera's power draw and the voltage
  lost over a thin tether, summarizes Doric's 24-contact commutator, and ranks
  the options: the camera's own cable through the joint's 4-pin auxiliary
  port at full speed first, then a serializer over one pair, then wireless.
  A draft of that camera cable gives the pin map, the two cables, the bench
  checks, and the open question of where the SYNC line goes, since USB takes
  all four contacts.
- `docs/vd55g1-fpdlink.md`: a design sketch for a freely moving build. Only
  the VD55G1 and a TI DS90UB953 serializer ride on the head; one thin coax
  through two of the commutator's four free contacts carries power, video,
  I²C and GPIO to a DS90UB934 deserializer and the ESP32-P4 at the base. The
  953 runs in its backward-compatible DVP mode, 700 Mbit/s on the coax
  instead of 4.16 Gbit/s. SYNC is timestamped at the base, so no sync wire
  crosses the joint. The plan starts with a bench test of the link through
  the joint on TI evaluation boards. Nothing is built.
- `docs/miniscope.md`: a design sketch for a dual-colour (GCaMP + red
  indicator) one-photon miniscope built from the VD55G1 build, as a
  replacement for the Inscopix nVue. It compares one sensor with alternating
  LEDs against two sensors, assigns the VD55G1's GPIOs for alternating
  excitation, notes what each option needs from the FPD-Link tether
  (`docs/vd55g1-fpdlink.md`), designs the excitation path (LED condensers,
  filter angles, combining two colours, fibre-coupled light, LED drivers),
  lists filter sets and indicator crosstalk, and compares candidate sensors
  (VD55G1, ams Mira050/Mira220, Sony IMX900 and IMX662, PYTHON480) per cell
  of tissue. Design only; open questions are marked VERIFY.

## [0.2.0] - 2026-10-14

### Added

- ESP32-P4 firmware: `EXTSYNC 1` now works instead of returning an error.
  Each pulse on the sensor's GPIO0 starts one frame ("VT slave" mode: GPIO0
  mode `0x0A`, then `VT_CTRL` `0x0309` = 1, the sequence ST's own VD55G1
  Linux driver uses). It is refused while streaming, `INIT` returns the
  sensor to free-running, and `INFO`'s `extsync` now follows the sensor. Not
  yet run on hardware: which edge starts a frame, the delay to exposure, and
  `START`/`STOP` with no pulses arriving are marked VERIFY.
- `docs/hardware.md`: a **Depth sensing** section for the pupil-dilation and
  facial-expression projects. It compares small depth sensors (direct and
  indirect ToF, structured light, RealSense D405, stereo), estimates stereo
  depth precision for the VD55G1 build, and recommends an approach for each
  project.

### Fixed

- ESP32-P4 firmware: the VD55G1 would never have booted. The firmware read
  model ID `0x53354731` as "revision 2, needs a patch" and refused it, but
  every VD55G1 reads `0x53354731`; `0x53354733` is the VD55G4 / VD65G4, which
  it accepted instead. The silicon cut is now read from register `0x0004`:
  cut 3 (`0x3030`) boots, and cut 2 (`0x2020`) still needs ST's patch. `INFO`
  reports it as `revision`.

## [0.1.0] - 2026-10-14

### Added

- **RP2040 camera firmware** (`firmware/`) for the Arducam HM01B0 module on a
  Seeed XIAO RP2040 or Raspberry Pi Pico:
  - continuous PIO + DMA capture at 160×120 (2×2 binned), 320×240 or 320×320;
  - a device timestamp on every frame (VSYNC edge) and every sync-line edge;
  - a random-interval sync pulse generator, so free-running cameras can be
    aligned;
  - IR LED PWM, switched off when the host disconnects;
  - a binary packet protocol over USB CDC, with text commands.
- **ESP32-P4 camera firmware** (`firmware-p4/`) for the ST VD55G1
  global-shutter sensor:
  - MIPI CSI-2 RAW8 capture (1 lane, ISP bypassed) into a PSRAM frame pool;
  - USB 2.0 high-speed CDC;
  - the same protocol as the RP2040 build, plus the `ROI`, `DGAIN`, `TRIG` and
    `EXTSYNC` commands.

  `EXTSYNC` is a stub until ST's UM3224 sequence is implemented, and
  revision-2 sensors need ST's firmware patch, which is not included.
- **Shared packet header**, `common/nec_protocol.h`, used by both firmwares and
  mirrored by `host/nireyecam/protocol.py`.
- **Host package `nireyecam`** (`host/`), Python 3.9–3.14:
  - `nireyecam-record`: configures one or more cameras, records lossless FFV1
    video plus per-frame and sync CSVs, and aligns the cameras afterwards;
  - `nireyecam-align`: puts every camera's frames on one timeline by matching
    the random sync pulse trains and fitting each camera's clock offset and
    drift;
  - `nireyecam-list`: finds connected cameras;
  - a simulated camera (`--cam name=sim`) that speaks the firmware protocol,
    with drifting clocks and a shared sync line, for testing without hardware.
- **Docs:**
  - `docs/hardware.md`: parts, wiring, optics for a mouse eye, sync scheme,
    IR safety, USB bandwidth;
  - `docs/protocol.md`: the USB protocol and command set;
  - `docs/vd55g1-esp32p4.md`: the global-shutter build;
  - README sections on building and flashing both firmwares.
- **CI (GitHub Actions):**
  - `firmware.yml` builds the RP2040 firmware (Pico and XIAO) and the ESP32-P4
    firmware, fails on any compiler warning, and uploads the binaries as
    artifacts. It skips commits that change only Markdown.
  - `host.yml` runs the host tests on Python 3.9 and 3.14.
  - `pre-commit.yml` runs the pre-commit hooks on every push and pull request.
  - `labeler.yml` labels pull requests by the parts they change;
    `stale.yml` marks and closes inactive issues and pull requests, with
    exemptions; `welcome.yml` greets first-time contributors.
- **Contributor tooling:** a pre-commit configuration (whitespace, line
  endings, actionlint, schema checks for workflows, config and citation
  files, ruff); issue forms, a Q&A discussion form and a pull request
  template; CONTRIBUTING, SUPPORT, SECURITY, CODE_OF_CONDUCT, CODEOWNERS,
  CITATION.cff and .zenodo.json; Dependabot for GitHub Actions; release-note
  categories.
- Princeton University license, adapted from Princeton's WordNet license, with
  a limitation-of-liability clause taken from BSD-3-Clause; README status and
  license badges.

### Fixed

- `nireyecam-record` crashed when stopping a recording on Python 3.9–3.12.
  `CameraRecorder`, a `threading.Thread` subclass, stored its stop flag in
  `self._stop`, which shadows a method `Thread.join()` calls on those
  versions. It was pushed to `main` before CI tested older Pythons.

[Unreleased]: https://github.com/olinesn/nir-eye-cam/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/olinesn/nir-eye-cam/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/olinesn/nir-eye-cam/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/olinesn/nir-eye-cam/releases/tag/v0.1.0
