import asyncio
import copy
import json
import threading

import pytest

from trajectory_remix import metrics
from trajectory_remix.compiler import Compiler, visible_to
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore, SQLiteSamples


def metadata(**extra):
    return {"validation_level": 3, "primary_domain": "software", "terminal_outcome": "completed",
            "scenario_family_id": "scenario-a", "template_family_id": "template-a", "split": "train",
            "is_original": True, "provenance": {"author": "human", "source": "authored", "version": "1"}, **extra}


def label(**extra):
    return {"present": True, "required": True, "demonstrated_correctly": True,
            "confidence": 0.9, "annotator": "reviewer", "version": "1",
            "evidence": ["replay[0][0]"], "justification": "Inspected the replay", "review_status": "reviewed", **extra}


@pytest.fixture
def store(tmp_path, compiler):
    return DatasetStore.initialize(tmp_path / "metrics.sqlite", compiler)


def test_content_concurrency_is_not_open_window_count_and_ratios_are_nullable(store, compiler, sample):
    sample["defaults"] = {"window-headers": False, "implicit-noop-loss-weight": 0}
    plan = compiler.compile(sample)
    result = metrics.measure(plan, compiler)
    assert result["peak_open_windows"] == 2
    assert result["peak_generators"] == 0
    assert result["generated_external_ratio"] is None
    assert result["generated_content_fraction"] is None
    assert result["noop_loss_weight_fraction"] is None
    assert result["supervised_tokens"] == 0
    assert result["window_lengths"] == [1, 1]  # masked BOS, including empty windows
    assert result["concurrency"]["model"][0]["fraction"] == 1

    sample["replay"].update({
        "1": [{"op": "append", "window": "a", "source": "user", "content": "X"}],
        "2": [{"op": "generate", "window": w, "content": "ab<|eos|>"} for w in ("a", "b")],
    })
    result = metrics.measure(compiler.compile(sample), compiler)
    assert result["generated_content_tokens"] == 4
    assert result["external_content_tokens"] == 1
    assert result["bos_tokens"] == result["eos_tokens"] == 2
    assert result["supervised_tokens"] == 6
    assert result["generated_external_ratio"] == 4
    assert result["generated_content_fraction"] == 0.8
    assert result["peak_generators"] == 2
    assert result["peak_external_streams"] == 0  # instant input is not an external stream
    assert result["duration_ticks"] == 5
    assert result["concurrency"]["model"][2]["ticks"] == 2


def test_context_peak_matches_canonical_frontier_after_replacement_and_eos(compiler, sample):
    sample["replay"].update({
        "1": [{"op": "append", "window": "a", "source": "user", "content": "abcdef"}],
        "2": [{"op": "append", "window": "b", "source": "user", "content": "123"}],
        "3": [{"op": "append", "window": "a", "source": "user", "content": "X", "replace-content": True}],
        "4": [{"op": "generate", "window": "a", "content": "x<|eos|>"}],
        "7": [{"op": "generate", "window": "b", "content": "z<|eos|>"}],
    })
    plan = compiler.compile(sample)
    ticks = {r["tick"] for r in plan["rows"] if r["phase"] == "D"}
    expected = max(sum(visible_to({"tick": tick, "phase": "D"}, r, plan["windows"]) for r in plan["rows"]) for tick in ticks)
    assert metrics.measure(plan, compiler)["peak_visible_tokens"] == expected
    # Same-tick phase D output is invisible to the other same-tick decision.
    for row in plan["rows"]:
        if row["phase"] == "D":
            assert not visible_to(row, row, plan["windows"])


