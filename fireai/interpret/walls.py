"""Wall ANALYSIS layer (Milestone 1.6): paired faces, thickness, centerlines,
junctions and openings, derived from wall linework.

Everything here is DERIVED ANALYSIS GEOMETRY, stored in ``model.wall_model``
and never in ``model.elements`` or ``model.entities``. It is marked
``derived: true`` and every object cites the wall elements / source entities
it came from. Nothing is inferred without linework evidence:

* W-PAIR        two wall-layer segments that are parallel (<= PARALLEL_TOL_DEG),
                MIN..MAX_THICKNESS_FT apart and overlapping along their length
                form a wall piece over their common interval. Each segment is
                claimed nearest-partner-first, so a third line between two faces
                prevents them from pairing.
* W-PAIR-ARC    concentric wall-layer arcs, same rules -> curved wall piece.
* W-JUNCTION    centerline endpoints meeting other pieces: L (corner), T, X,
                or free end.
* W-OPENING     a gap between two collinear pieces of equal thickness,
                MIN..MAX_OPENING_FT long. With a door element in the gap -> door
                opening; otherwise doorless opening (cased opening, pass-through,
                or simply two walls that do not meet -> always needs review).
                Jamb lines across the wall at both ends are recorded as evidence.
* W-EXTERIOR    a piece with a face on the OUTER outline of the closed wall
                linework is "exterior_evidence"; otherwise "interior_evidence";
                "unknown" when the closed linework does not fill most of the
                wall extent (e.g. open exterior walls). Evidence, not fact.

Segments that pair with nothing stay "unpaired linework": FireAI does not
assume a thickness for them.
"""

from __future__ import annotations

import math
from collections import defaultdict

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from fireai.ingest.extract import uid_for

MIN_THICKNESS_FT = 0.25        # 3 in
MAX_THICKNESS_FT = 2.0         # 24 in
TYPICAL_THICKNESS_FT = (0.29, 1.5)   # 3.5 in .. 18 in
PARALLEL_TOL_DEG = 1.0
MIN_OVERLAP_FT = 0.5
MIN_OPENING_FT = 1.5
MAX_OPENING_FT = 12.0
COLLINEAR_TOL_FT = 0.1
MIN_SEGMENT_FT = 0.05
CENTER_TOL_FT = 0.05
EXTERIOR_MIN_COVER = 0.5         # closed outline must fill >= 50% of the wall bounding box
MIN_LENGTH_TO_THICKNESS = 1.25   # shorter pieces are corner overlaps, not walls (short stubs stay unpaired)
PLAN_VIEW_TYPES = {"FLOOR_PLAN", "UNKNOWN"}


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (0.0, 0.0)


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1]


class _Seg:
    __slots__ = ("i", "a", "b", "u", "n", "length", "element_id", "entity_ids", "region", "claims", "ambiguous")

    def __init__(self, i, a, b, element_id, entity_ids, region):
        self.i, self.a, self.b = i, a, b
        self.u = _unit(a, b)
        self.n = (-self.u[1], self.u[0])
        self.length = math.dist(a, b)
        self.element_id, self.entity_ids, self.region = element_id, entity_ids, region
        self.claims: list[tuple[float, float, int]] = []   # (t0, t1, partner) along this segment
        self.ambiguous = False

    def t(self, p):
        return _dot((p[0] - self.a[0], p[1] - self.a[1]), self.u)

    def at(self, t):
        return (self.a[0] + self.u[0] * t, self.a[1] + self.u[1] * t)

    def offset(self, p):
        return _dot((p[0] - self.a[0], p[1] - self.a[1]), self.n)


def _subtract(intervals, lo, hi):
    """Parts of [lo, hi] not covered by intervals."""
    out = [(lo, hi)]
    for a, b in sorted(intervals):
        nxt = []
        for x, y in out:
            if b <= x or a >= y:
                nxt.append((x, y))
            else:
                if a > x:
                    nxt.append((x, a))
                if b < y:
                    nxt.append((b, y))
        out = nxt
    return [(x, y) for x, y in out if y - x >= MIN_OVERLAP_FT]


def _segments(walls, region_of_element) -> tuple[list[_Seg], list]:
    segs, arcs = [], []
    for w in walls:
        g = w.geometry
        if g is None:
            continue
        region = region_of_element.get(w.id)
        if g.kind == "arc" and g.center is not None and g.radius:
            arcs.append((w, region))
            continue
        if g.kind not in ("line", "polyline"):
            continue
        pts = list(g.points)
        if g.closed and len(pts) > 2:
            pts.append(pts[0])
        for a, b in zip(pts, pts[1:], strict=False):
            if math.dist(a, b) >= MIN_SEGMENT_FT:
                segs.append(_Seg(len(segs), tuple(a), tuple(b), w.id, list(w.source_entity_ids), region))
    return segs, arcs


