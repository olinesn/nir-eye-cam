import numpy as np
import pytest

from nireyecam.align import AlignError, match_pulses


def _random_train(n, seed=0):
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.uniform(0.4e6, 1.0e6, n)) + 5e6


def test_recovers_offset_and_drift_with_jitter():
    true = _random_train(60)
    ref = true * (1 - 20e-6) + 1.5e9 + np.random.default_rng(1).normal(0, 5, true.size)
    cam = true * (1 + 35e-6) + 7.2e10 + np.random.default_rng(2).normal(0, 5, true.size)
    fit = match_pulses(ref, cam)
    assert fit.matched == 60
    assert fit.residual_rms_us < 15
    assert abs(fit.drift_ppm - (-55)) < 1  # ref/cam rate ratio
    np.testing.assert_allclose(fit.map(cam), ref, atol=30)


def test_cameras_that_started_at_different_times_and_missed_a_pulse():
    true = _random_train(80)
    ref = true[:60]                       # reference stopped early
    cam = np.delete(true[15:], 10) + 3e9  # started late, missed one edge
    fit = match_pulses(ref, cam)
    assert fit.matched == 44              # true[15:60] minus the missed one
    j = np.arange(40)  # cam pulse j is true pulse 15 + j, skipping the missed one
    np.testing.assert_allclose(fit.map(cam[:40]), ref[15 + j + (j >= 10)], atol=1)


def test_unrelated_trains_are_rejected():
    with pytest.raises(AlignError):
        match_pulses(_random_train(30, seed=1), _random_train(30, seed=2) + 1e9)


def test_too_few_pulses():
    with pytest.raises(AlignError, match="sync pulses"):
        match_pulses(_random_train(2), _random_train(2))


def test_periodic_external_ttl_pairs_by_index_and_warns():
    true = np.arange(20) * 1e6 + 3e6
    fit = match_pulses(true, true * (1 + 10e-6) + 2e9)
    assert fit.method == "index" and "periodic" in fit.warning
    assert abs(fit.drift_ppm + 10) < 0.1
