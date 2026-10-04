import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov"
FIRMS_ATTRIBUTION = "NASA FIRMS"

# west, south, east, north: FIRMS ждёт долготу первой
KZ_BBOX = (46.0, 40.0, 88.0, 56.0)

SATELLITES = ("SNPP", "NOAA20")

DATA_DIR = Path("data")


@dataclass(frozen=True)
class Settings:
    database_url: str
    # repr=False: настройки попадают в логи и трейсбеки, ключи туда попадать не должны
    firms_map_key: str = field(default="", repr=False)
    telegram_bot_token: str = field(default="", repr=False)


def load_settings(env_file: str | Path = ".env") -> Settings:
    load_dotenv(env_file, override=False)
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://kzfires:kzfires@localhost:5432/kzfires"
        ),
        firms_map_key=os.environ.get("FIRMS_MAP_KEY", "").strip(),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
    )
