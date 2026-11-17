"""Interactive, step-by-step Flow walkthrough for debugging generation settings.

A walkthrough samples one proposal, freezes a plan exactly as a generation run does,
then advances the Flow interpreter one transition per request. It reuses the run's
prompt builders and acceptance checks, so each step shows what a real run would send
and accept. Nothing is stored: the browser holds the state and sends it back each step.
Model calls go straight to the Generator endpoint, one at a time, without the job
queue's retries. Failed or rejected responses never change the state they started from,
except to record the correction a real run would send next.
"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import jsonschema
from pydantic import Field

from . import adaptive_driver
from . import adaptive_flow as interpreter
from .flow import append_event, chat_echo
from .flow_store import project_text
from .models import DTO, GraphSource, JsonValue
from .provider import invalid_reason, request_body
from .sampling import SamplerService
from .validation import diagnose

VERSION = "flow-walkthrough/1"


class WrapperError(Exception):
    """An accepted reply that would still break the graph. Runs pause instead of retrying."""


class WalkStart(DTO):
    seed: int | None = None
    overrides: dict[str, str] = Field(default_factory=dict)
    flow: dict[str, JsonValue] | None = None
    flow_yaml: str | None = None


class WalkState(DTO):
    version: Literal["flow-walkthrough/1"] = VERSION
    settings_revision: int = Field(ge=1)
    plan: dict[str, JsonValue]
    source: dict[str, JsonValue]
    checkpoint: dict[str, JsonValue]
    done: bool = False
    outcome: str | None = None


class WalkStep(DTO):
    state: WalkState
    mode: Literal["call", "manual"] = "call"
    response: JsonValue = None


def _snapshot(store, flow, text):
    from .flow_document import check

    if flow is not None and text is not None:
        raise ValueError("Send flow or flow_yaml, not both")
    if flow is None:
        return interpreter.load_flow(text=project_text(store) if text is None else text)
    result = check(flow=flow)
    if not result["valid"]:
        first = next(p for p in result["diagnostics"] if p["severity"] == "error")
        raise ValueError("Flow draft is invalid: " + first["message"])
    return dict(flow=result["flow"], flow_hash=result["flow_hash"])


def _errors(source, settings):
    return [d for d in diagnose(source, settings) if d.severity == "error"]


class Walkthrough:
    def __init__(self, store, sampler, transport):
        self.store, self.sampler, self.transport = store, sampler, transport
        self.lock = asyncio.Lock()  # At most one walkthrough model call at a time.

    def start(self, seed=None, overrides=None, flow=None, flow_yaml=None):
        frozen = self.sampler.editor()
        settings = self.store.settings(frozen["settings_revision"]).settings
        snapshot = _snapshot(self.store, flow, flow_yaml)
        proposal = self.sampler.propose(
            seed=seed, overrides=overrides or {}, frozen=frozen, persist=False
        )
        plan = interpreter.frozen_plan(proposal["proposal"], settings, snapshot)
        metadata = SamplerService.metadata(proposal)
        metadata["proposal"].pop("frozen", None)  # The full matrix is not useful here.
        metadata.update(stage=-3, split="Auto")
        source = dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows=plan["windows"],
            events={},
            metadata=metadata,
        )
        checkpoint = dict(serial=0, phase="event", index=0, errors=0, flow=interpreter.state(plan))
        state = WalkState(
            settings_revision=frozen["settings_revision"],
            plan=plan,
            source=GraphSource.model_validate(source).to_dict(),
            checkpoint=checkpoint,
        ).model_dump(mode="json")
        return dict(state=state, next=self.describe(state))

    def _load(self, state):
        state = WalkState.model_validate(state).model_dump(mode="json")
        settings = self.store.settings(state["settings_revision"]).settings
        return state, settings, GraphSource.model_validate(state["source"])

    def describe(self, state):
        """What the next step will do, including the exact request for model steps."""
        state, settings, source = self._load(state)
        cp, plan = state["checkpoint"], state["plan"]
        current = cp["flow"]
        node = plan["flow"]["nodes"][current["node"]]
        result = dict(node=current["node"], kind=node["kind"], knows=self.knows(state))
        if state["done"]:
            return dict(result, action="done", summary=f"Finished: {state['outcome']}")
        if cp.get("echo"):
            text = f"Deliver the chat sent by {cp['echo']} to the chatroom (no model call)"
            return dict(result, action="chat_echo", summary=text)
        if adaptive_driver.limit_reached(source, current, plan):
            return dict(result, action="limit", summary="A limit is reached; the run stops here")
        if node["kind"] == "end":
            return dict(result, action="end", summary="Finish the trajectory, then reviews run")
        if node["kind"] == "branch":
            return dict(result, action="branch", summary=_branch_text(node))
        try:
            payload = adaptive_driver.request_payload(source, cp, plan, settings)
        except ValueError as error:
            return dict(result, action="blocked", summary=str(error))
        return dict(
            result,
            action="model",
            summary=(
                "Ask the Generator to choose the next event"
                if node["kind"] == "select"
                else f"Ask the Generator to write event {payload['task']['id']}"
            ),
            request=self.request(payload, settings, source.id),
        )

    @staticmethod
    def request(payload, settings, trajectory_id=""):
        endpoint_id = settings.endpoint_roles.get("generator")
        shown = {k: v for k, v in payload.items() if k != "flow_plan"}
        if endpoint_id is None:
            return dict(shown, endpoint=None, wire=None)
        endpoint = settings.endpoints[endpoint_id].model_dump(mode="json")
        sent = {k: v for k, v in payload.items() if k != "flow_plan"}
        body, budget = request_body(dict(sent, endpoint_config=endpoint), trajectory_id)
        wire = dict(url=endpoint["url"].rstrip("/") + "/chat/completions", **body)
        return dict(shown, endpoint=endpoint_id, wire=wire, budget=budget)

    def knows(self, state):
        """What the interpreter holds at this point: its position, memory and draft."""
        state, settings, source = self._load(state)
        cp, plan = state["checkpoint"], state["plan"]
        current = cp["flow"]
        problems = diagnose(source, settings)
        return dict(
            node=current["node"],
            steps=current["steps"],
            max_steps=plan["flow"]["max_steps"],
            events=len(source.events),
            max_events=plan["flow"]["max_events"],
            last_selection=current.get("output"),
            pending_task=current.get("task"),
            correction=cp.get("flow_error", ""),
            format_errors=cp["errors"],
            format_retries=settings.generation.format_retries,
            chat_echo_pending=cp.get("echo"),
            windows=sorted(source.windows),
            diagnostics=[d.model_dump(mode="json") for d in problems],
        )

    async def step(self, state, mode="call", response=None):
        state, settings, source = self._load(state)
        if state["done"]:
            raise ValueError("This walkthrough has finished; start a new sample")
        before = deepcopy(state)
        cp, plan = state["checkpoint"], state["plan"]
        current = cp["flow"]
        node_id = current["node"]
        node = plan["flow"]["nodes"][node_id]
        trace = dict(node=node_id, kind=node["kind"], accepted=True, error=None)
        if cp.get("echo"):
            source = self._echo(source, cp, settings, trace)
        elif adaptive_driver.limit_reached(source, current, plan):
            data = source.to_dict()
            data["metadata"]["generation_failure"] = dict(kind="flow_limit", node=node_id)
            source = GraphSource.model_validate(data)
            state.update(done=True, outcome="flow_limit")
            trace.update(action="limit", summary="Limit reached; a run keeps this as Unfinished")
        elif node["kind"] == "end":
            source = self._end(source, state, trace)
        elif node["kind"] == "branch":
            self._branch(node, current, trace)
        else:
            source = await self._model(source, state, settings, mode, response, trace)
        trace["to"] = cp["flow"]["node"]
        state["source"] = source.to_dict()
        state = WalkState.model_validate(state).model_dump(mode="json")
        trace["added_events"] = sorted(
            set(state["source"]["events"]) - set(before["source"]["events"])
        )
        trace["knows"] = self.knows(state)
        return dict(state=state, trace=trace, next=self.describe(state))

    @staticmethod
    def _echo(source, cp, settings, trace):
        echo = chat_echo(source, cp.pop("echo"))
        trace.update(action="chat_echo", summary="No chat delivery was needed")
        if echo:
            content = echo.pop("content")
            source = append_event(source, echo, {"content": content}, settings)
            trace["summary"] = f"Delivered {echo['id']} to the chatroom"
            if _errors(source, settings):
                raise ValueError("Chat helper structural error")
        return source

    @staticmethod
    def _end(source, state, trace):
        output = state["checkpoint"]["flow"].get("output") or {}
        trace["action"] = "end"
        if not output.get("finished"):
            # A run raises here and the worker pauses the job.
            state.update(done=True, outcome="end_without_finish")
            trace.update(
                accepted=False,
                error="Flow end requires a validated finish selection; a run would pause here",
            )
            return source
        data = source.to_dict()
        data["metadata"]["flow_outcome"] = output
        data["metadata"]["stage"] = 0
        state.update(done=True, outcome="finished")
        trace["summary"] = "Finished; a run now hands the trajectory to review"
        return GraphSource.model_validate(data)

    @staticmethod
    def _branch(node, current, trace):
        value, found = current.get("output"), True
        for part in node["path"].split("."):
            if not isinstance(value, dict) or part not in value:
                value, found = None, False
                break
            value = value[part]
        target = interpreter.branch(node, current.get("output"))
        current["node"] = target
        current["steps"] += 1
        trace.update(
            action="branch",
            path=node["path"],
            value=value,
            found=found,
            equals=node["equals"],
            matched=target == node["then"] and node["then"] != node["otherwise"],
            summary=f"{node['path']} = {json.dumps(value) if found else '(missing)'} → {target}",
        )

    async def _model(self, source, state, settings, mode, response, trace):
        cp, plan = state["checkpoint"], state["plan"]
        payload = adaptive_driver.request_payload(source, cp, plan, settings)
        trace.update(action="model", operation=payload["operation"])
        trace["request"] = self.request(payload, settings, source.id)
        if mode == "manual":
            result = dict(output=response, model="manual")
        else:
            result = await self._call(payload, settings, source, trace)
            if result is None:
                return source
        trace["response"] = result
        if result.get("context_limit"):
            trace.update(accepted=False, error="Context limit: a run pauses without a verdict")
            return source
        if result.get("invalid_output"):
            return self._reject(source, state, settings, invalid_reason(result), trace)
        body = result.get("output")
        try:
            jsonschema.Draft202012Validator(payload["schema"]).validate(body)
        except jsonschema.ValidationError as error:
            return self._reject(source, state, settings, error.message, trace)
        try:
            if payload["operation"] == "flow_select":
                return self._select(source, state, settings, body, trace)
            return self._write(source, state, settings, payload["task"], body, trace)
        except WrapperError as error:
            trace.update(accepted=False, error=str(error) + "; a run would pause here")
            return source
        except (ValueError, TypeError, KeyError) as error:
            return self._reject(source, state, settings, str(error), trace)

    async def _call(self, payload, settings, source, trace):
        endpoint = settings.endpoint_roles.get("generator")
        if endpoint is None:
            trace.update(accepted=False, error="Configure the Generator endpoint first")
            return None
        ticket = SimpleNamespace(
            job_id="walkthrough",
            trajectory_id=source.id,
            endpoint=endpoint,
            step="walkthrough",
            epoch=0,
            payload=dict(
                payload,
                endpoint_config=settings.endpoints[endpoint].model_dump(mode="json"),
            ),
        )
        try:
            async with self.lock:
                return await self.transport(ticket)
        except Exception as error:  # Reported to the person debugging; never logged.
            status = getattr(getattr(error, "response", None), "status_code", None)
            detail = f" HTTP {status}" if type(status) is int else ""
            text = f"{type(error).__name__}{detail}: {str(error)[:300]}"
            provider = provider_message(error)
            if provider:
                text += f" Provider says: {provider}"
            trace.update(accepted=False, error="Request failed: " + text)
            return None

    @staticmethod
    def _reject(source, state, settings, message, trace):
        cp = state["checkpoint"]
        cp["flow_error"] = message[:2000]
        cp["errors"] += 1
        trace.update(accepted=False, error=message[:2000])
        if cp["errors"] > settings.generation.format_retries:
            data = source.to_dict()
            data["metadata"]["stage"] = -1
            data["metadata"]["generation_failure"] = dict(
                kind="invalid_provider_output", event_index=cp["index"]
            )
            state.update(done=True, outcome="invalid_provider_output")
            trace["summary"] = "Correction limit reached; a run marks this Unsuccessful"
            return GraphSource.model_validate(data)
        trace["summary"] = (
            f"Rejected; correction {cp['errors']}/{settings.generation.format_retries}"
        )
        return source

    @staticmethod
    def _select(source, state, settings, body, trace):
        cp, plan = state["checkpoint"], state["plan"]
        selected, task, selection = interpreter.accept_selection(source, body, plan, settings)
        cp["errors"] = 0
        cp["flow"].update(output=selection, task=task)
        adaptive_driver.advance(cp, plan)
        trace.update(
            selection=selection,
            task=task,
            summary="Chose to finish" if task is None else f"Chose event {task['id']}",
        )
        return selected

    @staticmethod
    def _write(source, state, settings, task, body, trace):
        cp, plan = state["checkpoint"], state["plan"]
        written = append_event(source, task, body, settings)
        errors = _errors(written, settings)
        if errors:
            # A run treats this as a wrapper bug and pauses; nothing is committed.
            raise WrapperError(
                f"Event would break the graph: {errors[0].code}: {errors[0].message}"
            )
        cp["errors"] = 0
        cp["index"] += 1
        adaptive_driver.advance(cp, plan)
        cp["echo"] = task["id"]
        trace.update(event=written.events[task["id"]].model_dump(mode="json"))
        trace["summary"] = f"Wrote event {task['id']}"
        return written


def provider_message(error):
    """The provider's own error message, such as an unsupported parameter. Shown in the
    walkthrough only; job logs never copy response bodies."""
    response = getattr(error, "response", None)
    try:
        body = response.json() if response is not None else None
    except ValueError:
        return (response.text or "")[:500] if response is not None else ""
    if isinstance(body, dict):
        inner = body.get("error", body)
        message = inner.get("message") if isinstance(inner, dict) else inner
        raw = inner.get("metadata", {}).get("raw") if isinstance(inner, dict) else None
        text = " ".join(str(part) for part in (message, raw) if part)
        return text[:500]
    return ""


def _branch_text(node):
    equals = json.dumps(node["equals"])
    return f"If {node['path']} = {equals} go to {node['then']}, otherwise {node['otherwise']}"
