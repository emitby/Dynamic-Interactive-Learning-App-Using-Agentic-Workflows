"""Scorers: code-graded checks of one saved lesson build (a ``runs/<id>/`` directory).

Each scorer returns a :class:`Score` (0-1, a pass threshold, and details explaining any
failure). They're deterministic and free, so any saved run can be re-scored at any time,
including after a rule changes.

What they verify, beyond the tool-time checks the agents already passed:
    * ``run_health``: the build finished cleanly, every agent ran, and cost stayed under the cap.
    * ``lesson_structure``: lesson.json is a complete, valid lesson.
    * ``grade_fit``: the reading-level and length rules still hold on the published text.
    * ``content_safety``: every learner-facing string passes the safety checks.
    * ``svg_safety``: the stored diagram re-passes the sanitizer and is already canonical.
    * ``quiz_grounding``: each correct answer is supported by the concept it cites
      (a heuristic: the answer's key words appear in that concept or the diagram labels).
    * ``diagram_relevance``: the diagram's labels share vocabulary with the concepts.
    * ``reviewed``: an independent Reviewer Agent approved the lesson.
"""

import json
import re
from dataclasses import asdict, dataclass, field

from studysprint.config import AgentConfig, N_CONCEPTS, N_OPTIONS, N_QUESTIONS
from studysprint.lesson import Lesson, concept_problems, quiz_problems
from studysprint.paths import confine
from studysprint.safety import content_problems
from studysprint.svg_sanitize import check_svg

AGENTS = {"Intake Agent", "Curriculum Agent", "Diagram Agent", "Quiz Agent", "Reviewer Agent"}

# Common words ignored when comparing vocabulary (grounding and relevance).
STOPWORDS = set("""a an the and or but of to in on at by for with from into onto over under is are was were be been
being it its this that these those they them their there here what which who whom whose when where why how
not no yes can could will would should may might must do does did done has have had very more most less least
also just only than then so such some any all each every both either neither other another same your you we our
he she his her him one two three four five many much few lot lots thing things called call make makes made""".split())


@dataclass
class Score:
    """One scorer's result.

    Attributes:
        name: Scorer name.
        score: Between 0 and 1.
        threshold: The minimum score to pass.
        details: Explanation, especially of failures.
    """
    name: str
    score: float
    threshold: float = 1.0
    details: dict = field(default_factory=dict)

    @property
    def passed(self):
        """``True`` when ``score >= threshold``."""
        return self.score >= self.threshold

    def to_dict(self):
        """JSON-ready form, including ``passed``."""
        return {**asdict(self), "passed": self.passed}


class Run:
    """Read-only access to one run directory's artifacts."""

    def __init__(self, path):
        """Args: path: A run directory (confined to the project)."""
        self.dir = confine(path)

    def json(self, name):
        """Parse ``name`` in the run directory, or return ``None`` if it's missing."""
        p = self.dir / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def trace(self):
        """All trace events, in order."""
        p = self.dir / "trace.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l] if p.exists() else []

    def lesson(self):
        """The published :class:`Lesson`, or ``None`` if the build didn't publish one."""
        data = self.json("lesson.json")
        return Lesson(**data) if data else None


def content_words(text):
    """Lower-case words of 3+ letters that aren't stopwords, with a crude plural fold ("volcanoes" → "volcano")."""
    words = set()
    for w in re.findall(r"[a-z]{3,}", text.lower()):
        if w in STOPWORDS:
            continue
        words.add(w[:-2] if w.endswith("es") and len(w) > 4 else w[:-1] if w.endswith("s") and len(w) > 3 else w)
    return words


def _missing(name, what):
    """A zero score for a scorer whose input artifact is missing."""
    return Score(name, 0.0, details={"error": f"missing {what}"})


# --------------------------------------------------------------------------- scorers

def run_health(run, max_cost_usd=AgentConfig().max_cost_usd):
    """Status ok, all five agents finished, no tool crashes, cost under the cap."""
    m = run.json("manifest.json") or {}
    trace = run.trace()
    finished = {e["agent"] for e in trace if e.get("type") == "finish"}
    tools = [e for e in trace if e.get("type") == "tool"]
    checks = {"status_ok": m.get("status") == "ok", "all_agents_finished": AGENTS <= finished,
              "no_tool_crashes": not any(e.get("type") == "tool_crash" for e in trace),
              "cost_under_cap": (m.get("cost_usd") or 0) <= max_cost_usd}
    return Score("run_health", sum(checks.values()) / len(checks), details={
        **checks, "cost_usd": m.get("cost_usd"), "seconds": m.get("seconds"),
        "tool_calls": len(tools), "self_corrected_errors": sum(not e["ok"] for e in tools)})


