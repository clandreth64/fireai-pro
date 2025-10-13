import os, uuid, json, time
from pathlib import Path
from typing import Optional, Dict
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from starlette.responses import FileResponse as FR
from concurrent.futures import ThreadPoolExecutor

app = FastAPI()

# --- storage root ---
DATA_ROOT = Path(
    os.environ.get("FIREAI_LOCAL_STORAGE")
    or os.environ.get("FIREAI_DATA_ROOT")
    or "/data/projects"
)
DATA_ROOT.mkdir(parents=True, exist_ok=True)

# --- helpers ---
def proj_dir(pid: str) -> Path:
    p = DATA_ROOT / pid
    p.mkdir(parents=True, exist_ok=True)
    return p

def job_dir(pid: str, jid: str) -> Path:
    d = proj_dir(pid) / jid
    d.mkdir(parents=True, exist_ok=True)
    return d

def latest_job_dir(pid: str) -> Optional[Path]:
    p = proj_dir(pid)
    jobs = [d for d in p.iterdir() if d.is_dir()]
    return sorted(jobs, key=lambda d: d.stat().st_mtime, reverse=True)[0] if jobs else None

def list_artifacts(path: Path) -> Dict[str, str]:
    # Find the project root (folder that contains project.json)
    base = path
    cur = path
    while True:
        if (cur / "project.json").exists():
            base = cur
            break
        if cur == cur.parent:
            break
        cur = cur.parent

    out: Dict[str, str] = {}
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        # always return paths relative to the project dir (works for nested job folders)
        out[f.name] = str(f.relative_to(base))
    return out

# --- health/readiness ---
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/readiness")
def readiness():
    try:
        from master_fireai_orchestrator import orchestrate  # noqa
        return {"status": "ready"}
    except Exception as e:
        return JSONResponse(
            {"detail": f"orchestrator not ready: {e.__class__.__name__}: {e}"},
            status_code=503,
        )

# --- create project (multipart or JSON) ---
@app.post("/api/projects")
async def create_project(
    project_id: str | None = Form(None),
    project_json: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    pid = project_id or str(uuid.uuid4())
    pdir = proj_dir(pid)

    # project.json
    meta = {"project_id": pid, "created": int(time.time())}
    if project_json:
        try:
            meta.update(json.loads(project_json))
        except Exception:
            pass
    (pdir / "project.json").write_text(json.dumps(meta, indent=2))

    # upload
    if file:
        (pdir / "upload.pdf").write_bytes(await file.read())

    return {"project_id": pid, "project_dir": str(pdir), "uploaded": bool(file)}

# --- run (creates job_id, calls orchestrator, writes manifest) ---
executor = ThreadPoolExecutor(max_workers=4)

def _run_orchestrator(orchestrator_func, pid: str, jid: str) -> dict:
    jdir = job_dir(pid, jid)
    pdir = proj_dir(pid)

    # Your orchestrator must write outputs into jdir and return {name: absolute_path}
    outputs = orchestrator_func(project_dir=pdir, output_dir=jdir)  # <-- real call

    manifest = {
        "project_id": pid,
        "job_id": jid,
        "output_dir": str(jdir),
        "deliverables": {k: str(Path(v).relative_to(pdir)) for k, v in (outputs or {}).items() if v},
    }
    (jdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest

@app.post("/api/projects/{project_id}/run")
def run_project(project_id: str):
    pid = project_id
    jid = str(uuid.uuid4())
    from master_fireai_orchestrator import orchestrate as orchestrator_func
    executor.submit(_run_orchestrator, orchestrator_func, pid, jid)
    return {"job_id": jid, "status": "queued"}

# --- jobs & results ---
@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    for p in DATA_ROOT.iterdir():
        j = p / job_id
        if j.is_dir():
            status = "succeeded" if (j / "manifest.json").exists() else "collecting"
            return {
                "job_id": job_id,
                "status": status,
                "step": "done" if status == "succeeded" else "orchestrator",
                "pct": 100 if status == "succeeded" else 30,
            }
    return JSONResponse({"detail": "Not Found"}, status_code=404)

@app.get("/api/projects/{project_id}/results")
def project_results(project_id: str):
    jdir = latest_job_dir(project_id)
    base = jdir if jdir else proj_dir(project_id)
    return {"output_dir": str(base), "deliverables": list_artifacts(base)}

@app.get("/api/projects/{project_id}/jobs/{job_id}/artifacts")
def job_artifacts(project_id: str, job_id: str):
    jdir = job_dir(project_id, job_id)
    m = jdir / "manifest.json"
    if m.exists():
        return json.loads(m.read_text())
    return {"project_id": project_id, "job_id": job_id, "deliverables": list_artifacts(jdir)}

# --- download (supports nested paths) ---
@app.get("/api/projects/{project_id}/download/{filename:path}")
def download(project_id: str, filename: str):
    pdir = proj_dir(project_id)
    path = (pdir / filename).resolve()
    if not str(path).startswith(str(pdir.resolve())):
        raise HTTPException(status_code=403, detail="Invalid path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return FR(str(path), filename=path.name)
