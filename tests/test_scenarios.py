"""Workflow boundaries, lifecycle, concurrency, and simulated replay integration."""

import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
import yaml

from trajectory_remix.dataset import training_eligible
from trajectory_remix.scenarios import ScenarioRegistry, resolve
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore

EXAMPLES = Path(__file__).parents[1] / "examples/scenarios"


def review_output(messages, valid=True, feedback="Incorrect answer", context_requests=None):
    schema = json.loads(messages[0]["content"].split("Return JSON matching this schema:\n")[-1])
    if "checks" not in schema["properties"]:
        result = {"valid": valid, "feedback": [] if valid else [feedback]}
    else:
        result = {"checks": {
            name: {"evidence": "replay[0][0]: reviewed source", "status": "pass" if valid else "fail", "feedback": "" if valid else feedback}
            for name in schema["properties"]["checks"]["required"]
        }}
    if context_requests:
        result["context_requests"] = context_requests
        if "checks" in result:
            for item in result["checks"].values():
                item.update(status="needs_context", evidence="Earlier evidence is required", feedback="")
    return result


def app_for(tmp_path, compiler, client, scenarios=EXAMPLES, *, legacy_reviews=True):
    path = tmp_path / "project.sqlite"
    DatasetStore.initialize(path, compiler)
    app = create_app(path, client=client, scenarios_dir=scenarios)
    # Historical lifecycle tests exercise the supported single-payload review
    # contract. Bounded review integration has dedicated tests below.
    if legacy_reviews:
        from trajectory_remix.scenarios import ReviewCheck

        for scenario in app.state.scenario_runner.registry.scenarios.values():
            for step in scenario.flow.steps.values():
                step.checks = {
                    name: value.prompt if isinstance(value, ReviewCheck) else value
                    for name, value in step.checks.items()
                }
                step.repair_mode = "replay"
    return app


class ScenarioClient:
    def __init__(self, turns=1, tools=False, valid=True):
        self.turns, self.tools, self.valid = turns, tools, valid
        self.messages = []
        self.assessments = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.hold = None

    async def complete(self, endpoint, messages, tools=None):
        self.messages.append(copy.deepcopy(messages))
        prompt = messages[0]["content"]
        if self.hold and self.hold in prompt:
            self.entered.set()
            await self.release.wait()
        if prompt.startswith("Invent"):
            output = {
                "identifier": "python-math-example",
                "files": [],
                "problem": "What is 2 + 2?",
                "situation": "A user needs arithmetic help.",
                "success_criteria": "Answer 4",
            }
        elif prompt.startswith("Judge"):
            output = {"similarity": 0, "feedback": "Distinct"}
        elif prompt.startswith("Solve"):
            output = {"answer": "4", "verification": "2 + 2 = 4"}
        elif prompt.startswith("Write the next fictional"):
            output = {"text": "What is 2 + 2?"}
        elif prompt.startswith("Plan ONLY"):
            output = {
                "calls": [
                    [{"tool": "calculator", "arguments": {"expression": "2 + 2"}}]
                ]
                if self.tools
                else []
            }
        elif prompt.startswith("Write Kidra"):
            assert "author-only" not in messages[1]["content"]
            output = {
                "text": "Adding two pairs yields four."
                if "brief reasoning" in prompt
                else "The answer is 4."
            }
        elif prompt.startswith("Decide"):
            self.assessments += 1
            output = {
                "resolved": self.assessments >= self.turns,
                "next_exchange": "Ask for a concrete example.",
            }
        elif prompt.startswith("Validate"):
            output = review_output(messages, self.valid)
        else:
            raise AssertionError(prompt[:80])
        return {"content": json.dumps(output)}


async def finish(runner):
    await asyncio.wait_for(runner.job, 5)
    assert runner.status["phase"] == "complete", (runner.status, list(runner.logs))


async def test_example_single_and_multiple_turns_are_compilable_and_pending_human_review(
    compiler, tmp_path
):
    fake = ScenarioClient(turns=2, tools=True)
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await finish(runner)
    samples = list(app.state.dataset["samples"])
    assert len(samples) == 1
    sample = samples[0]
    assert sample["metadata"]["validation_level"] == 1
    assert not training_eligible(sample)
    assert sample["metadata"]["context_tags"]
    assert sample["metadata"]["roles"]["kidra"]["tags"] == {}
    plan = compiler.compile(sample)
    assert [call["tool"] for call in plan["tool_calls"]].count("calculator") == 2
    replies = [call for call in plan["tool_calls"] if call["tool"] == "chat"]
    assert len(replies) == 2
    assert all(call["outcome"]["ok"] for call in replies)
    assert all(call["arguments"] == {"content": "The answer is 4."} for call in replies)
    assert not any(
        d.get("code") in {"tool_not_shown", "tools_in_system", "direct_chat_generation"}
        for d in plan["diagnostics"]
    )
    assert all(
        event["window"] == "tools"
        for events in sample["replay"].values()
        for event in events
        if event["op"] == "show_tools"
    )
    assert not any(
        event["op"] == "generate"
        and event["window"] == "chat"
        and event["content"] != compiler.special["eos"]
        for events in sample["replay"].values()
        for event in events
    )
    # Request tokens are supervised; the actual message is inserted by the tool
    # only when it dispatches, and that external observation is masked.
    for call in replies:
        before = compiler.frame(plan, call["start"] - 1)["windows"]["chat"]["text"]
        after = compiler.frame(plan, call["end"])["windows"]["chat"]["text"]
        assert (
            after.count("Kidra: The answer is 4.")
            == before.count("Kidra: The answer is 4.") + 1
        )
        request_rows = [
            r for r in plan["rows"] if r["event"] == call["path"] + ".request"
        ]
        delivery_rows = [
            r
            for r in plan["rows"]
            if r["window"] == "chat"
            and r["event"].startswith(call["path"] + ".window.")
        ]
        assert request_rows and all(r["loss_weight"] > 0 for r in request_rows)
        assert delivery_rows and all(r["loss_weight"] == 0 for r in delivery_rows)
    assert all(w["close"] is not None for w in plan["windows"])
    observations = [
        m[1]["content"]
        for m in fake.messages
        if m[0]["content"].startswith("Write Kidra's brief reasoning")
    ]
    assert all('"ok":true' in message.replace(" ", "") for message in observations)
    answers = [json.loads(m[1]["content"]) for m in fake.messages
               if m[0]["content"].startswith("Write Kidra") and "brief reasoning" not in m[0]["content"]]
    assert answers and all("observations" not in answer and "reasoning" in answer for answer in answers)
    # Technical solving does not receive the user personality; the user does.
    solution = next(m for m in fake.messages if m[0]["content"].startswith("Solve"))
    assert "role profile" not in solution[0]["content"]
    assert "behavior" not in solution[1]["content"]
    user = next(
        m
        for m in fake.messages
        if m[0]["content"].startswith("Write the next fictional")
    )
    assert "behavior" in user[0]["content"]
    assert runner.snapshot["steps"]["conversation.0.answer"]["messages"]
    # Restart retains inspectable outputs and status, without replaying LLM calls.
    restored = create_app(tmp_path / "project.sqlite", scenarios_dir=EXAMPLES)
    assert (
        restored.state.scenario_runner.snapshot["steps"]["solution"]["output"]["answer"]
        == "4"
    )


