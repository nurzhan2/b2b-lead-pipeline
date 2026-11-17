"""Configuration: .env-backed runtime settings. Pure data, no network."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8-sig",
        extra="ignore",
    )

    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    # DaData API key from the personal cabinet ("Ключи" section). Without it
    # the worker refuses to start rather than silently doing nothing.
    dadata_api_key: str = ""

    # --- volume ---
    # The free tier allows 10 000 suggestion requests per day, counted across
    # every suggestion type. The default here stays well under it so a
    # scheduled run can't exhaust the quota an interactive lookup might need.
    egrul_batch: int = Field(default=50, ge=1, le=500)
    max_batches_per_run: int = Field(default=20, ge=1)
    max_lookups_per_run: int = Field(default=2000, ge=1)

    # DaData allows at most 60 new connections per minute from one IP, so
    # concurrency stays low and the client reuses connections.
    egrul_concurrency: int = Field(default=3, ge=1, le=10)
    request_delay_seconds: float = Field(default=0.3, ge=0)
    request_timeout_seconds: float = Field(default=20.0, gt=0)

    # --- matching ---
    # A name match is a guess: "Ромашка" matches dozens of companies. Below
    # this similarity the candidate is discarded rather than stored, because
    # a wrong CEO name on a call is worse than no name at all.
    min_name_similarity: float = Field(default=0.82, ge=0.0, le=1.0)
    # Only consider ACTIVE entries. A liquidated company's former director is
    # not someone to call.
    active_only: bool = True

    run_once: bool = True
    loop_interval_minutes: int = Field(default=1440, ge=1)


def load_settings() -> Settings:
    return Settings()
