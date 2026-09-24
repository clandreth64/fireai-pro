"""Region boundary classification and plan openings (Milestone 1.9, schema 0.5.0).

Runs after the wall analysis, room analysis and human corrections. For every physical region
(`room` element, machine or human) it classifies the ordered perimeter into segments that answer
"what bounds the region here?" using evidence already in the model — never CAD re-parsing:

* B-DOOR-CLOSURE  the edge is an analysis line FireAI drew across a door opening
                  (``room.properties["door_closures"]``) AND continues the line of the
                  wall it interrupts (B-OPENING-IN-WALL-LINE)                    -> door_opening
* B-WALL-FACE     the edge lies on wall linework (source geometry)               -> wall
* B-WALL-BAND     the edge lies inside a derived wall piece (e.g. a room polygon drawn
                  on the wall centreline)                                        -> wall
* B-WINDOW        a window element sits in the wall just outside a wall/gap edge: it overlaps
                  the edge's outward strip and its CENTRE lies within that strip (a window in the
                  neighbouring, perpendicular wall of a corner or bay does not count)  -> window
* B-DOOR-GAP      an edge portion with no wall, spanned by a door element        -> door_opening
* B-OPEN-OPENING  an edge portion with no wall at a doorless wall-analysis opening -> open_opening
* B-UNKNOWN       anything else: no evidence of what bounds the region           -> unknown

* B-OPENING-IN-WALL-LINE  an opening (door, doorless, window gap) is only recognised where the
                  boundary runs ALONG a wall line: the same edge carries wall linework, or wall
                  linework parallel to the edge ends where the opening ends. An analysis line that
                  cuts diagonally across a wall corner or between two door leaves is not a doorway;
                  it stays ``unknown``.

Nothing is invented: a portion with no evidence stays ``unknown`` (``encloses: None``).

* O-OPENING       each physical opening crossed by region boundaries becomes ONE `opening`
                  element (the void), filled by its door/window element where one exists. Openings
                  are keyed by their fill element (or the doorless wall-analysis opening), so the
                  wall analysis's overlapping duplicate gaps for one door never become duplicate
                  openings; the duplicates are listed on the opening for traceability.
"""

from __future__ import annotations

import math
from collections import defaultdict

from shapely.geometry import LineString, MultiPoint, Point, Polygon

from shapely.geometry.polygon import orient

from fireai.ingest.extract import uid_for
from fireai.model import (BoundaryRing, BoundarySegment, BuildingElement, Geometry, Issue, Provenance,
                          RegionBoundary)

ON_LINE_TOL_FT = 0.02          # an edge portion within this distance of wall linework lies on it
PARALLEL_SIN = math.sin(math.radians(1.0))
MIN_PIECE_FT = 0.02            # shorter pieces are merged into a neighbour (numerical noise)
GAP_EVIDENCE_OVERLAP = 0.6     # door/window/opening must span this fraction of an uncovered gap
DOOR_GAP_MAX_FT = 12.0         # a door never spans a longer uncovered gap
WINDOW_STRIP_FT = 1.25         # windows are looked for this far OUTSIDE a region edge (inside the wall)
WINDOW_MIN_FT = 0.5
ENCLOSES = {"wall": True, "window": True, "door_opening": False, "open_opening": False, "unknown": None}
CONF = {"B-DOOR-CLOSURE": 0.7, "B-WALL-FACE": 0.9, "B-WALL-BAND": 0.7, "B-WINDOW": 0.7, "B-DOOR-GAP": 0.6,
        "B-OPEN-OPENING": 0.4, "B-UNKNOWN": 0.0}
VERIFY_BELOW = 0.8


def _live(el) -> bool:
    return el.provenance.review.status != "rejected"


def _segments_of(geom) -> list[tuple[tuple, tuple]]:
    pts = list(geom.points) if geom else []
    if geom is not None and geom.closed and len(pts) > 2:
        pts = pts + [pts[0]]
    return [(tuple(pts[i]), tuple(pts[i + 1])) for i in range(len(pts) - 1) if pts[i] != pts[i + 1]]


