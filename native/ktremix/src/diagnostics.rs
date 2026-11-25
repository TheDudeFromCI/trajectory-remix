use crate::model::{Content, Kind, Mode, Piece, Source};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};

/// How serious a [`Diagnostic`] is.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    /// Blocks compilation.
    Error,
    /// Reported, but compilation can continue.
    Warning,
}

/// A problem found while analysing or scheduling a graph.
#[derive(Clone, Debug, Serialize)]
pub struct Diagnostic {
    /// Whether the finding blocks compilation.
    pub severity: Severity,
    /// Stable machine-readable code such as `compile_error`.
    pub code: &'static str,
    /// JSON path to the offending value in the source graph.
    pub path: Vec<Value>,
    /// Event the finding is about, if any.
    pub event_id: Option<String>,
    /// Human-readable explanation.
    pub message: String,
    /// Other events involved, such as the members of a dependency cycle.
    pub related_events: Vec<String>,
}

impl Diagnostic {
    pub(crate) fn general(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            severity: Severity::Error,
            code,
            path: Vec::new(),
            event_id: None,
            message: message.into(),
            related_events: Vec::new(),
        }
    }

    fn event(code: &'static str, message: impl Into<String>, id: &str, field: &str) -> Self {
        let mut value = Self::general(code, message);
        value.event_id = Some(id.to_owned());
        value.path = vec![json!("events"), json!(id), json!(field)];
        value
    }

    fn warning(mut self) -> Self {
        self.severity = Severity::Warning;
        self
    }

    fn related(mut self, id: &str) -> Self {
        self.related_events.push(id.to_owned());
        self
    }
}

#[derive(Default, Deserialize)]
#[serde(default)]
pub(crate) struct Settings {
    reasoning_levels: Vec<String>,
    tools: BTreeMap<String, Tool>,
}

#[derive(Deserialize)]
struct Tool {
    #[serde(default = "empty_schema")]
    input_schema: Value,
    response_window: Option<String>,
}

fn empty_schema() -> Value {
    json!({})
}

pub(crate) struct Graph<'a> {
    pub names: Vec<&'a str>,
    pub dependencies: Vec<Vec<usize>>,
    pub children: Vec<Vec<usize>>,
    ranks: Vec<usize>,
}

impl<'a> Graph<'a> {
    fn ancestor(&self, ancestor: usize, mut node: usize) -> bool {
        if self.ranks[ancestor] >= self.ranks[node] {
            return false;
        }
        let mut alternatives = Vec::new();
        let mut visited = HashSet::new();
        loop {
            let mut next = None;
            for &dep in &self.dependencies[node] {
                if dep == ancestor {
                    return true;
                }
                if self.ranks[dep] > self.ranks[ancestor] {
                    if let Some(previous) = next.replace(dep) {
                        alternatives.push(previous);
                    }
                }
            }
            if let Some(dep) = next {
                node = dep;
                continue;
            }
            loop {
                let Some(candidate) = alternatives.pop() else {
                    return false;
                };
                if visited.insert(candidate) {
                    node = candidate;
                    break;
                }
            }
        }
    }

    fn compare(&self, ids: &[usize], diagnostics: &mut Vec<Diagnostic>) -> bool {
        if ids.windows(2).all(|pair| self.ancestor(pair[0], pair[1])) {
            return true;
        }
        let mut valid = true;
        for (index, &left) in ids.iter().enumerate() {
            for &right in &ids[index + 1..] {
                if !self.ancestor(left, right) {
                    diagnostics.push(
                        Diagnostic::event(
                            "unordered_window_writers",
                            "Writers must be dependency ordered",
                            self.names[right],
                            "after",
                        )
                        .related(self.names[left]),
                    );
                    valid = false;
                }
            }
        }
        valid
    }

    fn cycles(&self) -> BTreeSet<usize> {
        let mut color = vec![0; self.names.len()];
        let mut position = vec![0; self.names.len()];
        let mut cyclic = BTreeSet::new();
        for root in 0..self.names.len() {
            if color[root] != 0 {
                continue;
            }
            let mut stack = vec![(root, 0)];
            color[root] = 1;
            while let Some((node, edge)) = stack.last_mut() {
                if let Some(&dep) = self.dependencies[*node].get(*edge) {
                    *edge += 1;
                    match color[dep] {
                        0 => {
                            color[dep] = 1;
                            position[dep] = stack.len();
                            stack.push((dep, 0));
                        }
                        1 => cyclic.extend(stack[position[dep]..].iter().map(|&(id, _)| id)),
                        _ => {}
                    }
                } else {
                    color[*node] = 2;
                    stack.pop();
                }
            }
        }
        cyclic
    }
}

