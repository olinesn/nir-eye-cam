"""Check that cameras expose at the same moment, by filming one blinking LED.

    nireyecam-record --out runs/sync --cam a=PORT_A --cam b=PORT_B \\
        --trigger 25 --blink 43 --exp 8 --duration 60
    nireyecam-synctest runs/sync

Every camera films the same LED, blinking as a square wave at f. A frame's
LED brightness is how long the LED was lit during that frame's exposure, so
from frame to frame the brightness traces the blink as that camera sampled
it. A sine at f, fitted to that trace a couple of seconds at a time, gives the
phase at which each camera saw the blink; the phase difference between two
cameras, divided by 2 pi f, is how far apart their exposures are. Added to the
timestamps of paired frames, that gives the offset to ~10 us, with no need for
exact timestamps.

When the cameras are closer than one exposure apart, frames that caught an
edge of the blink in both cameras give it finer still: within such a frame the
brightness moves in proportion to where the edge fell, so the difference in
how much of the exposure was lit, times the exposure, is the offset (within
about 1 us of the truth after 30 s on the simulator). This needs equal
exposures, and an LED that does not saturate the sensor.

The offset is between the exposures' centres: with equal exposure settings,
between their starts. Positive means the camera exposes after the reference.

Choosing the rates: f/F (F the frame rate) must not be near a whole or half
number, or every frame catches the LED at the same point of its cycle; and the
exposure must stay well under 1/(4 f), or each frame averages the blink away.
The tool warns about both.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

BLOCK_S = 2.0              # seconds per sine fit
MIN_BLOCKS = 3
MIN_FRAMES_PER_BLOCK = 8
TOLERANCE_US = 100.0       # default pass threshold on the exposure offset
LED_MIN_AMPLITUDE = 2.0    # gray levels: below this the LED is not seen
LED_MIN_COHERENCE = 0.9    # phase agreement between consecutive fits
EDGE_LIT = (0.1, 0.9)      # an "edge frame" is lit for this share of its exposure
CLEAR_LIT = (0.2, 0.8)     # clearly off below, clearly on above
MIN_EDGE_PAIRS = 3         # per fit, to use edge frames rather than the phase
SATURATED = 254            # gray level counted as clipped
SATURATED_SHARE = 0.01     # of frames, before the LED counts as saturating the sensor

PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"


class SyncTestError(Exception):
    pass


@dataclass
class CameraResult:
    name: str
    frames: int
    roi: tuple[int, int, int, int]       # x, y, w, h of the LED region
    amplitude: float                     # the blink's fitted amplitude, gray levels (median over fits)
    coherence: float                     # 1 = the same phase fit after fit; ~0 = no LED
    missed_triggers: int | None          # gaps in the frame times, with a trigger
    exposure_ms: float | None
    led_seen: bool = False
    saturated: bool = False


@dataclass
class PairResult:
    camera: str
    reference: str
    pairs: int                           # frames matched to a reference frame
    unpaired: int                        # reference frames with no partner
    offset_us: float | None              # median exposure offset, camera minus reference
    offset_se_us: float | None           # its standard error
    offset_min_us: float | None
    offset_max_us: float | None
    timestamp_error_us: float | None     # how far the timestamps misstate that offset
    contradictions: int | None           # paired frames: LED clearly on in one, clearly off in the other
    method: str = ""                     # "edges" (frames that caught a blink edge) or "phase"
    edge_pairs: int = 0
    verdict: str = INCONCLUSIVE
    reason: str = ""
    blocks_us: list[float] = field(default_factory=list)


@dataclass
class SyncReport:
    session: str
    frame_hz: float
    frame_hz_source: str
    blink_hz: float
    tolerance_us: float
    cameras: list[CameraResult]
    pairs: list[PairResult]
    warnings: list[str]
    verdict: str


# ------------------------------------------------------------------ reading


def _aligned_times(folder: Path) -> np.ndarray:
    path = folder / "frames_aligned.csv"
    if not path.exists():
        raise SyncTestError(f"{path} is missing: run nireyecam-align on the session first")
    with open(path, newline="") as f:
        return np.array([float(r["t_s"]) for r in csv.DictReader(f)])


def iter_frames(folder: Path, width: int, height: int) -> Iterator[np.ndarray]:
    """The camera's frames in order, from frames.u8 or video.mkv (row i of frames.csv = frame i)."""
    raw = folder / "frames.u8"
    if raw.exists():
        meta = folder / "frames.u8.json"
        if meta.exists():
            m = json.loads(meta.read_text())
            width, height = m["width"], m["height"]
        data = np.memmap(raw, np.uint8, "r")
        n = data.size // (width * height)
        yield from data[: n * width * height].reshape(n, height, width)
        return
    mkv = folder / "video.mkv"
    if not mkv.exists():
        raise SyncTestError(f"{folder}: no video.mkv or frames.u8")
    if not shutil.which("ffmpeg"):
        raise SyncTestError("ffmpeg is needed to read video.mkv")
    size = width * height
    proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", str(mkv), "-f", "rawvideo",
                             "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE)
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(height, width)
    finally:
        proc.stdout.close()
        proc.wait()


