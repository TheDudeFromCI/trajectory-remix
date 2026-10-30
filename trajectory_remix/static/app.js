let generationPanelIntent = null, renderedRecoveryIssues = "";
let lastGenerationDraft = null, configBaseline = null, renderedLog = "", renderedTagScenario = "";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="remix-token"]').content;
const AUTO_COMPILE_DELAY_KEY = "trajectory-remix.auto-compile-delay";
const SHOW_HEADERS_KEY = "trajectory-remix.show-window-headers";
const COLLAPSE_STATE_PREFIX = "trajectory-remix.event-collapse.";
const SAMPLE_PAGE_SIZE = 100;
let sampleOffset = 0,
  searchTimer;
let migrationBusy = false, migrationTracking = false, migrationStarting = false;
let state,
  selected = -1,
  sample = null,
  plan = null,
  tick = 0,
  dirty = false,
  view = "events",
  playing = null;
let frameRequest = null,
  currentFrame = null,
  frameCache = new Map(),
  frameFailure = null,
  previewMessage = null;
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
    error.code = e.code;
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
    const followDraft = !state?.status.running || !sample || sample.id === lastGenerationDraft;
    state = next;
    renderTokenizerMigration(next.tokenizer_migration);
    renderTrajectoryValidation(next.validation_status);
    renderStatus();
    if (changed || initial || searchWasInvalid) renderSamples();
    if (initial && state.samples.length && !migrationBusy)
      await selectSample(state.samples[0].index, true);
    if (
      !initial &&
      sample &&
      next.revision !== sampleRevision &&
      !migrationBusy &&
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
    const draftJustArrived = next.status.draft_id && next.status.draft_id !== lastGenerationDraft;
    if (draftJustArrived && !hasPendingChanges() && Number.isInteger(next.status.draft_index) && followDraft) {
      lastGenerationDraft = next.status.draft_id;
      await selectSample(next.status.draft_index, true);
    } else if (!sample && state.samples.length && selected < 0 && !hasPendingChanges() && !migrationBusy)
      await selectSample(state.samples[state.samples.length - 1].index, true);
    if (draftJustArrived) lastGenerationDraft = next.status.draft_id;
  } finally {
    pollBusy = false;
    renderValidationState();
  }
}
function renderValidationState() {
  $("set-state-menu-button").disabled = !sample;
  if (!sample) $("set-state-menu").hidden = true;
  const level = state?.samples.find(item => item.id === sample?.id)?.validation_level
    ?? sample?.metadata?.validation_level ?? -3;
  for (const button of document.querySelectorAll("[data-validation-state]")) {
    button.disabled = !sample;
    button.setAttribute("aria-checked", String(!!sample &&
      Number(button.dataset.validationState) === level));
  }
}
function renderStatus() {
  $("sample-count").textContent = state.sample_count ?? state.samples.length;
  const s = state.status;
  const switchReady = canSwitchGeneration();
  $("migrate-tokenizer-button").disabled = s.running || !!state.validation_status?.running;
  const scenarioSelect = $("scenario-type");
  const scenarioNames = (state.scenarios || []).map((item) => item.name);
  if (Array.from(scenarioSelect.options).map((option) => option.value).join() !== scenarioNames.join()) {
    const previous = scenarioSelect.value;
    scenarioSelect.replaceChildren(...scenarioNames.map((name) => {
      const option = node("option", "", name);
      option.value = name;
      return option;
    }));
    scenarioSelect.value = scenarioNames.includes(previous) ? previous : (scenarioNames[0] || "");
  }
  scenarioSelect.disabled = s.running;
  $("generation-count").disabled = s.running;
  const batch = s.batch;
  if (batch && !(generationPanelIntent === "create" && !s.running) && (s.running || ["api_paused", "evaluation_failed"].includes(s.phase))) $("generation-count").value = String(batch.target);
  $("batch-progress").hidden = !batch || batch.target <= 1;
  const batchState = s.running && s.paused ? "paused" : s.pause_requested ? "pausing" : batch?.state;
  $("batch-progress").textContent = batch ? `${batch.completed}/${batch.target} completed · ${batch.attempts}/${batch.attempt_limit} attempts · ${batchState}` : "";
  $("view-generation-batch").hidden = !batch || batch.target <= 1;
  $("scenario-hint").textContent = scenarioNames.length
    ? (state.scenarios.find((item) => item.name === scenarioSelect.value)?.description || "")
    : "No generation scenarios are available.";
  $("generate-one").disabled = !switchReady || migrationBusy || !!state.validation_status?.running;
  $("generate-batch").disabled = !switchReady || migrationBusy || !!state.validation_status?.running;
  $("validate-trajectory").disabled = !sample || s.running || migrationBusy;
  const sampleLocked = s.running && sample?.id === s.draft_id;
  $("delete-sample").disabled = sampleLocked || !sample;
  $("sample-json").readOnly = sampleLocked;
  $("resume-trajectory-generation").disabled = !switchReady || !sample || !state.can_resume_generation;
  const recoveryDisabled = !switchReady || migrationBusy || !!state.validation_status?.running || !state.unfinished_generation_count;
  $("resume-all-generations").disabled = recoveryDisabled;
  $("resume-all-generations").title = `Resume ${state.unfinished_generation_count || 0} unfinished generated trajectories across the dataset`;
  renderGenerationAction(recoveryDisabled);
  renderValidationState();
  renderHistoryActions();
  $("selected-tags").replaceChildren(
    ...Object.entries(s.conditions || {}).flatMap(([scope, tags]) => Object.entries(tags).map(([dimension, tag]) => node("span", "tag", `${scope} · ${dimension}: ${tag.name}${tag.description ? " — " + tag.description : ""}`))),
  );
  for (const id of ["copy-generation-log", "export-generation-log", "clear-generation-log"]) $(id).hidden = !state.logs?.length;
  const logBox = $("activity-log"), logSignature = JSON.stringify(state.logs);
  if (logSignature !== renderedLog) {
    const atBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight <= 2;
    const previous = renderedLog ? JSON.parse(renderedLog) : [];
    const appendOnly = previous.length <= state.logs.length && previous.every((entry, i) => JSON.stringify(entry) === JSON.stringify(state.logs[i]));
    if (!appendOnly) logBox.replaceChildren();
    for (const log of state.logs.slice(appendOnly ? previous.length : 0)) {
      const row = node("div", "log-line log-" + (log.level || "info"));
      row.append(node("time", "", new Date(log.time).toLocaleTimeString()), document.createTextNode((log.step ? `[${log.step}] ` : "") + log.message));
      logBox.append(row);
    }
    renderedLog = logSignature;
    if (atBottom) logBox.scrollTop = logBox.scrollHeight;
  }
  renderScenarioTags();
  renderGenerationMetrics();
  updateSettingsState();
}
function canSwitchGeneration() {
  const s = state?.status || {};
  return !s.running || (state.can_suspend_generation ?? (s.paused && !s.active?.length));
}
function renderScenarioTags() {
  const scenario = state.scenarios?.find(item => item.name === $("scenario-type").value);
  if (scenario && renderedTagScenario !== scenario.name) {
    const controls = $("scenario-tag-controls"); controls.replaceChildren();
    for (const [scope, dimensions] of Object.entries(scenario.tag_pools || {})) for (const [dimension, options] of Object.entries(dimensions)) {
      const choice = select(options.map(tag => tag.name), options[0]?.name, () => { $("randomize-tags").checked = false; });
      choice.dataset.scope = scope; choice.dataset.dimension = dimension;
      controls.append(field(`${scope} · ${dimension}`, choice));
    }
    renderedTagScenario = scenario.name;
    $("randomize-tags").checked = true;
  }
  $("scenario-tag-controls").hidden = $("randomize-tags").checked;
  $("scenario-tag-controls").disabled = state.status.running;
  $("randomize-tags").disabled = state.status.running;
}
function selectedScenarioTags() {
  if ($("randomize-tags").checked) return null;
  const scenario = state.scenarios.find(item => item.name === $("scenario-type").value);
  const result = Object.fromEntries(Object.keys(scenario.tag_pools || {}).map(scope => [scope, {}]));
  for (const choice of $("scenario-tag-controls").querySelectorAll("select")) result[choice.dataset.scope][choice.dataset.dimension] = choice.value;
  return result;
}
$("randomize-tags").onchange = () => renderScenarioTags();
$("scenario-type").onchange = () => { renderScenarioTags(); };
function generationAction() {
  const s = state.status, recovery = state.generation_resume;
  if (s.running) return recovery?.running ? "resume-all" : s.activity || generationPanelIntent || "create";
  return generationPanelIntent || s.activity || (s.phase === "resume_complete" ? "resume-all" : s.draft_id && ["interrupted","cancelled","suspended","api_paused","evaluation_failed"].includes(s.phase) ? "resume" : "create");
}
function renderGenerationAction(recoveryDisabled) {
  const s = state.status, recovery = state.generation_resume || {}, mode = generationAction();
  const bulk = mode === "resume-all", setup = mode === "create" && !s.running && (generationPanelIntent === "create" || !s.draft_id);
  $("generation-panel").dataset.action = mode;
  $("generation-action-title").textContent = bulk ? "Resume unfinished drafts" : mode === "force-resume" ? "Force resume trajectory" : mode === "restart" ? "Restart trajectory" : mode === "resume" ? "Resume trajectory" : "Create trajectories";
  $("generation-action-description").textContent = bulk
    ? "Continue saved work across the dataset. Manual trajectories are excluded."
    : mode === "restart" ? "Regenerate content using the original prompts and sampled conditions." : mode === "force-resume" ? "Continue from the saved checkpoint with compatibility warnings overridden." : mode === "resume" ? "Continue from the saved checkpoint, reusing completed work."
    : setup ? "Choose a scenario and how many samples to create."
    : `${s.scenario || $("scenario-type").value} · ${s.batch?.target || 1} ${(s.batch?.target || 1) === 1 ? "trajectory" : "trajectories"}`;
  const phaseLabels = {idle:"Ready", generating:"Generating", complete:"Generation complete", resume_complete:"Resume pass complete", suspended:"Task saved for later", api_paused:"API request paused", evaluation_failed:"Review interrupted", rejected:"Needs attention", error:"Needs attention", cancelled:"Stopped", interrupted:"Interrupted"};
  $("run-status").textContent = setup ? "Ready" : s.running
    ? s.paused ? "Paused" : s.pause_requested ? "Pausing after active requests finish" : bulk ? "Resuming saved drafts" : ["resume","force-resume"].includes(mode) ? "Resuming trajectory" : mode === "restart" ? "Restarting trajectory" : "Generating"
    : bulk ? ({complete:"Resume pass complete", suspended:"Resume pass saved for later", cancelled:"Resume pass stopped", interrupted:"Resume pass interrupted", error:"Resume pass needs attention"}[recovery.state] || "Ready to resume") : (phaseLabels[s.phase] || "Ready");
  $("run-dot").style.background = s.running ? "#7be0c3" : "#667a95";
  $("generation-setup").hidden = !setup;
  $("generation-work").hidden = setup;
  $("batch-progress").hidden = bulk || setup || !s.batch || s.batch.target <= 1;
  $("view-generation-batch").hidden = setup || bulk || !s.batch || s.batch.target <= 1;
  $("view-generation-draft").hidden = setup || !s.draft_id;
  $("generation-resume-results").hidden = s.running || bulk || setup || !s.recovery_id;
  $("view-generation-draft").parentElement.hidden = $("view-generation-draft").hidden && $("view-generation-batch").hidden && $("generation-resume-results").hidden;
  $("generation-run-conditions").hidden = mode !== "create" || setup || !Object.keys(s.conditions || {}).length;
  $("generation-create-stats").hidden = mode !== "create" || setup;
  $("accepted-stat").textContent = s.batch?.completed ?? s.accepted ?? 0;
  $("attempt-stat").textContent = s.batch?.attempts ?? s.attempts ?? 0;
  $("generation-resume-stats").hidden = !bulk;
  for (const [id, value] of [["completed",recovery.completed], ["failed",recovery.failed], ["skipped",recovery.skipped], ["remaining",Math.max(0, (recovery.target || 0) - (recovery.processed || 0))]]) $("resume-" + id + "-stat").textContent = value || 0;
  $("resume-all-progress").hidden = !bulk || !recovery.target;
  $("resume-all-progress").textContent = bulk && recovery.target ? `${recovery.processed}/${recovery.target} processed` : "";
  const overall = bulk ? {done: recovery.processed || 0, total: recovery.target || 1} : s.batch?.target > 1 ? {done:s.batch.completed, total:s.batch.target} : {done:s.completed || 0, total:s.total || 1};
  $("generation-progress-label").textContent = bulk ? "Dataset progress" : s.batch?.target > 1 ? "Batch progress" : "Trajectory progress";
  $("generation-progress-count").textContent = `${Math.round(100 * overall.done / Math.max(1, overall.total))}%`;
  $("generation-progress").max = Math.max(1, overall.total);
  $("generation-progress").value = overall.done;
  $("generation-progress").title = bulk ? `${overall.done}/${overall.total} drafts processed` : `${s.completed || 0}/${s.total || 0} steps complete`;
  const current = bulk ? recovery.current_id : s.draft_id;
  $("generation-current").hidden = !s.running || !current;
  $("generation-current").textContent = current ? `Current draft: ${current}` : "";
  $("generation-step").hidden = !s.running || !(bulk || s.batch?.target > 1) || !s.total;
  $("generation-step-progress").max = Math.max(1, s.total || 1);
  $("generation-step-progress").value = s.completed || 0;
  $("generation-step-count").textContent = `${s.completed || 0}/${s.total || 0} steps`;
  $("generation-detail").textContent = s.running ? s.paused ? "Work is saved. Resume when you’re ready." : s.pause_requested ? "Active requests will finish before pausing." : s.active?.length ? `${s.active.length === 1 ? "Generating content" : `${s.active.length} content requests running`}.` : "Preparing the next step…" : s.phase === "suspended" ? "This task is saved. Start another task or resume it later." : bulk ? (recovery.attention_count ?? ((recovery.failed || 0) + (recovery.skipped || 0))) ? "Some drafts need attention. Their content and checkpoints are retained." : "All entries in this pass have been processed." : (s.last_error || (s.phase === "complete" ? "Drafts are saved and ready for review." : ""));
  const canContinue = !s.running && s.draft_id && (state.can_resume_active_generation ?? ["api_paused","evaluation_failed","cancelled","interrupted","error","rejected"].includes(s.phase));
  $("pause-run").hidden = !s.running && !setup && !(mode !== "resume-all" && canContinue);
  $("pause-run").textContent = s.running ? s.paused || s.pause_requested ? "Resume" : "Pause" : setup ? "Start" : "Resume";
  $("pause-run").disabled = migrationBusy || !!state.validation_status?.running || (setup && !state.scenarios?.length);
  $("stop-run").hidden = !s.running;
  $("stop-run").disabled = !s.running;
  $("resume-all-generations-panel").hidden = !bulk || s.running || !state.unfinished_generation_count;
  $("resume-all-generations-panel").disabled = recoveryDisabled;
  $("resume-all-generations-panel").textContent = `Resume unfinished (${state.unfinished_generation_count || 0})`;
  $("generation-create-more").hidden = mode !== "create" || setup || s.running;
  $("generation-switch-task").hidden = setup || !canSwitchGeneration() || migrationBusy || !!state.validation_status?.running || (mode === "create" && !s.running);
  $("generation-actions").hidden = [...$("generation-actions").children].every(child => child.hidden);
  $("generation-metrics-panel").hidden = setup || !(s.model_requests || Object.keys(s.metrics || {}).length);
  $("resume-issues-panel").hidden = !bulk || !recovery.issues?.length;
  $("resume-issues-summary").textContent = `Needs attention · ${recovery.attention_count ?? ((recovery.failed || 0) + (recovery.skipped || 0))}`;
  $("resume-issues-limit").hidden = (recovery.attention_count ?? ((recovery.failed || 0) + (recovery.skipped || 0))) <= 25;
  const recoveryBusy = s.running || !!state.validation_status?.running || migrationBusy;
  const issueSignature = JSON.stringify([recovery.issues || [], recoveryBusy]);
  if (renderedRecoveryIssues !== issueSignature) {
    renderedRecoveryIssues = issueSignature;
    $("resume-all-findings").replaceChildren(...(recovery.issues || []).map(issue => {
      const entry = node("article", "generation-issue"), heading = node("div", "generation-section-heading");
      heading.append(node("strong", "", issue.sample_id), node("span", "generation-issue-state", issue.state === "failed" ? "Failed" : "Skipped"));
      const error = issue.error || "No details supplied.";
      const explanation = /Tokenizer or tools changed/i.test(error) ? "The tokenizer or tools changed after this checkpoint was saved."
        : /edited since checkpoint/i.test(error) ? "This draft was edited after its checkpoint was saved. Its edits were preserved."
        : /checkpoint not found/i.test(error) ? "The saved checkpoint is missing. This draft needs manual recovery."
        : /rejected after|generation could not finish/i.test(error) ? "A generation step could not finish. Its output is retained for review."
        : error;
      const details = node("details", "generation-issue-details"); details.append(node("summary", "", "Technical details"), node("pre", "", error));
      entry.append(heading, node("p", "", explanation), details);
      if (issue.actions && issue.source_hash && Number.isInteger(issue.position)) {
        if (recoveryBusy) entry.append(node("small", "generation-help", "Recovery actions are available when generation and review are stopped."));
        else {
          const actions = node("div", "generation-recovery-actions"), choice = node("select");
          choice.setAttribute("aria-label", `Recovery action for ${issue.sample_id}`);
          const blank = node("option", "", "Choose an action…"); blank.value = ""; choice.append(blank);
          for (const [value,label] of [["force_resume","Force resume"],["restart","Restart from beginning"],["delete","Delete sample"]]) if (issue.actions[value]) {
            const option = node("option", "", label); option.value = value; choice.append(option);
          }
          const apply = button("Apply", action(() => recoverGenerationIssue(issue, choice.value)), "generation-recovery-apply");
          apply.disabled = true; choice.onchange = () => { apply.disabled = !choice.value; apply.classList.toggle("danger-text", choice.value === "delete"); };
          actions.append(choice, apply); entry.append(actions);
          if (issue.action_note) entry.append(node("small", "generation-help", issue.action_note));
        }
      }
      return entry;
    }));
  }
}
async function recoverGenerationIssue(issue, recoveryAction) {
  const message = recoveryAction === "force_resume"
    ? `Force resume "${issue.sample_id}"? This can replace edits with its saved checkpoint and override tokenizer or tool compatibility warnings. Compiler checks still apply.`
    : recoveryAction === "restart"
    ? `Restart "${issue.sample_id}" from the beginning using its original prompts and sampled conditions? Its current content will be replaced and archived.`
    : `Delete "${issue.sample_id}" from the dataset? You can undo deletion in this session.`;
  if (!confirm(message)) return;
  if (sample && hasPendingChanges()) await compileSample(true);
  await json("scenarios/recover", "POST", {run_id:state.generation_resume.id, position:issue.position, action:recoveryAction, source_hash:issue.source_hash});
  generationPanelIntent = recoveryAction === "delete" ? "resume-all" : null;
  await refresh();
}
$("generation-resume-results").onclick = () => { generationPanelIntent = "resume-all"; renderStatus(); };