def lesson_structure(run):
    """A complete lesson: 3 concepts, 5 questions with 4 distinct options and a valid answer key."""
    lesson = run.lesson()
    if lesson is None:
        return _missing("lesson_structure", "lesson.json")
    checks = {"three_concepts": len(lesson.concepts) == N_CONCEPTS,
              "five_questions": len(lesson.questions) == N_QUESTIONS,
              "four_distinct_options": all(len({o.lower() for o in q.options}) == N_OPTIONS for q in lesson.questions),
              "valid_answer_keys": all(0 <= q.answer_index < len(q.options) for q in lesson.questions),
              "every_concept_tested": {q.concept_index for q in lesson.questions} == set(range(N_CONCEPTS)),
              "diagram_has_alt_text": len(lesson.diagram.alt_text) >= 10}
    return Score("lesson_structure", sum(checks.values()) / len(checks), details=checks)


def grade_fit(run):
    """The published concepts and quiz still pass every reading-level and length rule for the grade."""
    lesson = run.lesson()
    if lesson is None:
        return _missing("grade_fit", "lesson.json")
    items = [{"question": q.question, "correct": q.options[q.answer_index],
              "distractors": [o for i, o in enumerate(q.options) if i != q.answer_index],
              "concept_index": q.concept_index, "why": q.why} for q in lesson.questions]
    problems = concept_problems(lesson.concepts, lesson.grade) + quiz_problems(items, lesson.grade)
    return Score("grade_fit", 0.0 if problems else 1.0, details={"grade": lesson.grade, "problems": problems})


def content_safety(run):
    """Every learner-facing string passes the safety checks (links, personal info, unkind words, ...)."""
    lesson = run.lesson()
    if lesson is None:
        return _missing("content_safety", "lesson.json")
    texts = [lesson.topic, lesson.diagram.alt_text, lesson.diagram.caption, *lesson.diagram.labels]
    for c in lesson.concepts:
        texts += [c.title, c.explanation, c.example]
    for q in lesson.questions:
        texts += [q.question, q.why, *q.options]
    hits = [f"{t[:40]!r}: {p}" for t in texts for p in content_problems(t)]
    return Score("content_safety", 0.0 if hits else 1.0, details={"strings_checked": len(texts), "problems": hits})


def svg_safety(run):
    """The stored SVG re-passes the sanitizer, is already in canonical form and has 3+ labels."""
    lesson = run.lesson()
    if lesson is None:
        return _missing("svg_safety", "lesson.json")
    r = check_svg(lesson.diagram.svg)
    checks = {"sanitizer_passes": r.ok, "already_canonical": r.ok and r.svg == lesson.diagram.svg,
              "no_script_text": "<script" not in lesson.diagram.svg.lower()}
    return Score("svg_safety", sum(checks.values()) / len(checks), details={**checks, "problems": r.problems})


def quiz_grounding(run, threshold=0.8):
    """Share of questions whose correct answer is supported by the concept they cite.

    Heuristic: at least one content word of the correct answer (or, for very short
    answers like "Lava", the answer itself) appears in the cited concept's text or the
    diagram labels. The threshold is 0.8 because a correct answer may legitimately be a
    paraphrase. The Reviewer Agent judges meaning; this catches questions about material
    the lesson never taught.
    """
    lesson = run.lesson()
    if lesson is None:
        return _missing("quiz_grounding", "lesson.json")
    labels = " ".join(lesson.diagram.labels)
    ungrounded = []
    for n, q in enumerate(lesson.questions, start=1):
        c = lesson.concepts[q.concept_index]
        source = content_words(f"{c.title} {c.explanation} {c.example} {labels}")
        answer = content_words(q.options[q.answer_index])
        if answer and not (answer & source):
            ungrounded.append(f"Q{n}: {q.options[q.answer_index]!r} not found in concept {q.concept_index}")
    score = 1 - len(ungrounded) / len(lesson.questions)
    return Score("quiz_grounding", score, threshold, {"ungrounded": ungrounded})


def diagram_relevance(run):
    """At least two diagram labels share a content word with the concepts or topic (an on-topic picture)."""
    lesson = run.lesson()
    if lesson is None:
        return _missing("diagram_relevance", "lesson.json")
    vocab = content_words(lesson.topic + " " + " ".join(f"{c.title} {c.explanation} {c.example}" for c in lesson.concepts))
    on_topic = [lbl for lbl in lesson.diagram.labels if content_words(lbl) & vocab]
    return Score("diagram_relevance", 1.0 if len(on_topic) >= 2 else 0.0,
                 details={"labels": lesson.diagram.labels, "on_topic": on_topic})


def reviewed(run):
    """The Reviewer Agent approved the published lesson."""
    review = run.json("review.json") or {}
    return Score("reviewed", 1.0 if review.get("approved") else 0.0, details={"review": review})


SCORERS = [run_health, lesson_structure, grade_fit, content_safety, svg_safety, quiz_grounding, diagram_relevance,
           reviewed]


def score_run(path):
    """Run every scorer on a run directory.

    A scorer that crashes scores 0 with the error recorded, so one broken scorer can't
    hide the others' results.

    Args:
        path: A run directory.

    Returns:
        A list of :class:`Score`.
    """
    run = Run(path)
    out = []
    for scorer in SCORERS:
        try:
            out.append(scorer(run))
        except Exception as exc:
            out.append(Score(scorer.__name__, 0.0, details={"error": f"{type(exc).__name__}: {exc}"}))
    return out
