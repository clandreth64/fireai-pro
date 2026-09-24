"""Engineering input contract (version 3): the ONLY view of a building model that engineering may use.

Format-independent: it is built from the normalized, HUMAN_VERIFIED ``BuildingModel`` and carries
no CAD internals (no DXF entities, handles, layers, block names or ezdxf objects) — only normalized
geometry in the LOCAL frame (feet), stable uids for traceability, and explicit states.

``build_engineering_input`` first calls ``require_verified_model`` (unchanged, never weakened) and
then applies the contract's additional hard blockers. Anything the drawing cannot establish (e.g.
ceiling height, hazard classification) is listed as NOT PROVIDED — never defaulted.

Version 3 (M1.9) adds, per physical region, an ORDERED, CLASSIFIED boundary (wall / window /
door_opening / open_opening / unknown segments) and the plan openings crossing those boundaries,
so engineering can tell bounding walls from doorways without re-reading CAD. Packages of other
versions are never reinterpreted as version 3 (see ``parse_engineering_input``).

This module performs no engineering and produces no design results.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from fireai.model import BuildingModel
from fireai.review.gate import ENGINEERING_VIEW_TYPES, ModelNotVerified, require_verified_model

CONTRACT_VERSION = "engineering_input/3"          # 3: classified boundaries + openings (M1.9)
LEGACY_CONTRACT_VERSIONS = ("engineering_input/2-draft",)

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


class ContractVersionError(ValueError):
    """A package is not of the contract version the caller requires. Never silently upgraded."""


class ContractRegion(BaseModel):
    uid: str
    id: str
    view_type: str
    view_type_source: str
    bbox_local_ft: list[float]


class ContractVerticalExtent(BaseModel):
    """Vertical extent of a boundary face / opening. 2D plans do not establish it: ``unknown``."""
    status: Literal["unknown", "from_source", "user"] = "unknown"
    datum: Optional[str] = None
    bottom_ft: Optional[float] = None
    top_ft: Optional[float] = None


class ContractBoundarySegment(BaseModel):
    """One straight piece of a region boundary. ``encloses``: True = physically closes the region
    (wall, glazed wall); False = passage (door opening, doorless opening); None = unknown."""
    uid: Optional[str] = None
    index: int
    kind: Literal["wall", "window", "door_opening", "open_opening", "unknown"]
    encloses: Optional[bool] = None
    start_local_ft: tuple[float, float]
    end_local_ft: tuple[float, float]
    length_ft: float
    opening_uid: Optional[str] = None                           # -> EngineeringInput.openings[].uid
    fill_element_uid: Optional[str] = None                      # the door/window object (model uid)
    derived_from: list[str] = Field(default_factory=list)       # uids (traceability, not CAD access)
    confidence: float
    requires_verification: bool
    rules: list[str] = Field(default_factory=list)
    vertical_extent: ContractVerticalExtent = Field(default_factory=ContractVerticalExtent)


class ContractBoundaryRing(BaseModel):
    role: Literal["outer", "inner"]
    orientation: Literal["ccw", "cw"]                           # outer: counter-clockwise, region on the LEFT
    segments: list[ContractBoundarySegment]


class ContractBoundary(BaseModel):
    """Plan projection of a region's boundary on a plane of UNKNOWN elevation. A future 3D
    representation (faces with vertical extents, floor/ceiling planes, volume) will be added next
    to it; this projection stays valid."""
    representation: Literal["plan_projection"] = "plan_projection"
    frame: Literal["LOCAL"] = "LOCAL"
    units: Literal["ft"] = "ft"
    plane_z_status: Literal["unknown", "from_source", "user"] = "unknown"
    rings: list[ContractBoundaryRing]
    complete: bool                                               # False = some segment is `unknown`
    length_by_kind_ft: dict[str, float] = Field(default_factory=dict)


class ContractSpace(BaseModel):
    """A PHYSICAL region (enclosure)."""
    uid: str
    id: str
    label: Optional[str] = None
    origin: Literal["human", "deterministic_inference"]
    polygon_local_ft: list[tuple[float, float]]
    area_sf: float
    region_uid: str
    review_status: str
    derived_from: list[str] = Field(default_factory=list)       # uids (traceability, not CAD access)
    boundary: ContractBoundary


class ContractSemanticSpace(BaseModel):
    """A named/use-defined space. Only spaces with a KNOWN boundary can pass the contract."""
    uid: str
    id: str
    label: Optional[str] = None
    boundary_state: Literal["known"]
    region_uid: str                                             # the physical region (ContractSpace) it occupies
    derived_from: list[str] = Field(default_factory=list)


class ContractOpening(BaseModel):
    """A plan opening (the void) crossed by region boundaries: ONE object per physical opening."""
    uid: str
    id: str
    kind: Literal["door", "open", "window", "unknown"]
    passable: Optional[bool] = None                             # door/open: True; window: False; unknown: None
    footprint_local_ft: list[tuple[float, float]]               # plan footprint of the void (or its span line)
    width_ft: float
    depth_ft: Optional[float] = None                            # wall depth when both faces are region boundaries
    fill_element_uid: Optional[str] = None
    fill_category: Optional[Literal["door", "window"]] = None
    space_uids: list[str] = Field(default_factory=list)         # ContractSpace uids whose boundary it crosses
    boundary_segment_uids: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    confidence: float
    requires_verification: bool
    review_status: str
    vertical_extent: ContractVerticalExtent = Field(default_factory=ContractVerticalExtent)


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
    contract_version: Literal["engineering_input/3"] = CONTRACT_VERSION
    model_id: str
    schema_version: str
    source_sha256: str
    document_guid: Optional[str] = None
    xref_sha256: list[str] = Field(default_factory=list)
    engine_version: str
    content_fingerprint: str                                    # interpretation content (fireai/review/content.py)
    verification_fingerprint: str
    verified_by: str                                            # unauthenticated name (see HUMAN_REVIEW.md)
    verified_at: str
    frame: Literal["LOCAL"] = "LOCAL"
    units: Literal["ft"] = "ft"
    source_units: str
    source_to_local: list[list[float]]                          # 4x4, SRC (drawing units) -> LOCAL (ft)
    spatial_context: dict[str, Any]                             # building/level; "unassigned" when unknown
    regions: list[ContractRegion]
    spaces: list[ContractSpace]                                 # PHYSICAL regions (enclosures) + classified boundary
    semantic_spaces: list[ContractSemanticSpace] = Field(default_factory=list)
    openings: list[ContractOpening] = Field(default_factory=list)
    walls_analysis: list[ContractWall]
    walls_linework: list[ContractLinework]
    columns: list[ContractLinework]
    xrefs: list[dict[str, Any]]
    conversion_significance: Optional[str] = None
    human_corrections_applied: list[str] = Field(default_factory=list)
    not_provided: list[str] = Field(default_factory=lambda: list(NOT_PROVIDED_BY_DRAWING_UNDERSTANDING))
    z_status: Literal["unknown"] = "unknown"


# ── legacy (read-only) ────────────────────────────────────────────────────────

class ContractSpaceV2Draft(BaseModel):
    uid: str
    id: str
    label: Optional[str] = None
    origin: Literal["human", "deterministic_inference"]
    polygon_local_ft: list[tuple[float, float]]
    area_sf: float
    region_uid: str
    review_status: str
    derived_from: list[str] = Field(default_factory=list)


class EngineeringInputV2Draft(BaseModel):
    """A package produced by contract ``engineering_input/2-draft`` (M1.8). Readable for audit and
    reproducibility only: it has no classified boundaries or openings, cannot be converted to
    version 3, and must not be given to engineering. Regenerate from the verified model instead."""
    contract_version: Literal["engineering_input/2-draft"]
    model_id: str
    schema_version: str
    source_sha256: str
    document_guid: Optional[str] = None
    xref_sha256: list[str] = Field(default_factory=list)
    engine_version: str
    verification_fingerprint: str
    verified_by: str
    verified_at: str
    frame: Literal["LOCAL"] = "LOCAL"
    units: Literal["ft"] = "ft"
    source_units: str
    source_to_local: list[list[float]]
    spatial_context: dict[str, Any]
    regions: list[ContractRegion]
    spaces: list[ContractSpaceV2Draft]
    semantic_spaces: list[ContractSemanticSpace] = Field(default_factory=list)
    walls_analysis: list[ContractWall]
    walls_linework: list[ContractLinework]
    columns: list[ContractLinework]
    xrefs: list[dict[str, Any]]
    conversion_significance: Optional[str] = None
    human_corrections_applied: list[str] = Field(default_factory=list)
    not_provided: list[str] = Field(default_factory=list)
    z_status: Literal["unknown"] = "unknown"


def parse_engineering_input(data: dict[str, Any]) -> EngineeringInput:
    """Load a persisted package for ENGINEERING USE. Only the current version is accepted; an older
    package is refused (never reinterpreted or upgraded) because it lacks information version 3
    guarantees. Regenerate it with ``build_engineering_input`` from the verified model."""
    v = data.get("contract_version")
    if v != CONTRACT_VERSION:
        hint = (" Read it for audit with read_legacy_engineering_input(); it cannot be converted."
                if v in LEGACY_CONTRACT_VERSIONS else "")
        raise ContractVersionError(f"engineering input package is {v!r}, engineering requires {CONTRACT_VERSION!r}; "
                                   f"regenerate it from the verified model.{hint}")
    return EngineeringInput.model_validate(data)


def read_legacy_engineering_input(data: dict[str, Any]) -> EngineeringInputV2Draft:
    """Read an ``engineering_input/2-draft`` package for audit/reproducibility (never for engineering)."""
    if data.get("contract_version") not in LEGACY_CONTRACT_VERSIONS:
        raise ContractVersionError(f"not a legacy engineering input package: {data.get('contract_version')!r}")
    return EngineeringInputV2Draft.model_validate(data)


# ── building ──────────────────────────────────────────────────────────────────

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


def _live_rooms(model) -> list:
    return [e for e in model.elements_of("room") if e.provenance.review.status != "rejected"]


def engineering_input_blockers(model: BuildingModel, store) -> list[str]:
    """All reasons engineering may not start. Starts with the unchanged verification gate."""
    try:
        readiness = require_verified_model(model, store)
    except ModelNotVerified as exc:
        return list(exc.readiness["blockers"])
    blockers: list[str] = []
    if _source_to_local(model) is None:
        blockers.append("coordinate transform SRC -> LOCAL is missing or invalid")
    if model.verification is None or not model.verification.content_fingerprint:
        blockers.append("model has no interpretation content fingerprint (produced before schema 0.5.0); "
                        "reprocess the drawing")
    regions = _selected(model, readiness)
    if not regions:
        blockers.append("no selected plan region")
    for r in regions:
        if r.get("view_type") not in ENGINEERING_VIEW_TYPES:
            blockers.append(f"selected region {r['id']} is not a plan view")
    rooms = _live_rooms(model)
    for r in regions:
        in_r = [e for e in rooms if (_region_of(e, [r]) is not None)]
        if not in_r:
            blockers.append(f"selected region {r['id']}: no room/space boundary (required room boundary unresolved)")
        merged = [e.id for e in in_r if e.subtype == "suspected_merged_region"]
        if merged:
            blockers.append(f"selected region {r['id']}: merged-room boundaries {merged} unresolved "
                            "(reject or replace them with human room boundaries)")
        live = {e.uid for e in in_r}
        unresolved = [s.id for s in model.elements_of("space")
                      if s.properties.get("boundary_state") != "known" and s.properties.get("region_uid") in live
                      and s.provenance.review.status != "rejected"]
        if unresolved:
            blockers.append(f"selected region {r['id']}: semantic spaces {unresolved} have UNRESOLVED boundaries "
                            "(draw or confirm their boundaries before engineering)")
        unclassified = [e.id for e in in_r if e.boundary is None]
        if unclassified:
            blockers.append(f"selected region {r['id']}: physical regions {unclassified} have no classified boundary "
                            "(model produced before schema 0.5.0, or an invalid polygon); reprocess the drawing")
    codes = {t.code for t in model.diagnostics.review_triggers}
    if "HUMAN_CORRECTIONS_NOT_APPLIED" in codes or "HUMAN_CORRECTIONS_FROM_OTHER_REVISION" in codes:
        blockers.append("stored human corrections were not applied to this model")
    return blockers


def space_engineering_blockers(package: EngineeringInput, space_uid: str,
                               require_semantic_space: bool = True) -> list[str]:
    """Reasons why engineering may not run on ONE space of a (valid, verified) package.

    Package-level blockers (``engineering_input_blockers``) decide whether a verified REGION may be
    engineered at all; this decides whether a single SPACE in it may be, using only the package (no
    model, no CAD). ``space_uid`` is a semantic-space uid or a physical-region (``spaces[]``) uid.
    A boundary portion that is not established (``unknown``) blocks: distances to walls are
    undefined there, and FireAI never assumes it is a wall."""
    semantic = {s.uid: s for s in package.semantic_spaces}
    regions = {s.uid: s for s in package.spaces}
    if space_uid in semantic:
        region = regions.get(semantic[space_uid].region_uid)
    else:
        region = regions.get(space_uid)
    if region is None:
        return [f"space {space_uid} is not a physical region or known semantic space of this package"]
    blockers: list[str] = []
    named = [s for s in package.semantic_spaces if s.region_uid == region.uid]
    if require_semantic_space and len(named) != 1:
        blockers.append(f"physical region {region.id} has {len(named)} known named spaces (exactly one is required)")
    unknown = [s for r in region.boundary.rings for s in r.segments if s.kind == "unknown"]
    if unknown or not region.boundary.complete:
        blockers.append(
            f"physical region {region.id}: boundary not established over {sum(s.length_ft for s in unknown):.2f} ft "
            f"(segments {[s.index for s in unknown]}): what bounds the space there is unknown; distances to walls "
            "are undefined. A person must resolve it (e.g. a human room boundary) before engineering")
    openings = {o.uid for o in package.openings}
    dangling = [s.index for r in region.boundary.rings for s in r.segments if s.opening_uid and s.opening_uid not in openings]
    if dangling:
        blockers.append(f"physical region {region.id}: boundary segments {dangling} reference openings missing from "
                        "the package")
    return blockers


def _contract_boundary(b) -> ContractBoundary:
    return ContractBoundary(
        plane_z_status=b.plane_z_status, complete=b.complete, length_by_kind_ft=dict(b.length_by_kind_ft),
        rings=[ContractBoundaryRing(role=ring.role, orientation=ring.orientation, segments=[
            ContractBoundarySegment(
                uid=s.uid, index=s.index, kind=s.kind, encloses=s.encloses, start_local_ft=tuple(s.start),
                end_local_ft=tuple(s.end), length_ft=s.length_ft, opening_uid=s.opening_uid,
                fill_element_uid=s.fill_element_uid, derived_from=list(s.derived_from), confidence=s.confidence,
                requires_verification=s.requires_verification, rules=list(s.rules),
                vertical_extent=ContractVerticalExtent(**s.vertical_extent.model_dump()))
            for s in ring.segments]) for ring in b.rings])


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
                                        derived_from=list(e.provenance.derived_from),
                                        boundary=_contract_boundary(e.boundary)))
        elif e.category in ("wall", "column") and e.provenance.review.status != "rejected" and e.geometry.points:
            item = ContractLinework(uid=e.uid, category=e.category, subtype=e.subtype,
                                    points_local_ft=[tuple(p) for p in e.geometry.points], closed=e.geometry.closed,
                                    region_uid=r["uid"], derived_from=list(e.provenance.derived_from))
            (walls_lw if e.category == "wall" else cols).append(item)
    live_regions = {s.uid for s in spaces}
    semantic = [ContractSemanticSpace(uid=s.uid, id=s.id, label=s.label, boundary_state="known",
                                      region_uid=s.properties["region_uid"], derived_from=list(s.provenance.derived_from))
                for s in model.elements_of("space")
                if s.properties.get("boundary_state") == "known" and s.properties.get("region_uid") in live_regions]
    openings = []
    for o in model.elements_of("opening"):
        p = o.properties
        connected = [u for u in p.get("connects_region_uids", []) if u in live_regions]
        if not connected or o.provenance.review.status == "rejected":
            continue
        openings.append(ContractOpening(
            uid=o.uid, id=o.id, kind=o.subtype if o.subtype in ("door", "open", "window") else "unknown",
            passable=p.get("passable"), footprint_local_ft=[tuple(q) for q in o.geometry.points],
            width_ft=p["width_ft"], depth_ft=p.get("depth_ft"), fill_element_uid=p.get("fill_element_uid"),
            fill_category=p.get("fill_category"), space_uids=connected,
            boundary_segment_uids=list(p.get("boundary_segment_uids", [])),
            derived_from=sorted(set(o.provenance.derived_from) | set(p.get("derived_from_elements", []))),
            confidence=o.confidence,
            requires_verification=o.requires_verification, review_status=o.provenance.review.status,
            vertical_extent=ContractVerticalExtent(**(p.get("vertical_extent") or {}))))
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
        content_fingerprint=v.content_fingerprint, verification_fingerprint=v.fingerprint,
        verified_by=ver.get("reviewer") or "", verified_at=ver.get("verified_at") or "",
        source_units=model.units.resolved_units, source_to_local=_source_to_local(model),
        spatial_context={"status": model.spatial_structure.status, "buildings": model.spatial_structure.buildings,
                         "levels": model.spatial_structure.levels},
        regions=[ContractRegion(uid=r["uid"], id=r["id"], view_type=r["view_type"],
                                view_type_source=r.get("view_type_source", "fireai"), bbox_local_ft=r["bbox_ft"])
                 for r in regions],
        spaces=spaces, semantic_spaces=semantic, openings=openings, walls_analysis=walls, walls_linework=walls_lw,
        columns=cols, xrefs=[{"name": x.name, "status": x.status, "sha256": x.sha256} for x in model.xrefs],
        conversion_significance=(model.source.conversion_audit or {}).get("significance"),
        human_corrections_applied=[a["correction_id"] for a in model.human_corrections_applied
                                   if a["status"] == "applied"],
    )