pub(crate) fn analyze<'a>(
    source: &'a Source,
    settings: Option<&Settings>,
) -> (Graph<'a>, Vec<Diagnostic>) {
    let names: Vec<_> = source.events.keys().map(String::as_str).collect();
    let indices: HashMap<_, _> = names
        .iter()
        .enumerate()
        .map(|(i, &name)| (name, i))
        .collect();
    let mut graph = Graph {
        names,
        dependencies: vec![Vec::new(); indices.len()],
        children: vec![Vec::new(); indices.len()],
        ranks: vec![0; indices.len()],
    };
    let mut diagnostics = Vec::new();
    let written: HashSet<_> = source
        .events
        .values()
        .map(|event| event.window.as_str())
        .collect();
    let mut total = 0usize;
    let mut agent = 0usize;
    if source.format != "kidra-trajectory-graph/1" {
        diagnostics.push(Diagnostic::general(
            "invalid_source",
            "Unsupported trajectory graph format",
        ));
    }
    if !valid_weight(source.defaults.noop_loss_weight) {
        diagnostics.push(Diagnostic::general(
            "invalid_source",
            "Invalid NOOP loss weight",
        ));
    }
    for (index, &id) in graph.names.iter().enumerate() {
        let event = &source.events[id];
        let mut emit =
            |code, message, field| diagnostics.push(Diagnostic::event(code, message, id, field));
        if id.is_empty() || event.window.is_empty() {
            emit(
                "invalid_source",
                "Identifiers must be nonempty".into(),
                "window",
            );
        }
        match source.windows.get(&event.window) {
            None => emit(
                "missing_window",
                format!("Unknown window: {}", event.window),
                "window",
            ),
            Some(window) if event.model() && !window.agent_writable => emit(
                "window_not_writable",
                "Agent output requires an agent-writable window".into(),
                "window",
            ),
            Some(window) if event.kind == Kind::ChatMessage && window.purpose != "chatroom" => {
                emit(
                    "invalid_chat_window",
                    "Chat messages need a window whose purpose is \"chatroom\"".into(),
                    "window",
                )
            }
            _ => {}
        }
        if (matches!(event.kind, Kind::ToolCall | Kind::ToolResponse)
            && event.call_id.as_ref().is_none_or(String::is_empty))
            || (event.kind == Kind::ToolCall && event.tool.as_ref().is_none_or(String::is_empty))
            || (event.kind == Kind::ChatMessage
                && (event
                    .chat
                    .as_ref()
                    .is_none_or(|chat| chat.speaker.is_empty())
                    || !matches!(event.delivery.mode, Mode::Instant)))
            || event
                .supervision
                .is_some_and(|value| !valid_weight(value.weight))
            || (event.replace_body
                && !matches!(
                    event.kind,
                    Kind::ExternalText | Kind::ToolSchema | Kind::ToolResponse
                ))
        {
            emit("invalid_source", "Invalid event fields".into(), "kind");
        }
        if event.after.iter().collect::<HashSet<_>>().len() != event.after.len() {
            diagnostics.push(
                Diagnostic::event(
                    "duplicate_dependency",
                    "Dependencies must be unique",
                    id,
                    "after",
                )
                .warning(),
            );
        }
        for (ordinal, dep) in event.after.iter().enumerate() {
            if let Some(&parent) = indices.get(dep.as_str()) {
                graph.dependencies[index].push(parent);
            } else {
                let mut diagnostic = Diagnostic::event(
                    "missing_dependency",
                    format!("Unknown event: {dep}"),
                    id,
                    "after",
                );
                diagnostic.path.push(json!(ordinal));
                diagnostics.push(diagnostic);
            }
        }
        graph.dependencies[index].sort_unstable();
        graph.dependencies[index].dedup();
        for &parent in &graph.dependencies[index] {
            graph.children[parent].push(index);
        }
        let mut characters = 0;
        let mut has_reference = false;
        match &event.content {
            Content::String(text) => characters = text.chars().count(),
            Content::Pieces(pieces) => {
                for (ordinal, piece) in pieces.iter().enumerate() {
                    match piece {
                        Piece::Text { text } => characters += text.chars().count(),
                        Piece::WindowRef { window } => {
                            has_reference = true;
                            let (code, message) = if !source.windows.contains_key(window) {
                                (
                                    "missing_window_reference",
                                    format!("Unknown referenced window: {window}"),
                                )
                            } else if !written.contains(window.as_str()) {
                                (
                                    "unopened_window_reference",
                                    format!("Referenced window never opens: {window}"),
                                )
                            } else {
                                continue;
                            };
                            let mut diagnostic = Diagnostic::event(code, message, id, "content");
                            diagnostic.path.extend([json!(ordinal), json!("window")]);
                            diagnostics.push(diagnostic);
                        }
                    }
                }
            }
        }
        total += characters;
        if event.model() {
            agent += characters;
        }
        if event.replace_body && characters == 0 && !has_reference {
            diagnostics.push(Diagnostic::event(
                "empty_replacement",
                "Replacement must insert nonempty content",
                id,
                "content",
            ));
        }
    }

    for (id, window) in &source.windows {
        if id.is_empty()
            || id == "@available"
            || window.purpose.is_empty()
            || window
                .reasoning_effort
                .as_ref()
                .is_some_and(String::is_empty)
        {
            let mut diagnostic = Diagnostic::general("invalid_source", "Invalid window definition");
            diagnostic.path = vec![json!("windows"), json!(id)];
            diagnostics.push(diagnostic);
        }
        if let Some(level) = &window.reasoning_effort {
            if settings.is_some_and(|settings| !settings.reasoning_levels.contains(level)) {
                let mut diagnostic = Diagnostic::general(
                    "unknown_reasoning_level",
                    format!("Unknown reasoning level: {level}"),
                );
                diagnostic.path = vec![json!("windows"), json!(id), json!("reasoning_effort")];
                diagnostics.push(diagnostic);
            }
        }
    }

    let mut degrees: Vec<_> = graph.dependencies.iter().map(Vec::len).collect();
    let mut ready: VecDeque<_> = (0..degrees.len()).filter(|&id| degrees[id] == 0).collect();
    let mut ordered = Vec::with_capacity(graph.names.len());
    while let Some(id) = ready.pop_front() {
        graph.ranks[id] = ordered.len();
        ordered.push(id);
        for &child in &graph.children[id] {
            degrees[child] -= 1;
            if degrees[child] == 0 {
                ready.push_back(child);
            }
        }
    }
    if ordered.len() != graph.names.len() {
        for id in graph.cycles() {
            diagnostics.push(Diagnostic::event(
                "dependency_cycle",
                "Event belongs to a dependency cycle",
                graph.names[id],
                "after",
            ));
        }
        return (graph, sort(diagnostics));
    }

    let mut windows: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    let mut calls: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    let mut responses: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for &id in &ordered {
        let event = &source.events[graph.names[id]];
        windows.entry(&event.window).or_default().push(id);
        if let Some(call) = event.call_id.as_deref() {
            match event.kind {
                Kind::ToolCall => calls.entry(call).or_default().push(id),
                Kind::ToolResponse => responses.entry(call).or_default().push(id),
                _ => {}
            }
        }
    }
    for (window, ids) in windows {
        let window_ordered = graph.compare(&ids, &mut diagnostics);
        let mut open = None;
        let mut closes = Vec::new();
        for &id in &ids {
            let name = graph.names[id];
            let event = &source.events[name];
            for &closed in &closes {
                if window_ordered || graph.ancestor(closed, id) {
                    diagnostics.push(
                        Diagnostic::event(
                            "output_after_close",
                            "Logical windows cannot reopen after closure",
                            name,
                            "window",
                        )
                        .related(graph.names[closed]),
                    );
                }
            }
            if window_ordered {
                if matches!(event.kind, Kind::ToolCall | Kind::ToolResponse) {
                    let frame = (event.kind, event.call_id.as_deref());
                    let (begin, end) = if event.kind == Kind::ToolCall {
                        (event.insert_tool_begin, event.insert_tool_end)
                    } else {
                        (event.insert_response_begin, event.insert_response_end)
                    };
                    if open.is_some_and(|previous| previous != frame) {
                        diagnostics.push(Diagnostic::event(
                            "interleaved_framing",
                            "Another request/response frame is still open in this window",
                            name,
                            "call_id",
                        ));
                    }
                    if begin {
                        open = Some(frame);
                    }
                    if end {
                        open = None;
                    }
                } else if open.is_some() && !event.content.is_empty() {
                    diagnostics.push(Diagnostic::event(
                        "interleaved_framing",
                        "Content inside an open frame must be an explicit fragment",
                        name,
                        "content",
                    ));
                }
            }
            if event.close_window {
                closes.push(id);
                open = None;
            }
        }
        if closes.is_empty()
            && source.windows.get(window).is_some_and(|window| {
                matches!(window.purpose.as_str(), "reasoning" | "tool")
                    && !window.intentionally_left_open
            })
        {
            diagnostics.push(
                Diagnostic::event(
                    "open_window",
                    "Reasoning/tool window remains open",
                    graph.names[*ids.last().unwrap()],
                    "close_window",
                )
                .warning(),
            );
        }
    }
    if total > 0 && agent <= (total - 1) / 16 {
        let message = format!(
            "Only {} of {} characters ({:.1}%) are agent-generated, below the 6.25% minimum (1/16)",
            grouped(agent),
            grouped(total),
            100.0 * agent as f64 / total as f64
        );
        let mut diagnostic = Diagnostic::general("low_agent_share", message).warning();
        diagnostic.path = vec![json!("events")];
        diagnostics.push(diagnostic);
    }
    tool_diagnostics(
        source,
        settings,
        &graph,
        &ordered,
        &calls,
        &responses,
        &mut diagnostics,
    );
    (graph, sort(diagnostics))
}

