import { Window } from "happy-dom";
import fs from "node:fs/promises";
import assert from "node:assert/strict";
const base = process.argv[2] || "http://127.0.0.1:8765";
const html = await (await fetch(base)).text();
const w = new Window({
  url: base,
  settings: { disableCSSFileLoading: true, disableJavaScriptFileLoading: true },
});
w.document.write(html);
w.structuredClone = structuredClone;
const requests = [];
let holdNextCompile = false,
  releaseCompile = null,
  holdNextFrames = false,
  releaseFrames = null,
  nextFramesError = null,
  completedStateReads = 0,
  lastPlan = null,
  confirmResult = true,
  confirmationMessage = null;
w.fetch = async (path, options) => {
  requests.push({ path, options });
  if (path.startsWith("/api/frames/") && nextFramesError) {
    const error = nextFramesError;
    nextFramesError = null;
    return new Response(JSON.stringify(error), { status: error.code === "preview_limit" ? 413 : 404 });
  }
  const response = await fetch(new URL(path, base), options);
  if (path.startsWith("/api/frames/") && holdNextFrames) {
    holdNextFrames = false;
    await new Promise((resolve) => (releaseFrames = resolve));
  }
  if (path.startsWith("/api/state?")) completedStateReads++;
  if (path === "/api/compile" && holdNextCompile) {
    holdNextCompile = false;
    await new Promise((resolve) => (releaseCompile = resolve));
  }
  if (path === "/api/compile" && response.ok)
    lastPlan = await response.clone().json();
  return response;
};
w.confirm = (message) => {
  confirmationMessage = message;
  return confirmResult;
};
w.ResizeObserver = class {
  observe() {}
};
w.HTMLCanvasElement.prototype.getContext = () =>
  new Proxy(
    {},
    {
      get: (t, k) => (typeof t[k] !== "undefined" ? t[k] : () => {}),
      set: (t, k, v) => {
        t[k] = v;
        return true;
      },
    },
  );
w.HTMLDialogElement.prototype.showModal = function () {
  this.setAttribute("open", "");
};
w.HTMLDialogElement.prototype.close = function () {
  this.removeAttribute("open");
};
const errors = [];
w.addEventListener("error", (e) => errors.push(e.message));
let downloadedBlob = null,
  downloadedFilename = null;
