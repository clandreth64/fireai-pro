"""
FireAI Pro — job_store.py
==========================
Drop-in SQLite replacement for the in-memory _jobs dict in api/app.py.

Keeps the EXACT same interface:
    _set_job(job_id, **kw)
    _get_job(job_id)

Drop this file at the repo root, then make 3 small edits to api/app.py
(see the comment block at the bottom of this file).

Jobs now survive Railway restarts. The background dispatcher (dispatcher.py)
reads from this store to pick up queued jobs automatically.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from fastapi import HTTPException
from pathlib import Path

log = logging.getLogger("fireai.job_store")

# ── DB location — Railway mounts a persistent volume at /data if configured,
#    otherwise falls back to the repo root. Either way it survives restarts.
_DB_PATH = os.getenv("JOB_DB_PATH", str(Path(__file__).parent / "fireai_jobs.db"))

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    """Create tables on first run. Safe to call multiple times."""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id      TEXT PRIMARY KEY,
                status      TEXT NOT NULL DEFAULT 'queued',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                data_json   TEXT NOT NULL DEFAULT '{}'
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_created ON jobs(created_at)")
    log.info("Job store initialised at %s", _DB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# Public API  — same signatures as the old _jobs dict helpers
# ─────────────────────────────────────────────────────────────────────────────

def _set_job(job_id: str, **kw) -> None:
    """
    Create or update a job record.
    Identical signature to the original _set_job — no call-site changes needed.
    """
    now = datetime.utcnow().isoformat()

    with _conn() as c:
        row = c.execute(
            "SELECT data_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()

        if row is None:
            # New job
            data = {"job_id": job_id, "created_at": now}
            data.update(kw)
            status = kw.get("status", "queued")
            c.execute(
                "INSERT INTO jobs (job_id, status, created_at, updated_at, data_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, status, now, now, json.dumps(data)),
            )
        else:
            # Update existing job
            data = json.loads(row["data_json"])
            data.update(kw)
            status = data.get("status", "queued")
            c.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, data_json = ? "
                "WHERE job_id = ?",
                (status, now, json.dumps(data), job_id),
            )


def _get_job(job_id: str) -> dict:
    """
    Return the full job dict.
    Raises HTTPException(404) just like the original — no call-site changes needed.
    """
    job_id = job_id.upper()
    with _conn() as c:
        row = c.execute(
            "SELECT data_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return json.loads(row["data_json"])


def _list_jobs(limit: int = 20) -> list[dict]:
    """Return the most recent jobs, newest first."""
    with _conn() as c:
        rows = c.execute(
            "SELECT data_json FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [json.loads(r["data_json"]) for r in rows]


def next_queued_job() -> dict | None:
    """
    Return the oldest queued job and atomically mark it as 'claimed'
    so the dispatcher doesn't double-pick it.
    Returns None if the queue is empty.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT job_id, data_json FROM jobs "
            "WHERE status = 'queued' "
            "ORDER BY created_at ASC LIMIT 1"
        ).fetchone()

        if row is None:
            return None

        # Atomically flip to 'claimed' before returning
        now  = datetime.utcnow().isoformat()
        data = json.loads(row["data_json"])
        data["status"] = "claimed"
        c.execute(
            "UPDATE jobs SET status = 'claimed', updated_at = ?, data_json = ? "
            "WHERE job_id = ? AND status = 'queued'",
            (now, json.dumps(data), row["job_id"]),
        )

    # If another process beat us to it, fetchone won't have updated — return None
    if data.get("status") != "claimed":
        return None

    return data


# ─────────────────────────────────────────────────────────────────────────────
# HOW TO WIRE THIS INTO api/app.py
# ─────────────────────────────────────────────────────────────────────────────
#
# Make these 3 edits to api/app.py — nothing else changes:
#
# ── EDIT 1: Replace the imports block near the top ───────────────────────────
#
#   REMOVE these 4 lines:
#       # In-memory job store
#       _jobs: dict[str, dict] = {}
#       def _set_job(job_id, **kw): ...   (the whole function)
#       def _get_job(job_id): ...         (the whole function)
#
#   ADD this import instead (one line):
#       from job_store import init_db, _set_job, _get_job, _list_jobs
#
# ── EDIT 2: Replace the app = FastAPI(...) line ──────────────────────────────
#
#   REPLACE:
#       app = FastAPI(title="FireAI Pro", version="3.0.0", ...)
#
#   WITH:
#       from contextlib import asynccontextmanager
#       import asyncio
#       from dispatcher import start_dispatcher, stop_dispatcher
#
#       @asynccontextmanager
#       async def lifespan(app):
#           init_db()
#           task = asyncio.create_task(start_dispatcher())
#           yield
#           stop_dispatcher()
#           task.cancel()
#
#       app = FastAPI(title="FireAI Pro", version="3.0.0",
#                     description="Enterprise fire sprinkler design system",
#                     lifespan=lifespan)
#
# ── EDIT 3: Update list_jobs endpoint ────────────────────────────────────────
#
#   REPLACE the list_jobs function body:
#       jobs = sorted(_jobs.values(), key=lambda j: j.get("created_at",""), reverse=True)
#       return {"jobs": jobs[:limit], "total": len(_jobs)}
#
#   WITH:
#       jobs = _list_jobs(limit=limit)
#       return {"jobs": jobs, "total": len(jobs)}
#
# That's it. Every other line in app.py stays identical.
# ─────────────────────────────────────────────────────────────────────────────
