"""Provider configuration, exact window selection and deterministic sample building."""

from __future__ import annotations

import json
import os
import uuid
from urllib.parse import urlparse

import httpx

from .dataset import reject_constant
from .tools import (
    Sandbox,
)

TAGS = [
    "competent",
    "novice",
    "expert",
    "confused",
    "annoyed",
    "impatient",
    "polite",
    "terse",
    "verbose",
    "messaging",
    "software",
    "debugging",
    "planning",
    "research",
    "mathematics",
    "finance",
    "health",
    "creative",
    "roleplay",
    "scheduling",
    "multitask",
    "interruption",
    "correction",
    "followup",
    "ambiguous",
    "underspecified",
    "constrained",
    "tool-use",
    "memory",
    "retrieval",
    "delegation",
    "refusal",
    "adversarial",
    "long-running",
    "error-recovery",
    "verification",
    "time",
    "filesystem",
    "notes",
    "skills",
    "factual",
    "empathetic",
    "formal",
    "casual",
]


def default_config():
    return {
        "endpoints": [
            {
                "id": "generator",
                "base_url": "http://127.0.0.1:8080/v1",
                "model": "local-model",
                "api_key_env": "REMIX_GENERATOR_KEY",
                "temperature": 0.8,
                "max_tokens": 1024,
                "timeout": 120,
                "structured_output": "native",
            },
            {
                "id": "validator",
                "base_url": "http://127.0.0.1:8080/v1",
                "model": "local-model",
                "api_key_env": "REMIX_VALIDATOR_KEY",
                "temperature": 0.1,
                "max_tokens": 1024,
                "timeout": 120,
                "structured_output": "native",
            },
        ],
        "content_first": {"context_budget": 8192, "reserve_tokens": 256},
        "single_generation": {"max_parallel": 1, "max_attempts": 3, "api_retries": 2},
        "tasks": [],
        "validation": {"endpoint": "validator", "max_input_tokens": 6000},
        "tags": ["competent", "software", "tool-use"],
        "clock": {"seed": "2030-01-01T09:00:00+00:00", "tick_rate": 10},
        "max_tool_calls": 64,
    }


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Config must be an object")
    content = config.get("content_first", {})
    if not isinstance(content, dict):
        raise ValueError("content_first must be an object")
    for key, default, low in (("context_budget", 8192, 1), ("reserve_tokens", 256, 0)):
        if type(content.get(key, default)) is not int or not low <= content.get(key, default) <= 1_000_000:
            raise ValueError(f"content_first.{key} must be an integer in [{low}, 1000000]")
    validation = config.get('validation', {})
    if not isinstance(validation, dict) or not isinstance(validation.get('endpoint', 'validator'), str) or not validation.get('endpoint', 'validator'):
        raise ValueError('validation.endpoint must be a nonempty endpoint ID')
    if type(validation.get('max_input_tokens', 6000)) is not int or not 512 <= validation.get('max_input_tokens', 6000) <= 100000:
        raise ValueError('validation.max_input_tokens must be between 512 and 100000')
    from .guidance import validate_guidance
    validate_guidance(config.get("review_guidance", {}))
    single = config.get("single_generation", {})
    if not isinstance(single, dict):
        raise ValueError("single_generation must be an object")
    if type(single.get("api_retries", 2)) is not int or not 0 <= single.get("api_retries", 2) <= 20:
        raise ValueError("api_retries must be an integer from 0 to 20")
    for key, default, maximum in (("max_parallel", 1, 128), ("max_attempts", 3, 100)):
        if (
            type(single.get(key, default)) is not int
            or not 1 <= single.get(key, default) <= maximum
        ):
            raise ValueError(f"{key} must be an integer from 1 to {maximum}")
    if not isinstance(config.get("automatic", {}), dict) or not isinstance(
        config.get("clock", {}), dict
    ):
        raise ValueError("automatic and clock must be objects")
    if not isinstance(config.get("tags", []), list) or any(
        not isinstance(tag, str) for tag in config.get("tags", [])
    ):
        raise ValueError("tags must be an array of strings")
    if not isinstance(config.get("endpoints"), list) or not config["endpoints"]:
        raise ValueError("At least one endpoint is required")
    seen = set()
    for ep in config["endpoints"]:
        if (
            not isinstance(ep, dict)
            or not isinstance(ep.get("id"), str)
            or not ep["id"]
            or ep["id"] in seen
        ):
            raise ValueError("Endpoint IDs must be nonempty and unique")
        seen.add(ep["id"])
        if ep.get("completion_limit_parameter", "max_tokens") not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("completion_limit_parameter must be max_tokens or max_completion_tokens")
        if "inference_tokenizer" in ep and not isinstance(ep["inference_tokenizer"], str):
            raise ValueError("inference_tokenizer must be text (blank disables token budget checks)")
        if type(ep.get('stream_usage', True)) is not bool:
            raise ValueError('stream_usage must be boolean')
        if ep.get('structured_output', 'native') not in ('native', 'prompted'):
            raise ValueError('structured_output must be native or prompted')
        context_size = ep.get("context_size", 0)
        if type(context_size) is not int or not 0 <= context_size <= 1_000_000_000:
            raise ValueError("Endpoint context_size must be an integer between 0 (unknown) and 1000000000")
        for name in ("base_url", "api_key", "api_key_env"):
            if not isinstance(ep.get(name, ""), str):
                raise ValueError(f"Endpoint {name} must be text")
        url = urlparse(ep.get("base_url", ""))
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Endpoint base_url must be an HTTP(S) URL without credentials, query or fragment"
            )
        if not isinstance(ep.get("model"), str) or not ep["model"]:
            raise ValueError("Endpoint model is required")
        for field, low, high, default in [
            ("temperature", 0, 2, 0.7),
            ("max_tokens", 1, 65536, 1024),
            ("timeout", 1, 600, 120),
        ]:
            value = ep.get(field, default)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f"Endpoint {field} must be between {low} and {high}")
    if not isinstance(config.get("tasks"), list) or len(config["tasks"]) > 100:
        raise ValueError("tasks must be an array of at most 100 tasks")
    task_ids = set()
    for task in config["tasks"]:
        if (
            not isinstance(task, dict)
            or not isinstance(task.get("id"), str)
            or not task["id"]
            or task["id"] in task_ids
        ):
            raise ValueError("Task IDs must be nonempty and unique")
        task_ids.add(task["id"])
        if task.get("kind") not in ("generate", "validate") or task.get(
            "layout", "messages"
        ) not in ("messages", "labeled", "json"):
            raise ValueError("Task kind/layout is invalid")
        if (
            not isinstance(task.get("prompt"), str)
            or not isinstance(task.get("visible", ["*"]), list)
            or any(not isinstance(w, str) for w in task.get("visible", ["*"]))
        ):
            raise ValueError(
                "Task prompt must be text, visible must list window IDs or '*'"
            )
        if task.get("tools") and str(task.get("start", "auto")) != "auto":
            raise ValueError(
                "Tool-enabled tasks require start=auto; author timed tool interactions as replay events"
            )
        if task["kind"] == "generate" and (
            task.get("op") not in ("append", "generate")
            or not isinstance(task.get("window"), str)
            or not task["window"]
        ):
            raise ValueError("Generation task requires window and append/generate op")
    auto = config.get("automatic", {})
    for field, low, high, default in [
        ("max_samples", 0, 100000, 10),
        ("max_attempts", 0, 1000000, 30),
        ("autosave_every", 1, 10000, 5),
        ("max_consecutive_failures", 1, 1000, 3),
        ("regenerations", 0, 10, 2),
    ]:
        value = auto.get(field, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{field} must be an integer in [{low}, {high}]")
    delay = auto.get("delay_seconds", 1)
    if type(delay) not in (int, float) or not 0.1 <= delay <= 3600:
        raise ValueError("delay_seconds must be between 0.1 and 3600")
    if auto.get("failure_action", "stop") not in ("stop", "pause", "skip"):
        raise ValueError("failure_action must be stop, pause or skip")
    for field, default, high in [
        ("max_tool_rounds", 12, 32),
        ("max_tool_calls", 64, 256),
    ]:
        if (
            type(config.get(field, default)) is not int
            or not 1 <= config.get(field, default) <= high
        ):
            raise ValueError(f"{field} exceeds allowed limit")
    for name in ("seed", "seconds_per_call"):
        if (
            name in config.get("clock", {})
            and name == "seed"
            and not isinstance(config["clock"][name], str)
        ):
            raise ValueError("Clock seed must be an ISO timestamp string")
    if set(config.get("clock", {})) - {"seed", "seconds_per_call", "tick_rate"}:
        raise ValueError("Unknown clock setting")
    # Validate synthetic clock without invoking a tool.
    clock = Sandbox(**config.get("clock", {}))
    clock.close()
    return config


def selected_windows(plan, compiler, task):
    ids = task.get("visible", ["*"])
    at = task.get("visible_tick", plan["max_tick"])
    if type(at) is not int or not -1 <= at <= plan["max_tick"]:
        raise ValueError("visible_tick must fall within the compiled trajectory")
    result = []
    ordered = (
        plan["windows"]
        if "*" in ids
        else sorted(
            plan["windows"],
            key=lambda w: ids.index(w["id"]) if w["id"] in ids else len(ids),
        )
    )
    for window in ordered:
        if window["open"] > at or ("*" not in ids and window["id"] not in ids):
            continue
        tokens = [
            r["token_id"]
            for r in plan["rows"]
            if r["window"] == window["id"]
            and r["tick"] <= at
            and not (
                r["phase"] == "D"
                and r["tick"] >= task.get("_decision_tick", plan["max_tick"] + 1)
            )
            and r["durable"]
            and ("removed_at" not in r or at < r["removed_at"]["tick"])
            and r["token_id"] not in compiler.ids.values()
        ]
        result.append(
            {
                "window": window["id"],
                "slot": window["slot"],
                "type": window["type"],
                "closed": window["close"] is not None and window["close"] <= at,
                "content": compiler.tokenizer.decode(tokens, skip_special_tokens=False),
            }
        )
    if sum(len(w["content"]) for w in result) > 500_000:
        raise ValueError("Visible task context exceeds 500k character limit")
    return result


def assemble(windows, task, tags, feedback=""):
    instruction = task["prompt"] + "\nConditions/tags: " + ", ".join(tags)
    if feedback:
        instruction += (
            "\nPrevious validation feedback (correct these issues):\n" + feedback
        )
    messages = [{"role": "system", "content": instruction}]
    layout = task.get("layout", "messages")
    if layout == "json":
        messages.append(
            {
                "role": "user",
                "content": json.dumps({"windows": windows}, ensure_ascii=False),
            }
        )
    elif layout == "labeled":
        messages.append(
            {
                "role": "user",
                "content": "\n\n".join(
                    f"[WINDOW {w['window']} / {w['type']}]\n{w['content']}"
                    for w in windows
                )
                or "Create the first content stream.",
            }
        )
    else:
        # Keep imported system text in data messages, not privileged instructions.
        for w in windows:
            messages.append(
                {
                    "role": "user",
                    "content": f"Window {w['window']} ({w['type']}):\n{w['content']}",
                }
            )
        messages.append(
            {
                "role": "user",
                "content": "Complete the configured task. Return only its content stream.",
            }
        )
    return messages


class EndpointHTTPError(ValueError):
    """An HTTP failure without disclosing a provider response body."""

    def __init__(self, endpoint_id, status_code):
        super().__init__(f'LLM endpoint {endpoint_id} returned HTTP {status_code}')
        self.status_code = status_code


class LLMClient:
    def __init__(self, transport=None):
        self.transport = transport

    async def complete(self, endpoint, messages, tools=None):
        key = endpoint.get("api_key") or os.getenv(endpoint.get("api_key_env", ""), "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        payload = {
            "model": endpoint["model"],
            "messages": messages,
            "temperature": endpoint.get("temperature", 0.7),
            endpoint.get("completion_limit_parameter", "max_tokens"): int(endpoint.get("max_tokens", 1024)),
            "stream": False,
        }
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if endpoint.get('response_format'):
            payload['response_format'] = endpoint['response_format']
        async with httpx.AsyncClient(
            timeout=endpoint.get("timeout", 120),
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            async with client.stream(
                "POST",
                endpoint["base_url"].rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    # Do not echo response bodies: providers may repeat secrets.
                    raise EndpointHTTPError(endpoint['id'], response.status_code)
                raw = bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    if len(raw) > 4_000_000:
                        raise ValueError("LLM response exceeds 4 MB")
                data = json.loads(raw, parse_constant=reject_constant)
        if not isinstance(data.get("choices"), list) or not data["choices"]:
            raise ValueError("LLM returned no choices")
        choice = data["choices"][0]
        if not endpoint.get("retain_incomplete") and choice.get("finish_reason") in ("length", "content_filter"):
            raise ValueError(f"Incomplete LLM response: {choice['finish_reason']}")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("LLM choice has no message")
        if message.get("content") is not None and not isinstance(
            message["content"], str
        ):
            raise ValueError("Only text completion content is supported")
        if endpoint.get("retain_incomplete"):
            message = {**message, "_usage": data.get("usage", {}), "_finish_reason": choice.get("finish_reason")}
        return message


class SampleBuilder:
    def __init__(self, compiler, config, client=None, log=None):
        self.compiler, self.config = compiler, config
        self.client = client or LLMClient()
        self.log = log or (lambda message: None)
        self.endpoints = {e["id"]: e for e in config["endpoints"]}
        self.sample = {
            "schema-version": "1.0",
            "id": str(uuid.uuid4()),
            "tick-policy": "external-first",
            "defaults": {"implicit-noop-loss-weight": 0.1},
            "replay": {},
            "metadata": {
                "tags": config.get("tags", []),
                "synthetic_clock": config.get("clock", {}),
            },
        }
        self.sample["uuid"] = self.sample["id"]
        self.next_tick = 0
        self.serial = 0

    def add_stream(
        self,
        window,
        content,
        op="generate",
        source="assistant",
        start="auto",
        close=False,
        loss=None,
        request_id=None,
    ):
        key = str(self.next_tick) if start == "auto" else str(start)
        events = self.sample["replay"].setdefault(key, [])
        all_events = [e for group in self.sample["replay"].values() for e in group]
        if not any(
            e["op"] in ("open", "tool-call") and e["window"] == window
            for e in all_events
        ):
            events.append(
                {
                    "op": "open",
                    "window": window,
                    "window-type": (
                        source
                        if op == "append"
                        else ("reasoning" if "reason" in window else "chat")
                    ),
                    **({"request-id": request_id} if request_id else {}),
                }
            )
        if (
            op == "generate"
            and close
            and not content.endswith(self.compiler.special["eos"])
        ):
            content += self.compiler.special["eos"]
        event = {
            "op": op,
            "window": window,
            "content": content,
            "loss": loss or {"mask": "auto"},
            **({"request-id": request_id} if request_id else {}),
        }
        if op == "append":
            event.update(source=source, delivery="instant")
        events.append(event)
        plan = self.compiler.compile(self.sample)
        self.next_tick = plan["max_tick"] + 1
        if op == "append" and close:
            self.add_stream(window, self.compiler.special["eos"], start="auto")
        return plan

    async def generate(self, *args, **kwargs):
        raise ValueError("Task-based generation is retired; use user_scenario or idle_scenario")

    async def validators(self, *args, **kwargs):
        raise ValueError("Run Validate Trajectory separately from content creation")
