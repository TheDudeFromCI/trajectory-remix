"""Opaque state, immutable window identity, and the per-tick tool protocol."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType

from jsonschema import Draft202012Validator, validators

ArgumentValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int
    ),
)

from ._protocol import ToolError
from ._timeout import invoke


@dataclass(frozen=True)
class ExecutionContext:
    tick: int = 0
    start: int = 0
    duration: int = 0  # Compatibility delay for older authored built-in calls.
    call_id: str = "standalone"
    invisible: bool = False
    metadata: dict = field(default_factory=dict)
    clock: dict = field(default_factory=dict)
    seed: str = "2030-01-01T09:00:00+00:00"
    max_bytes: int = 2_000_000
    recorded_outcome: dict | None = None

    @property
    def elapsed(self):
        return self.tick - self.start


execution_context = ContextVar("tool_execution_context", default=ExecutionContext())


class WindowProperties(MutableMapping):
    """Tools may replace content, but cannot change a window's identity or purpose."""

    __slots__ = ("_name", "_purpose", "_content", "_write")

    def __init__(self, name: str, purpose: str, content: str, write=None):
        self._name, self._purpose, self._content, self._write = (
            name,
            purpose,
            content,
            write,
        )

    @property
    def name(self):
        return self._name

    @property
    def purpose(self):
        return self._purpose

    @property
    def content(self):
        return self._content

    @content.setter
    def content(self, value):
        if not isinstance(value, str):
            raise ToolError("Window content must be text")
        if self._write:
            self._write(self._name, self._content, value)
        self._content = value

    def __getitem__(self, key):
        if key not in ("name", "purpose", "content"):
            raise KeyError(key)
        return getattr(self, key)

    def __setitem__(self, key, value):
        if key != "content":
            raise ToolError("Window name and purpose are immutable")
        self.content = value

    def __iter__(self):
        return iter(("name", "purpose", "content"))

    def __len__(self):
        return 3

    def __delitem__(self, key):
        raise ToolError("Window properties cannot be deleted")


def schema_invisible(schema, root, seen=frozenset()):
    if not isinstance(schema, dict):
        return False
    if schema.get("invisible") or schema.get("x-invisible"):
        return True
    reference = schema.get("$ref")
    if (
        isinstance(reference, str)
        and reference.startswith("#/")
        and reference not in seen
    ):
        target = root
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return schema_invisible(target, root, seen | {reference})
    return False


def public_schema(schema, root=None):
    """Remove invisible arguments without interpreting defaults or property names as schema keywords."""
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    root = schema if root is None else root
    mappings = {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
    }
    children = {
        "additionalProperties",
        "unevaluatedProperties",
        "items",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
    }
    arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}
    hidden = {
        name
        for name, spec in schema.get("properties", {}).items()
        if schema_invisible(spec, root)
    }
    result = {}
    for key, value in schema.items():
        if key in ("invisible", "x-invisible"):
            continue
        if key in mappings:
            result[key] = {
                name: public_schema(spec, root)
                for name, spec in value.items()
                if key != "properties" or name not in hidden
            }
        elif key in children:
            result[key] = public_schema(value, root)
        elif key in arrays:
            result[key] = [public_schema(spec, root) for spec in value]
        elif key == "required":
            result[key] = [name for name in value if name not in hidden]
        else:
            result[key] = copy.deepcopy(value)
    return result


def reject_invisible(value, schema, path="arguments", root=None, seen=None):
    root = schema if root is None else root
    seen = set() if seen is None else seen
    if not isinstance(schema, dict):
        return
    if schema.get("invisible") or schema.get("x-invisible"):
        raise ToolError(
            f"Invisible argument {path} is only allowed in invisible-tool-calls"
        )
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        target = root
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        marker = (reference, id(value))
        if marker not in seen:
            reject_invisible(value, target, path, root, seen | {marker})
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key, child in value.items():
            spec = properties.get(key, schema.get("additionalProperties", {}))
            if isinstance(spec, dict):
                if spec.get("invisible") or spec.get("x-invisible"):
                    raise ToolError(
                        f"Invisible argument {path}.{key} is only allowed in invisible-tool-calls"
                    )
                reject_invisible(child, spec, f"{path}.{key}", root, seen)
        import re

        for pattern, spec in schema.get("patternProperties", {}).items():
            for key, child in value.items():
                if re.search(pattern, key):
                    reject_invisible(child, spec, f"{path}.{key}", root, seen)
        for spec in schema.get("dependentSchemas", {}).values():
            reject_invisible(value, spec, path, root, seen)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            prefixes = schema.get("prefixItems", [])
            spec = prefixes[index] if index < len(prefixes) else schema.get("items", {})
            reject_invisible(item, spec, f"{path}[{index}]", root, seen)
    for keyword in ("allOf", "anyOf", "oneOf"):
        for child in schema.get(keyword, []):
            reject_invisible(value, child, path, root, seen)
    for keyword in ("if", "then", "else"):
        if keyword in schema:
            reject_invisible(value, schema[keyword], path, root, seen)


