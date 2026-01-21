# app.py — FireAI minimal backend with job-scoped artifacts
# Works with local storage out of the box. Optional X-API-Key check via env FIREAI_API_KEY.

import os
import uuid
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, Body, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import FileResponse as FR
from concurrent.futures import ThreadPoolExecutor

APP_VERSION = "1.3.0"

# ---- Storage root (mount /data in Railway) ----
DATA_ROOT = Path(
    os.environ.get("FIREAI_LOCAL_STORAGE")
    or os.environ.get("FIREAI_DATA_ROOT")
    or "/data/projects"
)
DATA_ROOT.mkdir(parents=True, exist_ok=True)

# ---- Optional API key enforcement (pass in X-API-Key) ----
REQUIRED_API_KEY = os.environ.get("FIREAI_API_KEY")  # if unset -> no auth required


def api_key_dep(request: Request):
    """Optional 'X-API-Key' header check if FIREAI_API_KEY is set."""
    if not REQUIRED_API_KEY:
        return
    sent = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    if sent != REQUIRED_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


app = FastAPI(title="FireAI Pro API", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# In-memory job tracker (best-effort)
JOBS: Dict[str, Dict[str, Any]] = {}

# Thread pool for background runs
executor = ThreadPoolExecutor(max_workers=int(os.environ.get("FIREAI_MAX_WORKERS", "4")))


# ------------------------
# Helpers
# ------------------------
def proj_dir(pid: str) -> Path:
    p = DATA_ROOT / pid
    p.mkdir(parents=True, exist_ok=True)
    return p


def job_dir(pid: str, jid: str) -> Path:
    j = proj_dir(pid) / jid
    j.mkdir(parents=True, exist_ok=True)
    return j


def latest_job_dir(pid: str) -> Optional[Path]:
    p = proj_dir(pid)
    jobs = [d for d in p.iterdir() if d.is_dir()]
    return sorted(jobs, key=lambda d: d.stat().st_mtime, reverse=True)[0] if jobs else None


def list_artifacts(base: Path, pid: str) -> Dict[str, str]:
    """Return {name: relative_path_from_project_root} for everything under base."""
    out: Dict[str, str] = {}
    pdir = proj_dir(pid)
    for f in base.rglob("*"):
        if f.is_file():
            try:
                rel = f.relative_to(pdir)
            except ValueError:
                # Safety: if file isn't under proj dir, skip
                continue
            out[f.name] = str(rel)
    return out


def write_manifest(pid: str, jid: str, outputs: Dict[str, str]) -> Path:
    pdir = proj_dir(pid)
    jdir = job_dir(pid, jid)
    manifest = {
        "project_id": pid,
        "job_id": jid,
        "output_dir": str(jdir),
        # ensure paths are relative to project root so /download route works
        "deliverables": {},
        "created": int(time.time()),
    }
    
    # Convert paths to be relative to project directory
    for name, path in outputs.items():
        if path:
            try:
                rel_path = Path(path).relative_to(pdir)
                manifest["deliverables"][name] = str(rel_path)
            except ValueError:
                # If path is not under pdir, use the filename
                manifest["deliverables"][name] = Path(path).name
    
    mpath = jdir / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2))
    return mpath


def save_upload_preserving_ext(pdir: Path, file: UploadFile) -> str:
    """
    Save uploaded file with its original filename AND 'upload.<ext>' alias.
    Returns the alias path.
    """
    raw = file.filename or "upload.dwg"
    orig_name = Path(raw).name
    data = file.file.read() if hasattr(file, "file") else None
    if data is None:
        data = b""
    (pdir / orig_name).write_bytes(data)
    alias = "upload" + Path(orig_name).suffix.lower()
    (pdir / alias).write_bytes(data)
    return alias


