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
- `docs/vd55g1-fpdlink.md`: a design sketch for a freely moving build. Only
  the VD55G1 and a TI DS90UB953 serializer ride on the head; one thin coax
  through two of the commutator's four free contacts carries power, video,
  I²C and GPIO to a DS90UB934 deserializer and the ESP32-P4 at the base. The
  953 runs in its backward-compatible DVP mode, 700 Mbit/s on the coax
  instead of 4.16 Gbit/s. SYNC is timestamped at the base, so no sync wire
  crosses the joint. The plan starts with a bench test of the link through
  the joint on TI evaluation boards. Nothing is built.
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

- `docs/miniscope.md`: a design sketch for a dual-colour (GCaMP + red
  indicator) one-photon miniscope built from the VD55G1 build, as a
  replacement for the Inscopix nVue. It compares one sensor with alternating
  LEDs against two sensors, assigns the VD55G1's GPIOs for alternating
  excitation, designs the excitation path (LED condensers, filter angles,
  combining two colours, fibre-coupled light, LED drivers), lists filter sets
  and indicator crosstalk, and compares candidate sensors (VD55G1, ams
  Mira050/Mira220, Sony IMX900 and IMX662, PYTHON480) per cell of tissue.
  Design only; open questions are marked VERIFY.
- `docs/sensor-bench-test.md`: the bench test plan for choosing between the
  VD55G1 and the Mira050. Lensless EMVA 1288-style measurements (photon
  transfer, QE through the real emission filters, dark current, fixed-pattern
  noise, corner fall-off), signal-to-noise per cell on fluorescent samples
  behind identical optics, two-LED alternation on each sensor, power and heat,
  a results table and decision rules. The winner's ESP32-P4 build is then
  checked with `nireyecam-bench`.

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

[Unreleased]: https://github.com/olinesn/nir-eye-cam/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/olinesn/nir-eye-cam/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/olinesn/nir-eye-cam/releases/tag/v0.1.0