async def test_stopped_draft_resumes_after_restart_without_regenerating_completed_steps(
    compiler, tmp_path
):
    fake = ScenarioClient()
    fake.hold = "Validate ONLY message framing"
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        assert (
            await client.post("/api/scenarios/run", json={"scenario": "user_problem"})
        ).status_code == 200
        await asyncio.wait_for(fake.entered.wait(), 5)
        state = (await client.get("/api/state")).json()
        assert state["status"]["running"]
        assert state["samples"][0]["validation_level"] == 0
        draft = (await client.get("/api/samples/0")).json()
        assert draft["id"] == state["status"]["draft_id"]
        assert (
            await client.post("/api/compile", json={"sample": draft})
        ).status_code == 200
        assert (await client.post("/api/export", json={})).text == ""
        assert (await client.post("/api/samples/0/approve")).status_code == 409
        assert (await client.post("/api/stop")).status_code == 200
        assert runner.status["phase"] == "cancelled"
        assert len(app.state.dataset["samples"]) == 1
        assert not fake.release.is_set()
        fake.release.set()
        restored = create_app(tmp_path / "project.sqlite", client=fake)
        restored_runner = restored.state.scenario_runner
        restored_runner.resume_saved(draft["id"])
        await finish(restored_runner)
        assert (
            sum(
                messages[0]["content"].startswith("Invent")
                for messages in fake.messages
            )
            == 1
        )
        assert not restored_runner.can_resume(draft["id"])


async def test_pause_finishes_current_call_and_blocks_next_request(compiler, tmp_path):
    fake = ScenarioClient()
    fake.hold = "Invent"
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(fake.entered.wait(), 3)
    runner.pause()
    fake.release.set()
    for _ in range(100):
        if runner.status["paused"]:
            break
        await asyncio.sleep(0.005)
    assert runner.status["paused"]
    assert len(fake.messages) == 1
    runner.resume()
    await finish(runner)


async def test_human_edit_preserves_approval_and_export(compiler, tmp_path):
    app = app_for(tmp_path, compiler, ScenarioClient())
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await finish(runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        assert (await client.post("/api/export", json={})).text == ""
        assert (await client.post("/api/samples/0/approve")).status_code == 200
        assert (await client.post("/api/export", json={})).text
        sample = (await client.get("/api/samples/0")).json()
        for events in sample["replay"].values():
            for event in events:
                if event["op"] == "append" and event.get("source") == "user":
                    event["content"] += "Changed"
        assert (
            await client.put("/api/samples/0", json={"sample": sample})
        ).status_code == 200
        assert (await client.get("/api/samples/0")).json()["metadata"][
            "validation_level"
        ] == 3
        assert (await client.post("/api/export", json={})).text


def write_scenario(tmp_path, flow, prompts=None):
    root = tmp_path / "scenarios" / "nested" / "custom"
    root.mkdir(parents=True)
    (root / "flow.yaml").write_text(yaml.safe_dump(flow))
    for name, text in (prompts or {"prompt.md": "Say something"}).items():
        (root / name).write_text(text)
    return tmp_path / "scenarios"


@pytest.mark.parametrize(
    "change",
    [
        {"needs": ["missing"]},
        {"needs": ["a"]},
        {"inputs": {"leak": "${steps.b.output}"}},
        {"prompt": "../../outside.md"},
        {"output_schema": "Missing"},
    ],
)
def test_invalid_definitions_fail_at_load(tmp_path, change):
    root = write_scenario(
        tmp_path,
        {"steps": {"a": {"uses": "llm.generate", "prompt": "prompt.md", **change}}},
    )
    with pytest.raises((ValueError, OSError)):
        ScenarioRegistry(root)


@pytest.mark.parametrize("limit", [1, 2])
async def test_parallel_calls_are_bounded_and_failure_fallback_is_explicit(
    compiler, tmp_path, limit
):
    root = write_scenario(
        tmp_path,
        {
            "steps": {
                "a": {
                    "uses": "llm.generate",
                    "prompt": "prompt.md",
                    "on_failure": {"action": "fallback", "value": "fallback text"},
                },
                "b": {"uses": "llm.generate", "prompt": "prompt.md"},
                "message": {
                    "uses": "remix.message",
                    "needs": ["a", "b"],
                    "inputs": {"text": "${steps.a.output}"},
                },
                "draft": {"uses": "remix.draft", "needs": ["message"]},
            }
        },
    )

    class Client:
        active = 0
        maximum = 0
        calls = 0

        async def complete(self, *args):
            self.calls += 1
            number = self.calls
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            if number == 1:
                raise ValueError("bad output")
            return {"content": "text"}

    fake = Client()
    app = app_for(tmp_path, compiler, fake, root)
    runner = app.state.scenario_runner
    runner.start("custom", limit)
    await finish(runner)
    assert fake.maximum == limit
    assert runner.snapshot["steps"]["a"]["state"] == "fallback"


async def test_rejected_validation_keeps_negative_level(compiler, tmp_path):
    # Disable repairs to exercise rejection independently of a repair model.
    root = tmp_path / "scenarios"
    import shutil

    shutil.copytree(EXAMPLES, root)
    path = root / "user_problem/flow.yaml"
    flow = yaml.safe_load(path.read_text())
    flow["steps"]["validate"]["max_repairs"] = 0
    flow["steps"]["validate"]["level"] = 2
    path.write_text(yaml.safe_dump(flow))
    app = app_for(tmp_path, compiler, ScenarioClient(valid=False), root)
    app.state.dataset["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 5)
    assert runner.status["phase"] == "rejected"
    assert app.state.dataset["samples"][0]["metadata"]["validation_level"] == -2


def test_references_do_not_evaluate_code():
    assert resolve("${inputs.value}", {"inputs": {"value": 3}}) == 3
    assert resolve("hello ${inputs.value}", {}) == "hello ${inputs.value}"
    assert not training_eligible({"metadata": {}})
    assert not training_eligible({"metadata": {"origin": "automation"}})


async def test_pydantic_ai_streaming_uses_prompted_json_without_tool_permissions(
    compiler, tmp_path, monkeypatch
):
    from pydantic_ai.providers.openai import OpenAIProvider

    import trajectory_remix.scenario_runner as runtime

    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["stream"] is True
        assert "tools" not in payload
        chunks = [
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "local-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": '{"text":"Hello"}'},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "local-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 5,
                    "total_tokens": 55,
                    "prompt_tokens_details": {"cached_tokens": 12},
                    "completion_tokens_details": {"reasoning_tokens": 1},
                },
            },
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as mock_http:
        monkeypatch.setattr(
            runtime,
            "OpenAIProvider",
            lambda **kwargs: OpenAIProvider(
                base_url="http://test/v1", api_key="local", http_client=mock_http
            ),
        )
        root = write_scenario(
            tmp_path,
            {
                "schemas": {
                    "Message": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    }
                },
                "steps": {
                    "text": {
                        "uses": "llm.generate",
                        "prompt": "prompt.md",
                        "output_schema": "Message",
                    },
                    "message": {
                        "uses": "remix.message",
                        "needs": ["text"],
                        "inputs": {"text": "${steps.text.output.text}"},
                    },
                    "draft": {"uses": "remix.draft", "needs": ["message"]},
                },
            },
        )
        app = app_for(tmp_path, compiler, None, root)
        runner = app.state.scenario_runner
        runner.start("custom")
        await finish(runner)
    assert len(requests) == 1
    usage = runner.snapshot["steps"]["text"]["metrics"]
    assert usage["prompt_tokens"] == 50
    assert usage["completion_tokens"] == 5
    assert usage["cache_read_tokens"] == 12
    assert usage["reasoning_tokens"] == 1
    assert runner.snapshot["steps"]["text"]["output"] == {"text": "Hello"}
    assert any(
        log["level"] == "output" and "Hello" in log["message"] for log in runner.logs
    )


