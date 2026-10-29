"""Dataset-owned Markdown guidance shared by the editor and MCP commands."""

GUIDE_TOPICS = ("start", "first", "parallel", "edit", "sandbox", "tools", "search", "review", "metrics", "reference", "example")
GUIDE_TITLES = ("Getting Started", "First Trajectory", "Parallel Work", "Editing", "Sandbox", "Tools", "Search", "Review", "Dataset Metrics", "Authoring Reference", "Example Trajectory")
MAX_GUIDE_LENGTH = 200_000


def validate_guidance(value):
    if not isinstance(value, dict) or set(value) - {"guide", "checklist", "scenarios"}:
        raise ValueError("review_guidance must contain only guide, checklist and scenarios")

    def validate_pair(pair):
        if not isinstance(pair, dict) or set(pair) - {"guide", "checklist"}:
            raise ValueError("Scenario guidance must contain only guide and checklist")
        for name, text in pair.items():
            if not isinstance(text, str) or len(text) > MAX_GUIDE_LENGTH:
                raise ValueError(f"Custom {name} must be Markdown text of at most {MAX_GUIDE_LENGTH} characters")

    validate_pair({k: v for k, v in value.items() if k != "scenarios"})
    scenarios = value.get("scenarios", {})
    if not isinstance(scenarios, dict) or len(scenarios) > 1000:
        raise ValueError("Guidance scenarios must be an object with at most 1000 entries")
    for name, pair in scenarios.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Guidance scenario names must have 1..200 characters")
        validate_pair(pair)


def custom_documents(config, scenario=None, *, include_empty=False):
    guidance = config.get("review_guidance", {})
    scopes = [("dataset", "Dataset", guidance)]
    if scenario:
        scopes.append(("scenario", f"Scenario {scenario}", guidance.get("scenarios", {}).get(scenario, {})))
    return [
        {"id": f"custom.{scope}.{kind}", "title": f"{label} {kind.title()}",
         "content": pair.get(kind, ""), "format": "markdown", "source": "dataset_config", "editable": True}
        for scope, label, pair in scopes for kind in ("guide", "checklist")
        if include_empty or pair.get(kind, "").strip()
    ]


def checklist_markdown(checklist):
    lines = [f"# {checklist.scenario} Review Checklist", f"Scenario tag: `{checklist.tag}`"]
    for name, check in checklist.checks.items():
        lines.append(f"\n## {name}")
        if check.view:
            lines.append(f"View: {check.view}")
        for ident, rule in check.rules.items():
            lines.append(f"\n- [ ] **{ident}**: {rule.requirement}")
            # Include every rule field, including applicability and evidence guidance.
            for key, value in rule.model_dump().items():
                if key != "requirement":
                    lines.append(f"  - {key}: {value}")
        lines.extend(["\n### Review Prompt", check.prompt])
        if check.repair_prompt:
            lines.extend(["\n### Repair Prompt", check.repair_prompt])
        for index, guide in enumerate(check.repair_guides, 1):
            lines.extend([f"\n### Repair Guide {index}", guide])
    return "\n\n".join(lines)
