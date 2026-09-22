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
MAX_XREF_FILES = 32

PUBLIC_FIELDS = ("job_id", "created_at", "updated_at", "processing_status", "engineering_review_status",
                 "ready_for_design", "requires_human_review", "failure", "source", "units_override",
                 "parent_job_id", "summary", "unit_resolution_required", "xref_files")


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


async def _save(upload: UploadFile, dest: Path, limit: int) -> bool:
    """Stream an upload to ``dest``; False (and nothing kept) if it exceeds ``limit``."""
    written = 0
    with dest.open("wb") as out:
        while chunk := await upload.read(CHUNK):
            written += len(chunk)
            if written > limit:
                out.close()
                dest.unlink(missing_ok=True)
                return False
            out.write(chunk)
    await upload.close()
    return True


def _fail_job(store: JobStore, job_id: str, status: int, f: PipelineFailure) -> JSONResponse:
    shutil.rmtree(store.job_dir(job_id), ignore_errors=True)
    store.update(job_id, processing_status="failed", requires_human_review=True, failure=f.to_dict())
    return JSONResponse(status_code=status, content={"detail": f.to_dict(), "job_id": job_id})


@router.post("", status_code=202)
async def submit_drawing(background: BackgroundTasks, file: UploadFile = File(...),
                         units: str | None = Form(None), xrefs: list[UploadFile] | None = File(None),  # noqa: B008
                         store: JobStore = Depends(get_store)):
    """Submit a drawing, optionally with the files it references as XREFs.

    XREFs are matched to the drawing's references by FILE NAME only; paths
    stored inside the drawing are never opened."""
    display = sanitize_filename(file.filename)
    ext = Path(display).suffix.lower()
    try:
        override = parse_user_units(units)
    except PipelineFailure as f:
        return _error(422, f)
    if ext not in (".dxf", ".dwg"):
        return _error(415, PipelineFailure(FailureCode.UNSUPPORTED_FORMAT,
                                           f"'{ext or '(none)'}' is not supported. Upload a .dxf or .dwg file."))
    xrefs = [x for x in (xrefs or []) if x is not None and (x.filename or "")]
    if len(xrefs) > MAX_XREF_FILES:
        return _error(422, PipelineFailure(FailureCode.INVALID_DRAWING,
                                           f"At most {MAX_XREF_FILES} XREF files may be supplied with one drawing."))
    names = [sanitize_filename(x.filename) for x in xrefs]
    if len({n.lower() for n in names}) != len(names):
        return _error(422, PipelineFailure(FailureCode.INVALID_DRAWING,
                                           "Two supplied XREF files have the same name; XREFs are matched by name, "
                                           "so the match would be ambiguous.", {"xref_files": names}))
    job = store.create(display, ext[1:], override)
    jid = job["job_id"]
    dest = store.source_dir(jid) / f"upload{ext}"   # server-chosen name, never the client's
    limit = store.settings.max_upload_bytes
    if not await _save(file, dest, limit):
        return _fail_job(store, jid, 413, PipelineFailure(FailureCode.FILE_TOO_LARGE, "Upload exceeds the size limit."))
    try:
        validate_upload(dest, display)
    except PipelineFailure as f:
        os.unlink(dest)
        return _fail_job(store, jid, 422, f)
    if xrefs:
        xdir = store.xref_dir(jid)
        xdir.mkdir(parents=True, exist_ok=True)
        for x, name in zip(xrefs, names, strict=True):
            # the sanitized basename is kept because XREF matching is by name; it
            # cannot contain a path separator and lives in the job's own xrefs dir
            xdest = xdir / name
            if not await _save(x, xdest, limit):
                return _fail_job(store, jid, 413, PipelineFailure(FailureCode.FILE_TOO_LARGE,
                                                                  f"XREF '{name}' exceeds the size limit."))
            try:
                validate_upload(xdest, name)
            except PipelineFailure as f:
                return _fail_job(store, jid, 422, PipelineFailure(f.code, f"XREF '{name}': {f.message}", f.details))
        store.update(jid, xref_files=names)
    background.add_task(run_job, store, jid)
    return {"job_id": jid, "processing_status": "queued", "xref_files": names,
            "status_url": f"/api/v2/drawings/{jid}"}


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
    if store.xref_dir(job_id).is_dir():
        shutil.copytree(store.xref_dir(job_id), store.xref_dir(new["job_id"]))
        store.update(new["job_id"], xref_files=job.get("xref_files") or [])
    background.add_task(run_job, store, new["job_id"])
    return {"job_id": new["job_id"], "processing_status": "queued", "parent_job_id": job_id,
            "status_url": f"/api/v2/drawings/{new['job_id']}"}


