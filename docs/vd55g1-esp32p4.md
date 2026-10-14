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

Sensor GPIOs run at 1.8 V and the ESP32-P4's at 3.3 V: the next section
gives the level shifting and the LED driver.

## IR LED driver and level shifting (bench prototype)

Breadboard parts for the ESP32-P4X-Function-EV-Board. Two jobs: shift the
sensor's 1.8 V GPIOs to and from the ESP32-P4's 3.3 V, and light the IR LEDs
only while the sensor's STROBE (on during exposure) **and** the firmware's
LED enable are both high, so the IR is off unless the host asked for it.

```
 P-Board (1.8 V)                 TXB0104 breakout          ESP32-P4X (header J1)
 sensor GPIO2  FSYNC_OUT ─────── A1      B1 ────────────── GPIO20 (J1-13)  FSYNC in
 sensor GPIO0  EXTSYNC   ─────── A2      B2 ────────────── GPIO23 (J1-7)   TRIG out
 sensor GPIO1  STROBE    ─────── A3      B3 ────────────── 74HCT08 pin 1A  (STROBE, 3.3 V)
 1.8 V I/O rail          ─────── VA      VB ────────────── 3V3   (J1-1)
 GND                     ─────── GND    GND ────────────── GND   (J1-6)

 74HCT08 (VCC from 5V, J1-2; GND; 0.1 µF across them)
   1A  ── B3 of the TXB0104 (STROBE)
   1B  ──┬── GPIO32 (J1-26), LED enable
         └── 100 kΩ ── GND
   1Y  ── 220 Ω ──┬── IRLB3813 gate
                  └── 100 kΩ ── GND
   unused inputs (2A 2B 3A 3B 4A 4B) ── GND

 IRLB3813:  source ── GND,  drain ── LED cathode end
 5V (J1-2) ── R ── LED ── LED ── IRLB3813 drain
```

| Part | Qty | Notes |
|---|---|---|
| TXB0104 level-shifter breakout (Adafruit 1875, or SparkFun BOB-11771) | 1 | A side 1.2–3.6 V, B side 1.65–5.5 V: 1.8 V on A, 3.3 V on B. Three of its four channels (FSYNC, TRIG, STROBE). Direction is automatic. |
| 74HCT08 quad 2-input AND gate, DIP-14 | 1 | On 5 V, so its output turns the MOSFET fully on; its TTL-level inputs (high above 2.0 V) read the 3.3 V signals. Tie the unused inputs to GND; 0.1 µF across its supply. |
| Infineon IRLB3813PBF N-channel MOSFET, TO-220 | 1 | Logic level: gate threshold 1.35–2.35 V, so the 74HCT08's 5 V turns it fully on; on-resistance at most 1.95 mΩ. Far more than two LEDs need. Same pin order as the IRLB8721 it replaces (gate, drain, source). |
| 220 Ω, and two 100 kΩ resistors | 1 + 2 | Gate resistor (see the rules); pull-downs on the gate and on LED enable, which keep the IR off while anything is unpowered or booting (GPIO32 floats until the firmware drives it). |
| Vishay TSAL6400, 940 nm IR LED (±25°, ~1.35 V) | 2, plus spares | For the CAM-5G1-080NIR (940 nm bandpass). With the clear module, an 850 nm LED and an 830 nm long-pass filter instead. |
| LED series resistor | 1 | R = (5 V − LEDs × 1.35 V) / current. Two LEDs: 120 Ω for 20 mA, 47 Ω for 50 mA. |
| 1.8 V for the shifter's A side | | From the P-Board's 1.8 V I/O rail, so the levels match the sensor's. **VERIFY** the P-Board brings it out; if not, a small 3.3 V → 1.8 V regulator. |

Rules:
- **Start at 20 mA, and measure before raising it.** Aim for well under
  1 mW/cm² at the eye ([hardware.md](hardware.md), Illumination and safety).
  STROBE lights the LEDs only during exposure, so the average irradiance is
  the exposure's share of the frame period.
- **The two pull-downs stay.** They are what keeps the IR off at power-up,
  before the firmware runs.
- **220 Ω on the gate.** This design first named the IRLB8721PBF, which is no
  longer made; the IRLB3813PBF is its recommended substitute. The gate
  resistor sets the 74HCT08's peak output current: about 35 mA through 100 Ω,
  over its ±25 mA rating, for as long as the gate takes to charge. That was a
  fraction of a microsecond for the IRLB8721 (7.6 nC); the IRLB3813 takes
  about 57 nC, so 220 Ω, which holds the peak near 20 mA. The LEDs still
  switch in a few microseconds, nothing next to a millisecond exposure.
  Another logic-level MOSFET works too: size its gate resistor the same way.
- **No resistors on the TXB0104's channels.** The TXB family is made for
  push-pull signals; strong pull-ups or pull-downs on its lines upset its
  automatic direction sensing.
- **Two cameras:** each camera has its own TXB0104, on its own P-Board's
  1.8 V. One board's TRIG out (GPIO23) goes to the B2 pin of both shifters,
  and a ground wire joins the boards. The other board's GPIO23 stays
  unconnected. The sync check's test LED hangs off the driving board's GPIO33
  through 330 Ω (README, "Check that two cameras expose together").
- **VERIFY when the P-Board arrives:** which sensor GPIOs and which 1.8 V
  point it brings out. ST's Raspberry Pi overlay drives the strobe from
  sensor GPIO2; if only GPIO2 is available, swap the STROBE and FSYNC_OUT
  roles in `firmware-p4/main/board.h`.

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
- Infineon IRLB3813PbF datasheet (gate threshold, on-resistance, gate charge);
  Digi-Key's IRLB8721PBF listing (obsolete, with the IRLB3813PBF as the
  manufacturer's recommended substitute); TI SN74HCT08 datasheet (output
  current)
