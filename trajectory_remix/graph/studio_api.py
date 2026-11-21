"""Authenticated loopback REST studio, using the shared graph/job services."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Route

from .bulk import BulkService
from .flow import ProposalInput, SamplerRun
from .job_api import create_job_app
from .models import DTO, GraphSource, JsonValue, ProjectSettings, default_checklists
from .query_export import iter_jsonl
from .reviews import ReviewInput
from .static_files import RevalidatingStaticFiles
from .studio import Studio
from .validation import diagnose


class Edit(DTO):
    expected_revision: int = Field(ge=1)
    patch: dict[str, JsonValue]


class Replace(DTO):
    expected_revision: int = Field(ge=1)
    source: GraphSource


class SettingsEdit(DTO):
    expected_revision: int = Field(ge=1)
    settings: ProjectSettings


class Delete(DTO):
    expected_revision: int = Field(ge=1)


class Dependencies(Delete):
    after: list[str]


class EventInput(Delete):
    event: dict[str, JsonValue]


class PreviewInput(Delete):
    profile: str
    active_limit: int = Field(default=8, ge=1, le=256)
    seed: int = 0
    max_frames: int = Field(default=1000, ge=1, le=5000)
    source: GraphSource | None = None
    client_key: str = Field(default="", max_length=128)


class ImportInput(DTO):
    text: str


class SamplerEdit(Delete):
    matrix: dict[str, JsonValue]
    metric_options: dict[str, JsonValue] = Field(default_factory=dict)


class MetricAnnotation(Delete):
    value: JsonValue
    origin: Literal["manual_annotation", "export"] = "manual_annotation"
    profile_fingerprint: str | None = None
    compiler_version: str | None = None


class MetricQuery(DTO):
    query: dict[str, JsonValue] = Field(default_factory=dict)
    population: Literal["all", "accepted"] = "all"


class AgentCall(DTO):
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


def create_studio_app(
    path,
    *,
    artifact_root="/tmp/trajectory-remix-exports",
    mcp_allowed_origins=(),
    provider=None,
    active_limit=20,
):
    app = create_job_app(path, artifact_root=artifact_root, active_limit=active_limit)
    app.title = "Trajectory Remix — Graph Studio"
    studio = Studio(app.state.jobs, app.state.bulk, app.state.artifacts, app.state.worker)
    from .metrics import MetricService
    from .sampling import SamplerService

    sampler = app.state.sampler = SamplerService(app.state.jobs)
    metrics = app.state.metrics = MetricService(sampler)
    from .generation import GenerationService
    from .provider import OpenAITransport

    generation = app.state.generation = GenerationService(
        app.state.jobs, sampler, app.state.bulk, provider or OpenAITransport()
    )
    app.state.worker.handlers["run"] = generation.run
    app.state.worker.handlers["bulk:review_repair"] = generation.run
    app.state.worker.review_start = generation.start_review
    studio.generation = generation
    app.state.worker.bulk_handlers["metrics"] = metrics.calculate
    app.state.studio = studio
    app.state.worker.handlers["import"] = studio.import_job
    token = app.state.csrf = secrets.token_urlsafe(32)
    static = Path(__file__).resolve().parents[1] / "static"
    from ..mcp_server import StreamableHTTP
    from .agent import GraphAgent

    agent = GraphAgent(studio)
    mcp = StreamableHTTP(agent, mcp_allowed_origins)
    app.routes.append(Route("/mcp", mcp, methods=["GET", "POST", "DELETE", "OPTIONS"]))
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            if mcp.manager is None:
                yield
            else:
                async with mcp.manager.run():
                    yield

    app.router.lifespan_context = lifespan
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
    )

    @app.middleware("http")
    async def local_guard(request, call_next):
        if request.url.path.startswith("/api/"):
            if request.headers.get("x-remix-token") != token:
                return JSONResponse(
                    dict(message="Reload the dashboard for its local session token"),
                    status_code=403,
                )
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse(
                    dict(message="Cross-origin requests are blocked"), status_code=403
                )
            # Enforce a streaming body bound, including chunked uploads.
            buffer = bytearray()
            async for chunk in request.stream():
                buffer.extend(chunk)
                if len(buffer) > 40_000_000:
                    return JSONResponse(dict(message="Request exceeds 40 MB"), status_code=413)
            request._body = bytes(buffer)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(ValidationError)
    async def invalid_dto(request, error):
        return JSONResponse(
            dict(
                code="invalid_fields",
                errors=error.errors(include_context=False, include_input=False),
            ),
            status_code=422,
        )

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (static / "graph.html").read_text().replace("__REMIX_TOKEN__", token)

    app.mount("/static", RevalidatingStaticFiles(directory=static), name="static")
    router = APIRouter(prefix="/api/v1")

    @router.get("/sampler")
    def sampler_editor():
        return sampler.editor()

    @router.put("/sampler")
    def sampler_edit(body: SamplerEdit):
        return sampler.update(body.matrix, body.metric_options, body.expected_revision)

    @router.post("/sampler/proposals", status_code=201)
    def sample(body: ProposalInput):
        return sampler.propose(**body.model_dump())

    @router.get("/sampler/proposals/{ident}")
    def proposal(ident: str):
        return sampler.read(ident)

    @router.post("/sampler/runs", status_code=202)
    async def sampler_run(body: SamplerRun):
        if "draft" in studio.store.settings().settings.endpoint_roles:
            job = generation.start(
                [r.model_dump() for r in body.requests], body.instructions, body.barriers
            )
        else:
            job = sampler.run([r.model_dump() for r in body.requests], body.instructions)
        studio.worker.kick(job)
        return dict(job_id=job, status_url=f"/api/v1/jobs/{job}")

    from .prompt_migration import migrate
    from .prompts_api import add_prompt_routes
    from .tools_api import add_tool_routes

    migrate(studio.store)
    add_tool_routes(router, studio.store)
    add_prompt_routes(router)
    from . import usage

    app.include_router(usage.router(studio.store))

    @router.post("/generation/runs", status_code=202)
    async def generation_run(body: SamplerRun):
        job = generation.start(
            [r.model_dump() for r in body.requests], body.instructions, body.barriers
        )
        studio.worker.kick(job)
        return dict(job_id=job, status_url=f"/api/v1/jobs/{job}")

    @router.post("/reviews", status_code=202)
    async def review_run(body: ReviewInput):
        if body.trajectory is not None and body.expected_revision is None:
            raise ValueError("Selected-source review requires expected_revision")
        job = generation.start_review(**body.model_dump())
        studio.worker.kick(job)
        return dict(job_id=job, status_url=f"/api/v1/jobs/{job}")

    @router.put("/metrics/trajectories/{ident}/{key}")
    def annotate_metric(ident: str, key: str, body: MetricAnnotation):
        return metrics.annotate(ident, key=key, **body.model_dump())

    @router.post("/metrics/population")
    def population(body: MetricQuery):
        return metrics.population(body.query, body.population)

    @router.get("/metrics/trajectories/{ident}")
    def metric_values(ident: str):
        return metrics.view(
            studio.store.read(ident), sampler.editor()["configuration"]["definitions"]
        )

    @router.post("/metrics/calculate-missing", status_code=202)
    async def calculate_missing(body: MetricQuery):
        query = dict(body.query)
        if body.population == "accepted":
            raise ValueError("Use the explicit stage query for a calculation job")
        job = studio.bulk.start(query, "metrics", sampler.editor()["configuration"])
        studio.worker.kick(job)
        return dict(job_id=job, status_url=f"/api/v1/jobs/{job}")

    @router.get("/project")
    def project():
        return dict(
            id=studio.store.project_id,
            format="kidra-graph-project/1",
            generation_available="draft" in studio.store.settings().settings.endpoint_roles,
        )

    @router.get("/settings")
    def settings():
        return studio.settings()

    @router.put("/settings")
    def settings_update(body: SettingsEdit):
        return studio.update_settings(body.settings, body.expected_revision)

    @router.get("/settings/default-review-checklists")
    def default_review_checklists():
        """The packaged Core and Critical criteria, for the dashboard's reset action."""
        return dict(
            review_checklists={
                group: {key: item.model_dump(mode="json") for key, item in items.items()}
                for group, items in default_checklists().items()
            }
        )

    @router.get("/schema/{kind}")
    def schema(kind: Literal["source", "settings"]):
        return (GraphSource if kind == "source" else ProjectSettings).model_json_schema()

    @router.get("/trajectories")
    def listing(
        text: str | None = None,
        stage: int | None = None,
        split: str | None = None,
        tool: str | None = None,
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        query = {
            k: v
            for k, v in dict(text=text, stage=stage, split=split, tool=tool).items()
            if v is not None
        }
        return studio.listing(query, limit=limit, offset=offset)

    @router.get("/trajectories/export")
    def export_matches(
        text: str | None = None,
        stage: int | None = None,
        split: str | None = None,
        tool: str | None = None,
    ):
        query = {
            k: v
            for k, v in dict(text=text, stage=stage, split=split, tool=tool).items()
            if v is not None
        }
        BulkService._where(query)  # Validate before the stream starts.
        return StreamingResponse(
            iter_jsonl(studio.store, query),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="trajectories.jsonl"'},
        )

    @router.post("/diagnostics")
    def draft_diagnostics(body: GraphSource, settings_revision: int | None = Query(None, ge=1)):
        settings = studio.store.settings(settings_revision)
        return dict(
            source=body.to_dict(),
            source_hash=body.intrinsic_hash,
            settings_revision=settings.revision,
            diagnostics=jsonable_encoder(diagnose(body, settings.settings)),
        )

    @router.post("/trajectories", status_code=201)
    def create(body: GraphSource):
        return studio.present(studio.commands.create(body.to_dict()))

    @router.get("/trajectories/{ident}")
    def read(ident: str, revision: int | None = Query(None, ge=1)):
        return studio.read(ident, revision)

    @router.put("/trajectories/{ident}")
    def replace(ident: str, body: Replace):
        return studio.present(
            studio.commands.replace(ident, body.source.to_dict(), body.expected_revision)
        )

    @router.patch("/trajectories/{ident}")
    def patch(ident: str, body: Edit):
        return studio.present(studio.commands.patch(ident, body.patch, body.expected_revision))

    @router.delete("/trajectories/{ident}")
    def delete(ident: str, body: Delete):
        return studio.delete(ident, body.expected_revision)

    @router.post("/trajectories/{ident}/events/{event}")
    def add_event(ident: str, event: str, body: EventInput):
        return studio.present(
            studio.commands.add_event(ident, event, body.event, body.expected_revision)
        )

    @router.patch("/trajectories/{ident}/events/{event}")
    def patch_event(ident: str, event: str, body: Edit):
        return studio.present(
            studio.commands.patch_event(ident, event, body.patch, body.expected_revision)
        )

    @router.delete("/trajectories/{ident}/events/{event}")
    def delete_event(ident: str, event: str, body: Delete):
        return studio.present(studio.commands.delete_event(ident, event, body.expected_revision))

    @router.put("/trajectories/{ident}/events/{event}/dependencies")
    def dependencies(ident: str, event: str, body: Dependencies):
        return studio.present(
            studio.commands.set_dependencies(ident, event, body.after, body.expected_revision)
        )

    @router.get("/trajectories/{ident}/graph")
    def graph(ident: str):
        return studio.graph(ident)

    @router.get("/trajectories/{ident}/diagnostics")
    def diagnostics(ident: str):
        value = studio.read(ident)
        return dict(
            diagnostics=value["diagnostics"],
            settings_revision=value["diagnostics_settings_revision"],
        )

    @router.post("/trajectories/{ident}/preview")
    def preview(ident: str, body: PreviewInput):
        return studio.preview(
            ident,
            body.expected_revision,
            body.profile,
            active_limit=body.active_limit,
            seed=body.seed,
            max_frames=body.max_frames,
            source=body.source,
        ) | dict(client_key=body.client_key)

    @router.post("/imports")
    async def import_source(body: ImportInput):
        job = studio.start_import(body.text)
        return JSONResponse(dict(job_id=job, status_url=f"/api/v1/jobs/{job}"), status_code=202)

    @router.get("/job-summaries")
    def job_summaries(limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return studio.jobs.summaries(limit=limit, offset=offset)

    @router.get("/jobs/{job}/manifest")
    def manifest(job: str):
        with studio.artifacts.lease(job, "manifest.json") as stream:
            import json

            return json.load(stream)

    @router.get("/agent/guide/{topic}")
    def guide(topic: str):
        return agent.guide(topic)

    @app.get("/api/mcp/tools")
    def mcp_tools():
        return dict(tools=agent.tools())

    @app.post("/api/mcp/call")
    async def mcp_call(body: AgentCall):
        return await agent.call(body.name, body.arguments)

    app.include_router(router)
    return app
