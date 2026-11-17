"""Сетевой предохранитель и переходы по hh — без браузера."""

import pytest

from workers.hh_apply.browser import (
    CaptchaError,
    NavigatedElsewhereError,
    NetGuard,
    is_captcha,
    on_site,
    open_hh_page,
    vacancy_ids_in,
)
from workers.hh_apply.tests.fakes import FakeRoute

VID = "136000001"


# --- NetGuard ----------------------------------------------------------------------


def test_outside_a_window_every_non_get_is_aborted():
    guard = NetGuard()
    route = FakeRoute("POST", "https://hh.kz/applicant/vacancy_response/popup?vacancyId=136000001")
    guard.handle(route)
    assert route.outcome == "aborted" and guard.blocked


def test_outside_a_window_gets_pass_so_pages_and_the_form_still_open():
    guard = NetGuard()
    route = FakeRoute("GET", "https://hh.kz/applicant/vacancy_response/popup?vacancyId=136000001")
    guard.handle(route)
    assert route.outcome == "continued"


def test_inside_the_window_this_vacancys_send_passes():
    guard = NetGuard()
    with guard.armed(VID):
        route = FakeRoute("POST", "https://hh.kz/applicant/vacancy_response", post_data='{"vacancy_id": 136000001}')
        guard.handle(route)
    assert route.outcome == "continued" and guard.allowed_while_armed


def test_inside_the_window_anything_naming_another_vacancy_is_aborted():
    guard = NetGuard()
    with guard.armed(VID):
        in_body = FakeRoute("POST", "https://hh.kz/applicant/vacancy_response", post_data="vacancyId=999999999")
        in_url = FakeRoute("GET", "https://hh.kz/applicant/vacancy_response/popup?vacancyId=999999999")
        guard.handle(in_body)
        guard.handle(in_url)
    assert in_body.outcome == in_url.outcome == "aborted"


def test_the_window_closes_even_when_the_flow_inside_fails():
    guard = NetGuard()
    with pytest.raises(RuntimeError):
        with guard.armed(VID):
            raise RuntimeError("boom")
    assert guard.armed_for is None
    route = FakeRoute("POST", "https://hh.kz/anything")
    guard.handle(route)
    assert route.outcome == "aborted"


def test_a_window_needs_a_real_vacancy_id():
    with pytest.raises(ValueError):
        with NetGuard().armed(""):
            pass


def test_vacancy_ids_are_found_in_every_spelling():
    assert vacancy_ids_in("https://hh.kz/x?vacancyId%3D136000001") == {"136000001"}
    assert vacancy_ids_in("https://hh.kz/applicant/vacancy_response/136000002") == {"136000002"}
    assert vacancy_ids_in("https://hh.kz/x", '{"vacancyId":136000003}') == {"136000003"}


# --- переходы ------------------------------------------------------------------------


class Page:
    def __init__(self, land_on=None, error=None):
        self.land_on, self.error, self.url = land_on, error, "about:blank"
        self.visited = []

    def goto(self, url, wait_until=None, timeout=None):
        self.visited.append(url)
        self.url = self.land_on or url
        if self.error:
            raise RuntimeError(self.error)

    def wait_for_load_state(self, *a, **k):
        pass


def test_the_regional_redirect_after_login_is_absorbed():
    page = Page(land_on=f"https://almaty.hh.kz/vacancy/{VID}", error="net::ERR_ABORTED at https://hh.kz/…")
    assert open_hh_page(page, f"https://hh.kz/vacancy/{VID}", site="hh.kz", expect_vacancy=VID).startswith("https://almaty")


def test_other_navigation_errors_are_not_swallowed():
    with pytest.raises(RuntimeError, match="Timeout"):
        open_hh_page(Page(error="Timeout 30000ms exceeded"), f"https://hh.kz/vacancy/{VID}", site="hh.kz")


def test_a_captcha_is_recognised_by_where_the_browser_ended_up():
    page = Page(land_on="https://hh.kz/account/captcha?backurl=%2Fvacancy%2F1")
    with pytest.raises(CaptchaError):
        open_hh_page(page, f"https://hh.kz/vacancy/{VID}", site="hh.kz", expect_vacancy=VID)
    assert is_captcha("https://hh.kz/account/captcha/")
    assert not is_captcha("https://hh.kz/accountancy")


def test_a_lookalike_host_is_refused_before_the_browser_moves():
    page = Page()
    with pytest.raises(NavigatedElsewhereError):
        open_hh_page(page, f"https://hh.kz.example.com/vacancy/{VID}", site="hh.kz")
    assert page.visited == []
    assert not on_site("https://evil.com\\@hh.kz/vacancy/1", "hh.kz")
    assert not on_site("http://hh.kz/vacancy/1", "hh.kz")


def test_landing_on_a_different_vacancy_or_a_login_wall_is_refused():
    page = Page(land_on="https://hh.kz/account/login?backurl=%2Fvacancy%2F136000001")
    with pytest.raises(NavigatedElsewhereError):
        open_hh_page(page, f"https://hh.kz/vacancy/{VID}", site="hh.kz", expect_vacancy=VID)
