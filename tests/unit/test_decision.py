import datetime as dt

from kzfires.core.decision import DecisionParams, SentMessage, decide
from kzfires.core.events import Event
from kzfires.core.threat import Threat

NOW = dt.datetime(2023, 6, 8, 8, 0, tzinfo=dt.UTC)
P = DecisionParams()


def ev(n_points: int = 3, age_h: float = 1.0, eid: str = "e1") -> Event:
    t = NOW - dt.timedelta(hours=age_h)
    return Event(eid, t, t, 50.0, 79.9, n_points, 1, 10.0)


def th(level: str = "L1", **reasons) -> Threat:
    return Threat(7.0, 270.0, "away", 30, level, reasons)


def sent(hours_ago: float, level: str = "L1", n: int = 3, eid: str = "e1") -> SentMessage:
    return SentMessage(eid, NOW - dt.timedelta(hours=hours_ago), level, n)


def test_first_alert_has_data_age():
    d = decide(ev(), th(), [], NOW, P)
    assert d.outcome == "alert"
    assert d.data_age_min == 60.0


def test_alert_never_without_age():
    for level in ("L1", "L2"):
        d = decide(ev(), th(level), [], NOW, P)
        if d.outcome in ("alert", "update"):
            assert d.data_age_min is not None


def test_masked_event_suppressed():
    d = decide(ev(), th("L0", in_mask=True), [], NOW, P)
    assert (d.outcome, d.reason) == ("suppress", "in_mask")


def test_out_of_radius_suppressed():
    d = decide(ev(), th("L0", out_of_radius=True), [], NOW, P)
    assert (d.outcome, d.reason) == ("suppress", "out_of_radius")


def test_few_points_suppressed():
    d = decide(ev(n_points=1), th(), [], NOW, P)
    assert (d.outcome, d.reason) == ("suppress", "few_points")


def test_old_data_suppressed():
    d = decide(ev(age_h=13), th(), [], NOW, P)
    assert (d.outcome, d.reason) == ("suppress", "too_old")


def test_same_event_same_level_is_duplicate():
    d = decide(ev(), th(), [sent(4)], NOW, P)
    assert (d.outcome, d.reason) == ("suppress", "duplicate")


def test_level_rise_gives_update_after_gap():
    d = decide(ev(), th("L2"), [sent(4, "L1")], NOW, P)
    assert d.outcome == "update"
    assert d.data_age_min is not None


def test_level_rise_too_soon_is_duplicate():
    d = decide(ev(), th("L2"), [sent(1, "L1")], NOW, P)
    assert (d.outcome, d.reason) == ("suppress", "duplicate")


def test_doubling_points_gives_update():
    d = decide(ev(n_points=6), th("L1"), [sent(4, "L1", n=3)], NOW, P)
    assert d.outcome == "update"


def test_level_drop_does_not_flicker():
    # гистерезис: после L2 падение до L1 и обратный рост не дают новых сообщений
    hist = [sent(5, "L1"), sent(4, "L2")]
    assert decide(ev(), th("L1"), hist, NOW, P).outcome == "suppress"
    assert decide(ev(), th("L2"), hist, NOW, P).outcome == "suppress"


def test_rate_limit_counts_all_events():
    hist = [sent(1, eid="a"), sent(2, eid="b"), sent(3, eid="c")]
    d = decide(ev(eid="new"), th(), hist, NOW, P)
    assert (d.outcome, d.reason) == ("suppress", "rate_limit")


def test_rate_limit_window_is_24h():
    hist = [sent(25, eid="a"), sent(26, eid="b"), sent(27, eid="c")]
    assert decide(ev(eid="new"), th(), hist, NOW, P).outcome == "alert"