async def test_validation_repairs_are_visible_and_revalidated(compiler, tmp_path):
    class RepairClient(ScenarioClient):
        repaired = False

        async def complete(self, endpoint, messages, tools=None):
            prompt = messages[0]["content"]
            if prompt.startswith("Repair ONLY"):
                self.repaired = True
                data = json.loads(
                    messages[1]["content"].split("\nOutput JSON schema:")[0]
                )
                assert data["findings"] == [
                    f"[{name}] Incorrect answer"
                    for name in ("layout", "tools", "reasoning", "correctness")
                ]
                return {"content": json.dumps({"replay": data["trajectory"]["replay"]})}
            self.valid = self.repaired
            return await super().complete(endpoint, messages, tools)

    app = app_for(tmp_path, compiler, RepairClient())
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await finish(runner)
    metadata = app.state.dataset["samples"][0]["metadata"]
    assert metadata["validation_level"] == 1
    assert [item["report"]["valid"] for item in metadata["evaluations"]] == [
        False,
        True,
    ]
    assert "validate.repair" in runner.snapshot["steps"]
    # Even an unchanged repair is reviewed afresh by every focused check.
    reviews = [
        m for m in runner.client.messages if m[0]["content"].startswith("Validate")
    ]
    assert len(reviews) == 8


async def test_transport_failure_does_not_reject_pending_draft(compiler, tmp_path):
    class Unavailable(ScenarioClient):
        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Validate"):
                raise httpx.ConnectError("unavailable")
            return await super().complete(endpoint, messages, tools)

    app = app_for(tmp_path, compiler, Unavailable())
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 5)
    assert runner.status["phase"] == "api_paused"
    assert app.state.dataset["samples"][0]["metadata"]["validation_level"] == 0


async def test_novelty_checks_human_samples_and_retains_best_fallback(
    compiler, tmp_path
):
    class Similar(ScenarioClient):
        comparisons = 0

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Judge"):
                self.comparisons += 1
                assert "existing user task" in messages[1]["content"]
                return {
                    "content": json.dumps(
                        {"similarity": 0.9, "feedback": "Use another approach"}
                    )
                }
            return await super().complete(endpoint, messages, tools)

    fake = Similar()
    app = app_for(tmp_path, compiler, fake)
    from trajectory_remix.dataset import new_sample

    sample = new_sample()
    for events in sample["replay"].values():
        for event in events:
            if event.get("source") == "user":
                event["content"] = "existing user task"
    app.state.dataset.store.replace([sample], 0)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await finish(runner)
    assert fake.comparisons == 3
    assert app.state.dataset["samples"][1]["metadata"]["novelty_relaxed"]


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    root = write_scenario(tmp_path, {"steps": {}})
    (root / "nested/custom/flow.yaml").write_text("steps: {}\nsteps: {}\n")
    with pytest.raises(ValueError, match="Duplicate"):
        ScenarioRegistry(root)


async def test_validation_failure_can_take_yaml_fallback_without_promoting_sample(
    compiler, tmp_path
):
    import shutil

    root = tmp_path / "scenarios"
    shutil.copytree(EXAMPLES, root)
    path = root / "user_problem/flow.yaml"
    flow = yaml.safe_load(path.read_text())
    flow["steps"]["validate"].update(
        max_repairs=0,
        on_failure={
            "action": "fallback",
            "value": {"valid": False, "feedback": ["Retain for human review"]},
        },
    )
    path.write_text(yaml.safe_dump(flow))
    app = app_for(tmp_path, compiler, ScenarioClient(valid=False), root)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await finish(runner)
    assert runner.snapshot["steps"]["validate"]["state"] == "fallback"
    assert app.state.dataset["samples"][0]["metadata"]["validation_level"] == -1


async def test_failed_attempt_retries_and_manual_starts_preserve_log_and_counter(
    compiler, tmp_path
):
    class Flaky(ScenarioClient):
        failures = 2

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Invent") and self.failures:
                self.failures -= 1
                return {"content": "malformed output for debugging"}
            result = await super().complete(endpoint, messages, tools)
            result["content"] = (
                "<think>private model reasoning</think>" + result["content"]
            )
            return result

    app = app_for(tmp_path, compiler, Flaky())
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await finish(runner)
    assert runner.status["attempts"] == 2
    assert runner.status["accepted"] == 1
    # The first attempt's two malformed requests are not part of this sample.
    assert runner.status["model_requests"] == len(runner.client.messages)
    assert any("malformed output" in log["message"] for log in runner.logs)
    assert not any("private model reasoning" in log["message"] for log in runner.logs)
    assert runner.status["conditions"]["scenario"] == runner.context["scenario"]["tags"]
    previous = list(runner.logs)
    previous_requests = len(runner.client.messages)
    runner.status["completed_model_seconds"] = 10000
    runner.status["total_model_seconds"] = 10000
    runner.start("user_problem")
    assert runner.status["accepted"] == 1
    assert runner.status["model_requests"] == 0
    assert runner.status["completed_model_seconds"] == 0
    assert runner.status["total_model_seconds"] == 0
    await finish(runner)
    assert runner.status["attempts"] == 3
    assert runner.status["accepted"] == 2
    assert (
        runner.status["model_requests"]
        == len(runner.client.messages) - previous_requests
    )
    assert 0 < runner.status["total_model_seconds"] < 10000
    assert list(runner.logs)[: len(previous)] == previous


