"""Селекторы принимаются только с файлом измерения, который их подтверждает."""

import json

import pytest

from workers.hh_apply import hh_selectors as sel
from workers.hh_apply.tests.fakes import HIDDEN, LETTER, LETTER_QUERY

NAME = "20260911-120000-open-form"


def write_evidence(directory, name=NAME, **over):
    payload = {
        "schema": sel.EVIDENCE_SCHEMA,
        "stage": "open-form",
        "authenticated": True,
        "data_qa_on_page": ["vacancy-response-link-top", "mainmenu_vacancyResponses"],
        "data_qa_in_modal": ["modal-overlay", "vacancy-response-submit-popup", "add-cover-letter", "resume-title"],
        "controls_before": [HIDDEN],
        "letter_field": {"control": LETTER, "queries_matching_exactly_it": [LETTER_QUERY], "readback_ok": True},
    }
    payload.update(over)
    (directory / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def measured(name=NAME, letter_query=LETTER_QUERY):
    return (
        sel.Selector("apply_link", sel.APPLY_LINK.query, sel.Where.PAGE, name),
        sel.Selector("response_form", sel.RESPONSE_FORM.query, sel.Where.MODAL, name),
        sel.Selector("submit_button", sel.SUBMIT_BUTTON.query, sel.Where.MODAL, name),
        sel.Selector("add_cover_letter", sel.ADD_COVER_LETTER.query, sel.Where.MODAL, name),
        sel.Selector("letter_field", letter_query, sel.Where.LETTER, name),
    )


def test_a_complete_measurement_verifies_every_selector(tmp_path):
    write_evidence(tmp_path)
    assert sel.problems_with(measured(), tmp_path) == []
    sel.assert_ready_to_apply(measured(), tmp_path)


def test_the_shipped_defaults_are_not_verified_until_the_probe_runs(tmp_path):
    problems = sel.problems_with(sel.REQUIRED_FOR_APPLYING, tmp_path)
    assert any("letter_field: не заполнен" in p for p in problems)
    with pytest.raises(sel.SelectorsNotVerifiedError):
        sel.assert_ready_to_apply(evidence_dir=tmp_path)
    assert not sel.letter_field_known(evidence_dir=tmp_path)


def test_a_guessed_letter_query_that_the_probe_did_not_verify_is_refused(tmp_path):
    write_evidence(tmp_path)
    problems = sel.problems_with(measured(letter_query='[data-qa="vacancy-response-letter"]'), tmp_path)
    assert any("не из списка запросов" in p for p in problems)


def test_a_letter_field_whose_text_could_not_be_read_back_is_refused(tmp_path):
    write_evidence(tmp_path, letter_field={"control": LETTER, "queries_matching_exactly_it": [LETTER_QUERY],
                                           "readback_ok": False})
    assert any("прочитать обратно" in p for p in sel.problems_with(measured(), tmp_path))


def test_a_measurement_without_authentication_is_refused(tmp_path):
    write_evidence(tmp_path, authenticated=False)
    assert any("без авторизации" in p for p in sel.problems_with(measured(), tmp_path))


def test_a_data_qa_the_probe_never_saw_is_refused(tmp_path):
    write_evidence(tmp_path, data_qa_in_modal=["modal-overlay"])
    problems = sel.problems_with(measured(), tmp_path)
    assert any("vacancy-response-submit-popup" in p for p in problems)


def test_a_missing_or_foreign_evidence_file_is_refused(tmp_path):
    assert sel.problems_with(measured(), tmp_path)
    write_evidence(tmp_path, schema="hh-agent-selector-evidence/1")
    assert any("схема" in p for p in sel.problems_with(measured(), tmp_path))


def test_form_baseline_comes_from_the_same_measurement(tmp_path):
    write_evidence(tmp_path)
    baseline = sel.form_baseline(measured()[-1], tmp_path)
    assert baseline.before == ()  # скрытые input не в счёт
    assert baseline.letter == ("textarea", "", "", "letter")


def test_no_baseline_without_a_verified_letter_field(tmp_path):
    assert sel.form_baseline(evidence_dir=tmp_path) is None


def test_names_in_reads_data_qa_and_prefix_form():
    assert sel.names_in('[data-qa="a"], [data-qa^="b"]') == [("a", False), ("b", True)]
