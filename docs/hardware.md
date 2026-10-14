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
