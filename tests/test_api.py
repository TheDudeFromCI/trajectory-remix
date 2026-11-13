from trajectory_remix.storage import DatasetStore
import json

import httpx
import pytest

from trajectory_remix.dataset import new_sample, parse_import
from trajectory_remix.llm import default_config
from trajectory_remix.server import create_app


@pytest.fixture
async def client(compiler, tmp_path):
    DatasetStore.initialize(tmp_path / "project.kidra-dataset", compiler)
    app = create_app(tmp_path / "project.kidra-dataset")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as c:
        yield c


async def test_crud_compile_frame_export_roundtrip(client, tmp_path):
    r = await client.post("/api/samples", json={})
    assert r.status_code == 200
    s = (await client.get("/api/samples/0")).json()
    p = (await client.post("/api/compile", json={"sample": s})).json()
    assert p["windows"] and p["plan_id"]
    frame = (await client.get(f"/api/frame/{p['plan_id']}/2")).json()
    assert frame["tick"] == 2
    assert s["metadata"]["validation_level"] == -3
    assert s["metadata"]["split"] == "train"
    assert (await client.post("/api/export", json={})).text == ""
    await client.post("/api/samples/0/approve")
    s = (await client.get("/api/samples/0")).json()
    export = await client.post("/api/export", json={"ticks_per_chunk": 16})
    records = [json.loads(s) for s in export.text.splitlines()]
    assert records[0]["format"] == "kidra-token-chunk/1"
    assert records[0]["tokenizer_sha256"] == p["tokenizer_sha256"]
    assert (await client.post("/api/checkpoint")).status_code == 200
    assert list(DatasetStore(tmp_path / "project.kidra-dataset").samples) == [s]
    s["id"] = "edited"
    assert (await client.put("/api/samples/0", json={"sample": s})).status_code == 200
    assert (await client.delete("/api/samples/0")).status_code == 200
    assert (await client.get("/api/state")).json()["samples"] == []


async def test_bad_marker_diagnostic(client):
    s = new_sample()
    s["replay"]["1"][0]["content"] = "x{N_TICKS:3}"
    r = await client.post("/api/compile", json={"sample": s})
    assert r.status_code == 422
    assert "Invalid marker" in r.json()["diagnostics"][0]["message"]


@pytest.mark.parametrize("sample", [None, False, 0, "", [], "trajectory"])
async def test_create_rejects_explicit_non_object_samples_without_inserting_demo(client, sample):
    before = (await client.get("/api/state")).json()
    response = await client.post("/api/samples", json={"sample": sample})
    assert response.status_code == 422
    assert response.json()["detail"] == "sample must be an object"
    after = (await client.get("/api/state")).json()
    assert after["revision"] == before["revision"]
    assert after["sample_count"] == 0


@pytest.mark.parametrize("index", [None, False, True, 0.0, "0", [], {}])
@pytest.mark.parametrize("route", ["compile", "validate"])
async def test_sample_lookup_rejects_non_integer_indices(client, route, index):
    await client.post("/api/samples", json={})
    response = await client.post(f"/api/{route}", json={"index": index})
    assert response.status_code == 422
    assert response.json()["detail"] == "index must be an integer"


@pytest.mark.parametrize("sample", [None, False, 0, "", [], "trajectory"])
async def test_validate_rejects_non_object_samples(client, sample):
    await client.post("/api/samples", json={})
    response = await client.post("/api/validate", json={"sample": sample, "index": 0})
    assert response.status_code == 422
    assert response.json()["detail"] == "sample must be an object"


@pytest.mark.parametrize("split", ["train", "development", "test"])
async def test_default_split_preferences_apply_to_new_trajectories(client, tmp_path, split):
    response = await client.post("/api/preferences", json={"default_split": split})
    assert response.status_code == 200, response.text
    assert json.loads(DatasetStore(tmp_path / "project.kidra-dataset").info()[3])["default_split"] == split
    await client.post("/api/samples", json={})
    assert (await client.get("/api/samples/0")).json()["metadata"]["split"] == split
    for template in ("empty", "demo"):
        response = await client.post("/api/mcp/call", json={
            "name": "create_trajectory", "arguments": {"sample_id": template, "template": template}
        })
        assert response.status_code == 200, response.text
    for index in (1, 2):
        assert (await client.get(f"/api/samples/{index}")).json()["metadata"]["split"] == split
    imported = new_sample("development")
    await client.post("/api/samples", json={"sample": imported})
    await client.post("/api/preferences", json={"default_split": "train"})
    assert (await client.get("/api/samples/0")).json()["metadata"]["split"] == split
    assert (await client.get("/api/samples/3")).json()["metadata"]["split"] == "development"


@pytest.mark.parametrize("split", [None, "invalid", 1, [], {}])
async def test_invalid_default_split_does_not_save_other_preferences(client, split):
    before = (await client.get("/api/state")).json()["config"]
    response = await client.post("/api/preferences", json={"default_split": split, "api_retries": 7})
    assert response.status_code == 422
    assert (await client.get("/api/state")).json()["config"] == before


