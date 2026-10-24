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
  completedStateReads = 0,
  lastPlan = null,
  confirmResult = true,
  confirmationMessage = null;
w.fetch = async (path, options) => {
  requests.push({ path, options });
  const response = await fetch(new URL(path, base), options);
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
await click("close-settings");
await click("edit-menu-button");
await click("generation-menu-button");
await click("status-button");
assert.equal($("generation-panel").hidden, false);
await click("edit-tags");
assert($("settings-dialog").hasAttribute("open"));
await w.document.querySelector('[data-config="tasks"]').onclick();
assert.equal(
  w.document.querySelectorAll("#config-content .config-card").length,
  7,
);
await w.document.querySelector('[data-config="conditions"]').onclick();
assert(w.document.querySelectorAll(".tag-picker button").length >= 40);
await w.document.querySelector('[data-config="automatic"]').onclick();
assert.match($("config-content").textContent, /Consecutive failure limit/);
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
  "FileEditHelp",
);
assert.equal($("file-menu").contains($("import-button")), true);
assert.equal($("generation-menu").contains($("settings-button")), true);
for (const id of ["generate-one", "validate-sample", "auto-start"])
  assert.equal($("generation-menu").contains($(id)), true);
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
      call.arguments.content === "new file version" &&
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
        event.text.includes('file_write",<|noop|>'),
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
  $("sandbox-state").textContent.includes(setCall.outcome.result.timestamp),
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
  replay: { 0: [{ op: "open", window: "a" }] },
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
await waitFor(() => lastPlan?.sample_id === "sqlite-page-204" && lastPlan.events.some((event) => event.key === "1" && event.text.includes('"chat"')));
assert.deepEqual(JSON.parse($("sample-json").value).replay["1"][0].tools, ["chat"]);
const shown = lastPlan.events.find((event) => event.key === "1").text;
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
await waitFor(() => !w.document.title.startsWith("*") && !$("undo-edit").disabled && lastPlan?.sandbox.initial.tick_rate === 20);
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
assert.equal(lastPlan.tool_calls.find((call) => call.tool === "calculator").outcome.result.value, 4);
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
assert.equal(interval.outputs.length, 2);
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
assert.deepEqual(errors, []);
console.log(
  JSON.stringify({
    test: "DOM integration against live local backend",
    checks: 99,
    errors,
  }),
);
await w.happyDOM.abort();
w.close();