def _footprint(el):
    pts = el.geometry.points if el.geometry else []
    if len(pts) >= 3:
        p = Polygon(pts)
        return p if p.is_valid and p.area > 0 else MultiPoint(pts).convex_hull
    if len(pts) == 2:
        return LineString(pts).buffer(0.05)
    return None


class _Edge:
    def __init__(self, a, b):
        self.a, self.b = a, b
        self.L = math.dist(a, b)
        self.u = ((b[0] - a[0]) / self.L, (b[1] - a[1]) / self.L)
        self.n_out = (self.u[1], -self.u[0])     # outer ring is CCW: the region is on the left

    def t(self, p):
        return (p[0] - self.a[0]) * self.u[0] + (p[1] - self.a[1]) * self.u[1]

    def d(self, p):                               # signed distance, positive = inside (left)
        return self.u[0] * (p[1] - self.a[1]) - self.u[1] * (p[0] - self.a[0])

    def at(self, t):
        return (self.a[0] + self.u[0] * t, self.a[1] + self.u[1] * t)

    def clip(self, t0, t1):
        lo, hi = max(0.0, min(t0, t1)), min(self.L, max(t0, t1))
        return (lo, hi) if hi - lo > 1e-9 else None

    def parallel(self, p, q) -> bool:
        L = math.dist(p, q)
        return L > 1e-9 and abs(self.u[0] * (q[1] - p[1]) - self.u[1] * (q[0] - p[0])) / L <= PARALLEL_SIN

    def collinear_interval(self, p, q, tol=ON_LINE_TOL_FT):
        if abs(self.d(p)) > tol or abs(self.d(q)) > tol:
            return None
        return self.clip(self.t(p), self.t(q))

    def strip(self, t0, t1, depth):
        o = self.n_out
        p0, p1 = self.at(t0), self.at(t1)
        return Polygon([p0, p1, (p1[0] + o[0] * depth, p1[1] + o[1] * depth),
                        (p0[0] + o[0] * depth, p0[1] + o[1] * depth)])

    def projection(self, geom):
        if geom is None or geom.is_empty:
            return None
        coords = []
        for g in getattr(geom, "geoms", [geom]):
            if hasattr(g, "exterior"):
                coords += list(g.exterior.coords)
            else:
                coords += list(g.coords)
        if not coords:
            return None
        ts = [self.t(c) for c in coords]
        return self.clip(min(ts), max(ts))


def _overlap(iv, jv) -> float:
    return max(0.0, min(iv[1], jv[1]) - max(iv[0], jv[0]))


class _Evidence:
    """Everything boundary classification may use, gathered once per model (LOCAL feet)."""

    def __init__(self, model):
        plan = [e for e in model.elements if _live(e) and e.properties.get("view_context") != "non_plan"]
        self.walls = [(p, q, w.uid) for w in plan if w.category == "wall" for p, q in _segments_of(w.geometry)]
        wm = model.wall_model or {}
        elem_uid = {e.id: e.uid for e in model.elements}
        self.bands = [(tuple(w["centerline"][0]), tuple(w["centerline"][1]), w["thickness_ft"], w.get("uid"),
                       sorted({elem_uid.get(f["element_id"]) for f in w["faces"] if elem_uid.get(f["element_id"])}))
                      for w in wm.get("walls", []) if w["kind"] == "straight"]
        self.doors = [(d, _footprint(d)) for d in plan if d.category == "door"]
        self.doors = [(d, f) for d, f in self.doors if f is not None]
        self.windows = [(w, _footprint(w)) for w in plan if w.category == "window"]
        self.windows = [(w, f) for w, f in self.windows if f is not None]
        wall_uid = {w["id"]: w.get("uid") for w in wm.get("walls", [])}
        self.doorless = [(tuple(o["span"][0]), tuple(o["span"][1]), o["thickness_ft"], o["id"],
                          sorted(u for u in (wall_uid.get(i) for i in o["wall_ids"]) if u))
                         for o in wm.get("openings", []) if o["kind"] == "doorless"]
        self.wall_openings = [(LineString(o["span"]).buffer(max(0.3, o["thickness_ft"])), o["id"])
                              for o in wm.get("openings", [])]
        self.element_by_id = {e.id: e for e in model.elements}


