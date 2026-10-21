"""Reading-level math: syllables, sentence counts, Flesch-Kincaid and the grade bands."""

import pytest

from studysprint.config import band_for, grade_number, readability_range
from studysprint.readability import avg_sentence_words, count_syllables, fk_grade, text_stats


@pytest.mark.parametrize("word, n", [("cat", 1), ("water", 2), ("make", 1), ("table", 2), ("tree", 1),
                                     ("evaporation", 5), ("photosynthesis", 5), ("a", 1), ("rhythm", 1)])
def test_syllables(word, n):
    """Within the heuristic's known accuracy: these are the cases the formula depends on most."""
    assert count_syllables(word) == n


def test_text_stats_counts_sentences_and_handles_fragments():
    assert text_stats("The sun is hot. Water rises!")["sentences"] == 2
    assert text_stats("A fragment without a period")["sentences"] == 1
    assert text_stats("Pi is about 3.14 today.")["sentences"] == 1      # a decimal isn't a sentence end
    assert text_stats("")["words"] == 0


def test_fk_orders_simple_below_complex():
    simple = "The sun is hot. It warms the sea. The water goes up."
    hard = ("Photosynthesis converts electromagnetic radiation into chemical potential energy, "
            "facilitating carbohydrate biosynthesis within chloroplast organelles.")
    assert fk_grade(simple) < 3 < 14 < fk_grade(hard)
    assert fk_grade("") == 0.0


def test_formula_matches_hand_calculation():
    # 6 words, 1 sentence, 7 syllables ("The cat sat on the mat." -> all 1 except none) = 6 syllables.
    s = text_stats("The cat sat on the mat.")
    assert (s["words"], s["sentences"], s["syllables"]) == (6, 1, 6)
    assert fk_grade("The cat sat on the mat.") == round(0.39 * 6 + 11.8 * 1 - 15.59, 1)


def test_avg_sentence_words():
    assert avg_sentence_words("One two three. Four five six seven eight nine.") == 4.5


def test_grade_scale_and_bands():
    assert [grade_number(g) for g in ("K", "1", "12", "C")] == [0, 1, 12, 14]
    with pytest.raises(ValueError):
        grade_number("13")
    assert band_for("K").words_max < band_for("5").words_max < band_for("C").words_max
    assert readability_range("2") == (float("-inf"), 5.0)       # no floor for young learners
    assert readability_range("10") == (4.0, 13.0)
    assert readability_range("C") == (8.0, 18.0)
