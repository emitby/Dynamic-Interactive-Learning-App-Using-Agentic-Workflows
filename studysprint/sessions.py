"""Study sessions: the reading → quiz → results flow, movement breaks and server-side grading.

A *session* is one learner working through one lesson. It lives only in memory. No
names, accounts or personal data are collected, and nothing about the learner is
written to disk.

Design decisions:
    * **The server owns the facts that matter.** Which exercise was assigned, which were
      completed, the answers and the score are all recorded here. The browser only runs
      the visible 60-second countdown and asks for a break when it reaches zero.
    * **Phases only move forward:** ``reading`` → ``quiz`` → ``done``. A request that
      doesn't fit the current phase (submitting answers while still reading, for example)
      is refused with :class:`FlowError`.
    * **A break must be completed before continuing.** While a break is pending, the
      learner can't finish reading or submit the quiz, matching the requirement that the
      exercise comes "before continuing".
    * Session IDs are 128-bit random tokens from :mod:`secrets`, so they can't be guessed.
"""

import secrets
import threading
import time
from dataclasses import dataclass, field

from studysprint.config import EXERCISES, N_QUESTIONS
from studysprint.exercises import pick_exercise, summarize
from studysprint.lesson import Lesson


class FlowError(ValueError):
    """A request that doesn't fit the session's current phase. The message is user-facing."""


@dataclass
class Break:
    """One movement break.

    Attributes:
        id: Short random ID, used by the browser to confirm completion.
        exercise: Exercise ID (a key of :data:`EXERCISES`).
        phase: ``"reading"`` or ``"quiz"``, the phase whose timer expired.
        completed: ``True`` once the learner clicked "I did it!".
    """
    id: str
    exercise: str
    phase: str
    completed: bool = False


@dataclass
class Session:
    """State for one learner working through one lesson.

    Attributes:
        id: Unguessable session token.
        lesson: The full lesson, including answer keys. Never sent to the browser as-is.
        phase: ``"reading"``, ``"quiz"`` or ``"done"``.
        breaks: Every break assigned, in order.
        answers: The submitted answer indexes, once graded.
        created: Monotonic creation time, used to expire old sessions.
    """
    id: str
    lesson: Lesson
    phase: str = "reading"
    breaks: list = field(default_factory=list)
    answers: list = field(default_factory=list)
    created: float = field(default_factory=time.monotonic)

    @property
    def pending_break(self):
        """The assigned but not yet completed break, or ``None``."""
        return next((b for b in self.breaks if not b.completed), None)


def results_message(percent):
    """Pick the big, enthusiastic headline for the results screen.

    Every score gets a genuinely positive message (user requirement: "something
    incredibly positive"). The tiers change *what* is celebrated (mastery, progress or
    effort), so the praise stays honest at every score.

    Args:
        percent: The score as a percentage, 0-100.

    Returns:
        A ``(headline, detail)`` pair of strings.
    """
    if percent == 100:
        return "PERFECT SCORE! Great work!", "You nailed every single question. You're a superstar learner!"
    if percent >= 80:
        return "Great work! Amazing job!", "You really know this stuff. Your brain and your body both got a workout!"
    if percent >= 60:
        return "Great work! You're on a roll!", "You learned a lot today. Every question makes your brain stronger!"
    return ("Great work! What a brave effort!",
            "Learning something new takes courage, and you did it. Try again and watch your score climb!")


