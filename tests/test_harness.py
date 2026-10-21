"""The harness and agents, end to end, with a scripted fake Claude (no API calls)."""

import copy

import pytest

from conftest import FakeClaude, response, text, tool_use
from studysprint.agents.runtime import AgentError, BudgetExceeded
from studysprint.config import AgentConfig
from studysprint.harness import Declined, StageError, build_lesson, new_context
from studysprint.paths import read_json


def ctx_for(client, workdir, subject="the water cycle", grade="3", **cfg):
    """A build context writing to the test's scratch folder."""
    return new_context(subject, grade, client=client, cfg=AgentConfig(**cfg) if cfg else None, runs_root=workdir)


def test_happy_path_builds_and_saves_a_lesson(happy, workdir):
    stages = []
    ctx = ctx_for(FakeClaude(happy), workdir)
    ctx.progress = stages.append
    lesson = build_lesson(ctx)
    assert lesson.topic == "The Water Cycle" and len(lesson.concepts) == 3 and len(lesson.questions) == 5
    for name in ("lesson.json", "manifest.json", "review.json", "trace.jsonl"):
        assert (ctx.run_dir / name).exists()
    manifest = read_json(ctx.run_dir / "manifest.json")
    assert manifest["status"] == "ok" and manifest["cost_usd"] > 0
    assert set(manifest["cost_by_agent_usd"]) == {"Intake Agent", "Curriculum Agent", "Diagram Agent", "Quiz Agent",
                                                  "Reviewer Agent"}
    assert stages[0].startswith("Choosing") and stages[-1].startswith("Checking")
    events = ctx.trace.events()
    assert [e["stage"] for e in events if e["type"] == "stage_ok"][:2] == ["Intake Agent", "Curriculum Agent"]


def test_topic_gate_declines_before_any_agent_runs(workdir):
    client = FakeClaude({})
    with pytest.raises(Declined):
        build_lesson(ctx_for(client, workdir, subject="how to make a bomb"))
    assert client.requests == []        # no API call, no cost


def test_subject_is_sent_as_quoted_data(happy, workdir):
    client = FakeClaude(happy)
    build_lesson(ctx_for(client, workdir))
    intake_task = next(kw for key, kw in client.requests if key == "intake")["messages"][0]["content"]
    assert '<subject>"the water cycle"</subject>' in intake_task


def test_intake_decline_reaches_the_learner(workdir):
    client = FakeClaude({"intake": [response(tool_use("decline", reason="That isn't a school subject. Try 'volcanoes'!")),
                                    response(text("Declined."))]})
    ctx = ctx_for(client, workdir, subject="asdfgh")
    with pytest.raises(Declined, match="volcanoes"):
        build_lesson(ctx)
    assert read_json(ctx.run_dir / "manifest.json")["status"] == "declined"


def test_intake_cannot_launder_a_blocked_topic(happy, workdir):
    """The agent rewrites a fine subject into a blocked one: set_topic re-runs the gate and refuses."""
    happy["intake"] = [response(tool_use("set_topic", topic="How to make a bomb", scope_note="x")),
                       response(tool_use("decline", reason="Let's study something else together!")),
                       response(text("Declined."))]
    ctx = ctx_for(FakeClaude(happy), workdir)
    with pytest.raises(Declined):
        build_lesson(ctx)
    tool_events = [e for e in ctx.trace.events() if e["type"] == "tool"]
    assert tool_events[0]["ok"] is False and "blocked" in tool_events[0]["error"]


def test_agent_self_corrects_after_rejection(happy, workdir, fixture_lesson):
    """A first submission that's too hard is rejected with measured numbers; the agent's retry is accepted."""
    bad = [{"title": "Thermal Phase Transitions", "explanation": "Evaporation constitutes an endothermic phase "
            "transition whereby molecular kinetic energy surpasses intermolecular cohesion.", "example": "Oceans."}] * 3
    good = happy["curriculum"][0]
    happy["curriculum"] = [response(tool_use("submit_concepts", concepts=bad)), good, response(text("Done."))]
    client = FakeClaude(happy)
    ctx = ctx_for(client, workdir)
    build_lesson(ctx)
    curriculum_calls = [kw for key, kw in client.requests if key == "curriculum"]
    rejection = curriculum_calls[1]["messages"][-1]["content"][0]
    assert rejection["is_error"] is True and "reading level is grade" in rejection["content"]


