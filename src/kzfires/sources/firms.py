"""Клиент FIRMS: опрос area API и идемпотентная загрузка в hotspots."""

import datetime as dt

import pandas as pd
import psycopg

Bbox = tuple[float, float, float, float]  # west, south, east, north


def fetch_area(source: str, bbox: Bbox, days: int, date: dt.date | None = None) -> pd.DataFrame:
    """Точки из /api/area/csv за `days` суток (1..5), начиная с `date`.

    Ключ стоит в пути URL, поэтому URL не должен попадать в логи и тексты исключений.
    Ошибку FIRMS может вернуть текстом с кодом 200.
    """
    raise NotImplementedError


def load_hotspots(conn: psycopg.Connection, df: pd.DataFrame, product: str) -> int:
    """Вставляет точки и возвращает число реально вставленных строк.

    Повторная загрузка того же окна вставляет 0 строк.
    """
    raise NotImplementedError


def mapkey_status() -> dict:
    """Текущее число транзакций и лимит ключа."""
    raise NotImplementedError
