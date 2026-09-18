from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Deployment and local documentation use conventional uppercase environment variables.
    model_config = SettingsConfigDict(case_sensitive=False)

    database_url: PostgresDsn
    household_id: str
    auth_mode: Literal['proxy', 'none'] = 'none'
    single_user_id: str = 'user@example.com'
    public_base_url: str | None = None

    web_dist_path: str = '/app/web-dist'
    migrations_path: str = '/app/migrations'

    openrouter_api_key: str | None = None
    openrouter_base_url: str = 'https://openrouter.ai/api/v1'
    openrouter_referer: str | None = None

    # Planning happens on a household clock, not the server's.
    timezone: str = 'America/New_York'
    # The worker is in-process: single-digit concurrency, no queue infrastructure.
    job_poll_seconds: float = 2.0
    job_worker_enabled: bool = True
    llm_log_retention_days: int = 30

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
