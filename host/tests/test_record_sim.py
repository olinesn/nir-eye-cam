"""End to end on simulated cameras: configure, record, align, check against truth."""

import csv
import json
import shutil
import subprocess

import numpy as np
import pytest

from nireyecam import record
from nireyecam.device import Camera, CameraError
from nireyecam.simulator import SimTransport, SimWorld


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_command_replies_and_errors():
    cam = Camera(SimTransport(SimWorld(), seed=1), "a")
    try:
        assert cam.command("PING") == ""
        assert cam.command("EXP 50") == "50"
        assert cam.info()["exposure"] == 50
        with pytest.raises(CameraError, match="AGAIN"):
            cam.command("AGAIN 9")
        cam.command("START")
        with pytest.raises(CameraError, match="stop first"):
            cam.command("RES 320x320")
    finally:
        cam.close()


def test_two_cameras_end_up_on_one_timeline(tmp_path, monkeypatch):
    world = SimWorld(seed=3)
    devices = {}

    def fake_open(spec, name, sim_world=None):
        t = SimTransport(world, seed={"left": 11, "right": 22}[name])
        devices[name] = t.device
        return Camera(t, name)

    monkeypatch.setattr(record, "open_camera", fake_open)
    out = tmp_path / "s"
    rc = record.main(["--out", str(out), "--cam", "left=sim", "--cam", "right=sim",
                      "--duration", "4", "--format", "raw", "--led", "500", "--exp", "60"])
    assert rc == 0

    meta = json.loads((out / "session.json").read_text())
    assert meta["sync_master"] == "left"
    for c in meta["cameras"]:
        assert c["frames"] > 90 and c["dropped"] == 0 and c["sync_pulses"] >= 3
        assert abs(c["timing"]["fps"] - 30) < 0.5
        assert c["device"]["exposure"] == 60 and c["device"]["led"] == 500
        n = c["frames"]
        assert (out / c["name"] / "frames.u8").stat().st_size == n * 160 * 120

    report = json.loads((out / "alignment.json").read_text())
    assert report["reference"] == "left"
    assert report["cameras"]["right"]["residual_max_us"] < 5

    # Ground truth: a frame's aligned time must equal its true start time
    # expressed on the left camera's clock, measured from left's first frame.
    left, right = devices["left"], devices["right"]
    left_rows = _rows(out / "left" / "frames_aligned.csv")
    t0_true = left.truth_frames[int(left_rows[0]["seq"])]
    for name, dev in devices.items():
        rows = _rows(out / name / "frames_aligned.csv")
        got = np.array([float(r["t_s"]) for r in rows])
        want = np.array([(left.dev_us(dev.truth_frames[int(r["seq"])]) - left.dev_us(t0_true)) / 1e6
                         for r in rows])
        assert np.max(np.abs(got - want)) < 20e-6, name
    # The two cameras really are free-running: their frames do not line up.
    assert left.clock_ppm != right.clock_ppm


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_ffv1_is_lossless(tmp_path):
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 256, (120, 160), dtype=np.uint8) for _ in range(5)]
    w = record.FFV1Writer(tmp_path / "v.mkv", 160, 120, 30)
    for f in frames:
        w.write(f.tobytes())
    w.close()
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(tmp_path / "v.mkv"),
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    assert raw == b"".join(f.tobytes() for f in frames)
