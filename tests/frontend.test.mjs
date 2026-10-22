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
  completedStateReads = 0;
w.fetch = async (path, options) => {
  requests.push({ path, options });
  const response = await fetch(new URL(path, base), options);
  if (path === "/api/state") completedStateReads++;
  if (path === "/api/compile" && holdNextCompile) {
    holdNextCompile = false;
    await new Promise((resolve) => (releaseCompile = resolve));
  }
  return response;
};
w.confirm = () => true;
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
assert($("file-save").textContent === "Save");
assert.equal($("file-save-as"), null);
assert.equal($("load-dataset"), null);
assert.equal($("checkpoint"), null);
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
assert.equal($("autosave-enabled").checked, true);
assert.equal($("autosave-minutes").value, "5");
$("auto-compile-delay").value = "0.2";
$("autosave-minutes").value = "2";
await $("preferences-form").onsubmit({ preventDefault() {} });
assert.equal($("preferences-dialog").hasAttribute("open"), false);
assert.equal(
  w.localStorage.getItem("trajectory-remix.auto-compile-delay"),
  "200",
);
assert.equal(
  w.localStorage.getItem("trajectory-remix.autosave-enabled"),
  "true",
);
assert.equal(w.localStorage.getItem("trajectory-remix.autosave-minutes"), "2");
await click("preferences-button");
assert.equal($("autosave-minutes").value, "2");
$("autosave-enabled").checked = false;
$("autosave-enabled").onchange();
assert.equal($("autosave-minutes").disabled, true);
await $("preferences-form").onsubmit({ preventDefault() {} });
assert.equal(
  w.localStorage.getItem("trajectory-remix.autosave-enabled"),
  "false",
);
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
for (
  let i = 0;
  i < 30 && !requests.some((request) => request.path === "/api/checkpoint");
  i++
)
  await new Promise((r) => setTimeout(r, 20));
assert(requests.some((request) => request.path === "/api/checkpoint"));
for (
  let i = 0;
  i < 30 &&
  (w.document.title.startsWith("*") ||
    !/^Saved /.test($("save-status").textContent));
  i++
)
  await new Promise((r) => setTimeout(r, 20));
assert.match($("save-status").textContent, /^Saved /);
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
assert.deepEqual(errors, []);
console.log(
  JSON.stringify({
    test: "DOM integration against live local backend",
    checks: 48,
    errors,
  }),
);
await w.happyDOM.abort();
w.close();
