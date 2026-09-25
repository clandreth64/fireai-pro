"""Design domain objects (Milestone 2.0): the seed of the authoritative engineering model's design side.

Every sprinkler is a first-class, 3D-capable design object with a stable uid, an explicit frame,
an explicit Z state (UNKNOWN until established — never invented), the design it belongs to, and
provenance pinning the exact package, rule sets, listing, inputs and engine. Later systems
(routing, hydraulics, coordination, fabrication, BIM export) extend these objects by uid; they
never re-derive sprinklers from drawings or reinterpret XY dots.

``EngineeringDesignResult`` enforces, at construction, that any result touching synthetic (TEST ONLY)
rules or listings is marked TEST ONLY / NOT FOR ENGINEERING USE in its structured fields.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from fireai.engineering.inputs import LayoutOrientation

PLACEMENT_ENGINE_VERSION = "fireai.engineering.placement/0.2.0"      # M2.2A: new measurements, fingerprint covers system/eligibility/deflector
ORDERING_STRATEGY = "ORDER-LONGAXIS-LR-BT/1"      # layouts: fewer sprinklers first; sprinklers: by v (bottom-to-top) then u (left-to-right) in the room frame
SYNTHETIC_DISCLAIMERS = ("TEST ONLY", "SYNTHETIC RULE VALUES", "NOT NFPA 13 COMPLIANT", "NOT FOR ENGINEERING USE")
# M2.2B: approved rules exercised with a SYNTHETIC listing (rule-engine validation only)
RULE_VALIDATION_DISCLAIMERS = ("RULE VALIDATION ONLY", "SYNTHETIC LISTING DATA", "NOT A PRODUCT-SPECIFIC DESIGN",
                               "NOT FOR ENGINEERING USE")
RULE_REVIEW_DISCLAIMERS = ("RULES UNDER REVIEW - NOT APPROVED", "KNOWN-ANSWER VERIFICATION ONLY",
                           "SYNTHETIC LISTING DATA", "NOT FOR ENGINEERING USE")


class ZState(BaseModel):
    status: Literal["unknown", "from_input", "derived"] = "unknown"
    value_ft: Optional[float] = None
    datum: Optional[str] = None
    reference: Optional[str] = None           # e.g. "ceiling_region:<uid>"
    note: str = ""


class DesignPoint(BaseModel):
    """A 3D-capable point. x, y in the contract's LOCAL frame (ft); z explicit (unknown allowed)."""
    frame: Literal["LOCAL"] = "LOCAL"
    units: Literal["ft"] = "ft"
    x: float
    y: float
    z: ZState = Field(default_factory=ZState)
    room_uv: tuple[float, float]              # the same point in the room frame (explanation/ordering)


class DesignProvenance(BaseModel):
    origin: Literal["design"] = "design"
    engine: str = PLACEMENT_ENGINE_VERSION
    request_fingerprint: str
    package_content_fingerprint: str
    package_verification_fingerprint: str
    space_uid: str
    rule_sets: list[dict]
    listing: Optional[dict] = None
    ceiling_uid: Optional[str] = None
    derived_from: list[str] = Field(default_factory=list)       # uids: space, physical region, ceiling region


class SprinklerPlacement(BaseModel):
    uid: str
    layout_uid: str
    index: int
    position: DesignPoint
    listing_ref: Optional[dict] = None
    orientation: Optional[str] = None
    provenance: DesignProvenance


class ConstraintEvaluation(BaseModel):
    constraint_key: str
    measurement: str
    bound: Literal["max", "min"]
    limit: float
    unit: str
    tolerance: float
    measured: Optional[float] = None
    margin: Optional[float] = None            # positive = inside the limit
    passed: bool
    outcome: Literal["pass", "fail", "not_evaluable"] = "pass"      # M2.1: not_evaluable is never a pass
    worst_subject: Optional[dict[str, Any]] = None
    reference_kinds: list[str] = Field(default_factory=list)
    governing_rule_id: Optional[str] = None
    contributing_rule_ids: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    note: str = ""


class LayoutEvaluation(BaseModel):
    layout_uid: str
    sprinkler_count: int
    array: dict[str, Any]                     # counts, lattice indices, spacings along u and v
    placements: list[SprinklerPlacement] = Field(default_factory=list)
    valid: bool
    evaluations: list[ConstraintEvaluation] = Field(default_factory=list)
    first_failure: Optional[str] = None


