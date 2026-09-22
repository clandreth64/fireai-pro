"""Drawing-job persistence and isolated per-job storage.

Layout (never web-served directly):
    <data_dir>/drawing_jobs.db
    <data_dir>/jobs/<job_id>/source/upload.<ext>
    <data_dir>/jobs/<job_id>/work/            (temporary, removed after run)
    <data_dir>/jobs/<job_id>/deliverables/    (only files registered in the job record are downloadable)

Job ids are 128-bit random hex (uuid4) and validated by regex before any
filesystem use.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireai.config import Settings

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS drawing_jobs ("
                      "job_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, owner TEXT, data_json TEXT NOT NULL)")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.settings.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    # ── paths ────────────────────────────────────────────────────────────────
    def job_dir(self, job_id: str) -> Path:
        if not JOB_ID_RE.match(job_id or ""):
            raise KeyError(job_id)
        d = (self.settings.jobs_dir / job_id).resolve()
        if d.parent != self.settings.jobs_dir.resolve():
            raise KeyError(job_id)
        return d

    def source_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "source"

    def xref_dir(self, job_id: str) -> Path:
        return self.source_dir(job_id) / "xrefs"

    def work_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "work"

    def deliverables_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "deliverables"

    # ── records ──────────────────────────────────────────────────────────────
    def create(self, filename: str, source_format: str, units_override: str | None,
               owner: str | None = None, parent_job_id: str | None = None) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id, "created_at": now(), "updated_at": now(),
            "processing_status": "queued",
            "engineering_review_status": "not_performed",
            "ready_for_design": False,
            "requires_human_review": None,
            "failure": None,
            "source": {"filename": filename, "format": source_format},
            "units_override": units_override,
            "parent_job_id": parent_job_id,
            "summary": None,
            "deliverables": {},
        }
        for d in (self.source_dir(job_id), self.deliverables_dir(job_id)):
            d.mkdir(parents=True, exist_ok=True)
        with _lock, self._conn() as c:
            c.execute("INSERT INTO drawing_jobs (job_id, created_at, owner, data_json) VALUES (?,?,?,?)",
                      (job_id, job["created_at"], owner, json.dumps(job)))
        return job

    def get(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.match(job_id or ""):
            raise KeyError(job_id)
        with self._conn() as c:
            row = c.execute("SELECT data_json FROM drawing_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return json.loads(row["data_json"])

    def update(self, job_id: str, **fields) -> dict[str, Any]:
        with _lock, self._conn() as c:
            row = c.execute("SELECT data_json FROM drawing_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = json.loads(row["data_json"])
            job.update(fields)
            job["updated_at"] = now()
            c.execute("UPDATE drawing_jobs SET data_json = ? WHERE job_id = ?", (json.dumps(job), job_id))
        return job

    def deliverable_path(self, job_id: str, deliverable_id: str) -> tuple[Path, dict]:
        """Resolve a registered deliverable. Raises KeyError for anything else."""
        job = self.get(job_id)
        entry = job.get("deliverables", {}).get(deliverable_id)
        if not entry:
            raise KeyError(deliverable_id)
        base = self.deliverables_dir(job_id).resolve()
        path = (base / entry["filename"]).resolve()
        if path.parent != base or not path.is_file():
            raise KeyError(deliverable_id)
        return path, entry
