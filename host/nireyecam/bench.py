"""Measure one camera on the bench and draft a "Bench test result" issue.

    nireyecam-bench --cam /dev/cu.usbmodem1101 --led 300 --sync

Aim the camera at a still, evenly lit scene (white paper under the IR LEDs
works) and leave it alone until the run ends: the brightness tests compare
frames taken seconds apart. One run measures:

- identity: board, firmware, sensor model and revision, CSI error count;
- streaming, at each resolution: once at the default frame length, and once
  at the shortest, which shows what the USB link carries when the sensor
  outruns it. Frame rate and timing jitter come from the camera's own
  timestamps, dropped frames from gaps in its frame counter;
- exposure: mean brightness at several exposures, fitted to a line (the
  intercept is the black level);
- analog gain: brightness ratios against the expected 1x / 2x / 4x / 8x;
- --led N: brightness with the IR LEDs off and on;
- --sync: the camera drives its sync output, wired back to its own sync
  input, and the pulses must return 0.4-1.0 s apart;
- --extsync HZ (ESP32-P4 only): TRIG_OUT wired to the sensor's GPIO0; with
  EXTSYNC on, frames must arrive exactly 1/HZ apart with none missing.

The camera's settings are restored at the end, and the IR LEDs left off,
also after an error or Ctrl-C. Try it without hardware: --cam sim.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import __version__, protocol
from .device import Camera, CameraError, open_camera

# Resolutions to stream, and AGAIN codes with the gain each should give: the
# HM01B0's index i is 2**i, the VD55G1's code c is 32 / (32 - c).
PROFILES = {
    "rp2040": {
        "resolutions": ["160x120", "320x240", "320x320"],
        "gains": [(0, 1.0), (1, 2.0), (2, 4.0), (3, 8.0)],
    },
    "esp32p4": {
        "resolutions": ["320x320", "640x480", "800x704"],
        "gains": [(0, 1.0), (16, 2.0), (24, 4.0), (28, 8.0)],
    },
}
VD55G1_MODEL_ID = 0x53354731
VD55G1_CUT3 = 0x3030

# Options of .github/ISSUE_TEMPLATE/bench_test.yml, word for word.
CONFIRMS = "Confirms what the code / docs assume"
DIFFERS = "Differs from what the code / docs assume"
INCONCLUSIVE = "Inconclusive"
OTHER = "Other (describe below)"
ITEM_RP_FPS = "RP2040: frame rate (depends on the Arducam module's oscillator)"
ITEM_RP_USB = "RP2040: USB throughput / dropped frames"
ITEM_RP_GAIN = "RP2040: analog gain register bits (0x0205[6:4])"
ITEM_RP_VSYNC = "RP2040: VSYNC timestamps while the PIO reads the same pin"
ITEM_HM_NIR = "HM01B0: NIR (850 nm) response through the stock lens"
ITEM_P4_BOOT = "ESP32-P4: sensor boots (I2C 0x10, model ID, CLKIN frequency)"
ITEM_P4_CSI = "ESP32-P4: RAW8 capture over CSI (csi_errors stays 0)"
ITEM_P4_FSYNC = "ESP32-P4: FSYNC_OUT timestamp per frame (capture.c VERIFY)"
ITEM_P4_USB = "ESP32-P4: USB high-speed throughput"
ITEM_P4_EXTSYNC = "ESP32-P4: external sync on GPIO0 (EXTSYNC / UM3224)"
ITEM_P4_DEVICE = "ESP32-P4: board's high-speed port works as a USB device"

SETTLE_FRAMES = 3         # frames ignored after each START, while settings take effect
CLIP_LEVEL = 250          # 8-bit level counted as clipped
GAIN_TOP_LEVEL = 160      # mean level aimed for at the highest gain
GAIN_TOLERANCE = 0.15     # a measured gain ratio may be off by this fraction
LED_MIN_RATIO = 1.5       # LEDs on must be at least this much brighter than off
JITTER_LIMIT = 0.02       # frame-period std, as a fraction of the period
PERIOD_TOLERANCE = 0.01   # measured vs expected frame period (FSYNC, EXTSYNC)
FULL_SPEED_MB_S = 1.25    # bulk data above this cannot cross a USB full-speed link


def profile_for(info: dict) -> str:
    return "esp32p4" if info.get("board") == "esp32p4" else "rp2040"


# ------------------------------------------------------------------ results

@dataclass
class StreamResult:
    resolution: str
    mode: str                                # "default" or "fastest" frame length, or "extsync"
    frame_length: int = 0
    expected_period_us: float | None = None  # what the firmware or trigger says it should be
    frames: int = 0
    dropped: int = 0
    period_us: float = 0.0
    period_std_us: float = 0.0
    period_outliers: int = 0
    sensor_fps: float = 0.0
    delivered_fps: float = 0.0
    delivered_mb_s: float = 0.0
    needed_mb_s: float = 0.0
    error: str | None = None


@dataclass
class LinearityResult:
    exposures: list = field(default_factory=list)
    means: list = field(default_factory=list)
    clipped: list = field(default_factory=list)
    slope: float = 0.0
    black_level: float = 0.0
    r2: float = 0.0
    error: str | None = None


@dataclass
class GainResult:
    codes: list = field(default_factory=list)
    expected: list = field(default_factory=list)
    exposure: int = 0
    means: list = field(default_factory=list)
    clipped: list = field(default_factory=list)
    measured: list = field(default_factory=list)
    worst_error: float = 0.0
    error: str | None = None


@dataclass
class LedResult:
    level: int = 0
    exposure: int = 0
    mean_off: float = 0.0
    mean_on: float = 0.0
    ratio: float | None = None
    error: str | None = None


@dataclass
class SyncResult:
    pulses: int = 0
    missing: int = 0           # gaps in the pulse counter
    intervals_s: list = field(default_factory=list)
    error: str | None = None


@dataclass
class ExtSyncResult:
    hz: int = 0
    stream: StreamResult | None = None
    error: str | None = None


@dataclass
class BenchReport:
    tool_version: str
    command: str = ""
    started: str = ""
    profile: str = ""
    info_before: dict = field(default_factory=dict)
    info_after: dict = field(default_factory=dict)
    streams: list = field(default_factory=list)
    linearity: LinearityResult | None = None
    gain: GainResult | None = None
    led: LedResult | None = None
    sync: SyncResult | None = None
    extsync: ExtSyncResult | None = None
    external_sync_pulses: int = 0   # pulses seen while this tool was not driving SYNC
    duration_s: float = 0.0
    error: str | None = None


def frame_stats(frames: list, r: StreamResult) -> StreamResult:
    """Fill ``r`` from [(Packet, host_ns)] frames, using the camera's own timestamps."""
    r.frames = len(frames)
    if len(frames) < 3:
        r.error = f"only {len(frames)} frames arrived"
        return r
    seq = np.array([p.seq for p, _ in frames], dtype=np.int64)
    t = np.array([p.t_us for p, _ in frames], dtype=np.float64)
    steps = np.diff(seq)
    ok = steps > 0
    if not ok.any():
        r.error = "frame counter never advanced"
        return r
    per = np.diff(t)[ok] / steps[ok]  # a gap of k frames spans k periods
    r.dropped = int(np.sum(steps[ok] - 1))
    r.period_us = float(np.median(per))
    r.period_std_us = float(np.std(per))
    r.period_outliers = int(np.sum(np.abs(per - r.period_us) > 0.05 * r.period_us))
    nbytes = float(np.mean([len(p.payload) for p, _ in frames]))
    span_s = (t[-1] - t[0]) / 1e6
    if r.period_us > 0:
        r.sensor_fps = 1e6 / r.period_us
    if span_s > 0:
        r.delivered_fps = (len(frames) - 1) / span_s
    r.delivered_mb_s = r.delivered_fps * nbytes / 1e6
    r.needed_mb_s = r.sensor_fps * nbytes / 1e6
    return r


