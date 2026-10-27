"""Advisory diagnostics for chat delivery and tool-schema placement."""

import pytest


@pytest.mark.parametrize("window", ["chat", "conversation"])
@pytest.mark.parametrize(
    "content,warns",
    [
        ("Hello!", True),
        ("Hello!<|eos|>", True),
        ("<|eos|>", False),
        ("<|noop|>", False),
        ("", False),
    ],
)
def test_chat_generation_warning_excludes_control_events(
    compiler, sample, window, content, warns
):
    sample["replay"] = {
        "0": [{"op": "open", "window": window, "window-type": "chat"}],
        "1": [{"op": "generate", "window": window, "content": content}],
    }
    plan = compiler.compile(sample)
    warnings = [
        d for d in plan["diagnostics"] if d.get("code") == "direct_chat_generation"
    ]
    assert bool(warnings) == warns
    if warns:
        assert warnings[0]["path"] == "replay[1][0]"
        assert warnings[0]["severity"] == "warning"
        assert "chat tool call" in warnings[0]["message"]


@pytest.mark.parametrize(
    "window,purpose,warns",
    [
        ("system", "system", True),
        ("instructions", "system", True),
        ("system", "document", True),
        ("tools", "tools", False),
    ],
)
def test_show_tools_warning_identifies_system_context(
    compiler, sample, window, purpose, warns
):
    sample["replay"] = {
        "0": [{"op": "open", "window": window, "window-type": purpose}],
        "1": [{"op": "show_tools", "window": window, "tools": ["calculator"]}],
    }
    plan = compiler.compile(sample)
    warnings = [d for d in plan["diagnostics"] if d.get("code") == "tools_in_system"]
    assert bool(warnings) == warns
    if warns:
        assert warnings[0]["path"] == "replay[1][0]"
        assert warnings[0]["severity"] == "warning"
        assert "dedicated tools window" in warnings[0]["message"]
