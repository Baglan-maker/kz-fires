"""schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE country_border (
    id            text PRIMARY KEY,
    source        text NOT NULL,
    source_sha256 text NOT NULL,
    geom          geometry(MultiPolygon, 4326) NOT NULL,
    loaded_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX country_border_geom_idx ON country_border USING gist (geom);

CREATE TABLE pipeline_runs (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('live', 'retro', 'eval')),
    params      jsonb NOT NULL,
    params_hash text NOT NULL,
    mask_id     text,
    git_sha     text,
    data_from   timestamptz,
    data_to     timestamptz,
    seed        bigint,
    started_at  timestamptz NOT NULL DEFAULT now()
);

-- Координаты в ключе уникальности хранятся как в CSV (5 знаков), без float:
-- иначе 50.12345 после round-trip через float может не совпасть сам с собой.
CREATE TABLE hotspots (
    id            bigserial PRIMARY KEY,
    product       text NOT NULL CHECK (product IN ('NRT', 'SP')),
    satellite     text NOT NULL CHECK (satellite IN ('N', 'N20', 'N21')),
    instrument    text NOT NULL DEFAULT 'VIIRS',
    acq_at        timestamptz NOT NULL,
    lat           numeric(8, 5) NOT NULL,
    lon           numeric(9, 5) NOT NULL,
    geom          geometry(Point, 4326)
                  GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon::float8, lat::float8), 4326))
                  STORED,
    confidence    text CHECK (confidence IN ('l', 'n', 'h')),
    frp           real,
    daynight      text CHECK (daynight IN ('D', 'N')),
    bright_ti4    real,
    bright_ti5    real,
    scan          real,
    track         real,
    version       text,
    fire_type     smallint,
    first_seen_at timestamptz,
    in_kz         boolean,
    UNIQUE (product, satellite, acq_at, lat, lon)
);
CREATE INDEX hotspots_geom_idx ON hotspots USING gist (geom);
CREATE INDEX hotspots_acq_at_idx ON hotspots (acq_at);
CREATE INDEX hotspots_in_kz_null_idx ON hotspots (id) WHERE in_kz IS NULL;

CREATE TABLE ingest_runs (
    id                   bigserial PRIMARY KEY,
    started_at           timestamptz NOT NULL DEFAULT now(),
    finished_at          timestamptz,
    source               text NOT NULL,
    bbox                 text,
    day_range            int,
    window_start         date,
    file_sha256          text,
    http_status          int,
    rows_fetched         int,
    rows_inserted        int,
    transactions_before  int,
    transactions_after   int,
    error                text
);
CREATE INDEX ingest_runs_source_started_idx ON ingest_runs (source, started_at);

CREATE TABLE source_status (
    source          text PRIMARY KEY,
    last_ok_at      timestamptz,
    last_new_row_at timestamptz,
    last_acq_at     timestamptz,
    state           text NOT NULL DEFAULT 'ok' CHECK (state IN ('ok', 'degraded', 'down')),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mask_versions (
    id           text PRIMARY KEY,
    built_from   timestamptz,
    built_until  timestamptz NOT NULL,
    cell_size_m  int NOT NULL,
    params       jsonb NOT NULL,
    git_sha      text,
    n_cells      int,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mask_cells (
    mask_id      text NOT NULL REFERENCES mask_versions (id) ON DELETE CASCADE,
    ix           int NOT NULL,
    iy           int NOT NULL,
    n_detections int NOT NULL,
    n_months     int,
    n_years      int,
    PRIMARY KEY (mask_id, ix, iy)
);

CREATE TABLE events (
    id           text NOT NULL,
    run_id       bigint NOT NULL REFERENCES pipeline_runs (id) ON DELETE CASCADE,
    first_acq_at timestamptz NOT NULL,
    last_acq_at  timestamptz NOT NULL,
    centroid     geometry(Point, 4326) NOT NULL,
    n_points     int NOT NULL,
    n_passes     int NOT NULL,
    max_frp      real,
    status       text NOT NULL CHECK (status IN ('open', 'closed')),
    params_hash  text NOT NULL,
    PRIMARY KEY (run_id, id)
);
CREATE INDEX events_centroid_idx ON events USING gist (centroid);

CREATE TABLE event_hotspots (
    run_id     bigint NOT NULL,
    event_id   text NOT NULL,
    hotspot_id bigint NOT NULL REFERENCES hotspots (id),
    PRIMARY KEY (run_id, event_id, hotspot_id),
    FOREIGN KEY (run_id, event_id) REFERENCES events (run_id, id) ON DELETE CASCADE
);

CREATE TABLE subscribers (
    id          bigserial PRIMARY KEY,
    tg_chat_id  bigint UNIQUE,
    lang        text NOT NULL DEFAULT 'ru' CHECK (lang IN ('ru', 'kk')),
    consent_at  timestamptz,
    zone_center geometry(Point, 4326) NOT NULL,
    radius_km   int NOT NULL CHECK (radius_km IN (5, 15, 30)),
    quiet_from  time,
    quiet_to    time,
    active      boolean NOT NULL DEFAULT true,
    is_virtual  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (is_virtual OR tg_chat_id IS NOT NULL)
);
CREATE INDEX subscribers_zone_idx ON subscribers USING gist (zone_center);

CREATE TABLE settlements (
    id         bigserial PRIMARY KEY,
    osm_id     bigint UNIQUE,
    name       text,
    place_type text,
    geom       geometry(Point, 4326) NOT NULL
);
CREATE INDEX settlements_geom_idx ON settlements USING gist (geom);

CREATE TABLE threat_assessments (
    run_id        bigint NOT NULL,
    event_id      text NOT NULL,
    subscriber_id bigint NOT NULL REFERENCES subscribers (id) ON DELETE CASCADE,
    assessed_at   timestamptz NOT NULL,
    dist_km       real NOT NULL,
    bearing_deg   real NOT NULL,
    wind_speed    real,
    wind_from_deg real,
    wind_class    text NOT NULL CHECK (wind_class IN ('toward', 'across', 'away', 'unknown')),
    land_cover    smallint,
    level         text NOT NULL CHECK (level IN ('L0', 'L1', 'L2')),
    reasons       jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, event_id, subscriber_id, assessed_at),
    FOREIGN KEY (run_id, event_id) REFERENCES events (run_id, id) ON DELETE CASCADE
);

CREATE TABLE decisions (
    id            bigserial PRIMARY KEY,
    run_id        bigint NOT NULL,
    event_id      text NOT NULL,
    subscriber_id bigint NOT NULL REFERENCES subscribers (id) ON DELETE CASCADE,
    decided_at    timestamptz NOT NULL,
    outcome       text NOT NULL CHECK (outcome IN ('alert', 'update', 'suppress')),
    reason        text NOT NULL,
    data_age_min  real,
    FOREIGN KEY (run_id, event_id) REFERENCES events (run_id, id) ON DELETE CASCADE,
    -- тревога без возраста данных — критическая ошибка, запрещаем на уровне схемы
    CHECK (outcome = 'suppress' OR data_age_min IS NOT NULL)
);
CREATE INDEX decisions_run_idx ON decisions (run_id, decided_at);

CREATE TABLE alerts (
    id              bigserial PRIMARY KEY,
    decision_id     bigint NOT NULL REFERENCES decisions (id) ON DELETE CASCADE,
    subscriber_id   bigint NOT NULL REFERENCES subscribers (id) ON DELETE CASCADE,
    event_id        text NOT NULL,
    seq             int NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    text            text NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'unknown')),
    attempts        int NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    sending_at      timestamptz,
    sent_at         timestamptz,
    tg_message_id   bigint
);
CREATE INDEX alerts_pending_idx ON alerts (id) WHERE status = 'pending';

CREATE TABLE feedback (
    id       bigserial PRIMARY KEY,
    alert_id bigint NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
    answer   text NOT NULL CHECK (answer IN ('fire', 'not_fire', 'unknown')),
    at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE label_sets (
    id        bigserial PRIMARY KEY,
    version   text NOT NULL,
    kind      text NOT NULL CHECK (kind IN ('points', 'events')),
    git_tag   text,
    sha256    text NOT NULL,
    frozen_at timestamptz,
    n         int NOT NULL,
    UNIQUE (version, kind)
);

CREATE TABLE labels (
    label_set_id     bigint NOT NULL REFERENCES label_sets (id) ON DELETE CASCADE,
    label_id         text NOT NULL,
    pass             smallint NOT NULL CHECK (pass IN (1, 2)),
    satellite        text NOT NULL,
    acq_at           timestamptz NOT NULL,
    lat              numeric(8, 5) NOT NULL,
    lon              numeric(9, 5) NOT NULL,
    event_id         text,
    stratum          text NOT NULL,
    set              text NOT NULL CHECK (set IN ('dev', 'test')),
    class            smallint NOT NULL CHECK (class BETWEEN 1 AND 4),
    label_confidence smallint NOT NULL CHECK (label_confidence BETWEEN 1 AND 3),
    s2_pre_date      date,
    s2_post_date     date,
    cloud_pre        real,
    cloud_post       real,
    dnbr             real,
    worldcover       smallint,
    evidence         text,
    labeled_at       timestamptz NOT NULL,
    seconds_spent    int,
    PRIMARY KEY (label_set_id, label_id, pass)
);

CREATE TABLE eval_runs (
    id              bigserial PRIMARY KEY,
    pipeline_run_id bigint NOT NULL REFERENCES pipeline_runs (id),
    label_set_id    bigint NOT NULL REFERENCES label_sets (id),
    git_sha         text,
    params_hash     text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    notes           text
);

CREATE TABLE eval_results (
    eval_run_id bigint NOT NULL REFERENCES eval_runs (id) ON DELETE CASCADE,
    metric      text NOT NULL,
    stratum     text NOT NULL DEFAULT 'all',
    n           int NOT NULL,
    k           int NOT NULL,
    value       double precision NOT NULL,
    ci_low      double precision,
    ci_high     double precision,
    method      text NOT NULL,
    PRIMARY KEY (eval_run_id, metric, stratum)
);

CREATE TABLE weather_cache (
    lat_cell      numeric(5, 1) NOT NULL,
    lon_cell      numeric(5, 1) NOT NULL,
    hour_utc      timestamptz NOT NULL,
    wind_speed    real,
    wind_from_deg real,
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lat_cell, lon_cell, hour_utc)
);
"""

TABLES = [
    "weather_cache",
    "eval_results",
    "eval_runs",
    "labels",
    "label_sets",
    "feedback",
    "alerts",
    "decisions",
    "threat_assessments",
    "settlements",
    "subscribers",
    "event_hotspots",
    "events",
    "mask_cells",
    "mask_versions",
    "source_status",
    "ingest_runs",
    "hotspots",
    "pipeline_runs",
    "country_border",
]


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS " + ", ".join(TABLES) + " CASCADE")
