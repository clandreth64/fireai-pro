from __future__ import annotations

import os, uuid, json, threading, traceback, sys, time
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import importlib

# --- NEW: shared models + job store (from fireai_schemas folder you added) ---
from fireai_schemas import JobResult, Deliverables, Artifact, ErrorInfo
from fireai_schemas.job_store import JobStore

# --- NEW: optional Prometheus metrics (safe if not installed) ---
try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST  # type: ignore
    PROM = True
except Exception:  # pragma: no cover
    PROM = False

# ---------------- Config ----------------
OUTPUT_ROOT = Path(os.getenv("FIREAI_LOCAL_STORAGE", "./fireai_outputs")).resolve()
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# ---------------- App ----------------
app = FastAPI(title="FireAI Pro API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- Job store ----------------
# Keep the in-memory dict around (not used anymore) to avoid surprises
JOBS: Dict[str, Dict[str, Any]] = {}
# NEW: Redis-backed store with in-memory fallback if REDIS_URL not set
STORE = JobStore(namespace="fireai", ttl_seconds=7 * 24 * 3600)

# NEW: metrics (optional)
if PROM:
    JOBS_CREATED = Counter("fireai_jobs_created_total", "Jobs created")
    JOBS_COMPLETED = Counter("fireai_jobs_completed_total", "Jobs completed")
    JOBS_FAILED = Counter("fireai_jobs_failed_total", "Jobs failed")
    JOB_DURATION = Histogram("fireai_job_duration_seconds", "Job duration in seconds")
else:  # pragma: no cover
    JOBS_CREATED = JOBS_COMPLETED = JOBS_FAILED = JOB_DURATION = None  # type: ignore

# Candidate orchestrator modules and entry points (keep your originals)
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

def _load_orchestrator():
    errors = []
    for name in ORCH_MODULES:
        try:
            return importlib.import_module(name)
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
    # Helpful diagnostics
    cwd = os.getcwd()
    files = [f for f in os.listdir(cwd) if f.endswith(".py")]
    raise RuntimeError(
        "Could not import an orchestrator module. Tried: "
        + ", ".join(ORCH_MODULES)
        + f"\nErrors: {errors}\nCWD: {cwd}\nPYTHONPATH: {sys.path}\nPython files in CWD: {files}"
    )

def _run_orchestrator(project_json: dict):
    """
    Keep your flexible entry-point logic exactly as-is.
    Returns whatever the orchestrator returns; we normalize after.
    """
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
                # Try next function if this one fails
                continue
    # If nothing callable, we assume the orchestrator runs on import and writes outputs.
    return None

# ---------------- Health / Readiness / Metrics (NEW) ----------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/readiness")
def readiness():
    # Orchestrator importable?
    try:
        _ = _load_orchestrator()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"orchestrator not ready: {e}")

    # Storage writable?
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
@app.post("/api/projects")
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
        # NEW: safer upload
        allowed_ext = {".dxf", ".dwg", ".ifc", ".zip", ".pdf"}
        ext = (Path(file.filename).suffix or "").lower()
        if ext not in allowed_ext:
            raise HTTPException(400, f"Unsupported file type: {ext}")

        max_bytes = int(os.getenv("UPLOAD_MAX_BYTES", "209715200"))  # 200 MB default
        blob = await file.read()
        if len(blob) > max_bytes:
            raise HTTPException(413, "File too large")

        saved_file = out_dir / f"upload{ext}"
        with saved_file.open("wb") as f_out:
            f_out.write(blob)

    # Persist project.json
    with (out_dir / "project.json").open("w") as f:
        json.dump(data, f, indent=2)

    return {"project_id": pid, "saved_file": str(saved_file) if saved_file else None}

@app.post("/api/projects/{project_id}/run")
async def run_project(project_id: str, background: BackgroundTasks):
    """Kick off the design run in a background thread (job state via JobStore)."""
    proj_dir = OUTPUT_ROOT / project_id
    if not proj_dir.exists():
        raise HTTPException(404, "project not found")

    job_id = str(uuid.uuid4())

    # NEW: seed canonical JobResult into the store
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

            # Run your orchestrator (flexible entry points)
            manifest = _run_orchestrator(pj) or {}

            # ---- Normalize to a Deliverables manifest (keeps your existing file outputs) ----
            # If orchestrator returned explicit paths, prefer them; otherwise scan.
            dxf = manifest.get("dxf") or next((str(p) for p in proj_dir.glob("*.dxf")), None)
            ifc = manifest.get("ifc") or next((str(p) for p in proj_dir.glob("*.ifc")), None)
            pdfs = manifest.get("pdfs") or {p.stem.lower(): str(p) for p in proj_dir.glob("*.pdf")}
            extras_list = manifest.get("extras") or []
            extras = [
                Artifact(kind=a.get("kind", "other"), name=a.get("name", ""), path=a.get("path", ""), meta=a.get("meta", {}))
                for a in extras_list
            ]

            delivs = Deliverables(ifc=ifc, dxf=dxf, pdfs=pdfs, extras=extras)

            set_status("succeeded", {"step": "done", "pct": 100, "deliverables": delivs.model_dump(), "metrics": manifest.get("metrics", {})})
            if PROM and JOBS_COMPLETED:
                JOBS_COMPLETED.inc()
            if PROM and JOB_DURATION:
                JOB_DURATION.observe(time.time() - start)

        except Exception as e:
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
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    # NEW: read status from JobStore
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
    return {
        "deliverables": files,
        "output_dir": str(out_dir),
    }

@app.get("/api/projects/{project_id}/download/{filename}")
def download(project_id: str, filename: str):
    path = OUTPUT_ROOT / project_id / filename
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path)
