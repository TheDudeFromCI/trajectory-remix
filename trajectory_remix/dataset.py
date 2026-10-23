"""JSON interchange and trajectory defaults. API keys never enter exports."""

import copy
import json
import uuid

FORMAT = "trajectory-remix/kidra-dataset/1"


def reject_constant(value):
    raise ValueError(f"Nonstandard JSON constant: {value}")


def trajectory_uuid(sample):
    """Legacy labels receive a stable UUID; persisted sources always include it."""
    supplied = sample.get("uuid")
    if supplied is not None:
        try:
            return str(uuid.UUID(supplied))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Trajectory uuid must be a valid UUID") from error
    ident = sample.get("id")
    if isinstance(ident, str) and ident:
        try:
            return str(uuid.UUID(ident))
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, "trajectory-remix:" + ident))
    return str(uuid.uuid4())


def prepare_sample(sample, existing=None):
    if not isinstance(sample, dict):
        raise ValueError("sample must be an object")
    sample = copy.deepcopy(sample)
    if existing is not None:
        original = trajectory_uuid(existing)
        if "uuid" in sample and sample["uuid"] != original:
            raise ValueError("Trajectory UUID is immutable")
        sample["uuid"] = original
    else:
        sample["uuid"] = trajectory_uuid(sample)
    return sample


def new_sample():
    ident = str(uuid.uuid4())
    return {
        "schema-version": "1.0",
        "id": ident,
        "uuid": ident,
        "tick-policy": "external-first",
        "defaults": {"implicit-noop-loss-weight": 0.1},
        "replay": {
            "0": [
                {"op": "open", "window": "system", "window-type": "system"},
                {
                    "op": "append",
                    "window": "system",
                    "source": "system",
                    "content": "You are Kidra, a coordinator.",
                },
                {"op": "open", "window": "user", "window-type": "chat"},
                {
                    "op": "append",
                    "window": "user",
                    "source": "user",
                    "content": "Inspect the task and respond.",
                },
                {"op": "open", "window": "reasoning", "window-type": "reasoning"},
                {"op": "open", "window": "chat", "window-type": "chat"},
            ],
            "1": [
                {
                    "op": "generate",
                    "window": "reasoning",
                    "content": "I will inspect the request.<|eos|>",
                }
            ],
            "50": [{"op": "generate", "window": "chat", "content": "Ready.<|eos|>"}],
        },
    }


def parse_import(name, text):
    if len(text.encode()) > 32_000_000:
        raise ValueError("Import limit is 32 MB per file")
    if name.lower().endswith(".jsonl"):
        samples = []
        for line_no, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    samples.append(json.loads(line, parse_constant=reject_constant))
                except ValueError as e:
                    raise ValueError(f"{name}, line {line_no}: {e}") from e
        config = None
    else:
        obj = json.loads(text, parse_constant=reject_constant)
        if isinstance(obj, dict) and obj.get("format") == FORMAT:
            samples, config = obj.get("samples"), obj.get("config")
        elif isinstance(obj, list):
            samples, config = obj, None
        elif isinstance(obj, dict) and "samples" in obj:
            samples, config = obj["samples"], obj.get("config")
        else:
            samples, config = [obj], None
    if not isinstance(samples, list) or any(not isinstance(s, dict) for s in samples):
        raise ValueError("Expected trajectory object, array, JSONL or .kidra-dataset")
    if len(samples) > 100_000:
        raise ValueError("Sample count limit exceeded")
    if config is not None and not isinstance(config, dict):
        raise ValueError("Dataset config must be an object")
    # Keep malformed trajectories editable. Compilation gives precise diagnostics.
    return samples, config


def public_config(config):
    result = copy.deepcopy(config)
    for endpoint in result.get("endpoints", []):
        endpoint.pop("api_key", None)
    return result