w.URL.createObjectURL = (blob) => {
  downloadedBlob = blob;
  return "blob:sample-export";
};
w.HTMLAnchorElement.prototype.click = function () {
  downloadedFilename = this.download;
};
const js = await fs.readFile(
  new URL("../trajectory_remix/static/app.js", import.meta.url),
  "utf8",
);
await w.eval(
  "(async()=>{" +
    js.replace(
      /setInterval\(\(\)=>action\(async\(\)=>\{const wasRunning[\s\S]*$/,
      "",
    ) +
    "\n})()",
);
const $ = (id) => w.document.getElementById(id);
const click = async (id) => {
  await $(id).onclick();
};
const waitFor = async (predicate, timeout = 3000) => {
  const deadline = Date.now() + timeout;
  while (!predicate() && Date.now() < deadline)
    await new Promise((resolve) => setTimeout(resolve, 20));
  assert(predicate(), "Timed out waiting for frontend state");
};
assert.equal(w.document.querySelectorAll(".window-card").length, 5);
assert.doesNotMatch($("window-grid").textContent, /<\|cxt\|>/);
// Tokenizer migration accepts pasted JSON and uploads, reports progress, and
// refreshes the current preview without requiring a page reload.
await click("file-menu-button");
await click("migrate-tokenizer-button");
assert.equal($("tokenizer-migration-dialog").open, true);
assert.equal($("tokenizer-eos").value, "<|eos|>");
$("tokenizer-config").value = "invalid JSON";
await $("tokenizer-migration-form").onsubmit({ preventDefault() {} });
assert.equal($("tokenizer-migration-error").hidden, false);
assert.match($("tokenizer-migration-error").textContent, /Invalid tokenizer configuration/);
assert.equal($("start-tokenizer-migration").disabled, false);
const migrationConfig = JSON.parse(await fs.readFile(new URL("../examples/demo-tokenizer.json", import.meta.url), "utf8"));
const migrationVocab = migrationConfig.model.vocab;
[migrationVocab.a, migrationVocab.b] = [migrationVocab.b, migrationVocab.a];
const tokenizerTransfer = new w.DataTransfer();
tokenizerTransfer.items.add(new w.File([JSON.stringify(migrationConfig)], "new-tokenizer.json"));
$("tokenizer-file").files = tokenizerTransfer.files;
await $("tokenizer-file").onchange();
assert.deepEqual(JSON.parse($("tokenizer-config").value), migrationConfig);
const beforeMigrationPlan = lastPlan;
const beforeMigrationSource = JSON.parse($("sample-json").value);
const migrationSubmit = $("tokenizer-migration-form").onsubmit({ preventDefault() {} });
assert.equal($("start-tokenizer-migration").disabled, true);
assert.equal($("tokenizer-migration-inputs").disabled, true);
assert.equal($("workspace").inert, true);
await migrationSubmit;
assert.equal($("tokenizer-migration-progress").value, 1);
assert.equal($("tokenizer-migration-count").textContent, "1 / 1 trajectories converted");
assert.match($("tokenizer-migration-message").textContent, /Migration complete/);
assert.equal($("tokenizer-migration-error").hidden, true);
assert.notEqual(lastPlan.tokenizer_sha256, beforeMigrationPlan.tokenizer_sha256);
assert.deepEqual(JSON.parse($("sample-json").value), beforeMigrationSource);
assert.equal($("workspace").inert, false);
await click("dismiss-tokenizer-migration");
assert.equal($("tokenizer-migration-dialog").open, false);
await click("preferences-button");
assert.equal($("show-window-headers").checked, false);
$("show-window-headers").checked = true;
await $("preferences-form").onsubmit({ preventDefault() {} });
assert.equal(
  w.localStorage.getItem("trajectory-remix.show-window-headers"),
  "true",
);
assert.match(
  $("window-grid").textContent,
  /<\|cxt\|>Window: \d+↵\nPurpose: reasoning<\|\/cxt\|>/,
);
assert.match($("window-grid").textContent, /slot \d+/);
// Each context window can be opened in a viewport-sized modal and restored.
const expandedDialog = $("expanded-window-dialog");
const initialWindowCount = w.document.querySelectorAll(".window-card").length;
const initialWindow = $("window-grid").querySelector(".window-card");
const initialWindowId = initialWindow.querySelector("strong").textContent;
const expandControl = initialWindow.querySelector(".window-size-toggle");
assert.equal(expandControl.textContent, "Expand");
assert.equal(expandControl.getAttribute("aria-label"), `Expand context window ${initialWindowId}`);
expandControl.onclick();
assert.equal(expandedDialog.open, true);
assert.equal($("window-grid").querySelectorAll(".window-card").length, initialWindowCount - 1);
assert.equal(expandedDialog.querySelector(".window-card strong").textContent, initialWindowId);
assert.equal(expandedDialog.querySelector(".window-size-toggle").textContent, "Shrink");
// A replay re-render preserves the expanded window, including its scroll position.
expandedDialog.querySelector(".window-content").scrollTop = 80;
$("tick-number").value = "1";
$("tick-number").onchange({ target: $("tick-number") });
assert.equal(expandedDialog.open, true);
assert.equal(expandedDialog.querySelector(".window-card strong").textContent, initialWindowId);
assert.equal(expandedDialog.querySelector(".window-content").scrollTop, 80);
$("tick-number").value = "0";
$("tick-number").onchange({ target: $("tick-number") });
expandedDialog.querySelector(".window-size-toggle").onclick();
assert.equal(expandedDialog.open, false);
assert.equal($("window-grid").querySelectorAll(".window-card").length, initialWindowCount);
assert.equal($("window-grid").querySelector(".window-size-toggle").textContent, "Expand");
// Native dialog closure (including Escape) must reset expansion state.
$("window-grid").querySelector(".window-size-toggle").onclick();
expandedDialog.close();
expandedDialog.onclose();
assert.equal(expandedDialog.open, false);
assert.equal($("window-grid").querySelectorAll(".window-card").length, initialWindowCount);
assert.equal($("sample-title"), null);
assert.equal(w.document.querySelector(".tabbar"), null);
assert.equal($("replay-view").querySelector("#dirty-indicator") !== null, true);
assert.equal($("apply-sample"), null);
assert.equal($("replay-view").hidden, false);
assert.equal(
  w.document.querySelector(".editor-panel").parentElement,
  $("workspace"),
);
assert.equal($("timeline").closest("main"), w.document.querySelector("main"));
assert.equal(w.document.title, "Trajectory Remix");
assert.equal(
  $("metadata-panel").closest(".sidebar"),
  w.document.querySelector(".sidebar"),
);
assert.equal($("metadata-fields").disabled, false);
assert.equal($("trajectory-uuid").readOnly, true);
assert.equal(
  $("trajectory-uuid").value,
  JSON.parse($("sample-json").value).uuid,
);
const selectedSample = JSON.parse($("sample-json").value);
confirmResult = false;
await click("edit-menu-button");
assert.equal($("edit-menu").hidden, false);
await click("delete-sample");
confirmResult = true;
assert.equal(
  confirmationMessage,
  `Delete sample "${selectedSample.id}" from the dataset?`,
);
assert.equal($("edit-menu").hidden, true);
assert.equal($("edit-menu-button").getAttribute("aria-expanded"), "false");
await click("file-menu-button");
await click("export-menu-button");
assert.equal($("export-menu").hidden, false);
assert.equal($("export-menu").contains($("export-sample")), true);
await click("export-sample");
assert.equal(downloadedFilename, `${selectedSample.id}.json`);
assert.deepEqual(JSON.parse(await downloadedBlob.text()), selectedSample);
const initialMetadata = JSON.parse($("metadata-json").value);
$("agent-name").value = "Nova";
$("agent-name").oninput();
$("user-name").value = "Alex";
$("user-name").oninput();
await [...w.document.querySelectorAll(".tab")]
  .find((b) => b.dataset.view === "json")
  .onclick();
let metadataDraft = JSON.parse($("sample-json").value);
assert.deepEqual(metadataDraft.metadata, {
  ...initialMetadata,
  agent_name: "Nova",
  user_name: "Alex",
});
metadataDraft.metadata.custom = { nested: [1, "kept"] };
$("sample-json").value = JSON.stringify(metadataDraft);
$("sample-json").oninput();
assert.deepEqual(JSON.parse($("metadata-json").value).custom, {
  nested: [1, "kept"],
});
$("user-name").value = "Sam";
$("user-name").oninput();
assert.equal(JSON.parse($("sample-json").value).metadata.user_name, "Sam");
await waitFor(
  () => !w.document.title.startsWith("*") && !$("undo-edit").disabled,
);
await click("undo-edit");
assert.equal($("agent-name").value, initialMetadata.agent_name || "");
await click("redo-edit");
assert.equal($("agent-name").value, "Nova");
assert.equal($("user-name").value, "Sam");
assert.deepEqual(JSON.parse($("metadata-json").value).custom, {
  nested: [1, "kept"],
});
await [...w.document.querySelectorAll(".tab")]
  .find((b) => b.dataset.view === "events")
  .onclick();
$("metadata-json").value = "[]";
$("metadata-json").oninput();
assert.match($("metadata-status").textContent, /JSON object/);
$("metadata-json").value = JSON.stringify({
  ...initialMetadata,
  agent_name: "Nova",
  user_name: "Sam",
  custom: { nested: [1, "kept"] },
});
$("metadata-json").oninput();
$("tick-number").value = "60";
$("tick-number").onchange({ target: $("tick-number") });
// Synchronize pending API reads in the emulated DOM, not a browser automation wait.
for (
  let i = 0;
  i < 30 && !$("window-grid").textContent.includes("A warning");
  i++
)
  await new Promise((r) => setTimeout(r, 20));
assert.match($("window-grid").textContent, /A warning/);
await click("next-tick");
assert.equal($("tick-number").value, "61");
await click("prev-tick");
assert.equal($("tick-number").value, "60");
await [...w.document.querySelectorAll(".tab")]
  .find((b) => b.dataset.view === "events")
  .onclick();
const eventCount = w.document.querySelectorAll(".event-card").length;
assert(eventCount >= 10);
assert.equal($("replay-view").hidden, false);
const firstEvent = w.document.querySelector(".event-card");
const firstEventKey = firstEvent.querySelector(".event-header input").value;
assert.equal(firstEvent.querySelector(".event-delete").textContent, "X");
assert.equal(
  firstEvent.querySelector(".event-delete").getAttribute("aria-label"),
  "Remove event",
);
assert.equal(firstEvent.querySelector(".event-add-below").textContent, "+");
await firstEvent.querySelector(".event-add-below").onclick();
const eventCards = w.document.querySelectorAll(".event-card");
assert.equal(eventCards.length, eventCount + 1);
assert.equal(
  eventCards[1].querySelector(".event-header input").value,
  firstEventKey,
);
await click("add-event");
assert.equal(w.document.title, "* Trajectory Remix");
assert.equal(w.document.querySelectorAll(".event-card").length, eventCount + 2);
await [...w.document.querySelectorAll(".tab")]
  .find((b) => b.dataset.view === "json")
  .onclick();
assert.equal($("replay-view").hidden, false);
const original = JSON.parse($("sample-json").value);
original.replay["1"][0].content = "bad{N_TICKS:1}";
$("sample-json").value = JSON.stringify(original);
$("sample-json").oninput();
assert.equal($("dirty-indicator").textContent, "Compile pending");
const compileCount = () =>
  requests.filter((request) => request.path === "/api/compile").length;
const compileCountBeforeEdit = compileCount();
await new Promise((resolve) => setTimeout(resolve, 600));
$("sample-json").oninput();
await new Promise((resolve) => setTimeout(resolve, 500));
assert.equal(compileCount(), compileCountBeforeEdit);
await waitFor(() => /Invalid marker/.test($("diagnostics").textContent));
assert.match($("diagnostics").textContent, /Invalid marker/);
assert.equal($("diagnostics").parentElement, $("events-view"));
assert($("diagnostics").querySelector(".diagnostics-close"));
await $("diagnostics").querySelector(".diagnostics-close").onclick();
assert.equal($("diagnostics").hidden, true);
original.replay["1"][0].content =
  "Scanning archive...\nWARNING: retrying chunk 7.{N_TICK:4} Backing off while the connection recovers. Resuming scan... Verified 192 files. Done.{N_TICK:5}";
// Remove the intentionally added unopened empty stream.
delete original.replay["170"];
for (const [key, events] of Object.entries(original.replay))
  original.replay[key] = events.filter((e) => e.window !== "new-window");
$("sample-json").value = JSON.stringify(original);
$("sample-json").oninput();
const compileCountBeforeCorrection = compileCount();
await waitFor(
  () =>
    compileCount() > compileCountBeforeCorrection &&
    !/Invalid marker/.test($("diagnostics").textContent),
);
assert.equal(w.document.title, "Trajectory Remix");
assert.doesNotMatch($("diagnostics").textContent, /Invalid marker/);
for (let i = 0; i < 30 && !$("window-grid").textContent.includes("↵"); i++)
  await new Promise((r) => setTimeout(r, 20));
assert(
  w.document.querySelector("#window-grid").textContent.includes("↵"),
  JSON.stringify($("window-grid").textContent),
);
await click("edit-menu-button");
await click("generation-menu-button");
await click("settings-button");
assert($("settings-dialog").hasAttribute("open"));
await w.document.querySelector('[data-config="endpoints"]').onclick();
assert.match($("config-content").textContent, /tokenizer directory \(optional\)/);
assert.match($("config-content").textContent, /Leave its directory blank to skip checks/);
const optionalTokenizer = w.document.querySelector('[data-config-path="endpoints.0.inference_tokenizer"]');
optionalTokenizer.value = "";
optionalTokenizer.dispatchEvent(new w.Event("input", {bubbles: true}));
assert.equal($("save-config").disabled, false);
await click("close-settings");
await click("edit-menu-button");
await click("generation-menu-button");
await click("status-button");
assert.equal($("generation-panel").hidden, false);
await click("edit-tags");
assert($("settings-dialog").hasAttribute("open"));
assert.equal(w.document.querySelector('[data-config="tasks"]'), null);
assert.equal(w.document.querySelector('[data-config="conditions"]'), null);
assert.match($("settings-dialog").textContent, /Generation Settings/);
await w.document.querySelector('[data-config="defaults"]').onclick();
assert.match($("config-content").textContent, /Request context budget/);
assert.doesNotMatch($("config-content").textContent, /Validate with LLM|Regenerations per attempt|Failure behavior/);
await click("save-config");
await click("close-settings");
assert.equal($("file-save"), null);
assert.equal($("file-save-as"), null);
assert.equal($("load-dataset"), null);
assert.equal($("checkpoint"), null);
assert.equal($("autosave-enabled"), null);
assert.equal($("autosave-minutes"), null);
assert.equal(w.document.querySelector("header .brand"), null);
assert.equal(
  [...w.document.querySelectorAll("header .menu-trigger, header .menu-label")]
    .map((item) => item.textContent)
    .join(""),
  "FileEditViewHelp",
);
const datasetSidebar = w.document.querySelector(".sidebar");
const datasetResizer = w.document.querySelector('[data-resize="sidebar"]');
assert.equal($("toggle-dataset-panel").getAttribute("role"), "menuitemcheckbox");
assert.equal(datasetSidebar.hidden, false);
assert.equal(datasetResizer.hidden, false);
assert.equal($("dataset-panel-check").textContent, "✔");
await click("view-menu-button");
assert.equal($("view-menu").hidden, false);
assert.equal($("view-menu-button").getAttribute("aria-expanded"), "true");
await click("toggle-dataset-panel");
assert.equal(datasetSidebar.hidden, true);
assert.equal(datasetResizer.hidden, true);
assert.equal($("workspace").classList.contains("dataset-panel-hidden"), true);
assert.equal($("toggle-dataset-panel").getAttribute("aria-checked"), "false");
assert.equal($("dataset-panel-check").textContent, "");
assert.equal($("view-menu").hidden, true);
assert.equal($("view-menu-button").getAttribute("aria-expanded"), "false");
await click("view-menu-button");
await click("toggle-dataset-panel");
assert.equal(datasetSidebar.hidden, false);
assert.equal(datasetResizer.hidden, false);
assert.equal($("workspace").classList.contains("dataset-panel-hidden"), false);
assert.equal($("toggle-dataset-panel").getAttribute("aria-checked"), "true");
assert.equal($("dataset-panel-check").textContent, "✔");
await click("view-menu-button");
await click("edit-menu-button");
assert.equal($("view-menu").hidden, true);
assert.equal($("edit-menu").hidden, false);
await click("edit-menu-button"); // Leave the existing menu tests in their initial closed state.
assert.equal($("file-menu").contains($("import-button")), true);
assert.equal($("generation-menu").contains($("settings-button")), true);
assert.equal($("generation-menu").contains($("generate-one")), true);
assert.equal($("generation-menu").contains($("generate-batch")), true);
for (const id of ["approve-sample", "validate-sample", "auto-start", "report-dialog"])
  assert.equal($(id), null);
await click("edit-menu-button");
assert.equal($("edit-menu").hidden, false);
assert.equal($("edit-menu").contains($("preferences-button")), true);
for (const id of ["duplicate", "delete-sample"])
  assert.equal($("edit-menu").contains($(id)), true);
assert.equal($("edit-menu").querySelectorAll('[role="separator"]').length, 3);
await click("preferences-button");
assert($("preferences-dialog").hasAttribute("open"));
assert.equal($("auto-compile-delay").value, "1");
$("auto-compile-delay").value = "0.2";
await $("preferences-form").onsubmit({ preventDefault() {} });
assert.equal($("preferences-dialog").hasAttribute("open"), false);
assert.equal(
  w.localStorage.getItem("trajectory-remix.auto-compile-delay"),
  "200",
);
await click("preferences-button");
await $("preferences-form").onsubmit({ preventDefault() {} });
await waitFor(() => !w.document.title.startsWith("*") && !$("undo-edit").disabled);
const compileCountBeforePreference = compileCount();
$("sample-json").oninput();
await waitFor(() => compileCount() > compileCountBeforePreference);
await click("edit-menu-button");
assert.equal($("edit-menu").hidden, false);
$("sample-json").focus();
$("sample-json").oninput();
assert.equal(w.document.title, "* Trajectory Remix");
const saveShortcut = new w.KeyboardEvent("keydown", {
  key: "s",
  ctrlKey: true,
  cancelable: true,
});
w.document.dispatchEvent(saveShortcut);
assert.equal(saveShortcut.defaultPrevented, true);
await waitFor(() =>
  requests.some(
    (request) =>
      request.path === "/api/samples/0" && request.options?.method === "PUT",
  ),
);
assert.equal(
  requests.some((request) => request.path === "/api/checkpoint"),
  false,
);
await waitFor(
  () => !w.document.title.startsWith("*") && !$("undo-edit").disabled,
);
assert.equal(w.document.title, "Trajectory Remix");

// External MCP edits refresh the selected sample; pending local edits survive a conflict.
const tool = async (name, args) => {
  const response = await fetch(base + "/api/mcp/call", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Remix-Token": w.document.querySelector('meta[name="remix-token"]')
        .content,
    },
    body: JSON.stringify({ name, arguments: args }),
  });
  assert.equal(response.status, 200, await response.clone().text());
  return response.json();
};
const currentId = JSON.parse($("sample-json").value).id;
await tool("update_trajectory_metadata", {
  sample_id: currentId,
  patch: { metadata: { title: "MCP metadata edit" } },
});
await waitFor(
  () =>
    JSON.parse($("sample-json").value).metadata?.title === "MCP metadata edit",
);
const localDraft = JSON.parse($("sample-json").value);
localDraft.metadata.title = "Pending browser draft";
$("sample-json").value = JSON.stringify(localDraft);
$("sample-json").oninput();
await tool("update_trajectory_metadata", {
  sample_id: currentId,
  patch: { metadata: { title: "Concurrent MCP edit" } },
});
await waitFor(() => /Dataset changed/.test($("toast").textContent));
await new Promise((resolve) => setTimeout(resolve, 1700));
assert.equal(
  JSON.parse($("sample-json").value).metadata.title,
  "Pending browser draft",
);
const remote = await tool("get_trajectory", { sample_id: currentId });
assert.equal(remote.sample.metadata.title, "Concurrent MCP edit");
await w.document.querySelector(".sample-item.active").onclick();
assert.equal(
  JSON.parse($("sample-json").value).metadata.title,
  "Concurrent MCP edit",
);
assert.equal(w.document.title, "Trajectory Remix");

// A revision observed while compilation is busy must still refresh on a later poll.
holdNextCompile = true;
const nextDraft = JSON.parse($("sample-json").value);
nextDraft.metadata.title = "Browser save before compile";
$("sample-json").value = JSON.stringify(nextDraft);
$("sample-json").oninput();
await waitFor(() => releaseCompile !== null);
await tool("update_trajectory_metadata", {
  sample_id: currentId,
  patch: { metadata: { title: "MCP edit during compile" } },
});
const stateReadsBeforePoll = completedStateReads;
await waitFor(() => completedStateReads > stateReadsBeforePoll);
await new Promise((resolve) => setTimeout(resolve, 50));
releaseCompile();
releaseCompile = null;
await waitFor(
  () =>
    JSON.parse($("sample-json").value).metadata.title ===
    "MCP edit during compile",
);
await waitFor(() => !$("undo-edit").disabled);
await click("undo-edit");
await waitFor(
  () =>
    JSON.parse($("sample-json").value).metadata.title ===
    "Browser save before compile",
);
await waitFor(() => !$("redo-edit").disabled);
await click("redo-edit");
await waitFor(
  () =>
    JSON.parse($("sample-json").value).metadata.title ===
    "MCP edit during compile",
);
await waitFor(() => !$("undo-edit").disabled);
$("undo-edit").focus();
w.document.dispatchEvent(
  new w.KeyboardEvent("keydown", { key: "z", ctrlKey: true }),
);
await waitFor(
  () =>
    JSON.parse($("sample-json").value).metadata.title ===
    "Browser save before compile",
);
await waitFor(() => !$("redo-edit").disabled);
w.document.dispatchEvent(
  new w.KeyboardEvent("keydown", { key: "z", ctrlKey: true, shiftKey: true }),
);
await waitFor(
  () =>
    JSON.parse($("sample-json").value).metadata.title ===
    "MCP edit during compile",
);
await waitFor(() => !$("undo-edit").disabled);
const pendingUndoDraft = JSON.parse($("sample-json").value);
pendingUndoDraft.metadata.title = "Pending draft to undo";
$("sample-json").value = JSON.stringify(pendingUndoDraft);
$("sample-json").oninput();
await click("undo-edit");
assert.equal(
  JSON.parse($("sample-json").value).metadata.title,
  "MCP edit during compile",
);
// Author an actual sandbox call and scrub across its completion in both directions.
await [...w.document.querySelectorAll(".tab")]
  .find((b) => b.dataset.view === "events")
  .onclick();
await click("add-tool-call");
const toolCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const at = toolCard.querySelector(".event-header input");
at.value = "150";
at.oninput();
const toolSelect = toolCard.querySelector(".event-options select");
toolSelect.value = "file_write";
toolSelect.onchange();
const updatedToolCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const args = updatedToolCard.querySelector(
  'textarea[aria-label="arguments JSON"]',
);
args.value = JSON.stringify({
  path: "history.txt",
  content: "new file version",
});
args.oninput();
const duration = updatedToolCard.querySelector(
  '.event-options input[type="number"]',
);
duration.value = "50";
duration.oninput();
await waitFor(() =>
  lastPlan?.tool_calls?.some(
    (call) =>
      call.tool === "file_write" &&
      call.end - call.start === 50 &&
      Number($("max-tick").textContent) === call.end,
  ),
);
const execution = lastPlan.tool_calls.find(
  (call) => call.tool === "file_write",
);
assert(execution.start > 150);
const jump = (value) => {
  $("tick-number").value = String(value);
  $("tick-number").onchange({ target: $("tick-number") });
};
const readFrame = async (at) => (await w.fetch(`/api/frame/${lastPlan.plan_id}/${at}`, {
  headers: { "X-Remix-Token": w.document.querySelector('meta[name="remix-token"]').content },
})).json();
jump(execution.start);
await waitFor(() =>
  /file_write · running/.test($("sandbox-calls").textContent),
);
assert.doesNotMatch($("sandbox-state").textContent, /new file version/);
jump(execution.end);
await waitFor(() => /new file version/.test($("sandbox-state").textContent));
assert.match($("sandbox-calls").textContent, /file_write · completed/);
const resultCards = w.document.querySelectorAll(".window-card").length;
$("timeline").onmousemove({
  target: $("timeline"),
  clientX:
    125 +
    execution.start * +$("timeline-zoom").value -
    $("timeline-scroll").scrollLeft,
  clientY: 35 + resultCards * 32,
});
assert.match(
  $("timeline-event").textContent,
  new RegExp(`file_write · ${execution.start}–${execution.end}`),
);
jump(100);
await waitFor(() => /"tick_rate": 10/.test($("sandbox-state").textContent));
assert.doesNotMatch($("sandbox-state").textContent, /new file version/);
// Tool windows exist at tick zero; result delivery changes independently of the request.
jump(0);
await waitFor(() => /Sandbox · tick 0/.test($("sandbox-title").textContent));
const toolWindowId = updatedToolCard.querySelector(".event-window").value;
const findToolWindow = () =>
  [...w.document.querySelectorAll(".window-card")].find(
    (card) => card.querySelector("strong").textContent === toolWindowId,
  );
assert.ok(findToolWindow());
assert.doesNotMatch(findToolWindow().className, /future/);
const resultDelivery = updatedToolCard.querySelectorAll(
  ".event-options select",
)[1];
assert.match(resultDelivery.parentElement.textContent, /Result delivery/);
resultDelivery.value = "stream";
resultDelivery.onchange();
const customRequest = updatedToolCard.querySelector('input[type="checkbox"]');
customRequest.checked = true;
customRequest.onchange();
const requestArea = w.document.querySelector(
  'textarea[aria-label="Tool request text"]',
);
requestArea.value =
  '<tool>{"tool":"file_write",<|noop|>"args":{"path":"history.txt","content":"new file version"}}</tool>';
requestArea.oninput();
await waitFor(
  () =>
    lastPlan?.events.some(
      (event) =>
        event.path.endsWith(".request") &&
        requestArea.value.includes('file_write",<|noop|>'),
    ) &&
    lastPlan.tool_calls[0].result_end > lastPlan.tool_calls[0].end &&
    Number($("max-tick").textContent) === lastPlan.max_tick,
);
jump(160);
await waitFor(() =>
  /file_write · composing/.test($("sandbox-calls").textContent),
);
assert.doesNotMatch($("sandbox-state").textContent, /new file version/);
assert.doesNotMatch(findToolWindow().textContent, /<tool_result>/);
jump(Number($("max-tick").textContent));
await waitFor(() =>
  /file_write · completed/.test($("sandbox-calls").textContent),
);
assert.match(findToolWindow().textContent, /<tool>/);
assert.match(findToolWindow().textContent, /<tool_result>/);
// Auto-generated anchors persist in source and mark the full streamed result.
const currentToolCard = () =>
  [...w.document.querySelectorAll(".event-card")].at(-1);
const anchorCheckbox = () =>
  [...currentToolCard().querySelectorAll("label")]
    .find((label) =>
      label.textContent.includes("Auto-generate post-result anchor"),
    )
    .querySelector('input[type="checkbox"]');
anchorCheckbox().checked = true;
anchorCheckbox().onchange();
await waitFor(() => lastPlan?.tool_calls[0].result_anchor !== undefined);
const resultAnchor = lastPlan.tool_calls[0].result_anchor;
assert.equal(
  lastPlan.anchors[resultAnchor].tick,
  lastPlan.tool_calls[0].result_end,
);
assert.match(
  currentToolCard().textContent,
  new RegExp(`Result complete: \\{N_TICK:${resultAnchor}\\}`),
);
await waitFor(
  () => !w.document.title.startsWith("*") && !$("undo-edit").disabled,
);
const savedSource = await (
  await w.fetch("/api/samples/0", {
    headers: {
      "X-Remix-Token": w.document.querySelector('meta[name="remix-token"]')
        .content,
    },
  })
).json();
assert.equal(savedSource.replay["150"][0]["result-anchor"], resultAnchor);
anchorCheckbox().checked = false;
anchorCheckbox().onchange();
await waitFor(() => lastPlan?.tool_calls[0].result_anchor === undefined);
// Chat is selectable as a synthetic tool and appends into the existing chat window.
await click("add-tool-call");
assert(![...w.document.querySelectorAll(".event-card")].at(-1).querySelector(".event-options select").querySelector('option[value="time_advance"]'));
let chatCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const chatTick = chatCard.querySelector(".event-header input");
chatTick.value = "0";
chatTick.oninput();
const chatSelect = chatCard.querySelector(".event-options select");
assert([...chatSelect.options].some((o) => o.value === "chat"));
chatSelect.value = "chat";
chatSelect.onchange();
chatCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const chatArgs = chatCard.querySelector(
  'textarea[aria-label="arguments JSON"]',
);
chatArgs.value = JSON.stringify({ content: "Hi, Sam!" });
chatArgs.oninput();
await waitFor(
  () => !w.document.title.startsWith("*") && !$("undo-edit").disabled,
);
jump(lastPlan.tool_calls.find((call) => call.tool === "chat").end);
await waitFor(() =>
  /<\|msg\|>Nova: Hi, Sam!<\|\/msg\|>/.test($("window-grid").textContent),
);
// External activity mutates the sandbox without creating a model window.
const windowCountBeforeExternal =
  w.document.querySelectorAll(".window-card").length;
await click("add-invisible-call");
const externalCard = [...w.document.querySelectorAll(".event-card")].at(-1);
assert.equal(externalCard.querySelector(".event-window").hidden, true);
assert.equal(
  externalCard.querySelector(".event-header select").value,
  "invisible-tool-call",
);
assert.equal(externalCard.querySelectorAll(".event-options select").length, 1);
const externalTick = externalCard.querySelector(".event-header input");
externalTick.value = "1000";
externalTick.oninput();
const externalArgs = externalCard.querySelector(
  'textarea[aria-label="arguments JSON"]',
);
externalArgs.value = JSON.stringify({
  path: "external.py",
  content: "external implementation",
});
externalArgs.oninput();
await waitFor(
  () => !w.document.title.startsWith("*") && !$("undo-edit").disabled,
);
jump(999);
await waitFor(() => /Sandbox · tick 999/.test($("sandbox-title").textContent));
await waitFor(() => !/external.py/.test($("sandbox-state").textContent));
jump(1000);
await waitFor(() =>
  /external implementation/.test($("sandbox-state").textContent),
);
assert.equal(
  w.document.querySelectorAll(".window-card").length,
  windowCountBeforeExternal,
);
assert.doesNotMatch($("window-grid").textContent, /external implementation/);
assert.match(
  $("sandbox-calls").textContent,
  /External · file_write · completed/,
);
// time_set initializes nullable arguments and is available for external mutations.
await click("add-tool-call");
let setCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const setSelect = setCard.querySelector(".event-options select");
setSelect.value = "time_set";
setSelect.onchange();
setCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const nullTime = {
  year: null,
  month: null,
  day: null,
  hour: null,
  minute: null,
  second: null,
  timezone: null,
};
assert.deepEqual(
  JSON.parse(
    setCard.querySelector('textarea[aria-label="arguments JSON"]').value,
  ),
  nullTime,
);
await waitFor(
  () => !w.document.title.startsWith("*") && !$("undo-edit").disabled,
);
const setCall = lastPlan.tool_calls.find((call) => call.tool === "time_set");
assert.equal(setCall.outcome.ok, true);
jump(setCall.end);
await waitFor(() =>
  /time_set · completed/.test($("sandbox-calls").textContent),
);
const setOperation = setCard.querySelector(".event-header select");
setOperation.value = "invisible-tool-call";
setOperation.onchange();
setCard = [...w.document.querySelectorAll(".event-card")].at(-1);
assert.equal(setCard.querySelector(".event-options select").value, "time_set");
assert.equal(setCard.querySelector(".event-window").hidden, true);
await waitFor(
  () => !w.document.title.startsWith("*") && !$("undo-edit").disabled,
);
assert.equal(
  lastPlan.tool_calls.find((call) => call.tool === "time_set").invisible,
  true,
);
// Listings are paged in SQLite, and search reaches trajectories beyond page one.
const pagedSamples = Array.from({ length: 205 }, (_, index) => ({
  "schema-version": "1.0",
  "tick-policy": "external-first",
  id: `sqlite-page-${index}`,
  replay: { 0: [{ op: "open", window: "a" }, {
    op: "append", window: "a", source: "user", content: index === 204 ? "Python humans math" : "complex",
  }] },
  metadata: { tags: index === 204 ? ["sqlite-last"] : [] },
}));
const imported = await fetch(base + "/api/import", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Remix-Token": w.document.querySelector('meta[name="remix-token"]')
      .content,
  },
  body: JSON.stringify({
    files: [{ name: "pages.json", text: JSON.stringify(pagedSamples) }],
  }),
});
assert.equal(imported.status, 200, await imported.clone().text());
$("sample-search").value = "sqlite-page-";
$("sample-search").oninput();
await waitFor(() => $("sample-page-label").textContent === "1–100 of 205");
assert.equal($("sample-list").children.length, 100);
await click("sample-page-next");
await waitFor(() => $("sample-page-label").textContent === "101–200 of 205");
assert.equal($("sample-list").children.length, 100);
assert.equal($("sample-page-prev").disabled, false);
$("sample-search").value = "python -(math complex) human* #sqlite-last @name:sqlite-page-* @windows:1";
$("sample-search").oninput();
await waitFor(() => $("sample-page-label").textContent === "1–1 of 1");
assert.equal($("sample-list").children.length, 1);
const searchGuide = await fetch(base + "/search-guide");
assert.equal(searchGuide.status, 200);
assert((await searchGuide.text()).includes("@uses_tool"));
assert($("search-help").querySelector('a[href="/search-guide"]'));
$("sample-search").value = "python |";
$("sample-search").oninput();
await waitFor(() => !$("search-error").hidden);
assert($("search-error").textContent.includes("Invalid search query"));
assert.equal($("sample-search").getAttribute("aria-invalid"), "true");
assert.equal($("sample-list").children.length, 0);
assert.equal($("sample-page-next").disabled, true);
// Returning to the last valid query must redraw even when revision/query match.
$("sample-search").value = "python -(math complex) human* #sqlite-last @name:sqlite-page-* @windows:1";
$("sample-search").oninput();
await waitFor(() => $("search-error").hidden && $("sample-list").children.length === 1);
assert.equal($("sample-search").hasAttribute("aria-invalid"), false);
$("sample-search").value = "sqlite-last";
$("sample-search").oninput();
await waitFor(() => $("sample-page-label").textContent === "1–1 of 1");
assert.equal($("sample-list").children.length, 1);
await $("sample-list").firstElementChild.onclick();
assert.equal(JSON.parse($("sample-json").value).id, "sqlite-page-204");
// Show-tools events advertise only checked tools and keep invisible arguments hidden.
await w.document.querySelector('.tab[data-view="events"]').onclick();
await click("add-show-tools");
let showCard = [...w.document.querySelectorAll(".event-card")].at(-1);
assert.equal(showCard.querySelector(".event-header select").value, "show_tools");
assert(![...showCard.querySelectorAll("label")].some((label) => label.textContent === "time_advance"));
const toolCheck = [...showCard.querySelectorAll("label")].find((label) => label.textContent === "chat").querySelector("input");
toolCheck.checked = true;
toolCheck.onchange();
await waitFor(() => !w.document.title.startsWith("*"));
await waitFor(() => lastPlan?.sample_id === "sqlite-page-204" && lastPlan.events.some((event) => event.key === "1" && event.op === "append"));
assert.deepEqual(JSON.parse($("sample-json").value).replay["1"][0].tools, ["chat"]);
const shown = (await readFrame(lastPlan.max_tick)).windows.a.text;
assert.match(shown, /"chat"/);
assert.doesNotMatch(shown, /"username"|"file_read"/);
const extraCheck = [...showCard.querySelectorAll("label")].find((label) => label.textContent === "Randomly add additional tools").querySelector("input");
extraCheck.checked = true;
extraCheck.onchange();
await waitFor(() => !w.document.title.startsWith("*") && !$("undo-edit").disabled && JSON.parse($("sample-json").value).replay["1"][0]["randomly-add-additional-tools"] === true);
assert.equal(JSON.parse($("sample-json").value).replay["1"][0]["randomly-add-additional-tools"], true);
// Per-trajectory tick rate is an ordinary persisted metadata setting.
$("tick-rate").value = "20";
$("tick-rate").oninput();
await waitFor(() => !w.document.title.startsWith("*") && !$("undo-edit").disabled && /"tick_rate": 20/.test($("sandbox-state").textContent));
assert.equal(JSON.parse($("sample-json").value).metadata.synthetic_clock.tick_rate, 20);
// New math, timer, and author-only load tools use the same actual editor flow.
await click("add-tool-call");
let mathCard = [...w.document.querySelectorAll(".event-card")].at(-1);
let mathSelect = mathCard.querySelector(".event-options select");
assert(mathSelect.querySelector('option[value="calculator"]'));
assert(!mathSelect.querySelector('option[value="hardware_load_set"]'));
assert(!mathSelect.querySelector('option[value="system_diagnostics_set"]'));
mathSelect.value = "calculator";
mathSelect.onchange();
mathCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const mathArgs = mathCard.querySelector('textarea[aria-label="arguments JSON"]');
mathArgs.value = JSON.stringify({expression: "sin(pi / 2) + sqrt(9)"});
mathArgs.oninput();
await waitFor(() => lastPlan.tool_calls.some((call) => call.tool === "calculator"));
assert.equal((await readFrame(lastPlan.max_tick)).tool_calls.find((call) => call.tool === "calculator").outcome.result.value, 4);
await click("add-tool-call");
let timerCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const timerSelect = timerCard.querySelector(".event-options select");
timerSelect.value = "timer";
timerSelect.onchange();
timerCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const timerArgs = timerCard.querySelector('textarea[aria-label="arguments JSON"]');
timerArgs.value = JSON.stringify({mode: "interval", seconds: 0.1, count: 2});
timerArgs.oninput();
await waitFor(() => lastPlan.tool_calls.some((call) => call.tool === "timer"));
const interval = lastPlan.tool_calls.find((call) => call.tool === "timer");
assert.equal((await readFrame(lastPlan.max_tick)).tool_calls.find((call) => call.tool === "timer").outputs.length, 2);
assert.equal(interval.end - interval.start, 4);
await click("add-invisible-call");
let loadCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const loadSelect = loadCard.querySelector(".event-options select");
assert(loadSelect.querySelector('option[value="hardware_load_set"]'));
loadSelect.value = "hardware_load_set";
loadSelect.onchange();
loadCard = [...w.document.querySelectorAll(".event-card")].at(-1);
const loadArgs = loadCard.querySelector('textarea[aria-label="arguments JSON"]');
loadArgs.value = JSON.stringify({file_io_ticks: 2});
loadArgs.oninput();
await waitFor(() => lastPlan.tool_calls.some((call) => call.tool === "hardware_load_set"));
jump(lastPlan.max_tick);
await waitFor(() => $("sandbox-state").textContent.includes('"file_io_ticks": 2'));
assert.match($("sandbox-state").textContent, /"file_io_ticks": 2/);
// Event and group collapse live in browser storage and never create trajectory edits.
const collapseUuid = JSON.parse($("sample-json").value).uuid;
const collapseKey = "trajectory-remix.event-collapse." + encodeURIComponent(collapseUuid);
const countSampleWrites = () =>
  requests.filter((r) => r.path.startsWith("/api/samples/") && r.options?.method === "PUT").length;
