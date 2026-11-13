import { Window } from "happy-dom";
import fs from "node:fs/promises";
import assert from "node:assert/strict";

const base = process.argv[2];
const w = new Window({url: base, settings: {
  disableCSSFileLoading: true, disableJavaScriptFileLoading: true,
}});
w.document.write(await (await fetch(base)).text());
const style = w.document.createElement("style");
style.textContent = await fs.readFile(new URL("../trajectory_remix/static/style.css", import.meta.url), "utf8");
w.document.head.append(style);
w.structuredClone = structuredClone;
w.confirm = () => true;
w.ResizeObserver = class { observe() {} };
w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
  get: (target, key) => target[key] ?? (() => {}),
  set: (target, key, value) => { target[key] = value; return true; },
});
w.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
const errors = [];
w.addEventListener("error", event => errors.push(event.message));
const $ = id => w.document.getElementById(id);
const headers = {"Content-Type": "application/json", "X-Remix-Token":
  w.document.querySelector('meta[name="remix-token"]').content};
const request = async (path, method = "GET", body) => {
  const response = await fetch(base + "/api/" + path, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert(response.ok, await response.clone().text());
  return response.json();
};
const waitFor = async predicate => {
  const deadline = Date.now() + 5000;
  while (!predicate() && Date.now() < deadline)
    await new Promise(resolve => setTimeout(resolve, 10));
  assert(predicate(), "Timed out waiting for editor state");
};
const holds = [];
w.fetch = async (path, options) => {
  const response = await fetch(new URL(path, base), options);
  const hold = holds.find(item => !item.release && item.matches(path, options));
  if (hold) await new Promise(resolve => { hold.release = resolve; });
  return response;
};
const holdResponse = matches => {
  const hold = {matches, release: null}; holds.push(hold); return hold;
};
// Keep polling explicit so response ordering is deterministic.
const js = await fs.readFile(new URL("../trajectory_remix/static/app.js", import.meta.url), "utf8");
const pollStart = js.lastIndexOf("\nsetInterval(");
assert(pollStart > 0);
await w.eval("(async()=>{" + js.slice(0, pollStart) +
  "\nwindow.editorTest = {refresh, selectSample, compileSample, changeView};})()");
const app = w.editorTest;
const draft = () => JSON.parse($("sample-json").value);
const editTitle = title => {
  const source = draft(); source.metadata.title = title;
  $("sample-json").value = JSON.stringify(source); $("sample-json").oninput();
};
await app.changeView("json");
assert.equal(w.getComputedStyle($("events-view")).display, "none");
assert.notEqual(w.getComputedStyle($("json-view")).display, "none");

// A save response must not replace edits typed while it was in flight.
const putHold = holdResponse((path, options) => path === "/api/samples/0" && options?.method === "PUT");
editTitle("First save");
const firstSave = app.compileSample();
await waitFor(() => putHold.release);
editTitle("Newer draft");
const secondSave = app.compileSample();
putHold.release();
await firstSave;
assert.equal(draft().metadata.title, "Newer draft");
await secondSave;
assert.equal((await request("samples/0")).metadata.title, "Newer draft");
assert.equal($("dirty-indicator").textContent, "");

// Navigation waits for the active compile; its preview cannot overwrite the destination.
const second = await request("samples", "POST", {});
await app.refresh();
const compileHold = holdResponse(path => path === "/api/compile");
editTitle("Before navigation");
const saving = app.compileSample();
await waitFor(() => compileHold.release);
const selecting = app.selectSample(second.index);
assert.equal(draft().metadata.title, "Before navigation");
compileHold.release();
await Promise.all([saving, selecting]);
const secondId = (await request("samples/" + second.index)).id;
assert.equal(draft().id, secondId);
assert.equal($("sample-id").value, secondId);

// The last selection wins even when an earlier GET response arrives last.
const sampleHold = holdResponse(path => path === "/api/samples/0");
const oldSelection = app.selectSample(0);
await waitFor(() => sampleHold.release);
await app.selectSample(second.index);
sampleHold.release();
await oldSelection;
assert.equal(draft().id, secondId);

// Typing into the current source while another sample loads cancels that selection.
const editHold = holdResponse(path => path === "/api/samples/0");
const loading = app.selectSample(0);
await waitFor(() => editHold.release);
editTitle("Keep typing");
editHold.release();
await loading;
assert.equal(draft().id, secondId);
assert.equal(draft().metadata.title, "Keep typing");
await app.compileSample();
assert.equal((await request("samples/" + second.index)).metadata.title, "Keep typing");

// A response to an obsolete invalid search must not hide the current results.
$("sample-search").value = "python |";
const queryHold = holdResponse(path => path.startsWith("/api/state?"));
const searching = app.refresh();
await waitFor(() => queryHold.release);
$("sample-search").value = secondId;
await app.refresh();
queryHold.release();
await searching;
assert.equal($("search-error").hidden, true);
assert.equal($("sample-list").children.length, 1);
assert($("sample-list").textContent.includes(secondId));

// Fetch the corrected page immediately when deletion shrinks the result set.
const paged = Array.from({length: 101}, (_, i) => ({
  "schema-version": "1.0", "tick-policy": "external-first", id: "maintenance-page-" + i,
  replay: {0: [{op: "open", window: "a"}]},
}));
await request("import", "POST", {files: [{name: "pages.json", text: JSON.stringify(paged)}]});
$("sample-search").value = "maintenance-page-";
await app.refresh();
await $("sample-page-next").onclick();
assert.equal($("sample-page-label").textContent, "101–101 of 101");
const last = await request("state?query=maintenance-page-&offset=100&limit=100");
await request("samples/" + last.samples[0].index + "?expected_revision=" + last.revision, "DELETE");
await app.refresh();
assert.equal($("sample-page-label").textContent, "1–100 of 100");
assert.equal($("sample-list").children.length, 100);
assert.equal($("sample-page-prev").disabled, true);
assert.deepEqual(errors, []);
console.log("Editor state regressions passed");
await w.happyDOM.abort();
w.close();
