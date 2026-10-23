from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import secrets
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryFile

from starlette.background import BackgroundTask

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .authoring import CommandError, TrajectoryCommands
from .compiler import CompileError, Compiler, chunks
from .dataset import (
    new_sample,
    parse_import,
    public_config,
    reject_constant,
)
from .llm import TAGS, SampleBuilder, default_config, validate_config
from .runner import Runner
from .tools import SCHEMAS
from .storage import DatasetState, DatasetStore, StorageConflict

MAX_BODY = 40_000_000


def create_app(compiler: Compiler, dataset: Path, client=None):
    store = DatasetStore(dataset, default_config())
    config = validate_config(json.loads(store.info()[3]))
    state = DatasetState(store, config)
    runner = Runner(state, compiler, dataset, client)
    token = secrets.token_urlsafe(32)
    previews = OrderedDict()
    static = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            try:
                await runner.stop()
            finally:
                commands.tool_sessions.close()

    app = FastAPI(title="Trajectory Remix", lifespan=lifespan)
    app.state.dataset = state
    app.state.runner = runner
    app.state.history = runner.history
    app.state.csrf = token
    commands = TrajectoryCommands(state, compiler, runner, client)
    app.state.commands = commands
    app.state.tool_sessions = commands.tool_sessions
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def local_guard(request, call_next):
        # Session token is injected into same-origin HTML, never a URL parameter.
        # Blocks hostile pages posting to a local generation/file-writing service.
        if request.url.path.startswith("/api/"):
            if request.headers.get("x-remix-token") != token:
                return JSONResponse(
                    {"detail": "Missing local session token; reload the dashboard"},
                    status_code=403,
                )
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse(
                    {"detail": "Cross-origin requests are blocked"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(CompileError)
    async def compile_error(request, error):
        return JSONResponse(
            {"detail": str(error), "diagnostics": [error.diagnostic()]}, status_code=422
        )

    @app.exception_handler(StorageConflict)
    async def storage_conflict(request, error):
        return JSONResponse({"detail": str(error), "code": "conflict"}, status_code=409)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(CommandError)
    async def command_error(request, error):
        status = {"not_found": 404, "conflict": 409, "busy": 409}.get(error.code, 422)
        return JSONResponse(
            {"detail": str(error), "code": error.code}, status_code=status
        )

    async def body(request):
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > MAX_BODY:
                raise HTTPException(413, "Request exceeds 40 MB")
        try:
            data = json.loads(raw, parse_constant=reject_constant)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(422, "Invalid JSON body")
        if not isinstance(data, dict):
            raise HTTPException(422, "Request must be a JSON object")
        return data

    def editable():
        if runner.status["running"]:
            raise HTTPException(
                409, "Stop generation before editing the dataset or configuration"
            )

    def sample_at(index):
        if not 0 <= index < len(state["samples"]):
            raise HTTPException(404, "Sample not found")
        return state["samples"][index]

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(
            (static / "index.html").read_text().replace("__REMIX_TOKEN__", token)
        )

    @app.get("/api/state")
    async def get_state(
        offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
        query: str = "", selected_id: str | None = None,
    ):
        revision = state["revision"]
        total, samples = store.listing(query, offset, limit, dashboard=True)
        selected_index = store.samples.index_of(selected_id) if selected_id is not None else None
        return {
            "samples": samples,
            "sample_count": len(store.samples),
            "total": total,
            "offset": offset,
            "limit": limit,
            "query": query,
            "selected_index": selected_index,
            "config": public_config(state["config"]),
            "revision": revision,
            "history": runner.history.view(),
            "status": {**runner.status, "last_saved_revision": revision},
            "logs": list(runner.logs),
            "tags": TAGS,
            "tools": SCHEMAS,
            "tokenizer": {
                "sha256": compiler.fingerprint,
                "vocab": compiler.tokenizer.get_vocab_size(),
                "special": compiler.special,
            },
        }

    @app.get("/api/samples/{index}")
    async def get_sample(index: int):
        revision = state["revision"]
        return JSONResponse(
            sample_at(index), headers={"X-Remix-Revision": str(revision)}
        )

    @app.get("/api/mcp/tools")
    async def mcp_tools():
        return {"tools": commands.tools()}

    @app.post("/api/mcp/call")
    async def mcp_call(request: Request):
        data = await body(request)
        if set(data) - {"name", "arguments"} or not isinstance(data.get("name"), str):
            raise ValueError("Expected tool name and arguments")
        arguments = data.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        return await commands.call(data["name"], arguments)

    @app.get("/api/tools")
    async def list_tools():
        return {"tools": SCHEMAS}

    @app.get("/api/samples/{index}/sandbox/{tick}")
    async def get_sandbox(index: int, tick: int):
        result = await commands.call(
            "get_sandbox_state",
            {
                "sample_id": sample_at(index).get("id"),
                "tick": tick,
            },
        )
        return result["sandbox"]

    @app.post("/api/samples/{index}/tool-calls")
    async def add_tool_call(index: int, request: Request):
        editable()
        data = await body(request)
        at = data.get("tick")
        if type(at) not in (int, str):
            raise ValueError("tick must be an integer or anchor expression")
        return await commands.call(
            "create_tool_call",
            {
                **data,
                "sample_id": sample_at(index).get("id"),
                "tick": str(at),
            },
        )

    @app.post("/api/samples")
    async def create_sample(request: Request):
        editable()
        data = await body(request)
        sample = data.get("sample") or new_sample()
        if "sample" not in data:
            for group in sample["replay"].values():
                for event in group:
                    if "content" in event:
                        event["content"] = event["content"].replace(
                            "<|eos|>", compiler.special["eos"]
                        )
        if not isinstance(sample, dict):
            raise ValueError("sample must be an object")
        updated = state["samples"].copy()
        updated.append(sample)
        runner.history.commit(updated, "Create trajectory in dashboard")
        return {"index": len(updated) - 1}

    @app.put("/api/samples/{index}")
    async def update_sample(index: int, request: Request):
        editable()
        data = await body(request)
        updated = state["samples"].copy()
        sample_at(index)
        expected_revision = data.get("expected_revision")
        if expected_revision is not None:
            if type(expected_revision) is not int or expected_revision < 0:
                raise ValueError("expected_revision must be a nonnegative integer")
            if expected_revision != state["revision"]:
                raise HTTPException(
                    409, "Dataset changed; reload the sample before saving"
                )
        sample = data.get("sample")
        if not isinstance(sample, dict):
            raise ValueError("sample must be an object")
        updated[index] = sample
        revision = runner.history.commit(
            updated, f"Edit trajectory in dashboard: {sample.get('id', 'untitled')}"
        )
        return {"ok": True, "revision": revision}

    @app.delete("/api/samples/{index}")
    async def delete_sample(index: int, expected_revision: int | None = Query(None, ge=0)):
        editable()
        updated = state["samples"].copy()
        if expected_revision is not None:
            updated.revision = expected_revision
        sample_at(index)
        updated.pop(index)
        runner.history.commit(updated, "Delete trajectory in dashboard")
        return {"ok": True}

    @app.post("/api/import")
    async def import_files(request: Request):
        editable()
        data = await body(request)
        files = data.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("files must be a nonempty list")
        imported, config = [], None
        for item in files:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("text"), str)
            ):
                raise ValueError("Each imported file requires name and text")
            try:
                samples, incoming = parse_import(item["name"], item["text"])
            except ValueError as e:
                raise ValueError(f"{item['name']}: {e}") from e
            imported.extend(samples)
            if incoming:
                config = validate_config(incoming)
        # Configuration and trajectories commit in the same SQLite transaction.
        incoming_config = config if data.get("load_config") else None
        if data.get("replace"):
            # Bulk replacement must not pull the previous dataset into undo RAM.
            revision = store.replace(imported, state["revision"], incoming_config)
            runner.history.undo_stack.clear()
            runner.history.redo_stack.clear()
            runner.history.tip = revision
            runner.history.notice = "History cleared after replacing the dataset"
        else:
            updated = state["samples"].copy()
            for sample in imported:
                updated.append(sample)
            runner.history.commit(updated, "Import trajectories in dashboard", config=incoming_config)
        return {"imported": len(imported)}

    @app.post("/api/compile")
    async def compile_sample(request: Request):
        data = await body(request)
        sample = data.get("sample")
        if sample is None:
            sample = sample_at(data.get("index", -1))
        plan = compiler.compile(sample)
        if len(plan["rows"]) > 50_000:
            raise ValueError(
                "Preview exceeds 50,000 rows; export via CLI or split the trajectory"
            )
        plan_id = hashlib.sha256(
            json.dumps(sample, sort_keys=True).encode()
        ).hexdigest()[:24]
        previews[plan_id] = plan
        previews.move_to_end(plan_id)
        while len(previews) > 6:
            previews.popitem(last=False)
        return {**plan, "plan_id": plan_id, "frame": compiler.frame(plan, 0)}

    @app.get("/api/frame/{plan_id}/{tick}")
    async def frame(plan_id: str, tick: int):
        if plan_id not in previews:
            raise HTTPException(404, "Preview expired; compile the sample again")
        return compiler.frame(previews[plan_id], tick)

    @app.post("/api/checkpoint")
    async def checkpoint():
        revision = await runner.save()
        return {"ok": True, "path": str(runner.save_path), "revision": revision}

    @app.post("/api/export")
    async def export(request: Request):
        data = await body(request)
        indices = data.get("indices")
        if indices is not None and (
            not isinstance(indices, list) or not indices
            or any(type(i) is not int or i < 0 for i in indices)
        ):
            raise ValueError("Select at least one sample to export")
        if indices is None and not len(store.samples):
            raise ValueError("Select at least one sample to export")
        size = data.get("ticks_per_chunk", 64)
        if type(size) is not int or not 1 <= size <= 100_000:
            raise ValueError("ticks_per_chunk must be an integer between 1 and 100000")
        # Spool a consistent snapshot to disk, one compiled trajectory at a time.
        # Validation still finishes before HTTP headers or output bytes are sent.
        spool = TemporaryFile(mode="w+b")
        try:
            with store.read_samples(indices) as samples:
                for sample in samples:
                    for chunk in chunks(compiler.compile(sample), size):
                        spool.write((json.dumps(chunk, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                        await asyncio.sleep(0)
            spool.seek(0)
        except BaseException:
            spool.close()
            raise

        async def close_export():
            spool.close()

        async def records():
            try:
                while block := spool.read(64 * 1024):
                    yield block
                    await asyncio.sleep(0)
            finally:
                spool.close()

        return StreamingResponse(
            records(), media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="kidra-token-chunks.jsonl"'},
            background=BackgroundTask(close_export),
        )

    @app.put("/api/config")
    async def config(request: Request):
        editable()
        data = await body(request)
        revision = state["revision"]
        candidate = validate_config(data.get("config"))
        # A blank UI key preserves an existing in-memory key; explicit clear_key clears it.
        old = {e["id"]: e for e in state["config"]["endpoints"]}
        for endpoint in candidate["endpoints"]:
            if endpoint.pop("clear_key", False):
                endpoint.pop("api_key", None)
            elif (
                not endpoint.get("api_key")
                and endpoint["id"] in old
                and old[endpoint["id"]].get("api_key")
            ):
                endpoint["api_key"] = old[endpoint["id"]]["api_key"]
        state.set_config(candidate, revision)
        return {"ok": True}

    @app.post("/api/run")
    async def start(request: Request):
        data = await body(request)
        task_ids = data.get("task_ids")
        if task_ids is not None and (
            not isinstance(task_ids, list)
            or any(not isinstance(t, str) for t in task_ids)
        ):
            raise ValueError("task_ids must be an array of task identifiers")
        seed_index = data.get("seed_index")
        if seed_index is not None:
            if type(seed_index) is not int:
                raise ValueError("seed_index must be an integer")
            sample_at(seed_index)
            if data.get("automatic"):
                raise ValueError(
                    "Filling an existing sample is only supported for a single run"
                )
        runner.start(
            single=not data.get("automatic", False),
            task_ids=task_ids,
            seed_index=seed_index,
            validate=data.get("validate"),
        )
        return {"ok": True}

    @app.post("/api/stop")
    async def stop():
        await runner.stop()
        return {"ok": True}

    @app.post("/api/pause")
    async def pause(request: Request):
        data = await body(request)
        runner.resume() if data.get("resume") else runner.pause()
        return {"ok": True}

    @app.post("/api/validate")
    async def validate(request: Request):
        data = await body(request)
        sample = data.get("sample") or sample_at(data.get("index", -1))
        compiler.compile(sample)
        builder = SampleBuilder(compiler, copy.deepcopy(state["config"]), client)
        return await builder.validators(sample)

    @app.middleware("http")
    async def no_cache_static(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
