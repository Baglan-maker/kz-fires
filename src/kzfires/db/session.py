import psycopg

from kzfires.config import load_settings


def connect(url: str | None = None, **kwargs) -> psycopg.Connection:
    return psycopg.connect(url or load_settings().database_url, **kwargs)


def sqlalchemy_url(url: str) -> str:
    """Alembic ходит через SQLAlchemy, ему нужен явный драйвер psycopg 3."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url