class DesignIssue(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class EngineeringDesignResult(BaseModel):
    schema_: Literal["engineering_design_result/1"] = Field("engineering_design_result/1", alias="schema")
    result_uid: str
    status: Literal["VALID_LAYOUTS_FOUND", "NO_VALID_LAYOUT_IN_SEARCH_SPACE", "REFUSED"]
    basis: Literal["authoritative", "synthetic_test_only", "authoritative_rules_synthetic_listing", "rules_under_review", "none"]
    engineering_use: Literal["NOT_FOR_ENGINEERING_USE", "REQUIRES_QUALIFIED_HUMAN_APPROVAL", "NONE_REFUSED"]
    disclaimers: list[str] = Field(default_factory=list)
    refusals: list[DesignIssue] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    context: dict[str, Any]
    inputs: dict[str, Any]
    rules: dict[str, Any]
    search: dict[str, Any] = Field(default_factory=dict)
    valid_layouts: list[LayoutEvaluation] = Field(default_factory=list)
    rejected_examples: list[LayoutEvaluation] = Field(default_factory=list)
    valid_set: Optional["ValidLayoutSet"] = None       # M2.1: the complete valid set, compactly
    ordering_strategy: str = ORDERING_STRATEGY
    engine_version: str = PLACEMENT_ENGINE_VERSION
    request_fingerprint: str
    result_fingerprint: str = ""

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _no_synthetic_result_can_pass_as_engineering(self):
        if self.basis == "synthetic_test_only":
            missing = [d for d in SYNTHETIC_DISCLAIMERS if d not in self.disclaimers]
            if missing or self.engineering_use != "NOT_FOR_ENGINEERING_USE":
                raise ValueError(f"synthetic results must be NOT_FOR_ENGINEERING_USE with disclaimers {missing}")
        if self.basis in ("authoritative_rules_synthetic_listing", "rules_under_review"):
            need = RULE_VALIDATION_DISCLAIMERS if self.basis == "authoritative_rules_synthetic_listing" else RULE_REVIEW_DISCLAIMERS
            missing = [d for d in need if d not in self.disclaimers]
            if missing or self.engineering_use != "NOT_FOR_ENGINEERING_USE":
                raise ValueError(f"rule-validation results must be NOT_FOR_ENGINEERING_USE with disclaimers {missing}")
        if self.status == "REFUSED" and (self.valid_layouts or self.valid_set or self.engineering_use != "NONE_REFUSED"):
            raise ValueError("a refused design carries no layouts and no engineering use")
        if self.valid_set is not None and (self.valid_set.count > 0) != (self.status == "VALID_LAYOUTS_FOUND"):
            raise ValueError("status must agree with the complete valid set, not only the explicit layouts")
        if self.basis == "authoritative" and self.status != "REFUSED" \
                and self.engineering_use != "REQUIRES_QUALIFIED_HUMAN_APPROVAL":
            raise ValueError("an authoritative design result always requires qualified human approval")
        return self


class ValidLayoutFamily(BaseModel):
    """Arrays with the same shape: n_u x n_v sprinklers at lattice spacings ds_u, ds_v (indices)."""
    n_u: int
    ds_u: int
    n_v: int
    ds_v: int
    offsets: list[tuple[int, int]]            # first lattice index (u, v) of each valid array of this shape


class ValidLayoutSet(BaseModel):
    """The COMPLETE valid set of the search space, compactly (M2.1). Layout k is re-materialised
    deterministically from the lattice (``fireai.engineering.placement.iter_valid_layouts``); nothing
    here is optimised or ranked. Positions: (u, v) = (i * step, j * step) in the room frame, mapped to
    LOCAL with ``room_frame``."""
    representation: Literal["room_axis_array_indices/1"] = "room_axis_array_indices/1"
    count: int
    lattice_step_ft: float
    room_frame: dict[str, Any]
    families: list[ValidLayoutFamily] = Field(default_factory=list)
    explicit_layouts_included: bool
    note: str = ""


class CandidateProposal(BaseModel):
    """A layout PROPOSED by an agent / optimiser / person. It carries no authority: the rules engine and
    the deterministic evaluator decide. It must say which verified model, rules and listing it was
    proposed against; any mismatch is refused (never silently re-targeted)."""
    proposed_by: str
    frame: str = "LOCAL"
    units: str = "ft"
    package_content_fingerprint: str
    package_verification_fingerprint: str
    rule_sets: list[dict[str, str]]           # [{rule_set_id, version, digest}]
    listing: dict[str, str]                   # {listing_id, version, digest}
    positions: list[tuple[float, float]]      # sprinkler plan positions (x, y) in the stated frame
    orientation: Optional[LayoutOrientation] = None   # M2.2A.1: the proposer may choose the branch-line direction
    note: str = ""


class ProposalEvaluation(BaseModel):
    verdict: Literal["PASS", "FAIL", "UNKNOWN", "REFUSED"]
    reasons: list[DesignIssue] = Field(default_factory=list)
    evaluations: list[ConstraintEvaluation] = Field(default_factory=list)
    proposal_digest: str
    request_fingerprint: str
    basis: Literal["authoritative", "synthetic_test_only", "authoritative_rules_synthetic_listing", "rules_under_review", "none"]
    disclaimers: list[str] = Field(default_factory=list)
    note: str = ("The verdict comes only from the rules engine and deterministic evaluation; the proposer cannot "
                 "override it. PASS on synthetic rules is TEST ONLY.")




EngineeringDesignResult.model_rebuild()
