import pytest

from openfan_service.curves import apply_hysteresis, combine, evaluate, to_pwm

PTS = [(25.0, 25.0), (35.0, 40.0), (45.0, 70.0), (50.0, 100.0)]


@pytest.mark.parametrize("temp,expected", [
    (-10, 25), (25, 25), (30, 32.5), (35, 40), (36, 43), (45, 70), (47.5, 85), (50, 100), (90, 100),
])
def test_linear(temp, expected):
    assert evaluate(PTS, temp, "linear") == pytest.approx(expected)


@pytest.mark.parametrize("temp,expected", [(0, 25), (25, 25), (34.9, 25), (35, 40), (44, 40), (49, 70), (55, 100)])
def test_step(temp, expected):
    assert evaluate(PTS, temp, "step") == expected


def test_below_first_point_never_returns_zero():
    # The old service returned 0 (fan stop) below the first threshold point.
    assert evaluate([(40.0, 50.0)], 10, "step") == 50
    assert evaluate([(40.0, 50.0)], 10, "linear") == 50


def test_single_point_is_fixed_speed():
    for t in (-20, 0, 40, 100):
        assert evaluate([(0.0, 60.0)], t) == 60


def test_combine():
    assert combine([30, 40, 35], "max") == 40
    assert combine([30, 40, 35], "min") == 30
    assert combine([30, 40, 35], "avg") == pytest.approx(35)
    with pytest.raises(ValueError):
        combine([], "max")


def test_hysteresis_rises_immediately_and_falls_late():
    eff = apply_hysteresis(None, 40, 2)
    assert eff == 40
    assert apply_hysteresis(eff, 42, 2) == 42           # rise: follow at once
    assert apply_hysteresis(42, 41, 2) == 42            # small drop: hold
    assert apply_hysteresis(42, 40.5, 2) == 42
    assert apply_hysteresis(42, 40, 2) == 40            # dropped by 2: follow
    assert apply_hysteresis(42, 41, 0) == 41            # disabled


def test_to_pwm_clamps_and_applies_minimum():
    assert to_pwm(-5) == 0
    assert to_pwm(150) == 100
    assert to_pwm(10, min_pwm=20) == 20
    assert to_pwm(42.6) == 43
