"""Room topology regressions (Milestone 1.7).

Human-discovered case (REAL_002, second floor): rooms separated only by doors were merged into
one region because door-closure analysis lines were built from ROUNDED wall vertices and so were
never noded into the wall linework. Synthetic fixtures with round coordinates never exposed it.
These tests reproduce the failure CLASS with generated drawings — no real-drawing data.
"""

from __future__ import annotations

import ezdxf
import pytest
from ezdxf.math import Matrix44

from conftest import run_pipeline
from fixtures import builders as B


def _to_inches_with_offset(src, dst, offset_in=(123.4567, 89.0123)):
    """Same building, redrawn in inches at a non-round origin: every vertex gets long decimals
    in feet (as in real inch-based drawings)."""
    doc = ezdxf.readfile(src)
    doc.header["$INSUNITS"] = 1
    m = Matrix44.scale(12, 12, 1) @ Matrix44.translate(offset_in[0], offset_in[1], 0)
    for blk in (doc.modelspace(),):
        for e in list(blk):
            e.transform(m)
    for b in doc.blocks:
        if b.name.upper() == "DOOR":
            for e in b:
                e.transform(Matrix44.scale(12, 12, 1))
    doc.saveas(dst)
    return dst


def _labels(m):
    return sorted(r.label for r in m.elements_of("room") if r.label)


@pytest.mark.parametrize("closed_pieces", [False, True])
def test_door_separates_rooms_with_non_round_coordinates(tmp_path, closed_pieces):
    src = B.make_walls_only(tmp_path / "w.dxf", with_door=True, closed_wall_pieces=closed_pieces)
    m = run_pipeline(_to_inches_with_offset(src, tmp_path / "w_in.dxf"), tmp_path).model
    rooms = m.elements_of("room")
    assert _labels(m) == ["OFFICE", "SALES FLOOR"], [(r.label, r.subtype) for r in rooms]
    assert not any(r.subtype == "suspected_merged_region" for r in rooms)
    for r in rooms:
        assert r.properties["area_sf"] == pytest.approx(19.25 * 19, rel=1e-3)
        assert "G-DOOR-OPENING-CLOSURE" in r.rules


def test_doorless_opening_still_flagged_not_split_with_non_round_coordinates(tmp_path):
    src = B.make_walls_only(tmp_path / "w.dxf", with_door=False)
    m = run_pipeline(_to_inches_with_offset(src, tmp_path / "w_in.dxf"), tmp_path).model
    (room,) = m.elements_of("room")
    assert room.subtype == "suspected_merged_region" and room.label is None       # unknown beats invented
    assert "ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS" in {t.code for t in m.diagnostics.review_triggers}
    sc = room.properties["split_candidates"]                                     # suggested, not applied
    assert sc["status"] == "suggested_not_applied" and len([f for f in sc["faces"] if f["labels"]]) == 2


