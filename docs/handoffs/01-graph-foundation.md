# 01 — Graph source, project contracts and revision-guarded edits

Read [CONTEXT](CONTEXT.md), then contracts **§2–3 and §5**. No prerequisites or
sampler attachments are needed. Suggested branch: `feat/graph-source-contracts`.

## Goal

Create the tested foundation for the new format without a legacy adapter or
premature UI rewrite. Finalize the engineering field grammar so downstream PRs
can share one source model, diagnostics model and edit service.

## Inspect first

`trajectory_remix/dataset.py`, `storage.py`, `compiler.py`, `validation.py`,
`server.py`, `commands.py` if present, relevant storage/validation/API tests and
`pyproject.toml`. Reuse FastAPI/Pydantic/SQLite; don't replace the stack.
Current code is a reference, not a compatibility requirement.

## Scope

- New versioned tokenizer-agnostic graph source with stable trajectory/window/
  event IDs, dependencies, whole-event supervision and delivery/framing fields.
- Event variants: agent/external text, tool call/response and structured chat.
  No authored special-token strings, NOOP events, tick anchors or sandbox effects.
- Primary fields contain intrinsic interpretation/compilation data. Metadata
  entirely optional; stage/split/signoffs/groups/targets/metrics remain metadata.
- Structural validation: missing/cyclic references, incomparable same-window
  writers, invalid closure/submission/framing. Errors block compilation later;
  graph drafts can retain diagnostics. DTO/JSON failures reject malformed input.
- Tools: input-schema warnings only; free-form outputs; no execution. Partial
  arguments need not parse until submission, and abandoned prefixes stay valid.
- Validate project/settings records for tool catalog, reasoning levels, tokenizer
  profiles, endpoints/checklists/matrix references and log settings. Do not require
  a training tokenizer at project creation or instantiate it on graph reads.
- New-format storage identity, immutable revision/commit identities, per-source
  CAS edits and indexed tool usage. Metadata-only edits do not change intrinsic
  source hash; no whole-project revision conflict for an unrelated sample.
- Small application command/query layer for read/create/patch/event/dependency
  operations. Dashboard REST and MCP will share it in 03.

Use incremental foundation modules rather than wiring the old UI to partially
implemented new behavior. No migration or old format readers are required in the
new path. New public docs/examples should be concise and self-contained.

## Required small checks

1. Parse/dump a minimal graph with metadata absent, empty, and unknown annotations.
2. Intrinsic hash unaffected by metadata; changed content/flags changes it.
3. Root events, dependency completion structure and same-window comparability;
   cycles and missing refs produce stable field/event diagnostics.
4. Deleting an event preserves remaining events/references and reports breakage;
   explicit removal fixes the reference without silent rewiring.
5. Fragmented request, submission, cancellation, combined tool end + EOS, and
   no output after closure. No cross-event semantic contamination indicators.
6. CAS preserves newer source and returns a conflict; another trajectory's edit
   does not invalidate the save. Old versions/settings are not silently mutated.
7. Project creation and ordinary graph operations make no tokenizer/compiler calls.

Tests stay tiny/deterministic; no load generators or throughput measurements.

## Boundaries / downstream

No compiled scheduler/KTREMIX writer (02), complete studio (03), sampler adapter
(04), operational worker implementation (05), or real model calls (06).
Coordinate typed IDs/revisions/diagnostics now; don't encode workflow metadata as
mandatory compiler fields. Runtime ownership tables may be extended in 05.

Deliver a focused draft PR plus source/schema/edit examples and exact check
results. Update the contract draft/downstream handoffs for actual field names;
keep this temporary packet out of the implementation PR diff. Do not merge.