function renderGenerationMetrics() {
  const root = $("generation-metrics"); root.replaceChildren();
  const entries = Object.entries(state.status.metrics || {}).filter(([path]) => !state.status.active?.length || state.status.active.includes(path));
  const format = value => typeof value === "number" ? value.toLocaleString(undefined, {maximumFractionDigits:1}) : value;
  $("generation-metrics-summary").textContent = `${state.status.model_requests || 0} requests`;
  for (const [path, metric] of entries.slice(-4)) {
    const row = node("div", "request-metrics"), heading = node("div", "generation-section-heading");
    heading.append(node("b", "", metric.model || "Model"), node("span", "generation-token-source", metric.token_source === "estimated" ? "Estimated tokens" : "Provider tokens"));
    const counts = node("div", "generation-token-grid");
    for (const [key,label] of [["prompt_tokens","Prompt"],["completion_tokens","Generated"],["total_tokens","Total"]]) {
      const stat = node("div"); stat.append(node("strong", "", format(metric[key] ?? 0)), node("small", "", label)); counts.append(stat);
    }
    row.append(heading, counts);
    if (metric.context_size && metric.context_used_tokens !== undefined && metric.budget_enforced !== false) {
      const percent = Math.min(100, 100 * metric.context_used_tokens / metric.context_size);
      const bar = node("div", "context-meter"); bar.title = `${Math.round(percent)}% context used`;
      bar.setAttribute("role", "meter"); bar.setAttribute("aria-label", "Model context usage"); bar.setAttribute("aria-valuenow", String(Math.round(percent))); bar.setAttribute("aria-valuemin", "0"); bar.setAttribute("aria-valuemax", "100");
      const used = node("span", "context-used"); used.style.width = `${percent}%`; bar.append(used); row.append(bar);
    }
    const timing = [metric.generation_seconds !== undefined ? `${format(metric.generation_seconds)} s` : null, metric.current_tokens_per_second !== undefined ? `${format(metric.current_tokens_per_second)} tokens/s` : metric.average_tokens_per_second !== undefined ? `${format(metric.average_tokens_per_second)} tokens/s` : null].filter(Boolean);
    row.append(node("small", "generation-request-timing", timing.join(" · ")));
    const details = node("details", "generation-request-details"); details.append(node("summary", "", "Request details"), node("code", "", path));
    for (const [key,label] of [["reasoning_tokens","Reasoning tokens"],["cache_read_tokens","Cached tokens"],["time_to_first_token_seconds","First token (s)"]]) if (metric[key] !== undefined) details.append(node("div", "", `${label}: ${format(metric[key])}`));
    row.append(details); root.append(row);
  }
  root.append(node("small", "generation-help", `This trajectory · ${state.status.model_requests || 0} requests · ${(state.status.total_model_seconds || 0).toFixed(1)} s inference time`));
}
$("clear-generation-log").onclick = action(async () => { await json("scenarios/log/clear", "POST"); await refresh(); });
function renderHistoryActions() {
  for (const [id, direction] of [
    ["undo-edit", "undo"],
    ["redo-edit", "redo"],
  ]) {
    const available = state?.history?.[direction] || [];
    $(id).disabled = (state?.status.running && !state.status.draft_id) || compileBusy || !available.length;
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
          `${s.events} events · ${{"-3":"Unfinished","-2":"Rejected","-1":"Unsuccessful","0":"Pending","1":"Candidate","2":"Validated","3":"Ready"}[s.validation_level ?? -3] || "Unknown"}${s.tags.length ? " · " + s.tags.slice(0, 2).join(", ") : ""}`,
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
  // Rebuild from this source; a previous tokenizer's plan must not supply
  // window colors or other preview state while loading the event editor.
  plan = null;
  sampleRevision = Number(response.headers.get("X-Remix-Revision"));
  selected = index;
  renderValidationState();
  $("sample-json").readOnly = state.status.running && sample.id === state.status.draft_id;
  $("resume-trajectory-generation").disabled = true;
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
      currentFrame = null;
      frameCache.clear();
      frameFailure = null;
      previewMessage = null;
      diagnostics(plan.diagnostics);
      if (!plan.preview.available) {
        previewMessage = plan.preview.message;
        plan = null;
        renderReplay();
        return;
      }
      setTick(Math.min(tick, plan.max_tick));
      await fetchFrame();
    } catch (e) {
      plan = null;
      previewMessage = null;
      renderReplay();
      diagnostics(
        e.diagnostics || [
          { severity: "error", path: "sample", message: e.message },
        ],
      );
    }
  } finally {
    compileBusy = false;
    renderValidationState();
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
  currentFrame = frameCache.get(tick) || null;
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
        previewMessage
          ? "Compiled successfully; preview unavailable."
          : sample
          ? "Compile the sample to preview its execution."
          : "Build a trajectory. Inspect every tick.",
      ),
      document.createTextNode(
        previewMessage || (sample
          ? "Compiler errors appear below. Edit events or source JSON, then apply again."
          : "Import JSON / JSONL samples or create a sample. Load a saved .kidra-dataset to restore its generation configuration."),
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
    const data = currentFrame?.tick === tick ? currentFrame.windows?.[w.id] : null;
    let content = data?.text || "";
    if (!showWindowHeaders)
      content = content.replace(/<\|cxt\|>[\s\S]*?<\|\/cxt\|>/, "");
    card.append(
      head,
      windowContent(
        !data
          ? frameFailure?.id === plan.plan_id && frameFailure.tick === tick
            ? `Preview unavailable: ${frameFailure.message}`
            : "Loading preview…"
          : content || (unopened ? "Not opened yet." : "No content."),
      ),
    );
    const count = data?.cached_tokens || 0;
    card.append(
      node(
        "div",
        "window-foot",
        `${data ? count : "…"} cached tokens · opened ${w.open}${w.close !== null ? " · closed " + w.close : ""}`,
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
function fetchFrame() {
  if (frameRequest || !plan || currentFrame?.tick === tick) return frameRequest;
  if (frameFailure?.id === plan.plan_id && frameFailure.tick === tick) return;
  const id = plan.plan_id,
    target = tick;
  frameRequest = (async () => {
    let failed = false;
    try {
      let result;
      try {
        // A small range amortizes request overhead during playback. Seeking always
        // starts at the selected tick; the browser never replays an earlier prefix.
        result = await json(`frames/${id}?start_tick=${target}&end_tick=${Math.min(target + 7, plan.max_tick)}`);
      } catch (e) {
        if (e.code !== "preview_limit") throw e;
        result = { frames: [await json(`frame/${id}/${target}`)] };
      }
      if (plan?.plan_id === id) {
        frameCache = new Map(result.frames.map((frame) => [frame.tick, frame]));
        currentFrame = frameCache.get(tick) || null;
        frameFailure = null;
        renderReplay();
      }
    } catch (e) {
      failed = true;
      if (plan?.plan_id === id && tick === target) {
        pause();
        frameFailure = { id, tick: target, message: e.message };
        toast(e.message, true);
        renderReplay();
      }
    } finally {
      frameRequest = null;
      // No retry loop for expired plans or output limits. A new seek/compile can
      // try again, and a seek made during an in-flight request must still load.
      if (plan && currentFrame?.tick !== tick && (!failed || plan.plan_id !== id || tick !== target))
        setTimeout(fetchFrame, 0);
    }
  })();
  return frameRequest;
}
function renderSandbox() {
  $("sandbox-panel").hidden = !plan;
  $("sandbox-title").textContent = `Sandbox · tick ${tick}`;
  $("sandbox-calls").replaceChildren();
  $("sandbox-state").replaceChildren();
  if (!plan) return;
  if (currentFrame?.tick !== tick) {
    $("sandbox-state").textContent = frameFailure?.id === plan.plan_id && frameFailure.tick === tick
      ? `Preview unavailable: ${frameFailure.message}` : "Loading sandbox…";
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
// Markdown is built as DOM text, so user guides cannot inject HTML or scripts.
function markdownInline(root, text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    root.append(document.createTextNode(text.slice(offset, match.index)));
    const token = match[0];
    if (token.startsWith("[")) {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (/^https?:\/\//i.test(link[2])) {
        const a = node("a", "", link[1]); a.href = link[2]; a.target = "_blank"; a.rel = "noopener noreferrer"; root.append(a);
      } else root.append(document.createTextNode(token));
    } else {
      const marker = token.startsWith("**") ? 2 : 1;
      const element = node(token[0] === "`" ? "code" : marker === 2 ? "strong" : "em", "", token.slice(marker, -marker));
      root.append(element);
    }
    offset = match.index + token.length;
  }
  root.append(document.createTextNode(text.slice(offset)));
}
function renderMarkdown(root, text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let list = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      const code = [];
      while (++i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i]);
      const pre = node("pre"); pre.append(node("code", "", code.join("\n"))); root.append(pre); list = null; continue;
    }
    if (!line.trim()) { list = null; continue; }
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const table = node("table");
      const cells = value => value.trim().replace(/^\||\|$/g, "").split("|");
      const row = (value, tag) => { const tr = node("tr"); for (const valueCell of cells(value)) { const cell = node(tag); markdownInline(cell, valueCell.trim()); tr.append(cell); } table.append(tr); };
      row(line, "th"); i++;
      while (i + 1 < lines.length && lines[i + 1].includes("|") && lines[i + 1].trim()) row(lines[++i], "td");
      root.append(table); list = null; continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const item = /^\s*(?:[-*+] |\d+\. )(.*)$/.exec(line);
    if (heading) { const h = node("h" + heading[1].length); markdownInline(h, heading[2]); root.append(h); list = null; }
    else if (item) {
      const type = /^\s*\d+\./.test(line) ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== type) { list = node(type); root.append(list); }
      const li = node("li"), task = /^\[([ xX])\] (.*)$/.exec(item[1]);
      if (task) { const check = node("input"); check.type = "checkbox"; check.disabled = true; check.checked = task[1] !== " "; li.append(check, document.createTextNode(" ")); }
      markdownInline(li, task ? task[2] : item[1]); list.append(li);
    } else {
      list = null;
      const p = node(line.startsWith("> ") ? "blockquote" : "p"); markdownInline(p, line.replace(/^> /, "")); root.append(p);
    }
  }
}
let guidanceBundle = null, guidanceTab = "standard.start", guidanceRequest = 0, customGuidanceScope = "";
function renderGuidelines() {
  const tabs = $("guidelines-tabs"), root = $("guidelines-content");
  tabs.replaceChildren(); root.replaceChildren();
  const documents = guidanceBundle?.documents || [];
  if (!documents.some(d => d.id === guidanceTab)) guidanceTab = documents[0]?.id;
  for (const guide of documents) {
    const tab = button(guide.title, () => { guidanceTab = guide.id; renderGuidelines(); });
    tab.id = "guide-tab-" + tabs.childElementCount; tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", "guidelines-content"); tab.setAttribute("aria-selected", String(guide.id === guidanceTab));
    tab.tabIndex = guide.id === guidanceTab ? 0 : -1;
    tab.onkeydown = event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = documents.findIndex(d => d.id === guidanceTab);
      const next = event.key === "Home" ? 0 : event.key === "End" ? documents.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + documents.length) % documents.length;
      guidanceTab = documents[next].id; renderGuidelines(); $("guidelines-tabs").children[next].focus();
    };
    tabs.append(tab);
  }
  const guide = documents.find(d => d.id === guidanceTab);
  if (!guide) return;
  root.setAttribute("aria-labelledby", [...tabs.children].find(t => t.getAttribute("aria-selected") === "true").id);
  $("guidelines-provenance").textContent = `${guide.editable ? "Custom · Edit in dashboard settings" : "Read-only"} · ${guide.source} · Dataset revision ${guidanceBundle.revision}`;
  if (!guide.content) root.append(node("p", "hint", "No custom requirements configured for this guide."));
  else if ($("guidelines-source").checked || guide.format !== "markdown") root.append(node("pre", "guide-source", guide.content));
  else renderMarkdown(root, guide.content);
}
async function loadGuidelines(scope = $("guidelines-scenario").value) {
  const request = ++guidanceRequest;
  const query = scope === "sample" && sample ? "?sample_id=" + encodeURIComponent(sample.id) : scope.startsWith("scenario:") ? "?scenario=" + encodeURIComponent(scope.slice(9)) : "";
  guidanceBundle = null;
  $("guidelines-tabs").replaceChildren(); $("guidelines-provenance").textContent = "";
  $("guidelines-error").hidden = true;
  if (!$("guidelines-scenario").options.length) {
    const names = [...new Set([...(state.scenarios || []).map(s => s.name), ...Object.keys(state.config.review_guidance?.scenarios || {})])].sort();
    for (const [value, label] of [...(sample ? [["sample", "Current trajectory (saved checklist)"]] : []), ["dataset", "Dataset / Standard guides"], ...names.map(n => ["scenario:" + n, n])]) {
      const option = node("option", "", label); option.value = value; $("guidelines-scenario").append(option);
    }
    $("guidelines-scenario").value = scope;
  }
  $("guidelines-content").textContent = "Loading guidelines…";
  try {
    const bundle = await json("guidance" + query);
    if (request !== guidanceRequest) return;
    guidanceBundle = bundle;
    const select = $("guidelines-scenario"); select.replaceChildren();
    if (sample) { const option = node("option", "", "Current trajectory (saved checklist)"); option.value = "sample"; select.append(option); }
    const dataset = node("option", "", "Dataset / Standard guides"); dataset.value = "dataset"; select.append(dataset);
    for (const name of bundle.scenarios) { const option = node("option", "", name); option.value = "scenario:" + name; select.append(option); }
    select.value = scope;
    renderGuidelines();
  } catch (error) {
    if (request !== guidanceRequest) return;
    $("guidelines-error").hidden = false; $("guidelines-error").textContent = error.message;
    $("guidelines-content").replaceChildren(); $("guidelines-tabs").replaceChildren();
  }
}
$("guidelines-button").onclick = action(async () => { closeMenus(); $("guidelines-dialog").showModal(); await loadGuidelines(sample ? "sample" : "dataset"); });
$("close-guidelines").onclick = () => $("guidelines-dialog").close();
$("guidelines-scenario").onchange = action(() => loadGuidelines());
$("guidelines-source").onchange = renderGuidelines;
$("edit-guidelines").onclick = () => { $("guidelines-dialog").close(); customGuidanceScope = guidanceBundle?.scenario || ""; configTab = "guidance"; openSettings(); };
function renderCustomGuidance(root) {
  root.append(node("p", "hint", "Additional requirements for MCP trajectory creation, validation and repair. Dataset requirements apply everywhere; scenario requirements apply to that scenario. Standard guides remain read-only. Markdown is supported."));
  const guidance = configDraft.review_guidance || {}, select = node("select");
  select.id = "custom-guidance-scenario";
  const names = [...new Set([...(state.scenarios || []).map(s => s.name), ...Object.keys(guidance.scenarios || {})])].sort();
  for (const [value, label] of [["", "Entire dataset"], ...names.map(n => [n, n])]) { const option = node("option", "", label); option.value = value; select.append(option); }
  if (!names.includes(customGuidanceScope)) customGuidanceScope = "";
  select.value = customGuidanceScope;
  select.onchange = () => { customGuidanceScope = select.value; renderConfig(); };
  root.append(field("Apply to", select));
  const pair = customGuidanceScope ? guidance.scenarios?.[customGuidanceScope] || {} : guidance;
  for (const kind of ["guide", "checklist"]) {
    const area = node("textarea"); area.id = "custom-" + kind; area.rows = 10; area.maxLength = 200000;
    area.value = pair[kind] || ""; area.placeholder = kind === "guide" ? "Use randomly generated usernames…" : "- [ ] Check dataset-specific requirements…";
    area.oninput = () => {
      configDraft.review_guidance ??= {};
      let target = configDraft.review_guidance;
      if (customGuidanceScope) {
        target.scenarios = {...target.scenarios, [customGuidanceScope]: {...target.scenarios?.[customGuidanceScope], [kind]: area.value}};
      } else target[kind] = area.value;
      updateSettingsState();
    };
    root.append(field(kind === "guide" ? "Custom Guide (Markdown)" : "Custom Checklist (Markdown)", area));
  }
}
function configIssues() {
  const errors = {};
  const range = (path, value, lo, hi, integer = false) => {
    if (!Number.isFinite(value) || value < lo || value > hi || (integer && !Number.isInteger(value))) errors[path] = `Must be ${integer ? "an integer " : ""}between ${lo} and ${hi}.`;
  };
  configDraft.endpoints.forEach((ep, i) => {
    const prefix = `endpoints.${i}.`;
    for (const key of ["id", "model"]) if (!ep[key]?.trim()) errors[prefix + key] = "Required.";
    if (configDraft.endpoints.some((other, j) => j !== i && other.id === ep.id)) errors[prefix + "id"] = "Endpoint IDs must be unique.";
    try { const url = new URL(ep.base_url); if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw Error(); } catch { errors[prefix + "base_url"] = "Enter an HTTP(S) URL without credentials, query, or fragment."; }
    range(prefix + "temperature", ep.temperature, 0, 2);
    range(prefix + "max_tokens", ep.max_tokens, 1, 65536);
    range(prefix + "timeout", ep.timeout, 1, 600);
    range(prefix + "context_size", ep.context_size ?? 0, 0, 1000000000, true);
    if (!["native", "prompted"].includes(ep.structured_output ?? "native")) errors[prefix + "structured_output"] = "Choose native schema output or prompt-only JSON.";
  });
  const single = configDraft.single_generation;
  range("single_generation.max_parallel", single.max_parallel, 1, 128, true);
  range("single_generation.max_attempts", single.max_attempts, 1, 100, true);

  range("content_first.context_budget", configDraft.content_first?.context_budget ?? 8192, 1, 1000000, true);
  range("content_first.reserve_tokens", configDraft.content_first?.reserve_tokens ?? 256, 0, 1000000, true);
  range("validation.max_input_tokens", configDraft.validation?.max_input_tokens ?? 6000, 512, 100000, true);
  range("max_tool_calls", configDraft.max_tool_calls, 1, 256, true);

  return errors;
}
function updateSettingsState() {
  if (!configDraft) return false;
  const issues = configIssues();
  for (const box of $("config-content").querySelectorAll("input, select")) {
    if (!box.dataset.configPath) {
      const label = box.closest("label")?.textContent || box.parentElement?.textContent || "";
      const keys = {"Request context budget":"content_first.context_budget", "Reserved tokens":"content_first.reserve_tokens", "Validation input token budget":"validation.max_input_tokens", "Maximum simultaneous LLM calls":"single_generation.max_parallel", "Maximum attempts per single sample":"single_generation.max_attempts", "Tool calls per sample":"max_tool_calls"};
      for (const [name, path] of Object.entries(keys)) if (label.includes(name)) box.dataset.configPath = path;
    }
    const message = issues[box.dataset.configPath] || "";
    box.setCustomValidity(message);
    box.setAttribute("aria-invalid", String(!!message));
    box.title = message;
  }
  $("settings-errors").textContent = Object.entries(issues).map(([path, message]) => `${path}: ${message}`).join("\n");
  const valid = !Object.keys(issues).length;
  $("save-config").disabled = !valid || JSON.stringify(configDraft) === configBaseline;
  return valid;
}
$("config-content").addEventListener("input", () => updateSettingsState());
$("config-content").addEventListener("change", () => updateSettingsState());
$("copy-generation-log").onclick = action(async () => { await navigator.clipboard.writeText(await (await api("scenarios/log")).text()); toast("Output copied."); });
$("export-generation-log").onclick = action(async () => {
  const url = URL.createObjectURL(await (await api("scenarios/log")).blob());
  const link = node("a"); link.href = url; link.download = "generation.log"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
});
function openSettings() {
  if (!configDraft) {
    configDraft = structuredClone(state.config);
    configDraft.single_generation = {max_parallel: 1, max_attempts: 3, ...configDraft.single_generation};
    configBaseline = JSON.stringify(configDraft);
  }
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
  if (configTab === "guidance") renderCustomGuidance(root);
  if (configTab === "endpoints") {
    configDraft.single_generation = {max_parallel: 1, max_attempts: 3, ...configDraft.single_generation};
    root.append(field("Maximum simultaneous LLM calls", Object.assign(input(configDraft.single_generation.max_parallel, v => configDraft.single_generation.max_parallel = v, "number"), {id: "scenario-parallel"})), field("Maximum attempts per single sample", input(configDraft.single_generation.max_attempts, v => configDraft.single_generation.max_attempts = v, "number")));
    root.append(
      node(
        "div",
        "hint",
        "Scenario steps select endpoints by ID. Configure generation and validation models here. Base URLs should include /v1. Context size 0 means unknown.",
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
        ["context_size", "Context size (tokens, 0 = unknown)", "number"],
        ["inference_tokenizer", "Local inference tokenizer directory (optional)", "text"],
      ]) {
        const box = input(ep[name] ?? (name === "context_size" ? 0 : undefined), (v) => (ep[name] = v), type);
        box.dataset.configPath = `endpoints.${i}.${name}`;
        form.append(field(label, box, name === "base_url" ? "wide" : ""));
      }
      form.append(node("small", "hint", "Token budget checks run only when the inference tokenizer is available. Leave its directory blank to skip checks."));
      const structuredOutput = select([
        ["native", "Native JSON schema (recommended)"],
        ["prompted", "Prompt-only JSON (compatibility)"],
      ], ep.structured_output ?? "native", v => ep.structured_output = v);
      structuredOutput.dataset.configPath = `endpoints.${i}.structured_output`;
      form.append(field("Structured output", structuredOutput, "wide"));
      form.append(node("small", "hint", "Native mode asks the server to enforce each output schema. Use prompt-only JSON if your server does not support the schema. Unsupported native requests pause generation and retain the draft."));
      form.append(
        field(
          "Clear stored session key",
          checkbox(ep.clear_key, (v) => (ep.clear_key = v)),
          "check",
        ),
      );
      const result = node("small", "endpoint-test-result");
      top.append(button("Test endpoint", action(async () => {
        result.textContent = "Testing…";
        try { const answer = await json("endpoints/test", "POST", {endpoint: ep}); result.textContent = `Connected: ${answer.model}`; }
        catch (error) { result.textContent = error.message; }
      })));
      card.append(top, form, result);
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
          structured_output: "native",
        });
        renderConfig();
      }),
    );
  }
  if (configTab === "defaults") {
    const form = node("div", "form-grid");
    configDraft.content_first ??= {context_budget: 8192, reserve_tokens: 256};
    configDraft.validation ??= {endpoint: "validator", max_input_tokens: 6000};
    root.append(node("div", "hint", "Content creation never performs semantic validation. Validate an existing trajectory separately from Edit → Generation."));
    form.append(
      field("Request context budget", input(configDraft.content_first.context_budget, v => configDraft.content_first.context_budget = v, "number")),
      field("Reserved tokens", input(configDraft.content_first.reserve_tokens, v => configDraft.content_first.reserve_tokens = v, "number")),
      field("Default validation endpoint", select(eps, configDraft.validation.endpoint, v => configDraft.validation.endpoint = v)),
      field("Validation input token budget", input(configDraft.validation.max_input_tokens, v => configDraft.validation.max_input_tokens = v, "number")),
      field("Tool calls per sample", input(configDraft.max_tool_calls, v => configDraft.max_tool_calls = v, "number")),
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
  updateSettingsState();
}
async function saveConfig() {
  if (!updateSettingsState()) return;
  try { await json("config", "PUT", { config: configDraft }); }
  catch (error) {
    $("settings-errors").textContent = error.message;
    $("save-config").disabled = true;
    throw error;
  }
  await refresh();
  configBaseline = JSON.stringify(configDraft);
  updateSettingsState();
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
  for (const name of ["file", "edit", "view"]) {
    $(name + "-menu").hidden = true;
    $(name + "-menu-button").setAttribute("aria-expanded", "false");
  }
  $("export-menu").hidden = true;
  $("export-menu-button").setAttribute("aria-expanded", "false");
  $("set-state-menu").hidden = true;
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
function toggleMenu(name) {
  const open = $(name + "-menu").hidden;
  closeMenus();
  $(name + "-menu").hidden = !open;
  $(name + "-menu-button").setAttribute("aria-expanded", String(open));
}
$("file-menu-button").onclick = () => toggleMenu("file");
$("edit-menu-button").onclick = () => toggleMenu("edit");
$("view-menu-button").onclick = () => toggleMenu("view");
function setDatasetPanelVisible(visible) {
  const workspace = $("workspace");
  workspace.classList.toggle("dataset-panel-hidden", !visible);
  workspace.querySelector(".sidebar").hidden = !visible;
  workspace.querySelector('[data-resize="sidebar"]').hidden = !visible;
  $("dataset-panel-check").textContent = visible ? "✔" : "";
  $("toggle-dataset-panel").setAttribute("aria-checked", String(visible));
  drawTimeline();
}
$("toggle-dataset-panel").onclick = () => {
  setDatasetPanelVisible(
    $("workspace").classList.contains("dataset-panel-hidden"),
  );
  closeMenus();
};
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
  $("model-api-retries").value = state.config.single_generation?.api_retries ?? 2;
  $("preferences-dialog").showModal();
};
function setMigrationBusy(busy) {
  migrationBusy = busy;
  $("workspace").inert = busy;
  $("tokenizer-migration-inputs").disabled = busy;
  $("start-tokenizer-migration").disabled = busy;
}
function renderTokenizerMigration(status) {
  if (!status || migrationStarting) return;
  if (status.status === "running") migrationTracking = true;
  if (!migrationTracking) return;
  setMigrationBusy(status.status === "running");
  const total = status.total || 0, converted = status.converted || 0;
  $("tokenizer-migration-progress").max = Math.max(1, total);
  $("tokenizer-migration-progress").value = status.status === "completed" ? Math.max(1, total) : converted;
  $("tokenizer-migration-count").textContent = `${converted} / ${total} trajectories converted`;
  $("tokenizer-migration-message").textContent = status.status === "completed"
    ? "Migration complete. The new tokenizer is saved and active."
    : status.status === "failed" ? "Migration failed. The project tokenizer was not changed."
    : "Converting trajectories… Migration continues if you close this window.";
  const details = (status.diagnostics || []).map(d => `${d.path}: ${d.message}`).join("\n");
  $("tokenizer-migration-error").hidden = status.status !== "failed";
  $("tokenizer-migration-error").textContent = [status.error, details].filter(Boolean).join("\n");
}
$("migrate-tokenizer-button").onclick = action(async () => {
  closeMenus();
  if (compileBusy) throw new Error("Wait for the current compilation to finish.");
  if (hasPendingChanges()) await compileSample(true);
  await refresh();
  if (state.status.running) throw new Error("Stop generation before migrating the tokenizer.");
  pause();
  migrationTracking = state.tokenizer_migration?.status === "running";
  if (migrationTracking) renderTokenizerMigration(state.tokenizer_migration);
  else {
    setMigrationBusy(false);
    $("tokenizer-migration-form").reset();
    for (const name of ["bos", "eos", "noop"])
      $("tokenizer-" + name).value = state.tokenizer.special[name];
    $("tokenizer-migration-progress").max = Math.max(1, state.sample_count);
    $("tokenizer-migration-progress").value = 0;
    $("tokenizer-migration-count").textContent = `0 / ${state.sample_count} trajectories converted`;
    $("tokenizer-migration-message").textContent = "Choose or paste the new tokenizer configuration to begin.";
    $("tokenizer-migration-error").hidden = true;
  }
  $("tokenizer-migration-dialog").showModal();
});
$("close-tokenizer-migration").onclick = $("dismiss-tokenizer-migration").onclick =
  () => $("tokenizer-migration-dialog").close();
$("tokenizer-file").onchange = action(async () => {
  const file = $("tokenizer-file").files[0];
  if (file) $("tokenizer-config").value = await file.text();
});
$("tokenizer-migration-form").onsubmit = async (event) => {
  event.preventDefault();
  if (migrationBusy) return;
  migrationStarting = true;
  setMigrationBusy(true);
  $("tokenizer-migration-error").hidden = true;
  try {
    let status = await json("tokenizer/migrate", "POST", {
      tokenizer: $("tokenizer-config").value,
      special: Object.fromEntries(["bos", "eos", "noop"].map(name => [name, $("tokenizer-" + name).value])),
      expected_revision: state.revision,
    });
    migrationStarting = false;
    migrationTracking = true;
    renderTokenizerMigration(status);
    while (status.status === "running") {
      await new Promise(resolve => setTimeout(resolve, 300));
      status = await json("tokenizer/migration");
      renderTokenizerMigration(status);
    }
    await refresh();
  } catch (error) {
    $("tokenizer-migration-error").hidden = false;
    $("tokenizer-migration-error").textContent = error.message;
    // A lost response may still have started a migration; recover its status.
    migrationStarting = false;
    migrationTracking = false;
    setMigrationBusy(false);
    await action(() => refresh())();
  } finally {
    migrationStarting = false;
  }
};
$("close-preferences").onclick = () => $("preferences-dialog").close();
$("cancel-preferences").onclick = () => $("preferences-dialog").close();
$("preferences-form").onsubmit = action(async (event) => {
  event.preventDefault();
  const delaySeconds = Number($("auto-compile-delay").value);
  if (!Number.isFinite(delaySeconds) || delaySeconds < 0.1 || delaySeconds > 60)
    throw new Error("Compile delay must be between 0.1 and 60 seconds.");
  const apiRetries = Number($("model-api-retries").value);
  if (!Number.isInteger(apiRetries) || apiRetries < 0 || apiRetries > 20) throw new Error("API retries must be an integer from 0 to 20.");
  if (apiRetries !== (state.config.single_generation?.api_retries ?? 2)) {
    await json("preferences", "POST", {api_retries: apiRetries});
    if (configDraft) { configDraft.single_generation ??= {}; configDraft.single_generation.api_retries = apiRetries; }
    if (configBaseline) { const baseline = JSON.parse(configBaseline); baseline.single_generation ??= {}; baseline.single_generation.api_retries = apiRetries; configBaseline = JSON.stringify(baseline); }
    await refresh();
  }
  const delayMs = Math.round(delaySeconds * 1000);
  localStorage.setItem(AUTO_COMPILE_DELAY_KEY, String(delayMs));
  autoCompileDelayMs = delayMs;
  showWindowHeaders = $("show-window-headers").checked;
  localStorage.setItem(SHOW_HEADERS_KEY, String(showWindowHeaders));
  renderReplay();
  if (compileTimer !== null) scheduleCompile();
  $("preferences-dialog").close();
});
for (const name of ["file", "edit", "view"])
  $(name + "-menu-button").onmouseenter = () => {
    const anyOpen = ["file", "edit", "view"].some(
      (menu) => !$(menu + "-menu").hidden,
    );
    if ($(name + "-menu").hidden && anyOpen) toggleMenu(name);
  };
document.addEventListener("click", (e) => {
  if (!e.target.closest(".file-menu")) closeMenus();
});
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
      // Buffer on slow requests so playback does not silently skip states.
      if (currentFrame?.tick !== tick) return;
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
      ? `${call.tool} · ${call.start}–${call.end}`
      : [
          ...anchors,
          ...events.map(
            (x) => `${x.op} · ${x.start}–${x.end}`,
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
  configTab = "endpoints";
  openSettings();
};
$("settings-button").onclick = () => {
  closeMenus();
  openSettings();
};
$("status-button").onclick = () => { generationPanelIntent = null; renderStatus(); openGenerationPanel(); };
$("set-state-menu-button").onclick = () => { if (sample) $("set-state-menu").hidden = false; };
for (const button of document.querySelectorAll("[data-validation-state]")) button.onclick = action(async () => {
  closeMenus(); $("set-state-menu").hidden = true;
  if (hasPendingChanges()) await compileSample(true);
  await json("samples/" + selected + "/state", "POST", {level: Number(button.dataset.validationState)});
  await refresh();
});
async function resumeGeneration(id) {
  const response = await fetch("/api/scenarios/resume", {method: "POST", headers: {"Content-Type":"application/json", "X-Remix-Token": document.querySelector('meta[name="remix-token"]').content}, body: JSON.stringify({sample_id:id})});
  const result = await response.json();
  if (result.confirmation_required) {
    if (!confirm("This trajectory was edited since its checkpoint. Continue from the saved generation checkpoint and replace those edits?")) return false;
    await json("scenarios/resume", "POST", {sample_id:id, confirm_edited:true});
  } else if (!response.ok) throw new Error(result.detail);
  return true;
}
async function savePausedGeneration() {
  if (!state.status.running) return;
  if (!canSwitchGeneration()) throw new Error("Pause generation and let active LLM calls finish before switching tasks.");
  await json("scenarios/suspend", "POST");
  await refresh();
}
$("resume-trajectory-generation").onclick = action(async () => {
  closeMenus();
  if (hasPendingChanges()) await compileSample(true);
  await savePausedGeneration();
  if (!await resumeGeneration(sample.id)) return;
  generationPanelIntent = "resume";
  openGenerationPanel(); await refresh();
});

async function resumeAllGenerations() {
  closeMenus();
  if (sample && hasPendingChanges()) await compileSample(true);
  if (state.status.running && state.generation_resume?.running && state.status.activity === "resume-all") {
    await json("pause", "POST", {resume:true});
    generationPanelIntent = "resume-all";
    openGenerationPanel(); await refresh();
    return;
  }
  await savePausedGeneration();
  await json("scenarios/resume-all", "POST", {});
  generationPanelIntent = "resume-all";
  openGenerationPanel();
  await refresh();
}
$("resume-all-generations").onclick = action(resumeAllGenerations);
$("resume-all-generations-panel").onclick = action(resumeAllGenerations);

function renderTrajectoryValidation(status = {}) {
  const busy = !!status.running;
  $("validation-progress").max = Math.max(1, status.total || 1);
  $("validation-progress").value = status.completed || 0;
  $("validation-status").textContent = status.last_error || (busy ? `Validating ${status.sample_id}: ${status.active?.join(" · ") || "preparing"}` : status.result ? (status.result.valid ? "Validation passed." : "Validation found issues.") : status.phase === "idle" ? "Ready to validate." : status.phase || "Ready to validate.");
  $("validation-findings").textContent = status.result ? JSON.stringify(status.result, null, 2) : "";
  $("start-validation").disabled = busy || !sample || !!state?.status.running;
  $("cancel-validation").disabled = !busy;
  $("resume-validation").hidden = !["cancelled", "interrupted", "incomplete"].includes(status.phase) || status.sample_id !== sample?.id;
  $("validation-endpoint").disabled = busy;
  $("validation-record").disabled = busy;
}
$("validate-trajectory").onclick = action(async () => {
  closeMenus();
  if (hasPendingChanges()) await compileSample(true);
  const choices = state.config.endpoints.map(ep => ep.id);
  const previous = $("validation-endpoint").value;
  $("validation-endpoint").replaceChildren(...choices.map(id => { const option = node("option", "", id); option.value = id; return option; }));
  $("validation-endpoint").value = choices.includes(previous) ? previous : choices.includes(state.config.validation?.endpoint || "validator") ? (state.config.validation?.endpoint || "validator") : (choices[0] || "");
  renderTrajectoryValidation(state.validation_status);
  $("validation-dialog").showModal();
});
$("close-validation").onclick = () => $("validation-dialog").close();
async function startTrajectoryValidation(resume = false) {
  if (hasPendingChanges()) await compileSample(true);
  await json("validation/run", "POST", {sample_id: sample.id, endpoint: $("validation-endpoint").value, record: $("validation-record").checked, resume});
  await refresh();
}
$("start-validation").onclick = action(() => startTrajectoryValidation());
$("resume-validation").onclick = action(() => startTrajectoryValidation(true));
$("cancel-validation").onclick = action(async () => { await json("validation/stop", "POST"); await refresh(); });

$("close-settings").onclick = () => $("settings-dialog").close();
$("save-config").onclick = action(saveConfig);
for (const b of document.querySelectorAll("[data-config]"))
  b.onclick = () => {
    configTab = b.dataset.config;
    renderConfig();
  };
async function prepareGenerationTask(kind) {
  closeMenus();
  if (sample && hasPendingChanges()) await compileSample(true);
  await savePausedGeneration();
  generationPanelIntent = "create"; renderStatus();
  openGenerationPanel();
  $("generation-count").value = kind === "single" ? "1" : "10";
  $(kind === "single" ? "scenario-type" : "generation-count").focus();
}
$("generate-one").onclick = action(() => prepareGenerationTask("single"));
$("generate-batch").onclick = action(() => prepareGenerationTask("batch"));
$("generation-next-task").onchange = action(async () => {
  const kind = $("generation-next-task").value;
  $("generation-next-task").value = "";
  if (kind) await prepareGenerationTask(kind);
});
$("generation-create-more").onclick = () => { generationPanelIntent = "create"; renderStatus(); $("scenario-type").focus(); };
$("view-generation-batch").onclick = action(async () => {
  if (!state.status.batch) return;
  if (hasPendingChanges()) await compileSample(true);
  const query = `@batch:${state.status.batch.id}`;
  $("sample-search").value = query;
  sampleOffset = 0;
  const listing = await json("state?" + new URLSearchParams({query, offset: "0", limit: String(SAMPLE_PAGE_SIZE)}));
  lastGenerationDraft = listing.status.draft_id;
  if (listing.samples.length) await selectSample(listing.samples[0].index, true);
  await refresh();
  $("generation-panel").hidden = true;
});
$("view-generation-draft").onclick = action(async () => {
  const listing = await json("state?" + new URLSearchParams({ selected_id: state.status.draft_id, limit: "1" }));
  if (Number.isInteger(listing.selected_index)) { await selectSample(listing.selected_index); $("generation-panel").hidden = true; }
  else toast("This draft is no longer available.");
});
$("stop-run").onclick = action(async () => {
  await json("stop", "POST");
  await refresh();
  if (selected >= 0 && !hasPendingChanges()) await selectSample(selected, true);
});
$("pause-run").onclick = action(async () => {
  if (!state.status.running) {
    if (hasPendingChanges()) await compileSample(true);
    if (generationPanelIntent !== "create" && ["api_paused", "evaluation_failed", "cancelled", "suspended", "interrupted", "error", "rejected"].includes(state.status.phase)) {
      if (!await resumeGeneration(state.status.draft_id)) return;
      generationPanelIntent = "resume";
    }
    else {
      const count = $("generation-count");
      if (!count.reportValidity()) return;
      await json("scenarios/run", "POST", {scenario: $("scenario-type").value, tags: selectedScenarioTags(), batch_size: Number(count.value)});
    }
  } else await json("pause", "POST", { resume: state.status.paused || state.status.pause_requested });
  generationPanelIntent = null;
  await refresh();
});
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
      const wasValidating = state?.validation_status?.running;
      await refresh();
      if (
        ((wasRunning && !state.status.running) || (wasValidating && !state.validation_status?.running)) &&
        selected >= 0 &&
        !hasPendingChanges()
      )
        await selectSample(selected, true);
    })(),
  2000,
);
