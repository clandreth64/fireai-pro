"""Deterministic source-geometry extraction from a DXF document (ezdxf).

Output is an inventory of what the drawing actually contains. No semantic
interpretation happens here.

* Coordinates are converted to WCS (handles OCS / mirrored entities).
* INSERTs are recorded as entities AND exploded into child entities with
  ``parent_id``/``block_path`` lineage (nested blocks and MINSERT arrays included).
* Entities FireAI cannot represent are kept in the inventory with
  ``supported=False`` and reported — never silently dropped.
* Paper-space TEXT/MTEXT/INSERT/VIEWPORT are inventoried (for title blocks and
  viewport scales) but excluded from model-space bounds and normalization.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ezdxf import path as ezpath
from ezdxf import recover
from ezdxf.document import Drawing
from ezdxf.lldxf.const import DXFStructureError

from fireai.errors import FailureCode, PipelineFailure
from fireai.model import Bounds, Geometry, Issue, SourceEntity, Transform

SUPPORTED_TYPES = {
    "LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE",
    "TEXT", "MTEXT", "ATTRIB", "DIMENSION", "HATCH", "POINT", "INSERT", "SOLID", "TRACE",
}
MAX_BLOCK_DEPTH = 16


@dataclass
class Extraction:
    doc: Drawing
    entities: list[SourceEntity]
    layer_table: dict[str, dict]
    block_inserts: Counter
    xref_blocks: set[str]
    unsupported: Counter
    warnings: list[Issue] = field(default_factory=list)
    audit_errors: list[str] = field(default_factory=list)
    audit_fixes: int = 0
    viewport_scales: list[dict] = field(default_factory=list)


# ── loading ──────────────────────────────────────────────────────────────────

def load_dxf(path: Path) -> tuple[Drawing, list[str], int]:
    """Load with ezdxf's recover mode. Structural corruption -> INVALID_DRAWING."""
    try:
        doc, auditor = recover.readfile(str(path))
    except (DXFStructureError, IOError, UnicodeDecodeError, ValueError) as exc:
        raise PipelineFailure(FailureCode.INVALID_DRAWING, f"DXF could not be read: {exc}") from exc
    except Exception as exc:  # any parser crash is an invalid drawing, not a success
        raise PipelineFailure(FailureCode.INVALID_DRAWING, f"DXF parser error: {type(exc).__name__}: {exc}") from exc
    errors = [f"{e.code}: {e.message}" for e in auditor.errors]
    return doc, errors, len(auditor.fixes)


# ── geometry helpers ─────────────────────────────────────────────────────────

def _xy(v) -> tuple[float, float]:
    return (float(v[0]), float(v[1]))


def _flatten(entity, rel_tol: float = 0.002) -> list[tuple[float, float]]:
    p = ezpath.make_path(entity)
    pts = list(p.control_vertices()) or [p.start]
    xs = [v.x for v in pts]; ys = [v.y for v in pts]
    size = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) if pts else 0.0
    dist = max(size * rel_tol, 1e-9)
    return [_xy(v) for v in p.flattening(dist)]


def _text_anchor(e) -> tuple[float, float]:
    d = e.dxf
    use_align = (d.get("halign", 0) or d.get("valign", 0)) and d.hasattr("align_point")
    p = d.align_point if use_align else d.insert
    return _xy(e.ocs().to_wcs(p))


