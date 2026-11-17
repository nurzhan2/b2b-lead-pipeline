"""Configuration: .env-backed runtime settings plus the human-editable
targets.yml (hashtags and advertiser accounts to sweep). Mirrors the shape of
workers/telegram_monitor/config.py so the two workers stay operable the same
way. Nothing here talks to Instagram or the core.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8-sig",  # -sig: tolerate the BOM Notepad adds
        extra="ignore",
    )

    ig_username: str
    ig_password: str
    # Session state is cached here so we log in once rather than on every
    # run; a fresh login per run is the single fastest way to get flagged.
    ig_session_file: Path = MODULE_DIR / "ig_session.json"
    ig_proxy: str | None = None

    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    targets_file: Path = MODULE_DIR / "targets.yml"
    seen_file: Path = MODULE_DIR / "seen_leads.json"
    output_csv: Path = MODULE_DIR / "instagram_leads.csv"
    # The worker is useful on its own. Sending into the Java leadgen core is
    # opt-in so a stopped core does not prevent usernames being collected.
    send_to_core: bool = False

    # --- Discovery ---
    reels_per_hashtag: int = 30
    reels_per_account: int = 20
    max_reel_age_days: int = 21  # older CTA reels are already worked by rivals
    # Known competitor accounts are high-signal. Their CTA may be spoken in
    # the video and absent from the caption, so sample all of their fresh
    # reels and let the comment histogram decide.
    scan_all_account_reels: bool = True

    # --- Keyword detection (see keyword_detect.detect) ---
    keyword_sample: int = 80
    keyword_min_hits: int = 5
    keyword_min_share: float = 0.35
    # Reels whose verdict scores below this are skipped entirely. 0.45 lets
    # "conflict" verdicts through; raise to 0.7 for caption+comments only.
    keyword_min_confidence: float = 0.45

    # --- Harvest ---
    comments_per_reel: int = 0  # 0 = all
    enrich_profiles: bool = True
    max_profile_lookups: int = 150  # per run; the priciest call by far
    ingest_batch_size: int = 50

    # --- Pacing. Instagram bans on rhythm, not just volume, so every call
    # goes through Pacer with jitter (see ig_client.py). ---
    min_delay_seconds: float = 3.0
    max_delay_seconds: float = 7.0
    max_flood_wait_seconds: float = 600.0
    max_requests_per_run: int = 400


class TargetsConfig(BaseModel):
    """Two ways in. Hashtags are broad and noisy; accounts are advertisers
    you already identified (e.g. from Meta Ad Library) and are far higher
    yield per request - prefer growing that list over adding hashtags."""

    hashtags: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)


def load_targets(path: Path) -> TargetsConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return TargetsConfig.model_validate(data)
