"""Thin wrapper around instagrapi: session reuse, jittered pacing, and a
FloodWait policy.

instagrapi is a private-API client, so the account doing the reading is the
resource being spent here. Two rules the rest of the worker relies on:

  * every network call goes through `_call`, which paces and counts it, so
    there is exactly one place that governs request rhythm;
  * a FloodWait longer than max_flood_wait_seconds aborts the run instead of
    sleeping through it. We never shorten or ignore the wait Instagram asks
    for - sitting blocked for an hour is how a soft limit becomes a ban.

Run this on a dedicated account with a residential proxy, never the account
you actually care about.
"""

import random
import time
from typing import Any, Callable

import structlog

log = structlog.get_logger(__name__)


class BudgetExhausted(RuntimeError):
    """Raised when the run hits its request ceiling or a long FloodWait.
    Callers treat it as 'stop cleanly and ingest what we have'."""


class RateLimited(RuntimeError):
    """A single call failed on rate limiting but the run may continue."""


class SessionMissing(RuntimeError):
    """The one-off interactive bootstrap has not been completed yet."""


class InstagramClient:
    def __init__(self, settings):
        from instagrapi import Client

        self._s = settings
        self._requests = 0
        # curl transport uses an HTTP/2/TLS fingerprint closer to the current
        # Android app. Instagram increasingly rejects the default requests
        # transport at login as an obsolete app client.
        self._client = Client(private_transport="curl")
        if settings.ig_proxy:
            self._client.set_proxy(settings.ig_proxy)
        self._login()

    def _login(self) -> None:
        session_file = self._s.ig_session_file
        if not session_file.exists():
            raise SessionMissing(
                "Instagram session is missing. Run "
                "`python -m workers.instagram_cta.bootstrap_login` once first."
            )
        self._client.load_settings(session_file)
        self._client.login(self._s.ig_username, self._s.ig_password)
        log.info("ig_client.session_reused", file=str(session_file))

    @property
    def requests_made(self) -> int:
        return self._requests

    def _pace(self) -> None:
        time.sleep(random.uniform(self._s.min_delay_seconds, self._s.max_delay_seconds))

    def _call(self, what: str, fn: Callable[[], Any]) -> Any:
        """Single choke point for every private-API call."""
        from instagrapi.exceptions import ClientError, LoginRequired, PleaseWaitFewMinutes

        if self._requests >= self._s.max_requests_per_run:
            raise BudgetExhausted(f"request ceiling {self._s.max_requests_per_run} reached")

        self._pace()
        self._requests += 1
        try:
            return fn()
        except PleaseWaitFewMinutes as exc:
            log.warning("ig_client.soft_block", call=what, error=str(exc))
            raise BudgetExhausted("Instagram asked us to wait - stopping run") from exc
        except LoginRequired as exc:
            log.error("ig_client.login_required", call=what)
            raise BudgetExhausted("session invalidated") from exc
        except ClientError as exc:
            log.warning("ig_client.call_failed", call=what, error=str(exc))
            raise RateLimited(what) from exc

    # --- Reads used by discovery.py / harvester.py -------------------------

    def hashtag_reels(self, tag: str, amount: int) -> list:
        """Top + recent medias for a tag, reels only. Two calls: 'top' is
        where paid reels with traction surface, 'recent' catches fresh ones
        that have not accumulated engagement yet."""
        out = []
        for label, fn in (
            ("top", lambda: self._client.hashtag_medias_top(tag, amount=amount)),
            ("recent", lambda: self._client.hashtag_medias_recent(tag, amount=amount)),
        ):
            try:
                out.extend(self._call(f"hashtag_{label}:{tag}", fn))
            except RateLimited:
                continue
        return [m for m in out if getattr(m, "product_type", "") == "clips"]

    def account_reels(self, username: str, amount: int) -> list:
        uid = self._call(f"user_id:{username}", lambda: self._client.user_id_from_username(username))
        return self._call(f"user_clips:{username}", lambda: self._client.user_clips(uid, amount=amount))

    def comments(self, media_id, amount: int) -> list:
        """Fetch comments one API page at a time through the pacer.

        instagrapi's convenience ``media_comments`` paginates internally, so
        treating it as one call silently bypasses both our delay and request
        budget. The chunk API keeps every actual request governed by _call.
        ``amount=0`` means all available comments.
        """
        comments = []
        min_id = ""
        max_id = ""
        while True:
            items, next_min_id, next_max_id = self._call(
                f"comments:{media_id}",
                lambda current_min=min_id, current_max=max_id: self._client.media_comments_v1_chunk(
                    str(media_id), current_min, current_max
                ),
            )
            comments.extend(items)
            if amount and len(comments) >= amount:
                return comments[:amount]
            if not items or (next_min_id, next_max_id) == (min_id, max_id):
                return comments
            min_id, max_id = next_min_id or "", next_max_id or ""

    def user_info(self, user_pk) -> Any:
        return self._call(f"user_info:{user_pk}", lambda: self._client.user_info(user_pk))

    @staticmethod
    def reel_url(code: str) -> str:
        return f"https://www.instagram.com/reel/{code}/"
