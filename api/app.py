from __future__ import annotations

import os
import uuid
import json
import logging
import traceback
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import importlib

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared models + job store (with fallback if fireai_schemas is unavailable)
try:
    from fireai_schemas import JobResult, Deliverables, Artifact, ErrorInfo
    from fireai_schemas.job_store import JobStore
except Exception:
    from pydantic import BaseModel, Field

    class Artifact(BaseModel):
        kind: str = "other"
        name: str = ""
        path: str = ""
        meta: dict = {}

    class Deliverables(BaseModel):
        ifc: str | None = None
        dxf: str | None = None
        pdfs: dict[str, str] = {}
        extras: list[Artifact] = []

    class ErrorInfo(BaseModel):
        code: str
        message: str
        engine: str = "orchestrator"
        hint: str | None = None

    class JobResult(BaseModel):
        job_id: str
        project_id: str
        status: str
        step: str | None = None
        pct: int | float | None = None
        deliverables: Deliverables | dict | None = None
        warnings: list[str] = []
        errors: list[str] = []
        error: ErrorInfo | None = None
        metrics: dict = {}

    class JobStore:
        def __init__(self, namespace: str = "fireai", ttl_seconds: int = 7 * 24 * 3600):
            self._d: dict[str, dict] = {}
        def set(self, k: str, v: dict):
            self._d[k] = v
        def get(self, k: str):
            return self._d.get(k)

# Optional metrics
try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
    PROM = True
except Exception:
    PROM = False
    logger.warning("Prometheus metrics unavailable; prometheus_client not installed")

# Optional S3 uploader
try:
    from s3_uploader import upload_deliverables_to_s3
except Exception:
    upload_deliverables_to_s3 = None
    logger.warning("S3 uploader unavailable; s3_uploader not found")

# Config
OUTPUT_ROOT = Path(os.getenv("FIREAI_LOCAL_STORAGE", "./fireai_outputs")).resolve()
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
ORCH_TIMEOUT_SECS = int(os.getenv("ORCH_TIMEOUT_SECS", "600"))  # 10 minutes
API_KEY = os.getenv("API_KEY")

