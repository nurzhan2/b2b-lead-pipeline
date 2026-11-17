"""HTTP client for the core's outreach boundary."""

import httpx
import structlog

log = structlog.get_logger(__name__)

PENDING_PATH = "/api/v1/outreach/pending"
PUSHED_PATH = "/api/v1/outreach/{lead_id}/pushed"
REQUEST_TIMEOUT_SECONDS = 15.0


class CoreUnavailable(Exception):
    pass


class CoreClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_pending(self, channel: str, limit: int) -> list[dict]:
        try:
            response = await self._client.get(
                f"{self._base_url}{PENDING_PATH}",
                params={"channel": channel, "limit": limit},
            )
        except httpx.TransportError as exc:
            raise CoreUnavailable(f"core unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise CoreUnavailable(f"core rejected pending: {response.status_code} {response.text[:200]}")
        return response.json()

    async def mark_pushed(self, lead_id: str, channel: str, external_id: str) -> bool:
        """Records the CRM id against the lead. The core owns this decision:
        if this call fails the lead stays pending, which risks a duplicate on
        the next run - but a duplicate deal is recoverable, and a lead that
        was never pushed and never retried is lost silently."""
        try:
            response = await self._client.post(
                f"{self._base_url}{PUSHED_PATH.format(lead_id=lead_id)}",
                json={"channel": channel, "externalId": str(external_id)},
            )
        except httpx.TransportError as exc:
            log.error("core.mark_failed", lead_id=lead_id, error=str(exc))
            return False
        if response.status_code >= 400:
            log.error("core.mark_rejected", lead_id=lead_id, status=response.status_code, body=response.text[:300])
            return False
        return True
