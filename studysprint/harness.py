"""The harness: deterministic orchestration of the five lesson-building agents.

Pipeline for one lesson::

    topic gate (code) → Intake → Curriculum → ┬ Diagram ┬ → Reviewer → [up to 2 revisions] → publish
                                              └ Quiz    ┘

* **Code decides the flow**, not an agent. Each stage runs one agent, then a
  **hand-off validator** re-checks what that agent produced (defense in depth: its tool
  already validated it). A failed validator stops the build, so later agents never build
  on bad input.
* **Diagram and Quiz run in parallel**, since both depend only on the concepts. This
  cuts about a third off the wait.
* **The Reviewer** may send back the concepts, the quiz or the diagram, up to
  ``cfg.max_revisions`` times. If the concepts change, the diagram and quiz are redone
  too (in parallel), because both are derived from the concepts. A live eval showed a
  revised concept list contradicting the old diagram. The Reviewer checks again after
  each revision; a final rejection fails the build rather than publishing a lesson a
  reviewer found wrong.
* **Every build gets a fresh run directory** (``runs/<timestamp>-<slug>/``) holding
  ``trace.jsonl``, ``lesson.json``, ``review.json`` and ``manifest.json``. The manifest
  is written even when the build fails, so failures are visible to evals.
* ``progress`` callbacks report the current stage to the web app's progress screen.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from studysprint import __version__
from studysprint.agents.runtime import Trace, make_client
from studysprint.config import GRADE_LABELS, AgentConfig
from studysprint.lesson import Diagram, Lesson, build_question, concept_problems, quiz_problems
from studysprint.paths import new_run_dir, write_json
from studysprint.safety import topic_gate, validate_subject
from studysprint.svg_sanitize import check_svg


class StageError(RuntimeError):
    """A stage's output failed its hand-off check. The message is user-facing."""


class Declined(RuntimeError):
    """The subject can't be taught at this grade (topic gate or Intake Agent). The message is user-facing."""


@dataclass
class BuildContext:
    """Shared state for one lesson build. Agents' tools write their results here.

    Attributes:
        subject: The validated subject the learner typed.
        grade: Grade code.
        run_dir: This build's run directory.
        trace: The shared :class:`Trace`.
        client: Anthropic client (or a fake in tests).
        cfg: :class:`AgentConfig`.
        progress: Called with a short stage description as the build advances.
        topic: Set by the Intake Agent's ``set_topic`` tool.
        scope_note: Set by ``set_topic``: what the lesson should cover.
        declined: Set by the Intake Agent's ``decline`` tool.
        concepts: Set by the Curriculum Agent (validated :class:`Concept` objects).
        diagram: Set by the Diagram Agent (a sanitized :class:`Diagram`).
        quiz_items: Set by the Quiz Agent (validated raw items, before shuffling).
        review: Set by the Reviewer Agent: ``{"approved": bool, "target": ..., "issues": [...]}``.
    """
    subject: str
    grade: str
    run_dir: object
    trace: Trace
    client: object
    cfg: AgentConfig = field(default_factory=AgentConfig)
    progress: Callable[[str], None] = lambda stage: None
    topic: Optional[str] = None
    scope_note: Optional[str] = None
    declined: Optional[str] = None
    concepts: Optional[list] = None
    diagram: Optional[Diagram] = None
    quiz_items: Optional[list] = None
    review: Optional[dict] = None

    @property
    def grade_label(self):
        """Human-readable grade, e.g. ``"Grade 5"``."""
        return GRADE_LABELS[self.grade]


