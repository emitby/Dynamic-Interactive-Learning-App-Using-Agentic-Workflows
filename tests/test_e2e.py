"""Browser end-to-end tests: real Chromium (Playwright) against the demo server.

Time is controlled with Playwright's clock (``page.clock``). The tests fast-forward 61
seconds instead of waiting, which works because the app's timer measures ``Date.now()``
against a deadline.

Chromium is installed inside the project (``.cache/ms-playwright``) by
``run.ps1 install-browser``. The launcher points ``PLAYWRIGHT_BROWSERS_PATH`` there. If
the browser is missing, these tests are skipped with instructions instead of failing.
"""

import socket
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

playwright_sync = pytest.importorskip("playwright.sync_api")

from studysprint.config import EXERCISES  # noqa: E402


def _free_port():
    """Ask the OS for a free loopback port (bind to port 0), so parallel runs never collide."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    """Run the demo app with uvicorn in a background thread for this module's tests."""
    import uvicorn

    from studysprint.api import create_app
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(demo=True, demo_stage_seconds=0.2), host="127.0.0.1",
                                        port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)


@pytest.fixture(scope="module")
def browser():
    """One headless Chromium for the module, or skip if it isn't installed."""
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"Chromium not installed ({type(exc).__name__}). Run: .\\run.ps1 install-browser")
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    """A fresh page with a fake clock, collecting console errors (including CSP violations)."""
    context = browser.new_context(viewport={"width": 1100, "height": 900})
    pg = context.new_page()
    pg.errors = []
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.clock.install()
    pg.goto(server)
    yield pg
    context.close()


def start_lesson(pg, subject="the water cycle", grade="3"):
    """Fill the start form and wait for the reading screen."""
    pg.fill("#subject", subject)
    pg.select_option("#grade", grade)
    pg.click("#start-button")
    pg.wait_for_selector("#screen-building:not([hidden])")
    for _ in range(40):                      # the progress poll uses timers, so advance the fake clock
        pg.clock.run_for(500)
        if pg.is_visible("#screen-reading"):
            break
    pg.wait_for_selector("#screen-reading:not([hidden])")


def do_break(pg):
    """Expect the break modal, check its content, click 'I did it!' and return the exercise shown."""
    pg.wait_for_selector("#break-modal:not([hidden])")
    exercise = pg.text_content("#break-exercise")
    assert exercise in EXERCISES.values()
    pg.click("#break-done")
    pg.wait_for_selector("#break-modal", state="hidden")
    return exercise


def answer_all(pg, index=0):
    """Pick option ``index`` for all five questions."""
    for q in range(5):
        pg.check(f'input[name="q{q}"][value="{index}"]')


def test_start_screen(page):
    assert page.is_visible("#screen-start")
    assert page.locator("#grade option").count() == 15          # placeholder + K, 1-12, College
    assert page.is_hidden("#timer")


def test_reading_timer_triggers_break_and_restarts(page):
    start_lesson(page)
    assert page.text_content("#timer-value") == "1:00"
    assert page.locator(".concept").count() == 3
    assert page.get_attribute("#diagram-img", "src").startswith("data:image/svg+xml")
    page.clock.run_for(30_000)
    assert page.is_hidden("#break-modal")                       # not before a minute
    page.clock.run_for(31_000)
    do_break(page)
    assert page.text_content("#timer-value") == "1:00"          # timer restarted
    page.clock.run_for(61_000)
    do_break(page)                                              # and it keeps cycling
    assert page.errors == []


def test_full_flow_with_quiz_break_results_and_start_over(page):
    start_lesson(page)
    page.clock.run_for(61_000)
    first = do_break(page)
    page.check("#finished-reading")                             # the "Finished Reading" radio button
    page.wait_for_selector("#screen-quiz:not([hidden])")
    assert page.locator(".question").count() == 5
    assert page.is_disabled("#submit-quiz")
    page.clock.run_for(61_000)                                  # a minute passes during the quiz
    second = do_break(page)
    answer_all(page)
    assert page.is_enabled("#submit-quiz")
    page.click("#submit-quiz")
    page.wait_for_selector("#screen-results:not([hidden])")
    assert "Great work" in page.text_content("#results-headline")
    assert page.text_content("#results-score").endswith("/ 5")
    assert page.text_content("#results-percent").endswith("%")
    exercises = page.text_content("#results-exercises")
    assert first in exercises and second in exercises
    assert page.locator("#results-review li").count() == 5
    assert page.is_hidden("#timer")
    page.click("#start-over")
    page.wait_for_selector("#screen-start:not([hidden])")
    assert page.input_value("#subject") == "" and page.input_value("#grade") == ""
    assert page.errors == []


def test_perfect_score_without_breaks(page):
    import json
    from studysprint.paths import FIXTURES_DIR
    lesson = json.loads((FIXTURES_DIR / "01-water-cycle-g3.json").read_text(encoding="utf-8"))
    start_lesson(page)
    page.check("#finished-reading")
    page.wait_for_selector("#screen-quiz:not([hidden])")
    for q, item in enumerate(lesson["questions"]):
        page.check(f'input[name="q{q}"][value="{item["answer_index"]}"]')
    page.click("#submit-quiz")
    page.wait_for_selector("#screen-results:not([hidden])")
    assert page.text_content("#results-percent") == "100%"
    assert "PERFECT" in page.text_content("#results-headline")
    assert "No breaks needed" in page.text_content("#results-exercises")


def test_blocked_and_invalid_subjects_show_errors(page):
    page.fill("#subject", "how to make a bomb")
    page.select_option("#grade", "5")
    page.click("#start-button")
    page.wait_for_selector("#start-error:not([hidden])")
    assert "can't teach" in page.text_content("#start-error") or "isn't something" in page.text_content("#start-error")
    page.fill("#subject", "x")
    page.click("#start-button")
    assert "subject" in page.text_content("#start-error").lower()
    assert page.is_visible("#screen-start")


def test_mobile_layout(browser, server):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    pg = context.new_page()
    pg.clock.install()
    pg.goto(server)
    start_lesson(pg)
    boxes = [pg.locator(".concept").nth(i).bounding_box() for i in range(3)]
    assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]       # stacked, not side by side
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390   # no horizontal scroll
    context.close()
