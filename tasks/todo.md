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
  - 185 tests (unit, API and Chromium end-to-end), including scans for `os` imports, deletion calls and missing docstrings
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

## Task 2: Render deployment (2026-10-25)

- [x] 1. `render.yaml` Blueprint: free Python web service, demo mode, `0.0.0.0:$PORT`, Python 3.12.7, health check `/api/config`
- [x] 2. Dry run from a clean clone in `.cache/render-dryrun`: build command, YAML parsed, `startCommand` run through bash with `PORT=10000`
- [x] 3. Push to `main`

## Review (Task 2)

- **Verified:** the build command installs cleanly in a fresh venv; the parsed `startCommand` serves `/`, `/static/app.js` and `/api/config` (`"demo": true`); a full session over HTTP (lesson → break → finish reading → submit) returned 200 at every step.
- **Design:** demo mode only, so a public URL can't spend API credits. The CLI still binds to `127.0.0.1` for local use; only the Render start command binds to `0.0.0.0`.
- **Not verified:** a real deploy on Render (needs the user's Render account).

## Task 3: Password-protected live AI service on Render (2026-10-26)

Decisions (user): keep the public demo and add a live service; the shell passes the password in (no environment reads in Python); cap AI lessons at 30 per UTC day.

- [x] 1. `cli.py`: `serve --public` (bind 0.0.0.0) and `--password`; refuse a public live server without a 12+ character password; public live mode applies `DAILY_LESSON_LIMIT` (config.py)
- [x] 2. `api.py`: `create_app(password=, daily_limit=)`; HTTP Basic Auth middleware (`secrets.compare_digest`, `/api/config` open for the health check); per-UTC-day build cap → 429
- [x] 3. `render.yaml`: `study-sprint` (demo) and `study-sprint-live` (password, `ANTHROPIC_API_KEY` and `STUDYSPRINT_PASSWORD` with `sync: false`), both via `python -m studysprint serve`
- [x] 4. Tests: auth 401/200, open health check, daily cap and day rollover; CLI refusal without or with a short password
- [x] 5. Docs: api/cli docstrings, README deploy section + safety bullet + test badge, guardrails.md H9, run.ps1 comment
- [x] 6. Verify: full test suite; clean-clone dry run of both start commands (stop servers by PID); commit and push

## Review (Task 3)

- **Built:** `serve --public` and `--password` (cli.py), HTTP Basic Auth and a daily build cap (api.py), and two services in render.yaml.
- **Verified:**
  - 197 tests pass (12 new), including the browser end-to-end tests.
  - Dry run from a clean clone: both `startCommand`s parsed from the YAML and run through bash.
    - Demo: a full session over HTTP.
    - Live: `/api/config` 200 without a password; `/` 401 with `WWW-Authenticate` without a password or with the wrong one; 200 with the right one.
    - A missing or short `STUDYSPRINT_PASSWORD` exits 2 before serving, so the deploy fails.
- **Found:** the startup line was invisible in piped logs (buffered stdout) → `flush=True`.
- **Known, not changed:** with no API key, a live build fails at the first model call with the friendly message (the SDK resolves the key lazily), so the 503 "no API key" branch in `start_lesson` never fires.
- **Not verified:** a real AI build on Render (needs the user's key there). The generated `docs/how-it-works.pdf` still says 185 tests until `run.ps1 guide` is rerun.

## Task 4: "About this project" box on the start screen (2026-10-26)

- [x] 1. `index.html`: an `<aside id="about">` at the top of the start screen (hidden once a lesson starts); `style.css`: `.about`
- [x] 2. Verify: 197 tests pass; screenshots at 1280 px and 390 px, no horizontal scroll; the box hides on the reading screen

## Review (Task 4)

- **Left out:** "hooks". The project uses no Claude Code hooks, so the box doesn't claim them.
- **Not updated:** the screenshots in `docs/` and `docs/how-it-works.pdf` show the old start screen until `run.ps1 guide` is rerun.
