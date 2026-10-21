"""Central configuration: grades, reading-level bands, timer, exercises and agent settings.

Keeping every tunable rule here means the agents' prompts, the validators, the web app
and the evals all read the **same** numbers. A rule can't drift between the text an
agent is told and the check that enforces it.
"""

from dataclasses import dataclass

# --------------------------------------------------------------------------- grades

#: Grade codes accepted by the API, in display order. "K" is kindergarten; "C" is college.
GRADES = ("K", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "C")

#: Human-readable labels for the grade dropdown and agent prompts.
GRADE_LABELS = {"K": "Kindergarten", "C": "College", **{str(g): f"Grade {g}" for g in range(1, 13)}}


def grade_number(grade):
    """Map a grade code to a number on a single scale, for reading-level math.

    Args:
        grade: One of :data:`GRADES`.

    Returns:
        ``0`` for kindergarten, ``1``–``12`` for school grades, and ``14`` for college
        (roughly a sophomore, the middle of an undergraduate program).

    Raises:
        ValueError: If ``grade`` isn't a known grade code.
    """
    if grade not in GRADES:
        raise ValueError(f"Unknown grade {grade!r}. Expected one of {', '.join(GRADES)}.")
    if grade == "K":
        return 0
    if grade == "C":
        return 14
    return int(grade)


@dataclass(frozen=True)
class Band:
    """Writing constraints for a group of grades.

    Attributes:
        name: Band name shown in prompts ("early elementary" ...).
        words_min: Minimum words in one concept explanation.
        words_max: Maximum words in one concept explanation.
        max_sentence_words: Maximum *average* words per sentence across a lesson's text.
            Short sentences are the strongest single driver of readability for young readers.
    """
    name: str
    words_min: int
    words_max: int
    max_sentence_words: float


def band_for(grade):
    """Return the :class:`Band` (length and sentence limits) for a grade code.

    The bands follow common US reading-development stages. Word limits keep each
    concept readable within the 1-minute reading window: a kindergartener hears or reads
    a few short sentences, while a college student reads a dense paragraph.

    Args:
        grade: One of :data:`GRADES`.

    Returns:
        The matching :class:`Band`.
    """
    n = grade_number(grade)
    if n <= 2:
        return Band("early elementary (K-2)", 12, 60, 12)
    if n <= 5:
        return Band("upper elementary (3-5)", 20, 80, 16)
    if n <= 8:
        return Band("middle school (6-8)", 30, 110, 20)
    if n <= 12:
        return Band("high school (9-12)", 35, 130, 24)
    return Band("college", 40, 160, 30)


def readability_range(grade):
    """Allowed Flesch-Kincaid grade range for a lesson aimed at ``grade``.

    Formula and limits:
        * **Upper bound = grade + 3** (college: 18). FK is noisy on short texts and
          subject words ("photosynthesis") push it up even in simple prose, so a strict
          "at or below grade" rule would reject good lessons. Three grades of slack still
          catches text written far above the reader, the failure that matters.
        * **Lower bound** applies only from grade 9 up (``grade - 6``; college: 8), so
          an advanced lesson can't be watered-down kid text. There's no lower bound below
          grade 9, because simpler text never hurts a young reader.

    Args:
        grade: One of :data:`GRADES`.

    Returns:
        ``(low, high)`` as floats. ``low`` is ``-inf`` when there's no lower bound.
    """
    n = grade_number(grade)
    high = 18.0 if grade == "C" else n + 3.0
    low = 8.0 if grade == "C" else (n - 6.0 if n >= 9 else float("-inf"))
    return low, high


# --------------------------------------------------------------------------- timer and exercises

#: Seconds of reading or quiz time before a movement break is required (user requirement: 1 minute).
TIMER_SECONDS = 60

#: The movement breaks, exactly as the user specified. Keys are stable IDs used in the API
#: and the results summary; values are the instructions shown on screen.
EXERCISES = {
    "jumping_jacks": "10 jumping jacks",
    "high_knees": "10 high knees (running in place)",
    "pushups": "10 push-ups",
}

# --------------------------------------------------------------------------- lesson shape

N_CONCEPTS = 3        # user requirement: three important concepts
N_QUESTIONS = 5       # user requirement: a 5-question quiz
N_OPTIONS = 4         # multiple choice: one correct answer and three distractors
SUBJECT_MIN_CHARS = 2
SUBJECT_MAX_CHARS = 60


# --------------------------------------------------------------------------- agents

@dataclass(frozen=True)
class AgentConfig:
    """Settings shared by every agent run that builds one lesson.

    Attributes:
        model: Claude model ID used by every agent.
        token_budget: Hard cap on total tokens (input + output + cache) for one lesson.
            The runtime checks it before each model call and stops the build if it's
            exceeded, so a looping agent can't run up the bill.
        max_cost_usd: Evals fail any lesson that cost more than this.
        max_revisions: How many times the Reviewer may send work back before the build
            fails. Two, because live evals showed a reviewer can find two distinct real
            problems in a row (a factual error, then a diagram/concept mismatch); a third
            round rarely helps and adds about 20 seconds each.
    """
    model: str = "claude-opus-5-5"
    token_budget: int = 400_000
    max_cost_usd: float = 1.00
    max_revisions: int = 2
