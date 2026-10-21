"""Diagram Agent: draws one labeled SVG diagram that illustrates the three concepts.

SVG is code that a browser renders, so this agent's output is the most security-sensitive
in the app. ``submit_diagram`` runs it through
:func:`studysprint.svg_sanitize.check_svg` (allowlisted elements and attributes, no
scripts, links or external references) and stores only the canonical re-serialized
markup. The front end also displays it as an ``<img>``, where browsers never run scripts.
"""

from studysprint.agents.common import AUDIENCE_RULES
from studysprint.agents.runtime import Agent, Tool, ToolError, schema
from studysprint.lesson import Diagram
from studysprint.safety import content_problems
from studysprint.svg_sanitize import MAX_BYTES, MIN_LABELS, check_svg

NAME = "Diagram Agent"

PROMPT = f"""You are the Diagram Agent for Study Sprint. You draw ONE clear, labeled diagram as SVG that helps a learner
understand the lesson's concepts (for example a labeled cycle, the parts of something, a timeline, a number line,
a simple chart or a before/after).

SVG rules (code rejects anything else):
- Root <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 W H">, about 640 x 400.
- Only these elements: svg, g, rect, circle, ellipse, line, polyline, polygon, path, text, tspan, title, desc,
  defs, marker, linearGradient, radialGradient, stop.
- Presentation attributes only (fill, stroke, font-size, text-anchor, transform, ...). NO style, class, href,
  event handlers, images, scripts or external references. url(#id) may only point inside the SVG
  (e.g. an arrowhead marker).
- At least {MIN_LABELS} <text> labels naming the important parts, at least 14px, high contrast on a light background,
  with font-family="Arial, Helvetica, sans-serif" (set it on the root <svg> or each <text>).
- Under {MAX_BYTES // 1000} KB. Keep it simple and uncluttered. Labels must not overlap.
- Use a friendly palette; never rely on color alone to carry meaning (label everything).

Also write alt_text (one or two sentences describing what the diagram shows, for screen readers, max 300
characters) and a caption (one short sentence, max 120 characters).
Submit with submit_diagram. If rejected, fix exactly what the error says. When accepted, reply with one short sentence.

{AUDIENCE_RULES}"""


def build(ctx):
    """Create the Diagram Agent with ``submit_diagram`` bound to ``ctx``.

    Args:
        ctx: The build context.

    Returns:
        A configured :class:`Agent`.
    """
    def submit_diagram(svg, alt_text, caption):
        """Sanitize the SVG and check the text fields, then store a :class:`Diagram`."""
        result = check_svg(svg)
        problems = list(result.problems)
        if not 10 <= len(alt_text.strip()) <= 300:
            problems.append("alt_text must be 10-300 characters describing the diagram.")
        if not 3 <= len(caption.strip()) <= 120:
            problems.append("caption must be 3-120 characters.")
        problems += [f"alt_text: {p}" for p in content_problems(alt_text)]
        problems += [f"caption: {p}" for p in content_problems(caption)]
        if problems:
            raise ToolError("Not accepted yet:\n- " + "\n- ".join(problems))
        ctx.diagram = Diagram(svg=result.svg, alt_text=alt_text.strip(), caption=caption.strip(),
                              labels=result.labels)
        return f"Accepted. Labels found: {', '.join(result.labels)}."

    tools = [Tool("submit_diagram",
                  "Submit the SVG diagram, its alt text and a caption. Code sanitizes the SVG against an "
                  "element/attribute allowlist and checks labels and text safety.",
                  schema({"svg": {"type": "string"}, "alt_text": {"type": "string"}, "caption": {"type": "string"}},
                         ["svg", "alt_text", "caption"]), submit_diagram)]
    return Agent(ctx.client, NAME, PROMPT, tools, ctx.trace, ctx.cfg.model, effort="medium", max_turns=8,
                 token_budget=ctx.cfg.token_budget)


def task(ctx):
    """The instruction: the lesson's concepts, which the diagram must illustrate."""
    from studysprint.harness import describe_lesson_for_agents
    return describe_lesson_for_agents(ctx) + "\n\nDraw one diagram that illustrates these concepts for this learner."
