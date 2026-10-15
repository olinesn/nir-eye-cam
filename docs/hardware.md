# Hardware: mouse-eye NIR camera

One camera unit = an Arducam HM01B0 module + a Seeed XIAO RP2040 (or a
Raspberry Pi Pico) + 850 nm LEDs + a visible-blocking filter + a longer lens.
Each unit is its own USB device. Several units share one sync wire.

## Parts (per camera)

| Part | Notes |
|---|---|
| Arducam HM01B0 QVGA camera module for Raspberry Pi Pico | 320×320 mono, 8-pin header, 3.3 V supply. |
| Seeed XIAO RP2040 (21×17.5 mm), or Raspberry Pi Pico | Firmware builds for either (`-DNEC_BOARD=xiao` / `pico`). |
| 850 nm IR LEDs, 1–4 | 3 mm / 5 mm or SMD. Wide beam (±20–30°) gives even light on the eye. |
| Logic-level N-MOSFET (e.g. AO3400, 2N7000) + 100 Ω gate + 100 kΩ pull-down | Lets the firmware switch and dim the LEDs (PWM, 125 kHz). |
| LED current resistor | Sets the *maximum* light: e.g. 2 LEDs in series (Vf ≈ 1.5 V each) from 5 V at 20 mA → (5 − 3) / 0.02 = 100 Ω. |
| 780–830 nm long-pass filter | In front of the lens. Blocks room light and visual stimuli so image brightness depends only on the IR LEDs. IR-pass acrylic or gel works for a first build. |
| Replacement lens, f ≈ 5–8 mm (M8 or M12) + 3D-printed holder | See Optics. The stock lens cannot frame a mouse eye. |
| Optional: 45° IR hot mirror | Reflects IR to the camera and passes visible light, so the camera sits outside the mouse's view. |
| Sync wire + common ground | One line to every camera's SYNC_IN (see Sync). |

## Wiring

Camera module → board (3.3 V logic on both sides):

| Arducam pin | XIAO RP2040 | Pico |
|---|---|---|
| VCC | 3V3 | 3V3 (pin 36) |
| GND | GND | GND |
| SCL | D5 (GP7) | GP5 |
| SDA | D4 (GP6) | GP4 |
| VSYNC | D0 (GP26) | GP6 |
| HREF | D1 (GP27) | GP7 |
| PCLK | D2 (GP28) | GP8 |
| DO | D3 (GP29) | GP9 |

Sync and LEDs:

| Signal | XIAO RP2040 | Pico |
|---|---|---|
| SYNC_IN (input, internal pull-down) | D6 (GP0) | GP14 |
| SYNC_OUT (master only) | D7 (GP1) | GP15 |
| LED (MOSFET gate) | D8 (GP2) | GP16 |

LED driver: `5V → resistor → LED(s) → MOSFET drain`, `source → GND`,
`gate ← LED pin through 100 Ω`, `100 kΩ gate → GND` (keeps the LEDs off
while the board boots). The IR stays off until the host sends `LED`, and it
switches off again if the host closes the port.

Keep the camera wires short (< 10 cm): PCLK runs at several MHz.

## Synchronization

**What you get:** every frame from every camera on one timeline, known to
within roughly 10–20 µs (interrupt latency). **What you do not get:**
simultaneous exposures. Each Arducam module runs on its own oscillator and
exposes no trigger or clock input, so the cameras free-run. At 30 fps, two
cameras' frames are offset by anything from 0 to 33 ms, and that offset
slowly drifts (crystals differ by tens of ppm).

How it works:

1. One camera is the **sync master** (`--sync-master NAME`). Its SYNC_OUT
   sends 10 ms pulses at random 0.4–1.0 s intervals.
2. Wire SYNC_OUT to **every** camera's SYNC_IN, the master's own included.
   Share ground (cameras on one computer or one powered hub already do).
3. Each camera timestamps every pulse and every frame start with its own
   microsecond clock.