class SessionStore:
    """Thread-safe, in-memory store of active sessions.

    FastAPI runs synchronous endpoints in a thread pool, so every access goes through
    one lock. Sessions older than ``ttl_seconds`` are dropped when new ones are created,
    which keeps memory bounded. That only removes in-memory objects; no files are involved.
    """

    def __init__(self, ttl_seconds=6 * 3600):
        """Create an empty store.

        Args:
            ttl_seconds: How long an idle session is kept. The default is 6 hours.
        """
        self._sessions = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def create(self, lesson):
        """Start a new session in the reading phase.

        Args:
            lesson: A validated :class:`Lesson`.

        Returns:
            The new :class:`Session`.
        """
        with self._lock:
            now = time.monotonic()
            self._sessions = {k: s for k, s in self._sessions.items() if now - s.created < self._ttl}
            s = Session(id=secrets.token_urlsafe(16), lesson=lesson)
            self._sessions[s.id] = s
            return s

    def get(self, session_id):
        """Look up a session.

        Args:
            session_id: The token returned by :meth:`create`.

        Returns:
            The :class:`Session`.

        Raises:
            KeyError: If the token is unknown or expired.
        """
        with self._lock:
            return self._sessions[session_id]

    # ------------------------------------------------------------------ flow actions
    # Each action takes the lock, so two quick clicks can't both change the state.

    def request_break(self, session_id):
        """Assign a movement break because the visible timer reached zero.

        Idempotent: if a break is already pending (for example the browser retried the
        request), the same break is returned, so one timeout never produces two exercises.

        Args:
            session_id: Session token.

        Returns:
            The pending :class:`Break`.

        Raises:
            FlowError: If the quiz is already finished.
        """
        with self._lock:
            s = self._sessions[session_id]
            if s.phase == "done":
                raise FlowError("The quiz is finished; no more breaks needed.")
            if s.pending_break:
                return s.pending_break
            b = Break(id=secrets.token_hex(4), exercise=pick_exercise(), phase=s.phase)
            s.breaks.append(b)
            return b

    def complete_break(self, session_id, break_id):
        """Record that the learner did the exercise, so the timer can restart.

        Args:
            session_id: Session token.
            break_id: The ID of the pending break.

        Returns:
            The completed :class:`Break`.

        Raises:
            FlowError: If ``break_id`` isn't the pending break.
        """
        with self._lock:
            s = self._sessions[session_id]
            b = s.pending_break
            if b is None or b.id != break_id:
                raise FlowError("That break isn't active.")
            b.completed = True
            return b

    def finish_reading(self, session_id):
        """Move from reading to the quiz ("Finished Reading" was selected).

        Raises:
            FlowError: If a break is pending or the session isn't in the reading phase.
        """
        with self._lock:
            s = self._sessions[session_id]
            if s.pending_break:
                raise FlowError("Finish your exercise break first!")
            if s.phase != "reading":
                raise FlowError("Reading is already finished.")
            s.phase = "quiz"

    def submit(self, session_id, answers):
        """Grade the quiz and finish the session.

        Args:
            session_id: Session token.
            answers: One option index per question, in question order.

        Returns:
            The results dict described in :func:`results`.

        Raises:
            FlowError: If not in the quiz phase, a break is pending, or the answers are
                malformed (wrong count, or an index outside the options).
        """
        with self._lock:
            s = self._sessions[session_id]
            if s.phase != "quiz":
                raise FlowError("The quiz isn't open.")
            if s.pending_break:
                raise FlowError("Finish your exercise break first!")
            qs = s.lesson.questions
            if len(answers) != N_QUESTIONS or any(
                    not isinstance(a, int) or not 0 <= a < len(q.options) for a, q in zip(answers, qs)):
                raise FlowError(f"Please answer all {N_QUESTIONS} questions.")
            s.answers = list(answers)
            s.phase = "done"
            return results(s)


def results(session):
    """Build the results screen data for a finished session.

    Args:
        session: A :class:`Session` in the ``done`` phase.

    Returns:
        A dict with:
            * ``score``, ``total`` and ``percent`` (rounded to a whole number)
            * ``headline`` and ``detail`` from :func:`results_message`
            * ``review``: per question, the learner's answer, the correct answer and why.
              Shown only after grading, so revealing keys here is safe.
            * ``exercises``: completed breaks summarized by :func:`studysprint.exercises.summarize`
            * ``exercise_total``: the number of breaks completed
    """
    qs = session.lesson.questions
    score = sum(a == q.answer_index for a, q in zip(session.answers, qs))
    percent = round(100 * score / len(qs))
    headline, detail = results_message(percent)
    done = [b.exercise for b in session.breaks if b.completed]
    return {
        "score": score, "total": len(qs), "percent": percent, "headline": headline, "detail": detail,
        "review": [{"question": q.question, "your_answer": q.options[a], "correct_answer": q.options[q.answer_index],
                    "correct": a == q.answer_index, "why": q.why} for a, q in zip(session.answers, qs)],
        "exercises": summarize(done),
        "exercise_total": len(done),
    }


def exercise_label(exercise_id):
    """User-facing instruction for an exercise ID, e.g. ``"10 push-ups"``."""
    return EXERCISES[exercise_id]