async def test_tool_authoring_history_and_atomic_rejection(client):
    await client.post("/api/samples", json={})
    assert any(
        s["function"]["name"] == "file_write"
        for s in (await client.get("/api/tools")).json()["tools"]
    )
    before = (await client.get("/api/samples/0")).json()
    invalid = await client.post(
        "/api/samples/0/tool-calls",
        json={
            "tick": "bad",
            "tool": "file_list",
            "arguments": {},
        },
    )
    assert invalid.status_code == 422
    assert (await client.get("/api/samples/0")).json() == before
    response = await client.post(
        "/api/samples/0/tool-calls",
        json={
            "tick": 150,
            "tool": "file_write",
            "arguments": {"path": "x", "content": "new"},
            "duration_ticks": 50,
            "window": "new-result",
        },
    )
    assert response.status_code == 200, response.text
    call = response.json()["tool_call"]
    assert call["start"] > call["request_start"] == 150
    assert call["end"] == call["start"] + 50
    old = (await client.get(f"/api/samples/0/sandbox/{call['start']}")).json()
    assert (
        old["sandbox"] == {"tick_rate": 10}
        and old["tool_calls"][-1]["status"] == "running"
    )
    assert (await client.get(f"/api/samples/0/sandbox/{call['end']}")).json()[
        "sandbox"
    ]["files"] == {"/x": "new"}
    assert (
        await client.get(f"/api/samples/0/sandbox/{call['end'] + 1}")
    ).status_code == 422
    assert (await client.get("/api/samples/1/sandbox/0")).status_code == 404


async def test_import_is_transactional(client):
    s = new_sample()
    r = await client.post(
        "/api/import",
        json={
            "files": [
                {"name": "good.json", "text": json.dumps(s)},
                {"name": "bad.jsonl", "text": "{bad"},
            ]
        },
    )
    assert r.status_code == 422
    assert (await client.get("/api/state")).json()["samples"] == []
    r = await client.post(
        "/api/import",
        json={"files": [{"name": "good.jsonl", "text": json.dumps(s) + "\n"}]},
    )
    assert r.status_code == 200
    r = await client.post(
        "/api/import",
        json={"files": [{"name": "duplicate.json", "text": json.dumps(s)}]},
    )
    assert r.status_code == 422 and "Duplicate" in r.json()["detail"]


async def test_keys_never_saved_or_read_back(client, tmp_path):
    c = default_config()
    c["endpoints"][0]["api_key"] = "unit-test-secret"
    assert (await client.put("/api/config", json={"config": c})).status_code == 200
    assert "unit-test-secret" not in (await client.get("/api/state")).text
    assert (await client.post("/api/checkpoint")).status_code == 200
    assert b"unit-test-secret" not in (tmp_path / "project.kidra-dataset").read_bytes()


async def test_session_guard_and_origin(client):
    assert (
        await client.get("/api/state", headers={"X-Remix-Token": ""})
    ).status_code == 403
    assert (
        await client.post(
            "/api/samples", json={}, headers={"Origin": "https://hostile.example"}
        )
    ).status_code == 403
    assert (
        await client.get("/api/state", headers={"Host": "hostile.example"})
    ).status_code == 400


async def test_export_is_all_or_nothing(client):
    bad = new_sample()
    bad["metadata"]["validation_level"] = 3
    bad["replay"]["1"][0]["window"] = "missing"
    await client.post("/api/samples", json={"sample": bad})
    r = await client.post("/api/export", json={"ticks_per_chunk": 4})
    assert r.status_code == 422 and "unopened" in r.text


@pytest.mark.parametrize(
    "name,text", [("one.json", "{}"), ("many.json", "[{}]"), ("two.jsonl", "{}\n{}\n")]
)
def test_import_formats(name, text):
    s, _ = parse_import(name, text)
    assert len(s) == (2 if name.endswith("jsonl") else 1)


async def test_malformed_sample_remains_editable(client):
    s = {"id": "broken", "replay": {"x": ["not an event"]}, "metadata": None}
    assert (await client.post("/api/samples", json={"sample": s})).status_code == 200
    assert (await client.get("/api/state")).status_code == 200
    assert (await client.post("/api/compile", json={"sample": s})).status_code == 422
    restored = (await client.get("/api/samples/0")).json()
    assert restored.pop("uuid")
    assert restored == s


async def test_endpoint_probe_uses_unsaved_model_without_saving_settings(
    compiler, tmp_path
):
    class Probe:
        calls = []

        async def complete(self, endpoint, messages, tools=None):
            self.calls.append(endpoint)
            return {"content": "OK"}

    from trajectory_remix.llm import default_config

    fake = Probe()
    path = tmp_path / "probe.sqlite"
    DatasetStore.initialize(path, compiler)
    app = create_app(path, client=fake)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as c:
        endpoint = default_config()["endpoints"][0]
        endpoint["model"] = "unsaved-model"
        result = await c.post("/api/endpoints/test", json={"endpoint": endpoint})
        assert result.status_code == 200
        assert result.json()["model"] == "unsaved-model"
        assert fake.calls[-1]["model"] == "unsaved-model"
        assert app.state.dataset["config"]["endpoints"][0]["model"] == "local-model"
