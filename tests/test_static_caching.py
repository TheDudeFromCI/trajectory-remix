"""Studio scripts must be revalidated so an upgrade is never served from a stale cache."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trajectory_remix.graph.static_files import RevalidatingStaticFiles

STATIC = Path(__file__).parents[1] / "trajectory_remix" / "static"


def test_static_files_are_served_with_no_cache_and_revalidate_with_etag():
    app = FastAPI()
    app.mount("/static", RevalidatingStaticFiles(directory=STATIC), name="static")
    client = TestClient(app)
    first = client.get("/static/graph.js")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache"
    again = client.get("/static/graph.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
