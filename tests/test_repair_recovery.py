"""Keep a candidate through invalid reviews/repairs and bounded recovery."""

import asyncio
import copy
import json

import httpx
import pytest

from test_scenarios import ScenarioClient, app_for, review_output, write_scenario
from trajectory_remix.server import create_app


async def done(runner):
    await asyncio.wait_for(runner.job, 20)


def repair_scenario(tmp_path, *, mode="edits", repairs=2):
    return write_scenario(tmp_path, {"steps": {
        "user": {"uses": "remix.message", "inputs": {"text": "Check the sum."}},
        "draft": {"uses": "remix.draft", "needs": ["user"],
                  "inputs": {"reasoning": "An incorrect calculation.", "response": "Four."}},
        "review": {
            "uses": "remix.validate", "needs": ["draft"], "endpoint": "validator",
            "checks": {name: {"prompt": "review.md", "view": view}
                       for name, view in [("notes", "reasoning"), ("delivery", "correctness")]},
            "repair_prompt": "repair.md", "repair_mode": mode, "max_repairs": repairs,
        },
    }}, {"review.md": "Validate the evidence.", "repair.md": "Repair ONLY the findings."})


class RepairClient:
    def __init__(self, failure, *, always=False, mode="edits"):
        self.failure, self.always, self.mode = failure, always, mode
        self.repairs = 0
        self.corrected = False
        self.reviews = []
        self.inputs = []

    async def complete(self, endpoint, messages, tools=None):
        data = json.loads(messages[1]["content"])
        if messages[0]["content"].startswith("Validate"):
            self.reviews.append(data["view"])
            return {"content": json.dumps(review_output(
                messages, self.corrected or data["view"] != "reasoning", "Correct the calculation"
            ))}
        self.repairs += 1
        self.inputs.append(data)
        if self.mode == "replay":
            if self.repairs == 1:
                return {"content": '{"replay":{"0":[{"op":"generate","window":"missing","content":"x"}]}}'}
            self.corrected = True
            return {"content": json.dumps({"replay": data["trajectory"]["replay"]})}
        unit = next(u for u in data["review"]["events"]
                    if u.get("data", {}).get("content") == "An incorrect calculation.")
        edit = {"action": "replace_text", "event_id": unit["event_id"],
                "field": "/content", "old": "incorrect", "new": "accurate"}
        if self.repairs == 1 or self.always:
            if self.failure == "json":
                return {"content": '{"edits":['}
            if self.failure == "schema":
                edit.pop("old")
            elif self.failure in ("data.content", "/data/content"):
                edit["field"] = self.failure
            elif self.failure == "wrapper":
                edit = {"action": "replace", "event_id": unit["event_id"], "event": unit}
            elif self.failure == "compile":
                edit = {"action": "append", "tick": "0", "event": {
                    "op": "generate", "window": "missing", "content": "x"}}
            elif self.failure == "conflict":
                # Different replacements of one source event must remain atomic.
                return {"content": json.dumps({"edits": [
                    {"action": "replace", "event_id": unit["event_id"], "event": {
                        "op": "generate", "window": unit["window"], "content": text}}
                    for text in ("one", "two")
                ]})}
        else:
            self.corrected = True
        return {"content": json.dumps({"edits": [edit]})}


@pytest.mark.parametrize("failure", ["json", "schema", "data.content", "/data/content", "wrapper", "compile", "conflict"])
async def test_invalid_repair_gets_feedback_and_second_attempt_on_same_sample(compiler, tmp_path, failure):
    fake = RepairClient(failure)
    runner = app_for(tmp_path, compiler, fake, repair_scenario(tmp_path), legacy_reviews=False).state.scenario_runner
    runner.start("custom")
    await done(runner)
    assert runner.status["phase"] == "complete", list(runner.logs)
    assert runner.status["attempts"] == runner.status["accepted"] == 1
    assert len(runner.state["samples"]) == 1 and fake.repairs == 2
    assert fake.inputs[1]["retry_feedback"]["error"]
    assert fake.inputs[0]["review"] == fake.inputs[1]["review"]
    assert fake.reviews == ["correctness", "reasoning"] * 2
    assert "An accurate calculation." in json.dumps(runner.builder.sample["replay"])
    compiler.compile(runner.state["samples"][0])
    assert runner.snapshot["validation_progress"]["review"]["repairs"] == 2


