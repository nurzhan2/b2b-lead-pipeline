from types import SimpleNamespace

from workers.instagram_cta.ig_client import InstagramClient


class ChunkClient:
    def __init__(self):
        self.calls = 0

    def media_comments_v1_chunk(self, media_id, min_id, max_id):
        self.calls += 1
        if self.calls == 1:
            return [1, 2], "next-min", "next-max"
        return [3, 4], "next-min", "next-max"


def test_comment_pages_each_pass_through_call_budget():
    wrapper = InstagramClient.__new__(InstagramClient)
    wrapper._client = ChunkClient()
    wrapper._s = SimpleNamespace()
    budgeted_calls = []

    def call(label, fn):
        budgeted_calls.append(label)
        return fn()

    wrapper._call = call

    assert wrapper.comments("123_456", amount=3) == [1, 2, 3]
    assert len(budgeted_calls) == 2
