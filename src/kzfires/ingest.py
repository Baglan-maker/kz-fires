"""Один опрос FIRMS NRT: загрузка, отметка «в Казахстане», журнал и свежесть источника."""

import datetime as dt
import logging

import psycopg

from kzfires.config import KZ_BBOX, SATELLITES
from kzfires.core.freshness import source_state
from kzfires.sources import firms
from kzfires.sources.border import load_border, mark_in_kz

log = logging.getLogger(__name__)

DAY_RANGE = 2
TX_SHARE_LIMIT = 0.6
# между пролётами одного спутника над Казахстаном до ~12 ч; сутки без новых строк — сбой
NEW_DATA_GAP_LIMIT = dt.timedelta(hours=24)


def sources() -> list[str]:
    return [f"VIIRS_{s}_NRT" for s in SATELLITES]


def _log_run(conn, source: str, **kw) -> None:
    cols = ["source", "bbox", "day_range", "finished_at", *kw]
    vals = [source, ",".join(map(str, KZ_BBOX)), DAY_RANGE, dt.datetime.now(dt.UTC), *kw.values()]
    conn.execute(
        f"INSERT INTO ingest_runs ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        vals,
    )


def _update_status(conn, source: str, ok: bool, inserted: int) -> str:
    now = dt.datetime.now(dt.UTC)
    row = conn.execute(
        "SELECT last_ok_at, last_new_row_at FROM source_status WHERE source = %s", (source,)
    ).fetchone() or (None, None)
    last_ok = now if ok else row[0]
    last_new = now if inserted else row[1]
    state = source_state(last_ok, last_new, now, new_data_gap_limit=NEW_DATA_GAP_LIMIT)
    conn.execute(
        """
        INSERT INTO source_status
            (source, last_ok_at, last_new_row_at, last_acq_at, state, updated_at)
        VALUES (%s, %s, %s,
                (SELECT max(acq_at) FROM hotspots WHERE product = 'NRT' AND in_kz), %s, %s)
        ON CONFLICT (source) DO UPDATE SET
            last_ok_at = EXCLUDED.last_ok_at, last_new_row_at = EXCLUDED.last_new_row_at,
            last_acq_at = EXCLUDED.last_acq_at, state = EXCLUDED.state,
            updated_at = EXCLUDED.updated_at
        """,
        (source, last_ok, last_new, state, now),
    )
    return state


def run_once(
    conn: psycopg.Connection,
    fetch=firms.fetch_area,
    status=firms.mapkey_status,
    commit: bool = True,
) -> dict:
    load_border(conn)
    try:
        before = status()
    except firms.FirmsError as e:
        log.warning("mapkey_status: %s", e)
        before = {}
    limit, used = before.get("transaction_limit"), before.get("current_transactions")
    if limit and used is not None and used > TX_SHARE_LIMIT * limit:
        for s in sources():
            _log_run(conn, s, error=f"пропуск: транзакций {used} из {limit}")
        if commit:
            conn.commit()
        return {"skipped": True}

    out = {}
    for s in sources():
        try:
            df = fetch(s, KZ_BBOX, DAY_RANGE)
            # savepoint: сбой одного спутника не откатывает данные другого
            with conn.transaction():
                inserted = firms.load_hotspots(conn, df, "NRT")
                mark_in_kz(conn)
            _log_run(
                conn,
                s,
                http_status=200,
                rows_fetched=len(df),
                rows_inserted=inserted,
                transactions_before=used,
            )
            state = _update_status(conn, s, True, inserted)
            out[s] = {"fetched": len(df), "inserted": inserted, "state": state}
        except firms.FirmsError as e:
            _log_run(conn, s, http_status=e.status, error=str(e))
            out[s] = {"error": str(e), "state": _update_status(conn, s, False, 0)}
        if commit:
            conn.commit()
    return out


def last_data_at(conn: psycopg.Connection) -> dt.datetime | None:
    return conn.execute(
        "SELECT max(acq_at) FROM hotspots WHERE product = 'NRT' AND in_kz"
    ).fetchone()[0]
