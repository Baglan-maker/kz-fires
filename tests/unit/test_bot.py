import datetime as dt
import logging

import pytest

from kzfires.bot import texts_ru as t
from kzfires.bot.app import Bot, MemoryStore
from kzfires.bot.telegram import Telegram, TelegramError

# синтетический подписчик, не реальное место
LAT, LON = 43.123456, 76.987654
TOKEN = "123456:SECRET-token-value"


def view(**kw):
    base = dict(
        dist_km=7.2,
        bearing_deg=270.0,
        acq_at=dt.datetime(2023, 6, 8, 6, 53, tzinfo=dt.UTC),
        data_age_min=185.0,
        land_cover=30,
        wind_from_deg=270.0,
        wind_speed_ms=5.0,
        wind_class="toward",
        lat=50.55,
        lon=80.62,
    )
    base.update(kw)
    return t.AlertView(**base)


def test_alert_has_mandatory_lines():
    text = t.render_alert(view())
    assert "возраст 3 ч 05 мин" in text
    assert t.DISCLAIMER in text
    assert t.SOURCE_LINE in text
    assert "NASA FIRMS" in text
    assert "западнее" in text
    assert "в вашу сторону" in text
    # Asia/Almaty: в 2023 году UTC+6, с 1 марта 2024 года UTC+5
    assert "08.06 12:53" in text
    later = t.render_alert(view(acq_at=dt.datetime(2025, 6, 8, 6, 53, tzinfo=dt.UTC)))
    assert "08.06 11:53" in later


def test_alert_without_age_is_impossible():
    with pytest.raises(ValueError):
        t.render_alert(view(data_age_min=None))


def test_unknown_wind_never_says_toward():
    text = t.render_alert(view(wind_from_deg=None, wind_class="unknown"))
    assert "в вашу сторону" not in text
    assert "Ветер: нет данных" in text


@pytest.mark.parametrize("word", ["оповещени", "эвакуац", "дойдёт", "%", "система раннего"])
def test_forbidden_words(word):
    texts = [t.render_alert(view()), t.render_alert(view(land_cover=50, is_update=True))]
    texts += [t.WELCOME, t.ASK_CONSENT, t.SUBSCRIBED, t.STOPPED, t.STATUS]
    for text in texts:
        assert word not in text.lower()


def test_industrial_land_marks_maybe_not_fire():
    assert "Возможно, не пожар" in t.render_alert(view(land_cover=50))


class FakeTg:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    def answer_callback(self, _id):
        pass


def flow(bot, chat=42):
    bot.handle({"message": {"chat": {"id": chat}, "text": "/start"}})
    bot.handle({"message": {"chat": {"id": chat}, "location": {"latitude": LAT, "longitude": LON}}})
    bot.handle({"callback_query": {"id": "1", "data": "r:15", "message": {"chat": {"id": chat}}}})
    bot.handle(
        {"callback_query": {"id": "2", "data": "consent", "message": {"chat": {"id": chat}}}}
    )


def test_subscribe_status_stop(caplog):
    caplog.set_level(logging.DEBUG)
    tg, store = FakeTg(), MemoryStore()
    bot = Bot(tg, store)
    flow(bot)
    sub = store.get(42)
    assert (sub.lat, sub.lon, sub.radius_km) == (43.12, 76.99, 15)
    bot.handle({"message": {"chat": {"id": 42}, "text": "/stop"}})
    assert store.get(42) is None
    assert tg.sent[-1][1] == t.STOPPED
    # координаты подписчика и их округления не попадают в логи
    for s in ("43.12", "76.98", "76.99", "43.1", "77.0"):
        assert s not in caplog.text


def test_consent_required():
    tg, store = FakeTg(), MemoryStore()
    bot = Bot(tg, store)
    bot.handle({"message": {"chat": {"id": 7}, "text": "/start"}})
    bot.handle({"message": {"chat": {"id": 7}, "location": {"latitude": LAT, "longitude": LON}}})
    bot.handle({"callback_query": {"id": "1", "data": "r:5", "message": {"chat": {"id": 7}}}})
    assert store.get(7) is None


class FakeResp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, data=None, exc=None):
        self.data, self.exc = data, exc

    def post(self, url, json=None, timeout=None):
        if self.exc:
            raise self.exc
        return FakeResp(self.data)


def test_token_hidden_in_errors():
    import requests

    tg = Telegram(TOKEN, FakeSession(exc=requests.ConnectionError(f"/bot{TOKEN}/sendMessage")))
    with pytest.raises(TelegramError) as ei:
        tg.send(1, "x")
    assert TOKEN not in str(ei.value)
    assert ei.value.__suppress_context__
    tg = Telegram(TOKEN, FakeSession({"ok": False, "error_code": 401, "description": TOKEN}))
    with pytest.raises(TelegramError) as ei:
        tg.send(1, "x")
    assert TOKEN not in str(ei.value)
    assert TOKEN not in repr(tg)


class RecordingSession:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json, timeout))
        return FakeResp({"ok": True, "result": self.result})


def test_get_updates_sends_long_poll_timeout_separately():
    s = RecordingSession([])
    Telegram(TOKEN, s).updates(offset=5, timeout=30)
    method, body, http_timeout = s.calls[0]
    assert method == "getUpdates"
    assert body["timeout"] == 30
    assert body["offset"] == 5
    assert http_timeout > 30


class UpdatesTg(FakeTg):
    def __init__(self, batch):
        super().__init__()
        self.batch = batch

    def updates(self, offset, timeout=50):
        return self.batch


def test_poll_survives_broken_update(caplog):
    class Boom(MemoryStore):
        def get(self, chat_id):
            raise RuntimeError(f"secret {LAT}")

    batch = [
        {"update_id": 1, "message": {"chat": {"id": 1}, "text": "/status"}},
        {"update_id": 2, "message": {"chat": {"id": 2}, "text": "/start"}},
    ]
    tg = UpdatesTg(batch)
    bot = Bot(tg, Boom())
    assert bot.poll_once(None) == 3
    assert tg.sent[-1][1] == t.WELCOME
    assert str(LAT) not in caplog.text
