"""FastAPI application factory for FireAI Pro 2.0 (drawing-understanding milestone).

Legacy v1 design endpoints are NOT mounted. They return 410 Gone with an
explanation: the v1 pipeline substituted synthetic buildings, used invalid
hydraulics, and reported failures as compliant (see docs/FIREAI_AUDIT.md).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from fireai import __version__
from fireai.api.routes import router
from fireai.api.security import BodySizeLimit, SecurityHeaders
from fireai.config import Settings, get_settings
from fireai.ingest.dwg import select_converter
from fireai.jobs.store import JobStore

log = logging.getLogger("fireai.api")
_STORE: JobStore | None = None
UI_PATH = Path(__file__).with_name("ui.html")

LEGACY_GONE = {
    "code": "ENDPOINT_RETIRED",
    "message": ("This v1 endpoint has been retired. The v1 design pipeline could substitute synthetic buildings, "
                "used unvalidated hydraulics, and could report failed stages as compliant. FireAI Pro 2.0 currently "
                "provides drawing understanding only: POST /api/v2/drawings."),
}
LEGACY_PATHS = [
    ("/api/generate", ["POST"]), ("/api/generate/upload", ["POST"]), ("/api/analyze", ["POST"]),
    ("/api/jobs", ["GET"]), ("/api/jobs/{rest:path}", ["GET", "POST"]),
    ("/api/improvement/{rest:path}", ["GET", "POST"]), ("/design", ["GET"]),
]


def current_store() -> JobStore:
    assert _STORE is not None, "app not initialised"
    return _STORE


def create_app(settings: Settings | None = None) -> FastAPI:
    global _STORE
    settings = settings or get_settings()
    _STORE = JobStore(settings)
    app = FastAPI(title="FireAI Pro — Drawing Understanding", version=f"2.0.0-dev ({__version__})",
                  description="Drawing ingestion -> normalized building model -> verification overlay. "
                              "No design, hydraulic, or compliance functionality in this milestone.")
    app.state.settings = settings
    if not settings.api_token:
        log.warning("FIREAI_API_TOKEN not set: API is UNAUTHENTICATED and not production-safe.")

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
                           allow_credentials=False)
    # multipart overhead allowance on top of the file limit
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_upload_bytes + 1024 * 1024)
    app.add_middleware(SecurityHeaders)
    app.include_router(router)

    @app.get("/", response_class=HTMLResponse)
    def ui():
        return HTMLResponse(UI_PATH.read_text(encoding="utf-8"))

    @app.get("/health")
    def health():
        checks = {}
        try:
            probe = settings.data_dir / ".probe"
            probe.write_text("ok"); probe.unlink()
            checks["data_dir_writable"] = True
        except Exception:
            checks["data_dir_writable"] = False
        try:
            with _STORE._conn() as c:
                c.execute("SELECT 1")
            checks["database"] = True
        except Exception:
            checks["database"] = False
        conv = select_converter(settings)
        ok = all(checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={
            "status": "ok" if ok else "degraded",
            "checks": checks,
            "authentication": "bearer-token" if settings.api_token else "NONE — not production-safe",
            "dwg_conversion": conv.name if conv else "unavailable (DWG uploads will fail with DWG_CONVERSION_UNAVAILABLE)",
            "capabilities": ["dxf_ingestion", "dwg_ingestion_via_converter", "building_model", "verification_overlay"],
            "not_provided": ["sprinkler_design", "hydraulic_calculations", "code_compliance", "permit_documents"],
            "legacy_v1_design_endpoints": "retired (410 Gone)",
        })

    def _gone(request: Request):
        return JSONResponse(status_code=410, content={"detail": LEGACY_GONE})

    for path, methods in LEGACY_PATHS:
        app.add_api_route(path, _gone, methods=methods, include_in_schema=False)
    return app
