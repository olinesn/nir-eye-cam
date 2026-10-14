"""nireyecam-bench: the measurements on a simulated camera, and the verdicts on made-up results."""

import json

from nireyecam import bench, protocol
from nireyecam.bench import (
    CONFIRMS, DIFFERS, INCONCLUSIVE, BenchReport, ExtSyncResult, GainResult, StreamResult,
)
from nireyecam.device import Camera
from nireyecam.protocol import Packet
from nireyecam.simulator import SimTransport, SimWorld


def _frame(seq, t_us, w=4, h=2):
    return Packet(protocol.FRAME, 0, seq, t_us, w, h, 0, 0, bytes(w * h)), 0


def test_frame_stats_counts_gaps_and_uses_device_time():
    # 100 fps; frames 4 and 7-8 never arrived.
    seqs = [1, 2, 3, 5, 6, 9, 10]
    frames = [_frame(s, 1_000_000 + (s - 1) * 10_000) for s in seqs]
    r = bench.frame_stats(frames, StreamResult("4x2", "default"))
    assert r.frames == 7 and r.dropped == 3
    assert r.period_us == 10_000 and r.period_outliers == 0
    assert round(r.sensor_fps, 6) == 100
    assert round(r.delivered_fps, 6) == round(6 / 0.09, 6)  # 7 frames over 90 ms
    assert round(r.needed_mb_s * 1e6) == 800                # 8 bytes x 100 fps


def test_frame_stats_needs_frames():
    assert "only 2" in bench.frame_stats([_frame(1, 0), _frame(2, 10)], StreamResult("4x2", "default")).error


def test_full_run_on_the_simulator():
    cam = Camera(SimTransport(SimWorld(seed=5), seed=7), "bench")
    try:
        before = cam.info()
        r = bench.run_bench(cam, resolutions=["160x120", "320x240"], seconds=1.2, sweep_seconds=0.5,
                            led=300, sync=True, sync_seconds=4.0, log=lambda *_: None)
        after = cam.info()
    finally:
        cam.close()

    assert r.error is None and r.profile == "rp2040"
    # 160x120 is already at its shortest frame length, so it gets one pass; 320x240 gets two.
    assert [(s.resolution, s.mode) for s in r.streams] == [
        ("160x120", "default"), ("320x240", "default"), ("320x240", "fastest")]
    for s in r.streams:
        assert s.error is None and s.dropped == 0 and s.period_outliers == 0
    assert all(abs(s.sensor_fps - 30) < 0.3 for s in r.streams if s.mode == "default")
    assert r.streams[2].sensor_fps > r.streams[1].sensor_fps

    lin = r.linearity
    assert lin.error is None and lin.r2 > 0.99 and lin.slope > 0
    g = r.gain
    assert g.error is None and g.worst_error < 0.1 and not any(g.clipped)
    assert r.led.ratio > 3
    assert r.sync.pulses >= 3 and r.sync.missing == 0
    assert all(0.39 <= x <= 1.01 for x in r.sync.intervals_s)
    assert r.external_sync_pulses == 0

    results = {item: res for item, res, _ in bench.verdicts(r)}
    assert results[bench.ITEM_RP_GAIN] == CONFIRMS
    assert results[bench.ITEM_RP_VSYNC] == CONFIRMS
    assert results[bench.ITEM_HM_NIR] == CONFIRMS
    assert results[bench.OTHER] == CONFIRMS  # sync loopback
    assert results[bench.ITEM_RP_USB] is None  # numbers only

    # Everything the run touched is back as it was, and the LEDs are off.
    for k in ("width", "height", "frame_length", "exposure", "again", "ae", "led", "syncgen", "streaming"):
        assert after[k] == before[k], k

    md = bench.to_markdown(r)
    for heading in ("### What was tested", "### Hardware and firmware", "### How it was measured",
                    "### Measurements", "### What should change"):
        assert heading in md
    assert "Simulated camera" in md
    json.loads(bench.to_json(r))


