"""The evals themselves are tested: every scorer must PASS a good lesson and FAIL a lesson with its defect.

A scorer that can't fail proves nothing (tasks/lessons.md), so each defect below is
planted on purpose in a copy of the demo lesson.
"""

import copy
import json

import pytest

from evals.redteam import run_cases
from evals.scorers import score_run
from studysprint.paths import write_json


def make_run(workdir, lesson, review=None, manifest=None, trace=None):
    """Write a fake run directory with the given artifacts and return its path."""
    write_json(workdir / "lesson.json", lesson)
    write_json(workdir / "review.json", review if review is not None else {"approved": True})
    write_json(workdir / "manifest.json", manifest or {"status": "ok", "cost_usd": 0.2, "seconds": 50})
    agents = ["Intake Agent", "Curriculum Agent", "Diagram Agent", "Quiz Agent", "Reviewer Agent"]
    lines = trace if trace is not None else [{"type": "finish", "agent": a} for a in agents]
    (workdir / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in lines), encoding="utf-8")
    return workdir


def by_name(scores):
    return {s.name: s for s in scores}


def test_good_lesson_passes_every_scorer(workdir, fixture_lesson):
    scores = score_run(make_run(workdir, fixture_lesson))
    failed = {s.name: s.details for s in scores if not s.passed}
    assert failed == {}


def _mutated(fixture_lesson, fn):
    lesson = copy.deepcopy(fixture_lesson)
    fn(lesson)
    return lesson


@pytest.mark.parametrize("scorer, mutate", [
    ("lesson_structure", lambda L: L["questions"].pop()),
    ("lesson_structure", lambda L: L["questions"][0].__setitem__("answer_index", 9)),
    ("grade_fit", lambda L: L["concepts"][0].__setitem__("explanation",
        "Evaporation constitutes an endothermic thermodynamic transformation whereby intermolecular cohesion "
        "is overcome by molecular kinetic energy, facilitating atmospheric vapor transport phenomena.")),
    ("content_safety", lambda L: L["questions"][1].__setitem__("why", "Learn more at www.weather.gov")),
    ("svg_safety", lambda L: L["diagram"].__setitem__("svg", L["diagram"]["svg"].replace("</svg>", "<script>x()</script></svg>"))),
    ("quiz_grounding", lambda L: [q["options"].__setitem__(q["answer_index"], f"Tectonic subduction {i}")
                                  for i, q in enumerate(L["questions"])]),
    ("diagram_relevance", lambda L: L["diagram"].__setitem__("labels", ["Zebra", "Guitar", "Pizza"])),
])
def test_scorer_fails_on_its_defect(workdir, fixture_lesson, scorer, mutate):
    scores = by_name(score_run(make_run(workdir, _mutated(fixture_lesson, mutate))))
    assert not scores[scorer].passed


def test_unapproved_review_fails(workdir, fixture_lesson):
    scores = by_name(score_run(make_run(workdir, fixture_lesson, review={"approved": False})))
    assert not scores["reviewed"].passed


@pytest.mark.parametrize("manifest, trace", [
    ({"status": "failed", "cost_usd": 0.1}, None),
    ({"status": "ok", "cost_usd": 5.0}, None),
    (None, [{"type": "finish", "agent": "Intake Agent"}]),
    (None, [{"type": "tool_crash", "agent": "Quiz Agent"}]),
])
def test_run_health_fails_on_bad_runs(workdir, fixture_lesson, manifest, trace):
    scores = by_name(score_run(make_run(workdir, fixture_lesson, manifest=manifest, trace=trace)))
    assert not scores["run_health"].passed


def test_missing_lesson_scores_zero_not_crash(workdir):
    scores = score_run(workdir)
    assert all(not s.passed for s in scores if s.name != "run_health")


def test_redteam_all_pass():
    failed = [c for c in run_cases() if not c["passed"]]
    assert failed == []
    assert len(run_cases()) >= 40