def make_single_ring_plan(path, with_doors=True):
    """Wall mass drawn as TWO closed polylines (exterior face + one interior ring whose partitions
    are peninsulas), like many real plans. Three rooms off a corridor; inches at a non-round origin."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    for n in ("A-WALL", "A-DOOR", "A-AREA-IDEN"):
        doc.layers.add(n)
    blk = doc.blocks.new("DOOR")
    blk.add_arc((0, 0), 36, 0, 90, dxfattribs={"layer": "A-DOOR"})
    blk.add_line((0, 0), (36, 0), dxfattribs={"layer": "A-DOOR"})
    o = (37.123, 11.987)
    P = lambda pts: [(x * 12 + o[0], y * 12 + o[1]) for x, y in pts]  # noqa: E731
    msp = doc.modelspace()
    msp.add_lwpolyline(P([(-0.5, -0.5), (40.5, -0.5), (40.5, 30.5), (-0.5, 30.5)]), close=True,
                       dxfattribs={"layer": "A-WALL"})
    # interior free-space ring: corridor along the bottom (y 0..8), three rooms above (y 8.5..30),
    # partitions 0.5 ft thick at x=13..13.5 and x=26.5..27, doors 3 ft wide in the corridor wall
    # doors: gaps in the corridor wall (y 8..8.5) at x 8..11 (room A) and x 21..24 (room B);
    # room C (x 27..40) opens on the corridor through a door gap at x 33..36
    ring = [(0, 0), (40, 0), (40, 8), (36, 8), (36, 8.5), (40, 8.5), (40, 30), (27, 30), (27, 8.5),
            (33, 8.5), (33, 8), (24, 8), (24, 8.5), (26.5, 8.5), (26.5, 30), (13.5, 30), (13.5, 8.5),
            (21, 8.5), (21, 8), (11, 8), (11, 8.5), (13, 8.5), (13, 30), (0, 30), (0, 8.5), (8, 8.5),
            (8, 8), (0, 8)]
    msp.add_lwpolyline(P(ring), close=True, dxfattribs={"layer": "A-WALL"})
    if with_doors:
        for x in (8, 21, 33):
            msp.add_blockref("DOOR", P([(x, 8.5)])[0], dxfattribs={"layer": "A-DOOR"})
    for name, (x, y) in (("ROOM A", (5, 20)), ("ROOM B", (19, 20)), ("ROOM C", (33, 20)), ("CORRIDOR", (20, 4))):
        msp.add_text(name, height=12, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(P([(x, y)])[0])
    doc.saveas(path)
    return path


def test_single_ring_wall_outline_splits_at_doors(tmp_path):
    m = run_pipeline(make_single_ring_plan(tmp_path / "ring.dxf"), tmp_path).model
    assert _labels(m) == ["CORRIDOR", "ROOM A", "ROOM B", "ROOM C"], [(r.label, r.subtype) for r in m.elements_of("room")]


def test_single_ring_without_doors_is_flagged_not_invented(tmp_path):
    m = run_pipeline(make_single_ring_plan(tmp_path / "ring.dxf", with_doors=False), tmp_path).model
    rooms = m.elements_of("room")
    assert len(rooms) == 1 and rooms[0].subtype == "suspected_merged_region"


def test_symbol_block_text_is_not_a_room_label(tmp_path):
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=True)
    doc = ezdxf.readfile(p)
    doc.layers.add("LIGHTING"); doc.layers.add("NOTES")   # a layer name FireAI does not recognize
    for t in doc.modelspace().query("TEXT MTEXT"):     # labels on a generic text layer (as in real files)
        t.dxf.layer = "NOTES"
    sw = doc.blocks.new("SWITCH3")
    sw.add_circle((0, 0), 0.2, dxfattribs={"layer": "0"})
    sw.add_text("3", height=0.3, dxfattribs={"layer": "0"}).set_placement((0.3, 0))
    doc.modelspace().add_blockref("SWITCH3", (30, 15), dxfattribs={"layer": "LIGHTING"})
    doc.saveas(p)
    m = run_pipeline(p, tmp_path).model
    right = next(r for r in m.elements_of("room") if r.properties.get("name") == "SALES FLOOR")
    assert right.label == "SALES FLOOR" and right.properties.get("number") is None


def test_room_tag_block_text_is_still_a_label(tmp_path):
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=True, right_label="")
    doc = ezdxf.readfile(p)
    tag = doc.blocks.new("ROOM_TAG")
    tag.add_text("STORAGE", height=1, dxfattribs={"layer": "0"}).set_placement((0, 0))
    doc.modelspace().add_blockref("ROOM_TAG", (27, 12))
    doc.saveas(p)
    m = run_pipeline(p, tmp_path).model
    assert "STORAGE" in _labels(m)


def test_bay_window_gap_is_window_not_doorless_opening(tmp_path):
    """Exterior wall pieces collinear with an 8 ft gap, no jambs, window drawn 1 ft outside."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    for n in ("A-WALL", "A-GLAZ"):
        doc.layers.add(n)
    msp = doc.modelspace()
    for (a, b) in [((0, 0), (10, 0)), ((18, 0), (30, 0)), ((0, 0.5), (10, 0.5)), ((18, 0.5), (30, 0.5))]:
        msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline([(10, -1.5), (18, -1.5), (18, -1.0), (10, -1.0)], close=True, dxfattribs={"layer": "A-GLAZ"})
    doc.saveas(tmp_path / "bay.dxf")
    m = run_pipeline(tmp_path / "bay.dxf", tmp_path).model
    (op,) = m.wall_model["openings"]
    assert op["kind"] == "window" and op["window_element_ids"] and op["confidence"] == 0.5
    assert op["requires_verification"] and m.wall_model["stats"]["doorless_openings"] == 0
