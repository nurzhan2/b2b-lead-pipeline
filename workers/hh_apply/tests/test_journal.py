"""Журнал: кто что может менять, и что письмо в него не пишется."""

import pytest

from workers.hh_apply.journal import Actor, Entry, IllegalTransitionError, Journal, Status, digest


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "hh_apply.sqlite3")


def test_only_a_human_confirms(journal):
    journal.record(Entry("1", Status.QUEUED), actor=Actor.AGENT)
    with pytest.raises(IllegalTransitionError):
        journal.record(Entry("1", Status.CONFIRMED), actor=Actor.AGENT)
    journal.record(Entry("1", Status.CONFIRMED), actor=Actor.HUMAN)
    assert journal.get("1").status is Status.CONFIRMED


def test_nothing_is_born_sent_or_confirmed(journal):
    for status in (Status.SENT, Status.CONFIRMED):
        with pytest.raises(IllegalTransitionError):
            journal.record(Entry("2", status), actor=Actor.AGENT)


def test_sent_is_terminal(journal):
    journal.record(Entry("3", Status.QUEUED), actor=Actor.AGENT)
    journal.record(Entry("3", Status.CONFIRMED), actor=Actor.HUMAN)
    journal.record(Entry("3", Status.SENT, letter_digest=digest("письмо")), actor=Actor.AGENT)
    for target in (Status.QUEUED, Status.FAILED, Status.SKIPPED):
        with pytest.raises(IllegalTransitionError):
            journal.record(Entry("3", target), actor=Actor.HUMAN)


def test_needs_manual_leaves_only_by_a_human(journal):
    journal.record(Entry("4", Status.NEEDS_MANUAL, reason="тест работодателя"), actor=Actor.AGENT)
    with pytest.raises(IllegalTransitionError):
        journal.record(Entry("4", Status.QUEUED), actor=Actor.AGENT)
    journal.record(Entry("4", Status.QUEUED), actor=Actor.HUMAN)


def test_the_letter_itself_is_never_stored(journal, tmp_path):
    letter = "Очень личное письмо, которое не должно лежать в базе"
    journal.record(Entry("5", Status.QUEUED), actor=Actor.AGENT)
    journal.record(Entry("5", Status.CONFIRMED), actor=Actor.HUMAN)
    journal.record(Entry("5", Status.SENT, letter_digest=digest(letter)), actor=Actor.AGENT)
    raw = (tmp_path / "hh_apply.sqlite3").read_bytes()
    assert letter.encode("utf-8") not in raw
    assert journal.get("5").letter_digest == digest(letter)


def test_count_sent_since(journal):
    from datetime import UTC, datetime, timedelta

    journal.record(Entry("6", Status.QUEUED), actor=Actor.AGENT)
    journal.record(Entry("6", Status.CONFIRMED), actor=Actor.HUMAN)
    journal.record(Entry("6", Status.SENT), actor=Actor.AGENT)
    assert journal.count_sent_since(datetime.now(UTC) - timedelta(minutes=1)) == 1
