"""Bounded, evidence-preserving review and atomic event repairs."""

import copy
import json
from itertools import pairwise

import pytest

from trajectory_remix.review import (
    ReviewView,
    apply_edits,
    compact,
    count,
    deduplicate,
    fragments,
)
from trajectory_remix.scenarios import ReviewCheck


def configured(**kwargs):
    return ReviewCheck(prompt="review.md", view="layout", **kwargs)


def timeline(sample, n=50):
    sample["replay"]["0"].append(
        {"op": "open", "window": "chat", "window-type": "chat"}
    )
    for i in range(1, n + 1):
        sample["replay"][str(i)] = [
            {
                "op": "append",
                "window": "chat",
                "source": "user",
                "content": f"Question {i}",
            }
        ]
    sample["metadata"] = {
        "evaluations": [{"secret": "DO_NOT_SEND_OLD_VERDICTS"}],
        "concept": {"files": "DO_NOT_SEND_CONCEPT" * 1000},
    }
    return sample


def test_overlapping_groups_cover_every_unit_without_full_source(compiler, sample):
    sample = timeline(sample, 100)
    view = ReviewView(
        sample, compiler.compile(sample), configured(), compiler.tokenizer
    )
    groups = list(
        view.groups({}, lambda p: count(compiler.tokenizer, compact(p)) < 5000)
    )
    assert len(groups) > 10
    assert {u["id"] for p in groups for u in p["events"]} == set(view.by_id)
    for left, right in pairwise(groups):
        assert {u["id"] for u in left["events"]} & {u["id"] for u in right["events"]}
    for group in groups:
        assert len(group["events"]) <= 10
        assert count(compiler.tokenizer, compact(group)) < 5000
        assert "DO_NOT_SEND" not in compact(group)
        assert "trajectory" not in group and "compiled" not in group


def test_large_unicode_text_is_split_losslessly(compiler, sample):
    original = 'first line\n  spaced code\t漢字 🙂 "quoted"\n' * 100
    chunks = list(fragments(original, compiler.tokenizer, 100))
    assert "".join(text for _, text in chunks) == original
    assert all(original[offset : offset + len(text)] == text for offset, text in chunks)
    assert all(count(compiler.tokenizer, text) <= 100 for _, text in chunks)
    sample = timeline(sample, 1)
    sample["replay"]["1"][0]["content"] = original
    view = ReviewView(
        sample,
        compiler.compile(sample),
        configured(content_tokens=100),
        compiler.tokenizer,
    )
    pieces = [u for u in view.units if u.get("field") == "/content"]
    assert "".join(u["text"] for u in pieces) == original
    assert {u["event_id"] for u in pieces} == {"replay[1][0]"}


def test_budget_overflow_fails_instead_of_truncating(compiler, sample):
    sample = timeline(sample, 1)
    view = ReviewView(
        sample,
        compiler.compile(sample),
        configured(inputs=["task"]),
        compiler.tokenizer,
    )
    with pytest.raises(ValueError, match="budget exceeded"):
        list(view.groups({"task": "x" * 10000}, lambda p: len(compact(p)) < 1000))


def test_view_inputs_are_explicit_and_missing_paths_fail(compiler, sample):
    sample = timeline(sample, 1)
    view = ReviewView(
        sample,
        compiler.compile(sample),
        configured(inputs=["idea.problem"]),
        compiler.tokenizer,
    )
    payload = view.payload(
        view.units,
        {
            "idea": {"problem": "actual question", "files": "private"},
            "roles": "private",
        },
    )
    assert payload["inputs"] == {"idea.problem": "actual question"}
    assert "private" not in compact(payload)
    with pytest.raises(ValueError, match="Unknown review input"):
        view.payload(view.units, {})


def test_boundary_has_lifetimes_pending_calls_and_context_access(compiler, sample):
    sample = timeline(sample, 40)
    sample["replay"]["2"].append(
        {
            "op": "tool-call",
            "window": "scan",
            "tool": "file_list",
            "arguments": {},
            "content": "x",
            "duration-ticks": 20,
        }
    )
    config = ReviewCheck(prompt="review.md", view="tools")
    view = ReviewView(sample, compiler.compile(sample), config, compiler.tokenizer)
    later = [u for u in view.units if u["start"] == 10]
    payload = view.payload(later, {})
    boundary = payload["boundary"]
    assert boundary["tick"] == 10
    assert any(c["tool"] == "file_list" for c in boundary["pending_calls"])
    assert "chat" in boundary["history"]
    assert any(w["id"] == "chat" for w in boundary["open_windows"])
    extra = view.context(["window:chat"], payload)
    assert extra and all(u["end"] < 10 for u in extra)
    with pytest.raises(ValueError, match="Unknown review context"):
        view.context(["nonexistent"], payload)


