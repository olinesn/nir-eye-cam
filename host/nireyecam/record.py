"""Record one or more NIR eye cameras to disk, then align them.

    nireyecam-record --out runs/mouse01 --cam left=/dev/cu.usbmodem1101 \\
        --cam right=/dev/cu.usbmodem1201 --sync-master left --led 300 --duration 600

Per camera, in <out>/<name>/:
    video.mkv     lossless FFV1, 8-bit gray (or frames.u8 raw with --format raw)
    frames.csv    one row per video frame: device timestamp, sensor frame counter
    sync.csv      every sync-line rising edge, in the device's clock
and in <out>/: session.json (settings, device info, stats), alignment.json,
and <name>/frames_aligned.csv with each frame's time on the shared timeline.

The video's frame rate is nominal. Real timing, including any dropped frames
(gaps in ``seq``), is in the CSVs: row i of frames.csv is frame i of the video.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import __version__, protocol
from .align import AlignError, align_session, print_report
from .device import Camera, CameraError, discover, open_camera


class FFV1Writer:
    def __init__(self, path: Path, width: int, height: int, fps: float) -> None:
        self.path = path
        self._p = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
             "-s", f"{width}x{height}", "-framerate", f"{fps:g}", "-i", "-",
             "-c:v", "ffv1", "-level", "3", "-g", "1", "-slicecrc", "1", str(path)],
            stdin=subprocess.PIPE,
        )

    def write(self, frame: bytes) -> None:
        self._p.stdin.write(frame)

    def close(self) -> None:
        self._p.stdin.close()
        if self._p.wait() != 0:
            raise RuntimeError(f"ffmpeg failed writing {self.path}")


class RawWriter:
    def __init__(self, path: Path, width: int, height: int, fps: float) -> None:
        self.path = path
        self._f = open(path, "wb")
        (path.parent / "frames.u8.json").write_text(
            json.dumps({"dtype": "uint8", "width": width, "height": height, "layout": "frames x height x width"})
        )

    def write(self, frame: bytes) -> None:
        self._f.write(frame)

    def close(self) -> None:
        self._f.close()


class CameraRecorder(threading.Thread):
    """Drains one camera's packet queue into its files."""

    def __init__(self, cam: Camera, folder: Path, fmt: str, nominal_fps: float) -> None:
        super().__init__(name=f"rec-{cam.name}", daemon=True)
        self.cam, self.folder, self.fmt, self.nominal_fps = cam, folder, fmt, nominal_fps
        self.frames = 0
        self.dropped = 0
        self.syncs = 0
        self.first_seq: int | None = None
        self.last_seq: int | None = None
        self.size: tuple[int, int] | None = None
        self.latest: np.ndarray | None = None
        self.recent_t: list[int] = []  # device times of the last frames, for live fps
        self.error: BaseException | None = None
        self._writer = None
        self._finishing = threading.Event()  # not _stop: Thread uses that name on Python < 3.13
        folder.mkdir(parents=True, exist_ok=True)
        self._frames_csv = open(folder / "frames.csv", "w", newline="")
        self._sync_csv = open(folder / "sync.csv", "w", newline="")
        self._fw = csv.writer(self._frames_csv)
        self._sw = csv.writer(self._sync_csv)
        self._fw.writerow(["frame", "seq", "device_t_us", "host_t_ns", "exposure_lines", "again", "ae"])
        self._sw.writerow(["seq", "device_t_us", "host_t_ns"])

    def run(self) -> None:
        try:
            while not (self._finishing.is_set() and self.cam.data.empty()):
                try:
                    p, host_ns = self.cam.data.get(timeout=0.1)
                except queue.Empty:
                    continue
                if p.type == protocol.FRAME:
                    self._frame(p, host_ns)
                elif p.type == protocol.SYNC:
                    self.syncs += 1
                    self._sw.writerow([p.seq, p.t_us, host_ns])
        except BaseException as e:
            self.error = e

    def _frame(self, p: protocol.Packet, host_ns: int) -> None:
        if self._writer is None:
            self.size = (p.width, p.height)
            cls = FFV1Writer if self.fmt == "ffv1" else RawWriter
            name = "video.mkv" if self.fmt == "ffv1" else "frames.u8"
            self._writer = cls(self.folder / name, p.width, p.height, self.nominal_fps)
        if (p.width, p.height) != self.size:
            raise RuntimeError(f"{self.cam.name}: resolution changed mid-recording")
        if self.last_seq is not None and p.seq > self.last_seq + 1:
            self.dropped += p.seq - self.last_seq - 1
        if self.first_seq is None:
            self.first_seq = p.seq
        self.last_seq = p.seq
        self._writer.write(p.payload)
        self._fw.writerow([self.frames, p.seq, p.t_us, host_ns, p.exposure, p.again,
                           int(bool(p.flags & protocol.FLAG_AE))])
        self.frames += 1
        self.recent_t = (self.recent_t + [p.t_us])[-60:]
        self.latest = p.image()

    def fps(self) -> float:
        t = self.recent_t
        return (len(t) - 1) / ((t[-1] - t[0]) / 1e6) if len(t) > 1 and t[-1] > t[0] else 0.0

    def finish(self) -> None:
        self._finishing.set()
        self.join(timeout=10)
        if self._writer:
            self._writer.close()
        self._frames_csv.close()
        self._sync_csv.close()