const countCompiles = () => requests.filter((r) => r.path === "/api/compile").length;
const initialWrites = countSampleWrites();
const initialCompiles = countCompiles();
const collapseCard = w.document.querySelector(".event-card");
const wasCollapsed = collapseCard.classList.contains("collapsed");
collapseCard.querySelector(".event-toggle").onclick();
assert.equal(w.document.querySelector(".event-card").classList.contains("collapsed"), !wasCollapsed);
const collapsedPrefs = JSON.parse(w.localStorage.getItem(collapseKey));
assert.equal(collapsedPrefs.events.includes(0), !wasCollapsed);
assert.equal(w.document.title, "Trajectory Remix");
await new Promise((resolve) => setTimeout(resolve, 300));
assert.equal(countSampleWrites(), initialWrites);
assert.equal(countCompiles(), initialCompiles);
await $("sample-list").querySelector(".active").onclick();
assert.equal(w.document.querySelector(".event-card").classList.contains("collapsed"), !wasCollapsed);
w.document.querySelector(".event-card .event-toggle").onclick();
assert.equal(w.document.querySelector(".event-card").classList.contains("collapsed"), wasCollapsed);
await click("add-group");
await waitFor(() => !w.document.title.startsWith("*") &&
  JSON.parse($("sample-json").value).metadata.event_groups?.length > 0);
