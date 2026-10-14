# Contributing

Thanks for helping. Everyone taking part is expected to follow the [Code of
Conduct](CODE_OF_CONDUCT.md). This repo is two camera firmwares and a Python
host package that all speak one USB protocol, so most changes touch more than
one of them. This guide covers how to set up, what to check before opening a
pull request, and the rules that keep the pieces in agreement.

## Layout

| Path | What |
|---|---|
| `firmware/` | RP2040 firmware (Pico SDK) for the Arducam HM01B0 module on a XIAO RP2040 or Pico |
| `firmware-p4/` | ESP32-P4 firmware (ESP-IDF) for the ST VD55G1 global-shutter sensor |
| `common/nec_protocol.h` | The packet header both firmwares use |
| `host/` | `nireyecam`: recorder, aligner, camera discovery, and a simulated camera |
| `docs/` | Hardware, protocol, the VD55G1 build, and the miniscope design |

## Set up

- **Host package:** Python 3.9 or newer, and ffmpeg for the lossless-video test
  (`brew install ffmpeg`).
  ```bash
  cd host
  python3 -m venv .venv && . .venv/bin/activate
  pip install -e '.[test]'
  ```
- **RP2040 firmware:** Arm GNU Toolchain 14.3.rel1 and Pico SDK 2.3.1. See
  "Build the firmware" in the [README](README.md).
- **ESP32-P4 firmware:** ESP-IDF v6.1. See [firmware-p4/README.md](firmware-p4/README.md).

You only need the toolchains for the parts you change. CI builds everything
on every pull request.

## Before you open a pull request

Install the pre-commit hooks once per clone. They check whitespace and line
endings, lint the workflows, validate the config and citation files against
their schemas, and catch Python bugs (undefined names, unused imports) on
every commit:

```bash
pip install pre-commit    # or prefix the commands below with uvx
pre-commit install
pre-commit run --all-files   # run them on everything, any time
```

Then run what CI runs for the parts you touched:

```bash
# Host
cd host && python -m pytest

# RP2040: CI turns warnings into errors, so build the same way
export PICO_SDK_PATH=~/pico/pico-sdk
export PICO_TOOLCHAIN_PATH=~/pico/toolchain/arm-gnu-toolchain-14.3.rel1-darwin-arm64-arm-none-eabi
cmake -S firmware -B firmware/build-xiao -G Ninja -DNEC_BOARD=xiao -DNEC_WERROR=ON
cmake --build firmware/build-xiao        # and the same with NEC_BOARD=pico

# ESP32-P4: ESP-IDF already treats warnings as errors
cd firmware-p4 && . ~/esp/esp-idf/export.sh && idf.py build
```

CI also runs the host tests on **Python 3.9**, the oldest version the package
supports. Code that passes on a newer Python can fail there (it has happened:
see the CHANGELOG), so test on 3.9 too if you can, e.g.
`uv run --python 3.9 --with-editable . --with pytest pytest`.

Then:

- Add an entry under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for anything
  a user of the cameras or the tools would notice.
- Update the docs that describe what you changed (README, `docs/`,
  `firmware-p4/README.md`).

Changes that touch only Markdown files under `firmware/` or `firmware-p4/` do
not trigger a firmware build.

## Rules that keep the pieces in agreement

**The protocol lives in four places; change them together.**
1. `common/nec_protocol.h` (both firmwares);
2. `host/nireyecam/protocol.py`;
3. `docs/protocol.md`;
4. the simulator, `host/nireyecam/simulator.py`.

A header field or packet type that changes in one place and not the others
breaks recording silently, not loudly.

**Commands: one reply each.** Every command gets exactly one reply,
`OK <CMD> …` or `ERR <CMD> <reason>`. The host's `Camera.command()` waits for
that reply, so a command that answers twice, or not at all, hangs or
desynchronizes the recorder. A new command also goes into:
- both firmwares' command lists (`firmware/src/main.c` and
  `firmware-p4/main/main.c`, where it applies);
- `docs/protocol.md`;
- the simulator, if the host uses it.

**The simulator stands in for the hardware.** The host tests drive it
exactly as they would a real camera. If you change firmware behavior the host
depends on, update `simulator.py` to match, or the tests keep passing against
behavior the cameras no longer have.

**Mark what is not verified on hardware.** Neither firmware has run on a real
board yet. Where code or docs rely on something only a bench test can
confirm, say so with a `VERIFY` comment or note, as the existing code does.
Don't describe behavior as tested on hardware unless it was.

**IR stays off unless the host turns it on.** Both firmwares boot with the IR
LEDs off and switch them off when the host disconnects. Keep it that way:
these LEDs shine into an animal's eye.

**Citation metadata lives in two files; change them together.**
`CITATION.cff` drives GitHub's "Cite this repository". `.zenodo.json` is what
Zenodo records for each archived release, and when it exists Zenodo uses it
instead of `CITATION.cff`. Both list the same authors, affiliations, ORCID iDs,
abstract and keywords: an author added to one and not the other is missing
from half the citations.

**Licensing.** The project uses a Princeton University license (see
[LICENSE](LICENSE)). Code adapted from elsewhere keeps its notice (see the
README's License section). Don't copy GPL code into the firmware. In
particular, the VD55G1 Linux driver may be consulted for register values but
not copied. Its firmware patch blob is GPL too, and is deliberately not
included.

## Commits and pull requests

- Questions about building or using the cameras go in **Discussions > Q&A**,
  which has its own form.
- Issues have three forms: bug report, feature request, and **bench test
  result**. Use the last to record what a measurement on real hardware
  showed for anything marked `VERIFY`, whether or not it matches the code.
  Pull requests get a checklist mirroring this guide.
- Work on a branch and open a pull request to `main`. Both CI workflows must
  pass before merging.
- Write commit messages like the existing history: a short imperative summary
  line, then a body explaining *why*, especially for anything found or
  measured.
- Keep unrelated changes in separate commits or pull requests.

## Releases

Follow the release steps at the top of [CHANGELOG.md](CHANGELOG.md).