def _slug(text):
    """A short, filesystem-safe slug for run-directory names ("Cells & DNA" → "cells-dna")."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "lesson"


def new_context(subject, grade, client=None, cfg=None, progress=None, runs_root=None):
    """Validate the inputs and prepare a :class:`BuildContext` with a fresh run directory.

    Args:
        subject: Raw subject text from the learner.
        grade: Grade code.
        client: Anthropic client; created from the environment if omitted.
        cfg: Optional :class:`AgentConfig`.
        progress: Optional stage callback.
        runs_root: Optional parent for the run directory (default ``runs/``; tests use a scratch folder).

    Returns:
        A new :class:`BuildContext`.

    Raises:
        SafetyError: If the subject fails input validation.
        ValueError: If the grade is unknown.
    """
    subject = validate_subject(subject)
    if grade not in GRADE_LABELS:
        raise ValueError(f"Unknown grade {grade!r}.")
    label = f"{_slug(subject)}-g{grade.lower()}"
    run_dir = new_run_dir(label, root=runs_root) if runs_root else new_run_dir(label)
    return BuildContext(subject=subject, grade=grade, run_dir=run_dir, trace=Trace(run_dir / "trace.jsonl"),
                        client=client or make_client(), cfg=cfg or AgentConfig(),
                        progress=progress or (lambda stage: None))


# --------------------------------------------------------------------------- hand-off validators
# Each re-checks a stage's output with the same functions its tool used. A tool might be
# skipped (an agent that replies without calling it), so the harness never assumes.

def validate_intake(ctx):
    """Intake must have set a topic that also passes the topic gate, or declined.

    Raises:
        Declined: If the agent declined (the learner sees its reason).
        StageError: If it did neither.
    """
    if ctx.declined:
        raise Declined(ctx.declined)
    if not ctx.topic:
        raise StageError("The Intake Agent did not choose a topic.")


def validate_curriculum(ctx):
    """There must be exactly three concepts that pass every concept rule.

    Raises:
        StageError: Otherwise.
    """
    if not ctx.concepts:
        raise StageError("The Curriculum Agent did not submit concepts.")
    problems = concept_problems(ctx.concepts, ctx.grade)
    if problems:
        raise StageError("Concepts failed checks: " + "; ".join(problems))


def validate_diagram(ctx):
    """There must be a diagram whose SVG still passes the sanitizer.

    Raises:
        StageError: Otherwise.
    """
    if not ctx.diagram:
        raise StageError("The Diagram Agent did not submit a diagram.")
    if not check_svg(ctx.diagram.svg).ok:
        raise StageError("The diagram failed the SVG safety check.")


def validate_quiz(ctx):
    """There must be five questions that pass every quiz rule.

    Raises:
        StageError: Otherwise.
    """
    if not ctx.quiz_items:
        raise StageError("The Quiz Agent did not submit a quiz.")
    problems = quiz_problems(ctx.quiz_items, ctx.grade)
    if problems:
        raise StageError("Quiz failed checks: " + "; ".join(problems))


# --------------------------------------------------------------------------- stages

def run_stage(ctx, module, validator, task=None):
    """Run one agent stage, then its hand-off validator.

    Args:
        ctx: The :class:`BuildContext`.
        module: An agent module exposing ``NAME``, ``build(ctx)`` and ``task(ctx)``.
        validator: A function from the validators above.
        task: Optional task override (used for revisions).

    Returns:
        The agent's final text reply.

    Raises:
        Whatever the validator or the agent raises.

    Side effects:
        Logs ``stage_start`` and ``stage_ok`` events with timings.
    """
    t0 = time.perf_counter()
    ctx.trace.log(type="stage_start", stage=module.NAME)
    reply = module.build(ctx).run(task or module.task(ctx))
    validator(ctx)
    ctx.trace.log(type="stage_ok", stage=module.NAME, seconds=round(time.perf_counter() - t0, 2))
    return reply


def _parallel(ctx, *stages):
    """Run several ``(module, validator)`` stages at once and re-raise the first failure.

    Args:
        ctx: The build context.
        *stages: ``(module, validator)`` pairs.
    """
    with ThreadPoolExecutor(max_workers=len(stages)) as pool:
        futures = [pool.submit(run_stage, ctx, m, v) for m, v in stages]
        for f in futures:
            f.result()      # re-raises a failed stage's exception


def assemble(ctx, lesson_id):
    """Turn validated stage outputs into a :class:`Lesson`, shuffling each quiz's options.

    Args:
        ctx: A build context in which every stage has passed.
        lesson_id: The public ID to give the lesson.

    Returns:
        The :class:`Lesson`.
    """
    questions = [build_question(it["question"], it["correct"], it["distractors"], it["concept_index"], it["why"])
                 for it in ctx.quiz_items]
    return Lesson(id=lesson_id, subject=ctx.subject, topic=ctx.topic, grade=ctx.grade, concepts=ctx.concepts,
                  diagram=ctx.diagram, questions=questions,
                  meta={"run": ctx.run_dir.name, "scope_note": ctx.scope_note})


def build_lesson(ctx, lesson_id=None):
    """Run the whole pipeline for one lesson.

    Args:
        ctx: A :class:`BuildContext` from :func:`new_context`.
        lesson_id: Optional public ID (defaults to the run directory's name).

    Returns:
        The published :class:`Lesson` (also saved as ``lesson.json`` in the run directory).

    Raises:
        Declined: If the topic gate or the Intake Agent declined the subject.
        StageError: If a stage's output failed its checks, or the final review still rejected the lesson.
        studysprint.agents.runtime.AgentError: If an agent ran out of turns or budget.

    Side effects:
        Writes ``lesson.json``, ``review.json`` and ``manifest.json`` (always) to the run directory.
    """
    # Imported here, not at module level: the agent modules import
    # describe_lesson_for_agents from this module, so a top-level import would be circular.
    from studysprint.agents import curriculum_agent, diagram_agent, intake_agent, quiz_agent, reviewer_agent

    t0 = time.perf_counter()
    status, error, lesson = "failed", None, None
    try:
        # 0. Topic gate (code). It runs before any agent, so a blocked subject costs nothing.
        reason = topic_gate(ctx.subject, ctx.grade)
        if reason:
            raise Declined(reason)

        ctx.progress("Choosing the best topic for your grade")
        run_stage(ctx, intake_agent, validate_intake)

        ctx.progress("Writing three key concepts")
        run_stage(ctx, curriculum_agent, validate_curriculum)

        ctx.progress("Drawing a diagram and writing your quiz")
        _parallel(ctx, (diagram_agent, validate_diagram), (quiz_agent, validate_quiz))

        for attempt in range(ctx.cfg.max_revisions + 1):
            ctx.progress("Checking everything for accuracy" if attempt == 0 else "Polishing the lesson")
            ctx.review = None
            run_stage(ctx, reviewer_agent, lambda c: None)
            if ctx.review and ctx.review["approved"]:
                break
            if attempt == ctx.cfg.max_revisions:
                raise StageError("The lesson didn't pass review. Please try again or rephrase the subject.")
            revise(ctx, ctx.review or {"target": "quiz", "issues": ["The reviewer gave no verdict."]})

        lesson = assemble(ctx, lesson_id or ctx.run_dir.name)
        write_json(ctx.run_dir / "lesson.json", lesson.model_dump())
        status = "ok"
        return lesson
    except Declined as exc:
        status, error = "declined", str(exc)
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        ctx.trace.log(type="build_failed", error=error)
        raise
    finally:
        write_json(ctx.run_dir / "review.json", ctx.review or {})
        write_json(ctx.run_dir / "manifest.json", {
            "status": status, "error": error, "version": __version__, "model": ctx.cfg.model,
            "subject": ctx.subject, "grade": ctx.grade, "topic": ctx.topic,
            "seconds": round(time.perf_counter() - t0, 1),
            "usage_tokens": ctx.trace.usage, "cost_usd": ctx.trace.cost_usd(),
            "cost_by_agent_usd": {a: ctx.trace.cost_usd(u) for a, u in ctx.trace.by_agent.items()},
        })


def revise(ctx, review):
    """Apply one round of reviewer feedback by re-running the affected agent(s).

    Args:
        ctx: The build context.
        review: The Reviewer's verdict: ``{"target": "concepts"|"quiz"|"diagram", "issues": [...]}``.

    Side effects:
        Replaces the targeted outputs in ``ctx``. A concepts revision also redoes the
        diagram and the quiz, because both are derived from the concepts and would
        otherwise contradict the new version.
    """
    from studysprint.agents import curriculum_agent, diagram_agent, quiz_agent

    issues = "\n".join(f"- {i}" for i in review.get("issues", [])) or "- (no details given)"
    note = f"\n\nA reviewer found these problems in the previous version. Fix them:\n{issues}"
    target = review.get("target", "quiz")
    if target == "concepts":
        ctx.concepts = None
        run_stage(ctx, curriculum_agent, validate_curriculum, curriculum_agent.task(ctx) + note)
        ctx.diagram, ctx.quiz_items = None, None
        _parallel(ctx, (diagram_agent, validate_diagram), (quiz_agent, validate_quiz))
    elif target == "diagram":
        ctx.diagram = None
        run_stage(ctx, diagram_agent, validate_diagram, diagram_agent.task(ctx) + note)
    else:
        ctx.quiz_items = None
        run_stage(ctx, quiz_agent, validate_quiz, quiz_agent.task(ctx) + note)


def describe_lesson_for_agents(ctx, with_answers=False):
    """Plain-text rendering of the lesson so far, used in later agents' tasks.

    Args:
        ctx: The build context.
        with_answers: Include the quiz with correct answers marked (Reviewer only).

    Returns:
        A multi-line string.
    """
    lines = [f"Topic: {ctx.topic} ({ctx.grade_label})", f"Scope: {ctx.scope_note or ''}", "", "Concepts:"]
    for i, c in enumerate(ctx.concepts or []):
        lines += [f"[{i}] {c.title}", f"    {c.explanation}", f"    Example: {c.example}"]
    if ctx.diagram:
        lines += ["", f"Diagram labels: {', '.join(ctx.diagram.labels)}", f"Diagram alt text: {ctx.diagram.alt_text}"]
    if with_answers and ctx.quiz_items:
        lines += ["", "Quiz (correct answer marked ✓):"]
        for n, it in enumerate(ctx.quiz_items, start=1):
            lines.append(f"Q{n} (tests concept {it['concept_index']}): {it['question']}")
            lines.append(f"    ✓ {it['correct']}")
            lines += [f"    ✗ {d}" for d in it["distractors"]]
            lines.append(f"    Why: {it['why']}")
    return "\n".join(lines)

