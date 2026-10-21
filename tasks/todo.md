# Task 1: Study Sprint, a multi-agent study app with movement breaks

Plan: `C:\Users\emitb\.claude\plans\piped-imagining-popcorn.md` (approved).

User decisions:
- agent-drawn SVG diagrams
- grades K–12 plus College
- location `C:\Users\emitb\study-sprint`
- no GitHub push unless asked

- [x] 1. Scaffold: CLAUDE.md copy, venv, requirements, launchers that keep caches and Playwright browsers inside the project
- [x] 2. Core code: paths, config, safety (topic gate and content checks), readability, SVG sanitizer, exercises, sessions and grading
- [x] 3. Agents: runtime (adapted from spy-ief-strategy), Intake, Curriculum, Diagram, Quiz and Reviewer; harness with parallel stage, revision loop, trace, budget and manifest
- [x] 4. Web app: FastAPI API and static front end (timers, break modal, quiz, results, start over); demo fixtures
- [x] 5. Tests: unit, API and Playwright E2E with clock control; no-`os` scan
- [x] 6. Evals: lesson scorers, red-team, live topic × grade matrix
- [x] 7. PDF guide with screenshots; docs; visual review by a subagent; fixes
- [x] 8. Final guardrail check and review section

## Review (Task 1)

- **Built:** FastAPI app with a vanilla JS front end, five Claude agents (Intake, Curriculum, Diagram, Quiz, Reviewer), a deterministic harness, demo mode, evals and a PDF guide with screenshots (`docs/how-it-works.pdf`).
- **Verified:**
  - 184 tests (unit, API and Chromium end-to-end), including scans for `os` imports, deletion calls and missing docstrings
  - 45/45 red-team cases
  - **live matrix 9/9** (K to College, 100% on all 8 scorers; $0.13–0.27 and 33–84 s per lesson)
- **Defects found:**
  - **Live matrix:** a concepts revision left a stale diagram → redo diagram and quiz; up to 2 revisions.
  - **Visual review:**
    - praise in "why" after wrong answers → a code rule
    - an ambiguous exercise label
    - a wrong guide screenshot
    - a silent no-op text edit in a script
    - the same exercise shown twice
    - a stark example
    - a cut-off quiz screenshot
    - kid-unfriendly × and ✗ wording
    - small lesson accuracy fixes
- **Not pushed to GitHub** (not requested).