4. After recording, `nireyecam-align` matches the pulse trains (the random
   intervals line up at only one offset) and fits each camera's clock
   offset + drift against the master. Every frame gets `t_s` in
   `frames_aligned.csv`.

To align with the rest of a rig, feed the rig's TTL into the same line instead
(`--external-sync`). A 5 V TTL needs a divider (10 kΩ over 20 kΩ) to reach
3.3 V. A random pulse pattern is best; a periodic one works only if every
camera is recording before the first pulse, because then the pulses are
paired by index.

Is that enough? For pupil diameter (changes over hundreds of ms) and for
aligning to stimuli or behavior, yes: compare cameras at a common `t_s` by
interpolating. Exposure-locked capture would need a sensor whose TRIG pin is
reachable, e.g. the bare SparkFun SEN-15570 module (TRIG is pin 7 on its
24-pin connector) on a 2.8 V adapter board.

## Optics for a mouse eye

The sensor is 1.15 × 1.15 mm (320 px × 3.6 µm). The 160×120 mode bins a
320×240 window, so it sees 1.15 × 0.86 mm of sensor at half the pixel count.

Field width ≈ 1.152 mm × (D − f) / f for focal length f at working distance D:

| Lens f | D = 25 mm | D = 30 mm | D = 40 mm |
|---|---|---|---|
| 0.66 mm (stock) | 43 mm | 51 mm | 69 mm |
| 4 mm | 6.0 mm | 7.5 mm | 10.4 mm |
| 6 mm | 3.7 mm | 4.6 mm | 6.5 mm |
| 8 mm | 2.5 mm | 3.2 mm | 4.5 mm |

A mouse eye is ~3.3 mm across; the pupil is roughly 0.2–2 mm. A **5–6 mm
field** shows the whole eye. At 160×120 that is ~30 px/mm, so a 1 mm pupil
spans ~30 px. Use 320×240 for ~60 px/mm at a lower frame rate (see USB budget).

The lens swap is the main mechanical job. Unscrew the stock M3.5 barrel and
hold a 5–8 mm lens over the sensor with a printed holder that sets the focus
distance. A lens made for a larger sensor has an image circle far bigger than
1.6 mm, so centering tolerance is loose. Close-up depth of field is under a
millimetre, so the holder needs fine focus (a threaded barrel).

## NIR: test before building

The datasheet gives sensitivity only at 530 nm and does not say whether the
lens cover glass blocks IR. Power one module, run
`nireyecam-record --cam test=PORT --preview --led 1000 --out /tmp/t`, and
point an 850 nm LED at it. If the image does not respond, the stock optics
block IR. You are replacing the lens anyway, so check the new lens has no IR
coating either.

## Illumination and safety

- Place the LEDs **off the camera axis** (more than about 10–15°). The pupil
  then looks dark (dark-pupil imaging) and is easy to segment. Aim the corneal
  glint away from the pupil edge.
- Mice see 850 nm very poorly, so the illumination should not drive the pupil.
  Measure that on your preparation rather than assuming it.
- Set the maximum current with the resistor, not in software. Keep corneal
  irradiance low: well under 1 mW/cm² is usually plenty at this distance. The
  human long-exposure limit is about 10 mW/cm², and no mouse-specific limit
  exists. Measure with a power meter if you can.
- With AE off (the default), the exposure is fixed, so pupil brightness does
  not track pupil size.

## USB budget

The RP2040 has full-speed USB (12 Mbit/s): about 1 MB/s of frames in practice.

| Mode | Bytes / frame | Max frame rate (approx.) |
|---|---|---|
| 160×120 (binned) | 19 KB | ~50 fps |
| 320×240 | 77 KB | ~13 fps |
| 320×320 | 102 KB | ~10 fps |

The sensor's own maximum also depends on the module's oscillator, which
Arducam does not specify. `nireyecam-record` prints the measured frame rate,
and `--fll` lowers it. Dropped frames appear as gaps in `seq`. A camera that
needs more needs high-speed USB: a Teensy 4.x, whose camera libraries
support this sensor.

## Tether and commutator (freely moving)

