# Design sketch: a dual-colour miniscope from the VD55G1 build

Status: **design only, nothing built**. Items marked **VERIFY** need a bench
test or a vendor answer before parts are committed. Sensor figures are the
maker's own unless marked *measured* (an EMVA 1288 report or a camera maker's
test).

The goal is a replacement for the Inscopix nVue: a head-mounted one-photon
miniscope that images a green (GCaMP) and a red calcium indicator in single
cells, through an implanted GRIN lens, in freely moving rats or mice. The
VD55G1 + ESP32-P4 build ([vd55g1-esp32p4.md](vd55g1-esp32p4.md)) already does
the electronics side: USB streaming, per-frame timestamps, external sync, an
LED strobe, and lossless recording. The optics, filters, excitation light and
focus are new.

In short:

- **Alternate the two colours on one sensor,** as the nVue does: blue and lime
  LEDs take turns frame by frame, so each colour gets half the frame rate. The
  VD55G1 can sequence this itself, with one LED output per colour.
- **Sensor:** the VD55G1 is workable. Bench the ams Mira050 beside it: on
  paper it is the same class, packaged, and stronger in red. A Sony STARVIS 2
  mono sensor is the low-light reference.
- **Excitation:** one LED per colour, each with a 3 mm half-ball lens and its
  own exciter, merged by a small dichroic. Keep the beam within about ±10° at
  the filters, but do not focus the LED die onto the tissue.
- **Indicators:** GCaMP with jRCaMP1b (or PinkyCaMP), not jRGECO1a, which
  photoswitches under blue light.

## What the nVue does

| | nVue |
|---|---|
| Colours | One sensor; a blue and a lime LED alternate every frame, so each colour gets half the frame rate. Two independent papers describe it this way; Inscopix does not state the sensor count. |
| Bands | Green: 455–480 nm excitation, 499–530 nm emission. Red: 545–580 nm excitation, 600–660 nm emission. |
| Light | 0–2 mW/mm² per colour, in 0.1 mW/mm² steps, at the objective. |
| Format | 1280 × 800 px over 1050 × 650 µm, NA 0.5. |
| Frame rate | Up to 30 fps in dual-colour mode. |
| Focus | Electronic, 300 µm range. Inscopix uses it to correct the GRIN lens's focus difference between green and red. |
| Weight | 2.4 g. |

## Two ways to get two colours

| | A. One sensor, alternating LEDs | B. Two sensors, emission split by a dichroic |
|---|---|---|
| Used by | nVue; Barbera et al. 2024 | Dong et al. 2025; Zhang et al. 2026; Doric |
| Frame rate per colour | Half | Full, and simultaneous |
| Green/red focus difference | An electrowetting lens switched every frame (Barbera does this), or a compromise focal plane | Each sensor slides to its own image plane |
| Hardware here | One sensor + one ESP32-P4 | Two of each: the P4 has one CSI-2 input, so two tethers |

**Start with A.** It is the trade-off the nVue makes, and it needs nothing
beyond one camera unit. B is the upgrade if the two colours must be
simultaneous. `nireyecam-align` already puts two cameras on one timeline, and
`EXTSYNC` is meant to start their exposures together (not yet run on
hardware).

### Alternating on the VD55G1

ST lists "programmable sequences of 4-frame contexts, including illumination
control", and each context sets its own exposure, gain and GPIO modes
(`GPIO_0_CTRL` is at `0x051D + 0x50 × context`). **VERIFY** that the sequence
steps through the contexts on its own, one per frame. Assign the GPIOs as:

| GPIO | Role |
|---|---|
| GPIO0 | EXTERNAL_SYNC, as now (`EXTSYNC`) |
| GPIO1 | STROBE in context 0: the **blue** LED driver |
| GPIO2 | FSYNC_OUT, as now (frame timestamps) |
| GPIO3 | STROBE in context 1: the **lime** LED driver |

- Give the red context a longer exposure or more gain: red indicators are the
  dim channel.
- **VERIFY** that the host can tell which colour each frame is. The parity of
  the sensor frame counter in `frames.csv` works if no frame is dropped. The
  host then splits the video into two channels.
