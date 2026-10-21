import json

import httpx
import pytest

from trajectory_remix.dataset import new_sample, parse_import
from trajectory_remix.llm import default_config
from trajectory_remix.server import create_app


@pytest.fixture
async def client(compiler, tmp_path):
    app = create_app(compiler, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as c:
        yield c


async def test_crud_compile_frame_export_roundtrip(client):
    r = await client.post("/api/samples", json={})
    assert r.status_code == 200
    s = (await client.get("/api/samples/0")).json()
    p = (await client.post("/api/compile", json={"sample": s})).json()
    assert p["windows"] and p["plan_id"]
    frame = (await client.get(f"/api/frame/{p['plan_id']}/2")).json()
    assert frame["tick"] == 2
    export = await client.post("/api/export", json={"ticks_per_chunk": 16})
    records = [json.loads(s) for s in export.text.splitlines()]
    assert records[0]["format"] == "kidra-token-chunk/1"
    assert records[0]["tokenizer_sha256"] == p["tokenizer_sha256"]
    download = (await client.get("/api/download")).json()
    assert parse_import("file.kidra-dataset", json.dumps(download))[0] == [s]
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


async def test_keys_never_saved_or_read_back(client):
    c = default_config()
    c["endpoints"][0]["api_key"] = "unit-test-secret"
    assert (await client.put("/api/config", json={"config": c})).status_code == 200
    assert "unit-test-secret" not in (await client.get("/api/state")).text
    assert "unit-test-secret" not in (await client.get("/api/download")).text


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
    assert (await client.get("/api/samples/0")).json() == s
