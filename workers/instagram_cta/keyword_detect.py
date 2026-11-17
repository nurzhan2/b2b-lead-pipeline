"""Auto-detection of the CTA keyword for a reel.

Ad reels use the "comment WORD and I'll DM you the details" pattern, but the
word itself varies per advertiser (АГЕНТ / СТАРТ / БОТ / GPT / +). Hardcoding
it means missing most reels, so we infer it per-reel from two independent
signals and cross-check them:

  1. caption  - the CTA sentence almost always names the word, either in
                quotes or in ALL CAPS. Free (we already have the caption).
  2. comments - CTA replies are single-word comments, so the keyword
                dominates the single-token distribution. Costs one sampling
                request per reel.

Agreement between the two is what makes a verdict trustworthy; either alone
is treated as weaker. Nothing here touches the network - pure functions over
strings, so it is cheap to unit test.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

# Reaction noise: frequent single-word comments that are never a CTA key.
# Deliberately does NOT contain "+", which IS a real CTA on some ads.
STOP_TOKENS = frozenset(
    {
        "спасибо", "класс", "круто", "огонь", "топ", "супер", "бомба", "жиза",
        "интересно", "красота", "вау", "ого", "да", "нет", "ага", "ок", "окей",
        "здравствуйте", "привет", "хочу", "надо", "пиши", "жду", "please",
        "nice", "cool", "wow", "love", "yes", "no", "ok", "thanks", "great",
        # Function words that can follow a CTA verb and get mistaken for the
        # keyword when the caption is shouting ("НАПИШИ МНЕ СЕЙЧАС").
        "мне", "меня", "нам", "нас", "тебе", "вам", "сюда", "туда", "тут",
        "там", "ниже", "выше", "слово", "коммент", "комменты", "комментарии",
        "сейчас", "пожалуйста", "директ", "личку", "me", "us", "below",
        "now", "word", "dm", "here", "comment", "comments",
    }
)

# Verbs that introduce a CTA. Matched case-insensitively; the keyword is then
# looked for in a short window AFTER the verb, which is what keeps us from
# grabbing a random capitalised brand name elsewhere in the caption.
_CTA_VERB = re.compile(
    r"(?:напиш|напис|пиш|отправ|остав|коммент|ставь|поставь|введи|скинь|кинь"
    r"|comment|write|type|drop|send)\w*",
    re.IGNORECASE,
)
_CTA_WINDOW = 70

# Inside that window, two shapes of keyword, in priority order.
_QUOTED = re.compile(r"[«\"'“„”‘’]\s*([^\n«»\"'“„”‘’]{1,20}?)\s*[»\"'“„”‘’]")
_CAPS = re.compile(r"\b([A-ZА-ЯЁ][A-ZА-ЯЁ0-9\-]{1,19})\b")  # case-SENSITIVE

# Strips emoji, punctuation and @mentions/#hashtags but keeps "+" as a token.
_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{1,24}|\+")
_MENTION = re.compile(r"[@#][\w.]+")


def tokenize(text: str) -> list[str]:
    """Lowercased content tokens, mentions and hashtags removed."""
    if not text:
        return []
    return _TOKEN.findall(_MENTION.sub(" ", text).lower())


def is_plausible_key(token: str) -> bool:
    """A CTA keyword is one short word that isn't generic praise."""
    if not token or token in STOP_TOKENS:
        return False
    return token == "+" or 2 <= len(token) <= 20


def keyword_from_caption(caption: str | None) -> str | None:
    """Lowercased keyword named by the caption's CTA sentence, or None.

    Quotes win over ALL CAPS because «АГЕНТ» is unambiguous while caps can
    also be shouting ("НАПИШИ МНЕ СЕЙЧАС") - the stop-list catches most of
    that, the quote branch avoids the question entirely.
    """
    if not caption:
        return None
    for verb in _CTA_VERB.finditer(caption):
        window = caption[verb.end() : verb.end() + _CTA_WINDOW]
        quoted = _QUOTED.search(window)
        if quoted:
            tokens = tokenize(quoted.group(1))
            if len(tokens) == 1 and is_plausible_key(tokens[0]):
                return tokens[0]
        caps = _CAPS.search(window)
        if caps:
            token = caps.group(1).lower()
            if is_plausible_key(token):
                return token
    return None


def single_token_histogram(comments: list[str]) -> Counter:
    """Counts of one-word comments - the shape a CTA reply always has.

    Multi-word comments are dropped rather than tokenized into the counter:
    counting every word would let filler ("это", "а", "как") outrank the
    keyword and would make the share threshold meaningless.
    """
    counter: Counter = Counter()
    for text in comments:
        tokens = tokenize(text)
        if len(tokens) == 1 and is_plausible_key(tokens[0]):
            counter[tokens[0]] += 1
    return counter


@dataclass
class KeywordVerdict:
    """Outcome of detection for one reel. `keyword` is None when neither
    signal was convincing - the caller should skip the reel rather than
    guess, since harvesting on a wrong key produces junk leads."""

    keyword: str | None = None
    confidence: float = 0.0
    evidence: str = "none"  # both | caption | comments | conflict | none
    sampled: int = 0
    single_word_comments: int = 0
    hits: int = 0
    runners_up: list[tuple[str, int]] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.keyword is not None


def detect(
    caption: str | None,
    comment_texts: list[str],
    *,
    min_hits: int = 5,
    min_share: float = 0.35,
) -> KeywordVerdict:
    """Cross-check caption and comment signals into a single verdict.

    `min_share` is measured against single-word comments only, not all
    comments, so a reel with lots of discussion underneath is not penalised.
    """
    from_caption = keyword_from_caption(caption)
    histogram = single_token_histogram(comment_texts)
    single_total = sum(histogram.values())

    top_word, top_count, share = None, 0, 0.0
    if single_total:
        top_word, top_count = histogram.most_common(1)[0]
        share = top_count / single_total

    from_comments = top_word if (top_count >= min_hits and share >= min_share) else None

    verdict = KeywordVerdict(
        sampled=len(comment_texts),
        single_word_comments=single_total,
        runners_up=histogram.most_common(4)[1:],
    )

    if from_caption and from_comments == from_caption:
        verdict.keyword, verdict.evidence = from_caption, "both"
        verdict.confidence = min(1.0, 0.7 + share * 0.3)
        verdict.hits = top_count
    elif from_caption and from_comments:
        # Both fired but disagree. The comments are what we will actually
        # match against, and they are observed behaviour rather than a
        # parse of ad copy, so they win - but the confidence stays low so
        # the caller can gate on it.
        verdict.keyword, verdict.evidence, verdict.confidence = from_comments, "conflict", 0.45
        verdict.hits = top_count
    elif from_comments:
        verdict.keyword, verdict.evidence = from_comments, "comments"
        verdict.confidence = min(0.9, 0.4 + share * 0.5)
        verdict.hits = top_count
    elif from_caption:
        # Caption promises a keyword nobody has answered yet - typical for a
        # reel published minutes ago. Worth harvesting; will be thin.
        verdict.keyword, verdict.evidence, verdict.confidence = from_caption, "caption", 0.5
        verdict.hits = histogram.get(from_caption, 0)

    return verdict
