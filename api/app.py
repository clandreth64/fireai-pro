"""
FireAI Pro — API Server  (api/app.py)
======================================
Drop this into your api/ folder, replacing the existing app.py.
Adds the /api/generate endpoint the format selector posts to,
plus status polling and artifact download.

Run locally:
  cd api && uvicorn app:app --reload --port 8000

Railway start command (Procfile):
  web: uvicorn api.app:app --host 0.0.0.0 --port $PORT
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Import FireAI Pro modules ─────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from fireai_orchestrator_v2 import FireAIOrchestrator
from fireai_drawing_engine   import FireAIDrawingEngine
from fireai_schemas.job_models import GenerateRequest, JobStatus, JobResult

log = logging.getLogger("fireai.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── App setup ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="FireAI Pro API",
    version="2.0.0",
    description="AI-powered fire sprinkler design system",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the format selector and any other static files from repo root
REPO_ROOT    = Path(__file__).parent.parent
OUTPUTS_DIR  = REPO_ROOT / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

# ── In-memory job store (swap for Redis/Postgres in prod) ─────────────────────

_jobs: dict[str, dict] = {}

def _set_job(job_id: str, **kwargs):
    if job_id not in _jobs:
        _jobs[job_id] = {"job_id": job_id, "created_at": datetime.utcnow().isoformat()}
    _jobs[job_id].update(kwargs)

def _get_job(job_id: str) -> dict:
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _jobs[job_id]


# ── Background worker ─────────────────────────────────────────────────────────

async def _run_job(job_id: str, request: "GenerateRequest"):
    """
    Full pipeline:
      1. Orchestrator  — parallel agents + NFPA 13 compliance loop
      2. Drawing engine — generates selected DXF sheets + PDF print set
    """
    job_output_dir = OUTPUTS_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 1: orchestrator ───────────────────────────────────────────
        _set_job(job_id, status="running", stage="agents",
                 message="Running parallel design agents...")
        log.info(f"[{job_id}] Starting orchestrator")

        orchestrator = FireAIOrchestrator()
        orch_result  = await orchestrator.run(
            project_context  = request.project_context,
            selected_formats = set(request.selected_formats or []),
        )

        _set_job(job_id, stage="drawings",
                 message="Generating construction drawings...",
                 orchestrator_result=orch_result)

        # ── Step 2: drawing engine ─────────────────────────────────────────
        log.info(f"[{job_id}] Starting drawing engine")

        drawing_engine = FireAIDrawingEngine(
            project           = request.project_context,
            cad_output        = orch_result.get("artifacts", {}).get("cad_layout")        or {},
            hydraulics_output = orch_result.get("artifacts", {}).get("hydraulics_report") or {},
            bracing_output    = orch_result.get("artifacts", {}).get("bracing_and_bom")   or {},
            compliance_result = type("CR", (), {
                "compliant":  orch_result.get("metadata", {}).get("compliant", False),
                "violations": [],
                "summary":    orch_result.get("metadata", {}).get("nfpa_summary", ""),
            })(),
        )

        # Only generate the sheets the designer selected
        selected_sheets = set(request.selected_sheets or [
            "sheet_fp00","sheet_fp10","sheet_fp20",
            "sheet_fp30","sheet_fp40","sheet_fp50","sheet_fp60",
        ])

        drawing_manifest = drawing_engine.generate_selected(
            output_dir      = str(job_output_dir),
            selected_sheets = selected_sheets,
            include_pdf     = "dwg_pdf" in (request.selected_formats or []),
            include_3d      = "dwg_3d"  in (request.selected_formats or []),
        )

        # ── Collect all published files ────────────────────────────────────
        all_files = (orch_result.get("published_files", []) +
                     [m["filename"] for m in drawing_manifest if not m.get("error")])

        _set_job(
            job_id,
            status          = "complete" if orch_result["metadata"]["compliant"] else "partial",
            stage           = "done",
            message         = "Complete" if orch_result["metadata"]["compliant"] else "Partial — human review required",
            compliant       = orch_result["metadata"]["compliant"],
            iterations_used = orch_result["metadata"]["iterations_used"],
            published_files = all_files,
            drawing_manifest= drawing_manifest,
            output_dir      = str(job_output_dir),
            completed_at    = datetime.utcnow().isoformat(),
            requires_human_review = orch_result.get("requires_human_review", False),
            frozen_violations     = orch_result["metadata"].get("frozen_violations", []),
        )

        log.info(f"[{job_id}] Done — {len(all_files)} file(s), compliant={orch_result['metadata']['compliant']}")

    except Exception as e:
        log.exception(f"[{job_id}] Job failed: {e}")
        _set_job(job_id, status="failed", stage="error", message=str(e),
                 completed_at=datetime.utcnow().isoformat())


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "FireAI Pro API", "version": "2.0.0", "status": "online"}

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/generate", response_model=dict, status_code=202)
async def generate(request: GenerateRequest, background_tasks: BackgroundTasks):
    """
    Queue a full FireAI Pro design job.

    Body (JSON):
      project_context   — full project definition dict (required)
      selected_sheets   — list of sheet keys e.g. ["sheet_fp10", "sheet_fp30"]
      selected_formats  — list of format keys e.g. ["dwg_pdf", "ifc", "hydraulics_json"]

    Returns immediately with job_id. Poll /api/jobs/{job_id} for status.
    """
    job_id = str(uuid.uuid4())[:8].upper()

    _set_job(job_id,
        status   = "queued",
        stage    = "queued",
        message  = "Job queued — starting shortly",
        project  = request.project_context.get("project_name", "Unnamed"),
        sheets   = request.selected_sheets,
        formats  = request.selected_formats,
    )

    background_tasks.add_task(_run_job, job_id, request)

    log.info(f"[{job_id}] Queued — project: {request.project_context.get('project_name')}")
    return {"job_id": job_id, "status": "queued", "poll_url": f"/api/jobs/{job_id}"}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    """Poll job status. Status values: queued → running → complete | partial | failed"""
    return _get_job(job_id.upper())


@app.get("/api/jobs/{job_id}/files")
async def list_files(job_id: str):
    """List all generated files for a completed job."""
    job = _get_job(job_id.upper())
    if job["status"] not in ("complete", "partial"):
        raise HTTPException(status_code=400, detail="Job not yet complete")
    return {
        "job_id":    job_id,
        "files":     job.get("published_files", []),
        "output_dir": job.get("output_dir"),
    }


@app.get("/api/jobs/{job_id}/download/{filename}")
async def download_file(job_id: str, filename: str):
    """Download a generated file by name."""
    job      = _get_job(job_id.upper())
    out_dir  = Path(job.get("output_dir", ""))
    file_path = out_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found for job {job_id}")

    return FileResponse(
        path         = str(file_path),
        filename     = filename,
        media_type   = "application/octet-stream",
    )


@app.get("/api/jobs")
async def list_jobs(limit: int = 20):
    """List recent jobs."""
    jobs = sorted(_jobs.values(), key=lambda j: j.get("created_at",""), reverse=True)
    return {"jobs": jobs[:limit], "total": len(_jobs)}


# ── Serve format selector UI ──────────────────────────────────────────────────

@app.get("/design")
async def serve_format_selector():
    selector_path = REPO_ROOT / "format_selector.html"
    if selector_path.exists():
        return FileResponse(str(selector_path), media_type="text/html")
    raise HTTPException(status_code=404, detail="format_selector.html not found in repo root")
