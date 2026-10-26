import pytest

from trajectory_remix.tools import Sandbox, ToolError, ToolRegistry


def call(tool, arguments, window="tool", **extra):
    return {
        "op": "tool-call",
        "tool": tool,
        "arguments": arguments,
        "window": window,
        "content": "x",
        **extra,
    }


def hidden(tool, arguments):
    return {"op": "invisible-tool-call", "tool": tool, "arguments": arguments}


def test_file_ranges_versions_and_numbering():
    sandbox = Sandbox()
    version = sandbox.call_sync(
        "file_write", {"path": "a", "content": "one\ntwo\nthree"}
    )["version"]
    sandbox.call_sync("file_write", {"path": "a", "content": "changed"})
    assert sandbox.call_sync(
        "file_read",
        {
            "path": "a",
            "version": version,
            "start_line": 2,
            "end_line": 3,
            "line_numbers": True,
        },
    ) == {
        "path": "/a",
        "version": version,
        "content": "2: two\n3: three",
        "start_line": 2,
        "end_line": 3,
        "total_lines": 3,
    }
    assert (
        sandbox.call_sync("file_read", {"path": "a", "start_line": 99})["content"] == ""
    )
    with pytest.raises(ToolError, match="end_line"):
        sandbox.call_sync("file_read", {"path": "a", "start_line": 3, "end_line": 1})


def test_atomic_multi_patch_and_stale_version():
    sandbox = Sandbox()
    version = sandbox.call_sync("file_write", {"path": "a", "content": "abc def"})[
        "version"
    ]
    with pytest.raises(ToolError, match="exactly once"):
        sandbox.call_sync(
            "file_patch",
            {
                "path": "a",
                "edits": [
                    {"old": "abc", "new": "xyz"},
                    {"old": "missing", "new": "oops"},
                ],
            },
        )
    assert sandbox.files["/a"] == "abc def"
    sandbox.call_sync(
        "file_patch",
        {
            "path": "a",
            "expected_version": version,
            "edits": [{"old": "abc", "new": "xyz"}, {"old": "def", "new": "123"}],
        },
    )
    assert sandbox.files["/a"] == "xyz 123"
    with pytest.raises(ToolError, match="version conflict"):
        sandbox.call_sync(
            "file_patch",
            {"path": "a", "expected_version": version, "old": "xyz", "new": "abc"},
        )
    assert sandbox.files["/a"] == "xyz 123"


@pytest.mark.parametrize("operation", ["file_copy", "file_move", "file_rename"])
def test_transfers_are_atomic_and_keep_history(operation):
    sandbox = Sandbox()
    version = sandbox.call_sync("file_write", {"path": "src", "content": "source"})[
        "version"
    ]
    sandbox.call_sync("file_write", {"path": "dest", "content": "destination"})
    for extra in ({}, {"overwrite": True, "expected_version": "wrong"}):
        with pytest.raises(ToolError):
            sandbox.call_sync(
                operation, {"source": "src", "destination": "dest", **extra}
            )
        assert sandbox.files == {"/src": "source", "/dest": "destination"}
    sandbox.call_sync(
        operation,
        {
            "source": "src",
            "destination": "dest",
            "overwrite": True,
            "expected_version": version,
        },
    )
    assert sandbox.files["/dest"] == "source"
    assert ("/src" in sandbox.files) == (operation == "file_copy")
    assert (
        sandbox.call_sync("file_read", {"path": "src", "version": version})["content"]
        == "source"
    )
    with pytest.raises(ToolError, match="differ"):
        sandbox.call_sync(
            operation, {"source": "dest", "destination": "dest", "overwrite": True}
        )


def test_search_and_glob_with_context_and_truncation():
    sandbox = Sandbox()
    for path in ("src/a.py", "src/b.py", "ignored.txt"):
        sandbox.call_sync(
            "file_write", {"path": path, "content": "before\nHELLO 42\nafter\nhello 43"}
        )
    result = sandbox.call_sync(
        "file_search",
        {
            "query": r"hello \d+",
            "regex": True,
            "case_sensitive": False,
            "glob": "src/*.py",
            "context_lines": 1,
            "max_results": 2,
        },
    )
    assert result["truncated"] is True
    assert result["matches"][0] == {
        "path": "/src/a.py",
        "line": 2,
        "text": "HELLO 42",
        "before": ["before"],
        "after": ["after"],
    }
    assert result["matches"][1]["line"] == 4
    assert sandbox.call_sync("file_list", {"glob": "src/*.py"}) == [
        "/src/a.py",
        "/src/b.py",
    ]
    assert sandbox.call_sync("file_search", {"query": ".*"}) == {
        "matches": [],
        "truncated": False,
    }
    with pytest.raises(ToolError, match="regex"):
        sandbox.call_sync("file_search", {"query": "[", "regex": True})


