"""Shared helpers owned by the simulation tools, never by the compiler."""

import json
from ._protocol import tool_failure


def result(value, done=True, modified=False):
    return json.dumps({"ok": True, "result": value}, ensure_ascii=False), done, modified


def failure(error, modified=False, *, max_bytes=None):
    value = tool_failure(error)
    output = json.dumps(value)
    if max_bytes is not None and len(output.encode()) > max_bytes:
        message = value["error"]
        low, high = 0, len(message)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = json.dumps({**value, "error": message[:middle] + "…"})
            if len(candidate.encode()) <= max_bytes:
                low = middle
            else:
                high = middle - 1
        output = json.dumps({**value, "error": message[:low] + "…"})
    return output, True, modified


SYSTEM_DEFAULTS = {
    "cpu_usage_percent": 0,
    "cpu_cores": 8,
    "cpu_model": "Simulated CPU",
    "ram_total_bytes": 16 * 1024**3,
    "ram_used_bytes": 4 * 1024**3,
    "vram_total_bytes": 8 * 1024**3,
    "vram_used_bytes": 0,
    "gpu_model": "Simulated GPU",
    "os": "Linux",
    "architecture": "x86_64",
}
SYSTEM_PROPERTIES = {
    "cpu_usage_percent": {"type": "number", "minimum": 0, "maximum": 100},
    "cpu_cores": {"type": "integer", "minimum": 1, "maximum": 4096},
    **{
        key: {"type": "integer", "minimum": 0, "maximum": 2**63 - 1}
        for key in (
            "ram_total_bytes",
            "ram_used_bytes",
            "vram_total_bytes",
            "vram_used_bytes",
        )
    },
    **{
        key: {"type": "string", "minLength": 1, "maxLength": 128}
        for key in ("cpu_model", "gpu_model", "os", "architecture")
    },
}
