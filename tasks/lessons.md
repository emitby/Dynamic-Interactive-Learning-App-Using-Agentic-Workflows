# Lessons

Carried over from earlier projects (same user, same CLAUDE.md):

- **Document all Python extensively (2026-10-21, user rule).** Docstrings on every module, class and function covering args, returns, raises and side effects. Comments explain rules and decisions, not obvious lines.
- **A perfect eval score doesn't mean a good product.** Always review the rendered output visually. Turn each finding into a guardrail at the source *and* a check, and confirm the check fails on the bad artifact.
- **One passing live run proves little.** Measure pass rates over several runs before calling agent behavior done.
- **A false-positive guardrail strands the agent.** Test every check against the correct phrasings the agent is asked to write, not only the bad ones.
- **Agents need a way to fix rejected work.** Rejections must name the exact problem and the fix.
- **Prefer guarantees by construction over after-the-fact checks.**
- **Under these guardrails, never use `os`.** Use pathlib, `secrets`, `Path.replace` and `confine()`. Keep caches and test scratch files inside the project.
- **Windows `\?\` path prefix** can break confinement checks. Normalize it.

Learned building Study Sprint (2026-10-21):

- **Scripted text replacements must assert their target exists.** A `str.replace` that matched nothing silently left "1 revision max" in the guide, and an independent review caught it. Use a `sub()` helper that asserts, or the Edit tool.
- **Re-derive everything downstream of revised work.** Revising the concepts without redrawing the diagram produced a contradiction the Reviewer rejected. Map dependencies (concepts → diagram, quiz) and redo all dependents.
- **Text shown in more than one context must fit all of them.** A quiz "why" is shown after right *and* wrong answers, so it can't open with praise.
- **Screenshots must prove the requirement visibly.** "Random" exercises need two different exercises on screen; a "progress screen" caption needs the progress screen. Check every caption against its image.
- **Guardrail regexes need a precision rule, not just a word list.** Praise counts only when followed by punctuation, so "Right angles…" passes. Blocked crime terms need the instruction form, so "stealth" passes.
- **Guardrail tests must be portable, or explicitly platform-gated.** CI on Linux failed because `..\evil.txt`, `C:\Windows\win.ini` and the `\?\` prefix are Windows-only spellings (on Linux they're ordinary names inside the project, so allowing them is correct). Build "outside" paths from `PROJECT_DIR.anchor`, and `skipif` Windows-only cases.
- **Mark binaries in `.gitattributes` before the first commit.** Git classified the PDF as text (line-ending conversion would corrupt it), and `run.sh` needs LF. Put `* text=auto` first: later lines override earlier ones.

Learned deploying to Render (2026-10-25):

- **Stop test servers by PID, never by image name or a wildcard filter.** A `taskkill /IM python.exe` cleanup could have killed the user's own running server. Look up the PID listening on the test port first, then stop that PID and its child.
- **Drive a multi-step flow in the order the app enforces.** A 409 from `finish-reading` was the test skipping "break done", not a bug. Read the route's rules before treating an error as a failure.
