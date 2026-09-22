"""/api/v2/drawings — drawing-understanding endpoints.

Downloads are addressed by deliverable id only (e.g. ``overlay_png``). The
server looks the id up in that job's registry; there is no user-controlled
path component anywhere in file access.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from fireai.api.security import require_principal
from fireai.errors import FailureCode, PipelineFailure
from fireai.ingest.filetype import sanitize_filename, validate_upload
from fireai.ingest.units import parse_user_units
from fireai.jobs.runner import run_job
from fireai.jobs.store import JobStore

router = APIRouter(prefix="/api/v2/drawings", tags=["drawing-understanding"])
CHUNK = 1024 * 1024

PUBLIC_FIELDS = ("job_id", "created_at", "updated_at", "processing_status", "engineering_review_status",
                 "ready_for_design", "requires_human_review", "failure", "source", "units_override",
                 "parent_job_id", "summary", "unit_resolution_required")


def get_store(principal=Depends(require_principal)) -> JobStore:  # noqa: B008
    from fireai.api.app import current_store
    return current_store()


def _public(job: dict) -> dict:
    out = {k: job.get(k) for k in PUBLIC_FIELDS}
    out["deliverables"] = [{"id": k, **{f: v[f] for f in ("filename", "content_type", "size_bytes")}}
                           for k, v in (job.get("deliverables") or {}).items()]
    return out


def _error(status: int, f: PipelineFailure) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": f.to_dict()})


def _job_or_404(store: JobStore, job_id: str) -> dict:
    try:
        return store.get(job_id)
    except KeyError:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Job not found."}) from None


@router.post("", status_code=202)
async def submit_drawing(background: BackgroundTasks, file: UploadFile = File(...),
                         units: str | None = Form(None), store: JobStore = Depends(get_store)):
    display = sanitize_filename(file.filename)
    ext = Path(display).suffix.lower()
    try:
        override = parse_user_units(units)
    except PipelineFailure as f:
        return _error(422, f)
    if ext not in (".dxf", ".dwg"):
        return _error(415, PipelineFailure(FailureCode.UNSUPPORTED_FORMAT,
                                           f"'{ext or '(none)'}' is not supported. Upload a .dxf or .dwg file."))
    job = store.create(display, ext[1:], override)
    dest = store.source_dir(job["job_id"]) / f"upload{ext}"   # server-chosen name, never the client's
    written = 0
    with dest.open("wb") as out:
        while chunk := await file.read(CHUNK):
            written += len(chunk)
            if written > store.settings.max_upload_bytes:
                out.close()
                shutil.rmtree(store.job_dir(job["job_id"]), ignore_errors=True)
                store.update(job["job_id"], processing_status="failed", requires_human_review=True,
                             failure={"code": "FILE_TOO_LARGE", "message": "Upload exceeds size limit.", "details": {}})
                return _error(413, PipelineFailure(FailureCode.FILE_TOO_LARGE, "Upload exceeds the size limit."))
            out.write(chunk)
    await file.close()
    try:
        validate_upload(dest, display)
    except PipelineFailure as f:
        store.update(job["job_id"], processing_status="failed", requires_human_review=True, failure=f.to_dict())
        os.unlink(dest)
        return JSONResponse(status_code=422, content={"detail": f.to_dict(), "job_id": job["job_id"]})
    background.add_task(run_job, store, job["job_id"])
    return {"job_id": job["job_id"], "processing_status": "queued",
            "status_url": f"/api/v2/drawings/{job['job_id']}"}


@router.get("/{job_id}")
def get_job(job_id: str, store: JobStore = Depends(get_store)):
    return _public(_job_or_404(store, job_id))


@router.get("/{job_id}/deliverables")
def list_deliverables(job_id: str, store: JobStore = Depends(get_store)):
    return {"job_id": job_id, "deliverables": _public(_job_or_404(store, job_id))["deliverables"]}


@router.get("/{job_id}/deliverables/{deliverable_id}")
def download(job_id: str, deliverable_id: str, store: JobStore = Depends(get_store)):
    try:
        path, entry = store.deliverable_path(job_id, deliverable_id)
    except KeyError:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Deliverable not found."}) from None
    inline = entry["content_type"] in ("image/png", "application/json")
    return FileResponse(path, media_type=entry["content_type"], filename=entry["filename"],
                        content_disposition_type="inline" if inline else "attachment")


class UnitResolution(BaseModel):
    units: str


@router.post("/{job_id}/resolve-units", status_code=202)
def resolve_units(job_id: str, body: UnitResolution, background: BackgroundTasks,
                  store: JobStore = Depends(get_store)):
    """Re-run a drawing with units supplied by the user. Creates a NEW job; the
    original record is kept for traceability."""
    job = _job_or_404(store, job_id)
    try:
        override = parse_user_units(body.units)
    except PipelineFailure as f:
        return _error(422, f)
    srcs = list(store.source_dir(job_id).glob("upload.*"))
    if not srcs:
        raise HTTPException(409, {"code": "SOURCE_UNAVAILABLE", "message": "Original upload is not available."})
    new = store.create(job["source"]["filename"], job["source"]["format"], override, parent_job_id=job_id)
    shutil.copyfile(srcs[0], store.source_dir(new["job_id"]) / srcs[0].name)
    background.add_task(run_job, store, new["job_id"])
    return {"job_id": new["job_id"], "processing_status": "queued", "parent_job_id": job_id,
            "status_url": f"/api/v2/drawings/{new['job_id']}"}
