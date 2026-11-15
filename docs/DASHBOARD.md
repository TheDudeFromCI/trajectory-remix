# Dashboard workflows

The graph dashboard retains the desktop dataset, window canvas, timeline and
right-hand event inspector. File/Edit/View menus expose import/export, history,
preferences and panel visibility. Advanced source/metadata JSON stays optional.
Workbench B remains the dedicated sampler workspace with inline pin buttons.

## Configure and generate

1. Open **Project Settings**. Add a model endpoint using its base URL, model
   identifier, concurrent-request limit and transport retry count. Assign the
   Generator, Fast Review and Critical Review roles using the dropdowns. A local
   endpoint can serve multiple roles. See [settings](PROJECT-SETTINGS.md) for
   authentication environment variables; credentials are not saved in projects.
2. Save settings. Fresh projects include an editable Chat input schema so the
   default instruction/response Flow produces a chatroom reply. Removing it is
   an explicit author choice. Other tools use the schema form.
3. Open **Generation Settings** to configure automatic reviews/repair and the
   project-wide checklists. Save changes before starting a run. The **Sampler
   Matrix** action opens Workbench B for weights and pins.
4. Set attempts/starting seed and start generation. The run freezes the saved
   settings, matrix and current pins. Editing settings later affects future runs.

## Inspect and recover work

**Jobs** opens a paginated overview. Each job opens an independent dialog with
progress, retained logs and Pause/Resume/Cancel. Bottom status buttons are shortcuts
rather than the only entry point. An attached trajectory also links to its owning
job. Endpoint recovery is separate from job Resume after retry exhaustion.

Clean selected trajectories refresh as events arrive and unlock when work ends.
An actual unsaved draft is preserved and reports a revision conflict rather than
being overwritten. Locked sources cannot be edited. Detachment permanently stops
that pipeline's updates; it does not manufacture a review verdict.

## Validation scope

Small browser checks exercise endpoint/role forms, generation, job inspection,
pause/resume, live refresh, dirty-buffer retention and settings round trips.
A successful mocked provider run establishes application behavior, not LLM content
quality. See [acceptance](ACCEPTANCE.md) for current evidence and limitations.
