"""Checkpointed local transitions for the YAML Flow interpreter."""

from copy import deepcopy

from . import adaptive_flow as flow
from .flow import event_payload
from .models import GraphSource


def prepare(driver, job, source, cp, plan, settings):
    current = cp.setdefault("flow", flow.state(plan))
    node = plan["flow"]["nodes"][current["node"]]
    if current["steps"] >= plan["flow"]["max_steps"] or (
        node["kind"] != "end" and len(source.events) >= plan["flow"]["max_events"]
    ):
        data = source.to_dict()
        data["metadata"]["generation_failure"] = dict(kind="flow_limit", node=current["node"])
        data["metadata"]["stage"] = -3
        cp["phase"] = "done"
        driver._save(job, GraphSource.model_validate(data), cp, outcome="fail")
        driver.jobs.append_log(
            job, "Flow limit reached; partial trajectory retained as Unfinished."
        )
        return None
    if node["kind"] == "end":
        if not current.get("output", {}).get("finished"):
            raise ValueError("Flow end requires a validated finish selection")
        data = source.to_dict()
        data["metadata"]["flow_outcome"] = current["output"]
        driver._after_generation(job, GraphSource.model_validate(data), cp)
        return None
    if node["kind"] == "branch":
        current["node"] = flow.branch(node, current.get("output"))
        current["steps"] += 1
        driver._save(job, source, cp)
        return None
    if node["kind"] == "select":
        payload = flow.selection_payload(source, plan, current, cp.get("flow_error", ""))
    else:
        if not current.get("task"):
            raise ValueError("Flow event node requires a selected task")
        task = deepcopy(current["task"])
        task["instruction"] += "\n" + node["instruction"]
        payload = event_payload(source, task, settings, cp["errors"] > 0)
        payload["task"] = task
    payload["flow_plan"] = plan  # Frozen author-only inputs, never provider messages.
    return payload


def advance(cp, plan):
    current = cp["flow"]
    current["node"] = plan["flow"]["nodes"][current["node"]]["next"]
    current["steps"] += 1
    cp.pop("flow_error", None)
