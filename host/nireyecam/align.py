"""Put every camera's frames on one timeline using the shared sync pulses.

Each camera timestamps frames and sync edges with its own free-running clock.
The pulse train on the shared line has random intervals, so one camera's
sequence of intervals matches another's at exactly one shift even if they
started recording at different times. From the matched pulses we fit
``t_ref = slope * t_cam + offset`` per camera (offset + crystal drift) and
convert every frame time to seconds on the reference camera's clock.

Usage: ``nireyecam-align <session_dir>`` (run automatically after recording).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

MATCH_TOL_US = 2000.0  # pulses farther apart than this after mapping are not the same pulse
MIN_PULSES = 3


class AlignError(RuntimeError):
    pass


@dataclass
class ClockFit:
    slope: float
    offset_us: float          # t_ref = slope * (t_cam - center_us) + offset_us
    center_us: float
    matched: int
    residual_rms_us: float
    residual_max_us: float
    method: str
    warning: str = ""

    @property
    def drift_ppm(self) -> float:
        return (self.slope - 1.0) * 1e6

    def map(self, t_cam_us) -> np.ndarray:
        return self.slope * (np.asarray(t_cam_us, dtype=np.float64) - self.center_us) + self.offset_us


def _fit(t_cam: np.ndarray, t_ref: np.ndarray, method: str) -> ClockFit:
    # Center first: device times are ~1e10 us, and a raw fit would lose precision.
    center = float(np.mean(t_cam))
    x = t_cam - center
    if len(x) >= 2 and np.ptp(x) > 0:
        slope, offset = np.polyfit(x, t_ref, 1)
    else:
        slope, offset = 1.0, float(np.mean(t_ref - x))
    res = t_ref - (slope * x + offset)
    return ClockFit(float(slope), float(offset), center, len(x),
                    float(np.sqrt(np.mean(res**2))), float(np.max(np.abs(res))), method)


def _best_shift(i_ref: np.ndarray, i_cam: np.ndarray, min_overlap: int):
    """Shift s pairing cam interval j with ref interval j+s that matches best."""
    scores = []
    for s in range(-(len(i_cam) - 1), len(i_ref)):
        j0, j1 = max(0, -s), min(len(i_cam), len(i_ref) - s)
        if j1 - j0 < min_overlap:
            continue
        err = float(np.median(np.abs(i_cam[j0:j1] - i_ref[j0 + s : j1 + s])))
        scores.append((err, s))
    scores.sort()
    return scores


def match_pulses(t_ref, t_cam, tol_us: float = MATCH_TOL_US) -> ClockFit:
    """Fit the cam->ref clock map from two lists of sync edge times (us)."""
    t_ref = np.asarray(t_ref, dtype=np.float64)
    t_cam = np.asarray(t_cam, dtype=np.float64)
    if len(t_ref) < MIN_PULSES or len(t_cam) < MIN_PULSES:
        raise AlignError(
            f"need at least {MIN_PULSES} sync pulses on each camera "
            f"(got {len(t_ref)} and {len(t_cam)}); is the sync line wired and SYNCGEN on?"
        )
    i_ref, i_cam = np.diff(t_ref), np.diff(t_cam)
    warning = ""
    regular = np.std(i_ref) < 0.01 * np.mean(i_ref)
    if regular:
        # A periodic train (e.g. an external 1 Hz TTL) matches at every shift.
        # Assume both cameras saw the same first pulse.
        warning = "sync train is periodic: pulses paired by index, assuming both cameras saw the same first pulse"
        n = min(len(t_ref), len(t_cam))
        fit = _fit(t_cam[:n], t_ref[:n], "index")
    else:
        scores = _best_shift(i_ref, i_cam, min_overlap=min(MIN_PULSES - 1, len(i_cam), len(i_ref)))
        if not scores or scores[0][0] > tol_us:
            raise AlignError("no consistent pulse match between cameras (were they on the same sync line?)")
        if len(scores) > 1 and scores[1][0] < tol_us and scores[1][0] < 4 * scores[0][0] + 50:
            warning = "pulse match is ambiguous; record longer so more pulses overlap"
        s = scores[0][1]
        j0, j1 = max(0, -s), min(len(i_cam), len(i_ref) - s)
        # Seed only from intervals that agree: across a missed edge the shift
        # changes by one, and pairing pulses there would skew the slope.
        j = np.arange(j0, j1)
        good = j[np.abs(i_cam[j] - i_ref[j + s]) < tol_us]
        cam_idx = np.union1d(good, good + 1)
        fit = _fit(t_cam[cam_idx], t_ref[cam_idx + s], "intervals")

    # Refine: map every cam pulse and pair it with the nearest ref pulse. This
    # also recovers pulses beyond a missed edge, which breaks interval runs.
    for _ in range(3):
        mapped = fit.map(t_cam)
        nearest = np.searchsorted(t_ref, mapped).clip(1, len(t_ref) - 1)
        nearest -= (mapped - t_ref[nearest - 1]) < (t_ref[nearest] - mapped)
        ok = np.abs(t_ref[nearest] - mapped) < tol_us
        if ok.sum() < MIN_PULSES:
            break
        fit = _fit(t_cam[ok], t_ref[nearest[ok]], fit.method)
    fit.warning = warning
    return fit


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def align_session(session: Path, reference: str | None = None) -> dict:
    """Write <cam>/frames_aligned.csv and alignment.json; return the report."""
    meta = json.loads((session / "session.json").read_text())
    names = [c["name"] for c in meta["cameras"]]
    ref = reference or meta.get("sync_master") or names[0]
    if ref not in names:
        raise AlignError(f"reference camera {ref!r} not in session ({names})")

    def sync_times(name: str) -> np.ndarray:
        _, rows = _read_csv(session / name / "sync.csv")
        return np.array([float(r[1]) for r in rows])

    _, ref_frames = _read_csv(session / ref / "frames.csv")
    if not ref_frames:
        raise AlignError(f"reference camera {ref!r} recorded no frames")
    t_zero = float(ref_frames[0][2])  # time 0 = the reference camera's first frame

    t_ref_sync = sync_times(ref)
    report: dict = {"reference": ref, "time_zero": "first frame of the reference camera", "cameras": {}}
    for name in names:
        if name == ref:
            fit = ClockFit(1.0, 0.0, 0.0, len(t_ref_sync), 0.0, 0.0, "reference")
        else:
            try:
                fit = match_pulses(t_ref_sync, sync_times(name))
            except AlignError as e:
                report["cameras"][name] = {"error": str(e)}
                continue
        header, rows = _read_csv(session / name / "frames.csv")
        t_dev = np.array([float(r[2]) for r in rows])
        t_s = (fit.map(t_dev) - t_zero) / 1e6 if len(rows) else np.array([])
        with open(session / name / "frames_aligned.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header + ["t_s"])
            for r, t in zip(rows, t_s):
                w.writerow(r + [f"{t:.6f}"])
        entry = asdict(fit)
        entry["drift_ppm"] = fit.drift_ppm
        report["cameras"][name] = entry

    (session / "alignment.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", type=Path)
    ap.add_argument("--reference", help="camera whose clock is the timeline (default: the sync master)")
    args = ap.parse_args(argv)
    try:
        report = align_session(args.session, args.reference)
    except (AlignError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print_report(report)
    return 0 if all("error" not in c for c in report["cameras"].values()) else 1


def print_report(report: dict) -> None:
    print(f"timeline: {report['reference']}'s clock, t=0 at its first frame")
    for name, c in report["cameras"].items():
        if "error" in c:
            print(f"  {name}: NOT ALIGNED - {c['error']}")
            continue
        print(f"  {name}: {c['matched']} pulses, drift {c['drift_ppm']:+.1f} ppm, "
              f"residual rms {c['residual_rms_us']:.1f} us (max {c['residual_max_us']:.1f})")
        if c.get("warning"):
            print(f"    warning: {c['warning']}")


if __name__ == "__main__":
    sys.exit(main())
