"""Deterministic sprinkler placement for ONE verified space (Milestone 2.0).

    DesignRequest ──► validation (every missing / unknown / unsupported input → structured REFUSAL)
                  ──► rules: resolve(rule stack + listing rules, explicit facts) → EngineeringConstraints
                  ──► A. candidate generation   (bounded, deterministic search space)
                  ──► B. constraint evaluation  (deterministic geometry; explicit tolerances)
                  ──► valid layout set (ALL valid layouts in the search space — no optimisation)

The engine decides WHETHER supplied constraints are satisfied; it never decides what NFPA 13 says.
Generation and evaluation are separate functions so future proposers (agents, optimisers) can feed
candidates to the same evaluator without becoming the authority on compliance.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from itertools import combinations, product
from typing import Any, Iterator

from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from fireai.contract import EngineeringInput, space_engineering_blockers
from fireai.engineering.design import (ORDERING_STRATEGY, PLACEMENT_ENGINE_VERSION, RULE_REVIEW_DISCLAIMERS,
                                       RULE_VALIDATION_DISCLAIMERS,
                                       SYNTHETIC_DISCLAIMERS,
                                       CandidateProposal, ConstraintEvaluation, DesignIssue, DesignPoint,
                                       DesignProvenance, EngineeringDesignResult, LayoutEvaluation, ProposalEvaluation,
                                       SprinklerPlacement, ValidLayoutFamily, ValidLayoutSet, ZState)
from fireai.engineering.envelope import envelope_blockers
from fireai.engineering.search import layout_key, search
from fireai.engineering.geometry import (RoomFrame, dist_point_segment, nearest_cells, room_frame,
                                         worst_boundary_point, worst_space_point)
from fireai.engineering.inputs import CeilingCondition, DesignRequest, InputSource, LayoutOrientation
from fireai.engineering.measure import (array_sxl, boundary_uv, ceiling_to_deflector, min_wall_clearance,
                                        misaligned_segments, perpendicular_walls)
from fireai.rules.constraints import (ORIENTATION_MEASUREMENTS, SXL_MEASUREMENTS, VERTICAL_MEASUREMENTS,
                                      WALL_RAY_MEASUREMENTS, WALL_REFERENCE_MEASUREMENTS, EngineeringConstraint)
from fireai.rules.resolve import RuleResolution, resolve

# evaluation order (cheap first); rejected layouts record the FIRST failure in this order
MEASUREMENT_ORDER = ("ceiling_to_deflector_vertical_distance", "point_to_boundary_min", "pairwise_min_distance",
                     "array_axis_spacing", "perpendicular_wall_distance", "boundary_point_to_nearest_sprinkler_max",
                     "space_point_to_nearest_sprinkler_max", "nearest_sprinkler_cell_area",
                     "min_perpendicular_wall_distance", "array_sxl_s_dimension", "array_sxl_l_dimension",
                     "array_sxl_protection_area")
Z_LIMITATION = ("Sprinkler elevation (Z) is not established: no deflector position was supplied, so placements "
                "carry Z UNKNOWN and no vertical rule was evaluated")
M2_LIMITATIONS = (
    "Only a single flat ceiling region with no features and an explicit 'no obstructions' statement is supported",
    "Only the bounded search space was examined (room-axis arrays on the stated lattice); layouts outside it "
    "were not considered",
    "No pipe routing, hydraulics, coordination or fabrication has been performed",
    "Reviewer identities are unauthenticated names (not production-safe)",
)


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode()).hexdigest()


def request_fingerprint(req: DesignRequest) -> str:
    """Everything the result depends on (attribution such as requested_by excluded)."""
    p = req.package
    return _sha({"engine": PLACEMENT_ENGINE_VERSION, "ordering": ORDERING_STRATEGY,
                 "package": [p.contract_version, p.content_fingerprint, p.verification_fingerprint],
                 "space": req.space_uid, "mode": req.mode, "jurisdiction": req.jurisdiction,
                 "rule_sets": sorted(s.digest() for s in req.rule_sets),
                 "listing": req.listing.model_dump(mode="json") if req.listing else None,
                 "ceiling": req.ceiling.model_dump(mode="json") if req.ceiling else None,
                 "classification": req.classification.model_dump(mode="json") if req.classification else None,
                 "tolerances": req.tolerances.model_dump(mode="json") if req.tolerances else None,
                 "search": req.search.model_dump(mode="json") if req.search else None,
                 # M2.2A: every input that can change a result is fingerprinted
                 "system": req.system.model_dump(mode="json") if req.system else None,
                 "eligibility": {k: v.model_dump(mode="json") for k, v in sorted(req.eligibility.items())},
                 "deflector": req.deflector.model_dump(mode="json") if req.deflector else None,
                 "orientation": req.orientation.model_dump(mode="json") if req.orientation else None,
                 "installation": req.installation.model_dump(mode="json") if req.installation else None})


def _uid(ns: uuid.UUID, key: str) -> str:
    return str(uuid.uuid5(ns, key))


def m2_ceiling_support(c: CeilingCondition) -> list[tuple[str, str]]:
    """(condition, reason) for every way the ceiling is outside what the engine supports. The schema
    is broader; nothing outside the supported condition is approximated as flat."""
    out = []
    if len(c.regions) != 1:
        elevs = {r.elevation.value_ft for r in c.regions if r.elevation.status == "known"}
        cond = "elevation_change" if len(elevs) > 1 else "multiple_regions" if c.regions else "no_ceiling_region"
        out.append((cond, f"{len(c.regions)} ceiling regions (exactly one covering the whole space is supported)"))
    for r in c.regions:
        if r.extent != "whole_space":
            out.append(("partial_region", f"ceiling region {r.uid} does not cover the whole space"))
        if r.surface != "flat":
            out.append(("surface", f"ceiling region {r.uid} surface is {r.surface!r} (only 'flat' is supported)"))
        if r.slope.status != "known":
            out.append(("slope_unknown", f"ceiling region {r.uid} slope is unknown"))
        elif (r.slope.value_deg or 0.0) != 0.0:
            out.append(("slope", f"ceiling region {r.uid} is sloped ({r.slope.value_deg} deg)"))
        if r.elevation.status != "known":
            out.append(("elevation_unknown", f"ceiling region {r.uid} elevation is unknown"))
        if r.construction == "obstructed":
            out.append(("construction_obstructed", f"ceiling region {r.uid} construction is obstructed"))
    for f in c.features:
        out.append((f"feature:{f.kind}", f"ceiling feature {f.uid} ({f.kind}) is not supported"))
    if c.obstructions_statement != "none_present":
        out.append(("obstructions_" + c.obstructions_statement,
                    f"obstructions statement is {c.obstructions_statement!r} (an explicit 'none_present' is required)"))
    return out


def design_facts(req: DesignRequest, area_sf: float | None) -> dict[str, Any]:
    """Explicit facts rules may test. Missing inputs are simply absent (→ UNKNOWN), never defaulted."""
    f: dict[str, Any] = {}
    if area_sf is not None:
        f["space.area_sf"] = area_sf
    if req.classification:
        f["hazard.scheme"] = req.classification.scheme
        f["hazard.classification"] = req.classification.value
    if req.system:
        f["system.type"] = req.system.system_type
        f["system.storage"] = req.system.storage
        if req.system.design_method != "unknown":
            f["system.design_method"] = req.system.design_method
    if req.installation is not None and req.installation.kind != "unknown":
        f["installation.context"] = req.installation.kind
    for name, dec in req.eligibility.items():       # "unknown" is a value that never equals "eligible"
        f[f"space.eligibility.{name}"] = dec.status
    if req.listing:
        f["sprinkler.type"] = req.listing.sprinkler_type
        f["sprinkler.orientation"] = req.listing.orientation
        if req.listing.response_type:
            f["sprinkler.response"] = req.listing.response_type
    if req.ceiling and len(req.ceiling.regions) == 1:
        r = req.ceiling.regions[0]
        f["ceiling.surface"] = r.surface
        f["ceiling.construction"] = r.construction                  # geometric / observed condition
        if r.construction_classification is not None:              # the standard's classification (separate)
            f["ceiling.construction_classification.scheme"] = r.construction_classification.scheme
            f["ceiling.construction_classification"] = r.construction_classification.value
        if r.slope.status == "known":
            f["ceiling.slope_deg"] = r.slope.value_deg
        if r.elevation.status == "known":
            f["ceiling.elevation_ft"] = r.elevation.value_ft
        f["ceiling.obstructions"] = req.ceiling.obstructions_statement
    return f


def _sources(req: DesignRequest) -> list[InputSource]:
    out = []
    for obj in (req.classification, req.tolerances, req.search, req.system):
        if obj is not None:
            out.append(obj.source)
    if req.ceiling:
        out += [s for s in [req.ceiling.statement_source] + [r.source for r in req.ceiling.regions]
                + [r.elevation.source for r in req.ceiling.regions]
                + [r.construction_classification.source for r in req.ceiling.regions if r.construction_classification]
                if s is not None]
    out += [d.source for d in req.eligibility.values()]
    if req.installation is not None:
        out.append(req.installation.source)
    if req.deflector and req.deflector.elevation.source:
        out.append(req.deflector.elevation.source)
    return out


class _State:
    """Validated request + resolved constraints + geometry, ready for generation/evaluation."""

    def __init__(self, req: DesignRequest, space, region, resolution: RuleResolution):
        self.req, self.space, self.region, self.resolution = req, space, region, resolution
        self.fp = request_fingerprint(req)
        self.ns = uuid.UUID(self.fp[:32])
        self.polygon = [tuple(p) for p in region.polygon_local_ft]
        self.poly = Polygon(self.polygon)
        self.prepared = prep(self.poly)
        self.frame: RoomFrame = room_frame(self.polygon)
        self.step = req.search.lattice_step_ft
        self.ku = self._count(self.frame.extent_u)
        self.kv = self._count(self.frame.extent_v)
        self.excluded = [prep(Polygon(r)) for r in req.search.excluded_regions_local_ft]
        self.segments = [s for ring in region.boundary.rings for s in ring.segments]
        self.bsegs_uv = boundary_uv(self.frame, self.segments)
        self.facts = design_facts(req, region.area_sf)
        self.s_axis = orientation_axis(self.frame, req.orientation, req.tolerances.length_ft) \
            if req.orientation is not None and req.orientation.frame == "LOCAL" else None
        order = {m: i for i, m in enumerate(MEASUREMENT_ORDER)}
        self.constraints = sorted(resolution.constraints, key=lambda c: (order.get(c.measurement, 99), c.key))
        self._point_cache: dict[tuple[int, int], dict] = {}

    def _count(self, extent: float) -> int:
        return max(0, math.ceil(extent / self.step - 1e-9) - 1)

    def point(self, i: int, j: int) -> dict:
        c = self._point_cache.get((i, j))
        if c is None:
            u, v = i * self.step, j * self.step
            x, y = self.frame.to_xy(u, v)
            pt = Point(x, y)
            c = {"uv": (u, v), "xy": (x, y), "inside": self.prepared.contains(pt),
                 "excluded": any(e.intersects(pt) for e in self.excluded), "bmin": {}}
            self._point_cache[(i, j)] = c
        return c

    def segs(self, kinds: list[str]) -> list[tuple[tuple, tuple]]:
        return [(tuple(s.start_local_ft), tuple(s.end_local_ft)) for s in self.segments if s.kind in kinds]


def _validate(req: DesignRequest) -> tuple[list[DesignIssue], Any, Any, RuleResolution | None]:
    issues: list[DesignIssue] = []
    pkg: EngineeringInput = req.package
    space = region = None
    if pkg.contract_version != "engineering_input/3":
        issues.append(DesignIssue(code="CONTRACT_VERSION", message=f"requires engineering_input/3, got {pkg.contract_version}"))
    for b in space_engineering_blockers(pkg, req.space_uid):
        issues.append(DesignIssue(code="SPACE_NOT_ENGINEERABLE", message=b))
    sem = {s.uid: s for s in pkg.semantic_spaces}
    regions = {s.uid: s for s in pkg.spaces}
    space = sem.get(req.space_uid)
    region = regions.get(space.region_uid) if space else regions.get(req.space_uid)
    if req.listing is None:
        issues.append(DesignIssue(code="MISSING_SPRINKLER_LISTING", message="no sprinkler / listing data supplied"))
    if req.ceiling is None:
        issues.append(DesignIssue(code="MISSING_CEILING_CONDITION", message="no ceiling condition supplied"))
    else:
        if req.ceiling.space_uid not in {req.space_uid, getattr(space, "uid", None), getattr(region, "uid", None)}:
            issues.append(DesignIssue(code="CEILING_FOR_ANOTHER_SPACE", message="the ceiling condition names another space"))
        for cond, why in m2_ceiling_support(req.ceiling):
            issues.append(DesignIssue(code="CEILING_NOT_SUPPORTED_IN_M2_0", message=why, detail={"condition": cond}))
    if req.classification is None:
        issues.append(DesignIssue(code="MISSING_DESIGN_CLASSIFICATION",
                                  message="no hazard / design classification supplied (FireAI never infers it)"))
    if req.tolerances is None:
        issues.append(DesignIssue(code="MISSING_TOLERANCES", message="no explicit engineering tolerances supplied"))
    if req.search is None:
        issues.append(DesignIssue(code="MISSING_SEARCH_SPACE", message="no explicit placement search space supplied"))
    if req.mode in ("rule_validation", "rule_review") and req.listing and \
            req.listing.content_basis != "synthetic_test_only":
        issues.append(DesignIssue(code="RULE_VALIDATION_NEEDS_SYNTHETIC_LISTING",
                                  message="rule_validation is for synthetic listings; use engineering mode with an "
                                          "approved authoritative listing"))
    if req.mode in ("engineering", "rule_validation", "rule_review"):
        if req.mode == "engineering" and req.listing and (req.listing.content_basis != "authoritative"
                                                          or req.listing.review_status != "approved"):
            issues.append(DesignIssue(code="LISTING_NOT_APPROVED_AUTHORITATIVE",
                                      message=f"listing {req.listing.listing_id} is {req.listing.content_basis} / "
                                              f"{req.listing.review_status}"))
        if req.classification and req.classification.source.kind not in ("human_decision", "project_document"):
            issues.append(DesignIssue(code="CLASSIFICATION_NOT_HUMAN_DECIDED",
                                      message="a design classification must be a human decision or project document"))
        if any(s.kind == "synthetic_test_only" for s in _sources(req)):   # (listing data is not an input source)
            issues.append(DesignIssue(code="SYNTHETIC_INPUT_IN_ENGINEERING_MODE",
                                      message="synthetic (TEST ONLY) inputs can never support engineering"))
    elif req.mode == "synthetic_test" and req.listing and req.listing.content_basis != "synthetic_test_only":
        issues.append(DesignIssue(code="MIXED_SYNTHETIC_AND_AUTHORITATIVE_INPUTS",
                                  message="synthetic test runs may only use synthetic listings"))
    if req.mode in ("engineering", "rule_validation", "rule_review"):
        issues.extend(envelope_blockers(req, req.rule_sets))
    area = region.area_sf if region is not None else None
    stack = list(req.rule_sets) + ([req.listing.rules] if req.listing else [])
    res = resolve(stack, design_facts(req, area), req.mode, req.jurisdiction)
    for r in res.refusals:
        issues.append(DesignIssue(code=r.code, message=r.message,
                                  detail={k: v for k, v in (("rule_id", r.rule_id), ("rule_set_id", r.rule_set_id)) if v}))
    if res.status == "resolved" and not res.constraints:
        issues.append(DesignIssue(code="NO_CONSTRAINTS_RESOLVED",
                                  message="no machine-evaluable constraint applies; no design can be declared valid"))
    issues.extend(_measurement_blockers(req, region, res))
    return issues, space, region, res


def _measurement_blockers(req: DesignRequest, region, res: RuleResolution) -> list[DesignIssue]:
    """M2.2A: conditions under which a resolved measurement cannot be made at all -> REFUSE up front."""
    out: list[DesignIssue] = []
    meas = {c.measurement for c in res.constraints}
    facts = design_facts(req, region.area_sf if region is not None else None)
    for c in res.constraints:
        if c.measurement == "fact_requirement" and facts.get(c.fact) is None:
            out.append(DesignIssue(code="FACT_REQUIREMENT_UNKNOWN",
                                   message=f"{c.key} (rule {c.governing_rule_id}) requires fact {c.fact!r}, which was "
                                           "not supplied: UNKNOWN is never a pass",
                                   detail={"fact": c.fact, "rule_id": c.governing_rule_id}))
    if region is not None and req.tolerances is not None and meas & WALL_REFERENCE_MEASUREMENTS:
        segs = [s for ring in region.boundary.rings for s in ring.segments]
        frame = room_frame([tuple(p) for p in region.polygon_local_ft])
        angled = misaligned_segments(boundary_uv(frame, segs), req.tolerances.length_ft)
        if angled:
            out.append(DesignIssue(code="IRREGULAR_BOUNDARY_UNSUPPORTED",
                                   message=f"{len(angled)} boundary segment(s) are angled relative to the array frame; "
                                           "array wall-reference measurements (S x L, perpendicular wall distance) "
                                           "are defined for straight, frame-aligned walls only and are not applied "
                                           "to irregular walls (declared measurement contracts: "
                                           "angled_wall_perpendicular_distance, angled_wall_protected_floor_worst_distance)",
                                   detail={"segments": [{"uid": a.uid, "index": a.index, "kind": a.kind} for a in angled]}))
        unknown = [s for s in segs if s.kind == "unknown"]
        if unknown:
            out.append(DesignIssue(code="BOUNDARY_KIND_UNKNOWN",
                                   message=f"{len(unknown)} boundary segment(s) are of unknown kind; a wall-reference "
                                           "measurement cannot tell whether they are walls",
                                   detail={"segments": [s.uid for s in unknown]}))
    if meas & ORIENTATION_MEASUREMENTS:
        o = req.orientation
        if o is None:
            out.append(DesignIssue(code="LAYOUT_ORIENTATION_MISSING",
                                   message="an S x L rule applies but no branch-line orientation (LayoutOrientation) "
                                           "was supplied; S is never taken from the room's long axis"))
        elif o.frame != "LOCAL":
            out.append(DesignIssue(code="WRONG_COORDINATE_FRAME",
                                   message=f"branch-line orientation is in frame {o.frame}; the evaluator works in LOCAL"))
        elif region is not None and req.tolerances is not None and orientation_axis(
                room_frame([tuple(p) for p in region.polygon_local_ft]), o, req.tolerances.length_ft) is None:
            out.append(DesignIssue(code="ORIENTATION_NOT_ALIGNED_WITH_ARRAY_FRAME",
                                   message="the branch-line direction is parallel to neither axis of the array frame "
                                           "(search family room_axis_array/1 generates frame-aligned arrays only); "
                                           "S x L is not evaluated for a direction the arrays do not follow",
                                   detail={"branch_line_direction": list(o.branch_line_direction)}))
    if meas & VERTICAL_MEASUREMENTS:
        creg = req.ceiling.regions[0] if req.ceiling and len(req.ceiling.regions) == 1 else None
        if creg is None or creg.elevation.status != "known":
            out.append(DesignIssue(code="VERTICAL_CEILING_ELEVATION_UNKNOWN",
                                   message="a vertical rule applies but the ceiling elevation is not known"))
        if req.deflector is None or req.deflector.elevation.status != "known":
            out.append(DesignIssue(code="SPRINKLER_Z_UNKNOWN",
                                   message="a vertical rule applies but no sprinkler deflector elevation was supplied; "
                                           "FireAI never invents Z"))
        elif req.deflector.frame != "LOCAL":
            out.append(DesignIssue(code="WRONG_COORDINATE_FRAME",
                                   message=f"deflector position is in frame {req.deflector.frame}; the evaluator works "
                                           "in LOCAL (no silent transformation)"))
        elif creg is not None and creg.elevation.status == "known" \
                and creg.elevation.datum != req.deflector.elevation.datum:
            out.append(DesignIssue(code="VERTICAL_DATUM_MISMATCH",
                                   message=f"ceiling elevation datum {creg.elevation.datum!r} differs from the deflector "
                                           f"datum {req.deflector.elevation.datum!r}; no silent conversion"))
    return out


# ── A. candidate generation ──────────────────────────────────────────────────

def _axis_sets(k: int, max_n: int) -> list[tuple[int, ...]]:
    """Uniformly spaced index sets on lattice 1..k: n=1 any index; n>=2 (start, spacing, n)."""
    out = [(i,) for i in range(1, k + 1)]
    for n in range(2, max_n + 1):
        for ds in range(1, k):
            for s0 in range(1, k - (n - 1) * ds + 1):
                out.append(tuple(s0 + t * ds for t in range(n)))
    return out


def generate_candidates(st: _State) -> Iterator[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Deterministic, bounded: every (u-set × v-set) array with at most max_sprinklers sprinklers,
    ordered by sprinkler count, then by the (v, u) lattice positions (bottom-to-top, left-to-right)."""
    s = st.req.search
    us, vs = _axis_sets(st.ku, s.max_per_axis), _axis_sets(st.kv, s.max_per_axis)
    cands = [(u, v) for u, v in product(us, vs) if len(u) * len(v) <= s.max_sprinklers]
    cands.sort(key=lambda c: (len(c[0]) * len(c[1]), sorted((j, i) for i in c[0] for j in c[1])))
    yield from cands


