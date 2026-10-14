"""Analyse the sensor bench test (docs/sensor-bench-test.md) from recorded frames.

    nireyecam-sensortest ptc ptc.csv --json ptc.json
    nireyecam-sensortest snr snr.csv --target-snr 100
    nireyecam-sensortest alternation runs/two-colour/cam --green-roi 0,0,300,704 --red-roi 500,0,804,704

One subcommand per part of the plan that needs its own analysis:

- ptc (Part 1, lensless): photon transfer (conversion gain, read noise,
  saturation capacity, linearity), quantum efficiency in each emission band,
  dark current, DSNU / PRNU and the corner-to-centre response, from a manifest
  of light and dark captures.
- snr (Part 2, fluorescence behind identical optics): signal-to-noise per
  cell-sized region, the excitation power that reaches a target SNR, drift,
  and how well a small LED step is detected, from a manifest of runs.
- alternation (Part 3): checks each frame's colour, taken from the parity of
  the sensor frame counter, against what the frame shows on a two-colour
  target; counts dropped frames; and measures leakage into the other colour's
  frames on a one-colour target.

Frames can be .npy stacks (frames x height x width, any integer dtype), raw
.u8 or .u16 (little-endian) files with --size WxH or a <file>.json sidecar,
or a nireyecam-record camera folder (frames.u8, or video.mkv via ffmpeg).
Results in electrons do not depend on how the DN are scaled (10-bit data
stored MSB-aligned in 16 bits, say), since the conversion gain is measured
in the same DN. Capture with every on-chip correction off: noise reduction,
defect correction, auto-exposure, black-level tracking.

The methods are EMVA 1288's, simplified: the photon-transfer and QE fits use
the points up to 70% of saturation, and spatial nonuniformity is measured
after removing a quadratic surface instead of EMVA's high-pass filter. As in
EMVA 1288, saturation capacity is the mean signal where the temporal variance
peaks, which sits somewhat below the full well; read noise is the dark
temporal noise at the shortest exposure, so it includes quantization and the
dark current's shot noise.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import __version__

H_PLANCK = 6.62607015e-34   # J s
C_LIGHT = 299792458.0       # m / s
FIT_TOP = 0.70              # photon-transfer and QE fits use points up to this fraction of saturation
LIN_RANGE = (0.05, 0.95)    # linearity is judged between these fractions of saturation
EXPOSURE_MATCH = 0.01       # a dark capture matches a light one within this relative exposure difference
PRNU_MIN_FRAMES = 16        # fewer averaged frames leave too much temporal noise in the spatial estimate
MIN_SEPARATION = 5.0        # two-colour frame classes closer than this (in noise units) are not trusted


class InputError(Exception):
    """A manifest, file or option the analysis cannot use."""


# ------------------------------------------------------------------ input

RAW_DTYPES = {".u8": np.dtype("u1"), ".u16": np.dtype("<u2")}


def parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise InputError(f"bad size {text!r}: use WxH, e.g. 804x704") from None
    return w, h


def parse_rect(text: str) -> tuple[int, int, int, int]:
    try:
        x0, y0, x1, y1 = (int(v) for v in text.split(","))
    except ValueError:
        raise InputError(f"bad region {text!r}: use X0,Y0,X1,Y1 in pixels") from None
    if x1 <= x0 or y1 <= y0:
        raise InputError(f"empty region {text!r}")
    return x0, y0, x1, y1


def _sidecar_size(path: Path) -> tuple[int, int] | None:
    side = path.with_name(path.name + ".json")
    if not side.exists():
        return None
    meta = json.loads(side.read_text())
    return int(meta["width"]), int(meta["height"])


def _read_seq(path: Path) -> np.ndarray:
    with open(path, newline="") as f:
        return np.array([int(r["seq"]) for r in csv.DictReader(f)], dtype=np.int64)


def _decode_video(path: Path) -> np.ndarray:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise InputError(f"{path}: decoding video needs ffmpeg and ffprobe on the PATH")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    w, h = (int(v) for v in probe.stdout.strip().split(",")[:2])
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(out.stdout, dtype=np.uint8).reshape(-1, h, w)


def load_stack(path, size: tuple[int, int] | None = None) -> tuple[np.ndarray, np.ndarray | None]:
    """Frames as (frames, height, width), plus the sensor frame counter if the source records it."""
    path = Path(path)
    if path.is_dir():
        frames_csv = path / "frames.csv"
        seq = _read_seq(frames_csv) if frames_csv.exists() else None
        if (path / "frames.u8").exists():
            frames, _ = load_stack(path / "frames.u8", size)
        elif (path / "video.mkv").exists():
            frames = _decode_video(path / "video.mkv")
        else:
            raise InputError(f"{path}: no frames.u8 or video.mkv (not a nireyecam-record camera folder)")
        if seq is not None and len(seq) != len(frames):
            n = min(len(seq), len(frames))
            frames, seq = frames[:n], seq[:n]
        return frames, seq
    if not path.exists():
        raise InputError(f"{path}: no such file")
    suffix = path.suffix.lower()
    if suffix == ".npy":
        a = np.load(path, mmap_mode="r")
        if a.ndim == 2:
            a = a[None]
        if a.ndim != 3:
            raise InputError(f"{path}: expected frames x height x width, got shape {a.shape}")
        return a, None
    if suffix in RAW_DTYPES:
        wh = _sidecar_size(path) or size
        if wh is None:
            raise InputError(f"{path}: raw frames need --size WxH, or a {path.name}.json sidecar")
        w, h = wh
        a = np.memmap(path, dtype=RAW_DTYPES[suffix], mode="r")
        if a.size % (w * h):
            raise InputError(f"{path}: {a.size} pixels is not a whole number of {w}x{h} frames")
        return a.reshape(-1, h, w), None
    raise InputError(f"{path}: unsupported format; use .npy, .u8, .u16 or a nireyecam-record camera folder")


def read_manifest(path) -> list[dict]:
    """Rows of a CSV manifest; each row's 'file' (and 'dark') resolved against the manifest's folder."""
    path = Path(path)
    if not path.exists():
        raise InputError(f"{path}: no such manifest")
    with open(path, newline="") as f:
        rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]
    if not rows:
        raise InputError(f"{path}: no rows")
    for line, r in enumerate(rows, start=2):
        r["_line"] = line
        if not r.get("file"):
            raise InputError(f"{path}:{line}: no 'file'")
        for key in ("file", "dark", "regions"):
            if r.get(key):
                r[key] = path.parent / r[key]
    return rows


def _num(row: dict, key: str, required: bool = False) -> float | None:
    text = row.get(key, "")
    if text == "":
        if required:
            raise InputError(f"manifest line {row['_line']}: '{key}' is required")
        return None
    try:
        return float(text)
    except ValueError:
        raise InputError(f"manifest line {row['_line']}: '{key}' is not a number: {text!r}") from None


# ------------------------------------------------------------------ statistics

@dataclass
class Moments:
    frames: int
    mean: float           # DN, over the region
    temporal_var: float   # DN^2: per-pixel variance over frames, averaged over the region


def _region(shape: tuple[int, int], crop: float) -> tuple[slice, slice]:
    h, w = shape
    ch, cw = max(1, round(h * crop)), max(1, round(w * crop))
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return slice(y0, y0 + ch), slice(x0, x0 + cw)


def moments(frames: np.ndarray, crop: float = 1.0) -> tuple[Moments, np.ndarray, np.ndarray]:
    """Mean and temporal variance over the central ``crop`` of the frame, plus the full mean and variance images."""
    n = len(frames)
    if n < 2:
        raise InputError("a capture needs at least 2 frames")
    x0 = np.asarray(frames[0], dtype=np.float64)
    s = np.zeros_like(x0)
    ss = np.zeros_like(x0)
    for f in frames:
        d = np.asarray(f, dtype=np.float64) - x0    # shifted, so large means do not cost precision
        s += d
        ss += d * d
    mean_img = x0 + s / n
    var_img = (ss - s * s / n) / (n - 1)
    ys, xs = _region(mean_img.shape, crop)
    return Moments(n, float(mean_img[ys, xs].mean()), float(var_img[ys, xs].mean())), mean_img, var_img


def detrend_quadratic(img: np.ndarray) -> np.ndarray:
    """``img`` minus its least-squares quadratic surface: removes shading, keeps pixel-to-pixel pattern."""
    h, w = img.shape
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    x = (x - (w - 1) / 2) / max(w, 1)
    y = (y - (h - 1) / 2) / max(h, 1)
    a = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=-1).reshape(-1, 6)
    coef, *_ = np.linalg.lstsq(a, img.ravel(), rcond=None)
    return img - (a @ coef).reshape(h, w)


def photons_per_pixel(irradiance_w_m2: float, wavelength_nm: float, pixel_um: float, exposure_ms: float) -> float:
    energy = irradiance_w_m2 * (pixel_um * 1e-6) ** 2 * exposure_ms * 1e-3
    return energy / (H_PLANCK * C_LIGHT / (wavelength_nm * 1e-9))


# ------------------------------------------------------------------ Part 1: photon transfer

@dataclass
class PtcPoint:
    exposure_ms: float
    photons: float | None     # mean photons per pixel in the exposure, if irradiance was given
    mean_dn: float            # light minus dark
    var_dn2: float            # light minus dark temporal variance
    dark_var_dn2: float
    frames: int
    dark_exposure_ms: float | None


@dataclass
class PtcResult:
    sensor: str
    gain: str
    band: str
    wavelength_nm: float | None
    points: list = field(default_factory=list)
    k_dn_per_e: float | None = None
    e_per_dn: float | None = None
    read_noise_e: float | None = None
    saturation_e: float | None = None
    saturation_reached: bool = False
    qe: float | None = None
    linearity_error_pct: float | None = None
    dynamic_range_db: float | None = None
    snr_max_db: float | None = None
    dsnu_e: float | None = None
    prnu_pct: float | None = None
    nonuniformity_exposure_ms: float | None = None
    corners: dict = field(default_factory=dict)   # corner -> response relative to the centre
    corner_min: float | None = None
    notes: list = field(default_factory=list)


@dataclass
class DarkCurrentResult:
    sensor: str
    gain: str
    exposures_ms: list = field(default_factory=list)
    means_dn: list = field(default_factory=list)
    black_level_dn: float | None = None
    dark_current_e_s: float | None = None
    notes: list = field(default_factory=list)


@dataclass
class PtcReport:
    tool_version: str
    command: str = ""
    crop: float = 1.0
    groups: list = field(default_factory=list)
    dark: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _match_dark(darks: list, light: dict) -> dict | None:
    exp = _num(light, "exposure_ms", required=True)
    pool = [d for d in darks if d["sensor"] == light["sensor"] and d["gain"] == light["gain"]
            and d["band"] in ("", light["band"])]
    if not pool:
        return None
    return min(pool, key=lambda d: abs(d["_exp"] - exp))


def _fit_ptc(res: PtcResult) -> None:
    pts = res.points
    y = np.array([p.mean_dn for p in pts])
    v = np.array([p.var_dn2 for p in pts])
    imax = int(np.argmax(v))
    res.saturation_reached = imax < len(v) - 1    # the variance drops after its peak: saturation
    y_sat = float(y[imax]) if res.saturation_reached else float(y.max())
    upto = np.arange(len(y)) <= imax
    fit = upto & (y > 0) & (y <= FIT_TOP * y_sat)
    if fit.sum() < 3:
        res.notes.append(f"only {int(fit.sum())} points below {FIT_TOP:.0%} of saturation; "
                         "photon transfer needs at least 3")
        return
    k = float(np.sum(y[fit] * v[fit]) / np.sum(y[fit] ** 2))   # sigma^2_y - sigma^2_dark = K (mu_y - mu_dark)
    if k <= 0:
        res.notes.append("the variance does not grow with the signal; check the light and the dark captures")
        return
    res.k_dn_per_e = k
    res.e_per_dn = 1 / k
    shortest = min(pts, key=lambda p: p.exposure_ms)
    res.read_noise_e = math.sqrt(max(shortest.dark_var_dn2, 0.0)) / k
    res.saturation_e = y_sat / k
    if not res.saturation_reached:
        res.notes.append("saturation not reached: saturation capacity is a lower bound")
    if res.read_noise_e > 0:
        res.dynamic_range_db = 20 * math.log10(res.saturation_e / res.read_noise_e)
    res.snr_max_db = 10 * math.log10(res.saturation_e)
    have_photons = all(p.photons is not None for p in pts)
    if have_photons:
        p_ = np.array([p.photons for p in pts])
        responsivity = float(np.sum(p_[fit] * y[fit]) / np.sum(p_[fit] ** 2))   # DN per photon
        res.qe = responsivity / k
    else:
        res.notes.append("no irradiance (or wavelength or pixel size) given: no QE; linearity against exposure")
    x = np.array([p.photons for p in pts]) if have_photons else np.array([p.exposure_ms for p in pts])
    lin = upto & (y >= LIN_RANGE[0] * y_sat) & (y <= LIN_RANGE[1] * y_sat)
    if lin.sum() >= 3:
        slope, icpt = np.polyfit(x[lin], y[lin], 1)
        model = slope * x[lin] + icpt
        dev = (y[lin] - model) / model * 100
        res.linearity_error_pct = float((dev.max() - dev.min()) / 2)


def _nonuniformity(res: PtcResult, rows: list, darks: list, size, crop: float, corner_frac: float) -> None:
    if res.k_dn_per_e is None:
        return
    y_sat = res.saturation_e * res.k_dn_per_e
    paired = list(zip(res.points, rows))
    many = [pr for pr in paired if pr[0].frames >= PRNU_MIN_FRAMES]
    point, row = min(many or paired, key=lambda pr: abs(pr[0].mean_dn - 0.5 * y_sat))
    if not many:
        res.notes.append(f"no light capture has {PRNU_MIN_FRAMES}+ frames: PRNU and DSNU are rough")
    res.nonuniformity_exposure_ms = point.exposure_ms
    frames, _ = load_stack(row["file"], size)
    lm, light_img, _ = moments(frames, crop)
    dark_row = _match_dark(darks, row)
    if dark_row is None:
        res.notes.append("no dark capture: PRNU includes the dark pattern, and there is no DSNU")
        signal = light_img
        dm, dark_img = Moments(0, 0.0, 0.0), None
    else:
        dframes, _ = load_stack(dark_row["file"], size)
        dm, dark_img, _ = moments(dframes, crop)
        if dark_img.shape != light_img.shape:
            raise InputError(f"{dark_row['file']}: dark frames are {dark_img.shape}, light frames {light_img.shape}")
        signal = light_img - dark_img
    ys, xs = _region(signal.shape, crop)
    residual_t = lm.temporal_var / lm.frames + (dm.temporal_var / dm.frames if dm.frames else 0.0)
    s2 = float(detrend_quadratic(signal[ys, xs]).var()) - residual_t
    mean = float(signal[ys, xs].mean())
    if mean > 0:
        res.prnu_pct = math.sqrt(max(s2, 0.0)) / mean * 100
    if dark_img is not None:
        s2d = float(detrend_quadratic(dark_img[ys, xs]).var()) - dm.temporal_var / dm.frames
        res.dsnu_e = math.sqrt(max(s2d, 0.0)) / res.k_dn_per_e
    h, w = signal.shape
    side = max(2, round(corner_frac * min(h, w)))
    cy, cx = (h - side) // 2, (w - side) // 2
    centre = float(signal[cy:cy + side, cx:cx + side].mean())
    if centre > 0:
        boxes = {"top left": signal[:side, :side], "top right": signal[:side, -side:],
                 "bottom left": signal[-side:, :side], "bottom right": signal[-side:, -side:]}
        res.corners = {k: float(b.mean()) / centre for k, b in boxes.items()}
        res.corner_min = min(res.corners.values())


def analyse_ptc(rows: list, *, size=None, crop: float = 1.0, corner_frac: float = 0.05) -> PtcReport:
    rep = PtcReport(tool_version=__version__, crop=crop)
    for r in rows:
        r.setdefault("sensor", "")
        r.setdefault("gain", "")
        r.setdefault("band", "")
        kind = r.get("kind", "").lower()
        if kind not in ("light", "dark"):
            raise InputError(f"manifest line {r['_line']}: 'kind' must be light or dark")
        r["kind"] = kind
        r["_exp"] = _num(r, "exposure_ms", required=True)
    darks = [r for r in rows if r["kind"] == "dark"]
    for d in darks:
        frames, _ = load_stack(d["file"], size)
        d["_m"] = moments(frames, crop)[0]

    groups: dict = {}
    for r in rows:
        if r["kind"] == "light":
            groups.setdefault((r["sensor"], r["gain"], r["band"]), []).append(r)
    if not groups:
        raise InputError("the manifest has no light captures")
    for (sensor, gain, band), grp in groups.items():
        grp.sort(key=lambda r: r["_exp"])
        res = PtcResult(sensor, gain, band, _num(grp[0], "wavelength_nm"))
        for r in grp:
            frames, _ = load_stack(r["file"], size)
            m = moments(frames, crop)[0]
            d = _match_dark(darks, r)
            dm = d["_m"] if d else Moments(0, 0.0, 0.0)
            if d is None:
                res.notes.append("no dark capture for this sensor and gain: dark not subtracted")
            elif abs(d["_exp"] - r["_exp"]) > EXPOSURE_MATCH * r["_exp"]:
                res.notes.append(f"light at {r['_exp']:g} ms used the dark at {d['_exp']:g} ms")
            irr, wl, pix = _num(r, "irradiance_w_m2"), _num(r, "wavelength_nm"), _num(r, "pixel_um")
            photons = photons_per_pixel(irr, wl, pix, r["_exp"]) if None not in (irr, wl, pix) else None
            res.points.append(PtcPoint(r["_exp"], photons, m.mean - dm.mean, m.temporal_var - dm.temporal_var,
                                       dm.temporal_var, m.frames, d["_exp"] if d else None))
        res.notes = list(dict.fromkeys(res.notes))
        _fit_ptc(res)
        _nonuniformity(res, grp, darks, size, crop, corner_frac)
        rep.groups.append(res)

    by_sg: dict = {}
    for d in darks:
        by_sg.setdefault((d["sensor"], d["gain"]), []).append(d)
    for (sensor, gain), ds in by_sg.items():
        dc = DarkCurrentResult(sensor, gain)
        ds = sorted(ds, key=lambda d: d["_exp"])
        dc.exposures_ms = [d["_exp"] for d in ds]
        dc.means_dn = [d["_m"].mean for d in ds]
        if len(set(dc.exposures_ms)) < 2:
            dc.notes.append("dark captures at one exposure only: no dark current")
        else:
            slope, icpt = np.polyfit(np.array(dc.exposures_ms) / 1e3, np.array(dc.means_dn), 1)
            dc.black_level_dn = float(icpt)
            ks = [g.k_dn_per_e for g in rep.groups
                  if g.sensor == sensor and g.gain == gain and g.k_dn_per_e is not None]
            if ks:
                dc.dark_current_e_s = float(slope) / float(np.mean(ks))
            else:
                dc.notes.append("no conversion gain for this sensor and gain: dark current in DN/s only "
                                f"({slope:.4g} DN/s)")
        rep.dark.append(dc)
    return rep


# ------------------------------------------------------------------ Part 2: SNR per cell

@dataclass
class SnrRun:
    sensor: str
    channel: str
    power_mw_mm2: float
    file: str
    frames: int = 0
    regions: int = 0
    pixels_per_region: float = 0.0
    signal_dn: float | None = None      # medians over the regions, per frame
    noise_dn: float | None = None       # frame-to-frame
    snr: float | None = None
    signal_e: float | None = None
    noise_e: float | None = None
    drift_pct: float | None = None
    drift_per: str = ""                 # "minute" or "1000 frames"
    first_minute_pct: float | None = None
    step_pct: float | None = None       # small-signal test: measured step, high vs low
    dprime: float | None = None         # small-signal test: step / frame-to-frame noise, per region
    notes: list = field(default_factory=list)


@dataclass
class TargetPower:
    sensor: str
    channel: str
    target_snr: float
    power_mw_mm2: float | None = None
    note: str = ""


@dataclass
class SnrReport:
    tool_version: str
    command: str = ""
    cell_um: float = 12.0
    field_um: float = 500.0
    target_snr: float = 100.0
    runs: list = field(default_factory=list)
    targets: list = field(default_factory=list)


def cell_regions(shape, sample_px_um: float, cell_um: float, field_um: float,
                 centres: list | None = None) -> tuple[np.ndarray, int]:
    """A label image: each cell-sized disk gets its index, everything else -1.

    Without ``centres``, the disks tile a square of ``field_um`` (in the sample)
    at the middle of the frame, so two sensors at different pixel sizes are
    compared over the same piece of the sample.
    """
    h, w = shape
    r = cell_um / 2 / sample_px_um
    if r < 1.5:
        raise InputError(f"a {cell_um:g} um cell is only {2 * r:.1f} px across at {sample_px_um:g} um/px")
    if centres is None:
        side = min(field_um / sample_px_um, min(h, w) - 2)
        n = int(side // (2 * r))
        if n < 1:
            raise InputError("the field is smaller than one cell")
        offs = (np.arange(n) - (n - 1) / 2) * 2 * r
        centres = [((w - 1) / 2 + dx, (h - 1) / 2 + dy) for dy in offs for dx in offs]
    labels = np.full((h, w), -1, dtype=np.int64)
    yy, xx = np.mgrid[0:h, 0:w]
    for i, (cx, cy) in enumerate(centres):
        y0, y1 = max(0, int(cy - r) - 1), min(h, int(cy + r) + 2)
        x0, x1 = max(0, int(cx - r) - 1), min(w, int(cx + r) + 2)
        disk = (xx[y0:y1, x0:x1] - cx) ** 2 + (yy[y0:y1, x0:x1] - cy) ** 2 <= r * r
        labels[y0:y1, x0:x1][disk] = i
    return labels, len(centres)


def _read_centres(path: Path) -> list:
    with open(path, newline="") as f:
        return [(float(r["x"]), float(r["y"])) for r in csv.DictReader(f)]


def region_sums(frames: np.ndarray, labels: np.ndarray, n: int, dark: np.ndarray | None) -> np.ndarray:
    """(frames, regions) sums of DN over each region, dark-subtracted."""
    sel = labels.ravel() >= 0
    idx = labels.ravel()[sel]
    out = np.empty((len(frames), n))
    for i, f in enumerate(frames):
        out[i] = np.bincount(idx, weights=np.asarray(f, dtype=np.float64).ravel()[sel], minlength=n)
    if dark is not None:
        out -= np.bincount(idx, weights=dark.ravel()[sel], minlength=n)
    return out


def _small_signal(run: SnrRun, sums: np.ndarray, period: int) -> None:
    """LED stepped between two levels every ``period`` frames: the step, and d' per region."""
    n = len(sums)
    if period < 3 or n < 4 * period:
        run.notes.append("small-signal test needs period_frames >= 3 and at least 2 full cycles")
        return
    trace = sums.sum(axis=1)
    i = np.arange(n)
    best = max(range(2 * period),
               key=lambda ph: float(np.dot(trace - trace.mean(), np.where(((i + ph) // period) % 2 == 0, 1, -1))))
    high = ((i + best) // period) % 2 == 0
    pos = (i + best) % period
    keep = pos != 0                     # the first frame of each block may straddle the step
    mh, ml = sums[high & keep].mean(axis=0), sums[~high & keep].mean(axis=0)
    within = (pos[:-1] >= 1) & (pos[1:] == pos[:-1] + 1)
    noise = np.diff(sums, axis=0)[within].std(axis=0, ddof=1) / math.sqrt(2)
    run.step_pct = float(np.median((mh - ml) / ml * 100))
    run.dprime = float(np.median((mh - ml) / noise))


def analyse_snr(rows: list, *, size=None, cell_um: float = 12.0, field_um: float = 500.0,
                target_snr: float = 100.0) -> SnrReport:
    rep = SnrReport(tool_version=__version__, cell_um=cell_um, field_um=field_um, target_snr=target_snr)
    for r in rows:
        run = SnrRun(r.get("sensor", ""), r.get("channel", ""), _num(r, "power_mw_mm2", required=True),
                     str(r["file"]))
        frames, _ = load_stack(r["file"], size)
        run.frames = len(frames)
        if run.frames < 3:
            raise InputError(f"manifest line {r['_line']}: an SNR run needs at least 3 frames")
        centres = _read_centres(r["regions"]) if r.get("regions") else None
        labels, n = cell_regions(frames.shape[1:], _num(r, "sample_px_um", required=True),
                                 cell_um, field_um, centres)
        run.regions = n
        run.pixels_per_region = float((labels >= 0).sum() / n)
        dark = None
        if r.get("dark"):
            dframes, _ = load_stack(r["dark"], size)
            dark = moments(dframes)[1]
        else:
            run.notes.append("no dark capture: the signal includes the black level, so the SNR is too high")
        sums = region_sums(frames, labels, n, dark)
        period = _num(r, "period_frames")
        if period:
            _small_signal(run, sums, int(period))
        else:
            signal = sums.mean(axis=0)
            noise = np.diff(sums, axis=0).std(axis=0, ddof=1) / math.sqrt(2)   # frame to frame: ignores drift
            run.signal_dn = float(np.median(signal))
            run.noise_dn = float(np.median(noise))
            run.snr = float(np.median(signal / noise))
            k = _num(r, "k_dn_per_e")
            if k:
                run.signal_e, run.noise_e = run.signal_dn / k, run.noise_dn / k
        trace = sums.sum(axis=1)
        fps = _num(r, "fps")
        t = np.arange(run.frames) / fps / 60 if fps else np.arange(run.frames) / 1000
        slope = np.polyfit(t, trace, 1)[0]
        run.drift_pct = float(slope / trace.mean() * 100)
        run.drift_per = "minute" if fps else "1000 frames"
        if fps and run.frames >= 60 * fps:
            k5 = int(5 * fps)
            first, end = trace[:k5].mean(), trace[int(55 * fps):int(60 * fps)].mean()
            run.first_minute_pct = float((end - first) / first * 100)
        rep.runs.append(run)

    keyed: dict = {}
    for run in rep.runs:
        if run.snr is not None:
            keyed.setdefault((run.sensor, run.channel), []).append(run)
    for (sensor, channel), runs in keyed.items():
        rep.targets.append(_target_power(sensor, channel, runs, target_snr))
    return rep


def _target_power(sensor: str, channel: str, runs: list, target: float) -> TargetPower:
    tp = TargetPower(sensor, channel, target)
    pts = sorted((r.power_mw_mm2, r.snr) for r in runs if r.power_mw_mm2 > 0 and r.snr and r.snr > 0)
    if not pts:
        tp.note = "no usable runs"
        return tp
    if pts[0][1] >= target:
        tp.note = f"already reached at the lowest power, {pts[0][0]:g} mW/mm2"
        return tp
    for (p1, s1), (p2, s2) in zip(pts, pts[1:]):
        if s1 < target <= s2:
            lp = math.log(p1) + (math.log(target) - math.log(s1)) * (math.log(p2) - math.log(p1)) / (
                math.log(s2) - math.log(s1))
            tp.power_mw_mm2 = math.exp(lp)
            return tp
    tp.note = f"not reached at the highest power, {pts[-1][0]:g} mW/mm2"
    return tp


# ------------------------------------------------------------------ Part 3: alternation

@dataclass
class AlternationReport:
    tool_version: str
    command: str = ""
    source: str = ""
    frames: int = 0
    labels_from: str = ""               # "sensor frame counter" or "frame index"
    dropped: int | None = None
    even_frames: str | None = None      # colour of even-numbered frames, as the content shows
    mislabelled: int | None = None
    mislabelled_frames: list = field(default_factory=list)
    separation: float | None = None     # gap between the two frame classes, in noise units
    leakage_pct: float | None = None
    leakage_bright_parity: str | None = None
    notes: list = field(default_factory=list)


def _roi_means(frames: np.ndarray, rect, dark: np.ndarray | None) -> np.ndarray:
    x0, y0, x1, y1 = rect
    if y1 > frames.shape[1] or x1 > frames.shape[2]:
        raise InputError(f"region {rect} is outside the {frames.shape[2]}x{frames.shape[1]} frame")
    v = np.array([float(np.asarray(f[y0:y1, x0:x1], dtype=np.float64).mean()) for f in frames])
    return v - (float(dark[y0:y1, x0:x1].mean()) if dark is not None else 0.0)


def two_means(x: np.ndarray) -> tuple[np.ndarray, float]:
    """Split 1-D values into two classes; returns the high class and the gap in pooled-noise units."""
    t = float(np.median(x))
    for _ in range(100):
        hi = x > t
        if hi.all() or not hi.any():
            return hi, 0.0
        t_new = (x[hi].mean() + x[~hi].mean()) / 2
        if t_new == t:
            break
        t = t_new
    hi = x > t
    noise = math.sqrt((x[hi].var() + x[~hi].var()) / 2) or 1e-12
    return hi, float((x[hi].mean() - x[~hi].mean()) / noise)


def analyse_alternation(source, *, size=None, dark_path=None, green_roi=None, red_roi=None,
                        leak_roi=None) -> AlternationReport:
    if not (green_roi and red_roi) and not leak_roi:
        raise InputError("give --green-roi and --red-roi (two-colour target), --leak-roi (one-colour target), or both")
    rep = AlternationReport(tool_version=__version__, source=str(source))
    frames, seq = load_stack(source, size)
    rep.frames = len(frames)
    if rep.frames < 4:
        raise InputError("the alternation check needs at least 4 frames")
    if seq is not None:
        rep.labels_from = "sensor frame counter"
        steps = np.diff(seq)
        rep.dropped = int(np.sum(steps[steps > 0] - 1))
        if np.any(steps <= 0):
            rep.notes.append("the frame counter goes backwards or repeats: labels after that point are suspect")
        parity = seq % 2
    else:
        rep.labels_from = "frame index"
        rep.notes.append("no frame counter (not a nireyecam-record folder): labels assume no dropped frames")
        parity = np.arange(rep.frames) % 2
    dark = moments(load_stack(dark_path, size)[0])[1] if dark_path else None

    if green_roi and red_roi:
        g, r = _roi_means(frames, green_roi, dark), _roi_means(frames, red_roi, dark)
        if np.any(g <= 0) or np.any(r <= 0):
            rep.notes.append("some frames have no signal above dark in a region; check the regions and the dark")
        ratio = np.log(np.clip(g, 1e-9, None) / np.clip(r, 1e-9, None))
        blue, rep.separation = two_means(ratio)      # blue light lights the green half
        miss_even_blue = blue != (parity == 0)
        miss_even_lime = blue != (parity == 1)
        if miss_even_blue.sum() <= miss_even_lime.sum():
            rep.even_frames, miss = "blue", miss_even_blue
        else:
            rep.even_frames, miss = "lime", miss_even_lime
        rep.mislabelled = int(miss.sum())
        rep.mislabelled_frames = [int(i) for i in np.flatnonzero(miss)[:20]]
        if rep.separation < MIN_SEPARATION:
            rep.notes.append(f"the two frame classes are only {rep.separation:.1f} noise units apart: "
                             "is the target lit by both LEDs, and are the regions on the right halves?")
    if leak_roi:
        s = _roi_means(frames, leak_roi, dark)
        even, odd = float(np.median(s[parity == 0])), float(np.median(s[parity == 1]))
        bright, dim = max(even, odd), min(even, odd)
        rep.leakage_bright_parity = "even" if even >= odd else "odd"
        if bright > 0:
            rep.leakage_pct = dim / bright * 100
    return rep


# ------------------------------------------------------------------ reports

def _fmt(v, digits: int = 3, unit: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        text = f"{v:.{digits}g}" if abs(v) < 10 ** digits else f"{v:,.0f}"
    else:
        text = str(v)
    return text + unit


def ptc_markdown(rep: PtcReport) -> str:
    sensors = list(dict.fromkeys(g.sensor for g in rep.groups))
    gains = list(dict.fromkeys(g.gain for g in rep.groups))
    bands = list(dict.fromkeys(g.band for g in rep.groups))

    def cell(sensor, pick, gain=None, band=None):
        vals = [pick(g) for g in rep.groups if g.sensor == sensor and (gain is None or g.gain == gain)
                and (band is None or g.band == band) and pick(g) is not None]
        return float(np.mean(vals)) if vals else None

    rows = []
    for gain in gains:
        sfx = f" at {gain}" if gain else ""
        rows.append((f"Conversion gain{sfx} (e⁻/DN)", [cell(s, lambda g: g.e_per_dn, gain) for s in sensors]))
        rows.append((f"Read noise{sfx} (e⁻)", [cell(s, lambda g: g.read_noise_e, gain) for s in sensors]))
        rows.append((f"Saturation capacity{sfx} (e⁻)", [cell(s, lambda g: g.saturation_e, gain) for s in sensors]))
        rows.append((f"Dynamic range{sfx} (dB)", [cell(s, lambda g: g.dynamic_range_db, gain) for s in sensors]))
    for band in bands:
        name = f"band {band}" if band else "the band"
        rows.append((f"QE through {name}", [cell(s, lambda g: g.qe, band=band) for s in sensors]))
        rows.append((f"Linearity error, {name} (%)",
                     [cell(s, lambda g: g.linearity_error_pct, band=band) for s in sensors]))
        rows.append((f"PRNU, {name} (%)", [cell(s, lambda g: g.prnu_pct, band=band) for s in sensors]))
        rows.append((f"Corner-to-centre response, {name} (lowest corner)",
                     [cell(s, lambda g: g.corner_min, band=band) for s in sensors]))
    for gain in gains:
        sfx = f" at {gain}" if gain else ""
        rows.append((f"DSNU{sfx} (e⁻)", [cell(s, lambda g: g.dsnu_e, gain) for s in sensors]))
        dcs = {d.sensor: d.dark_current_e_s for d in rep.dark if d.gain == gain}
        rows.append((f"Dark current{sfx} (e⁻/s)", [dcs.get(s) for s in sensors]))

    out = ["# Sensor bench test: Part 1, photon transfer", "",
           f"`nireyecam-sensortest` {rep.tool_version}" + (f": `{rep.command}`" if rep.command else ""), "",
           "| | " + " | ".join(sensors) + " |", "|---|" + "---|" * len(sensors)]
    out += ["| " + name + " | " + " | ".join(_fmt(v) for v in vals) + " |" for name, vals in rows]
    out += ["", "## Each sensor, gain and band", ""]
    for g in rep.groups:
        out.append(f"### {g.sensor or '(sensor)'}, gain {g.gain or '—'}, band {g.band or '—'}")
        out.append("")
        out.append(f"- {len(g.points)} exposures, {g.points[0].exposure_ms:g}–{g.points[-1].exposure_ms:g} ms; "
                   f"saturation {'reached' if g.saturation_reached else 'not reached'}")
        out.append(f"- K = {_fmt(g.k_dn_per_e, 4)} DN/e⁻ ({_fmt(g.e_per_dn, 4)} e⁻/DN), read noise "
                   f"{_fmt(g.read_noise_e)} e⁻, saturation {_fmt(g.saturation_e)} e⁻, QE {_fmt(g.qe)}")
        if g.corners:
            out.append("- Corners relative to the centre: "
                       + ", ".join(f"{k} {v:.3f}" for k, v in g.corners.items())
                       + f" (from the {g.nonuniformity_exposure_ms:g} ms capture)")
        out += [f"- Note: {n}" for n in g.notes]
        out.append("")
    if rep.dark:
        out += ["## Dark", ""]
        for d in rep.dark:
            out.append(f"- {d.sensor or '(sensor)'}, gain {d.gain or '—'}: black level "
                       f"{_fmt(d.black_level_dn)} DN, dark current {_fmt(d.dark_current_e_s)} e⁻/s, from "
                       f"{len(d.exposures_ms)} captures, {d.exposures_ms[0]:g}–{d.exposures_ms[-1]:g} ms")
            out += [f"  - Note: {n}" for n in d.notes]
        out.append("")
    return "\n".join(out)


def snr_markdown(rep: SnrReport) -> str:
    out = ["# Sensor bench test: Part 2, SNR per cell", "",
           f"`nireyecam-sensortest` {rep.tool_version}" + (f": `{rep.command}`" if rep.command else ""), "",
           f"Regions: {rep.cell_um:g} µm disks tiling the central {rep.field_um:g} µm of the sample "
           "(or the given centres). SNR is the region's mean over its frame-to-frame noise, per frame; "
           "values are medians over the regions.", "",
           "| Sensor | Channel | Power (mW/mm²) | Regions | Px/region | Signal (DN) | Noise (DN) | SNR | "
           "Signal (e⁻) | Drift (%) | Step (%) | d′ |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep.runs:
        drift = f"{r.drift_pct:+.2f} per {r.drift_per}" if r.drift_pct is not None else "—"
        out.append(f"| {r.sensor} | {r.channel} | {r.power_mw_mm2:g} | {r.regions} | {r.pixels_per_region:.0f} | "
                   f"{_fmt(r.signal_dn)} | {_fmt(r.noise_dn)} | {_fmt(r.snr)} | {_fmt(r.signal_e)} | {drift} | "
                   f"{_fmt(r.step_pct)} | {_fmt(r.dprime)} |")
    if rep.targets:
        out += ["", f"## Power for SNR {rep.target_snr:g} per cell", "",
                "| Sensor | Channel | Power (mW/mm²) |", "|---|---|---|"]
        for t in rep.targets:
            out.append(f"| {t.sensor} | {t.channel} | {_fmt(t.power_mw_mm2) if t.power_mw_mm2 else t.note} |")
    notes = [(r, n) for r in rep.runs for n in r.notes]
    first_minute = [r for r in rep.runs if r.first_minute_pct is not None]
    if notes or first_minute:
        out += ["", "## Notes", ""]
        out += [f"- {r.sensor} {r.channel} {r.power_mw_mm2:g} mW/mm²: LED warm-up over the first minute "
                f"{r.first_minute_pct:+.2f}%" for r in first_minute]
        out += [f"- {r.sensor} {r.channel} {r.power_mw_mm2:g} mW/mm²: {n}" for r, n in notes]
    return "\n".join(out) + "\n"


def alternation_markdown(rep: AlternationReport) -> str:
    out = ["# Sensor bench test: Part 3, alternation", "",
           f"`nireyecam-sensortest` {rep.tool_version}" + (f": `{rep.command}`" if rep.command else ""), "",
           f"- Source: `{rep.source}`, {rep.frames:,} frames; labels from the {rep.labels_from}"]
    if rep.dropped is not None:
        out.append(f"- Dropped frames (gaps in the frame counter): {rep.dropped:,}")
    if rep.mislabelled is not None:
        out.append(f"- Even frames show **{rep.even_frames}** excitation. Frames whose content disagrees with "
                   f"their label: **{rep.mislabelled:,}** of {rep.frames:,}"
                   + (f" (first: {', '.join(map(str, rep.mislabelled_frames))})" if rep.mislabelled_frames else ""))
        out.append(f"- Separation between the two frame classes: {_fmt(rep.separation)} noise units")
    if rep.leakage_pct is not None:
        out.append(f"- Leakage: the dim frames carry **{rep.leakage_pct:.2f}%** of the bright frames' signal "
                   f"(bright: {rep.leakage_bright_parity} frames). This includes the other LED genuinely "
                   "exciting the dye; image the target under each LED alone to separate that from timing leakage.")
    out += [f"- Note: {n}" for n in rep.notes]
    return "\n".join(out) + "\n"


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(o) or math.isinf(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, Path):
        return str(o)
    return o


def to_json(rep) -> str:
    return json.dumps(_jsonable(asdict(rep)), indent=2) + "\n"


# ------------------------------------------------------------------ command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nireyecam-sensortest", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Manifest columns are listed in docs/sensor-bench-test.md, under Analysis.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, default_out):
        p.add_argument("--size", type=parse_size, metavar="WxH", help="frame size of raw .u8/.u16 files")
        p.add_argument("--out", type=Path, default=Path(default_out), help=f"report (default {default_out})")
        p.add_argument("--json", type=Path, help="also write every number as JSON")

    p = sub.add_parser("ptc", help="Part 1: photon transfer, QE, dark current, nonuniformity")
    p.add_argument("manifest", type=Path)
    p.add_argument("--crop", type=float, default=1.0, metavar="F",
                   help="use the central fraction F of the frame for the statistics (default 1)")
    p.add_argument("--corner", type=float, default=0.05, metavar="F",
                   help="corner and centre boxes are F of the shorter side (default 0.05)")
    common(p, "ptc-report.md")

    p = sub.add_parser("snr", help="Part 2: SNR per cell, power for a target SNR, drift, small-signal steps")
    p.add_argument("manifest", type=Path)
    p.add_argument("--cell-um", type=float, default=12.0, help="cell diameter in the sample (default 12)")
    p.add_argument("--field-um", type=float, default=500.0,
                   help="side of the central square of the sample tiled with cells (default 500)")
    p.add_argument("--target-snr", type=float, default=100.0, help="SNR per cell per frame to reach (default 100)")
    common(p, "snr-report.md")

    p = sub.add_parser("alternation", help="Part 3: frame colour labels, dropped frames, leakage")
    p.add_argument("source", type=Path, help="a nireyecam-record camera folder, or a frame stack")
    p.add_argument("--dark", type=Path, help="dark frames to subtract")
    p.add_argument("--green-roi", type=parse_rect, metavar="X0,Y0,X1,Y1", help="the green half of the target")
    p.add_argument("--red-roi", type=parse_rect, metavar="X0,Y0,X1,Y1", help="the red half of the target")
    p.add_argument("--leak-roi", type=parse_rect, metavar="X0,Y0,X1,Y1", help="a one-colour target")
    common(p, "alternation-report.md")

    try:
        args = ap.parse_args(argv)
        command = " ".join(["nireyecam-sensortest"] + (sys.argv[1:] if argv is None else list(argv)))
        if args.cmd == "ptc":
            rep = analyse_ptc(read_manifest(args.manifest), size=args.size, crop=args.crop, corner_frac=args.corner)
            rep.command = command
            text = ptc_markdown(rep)
        elif args.cmd == "snr":
            rep = analyse_snr(read_manifest(args.manifest), size=args.size, cell_um=args.cell_um,
                              field_um=args.field_um, target_snr=args.target_snr)
            rep.command = command
            text = snr_markdown(rep)
        else:
            rep = analyse_alternation(args.source, size=args.size, dark_path=args.dark, green_roi=args.green_roi,
                                      red_roi=args.red_roi, leak_roi=args.leak_roi)
            rep.command = command
            text = alternation_markdown(rep)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    args.out.write_text(text)
    if args.json:
        args.json.write_text(to_json(rep))
    print(text)
    print(f"wrote {args.out}" + (f" and {args.json}" if args.json else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
