from __future__ import annotations

import os, uuid, json, threading, traceback, sys, time
from pathlib import Path
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import importlib

# Shared models + job store
from fireai_schemas import JobResult, Deliverables, Artifact, ErrorInfo
from fireai_schemas.job_store import JobStore

# Optional metrics
try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST  # type: ignore
    PROM = True
except Exception:  # pragma: no cover
    PROM = False

# Optional S3 uploader (external file)
try:
    from s3_uploader import upload_deliverables_to_s3
except Exception:
    upload_deliverables_to_s3 = None  # type: ignore

# ---------------- Config ----------------
OUTPUT_ROOT = Path(os.getenv("FIREAI_LOCAL_STORAGE", "./fireai_outputs")).resolve()
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
ORCH_TIMEOUT_SECS = int(os.getenv("ORCH_TIMEOUT_SECS", "1800"))  # 30 minutes default

# ---------------- App ----------------
app = FastAPI(title="FireAI Pro API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- Auth (API key) ----------------
API_KEY = os.getenv("API_KEY")

def require_api_key(x_api_key: str | None = Header(None)):
    # If API_KEY not set, leave routes open (useful for dev)
    if not API_KEY:
        return True
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    return True

# ---------------- Job store ----------------
JOBS: Dict[str, Dict[str, Any]] = {}  # not used; kept for compatibility
STORE = JobStore(namespace="fireai", ttl_seconds=7 * 24 * 3600)

# Metrics
if PROM:
    JOBS_CREATED = Counter("fireai_jobs_created_total", "Jobs created")
    JOBS_COMPLETED = Counter("fireai_jobs_completed_total", "Jobs completed")
    JOBS_FAILED = Counter("fireai_jobs_failed_total", "Jobs failed")
    JOB_DURATION = Histogram("fireai_job_duration_seconds", "Job duration in seconds")
else:  # pragma: no cover
    JOBS_CREATED = JOBS_COMPLETED = JOBS_FAILED = JOB_DURATION = None  # type: ignore

# Orchestrator discovery (keep your originals)
ORCH_MODULES = [
    "master_fireai_orchestrator",
    "hardened_orchestrator",
    "corrected_orchestrator_enhanced_exports",
]
ORCH_FUNCS = [
    "run_project",
    "run_pipeline",
    "process_project",
    "orchestrate_project",
    "handle_project",
    "main",
]

# ---- Prometheus de-dupe: remove conflicting collectors before import ----
def _prometheus_unload(prefixes=(
    "fireai_job_duration_seconds",   # covers _bucket, _count, _sum, _created
    "fireai_jobs_created",           # covers _total
    "fireai_jobs_completed",
    "fireai_jobs_failed",
    "fireai_jobs_succeeded",
)):
    """
    Some imports register Prometheus metrics multiple times. On re-import
    we proactively remove any existing collectors whose names start with
    our prefixes to avoid 'Duplicated timeseries in CollectorRegistry'.
    """
    try:
        from prometheus_client import REGISTRY  # optional dep
        name_map = getattr(REGISTRY, "_names_to_collectors", {})
        for name in list(name_map.keys()):          # copy keys; we mutate the dict
            if any(name.startswith(p) for p in prefixes):
                name_map.pop(name, None)
    except Exception:
        pass

def _load_orchestrator():
    errors = []
    for name in ORCH_MODULES:
        try:
            # Clear potentially duplicated collectors before each import attempt
            _prometheus_unload()
            return importlib.import_module(name)
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
    cwd = os.getcwd()
    files = [f for f in os.listdir(cwd) if f.endswith(".py")]
    raise RuntimeError(
        "Could not import an orchestrator module. Tried: "
        + ", ".join(ORCH_MODULES)
        + f"\nErrors: {errors}\nCWD: {cwd}\nPYTHONPATH: {sys.path}\nPython files in CWD: {files}"
    )

def _run_orchestrator(project_json: dict):
    """Flexible entry-point logic; returns orchestrator output (manifest)."""
    orch = _load_orchestrator()
    for fn in ORCH_FUNCS:
        f = getattr(orch, fn, None)
        if callable(f):
            try:
                try:
                    return f(project_json)
                except TypeError:
                    try:
                        return f(project_json=project_json)
                    except TypeError:
                        return f()
            except Exception:
                continue
    return None

# ---------------- Health / Readiness / Metrics ----------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/readiness")
def readiness():
    try:
        _ = _load_orchestrator()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"orchestrator not ready: {e}")
    try:
        probe = OUTPUT_ROOT / ".__writecheck__"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"storage not writable: {e}")
    return {"status": "ready"}

if PROM:
    @app.get("/metrics")
    def metrics():
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

