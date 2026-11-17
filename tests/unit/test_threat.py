import datetime as dt

import pytest

from kzfires.core.events import Event
from kzfires.core.threat import ThreatParams, Wind, Zone, assess, bearing_deg, haversine_km

T0 = dt.datetime(2023, 6, 8, 6, 0, tzinfo=dt.UTC)
ZONE = Zone(lat=50.0, lon=80.0, radius_km=15.0)
P = ThreatParams()


def event_at(lat: float, lon: float) -> Event:
    return Event(
        id="e1",
        first_acq_at=T0,
        last_acq_at=T0,
        centroid_lat=lat,
        centroid_lon=lon,
        n_points=3,
        n_passes=1,
        max_frp=10.0,
    )


# 0,1° долготы на 50° с. ш. ≈ 7,15 км
WEST = event_at(50.0, 79.9)
EAST = event_at(50.0, 80.1)


def test_haversine_one_degree_latitude():
    assert haversine_km(50.0, 80.0, 51.0, 80.0) == pytest.approx(111.2, abs=0.2)


def test_bearing_cardinal_directions():
    assert bearing_deg(50.0, 80.0, 51.0, 80.0) == pytest.approx(0.0, abs=0.5)
    assert bearing_deg(50.0, 80.0, 50.0, 81.0) == pytest.approx(90.0, abs=0.5)
    assert bearing_deg(50.0, 80.0, 49.0, 80.0) == pytest.approx(180.0, abs=0.5)
    assert bearing_deg(50.0, 80.0, 50.0, 79.0) == pytest.approx(270.0, abs=0.5)


def test_west_wind_and_fire_to_the_west_is_toward():
    t = assess(WEST, ZONE, Wind(speed_ms=5.0, from_deg=270.0), 30, P)
    assert t.wind_class == "toward"
    assert t.level == "L2"
    assert t.bearing_deg == pytest.approx(270.0, abs=0.5)
    assert t.dist_km == pytest.approx(7.15, abs=0.1)


def test_west_wind_and_fire_to_the_east_is_away():
    t = assess(EAST, ZONE, Wind(speed_ms=5.0, from_deg=270.0), 30, P)
    assert t.wind_class == "away"
    assert t.level == "L1"


def test_wind_from_north_with_fire_to_the_west_is_across():
    t = assess(WEST, ZONE, Wind(speed_ms=5.0, from_deg=0.0), 30, P)
    assert t.wind_class == "across"
    assert t.level == "L1"


def test_toward_boundary_wraps_around_north():
    north = event_at(50.05, 80.0)
    t = assess(north, ZONE, Wind(speed_ms=5.0, from_deg=350.0), 30, P)
    assert t.wind_class == "toward"


def test_missing_wind_is_unknown_and_never_l2():
    for wind in (Wind(None, None), Wind(5.0, None), Wind(None, 270.0)):
        t = assess(WEST, ZONE, wind, 30, P)
        assert t.wind_class == "unknown"
        assert t.level == "L1"


def test_weak_wind_is_not_toward():
    t = assess(WEST, ZONE, Wind(speed_ms=1.0, from_deg=270.0), 30, P)
    assert t.wind_class != "toward"
    assert t.level == "L1"


def test_outside_radius_is_l0():
    far = event_at(50.0, 79.5)  # ≈ 36 км
    t = assess(far, ZONE, Wind(5.0, 270.0), 30, P)
    assert t.level == "L0"
    assert t.reasons.get("out_of_radius") is True


def test_masked_event_is_l0():
    t = assess(WEST, ZONE, Wind(5.0, 270.0), 30, P, in_mask=True)
    assert t.level == "L0"
    assert t.reasons.get("in_mask") is True


def test_distance_uses_nearest_point_of_event():
    e = event_at(50.0, 79.5)
    e.points = [(50.0, 79.5), (50.0, 79.9)]
    t = assess(e, ZONE, Wind(5.0, 270.0), 30, P)
    assert t.dist_km == pytest.approx(7.15, abs=0.1)
    assert t.level == "L2"