def configure(cam: Camera, args, master: bool) -> dict:
    cam.command("STOP")
    info = cam.info()
    if not info.get("cam_ok"):
        raise CameraError(f"{cam.name}: sensor not ready: {info.get('cam_error')}")
    if args.res:
        cam.command(f"RES {args.res}")
    if args.fll is not None:
        cam.command(f"FLL {args.fll}")
    if args.exp is not None:
        cam.command(f"EXP {args.exp}")
    cam.command(f"AGAIN {args.gain}")
    cam.command(f"AE {int(args.ae)}")
    cam.command(f"LED {args.led}")
    cam.command(f"SYNCGEN {int(master)}")
    return cam.info()


def frame_timing(folder: Path, frame_length: int) -> dict:
    """Measured frame period, and the line time it implies (exposure is set in lines)."""
    with open(folder / "frames.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 2:
        return {}
    seq = np.array([int(r["seq"]) for r in rows])
    t = np.array([float(r["device_t_us"]) for r in rows])
    steps = np.diff(seq)
    periods = np.diff(t)[steps > 0] / steps[steps > 0]
    period_us = float(np.median(periods))
    line_us = period_us / frame_length
    exposure = int(rows[-1]["exposure_lines"])
    return {
        "frame_period_us": period_us,
        "fps": 1e6 / period_us,
        "line_time_us": line_us,
        "exposure_ms": exposure * line_us / 1000,
    }


def run(args) -> int:
    out: Path = args.out
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        print(f"error: {out} is not empty (use --overwrite)", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)

    specs = []
    for i, s in enumerate(args.cam):
        name, _, port = s.partition("=") if "=" in s else (f"cam{i}", "", s)
        specs.append((name, port))
    names = [n for n, _ in specs]
    if len(set(names)) != len(names):
        print("error: camera names must be unique", file=sys.stderr)
        return 1
    master = args.sync_master or (names[0] if len(names) > 1 and not args.external_sync else None)
    if master and master not in names:
        print(f"error: --sync-master {master!r} is not one of {names}", file=sys.stderr)
        return 1

    fmt = args.format
    if fmt == "ffv1" and not shutil.which("ffmpeg"):
        print("warning: ffmpeg not found; writing raw frames instead", file=sys.stderr)
        fmt = "raw"

    sim_world = None
    if any(p.startswith("sim") for _, p in specs):
        from .simulator import SimWorld

        sim_world = SimWorld()

    cams: list[Camera] = []
    recs: list[CameraRecorder] = []
    infos = {}
    started = None
    failed = False
    try:
        for name, port in specs:
            cams.append(open_camera(port, name, sim_world))
        for cam in cams:
            infos[cam.name] = configure(cam, args, master=cam.name == master)
            print(f"{cam.name}: {infos[cam.name]['board']} {infos[cam.name]['uid']} "
                  f"{infos[cam.name]['width']}x{infos[cam.name]['height']} exp={infos[cam.name]['exposure']} lines")
        for cam in cams:
            rec = CameraRecorder(cam, out / cam.name, fmt, args.nominal_fps)
            rec.start()
            recs.append(rec)

        started = dt.datetime.now().astimezone()
        for cam in cams:
            cam.command("START")
        print(f"recording to {out} - Ctrl-C to stop")
        _record_loop(args, cams, recs)
    except CameraError as e:
        print(f"error: {e}", file=sys.stderr)
        failed = True
    finally:
        for cam in cams:
            for cmd in ("STOP", "LED 0", "SYNCGEN 0"):
                try:
                    cam.command(cmd)
                except CameraError:
                    pass
        time.sleep(0.3)  # let in-flight packets arrive
        for rec in recs:
            rec.finish()
        for cam in cams:
            cam.close()

    if not recs:
        return 1
    meta = {
        "nireyecam": __version__,
        "started": started.isoformat() if started else None,
        "settings": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items() if k != "func"},
        "sync_master": master,
        "cameras": [],
    }
    for rec in recs:
        info = infos[rec.cam.name]
        meta["cameras"].append({
            "name": rec.cam.name,
            "port": dict(specs)[rec.cam.name],
            "device": info,
            "format": fmt,
            "frames": rec.frames,
            "dropped": rec.dropped,
            "sync_pulses": rec.syncs,
            "parser_bytes_skipped": rec.cam.parser.bytes_skipped,
            "timing": frame_timing(out / rec.cam.name, info["frame_length"]),
            "error": repr(rec.error) if rec.error else None,
        })
    (out / "session.json").write_text(json.dumps(meta, indent=2))

    for c in meta["cameras"]:
        t = c["timing"]
        rate = f"{t['fps']:.2f} fps, exposure {t['exposure_ms']:.2f} ms" if t else "no timing"
        print(f"{c['name']}: {c['frames']} frames, {c['dropped']} dropped, "
              f"{c['sync_pulses']} sync pulses, {rate}")
        if c["error"]:
            print(f"  error: {c['error']}")

    if len(recs) > 1 or any(r.syncs for r in recs):
        try:
            print_report(align_session(out))
        except AlignError as e:
            print(f"alignment failed: {e}", file=sys.stderr)
            return 1
    return 1 if failed or any(r.error for r in recs) else 0


