"""Picking the right company out of a name search.

This is the part that can quietly poison the lead list. "Ромашка" matches
dozens of live companies, and storing the wrong director's name is worse
than storing none: the call opens by asking for a person who has nothing to
do with that business, and the prospect hears a scam.

So the rules here are conservative on purpose:

  - an exact ИНН lookup is trusted outright;
  - a name match must clear `min_name_similarity` on a normalised
    comparison, otherwise nothing is stored;
  - a candidate in a different city loses ground but isn't disqualified,
    since OSM city data is patchy;
  - liquidated companies are dropped when `active_only` is on - a former
    director of a dead company is not someone to call;
  - a tie between two equally plausible candidates stores nothing. Ambiguity
    is a real answer, and "we don't know" beats a coin flip.

Pure functions over data - no network, no I/O, so all of it is testable.
"""

import re
from difflib import SequenceMatcher

from .dadata_client import PartyCandidate

# Legal-form noise that says nothing about which company this is. Stripped
# before comparison so «ООО "Ромашка"» and «Ромашка» aren't treated as
# different names.
_LEGAL_FORMS = (
    "общество с ограниченной ответственностью",
    "акционерное общество",
    "публичное акционерное общество",
    "непубличное акционерное общество",
    "закрытое акционерное общество",
    "открытое акционерное общество",
    "индивидуальный предприниматель",
    "муниципальное бюджетное учреждение",
    "государственное бюджетное учреждение",
    "федеральное государственное",
    "муниципальное казенное учреждение",
    "ооо",
    "оао",
    "зао",
    "пао",
    "ао",
    "ип",
    "нао",
    "мбу",
    "гбу",
    "фгбу",
    "мку",
    "гку",
    "фку",
    "муп",
    "гуп",
)

_ACTIVE_STATUSES = ("ACTIVE",)


def normalize_name(value: str | None) -> str:
    """Lowercase, strip legal forms, quotes and punctuation, collapse spaces."""
    if not value:
        return ""
    text = value.lower().replace("ё", "е")
    text = re.sub(r'[«»"\'`]', " ", text)
    for form in _LEGAL_FORMS:
        text = re.sub(rf"\b{re.escape(form)}\b", " ", text)
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def name_similarity(left: str | None, right: str | None) -> float:
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def same_city(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return normalize_name(left) == normalize_name(right)


def is_active(candidate: PartyCandidate) -> bool:
    # An unknown status is treated as active: DaData omits it for some
    # entries, and discarding those would throw away good matches to guard
    # against a rarer bad one.
    return candidate.status is None or candidate.status.upper() in _ACTIVE_STATUSES


def pick_candidate(
    company_name: str,
    company_city: str | None,
    candidates: list[PartyCandidate],
    *,
    min_similarity: float = 0.82,
    active_only: bool = True,
) -> tuple[PartyCandidate | None, str]:
    """Returns (candidate, note). A None candidate always comes with a note
    explaining why - that note is stored, so a later reader can tell "no
    match" from "too ambiguous to choose"."""

    pool = [c for c in candidates if c.ceo_name]
    if not pool:
        return None, "кандидаты без руководителя"

    if active_only:
        active = [c for c in pool if is_active(c)]
        if not active:
            return None, "все кандидаты ликвидированы"
        pool = active

    scored = []
    for candidate in pool:
        similarity = name_similarity(company_name, candidate.name)
        # A city match is a tiebreaker, not a pass: it can lift a candidate
        # above a same-named rival, but never above the similarity floor.
        bonus = 0.05 if same_city(company_city, candidate.city) else 0.0
        scored.append((similarity + bonus, similarity, candidate))

    scored.sort(key=lambda row: row[0], reverse=True)
    best_total, best_similarity, best = scored[0]

    if best_similarity < min_similarity:
        return None, f"лучшее совпадение {best_similarity:.2f} ниже порога {min_similarity:.2f}"

    if len(scored) > 1:
        runner_up_total, _, runner_up = scored[1]
        # Two candidates this close are genuinely indistinguishable by name.
        # Picking either would be a coin flip dressed up as data.
        if best_total - runner_up_total < 0.03 and runner_up.inn != best.inn:
            return None, "несколько одинаково похожих компаний - выбор неоднозначен"

    return best, f"совпадение по названию {best_similarity:.2f}"
