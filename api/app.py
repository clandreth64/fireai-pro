from __future__ import annotations

import os
import sys
sys.path.append('..')  # This looks in the parent folder (root) for modules
import uuid
import json
import logging
import traceback
import sys
import glob
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse
import importlib
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==== FireAI: data/projects root for artifacts ====
DATA_ROOT = Path(os.environ.get("FIREAI_DATA_ROOT", "/data/projects")).resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)

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

# Config - Updated to use DATA_ROOT consistently
OUTPUT_ROOT = DATA_ROOT  # Use DATA_ROOT for consistency
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
ORCH_TIMEOUT_SECS = int(os.getenv("ORCH_TIMEOUT_SECS", "900"))  # 15 minutes for artifact publishing
API_KEY = os.getenv("API_KEY")

# App
app = FastAPI(
    title="FireAI Pro API with Artifact Publishing",
    version="1.2.0",
    description="Enhanced FireAI API with comprehensive artifact publishing support"
)

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

def _require_api_key():
    """Simplified auth helper for the enhanced create_project function"""
    return True  # Uses the existing middleware/dependency injection

# Job store
STORE = JobStore(namespace="fireai", ttl_seconds=7 * 24 * 3600)

# Metrics
if PROM:
    JOBS_CREATED = Counter("fireai_jobs_created_total", "Jobs created")
    JOBS_COMPLETED = Counter("fireai_jobs_completed_total", "Jobs completed")
    JOBS_FAILED = Counter("fireai_jobs_failed_total", "Jobs failed")
    JOB_DURATION = Histogram("fireai_job_duration_seconds", "Job duration in seconds")
else:
    JOBS_CREATED = JOBS_COMPLETED = JOBS_FAILED = JOB_DURATION = None

# Orchestrator - Updated to support both legacy and new orchestrators
ORCH_MODULES = [
    "master_fireai_orchestrator",  # Enhanced version with publishing
    "fireai_orchestrator_enhanced",  # Alternative name
    "orchestrator"  # Fallback
]
ORCH_FUNCS = [
    "run_project", 
    "run_pipeline", 
    "process_project", 
    "orchestrate_project", 
    "handle_project",
    "process_design"  # New enhanced function
]

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
                name_map.pop(name, None)
    except Exception:
        logger.warning("Failed to unload Prometheus collectors")

def _load_orchestrator():
    """Enhanced orchestrator loading with multiple module support"""
    try:
        _prometheus_unload()
        
        # Try to load enhanced orchestrator modules first
        for module_name in ORCH_MODULES:
            try:
                logger.info(f"Attempting to import orchestrator: {module_name}")
                orch = importlib.import_module(module_name)
                
                # Check if it has the enhanced MasterOrchestrator class
                if hasattr(orch, 'MasterOrchestrator'):
                    logger.info(f"Found enhanced orchestrator in {module_name}")
                    return orch
                elif hasattr(orch, 'orchestrator') and hasattr(orch.orchestrator, 'process_design'):
                    logger.info(f"Found orchestrator instance in {module_name}")
                    return orch
                else:
                    # Check for any of the expected functions
                    for func_name in ORCH_FUNCS:
                        if hasattr(orch, func_name):
                            logger.info(f"Found orchestrator function {func_name} in {module_name}")
                            return orch
                            
            except ImportError as e:
                logger.debug(f"Could not import {module_name}: {e}")
                continue
                
        logger.error("No suitable orchestrator module found")
        return None
        
    except Exception as e:
        logger.error(f"Failed to load orchestrator: {e}")
        cwd = os.getcwd()
        files = [f for f in os.listdir(cwd) if f.endswith(".py")]
        error_msg = (
            f"Could not load any orchestrator module. Error: {e}\n"
            f"CWD: {cwd}\nPYTHONPATH: {sys.path}\nPython files in CWD: {files}"
        )
        logger.error(error_msg)
        return None