const groupMetadata = JSON.parse($("sample-json").value).metadata.event_groups[0];
assert.equal("collapsed" in groupMetadata, false);
const groupWrites = countSampleWrites();
const groupCompiles = countCompiles();
w.document.querySelector(".event-group .event-toggle").onclick();
assert.equal(w.document.querySelector(".event-group").classList.contains("collapsed"), true);
assert.deepEqual(JSON.parse(w.localStorage.getItem(collapseKey)).groups, [groupMetadata.id]);
assert.equal(w.document.title, "Trajectory Remix");
await new Promise((resolve) => setTimeout(resolve, 300));
assert.equal(countSampleWrites(), groupWrites);
assert.equal(countCompiles(), groupCompiles);
await $("sample-list").querySelector(".active").onclick();
assert.equal(w.document.querySelector(".event-group").classList.contains("collapsed"), true);
assert.equal("collapsed_events" in JSON.parse($("sample-json").value).metadata, false);
assert.equal("collapsed" in JSON.parse($("sample-json").value).metadata.event_groups[0], false);
// Opening old samples migrates legacy metadata collapse flags to local storage.
await w.document.querySelector('.tab[data-view="json"]').onclick();
const legacy = JSON.parse($("sample-json").value);
legacy.metadata.collapsed_events = [0];
legacy.metadata.event_groups[0].collapsed = true;
$("sample-json").value = JSON.stringify(legacy);
$("sample-json").oninput();
w.localStorage.removeItem(collapseKey);
await w.document.querySelector('.tab[data-view="events"]').onclick();
assert.equal(w.document.querySelector(".event-card").classList.contains("collapsed"), true);
assert.equal(w.document.querySelector(".event-group").classList.contains("collapsed"), true);
assert.deepEqual(JSON.parse(w.localStorage.getItem(collapseKey)).events, [0]);
assert.deepEqual(JSON.parse(w.localStorage.getItem(collapseKey)).groups, [groupMetadata.id]);
await waitFor(() => !w.document.title.startsWith("*") &&
  !("collapsed_events" in JSON.parse($("sample-json").value).metadata));