async def test_manual_tags_are_validated_and_fixed_across_retries(compiler, tmp_path):
    class Retry(ScenarioClient):
        failures = 2

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Invent") and self.failures:
                self.failures -= 1
                return {"content": "invalid"}
            return await super().complete(endpoint, messages, tools)

    app = app_for(tmp_path, compiler, Retry())
    runner = app.state.scenario_runner
    flow = runner.registry.scenarios["user_problem"].flow
    pools = {
        "scenario": flow.tags,
        **{name: role.tags for name, role in flow.roles.items()},
    }
    tags = {
        scope: {
            dimension: options[-1].name for dimension, options in dimensions.items()
        }
        for scope, dimensions in pools.items()
    }
    runner.start("user_problem", tags=tags)
    await finish(runner)
    assert runner.status["attempts"] == 2
    assert {
        scope: {dimension: tag["name"] for dimension, tag in dimensions.items()}
        for scope, dimensions in runner.status["conditions"].items()
    } == tags
    bad = copy.deepcopy(tags)
    bad["scenario"]["domain"] = "not in YAML"
    with pytest.raises(ValueError, match="Unknown tag"):
        runner.start("user_problem", tags=bad)


async def test_retry_feedback_and_virtual_scenario_files(compiler, tmp_path):
    class Client(ScenarioClient):
        malformed = True

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Plan ONLY"):
                data = json.loads(messages[1]["content"])
                if self.malformed:
                    self.malformed = False
                    return {"content": '[["file_read"]]'}
                assert "not of type" in data["retry_feedback"]["error"]
                assert data["retry_feedback"]["previous_output"] == '[["file_read"]]'
                return {
                    "content": '{"calls":[[{"tool":"file_read","arguments":{"path":"notes.txt"}}]]}'
                }
            value = await super().complete(endpoint, messages, tools)
            if messages[0]["content"].startswith("Invent"):
                idea = json.loads(value["content"])
                idea["files"] = [
                    {"path": "notes.txt", "content": "Authoritative scenario facts"}
                ]
                value["content"] = json.dumps(idea)
            return value

    fake = Client()
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await finish(runner)
    reasoning = next(
        messages
        for messages in fake.messages
        if messages[0]["content"].startswith("Write Kidra")
    )
    assert "Authoritative scenario facts" in reasoning[1]["content"]
    assert runner.status["attempts"] == 1
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        assert (await client.post("/api/scenarios/log/clear")).status_code == 200
    assert not runner.logs
    assert runner.snapshot["logs"] == []
    assert runner.status["attempts"] == 1


async def test_unrelated_samples_edit_validate_and_state_changes_during_generation(
    compiler, tmp_path
):
    fake = ScenarioClient()
    fake.hold = "Invent"
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(fake.entered.wait(), 3)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        assert (await client.post("/api/samples", json={})).status_code == 200
        unrelated = (await client.get("/api/samples/1")).json()
        unrelated["metadata"]["title"] = "Editable"
        assert (
            await client.put("/api/samples/1", json={"sample": unrelated})
        ).status_code == 200
        assert (
            await app.state.commands.call(
                "validate_trajectory", {"sample_id": unrelated["id"]}
            )
        )["valid"]
        await app.state.commands.call(
            "update_trajectory_metadata",
            {
                "sample_id": unrelated["id"],
                "patch": {"metadata": {"title": "Agent edited"}},
            },
        )
        await app.state.commands.call("undo_edits", {})
        await app.state.commands.call("redo_edits", {})
        active = (await client.get("/api/samples/0")).json()
        assert (
            await client.put("/api/samples/0", json={"sample": active})
        ).status_code == 409
        for level in range(-3, 4):
            assert (
                await client.post("/api/samples/0/state", json={"level": level})
            ).status_code == 200
        assert (
            await client.post("/api/preferences", json={"api_retries": 4})
        ).status_code == 200
    fake.release.set()
    await finish(runner)
    assert app.state.dataset["samples"][0]["metadata"]["validation_level"] == 3
    assert app.state.dataset["config"]["single_generation"]["api_retries"] == 4


@pytest.mark.parametrize("failure", ["connection", "model_api", "model_http"])
@pytest.mark.parametrize("stage", ["idea", "validate"])
async def test_api_retry_budget_pauses_same_attempt_and_can_resume(
    compiler, tmp_path, failure, stage
):
    from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

    class APIClient(ScenarioClient):
        broken = True
        failures = 0

        async def complete(self, endpoint, messages, tools=None):
            fails_here = (
                messages[0]["content"].startswith("Validate")
                if stage == "validate"
                else messages[0]["content"].startswith("Invent")
            )
            if self.broken and fails_here:
                self.failures += 1
                if failure == "model_api":
                    raise ModelAPIError("local-model", "Service disconnected")
                if failure == "model_http":
                    raise ModelHTTPError(503, "local-model")
                raise httpx.ConnectError("offline")
            return await super().complete(endpoint, messages, tools)

    fake = APIClient()
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 5)
    assert fake.failures == 3  # initial request plus two API retries
    assert runner.status["phase"] == "api_paused"
    assert runner.status["paused"]
    assert not any(
        "retrying with freshly sampled" in log["message"] for log in runner.logs
    )
    assert runner.status["attempts"] == 1
    assert app.state.dataset["samples"][0]["metadata"]["validation_level"] == 0
    previous_requests = runner.status["model_requests"]
    previous_seconds = runner.status["total_model_seconds"]
    fake.broken = False
    runner.resume_saved(runner.status["draft_id"])
    await finish(runner)
    assert runner.status["attempts"] == 1
    assert runner.status["total_model_seconds"] > 0
    assert runner.status["model_requests"] > previous_requests
    assert runner.status["total_model_seconds"] >= previous_seconds
    assert runner.status["accepted"] == 1


async def test_resume_inside_conversation_does_not_duplicate_messages_or_tool_calls(
    compiler, tmp_path
):
    fake = ScenarioClient(tools=True)
    fake.hold = "Write Kidra's next response"
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(fake.entered.wait(), 5)
    ident = runner.status["draft_id"]
    await runner.stop()
    fake.release.set()
    restored = create_app(tmp_path / "project.sqlite", client=fake)
    resumed = restored.state.scenario_runner
    resumed.resume_saved(ident)
    await finish(resumed)
    assert (
        sum(
            messages[0]["content"].startswith("Plan ONLY") for messages in fake.messages
        )
        == 1
    )
    assert (
        sum(
            messages[0]["content"].startswith("Write the next fictional")
            for messages in fake.messages
        )
        == 1
    )
    plan = compiler.compile(restored.state.dataset["samples"][0])
    assert [call["tool"] for call in plan["tool_calls"]] == ["calculator", "chat"]


