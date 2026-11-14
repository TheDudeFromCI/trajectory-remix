"""Small deterministic graph scheduling and visibility fixtures, never timed."""
import copy
import json
import uuid

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers, decoders

from trajectory_remix.graph.models import GraphSource, TokenizerProfile, REQUIRED_CONTROLS
from trajectory_remix.graph.compiler import GraphCompiler, CompileError
from trajectory_remix.graph import ktremix as k


def profile(reverse=False):
    ordinary = sorted(pre_tokenizers.ByteLevel.alphabet())
    if reverse:
        ordinary.reverse()
    controls = sorted(REQUIRED_CONTROLS)
    vocab = {t: i for i, t in enumerate(ordinary + [f"<{c}>" for c in controls])}
    tok = Tokenizer(models.BPE(vocab, []))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)
    tok.decoder = decoders.ByteLevel()
    tok.add_special_tokens([f"<{c}>" for c in controls])
    return TokenizerProfile(name="bytes", definition=json.loads(tok.to_str()),
                            special_tokens={c: vocab[f"<{c}>"] for c in controls})


def source(events, windows=None, metadata=None):
    ids = {e["window"] for e in events.values()}
    data = dict(format="kidra-trajectory-graph/1", id=str(uuid.UUID(int=1)),
                windows=windows or {w: dict(purpose="document", agent_writable=True) for w in ids},
                events=events)
    if metadata is not None:
        data["metadata"] = metadata
    return GraphSource.model_validate(data)


def event(kind, window, content="x", after=(), **kw):
    return dict(kind=kind, window=window, content=content, after=list(after), **kw)


def compile_source(s, **kw):
    c = GraphCompiler(profile(), active_limit=3, **kw)
    a, meta = c.compile(s)
    raw, index = k.encode_sample(s.id, a)
    k.Sample(raw, index).validate()
    return c, a, meta


def emissions(c, s):
    return [(f.tick, e) for f in c.frames(s) for e in (*f.inputs, *f.outputs) if e.event]


def test_source_phase_headers_and_metadata_independence():
    s = source({"in": event("external_text", "in"),
                "out": event("agent_text", "out", after=["in"], close_window=True)})
    c, a, m = compile_source(s)
    rows = emissions(c, s)
    first = {e.event: tick for tick, e in reversed(rows)}
    assert first == {"in": 0, "out": 0}
    external = next(e for _, e in rows if e.event == "in")
    assert external.tokens[0].control == "ctx_begin"
    assert external.tokens[-2].control == "ctx_end"
    assert len([t for t in external.tokens if t.header]) > 1
    assert all(len(e.tokens) == 1 for _, e in rows if e.model)
    assert next(e for _, e in rows if e.event == "out").tokens[0].control == "ctx_begin"
    changed = s.model_copy(update={"metadata": {"stage": 3, "split": "test"}})
    assert c.compile(changed)[0] == a
    assert c.preview(s, max_frames=2)["truncated"]
    assert c.preview(s)["cache_key"] == c.preview(changed)["cache_key"]
    assert m["peak_active_windows"] == 2


@pytest.mark.parametrize("immediate,expected", [(True, 0), (False, 1)])
def test_external_chain_checkbox(immediate, expected):
    s = source({"a": event("external_text", "a"),
                "b": event("external_text", "b", after=["a"],
                           delivery={"chain_immediately": immediate}),
                "c": event("agent_text", "c", after=["b"], close_window=True)})
    c, _, _ = compile_source(s)
    rows = emissions(c, s)
    assert next(t for t, e in rows if e.event == "b") == expected
    assert next(t for t, e in rows if e.event == "c") == expected


def test_agent_dependency_and_opening_stagger_and_reuse():
    s = source({"a": event("agent_text", "a", close_window=True),
                "b": event("agent_text", "b", close_window=True),
                "z": event("external_text", "z", after=["a", "b"])})
    c = GraphCompiler(profile(), active_limit=2)
    a, m = c.compile(s)
    raw, index = k.encode_sample(s.id, a)
    k.Sample(raw, index).validate()
    rows = emissions(c, s)
    assert next(t for t, e in rows if e.event == "b") == 1
    completed = max(t for t, e in rows if e.event in ("a", "b"))
    assert next(t for t, e in rows if e.event == "z") == completed + 1
    assert len(a["window_slots"]) > len(set(a["window_slots"]))
    assert c.compile(s)[0] == a
    # Exactly one Available at every query, including opening and closing frames.
    for q in a["query_frontiers"]:
        count = sum(o <= q < end and q < active and flags & k.INITIALLY_AVAILABLE != 0
                    for o, end, active, flags in zip(a["window_open_frontiers"],
                        a["window_close_frontiers"], a["window_activation_frontiers"], a["window_flags"]))
        assert count == 1


