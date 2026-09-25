"""Registered rule-set identities and development envelopes (data, not code paths).

Editions are DATA. Nothing in the geometry / design engines refers to an edition: a base rule set
names its edition, and the envelope registry says which conditions FireAI supports for it. Adding
NFPA 13-2022 later means registering its identity (and, when ready, an envelope) and entering its
reviewed rules — no placement-code change.

The NFPA 13-2025 identity is created EMPTY and DRAFT: it carries no requirement, cannot be
approved empty, and makes real engineering refuse until human-reviewed rules are entered.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from fireai.rules.model import RuleSet


class RuleSetIdentity(BaseModel):
    rule_set_id: str
    layer: str
    governing_standard: str
    edition: str
    first_version: str = "1"
    description: str = ""


class DevelopmentEnvelope(BaseModel):
    """The conditions FireAI supports for real design under one base standard + edition. Values are
    category NAMES the owner chose for the first development slice — not requirements, and never a
    classification of any real space: the matching facts must still be explicit inputs."""
    envelope_id: str
    governing_standard: str
    edition: str
    status: str = "development"
    system_types: list[str]
    storage_conditions: list[str]
    classifications: list[tuple[str, str]]          # (scheme, value)
    sprinkler_types: list[str]
    orientations: list[str]
    ceiling_surfaces: list[str]
    ceiling_constructions: list[str]
    obstruction_statements: list[str]
    # M2.2A.1 (None / False = not a condition of this envelope)
    design_methods: Optional[list[str]] = None                        # system.design_method
    construction_classifications: Optional[list[tuple[str, str]]] = None   # (scheme, value), per ceiling region
    small_room_statuses: Optional[list[str]] = None                   # allowed explicit eligibility statuses
    requires_branch_line_orientation: bool = False
    requires_orthogonal_geometry: bool = False                        # straight, frame-aligned walls only
    requires_horizontal_ceiling: bool = False                         # explicit slope 0 and known elevation
    search_families: Optional[list[str]] = None
    not_included: list[str] = Field(default_factory=list)             # documented exclusions (refused)
    space_scope: str = "one simple known space at a time"
    note: str = ""
    approved_by: str = "owner"
    approved_at: str = ""


NFPA13_2025_BASE = RuleSetIdentity(
    rule_set_id="NFPA13-2025-BASE", layer="base_standard", governing_standard="NFPA 13", edition="2025",
    description="NFPA 13, 2025 edition — base rule set. Rules are entered only by qualified people from "
                "lawfully accessed source material and approved by a second person.")

NFPA13_2025_FIRST_ENVELOPE = DevelopmentEnvelope(
    envelope_id="NFPA13-2025-DEV-ENVELOPE-1", governing_standard="NFPA 13", edition="2025",
    system_types=["wet_pipe"], storage_conditions=["non_storage"],
    classifications=[("NFPA 13 occupancy hazard classification", "Light Hazard")],
    sprinkler_types=["standard_spray"], orientations=["pendent"],
    ceiling_surfaces=["flat"], ceiling_constructions=["smooth_unobstructed"],
    obstruction_statements=["none_present"],
    note="First development envelope (owner decision 2026-09-24). Defines what FireAI will ATTEMPT to support "
         "first; it is not permission to classify any real space and carries no NFPA requirement.",
    approved_at="2026-09-24")

# M2.2A: NFPA 13-2019 — a separate identity (never a copy of 2025). EMPTY + DRAFT. Its development
# envelope (M2.2A.1, below) does not approve it.
NFPA13_2019_BASE = RuleSetIdentity(
    rule_set_id="NFPA13-2019-BASE", layer="base_standard", governing_standard="NFPA 13", edition="2019",
    description="NFPA 13, 2019 edition - base rule set. Rules are entered only by qualified people from "
                "lawfully accessed source material and approved by a second person.")

# M2.2A.1: owner-registered SUPPORTED DEVELOPMENT ENVELOPE for NFPA 13-2019. It is NOT an approved rule
# set, holds no rule value, and does not approve NFPA13-2019-BASE; it only states which explicit input
# combination FireAI will attempt under 2019. Everything outside it refuses.
NFPA13_2019_CONSTRUCTION_SCHEME = "NFPA 13 ceiling construction classification"
NFPA13_2019_FIRST_ENVELOPE = DevelopmentEnvelope(
    envelope_id="NFPA13-2019-DEV-ENVELOPE-1", governing_standard="NFPA 13", edition="2019",
    system_types=["wet_pipe"], storage_conditions=["non_storage"],
    design_methods=["hydraulically_calculated"],
    classifications=[("NFPA 13 occupancy hazard classification", "Light Hazard")],
    sprinkler_types=["standard_spray"], orientations=["pendent"],
    ceiling_surfaces=["flat"], ceiling_constructions=["smooth_unobstructed"],
    construction_classifications=[(NFPA13_2019_CONSTRUCTION_SCHEME, "noncombustible_unobstructed")],
    obstruction_statements=["none_present"],
    small_room_statuses=["not_eligible"],
    requires_branch_line_orientation=True, requires_orthogonal_geometry=True, requires_horizontal_ceiling=True,
    search_families=["room_axis_array/1"],
    not_included=["small-room provisions (eligible or undetermined)", "angled / irregular walls", "storage",
                  "beams, soffits, clouds, known obstructions", "sloped / stepped / multi-plane ceilings",
                  "pipe-schedule design", "other hazards, system types, sprinkler types or orientations"],
    note="Owner decision (M2.2A.1). Scope of what FireAI will ATTEMPT under NFPA 13-2019; not an approved rule set, "
         "not a classification of any real space, and no NFPA requirement.",
    approved_at="2026-09-24")

REGISTERED_IDENTITIES: dict[tuple[str, str], RuleSetIdentity] = {("NFPA 13", "2025"): NFPA13_2025_BASE,
                                                                 ("NFPA 13", "2019"): NFPA13_2019_BASE}
ENVELOPES: dict[tuple[str, str], DevelopmentEnvelope] = {("NFPA 13", "2025"): NFPA13_2025_FIRST_ENVELOPE,
                                                         ("NFPA 13", "2019"): NFPA13_2019_FIRST_ENVELOPE}


def envelope_for(standard: str, edition: Optional[str]) -> Optional[DevelopmentEnvelope]:
    return ENVELOPES.get((standard, edition or ""))


def empty_draft(identity: RuleSetIdentity) -> RuleSet:
    """The identity's first version as an IN-MEMORY EMPTY DRAFT (nothing written): carries no rule."""
    return RuleSet(rule_set_id=identity.rule_set_id, version=identity.first_version, layer=identity.layer,
                   governing_standard=identity.governing_standard, edition=identity.edition,
                   content_basis="authoritative", review_status="draft", rules=[], description=identity.description)


def ensure_rule_set_identity(store, identity: RuleSetIdentity, created_by: str) -> RuleSet:
    """Create the identity's first version as an EMPTY DRAFT if it does not exist. Never overwrites."""
    if identity.first_version in store.versions(identity.rule_set_id):
        return store.get(identity.rule_set_id, identity.first_version)
    return store.create_rule_set(rule_set_id=identity.rule_set_id, version=identity.first_version,
                                 layer=identity.layer, governing_standard=identity.governing_standard,
                                 edition=identity.edition, created_by=created_by, description=identity.description)
