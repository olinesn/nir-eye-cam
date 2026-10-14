# Design sketch: VD55G1 global-shutter eye camera on an ESP32-P4

Status: **design only, nothing built**. Items marked **VERIFY** are open
questions that could change the design; the plan below tests them first.

Why this build: the Arducam HM01B0 boards free-run with no trigger or clock
input, so cameras can only be time-aligned (see hardware.md). The ST VD55G1 is
a global-shutter mono sensor whose module is about as small as a Himax one,
and it has GPIOs for sync and strobe. It outputs MIPI CSI-2, which an RP2040
cannot receive, so the host chip becomes an ESP32-P4: MIPI CSI-2 input plus
USB 2.0 high speed (480 Mbit/s).

For a freely moving animal tethered through a commutator, see
[vd55g1-fpdlink.md](vd55g1-fpdlink.md): the same sensor with only a serializer
on the head, and the ESP32-P4 at the base.

## Block diagram (one camera)

```
            eye ── close-up lens ── [CAM-5G1 promodule, 5 x 5 mm head]
                                          │ 30-pin Hirose BM28 FPC
                                          ▼
   ┌───────────────────────── carrier ─────────────────────────────┐
   │ 2.8 V / 1.8 V / 1.15 V LDOs   24 MHz osc ──► CLKIN           │
   │                                                               │
   │ MIPI CSI-2, 1 lane ───────────────► ESP32-P4  CSI             │
   │ I2C (SCL/SDA), XSHUTDOWN ◄────────── ESP32-P4  GPIO / I2C     │
   │ sensor GPIO0  ◄── EXTERNAL_SYNC ◄─┐ (1.8 V level shift)       │
   │ sensor GPIO1  ──► STROBE ──► LED MOSFET ──► 940/850 nm LEDs   │
   │ sensor GPIO2  ──► FSYNC_OUT ──► ESP32-P4 GPIO (timestamp IRQ) │
   │                                   │                           │
   │ SYNC connector ───────────────────┴──► ESP32-P4 GPIO (stamp)  │
   │ ESP32-P4 USB-HS ──► USB-C to the PC (nireyecam protocol)      │
   └───────────────────────────────────────────────────────────────┘
```

Several cameras share one SYNC wire. Each sensor starts its exposure on the
pulse (external-sync mode), and each ESP32-P4 also timestamps the pulses and
frame starts. The existing `nireyecam-align` then checks that no camera missed
a trigger, on top of providing the timeline.

## Parts

| Part | Notes |
|---|---|
| ST **CAM-5G1-080NIR** promodule | VD55G1, 804×704 mono global shutter, 2.16 µm. 80° DFOV, EFL 1.32 mm, F/2.0, **940 nm bandpass filter built in**. Head 5.0 × 5.0 × 2.55 mm; 11.65 × 8.0 mm including the connector. |
| or **CAM-5G1-081CLR** | Same optics with a clear filter. Use it for 850 nm illumination and add an 830 nm long-pass filter. |
| (bring-up only) **CAM-5G1-160CLR** | Same sensor and connector, 160° DFOV fisheye (EFL 0.825 mm, < −76% TV distortion). Fine for bring-up; too wide and too distorted for the eye. |
| ST **STEVAL-CAM-M0I** "P-Board" (prototype; order code STEVAL-CAM-M0I1) | 30-pin promodule → 22-pin FFC, MIPI 1–2 lanes + I2C, 30 × 35 × 11 mm, 10 cm cable. The 22-pin output uses the Raspberry Pi 5 pinout, so a Raspberry Pi 5 camera cable (22-pin → 15-pin) connects it to the ESP32-P4X board, whose connector carries the Raspberry Pi 15-pin signals. Its sensor reset comes from the connector's CAM_IO0, which must be high: see [`firmware-p4/README.md`](../firmware-p4/README.md). **VERIFY** lane polarity: ST warned that another of its boards (STEVAL-56G3MAI) has lane polarity that does not match the Raspberry Pi pinout and could damage boards. |
| ESP32-P4 board (prototype) | Espressif **ESP32-P4X-Function-EV-Board**: chip revision v3.1+ (what the firmware builds for), a Type-C port wired to USB 2.0 high speed as a device, a 15-pin 1.0 mm MIPI CSI connector, 40-pin header. Pin map in [`firmware-p4/README.md`](../firmware-p4/README.md). Not the older ESP32-P4-Function-EV-Board (v1.x silicon), and not the Waveshare ESP32-P4-NANO, whose high-speed port is a Type-A host-style socket. |
| Final: custom carrier | ESP32-P4 module + matching BM28 receptacle (likely the `BM28B0.6-30DS/2-0.35V` part; check) + 3 LDOs + 24 MHz oscillator + 1.8 V level shifters + LED driver + USB-C. Only the 5 mm module and lens need to sit at the eye; the carrier can be centimetres away on a flex. |
| Close-up lens | Small plano-convex, ~6 mm diameter, f = 10–20 mm, in a 3D-printed cap over the module. |
| IR LEDs | 940 nm with the NIR module, or 850 nm with the clear one. Driven from the sensor's STROBE output, so they are on only during exposure. |

