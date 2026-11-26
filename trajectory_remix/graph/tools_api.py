"""REST route that renders a tool_schema listing for manual authoring."""

from pydantic import Field

from . import tool_listing
from .models import DTO


class ToolListing(DTO):
    trajectory_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    tools: list[str] = Field(max_length=500)


def add_tool_routes(router, store):
    @router.post("/tools/listing")
    def tool_listing_content(body: ToolListing):
        """A tool_schema listing of exactly the chosen tools; random extras join at compile time."""
        settings = store.settings().settings
        tools = {key: value.model_dump(mode="json") for key, value in settings.tools.items()}
        return tool_listing.listing(tools, body.trajectory_id, body.event_id, body.tools, 0)