def test_summary_distributions_and_queries_never_read_replay_when_cached(store, compiler, sample, monkeypatch):
    for i in range(3):
        record = {**copy.deepcopy(sample), "id": f"sample-{i}", "metadata": metadata(scenario_family_id=f"family-{i}")}
        store.samples.append(record)
    assert metrics.refresh(store, compiler, 2)["processed"] == 2
    assert metrics.overview(store, compiler)["summary"]["pending"] == 1
    assert metrics.refresh(store, compiler, 2)["processed"] == 1

    original_connect = store.connect
    from contextlib import contextmanager
    @contextmanager
    def projected_only():
        with original_connect() as db:
            # SQLite authorizer proves overview/drilldown do not even select source.
            def authorize(action, table, column, *_):
                import sqlite3
                assert not (action == sqlite3.SQLITE_READ and table == "samples" and column == "source")
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            yield db
    monkeypatch.setattr(store, "connect", projected_only)
    monkeypatch.setattr(SQLiteSamples, "__iter__", lambda *_: pytest.fail("No full dataset iteration"))
    monkeypatch.setattr(compiler, "compile", lambda *_args, **_kwargs: pytest.fail("Unexpected recompilation"))
    result = metrics.overview(store, compiler)
    assert result["summary"]["cached"] == 3
    assert result["summary"]["samples_missing_metadata"] == 0
    assert result["summary"]["exact_replay_duplicate_extras"] == 2
    assert metrics.refresh(store, compiler)["processed"] == 0
    page = metrics.query_samples(store, compiler, metric="peak_generators", maximum=0, offset=1, limit=1)
    assert page["total"] == 3 and page["samples"][0]["id"] == "sample-1"
    assert "idle_runs" not in page["samples"][0]["metrics"]  # bounded response
    with metrics.snapshot(store, compiler, "all") as (db, _):
        distribution = metrics.distribution(db, "window_instances", [0, 1, 2, 3])
    assert [b["count"] for b in distribution["bins"]] == [0, 0, 0, 3, 0]
    assert distribution["mean"] == distribution["p50"] == distribution["p99"] == 2
    assert distribution["stddev"] == 0


def test_cache_invalidates_for_source_tokenizer_tools_and_definition_changes(store, compiler, sample, monkeypatch):
    store.samples.append(sample)
    metrics.refresh(store, compiler)
    key = metrics.cache_key(compiler)
    source = store.samples[0]
    source["metadata"] = metadata()
    store.samples[0] = source
    assert metrics.overview(store, compiler)["summary"]["pending"] == 1
    metrics.refresh(store, compiler)
    assert metrics.overview(store, compiler)["summary"]["pending"] == 0
    changed = Compiler(compiler.tokenizer, bos="<|eos|>", eos="<|bos|>", slot_policy=compiler.slot_policy)
    assert metrics.cache_key(changed) != key
    assert metrics.overview(store, changed)["summary"]["pending"] == 1
    monkeypatch.setattr(compiler.tools, "fingerprint", "changed tool implementation")
    assert metrics.cache_key(compiler) != key
    assert metrics.overview(store, compiler)["summary"]["pending"] == 1
    monkeypatch.setattr(metrics, "implementation_hash", lambda: "new metric definition")
    assert metrics.cache_key(compiler) != key
    assert metrics.overview(store, compiler)["summary"]["pending"] == 1


def test_streaming_ticks_and_tool_invocations_are_counted_separately(compiler, sample):
    sample["defaults"] = {"window-headers": False}
    sample["replay"].update({
        "1": [{"op": "append", "window": "a", "source": "user", "delivery": "stream", "rate": 1, "content": "xyz"}],
        "5": [{"op": "invisible-tool-call", "tool": "file_list", "arguments": {}, "duration-ticks": 2}],
        "8": [{"op": "invisible-tool-call", "tool": "file_list", "arguments": {}, "duration-ticks": 3}],
    })
    result = metrics.measure(compiler.compile(sample), compiler)
    assert result["tools"] == {"file_list": 2}
    assert result["tool_invocations"] == 2
    assert result["tool_latency_ticks"] == [2, 3]
    assert result["peak_external_streams"] == 1
    assert result["concurrency"]["external"][1]["ticks"] == 3


@pytest.mark.parametrize("broken", [
    {"review_status": "pending"}, {"confidence": True}, {"confidence": 1.1},
    {"evidence": []}, {"evidence": ["future-nonexistent-event"]}, {"annotator": ""},
    {"required": "true"}, {"demonstrated_correctly": "unknown"},
])
def test_requested_or_incomplete_labels_never_count_as_confirmed_coverage(store, compiler, sample, broken):
    sample["metadata"] = metadata(tags=["capability.delegation"], observed_labels={"capability.delegation": label(**broken)})
    store.samples.append(sample)
    metrics.refresh(store, compiler)
    report = metrics.overview(store, compiler, "accepted_train_originals")
    rule = next(r for r in report["coverage"] if r["id"] == "delegation")
    assert rule["denominator"] == rule["unknown"] == 1
    assert rule["numerator"] == 0
    assert rule["status"] == "insufficient_support"


