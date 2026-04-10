import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_name: str = os.getenv("APP_NAME", "Webhook Delivery System")
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'webhooks.db'}")
    worker_poll_interval_seconds: float = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1"))
    worker_max_concurrency: int = int(os.getenv("WORKER_MAX_CONCURRENCY", "5"))
    delivery_timeout_seconds: float = float(os.getenv("DELIVERY_TIMEOUT_SECONDS", "10"))
    retry_delay_seconds: int = int(os.getenv("RETRY_DELAY_SECONDS", "5"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
