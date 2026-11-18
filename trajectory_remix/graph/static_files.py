"""Static file serving that makes browsers revalidate the studio's scripts."""

from fastapi.staticfiles import StaticFiles


class RevalidatingStaticFiles(StaticFiles):
    """Serve files with ``Cache-Control: no-cache``.

    Browsers still keep a copy, but ask the server on every load (the ETag makes an
    unchanged file a cheap 304), so an upgraded script is never served stale.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response