def _pair_straight(segs: list[_Seg]):
    if not segs:
        return []
    lines = [LineString([s.a, s.b]) for s in segs]
    tree = STRtree(lines)
    cos_tol = math.cos(math.radians(PARALLEL_TOL_DEG))
    cands: dict[int, list[tuple[float, int, float, float]]] = defaultdict(list)
    for s in segs:
        for j in tree.query(lines[s.i].buffer(MAX_THICKNESS_FT)):
            j = int(j)
            if j == s.i:
                continue
            t = segs[j]
            if abs(_dot(s.u, t.u)) < cos_tol or s.region != t.region:
                continue
            d = abs(s.offset(t.a))
            d2 = abs(s.offset(t.b))
            if abs(d - d2) > COLLINEAR_TOL_FT or not (MIN_THICKNESS_FT <= (d + d2) / 2 <= MAX_THICKNESS_FT):
                continue
            ta, tb = sorted((s.t(t.a), s.t(t.b)))
            lo, hi = max(0.0, ta), min(s.length, tb)
            if hi - lo < min(MIN_OVERLAP_FT, 0.5 * min(s.length, t.length)):
                continue
            cands[s.i].append(((d + d2) / 2, j, lo, hi, 1 if s.offset(t.a) > 0 else -1))
    # nearest partner claims first; farther partners only claim what is left
    for i, cl in cands.items():
        s = segs[i]
        cl = sorted(cl)
        for d, j, lo, hi, side in cl:
            # equally near partners on BOTH sides over the same stretch: pairing is ambiguous
            if any(abs(d2 - d) <= 0.02 and side2 != side and min(hi, hi2) - max(lo, lo2) >= MIN_OVERLAP_FT
                   for d2, _j2, lo2, hi2, side2 in cl):
                s.ambiguous = True
            for x, y in _subtract([(a, b) for a, b, _ in s.claims], lo, hi):
                s.claims.append((x, y, j))
    pieces = []
    done = set()
    for s in segs:
        for x, y, j in s.claims:
            t = segs[j]
            # mutual: t must claim s over an overlapping interval
            back = [(a, b) for a, b, k in t.claims if k == s.i]
            if not back:
                continue
            # intersect intervals in s's parameter space
            best = None
            for a, b in back:
                pa, pb = sorted((s.t(t.at(a)), s.t(t.at(b))))
                lo, hi = max(x, pa), min(y, pb)
                if hi - lo >= MIN_OVERLAP_FT and (best is None or hi - lo > best[1] - best[0]):
                    best = (lo, hi)
            if best is None:
                continue
            lo, hi = best
            off = s.offset(t.at(t.t(s.at((lo + hi) / 2))))
            if hi - lo < MIN_LENGTH_TO_THICKNESS * abs(off):
                continue    # square-ish overlap (e.g. where two closed wall outlines overlap at a corner)
            p0, p1 = s.at(lo), s.at(hi)
            c0 = (p0[0] + s.n[0] * off / 2, p0[1] + s.n[1] * off / 2)
            c1 = (p1[0] + s.n[0] * off / 2, p1[1] + s.n[1] * off / 2)
            q0 = (p0[0] + s.n[0] * off, p0[1] + s.n[1] * off)
            q1 = (p1[0] + s.n[0] * off, p1[1] + s.n[1] * off)
            key = (min(s.i, j), max(s.i, j), tuple(sorted((round(c0[0], 3), round(c0[1], 3), round(c1[0], 3),
                                                            round(c1[1], 3)))))
            if key in done:
                continue      # the same piece seen from the partner segment
            done.add(key)
            pieces.append({"kind": "straight", "thickness_ft": abs(off), "centerline": [c0, c1],
                           "faces": [(s, [p0, p1]), (t, [q0, q1])], "region": s.region,
                           "ambiguous": s.ambiguous or t.ambiguous})
    return pieces


