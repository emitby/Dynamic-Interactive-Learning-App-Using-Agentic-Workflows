"""Reading-level measurement, computed by code so agents can't simply claim a grade level.

Uses the Flesch-Kincaid grade level, the standard US readability formula:

    FK = 0.39 * (words / sentences) + 11.8 * (syllables / words) - 15.59

The result is roughly the US school grade needed to understand the text. Syllables are
counted with a vowel-group heuristic. It's within about one syllable of a dictionary for
most English words, which is accurate enough for a guardrail with slack built into its
limits (see :func:`studysprint.config.readability_range`).
"""

import re

_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
# A sentence ends at ., ! or ? followed by whitespace or the end of the text. Decimals
# like "3.5" don't match, because the period isn't followed by whitespace.
_SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s|$)")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")


def count_syllables(word):
    """Estimate the syllables in one English word.

    Heuristic: count groups of consecutive vowels (y counts as a vowel), then correct
    two common cases:
        * A silent final "e" ("make", "slope") doesn't add a syllable, but "-le" after a
          consonant does ("ta-ble", "cir-cle").
        * Every word has at least one syllable.

    Args:
        word: A single word (letters, optionally with an apostrophe).

    Returns:
        The estimated syllable count, always at least 1.
    """
    w = word.lower().strip("'")
    if not w:
        return 1
    count = len(_VOWEL_GROUP_RE.findall(w))
    # Silent final e ("make" -> 1). "-le" ("table"), "-ee" ("tree") and "-ye" ("bye") are
    # excluded because their final vowel group really is (part of) a syllable.
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and count > 1:
        count -= 1
    return max(1, count)


def text_stats(text):
    """Count the words, sentences and syllables in a text.

    Args:
        text: Any prose. Bullet fragments without final punctuation count as one sentence each.

    Returns:
        A dict with ``words``, ``sentences`` and ``syllables`` (ints). ``sentences`` is
        at least 1 when there's any word, so the ratios below never divide by zero.
    """
    words = _WORD_RE.findall(text)
    sentences = len(_SENTENCE_END_RE.findall(text))
    # Text that doesn't end with punctuation still contains a final sentence.
    if words and not re.search(r"[.!?]\s*$", text.strip()):
        sentences += 1
    return {"words": len(words), "sentences": max(1, sentences) if words else 0,
            "syllables": sum(count_syllables(w) for w in words)}


def fk_grade(text):
    """Flesch-Kincaid grade level of ``text``.

    Args:
        text: Prose to measure.

    Returns:
        The grade level, rounded to one decimal. Very simple text can score below 0, and
        that's kept, because a lower score only means easier text. Returns ``0.0`` for
        text with no words.
    """
    s = text_stats(text)
    if s["words"] == 0:
        return 0.0
    return round(0.39 * s["words"] / s["sentences"] + 11.8 * s["syllables"] / s["words"] - 15.59, 1)


def avg_sentence_words(text):
    """Average words per sentence, the sentence-length limit used in :class:`config.Band`.

    Args:
        text: Prose to measure.

    Returns:
        The average as a float, or ``0.0`` for empty text.
    """
    s = text_stats(text)
    return s["words"] / s["sentences"] if s["sentences"] else 0.0
