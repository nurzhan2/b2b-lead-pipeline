"""DaData suggest/party client - the only code here that talks to ЕГРЮЛ.

Two lookup modes:

  by ИНН  - exact. Used whenever a госзакупка notice already gave us one.
  by name - a guess. "Ромашка" matches dozens of live companies, so the
            caller scores candidates by name similarity and discards weak
            matches (see matcher.py). City is passed as a location hint,
            which narrows but never guarantees.

Returns only what the register publishes about management: ФИО, должность,
ИНН, ОГРН, legal name, status. No contact details are requested and none
would be stored if the API returned them.
"""

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

log = structlog.get_logger(__name__)

SUGGEST_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"
FIND_BY_ID_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


class PartyCandidate(BaseModel):
    """One company as the register describes it."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    legal_name: str | None = None
    inn: str | None = None
    ogrn: str | None = None
    ceo_name: str | None = None
    ceo_post: str | None = None
    status: str | None = None
    city: str | None = None


class DadataUnavailable(Exception):
    """DaData refused or could not answer. Never fatal: the company is simply
    recorded as attempted with a note, and the run continues."""


class DadataQuotaExceeded(DadataUnavailable):
    """Daily quota is gone. Unlike other failures this one aborts the whole
    run - every further request would fail the same way, and hammering a
    dead quota is pointless."""


class DadataClient:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None, timeout_seconds: float = 20.0):
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def find_by_inn(self, inn: str) -> PartyCandidate | None:
        payload = {"query": inn, "count": 1}
        results = await self._post(FIND_BY_ID_URL, payload)
        return results[0] if results else None

    async def suggest_by_name(self, name: str, city: str | None, count: int = 10) -> list[PartyCandidate]:
        payload: dict = {"query": name, "count": count}
        if city:
            # A hint, not a filter: DaData ranks matches in the named city
            # higher but still returns others, which is why the caller must
            # check the city itself rather than trusting the order.
            payload["locations_boost"] = [{"city": city}]
        return await self._post(SUGGEST_URL, payload)

    async def _post(self, url: str, payload: dict) -> list[PartyCandidate]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Token {self._api_key}",
        }
        try:
            response = await self._client.post(url, json=payload, headers=headers, timeout=self._timeout)
        except httpx.TransportError as exc:
            raise DadataUnavailable(f"transport error: {exc}") from exc

        if response.status_code == 403:
            # DaData returns 403 both for a bad key and for an exhausted
            # daily quota; the body tells them apart.
            body = response.text.lower()
            if "limit" in body or "quota" in body:
                raise DadataQuotaExceeded("daily quota exhausted (403)")
            raise DadataUnavailable("403 - check DADATA_API_KEY")
        if response.status_code == 429:
            raise DadataQuotaExceeded("rate limited (429)")
        if response.status_code >= 400:
            raise DadataUnavailable(f"{response.status_code}: {response.text[:200]}")

        try:
            suggestions = response.json().get("suggestions") or []
        except ValueError as exc:
            raise DadataUnavailable(f"unparseable response: {exc}") from exc

        return [_to_candidate(item) for item in suggestions]


def _to_candidate(item: dict) -> PartyCandidate:
    data = item.get("data") or {}
    management = data.get("management") or {}
    address = data.get("address") or {}
    address_data = address.get("data") or {}
    state = data.get("state") or {}
    name_block = data.get("name") or {}

    return PartyCandidate(
        name=item.get("value"),
        legal_name=name_block.get("full_with_opf") or item.get("unrestricted_value"),
        inn=data.get("inn"),
        ogrn=data.get("ogrn"),
        # For an ИП there is no "management" block - the entrepreneur
        # themselves is the answer, and fio is where the register puts them.
        ceo_name=management.get("name") or _fio(data.get("fio")),
        ceo_post=management.get("post") or ("Индивидуальный предприниматель" if data.get("fio") else None),
        status=state.get("status"),
        city=address_data.get("city") or address_data.get("settlement"),
    )


def _fio(fio: dict | None) -> str | None:
    if not fio:
        return None
    parts = [fio.get("surname"), fio.get("name"), fio.get("patronymic")]
    joined = " ".join(part for part in parts if part)
    return joined or None