On a freely moving animal, the camera's cable has to pass a commutator. The
Neuropixels 2.0 rigs already pass a Doric commutator, so the question was
whether IMEC's Neuropixels cable, **CBL_1000**, could carry the ESP32-P4
build's USB ([vd55g1-esp32p4.md](vd55g1-esp32p4.md)). In short:

- **It cannot, at high or full speed.** CBL_1000 is one twisted pair: two
  wires. USB 2.0 needs four (VBUS, GND, D+, D−), and a USB-C-to-USB-C cable
  needs five plus a shield. New connectors do not add wires, so do not cut
  one for this.
- **The plan: a serializer head over one thin coax**
  ([vd55g1-fpdlink.md](vd55g1-fpdlink.md)), through two of the four contacts
  the joint has free. The eye video has to be aligned to the Neuropixels
  recording, and USB through the joint (option 1 below) leaves no contact for
  the sync line. Option 1 stays the quicker way to try a camera through the
  joint, without sync.

### CBL_1000 against USB 2.0

| | USB 2.0 / USB-C requirement | CBL_1000 |
|---|---|---|
| Wires | 4. USB-C to USB-C (USB 2.0): GND, VBUS, CC, D+, D−, plus a shield | 2, unshielded |
| Data pair | 90 Ω ±15% (76.5–103.5 Ω), shielded | ~110–130 Ω (estimate) |
| Length | ≤ 26 ns one way (~5 m); USB-C cables for USB 2.0 ~4 m | 5 m, plus Doric's adapter cables |
| Loss at 400 MHz | ≤ 5.8 dB | ~3–5 dB (estimate), so loss is not the problem |
| Drop on the power wires | ≤ 125 mV on VBUS plus 125 mV on GND | ~1.6 V at 250 mA, through the commutator (below) |

IMEC gives 5 m, 5 g, "2 × 0.41 mm" and "1 twisted pair", with a 4-pin
Omnetics plug at the headstage and USB-C at the PXIe card. Power and the
headstage serializer's data share the pair. IMEC publishes no wire gauge,
impedance or current rating. The estimates assume ~32 AWG copper, which 5 g
for 2 × 5 m of 0.41 mm wire matches (~2.7 Ω per wire); measure before relying
on them. The USB-C plug is only a connector and the PXIe ports are not USB
ports: keep a CBL_1000 out of computers, and USB devices out of the PXIe card.

### Power over a thin tether

Estimated draw of the ESP32-P4 build (nothing built yet, **VERIFY**):

| Part | Draw |
|---|---|
| ESP32-P4 | Up to 123 mA at 3.3 V for the chip alone (360 MHz, both cores, all peripheral clocks on). ~200 mA with PSRAM and the USB and MIPI PHYs. |
| VD55G1 | ≤ ~0.15 W. Check ST's datasheet (DS13604). |
| IR LEDs | E.g. 2 × 100 mA pulses at ~20% duty. |
| Total at 5 V | ~250 mA average, ~350 mA during strobes. |

Over CBL_1000's pair (~5.4 Ω loop, plus up to 2 × 0.5 Ω through the
commutator), that leaves the camera ~3.4 V, and ~2.8 V during strobes. USB
promises a device at least 4.40 V (4.75 V above 100 mA), and a 3.3 V regulator
needs ~3.5 V. Any light tether has this problem. Send power at a higher
voltage and step it down on the head (at 12 V the camera draws ~0.12 A and
loses ~0.8 V), or double the power wires on a joint with contacts to spare.

### The commutator

Doric's 24-contact assisted rotary joints, electrical only or with a
fiber-optic channel: under 0.5 Ω per contact, under 0.025 Ω of variation
while rotating, up to 40 rpm. They come with HDMI + 4-pin Molex, 2 × PZN-12,
2 × HARWIN, or SMA + micro-USB + PZN-12 connectors. Doric lists Neuropixels
1.0/2.0 and the UCLA Miniscope V4 as tested, through its own adapter cables.

