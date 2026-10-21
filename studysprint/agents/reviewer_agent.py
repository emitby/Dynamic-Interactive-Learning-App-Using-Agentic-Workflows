"""Reviewer Agent: an independent check of accuracy and grade fit before a lesson is published.

Code can verify structure, reading level and safety, but not whether a fact is true or
whether a quiz's "correct" answer is actually correct. That judgment needs a second
reader. The Reviewer sees the whole lesson **with the answer key** (which the learner
never does) and either approves it or requests a revision of one specific part, with
concrete issues. The harness decides what happens next (:func:`studysprint.harness.revise`),
not the Reviewer.
"""

from studysprint.agents.runtime import Agent, Tool, ToolError, schema

NAME = "Reviewer Agent"

PROMPT = """You are the Reviewer Agent for Study Sprint, a careful teacher checking a lesson before a learner sees it.

Check:
1. Accuracy: every fact in the concepts, diagram labels and quiz is correct at this level (simplified is OK,
   false is not).
2. Quiz answer key: each marked correct answer (✓) is truly correct and each distractor (✗) is truly wrong;
   each question is answerable from the concepts alone; no question is ambiguous.
3. Grade fit: the ideas and vocabulary suit the grade; nothing is frightening or inappropriate.
4. Coherence: the diagram labels match the concepts.

Structure, length, reading level and safety words were already checked by code, so don't nitpick style.
Approve unless there is a real problem a teacher would fix. If there is, call request_revision with the ONE
part to fix (concepts, quiz or diagram) and specific, actionable issues. Then reply with one short sentence."""


def build(ctx):
    """Create the Reviewer Agent with ``approve`` and ``request_revision`` bound to ``ctx``.

    Args:
        ctx: The build context.

    Returns:
        A configured :class:`Agent`.
    """
    def approve(notes=""):
        """Record approval."""
        ctx.review = {"approved": True, "target": None, "issues": [], "notes": notes.strip()}
        return "Approved."

    def request_revision(target, issues):
        """Record a revision request for one part of the lesson."""
        issues = [i.strip() for i in issues if i and i.strip()]
        if not issues:
            raise ToolError("List at least one specific issue.")
        ctx.review = {"approved": False, "target": target, "issues": issues}
        return f"Revision requested for {target}."

    tools = [
        Tool("approve", "Approve the lesson for publishing.",
             schema({"notes": {"type": "string"}}), approve),
        Tool("request_revision", "Send ONE part of the lesson back with specific issues to fix.",
             schema({"target": {"type": "string", "enum": ["concepts", "quiz", "diagram"]},
                     "issues": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
                    ["target", "issues"]), request_revision),
    ]
    return Agent(ctx.client, NAME, PROMPT, tools, ctx.trace, ctx.cfg.model, effort="medium", max_turns=4,
                 token_budget=ctx.cfg.token_budget)


def task(ctx):
    """The instruction: the full lesson, including the answer key."""
    from studysprint.harness import describe_lesson_for_agents
    return describe_lesson_for_agents(ctx, with_answers=True) + "\n\nReview this lesson."