# ── B. constraint evaluation ─────────────────────────────────────────────────

def _eval_constraint(st: _State, c: EngineeringConstraint, cells: list[dict], arr, grid=None) -> ConstraintEvaluation:
    """``arr``: (spacing_u, spacing_v) of a uniform room-aligned array, else None. ``grid``: (us, vs) room-
    frame coordinates of a complete rectangular grid (uniform or not), else None."""
    tol = st.req.tolerances.length_ft if c.unit == "ft" else st.req.tolerances.area_sf
    pts = [p["xy"] for p in cells]
    measured, subject, note = None, None, ""
    worst = max if c.bound == "max" else min
    if c.measurement == "point_to_boundary_min":
        segs = st.segs(c.reference_kinds)
        if segs:
            per = []
            for idx, p in enumerate(cells):
                key = (c.key,)
                if key not in p["bmin"]:
                    p["bmin"][key] = min(dist_point_segment(p["xy"], a, b) for a, b in segs)
                per.append((p["bmin"][key], idx))
            measured, idx = worst(per)
            subject = {"sprinkler_index": idx}
        else:
            note = f"no boundary segments of kinds {c.reference_kinds} in this space"
    elif c.measurement == "pairwise_min_distance":
        if len(pts) >= 2:
            measured, pair = min((math.dist(pts[a], pts[b]), (a, b)) for a, b in combinations(range(len(pts)), 2))
            subject = {"sprinkler_pair": list(pair)}
        else:
            note = "fewer than two sprinklers"
    elif c.measurement == "array_axis_spacing":
        if arr is None:
            return _not_evaluable(st, c, "the layout is not a room-aligned rectangular array, so array axis spacing "
                                         "is undefined; it is not assumed to pass")
        sp = [(v, a) for v, a in ((arr[0], "u"), (arr[1], "v")) if v is not None]
        if sp:
            measured, axis = worst(sp)
            subject = {"axis": axis}
        else:
            note = "no adjacent sprinklers along either axis"
    elif c.measurement == "boundary_point_to_nearest_sprinkler_max":
        segs = st.segs(c.reference_kinds)
        if segs:
            measured, where = worst_boundary_point(segs, pts)
            subject = {"boundary_point_local_ft": list(where)}
        else:
            note = f"no boundary segments of kinds {c.reference_kinds} in this space"
    elif c.measurement == "space_point_to_nearest_sprinkler_max":
        measured, where = worst_space_point(st.polygon, pts)
        subject = {"space_point_local_ft": list(where)}
    elif c.measurement == "nearest_sprinkler_cell_area":
        areas = [(g.area, i) for i, g in enumerate(nearest_cells(st.polygon, pts))]
        measured, idx = worst(areas)
        subject = {"sprinkler_index": idx}
    elif c.measurement == "fact_requirement":
        value = st.facts.get(c.fact)
        if value is None:
            return _not_evaluable(st, c, f"fact {c.fact!r} is unknown")
        ok = value in (c.allowed_values or [])
        return ConstraintEvaluation(
            constraint_key=c.key, measurement=c.measurement, bound=c.bound, limit=None, unit=c.unit, tolerance=0.0,
            passed=ok, outcome="pass" if ok else "fail", fact=c.fact, fact_value=value,
            allowed_values=list(c.allowed_values or []),
            worst_subject={"fact": c.fact, "value": value, "allowed": list(c.allowed_values or [])},
            governing_rule_id=c.governing_rule_id, contributing_rule_ids=[x.rule_id for x in c.contributions],
            references=sorted({x.source_document + (f" {x.source_reference}" if x.source_reference else "")
                               for x in c.contributions}),
            note=f"{c.fact} = {value!r} is {'' if ok else 'NOT '}one of {c.allowed_values}")
    elif c.measurement == "min_perpendicular_wall_distance":
        m = min_wall_clearance(st.bsegs_uv, [tuple(p["uv"]) for p in cells], c.reference_kinds,
                               st.req.tolerances.length_ft, worst)
        if m.not_evaluable:
            return _not_evaluable(st, c, m.not_evaluable, m.subject)
        measured, subject = m.value, m.subject
    elif c.measurement in WALL_RAY_MEASUREMENTS or c.measurement in VERTICAL_MEASUREMENTS:
        m = measure_special(st, c, grid)
        if m.not_evaluable:
            return _not_evaluable(st, c, m.not_evaluable, m.subject)
        measured, subject = m.value, m.subject
        if measured is None:
            note = "nothing to measure"
    else:  # unreachable: the resolver refuses unknown measurements
        raise ValueError(c.measurement)
    if measured is None:
        passed, margin = True, None
        note = note + " — nothing to measure; the constraint is satisfied vacuously"
    elif c.bound == "max":
        passed, margin = measured <= c.limit + tol, c.limit - measured
    else:
        passed, margin = measured >= c.limit - tol, measured - c.limit
    return ConstraintEvaluation(
        constraint_key=c.key, measurement=c.measurement, bound=c.bound, limit=c.limit, unit=c.unit, tolerance=tol,
        measured=measured, margin=margin, passed=passed, outcome="pass" if passed else "fail",
        worst_subject=subject, reference_kinds=list(c.reference_kinds),
        governing_rule_id=c.governing_rule_id, contributing_rule_ids=[x.rule_id for x in c.contributions],
        references=sorted({f"{x.source_document}" + (f" {x.source_reference}" if x.source_reference else "")
                           for x in c.contributions}), note=note)


