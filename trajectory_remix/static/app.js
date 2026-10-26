const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="remix-token"]').content;
const AUTO_COMPILE_DELAY_KEY = "trajectory-remix.auto-compile-delay";
const SHOW_HEADERS_KEY = "trajectory-remix.show-window-headers";
const COLLAPSE_STATE_PREFIX = "trajectory-remix.event-collapse.";
const SAMPLE_PAGE_SIZE = 100;
let sampleOffset = 0,
  searchTimer;
let state,
  selected = -1,
  sample = null,
  plan = null,
  tick = 0,
  dirty = false,
  view = "events",
  playing = null;
let frameRequest = false,
  currentFrame = null;
let configDraft,
  configTab = "endpoints",
  eventDraft = [],
  toastTimer,
  compileTimer,
  editRevision = 0,
  autoCompileDelayMs = loadAutoCompileDelay(),
  showWindowHeaders = loadShowWindowHeaders(),
  pollBusy = false,
  compileBusy = false,
  sampleRevision = null;
const colors = [
  "#7be0c3",
  "#bba6fb",
  "#83baff",
  "#e6bc7c",
  "#ee99bc",
  "#83d2e0",
  "#c4d38b",
  "#c3ad91",
];
function node(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function button(text, action, cls = "") {
  const n = node("button", cls, text);
  n.onclick = action;
  return n;
}
function toast(text, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = text;
  $("toast").className = error ? "error" : "";
  $("toast").hidden = false;
  toastTimer = setTimeout(
    () => ($("toast").hidden = true),
    error ? 11000 : 4500,
  );
}
async function api(path, method = "GET", body) {
  const res = await fetch("/api/" + path, {
    method,
    headers: {
      "X-Remix-Token": token,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let e;
    try {
      e = await res.json();
    } catch {
      e = { detail: `HTTP ${res.status}` };
    }
    const error = new Error(e.detail || "Request failed");
    error.status = res.status;
    error.diagnostics = e.diagnostics;
    throw error;
  }
  return res;
}
async function json(path, method = "GET", body) {
  return (await api(path, method, body)).json();
}
function action(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      toast(e.message, true);
      if (e.diagnostics) diagnostics(e.diagnostics);
    }
  };
}
function loadAutoCompileDelay() {
  try {
    const saved = Number(localStorage.getItem(AUTO_COMPILE_DELAY_KEY));
    return Number.isFinite(saved) && saved >= 100 && saved <= 60000
      ? saved
      : 1000;
  } catch {
    return 1000;
  }
}
function loadShowWindowHeaders() {
  try {
    return localStorage.getItem(SHOW_HEADERS_KEY) === "true";
  } catch {
    return false;
  }
}
function scheduleCompile() {
  clearTimeout(compileTimer);
  compileTimer = setTimeout(
    action(async () => {
      if (compileBusy) {
        scheduleCompile();
        return;
      }
      compileTimer = null;
      await compileSample(true);
    }),
    autoCompileDelayMs,
  );
}
function setDirty(value = true) {
  dirty = value;
  if (value) {
    editRevision++;
    scheduleCompile();
  } else {
    clearTimeout(compileTimer);
    compileTimer = null;
  }
  const pending = dirty || compileTimer !== null;
  document.title = document.title.replace(/^\*\s*/, "");
  if (pending) document.title = "* " + document.title;
  $("dirty-indicator").textContent = pending ? "Compile pending" : "";
}
function hasPendingChanges() {
  return dirty || compileTimer !== null;
}
function diagnostics(items = []) {
  $("diagnostics").replaceChildren();
  $("diagnostics").hidden = !items.length;
  $("diagnostics").className =
    "diagnostics" + (items.some((i) => i.severity === "error") ? " error" : "");
  for (const d of items) {
    const r = node("div", "diagnostic-row");
    r.append(node("b", "", d.path + " "), node("span", "", d.message));
    $("diagnostics").append(r);
  }
  if (items.length) {
    const close = button(
      "×",
      () => ($("diagnostics").hidden = true),
      "square diagnostics-close",
    );
    close.setAttribute("aria-label", "Close diagnostics");
    close.title = "Close";
    $("diagnostics").append(close);
  }
}
async function refresh(initial = false) {
  if (pollBusy) return;
  pollBusy = true;
  try {
    const params = new URLSearchParams({
      offset: String(sampleOffset),
      limit: String(SAMPLE_PAGE_SIZE),
      query: $("sample-search").value,
    });
    if (sample?.id) params.set("selected_id", sample.id);
    let next;
    try {
      next = await json("state?" + params);
    } catch (error) {
      if (error.status !== 422) throw error;
      $("search-error").textContent = error.message;
      $("search-error").hidden = false;
      $("sample-search").setAttribute("aria-invalid", "true");
      $("sample-list").replaceChildren();
      $("sample-page-label").textContent = "Invalid query";
      $("sample-page-prev").disabled = true;
      $("sample-page-next").disabled = true;
      return;
    }
    const searchWasInvalid = !$("search-error").hidden;
    $("search-error").hidden = true;
    $("search-error").textContent = "";
    $("sample-search").removeAttribute("aria-invalid");
    if (next.total > 0 && sampleOffset >= next.total) {
      sampleOffset =
        Math.floor((next.total - 1) / SAMPLE_PAGE_SIZE) * SAMPLE_PAGE_SIZE;
    }
    const changed =
      !state ||
      next.revision !== state.revision ||
      next.offset !== state.offset ||
      next.query !== state.query;
    state = next;
    renderStatus();
    if (changed || initial || searchWasInvalid) renderSamples();
    if (initial && state.samples.length)
      await selectSample(state.samples[0].index, true);
    if (
      !initial &&
      sample &&
      next.revision !== sampleRevision &&
      !compileBusy &&
      !hasPendingChanges()
    ) {
      const current =
        state.samples.find((s) => s.id === sample.id) ||
        (Number.isInteger(next.selected_index) && next.selected_index >= 0
          ? { index: next.selected_index }
          : null);
      if (current) await selectSample(current.index, true);
      else {
        selected = -1;
        sample = null;
        plan = null;
        renderReplay();
      }
    }
    if (
      selected >= (state.sample_count ?? state.samples.length) &&
      !hasPendingChanges()
    ) {
      selected = -1;
      sample = null;
      plan = null;
      renderReplay();
    }
    if (!sample && state.samples.length && selected < 0 && !hasPendingChanges())
      await selectSample(state.samples[state.samples.length - 1].index, true);
  } finally {
    pollBusy = false;
  }
}
function renderStatus() {
  $("sample-count").textContent = state.sample_count ?? state.samples.length;
  const s = state.status;
  $("run-status").textContent = s.running
    ? s.paused
      ? "Paused"
      : "Generating"
    : "Idle";
  $("run-dot").style.background = s.running ? "#7be0c3" : "#667a95";
  $("accepted-stat").textContent = s.accepted;
  $("attempt-stat").textContent = s.attempts;
  for (const id of [
    "generate-one",
    "auto-start",
    "new-sample",
    "delete-sample",
    "import-button",
    "save-config",
  ])
    $(id).disabled = s.running;
  $("pause-run").textContent = s.paused ? "Resume" : "Pause";
  $("pause-run").disabled = !s.running;
  $("stop-run").disabled = !s.running;
  renderHistoryActions();
  $("selected-tags").replaceChildren(
    ...(state.config.tags || []).map((t) => node("span", "tag", t)),
  );
  $("activity-log").replaceChildren(
    ...state.logs
      .slice()
      .reverse()
      .map((log) => {
        const row = node("div", "log-line");
        row.append(
          node("time", "", new Date(log.time).toLocaleTimeString()),
          document.createTextNode(log.message),
        );
        return row;
      }),
  );
}
function renderHistoryActions() {
  for (const [id, direction] of [
    ["undo-edit", "undo"],
    ["redo-edit", "redo"],
  ]) {
    const available = state?.history?.[direction] || [];
    $(id).disabled = state?.status.running || compileBusy || !available.length;
    $(id).title = available[0]?.label || `Nothing to ${direction}`;
  }
}
function renderSamples() {
  const total = state.total ?? state.samples.length;
  $("export-sample").disabled = selected < 0 || !sample;
  $("sample-page-label").textContent = total
    ? `${(state.offset || 0) + 1}–${Math.min((state.offset || 0) + SAMPLE_PAGE_SIZE, total)} of ${total}`
    : "0 results";
  $("sample-page-prev").disabled = sampleOffset === 0;
  $("sample-page-next").disabled = sampleOffset + SAMPLE_PAGE_SIZE >= total;
  $("sample-list").replaceChildren(
    ...state.samples.map((s) => {
      const b = button(
        "",
        action(() => selectSample(s.index)),
      );
      b.className = "sample-item" + (s.index === selected ? " active" : "");
      const title = node("strong");
      title.append(
        node("span", "item-num", String(s.index + 1).padStart(3, "0")),
        document.createTextNode(s.id),
      );
      b.append(
        title,
        node(
          "small",
          "",
          `${s.events} events${s.tags.length ? " · " + s.tags.slice(0, 2).join(", ") : ""}`,
        ),
      );
      return b;
    }),
  );
}
async function selectSample(index, force = false) {
  if (
    !force &&
    hasPendingChanges() &&
    !confirm("Discard unapplied sample edits?")
  )
    return;
  clearTimeout(compileTimer);
  compileTimer = null;
  pause();
  const response = await api("samples/" + index);
  sample = await response.json();
  sampleRevision = Number(response.headers.get("X-Remix-Revision"));
  selected = index;
  if (!$("sample-search").value)
    sampleOffset = Math.floor(index / SAMPLE_PAGE_SIZE) * SAMPLE_PAGE_SIZE;
  setDirty(false);
  $("sample-json").value = JSON.stringify(sample, null, 2);
  loadMetadata(sample);
  renderSamples();
  if (validEventShape()) loadEvents();
  else {
    view = "json";
    for (const name of ["events", "json"])
      $(name + "-view").hidden = name !== "json";
    document
      .querySelectorAll(".tab")
      .forEach((b) => b.classList.toggle("active", b.dataset.view === "json"));
  }
  await compileSample(false);
}
function pause() {
  if (playing) clearInterval(playing);
  playing = null;
  $("play").textContent = "Play";
}
async function compileSample(persist = true) {
  if (compileBusy) return;
  const revision = editRevision;
  clearTimeout(compileTimer);
  compileTimer = null;
  compileBusy = true;
  renderHistoryActions();
  pause();
  try {
    if ($("metadata-json").validationMessage)
      throw new Error($("metadata-json").validationMessage);
    if (view === "json") sample = JSON.parse($("sample-json").value);
    else if (view === "events") sample = sampleFromEvents();
    if (!sample) {
      plan = null;
      renderReplay();
      return;
    }
    // Strip legacy UI state from the source, including Source JSON edits.
    stripCollapseMetadata(sample);
    // Persist an invalid draft too: it must remain editable and downloadable.
    if (persist) {
      const result = await json("samples/" + selected, "PUT", {
        sample,
        expected_revision: sampleRevision,
      });
      sampleRevision = result.revision;
      if (revision === editRevision) setDirty(false);
      await refresh();
    }
    $("sample-json").value = JSON.stringify(sample, null, 2);
    try {
      plan = await json("compile", "POST", { sample });
      currentFrame = plan.frame;
      diagnostics(plan.diagnostics);
      setTick(Math.min(tick, plan.max_tick));
    } catch (e) {
      plan = null;
      renderReplay();
      diagnostics(
        e.diagnostics || [
          { severity: "error", path: "sample", message: e.message },
        ],
      );
    }
  } finally {
    compileBusy = false;
    renderHistoryActions();
  }
}
function setTick(value) {
  tick = Math.max(
    0,
    Math.min(plan?.max_tick || 0, Math.trunc(Number(value) || 0)),
  );
  $("tick-number").value = tick;
  $("tick-scrubber").value = tick;
  renderReplay();
}
let expandedWindowId = null;
function shrinkWindow() {
  expandedWindowId = null;
  const dialog = $("expanded-window-dialog");
  if (dialog.open) dialog.close();
  renderReplay();
}
// Escape and other native dialog closures must restore the replay grid.
$("expanded-window-dialog").onclose = () => {
  if (expandedWindowId !== null) shrinkWindow();
};
function renderReplay() {
  if (!sample) loadMetadata(null);
  const grid = $("window-grid");
  const dialog = $("expanded-window-dialog");
  const expandedScroll = dialog.querySelector(".window-content")?.scrollTop ?? 0;
  grid.replaceChildren();
  dialog.replaceChildren();
  $("max-tick").textContent = plan?.max_tick || 0;
  $("tick-scrubber").max = plan?.max_tick || 0;
  $("tick-number").max = plan?.max_tick || 0;
  renderSandbox();
  if (!plan) {
    expandedWindowId = null;
    if (dialog.open) dialog.close();
    const empty = node("div", "empty");
    empty.append(
      node(
        "strong",
        "",
        sample
          ? "Compile the sample to preview its execution."
          : "Build a trajectory. Inspect every tick.",
      ),
      document.createTextNode(
        sample
          ? "Compiler errors appear below. Edit events or source JSON, then apply again."
          : "Import JSON / JSONL samples or create a sample. Load a saved .kidra-dataset to restore its generation configuration.",
      ),
    );
    grid.append(empty);
    $("open-count").textContent = 0;
    drawTimeline();
    return;
  }
  let active = 0;
  plan.windows.forEach((w, i) => {
    const unopened = w.open > tick,
      closed = w.close !== null && w.close <= tick;
    if (!unopened && !closed) active++;
    if (closed && !$("show-closed").checked) return;
    const card = node(
      "article",
      "window-card" + (closed ? " closed" : "") + (unopened ? " future" : ""),
    );
    const head = node("div", "window-head");
    const isExpanded = expandedWindowId === w.id;
    const dot = node("span", "window-color");
    dot.style.background = colors[i % colors.length];
    head.append(
      dot,
      node("strong", "", w.id),
      node(
        "small",
        "",
        `slot ${w.slot} · ${unopened ? "scheduled" : closed ? "closed" : w.type}`,
      ),
    );
    const resize = button(
      isExpanded ? "Shrink" : "Expand",
      () => {
        if (isExpanded) {
          shrinkWindow();
        } else {
          expandedWindowId = w.id;
          renderReplay();
          dialog.showModal();
        }
      },
      "window-size-toggle",
    );
    resize.title = isExpanded ? "Shrink context window" : "Expand context window";
    resize.setAttribute(
      "aria-label",
      `${isExpanded ? "Shrink" : "Expand"} context window ${w.id}`,
    );
    head.append(resize);
    const data = currentFrame?.windows?.[w.id];
    let content = data?.text || "";
    if (!showWindowHeaders)
      content = content.replace(/<\|cxt\|>[\s\S]*?<\|\/cxt\|>/, "");
    card.append(
      head,
      windowContent(content || (unopened ? "Not opened yet." : "No content.")),
    );
    const count = data?.cached_tokens || 0;
    card.append(
      node(
        "div",
        "window-foot",
        `${count} cached tokens · opened ${w.open}${w.close !== null ? " · closed " + w.close : ""}`,
      ),
    );
    if (isExpanded) {
      dialog.append(card);
      card.querySelector(".window-content").scrollTop = expandedScroll;
    } else {
      grid.append(card);
    }
  });
  // A hidden or removed window cannot remain in an expanded dialog.
  if (expandedWindowId !== null && !dialog.querySelector(".window-card")) {
    shrinkWindow();
    return;
  }
  $("open-count").textContent = active;
  drawTimeline();
  if (currentFrame?.tick !== tick) fetchFrame();
}
async function fetchFrame() {
  if (frameRequest || !plan) return;
  frameRequest = true;
  const id = plan.plan_id,
    target = tick;
  try {
    const result = await json(`frame/${id}/${target}`);
    if (plan?.plan_id === id) {
      currentFrame = result;
      if (tick === target) renderReplay();
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    frameRequest = false;
    if (plan && currentFrame?.tick !== tick) setTimeout(fetchFrame, 25);
  }
}
function renderSandbox() {
  $("sandbox-panel").hidden = !plan;
  $("sandbox-title").textContent = `Sandbox · tick ${tick}`;
  $("sandbox-calls").replaceChildren();
  $("sandbox-state").replaceChildren();
  if (!plan) return;
  if (currentFrame?.tick !== tick) {
    $("sandbox-state").textContent = "Loading sandbox…";
    return;
  }
  const sandbox = currentFrame.sandbox;
  if (!sandbox) return;
  for (const call of currentFrame.tool_calls || []) {
    const row = node("details", `sandbox-call ${call.status}`);
    row.append(
      node(
        "summary",
        "",
        `${call.invisible ? "External · " : ""}${call.tool} · ${call.status} · ticks ${call.start}–${call.end}`,
      ),
    );
    row.append(
      node(
        "pre",
        "window-content",
        JSON.stringify(
          { arguments: call.arguments, ...(call.outcome || {}) },
          null,
          2,
        ),
      ),
    );
    $("sandbox-calls").append(row);
  }
  const contents = node("pre", "window-content", JSON.stringify(sandbox, null, 2));
  $("sandbox-state").append(contents);
}
function drawTimeline() {
  const canvas = $("timeline"),
    scroll = $("timeline-scroll"),
    space = $("timeline-space");
  const width = Math.max(300, scroll.clientWidth),
    height = Math.max(
      100,
      ((plan?.windows.length || 1) + (plan?.tool_calls?.length || 0)) * 32 + 30,
    ),
    px = +$("timeline-zoom").value,
    label = 125,
    offset = scroll.scrollLeft;
  space.style.width =
    Math.max(width, label + ((plan?.max_tick || 0) + 2) * px) + "px";
  space.style.height = height + "px";
  const dpr = devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  canvas.style.width = width + "px";
  canvas.style.height = height + "px";
  const c = canvas.getContext("2d");
  c.scale(dpr, dpr);
  c.fillStyle = "#0e1622";
  c.fillRect(0, 0, width, height);
  if (!plan) return;
  const x = (t) => label + t * px - offset;
  let step = px < 3 ? 100 : px < 6 ? 25 : 10;
  while (step * px < 45) step *= 2;
  c.font = "9px ui-monospace,monospace";
  for (
    let t = Math.max(0, Math.floor((offset - label) / px / step) * step);
    t <= plan.max_tick + 1 && x(t) < width;
    t += step
  ) {
    if (x(t) < label) continue;
    c.strokeStyle = "#233045";
    c.beginPath();
    c.moveTo(x(t), 24);
    c.lineTo(x(t), height);
    c.stroke();
    c.fillStyle = "#778da9";
    c.fillText(String(t), x(t) + 3, 16);
  }
  plan.windows.forEach((w, i) => {
    const y = 30 + i * 32;
    const color = colors[i % colors.length];
    const left = Math.max(label, x(w.open)),
      right = Math.min(width, x(w.close ?? plan.max_tick) + px);
    if (right > left) {
      c.fillStyle = color + "25";
      c.fillRect(left, y, right - left, 18);
    }
    for (const e of plan.events.filter(
      (e) => e.window === w.id && e.op !== "open",
    )) {
      const a = Math.max(label, x(e.start)),
        b = Math.min(width, x(e.end) + Math.max(px, 3));
      if (b > a) {
        c.fillStyle = e.op === "append" ? "#bba6fb" : color;
        c.fillRect(a, y + 4, Math.max(2, b - a), 10);
      }
    }
    if (w.close !== null && x(w.close) >= label && x(w.close) <= width) {
      c.fillStyle = "#ffe0d6";
      c.fillRect(x(w.close), y, 2, 18);
    }
  });
  (plan.tool_calls || []).forEach((call, i) => {
    const y = 30 + (plan.windows.length + i) * 32;
    const a = Math.max(label, x(call.start)),
      b = Math.min(width, x(call.end) + Math.max(px, 3));
    if (b > a) {
      c.fillStyle =
        tick < call.start
          ? "#667a95"
          : tick < call.end
            ? "#e6bc7c"
            : call.outcome.ok
              ? "#7be0c3"
              : "#ee99bc";
      c.fillRect(a, y + 3, Math.max(2, b - a), 12);
    }
  });
  for (const [id, a] of Object.entries(plan.anchors)) {
    const xx = x(a.tick);
    if (xx < label || xx > width) continue;
    c.strokeStyle = "#edc57b";
    c.setLineDash([3, 3]);
    c.beginPath();
    c.moveTo(xx, 23);
    c.lineTo(xx, height);
    c.stroke();
    c.setLineDash([]);
    c.fillStyle = "#edc57b";
    c.fillText("N" + id, xx + 3, 25);
  }
  const current = x(tick);
  if (current >= label && current <= width) {
    c.strokeStyle = "#f3f7fc";
    c.beginPath();
    c.moveTo(current, 23);
    c.lineTo(current, height);
    c.stroke();
    c.fillStyle = "#f3f7fc";
    c.beginPath();
    c.moveTo(current - 4, 21);
    c.lineTo(current + 4, 21);
    c.lineTo(current, 27);
    c.fill();
  }
  c.fillStyle = "#121d2b";
  c.fillRect(0, 0, label, height);
  c.fillStyle = "#7c91ae";
  c.fillText("WINDOW / GLOBAL TICK", 10, 16);
  plan.windows.forEach((w, i) => {
    c.fillStyle = colors[i % colors.length];
    c.fillRect(10, 36 + i * 32, 5, 5);
    c.fillStyle = "#c9d6e8";
    c.fillText(
      w.id.length > 15 ? w.id.slice(0, 14) + "…" : w.id,
      24,
      42 + i * 32,
    );
  });
  (plan.tool_calls || []).forEach((call, i) => {
    c.fillStyle = "#e6bc7c";
    c.fillText(
      `↳ ${call.tool}`.slice(0, 18),
      10,
      42 + (plan.windows.length + i) * 32,
    );
  });
}
function focusTick() {
  const scroll = $("timeline-scroll"),
    x = tick * +$("timeline-zoom").value;
  if (x < scroll.scrollLeft || x > scroll.scrollLeft + scroll.clientWidth - 150)
    scroll.scrollLeft = Math.max(0, x - scroll.clientWidth / 2 + 125);
}
function validEventShape() {
  return (
    sample?.replay &&
    typeof sample.replay === "object" &&
    !Array.isArray(sample.replay) &&
    Object.values(sample.replay).every(
      (events) =>
        Array.isArray(events) &&
        events.every(
          (e) =>
            e &&
            typeof e === "object" &&
            !Array.isArray(e) &&
            typeof e.op === "string" &&
            (!("loss" in e) ||
              (e.loss && typeof e.loss === "object" && !Array.isArray(e.loss))),
        ),
    )
  );
}
function loadEvents() {
  if (!sample) return;
  eventDraft = [];
  eventGroups = [];
  collapsedEvents = new WeakSet();
  selectedEvents.clear();
  for (const [key, events] of Object.entries(sample.replay || {})) {
    if (!Array.isArray(events)) continue;
    events.forEach((e) => eventDraft.push({ key, event: structuredClone(e) }));
  }
  const saved = sample.metadata?.event_groups;
  const savedColors = sample.metadata?.window_colors;
  windowColors =
    savedColors &&
    typeof savedColors === "object" &&
    !Array.isArray(savedColors)
      ? { ...savedColors }
      : {};
  // A saved browser preference takes precedence over legacy trajectory metadata.
  const collapseState = loadCollapseState(sample);
  const collapsedIndices = collapseState?.events ?? sample.metadata?.collapsed_events;
  if (Array.isArray(collapsedIndices))
    for (const k of collapsedIndices)
      if (eventDraft[k]) collapsedEvents.add(eventDraft[k]);
  if (Array.isArray(saved))
    for (const g of saved) {
      if (!g || typeof g.id !== "string") continue;
      eventGroups.push({
        id: g.id,
        name: String(g.name ?? ""),
        collapsed: collapseState
          ? collapseState.groups.includes(g.id)
          : !!g.collapsed,
      });
      for (const k of Array.isArray(g.events) ? g.events : [])
        if (eventDraft[k] && !eventDraft[k].group) eventDraft[k].group = g.id;
    }
  // Migrate old preferences without triggering a trajectory edit or API write.
  if (
    !collapseState &&
    (Array.isArray(sample.metadata?.collapsed_events) ||
      (Array.isArray(saved) && saved.some((g) => g?.collapsed)))
  )
    saveCollapseState(eventDraft);
  $("sample-id").value = sample.id || "";
  $("noop-weight").value =
    sample.defaults?.["implicit-noop-loss-weight"] ?? 0.1;
  renderEvents();
}
function readMetadata() {
  const metadata = JSON.parse($("metadata-json").value);
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata))
    throw new Error("Metadata must be a JSON object.");
  return metadata;
}
function showMetadataNames(metadata) {
  $("agent-name").value =
    typeof metadata?.agent_name === "string" ? metadata.agent_name : "";
  $("user-name").value =
    typeof metadata?.user_name === "string" ? metadata.user_name : "";
  $("tick-rate").value = metadata?.synthetic_clock?.tick_rate ?? 10;
}
function loadMetadata(trajectory) {
  $("trajectory-uuid").value = trajectory?.uuid || "";
  $("metadata-fields").disabled = !trajectory;
  $("metadata-json").setCustomValidity("");
  $("metadata-json").value = JSON.stringify(
    trajectory?.metadata ?? {},
    null,
    2,
  );
  showMetadataNames(trajectory?.metadata);
  $("metadata-status").textContent = trajectory
    ? "Defaults to Kidra and User when left blank."
    : "Select a trajectory to edit its metadata.";
}
function updateMetadata() {
  try {
    const metadata = readMetadata();
    $("metadata-json").setCustomValidity("");
    showMetadataNames(metadata);
    if (view === "json") {
      const trajectory = JSON.parse($("sample-json").value);
      trajectory.metadata = metadata;
      $("sample-json").value = JSON.stringify(trajectory, null, 2);
    }
    $("metadata-status").textContent = "Changes save with this trajectory.";
  } catch (e) {
    $("metadata-json").setCustomValidity(e.message);
    $("metadata-status").textContent = e.message;
  }
  setDirty();
}
function updateMetadataName(key, value) {
  try {
    const metadata = readMetadata();
    if (value) metadata[key] = value;
    else delete metadata[key];
    $("metadata-json").value = JSON.stringify(metadata, null, 2);
    updateMetadata();
  } catch (e) {
    $("metadata-status").textContent = e.message;
  }
}
function input(value, onChange, type = "text") {
  const n = node("input");
  n.type = type;
  n.value = value ?? "";
  n.oninput = () => onChange(type === "number" ? Number(n.value) : n.value);
  return n;
}
function select(options, value, onChange) {
  const n = node("select");
  for (const option of options) {
    const v = typeof option === "string" ? option : option[0],
      label = typeof option === "string" ? option : option[1];
    const o = node("option", "", label);
    o.value = v;
    n.append(o);
  }
  n.value = value;
  n.onchange = () => onChange(n.value);
  return n;
}
function field(label, control, cls = "") {
  const n = node("label", cls, label);
  n.append(control);
  return n;
}
function windowContent(text) {
  const content = node("pre", "window-content");
  const parts = text.split(/(\r\n|\r|\n)/);
  for (const part of parts) {
    if (/^(\r\n|\r|\n)$/.test(part)) {
      content.append(node("span", "newline-indicator", "↵"), part);
    } else {
      content.append(part);
    }
  }
  return content;
}
function checkbox(value, onChange) {
  const n = node("input");
  n.type = "checkbox";
  n.checked = !!value;
  n.onchange = () => onChange(n.checked);
  return n;
}
let collapsedEvents = new WeakSet();
// Window id -> color, kept so event borders match even before a compile.
let windowColors = {};
// Group membership and names persist; group collapse is browser-only.
let eventGroups = [];
const selectedEvents = new Set();
function collapseStorageKey(trajectory = sample) {
  const id = trajectory?.uuid || trajectory?.id;
  return id ? COLLAPSE_STATE_PREFIX + encodeURIComponent(id) : null;
}
function loadCollapseState(trajectory) {
  const key = collapseStorageKey(trajectory);
  if (!key) return null;
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (!saved || !Array.isArray(saved.events) || !Array.isArray(saved.groups))
      return null;
    return {
      events: saved.events.filter((i) => Number.isInteger(i) && i >= 0),
      groups: saved.groups.filter((id) => typeof id === "string"),
    };
  } catch {
    return null; // Storage can be blocked or contain invalid data.
  }
}
function orderedEventItems() {
  const byKey = {};
  for (const item of eventDraft) (byKey[item.key] ??= []).push(item);
  return Object.keys(byKey).flatMap((key) => byKey[key]);
}
function saveCollapseState(ordered = orderedEventItems()) {
  const key = collapseStorageKey();
  if (!key) return;
  try {
    localStorage.setItem(
      key,
      JSON.stringify({
        events: ordered.flatMap((item, i) =>
          collapsedEvents.has(item) ? [i] : [],
        ),
        groups: eventGroups.filter((group) => group.collapsed).map((group) => group.id),
      }),
    );
  } catch {
    // Editing continues normally when browser storage is unavailable.
  }
}
function stripCollapseMetadata(trajectory) {
  const metadata = trajectory?.metadata;
  if (!metadata || typeof metadata !== "object") return;
  delete metadata.collapsed_events;
  if (Array.isArray(metadata.event_groups))
    for (const group of metadata.event_groups)
      if (group && typeof group === "object") delete group.collapsed;
}
let dragItems = [];
let dragGroup = null;
function insertMoved(moving, groupId, after) {
  const idx = eventDraft.flatMap((it, k) => (it.group === groupId ? [k] : []));
  const at = !idx.length
    ? eventDraft.length
    : after
      ? idx[idx.length - 1] + 1
      : idx[0];
  eventDraft.splice(at, 0, ...moving);
}
function normalizeGroups() {
  const out = [],
    done = new Set();
  for (const item of eventDraft) {
    if (!item.group) out.push(item);
    else if (!done.has(item.group)) {
      done.add(item.group);
      out.push(...eventDraft.filter((it) => it.group === item.group));
    }
  }
  eventDraft = out;
}
function takeDragged() {
  const moving = eventDraft.filter((it) => dragItems.includes(it));
  eventDraft = eventDraft.filter((it) => !dragItems.includes(it));
  return moving;
}
function dropOnEvent(target, after) {
  if (dragItems.includes(target)) return;
  const moving = takeDragged();
  if (dragGroup) {
    if (target.group) insertMoved(moving, target.group, after);
    else
      eventDraft.splice(
        eventDraft.indexOf(target) + (after ? 1 : 0),
        0,
        ...moving,
      );
    return finishDrop();
  }
  moving.forEach((it) => (it.group = target.group));
  eventDraft.splice(eventDraft.indexOf(target) + (after ? 1 : 0), 0, ...moving);
  finishDrop();
}
function dropGroupOnGroup(group, after) {
  if (group === dragGroup) return;
  const moving = takeDragged();
  insertMoved(moving, group.id, after);
  finishDrop();
}
function dropOnGroup(group) {
  const moving = takeDragged();
  moving.forEach((it) => (it.group = group.id));
  let at = eventDraft.length;
  for (let k = eventDraft.length - 1; k >= 0; k--)
    if (eventDraft[k].group === group.id) {
      at = k + 1;
      break;
    }
  eventDraft.splice(at, 0, ...moving);
  group.collapsed = false;
  finishDrop();
}
function dropOnRoot() {
  const moving = takeDragged();
  if (!dragGroup) moving.forEach((it) => delete it.group);
  eventDraft.push(...moving);
  finishDrop();
}
function finishDrop() {
  dragItems = [];
  dragGroup = null;
  selectedEvents.clear();
  setDirty();
  renderEvents();
}
function clearDropMarks() {
  document
    .querySelectorAll(".drop-before, .drop-after, .drop-into")
    .forEach((n) =>
      n.classList.remove("drop-before", "drop-after", "drop-into"),
    );
}
function allowDrop(el, onDrop, mark) {
  el.addEventListener("dragover", (ev) => {
    if (!dragItems.length) return;
    ev.preventDefault();
    ev.stopPropagation();
    clearDropMarks();
    const cls = mark(ev);
    if (cls) el.classList.add(cls);
  });
  el.addEventListener("drop", (ev) => {
    if (!dragItems.length) return;
    ev.preventDefault();
    ev.stopPropagation();
    clearDropMarks();
    onDrop(ev);
  });
}
function renderGroup(group, cards) {
  const members = eventDraft.filter((it) => it.group === group.id);
  const box = node(
    "div",
    "event-group" + (group.collapsed ? " collapsed" : ""),
  );
  const head = node("div", "event-group-header");
  const toggle = button(
    group.collapsed ? "▸" : "▾",
    () => {
      group.collapsed = !group.collapsed;
      saveCollapseState();
      renderEvents();
    },
    "event-toggle",
  );
  toggle.title = group.collapsed ? "Expand group" : "Collapse group";
  const name = input(group.name, (v) => {
    group.name = v;
    setDirty();
  });
  name.placeholder = "Group name";
  const grip = node("span", "event-grip", "⠇");
  grip.draggable = true;
  grip.title = "Drag to move group";
  grip.ondragstart = (ev) => {
    dragGroup = group;
    dragItems = [...members];
    ev.dataTransfer.effectAllowed = "move";
    ev.dataTransfer.setData("text/plain", "");
    ev.dataTransfer.setDragImage(box, 0, 0);
  };
  grip.ondragend = () => {
    dragGroup = null;
    dragItems = [];
    clearDropMarks();
  };
  head.append(
    toggle,
    grip,
    name,
    node(
      "small",
      "",
      `${members.length} event${members.length === 1 ? "" : "s"}`,
    ),
    button(
      "Ungroup",
      () => {
        members.forEach((it) => delete it.group);
        eventGroups = eventGroups.filter((g) => g !== group);
        setDirty();
        renderEvents();
      },
      "event-delete",
    ),
  );
  box.append(head);
  if (!group.collapsed) {
    const body = node("div", "event-group-body");
    if (!members.length)
      body.append(node("small", "event-group-empty", "Drag events here"));
    members.forEach((it) => body.append(cards.get(it)));
    box.append(body);
  }
  allowDrop(
    box,
    (ev) =>
      dragGroup
        ? dropGroupOnGroup(group, isLowerHalf(ev, box))
        : dropOnGroup(group),
    (ev) =>
      dragGroup
        ? isLowerHalf(ev, box)
          ? "drop-after"
          : "drop-before"
        : "drop-into",
  );
  return box;
}
function renderEvents() {
  normalizeGroups();
  plan?.windows.forEach(
    (w, i) => (windowColors[w.id] = colors[i % colors.length]),
  );
  const cards = new Map();
  eventDraft.forEach((item, i) => cards.set(item, buildEventCard(item, i)));
  const out = [],
    shown = new Set();
  for (const item of eventDraft) {
    const group = eventGroups.find((g) => g.id === item.group);
    if (!group) out.push(cards.get(item));
    else if (!shown.has(group)) {
      shown.add(group);
      out.push(renderGroup(group, cards));
    }
  }
  for (const g of eventGroups)
    if (!shown.has(g)) out.push(renderGroup(g, cards));
  $("event-list").replaceChildren(...out);
}
function buildEventCard(item, i) {
  {
    {
      const e = item.event,
        card = node("div", "event-card"),
        head = node("div", "event-header");
      const update = () => setDirty();
      const winColor = windowColors[e.window];
      if (winColor) card.style.borderLeftColor = winColor;
      const collapsed = collapsedEvents.has(item);
      const toggle = button(
        collapsed ? "▸" : "▾",
        () => {
          if (collapsed) collapsedEvents.delete(item);
          else collapsedEvents.add(item);
          saveCollapseState();
          renderEvents();
        },
        "event-toggle",
      );
      toggle.title = collapsed ? "Expand event" : "Collapse event";
      const grip = node("span", "event-grip", "⠇");
      grip.draggable = true;
      grip.title = "Drag to move; click to select";
      card.classList.toggle("selected", selectedEvents.has(item));
      grip.onclick = () => {
        if (selectedEvents.has(item)) selectedEvents.delete(item);
        else selectedEvents.add(item);
        card.classList.toggle("selected", selectedEvents.has(item));
      };
      grip.ondragstart = (ev) => {
        dragItems = selectedEvents.has(item) ? [...selectedEvents] : [item];
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", "");
        ev.dataTransfer.setDragImage(card, 0, 0);
      };
      grip.ondragend = () => {
        dragItems = [];
        clearDropMarks();
      };
      allowDrop(
        card,
        (ev) => dropOnEvent(item, isLowerHalf(ev, card)),
        (ev) => (isLowerHalf(ev, card) ? "drop-after" : "drop-before"),
      );
      if (collapsed) {
        card.classList.add("collapsed");
        head.append(
          toggle,
          grip,
          node(
            "small",
            "event-summary",
            `${item.key} · ${e.op}${e.window ? " · " + e.window : ""}`,
          ),
        );
        card.append(head);
        return card;
      }
      head.append(toggle, grip);
      const at = input(item.key, (v) => {
        item.key = v;
        update();
      });
      at.title = "Absolute tick or anchor expression";
      head.append(
        at,
        select(
          ["open", "append", "generate", "tool-call", "invisible-tool-call", "show_tools"],
          e.op,
          (v) => {
            e.op = v;
            if (
              v === "open" ||
              v === "show_tools" ||
              v === "tool-call" ||
              v === "invisible-tool-call"
            ) {
              delete e.content;
              delete e.segments;
              delete e.loss;
              delete e.delivery;
              delete e["tokens-per-tick"];
              delete e.source;
              if (v === "tool-call" || v === "invisible-tool-call") {
                e.tool ??= "file_list";
                e.arguments ??= {};
                e["duration-ticks"] ??= 0;
              }
            } else {
              if (!e.segments) e.content ??= "";
              e.loss ??= { mask: "auto" };
              if (v === "append") e.source ??= "tool";
            }
            if (v === "invisible-tool-call") {
              for (const key of [
                "window",
                "window-type",
                "request-id",
                "caused-by",
                "outcome",
                "result-anchor",
              ])
                delete e[key];

            } else e.window ||= "new-window";
            if (v === "show_tools") e.tools ??= [];
            else {
              delete e.tools;
              delete e["randomly-add-additional-tools"];
            }
            if (v !== "tool-call" && v !== "invisible-tool-call") {
              for (const key of [
                "tool",
                "arguments",
                "duration-ticks",
                "outcome",
                "result-anchor",
              ])
                delete e[key];
            }
            update();
            renderEvents();
          },
        ),
      );
      const win = input(e.window || "", (v) => {
        e.window = v;
        update();
      });
      win.className = "event-window";
      win.hidden = e.op === "invisible-tool-call";
      win.placeholder = e.op === "tool-call" ? "Tool window ID" : "Window ID";
      const removeButton = button(
        "X",
        () => {
          selectedEvents.delete(item);
          eventDraft.splice(i, 1);
          update();
          renderEvents();
        },
        "danger-text event-delete",
      );
      removeButton.title = "Remove event";
      removeButton.setAttribute("aria-label", "Remove event");
      const addBelowButton = button(
        "+",
        () => {
          eventDraft.splice(i + 1, 0, {
            key: item.key,
            group: item.group,
            event: {
              op: "generate",
              window: "new-window",
              content: "",
              loss: { mask: "auto", weight: 1 },
            },
          });
          update();
          renderEvents();
        },
        "event-add-below",
      );
      addBelowButton.title = "Add event below";
      addBelowButton.setAttribute("aria-label", "Add event below");
      head.append(win, removeButton, addBelowButton);
      card.append(head);
      if (e.op === "open") {
        const options = node("div", "event-options");
        options.append(
          field(
            "Window type",
            input(e["window-type"] || "", (v) => {
              e["window-type"] = v;
              update();
            }),
          ),
        );
        card.append(options);
      } else if (e.op === "show_tools") {
        const options = node("div", "event-options show-tools-options");
        for (const schema of (state.tools || []).filter((s) => !s.invisible)) {
          const name = schema.function.name;
          options.append(field(name, checkbox((e.tools || []).includes(name), (checked) => {
            const selected = new Set(e.tools || []);
            if (checked) selected.add(name);
            else selected.delete(name);
            e.tools = [...selected];
            update();
          })));
        }
        options.append(field("Randomly add additional tools", checkbox(e["randomly-add-additional-tools"] || false, (checked) => {
          e["randomly-add-additional-tools"] = checked;
          update();
        })));
        card.append(options);
      } else if (e.op === "tool-call" || e.op === "invisible-tool-call") {
        const options = node("div", "event-options");
        const invisible = e.op === "invisible-tool-call";
        const schemas = (state.tools || []).filter((s) => invisible || !s.invisible).map((s) => s.function);
        options.append(
          field(
            "Tool",
            select(
              schemas.map((s) => s.name),
              e.tool,
              (value) => {
                e.tool = value;
                const parameters = schemas.find(
                  (s) => s.name === value,
                ).parameters;
                e.arguments = Object.fromEntries(
                  (parameters.required || []).map((key) => [
                    key,
                    parameters.properties[key].type.includes("null")
                      ? null
                      : (parameters.properties[key].enum?.[0] ??
                        (parameters.properties[key].type === "number"
                          ? 0
                          : "")),
                  ]),
                );
                delete e.outcome;
                update();
                renderEvents();
              },
            ),
          ),
        );
        const duration = input(
          e["duration-ticks"] ?? 0,
          (v) => {
            e["duration-ticks"] = v;
            update();
          },
          "number",
        );
        duration.min = "0";
        duration.step = "1";
        options.append(field("Duration (ticks)", duration));
        if (!invisible)
          options.append(
            field(
              "Result delivery",
              select(
                ["instant", "stream"],
                e.delivery || "instant",
                (value) => {
                  e.delivery = value;
                  update();
                  renderEvents();
                },
              ),
            ),
            field(
              "Custom request text",
              checkbox("content" in e || "segments" in e, (value) => {
                if (value) e.content = "";
                else {
                  delete e.content;
                  delete e.segments;
                }
                update();
                renderEvents();
              }),
            ),
            field(
              "Auto-generate post-result anchor",
              checkbox("result-anchor" in e, (value) => {
                if (value) {
                  const ids = [
                    ...JSON.stringify(eventDraft).matchAll(/\{N_TICK:(\d+)\}/g),
                  ].map((match) => Number(match[1]));
                  ids.push(
                    ...eventDraft
                      .map(({ event }) => event["result-anchor"])
                      .filter(Number.isInteger),
                  );
                  e["result-anchor"] = Math.max(0, ...ids) + 1;
                } else delete e["result-anchor"];
                update();
                renderEvents();
              }),
            ),
          );
        if (e.delivery === "stream") {
          const rate = input(
            e["tokens-per-tick"] ?? 1,
            (value) => {
              e["tokens-per-tick"] = value;
              update();
            },
            "number",
          );
          rate.min = "1";
          rate.step = "1";
          options.append(field("Result tokens per tick", rate));
        }
        if (invisible) {
          options.append(
            field(
              "External source",
              input(e.source || "external", (value) => {
                e.source = value;
                update();
              }),
            ),
          );
          card.append(
            node(
              "p",
              "segmented-note",
              "External sandbox activity. No model tokens or windows are created.",
            ),
          );
        }
        card.append(options);
        if ("result-anchor" in e) {
          card.append(
            node(
              "small",
              "hint",
              `Result complete: {N_TICK:${e["result-anchor"]}}`,
            ),
          );
        }
        if (e.segments) {
          card.append(
            node(
              "div",
              "segmented-note",
              "Edit request segments and loss masks in Source JSON.",
            ),
          );
        } else if ("content" in e) {
          const area = node("textarea");
          area.setAttribute("aria-label", "Tool request text");
          area.value = e.content;
          area.spellcheck = false;
          area.oninput = () => {
            e.content = area.value;
            update();
          };
          card.append(
            field("Request text (anchors and NOOPs supported)", area),
          );
        }
        for (const key of e.tool === "subagent"
          ? ["arguments", "outcome"]
          : ["arguments"]) {
          const area = node("textarea");
          area.setAttribute("aria-label", `${key} JSON`);
          area.value =
            typeof e[key] === "string"
              ? e[key]
              : JSON.stringify(e[key] ?? {}, null, 2);
          area.spellcheck = false;
          area.oninput = () => {
            try {
              e[key] = JSON.parse(area.value);
            } catch {
              e[key] = area.value;
            }
            update();
          };
          card.append(field(`${key} (JSON)`, area));
        }
        card.append(
          node(
            "small",
            "hint",
            "Tool windows are open from tick 0. Requests always stream one token per tick; execution begins on the following tick. Duration counts execution ticks. Result delivery is independent. A post-result anchor marks when the full result has arrived.",
          ),
        );
      } else {
        if (e.segments) {
          card.append(
            node(
              "div",
              "segmented-note",
              "Segmented loss masks: edit this event in Source JSON to retain exact segment boundaries.",
            ),
          );
        } else {
          const area = node("textarea");
          area.value = e.content || "";
          area.spellcheck = false;
          area.oninput = () => {
            e.content = area.value;
            update();
            markers.textContent = (
              area.value.match(/\{(?:N_TICKS?|WINDOW)[^}]*\}/g) || []
            ).join(" · ");
          };
          card.append(area);
          const markers = node(
            "div",
            "event-marker",
            (e.content?.match(/\{(?:N_TICKS?|WINDOW)[^}]*\}/g) || []).join(
              " · ",
            ),
          );
          card.append(markers);
        }
        const options = node("div", "event-options");
        e.loss ??= { mask: "auto" };
        options.append(
          field(
            "Loss",
            select(
              ["auto", "include", "exclude"],
              e.loss.mask || "auto",
              (v) => {
                e.loss.mask = v;
                update();
              },
            ),
          ),
          field(
            "Weight",
            input(
              e.loss.weight ?? 1,
              (v) => {
                e.loss.weight = v;
                update();
              },
              "number",
            ),
          ),
        );
        if (e.op === "append") {
          const src = input(e.source || "tool", (v) => {
            e.source = v;
            update();
          });
          src.className = "source-input";
          options.append(
            field("Source", src),
            field(
              "Delivery",
              select(["instant", "stream"], e.delivery || "instant", (v) => {
                e.delivery = v;
                update();
              }),
            ),
            field(
              "Tokens/tick",
              input(
                e["tokens-per-tick"] || 1,
                (v) => {
                  e["tokens-per-tick"] = v;
                  update();
                },
                "number",
              ),
            ),
          );
        } else
          options.append(
            button("+ EOS", () => {
              e.content = (e.content || "") + state.tokenizer.special.eos;
              delete e.segments;
              update();
              renderEvents();
            }),
          );
        card.append(options);
      }
      return card;
    }
  }
}
function isLowerHalf(ev, el) {
  const r = el.getBoundingClientRect();
  return ev.clientY > r.top + r.height / 2;
}
function sampleFromEvents() {
  const replay = {},
    byKey = {};
  for (const item of eventDraft) {
    (replay[item.key] ??= []).push(item.event);
    (byKey[item.key] ??= []).push(item);
  }
  // Group members are saved as indices in replay iteration order, matching loadEvents.
  const ordered = Object.keys(replay).flatMap((k) => byKey[k]);
  saveCollapseState(ordered);
  let metadata = readMetadata();
  {
    delete metadata.collapsed_events;
    delete metadata.window_colors;
    const used = Object.fromEntries(
      ordered
        .filter((it) => windowColors[it.event.window])
        .map((it) => [it.event.window, windowColors[it.event.window]]),
    );
    if (Object.keys(used).length) metadata.window_colors = used;
    delete metadata.event_groups;
    if (eventGroups.length)
      metadata.event_groups = eventGroups.map((g) => ({
        id: g.id,
        name: g.name,
        events: ordered.flatMap((it, k) => (it.group === g.id ? [k] : [])),
      }));
  }
  return {
    ...sample,
    metadata,
    id: $("sample-id").value,
    defaults: {
      ...sample.defaults,
      "implicit-noop-loss-weight": Number($("noop-weight").value),
    },
    replay,
  };
}
async function changeView(next) {
  if (next === view) return;
  if (view === "json" && sample) {
    try {
      sample = JSON.parse($("sample-json").value);
    } catch (e) {
      throw new Error(
        "Fix the source JSON before switching views: " + e.message,
      );
    }
  } else if (view === "events" && sample) sample = sampleFromEvents();
  view = next;
  document
    .querySelectorAll(".tab")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  for (const name of ["events", "json"])
    $(name + "-view").hidden = name !== view;
  if (view === "events") {
    if (!validEventShape()) {
      await changeView("json");
      throw new Error("Malformed event structure; repair it in Source JSON.");
    }
    loadEvents();
    loadMetadata(sample);
  }
  if (view === "json") $("sample-json").value = JSON.stringify(sample, null, 2);
  drawTimeline();
}
async function download(path, filename, method = "GET", body) {
  const res = await api(path, method, body),
    blob = await res.blob(),
    url = URL.createObjectURL(blob),
    a = node("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
function openSettings() {
  configDraft = structuredClone(state.config);
  $("settings-dialog").showModal();
  renderConfig();
}
function renderConfig() {
  document
    .querySelectorAll("[data-config]")
    .forEach((b) =>
      b.classList.toggle("active", b.dataset.config === configTab),
    );
  const root = $("config-content");
  root.replaceChildren();
  const eps = configDraft.endpoints.map((e) => e.id);
  if (configTab === "endpoints") {
    root.append(
      node(
        "div",
        "hint",
        "Each task selects its own endpoint. Use a low-cost generator and a separate validator. Base URLs should include /v1.",
      ),
    );
    configDraft.endpoints.forEach((ep, i) => {
      const card = node("div", "config-card"),
        top = node("div", "card-top");
      top.append(
        node("h3", "", ep.id),
        button(
          "Remove",
          () => {
            configDraft.endpoints.splice(i, 1);
            renderConfig();
          },
          "danger-text",
        ),
      );
      const form = node("div", "form-grid");
      for (const [name, label, type] of [
        ["id", "Endpoint ID", "text"],
        ["model", "Model", "text"],
        ["base_url", "Base URL", "url"],
        ["api_key_env", "API key environment variable", "text"],
        ["api_key", "API key (session only)", "password"],
        ["temperature", "Temperature", "number"],
        ["max_tokens", "Max completion tokens", "number"],
        ["timeout", "Timeout (seconds)", "number"],
      ])
        form.append(
          field(
            label,
            input(ep[name], (v) => (ep[name] = v), type),
            name === "base_url" ? "wide" : "",
          ),
        );
      form.append(
        field(
          "Clear stored session key",
          checkbox(ep.clear_key, (v) => (ep.clear_key = v)),
          "check",
        ),
      );
      card.append(top, form);
      root.append(card);
    });
    root.append(
      button("+ Add endpoint", () => {
        configDraft.endpoints.push({
          id: "endpoint-" + (Date.now() % 100000),
          base_url: "http://127.0.0.1:8080/v1",
          model: "local-model",
          temperature: 0.7,
          max_tokens: 1024,
          timeout: 120,
        });
        renderConfig();
      }),
    );
  }
  if (configTab === "tasks") {
    root.append(
      node(
        "div",
        "hint",
        'Tasks run in the listed order. Each generation task fills one window stream. Visible windows can be ordered by entering comma-separated IDs; * includes all. Use explicit start expressions for concurrent streams. Validators return {"valid": boolean, "feedback": ["issue"]}.',
      ),
    );
    configDraft.tasks.forEach((task, i) => {
      const card = node("div", "config-card"),
        top = node("div", "card-top");
      top.append(
        field(
          task.id,
          checkbox(task.enabled !== false, (v) => (task.enabled = v)),
          "check",
        ),
      );
      const buttons = node("div", "two-buttons");
      buttons.append(
        button("↑", () => {
          if (i) {
            [configDraft.tasks[i - 1], configDraft.tasks[i]] = [
              task,
              configDraft.tasks[i - 1],
            ];
            renderConfig();
          }
        }),
        button("↓", () => {
          if (i < configDraft.tasks.length - 1) {
            [configDraft.tasks[i + 1], configDraft.tasks[i]] = [
              task,
              configDraft.tasks[i + 1],
            ];
            renderConfig();
          }
        }),
        button(
          "Remove",
          () => {
            configDraft.tasks.splice(i, 1);
            renderConfig();
          },
          "danger-text",
        ),
      );
      top.append(buttons);
      const form = node("div", "form-grid");
      form.append(
        field(
          "Task ID",
          input(task.id, (v) => (task.id = v)),
        ),
        field(
          "Task kind",
          select(["generate", "validate"], task.kind, (v) => {
            task.kind = v;
            renderConfig();
          }),
        ),
        field(
          "Endpoint",
          select(eps, task.endpoint, (v) => (task.endpoint = v)),
        ),
        field(
          "Visible windows (ordered IDs)",
          input(
            (task.visible || ["*"]).join(", "),
            (v) =>
              (task.visible = v
                .split(",")
                .map((x) => x.trim())
                .filter(Boolean)),
          ),
          "wide",
        ),
        field(
          "Organization",
          select(
            [
              ["messages", "Separate labeled messages"],
              ["labeled", "Single labeled transcript"],
              ["json", "Structured JSON"],
            ],
            task.layout || "messages",
            (v) => (task.layout = v),
          ),
        ),
      );
      const prompt = node("textarea");
      prompt.value = task.prompt;
      prompt.oninput = () => (task.prompt = prompt.value);
      form.append(field("Written task prompt", prompt, "wide"));
      if (task.kind === "generate") {
        form.append(
          field(
            "Target window",
            input(task.window, (v) => (task.window = v)),
          ),
          field(
            "Ownership",
            select(
              [
                ["generate", "Kidra generated target"],
                ["append", "External observation"],
              ],
              task.op || "generate",
              (v) => (task.op = v),
            ),
          ),
          field(
            "Source / type",
            input(task.source || "assistant", (v) => (task.source = v)),
          ),
          field(
            "Start tick / expression",
            input(task.start || "auto", (v) => (task.start = v)),
          ),
          field(
            "Context cutoff tick (optional)",
            input(task.visible_tick ?? "", (v) => {
              if (v === "") delete task.visible_tick;
              else task.visible_tick = Number(v);
            }),
          ),
          field(
            "Close after stream (EOS)",
            checkbox(task.close, (v) => (task.close = v)),
            "check",
          ),
          field(
            "Allow sandbox tools",
            checkbox(task.tools, (v) => (task.tools = v)),
            "check",
          ),
        );
        task.loss ??= { mask: "auto", weight: 1 };
        form.append(
          field(
            "Loss mask",
            select(
              ["auto", "include", "exclude"],
              task.loss.mask,
              (v) => (task.loss.mask = v),
            ),
          ),
          field(
            "Loss weight",
            input(
              task.loss.weight ?? 1,
              (v) => (task.loss.weight = v),
              "number",
            ),
          ),
        );
      }
      card.append(top, form);
      if (task.kind === "generate")
        card.append(
          button(
            "Run this task in current sample",
            action(async () => {
              await saveConfig();
              if (selected < 0)
                throw new Error("Select or create a sample first");
              await json("run", "POST", {
                automatic: false,
                task_ids: [task.id],
                seed_index: selected,
                validate: false,
              });
              $("settings-dialog").close();
              await refresh();
            }),
            "text-button",
          ),
        );
      root.append(card);
    });
    root.append(
      button("+ Add task", () => {
        const id = "stream-" + (Date.now() % 100000);
        configDraft.tasks.push({
          id,
          kind: "generate",
          enabled: true,
          endpoint: eps[0],
          window: id,
          op: "generate",
          source: "assistant",
          prompt: "Write the content for this stream.",
          visible: ["*"],
          layout: "messages",
          start: "auto",
          close: true,
          tools: false,
        });
        renderConfig();
      }),
    );
  }
  if (configTab === "conditions") {
    root.append(
      node(
        "div",
        "hint",
        "These conditions are included in every generation task. Add project-specific tags below.",
      ),
    );
    const picker = node("div", "tag-picker");
    for (const tag of [
      ...new Set([...state.tags, ...(configDraft.tags || [])]),
    ]) {
      const b = button(
        tag,
        () => {
          configDraft.tags ??= [];
          configDraft.tags = configDraft.tags.includes(tag)
            ? configDraft.tags.filter((t) => t !== tag)
            : [...configDraft.tags, tag];
          renderConfig();
        },
        configDraft.tags?.includes(tag) ? "selected" : "",
      );
      picker.append(b);
    }
    root.append(picker);
    const custom = input("", () => {});
    custom.placeholder = "Custom condition tag";
    const wrap = node("div", "editor-toolbar");
    wrap.style.marginTop = "10px";
    wrap.append(
      custom,
      button("Add condition", () => {
        if (custom.value.trim()) {
          configDraft.tags = [
            ...new Set([...(configDraft.tags || []), custom.value.trim()]),
          ];
          renderConfig();
        }
      }),
    );
    root.append(wrap);
  }
  if (configTab === "automatic") {
    const a = configDraft.automatic,
      form = node("div", "form-grid");
    root.append(
      node(
        "div",
        "hint",
        "Limits count newly accepted samples. Max samples / attempts = 0 means unlimited. Regeneration reruns all selected tasks with validator or compiler feedback. A sandbox is destroyed after every candidate.",
      ),
    );
    for (const [key, label] of [
      ["max_samples", "Maximum new samples"],
      ["max_attempts", "Maximum candidate attempts"],
      ["autosave_every", "Autosave every N accepted"],
      ["regenerations", "Regenerations per attempt"],
      ["max_consecutive_failures", "Consecutive failure limit"],
      ["delay_seconds", "Delay between attempts (s)"],
    ])
      form.append(
        field(
          label,
          input(a[key], (v) => (a[key] = v), "number"),
        ),
      );
    form.append(
      field(
        "Failure behavior",
        select(
          ["stop", "pause", "skip"],
          a.failure_action,
          (v) => (a.failure_action = v),
        ),
      ),
      field(
        "Validate with LLM",
        checkbox(a.validate, (v) => (a.validate = v)),
        "check",
      ),
      field(
        "Tool calls per sample",
        input(
          configDraft.max_tool_calls,
          (v) => (configDraft.max_tool_calls = v),
          "number",
        ),
      ),
      field(
        "Tool rounds per task",
        input(
          configDraft.max_tool_rounds,
          (v) => (configDraft.max_tool_rounds = v),
          "number",
        ),
      ),
    );
    configDraft.clock ??= {
      seed: "2030-01-01T09:00:00+00:00",
      tick_rate: 10,
    };
    form.append(
      field(
        "Synthetic clock seed (ISO timestamp)",
        input(configDraft.clock.seed, (v) => (configDraft.clock.seed = v)),
        "wide",
      ),
      field(
        "Default tick rate (tokens/s)",
        input(
          configDraft.clock.tick_rate ?? 10,
          (v) => (configDraft.clock.tick_rate = v),
          "number",
        ),
      ),
    );
    root.append(form);
  }
}
async function saveConfig() {
  await json("config", "PUT", { config: configDraft });
  await refresh();
  toast("Configuration saved.");
}
$("sample-search").oninput = () => {
  clearTimeout(searchTimer);
  sampleOffset = 0;
  searchTimer = setTimeout(
    action(() => refresh()),
    200,
  );
};
$("sample-page-prev").onclick = action(async () => {
  sampleOffset = Math.max(0, sampleOffset - SAMPLE_PAGE_SIZE);
  await refresh();
});
$("sample-page-next").onclick = action(async () => {
  sampleOffset += SAMPLE_PAGE_SIZE;
  await refresh();
});
$("import-button").onclick = () => $("file-input").click();
$("file-input").onchange = action(async (e) => {
  if (hasPendingChanges()) await compileSample(true);
  const files = await Promise.all(
    [...e.target.files].map(async (f) => ({
      name: f.name,
      text: await f.text(),
    })),
  );
  await json("import", "POST", { files, load_config: true });
  e.target.value = "";
  await refresh();
  if (state.samples.length)
    await selectSample(
      Math.max(0, (state.sample_count ?? state.samples.length) - 1),
      true,
    );
  toast("Samples imported.");
});
$("new-sample").onclick = action(async () => {
  if (hasPendingChanges()) await compileSample(true);
  const r = await json("samples", "POST", {});
  await refresh();
  await selectSample(r.index, true);
});
$("sample-json").oninput = () => {
  try {
    loadMetadata(JSON.parse($("sample-json").value));
  } catch {
    $("metadata-fields").disabled = true;
    $("metadata-status").textContent = "Fix Source JSON to edit metadata here.";
  }
  setDirty();
};
$("metadata-json").oninput = updateMetadata;
$("agent-name").oninput = () =>
  updateMetadataName("agent_name", $("agent-name").value);
$("user-name").oninput = () =>
  updateMetadataName("user_name", $("user-name").value);
$("tick-rate").oninput = () => {
  try {
    const metadata = readMetadata();
    metadata.synthetic_clock = {...(metadata.synthetic_clock || {}), tick_rate: Number($("tick-rate").value)};
    $("metadata-json").value = JSON.stringify(metadata, null, 2);
    updateMetadata();
  } catch (e) { $("metadata-status").textContent = e.message; }
};
$("sample-id").oninput = () => setDirty();
$("noop-weight").oninput = () => setDirty();
allowDrop(
  $("event-list"),
  () => dropOnRoot(),
  () => "",
);
$("add-group").onclick = () => {
  eventGroups.push({
    id: `g${Date.now().toString(36)}${eventGroups.length}`,
    name: "Group",
    collapsed: false,
  });
  setDirty();
  renderEvents();
};
$("add-event").onclick = () => {
  eventDraft.push({
    key: String(plan ? plan.max_tick + 1 : 0),
    event: {
      op: "generate",
      window: "new-window",
      content: "",
      loss: { mask: "auto", weight: 1 },
    },
  });
  setDirty();
  renderEvents();
};
$("add-show-tools").onclick = () => {
  const window = eventDraft.find(({event}) => event.op === "open")?.event.window || "system";
  eventDraft.push({key: "1", event: {op: "show_tools", window, tools: []}});
  setDirty();
  renderEvents();
};
$("add-tool-call").onclick = () => {
  if (!sample) return;
  eventDraft.push({
    key: String(tick),
    event: {
      op: "tool-call",
      tool: "file_list",
      arguments: {},
      window: `tool-result-${Date.now().toString(36)}`,
      "duration-ticks": 0,
    },
  });
  setDirty();
  renderEvents();
};
$("add-invisible-call").onclick = () => {
  if (!sample) return;
  eventDraft.push({
    key: String(tick),
    event: {
      op: "invisible-tool-call",
      tool: "file_write",
      arguments: { path: "/example.txt", content: "" },
      source: "external",
    },
  });
  setDirty();
  renderEvents();
};
$("duplicate").onclick = action(async () => {
  if (!sample) return;
  if (hasPendingChanges()) await compileSample(true);
  const copy = structuredClone(sample);
  delete copy.uuid;
  copy.id = (copy.id || "sample") + "-copy-" + Date.now().toString(36);
  const r = await json("samples", "POST", { sample: copy });
  await refresh();
  await selectSample(r.index, true);
});
$("delete-sample").onclick = action(async () => {
  if (selected < 0) return;
  closeMenus();
  if (!confirm(`Delete sample "${sample.id}" from the dataset?`)) return;
  await json(
    `samples/${selected}?expected_revision=${sampleRevision}`,
    "DELETE",
  );
  selected = -1;
  sample = null;
  plan = null;
  setDirty(false);
  await refresh();
  if (state.samples.length) await selectSample(0, true);
  else {
    renderReplay();
  }
});
async function changeHistory(direction) {
  if (compileBusy)
    throw new Error("Wait for the current compilation to finish.");
  if (hasPendingChanges()) await compileSample(true);
  const history = await json("mcp/call", "POST", { name: "get_edit_history" });
  await json("mcp/call", "POST", {
    name: direction === "undo" ? "undo_edits" : "redo_edits",
    arguments: { expected_revision: history.revision },
  });
  await refresh();
  closeMenus();
}
$("undo-edit").onclick = action(() => changeHistory("undo"));
$("redo-edit").onclick = action(() => changeHistory("redo"));
function closeMenus() {
  for (const name of ["file", "edit"]) {
    $(name + "-menu").hidden = true;
    $(name + "-menu-button").setAttribute("aria-expanded", "false");
  }
  $("export-menu").hidden = true;
  $("export-menu-button").setAttribute("aria-expanded", "false");
  $("generation-menu").hidden = true;
  $("generation-menu-button").setAttribute("aria-expanded", "false");
}
$("generation-menu-button").onclick = () => {
  $("generation-menu").hidden = false;
  $("generation-menu-button").setAttribute("aria-expanded", "true");
};
$("export-menu-button").onclick = () => {
  $("export-menu").hidden = false;
  $("export-menu-button").setAttribute("aria-expanded", "true");
};
function openGenerationPanel() {
  closeMenus();
  $("generation-panel").hidden = false;
}
$("close-generation-panel").onclick = () =>
  ($("generation-panel").hidden = true);
$("generation-panel-header").onmousedown = (e) => {
  if (e.target.closest("button")) return;
  const panel = $("generation-panel"),
    r = panel.getBoundingClientRect(),
    dx = e.clientX - r.left,
    dy = e.clientY - r.top;
  const move = (m) => {
    panel.style.right = "auto";
    panel.style.left =
      Math.max(0, Math.min(innerWidth - 60, m.clientX - dx)) + "px";
    panel.style.top =
      Math.max(0, Math.min(innerHeight - 40, m.clientY - dy)) + "px";
  };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
};
function toggleMenu(name) {
  const open = $(name + "-menu").hidden;
  closeMenus();
  $(name + "-menu").hidden = !open;
  $(name + "-menu-button").setAttribute("aria-expanded", String(open));
}
$("file-menu-button").onclick = () => toggleMenu("file");
$("edit-menu-button").onclick = () => toggleMenu("edit");
$("export-sample").onclick = action(async () => {
  closeMenus();
  if (selected < 0 || !sample) return;
  if (hasPendingChanges()) await compileSample(true);
  const name = String(sample.id || `trajectory-${selected + 1}`).replace(
    /[^a-zA-Z0-9._-]+/g,
    "_",
  );
  const blob = new Blob([JSON.stringify(sample, null, 2) + "\n"], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = node("a");
  link.href = url;
  link.download = `${name}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
});
$("preferences-button").onclick = () => {
  closeMenus();
  $("auto-compile-delay").value = (autoCompileDelayMs / 1000).toString();
  $("show-window-headers").checked = showWindowHeaders;
  $("preferences-dialog").showModal();
};
$("close-preferences").onclick = () => $("preferences-dialog").close();
$("cancel-preferences").onclick = () => $("preferences-dialog").close();
$("preferences-form").onsubmit = action(async (event) => {
  event.preventDefault();
  const delaySeconds = Number($("auto-compile-delay").value);
  if (!Number.isFinite(delaySeconds) || delaySeconds < 0.1 || delaySeconds > 60)
    throw new Error("Compile delay must be between 0.1 and 60 seconds.");
  const delayMs = Math.round(delaySeconds * 1000);
  localStorage.setItem(AUTO_COMPILE_DELAY_KEY, String(delayMs));
  autoCompileDelayMs = delayMs;
  showWindowHeaders = $("show-window-headers").checked;
  localStorage.setItem(SHOW_HEADERS_KEY, String(showWindowHeaders));
  renderReplay();
  if (compileTimer !== null) scheduleCompile();
  $("preferences-dialog").close();
});
for (const name of ["file", "edit"])
  $(name + "-menu-button").onmouseenter = () => {
    const anyOpen = !$("file-menu").hidden || !$("edit-menu").hidden;
    if ($(name + "-menu").hidden && anyOpen) toggleMenu(name);
  };
document.addEventListener("click", (e) => {
  if (!e.target.closest(".file-menu")) closeMenus();
});
$("validate-sample").onclick = action(async () => {
  closeMenus();
  if (!sample) throw new Error("Select a sample first");
  if (hasPendingChanges()) await compileSample(true);
  $("validation-report").textContent = "Validating…";
  $("report-dialog").showModal();
  try {
    $("validation-report").textContent = JSON.stringify(
      await json("validate", "POST", { sample }),
      null,
      2,
    );
  } catch (e) {
    $("validation-report").textContent = e.message;
    throw e;
  }
});
$("close-report").onclick = () => $("report-dialog").close();
for (const b of document.querySelectorAll(".tab"))
  b.onclick = action(() => changeView(b.dataset.view));
$("show-closed").onchange = renderReplay;
$("tick-number").onchange = (e) => setTick(e.target.value);
$("tick-scrubber").oninput = (e) => {
  pause();
  setTick(e.target.value);
  focusTick();
};
$("first-tick").onclick = () => {
  pause();
  setTick(0);
  focusTick();
};
$("last-tick").onclick = () => {
  pause();
  setTick(plan?.max_tick || 0);
  focusTick();
};
$("prev-tick").onclick = () => {
  pause();
  setTick(tick - 1);
  focusTick();
};
$("next-tick").onclick = () => {
  pause();
  setTick(tick + 1);
  focusTick();
};
$("play").onclick = () => {
  if (playing) {
    pause();
    return;
  }
  if (!plan) return;
  if (tick === plan.max_tick) setTick(0);
  $("play").textContent = "Pause";
  playing = setInterval(
    () => {
      if (tick >= plan.max_tick) {
        pause();
        return;
      }
      setTick(tick + 1);
      focusTick();
    },
    1000 / Number($("play-speed").value),
  );
};
$("play-speed").onchange = () => {
  if (playing) {
    pause();
    $("play").click();
  }
};
$("timeline-zoom").oninput = () => {
  drawTimeline();
  focusTick();
};
$("timeline-scroll").onscroll = drawTimeline;
$("timeline").onclick = (e) => {
  if (!plan) return;
  const rect = e.target.getBoundingClientRect();
  if (e.clientX - rect.left < 125) return;
  pause();
  setTick(
    Math.round(
      (e.clientX - rect.left + $("timeline-scroll").scrollLeft - 125) /
        +$("timeline-zoom").value,
    ),
  );
};
$("timeline").onmousemove = (e) => {
  if (!plan) return;
  const r = e.target.getBoundingClientRect(),
    t = Math.round(
      (e.clientX - r.left + $("timeline-scroll").scrollLeft - 125) /
        +$("timeline-zoom").value,
    ),
    wi = Math.floor((e.clientY - r.top - 30) / 32),
    w = plan.windows[wi],
    events = plan.events.filter(
      (x) => x.window === w?.id && x.start <= t && t <= x.end,
    );
  const anchors = Object.entries(plan.anchors)
    .filter(([, a]) => a.tick === t && a.window === w?.id)
    .map(([id]) => `{N_TICK:${id}}`);
  const call = plan.tool_calls?.[wi - plan.windows.length];
  const text =
    call && call.start <= t && t <= call.end
      ? `${call.tool} · ${call.start}–${call.end} · ${JSON.stringify(call.arguments)}`
      : [
          ...anchors,
          ...events.map(
            (x) => `${x.op} · ${x.start}–${x.end} · ${x.text.slice(0, 100)}`,
          ),
        ].join(" | ");
  $("timeline-event").textContent = text;
  e.target.title = text;
};
new ResizeObserver(drawTimeline).observe($("timeline-scroll"));

document.querySelectorAll(".resizer").forEach((handle) => {
  handle.addEventListener("pointerdown", (down) => {
    const kind = handle.dataset.resize,
      ws = $("workspace"),
      wsRect = ws.getBoundingClientRect(),
      main = document.querySelector("main"),
      vertical = kind === "timeline",
      startH = $("timeline-scroll").offsetHeight;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    down.preventDefault();
    handle.setPointerCapture(down.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add(
      "resizing",
      vertical ? "resizing-y" : "resizing-x",
    );
    const move = (e) => {
      if (kind === "sidebar")
        ws.style.setProperty(
          "--sidebar-w",
          clamp(e.clientX - wsRect.left, 120, wsRect.width * 0.5) + "px",
        );
      else if (kind === "editor")
        ws.style.setProperty(
          "--editor-w",
          clamp(wsRect.right - e.clientX, 240, wsRect.width * 0.7) + "px",
        );
      else {
        const mainRect = main.getBoundingClientRect();
        main.style.setProperty(
          "--timeline-h",
          clamp(startH + down.clientY - e.clientY, 100, mainRect.height - 200) +
            "px",
        );
      }
      drawTimeline();
    };
    const up = () => {
      handle.classList.remove("dragging");
      document.body.classList.remove("resizing", "resizing-x", "resizing-y");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
});
$("edit-tags").onclick = () => {
  configTab = "conditions";
  openSettings();
};
$("settings-button").onclick = () => {
  closeMenus();
  openSettings();
};
$("status-button").onclick = openGenerationPanel;
$("close-settings").onclick = () => $("settings-dialog").close();
$("save-config").onclick = action(saveConfig);
for (const b of document.querySelectorAll("[data-config]"))
  b.onclick = () => {
    configTab = b.dataset.config;
    renderConfig();
  };
$("generate-one").onclick = action(async () => {
  openGenerationPanel();
  if (hasPendingChanges()) await compileSample(true);
  await json("run", "POST", { automatic: false });
  await refresh();
});
$("auto-start").onclick = action(async () => {
  openGenerationPanel();
  if (hasPendingChanges()) await compileSample(true);
  await json("run", "POST", { automatic: true });
  await refresh();
});
$("stop-run").onclick = action(async () => {
  await json("stop", "POST");
  await refresh();
  if (selected >= 0 && !hasPendingChanges()) await selectSample(selected, true);
});
$("pause-run").onclick = action(async () => {
  await json("pause", "POST", { resume: state.status.paused });
  await refresh();
});
$("export-button").onclick = () => $("export-dialog").showModal();
$("close-export").onclick = () => $("export-dialog").close();
$("download-export").onclick = action(async () => {
  if (hasPendingChanges()) await compileSample(true);
  const body = { ticks_per_chunk: Number($("chunk-size").value) };
  if ($("export-scope").value === "current") body.indices = [selected];
  await download("export", "kidra-token-chunks.jsonl", "POST", body);
  $("export-dialog").close();
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    return;
  }
  if (e.key === "Escape") closeMenus();
  if (
    ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) ||
    document.querySelector("dialog[open]")
  )
    return;
  if ((e.ctrlKey || e.metaKey) && ["z", "y"].includes(e.key.toLowerCase())) {
    e.preventDefault();
    const redo = e.key.toLowerCase() === "y" || e.shiftKey;
    $(redo ? "redo-edit" : "undo-edit").click();
    return;
  }
  if (e.code === "Space") {
    e.preventDefault();
    $("play").click();
  }
  if (e.code === "ArrowLeft") {
    $("prev-tick").click();
    e.preventDefault();
  }
  if (e.code === "ArrowRight") {
    $("next-tick").click();
    e.preventDefault();
  }
});
window.addEventListener("beforeunload", (e) => {
  if (hasPendingChanges()) {
    e.preventDefault();
    e.returnValue = "";
  }
});
await action(() => refresh(true))();
setInterval(
  () =>
    action(async () => {
      const wasRunning = state?.status.running;
      await refresh();
      if (
        wasRunning &&
        !state.status.running &&
        selected >= 0 &&
        !hasPendingChanges()
      )
        await selectSample(selected, true);
    })(),
  1500,
);
