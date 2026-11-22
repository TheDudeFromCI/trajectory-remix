"""Provider and MCP tool contracts for editing one trajectory."""

from typing import Literal

from pydantic import Field

from .models import DTO, Content, Identifier, Weight

EventKind = Literal[
    "agent_text", "external_text", "tool_schema", "tool_call", "tool_response", "chat_message"
]


class WindowFields(DTO):
    purpose: Identifier | None = None
    agent_writable: bool | None = None
    reasoning_effort: Identifier | None = None
    intentionally_left_open: bool | None = None


class ConfigureWindow(WindowFields):
    window_id: Identifier


class EventID(DTO):
    event_id: Identifier


class WindowID(DTO):
    window_id: Identifier


class SupervisionFields(DTO):
    masked: bool | None = None
    weight: Weight | None = None


class DeliveryFields(DTO):
    mode: Literal["instant", "streamed"] | None = None
    chain_immediately: bool | None = None


class ChatFields(DTO):
    speaker: Identifier


class EventFields(EventID):
    window: Identifier | None = None
    kind: EventKind | None = None
    after: list[Identifier] | None = None
    content: Content | None = None
    close_window: bool | None = None
    supervision: SupervisionFields | None = None
    delivery: DeliveryFields | None = None
    replace_body: bool | None = None
    call_id: Identifier | None = None
    tool: Identifier | None = None
    insert_tool_begin: bool | None = None
    insert_tool_end: bool | None = None
    insert_response_begin: bool | None = None
    insert_response_end: bool | None = None
    echo_chat: bool | None = None
    chat: ChatFields | None = None
    extra_tools: list[Identifier] | None = None


class CreateEvent(EventFields):
    kind: EventKind
    window: Identifier
    content: Content
    window_properties: WindowFields | None = None


class PostChat(EventID):
    origin: Literal["user", "agent"]
    speaker: Identifier
    content: str
    window: Identifier = "chatroom"
    after: list[Identifier] = Field(default_factory=list)


class PostSchema(DTO):
    tool_ids: list[Identifier]


class ViewGraph(DTO):
    pass


class ViewDiagnostics(DTO):
    pass


class CausalClaim(DTO):
    consumer_event_id: Identifier
    evidence_event_ids: list[Identifier] = Field(min_length=1)


class Evidence(DTO):
    status: Literal["pass", "fail", "not_applicable"]
    evidence: str = Field(min_length=1)
    event_ids: list[Identifier]
    causal_claims: list[CausalClaim] | None = None


class Signoff(DTO):
    status: Literal["complete", "unfeasible"]
    summary: str = Field(min_length=1)
    items: dict[Identifier, Evidence] = Field(default_factory=dict)


TOOLS = {
    "create_event": (
        CreateEvent,
        "Create an event with a unique ID. Its window is created if needed. The after list names causal dependencies.",
    ),
    "update_event": (
        EventFields,
        "Edit an existing event. Omitted fields stay the same, including nested delivery and supervision fields. Supply null to clear a nullable field.",
    ),
    "delete_event": (
        EventID,
        "Delete one event. Dependencies from other events remain for you to fix.",
    ),
    "configure_window": (
        ConfigureWindow,
        "Configure a logical window, including before its first use. Omitted fields stay the same. Null clears reasoning effort.",
    ),
    "post_chat_message": (
        PostChat,
        "Deliver a user message or author a synthetic agent chat call and its linked delivery. The supplied event ID identifies the delivery.",
    ),
    "post_tool_schema": (
        PostSchema,
        "Replace the initial tools listing with these exact project tool IDs.",
    ),
    "view_event": (EventID, "Read an event and the IDs of its visible predecessors."),
    "view_window": (WindowID, "Read a window and the full bodies of its events."),
    "view_graph": (
        ViewGraph,
        "Read event IDs, kinds, windows and dependencies without event bodies.",
    ),
    "view_diagnostics": (
        ViewDiagnostics,
        "Run validation on the graph at this point in the tool batch. Returns current errors and enabled warnings.",
    ),
    "signoff": (
        Signoff,
        "Complete the pass with evidence for every requested criterion. Use unfeasible with a reason to end a failed attempt.",
    ),
}

READ_ONLY = frozenset({"view_event", "view_window", "view_graph", "view_diagnostics"})


def _provider_tool(name, parameters=None):
    return dict(
        type="function",
        function=dict(
            name=name,
            description=TOOLS[name][1],
            parameters=parameters or TOOLS[name][0].model_json_schema(),
        ),
    )


def provider_tools(*, signoff_parameters):
    """Every authoring tool, with signoff limited to the pass's criteria."""
    return [
        _provider_tool(name, signoff_parameters if name == "signoff" else None) for name in TOOLS
    ]


def editing_tools():
    """Every edit and read tool, without signoff, for sessions that end another way."""
    return [_provider_tool(name) for name in TOOLS if name != "signoff"]