def test_collision_and_capacity_errors():
    s = source({"a": event("agent_text", "w"), "b": event("agent_text", "w")})
    with pytest.raises(CompileError, match="unordered_window_writers"):
        GraphCompiler(profile()).compile(s)
    s = source({"a": event("external_text", "a"), "b": event("external_text", "b")})
    with pytest.raises(CompileError, match="capacity"):
        GraphCompiler(profile(), active_limit=1).compile(s)


def test_eos_is_label_and_pre_contents_visible_to_simultaneous_peer():
    # Both external openings are atomic; subsequent outputs have no header delay.
    s = source({"ia": event("external_text", "a", "OLD"),
                "ib": event("external_text", "b", "PEER"),
                "a": event("agent_text", "a", "", ["ia"], close_window=True),
                "b": event("agent_text", "b", "yz", ["ib"], close_window=True)})
    c, a, m = compile_source(s)
    eos = next(i for i, f in enumerate(a["row_flags"]) if f & k.EOS)
    wid = a["window_ids"][eos]
    assert not a["row_flags"][eos] & k.DURABLE
    peers = [i for i, f in enumerate(a["row_flags"]) if f & k.MODEL and a["window_ids"][i] != wid
             and not f & k.NOOP]
    old = [i for i, w in enumerate(a["window_ids"]) if w == wid and a["row_flags"][i] & k.DURABLE]
    q0, q1 = a["query_frontiers"][peers[0]], a["query_frontiers"][peers[1]]
    assert all(a["visible_from"][i] <= q0 < a["visible_until"][i] for i in old)
    assert all(not a["visible_from"][i] <= q1 < a["visible_until"][i] for i in old)
    # Same-pass peers cannot see each other's current labels.
    assert a["query_frontiers"][eos] == q0
    assert a["visible_from"][peers[0]] > q0


def test_replacement_streaming_mask_and_event_boundaries():
    s = source({"a": event("external_text", "w", "OLD"),
                "b": event("external_text", "w", "NEW", ["a"], replace_body=True,
                           delivery={"mode": "streamed"}),
                "c": event("agent_text", "v", "!", ["b"], close_window=True)})
    c, a, m = compile_source(s)
    rows = emissions(c, s)
    assert [t for t, e in rows if e.event == "b"] == [0, 1, 2]
    assert all(sum(not t.header for t in e.tokens) == 1 for _, e in rows if e.event == "b")
    wid = m["logical_windows"].index("w")
    body = [i for i, w in enumerate(a["window_ids"]) if w == wid and
            a["token_ids"][i] in [c.text(x)[0].id for x in "OLDNEW"]]
    assert all(a["loss_weights"][i] == 0 for i in body)
    newest = [i for i in body if a["token_ids"][i] in [c.text(x)[0].id for x in "NEW"]]
    assert all(a["visible_until"][i] == k.FOREVER for i in newest)


def test_literal_controls_profile_independence_and_noop_sleep():
    s = source({"a": event("external_text", "w", "<eos>"),
                "b": event("external_text", "w", "x", ["a"], delivery={"chain_immediately": False})})
    c, a, _ = compile_source(s)
    literal_ids = [t.id for t in c.text("<eos>")]
    assert c.controls["eos"] not in literal_ids
    b = GraphCompiler(profile(True), active_limit=3).compile(s)[0]
    assert a["token_ids"] != b["token_ids"]
    for field in ("query_frontiers", "visible_from", "visible_until", "frame_offsets", "row_flags"):
        assert a[field] == b[field]
    # There is only one all-NOOP decision after the last context change.
    frames = list(c.frames(s))
    assert not (not frames[-1].inputs and not frames[-2].inputs)


