"""Evidence and coordination checks beyond token structure. Never executes a provider."""


def audit(sample, plan, state):
    metadata = sample.get("metadata", {})
    contract = (
        metadata.get("coordination_contract") if isinstance(metadata, dict) else None
    )
    if not isinstance(contract, dict):
        return None
    issues = []

    def require(condition, message):
        if not condition:
            issues.append(
                {
                    "severity": "error",
                    "path": "metadata.coordination_contract",
                    "message": message,
                }
            )

    conversations, jobs, notes, executions = (
        state[k] for k in ("conversations", "jobs", "notes", "executions")
    )
    latest = {}
    for job in jobs.values():
        latest[job["conversationId"]] = job
    workers = {c.get("worker_id") for c in conversations.values()} - {None}
    followups = sum(
        sum(m["role"] == "assistant" for m in c["messages"]) >= 2
        for c in conversations.values()
    )
    minimum = contract.get("minimum_subagents", 6)
    require(
        type(minimum) is int and 1 <= minimum <= 16, "minimum_subagents must be 1..16"
    )
    if type(minimum) is not int:
        minimum = 6
    require(
        len(conversations) >= minimum, f"Expected at least {minimum} distinct subagents"
    )
    require(
        len(workers) >= minimum,
        f"Expected at least {minimum} distinct recorded workers",
    )
    if contract.get("require_followups", True):
        require(
            followups >= minimum,
            "Clarifying follow-ups are missing for some specialists",
        )
    require(
        all(j["status"] == "completed" for j in latest.values()),
        "Some specialist conversations have unfinished or failed work",
    )
    require(
        len(notes) >= minimum and all(n["status"] == "done" for n in notes.values()),
        "Task notes must track all specialists through completion",
    )
    passed = [run for run in executions.values() if run["status"] == "passed"]
    if contract.get("require_execution", True):
        require(bool(passed), "No passing isolated execution receipt is recorded")
    for key, note in notes.items():
        if note.get("requires_evidence"):
            require(bool(note.get("evidence")), f"Task {key} lacks required evidence")
            for eid in note.get("evidence", []):
                run = executions.get(eid, {})
                require(
                    run.get("status") == "passed",
                    f"Task {key} references missing or failing evidence",
                )
                require(
                    all(
                        ref["path"] in state["files"]
                        and state["artifacts"].get(_artifact_key(ref))
                        == state["files"][ref["path"]]
                        for ref in run.get("inputs", [])
                    ),
                    f"Task {key} references stale tested files",
                )
    visible = [c for c in plan["tool_calls"] if not c.get("invisible")]
    coordinator_writes = [
        c for c in visible if c["tool"] in ("file_write", "file_patch", "file_delete")
    ]
    if contract.get("coordinator_only", True):
        require(
            not coordinator_writes, "The coordinator wrote implementation artifacts"
        )
    require(
        sum(c["tool"] == "runtime_status" for c in visible) >= 2,
        "Runtime status was not inspected during coordination",
    )
    require(
        sum(c["tool"] == "chat" for c in visible) >= 3, "Progress updates are missing"
    )
    reviews = [j for j in latest.values() if j["arguments"].get("role") == "review"]
    if contract.get("require_reviews", True):
        require(bool(reviews), "No peer review is recorded")
        for job in reviews:
            refs = job["arguments"].get("artifacts", [])
            require(
                any(
                    r["path"].endswith(".py")
                    and not r["path"].rsplit("/", 1)[-1].startswith("test_")
                    for r in refs
                ),
                "A reviewer was not supplied implementation source",
            )
            require(
                all(
                    state["artifacts"].get(_artifact_key(r))
                    == state["files"].get(r["path"])
                    for r in refs
                    if r["path"].endswith(".py") or r["path"] == "/README.md"
                ),
                "A final reviewer used stale source/tests/docs versions",
            )
            result = job.get("outcome", {}).get("result", {})
            require(
                result.get("reviewed_artifacts") == refs,
                "Review provenance does not match supplied artifacts",
            )
    return {
        "valid": not issues,
        "diagnostics": issues,
        "subagents": len(conversations),
        "workers": len(workers),
        "conversations_with_followups": followups,
        "jobs": len(jobs),
        "completed_tasks": sum(n["status"] == "done" for n in notes.values()),
        "passing_executions": len(passed),
        "coordinator_file_writes": len(coordinator_writes),
        "evidence_origin": "recorded Docker receipts and worker tool traces",
    }


def _artifact_key(ref):
    import json

    return json.dumps([ref["path"], ref["version"]])
