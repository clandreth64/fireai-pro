"""
FireAI Pro — API Server v3  (api/app.py)
==========================================
Full enterprise version with:
  - File upload endpoint (/api/upload + /api/generate multipart)
  - Document processor integration
  - NFPA 13 design engine integration
  - Construction drawing generation
  - Job status polling + file download
  - Professional project intake UI at /
  - /api/analyze — fast form auto-population from drawing set

LAYERS:
  Layer 1 — SQLite job persistence + autonomous dispatcher
  Layer 2 — Context bus wired via updated fireai_orchestrator_v2.py
  Layer 3 — Nightly self-improvement loop
  Layer 4 — Document auto-analysis for UI form population
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from fireai_orchestrator_v2      import FireAIOrchestrator
from fireai_drawing_engine       import FireAIDrawingEngine
from fireai_document_processor   import handle_upload
from fireai_document_intelligence import handle_document_set
from fireai_nfpa13_design_engine import NFPA13DesignEngine
from fireai_project_extractor    import extract_project_context
from fireai_schemas.job_models   import GenerateRequest, JobStatus, JobResult

# Layer 1 — persistent job store + dispatcher
from job_store   import init_db, _set_job, _get_job, _list_jobs
from dispatcher  import start_dispatcher, stop_dispatcher

# Layer 3 — self-improvement loop
from agent_config_store import init_config_db
from improvement_loop   import (
    start_improvement_loop,
    stop_improvement_loop,
    record_job_performance,
)

# Layer 4 — fast document analysis for UI form auto-population
from document_analyzer import analyze_document_set

log = logging.getLogger("fireai.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

REPO_ROOT   = Path(__file__).parent.parent
OUTPUTS_DIR = REPO_ROOT / "outputs"
UPLOADS_DIR = REPO_ROOT / "uploads"
OUTPUTS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)


# ── Lifespan: init databases + start all background services ──────────────────

@asynccontextmanager
async def lifespan(app):
    init_db()
    init_config_db()
    dispatcher_task = asyncio.create_task(start_dispatcher())
    improve_task    = asyncio.create_task(start_improvement_loop())
    log.info("FireAI Pro started — dispatcher + nightly improvement loop active")
    yield
    stop_dispatcher()
    stop_improvement_loop()
    dispatcher_task.cancel()
    improve_task.cancel()
    try:
        await asyncio.gather(dispatcher_task, improve_task, return_exceptions=True)
    except Exception:
        pass
    log.info("FireAI Pro shut down cleanly")


app = FastAPI(
    title="FireAI Pro",
    version="3.0.0",
    description="Enterprise fire sprinkler design system",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ─── Background job runner ────────────────────────────────────────────────────

async def _run_job(job_id: str, project_context: dict,
                   selected_sheets: list, selected_formats: list,
                   geometry: dict | None = None):
    """
    Full pipeline:
      1. NFPA 13 design engine  — compute full design from geometry
      2. Orchestrator           — AI agents validate and enhance
      3. Drawing engine         — generate DXF construction sheets
    """
    job_output_dir = OUTPUTS_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 1: NFPA 13 design engine ────────────────────────────────────
        _set_job(job_id, status="running", stage="design",
                 message="Running NFPA 13 design engine...")
        log.info(f"[{job_id}] Running NFPA 13 design engine")

        design_engine = NFPA13DesignEngine(geometry or {}, project_context)
        design_output = design_engine.design()
        log.info(f"[{job_id}] Design engine complete — "
                 f"{len(design_output.get('sprinkler_placements',[]))} sprinklers | "
                 f"{design_output.get('flow_demand',0):.0f} gpm @ "
                 f"{design_output.get('required_pressure',0):.1f} psi")

        # ── Step 2: AI orchestrator ───────────────────────────────────────────
        _set_job(job_id, stage="agents", message="Running parallel AI design agents...")
        log.info(f"[{job_id}] Starting orchestrator")

        orchestrator = FireAIOrchestrator()
        orch_result  = await orchestrator.run(
            project_context  = project_context,
            selected_formats = set(selected_formats),
        )

        # Merge — design engine always wins over AI agent outputs
        if design_output:
            artifacts = orch_result.setdefault("artifacts", {})

            cad_out = artifacts.get("cad_layout") or {}
            for key in ["sprinkler_placements","pipe_sections","valves","equipment",
                        "walls","columns","rooms","hangers"]:
                if design_output.get(key):
                    cad_out[key] = design_output[key]
            cad_out["dxf_ready"] = design_output.get("dxf_ready", cad_out.get("dxf_ready", False))
            cad_out["ifc_ready"] = design_output.get("ifc_ready", cad_out.get("ifc_ready", False))
            artifacts["cad_layout"] = cad_out

            hyd_out = {}
            for key in ["static_pressure","residual_pressure","required_pressure",
                        "pressure_delta","flow_demand","density_area","demand_curve",
                        "remote_area_calcs","compliant"]:
                if design_output.get(key) is not None:
                    hyd_out[key] = design_output[key]
                elif artifacts.get("hydraulics_report", {}).get(key) is not None:
                    hyd_out[key] = artifacts["hydraulics_report"][key]
            artifacts["hydraulics_report"] = hyd_out

            brc_out = {}
            for key in ["hanger_schedule","sway_braces","seismic_zone","bom","total_material_cost"]:
                if design_output.get(key):
                    brc_out[key] = design_output[key]
                elif artifacts.get("bracing_and_bom", {}).get(key):
                    brc_out[key] = artifacts["bracing_and_bom"][key]
            artifacts["bracing_and_bom"] = brc_out

            dm = design_output.get("design_metadata", {})
            orch_result.setdefault("metadata", {}).update({
                "total_sprinklers":   len(design_output.get("sprinkler_placements", [])),
                "total_pipe_ft":      dm.get("total_pipe_ft", 0),
                "floor_area_sf":      dm.get("floor_area_sf", 0),
                "hazard_class":       dm.get("hazard_class", ""),
                "zones":              dm.get("zones", []),
                "geometry_synthetic": dm.get("geometry_synthetic", False),
                "nfpa_references":    dm.get("nfpa_references", []),
                "compliance_flags":   dm.get("compliance_flags", []),
            })

        _set_job(job_id, stage="drawings",
                 message="Generating construction drawings...",
                 orchestrator_result=orch_result)

        # ── Step 3: Drawing engine ────────────────────────────────────────────
        log.info(f"[{job_id}] Starting drawing engine")

        artifacts   = orch_result.get("artifacts", {})
        cad_out     = artifacts.get("cad_layout")        or design_output or {}
        hyd_out     = artifacts.get("hydraulics_report") or {}
        brc_out     = artifacts.get("bracing_and_bom")   or {}
        compliance  = type("CR", (), {
            "compliant":  orch_result.get("metadata", {}).get("compliant", False),
            "violations": [],
            "summary":    orch_result.get("metadata", {}).get("nfpa_summary", ""),
        })()

        drawing_engine = FireAIDrawingEngine(
            project           = project_context,
            cad_output        = cad_out,
            hydraulics_output = hyd_out,
            bracing_output    = brc_out,
            compliance_result = compliance,
        )

        selected_sheet_set = set(selected_sheets) or {
            "sheet_fp00","sheet_fp10","sheet_fp20","sheet_fp30",
            "sheet_fp40","sheet_fp50","sheet_fp60",
        }
        drawing_manifest = drawing_engine.generate_selected(
            output_dir      = str(job_output_dir),
            selected_sheets = selected_sheet_set,
            include_pdf     = "dwg_pdf"  in (selected_formats or []),
            include_3d      = "dwg_3d"   in (selected_formats or []),
        )

        all_files = (orch_result.get("published_files", []) +
                     [m["filename"] for m in drawing_manifest if not m.get("error")])

        compliant        = orch_result["metadata"]["compliant"]
        iterations_used  = orch_result["metadata"]["iterations_used"]
        total_sprinklers = len(cad_out.get("sprinkler_placements", []))
        total_pipe_ft    = sum(s.get("length", 0) for s in cad_out.get("pipe_sections", []))

        _set_job(
            job_id,
            status            = "complete" if compliant else "partial",
            stage             = "done",
            message           = f"Complete — {total_sprinklers} sprinklers, "
                                f"{total_pipe_ft:.0f}ft pipe, "
                                f"{iterations_used} compliance iteration(s)",
            compliant         = compliant,
            iterations_used   = iterations_used,
            total_sprinklers  = total_sprinklers,
            total_pipe_ft     = round(total_pipe_ft, 1),
            published_files   = all_files,
            drawing_manifest  = drawing_manifest,
            output_dir        = str(job_output_dir),
            completed_at      = datetime.utcnow().isoformat(),
            requires_human_review = orch_result.get("requires_human_review", False),
            frozen_violations     = orch_result["metadata"].get("frozen_violations", []),
        )

        # Layer 3: log performance for tonight's improvement cycle
        try:
            record_job_performance(_get_job(job_id))
        except Exception as e:
            log.warning(f"[{job_id}] Performance logging failed (non-fatal): {e}")

        log.info(f"[{job_id}] Done — {len(all_files)} file(s), compliant={compliant}, "
                 f"{total_sprinklers} sprinklers")

    except Exception as e:
        log.exception(f"[{job_id}] Job failed: {e}")
        _set_job(job_id, status="failed", stage="error", message=str(e),
                 completed_at=datetime.utcnow().isoformat())


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_ui():
    ui_path = REPO_ROOT / "fireai_upload_ui.html"
    if ui_path.exists():
        return FileResponse(str(ui_path), media_type="text/html")
    return {"service": "FireAI Pro API", "version": "3.0.0"}

@app.get("/design")
async def serve_format_selector():
    p = REPO_ROOT / "format_selector.html"
    if p.exists():
        return FileResponse(str(p), media_type="text/html")
    raise HTTPException(404, "format_selector.html not found")

@app.get("/health")
async def health():
    return {
        "status":           "ok",
        "timestamp":        datetime.utcnow().isoformat(),
        "dispatcher":       True,
        "improvement_loop": True,
    }


# ── Form auto-population ──────────────────────────────────────────────────────

@app.post("/api/analyze")
async def analyze_documents(files: List[UploadFile] = File(...)):
    """
    Fast document scan — reads the first 3 pages of each file and returns
    project parameters (name, address, occupancy, AHJ, area, seismic zone, etc.)
    to auto-populate the UI form. Runs in ~15 seconds.
    Full geometry extraction happens later in /api/generate/upload.
    """
    file_list = []
    for f in files:
        file_bytes = await f.read()
        file_list.append({"bytes": file_bytes, "filename": f.filename or "upload.pdf"})

    log.info("Analyzing %d file(s) for project parameters", len(file_list))
    params = await analyze_document_set(file_list)
    return params


# ── Design job endpoints ──────────────────────────────────────────────────────

@app.post("/api/generate", status_code=202)
async def generate(request: GenerateRequest, background_tasks: BackgroundTasks):
    """Queue a design job — JSON body, no file upload."""
    job_id  = str(uuid.uuid4())[:8].upper()
    ctx     = request.project_context
    sheets  = list(request.validated_sheets())
    formats = list(request.validated_formats())
    _set_job(job_id,
             status="queued", stage="queued",
             message="Job queued — starting shortly",
             project=ctx.get("project_name", "Unnamed"),
             project_context=ctx,
             selected_sheets=sheets,
             selected_formats=formats,
             geometry={})
    background_tasks.add_task(_run_job, job_id, ctx, sheets, formats, {})
    log.info(f"[{job_id}] Queued — {ctx.get('project_name')}")
    return {"job_id": job_id, "status": "queued", "poll_url": f"/api/jobs/{job_id}"}


@app.post("/api/generate/upload", status_code=202)
async def generate_upload(
    background_tasks: BackgroundTasks,
    files:            List[UploadFile] = File(...),
    project_context:  str = Form(...),
    selected_sheets:  str = Form("[]"),
    selected_formats: str = Form("[]"),
):
    """Queue a design job from a full construction document set."""
    job_id  = str(uuid.uuid4())[:8].upper()
    ctx     = json.loads(project_context)
    sheets  = json.loads(selected_sheets)
    formats = json.loads(selected_formats)

    file_names = [f.filename for f in files]
    log.info(f"[{job_id}] Received {len(files)} document(s): {', '.join(file_names)}")

    # IMPORTANT: status is "processing" (NOT "queued") so the autonomous
    # dispatcher in dispatcher.py — which polls for status='queued' — cannot
    # grab this job mid-extraction. The upload handler below runs extraction
    # to completion and then calls _run_job() directly with full geometry.
    # Changing this single value back to "queued" reintroduces a race that
    # crashes the design engine with empty inputs.
    _set_job(job_id,
             status="processing", stage="doc_analysis",
             message=f"Received {len(files)} document(s) — analyzing...",
             project=ctx.get("project_name", "Unnamed"),
             project_context=ctx,
             selected_sheets=sheets,
             selected_formats=formats,
             document_count=len(files))

    file_list = []
    for f in files:
        file_bytes = await f.read()
        file_list.append({"bytes": file_bytes, "filename": f.filename or "upload.pdf"})

    async def run_with_documents():
        try:
            import tempfile, os

            # ── STAGE 1: PROJECT CONTEXT EXTRACTION ──────────────────────────
            # For every uploaded PDF, run the project extractor to fill in all
            # design parameters (occupancy, construction type, areas, hazard,
            # structural framing, codes, rooms, etc.).
            #
            # Priority: fields already in ctx (user-entered in UI) are never
            # overwritten. Extracted values fill in gaps only.
            _set_job(job_id, stage="doc_analysis",
                     message=f"Reading {len(file_list)} document(s) for project data...")

            for doc in file_list:
                fname = doc["filename"].lower()
                # Only extract from PDFs (DXFs/IFCs go straight to geometry)
                if not fname.endswith(".pdf"):
                    continue
                # Write to temp file so extractor can read it
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(doc["bytes"])
                    tmp_path = tmp.name
                try:
                    _set_job(job_id, stage="doc_analysis",
                             message=f"Extracting project data from {doc['filename']}...")
                    extracted = await extract_project_context(tmp_path, run_vision=True)

                    # Merge into ctx — extracted values fill in only what's missing.
                    # Numeric 0 / 0.0 are also treated as "empty" so that frontend
                    # placeholder zeros (e.g. total_area=0) get correctly overwritten
                    # by real extracted values from the drawings.
                    filled = 0
                    for k, v in extracted.items():
                        if v is None or v == "" or v == [] or v == {}:
                            continue
                        if k not in ctx or ctx[k] in (None, "", [], {}, 0, 0.0):
                            ctx[k] = v
                            filled += 1
                    log.info(f"[{job_id}] Extracted {filled} fields from {doc['filename']}")
                finally:
                    try: os.unlink(tmp_path)
                    except: pass

            log.info(f"[{job_id}] Context after extraction: "                     f"project='{ctx.get('project_name','?')}' "                     f"area={ctx.get('total_area','?')} "                     f"hazard={ctx.get('warehouse_hazard','?')}")

            # ── STAGE 2: BUILDING GEOMETRY EXTRACTION ─────────────────────────
            # Extract room boundaries, walls, structural grid from the floor plan.
            # This produces the geometry dict used by NFPA13DesignEngine.
            _set_job(job_id, stage="doc_analysis",
                     message="Extracting building geometry...")

            if len(file_list) == 1:
                geometry = await handle_upload(
                    file_list[0]["bytes"], file_list[0]["filename"], ctx)
                log.info(f"[{job_id}] Single doc: "                         f"{len(geometry.get('rooms',[]))} rooms, "                         f"{len(geometry.get('walls',[]))} walls")
            else:
                geometry = await handle_document_set(file_list, ctx)
                log.info(f"[{job_id}] Doc set ({len(file_list)} files): "                         f"{len(geometry.get('rooms',[]))} rooms")
                ws = geometry.get("water_supply", {})
                if ws.get("static_pressure_psi"):   ctx["static_pressure"]   = ws["static_pressure_psi"]
                if ws.get("residual_pressure_psi"): ctx["residual_pressure"] = ws["residual_pressure_psi"]
                if ws.get("flow_gpm"):              ctx["water_supply_flow"] = ws["flow_gpm"]
                if geometry.get("spec", {}).get("pipe_material"): ctx.setdefault("pipe_material", geometry["spec"]["pipe_material"])
                if geometry.get("spec", {}).get("seismic_zone"):  ctx.setdefault("seismic_zone",  geometry["spec"]["seismic_zone"])

            # Rooms from geometry extractor override rooms from project extractor
            # (geometry extractor has actual floor plan coordinates)
            if geometry.get("rooms") and not ctx.get("_rooms_from_user"):
                ctx["rooms"] = geometry["rooms"]

            await _run_job(job_id, ctx, sheets, formats, geometry)

        except Exception as e:
            log.exception(f"[{job_id}] Document processing failed: {e}")
            _set_job(job_id, status="failed", stage="error", message=str(e),
                     completed_at=datetime.utcnow().isoformat())

    background_tasks.add_task(run_with_documents)
    return {"job_id": job_id, "status": "queued",
            "poll_url": f"/api/jobs/{job_id}",
            "document_count": len(files)}


# ── Job status endpoints ──────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return _get_job(job_id)

@app.get("/api/jobs/{job_id}/files")
async def list_files(job_id: str):
    job = _get_job(job_id)
    if job["status"] not in ("complete", "partial"):
        raise HTTPException(400, "Job not yet complete")
    return {"job_id": job_id, "files": job.get("published_files", []),
            "output_dir": job.get("output_dir")}

@app.get("/api/jobs/{job_id}/download/{filename}")
async def download_file(job_id: str, filename: str):
    job   = _get_job(job_id)
    fpath = Path(job.get("output_dir", "")) / filename
    if not fpath.exists():
        raise HTTPException(404, f"{filename} not found")
    return FileResponse(str(fpath), filename=filename, media_type="application/octet-stream")

@app.get("/api/jobs")
async def list_jobs(limit: int = 20):
    jobs = _list_jobs(limit=limit)
    return {"jobs": jobs, "total": len(jobs)}


# ── Layer 3: improvement loop endpoints ──────────────────────────────────────

@app.get("/api/improvement/status")
async def improvement_status():
    """Show current agent prompt versions and performance summaries."""
    from agent_config_store import get_all_agent_summaries, get_config_history
    summaries = get_all_agent_summaries(days=7)
    histories = {
        aid: get_config_history(aid, limit=3)
        for aid in ["cad","hydraulics","routing","bracing","ahj"]
    }
    return {"performance": summaries, "config_history": histories}

@app.post("/api/improvement/run")
async def trigger_improvement(background_tasks: BackgroundTasks):
    """Manually trigger the improvement cycle without waiting for 2 AM."""
    from improvement_loop import run_improvement_cycle
    background_tasks.add_task(run_improvement_cycle)
    return {"status": "queued", "message": "Improvement cycle started in background"}

@app.post("/api/improvement/rollback/{agent_id}")
async def rollback_agent(agent_id: str):
    """Roll back a specific agent to its previous prompt version."""
    from agent_config_store import rollback_config
    ok = rollback_config(agent_id)
    if not ok:
        raise HTTPException(400, f"No previous version for {agent_id}")
    return {"status": "rolled_back", "agent_id": agent_id}
