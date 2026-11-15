# Dashboard

The dashboard is the browser app served by `graph-studio`. This page covers the
everyday workflow: set up models, generate, and keep an eye on running work. For the
editing forms, previews and REST routes, see the [Studio reference](GRAPH-STUDIO.md).

## The layout

| Area | What it does |
| --- | --- |
| **Left sidebar** | Search and list trajectories, with stage and split. |
| **Centre** | One card per window, plus the timeline you can scrub. |
| **Right inspector** | Edit the selected event: window, dependencies, masks, delivery. |
| **Menus** | **File** (import and export), **Edit** (history, preferences), **View** (panel visibility). |
| **Bottom bar** | Shortcuts to job dialogs. |

Advanced Source and Metadata JSON editors are optional. The **Sampler Matrix** has
its own workspace, described in [Sampler Matrix](SAMPLER-WORKBENCH.md).

## Set up and generate

1. **Add a model endpoint.** Open **Project Settings**, enter a base URL, the exact
   model name, a concurrent-request limit and a retry count, then assign the
   *Generator*, *Fast Review* and *Critical Review* roles. One endpoint can serve every
   role. Details, including credentials, are in [Project settings](PROJECT-SETTINGS.md).
2. **Save.** New projects include a default `chat` tool input schema, so the standard
   flow produces a chatroom reply. Removing it is your choice.
3. **Choose review behavior.** Open **Generation Settings** to turn automatic review
   and repair on or off and to edit the Core and Critical checklists. Save before you
   start a run.
4. **Tune the sampler (optional).** **Sampler Matrix** opens the workbench for weights
   and pins.
5. **Start.** Set the number of attempts and a starting seed, then start generation.

A run **freezes** its settings, matrix and pins when it starts, so later edits only
affect future runs. Generated trajectories are never automatically Ready.

## Watch and recover work

**Jobs** lists every job, paginated. Each opens in its own dialog with progress,
retained logs, and **Pause**, **Resume** and **Cancel**. Closing a dialog never stops a
job, because the server owns the work. A trajectory that a job is working on links back
to that job.

Two kinds of "resume" are separate:

- **Endpoint Resume** restarts an endpoint that paused after its retries ran out.
- **Job Resume** restarts a job you or a restart paused.

If the server restarts, jobs come back **paused**, with unfinished model text
discarded and completed events kept. Nothing restarts on its own, so you resume
deliberately. See [Jobs](GRAPH-JOBS.md) for the full recovery model.

## Live updates and conflicts

While a pipeline works on a trajectory, the clean copy you have selected refreshes as
events arrive, and unlocks when the work ends.

- **Locked** trajectories can't be edited. Ownership is exclusive.
- If you have **unsaved edits**, they are never overwritten. A conflicting save reports
  a revision conflict and leaves your text intact. Reloading the saved version is an
  explicit step.
- **Detaching** a trajectory from its pipeline is permanent. That pipeline stops
  updating it, and no review verdict is invented.

## Stages at a glance

| Stage | Meaning |
| --- | --- |
| Unfinished (-3) | Hand-made drafts, or generation that failed or ran out of context. |
| Pending (0) | Generated, waiting for review. |
| Candidate (1) / Unsuccessful (-1) | Passed or failed the Fast review. |
| Validated (2) / Rejected (-2) | Passed or failed the Critical review. |
| **Ready (3)** | Approved by you. This is what export includes by default. |

Changing a source or checklist can make an old review **stale**. Stale reviews keep
their stage and are shown as stale. Nothing is re-reviewed unless you ask. See
[Event generation](GRAPH-GENERATION.md#reviews-and-repair).

## Good to know

- The dashboard checks forms, generation, jobs, live refresh and settings round trips
  with mocked providers. That shows the app works, not that a model writes good
  content. See [Acceptance](ACCEPTANCE.md) for the current evidence.
- Preferences (such as the Tick history profile) and Undo history live in your browser,
  not in the project.