def cell_size(width: int, height: int) -> int:
    """Side of the square cells the LED is searched over: ~40 across the shorter side."""
    return max(2, round(min(width, height) / 40))


def cell_signals(frames: Iterator[np.ndarray], cell: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean brightness of every cell in every frame, (frames, rows, cols), and per cell the
    number of frames in which one of its pixels clipped."""
    out, clipped = [], None
    for f in frames:
        gh, gw = f.shape[0] // cell, f.shape[1] // cell
        cells = f[: gh * cell, : gw * cell].reshape(gh, cell, gw, cell)
        out.append(cells.mean(axis=(1, 3), dtype=np.float32))
        hit = cells.max(axis=(1, 3)) >= SATURATED
        clipped = hit.astype(np.int64) if clipped is None else clipped + hit
    if not out:
        return np.zeros((0, 0, 0), np.float32), np.zeros((0, 0), np.int64)
    return np.stack(out), clipped


def roi_signal(frames: Iterator[np.ndarray], roi: tuple[int, int, int, int]) -> tuple[np.ndarray, int]:
    """Mean brightness inside ``roi`` per frame, and the number of frames in which it clipped."""
    x, y, w, h = roi
    means, clipped = [], 0
    for f in frames:
        box = f[y : y + h, x : x + w]
        means.append(box.mean(dtype=np.float64))
        clipped += int(box.max() >= SATURATED)
    return np.array(means), clipped


# ------------------------------------------------------------------ the fit


def fit_blocks(t: np.ndarray, y: np.ndarray, hz: float, edges: np.ndarray) -> np.ndarray:
    """Least-squares DC + sine at ``hz`` in each block of time [edges[i], edges[i+1]).

    ``y`` is (frames,) or (frames, signals). Returns the complex amplitude
    a + ib of a*cos(wt) + b*sin(wt) per block (and per signal); NaN where a
    block has too few frames. Its angle is the phase at which this camera saw
    the blink, its magnitude the blink's amplitude.
    """
    y2 = y.reshape(len(y), -1).astype(np.float64)
    z = np.full((len(edges) - 1, y2.shape[1]), np.nan + 0j)
    for i in range(len(edges) - 1):
        m = (t >= edges[i]) & (t < edges[i + 1])
        if m.sum() < MIN_FRAMES_PER_BLOCK:
            continue
        ph = 2 * np.pi * hz * t[m]
        design = np.column_stack([np.ones(m.sum()), np.cos(ph), np.sin(ph)])
        coef, *_ = np.linalg.lstsq(design, y2[m], rcond=None)
        z[i] = coef[1] + 1j * coef[2]
    return z if y.ndim > 1 else z[:, 0]


def coherence(z: np.ndarray) -> float:
    """How steady the phase is from one fit to the next: ~1 for a visible LED, ~0 for noise."""
    z = z[np.isfinite(z)]
    if len(z) < 2:
        return 0.0
    num = abs(np.sum(z[1:] * np.conj(z[:-1])))
    den = np.sum(np.abs(z[1:]) * np.abs(z[:-1]))
    return float(num / den) if den > 0 else 0.0


def find_led(t: np.ndarray, cells: np.ndarray, hz: float, edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The cells that see the LED, and their mean brightness per frame.

    The cell whose brightness carries the most blink, plus every cell with at
    least half its blink amplitude in the same phase: averaging the whole spot
    rather than one cell is what keeps the edge estimate's noise down.
    """
    flat = cells.reshape(len(cells), -1)
    zb = fit_blocks(t, flat, hz, edges)
    amp = np.nanmedian(np.abs(zb), axis=0)
    best = int(np.nanargmax(amp))
    # Phase of each cell relative to the best one, per fit, then the typical value.
    rel = np.nanmedian(np.abs(np.angle(zb * np.conj(zb[:, best : best + 1]))), axis=0)
    mask = (amp >= 0.5 * amp[best]) & (rel < 0.3)
    mask[best] = True
    return mask, flat[:, mask].mean(axis=1, dtype=np.float64)


def pair_frames(t_ref: np.ndarray, t: np.ndarray, max_dt: float) -> tuple[np.ndarray, np.ndarray]:
    """For each reference frame, the nearest frame of the other camera within ``max_dt``."""
    if len(t) < 2 or not len(t_ref):
        return np.array([], int), np.array([], int)
    j = np.clip(np.searchsorted(t, t_ref), 1, len(t) - 1)
    j = np.where(np.abs(t[j - 1] - t_ref) <= np.abs(t[j] - t_ref), j - 1, j)
    ok = np.abs(t[j] - t_ref) < max_dt
    return np.flatnonzero(ok), j[ok]


def lit_fraction(b: np.ndarray) -> np.ndarray:
    """Brightness rescaled to the share of the exposure the LED was lit: 0 dark, 1 lit throughout."""
    lo, hi = np.percentile(b, [5, 95])
    return (b - lo) / (hi - lo) if hi > lo else np.zeros_like(b)


def wrap(x: float, period: float) -> float:
    return (x + period / 2) % period - period / 2


# ------------------------------------------------------------------ the test


def analyze(session: Path, *, frame_hz: float | None = None, blink_hz: float | None = None,
            reference: str | None = None, rois: dict[str, tuple[int, int, int, int]] | None = None,
            tolerance_us: float = TOLERANCE_US, block_s: float = BLOCK_S) -> SyncReport:
    meta = json.loads((session / "session.json").read_text())
    settings = meta.get("settings", {})
    cams = meta["cameras"]
    if len(cams) < 2:
        raise SyncTestError("the sync test needs at least two cameras")
    names = [c["name"] for c in cams]
    reference = reference or names[0]
    if reference not in names:
        raise SyncTestError(f"--reference {reference!r} is not one of {names}")
    rois = rois or {}
    warnings: list[str] = []

    blink_hz = blink_hz or settings.get("blink")
    if not blink_hz:
        raise SyncTestError("no blink rate: record with --blink HZ, or pass --blink-hz")

    times = {c["name"]: _aligned_times(session / c["name"]) for c in cams}
    for name, t in times.items():
        if len(t) < 2:
            raise SyncTestError(f"{name} recorded {len(t)} frames")
    trigger = settings.get("trigger")
    if frame_hz:
        source = "--frame-hz"
    elif trigger:
        frame_hz, source = float(trigger), "the trigger"
    else:
        frame_hz, source = 1 / float(np.median(np.diff(times[reference]))), "measured"
        warnings.append("this session was recorded without --trigger: the cameras free-run, "
                        "so expect their exposures to drift apart")

    ratio = blink_hz / frame_hz
    eps = ratio % 1
    if min(eps, 1 - eps) < 0.05 or abs(eps - 0.5) < 0.05:
        warnings.append(f"blink/frame rate = {ratio:.3f} is close to a whole or half number, so frames "
                        "catch the LED at nearly the same point of its cycle: pick a blink rate with a "
                        "ratio near x.3 or x.7 (for example 43 Hz at 25 fps)")

    start = max(t[0] for t in times.values())
    end = min(t[-1] for t in times.values())
    n_blocks = int((end - start) // block_s)
    if n_blocks < MIN_BLOCKS:
        raise SyncTestError(f"the cameras overlap for {end - start:.1f} s: record at least "
                            f"{MIN_BLOCKS * block_s + 1:.0f} s")
    edges = start + block_s * np.arange(n_blocks + 1)

    results: dict[str, CameraResult] = {}
    signals: dict[str, np.ndarray] = {}
    fits: dict[str, np.ndarray] = {}
    for c in cams:
        name, dev = c["name"], c.get("device", {})
        w, h = int(dev.get("width", 0)), int(dev.get("height", 0))
        t = times[name]
        folder = session / name
        if name in rois:
            roi = rois[name]
            b, clipped = roi_signal(iter_frames(folder, w, h), roi)
        else:
            cell = cell_size(w, h)
            grid, clipped_cells = cell_signals(iter_frames(folder, w, h), cell)
            n = min(len(grid), len(t))
            mask, b = find_led(t[:n], grid[:n], blink_hz, edges)
            clipped = int(clipped_cells.reshape(-1)[mask].max())
            rows, cols = np.nonzero(mask.reshape(grid.shape[1:]))
            roi = (cols.min() * cell, rows.min() * cell, (cols.max() - cols.min() + 1) * cell,
                   (rows.max() - rows.min() + 1) * cell)
        n = min(len(b), len(t))
        if len(b) != len(t):
            warnings.append(f"{name}: {len(b)} video frames but {len(t)} timestamps; using the first {n}")
        b, times[name] = b[:n], t[:n]
        z = fit_blocks(times[name], b, blink_hz, edges)
        missed = None
        if trigger:
            gaps = np.round(np.diff(times[name]) * frame_hz) - 1
            missed = int(gaps[gaps > 0].sum())
            if missed:
                warnings.append(f"{name}: {missed} missed triggers (gaps in the frame times)")
        exposure_ms = (c.get("timing") or {}).get("exposure_ms")
        if exposure_ms and blink_hz * exposure_ms / 1000 > 0.25:
            warnings.append(f"{name}: exposure {exposure_ms:.2f} ms is long for a {blink_hz:g} Hz blink: keep "
                            f"it under {250 / blink_hz:.2f} ms so frames do not average the blink away")
        r = CameraResult(name=name, frames=n, roi=tuple(int(v) for v in roi),
                         amplitude=float(np.nanmedian(np.abs(z))) if np.isfinite(z).any() else 0.0,
                         coherence=coherence(z), missed_triggers=missed, exposure_ms=exposure_ms)
        r.led_seen = r.amplitude >= LED_MIN_AMPLITUDE and r.coherence >= LED_MIN_COHERENCE
        r.saturated = clipped > SATURATED_SHARE * n
        if r.saturated:
            warnings.append(f"{name}: the LED saturates the sensor; dim it or shorten the exposure, "
                            "or the fine offset is biased")
        results[name], signals[name], fits[name] = r, b, z

    pairs = [_compare(name, reference, times, signals, fits, results, edges, blink_hz, frame_hz, tolerance_us)
             for name in names if name != reference]
    order = {PASS: 0, INCONCLUSIVE: 1, FAIL: 2}
    verdict = max((p.verdict for p in pairs), key=order.__getitem__)
    return SyncReport(session=str(session), frame_hz=frame_hz, frame_hz_source=source, blink_hz=blink_hz,
                      tolerance_us=tolerance_us, cameras=list(results.values()), pairs=pairs,
                      warnings=warnings, verdict=verdict)


def _compare(name, reference, times, signals, fits, results, edges, blink_hz, frame_hz, tolerance_us) -> PairResult:
    t_ref, t = times[reference], times[name]
    i_ref, i = pair_frames(t_ref, t, 0.5 / frame_hz)
    p = PairResult(camera=name, reference=reference, pairs=len(i), unpaired=len(t_ref) - len(i),
                   offset_us=None, offset_se_us=None, offset_min_us=None, offset_max_us=None,
                   timestamp_error_us=None, contradictions=None)
    if not len(i):
        p.reason = "no frames close enough in time to pair: is the session aligned?"
        return p
    lit_ref, lit = lit_fraction(signals[reference])[i_ref], lit_fraction(signals[name])[i]
    off, on = CLEAR_LIT
    p.contradictions = int(np.sum(((lit_ref < off) & (lit > on)) | ((lit_ref > on) & (lit < off))))
    for cam in (reference, name):
        if not results[cam].led_seen:
            p.reason = (f"{cam} does not see the blinking LED (amplitude {results[cam].amplitude:.1f} gray "
                        f"levels, coherence {results[cam].coherence:.2f}): check it is in view, lit, and that "
                        "the exposure is short")
            return p

    period = 1 / blink_hz
    dt = t[i] - t_ref[i_ref]
    block = np.searchsorted(edges, t_ref[i_ref], side="right") - 1
    used, coarse, mean_dt = [], [], []
    for k in range(len(edges) - 1):
        zr, zc = fits[reference][k], fits[name][k]
        sel = block == k
        if not (np.isfinite(zr) and np.isfinite(zc) and sel.any()):
            continue
        # Each camera sees the LED's phase at (its timestamp + its timestamp-to-exposure delay);
        # the phase difference is the difference of those delays.
        delay = wrap(float(np.angle(zr * np.conj(zc))) / (2 * np.pi * blink_hz), period)
        used.append(k)
        coarse.append(float(np.mean(dt[sel])) + delay)
        mean_dt.append(float(np.mean(dt[sel])))
    if not used:
        p.reason = "no time block holds fits from both cameras"
        return p
    offsets = coarse
    p.method = "phase"

    # Finer, from frames that caught an edge of the blink in both cameras. On a rising edge a
    # later exposure is lit for longer: lit share difference x exposure = offset (negated on a
    # falling edge). Which edge it was comes from the reference's fitted sine at that frame.
    e_ref, e = results[reference].exposure_ms, results[name].exposure_ms
    if (e_ref and e and abs(e - e_ref) <= 0.01 * e_ref and abs(np.median(coarse)) < e_ref / 2000
            and not (results[reference].saturated or results[name].saturated)):
        lo, hi = EDGE_LIT
        both = (lit_ref > lo) & (lit_ref < hi) & (lit > lo) & (lit < hi)
        psi = 2 * np.pi * blink_hz * t_ref[i_ref] - np.angle(fits[reference][np.clip(block, 0, len(edges) - 2)])
        d = np.where(np.sin(psi) < 0, 1.0, -1.0) * (e_ref / 1000) * (lit - lit_ref)
        counts = [int((both & (block == k)).sum()) for k in used]
        if min(counts) >= MIN_EDGE_PAIRS:
            offsets = [float(np.median(d[both & (block == k)])) for k in used]
            p.method, p.edge_pairs = "edges", sum(counts)
            all_d = d[both & np.isin(block, used)]
            p.offset_se_us = float(1.2533 * np.std(all_d, ddof=1) / np.sqrt(len(all_d)) * 1e6)
    ts_errors = np.array(offsets) - np.array(mean_dt)
    us = np.array(offsets) * 1e6
    if p.offset_se_us is None and len(us) > 1:
        p.offset_se_us = float(1.2533 * np.std(us, ddof=1) / np.sqrt(len(us)))
    p.blocks_us = [round(float(v), 2) for v in us]
    p.offset_us, p.offset_min_us, p.offset_max_us = float(np.median(us)), float(us.min()), float(us.max())
    p.timestamp_error_us = float(np.median(ts_errors) * 1e6)
    spread = p.offset_max_us - p.offset_min_us
    if np.max(np.abs(us)) <= tolerance_us:
        p.verdict, p.reason = PASS, f"exposures within {tolerance_us:g} us of the reference's throughout"
    elif spread > tolerance_us:
        span = edges[-1] - edges[0]
        p.verdict, p.reason = FAIL, (f"the offset drifts by {spread:.0f} us over {span:.0f} s: the cameras are "
                                     "not exposing on a common trigger")
    else:
        p.verdict, p.reason = FAIL, (f"a steady offset of {p.offset_us:+.0f} us: check that the trigger reaches "
                                     "both sensors and that their exposure settings match")
    return p


# ------------------------------------------------------------------ output


def format_report(r: SyncReport) -> str:
    lines = [f"Sync test: {r.session}",
             f"  frame rate {r.frame_hz:g} Hz ({r.frame_hz_source}), blink {r.blink_hz:g} Hz "
             f"(ratio {r.blink_hz / r.frame_hz:.3f}), tolerance {r.tolerance_us:g} us"]
    for c in r.cameras:
        x, y, w, h = c.roi
        missed = f", {c.missed_triggers} missed triggers" if c.missed_triggers is not None else ""
        seen = "" if c.led_seen else "  <- LED not seen"
        lines.append(f"  {c.name}: {c.frames} frames, LED at x={x} y={y} ({w}x{h}), blink amplitude "
                     f"{c.amplitude:.1f} gray levels, coherence {c.coherence:.2f}{missed}{seen}")
    for p in r.pairs:
        lines.append(f"  {p.camera} vs {p.reference}: {p.verdict}")
        if p.offset_us is not None:
            how = (f"{p.edge_pairs} frame pairs that caught a blink edge" if p.method == "edges"
                   else "the blink's phase")
            se = f" +/- {p.offset_se_us:.1f}" if p.offset_se_us is not None else ""
            lines.append(f"    exposure offset {p.offset_us:+.1f}{se} us (from {p.offset_min_us:+.1f} to "
                         f"{p.offset_max_us:+.1f} us over {len(p.blocks_us)} fits of {BLOCK_S:g} s; "
                         f"from {how})")
            lines.append(f"    the timestamps misstate it by {p.timestamp_error_us:+.1f} us")
        if p.contradictions is not None:
            lines.append(f"    LED clearly on in one camera and off in the other in {p.contradictions} of "
                         f"{p.pairs} frame pairs" + (f" ({p.unpaired} reference frames unpaired)" if p.unpaired else ""))
        lines.append(f"    {p.reason}")
    for w in r.warnings:
        lines.append(f"  warning: {w}")
    lines.append(f"Verdict: {r.verdict}")
    return "\n".join(lines)


def _roi_arg(text: str) -> tuple[str, tuple[int, int, int, int]]:
    name, sep, box = text.partition("=")
    try:
        x, y, w, h = (int(v) for v in box.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("expected NAME=X,Y,W,H") from None
    if not sep or w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("expected NAME=X,Y,W,H")
    return name, (x, y, w, h)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("session", type=Path, help="session folder from nireyecam-record")
    ap.add_argument("--blink-hz", type=float, help="LED blink rate (default: --blink from session.json)")
    ap.add_argument("--frame-hz", type=float,
                    help="frame rate (default: --trigger from session.json, else measured)")
    ap.add_argument("--reference", help="camera the others are compared with (default: the first)")
    ap.add_argument("--roi", action="append", type=_roi_arg, default=[], metavar="NAME=X,Y,W,H",
                    help="where the LED is in that camera's image (default: found automatically)")
    ap.add_argument("--tolerance-us", type=float, default=TOLERANCE_US,
                    help=f"largest exposure offset that passes (default {TOLERANCE_US:g})")
    args = ap.parse_args(argv)
    try:
        report = analyze(args.session, frame_hz=args.frame_hz, blink_hz=args.blink_hz,
                         reference=args.reference, rois=dict(args.roi), tolerance_us=args.tolerance_us)
    except (SyncTestError, OSError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(format_report(report))
    (args.session / "synctest.json").write_text(json.dumps(asdict(report), indent=2))
    return 0 if report.verdict == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