def measure_special(st: _State, c: EngineeringConstraint, grid):
    """M2.2A measurements (shared by evaluation, the pruned search and proposals)."""
    from fireai.engineering.measure import Measured
    worst = max if c.bound == "max" else min
    if c.measurement in VERTICAL_MEASUREMENTS:
        creg = st.req.ceiling.regions[0]
        return ceiling_to_deflector(creg.elevation, st.req.deflector)
    if grid is None:
        return Measured(None, not_evaluable="the layout is not a complete room-aligned rectangular grid, so array "
                                            "directions are undefined; it is not assumed to pass")
    us, vs = grid
    if c.measurement in SXL_MEASUREMENTS:
        if st.s_axis is None:
            return Measured(None, not_evaluable="no usable branch-line orientation: S and L are undefined")
        o = st.req.orientation
        return array_sxl(st.bsegs_uv, us, vs, c.reference_kinds, st.req.tolerances.length_ft, worst, st.s_axis,
                         SXL_MEASUREMENTS[c.measurement],
                         {"branch_line_axis": st.s_axis, "branch_line_direction_local": list(o.branch_line_direction),
                          "strategy": o.strategy, "version": o.version})
    return perpendicular_walls(st.bsegs_uv, us, vs, c.reference_kinds, st.req.tolerances.length_ft, worst)


