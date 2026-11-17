"""Turning a lead into amoCRM payloads.

Pure functions over data - no network - because this is where a bad
decision becomes 100 wrong deals in someone's CRM, and that is worth
testing properly.
"""

import json

# amoCRM's own field codes for contact details. Custom fields would need
# per-account ids; these are built in and work on any account.
FIELD_PHONE = "PHONE"
FIELD_EMAIL = "EMAIL"

MAX_NAME_LENGTH = 200
MAX_NOTE_LENGTH = 2000


def deal_name(lead: dict) -> str:
    """Company plus score. The score is in the name because a CRM list view
    sorts by name, and a caller working top-down should meet the best lead
    first even without touching filters."""
    name = (lead.get("name") or "Без названия").strip()
    score = lead.get("score")
    suffix = f" [{score}]" if score is not None else ""
    room = MAX_NAME_LENGTH - len(suffix)
    return name[:room] + suffix


def contact_name(lead: dict) -> str | None:
    """The person to ask for, if anyone is known.

    Order matters: the responsible official from a procurement notice runs
    THIS purchase, while a CEO from the register merely signs. Falls back to
    nothing rather than inventing a placeholder - a contact called
    "Представитель" helps no one.
    """
    person = (lead.get("contactPerson") or "").strip()
    if person:
        return person[:MAX_NAME_LENGTH]

    ceo = (lead.get("ceoName") or "").strip()
    if not ceo:
        return None
    post = (lead.get("ceoPost") or "").strip()
    return (f"{post} {ceo}" if post else ceo)[:MAX_NAME_LENGTH]


def build_lead_payload(lead: dict, *, pipeline_id: int | None, status_id: int | None, tag: str) -> dict:
    payload: dict = {"name": deal_name(lead)}
    if pipeline_id:
        payload["pipeline_id"] = pipeline_id
    if status_id:
        payload["status_id"] = status_id
    if tag:
        # Tagged so a bad run can be found and undone with one CRM filter.
        payload["_embedded"] = {"tags": [{"name": tag}]}
    return payload


def build_contact_payload(lead: dict, amo_lead_id: int) -> dict | None:
    """None when there is neither a name nor any way to reach them - an empty
    contact card is noise in a CRM, not data."""
    name = contact_name(lead)
    phone = (lead.get("phone") or "").strip()
    email = (lead.get("email") or "").strip()

    if not name and not phone and not email:
        return None

    custom_fields = []
    if phone:
        # The core stores several numbers separated by "; " - amoCRM takes
        # them as separate values on the same field, which is how a CRM user
        # expects to see them.
        values = [{"value": part.strip(), "enum_code": "WORK"} for part in phone.split(";") if part.strip()]
        custom_fields.append({"field_code": FIELD_PHONE, "values": values})
    if email:
        custom_fields.append({"field_code": FIELD_EMAIL, "values": [{"value": email, "enum_code": "WORK"}]})

    payload: dict = {
        "name": name or (lead.get("name") or "Контакт")[:MAX_NAME_LENGTH],
        "_embedded": {"leads": [{"id": amo_lead_id}]},
    }
    if custom_fields:
        payload["custom_fields_values"] = custom_fields
    return payload


def build_note(lead: dict) -> str:
    """Why this is a lead, in words a caller can use.

    This is the part that earns the export. A deal with a company name and a
    phone tells a salesperson nothing; this tells them what to open with.
    """
    lines = []

    reason = (lead.get("hotReason") or "").strip()
    if reason:
        lines.append(f"Почему лид: {reason}")

    checks = _checks(lead.get("auditFailedChecks"))
    if checks:
        lines.append(f"Что не так с сайтом: {checks}")

    domain = (lead.get("domain") or "").strip()
    lines.append(f"Сайт: {domain}" if domain else "Сайта нет")

    city = (lead.get("city") or "").strip()
    if city:
        lines.append(f"Город: {city}")

    inn = (lead.get("inn") or "").strip()
    if inn:
        lines.append(f"ИНН: {inn}")

    messenger = (lead.get("messenger") or "").strip()
    if messenger:
        lines.append(f"Мессенджер: {messenger}")

    source = (lead.get("source") or "").strip()
    source_url = (lead.get("sourceUrl") or "").strip()
    if source:
        lines.append(f"Источник: {source}" + (f" — {source_url}" if source_url else ""))

    lines.append(f"Скоринг: {lead.get('score')}")
    return "\n".join(lines)[:MAX_NOTE_LENGTH]


def _checks(value) -> str:
    """raw.audit_failed_checks arrives as a JSON array in a string."""
    if not value:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    try:
        parsed = json.loads(value)
        return ", ".join(str(item) for item in parsed) if isinstance(parsed, list) else str(value)
    except (ValueError, TypeError):
        return str(value)
