# Dashboard

The dashboard is the browser app served by `graph-studio`. This page gives you a tour of
the screen, then walks through the everyday tasks step by step: editing, generating,
reviewing, following jobs and exporting. For the editing rules, previews and REST
routes, see the [Studio reference](GRAPH-STUDIO.md).

![The editor: dataset, windows, timeline and event editor](images/graph-studio.png)

## The layout

The studio fits on one screen. Instead of scrolling a long page, you switch **views**
with the tabs at the top and reveal detail with tabs, menus and tooltips.

| Area | What it does |
| --- | --- |
| **View tabs** | **Editor**, **Jobs**, **Metrics** and **Sampler**. A badge on Jobs counts running and paused jobs. |
| **Header actions** | **Undo** and **Redo**, the **Project** menu (import, export, preferences), the **View** menu (show or hide editor panels), **Settings** and **Generate**. **Review Settings** is a tab next to **Sampler**. |
| **Status bar** | The latest message on the left. On the right, one chip per active job with a progress bar. Click a chip to open that job. While the Editor's timeline is hidden, the timeline's step and play controls appear here too. |

Hover anything with an **ⓘ** for an explanation. Units, reasons and definitions live in
tooltips rather than in extra text on screen.

### The Editor view

| Panel | What it does |
| --- | --- |
| **Dataset** (left) | Search and filter trajectories by text, stage, split, tool, comment state or size (type `@size:m` or `@size:<=xs` in the search box; see [Metrics](METRICS.md#character-counts-and-trajectory-sizes)). Each row shows a short ID, its stage and split, a speech bubble with the number of unresolved comments (amber when some await your review), and a lock if a job owns it. Hover a row for the full ID. |
| **Windows** (centre) | One card per context window, as it looks at the current timeline position, in a readable view. **Raw** shows the exact tokens; the expand button opens the window in a large panel. The speech bubble leaves a comment on the window. |
| **Timeline** (bottom) | One lane per window. Click an event to edit it; Shift-click to make it a dependency of the selected event. Scrub, step or press **Play**. |
| **Event panel** (right) | At the top, the **trajectory bar**: one fixed row with the selected trajectory's ID, save state and problem count. Every change saves itself, so there is no Save button. **More** holds Review, Export, Duplicate, Reload and Delete. While a job holds the trajectory, a lock notice appears under the bar. Below it, tabs for the selected event: **Content**, **Options** (dependencies, closing, masks, delivery, framing), **Windows**, **JSON** and **Metrics** (the trajectory's **Stage**, **Split**, **Complexity** and size, then its categories, labels and outcomes; see [Metrics](METRICS.md#set-a-trajectorys-metrics)), and on the right two icon tabs with counters: **Problems** (the warning triangle) and **Comments** (the speech bubble). |

Each window keeps **one colour** everywhere: its card, its timeline lane and its event
blocks. That makes it easy to follow one window across the screen.

#### Reading windows

The readable view only changes how a window looks. The tokens, masks and training data
are untouched, and **Raw** on any card switches back to the exact token stream.

| You see | What it is |
| --- | --- |
| **Chat bubbles** | Messages in a `chatroom` window. The agent's messages sit on the right; each other speaker keeps one colour. |
| **Formatted JSON** | Tool calls, tool results and other JSON, indented and colour-coded even while it is still streaming. |
| **Markdown** | Message and text content: headings, **bold**, *italic*, `code`, code blocks, lists, quotes, tables and links. Plain text looks exactly as before. HTML is shown as text, and only `http(s)` and `mailto` links are clickable. |
| **Context** line | The window's header, such as its purpose and reasoning effort. |
| **Streaming** tag, dashed border | A message, call or result whose end marker hasn't arrived at this position. |
| **NOOP ×N** | In Tick history, an open window the agent can write to predicts NOOP on every tick it has nothing to say. NOOP is a training label ("wait"), not window content: it never enters the context. A run of them is shown as one chip with a count. |

![Readable windows: chat bubbles and formatted tool JSON](images/dashboard/windows.png)

### The Jobs view

![A generation job with its attempts](images/dashboard/jobs.png)

The list on the left shows every job with its kind, state and progress. Select one to see
its progress bar, counts, controls and tabs for **Logs**, **Attempts** (generation),
**Usage** (tokens, cached share and provider-reported cost for model jobs),
**Results** (bulk edits and reviews) and **Files** (exports). The page updates in place
while you watch, so it never flickers or loses your scroll position.

### The Metrics view

![Dataset metrics](images/dashboard/metrics.png)

Summary tiles, then target-versus-observed shares per sampler dimension, which metrics
are measured, and the selected trajectory's values. It covers the same search and
filters as the Editor's dataset panel. See [Metrics](METRICS.md).

### The Sampler view

The weights and rules that decide what each generated trajectory is about. It has its
own guide: [Sampler Matrix](SAMPLER-WORKBENCH.md).

## Set up and generate

1. **Add a model endpoint.** Click **Settings**. On **Models & defaults**, enter an
   endpoint ID and click **Add endpoint**, then fill in the base URL, the exact model
   name, a concurrent-request limit and a retry count. Assign the *Draft* role, and optionally *Assembly*, *Fast Review* and *Critical Review*. One
   endpoint can serve every role. Details,
   including credentials, are in [Project settings](PROJECT-SETTINGS.md).
2. **Save settings.** New projects include a default `chat` tool input schema, so the
   agent can reply in the chatroom. Removing it is your choice.
3. **Set up review (optional).** Open the **Review Settings** tab to choose automatic
   review options, edit the Core and Critical checklists, and edit the
   generation prompts. Click **Save review settings** if you change them.
4. **Open Generate.** It shows which endpoint fills each role and any pins from the
   Sampler.
5. **Tune the sampler (optional).** **Open Sampler** jumps to the weights and pins.
6. **Start.** Set the number of attempts and a starting seed, then click
   **Start generation**. The new job opens in the Jobs view.

![The Generate dialog](images/dashboard/generate.png)

A run **freezes** its settings, prompts, matrix and pins when it starts, so later edits only
affect future runs. Generated trajectories are never automatically Ready.

## Tutorials

Short recipes for common tasks. Each assumes the studio is open in your browser.

### Create and edit a trajectory by hand

1. In the **Dataset** panel, click **+**. A new, empty trajectory is selected, with one
   window called `reasoning`.
2. In the event editor, click **+** to add an event. It starts as agent text in the first
   window.
3. On the **Content** tab, choose a **Kind** and **Window**, then type the content. The
   windows and timeline update as you type.
4. Need another window? Open the **Windows** tab, type a new window ID and purpose, and
   click **Add / update window**.
5. Want the agent to see its tools? Add an event of kind **Tool schema** in a `tools`
   window and tick the tools. See [Show the agent its tools](TOOLS.md#show-the-agent-its-tools).
   Randomly added tools are grayed out; hide them under **Preferences**.
6. Set the trajectory's **Stage**, **Split** and **Complexity** at the top of the **Metrics** tab.

Every change saves as soon as you make it; there is no Save button. Choices, checkboxes and
buttons save at once. Typing saves when you pause, so a sentence is one save. The badge reads
*Saving…* and then *Saved · rev N*. Each save is one step for **Undo** and **Redo**, including
metric values.

Some in-between states can't be stored yet, such as a tool call with no call ID. The badge
then reads *Not saved* (hover it for the reason), your draft is kept, and it saves with your
next change.

### Link events in order

Events in the same window must be ordered, and events in different windows run in
parallel unless you link them.

1. Select the later event.
2. Either **Shift-click** the earlier event in the timeline, or open the **Options** tab,
   pick an event ID and click **Add**.
3. To remove a dependency, click its chip under **Runs after** on the Options tab.

![Runs after on the Options tab, with one dependency](images/dashboard/event-links.png)

### Fix problems in a draft

Broken drafts always save. Problems show up as you edit:

- The trajectory bar shows a red **errors** or amber **warnings** tag. Click it to open the
  **Problems** tab (the warning triangle on the right of the tabs).
- Each problem has a code, a message and, where it applies, **Inspect event** to jump to
  the event.
- Errors block compiled export; warnings don't.

Problems come from two places, labelled on each row:

| Label | What it checks | Examples |
| --- | --- | --- |
| **Compiler** | The trajectory's structure, on the server | Missing dependencies, windows left open |
| **Studio** | Editor housekeeping, worked out in your browser | `missing_split` (**Set split**), `review_stale` or `signoff_stale`, `stale_metrics` (**Show metrics**) |

Studio warnings update as you edit, never block export, and aren't shown to the review or
assembly agents. Choosing **Auto** as the split clears `missing_split` without a warning
at export.

![A missing dependency on the Problems tab](images/dashboard/problems.png)

### Leave comments for an agent to fix

Some problems no checklist catches: a tool argument the model couldn't have known, or a
window closed while a later event still needs it. Point them out with a comment, and an
agent connected over [MCP](MCP.md) can find it and fix it.

1. Choose what the comment is about:
   - **A window:** click the speech bubble on its card.
   - **An event:** select it on the timeline, then open the **Comments** tab (the speech bubble at the right of the tabs). The composer follows the selected event.
   - **Anything else:** pick it from **Comment on**: the whole trajectory, any event or any
     window.
2. Write what's wrong, for example *"This tool call closes the window too early. Event 13
   is still using it."*, and click **Add comment** (or press Ctrl+Enter).

Comments save straight away. They aren't part of the trajectory, so they don't make the
draft unsaved, don't change its revision or review, aren't exported, and still work while a
job holds the trajectory.

| Where | What you see |
| --- | --- |
| Dataset row | A speech bubble with the number of unresolved comments, amber when some await your review |
| Timeline event, window card | A speech bubble with that target's unresolved comments |
| Comments tab | **Awaiting your review**, then **Open**, then **Resolved**, each with who wrote it (**You** or **Agent**) and the revision it was written against |

#### Review an agent's fix

A comment moves through three states, and **only you resolve it**. An agent can say it's
done, but that never closes the comment, so a fix that misses the point can't be marked
complete by mistake.

| State | Meaning | What you can do |
| --- | --- | --- |
| **Open** | Work to do. Agents pick these up. | **Reply**, **Resolve** (if you fixed it yourself), **Delete** |
| **Awaiting review** | An agent says it's fixed. Its note on what changed is the latest reply. | **Accept** to resolve it, or **Request changes** to reply and reopen it in one step |
| **Resolved** | Done. | **Reopen**, **Delete** |

Click a comment's target to jump to it. **Reply** adds to the thread without changing the
state, for example to answer an agent's question. When you request changes, say what's
still wrong: the comment goes back to the agents with your reply in its thread. If the event
or window has since been deleted, the comment is marked **Removed**.

To find work, use the comments filter in the dataset panel: **Unresolved**, **To review**
(awaiting your review) or **Open for agents**. It combines with the other filters, and **Bulk edit all
matches** uses it too.

#### Send comments to an agent

Instead of connecting an agent yourself, you can have the project's **Assistant** endpoint
work through a trajectory's open comments.

1. In **Settings**, assign an endpoint to the **Assistant** role (see
   [Project settings](PROJECT-SETTINGS.md)). It's separate from the generation and review
   roles.
2. Click **Send open comments to agent** on the Comments tab.

The job takes the open comments as they are at that moment, with their replies. The model
edits the trajectory with the same authoring tools Assembly uses, then answers every comment
with its `reply` tool: it marks the ones it fixed **Awaiting review**, with a note on what
changed, and replies to the rest with a reason or a question, leaving them open. The next time
you send comments on the same trajectory, the agent continues the same conversation, so it
remembers what it already did. Review its fixes as above. The trajectory's stage doesn't change. Follow it in **Jobs**, where Undo can
revert its edits, and read the answers in the Comments tab. If the session runs out of
room, it stops with its edits so far kept and the comments still open.

To skip the button, turn on **Automatically send new comments to the assistant** in
**Preferences** (off by default, stored in this browser). Adding a comment, replying to a comment that is still open, or requesting
changes on an agent's fix, then sends the trajectory's open comments at once. The same
checks apply. If a job is already working on the trajectory (for
example the assistant's previous run), the comment is saved and queued instead. When the job
settles, the open comments, however many you added, go out together as one more job. A queued
send waits while you have unsaved edits, and is dropped if nothing is left open. **Send open comments to agent** stays enabled in this mode, so you can resend by hand
(for example after the assistant hits its context limit).

### Preview the tokens

1. Add a tokenizer profile in **Settings** (see
   [Project settings](PROJECT-SETTINGS.md#tokenizer-profiles)).
2. Open **Project → Preferences…** and choose it as the **Preview tokenizer**.
3. In the timeline, switch **Dependency graph** to **Tick history**. Scrub to watch each
   window fill tick by tick.

![Tick history](images/graph-studio-ticks.png)

### Read a conversation or a tool call

1. Scrub the timeline to the position you want.
2. Chatroom windows show the conversation as bubbles; tool windows show calls and results
   as formatted JSON.
3. Click the **expand** button (two outward arrows) on a window card to read it in a large
   panel. The panel follows the timeline, and its own arrows step backward and forward.
4. Click **shrink** (or press **Esc**) to return to the grid.
5. Need the exact tokens? Click **Raw** on the card. Click it again for the readable view.

![An expanded chatroom window](images/dashboard/popout.png)

### Rearrange the editor

Open the **View** menu and choose **Hide dataset panel**, **Hide timeline panel** or
**Hide event panel**; the same items read **Show …** to bring a panel back, and
**Show all panels** restores everything. The windows grow into the freed space. Hiding the
event panel also hides the trajectory bar and its save state. When you hide the timeline,
its step and **Play** controls move to the status bar so you can still scrub the windows.
Your layout is remembered in this browser.

### Move the timeline to another monitor

Have a second screen? Click the **pop-out** button at the right end of the timeline's header
(or choose **View → Pop out timeline panel**). The timeline opens in its own browser window,
which you can drag to the other monitor and resize. Everything still works from there:
clicking an event opens it in the event panel, **Shift**-click adds a dependency, and the
scrub line, **Play**, zoom and view picker drive the windows in the main editor. The windows
grow into the space the timeline left.

To bring it back, close the popup window, click the pop-out button again, or choose
**View → Return timeline panel**. The popup remembers its size and position for next time,
though some browsers keep new windows on the screen that opened them.

| If you… | What happens |
| --- | --- |
| Close the timeline window | The timeline returns to the editor. Pop it out again any time. |
| Reload the timeline window | It reconnects and keeps showing the timeline. |
| Reload the editor | The timeline window waits a moment, then reconnects to the reloaded editor. |
| Close the editor | The timeline window closes too. |
| Open the editor in two tabs | Each tab gets its own timeline window, and each only controls its own tab. |

If nothing opens, your browser blocked the popup: allow popups for the studio's address
and try again. Hover tooltips don't show in the popup window; hover an event for its
dependencies instead.

### Resize panels

Drag the thin gap between two panels to resize them: the dataset and event panels
sideways, the timeline up or down, and the job list in the Jobs view. Each handle also
works from the keyboard: focus it and press the arrow keys (hold **Shift** for bigger
steps). **Double-click** a handle or press **Home** to restore its default size. Sizes are
remembered in this browser, and panels can't shrink below a usable minimum or squeeze the
windows out.

### Choose how trajectories are built

Every run builds trajectories in four stages: **Draft → Assembly → Fast Review → Critical
Review** (see [Event generation](GRAPH-GENERATION.md#draft-and-assembly)). What you control is
the prompts and the models:

1. Open **Review Settings** and find **Generation prompts**.
2. Edit the prompts (click a prompt's title to collapse or expand it):
   - **Agent prompt:** what a trajectory is and how the agent behaves. Every endpoint sees it,
     and a persona pack goes at its end.
   - **Authoring prompt:** how to use the authoring tools. Assembly, review and the Assistant
     see it.
   - **Draft instructions** and **Assembly instructions:** the task for each stage.
   - **Assistant instructions:** the task that opens each Send to agent message, before the
     comments table.
3. **Load Default Prompts** restores the packaged prompts, after you confirm.
4. **Save review settings.** The next run uses the new prompts; runs already started keep theirs.

To use a different model for a stage, assign the **Draft** or **Assembly** role to another
endpoint in **Settings → Models & defaults**.

### Import and export

- **Import:** **Project → Import trajectories…**, then choose a `.json` or `.jsonl` file.
  The import runs as a job and opens in the Jobs view.
- **Export:** **Project → Export dataset…**. Choose raw JSONL or compiled KTREMIX, the
  minimum stage and the metadata to keep, then **Start export**. When the job completes,
  its **Files** tab has the download buttons. Files expire after 24 hours. See
  [Export](EXPORT.md).

![The Export dialog](images/dashboard/export.png)

### Review and repair trajectories

1. Save the trajectory you want reviewed, or set up a search that matches the ones you
   want.
2. For one trajectory, open **More** in the trajectory header and choose **Review**. For
   every search match, open **Bulk edit all matches**, pick **Review** as the action, and
   click **Apply**.
3. Follow the job in Jobs. A message appears when it finishes.

A review checks the trajectory against the checklist, fixes anything that fails, and signs
off. If the reviewer decides a trajectory can't be fixed, it signs off as unfeasible with a
reason, and the review fails. Manual reviews use standard mode: Pending trajectories get the
Fast review, Candidate trajectories get the Critical review, and Unsuccessful or Rejected
trajectories are reviewed again at their own tier.

The **Review Settings** tab holds the automatic review settings and the review checklists.

### Change many trajectories at once

1. Filter the dataset to the trajectories you want.
2. Open **Bulk edit all matches** at the bottom of the Dataset panel.
3. Choose **Set stage**, **Set split**, **Review** or **Delete**, enter the value (a stage number such
   as `3` for Ready, or a split name; review and delete need none), and click **Apply**.

Bulk edits skip locked trajectories and can be undone with **Undo** once they finish.

### Follow, pause and resume a job

1. Click a job chip in the status bar, or open the **Jobs** tab and select a job.
2. Use **Pause**, **Resume** or **Cancel** in the job header. These buttons only appear
   while the job is still active.
3. On a generation job, the **Attempts** tab lists each trajectory. Click an ID to open it
   in the Editor, or **Detach** to stop automatic work on it so you can edit by hand.

### Check dataset coverage

1. Set the Editor's search and filters (or leave them empty for the whole dataset).
2. Open **Metrics**. Pick a dimension in **Targets vs observed** to compare what the
   sampler aimed for with what accepted trajectories contain.
3. Click **Calculate missing metrics** to measure what can be measured. It runs as a job.
4. To fill values that can't be measured, such as categories and outcomes, open a trajectory in the
   Editor and use its **Metrics** tab.

## Watch and recover work

**Jobs** lists every job, paginated. Selecting one shows its progress, retained logs, and
**Pause**, **Resume** and **Cancel**. Leaving the Jobs view never stops a job, because
the server owns the work. A trajectory that a job is working on shows a lock and an
**Inspect owning job** button.

Log lines are coloured by status: ordinary progress is grey, **green** marks successes
(a passing verdict or a trajectory that settled as a pass), **amber** marks things that
need a look (a request that failed but will retry, an invalid reply being corrected, a
held trajectory) and **red** marks errors (retries ran out, a trajectory failed, a job stopped on
an error). The wording always says the same thing, so colour is never the only clue. A failed
request says why: the HTTP status with a plain-language hint (and the provider's short error
code, if it sends one), a timeout with the limit that applied, a refused connection, or a
missing API key variable. A local error names the exception and the place in the code; the
full traceback is printed to the server output. Prompts, model replies and keys never
appear in logs. The "still waiting" heartbeat lines are off.

Two kinds of "resume" are separate:

- **Endpoint Resume** restarts an endpoint that paused after its retries ran out. It
  appears as a warning in the job's summary.
- **Job Resume** restarts a job you or a restart paused.

If the server restarts, jobs come back **paused**, with unfinished model text
discarded and completed events kept. Nothing restarts on its own, so you resume
deliberately. See [Jobs](GRAPH-JOBS.md) for the full recovery model.

## Live updates and conflicts

While a pipeline works on a trajectory, the clean copy you have selected refreshes as
events arrive, and unlocks when the work ends.

- **Locked** trajectories can't be edited. Ownership is exclusive, and nothing saves
  until the lock clears.
- Edits that haven't saved yet are never overwritten. A conflicting save reports
  a revision conflict and leaves your text intact. Reloading the saved version is an
  explicit step (**More → Reload saved source**).
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
their stage and show a `review_stale` warning on the Problems tab. Nothing is re-reviewed
unless you ask. See [Event generation](GRAPH-GENERATION.md#reviews-and-repair).

## Good to know

- The dashboard checks forms, generation, jobs, live refresh and settings round trips
  with mocked providers. That shows the app works, not that a model writes good
  content. See [Acceptance](ACCEPTANCE.md) for the current evidence.
- Preferences (such as the Tick history profile) and Undo history live in your browser,
  not in the project.
- Fonts are bundled with the app, so the studio needs no internet connection.

## For developers

The frontend is plain ES modules and CSS in `trajectory_remix/static/`, with no build
step. Element IDs used by the tests are stable; keep them when you rearrange markup.

| File | Responsibility |
| --- | --- |
| `graph.html` | App shell: header, the views, status bar and dialogs. |
| `style.css` | Theme tokens (the *Dark Cyan Base* design system) and shared components: buttons, fields, widgets, tags, tabs, menus, progress bars, tooltips, dialogs, tables. |
| `graph.css` | Layout of the shell, Editor, Jobs and Metrics views and the status bar. |
| `sampler.css`, `project-settings.css` | The Sampler view and the settings forms. |
| `shell.js` | View tabs, disclosure menus, keyboard-navigable tab panels and the `[data-tip]` tooltip. |
| `layout.js` | Draggable splitters (sizes are CSS variables on the view, kept in `localStorage`), and moving the timeline controls into the status bar while the timeline is hidden. |
| `views.js` | `showView()` / `currentView()`; dispatches a `studio-view` event that loads Jobs, Metrics and the Sampler on demand. |
| `ui-kit.js` | DOM helpers: keyed `reconcile()`, idempotent `setText`/`setAttr`/`toggleClass`, `progressBar()`, `makeTabs()`, stage and job tone maps. |
| `graph.js` | `StudioUI`, the editor controller: drafts, saving, previews, polling, history and dialogs. |
| `jobs-view.js` | `JobsView`: the job list and detail panel (`StudioUI.dialogs` maps job IDs to detail panels). |
| `metrics-view.js` | Renders the population and per-trajectory metrics payloads. |
| `window-render.js` | Readable window view: header fields, chat bubbles, tool name lists for `tools` windows (`tool-list-view.js`), tolerant JSON formatting, Markdown text, collapsed NOOP runs, and the Raw view. Visual only. |
| `markdown-view.js`, `markdown-inline.js`, `window-markdown.css` | Markdown for window text, built with DOM calls only (never `innerHTML`). Text with no Markdown syntax is returned as the plain text node. |
| `sampler.js`, `project-settings.js` | The Sampler workbench and the settings form editors. |

The poll loop runs every 1.5 s. Anything it renders must update in place: use
`reconcile()` for lists and the idempotent setters for text, attributes and classes, so
an unchanged poll produces no DOM mutations. `tests/studio_ui_frontend.mjs` checks this
with a `MutationObserver`.

Theme colours, type, spacing and radii come from CSS custom properties on `:root` in
`style.css`. Use the tokens (for example `var(--surface-200)` or `var(--space-3)`) rather
than literal values. Pastels (`--pastel-*`) are for tracking entities such as windows and
job kinds, and status always pairs a colour with a word.
