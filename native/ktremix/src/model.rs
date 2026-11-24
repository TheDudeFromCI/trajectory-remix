use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use uuid::Uuid;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Source {
    pub format: String,
    pub id: Uuid,
    #[serde(default)]
    pub defaults: Defaults,
    #[serde(default)]
    pub windows: BTreeMap<String, Window>,
    #[serde(default)]
    pub events: BTreeMap<String, Event>,
    pub metadata: Option<BTreeMap<String, Value>>,
}

impl Source {
    pub fn intrinsic(&self) -> Value {
        let events = self
            .events
            .iter()
            .map(|(id, event)| (id.clone(), event.canonical()))
            .collect::<serde_json::Map<String, Value>>();
        json!({
            "format": self.format, "id": self.id, "defaults": self.defaults,
            "windows": self.windows, "events": events,
        })
    }
}

fn is_false(value: &bool) -> bool {
    !value
}

#[derive(Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Defaults {
    pub noop_loss_weight: f64,
}

impl Default for Defaults {
    fn default() -> Self {
        Self {
            noop_loss_weight: 0.1,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Window {
    pub purpose: String,
    pub agent_writable: bool,
    pub reasoning_effort: Option<String>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub intentionally_left_open: bool,
}

#[derive(Deserialize, Serialize, PartialEq)]
#[serde(untagged)]
pub(crate) enum Content {
    String(String),
    Pieces(Vec<Piece>),
}

impl Content {
    pub fn text(&self) -> Option<String> {
        match self {
            Self::String(text) => Some(text.clone()),
            Self::Pieces(pieces) => pieces
                .iter()
                .map(|p| match p {
                    Piece::Text { text } => Some(text.as_str()),
                    Piece::WindowRef { .. } => None,
                })
                .collect(),
        }
    }

    pub fn is_empty(&self) -> bool {
        match self {
            Self::String(text) => text.is_empty(),
            Self::Pieces(pieces) => pieces.is_empty(),
        }
    }
}

#[derive(Deserialize, Serialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Piece {
    Text { text: String },
    WindowRef { window: String },
}

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Supervision {
    pub masked: bool,
    pub weight: f64,
}

impl Default for Supervision {
    fn default() -> Self {
        Self {
            masked: false,
            weight: 1.0,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Mode {
    Instant,
    Streamed,
}

#[derive(Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Delivery {
    pub mode: Mode,
    pub chain_immediately: bool,
}

impl Default for Delivery {
    fn default() -> Self {
        Self {
            mode: Mode::Instant,
            chain_immediately: true,
        }
    }
}

#[derive(Deserialize, Serialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Kind {
    AgentText,
    ExternalText,
    ToolSchema,
    ToolCall,
    ToolResponse,
    ChatMessage,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Chat {
    pub speaker: String,
}

fn yes() -> bool {
    true
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Event {
    pub kind: Kind,
    pub window: String,
    pub content: Content,
    #[serde(default)]
    pub after: Vec<String>,
    #[serde(default)]
    pub close_window: bool,
    pub supervision: Option<Supervision>,
    #[serde(default)]
    pub delivery: Delivery,
    #[serde(default)]
    pub replace_body: bool,
    pub call_id: Option<String>,
    pub tool: Option<String>,
    pub chat: Option<Chat>,
    #[serde(default = "yes")]
    pub insert_tool_begin: bool,
    #[serde(default = "yes")]
    pub insert_tool_end: bool,
    #[serde(default = "yes")]
    pub insert_response_begin: bool,
    #[serde(default = "yes")]
    pub insert_response_end: bool,
    #[serde(default = "yes")]
    pub echo_chat: bool,
    #[serde(default)]
    pub extra_tools: Vec<String>,
}

impl Event {
    fn canonical(&self) -> Value {
        let mut event = json!({
            "kind": self.kind, "window": self.window, "content": self.content,
            "after": self.after, "close_window": self.close_window, "supervision": self.supervision(),
        });
        match self.kind {
            Kind::AgentText => {}
            Kind::ExternalText | Kind::ToolSchema => {
                event["delivery"] = json!(self.delivery);
                event["replace_body"] = json!(self.replace_body);
                if self.kind == Kind::ToolSchema {
                    event["extra_tools"] = json!(self.extra_tools);
                }
            }
            Kind::ToolCall => {
                event["call_id"] = json!(self.call_id);
                event["tool"] = json!(self.tool);
                event["insert_tool_begin"] = json!(self.insert_tool_begin);
                event["insert_tool_end"] = json!(self.insert_tool_end);
                event["echo_chat"] = json!(self.echo_chat);
            }
            Kind::ToolResponse => {
                event["call_id"] = json!(self.call_id);
                event["delivery"] = json!(self.delivery);
                event["replace_body"] = json!(self.replace_body);
                event["insert_response_begin"] = json!(self.insert_response_begin);
                event["insert_response_end"] = json!(self.insert_response_end);
            }
            Kind::ChatMessage => {
                event["chat"] = json!(self.chat);
                event["delivery"] = json!(self.delivery);
                event["call_id"] = json!(self.call_id);
            }
        }
        event
    }

    fn supervision(&self) -> Supervision {
        self.supervision.unwrap_or(Supervision {
            masked: !self.model(),
            weight: 1.0,
        })
    }

    pub fn model(&self) -> bool {
        matches!(self.kind, Kind::AgentText | Kind::ToolCall)
    }

    pub fn weight(&self) -> f64 {
        let supervision = self.supervision();
        if supervision.masked {
            0.0
        } else {
            supervision.weight
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Token {
    pub id: Option<u32>,
    pub control: Option<&'static str>,
    pub header: bool,
    pub reference: Option<(String, usize)>,
}

impl Token {
    pub(crate) fn literal(id: u32) -> Self {
        Self {
            id: Some(id),
            control: None,
            header: false,
            reference: None,
        }
    }

    pub(crate) fn control(id: u32, name: &'static str, header: bool) -> Self {
        Self {
            id: Some(id),
            control: Some(name),
            header,
            reference: None,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct Emission {
    pub event: Option<String>,
    pub window: String,
    pub tokens: Vec<Token>,
    pub model: bool,
    pub weight: f64,
    pub replace: bool,
    pub opening: bool,
}

#[derive(Debug, Serialize)]
pub struct Frame {
    pub tick: u64,
    pub inputs: Vec<Emission>,
    pub outputs: Vec<Emission>,
}
