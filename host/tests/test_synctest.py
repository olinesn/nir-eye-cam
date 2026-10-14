"""The two-camera sync test: synthetic sessions with known offsets, and the simulator end to end."""

import csv
import json

import numpy as np
import pytest

from nireyecam import record, synctest
from nireyecam.device import Camera
from nireyecam.simulator import SimTransport, SimWorld

BLINK_T0 = 0.0123  # first rising edge of the synthetic LED


def lit_seconds(a, b, hz):
    """Seconds a 50% square wave (rising at BLINK_T0) is on within [a, b]."""
    period = 1 / hz

    def until(t):
        x = np.maximum(t - BLINK_T0, 0)
        n = np.floor(x / period)
        return n * period / 2 + np.minimum(x - n * period, period / 2)

    return until(b) - until(a)


def make_session(root, starts, *, exposure_s=2e-3, blink_hz=43, trigger=25, stamp_shift=None,
                 led=150.0, noise=2.0, seed=0):
    """A recorded session on disk: frames that film a blinking LED, exposed at ``starts`` (true time)."""
    rng = np.random.default_rng(seed)
    h, w = 120, 160
    yy, xx = np.mgrid[0:h, 0:w]
    spot = (xx - 20) ** 2 + (yy - 15) ** 2 < 5**2
    cams = []
    for name, st in starts.items():
        lit = lit_seconds(st, st + exposure_s, blink_hz) / exposure_s if blink_hz else np.zeros(len(st))
        frames = rng.normal(20, noise, (len(st), h, w)).astype(np.float32)
        frames[:, spot] += (led * lit)[:, None].astype(np.float32)
        folder = root / name
        folder.mkdir(parents=True)
        np.clip(np.round(frames), 0, 255).astype(np.uint8).tofile(folder / "frames.u8")
        (folder / "frames.u8.json").write_text(json.dumps({"dtype": "uint8", "width": w, "height": h}))
        t = st + (stamp_shift or {}).get(name, 0.0)
        with open(folder / "frames_aligned.csv", "w", newline="") as f:
            out = csv.writer(f)
            out.writerow(["frame", "seq", "t_s"])
            out.writerows((i, i + 1, f"{v:.9f}") for i, v in enumerate(t))
        cams.append({"name": name, "device": {"width": w, "height": h}, "timing": {"exposure_ms": exposure_s * 1e3}})
    (root / "session.json").write_text(json.dumps({"settings": {"trigger": trigger, "blink": blink_hz},
                                                   "cameras": cams}))
    return root


def triggered(offset_s, seconds=20.0, hz=25):
    a = np.arange(int(seconds * hz)) / hz + 0.001
    return {"a": a, "b": a + offset_s}


@pytest.mark.parametrize("offset_us", [30.0, -45.0])
def test_locked_cameras_pass_with_their_offset(tmp_path, offset_us):
    r = synctest.analyze(make_session(tmp_path, triggered(offset_us * 1e-6)))
    (p,) = r.pairs
    assert r.verdict == p.verdict == synctest.PASS
    assert p.method == "edges" and p.edge_pairs > 30
    assert p.offset_us == pytest.approx(offset_us, abs=3)
    assert p.offset_se_us < 2
    assert p.contradictions == 0 and p.unpaired == 0
    assert all(c.led_seen and c.missed_triggers == 0 for c in r.cameras)
    assert not r.warnings


def test_offset_comes_from_the_led_not_the_timestamps(tmp_path):
    # b exposes 20 us before a, but its timestamps run 500 us late.
    r = synctest.analyze(make_session(tmp_path, triggered(-20e-6), stamp_shift={"b": 500e-6}))
    (p,) = r.pairs
    assert p.verdict == synctest.PASS and p.method == "edges"
    assert p.offset_us == pytest.approx(-20, abs=3)
    assert p.timestamp_error_us == pytest.approx(-500, abs=3)


def test_steady_offset_beyond_one_exposure_fails_from_the_phase(tmp_path):
    # b exposes 3 ms after a, and its timestamps say 3.5 ms.
    r = synctest.analyze(make_session(tmp_path, triggered(3e-3), stamp_shift={"b": 500e-6}))
    (p,) = r.pairs
    assert p.verdict == synctest.FAIL and "steady offset" in p.reason
    assert p.method == "phase"
    assert p.offset_us == pytest.approx(3000, abs=50)
    assert p.timestamp_error_us == pytest.approx(-500, abs=50)
    assert p.offset_max_us - p.offset_min_us < 100


def test_free_running_cameras_drift_and_fail(tmp_path):
    a = np.arange(500) * 0.04
    b = np.arange(500) * 0.04 * (1 + 100e-6) + 0.0101  # 100 ppm apart, ~10 ms out of phase
    r = synctest.analyze(make_session(tmp_path, {"a": a, "b": b}, trigger=None))
    (p,) = r.pairs
    assert r.frame_hz_source == "measured"
    assert any("without --trigger" in w for w in r.warnings)
    assert p.verdict == synctest.FAIL and "drifts" in p.reason
    assert p.offset_max_us - p.offset_min_us > 1000
    assert p.contradictions > 50


