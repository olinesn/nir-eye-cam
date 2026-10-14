# Getting help

nir-eye-cam is a research project maintained alongside lab work, so help is
on a best-effort basis: there are no guaranteed response times, except for
security reports (see [SECURITY.md](SECURITY.md)).

## Before you ask

Much of what people need is in the docs:

- [README.md](README.md): overview, building and flashing the firmware,
  recording.
- [docs/hardware.md](docs/hardware.md): parts, wiring, optics for a mouse eye,
  synchronization, IR safety, USB bandwidth.
- [docs/protocol.md](docs/protocol.md): the USB protocol and every command.
- [docs/vd55g1-esp32p4.md](docs/vd55g1-esp32p4.md) and
  [firmware-p4/README.md](firmware-p4/README.md): the global-shutter
  ESP32-P4 camera.

To tell a hardware problem from a software one, try the same thing with the
simulated camera (`nireyecam-record --cam test=sim ...`). It speaks the same
protocol as the firmware.

## Where to go

| You have... | Go to |
|---|---|
| A question about building, flashing or using the cameras | [Discussions > Q&A](https://github.com/olinesn/nir-eye-cam/discussions/categories/q-a) |
| Something that doesn't work as the docs describe | Issues > **Bug report** |
| An idea for a change | Issues > **Feature request** |
| A measurement from real hardware (anything marked `VERIFY`) | Issues > **Bench test result** |
| A security problem, or IR LEDs that stay on when they shouldn't | Report it privately: [SECURITY.md](SECURITY.md) |
| A code of conduct concern | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

New issues start from [the issue chooser](https://github.com/olinesn/nir-eye-cam/issues/new/choose).
Each form asks for what is needed to help; filling it in fully is the
fastest way to an answer.

To contribute a fix or feature yourself, see
[CONTRIBUTING.md](CONTRIBUTING.md).
