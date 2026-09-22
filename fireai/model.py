"""Canonical normalized building model (schema 0.1.0).

Two layers are kept strictly separate:

* ``entities`` — what the drawing actually contains (source geometry), with
  original layer, entity type, handle, source coordinates and normalized
  coordinates. Nothing here is interpreted.
* ``elements`` — what FireAI believes those entities represent. Every element
  cites its source entity ids, a confidence, the evidence/rules used, and
  whether a human must verify it.

Normalized coordinates: ``normalized = (source - transform.origin) * transform.scale``
in ``units.normalized_units`` (feet). The transform is stored so any normalized
value can be traced back to the source drawing exactly.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "0.1.0"

Point = tuple[float, float]


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
    insert_count: int = 0
    is_xref: bool = False
    inferred_role: Optional[str] = None
    role_confidence: float = 0.0
    role_rule: Optional[str] = None


class Geometry(BaseModel):
    """2D geometry. ``kind`` determines which fields are populated."""
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


class SourceEntity(BaseModel):
    id: str                               # stable id within this model, e.g. "E000123"
    handle: Optional[str] = None          # DXF handle (None for block-exploded children)
    type: str                             # DXF entity type (LINE, INSERT, ...)
    layer: str
    space: Literal["model", "paper"] = "model"
    parent_id: Optional[str] = None       # INSERT this entity was exploded from
    block_path: list[str] = Field(default_factory=list)  # block names from outermost INSERT
    visible: bool = True                  # False if on an off/frozen layer
    supported: bool = True                # False if FireAI cannot represent its geometry
    source: Optional[Geometry] = None     # original WCS coordinates, drawing units
    normalized: Optional[Geometry] = None # normalized coordinates (None if units unresolved)
    attributes: dict[str, Any] = Field(default_factory=dict)


class BuildingElement(BaseModel):
    id: str
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
    geometry: Optional[Geometry] = None          # normalized coordinates
    properties: dict[str, Any] = Field(default_factory=dict)


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
    transform: Transform
    bounds_source: Optional[Bounds] = None
    bounds_normalized: Optional[Bounds] = None
    scale: ScaleInfo = Field(default_factory=ScaleInfo)
    layers: list[LayerInfo] = Field(default_factory=list)
    blocks: list[BlockInfo] = Field(default_factory=list)
    entities: list[SourceEntity] = Field(default_factory=list)
    elements: list[BuildingElement] = Field(default_factory=list)
    unclassified_entity_ids: list[str] = Field(default_factory=list)
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
