"""REST route that renders a tool_schema listing for manual authoring."""

from pydantic import Field

from . import tool_listing
from .models import DTO


class ToolListing(DTO):
    trajectory_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    tools: list[str] = Field(max_length=500)
    # None uses the project's tool_schema_extra_chance.
    extra_tools: float | None = Field(default=None, ge=0, le=1)


def add_tool_routes(router, store):
    @router.post("/tools/listing")
    def tool_listing_content(body: ToolListing):
        """A tool_schema listing, identical to the one Assembly writes."""
        settings = store.settings().settings
        tools = {key: value.model_dump(mode="json") for key, value in settings.tools.items()}
        chance = settings.tool_schema_extra_chance if body.extra_tools is None else body.extra_tools
        return tool_listing.listing(tools, body.trajectory_id, body.event_id, body.tools, chance)
