"""Server for published apps in vibe-runtime: `uvicorn miira_vibe.runtime:app`.

- /healthz           liveness for the orchestrator and the image smoke test
- /api/*             the app's own FastAPI (`api/main.py` exporting `app`)
- everything else    `web/dist`, with index.html as SPA fallback

VIBE_APP_DIR (default /srv/app) is the read-only published snapshot.
"""

import importlib.util
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles


class SpaStaticFiles(StaticFiles):
    """Static files that fall back to index.html for client-side routes."""

    async def get_response(self, path: str, scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404 or "." in Path(path).name:
                raise
            return FileResponse(Path(str(self.directory)) / "index.html")


def load_api(app_dir: Path):
    api_dir = app_dir / "api"
    main = api_dir / "main.py"
    if not main.exists():
        return None
    sys.path.insert(0, str(api_dir))
    spec = importlib.util.spec_from_file_location("main", main)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["main"] = module
    spec.loader.exec_module(module)
    return module.app


def create_app(app_dir: str | Path | None = None) -> Starlette:
    root = Path(app_dir or os.environ.get("VIBE_APP_DIR", "/srv/app"))
    api = load_api(root)
    dist = root / "web" / "dist"

    async def healthz(request) -> JSONResponse:
        return JSONResponse({"ok": True, "api": api is not None, "web": (dist / "index.html").exists()})

    routes: list = [Route("/healthz", healthz)]
    if api is not None:
        routes.append(Mount("/api", app=api))
    if dist.is_dir():
        routes.append(Mount("/", app=SpaStaticFiles(directory=dist, html=True)))

    @asynccontextmanager
    async def lifespan(_):
        # Mounted apps don't get lifespan events from Starlette; run the API's here.
        if api is not None:
            async with api.router.lifespan_context(api):
                yield
        else:
            yield

    return Starlette(routes=routes, lifespan=lifespan)


def __getattr__(name: str):
    # Built lazily so importing the module (tests, tooling) doesn't require /srv/app.
    if name == "app":
        app = create_app()
        globals()["app"] = app
        return app
    raise AttributeError(name)
