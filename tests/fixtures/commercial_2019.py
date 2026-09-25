"""Generated commercial known-answer SPACE for future authoritative-rule tests (M2.2A.1 → M2.2B).

A simple rectangular commercial space whose every engineering fact is an EXPLICIT, attributed input
matching NFPA13-2019-DEV-ENVELOPE-1. It contains NO NFPA limit and no rule: M2.2B supplies reviewed
rules (and a real approved listing) and hand-worked expected answers against this geometry.

* Geometry: 57.5 ft x 38.0 ft interior, straight orthogonal walls, no openings. The dimensions are
  arbitrary non-round numbers chosen so that no small-room path applies (and not to force any
  sprinkler count).
* Facts (all attributed to the fixture author, ``FIXTURE_SOURCE``; nothing inferred by FireAI):
  Light Hazard (person-decided) · wet pipe · hydraulically calculated · non-storage · smooth / flat /
  horizontal single-plane ceiling at a known elevation · noncombustible unobstructed construction
  classification · no beams / soffits / clouds / obstructions · small-room: NOT eligible (explicit) ·
  branch lines along LOCAL +x (explicit design input).
* The listing slot is a TEST ONLY synthetic standard-spray pendent placeholder until a real, approved
  listing exists; engineering mode therefore still refuses on the listing — by design.
"""

from __future__ import annotations

from pathlib import Path

from fireai.engineering import (CeilingCondition, CeilingRegion, DesignClassification, DesignRequest, Elevation,
                                EngineeringTolerances, InputSource, PlacementSearchSpace, Slope)
from fireai.engineering.inputs import (ConstructionClassification, EligibilityCriterion, EligibilityDecision,
                                       LayoutOrientation, SystemCondition)
from fireai.rules.catalog import NFPA13_2019_CONSTRUCTION_SCHEME
from fixtures import builders as B
from fixtures import synthetic_design as S

WIDTH_FT, DEPTH_FT = 57.5, 38.0
CEILING_ELEVATION_FT = 10.75
DATUM = "finished floor of the space"
FIXTURE_SOURCE = InputSource(kind="human_decision", by="M2.2B fixture author (generated space; not a real project)",
                             note="explicit fixture input; FireAI inferred nothing")


def listing_placeholder():
    """TEST ONLY: a synthetic standard-spray pendent listing (no manufacturer data)."""
    return S.listing().model_copy(update={"sprinkler_type": "standard_spray", "orientation": "pendent"})


def space(tmp: Path):
    d = tmp / "commercial_2019"
    d.mkdir(exist_ok=True)
    return S.verified_package(d, B.make_room(d / "space.dxf", w=WIDTH_FT, h=DEPTH_FT, label="SYN OPEN OFFICE"))


def facts(space_uid: str, src: InputSource = FIXTURE_SOURCE) -> dict:
    ceiling = CeilingCondition(
        uid="FIXTURE_CEILING", space_uid=space_uid,
        regions=[CeilingRegion(uid="FIXTURE_CEILING_R1", surface="flat", construction="smooth_unobstructed",
                               construction_classification=ConstructionClassification(
                                   scheme=NFPA13_2019_CONSTRUCTION_SCHEME, value="noncombustible_unobstructed",
                                   reason="fixture statement", source=src),
                               slope=Slope(status="known", value_deg=0.0, source=src),
                               elevation=Elevation(status="known", value_ft=CEILING_ELEVATION_FT, datum=DATUM, source=src),
                               source=src)],
        obstructions_statement="none_present", statement_source=src)
    return dict(
        classification=DesignClassification(scheme="NFPA 13 occupancy hazard classification", value="Light Hazard",
                                            reason="fixture: stated by the fixture author", source=src),
        system=SystemCondition(system_type="wet_pipe", storage="non_storage", design_method="hydraulically_calculated",
                               reason="fixture statement", source=src),
        ceiling=ceiling,
        eligibility={"small_room": EligibilityDecision(
            status="not_eligible", reason="fixture statement: small-room provisions are not part of this envelope",
            criteria=[EligibilityCriterion(fact="space.area_sf", value=WIDTH_FT * DEPTH_FT)], source=src)},
        orientation=LayoutOrientation(branch_line_direction=(1.0, 0.0), strategy="explicit_design_input", source=src,
                                      note="fixture: branch lines along LOCAL +x"),
        tolerances=EngineeringTolerances(length_ft=1e-6, area_sf=1e-6, source=src),
        search=PlacementSearchSpace(lattice_step_ft=0.5, max_sprinklers=24, max_per_axis=6, source=src),
    )


def request(pkg, rule_sets, *, mode="engineering", jurisdiction="amendments_not_evaluated", listing=None,
            **overrides) -> DesignRequest:
    f = facts(pkg.semantic_spaces[0].uid)
    f.update(overrides)
    return DesignRequest(package=pkg, space_uid=pkg.semantic_spaces[0].uid, mode=mode, rule_sets=list(rule_sets),
                         jurisdiction=jurisdiction, listing=listing or listing_placeholder(),
                         requested_by="m2.2b fixture", **f)
