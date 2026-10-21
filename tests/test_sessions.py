"""Sessions: breaks, phase order, server-side grading and the results summary."""

import pytest

from studysprint.config import EXERCISES
from studysprint.exercises import pick_exercise, summarize
from studysprint.lesson import Lesson
from studysprint.sessions import FlowError, SessionStore, results_message


@pytest.fixture
def store_and_session(fixture_lesson):
    store = SessionStore()
    return store, store.create(Lesson(**fixture_lesson))


def correct_answers(lesson):
    return [q.answer_index for q in lesson.questions]


def test_pick_exercise_uses_only_the_three_and_hits_all():
    seen = {pick_exercise() for _ in range(300)}
    assert seen == set(EXERCISES) == {"jumping_jacks", "high_knees", "pushups"}


def test_summarize_counts_in_first_seen_order():
    assert summarize(["pushups", "high_knees", "pushups"]) == [
        {"id": "pushups", "label": "10 push-ups", "times": 2},
        {"id": "high_knees", "label": "10 high knees (running in place)", "times": 1}]
    assert summarize([]) == []


def test_happy_flow_with_breaks_in_both_phases(store_and_session):
    store, s = store_and_session
    b1 = store.request_break(s.id)
    assert b1.phase == "reading"
    store.complete_break(s.id, b1.id)
    store.finish_reading(s.id)
    b2 = store.request_break(s.id)
    assert b2.phase == "quiz"
    store.complete_break(s.id, b2.id)
    r = store.submit(s.id, correct_answers(s.lesson))
    assert (r["score"], r["total"], r["percent"]) == (5, 5, 100)
    assert r["exercise_total"] == 2 and sum(e["times"] for e in r["exercises"]) == 2
    assert "Great work" in r["headline"]


def test_break_blocks_progress_until_done(store_and_session):
    store, s = store_and_session
    b = store.request_break(s.id)
    with pytest.raises(FlowError):
        store.finish_reading(s.id)
    store.complete_break(s.id, b.id)
    store.finish_reading(s.id)
    b = store.request_break(s.id)
    with pytest.raises(FlowError):
        store.submit(s.id, correct_answers(s.lesson))


def test_break_request_is_idempotent(store_and_session):
    store, s = store_and_session
    assert store.request_break(s.id) is store.request_break(s.id)
    assert len(s.breaks) == 1


def test_wrong_break_id_rejected(store_and_session):
    store, s = store_and_session
    store.request_break(s.id)
    with pytest.raises(FlowError):
        store.complete_break(s.id, "nope")


def test_phases_only_move_forward(store_and_session):
    store, s = store_and_session
    with pytest.raises(FlowError):
        store.submit(s.id, [0] * 5)                 # can't submit while reading
    store.finish_reading(s.id)
    with pytest.raises(FlowError):
        store.finish_reading(s.id)                  # can't finish reading twice
    store.submit(s.id, [0] * 5)
    with pytest.raises(FlowError):
        store.submit(s.id, [0] * 5)                 # can't resubmit
    with pytest.raises(FlowError):
        store.request_break(s.id)                   # no breaks after the quiz


@pytest.mark.parametrize("answers", [[0] * 4, [0] * 6, [0, 0, 0, 0, 4], [0, 0, 0, 0, -1], [0, 0, 0, 0, "1"]])
def test_malformed_answers_rejected(store_and_session, answers):
    store, s = store_and_session
    store.finish_reading(s.id)
    with pytest.raises(FlowError):
        store.submit(s.id, answers)


def test_partial_score_and_review(store_and_session):
    store, s = store_and_session
    store.finish_reading(s.id)
    answers = correct_answers(s.lesson)
    answers[0] = (answers[0] + 1) % 4
    answers[3] = (answers[3] + 1) % 4
    r = store.submit(s.id, answers)
    assert (r["score"], r["percent"]) == (3, 60)
    assert [item["correct"] for item in r["review"]] == [False, True, True, False, True]
    assert r["exercises"] == [] and r["exercise_total"] == 0


@pytest.mark.parametrize("pct", [0, 20, 40, 60, 80, 100])
def test_every_score_gets_a_very_positive_message(pct):
    headline, detail = results_message(pct)
    assert headline.startswith(("Great work", "PERFECT")) and "!" in headline and detail


def test_unknown_session_and_ids_are_unguessable(store_and_session):
    store, s = store_and_session
    with pytest.raises(KeyError):
        store.get("nope")
    assert len(s.id) >= 20