async def test_idea_identifier_allocates_free_suffix(compiler, tmp_path):
    app = app_for(tmp_path, compiler, ScenarioClient())
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await finish(runner)
    assert runner.status["draft_id"] == "python-math-example-1"
    runner.start("user_problem")
    await finish(runner)
    assert runner.status["draft_id"] == "python-math-example-2"
    assert (
        app.state.dataset["samples"][0]["uuid"]
        != app.state.dataset["samples"][1]["uuid"]
    )


async def test_edited_checkpoint_requires_explicit_confirmation(compiler, tmp_path):
    fake = ScenarioClient()
    fake.hold = "Invent"
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(fake.entered.wait(), 3)
    await runner.stop()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        sample = (await client.get("/api/samples/0")).json()
        sample["metadata"]["title"] = "Edited after stopping"
        sample["id"] = "renamed-after-stopping"
        assert (
            await client.put("/api/samples/0", json={"sample": sample})
        ).status_code == 200
        response = await client.post(
            "/api/scenarios/resume", json={"sample_id": sample["id"]}
        )
        assert response.status_code == 409
        assert response.json()["confirmation_required"]
        fake.release.set()
        assert (
            await client.post(
                "/api/scenarios/resume",
                json={"sample_id": sample["id"], "confirm_edited": True},
            )
        ).status_code == 200
        await finish(runner)


async def test_api_diagnostics_are_console_only_and_redact_credentials(
    compiler, tmp_path, caplog
):
    from pydantic_ai.exceptions import ModelAPIError

    secret = "test-private-credential"

    class Broken:
        async def complete(self, *args):
            raise ModelAPIError("local-model", f"Connection dropped: {secret}")

    app = app_for(tmp_path, compiler, Broken())
    app.state.dataset["config"]["endpoints"][0]["api_key"] = secret
    app.state.dataset["config"].setdefault("single_generation", {})["api_retries"] = 0
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 5)
    assert "Connection dropped" in caplog.text
    assert secret not in caplog.text
    assert "[redacted]" in caplog.text
    assert not any("Connection dropped" in line["message"] for line in runner.logs)


async def test_settings_missing_endpoint_names_scenario_and_step(compiler, tmp_path):
    app = app_for(tmp_path, compiler, ScenarioClient())
    config = copy.deepcopy(app.state.dataset["config"])
    config["endpoints"] = [ep for ep in config["endpoints"] if ep["id"] != "validator"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        response = await client.put("/api/config", json={"config": config})
    assert response.status_code == 422
    assert (
        "Scenario 'user_problem' requires endpoint 'validator'"
        in response.json()["detail"]
    )
    assert "step 'validate'" in response.json()["detail"]
    assert len(app.state.dataset["config"]["endpoints"]) == 2


async def test_validation_connection_failure_identifies_endpoint_url_and_model(
    compiler, tmp_path
):
    class Offline(ScenarioClient):
        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Validate"):
                raise httpx.ConnectError("offline")
            return await super().complete(endpoint, messages, tools)

    app = app_for(tmp_path, compiler, Offline())
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 5)
    message = runner.status["last_error"]
    assert "Endpoint 'validator'" in message
    assert "http://127.0.0.1:8080/v1" in message
    assert "local-model" in message
    assert any("Endpoint 'validator'" in log["message"] for log in runner.logs)