def orientation_axis(frame: RoomFrame, o: LayoutOrientation | None, tol_ft: float) -> str | None:
    """'u' / 'v': the array-frame axis PARALLEL to the branch lines, or None (missing or not aligned). The
    angular deviation is accepted only if it moves a point by at most the explicit length tolerance
    across the room's extent."""
    if o is None:
        return None
    dx, dy = o.branch_line_direction
    extent = max(frame.extent_u, frame.extent_v, 1.0)
    if abs(dx * frame.uy - dy * frame.ux) * extent <= tol_ft:
        return "u"
    if abs(dx * frame.ux + dy * frame.uy) * extent <= tol_ft:
        return "v"
    return None


def default_orientation(region, source: InputSource) -> LayoutOrientation | None:
    """ROOM-LONG-AXIS-DEFAULT/1: a named, versioned candidate-generation STRATEGY (not an engineering
    definition of S) — branch lines along the room frame's long axis. None when the long axis is
    ambiguous (equal extents): the caller must then choose explicitly."""
    fr = room_frame([tuple(p) for p in region.polygon_local_ft])
    if abs(fr.extent_u - fr.extent_v) <= 1e-9 * max(fr.extent_u, fr.extent_v, 1.0):
        return None
    return LayoutOrientation(branch_line_direction=(fr.ux, fr.uy), strategy="ROOM-LONG-AXIS-DEFAULT/1", source=source,
                             note="default candidate-generation strategy before routing exists; replaceable by "
                                  "routing / optimisation / a person")


