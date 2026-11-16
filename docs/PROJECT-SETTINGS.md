# Project settings

**Settings** (in the header) holds the things shared by everyone using a project: model
endpoints and roles, tokenizer profiles, tools and defaults, on the **Models & defaults**,
**Tools**, **Storage** and **Advanced JSON** tabs. The **Review Settings** tab holds review and
repair options and the review checklists.

![Project settings](images/dashboard/settings.png)

Both are a shared local draft until you press **Save**. Closing a dialog or editing a
field never starts a model request, and running jobs keep the configuration they
started with.

## Configure a model endpoint

You bring your own OpenAI-compatible server. Remix doesn't launch, probe or download
models.

1. Start your server.
2. In **Settings → Models & defaults**, enter a unique endpoint ID and click **Add endpoint**.
3. Fill in the **base URL** (for example `http://127.0.0.1:8000/v1`; Remix adds
   `/chat/completions`), the exact **model** name, a **concurrent-request limit** and a
   **retry count** for transport errors.
4. Assign the **Generator**, **Fast Review** and **Critical Review** roles from the
   dropdowns. One endpoint can fill all three. Leave a review role empty to skip that
   review.
5. **Save settings**, then open **Generate** to choose review and repair options.

Removing an endpoint also clears the roles that pointed at it. Saving that affects only
future work.

### Credentials

If your server needs a key, set it in the environment **before starting Remix**. The
variable is `REMIX_API_KEY_<ENDPOINT>`, with the endpoint ID uppercased and hyphens
turned into underscores. For example, endpoint `local-model` reads
`REMIX_API_KEY_LOCAL_MODEL`. The endpoint card shows its variable name. Credentials
never go into project settings, sources, instructions or logs.

## Tokenizer profiles

You only need a profile for **Tick history** preview and for compiled export. Graph
authoring, generation, review and metrics never touch a tokenizer.

1. Expand **Tokenizer profiles**, enter an ID and choose the tokenizer's JSON file.
2. Map all twelve logical controls to dedicated special tokens that already exist in
   the file. Recognized spellings are preselected, and you can pick others.
3. Save. Profiles with missing or duplicate mappings are rejected, as are files with
   fewer than twelve dedicated special tokens. Remix never extends the vocabulary.

The twelve controls are `bos`, `eos`, `noop`, `ctx_begin`, `ctx_end`, `tool_begin`,
`tool_end`, `response_begin`, `response_end`, `msg_begin`, `msg_end` and `window_ref`.

Pick the preview profile in **Project → Preferences…** (stored in your browser) and the
export profile in the Export dialog. They are chosen independently.

## Tools

The tool list is a catalogue of **input schemas** (JSON Schema). Tools have no code and
no output schema: authors and models write the results. The settings form can create an
object schema and add or remove fields and required flags. Advanced JSON keeps nested or
custom schemas. Removing a tool keeps the existing calls that use it. See
[Synthetic tools and chat](TOOLS.md).

## Defaults

| Setting | Notes |
| --- | --- |
| Train / development / test fractions | Must total 1. Used when export assigns splits. |
| Reasoning effort levels | Defaults to Low, Medium and High. |
| Log cache size (MiB) | Project-wide retained log cap. Default 1024, blank means unlimited. |

## Review criteria

In the **Review Settings** tab, each criterion is a line of text with an optional
**not-applicable** allowance. Add or remove criteria in the **Core** and **Critical**
lists. Fast review checks Core, and Critical review checks Core plus Critical.

Changing a checklist marks affected sign-offs **stale**. Their stages are kept, and you
re-review manually. Form edits preserve unrelated fields, and the advanced project
JSON remains for unusual settings.

### Default criteria

A new project starts with these. The coordinator items are the studio's default standard
for the multi-agent coordinator the data is written for.

| List | Criterion | What the reviewer checks |
| --- | --- | --- |
| Core | `objective_alignment`, `causal_evidence`, `actor_and_supervision`, `world_consistency`, `coherent_reasoning`, `truthful_episode_outcome` | The general quality of the episode: objective, causal evidence, supervision, consistency, reasoning and honest outcomes. |
| Core | `coordinator_role` | The agent coordinates specialized work rather than personally doing substantial implementation or research, and delegation fits the task size. |
| Core | `authorization_respected` | Consequential actions stay within the user's stated authority or follow an observed approval; otherwise the agent asks or escalates. |
| Critical | `decision_quality`, `long_horizon_continuity`, `exploration_and_recovery`, `training_value` | Decisions, continuity, recovery and teaching value. |
| Critical | `delegation_discipline` | No repeated delegation of an unchanged failed task, no recursive or excessive agent creation, no unbounded planning, and budgets respected. |
| Critical | `strategic_investment_judgment` | Automation or infrastructure decisions weigh reuse, savings, build and maintenance cost and uncertainty from visible evidence, and are revisited when things change. Not-applicable is allowed, because many episodes have no such decision. |
| Critical | `execution_progress` | When execution is available, the episode advances real work, not only plans. |

### Reset to the defaults

Press **Reset review checklists to defaults** in the Review Settings tab to replace both
lists with the packaged criteria. A confirmation asks first, because your added,
edited and removed criteria in the draft are lost. Declining changes nothing. The
reset stays in the draft until you press **Save review settings**, and saving marks affected
sign-offs stale like any other checklist change.

### Projects that already saved checklists

Only new projects get new defaults. A project that already saved its checklists keeps
them exactly as they are, so existing sign-offs do not become stale on upgrade. To adopt
the coordinator criteria in an existing project, either press **Reset review checklists
to defaults** (this replaces your custom criteria too), or add the criteria you want by
hand with **Add core criterion** and **Add critical criterion**, using the IDs and
wording in the table above. There is no merge action that adds only the missing
recommended criteria.