def _pair_arcs(arcs):
    pieces = []
    used = set()
    for i, (w, reg) in enumerate(arcs):
        gi = w.geometry
        best = None
        for j, (v, reg2) in enumerate(arcs):
            if j <= i or j in used or i in used or reg != reg2:
                continue
            gj = v.geometry
            if math.dist(gi.center, gj.center) > CENTER_TOL_FT:
                continue
            d = abs(gi.radius - gj.radius)
            if not (MIN_THICKNESS_FT <= d <= MAX_THICKNESS_FT):
                continue
            s0, e0 = gi.start_angle % 360, gi.end_angle % 360
            s1, e1 = gj.start_angle % 360, gj.end_angle % 360
            span0 = (e0 - s0) % 360 or 360
            span1 = (e1 - s1) % 360 or 360
            # angular overlap (start of the later arc within the other)
            lo = s0 if (s0 - s1) % 360 <= span1 else s1
            hi_span = min((s0 + span0 - lo) % 360 or 360, (s1 + span1 - lo) % 360 or 360)
            if (s0 - s1) % 360 > span1 and (s1 - s0) % 360 > span0:
                continue
            if best is None or d < best[0]:
                best = (d, j, lo, hi_span)
        if best:
            d, j, lo, span = best
            used.update((i, j))
            v = arcs[j][0]
            r = (gi.radius + v.geometry.radius) / 2
            pieces.append({"kind": "curved", "thickness_ft": d, "arc": {"center": list(gi.center), "radius": r,
                                                                         "start_angle": lo, "end_angle": (lo + span) % 360},
                           "length_ft": math.radians(span) * r, "faces_elements": [w, v], "region": reg})
    return pieces


def _junctions(walls_out):
    lines = {w["id"]: LineString(w["centerline"]) for w in walls_out if w["kind"] == "straight"}
    if not lines:
        return []
    ids = list(lines)
    tree = STRtree([lines[i] for i in ids])
    thick = {w["id"]: w["thickness_ft"] for w in walls_out}
    out = []
    seen = set()
    for wid, ln in lines.items():
        for end in (0, -1):
            p = Point(ln.coords[end])
            touching = []
            for k in tree.query(p.buffer(thick[wid] * 0.75 + 0.1)):
                oid = ids[int(k)]
                if oid == wid:
                    continue
                other = lines[oid]
                tol = max(thick[wid], thick[oid]) * 0.75 + 0.1
                if other.distance(p) > tol:
                    continue
                dend = min(Point(other.coords[0]).distance(p), Point(other.coords[-1]).distance(p))
                touching.append((oid, "end" if dend <= tol else "interior"))
            if not touching:
                out.append({"type": "free_end", "point": [p.x, p.y], "wall_ids": [wid]})
                continue
            members = [wid] + [o for o, _ in touching]
            key = frozenset(members)
            if key in seen:
                continue      # same junction reached from another member's endpoint
            seen.add(key)
            if any(kind == "interior" for _, kind in touching):
                jt = "T"
            elif len(members) == 2:
                jt = "L"
            else:
                jt = f"{len(members)}-way"
            out.append({"type": jt, "point": [p.x, p.y], "wall_ids": sorted(set(members))})
    # X: centerlines crossing in both interiors
    for a in ids:
        for k in tree.query(lines[a]):
            b = ids[int(k)]
            if b <= a:
                continue
            inter = lines[a].intersection(lines[b])
            if inter.geom_type != "Point":
                continue
            ends = [Point(c) for ln in (lines[a], lines[b]) for c in (ln.coords[0], ln.coords[-1])]
            if min(e.distance(inter) for e in ends) > max(thick[a], thick[b]):
                out.append({"type": "X", "point": [inter.x, inter.y], "wall_ids": [a, b]})
    return out