def _run_orchestrator(project_json: dict):
    """Enhanced orchestrator runner with support for new API"""
    logger.info("Running orchestrator with enhanced support")
    orch = _load_orchestrator()
    if orch is None:
        logger.warning("No orchestrator module loaded; returning empty manifest")
        return {}
    
    # Try enhanced orchestrator first (with MasterOrchestrator class)
    if hasattr(orch, 'MasterOrchestrator'):
        try:
            logger.info("Using MasterOrchestrator class")
            settings_class = getattr(orch, 'Settings', None)
            if settings_class:
                settings = settings_class()
                master_orch = orch.MasterOrchestrator(settings)
                
                # Use async process_design if available
                if hasattr(master_orch, 'process_design'):
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        result = loop.run_until_complete(
                            master_orch.process_design(
                                project_json,
                                project_json.get('input_file')
                            )
                        )
                        return result or {}
                    finally:
                        loop.close()
            else:
                logger.warning("Settings class not found in orchestrator module")
        except Exception as e:
            logger.warning(f"MasterOrchestrator failed: {e}")
    
    # Try orchestrator instance
    if hasattr(orch, 'orchestrator'):
        try:
            logger.info("Using orchestrator instance")
            orch_instance = orch.orchestrator
            if hasattr(orch_instance, 'process_design'):
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(
                        orch_instance.process_design(
                            project_json,
                            project_json.get('input_file')
                        )
                    )
                    return result or {}
                finally:
                    loop.close()
        except Exception as e:
            logger.warning(f"Orchestrator instance failed: {e}")
    
    # Fallback to legacy function-based approach
    for fn in ORCH_FUNCS:
        f = getattr(orch, fn, None)
        if callable(f):
            logger.info(f"Trying legacy orchestrator function: {fn}")
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

def _to_dict(obj):
    """Helper to convert objects to dict"""
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        return obj.model_dump()
    elif hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        return obj.dict()
    elif hasattr(obj, "__dict__"):
        return obj.__dict__
    return {}

# Health / Readiness / Metrics
@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "1.2.0",
        "features": {
            "artifact_publishing": True,
            "enhanced_orchestrator": True,
            "prometheus_metrics": PROM,
            "s3_upload": upload_deliverables_to_s3 is not None
        }
    }

@app.get("/readiness")
def readiness():
    try:
        orch = _load_orchestrator()
        if orch is None:
            raise Exception("No orchestrator module available")
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
    return {"status": "ready", "data_root": str(DATA_ROOT)}

if PROM:
    @app.get("/metrics")
    def metrics():
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

