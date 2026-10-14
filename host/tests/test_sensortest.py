"""nireyecam-sensortest: each analysis recovers the parameters of a synthetic sensor."""

import csv
import json
import math

import numpy as np
import pytest

from nireyecam import sensortest as st

H, W = 48, 64


class FakeSensor:
    """Photons -> Poisson electrons (with QE, PRNU, shading, dark current) -> clip at the well
    -> plus DSNU and read noise -> DN with a black level."""

    def __init__(self, *, k=0.5, read_e=4.0, well_e=8000.0, qe=0.8, dark_e_s=50.0, black=64.0,
                 prnu=0.01, dsnu_e=2.0, corner=0.8, max_dn=4095, seed=1):
        self.rng = np.random.default_rng(seed)
        self.k, self.read_e, self.well_e, self.qe = k, read_e, well_e, qe
        self.dark_e_s, self.black, self.max_dn = dark_e_s, black, max_dn
        y, x = np.mgrid[0:H, 0:W]
        r2 = (((x - (W - 1) / 2) / ((W - 1) / 2)) ** 2 + ((y - (H - 1) / 2) / ((H - 1) / 2)) ** 2) / 2
        self.shading = 1 - (1 - corner) * r2                  # quadratic: 1 at the centre, `corner` at the corners
        self.prnu_map = 1 + prnu * self.rng.standard_normal((H, W))
        self.dsnu_map = dsnu_e * self.rng.standard_normal((H, W))

    def frames(self, n, photons_per_px, exposure_ms, dtype=np.uint16):
        """``photons_per_px`` may be a scalar, or one value per frame."""
        p = np.broadcast_to(np.asarray(photons_per_px, dtype=float).reshape(-1, 1, 1), (n, 1, 1))
        mean_e = self.qe * p * self.shading * self.prnu_map + self.dark_e_s * exposure_ms / 1e3
        e = np.minimum(self.rng.poisson(mean_e).astype(float), self.well_e)
        e = e + self.dsnu_map + self.rng.normal(0, self.read_e, e.shape)
        return np.clip(np.round(self.k * e + self.black), 0, self.max_dn).astype(dtype)


