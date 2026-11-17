"""Configuration: .env-backed runtime settings. Pure data, no network."""

from pathlib import Path

from pydantic import Field, field_validator
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

    # --- amoCRM ---
    # Long-term token from the integration card ("Ключи и доступы"). No
    # refresh flow: the token carries its own expiry and is replaced by hand.
    amo_access_token: str = ""
    # The api_domain claim inside the token, NOT the account subdomain -
    # amoCRM shards accounts and using the wrong host gives a confusing 401.
    amo_base_url: str = "https://api-b.amocrm.ru"

    # Pipeline and stage for new deals. Left empty, amoCRM drops them into
    # the first stage of the main pipeline, which is usually what you want
    # on the first run; set them once you know where these should land.
    amo_pipeline_id: int | None = None
    amo_status_id: int | None = None
    # Tag applied to every deal, so a bad run can be found and undone with
    # one filter in the CRM.
    amo_tag: str = "qorsa-leadgen"

    # --- volume ---
    push_batch: int = Field(default=25, ge=1, le=200)
    max_batches_per_run: int = Field(default=10, ge=1)
    # amoCRM allows 7 requests/second per integration. Each lead costs three
    # calls (deal, contact, note), so this stays well clear.
    request_delay_seconds: float = Field(default=0.5, ge=0)
    request_timeout_seconds: float = Field(default=30.0, gt=0)

    # Safety valve for the first run: refuse to push more than this in one
    # go. Undoing a bad bulk import by hand is genuinely painful.
    max_leads_per_run: int = Field(default=100, ge=1)

    dry_run: bool = False
    run_once: bool = True
    loop_interval_minutes: int = Field(default=60, ge=1)

    @field_validator("amo_pipeline_id", "amo_status_id", mode="before")
    @classmethod
    def _blank_means_unset(cls, value):
        """An empty line in .env is "not configured", not "invalid".

        `AMO_PIPELINE_ID=` reaches pydantic as an empty string, which fails
        int parsing and takes the whole worker down at startup. Commenting
        the line out would work, but a config file where a present-but-empty
        key is an error is a trap - so treat blank as None.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value


def load_settings() -> Settings:
    return Settings()