# ------------------------------------------------------------------ measuring

def _drain(cam: Camera) -> list:
    out = []
    while True:
        try:
            out.append(cam.data.get_nowait())
        except queue.Empty:
            return out


class _Bench:
    def __init__(self, cam: Camera) -> None:
        self.cam = cam
        self.sync_pulses_seen = 0

    def capture(self, seconds: float, drain: bool = True) -> tuple[list, list]:
        """Stream for ``seconds``; return the frames and sync events as [(Packet, host_ns)]."""
        if drain:
            _drain(self.cam)
        self.cam.command("START")
        frames, syncs, skipped = [], [], 0
        t_end = time.monotonic() + seconds
        try:
            while time.monotonic() < t_end:
                try:
                    p, host_ns = self.cam.data.get(timeout=0.1)
                except queue.Empty:
                    continue
                if p.type == protocol.SYNC:
                    syncs.append((p, host_ns))
                elif p.type == protocol.FRAME:
                    if skipped < SETTLE_FRAMES:
                        skipped += 1
                    else:
                        frames.append((p, host_ns))
        finally:
            self.cam.command("STOP")
            time.sleep(0.1)
            syncs += [(p, h) for p, h in _drain(self.cam) if p.type == protocol.SYNC]
        self.sync_pulses_seen += len(syncs)
        return frames, syncs

    def brightness(self, seconds: float) -> tuple[float, bool]:
        """Mean level over the capture, and whether more than 1% of pixels clipped."""
        frames, _ = self.capture(seconds)
        if not frames:
            raise CameraError("no frames arrived")
        imgs = [p.image() for p, _ in frames]
        mean = float(np.mean([img.mean() for img in imgs]))
        clipped = float(np.mean([(img >= CLIP_LEVEL).mean() for img in imgs]))
        return mean, clipped > 0.01

    def set_exposure(self, lines: int) -> int:
        return int(self.cam.command(f"EXP {lines}").split()[0])

    def set_frame_length(self, lines: int) -> int:
        return int(self.cam.command(f"FLL {lines}").split()[0])  # "OK FLL <fll> EXP <exp>"

    # -------------------------------------------------------------- tests

    def stream(self, res: str, seconds: float) -> list[StreamResult]:
        try:
            self.cam.command(f"RES {res}")
            info = self.cam.info()
        except CameraError as e:
            return [StreamResult(res, "default", error=str(e))]
        default_fll = int(info.get("frame_length") or 0)
        out = [self._pass(res, "default", default_fll, info, seconds)]
        try:
            fastest = self.set_frame_length(0)  # clamped to the shortest the sensor allows
            if fastest < default_fll:
                out.append(self._pass(res, "fastest", fastest, self.cam.info(), seconds))
        except CameraError as e:
            out.append(StreamResult(res, "fastest", error=str(e)))
        finally:
            try:
                self.set_frame_length(default_fll)
            except CameraError:
                pass
        return out

    def _pass(self, res: str, mode: str, fll: int, info: dict, seconds: float) -> StreamResult:
        r = StreamResult(res, mode, frame_length=fll,
                         expected_period_us=float(info["frame_period_us"]) if info.get("frame_period_us") else None)
        try:
            frames, _ = self.capture(seconds)
        except CameraError as e:
            r.error = str(e)
            return r
        return frame_stats(frames, r)

    def linearity(self, frame_length: int, seconds: float) -> LinearityResult:
        r = LinearityResult()
        max_exp = max(2, frame_length - 2)
        points: dict[int, tuple[float, bool]] = {}

        def measure(fractions):
            for f in fractions:
                applied = self.set_exposure(max(2, int(f * max_exp)))
                if applied not in points:
                    points[applied] = self.brightness(seconds)

        try:
            measure((0.1, 0.2, 0.4, 0.8))
            if sum(not c for _, c in points.values()) < 3:
                measure((0.05, 0.025))  # too bright: go lower
        except CameraError as e:
            r.error = str(e)
            return r
        for e in sorted(points):
            r.exposures.append(e)
            r.means.append(points[e][0])
            r.clipped.append(points[e][1])
        x = np.array([e for e, c in zip(r.exposures, r.clipped) if not c], dtype=float)
        y = np.array([m for m, c in zip(r.means, r.clipped) if not c], dtype=float)
        if len(x) < 3:
            r.error = "fewer than three exposures without clipping: dim the scene or the LEDs"
            return r
        slope, intercept = np.polyfit(x, y, 1)
        resid = y - (slope * x + intercept)
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r.slope, r.black_level = float(slope), float(intercept)
        r.r2 = 1.0 - float(np.sum(resid**2)) / ss_tot if ss_tot > 0 else 0.0
        if slope <= 0:
            r.error = "brightness does not rise with exposure: is the scene lit?"
        return r

    def gain(self, gains, lin: LinearityResult, frame_length: int, seconds: float) -> GainResult:
        r = GainResult(codes=[c for c, _ in gains], expected=[g for _, g in gains])
        if lin.error or lin.slope <= 0:
            r.error = "skipped: needs the exposure fit"
            return r
        # Pick the exposure whose 1x level, times the top gain, lands near GAIN_TOP_LEVEL.
        want = (GAIN_TOP_LEVEL - lin.black_level) / max(r.expected) / lin.slope
        try:
            r.exposure = self.set_exposure(int(min(max(round(want), 2), max(2, frame_length - 2))))
            for code, _ in gains:
                self.cam.command(f"AGAIN {code}")
                mean, clipped = self.brightness(seconds)
                r.means.append(mean)
                r.clipped.append(clipped)
        except CameraError as e:
            r.error = str(e)
            return r
        finally:
            try:
                self.cam.command(f"AGAIN {gains[0][0]}")
            except CameraError:
                pass
        base = r.means[0] - lin.black_level
        if base <= 2:
            r.error = "too dark at the lowest gain to measure ratios: add light or use --led"
            return r
        r.measured = [(m - lin.black_level) / base for m in r.means]
        usable = [(m, e) for m, e, c in zip(r.measured, r.expected, r.clipped) if not c]
        if len(usable) < 2:
            r.error = "clipped at every gain above 1x: dim the scene or the LEDs"
            return r
        r.worst_error = max(abs(m / e - 1) for m, e in usable)
        return r

    def led(self, level: int, seconds: float) -> LedResult:
        r = LedResult(level=level)
        try:
            self.cam.command("LED 0")
            r.mean_off, _ = self.brightness(seconds)
            self.cam.command(f"LED {level}")
            r.mean_on, _ = self.brightness(seconds)
        except CameraError as e:
            r.error = str(e)
            return r
        r.ratio = r.mean_on / r.mean_off if r.mean_off > 0 else None
        return r

    def sync(self, seconds: float) -> SyncResult:
        r = SyncResult()
        _drain(self.cam)
        try:
            self.cam.command("SYNCGEN 1")
            _, syncs = self.capture(seconds, drain=False)  # the first pulse follows SYNCGEN at once
        except CameraError as e:
            r.error = str(e)
            return r
        finally:
            try:
                self.cam.command("SYNCGEN 0")
            except CameraError:
                pass
        syncs.sort(key=lambda s: s[0].t_us)
        r.pulses = len(syncs)
        seqs = [p.seq for p, _ in syncs]
        r.missing = int(sum(max(b - a - 1, 0) for a, b in zip(seqs, seqs[1:])))
        t = [p.t_us for p, _ in syncs]
        r.intervals_s = [round((b - a) / 1e6, 4) for a, b in zip(t, t[1:])]
        if not r.pulses:
            r.error = "no pulses came back: is the sync output wired to the sync input?"
        return r

    def extsync(self, res: str, hz: int, seconds: float) -> ExtSyncResult:
        r = ExtSyncResult(hz=hz)
        period = 1e6 / hz
        try:
            self.cam.command(f"RES {res}")
            fll = self.set_frame_length(0)  # the sensor's own frame must be shorter than the trigger's
            own = float(self.cam.info().get("frame_period_us") or 0)
            if own >= period:
                r.error = f"the sensor's shortest frame ({own:.0f} us) is not shorter than 1/{hz} s: use a lower rate"
                return r
            self.cam.command(f"TRIG {hz}")
            self.cam.command("EXTSYNC 1")
            frames, _ = self.capture(seconds)
        except CameraError as e:
            r.error = str(e)
            return r
        finally:
            # STOP before the trigger goes away (firmware-p4/README.md, bring-up step 5).
            for cmd in ("STOP", "TRIG 0", "EXTSYNC 0"):
                try:
                    self.cam.command(cmd)
                except CameraError:
                    pass
        r.stream = frame_stats(frames, StreamResult(res, "extsync", frame_length=fll, expected_period_us=period))
        return r


