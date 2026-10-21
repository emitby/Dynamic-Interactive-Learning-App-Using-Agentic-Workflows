"""The agent loop: Claude plus validating tools, with tracing, a token budget and cost accounting.

Adapted from the spy-ief-strategy project, where this loop ran hundreds of live agent
sessions. The cycle:

    1. Send the conversation, the system prompt and the tool schemas to Claude.
    2. If Claude asks for tools, run them (concurrently), append **all** results in one
       user message, and repeat.
    3. When Claude replies with text only, the agent is done; that text is its reply.

Guardrail behavior:
    * Tools are the agent's only way to act. Agents have no file, shell or network
      access; tool handlers are plain Python functions bound to one lesson build.
    * A :class:`ToolError` (a rule violation) goes back to the model as an ``is_error``
      tool result naming the fix, so the agent can correct itself.
    * Unexpected exceptions are reported to the model as a generic internal error and
      logged as ``tool_crash``, so the run stays alive and evals can see the crash.
    * Every call is checked against the run-wide token budget *before* it's made.
    * A policy refusal ends the agent with a safe reply. Server-side fallbacks
      (``fallbacks="default"``) are enabled for refusal handling.
"""

import inspect
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import anthropic

from studysprint.paths import confine

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
# USD per million tokens for claude-opus-5-5: input, output, cache write (1.25x input), cache read (0.05x).
PRICES = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}
REFUSAL_REPLY = "The model declined this request."


class AgentError(RuntimeError):
    """An agent couldn't finish its task (for example it ran out of turns)."""


class BudgetExceeded(AgentError):
    """The lesson build used more tokens than its budget allows."""


class ToolError(ValueError):
    """A rule violation or precondition failure that the model should read and fix."""


