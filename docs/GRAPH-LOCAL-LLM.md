# Local-model validation

This is a hands-on checklist for trying generation and review against a model **you** run. It hasn't been run
against a live model by the implementation team, so treat it as a recipe to follow and report on. It needs no
benchmark or training run. The smoke script refuses to run in CI and requires an explicit `--user-machine`.

A reported pass is a model's verdict, not proof of content quality. Passing schema and graph checks also doesn't
certify tool coherence, objective coverage or truthful reasoning, so record what you find before using samples.

## 1. Set up

1. Install the project and start your own OpenAI-compatible server with structured JSON-schema output.
2. Copy [`examples/graph_local_llm.json`](../examples/graph_local_llm.json) to a local config and set each base URL
   and model. The three roles (generator, fast, critical) may share one server, and the example uses concurrency 1.
   Project toggles let you opt out of review and repair. No cloud account is needed.
3. If your server needs a key, set `REMIX_API_KEY_LOCAL_GENERATOR`, `REMIX_API_KEY_LOCAL_FAST` and
   `REMIX_API_KEY_LOCAL_CRITICAL` in the environment.

## 2. Run one small case first

```bash
python scripts/graph_local_llm_smoke.py --user-machine --config /path/to/local-config.json \
  --output /tmp/remix-local-smoke-01 --cases ordinary
python -m trajectory_remix graph-studio \
  --dataset /tmp/remix-local-smoke-01/project.sqlite --port 8765
```

Open the studio and inspect the source, stage, evaluations, each job's log and `report.json`. If a job or endpoint
pauses, the script stops creating cases. Resume it in the studio yourself. Output directories are never reused, so
the earlier evidence stays safe.

## 3. Run the rest

Use a fresh directory. This is seven attempts in total, with no parallel or load experiment:

```bash
python scripts/graph_local_llm_smoke.py --user-machine --config /path/to/local-config.json \
  --output /tmp/remix-local-smoke-02 --cases multi-user-wakeup \
  partial-instant partial-streamed document-replacement filtered-chat rejected-chat
```

Look at outcomes, not just final stages:

| Case | What to check |
| --- | --- |
| `ordinary` | A user enters a read-only chatroom, the agent submits chat through a tool, and there is exactly one linked, masked message echo. |
| `multi-user-wakeup` | Alice and Bob keep their identities and ownership, and a watcher alarm fires with no fabricated request. The autonomous-to-user transition stays coherent. |
| `partial-instant`, `partial-streamed` | An intentionally incomplete chat argument spans a document lookup, and the retrieval influences only the later fragment. Response begin/end, delivery and masks are right. |
| `document-replacement` | A new TODO snapshot replaces the old body, later prompts use the current visible windows, and any retained fact needs an explicit visible copy. |
| `filtered-chat` | The automatic echo is off, there's one explicitly transformed delivery, and no duplicate uncensored echo. |
| `rejected-chat` | The echo is off and an authored rate-limit failure results in no chatroom delivery. |

For each sample, write down the case and seed, job and source IDs, endpoint and model names, final stage, criterion
evidence, unexpected pauses or format corrections, and the event IDs where prompts need work. Keep credentials out
of reports. Actor prompts never contain the full proposal, review transcript or private world data, and the request
table keeps frontier, source and prompt identities for debugging.

## 4. Try manual review and repair (on a copy)

Work on a **copy** of a smoke project:

- **Stage rules.** Pending + Standard + repair off runs Fast only. Candidate + Standard runs Critical. Validated or
  Ready + Standard skips. A forced Fast pass keeps a higher stage, and a forced failure follows its tier's failure
  transition. Locked sources are skipped.
- **A planted flaw.** Introduce a small, concrete inconsistency in a detached sample, set it to Unsuccessful or
  Rejected by hand, and run Standard with repair on. Watch the continuing event patches and the single combined
  verdict. There should be no extra same-model validation after a successful repair. Whether the flaw is noticed
  depends on the model, so record misses.
- **Repair limits.** Use an impossible criterion to elicit a negative verdict, or set a small
  `repair_context_bytes` on a new operation. A failure ends detached as Unfinished, keeping completed edits. Test a
  provider-reported context limit separately if your server supports it.
- **Stale indicators.** Change a checklist and edit a reviewed source. Stale markers appear, stages don't move and
  nothing re-reviews until you ask.
- **Failures.** Stop the endpoint mid-call. After its retries run out, the queue pauses with no quality verdict and
  no fallback model, so resume it manually. Restart Remix mid-request: committed events and patches survive,
  unaccepted text is discarded, and the original inputs are reused after a manual Resume.
- **Pause, cancel, history.** Pause or cancel a tiny run and inspect ownership. Cancel is terminal. Undo and Redo of
  finished manual review edits restores saved results without model calls, and intervening edits cause skips.

Malformed review JSON, unknown evidence references, format limits and stage and lock behavior are covered by offline
tests. To check real server errors, read the full response and finish reason yourself, and don't treat API errors as
negative quality verdicts.

The default prompts and free-form response construction are a baseline to iterate on. The separate 25k-stored /
20-active and full-training gates are outside this guide, and nothing here starts them.
