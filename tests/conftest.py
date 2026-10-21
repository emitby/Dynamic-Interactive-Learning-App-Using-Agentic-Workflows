"""Shared test fixtures: a scripted fake Claude client, scratch folders, and a valid lesson.

No test calls the real API. :class:`FakeClaude` returns pre-written responses. They're
routed **by agent** (identified from the system prompt), because the harness runs the
Diagram and Quiz agents in parallel and one shared queue would hand responses to the
wrong agent.

Scratch files go to ``.pytest_tmp/`` inside the project (gitignored) and are never
deleted (CLAUDE.md: nothing outside the project, no deletions).
"""

import copy
import json
import secrets
import threading
from types import SimpleNamespace as NS

import pytest

from studysprint.paths import FIXTURES_DIR, PROJECT_DIR

TEST_ROOT = PROJECT_DIR / ".pytest_tmp"


@pytest.fixture
def workdir():
    """A fresh scratch directory inside the project for one test."""
    d = TEST_ROOT / secrets.token_hex(6)
    d.mkdir(parents=True)
    return d


@pytest.fixture
def fixture_lesson():
    """The hand-written demo lesson (Water Cycle, Grade 3) as a dict. Every rule passes on it."""
    return json.loads((FIXTURES_DIR / "01-water-cycle-g3.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- scripted Claude

def text(t):
    """A text content block."""
    return NS(type="text", text=t)


def tool_use(name, id_=None, **input_):
    """A tool_use content block calling ``name`` with ``input_``."""
    return NS(type="tool_use", id=id_ or f"tu_{secrets.token_hex(3)}", name=name, input=input_)


def response(*content, stop=None):
    """A Messages API response. ``stop`` defaults to ``tool_use`` if any block is a tool call."""
    if stop is None:
        stop = "tool_use" if any(c.type == "tool_use" for c in content) else "end_turn"
    usage = NS(input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return NS(content=list(content), stop_reason=stop, usage=usage)


AGENT_KEYS = {"Intake & Safety Agent": "intake", "Curriculum Agent": "curriculum", "Diagram Agent": "diagram",
              "Quiz Agent": "quiz", "Reviewer Agent": "reviewer"}


class FakeClaude:
    """Stands in for ``anthropic.Anthropic`` and replays scripted responses per agent.

    Args:
        script: ``{"intake": [response, ...], "curriculum": [...], ...}``.

    Attributes:
        requests: Every request made, as ``(agent_key, kwargs)``, for assertions on what
            agents were sent.
    """

    def __init__(self, script):
        """Store the script. Each agent's list is consumed in order."""
        self.script = {k: list(v) for k, v in script.items()}
        self.requests = []
        self._lock = threading.Lock()
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        """Return the next scripted response for the agent named in the system prompt."""
        key = next(v for k, v in AGENT_KEYS.items() if f"You are the {k}" in kwargs["system"])
        with self._lock:
            self.requests.append((key, {**kwargs, "messages": list(kwargs["messages"])}))
            if not self.script.get(key):
                raise AssertionError(f"No scripted response left for {key}")
            return self.script[key].pop(0)


def happy_script(lesson):
    """A script in which every agent succeeds first time, producing ``lesson`` (a fixture dict)."""
    concepts = [{k: c[k] for k in ("title", "explanation", "example")} for c in lesson["concepts"]]
    quiz = [{"question": q["question"], "correct": q["options"][q["answer_index"]],
             "distractors": [o for i, o in enumerate(q["options"]) if i != q["answer_index"]],
             "concept_index": q["concept_index"], "why": q["why"]} for q in lesson["questions"]]
    d = lesson["diagram"]
    return {
        "intake": [response(tool_use("set_topic", topic=lesson["topic"], scope_note="The four steps of the water cycle.")),
                   response(text("Topic set."))],
        "curriculum": [response(tool_use("submit_concepts", concepts=concepts)), response(text("Done."))],
        "diagram": [response(tool_use("submit_diagram", svg=d["svg"], alt_text=d["alt_text"], caption=d["caption"])),
                    response(text("Done."))],
        "quiz": [response(tool_use("submit_quiz", questions=quiz)), response(text("Done."))],
        "reviewer": [response(tool_use("approve", notes="Accurate.")), response(text("Approved."))],
    }


@pytest.fixture
def happy(fixture_lesson):
    """A fresh copy of the happy-path script (scripts are consumed, so each test gets its own)."""
    return copy.deepcopy(happy_script(fixture_lesson))
