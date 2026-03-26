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
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from fireai_orchestrator_v2      import FireAIOrchestrator
from fireai_drawing_engine       import FireAIDrawingEngine
from fireai_document_processor   import handle_upload
from fireai_document_intelligence import handle_document_set
from fireai_nfpa13_design_engine import NFPA13DesignEngine
from fireai_schemas.job_models   import GenerateRequest, JobStatus, JobResult

log = logging.getLogger("fireai.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

REPO_ROOT   = Path(__file__).parent.parent
OUTPUTS_DIR = REPO_ROOT / "outputs"
UPLOADS_DIR = REPO_ROOT / "uploads"
OUTPUTS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="FireAI Pro", version="3.0.0",
              description="Enterprise fire sprinkler design system")

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# In-memory job store
_jobs: dict[str, dict] = {}

def _set_job(job_id, **kw):
    if job_id not in _jobs:
        _jobs[job_id] = {"job_id": job_id, "created_at": datetime.utcnow().isoformat()}
    _jobs[job_id].update(kw)

def _get_job(job_id):
    job_id = job_id.upper()
    if job_id not in _jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    return _jobs[job_id]


# ─── Background job runner ────────────────────────────────────────────────────

async def _run_job(job_id: str, project_context: dict,
                   selected_sheets: list, selected_formats: list,
                   geometry: dict | None = None):
    """
    Full pipeline:
      1. NFPA 13 design engine  — if geometry provided, compute full design
      2. Orchestrator           — AI agents validate and enhance the design
      3. Drawing engine         — generate DXF construction sheets
    """
    job_output_dir = OUTPUTS_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 1: NFPA 13 design engine ────────────────────────────────────
        # Runs always — uses extracted geometry if available, synthetic if not
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

        # Merge design engine outputs into orchestrator results.
        # Design engine output ALWAYS wins — it is based on real engineering math
        # (ESFR criteria, Hazen-Williams, actual geometry). AI agent outputs are
        # used only where the design engine produced nothing.
        if design_output:
            artifacts = orch_result.setdefault("artifacts", {})

            # ── CAD / geometry — design engine always wins ────────────────────
            cad_out = artifacts.get("cad_layout") or {}
            for key in ["sprinkler_placements","pipe_sections","valves","equipment",
                        "walls","columns","rooms","hangers"]:
                if design_output.get(key):          # design engine has it → use it
                    cad_out[key] = design_output[key]
            cad_out["dxf_ready"] = design_output.get("dxf_ready", cad_out.get("dxf_ready", False))
            cad_out["ifc_ready"] = design_output.get("ifc_ready", cad_out.get("ifc_ready", False))
            artifacts["cad_layout"] = cad_out

            # ── Hydraulics — design engine always wins ────────────────────────
            # The AI hydraulics agent does not know the system is ESFR and will
            # produce wrong values. The design engine calculated correct ESFR demand.
            hyd_out = {}
            for key in ["static_pressure","residual_pressure","required_pressure",
                        "pressure_delta","flow_demand","density_area","demand_curve",
                        "remote_area_calcs","compliant"]:
                if design_output.get(key) is not None:
                    hyd_out[key] = design_output[key]   # design engine value
                elif artifacts.get("hydraulics_report", {}).get(key) is not None:
                    hyd_out[key] = artifacts["hydraulics_report"][key]  # AI fallback
            artifacts["hydraulics_report"] = hyd_out

            # ── Bracing / BOM — design engine always wins ─────────────────────
            brc_out = {}
            for key in ["hanger_schedule","sway_braces","seismic_zone","bom","total_material_cost"]:
                if design_output.get(key):
                    brc_out[key] = design_output[key]
                elif artifacts.get("bracing_and_bom", {}).get(key):
                    brc_out[key] = artifacts["bracing_and_bom"][key]
            artifacts["bracing_and_bom"] = brc_out

            # ── Propagate design metadata to orchestrator metadata ─────────────
            dm = design_output.get("design_metadata", {})
            orch_result.setdefault("metadata", {}).update({
                "total_sprinklers":     len(design_output.get("sprinkler_placements", [])),
                "total_pipe_ft":        dm.get("total_pipe_ft", 0),
                "floor_area_sf":        dm.get("floor_area_sf", 0),
                "hazard_class":         dm.get("hazard_class", ""),
                "zones":                dm.get("zones", []),
                "geometry_synthetic":   dm.get("geometry_synthetic", False),
                "nfpa_references":      dm.get("nfpa_references", []),
                "compliance_flags":     dm.get("compliance_flags", []),
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

        # ── Collect results ───────────────────────────────────────────────────
        all_files = (orch_result.get("published_files", []) +
                     [m["filename"] for m in drawing_manifest if not m.get("error")])

        compliant       = orch_result["metadata"]["compliant"]
        iterations_used = orch_result["metadata"]["iterations_used"]
        total_sprinklers= len(cad_out.get("sprinkler_placements", []))
        total_pipe_ft   = sum(s.get("length", 0) for s in cad_out.get("pipe_sections", []))

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
        log.info(f"[{job_id}] Done — {len(all_files)} file(s), compliant={compliant}, "
                 f"{total_sprinklers} sprinklers")

    except Exception as e:
        log.exception(f"[{job_id}] Job failed: {e}")
        _set_job(job_id, status="failed", stage="error", message=str(e),
                 completed_at=datetime.utcnow().isoformat())


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_ui():
    """Serve the enterprise project intake UI."""
    ui_path = REPO_ROOT / "fireai_upload_ui.html"
    if ui_path.exists():
        return FileResponse(str(ui_path), media_type="text/html")
    return {"service": "FireAI Pro API", "version": "3.0.0", "ui": "/"}

@app.get("/design")
async def serve_format_selector():
    p = REPO_ROOT / "format_selector.html"
    if p.exists():
        return FileResponse(str(p), media_type="text/html")
    raise HTTPException(404, "format_selector.html not found")

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/")
async def root():
    return {"service": "FireAI Pro API", "version": "3.0.0", "status": "online"}


@app.post("/api/generate", status_code=202)
async def generate(request: GenerateRequest, background_tasks: BackgroundTasks):
    """Queue a design job — JSON body."""
    job_id  = str(uuid.uuid4())[:8].upper()
    ctx     = request.project_context
    sheets  = list(request.validated_sheets())
    formats = list(request.validated_formats())
    _set_job(job_id, status="queued", stage="queued",
             message="Job queued — starting shortly",
             project=ctx.get("project_name","Unnamed"))
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
    """
    Queue a design job from a full construction document set.
    Accepts one or more files (floor plans, structural, mechanical, specs, etc.)
    FireAI Pro will classify each document and extract all relevant data.
    """
    job_id  = str(uuid.uuid4())[:8].upper()
    ctx     = json.loads(project_context)
    sheets  = json.loads(selected_sheets)
    formats = json.loads(selected_formats)

    file_names = [f.filename for f in files]
    log.info(f"[{job_id}] Received {len(files)} document(s): {', '.join(file_names)}")

    _set_job(job_id, status="queued", stage="queued",
             message=f"Received {len(files)} document(s) — analyzing...",
             project=ctx.get("project_name", "Unnamed"),
             document_count=len(files))

    # Read all file bytes before the background task runs
    file_list = []
    for f in files:
        file_bytes = await f.read()
        file_list.append({"bytes": file_bytes, "filename": f.filename or "upload.pdf"})

    async def run_with_documents():
        try:
            if len(file_list) == 1:
                # Single file: use existing document processor (faster)
                _set_job(job_id, stage="doc_analysis",
                         message="Analyzing construction document...")
                geometry = await handle_upload(
                    file_list[0]["bytes"], file_list[0]["filename"], ctx)
                log.info(f"[{job_id}] Single doc: "
                         f"{len(geometry.get('rooms',[]))} rooms, "
                         f"{len(geometry.get('walls',[]))} walls")
            else:
                # Multiple files: full document intelligence pipeline
                _set_job(job_id, stage="doc_analysis",
                         message=f"Classifying {len(file_list)} documents...")
                geometry = await handle_document_set(file_list, ctx)
                log.info(f"[{job_id}] Doc set ({len(file_list)} files): "
                         f"{len(geometry.get('rooms',[]))} rooms, "
                         f"{len(geometry.get('obstructions',[]))} obstructions, "
                         f"water supply {geometry.get('water_supply',{}).get('static_pressure_psi',0):.0f} psi")

                # Push synthesized water supply back to context
                ws = geometry.get("water_supply", {})
                if ws.get("static_pressure_psi"):
                    ctx["static_pressure"]  = ws["static_pressure_psi"]
                if ws.get("residual_pressure_psi"):
                    ctx["residual_pressure"] = ws["residual_pressure_psi"]
                if ws.get("flow_gpm"):
                    ctx["water_supply_flow"] = ws["flow_gpm"]
                if geometry.get("spec", {}).get("pipe_material"):
                    ctx["pipe_material"] = geometry["spec"]["pipe_material"]
                if geometry.get("spec", {}).get("seismic_zone"):
                    ctx["seismic_zone"] = geometry["spec"]["seismic_zone"]

            await _run_job(job_id, ctx, sheets, formats, geometry)

        except Exception as e:
            log.exception(f"[{job_id}] Document processing failed: {e}")
            _set_job(job_id, status="failed", stage="error", message=str(e),
                     completed_at=datetime.utcnow().isoformat())

    background_tasks.add_task(run_with_documents)
    log.info(f"[{job_id}] Queued — {ctx.get('project_name')} — {len(files)} file(s)")
    return {"job_id": job_id, "status": "queued",
            "poll_url": f"/api/jobs/{job_id}",
            "document_count": len(files)}


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
    jobs = sorted(_jobs.values(), key=lambda j: j.get("created_at",""), reverse=True)
    return {"jobs": jobs[:limit], "total": len(_jobs)}
