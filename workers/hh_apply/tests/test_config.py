"""Настройки: темп человеческий, и опечаткой его не сломать."""

import random

import pytest
from pydantic import ValidationError

from workers.hh_apply.config import DelayRange, Settings


def settings(**over):
    return Settings(_env_file=None, **over)


def test_defaults_match_the_brief():
    s = settings()
    assert s.apply_delay_seconds == "20-30"
    assert (s.apply_delay.low, s.apply_delay.high) == (20.0, 30.0)
    assert s.max_applies_per_run == 20
    assert s.site == "hh.kz"


@pytest.mark.parametrize("raw, expected", [
    ("20-30", (20.0, 30.0)), ("20–30", (20.0, 30.0)), ("20..30", (20.0, 30.0)),
    ("25", (25.0, 25.0)), (" 30 - 20 ", (20.0, 30.0)), ("2,5-3,5", (2.5, 3.5)),
])
def test_delay_ranges_parse(raw, expected):
    delay = DelayRange.parse(raw)
    assert (delay.low, delay.high) == expected


def test_a_drawn_pause_stays_inside_the_range_and_is_not_constant():
    delay = DelayRange.parse("20-30")
    rng = random.Random(1)
    draws = [delay.draw(rng) for _ in range(20)]
    assert all(20 <= d <= 30 for d in draws) and len(set(draws)) > 1


@pytest.mark.parametrize("raw", ["2", "5-30", "0"])
def test_an_apply_delay_below_ten_seconds_is_refused(raw):
    with pytest.raises(ValidationError, match="человеческим"):
        settings(apply_delay_seconds=raw)


def test_garbage_delays_are_refused():
    with pytest.raises(ValidationError):
        settings(browse_delay_seconds="быстро")


def test_the_per_run_limit_has_a_hard_ceiling():
    with pytest.raises(ValidationError):
        settings(max_applies_per_run=500)


def test_the_host_must_be_hh():
    assert settings(hh_host="https://almaty.hh.kz/").hh_host == "almaty.hh.kz"
    with pytest.raises(ValidationError):
        settings(hh_host="hh.kz.example.com")