# ---------------- API ----------------
@app.post("/api/projects", dependencies=[Depends(require_api_key)])
async def create_project(
    project_id: Optional[str] = Form(None),
    project_json: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """Create a project; optional file upload (DWG/IFC/PDF/ZIP)."""
    pid = project_id or str(uuid.uuid4())
    out_dir = (OUTPUT_ROOT / pid)
    out_dir.mkdir(parents=True, exist_ok=True)

    data: Dict[str, Any] = {}
    if project_json:
        try:
            data = json.loads(project_json)
        except Exception as e:
            raise HTTPException(400, f"Invalid project_json: {e}")
    data.setdefault("project_id", pid)

    saved_file = None
    if file:
        allowed_ext = {".dxf", ".dwg", ".ifc", ".zip", ".pdf"}
        ext = (Path(file.filename).suffix or "").lower()
        if ext not in allowed_ext:
            raise HTTPException(400, f"Unsupported file type: {ext}")
        max_bytes = int(os.getenv("UPLOAD_MAX_BYTES", "209715200"))  # 200 MB
        blob = await file.read()
        if len(blob) > max_bytes:
            raise HTTPException(413, "File too large")
        saved_file = out_dir / f"upload{ext}"
        with saved_file.open("wb") as f_out:
            f_out.write(blob)

    with (out_dir / "project.json").open("w") as f:
        json.dump(data, f, indent=2)

    return {"project_id": pid, "saved_file": str(saved_file) if saved_file else None}

@app.post("/api/projects/{project_id}/upload", dependencies=[Depends(require_api_key)])
async def upload_file(project_id: str, file: UploadFile = File(...)):
    allowed_ext = {".dxf", ".dwg", ".ifc", ".zip", ".pdf"}
    ext = (Path(file.filename or "").suffix or "").lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    max_bytes = int(os.getenv("UPLOAD_MAX_BYTES", "209715200"))
    blob = await file.read()
    if len(blob) > max_bytes:
        raise HTTPException(status_code=413, detail="File too large")

    out_dir = OUTPUT_ROOT / project_id
    if not out_dir.exists():
        raise HTTPException(404, "project not found")

    target = out_dir / os.path.basename(file.filename)
    with open(target, "wb") as f:
        f.write(blob)
    return {"ok": True, "path": str(target)}

@app.post("/api/projects/{project_id}/run", response_model=JobResult, dependencies=[Depends(require_api_key)])
async def run_project(project_id: str, background: BackgroundTasks):
    """Kick off the design run in a background thread (job state via JobStore)."""
    proj_dir = OUTPUT_ROOT / project_id
    if not proj_dir.exists():
        raise HTTPException(404, "project not found")

    job_id = str(uuid.uuid4())
    jr = JobResult(job_id=job_id, project_id=project_id, status="queued").model_dump()
    STORE.set(job_id, jr)
    if PROM and JOBS_CREATED:
        JOBS_CREATED.inc()

    def _worker():
        start = time.time()

        def set_status(status: str, extra: Dict[str, Any] | None = None):
            cur = STORE.get(job_id) or {}
            cur["status"] = status
            if extra:
                cur.update(extra)
            STORE.set(job_id, cur)

        try:
            set_status("routing", {"pct": 10, "step": "routing"})
            with (proj_dir / "project.json").open() as f:
                pj = json.load(f)

            # Run orchestrator with a hard timeout
set_status("running", {"pct": 30, "step": "orchestrator"})
try:
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_run_orchestrator, pj)
        manifest = fut.result(timeout=ORCH_TIMEOUT_SECS) or {}
except FuturesTimeoutError:
    err = f"Orchestrator exceeded timeout ({ORCH_TIMEOUT_SECS}s)"
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

            # Normalize to Deliverables (prefer manifest values; fall back to scanning)
            dxf = manifest.get("dxf") or next((str(p) for p in proj_dir.glob("*.dxf")), None)
            ifc = manifest.get("ifc") or next((str(p) for p in proj_dir.glob("*.ifc")), None)
            pdfs = manifest.get("pdfs") or {p.stem.lower(): str(p) for p in proj_dir.glob("*.pdf")}
            extras_list = manifest.get("extras") or []
            extras = [
                Artifact(
                    kind=a.get("kind", "other"),
                    name=a.get("name", ""),
                    path=a.get("path", ""),
                    meta=a.get("meta", {}),
                )
                for a in extras_list
            ]
            delivs = Deliverables(ifc=ifc, dxf=dxf, pdfs=pdfs, extras=extras)

            # Upload to S3 (if configured) and replace local paths with presigned URLs
            if upload_deliverables_to_s3:
                try:
                    delivs = upload_deliverables_to_s3(delivs, project_id)  # type: ignore
                except Exception:
                    # keep local paths if S3 upload fails
                    pass

            set_status("succeeded", {"step": "done", "pct": 100, "deliverables": delivs.model_dump(), "metrics": manifest.get("metrics", {})})
            if PROM and JOBS_COMPLETED:
                JOBS_COMPLETED.inc()
            if PROM and JOB_DURATION:
                JOB_DURATION.observe(time.time() - start)

        except BaseException as e:
            if PROM and JOBS_FAILED:
                JOBS_FAILED.inc()
            set_status(
                "failed",
                {
                    "step": "error",
                    "pct": 100,
                    "errors": [str(e), traceback.format_exc()],
                    "error": ErrorInfo(code="ORCH_FAIL", message=str(e), engine="orchestrator", hint="See server logs").model_dump(),
                },
            )

    threading.Thread(target=_worker, daemon=True).start()
    return JobResult(**STORE.get(job_id))

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = STORE.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job

@app.get("/api/projects/{project_id}/results")
def get_results(project_id: str):
    out_dir = OUTPUT_ROOT / project_id
    if not out_dir.exists():
        raise HTTPException(404, "project not found")
    files = {p.name: str(p) for p in out_dir.glob("*")}
    return {"deliverables": files, "output_dir": str(out_dir)}

@app.get("/api/projects/{project_id}/download/{filename}")
def download(project_id: str, filename: str):
    path = OUTPUT_ROOT / project_id / filename
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path)
