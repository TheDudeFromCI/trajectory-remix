"""Authenticated loopback REST studio, using the shared graph/job services."""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Route

from .job_api import create_job_app
from .models import DTO, GraphSource, JsonValue, ProjectSettings
from .studio import Studio, result
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


class AgentCall(DTO):
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


def create_studio_app(path, *, artifact_root='/tmp/trajectory-remix-exports', mcp_allowed_origins=()):
    app = create_job_app(path, artifact_root=artifact_root)
    app.title = 'Trajectory Remix — Graph Studio'
    studio = Studio(app.state.jobs, app.state.bulk, app.state.artifacts, app.state.worker)
    app.state.studio = studio
    app.state.worker.handlers['import'] = studio.import_job
    token = app.state.csrf = secrets.token_urlsafe(32)
    static = Path(__file__).resolve().parents[1] / 'static'
    from .agent import GraphAgent
    from ..mcp_server import StreamableHTTP
    agent = GraphAgent(studio)
    mcp = StreamableHTTP(agent, mcp_allowed_origins)
    app.routes.append(Route('/mcp', mcp, methods=['GET', 'POST', 'DELETE', 'OPTIONS']))
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
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])

    @app.middleware('http')
    async def local_guard(request, call_next):
        if request.url.path.startswith('/api/'):
            if request.headers.get('x-remix-token') != token:
                return JSONResponse(dict(message='Reload the dashboard for its local session token'), status_code=403)
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse(dict(message='Cross-origin requests are blocked'), status_code=403)
            # Enforce a streaming body bound, including chunked uploads.
            buffer = bytearray()
            async for chunk in request.stream():
                buffer.extend(chunk)
                if len(buffer) > 40_000_000:
                    return JSONResponse(dict(message='Request exceeds 40 MB'), status_code=413)
            request._body = bytes(buffer)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValidationError)
    async def invalid_dto(request, error):
        return JSONResponse(dict(code='invalid_fields', errors=error.errors(include_context=False, include_input=False)), status_code=422)

    @app.get('/', response_class=HTMLResponse)
    def index():
        return (static / 'graph.html').read_text().replace('__REMIX_TOKEN__', token)

    app.mount('/static', StaticFiles(directory=static), name='static')
    router = APIRouter(prefix='/api/v1')

    @router.get('/project')
    def project():
        return dict(id=studio.store.project_id, format='kidra-graph-project/1', generation_available=False)

    @router.get('/settings')
    def settings():
        return studio.settings()

    @router.put('/settings')
    def settings_update(body: SettingsEdit):
        return studio.update_settings(body.settings, body.expected_revision)

    @router.get('/schema/{kind}')
    def schema(kind: Literal['source', 'settings']):
        return (GraphSource if kind == 'source' else ProjectSettings).model_json_schema()

    @router.get('/trajectories')
    def listing(text: str | None = None, stage: int | None = None, split: str | None = None,
                tool: str | None = None, limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0)):
        query = {k: v for k, v in dict(text=text, stage=stage, split=split, tool=tool).items() if v is not None}
        return studio.listing(query, limit=limit, offset=offset)

    @router.post('/diagnostics')
    def draft_diagnostics(body: GraphSource):
        return dict(source=body.to_dict(), source_hash=body.intrinsic_hash,
                    diagnostics=jsonable_encoder(diagnose(body, studio.store.settings().settings)))

    @router.post('/trajectories', status_code=201)
    def create(body: GraphSource):
        return result(studio.commands.create(body.to_dict()))

    @router.get('/trajectories/{ident}')
    def read(ident: str, revision: int | None = Query(None, ge=1)):
        return studio.read(ident, revision)

    @router.put('/trajectories/{ident}')
    def replace(ident: str, body: Replace):
        return result(studio.commands.replace(ident, body.source.to_dict(), body.expected_revision))

    @router.patch('/trajectories/{ident}')
    def patch(ident: str, body: Edit):
        return result(studio.commands.patch(ident, body.patch, body.expected_revision))

    @router.delete('/trajectories/{ident}')
    def delete(ident: str, body: Delete):
        return studio.delete(ident, body.expected_revision)

    @router.post('/trajectories/{ident}/events/{event}')
    def add_event(ident: str, event: str, body: EventInput):
        return result(studio.commands.add_event(ident, event, body.event, body.expected_revision))

    @router.patch('/trajectories/{ident}/events/{event}')
    def patch_event(ident: str, event: str, body: Edit):
        return result(studio.commands.patch_event(ident, event, body.patch, body.expected_revision))

    @router.delete('/trajectories/{ident}/events/{event}')
    def delete_event(ident: str, event: str, body: Delete):
        return result(studio.commands.delete_event(ident, event, body.expected_revision))

    @router.put('/trajectories/{ident}/events/{event}/dependencies')
    def dependencies(ident: str, event: str, body: Dependencies):
        return result(studio.commands.set_dependencies(ident, event, body.after, body.expected_revision))

    @router.get('/trajectories/{ident}/graph')
    def graph(ident: str):
        return studio.graph(ident)

    @router.get('/trajectories/{ident}/diagnostics')
    def diagnostics(ident: str):
        return dict(diagnostics=studio.read(ident)['diagnostics'])

    @router.post('/trajectories/{ident}/preview')
    def preview(ident: str, body: PreviewInput):
        return studio.preview(ident, body.expected_revision, body.profile, active_limit=body.active_limit,
                              seed=body.seed, max_frames=body.max_frames, source=body.source) | dict(client_key=body.client_key)

    @router.post('/imports')
    async def import_source(body: ImportInput):
        job = studio.start_import(body.text)
        return JSONResponse(dict(job_id=job, status_url=f'/api/v1/jobs/{job}'), status_code=202)

    @router.get('/job-summaries')
    def job_summaries(limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0)):
        with studio.store.connect() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,kind,state,progress,total,artifact FROM graph_jobs ORDER BY created DESC,id LIMIT ? OFFSET ?',
                (limit, offset))]

    @router.get('/jobs/{job}/manifest')
    def manifest(job: str):
        with studio.artifacts.lease(job, 'manifest.json') as stream:
            import json
            return json.load(stream)

    @router.get('/agent/guide/{topic}')
    def guide(topic: str):
        return agent.guide(topic)

    @app.get('/api/mcp/tools')
    def mcp_tools():
        return dict(tools=agent.tools())

    @app.post('/api/mcp/call')
    async def mcp_call(body: AgentCall):
        return await agent.call(body.name, body.arguments)

    app.include_router(router)
    return app
