import asyncio
import copy

import pytest
from tokenizers import Tokenizer

from trajectory_remix.compiler import Compiler, chunks
from trajectory_remix.llm import selected_windows


CONTEXT_TOKENS = {
    "tool": "<tool>", "/tool": "</tool>",
    "tool_result": "<tool_result>", "/tool_result": "</tool_result>",
    "msg": "<|msg|>", "/msg": "<|/msg|>",
    "ctx": "<|ctx|>", "/ctx": "<|/ctx|>",
}


def test_context_tokens_without_dedicated_ids_remain_supported(compiler, sample):
    before = compiler.tokenizer.to_str()
    assert compiler.special_tokens == {**compiler.special, **CONTEXT_TOKENS}
    assert compiler.special_token_ids == compiler.ids
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 0)["windows"]["a"]["text"].startswith("<|ctx|>")
    assert compiler.tokenizer.to_str() == before


def test_dedicated_context_tokens_are_reported_and_preserved(compiler, sample):
    compiler.tokenizer.add_special_tokens(list(CONTEXT_TOKENS.values()))
    compiler = Compiler(compiler.tokenizer, slot_policy="declaration-v1")
    before = compiler.tokenizer.to_str()
    content = "<tool>{}</tool><tool_result>{}</tool_result><|msg|>Hello<|/msg|>"
    sample["replay"]["0"].append({
        "op": "append", "window": "a", "source": "user", "content": content,
    })
    plan = compiler.compile(sample)
    expected = "<|ctx|>Window: 0\nPurpose: document<|/ctx|>" + content
    for name, spelling in CONTEXT_TOKENS.items():
        assert plan["special_tokens"][name] == compiler.tokenizer.token_to_id(spelling)
    assert compiler.frame(plan, 0)["windows"]["a"]["text"] == expected
    task = {"visible": ["a"], "visible_tick": 0}
    assert selected_windows(plan, compiler, task)[0]["content"] == expected
    assert next(chunks(plan, 1))["special_tokens"] == plan["special_tokens"]
    assert compiler.tokenizer.to_str() == before


async def test_context_metadata_refreshes_after_migration_and_reopen(studio):
    app, client = studio
    response = await client.post("/api/mcp/call", json={
        "name": "get_session", "arguments": {},
    })
    assert response.status_code == 200, response.text
    assert response.json()["special_tokens"] == app.state.compiler.special_tokens
    tokenizer = app.state.compiler.tokenizer
    # Work on a copy so only the migration installs the new tokenizer.
    replacement = Tokenizer.from_str(tokenizer.to_str())
    replacement.add_special_tokens(list(CONTEXT_TOKENS.values()))
    response = await client.post("/api/tokenizer/migrate", json={
        "tokenizer": replacement.to_str(), "expected_revision": 0,
    })
    assert response.status_code == 202, response.text
    await asyncio.wait_for(asyncio.shield(app.state.tokenizer_migration.task), 10)
    assert app.state.tokenizer_migration.status["status"] == "completed"
    state = (await client.get("/api/state")).json()
    reopened = app.state.dataset.store.load_compiler()
    assert state["tokenizer"]["special"] == reopened.special_tokens
    assert state["tokenizer"]["special_ids"] == reopened.special_token_ids
    for name, spelling in CONTEXT_TOKENS.items():
        assert state["tokenizer"]["special"][name] == spelling
        assert state["tokenizer"]["special_ids"][name] == replacement.token_to_id(spelling)


async def test_custom_context_migration_renders_protocol_and_survives_reopen(studio):
    app, client = studio
    store = app.state.dataset.store
    sample = copy.deepcopy(store.samples[0])
    sample["replay"]["0"].extend([
        {"op": "open", "window": "chat"},
        {"op": "append", "window": "a", "source": "user",
         "content": "".join(CONTEXT_TOKENS.values())},
    ])
    sample["replay"]["1"] = [{"op": "tool-call", "window": "tool",
        "tool": "time_now", "arguments": {}, "result-anchor": 7}]
    sample["replay"]["{N_TICK:7}+1"] = [{"op": "tool-call", "window": "tool",
        "tool": "chat", "arguments": {"content": "Hello"}}]
    store.replace([sample], 0)
    custom = {name: f"<custom:{name}>" for name in CONTEXT_TOKENS}
    tokenizer = Tokenizer.from_str(app.state.compiler.tokenizer.to_str())
    tokenizer.add_special_tokens(list(custom.values()))
    response = await client.post("/api/tokenizer/migrate", json={
        "tokenizer": tokenizer.to_str(), "special": custom,
        "expected_revision": store.info()[0],
    })
    assert response.status_code == 202, response.text
    await asyncio.wait_for(asyncio.shield(app.state.tokenizer_migration.task), 10)
    assert app.state.tokenizer_migration.status["status"] == "completed"
    assert store.samples[0] == sample
    for compiler in (app.state.compiler, store.load_compiler()):
        assert compiler.context_tokens == custom
        plan = compiler.compile(sample)
        frame = compiler.frame(plan, plan["max_tick"])["windows"]
        assert frame["a"]["text"] == (
            custom["ctx"] + "Window: " + str(plan["window_slots"]["a"])
            + "\nPurpose: document" + custom["/ctx"] + "".join(custom.values())
        )
        for name in ("tool", "/tool", "tool_result", "/tool_result"):
            assert custom[name] in frame["tool"]["text"]
        assert custom["msg"] + "Kidra: Hello" + custom["/msg"] in frame["chat"]["text"]
        for name in custom:
            assert plan["special_tokens"][name] == tokenizer.token_to_id(custom[name])
    # Omitting the special overrides on a subsequent migration retains them.
    response = await client.post("/api/tokenizer/migrate", json={
        "tokenizer": tokenizer.to_str(), "expected_revision": store.info()[0],
    })
    assert response.status_code == 202, response.text
    await app.state.tokenizer_migration.task
    assert store.load_compiler().context_tokens == custom


@pytest.mark.parametrize("name", CONTEXT_TOKENS)
@pytest.mark.parametrize("spelling", ["", "missing-token", "<|eos|>", 42])
async def test_invalid_context_override_never_starts_migration(studio, name, spelling):
    app, client = studio
    response = await client.post("/api/tokenizer/migrate", json={
        "tokenizer": app.state.compiler.tokenizer.to_str(),
        "special": {name: spelling}, "expected_revision": 0,
    })
    assert response.status_code == 422, response.text
    assert app.state.tokenizer_migration.task is None