assert.equal("collapsed" in JSON.parse($("sample-json").value).metadata.event_groups[0], false);
await click("generate-batch");
assert.equal($("generation-count").value, "10");
await click("generate-one");
assert.equal($("generation-count").value, "1");
assert.equal($("generation-panel").hidden, false);
assert.equal($("scenario-type").value, "user_scenario");
assert.equal($("scenario-start"), null);
assert.equal($("export-button"), null);
assert.equal($("pause-run").textContent, "Start");
assert.equal($("pause-run").disabled, false);
assert.equal($("generation-progress").getAttribute("aria-label"), "Generation progress");
assert.match($("generation-progress").title, /steps complete/);
assert.equal($("scenario-tag-controls").hidden, true);
const beforeBatchStartFetch = w.fetch;
let batchStartBody;
let batchQueryRequested = false;
w.fetch = async (path, options) => {
  if (path === "/api/scenarios/run") {
    batchStartBody = JSON.parse(options.body);
    return new w.Response(JSON.stringify({ok:true}), {status:200, headers:{"content-type":"application/json"}});
  }
  if (String(path).startsWith("/api/state?")) {
    const url = new URL(path, base);
    if (url.searchParams.get("query") === "@batch:test-batch") {
      batchQueryRequested = true;
      url.searchParams.set("query", "");
    }
    const response = await beforeBatchStartFetch(url.pathname + url.search, options);
    const data = await response.json();
    data.status.batch = {id:"test-batch", target:3, completed:3, attempts:3, attempt_limit:9, state:"complete"};
    return new w.Response(JSON.stringify(data), {status:200, headers:{"content-type":"application/json"}});
  }
  return beforeBatchStartFetch(path, options);
};
$("generation-count").value = "3";
await click("pause-run");
assert.equal(batchStartBody.batch_size, 3);
assert.equal(batchStartBody.scenario, "user_scenario");
assert.match($("batch-progress").textContent, /3\/3 completed/);
await click("view-generation-batch");
assert.equal($("sample-search").value, "@batch:test-batch");
assert.equal(batchQueryRequested, true);
assert.equal($("generation-panel").hidden, true);
w.fetch = beforeBatchStartFetch;
$("sample-search").value = "";
$("sample-search").oninput();
await waitFor(() => $("view-generation-batch").hidden, 5000);
$("generation-count").value = "1";
$("randomize-tags").checked = false; $("randomize-tags").onchange();
assert.equal($("scenario-tag-controls").hidden, false);
$("randomize-tags").checked = true; $("randomize-tags").onchange();
assert.equal($("scenario-tag-controls").hidden, true);
assert.equal($("view-generation-draft").parentElement.lastElementChild.id, "view-generation-draft");
assert.equal($("view-generation-draft").hidden, true);
assert.equal($("stop-run").textContent, "Cancel");
await click("close-generation-panel");
assert.equal($("generation-panel").hidden, true);
// Semantic validation is an explicit operation on the saved current sample.
assert.equal($("validate-trajectory").parentElement.id, "generation-menu");
assert.equal($("validate-trajectory").textContent, "Validate Trajectory");
await click("validate-trajectory");
assert($("validation-dialog").hasAttribute("open"));
assert.equal($("validation-record").checked, true);
const beforeValidationFetch = w.fetch;
let validationBody, validationStatus = {running:false, phase:"idle", completed:0, total:4, active:[]};
w.fetch = async (path, options) => {
  if (path === "/api/validation/run") {
    validationBody = JSON.parse(options.body);
    validationStatus = {running:true, phase:"validating", completed:1, total:4, active:["layout"], sample_id:validationBody.sample_id};
    return new w.Response(JSON.stringify({job_id:"review",status:validationStatus}), {status:202,headers:{"content-type":"application/json"}});
  }
  if (path === "/api/validation/stop") {
    validationStatus = {...validationStatus,running:false,phase:"cancelled",active:[]};
    return new w.Response(JSON.stringify(validationStatus), {status:200,headers:{"content-type":"application/json"}});
  }
  const response = await beforeValidationFetch(path, options);
  if (String(path).startsWith("/api/state?")) {
    const data = await response.json(); data.validation_status = validationStatus;
    return new w.Response(JSON.stringify(data), {status:200,headers:{"content-type":"application/json"}});
  }
  return response;
};
await click("start-validation");
assert.equal(validationBody.record, true);
assert.equal(validationBody.endpoint, "validator");
assert.equal($("validation-progress").value, 1);
assert.equal($("start-validation").disabled, true);
assert.equal($("generate-one").disabled, true);
await click("cancel-validation");
assert.equal($("resume-validation").hidden, false);
await click("resume-validation");
assert.equal(validationBody.resume, true);
await click("cancel-validation");
await click("close-validation");
w.fetch = beforeValidationFetch;

