import asyncio

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