# ── human review (Milestone 1.6) ─────────────────────────────────────────────

def _job_model(store: JobStore, job_id: str):
    from fireai.schema import load_model
    job = _job_or_404(store, job_id)
    try:
        path, _entry = store.deliverable_path(job_id, "model_json")
    except KeyError:
        raise HTTPException(409, {"code": "NO_MODEL", "message": "This job produced no building model."}) from None
    import json as _json
    model, _applied = load_model(_json.loads(path.read_text(encoding="utf-8")))
    return job, model


def _review_store(store: JobStore):
    from fireai.review.store import ReviewStore
    return ReviewStore(store.settings.review_dir)


class Correction(BaseModel):
    kind: str
    data: dict
    reviewer: str
    note: str | None = None


class Verification(BaseModel):
    reviewer: str
    decision: str
    checklist: dict[str, dict]
    acknowledged_triggers: list[str] = []
    selected_region_uids: list[str] = []
    note: str | None = None


@router.get("/{job_id}/verification")
def get_verification(job_id: str, store: JobStore = Depends(get_store)):
    from fireai.review.gate import engineering_readiness
    from fireai.review.store import OPTIONAL_CATEGORIES, REQUIRED_CATEGORIES
    _job, model = _job_model(store, job_id)
    rs = _review_store(store)
    return {"job_id": job_id, "readiness": engineering_readiness(model, rs),
            "required_categories": list(REQUIRED_CATEGORIES), "optional_categories": list(OPTIONAL_CATEGORIES),
            "review_trigger_codes": sorted({t.code for t in model.diagnostics.review_triggers}),
            "regions": [{k: r.get(k) for k in ("id", "uid", "view_type", "view_type_confidence", "review_state",
                                              "significant", "bbox_ft")} for r in model.view_regions],
            "corrections": rs.corrections(model.source.sha256),
            "human_corrections_applied": model.human_corrections_applied}


@router.post("/{job_id}/verification")
def post_verification(job_id: str, body: Verification, store: JobStore = Depends(get_store)):
    from fireai.review.gate import engineering_readiness
    from fireai.review.store import ReviewError
    _job, model = _job_model(store, job_id)
    rs = _review_store(store)
    try:
        rec = rs.record_verification(model, body.reviewer, body.decision, body.checklist,
                                     body.acknowledged_triggers, body.selected_region_uids, body.note)
    except ReviewError as exc:
        raise HTTPException(422, {"code": "REVIEW_REJECTED", "message": str(exc)}) from None
    return {"recorded": rec["status"], "readiness": engineering_readiness(model, rs)}


@router.post("/{job_id}/corrections", status_code=201)
def post_correction(job_id: str, body: Correction, store: JobStore = Depends(get_store)):
    from fireai.review.store import ReviewError
    _job, model = _job_model(store, job_id)
    try:
        rec = _review_store(store).add_correction(model, body.kind, body.data, body.reviewer, body.note)
    except ReviewError as exc:
        raise HTTPException(422, {"code": "CORRECTION_REJECTED", "message": str(exc)}) from None
    return {"correction": rec,
            "note": "Stored. It is applied when the drawing is reprocessed (POST /reprocess); any existing "
                    "verification of this drawing becomes INVALIDATED on that run."}


@router.post("/{job_id}/reprocess", status_code=202)
def reprocess(job_id: str, background: BackgroundTasks, store: JobStore = Depends(get_store)):
    """Re-run the same source (and XREFs, units) — e.g. to apply stored corrections. Creates a NEW job."""
    job = _job_or_404(store, job_id)
    srcs = list(store.source_dir(job_id).glob("upload.*"))
    if not srcs:
        raise HTTPException(409, {"code": "SOURCE_UNAVAILABLE", "message": "Original upload is not available."})
    new = store.create(job["source"]["filename"], job["source"]["format"], job.get("units_override"),
                       parent_job_id=job_id)
    shutil.copyfile(srcs[0], store.source_dir(new["job_id"]) / srcs[0].name)
    if store.xref_dir(job_id).is_dir():
        shutil.copytree(store.xref_dir(job_id), store.xref_dir(new["job_id"]))
        store.update(new["job_id"], xref_files=job.get("xref_files") or [])
    background.add_task(run_job, store, new["job_id"])
    return {"job_id": new["job_id"], "processing_status": "queued", "parent_job_id": job_id,
            "status_url": f"/api/v2/drawings/{new['job_id']}"}
