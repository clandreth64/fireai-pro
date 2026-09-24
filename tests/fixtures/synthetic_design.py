"""TEST ONLY synthetic design inputs for Milestone 2.0 tests.

TEST_ONLY_SYNTHETIC_NFPA13_SHAPED_RULESET is NOT an NFPA 13 rule set. It exercises the SAME
interfaces a future, human-reviewed NFPA 13 rule set will use, with SYNTHETIC numbers chosen so
that known answers can be derived by hand. It carries no NFPA section references. Nothing here may
be used, copied or cited as a fire-protection requirement.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_pipeline
from fireai.contract import build_engineering_input
from fireai.engineering import (CeilingCondition, CeilingRegion, DesignClassification, DesignRequest, Elevation,
                                EngineeringTolerances, InputSource, PlacementSearchSpace, Slope, SprinklerListing)
from fireai.review.store import ReviewStore
from fireai.rules import Condition, ConstraintTemplate, Quantity, Rule, RuleApplicability, RuleParameter, RuleSet, RuleSource

SYN = InputSource(kind="synthetic_test_only", by="test-suite", note="TEST ONLY synthetic input")
SRC = RuleSource(kind="synthetic_test_only", document="TEST_ONLY_SYNTHETIC rule values (not NFPA 13)")
CLASS = "SYNTHETIC-CLASS-A"
BASE_ID = "TEST_ONLY_SYNTHETIC_NFPA13_SHAPED_RULESET"
FULL = {c: {"status": "CONFIRMED"} for c in ("units", "drawing_type", "view_regions", "extents", "walls", "rooms")}


def rule(rid: str, key: str, measurement: str, bound: str, value: float, unit: str = "ft",
         kinds: list[str] | None = None, category: str = "distance_to_boundary", **kw) -> Rule:
    return Rule(rule_id=rid, category=category, title=f"synthetic {key}",
                parameters=[RuleParameter(name="limit", value=Quantity(value=value, unit=unit))],
                applicability=kw.pop("applicability", RuleApplicability(all_of=[
                    Condition(fact="hazard.classification", op="eq", value=CLASS)])),
                constraint=ConstraintTemplate(key=key, measurement=measurement, bound=bound, limit_parameter="limit",
                                              reference_kinds=kinds or []),
                source=SRC, author="test-suite", authored_at="2026-09-24", review_status="reviewed", **kw)


def base_ruleset(*, max_boundary=None, min_wall=None, min_wall_kinds=("wall", "window"), max_spacing=None,
                 min_spacing=None, max_cell=None, max_space=None, version="1", extra=(), boundary_replaceable_by=()):
    rules = []
    if max_boundary is not None:
        rules.append(rule("SYN-B-MAXBOUND", "sprinkler.max_boundary_distance", "boundary_point_to_nearest_sprinkler_max",
                          "max", max_boundary, kinds=["wall", "window"], replaceable_by=list(boundary_replaceable_by)))
    if min_wall is not None:
        rules.append(rule("SYN-B-MINWALL", "sprinkler.min_boundary_distance", "point_to_boundary_min", "min",
                          min_wall, kinds=list(min_wall_kinds)))
    if max_spacing is not None:
        rules.append(rule("SYN-B-MAXSPACING", "sprinkler.max_axis_spacing", "array_axis_spacing", "max",
                          max_spacing, category="spacing"))
    if min_spacing is not None:
        rules.append(rule("SYN-B-MINSPACING", "sprinkler.min_spacing", "pairwise_min_distance", "min",
                          min_spacing, category="spacing"))
    if max_cell is not None:
        rules.append(rule("SYN-B-MAXCELL", "sprinkler.max_cell_area", "nearest_sprinkler_cell_area", "max",
                          max_cell, unit="sf", category="protection_area"))
    if max_space is not None:
        rules.append(rule("SYN-B-MAXSPACE", "sprinkler.max_space_distance", "space_point_to_nearest_sprinkler_max",
                          "max", max_space))
    rules += list(extra)
    return RuleSet(rule_set_id=BASE_ID, version=version, layer="base_standard",
                   governing_standard="TEST_ONLY_SYNTHETIC (NFPA 13-shaped; NOT NFPA 13)", edition="TEST_ONLY/NOT_APPLICABLE",
                   content_basis="synthetic_test_only", review_status="reviewed", rules=rules,
                   description="TEST ONLY synthetic values; not NFPA 13 requirements")


def layer_ruleset(layer: str, rules, rid: str) -> RuleSet:
    return RuleSet(rule_set_id=rid, version="1", layer=layer, governing_standard="TEST_ONLY_SYNTHETIC",
                   edition=None, content_basis="synthetic_test_only", review_status="reviewed", rules=list(rules))


def listing(rules=(), response=None) -> SprinklerListing:
    return SprinklerListing(
        listing_id="TEST_ONLY_SYNTHETIC_LISTING_A", version="1", content_basis="synthetic_test_only",
        manufacturer="TEST_ONLY_SYNTHETIC", model="SYN-1", sprinkler_type="TEST_ONLY_SYNTHETIC_TYPE",
        orientation="TEST_ONLY_ORIENTATION", response_type=response,
        document=RuleSource(kind="synthetic_test_only", document="TEST_ONLY_SYNTHETIC listing document"),
        rules=layer_ruleset("listing", rules, "TEST_ONLY_SYNTHETIC_LISTING_A_RULES"))


def ceiling(space_uid: str, elevation_ft: float = 9.0) -> CeilingCondition:
    return CeilingCondition(
        uid="TEST_ONLY_SYNTHETIC_CEILING", space_uid=space_uid,
        regions=[CeilingRegion(uid="TEST_ONLY_SYNTHETIC_CEILING_R1", surface="flat",
                               slope=Slope(status="known", value_deg=0.0, source=SYN),
                               elevation=Elevation(status="known", value_ft=elevation_ft,
                                                   datum="finished floor of the space", source=SYN), source=SYN)],
        obstructions_statement="none_present", statement_source=SYN)


def classification() -> DesignClassification:
    return DesignClassification(scheme="TEST_ONLY_SYNTHETIC_SCHEME", value=CLASS, reason="test fixture", source=SYN)


def tolerances(length=1e-9, area=1e-9) -> EngineeringTolerances:
    return EngineeringTolerances(length_ft=length, area_sf=area, source=SYN)


def search(step=1.0, max_n=1, max_axis=None) -> PlacementSearchSpace:
    return PlacementSearchSpace(lattice_step_ft=step, max_sprinklers=max_n, max_per_axis=max_axis or max_n, source=SYN)


def verified_package(tmp: Path, path: Path):
    """Generated drawing → real pipeline → (test-only) verification in a throw-away store → v3 package."""
    store = ReviewStore(tmp / "r")
    m = run_pipeline(path, tmp, review_store=store).model
    r = next(r for r in m.view_regions if r["significant"])
    if r["view_type"] != "FLOOR_PLAN":
        store.add_correction(m, "view_type", {"region_uid": r["uid"], "view_type": "FLOOR_PLAN"}, "Owner")
        m = run_pipeline(path, tmp, review_store=store).model
    store.record_verification(m, "Owner", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}),
                              [next(r["uid"] for r in m.view_regions if r["significant"])])
    return build_engineering_input(m, store)


def request(package, *, rule_sets, space_uid=None, lst="default", ceil="default", cls="default", tol="default",
            srch=None, mode="synthetic_test", jurisdiction=None, system=None) -> DesignRequest:
    space_uid = space_uid or package.semantic_spaces[0].uid
    return DesignRequest(package=package, space_uid=space_uid, mode=mode, rule_sets=list(rule_sets),
                         jurisdiction=jurisdiction, listing=listing() if lst == "default" else lst,
                         ceiling=ceiling(space_uid) if ceil == "default" else ceil,
                         classification=classification() if cls == "default" else cls,
                         tolerances=tolerances() if tol == "default" else tol, search=srch or search(), system=system,
                         requested_by="test-suite")


def room_positions(result, t: float = 0.5) -> list[list[tuple[float, float]]]:
    """Valid layouts as sorted room coordinates (LOCAL − wall thickness), rounded for comparison."""
    return [sorted((round(p.position.x - t, 9), round(p.position.y - t, 9)) for p in lay.placements)
            for lay in result.valid_layouts]


def dumps(result) -> str:
    return json.dumps(result.model_dump(mode="json", by_alias=True), sort_keys=True)
