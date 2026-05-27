"""
FireAI Pro — dispatcher.py
===========================
Autonomous background job dispatcher.

Polls the SQLite job store every POLL_INTERVAL seconds.
When a queued job appears, it fires the exact same _run_job() pipeline
that api/app.py already uses — no duplicate logic.

Drop this file at the repo root alongside job_store.py.
It is started/stopped by the lifespan hook in api/app.py (see job_store.py
for the 3-line wiring instructions).
"""

import asyncio
import importlib
import json
import logging
import os
from datetime import datetime

from job_store import next_queued_job, _set_job

log = logging.getLogger("fireai.dispatcher")

POLL_INTERVAL = float(os.getenv("DISPATCHER_POLL_INTERVAL", "5.0"))  # seconds

_running = False


# ─────────────────────────────────────────────────────────────────────────────
# Public control functions (called from api/app.py lifespan)
# ─────────────────────────────────────────────────────────────────────────────

async def start_dispatcher() -> None:
    """
    Infinite async loop. Starts at app startup via the lifespan hook.
    Picks up queued jobs and runs them through the existing _run_job pipeline.
    """
    global _running
    _running = True
    log.info("Dispatcher started — polling every %.1fs", POLL_INTERVAL)

    while _running:
        try:
            await _poll_once()
        except Exception as exc:
            log.error("Dispatcher error: %s", exc, exc_info=True)
        await asyncio.sleep(POLL_INTERVAL)


def stop_dispatcher() -> None:
    global _running
    _running = False
    log.info("Dispatcher stopping")


# ─────────────────────────────────────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────────────────────────────────────

async def _poll_once() -> None:
    """Check for one queued job and run it if found."""
    job = next_queued_job()
    if job is None:
        return  # Queue empty

    job_id = job["job_id"]
    log.info("[%s] Dispatcher picked up job: %s", job_id, job.get("project", "unnamed"))

    # Dynamically import _run_job from api/app.py to avoid circular imports.
    # This means the dispatcher always uses the live version of the pipeline.
    try:
        api_module = importlib.import_module("api.app")
        _run_job   = api_module._run_job
    except Exception as exc:
        log.error("[%s] Cannot import _run_job: %s", job_id, exc)
        _set_job(job_id, status="failed", stage="error",
                 message=f"Dispatcher import error: {exc}",
                 completed_at=datetime.utcnow().isoformat())
        return

    # Reconstruct the arguments _run_job expects from the stored job dict
    project_context  = job.get("project_context", {})
    selected_sheets  = job.get("selected_sheets",  [])
    selected_formats = job.get("selected_formats", [])
    geometry         = job.get("geometry",         {})

    # Mark as running before firing
    _set_job(job_id, status="running", stage="queued",
             message="Picked up by autonomous dispatcher")

    try:
        await _run_job(job_id, project_context, selected_sheets, selected_formats, geometry)
    except Exception as exc:
        log.error("[%s] Pipeline failed: %s", job_id, exc, exc_info=True)
        _set_job(job_id, status="failed", stage="error",
                 message=str(exc),
                 completed_at=datetime.utcnow().isoformat())
