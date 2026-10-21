"""The lesson data model and the rules every lesson must satisfy.

A lesson is three concepts, one diagram and a five-question quiz. Agents write the
content, and this module decides whether it's acceptable. The same functions are used:
    * at tool time, so an agent gets an actionable error and rewrites
    * by the harness, before a lesson is published to the web app
    * by the evals, on saved lessons

so a rule can't be enforced in one place and forgotten in another.

Design decisions:
    * **Answer positions are shuffled by code** (:func:`build_question`). The Quiz Agent
      writes the correct answer and three distractors separately, and code places them
      with ``secrets``. "The answer is always B" can't happen, and the agent never has to
      reason about positions.
    * **Answer keys never leave the server** before grading. :meth:`Lesson.public_view`
      is the only form sent to the browser, and it omits ``answer_index``.
"""

import re
import secrets

from pydantic import BaseModel, Field

from studysprint.config import GRADE_LABELS, N_CONCEPTS, N_OPTIONS, N_QUESTIONS, band_for, readability_range
from studysprint.readability import avg_sentence_words, fk_grade, text_stats
from studysprint.safety import content_problems


class Concept(BaseModel):
    """One of the three key concepts on the reading screen."""
    title: str = Field(description="A short heading, 2-8 words")
    explanation: str = Field(description="The idea itself, in grade-appropriate language")
    example: str = Field(description="One concrete example or everyday connection")


class Diagram(BaseModel):
    """The sanitized SVG picture for the lesson, with accessibility text."""
    svg: str
    alt_text: str
    caption: str
    labels: list[str] = []


class Question(BaseModel):
    """One multiple-choice question. ``answer_index`` is server-side only."""
    question: str
    options: list[str]
    answer_index: int
    concept_index: int = Field(description="0-based index of the concept this question tests")
    why: str = Field(description="One sentence explaining the correct answer, shown after grading")


class Lesson(BaseModel):
    """A complete, validated lesson as stored in ``lesson.json`` and served to the app."""
    id: str
    subject: str
    topic: str
    grade: str
    concepts: list[Concept]
    diagram: Diagram
    questions: list[Question]
    meta: dict = {}

    def public_view(self):
        """The lesson as sent to the browser, **without answer keys or explanations**.

        Returns:
            A JSON-ready dict: the concepts, the diagram and the questions as
            ``{"question", "options"}`` only.
        """
        return {
            "id": self.id, "subject": self.subject, "topic": self.topic, "grade": self.grade,
            "grade_label": GRADE_LABELS[self.grade],
            "concepts": [c.model_dump() for c in self.concepts],
            "diagram": {"svg": self.diagram.svg, "alt_text": self.diagram.alt_text, "caption": self.diagram.caption},
            "questions": [{"question": q.question, "options": q.options} for q in self.questions],
        }


# --------------------------------------------------------------------------- concept rules

def concept_problems(concepts, grade):
    """Check three concepts against the structure, length, reading-level and safety rules.

    Rules:
        * exactly :data:`N_CONCEPTS` concepts, with distinct titles of 2-8 words
        * each explanation within the grade band's word range (fits the 1-minute read)
        * Flesch-Kincaid grade of all the lesson text combined within
          :func:`readability_range`, and the average sentence length within the band.
          The text is combined because FK is unstable on a single short paragraph.
        * every string passes :func:`studysprint.safety.content_problems`

    Args:
        concepts: A list of :class:`Concept` (or dicts with the same keys).
        grade: A grade code.

    Returns:
        A list of problems written for the Curriculum Agent. Empty means acceptable.
    """
    concepts = [c if isinstance(c, Concept) else Concept(**c) for c in concepts]
    band = band_for(grade)
    problems = []
    if len(concepts) != N_CONCEPTS:
        return [f"Provide exactly {N_CONCEPTS} concepts (got {len(concepts)})."]
    titles = [c.title.strip().lower() for c in concepts]
    if len(set(titles)) != len(titles):
        problems.append("Each concept needs a different title.")
    for i, c in enumerate(concepts, start=1):
        n_title = text_stats(c.title)["words"]
        if not 2 <= n_title <= 8:
            problems.append(f"Concept {i} title should be 2-8 words (has {n_title}).")
        n = text_stats(c.explanation)["words"]
        if not band.words_min <= n <= band.words_max:
            problems.append(f"Concept {i} explanation has {n} words; for {band.name} use "
                            f"{band.words_min}-{band.words_max}.")
        if not c.example.strip():
            problems.append(f"Concept {i} needs an example.")
        for field in ("title", "explanation", "example"):
            problems += [f"Concept {i} {field}: {p}" for p in content_problems(getattr(c, field))]

    body = " ".join(f"{c.explanation} {c.example}" for c in concepts)
    low, high = readability_range(grade)
    fk = fk_grade(body)
    if fk > high:
        problems.append(f"The reading level is grade {fk}, too hard for {GRADE_LABELS[grade]} "
                        f"(max {high:g}). Use shorter sentences and simpler words.")
    elif fk < low:
        problems.append(f"The reading level is grade {fk}, too simple for {GRADE_LABELS[grade]} "
                        f"(min {low:g}). Use the subject's proper vocabulary and fuller explanations.")
    asw = avg_sentence_words(body)
    if asw > band.max_sentence_words:
        problems.append(f"Sentences average {asw:.1f} words; keep them under {band.max_sentence_words:g} "
                        f"for {band.name}.")
    return problems


