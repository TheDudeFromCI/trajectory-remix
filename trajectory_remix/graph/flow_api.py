"""REST routes for the Flow editor and the step-by-step walkthrough."""

from fastapi.responses import JSONResponse
from pydantic import Field

from . import flow_document
from .models import DTO, JsonValue
from .walkthrough import WalkStart, WalkState, WalkStep, Walkthrough


class FlowDraft(DTO):
    flow: dict[str, JsonValue] | None = None
    yaml: str | None = None


class FlowSave(FlowDraft):
    expected_revision: str = Field(min_length=1, max_length=128)


def _one(body):
    if (body.flow is None) == (body.yaml is None):
        raise ValueError("Send exactly one of flow or yaml")
    return dict(flow=body.flow, text=body.yaml)


def add_flow_routes(app, router, studio, sampler, transport):
    path = flow_document.flow_path(studio.store)
    walker = app.state.walkthrough = Walkthrough(studio.store, sampler, transport, path)

    @app.exception_handler(flow_document.FlowConflict)
    async def conflict(request, error):
        return JSONResponse(
            dict(
                code=error.code,
                message=str(error),
                expected_revision=error.expected_revision,
                current_revision=error.current_revision,
            ),
            status_code=409,
        )

    @router.get("/generation/flow")
    def generation_flow():
        # flow and flow_hash keep their original meaning for older clients.
        return flow_document.read_document(path)

    @router.put("/generation/flow")
    def save_flow(body: FlowSave):
        return flow_document.save(path, body.expected_revision, **_one(body))

    @router.post("/generation/flow/check")
    def check_flow(body: FlowDraft):
        return flow_document.check(**_one(body))

    @router.post("/walkthrough/start")
    def walkthrough_start(body: WalkStart):
        return walker.start(**body.model_dump())

    @router.post("/walkthrough/describe")
    def walkthrough_describe(body: WalkState):
        return walker.describe(body.model_dump(mode="json"))

    @router.post("/walkthrough/step")
    async def walkthrough_step(body: WalkStep):
        return await walker.step(body.state.model_dump(mode="json"), body.mode, body.response)
