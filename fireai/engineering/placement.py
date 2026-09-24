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
from fireai.engineering.design import (ORDERING_STRATEGY, PLACEMENT_ENGINE_VERSION, SYNTHETIC_DISCLAIMERS,
                                       ConstraintEvaluation, DesignIssue, DesignPoint, DesignProvenance,
                                       EngineeringDesignResult, LayoutEvaluation, SprinklerPlacement, ZState)
from fireai.engineering.geometry import (RoomFrame, dist_point_segment, nearest_cells, room_frame,
                                         worst_boundary_point, worst_space_point)
from fireai.engineering.inputs import CeilingCondition, DesignRequest, InputSource
from fireai.rules.constraints import EngineeringConstraint
from fireai.rules.resolve import RuleResolution, resolve

# evaluation order (cheap first); rejected layouts record the FIRST failure in this order
MEASUREMENT_ORDER = ("point_to_boundary_min", "pairwise_min_distance", "array_axis_spacing",
                     "boundary_point_to_nearest_sprinkler_max", "space_point_to_nearest_sprinkler_max",
                     "nearest_sprinkler_cell_area")
M2_LIMITATIONS = (
    "Sprinkler elevation (Z) is not established: the ceiling elevation is an input, the sprinkler position "
    "below it is not derived in M2.0",
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
                 "search": req.search.model_dump(mode="json") if req.search else None})


def _uid(ns: uuid.UUID, key: str) -> str:
    return str(uuid.uuid5(ns, key))


def m2_ceiling_support(c: CeilingCondition) -> list[str]:
    """Reasons the ceiling is outside what M2.0 supports (the schema itself is broader)."""
    out = []
    if len(c.regions) != 1:
        out.append(f"{len(c.regions)} ceiling regions (M2.0 supports exactly one covering the whole space)")
    for r in c.regions:
        if r.extent != "whole_space":
            out.append(f"ceiling region {r.uid} does not cover the whole space")
        if r.surface != "flat":
            out.append(f"ceiling region {r.uid} surface is {r.surface!r} (M2.0 supports 'flat' only)")
        if r.slope.status != "known" or (r.slope.value_deg or 0.0) != 0.0:
            out.append(f"ceiling region {r.uid} slope is {r.slope.status} / {r.slope.value_deg}")
        if r.elevation.status != "known":
            out.append(f"ceiling region {r.uid} elevation is unknown")
    if c.features:
        out.append(f"{len(c.features)} ceiling features (beams, soffits, obstructions…) are not supported in M2.0")
    if c.obstructions_statement != "none_present":
        out.append(f"obstructions statement is {c.obstructions_statement!r} (an explicit 'none_present' is required)")
    return out


def design_facts(req: DesignRequest, area_sf: float | None) -> dict[str, Any]:
    """Explicit facts rules may test. Missing inputs are simply absent (→ UNKNOWN), never defaulted."""
    f: dict[str, Any] = {}
    if area_sf is not None:
        f["space.area_sf"] = area_sf
    if req.classification:
        f["hazard.scheme"] = req.classification.scheme
        f["hazard.classification"] = req.classification.value
    if req.listing:
        f["sprinkler.type"] = req.listing.sprinkler_type
        f["sprinkler.orientation"] = req.listing.orientation
        if req.listing.response_type:
            f["sprinkler.response"] = req.listing.response_type
    if req.ceiling and len(req.ceiling.regions) == 1:
        r = req.ceiling.regions[0]
        f["ceiling.surface"] = r.surface
        if r.slope.status == "known":
            f["ceiling.slope_deg"] = r.slope.value_deg
        if r.elevation.status == "known":
            f["ceiling.elevation_ft"] = r.elevation.value_ft
        f["ceiling.obstructions"] = req.ceiling.obstructions_statement
    return f


