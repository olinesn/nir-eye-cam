# Bench test plan: VD55G1 vs Mira050 for the dual-colour miniscope

Status: **plan only, nothing measured**. It expands step 1 of the plan in
[miniscope.md](miniscope.md#plan-risk-first). Items marked **VERIFY** must be
settled before or during the test.

**The question.** Under the same optics, which sensor gives more
signal-to-noise per cell in the red channel, and in green, at a given
excitation power? And can it alternate two LEDs reliably, frame by frame?

The test runs in two halves. **Lensless, under calibrated light,** isolates
the sensors themselves. **Behind identical optics, on fluorescent samples,**
is the comparison that decides.

## Before you start

- **Boards with a removable lens.**
  - VD55G1: ST's STEVAL-55G1MBI has an M12 lens holder. **VERIFY** there is no
    filter in the holder; if there is, remove it.
  - Mira050: an ams evaluation kit or an M12 module (**VERIFY**
    availability), or else a carrier board for the chip-scale package.
- **Hosts.** Use vendor evaluation software, or a Raspberry Pi 5 (two camera
  ports). The VD55G1 has a mainline Linux driver
  (`drivers/media/i2c/vd55g1.c`); ams's Mira050 driver needs checking
  (**VERIFY**). Don't wait for the ESP32-P4 firmware: it has not run on
  hardware yet, and the sensor physics does not depend on it. The P4 build of
  whichever sensor wins is checked afterwards with `nireyecam-bench` (Part 4).
- **Raw data only.**
  - Linear RAW10 or RAW12, auto-exposure off, black level fixed.
  - **Turn off all on-chip processing:** the VD55G1's adaptive noise
    reduction (up to 9 dB) and defect correction, the Mira050's defect
    correction, and any PWL compression. Left on, noise reduction makes the
    noise numbers look better than the sensor is.
- **Same conditions for both:** a black enclosure, the same room temperature,
  15 minutes' warm-up, and each chip's own temperature sensor logged with
  every run.

## Questions for the vendors

These go to ST (VD55G1) and ams OSRAM (Mira050). An answer can settle an item
before the bench does. The last column says what happens without one: most
are measured here anyway, and only the hardware questions block the setup.

**Sensor physics.** The bench measures all of these; an answer is a
cross-check.

| Open item | Ask | Step | Without an answer |
|---|---|---|---|
| QE, tabulated 450–700 nm. The VD55G1's is Fig. 21 of DS13604; the Mira050's is not published | Both | 2 | Measured in each emission band |
| Conversion gain and temporal noise at each gain; dark current at 25 and 40 °C; PRNU and DSNU | Both | 1, 3, 4 | Measured |
| Binning before or after readout, and a binned pixel's read noise | Both | 1 | Measured: digital 2×2 binning doubles the read noise per binned pixel |
| Leakage into the stored frame at 550 nm (the VD55G1's figure is at 940 nm only) | ST | 11 | Measured in the worst-case test |
| The microlenses' chief-ray profile, and the corner response with near-straight light | Both | 5 | Measured |
| Cover-glass transmission, 450–700 nm | ams | 2 | Included in the measured QE, since the glass stays on |

**Hardware and supply.** These block the setup.

| Open item | Ask | Step | Without an answer |
|---|---|---|---|
| A lensless VD55G1: the packaged (ChipPack PLCC) part's drawing, cover glass and MOQ, or whether the CAM-5G1-081CLR's lens comes off | ST | Before you start | Use the STEVAL-55G1MBI's M12 holder |
| Which silicon cut ships now. Cut 2 needs ST's patch, which the ESP32-P4 firmware does not include | ST | 15 | Read `revision` from `INFO` on arrival |
| A Mira050 evaluation kit that exposes `TRIG_ILLUM` and the context registers; reference register settings or a driver, and its licence | ams | Before you start, Part 3 | A carrier board for the chip-scale package, configured from the datasheet |
| Mira050 assembly guidance (reflow profile, underfill); samples below 120; lifecycle and lead time | ams | Before you start | Buy single units from a distributor |

**Alternation.** These decide how Part 3 is run.

| Open item | Ask | Step | Without an answer |
|---|---|---|---|
| Does the VD55G1 step through two contexts on its own, with a different STROBE GPIO in each, also in VT-slave (external sync) mode? | ST | 12 | Tested there |
| Can the Mira050 toggle its contexts every frame without a host write? If not, how late in a frame can the write arrive? | ams | 12 | Find the deadline by moving the write later |
| Is the active context reported in the frame data? | Both | 10 | Label by the frame counter's parity |
| A second Mira050 illumination trigger, or a pin that shows the active context | ams | 9 | Route `TRIG_ILLUM` with a host GPIO |
| External frame start: which edge, the delay and jitter to exposure, and START/STOP with no pulses (the VD55G1's open `EXTSYNC` items) | Both | 15 | Measured by `nireyecam-bench --extsync` |

## Equipment

| Item | Suggested |
|---|---|
| LEDs | Mounted LEDs at 470, 530, 565 and 625 nm (e.g. Thorlabs M470L5, M530L4, M565L3, M625L4) on a constant-current driver (Thorlabs DC2200 or LEDD1B) |
| Filters | The real set: exciters ET470/40x and ET560/25x, emitters ET525/50m and ET630/75m (or whichever set is chosen), a dichroic cube |
| Light calibration | A calibrated low-light power meter (Thorlabs PM100D with an S130C or S120C), an opal diffuser, an aperture |
| Imaging optics (Part 2) | An infinity-corrected objective and an achromat tube lens giving about 1.6×, the same for both sensors |
| Samples | Chroma green and orange fluorescent plastic slides, which barely bleach; 10–15 µm green and red fluorescent beads as cell-sized targets |
| Electronics | A 4-channel oscilloscope, a fast photodiode, a current monitor on each sensor rail (e.g. INA226, or a bench supply), a thermocouple |

## Part 1: lensless sensor characterization (EMVA 1288-style)

Light path: LED → emission filter → opal diffuser → aperture, at f/8 or
slower, so the light arrives nearly straight on. Run every step twice, through
**ET525/50m** and through **ET630/75m**: QE averaged over the real emission
bands is the number that matters. Measure the irradiance at the sensor plane
with the power meter.

1. **Photon transfer,** at gain 1× and at the planned working gain: 50
   exposure steps from dark to saturation, two frames per step, and a dark
   capture at every exposure. From mean against variance: conversion gain K
   (e⁻/DN), read noise (e⁻), saturation capacity (e⁻), and linearity error.
   Repeat it in 2×2 binning. Digital binning sums four pixels' read noise
   (twice one pixel's); binning before readout adds less. The binned read
   noise says which each sensor does.
2. **QE in each band** = mean signal (e⁻) ÷ photons per pixel, where photons
   per pixel = irradiance × pixel area × exposure ÷ (hc/λ).
3. **Dark current:** exposures from 1 to 1,000 ms, at about 25 °C and at the
   board's own running temperature.
4. **Fixed-pattern noise:** DSNU from 100 averaged dark frames; PRNU from 100
   averaged frames at 50% of saturation.
5. **Corner fall-off:** the corner-to-centre response ratio under the same
   f/8 light. This settles the microlens-angle question for the near-straight
   light a miniscope's tube lens delivers. Optional: repeat with a wide cone
   (diffuser close up, about f/2).

## Part 2: fluorescence through identical optics (the deciding test)

The objective, tube lens, filter cube and sample stay the same; only the
sensor changes. At 1.6× a VD55G1 pixel covers 1.35 µm of the sample and a
Mira050 pixel 1.74 µm, so **compare per cell, not per pixel:** sum each
12 µm bead or disk region.

6. **Signal-to-noise against excitation power.**
   - Powers: 0.1, 0.25, 0.5, 1 and 2 mW/mm², measured at the sample plane.
   - Channels: green (470 nm in, 525 nm out) and red (565 nm in, 630 nm out).
   - Exposures for 30 fps per colour.
   - Per region: mean ÷ temporal standard deviation over 500 frames.
   - **The headline number:** the power each sensor needs to reach a target
     SNR (e.g. 100 per cell per frame) in red.
7. **Small-signal detectability.** Step the LED current ±2% in a slow square
   wave (a stand-in for ΔF/F), and measure how well each sensor separates the
   two levels per region per frame.
8. **Stability.** 10 minutes continuous at 1 mW/mm². Report frame-to-frame
   noise, drift, and the LED warm-up over the first minute, with both sensors
   on the same LED and driver.

## Part 3: alternating blue and lime frames

- **VD55G1:** the built-in two-context sequence, with GPIO1 strobing blue and
  GPIO3 strobing lime ([miniscope.md](miniscope.md#alternating-on-the-vd55g1)).
- **Mira050:** the host switches context over I²C every frame, and a host GPIO
  routes the single `TRIG_ILLUM` pin to the right LED driver.

9. **Timing.** Scope the strobe or trigger, the frame start and the LED
   current, with the photodiode at the sample. Confirm each LED pulse falls
   inside its own exposure, and measure the jitter.
10. **Labelling.** Image a two-colour target (green slide on one half, red on
    the other) for 100,000 frames at 60 fps total. Count frames whose content
    does not match their label, and any dropped frames. If a sensor reports
    its active context in the frame data, label by that and check it against
    the parity too.
11. **Leakage.** On a red-only target, how much signal appears in the blue
    frames, against the red frames? Optional worst case: leave one LED on
    through the other frame's readout, to check the claimed leakage into the
    stored frame (−80 dB at 940 nm for the VD55G1, −120 dB at 550 nm for the
    Mira050).
12. **Settings per colour.** Check that each context's exposure and gain
    actually apply, and find the fastest stable alternation rate. For the
    Mira050, also find how late in a frame the host's context write can
    arrive: move it later until frames start being mislabelled.

## Part 4: power, heat, and the ESP32-P4 build

13. **Power:** current on each sensor rail at 30 and 60 fps, full resolution
    and 2×2 binned, 8 and 10 bits. Subtract the board's baseline.
14. **Heat:** the sensor's temperature rise after 30 minutes, from its own
    register and a thermocouple. Repeat the dark current at that temperature.
15. **The winner on the ESP32-P4.** Once its firmware runs, `nireyecam-bench`
    measures what the plan otherwise does by hand: frame rate, jitter and
    dropped frames (including the fastest rate the USB link sustains),
    exposure linearity and black level, the analog-gain steps, and `EXTSYNC`.
    File its report as a [Bench test result](../.github/ISSUE_TEMPLATE/bench_test.yml)
    issue. `nireyecam-bench` speaks only this repo's firmware, so it cannot
    test a Mira050 on a vendor board, and it does not do photon transfer, QE
    or per-cell SNR: `nireyecam-sensortest` (below) analyses Parts 1–3.

## Analysis: `nireyecam-sensortest`

Three subcommands read the recorded frames and fill most of the results
table. Frames can be `.npy` stacks (frames × height × width, any integer
type), raw `.u8` or `.u16` files (with `--size WxH`, or a `<file>.json`
sidecar giving `width` and `height`), or a `nireyecam-record` camera folder.
Each command writes a Markdown report (`--out`) and, with `--json`, every
number. Results in electrons do not depend on how the DN are scaled, since
the conversion gain is measured in the same DN.

### Part 1: `ptc`

```bash
nireyecam-sensortest ptc ptc.csv --crop 0.5 --json ptc.json
```

One manifest row per capture, each of at least 2 frames:

| Column | Meaning |
|---|---|
| `sensor` | The sensor's name; the report has one column per sensor |
| `gain` | A label for the analog gain, e.g. `1x` |
| `band` | The emission filter, e.g. `525/50`; leave it empty on dark rows |
| `kind` | `light` or `dark` |
| `exposure_ms` | Exposure time |
| `irradiance_w_m2` | At the sensor plane; light rows only. Without it there is no QE |
| `wavelength_nm` | The band's centre, for the photon energy |
| `pixel_um` | Pixel pitch |
| `file` | The frames, relative to the manifest |

- Sweep the exposure from dark to past saturation in about 50 even steps.
- Take a dark capture at every light exposure, as EMVA 1288 does. Each light
  capture uses the dark of the same sensor and gain at the nearest exposure.
- Make one light capture near half of saturation with 16 or more frames,
  and its dark likewise: PRNU, DSNU and the corner-to-centre response come
  from that pair.
- `--crop` limits the statistics to the centre of the frame. The
  corner-to-centre response always uses the whole frame.
- **Binned captures** (step 1) are rows of their own: give them their own
  `gain` label, e.g. `1x 2x2`, and the binned pixel pitch in `pixel_um`
  (4.32 µm for the VD55G1, 5.58 µm for the Mira050), so they are fitted
  separately and their QE uses the right pixel area.

### Part 2: `snr`

```bash
nireyecam-sensortest snr snr.csv --cell-um 12 --field-um 500 --target-snr 100
```

One manifest row per run:

| Column | Meaning |
|---|---|
| `sensor`, `channel` | E.g. `VD55G1`, `red` |
| `power_mw_mm2` | Excitation at the sample plane |
| `sample_px_um` | One pixel's size in the sample: pixel pitch ÷ magnification |
| `file`, `dark` | The frames, and a dark capture at the same exposure. Without the dark, the black level counts as signal |
| `k_dn_per_e` | Optional: the conversion gain from `ptc`, for results in electrons |
| `fps` | Optional: drift per minute, and the LED's change over the first minute |
| `period_frames` | Optional: marks a small-signal run (step 7), with the LED stepped every N frames |
| `regions` | Optional: a CSV of `x,y` centres in pixels (beads, say). Otherwise 12 µm disks tile the central `--field-um` of the sample, the same piece of sample for both sensors |

SNR per region is its mean over its frame-to-frame noise (differences of
consecutive frames ÷ √2, so slow drift does not count as noise). The report
gives medians over the regions, and for each sensor and channel the power
that reaches `--target-snr`, interpolated (log-log) between runs.

### Part 3: `alternation`

```bash
nireyecam-sensortest alternation runs/two-colour/cam --dark dark.npy \
    --green-roi 0,0,300,704 --red-roi 500,0,804,704
nireyecam-sensortest alternation runs/red-only/cam --dark dark.npy --leak-roi 200,200,600,500
```

- Labels come from the parity of the sensor frame counter in `frames.csv`,
  so a dropped frame does not shift them. Other inputs fall back to the
  frame index, which assumes no dropped frames.
- **Two-colour target** (step 10): finds which parity is the blue frames and
  counts the frames whose content disagrees with their label.
- **One-colour target** (step 11): the dim frames' signal as a percentage of
  the bright frames'. This includes the other LED genuinely exciting the
  dye; image the target under each LED alone to separate that from timing
  leakage.

## Results

One column per sensor:

| | VD55G1 | Mira050 |
|---|---|---|
| Conversion gain (e⁻/DN), at 1× and working gain | | |
| Read noise (e⁻) | | |
| Read noise, 2×2 binned (e⁻) | | |
| Saturation capacity (e⁻) | | |
| QE through ET525/50m | | |
| QE through ET630/75m | | |
| Dark current at running temperature (e⁻/s) | | |
| DSNU / PRNU | | |
| Corner-to-centre response | | |
| SNR per cell, green, at 0.5 and 1 mW/mm² | | |
| SNR per cell, red, at 0.5 and 1 mW/mm² | | |
| Power for the target SNR in red (mW/mm²) | | |
| Mislabelled or dropped frames per 100,000 | | |
| Leakage into the wrong frame (%) | | |
| Fastest stable alternation rate (fps) | | |
| Sensor power at 30 and 60 fps (mW) | | |
| Temperature rise after 30 minutes (°C) | | |

## How to decide

1. **Red per-cell SNR at the planned power (step 6) decides first.** A clear
   win is roughly 25% less light or more for the same SNR; under about 10% is
   a tie.
2. **On a tie, prefer the VD55G1.** Its hardware alternation removes a timing
   risk, and its firmware already exists.
3. **Choose the Mira050** if it wins on red *and* labels every frame
   correctly through step 10 with host-driven switching. It is also the
   better pick if a solderable package matters more than hardware
   alternation.
4. **If neither reaches the target SNR in red** at a power jRCaMP1b
   tolerates, repeat Part 2 with a STARVIS 2 mono module (e.g. IMX662) as the
   low-noise reference before redesigning.

## Sources

- EMVA 1288, the standard for characterizing image sensors and cameras
  (photon transfer, QE, dark current, DSNU/PRNU):
  [emva.org](https://www.emva.org/standards-technology/emva-1288/).
- Sensor figures and the reasons for each test: [miniscope.md](miniscope.md)
  and its sources.