def _openings(walls_out, straight_segs, doors):
    by_axis = [w for w in walls_out if w["kind"] == "straight"]
    door_geoms = [(d.id, LineString(d.geometry.points).buffer(0.25) if len(d.geometry.points) > 1
                   else Point(d.geometry.points[0]).buffer(0.5)) for d in doors if d.geometry and d.geometry.points]
    seg_lines = [(s, LineString([s.a, s.b])) for s in straight_segs]
    out = []
    for i, a in enumerate(by_axis):
        a0, a1 = a["centerline"]
        ua = _unit(a0, a1)
        best = None
        for b in by_axis[i + 1:]:
            if b["region"] != a["region"] or abs(a["thickness_ft"] - b["thickness_ft"]) > 0.1:
                continue
            b0, b1 = b["centerline"]
            if abs(abs(_dot(ua, _unit(b0, b1))) - 1) > 1e-3:
                continue
            nrm = (-ua[1], ua[0])
            if abs(_dot((b0[0] - a0[0], b0[1] - a0[1]), nrm)) > COLLINEAR_TOL_FT:
                continue
            ta = sorted((0.0, math.dist(a0, a1)))
            tb = sorted((_dot((b0[0] - a0[0], b0[1] - a0[1]), ua), _dot((b1[0] - a0[0], b1[1] - a0[1]), ua)))
            if tb[0] >= ta[1]:
                gap, g0, g1 = tb[0] - ta[1], ta[1], tb[0]
            elif ta[0] >= tb[1]:
                gap, g0, g1 = ta[0] - tb[1], tb[1], ta[0]
            else:
                continue
            if MIN_OPENING_FT <= gap <= MAX_OPENING_FT and (best is None or gap < best[0]):
                best = (gap, b, g0, g1)
        if best is None:
            continue
        gap, b, g0, g1 = best
        p0 = (a0[0] + ua[0] * g0, a0[1] + ua[1] * g0)
        p1 = (a0[0] + ua[0] * g1, a0[1] + ua[1] * g1)
        gapline = LineString([p0, p1])
        th = a["thickness_ft"]
        # something crossing the gap (another wall piece) means it is not an opening
        blocked = any(w is not a and w is not b and w["kind"] == "straight"
                      and LineString(w["centerline"]).distance(gapline.interpolate(0.5, normalized=True)) < th / 2
                      for w in by_axis)
        if blocked:
            continue
        zone = gapline.buffer(th * 0.75 + 0.25)
        door_ids = [did for did, g in door_geoms if g.intersects(zone)]
        # jambs: segments across the wall (perpendicular, ~thickness long) at each gap end
        jambs = 0
        for end in (p0, p1):
            e = Point(end)
            for s, ln in seg_lines:
                if abs(_dot(s.u, ua)) < 0.05 and abs(s.length - th) <= 0.1 + 0.1 * th and ln.distance(e) <= 0.1:
                    jambs += 1
                    break
        kind = "door" if door_ids else "doorless"
        conf = 0.7 if door_ids else (0.5 if jambs == 2 else 0.3)
        ev = [f"gap of {gap:.2f} ft between collinear wall pieces {a['id']} and {b['id']} "
              f"(thickness {th:.3f} ft)", f"jamb lines found at {jambs} of 2 ends"]
        ev.append(f"door element(s) {door_ids} in the gap" if door_ids else
                  "no door element in the gap: doorless opening (cased opening / pass-through) or two walls "
                  "that do not meet")
        out.append({"kind": kind, "wall_ids": [a["id"], b["id"]], "width_ft": round(gap, 4),
                    "thickness_ft": round(th, 4), "center": [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2],
                    "span": [list(p0), list(p1)], "door_element_ids": door_ids, "jambs": jambs,
                    "confidence": conf, "rules": ["W-OPENING"], "evidence": ev,
                    "requires_verification": kind == "doorless" or conf < 0.7, "region": a["region"]})
    return out


def _exterior(all_lines):
    """Outer outline of the closed wall linework: the outer rings of the filled
    polygonized faces (interior ring boundaries are NOT outline). Returns
    ("unknown", None) unless the outline covers most of the wall extent."""
    if not all_lines:
        return "unknown", None
    faces = list(polygonize(unary_union(all_lines)))
    if not faces:
        return "unknown", None
    filled = unary_union([Polygon(f.exterior) for f in faces])
    minx, miny, maxx, maxy = unary_union(all_lines).bounds
    extent = max((maxx - minx) * (maxy - miny), 1e-9)
    if filled.area < EXTERIOR_MIN_COVER * extent:
        return "unknown", None
    polys = list(getattr(filled, "geoms", [filled]))
    return "ok", unary_union([LineString(pg.exterior.coords) for pg in polys])


