"""Тексты сообщений бота.

Обязательные строки (возраст данных, оговорка про 112, источник) вставляет
сам шаблон; собрать сообщение без них нельзя.
"""

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from kzfires.config import FIRMS_ATTRIBUTION

ALMATY = ZoneInfo("Asia/Almaty")

DISCLAIMER = "Неофициальная информация, источник не подтверждён. При опасности звоните 112."
SOURCE_LINE = f"Сообщение сформировано автоматически. Данные: {FIRMS_ATTRIBUTION}."

SIDES = ["севернее", "северо-восточнее", "восточнее", "юго-восточнее",
         "южнее", "юго-западнее", "западнее", "северо-западнее"]  # fmt: skip
WIND_FROM = ["северный", "северо-восточный", "восточный", "юго-восточный",
             "южный", "юго-западный", "западный", "северо-западный"]  # fmt: skip
LAND = {10: "лес", 20: "степь", 30: "степь", 40: "поле", 50: "населённый пункт"}
MAYBE_NOT_FIRE = {50, 60}


def _octant(deg: float) -> int:
    return int(((deg % 360) + 22.5) // 45) % 8


def format_age(minutes: float) -> str:
    m = int(round(minutes))
    if m < 60:
        return f"{m} мин"
    h, m = divmod(m, 60)
    return f"{h} ч {m:02d} мин"


@dataclass(frozen=True)
class AlertView:
    dist_km: float
    bearing_deg: float
    acq_at: dt.datetime
    data_age_min: float
    land_cover: int | None
    wind_from_deg: float | None
    wind_speed_ms: float | None
    wind_class: str
    lat: float
    lon: float
    is_update: bool = False
    demo_note: str | None = None


def render_alert(v: AlertView) -> str:
    if v.data_age_min is None:
        raise ValueError("тревога без возраста данных не отправляется")
    head = "Обновление: " if v.is_update else ""
    lines = []
    if v.demo_note:
        lines.append(v.demo_note)
    lines.append(
        f"{head}Горячая точка в {v.dist_km:.0f} км {SIDES[_octant(v.bearing_deg)]} от вашей точки."
    )
    local = v.acq_at.astimezone(ALMATY)
    lines.append(f"Данные спутника: {local:%d.%m %H:%M}, возраст {format_age(v.data_age_min)}.")
    land = LAND.get(v.land_cover, "нет данных") if v.land_cover is not None else "нет данных"
    if v.wind_from_deg is None or v.wind_speed_ms is None:
        wind = "нет данных"
    else:
        wind = f"{WIND_FROM[_octant(v.wind_from_deg)]}, {v.wind_speed_ms:.0f} м/с"
        if v.wind_class == "toward":
            wind += ", в вашу сторону"
    lines.append(f"Тип земли: {land}. Ветер: {wind}.")
    if v.land_cover in MAYBE_NOT_FIRE:
        lines.append("Возможно, не пожар: рядом может быть промышленный источник тепла.")
    lines.append(f"Карта: {map_link(v.lat, v.lon)}")
    lines.append(DISCLAIMER)
    lines.append(SOURCE_LINE)
    return "\n".join(lines)


def map_link(lat: float, lon: float) -> str:
    return (
        f"https://www.openstreetmap.org/?mlat={lat:.4f}&mlon={lon:.4f}#map=12/{lat:.4f}/{lon:.4f}"
    )


WELCOME = (
    "Бот присылает сообщение, если спутник NASA видит горячую точку рядом с выбранным "
    "местом. Это учебный проект, а не служба спасения.\n\n"
    "Отправьте геопозицию кнопкой ниже. Храним только точку, округлённую примерно до 1 км, "
    "и радиус. /stop удаляет всё."
)
ASK_RADIUS = "В каком радиусе сообщать?"
ASK_CONSENT = (
    "Сохраняем округлённую точку и радиус, чтобы присылать сообщения. Согласны? "
    "Удалить данные можно командой /stop."
)
SUBSCRIBED = "Готово. Радиус {radius} км. /status — что сохранено, /stop — удалить."
STOPPED = "Данные удалены. Сообщений больше не будет."
NOT_SUBSCRIBED = "Подписки нет. /start — подписаться."
STATUS = "Радиус {radius} км. Последние данные спутника: {age}."
