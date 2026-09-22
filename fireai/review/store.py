"""Local review store, keyed by the SOURCE drawing's sha256.

Layout: <root>/<sha[:2]>/<sha>/corrections.json, verification.json

* Corrections persist across reprocessing of the same source file: every new
  run of that file applies them (see ``apply_*``). Each correction records the
  context it was made in (XREF shas, engine version). A correction whose
  context no longer matches is NOT applied and is reported.
* A verification is bound to the model's verification fingerprint (source,
  XREFs, units, engine version, applied corrections). Any change -> INVALIDATED.
* Only a person writes here (API / review tool). The pipeline only reads.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
CORRECTION_KINDS = {"view_type", "room_boundary", "element_reject", "element_confirm"}
CHECK_STATUSES = {"CONFIRMED", "CORRECTED", "NOT_EVALUATED"}
# Categories a person must confirm or correct before a model can be HUMAN_VERIFIED.
REQUIRED_CATEGORIES = ("units", "drawing_type", "view_regions", "extents", "walls", "rooms")
OPTIONAL_CATEGORIES = ("doors", "windows", "columns", "stairs", "grids", "sprinkler_components", "title_block",
                       "other")
_lock = threading.Lock()


class ReviewError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, source_sha: str) -> Path:
        if not SHA_RE.match(source_sha or ""):
            raise ReviewError("invalid source sha256")
        return self.root / source_sha[:2] / source_sha

    def _read(self, source_sha: str, name: str, default):
        p = self._dir(source_sha) / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default

    def _write(self, source_sha: str, name: str, data) -> None:
        d = self._dir(source_sha)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(d / name)

    # ── revisions: files sharing a DXF document GUID ($FINGERPRINTGUID) ────────
    def _doc_index_path(self, guid: str) -> Path:
        return self.root / "_by_document" / (hashlib.sha256(guid.encode()).hexdigest() + ".json")

    def _index(self, model) -> None:
        guid = model.source.document_guid
        if not guid:
            return
        p = self._doc_index_path(guid)
        shas = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
        if model.source.sha256 not in shas:
            shas.append(model.source.sha256)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(shas), encoding="utf-8")

    def other_revisions(self, model) -> list[str]:
        """Other source files with the same document GUID that have review data.
        (The GUID survives Save/Save-As, so these are likely earlier/later revisions.)"""
        guid = model.source.document_guid
        if not guid:
            return []
        p = self._doc_index_path(guid)
        shas = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
        return [x for x in shas if x != model.source.sha256]

    # ── corrections ──────────────────────────────────────────────────────────
    def corrections(self, source_sha: str) -> list[dict]:
        return self._read(source_sha, "corrections.json", [])

    def add_correction(self, model, kind: str, data: dict[str, Any], reviewer: str, note: str | None = None) -> dict:
        from fireai.interpret.regions import VIEW_TYPES
        if kind not in CORRECTION_KINDS:
            raise ReviewError(f"unknown correction kind {kind!r}; expected one of {sorted(CORRECTION_KINDS)}")
        if not (reviewer or "").strip():
            raise ReviewError("reviewer is required")
        if kind == "view_type":
            if data.get("view_type") not in VIEW_TYPES:
                raise ReviewError(f"view_type must be one of {VIEW_TYPES}")
            if not any(r.get("uid") == data.get("region_uid") for r in model.view_regions):
                raise ReviewError("region_uid not found in this model")
        elif kind == "room_boundary":
            pts = data.get("polygon_src")
            if not isinstance(pts, list) or len(pts) < 3 or not all(
                    isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p)
                    for p in pts):
                raise ReviewError("polygon_src must be a list of >= 3 [x, y] points in the SRC frame "
                                  "(drawing units, original coordinates)")
            rep = data.get("replaces_element_uid")
            if rep and not any(e.uid == rep for e in model.elements_of("room")):
                raise ReviewError("replaces_element_uid is not a room in this model")
        else:
            if not any(e.uid == data.get("element_uid") for e in model.elements):
                raise ReviewError("element_uid not found in this model")
        v = model.verification
        rec = {"id": "C" + uuid.uuid4().hex[:12], "kind": kind, "data": data, "reviewer": reviewer.strip(),
               "note": note, "created_at": _now(),
               "context": {"source_sha256": model.source.sha256,
                           "xref_sha256": v.xref_sha256 if v else [],
                           "engine_version": v.engine_version if v else None,
                           "created_on_model": model.model_id}}
        with _lock:
            items = self.corrections(model.source.sha256)
            items.append(rec)
            self._write(model.source.sha256, "corrections.json", items)
            self._index(model)
        return rec

    # ── verification ─────────────────────────────────────────────────────────
    def verification(self, source_sha: str) -> dict | None:
        return self._read(source_sha, "verification.json", None)

    def record_verification(self, model, reviewer: str, decision: str, checklist: dict[str, dict],
                            acknowledged_triggers: list[str], selected_region_uids: list[str],
                            note: str | None = None) -> dict:
        """decision: "verify" (-> HUMAN_VERIFIED) or "reject" (-> REVIEW_REQUIRED)."""
        if not (reviewer or "").strip():
            raise ReviewError("reviewer is required")
        if model.verification is None:
            raise ReviewError("model has no verification binding (produced before schema 0.3.0); reprocess it")
        if decision not in ("verify", "reject"):
            raise ReviewError("decision must be 'verify' or 'reject'")
        for cat, item in checklist.items():
            if cat not in REQUIRED_CATEGORIES + OPTIONAL_CATEGORIES:
                raise ReviewError(f"unknown checklist category {cat!r}")
            if item.get("status") not in CHECK_STATUSES:
                raise ReviewError(f"{cat}: status must be one of {sorted(CHECK_STATUSES)}")
            if item["status"] == "CORRECTED" and not (item.get("note") or item.get("value") is not None):
                raise ReviewError(f"{cat}: a CORRECTED item needs the corrected value or a note")
        if decision == "verify":
            seen = {a["correction_id"] for a in model.human_corrections_applied}
            pending = [c["id"] for c in self.corrections(model.source.sha256) if c["id"] not in seen]
            if pending:
                raise ReviewError(f"cannot verify: corrections {pending} are stored but not in this model; "
                                  "reprocess the drawing and verify the new result")
            missing = [c for c in REQUIRED_CATEGORIES
                       if checklist.get(c, {}).get("status") not in ("CONFIRMED", "CORRECTED")]
            if missing:
                raise ReviewError(f"cannot verify: required categories not confirmed/corrected: {missing}")
            codes = sorted({t.code for t in model.diagnostics.review_triggers})
            unack = [c for c in codes if c not in set(acknowledged_triggers)]
            if unack:
                raise ReviewError(f"cannot verify: review triggers not acknowledged: {unack}")
            uids = {r.get("uid") for r in model.view_regions}
            if not selected_region_uids or any(u not in uids for u in selected_region_uids):
                raise ReviewError("cannot verify: select the drawing region(s) in scope (region uids from this model)")
        rec = {"status": "HUMAN_VERIFIED" if decision == "verify" else "REJECTED",
               "fingerprint": model.verification.fingerprint, "model_id": model.model_id,
               "reviewer": reviewer.strip(), "at": _now(), "checklist": checklist,
               "acknowledged_triggers": sorted(set(acknowledged_triggers)),
               "selected_region_uids": list(selected_region_uids), "note": note,
               "binding": model.verification.model_dump()}
        with _lock:
            self._write(model.source.sha256, "verification.json", rec)
            self._index(model)
        return rec


def verification_state(model, store: ReviewStore | None) -> dict:
    """UNREVIEWED | REVIEW_REQUIRED | HUMAN_VERIFIED | INVALIDATED, with reasons."""
    v = model.verification
    if v is None:
        return {"status": "INVALIDATED", "reasons": ["model has no verification binding (pre-0.3.0); reprocess"]}
    rec = store.verification(model.source.sha256) if store else None
    if rec is None:
        prior = [x for x in (store.other_revisions(model) if store else []) if store.verification(x)]
        if prior:
            return {"status": "INVALIDATED",
                    "reasons": ["source drawing changed: a verification exists for another revision of this "
                                f"drawing (same document GUID, {len(prior)} other file(s)); this revision is unverified"]}
        return {"status": "REVIEW_REQUIRED" if model.requires_human_review else "UNREVIEWED", "reasons": []}
    if rec["fingerprint"] != v.fingerprint:
        old = rec.get("binding") or {}
        reasons = []
        if old.get("source_sha256") != v.source_sha256:
            reasons.append("source drawing changed")
        if old.get("xref_sha256") != v.xref_sha256:
            reasons.append("XREF set or content changed")
        if old.get("resolved_units") != v.resolved_units:
            reasons.append("unit resolution changed")
        if old.get("engine_version") != v.engine_version:
            reasons.append(f"interpretation engine changed ({old.get('engine_version')} -> {v.engine_version})")
        if old.get("corrections_digest") != v.corrections_digest:
            reasons.append("human corrections changed since verification")
        return {"status": "INVALIDATED", "reasons": reasons or ["verification fingerprint differs"],
                "verified_at": rec.get("at"), "reviewer": rec.get("reviewer")}
    if rec["status"] == "HUMAN_VERIFIED":
        return {"status": "HUMAN_VERIFIED", "reasons": [], "verified_at": rec["at"], "reviewer": rec["reviewer"],
                "selected_region_uids": rec["selected_region_uids"]}
    return {"status": "REVIEW_REQUIRED", "reasons": [f"rejected by {rec['reviewer']}: {rec.get('note') or ''}"]}