@dataclass(frozen=True)
class Tool:
    name: str
    schema: dict
    run: object
    description: str = ""
    file_hash: str = ""
    external: bool = False
    invisible: bool = False

    def definition(self, invisible=False):
        if self.invisible and not invisible:
            raise ToolError(
                f"Tool {self.name} is invisible-only; use invisible-tool-calls"
            )
        return {
            **({"invisible": self.invisible} if invisible else {}),
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(self.schema)
                if invisible
                else public_schema(self.schema),
            },
        }


class ToolRegistry:
    def __init__(self, folder=None):
        from importlib import import_module

        self.tools = {}
        self.folder = Path(folder).resolve() if folder else None
        for path in sorted(Path(__file__).parent.glob("*.py")):
            if path.stem.startswith("_") or path.stem == "runtime":
                continue
            module = import_module(f"{__package__}.{path.stem}")
            self.register(module, hashlib.sha256(path.read_bytes()).hexdigest())
        if self.folder:
            if not self.folder.is_dir():
                raise ToolError(f"Custom tool folder does not exist: {self.folder}")
            for path in sorted(self.folder.glob("*.py")):
                source = path.read_bytes()
                module = ModuleType(
                    f"remix_custom_{hashlib.sha256(str(path).encode() + source).hexdigest()}"
                )
                module.__file__ = str(path)
                sys.modules[module.__name__] = module
                try:
                    # Compile source directly: same-size edits must not reuse stale pyc files.
                    exec(compile(source, str(path), "exec"), module.__dict__)
                    # Spawned workers must receive dynamic modules by value.
                    import cloudpickle

                    cloudpickle.register_pickle_by_value(module)
                    self.register(
                        module, hashlib.sha256(source).hexdigest(), external=True
                    )
                except Exception as error:
                    sys.modules.pop(module.__name__, None)
                    raise ToolError(
                        f"Could not load custom tool {path.name}: {error}"
                    ) from error
        self.fingerprint = hashlib.sha256(
            json.dumps(
                [
                    (t.name, t.schema, t.file_hash, t.invisible)
                    for t in self.tools.values()
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def register(self, module, file_hash="", external=False):
        name, schema, run = (
            getattr(module, "name", None),
            getattr(module, "schema", None),
            getattr(module, "run", None),
        )
        if not isinstance(name, str) or not name or len(name) > 128:
            raise ToolError("Tool requires a nonempty name of at most 128 characters")
        if name in self.tools:
            raise ToolError(f"Duplicate tool name: {name}")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ToolError(f"{name}: schema must describe an object")
        invisible = getattr(module, "invisible", False)
        if type(invisible) is not bool:
            raise ToolError(f"{name}: invisible must be a boolean")
        Draft202012Validator.check_schema(schema)
        if not callable(run) or inspect.iscoroutinefunction(run):
            raise ToolError(f"{name}: run must be a synchronous function")
        inspect.signature(run).bind({}, {}, [])
        self.tools[name] = Tool(
            name,
            copy.deepcopy(schema),
            run,
            getattr(
                module,
                "description",
                schema.get("description", inspect.getdoc(module) or ""),
            ),
            file_hash,
            external,
            invisible,
        )

    def get(self, name):
        if name not in self.tools:
            raise ToolError(f"Unknown tool (missing): {name}")
        return self.tools[name]

    def schemas(self, invisible=False):
        return [
            tool.definition(invisible)
            for tool in self.tools.values()
            if invisible or not tool.invisible
        ]

    def check_visibility(self, name, invisible=False):
        tool = self.get(name)
        if tool.invisible and not invisible:
            raise ToolError(f"Tool {name} is invisible-only; use invisible-tool-calls")
        return tool

    def validate(self, name, args, invisible=False):
        tool = self.check_visibility(name, invisible)
        if not isinstance(args, dict):
            raise ToolError("Tool arguments must be an object")
        if not invisible:
            reject_invisible(args, tool.schema)
        schema = tool.schema if invisible else public_schema(tool.schema)
        try:
            json.dumps(args, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ToolError(f"Invalid tool arguments: {error}") from error
        error = next(ArgumentValidator(schema).iter_errors(args), None)
        if error:
            raise ToolError(f"{name}: {error.message}")
        return tool

    def run_tick(self, name, args, state, windows, context):
        tool = self.check_visibility(name, context.invisible)
        from ._clock import initialize

        initialized = "tick_rate" not in state
        initialize(state, context.clock)
        token = execution_context.set(context)
        try:
            result = invoke(
                tool,
                args,
                state,
                windows,
                context,
            )
            if not isinstance(result, tuple) or len(result) != 3:
                raise ToolError(
                    f"{name}: run must return (output, is_done, modified_state)"
                )
            output, done, modified = result
            if (
                not isinstance(output, str)
                or type(done) is not bool
                or type(modified) is not bool
            ):
                raise ToolError(f"{name}: expected (str, bool, bool) from run")
            return output, done, modified or initialized
        finally:
            execution_context.reset(token)


_default_registry = None


def default_registry():
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry


class Sandbox:
    """Workbench/generation adapter. The sandbox is a dict initialized with the trajectory tick rate."""

    def __init__(
        self,
        seed="2030-01-01T09:00:00+00:00",
        seconds_per_call=1,
        max_calls=64,
        max_bytes=2_000_000,
        *,
        random_seed=None,
        registry=None,
        tick_rate=10,
    ):
        import math

        datetime.fromisoformat(seed)
        if (
            type(seconds_per_call) not in (int, float)
            or not math.isfinite(seconds_per_call)
            or not 0 <= seconds_per_call <= 31_536_000
        ):
            raise ToolError("seconds_per_call must be finite and nonnegative")
        self.state = {}
        from ._clock import initialize

        initialize(self.state, {"tick_rate": tick_rate})
        self.tick = 0
        self.registry = registry or default_registry()
        self.options = {"seed": seed, "tick_rate": tick_rate}
        self.random_seed = seed if random_seed is None else random_seed
        self.step = seconds_per_call
        self.max_calls, self.max_bytes, self.calls = max_calls, max_bytes, 0
        self.closed = False

    @property
    def clock(self):
        from ._clock import now

        return now(
            self.state, ExecutionContext(tick=max(0, self.tick - 1), clock=self.options)
        )

    def __getattr__(self, key):
        if key in ("files", "memory", "notes", "skills", "artifacts", "conversations"):
            return self.state.get(key, {})
        raise AttributeError(key)

    def snapshot(self):
        return copy.deepcopy(self.state)

    def restore(self, state, *, tick=None):
        self.state = copy.deepcopy(state)
        self.tick = state.get("clock_tick", -1) + 1 if tick is None else tick

    def close(self):
        self.state.clear()
        self.closed = True

    def check_args(self, name, args, invisible=False):
        self.registry.validate(name, args, invisible)
        if len(json.dumps(args).encode()) > self.max_bytes:
            raise ToolError("Argument quota exceeded")

    def call_sync(
        self,
        name,
        args,
        *,
        windows=None,
        invisible=False,
        recorded_outcome=None,
        subagent_outcome=None,
        chat_open=False,
        agent_name="Kidra",
    ):
        if self.closed:
            raise ToolError("Sandbox has been destroyed")
        self.check_args(name, args, invisible)
        if not invisible:
            self.calls += 1
            if self.calls > self.max_calls:
                raise ToolError("Tool call budget exhausted")
        if windows is None:
            windows = [WindowProperties("chat", "chat", "")] if chat_open else []
        output = []
        start = self.tick
        for tick in range(start, start + 100_000):
            text, done, _ = self.registry.run_tick(
                name,
                args,
                self.state,
                windows,
                ExecutionContext(
                    tick=tick,
                    start=start,
                    call_id=f"standalone-{start}",
                    invisible=invisible,
                    clock=self.options,
                    seed=self.random_seed,
                    metadata={"agent_name": agent_name},
                    recorded_outcome=recorded_outcome
                    if recorded_outcome is not None
                    else subagent_outcome,
                    max_bytes=self.max_bytes,
                ),
            )
            output.append(text)
            if done:
                self.tick = tick + 1
                text = "".join(output)
                try:
                    value = json.loads(text)
                except ValueError:
                    return text
                if isinstance(value, dict) and type(value.get("ok")) is bool:
                    if not value["ok"]:
                        raise ToolError(
                            value.get("error", "Tool failed"),
                            value.get("conversationId"),
                        )
                    return value.get("result")
                return text
        raise ToolError("Tool did not finish before the tick limit")

    def execute_sync(self, name, args, **context):
        return self.call_sync(name, args, invisible=True, **context)

    async def call(self, name, args, **context):
        return self.call_sync(name, args, **context)
