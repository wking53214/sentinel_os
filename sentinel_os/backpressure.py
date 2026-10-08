"""backpressure.py -- queue-occupancy delay signal.

Given how full a bounded queue is, say how long a producer should wait
before adding more work. Below a high-watermark the answer is zero. Above
it, the signal ramps up linearly and reaches its maximum at full capacity.

This module only computes the signal. It does not sleep, drop work, or
reject requests. The caller decides what to do with the number.

Origin: determine_backpressure_delay from the STRIDE salvage. Changes from
the original: capacity and depth are validated, the watermark is checked,
and a seconds form with a hard ceiling is added so a caller can use the
result directly as a wait time.
"""

from __future__ import annotations

import math

__all__ = ["backpressure_delay_seconds", "backpressure_signal"]


def _check_capacity(capacity: int) -> None:
    if isinstance(capacity, bool) or not isinstance(capacity, int):
        raise TypeError("capacity must be an int")
    if capacity <= 0:
        raise ValueError("capacity must be positive")


def _check_depth(depth: int) -> None:
    if isinstance(depth, bool) or not isinstance(depth, int):
        raise TypeError("depth must be an int")
    if depth < 0:
        raise ValueError("depth must not be negative")


def _check_watermark(high_watermark: float) -> None:
    if not isinstance(high_watermark, (int, float)) or math.isnan(high_watermark):
        raise TypeError("high_watermark must be a number")
    if not 0.0 < high_watermark < 1.0:
        raise ValueError("high_watermark must be between 0 and 1, exclusive")


def backpressure_signal(
    depth: int, capacity: int, high_watermark: float = 0.85
) -> float:
    """Return a 0.0 to 1.0 pressure signal for a queue.

    0.0 means no delay is needed. At or below the watermark the signal is
    0.0. Between the watermark and full capacity it rises linearly. At or
    above capacity it is 1.0. Depth above capacity is clamped to 1.0.
    """
    _check_capacity(capacity)
    _check_depth(depth)
    _check_watermark(high_watermark)
    occupancy = depth / capacity
    if occupancy <= high_watermark:
        return 0.0
    overage = (occupancy - high_watermark) / (1.0 - high_watermark)
    return round(min(overage, 1.0), 4)


def backpressure_delay_seconds(
    depth: int,
    capacity: int,
    max_delay_seconds: float,
    high_watermark: float = 0.85,
) -> float:
    """Return how long a producer should wait, in seconds.

    The result is the pressure signal scaled by max_delay_seconds, so it
    never exceeds that ceiling. A ceiling of zero turns the signal off.
    """
    if isinstance(max_delay_seconds, bool) or not isinstance(
        max_delay_seconds, (int, float)
    ):
        raise TypeError("max_delay_seconds must be a number")
    if math.isnan(max_delay_seconds) or max_delay_seconds < 0:
        raise ValueError("max_delay_seconds must be zero or positive")
    signal = backpressure_signal(depth, capacity, high_watermark)
    return round(signal * max_delay_seconds, 4)
