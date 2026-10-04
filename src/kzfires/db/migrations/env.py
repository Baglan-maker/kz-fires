from alembic import context
from sqlalchemy import create_engine, pool

from kzfires.config import load_settings
from kzfires.db.session import sqlalchemy_url


def run_migrations_online() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or load_settings().database_url
    engine = create_engine(sqlalchemy_url(url), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline-режим не поддерживается: миграции применяются к живой БД")
run_migrations_online()
