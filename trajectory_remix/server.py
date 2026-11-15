"""Public application factory for graph projects only."""
from .graph.studio_api import create_studio_app


def create_app(path, *, mcp_allowed_origins=(), **kwargs):
    return create_studio_app(path, mcp_allowed_origins=mcp_allowed_origins, **kwargs)