def test_compact_json_and_dedup_preserve_strings():
    text = '  source code\n  "quoted" Ω ' * 100
    payload = {"a": text, "b": text, "short": "a b"}
    shared = deduplicate(payload)
    assert list(shared["content_pool"].values()) == [text]
    assert shared["a"] == shared["b"]
    assert json.loads(compact(payload)) == payload
    assert len(compact(shared)) < len(compact(payload))
    assert compact({"x": 1, "y": 2}) == '{"x":1,"y":2}'


def test_repairs_use_original_ids_and_are_atomic(sample):
    sample["replay"]["1"] = [
        {"op": "append", "window": "a", "content": "wrong fact", "source": "user"},
        {"op": "append", "window": "b", "content": "delete me", "source": "user"},
    ]
    before = copy.deepcopy(sample)
    result = apply_edits(
        sample,
        [
            {
                "action": "insert_before",
                "event_id": "replay[1][0]",
                "event": {"op": "open", "window": "extra"},
            },
            {
                "action": "replace_text",
                "event_id": "replay[1][0]",
                "field": "/content",
                "old": "wrong",
                "new": "correct",
            },
            {"action": "delete", "event_id": "replay[1][1]"},
        ],
    )
    assert result["replay"]["1"][1]["content"] == "correct fact"
    assert len(result["replay"]["1"]) == 2
    assert sample == before
    with pytest.raises(ValueError, match="unknown or deleted|Conflicting"):
        apply_edits(
            sample,
            [
                {"action": "delete", "event_id": "replay[1][1]"},
                {"action": "delete", "event_id": "replay[1][1]"},
            ],
        )
    with pytest.raises(ValueError, match="unambiguous"):
        apply_edits(
            sample,
            [
                {
                    "action": "replace_text",
                    "event_id": "replay[1][0]",
                    "field": "/content",
                    "old": "missing",
                    "new": "x",
                }
            ],
        )
    assert sample == before


def test_configuration_bounds():
    with pytest.raises(ValueError, match="overlap"):
        configured(events_per_chunk=2, overlap=2)
    with pytest.raises(ValueError, match="content_tokens"):
        configured(max_input_tokens=1000, content_tokens=2000)


@pytest.mark.parametrize("marker", ["{N_TICK:17}", "{WINDOW:b}", r"\{N_TICK:17}"])
def test_stream_fragments_have_actual_token_timing(compiler, sample, marker):
    sample["replay"]["0"][0]["window-type"] = "reasoning"
    text = "a" * 60 + marker + "b" * 140
    sample["replay"]["1"] = [{"op": "generate", "window": "a", "content": text}]
    plan = compiler.compile(sample)
    view = ReviewView(
        sample,
        plan,
        ReviewCheck(prompt="review.md", view="reasoning", content_tokens=64),
        compiler.tokenizer,
    )
    pieces = [u for u in view.units if u.get("field") == "/content"]
    assert len(pieces) >= 3
    assert all(u["timing_scope"] == "token_offsets" for u in pieces)
    assert pieces[0]["start"] == 1
    assert pieces[-1]["end"] == plan["max_tick"]
    assert pieces[-1]["start"] > pieces[0]["end"]
    assert "".join(u["text"] for u in sorted(pieces, key=lambda u: u["offset"])) == text
    assert all(
        u["event_start"] == 1 and u["event_end"] == plan["max_tick"] for u in pieces
    )


def test_correctness_sees_attachments_but_not_old_evaluations(compiler, sample):
    sample = timeline(sample, 1)
    sample["replay"]["0"].append(
        {
            "op": "invisible-tool-call",
            "tool": "file_write",
            "arguments": {"path": "facts.txt", "content": "Authoritative fact: 2+2=4"},
        }
    )
    plan = compiler.compile(sample)
    view = ReviewView(
        sample,
        plan,
        ReviewCheck(prompt="review.md", view="correctness"),
        compiler.tokenizer,
    )
    body = compact(list(view.groups({}, lambda _: True)))
    assert "Authoritative fact" in body and "Question 1" in body
    assert "DO_NOT_SEND" not in body
