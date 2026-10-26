import copy
import json
import math

import pytest

from trajectory_remix.tools import ExecutionContext, Sandbox, ToolError, ToolRegistry
from test_io_extensions import call, hidden


URL = "https://docs.example.test/releases/4"


def page(text="Version 4 requires Python 3.12 or later.", **extra):
    return {"url": URL, "title": "Version 4 release notes", "text": text, **extra}


def seed(sandbox, pages=None, **extra):
    return sandbox.call_sync("web_set", {"pages": pages if pages is not None else [page()], **extra}, invisible=True)


def invoke_raw(sandbox, tool, args):
    output, done, _ = sandbox.registry.run_tick(
        tool, args, sandbox.state, [], ExecutionContext(tick=sandbox.tick, start=sandbox.tick,
                                                      call_id="bounded", max_bytes=sandbox.max_bytes)
    )
    assert done
    assert len(output.encode()) <= args["max_output_bytes"]
    value = json.loads(output)
    assert value["ok"], value
    return value["result"]


def test_catalog_replaces_network_tools_and_hides_author_setup():
    registry = ToolRegistry()
    public = {entry["function"]["name"] for entry in registry.schemas()}
    all_tools = {entry["function"]["name"] for entry in registry.schemas(invisible=True)}
    assert {"web_search", "web_open", "web_find"} <= public
    assert "web_set" in all_tools - public
    assert not {"network_set", "network_request"} & all_tools
    with pytest.raises(ToolError, match="invisible"):
        Sandbox().call_sync("web_set", {"pages": []})


def test_search_open_find_and_follow_link_share_source_evidence():
    sandbox = Sandbox()
    seed(sandbox, [page(published_at="2030-02-12", links=[
        {"id": "install", "text": "Installation", "url": "/install"}
    ]), page("Install with pip.", url="https://docs.example.test/install", title="Installation")])
    search = sandbox.call_sync("web_search", {"query": "version 4 Python requirement"})
    hit = search["results"][0]
    opened = sandbox.call_sync("web_open", {"ref": hit["ref"]})
    found = sandbox.call_sync("web_find", {"ref": hit["ref"], "text": "python"})
    assert opened["ref"] == found["ref"] == hit["ref"]
    assert hit["snippet"] in opened["content"]
    assert hit["published_at"] == "2030-02-12"
    assert found["matches"][0]["line"] == 1
    assert found["matches"][0]["text"] in opened["content"]
    assert sandbox.call_sync("web_open", {"url": opened["links"][0]["url"]})["content"] == "Install with pip."
    assert "web_pending" not in sandbox.state


def test_bm25_title_boost_stable_ties_and_unrelated_queries():
    sandbox = Sandbox()
    seed(sandbox, [page("Python", title="Unrelated", url="https://example.test/z"),
                   page("Python", title="Python", url="https://example.test/b"),
                   page("Python", title="Python", url="https://example.test/a")])
    args = {"query": "PYTHON"}
    first = sandbox.call_sync("web_search", args)
    assert [hit["url"] for hit in first["results"]] == [
        "https://example.test/a", "https://example.test/b", "https://example.test/z"]
    assert sandbox.call_sync("web_search", args) == first
    assert sandbox.call_sync("web_search", {"query": "pineapple"}) == {
        "results": [], "total_results": 0, "truncated": False, "next_offset": None}


def test_exact_query_overrides_allow_empty_results_and_fallback():
    sandbox = Sandbox()
    seed(sandbox, [page(), page("Python", url="https://other.test/", title="Other")],
         searches=[{"query": "Python", "urls": ["https://other.test/", URL]},
                   {"query": "no results", "urls": []}])
    assert [hit["url"] for hit in sandbox.call_sync("web_search", {"query": "  PYTHON  "})["results"]] == ["https://other.test/", URL]
    assert sandbox.call_sync("web_search", {"query": "no results"})["results"] == []
    assert sandbox.call_sync("web_search", {"query": "Version"})["results"][0]["url"] == URL


