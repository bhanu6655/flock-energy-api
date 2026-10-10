import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    URJA_BASE_URL: str = "https://urja-ops.flockenergy.tech"
    URJA_OPERATOR_EMAIL: str = "operator@urja.local"
    URJA_OPERATOR_PASSWORD: str = "urja-ops-2026"
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    REQUEST_TIMEOUT: float = 15.0

settings = Settings()