def test_invalid_tokenizer_and_preview_cancel():
    p = profile()
    broken = p.model_copy(update={"definition": {**p.definition, "model": {"type": "bad"}}})
    with pytest.raises(CompileError, match="Invalid tokenizer"):
        GraphCompiler(broken)
    s = source({"a": event("agent_text", "a", close_window=True)})
    from trajectory_remix.graph.compiler import Cancelled
    with pytest.raises(Cancelled):
        GraphCompiler(p).preview(s, cancelled=lambda: True)


@pytest.mark.parametrize("mode", ["instant", "streamed"])
def test_tool_result_framing_event_masks_and_free_form(mode):
    s = source({
        "request-a": event("tool_call", "request", '{"x":', call_id="c", tool="lookup",
                           insert_tool_end=False, supervision={"masked": True}),
        "request-b": event("tool_call", "request", "1}", ["request-a"], call_id="c", tool="lookup",
                           insert_tool_begin=False),
        "answer": event("tool_response", "response", "not json", ["request-b"], call_id="c",
                        delivery={"mode": mode}, close_window=True,
                        supervision={"masked": False, "weight": 0.3}),
    })
    c, a, _ = compile_source(s)
    rows = emissions(c, s)
    req_a = [e for _, e in rows if e.event == "request-a"]
    req_b = [e for _, e in rows if e.event == "request-b"]
    assert all(e.weight == 0 for e in req_a)
    assert all(e.weight == 1 for e in req_b)
    req_controls = [t.control for e in req_a + req_b for t in e.tokens if t.control]
    assert req_controls.count("tool_begin") == req_controls.count("tool_end") == 1
    answer = [(tick, e) for tick, e in rows if e.event == "answer"]
    assert answer[0][0] == max(t for t, e in rows if e.event == "request-b") + 1
    assert all(e.weight == 0.3 for _, e in answer)
    assert len(answer) == (1 if mode == "instant" else len("not json") + 3)
    assert answer[0][1].tokens[0].header
    assert answer[-1][1].tokens[-1].control == "eos"


def test_chat_atomic_framing_and_typed_window_reference():
    s = source({"doc": event("external_text", "doc", "a"),
                "chat": event("chat_message", "chat", [dict(kind="window_ref", window="doc")],
                              ["doc"], chat={"speaker": "alice"})},
               windows={"doc": dict(purpose="document", agent_writable=False),
                        "chat": dict(purpose="chatroom", agent_writable=False)})
    c, a, meta = compile_source(s)
    rows = emissions(c, s)
    chat = next(e for _, e in rows if e.event == "chat")
    controls = [t.control for t in chat.tokens if t.control]
    assert controls == ["ctx_begin", "ctx_end", "msg_begin", "window_ref", "msg_end"]
    assert all(e.weight == 0 for _, e in rows)
    assert len([f for f in c.frames(s)]) == 1  # one all-NOOP decision, then sleep


def test_replacement_atomic_cutoff_and_retained_prefix():
    s = source({"old": event("external_text", "w", "OLD"),
                "new": event("external_text", "w", "NEW", ["old"], replace_body=True,
                             supervision={"masked": False})})
    c, a, _ = compile_source(s)
    old = next(i for i, t in enumerate(a["token_ids"]) if t == c.text("O")[0].id)
    new = next(i for i, t in enumerate(a["token_ids"]) if t == c.text("N")[0].id)
    assert a["visible_until"][old] == a["visible_from"][new]
    assert a["token_ids"][a["previous_rows"][new]] == c.controls["ctx_end"]


def test_event_boundary_prevents_bpe_merge_and_mask_blending():
    p = profile()
    vocab = p.definition["model"]["vocab"]
    vocab["ab"] = len(vocab)
    p.definition["model"]["merges"] = [["a", "b"]]
    c = GraphCompiler(p)
    assert len(c.text("ab")) == 1
    s = source({"a": event("external_text", "w", "a"),
                "b": event("external_text", "w", "b", ["a"], supervision={"masked": False})})
    a, _ = c.compile(s)
    assert vocab["ab"] not in a["token_ids"]
    b = next(i for i, token in enumerate(a["token_ids"]) if token == vocab["b"])
    assert a["loss_weights"][b] == 1


