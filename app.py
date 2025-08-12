
import os, uuid, json, threading, traceback, sys
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import importlib

# ---------------- Config ----------------
OUTPUT_ROOT = Path(os.getenv("FIREAI_LOCAL_STORAGE", "./fireai_outputs")).resolve()
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# ---------------- App ----------------
app = FastAPI(title="FireAI Pro API", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- Job store (in-memory) ----------------
JOBS: Dict[str, Dict[str, Any]] = {}

# Candidate orchestrator modules and entry points
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

# ---------------- API ----------------
@app.post("/api/projects")
async def create_project(
    project_id: Optional[str] = Form(None),
    project_json: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """Create a project; optional file upload (DWG/IFC/PDF)."""
    pid = project_id or str(uuid.uuid4())
    out_dir = (OUTPUT_ROOT / pid)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = {}
    if project_json:
        try:
            data = json.loads(project_json)
        except Exception as e:
            raise HTTPException(400, f"Invalid project_json: {e}")
    data.setdefault("project_id", pid)

    saved_file = None
    if file:
        ext = Path(file.filename).suffix or ""
        saved_file = out_dir / f"upload{ext}"
        with saved_file.open("wb") as f_out:
            f_out.write(await file.read())

    # Persist project.json
    with (out_dir / "project.json").open("w") as f:
        json.dump(data, f, indent=2)

    return {"project_id": pid, "saved_file": str(saved_file) if saved_file else None}

@app.post("/api/projects/{project_id}/run")
async def run_project(project_id: str, background: BackgroundTasks):
    """Kick off the design run in a background thread."""
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "step": "queued", "pct": 0, "errors": [], "deliverables": {}}

    def _worker():
        try:
            JOBS[job_id].update(status="routing", step="routing", pct=10)
            with (OUTPUT_ROOT / project_id / "project.json").open() as f:
                pj = json.load(f)
            _run_orchestrator(pj)
            # Scan deliverables
            d = {}
            out_dir = OUTPUT_ROOT / project_id
            if out_dir.exists():
                for p in out_dir.glob("*"):
                    d[p.name] = str(p)
            JOBS[job_id].update(status="succeeded", step="done", pct=100, deliverables=d)
        except Exception as e:
            JOBS[job_id].update(status="failed", step="error", pct=100, errors=[str(e), traceback.format_exc()])

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
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
