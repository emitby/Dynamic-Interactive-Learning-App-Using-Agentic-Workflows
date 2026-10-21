"""The web app: a FastAPI backend serving the static front end and a small JSON API.

Flow (all user-facing routes):

    GET  /                                    the single-page app (static/index.html)
    GET  /api/config                          grades, timer length, exercise names, demo flag
    POST /api/lessons {subject, grade}        validate input and start building a lesson -> {job_id}
    GET  /api/lessons/{job_id}                building | ready (+ lesson, session_id) | declined | failed
    POST /api/sessions/{sid}/break            the visible timer hit 0: assign a random exercise
    POST /api/sessions/{sid}/break/{bid}/done the learner did it; the timer may restart
    POST /api/sessions/{sid}/finish-reading   "Finished Reading" selected: open the quiz
    POST /api/sessions/{sid}/submit {answers} grade the quiz and return the results

Design decisions:
    * **Lesson builds run in background threads.** A build takes a minute or more, so the
      browser polls a job and shows each stage on a progress screen instead of holding a
      request open.
    * **At most** :data:`MAX_ACTIVE_BUILDS` builds run at once (HTTP 429 beyond that).
      Each build costs API credits, so this caps spend if the page is spammed.
    * **Demo mode** (``create_app(demo=True)``) serves bundled lessons from
      ``studysprint/fixtures/`` with no API key and no cost. Tests, screenshots and offline
      tries use it.
    * **Security headers:** a strict Content-Security-Policy (only this server's scripts
      and styles; images from this server or ``data:``), ``nosniff`` and no framing. With
      the sanitizer and ``<img>`` rendering of diagrams, agent-written content can't
      execute in the browser.
    * The server binds to ``127.0.0.1`` only (see :mod:`studysprint.cli`), so it isn't
      reachable from the network.
"""

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from studysprint.config import EXERCISES, GRADE_LABELS, GRADES, TIMER_SECONDS
from studysprint.harness import Declined, build_lesson, new_context
from studysprint.lesson import Lesson
from studysprint.paths import FIXTURES_DIR, STATIC_DIR, confine, read_json
from studysprint.safety import SafetyError, topic_gate, validate_subject
from studysprint.sessions import FlowError, SessionStore, exercise_label

MAX_ACTIVE_BUILDS = 3

#: Progress-screen stages, in order. Demo mode replays them with short pauses so the
#: progress screen behaves (and can be screenshotted) like a real build.
DEMO_STAGES = ["Choosing the best topic for your grade", "Writing three key concepts",
               "Drawing a diagram and writing your quiz", "Checking everything for accuracy"]

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class LessonRequest(BaseModel):
    """Body of ``POST /api/lessons``."""
    subject: str
    grade: str


class SubmitRequest(BaseModel):
    """Body of ``POST /api/sessions/{sid}/submit``: one option index per question."""
    answers: list[int]


@dataclass
class Job:
    """A lesson build in progress (or finished).

    Attributes:
        id: Public job token.
        status: ``building``, ``ready``, ``declined`` or ``failed``.
        stage: The current stage description for the progress screen.
        message: The user-facing reason when declined or failed.
        session_id: Set when ready: the learner's session for this lesson.
        lesson: Set when ready.
    """
    id: str
    status: str = "building"
    stage: str = "Getting started"
    message: Optional[str] = None
    session_id: Optional[str] = None
    lesson: Optional[Lesson] = None
    created: float = field(default_factory=time.monotonic)


def load_fixtures():
    """Load the demo lessons bundled in ``studysprint/fixtures/``.

    Returns:
        A list of :class:`Lesson`, sorted by file name so the default demo lesson is stable.
    """
    folder = confine(FIXTURES_DIR)
    return [Lesson(**read_json(p)) for p in sorted(folder.glob("*.json"))]


def pick_fixture(fixtures, subject):
    """Choose the demo lesson that best matches a subject.

    A fixture matches when any word of its topic or subject (4+ letters) appears in the
    requested subject. Otherwise the first fixture is used. The lesson keeps its own topic
    and grade, so the screen always shows what is really being taught.

    Args:
        fixtures: Lessons from :func:`load_fixtures`.
        subject: The requested subject.

    Returns:
        A :class:`Lesson`.
    """
    s = subject.lower()
    for lesson in fixtures:
        words = {w for w in f"{lesson.topic} {lesson.subject}".lower().split() if len(w) >= 4}
        if any(w in s for w in words):
            return lesson
    return fixtures[0]


