"""Tests for sentinel_os.backpressure."""

import math

import pytest

from sentinel_os.backpressure import backpressure_delay_seconds, backpressure_signal


def test_no_pressure_below_watermark():
    assert backpressure_signal(0, 100) == 0.0
    assert backpressure_signal(80, 100) == 0.0


def test_no_pressure_exactly_at_watermark():
    assert backpressure_signal(85, 100) == 0.0


def test_signal_rises_between_watermark_and_full():
    assert backpressure_signal(92, 100) == pytest.approx(0.4667, abs=1e-4)
    assert backpressure_signal(95, 100) == pytest.approx(0.6667, abs=1e-4)


def test_signal_is_one_at_capacity():
    assert backpressure_signal(100, 100) == 1.0


def test_signal_is_clamped_above_capacity():
    assert backpressure_signal(250, 100) == 1.0


def test_signal_is_monotonic_in_depth():
    values = [backpressure_signal(d, 100) for d in range(101)]
    assert values == sorted(values)


def test_custom_watermark_is_respected():
    assert backpressure_signal(50, 100, high_watermark=0.5) == 0.0
    assert backpressure_signal(75, 100, high_watermark=0.5) == 0.5


@pytest.mark.parametrize("capacity", [0, -1])
def test_non_positive_capacity_rejected(capacity):
    with pytest.raises(ValueError):
        backpressure_signal(1, capacity)


def test_negative_depth_rejected():
    with pytest.raises(ValueError):
        backpressure_signal(-1, 10)


@pytest.mark.parametrize("watermark", [0.0, 1.0, 1.5, -0.2, math.nan])
def test_bad_watermark_rejected(watermark):
    with pytest.raises((ValueError, TypeError)):
        backpressure_signal(5, 10, high_watermark=watermark)


@pytest.mark.parametrize("bad", [True, 1.5, "10"])
def test_non_integer_inputs_rejected(bad):
    with pytest.raises(TypeError):
        backpressure_signal(bad, 10)
    with pytest.raises(TypeError):
        backpressure_signal(5, bad)


def test_delay_seconds_scales_signal_to_ceiling():
    assert backpressure_delay_seconds(0, 100, max_delay_seconds=2.0) == 0.0
    assert backpressure_delay_seconds(100, 100, max_delay_seconds=2.0) == 2.0
    assert backpressure_delay_seconds(95, 100, max_delay_seconds=3.0) == round(
        backpressure_signal(95, 100) * 3.0, 4
    )


def test_delay_never_exceeds_ceiling():
    for depth in range(0, 301, 7):
        assert backpressure_delay_seconds(depth, 100, max_delay_seconds=0.5) <= 0.5


def test_zero_ceiling_turns_signal_off():
    assert backpressure_delay_seconds(100, 100, max_delay_seconds=0.0) == 0.0


@pytest.mark.parametrize("bad", [-1.0, math.nan, True])
def test_bad_ceiling_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        backpressure_delay_seconds(10, 100, max_delay_seconds=bad)
