"""Visual verification outputs.

* source.png   — the drawing rendered directly from the DXF by ezdxf's drawing
                 add-on (independent of FireAI's parser: if FireAI missed
                 something, it still appears here).
* overlay.png / overlay.svg — the same drawing in light grey with FireAI's
                 interpreted elements drawn on top in category colours, element
                 ids, a legend, and a status header.
* overlay.dxf  — the original drawing plus FAI-* layers containing the
                 interpretation, for layer-by-layer checking in CAD.

Reuses the ezdxf drawing add-on + matplotlib technique from the v1
``FireAIDrawingEngine._export_dxf_to_pdf`` (with its page-size bug fixed: the
figure is sized to the drawing and not cropped with bbox_inches='tight').
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Polygon as MplPolygon  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, MaxNLocator  # noqa: E402

from ezdxf import recover  # noqa: E402
from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: E402
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402

from fireai.model import BuildingElement, BuildingModel, SourceEntity  # noqa: E402

STYLE = {
    "wall":                     ("#1f4e9c", "FAI-WALL", 5, "Wall linework"),
    "room":                     ("#2e8b57", "FAI-ROOM", 3, "Room boundary"),
    "area":                     ("#6a3d9a", "FAI-AREA", 6, "Gross area boundary"),
    "door":                     ("#d62728", "FAI-DOOR", 1, "Door"),
    "window":                   ("#00a0b0", "FAI-WINDOW", 4, "Window"),
    "column":                   ("#2ca02c", "FAI-COLUMN", 3, "Column"),
    "stair":                    ("#9467bd", "FAI-STAIR", 6, "Stair"),
    "shaft":                    ("#8c564b", "FAI-SHAFT", 34, "Shaft"),
    "structural":               ("#8a8a00", "FAI-STRUCT", 52, "Structural (layer only)"),
    "grid_line":                ("#7f7f7f", "FAI-GRID", 9, "Grid line"),
    "ceiling":                  ("#6baed6", "FAI-CEILING", 151, "Ceiling"),
    "existing_fire_protection": ("#c71585", "FAI-EXIST-FP", 221, "Existing fire protection"),
    "existing_mep":             ("#e377c2", "FAI-EXIST-MEP", 231, "Existing MEP"),
    "dimension":                ("#b8860b", "FAI-DIM", 42, "Dimension"),
    "text_annotation":          ("#444444", "FAI-TEXT", 250, "Text"),
    "title_block":              ("#000000", "FAI-TITLE", 7, "Title block"),
}
UNCLASSIFIED = ("#ff7f00", "FAI-UNCLASSIFIED", 30, "UNCLASSIFIED geometry")
VERIFY_NOTE = "Dashed outline = requires human verification"
MAX_LABELS_PER_CATEGORY = 400


class Inverse:
    """normalized (ft) -> source drawing coordinates."""

    def __init__(self, model: BuildingModel):
        self.ox, self.oy = model.transform.origin
        self.s = model.transform.scale

    def __call__(self, p):
        return (p[0] / self.s + self.ox, p[1] / self.s + self.oy)


def _load(dxf_path: Path):
    doc, _ = recover.readfile(str(dxf_path))
    return doc


def _draw_underlay(ax, doc, color: str | None):
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                        color_policy=ColorPolicy.CUSTOM if color else ColorPolicy.COLOR,
                        custom_fg_color=color or "#000000")
    ctx = RenderContext(doc)
    Frontend(ctx, MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=False)
    # The ezdxf backend hides both axes and all spines; reviewers need the coordinate scale.
    ax.xaxis.set_visible(True)
    ax.yaxis.set_visible(True)
    for s in ax.spines.values():
        s.set_visible(True)
        s.set_color("#bbbbbb")


def _figure(bounds_src, width_in=16.0, header_in=1.1):
    (x0, y0), (x1, y1) = bounds_src.min, bounds_src.max
    w = max(x1 - x0, 1e-9); h = max(y1 - y0, 1e-9)
    aspect = min(max(h / w, 0.25), 2.5)
    fig = plt.figure(figsize=(width_in, width_in * aspect * 0.78 + header_in), facecolor="white")
    ax = fig.add_axes([0.05, 0.06, 0.70, 0.86 - header_in / (width_in * aspect * 0.78 + header_in)])
    ax.set_facecolor("white")
    pad = 0.03 * max(w, h)
    return fig, ax, (x0 - pad, x1 + pad, y0 - pad, y1 + pad)


def render_source_png(dxf_path: Path, bounds_src, out_png: Path, title: str) -> None:
    doc = _load(dxf_path)
    fig, ax, lim = _figure(bounds_src)
    _draw_underlay(ax, doc, None)
    ax.set_aspect("equal", adjustable="box"); ax.set_xlim(lim[0], lim[1]); ax.set_ylim(lim[2], lim[3])
    ax.set_title(f"SOURCE DRAWING (as rendered from file) — {title}", fontsize=11, loc="left")
    ax.tick_params(labelsize=7)
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)


def _source_paths(ent: SourceEntity, model: BuildingModel, by_parent) -> list[tuple[list, bool]]:
    """Return drawable (points, closed) lists in source coordinates for an entity."""
    out = []
    if ent.type == "INSERT":
        stack = list(by_parent.get(ent.id, []))
        while stack:
            c = stack.pop()
            stack.extend(by_parent.get(c.id, []))
            if c.type != "INSERT":
                out.extend(_source_paths(c, model, by_parent))
        return out
    g = ent.source
    if g is None:
        return out
    if g.points and g.kind in ("line", "polyline", "arc", "circle", "polygon", "dimension"):
        out.append((list(g.points), bool(g.closed or g.kind == "circle")))
    for p in g.paths:
        out.append((list(p), True))
    return out


def _element_paths(el: BuildingElement, model: BuildingModel, inv: Inverse, by_parent, by_id):
    if el.category in ("room", "area") and el.geometry is not None:
        return [([inv(p) for p in el.geometry.points], True)]
    paths = []
    for sid in el.source_entity_ids:
        ent = by_id[sid]
        if ent.parent_id and ent.parent_id in el.source_entity_ids:
            continue  # drawn via its parent INSERT
        paths.extend(_source_paths(ent, model, by_parent))
    if not paths and el.geometry is not None and el.geometry.points:
        paths.append(([inv(p) for p in el.geometry.points], el.geometry.closed))
    return paths


def _anchor(el: BuildingElement, inv: Inverse):
    g = el.geometry
    if g is None:
        return None
    if g.insert is not None:
        return inv(g.insert)
    if g.points:
        xs = [p[0] for p in g.points]; ys = [p[1] for p in g.points]
        return inv(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
    return None


def render_overlay(dxf_path: Path, model: BuildingModel, out_png: Path, out_svg: Path) -> None:
    doc = _load(dxf_path)
    inv = Inverse(model)
    by_id = {e.id: e for e in model.entities}
    by_parent: dict[str, list[SourceEntity]] = {}
    for e in model.entities:
        if e.parent_id:
            by_parent.setdefault(e.parent_id, []).append(e)

    fig, ax, lim = _figure(model.bounds_source)
    _draw_underlay(ax, doc, "#c8c8c8")

    counts = Counter(el.category for el in model.elements)
    label_counts: Counter = Counter()
    room_colors = plt.get_cmap("tab20")
    fs = 6.5

    for i, el in enumerate(sorted(model.elements, key=lambda e: e.category != "room")):
        color = STYLE[el.category][0]
        ls = "--" if el.requires_verification else "-"
        paths = _element_paths(el, model, inv, by_parent, by_id)
        if el.category == "text_annotation":
            a = _anchor(el, inv)
            if a:
                ax.plot([a[0]], [a[1]], marker=".", ms=2.5, color=color, zorder=6)
            continue
        for pts, closed in paths:
            if len(pts) < 2:
                if pts:
                    ax.plot([pts[0][0]], [pts[0][1]], marker="o", ms=3, color=color, zorder=7)
                continue
            if el.category == "room":
                ax.add_patch(MplPolygon(pts, closed=True, facecolor=room_colors(i % 20), alpha=0.28,
                                        edgecolor=color, linewidth=1.6, linestyle=ls, zorder=4))
            elif el.category == "area":
                ax.add_patch(MplPolygon(pts, closed=True, fill=False, edgecolor=color, linewidth=1.2,
                                        linestyle=":", zorder=4))
            else:
                xs = [p[0] for p in pts] + ([pts[0][0]] if closed else [])
                ys = [p[1] for p in pts] + ([pts[0][1]] if closed else [])
                lw = {"wall": 2.0, "door": 1.6, "window": 1.6, "column": 1.8, "title_block": 0.8}.get(el.category, 1.0)
                ax.plot(xs, ys, color=color, linewidth=lw, linestyle=ls, zorder=6, solid_capstyle="round")
        if label_counts[el.category] < MAX_LABELS_PER_CATEGORY and el.category not in ("wall", "text_annotation"):
            a = _anchor(el, inv)
            if a:
                label_counts[el.category] += 1
                if el.category == "room":
                    area = el.properties.get("area_sf")
                    txt = f"{el.id}\n{el.label or 'UNLABELED'}\n{area:,.0f} sf" + ("\n[VERIFY]" if el.requires_verification else "")
                    ax.text(a[0], a[1], txt, ha="center", va="center", fontsize=fs + 1, color="#0b3d20",
                            zorder=9, bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.85, lw=0.6))
                else:
                    ax.text(a[0], a[1], el.id, fontsize=fs - 1, color=color, zorder=9, ha="left", va="bottom")

    uncl_n = 0
    for sid in model.unclassified_entity_ids:
        for pts, closed in _source_paths(by_id[sid], model, by_parent):
            uncl_n += 1
            if len(pts) >= 2:
                xs = [p[0] for p in pts] + ([pts[0][0]] if closed else [])
                ys = [p[1] for p in pts] + ([pts[0][1]] if closed else [])
                ax.plot(xs, ys, color=UNCLASSIFIED[0], linewidth=1.0, linestyle=":", zorder=5)
        g = by_id[sid].source
        if g is not None and g.kind in ("text", "point") and g.insert:
            ax.plot([g.insert[0]], [g.insert[1]], marker="x", ms=4, color=UNCLASSIFIED[0], zorder=5)

    ax.set_aspect("equal", adjustable="box"); ax.set_xlim(lim[0], lim[1]); ax.set_ylim(lim[2], lim[3])
    # Ticks at round values of normalized feet (not round source units).
    for axis, (lo, hi), origin in ((ax.xaxis, lim[:2], inv.ox), (ax.yaxis, lim[2:], inv.oy)):
        ft_ticks = MaxNLocator(nbins=10, steps=[1, 2, 5, 10]).tick_values((lo - origin) * inv.s, (hi - origin) * inv.s)
        axis.set_major_locator(FixedLocator([t / inv.s + origin for t in ft_ticks]))
        axis.set_major_formatter(FuncFormatter(lambda v, _, o=origin: f"{(v - o) * inv.s:,.0f}"))
    ax.set_xlabel("normalized X (ft)", fontsize=8); ax.set_ylabel("normalized Y (ft)", fontsize=8)
    ax.tick_params(labelsize=7)

    # Legend with counts
    handles = []
    for cat, (color, _, _, name) in STYLE.items():
        if counts.get(cat):
            if cat == "room":
                handles.append(Patch(facecolor=color, alpha=0.3, edgecolor=color, label=f"{name} ({counts[cat]})"))
            else:
                handles.append(Line2D([0], [0], color=color, lw=2, label=f"{name} ({counts[cat]})"))
    handles.append(Line2D([0], [0], color=UNCLASSIFIED[0], lw=1.5, ls=":",
                          label=f"{UNCLASSIFIED[3]} ({len(model.unclassified_entity_ids)} entities)"))
    handles.append(Line2D([0], [0], color="#c8c8c8", lw=2, label="Source drawing (all entities)"))
    handles.append(Line2D([0], [0], color="#555555", lw=1.5, ls="--", label=VERIFY_NOTE))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.765, 0.90), fontsize=8, frameon=True,
               title="FireAI interpretation", title_fontsize=9)

    review = model.requires_human_review
    status = ("REQUIRES HUMAN REVIEW — " + str(len(model.diagnostics.review_triggers)) + " trigger(s)") if review \
        else "No automatic review triggers (visual verification still required)"
    b = model.bounds_normalized
    fig.text(0.05, 0.975, f"FireAI DRAWING UNDERSTANDING — {model.source.filename}", fontsize=13, weight="bold", va="top")
    fig.text(0.05, 0.948,
             f"units: {model.units.resolved_units} ({model.units.resolution_method})  |  extents: "
             f"{b.width:,.1f} x {b.height:,.1f} ft  |  entities: {len(model.entities):,}  |  "
             f"geometry_is_synthetic: false  |  AI inference: none", fontsize=8.5, va="top")
    fig.text(0.05, 0.928, status, fontsize=9.5, weight="bold", va="top", color="#b00020" if review else "#1b5e20")
    fig.text(0.765, 0.975, "NOT FOR DESIGN OR PERMIT\nVerification of drawing interpretation only",
             fontsize=8.5, weight="bold", va="top", color="#b00020")
    trig = model.diagnostics.review_triggers[:8]
    if trig:
        fig.text(0.765, 0.30, "Review triggers:\n" + "\n".join(f"• {t.code}" for t in trig)
                 + ("\n…" if len(model.diagnostics.review_triggers) > 8 else ""), fontsize=7.5, va="top")
    fig.savefig(out_png, dpi=150, facecolor="white")
    fig.savefig(out_svg, facecolor="white")
    plt.close(fig)


def write_overlay_dxf(dxf_path: Path, model: BuildingModel, out_path: Path) -> None:
    doc = _load(dxf_path)
    inv = Inverse(model)
    by_id = {e.id: e for e in model.entities}
    by_parent: dict[str, list[SourceEntity]] = {}
    for e in model.entities:
        if e.parent_id:
            by_parent.setdefault(e.parent_id, []).append(e)
    for _color, layer, aci, _name in list(STYLE.values()) + [UNCLASSIFIED]:
        if layer not in doc.layers:
            doc.layers.add(layer, color=aci)
    if "FAI-LABEL" not in doc.layers:
        doc.layers.add("FAI-LABEL", color=7)
    msp = doc.modelspace()
    text_h = 0.6 / inv.s  # 0.6 ft text in source units

    for el in model.elements:
        if el.category == "text_annotation":
            continue
        layer = STYLE[el.category][1]
        attribs = {"layer": layer, "lineweight": 50 if el.category in ("wall", "room") else 35}
        if el.requires_verification:
            attribs["linetype"] = "DASHED" if "DASHED" in doc.linetypes else "CONTINUOUS"
        for pts, closed in _element_paths(el, model, inv, by_parent, by_id):
            if len(pts) >= 2:
                msp.add_lwpolyline(pts, close=closed, dxfattribs=attribs)
        a = _anchor(el, inv)
        if a and el.category != "wall":
            label = f"{el.id} {el.label or ''}".strip()
            if el.category == "room":
                label += f" {el.properties.get('area_sf', 0):,.0f}SF"
            if el.requires_verification:
                label += " [VERIFY]"
            msp.add_text(label, height=text_h, dxfattribs={"layer": "FAI-LABEL", "insert": a})
    for sid in model.unclassified_entity_ids:
        for pts, closed in _source_paths(by_id[sid], model, by_parent):
            if len(pts) >= 2:
                msp.add_lwpolyline(pts, close=closed, dxfattribs={"layer": UNCLASSIFIED[1]})

    # Legend to the right of the drawing
    (x0, _y0), (x1, y1) = model.bounds_source.min, model.bounds_source.max
    lx = x1 + 0.05 * (x1 - x0) + 2 * text_h
    ly = y1
    msp.add_text("FIREAI INTERPRETATION LEGEND (layers FAI-*) - NOT FOR DESIGN", height=text_h * 1.4,
                 dxfattribs={"layer": "FAI-LABEL", "insert": (lx, ly)})
    for i, (_color, layer, _aci, name) in enumerate(list(STYLE.values()) + [UNCLASSIFIED]):
        y = ly - (i + 2) * text_h * 2
        msp.add_line((lx, y), (lx + 6 * text_h, y), dxfattribs={"layer": layer, "lineweight": 50})
        msp.add_text(f"{layer}: {name}", height=text_h, dxfattribs={"layer": "FAI-LABEL", "insert": (lx + 7 * text_h, y)})
    doc.saveas(out_path)
