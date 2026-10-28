from __future__ import annotations

import asyncio
import copy
import json
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from tempfile import TemporaryFile

from starlette.background import BackgroundTask

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Route

from .authoring import CommandError, TrajectoryCommands
from .compiler import CompileError, chunks
from .preview import MAX_PREVIEW_BYTES, MAX_PREVIEW_FRAMES, PreviewCache, PreviewLimit, bounded_json
from .migration import TokenizerMigration
from .dataset import (
    new_sample,
    invalidate_automation,
    parse_import,
    public_config,
    reject_constant,
    training_eligible,
)
from .llm import TAGS, SampleBuilder, validate_config
from .runner import Runner
from .scenarios import ScenarioRegistry, check_scenario_endpoints
from .scenario_runner import ScenarioRunner
from .mcp_server import StreamableHTTP
from .tools import ToolRegistry, default_registry
from .storage import DatasetState, DatasetStore, StorageConflict

MAX_BODY = 40_000_000


def create_app(dataset: Path, client=None, mcp_allowed_origins=(), tools_dir=None, scenarios_dir=None):
    store = DatasetStore(dataset)
    compiler, tool_diagnostics = store.configure_tools(ToolRegistry(tools_dir) if tools_dir is not None else default_registry())
    config = validate_config(json.loads(store.info()[3]))
    state = DatasetState(store, config)
    runner = Runner(state, compiler, dataset, client)
    scenario_runner = ScenarioRunner(runner, ScenarioRegistry(scenarios_dir), client)
    runner.scenario_runner = scenario_runner
    generation_mode = {"scenario": bool(scenario_runner.snapshot)}
    token = secrets.token_urlsafe(32)
    static = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        async def expire_previews():
            while True:
                await asyncio.sleep(60)
                commands.previews.purge()

        async with AsyncExitStack() as stack:
            if mcp_http.manager is not None:
                await stack.enter_async_context(mcp_http.manager.run())
            expiry = asyncio.create_task(expire_previews())
            try:
                yield
            finally:
                expiry.cancel()
                await asyncio.gather(expiry, return_exceptions=True)
                if migration.task is not None:
                    await asyncio.shield(migration.task)
                try:
                    await scenario_runner.stop()
                    await runner.stop()
                finally:
                    commands.tool_sessions.close()

    app = FastAPI(title="Trajectory Remix", lifespan=lifespan)
    app.state.dataset = state
    app.state.runner = runner
    app.state.scenario_runner = scenario_runner
    app.state.history = runner.history
    app.state.csrf = token
    commands = TrajectoryCommands(state, compiler, runner, client)
    app.state.compiler = compiler
    app.state.tool_diagnostics = tool_diagnostics
    app.state.commands = commands
    app.state.tool_sessions = commands.tool_sessions

    def activate_tokenizer(candidate, revision):
        nonlocal compiler
        compiler = candidate
        app.state.compiler = runner.compiler = scenario_runner.compiler = commands.compiler = candidate
        commands.previews = PreviewCache(candidate)
        runner.history.undo_stack.clear()
        runner.history.redo_stack.clear()
        runner.history.tip = revision
        runner.history.notice = "History cleared after tokenizer migration"

    migration = TokenizerMigration(store, runner, activate_tokenizer)
    app.state.tokenizer_migration = migration
    mcp_http = StreamableHTTP(commands, mcp_allowed_origins)
    app.routes.append(
        Route("/mcp", mcp_http, methods=["GET", "POST", "DELETE", "OPTIONS"])
    )
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
            if migration.running and request.method not in ("GET", "HEAD"):
                return JSONResponse(
                    {"detail": "Wait for tokenizer migration to finish", "code": "busy"}, status_code=409
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

    @app.exception_handler(PreviewLimit)
    async def preview_limit(request, error):
        return JSONResponse(
            {"detail": str(error), "code": "preview_limit"}, status_code=413
        )

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

    def editable(sample_id=None):
        if migration.running:
            raise HTTPException(409, "Wait for tokenizer migration to finish")
        if runner.status["running"] or scenario_runner.locked(sample_id):
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

    @app.get("/api/guidance")
    async def get_guidance(sample_id: str | None = None, scenario: str | None = None):
        return await commands.call("get_guidance", {"sample_id": sample_id, "scenario": scenario})

    @app.get("/api/state")
    async def get_state(
        offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
        query: str = "", selected_id: str | None = None,
    ):
        revision = state["revision"]
        active_runner = scenario_runner if generation_mode["scenario"] else runner
        total, samples = store.listing(query, offset, limit, dashboard=True)
        selected_index = store.samples.index_of(selected_id) if selected_id is not None else None
        return {
            "samples": samples,
            "sample_count": len(store.samples),
            "max_window_slots": compiler.max_window_slots,
            "slot_policy": compiler.slot_policy,
            "total": total,
            "offset": offset,
            "limit": limit,
            "query": query,
            "selected_index": selected_index,
            "can_resume_generation": scenario_runner.can_resume(selected_id),
            "config": public_config(state["config"]),
            "revision": revision,
            "history": runner.history.view(),
            "status": {**active_runner.status, "last_saved_revision": revision},
            "scenarios": scenario_runner.registry.listing(),
            "logs": list(active_runner.logs),
            "tags": TAGS,
            "tools": compiler.tools.schemas(invisible=True),
            "tool_diagnostics": tool_diagnostics,
            "tokenizer": {
                "sha256": compiler.fingerprint,
                "vocab": compiler.tokenizer.get_vocab_size(),
                "special": compiler.special,
            },
            "tokenizer_migration": dict(migration.status),
        }

    @app.post("/api/tokenizer/migrate", status_code=202)
    async def migrate_tokenizer(request: Request):
        data = await body(request)
        if runner.status["running"] or scenario_runner.status["running"] or migration.running:
            raise HTTPException(409, "Stop generation and wait for any tokenizer migration to finish")
        return migration.start(data, compiler)

    @app.get("/api/tokenizer/migration")
    async def tokenizer_migration_status():
        return dict(migration.status)

    @app.get("/search-guide", response_class=PlainTextResponse)
    async def search_guide():
        return (Path(__file__).parent / "guides" / "search.md").read_text(encoding="utf-8")

    @app.get("/api/samples/{index}")
    async def get_sample(index: int):
        revision = state["revision"]
        return JSONResponse(
            sample_at(index), headers={"X-Remix-Revision": str(revision)}
        )

    @app.post("/api/metadata/query")
    async def metadata_query(request: Request):
        return await commands.call("query_trajectories", await body(request))

    @app.post("/api/metadata/counts")
    async def metadata_counts(request: Request):
        return await commands.call("get_metadata_counts", await body(request))

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
        return {"tools": compiler.tools.schemas(invisible=True)}

    @app.get("/api/samples/{index}/sandbox/{tick}")
    async def get_sandbox(index: int, tick: int):
        result = await commands.call(
            "get_sandbox_state",
            {
                "sample_id": sample_at(index).get("id"),
                "tick": tick,
            },
        )
        return {"sandbox": result["sandbox"], "tool_calls": result["tool_calls"]}

    @app.post("/api/samples/{index}/tool-calls")
    async def add_tool_call(index: int, request: Request):
        editable(sample_at(index)["id"])
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
        editable(sample_at(index)["id"])
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
        invalidate_automation(sample, sample_at(index))
        updated[index] = sample
        revision = runner.history.commit(
            updated, f"Edit trajectory in dashboard: {sample.get('id', 'untitled')}"
        )
        return {"ok": True, "revision": revision}

    @app.delete("/api/samples/{index}")
    async def delete_sample(index: int, expected_revision: int | None = Query(None, ge=0)):
        editable(sample_at(index)["id"])
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
        preview = commands.previews.compile(sample, revision=state["revision"])
        result = {**preview.metadata(), "preview": {"available": True}}
        try:
            encoded = bounded_json(result, "Timeline metadata exceeds 4 MB; inspect a window/range with MCP get_window_history or preview_snapshots.")
        except PreviewLimit as error:
            return {
                "valid": True, "plan_id": preview.plan_id,
                "row_count": len(preview.plan["rows"]),
                "diagnostics": [], "diagnostic_count": len(preview.plan["diagnostics"]),
                "preview": {"available": False, "message": str(error)},
            }
        # Compilation success is independent of the initial snapshot's size.
        # Frames are requested separately with their own count/output budgets.
        return Response(encoded, media_type="application/json")

    def get_preview(plan_id):
        preview = commands.previews.get(plan_id)
        if preview is None:
            raise HTTPException(404, "Preview expired or evicted from the bounded cache; compile the sample again")
        return preview

    @app.get("/api/frame/{plan_id}/{tick}")
    async def frame(plan_id: str, tick: int):
        result = get_preview(plan_id).frame(tick)
        encoded = bounded_json(result, "Frame exceeds 4 MB; inspect selected windows with MCP preview_snapshots(windows=[...]) or paginated get_window_history.")
        return Response(encoded, media_type="application/json")

    @app.get("/api/frames/{plan_id}")
    async def frames(
        plan_id: str, start_tick: int = Query(ge=0), end_tick: int = Query(ge=0),
    ):
        preview = get_preview(plan_id)
        commands._range(preview.plan, start_tick, end_tick)
        if end_tick - start_tick + 1 > MAX_PREVIEW_FRAMES:
            raise PreviewLimit(f"Preview request exceeds {MAX_PREVIEW_FRAMES} frames; reduce end_tick")
        result, size = [], len(b'{"frames":[]}')
        for tick in range(start_tick, end_tick + 1):
            frame = preview.frame(tick)
            encoded = bounded_json(frame, "Frame exceeds 4 MB; inspect selected windows with MCP preview_snapshots(windows=[...]) or paginated get_window_history.")
            size += len(encoded) + bool(result)
            if size > MAX_PREVIEW_BYTES:
                raise PreviewLimit("Preview range exceeds 4 MB; request fewer frames")
            result.append(encoded)
        return Response(b'{"frames":[' + b','.join(result) + b']}', media_type="application/json")

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
                    if not training_eligible(sample):
                        continue
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
        incoming = data.get("config")
        if isinstance(incoming, dict) and isinstance(incoming.get("endpoints"), list) and all(isinstance(ep, dict) and isinstance(ep.get("id"), str) for ep in incoming["endpoints"]):
            for scenario in scenario_runner.registry.scenarios.values():
                check_scenario_endpoints(scenario.name, scenario.flow, incoming["endpoints"], content_first=scenario.flow.generation_mode == "content-first")
        candidate = validate_config(incoming)
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

    @app.get("/api/generation/analytics")
    async def generation_analytics():
        from .content_first import dataset_generation_metrics
        return {"modes": dataset_generation_metrics(state['samples'])}

    @app.post("/api/scenarios/run")
    async def start_scenario(request: Request):
        data = await body(request)
        scenario_runner.start(data.get("scenario"), data.get("max_parallel"), tags=data.get("tags"), batch_size=data.get("batch_size", 1))
        generation_mode["scenario"] = True
        return {"ok": True}

    @app.post("/api/scenarios/resume")
    async def resume_generation(request: Request):
        data = await body(request)
        if scenario_runner.status["running"] and scenario_runner.status["paused"] and data.get("sample_id") == scenario_runner.status["draft_id"]:
            scenario_runner.resume()
        else:
            try:
                scenario_runner.resume_saved(data.get("sample_id"), confirm_edited=data.get("confirm_edited") is True)
            except ValueError as error:
                if "edited since checkpoint" in str(error):
                    return JSONResponse({"detail": str(error), "confirmation_required": True}, status_code=409)
                raise
        generation_mode["scenario"] = True
        return {"ok": True}

    @app.post("/api/endpoints/test")
    async def test_endpoint(request: Request):
        data = await body(request)
        candidate = copy.deepcopy(state["config"])
        endpoint = data.get("endpoint")
        candidate["endpoints"] = [endpoint]
        candidate["tasks"] = []
        validate_config(candidate)
        endpoint = copy.deepcopy(endpoint)
        stored = next((e for e in state["config"]["endpoints"] if e["id"] == endpoint["id"]), {})
        if not endpoint.get("api_key") and not endpoint.get("clear_key"):
            endpoint["api_key"] = stored.get("api_key", "")
        from .llm import LLMClient
        try:
            if scenario_runner.status["running"]:
                async with scenario_runner.limit:
                    response = await (client or LLMClient()).complete(endpoint, [{"role": "user", "content": "Reply with OK."}])
            else:
                response = await (client or LLMClient()).complete(endpoint, [{"role": "user", "content": "Reply with OK."}])
            if not response.get("content"):
                raise ValueError("Endpoint returned no text")
            return {"ok": True, "model": endpoint["model"]}
        except Exception as error:
            return JSONResponse({"detail": f"Endpoint test failed: {scenario_runner.clean(str(error)) if isinstance(error, ValueError) else type(error).__name__}"}, status_code=422)

    @app.post("/api/preferences")
    async def preferences(request: Request):
        data = await body(request)
        value = data.get("api_retries")
        if type(value) is not int or not 0 <= value <= 20:
            raise ValueError("API retries must be an integer from 0 to 20")
        config = copy.deepcopy(state["config"])
        config.setdefault("single_generation", {})["api_retries"] = value
        state.set_config(config, state["revision"])
        return {"ok": True}

    @app.post("/api/samples/{index}/state")
    async def set_sample_state(index: int, request: Request):
        data = await body(request)
        level = data.get("level")
        if type(level) is not int or level not in range(-3, 4):
            raise ValueError("Unknown validation state")
        sample = copy.deepcopy(sample_at(index))
        if not isinstance(sample.get("metadata"), dict):
            sample["metadata"] = {}
        sample.setdefault("metadata", {})["validation_level"] = level
        updated = state["samples"].copy()
        updated[index] = sample
        runner.history.commit(updated, f"Set state: {sample['id']} → {level}")
        if sample["id"] == scenario_runner.status.get("draft_id") and hasattr(scenario_runner, "builder"):
            scenario_runner.expected_draft = copy.deepcopy(sample)
            scenario_runner.state_override = level
            scenario_runner.builder.sample["metadata"]["validation_level"] = level
            scenario_runner.persist()
        else:
            job_id = sample["metadata"].get("generation_job")
            if job_id:
                with store.connect() as db:
                    row = db.execute("SELECT snapshot FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
                    if row:
                        saved = json.loads(row[0]); saved["checkpoint"]["expected_draft"] = sample
                        saved["checkpoint"]["state_override"] = level
                        db.execute("UPDATE generation_jobs SET snapshot=? WHERE id=?", (json.dumps(saved), job_id))
        return {"ok": True}

    @app.post("/api/scenarios/log/clear")
    async def clear_generation_log():
        scenario_runner.logs.clear()
        runner.logs.clear()
        scenario_runner.persist()
        return {"ok": True}

    @app.get("/api/scenarios/job")
    async def scenario_job():
        return json.loads(scenario_runner.clean(scenario_runner.snapshot)) if scenario_runner.snapshot else {}

    @app.post("/api/samples/{index}/approve")
    async def approve(index: int):
        editable(sample_at(index)["id"])
        sample = copy.deepcopy(sample_at(index))
        compiler.compile(sample)
        sample.setdefault("metadata", {})["validation_level"] = 3
        updated = state["samples"].copy()
        updated[index] = sample
        runner.history.commit(updated, f"Human approve: {sample['id']}")
        return {"ok": True}

    @app.post("/api/run")
    async def start(request: Request):
        data = await body(request)
        if scenario_runner.status["running"]:
            raise ValueError("Scenario generation is already running")
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
        generation_mode["scenario"] = False
        runner.start(
            single=not data.get("automatic", False),
            task_ids=task_ids,
            seed_index=seed_index,
            validate=data.get("validate"),
        )
        return {"ok": True}

    @app.post("/api/stop")
    async def stop():
        if scenario_runner.status["running"]:
            await scenario_runner.stop()
        else:
            await runner.stop()
        return {"ok": True}

    @app.post("/api/pause")
    async def pause(request: Request):
        data = await body(request)
        active = scenario_runner if scenario_runner.status["running"] else runner
        active.resume() if data.get("resume") else active.pause()
        return {"ok": True}

    @app.post("/api/validate")
    async def validate(request: Request):
        data = await body(request)
        sample = data.get("sample") or sample_at(data.get("index", -1))
        editable(sample.get("id"))
        compiler.compile(sample)
        builder = SampleBuilder(compiler, copy.deepcopy(state["config"]), client)
        if scenario_runner.status["running"]:
            async with scenario_runner.limit:
                return await builder.validators(sample)
        return await builder.validators(sample)

    @app.middleware("http")
    async def no_cache_static(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
