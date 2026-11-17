from workers.instagram_cta.keyword_detect import (
    detect,
    keyword_from_caption,
    single_token_histogram,
    tokenize,
)


def test_caption_quoted_keyword():
    caption = 'Собрал ИИ-агента за вечер. Напиши в комментах «АГЕНТ» и вышлю разбор'
    assert keyword_from_caption(caption) == "агент"


def test_caption_caps_keyword():
    caption = "Пиши СТАРТ и я скину инструкцию бесплатно"
    assert keyword_from_caption(caption) == "старт"


def test_caption_without_cta_returns_none():
    assert keyword_from_caption("Напишите в комментах, что думаете об этом") is None
    assert keyword_from_caption(None) is None


def test_caps_shouting_is_not_mistaken_for_keyword():
    # "МНЕ" follows the verb but is stop-listed noise, not a CTA key.
    assert keyword_from_caption("НАПИШИ МНЕ и обсудим") is None


def test_tokenize_drops_emoji_and_mentions():
    assert tokenize("АГЕНТ 🔥🔥 @someone #ai") == ["агент"]


def test_histogram_ignores_multiword_comments():
    hist = single_token_histogram(["агент", "агент", "а этот агент работает?", "огонь"])
    assert hist["агент"] == 2
    assert "работает" not in hist  # multi-word comments contribute nothing
    assert "огонь" not in hist  # stop-listed reaction


def test_detect_agreement_gives_high_confidence():
    verdict = detect(
        'Напиши «АГЕНТ» и вышлю подробности',
        ["агент"] * 12 + ["огонь", "круто", "а сколько стоит?"],
    )
    assert verdict.keyword == "агент"
    assert verdict.evidence == "both"
    assert verdict.confidence > 0.9


def test_detect_finds_keyword_with_no_caption_hint():
    verdict = detect("Разбор ИИ-агентов", ["gpt"] * 8 + ["класс", "вау"])
    assert verdict.keyword == "gpt"
    assert verdict.evidence == "comments"


def test_detect_bails_out_when_nothing_dominates():
    verdict = detect("Просто рилс", ["класс", "огонь", "супер", "вау"])
    assert verdict.keyword is None
    assert not verdict.usable


def test_detect_below_min_hits_is_rejected():
    verdict = detect(None, ["агент", "агент"], min_hits=5)
    assert verdict.keyword is None


def test_conflict_prefers_observed_comments():
    verdict = detect('Напиши «СТАРТ»', ["агент"] * 10)
    assert verdict.keyword == "агент"
    assert verdict.evidence == "conflict"
    assert verdict.confidence < 0.5