async def test_one_failed_focused_check_blocks_promotion(compiler, tmp_path):
    import shutil

    class FocusedClient(ScenarioClient):
        def __init__(self):
            super().__init__()
            self.reviewed = []

        async def complete(self, endpoint, messages, tools=None):
            prompt = messages[0]["content"]
            if prompt.startswith("Validate"):
                data = json.loads(
                    messages[1]["content"].split("\nOutput JSON schema:")[0]
                )
                assert {
                    "events",
                    "windows",
                    "anchors",
                    "tool_calls",
                    "diagnostics",
                } <= data["compiled"].keys()
                assert "evaluations" not in data["trajectory"]["metadata"]
                assert "validation_level" not in data["trajectory"]["metadata"]
                assert "files" in data["idea"]
                self.reviewed.append(prompt)
                valid = not prompt.startswith("Validate ONLY factual correctness")
                return {
                    "content": json.dumps(
                        {
                            "valid": valid,
                            "feedback": [] if valid else ["Schedule is not unique"],
                        }
                    )
                }
            return await super().complete(endpoint, messages, tools)

    root = tmp_path / "scenarios"
    shutil.copytree(EXAMPLES, root)
    path = root / "user_problem/flow.yaml"
    flow = yaml.safe_load(path.read_text())
    flow["steps"]["validate"]["max_repairs"] = 0
    path.write_text(yaml.safe_dump(flow, sort_keys=False))
    fake = FocusedClient()
    app = app_for(tmp_path, compiler, fake, root)
    app.state.dataset["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 5)
    assert len(fake.reviewed) == 4
    sample = app.state.dataset["samples"][0]
    assert sample["metadata"]["validation_level"] == -1
    report = sample["metadata"]["evaluations"][-1]["report"]
    assert not report["valid"]
    assert report["feedback"] == ["[correctness] Schedule is not unique"]
    assert report["checks"]["layout"]["valid"]
    assert not report["checks"]["correctness"]["valid"]


async def test_focused_reviews_resume_without_repeating_completed_checks(
    compiler, tmp_path
):
    fake = ScenarioClient()
    fake.hold = "Validate ONLY reasoning quality"
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(fake.entered.wait(), 5)
    draft_id = runner.status["draft_id"]
    assert "report" in runner.snapshot["steps"]["validate.checks.layout"]
    await runner.stop()
    restored = create_app(tmp_path / "project.sqlite", client=fake)
    fake.hold = None
    restored_runner = restored.state.scenario_runner
    restored_runner.resume_saved(draft_id)
    await finish(restored_runner)
    reviews = [
        m[0]["content"] for m in fake.messages if m[0]["content"].startswith("Validate")
    ]
    assert sum(p.startswith("Validate ONLY message framing") for p in reviews) == 1
    assert sum(p.startswith("Validate ONLY tool use") for p in reviews) == 1
    assert sum(p.startswith("Validate ONLY reasoning quality") for p in reviews) == 2
    assert sum(p.startswith("Validate ONLY factual correctness") for p in reviews) == 1


@pytest.mark.parametrize(
    "checks",
    [
        {"bad.name": "review.md"},
        {"review": "../../outside.md"},
        {"review": "missing.md"},
        {"review": ""},
    ],
)
def test_focused_check_definitions_are_validated(tmp_path, checks):
    root = write_scenario(
        tmp_path, {"steps": {"review": {"uses": "remix.validate", "checks": checks}}}
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        ScenarioRegistry(root)


async def test_single_prompt_validation_remains_supported(compiler, tmp_path):
    root = write_scenario(
        tmp_path,
        {
            "steps": {
                "review": {
                    "uses": "remix.validate",
                    "prompt": "review.md",
                    "endpoint": "validator",
                }
            }
        },
        {"review.md": "Validate this trajectory."},
    )
    fake = ScenarioClient()
    app = app_for(tmp_path, compiler, fake, root)
    runner = app.state.scenario_runner
    runner.start("custom")
    await finish(runner)
    report = app.state.dataset["samples"][0]["metadata"]["evaluations"][0]["report"]
    assert report == {"valid": True, "feedback": []}


@pytest.mark.parametrize(
    "kind",
    [
        "success",
        "direct",
        "missing",
        "invisible",
        "empty",
        "failed",
        "cancelled",
        "later_user",
    ],
)
def test_chat_delivery_requires_a_successful_visible_reply(
    compiler, tmp_path, sample, kind
):
    sample["replay"]["0"].extend(
        [
            {"op": "open", "window": "chat", "window-type": "chat"},
            {"op": "show_tools", "window": "a", "tools": ["chat"]},
        ]
    )
    sample["replay"]["1"] = [
        {
            "op": "append",
            "window": "chat",
            "source": "user",
            "content": "Hello",
        }
    ]
    if kind == "direct":
        sample["replay"]["2"] = [
            {"op": "generate", "window": "chat", "content": "Kidra: Hello!"}
        ]
    elif kind != "missing":
        sample["replay"]["2"] = [
            {
                "op": "invisible-tool-call" if kind == "invisible" else "tool-call",
                "window": "send",
                "tool": "chat",
                "arguments": {"content": " " if kind == "empty" else "Hello!"},
                "content": "x",
            }
        ]
    if kind == "failed":
        sample["replay"]["0"].append(
            {
                "op": "invisible-tool-call",
                "tool": "tool_permissions_set",
                "arguments": {"default": "deny"},
            }
        )
    if kind == "invisible":
        sample["replay"]["2"][0].pop("window")
        sample["replay"]["2"][0].pop("content")
    if kind == "cancelled":
        sample["replay"]["2"][0]["content"] = "x" + compiler.special["eos"]
    plan = compiler.compile(sample)
    if kind == "later_user":
        sample["replay"][str(plan["max_tick"] + 1)] = [
            {
                "op": "append",
                "window": "chat",
                "source": "user",
                "content": "One more question?",
            }
        ]
        plan = compiler.compile(sample)
    runner = app_for(tmp_path, compiler, ScenarioClient()).state.scenario_runner
    report = runner.chat_delivery_report(sample, plan)
    assert report["valid"] == (kind == "success")
    if kind == "direct":
        assert any("directly into chat" in finding for finding in report["feedback"])


async def test_missing_chat_delivery_overrides_a_positive_model_verdict(
    compiler, tmp_path
):
    root = write_scenario(
        tmp_path,
        {
            "steps": {
                "user": {"uses": "remix.message", "inputs": {"text": "Hello"}},
                "draft": {
                    "uses": "remix.draft",
                    "needs": ["user"],
                    "inputs": {"reasoning": "I should greet the user."},
                },
                "review": {
                    "uses": "remix.validate",
                    "needs": ["draft"],
                    "prompt": "review.md",
                    "endpoint": "validator",
                    "inputs": {"require_chat_delivery": True},
                },
            }
        },
        {"review.md": "Validate the trajectory."},
    )
    app = app_for(tmp_path, compiler, ScenarioClient(), root)
    app.state.dataset["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner = app.state.scenario_runner
    runner.start("custom")
    await asyncio.wait_for(runner.job, 5)
    assert runner.status["phase"] == "rejected"
    sample = app.state.dataset["samples"][0]
    assert sample["metadata"]["validation_level"] == -1
    report = sample["metadata"]["evaluations"][-1]["report"]
    assert not report["valid"]
    assert not report["delivery"]["valid"]
    assert any("[delivery]" in finding for finding in report["feedback"])


async def test_repair_cannot_replace_chat_delivery_with_direct_generation(
    compiler, tmp_path
):
    import shutil

    class BadRepairClient(ScenarioClient):
        repaired = False

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Repair ONLY"):
                data = json.loads(
                    messages[1]["content"].split("\nOutput JSON schema:")[0]
                )
                replay = copy.deepcopy(data["trajectory"]["replay"])
                for events in list(replay.values()):
                    for index, event in enumerate(events):
                        if event["op"] == "tool-call" and event["tool"] == "chat":
                            replay["0"].append(
                                {
                                    "op": "open",
                                    "window": event["window"],
                                    "window-type": "tool",
                                }
                            )
                            events[index] = {
                                "op": "generate",
                                "window": "chat",
                                "content": "Kidra: Hello!",
                            }
                self.repaired = True
                return {"content": json.dumps({"replay": replay})}
            self.valid = self.repaired
            return await super().complete(endpoint, messages, tools)

    root = tmp_path / "scenarios"
    shutil.copytree(EXAMPLES, root)
    path = root / "user_problem/flow.yaml"
    flow = yaml.safe_load(path.read_text())
    flow["steps"]["validate"]["max_repairs"] = 1
    path.write_text(yaml.safe_dump(flow, sort_keys=False))
    app = app_for(tmp_path, compiler, BadRepairClient(), root)
    app.state.dataset["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 5)
    assert runner.status["phase"] == "rejected"
    sample = app.state.dataset["samples"][0]
    evaluations = sample["metadata"]["evaluations"]
    assert evaluations[0]["report"]["delivery"]["valid"]
    report = evaluations[-1]["report"]
    assert all(check["valid"] for check in report["checks"].values())
    assert not report["valid"] and not report["delivery"]["valid"]
    assert any("directly into chat" in finding for finding in report["feedback"])


async def test_resuming_older_attempt_keeps_cumulative_counts_and_its_own_metrics(
    compiler, tmp_path
):
    fake = ScenarioClient()
    fake.hold = "Validate ONLY reasoning quality"
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(fake.entered.wait(), 5)
    old_id = runner.status["draft_id"]
    await runner.stop()
    old_requests = runner.status["model_requests"]
    old_seconds = runner.status["total_model_seconds"]
    fake.hold = None
    runner.start("user_problem")
    await finish(runner)
    assert runner.status["accepted"] == 1
    assert runner.status["attempts"] == 2
    restored = create_app(tmp_path / "project.sqlite", client=fake)
    resumed = restored.state.scenario_runner
    resumed.resume_saved(old_id)
    assert resumed.status["accepted"] == 1
    assert resumed.status["attempts"] == 2
    assert resumed.status["model_requests"] == old_requests
    assert resumed.status["total_model_seconds"] == old_seconds
    await finish(resumed)
    assert resumed.status["accepted"] == 2
    assert resumed.status["attempts"] == 2
    assert resumed.status["model_requests"] > old_requests


@pytest.mark.parametrize("frontier_passes", [True, False])
async def test_accepted_counts_completed_samples_once_not_validation_stages(
    compiler, tmp_path, frontier_passes
):
    import shutil

    class FrontierClient(ScenarioClient):
        async def complete(self, endpoint, messages, tools=None):
            if (
                messages[0]["content"].startswith("Validate")
                and json.loads(messages[1]["content"]).get("frontier") is True
            ):
                self.entered.set()
                await self.release.wait()
                return {
                    "content": json.dumps(
                        {
                            "valid": frontier_passes,
                            "feedback": [] if frontier_passes else ["Incorrect"],
                        }
                    )
                }
            return await super().complete(endpoint, messages, tools)

    root = tmp_path / "scenarios"
    shutil.copytree(EXAMPLES, root)
    path = root / "user_problem/flow.yaml"
    flow = yaml.safe_load(path.read_text())
    frontier = copy.deepcopy(flow["steps"]["validate"])
    frontier.update(needs=["validate"], level=2, max_repairs=0)
    frontier["inputs"]["frontier"] = True
    flow["steps"]["frontier"] = frontier
    path.write_text(yaml.safe_dump(flow, sort_keys=False))
    fake = FrontierClient()
    app = app_for(tmp_path, compiler, fake, root)
    app.state.dataset["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(fake.entered.wait(), 5)
    assert runner.status["accepted"] == 0
    fake.release.set()
    await asyncio.wait_for(runner.job, 5)
    assert runner.status["phase"] == ("complete" if frontier_passes else "rejected")
    assert runner.status["accepted"] == int(frontier_passes)


async def test_bundled_bounded_reviews_use_small_projected_prompts(compiler, tmp_path):
    class BoundedClient(ScenarioClient):
        reviews = 0

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Validate"):
                self.reviews += 1
                data = json.loads(messages[1]["content"])
                assert "trajectory" not in data and "compiled" not in data
                assert "events" in data and "boundary" in data
                assert (
                    sum(
                        len(compiler.tokenizer.encode(m["content"]).ids)
                        for m in messages
                    )
                    + 128
                    <= 6000
                )
                assert "evaluations" not in data
            return await super().complete(endpoint, messages, tools)

    fake = BoundedClient()
    runner = app_for(
        tmp_path, compiler, fake, legacy_reviews=False
    ).state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 15)
    assert runner.status["phase"] == "complete", list(runner.logs)
    assert fake.reviews >= 4
    report = runner.builder.sample["metadata"]["evaluations"][-1]["report"]
    assert all(item["groups"] > 0 for item in report["checks"].values())
    assert fake.reviews == sum(item["groups"] for item in report["checks"].values())
    definitions = runner.scenario.flow.steps["validate"].checks
    for name, category in report["checks"].items():
        for group in category["rule_results"]:
            assert set(group["checks"]) == set(definitions[name].rules)
            assert all(item["status"] == "pass" for item in group["checks"].values())


async def test_bounded_review_repairs_only_the_identified_text(compiler, tmp_path):
    class EditClient(ScenarioClient):
        repaired = False

        async def complete(self, endpoint, messages, tools=None):
            prompt = messages[0]["content"]
            if prompt.startswith("Repair ONLY"):
                data = json.loads(messages[1]["content"])
                assert "trajectory" not in data and "authoring_guide" not in data
                assert len(data["authoring_notes"]) < 1500
                unit = next(
                    u
                    for u in data["review"]["events"]
                    if u.get("data", {}).get("content")
                    == "Adding two pairs yields four."
                )
                self.repaired = True
                return {
                    "content": json.dumps(
                        {
                            "edits": [
                                {
                                    "action": "replace_text",
                                    "event_id": unit["event_id"],
                                    "field": "/content",
                                    "old": "Adding two pairs yields four.",
                                    "new": "I should add the pairs and verify the total.",
                                }
                            ]
                        }
                    )
                }
            if prompt.startswith("Validate"):
                data = json.loads(messages[1]["content"])
                fail = (
                    data["view"] == "reasoning"
                    and not self.repaired
                    and any(
                        u.get("data", {}).get("content")
                        == "Adding two pairs yields four."
                        for u in data["events"]
                    )
                )
                return {
                    "content": json.dumps(
                        review_output(messages, not fail, "Reasoning should be private working notes")
                    )
                }
            return await super().complete(endpoint, messages, tools)

    runner = app_for(
        tmp_path, compiler, EditClient(), legacy_reviews=False
    ).state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 15)
    assert runner.status["phase"] == "complete", list(runner.logs)
    assert "I should add the pairs" in json.dumps(runner.builder.sample["replay"])
    assert [
        call["tool"] for call in compiler.compile(runner.builder.sample)["tool_calls"]
    ] == ["chat"]


@pytest.mark.parametrize("stale_group", [None, "missing", "different"])
async def test_bounded_scan_reuses_only_matching_completed_groups(
    compiler, tmp_path, stale_group
):
    class ScanClient(ScenarioClient):
        scans = 0

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Validate"):
                self.scans += 1
                if self.scans == 2:
                    self.entered.set()
                    await self.release.wait()
            return await super().complete(endpoint, messages, tools)

    fake = ScanClient()
    app = app_for(tmp_path, compiler, fake, legacy_reviews=False)
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(fake.entered.wait(), 10)
    ident = runner.status["draft_id"]
    first = copy.deepcopy(runner.snapshot["steps"]["validate.checks.layout.groups.0"])
    assert first["report"]["valid"]
    await runner.stop()
    if stale_group:
        cached = runner.snapshot["steps"]["validate.checks.layout.groups.0"]
        if stale_group == "missing":
            cached.pop("review_group_hash")
        else:
            cached["review_group_hash"] = "earlier group boundaries"
        runner.persist()
    restored = create_app(tmp_path / "project.sqlite", client=fake)
    resumed = restored.state.scenario_runner
    resumed.resume_saved(ident)
    await asyncio.wait_for(resumed.job, 15)
    assert resumed.status["phase"] == "complete", list(resumed.logs)
    current = resumed.snapshot["steps"]["validate.checks.layout.groups.0"]
    if not stale_group:
        assert current["metrics"] == first["metrics"]
    assert current["messages"] == first["messages"]
    groups = sum(c["groups"] for c in resumed.builder.sample["metadata"]["evaluations"][-1]["report"]["checks"].values())
    assert fake.scans == groups + 1 + bool(stale_group)  # Includes the cancelled call.


async def test_review_context_request_retries_with_evidence(compiler, tmp_path):
    class ContextClient(ScenarioClient):
        requested = False
        received = False

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Validate"):
                data = json.loads(messages[1]["content"])
                ids = {u["id"] for u in data["events"]}
                older = [
                    uid
                    for values in data["boundary"]["history"].values()
                    for uid in values
                    if uid not in ids
                ]
                if older and not self.requested:
                    self.requested = True
                    self.requested_id = older[0]
                    return {
                        "content": json.dumps(
                            review_output(messages, context_requests=[self.requested_id])
                        )
                    }
                if self.requested and self.requested_id in ids:
                    self.received = True
            return await super().complete(endpoint, messages, tools)

    fake = ContextClient()
    runner = app_for(
        tmp_path, compiler, fake, legacy_reviews=False
    ).state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 15)
    assert runner.status["phase"] == "complete", list(runner.logs)
    assert fake.requested and fake.received


async def test_oversized_review_pauses_before_contacting_model(compiler, tmp_path):
    class Forbidden:
        async def complete(self, *args, **kwargs):
            pytest.fail("An over-budget review must not contact the provider")

    root = write_scenario(
        tmp_path,
        {
            "steps": {
                "user": {"uses": "remix.message", "inputs": {"text": "Hi"}},
                "draft": {
                    "uses": "remix.draft",
                    "needs": ["user"],
                    "inputs": {"response": "Hello"},
                },
                "review": {
                    "uses": "remix.validate",
                    "endpoint": "validator",
                    "needs": ["draft"],
                    "checks": {
                        "layout": {
                            "prompt": "review.md",
                            "view": "layout",
                            "max_input_tokens": 512,
                            "content_tokens": 64,
                        },
                    },
                },
            }
        },
        {"review.md": "Validate " + "very detailed instructions " * 100},
    )
    runner = app_for(
        tmp_path, compiler, Forbidden(), root, legacy_reviews=False
    ).state.scenario_runner
    runner.state["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner.start("custom")
    await asyncio.wait_for(runner.job, 5)
    assert runner.status["phase"] == "evaluation_failed"
    assert runner.status["model_requests"] == 0
    assert any("budget exceeded" in log["message"] for log in runner.logs)


async def test_invalid_targeted_repair_is_not_published(compiler, tmp_path):
    class InvalidRepair(ScenarioClient):
        async def complete(self, endpoint, messages, tools=None):
            prompt = messages[0]["content"]
            if prompt.startswith("Repair ONLY"):
                return {
                    "content": json.dumps(
                        {
                            "edits": [
                                {
                                    "action": "append",
                                    "tick": "0",
                                    "event": {
                                        "op": "generate",
                                        "window": "missing",
                                        "content": "x",
                                    },
                                }
                            ]
                        }
                    )
                }
            if prompt.startswith("Validate"):
                data = json.loads(messages[1]["content"])
                valid = data["view"] != "reasoning"
                return {
                    "content": json.dumps(
                        review_output(messages, valid, "Fix private notes")
                    )
                }
            return await super().complete(endpoint, messages, tools)

    app = app_for(tmp_path, compiler, InvalidRepair(), legacy_reviews=False)
    app.state.dataset["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner = app.state.scenario_runner
    runner.start("user_problem")
    await asyncio.wait_for(runner.job, 15)
    assert runner.status["phase"] == "rejected"
    saved = app.state.dataset["samples"][0]
    assert all(
        event.get("window") != "missing"
        for events in saved["replay"].values()
        for event in events
    )
    compiler.compile(saved)


def checklist_scenario(tmp_path, *, repairs=0):
    return write_scenario(tmp_path, {"steps": {"review": {
        "uses": "remix.validate", "endpoint": "validator",
        "checks": {
            "primary": {"prompt": "review.md", "view": "layout", "rules": {
                "grounding": {"requirement": "Claims match observations."},
                "specialists": {"requirement": "Specialists have evidence.", "allow_not_applicable": True},
            }},
            "secondary": {"prompt": "review.md", "view": "reasoning", "rules": {
                "private_notes": {"requirement": "Reasoning is private."},
            }},
        },
        "max_repairs": repairs, "repair_mode": "edits", "repair_prompt": "repair.md",
    }}}, {"review.md": "Validate this checklist.", "repair.md": "Repair ONLY the findings."})


async def test_rule_failure_collects_all_categories_then_rechecks_every_rule(compiler, tmp_path):
    class ChecklistClient:
        reviews = 0
        repaired = False

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Repair"):
                assert self.reviews == 2  # No short circuit after the first failure.
                data = json.loads(messages[1]["content"])
                assert len(data["findings"]) == 1
                assert "[grounding]" in data["findings"][0]
                assert "specialists" not in data["findings"][0]
                self.repaired = True
                return {"content": '{"edits":[]}'}
            self.reviews += 1
            result = review_output(messages)
            if "grounding" in result["checks"]:
                result["checks"]["specialists"].update(
                    status="not_applicable", evidence="No specialist calls in this group"
                )
                if not self.repaired:
                    result["checks"]["grounding"].update(status="fail", feedback="Correct the unsupported claim.")
            return {"content": json.dumps(result)}

    client = ChecklistClient()
    runner = app_for(tmp_path, compiler, client, checklist_scenario(tmp_path, repairs=1), legacy_reviews=False).state.scenario_runner
    runner.start("custom")
    await finish(runner)
    assert client.reviews == 4  # Same candidate still requires a complete new review.
    reports = [e["report"] for e in runner.builder.sample["metadata"]["evaluations"]]
    assert [r["valid"] for r in reports] == [False, True]
    assert len(reports[0]["feedback"]) == 1
    assert reports[0]["checks"]["secondary"]["valid"]
    assert runner.snapshot["flow"]["steps"]["review"]["checks"]["primary"]["rules"]["grounding"]["requirement"] == "Claims match observations."


@pytest.mark.parametrize("failure", ["missing_item", "extra_item", "invalid_applicability", "missing_context_request"])
async def test_malformed_rule_results_never_promote_a_sample(compiler, tmp_path, failure):
    class MalformedClient:
        async def complete(self, endpoint, messages, tools=None):
            result = review_output(messages)
            if failure == "missing_item":
                result["checks"].pop("grounding")
            elif failure == "extra_item":
                result["checks"]["invented"] = result["checks"]["grounding"]
            else:
                result["checks"]["grounding"]["status"] = (
                    "not_applicable" if failure == "invalid_applicability" else "needs_context"
                )
            return {"content": json.dumps(result)}

    runner = app_for(tmp_path, compiler, MalformedClient(), checklist_scenario(tmp_path), legacy_reviews=False).state.scenario_runner
    runner.state["config"].setdefault("single_generation", {})["max_attempts"] = 1
    runner.start("custom")
    await asyncio.wait_for(runner.job, 5)
    assert runner.status["phase"] == "evaluation_failed"
    assert not runner.builder.sample["metadata"].get("evaluations")
    assert runner.status["accepted"] == 0