Module supply rails: 2.8 V analog + 1.8 V I/O + 1.15 V core (or a 2.8 V +
1.2 V variant). External clock 6–27 MHz. Connector signals: VANA_2V8,
VDDIO_1V8, VCORE_1V15, CLKIN, CLKP/N, DATA1P/N, SCL, SDA, XSHUTDOWN,
GPIO0–3, plus grounds (ST data brief DB5267).

## Optics

The module is focused far away (depth of field 13.4 cm → infinity), and its
1.32 mm lens is far too short to frame an eye at any usable distance. A
close-up lens in front fixes both: with the module at infinity focus, the
object sits about one close-up focal length F away, and

    field width ≈ sensor width × F / EFL = 1.737 mm × F / 1.32 mm

| Close-up F | Working distance | Field (W × H) | px / mm | 1 mm pupil |
|---|---|---|---|---|
| 10 mm | ~10 mm | 13.2 × 11.5 mm | 61 | 61 px |
| 15 mm | ~15 mm | 19.7 × 17.3 mm | 41 | 41 px |
| 20 mm | ~20 mm | 26.3 × 23.0 mm | 31 | 31 px |

**F = 15 mm is a good start.** A 3.3 mm mouse eye is ~135 px across.
Read out only a **320 × 320 crop** (~7.8 mm) around the eye: the sensor crops
on chip (X/Y start and size registers), which saves USB bandwidth. Depth of
field is roughly 2 mm (estimate), plenty for a head-fixed eye. Focus by moving
the cap.

## Synchronization

From the VD55G1 Linux drivers (mainline `drivers/media/i2c/vd55g1.c` and
ST's own `vd55g1-linux-driver`) and ST's integration manual UM3224:

- Each GPIO can be **FSYNC_OUT** (0x00), **input** (0x01), **STROBE** (0x02)
  or **VT slave** (0x0A, GPIO0 only), set per context in `GPIO_0_CTRL`
  (0x051D + 0x50 × ctx).
- **Only GPIO0 can be the EXTERNAL_SYNC input** (UM3224), which calls this
  "VT slave" mode. ST's own Linux driver (not the mainline one) enables it in
  standby: GPIO0's mode = 0x0A, then `VT_CTRL` (0x0309) = 1. `EXTSYNC 1`
  does the same. `FRAME_LENGTH` still caps the frame rate, so the frame
  period must be shorter than the trigger period. **VERIFY**: which edge
  starts a frame, the delay from it to the start of exposure, and that
  START and STOP complete with no pulses arriving. This is the one thing
  everything else depends on. Test it first (phase 0).

Plan:

1. One pulse source (a master ESP32-P4, or the rig) drives the SYNC line at
   the frame rate.
2. Each sensor's GPIO0 = EXTERNAL_SYNC, so all exposures start on the same
   edge. With a global shutter, the whole frame shares that exposure window.
3. GPIO1 = STROBE drives the LEDs only during exposure: less IR on the eye,
   and motion is frozen by the pulse.
4. GPIO2 = FSYNC_OUT goes to an ESP32-P4 interrupt, which timestamps every
   frame (the VSYNC role in the RP2040 build).
5. The ESP32-P4 also timestamps the SYNC edges, so `nireyecam-align` works
   unchanged and flags any camera that missed a trigger.

Fallback if external sync does not work: free-run with FSYNC_OUT timestamps,
which is the current time-aligned scheme with a better sensor.

Sensor GPIOs run at 1.8 V. Put a level shifter (e.g. SN74LVC1T45) between
them and the ESP32-P4, and make the LED MOSFET's gate threshold safe at 1.8 V
(or buffer it).

## Firmware (ESP-IDF)

- **Sensor driver: to write.** ESP-IDF's `esp_cam_sensor` has no VD55G1
  driver. Registers worth knowing:
  - boot `0x0200`, start `0x0201`, stop `0x0202`, state `0x001C`;
  - external-clock frequency `0x0220`, MIPI data rate `0x0224` (250–1200 Mb/s);
  - line length `0x0300` (min 1128); frame length `0x050C` per context
    (1860 = 60 fps);
  - crop: X start/width `0x0514`/`0x0516`, Y start/height `0x0510`/`0x0512`;
  - manual exposure `0x0502`, analog gain `0x0501`.

  **Cut-2 silicon needs a firmware patch blob** written to `0x2000` before
  boot; cut 3 does not, in ST's STM32 camera middleware. **VERIFY**: the
  mainline Linux driver patches every VD55G1. Both cuts report model ID
  `0x53354731`; the cut is in `0x0004` (`0x2020` = cut 2, `0x3030` = cut 3).
  The Linux driver carries the blob but is GPL-2.0. ST's STM32 camera
  middleware carries it too, so check its license as a source instead.
- **Capture:** `esp_cam_ctlr_csi` with `data_lane_num = 1`,
  `input_data_color_type = CAM_CTLR_COLOR_RAW8`. **VERIFY** that the CSI path
  can hand RAW8 to memory unconverted: Espressif's example converts RAW8 →
  RGB565, and the ISP is meant for Bayer, which a mono sensor does not need.
  Frame buffers go in PSRAM (32 MB on P4NRW32 parts), which absorbs USB
  hiccups.
- **USB:** TinyUSB vendor-bulk (or CDC) on the HS port carrying the existing
  NEC1 packets. Needed changes:
  - firmware: device timestamps from `esp_timer_get_time()` in the GPIO
    interrupts;
  - host: raise the parser's frame-size limit in `protocol.py` (`_plausible`
    caps width/height at 320).

  The recorder and aligner are otherwise unchanged.

