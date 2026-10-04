import pytest

from kzfires.core.threat import wind_to_deg

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="ещё не реализовано")


@pytest.mark.parametrize(
    "dir_from, expected",
    [(270, 90), (90, 270), (0, 180), (180, 0), (359, 179), (360, 180)],
)
def test_wind_to_deg(dir_from, expected):
    assert wind_to_deg(dir_from) == pytest.approx(expected)
