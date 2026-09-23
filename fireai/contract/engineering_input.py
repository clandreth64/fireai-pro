"""Engineering input contract (draft 1): the ONLY view of a building model that engineering may use.

Format-independent: it is built from the normalized, HUMAN_VERIFIED ``BuildingModel`` and carries
no CAD internals (no DXF entities, handles, layers or ezdxf objects) — only normalized geometry in
the LOCAL frame (feet), stable uids for traceability, and explicit states.

``build_engineering_input`` first calls ``require_verified_model`` (unchanged, never weakened) and
then applies the contract's additional hard blockers. Anything the drawing cannot establish (e.g.
ceiling height, hazard classification) is listed as NOT PROVIDED — never defaulted.

This module performs no engineering and produces no design results.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from fireai.model import BuildingModel
from fireai.review.gate import ENGINEERING_VIEW_TYPES, ModelNotVerified, require_verified_model

CONTRACT_VERSION = "engineering_input/1-draft"

# Facts engineering will need that drawing understanding does NOT establish. They must arrive as
# explicit, separately recorded design inputs (see docs/MILESTONE_2_0_SPEC.md) — never inferred here.
NOT_PROVIDED_BY_DRAWING_UNDERSTANDING = (
    "ceiling_height_and_ceiling_geometry", "elevations_and_levels", "hazard_or_occupancy_classification",
    "sprinkler_type_and_listing", "applicable_ruleset_and_edition", "obstructions_above_or_below_the_plan",
    "construction_type", "water_supply",
)


class ContractViolation(Exception):
    def __init__(self, blockers: list[str]):
        super().__init__("; ".join(blockers))
        self.blockers = blockers


class ContractRegion(BaseModel):
    uid: str
    id: str
    view_type: str
    view_type_source: str
    bbox_local_ft: list[float]


class ContractSpace(BaseModel):
    uid: str
    id: str
    label: Optional[str] = None
    origin: Literal["human", "deterministic_inference"]
    polygon_local_ft: list[tuple[float, float]]
    area_sf: float
    region_uid: str
    review_status: str
    derived_from: list[str] = Field(default_factory=list)       # uids (traceability, not CAD access)


class ContractWall(BaseModel):
    uid: Optional[str] = None
    kind: str                                                   # straight | curved (derived analysis)
    thickness_ft: float
    centerline_local_ft: Optional[list[list[float]]] = None
    arc_local_ft: Optional[dict[str, Any]] = None
    derived: Literal[True] = True
    face_element_uids: list[str] = Field(default_factory=list)
    region_uid: str


class ContractLinework(BaseModel):
    uid: str
    category: str                                               # wall | column
    subtype: Optional[str] = None
    points_local_ft: list[tuple[float, float]]
    closed: bool = False
    region_uid: str
    derived_from: list[str] = Field(default_factory=list)


class EngineeringInput(BaseModel):
    contract_version: Literal["engineering_input/1-draft"] = CONTRACT_VERSION
    model_id: str
    schema_version: str
    source_sha256: str
    document_guid: Optional[str] = None
    xref_sha256: list[str] = Field(default_factory=list)
    engine_version: str
    verification_fingerprint: str
    verified_by: str                                            # unauthenticated name (see HUMAN_REVIEW.md)
    verified_at: str
    frame: Literal["LOCAL"] = "LOCAL"
    units: Literal["ft"] = "ft"
    source_units: str
    source_to_local: list[list[float]]                          # 4x4, SRC (drawing units) -> LOCAL (ft)
    spatial_context: dict[str, Any]                             # building/level; "unassigned" when unknown
    regions: list[ContractRegion]
    spaces: list[ContractSpace]
    walls_analysis: list[ContractWall]
    walls_linework: list[ContractLinework]
    columns: list[ContractLinework]
    xrefs: list[dict[str, Any]]
    conversion_significance: Optional[str] = None
    human_corrections_applied: list[str] = Field(default_factory=list)
    not_provided: list[str] = Field(default_factory=lambda: list(NOT_PROVIDED_BY_DRAWING_UNDERSTANDING))
    z_status: Literal["unknown"] = "unknown"


def _source_to_local(model: BuildingModel) -> list[list[float]] | None:
    frames = {f.id: f for f in model.coordinate_frames}
    s = model.transform.scale
    if not s or not math.isfinite(s) or s <= 0:
        return None
    local = frames.get("LOCAL")
    if local is None or local.status != "defined" or local.matrix is None:
        return None
    if not all(math.isfinite(v) for row in local.matrix for v in row):
        return None
    ox, oy = model.transform.origin
    if not (math.isfinite(ox) and math.isfinite(oy)):
        return None
    return [[s, 0.0, 0.0, -ox * s], [0.0, s, 0.0, -oy * s], [0.0, 0.0, s, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _in_bbox(pts, bb) -> bool:
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return bb[0] <= cx <= bb[2] and bb[1] <= cy <= bb[3]


def _selected(model, readiness) -> list[dict]:
    uids = readiness["verification"].get("selected_region_uids") or []
    return [r for r in model.view_regions if r.get("uid") in uids]


def _region_of(el, regions) -> dict | None:
    rid = el.properties.get("view_region")
    for r in regions:
        if rid is not None and r["id"] == rid:
            return r
    if el.geometry and el.geometry.points and rid is None:          # human rooms carry no region id
        for r in regions:
            if _in_bbox(el.geometry.points, r["bbox_ft"]):
                return r
    return None


def engineering_input_blockers(model: BuildingModel, store) -> list[str]:
    """All reasons engineering may not start. Starts with the unchanged verification gate."""
    try:
        readiness = require_verified_model(model, store)
    except ModelNotVerified as exc:
        return list(exc.readiness["blockers"])
    blockers: list[str] = []
    if _source_to_local(model) is None:
        blockers.append("coordinate transform SRC -> LOCAL is missing or invalid")
    regions = _selected(model, readiness)
    if not regions:
        blockers.append("no selected plan region")
    for r in regions:
        if r.get("view_type") not in ENGINEERING_VIEW_TYPES:
            blockers.append(f"selected region {r['id']} is not a plan view")
    rooms = [e for e in model.elements_of("room") if e.provenance.review.status != "rejected"]
    for r in regions:
        in_r = [e for e in rooms if (_region_of(e, [r]) is not None)]
        if not in_r:
            blockers.append(f"selected region {r['id']}: no room/space boundary (required room boundary unresolved)")
        merged = [e.id for e in in_r if e.subtype == "suspected_merged_region"]
        if merged:
            blockers.append(f"selected region {r['id']}: merged-room boundaries {merged} unresolved "
                            "(reject or replace them with human room boundaries)")
    codes = {t.code for t in model.diagnostics.review_triggers}
    if "HUMAN_CORRECTIONS_NOT_APPLIED" in codes or "HUMAN_CORRECTIONS_FROM_OTHER_REVISION" in codes:
        blockers.append("stored human corrections were not applied to this model")
    return blockers


def build_engineering_input(model: BuildingModel, store) -> EngineeringInput:
    blockers = engineering_input_blockers(model, store)
    if blockers:
        raise ContractViolation(blockers)
    readiness = require_verified_model(model, store)
    ver = readiness["verification"]
    regions = _selected(model, readiness)
    reg_uid = {r["id"]: r["uid"] for r in regions}
    spaces, walls_lw, cols = [], [], []
    for e in model.elements:
        r = _region_of(e, regions)
        if r is None or e.geometry is None:
            continue
        if e.category == "room" and e.provenance.review.status != "rejected":
            spaces.append(ContractSpace(uid=e.uid, id=e.id, label=e.label, origin=e.provenance.origin,
                                        polygon_local_ft=[tuple(p) for p in e.geometry.points],
                                        area_sf=e.properties.get("area_sf"), region_uid=r["uid"],
                                        review_status=e.provenance.review.status,
                                        derived_from=list(e.provenance.derived_from)))
        elif e.category in ("wall", "column") and e.provenance.review.status != "rejected" and e.geometry.points:
            item = ContractLinework(uid=e.uid, category=e.category, subtype=e.subtype,
                                    points_local_ft=[tuple(p) for p in e.geometry.points], closed=e.geometry.closed,
                                    region_uid=r["uid"], derived_from=list(e.provenance.derived_from))
            (walls_lw if e.category == "wall" else cols).append(item)
    walls = []
    for w in (model.wall_model or {}).get("walls", []):
        if w.get("region") not in reg_uid:
            continue
        faces = sorted({el.uid for el in model.elements for f in w["faces"] if el.id == f["element_id"]})
        walls.append(ContractWall(uid=w.get("uid"), kind=w["kind"], thickness_ft=w["thickness_ft"],
                                  centerline_local_ft=w.get("centerline"), arc_local_ft=w.get("arc"),
                                  face_element_uids=faces, region_uid=reg_uid[w["region"]]))
    v = model.verification
    return EngineeringInput(
        model_id=model.model_id, schema_version=model.schema_version, source_sha256=model.source.sha256,
        document_guid=model.source.document_guid, xref_sha256=v.xref_sha256, engine_version=v.engine_version,
        verification_fingerprint=v.fingerprint, verified_by=ver.get("reviewer") or "", verified_at=ver.get("verified_at") or "",
        source_units=model.units.resolved_units, source_to_local=_source_to_local(model),
        spatial_context={"status": model.spatial_structure.status, "buildings": model.spatial_structure.buildings,
                         "levels": model.spatial_structure.levels},
        regions=[ContractRegion(uid=r["uid"], id=r["id"], view_type=r["view_type"],
                                view_type_source=r.get("view_type_source", "fireai"), bbox_local_ft=r["bbox_ft"])
                 for r in regions],
        spaces=spaces, walls_analysis=walls, walls_linework=walls_lw, columns=cols,
        xrefs=[{"name": x.name, "status": x.status, "sha256": x.sha256} for x in model.xrefs],
        conversion_significance=(model.source.conversion_audit or {}).get("significance"),
        human_corrections_applied=[a["correction_id"] for a in model.human_corrections_applied
                                   if a["status"] == "applied"],
    )
