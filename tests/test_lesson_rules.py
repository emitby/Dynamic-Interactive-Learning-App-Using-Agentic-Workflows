"""Lesson rules: concepts, quiz items, answer shuffling and answer-key secrecy."""

import copy
import random

import pytest
from collections import Counter

from studysprint.lesson import Concept, Lesson, build_question, concept_problems, quiz_problems


def _quiz_items(lesson):
    """Turn a fixture's assembled questions back into raw Quiz Agent items."""
    return [{"question": q["question"], "correct": q["options"][q["answer_index"]],
             "distractors": [o for i, o in enumerate(q["options"]) if i != q["answer_index"]],
             "concept_index": q["concept_index"], "why": q["why"]} for q in lesson["questions"]]


def test_fixture_concepts_and_quiz_pass(fixture_lesson):
    assert concept_problems(fixture_lesson["concepts"], "3") == []
    assert quiz_problems(_quiz_items(fixture_lesson), "3") == []


def test_concept_count_and_duplicate_titles(fixture_lesson):
    cs = fixture_lesson["concepts"]
    assert "exactly 3" in concept_problems(cs[:2], "3")[0]
    dup = copy.deepcopy(cs)
    dup[1]["title"] = dup[0]["title"]
    assert any("different title" in p for p in concept_problems(dup, "3"))


def test_too_hard_for_kindergarten_but_fine_for_college():
    """Real college prose (FK about 14) is rejected for kindergarten and accepted for college."""
    explanation = ("Evaporation is an endothermic phase change in which water molecules gain enough kinetic energy "
                   "to escape into the atmosphere. The energy absorbed is stored as latent heat and released later "
                   "during condensation, which helps drive weather systems.")
    example = "Oceanic evaporation supplies most atmospheric moisture."
    hard = [{"title": t, "explanation": explanation, "example": example}
            for t in ("Phase Transitions Explained", "Condensation Nucleation Physics", "Precipitation Microphysics Overview")]
    assert any("too hard" in p for p in concept_problems(hard, "K"))
    assert not any("too hard" in p or "too simple" in p for p in concept_problems(hard, "C"))


def test_too_simple_for_college(fixture_lesson):
    assert any("too simple" in p for p in concept_problems(fixture_lesson["concepts"], "C"))


def test_word_limits_and_unsafe_content(fixture_lesson):
    cs = copy.deepcopy(fixture_lesson["concepts"])
    cs[0]["explanation"] = "Too short."
    cs[1]["example"] = "See www.weather.com for more."
    problems = concept_problems(cs, "3")
    assert any("Concept 1 explanation has" in p for p in problems)
    assert any("Concept 2 example" in p and "links" in p for p in problems)


def test_quiz_structure_rules(fixture_lesson):
    items = _quiz_items(fixture_lesson)
    assert "exactly 5" in quiz_problems(items[:4], "3")[0]
    bad = copy.deepcopy(items)
    bad[0]["distractors"] = bad[0]["distractors"][:2]                    # only 2 distractors
    bad[1]["distractors"][0] = bad[1]["correct"]                         # duplicate option
    bad[2]["distractors"][0] = "All of the above"                        # meaningless once shuffled
    for it in bad:
        it["concept_index"] = 0                                          # concepts 1 and 2 untested
    problems = " ".join(quiz_problems(bad, "3"))
    for expected in ("exactly 3 wrong answers", "duplicate", "of the above", "concept(s) [1, 2]"):
        assert expected in problems


def test_quiz_rejects_duplicate_questions_and_bad_concept_index(fixture_lesson):
    items = _quiz_items(fixture_lesson)
    items[1]["question"] = items[0]["question"]
    items[2]["concept_index"] = 7
    problems = " ".join(quiz_problems(items, "3"))
    assert "must be different" in problems and "concept_index 0, 1 or 2" in problems


def test_shuffle_places_correct_answer_and_spreads_positions():
    rng = random.Random(1)
    qs = [build_question("Q?", "right", ["w1", "w2", "w3"], 0, "because", rng=rng) for _ in range(400)]
    assert all(q.options[q.answer_index] == "right" and sorted(q.options) == ["right", "w1", "w2", "w3"] for q in qs)
    counts = Counter(q.answer_index for q in qs)
    assert set(counts) == {0, 1, 2, 3} and min(counts.values()) > 60     # roughly uniform (100 expected each)


def test_public_view_never_contains_answer_keys(fixture_lesson):
    view = Lesson(**fixture_lesson).public_view()
    flat = str(view)
    assert "answer_index" not in flat and "why" not in flat and "concept_index" not in flat
    assert len(view["questions"]) == 5 and all(set(q) == {"question", "options"} for q in view["questions"])


def test_concept_model_requires_fields():
    import pytest
    with pytest.raises(Exception):
        Concept(title="x")


@pytest.mark.parametrize("why, blocked", [("Great job! Magma is melted rock.", True), ("Awesome! Shield volcanoes are wide.", True),
                                          ("Correct! The vent is a tube.", True), ("Nice work, lava is hot.", True),
                                          ("Right angles measure 90 degrees.", False), ("Great Lakes hold fresh water.", False),
                                          ("Perfect squares like 9 have whole-number roots.", False),
                                          ("Melted rock is called magma.", False)])
def test_why_must_not_open_with_praise(fixture_lesson, why, blocked):
    """The explanation is shown after wrong answers too, so it can't start with "Great job!" (found in visual review)."""
    items = _quiz_items(fixture_lesson)
    items[0]["why"] = why
    assert any("not praise" in p for p in quiz_problems(items, "3")) == blocked
