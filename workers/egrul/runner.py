"""Orchestrates one ЕГРЮЛ pass: pull companies, look each one up, report back."""

import asyncio

import structlog

from .core_client import CoreClient, CoreUnavailable, EgrulPatch, PendingCompany
from .dadata_client import DadataClient, DadataQuotaExceeded, DadataUnavailable
from .matcher import pick_candidate

log = structlog.get_logger(__name__)


class EgrulRunner:
    def __init__(self, settings, core: CoreClient, dadata: DadataClient):
        self._settings = settings
        self._core = core
        self._dadata = dadata
        self._lookups = 0
        self._lock = asyncio.Lock()

    async def run_once(self) -> dict:
        stats = {
            "companies": 0,
            "by_inn": 0,
            "by_name": 0,
            "ceo_found": 0,
            "no_match": 0,
            "ambiguous": 0,
            "failed": 0,
            "patched": 0,
            "patch_failed": 0,
            "batches": 0,
            "aborted": None,
        }
        semaphore = asyncio.Semaphore(self._settings.egrul_concurrency)

        for _ in range(self._settings.max_batches_per_run):
            try:
                batch = await self._core.fetch_pending(self._settings.egrul_batch)
            except CoreUnavailable as exc:
                log.error("egrul.core_unavailable", error=str(exc))
                stats["aborted"] = "core_unavailable"
                break

            if not batch:
                break

            stats["batches"] += 1
            results = await asyncio.gather(
                *(self._handle(company, semaphore) for company in batch),
                return_exceptions=True,
            )

            quota_gone = False
            for result in results:
                if isinstance(result, DadataQuotaExceeded):
                    quota_gone = True
                    continue
                if isinstance(result, BaseException):
                    log.error("egrul.company_crashed", error=str(result))
                    stats["failed"] += 1
                    continue
                for key, value in result.items():
                    stats[key] = stats.get(key, 0) + value

            if quota_gone:
                # Every remaining request would fail the same way. Stop and
                # keep the rest pending for tomorrow - the attempt flag was
                # never written for them, so nothing is lost.
                log.warning("egrul.quota_exhausted", hint="остальные компании остались в очереди")
                stats["aborted"] = "quota_exhausted"
                break

            if self._lookups >= self._settings.max_lookups_per_run:
                stats["aborted"] = "lookup_cap_reached"
                break

        log.info("egrul.run_summary", **stats)
        return stats

    async def _handle(self, company: PendingCompany, semaphore: asyncio.Semaphore) -> dict:
        async with semaphore:
            counters = {"companies": 1}
            name = (company.name or "").strip()
            if not name and not company.inn:
                return counters

            async with self._lock:
                if self._lookups >= self._settings.max_lookups_per_run:
                    return counters
                self._lookups += 1

            try:
                if company.inn:
                    # Exact: a госзакупка notice already told us the ИНН, so
                    # there is nothing to guess about which company this is.
                    candidate = await self._dadata.find_by_inn(company.inn)
                    note = "поиск по ИНН из закупки"
                    counters["by_inn"] = 1
                    if candidate is None or not candidate.ceo_name:
                        candidate, note = None, "по ИНН руководитель не указан"
                else:
                    suggestions = await self._dadata.suggest_by_name(name, company.city)
                    counters["by_name"] = 1
                    candidate, note = pick_candidate(
                        name, company.city, suggestions,
                        min_similarity=self._settings.min_name_similarity,
                        active_only=self._settings.active_only,
                    )
            except DadataQuotaExceeded:
                raise
            except DadataUnavailable as exc:
                log.warning("egrul.lookup_failed", company_id=company.id, error=str(exc))
                counters["failed"] = 1
                return counters

            if candidate is None:
                counters["ambiguous" if "неоднозначен" in note else "no_match"] = 1
                patch = EgrulPatch(egrul_notes=note[:300])
            else:
                counters["ceo_found"] = 1
                patch = EgrulPatch(
                    inn=candidate.inn,
                    ogrn=candidate.ogrn,
                    legal_name=candidate.legal_name,
                    ceo_name=candidate.ceo_name,
                    ceo_post=candidate.ceo_post,
                    egrul_notes=note[:300],
                )

            ok = await self._core.patch_egrul(company.id, patch)
            counters["patched" if ok else "patch_failed"] = 1

            log.info("egrul.company_done", company_id=company.id, name=name,
                     ceo=candidate.ceo_name if candidate else None,
                     post=candidate.ceo_post if candidate else None,
                     note=note, patched=ok)

            if self._settings.request_delay_seconds:
                await asyncio.sleep(self._settings.request_delay_seconds)
            return counters
