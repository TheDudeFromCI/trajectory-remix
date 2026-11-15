# Local-LLM validation (user machine)

These checks are prepared for you to run. They have **not** been run against a
live model by the implementation agent. No benchmark or training is needed. The script refuses CI and requires explicit
`--user-machine` opt-in.

1. Install the project and use your own running OpenAI-compatible local server
   with structured JSON-schema output. Copy `examples/graph_local_llm.json` to a
   local config and set each base URL/model. The three logical roles may use the
   same local server; concurrency is 1 per endpoint in this small example. Use
   project toggles to opt out of review/repair. No OpenRouter account is required.
   Supply authentication only through `REMIX_API_KEY_LOCAL_GENERATOR`,
   `REMIX_API_KEY_LOCAL_FAST`, `REMIX_API_KEY_LOCAL_CRITICAL` if needed.
2. Start with one small case and a fresh output directory:

   ```bash
   python scripts/graph_local_llm_smoke.py --user-machine --config /path/to/local-config.json \
     --output /tmp/remix-local-smoke-01 --cases ordinary
   python -m trajectory_remix graph-studio \
     --dataset /tmp/remix-local-smoke-01/project.sqlite --port 8765
   ```

   Open `http://127.0.0.1:8765`. Inspect source, stage, evaluations, independent
   job logs and `report.json`. A reported pass is a model verdict, not proof of
   content quality. If a job/endpoint pauses, the script stops creating cases;
   open the studio and explicitly Resume the endpoint/job as appropriate.
   Reusing an output database is refused to protect the prior evidence.
3. Run the remaining cases in another fresh directory, still as a small content
   smoke check (seven attempts total, no parallel/load experiment):

   ```bash
   python scripts/graph_local_llm_smoke.py --user-machine --config /path/to/local-config.json \
     --output /tmp/remix-local-smoke-02 --cases multi-user-wakeup \
     partial-instant partial-streamed document-replacement filtered-chat rejected-chat
   ```

Inspect these outcomes rather than only final stages:

| Case | Inspect |
| --- | --- |
| ordinary | User enters a read-only chatroom; agent submits chat through a tool; exactly one linked masked MSG echo |
| multi-user-wakeup | Alice/Bob identities, ownership and a watcher alarm without a fabricated request; autonomous/user phase transition stays coherent |
| partial-instant / partial-streamed | An intentionally incomplete chat argument spans a document lookup; retrieval influences only the later fragment; response begin/end, delivery and source masks are correct |
| document-replacement | New TODO snapshot replaces old body; later actor prompt uses current visible windows; any retained facts require an explicit visible copy |
| filtered-chat | Automatic echo disabled; one explicitly transformed delivery; no duplicate uncensored echo |
| rejected-chat | Automatic echo disabled; authored rate-limit failure with no chatroom delivery |

For each sample, report the case/seed, job and source IDs, endpoint/model names,
final stage/outcome, criterion evidence, unexpected pauses/format corrections,
and specific event IDs requiring prompt iteration. Keep credentials out of reports.
The graph request table retains frontier/source/prompt identities for debugging;
actor prompts contain no full proposal, review transcript or privileged world data.

Additional manual review/repair checks on a **copy** of a smoke project:

- Pending + Standard + repair off: Fast only. Candidate + Standard: Critical.
  Validated/Ready + Standard: skip. Force Fast pass preserves a higher stage;
  force failure follows its tier's failure transition. Locked sources skip.
- Introduce a small concrete content inconsistency in a detached sample, set
  Unsuccessful or Rejected manually, and run Standard with repair enabled.
  Inspect continuing event patches and the single combined tier verdict. There
  should be no extra same-model validation call after repair success. Model
  behavior determines whether a deliberate flaw is detected; record misses.
- Use a deliberately impossible criterion to elicit a negative repair verdict,
  or set a small `repair_context_bytes` in a new operation to exercise the local
  bound. Failure ends detached Unfinished with completed edits retained. Test a
  provider-reported context limit separately if your local server supports it.
- Change one checklist and edit one reviewed source. Stale indicators appear,
  stages remain unchanged and no review runs until requested manually.
- Stop the local endpoint during a call. After transport retry exhaustion its
  queue pauses without a quality verdict or fallback model. Resume manually.
  Restart Remix mid-request: committed events/patches survive; unaccepted text
  is discarded and the original event inputs are reused after manual Resume.
- Pause/cancel a tiny run and inspect source/ownership. Cancel is terminal and
  cannot reattach or replace an attempt. Undo/Redo completed manual review edits
  restores saved results without model calls; intervening edits produce skips.

Malformed review JSON, unknown evidence references, format bounds and exact
stage/lock behavior are deterministically covered by offline tests. To validate
actual server error reporting, inspect its complete response/finish reason
locally; do not reinterpret provider/API errors as negative quality verdicts.

The default prompts and free-form response construction remain a baseline for
local iteration. Passing schema/graph checks does not certify tool coherence,
objective coverage, truthful reasoning or training value. Record those findings
before using samples. The separate 25k-stored/20-active and full-training gates
are outside this smoke guide; nothing here starts or schedules them.