def _p4_report(**kw):
    info = {"board": "esp32p4", "cam_ok": 1, "model_id": "0x53354731", "revision": "0x3030", "csi_errors": 0}
    r = BenchReport(tool_version="test", profile="esp32p4", info_before=info,
                    info_after=dict(info, csi_errors=kw.pop("csi_after", 0)))
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def _stream(res="320x320", mode="default", **kw):
    s = StreamResult(res, mode, frames=100, period_us=10_000, sensor_fps=100, delivered_fps=100,
                     delivered_mb_s=10.24, needed_mb_s=10.24, expected_period_us=10_000)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_verdicts_for_a_good_p4_run():
    r = _p4_report(streams=[_stream()],
                   gain=GainResult(codes=[0, 16, 24, 28], expected=[1, 2, 4, 8], means=[20, 40, 80, 160],
                                   clipped=[False] * 4, measured=[1, 2.02, 3.95, 8.1], worst_error=0.0125),
                   extsync=ExtSyncResult(hz=50, stream=_stream(mode="extsync", period_us=20_000,
                                                               expected_period_us=20_000)))
    results = {item: res for item, res, _ in bench.verdicts(r)}
    assert results[bench.ITEM_P4_BOOT] == CONFIRMS
    assert results[bench.ITEM_P4_CSI] == CONFIRMS
    assert results[bench.ITEM_P4_FSYNC] == CONFIRMS
    assert results[bench.ITEM_P4_DEVICE] == CONFIRMS  # 10 MB/s cannot cross full speed
    assert results[bench.ITEM_P4_USB] is None
    assert results[bench.ITEM_P4_EXTSYNC] == CONFIRMS
    assert results[bench.OTHER] == CONFIRMS  # gain


def test_verdicts_flag_what_differs():
    r = _p4_report(csi_after=7,
                   info_before={"board": "esp32p4", "cam_ok": 1, "model_id": "0x12345678",
                                "revision": "0x2020", "csi_errors": 0},
                   streams=[_stream(period_us=10_400, delivered_mb_s=0.9)],  # 4% off INFO's frame period
                   gain=GainResult(codes=[0, 16, 24, 28], expected=[1, 2, 4, 8], means=[20, 30, 40, 50],
                                   clipped=[False] * 4, measured=[1, 1.5, 2, 2.5], worst_error=0.69),
                   extsync=ExtSyncResult(hz=50, stream=_stream(mode="extsync", period_us=20_000,
                                                               expected_period_us=20_000, dropped=3)))
    verdicts = bench.verdicts(r)
    results = {item: res for item, res, _ in verdicts}
    assert results[bench.ITEM_P4_BOOT] == DIFFERS
    assert "not cut 3" in dict((i, w) for i, _, w in verdicts)[bench.ITEM_P4_BOOT]
    assert results[bench.ITEM_P4_CSI] == DIFFERS
    assert results[bench.ITEM_P4_FSYNC] == DIFFERS
    assert results[bench.ITEM_P4_DEVICE] == INCONCLUSIVE  # 0.9 MB/s fits full speed too
    assert results[bench.ITEM_P4_EXTSYNC] == DIFFERS
    assert results[bench.OTHER] == DIFFERS


class _DeadSensor:
    """A camera whose sensor never came up: only STOP and INFO are answered."""

    def __init__(self):
        self.sent = []

    def command(self, line):
        self.sent.append(line)
        return ""

    def info(self):
        return {"board": "esp32p4", "cam_ok": 0, "cam_error": "ESP_ERR_NOT_FOUND",
                "model_id": "0x00000000", "revision": "0x0000"}


def test_a_dead_sensor_still_gets_a_report():
    cam = _DeadSensor()
    r = bench.run_bench(cam, log=lambda *_: None)
    assert cam.sent == ["STOP"]  # nothing else is tried
    assert "ESP_ERR_NOT_FOUND" in r.error
    assert bench.verdicts(r) == [(bench.ITEM_P4_BOOT, DIFFERS, "cam_error 'ESP_ERR_NOT_FOUND'")]
    assert "stopped early" in bench.to_markdown(r)


def test_cli_writes_both_files(tmp_path, monkeypatch):
    real = bench.run_bench
    monkeypatch.setattr(bench, "run_bench", lambda cam, **kw: real(cam, sweep_seconds=0.3, **kw))
    md, js = tmp_path / "r.md", tmp_path / "r.json"
    assert bench.main(["--cam", "sim:3", "--res", "160x120", "--seconds", "1",
                       "--out", str(md), "--json", str(js)]) == 0
    assert "### Measurements" in md.read_text()
    data = json.loads(js.read_text())
    assert data["streams"][0]["resolution"] == "160x120" and data["streams"][0]["frames"] > 20
