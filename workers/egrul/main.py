"""Entry point: `python -m workers.egrul.main` (from the repo root, core up)."""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings
from .core_client import CoreClient
from .dadata_client import DadataClient
from .runner import EgrulRunner


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


async def _run() -> int:
    settings = Settings()
    _configure_logging(settings.log_level)
    log = structlog.get_logger(__name__)

    if not settings.dadata_api_key:
        # Refuse rather than run: a keyless pass would mark every company as
        # attempted while finding nothing, and those attempts are sticky.
        log.error(
            "egrul.no_api_key",
            hint="DADATA_API_KEY пуст. Зарегистрируйся на dadata.ru, подтверди почту, "
                 "скопируй ключ из раздела 'Ключи' в личном кабинете и впиши его в "
                 "workers/egrul/.env. Бесплатный тариф - 10 000 запросов в день.",
        )
        return 1

    log.info("egrul.starting", core_url=settings.core_url, batch=settings.egrul_batch,
             max_batches=settings.max_batches_per_run, concurrency=settings.egrul_concurrency,
             max_lookups=settings.max_lookups_per_run,
             min_similarity=settings.min_name_similarity)

    limits = httpx.Limits(max_connections=settings.egrul_concurrency * 2,
                          max_keepalive_connections=settings.egrul_concurrency)
    core = CoreClient(settings.core_url)
    async with httpx.AsyncClient(limits=limits, timeout=settings.request_timeout_seconds) as http_client:
        dadata = DadataClient(settings.dadata_api_key, client=http_client,
                              timeout_seconds=settings.request_timeout_seconds)
        try:
            while True:
                runner = EgrulRunner(settings, core, dadata)
                summary = await runner.run_once()
                log.info("egrul.run_finished", **summary)
                if settings.run_once:
                    break
                log.info("egrul.sleeping_until_next_run", minutes=settings.loop_interval_minutes)
                await asyncio.sleep(settings.loop_interval_minutes * 60)
        finally:
            await core.aclose()
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(_run()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
