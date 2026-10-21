"""Quiz Agent: writes the five multiple-choice questions on the lesson's concepts.

The agent writes each question's correct answer and three distractors **separately**.
Code shuffles them into positions when the lesson is assembled
(:func:`studysprint.lesson.build_question`), so answer-position bias is impossible by
construction. ``submit_quiz`` enforces structure, distinct options, coverage of all three
concepts, reading level and safety (:func:`studysprint.lesson.quiz_problems`).
"""

from studysprint.agents.common import AUDIENCE_RULES, level_guidance
from studysprint.agents.runtime import Agent, Tool, ToolError, schema
from studysprint.config import N_CONCEPTS, N_OPTIONS, N_QUESTIONS
from studysprint.lesson import quiz_problems

NAME = "Quiz Agent"

PROMPT = f"""You are the Quiz Agent for Study Sprint. You write a quick {N_QUESTIONS}-question multiple-choice quiz about the
lesson the learner just read.

Rules:
- Every question must be answerable from the lesson's concepts alone (the learner can't look anything up).
- Test understanding, not trivia: prefer "which example shows...", "what happens when..." over copying a sentence.
- Each question has ONE clearly correct answer and {N_OPTIONS - 1} plausible but clearly wrong distractors.
  No trick questions, no "all/none of the above" (code shuffles the options).
- concept_index says which concept (0-{N_CONCEPTS - 1}) the question tests; every concept must be tested at least once.
- "why" is one friendly sentence explaining the correct answer. It's shown after the quiz whether the learner
  was right or wrong, so start with the explanation itself, never with praise like "Great job!" or "Correct!".
- Match the learner's reading level. Keep questions and answers short.

Submit with submit_quiz. If rejected, fix exactly what the error says. When accepted, reply with one short sentence.

{AUDIENCE_RULES}"""

_ITEM = schema({
    "question": {"type": "string"},
    "correct": {"type": "string"},
    "distractors": {"type": "array", "items": {"type": "string"}, "minItems": N_OPTIONS - 1, "maxItems": N_OPTIONS - 1},
    "concept_index": {"type": "integer", "minimum": 0, "maximum": N_CONCEPTS - 1},
    "why": {"type": "string"},
}, ["question", "correct", "distractors", "concept_index", "why"])


def build(ctx):
    """Create the Quiz Agent with ``submit_quiz`` bound to ``ctx``.

    Args:
        ctx: The build context.

    Returns:
        A configured :class:`Agent`.
    """
    def submit_quiz(questions):
        """Validate and store the raw quiz items (shuffling happens at assembly)."""
        if not isinstance(questions, list) or not all(isinstance(q, dict) for q in questions):
            raise ToolError("questions must be a list of objects.")
        problems = quiz_problems(questions, ctx.grade)
        if problems:
            raise ToolError("Not accepted yet:\n- " + "\n- ".join(problems))
        ctx.quiz_items = [dict(q) for q in questions]
        return "Accepted."

    tools = [Tool("submit_quiz",
                  f"Submit exactly {N_QUESTIONS} questions, each with the correct answer, {N_OPTIONS - 1} distractors, the "
                  "concept it tests and a one-sentence why. Code checks the structure, coverage, reading level and safety.",
                  schema({"questions": {"type": "array", "items": _ITEM, "minItems": N_QUESTIONS,
                                        "maxItems": N_QUESTIONS}}, ["questions"]), submit_quiz)]
    return Agent(ctx.client, NAME, PROMPT, tools, ctx.trace, ctx.cfg.model, effort="medium", max_turns=8,
                 token_budget=ctx.cfg.token_budget)


def task(ctx):
    """The instruction: the concepts to test and the reading level."""
    from studysprint.harness import describe_lesson_for_agents
    return (describe_lesson_for_agents(ctx) + "\n\n" + level_guidance(ctx.grade, ctx.grade_label)
            + f"\nWrite the {N_QUESTIONS}-question quiz.")