def _not_evaluable(st: _State, c: EngineeringConstraint, why: str, subject=None) -> ConstraintEvaluation:
    tol = st.req.tolerances.length_ft if c.unit == "ft" else st.req.tolerances.area_sf
    return ConstraintEvaluation(
        constraint_key=c.key, measurement=c.measurement, bound=c.bound, limit=c.limit, unit=c.unit, tolerance=tol,
        passed=False, outcome="not_evaluable", reference_kinds=list(c.reference_kinds), worst_subject=subject,
        governing_rule_id=c.governing_rule_id, contributing_rule_ids=[x.rule_id for x in c.contributions],
        references=sorted({x.source_document + (f" {x.source_reference}" if x.source_reference else "")
                           for x in c.contributions}), note=why)


def _spacings(st: _State, iu, iv) -> tuple:
    return ((iu[1] - iu[0]) * st.step if len(iu) > 1 else None, (iv[1] - iv[0]) * st.step if len(iv) > 1 else None)


def _grid_of(st: _State, iu, iv) -> tuple[list[float], list[float]]:
    return [i * st.step for i in iu], [j * st.step for j in iv]


def _cells(st: _State, iu, iv) -> list[dict]:
    pts = [st.point(i, j) for j in iv for i in iu]
    return sorted(pts, key=lambda p: (p["uv"][1], p["uv"][0]))


