import datetime as dt

from kzfires.core.freshness import new_data_gap, poll_age, source_state

NOW = dt.datetime(2026, 10, 4, 17, 35, tzinfo=dt.UTC)
MIN = dt.timedelta(minutes=1)


def test_ages():
    assert poll_age(NOW - 5 * MIN, NOW) == 5 * MIN
    assert new_data_gap(NOW - 400 * MIN, NOW) == 400 * MIN
    assert poll_age(None, NOW) is None
    assert new_data_gap(None, NOW) is None


def test_never_polled_is_down():
    assert source_state(None, None, NOW) == "down"


def test_fresh_poll_is_ok_even_if_no_new_rows_for_hours():
    # 6,5 часа без новых строк между пролётами — норма
    assert source_state(NOW - 3 * MIN, NOW - 390 * MIN, NOW) == "ok"


def test_stale_poll_is_degraded_then_down():
    assert source_state(NOW - 45 * MIN, NOW - 60 * MIN, NOW) == "degraded"
    assert source_state(NOW - 4 * 60 * MIN, NOW - 5 * 60 * MIN, NOW) == "down"


def test_calibrated_gap_degrades():
    limit = dt.timedelta(hours=10)
    assert source_state(NOW - MIN, NOW - 11 * 60 * MIN, NOW, new_data_gap_limit=limit) == "degraded"
    assert source_state(NOW - MIN, NOW - 9 * 60 * MIN, NOW, new_data_gap_limit=limit) == "ok"