def create_app(demo=False, client_factory=None, demo_stage_seconds=0.6, runs_root=None):
    """Build the FastAPI application.

    Args:
        demo: Serve bundled fixture lessons instead of calling Claude.
        client_factory: Optional zero-argument callable returning an Anthropic client
            (tests inject a scripted fake). Defaults to the real client.
        demo_stage_seconds: Pause per progress stage in demo mode (tests pass 0).
        runs_root: Optional parent folder for lesson run directories (tests use a scratch folder).

    Returns:
        The configured ``FastAPI`` app. Its state (jobs, sessions) lives in this closure,
        so separate apps (for example in parallel tests) never share state.
    """
    app = FastAPI(title="Study Sprint", docs_url=None, redoc_url=None, openapi_url=None)
    sessions = SessionStore()
    jobs = {}
    jobs_lock = threading.Lock()
    fixtures = load_fixtures() if demo else []

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Add security headers to every response. See the module docstring."""
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=str(confine(STATIC_DIR))), name="static")

    @app.get("/")
    def index():
        """Serve the single-page app."""
        return FileResponse(confine(STATIC_DIR / "index.html"))

    @app.get("/api/config")
    def config():
        """Everything the front end needs to render the start screen and run the timer."""
        return {"grades": [{"code": g, "label": GRADE_LABELS[g]} for g in GRADES],
                "timer_seconds": TIMER_SECONDS, "exercises": EXERCISES, "demo": demo}

    # ------------------------------------------------------------------ lesson jobs

    def _finish(job, lesson):
        """Mark a job ready and open the learner's session for its lesson."""
        job.lesson = lesson
        job.session_id = sessions.create(lesson).id
        job.status = "ready"

    def _run_live(job, ctx):
        """Background thread body: run the agent pipeline and record the outcome on ``job``."""
        try:
            _finish(job, build_lesson(ctx, lesson_id=job.id))
        except Declined as exc:
            job.status, job.message = "declined", str(exc)
        except Exception:
            # Details are in the run's trace and manifest; the learner gets a friendly message.
            job.status = "failed"
            job.message = "Sorry, we couldn't build that lesson. Please try again or try a different subject."

    def _run_demo(job, subject):
        """Background thread body for demo mode: replay the stages, then serve a fixture."""
        for stage in DEMO_STAGES:
            job.stage = stage
            time.sleep(demo_stage_seconds)
        _finish(job, pick_fixture(fixtures, subject))

    @app.post("/api/lessons")
    def start_lesson(req: LessonRequest):
        """Validate the request and start a build.

        Returns:
            ``{"job_id": ...}``, or ``{"job_id", "status": "declined", "message"}`` when
            the topic gate declines at once (no build, no cost).

        Raises:
            HTTPException 400: The subject or grade is invalid (message is user-facing).
            HTTPException 429: Too many builds are already running.
        """
        if req.grade not in GRADE_LABELS:
            raise HTTPException(400, "Please choose a grade.")
        try:
            subject = validate_subject(req.subject)
        except SafetyError as exc:
            raise HTTPException(400, str(exc)) from None
        job = Job(id=secrets.token_urlsafe(12))
        reason = topic_gate(subject, req.grade)
        if reason:
            job.status, job.message = "declined", reason
            with jobs_lock:
                jobs[job.id] = job
            return {"job_id": job.id, "status": job.status, "message": job.message}
        with jobs_lock:
            if sum(j.status == "building" for j in jobs.values()) >= MAX_ACTIVE_BUILDS:
                raise HTTPException(429, "Lots of lessons are being built right now. Please try again in a minute.")
            jobs[job.id] = job
        if demo:
            target, args = _run_demo, (job, subject)
        else:
            def progress(stage):
                """Harness callback: show the current stage on the progress screen."""
                job.stage = stage
            try:
                ctx = new_context(subject, req.grade, client=client_factory() if client_factory else None,
                                  progress=progress, runs_root=runs_root)
            except Exception:
                # Typically a missing ANTHROPIC_API_KEY. Free the slot and explain.
                job.status, job.message = "failed", "The lesson builder isn't configured (no API key)."
                raise HTTPException(503, "The lesson builder isn't available. Try demo mode: run.ps1 serve --demo") from None
            target, args = _run_live, (job, ctx)
        threading.Thread(target=target, args=args, daemon=True).start()
        return {"job_id": job.id, "status": job.status}

    @app.get("/api/lessons/{job_id}")
    def lesson_status(job_id: str):
        """Poll a build. When ready, includes the lesson (no answer keys) and the session ID."""
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown lesson.")
        out = {"status": job.status, "stage": job.stage, "message": job.message}
        if job.status == "ready":
            out.update(session_id=job.session_id, lesson=job.lesson.public_view())
        return out

    # ------------------------------------------------------------------ sessions

    def _session_call(fn, *args):
        """Run a session action, mapping flow and lookup errors to HTTP errors.

        Raises:
            HTTPException 404: Unknown or expired session.
            HTTPException 409: The action doesn't fit the session's phase.
        """
        try:
            return fn(*args)
        except KeyError:
            raise HTTPException(404, "This session has expired. Please start over.") from None
        except FlowError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/sessions/{sid}/break")
    def start_break(sid: str):
        """Assign a random exercise because the 1-minute timer ran out."""
        b = _session_call(sessions.request_break, sid)
        return {"break_id": b.id, "exercise": b.exercise, "label": exercise_label(b.exercise)}

    @app.post("/api/sessions/{sid}/break/{bid}/done")
    def finish_break(sid: str, bid: str):
        """Record the exercise as done so the timer can restart."""
        _session_call(sessions.complete_break, sid, bid)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/finish-reading")
    def finish_reading(sid: str):
        """Leave the reading phase and open the quiz."""
        _session_call(sessions.finish_reading, sid)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/submit")
    def submit(sid: str, req: SubmitRequest):
        """Grade the quiz. Returns the score, percentage, message, review and exercises done."""
        return _session_call(sessions.submit, sid, req.answers)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        """Return errors as ``{"error": message}`` for the front end to display."""
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