def build_wall_model(model, source_uid: str | None) -> dict:
    """Derive the wall analysis layer from wall elements in plan-type regions."""
    region_of_element = {}
    region_type = {r["id"]: r.get("view_type", "UNKNOWN") for r in model.view_regions}
    for el in model.elements:
        rid = el.properties.get("view_region")
        if rid:
            region_of_element[el.id] = rid
    all_walls = [w for w in model.elements if w.category == "wall" and w.subtype == "wall_linework"]
    walls = [w for w in all_walls if region_type.get(region_of_element.get(w.id), "UNKNOWN") in PLAN_VIEW_TYPES]
    skipped = len(all_walls) - len(walls)
    segs, arcs = _segments(walls, region_of_element)
    straight = _pair_straight(segs)
    curved = _pair_arcs(arcs)
    lines_by_region = defaultdict(list)
    for sg in segs:
        lines_by_region[sg.region].append(LineString([sg.a, sg.b]))
    outlines = {reg: _exterior(lines) for reg, lines in lines_by_region.items()}
    ents = {e.id: e for e in model.entities}

    walls_out = []
    for n, p in enumerate(straight + curved, start=1):
        wid = f"WA{n:05d}"
        if p["kind"] == "straight":
            faces = [{"element_id": s.element_id, "entity_ids": s.entity_ids,
                      "segment": [list(q) for q in pts]} for s, pts in p["faces"]]
            length = math.dist(*p["centerline"])
            geom = {"centerline": [list(c) for c in p["centerline"]]}
        else:
            faces = [{"element_id": w.id, "entity_ids": list(w.source_entity_ids)} for w in p["faces_elements"]]
            length = p["length_ft"]
            geom = {"arc": p["arc"]}
        th = p["thickness_ft"]
        ev = [f"two parallel wall-layer {'lines' if p['kind'] == 'straight' else 'arcs'} "
              f"{th:.3f} ft ({th * 12:.1f} in) apart over {length:.2f} ft"]
        conf = 0.7
        if not (TYPICAL_THICKNESS_FT[0] <= th <= TYPICAL_THICKNESS_FT[1]):
            ev.append(f"thickness {th * 12:.1f} in is outside the typical 3.5-18 in range")
            conf = 0.5
        if p.get("ambiguous"):
            ev.append("a face has equally near parallel partners on both sides (e.g. three parallel lines); "
                      "which pair forms the wall is ambiguous")
            conf = min(conf, 0.4)
        ext = "unknown"
        status, outline = outlines.get(p["region"], ("unknown", None))
        if status == "ok" and p["kind"] == "straight":
            on = [outline.distance(Point(f["segment"][0])) < 0.02 and outline.distance(Point(f["segment"][1])) < 0.02
                  for f in faces]
            ext = "exterior_evidence" if any(on) else "interior_evidence"
            ev.append("one face lies on the outline of all wall linework" if any(on)
                      else "neither face lies on the outline of all wall linework")
        uids = sorted(ents[i].uid for f in faces for i in f["entity_ids"] if i in ents and ents[i].uid)
        key = "|".join(uids) + "|" + ",".join(f"{c:.3f}" for pt in geom.get("centerline", []) for c in pt)
        walls_out.append({"id": wid, "uid": uid_for(source_uid, "wall_analysis|" + key) if source_uid else None,
                          "kind": p["kind"], "thickness_ft": round(th, 4), "length_ft": round(length, 4),
                          **geom, "faces": faces, "exterior": ext, "confidence": conf,
                          "rules": ["W-PAIR" if p["kind"] == "straight" else "W-PAIR-ARC"]
                                   + (["W-EXTERIOR"] if ext != "unknown" else []),
                          "evidence": ev, "requires_verification": conf < 0.7, "region": p["region"]})

    doors = [el for el in model.elements if el.category == "door"]
    junctions = _junctions(walls_out)
    openings = _openings(walls_out, segs, doors)
    for n, j in enumerate(junctions, start=1):
        j["id"] = f"WJ{n:05d}"
        j["rules"] = ["W-JUNCTION"]
    for n, o in enumerate(openings, start=1):
        o["id"] = f"WO{n:05d}"

    paired = {s.i for p in straight for s, _ in p["faces"]}
    paired_elems = {s.element_id for s in segs if s.i in paired}
    unpaired = [s for s in segs if s.i not in paired]
    unpaired_elems = sorted({s.element_id for s in unpaired} - paired_elems)
    return {
        "schema": "wall_analysis/1",
        "derived": True,
        "frame": "LOCAL", "units": "ft",
        "note": ("DERIVED ANALYSIS GEOMETRY from wall linework. Centerlines, thicknesses, junctions and openings are "
                 "not source geometry and are not walls in their own right; each cites the linework it came from."),
        "parameters": {"min_thickness_ft": MIN_THICKNESS_FT, "max_thickness_ft": MAX_THICKNESS_FT,
                       "parallel_tol_deg": PARALLEL_TOL_DEG, "min_overlap_ft": MIN_OVERLAP_FT,
                       "opening_range_ft": [MIN_OPENING_FT, MAX_OPENING_FT]},
        "walls": walls_out,
        "junctions": junctions,
        "openings": openings,
        "unpaired_linework": {"segment_count": len(unpaired), "element_ids": unpaired_elems[:500],
                              "element_count": len(unpaired_elems),
                              "note": "wall-layer segments with no parallel partner; no thickness assumed"},
        "stats": {"wall_segments": len(segs), "paired_segments": len(paired), "wall_pieces": len(walls_out),
                  "curved_pieces": len(curved), "junctions": len(junctions),
                  "door_openings": sum(1 for o in openings if o["kind"] == "door"),
                  "doorless_openings": sum(1 for o in openings if o["kind"] == "doorless"),
                  "wall_elements_in_non_plan_views_skipped": skipped,
                  "exterior_outline_by_region": {str(k): v[0] for k, v in outlines.items()}},
    }
