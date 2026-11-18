"""Versioned intrinsic source and project DTOs. No runtime/tokenizer imports."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

import jsonschema
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_serializer,
    model_validator,
)

from . import prompt_defaults

Identifier = Annotated[str, Field(min_length=1)]
Weight = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


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
    intentionally_left_open: bool = False

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        result = handler(self)
        # Preserve pre-acknowledgment source hashes/review identities by default.
        if not self.intentionally_left_open:
            result.pop("intentionally_left_open", None)
        return result


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


GraphEvent = Annotated[
    AgentText | ExternalText | ToolCall | ToolResponse | ChatMessage, Field(discriminator="kind")
]


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
REQUIRED_CONTROLS = frozenset(
    {
        "bos",
        "eos",
        "noop",
        "ctx_begin",
        "ctx_end",
        "tool_begin",
        "tool_end",
        "response_begin",
        "response_end",
        "msg_begin",
        "msg_end",
        "window_ref",
    }
)


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
            if (
                isinstance(token, dict)
                and type(token.get("id")) is int
                and token.get("special") is True
                and isinstance(token.get("content"), str)
                and token["content"]
            ):
                dedicated.add(token["id"])
        absent = set(self.special_tokens.values()) - dedicated
        if absent:
            raise ValueError(f"Controls absent from dedicated tokenizer tokens: {sorted(absent)}")
        return self

    @property
    def fingerprint(self):
        return digest(self.model_dump(mode="json"))


RESERVED_BODY_FIELDS = frozenset(
    {
        "model",
        "messages",
        "stream",
        "max_tokens",
        "max_completion_tokens",
        "response_format",
        "session_id",
        "temperature",
        "top_p",
        "presence_penalty",
        "reasoning_effort",
    }
)


class Endpoint(DTO):
    url: Annotated[str, Field(pattern=r"^https?://[^/\s]+(?:/[^\s]*)?$")]
    model: Identifier
    concurrency: Annotated[int, Field(ge=1)] = 1
    retries: Annotated[int, Field(ge=0)] = 2
    timeout_seconds: Annotated[float, Field(gt=0)] = 120.0
    # When every retry fails the endpoint pauses. If set, it reconnects by itself after
    # this many seconds and tries again; None leaves it paused until a manual Resume.
    reconnect_seconds: Annotated[float, Field(gt=0)] | None = None
    # Reasoning level sent as reasoning_effort, such as "none" (reasoning off), "low" or
    # "high"; None omits
    # the field so the model uses its own default.
    # Any non-empty string is accepted, since some models use their own level names.
    reasoning: Identifier | None = None
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    top_p: Annotated[float, Field(ge=0, le=1)] | None = None
    presence_penalty: Annotated[float, Field(ge=-2, le=2)] | None = None

    context_window_tokens: Annotated[int, Field(ge=1024)] = 65536
    max_output_tokens: Annotated[int, Field(ge=1)] = 8192
    # The context guard estimates input tokens as UTF-8 bytes / bytes_per_token. 1.0 can
    # never undercount; English and compact JSON average about 3.5 bytes per token, so
    # 3.0 keeps a margin while letting a model's real window be used.
    bytes_per_token: Annotated[float, Field(ge=1, le=6)] = 1.0
    # Send the trajectory ID as session_id so routers such as OpenRouter keep one
    # trajectory on one provider, where its prompt prefix stays cached.
    sticky_sessions: bool = False
    # Extra request-body fields for this provider, such as OpenRouter's reasoning or
    # provider routing options. Fields the transport controls cannot be replaced.
    extra_body: dict[str, JsonValue] = Field(default_factory=dict)
    # How replies are kept to the JSON schema. "auto" uses native strict JSON schema
    # where possible. "json_object" always uses JSON mode with the schema in the prompt.
    # "prompt" sends no response_format at all, for servers that support neither.
    output_mode: Literal["auto", "json_object", "prompt"] = "auto"

    @model_validator(mode="after")
    def context_budget(self):
        if self.max_output_tokens + 512 >= self.context_window_tokens:
            raise ValueError("Reserve input space beyond output tokens and framing")
        reserved = set(self.extra_body) & RESERVED_BODY_FIELDS
        if reserved:
            raise ValueError("extra_body cannot set " + ", ".join(sorted(reserved)))
        return self


class SplitRatios(DTO):
    train: Weight = 0.8
    development: Weight = 0.1
    test: Weight = 0.1

    @model_validator(mode="after")
    def total(self):
        if abs(self.train + self.development + self.test - 1) > 1e-9:
            raise ValueError("Split ratios must sum to one")
        return self


class Criterion(DTO):
    text: Identifier
    allow_not_applicable: bool = False


def default_checklists():
    return {
        "core": {
            key: Criterion(text=text)
            for key, text in {
                "objective_alignment": "The episode meaningfully exercises its objective; numeric targets are advisory.",
                "causal_evidence": "Every decision uses only evidence visible at its causal frontier.",
                "actor_and_supervision": "Sources, masks, chat delivery and tool framing match actor ownership.",
                "world_consistency": "Observations, identities and authored tool outcomes are consistent.",
                "coherent_reasoning": "Supervised reasoning follows evidence; distinguish intentional masked mistakes.",
                "truthful_episode_outcome": "Progress, blocking and completion claims match observed evidence.",
                "coordinator_role": "Kidra coordinates specialized work rather than personally performing substantial implementation or research, and delegation is proportional to task size.",
                "authorization_respected": "Consequential actions occur only within the user's stated authority or after an observed approval; otherwise Kidra requests approval or escalates.",
            }.items()
        },
        "critical": {
            # Many episodes contain no automation or infrastructure decision.
            key: Criterion(text=text, allow_not_applicable=key == "strategic_investment_judgment")
            for key, text in {
                "decision_quality": "Decisions consider relevant constraints, alternatives and proportional delegation.",
                "long_horizon_continuity": "Ownership and retained context remain coherent across interruptions and participants.",
                "exploration_and_recovery": "Critique and recovery respond to actual evidence without hindsight.",
                "training_value": "The episode teaches useful behavior without padded boilerplate.",
                "delegation_discipline": "No unchanged failed task is delegated again, agents are not created recursively or excessively, planning does not go unbounded, and visible budgets are respected.",
                "strategic_investment_judgment": "Automation or infrastructure decisions weigh expected reuse, savings, build and maintenance cost and uncertainty using visible evidence, and are revisited when circumstances change.",
                "execution_progress": "When execution is available the episode advances real work, not only plans.",
            }.items()
        },
    }


class GenerationPrompts(DTO):
    """The project's generation prompts; Load default restores the packaged ones."""

    actor: str = Field(default_factory=prompt_defaults.actor, min_length=1)
    draft: str = Field(default_factory=prompt_defaults.draft, min_length=1)
    assembly: str = Field(default_factory=prompt_defaults.assembly, min_length=1)


