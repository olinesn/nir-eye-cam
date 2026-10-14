<!-- Fixing a security problem? Don't describe the vulnerability here: a pull
request is visible to everyone who can see the repository. Report it
privately first, as SECURITY.md describes, and keep this description to what
the change does. -->

## What and why

<!-- What this changes, and the reason. Link the issue if there is one. -->

## Parts touched

- [ ] RP2040 firmware (`firmware/`)
- [ ] ESP32-P4 firmware (`firmware-p4/`)
- [ ] USB protocol or commands
- [ ] Host tools (`host/`)
- [ ] CI, docs only

## Checks

See [CONTRIBUTING.md](https://github.com/olinesn/nir-eye-cam/blob/main/CONTRIBUTING.md) for the commands.

- [ ] pre-commit hooks pass (`pre-commit run --all-files`; CI runs them too)
- [ ] Host tests pass (and on Python 3.9, if host code changed)
- [ ] Firmware builds with warnings as errors (`-DNEC_WERROR=ON` / `idf.py build`) for what changed
- [ ] Protocol or command changes are in all four places: `common/nec_protocol.h`, `host/nireyecam/protocol.py`, `docs/protocol.md`, `host/nireyecam/simulator.py`
- [ ] The simulator still matches firmware behavior the host relies on
- [ ] IR LEDs still default to off and switch off on disconnect
- [ ] Changes to authors, affiliations or keywords are in both `CITATION.cff` and `.zenodo.json`
- [ ] CHANGELOG entry under **Unreleased**, and docs updated

## Tested on hardware?

<!-- Board, sensor, what was checked, and the result. Or "Not tested on
hardware". Anything that still needs a bench test should carry a VERIFY
marker in the code or docs. -->
