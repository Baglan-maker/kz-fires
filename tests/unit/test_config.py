import pytest

from kzfires.config import Settings, load_settings
from kzfires.db.session import sqlalchemy_url

ENV_KEYS = ("FIRMS_MAP_KEY", "TELEGRAM_BOT_TOKEN", "DATABASE_URL")


@pytest.fixture
def clean_env(monkeypatch):
    # setenv + delenv: monkeypatch запоминает исходное состояние и после теста
    # уберёт то, что load_dotenv положил в os.environ
    for k in ENV_KEYS:
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)


def test_repr_hides_secrets():
    s = Settings(
        database_url="postgresql://u:p@h/d",
        firms_map_key="FAKEKEY0123456789",
        telegram_bot_token="123456:FAKE-token",
    )
    assert "FAKEKEY0123456789" not in repr(s)
    assert "FAKE-token" not in repr(s)


def test_load_settings_from_env_file(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text("FIRMS_MAP_KEY= abc \nDATABASE_URL=postgresql://u:p@h:1/d\n")
    s = load_settings(env)
    assert s.firms_map_key == "abc"
    assert s.database_url == "postgresql://u:p@h:1/d"
    assert s.telegram_bot_token == ""


@pytest.mark.parametrize(
    "url, expected",
    [
        ("postgresql://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
        ("postgres://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
        ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ],
)
def test_sqlalchemy_url(url, expected):
    assert sqlalchemy_url(url) == expected
