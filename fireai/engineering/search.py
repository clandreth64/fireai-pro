"""Exact, pruned search over the ``room_axis_array/1`` space (Milestone 2.1).

Same search space and same valid set as the M2.0 brute force (``reference_search``) — only
candidates that are PROVEN invalid are skipped. Every pruning step is a proof of failure of one
specific constraint, and the rejection is attributed to that constraint:

  1. AXIS    array_axis_spacing (either bound) and pairwise_min_distance (min bound) are decided per
             axis before any combination: in a rectangular array the closest pair is adjacent along an
             axis, so min pairwise distance = min of the present axis spacings.
  2. COUNT   nearest_sprinkler_cell_area: the cells partition the space, so max cell >= area / n and
             min cell <= area / n — whole (n_u, n_v) groups are decided without coordinates.
  3. POINTS  structural containment / exclusion and point_to_boundary_min are properties of single
             lattice points; a combination fails at its earliest-ordered failing point (bit masks).
  4. PAIR    pairwise_min_distance (max bound), from the axis spacings.
  5. COVER   boundary / space "worst point" maxima are at least the distance from any vertex to its
             nearest sprinkler: a vertex farther than the limit proves failure.
  6. FULL    exact measurement of the remaining constraints (documented order).

Nothing is approximated; completeness within the stated search space is preserved (tested against
the reference on the known-answer cases). Parallelisation later: stage 3+ is independent per u-set.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from fireai.engineering.geometry import dist_point_segment, nearest_cells, worst_boundary_point, worst_space_point

PIPELINE = ("axis", "count", "points", "pair", "cover", "full")
AXIS_MEAS = {"array_axis_spacing"}
POINT_MEAS = {"point_to_boundary_min"}
COVER_MEAS = {"boundary_point_to_nearest_sprinkler_max", "space_point_to_nearest_sprinkler_max"}


@dataclass
class SearchOutcome:
    valid: list[tuple[tuple[int, ...], tuple[int, ...]]]
    rejected_by: dict[str, int]
    pruned_by_stage: dict[str, int]
    search_space_size: int
    fully_evaluated: int
    examples: list[tuple[tuple[int, ...], tuple[int, ...]]] = field(default_factory=list)


def axis_sets(k: int, max_n: int) -> list[tuple[int, ...]]:
    out = [(i,) for i in range(1, k + 1)]
    for n in range(2, max_n + 1):
        for ds in range(1, k):
            for s0 in range(1, k - (n - 1) * ds + 1):
                out.append(tuple(s0 + t * ds for t in range(n)))
    return out


def layout_key(iu, iv):
    """The documented layout order: fewer sprinklers first, then positions bottom-to-top, left-to-right."""
    return (len(iu) * len(iv), sorted((j, i) for i in iu for j in iv))


def _viol(c, value: float, tol: float) -> bool:
    return value > c.limit + tol if c.bound == "max" else value < c.limit - tol


def _tol(st, c) -> float:
    return st.req.tolerances.length_ft if c.unit == "ft" else st.req.tolerances.area_sf


def search(st) -> SearchOutcome:
    s = st.req.search
    cons = st.constraints
    step = st.step
    U_all, V_all = axis_sets(st.ku, s.max_per_axis), axis_sets(st.kv, s.max_per_axis)
    hv_all = Counter(len(v) for v in V_all)
    size = sum(hv_all[lv] for u in U_all for lv in hv_all if len(u) * lv <= s.max_sprinklers)
    rejected: Counter = Counter()
    stages: Counter = Counter()
    examples: list = []

    def note(stage, key, n, ex=None):
        if n:
            rejected[key] += n
            stages[stage] += n
            if ex is not None and len(examples) < s.max_rejected_examples:
                examples.append(ex)

    # 1. AXIS ------------------------------------------------------------------------------------
    axis_cons = [c for c in cons if c.measurement in AXIS_MEAS or (c.measurement == "pairwise_min_distance"
                                                                      and c.bound == "min")]

    def axis_fail(ix):
        if len(ix) < 2:
            return None
        sp = (ix[1] - ix[0]) * step
        for c in axis_cons:
            if _viol(c, sp, _tol(st, c)):
                return c.key
        return None

    U_ok, V_ok = [], []
    for u in U_all:
        k = axis_fail(u)
        if k:
            n = sum(hv_all[lv] for lv in hv_all if len(u) * lv <= s.max_sprinklers)
            first_v = next((v for v in V_all if len(u) * len(v) <= s.max_sprinklers), None)
            note("axis", k, n, (u, first_v) if first_v else None)
        else:
            U_ok.append(u)
    hu_ok = Counter(len(u) for u in U_ok)
    for v in V_all:
        k = axis_fail(v)
        if k:
            n = sum(hu_ok[lu] for lu in hu_ok if lu * len(v) <= s.max_sprinklers)
            first_u = next((u for u in U_ok if len(u) * len(v) <= s.max_sprinklers), None)
            note("axis", k, n, (first_u, v) if first_u else None)
        else:
            V_ok.append(v)

    # 2. COUNT -----------------------------------------------------------------------------------
    area = st.poly.area
    cell_cons = [c for c in cons if c.measurement == "nearest_sprinkler_cell_area"]

    def count_fail(n):
        for c in cell_cons:
            if _viol(c, area / n, _tol(st, c)):
                return c.key
        return None

    by_lu: dict[int, list] = {}
    for u in U_ok:
        by_lu.setdefault(len(u), []).append(u)
    by_lv: dict[int, list] = {}
    for v in V_ok:
        by_lv.setdefault(len(v), []).append(v)
    groups = []
    for lu in sorted(by_lu):
        for lv in sorted(by_lv):
            if lu * lv > s.max_sprinklers:
                continue
            k = count_fail(lu * lv)
            if k:
                note("count", k, len(by_lu[lu]) * len(by_lv[lv]), (by_lu[lu][0], by_lv[lv][0]))
            else:
                groups.append((lu, lv))

    # 3. POINTS (bit masks over the v lattice) ------------------------------------------------------
    point_cons = [c for c in cons if c.measurement in POINT_MEAS]
    reasons = ["structural:outside_space", "structural:excluded_region"] + [c.key for c in point_cons]
    inf = len(reasons)
    rank = {}
    for i in range(1, st.ku + 1):
        for j in range(1, st.kv + 1):
            p = st.point(i, j)
            r = inf
            if not p["inside"]:
                r = 0
            elif p["excluded"]:
                r = 1
            else:
                for n_c, c in enumerate(point_cons):
                    segs = st.segs(c.reference_kinds)
                    if not segs:
                        continue
                    key = (c.key,)
                    if key not in p["bmin"]:
                        p["bmin"][key] = min(dist_point_segment(p["xy"], a, b) for a, b in segs)
                    if _viol(c, p["bmin"][key], _tol(st, c)):
                        r = 2 + n_c
                        break
            rank[(i, j)] = r

    survivors = []
    for lu, lv in groups:
        for u in by_lu[lu]:
            col = {j: min(rank[(i, j)] for i in u) for j in range(1, st.kv + 1)}
            for v in by_lv[lv]:
                r = min(col[j] for j in v)
                if r < inf:
                    note("points", reasons[r], 1, (u, v))
                else:
                    survivors.append((u, v))

    # 4-6. PAIR / COVER / FULL ---------------------------------------------------------------------
    pair_max = [c for c in cons if c.measurement == "pairwise_min_distance" and c.bound == "max"]
    cover = [c for c in cons if c.measurement in COVER_MEAS and c.bound == "max"]
    verts = {c.key: ([pt for seg in st.segs(c.reference_kinds) for pt in seg]
                     if c.measurement == "boundary_point_to_nearest_sprinkler_max" else list(st.polygon)) for c in cover}
    full_cons = [c for c in cons if not (c.measurement in AXIS_MEAS or c.measurement in POINT_MEAS
                                         or (c.measurement == "pairwise_min_distance"))]
    valid, evaluated = [], 0
    for u, v in survivors:
        pts = [st.point(i, j)["xy"] for j in v for i in u]
        failed = None
        if len(pts) >= 2:
            spacings = [(u[1] - u[0]) * step] if len(u) > 1 else []
            spacings += [(v[1] - v[0]) * step] if len(v) > 1 else []
            mind = min(spacings)
            for c in pair_max:
                if _viol(c, mind, _tol(st, c)):
                    failed = ("pair", c.key)
                    break
        if failed is None:
            for c in cover:
                lim = c.limit + _tol(st, c)
                if any(min(math.dist(q, p) for p in pts) > lim for q in verts[c.key]):
                    failed = ("cover", c.key)
                    break
        if failed is None:
            evaluated += 1
            for c in full_cons:
                if _viol(c, full_measure(st, c, pts), _tol(st, c)):
                    failed = ("full", c.key)
                    break
        if failed:
            note(failed[0], failed[1], 1, (u, v))
        else:
            valid.append((u, v))
    valid.sort(key=lambda t: layout_key(*t))
    return SearchOutcome(valid=valid, rejected_by=dict(sorted(rejected.items())), pruned_by_stage=dict(stages),
                         search_space_size=size, fully_evaluated=evaluated, examples=examples)


def full_measure(st, c, pts) -> float:
    """Exact measurement for constraints decided in the FULL stage (a vacuous measurement passes)."""
    if c.measurement == "boundary_point_to_nearest_sprinkler_max":
        segs = st.segs(c.reference_kinds)
        return worst_boundary_point(segs, pts)[0] if segs else (-math.inf if c.bound == "max" else math.inf)
    if c.measurement == "space_point_to_nearest_sprinkler_max":
        return worst_space_point(st.polygon, pts)[0]
    if c.measurement == "nearest_sprinkler_cell_area":
        areas = [g.area for g in nearest_cells(st.polygon, pts)]
        return max(areas) if c.bound == "max" else min(areas)
    if c.measurement == "pairwise_min_distance":
        return min((math.dist(a, b) for a, b in combinations(pts, 2)), default=math.inf if c.bound == "min" else -math.inf)
    raise ValueError(c.measurement)
