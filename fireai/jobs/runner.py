"""Runs a drawing-understanding job and records the outcome.

The job record's ``processing_status`` is exactly the pipeline's status
(completed / needs_human_input / failed). There is no path by which an
exception becomes "completed", and nothing here sets any compliance field.
"""

from __future__ import annotations

import logging
import threading

from fireai.config import Settings
from fireai.errors import FailureCode
from fireai.jobs.store import JobStore
from fireai.pipeline import DELIVERABLES, understand_drawing

log = logging.getLogger("fireai.jobs")
_slots: dict[int, threading.BoundedSemaphore] = {}


def _slot(settings: Settings) -> threading.BoundedSemaphore:
    return _slots.setdefault(settings.max_concurrent_jobs, threading.BoundedSemaphore(settings.max_concurrent_jobs))


def run_job(store: JobStore, job_id: str) -> None:
    settings = store.settings
    with _slot(settings):
        job = store.update(job_id, processing_status="running")
        try:
            src = next(store.source_dir(job_id).glob("upload.*"))
            result = understand_drawing(src, job["source"]["filename"], store.work_dir(job_id),
                                        store.deliverables_dir(job_id), job.get("units_override"), settings)
            out_dir = store.deliverables_dir(job_id)
            registry = {}
            for did, fname in result.deliverables.items():
                p = out_dir / fname
                if p.is_file():
                    registry[did] = {"filename": fname, "content_type": DELIVERABLES[did][1],
                                     "size_bytes": p.stat().st_size}
            rep = result.report
            summary = None
            if result.model is not None:
                summary = {"element_counts": rep.get("element_counts"),
                           "units": (rep.get("units") or {}).get("resolved_units"),
                           "bounds_normalized_ft": rep.get("bounds_normalized_ft"),
                           "review_trigger_codes": [t["code"] for t in rep.get("review_triggers", [])],
                           "unclassified_count": (rep.get("unclassified") or {}).get("count")}
            store.update(job_id, processing_status=result.processing_status, failure=result.failure,
                         requires_human_review=bool(rep.get("requires_human_review", True)),
                         unit_resolution_required=rep.get("unit_resolution_required"),
                         summary=summary, deliverables=registry)
        except Exception as exc:  # never convert to success
            log.exception("job %s crashed", job_id)
            store.update(job_id, processing_status="failed", requires_human_review=True,
                         failure={"code": FailureCode.INTERNAL_ERROR.value, "message": f"{type(exc).__name__}: {exc}",
                                  "details": {}})
