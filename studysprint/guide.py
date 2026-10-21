"""Build ``docs/how-it-works.pdf``: a guide to the app, with real screenshots.

Three steps, all reproducible with ``run.ps1 guide``:

1. :func:`capture_screenshots` starts the app in **demo mode** on a free loopback port and
   drives a real Chromium through one complete session with Playwright. Time is
   fast-forwarded with Playwright's clock, so the 1-minute timers expire on cue. The demo
   lesson ("How Volcanoes Form and Erupt", Grade 4) is real output from a live agent build.
2. :func:`render_workflow_png` rasterizes the agent-workflow diagram
   (``docs/images/agent-workflow.svg``) for the PDF.
3. :func:`build_pdf` lays out the guide with ReportLab: what the app does, a step-by-step
   walkthrough with the screenshots, the agent workflow, guardrails, evals and how to run it.

Screenshots go to ``docs/images/screens/`` and are overwritten on each run, never deleted.
"""

import socket
import threading
import time

from studysprint.paths import PROJECT_DIR, confine

SCREENS_DIR = PROJECT_DIR / "docs" / "images" / "screens"
PDF_PATH = PROJECT_DIR / "docs" / "how-it-works.pdf"
WORKFLOW_SVG = PROJECT_DIR / "docs" / "images" / "agent-workflow.svg"
WORKFLOW_PNG = PROJECT_DIR / "docs" / "images" / "agent-workflow.png"


# --------------------------------------------------------------------------- screenshots