def _layout_uid(st: _State, cells: list[dict]) -> str:
    return _uid(st.ns, "layout|" + "|".join(f"{p['xy'][0]:.9f},{p['xy'][1]:.9f}" for p in cells))


def evaluate_layout(st: _State, iu, iv, full: bool) -> LayoutEvaluation:
    cells = _cells(st, iu, iv)
    luid = _layout_uid(st, cells)
    arr = {"u_indices": list(iu), "v_indices": list(iv), "n_u": len(iu), "n_v": len(iv),
           "spacing_u_ft": (iu[1] - iu[0]) * st.step if len(iu) > 1 else None,
           "spacing_v_ft": (iv[1] - iv[0]) * st.step if len(iv) > 1 else None}
    if st.req.orientation is not None:
        arr["orientation"] = {"branch_line_axis": st.s_axis,
                              "branch_line_direction_local": list(st.req.orientation.branch_line_direction),
                              "strategy": st.req.orientation.strategy, "version": st.req.orientation.version}
    evals, first = [], None
    if not all(p["inside"] for p in cells):
        first = "structural:outside_space"
    elif any(p["excluded"] for p in cells):
        first = "structural:excluded_region"
    for c in st.constraints:          # fast path stops at the first failure; full explains everything
        if first is not None and not full:
            break
        e = _eval_constraint(st, c, cells, _spacings(st, iu, iv), _grid_of(st, iu, iv))
        evals.append(e)
        if not e.passed and first is None:
            first = c.key
    ev = LayoutEvaluation(layout_uid=luid, sprinkler_count=len(cells), array=arr, valid=first is None,
                          evaluations=evals, first_failure=first)
    if ev.valid or full:
        ev.placements = _placements(st, luid, cells)
    return ev


def _placements(st: _State, luid: str, cells: list[dict]) -> list[SprinklerPlacement]:
    req = st.req
    creg = req.ceiling.regions[0]
    prov = DesignProvenance(
        request_fingerprint=st.fp, package_content_fingerprint=req.package.content_fingerprint,
        package_verification_fingerprint=req.package.verification_fingerprint, space_uid=req.space_uid,
        rule_sets=st.resolution.rule_sets, listing=req.listing.identity(), ceiling_uid=req.ceiling.uid,
        derived_from=[u for u in (getattr(st.space, "uid", None), st.region.uid, creg.uid) if u])
    d = req.deflector
    if d is not None and d.frame == "LOCAL" and d.elevation.status == "known":
        z = ZState(status="from_input", value_ft=d.elevation.value_ft, datum=d.elevation.datum,
                   reference=f"ceiling_region:{creg.uid}",
                   note="deflector elevation from the explicit design input (DeflectorPosition)")
    else:
        z = ZState(status="unknown", reference=f"ceiling_region:{creg.uid}",
                   note="sprinkler elevation not established: no deflector position was supplied (never invented)")
    return [SprinklerPlacement(uid=_uid(st.ns, f"{luid}|{i}"), layout_uid=luid, index=i,
                               position=DesignPoint(x=p["xy"][0], y=p["xy"][1], z=z, room_uv=p["uv"]),
                               listing_ref=req.listing.identity(), orientation=req.listing.orientation, provenance=prov)
            for i, p in enumerate(cells)]


# ── orchestration ─────────────────────────────────────────────────────────────

def _context(req: DesignRequest, space, region, frame: RoomFrame | None) -> dict:
    p = req.package
    return {"mode": req.mode, "contract_version": p.contract_version, "model_id": p.model_id,
            "schema_version": p.schema_version, "drawing_understanding_engine": p.engine_version,
            "package_content_fingerprint": p.content_fingerprint,
            "package_verification_fingerprint": p.verification_fingerprint,
            "verified_by": p.verified_by, "verified_by_identity": "unauthenticated_name", "verified_at": p.verified_at,
            "regions": [{"uid": r.uid, "id": r.id, "view_type": r.view_type} for r in p.regions],
            "spatial_context": p.spatial_context, "frame": p.frame, "units": p.units, "source_units": p.source_units,
            "z_status": p.z_status,
            "space": {"requested_uid": req.space_uid,
                      "semantic_space": {"uid": space.uid, "id": space.id, "label": space.label} if space else None,
                      "physical_region": ({"uid": region.uid, "id": region.id, "area_sf": region.area_sf,
                                           "boundary_complete": region.boundary.complete,
                                           "length_by_kind_ft": region.boundary.length_by_kind_ft}
                                          if region else None)},
            "room_frame": ({"u_axis": [frame.ux, frame.uy], "origin_uv": [frame.ou, frame.ov],
                            "extent_u_ft": frame.extent_u, "extent_v_ft": frame.extent_v, "strategy": frame.strategy}
                           if frame else None),
            "requested_by": req.requested_by}


def _inputs(req: DesignRequest) -> dict:
    return {"listing": req.listing.identity() if req.listing else None,
            "ceiling": req.ceiling.model_dump(mode="json") if req.ceiling else None,
            "classification": req.classification.model_dump(mode="json") if req.classification else None,
            "tolerances": req.tolerances.model_dump(mode="json") if req.tolerances else None,
            "search": req.search.model_dump(mode="json") if req.search else None,
            "jurisdiction": req.jurisdiction,
            "system": req.system.model_dump(mode="json") if req.system else None,
            "eligibility": {k: v.model_dump(mode="json") for k, v in sorted(req.eligibility.items())},
            "deflector": req.deflector.model_dump(mode="json") if req.deflector else None,
            "orientation": req.orientation.model_dump(mode="json") if req.orientation else None,
            "installation": req.installation.model_dump(mode="json") if req.installation else None}


def _finish(res: EngineeringDesignResult) -> EngineeringDesignResult:
    body = res.model_dump(mode="json", by_alias=True, exclude={"result_fingerprint"})
    res.result_fingerprint = _sha(body)
    return res