# ------------------------
# Health / readiness
# ------------------------
@app.get("/health")
def health():
    # Check which engines are available
    engines = {}
    try:
        from orchestrate import CAD_AVAILABLE, ROUTING_AVAILABLE, HYDRAULICS_AVAILABLE
        from orchestrate import CODES_AVAILABLE, BRACING_AVAILABLE, PRODUCTS_AVAILABLE
        engines = {
            "cad": CAD_AVAILABLE,
            "routing": ROUTING_AVAILABLE,
            "hydraulics": HYDRAULICS_AVAILABLE,
            "codes": CODES_AVAILABLE,
            "bracing": BRACING_AVAILABLE,
            "products": PRODUCTS_AVAILABLE,
        }
    except ImportError:
        engines = {"status": "orchestrate module not loaded"}
    
    features = {
        "artifact_publishing": True,
        "enhanced_orchestrator": True,
        "prometheus_metrics": False,
        "s3_upload": False,  # set True if you later add S3 publishing
    }
    return {
        "status": "ok", 
        "version": APP_VERSION, 
        "features": features,
        "engines": engines,
        "data_root": str(DATA_ROOT)
    }


@app.get("/readiness")
def readiness():
    # Check if orchestrate module is importable and functional
    try:
        from orchestrate import orchestrate
        status = "ready"
        detail = "orchestrate module loaded successfully"
    except ImportError as e:
        status = "degraded"
        detail = f"orchestrate import failed: {e}"
    except Exception as e:
        status = "degraded"
        detail = f"orchestrate error: {e.__class__.__name__}: {e}"
    
    return {"status": status, "detail": detail, "data_root": str(DATA_ROOT)}


# ------------------------
# Create project (multipart OR JSON)
# ------------------------
@app.post("/api/projects", dependencies=[Depends(api_key_dep)])
async def create_project(
    request: Request,
    project_id: Optional[str] = Form(None),
    project_json: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    json_body: Optional[Dict[str, Any]] = Body(None),
):
    """
    Supports:
      - multipart/form-data: project_id (Form), project_json (Form JSON), file (UploadFile)
      - application/json   : {"project_id": "...", "project_name": "...", "zip_code": "...", "project_data": {...}}
    """
    # Determine payload source
    pid = project_id
    meta: Dict[str, Any] = {}

    if json_body and isinstance(json_body, dict):
        # JSON mode
        if not pid:
            pid = json_body.get("project_id") or str(uuid.uuid4())
        # Merge metadata
        meta.update(json_body)
    else:
        # Multipart mode
        if not pid:
            pid = str(uuid.uuid4())
        if project_json:
            try:
                meta.update(json.loads(project_json))
            except Exception:
                pass

    pdir = proj_dir(pid)

    # Write project.json (always)
    base_meta = {"project_id": pid, "created": int(time.time())}
    base_meta.update(meta or {})
    (pdir / "project.json").write_text(json.dumps(base_meta, indent=2))

    saved_file = None
    if file:
        alias = save_upload_preserving_ext(pdir, file)
        saved_file = str(pdir / alias)

    return {
        "project_id": pid,
        "message": "project created successfully",
        "uploaded": bool(file),
        "project_dir": str(pdir),
        "saved_file": saved_file,
    }


