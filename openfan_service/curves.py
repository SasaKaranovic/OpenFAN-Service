"""Pure fan-curve maths. No I/O, so it is easy to test exhaustively."""

from __future__ import annotations

from collections.abc import Sequence

Point = tuple[float, float]


def evaluate(points: Sequence[Point], temperature: float, curve_type: str = "linear") -> float:
    """Return the fan speed (0..100 %) for ``temperature``.

    ``points`` must be sorted by temperature (the config model guarantees it).
    Below the first point the first point's speed is used and above the last
    point the last point's speed is used, so a curve never returns 0 just
    because the temperature is outside the range the user drew.
    """
    if not points:
        raise ValueError("curve has no points")
    first_t, first_v = points[0]
    last_t, last_v = points[-1]
    if temperature <= first_t:
        return float(first_v)
    if temperature >= last_t:
        return float(last_v)

    if curve_type == "step":
        value = first_v
        for t, v in points:
            if temperature >= t:
                value = v
            else:
                break
        return float(value)

    for (t1, v1), (t2, v2) in zip(points, points[1:]):
        if t1 <= temperature <= t2:
            if t2 == t1:
                return float(v2)
            return v1 + (v2 - v1) * (temperature - t1) / (t2 - t1)
    return float(last_v)  # pragma: no cover - unreachable with sorted points


def combine(values: Sequence[float], how: str = "max") -> float:
    if not values:
        raise ValueError("no values to combine")
    if how == "min":
        return min(values)
    if how == "avg":
        return sum(values) / len(values)
    return max(values)


def apply_hysteresis(previous: float | None, current: float, hysteresis: float) -> float:
    """Return the temperature the curve should use.

    Rising temperatures are followed immediately. Falling temperatures are only
    followed once they have dropped by at least ``hysteresis`` degrees below the
    value currently in use, which stops fans hunting around a curve point.
    """
    if previous is None or hysteresis <= 0 or current >= previous:
        return current
    if previous - current >= hysteresis:
        return current
    return previous


def to_pwm(value: float, min_pwm: int = 0) -> int:
    """Clamp and round a curve value to an integer PWM percentage."""
    value = max(float(min_pwm), value)
    return int(round(min(100.0, max(0.0, value))))