def run_design(req: DesignRequest) -> EngineeringDesignResult:
    issues, space, region, res = _validate(req)
    fp = request_fingerprint(req)
    ns = uuid.UUID(fp[:32])
    rules = res.model_dump(mode="json") if res else {}
    if issues:
        frame = room_frame([tuple(p) for p in region.polygon_local_ft]) if region is not None else None
        return _finish(EngineeringDesignResult(
            result_uid=_uid(ns, "result"), status="REFUSED", basis="none", engineering_use="NONE_REFUSED",
            refusals=issues, limitations=list(res.limitations if res else []), context=_context(req, space, region, frame),
            inputs=_inputs(req), rules=rules, request_fingerprint=fp))
    st = _State(req, space, region, res)
    out = search(st)
    explicit = len(out.valid) <= req.search.max_explicit_layouts
    valid = [evaluate_layout(st, iu, iv, full=True) for iu, iv in out.valid] if explicit else []
    rejected_examples = [evaluate_layout(st, iu, iv, full=True) for iu, iv in out.examples]
    synthetic = res.basis == "synthetic_test_only"
    validation = res.basis in ("authoritative_rules_synthetic_listing", "rules_under_review")
    fams: dict[tuple, list] = {}
    for iu, iv in out.valid:
        key = (len(iu), (iu[1] - iu[0]) if len(iu) > 1 else 0, len(iv), (iv[1] - iv[0]) if len(iv) > 1 else 0)
        fams.setdefault(key, []).append((iu[0], iv[0]))
    frame = st.frame
    valid_set = ValidLayoutSet(
        count=len(out.valid), lattice_step_ft=st.step,
        room_frame={"u_axis": [frame.ux, frame.uy], "origin_uv": [frame.ou, frame.ov], "extent_u_ft": frame.extent_u,
                    "extent_v_ft": frame.extent_v, "strategy": frame.strategy},
        families=[ValidLayoutFamily(n_u=k[0], ds_u=k[1], n_v=k[2], ds_v=k[3], offsets=v) for k, v in sorted(fams.items())],
        explicit_layouts_included=explicit,
        note="" if explicit else f"{len(out.valid)} valid layouts exceed max_explicit_layouts="
                                 f"{req.search.max_explicit_layouts}: enumerate on demand (iter_valid_layouts)")
    return _finish(EngineeringDesignResult(
        result_uid=_uid(ns, "result"), status="VALID_LAYOUTS_FOUND" if out.valid else "NO_VALID_LAYOUT_IN_SEARCH_SPACE",
        basis=res.basis,
        engineering_use="NOT_FOR_ENGINEERING_USE" if synthetic or validation else "REQUIRES_QUALIFIED_HUMAN_APPROVAL",
        disclaimers=(list(SYNTHETIC_DISCLAIMERS) if synthetic else list(RULE_VALIDATION_DISCLAIMERS)
                     if res.basis == "authoritative_rules_synthetic_listing" else list(RULE_REVIEW_DISCLAIMERS) if res.basis == "rules_under_review" else []),
        limitations=list(res.limitations) + ([] if _z_known(req) else [Z_LIMITATION]) + list(M2_LIMITATIONS),
        context=_context(req, space, region, st.frame),
        inputs=_inputs(req), rules=rules,
        search={"family": req.search.family, "algorithm": "exact_pruned/1", "lattice_step_ft": st.step,
                "lattice_u": st.ku, "lattice_v": st.kv, "candidates_generated": out.search_space_size,
                "valid": len(out.valid), "rejected": out.search_space_size - len(out.valid),
                "rejected_by_first_failure": out.rejected_by, "pruned_by_stage": out.pruned_by_stage,
                "fully_evaluated": out.fully_evaluated,
                "evaluation_order": "pipeline axis -> count -> points (structural, point constraints) -> pair -> "
                                    "cover -> full; a rejection is attributed to the first stage that proves it"},
        valid_layouts=valid, rejected_examples=rejected_examples, valid_set=valid_set, request_fingerprint=fp))


def _z_known(req: DesignRequest) -> bool:
    return bool(req.deflector and req.deflector.frame == "LOCAL" and req.deflector.elevation.status == "known")


def explain_layout(req: DesignRequest, layout_uid: str) -> LayoutEvaluation | None:
    """Full, re-derived explanation of any candidate in the search space (valid or rejected)."""
    issues, space, region, res = _validate(req)
    if issues:
        return None
    st = _State(req, space, region, res)
    for iu, iv in generate_candidates(st):
        if _layout_uid(st, _cells(st, iu, iv)) == layout_uid:
            return evaluate_layout(st, iu, iv, full=True)
    return None


# ── M2.1: reference search, compact-set enumeration, agent proposals ─────────

def reference_search(req: DesignRequest) -> tuple[list[tuple], int]:
    """The M2.0 brute force (every candidate fully checked). Kept as the equivalence reference for the
    pruned search; returns (valid (u-set, v-set) in layout order, candidates examined)."""
    issues, space, region, res = _validate(req)
    if issues:
        raise ValueError("reference_search needs a valid request")
    st = _State(req, space, region, res)
    valid, n = [], 0
    for iu, iv in generate_candidates(st):
        n += 1
        if evaluate_layout(st, iu, iv, full=False).valid:
            valid.append((iu, iv))
    return valid, n


def iter_valid_layouts(result: EngineeringDesignResult) -> Iterator[list[tuple[float, float]]]:
    """Deterministically re-materialise every valid layout (LOCAL x, y) from the compact set, in the
    documented layout order. Nothing is re-read from CAD."""
    vs = result.valid_set
    if vs is None:
        return
    f = vs.room_frame
    frame = RoomFrame(f["u_axis"][0], f["u_axis"][1], f["origin_uv"][0], f["origin_uv"][1], f["extent_u_ft"],
                      f["extent_v_ft"])
    arrays = [(tuple(i0 + t * fam.ds_u for t in range(fam.n_u)), tuple(j0 + t * fam.ds_v for t in range(fam.n_v)))
              for fam in vs.families for i0, j0 in fam.offsets]
    arrays.sort(key=lambda a: layout_key(*a))
    for iu, iv in arrays:
        cells = sorted(((i * vs.lattice_step_ft, j * vs.lattice_step_ft) for j in iv for i in iu), key=lambda q: (q[1], q[0]))
        yield [frame.to_xy(u, v) for u, v in cells]