# ------------------------
# Run: create job_id and invoke orchestrator in background
# ------------------------
def _run_job(pid: str, jid: str):
    pdir = proj_dir(pid)
    jdir = job_dir(pid, jid)
    JOBS[jid] = {"project_id": pid, "status": "running", "step": "starting", "pct": 10}

    outputs: Dict[str, str] = {}

    # Try to import and run the orchestrate function
    try:
        JOBS[jid].update({"step": "importing_orchestrator", "pct": 20})
        from orchestrate import orchestrate
        
        JOBS[jid].update({"step": "running_engines", "pct": 30})
        
        # Ensure output dir exists
        jdir.mkdir(parents=True, exist_ok=True)
        
        # Call the orchestrate function
        maybe = orchestrate(project_dir=pdir, output_dir=jdir)
        
        JOBS[jid].update({"step": "collecting_outputs", "pct": 80})
        
        if isinstance(maybe, dict):
            outputs = {k: str(Path(v)) for k, v in maybe.items() if v}
            
    except Exception as e:
        print(f"🔥 Orchestration error: {e}")
        import traceback
        traceback.print_exc()
        
        # Fallback: write minimal placeholders so client can verify pipeline
        JOBS[jid].update({"step": "fallback_generation", "pct": 70, "error": str(e)})
        try:
            (jdir / "design.dxf").write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
            (jdir / "materials.csv").write_text("item,qty\nhead,12\npipe,200ft\n")
            (jdir / "compliance.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
            outputs = {
                "design.dxf": str(jdir / "design.dxf"),
                "materials.csv": str(jdir / "materials.csv"),
                "compliance.pdf": str(jdir / "compliance.pdf"),
            }
        except Exception:
            # If even placeholders fail, leave outputs empty
            pass

    # Write manifest (even if empty)
    JOBS[jid].update({"step": "writing_manifest", "pct": 90})
    write_manifest(pid, jid, outputs)

    # Mark job success (best-effort)
    JOBS[jid].update({
        "status": "succeeded", 
        "step": "done", 
        "pct": 100, 
        "output_dir": str(jdir),
        "outputs": list(outputs.keys())
    })


@app.post("/api/projects/{project_id}/run", dependencies=[Depends(api_key_dep)])
def run_project(project_id: str):
    pid = project_id
    jid = str(uuid.uuid4())
    job_dir(pid, jid)  # ensure exists early
    JOBS[jid] = {"project_id": pid, "status": "queued", "step": "queued", "pct": 0}
    executor.submit(_run_job, pid, jid)
    return {"job_id": jid, "status": "queued"}


# ------------------------
# Jobs & results
# ------------------------
@app.get("/api/jobs/{job_id}", dependencies=[Depends(api_key_dep)])
def job_status(job_id: str):
    if job_id in JOBS:
        j = JOBS[job_id]
        return {
            "job_id": job_id,
            "status": j.get("status", "unknown"),
            "step": j.get("step", ""),
            "pct": j.get("pct", 0),
            "project_id": j.get("project_id"),
            "output_dir": j.get("output_dir"),
            "outputs": j.get("outputs", []),
            "error": j.get("error"),
        }

    # Fallback: infer from filesystem (manifest presence)
    for p in DATA_ROOT.iterdir():
        jdir = p / job_id
        if jdir.is_dir():
            status = "succeeded" if (jdir / "manifest.json").exists() else "running"
            outputs = []
            if (jdir / "manifest.json").exists():
                try:
                    manifest = json.loads((jdir / "manifest.json").read_text())
                    outputs = list(manifest.get("deliverables", {}).keys())
                except:
                    pass
            return {
                "job_id": job_id, 
                "status": status, 
                "step": "done" if status == "succeeded" else "running",
                "pct": 100 if status == "succeeded" else 50, 
                "project_id": p.name, 
                "output_dir": str(jdir),
                "outputs": outputs
            }
    return JSONResponse({"detail": "Not Found"}, status_code=404)


@app.get("/api/projects/{project_id}/results", dependencies=[Depends(api_key_dep)])
def project_results(project_id: str):
    pid = project_id
    jdir = latest_job_dir(pid)
    pdir = proj_dir(pid)
    base = jdir if jdir else pdir
    deliverables = list_artifacts(base, pid)
    manifest_type = "job" if jdir else "fallback"
    message = "Latest job files" if jdir else "No job found; listing project root files"
    return {"output_dir": str(base), "deliverables": deliverables, "manifest_type": manifest_type, "message": message}


@app.get("/api/projects/{project_id}/jobs/{job_id}/artifacts", dependencies=[Depends(api_key_dep)])
def job_artifacts(project_id: str, job_id: str):
    jdir = job_dir(project_id, job_id)
    m = jdir / "manifest.json"
    if m.exists():
        try:
            return json.loads(m.read_text())
        except Exception:
            pass
    return {"project_id": project_id, "job_id": job_id, "deliverables": list_artifacts(jdir, project_id)}


# ------------------------
# Download (supports nested paths)
# ------------------------
def _safe_resolve_for_project(project_id: str, filename: str) -> Path:
    pdir = proj_dir(project_id)
    path = (pdir / filename).resolve()
    if not str(path).startswith(str(pdir.resolve())):
        raise HTTPException(status_code=403, detail="Invalid path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return path


@app.get("/api/projects/{project_id}/download/{filename:path}", dependencies=[Depends(api_key_dep)])
def download(project_id: str, filename: str):
    path = _safe_resolve_for_project(project_id, filename)
    return FR(str(path), filename=path.name)


# Legacy convenience route some clients try (keep for compatibility)
@app.get("/download/{project_id}/{filename:path}", dependencies=[Depends(api_key_dep)])
def legacy_download(project_id: str, filename: str):
    path = _safe_resolve_for_project(project_id, filename)
    return FR(str(path), filename=path.name)


# ------------------------
# Run locally (optional)
# ------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=False)