## Bandwidth

| Readout | Bytes / frame | At 100 fps | At 30 fps |
|---|---|---|---|
| 320 × 320 crop | 102 KB | 10 MB/s | 3 MB/s |
| 804 × 704 full | 566 KB | 57 MB/s | 17 MB/s |

USB 2.0 HS allows ~57 MB/s of bulk transfer in theory. Published ESP32-P4
TinyUSB measurements are far lower (~4–5 MB/s for MSC and CDC-NCM), but those
were not raw vendor bulk. **VERIFY** by measuring early. The 320 × 320 crop at
30–100 fps stays inside even pessimistic numbers.

## Plan, risk first

0. **Evaluate the sensor and its sync without building anything.** Use the
   CAM-5G1 promodule with ST's EVK Main (USB to a PC, ST software):
   - check the image at 940 nm through the close-up lens;
   - get UM3224's external-sync sequence working on two modules;
   - measure exposure simultaneity, e.g. film a fast-blinking LED, which both
     cameras must see in the same state.
1. **ESP32-P4 bring-up:** dev board + P-Board (pinout checked) to get I2C,
   boot, then RAW8 frames over CSI.
2. **Streaming:** USB-HS throughput measured, then NEC1 packets into
   `nireyecam-record`.
3. **Sync:** two units, common trigger to GPIO0, STROBE-driven LEDs, FSYNC_OUT
   timestamps, alignment report.
4. **Custom carrier:** ESP32-P4 module + BM28 receptacle + rails + level
   shifting + LED driver + USB-C; module on a short flex at the eye.

## Sources

- ST CAM-55G1 data brief DB5267 rev 2 (module optics, sizes, connector, rails)
- ST VD55G1 datasheet; UM3224 "How to integrate and configure the VD55G1 sensor"
- Linux `drivers/media/i2c/vd55g1.c` (register map, GPIO modes, boot/patch)
- ST `vd55g1-linux-driver` on GitHub (VT slave mode: GPIO0 mode 0x0A,
  `VT_CTRL` 0x0309)
- ST `stm32-mw-camera` on GitHub, `sensors/vd55g1` (model ID, silicon cut,
  which cut needs the patch)
- ST STEVAL-CAM-M0I (P-Board)
- ESP-IDF camera controller driver docs (ESP32-P4); esp_video UVC example
- Waveshare ESP32-P4-NANO docs
- Espressif ESP32-P4X-Function-EV-Board user guide (ports, header J1); ESP-IDF v6.1
  camera examples (`examples/peripherals/camera`, v3-board pin sets)
- ST `vd55g1-linux-driver` Raspberry Pi overlays (`dts/rpi`: 12 MHz clock,
  lane polarity, LED on sensor GPIO2)
- Espressif's camera adapter schematic for the ESP32-P4 Function EV Board
  (`esp32-p4-function-ev-board-camera-subboard-schematics.pdf`: the 15-pin
  connector's pinout, RESET pulled up on the adapter)
- ST community thread "STEVAL-CAM-M0I pinout compatibility with Raspberry Pi
  CM4" (Oct 2024: the 22-pin output uses the Pi 5 pinout; reset is driven by
  the connector's GPIO)