# App
app = FastAPI(title="FireAI Pro API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth
def require_api_key(x_api_key: str | None = Header(None)):
    if not API_KEY:
        logger.info("API_KEY not set; allowing open access")
        return True
    if not x_api_key or x_api_key != API_KEY:
        logger.warning(f"Invalid or missing API key: {x_api_key}")
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    return True

# Job store
JOBS: Dict[str, Dict[str, Any]] = {}  # Unused, kept for compatibility
STORE = JobStore(namespace="fireai", ttl_seconds=7 * 24 * 3600)

# Metrics
if PROM:
    JOBS_CREATED = Counter("fireai_jobs_created_total", "Jobs created")
    JOBS_COMPLETED = Counter("fireai_jobs_completed_total", "Jobs completed")
    JOBS_FAILED = Counter("fireai_jobs_failed_total", "Jobs failed")
    JOB_DURATION = Histogram("fireai_job_duration_seconds", "Job duration in seconds")
else:
    JOBS_CREATED = JOBS_COMPLETED = JOBS_FAILED = JOB_DURATION = None

# Orchestrator
ORCH_MODULE = "master_fireai_orchestrator"
ORCH_FUNCS = ["run_project", "run_pipeline", "process_project", "orchestrate_project", "handle_project"]

def _prometheus_unload(prefixes=(
    "fireai_job_duration_seconds",
    "fireai_jobs_created",
    "fireai_jobs_completed",
    "fireai_jobs_failed",
    "fireai_jobs_succeeded",
)):
    try:
        from prometheus_client import REGISTRY
        name_map = getattr(REGISTRY, "_names_to_collectors", {})
        for name in list(name_map.keys()):
            if any(name.startswith(p) for p in prefixes):
                name_map.pop(name, None
)
    except Exception:
        logger.warning("Failed to unload Prometheus collectors")

def _load_orchestrator():
    try:
        _prometheus_unload()
        logger.info(f"Attempting to import orchestrator: {ORCH_MODULE}")
        return importlib.import_module(ORCH_MODULE)
    except Exception as e:
        logger.error(f"Failed to import {ORCH_MODULE}: {e}")
        cwd = os.getcw d()
        files = [f for f in os.listdir(cwd) if f.endswith(".py")]
        error_msg = (
            f"Could not import {ORCH_MODULE}. Error: {e}\n"
            f"CWD: {cwd}\nPYTHONPATH: {sys.path}\nPython files in CWD: {files}"
        )
        logger.error(error_msg)
        return None  # Don't raise, allow fallback

def _run_orchestrator(project_json: dict):
    logger.info("Running orchestrator")
    orch = _load_orchestrator()
    if orch is None:
        logger.warning("No orchestrator module loaded; returning empty manifest")
        return {}
    for fn in ORCH_FUNCS:
        f = getattr(orch, fn, None)
        if callable(f):
            logger.info(f"Trying orchestrator function: {fn}")
            try:
                return f(project_json) or {}
            except TypeError:
                try:
                    return f(project_json=project_json) or {}
                except TypeError:
                    try:
                        return f() or {}
                    except Exception as e:
                        logger.warning(f"Function {fn} failed: {e}")
                        continue
    logger.warning("No valid orchestrator function found; returning empty manifest")
    return {}

# Health / Readiness / Metrics
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/readiness")
def readiness():
    try:
        _ = _load_orchestrator()
    except Exception as e:
        logger.error(f"Readiness check failed: orchestrator not ready: {e}")
        raise HTTPException(status_code=503, detail=f"orchestrator not ready: {e}")
    try:
        probe = OUTPUT_ROOT / ".__writecheck__"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Readiness check failed: storage not writable: {e}")
        raise HTTPException(status_code=503, detail=f"storage not writable: {e}")
    return {"status": "ready"}

if PROM:
    @app.get("/metrics")
    def metrics():
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

# API
@app.post("/api/projects", dependencies=[Depends(require_api_key)])
async def create_project(
    project_id: Optional[str] = Form(None),
    project_json: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """Create a project; optional file upload (DWG/IFC/PDF/ZIP)."""
    pid = project_id or str(uuid.uuid4())
    out_dir = OUTPUT_ROOT / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Creating project: {pid}, out_dir: {out_dir}")

    data: Dict[str, Any] = {}
    if project_json:
        try:
            data = json.loads(project_json)
        except Exception as e:
            logger.error(f"Invalid project_json: {e}")
            raise HTTPException(400, f"Invalid project_json: {e}")
    data.setdefault("project_id", pid)

    saved_file = None
    if file:
        allowed_ext = {".dxf", ".dwg", ".ifc", ".zip", ".pdf"}
        ext = (Path(file.filename).suffix or "").lower()
        if ext not in allowed_ext:
            logger.error(f"Unsupported file type: {ext}")
            raise HTTPException(400, f"Unsupported file type: {ext}")
        max_bytes = int(os.getenv("UPLOAD_MAX_BYTES", "209715200"))  # 200 MB
        blob = await file.read()
        if len(blob) > max_bytes:
            logger.error("File too large")
            raise HTTPException(413, "File too large")
        saved_file = out_dir / f"upload{ext}"
        with saved_file.open("wb") as f_out:
            f_out.write(blob)
        logger.info(f"Saved file: {saved_file}")

    with (out_dir / "project.json").open("w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Wrote project.json for project: {pid}")

    return {"project_id": pid, "saved_file": str(saved_file) if saved_file else None}

@app.post("/api/projects/{project_id}/run", response_model=JobResult, dependencies=[Depends(require_api_key)])
async def run_project(project_id: str, background: BackgroundTasks):
    """Kick off the design run in a background task (job state via JobStore)."""
    proj_dir = OUTPUT_ROOT / project_id
    logger.info(f"Starting job for project_id: {project_id}, proj_dir: {proj_dir}")
    if not proj_dir.exists():
        logger.error(f"Project directory not found: {proj_dir}")
        raise HTTPException(404, "project not found")

    job_id = str(uuid.uuid4())
    jr = JobResult(job_id=job_id, project_id=project_id, status="queued", deliverables=None).model_dump()
    STORE.set(job_id, jr)
    if PROM and JOBS_CREATED:
        JOBS_CREATED.inc()
    logger.info(f"Created job_id: {job_id}")

    def _worker():
        start = time.time()
        logger.info(f"Worker started for job_id: {job_id}")

        def set_status(status: str, extra: Dict[str, Any] | None = None):
            cur = STORE.get(job_id) or {}
            cur["status"] = status
            if extra:
                cur.update(extra)
            STORE.set(job_id, cur)
            logger.info(f"Job {job_id} status updated to {status}, extra: {extra}")

        try:
            # Pre-orchestrator setup
            set_status("routing", {"pct": 10, "step": "routing"})
            project_json_path = proj_dir / "project.json"
            if not project_json_path.exists():
                logger.error(f"project.json not found in {proj_dir}")
                raise FileNotFoundError(f"project.json not found in {proj_dir}")
            with project_json_path.open() as f:
                pj = json.load(f)
            logger.info(f"Loaded project.json for job_id: {job_id}")

            # Run orchestrator with a hard timeout
            set_status("running", {"pct": 30, "step": "orchestrator"})
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_run_orchestrator, pj)
                try:
                    manifest = fut.result(timeout=ORCH_TIMEOUT_SECS) or {}
                    logger.info(f"Orchestrator completed for job_id: {job_id}, manifest: {manifest}")
                except FuturesTimeoutError:
                    err = f"Orchestrator exceeded timeout ({ORCH_TIMEOUT_SECS}s)"
                    logger.error(err)
                    set_status(
                        "failed",
                        {
                            "step": "timeout",
                            "pct": 100,
                            "errors": [err],
                            "error": ErrorInfo(code="TIMEOUT", message=err, engine="orchestrator").model_dump(),
                        },
                    )
                    if PROM and JOBS_FAILED:
                        JOBS_FAILED.inc()
                    return

            # Collect deliverables
            dxf = manifest.get("dxf") or next((str(p) for p in proj_dir.glob("*.dxf")), None)
            ifc = manifest.get("ifc") or next((str(p) for p in proj_dir.glob("*.ifc")), None)

            raw_pdfs = manifest.get("pdfs")
            if isinstance(raw_pdfs, dict):
                pdfs = {str(k).lower(): str(v) for k, v in raw_pdfs.items() if v}
            elif isinstance(raw_pdfs, list):
                pdfs = {}
                for p in raw_pdfs:
                    try:
                        if p:
                            name = os.path.splitext(os.path.basename(str(p)))[0].lower()
                            pdfs[name] = str(p)
                    except Exception as e:
                        logger.warning(f"Failed to process PDF: {e}")
                        continue
            else:
                pdfs = {os.path.splitext(p.name)[0].lower(): str(p) for p in proj_dir.glob("*.pdf")}

            extras_list = manifest.get("extras") or []
            extras = []
            for a in extras_list:
                try:
                    if hasattr(a, "model_dump") and callable(getattr(a, "model_dump")):
                        d = a.model_dump()
                        extras.append(
                            Artifact(
                                kind=d.get("kind", "other"),
                                name=d.get("name") or os.path.basename(d.get("path", "") or ""),
                                path=d.get("path", "") or "",
                                meta=d.get("meta", {}) or {},
                            )
                        )
                    elif isinstance(a, Artifact):
                        extras.append(a)
                    elif isinstance(a, dict):
                        extras.append(
                            Artifact(
                                kind=a.get("kind", "other"),
                                name=a.get("name") or os.path.basename(a.get("path", "") or ""),
                                path=a.get("path", "") or "",
                                meta=a.get("meta", {}) or {},
                            )
                        )
                    elif isinstance(a, str):
                        extras.append(Artifact(kind="other", name=os.path.basename(a), path=str(a), meta={}))
                except Exception as e:
                    logger.warning(f"Failed to process extra artifact: {e}")
                    continue

            delivs = Deliverables(ifc=ifc, dxf=dxf, pdfs=pdfs, extras=extras)
            logger.info(f"Collected deliverables for job_id: {job_id}: {delivs}")

            # Optional S3 upload
            if upload_deliverables_to_s3:
                try:
                    delivs = upload_deliverables_to_s3(delivs, project_id)
                    logger.info(f"S3 upload successful for job_id: {job_id}")
                except Exception as e:
                    logger.warning(f"S3 upload failed for job_id: {job_id}: {e}")
                    pass

            # Done
            set_status(
                "succeeded",
                {
                    "step": "done",
                    "pct": 100,
                    "deliverables": delivs.model_dump() if hasattr(delivs, "model_dump") else delivs.__dict__,
                    "metrics": manifest.get("metrics", {}),
                },
            )
            if PROM and JOBS_COMPLETED:
                JOBS_COMPLETED.inc()
            if PROM and JOB_DURATION:
                JOB_DURATION.observe(time.time() - start)
            logger.info(f"Job {job_id} completed successfully")

        except Exception as e:
            if PROM and JOBS_FAILED:
                JOBS_FAILED.inc()
            logger.error(f"Job {job_id} failed: {str(e)}\n{traceback.format_exc()}")
            set_status(
                "failed",
                {
                    "step": "error",
                    "pct": 100,
                    "errors": [str(e), traceback.format_exc()],
                    "error": ErrorInfo(code="ORCH_FAIL", message=str(e), engine="orchestrator", hint="See server logs").model_dump(),
                },
            )

    background.add_task(_worker)
    return JobResult(**STORE.get(job_id))

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = STORE.get(job_id)
    if not job:
        logger.warning(f"Job not found: {job_id}")
        raise HTTPException(404, "job not found")
    return job

@app.get("/api/projects/{project_id}/results")
def get_results(project_id: str):
    out_dir = OUTPUT_ROOT / project_id
    if not out_dir.exists():
        logger.warning(f"Project directory not found: {out_dir}")
        raise HTTPException(404, "project not found")
    files = {p.name: str(p) for p in out_dir.glob("*")}
    logger.info(f"Retrieved results for project_id: {project_id}, files: {files}")
    return {"deliverables": files, "output_dir": str(out_dir)}

@app.get("/api/projects/{project_id}/download/{filename}")
def download(project_id: str, filename: str):
    path = OUTPUT_ROOT / project_id / filename
    if not path.exists():
        logger.warning(f"File not found: {path}")
        raise HTTPException(404, "file not found")
    logger.info(f"Downloading file: {path}")
    return FileResponse(path)