def test_accepted_coverage_excludes_rejected_remixes_and_test_and_counts_families(store, compiler, sample):
    for i, changes in enumerate([
        {}, {}, {"split": "test"}, {"is_original": False}, {"validation_level": -2},
    ]):
        record = {**copy.deepcopy(sample), "id": f"s-{i}"}
        record["metadata"] = metadata(observed_labels={"capability.delegation": label()}, **changes)
        store.samples.append(record)
    metrics.refresh(store, compiler)
    result = metrics.overview(store, compiler, "accepted_train_originals")
    assert result["summary"]["samples"] == 2
    rule = next(r for r in result["coverage"] if r["id"] == "delegation")
    assert rule["numerator"] == rule["denominator"] == 2
    assert rule["unknown"] == 0 and rule["distinct_families"] == 1
    assert result["family_split_conflicts"]
    assert metrics.query_samples(store, compiler, "accepted_train_originals", issue="unknown_labels")["total"] == 2
    joint = next(r for r in result["coverage"] if r["id"] == "delegation_failure")
    assert joint["status"] == "pilot"
    assert all(r["status"] == "unavailable" for r in result["coverage"] if r.get("measure") == "exported_sample_fraction")


def test_invalid_drafts_and_empty_populations_have_explicit_missing_support(store, compiler):
    empty = metrics.overview(store, compiler)
    assert empty["summary"]["samples"] == 0
    assert empty["distributions"]["duration_ticks"]["mean"] is None
    store.samples.append({"id": "broken", "replay": {"-1": []}})
    revision = store.info()[0]
    metrics.refresh(store, compiler)
    result = metrics.overview(store, compiler)
    assert store.info()[0] == revision
    assert result["summary"]["compile_errors"] == 1
    assert result["distributions"]["duration_ticks"]["missing"] == 1
    assert metrics.query_samples(store, compiler, issue="compile_error")["samples"][0]["error"]["severity"] == "error"


def test_existing_project_opens_without_source_changes_and_caches_survive_reopen(store, compiler, sample):
    store.samples.append(sample)
    before, revision = store.samples[0], store.info()[0]
    with store.connect() as db:
        db.execute("DROP TABLE trajectory_metrics")  # an existing v5 project
    reopened = DatasetStore(store.path)
    assert reopened.samples[0] == before and reopened.info()[0] == revision
    metrics.refresh(reopened, compiler)
    again = DatasetStore(store.path)
    assert metrics.overview(again, compiler)["summary"]["pending"] == 0
    del again.samples[0]
    with again.connect() as db:
        assert db.execute("SELECT count(*) FROM trajectory_metrics").fetchone()[0] == 0


async def test_rest_mcp_validation_edit_undo_and_nonblocking_cache_refresh(studio, monkeypatch):
    app, client = studio
    commands = app.state.commands
    catalog = (await client.get("/api/mcp/tools")).json()["tools"]
    assert {"get_dataset_metrics", "refresh_dataset_metrics", "query_dataset_metrics", "get_dataset_metric_distribution"} <= {t["name"] for t in catalog}
    for path, arguments in [
        ("metrics", {"population": "unknown"}), ("metrics/refresh", {"limit": 0}),
        ("metrics/query", {"offset": -1}), ("metrics/query", {"metric": "x') OR 1=1 --"}),
        ("metrics/distribution", {"metric": "duration_ticks", "edges": [2, 1]}),
        ("metrics/query", {"minimum": 1}),
    ]:
        response = await client.post("/api/" + path, json=arguments)
        assert response.status_code == 422, response.text

    started, release = threading.Event(), threading.Event()
    compile_original = commands.compiler.compile
    def blocked_compile(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return compile_original(*args, **kwargs)
    monkeypatch.setattr(commands.compiler, "compile", blocked_compile)
    refresh = asyncio.create_task(client.post("/api/metrics/refresh", json={"limit": 1}))
    assert await asyncio.to_thread(started.wait, 3)
    try:
        assert (await asyncio.wait_for(client.get("/api/state"), 1)).status_code == 200
        source = (await client.get("/api/samples/0")).json()
        source["metadata"] = metadata()
        assert (await client.put("/api/samples/0", json={"sample": source})).status_code == 200
    finally:
        release.set()
    assert (await refresh).status_code == 200
    # The old in-flight result must not be published for the edited source.
    assert (await client.post("/api/metrics", json={})).json()["summary"]["pending"] == 1
    monkeypatch.setattr(commands.compiler, "compile", compile_original)
    assert (await client.post("/api/metrics/refresh", json={})).status_code == 200
    overview = (await client.post("/api/metrics", json={})).json()
    assert overview["summary"]["samples_missing_metadata"] == 0
    mcp = await client.post("/api/mcp/call", json={"name": "get_dataset_metrics", "arguments": {}})
    assert mcp.json() == overview
    assert (await client.post("/api/mcp/call", json={"name": "undo_edits", "arguments": {}})).status_code == 200
    assert (await client.post("/api/metrics", json={})).json()["summary"]["samples_missing_metadata"] == 1
