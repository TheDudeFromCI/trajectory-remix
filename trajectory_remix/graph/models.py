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

from . import prompt_defaults, token_limits

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


class ToolSchema(Event):
    """The tools the agent is shown: JSON lines of tool schemas, as masked external input.

    extra_tools names the lines that were added at random rather than chosen; the
    dashboard shows them grayed out. The compiler treats the event like external_text.
    """

    kind: Literal["tool_schema"]
    supervision: Supervision = Field(default_factory=external_supervision)
    delivery: Delivery = Field(default_factory=Delivery)
    replace_body: bool = False
    extra_tools: list[Identifier] = Field(default_factory=list)


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
    AgentText | ExternalText | ToolSchema | ToolCall | ToolResponse | ChatMessage,
    Field(discriminator="kind"),
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

    # Token limits; -1 (the default) disables one and leaves it to the provider. See
    # token_limits for how each is used.
    context_window_tokens: Annotated[int, Field(ge=token_limits.DISABLED)] = token_limits.DISABLED
    max_output_tokens: Annotated[int, Field(ge=token_limits.DISABLED)] = token_limits.DISABLED
    # The context guard estimates input tokens as UTF-8 bytes / bytes_per_token. 1.0 can
    # never undercount; English and compact JSON average about 3.5 bytes per token, so
    # 3.0 keeps a margin while letting a model's real window be used.
    bytes_per_token: Annotated[float, Field(ge=1, le=6)] = 3.0
    # Send the trajectory ID as session_id so routers such as OpenRouter keep one
    # trajectory on one provider, where its prompt prefix stays cached.
    sticky_sessions: bool = False
    # Extra request-body fields for this provider, such as OpenRouter's reasoning or
    # provider routing options. Fields the transport controls cannot be replaced.
    extra_body: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def drop_output_mode(cls, data):
        # output_mode chose a JSON response_format for Draft replies. Drafts are plain
        # text now and no request sends response_format, so saved settings drop it.
        if isinstance(data, dict) and "output_mode" in data:
            data = {k: v for k, v in data.items() if k != "output_mode"}
        return data

    @model_validator(mode="after")
    def context_budget(self):
        problem = token_limits.problem(self.context_window_tokens, self.max_output_tokens)
        if problem:
            raise ValueError(problem)
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


class SizeLabel(DTO):
    """A trajectory size class; the nearest total_tokens (log2) names a trajectory's size."""

    name: Identifier
    agent_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=1)]


def default_size_labels():
    from .size_labels import default_size_labels as defaults

    return [SizeLabel(**x) for x in defaults()]


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
    authoring: str = Field(default_factory=prompt_defaults.authoring, min_length=1)
    draft: str = Field(default_factory=prompt_defaults.draft, min_length=1)
    assembly: str = Field(default_factory=prompt_defaults.assembly, min_length=1)
    assistant: str = Field(default_factory=prompt_defaults.assistant, min_length=1)

    @field_validator("actor")
    @classmethod
    def upgrade_packaged_actor(cls, value):
        # A shipped agent prompt, possibly with a persona appended, gets the current one.
        for retired in prompt_defaults.retired_actors():
            if value.startswith(retired):
                return prompt_defaults.actor() + value[len(retired) :]
        return value

    @field_validator("authoring", "draft", "assembly", "assistant")
    @classmethod
    def upgrade_packaged_prompt(cls, value, info):
        # Only replace exact shipped defaults. Custom guidance stays with the project.
        previous = {
            "authoring": {
                "2f5c97260d020513efe645f4f77ea59b04e8cb26cdf70f4fada584bc59a789a9",
            },
            "draft": {
                "3f4b2567d48cfe2c6f3e36ee34e20f68923f0123f55687a01e360971d5fbca4a",
                "379f799bc52e67227994ec99bd671a194b2cabd11558d584e897551f2b0bd250",
                "81fc1038e09cb959a01f19554ee649fe5cd4b37759fd03f10c953b2253a3a424",
                # The default that asked for a JSON object, before plain-text Drafts.
                "e86e67f1e0d967affca80621f9194e329cc6b4be1aa9601a749225ac0b2df4db",
            },
            "assistant": {
                # The lead-in alone, before the reply instructions moved into the prompt.
                "ee85df4ba298caba9feec68c73c5dc42de11fb96bbf88ac093dd615ce4a79139",
            },
            "assembly": {
                "eb7363281b287f472a6197ecf16cb77cb9b42f9abe4e8dd6eea14ceaea4a5c3b",
                "325bbdc768a88d57609bd1cc38f2dec2b61632f395de754a096d3d35518dee51",
                "9cdbe536af2c43734bb288e2d14ced358643bde21a3106b8a8ba9eae81ed6d87",
                "5503627261f2d0c6e7541468d9150a6e3562e6cccd67fb3df9638001ab724058",
                "f5bda2ec207ce5c99a8e25205f127ab9a31c25bf64234e343d776e939dfee6c3",
                "063330936af9ac7e59136dc4c5cb9c26cfeda60c21d98c322c21bd04a5a8fa46",
            },
        }
        if digest(value) in previous[info.field_name]:
            return getattr(prompt_defaults, info.field_name)()
        return value


