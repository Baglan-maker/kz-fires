"""Свежесть источника: когда мы последний раз получили ответ и новые строки."""

import datetime as dt
from typing import Literal

State = Literal["ok", "degraded", "down"]


def poll_age(last_ok_at: dt.datetime | None, now: dt.datetime) -> dt.timedelta | None:
    raise NotImplementedError


def new_data_gap(last_new_row_at: dt.datetime | None, now: dt.datetime) -> dt.timedelta | None:
    raise NotImplementedError


def source_state(
    last_ok_at: dt.datetime | None,
    last_new_row_at: dt.datetime | None,
    now: dt.datetime,
    poll_age_limit: dt.timedelta = dt.timedelta(minutes=30),
    new_data_gap_limit: dt.timedelta | None = None,
) -> State:
    raise NotImplementedError
