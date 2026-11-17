"""The Selection a flow's select node asks for, with every field described.

In JSON mode the schema is written into the prompt, so these descriptions are the
selector's reference for the event envelope. Small models need them: without a field
list they invent names such as "type", "sender" or "timestamp". Only fields a selector may
set are offered; supervision, masks and recipe-only fields such as "when" are not.
Descriptions are kept short because every selector request carries them.
"""

from typing import Literal

from pydantic import Field

from .models import DTO, Delivery, Identifier, JsonValue

KINDS = (
    "agent_text: the agent's reasoning. tool_call: the agent calls a tool. tool_response:"
    " that call's result. chat_message: a person speaks. external_text: other input such as"
    " a document, ticket or report."
)


class NewWindow(DTO):
    purpose: Identifier = Field(
        description=(
            "chatroom: where people chat (needed for chat_message). reasoning: one step of"
            " the agent's work. notes: its lasting conclusions. Otherwise a short word such"
            " as document."
        )
    )
    agent_writable: bool = Field(
        description="true where the agent writes (reasoning, tool); false for chatroom."
    )
    reasoning_effort: Identifier | None = Field(
        default=None, description="Optional reasoning level for a reasoning window."
    )


class Speaker(DTO):
    speaker: Identifier = Field(description="Who speaks, for example the user's name.")


class Envelope(DTO):
    kind: Literal["agent_text", "external_text", "tool_call", "tool_response", "chat_message"] = (
        Field(description=KINDS)
    )
    window: Identifier = Field(description="An existing window ID, or one defined in windows.")
    after: list[Identifier] = Field(
        default_factory=list,
        description="IDs of committed events this follows; [] only for the first event.",
    )
    chat: Speaker | None = Field(default=None, description="chat_message only.")
    tool: Identifier | None = Field(default=None, description="tool_call only: an ID from tools.")
    call_id: Identifier | None = Field(
        default=None, description="tool_call and its tool_response: the same ID."
    )
    close_window: bool = Field(
        default=False, description="Close the window after this event; it cannot reopen."
    )
    replace_body: bool = Field(
        default=False, description="external_text or tool_response: replace earlier content."
    )
    delivery: Delivery | None = Field(
        default=None, description="Optional streamed delivery for external input."
    )
    echo_chat: bool = Field(
        default=True, description="chat tool_call: false stops automatic chatroom delivery."
    )


class SelectedTask(DTO):
    id: str = Field(min_length=1, description="A new, unique event ID.")
    envelope: Envelope
    instruction: str = Field(
        default="", description="External events: what to write. Ignored for agent events."
    )
    world: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="External events: private facts for their author. {} for agent events.",
    )
    # Called "arguments" internally; small models put the arguments themselves there.
    call_format: Literal["text", "json"] = Field(
        default="text",
        description=(
            'tool_call only, a setting: "json" if its author writes the call\'s arguments as'
            ' one JSON object, else "text". Never the arguments themselves.'
        ),
    )

    def as_event_task(self):
        """The internal task form; unset optional envelope fields are left out."""
        from .flow import EventTask

        data = self.model_dump(mode="json")
        data["arguments"] = data.pop("call_format")
        envelope = self.envelope.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        data["envelope"] = dict(envelope, kind=self.envelope.kind, window=self.envelope.window)
        return EventTask.model_validate(data)


class Selection(DTO):
    finished: bool = Field(description="true only to end the episode, with task null.")
    reason: str = Field(
        min_length=1, description="Notes: objectives shown by event ID, and what remains."
    )
    windows: dict[str, NewWindow] = Field(
        default_factory=dict, description="NEW windows by ID, or {}."
    )
    task: SelectedTask | None = Field(
        default=None, description="The next event, or null when finished."
    )


def _untitled(value):
    if isinstance(value, dict):
        return {key: _untitled(item) for key, item in value.items() if key != "title"}
    if isinstance(value, list):
        return [_untitled(item) for item in value]
    return value


def prompt_schema():
    """The Selection schema for requests: descriptions kept, generated titles removed."""
    return _untitled(Selection.model_json_schema())