def test_search_and_find_pagination():
    sandbox = Sandbox()
    seed(sandbox, [page("hit\nHit\nHIT", url=f"https://example.test/{i}") for i in range(7)])
    urls, offset = [], 0
    while offset is not None:
        result = sandbox.call_sync("web_search", {"query": "hit", "offset": offset, "max_results": 2})
        urls.extend(hit["url"] for hit in result["results"])
        offset = result["next_offset"]
    assert len(urls) == len(set(urls)) == 7
    ref = sandbox.call_sync("web_open", {"url": urls[0]})["ref"]
    first = sandbox.call_sync("web_find", {"ref": ref, "text": "hit", "max_results": 1})
    assert first["truncated"] and first["next_line"] == 2
    rest = sandbox.call_sync("web_find", {"ref": ref, "text": "hit", "start_line": first["next_line"]})
    assert [hit["line"] for hit in rest["matches"]] == [2, 3]
    assert rest["next_line"] is None and not rest["truncated"]
    exact = sandbox.call_sync("web_find", {"ref": ref, "text": "hit", "case_sensitive": True})
    assert [hit["line"] for hit in exact["matches"]] == [1]


@pytest.mark.parametrize("text", ["x" * 14000, "😀\\\"" * 3000, "αβγ\n" * 2000], ids=["long-line", "escaped-emoji", "many-lines"])
def test_open_budget_and_continuation_recover_exact_text(text):
    sandbox = Sandbox(max_calls=256)
    seed(sandbox, [page(text)])
    content, position = "", {"start_line": 1, "start_column": 0}
    for _ in range(256):
        result = invoke_raw(sandbox, "web_open", {
            "url": URL, "max_output_bytes": 1024, "max_lines": 17, **position})
        content += result["content"]
        if not result["truncated"]:
            assert result["next_line"] is result["next_column"] is None
            break
        assert result["content"]
        position = {"start_line": result["next_line"], "start_column": result["next_column"]}
    else:
        pytest.fail("continuation did not finish")
    assert content == text


@pytest.mark.parametrize("tool,args", [
    ("web_search", {"query": "hit"}),
    ("web_find", {"url": URL, "text": "hit"}),
])
def test_search_and_find_aggregate_utf8_budgets(tool, args):
    sandbox = Sandbox()
    seed(sandbox, [page(("hit " + "😀" * 200 + "\n") * 15, url=URL if i == 0 else f"https://example.test/{i}")
                   for i in range(12)])
    result = invoke_raw(sandbox, tool, {**args, "max_output_bytes": 1024})
    assert result["truncated"]
    assert result["results" if tool == "web_search" else "matches"]


def test_open_empty_pages_out_of_range_lines_and_long_link_pagination():
    sandbox = Sandbox()
    seed(sandbox, [page("", links=[{"id": str(i), "text": "Label" * 70, "url": f"/link/{i}"}
                                   for i in range(12)])])
    result = sandbox.call_sync("web_open", {"url": URL, "max_links": 0})
    assert result["content"] == "" and result["total_lines"] == 0
    assert result["next_line"] is None and result["next_link_offset"] == 0
    links, offset = [], 0
    while offset is not None:
        result = invoke_raw(sandbox, "web_open", {"url": URL, "start_line": 99,
                            "link_offset": offset, "max_output_bytes": 1024})
        links.extend(result["links"])
        assert result["links"]
        offset = result["next_link_offset"]
    assert len(links) == 12


def test_refs_pin_old_evidence_while_urls_and_search_use_replacement():
    sandbox = Sandbox()
    seed(sandbox, [page("Old Python facts")])
    original = sandbox.call_sync("web_search", {"query": "Python"})["results"][0]
    seed(sandbox, [page("New Python facts")])
    assert sandbox.call_sync("web_open", {"ref": original["ref"]})["content"] == "Old Python facts"
    assert sandbox.call_sync("web_find", {"ref": original["ref"], "text": "Old"})["matches"]
    current = sandbox.call_sync("web_open", {"url": URL})
    assert current["content"] == "New Python facts" and current["ref"] != original["ref"]
    seed(sandbox, [])
    assert sandbox.call_sync("web_open", {"ref": original["ref"]})["content"] == "Old Python facts"
    assert sandbox.call_sync("web_search", {"query": "Python"})["results"] == []
    with pytest.raises(ToolError, match="No simulated"):
        sandbox.call_sync("web_open", {"url": URL})


def test_content_refs_do_not_depend_on_fixture_object_key_order():
    sandbox = Sandbox()
    original = page()
    seed(sandbox, [original])
    ref = sandbox.call_sync("web_open", {"url": URL})["ref"]
    seed(sandbox, [dict(reversed(list(original.items())))])
    assert sandbox.call_sync("web_open", {"url": URL})["ref"] == ref


