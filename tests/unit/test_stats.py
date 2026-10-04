import pytest

from kzfires.eval.stats import wilson

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="ещё не реализовано")


def test_wilson_reference_value():
    lo, hi = wilson(270, 300)
    assert lo == pytest.approx(0.861, abs=1e-3)
    assert hi == pytest.approx(0.929, abs=1e-3)


@pytest.mark.parametrize(
    "k, n, lo_expected, hi_expected",
    [(45, 50, 0.79, 0.96), (90, 100, 0.83, 0.94), (180, 200, 0.85, 0.93)],
)
def test_wilson_planning_table(k, n, lo_expected, hi_expected):
    lo, hi = wilson(k, n)
    assert lo == pytest.approx(lo_expected, abs=0.005)
    assert hi == pytest.approx(hi_expected, abs=0.005)


def test_wilson_stays_inside_unit_interval():
    lo, hi = wilson(0, 10)
    assert 0.0 <= lo < 1e-9
    assert 0.0 < hi < 1.0
    lo, hi = wilson(10, 10)
    assert 0.0 < lo < 1.0
    assert 1.0 - 1e-9 < hi <= 1.0
