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
w.fetch = (path, options) => {
  requests.push({ path, options });
  return fetch(new URL(path, base), options);
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
assert.equal(w.document.querySelectorAll(".window-card").length, 5);
assert.equal($("sample-title").textContent, "react-to-stream-warning");
assert.equal($("apply-sample").disabled, true);
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
await click("add-event");
assert.equal(w.document.title, "* Trajectory Remix");
assert.equal(w.document.querySelectorAll(".event-card").length, eventCount + 1);
await [...w.document.querySelectorAll(".tab")]
  .find((b) => b.dataset.view === "json")
  .onclick();
const original = JSON.parse($("sample-json").value);
original.replay["1"][0].content = "bad{N_TICKS:1}";
$("sample-json").value = JSON.stringify(original);
$("sample-json").oninput();
assert.equal($("apply-sample").disabled, false);
await click("apply-sample");
assert.match($("diagnostics").textContent, /Invalid marker/);
assert($("diagnostics").querySelector(".diagnostics-close"));
await $("diagnostics").querySelector(".diagnostics-close").onclick();
assert.equal($("diagnostics").hidden, true);
original.replay["1"][0].content =
  "Scanning archive... WARNING: retrying chunk 7.{N_TICK:4} Backing off while the connection recovers. Resuming scan... Verified 192 files. Done.{N_TICK:5}";
// Remove the intentionally added unopened empty stream.
delete original.replay["170"];
for (const [key, events] of Object.entries(original.replay))
  original.replay[key] = events.filter((e) => e.window !== "new-window");
$("sample-json").value = JSON.stringify(original);
$("sample-json").oninput();
await click("apply-sample");
assert.equal(w.document.title, "Trajectory Remix");
assert.equal($("apply-sample").disabled, true);
assert.doesNotMatch($("diagnostics").textContent, /Invalid marker/);
await click("settings-button");
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
assert.equal($("edit-menu").contains($("settings-button")), true);
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
assert.deepEqual(errors, []);
console.log(
  JSON.stringify({
    test: "DOM integration against live local backend",
    checks: 22,
    errors,
  }),
);
await w.happyDOM.abort();
w.close();
