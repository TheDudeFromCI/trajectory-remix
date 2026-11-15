# Project settings

**Project Settings** holds the things shared by everyone using a project: model
endpoints and roles, tokenizer profiles, tools and defaults. **Generation Settings**
holds review and repair options and the review checklists.

Both are a shared local draft until you press **Save**. Closing a dialog or editing a
field never starts a model request, and running jobs keep the configuration they
started with.

## Configure a model endpoint

You bring your own OpenAI-compatible server. Remix doesn't launch, probe or download
models.

1. Start your server.
2. In Project Settings, enter a unique endpoint ID and click **Add endpoint**.
3. Fill in the **base URL** (for example `http://127.0.0.1:8000/v1`; Remix adds
   `/chat/completions`), the exact **model** name, a **concurrent-request limit** and a
   **retry count** for transport errors.
4. Assign the **Generator**, **Fast Review** and **Critical Review** roles from the
   dropdowns. One endpoint can fill all three. Leave a review role empty to skip that
   review.
5. **Save**, then open Generation Settings to choose review and repair options.

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

Pick the preview profile in your browser **Preferences** and the export profile in the
Export dialog. They are chosen independently.

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

In **Generation Settings**, each criterion is a line of text with an optional
**not-applicable** allowance. Add or remove criteria in the **Core** and **Critical**
lists. Fast review checks Core, and Critical review checks Core plus Critical.

Changing a checklist marks affected sign-offs **stale**. Their stages are kept, and you
re-review manually. Form edits preserve unrelated fields, and the advanced project
JSON remains for unusual settings.
