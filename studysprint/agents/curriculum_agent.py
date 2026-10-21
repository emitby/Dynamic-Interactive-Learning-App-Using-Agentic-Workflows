"""Curriculum Agent: writes the three key concepts at the learner's reading level.

The agent writes; code measures. ``submit_concepts`` computes the Flesch-Kincaid grade,
sentence length and word counts itself (:func:`studysprint.lesson.concept_problems`), so
"written for Grade 2" is checked rather than claimed. A rejection quotes the measured
numbers, so the agent knows exactly how far off it was.
"""

from studysprint.agents.common import AUDIENCE_RULES, level_guidance
from studysprint.agents.runtime import Agent, Tool, ToolError, schema
from studysprint.config import N_CONCEPTS
from studysprint.lesson import Concept, concept_problems
from studysprint.readability import fk_grade

NAME = "Curriculum Agent"

PROMPT = f"""You are the Curriculum Agent for Study Sprint. You teach one topic as exactly {N_CONCEPTS} important concepts
that a learner can read in about one minute.

For each concept write:
- title: a short heading (2-8 words)
- explanation: the idea itself, clear and correct, at the learner's level
- example: one concrete example or everyday connection that makes it stick

Choose the {N_CONCEPTS} concepts a good teacher would say matter MOST for this topic at this grade, ordered so each
builds on the last. Write directly to the learner ("you"). Use the subject's real vocabulary, explained simply.

Submit with submit_concepts. If it's rejected, read the measured numbers in the error, rewrite and resubmit.
When accepted, reply with one short sentence.

{AUDIENCE_RULES}"""

_CONCEPT_SCHEMA = {
    "type": "array", "minItems": N_CONCEPTS, "maxItems": N_CONCEPTS,
    "items": schema({"title": {"type": "string"}, "explanation": {"type": "string"}, "example": {"type": "string"}},
                    ["title", "explanation", "example"]),
}


def build(ctx):
    """Create the Curriculum Agent with ``submit_concepts`` bound to ``ctx``.

    Args:
        ctx: The build context.

    Returns:
        A configured :class:`Agent`.
    """
    def submit_concepts(concepts):
        """Validate and store the concepts. Rejections name each failed rule with measured values."""
        try:
            parsed = [Concept(**c) for c in concepts]
        except Exception as exc:  # pydantic ValidationError, or a non-dict item
            raise ToolError(f"Each concept needs title, explanation and example strings ({exc}).") from None
        problems = concept_problems(parsed, ctx.grade)
        if problems:
            raise ToolError("Not accepted yet:\n- " + "\n- ".join(problems))
        ctx.concepts = parsed
        body = " ".join(f"{c.explanation} {c.example}" for c in parsed)
        return f"Accepted. Measured reading level: grade {fk_grade(body)}."

    tools = [Tool("submit_concepts",
                  f"Submit exactly {N_CONCEPTS} concepts. Code checks the structure, word counts, reading level "
                  "(Flesch-Kincaid, computed by code) and safety rules.",
                  schema({"concepts": _CONCEPT_SCHEMA}, ["concepts"]), submit_concepts)]
    return Agent(ctx.client, NAME, PROMPT, tools, ctx.trace, ctx.cfg.model, effort="medium", max_turns=8,
                 token_budget=ctx.cfg.token_budget)


def task(ctx):
    """The instruction: topic, scope and the measured writing-level targets for this grade."""
    return (f"Topic: {ctx.topic}\nScope: {ctx.scope_note}\nLearner: {ctx.grade_label}\n"
            f"{level_guidance(ctx.grade, ctx.grade_label)}")