class GenerationOptions(DTO):
    @model_validator(mode="before")
    @classmethod
    def drop_retired_method(cls, data):
        # Projects saved while the step-by-step Flow existed carry a method field;
        # Draft and Assembly is now the only way to generate.
        if isinstance(data, dict) and "method" in data:
            data = {k: v for k, v in data.items() if k != "method"}
        return data

    # Runs only: every trajectory finishes generation (Draft and Assembly) before any review
    # starts; the job then pauses so models can be swapped, and Resume starts the reviews.
    phase_barrier: bool = False
    fast: bool = True
    critical: bool = True
    repair: bool = True
    format_retries: Annotated[int, Field(ge=0, le=5)] = 2
    # An explicit byte budget for the continuing repair transcript, not a token
    # estimate or a spending limit. Provider context exhaustion also terminates it.
    repair_context_bytes: Annotated[int, Field(ge=256)] = 65536
    # A spending guard: a repair session that outgrows repair_context_bytes starts a
    # fresh round on the current graph, up to this many rounds in all.
    repair_rounds: Annotated[int, Field(ge=1, le=10)] = 3


class ProjectSettings(DTO):
    @model_validator(mode="before")
    @classmethod
    def migrate_generator_role(cls, data):
        # Projects saved while a Generator endpoint role existed: Draft replaces it.
        roles = data.get("endpoint_roles") if isinstance(data, dict) else None
        if isinstance(roles, dict) and "generator" in roles:
            roles = {k: v for k, v in roles.items() if k != "generator"}
            roles.setdefault("draft", data["endpoint_roles"]["generator"])
            data = {**data, "endpoint_roles": roles}
        return data

    format: Literal["kidra-graph-settings/1"] = "kidra-graph-settings/1"
    tools: dict[Identifier, ToolDefinition] = Field(
        default_factory=lambda: {
            "chat": ToolDefinition(
                name="Chat",
                input_schema={
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                    "additionalProperties": False,
                },
            )
        }
    )
    reasoning_levels: list[Identifier] = Field(default_factory=lambda: ["Low", "Medium", "High"])
    tokenizer_profiles: dict[Identifier, TokenizerProfile] = Field(default_factory=dict)
    endpoints: dict[Identifier, Endpoint] = Field(default_factory=dict)
    # Assembly is optional: it falls back to Draft.
    endpoint_roles: dict[Literal["draft", "assembly", "fast", "critical"], Identifier] = Field(
        default_factory=dict
    )
    checklists: dict[Literal["core", "critical"], VersionedReference] = Field(default_factory=dict)
    review_checklists: dict[Literal["core", "critical"], dict[Identifier, Criterion]] = Field(
        default_factory=default_checklists
    )
    generation: GenerationOptions = Field(default_factory=GenerationOptions)
    prompts: GenerationPrompts = Field(default_factory=GenerationPrompts)
    matrix: VersionedReference | None = None
    metric_registry: VersionedReference | None = None
    sampler_configuration: dict[str, JsonValue] | None = None
    split_ratios: SplitRatios = Field(default_factory=SplitRatios)
    log_cache_mib: Annotated[int, Field(ge=0)] | None = 1024

    @model_validator(mode="after")
    def references(self):
        if self.sampler_configuration is not None:
            from .sampling import configuration

            c = self.sampler_configuration
            if "matrix" not in c:
                raise ValueError("Sampler configuration requires matrix")
            c = configuration(c["matrix"], c.get("metric_options"))
            self.sampler_configuration = c
            self.matrix = VersionedReference(
                id=c["matrix"]["matrix_id"],
                version=c["matrix"]["schema_version"],
                digest=c["matrix_digest"],
                uri="settings:sampler_configuration.matrix",
            )
            self.metric_registry = VersionedReference(
                id="sampler-metrics",
                version="1",
                digest=c["registry_digest"],
                uri="settings:sampler_configuration.definitions",
            )
        if len(self.reasoning_levels) != len(set(self.reasoning_levels)):
            raise ValueError("Reasoning levels must be unique")
        for role, endpoint in self.endpoint_roles.items():
            if endpoint not in self.endpoints:
                raise ValueError(f"Unknown endpoint for {role}: {endpoint}")
        return self
