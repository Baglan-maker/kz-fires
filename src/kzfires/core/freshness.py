"""Свежесть источника: когда мы последний раз получили ответ и новые строки.

Возраст последней точки не годится: между пролётами спутников по 6 часов
тишины — это норма. Смотрим отдельно на «API отвечает» и «приходят новые строки».
"""

import datetime as dt
from typing import Literal

State = Literal["ok", "degraded", "down"]

DOWN_AFTER = dt.timedelta(hours=3)


def poll_age(last_ok_at: dt.datetime | None, now: dt.datetime) -> dt.timedelta | None:
    return None if last_ok_at is None else now - last_ok_at


def new_data_gap(last_new_row_at: dt.datetime | None, now: dt.datetime) -> dt.timedelta | None:
    return None if last_new_row_at is None else now - last_new_row_at


def source_state(
    last_ok_at: dt.datetime | None,
    last_new_row_at: dt.datetime | None,
    now: dt.datetime,
    poll_age_limit: dt.timedelta = dt.timedelta(minutes=30),
    new_data_gap_limit: dt.timedelta | None = None,
) -> State:
    age = poll_age(last_ok_at, now)
    if age is None or age > DOWN_AFTER:
        return "down"
    if age > poll_age_limit:
        return "degraded"
    # порог по новым строкам включается только после калибровки на живых данных
    gap = new_data_gap(last_new_row_at, now)
    if new_data_gap_limit is not None and (gap is None or gap > new_data_gap_limit):
        return "degraded"
    return "ok"
