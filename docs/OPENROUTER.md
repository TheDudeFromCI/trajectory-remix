# Generate with OpenRouter

This page sets up Remix to generate trajectories with small open-weight models on
[OpenRouter](https://openrouter.ai), then walks through a 10-trajectory pilot and what to
check before running more. Nothing here has been run against a live model by the
implementation team: treat it as a checklist to follow and report on.

## What you need

- An OpenRouter API key with a few dollars of credit. A 10-trajectory pilot on the models
  below costs well under $1.
- A model that supports **structured outputs** and, ideally, a **cache discount**. Use the
  model checker below rather than picking by price alone.

To check generation for free first, run a few trajectories on a local server: see
[Generate from the studio with a local server](GRAPH-LOCAL-LLM.md#generate-from-the-studio-with-a-local-server).

## 1. Choose a model

Run the checker. It reads OpenRouter's public model list (no key needed, no generation):

```bash
python scripts/openrouter_models.py mistralai/mistral-small-3.2-24b-instruct deepseek/deepseek-v4-flash
```

For each model it reports whether Remix can use it, how its reasoning must be set, the
exact **extra request fields** to use, and an estimated Draft and Assembly cost per 1,000
trajectories at 20, 40 and 80 events. To list every ready model under a price:

```bash
python scripts/openrouter_models.py --max-input-price 0.15
```

What to look for:

| Check | Why it matters |
| --- | --- |
| **Ready** | Remix sends JSON-schema requests. Models without structured outputs ignore them or cannot be routed. |
| **Cache discount** | Each native tool turn extends the conversation for that pass. With a discount, that cached input costs a fraction of the normal price. |
| **Reasoning** | Reasoning (thinking) tokens are billed as output. The estimate assumes medium reasoning, as in the pilots; turning it off costs less, and the checker gives the right field. |

A good first choice is **Mistral Small 3.2 24B**: structured outputs, no reasoning, a
cache discount and a 256k window.

## 2. Configure the endpoint

1. Set your key in the environment **before** starting the studio. For an endpoint named
   `openrouter`:

   ```bash
   export REMIX_API_KEY_OPENROUTER=sk-or-...
   python -m trajectory_remix graph-studio --dataset project.sqlite
   ```

2. In **Settings → Models & defaults**, add an endpoint `openrouter` with:

   | Field | Value |
   | --- | --- |
   | Base URL | `https://openrouter.ai/api/v1` |
   | Model identifier | The OpenRouter model ID, such as `mistralai/mistral-small-3.2-24b-instruct` |
   | Concurrent requests | `8` (lower it if you see HTTP 429 errors) |
   | Context limit (tokens) | `65536` |
   | Max output tokens | `8192` |
   | Provider options → Bytes per token | `3` |
   | Provider options → Sticky sessions | On |
   | Provider options → Extra request fields | The `extra_body` the checker printed, for example `{"provider": {"require_parameters": true}}` |

3. Assign the **Draft** role to `openrouter` (Assembly falls back to it). Leave Fast and
   Critical Review empty to skip reviews for now.
4. **Save settings.**

[`examples/openrouter.json`](../examples/openrouter.json) has the same configuration as
settings JSON. Pasting it into **Advanced JSON** replaces all settings, including tools,
so merge its `endpoints`, `endpoint_roles` and `generation` blocks into your own instead.

Why these settings:

- **Sticky sessions** send the trajectory ID as `session_id`. OpenRouter otherwise groups
  requests by their first messages, which change on every call here, so requests would
  scatter across providers and miss the cache.
- **`require_parameters: true`** routes only to providers that honour the structured
  output request. Without it, a provider may ignore it and the reply will need retries.
- **Bytes per token 3** lets the context check use most of the 32k window. At the default
  of 1, a prompt of about 30 KB (roughly 9k real tokens) would already be refused.

## 3. Check one trajectory first

Before spending on a run, start **1 attempt** from **Generate**:

- In **Jobs**, the log should show **Draft written**, then **Graph assembled**. A refused
  request is logged with its HTTP status; check **Extra request fields** if it says a field is
  unsupported.
- The **Usage** tab shows each stage's tokens, time and cost.

Then open the trajectory in the Editor and read it before running more.

## 4. Run the 10-trajectory pilot

Open **Generate**, set **Attempts** to 10, keep the seed and start the run. Then, in the
**Jobs** view:

| Tab | What to check |
| --- | --- |
| **Usage** | Total and per-trajectory cost, prompt and output tokens, and the cached share of prompt tokens. Multiply the per-trajectory cost by 1,000 for the real price of a larger run. |
| **Attempts** | How many trajectories finished, and which ended Unsuccessful or Unfinished. |
| **Logs** | Look for `Invalid structured output; correction`, `Local context guard`, `Request failed` and `endpoint paused`. |

Open a few finished trajectories in the **Editor** to judge the content itself.

## When things go wrong

| You see | What to do |
| --- | --- |
| `Request failed (HTTPStatusError HTTP 400)` and the endpoint pauses | Usually an unsupported request field. Read the provider's message in the job log, fix **Extra request fields**, then **Resume** the endpoint. |
| HTTP 401 or 402 | The key is missing (set it before starting the studio) or the account is out of credit. |
| HTTP 429 | Too many concurrent requests. Lower **Concurrent requests**, then **Resume**. |
| Many `Invalid structured output` corrections | The model struggles with the format. Make sure `require_parameters` is set; otherwise try another model. |
| Corrections saying the reply was **cut off** | The reply hit **Max output tokens**. With reasoning on, the thinking used it up: turn reasoning off, or raise the limit. With reasoning off, the model probably got stuck repeating itself. That costs a minute or two and the full output limit each time, so for a model without reasoning set **Max output tokens** to about `3000`: a stuck reply then fails fast and is retried. |
| `Local context guard` holds a trajectory | A request was over the allowance. Raise **Context limit** toward the model's real window, or check **Bytes per token**. |
| Cached share near 0% on the **Usage** tab | Check **Sticky sessions** is on and the model has a cache discount. |
| Reasoning tokens in **Usage** | Reasoning is still on. Use the field the model checker suggests. |

## What to bring back for review

After the pilot, note: the model, total and per-trajectory cost, cached share, how many
of the 10 finished, how many corrections and pauses appeared, typical events per
trajectory, and a few trajectory IDs that look good or bad. Those decide whether to run
100 next and what to change first.