def _write_manifest(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


# ------------------------------------------------------------------ Part 1

def _ptc_fixture(tmp_path, sensor, name="S1"):
    wl, pix = 525.0, 2.0
    flux = 20_000.0                                  # photons per pixel per second
    irr = flux / st.photons_per_pixel(1.0, wl, pix, 1000.0)
    rows = []
    exposures = [float(e) for e in np.linspace(0, 700, 36)[1:]]   # saturates near 500 ms
    for i, e in enumerate(exposures):
        np.save(tmp_path / f"{name}-light-{i}.npy", sensor.frames(4, flux * e / 1e3, e))
        np.save(tmp_path / f"{name}-dark-{i}.npy", sensor.frames(4, 0.0, e))
        rows.append(dict(sensor=name, gain="1x", band="525/50", wavelength_nm=wl, pixel_um=pix, kind="light",
                         exposure_ms=e, irradiance_w_m2=irr, file=f"{name}-light-{i}.npy"))
        rows.append(dict(sensor=name, gain="1x", band="", wavelength_nm="", pixel_um="", kind="dark",
                         exposure_ms=e, irradiance_w_m2="", file=f"{name}-dark-{i}.npy"))
    # Many frames at about half of saturation, for PRNU, DSNU and the corners.
    np.save(tmp_path / f"{name}-light-half.npy", sensor.frames(32, flux * 0.25, 250.0))
    np.save(tmp_path / f"{name}-dark-half.npy", sensor.frames(32, 0.0, 250.0))
    light, dark = rows[0], rows[1]
    rows += [dict(light, exposure_ms=250.0, file=f"{name}-light-half.npy"),
             dict(dark, exposure_ms=250.0, file=f"{name}-dark-half.npy")]
    return rows


def test_photon_transfer_recovers_the_sensor(tmp_path):
    sensor = FakeSensor()
    manifest = _write_manifest(tmp_path / "ptc.csv", _ptc_fixture(tmp_path, sensor))
    rep = st.analyse_ptc(st.read_manifest(manifest), crop=0.3)

    (g,) = rep.groups
    assert (g.sensor, g.gain, g.band) == ("S1", "1x", "525/50")
    assert g.saturation_reached
    assert g.k_dn_per_e == pytest.approx(0.5, rel=0.03)
    # Read noise as EMVA measures it: read noise, the dark current's shot noise and quantization,
    # at the shortest exposure (20 ms).
    assert g.read_noise_e == pytest.approx(math.sqrt(16 + 50 * 0.020 + 1 / (12 * 0.5 ** 2)), rel=0.1)
    assert 0.85 * 8000 < g.saturation_e < 1.02 * 8000
    assert g.qe == pytest.approx(0.8, rel=0.03)              # the central crop is barely shaded
    assert g.linearity_error_pct < 1.0
    assert g.prnu_pct == pytest.approx(1.0, abs=0.25)        # the quadratic shading is removed first
    assert g.dsnu_e == pytest.approx(2.0, rel=0.25)
    assert g.corner_min == pytest.approx(0.8, abs=0.03)
    assert g.nonuniformity_exposure_ms == 250.0

    (d,) = rep.dark
    assert d.dark_current_e_s == pytest.approx(50.0, rel=0.05)
    assert d.black_level_dn == pytest.approx(64.0, abs=1.0)

    lines = st.ptc_markdown(rep).splitlines()
    assert any(line.startswith("| Conversion gain at 1x (e⁻/DN) | ") for line in lines)
    assert any(line.startswith("| QE through band 525/50 | 0.") for line in lines)


def test_full_frame_removes_shading_before_prnu(tmp_path):
    # Over the whole frame the shading runs from 1 down to 0.8: left in, it would read as ~5% PRNU.
    sensor = FakeSensor(seed=12)
    rep = st.analyse_ptc(st.read_manifest(_write_manifest(tmp_path / "ptc.csv", _ptc_fixture(tmp_path, sensor))))
    g = rep.groups[0]
    assert g.prnu_pct == pytest.approx(1.0, abs=0.15)
    assert g.dsnu_e == pytest.approx(2.0, rel=0.15)
    assert g.qe == pytest.approx(0.8 * float(sensor.shading.mean()), rel=0.03)   # QE of the average pixel


def test_two_sensors_side_by_side(tmp_path):
    rows = _ptc_fixture(tmp_path, FakeSensor(seed=2), "A") + _ptc_fixture(
        tmp_path, FakeSensor(k=0.25, qe=0.6, seed=3), "B")
    rep = st.analyse_ptc(st.read_manifest(_write_manifest(tmp_path / "ptc.csv", rows)), crop=0.3)
    by = {g.sensor: g for g in rep.groups}
    assert by["A"].qe == pytest.approx(0.8, rel=0.04) and by["B"].qe == pytest.approx(0.6, rel=0.04)
    assert by["B"].e_per_dn == pytest.approx(4.0, rel=0.04)
    header = next(line for line in st.ptc_markdown(rep).splitlines() if line.startswith("| |"))
    assert header == "| | A | B |"


def test_no_irradiance_means_no_qe_but_still_photon_transfer(tmp_path):
    rows = _ptc_fixture(tmp_path, FakeSensor(seed=4))
    for r in rows:
        r["irradiance_w_m2"] = ""
    rep = st.analyse_ptc(st.read_manifest(_write_manifest(tmp_path / "ptc.csv", rows)), crop=0.3)
    g = rep.groups[0]
    assert g.qe is None and g.k_dn_per_e == pytest.approx(0.5, rel=0.03)
    assert any("no irradiance" in n for n in g.notes)


# ------------------------------------------------------------------ Part 2

def _expected_snr(sensor, photons_per_um2, sample_px_um, npx, exposure_ms):
    e = sensor.qe * photons_per_um2 * sample_px_um ** 2 * npx
    per_px_var = sensor.read_e ** 2 + sensor.dark_e_s * exposure_ms / 1e3 + 1 / (12 * sensor.k ** 2)
    return e / math.sqrt(e + npx * per_px_var)


def test_snr_per_cell_and_power_for_a_target(tmp_path):
    sensor = FakeSensor(prnu=0.0, dsnu_e=0.0, corner=1.0, seed=5)
    px = 1.35                                          # um of sample per pixel
    np.save(tmp_path / "dark.npy", sensor.frames(32, 0.0, 30.0))
    density = {0.5: 5.0, 2.0: 20.0}                    # mW/mm2 -> photons per um2 per frame
    rows = []
    for power, d in density.items():
        np.save(tmp_path / f"run-{power}.npy", sensor.frames(200, d * px * px, 30.0))
        rows.append(dict(sensor="S", channel="red", power_mw_mm2=power, sample_px_um=px,
                         file=f"run-{power}.npy", dark="dark.npy", k_dn_per_e=0.5, fps="", period_frames=""))
    rep = st.analyse_snr(st.read_manifest(_write_manifest(tmp_path / "snr.csv", rows)), target_snr=30)

    lo, hi = rep.runs
    assert lo.regions == 25 and 55 < lo.pixels_per_region < 70
    for run, d in ((lo, 5.0), (hi, 20.0)):
        assert run.snr == pytest.approx(_expected_snr(sensor, d, px, run.pixels_per_region, 30.0), rel=0.06)
        assert run.signal_e == pytest.approx(0.8 * d * px * px * run.pixels_per_region, rel=0.05)
    (t,) = rep.targets
    assert lo.snr < 30 < hi.snr and 0.5 < t.power_mw_mm2 < 2.0
    # The model's own answer: solve SNR(P) = 30 with SNR = aP / sqrt(aP + b).
    a = 0.8 * 10.0 * px * px * lo.pixels_per_region       # electrons per (mW/mm2)
    b = lo.pixels_per_region * (16 + 50 * 0.03 + 1 / 3)
    exact = (900 + math.sqrt(900 ** 2 + 4 * 900 * b)) / 2 / a
    assert t.power_mw_mm2 == pytest.approx(exact, rel=0.1)
    assert "## Power for SNR 30 per cell" in st.snr_markdown(rep)


def test_snr_without_dark_warns(tmp_path):
    sensor = FakeSensor(prnu=0.0, dsnu_e=0.0, corner=1.0, seed=6)
    np.save(tmp_path / "run.npy", sensor.frames(20, 4.0, 30.0))
    rows = [dict(sensor="S", channel="green", power_mw_mm2=1, sample_px_um=1.35, file="run.npy")]
    rep = st.analyse_snr(st.read_manifest(_write_manifest(tmp_path / "snr.csv", rows)))
    assert any("black level" in n for n in rep.runs[0].notes)


def test_small_led_step_is_found_at_any_phase(tmp_path):
    sensor = FakeSensor(prnu=0.0, dsnu_e=0.0, corner=1.0, seed=7)
    period, n = 10, 400
    level = np.where(((np.arange(n) + 3) // period) % 2 == 0, 1.02, 0.98)   # +-2%, starting mid-block
    np.save(tmp_path / "step.npy", sensor.frames(n, 500.0 * level, 30.0))
    np.save(tmp_path / "dark.npy", sensor.frames(16, 0.0, 30.0))
    rows = [dict(sensor="S", channel="green", power_mw_mm2=1, sample_px_um=1.35, file="step.npy",
                 dark="dark.npy", period_frames=period)]
    run = st.analyse_snr(st.read_manifest(_write_manifest(tmp_path / "snr.csv", rows))).runs[0]
    assert run.step_pct == pytest.approx(4.08, abs=0.4)
    assert run.dprime > 5 and run.snr is None


def test_drift_and_first_minute(tmp_path):
    sensor = FakeSensor(prnu=0.0, dsnu_e=0.0, corner=1.0, seed=8)
    fps, n = 10, 1300                                   # 130 s
    level = 1 + 0.01 * np.arange(n) / fps / 60          # +1% per minute
    np.save(tmp_path / "long.npy", sensor.frames(n, 200.0 * level, 30.0))
    np.save(tmp_path / "dark.npy", sensor.frames(16, 0.0, 30.0))
    rows = [dict(sensor="S", channel="green", power_mw_mm2=1, sample_px_um=1.35, file="long.npy",
                 dark="dark.npy", fps=fps)]
    run = st.analyse_snr(st.read_manifest(_write_manifest(tmp_path / "snr.csv", rows))).runs[0]
    assert run.drift_per == "minute"
    assert run.drift_pct == pytest.approx(1.0, rel=0.1)
    assert run.first_minute_pct == pytest.approx(55 / 60, abs=0.15)


# ------------------------------------------------------------------ Part 3

def _camera_folder(path, frames, seq):
    path.mkdir()
    frames.astype(np.uint8).tofile(path / "frames.u8")
    (path / "frames.u8.json").write_text(json.dumps({"dtype": "uint8", "width": W, "height": H}))
    with open(path / "frames.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "seq", "device_t_us", "host_t_ns", "exposure_lines", "again", "ae"])
        for i, s in enumerate(seq):
            w.writerow([i, s, s * 16_667, 0, 100, 0, 0])
    return path


def test_alternation_labels_drops_and_a_wrong_frame(tmp_path):
    sensor = FakeSensor(k=0.5, black=16.0, prnu=0.0, dsnu_e=0.0, corner=1.0, max_dn=255, seed=9)
    seq = np.array([s for s in range(400) if s not in (57, 58, 200)])   # 3 dropped frames
    blue = seq % 2 == 0
    blue[100] = not blue[100]                                           # one frame lit by the wrong LED
    frames = np.empty((len(seq), H, W), dtype=np.uint16)
    for i, b in enumerate(blue):
        f = sensor.frames(1, 0.0, 0.0)[0].astype(float)
        green_e, red_e = (300.0, 40.0) if b else (30.0, 280.0)
        f[:, : W // 2] += 0.5 * green_e
        f[:, W // 2:] += 0.5 * red_e
        frames[i] = np.clip(f, 0, 255)
    folder = _camera_folder(tmp_path / "cam", frames, seq)
    rep = st.analyse_alternation(folder, green_roi=(4, 4, 28, 44), red_roi=(36, 4, 60, 44))
    assert rep.labels_from == "sensor frame counter"
    assert rep.dropped == 3
    assert rep.even_frames == "blue"
    assert rep.mislabelled == 1 and rep.mislabelled_frames == [100]
    assert rep.separation > st.MIN_SEPARATION
    assert "**1**" in st.alternation_markdown(rep)


def test_leakage_on_a_one_colour_target(tmp_path):
    sensor = FakeSensor(prnu=0.0, dsnu_e=0.0, corner=1.0, seed=10)
    n = 200
    level = np.where(np.arange(n) % 2 == 1, 1000.0, 20.0) / 0.8     # odd frames lit, even frames 2% of that
    np.save(tmp_path / "red.npy", sensor.frames(n, level, 0.0))
    np.save(tmp_path / "dark.npy", sensor.frames(16, 0.0, 0.0))
    rep = st.analyse_alternation(tmp_path / "red.npy", dark_path=tmp_path / "dark.npy", leak_roi=(0, 0, W, H))
    assert rep.labels_from == "frame index" and rep.dropped is None
    assert rep.leakage_bright_parity == "odd"
    assert rep.leakage_pct == pytest.approx(2.0, abs=0.1)


def test_alternation_needs_a_region(tmp_path):
    with pytest.raises(st.InputError, match="green-roi"):
        st.analyse_alternation(tmp_path / "none.npy")


# ------------------------------------------------------------------ input and command line

def test_raw_frames_need_a_size(tmp_path):
    frames = np.arange(3 * H * W, dtype="<u2").reshape(3, H, W)
    frames.tofile(tmp_path / "f.u16")
    with pytest.raises(st.InputError, match="--size"):
        st.load_stack(tmp_path / "f.u16")
    a, seq = st.load_stack(tmp_path / "f.u16", size=(W, H))
    assert a.shape == (3, H, W) and seq is None and int(a[2, H - 1, W - 1]) == 3 * H * W - 1
    with pytest.raises(st.InputError, match="whole number"):
        st.load_stack(tmp_path / "f.u16", size=(W + 1, H))


def test_manifest_errors_name_the_line(tmp_path):
    p = _write_manifest(tmp_path / "m.csv", [dict(sensor="S", kind="light", exposure_ms="ten", file="x.npy")])
    with pytest.raises(st.InputError, match="line 2.*exposure_ms"):
        st.analyse_ptc(st.read_manifest(p))


def test_command_line_writes_report_and_json(tmp_path, capsys):
    manifest = _write_manifest(tmp_path / "ptc.csv", _ptc_fixture(tmp_path, FakeSensor(seed=11)))
    out, js = tmp_path / "r.md", tmp_path / "r.json"
    assert st.main(["ptc", str(manifest), "--crop", "0.3", "--out", str(out), "--json", str(js)]) == 0
    assert out.read_text().startswith("# Sensor bench test: Part 1")
    data = json.loads(js.read_text())
    assert data["groups"][0]["k_dn_per_e"] == pytest.approx(0.5, rel=0.03)
    assert "nireyecam-sensortest ptc" in data["command"]
    assert st.main(["alternation", str(tmp_path / "missing")]) == 2
    assert "error:" in capsys.readouterr().err
