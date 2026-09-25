"""Explicit engineering inputs for design (Milestone 2.0).

Everything the drawing cannot establish arrives here as an explicit, attributed input — never a
default. The schemas are durable (commercial scope); M2.0 SUPPORTS only a subset and refuses the
rest (``m2_ceiling_support``). Unknown is a first-class state everywhere.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from fireai.contract import EngineeringInput
from fireai.rules.model import SYNTHETIC_MARK, Quantity, RuleParameter, RuleSet, RuleSource
from fireai.rules.resolve import JurisdictionStatement, Mode

InputKind = Literal["human_decision", "project_document", "survey", "bim_model", "manufacturer_document",
                    "design_strategy", "synthetic_test_only"]


class InputSource(BaseModel):
    kind: InputKind
    by: Optional[str] = None                 # unauthenticated name today (not production-safe)
    at: Optional[str] = None
    reference: Optional[str] = None
    note: str = ""


class Elevation(BaseModel):
    """A height relative to an explicit datum (e.g. 'finished floor of the space'). Never defaulted."""
    status: Literal["known", "unknown"] = "unknown"
    value_ft: Optional[float] = None
    datum: Optional[str] = None
    source: Optional[InputSource] = None

    @model_validator(mode="after")
    def _known_needs_value(self):
        if self.status == "known" and (self.value_ft is None or not self.datum or self.source is None):
            raise ValueError("a known elevation needs a value, a datum and a source")
        return self


class Slope(BaseModel):
    status: Literal["known", "unknown"] = "unknown"
    value_deg: Optional[float] = None
    direction_deg: Optional[float] = None     # plan direction of the fall (LOCAL frame), when sloped
    source: Optional[InputSource] = None


class CeilingFeature(BaseModel):
    uid: str
    kind: Literal["beam", "soffit", "cloud", "structural_obstruction", "mep_obstruction", "opening", "other"]
    footprint_local_ft: Optional[list[tuple[float, float]]] = None
    bottom: Elevation = Field(default_factory=Elevation)
    status: Literal["known", "unknown"] = "unknown"
    source: Optional[InputSource] = None


class ConstructionClassification(BaseModel):
    """M2.2A: the construction CLASSIFICATION a standard uses for rule applicability (the standard
    defines the categories; a person states which applies). Distinct from the GEOMETRIC ceiling facts
    (surface, slope, elevation, the observed ``construction`` condition): a geometrically flat, smooth,
    unobstructed ceiling does not by itself establish a classification, and FireAI never derives one."""
    scheme: str                               # e.g. which standard / edition's classification scheme
    value: str                                # the stated category, verbatim as the person recorded it
    reason: str = ""
    source: InputSource


class CeilingRegion(BaseModel):
    uid: str
    extent: Literal["whole_space"] | list[tuple[float, float]] = "whole_space"
    surface: Literal["flat", "sloped", "curved", "stepped", "open_structure", "unknown"] = "unknown"
    # GEOMETRIC / observed condition (M2.1): what the person states about the ceiling's form. Unknown is
    # the default and is never treated as unobstructed. NOT the standard's construction classification.
    construction: Literal["smooth_unobstructed", "obstructed", "unknown"] = "unknown"
    # M2.2A: the standard's construction classification (rule applicability) — a separate fact
    construction_classification: Optional[ConstructionClassification] = None
    slope: Slope = Field(default_factory=Slope)
    elevation: Elevation = Field(default_factory=Elevation)
    source: Optional[InputSource] = None


class CeilingCondition(BaseModel):
    """The ceiling over ONE space: regions (with changes in elevation), features (beams, soffits,
    clouds, structural/MEP obstructions) and an explicit statement about obstructions."""
    uid: str
    space_uid: str
    regions: list[CeilingRegion] = Field(default_factory=list)
    features: list[CeilingFeature] = Field(default_factory=list)
    obstructions_statement: Literal["none_present", "present_listed", "unknown"] = "unknown"
    statement_source: Optional[InputSource] = None


class DesignClassification(BaseModel):
    """Hazard / occupancy / commodity classification: an explicit ENGINEERING INPUT with its basis.
    FireAI never infers it; a future recommendation stays a proposal until a person decides."""
    scheme: str                               # the classification scheme it belongs to
    value: str
    reason: str
    source: InputSource


class SprinklerListing(BaseModel):
    """One sprinkler's listing data: authoritative, versioned input (never invented). Listing
    constraints are rules of the ``listing`` layer so they combine with the standard's rules."""
    listing_id: str
    version: str
    content_basis: Literal["authoritative", "synthetic_test_only"]
    manufacturer: str
    model: str
    sin: Optional[str] = None
    product_family: Optional[str] = None
    sprinkler_type: str
    orientation: str
    k_factor: Optional[Quantity] = None                 # unknown stays None — never a remembered value
    temperature_rating: Optional[Quantity] = None
    response_type: Optional[str] = None
    parameters: list[RuleParameter] = Field(default_factory=list)          # further listed data, with units
    installation_constraints: list[RuleParameter] = Field(default_factory=list)
    document: RuleSource                                # data sheet / listing document: id, revision, date, access
    author: Optional[str] = None
    authored_at: Optional[str] = None
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_status: Literal["draft", "under_review", "reviewed", "approved", "superseded", "retired",
                           "withdrawn"] = "draft"
    change_reason: str = ""
    rules: RuleSet                                      # coverage / spacing / other listed limits as listing rules

    @model_validator(mode="after")
    def _consistent(self):
        if self.rules.layer != "listing":
            raise ValueError("listing rules must be a 'listing' layer rule set")
        if self.rules.content_basis != self.content_basis:
            raise ValueError("listing rules must share the listing's content basis")
        if self.content_basis == "synthetic_test_only" and SYNTHETIC_MARK not in self.listing_id:
            raise ValueError(f"synthetic listings must carry {SYNTHETIC_MARK} in their id")
        return self

    def digest(self) -> str:
        import hashlib
        import json
        body = self.model_dump(mode="json", exclude={"reviewer", "reviewed_at", "review_status"})
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def identity(self) -> dict:
        return {"listing_id": self.listing_id, "version": self.version, "content_basis": self.content_basis,
                "manufacturer": self.manufacturer, "model": self.model, "sin": self.sin,
                "document": self.document.document, "revision": self.document.revision,
                "review_status": self.review_status, "rules_digest": self.rules.digest(), "digest": self.digest()}