@pytest.mark.parametrize("bad", [
    {"pages": [page(), page()]},
    {"pages": [page(links=[{"id": "x", "text": "a", "url": "/a"}, {"id": "x", "text": "b", "url": "/b"}])]},
    {"pages": [page(links=[{"id": "x", "text": "bad", "url": "file:///secret"}])]},
    {"pages": [page()], "searches": [{"query": "x", "urls": ["https://missing.test/"]}]},
    {"pages": [page()], "searches": [{"query": "x", "urls": [URL, URL]}]},
    {"pages": [], "searches": [{"query": "X", "urls": []}, {"query": " x ", "urls": []}]},
])
def test_invalid_fixture_replacement_is_atomic(bad):
    sandbox = Sandbox()
    seed(sandbox)
    before = sandbox.snapshot()
    with pytest.raises(ToolError):
        sandbox.call_sync("web_set", bad, invisible=True)
    assert sandbox.snapshot() == before


@pytest.mark.parametrize("url", ["file:///secret", "https://user:pass@example.test/", "https://[bad/",
                                     "https://example.test:wrong/", "https://example.test/a b"])
def test_invalid_urls_do_not_mutate_web_state(url):
    sandbox = Sandbox()
    before = sandbox.snapshot()
    with pytest.raises(ToolError, match="URL"):
        seed(sandbox, [page(url=url)])
    with pytest.raises(ToolError, match="URL"):
        sandbox.call_sync("web_open", {"url": url})
    assert sandbox.snapshot() == before


@pytest.mark.parametrize("tool,args", [
    ("web_open", {}), ("web_open", {"url": URL, "ref": "both"}),
    ("web_find", {"url": URL, "text": ""}), ("web_search", {"query": "x", "max_output_bytes": 100}),
    ("web_search", {"query": "x", "timeout_seconds": float("nan")}),
])
def test_invalid_arguments(tool, args):
    with pytest.raises(ToolError):
        Sandbox().call_sync(tool, args)


def test_unknown_sources_blank_queries_and_invalid_columns():
    sandbox = Sandbox()
    seed(sandbox)
    for tool, args in [("web_open", {"url": "https://unconfigured.test/"}),
                       ("web_open", {"ref": "unknown"}),
                       ("web_open", {"url": URL, "start_column": 999}),
                       ("web_search", {"query": "  "}),
                       ("web_search", {"query": "!!!"}),
                       ("web_find", {"url": URL, "text": "   "})]:
        with pytest.raises(ToolError):
            sandbox.call_sync(tool, args)
    assert "web_pending" not in sandbox.state


def test_fixture_and_historical_snapshot_quotas_are_atomic():
    sandbox = Sandbox(max_bytes=3500)
    seed(sandbox, [page("a" * 2000)])
    before = sandbox.snapshot()
    with pytest.raises(ToolError, match="quota"):
        seed(sandbox, [page("b" * 2000)])
    assert sandbox.snapshot() == before


@pytest.mark.parametrize("status", [403, 404, 429, 503])
def test_authored_http_status_is_a_readable_result(status):
    sandbox = Sandbox()
    seed(sandbox, [page("Unavailable", status=status)])
    assert sandbox.call_sync("web_open", {"url": URL})["status"] == status


@pytest.mark.parametrize("error", ["dns", "tls", "connection_refused", "connection_reset", "timeout"])
def test_transport_errors_and_cleanup(error):
    sandbox = Sandbox()
    seed(sandbox, [page(error=error)])
    with pytest.raises(ToolError, match="ETIMEDOUT" if error == "timeout" else error):
        sandbox.call_sync("web_open", {"url": URL, "timeout_seconds": 0.2})
    assert "web_pending" not in sandbox.state


def test_url_permissions_cannot_be_bypassed_by_refs_or_fragments():
    sandbox = Sandbox()
    seed(sandbox)
    ref = sandbox.call_sync("web_open", {"url": URL})["ref"]
    sandbox.call_sync("tool_permissions_set", {"rules": [
        {"tool": "web_*", "path": URL, "decision": "deny"}]}, invisible=True)
    for target in ({"url": URL}, {"url": URL + "#python"}, {"ref": ref}):
        for tool, extra in (("web_open", {}), ("web_find", {"text": "Python"})):
            with pytest.raises(ToolError, match="Permission denied"):
                sandbox.call_sync(tool, {**target, **extra})
    assert sandbox.call_sync("web_search", {"query": "Python"})["results"] == []
    assert sandbox.call_sync("web_open", {"ref": ref}, invisible=True)["content"]


