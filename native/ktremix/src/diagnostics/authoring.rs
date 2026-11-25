//! Causal tool visibility and authoring checks on the compiler's graph order.

use super::{Diagnostic, Graph};
use crate::model::{Content, Event, Kind, Piece, Source};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub(super) fn has_content(content: &Content) -> bool {
    match content {
        Content::String(text) => !text.trim().is_empty(),
        Content::Pieces(pieces) => pieces.iter().any(|piece| match piece {
            Piece::Text { text } => !text.trim().is_empty(),
            Piece::WindowRef { .. } => true,
        }),
    }
}

pub(super) fn check_evidence(
    source: &Source,
    graph: &Graph<'_>,
    ordered: &[usize],
    diagnostics: &mut Vec<Diagnostic>,
) {
    let event = |id| &source.events[graph.names[id]];
    let mut invalidators: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for &id in ordered {
        let current = event(id);
        if current.close_window || current.replace_body {
            invalidators.entry(&current.window).or_default().push(id);
        }
    }
    let indices: BTreeMap<_, _> = graph
        .names
        .iter()
        .enumerate()
        .map(|(id, &name)| (name, id))
        .collect();
    for &consumer in ordered {
        let current = event(consumer);
        if !current.model() || (current.kind == Kind::AgentText && !has_content(&current.content)) {
            continue;
        }
        for (ordinal, dependency) in current.after.iter().enumerate() {
            let Some(&evidence) = indices.get(dependency.as_str()) else {
                continue;
            };
            let result = event(evidence);
            if result.kind != Kind::ToolResponse || !has_content(&result.content) {
                continue;
            }
            let blocker = if result.close_window {
                Some((evidence, "closes its own window"))
            } else {
                invalidators
                    .get(result.window.as_str())
                    .into_iter()
                    .flatten()
                    .copied()
                    .find(|&id| {
                        id != consumer
                            && graph.ancestor(evidence, id)
                            && graph.ancestor(id, consumer)
                    })
                    .map(|id| {
                        let reason = if event(id).close_window {
                            "closes the window"
                        } else {
                            "replaces its body"
                        };
                        (id, reason)
                    })
            };
            if let Some((hidden_by, reason)) = blocker {
                let hidden_by = graph.names[hidden_by];
                let mut diagnostic = Diagnostic::event(
                    "tool_result_unavailable",
                    format!("Tool response {dependency} is no longer visible here. Event {hidden_by} {reason}. If this event uses the result, keep it visible or depend on a visible copy."),
                    graph.names[consumer],
                    "after",
                ).related(dependency);
                if hidden_by != dependency {
                    diagnostic = diagnostic.related(hidden_by);
                }
                diagnostic.path.push(json!(ordinal));
                diagnostics.push(diagnostic);
            }
        }
    }
}

pub(super) fn check_window(
    source: &Source,
    graph: &Graph<'_>,
    window: &str,
    ids: &[usize],
    ordered: bool,
    diagnostics: &mut Vec<Diagnostic>,
) {
    let event = |id| &source.events[graph.names[id]];
    let purpose = source
        .windows
        .get(window)
        .map(|window| window.purpose.as_str());
    let mut first_calls = Vec::new();
    let mut calls = BTreeSet::new();
    let mut has_result = false;
    for &id in ids {
        let current = event(id);
        if current.kind == Kind::ToolCall && calls.insert(current.call_id.as_deref()) {
            first_calls.push(graph.names[id].to_owned());
        }
        has_result |= current.kind == Kind::ToolResponse;
    }
    if first_calls.len() > 1 {
        let mut diagnostic = Diagnostic::general(
            "multiple_tool_calls_in_window",
            format!(
                "Window {window} contains {} distinct tool calls. Give each call its own window.",
                first_calls.len()
            ),
        );
        diagnostic.path = vec![json!("windows"), json!(window)];
        diagnostic.related_events = first_calls;
        diagnostics.push(diagnostic);
    }
    if !calls.is_empty() || has_result || purpose == Some("tool") {
        for &id in ids {
            let current = event(id);
            if current.kind == Kind::AgentText && has_content(&current.content) {
                diagnostics.push(Diagnostic::event(
                    "reasoning_in_tool_window",
                    "Move reasoning to a dedicated window and preserve its causal dependencies.",
                    graph.names[id],
                    "window",
                ));
            }
        }
    }
    if purpose == Some("tools") && ordered && !event(ids[0]).after.is_empty() {
        let mut diagnostic = Diagnostic::event(
            "tools_window_has_dependencies",
            "Open the tools window at the start by giving its first event an empty after list.",
            graph.names[ids[0]],
            "after",
        );
        diagnostic.related_events = event(ids[0]).after.clone();
        diagnostics.push(diagnostic);
    }
}

pub(super) struct Listing<'a> {
    id: usize,
    event: &'a Event,
    names: BTreeSet<String>,
}

pub(super) fn presentations<'a>(
    source: &'a Source,
    graph: &Graph<'_>,
    ordered: &[usize],
) -> Vec<Listing<'a>> {
    ordered
        .iter()
        .filter_map(|&id| {
            let event = &source.events[graph.names[id]];
            if source.windows.get(&event.window)?.purpose != "tools" {
                return None;
            }
            let names = if matches!(event.kind, Kind::ToolSchema | Kind::ExternalText) {
                event
                    .content
                    .text()
                    .into_iter()
                    .flat_map(|text| {
                        text.lines()
                            .filter_map(|line| {
                                let entry: Value = serde_json::from_str(line).ok()?;
                                let name = entry["name"].as_str()?;
                                let parameters = &entry["parameters"];
                                (parameters.is_object() || parameters.is_boolean())
                                    .then(|| name.to_owned())
                            })
                            .collect::<Vec<_>>()
                    })
                    .collect()
            } else {
                BTreeSet::new()
            };
            Some(Listing { id, event, names })
        })
        .collect()
}

pub(super) fn check_tool_visibility(
    tool: &str,
    id: usize,
    graph: &Graph<'_>,
    listings: &[Listing<'_>],
    missing_from_catalog: bool,
    diagnostics: &mut Vec<Diagnostic>,
) {
    let mut windows: BTreeMap<&str, Vec<&Listing<'_>>> = BTreeMap::new();
    for listing in listings
        .iter()
        .filter(|listing| graph.ancestor(listing.id, id))
    {
        let event = listing.event;
        let window = event.window.as_str();
        if event.close_window || event.replace_body {
            windows.remove(window);
        }
        if !listing.names.is_empty() && !event.close_window {
            windows.entry(window).or_default().push(listing);
        }
    }
    let visible: Vec<_> = windows.values().flatten().collect();
    if visible.is_empty() {
        diagnostics.push(Diagnostic::event(
            "missing_tool_listing",
            "Show a tool listing before this call and add it as a dependency",
            graph.names[id],
            "after",
        ));
    }
    if missing_from_catalog {
        diagnostics.push(Diagnostic::event(
            "unknown_tool",
            format!("Tool is absent from Project Settings: {tool}"),
            graph.names[id],
            "tool",
        ));
    } else if !visible.is_empty() && !visible.iter().any(|row| row.names.contains(tool)) {
        let mut diagnostic = Diagnostic::event(
            "unknown_tool",
            format!("Tool is absent from the visible listing: {tool}"),
            graph.names[id],
            "tool",
        );
        diagnostic.related_events = visible
            .iter()
            .map(|row| graph.names[row.id].to_owned())
            .collect();
        diagnostic.related_events.sort_unstable();
        diagnostics.push(diagnostic);
    }
}
