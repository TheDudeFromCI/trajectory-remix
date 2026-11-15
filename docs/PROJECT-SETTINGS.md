# Project and generation settings

**Project Settings** contains forms for endpoints, their roles, tokenizer
profiles, tools and project defaults. **Generation Settings** contains automatic
review/repair toggles and editable Core/Critical criteria. Settings are a shared
local draft until the dialog's Save action succeeds. Closing or editing a field
never starts a model request. Existing jobs retain their frozen configuration.

## Configure a local model

1. Start your own OpenAI-compatible server.
2. In Project Settings, enter a unique endpoint ID and click **Add endpoint**.
3. Set its base URL (for example `http://127.0.0.1:8000/v1`), exact model
   identifier, concurrent-request limit and transport retry count.
4. Assign Generator, Fast Review and Critical Review using the role dropdowns.
   One endpoint may serve all roles. Leave a review role unconfigured to omit it.
5. Save settings. In Generation Settings, choose automatic review/repair options
   and inspect the project checklists, then save those changes.

If authentication is required, configure the server process environment before
starting Remix. The endpoint card names its variable, such as
`REMIX_API_KEY_LOCAL_MODEL` for endpoint `local-model`. Credentials are never
stored in project JSON, generated sources or logs. The forms do not probe the
endpoint or silently launch or download a model.

Removing an endpoint from the draft also clears roles referring to it. Saving
that change affects future operations; frozen existing jobs keep their endpoint
configuration. Use their job dialog to pause, cancel or resume them.

## Tokenizer profiles

Expand **Tokenizer profiles**, enter an ID and choose the tokenizer's JSON file.
Map all twelve logical controls to existing dedicated special tokens. Recognized
control spellings are preselected; other spellings can be selected explicitly.
Profiles with missing or duplicate mappings are rejected on Save. Files with
fewer than twelve dedicated special tokens are rejected on import. Remix never
extends the imported vocabulary. Choose preview and export profiles independently
in their corresponding controls; ordinary graph authoring needs no tokenizer.

## Defaults and review criteria

Project defaults include train/development/test fractions (which must total 1),
reasoning effort levels and the retained log size in MiB. Blank log size means
unlimited. Client-only preferences remain in Preferences.

Generation Settings exposes each criterion as text with an optional
not-applicable allowance. You can add/remove criteria in Core and Critical.
Checklist changes mark affected signoffs stale, preserving stage and requiring
manual re-review. Advanced project JSON remains available for unusual settings;
form edits preserve unrelated fields and do not replace tool or sampler drafts.