await click("settings-button");
await w.document.querySelector('[data-config="endpoints"]').onclick();
assert.equal($("save-config").disabled, true);
const timeoutBox = w.document.querySelector('[data-config-path="endpoints.0.timeout"]');
const urlBox = w.document.querySelector('[data-config-path="endpoints.0.base_url"]');
urlBox.value = "http://localhost:9090/v1";
urlBox.dispatchEvent(new w.Event("input", {bubbles:true}));
timeoutBox.value = "999";
timeoutBox.dispatchEvent(new w.Event("input", {bubbles:true}));
assert.equal(timeoutBox.getAttribute("aria-invalid"), "true");
assert.equal($("save-config").disabled, true);
await click("close-settings");
await click("settings-button");
assert.equal(w.document.querySelector('[data-config-path="endpoints.0.base_url"]').value, "http://localhost:9090/v1");
const fixedTimeout = w.document.querySelector('[data-config-path="endpoints.0.timeout"]');
fixedTimeout.value = "120";
fixedTimeout.dispatchEvent(new w.Event("input", {bubbles:true}));
assert.equal(fixedTimeout.getAttribute("aria-invalid"), "false");
assert.equal($("save-config").disabled, false);
await click("save-config");
assert.equal($("save-config").disabled, true);
await click("close-settings");
// Guidance settings persist Markdown and the editor shows the exact MCP bundle.
await click("view-menu-button");
await click("guidelines-button");
assert($("guidelines-dialog").open);
assert($("guidelines-content").querySelector("h1"));
await click("edit-guidelines");
assert($("settings-dialog").open);
assert($("custom-guide"));
const customMarkdown = "# Dataset Names\nUse **random usernames**.\n- [ ] Include a joke.\n<img src=x onerror=alert(1)>\n[unsafe](javascript:alert(1))\n\n```text\nExact code\n```";
$("custom-guide").value = customMarkdown;
$("custom-guide").dispatchEvent(new w.Event("input", {bubbles: true}));
$("custom-checklist").value = "- [ ] Check every window.";
$("custom-checklist").dispatchEvent(new w.Event("input", {bubbles: true}));
await click("save-config");
assert.equal($("save-config").disabled, true);
await click("close-settings");
await click("guidelines-button");
const guideTab = title => [...$("guidelines-tabs").children].find(tab => tab.textContent === title);
assert(guideTab("Dataset Guide"));
guideTab("Dataset Guide").onclick();
assert.equal($("guidelines-content").querySelector("strong").textContent, "random usernames");
assert.equal($("guidelines-content").querySelector("img"), null);
assert.equal($("guidelines-content").querySelector("a"), null);
assert.equal($("guidelines-content").querySelector('input[type="checkbox"]').disabled, true);
$("guidelines-source").checked = true; $("guidelines-source").onchange();
assert.equal($("guidelines-content").textContent, customMarkdown);
$("guidelines-source").checked = false; $("guidelines-source").onchange();
$("guidelines-scenario").value = "scenario:user_scenario";
await $("guidelines-scenario").onchange();
assert(guideTab("Review Checklist"));
assert(guideTab("idea.md"));
guideTab("Review Checklist").onclick();
assert.match($("guidelines-content").textContent, /user_scenario/);
await click("edit-guidelines");
assert.equal($("custom-guidance-scenario").value, "user_scenario");
$("custom-guide").value = "# Scenario Guide\nMake a pun.";
$("custom-guide").dispatchEvent(new w.Event("input", {bubbles: true}));
await click("save-config");
$("custom-guidance-scenario").value = ""; $("custom-guidance-scenario").onchange();
assert.equal($("custom-guide").value, customMarkdown);
await click("close-settings");
await click("guidelines-button");
$("guidelines-scenario").value = "scenario:user_scenario"; await $("guidelines-scenario").onchange();
guideTab("Scenario user_scenario Guide").onclick();
assert.match($("guidelines-content").textContent, /Make a pun/);
const sharedGuidance = await (await w.fetch("/api/mcp/call", {method: "POST", headers: {
  "X-Remix-Token": w.document.querySelector('meta[name="remix-token"]').content,
  "Content-Type": "application/json",
}, body: JSON.stringify({name: "get_guidance", arguments: {scenario: "user_scenario"}})})).json();
assert.equal(sharedGuidance.documents.find(d => d.id === "custom.dataset.guide").content, customMarkdown);
assert.equal(sharedGuidance.documents.find(d => d.id === "custom.scenario.guide").content, "# Scenario Guide\nMake a pun.");
await click("close-guidelines");
const manualTag = $("scenario-tag-controls").querySelector("select");
assert(manualTag);
manualTag.value = manualTag.options[manualTag.options.length - 1].value;
manualTag.onchange();
assert.equal($("randomize-tags").checked, false);
$("randomize-tags").checked = true;
await click("export-generation-log");
assert.equal(downloadedFilename, "generation.log");
assert.equal(typeof await downloadedBlob.text(), "string");
const actualFetch = w.fetch;
let logFixture = [{time:"2026-10-25T00:00:00Z", level:"info", message:"Stable log entry"}];
w.fetch = async (path, options) => {
  const response = await actualFetch(path, options);
  if (path.startsWith("/api/state?")) {
    const data = await response.json(); data.logs = logFixture;
    return new w.Response(JSON.stringify(data), {status:200, headers:{"content-type":"application/json"}});
  }
  return response;
};
await waitFor(() => $("activity-log").textContent.includes("Stable log entry"), 5000);
const retainedLogNode = $("activity-log").firstChild;
const previousReads = completedStateReads;
await waitFor(() => completedStateReads > previousReads, 5000);
assert.equal($("activity-log").firstChild, retainedLogNode);
Object.defineProperty($("activity-log"), "scrollHeight", {value:1000, configurable:true});
Object.defineProperty($("activity-log"), "clientHeight", {value:100, configurable:true});
$("activity-log").scrollTop = 100;
logFixture = [...logFixture, {time:"2026-10-25T00:00:01Z", level:"info", message:"New log entry"}];
await waitFor(() => $("activity-log").textContent.includes("New log entry"), 5000);
assert.equal($("activity-log").scrollTop, 100);
logFixture = [];
await click("clear-generation-log");
await waitFor(() => !$("activity-log").textContent, 5000);
assert.equal(w.document.querySelectorAll("[data-validation-state]").length, 7);
await w.document.querySelector('[data-validation-state="0"]').onclick();
await waitFor(() => $("sample-list").textContent.includes("Pending"), 5000);
assert.equal($("set-state-menu-button").disabled, false);
await waitFor(() => w.document.querySelector('[data-validation-state="0"]').getAttribute("aria-checked") === "true", 5000);
assert.equal(w.document.querySelectorAll('#set-state-menu [aria-checked="true"]').length, 1);
await w.document.querySelector('[data-validation-state="3"]').onclick();
await waitFor(() => $("sample-list").textContent.includes("Ready"), 5000);
await waitFor(() => w.document.querySelector('[data-validation-state="3"]').getAttribute("aria-checked") === "true", 5000);
assert.equal(w.document.querySelector('[data-validation-state="0"]').getAttribute("aria-checked"), "false");
assert.equal($("resume-trajectory-generation").disabled, true);
await click("preferences-button");
assert.equal($("model-api-retries").value, "2");
$("model-api-retries").value = "1";
await $("preferences-form").onsubmit({preventDefault(){}});
await click("preferences-button");
assert.equal($("model-api-retries").value, "1");
await click("cancel-preferences");
const beforeResumeFetch = w.fetch;
let resumeRequests = 0;
w.fetch = async (path, options) => {
  if (path === "/api/scenarios/resume") {
    resumeRequests++;
    const confirmed = JSON.parse(options.body).confirm_edited;
    return new w.Response(JSON.stringify(confirmed ? {ok:true} : {detail:"Trajectory edited since checkpoint", confirmation_required:true}), {status:confirmed ? 200 : 409, headers:{"content-type":"application/json"}});
  }
  return beforeResumeFetch(path, options);
};
confirmResult = false;
await click("resume-trajectory-generation");
assert.equal(resumeRequests, 1);
assert.match(confirmationMessage, /replace those edits/);
confirmResult = true;
await click("resume-trajectory-generation");
assert.equal(resumeRequests, 3);
w.fetch = beforeResumeFetch;
// Evaluation failures retain the existing candidate and resume the batch.
const beforeEvaluationFetch = w.fetch;
let evaluationResumed = false;
w.fetch = async (path, options) => {
  if (path === "/api/scenarios/resume") {
    assert.equal(JSON.parse(options.body).sample_id, "evaluation-candidate");
    evaluationResumed = true;
    return new w.Response(JSON.stringify({ok:true}), {status:200});
  }
  if (path === "/api/scenarios/start") assert.fail("Evaluation recovery must not start a fresh sample");
  const response = await beforeEvaluationFetch(path, options);
  if (path.startsWith("/api/state?")) {
    const data = await response.json();
    data.status = {...data.status, running:false, phase:"evaluation_failed",
      draft_id:"evaluation-candidate", last_error:"Evaluation failed; review context unavailable",
      batch:{id:"evaluation-batch", target:3, completed:0, attempts:1, attempt_limit:9, state:"paused"}};
    return new w.Response(JSON.stringify(data), {status:200, headers:{"content-type":"application/json"}});
  }
  return response;
};
await waitFor(() => $("pause-run").textContent === "Resume", 5000);
assert.equal($("generation-count").value, "3");
assert.match($("generation-detail").textContent, /Evaluation failed/);
await click("pause-run");
assert.equal(evaluationResumed, true);
w.fetch = beforeEvaluationFetch;
await waitFor(() => $("pause-run").textContent === "Start", 5000);
// A short source can expand past 50,000 records because open windows idle.
await w.document.querySelector('.tab[data-view="json"]').onclick();
const largeSource = {
  "schema-version": "1.0", "tick-policy": "external-first", id: "large-preview",
  uuid: JSON.parse($("sample-json").value).uuid,
  replay: {
    0: ["a", "b", "c"].map((window) => ({ op: "open", window })),
    1: [{ op: "append", window: "a", source: "user", content: "earlier" },
        { op: "append", window: "b", source: "user", content: "old" }],
    20000: [{ op: "generate", window: "a", content: "xyz<|eos|>" }],
    20001: [{ op: "invisible-tool-call", tool: "file_write", arguments: {path: "x", content: "new-state"}, "duration-ticks": 3 }],
    20002: [{ op: "append", window: "b", source: "user", content: "replacement", "replace-content": true }],
    20015: [{ op: "append", window: "b", source: "user", content: "tail" }],
  },
};
$("sample-json").value = JSON.stringify(largeSource);
$("sample-json").oninput();
await waitFor(() => lastPlan?.sample_id === "large-preview" && lastPlan.row_count > 50000 &&
  !$("undo-edit").disabled && !$("window-grid").textContent.includes("Loading preview"));