def _window_faces(edge: _Edge, fp) -> bool:
    """The window's centre lies OUTSIDE the edge, no deeper than the window strip."""
    c = fp.centroid
    return 0.0 <= -edge.d((c.x, c.y)) <= WINDOW_STRIP_FT


def _wall_line_at(edge: _Edge, ev: _Evidence, pt) -> bool:
    """Wall linework parallel to ``edge`` ends at (or passes through) ``pt``."""
    for p, q, _uid in ev.walls:
        if edge.parallel(p, q) and LineString([p, q]).distance(Point(pt)) <= ON_LINE_TOL_FT:
            return True
    return False


def _classify_edge(edge: _Edge, ev: _Evidence, closures) -> list[dict]:
    """Ordered pieces of one edge: {t0, t1, kind, rule, ref, derived_from}."""
    marks = []   # (t0, t1, cls, rule, ref, derived)
    for p, q, uid in ev.walls:
        iv = edge.collinear_interval(p, q)
        if iv:
            marks.append((*iv, "wall", "B-WALL-FACE", None, [uid] if uid else []))
    edge_has_wall = bool(marks)
    for c in closures:
        iv = edge.collinear_interval(c["from"], c["to"])
        if iv and (edge_has_wall or _wall_line_at(edge, ev, edge.at(iv[0])) or _wall_line_at(edge, ev, edge.at(iv[1]))):
            marks.append((*iv, "door_opening", "B-DOOR-CLOSURE", c["door_uid"], [c["door_uid"]]))
    for c0, c1, th, uid, faces in ev.bands:
        if not edge.parallel(c0, c1):
            continue
        if abs(edge.d(c0)) <= th / 2 + ON_LINE_TOL_FT and abs(edge.d(c1)) <= th / 2 + ON_LINE_TOL_FT:
            iv = edge.clip(edge.t(c0), edge.t(c1))
            if iv:
                marks.append((*iv, "wall", "B-WALL-BAND", None, ([uid] if uid else []) + faces))
    for w, fp in ev.windows:
        if not _window_faces(edge, fp):
            continue
        iv = edge.projection(fp.intersection(edge.strip(0.0, edge.L, WINDOW_STRIP_FT)))
        if iv and iv[1] - iv[0] >= WINDOW_MIN_FT:
            marks.append((*iv, "window", "B-WINDOW", w.uid, [w.uid] if w.uid else []))

    cuts = sorted({0.0, edge.L} | {m[0] for m in marks} | {m[1] for m in marks})
    pieces = []
    for t0, t1 in zip(cuts, cuts[1:], strict=False):
        if t1 - t0 <= 1e-9:
            continue
        mid = (t0 + t1) / 2
        here = [m for m in marks if m[0] - 1e-9 <= mid <= m[1] + 1e-9]
        has = {m[2] for m in here}
        if "door_opening" in has:
            cls = "door_opening"
        elif "window" in has and "wall" in has:
            cls = "window"                      # glazing inside a wall: still encloses the region
        elif "wall" in has:
            cls = "wall"
        else:
            cls = "gap"                          # classified below as one run
        chosen = [m for m in here if m[2] == cls]
        pieces.append({"t0": t0, "t1": t1, "kind": cls,
                       "rule": min((m[3] for m in chosen), default=None, key=lambda r: -CONF.get(r, 0)),
                       "ref": next((m[4] for m in chosen if m[4]), None),
                       "derived_from": sorted({u for m in chosen for u in m[5]})})

    # uncovered runs: decide once per run, from door / doorless / window evidence spanning it
    out, i = [], 0
    while i < len(pieces):
        p = pieces[i]
        if p["kind"] != "gap":
            out.append(p)
            i += 1
            continue
        j = i
        while j + 1 < len(pieces) and pieces[j + 1]["kind"] == "gap":
            j += 1
        run = (pieces[i]["t0"], pieces[j]["t1"])
        in_wall_line = (any(q["kind"] in ("wall", "window") for q in pieces)
                        or _wall_line_at(edge, ev, edge.at(run[0])) or _wall_line_at(edge, ev, edge.at(run[1])))
        out.append({"t0": run[0], "t1": run[1],
                    **(_classify_gap(edge, run, ev) if in_wall_line else
                       {"kind": "unknown", "rule": "B-UNKNOWN", "ref": None, "derived_from": []})})
        i = j + 1
    return _tidy(out)


