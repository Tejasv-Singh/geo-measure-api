from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "geo-measure-api"
    database_url: str = "sqlite:///./data/app.db"
    storage_dir: Path = Path("./data/uploads")
    max_upload_bytes: int = 50 * 1024 * 1024
    max_uncompressed_bytes: int = 500 * 1024 * 1024
    max_features: int = 250_000
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
