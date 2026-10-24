import pytest

from trajectory_remix.tools import Sandbox, ToolError


async def test_isolated_files_and_cleanup():
    a, b = Sandbox(), Sandbox()
    await a.call("file_write", {"path": "/etc/example", "content": "virtual only"})
    assert (await a.call("file_read", {"path": "/etc/example"}))[
        "content"
    ] == "virtual only"
    assert await b.call("file_list", {}) == []
    a.close()
    assert a.files == {}
    with pytest.raises(ToolError, match="destroyed"):
        await a.call("file_list", {})


@pytest.mark.parametrize("path", ["../../etc/passwd", "/foo/../bar", "a\\b", "bad\x00"])
async def test_traversal_rejected(path):
    with pytest.raises(ToolError):
        await Sandbox().call("file_read", {"path": path})


async def test_allowlist_and_argument_validation():
    s = Sandbox()
    with pytest.raises(ToolError, match="Unknown tool"):
        await s.call("shell", {"command": "id"})
    with pytest.raises(ToolError):
        await s.call("file_list", {"path": "/"})
    with pytest.raises(ToolError):
        await s.call("time_advance", {"seconds": float("inf")}, invisible=True)


async def test_fake_clock():
    s = Sandbox(seed="2030-01-01T00:00:00Z", seconds_per_call=2)
    assert (await s.call("time_now", {}))["timestamp"] == "2030-01-01T00:00:02+00:00"
    assert (await s.call("time_advance", {"seconds": 8}, invisible=True))[
        "timestamp"
    ] == "2030-01-01T00:00:10+00:00"


async def test_rag_notes_and_skills():
    s = Sandbox()
    await s.call("memory_save", {"key": "one", "text": "Kidra coordinates tools"})
    await s.call("memory_save", {"key": "two", "text": "Unrelated apples"})
    result = await s.call("memory_search", {"query": "Kidra tools"})
    assert [r["key"] for r in result] == ["one"]
    await s.call("note_save", {"key": "work", "text": "inspect", "status": "todo"})
    assert (await s.call("note_list", {}))["work"]["status"] == "todo"
    await s.call("note_delete", {"key": "work"})
    await s.call("skill_save", {"name": "audit", "content": "Verify results."})
    assert (await s.call("skill_load", {"name": "audit"}))[
        "content"
    ] == "Verify results."
    assert await s.call("skill_list", {}) == ["audit"]


async def test_quotas():
    s = Sandbox(max_calls=1, max_bytes=100)
    with pytest.raises(ToolError, match="quota"):
        await s.call("file_write", {"path": "x", "content": "a" * 1000})
    await s.call("file_list", {})
    with pytest.raises(ToolError, match="budget"):
        await s.call("file_list", {})


async def test_subagent_uses_only_authored_replies():
    sandbox = Sandbox()
    result = await sandbox.call(
        "subagent",
        {"task": "inspect"},
        subagent_outcome={"ok": True, "result": {"response": "Authored inspection"}},
    )
    assert result == {
        "conversationId": "conversation-1",
        "response": "Authored inspection",
    }
    with pytest.raises(ToolError, match="authored response") as error:
        await sandbox.call(
            "subagent", {"task": "Explain", "resume": result["conversationId"]}
        )
    assert sandbox.conversations[result["conversationId"]]["status"] == "ready"