def test_transient_io_failure_is_consumed_once_and_retry_succeeds(compiler, sample):
    sample["replay"].update(
        {
            "0": [
                hidden(
                    "io_failure_set",
                    {
                        "rules": [
                            {
                                "tool": "file_write",
                                "path": "/x",
                                "code": "ENOSPC",
                                "remaining": 1,
                            }
                        ]
                    },
                )
            ],
            "1": [call("file_write", {"path": "x", "content": "failed"}, "first")],
            "4": [call("file_write", {"path": "x", "content": "success"}, "retry")],
        }
    )
    plan = compiler.compile(sample)
    assert compiler.compile(sample) == plan
    assert "ENOSPC" in plan["tool_calls"][1]["outcome"]["error"]
    assert "files" not in compiler.frame(plan, 2)["sandbox"]
    assert compiler.frame(plan, 5)["sandbox"]["files"] == {"/x": "success"}
    assert (
        compiler.frame(plan, 2)["sandbox"]["io_failures"]["rules"][0]["remaining"] == 0
    )


def test_persistent_faults_clear_and_match_transfer_destination():
    sandbox = Sandbox()
    sandbox.call_sync("file_write", {"path": "a", "content": "data"})
    sandbox.call_sync(
        "io_failure_set",
        {"rules": [{"tool": "file_*", "path": "/blocked", "code": "EIO"}]},
        invisible=True,
    )
    for _ in range(2):
        with pytest.raises(ToolError, match="EIO"):
            sandbox.call_sync("file_move", {"source": "a", "destination": "blocked"})
        assert sandbox.files == {"/a": "data"}
    sandbox.call_sync("io_failure_set", {"rules": []}, invisible=True)
    sandbox.call_sync("file_move", {"source": "a", "destination": "blocked"})
    assert sandbox.files == {"/blocked": "data"}


def test_user_rejection_then_retry_and_author_bypass():
    sandbox = Sandbox()
    sandbox.call_sync(
        "tool_permissions_set",
        {
            "rules": [
                {
                    "tool": "file_write",
                    "decision": "deny",
                    "remaining": 1,
                    "message": "User clicked Reject",
                }
            ]
        },
        invisible=True,
    )
    with pytest.raises(ToolError, match="User clicked Reject"):
        sandbox.call_sync("file_write", {"path": "x", "content": "no"})
    assert sandbox.files == {}
    sandbox.call_sync("file_write", {"path": "x", "content": "yes"})
    sandbox.call_sync("tool_permissions_set", {"default": "deny"}, invisible=True)
    with pytest.raises(ToolError, match="Permission denied"):
        sandbox.call_sync("calculator", {"expression": "1+1"})
    sandbox.call_sync("file_write", {"path": "x", "content": "author"}, invisible=True)
    assert sandbox.files["/x"] == "author"


def test_permissions_filter_search_and_list_without_leaking_paths():
    sandbox = Sandbox()
    for path in ("public/data", "private/data"):
        sandbox.call_sync("file_write", {"path": path, "content": "secret"})
    sandbox.call_sync(
        "tool_permissions_set",
        {"rules": [{"tool": "file_*", "path": "/private/*", "decision": "deny"}]},
        invisible=True,
    )
    assert sandbox.call_sync("file_list", {}) == ["/public/data"]
    assert [
        r["path"]
        for r in sandbox.call_sync("file_search", {"query": "secret"})["matches"]
    ] == ["/public/data"]
    with pytest.raises(ToolError, match="Permission denied"):
        sandbox.call_sync("file_read", {"path": "private/data"})


def test_allow_on_transfer_source_cannot_bypass_destination_rejection():
    sandbox = Sandbox()
    sandbox.call_sync("file_write", {"path": "source", "content": "data"})
    sandbox.call_sync(
        "tool_permissions_set",
        {
            "rules": [
                {"path": "/source", "decision": "allow"},
                {"path": "/destination", "decision": "deny"},
            ]
        },
        invisible=True,
    )
    with pytest.raises(ToolError, match="Permission denied"):
        sandbox.call_sync(
            "file_move", {"source": "source", "destination": "destination"}
        )
    assert sandbox.files == {"/source": "data"}


def test_permission_rejection_cannot_execute_custom_tool(tmp_path):
    (tmp_path / "side_effect.py").write_text(
        'name="side_effect"\nschema={"type":"object"}\ndef run(args,state,windows):\n state["ran"]=True\n return "ran",True,True\n'
    )
    sandbox = Sandbox(registry=ToolRegistry(tmp_path))
    sandbox.call_sync("tool_permissions_set", {"default": "deny"}, invisible=True)
    with pytest.raises(ToolError, match="Permission denied"):
        sandbox.call_sync("side_effect", {})
    assert "ran" not in sandbox.state


def test_new_author_controls_never_appear_in_public_schemas():
    registry = ToolRegistry()
    names = {entry["function"]["name"] for entry in registry.schemas()}
    assert not names.intersection(
        {"io_failure_set", "tool_permissions_set", "web_set"}
    )
