"""Бот: подписка за три шага, /status, /stop, демо-тревога на архиве Абая.

Координаты подписчика в логи не пишутся: логируем только chat_id и шаг.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field

import psycopg

from kzfires.bot import texts_ru as t
from kzfires.bot.telegram import Telegram, TelegramError

log = logging.getLogger(__name__)

RADII = (5, 15, 30)


@dataclass
class Pending:
    lat: float | None = None
    lon: float | None = None
    radius: int | None = None


@dataclass
class Subscription:
    lat: float
    lon: float
    radius_km: int


class MemoryStore:
    def __init__(self):
        self.subs: dict[int, Subscription] = {}

    def save(self, chat_id: int, sub: Subscription) -> None:
        self.subs[chat_id] = sub

    def get(self, chat_id: int) -> Subscription | None:
        return self.subs.get(chat_id)

    def delete(self, chat_id: int) -> bool:
        return self.subs.pop(chat_id, None) is not None


class DbStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def save(self, chat_id: int, sub: Subscription) -> None:
        self.conn.execute(
            """
            INSERT INTO subscribers (tg_chat_id, consent_at, zone_center, radius_km)
            VALUES (%s, now(), ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)
            ON CONFLICT (tg_chat_id) DO UPDATE
              SET zone_center = EXCLUDED.zone_center, radius_km = EXCLUDED.radius_km,
                  consent_at = now(), active = true
            """,
            (chat_id, sub.lon, sub.lat, sub.radius_km),
        )
        self.conn.commit()

    def get(self, chat_id: int) -> Subscription | None:
        row = self.conn.execute(
            "SELECT ST_Y(zone_center), ST_X(zone_center), radius_km FROM subscribers "
            "WHERE tg_chat_id = %s AND active",
            (chat_id,),
        ).fetchone()
        return Subscription(*row) if row else None

    def delete(self, chat_id: int) -> bool:
        n = self.conn.execute("DELETE FROM subscribers WHERE tg_chat_id = %s", (chat_id,)).rowcount
        self.conn.commit()
        return n > 0


@dataclass
class Bot:
    tg: Telegram
    store: object
    demo: object = None  # (Subscription) -> list[str]
    last_data_at: object = None  # () -> datetime | None
    pending: dict[int, Pending] = field(default_factory=dict)

    def handle(self, update: dict) -> None:
        if "callback_query" in update:
            return self._callback(update["callback_query"])
        msg = update.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        if chat is None:
            return
        if "location" in msg:
            return self._location(chat, msg["location"])
        text = (msg.get("text") or "").strip()
        cmd = text.split()[0].split("@")[0] if text else ""
        if cmd == "/start":
            self.pending[chat] = Pending()
            log.info("start chat=%s", chat)
            self.tg.send(chat, t.WELCOME, _location_keyboard())
        elif cmd == "/stop":
            self.pending.pop(chat, None)
            gone = self.store.delete(chat)
            log.info("stop chat=%s deleted=%s", chat, gone)
            self.tg.send(chat, t.STOPPED if gone else t.NOT_SUBSCRIBED, {"remove_keyboard": True})
        elif cmd == "/status":
            sub = self.store.get(chat)
            if sub is None:
                self.tg.send(chat, t.NOT_SUBSCRIBED)
            else:
                at = self.last_data_at() if self.last_data_at else None
                age = (
                    "нет данных"
                    if at is None
                    else t.format_age((dt.datetime.now(dt.UTC) - at).total_seconds() / 60)
                )
                self.tg.send(chat, t.STATUS.format(radius=sub.radius_km, age=age))
        elif cmd == "/demo":
            sub = self.store.get(chat)
            if sub is None:
                self.tg.send(chat, t.NOT_SUBSCRIBED)
            elif self.demo is not None:
                log.info("demo chat=%s", chat)
                for text in self.demo(sub):
                    self.tg.send(chat, text)

    def _location(self, chat: int, loc: dict) -> None:
        p = self.pending.setdefault(chat, Pending())
        # округляем сразу: точнее ~1 км хранить незачем
        p.lat, p.lon = round(float(loc["latitude"]), 2), round(float(loc["longitude"]), 2)
        log.info("location chat=%s", chat)
        kb = {"inline_keyboard": [[{"text": f"{r} км", "callback_data": f"r:{r}"} for r in RADII]]}
        self.tg.send(chat, t.ASK_RADIUS, {"remove_keyboard": True})
        self.tg.send(chat, "Радиус:", kb)

    def _callback(self, cq: dict) -> None:
        chat = cq["message"]["chat"]["id"]
        data = cq.get("data", "")
        self.tg.answer_callback(cq["id"])
        p = self.pending.get(chat)
        if p is None or p.lat is None:
            self.tg.send(chat, t.NOT_SUBSCRIBED)
            return
        if data.startswith("r:"):
            p.radius = int(data[2:])
            if p.radius not in RADII:
                return
            kb = {"inline_keyboard": [[{"text": "Согласен", "callback_data": "consent"}]]}
            self.tg.send(chat, t.ASK_CONSENT, kb)
        elif data == "consent" and p.radius:
            self.store.save(chat, Subscription(p.lat, p.lon, p.radius))
            self.pending.pop(chat, None)
            log.info("subscribed chat=%s radius=%s", chat, p.radius)
            self.tg.send(chat, t.SUBSCRIBED.format(radius=p.radius))

    def poll_forever(self) -> None:
        offset = None
        while True:
            try:
                for u in self.tg.updates(offset):
                    offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except TelegramError as e:
                        log.warning("send failed: %s", e)
            except TelegramError as e:
                log.warning("poll failed: %s", e)


def _location_keyboard() -> dict:
    return {
        "keyboard": [[{"text": "Отправить геопозицию", "request_location": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
