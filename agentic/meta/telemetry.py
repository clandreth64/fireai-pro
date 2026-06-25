"""Stage 4 — meta-loop telemetry.

Best-effort recorders for repairs, escalations, and extraction gaps. These feed
the nightly improvement loop's analyst. Everything here is wrapped so a telemetry
failure can NEVER break a job — the app calls these on the hot path.

api/app.py imports:  from agentic.meta import telemetry as agent_telemetry
"""

from __future__ import annotations
import json
import os
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()


def _store_path() -> str:
    # Sit next to the job store if we can find it; fall back to /tmp.
    for base in ("/app", os.getcwd(), "/tmp"):
        try:
            if os.path.isdir(base) and os.access(base, os.W_OK):
                return os.path.join(base, "fireai_agent_telemetry.jsonl")
        except Exception:
            continue
    return "/tmp/fireai_agent_telemetry.jsonl"


def _append(event: dict) -> None:
    event["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        with _LOCK:
            with open(_store_path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
    except Exception:
        # Telemetry must never break a job.
        pass


def record_repair(name: str, project: str | None = None) -> None:
    _append({"type": "repair", "name": name, "project": project})


def record_escalation(reason: str, project: str | None = None) -> None:
    _append({"type": "escalation", "reason": reason, "project": project})


def record_extraction_gap(field: str, doc_type: str | None = None,
                          project: str | None = None) -> None:
    _append({"type": "extraction_gap", "field": field,
             "doc_type": doc_type, "project": project})
