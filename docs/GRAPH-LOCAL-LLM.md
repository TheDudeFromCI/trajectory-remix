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

## 2. Run one trajectory first

```bash
python scripts/graph_local_llm_smoke.py --user-machine --config /path/to/local-config.json \
  --output /tmp/remix-local-smoke-01 --count 1
python -m trajectory_remix graph-studio \
  --dataset /tmp/remix-local-smoke-01/project.sqlite --port 8765
```

Open the studio and inspect the source, stage, evaluations, each job's log and `report.json`. If a job or endpoint
pauses, the script stops. Resume it in the studio yourself. Output directories are never reused, so
the earlier evidence stays safe.

## 3. Run the rest

Use a fresh directory. Five trajectories is enough to see the usual corrections and repairs,
with no parallel or load experiment:

```bash
python scripts/graph_local_llm_smoke.py --user-machine --config /path/to/local-config.json \
  --output /tmp/remix-local-smoke-02 --count 5
```

Look at outcomes, not just final stages. For each trajectory, check that:

- the draft (in `metadata.draft`) reads like a real episode and covers the proposal;
- the assembled graph keeps the draft's facts, closes its reasoning windows and has at most
  a few assembly fix turns (`metadata.assembly_fix`);
- chat calls have exactly one linked, masked delivery (`:echo`) in the chatroom;
- review verdicts cite real evidence, and nothing is claimed before it is shown.

For each sample, write down the seed, job and source IDs, endpoint and model names, final stage, criterion
evidence, unexpected pauses or format corrections, and the event IDs where prompts need work. Keep credentials out
of reports. The request table keeps source and prompt identities for debugging.

## 4. Try manual review and repair (on a copy)

Work on a **copy** of a smoke project:

- **Stage rules.** Pending + Standard + repair off runs Fast only. Candidate + Standard runs Critical. Validated or
  Ready + Standard skips. A forced Fast pass keeps a higher stage, and a forced failure follows its tier's failure
  transition. Locked sources are skipped.
- **A planted flaw.** Introduce a small, concrete inconsistency in a detached sample, set it to Unsuccessful or
  Rejected by hand, and run Standard with repair on. Watch the continuing event patches and the single combined
  verdict. There should be no extra same-model validation after a successful repair. Whether the flaw is noticed
  depends on the model, so record misses.
- **Repair limits.** Use an impossible criterion to elicit a negative verdict: the session should keep
  going until the model gives up. Set a small `repair_context_bytes` and `repair_rounds: 1` to see the round
  cap end the repair detached as Unfinished, keeping completed edits. Test a provider-reported context limit
  separately if your server supports it.
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

## Generate from the studio with a local server

Use this to check generation end to end on your own hardware before paying for hosted
models: it costs nothing but runs slowly. Any OpenAI-compatible server works. Notes for
PolyStrata and llama.cpp follow; the steps after them are the same for both.

### PolyStrata

[PolyStrata](https://github.com/VecSzn/PolyStrata) serves mixture-of-experts GGUF models
through an OpenAI-compatible API at `http://127.0.0.1:8080/v1`. Locally it accepts any
API key and any model name. Its README does not document three things Remix relies on,
so check them with a one-trajectory run (see below) before a larger one:

| Not documented | What to set in Remix |
| --- | --- |
| JSON-schema output (`response_format`) | Start with **Structured output: Auto**. If replies come back in the wrong shape or the request is refused, switch to **JSON object**, then **Prompt only**. Both put the schema in the prompt. |
| The request field for reasoning effort | The README names reasoning levels off, low, medium and high. Try `{"reasoning_effort": "none"}` in **Extra request fields**, or `"low"` if `none` is refused, and check the **Reasoning** column in **Usage**. |
| Parallel requests | Only models configured with `"parallel"` in their `strata-<model>.json` answer more than one request at a time. Otherwise set **Concurrent requests** to 1. |

Use the context size chosen when the model was installed as the **Context limit**.

### llama.cpp

Start `llama-server` with the model's own chat template:

```bash
llama-server -m /path/to/model.gguf --jinja -c 65536 -np 2 --port 8000
```

`-c` is shared between the `-np` parallel slots, so this gives each request 32,768
tokens: use that as the **Context limit** and `-np` as **Concurrent requests**. For
Qwen3-family hybrid models, `{"chat_template_kwargs": {"enable_thinking": false}}` in
**Extra request fields** turns thinking off.

### Set up and run

1. **Add an endpoint** in **Settings → Models & defaults**:

   | Field | Value |
   | --- | --- |
   | Base URL | `http://127.0.0.1:8080/v1` (PolyStrata) or `http://127.0.0.1:8000/v1` (llama.cpp above) |
   | Model identifier | The model's name, for example its GGUF file name; it is recorded with each response |
   | Concurrent requests | What the server can answer at once (see above) |
   | Context limit (tokens) | The server's context per request |
   | Max output tokens | `8192` (reasoning models need room to think before the JSON) |
   | Provider options → Bytes per token | `3` |
   | Provider options → Structured output | **Auto** to start (see above) |
   | Provider options → Sticky sessions | Off (a single local server needs no routing) |
   | Provider options → Extra request fields | The server's setting that turns thinking off (see above) |

   Turning thinking off matches the hosted pilot and avoids replies cut off at the output
   limit. If you leave it on, raise **Max output tokens**.

2. **Assign the Draft role** to the endpoint (and **Assembly** too, or leave it to fall back to
   Draft), leave the review roles empty, and save.

3. **Check one trajectory first.** In **Generate**, start 1 attempt. In **Jobs**, the log should
   show **Draft written** and **Graph assembled**. A refused request shows the server's own
   error message; a reply in the wrong shape shows up as a correction with the reason.

4. **Run a few trajectories** from **Generate** (for example 5 attempts). In **Jobs**, the
   **Usage** tab shows tokens and time. A local server reports no cost, so that column
   shows "—". **Model time** adds up every request's wait; the wall time line shows how long
   the run took from first to last response.

If replies are **cut off at the output limit** even with a few thousand tokens, the model
is almost certainly still thinking. Open the step's **Reply** tab: it shows the start of the
reasoning or reply text. Try another thinking-off field in **Extra request fields** and save:
with **Use current settings** on, the next step uses it straight away. **Stop** cancels a
step still waiting on the model.

What to look at before moving to a hosted model: whether trajectories finish, how many
format corrections and pauses the logs show, typical events per trajectory and prompt
tokens per request, and whether the content reads right in the **Editor**.
