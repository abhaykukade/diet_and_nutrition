import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """App settings, overridable with NUTRITION_* environment variables or a .env file."""

    model_config = SettingsConfigDict(env_prefix="NUTRITION_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'nutrition.db'}"
    data_dir: Path = PROJECT_ROOT / "data"
    # Signs the login cookie. If unset, one is generated once and kept in data_dir/secret_key.
    secret_key: str | None = None
    # Set to true when the app is served over HTTPS so the login cookie is never sent in clear.
    secure_cookies: bool = False
    session_max_age_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_secret_key(settings: Settings) -> str:
    if settings.secret_key:
        return settings.secret_key
    path = settings.data_dir / "secret_key"
    if not path.exists():
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    return path.read_text().strip()