def _classify_gap(edge: _Edge, run, ev: _Evidence) -> dict:
    need = GAP_EVIDENCE_OVERLAP * (run[1] - run[0])
    zone = edge.strip(run[0], run[1], 0.0).buffer(0.75) if run[1] > run[0] else None
    if run[1] - run[0] <= DOOR_GAP_MAX_FT:
        best = None
        for d, fp in ev.doors:
            if zone is None or not fp.intersects(zone):
                continue
            iv = edge.projection(fp)
            ov = _overlap(iv, run) if iv else 0.0
            if ov >= need and (best is None or (ov, d.uid or "") > (best[0], best[1].uid or "")):
                best = (ov, d)
        if best:
            d = best[1]
            return {"kind": "door_opening", "rule": "B-DOOR-GAP", "ref": d.uid, "derived_from": [d.uid] if d.uid else []}
    for s0, s1, th, oid, walls in ev.doorless:
        if not edge.parallel(s0, s1) or abs(edge.d(s0)) > th / 2 + 0.5 or abs(edge.d(s1)) > th / 2 + 0.5:
            continue
        iv = edge.clip(edge.t(s0), edge.t(s1))
        if iv and _overlap(iv, run) >= need:
            return {"kind": "open_opening", "rule": "B-OPEN-OPENING", "ref": "doorless|" + "|".join(walls or [oid]),
                    "derived_from": walls, "wall_analysis_opening": oid}
    for w, fp in ev.windows:
        if not _window_faces(edge, fp):
            continue
        iv = edge.projection(fp.intersection(edge.strip(run[0], run[1], WINDOW_STRIP_FT)))
        if iv and _overlap(iv, run) >= need:
            return {"kind": "window", "rule": "B-WINDOW", "ref": w.uid, "derived_from": [w.uid] if w.uid else []}
    return {"kind": "unknown", "rule": "B-UNKNOWN", "ref": None, "derived_from": []}


def _tidy(pieces: list[dict]) -> list[dict]:
    """Absorb numerical slivers and merge neighbours of the same kind and reference."""
    for p in pieces:
        if p["kind"] == "gap":
            p.update(kind="unknown", rule="B-UNKNOWN")
    changed = True
    while changed and len(pieces) > 1:
        changed = False
        for k, p in enumerate(pieces):
            if p["t1"] - p["t0"] < MIN_PIECE_FT:
                nb = pieces[k - 1] if k > 0 else pieces[k + 1]
                nb["t0"], nb["t1"] = min(nb["t0"], p["t0"]), max(nb["t1"], p["t1"])
                del pieces[k]
                changed = True
                break
    merged: list[dict] = []
    for p in pieces:
        q = merged[-1] if merged else None
        if q and q["kind"] == p["kind"] and q["ref"] == p["ref"]:
            q["t1"] = p["t1"]
            q["derived_from"] = sorted(set(q["derived_from"]) | set(p["derived_from"]))
            if CONF.get(p["rule"], 0) < CONF.get(q["rule"], 0):
                q["rule"] = p["rule"]          # a merged piece is only as certain as its weakest part
        else:
            merged.append(dict(p))
    return merged


def _merge_collinear(raw: list[dict]) -> list[dict]:
    """Join consecutive pieces of the same kind and reference whose shared vertex lies on the line
    through their outer ends (within 1e-6 ft): the boundary still reproduces the polygon exactly,
    it just does not split a straight wall at a collinear vertex. The ring start is never merged
    across, so segment order stays deterministic."""
    out: list[dict] = []
    for p in raw:
        q = out[-1] if out else None
        if q and q["kind"] == p["kind"] and q["ref"] == p["ref"]:
            ln = LineString([q["start"], p["end"]])
            if ln.length > 1e-9 and ln.distance(Point(q["end"])) <= 1e-6:
                q["end"] = p["end"]
                q["derived_from"] = sorted(set(q["derived_from"]) | set(p["derived_from"]))
                if CONF.get(p["rule"], 0) < CONF.get(q["rule"], 0):
                    q["rule"] = p["rule"]
                continue
        out.append(dict(p))
    return out