- **No current or bandwidth rating** is published.
- **USB high speed through it is untested.** Its connectors and slip rings are
  not built for 480 Mbit/s edges (~0.5 ns), and brief contact dropouts can
  make the host drop a high-speed link. Bench-test it (**VERIFY**).
- **USB full speed** (12 Mbit/s, slow edges) will very likely pass.
- **Doric sells no general USB commutator.** Its manual shows a joint with a
  "USB2 camera signal" port (Rotary Bundle-imaging Fluorescence Mini Cube) and
  a USB-C joint for its 2-color microscope only, and does not say whether that
  USB crosses the slip ring. Ask Doric.
- **Our rigs have the hybrid HDMI + 4 version,** `AHRJ-OE_1x1_24_HDMI+4`
  ("Assisted 1x1 Fiber-optic & Electric Rotary Joint - 24 contacts, HDMI + 4
  auxiliary contact connectors"). Neuropixels goes through the HDMI port on
  Doric's `ADAPTER_NPX_HDMI` kit: USB-C at the PXIe card to an Omnetics PZN-04
  at the headstage, one cable on each side of the joint. The "+4 auxiliary
  contact" connectors are 4-pin Molex Micro-Fit 3.0 ports on the rotor and the
  stator, for "custom use" in Doric's manual: four contacts, the same as USB
  2.0's VBUS, GND, D+ and D−. Both are empty on our rig and have a pin in
  each cavity; ask Doric for the current rating per contact.

### Options

We chose option 2: the eye video has to be aligned to the Neuropixels
recording, and option 1 uses every free contact, leaving none for SYNC.

1. **The camera's own cable through the 4-pin Molex port, full speed first.**
   Two cables, each with a Molex plug: one from the rotor to the camera
   (VBUS, GND and a twisted D+/D− pair), one from the stator to the PC.
   - **Keep the whole path under ~5 m.** USB allows ~5 m of cable (26 ns one
     way, at full and high speed alike), counted from the PC to the camera
     and including the joint's own wiring. With ~1 m on each side of the
     joint, as on the Neuropixels adapter, it fits. If the PC is farther
     away, put a powered USB hub next to the commutator: it starts a new 5 m
     segment, and gives a firm 5 V right at the joint.
   - **Power at 5 V works with a reasonable tether** (estimates): at 250 mA
     the joint loses up to ~0.25 V, and 1 m of 28 AWG on each side ~0.12 V
     each. The camera sees ~4.5 V from a 5.0 V port (~4.3 V during strobes):
     outside USB's 125 + 125 mV budget, but enough for a 3.3 V regulator.
     With a longer or thinner tether, put a small board at the stator that
     sends ~12 V on the VBUS contact, switched on only while the PC's VBUS is
     present, and step it down on the head. The camera is then self-powered
     as far as USB is concerned, so it must sense that line before connecting
     to the bus (USB 2.0 §7.1.5.1; esp_tinyusb's `self_powered` and
     `vbus_monitor_io`).
   - **USB-C at the PC** needs a fifth wire (CC) that the joint does not
     have. End the stator cable in USB-A, or in a USB-C plug with Rd (5.1 kΩ)
     from CC to GND inside it, as in a USB-C-to-USB-B cable (USB-C
     specification Table 3-15).
   - **Frame rates.** Full speed carries ~1 MB/s of frames (USB budget,
     above): 320 × 320 at ~10 fps, 160 × 160 at ~40 fps, 100 × 100 at
     ~100 fps. The ESP32-P4's 32 MB of PSRAM can hold a burst: up to ~3.5 s of
     320 × 320 at 100 fps, then ~30 s to send it. Then test high speed through
     the joint: the Micro-Fit is a power connector, not built for 480 Mbit/s.
     If it holds, ~10 MB/s.
2. **A serializer over one thin coax,** as Neuropixels, Open Ephys ONIX
   headstages and the UCLA Miniscope V4 do. Only the sensor, a TI DS90UB953
   serializer and its supplies go on the animal; the ESP32-P4 sits at the base
   behind a DS90UB934 deserializer. The coax takes two of the four contacts and
   carries power, video, I²C and GPIO. SYNC needs no wire through the joint,
   because the ESP32-P4 timestamps it at the base. In its backward-compatible
   mode the 953 runs the coax at 700 Mbit/s (50 Mpixel/s), the rate ONIX
   uses, instead of its usual 4.16 Gbit/s. **VERIFY** that rate through the
   Micro-Fit contacts and slip rings first. Design sketch:
   [vd55g1-fpdlink.md](vd55g1-fpdlink.md).
3. **Wireless.** The ESP32-P4 has no radio, and a 100 mAh battery runs ~1 W
   for only ~20 minutes, on a mouse.

### Camera cable through the 4-pin port (draft)

Option 1 as two cables, one on each side of the joint. Nothing is built yet:
**VERIFY** all of it on the bench.

    PC ─USB-A─ fixed-side cable, ~1 m ─ Molex ▸ stator | joint | rotor ◂ Molex ─ tether, ~1 m ─ camera

Positions are as seen facing the joint's 4-pin port with the latch up: 1 top
left, 2 top right, 3 bottom left, 4 bottom right. Seen from the wire side of a
plug, latch up, they are the same, which is the side the terminals go in from.

| Position | Signal | USB-A pin | Wire | USB-C plug at the camera, if used |
|---|---|---|---|---|
| 1 | VBUS | 1 | red | A4, A9, B4, B9 |
| 2 | GND | 4 | black | A1, A12, B1, B12 |
| 3 | D+ | 3 | green | A6 |
| 4 | D− | 2 | white | A7 |

- **Both Molex ends are wired the same.** The joint connects each position
  straight through (measured, below), so power is the top row and data the
  bottom row on both sides, and the data pair stays together through the
  connector.
- **No CC wire.** The USB-A end needs none. On a USB-C plug at the camera,
  leave CC (A5) open: the board takes VBUS directly.
- **Fixed-side cable:** a generic USB 2.0 cable with a USB-A plug, its far end
  replaced by Micro-Fit terminals. Fold the braid back and insulate it at the
  Molex end; it stays connected to the USB-A shell. Check the colours with a
  meter first: cheap cables do not always follow them.
- **Tether:** four fine-stranded 30 AWG wires, the thinnest the small
  Micro-Fit terminals take. Twist D+ with D−, and VBUS with GND, and leave
  under 1 cm untwisted at each crimp. Estimated ~4.5 V at the camera at
  250 mA, ~4.25 V during strobes. Run it with the Neuropixels cable so the
  joint's torque sensor follows both (through the torque-sensor loop if the
  fiber is unused, Doric manual Table 2.1). On the head, solder it to pads on
  the carrier: a captive cable (USB-C specification §3.4.3) is lighter than
  any USB connector.