async def test_invalid_repairs_exhaust_budget_without_publishing_or_revalidating_them(compiler, tmp_path):
    fake = RepairClient("compile", always=True)
    runner = app_for(tmp_path, compiler, fake, repair_scenario(tmp_path), legacy_reviews=False).state.scenario_runner
    runner.state["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner.start("custom")
    await done(runner)
    assert runner.status["phase"] == "rejected"
    assert fake.repairs == 2 and fake.reviews == ["correctness", "reasoning"]
    saved = runner.state["samples"][0]
    assert "missing" not in json.dumps(saved["replay"])
    assert "An incorrect calculation." in json.dumps(saved["replay"])
    assert saved["metadata"]["validation_level"] == -1
    assert "after 2 repair attempt(s)" in runner.status["last_error"]
    compiler.compile(saved)


async def test_full_replay_repair_recovers_from_compile_failure(compiler, tmp_path):
    fake = RepairClient("compile", mode="replay")
    runner = app_for(tmp_path, compiler, fake, repair_scenario(tmp_path, mode="replay"), legacy_reviews=False).state.scenario_runner
    runner.start("custom")
    await done(runner)
    assert runner.status["phase"] == "complete", list(runner.logs)
    assert fake.repairs == 2 and fake.inputs[1]["retry_feedback"]["error"]
    assert fake.inputs[0]["trajectory"]["replay"] == fake.inputs[1]["trajectory"]["replay"]


@pytest.mark.parametrize("interruption", ["provider", "cancel"])
async def test_interruption_preserves_remaining_repair_budget_on_resume(compiler, tmp_path, interruption):
    class Outage(RepairClient):
        offline = True

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Repair") and self.repairs == 1 and self.offline:
                if interruption == "cancel":
                    self.entered.set()
                    await asyncio.Event().wait()
                raise httpx.ConnectError("offline")
            return await super().complete(endpoint, messages, tools)

    fake = Outage("compile")
    fake.entered = asyncio.Event()
    root = repair_scenario(tmp_path)
    runner = app_for(tmp_path, compiler, fake, root, legacy_reviews=False).state.scenario_runner
    runner.start("custom")
    if interruption == "cancel":
        await asyncio.wait_for(fake.entered.wait(), 10)
        await runner.stop()
    else:
        await done(runner)
    assert runner.status["phase"] == ("api_paused" if interruption == "provider" else "cancelled")
    progress = runner.snapshot["validation_progress"]["review"]
    assert progress["repairs"] == 2 and progress["pending"]
    assert progress["feedback"]["error"]
    fake.offline = False
    resumed = create_app(tmp_path / "project.sqlite", client=fake, scenarios_dir=root).state.scenario_runner
    resumed.resume_saved(runner.status["draft_id"])
    await done(resumed)
    assert resumed.status["phase"] == "complete", list(resumed.logs)
    assert resumed.status["attempts"] == 1 and fake.repairs == 2


@pytest.mark.parametrize("failure", ["json", "unknown_context", "repeated_context", "rounds"])
async def test_review_errors_recover_without_spending_repair_budget(compiler, tmp_path, failure):
    class Reviewer:
        calls = 0

        async def complete(self, endpoint, messages, tools=None):
            self.calls += 1
            data = json.loads(messages[1]["content"])
            if self.calls == 1:
                if failure == "json":
                    return {"content": "broken JSON"}
                uid = "history:replay[0][0]" if failure == "unknown_context" else data["events"][0]["id"]
                return {"content": json.dumps(review_output(messages, context_requests=[uid]))}
            assert data["retry_feedback"]["error"]
            return {"content": json.dumps(review_output(messages))}

    root = write_scenario(tmp_path, {"steps": {"review": {
        "uses": "remix.validate", "endpoint": "validator", "max_repairs": 2,
        "checks": {"layout": {"prompt": "review.md", "view": "layout",
                              "context_rounds": 0 if failure == "rounds" else 2}},
    }}}, {"review.md": "Validate the evidence."})
    fake = Reviewer()
    runner = app_for(tmp_path, compiler, fake, root, legacy_reviews=False).state.scenario_runner
    runner.start("custom")
    await done(runner)
    assert runner.status["phase"] == "complete", list(runner.logs)
    assert fake.calls == 2 and runner.status["attempts"] == 1
    assert runner.snapshot["validation_progress"]["review"]["repairs"] == 0


async def test_exhausted_review_recovery_pauses_batch_and_resumes_same_candidate(compiler, tmp_path):
    class Reviewer:
        broken = True
        calls = 0

        async def complete(self, endpoint, messages, tools=None):
            self.calls += 1
            result = review_output(messages, context_requests=["history:replay[0][0]"]) if self.broken else review_output(messages)
            return {"content": json.dumps(result)}

    root = repair_scenario(tmp_path)
    fake = Reviewer()
    runner = app_for(tmp_path, compiler, fake, root, legacy_reviews=False).state.scenario_runner
    runner.start("custom", batch_size=2)
    await done(runner)
    assert runner.status["phase"] == "evaluation_failed" and fake.calls == 3
    assert runner.status["batch"]["state"] == "paused"
    assert runner.status["attempts"] == 1 and runner.status["accepted"] == 0
    saved = copy.deepcopy(runner.state["samples"][0])
    assert saved["metadata"]["validation_level"] == 0
    assert saved["metadata"]["generation_phase"] == "planning"
    assert saved["metadata"]["evaluation_error"] and not saved["metadata"].get("evaluations")
    assert runner.can_resume(saved["id"])
    fake.broken = False
    resumed = create_app(tmp_path / "project.sqlite", client=fake, scenarios_dir=root).state.scenario_runner
    resumed.resume_saved(saved["id"])
    await done(resumed)
    assert resumed.status["phase"] == "complete", list(resumed.logs)
    assert resumed.status["batch"]["completed"] == resumed.status["attempts"] == 2
    assert resumed.state["samples"][0]["id"] == saved["id"]
    assert resumed.state["samples"][0]["replay"] == saved["replay"]
    assert "evaluation_error" not in resumed.state["samples"][0]["metadata"]


async def test_over_budget_context_request_can_be_narrowed_without_losing_evidence(compiler, tmp_path):
    class Reviewer:
        requested = False
        corrected = False
        received = False

        async def complete(self, endpoint, messages, tools=None):
            data = json.loads(messages[1]["content"])
            assert sum(len(compiler.tokenizer.encode(m["content"]).ids) for m in messages) + 128 <= 2200
            ids = {u["id"] for u in data["events"]}
            prior = [uid for values in data["boundary"]["history"].values() for uid in values
                     if uid not in ids and "/content@" in uid]
            if prior and data["boundary"]["tick"] > 0 and not self.requested:
                self.requested = True
                self.uid = prior[-1]
                # The full earlier message exceeds the budget; a single missing
                # fragment fits. No source content may be silently truncated.
                result = review_output(messages, context_requests=["window:chat"])
            elif "retry_feedback" in data and not self.corrected:
                assert "max_input_tokens" in data["retry_feedback"]["error"]
                self.corrected = True
                result = review_output(messages, context_requests=[self.uid])
            else:
                if self.corrected and self.uid in ids:
                    self.received = True
                result = review_output(messages)
            return {"content": json.dumps(result)}

    root = write_scenario(tmp_path, {"steps": {
        "user": {"uses": "remix.message", "inputs": {"text": "earlier evidence " * 150}},
        "draft": {"uses": "remix.draft", "needs": ["user"], "inputs": {"response": "Acknowledged."}},
        "review": {"uses": "remix.validate", "needs": ["draft"], "endpoint": "validator",
                   "checks": {"layout": {"prompt": "review.md", "view": "layout",
                                         "max_input_tokens": 2200, "content_tokens": 128}}},
    }}, {"review.md": "Validate the evidence."})
    fake = Reviewer()
    runner = app_for(tmp_path, compiler, fake, root, legacy_reviews=False).state.scenario_runner
    runner.start("custom")
    await done(runner)
    assert runner.status["phase"] == "complete", list(runner.logs)
    assert fake.requested and fake.corrected and fake.received


@pytest.mark.parametrize("release", [True, False])
async def test_tool_evidence_lifetime_follows_explicit_summary_boundary(compiler, tmp_path, release):
    fake = ScenarioClient(turns=2, tools=True)
    runner = app_for(tmp_path, compiler, fake).state.scenario_runner
    runner.registry.scenarios["user_problem"].flow.steps["conversation"].steps["draft"].inputs["release_tool_context"] = release
    runner.start("user_problem")
    await done(runner)
    assert runner.status["phase"] == "complete", list(runner.logs)
    plan = compiler.compile(runner.state["samples"][0])
    windows = {w["id"]: w for w in plan["windows"]}
    calls = plan["tool_calls"]
    notes = [e for e in plan["events"] if e.get("op") == "generate" and e.get("text") == "Adding two pairs yields four."]
    assert len(notes) == 2
    for i, note in enumerate(notes):
        observation, reply = calls[2*i:2*i+2]
        closed = windows[observation["window"]]["close"]
        assert observation["result_end"] < note["start"] <= note["end"] < closed
        if release:
            assert closed < reply["request_start"]
        else:
            assert reply["result_end"] < closed
        assert windows[note["window"]]["close"] > reply["result_end"]