def test_preview_and_export_have_identical_schedule_and_no_side_effects():
    s = source({"a": event("agent_text", "a", close_window=True)})
    c = GraphCompiler(profile(), seed=2)
    original = copy.deepcopy(s.to_dict())
    preview = c.preview(s)
    a, _ = c.compile(s)
    assert preview["frames"] == list(c.frames(s))
    assert len(preview["frames"]) == len(a["frame_offsets"]) - 1
    assert s.to_dict() == original
    assert c.preview(s)["cache_key"] != GraphCompiler(profile(), seed=3).preview(s)["cache_key"]


def test_frontier_visibility_matches_legacy_predicate_on_shared_phase_semantics():
    # The old predicate is a regression oracle only; no replay source is compiled.
    from trajectory_remix.compiler import visible_to
    s = source({"i": event("external_text", "a", "P"),
                "a": event("agent_text", "a", "xy", ["i"], close_window=True),
                "j": event("external_text", "b", "Q"),
                "b": event("agent_text", "b", "uvw", ["j"], close_window=True)})
    c, a, _ = compile_source(s)
    frames = list(c.frames(s))
    # Map each numeric row to a frame; BOS rows are phase A conditioning, while
    # all input/body rows precede the simultaneous phase D emissions.
    rows = []
    for fi, (start, stop) in enumerate(zip(a["frame_offsets"], a["frame_offsets"][1:])):
        rank = 0
        for r in range(start, stop):
            flags = a["row_flags"][r]
            phase = "D" if flags & k.MODEL else "B"
            rows.append(dict(index=r, tick=frames[fi].tick, phase=phase, input_rank=rank,
                             window=a["window_ids"][r], durable=bool(flags & k.DURABLE)))
            rank += 1
    windows = [dict(id=w, open=0, close=None) for w in range(len(a["window_slots"]))]
    for w, close in enumerate(a["window_close_frontiers"]):
        if close != k.FOREVER:
            windows[w]["close"] = max(row["tick"] for row in rows if row["window"] == w)
    # Restrict to ordinary external text and model body rows; derived reserve
    # BOS insertions occur after commit and have no exact old-source analogue.
    candidates = [r for r in rows if a["token_ids"][r["index"]] in [c.text(t)[0].id for t in "PQxyuvw"]]
    for query in candidates:
        if query["phase"] != "D":
            continue
        q = a["query_frontiers"][query["index"]]
        for key in candidates:
            i = key["index"]
            actual = bool(a["row_flags"][i] & k.DURABLE and a["visible_from"][i] <= q < a["visible_until"][i])
            assert actual == visible_to(query, key, windows)


def test_future_and_closed_reference_resolves_to_actual_reusable_slot():
    s = source({"ref": event("agent_text", "a", [dict(kind="window_ref", window="b")], close_window=True),
                "b": event("external_text", "b", "data", ["ref"], close_window=True),
                "past": event("agent_text", "c", [dict(kind="window_ref", window="b")], ["b"], close_window=True)})
    c = GraphCompiler(profile(), active_limit=1)
    a, meta = c.compile(s)
    raw, entry = k.encode_sample(s.id, a)
    k.Sample(raw, entry).validate()
    slot = a["window_slots"][meta["logical_windows"].index("b")]
    refs = [i for i, token in enumerate(a["token_ids"]) if token == c.controls["window_ref"]]
    assert len(refs) == 2
    for ref in refs:
        # Agent reference digits are separated by reserve NOOP rows in the arrays.
        own = [i for i in range(ref + 1, len(a["token_ids"])) if a["window_ids"][i] == a["window_ids"][ref]
               and a["row_flags"][i] & k.DURABLE][:8]
        assert [a["token_ids"][i] for i in own] == [c.text(char)[0].id for char in f"{slot:08x}"]


def test_external_instant_closure_can_reuse_slot_in_same_input_phase():
    s = source({"a": event("external_text", "a", "x", close_window=True),
                "b": event("external_text", "b", "y", close_window=True)})
    c = GraphCompiler(profile(), active_limit=1)
    a, meta = c.compile(s)
    raw, index = k.encode_sample(s.id, a)
    k.Sample(raw, index).validate()
    assert meta["peak_active_windows"] == 1
    assert a["window_slots"][1] == a["window_slots"][2]
    assert a["window_close_frontiers"][1] <= a["window_open_frontiers"][2]
