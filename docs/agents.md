# The agents

All five run on `claude-opus-5-5` through the same runtime (`studysprint/agents/runtime.py`). Each has **one job and the minimum tools for it**: no file, shell or network access. Every tool validates its input with code and returns an actionable error on rejection, so the agent can fix its work. All prompts share the audience and safety rules in `agents/common.py`.

| Agent | Effort | Max turns | Tools | Writes to the build context |
|---|---|---|---|---|
| Intake | low | 4 | `set_topic(topic, scope_note)`, `decline(reason)` | `topic`, `scope_note` or `declined` |
| Curriculum | medium | 8 | `submit_concepts(concepts[3])` | `concepts` |
| Diagram | medium | 8 | `submit_diagram(svg, alt_text, caption)` | `diagram` |
| Quiz | medium | 8 | `submit_quiz(questions[5])` | `quiz_items` |
| Reviewer | medium | 4 | `approve(notes)`, `request_revision(target, issues[])` | `review` |

## Intake & Safety Agent
- **Job:** decide whether the subject is a real, school-appropriate subject for the grade. If so, turn it into a specific topic: "science" for Grade 7 becomes a concrete topic, and "space" for Grade 2 becomes "The Solar System". Otherwise, decline kindly and suggest something to try.
- **Code enforces:**
  - The subject arrives as JSON-quoted data (`<subject>"..."</subject>`) and is never treated as instructions.
  - `set_topic` re-runs the topic gate on the agent's own wording, so a blocked subject can't be laundered through a rewording.
  - Content checks apply to both the topic and the scope note.

## Curriculum Agent
- **Job:** the three concepts a good teacher would say matter most, each with an example, written to the learner.
- **Code enforces (`lesson.concept_problems`):**
  - exactly 3 concepts, with distinct titles of 2–8 words
  - word counts within the grade band (K–2: 12–60 words, …, College: 40–160)
  - the Flesch–Kincaid grade of all the text within `readability_range(grade)`
  - average sentence length within the band
  - safety checks on every field
- **Errors quote measured numbers,** for example "reading level is grade 7.4, too hard for Grade 3 (max 6)".

## Diagram Agent
- **Job:** one labeled SVG diagram (cycle, parts, timeline, number line, …) with alt text and a caption.
- **Code enforces (`svg_sanitize.check_svg`):**
  - an element and attribute allowlist
  - no scripts, event handlers, `style`, `href` or external `url()`
  - a viewBox, at most 40 KB and 400 elements
  - at least 3 safe text labels
  - length and safety checks on the alt text and caption
- **The stored SVG is the canonical re-serialization,** not the agent's raw text.

## Quiz Agent
- **Job:** 5 multiple-choice questions on the concepts. Each has the correct answer, 3 distractors, the concept it tests and a one-sentence "why".
- **Code enforces (`lesson.quiz_problems`):**
  - 5 distinct questions
  - 3 distinct distractors each
  - no "all/none of the above"
  - every concept tested
  - question reading level at most the grade maximum plus 2
  - a "why" that doesn't open with praise, because it's shown after wrong answers too
  - safety checks
- **Code shuffles answer positions** when the lesson is assembled.

## Reviewer Agent
- **Job:** an independent accuracy check. The Reviewer sees the whole lesson **including the answer key**. It checks the facts, that each marked answer is truly correct and each distractor truly wrong, grade fit, and that the diagram matches the concepts.
- **Outcome:** it approves, or sends **one** part back with specific issues. The harness decides what happens next (see [harness.md](harness.md)).
- **Why it matters:** code can't tell whether a fact is true. In the live evals, the Reviewer caught a false claim (hieroglyphs inside the Great Pyramid) and a diagram that contradicted the concepts.