def run_bench(cam: Camera, *, resolutions: list[str] | None = None, seconds: float = 4.0,
              sweep_seconds: float = 0.8, led: int | None = None, sync: bool = False,
              sync_seconds: float = 8.0, extsync_hz: int | None = None, command: str = "",
              log=print) -> BenchReport:
    """Run every measurement on ``cam`` and return the report.

    The camera's settings are put back afterwards and the IR LEDs left off.
    """
    t0 = time.monotonic()
    report = BenchReport(tool_version=__version__, command=command,
                         started=time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    cam.command("STOP")
    info = report.info_before = report.info_after = cam.info()
    prof = report.profile = profile_for(info)
    if not info.get("cam_ok"):
        report.error = f"sensor not ready: {info.get('cam_error') or 'no reason given'}"
        return report
    if extsync_hz and prof != "esp32p4":
        raise CameraError("--extsync needs the ESP32-P4 firmware (TRIG and EXTSYNC)")
    resolutions = resolutions or PROFILES[prof]["resolutions"]
    bench = _Bench(cam)
    restore = ["STOP", "LED 0", "SYNCGEN 0"]
    if prof == "esp32p4":
        restore += ["TRIG 0", "EXTSYNC 0"]
    restore += [f"RES {info.get('width')}x{info.get('height')}", f"FLL {info.get('frame_length')}",
                f"EXP {info.get('exposure')}", f"AGAIN {info.get('again')}", f"AE {info.get('ae')}"]
    try:
        cam.command("AE 0")
        cam.command("LED 0")
        for res in resolutions:
            log(f"streaming {res} ...")
            report.streams += bench.stream(res, seconds)

        # Brightness, at the first resolution's default frame length.
        cam.command(f"RES {resolutions[0]}")
        fll = int(cam.info().get("frame_length") or 0)
        if led:
            cam.command(f"LED {led}")
        log("exposure sweep ...")
        report.linearity = bench.linearity(fll, sweep_seconds)
        log("gain sweep ...")
        report.gain = bench.gain(PROFILES[prof]["gains"], report.linearity, fll, sweep_seconds)
        if led:
            log("IR LEDs off and on ...")
            lin = report.linearity
            if lin.exposures:
                bench.set_exposure(lin.exposures[len(lin.exposures) // 2])
            report.led = bench.led(led, sweep_seconds)
            report.led.exposure = int(cam.info().get("exposure") or 0)
        external = bench.sync_pulses_seen
        if sync:
            log("sync loopback ...")
            report.sync = bench.sync(sync_seconds)
        if extsync_hz:
            log(f"external sync at {extsync_hz} Hz ...")
            report.extsync = bench.extsync(resolutions[0], extsync_hz, seconds)
        report.external_sync_pulses = external
    finally:
        for cmd in restore:
            try:
                cam.command(cmd)
            except CameraError:
                pass
        try:
            report.info_after = cam.info()
        except CameraError:
            pass
        report.duration_s = round(time.monotonic() - t0, 1)
    return report


# ------------------------------------------------------------------ verdicts

def _int(v) -> int | None:
    try:
        return int(str(v), 0)
    except ValueError:
        return None


def _timing_ok(s: StreamResult) -> bool:
    if s.period_outliers or (s.period_us and s.period_std_us / s.period_us > JITTER_LIMIT):
        return False
    return not s.expected_period_us or abs(s.period_us / s.expected_period_us - 1) <= PERIOD_TOLERANCE


def _timing_text(s: StreamResult) -> str:
    want = f" (expected {s.expected_period_us:.0f})" if s.expected_period_us else ""
    return (f"{s.resolution}: period {s.period_us:.0f} us{want}, std {s.period_std_us:.1f} us, "
            f"{s.period_outliers} outliers")


def _rate_text(s: StreamResult) -> str:
    return (f"{s.resolution} {s.mode}: sensor {s.sensor_fps:.1f} fps, delivered {s.delivered_fps:.1f} fps "
            f"= {s.delivered_mb_s:.2f} MB/s, {s.dropped} dropped")


def verdicts(r: BenchReport) -> list[tuple[str, str | None, str]]:
    """(form item, suggested result or None, why) for each item this run covers.

    None means the run measured the item but there is no pass/fail line to
    draw: the numbers are the result.
    """
    out: list[tuple[str, str | None, str]] = []
    p4 = r.profile == "esp32p4"
    info = r.info_before
    if p4:
        if info.get("cam_ok"):
            model, rev = info.get("model_id", "?"), info.get("revision", "?")
            note = "" if _int(rev) == VD55G1_CUT3 else " (not cut 3, which may need ST's patch)"
            out.append((ITEM_P4_BOOT, CONFIRMS if _int(model) == VD55G1_MODEL_ID else DIFFERS,
                        f"model_id {model}, revision {rev}{note}"))
        else:
            out.append((ITEM_P4_BOOT, DIFFERS, f"cam_error {info.get('cam_error')!r}"))
    if r.error:
        return out

    default = [s for s in r.streams if s.mode == "default" and not s.error]
    good = [s for s in r.streams if not s.error]
    failed = [s for s in r.streams if s.error]
    rates = "; ".join(_rate_text(s) for s in good) or "no stream succeeded"
    if failed:
        rates += "; failed: " + "; ".join(f"{s.resolution} {s.mode}: {s.error}" for s in failed)
    top = max((s.delivered_mb_s for s in good), default=0.0)

    if p4:
        before, after = info.get("csi_errors"), r.info_after.get("csi_errors")
        if good and before is not None and after is not None:
            out.append((ITEM_P4_CSI, CONFIRMS if after == before else DIFFERS,
                        f"csi_errors {before} -> {after} over {len(good)} streaming passes"))
        if default:
            out.append((ITEM_P4_FSYNC, CONFIRMS if all(map(_timing_ok, default)) else DIFFERS,
                        "; ".join(map(_timing_text, default))))
        out.append((ITEM_P4_USB, None, rates))
        if good:
            out.append((ITEM_P4_DEVICE, CONFIRMS if top > FULL_SPEED_MB_S else INCONCLUSIVE,
                        f"up to {top:.2f} MB/s, " + ("more than USB full speed can carry, so the link is high speed"
                                                      if top > FULL_SPEED_MB_S else
                                                      "which full speed could also carry: run a larger resolution")))
    else:
        out.append((ITEM_RP_FPS, None, "; ".join(f"{s.resolution} {s.mode}: {s.sensor_fps:.2f} fps" for s in good)
                    or "no stream succeeded"))
        out.append((ITEM_RP_USB, None, rates + " (docs/hardware.md expects about 1 MB/s)"))
        if default:
            out.append((ITEM_RP_VSYNC, CONFIRMS if all(map(_timing_ok, default)) else DIFFERS,
                        "; ".join(map(_timing_text, default))))

    g = r.gain
    if g:
        item, prefix = (OTHER, "analog gain codes " + "/".join(map(str, g.codes)) + ": ") if p4 else (ITEM_RP_GAIN, "")
        if g.error or not g.measured:
            out.append((item, INCONCLUSIVE, prefix + (g.error or "no measurement")))
        else:
            clip = " (clipped steps not judged)" if any(g.clipped) else ""
            out.append((item, CONFIRMS if g.worst_error <= GAIN_TOLERANCE else DIFFERS,
                        prefix + "ratios " + ", ".join(f"{m:.2f}" for m in g.measured)
                        + " vs " + ", ".join(f"{e:g}" for e in g.expected) + clip))
    if r.led:
        item, prefix = (OTHER, "IR LEDs through the sensor's STROBE: ") if p4 else (ITEM_HM_NIR, "")
        if r.led.error or r.led.ratio is None:
            out.append((item, INCONCLUSIVE, prefix + (r.led.error or "LEDs-off image was black")))
        else:
            out.append((item, CONFIRMS if r.led.ratio >= LED_MIN_RATIO else DIFFERS,
                        prefix + f"mean level {r.led.mean_off:.1f} off, {r.led.mean_on:.1f} on "
                        f"({r.led.ratio:.1f}x) at {r.led.exposure} lines"))
    if r.sync:
        s = r.sync
        iv = s.intervals_s
        ok = not s.error and s.pulses >= 3 and not s.missing and all(0.39 <= x <= 1.01 for x in iv)
        why = s.error or (f"{s.pulses} pulses, {s.missing} missing"
                          + (f", {min(iv):.3f}-{max(iv):.3f} s apart" if iv else ""))
        out.append((OTHER, CONFIRMS if ok else DIFFERS, "sync loopback (SYNCGEN out to the sync input): " + why))
    if r.extsync:
        e = r.extsync
        s = e.stream
        if e.error or s is None or s.error:
            out.append((ITEM_P4_EXTSYNC, INCONCLUSIVE, e.error or (s.error if s else "no frames")))
        else:
            ok = _timing_ok(s) and s.dropped == 0
            out.append((ITEM_P4_EXTSYNC, CONFIRMS if ok else DIFFERS,
                        f"TRIG {e.hz} Hz: {_timing_text(s)}, {s.dropped} missed. Which edge starts the "
                        "exposure, and its delay, need the blinking-LED test (firmware-p4/README.md)"))
    return out


# ------------------------------------------------------------------ report

def _result_cell(result: str | None) -> str:
    return result if result else "_record the numbers_"


def to_markdown(r: BenchReport) -> str:
    info = r.info_before
    lines = [
        f"## Bench test (nireyecam-bench {r.tool_version})",
        "",
        "Draft for a **Bench test result** issue: one issue per row below, or *Other* with the whole",
        "report. The suggested results are the tool's thresholds; check the numbers before agreeing.",
        "",
    ]
    if info.get("board") == "sim":
        lines += ["> **Simulated camera**: these numbers describe the simulator, not hardware.", ""]
    if r.error:
        lines += [f"> **The run stopped early**: {r.error}", ""]
    lines += ["### What was tested", "", "| What was tested | Suggested result | Why |", "|---|---|---|"]
    lines += [f"| {item} | {_result_cell(res)} | {why} |" for item, res, why in verdicts(r)]

    lines += ["", "### Hardware and firmware", ""]
    keys = ["board", "fw", "uid", "sensor", "model_id", "revision", "cam_ok", "cam_error",
            "width", "height", "exposure", "frame_length", "frame_period_us", "again", "csi_errors"]
    lines += [f"- `{k}`: {info[k]}" for k in keys if k in info and info[k] != ""]
    lines += ["- Lens, filter, IR LEDs, wiring and scene: _fill in_"]

    lines += ["", "### How it was measured", "",
              f"- `{r.command or 'nireyecam-bench'}`, started {r.started}, took {r.duration_s:g} s.",
              "- Auto exposure off. After each START the first "
              f"{SETTLE_FRAMES} frames are ignored while new settings take effect.",
              "- Each resolution streams at its default frame length, then at the shortest (`FLL 0`).",
              "- Frame period, jitter and frame rates come from the camera's timestamps;",
              "  dropped frames from gaps in its frame counter.",
              "- Brightness tests average whole frames at the first resolution."]

    lines += ["", "### Measurements", ""]
    if r.streams:
        lines += ["| Resolution | Pass | Frame length | Frames | Dropped | Sensor fps | Delivered fps "
                  "| Delivered MB/s | Needed MB/s | Period (us) | Expected (us) | Std (us) | Outliers |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        rows = list(r.streams) + ([r.extsync.stream] if r.extsync and r.extsync.stream else [])
        for s in rows:
            if s.error:
                lines.append(f"| {s.resolution} | {s.mode} | {s.frame_length} | {s.frames} "
                             f"| error: {s.error} | | | | | | | | |")
            else:
                want = f"{s.expected_period_us:.0f}" if s.expected_period_us else ""
                lines.append(f"| {s.resolution} | {s.mode} | {s.frame_length} | {s.frames} | {s.dropped} "
                             f"| {s.sensor_fps:.2f} | {s.delivered_fps:.2f} | {s.delivered_mb_s:.2f} "
                             f"| {s.needed_mb_s:.2f} | {s.period_us:.0f} | {want} | {s.period_std_us:.1f} "
                             f"| {s.period_outliers} |")
    lin = r.linearity
    if lin:
        sign = "+" if lin.black_level >= 0 else "-"
        fit = f"level = {lin.slope:.4f} x lines {sign} {abs(lin.black_level):.1f}, R² = {lin.r2:.4f}"
        lines += ["", "**Exposure sweep**: " + (lin.error or fit), "",
                  "| Exposure (lines) | Mean level | Clipped |", "|---|---|---|"]
        lines += [f"| {e} | {m:.1f} | {'yes' if c else 'no'} |"
                  for e, m, c in zip(lin.exposures, lin.means, lin.clipped)]
    g = r.gain
    if g and g.means:
        lines += ["", f"**Gain sweep** at {g.exposure} lines" + (f": {g.error}" if g.error else ""), "",
                  "| AGAIN | Expected | Mean level | Measured | Clipped |", "|---|---|---|---|---|"]
        for i, (code, exp) in enumerate(zip(g.codes, g.expected)):
            mean = f"{g.means[i]:.1f}" if i < len(g.means) else ""
            meas = f"{g.measured[i]:.2f}x" if i < len(g.measured) else ""
            clip = ("yes" if g.clipped[i] else "no") if i < len(g.clipped) else ""
            lines.append(f"| {code} | {exp:g}x | {mean} | {meas} | {clip} |")
    elif g and g.error:
        lines += ["", f"**Gain sweep**: {g.error}"]
    if r.led:
        lines += ["", f"**IR LEDs** at {r.led.exposure} lines: "
                  + (r.led.error or f"mean level {r.led.mean_off:.1f} off, {r.led.mean_on:.1f} on (LED {r.led.level})")]
    if r.sync:
        s = r.sync
        lines += ["", "**Sync loopback**: " + (s.error or f"{s.pulses} pulses, {s.missing} missing")
                  + (", intervals " + ", ".join(f"{x:.3f}" for x in s.intervals_s) + " s" if s.intervals_s else "")]
    if r.extsync and r.extsync.error:
        lines += ["", f"**External sync** at {r.extsync.hz} Hz: {r.extsync.error}"]
    if r.external_sync_pulses:
        lines += ["", f"**Sync input**: {r.external_sync_pulses} pulses arrived from outside during the run."]

    lines += ["", "### What should change", "", "_If a result differs: which code or docs are now wrong, and how._"]
    return "\n".join(lines) + "\n"


def to_json(r: BenchReport) -> str:
    return json.dumps(asdict(r), indent=2)


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nireyecam-bench", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 2)[2])
    ap.add_argument("--cam", required=True, help="the camera's serial port, or 'sim'")
    ap.add_argument("--res", action="append", metavar="WxH",
                    help="resolution to stream; repeat for several (default: a set for the board)")
    ap.add_argument("--seconds", type=float, default=4.0, help="streaming time per pass (default 4)")
    ap.add_argument("--led", type=int, metavar="N",
                    help="light the scene with the IR LEDs at N (1-1000; any N is on/off on the ESP32-P4) "
                         "and compare against LEDs off")
    ap.add_argument("--sync", action="store_true",
                    help="drive the sync output and count the pulses that return (wire SYNC_OUT to SYNC_IN)")
    ap.add_argument("--extsync", type=int, metavar="HZ",
                    help="ESP32-P4: trigger frames at HZ through EXTSYNC (wire TRIG_OUT to the sensor's GPIO0)")
    ap.add_argument("--out", type=Path, default=Path("bench-report.md"), help="report (default bench-report.md)")
    ap.add_argument("--json", type=Path, help="also write every number as JSON")
    args = ap.parse_args(argv)
    if args.led is not None and not 1 <= args.led <= 1000:
        ap.error("--led must be 1-1000")
    if args.extsync is not None and not 1 <= args.extsync <= 1000:
        ap.error("--extsync must be 1-1000 Hz")
    if args.seconds < 1:
        ap.error("--seconds must be at least 1")

    command = " ".join(["nireyecam-bench", *(sys.argv[1:] if argv is None else argv)])
    print("Keep the camera and the scene still until the run ends.")
    try:
        cam = open_camera(args.cam, "bench")
    except Exception as e:  # missing port, permissions, ...
        print(f"error: cannot open {args.cam}: {e}", file=sys.stderr)
        return 1
    try:
        report = run_bench(cam, resolutions=args.res, seconds=args.seconds, led=args.led,
                           sync=args.sync, extsync_hz=args.extsync, command=command)
    except CameraError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        cam.close()

    args.out.write_text(to_markdown(report))
    if args.json:
        args.json.write_text(to_json(report))
    print()
    if report.error:
        print(f"Stopped early: {report.error}")
    short = {CONFIRMS: "confirms", DIFFERS: "DIFFERS", INCONCLUSIVE: "inconclusive", None: "numbers"}
    for item, result, why in verdicts(report):
        print(f"- [{short[result]}] {item}\n    {why}")
    print(f"\nReport: {args.out}" + (f", numbers: {args.json}" if args.json else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
