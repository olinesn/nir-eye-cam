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

[Unreleased]: https://github.com/olinesn/nir-eye-cam/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/olinesn/nir-eye-cam/releases/tag/v0.1.0