class SystemCondition(BaseModel):
    """The system and storage condition of the design area: explicit, attributed inputs (never assumed).
    M2.2A: ``design_method`` (how the system is designed) is a separate fact from ``system_type`` (what
    kind of system it is); neither implies the other."""
    system_type: str                                  # e.g. "wet_pipe" / "dry_pipe" / "preaction" (stated, not inferred)
    storage: str                                      # e.g. "non_storage" / "storage" / "unknown"
    design_method: Literal["hydraulically_calculated", "pipe_schedule", "other", "unknown"] = "unknown"
    reason: str = ""
    source: InputSource


class EligibilityCriterion(BaseModel):
    """One consideration a person weighed (hazard, area, ceiling/construction, walls/openings, ...)."""
    fact: str
    value: Optional[str | float | bool] = None
    note: str = ""


class EligibilityDecision(BaseModel):
    """M2.2A: an explicit, human-attributed eligibility determination for a special provision (e.g. a
    small-room provision). FireAI never derives eligibility — in particular not from area alone. Only
    ``eligible`` can activate a provision; ``unknown`` never does, and an absent decision is UNKNOWN to
    the rules engine (a rule that needs it refuses)."""
    status: Literal["eligible", "not_eligible", "unknown"]
    criteria: list[EligibilityCriterion] = Field(default_factory=list)
    reason: str = ""
    source: InputSource

    @model_validator(mode="after")
    def _decided_by_a_person(self):
        if self.status != "unknown" and (self.source.kind not in ("human_decision", "project_document",
                                                                 "synthetic_test_only") or not self.source.by):
            raise ValueError("an eligibility determination must be a named human decision or project document")
        if self.status == "eligible" and not self.criteria:
            raise ValueError("an 'eligible' determination must record the criteria that were considered")
        return self


