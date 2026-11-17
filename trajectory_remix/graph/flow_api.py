"""REST routes for the Flow editor and the step-by-step walkthrough."""

from fastapi.responses import JSONResponse
from pydantic import Field

from . import flow_document, tool_listing
from .models import DTO, JsonValue
from .walkthrough import WalkCancel, WalkStart, WalkState, WalkStep, Walkthrough


class ToolListing(DTO):
    trajectory_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    tools: list[str] = Field(min_length=1, max_length=500)
    extra_tools: float = Field(default=0.0, ge=0, le=1)


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
    store = studio.store
    walker = app.state.walkthrough = Walkthrough(store, sampler, transport)

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
        return flow_document.read_document(store)

    @router.get("/generation/flow/default")
    def default_flow():
        """The packaged default flow. Loading it changes nothing until it is saved."""
        return flow_document.packaged_document()

    @router.put("/generation/flow")
    def save_flow(body: FlowSave):
        return flow_document.save(store, body.expected_revision, **_one(body))

    @router.post("/generation/flow/check")
    def check_flow(body: FlowDraft):
        return flow_document.check(**_one(body))

    @router.post("/tools/listing")
    def tool_listing_content(body: ToolListing):
        """A Show tools listing for manual authoring, identical to the flow step's."""
        tools = {
            key: value.model_dump(mode="json")
            for key, value in store.settings().settings.tools.items()
        }
        return tool_listing.listing(
            tools, body.trajectory_id, body.event_id, body.tools, body.extra_tools
        )

    @router.post("/walkthrough/start")
    def walkthrough_start(body: WalkStart):
        return walker.start(**body.model_dump())

    @router.post("/walkthrough/describe")
    def walkthrough_describe(body: WalkState, latest_settings: bool = False):
        return walker.describe(body.model_dump(mode="json"), latest_settings)

    @router.post("/walkthrough/step")
    async def walkthrough_step(body: WalkStep):
        return await walker.step(
            body.state.model_dump(mode="json"),
            body.mode,
            body.response,
            body.latest_settings,
            body.call_id,
        )

    @router.post("/walkthrough/cancel")
    def walkthrough_cancel(body: WalkCancel):
        return walker.cancel(body.call_id)