def test_permission_then_transient_fault_then_success_with_ref_matching():
    sandbox = Sandbox()
    seed(sandbox)
    ref = sandbox.call_sync("web_open", {"url": URL})["ref"]
    sandbox.call_sync("tool_permissions_set", {"rules": [
        {"tool": "web_open", "path": URL, "decision": "deny", "remaining": 1}]}, invisible=True)
    sandbox.call_sync("io_failure_set", {"rules": [
        {"tool": "web_open", "path": URL, "code": "ECONNRESET", "remaining": 1}]}, invisible=True)
    for error in ("Permission denied", "ECONNRESET"):
        with pytest.raises(ToolError, match=error):
            sandbox.call_sync("web_open", {"ref": ref})
        assert "web_pending" not in sandbox.state
    assert sandbox.call_sync("web_open", {"ref": ref})["content"]


@pytest.mark.parametrize("rate", [2.5, 10, 20])
@pytest.mark.parametrize("tool,args", [("web_open", {"url": URL}), ("web_search", {"query": "Python"}),
                                       ("web_find", {"url": URL, "text": "Python"})])
def test_replay_latency_deadlines_and_sparse_snapshots(compiler, sample, rate, tool, args):
    sample["metadata"] = {"synthetic_clock": {"tick_rate": rate}}
    sample["replay"].update({
        "0": [hidden("web_set", {"pages": [page(latency_seconds=2)], "search_latency_seconds": 2})],
        "1": [call(tool, args, "success"),
              call(tool, {**args, "timeout_seconds": 1}, "timeout"),
              call(tool, {**args, "timeout_seconds": 2}, "tie")],
    })
    plan = compiler.compile(sample)
    assert compiler.compile(copy.deepcopy(sample)) == plan
    success, timeout, tie = plan["tool_calls"][1:]
    assert success["end"] == success["start"] + 2 * rate
    assert timeout["end"] == timeout["start"] + math.ceil(rate)
    assert "ETIMEDOUT" in timeout["outcome"]["error"]
    assert tie["outcome"]["ok"]
    assert not set(map(str, range(3, math.ceil(rate) + 2))).intersection(plan["sandbox"]["snapshots"])
    assert "web_pending" not in compiler.frame(plan, plan["max_tick"])["sandbox"]


@pytest.mark.parametrize("tool,args,field", [("web_open", {"url": URL}, "content"),
    ("web_search", {"query": "Python"}, "results"), ("web_find", {"url": URL, "text": "Python"}, "matches")])
def test_inflight_results_survive_fixture_changes(compiler, sample, tool, args, field):
    sample["replay"].update({
        "0": [hidden("web_set", {"pages": [page("Old Python facts", latency_seconds=1)], "search_latency_seconds": 1})],
        "1": [call(tool, args)],
        "5": [hidden("web_set", {"pages": [page("New Python facts")]})],
    })
    plan = compiler.compile(sample)
    observed = plan["tool_calls"][1]["outcome"]["result"][field]
    assert "Old Python facts" in str(observed) and "New Python facts" not in str(observed)
    assert plan["tool_calls"][1]["end"] == 12


def test_clock_jump_completes_waiting_request(compiler, sample):
    sample["replay"].update({
        "0": [hidden("web_set", {"pages": [page(latency_seconds=50)]})],
        "1": [call("web_open", {"url": URL, "timeout_seconds": 60})],
        "8": [hidden("time_advance", {"seconds": 50})],
    })
    plan = compiler.compile(sample)
    assert plan["tool_calls"][1]["end"] == 9


def test_pending_request_quota_failure_does_not_leave_state():
    sandbox = Sandbox(max_bytes=1500)
    seed(sandbox, [page("a" * 650)])
    before = sandbox.snapshot()
    with pytest.raises(ToolError, match="quota"):
        sandbox.call_sync("web_open", {"url": URL})
    assert sandbox.snapshot() == before


@pytest.mark.parametrize("control,rule,error", [
    ("tool_permissions_set", {"decision": "deny"}, "Permission denied"),
    ("io_failure_set", {"code": "EIO"}, "EIO"),
])
def test_long_authored_errors_also_obey_response_budget(control, rule, error):
    sandbox = Sandbox()
    seed(sandbox)
    sandbox.call_sync(control, {"rules": [{"tool": "web_open", "message": "😀" * 1024, **rule}]}, invisible=True)
    output, done, _ = sandbox.registry.run_tick("web_open", {"url": URL, "max_output_bytes": 1024},
        sandbox.state, [], ExecutionContext(tick=2, start=2))
    assert done and len(output.encode()) <= 1024
    value = json.loads(output)
    assert not value["ok"] and error in value["error"]
    assert "web_pending" not in sandbox.state