class LayoutOrientation(BaseModel):
    """M2.2A.1: the sprinkler layout's BRANCH-LINE direction (a first-class design fact).

    S is measured along the branch lines, L across them (between branch lines). The room's long axis
    does NOT define S: an orientation is chosen by a person, a routing engine, an optimiser, or — before
    routing exists — an explicitly named, versioned DEFAULT STRATEGY (``strategy``), and it is recorded,
    fingerprinted and provenance-bearing like any other input. A direction is a line: its sign does not
    matter and is canonicalised (x > 0, or x == 0 and y > 0)."""
    frame: Literal["LOCAL", "PROJECT"] = "LOCAL"
    branch_line_direction: tuple[float, float]         # plan direction of the branch lines, in ``frame``
    strategy: str                                      # "explicit_design_input" | "ROOM-LONG-AXIS-DEFAULT/1" | "routing:<id>" ...
    version: str = "1"
    source: InputSource
    note: str = ""

    @model_validator(mode="after")
    def _unit_and_canonical(self):
        x, y = self.branch_line_direction
        n = (x * x + y * y) ** 0.5
        if n == 0.0:
            raise ValueError("a branch-line direction must be a non-zero vector")
        x, y = x / n, y / n
        if x < 0 or (x == 0 and y < 0):
            x, y = -x, -y
        self.branch_line_direction = (x, y)
        return self

    @property
    def cross_line_direction(self) -> tuple[float, float]:
        x, y = self.branch_line_direction
        return (-y, x)


class DeflectorPosition(BaseModel):
    """M2.2A: the sprinkler deflector elevation, as an explicit design input on an explicit datum.
    With it, placements carry a known Z; without it Z stays UNKNOWN and vertical rules refuse."""
    frame: Literal["LOCAL", "PROJECT"] = "LOCAL"
    elevation: Elevation                               # deflector elevation (value, datum, source)


class EngineeringTolerances(BaseModel):
    """Pass/fail tolerances are explicit inputs; engines never introduce one silently."""
    length_ft: float = Field(ge=0)
    area_sf: float = Field(ge=0)
    source: InputSource


class PlacementSearchSpace(BaseModel):
    """The deliberately bounded, deterministic candidate space. ``room_axis_array/1``: rectangular
    arrays aligned with the room frame (u = long axis), positions on a lattice of ``lattice_step_ft``
    strictly inside the space, uniform spacing along each axis."""
    family: Literal["room_axis_array/1"] = "room_axis_array/1"
    lattice_step_ft: float = Field(gt=0)
    max_sprinklers: int = Field(ge=1)
    max_per_axis: int = Field(ge=1)
    excluded_regions_local_ft: list[list[tuple[float, float]]] = Field(default_factory=list)
    max_rejected_examples: int = Field(default=3, ge=0)
    # representation (not engineering): valid sets larger than this are returned compactly
    # (ValidLayoutSet) and materialised on demand instead of as explicit layout objects
    max_explicit_layouts: int = Field(default=200, ge=0)
    source: InputSource


class DesignRequest(BaseModel):
    """Everything one deterministic design run depends on (all explicit)."""
    package: EngineeringInput
    space_uid: str
    mode: Mode
    rule_sets: list[RuleSet] = Field(default_factory=list)       # base / edition / jurisdiction / referenced / project / decisions
    jurisdiction: Optional[JurisdictionStatement] = None
    listing: Optional[SprinklerListing] = None
    ceiling: Optional[CeilingCondition] = None
    classification: Optional[DesignClassification] = None
    system: Optional[SystemCondition] = None
    tolerances: Optional[EngineeringTolerances] = None
    search: Optional[PlacementSearchSpace] = None
    eligibility: dict[str, EligibilityDecision] = Field(default_factory=dict)   # M2.2A, e.g. {"small_room": ...}
    deflector: Optional[DeflectorPosition] = None                               # M2.2A
    orientation: Optional[LayoutOrientation] = None                             # M2.2A.1: branch-line direction
    requested_by: Optional[str] = None