def test_no_led_is_inconclusive(tmp_path, capsys):
    session = make_session(tmp_path, triggered(0.0, seconds=10), led=0.0)
    assert synctest.main([str(session)]) == 1
    report = json.loads((session / "synctest.json").read_text())
    assert report["verdict"] == synctest.INCONCLUSIVE
    assert "does not see the blinking LED" in report["pairs"][0]["reason"]
    assert "Verdict: INCONCLUSIVE" in capsys.readouterr().out


def test_warnings_for_rates_and_exposure(tmp_path):
    r = synctest.analyze(make_session(tmp_path, triggered(0.0, seconds=10), blink_hz=50, exposure_s=8e-3))
    text = " ".join(r.warnings)
    assert "whole or half number" in text and "is long for a 50 Hz blink" in text


def test_saturated_led_falls_back_to_the_phase(tmp_path):
    r = synctest.analyze(make_session(tmp_path, triggered(30e-6), led=400.0))
    (p,) = r.pairs
    assert any("saturates" in w for w in r.warnings)
    assert p.method == "phase" and p.verdict == synctest.PASS


def test_main_writes_the_report(tmp_path, capsys):
    session = make_session(tmp_path, triggered(10e-6, seconds=10))
    assert synctest.main([str(session), "--roi", "b=15,10,10,10"]) == 0
    report = json.loads((session / "synctest.json").read_text())
    assert report["verdict"] == synctest.PASS
    assert report["cameras"][1]["roi"] == [15, 10, 10, 10]
    assert "b vs a: PASS" in capsys.readouterr().out


# ------------------------------------------------------------------ simulator


def test_sim_blink_and_trigger_lines():
    world = SimWorld()
    a, b = object(), object()
    world.set_blink(a, 50)
    world.set_blink(b, 10)  # only one driver
    t0, period = world._blink
    assert period == pytest.approx(0.02)
    assert world.blink_on_time(t0, t0 + period / 4) == pytest.approx(period / 4)
    assert world.blink_on_time(t0 + period / 2, t0 + period) == pytest.approx(0)
    assert world.blink_on_time(t0 - 1, t0 + 10 * period) == pytest.approx(5 * period)
    world.set_trigger(a, 100)
    edge0, period = world._trigger
    assert len(world.trigger_edges_between(edge0 - 1e-6, edge0 + 1 - 1e-6)) == 100
    world.set_trigger(b, 0)  # not the driver: no effect
    assert world.trigger_edges_between(edge0, edge0 + 0.05)
    world.set_trigger(a, 0)
    assert world.trigger_edges_between(edge0, edge0 + 0.05) == []


def _sim_open(world, devices):
    def fake_open(spec, name, sim_world=None):
        t = SimTransport(world, seed={"a": 11, "b": 22}[name])
        devices[name] = t.device
        return Camera(t, name)

    return fake_open


def test_triggered_recording_passes_the_sync_test(tmp_path, monkeypatch):
    devices = {}
    monkeypatch.setattr(record, "open_camera", _sim_open(SimWorld(seed=3), devices))
    out = tmp_path / "s"
    rc = record.main(["--out", str(out), "--cam", "a=sim", "--cam", "b=sim", "--duration", "8",
                      "--format", "raw", "--exp", "8", "--trigger", "25", "--blink", "43"])
    assert rc == 0
    meta = json.loads((out / "session.json").read_text())
    assert meta["trigger_driver"] == "a"
    assert all(c["device"]["extsync"] == 1 for c in meta["cameras"])
    assert all(abs(c["timing"]["fps"] - 25) < 0.01 for c in meta["cameras"])
    assert all(c["timing"]["exposure_ms"] == pytest.approx(8 / 30 / 128 * 1e3, rel=1e-3) for c in meta["cameras"])
    assert all(d.missed_triggers == 0 and not d.extsync for d in devices.values())  # EXTSYNC 0 at the end

    assert synctest.main([str(out)]) == 0
    (p,) = json.loads((out / "synctest.json").read_text())["pairs"]
    truth_us = (devices["b"].trigger_latency_s - devices["a"].trigger_latency_s) * 1e6
    assert p["verdict"] == synctest.PASS
    assert p["offset_us"] == pytest.approx(truth_us, abs=15)


def test_trigger_faster_than_the_sensor_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(record, "open_camera", _sim_open(SimWorld(), {}))
    rc = record.main(["--out", str(tmp_path / "s"), "--cam", "a=sim", "--cam", "b=sim", "--duration", "1",
                      "--format", "raw", "--trigger", "40"])
    assert rc == 1
    assert "not shorter than the trigger period" in capsys.readouterr().err
