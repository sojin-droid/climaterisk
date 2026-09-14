"""FastAPI application entry point.

Run with: ``uv run uvicorn climaterisk.api.main:app --reload``
(the ``run.command`` launcher does this with host/port from settings).
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from climaterisk import __version__
from climaterisk.api.routers import data, libraries, report, run, session, transition
from climaterisk.config import get_settings
from climaterisk.logger import get_logger
from climaterisk.runs.manager import WorkerCapacityError

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title="climaterisk", version=__version__)

    @app.exception_handler(WorkerCapacityError)
    async def _worker_busy(_request: Request, exc: WorkerCapacityError) -> JSONResponse:
        """Return 503 (retryable) when all worker slots are busy, not an opaque 500."""
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)}
        )

    # The Vite dev server proxies /api, but allow direct localhost calls too.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = APIRouter(prefix="/api")

    @api.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness probe used by ``run.command``."""
        return {"status": "ok", "service": "climaterisk", "version": app.version}

    @api.get("/hazard-catalog", tags=["hazard"])
    def hazard_catalog() -> dict[str, object]:
        """The local CLIMADA-ready hazard catalog the worker resolves from (Data-API fallback)."""
        from climaterisk.data.hazard_catalog import read_catalog

        return read_catalog()

    @api.get("/status/korea", tags=["status"])
    def status_korea() -> dict[str, object]:
        """현황 체크 — KMA archives on disk, KOR catalog layers, credentials (by name), 1 km grades.

        A reading of local state only: nothing is parsed with CLIMADA and no value is inferred
        (``climaterisk.data.korea_status``).
        """
        from climaterisk.data.korea_status import korea_status

        return korea_status()

    api.include_router(session.router)
    api.include_router(run.router)
    api.include_router(transition.router)
    api.include_router(report.router)
    api.include_router(libraries.router)
    api.include_router(data.router)
    app.include_router(api)

    logger.info("climaterisk API ready on %s:%d", settings.backend_host, settings.backend_port)
    return app


app = create_app()
