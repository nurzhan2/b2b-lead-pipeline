"""Tests for lead -> amoCRM payload mapping.

This is where a bad decision becomes a hundred wrong deals in someone's CRM,
so the edges get covered properly.
"""

import json

from workers.amocrm.mapper import (
    build_contact_payload,
    build_lead_payload,
    build_note,
    contact_name,
    deal_name,
)


def lead(**overrides) -> dict:
    base = {
        "leadId": "11111111-1111-1111-1111-111111111111",
        "name": "ООО Ромашка",
        "score": 80,
        "hotReason": "прямой интент; упомянут бюджет",
        "phone": "+74951234567",
        "email": "info@romashka.ru",
        "city": "Москва",
        "domain": "romashka.ru",
        "source": "ZAKUPKI",
    }
    base.update(overrides)
    return base


class TestDealName:
    def test_score_is_in_the_name(self):
        assert deal_name(lead()) == "ООО Ромашка [80]"

    def test_long_name_is_truncated_but_keeps_the_score(self):
        name = deal_name(lead(name="Р" * 400))

        assert len(name) <= 200
        assert name.endswith("[80]")

    def test_missing_name_does_not_produce_an_empty_deal(self):
        assert deal_name(lead(name=None)).startswith("Без названия")


class TestContactName:
    def test_procurement_official_wins_over_ceo(self):
        # The official runs THIS purchase; the CEO merely signs.
        name = contact_name(lead(contactPerson="Иванова М.П.", ceoName="Петров П.П.", ceoPost="Директор"))

        assert name == "Иванова М.П."

    def test_ceo_is_used_with_their_post(self):
        assert contact_name(lead(ceoName="Петров П.П.", ceoPost="Директор")) == "Директор Петров П.П."

    def test_no_name_is_none_not_a_placeholder(self):
        # "Представитель" as a contact name helps nobody.
        assert contact_name(lead()) is None


class TestContactPayload:
    def test_phone_and_email_become_amo_fields(self):
        payload = build_contact_payload(lead(ceoName="Петров П.П."), 500)

        codes = {f["field_code"] for f in payload["custom_fields_values"]}
        assert codes == {"PHONE", "EMAIL"}
        assert payload["_embedded"]["leads"] == [{"id": 500}]

    def test_several_phones_become_several_values(self):
        payload = build_contact_payload(lead(phone="+74951234567; +79991234567"), 500)

        phones = next(f for f in payload["custom_fields_values"] if f["field_code"] == "PHONE")
        assert len(phones["values"]) == 2

    def test_nothing_to_show_means_no_contact_card(self):
        # An empty contact is noise in a CRM, not data.
        assert build_contact_payload(lead(phone=None, email=None), 500) is None

    def test_company_name_is_used_when_the_person_is_unknown(self):
        payload = build_contact_payload(lead(), 500)

        assert payload["name"] == "ООО Ромашка"


class TestLeadPayload:
    def test_tag_is_attached(self):
        payload = build_lead_payload(lead(), pipeline_id=None, status_id=None, tag="qorsa-leadgen")

        assert payload["_embedded"]["tags"] == [{"name": "qorsa-leadgen"}]

    def test_pipeline_is_omitted_when_not_configured(self):
        payload = build_lead_payload(lead(), pipeline_id=None, status_id=None, tag="")

        assert "pipeline_id" not in payload
        assert "_embedded" not in payload

    def test_pipeline_and_status_are_passed_through(self):
        payload = build_lead_payload(lead(), pipeline_id=77, status_id=88, tag="")

        assert payload["pipeline_id"] == 77
        assert payload["status_id"] == 88


class TestNote:
    def test_note_carries_the_reason_and_the_site_problems(self):
        note = build_note(lead(auditFailedChecks=json.dumps(["нет h1", "смешанный контент"])))

        assert "прямой интент" in note
        assert "нет h1, смешанный контент" in note

    def test_no_site_is_stated_outright(self):
        assert "Сайта нет" in build_note(lead(domain=None))

    def test_checks_as_a_plain_list_also_work(self):
        note = build_note(lead(auditFailedChecks=["нет favicon"]))

        assert "нет favicon" in note

    def test_note_is_capped(self):
        assert len(build_note(lead(hotReason="повод; " * 800))) <= 2000
