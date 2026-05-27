"""
FireAI Pro — agent_config_store.py
====================================
SQLite-backed store for live agent prompt configs.

How it works:
  - Each agent has a "current" prompt record in agent_configs table
  - Every change is versioned in agent_config_history (full audit trail)
  - Agents call get_config(agent_id) at runtime — returns override if one exists,
    None if no override (agent falls back to its hardcoded prompt)
  - The improvement loop calls save_config() to hot-swap a prompt
  - Next job that runs picks up the new prompt automatically — no redeploy needed

Also owns the performance_log table, which the orchestrator writes to
after each job and the improvement loop reads from nightly.

Drop this file at the repo root.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
import os

log = logging.getLogger("fireai.config_store")

_DB_PATH = os.getenv("JOB_DB_PATH", str(Path(__file__).parent / "fireai_jobs.db"))


# ─────────────────────────────────────────────────────────────────────────────
# DB init
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_config_db() -> None:
    """Create tables. Safe to call multiple times."""
    with _conn() as c:
        # Current live config per agent
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_configs (
                agent_id        TEXT PRIMARY KEY,
                version         INTEGER NOT NULL DEFAULT 1,
                system_prompt   TEXT NOT NULL,
                schema_prompt   TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL,
                updated_by      TEXT NOT NULL DEFAULT 'improvement_loop',
                reason          TEXT NOT NULL DEFAULT ''
            )
        """)

        # Full history of every prompt version
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_config_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id        TEXT NOT NULL,
                version         INTEGER NOT NULL,
                system_prompt   TEXT NOT NULL,
                schema_prompt   TEXT NOT NULL DEFAULT '',
                saved_at        TEXT NOT NULL,
                saved_by        TEXT NOT NULL DEFAULT 'improvement_loop',
                reason          TEXT NOT NULL DEFAULT '',
                test_score      REAL,       -- violation delta vs previous
                deployed        INTEGER NOT NULL DEFAULT 1
            )
        """)

        # Per-job per-agent performance data
        c.execute("""
            CREATE TABLE IF NOT EXISTS performance_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT NOT NULL,
                agent_id        TEXT NOT NULL,
                success         INTEGER NOT NULL DEFAULT 1,
                violation_count INTEGER NOT NULL DEFAULT 0,
                parse_errors    INTEGER NOT NULL DEFAULT 0,
                was_frozen      INTEGER NOT NULL DEFAULT 0,
                iterations_used INTEGER NOT NULL DEFAULT 1,
                warnings_count  INTEGER NOT NULL DEFAULT 0,
                logged_at       TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_perf_agent ON performance_log(agent_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_perf_logged ON performance_log(logged_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_perf_job ON performance_log(job_id)")

    log.info("Agent config store initialised")


# ─────────────────────────────────────────────────────────────────────────────
# Config read/write
# ─────────────────────────────────────────────────────────────────────────────

def get_config(agent_id: str) -> Optional[dict]:
    """
    Return live config for an agent, or None if no override exists.
    Called by FireAIAgent.system_prompt() at runtime.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT system_prompt, schema_prompt, version FROM agent_configs WHERE agent_id = ?",
            (agent_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "system_prompt": row["system_prompt"],
        "schema_prompt": row["schema_prompt"],
        "version":       row["version"],
    }


def save_config(
    agent_id:      str,
    system_prompt: str,
    schema_prompt: str  = "",
    reason:        str  = "",
    saved_by:      str  = "improvement_loop",
    test_score:    float = 0.0,
) -> int:
    """
    Save a new prompt config for an agent.
    Increments version, writes to history, updates live config.
    Returns the new version number.
    """
    now = datetime.utcnow().isoformat()
    with _conn() as c:
        existing = c.execute(
            "SELECT version FROM agent_configs WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        new_version = (existing["version"] + 1) if existing else 1

        # Upsert live config
        c.execute("""
            INSERT INTO agent_configs (agent_id, version, system_prompt, schema_prompt, updated_at, updated_by, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                version       = excluded.version,
                system_prompt = excluded.system_prompt,
                schema_prompt = excluded.schema_prompt,
                updated_at    = excluded.updated_at,
                updated_by    = excluded.updated_by,
                reason        = excluded.reason
        """, (agent_id, new_version, system_prompt, schema_prompt, now, saved_by, reason))

        # Write history record
        c.execute("""
            INSERT INTO agent_config_history
                (agent_id, version, system_prompt, schema_prompt, saved_at, saved_by, reason, test_score, deployed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (agent_id, new_version, system_prompt, schema_prompt, now, saved_by, reason, test_score))

    log.info("Config saved: agent=%s version=%d reason=%s", agent_id, new_version, reason[:60])
    return new_version


def rollback_config(agent_id: str) -> bool:
    """Roll back to the previous version. Returns True if successful."""
    with _conn() as c:
        rows = c.execute("""
            SELECT version, system_prompt, schema_prompt FROM agent_config_history
            WHERE agent_id = ? AND deployed = 1
            ORDER BY version DESC LIMIT 2
        """, (agent_id,)).fetchall()

    if len(rows) < 2:
        log.warning("Rollback: no previous version for %s", agent_id)
        return False

    prev = rows[1]
    save_config(
        agent_id      = agent_id,
        system_prompt = prev["system_prompt"],
        schema_prompt = prev["schema_prompt"],
        reason        = f"rollback to v{prev['version']}",
        saved_by      = "rollback",
    )
    log.info("Rolled back %s to version %d", agent_id, prev["version"])
    return True


def get_config_history(agent_id: str, limit: int = 10) -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT version, saved_at, saved_by, reason, test_score
            FROM agent_config_history WHERE agent_id = ?
            ORDER BY version DESC LIMIT ?
        """, (agent_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Performance logging
# ─────────────────────────────────────────────────────────────────────────────

def log_job_performance(
    job_id:         str,
    agent_id:       str,
    success:        bool,
    violation_count: int  = 0,
    parse_errors:   int   = 0,
    was_frozen:     bool  = False,
    iterations_used: int  = 1,
    warnings_count: int   = 0,
) -> None:
    with _conn() as c:
        c.execute("""
            INSERT INTO performance_log
                (job_id, agent_id, success, violation_count, parse_errors,
                 was_frozen, iterations_used, warnings_count, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, agent_id,
            1 if success else 0,
            violation_count, parse_errors,
            1 if was_frozen else 0,
            iterations_used, warnings_count,
            datetime.utcnow().isoformat(),
        ))


def get_agent_performance_summary(agent_id: str, days: int = 7) -> dict:
    """
    Aggregate performance stats for one agent over the last N days.
    Used by the Analysis step of the improvement loop.
    """
    since = datetime.utcnow().replace(hour=0, minute=0, second=0).isoformat()
    # Simple approximation: last `days` worth of rows
    with _conn() as c:
        rows = c.execute("""
            SELECT success, violation_count, parse_errors, was_frozen,
                   iterations_used, warnings_count
            FROM performance_log
            WHERE agent_id = ?
            ORDER BY logged_at DESC LIMIT ?
        """, (agent_id, days * 20)).fetchall()  # ~20 jobs/day estimate

    if not rows:
        return {"agent_id": agent_id, "jobs": 0, "needs_improvement": False}

    total         = len(rows)
    failures      = sum(1 for r in rows if not r["success"])
    parse_errs    = sum(r["parse_errors"] for r in rows)
    freezes       = sum(1 for r in rows if r["was_frozen"])
    avg_violations= sum(r["violation_count"] for r in rows) / total
    avg_iters     = sum(r["iterations_used"] for r in rows) / total

    # Needs improvement if: >20% failure rate, or >10% freeze rate,
    # or avg violations > 2, or >5 parse errors total
    needs_improvement = (
        (failures / total) > 0.20
        or (freezes / total) > 0.10
        or avg_violations > 2.0
        or parse_errs > 5
    )

    return {
        "agent_id":         agent_id,
        "jobs_analyzed":    total,
        "failure_rate":     round(failures / total, 3),
        "freeze_rate":      round(freezes / total, 3),
        "avg_violations":   round(avg_violations, 2),
        "avg_iterations":   round(avg_iters, 2),
        "total_parse_errors": parse_errs,
        "needs_improvement": needs_improvement,
    }


def get_all_agent_summaries(days: int = 7) -> list[dict]:
    """Return performance summary for every agent."""
    agent_ids = ["cad", "hydraulics", "routing", "bracing", "ahj"]
    return [get_agent_performance_summary(aid, days) for aid in agent_ids]
