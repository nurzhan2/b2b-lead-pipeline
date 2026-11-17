"""Finding candidate CTA ad reels.

Instagram has no caption search, so discovery is: pull a pool of reels from
hashtags and known advertiser accounts, then keep the ones whose caption
looks like a keyword CTA. The filter runs on captions we already have, so
narrowing here costs nothing and saves a comment-sampling request per reel
that gets dropped.
"""

from datetime import datetime, timedelta, timezone

import structlog

from .ig_client import BudgetExhausted, RateLimited
from .keyword_detect import keyword_from_caption

log = structlog.get_logger(__name__)

# A CTA reel is one whose caption both invites a comment AND promises a
# reply. Requiring the promise is what separates "напиши АГЕНТ и вышлю
# разбор" from "напишите в комментах, что думаете".
_PROMISE = (
    "вышл", "пришл", "отправ", "скину", "скинем", "дам", "дадим", "получ",
    "в директ", "в личк", "в дм", "бесплатн", "разбор", "гайд", "подроб",
    "инструкц", "доступ", "шаблон", "чек-лист", "чеклист",
    "dm", "link in bio", "free",
)


def looks_like_cta(caption: str | None) -> bool:
    """Caption names a keyword to comment AND promises something back."""
    if not caption or keyword_from_caption(caption) is None:
        return False
    lowered = caption.lower()
    return any(marker in lowered for marker in _PROMISE)


def _fresh(media, max_age_days: int) -> bool:
    taken = getattr(media, "taken_at", None)
    if taken is None:
        return True
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=timezone.utc)
    return taken >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


def find_cta_reels(client, targets, settings) -> list:
    """Deduplicated, freshness-filtered CTA reels across all targets.

    Accounts are swept before hashtags: they are the higher-yield source, so
    if the request budget runs out mid-run we want it spent there.
    """
    pool: dict = {}
    account_media_pks: set = set()

    def sweep(label: str, fetch, *, from_account: bool = False):
        try:
            for media in fetch():
                if media.pk not in pool:
                    pool[media.pk] = media
                if from_account:
                    account_media_pks.add(media.pk)
        except BudgetExhausted:
            raise
        except (RateLimited, Exception) as exc:  # noqa: BLE001 - one bad target must not kill the sweep
            log.warning("discovery.target_failed", target=label, error=str(exc))

    try:
        for account in targets.accounts:
            sweep(
                f"@{account}",
                lambda a=account: client.account_reels(a, settings.reels_per_account),
                from_account=True,
            )
        for tag in targets.hashtags:
            sweep(f"#{tag}", lambda t=tag: client.hashtag_reels(t, settings.reels_per_hashtag))
    except BudgetExhausted as exc:
        log.warning("discovery.budget_exhausted", error=str(exc), pooled=len(pool))

    candidates = [
        m
        for m in pool.values()
        if _fresh(m, settings.max_reel_age_days)
        and (
            (settings.scan_all_account_reels and m.pk in account_media_pks)
            or looks_like_cta(getattr(m, "caption_text", None))
        )
    ]
    candidates.sort(key=lambda m: getattr(m, "comment_count", 0) or 0, reverse=True)

    log.info("discovery.done", pooled=len(pool), candidates=len(candidates))
    return candidates
