"""Per-reel harvest: detect the keyword, then collect the people who wrote it.

The two-pass shape matters for cost. Pass one samples `keyword_sample`
comments and runs detection; only if the verdict clears the confidence gate
does pass two pull the full comment list. A reel that turns out not to be a
keyword CTA therefore costs one request, not dozens.
"""

import random

import structlog

from .ig_client import BudgetExhausted, RateLimited
from .keyword_detect import detect, tokenize

log = structlog.get_logger(__name__)


def _matches(text: str, keyword: str) -> bool:
    """A lead wrote the keyword and essentially nothing else.

    Deliberately strict: "агент" alone is an answer to the CTA, while "а этот
    агент реально работает?" is a question. Allowing the loose form floods
    the pipeline with people who are discussing the ad, not responding to it.
    """
    tokens = tokenize(text)
    return len(tokens) <= 2 and keyword in tokens


def resolve_keyword(client, media, settings):
    """Pass one. Returns a KeywordVerdict; check `.usable` before harvesting."""
    sample = []
    try:
        comments = client.comments(getattr(media, "id", media.pk), amount=settings.keyword_sample)
        sample = [c.text for c in comments if c.text]
    except RateLimited:
        log.warning("harvester.sample_failed", reel=media.code)

    # Instagram returns comments in order, so a long thread would otherwise
    # be judged on its oldest slice only. Sampling keeps the histogram
    # representative when we did get more than we need.
    if len(sample) > settings.keyword_sample:
        sample = random.sample(sample, settings.keyword_sample)

    verdict = detect(
        getattr(media, "caption_text", None),
        sample,
        min_hits=settings.keyword_min_hits,
        min_share=settings.keyword_min_share,
    )
    log.info(
        "harvester.keyword",
        reel=media.code,
        keyword=verdict.keyword,
        evidence=verdict.evidence,
        confidence=round(verdict.confidence, 2),
        hits=verdict.hits,
        sampled=verdict.sampled,
        runners_up=verdict.runners_up,
    )
    return verdict


def harvest_reel(client, media, verdict, settings) -> dict:
    """Pass two. {user_pk: {...}} for everyone who answered the CTA.

    Keyed by pk rather than username because usernames get changed and the
    pk is what makes cross-reel deduplication (and the repeat-responder
    signal in main.py) reliable.
    """
    if not verdict.usable:
        return {}

    try:
        comments = client.comments(getattr(media, "id", media.pk), amount=settings.comments_per_reel)
    except RateLimited:
        log.warning("harvester.harvest_failed", reel=media.code)
        return {}

    leads: dict = {}
    for c in comments:
        if not c.text or not _matches(c.text, verdict.keyword):
            continue
        leads[c.user.pk] = {
            "pk": c.user.pk,
            "username": c.user.username,
            "full_name": getattr(c.user, "full_name", "") or "",
            "keyword": verdict.keyword,
            "comment_text": c.text.strip()[:200],
            "commented_at": str(getattr(c, "created_at_utc", "")),
            "reel_code": media.code,
            "reel_url": client.reel_url(media.code),
            "advertiser": getattr(getattr(media, "user", None), "username", None),
            "confidence": round(verdict.confidence, 2),
        }

    log.info("harvester.harvested", reel=media.code, keyword=verdict.keyword, leads=len(leads))
    return leads


def enrich(client, lead: dict) -> dict:
    """Add profile fields to one lead. The single priciest call in the run,
    so main.py caps how many of these happen and spends them on the
    highest-scoring leads first.

    public_email / public_phone_number are only ever populated for business
    accounts that chose to publish contacts on their own profile; we read
    them, we do not derive them.
    """
    try:
        info = client.user_info(lead["pk"])
    except (RateLimited, BudgetExhausted):
        return lead

    external = getattr(info, "external_url", None) or None
    lead.update(
        {
            "full_name": lead["full_name"] or (getattr(info, "full_name", "") or ""),
            "biography": (getattr(info, "biography", "") or "")[:300],
            "external_url": external,
            "followers": getattr(info, "follower_count", None),
            "is_business": bool(getattr(info, "is_business", False)),
            "public_email": getattr(info, "public_email", None) or None,
            "public_phone": getattr(info, "public_phone_number", None) or None,
        }
    )
    return lead