def test_reviewer_revision_of_concepts_also_redoes_diagram_and_quiz(happy, workdir):
    """Regression (live eval, ancient Egypt): revised concepts contradicted the old diagram."""
    h = copy.deepcopy(happy)
    h["reviewer"] = [response(tool_use("request_revision", target="concepts", issues=["Concept 2 is unclear."])),
                     response(text("Sent back.")),
                     response(tool_use("approve")), response(text("Approved."))]
    h["curriculum"] += copy.deepcopy(happy["curriculum"])
    h["quiz"] += copy.deepcopy(happy["quiz"])
    h["diagram"] += copy.deepcopy(happy["diagram"])
    client = FakeClaude(h)
    build_lesson(ctx_for(client, workdir))
    keys = [k for k, _ in client.requests]
    assert keys.count("curriculum") == 4 and keys.count("quiz") == 4 and keys.count("diagram") == 4
    revision_task = [kw for k, kw in client.requests if k == "curriculum"][2]["messages"][0]["content"]
    assert "Concept 2 is unclear." in revision_task


def test_two_revisions_then_approval_publishes(happy, workdir):
    h = copy.deepcopy(happy)
    reject = [response(tool_use("request_revision", target="quiz", issues=["Q3 has two right answers."])),
              response(text("Sent back."))]
    h["reviewer"] = reject + copy.deepcopy(reject) + [response(tool_use("approve")), response(text("Approved."))]
    h["quiz"] += copy.deepcopy(happy["quiz"]) + copy.deepcopy(happy["quiz"])
    lesson = build_lesson(ctx_for(FakeClaude(h), workdir))
    assert len(lesson.questions) == 5


def test_final_rejection_fails_the_build(happy, workdir):
    h = copy.deepcopy(happy)
    reject = [response(tool_use("request_revision", target="quiz", issues=["Q3 has two right answers."])),
              response(text("Sent back."))]
    h["reviewer"] = reject + copy.deepcopy(reject)
    h["quiz"] += copy.deepcopy(happy["quiz"])
    ctx = ctx_for(FakeClaude(h), workdir, max_revisions=1)
    with pytest.raises(StageError, match="review"):
        build_lesson(ctx)
    manifest = read_json(ctx.run_dir / "manifest.json")
    assert manifest["status"] == "failed" and "review" in manifest["error"]


def test_agent_that_never_submits_fails_hand_off(happy, workdir):
    happy["diagram"] = [response(text("I drew it in my head."))]
    with pytest.raises(StageError, match="Diagram"):
        build_lesson(ctx_for(FakeClaude(happy), workdir))


def test_turn_limit(happy, workdir):
    # The Intake Agent keeps calling a tool with bad arguments and never finishes.
    happy["intake"] = [response(tool_use("set_topic", topic="x")) for _ in range(10)]
    with pytest.raises(AgentError, match="did not finish"):
        build_lesson(ctx_for(FakeClaude(happy), workdir))


def test_token_budget(happy, workdir):
    with pytest.raises(BudgetExceeded):
        build_lesson(ctx_for(FakeClaude(happy), workdir, token_budget=150))


def test_unknown_tool_and_bad_arguments_are_returned_as_errors(happy, workdir):
    happy["intake"] = [response(tool_use("delete_files")), response(tool_use("set_topic", topic="Water")),
                       *happy["intake"]]
    client = FakeClaude(happy)
    build_lesson(ctx_for(client, workdir))
    intake = [kw for k, kw in client.requests if k == "intake"]
    assert "Unknown tool" in intake[1]["messages"][-1]["content"][0]["content"]
    assert "Invalid arguments" in intake[2]["messages"][-1]["content"][0]["content"]


def test_refusal_is_handled(happy, workdir):
    happy["intake"] = [response(stop="refusal")]
    with pytest.raises(StageError, match="did not choose a topic"):
        build_lesson(ctx_for(FakeClaude(happy), workdir))


def test_all_agents_use_configured_model_and_caching(happy, workdir):
    client = FakeClaude(happy)
    build_lesson(ctx_for(client, workdir))
    assert {kw["model"] for _, kw in client.requests} == {"claude-opus-5-5"}
    assert all(kw["cache_control"] == {"type": "ephemeral"} for _, kw in client.requests)


def test_reviewer_sees_answer_key_and_code_keeps_correct_answers(happy, workdir, fixture_lesson):
    client = FakeClaude(happy)
    lesson = build_lesson(ctx_for(client, workdir))
    review_task = next(kw for k, kw in client.requests if k == "reviewer")["messages"][0]["content"]
    assert "✓" in review_task
    # After code shuffles the options, each answer key still points at the agent's correct answer.
    expected = [q["options"][q["answer_index"]] for q in fixture_lesson["questions"]]
    assert [q.options[q.answer_index] for q in lesson.questions] == expected
