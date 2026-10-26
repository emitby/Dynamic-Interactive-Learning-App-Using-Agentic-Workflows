# Guardrails: what each one protects

Every guardrail is enforced **in code**. Prompts describe the rules so agents comply on the first try, but nothing depends on an agent obeying. Most rules are checked in up to four places:
1. **At tool time:** the agent gets an actionable error and fixes its work.
2. **At the harness hand-off.**
3. **In the evals,** on saved lessons.
4. **In the red-team,** where the guardrail itself is attacked.

| # | Harm | Guardrail | Where | Tests |
|---|---|---|---|---|
| H1 | **Unsafe or adult subjects** for a child | Hard blocklist (adult content, weapon/explosive making, self-harm, crime how-tos, graphic violence, hate). **Age gates** (drugs/alcohol and puberty from Grade 5, the Holocaust from 6, suicide prevention from 9). Then the Intake Agent's judgment. `set_topic` re-runs the gate on the agent's wording. | `safety.topic_gate`, API, harness, Intake tool | `test_safety.py`, `test_harness.py` (laundering), red-team "subject" cases |
| H2 | **Prompt injection** through the subject box | 2–60 characters. A character allowlist (no `< > { }` or backticks). Injection phrasing rejected ("ignore previous instructions", "you are now", "system prompt", …). The subject is passed to agents as quoted JSON data. | `safety.validate_subject`, `quote_subject` | `test_safety.py`, `test_api.py`, red-team |
| H3 | **Unsafe lesson text** | Every agent string is checked for links and domains, emails, phone numbers, **requests for personal information**, profanity and put-downs ("stupid", "dumb"), HTML, and blocked topics | `safety.content_problems`, used in every tool and the `content_safety` scorer | `test_safety.py` (both directions), red-team "output" cases |
| H4 | **Script execution through the diagram** | SVG element and attribute allowlist. DTDs rejected (entity attacks). Only internal `url(#id)`. Re-serialized canonical output. Rendered as `<img src="data:image/svg+xml">`, where browsers never run scripts. A CSP with no inline script. | `svg_sanitize.check_svg`, `app.js`, `api.CSP` | `test_svg_sanitize.py` (16 attack and invalid-input variants), `svg_safety` scorer, red-team "svg" |
| H5 | **Text too hard (or too easy) for the learner** | Flesch–Kincaid grade **computed by code**, within a per-grade range. Word and sentence-length limits per band. | `readability.py`, `lesson.concept_problems` / `quiz_problems` | `test_readability.py`, `test_lesson_rules.py`, `grade_fit` scorer |
| H6 | **Wrong facts or a wrong answer key** | An independent Reviewer Agent sees the answer key, with up to 2 revision rounds; a final rejection fails the build. Answer positions are shuffled by code. Quiz grounding and diagram relevance scorers. | `reviewer_agent`, `harness.revise`, `lesson.build_question` | `test_harness.py`, `quiz_grounding` / `reviewed` scorers |
| H7 | **Discouraging feedback** | Results headlines are code-chosen and positive at every score. A quiz "why" can't open with praise (it's shown after wrong answers). Put-downs are blocked. | `sessions.results_message`, `lesson.quiz_problems` | `test_sessions.py`, `test_lesson_rules.py` |
| H8 | **Cheating or skipping breaks** | Answer keys never reach the browser before grading (`public_view`). Grading is server-side. Phases only move forward. A pending break blocks `finish-reading` and `submit`. Answers are validated. | `lesson.public_view`, `sessions.SessionStore` | `test_sessions.py`, `test_api.py` (no answer leak) |
| H9 | **Privacy** | No accounts or names. Sessions live in memory only, with 128-bit random IDs and a 6-hour expiry. No learner data is written to disk. Locally the server listens on `127.0.0.1` only. A public live server (`serve --public`, for Render) refuses to start without a password of 12+ characters, checked with HTTP Basic Auth on every route except the `/api/config` health check. | `sessions.py`, `cli.HOST`, `cli.check_serve_args`, `api.require_password` | `test_project_guardrails.py` (loopback default, refusal), `test_api.py` (401s) |
| H10 | **Runaway cost or loops** | Turn limits per agent. A 400K-token budget per lesson. At most 3 concurrent builds (429). A public live server builds at most 30 lessons per UTC day (429). Blocked subjects cost nothing (gated before agents). A $1 cost cap in evals. | runtime, api, harness | `test_harness.py` (turn limit, budget), `test_api.py` (429, daily cap) |
| H11 | **Exercise safety** | Only the three exercises the user specified, chosen by code. The break modal says "Move safely, with space around you. Skip anything that hurts." | `exercises.py`, `index.html` | `test_sessions.py`, e2e |
| H12 | **Harm to the host computer** (CLAUDE.md) | No `import os` anywhere. `confine()` on every path, including Windows `\\?\` normalization. No deletion calls. Atomic writes. Fresh run directories. Caches and Playwright browsers in `.cache/` via the launchers. Agents have no file, shell or network tools. | `paths.py`, `run.ps1` / `run.sh` | `test_project_guardrails.py` (scans every file for `os` and deletion calls) |

## Known limits

- **Pattern lists catch known phrasings.** A novel unsafe subject or phrasing can pass the code gate. The Intake Agent and Reviewer are the second line, and the red-team grows as cases are found.
- **Readability formulas are approximate.** Subject words ("photosynthesis") raise the score of simple prose, so the limits have deliberate slack (upper bound = grade + 3).
- **Code can't verify facts.** Accuracy rests on the Reviewer Agent, which the live evals show catching real errors. A reviewer can still miss one.
- **Text-label overlap in diagrams isn't checked by code.** It's checked by visual review.
- **The timer runs in the browser.** A determined learner could skip breaks with developer tools. The server records which breaks were completed, which is what the results show.

## Design lessons (from building this)

| Lesson | Where it came from |
|---|---|
| Test every guardrail in **both** directions | "stealth aircraft", "bomb calorimetry" and "Hellenistic" are benign look-alikes that a naive pattern would block |
| Match praise only with punctuation | "Right angles…", "Great Lakes…" and "Perfect squares…" are explanations, not praise |
| Re-derive everything that depends on revised work | Revised concepts contradicted the old diagram (live eval). The concepts revision now redoes the diagram and the quiz. |
| Prefer guarantees by construction | Code shuffles answers, so the agents never place them |
| Three independent barriers for anything executable | Sanitizer, canonical re-serialization and `<img>` rendering, plus CSP |
