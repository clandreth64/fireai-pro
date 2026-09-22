"""Room/space post-analysis (Milestone 1.6), run after the wall analysis layer.

Adds evidence to room elements; it never creates, merges or splits rooms:

* R-CONNECTIVITY    each wall opening (door or doorless) is linked to the room
                    on either side of it -> ``model.wall_model["room_connections"]``
                    and ``room.properties["openings"]``.
* R-SPLIT-CANDIDATE for a room flagged ``suspected_merged_region`` (several
                    names in one boundary), closing its DOORLESS openings (with
                    analysis lines across the opening) is tried; if that yields
                    faces with at most one label each, they are recorded as
                    ``split_candidates`` for a person to accept. Not applied.
                    Labels alone never split a space.
* R-SHAPE           shape hints from geometry only: corridor-like (long and
                    narrow), rectangular vs irregular. Hints, not room types.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

CORRIDOR_MIN_ELONGATION = 4.0
CORRIDOR_MAX_WIDTH_FT = 10.0
RECT_FILL = 0.98


def _poly(el) -> Polygon | None:
    g = el.geometry
    if g is None or len(g.points) < 3:
        return None
    p = Polygon(g.points)
    return p if p.is_valid and p.area > 0 else p.buffer(0)


def shape_hints(poly: Polygon) -> dict:
    mrr = poly.minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    sides = sorted(math.dist((xs[i], ys[i]), (xs[i + 1], ys[i + 1])) for i in range(2))
    short, long_ = sides
    fill = poly.area / mrr.area if mrr.area else 0.0
    hints = {"vertex_count": len(poly.exterior.coords) - 1, "mrr_short_ft": round(short, 3),
             "mrr_long_ft": round(long_, 3), "rectangularity": round(fill, 4),
             "shape": "rectangular" if fill >= RECT_FILL else "irregular"}
    mean_width = 2 * poly.area / poly.length if poly.length else 0.0
    if short and long_ / short >= CORRIDOR_MIN_ELONGATION and mean_width <= CORRIDOR_MAX_WIDTH_FT:
        hints["corridor_like"] = True
    return hints


def annotate_rooms(model) -> list:
    """Returns review issues. Mutates room element properties and wall_model."""
    from fireai.model import Issue
    wm = model.wall_model or {}
    rooms = [(el, _poly(el)) for el in model.elements_of("room")]
    rooms = [(el, p) for el, p in rooms if p is not None]
    issues = []
    for el, p in rooms:
        # annotation only: element rules/provenance (and so its uid basis) are left unchanged
        el.properties["shape_hints"] = {**shape_hints(p), "rules": ["R-SHAPE"]}

    # connectivity through openings
    connections = []
    for op in wm.get("openings", []):
        (x0, y0), (x1, y1) = op["span"]
        L = math.dist((x0, y0), (x1, y1)) or 1.0
        nx, ny = -(y1 - y0) / L, (x1 - x0) / L
        cx, cy = op["center"]
        d = op["thickness_ft"] / 2 + 0.5
        sides = []
        for sgn in (1, -1):
            q = Point(cx + sgn * nx * d, cy + sgn * ny * d)
            hit = [el for el, poly in rooms if poly.contains(q)]
            sides.append(min(hit, key=lambda e: e.properties.get("area_sf", 0)).id if hit else None)
        same = sides[0] is not None and sides[0] == sides[1]
        connections.append({"opening_id": op["id"], "kind": op["kind"], "rooms": sides,
                            "opening_inside_one_room": same, "rules": ["R-CONNECTIVITY"]})
        for rid in {s for s in sides if s}:
            room = next(e for e, _ in rooms if e.id == rid)
            room.properties.setdefault("openings", []).append(
                {"opening_id": op["id"], "kind": op["kind"], "width_ft": op["width_ft"],
                 "connects_to": [s for s in sides if s and s != rid] or None,
                 "within_this_boundary": same})
    if wm:
        wm["room_connections"] = connections

    # split candidates for suspected merged regions
    ops = {o["id"]: o for o in wm.get("openings", [])}
    by_id = {e.id: e for e in model.entities}
    for el, poly in rooms:
        if el.subtype != "suspected_merged_region":
            continue
        inside = [ops[c["opening_id"]] for c in connections
                  if c["opening_inside_one_room"] and c["rooms"][0] == el.id and ops[c["opening_id"]]["kind"] == "doorless"]
        if not inside:
            el.evidence.append("no doorless wall opening with wall faces on both sides was found inside this boundary; "
                               "no split candidate")
            continue
        lines = [LineString(poly.exterior.coords)]
        for w in wm.get("walls", []):
            if w["kind"] == "straight":
                for f in w["faces"]:
                    seg = LineString(f["segment"])
                    if seg.intersects(poly):
                        lines.append(seg)
        for op in inside:
            (x0, y0), (x1, y1) = op["span"]
            L = math.dist((x0, y0), (x1, y1)) or 1.0
            nx, ny = -(y1 - y0) / L, (x1 - x0) / L
            h = op["thickness_ft"] / 2
            for sgn in (1, -1):   # close both wall faces across the opening
                lines.append(LineString([(x0 + sgn * nx * h, y0 + sgn * ny * h), (x1 + sgn * nx * h, y1 + sgn * ny * h)]))
        faces = [f for f in polygonize(unary_union(lines)) if poly.buffer(0.05).contains(f) and f.area >= 20.0]
        labels = [by_id[i] for i in el.properties.get("label_entity_ids", []) if i in by_id]
        cand = []
        for f in faces:
            inn = [t.normalized.text for t in labels if t.normalized and t.normalized.insert
                   and f.contains(Point(t.normalized.insert))]
            cand.append({"polygon": [list(c) for c in f.exterior.coords[:-1]], "area_sf": round(f.area, 2),
                         "labels": inn})
        labelled = [c for c in cand if c["labels"]]
        if len(labelled) >= 2 and all(len(c["labels"]) <= 1 for c in cand):
            el.properties["split_candidates"] = {
                "status": "suggested_not_applied", "rules": ["R-SPLIT-CANDIDATE"],
                "closed_openings": [op["id"] for op in inside], "faces": cand, "frame": "LOCAL",
                "note": "Analysis only: closing the doorless opening(s) would separate the labels. A person must "
                        "decide whether these are separate rooms or one open space."}
            issues.append(Issue(code="ROOM_SPLIT_CANDIDATE", element_ids=[el.id],
                                message=f"{el.id}: closing {len(inside)} doorless opening(s) would separate its "
                                        f"{len(labelled)} labelled parts; review whether they are separate rooms."))
        else:
            el.evidence.append(f"closing {len(inside)} doorless opening(s) does not separate the labels cleanly; "
                               "no split candidate")
    return issues