@dataclass
class Tool:
    """One tool an agent may call.

    Attributes:
        name: Tool name the model uses.
        description: What the tool does and the rules it enforces. The model reads this.
        input_schema: JSON Schema of the arguments.
        handler: Python function called with the model's arguments as keyword arguments.
    """
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]

    def to_param(self):
        """The tool definition in the Messages API's format."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def schema(properties=None, required=()):
    """Build a strict JSON object schema: listed properties only, no extras allowed.

    Args:
        properties: ``{name: JSON Schema}`` for each argument.
        required: Names of required arguments.

    Returns:
        A JSON Schema dict with ``additionalProperties: false``.
    """
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": False}


class Trace:
    """Append-only JSONL log of every model call and tool call, with token usage totals.

    One trace file per lesson build (``runs/<id>/trace.jsonl``). Thread-safe, because the
    Diagram and Quiz agents run in parallel and share it.
    """

    def __init__(self, path):
        """Open (create) the trace file.

        Args:
            path: Where to write the JSONL (confined to the project).

        Side effects:
            Creates the parent directory if needed.
        """
        self.path = confine(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        self.by_agent = {}

    def log(self, **event):
        """Append one event (a JSON object with a UTC timestamp added) to the trace.

        Args:
            **event: Event fields. ``type`` and ``agent`` are conventional.
        """
        event["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = json.dumps(event, default=str, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def add_usage(self, agent, usage):
        """Add one API response's token usage to the run and per-agent totals.

        Args:
            agent: Agent name.
            usage: The ``usage`` object from an API response.

        Returns:
            The usage as a plain dict (input, output, cache_write, cache_read).
        """
        u = {"input": usage.input_tokens or 0, "output": usage.output_tokens or 0,
             "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
             "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0}
        with self._lock:
            for k, v in u.items():
                self.usage[k] += v
                self.by_agent.setdefault(agent, dict.fromkeys(self.usage, 0))[k] += v
        return u

    @property
    def total_tokens(self):
        """All tokens used so far (input + output + cache write + cache read)."""
        return sum(self.usage.values())

    def cost_usd(self, usage=None):
        """Dollar cost of ``usage`` (default: the whole run) at :data:`PRICES`.

        Args:
            usage: Optional usage dict with the four token types.

        Returns:
            Cost in USD, rounded to 4 decimals.
        """
        u = usage or self.usage
        return round(sum(u[k] * PRICES[k] for k in PRICES) / 1_000_000, 4)

    def events(self):
        """Read back every event written so far (used by evals and tests)."""
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]


class Agent:
    """One Claude agent: a system prompt, a set of tools and the loop that runs them."""

    def __init__(self, client, name, system, tools, trace, model, effort="medium", max_turns=12,
                 token_budget=None):
        """Configure an agent.

        Args:
            client: An ``anthropic.Anthropic`` client, or a scripted fake in tests.
            name: Display name used in the trace ("Curriculum Agent").
            system: The system prompt.
            tools: The :class:`Tool` objects this agent may use. Least privilege: each
                agent gets only what its job needs.
            trace: The shared :class:`Trace`.
            model: Claude model ID.
            effort: Output effort level ("low", "medium" or "high").
            max_turns: Model calls allowed before the agent is stopped.
            token_budget: Run-wide token cap checked before every call (``None`` = unlimited).
        """
        self.client = client
        self.name = name
        self.system = system
        self.tools = {t.name: t for t in tools}
        self.tool_params = [t.to_param() for t in tools]
        self.trace = trace
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.token_budget = token_budget

    def run(self, task):
        """Run the agent on one task until it replies with text.

        Args:
            task: The user-turn instruction (what to do now, with any data it needs).

        Returns:
            The agent's final text reply (or :data:`REFUSAL_REPLY`).

        Raises:
            BudgetExceeded: If the run's token budget is used up.
            AgentError: If the agent doesn't finish within ``max_turns``.
        """
        messages = [{"role": "user", "content": task}]
        self.trace.log(agent=self.name, type="start", task=task)
        for turn in range(self.max_turns):
            if self.token_budget and self.trace.total_tokens > self.token_budget:
                raise BudgetExceeded(f"Token budget of {self.token_budget:,} exceeded.")
            t0 = time.perf_counter()
            response = self.client.beta.messages.create(
                model=self.model, max_tokens=MAX_TOKENS, system=self.system, tools=self.tool_params,
                messages=messages, output_config={"effort": self.effort},
                # Prompt caching: the system prompt, tools and growing history are re-read
                # from the cache on later turns at about 1/20 of the input price.
                cache_control={"type": "ephemeral"}, betas=[FALLBACK_BETA], fallbacks="default",
            )
            usage = self.trace.add_usage(self.name, response.usage)
            self.trace.log(agent=self.name, type="model", turn=turn, stop_reason=response.stop_reason,
                           latency_s=round(time.perf_counter() - t0, 2), usage=usage)
            # The history is append-only: the assistant turn goes in exactly as returned.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                self.trace.log(agent=self.name, type="refusal")
                return REFUSAL_REPLY
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                reply = "\n".join(b.text for b in response.content if b.type == "text").strip()
                self.trace.log(agent=self.name, type="finish", reply=reply)
                return reply
            # Independent tool calls run concurrently. All results go back in ONE user
            # message, which the API requires for parallel tool use.
            with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
                results = list(pool.map(self._execute, tool_uses))
            messages.append({"role": "user", "content": results})
        raise AgentError(f"{self.name} did not finish within {self.max_turns} turns.")

    def _execute(self, block):
        """Run one ``tool_use`` block and turn the outcome into a ``tool_result``.

        Args:
            block: A ``tool_use`` content block from the model.

        Returns:
            A ``tool_result`` dict. ``is_error`` is set when the call failed, so the model
            knows to fix something.

        Side effects:
            Logs a ``tool`` event (and ``tool_crash`` for unexpected exceptions).
        """
        tool = self.tools.get(block.name)
        t0 = time.perf_counter()
        ok = False
        try:
            if tool is None:
                raise ToolError(f"Unknown tool {block.name!r}. Available: {sorted(self.tools)}.")
            if not isinstance(block.input, dict):
                raise ToolError("Tool input must be a JSON object.")
            # Check the arguments against the handler's signature first, so a
            # missing or extra argument gets a clear message instead of a TypeError.
            try:
                inspect.signature(tool.handler).bind(**block.input)
            except TypeError as exc:
                raise ToolError(f"Invalid arguments for {block.name}: {exc}") from None
            out = tool.handler(**block.input)
            content = out if isinstance(out, str) else json.dumps(out, default=str, ensure_ascii=False)
            ok = True
        except ToolError as exc:
            content = str(exc)
        except (ValueError, KeyError, TypeError) as exc:
            content = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # unexpected: report generically, log, keep the run alive
            content = f"Internal error in {block.name}: {type(exc).__name__}"
            self.trace.log(agent=self.name, type="tool_crash", tool=block.name, error=repr(exc))
        self.trace.log(agent=self.name, type="tool", tool=block.name, input=block.input, ok=ok,
                       latency_s=round(time.perf_counter() - t0, 3), error=None if ok else content[:500],
                       result_chars=len(content))
        result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
        if not ok:
            result["is_error"] = True
        return result


def make_client():
    """Create the real Anthropic client. It reads ``ANTHROPIC_API_KEY`` from the environment."""
    return anthropic.Anthropic()
