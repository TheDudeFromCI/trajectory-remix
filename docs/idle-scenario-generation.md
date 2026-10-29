# Idle-work generation without local validation

Select the bundled **`idle_scenario`**; no scenario-directory option is needed. This is a
standalone content-first scenario with tag **`#idle_scenario`**. Only a Kidra role
exists: there is no fictional user, user-message generator, reference solution,
novelty judge, required chat reply, local semantic validator or local repair model.
Use the same inference-model tokenizer and context-budget settings described in
[Content-first generation](content-first-generation.md).

[PDF flow chart](idle-scenario-flow.pdf) · [Editable Graphviz source](idle-scenario-flow.dot)

## Inputs and coverage

Each attempt samples a domain, wake trigger and complication; the existing manual
selection and batch APIs also work. Domains include project maintenance, task
management, knowledge organization and scheduling. Triggers cover routine idle
ticks, TODO backlogs, fired alarms and changed project files. Complications include
straightforward work, blockers, competing priorities, conflicting records and a
quiet workspace. The conditions guide world generation rather than forcing extra
activity or a successful outcome.

The first bounded request creates a small workspace:

- **Author-only:** situation and original success criteria, retained with the
  generation record and portable review checklist.
- **Visible:** an external scheduler observation and standing policy in an
  `idle-context` document window. This may contain an already-fired alarm payload;
  it is not a generated Kidra action or an invented user request.
- **Sandbox:** virtual project files and optional setup operations using
  `note_save`, `memory_save` and `time_set`. Invisible setup does not reveal file
  contents or memories. TODOs are automatically exposed in the compiler-managed
  persistent `notes` window, with their real priorities, statuses and blockers.

Standing authority is part of the visible workspace policy. Missing information
or authority may block work; an idle agent need not contact an absent user or
pretend the task succeeded. No host processes, live services or real external
communications are used by this bundled flow.

## Adaptive action loop

One content request per cycle receives the currently open windows and the exact
public contracts for the supported file, TODO, memory, calculation, time and timer
tools. Author-only requirements and hidden full iteration history are not supplied.
The model returns:

1. One to three private reasoning streams, with independent analyses only when
   useful. IDs, headers, replay syntax, ticks and loss masks are supplied by code.
2. One parallel batch of independent tool requests, possibly empty.
3. Exact open window IDs to release after this batch's consumers finish.
4. `finished`, indicating whether this bounded idle session should stop quietly.

The assembler commits generated reasoning and request streams concurrently,
executes the real simulator, appends masked observations, and emits generated EOS
for the selected window releases. The next request sees the actual resulting
state before choosing another action. For example, reading a source, selecting an
edit from that evidence, observing the write, and completing its TODO are separate
decisions. A model must not preplan dependent actions inside the same parallel batch.
Repeated tool names reuse their already-visible contracts instead of appending
another identical schema on each content-first cycle; existing-mode behavior is
unchanged.

The model chooses which useful state to retain. A new compact summary must preserve
needed values, source versions, caveats and unfinished obligations before old
results/notes close. System, tools and automatic TODO notes cannot be released by
this action API. There is no unconditional private-memory reset between cycles.
Each published draft closes ordinary remaining windows on a copy; the generation
checkpoint keeps live windows for continuation. The automatic TODO window remains
under compiler control and is never manually opened or closed.

`finished=true` requires an empty tool batch and a private explanation of completed
work, legitimate blockers, or no current obligation. It does not mean every TODO is
done and does not validate the sample. The flow allows at most twelve decisions:
a quiet session can take just **two requests** (workspace plus stop), while a longer
session uses one workspace request plus its actual decision count. Exhausting the
limit retains an incomplete pending candidate, not an approved success.

## Run and inspect

Configure the generator endpoint and its local inference tokenizer, then choose
`idle_scenario` in the existing Single Sample or Batch UI. The run API accepts:

```json
{"scenario":"idle_scenario","batch_size":1,"max_parallel":1}
```

The action requests are sequential because each depends on previous observations;
raising `max_parallel` does not create independent inference branches inside this
flow. Multiple generated reasoning streams and independent tool requests still
execute concurrently in replay. Each request independently obeys the configured
context budget (default 8,192, including completion and reserve). Required context
is never silently truncated. Large retained windows/TODOs can exceed that budget;
external repair or a smaller world is then necessary.

For a repeatable **scripted offline demonstration**, install the inference extra and
use a fresh output directory:

```sh
python scripts/demo_idle_scenario.py --output-dir work/idle-demo
```

This runs the real runner, local Hugging Face fixture tokenizer, compiler, tools
and SQLite storage without making model API calls. The fixed alarm example reads
inventory/report files in parallel, corrects a stale count, reads it back, completes
the TODO after verification and stops without chat. It uses six scripted content
requests and writes `idle.sqlite`, `trajectory.json`, `report.json`, the fixture
inference tokenizer and the exact training tokenizer. All samples remain Pending
(level 0). Token counts describe the fixture; timings and outcomes are not evidence
of local-model speed or semantic quality.

## Review and recovery

Compiler checks, supervision, observation masks, BOS/EOS/NOOP and tick visibility
remain active. Raw/partial proposals, attempted failed constructions, original
requirements, diagnostics, usage and checkpoints use the existing content-first
journal. Cancellation/restart resume successful work without regenerating the
workspace. Schema-valid but unusable construction proposals remain available for
external repair rather than an automatic local semantic repair loop.

The saved shared checklist has layout, tools, private-reasoning and correctness
categories. Local model review is available separately through **Edit → Generation
→ Validate Trajectory**, using the same explicit service as MCP semantic validation. Through MCP, inspect `get_generation_record`, `review_trajectory` and
`get_review_checklist`; repair with `repair_trajectory` or targeted edits, then
record a review against the original checklist. No local inference is required.
External sign-off and human Ready approval remain separate; **only level 3 exports
training tokens**. The original world and checklist remain authoritative after
repairs and even when the scenario directory is unavailable.

## Current limits

- Each parallel tool batch joins before another model decision. A timer can overlap
  a read, but the flow cannot react to one result while another tool in that same
  batch is still pending. Scheduler alarm payloads supplied at startup are external
  observations; newly generated timers produce their own actual tool observations.
- Each private/tool stream uses a distinct window ID. The project’s configured
  distinct-window limit still applies after closure; the included demo uses 15
  windows. Initialize a project without `--max-window-slots` for unlimited slots,
  or choose enough capacity for the intended session length.
- There is no mid-session external world-injection step, incremental token-level
  intervention or synthetic specialist delegation in this initial flow.
- Choosing priorities, preserving sufficient summaries, handling alarms and stopping
  appropriately remain semantic training targets for external review. Cheap schema
  checks cannot prove those decisions correct.
- Model/world diversity and real inference performance still need endpoint runs.
  The included demonstration and regression fixtures establish mechanics, not
  production training quality.