# --------------------------------------------------------------------------- quiz rules

# The "why" explanation is shown in the answer review whether the learner got the
# question right or wrong, so it must not open with praise ("Awesome!" after a wrong
# answer reads as mockery or confusion). Celebration belongs to the results headline.
# Praise is matched only when followed by punctuation ("Great job!", "Correct!", "Nice work,"),
# so explanations that merely start with such a word ("Right angles...", "Great Lakes...",
# "Perfect squares...") are allowed.
_PRAISE_OPENER_RE = re.compile(r"^\W*(great|awesome|nice|good|well done|you got it|correct|excellent|amazing|super|"
                               r"yes|way to go|fantastic|perfect|right)(\s+\w+)?\s*[!,.]", re.IGNORECASE)

def build_question(question, correct, distractors, concept_index, why, rng=secrets.SystemRandom()):
    """Assemble a :class:`Question`, placing the correct answer at a random position.

    Args:
        question: The question text.
        correct: The one correct answer.
        distractors: Three plausible wrong answers.
        concept_index: 0-based index of the concept it tests.
        why: A one-sentence explanation of the correct answer.
        rng: Random source for the shuffle. Defaults to the OS-backed ``SystemRandom``;
            tests pass a seeded ``random.Random`` for reproducibility.

    Returns:
        A :class:`Question` with ``options`` shuffled and ``answer_index`` pointing at ``correct``.
    """
    options = [correct, *distractors]
    rng.shuffle(options)
    return Question(question=question, options=options, answer_index=options.index(correct),
                    concept_index=concept_index, why=why)


def quiz_problems(items, grade):
    """Check raw quiz items (as the Quiz Agent submits them) before they're assembled.

    Rules:
        * exactly :data:`N_QUESTIONS` questions, all different
        * each has one correct answer and exactly ``N_OPTIONS - 1`` distractors, all
          options distinct (case-insensitive) and non-empty
        * no "all of the above" / "none of the above" (meaningless once options are shuffled)
        * the "why" explanation doesn't open with praise (it's shown after wrong answers too)
        * each cites a concept index 0-2, and **every concept is tested at least once**
        * question stems are within the grade's reading level (upper bound + 2, because
          questions are short and FK overstates short texts)
        * every string passes the content safety checks

    Args:
        items: A list of dicts with ``question``, ``correct``, ``distractors``,
            ``concept_index`` and ``why``.
        grade: A grade code.

    Returns:
        A list of problems written for the Quiz Agent. Empty means acceptable.
    """
    problems = []
    if len(items) != N_QUESTIONS:
        return [f"Write exactly {N_QUESTIONS} questions (got {len(items)})."]
    stems = [str(it.get("question", "")).strip() for it in items]
    if len({s.lower() for s in stems}) != len(stems):
        problems.append("Every question must be different.")
    covered = set()
    for i, it in enumerate(items, start=1):
        correct = str(it.get("correct", "")).strip()
        distractors = [str(d).strip() for d in it.get("distractors", [])]
        if len(distractors) != N_OPTIONS - 1:
            problems.append(f"Question {i} needs exactly {N_OPTIONS - 1} wrong answers (got {len(distractors)}).")
        options = [correct, *distractors]
        if any(not o for o in options):
            problems.append(f"Question {i} has an empty answer option.")
        if len({o.lower() for o in options}) != len(options):
            problems.append(f"Question {i} has duplicate answer options; each must be different.")
        if any("of the above" in o.lower() for o in options):
            problems.append(f"Question {i}: don't use 'all/none of the above'; answers are shuffled.")
        if _PRAISE_OPENER_RE.match(str(it.get("why", ""))):
            problems.append(f"Question {i} why: start with the explanation, not praise; it's shown even when "
                            "the learner answered wrong.")
        ci = it.get("concept_index")
        if not isinstance(ci, int) or not 0 <= ci < N_CONCEPTS:
            problems.append(f"Question {i} must cite concept_index 0, 1 or 2.")
        else:
            covered.add(ci)
        for field, text in (("question", stems[i - 1]), ("why", str(it.get("why", ""))), *(("answer", o) for o in options)):
            problems += [f"Question {i} {field}: {p}" for p in content_problems(text)]
    missing = sorted(set(range(N_CONCEPTS)) - covered)
    if missing:
        problems.append(f"Every concept must be tested; no question cites concept(s) {missing}.")
    _, high = readability_range(grade)
    fk = fk_grade(" ".join(stems))
    if fk > high + 2:
        problems.append(f"Questions read at grade {fk}, too hard for {GRADE_LABELS[grade]} (max {high + 2:g}).")
    return problems