class GenerationOptions(DTO):
    @model_validator(mode="before")
    @classmethod
    def drop_retired_method(cls, data):
        # Projects saved while the step-by-step Flow existed carry a method field;
        # Draft and Assembly is now the only way to generate.
        # phase_barrier moved to the Generate trajectories panel (chosen per job).
        # Review now always repairs, so repair and repair_rounds are gone; the context
        # budget they shared belongs to the Assistant alone.
        if isinstance(data, dict):
            retired = {"method", "phase_barrier", "repair", "repair_rounds"}
            data = {k: v for k, v in data.items() if k not in retired}
            if "repair_context_bytes" in data:
                data.setdefault("assistant_context_bytes", data.pop("repair_context_bytes"))
        return data

    fast: bool = True
    critical: bool = True
    review_include_draft: bool = False
    scaling_experiment: bool = False
    scaling_section_tokens: Annotated[int, Field(ge=2000, le=16000)] = 4000
    max_authoring_exchanges: Annotated[int, Field(ge=1)] = 10
    format_retries: Annotated[int, Field(ge=0, le=5)] = 2
    # An explicit byte budget for an Assistant conversation, kept between sends; not a
    # token estimate. The endpoint's context limit also ends a session.
    assistant_context_bytes: Annotated[int, Field(ge=256)] = 65536


class ProjectSettings(DTO):
    @model_validator(mode="before")
    @classmethod
    def migrate_generator_role(cls, data):
        # Projects saved while a Generator endpoint role existed: Draft replaces it.
        roles = data.get("endpoint_roles") if isinstance(data, dict) else None
        if not isinstance(roles, dict):
            return data
        endpoints = data.get("endpoints", {})
        roles = {
            key: value
            for key, value in roles.items()
            if key not in ("fast", "critical")
            or (
                value is not None
                and (value != "none" or (isinstance(endpoints, dict) and "none" in endpoints))
            )
        }
        if "generator" in roles:
            old_role = roles.pop("generator")
            roles.setdefault("draft", old_role)
        return {**data, "endpoint_roles": roles}

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
    # Assembly is optional: it falls back to Draft. Assistant answers reviewer comments
    # ("Send to agent") and is separate from the four generation roles.
    endpoint_roles: dict[
        Literal["draft", "assembly", "fast", "critical", "assistant"], Identifier
    ] = Field(default_factory=dict)
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
    # Chance that each tool outside a tool_schema event's chosen tools is listed anyway (0 = never).
    tool_schema_extra_chance: Annotated[float, Field(ge=0, le=1)] = 0.5
    disabled_warnings: list[Identifier] = Field(default_factory=list)
    size_labels: list[SizeLabel] = Field(default_factory=default_size_labels)

    @field_validator("size_labels")
    @classmethod
    def unique_size_labels(cls, labels):
        names = [x.name for x in labels]
        if len(names) != len(set(names)):
            raise ValueError("Size label names must be unique")
        return sorted(labels, key=lambda x: x.total_tokens)

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