def _record_loop(args, cams, recs) -> None:
    stop = threading.Event()
    old = signal.signal(signal.SIGINT, lambda *_: stop.set())
    deadline = time.monotonic() + args.duration if args.duration else None
    preview = _Preview(recs) if args.preview else None
    last_status = 0.0
    try:
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            for cam in cams:
                if cam.error:
                    raise CameraError(f"{cam.name}: connection lost: {cam.error}")
            for rec in recs:
                if rec.error:
                    raise CameraError(f"{rec.cam.name}: {rec.error}")
            if preview:
                if not preview.show():
                    break
            else:
                time.sleep(0.05)
            if time.monotonic() - last_status > 2:
                last_status = time.monotonic()
                print("  " + "  ".join(f"{r.cam.name}: {r.frames} fr {r.fps():.1f} fps "
                                       f"drop {r.dropped} sync {r.syncs}" for r in recs), flush=True)
    finally:
        signal.signal(signal.SIGINT, old)
        if preview:
            preview.close()


class _Preview:
    def __init__(self, recs) -> None:
        import cv2  # optional dependency: pip install opencv-python

        self.cv2, self.recs = cv2, recs

    def show(self) -> bool:
        for r in self.recs:
            if r.latest is not None:
                img = self.cv2.resize(r.latest, None, fx=3, fy=3, interpolation=self.cv2.INTER_NEAREST)
                self.cv2.imshow(r.cam.name, img)
        return self.cv2.waitKey(30) not in (ord("q"), 27)

    def close(self) -> None:
        self.cv2.destroyAllWindows()


def list_main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="List connected NIR Eye Cams.").parse_args(argv)
    found = discover()
    if not found:
        print("no cameras found")
        return 1
    for c in found:
        print(f"{c.port}  serial={c.serial_number}  {c.product or ''}")
    return 0


def _res_arg(text: str) -> str:
    w, sep, h = text.lower().partition("x")
    if not (sep and w.isdigit() and h.isdigit()):
        raise argparse.ArgumentTypeError("expected WxH, e.g. 320x320")
    return f"{int(w)}x{int(h)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--out", type=Path, required=True, help="session folder")
    ap.add_argument("--cam", action="append", required=True, metavar="NAME=PORT",
                    help="camera serial port (or 'sim'); repeat for each camera")
    ap.add_argument("--res", type=_res_arg, metavar="WxH",
                    help="frame size (default: keep the device's). RP2040: 160x120, 320x240, 320x320; "
                         "ESP32-P4/VD55G1: any centred crop up to 804x704 with W*H a multiple of 8")
    ap.add_argument("--exp", type=int, help="exposure in sensor lines (default: firmware default)")
    ap.add_argument("--fll", type=int, help="frame length in lines; raise it to lower the frame rate")
    ap.add_argument("--gain", type=int, default=0,
                    help="analog gain code. RP2040: 0-3 = 1x/2x/4x/8x; VD55G1: 0-28, gain = 32/(32-code)")
    ap.add_argument("--ae", action="store_true", help="sensor auto exposure (avoid for pupil size)")
    ap.add_argument("--led", type=int, default=0, help="IR LED duty 0-1000 (default off)")
    ap.add_argument("--sync-master", help="camera that drives the sync line (default: the first, if >1 camera)")
    ap.add_argument("--external-sync", action="store_true",
                    help="the sync line is driven by your rig, not by a camera")
    ap.add_argument("--duration", type=float, help="seconds (default: until Ctrl-C)")
    ap.add_argument("--format", choices=["ffv1", "raw"], default="ffv1")
    ap.add_argument("--nominal-fps", type=float, default=30.0, help="frame rate written into the video container")
    ap.add_argument("--preview", action="store_true", help="live window per camera (needs opencv-python)")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    if not 0 <= args.led <= 1000:
        ap.error("--led must be 0-1000")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