def _geometry(e) -> tuple[Geometry | None, dict]:
    """Return (source geometry in WCS drawing units, attributes)."""
    t = e.dxftype()
    attrs: dict = {}
    if t == "LINE":
        return Geometry(kind="line", points=[_xy(e.dxf.start), _xy(e.dxf.end)]), attrs
    if t == "LWPOLYLINE":
        has_bulge = any(abs(b) > 1e-12 for *_, b in e.get_points("xyb"))
        attrs["has_arc_segments"] = has_bulge
        pts = _flatten(e) if has_bulge else [_xy(v) for v in e.vertices_in_wcs()]
        return Geometry(kind="polyline", points=pts, closed=bool(e.closed)), attrs
    if t == "POLYLINE":
        if e.is_poly_face_mesh or e.is_polygon_mesh:
            return None, {"reason": "polyface/polygon mesh not supported"}
        return Geometry(kind="polyline", points=_flatten(e), closed=bool(e.is_closed)), attrs
    if t in ("ARC", "CIRCLE"):
        c = _xy(e.ocs().to_wcs(e.dxf.center))
        pts = _flatten(e)
        g = Geometry(kind="arc" if t == "ARC" else "circle", center=c, radius=float(e.dxf.radius), points=pts)
        if t == "ARC" and pts:
            g.start_angle = math.degrees(math.atan2(pts[0][1] - c[1], pts[0][0] - c[0])) % 360
            g.end_angle = math.degrees(math.atan2(pts[-1][1] - c[1], pts[-1][0] - c[0])) % 360
        if e.dxf.extrusion.z < 0:
            attrs["mirrored_ocs"] = True
        return g, attrs
    if t in ("ELLIPSE", "SPLINE"):
        attrs["approximated"] = True
        closed = bool(getattr(e, "closed", False)) if t == "SPLINE" else False
        return Geometry(kind="polyline", points=_flatten(e), closed=closed), attrs
    if t in ("SOLID", "TRACE"):
        pts = [_xy(e.ocs().to_wcs(e.dxf.get(k))) for k in ("vtx0", "vtx1", "vtx3", "vtx2") if e.dxf.hasattr(k)]
        return Geometry(kind="polyline", points=pts, closed=True), attrs
    if t in ("TEXT", "ATTRIB"):
        return Geometry(kind="text", insert=_text_anchor(e), text=e.plain_text(),
                        height=float(e.dxf.height), rotation=float(e.dxf.get("rotation", 0.0))), attrs
    if t == "MTEXT":
        return Geometry(kind="text", insert=_xy(e.dxf.insert), text=e.plain_text(),
                        height=float(e.dxf.char_height), rotation=float(e.get_rotation())), attrs
    if t == "POINT":
        p = _xy(e.dxf.location)
        return Geometry(kind="point", points=[p], insert=p), attrs
    if t == "HATCH":
        paths = []
        for hp in ezpath.from_hatch(e):
            paths.append([_xy(v) for v in hp.flattening(max(1e-9, _path_size(hp) * 0.002))])
        attrs["pattern"] = e.dxf.pattern_name
        attrs["solid_fill"] = bool(e.dxf.solid_fill)
        return Geometry(kind="hatch", paths=paths), attrs
    if t == "DIMENSION":
        d = e.dxf
        pts = [_xy(d.get(k)) for k in ("defpoint2", "defpoint3") if d.hasattr(k)]
        try:
            m = e.get_measurement()
            measurement = float(m) if isinstance(m, (int, float)) else None
        except Exception:
            measurement = None
        override = d.get("text", "") or ""
        attrs["dimtype"] = int(e.dimtype)
        attrs["text_override"] = override
        try:
            attrs["dimlfac"] = float(e.override().get("dimlfac", 1.0) or 1.0)
        except Exception:
            attrs["dimlfac"] = None
        anchor = _xy(d.text_midpoint) if d.hasattr("text_midpoint") else (pts[0] if pts else None)
        return Geometry(kind="dimension", points=pts, insert=anchor, measurement=measurement,
                        text=override if override not in ("", "<>") else None), attrs
    if t == "INSERT":
        attrs["block"] = e.dxf.name
        attrs["rotation"] = float(e.dxf.get("rotation", 0.0))
        attrs["xscale"] = float(e.dxf.get("xscale", 1.0))
        attrs["yscale"] = float(e.dxf.get("yscale", 1.0))
        if e.attribs:
            attrs["attribs"] = {a.dxf.tag: a.dxf.text for a in e.attribs}
        return Geometry(kind="insert", insert=_xy(e.ocs().to_wcs(e.dxf.insert))), attrs
    return None, {"reason": f"entity type {t} not supported"}


