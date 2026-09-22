"""Canonical normalized building model (schema 0.2.0).

Schema history (see fireai/schema.py for migrations):
* 0.1.0 — Milestone 1: 2D drawing understanding.
* 0.2.0 — Milestone 1.5 foundations: persistent uids, explicit coordinate
  frames, placement (building/level/Z) fields that default to UNKNOWN,
  provenance, recorded source Z range, DWG->DXF conversion linkage.

Two layers are kept strictly separate:

* ``entities`` — what the drawing actually contains (source geometry), with
  original layer, entity type, handle, source coordinates and normalized
  coordinates. Nothing here is interpreted.
* ``elements`` — what FireAI believes those entities represent. Every element
  cites its source entity ids, a confidence, the evidence/rules used, and
  whether a human must verify it.

Coordinate frames (``coordinate_frames``; every Geometry carries ``frame``):
* SRC     — source drawing WCS, drawing units. Entity ``source`` geometry.
* SRC_FT  — source WCS scaled to feet, NO translation. Stable across edits and
            shared by files that share CAD coordinates. Basis for PROJECT.
* LOCAL   — SRC_FT translated so the lower-left of visible model-space geometry
            is (0, 0). Entity ``normalized`` and element geometry. Convenient
            for display; NOT stable (depends on drawing content).
* PROJECT — coordinated project frame. UNRESOLVED until a person or adapter
            establishes the SRC_FT -> PROJECT transform. Never assumed.
Z: plan geometry is XY. Source Z is recorded as ``z_range`` (drawing values),
never promoted to elevation. Element elevations/levels stay UNKNOWN unless the
source establishes them.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "0.2.0"

Point = tuple[float, float]
FrameId = Literal["SRC", "SRC_FT", "LOCAL", "PROJECT"]


class Issue(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    entity_ids: list[str] = Field(default_factory=list)
    element_ids: list[str] = Field(default_factory=list)


class SourceInfo(BaseModel):
    filename: str                      # sanitized original filename (display only)
    format: Literal["dxf", "dwg"]
    sha256: str
    size_bytes: int
    dxf_version: Optional[str] = None  # e.g. AC1032
    converted_from_dwg: bool = False
    converter: Optional[dict[str, Any]] = None  # {name, version, command}
    # 0.2.0: identity + DWG->DXF linkage
    source_uid: Optional[str] = None             # uuid5 of sha256 — namespace for every uid in this model
    document_guid: Optional[str] = None          # DXF $FINGERPRINTGUID (survives saves; NOT unique across Save-As copies)
    version_guid: Optional[str] = None           # DXF $VERSIONGUID (changes on every save)
    converted_dxf_sha256: Optional[str] = None   # the DXF actually parsed, when converted from DWG
    converter_log_tail: Optional[str] = None
    converter_warnings: list[str] = Field(default_factory=list)
    conversion_audit: Optional[dict[str, Any]] = None   # independent DWG vs DXF entity census (see ingest/dwg.py)


class CoordinateFrame(BaseModel):
    """A named coordinate frame and its affine transform to its parent frame.

    ``matrix`` is a row-major 4x4 homogeneous matrix mapping a point in THIS
    frame's parent into this frame: p_this = M @ p_parent. ``None`` with
    status "unresolved" means the relationship is unknown (never assumed).
    """
    id: FrameId
    parent: Optional[FrameId] = None
    units: Optional[str] = None
    status: Literal["defined", "unresolved"] = "defined"
    matrix: Optional[list[list[float]]] = None
    description: str = ""


class Placement(BaseModel):
    """Spatial placement of an element. Everything defaults to UNKNOWN.
    Values are set only when the source establishes them — never inferred to
    make the model look complete."""
    building_id: Optional[str] = None
    level_id: Optional[str] = None
    assignment_status: Literal["unassigned", "from_source", "user"] = "unassigned"
    elevation_ft: Optional[float] = None       # base elevation in the PROJECT vertical datum
    height_ft: Optional[float] = None
    thickness_ft: Optional[float] = None
    rotation_deg: Optional[float] = None       # plan rotation about Z, from source data only
    z_status: Literal["unknown", "from_source", "user"] = "unknown"


class ReviewState(BaseModel):
    status: Literal["unreviewed", "confirmed", "rejected", "modified"] = "unreviewed"
    by: Optional[str] = None
    at: Optional[str] = None
    note: Optional[str] = None


class Provenance(BaseModel):
    """Where an object came from. Designed to extend (design, coordination,
    fabrication release) without changing its meaning."""
    origin: Literal["source", "deterministic_inference", "design", "human"]
    engine: str = "fireai"
    engine_version: Optional[str] = None
    rule_ids: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)   # uids of upstream objects
    review: ReviewState = Field(default_factory=ReviewState)


class SpatialStructure(BaseModel):
    """Project -> Building -> Level hierarchy. Empty/unassigned until the source
    (or a person) establishes it; FireAI does not invent buildings or levels."""
    project_id: Optional[str] = None
    buildings: list[dict[str, Any]] = Field(default_factory=list)
    levels: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["unassigned", "from_source", "user"] = "unassigned"
    note: str = "Building/level assignment not established from this source."


class UnitsInfo(BaseModel):
    insunits_code: Optional[int] = None
    detected_units: Optional[str] = None       # from the drawing header
    resolved_units: Optional[str] = None       # units actually used for normalization
    resolution_method: Literal["drawing_header", "user_override", "unresolved"] = "unresolved"
    normalized_units: Literal["ft"] = "ft"
    scale_to_normalized: Optional[float] = None
    measurement_system_hint: Optional[str] = None  # $MEASUREMENT: informational only, never used to resolve
    resolved: bool = False
    note: Optional[str] = None
    evidence: Optional[dict[str, Any]] = None  # drawing statements about scale/units; suggestion only, never applied


class Transform(BaseModel):
    origin: Point = (0.0, 0.0)   # source-coordinate point mapped to normalized (0, 0)
    scale: Optional[float] = None  # source units -> normalized units


class Bounds(BaseModel):
    min: Point
    max: Point

    @property
    def width(self) -> float:
        return self.max[0] - self.min[0]

    @property
    def height(self) -> float:
        return self.max[1] - self.min[1]

    def as_dict(self) -> dict[str, Any]:
        return {"min": list(self.min), "max": list(self.max), "width": self.width, "height": self.height}


class DimensionCheck(BaseModel):
    entity_id: str
    stated_text: str
    stated_length_ft: float
    measured_length_ft: float
    relative_error: float
    agrees: bool


class ScaleInfo(BaseModel):
    model_space_full_scale_assumed: bool = True
    dimension_checks: list[DimensionCheck] = Field(default_factory=list)
    viewport_scales: list[dict[str, Any]] = Field(default_factory=list)
    title_block_scale_text: Optional[str] = None


class LayerInfo(BaseModel):
    name: str
    color: Optional[int] = None
    is_off: bool = False
    is_frozen: bool = False
    entity_count: int = 0
    entity_types: dict[str, int] = Field(default_factory=dict)
    inferred_role: Optional[str] = None
    role_confidence: float = 0.0
    role_rule: Optional[str] = None


class BlockInfo(BaseModel):
    name: str
    effective_name: Optional[str] = None      # original name of an anonymous dynamic-block copy (*Unn/*Bnn)
    insert_count: int = 0
    is_xref: bool = False
    inferred_role: Optional[str] = None
    role_confidence: float = 0.0
    role_rule: Optional[str] = None


class Geometry(BaseModel):
    """2D plan geometry. ``kind`` determines which fields are populated.
    ``frame`` names the coordinate frame the coordinates are expressed in."""
    kind: Literal["line", "polyline", "arc", "circle", "text", "point", "insert", "dimension", "hatch", "polygon", "multi"]
    points: list[Point] = Field(default_factory=list)       # line/polyline/flattened curves
    closed: bool = False
    center: Optional[Point] = None
    radius: Optional[float] = None
    start_angle: Optional[float] = None
    end_angle: Optional[float] = None
    paths: list[list[Point]] = Field(default_factory=list)  # hatch boundaries / multi-part
    insert: Optional[Point] = None                           # text/insert/dimension anchor
    text: Optional[str] = None
    height: Optional[float] = None
    rotation: Optional[float] = None
    measurement: Optional[float] = None                      # dimension measurement (in these units)
    frame: Optional[FrameId] = None                          # 0.2.0: SRC for entity.source, LOCAL otherwise


class SourceEntity(BaseModel):
    id: str                               # short display id within this model, e.g. "E000123"
    uid: Optional[str] = None             # 0.2.0: globally unique, deterministic (uuid5 of source_uid + handle path)
    handle_path: list[str] = Field(default_factory=list)  # 0.2.0: handles from outermost INSERT; block
                                          # children append their explode index ("#3"), MINSERT copies "@i"
    uid_basis: Literal["handle", "sequence"] = "handle"   # "sequence" = no DXF handles; uid unstable across edits
    source_object_key: Optional[str] = None  # document_guid + handle path: candidate cross-revision identity
    z_range: Optional[tuple[float, float]] = None  # 0.2.0: min/max source Z seen (drawing units); NOT an elevation
    handle: Optional[str] = None          # DXF handle (None for block-exploded children)
    type: str                             # DXF entity type (LINE, INSERT, ...)
    layer: str
    space: Literal["model", "paper"] = "model"
    parent_id: Optional[str] = None       # INSERT this entity was exploded from
    block_path: list[str] = Field(default_factory=list)  # block names from outermost INSERT
    visible: bool = True                  # False if on an off/frozen layer
    supported: bool = True                # False if FireAI cannot represent its geometry
    source: Optional[Geometry] = None     # original WCS coordinates, drawing units (frame SRC)
    normalized: Optional[Geometry] = None # LOCAL frame, feet (None if units unresolved)
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=lambda: Provenance(origin="source"))


class BuildingElement(BaseModel):
    id: str
    uid: Optional[str] = None                    # 0.2.0: uuid5(source_uid, category, source entity uids, rules)
    category: Literal[
        "wall", "door", "window", "room", "area", "column", "stair", "shaft",
        "structural", "grid_line", "text_annotation", "dimension", "title_block",
        "ceiling", "existing_fire_protection", "existing_mep",
    ]
    subtype: Optional[str] = None
    label: Optional[str] = None
    confidence: float
    evidence: list[str]
    rules: list[str] = Field(default_factory=list)
    source_entity_ids: list[str]
    requires_verification: bool
    geometry: Optional[Geometry] = None          # LOCAL frame, feet
    properties: dict[str, Any] = Field(default_factory=dict)
    placement: Placement = Field(default_factory=Placement)          # 0.2.0: UNKNOWN unless source-established
    provenance: Provenance = Field(default_factory=lambda: Provenance(origin="deterministic_inference"))


class Diagnostics(BaseModel):
    warnings: list[Issue] = Field(default_factory=list)
    errors: list[Issue] = Field(default_factory=list)
    review_triggers: list[Issue] = Field(default_factory=list)


class BuildingModel(BaseModel):
    schema_version: str = SCHEMA_VERSION
    model_id: str
    created_at: str
    fireai_version: str
    source: SourceInfo
    units: UnitsInfo
    transform: Transform                          # SRC -> LOCAL (kept for 0.1.0 compatibility)
    coordinate_frames: list[CoordinateFrame] = Field(default_factory=list)   # 0.2.0
    spatial_structure: SpatialStructure = Field(default_factory=SpatialStructure)  # 0.2.0
    bounds_source: Optional[Bounds] = None
    bounds_normalized: Optional[Bounds] = None
    scale: ScaleInfo = Field(default_factory=ScaleInfo)
    layers: list[LayerInfo] = Field(default_factory=list)
    blocks: list[BlockInfo] = Field(default_factory=list)
    entities: list[SourceEntity] = Field(default_factory=list)
    elements: list[BuildingElement] = Field(default_factory=list)
    unclassified_entity_ids: list[str] = Field(default_factory=list)
    view_regions: list[dict[str, Any]] = Field(default_factory=list)   # separate drawing regions in model space
    title_block: Optional[dict[str, Any]] = None
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)
    requires_human_review: bool = True
    # Prohibited in this pipeline: there is no code path that fabricates geometry.
    geometry_is_synthetic: Literal[False] = False
    # No design is performed in this milestone.
    ready_for_design: Literal[False] = False
    engineering_review_status: Literal["not_performed"] = "not_performed"
    assumptions: list[str] = Field(default_factory=list)
    ai_inference_used: Literal[False] = False

    def entity(self, entity_id: str) -> SourceEntity:
        for e in self.entities:
            if e.id == entity_id:
                return e
        raise KeyError(entity_id)

    def elements_of(self, category: str) -> list[BuildingElement]:
        return [e for e in self.elements if e.category == category]