- **Parts.** The joint's ports have a pin in each cavity (checked on the
  rig), so both cables use the receptacle housing 43025-0400 with female
  43030 terminals: 43030-0010 tin or 43030-0012 gold for 26–30 AWG, per RS
  listings (**VERIFY** against Molex's drawings).

Bench checks, in order:

1. **Map the joint.** Done on our rig (2026-10-15): each position on the
   rotor reaches the same position on the stator. Still to check with the
   meter: no shorts between any two pins or to the joint's body, under 0.5 Ω
   per channel with the leads zeroed, and no dropouts while turning the rotor
   slowly by hand with the torque assist off (the data pair, 3 and 4, matters
   most).
2. **Each cable end to end,** against the positions above: right position at
   each end, no shorts.
3. **Power only:** the fixed-side cable into the PC, nothing on the rotor.
   Expect ~5 V between positions 1 and 2 and ~0 V on 3 and 4. Mate the Molex
   plugs first and the USB-A last: a USB plug's power pins touch before its
   data pins (USB 2.0 §7.2.4.1), and nothing guarantees that order at the
   Molex.
4. **With the camera:** it enumerates at full speed, and VBUS at the camera
   stays above ~4.2 V while streaming with the LEDs on. Then stream while
   turning the joint by hand, and look for gaps in `seq`.

**No wire is left for SYNC.** USB takes all four contacts, so the camera's
SYNC input has no path through the joint. Decide before designing the head
carrier:

- **A spare HDMI contact.** Neuropixels likely uses only a few of the 19. This
  needs Doric to say which ones its Neuropixels adapter uses, and a small
  pass-through adapter that taps a free one.
- **An optical code.** A dim IR LED on the rig blinks the random sync pattern
  where the camera can see it. No wire, but the sync would then come from
  image brightness, which `nireyecam-align` does not read yet.

### Sources

- IMEC [Neuropixels PXIe Control System datasheet](https://www.neuropixels.org/_files/ugd/328966_790b2b46eceb4e6db0ad41aaef2b3364.pdf)
  (CBL_1000) and [Neuropixels 1.0 user manual](https://www.manualslib.com/manual/2063930/Imec-Neuropixels-1-0.html)
  (cable, headstage serializer). Heys Lab,
  [Neuropixels cable repair](https://dx.doi.org/10.17504/protocols.io.q26g7q358lwz/v1),
  protocols.io.
- [USB 2.0 specification](https://www.usb.org/document-library/usb-20-specification):
  Table 6-1 and Figure 6-11 (pins, wire colours), §7.1.1.1 (90 Ω ±15%),
  §7.1.5.1 (pull-up only with VBUS), §7.1.16 (cable delay), Table 7-6 (cable
  loss), §7.2.2 (voltage drop), §7.2.4.1 (power pins touch first).
  [USB Type-C specification](https://www.usb.org/document-library/usb-type-cr-cable-and-connector-specification-release-25)
  R2.5: Table 3-1 (cable lengths), Table 3-11 (USB 2.0 cable wiring), §3.4.3
  (captive cables), Table 3-15 (USB-C to USB 2.0 Standard-B), §4.4.1 (IR
  drop).
- Doric Lenses [Assisted Electrical Rotary Joints, 24 contacts](https://neuro.doriclenses.com/collections/electrical-rotary-joints/products/assisted-electrical-rotary-joints-24-contacts)
  and [Assisted 1x1 Fiber-optic & Electric Rotary Joints, 24 contacts](https://neuro.doriclenses.com/products/assisted-fiber-optic-electric-rotary-joints-1)
  product pages, and the [user manual](https://www.doriclenses.com/downloads/UserManual/UserManual_ARJ_24_V1.0.0.pdf)
  v1.0.0 for both (Table 2.1: torque-sensor loop); [Neuropixels Adapter](https://neuro.doriclenses.com/products/adapter-hdmi-Neuropixels).
- Molex [Micro-Fit 3.0 43025 series](https://www.molex.com/en-us/products/series-chart/43025)
  (receptacle housings). RS listing for the
  [43030-0010](https://uk.rs-online.com/web/p/crimp-contacts/2333021) crimp
  terminal (26–30 AWG).
- Espressif [ESP32-P4 datasheet](https://documentation.espressif.com/esp32-p4-chip-revision-v1.3_datasheet_en.html),
  Table 5-7. TI [DS90UB953-Q1](https://www.ti.com/product/DS90UB953-Q1)
  product page. Open Ephys ONIX docs,
  [Coaxial Data Serialization & Power](https://open-ephys.github.io/onix-docs/Hardware%20Guide/Headstages/serialization.html).

## Depth sensing

Two projects built on these cameras could use depth: **pupil dilation** and
**facial expression detection**. In short:

- **Pupil dilation:** no depth sensor. A fixed magnification cancels out of
  relative dilation, and no depth sensor works on an eye at this distance
  (below). If you need the eye's 3D position, use a stereo pair of VD55G1
  cameras.
- **Facial expression:** start in 2D. For 3D, use a stereo pair of triggered
  VD55G1 cameras, plus a 940 nm dot projector if the coat has little texture.

### Depth sensors at 1–10 cm

| Type | Small parts | Fit here |
|---|---|---|
| Multi-zone direct ToF | ST VL53L8CX: 8×8 zones, 65° diagonal, 2–400 cm, 6.4 × 3.0 × 1.75 mm. ams TMF8829: up to 48×32 zones, 80° diagonal, 5.7 × 2.9 × 1.5 mm, pre-production. ST VL53L9CA: 54×42 zones, from below 5 cm. | I²C/SPI, so a XIAO RP2040 could drive one. But at 3 cm one VL53L8CX zone is ~3.4 mm wide, the whole eye, and a TMF8829 zone is ~1.4 mm at 5 cm. A distance reading, not a shape. Check the datasheets for accuracy at close range. |
| Indirect ToF imager | ST VD55H1: 672 × 804, 4.6 µm, sold only as a die on reconstructed wafers. Infineon IRS2976C: 640 × 480, 23 mm². | Needs a VCSEL, a driver and a MIPI host. Built for tens of centimetres to 5–10 m, with millimetre-level noise or worse. |
| Structured light (Face ID) | Not sold as a part. | Works at 25–50 cm. |
| Packaged stereo | Intel RealSense D405: minimum depth ~7 cm at 480p, 42 × 42 × 23 mm, USB. | No projector, and an IR-cut filter: it needs visible light, which the animal sees and which drives the pupil. Useful only as a bench test for facial expression. |
| Stereo from two of these cameras | Two VD55G1 units on the shared trigger, optionally with an ams OSRAM Belago dot projector. Belago 1.1: 4.2 × 3.6 × 3.3 mm, ~5,000 dots. Belago 1.2: 940 nm, ~15,000 dots. | Recommended. Precision below. |

Two things defeat every active sensor on an **eye**. The cornea is clear and
mirror-like, so ToF light and projected dots pass through it or glint off it
instead of returning from its surface. And the pupil is a hole that returns
almost nothing. On a **face**, fur scatters light inside the coat, which adds
error to ToF depth.

### Stereo depth precision

For two parallel cameras:

    depth step ≈ (working distance ÷ baseline)
                 × (pixel size on the subject) × (matching precision, px)

The pixel size on the subject is 1 / (px per mm), from the optics table in
[vd55g1-esp32p4.md](vd55g1-esp32p4.md). With ¼-pixel matching:

| Subject | Close-up lens F (≈ working distance) | px / mm | Baseline | Depth step |
|---|---|---|---|---|
| Mouse eye | 15 mm | 41 | 8 mm | ~0.01 mm |
| Mouse face | 30 mm | 20 | 12 mm | ~0.03 mm |
| Rat face | 50 mm | 12 | 20 mm | ~0.05 mm |

- **¼-pixel matching needs texture:** the pupil edge, whiskers, or patterned
  fur. A uniform coat under flood light matches worse, and the depth step
  grows in proportion (½ px doubles it). A dot projector adds texture.
- **The exposures must be simultaneous.** The HM01B0 cameras free-run, 0–33 ms
  apart at 30 fps (see Synchronization). That is fine for pupil diameter, but
  not for a moving eye or a whisking face. Stereo needs the VD55G1 build with
  external sync (`EXTSYNC`), which is not implemented yet.
- **Calibrate** with a millimetre-scale ChArUco board at the working distance.
  Label keypoints in SLEAP and triangulate them in LUCID or anipose. For a
  dense depth map, rectify the pair and run a stereo matcher (e.g. OpenCV's
  `StereoSGBM`).

### Project 1: pupil dilation

- **Relative dilation needs no depth.** With the head fixed, or the camera
  mounted on the head, the eye-to-camera distance is constant. So is the
  magnification, which then cancels out of dilation relative to baseline.
- **Use the pupil ellipse's major axis,** not its area or minor axis. When the
  eye rotates, the pupil foreshortens along one axis only, so the major axis
  stays close to the true diameter.
- **For millimetres,** image a scale at the pupil's position once. Movement
  along the camera axis changes the magnification by about (movement ÷ working
  distance): at 15–30 mm, 0.15–0.3 mm of movement is a 1% size error. You
  measure the *apparent* pupil, magnified by the cornea. That factor is
  constant, so it does not affect relative dilation.
- **If the eye moves along the axis enough to matter, or you need its 3D
  position,** use a stereo pair (above).
- **For gaze without a second camera,** fit a 3D eye model to the pupil
  ellipse over time (Świrski & Dodgson 2013), or track the pupil against the
  corneal reflections of two LEDs.

### Project 2: facial expression detection

- **What is scored.** The Mouse Grimace Scale scores orbital tightening, nose
  bulge, cheek bulge, ear position and whisker change (Langford et al. 2010).
  The Rat Grimace Scale scores orbital tightening, nose/cheek flattening, ear
  changes and whisker change (Sotocinal et al. 2011). The eyes, ears and
  whiskers show in 2D. The nose and cheek changes are changes of 3D shape,
  which is where depth adds the most.
- **Start in 2D.** Published work classifies mouse expressions from one
  side-view camera (Dolensek et al. 2020) and tracks face keypoints in 2D
  (Facemap, Syeda et al. 2024). One VD55G1 camera with a close-up lens of
  F ≈ 30 mm (mouse) or 50 mm (rat) frames the face. The module's own focus
  runs from 13.4 cm to infinity, so it needs the close-up lens here too.
- **For 3D, add a second triggered camera.** The pair gives both 3D keypoints
  and a dense depth map from the same hardware (precision above).
- **Add a projector if the coat is uniform.** Under flood light, a light coat
  has little texture to match. The Belago 1.2 emits at 940 nm, the band the
  CAM-5G1-080NIR's filter passes. **VERIFY** its pattern before buying: it is
  designed for robots at room scale, so check the dot spacing and focus at
  3–5 cm. Its eye-safety design assumes normal viewing distances, but here it
  would sit centimetres from the animal's eye, so measure the irradiance there.
- **To run both projects on one animal,** keep the projector out of the pupil
  video: the eye camera's filter (780–830 nm long-pass, or the
  CAM-5G1-080NIR's 940 nm bandpass) passes 940 nm. Drive the projector from
  the face cameras' STROBE and trigger the eye camera between face exposures,
  or use the face pair without the projector.
- **Not suitable:** ToF, because fur adds error and the noise is
  millimetre-level, and the RealSense D405 in a dark rig, because it sees
  visible light only. On the bench in room light, though, a D405 can show
  whether depth separates the expressions you care about before you build
  anything.

### Sources

- ST [VL53L8CX](https://www.st.com/en/imaging-and-photonics-solutions/vl53l8cx.html),
  [VL53L9CA](https://www.st.com/en/imaging-and-photonics-solutions/vl53l9ca.html)
  and [VD55H1](https://www.st.com/en/imaging-and-photonics-solutions/vd55h1.html)
  product pages.
- ams OSRAM [TMF8829](https://ams-osram.com/products/sensor-solutions/direct-time-of-flight-sensors-dtof/ams-tmf8829-48x32-multi-zone-time-of-flight-sensor)
  product page; [Belago 1.1](https://ams-osram.com/news/press-releases/luxonis-belago1.1)
  press release.
- Infineon [IRS2976C](https://www.infineon.com/cms/en/about-infineon/press/market-news/2023/INFPSS202302-067.html)
  press release.
- Intel [RealSense D405](https://www.intelrealsense.com/depth-camera-d405/)
  product page. RealSense support forum: the D405 has no projector, and an
  IR-cut filter on its sensors.
- Langford et al. 2010, *Nature Methods* (Mouse Grimace Scale).
  Sotocinal et al. 2011, *Molecular Pain* (Rat Grimace Scale).
  Dolensek et al. 2020, *Science* (facial expressions of emotion in mice).
  Syeda et al. 2024, *Nature Neuroscience* (Facemap).
  Świrski & Dodgson 2013, PETMEI (single-camera 3D eye model fitting).