pub(crate) fn valid_weight(weight: f64) -> bool {
    weight.is_finite()
        && (0.0..=f32::MAX as f64).contains(&weight)
        && (weight == 0.0 || (weight as f32) > 0.0)
}

fn grouped(value: usize) -> String {
    let value = value.to_string();
    let mut output = String::new();
    for (index, char) in value.chars().enumerate() {
        if index != 0 && (value.len() - index).is_multiple_of(3) {
            output.push(',');
        }
        output.push(char);
    }
    output
}

fn sort(mut diagnostics: Vec<Diagnostic>) -> Vec<Diagnostic> {
    diagnostics.sort_by_cached_key(|d| (json!(d.path).to_string(), d.code));
    diagnostics
}

fn tool_diagnostics(
    source: &Source,
    settings: Option<&Settings>,
    graph: &Graph,
    ordered: &[usize],
    calls: &BTreeMap<&str, Vec<usize>>,
    responses: &BTreeMap<&str, Vec<usize>>,
    diagnostics: &mut Vec<Diagnostic>,
) {
    let event = |id: usize| &source.events[graph.names[id]];
    let mut submitted = BTreeMap::new();
    for (&call, ids) in calls {
        let ordered_call = ids.windows(2).all(|pair| graph.ancestor(pair[0], pair[1]));
        let first = event(ids[0]);
        let (mut finished, mut cancelled) = (false, false);
        for (position, &id) in ids.iter().enumerate() {
            let current = event(id);
            let name = graph.names[id];
            let mut emit = |code, message, field| {
                diagnostics.push(Diagnostic::event(code, message, name, field))
            };
            if current.tool != first.tool || current.window != first.window {
                emit(
                    "inconsistent_call",
                    "Fragments must share tool and request window",
                    "call_id",
                );
            }
            if current.insert_tool_begin != (position == 0) {
                emit(
                    "invalid_tool_begin",
                    "Only the first fragment must insert tool begin",
                    "insert_tool_begin",
                );
            }
            if finished || cancelled {
                emit(
                    "call_already_finished",
                    "Cannot append to a submitted or cancelled request",
                    "call_id",
                );
            }
            if let (Some(settings), Some(tool)) = (settings, current.tool.as_deref()) {
                if !settings.tools.contains_key(tool) {
                    diagnostics.push(
                        Diagnostic::event(
                            "unknown_tool",
                            format!("Unknown tool: {tool}"),
                            name,
                            "tool",
                        )
                        .warning(),
                    );
                }
            }
            if current.insert_tool_end && !finished && !cancelled {
                finished = true;
                if ordered_call {
                    submitted.insert(call, id);
                }
                if let (Some(settings), Some(tool)) = (settings, current.tool.as_deref()) {
                    if let Some(definition) = settings.tools.get(tool) {
                        let fragments = ids[..=position]
                            .iter()
                            .map(|&id| event(id).content.text())
                            .collect::<Option<String>>();
                        if let Some(message) = schema_warning(fragments, &definition.input_schema) {
                            diagnostics.push(
                                Diagnostic::event("tool_input_schema", message, name, "content")
                                    .warning(),
                            );
                        }
                    }
                }
            }
            if current.close_window && !finished {
                cancelled = true;
            }
        }
    }
    for (&call, ids) in responses {
        let first = event(ids[0]);
        let mut ended = false;
        for (position, &id) in ids.iter().enumerate() {
            let current = event(id);
            let mut emit = |code, message, field| {
                diagnostics.push(Diagnostic::event(code, message, graph.names[id], field))
            };
            if current.insert_response_begin != (position == 0) {
                emit(
                    "invalid_response_begin",
                    "Only the first response fragment must insert response begin",
                    "insert_response_begin",
                );
            }
            if ended {
                emit(
                    "response_already_finished",
                    "Cannot append after response end",
                    "call_id",
                );
            }
            ended |= current.insert_response_end;
            if current.window != first.window {
                emit(
                    "inconsistent_response",
                    "Response fragments must share a window",
                    "window",
                );
            }
            if !submitted
                .get(call)
                .is_some_and(|&submission| graph.ancestor(submission, id))
            {
                emit(
                    "unsubmitted_call",
                    "Response requires an ancestor submitted request",
                    "call_id",
                );
            }
            if let Some(configured) = settings.and_then(|settings| {
                let tool = event(*calls.get(call)?.first()?).tool.as_ref()?;
                settings.tools.get(tool)?.response_window.as_ref()
            }) {
                if &current.window != configured {
                    emit(
                        "response_window",
                        "Response does not target the configured window",
                        "window",
                    );
                }
            }
        }
    }
    for &id in ordered {
        let current = event(id);
        if current.kind != Kind::ChatMessage {
            continue;
        }
        let Some(call) = current.call_id.as_deref() else {
            continue;
        };
        let mut emit = |code, message, field| {
            diagnostics.push(Diagnostic::event(code, message, graph.names[id], field))
        };
        if !submitted
            .get(call)
            .is_some_and(|&submission| graph.ancestor(submission, id))
        {
            emit(
                "unsubmitted_call",
                "Chat echo requires an ancestor submitted request",
                "call_id",
            );
        } else if !calls[call].iter().all(|&id| event(id).echo_chat) {
            emit(
                "chat_echo_disabled",
                "Linked chat echo is disabled for this call",
                "call_id",
            );
        } else if event(*calls[call].last().unwrap()).tool.as_deref() == Some("chat") {
            let fragments = calls[call]
                .iter()
                .map(|&id| match &event(id).content {
                    Content::String(text) => Some(text.as_str()),
                    Content::Pieces(_) => None,
                })
                .collect::<Option<String>>();
            if let Some(arguments) =
                fragments.and_then(|body| serde_json::from_str::<Value>(&body).ok())
            {
                if arguments.is_object()
                    && !matches!(&current.content, Content::String(text)
                    if arguments.get("content").and_then(Value::as_str) == Some(text))
                {
                    emit(
                        "chat_echo_mismatch",
                        "Chat echo differs from submitted content",
                        "content",
                    );
                }
            }
        }
    }
}

fn schema_warning(fragments: Option<String>, schema: &Value) -> Option<String> {
    let Some(fragments) = fragments else {
        return Some("Arguments contain logical references; schema check deferred".into());
    };
    let result = (|| -> std::result::Result<(), String> {
        let arguments: Value =
            serde_json::from_str(&fragments).map_err(|error| error.to_string())?;
        let validator = jsonschema::draft202012::new(schema).map_err(|error| error.to_string())?;
        validator
            .validate(&arguments)
            .map_err(|error| error.to_string())?;
        Ok(())
    })();
    result.err().map(|error| {
        format!(
            "Submitted arguments could not be validated: {}",
            error.chars().take(1000).collect::<String>()
        )
    })
}