# --- BEGIN: Enhanced /api/projects create handler with server fix ---
@app.post("/api/projects", dependencies=[Depends(require_api_key)])
async def create_project(
    request: Optional[str] = Form(None),           # multipart: JSON string in "request"
    file: Optional[UploadFile] = File(None),       # multipart: "file"
    body: Optional[dict] = None,                   # application/json body
    project_id: Optional[str] = Form(None),        # legacy support
    project_json: Optional[str] = Form(None),      # legacy support
    _auth: bool = Depends(_require_api_key),
):
    """
    Enhanced create project handler with robust error handling and streaming file upload.
    Supports both new enhanced format and legacy compatibility.
    """
    try:
        # 1) Normalize payload - Handle multiple input formats
        payload = {}
        
        # New enhanced format priority
        if isinstance(body, dict) and body:
            payload = body
        elif request:
            try:
                payload = json.loads(request)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in form field 'request'")
        
        # Legacy format fallback
        elif project_json:
            try:
                payload = json.loads(project_json)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in project_json field")
        
        # Default empty payload if none provided
        if not payload:
            payload = {}

        # Extract project details with legacy compatibility
        project_name = (payload.get("project_name") or 
                       payload.get("name") or 
                       "Untitled Project")
        zip_code = payload.get("zip_code", "")
        project_data = (payload.get("project_data") or 
                       payload.get("metadata") or 
                       payload.get("data", {}))

        # 2) Allocate project folder
        pid = project_id or payload.get("project_id") or str(uuid.uuid4())
        project_dir = DATA_ROOT / pid
        project_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Creating project: {pid}, project_dir: {project_dir}")

        # 3) Build comprehensive project.json
        project_config = {
            "project_id": pid,
            "project_name": project_name,
            "zip_code": zip_code,
            "project_data": project_data,
            # Add original payload for enhanced orchestrator compatibility
            **payload
        }

        # 4) Handle file upload with streaming and proper file type detection
        uploaded = False
        saved_file_path = None
        
        if file is not None:
            # Determine file extension and validate
            filename = file.filename or "upload"
            file_ext = Path(filename).suffix.lower() or ".pdf"  # Default to PDF
            
            # Enhanced file type support
            allowed_extensions = {".dxf", ".dwg", ".ifc", ".zip", ".pdf", ".txt", ".csv"}
            if file_ext not in allowed_extensions:
                logger.warning(f"Unsupported file type: {file_ext}")
                # Don't fail - save as .dat for processing by orchestrator
                file_ext = ".dat"
            
            # Stream the file to disk (no memory loading)
            saved_file_path = project_dir / f"upload{file_ext}"
            try:
                with saved_file_path.open("wb") as out:
                    shutil.copyfileobj(file.file, out)
                uploaded = True
                logger.info(f"Saved uploaded file: {saved_file_path}")
                
                # Add file info to project config for orchestrator
                project_config["input_file"] = str(saved_file_path)
                project_config["uploaded_file_path"] = str(saved_file_path)
                project_config["original_filename"] = filename
                
            except Exception as e:
                logger.error(f"Failed to save uploaded file: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {e}")

        # 5) Write project.json with all configuration
        project_json_path = project_dir / "project.json"
        try:
            with project_json_path.open("w", encoding="utf-8") as f:
                json.dump(project_config, f, indent=2, ensure_ascii=False)
            logger.info(f"Wrote project.json for project: {pid}")
        except Exception as e:
            logger.error(f"Failed to write project.json: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to write project configuration: {e}")

        # 6) Return success response
        response_data = {
            "project_id": pid,
            "message": "project created successfully",
            "uploaded": uploaded,
            "project_dir": str(project_dir)
        }
        
        if saved_file_path:
            response_data["saved_file"] = str(saved_file_path)
            
        logger.info(f"Project {pid} created successfully")
        return JSONResponse(status_code=200, content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        # Enhanced error logging with full traceback
        error_details = f"Create project failed: {str(e)}"
        logger.error(f"ERROR create_project: {error_details}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_details)
# --- END: Enhanced create handler ---

@app.post("/api/projects/{project_id}/run", dependencies=[Depends(require_api_key)])
async def run_project(project_id: str, background: BackgroundTasks, body: dict = Body(default_factory=dict)):
    """Kick off the design run in a background task with enhanced orchestrator support."""
    options = body or {}
    proj_dir = OUTPUT_ROOT / project_id
    logger.info(f"Starting job for project_id: {project_id}, proj_dir: {proj_dir}, options: {options}")
    
    if not proj_dir.exists():
        logger.error(f"Project directory not found: {proj_dir}")
        raise HTTPException(404, "project not found")

    # Create initial job record
    job_id = str(uuid.uuid4())
    jr_dict = {"job_id": job_id, "project_id": project_id, "status": "queued", "deliverables": None}
    STORE.set(job_id, jr_dict)
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
            
            # Merge in options from the request body
            pj.update(options)
            
            logger.info(f"Loaded project.json for job_id: {job_id}")

            # Run orchestrator with enhanced timeout for artifact publishing
            set_status("running", {"pct": 30, "step": "orchestrator"})
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_run_orchestrator, pj)
                try:
                    result = fut.result(timeout=ORCH_TIMEOUT_SECS) or {}
                    logger.info(f"Orchestrator completed for job_id: {job_id}, result: {result}")
                    
                    # Enhanced result handling for new orchestrator
                    if isinstance(result, dict):
                        if result.get("status") == "completed":
                            manifest = result.get("deliverables", {})
                        elif result.get("status") == "failed":
                            raise Exception(f"Orchestrator failed: {result.get('error', 'Unknown error')}")
                        else:
                            manifest = result
                    else:
                        manifest = {}
                        
                except FuturesTimeoutError:
                    err = f"Orchestrator exceeded timeout ({ORCH_TIMEOUT_SECS}s)"
                    logger.error(err)
                    set_status(
                        "failed",
                        {
                            "step": "timeout",
                            "pct": 100,
                            "errors": [err],
                            "error": _to_dict(ErrorInfo(code="TIMEOUT", message=err, engine="orchestrator")),
                        },
                    )
                    if PROM and JOBS_FAILED:
                        JOBS_FAILED.inc()
                    return

            # Enhanced deliverables collection with artifact publishing support
            set_status("collecting", {"pct": 80, "step": "collecting_artifacts"})
            
            # Check for enhanced manifest first
            artifacts_json = proj_dir / "artifacts.json"
            manifest_json = proj_dir / "manifest.json"
            
            if artifacts_json.exists():
                logger.info("Found enhanced artifacts.json manifest")
                try:
                    with artifacts_json.open() as f:
                        artifact_manifest = json.load(f)
                    
                    # Extract deliverables from enhanced manifest
                    deliverables_dict = {}
                    for artifact in artifact_manifest.get("artifacts", []):
                        name = artifact.get("name", "")
                        path = proj_dir / name
                        if path.exists():
                            deliverables_dict[name] = str(path)
                    
                    # Create enhanced deliverables object
                    dxf = deliverables_dict.get("design.dxf")
                    ifc = deliverables_dict.get("model.ifc")
                    
                    pdfs = {}
                    extras = []
                    
                    for name, path in deliverables_dict.items():
                        if name.endswith('.pdf'):
                            pdf_key = name.replace('.pdf', '')
                            pdfs[pdf_key] = path
                        elif name not in ['design.dxf', 'model.ifc'] and not name.endswith('.json'):
                            # Determine artifact kind based on file extension
                            ext = Path(name).suffix.lower()
                            if ext == '.csv':
                                kind = 'bom'
                            elif ext == '.txt':
                                kind = 'log'
                            elif ext == '.dwg':
                                kind = 'dwg'
                            else:
                                kind = 'other'
                            
                            extras.append(Artifact(
                                kind=kind,
                                name=name,
                                path=path,
                                meta={}
                            ))
                    
                    delivs = Deliverables(ifc=ifc, dxf=dxf, pdfs=pdfs, extras=extras)
                    logger.info(f"Enhanced deliverables from artifacts.json: {delivs}")
                    
                except Exception as e:
                    logger.warning(f"Failed to parse artifacts.json: {e}")
                    # Fall back to legacy collection
                    delivs = _collect_legacy_deliverables(proj_dir, manifest)
            else:
                # Legacy deliverables collection
                delivs = _collect_legacy_deliverables(proj_dir, manifest)

            # Optional S3 upload
            if upload_deliverables_to_s3:
                logger.info("S3 uploader available, attempting upload")
                try:
                    delivs = upload_deliverables_to_s3(delivs, project_id)
                    logger.info(f"S3 upload successful for job_id: {job_id}")
                except Exception as e:
                    logger.warning(f"S3 upload failed for job_id: {job_id}: {e}")
                    # continue without failing the job

            # Success
            set_status(
                "succeeded",
                {
                    "step": "done",
                    "pct": 100,
                    "deliverables": _to_dict(delivs),
                    "metrics": manifest.get("metrics", {}) if isinstance(manifest, dict) else {},
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
                    "error": _to_dict(ErrorInfo(
                        code="ORCH_FAIL", 
                        message=str(e), 
                        engine="orchestrator", 
                        hint="See server logs"
                    )),
                },
            )

    background.add_task(_worker)
    return {"job_id": job_id, "project_id": project_id, "status": "queued"}

def _collect_legacy_deliverables(proj_dir: Path, manifest: dict) -> Deliverables:
    """Legacy deliverables collection for backward compatibility"""
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
    extras: list[Artifact] = []
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

    # Include the originally uploaded file
    upload_matches = glob.glob(str(proj_dir / "upload.*"))
    if upload_matches:
        up = upload_matches[0]
        ext = Path(up).suffix.lower()
        base = Path(up).stem

        pdfs = (pdfs or {})
        extras = (extras or [])

        if ext == ".dxf":
            dxf = up
        elif ext == ".ifc":
            ifc = up
        elif ext == ".pdf":
            pdfs[base] = up
        elif ext == ".dwg":
            extras.append(Artifact(kind="dwg", name=Path(up).name, path=up, meta={}))
        else:
            extras.append(Artifact(kind="upload", name=Path(up).name, path=up, meta={}))

    return Deliverables(ifc=ifc, dxf=dxf, pdfs=pdfs, extras=extras)

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = STORE.get(job_id)
    if not job:
        logger.warning(f"Job not found: {job_id}")
        raise HTTPException(404, "job not found")
    return job

# ==== FireAI: results manifest endpoint (reads artifacts.json / manifest.json) ====
@app.get("/api/projects/{project_id}/results")
def get_project_results(project_id: str):
    """Enhanced results endpoint that reads from artifacts.json or manifest.json"""
    project_dir = DATA_ROOT / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    manifest = None
    manifest_source = None
    
    # Try enhanced manifest first, then legacy
    for fname in ("artifacts.json", "manifest.json"):
        mf = project_dir / fname
        if mf.exists():
            try:
                manifest = json.loads(mf.read_text())
                manifest_source = fname
                logger.info(f"Loaded manifest from {fname} for project {project_id}")
                break
            except Exception as e:
                logger.warning(f"Failed to parse {fname}: {e}")
                pass

    if manifest and isinstance(manifest, dict):
        # Handle enhanced artifacts.json format
        if manifest_source == "artifacts.json" and "artifacts" in manifest:
            deliverables = {}
            for artifact in manifest.get("artifacts", []):
                name = artifact.get("name", "")
                if name:
                    deliverables[name] = str(project_dir / name)
            
            return JSONResponse(
                content={
                    "output_dir": str(project_dir),
                    "deliverables": deliverables,
                    "manifest_type": "enhanced",
                    "summary": manifest.get("processing_summary", {}),
                    "pipeline_version": manifest.get("pipeline_version", "unknown")
                }
            )
        
        # Handle legacy manifest.json format
        elif "deliverables" in manifest:
            deliverables = manifest.get("deliverables", {})
            return JSONResponse(
                content={
                    "output_dir": str(project_dir),
                    "deliverables": {k: str(v) for k, v in deliverables.items()},
                    "manifest_type": "legacy"
                }
            )

    # Fallback: enumerate files if no manifest yet
    logger.info(f"No manifest found for {project_id}, enumerating files")
    files = []
    deliverables = {}
    
    for p in project_dir.rglob("*"):
        if p.is_file() and not p.name.startswith('.'):
            rel_path = str(p.relative_to(project_dir))
            files.append(rel_path)
            deliverables[p.name] = str(p)
    
    return JSONResponse(
        content={
            "output_dir": str(project_dir),
            "files": files,
            "deliverables": deliverables,
            "manifest_type": "fallback",
            "message": "No manifest found, listing all files"
        }
    )

# ==== FireAI: download a specific artifact ====
@app.get("/api/projects/{project_id}/download/{filename:path}")
def download_project_file(project_id: str, filename: str):
    """Enhanced download endpoint with security checks"""
    file_path = DATA_ROOT / project_id / filename
    
    # Security check - ensure file is within project directory
    try:
        file_path_resolved = file_path.resolve()
        project_dir_resolved = (DATA_ROOT / project_id).resolve()
        
        if not str(file_path_resolved).startswith(str(project_dir_resolved)):
            logger.warning(f"Security violation: attempted path traversal for {file_path}")
            raise HTTPException(status_code=403, detail="Access denied")
            
        if not file_path.exists() or not file_path.is_file():
            logger.warning(f"File not found: {file_path}")
            raise HTTPException(status_code=404, detail="File not found")
            
        logger.info(f"Serving download: {file_path}")
        return FileResponse(str(file_path))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download error for {project_id}/{filename}: {e}")
        raise HTTPException(status_code=500, detail="Download failed")

# Legacy endpoint for backward compatibility
@app.get("/api/projects/{project_id}/download/{filename}")
def download_legacy(project_id: str, filename: str):
    """Legacy download endpoint - redirects to enhanced version"""
    return download_project_file(project_id, filename)

# Root endpoint with enhanced information
@app.get("/")
def root():
    return {
        "service": "FireAI Pro API with Enhanced Artifact Publishing",
        "version": "1.2.0",
        "features": {
            "artifact_publishing": True,
            "enhanced_orchestrator_support": True,
            "legacy_compatibility": True,
            "robust_file_handling": True,
            "streaming_uploads": True,
            "prometheus_metrics": PROM,
            "s3_upload": upload_deliverables_to_s3 is not None
        },
        "endpoints": {
            "create_project": "POST /api/projects",
            "run_project": "POST /api/projects/{project_id}/run",
            "get_job": "GET /api/jobs/{job_id}",
            "get_results": "GET /api/projects/{project_id}/results",
            "download": "GET /api/projects/{project_id}/download/{filename}",
            "health": "GET /health",
            "readiness": "GET /readiness"
        },
        "data_root": str(DATA_ROOT),
        "timeout_seconds": ORCH_TIMEOUT_SECS
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