def _proposal_digest(p: CandidateProposal) -> str:
    return _sha(p.model_dump(mode="json"))


def _grid(st: _State, pts: list[tuple[float, float]]):
    """(us, vs) room-frame coordinates if the points form a complete room-aligned grid, else None."""
    uv = [st.frame.to_uv(x, y) for x, y in pts]
    us = sorted({round(u, 9) for u, _v in uv})
    vs = sorted({round(v, 9) for _u, v in uv})
    if len(us) * len(vs) != len(uv) or {(round(u, 9), round(v, 9)) for u, v in uv} != {(u, v) for u in us for v in vs}:
        return None
    return us, vs


def _as_array(st: _State, pts: list[tuple[float, float]]):
    """(spacing_u, spacing_v) if the points form a complete, uniformly spaced room-aligned grid, else None."""
    g = _grid(st, pts)
    if g is None:
        return None
    us, vs = g

    def uniform(a):
        if len(a) < 2:
            return None, True
        d = [b - x for x, b in zip(a, a[1:], strict=False)]
        return d[0], all(abs(x - d[0]) <= 1e-9 for x in d)
    su, ok_u = uniform(us)
    sv, ok_v = uniform(vs)
    return (su, sv) if ok_u and ok_v else None


def evaluate_proposal(req: DesignRequest, proposal: CandidateProposal) -> ProposalEvaluation:
    """Deterministically evaluate a PROPOSED layout. PASS / FAIL / UNKNOWN / REFUSED come only from the
    rules engine and the geometry evaluator; nothing in the proposal can override them."""
    digest = _proposal_digest(proposal)
    refusals: list[DesignIssue] = []
    if proposal.orientation is not None:
        if req.orientation is None:
            req = req.model_copy(update={"orientation": proposal.orientation})     # proposer-chosen, fingerprinted
        elif proposal.orientation.model_dump(mode="json") != req.orientation.model_dump(mode="json"):
            refusals.append(DesignIssue(code="ORIENTATION_MISMATCH",
                                        message="the proposal's branch-line orientation differs from the request's"))
    fp = request_fingerprint(req)
    if proposal.frame != "LOCAL" or proposal.units != "ft":
        refusals.append(DesignIssue(code="WRONG_COORDINATE_FRAME",
                                    message=f"proposal is in {proposal.frame}/{proposal.units}; the evaluator works in "
                                            "LOCAL/ft of the verified package (no silent conversion)"))
    if (proposal.package_content_fingerprint, proposal.package_verification_fingerprint) != (
            req.package.content_fingerprint, req.package.verification_fingerprint):
        refusals.append(DesignIssue(code="STALE_SOURCE_FINGERPRINT",
                                    message="the proposal was made against a different (or superseded) verified model"))
    want = sorted((s.rule_set_id, s.version, s.digest()) for s in req.rule_sets)
    got = sorted((r.get("rule_set_id"), r.get("version"), r.get("digest")) for r in proposal.rule_sets)
    if want != got:
        refusals.append(DesignIssue(code="RULESET_VERSION_MISMATCH",
                                    message="the proposal names different rule sets / versions than the request"))
    if req.listing is None or (proposal.listing.get("listing_id"), proposal.listing.get("version"),
                               proposal.listing.get("digest")) != (req.listing.listing_id, req.listing.version,
                                                                   req.listing.digest()):
        refusals.append(DesignIssue(code="LISTING_VERSION_MISMATCH",
                                    message="the proposal names a different listing / version than the request"))
    if not proposal.positions:
        refusals.append(DesignIssue(code="EMPTY_PROPOSAL", message="no sprinkler positions proposed"))
    issues, space, region, res = _validate(req)
    refusals += issues
    basis = res.basis if res and not refusals else "none"
    if refusals:
        return ProposalEvaluation(verdict="REFUSED", reasons=refusals, proposal_digest=digest, request_fingerprint=fp,
                                  basis="none")
    st = _State(req, space, region, res)
    pts = [tuple(p) for p in proposal.positions]
    cells = [{"xy": p, "uv": st.frame.to_uv(*p), "bmin": {}} for p in pts]
    reasons, evals = [], []
    outside = [i for i, p in enumerate(pts) if not st.prepared.contains(Point(p))]
    excluded = [i for i, p in enumerate(pts) if any(e.intersects(Point(p)) for e in st.excluded)]
    if outside:
        reasons.append(DesignIssue(code="OUTSIDE_SPACE", message=f"sprinklers {outside} are not strictly inside the space"))
    if excluded:
        reasons.append(DesignIssue(code="IN_EXCLUDED_REGION", message=f"sprinklers {excluded} are in excluded regions"))
    arr = _as_array(st, pts)
    grid = _grid(st, pts)
    for c in st.constraints:
        e = _eval_constraint(st, c, cells, arr, grid)
        evals.append(e)
        if e.outcome == "fail":
            reasons.append(DesignIssue(code="CONSTRAINT_FAILED", message=f"{c.key}: measured {e.measured} vs {c.bound} "
                                       f"{c.limit} {c.unit}", detail={"rule": c.governing_rule_id}))
        elif e.outcome == "not_evaluable":
            reasons.append(DesignIssue(code="NOT_EVALUABLE", message=f"{c.key}: {e.note}"))
    failed = bool(outside or excluded or any(e.outcome == "fail" for e in evals))
    unknown = any(e.outcome == "not_evaluable" for e in evals)
    verdict = "FAIL" if failed else "UNKNOWN" if unknown else "PASS"
    return ProposalEvaluation(verdict=verdict, reasons=reasons, evaluations=evals, proposal_digest=digest,
                              request_fingerprint=fp, basis=basis,
                              disclaimers=(list(SYNTHETIC_DISCLAIMERS) if basis == "synthetic_test_only" else
                                           list(RULE_VALIDATION_DISCLAIMERS) if basis == "authoritative_rules_synthetic_listing" else
                                           list(RULE_REVIEW_DISCLAIMERS) if basis == "rules_under_review" else []))
