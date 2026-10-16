from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """App settings, overridable with NUTRITION_* environment variables or a .env file."""

    model_config = SettingsConfigDict(env_prefix="NUTRITION_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'nutrition.db'}"
    data_dir: Path = PROJECT_ROOT / "data"


@lru_cache
def get_settings() -> Settings:
    return Settings()
