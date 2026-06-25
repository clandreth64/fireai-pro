"""Stage 4 — meta-loop telemetry (best-effort; never breaks a job)."""

from __future__ import annotations
import json, os, sys, threading, types
from datetime import datetime, timezone

_LOCK = threading.Lock()


def _store_path() -> str:
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
        pass


def record_repair(name: str, project: str | None = None) -> None:
    _append({"type": "repair", "name": name, "project": project})


def record_escalation(reason: str, project: str | None = None) -> None:
    _append({"type": "escalation", "reason": reason, "project": project})


def record_extraction_gap(field: str, doc_type: str | None = None, project: str | None = None) -> None:
    _append({"type": "extraction_gap", "field": field, "doc_type": doc_type, "project": project})


# Expose `telemetry` as a submodule so `from agentic.meta import telemetry` works.
telemetry = types.ModuleType("agentic.meta.telemetry")
telemetry.record_repair = record_repair
telemetry.record_escalation = record_escalation
telemetry.record_extraction_gap = record_extraction_gap
sys.modules["agentic.meta.telemetry"] = telemetry

__all__ = ["telemetry", "record_repair", "record_escalation", "record_extraction_gap"]
