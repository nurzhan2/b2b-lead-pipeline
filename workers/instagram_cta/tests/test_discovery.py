from datetime import datetime, timezone
from types import SimpleNamespace

from workers.instagram_cta.discovery import find_cta_reels


def media(pk, caption):
    return SimpleNamespace(
        pk=pk,
        code=f"code-{pk}",
        caption_text=caption,
        taken_at=datetime.now(timezone.utc),
        comment_count=10,
    )


class Client:
    def account_reels(self, username, amount):
        return [media(1, "CTA is only spoken in this video")]

    def hashtag_reels(self, tag, amount):
        return [
            media(2, "ordinary reel"),
            media(3, 'Напиши «СТАРТ» и вышлю гайд'),
        ]


def test_known_accounts_are_sampled_even_without_caption_cta():
    targets = SimpleNamespace(accounts=["competitor"], hashtags=["ai"])
    settings = SimpleNamespace(
        reels_per_account=10,
        reels_per_hashtag=10,
        max_reel_age_days=30,
        scan_all_account_reels=True,
    )

    assert [item.pk for item in find_cta_reels(Client(), targets, settings)] == [1, 3]
