# The PDF guide (`docs/how-it-works.pdf`)

A 12-page illustrated guide to the app, for people who want to see how it works without running it.

| Pages | Contents |
|---|---|
| 1 | Cover: what the app does, plus the reading screen of a real agent-built lesson |
| 2 | What it does: lessons, movement breaks, child safety, and the session step by step |
| 3–10 | **Walkthrough with screenshots:** start → progress → reading → break → timer restarted → quiz → quiz break → results → Start Over → a kind decline → phone layout |
| 11 | **How the agents build a lesson:** the [agent workflow diagram](images/agent-workflow.svg), what each agent does, and what code checks before accepting its work |
| 12 | Guardrails table, evals summary, how to run it |

## How it's generated

`.\run.ps1 guide` (`studysprint/guide.py`) does this in three steps:

1. **Screenshots** (`capture_screenshots`):
   - Starts the app in demo mode on a free `127.0.0.1` port.
   - Drives Chromium through a full session with Playwright, using `page.clock` to expire the 1-minute timers on cue.
   - Keeps taking breaks until a *different* exercise appears, so the randomness is visible.
   - Answers one question wrong on purpose, so the results show 4 / 5 and the gentle review.
   - Writes `docs/images/screens/*.png`.
2. **Workflow diagram** (`render_workflow_png`): Chromium rasterizes `docs/images/agent-workflow.svg` (the source of truth, which GitHub shows directly) to PNG for embedding.
3. **Layout** (`build_pdf`): ReportLab builds the pages, with a footer and page number on each.

Everything is overwritten in place; nothing is deleted (CLAUDE.md). The guide has been reviewed twice: once by me, and once by an independent subagent. Its findings and fixes are listed in [evals.md](evals.md#what-the-evals-found-and-the-fix-for-each).

See the [screenshot gallery](screenshots.md) for every image at full size.