assert.equal("rows" in lastPlan, false);
assert.equal("sandbox" in lastPlan, false);
assert(JSON.stringify(lastPlan).length < 10000);
const largeCompileCount = compileCount();
$("timeline").onmousemove({
  target: $("timeline"),
  clientX: 125 + 20000 * +$("timeline-zoom").value - $("timeline-scroll").scrollLeft,
  clientY: 35,
});
assert.match($("timeline-event").textContent, /generate · 20000–20003/);
jump(20004);
await waitFor(() => /new-state/.test($("sandbox-state").textContent));
assert.match($("window-grid").textContent, /earlierxyz/);
assert.match($("window-grid").textContent, /replacement/);
jump(1);
assert.doesNotMatch($("window-grid").textContent, /earlierxyz|replacement/);
await waitFor(() => /old/.test($("window-grid").textContent));
assert.doesNotMatch($("sandbox-state").textContent, /new-state/);
// A seek during a delayed range response must settle on the latest tick.
holdNextFrames = true;
jump(19999);
await waitFor(() => releaseFrames !== null);
assert.match($("window-grid").textContent, /Loading preview/);
jump(20002);
releaseFrames();
releaseFrames = null;
await waitFor(() => /replacement/.test($("window-grid").textContent));
assert.equal($("tick-number").value, "20002");
assert.doesNotMatch($("sandbox-state").textContent, /new-state/);
// Slow loading buffers playback at a chunk boundary rather than skipping ticks.
holdNextFrames = true;
$("play-speed").value = "120";
await click("play");
await waitFor(() => releaseFrames !== null);
assert.equal($("tick-number").value, "20007");
await new Promise((resolve) => setTimeout(resolve, 60));
assert.equal($("tick-number").value, "20007");
releaseFrames();
releaseFrames = null;
await waitFor(() => $("tick-number").value === "20015" && $("play").textContent === "Play");
assert.match($("window-grid").textContent, /replacementtail/);
assert.match($("sandbox-state").textContent, /new-state/);
assert.equal(compileCount(), largeCompileCount);
const rangeRequests = requests.filter((r) => r.path.startsWith(`/api/frames/${lastPlan.plan_id}?`));
assert(rangeRequests.some((r) => r.path.includes("start_tick=19999")));
assert(rangeRequests.length < 12);
for (const request of rangeRequests) {
  const query = new URL(request.path, base).searchParams;
  assert(Number(query.get("end_tick")) - Number(query.get("start_tick")) < 8);
}
// An oversized range falls back to one frame; expiry does not cause a retry loop.
nextFramesError = { code: "preview_limit", detail: "Preview range exceeds 4 MB; request fewer frames" };
jump(1000);
await waitFor(() => requests.some((r) => r.path === `/api/frame/${lastPlan.plan_id}/1000`) &&
  !$("window-grid").textContent.includes("Loading preview"));