def _free_port():
    """A free loopback port chosen by the OS."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_demo_server():
    """Run the demo app in a background thread.

    Returns:
        ``(base_url, server)``. Set ``server.should_exit = True`` to stop it.
    """
    import uvicorn

    from studysprint.api import create_app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(demo=True, demo_stage_seconds=1.0), host="127.0.0.1",
                                           port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server


def capture_screenshots(out_dir=SCREENS_DIR):
    """Drive one full study session in Chromium and save a screenshot of each step.

    Args:
        out_dir: Folder for the PNGs (confined to the project).

    Returns:
        ``{step_name: Path}`` for every screenshot taken.

    Side effects:
        Starts and stops a local demo server; writes (overwrites) PNG files.
    """
    from playwright.sync_api import sync_playwright

    out = confine(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shots = {}
    base, server = _start_demo_server()

    def snap(page, name, full=False):
        """Save a screenshot of ``page`` as ``<name>.png`` and remember its path."""
        path = out / f"{name}.png"
        page.screenshot(path=str(path), full_page=full)
        shots[name] = path

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 860}, device_scale_factor=1.5)
            page.clock.install()
            page.goto(base)

            # 1. Start screen, filled in.
            page.fill("#subject", "volcanoes")
            page.select_option("#grade", "4")
            snap(page, "01-start")

            # 2. Building: the agents' progress. Demo stages advance in real time on the
            # server (1 s each), but the page polls on the fake clock. Trigger one poll
            # just after each stage boundary so every stage is listed, then capture while
            # the last stage is still running.
            page.click("#start-button")
            page.wait_for_selector("#screen-building:not([hidden])")
            for _ in range(3):
                time.sleep(1.05)
                page.clock.run_for(800)
            page.wait_for_function("document.querySelectorAll('#build-steps li').length >= 3", polling=100)
            snap(page, "02-building")
            for _ in range(30):
                page.clock.run_for(800)
                if page.is_visible("#screen-reading"):
                    break
                time.sleep(0.3)
            page.wait_for_selector("#screen-reading:not([hidden])")

            # 3. Reading: three concepts and the diagram (full page), timer at 0:42.
            page.clock.run_for(18_000)
            snap(page, "03-reading", full=True)

            # 4. A minute passes: the movement break. Then the timer is back at 1:00.
            page.clock.run_for(43_000)
            page.wait_for_selector("#break-modal:not([hidden])")
            first = page.text_content("#break-exercise")
            snap(page, "04-break-reading")
            page.click("#break-done")
            page.wait_for_selector("#break-modal", state="hidden")
            snap(page, "04b-timer-restarted")

            # 5. "Finished Reading" opens the quiz (full page: all five questions and the button).
            page.check("#finished-reading")
            page.wait_for_selector("#screen-quiz:not([hidden])")
            page.check('input[name="q0"][value="0"]')
            page.check('input[name="q1"][value="1"]')
            page.clock.run_for(25_000)
            snap(page, "05-quiz", full=True)

            # 6. A minute passes during the quiz: another break. The exercise is random, so
            # to *show* that, the guide keeps taking breaks (a real learner would too, if
            # slow) until one differs from the first. Usually that's the first or second.
            page.clock.run_for(36_000)
            for _ in range(8):
                page.wait_for_selector("#break-modal:not([hidden])")
                if page.text_content("#break-exercise") != first:
                    break
                page.click("#break-done")
                page.wait_for_selector("#break-modal", state="hidden")
                page.clock.run_for(61_000)
            snap(page, "06-break-quiz")
            page.click("#break-done")
            page.wait_for_selector("#break-modal", state="hidden")

            # 7. Results: answer the rest with one deliberate mistake (4 / 5).
            for q, a in ((2, 3), (3, 2), (4, 1)):
                page.check(f'input[name="q{q}"][value="{a}"]')
            page.click("#submit-quiz")
            page.wait_for_selector("#screen-results:not([hidden])")
            snap(page, "07-results", full=True)

            # 8. Start Over returns to an empty form.
            page.click("#start-over")
            page.wait_for_selector("#screen-start:not([hidden])")
            snap(page, "07b-start-over")

            # 9. An age-gated subject is declined kindly, before any agent runs.
            page.fill("#subject", "drugs and alcohol")
            page.select_option("#grade", "2")
            page.click("#start-button")
            page.wait_for_selector("#start-error:not([hidden])")
            snap(page, "08-declined")

            # 10. Phone layout.
            phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            phone.clock.install()
            phone.goto(base)
            phone.fill("#subject", "volcanoes")
            phone.select_option("#grade", "4")
            phone.click("#start-button")
            for _ in range(30):
                phone.clock.run_for(800)
                if phone.is_visible("#screen-reading"):
                    break
                time.sleep(0.3)
            phone.wait_for_selector("#screen-reading:not([hidden])")
            snap(phone, "09-phone")
            browser.close()
    finally:
        server.should_exit = True
    return shots


# --------------------------------------------------------------------------- workflow diagram

def render_workflow_png(svg_path=WORKFLOW_SVG, png_path=WORKFLOW_PNG):
    """Render the hand-written agent-workflow SVG to a PNG for the PDF.

    The SVG (``docs/images/agent-workflow.svg``) is the source of truth; GitHub shows it
    directly in the README. ReportLab can't embed SVG, so Chromium rasterizes it at 2x.

    Args:
        svg_path: Source SVG (confined).
        png_path: Output PNG (confined, overwritten).

    Returns:
        The PNG path.
    """
    import urllib.parse

    from playwright.sync_api import sync_playwright
    svg = confine(svg_path).read_text(encoding="utf-8")
    url = "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg)
    out = confine(png_path)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 760}, device_scale_factor=2)
        page.set_content(f'<body style="margin:0"><img src="{url}" width="1200" height="760"></body>')
        page.wait_for_timeout(300)
        page.screenshot(path=str(out))
        browser.close()
    return out


# --------------------------------------------------------------------------- PDF

def build_pdf(shots, path=PDF_PATH):
    """Lay out the guide PDF.

    Args:
        shots: ``{step_name: Path}`` from :func:`capture_screenshots`.
        path: Output PDF path (confined).

    Returns:
        The PDF path.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    from studysprint.config import EXERCISES, GRADE_LABELS, GRADES, TIMER_SECONDS

    out = confine(path)
    styles = getSampleStyleSheet()
    ink = colors.HexColor("#14213d")
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=ink, fontSize=22, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor("#1d5aa6"), fontSize=15,
                        spaceBefore=8, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10.5, leading=15, textColor=ink)
    cap = ParagraphStyle("cap", parent=body, fontSize=9, textColor=colors.HexColor("#4a5568"), alignment=TA_CENTER)
    cover_t = ParagraphStyle("ct", parent=h1, fontSize=34, leading=40, alignment=TA_CENTER, spaceAfter=16)
    cover_s = ParagraphStyle("cs", parent=body, fontSize=14, leading=20, alignment=TA_CENTER)
    width = letter[0] - 1.5 * inch

    def shot(name, caption, max_h=6.2 * inch, w=width):
        """A screenshot scaled to fit, with its caption, kept on one page."""
        from reportlab.lib.utils import ImageReader
        iw, ih = ImageReader(str(shots[name])).getSize()
        scale = min(w / iw, max_h / ih)
        img = Image(str(shots[name]), width=iw * scale, height=ih * scale)
        img.hAlign = "CENTER"
        return KeepTogether([img, Spacer(1, 4), Paragraph(caption, cap), Spacer(1, 10)])

    def table(rows, widths):
        """A styled table: shaded header row, light grid, top-aligned cells."""
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9f2fd")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3e0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
        return t

    def P(text):
        """A table-cell paragraph (wraps text; supports simple inline markup)."""
        return Paragraph(text, ParagraphStyle("cell", parent=body, fontSize=9, leading=12))

    story = [
        Spacer(1, 0.9 * inch), Paragraph("Study Sprint", cover_t),
        Paragraph("How the app works", cover_s), Spacer(1, 18),
        Paragraph("A multi-agent study app: pick a school subject and grade, read three key ideas with a diagram, take a "
                  "5-question quiz, and do a quick movement break whenever the 1-minute timer runs out.", cover_s),
        Spacer(1, 24), shot("03-reading", "The reading screen for a lesson built by the agents (volcanoes, Grade 4; "
                            "explanations lightly edited).", max_h=4.5 * inch, w=5.4 * inch),
        PageBreak(),

        Paragraph("What it does", h1),
        Paragraph(f"Study Sprint turns any school subject into a short lesson at the right level for the learner, "
                  f"from {GRADE_LABELS[GRADES[0]]} to {GRADE_LABELS[GRADES[-1]]}. Each lesson has <b>three important "
                  f"concepts</b>, a <b>labeled diagram</b> and a <b>5-question quiz</b>. Five Claude agents write the "
                  f"lesson; code checks everything they write before a learner sees it.", body), Spacer(1, 6),
        Paragraph(f"<b>Movement breaks.</b> The reading screen and the quiz each run a {TIMER_SECONDS}-second timer. "
                  f"When it reaches zero, the app picks one exercise at random ("
                  f"{', '.join(EXERCISES.values())}). The learner clicks <i>I did it!</i> and the timer starts again. "
                  "The results screen lists every exercise completed.", body), Spacer(1, 6),
        Paragraph("<b>Built to be safe for children.</b> Subjects are checked before any agent runs. Every sentence "
                  "the agents write is checked for links, requests for personal information and unkind words. "
                  "Diagrams are sanitized, and answer keys never reach the browser until the quiz is graded. "
                  "No names or accounts are collected.", body),
        Spacer(1, 10), Paragraph("The session, step by step", h2),
        table([[P("<b>Step</b>"), P("<b>What happens</b>"), P("<b>Who does it</b>")],
               [P("1. Choose"), P("Type a subject, pick a grade, press <i>Build my lesson</i>."), P("Learner")],
               [P("2. Build"), P("Topic gate, then five agents write and check the lesson (about a minute)."),
                P("Code + agents")],
               [P("3. Read"), P(f"Three concepts and a diagram, with a {TIMER_SECONDS}-second timer."), P("Learner")],
               [P("4. Move"), P("Timer at zero: a random exercise, then the timer restarts."), P("Code picks; learner moves")],
               [P("5. Quiz"), P("Select <i>Finished Reading</i>; answer 5 questions (same timer and breaks)."), P("Learner")],
               [P("6. Celebrate"), P("Score, percentage, a big \"Great work!\", the exercises done and an answer "
                                     "review. <i>Start Over</i> asks for a new subject and grade."), P("Code grades")]],
              [0.9 * inch, 4.4 * inch, 1.7 * inch]),
        PageBreak(),

        Paragraph("Walkthrough", h1),
        Paragraph("These screenshots were taken automatically (<font face='Courier'>run.ps1 guide</font>) in "
                  "<b>demo mode</b>, which replays a lesson saved from a live agent build. That's why each screen "
                  "shows a demo banner: the guide can be regenerated at any time without API calls, and the content "
                  "is what the agents actually produced.", body), Spacer(1, 8),
        shot("01-start", "1. The start screen asks what to study and the learner's grade (Kindergarten to College).",
             max_h=3.5 * inch),
        shot("02-building", "2. While the agents work (about a minute live), the progress screen lists each stage.",
             max_h=3.5 * inch),
        PageBreak(),
        shot("03-reading", "3. The lesson: three key concepts, each with an example, and a diagram drawn by the "
                           "Diagram Agent. The timer (top right) counts down from 1:00. Selecting "
                           "<b>Finished Reading</b> opens the quiz.", max_h=8.4 * inch),
        PageBreak(),
        shot("04-break-reading", "4. When the minute is up, the lesson is covered and a random exercise appears.",
             max_h=4.0 * inch),
        shot("04b-timer-restarted", "After <i>I did it!</i>, the lesson returns and the timer starts again at 1:00.",
             max_h=4.0 * inch),
        PageBreak(),
        shot("05-quiz", "5. The quiz: five multiple-choice questions on the concepts, then <i>Check my answers</i>. "
                        "The answer key stays on the server until grading.", max_h=8.4 * inch),
        PageBreak(),
        shot("06-break-quiz", "6. A minute passing during the quiz brings another movement break before continuing. "
                              "Each break's exercise is picked at random, so it often differs from the last one.",
             max_h=5.0 * inch),
        PageBreak(),
        shot("07-results", "7. Results: score, percentage, an enthusiastic message, every exercise completed and a "
                           "gentle answer review.", max_h=8.4 * inch),
        PageBreak(),
        shot("07b-start-over", "8. <i>Start Over</i> returns to an empty form, ready for a new subject and grade.",
             max_h=3.9 * inch),
        shot("08-declined", "Subjects that aren't right for the learner's grade are declined kindly before any agent "
                            "runs (here, an age-gated topic for Grade 2).", max_h=3.9 * inch),
        PageBreak(),
        Paragraph("Works on phones", h2),
        shot("09-phone", "The layout stacks the concepts on narrow screens.", max_h=7.6 * inch, w=3.2 * inch),
        PageBreak(),

        Paragraph("How the agents build a lesson", h1),
        Image(str(render_workflow_png()), width=width, height=width * 760 / 1200), Spacer(1, 8),
        table([[P("<b>Agent</b>"), P("<b>Job</b>"), P("<b>What code checks before accepting its work</b>")],
               [P("Intake"), P("Turns the subject into a specific topic for the grade, or declines it kindly."),
                P("The topic gate runs again on the agent's topic, so a blocked subject can't be reworded through.")],
               [P("Curriculum"), P("Writes the three concepts, each with an example."),
                P("Exactly 3; word counts per grade band; reading level (Flesch-Kincaid, computed by code); "
                  "sentence length; safety checks.")],
               [P("Diagram"), P("Draws one labeled SVG diagram with alt text."),
                P("Element and attribute allowlist (no scripts, links or styles); size limits; at least 3 labels.")],
               [P("Quiz"), P("Writes 5 questions: a correct answer, 3 distractors and an explanation each."),
                P("Distinct options; every concept tested; reading level; safety. Code shuffles answer positions.")],
               [P("Reviewer"), P("Checks facts, the answer key and grade fit."),
                P("May send one part back, up to twice; if it still isn't right, the build fails instead of publishing. Revised concepts also redo the diagram and quiz.")]],
              [0.95 * inch, 2.4 * inch, 3.65 * inch]),
        Spacer(1, 8),
        Paragraph("The <b>harness</b> (plain Python) runs the stages in order, runs Diagram and Quiz in parallel, "
                  "re-checks every hand-off, enforces turn limits and a token budget, and records a trace, cost and "
                  "manifest for every build in <font face='Courier'>runs/</font>. Exercise choice, timers and grading "
                  "are code, never agents.", body),
        PageBreak(),

        Paragraph("Guardrails", h1),
        table([[P("<b>Protects against</b>"), P("<b>How</b>")],
               [P("Unsafe or adult subjects"), P("Hard blocklist and age-gated topics (code), then the Intake Agent's judgment.")],
               [P("Prompt injection"), P("Subject length and character allowlist; injection phrases rejected; the "
                                         "subject is passed to agents as quoted data.")],
               [P("Unsafe lesson text"), P("Every string checked for links, emails, phone numbers, requests for "
                                           "personal information, unkind words and HTML.")],
               [P("Malicious diagrams"), P("SVG allowlist and re-serialization; shown as an image, where browsers "
                                           "never run scripts; strict Content-Security-Policy.")],
               [P("Too hard / too easy"), P("Reading level measured by code against the grade's range.")],
               [P("Wrong answers"), P("Independent Reviewer Agent; grounding eval; answer positions shuffled by code.")],
               [P("Cheating / tampering"), P("Answer keys stay on the server; phases only move forward; a pending "
                                             "break blocks progress.")],
               [P("Runaway cost"), P("Turn limits, a per-lesson token budget, at most 3 builds at once.")],
               [P("The computer it runs on"), P("CLAUDE.md rules: no <font face='Courier'>os</font> module, every "
                                                "path confined to the project, no deletions, caches inside the "
                                                "project, server on 127.0.0.1 only.")]],
              [1.8 * inch, 5.2 * inch]),
        Spacer(1, 10),
        Paragraph("Evals", h2),
        Paragraph("<b>Unit, API and browser tests</b> (offline, free) cover every rule, the session flow and the full "
                  "browser experience with the clock fast-forwarded. <b>Lesson scorers</b> re-check any saved build "
                  "(structure, reading level, safety, SVG, quiz grounding, diagram relevance, review). The "
                  "<b>red-team</b> suite attacks the guardrails and checks that benign look-alikes still pass. The "
                  "<b>live matrix</b> builds lessons from Kindergarten to College with the real agents.", body),
        Spacer(1, 10),
        Paragraph("Run it", h2),
        Paragraph("<font face='Courier'>.\\run.ps1 serve --demo</font> &nbsp; sample lessons, no API key<br/>"
                  "<font face='Courier'>.\\run.ps1 serve</font> &nbsp; live agents (needs ANTHROPIC_API_KEY)<br/>"
                  "<font face='Courier'>.\\run.ps1 test</font> &nbsp;|&nbsp; <font face='Courier'>.\\run.ps1 eval</font>"
                  " &nbsp;|&nbsp; <font face='Courier'>.\\run.ps1 guide</font><br/>Then open http://127.0.0.1:8000.", body),
    ]

    def footer(canvas, doc):
        """Draw the page footer: guide title on the left, page number on the right."""
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.drawString(0.75 * inch, 0.5 * inch, "Study Sprint: how the app works")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.8 * inch, title="Study Sprint: how the app works",
                            author="Study Sprint")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out


def build_guide():
    """Take the screenshots and build the PDF.

    Returns:
        The PDF's path.
    """
    return build_pdf(capture_screenshots())