def _canonical_ring(points) -> list[tuple[float, float]] | None:
    poly = Polygon(points)
    if not poly.is_valid or poly.area <= 0:
        return None
    ring = list(orient(poly, 1.0).exterior.coords)[:-1]
    dedup = [p for i, p in enumerate(ring) if p != ring[i - 1]]
    k = min(range(len(dedup)), key=lambda i: (round(dedup[i][1], 6), round(dedup[i][0], 6)))
    return dedup[k:] + dedup[:k]


def classify_region_boundaries(model, source_uid: str | None, engine_version: str) -> list[Issue]:
    """Sets ``room.boundary`` for every physical region with polygon geometry and appends
    `opening` elements. Returns review issues."""
    ev = _Evidence(model)
    uid_of_id = {e.id: e.uid for e in model.elements}
    rooms = [r for r in model.elements_of("room") if r.geometry and len(r.geometry.points) >= 3]
    unknown_rooms = []
    groups: dict[tuple, list] = defaultdict(list)            # opening key -> [(room, segment, piece)]
    for room in rooms:
        ring = _canonical_ring(room.geometry.points)
        if ring is None:
            room.evidence.append("boundary not classified: the region polygon is not a valid simple polygon")
            continue
        closures = [{"from": tuple(c["from"]), "to": tuple(c["to"]), "door_uid": uid_of_id.get(c["door_id"])}
                    for c in room.properties.get("door_closures", [])]
        raw = []
        for i, a in enumerate(ring):
            edge = _Edge(a, ring[(i + 1) % len(ring)])
            if edge.L <= 1e-9:
                continue
            for piece in _classify_edge(edge, ev, closures):
                raw.append({**piece, "start": edge.at(piece["t0"]), "end": edge.at(piece["t1"])})
        segs, idx = [], 0
        for piece in _merge_collinear(raw):
            kind, rule = piece["kind"], piece["rule"]
            conf = min(CONF[rule], room.confidence) if kind != "unknown" else 0.0
            start, end = piece["start"], piece["end"]
            seg = BoundarySegment(
                uid=uid_for(source_uid, f"boundary|{room.uid}|{idx}|{kind}|{start[0]:.6f},{start[1]:.6f}|"
                                        f"{end[0]:.6f},{end[1]:.6f}") if source_uid and room.uid else None,
                index=idx, kind=kind, encloses=ENCLOSES[kind], start=start, end=end,
                length_ft=round(math.dist(start, end), 6),
                fill_element_uid=piece["ref"] if kind in ("door_opening", "window") else None,
                derived_from=piece["derived_from"], confidence=round(conf, 3),
                requires_verification=conf < VERIFY_BELOW, rules=[rule])
            segs.append(seg)
            if kind in ("door_opening", "window", "open_opening") and piece["ref"]:
                groups[(kind, piece["ref"])].append((room, seg, piece))
            idx += 1
        by_kind: dict[str, float] = defaultdict(float)
        for s in segs:
            by_kind[s.kind] += s.length_ft
        complete = by_kind.get("unknown", 0.0) == 0.0
        room.boundary = RegionBoundary(
            rings=[BoundaryRing(role="outer", orientation="ccw", segments=segs)], complete=complete,
            length_by_kind_ft={k: round(v, 4) for k, v in sorted(by_kind.items())},
            rules=["B-BOUNDARY-CLASSIFY"] + sorted({r for s in segs for r in s.rules}),
            engine_version=engine_version)
        if not complete and _live(room):
            unknown_rooms.append(room)

    _openings(model, groups, ev, source_uid, engine_version)
    issues = []
    if unknown_rooms:
        issues.append(Issue(
            code="REGION_BOUNDARY_UNCLASSIFIED", severity="warning", element_ids=[r.id for r in unknown_rooms],
            message=f"{len(unknown_rooms)} physical region(s) have boundary portions with no evidence of a wall or an "
                    "opening; they are classified UNKNOWN (not assumed to be walls): "
                    + ", ".join(f"{r.id} ({r.boundary.length_by_kind_ft.get('unknown', 0):.1f} ft)" for r in unknown_rooms)))
    return issues