nextFramesError = { detail: "Preview expired; compile the sample again" };
jump(500);
await waitFor(() => /Preview unavailable: Preview expired/.test($("window-grid").textContent));
const frameRequestCount = () => requests.filter((r) => /^\/api\/frames?\//.test(r.path)).length;
const expiredRequestCount = frameRequestCount();
await new Promise((resolve) => setTimeout(resolve, 100));
assert.equal(frameRequestCount(), expiredRequestCount);

const emptyStateResponse = await w.fetch("/api/state", {headers: {
  "X-Remix-Token": w.document.querySelector('meta[name="remix-token"]').content,
}});
assert.equal(emptyStateResponse.ok, true);
const emptyState = await emptyStateResponse.json();
const beforeEmptyFetch = w.fetch;
w.fetch = async (path, options) => {
  if (String(path).startsWith("/api/state")) return new w.Response(JSON.stringify({
    ...emptyState, samples: [], sample_count: 0, total: 0, selected_index: -1,
    revision: emptyState.revision + 1,
    status: {...emptyState.status, draft_id: null, draft_index: null},
  }), {status: 200, headers: {"content-type": "application/json"}});
  return beforeEmptyFetch(path, options);
};
await waitFor(() => $("set-state-menu-button").disabled, 5000);
assert.equal($("set-state-menu").hidden, true);
assert.equal(w.document.querySelectorAll('#set-state-menu [aria-checked="true"]').length, 0);
assert.equal([...w.document.querySelectorAll("[data-validation-state]")].every(button => button.disabled), true);
assert.deepEqual(errors, []);
console.log(
  JSON.stringify({
    test: "DOM integration against live local backend",
    checks: 140,
    errors,
  }),
);
await w.happyDOM.abort();
w.close();
