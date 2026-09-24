"""Deterministic plan geometry for sprinkler evaluation (Milestone 2.0).

Pure mathematics on the engineering contract's LOCAL-frame geometry (feet). Knows nothing about
NFPA 13: it measures what a constraint asks for, exactly (no sampling):

* worst boundary point: the distance from a segment point to the nearest of several points is
  maximal at a segment end or where the segment crosses a perpendicular bisector of two points;
* worst space point: maximal at a polygon vertex, a bisector/edge crossing or a Voronoi vertex
  (circumcentre of three points) inside the space;
* nearest-sprinkler cell: the space clipped by the half-planes closer to each sprinkler.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

from shapely.geometry import Point, Polygon

EPS = 1e-12


@dataclass(frozen=True)
class RoomFrame:
    """Room-aligned frame: u along the space's long axis, v perpendicular (counter-clockwise), origin
    at the minimum (u, v) of the space. Deterministic: the long side of the minimum rotated
    rectangle; for a square, the side direction closest to +x; directions point to +x (or +y)."""
    ux: float
    uy: float
    ou: float
    ov: float
    extent_u: float
    extent_v: float
    strategy: str = "ROOM-FRAME-LONG-AXIS/1"

    def to_uv(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.ux + y * self.uy - self.ou, -x * self.uy + y * self.ux - self.ov)

    def to_xy(self, u: float, v: float) -> tuple[float, float]:
        uu, vv = u + self.ou, v + self.ov
        return (uu * self.ux - vv * self.uy, uu * self.uy + vv * self.ux)


def _canon(dx: float, dy: float) -> tuple[float, float]:
    L = math.hypot(dx, dy)
    dx, dy = dx / L, dy / L
    dx = 0.0 if abs(dx) < 1e-12 else dx
    dy = 0.0 if abs(dy) < 1e-12 else dy
    if dx < 0 or (dx == 0 and dy < 0):
        dx, dy = -dx, -dy
    return dx, dy


def room_frame(points: list[tuple[float, float]]) -> RoomFrame:
    poly = Polygon(points)
    rect = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    e1 = (rect[1][0] - rect[0][0], rect[1][1] - rect[0][1])
    e2 = (rect[2][0] - rect[1][0], rect[2][1] - rect[1][1])
    l1, l2 = math.hypot(*e1), math.hypot(*e2)
    d1, d2 = _canon(*e1), _canon(*e2)
    if abs(l1 - l2) <= 1e-9 * max(l1, l2):
        d = min((d1, d2), key=lambda d: (abs(math.atan2(d[1], d[0])), d[1]))
    else:
        d = d1 if l1 > l2 else d2
    ux, uy = d
    us = [x * ux + y * uy for x, y in points]
    vs = [-x * uy + y * ux for x, y in points]
    return RoomFrame(ux, uy, min(us), min(vs), max(us) - min(us), max(vs) - min(vs))


def dist_point_segment(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= EPS:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _nearest(q, pts) -> float:
    return min(math.dist(q, p) for p in pts)


def worst_boundary_point(segments: list[tuple[tuple, tuple]], pts: list[tuple]) -> tuple[float, tuple | None]:
    """max over all points on the segments of the distance to the nearest point in ``pts``."""
    best, where = -1.0, None
    for a, b in segments:
        cands = [a, b]
        dx, dy = b[0] - a[0], b[1] - a[1]
        for p, q in combinations(pts, 2):
            # bisector of p,q: (x - m)·(q - p) = 0 ; segment a + t (b - a)
            nx, ny = q[0] - p[0], q[1] - p[1]
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            den = dx * nx + dy * ny
            if abs(den) > EPS:
                t = ((mx - a[0]) * nx + (my - a[1]) * ny) / den
                if 0.0 <= t <= 1.0:
                    cands.append((a[0] + t * dx, a[1] + t * dy))
        for c in cands:
            d = _nearest(c, pts)
            if d > best:
                best, where = d, c
    return best, where


def _circumcentre(a, b, c):
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) <= EPS:
        return None
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    return ((a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d,
            (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d)


def worst_space_point(polygon: list[tuple], pts: list[tuple]) -> tuple[float, tuple | None]:
    """max over all points of the polygon (boundary and interior) of the distance to the nearest point."""
    ring = list(polygon)
    edges = [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]
    best, where = worst_boundary_point(edges, pts)
    poly = Polygon(ring)
    for a, b, c in combinations(pts, 3):
        cc = _circumcentre(a, b, c)
        if cc is not None and poly.covers(Point(cc)):
            d = _nearest(cc, pts)
            if d > best + 1e-12:
                best, where = d, cc
    return best, where


def nearest_cells(polygon: list[tuple], pts: list[tuple]) -> list:
    """Voronoi cells of ``pts`` clipped to the polygon (shapely geometries, in input order)."""
    poly = Polygon(polygon)
    minx, miny, maxx, maxy = poly.bounds
    big = 4 * max(maxx - minx, maxy - miny, 1.0)
    cells = []
    for i, p in enumerate(pts):
        cell = poly
        for j, q in enumerate(pts):
            if i == j:
                continue
            nx, ny = q[0] - p[0], q[1] - p[1]
            L = math.hypot(nx, ny)
            nx, ny = nx / L, ny / L
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            tx, ty = -ny, nx                     # along the bisector
            half = Polygon([(mx + tx * big, my + ty * big), (mx - tx * big, my - ty * big),
                            (mx - tx * big - nx * big, my - ty * big - ny * big),
                            (mx + tx * big - nx * big, my + ty * big - ny * big)])
            cell = cell.intersection(half)
        cells.append(cell)
    return cells