def _path_size(p) -> float:
    pts = list(p.control_vertices())
    if not pts:
        return 0.0
    xs = [v.x for v in pts]; ys = [v.y for v in pts]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def geometry_points(g: Geometry | None) -> list[tuple[float, float]]:
    if g is None:
        return []
    pts = list(g.points)
    for p in g.paths:
        pts.extend(p)
    if g.kind in ("text", "insert", "point") and g.insert is not None and not pts:
        pts.append(g.insert)
    if g.kind == "dimension" and g.insert is not None:
        pts.append(g.insert)  # text/dimension-line location, so the drawn dimension is inside bounds
    return pts


# ── extraction ───────────────────────────────────────────────────────────────

class _Collector:
    def __init__(self, doc: Drawing, max_entities: int):
        self.doc = doc
        self.max_entities = max_entities
        self.entities: list[SourceEntity] = []
        self.unsupported: Counter = Counter()
        self.block_inserts: Counter = Counter()
        self.xref_blocks: set[str] = set()
        self.warnings: list[Issue] = []
        self.layer_state = {}
        for layer in doc.layers:
            self.layer_state[layer.dxf.name.upper()] = not (layer.is_off() or layer.is_frozen())

    def _next_id(self) -> str:
        return f"E{len(self.entities) + 1:06d}"

    def _visible(self, e, parent_visible: bool) -> bool:
        if not parent_visible:
            return False
        if e.dxf.get("invisible", 0):
            return False
        return self.layer_state.get(e.dxf.layer.upper(), True)

    def add(self, e, space: str, parent: SourceEntity | None, block_path: list[str],
            depth: int, parent_visible: bool = True) -> None:
        if len(self.entities) >= self.max_entities:
            raise PipelineFailure(FailureCode.GEOMETRY_EXTRACTION_FAILED,
                                  f"Drawing exceeds the {self.max_entities:,} entity limit for this milestone.")
        t = e.dxftype()
        layer = e.dxf.get("layer", "0")
        # Block-internal entities on layer "0" inherit the INSERT's layer (CAD convention).
        if parent is not None and layer == "0":
            layer = parent.layer
        supported = t in SUPPORTED_TYPES
        geom, attrs = (None, {"reason": f"entity type {t} not supported"})
        if supported:
            try:
                geom, attrs = _geometry(e)
            except Exception as exc:
                geom, attrs = None, {"reason": f"geometry read error: {type(exc).__name__}: {exc}"}
            supported = geom is not None
        if not supported:
            self.unsupported[t] += 1
        visible = self._visible(e, parent_visible)
        ent = SourceEntity(
            id=self._next_id(),
            handle=None if parent is not None else e.dxf.get("handle"),
            type=t, layer=layer, space=space,
            parent_id=parent.id if parent else None,
            block_path=list(block_path),
            visible=visible, supported=supported, source=geom, attributes=attrs,
        )
        self.entities.append(ent)

        if t == "INSERT":
            self._explode(e, ent, space, block_path, depth, visible)

    def _explode(self, e, ent: SourceEntity, space: str, block_path: list[str], depth: int, visible: bool) -> None:
        name = e.dxf.name
        self.block_inserts[name] += 1
        block = self.doc.blocks.get(name)
        if block is None:
            ent.supported = False
            ent.attributes["reason"] = f"block definition '{name}' missing"
            self.warnings.append(Issue(code="MISSING_BLOCK_DEFINITION",
                                       message=f"INSERT references missing block '{name}'.", entity_ids=[ent.id]))
            return
        if block.block_record.is_xref:
            self.xref_blocks.add(name)
            ent.attributes["xref"] = True
            return
        if depth >= MAX_BLOCK_DEPTH:
            self.warnings.append(Issue(code="BLOCK_NESTING_TOO_DEEP",
                                       message=f"Block nesting deeper than {MAX_BLOCK_DEPTH} at '{name}'; not expanded.",
                                       entity_ids=[ent.id]))
            return
        inserts: Iterable = e.multi_insert() if e.mcount > 1 else [e]
        for ins in inserts:
            try:
                children = list(ins.virtual_entities())
            except Exception as exc:
                self.warnings.append(Issue(code="BLOCK_EXPLODE_FAILED",
                                           message=f"Could not expand block '{name}': {exc}", entity_ids=[ent.id]))
                continue
            for child in children:
                if child.dxftype() == "ATTDEF":
                    continue  # attribute definitions are templates, not drawn geometry
                self.add(child, space, ent, block_path + [name], depth + 1, visible)
            # Attribute values (e.g. room-tag name/number) are real drawing text.
            for att in getattr(ins, "attribs", []) or []:
                if not att.is_invisible:
                    self.add(att, space, ent, block_path + [name], depth + 1, visible)


