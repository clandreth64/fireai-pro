"""Array-direction and vertical measurements (Milestone 2.2A). Pure geometry, no NFPA content.

Everything is computed in the ROOM FRAME (u = long axis, v = perpendicular), the frame the arrays
are defined in, so a rotated room gives the same result as an axis-aligned one.

* ``ray_hit``: from a sprinkler, follow +/-u or +/-v to the FIRST boundary segment reached. The hit is
  usable only if that segment is PERPENDICULAR to the direction (within the explicit length
  tolerance) and of a PARTICIPATING kind (the rule decides: a door opening, window, open opening
  or unknown segment is never silently a wall). Otherwise the measurement is NOT EVALUABLE.
* ``array_sxl`` (SXL-ARRAY/1): per sprinkler, S along u and L along v; each is the larger of its two
  sides, a side being the distance to the adjacent sprinkler or, with none, twice the perpendicular
  distance to the participating wall reference reached on that side.
* ``perpendicular_walls`` (PERP-WALL/1): per sprinkler, the perpendicular distance to the wall
  reference in every array direction with no adjacent sprinkler.
* ``ceiling_to_deflector``: ceiling elevation - deflector elevation on one explicit datum.

A NOT EVALUABLE outcome is never a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DIRECTIONS = (("u", -1), ("u", +1), ("v", -1), ("v", +1))


@dataclass(frozen=True)
class UVSegment:
    a: tuple[float, float]
    b: tuple[float, float]
    kind: str
    uid: Optional[str]
    index: int


@dataclass
class WallHit:
    status: str                                   # ok | non_participating | angled | none
    distance: Optional[float] = None
    kind: Optional[str] = None
    segment_uid: Optional[str] = None
    tied_kinds: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        return {"status": self.status, "distance_ft": self.distance, "kind": self.kind,
                "segment_uid": self.segment_uid, **({"tied_kinds": self.tied_kinds} if self.tied_kinds else {})}


@dataclass
class Measured:
    value: Optional[float]                        # worst value over the subjects (None = nothing to measure)
    subject: Optional[dict] = None
    not_evaluable: Optional[str] = None           # reason; the measurement could not be made


def boundary_uv(frame, segments) -> list[UVSegment]:
    return [UVSegment(frame.to_uv(*s.start_local_ft), frame.to_uv(*s.end_local_ft), s.kind, s.uid, s.index)
            for s in segments]


def misaligned_segments(bsegs: list[UVSegment], tol: float) -> list[UVSegment]:
    """Segments that are neither along u nor along v (angled / irregular) beyond the length tolerance."""
    return [s for s in bsegs if abs(s.b[0] - s.a[0]) > tol and abs(s.b[1] - s.a[1]) > tol]


def ray_hit(bsegs: list[UVSegment], u0: float, v0: float, axis: str, sign: int, kinds: list[str],
            tol: float) -> WallHit:
    ia, ib = (0, 1) if axis == "u" else (1, 0)          # ia: along the ray, ib: across it
    p_along, p_across = (u0, v0) if axis == "u" else (v0, u0)
    cands = []
    for s in bsegs:
        a_al, a_ac, b_al, b_ac = s.a[ia], s.a[ib], s.b[ia], s.b[ib]
        if abs(b_ac - a_ac) <= 1e-12:                    # parallel to the ray: never the reference reached
            continue
        lo, hi = min(a_ac, b_ac), max(a_ac, b_ac)
        if not (lo - 1e-9 <= p_across <= hi + 1e-9):
            continue
        t = (p_across - a_ac) / (b_ac - a_ac)
        d = sign * (a_al + t * (b_al - a_al) - p_along)
        if d > 1e-9:
            cands.append((d, s))
    if not cands:
        return WallHit(status="none")
    dmin = min(d for d, _s in cands)
    ties = sorted((s for d, s in cands if d <= dmin + 1e-9), key=lambda s: s.index)
    perp = [s for s in ties if abs(s.b[ia] - s.a[ia]) <= tol]
    if not perp:
        return WallHit(status="angled", distance=dmin, kind=ties[0].kind, segment_uid=ties[0].uid,
                       tied_kinds=sorted({s.kind for s in ties}))
    part = [s for s in perp if s.kind in kinds]
    if not part:
        return WallHit(status="non_participating", distance=dmin, kind=perp[0].kind, segment_uid=perp[0].uid,
                       tied_kinds=sorted({s.kind for s in ties}))
    return WallHit(status="ok", distance=dmin, kind=part[0].kind, segment_uid=part[0].uid,
                   tied_kinds=sorted({s.kind for s in ties}) if len(ties) > 1 else [])


def _side(coords: list[float], k: int, sign: int) -> Optional[float]:
    j = k + sign
    return abs(coords[j] - coords[k]) if 0 <= j < len(coords) else None


def _explain_miss(hit: WallHit, axis: str, sign: int, kinds: list[str]) -> str:
    d = f"{'+' if sign > 0 else '-'}{axis}"
    if hit.status == "angled":
        return (f"the boundary reached in direction {d} is not perpendicular to it (angled / irregular wall): "
                "straight-wall logic is not applied")
    if hit.status == "non_participating":
        return (f"the boundary reached in direction {d} is {hit.kind!r} (tied: {hit.tied_kinds or [hit.kind]}), not "
                f"a participating reference kind {kinds}")
    return f"no boundary is reached in direction {d}"


def array_sxl(bsegs, us: list[float], vs: list[float], kinds: list[str], tol: float, worst) -> Measured:
    """SXL-ARRAY/1 over a complete rectangular grid (room-frame coordinates us x vs)."""
    per = []
    for j, v in enumerate(vs):
        for i, u in enumerate(us):
            dims = {}
            for axis, coords, k in (("u", us, i), ("v", vs, j)):
                sides = []
                for sign in (-1, +1):
                    adj = _side(coords, k, sign)
                    if adj is not None:
                        sides.append({"direction": f"{'+' if sign > 0 else '-'}{axis}", "value_ft": adj,
                                      "from": "adjacent_sprinkler"})
                        continue
                    hit = ray_hit(bsegs, u, v, axis, sign, kinds, tol)
                    if hit.status != "ok":
                        return Measured(None, {"sprinkler_index": j * len(us) + i, "wall": hit.describe()},
                                        not_evaluable=_explain_miss(hit, axis, sign, kinds))
                    sides.append({"direction": f"{'+' if sign > 0 else '-'}{axis}", "value_ft": 2.0 * hit.distance,
                                  "from": "twice_wall_distance", "wall": hit.describe()})
                gov = max(sides, key=lambda x: x["value_ft"])
                dims[axis] = {"value_ft": gov["value_ft"], "governed_by": gov["direction"], "sides": sides}
            area = dims["u"]["value_ft"] * dims["v"]["value_ft"]
            per.append((area, j * len(us) + i, dims))
    if not per:
        return Measured(None)
    area, idx, dims = worst(per, key=lambda x: (x[0], -x[1]) if worst is max else (x[0], x[1]))
    return Measured(area, {"sprinkler_index": idx, "S_axis": "u", "L_axis": "v",
                           "S_ft": dims["u"]["value_ft"], "L_ft": dims["v"]["value_ft"],
                           "S": dims["u"], "L": dims["v"],
                           "orientation": "S along the array's room-frame u axis (ROOM-FRAME-LONG-AXIS/1); the "
                                          "S x L product is invariant under the S/L assignment",
                           "per_sprinkler": [{"index": k, "S_ft": dd["u"]["value_ft"], "L_ft": dd["v"]["value_ft"],
                                              "area_sf": a, "S_from": dd["u"]["governed_by"],
                                              "L_from": dd["v"]["governed_by"]}
                                             for a, k, dd in sorted(per, key=lambda x: x[1])]})


def perpendicular_walls(bsegs, us: list[float], vs: list[float], kinds: list[str], tol: float, worst) -> Measured:
    """PERP-WALL/1: distances to the wall reference in each direction without an adjacent sprinkler."""
    per = []
    for j, v in enumerate(vs):
        for i, u in enumerate(us):
            for axis, coords, k in (("u", us, i), ("v", vs, j)):
                for sign in (-1, +1):
                    if _side(coords, k, sign) is not None:
                        continue
                    hit = ray_hit(bsegs, u, v, axis, sign, kinds, tol)
                    if hit.status != "ok":
                        return Measured(None, {"sprinkler_index": j * len(us) + i, "wall": hit.describe()},
                                        not_evaluable=_explain_miss(hit, axis, sign, kinds))
                    per.append((hit.distance, j * len(us) + i, f"{'+' if sign > 0 else '-'}{axis}", hit))
    if not per:
        return Measured(None)
    d, idx, direction, hit = worst(per, key=lambda x: (x[0], -x[1]) if worst is max else (x[0], x[1]))
    return Measured(d, {"sprinkler_index": idx, "direction": direction, "wall": hit.describe(),
                        "per_direction": [{"index": k, "direction": dr, "distance_ft": x, "kind": h.kind}
                                          for x, k, dr, h in sorted(per, key=lambda y: (y[1], y[2]))]})


def ceiling_to_deflector(ceiling_elevation, deflector) -> Measured:
    """Signed vertical distance (positive = deflector below the ceiling) on ONE explicit datum."""
    if ceiling_elevation is None or ceiling_elevation.status != "known":
        return Measured(None, not_evaluable="ceiling elevation is unknown")
    if deflector is None or deflector.elevation.status != "known":
        return Measured(None, not_evaluable="sprinkler deflector elevation (Z) is unknown")
    if deflector.frame != "LOCAL":
        return Measured(None, not_evaluable=f"deflector position is in frame {deflector.frame}, not LOCAL")
    if deflector.elevation.datum != ceiling_elevation.datum:
        return Measured(None, not_evaluable=f"datum mismatch: ceiling {ceiling_elevation.datum!r} vs deflector "
                                            f"{deflector.elevation.datum!r} (no silent conversion)")
    d = ceiling_elevation.value_ft - deflector.elevation.value_ft
    return Measured(d, {"ceiling_elevation_ft": ceiling_elevation.value_ft,
                        "deflector_elevation_ft": deflector.elevation.value_ft, "datum": ceiling_elevation.datum,
                        "sign": "positive = deflector below the ceiling"})
