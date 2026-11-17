"""Entry point: `python -m workers.amocrm.main` (from the repo root, core up).

First run: use --dry-run to see exactly what would be created before
anything lands in the CRM. Undoing a bad bulk import by hand is painful.
"""

import argparse
import asyncio
import logging
import sys

import httpx
import structlog

from .amo_client import AmoAuthError, AmoClient, AmoError
from .config import Settings
from .core_client import CoreClient
from .runner import AmoRunner


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


async def _run(args) -> int:
    settings = Settings()
    if args.dry_run:
        settings.dry_run = True
    if args.limit:
        settings.max_leads_per_run = args.limit

    _configure_logging(settings.log_level)
    log = structlog.get_logger(__name__)

    if not settings.amo_access_token and not settings.dry_run:
        log.error(
            "amo.no_token",
            hint="AMO_ACCESS_TOKEN пуст. Долгосрочный токен берётся в карточке "
            "интеграции, вкладка 'Ключи и доступы'. Впиши его в "
            "workers/amocrm/.env.",
        )
        return 1

    log.info(
        "amo.starting",
        core_url=settings.core_url,
        amo_base_url=settings.amo_base_url,
        batch=settings.push_batch,
        max_leads=settings.max_leads_per_run,
        pipeline_id=settings.amo_pipeline_id,
        status_id=settings.amo_status_id,
        tag=settings.amo_tag,
        dry_run=settings.dry_run,
    )

    core = CoreClient(settings.core_url)
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as http_client:
        amo = AmoClient(
            settings.amo_base_url,
            settings.amo_access_token,
            client=http_client,
            timeout_seconds=settings.request_timeout_seconds,
        )

        if not settings.dry_run:
            # One cheap request to prove the token before creating anything.
            try:
                account = await amo.whoami()
                log.info("amo.connected", account=account.get("name"), account_id=account.get("id"))
            except AmoAuthError as exc:
                log.error("amo.auth_failed", error=str(exc))
                return 1
            except AmoError as exc:
                log.error("amo.account_check_failed", error=str(exc))
                return 1

        try:
            while True:
                runner = AmoRunner(settings, core, amo)
                summary = await runner.run_once()
                log.info("amo.run_finished", **summary)
                if settings.run_once:
                    break
                await asyncio.sleep(settings.loop_interval_minutes * 60)
        finally:
            await core.aclose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="показать, что было бы создано, ничего не создавая")
    parser.add_argument("--limit", type=int, help="ограничить число лидов за прогон")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_run(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
