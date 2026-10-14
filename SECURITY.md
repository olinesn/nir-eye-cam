# Security policy

<!-- Maintainers: GitHub offers private vulnerability reporting only on public
repositories. When this repository is made public, enable it under
Settings > Advanced Security > Private vulnerability reporting, or the button
described below will not exist. -->

## Supported versions

Releases are listed in [CHANGELOG.md](CHANGELOG.md). Fixes go to `main` and
into the next release, so please check that a problem still occurs on the
latest `main` before reporting it.

## Reporting a vulnerability

Please do not report security problems in a public issue, pull request or
discussion.

Report them privately through GitHub instead: on the repository's
**Security** tab, choose **Report a vulnerability**. Only the maintainers see
the report, and they can work on a fix with you in a private security
advisory.

While this repository is private, that button is not available. If you are a
collaborator, contact a maintainer directly.

Please include:
- what is affected: which firmware and board, the host tools, or CI;
- the commit you tested;
- steps to reproduce;
- what an attacker could do with it.

The maintainers will acknowledge your report within one week, and keep you
updated while it is investigated and fixed. If you have not heard back after a
week, open a public issue saying only that you sent a security report, with
no details, so it does not go unnoticed.

## What to report privately

- **Camera firmware:** anything that lets data sent to a camera over USB crash
  it, hang it, corrupt its memory or run code, for example through the command
  parser or packet handling.
- **IR safety:** any way the IR LEDs can turn on or stay on when they should be
  off, or exceed the level the hardware is built for. Both firmwares boot with
  the IR off and switch it off when the computer disconnects, and they must
  keep doing so after any error. These LEDs shine into an animal's eye, so
  treat a failure here like a security problem and report it privately.
- **Host tools:** a crafted packet stream or session file that makes
  `nireyecam-record` or `nireyecam-align` write outside the session folder, run
  commands, or lose data that was already recorded.
- **Build and CI:** anything that would let someone change what the workflows
  build or publish, for example through the pinned actions, the ESP-IDF
  container image or the toolchain download.

## Out of scope, by design

- **The cameras have no authentication.** Any computer a camera is plugged
  into can configure it, stream from it and switch its IR on. Protect the
  recording computer and who can plug into it.
- **Anyone with physical access can reflash a camera** through its USB
  bootloader (the BOOT/BOOTSEL button).
- **Vulnerabilities in third-party components** (Pico SDK, ESP-IDF, TinyUSB,
  Python packages, ffmpeg) belong with those projects. Tell us as well if this
  project needs to update or work around one.
