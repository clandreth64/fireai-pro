"""Persistent PROJECT ENGINEERING MODEL (Milestone 2.1).

The authoritative home of engineering work. It REFERS to interpreted geometry by identity and
fingerprint (source sha256, verified-model content / verification fingerprints, space and region uids)
— it never copies or re-derives the building model — and it keeps every design revision.

    Project ─ Building ─ Level (plan references: source sha256 + verified region uid)
       └─ DesignArea / engineering scope (space references, levels, future systems/zones)
             ├─ InputRecord      versioned, write-once engineering inputs (ceiling, classification, system, …)
             └─ EngineeringDesign ─ DesignRevision (immutable: request + result + dependency snapshot)
                                        └─ LayoutSelection (a person / optimiser picks a valid layout)

Revisions are never overwritten; a new run is a new revision. ``currency`` compares a revision's
dependency snapshot with the CURRENT dependencies: a changed verified model (reprocessing, human
correction, re-verification) INVALIDATES it; changed rule sets, listing, inputs or engine make it
STALE. Routing / hydraulics / fabrication consume ``selection_placements`` — materialised from the
stored request (which contains the verified engineering package), never from CAD.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from fireai.engineering.design import EngineeringDesignResult, SprinklerPlacement
from fireai.engineering.inputs import DesignRequest

IDENTITY_ASSURANCE = "unauthenticated_name (NOT PRODUCTION SAFE)"
_UID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_lock = threading.Lock()


class ProjectError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(obj: Any) -> str:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class PlanRef(BaseModel):
    source_sha256: str
    region_uid: str


class SpaceRef(BaseModel):
    source_sha256: str
    space_uid: str                                   # semantic space (or physical region) uid in the verified package
    region_uid: Optional[str] = None                 # the verified plan region (view) it lies in
    label: Optional[str] = None                      # display only


class ProjectRecord(BaseModel):
    uid: str
    name: str
    created_by: str
    created_at: str
    identity_assurance: str = IDENTITY_ASSURANCE


class BuildingRecord(BaseModel):
    uid: str
    project_uid: str
    name: str
    created_by: str
    created_at: str


class LevelRecord(BaseModel):
    uid: str
    building_uid: str
    name: str
    elevation: dict[str, Any] = Field(default_factory=lambda: {"status": "unknown"})   # never defaulted to 0
    plan_refs: list[PlanRef] = Field(default_factory=list)
    created_by: str
    created_at: str


class DesignAreaRecord(BaseModel):
    """An engineering scope: where one set of criteria (classification, system, rules, listing) applies.
    Different areas of one project may have different criteria and even different editions."""
    uid: str
    project_uid: str
    name: str
    building_uid: Optional[str] = None
    level_uid: Optional[str] = None
    space_refs: list[SpaceRef] = Field(default_factory=list)
    created_by: str
    created_at: str


class InputRecord(BaseModel):
    uid: str
    design_area_uid: str
    kind: Literal["ceiling", "classification", "system", "tolerances", "search", "jurisdiction"]
    version: int
    payload: dict[str, Any]
    digest: str
    recorded_by: str
    recorded_at: str
    supersedes: Optional[str] = None


class DesignDependencies(BaseModel):
    contract_version: str
    source_sha256: str
    package_content_fingerprint: str
    package_verification_fingerprint: str
    space_uid: str
    rule_sets: list[dict[str, str]]
    listing: Optional[dict[str, str]] = None
    inputs: dict[str, Optional[str]]                 # kind -> digest
    engine_version: str
    mode: str


class DesignRevisionRecord(BaseModel):
    uid: str
    design_uid: str
    design_area_uid: str
    revision: int
    created_by: str
    created_at: str
    dependencies: DesignDependencies
    request_fingerprint: str
    result_fingerprint: str
    result_status: str
    basis: str
    engineering_use: str
    supersedes: Optional[str] = None
    note: str = ""
    identity_assurance: str = IDENTITY_ASSURANCE


class LayoutSelection(BaseModel):
    uid: str
    revision_uid: str
    u_indices: list[int]
    v_indices: list[int]
    selected_by: str
    selected_at: str
    reason: str
    identity_assurance: str = IDENTITY_ASSURANCE


def dependencies_of(req: DesignRequest) -> DesignDependencies:
    from fireai.engineering.design import PLACEMENT_ENGINE_VERSION
    p = req.package
    return DesignDependencies(
        contract_version=p.contract_version, source_sha256=p.source_sha256,
        package_content_fingerprint=p.content_fingerprint, package_verification_fingerprint=p.verification_fingerprint,
        space_uid=req.space_uid,
        rule_sets=sorted(({"rule_set_id": s.rule_set_id, "version": s.version, "digest": s.digest()} for s in req.rule_sets),
                         key=lambda d: (d["rule_set_id"], d["version"])),
        listing=({"listing_id": req.listing.listing_id, "version": req.listing.version, "digest": req.listing.digest()}
                 if req.listing else None),
        inputs={k: (_digest(getattr(req, a)) if getattr(req, a) is not None else None)
                for k, a in (("ceiling", "ceiling"), ("classification", "classification"), ("system", "system"),
                             ("tolerances", "tolerances"), ("search", "search"))} | {"jurisdiction": req.jurisdiction},
        engine_version=PLACEMENT_ENGINE_VERSION, mode=req.mode)


def currency(rev: DesignRevisionRecord, current: DesignDependencies) -> dict:
    """CURRENT / STALE / INVALIDATED for a stored revision against the current dependencies."""
    old = rev.dependencies
    invalid, stale = [], []
    if (old.source_sha256, old.package_content_fingerprint, old.package_verification_fingerprint, old.space_uid) != \
            (current.source_sha256, current.package_content_fingerprint, current.package_verification_fingerprint,
             current.space_uid):
        invalid.append("the verified building model changed (reprocessing, human correction or re-verification)")
    if old.contract_version != current.contract_version:
        invalid.append("engineering contract version changed")
    if old.rule_sets != current.rule_sets:
        stale.append("rule sets changed (version or content)")
    if old.listing != current.listing:
        stale.append("sprinkler listing changed (version or content)")
    for k in sorted(set(old.inputs) | set(current.inputs)):
        if old.inputs.get(k) != current.inputs.get(k):
            stale.append(f"engineering input '{k}' changed")
    if old.engine_version != current.engine_version:
        stale.append("placement engine version changed")
    status = "INVALIDATED" if invalid else "STALE" if stale else "CURRENT"
    return {"status": status, "reasons": invalid + stale}


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    # ── io (write-once) ───────────────────────────────────────────────────────
    def _p(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def _write_once(self, path: Path, data: BaseModel | dict) -> None:
        if path.exists():
            raise ProjectError(f"{path.name} already exists: engineering records are never overwritten")
        path.parent.mkdir(parents=True, exist_ok=True)
        body = data.model_dump(mode="json", by_alias=True) if hasattr(data, "model_dump") else data
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _read(self, path: Path, cls):
        if not path.is_file():
            raise ProjectError(f"not found: {path.relative_to(self.root)}")
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _who(name: str) -> str:
        if not (name or "").strip():
            raise ProjectError("a named person is required")
        return name.strip()

    @staticmethod
    def _check(uid: str) -> str:
        if not _UID.match(uid or ""):
            raise ProjectError(f"invalid uid {uid!r}")
        return uid

    # ── hierarchy ─────────────────────────────────────────────────────────────
    def create_project(self, name: str, by: str) -> ProjectRecord:
        rec = ProjectRecord(uid=str(uuid.uuid4()), name=name, created_by=self._who(by), created_at=_now())
        self._write_once(self._p(rec.uid, "project.json"), rec)
        return rec

    def project(self, uid: str) -> ProjectRecord:
        return self._read(self._p(self._check(uid), "project.json"), ProjectRecord)

    def add_building(self, project_uid: str, name: str, by: str) -> BuildingRecord:
        self.project(project_uid)
        rec = BuildingRecord(uid=str(uuid.uuid4()), project_uid=project_uid, name=name, created_by=self._who(by),
                             created_at=_now())
        self._write_once(self._p(project_uid, "buildings", f"{rec.uid}.json"), rec)
        return rec

    def add_level(self, project_uid: str, building_uid: str, name: str, by: str,
                  plan_refs: list[PlanRef] | None = None) -> LevelRecord:
        self._read(self._p(self._check(project_uid), "buildings", f"{self._check(building_uid)}.json"), BuildingRecord)
        rec = LevelRecord(uid=str(uuid.uuid4()), building_uid=building_uid, name=name, plan_refs=plan_refs or [],
                          created_by=self._who(by), created_at=_now())
        self._write_once(self._p(project_uid, "levels", f"{rec.uid}.json"), rec)
        return rec

    def add_design_area(self, project_uid: str, name: str, by: str, space_refs: list[SpaceRef],
                        building_uid: str | None = None, level_uid: str | None = None) -> DesignAreaRecord:
        self.project(project_uid)
        rec = DesignAreaRecord(uid=str(uuid.uuid4()), project_uid=project_uid, name=name, building_uid=building_uid,
                               level_uid=level_uid, space_refs=space_refs, created_by=self._who(by), created_at=_now())
        self._write_once(self._p(project_uid, "areas", rec.uid, "area.json"), rec)
        return rec

    def design_area(self, project_uid: str, area_uid: str) -> DesignAreaRecord:
        return self._read(self._p(self._check(project_uid), "areas", self._check(area_uid), "area.json"),
                          DesignAreaRecord)

    # ── inputs (versioned, write-once) ────────────────────────────────────────
    def record_input(self, project_uid: str, area_uid: str, kind: str, payload: BaseModel | dict, by: str) -> InputRecord:
        self.design_area(project_uid, area_uid)
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else dict(payload)
        with _lock:
            prev = self.inputs(project_uid, area_uid, kind)
            rec = InputRecord(uid=str(uuid.uuid4()), design_area_uid=area_uid, kind=kind, version=len(prev) + 1,
                              payload=body, digest=_digest(body), recorded_by=self._who(by), recorded_at=_now(),
                              supersedes=prev[-1].uid if prev else None)
            self._write_once(self._p(project_uid, "areas", area_uid, "inputs", f"{kind}-v{rec.version:04d}.json"), rec)
        return rec

    def inputs(self, project_uid: str, area_uid: str, kind: str) -> list[InputRecord]:
        d = self._p(project_uid, "areas", area_uid, "inputs")
        return [self._read(p, InputRecord) for p in sorted(d.glob(f"{kind}-v*.json"))] if d.is_dir() else []

    # ── designs and revisions ─────────────────────────────────────────────────
    def record_design(self, project_uid: str, area_uid: str, req: DesignRequest, result: EngineeringDesignResult,
                      by: str, design_uid: str | None = None, note: str = "") -> DesignRevisionRecord:
        self.design_area(project_uid, area_uid)
        if result.request_fingerprint != _request_fp(req):
            raise ProjectError("the result was not produced from this request")
        who = self._who(by)
        with _lock:
            design_uid = design_uid or str(uuid.uuid4())
            ddir = self._p(project_uid, "designs", self._check(design_uid))
            if not (ddir / "design.json").exists():
                self._write_once(ddir / "design.json", {"uid": design_uid, "design_area_uid": area_uid,
                                                        "space_uid": req.space_uid, "created_by": who,
                                                        "created_at": _now()})
            prev = self.revisions(project_uid, design_uid)
            rev = DesignRevisionRecord(
                uid=str(uuid.uuid4()), design_uid=design_uid, design_area_uid=area_uid, revision=len(prev) + 1,
                created_by=who, created_at=_now(), dependencies=dependencies_of(req),
                request_fingerprint=result.request_fingerprint, result_fingerprint=result.result_fingerprint,
                result_status=result.status, basis=result.basis, engineering_use=result.engineering_use,
                supersedes=prev[-1].uid if prev else None, note=note)
            rdir = ddir / f"rev-{rev.revision:04d}"
            self._write_once(rdir / "request.json", req)
            self._write_once(rdir / "result.json", result)
            self._write_once(rdir / "revision.json", rev)
        return rev

    def revisions(self, project_uid: str, design_uid: str) -> list[DesignRevisionRecord]:
        d = self._p(project_uid, "designs", design_uid)
        return [self._read(p / "revision.json", DesignRevisionRecord) for p in sorted(d.glob("rev-*"))
                if (p / "revision.json").is_file()] if d.is_dir() else []

    def load_request(self, project_uid: str, rev: DesignRevisionRecord) -> DesignRequest:
        return self._read(self._p(project_uid, "designs", rev.design_uid, f"rev-{rev.revision:04d}", "request.json"),
                          DesignRequest)

    def load_result(self, project_uid: str, rev: DesignRevisionRecord) -> EngineeringDesignResult:
        res = self._read(self._p(project_uid, "designs", rev.design_uid, f"rev-{rev.revision:04d}", "result.json"),
                         EngineeringDesignResult)
        if res.result_fingerprint != rev.result_fingerprint:
            raise ProjectError("stored result does not match its revision record (tampered or corrupted)")
        return res

    # ── layout selection (the design a downstream system consumes) ────────────
    def select_layout(self, project_uid: str, rev: DesignRevisionRecord, u_indices: list[int], v_indices: list[int],
                      by: str, reason: str) -> LayoutSelection:
        res = self.load_result(project_uid, rev)
        vs = res.valid_set
        ok = vs is not None and any(
            len(u_indices) == f.n_u and len(v_indices) == f.n_v
            and (u_indices[0], v_indices[0]) in [tuple(o) for o in f.offsets]
            and all(u_indices[k] == u_indices[0] + k * f.ds_u for k in range(f.n_u))
            and all(v_indices[k] == v_indices[0] + k * f.ds_v for k in range(f.n_v)) for f in vs.families)
        if not ok:
            raise ProjectError("only a layout from this revision's VALID set can be selected")
        if not reason.strip():
            raise ProjectError("a reason is required for a layout selection")
        sel = LayoutSelection(uid=str(uuid.uuid4()), revision_uid=rev.uid, u_indices=u_indices, v_indices=v_indices,
                              selected_by=self._who(by), selected_at=_now(), reason=reason)
        self._write_once(self._p(project_uid, "designs", rev.design_uid, f"rev-{rev.revision:04d}", "selections",
                                 f"{sel.uid}.json"), sel)
        return sel

    def selection_placements(self, project_uid: str, rev: DesignRevisionRecord,
                             sel: LayoutSelection) -> list[SprinklerPlacement]:
        """The selected layout's sprinkler objects, re-materialised deterministically from the STORED request
        (verified package + inputs + rules): no CAD, no re-interpretation."""
        from fireai.engineering.placement import _State, _validate, evaluate_layout
        req = self.load_request(project_uid, rev)
        issues, space, region, res = _validate(req)
        if issues:
            raise ProjectError("the stored request no longer validates: " + "; ".join(i.code for i in issues))
        ev = evaluate_layout(_State(req, space, region, res), tuple(sel.u_indices), tuple(sel.v_indices), full=True)
        if not ev.valid:
            raise ProjectError("the selected layout does not re-evaluate as valid (engine or data changed)")
        return ev.placements


def _request_fp(req: DesignRequest) -> str:
    from fireai.engineering.placement import request_fingerprint
    return request_fingerprint(req)
