"""Versioned intrinsic source and project DTOs. No runtime/tokenizer imports."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1)]
Weight = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True,
                              revalidate_instances="always", allow_inf_nan=False)


class TextPiece(DTO):
    kind: Literal["text"]
    text: str


class WindowReference(DTO):
    kind: Literal["window_ref"]
    window: Identifier


Piece = Annotated[TextPiece | WindowReference, Field(discriminator="kind")]
Content = str | list[Piece]


class Supervision(DTO):
    masked: bool = False
    weight: Weight = 1.0


def external_supervision():
    return Supervision(masked=True)


class Delivery(DTO):
    mode: Literal["instant", "streamed"] = "instant"
    chain_immediately: bool = True


class InstantDelivery(Delivery):
    mode: Literal["instant"] = "instant"


class Window(DTO):
    purpose: Identifier
    agent_writable: bool
    reasoning_effort: Identifier | None = None


class Event(DTO):
    window: Identifier
    after: list[Identifier] = Field(default_factory=list)
    content: Content
    close_window: bool = False


class AgentText(Event):
    kind: Literal["agent_text"]
    supervision: Supervision = Field(default_factory=Supervision)


class ExternalText(Event):
    kind: Literal["external_text"]
    supervision: Supervision = Field(default_factory=external_supervision)
    delivery: Delivery = Field(default_factory=Delivery)
    replace_body: bool = False


class ToolCall(Event):
    kind: Literal["tool_call"]
    supervision: Supervision = Field(default_factory=Supervision)
    call_id: Identifier
    tool: Identifier
    insert_tool_begin: bool = True
    insert_tool_end: bool = True
    echo_chat: bool = True


class ToolResponse(Event):
    kind: Literal["tool_response"]
    supervision: Supervision = Field(default_factory=external_supervision)
    delivery: Delivery = Field(default_factory=Delivery)
    call_id: Identifier
    insert_response_begin: bool = True
    insert_response_end: bool = True
    replace_body: bool = False


class Chat(DTO):
    speaker: Identifier


class ChatMessage(Event):
    kind: Literal["chat_message"]
    supervision: Supervision = Field(default_factory=external_supervision)
    chat: Chat
    delivery: InstantDelivery = Field(default_factory=InstantDelivery)
    # Optional link for a chat tool's ordinary authored echo outcome.
    call_id: Identifier | None = None


GraphEvent = Annotated[AgentText | ExternalText | ToolCall | ToolResponse | ChatMessage,
                       Field(discriminator="kind")]


class Defaults(DTO):
    noop_loss_weight: Weight = 0.1


class GraphSource(DTO):
    format: Literal["kidra-trajectory-graph/1"]
    id: Annotated[str, Field(json_schema_extra={"format": "uuid"})]
    defaults: Defaults = Field(default_factory=Defaults)
    windows: dict[Identifier, Window] = Field(default_factory=dict)
    events: dict[Identifier, GraphEvent] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def trajectory_uuid(cls, value):
        return str(UUID(value))

    def to_dict(self):
        result = self.model_dump(mode="json", exclude={"metadata"})
        if "metadata" in self.model_fields_set:
            result["metadata"] = self.model_dump(mode="json")["metadata"]
        return result

    @property
    def intrinsic_hash(self):
        return digest(self.model_dump(mode="json", exclude={"metadata"}))


class VersionedReference(DTO):
    id: Identifier
    version: Identifier
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    uri: Identifier


class ToolDefinition(DTO):
    name: Identifier
    input_schema: dict[str, JsonValue] | bool = Field(default_factory=dict)
    response_window: Identifier | None = None

    @field_validator("input_schema")
    @classmethod
    def schema(cls, value):
        try:
            jsonschema.Draft202012Validator.check_schema(value)
        except jsonschema.SchemaError as error:
            raise ValueError(f"Invalid tool input schema: {error.message}") from error
        return value


# Logical names, never tokenizer-native spellings in source. A profile is usable
# only with dedicated IDs for all controls; creating a project needs no profile.
REQUIRED_CONTROLS = frozenset({"bos", "eos", "noop", "ctx_begin", "ctx_end",
                               "tool_begin", "tool_end", "response_begin",
                               "response_end", "msg_begin", "msg_end", "window_ref"})


class TokenizerProfile(DTO):
    name: Identifier
    definition: dict[str, JsonValue]
    special_tokens: dict[Identifier, Annotated[int, Field(ge=0)]]

    @model_validator(mode="after")
    def controls(self):
        missing = REQUIRED_CONTROLS - self.special_tokens.keys()
        if missing:
            raise ValueError(f"Missing required controls: {', '.join(sorted(missing))}")
        if len(set(self.special_tokens.values())) != len(self.special_tokens):
            raise ValueError("Controls require distinct dedicated token IDs")
        # Inspect the serialized dedicated-token table without constructing a
        # tokenizer or extending its vocabulary. Full encoding compatibility is
        # checked by the compiler when a profile is explicitly used.
        tokens = self.definition.get("added_tokens", [])
        if not isinstance(tokens, list):
            raise ValueError("Tokenizer definition requires an added_tokens array")
        dedicated = set()
        for token in tokens:
            if (isinstance(token, dict) and type(token.get("id")) is int
                    and token.get("special") is True and isinstance(token.get("content"), str)
                    and token["content"]):
                dedicated.add(token["id"])
        absent = set(self.special_tokens.values()) - dedicated
        if absent:
            raise ValueError(f"Controls absent from dedicated tokenizer tokens: {sorted(absent)}")
        return self

    @property
    def fingerprint(self):
        return digest(self.model_dump(mode="json"))


class Endpoint(DTO):
    url: Annotated[str, Field(pattern=r"^https?://[^/\s]+(?:/[^\s]*)?$")]
    model: Identifier
    concurrency: Annotated[int, Field(ge=1)] = 1
    retries: Annotated[int, Field(ge=0)] = 2


class SplitRatios(DTO):
    train: Weight = 0.8
    development: Weight = 0.1
    test: Weight = 0.1

    @model_validator(mode="after")
    def total(self):
        if abs(self.train + self.development + self.test - 1) > 1e-9:
            raise ValueError("Split ratios must sum to one")
        return self


class ProjectSettings(DTO):
    format: Literal["kidra-graph-settings/1"] = "kidra-graph-settings/1"
    tools: dict[Identifier, ToolDefinition] = Field(default_factory=dict)
    reasoning_levels: list[Identifier] = Field(default_factory=lambda: ["Low", "Medium", "High"])
    tokenizer_profiles: dict[Identifier, TokenizerProfile] = Field(default_factory=dict)
    endpoints: dict[Identifier, Endpoint] = Field(default_factory=dict)
    endpoint_roles: dict[Literal["generator", "fast", "critical"], Identifier] = Field(default_factory=dict)
    checklists: dict[Literal["core", "critical"], VersionedReference] = Field(default_factory=dict)
    matrix: VersionedReference | None = None
    metric_registry: VersionedReference | None = None
    split_ratios: SplitRatios = Field(default_factory=SplitRatios)
    log_cache_mib: Annotated[int, Field(ge=0)] | None = 1024

    @model_validator(mode="after")
    def references(self):
        if len(self.reasoning_levels) != len(set(self.reasoning_levels)):
            raise ValueError("Reasoning levels must be unique")
        for role, endpoint in self.endpoint_roles.items():
            if endpoint not in self.endpoints:
                raise ValueError(f"Unknown endpoint for {role}: {endpoint}")
        return self