def extract(doc: Drawing, max_entities: int, audit_errors: list[str], audit_fixes: int) -> Extraction:
    col = _Collector(doc, max_entities)
    for e in doc.modelspace():
        col.add(e, "model", None, [], 0)
    viewport_scales = []
    for layout in doc.layouts:
        if layout.is_modelspace:
            continue
        for e in layout:
            t = e.dxftype()
            if t in ("TEXT", "MTEXT", "INSERT"):
                col.add(e, "paper", None, [], 0)
            elif t == "VIEWPORT" and e.dxf.get("id", 0) > 1:
                vh = float(e.dxf.get("view_height", 0) or 0)
                h = float(e.dxf.get("height", 0) or 0)
                if vh > 0 and h > 0:
                    viewport_scales.append({"layout": layout.name, "handle": e.dxf.handle,
                                            "paper_units_per_model_unit": h / vh})

    layer_table: dict[str, dict] = {}
    for layer in doc.layers:
        layer_table[layer.dxf.name] = {
            "color": layer.dxf.get("color"),
            "is_off": layer.is_off(), "is_frozen": layer.is_frozen(),
        }
    ex = Extraction(doc=doc, entities=col.entities, layer_table=layer_table,
                    block_inserts=col.block_inserts, xref_blocks=col.xref_blocks,
                    unsupported=col.unsupported, warnings=col.warnings,
                    audit_errors=audit_errors, audit_fixes=audit_fixes,
                    viewport_scales=viewport_scales)
    return ex


# ── bounds & normalization ───────────────────────────────────────────────────

def compute_source_bounds(entities: list[SourceEntity]) -> Bounds | None:
    xs: list[float] = []; ys: list[float] = []
    for ent in entities:
        if ent.space != "model" or not ent.visible or not ent.supported or ent.type == "INSERT":
            continue
        for x, y in geometry_points(ent.source):
            if math.isfinite(x) and math.isfinite(y):
                xs.append(x); ys.append(y)
    if not xs:
        return None
    return Bounds(min=(min(xs), min(ys)), max=(max(xs), max(ys)))


def _tx(p, t: Transform):
    return ((p[0] - t.origin[0]) * t.scale, (p[1] - t.origin[1]) * t.scale)


def normalize_geometry(g: Geometry, t: Transform) -> Geometry:
    s = t.scale
    n = g.model_copy(deep=True)
    n.points = [_tx(p, t) for p in g.points]
    n.paths = [[_tx(p, t) for p in path] for path in g.paths]
    if g.center is not None:
        n.center = _tx(g.center, t)
    if g.insert is not None:
        n.insert = _tx(g.insert, t)
    if g.radius is not None:
        n.radius = g.radius * s
    if g.height is not None:
        n.height = g.height * s
    if g.measurement is not None:
        n.measurement = g.measurement * s
    return n


def normalize_entities(entities: list[SourceEntity], t: Transform) -> None:
    for ent in entities:
        if ent.space == "model" and ent.source is not None and t.scale is not None:
            ent.normalized = normalize_geometry(ent.source, t)


def layer_statistics(entities: list[SourceEntity]) -> dict[str, Counter]:
    stats: dict[str, Counter] = defaultdict(Counter)
    for ent in entities:
        if ent.parent_id is None:
            stats[ent.layer][ent.type] += 1
    return stats
