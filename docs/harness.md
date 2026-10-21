# The harness

The harness (`studysprint/harness.py`) is deterministic Python that decides **what runs when**, **what counts as a finished hand-off** and **what gets recorded**. No agent controls the flow.

## Pipeline

```
validate_subject → topic_gate → Intake → Curriculum → ┬ Diagram ┬ → Reviewer ─ approve ─→ assemble → lesson.json
                                                      └ Quiz    ┘      │
                                                                       └ request_revision → revise() → Reviewer (up to 2x)
```

| Stage | Agent | Hand-off validator (re-checks with the same rule functions as the tool) |
|---|---|---|
| Gate | none (code) | `topic_gate`: blocked subjects never reach an agent |
| Intake | Intake | `validate_intake`: a topic was set, or `Declined` is raised with the agent's kind reason |
| Curriculum | Curriculum | `validate_curriculum`: `concept_problems()` is empty |
| Diagram ∥ Quiz | Diagram, Quiz | `validate_diagram` (`check_svg().ok`), `validate_quiz` (`quiz_problems()` is empty) |
| Review | Reviewer | Verdict recorded in `ctx.review` |

A tool may be skipped (an agent can reply without calling it), so **validators never assume**. A failed validator raises `StageError` and stops the build.

## Revisions (`revise`)

| Reviewer target | Re-run | Why |
|---|---|---|
| `concepts` | Curriculum, then **Diagram ∥ Quiz** | Both are derived from the concepts. The live eval for "ancient Egypt" showed revised concepts contradicting the old diagram. |
| `diagram` | Diagram | |
| `quiz` | Quiz | |

The issues are appended to the agent's task ("A reviewer found these problems…"). After each revision the Reviewer checks again, up to `AgentConfig.max_revisions = 2`. If it still rejects the lesson, the build **fails rather than publishing** a lesson a reviewer found wrong, and the learner sees a friendly "please try again".

## Runtime (`agents/runtime.py`)

- **Loop:**
  1. Call Claude with the prompt, tools and history.
  2. Run all requested tools concurrently.
  3. Append every `tool_result` in **one** user message.
  4. Repeat until a text reply.
- **Errors:**
  - `ToolError`: the agent gets an `is_error` result naming the fix. This is how most self-correction happens.
  - Bad or missing arguments: caught before the handler runs (`inspect.signature().bind`).
  - Unexpected exceptions: a generic message, plus a `tool_crash` trace event.
- **Refusals:** `stop_reason == "refusal"` returns a safe reply. Server-side fallbacks are enabled (`fallbacks="default"`).
- **Prompt caching:** `cache_control: ephemeral` on every call.

| Limit | Value |
|---|---|
| Turns per agent | Intake 4, Curriculum 8, Diagram 8, Quiz 8, Reviewer 4 |
| Token budget per lesson | 400,000 (checked before every model call → `BudgetExceeded`) |
| Output per call | 16,000 tokens |
| Concurrent builds (web app) | 3 (HTTP 429 beyond that) |
| Cost cap (evals) | $1.00 per lesson |

## Trace events (`trace.jsonl`)

| `type` | Fields |
|---|---|
| `start` / `finish` | agent, task / reply |
| `model` | agent, turn, stop_reason, latency_s, usage {input, output, cache_write, cache_read} |
| `tool` | agent, tool, input, ok, latency_s, error, result_chars |
| `tool_crash`, `refusal` | agent, tool, error |
| `stage_start` / `stage_ok` | stage, seconds |
| `build_failed` | error |

## Cost

Usage is priced at claude-opus-5-5 rates:

| Token type | Price per million |
|---|---|
| Input | $4 |
| Output | $20 |
| Cache write | $5 |
| Cache read | $0.20 |

`manifest.json` records the total and the cost per agent.

A typical lesson costs about **$0.20** and takes about **a minute**. The Diagram Agent is the largest share, because the SVG is output tokens.

## Progress reporting

`ctx.progress(stage)` is called at each stage ("Choosing the best topic for your grade", "Writing three key concepts", …). The API stores it on the job, and the browser's progress screen lists each stage as it's reached.
