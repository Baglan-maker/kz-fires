import datetime as dt
from concurrent.futures import Future

import pandas as pd
import pytest

from kzfires.labeling import store
from kzfires.labeling.server import LabelApp, Session

NOW = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.UTC)
POINT = {
    "label_id": "dev-001",
    "set": "dev",
    "stratum": "S2",
    "period": "P1",
    "satellite": "N",
    "acq_at": "2023-06-08T06:53:00Z",
    "lat": "50.55674",
    "lon": "80.62470",
}
CARD = {
    "pre": {"date": "2023-06-06", "footprint_cloud": 0.0},
    "post": {"date": "2023-06-14", "footprint_cloud": 12.5},
    "dnbr": 0.123456,
    "worldcover": {"majority": 10},
}


def form(**kw):
    base = {
        "class": 1,
        "label_confidence": 3,
        "evidence": "  пятно \n появилось ",
        "seconds_spent": 41.6,
    }
    base.update(kw)
    return base


def test_make_row_fills_schema_from_point_and_card():
    row = store.make_row(POINT, CARD, form(), pass_no=1, now=NOW)
    assert list(row) == store.LABEL_COLUMNS
    assert row["lat"] == "50.55674" and row["lon"] == "80.62470"
    assert row["s2_pre_date"] == "2023-06-06" and row["cloud_post"] == 12.5
    assert row["dnbr"] == 0.1235 and row["worldcover"] == 10
    assert row["evidence"] == "пятно появилось"
    assert row["seconds_spent"] == 42
    assert row["labeled_at"] == "2026-10-10T12:00:00+00:00"


@pytest.mark.parametrize(
    "bad", [{"class": 5}, {"class": 0}, {"label_confidence": 4}, {"seconds_spent": -1}]
)
def test_make_row_rejects_invalid_input(bad):
    with pytest.raises(ValueError):
        store.make_row(POINT, CARD, form(**bad), pass_no=1, now=NOW)


def test_make_row_without_card_is_allowed_for_unclear_points():
    row = store.make_row(POINT, {}, form(**{"class": 4, "label_confidence": 1}), 1, NOW)
    assert row["class"] == 4 and row["dnbr"] == "" and row["s2_pre_date"] == ""


def test_append_writes_header_once_and_last_row_wins(tmp_path):
    path = tmp_path / "labels" / "labels_dev.csv"
    store.append(path, store.make_row(POINT, CARD, form(**{"class": 1}), 1, NOW))
    store.append(path, store.make_row(POINT, CARD, form(**{"class": 2}), 1, NOW))
    text = path.read_text()
    assert text.count("label_id,") == 1
    labels = store.read_labels(path)
    assert len(labels) == 2
    latest = store.latest(labels)
    assert latest["class"].tolist() == ["2"]


def test_evidence_with_commas_survives_csv(tmp_path):
    path = tmp_path / "l.csv"
    store.append(
        path, store.make_row(POINT, CARD, form(evidence='края ровные, "поле"; дым'), 1, NOW)
    )
    assert store.read_labels(path)["evidence"].iloc[0] == 'края ровные, "поле"; дым'


def todo_of(n: int) -> pd.DataFrame:
    return pd.DataFrame({"label_id": [f"dev-{i:03d}" for i in range(1, n + 1)]})


def labels_for(ids, pass_no=1, start=NOW):
    return pd.DataFrame(
        {
            "label_id": ids,
            "pass": str(pass_no),
            "labeled_at": [(start + dt.timedelta(minutes=i)).isoformat() for i in range(len(ids))],
        }
    )


def test_queue_first_pass_keeps_todo_order_and_skips_done():
    todo = todo_of(5)
    labels = labels_for(["dev-002", "dev-001"])
    assert store.queue(todo, labels, 1) == ["dev-003", "dev-004", "dev-005"]


def test_second_pass_relabels_first_sixty_in_new_order():
    todo = todo_of(100)
    first_pass = labels_for([f"dev-{i:03d}" for i in range(1, 81)])
    q = store.queue(todo, first_pass, 2)
    assert sorted(q) == [f"dev-{i:03d}" for i in range(1, 61)]
    assert q != sorted(q)
    assert q == store.queue(todo, first_pass, 2)
    done_two = labels_for(q[:5], pass_no=2, start=NOW + dt.timedelta(days=8))
    assert store.queue(todo, pd.concat([first_pass, done_two]), 2) == q[5:]


def test_days_since_first_label():
    labels = labels_for(["dev-001", "dev-002"])
    assert store.days_since_first_label(labels, NOW + dt.timedelta(days=7)) == pytest.approx(7.0)
    assert store.days_since_first_label(labels_for([]), NOW) is None


def offline_app(tmp_path, practice: bool) -> LabelApp:
    """LabelApp без сети: карточка уже готова, каталог и OSM не нужны."""
    todo = pd.DataFrame(
        [{**POINT, "fire_type": "2", "confidence": "h", "frp": "103.4", "hint": "факел"}]
    )
    todo_path = tmp_path / "todo.csv"
    todo.to_csv(todo_path, index=False)
    app = LabelApp.__new__(LabelApp)
    app.s = Session("dev", todo_path, tmp_path / "labels.csv", tmp_path / "cache", 1, practice)
    app.todo = store.read_todo(todo_path)
    app.points = {r["label_id"]: r for r in app.todo.to_dict("records")}
    done = Future()
    done.set_result(CARD)
    app.card_future = lambda label_id: done
    return app


def test_point_payload_hides_firms_fields(tmp_path):
    payload = offline_app(tmp_path, practice=False).point("dev-001")
    assert {"fire_type", "confidence", "frp", "hint", "stratum"}.isdisjoint(payload)
    assert payload["lat"] == "50.55674" and payload["card"] == CARD


def test_practice_payload_shows_hint(tmp_path):
    payload = offline_app(tmp_path, practice=True).point("dev-001")
    assert payload["hint"] == "факел"
    assert "fire_type" not in payload
