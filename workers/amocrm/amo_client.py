"""amoCRM REST API v4 client. The only code here that talks to the CRM.

Three calls per lead, in this order:

  POST /api/v4/leads            -> the deal, returns its id
  POST /api/v4/contacts         -> the person, linked to the deal
  POST /api/v4/leads/{id}/notes -> why this is a lead

The deal is created first on purpose. If the contact call fails, you are
left with a deal carrying a note and a phone in its name - recoverable. The
other order would leave orphan contacts with nothing attached.
"""

import httpx
import structlog

log = structlog.get_logger(__name__)


class AmoError(Exception):
    """amoCRM refused the request. Carries the body, which is where amoCRM
    puts the actual reason - the status code alone rarely says enough."""


class AmoAuthError(AmoError):
    """401/403 - token is wrong, expired, or the wrong api_domain is used.
    Fatal for the whole run: every further call fails identically."""


class AmoRateLimited(AmoError):
    """429 - back off and retry later rather than hammering."""


class AmoClient:
    def __init__(self, base_url: str, access_token: str,
                 client: httpx.AsyncClient | None = None, timeout_seconds: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._token = access_token
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def whoami(self) -> dict:
        """GET /api/v4/account - cheapest way to prove the token works before
        creating anything. Worth one request to avoid discovering a bad token
        halfway through a bulk push."""
        return await self._request("GET", "/api/v4/account")

    async def create_lead(self, payload: dict) -> int:
        result = await self._request("POST", "/api/v4/leads", json=[payload])
        return _first_id(result, "leads")

    async def create_contact(self, payload: dict) -> int:
        result = await self._request("POST", "/api/v4/contacts", json=[payload])
        return _first_id(result, "contacts")

    async def add_note(self, lead_id: int, text: str) -> None:
        await self._request(
            "POST", f"/api/v4/leads/{lead_id}/notes",
            json=[{"note_type": "common", "params": {"text": text}}],
        )

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        try:
            response = await self._client.request(
                method, f"{self._base_url}{path}", headers=headers,
                timeout=self._timeout, **kwargs)
        except httpx.TransportError as exc:
            raise AmoError(f"transport error: {exc}") from exc

        if response.status_code in (401, 403):
            raise AmoAuthError(f"{response.status_code}: проверь AMO_ACCESS_TOKEN и AMO_BASE_URL "
                               f"(домен берётся из поля api_domain в токене) - {response.text[:200]}")
        if response.status_code == 429:
            raise AmoRateLimited("429 - слишком часто")
        if response.status_code >= 400:
            raise AmoError(f"{response.status_code}: {response.text[:400]}")

        # 204 on some endpoints, and amoCRM returns an empty body for them.
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise AmoError(f"unparseable response: {exc}") from exc


def _first_id(result: dict, collection: str) -> int:
    try:
        return result["_embedded"][collection][0]["id"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AmoError(f"не нашёл id в ответе amoCRM: {str(result)[:300]}") from exc
