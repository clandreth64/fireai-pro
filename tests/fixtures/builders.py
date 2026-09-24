"""Deterministic DXF fixture builders.

Every fixture is generated from code so its ground truth is known exactly.
``make_office(unit)`` draws the SAME 100 ft x 60 ft building in any unit so
unit-equivalence can be asserted. ``make_warehouse()`` is a materially
different building used by the critical "different drawings must differ"
regression test.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

UNIT_CODES = {"in": 1, "ft": 2, "mm": 4, "cm": 5, "m": 6}
PER_FT = {"in": 12.0, "ft": 1.0, "mm": 304.8, "cm": 30.48, "m": 0.3048}
DIM_TEXT_100FT = {"in": "100'-0\"", "ft": "100'-0\"", "mm": "30480", "cm": "3048", "m": "30.48"}

# Ground truth for the office fixture (feet, relative to building corner)
OFFICE_ROOMS = {
    "OFFICE 101": ((0.5, 0.5), (59.75, 49.75)),
    "STORAGE 102": ((60.25, 0.5), (99.5, 49.75)),
    "CORRIDOR C1": ((0.5, 50.25), (99.5, 59.5)),
}


def _area(r):
    (x0, y0), (x1, y1) = r
    return (x1 - x0) * (y1 - y0)


OFFICE_ROOM_AREAS = {k: _area(v) for k, v in OFFICE_ROOMS.items()}


def _new(unit: str | None, insunits: int | None = None):
    doc = ezdxf.new("R2018", setup=True)
    if insunits is not None:
        doc.header["$INSUNITS"] = insunits
    elif unit is not None:
        doc.header["$INSUNITS"] = UNIT_CODES[unit]
    return doc


def make_office(path: Path, unit: str = "in", origin_ft=(1000.0, 400.0), insunits: int | None = None,
                dim_text: str | None = None) -> Path:
    k = PER_FT[unit]
    doc = _new(unit, insunits)
    for name, color in (("A-WALL", 7), ("A-DOOR", 1), ("A-GLAZ", 4), ("A-AREA", 3), ("A-AREA-IDEN", 2),
                        ("A-ANNO-DIMS", 2), ("A-ANNO-NOTE", 7), ("S-COLS", 3), ("S-GRID", 8), ("A-FLOR-STRS", 6),
                        ("F-SPRN", 1), ("G-ANNO-TTLB", 7), ("A-FURN", 9), ("MISC-STUFF", 30)):
        doc.layers.add(name, color=color)
    ox, oy = origin_ft

    def P(x, y):
        return ((ox + x) * k, (oy + y) * k)

    msp = doc.modelspace()
    # Blocks (drawn on layer 0 at 1 ft = k units)
    door = doc.blocks.new("DOOR-36")
    door.add_line((0, 0), (0, 3 * k))
    door.add_arc((0, 0), 3 * k, 0, 90)
    win = doc.blocks.new("WINDOW-48")
    win.add_lwpolyline([(0, -0.25 * k), (4 * k, -0.25 * k), (4 * k, 0.25 * k), (0, 0.25 * k)], close=True)
    win.add_line((0, 0), (4 * k, 0))
    col = doc.blocks.new("COL-12")
    col.add_lwpolyline([(-0.5 * k, -0.5 * k), (0.5 * k, -0.5 * k), (0.5 * k, 0.5 * k), (-0.5 * k, 0.5 * k)], close=True)
    tb = doc.blocks.new("TITLEBLOCK")
    tb.add_lwpolyline([(0, 0), (30 * k, 0), (30 * k, 20 * k), (0, 20 * k)], close=True)
    for i, line in enumerate(["PROJECT: AUDIT TEST OFFICE", "ADDRESS: 123 MAIN ST SPRINGFIELD",
                              "SHEET NO: A1.01", "SCALE: 1/8\" = 1'-0\"", "DATE: 09/22/2026"]):
        tb.add_text(line, height=0.8 * k).set_placement((1 * k, (17 - 3 * i) * k))

    # Exterior walls: double line, 6" thick
    msp.add_lwpolyline([P(0, 0), P(100, 0), P(100, 60), P(0, 60)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline([P(0.5, 0.5), P(99.5, 0.5), P(99.5, 59.5), P(0.5, 59.5)], close=True, dxfattribs={"layer": "A-WALL"})
    # Interior partition x=60 (double line) with a door gap y=20..23
    for x in (59.75, 60.25):
        msp.add_line(P(x, 0.5), P(x, 20), dxfattribs={"layer": "A-WALL"})
        msp.add_line(P(x, 23), P(x, 49.75), dxfattribs={"layer": "A-WALL"})
    # Corridor wall y=50 (double line) with door gaps x=30..33 and x=80..83
    for y in (49.75, 50.25):
        for a, b in ((0.5, 30), (33, 80), (83, 99.5)):
            msp.add_line(P(a, y), P(b, y), dxfattribs={"layer": "A-WALL"})
    # Doors (one mirrored)
    msp.add_blockref("DOOR-36", P(59.75, 20), dxfattribs={"layer": "A-DOOR", "rotation": 0})
    msp.add_blockref("DOOR-36", P(33, 49.75), dxfattribs={"layer": "A-DOOR", "rotation": 90, "xscale": -1})
    msp.add_blockref("DOOR-36", P(80, 50.25), dxfattribs={"layer": "A-DOOR", "rotation": 270})
    # Windows on south wall
    for x in (15, 75):
        msp.add_blockref("WINDOW-48", P(x, 0.25), dxfattribs={"layer": "A-GLAZ"})
    # Columns: two blocks + one circle
    for x in (30, 80):
        msp.add_blockref("COL-12", P(x, 25), dxfattribs={"layer": "S-COLS"})
    msp.add_circle(P(45, 25), 0.5 * k, dxfattribs={"layer": "S-COLS"})
    # Rooms (area layer) + labels
    for (x0, y0), (x1, y1) in OFFICE_ROOMS.values():
        msp.add_lwpolyline([P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)], close=True, dxfattribs={"layer": "A-AREA"})
    msp.add_mtext("OFFICE\\P101", dxfattribs={"layer": "A-AREA-IDEN", "char_height": 1.0 * k, "insert": P(25, 30)})
    msp.add_text(f"{OFFICE_ROOM_AREAS['OFFICE 101']:,.0f} SF", height=0.8 * k,
                 dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(P(25, 26))
    msp.add_text("STORAGE 102", height=1.0 * k, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(P(70, 30))
    msp.add_text("CORRIDOR C1", height=1.0 * k, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(P(40, 54))
    # Stair in corridor east end
    msp.add_lwpolyline([P(88, 51), P(98, 51), P(98, 58), P(88, 58)], close=True, dxfattribs={"layer": "A-FLOR-STRS"})
    for x in range(89, 98, 1):
        msp.add_line(P(x, 51), P(x, 58), dxfattribs={"layer": "A-FLOR-STRS"})
    # Existing sprinklers
    for x, y in ((15, 15), (45, 15), (15, 40)):
        msp.add_circle(P(x, y), 0.25 * k, dxfattribs={"layer": "F-SPRN"})
    # Grid
    for i, x in enumerate((0, 30, 60, 90)):
        msp.add_line(P(x, -2), P(x, 64), dxfattribs={"layer": "S-GRID"})
        msp.add_circle(P(x, 66), 1.5 * k, dxfattribs={"layer": "S-GRID"})
        msp.add_text("ABCD"[i], height=1.2 * k, dxfattribs={"layer": "S-GRID"}).set_placement(P(x - 0.4, 65.4))
    # Dimensions: measured and explicit override
    # dimlfac=1: ezdxf's "EZDXF" style defaults to 100 (m drawn, cm displayed).
    ov = {"dimtxt": 1.0 * k, "dimasz": 0.8 * k, "dimexo": 0.3 * k, "dimexe": 0.3 * k, "dimgap": 0.2 * k,
          "dimlfac": 1.0}
    msp.add_linear_dim(base=P(0, -5), p1=P(0, 0), p2=P(100, 0), dimstyle="EZDXF", override=ov,
                       dxfattribs={"layer": "A-ANNO-DIMS"}).render()
    msp.add_linear_dim(base=P(-5, 0), p1=P(0, 0), p2=P(0, 60), angle=90, dimstyle="EZDXF", override=ov,
                       text=dim_text or DIM_TEXT_100FT[unit].replace("100", "60").replace("30480", "18288")
                       .replace("3048", "1828.8").replace("30.48", "18.288"),
                       dxfattribs={"layer": "A-ANNO-DIMS"}).render()
    msp.add_linear_dim(base=P(0, -10), p1=P(0, 0), p2=P(100, 0), dimstyle="EZDXF", override=ov,
                       text=dim_text or DIM_TEXT_100FT[unit], dxfattribs={"layer": "A-ANNO-DIMS"}).render()
    # Notes, title block, unrecognized content
    msp.add_text("GENERAL NOTE: VERIFY ALL DIMENSIONS IN FIELD", height=1.0 * k,
                 dxfattribs={"layer": "A-ANNO-NOTE"}).set_placement(P(0, -14))
    msp.add_blockref("TITLEBLOCK", P(108, -12), dxfattribs={"layer": "G-ANNO-TTLB"})
    msp.add_lwpolyline([P(5, 5), P(11, 5), P(11, 8), P(5, 8)], close=True, dxfattribs={"layer": "A-FURN"})
    msp.add_lwpolyline([P(70, 10), P(72, 12), P(74, 10), P(76, 12)], dxfattribs={"layer": "MISC-STUFF"})
    doc.saveas(path)
    return path


WAREHOUSE_ROOMS = {"WAREHOUSE": 200 * 80 + 120 * 40 - 50 * 40, "OFFICE 1": 50 * 40}


def make_warehouse(path: Path) -> Path:
    """L-shaped 200 x 120 ft warehouse in FEET, non-NCS layer names, no area layer:
    rooms must come from wall linework (polygonize)."""
    doc = _new("ft")
    for name in ("WALLS", "RMNAME", "STRUCT-COLUMNS", "NOTES"):
        doc.layers.add(name)
    msp = doc.modelspace()
    outline = [(0, 0), (200, 0), (200, 80), (120, 80), (120, 120), (0, 120)]
    msp.add_lwpolyline(outline, close=True, dxfattribs={"layer": "WALLS"})
    # Office in the north-west corner: x 0..50, y 80..120
    msp.add_line((0, 80), (50, 80), dxfattribs={"layer": "WALLS"})
    msp.add_line((50, 80), (50, 120), dxfattribs={"layer": "WALLS"})
    msp.add_text("WAREHOUSE", height=3, dxfattribs={"layer": "RMNAME"}).set_placement((100, 40))
    msp.add_text("OFFICE 1", height=2, dxfattribs={"layer": "RMNAME"}).set_placement((20, 100))
    colb = doc.blocks.new("COLUMN")
    colb.add_lwpolyline([(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)], close=True)
    for x in (40, 80, 120, 160):
        msp.add_blockref("COLUMN", (x, 40), dxfattribs={"layer": "STRUCT-COLUMNS"})
    msp.add_text("ALL COLUMNS W10X33", height=2, dxfattribs={"layer": "NOTES"}).set_placement((0, -10))
    doc.saveas(path)
    return path


def make_simple_rect(path: Path) -> Path:
    doc = _new("ft")
    doc.layers.add("A-WALL"); doc.layers.add("A-AREA"); doc.layers.add("A-AREA-IDEN")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 30), (0, 30)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline([(0.5, 0.5), (39.5, 0.5), (39.5, 29.5), (0.5, 29.5)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline([(0.5, 0.5), (39.5, 0.5), (39.5, 29.5), (0.5, 29.5)], close=True, dxfattribs={"layer": "A-AREA"})
    msp.add_text("OPEN AREA 100", height=1, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement((15, 15))
    doc.saveas(path)
    return path


def make_empty(path: Path) -> Path:
    _new("ft").saveas(path)
    return path


def make_units_code(path: Path, insunits: int) -> Path:
    """Simple-rect building with an arbitrary $INSUNITS code (0 = unitless)."""
    make_simple_rect(path)
    doc = ezdxf.readfile(path)
    doc.header["$INSUNITS"] = insunits
    doc.saveas(path)
    return path


def make_missing_units(path: Path) -> Path:
    make_simple_rect(path)
    doc = ezdxf.readfile(path)
    del doc.header["$INSUNITS"]
    doc.saveas(path)
    return path


def make_corrupted(path: Path) -> Path:
    good = path.with_suffix(".tmp.dxf")
    make_office(good, "ft")
    data = good.read_bytes()
    good.unlink()
    # Keep a valid header start, then truncate mid-ENTITIES and inject garbage.
    cut = data.find(b"ENTITIES") + 400
    path.write_bytes(data[:cut] + b"\n  0\nLINE\n  8\nA-WALL\n 10\nNOT_A_NUMBER\n\x00\x01\x02garbage")
    return path


def make_fake_dwg(path: Path) -> Path:
    path.write_bytes(b"AC1032\x00\x00\x00\x00FAKE-DWG-PAYLOAD-NOT-A-DRAWING" + bytes(range(256)))
    return path


def make_renamed_pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    return path


def make_hidden_only(path: Path) -> Path:
    doc = _new("ft")
    layer = doc.layers.add("A-WALL")
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "A-WALL"})
    layer.off()
    doc.saveas(path)
    return path


# ── Milestone 1.5: fixtures reproducing real-world failure CLASSES (synthetic, no proprietary data) ──

def make_two_plans(path: Path) -> Path:
    """Two floor plans side by side in one model space (MULTIPLE_DRAWING_REGIONS)."""
    doc = _new("ft")
    for n in ("A-WALL", "A-AREA", "A-AREA-IDEN"):
        doc.layers.add(n)
    msp = doc.modelspace()
    for ox, names in ((0, ("LOBBY", "OFFICE")), (150, ("BEDROOM", "BATH"))):
        msp.add_lwpolyline([(ox, 0), (ox + 60, 0), (ox + 60, 40), (ox, 40)], close=True, dxfattribs={"layer": "A-WALL"})
        msp.add_line((ox + 30, 0), (ox + 30, 40), dxfattribs={"layer": "A-WALL"})
        for i, nm in enumerate(names):
            x0 = ox + i * 30
            msp.add_lwpolyline([(x0, 0), (x0 + 30, 0), (x0 + 30, 40), (x0, 40)], close=True, dxfattribs={"layer": "A-AREA"})
            msp.add_text(nm, height=1, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement((x0 + 10, 20))
    doc.saveas(path)
    return path


def make_walls_only(path: Path, with_door: bool = True, closed_wall_pieces: bool = False,
                    right_label: str = "SALES FLOOR") -> Path:
    """Two rooms (20 x 20 ft) drawn only with double-line walls (0.5 ft), sharing a
    wall with a 3 ft opening at y=8..11. No room/area layer. Labels: 'OFFICE' +
    finish note 'HRWD FLOOR' (left), right_label (right). Optional door block in the
    opening. closed_wall_pieces draws each wall piece as a closed polyline (like
    real files) instead of exploded lines."""
    doc = _new("ft")
    for n in ("WALLS", "RMNAME", "DOORS"):
        doc.layers.add(n)
    msp = doc.modelspace()
    if closed_wall_pieces:
        pieces = [((0, 0), (40, 0.5)), ((0, 19.5), (40, 20)), ((0, 0), (0.5, 20)), ((39.5, 0), (40, 20)),
                  ((19.75, 0.5), (20.25, 8)), ((19.75, 11), (20.25, 19.5))]
        for (x0, y0), (x1, y1) in pieces:
            msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "WALLS"})
    else:
        segs = [((0, 0), (40, 0)), ((40, 0), (40, 20)), ((40, 20), (0, 20)), ((0, 20), (0, 0)),       # outer face
                ((0.5, 0.5), (39.5, 0.5)), ((39.5, 0.5), (39.5, 19.5)), ((39.5, 19.5), (0.5, 19.5)),  # inner face
                ((0.5, 19.5), (0.5, 0.5)),
                ((19.75, 0.5), (19.75, 8)), ((20.25, 0.5), (20.25, 8)), ((19.75, 8), (20.25, 8)),     # partition + jambs
                ((19.75, 11), (19.75, 19.5)), ((20.25, 11), (20.25, 19.5)), ((19.75, 11), (20.25, 11))]
        for a, b in segs:
            msp.add_line(a, b, dxfattribs={"layer": "WALLS"})
    if with_door:
        blk = doc.blocks.new("DOOR")
        blk.add_line((0, 0), (3, 0))
        blk.add_arc((0, 0), 3, 0, 90)
        msp.add_blockref("DOOR", (20.25, 8), dxfattribs={"layer": "DOORS", "rotation": 90})
    msp.add_mtext(r"OFFICE\PHRWD FLOOR", dxfattribs={"layer": "RMNAME", "char_height": 1, "insert": (8, 12)})
    msp.add_text(right_label, height=1, dxfattribs={"layer": "RMNAME"}).set_placement((27, 12))
    doc.saveas(path)
    return path


def make_dynamic_door(path: Path) -> Path:
    """A door inserted as an anonymous dynamic-block copy (*U) on a NON-door layer;
    only the effective name ('Door') identifies it."""
    doc = _new("ft")
    doc.layers.add("A-WALL"); doc.layers.add("MISC")
    doc.appids.add("AcDbBlockRepBTag")
    door = doc.blocks.new("Door")
    door.add_line((0, 0), (3, 0)); door.add_arc((0, 0), 3, 0, 90)
    anon = doc.blocks.new_anonymous_block("U")
    anon.add_line((0, 0), (3, 0)); anon.add_arc((0, 0), 3, 0, 90)
    anon.block_record.set_xdata("AcDbBlockRepBTag", [(1070, 1), (1005, door.block_record.dxf.handle)])
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (30, 0), (30, 20), (0, 20)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_blockref(anon.name, (10, 0), dxfattribs={"layer": "MISC"})
    doc.saveas(path)
    return path


def make_multileader(path: Path) -> Path:
    doc = _new("ft")
    doc.layers.add("A-WALL"); doc.layers.add("A-ANNO-NOTE")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (30, 0), (30, 20), (0, 20)], close=True, dxfattribs={"layer": "A-WALL"})
    ml = msp.add_multileader_mtext("Standard", dxfattribs={"layer": "A-ANNO-NOTE"})
    ml.set_content("GYP BD CEILING", char_height=0.5)
    from ezdxf.render.mleader import ConnectionSide
    ml.add_leader_line(ConnectionSide.left, [(10, 10)])
    from ezdxf.math import Vec2
    ml.build(insert=Vec2(15, 15))
    doc.saveas(path)
    return path


def make_wall_qualifiers(path: Path) -> Path:
    doc = _new("ft")
    for n in ("A-WALL", "A-WALL-ABOVE", "A-WALL-DEMO"):
        doc.layers.add(n)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (30, 0), (30, 20), (0, 20)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_line((10, 0), (10, 20), dxfattribs={"layer": "A-WALL-ABOVE"})
    msp.add_line((20, 0), (20, 20), dxfattribs={"layer": "A-WALL-DEMO"})
    doc.saveas(path)
    return path


def make_unitless_with_scale_evidence(path: Path, viewport_ratio: float = 1 / 96,
                                      scale_text: str = "SCALE: 1/8\" = 1'-0\"") -> Path:
    """Unitless drawing whose paper space states its scale (viewport + title text)."""
    make_simple_rect(path)
    doc = ezdxf.readfile(path)
    doc.header["$INSUNITS"] = 0
    layout = doc.layouts.new("A1")
    layout.dxf_layout.dxf.plot_paper_units = 0          # inches
    h = 6.0
    layout.add_viewport(center=(5, 4), size=(8, h), view_center_point=(240, 180), view_height=h / viewport_ratio)
    layout.add_text(scale_text, height=0.1).set_placement((1, 0.5))
    doc.saveas(path)
    return path


def make_room(path: Path, unit: str = "ft", w: float = 10.0, h: float = 10.0, *, t: float = 0.5,
              door: tuple[str, float, float] | None = None, window: tuple[str, float, float] | None = None,
              notch: tuple[float, float] | None = None, label: str = "SYN ROOM") -> Path:
    """One room drawn with double-line walls (thickness ``t``), interior w x h ft. The interior's
    lower-left corner is at (t, t) in the drawing's LOCAL frame, so room coordinates = LOCAL - t.

    door / window: (side, a, b) with side in right|left|top|bottom and the opening from a to b
    measured along that side (room coordinates). A door is a gap in both wall faces with jamb lines
    and a door block; a window is a block drawn INSIDE a continuous wall.
    notch: (nw, nh) removes the top-right nw x nh corner (an L-shaped room; no openings then)."""
    k = PER_FT[unit]
    doc = _new(unit)
    for n in ("A-WALL", "A-DOOR", "A-GLAZ", "A-AREA-IDEN"):
        doc.layers.add(n)
    msp = doc.modelspace()

    def P(x, y):
        return (x * k, y * k)

    if notch:
        nw, nh = notch
        inner = [(0, 0), (w, 0), (w, h - nh), (w - nw, h - nh), (w - nw, h), (0, h)]
        outer = [(-t, -t), (w + t, -t), (w + t, h - nh + t), (w - nw + t, h - nh + t), (w - nw + t, h + t), (-t, h + t)]
        for ring in (inner, outer):
            msp.add_lwpolyline([P(x + t, y + t) for x, y in ring], close=True, dxfattribs={"layer": "A-WALL"})
    else:
        sides = {"bottom": ((0, 0), (w, 0), (0, -t)), "right": ((w, 0), (w, h), (t, 0)),
                 "top": ((0, h), (w, h), (0, t)), "left": ((0, 0), (0, h), (-t, 0))}
        corners_in = {"bottom": ((0, 0), (w, 0)), "right": ((w, 0), (w, h)), "top": ((w, h), (0, h)),
                      "left": ((0, h), (0, 0))}
        for side, (a, b, off) in sides.items():
            gap = door[1:] if door and door[0] == side else None
            ext = {"bottom": ((-t, -t), (w + t, -t)), "right": ((w + t, -t), (w + t, h + t)),
                   "top": ((-t, h + t), (w + t, h + t)), "left": ((-t, -t), (-t, h + t))}[side]
            for (p0, p1) in ((corners_in[side][0], corners_in[side][1]), ext):
                horiz = p0[1] == p1[1]
                lo, hi = sorted((p0[0], p1[0]) if horiz else (p0[1], p1[1]))
                pieces = [(lo, gap[0]), (gap[1], hi)] if gap else [(lo, hi)]
                for s0, s1 in pieces:
                    q0 = (s0, p0[1]) if horiz else (p0[0], s0)
                    q1 = (s1, p0[1]) if horiz else (p0[0], s1)
                    msp.add_line(P(q0[0] + t, q0[1] + t), P(q1[0] + t, q1[1] + t), dxfattribs={"layer": "A-WALL"})
            if gap:                                       # jamb lines across the wall at both gap ends
                for g in gap:
                    ip = (g, a[1]) if a[1] == b[1] else (a[0], g)
                    op = (ip[0] + off[0], ip[1] + off[1])
                    msp.add_line(P(ip[0] + t, ip[1] + t), P(op[0] + t, op[1] + t), dxfattribs={"layer": "A-WALL"})
        if door:
            side, a0, a1 = door
            dw = a1 - a0
            blk = doc.blocks.new("DOOR")
            blk.add_line((0, 0), (dw * k, 0))
            blk.add_arc((0, 0), dw * k, 0, 90)
            ins, rot = {"right": ((w, a0), 90), "left": ((0, a1), 270), "top": ((a1, h), 180),
                        "bottom": ((a0, 0), 0)}[side]
            msp.add_blockref("DOOR", P(ins[0] + t, ins[1] + t), dxfattribs={"layer": "A-DOOR", "rotation": rot})
        if window:
            side, a0, a1 = window
            L = a1 - a0
            blk = doc.blocks.new("WINDOW")
            blk.add_lwpolyline([(0, 0), (L * k, 0), (L * k, t * k), (0, t * k)], close=True)
            blk.add_line((0, t * k / 2), (L * k, t * k / 2))
            ins, rot = {"left": ((0, a0), 90), "right": ((w + t, a0), 90), "bottom": ((a0, -t), 0),
                        "top": ((a0, h), 0)}[side]
            msp.add_blockref("WINDOW", P(ins[0] + t, ins[1] + t), dxfattribs={"layer": "A-GLAZ", "rotation": rot})
    msp.add_text(label, height=0.5 * k, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(P(t + 1.0, t + 1.0))
    doc.saveas(path)
    return path