def _sources(req: DesignRequest) -> list[InputSource]:
    out = []
    for obj in (req.classification, req.tolerances, req.search):
        if obj is not None:
            out.append(obj.source)
    if req.ceiling:
        out += [s for s in [req.ceiling.statement_source] + [r.source for r in req.ceiling.regions]
                + [r.elevation.source for r in req.ceiling.regions] if s is not None]
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
        for why in m2_ceiling_support(req.ceiling):
            issues.append(DesignIssue(code="CEILING_NOT_SUPPORTED_IN_M2_0", message=why))
    if req.classification is None:
        issues.append(DesignIssue(code="MISSING_DESIGN_CLASSIFICATION",
                                  message="no hazard / design classification supplied (FireAI never infers it)"))
    if req.tolerances is None:
        issues.append(DesignIssue(code="MISSING_TOLERANCES", message="no explicit engineering tolerances supplied"))
    if req.search is None:
        issues.append(DesignIssue(code="MISSING_SEARCH_SPACE", message="no explicit placement search space supplied"))
    if req.mode == "engineering":
        if req.listing and (req.listing.content_basis != "authoritative" or req.listing.review_status != "approved"):
            issues.append(DesignIssue(code="LISTING_NOT_APPROVED_AUTHORITATIVE",
                                      message=f"listing {req.listing.listing_id} is {req.listing.content_basis} / "
                                              f"{req.listing.review_status}"))
        if req.classification and req.classification.source.kind not in ("human_decision", "project_document"):
            issues.append(DesignIssue(code="CLASSIFICATION_NOT_HUMAN_DECIDED",
                                      message="a design classification must be a human decision or project document"))
        if any(s.kind == "synthetic_test_only" for s in _sources(req)):
            issues.append(DesignIssue(code="SYNTHETIC_INPUT_IN_ENGINEERING_MODE",
                                      message="synthetic (TEST ONLY) inputs can never support engineering"))
    elif req.listing and req.listing.content_basis != "synthetic_test_only":
        issues.append(DesignIssue(code="MIXED_SYNTHETIC_AND_AUTHORITATIVE_INPUTS",
                                  message="synthetic test runs may only use synthetic listings"))
    area = region.area_sf if region is not None else None
    stack = list(req.rule_sets) + ([req.listing.rules] if req.listing else [])
    res = resolve(stack, design_facts(req, area), req.mode, req.jurisdiction)
    for r in res.refusals:
        issues.append(DesignIssue(code=r.code, message=r.message,
                                  detail={k: v for k, v in (("rule_id", r.rule_id), ("rule_set_id", r.rule_set_id)) if v}))
    if res.status == "resolved" and not res.constraints:
        issues.append(DesignIssue(code="NO_CONSTRAINTS_RESOLVED",
                                  message="no machine-evaluable constraint applies; no design can be declared valid"))
    return issues, space, region, res


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

def _eval_constraint(st: _State, c: EngineeringConstraint, cells: list[dict], arr) -> ConstraintEvaluation:
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
        (iu, iv) = arr
        sp = []
        if len(iu) >= 2:
            sp.append(((iu[1] - iu[0]) * st.step, "u"))
        if len(iv) >= 2:
            sp.append(((iv[1] - iv[0]) * st.step, "v"))
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
        measured=measured, margin=margin, passed=passed, worst_subject=subject, reference_kinds=list(c.reference_kinds),
        governing_rule_id=c.governing_rule_id, contributing_rule_ids=[x.rule_id for x in c.contributions],
        references=sorted({f"{x.source_document}" + (f" {x.source_reference}" if x.source_reference else "")
                           for x in c.contributions}), note=note)


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
    evals, first = [], None
    if not all(p["inside"] for p in cells):
        first = "structural:outside_space"
    elif any(p["excluded"] for p in cells):
        first = "structural:excluded_region"
    for c in st.constraints:          # fast path stops at the first failure; full explains everything
        if first is not None and not full:
            break
        e = _eval_constraint(st, c, cells, (iu, iv))
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
    z = ZState(status="unknown", reference=f"ceiling_region:{creg.uid}",
               note="sprinkler elevation not established in M2.0 (the ceiling elevation is an input; the position "
                    "below it is not derived)")
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
            "jurisdiction": req.jurisdiction}


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
    valid, rejected_examples, counts, generated = [], [], {}, 0
    for iu, iv in generate_candidates(st):
        generated += 1
        ev = evaluate_layout(st, iu, iv, full=False)
        if ev.valid:
            valid.append(evaluate_layout(st, iu, iv, full=True))
        else:
            counts[ev.first_failure] = counts.get(ev.first_failure, 0) + 1
            if len(rejected_examples) < req.search.max_rejected_examples:
                rejected_examples.append(evaluate_layout(st, iu, iv, full=True))
    synthetic = res.basis == "synthetic_test_only"
    return _finish(EngineeringDesignResult(
        result_uid=_uid(ns, "result"), status="VALID_LAYOUTS_FOUND" if valid else "NO_VALID_LAYOUT_IN_SEARCH_SPACE",
        basis=res.basis, engineering_use="NOT_FOR_ENGINEERING_USE" if synthetic else "REQUIRES_QUALIFIED_HUMAN_APPROVAL",
        disclaimers=list(SYNTHETIC_DISCLAIMERS) if synthetic else [],
        limitations=list(res.limitations) + list(M2_LIMITATIONS), context=_context(req, space, region, st.frame),
        inputs=_inputs(req), rules=rules,
        search={"family": req.search.family, "lattice_step_ft": st.step, "lattice_u": st.ku, "lattice_v": st.kv,
                "candidates_generated": generated, "valid": len(valid), "rejected": generated - len(valid),
                "rejected_by_first_failure": dict(sorted(counts.items())),
                "evaluation_order": ["structural:outside_space", "structural:excluded_region"]
                + [c.key for c in st.constraints]},
        valid_layouts=valid, rejected_examples=rejected_examples, request_fingerprint=fp))


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