- Binned 2×2 at 60 fps (30 per colour, the nVue's rate) is 8.5 MB/s in 8 bits;
  full resolution at 60 fps is 34 MB/s. Both are under USB 2.0's ~57 MB/s but
  above the ~4–5 MB/s published for the P4's TinyUSB classes, so measure the
  P4's throughput first (a **VERIFY** in
  [vd55g1-esp32p4.md](vd55g1-esp32p4.md#bandwidth)). Binned at 30 fps
  (15 per colour) is 4.2 MB/s.
- The sensor outputs RAW10, but the packet format and the file writer are
  8-bit only. A 10-bit path is optional, and touches the protocol in all four
  places (see CONTRIBUTING.md).

### Green and red focus at different depths

A GRIN lens focuses red deeper than green: about 120 µm through a 1 mm lens
(Barbera et al. 2024, whose table prints the unit as mm; it can only be µm).
Under A, either switch an electrowetting lens with the LEDs every frame, or
accept one compromise focal plane. Under B, the red sensor sits behind the
green one. That offset scales with the magnification squared: about 0.28 mm
behind a VD55G1 at 1.5×, against 1.0 mm behind a PYTHON480 at 2.9×. The
positioning tolerance shrinks by the same factor.

## Optical path

```
Excitation: LED + half-ball + exciter, one per colour
            → T510lpxr merges them → multi-band main dichroic (reflects)
            → objective → focus lens → GRIN (implanted) → tissue
Emission:   tissue → GRIN → focus lens → objective → main dichroic (passes)
            → dual-band emitter → tube lens → sensor (no lens, no filter)
```

**Magnification.** The VD55G1's array is 1.74 × 1.52 mm (2.16 µm pixels).

| Field | Magnification | Pixel at the tissue | 2×2 binned |
|---|---|---|---|
| 1 mm round, across the short side | 1.52× | 1.42 µm | 2.8 µm |
| nVue's 1.05 mm width | 1.65× | 1.31 µm | 2.6 µm |

Unbinned, both oversample what a GRIN lens resolves.

**The sensor must come without its own lens and filter.** The
CAM-5G1-080NIR has a 940 nm bandpass built in. Use the clear module
(CAM-5G1-081CLR) with its lens removed (**VERIFY** that it comes off), a bare
die on a chip-on-board carrier, or the PLCC-packaged VD55G1 that ChipPack
lists (**VERIFY** its dimensions and cover glass).

## Excitation light

"Collimated" has to mean two things at once.

**1. Near-parallel at the filters.** An interference filter's edge moves to
shorter wavelengths as the beam tilts:

    edge shift ≈ λ × (1 − √(1 − (sin θ / n_eff)²)),   n_eff ≈ 1.8–2.0

| Beam angle | Shift at 470 nm | Shift at 565 nm |
|---|---|---|
| ±5° | 0.4 nm | 0.7 nm |
| ±10° | 1.8 nm | 2.6 nm |
| ±15° | 4.0 nm | 5.9 nm |
| ±30° | 15 nm | 22 nm |

A bare LED spreads to about ±60°. At ±30° the excitation band slides far
enough to leak into the emission band. Keep it within about ±10°.

**2. Not focused on the tissue.** If the LED die sits exactly at the
condenser's focal point, the objective images the die onto the tissue. The V4
build guide warns of exactly this: "a very bright small square in your image".
V4 and NINscope avoid it with a 3 mm N-BK7 half-ball lens (Edmund #47-269)
sitting directly on the LED, which defocuses the die's image. Ghosh et al.
2011 used a drum lens and describe it as approximating Köhler illumination.
The cleaner version images the die into the objective's back aperture.

**What is left of the spread** is about atan(die width / 2f). A half-ball's
focal length is R / (n − 1), about 2.9 mm for the 3 mm lens.

| Die width | f = 1.9 mm (2 mm half-ball) | f = 2.9 mm (3 mm) | f = 3.9 mm (4 mm) |
|---|---|---|---|
| 0.5 mm | ±7.4° | ±4.9° | ±3.6° |
| 1.0 mm | ±14.5° | ±9.8° | ±7.1° |

The same spread sets how wide a patch of tissue is lit, so it trades filter
performance against field size.

### Adding the second colour

| Option | How | Strengths | Costs |
|---|---|---|---|
| **Two LEDs on the head** (Zhang et al. 2026) | Blue LXML-PB01 and lime LXML-PX02 (Luxeon Z: LXZ1-PB01, LXZ1-PX01), each with its own half-ball and exciter (ET470/40x, ET560/25x), merged by a T510lpxr dichroic. | Proven; no fibre. | Both beams must be aligned to one angle, or each colour lights a slightly different patch. LED heat on the head. |
| **One fibre from off the head** (as Doric does) | Thorlabs fibre-coupled LEDs: M470F4 about 20 mW and M565F3 about 13.5 mW into a Ø400 µm fibre, merged on the bench; a small aspheric lens on the head. | Both colours leave the same spot, so they light the same field. The fibre face is evenly lit, so imaging it onto the tissue is fine: lit diameter ≈ core × f(objective) / f(collimator). No LED or driver on the head; switching happens off the head, triggered by the STROBE lines. | An electrical-plus-optical commutator, a stiffer tether, and throughput that changes as the fibre bends. Suits a rat better than a mouse. |
| Ruled out | Multi-die LED packages: the gap between dies becomes an angle (about 27° for 1.5 mm at f = 2.9 mm), so the colours light different fields. Laser diodes: there is no 561 nm diode. | | |

**Lime LEDs are phosphor-converted** and about 100 nm wide, so the lime exciter
has to cut hard: ET560/25x (Zhang et al.) or 563/9 nm (Doric).

**Power at the tissue:** the nVue's range, 0–2 mW/mm², is a guide. A fibre LED
delivering 13.5 mW leaves plenty of margin for a ~1 mm² field.

### LED drivers

One constant-current channel per colour, with the current set as an analog
level and switched on by that colour's STROBE. Do not dim with PWM:
fluorescence tracks the light, so any ripple shows up as ΔF/F. Two proven
choices:

- **UCLA Miniscope V4:** LTC3218 charge pump, current set at its ISET pin by a
  TPL0102 digital potentiometer.
- **NINscope:** TI LM3643, a dual driver with a hardware strobe pin and
  1.4 mA current steps.

Ghosh et al. 2011 measured about 10% LED drift over the first minute, so let
the LEDs warm up before recording.

## Filters

| Part | Candidates |
|---|---|
| Dual-band sets | Chroma 59022 (ET-EGFP/mCherry). Semrock GFP/DsRed-A: FF01-468/553, FF493/574-Di01, FF01-512/630 (IDEX lists the set as obsolete). |
| Zhang et al. 2026 (two LEDs, two sensors) | ET470/40x, ET560/25x, T510lpxr to merge the LEDs, 69013bs main dichroic, T550lpxrxt to split, ET525/36m and ET630/75m. |
| Dong et al. 2025 (two sensors) | ZET488/561x, ZT488/561rpc, T556lpxr to split, ET525/50m and ET600/50m. The ZT488/561rpc reflects only narrow laser bands, so with LEDs it needs narrow exciters. |
| Size | The Miniscope build guide cuts filters to 4 × 4 mm and dichroics to 4.8 × 4 mm, ≤ 1.1 mm thick. Chroma offers custom cutting. |

## Indicators and crosstalk

- **jRGECO1a photoswitches under blue light**: up to about 25% brighter at
  ~7 mW/mm², and up to 12% of the signal for R-GECO1.2 at 1.35 mW/mm². That
  corrupts option A. jRCaMP1a/b do not switch (Dana et al. 2016), and
  PinkyCaMP showed no switching at 470 nm and 1 mW/mm² in a study that used
  the nVue.
- **The red channel is the dim one.** Red GECIs have 3–4× smaller maximum
  ΔF/F than GCaMP6 (Dana et al. 2016).
- **Crosstalk still needs correcting.** 470 nm still excites red indicators
  at about 10% of their peak, and GCaMP leaks about 7.5% into a 600/50 red
  channel (Dong et al. 2025). Plan on unmixing, with single-indicator controls
  as references.

## Sensor choice

### Shortlist

| | Pixel, array | Green QE | Red QE (~620 nm) | Read noise | Full well | Power | Package |
|---|---|---|---|---|---|---|---|
| ST VD55G1 (this build) | 2.16 µm BSI GS, 804 × 704 | ≥ 83% at 550 nm | Unpublished in what we could read; ≥ ~60% estimated | < 5 e⁻ | 7,500 e⁻ | 20 mW at 30 fps, 40 mW at 60 fps (target) | Bare die; PLCC via ChipPack |
| **ams OSRAM Mira050** | 2.79 µm BSI GS, 576 × 768 | 93% at 550 nm | Unpublished; ~90% on the Mira220, same pixel | 6.1 e⁻ (4.9 at 8× gain) | 9,000 e⁻ | 11 mW (8-bit, 15 fps) to 96 mW (12-bit, 120 fps) | CSP 2.29 × 2.83 × 0.64 mm with AR-coated glass; 1-lane MIPI |
| ams OSRAM Mira220 | 2.79 µm BSI GS, 1600 × 1400 | ~96% at 525 nm (curve) | ~90% (curve) | 7.0 e⁻ | 10,800 e⁻ | 168 mW at 30 fps | CSP 5.45 mm square; 2-lane MIPI |
| Sony IMX900 | 2.25 µm BSI GS, 2064 × 1552 | 85–89% at 541 nm, *measured* | Not found | 5.4 e⁻, *measured* | ~10,000 e⁻, *measured* | ~420 mW (whole module) | Ceramic LGA 12.0 × 9.3 mm; MIPI 1/2/4 lanes |
| Sony IMX662 mono (STARVIS 2) | 2.9 µm BSI, **rolling shutter**, 1920 × 1080 | ~91% (IMX585, same generation, *measured*) | ~86% (IMX585, *measured*) | ~0.8 e⁻ in its low-noise mode, *measured* | Low in that mode (not found) | 350 mW–1 W (whole module) | Ceramic LGA; MIPI 2/4 lanes |
| onsemi PYTHON480 (UCLA V4) | 4.8 µm GS, 808 × 608 | ~52% at 500–530 nm; 54% at 545 nm *measured* on sibling parts | ~53% | < 11 e⁻ | 10,000 e⁻ | 226–265 mW | CSP 6.13 × 4.93 mm |

**VD55G1 red QE, estimated.** 83% QE at 550 nm needs at least ~2.8 µm of
silicon (absorption length ~1.6 µm). That much silicon alone absorbs ~68% at
600 nm and ~54% at 650 nm (absorption lengths ~2.4 and ~3.6 µm), and any
back-reflection raises both. This is an inference; Fig. 21 of ST's datasheet
DS13604 has the measured curve. **VERIFY** before committing.

### Light needed for equal signal-to-noise

Each sensor's pixel is mapped to 1.6 µm of tissue (about V4's sampling), so a
12 µm cell covers ~44 pixels. The table gives the light each sensor needs to
match the PYTHON480's signal-to-noise. The light levels are estimates, in
photons per cell per frame: 120k is typical green (about 1,500 electrons per
PYTHON480 pixel), 20k a dim red channel, 3k very dim.

| | Green, 120k | Red, 20k | Red, 3k |
|---|---|---|---|
| PYTHON480 | 1.00 | 1.00 | 1.00 |
| VD55G1 (red at ~60%, the estimate above) | 0.59 | 0.67 | 0.47 |
| Mira050 (red borrowed from the Mira220) | 0.53 | 0.47 | 0.36 |
| Mira220 | 0.52 | 0.49 | 0.40 |
| IMX900 | 0.57 | ? | ? |
| IMX662, low-noise mode (IMX585's QE) | 0.53 | 0.41 | 0.15 |
| IMX296 mono (front-illuminated, *measured*) | 0.76 | 0.68 | 0.47 |

- **In bright green only QE matters.** Every backside-illuminated sensor needs
  about 55% of the PYTHON480's light.
- **Read noise only matters in dim light,** which is where STARVIS 2 pulls
  ahead.
- **Red QE is what could swap the VD55G1 and the Mira050.** At the estimate
  above, the Mira050 needs about a third less light in red. If the VD55G1
  turns out near 85%, they tie.
- **Charge per cell** at this sampling is full well per pixel × 44. It only
  matters near saturation: the VD55G1 holds 25% less than the PYTHON480, the
  Mira050 10% less, the Mira220 8% more.

### The candidates

- **Mira050, the one to bench beside the VD55G1.**
  - Same 1-lane MIPI, so the ESP32-P4 path is unchanged, but it needs its own
    sensor driver.
  - A solderable package removes the chip-on-board problem. Mono CSP is
    `Mira050-2QM1WB`, sold by ams in multiples of 120 and stocked by Farnell.
  - Its pixel accepts any lens chief-ray angle from 0° to 30°, so a miniscope
    tube lens's near-straight light is fine.
  - Its near-infrared QE (56% at 850 nm, 36% at 940 nm, against the VD55G1's
    22% at 940 nm) suggests strong red response. ams publishes no curve:
    **VERIFY** with ams.
  - Shutter efficiency −120 dB at 550 nm; dark current 30 e⁻/s at 60 °C.
  - **Alternating colours takes help from the P4.** It has two register
    contexts (exposure, frame time, region of interest, gain, and the LED
    trigger on or off), but the switch between them is an I²C command from
    the host. It has one LED trigger output, timed to the exposure, so the P4
    must also route it to the right LED driver each frame, e.g. by gating
    each driver's enable. The VD55G1 does both in hardware.
  - Only 576 usable rows, so a 1 mm field gets 1.74 µm pixels at the tissue.
- **IMX662 mono (STARVIS 2), the low-light reference.**
  - It is a rolling shutter. At 30 fps there is a ~22 ms window when every row
    is exposing, for the LED pulse. Alternating at 60 fps leaves ~5 ms on
    4 lanes and less on the P4's 2, unless fewer rows are read.
  - Its well is shallow in low-noise mode, and it draws 10–25× the VD55G1's
    power.
  - Sony's datasheet is under NDA, and the IMX662 is no longer on Sony's
    lineup page. FRAMOS and Veye sell mono modules.
  - No miniscope has used STARVIS 2. MiniFAST used the first-generation
    IMX290.
- **Mira220 or IMX900, for a large-field rat scope.** At 1.6 µm sampling they
  cover 1.7–2.1 mm. The Mira220 has the best published red curve and fits the
  P4's 2 lanes. The IMX900 is the only candidate measured independently, but
  its package and power suit a rat, not a mouse.

**Not better than the VD55G1:** onsemi AR0144 / AR0145 / AR0234,
OmniVision OV9281 and Sony IMX296 / IMX297 (front-illuminated, 60–67% QE in
green); OmniVision OG0VA1B and OG01A1B (no visible-light data; the OG01A1B is
bare die only); onsemi ARX3A0 (rolling shutter, no data); Mira016 (400 × 400
is too few pixels); Mira130 (discontinued).

**The PYTHON480 is not a safe fallback either.** UCLA V4 uses
`NOIP1SP0480A` (23.6° chief-ray angle), which onsemi lists as "not
recommended for new design" and unavailable. The 1.65° `NOIP1SN0480A` is
active.

**What other miniscopes use.** The open designs settled on the PYTHON480
(V4, NINscope, Miniscope Zero, Dong et al.'s two-sensor scope). MiniLFOV and
MiniXL moved to the onsemi MT9P031 for field of view, and MiniFAST to Sony's
IMX290 for speed. Inscopix's sensor is not identified. Nobody has published a
miniscope on a Mira, STARVIS 2 or IMX900 sensor.

## Plan, risk first

0. **Answers from vendors:** Fig. 21 of DS13604 (the VD55G1's QE curve), the
   Mira050 QE curve from ams, and ChipPack's VD55G1 PLCC drawing.
1. **Sensor bench test:** the clear VD55G1 module and a Mira050 on the same
   ESP32-P4 carrier. Photon-transfer curves at 525 and 620 nm through the real
   filters, and frame-to-frame noise on green and red fluorescent slides at
   about 1 mW/mm². If jRCaMP1b at low power looks marginal, add an IMX662 mono
   module as the low-noise reference.
2. **Excitation bench test:** two LEDs with half-balls and their exciters
   merged by a T510lpxr. Measure the field's evenness and the LEDs' stability
   frame to frame and over the first minute.
3. **Alternation:** context-sequenced STROBE on GPIO1 and GPIO3 driving two
   constant-current channels; the host splits the channels, and the colour of
   every frame is checked.
4. **Optics:** a dual-band filter cube, objective, focus lens and tube lens
   at ~1.5× onto the chosen sensor; then the electrowetting lens switched with
   the LEDs.

For analysis, CaImAn (CNMF-E) or Minian can replace Inscopix's software. To
align the recordings with the rest of a rig, feed the rig's TTL into the sync
line ([hardware.md](hardware.md#synchronization)).

## Sources

- nVue: [system specifications](https://smallanimalimaging.readthedocs.io/en/latest/4-nVue-nVision/system-specifications.html);
  Inscopix on [dual-colour imaging](https://inscopix.com/2022/05/simultaneous-blood-flow-and-neural-activity-imaging-with-inscopix-miniscopes/);
  patent [US12066618](https://patents.google.com/patent/US12066618).
- Dual-colour miniscopes: [Dong et al. 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12219470/),
  [Zhang et al. 2026](https://pmc.ncbi.nlm.nih.gov/articles/PMC12934655/),
  [Barbera et al. 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11663848/),
  [Doric 2-colour system](https://neuro.doriclenses.com/products/2-color-fluorescence-microscopy-system).
- Excitation optics: [V4 assembly guide](https://miniscope.org/wiki/Guide/Miniscope_V4_Assembly),
  [V4 BOM](https://github.com/Aharoni-Lab/Miniscope-v4/blob/master/Miniscope-v4-Rigid-Flex/KiCad/Output/BOM/Miniscope-v4-Rigid-Flex_bom_4_41.csv),
  [NINscope](https://pmc.ncbi.nlm.nih.gov/articles/PMC6989121),
  [Ghosh et al. 2011](https://pmc.ncbi.nlm.nih.gov/articles/PMC3810311),
  [Miniscope fabrication guide](https://miniscope.org/wiki/Fabrication_and_Assembly_Guide),
  TI [LM3643](https://www.ti.com/lit/gpn/LM3643).
- Indicators: [Dana et al. 2016](https://pmc.ncbi.nlm.nih.gov/articles/PMC4846379),
  [PinkyCaMP](https://www.nature.com/articles/s41592-026-03065-2),
  [FPbase jRGECO1a](https://www.fpbase.org/protein/jrgeco1a/).
- Sensors: [PYTHON480 datasheet](https://www.onsemi.com/download/data-sheet/pdf/noip1sn0480a-d.pdf)
  and [product page](https://www.onsemi.com/products/sensors/image-sensors/python480);
  [VD55G1 module spec (ZS1320-B)](https://www1.futureelectronics.com/doc/Chongqing%20Zhongshun%20Micro/ZS1320-B(VD55G1CCB0)FOV79.8%C2%B0CameraModuleSpec231206.pdf);
  [Mira050](https://look.ams-osram.com/m/6292d2e79c3e0bbf/original/Mira050-0-5-MP-NIR-enhanced-high-speed-global-shutter-image-sensor.pdf)
  and [Mira220](https://look.ams-osram.com/m/7591efdbe4af32dc/original/Mira220-1-2-7-2-2-MP-NIR-enhanced-global-shutter-image-sensor.pdf)
  datasheets; Sony [IMX900](https://www.sony-semicon.com/en/products/is/industry/gs/imx900.html);
  [Balluff IMX900 EMVA report](https://assets.balluff.com/EMVA1288/PDF/emva1288_report_BVS%20CA-GV1-0032BG_short.pdf);
  [ZWO ASI585MM test](https://i.zwoastro.com/wp-content/uploads/2025/09/04a912015d682f11887ca758deedb947.pdf);
  [FLIR IMX296 EMVA](https://softwareservices.flir.com/FFY-U3-16S2/latest/EMVA/EMVA.html).
- Other miniscopes: [MiniLFOV](https://pmc.ncbi.nlm.nih.gov/articles/PMC10121160/),
  [MiniXL](https://pmc.ncbi.nlm.nih.gov/articles/PMC12154187/),
  [MiniFAST](https://www.biorxiv.org/content/10.1101/2020.11.03.367466v1.full).
