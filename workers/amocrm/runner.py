"""Orchestrates one export pass: pull HOT leads, create deals, report back.

Sequential on purpose. amoCRM allows 7 requests/second and each lead costs
three, but more importantly a bulk import into someone's CRM is not a place
to optimise throughput - if something is wrong, you want to have made ten
mistakes, not two hundred.
"""

import asyncio

import structlog

from .amo_client import AmoAuthError, AmoClient, AmoError, AmoRateLimited
from .core_client import CoreClient, CoreUnavailable
from .mapper import build_contact_payload, build_lead_payload, build_note, deal_name

log = structlog.get_logger(__name__)

CHANNEL = "amocrm"


class AmoRunner:
    def __init__(self, settings, core: CoreClient, amo: AmoClient):
        self._settings = settings
        self._core = core
        self._amo = amo
        self._pushed = 0
        # Lead ids already handled in THIS run. The core excludes anything it
        # has an outreach row for, but that row is only written after a
        # successful push - so a failed mark (or a dry run, which never
        # marks) hands the same leads back on the next batch. Without this
        # set, one lost response turns into a duplicate deal inside a single
        # run, and a dry run counts the same lead once per batch.
        self._seen: set[str] = set()

    async def run_once(self) -> dict:
        stats = {
            "leads": 0,
            "deals_created": 0,
            "contacts_created": 0,
            "notes_added": 0,
            "no_contact": 0,
            "failed": 0,
            "marked": 0,
            "mark_failed": 0,
            "batches": 0,
            "aborted": None,
        }

        for _ in range(self._settings.max_batches_per_run):
            try:
                batch = await self._core.fetch_pending(CHANNEL, self._settings.push_batch)
            except CoreUnavailable as exc:
                log.error("amo.core_unavailable", error=str(exc))
                stats["aborted"] = "core_unavailable"
                break

            if not batch:
                break

            fresh = [lead for lead in batch if str(lead.get("leadId")) not in self._seen]
            if not fresh:
                # Every lead in this batch was already handled - the core has
                # nothing new to give, so asking again just loops.
                break

            stats["batches"] += 1
            for lead in fresh:
                self._seen.add(str(lead.get("leadId")))
                if self._pushed >= self._settings.max_leads_per_run:
                    stats["aborted"] = "max_leads_reached"
                    log.warning(
                        "amo.cap_reached",
                        cap=self._settings.max_leads_per_run,
                        hint="остальные лиды остались в очереди",
                    )
                    break

                try:
                    await self._push(lead, stats)
                except AmoAuthError as exc:
                    # Every further call fails identically - stop rather than
                    # burning through the queue producing the same error.
                    log.error("amo.auth_failed", error=str(exc))
                    stats["aborted"] = "auth_failed"
                    return self._finish(stats)
                except AmoRateLimited:
                    log.warning("amo.rate_limited", hint="пауза 5 секунд")
                    await asyncio.sleep(5)
                    stats["failed"] += 1
                except AmoError as exc:
                    log.error("amo.push_failed", lead_id=lead.get("leadId"), error=str(exc))
                    stats["failed"] += 1

                if self._settings.request_delay_seconds:
                    await asyncio.sleep(self._settings.request_delay_seconds)

            if stats["aborted"]:
                break

        return self._finish(stats)

    def _finish(self, stats: dict) -> dict:
        log.info("amo.run_summary", **stats)
        return stats

    async def _push(self, lead: dict, stats: dict) -> None:
        stats["leads"] += 1
        lead_id = lead.get("leadId")

        if self._settings.dry_run:
            log.info("amo.dry_run", lead_id=lead_id, deal=deal_name(lead), note=build_note(lead)[:200])
            return

        deal_payload = build_lead_payload(
            lead,
            pipeline_id=self._settings.amo_pipeline_id,
            status_id=self._settings.amo_status_id,
            tag=self._settings.amo_tag,
        )
        amo_lead_id = await self._amo.create_lead(deal_payload)
        stats["deals_created"] += 1
        self._pushed += 1

        contact_payload = build_contact_payload(lead, amo_lead_id)
        if contact_payload is None:
            stats["no_contact"] += 1
        else:
            await self._amo.create_contact(contact_payload)
            stats["contacts_created"] += 1

        note = build_note(lead)
        if note:
            await self._amo.add_note(amo_lead_id, note)
            stats["notes_added"] += 1

        ok = await self._core.mark_pushed(lead_id, CHANNEL, amo_lead_id)
        stats["marked" if ok else "mark_failed"] += 1

        log.info(
            "amo.lead_pushed",
            lead_id=lead_id,
            amo_lead_id=amo_lead_id,
            company=lead.get("name"),
            score=lead.get("score"),
            marked=ok,
        )
