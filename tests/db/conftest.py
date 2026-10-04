import os

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from kzfires.config import load_settings

TEST_DB = "kzfires_test"


@pytest.fixture(scope="session")
def db_url():
    base = load_settings().database_url
    try:
        admin = psycopg.connect(base, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError:
        if os.environ.get("REQUIRE_DB"):
            raise
        pytest.skip("PostGIS недоступен: make db-up")
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    url = base.rsplit("/", 1)[0] + "/" + TEST_DB

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    # прогон вниз и снова вверх ловит сломанный downgrade
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    yield url
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.close()


@pytest.fixture
def conn(db_url):
    with psycopg.connect(db_url) as c:
        yield c
        c.rollback()
