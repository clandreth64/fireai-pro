"""NFPA 13-centred rules domain model (Milestone 2.0). STRUCTURE ONLY — no rule values.

FireAI's commercial rules architecture is built around NFPA 13. This module defines HOW rules are
represented, selected, layered, traced and compiled into deterministic engineering constraints. It
contains no NFPA 13 requirement: production rule CONTENT must come from lawfully accessed
authoritative sources, be structured by a person and be human-reviewed (see
docs/NFPA13_RULES_ARCHITECTURE.md). LLM output, model memory, internet summaries and FireAI output
are never sources of rule content.

Rule hierarchy (``RuleLayer``), applied in this order:
    base_standard (NFPA 13) → edition → jurisdiction_amendment → referenced_standard →
    listing (manufacturer) → project → engineering_decision
Later layers do NOT simply override earlier ones: by default contributions to the same constraint
combine MOST-RESTRICTIVELY; a rule may only REPLACE another when the replaced rule explicitly
permits replacement by that layer (see ``fireai/rules/resolve.py``).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

RuleLayer = Literal["base_standard", "edition", "jurisdiction_amendment", "referenced_standard", "listing",
                    "project", "engineering_decision"]
LAYER_ORDER: tuple[str, ...] = ("base_standard", "edition", "jurisdiction_amendment", "referenced_standard",
                                "listing", "project", "engineering_decision")

# Families the rule model must be able to express (most are not used yet; listed so the model does
# not dead-end when they arrive).
RuleCategory = Literal[
    "occupancy_hazard_applicability", "system_requirement", "sprinkler_selection", "spacing",
    "protection_area", "distance_to_boundary", "ceiling_configuration", "obstruction", "structural_interference",
    "environmental_condition", "storage", "special_occupancy", "design_area", "density_discharge",
    "hose_allowance", "hydraulic_criteria", "pipe_system", "seismic", "hanging_bracing", "valves_equipment",
    "documentation", "other"]

# Where rule CONTENT comes from. Only the non-synthetic kinds can support real engineering.
SourceKind = Literal["authoritative_standard", "licensed_structured_content", "jurisdiction_amendment_document",
                     "referenced_standard", "manufacturer_listing_document", "project_specification",
                     "engineering_decision_record", "synthetic_test_only"]
ContentBasis = Literal["authoritative", "synthetic_test_only"]
ReviewStatus = Literal["draft", "reviewed", "approved", "withdrawn"]
EffectiveStatus = Literal["effective", "superseded", "withdrawn", "not_yet_effective"]

SYNTHETIC_MARK = "TEST_ONLY_SYNTHETIC"


class RuleSource(BaseModel):
    """Provenance of rule content. ``reference`` is a section/clause locator (e.g. a section number),
    never copied standard text. Synthetic rules must say so and carry no standard reference."""
    kind: SourceKind
    document: str                                   # e.g. standard designation, amendment ordinance, data sheet id
    edition: Optional[str] = None
    reference: Optional[str] = None                 # section / table / clause locator
    revision: Optional[str] = None
    publication_date: Optional[str] = None
    note: Optional[str] = None

    @model_validator(mode="after")
    def _synthetic_has_no_standard_reference(self):
        if self.kind == "synthetic_test_only":
            if self.reference is not None or (self.edition not in (None, "TEST_ONLY/NOT_APPLICABLE")):
                raise ValueError("synthetic rule sources carry no edition or standard reference")
            if SYNTHETIC_MARK not in self.document:
                raise ValueError(f"synthetic rule source documents must be marked {SYNTHETIC_MARK}")
        return self


class Quantity(BaseModel):
    """A value with an explicit unit. Engines convert with ``fireai.rules.units`` (exact factors)."""
    value: float
    unit: str


class RuleParameter(BaseModel):
    name: str
    value: Union[Quantity, float, int, str, bool, list[Any]]
    meaning: str = ""


class Condition(BaseModel):
    """One applicability test over a design-context fact (three-valued: a missing fact is UNKNOWN,
    never false). Facts are named paths such as ``hazard.classification``, ``ceiling.surface``,
    ``ceiling.slope``, ``sprinkler.type``, ``sprinkler.orientation``, ``space.area_sf``."""
    fact: str
    op: Literal["eq", "ne", "in", "not_in", "lt", "le", "gt", "ge", "known", "unknown"]
    value: Any = None


class RuleApplicability(BaseModel):
    all_of: list[Condition] = Field(default_factory=list)       # every condition must hold
    note: str = ""


class RuleException(BaseModel):
    """When ``when`` holds, the rule does not apply (recorded, with the reason)."""
    when: list[Condition]
    reason: str
    source: Optional[RuleSource] = None


class ConstraintTemplate(BaseModel):
    """What the rule asks the deterministic engines to check, in their measurement vocabulary
    (``fireai/rules/constraints.py``). The rule decides WHAT is measured and against which boundary
    semantics; the geometry engine only performs the measurement."""
    key: str                                        # identity for layering, e.g. "sprinkler.max_boundary_distance"
    measurement: str                                # a MEASUREMENTS key
    bound: Literal["max", "min"]
    limit_parameter: str                            # name of the RuleParameter holding the limit
    reference_kinds: list[str] = Field(default_factory=list)   # boundary segment kinds that participate


class Rule(BaseModel):
    rule_id: str
    category: RuleCategory
    title: str                                      # short paraphrase / label — never copied standard text
    parameters: list[RuleParameter] = Field(default_factory=list)
    applicability: RuleApplicability = Field(default_factory=RuleApplicability)
    exceptions: list[RuleException] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)          # rule ids that must also apply
    constraint: Optional[ConstraintTemplate] = None               # None = not (yet) machine-evaluable
    # layering
    replaces: list[str] = Field(default_factory=list)            # rule ids this rule replaces (if permitted)
    replaceable_by: list[RuleLayer] = Field(default_factory=list)  # layers allowed to REPLACE this rule
    # provenance / review
    source: RuleSource
    author: str
    authored_at: str
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_status: ReviewStatus = "draft"
    effective_status: EffectiveStatus = "effective"
    change_reason: str = ""

    def parameter(self, name: str) -> RuleParameter | None:
        return next((p for p in self.parameters if p.name == name), None)


class RuleSet(BaseModel):
    """A versioned, reviewed collection of rules belonging to ONE layer."""
    rule_set_id: str
    version: str
    layer: RuleLayer
    governing_standard: str                         # "NFPA 13" for the base standard; the amended/owning document otherwise
    edition: Optional[str]                          # REQUIRED for real NFPA 13 engineering; never defaulted
    jurisdiction: Optional[str] = None              # for jurisdiction_amendment layers
    content_basis: ContentBasis
    review_status: ReviewStatus = "draft"
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    description: str = ""
    rules: list[Rule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self):
        ids = [r.rule_id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"rule set {self.rule_set_id}: duplicate rule ids")
        synthetic = self.content_basis == "synthetic_test_only"
        for r in self.rules:
            if (r.source.kind == "synthetic_test_only") != synthetic:
                raise ValueError(f"rule {r.rule_id}: source kind does not match the rule set's content basis")
        if synthetic and (SYNTHETIC_MARK not in self.rule_set_id or self.edition not in (None, "TEST_ONLY/NOT_APPLICABLE")):
            raise ValueError(f"synthetic rule sets must carry {SYNTHETIC_MARK} in their id and no real edition")
        return self

    def digest(self) -> str:
        """sha256 of the canonical content: pins the exact rules a result was computed with."""
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()

    def identity(self) -> dict:
        return {"rule_set_id": self.rule_set_id, "version": self.version, "layer": self.layer,
                "governing_standard": self.governing_standard, "edition": self.edition,
                "jurisdiction": self.jurisdiction, "content_basis": self.content_basis,
                "review_status": self.review_status, "digest": self.digest()}
