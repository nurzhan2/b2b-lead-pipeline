"""Entry point: `python -m workers.instagram_cta.main` (from the repo root,
with the Java core already up).

Run shape: discover CTA reels -> per reel, detect the keyword and harvest
everyone who answered it -> score by repeat responses -> enrich the top slice
-> POST to the core. The run is deliberately finite (request ceiling in
Settings) rather than a daemon; schedule it a couple of times a day instead
of leaving it running, which is both safer for the account and easier to
reason about when something goes wrong.
"""

import csv
import json
import logging
import os
import sys
from pathlib import Path

import structlog

from .config import Settings, load_targets
from .core_client import CoreClient, RawCompanyRequest
from .discovery import find_cta_reels
from .harvester import enrich, harvest_reel, resolve_keyword
from .ig_client import BudgetExhausted, InstagramClient, SessionMissing

log = structlog.get_logger(__name__)


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
    )


def _load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def _save_seen(path: Path, seen: set[str]) -> None:
    path.write_text(json.dumps(sorted(seen)), encoding="utf-8")


CSV_FIELDS = (
    "pk",
    "username",
    "full_name",
    "keyword",
    "comment_text",
    "commented_at",
    "advertiser",
    "reel_url",
    "responses",
    "confidence",
    "followers",
    "is_business",
    "public_email",
    "public_phone",
    "external_url",
)


def _export_csv(path: Path, leads: list[dict]) -> int:
    """Upsert leads by stable Instagram pk and atomically rewrite the CSV."""
    rows: dict[str, dict] = {}
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("pk"):
                    rows[str(row["pk"])] = row

    for lead in leads:
        rows[str(lead["pk"])] = {
            field: lead.get(field, "") if lead.get(field) is not None else ""
            for field in CSV_FIELDS
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows.values())
    os.replace(temp, path)
    return len(rows)


def _to_request(lead: dict) -> RawCompanyRequest:
    """Map a harvested commenter onto the core's ingest contract.

    Everything Instagram-specific rides in `raw` rather than being forced
    into a company field, so the core keeps one schema across all workers.
    """
    external = lead.get("external_url")
    return RawCompanyRequest(
        name=lead.get("full_name") or f"@{lead['username']}",
        domain=external,
        email=lead.get("public_email"),
        phone=lead.get("public_phone"),
        messenger=f"instagram:@{lead['username']}",
        source="INSTAGRAM_CTA",
        source_url=lead["reel_url"],
        has_site=bool(external),
        raw={
            "ig_pk": str(lead["pk"]),
            "ig_username": lead["username"],
            "keyword": lead["keyword"],
            "comment_text": lead.get("comment_text"),
            "commented_at": lead.get("commented_at"),
            "advertiser": lead.get("advertiser"),
            "reel_code": lead.get("reel_code"),
            "responses": lead.get("responses", 1),
            "detection_confidence": lead.get("confidence"),
            "followers": lead.get("followers"),
            "is_business": lead.get("is_business"),
            "biography": lead.get("biography"),
        },
    )


def _collect(client, reels, settings) -> dict:
    """Merge per-reel harvests into one pk-keyed map.

    Someone who answered the CTA under three different ads is looking for a
    solution; someone who answered once may have been idly curious. That
    count is the strongest ranking signal available here, so it is tracked
    on merge and carried into the core as `responses`.
    """
    all_leads: dict = {}
    for media in reels:
        try:
            verdict = resolve_keyword(client, media, settings)
            if not verdict.usable or verdict.confidence < settings.keyword_min_confidence:
                continue
            for pk, lead in harvest_reel(client, media, verdict, settings).items():
                if pk in all_leads:
                    existing = all_leads[pk]
                    existing["responses"] = existing.get("responses", 1) + 1
                    existing["confidence"] = max(existing["confidence"], lead["confidence"])
                else:
                    lead["responses"] = 1
                    all_leads[pk] = lead
        except BudgetExhausted as exc:
            log.warning("main.budget_exhausted", error=str(exc), collected=len(all_leads))
            break
    return all_leads


def run() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)
    log.info("instagram_cta.starting", core_url=settings.core_url)

    targets = load_targets(settings.targets_file)
    seen = _load_seen(settings.seen_file)
    client = InstagramClient(settings)
    core = CoreClient(settings.core_url) if settings.send_to_core else None

    try:
        reels = find_cta_reels(client, targets, settings)
        leads = _collect(client, reels, settings)

        ordered = list(leads.values())
        ordered.sort(key=lambda l: (l.get("responses", 1), l["confidence"]), reverse=True)
        log.info("main.collected", total=len(leads))

        if settings.enrich_profiles:
            for lead in ordered[: settings.max_profile_lookups]:
                try:
                    enrich(client, lead)
                except BudgetExhausted:
                    log.warning("main.enrich_stopped_early")
                    break

        exported = _export_csv(settings.output_csv, ordered)
        log.info("main.csv_exported", path=str(settings.output_csv), rows=exported)

        if core is not None:
            fresh = [lead for pk, lead in leads.items() if str(pk) not in seen]
            fresh.sort(key=lambda l: (l.get("responses", 1), l["confidence"]), reverse=True)
            for start in range(0, len(fresh), settings.ingest_batch_size):
                batch = fresh[start : start + settings.ingest_batch_size]
                if core.send_leads([_to_request(l) for l in batch]) is not None:
                    seen.update(str(l["pk"]) for l in batch)
    finally:
        if core is not None:
            _save_seen(settings.seen_file, seen)
            core.close()
        log.info("instagram_cta.finished", requests_made=client.requests_made, seen=len(seen))


if __name__ == "__main__":
    try:
        run()
    except SessionMissing as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        pass
