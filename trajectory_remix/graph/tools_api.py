"""REST route for Show tools in manual authoring."""

from pydantic import Field

from . import tool_listing
from .models import DTO


class ToolListing(DTO):
    trajectory_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    tools: list[str] = Field(min_length=1, max_length=500)
    extra_tools: float = Field(default=0.0, ge=0, le=1)


def add_tool_routes(router, store):
    @router.post("/tools/listing")
    def tool_listing_content(body: ToolListing):
        """A Show tools listing, identical to the one Assembly writes."""
        tools = {
            key: value.model_dump(mode="json")
            for key, value in store.settings().settings.tools.items()
        }
        return tool_listing.listing(
            tools, body.trajectory_id, body.event_id, body.tools, body.extra_tools
        )