def _openings(model, groups, ev: _Evidence, source_uid, engine_version) -> None:
    by_uid = {e.uid: e for e in model.elements if e.uid}
    ent_uid = {e.id: e.uid for e in model.entities}
    n = sum(1 for e in model.elements if e.category == "opening")
    for (kind, ref) in sorted(groups, key=lambda k: (k[0], str(k[1]))):
        members = groups[(kind, ref)]
        segs = [s for _r, s, _p in members]
        rooms = sorted({r.uid for r, _s, _p in members if r.uid})
        pts = [p for s in segs for p in (s.start, s.end)]
        hull = MultiPoint(pts).convex_hull
        fill = by_uid.get(ref) if kind != "open_opening" else None
        subtype = {"door_opening": "door", "window": "window", "open_opening": "open"}[kind]
        depth = None
        if len(segs) >= 2:
            e0 = _Edge(segs[0].start, segs[0].end)
            others = [abs(e0.d(s.start)) for s in segs[1:] if e0.parallel(s.start, s.end)]
            depth = round(max(others), 4) if others else None
        dups = sorted(oid for g, oid in ev.wall_openings if g.intersects(hull.buffer(0.05)))
        wa = next((p["wall_analysis_opening"] for _r, _s, p in members if p.get("wall_analysis_opening")), None)
        conf = min(s.confidence for s in segs)
        n += 1
        # provenance.derived_from = uids of the SOURCE entities (model-wide invariant); the upstream
        # elements it was derived from (door/window, wall linework, wall-analysis pieces) are listed
        # in properties["derived_from_elements"]
        src_ids = list(fill.source_entity_ids) if fill else []
        derived = [ent_uid[i] for i in src_ids if ent_uid.get(i)]
        derived_elements = sorted({u for s in segs for u in s.derived_from})
        el = BuildingElement(
            id=f"OP{n:05d}", category="opening", subtype=subtype, label=None,
            uid=uid_for(source_uid, f"opening|{kind}|{ref}") if source_uid else None,
            confidence=round(conf, 3),
            evidence=[f"{len(segs)} boundary segment(s) of {len(rooms)} physical region(s) cross this {subtype} opening",
                      (f"filled by {fill.category} {fill.id}" if fill else
                       f"doorless opening found by the wall analysis ({wa})")]
                     + ([f"the wall analysis reported {len(dups)} overlapping gap(s) here ({', '.join(dups)}); "
                         "they are the same physical opening"] if len(dups) > 1 else []),
            rules=["O-OPENING"], source_entity_ids=src_ids,
            requires_verification=any(s.requires_verification for s in segs) or kind == "open_opening",
            geometry=(Geometry(kind="polygon", points=[tuple(c) for c in hull.exterior.coords[:-1]], closed=True,
                               frame="LOCAL") if hull.geom_type == "Polygon" else
                      Geometry(kind="line", points=[tuple(c) for c in hull.coords], frame="LOCAL")),
            properties={"passable": kind != "window", "width_ft": round(max(s.length_ft for s in segs), 4),
                        "depth_ft": depth, "fill_element_uid": fill.uid if fill else None,
                        "fill_category": fill.category if fill else None,
                        "connects_region_uids": rooms, "boundary_segment_uids": [s.uid for s in segs],
                        "analysis_gap_ids": dups, "derived_from_elements": derived_elements,
                        "view_region": (fill.properties.get("view_region") if fill else None)
                        or next((r.properties.get("view_region") for r, _s, _p in members), None),
                        "view_context": "plan",
                        "vertical_extent": {"status": "unknown", "datum": None, "bottom_ft": None, "top_ft": None}},
            provenance=Provenance(origin="deterministic_inference", engine="fireai.interpret",
                                  engine_version=engine_version, rule_ids=["O-OPENING"], derived_from=derived),
        )
        model.elements.append(el)
        for s in segs:
            s.opening_uid = el.uid
