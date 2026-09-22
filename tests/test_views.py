"""Drawing / view / plan separation (Milestone 1.6)."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from conftest import run_pipeline
from fireai.interpret.regions import classify_title


@pytest.mark.parametrize("text,expected", [
    ("FIRST FLOOR PLAN", "FLOOR_PLAN"),
    ("LEVEL 2 PLAN", "FLOOR_PLAN"),
    ("FIRE SPRINKLER PLAN", "FLOOR_PLAN"),
    ("REFLECTED CEILING PLAN", "REFLECTED_CEILING_PLAN"),
    ("FIRST FLOOR RCP", "REFLECTED_CEILING_PLAN"),
    ("BUILDING SECTION A-A", "SECTION"),
    ("STAIR SECTION 1", "SECTION"),
    ("SECTION 3/A5.1", "SECTION"),
    ("EAST ELEVATION", "ELEVATION"),
    ("TYPICAL DETAIL", "DETAIL"),
    ("RISER DETAIL", "DETAIL"),
    ("FIRE SPRINKLER RISER DIAGRAM", "RISER_DIAGRAM"),
    ("SITE PLAN", "SITE_PLAN"),
    ("SYMBOL LEGEND", "LEGEND"),
    ("DOOR SCHEDULE", "SCHEDULE"),
    ('SECOND FLOOR PLAN 1/4" = 1\'-0"', "FLOOR_PLAN"),
    ("KEY PLAN", "FLOOR_PLAN"),
    # not view titles
    ("SEE SECTION 3/A5.1", None),
    ('ELEV. 5\'-10 1/2" TOP OF LANDING', None),
    ("ALL PIPING SHALL BE INSTALLED PER NFPA 13 SECTION 8.15", None),
    ("ROOF PLAN", None),                  # a plan, but not a floor plan: note only
    ('1/4" = 1\'-0"', None),
    ("OFFICE 101", None),
    ("HRWD FLOOR", None),
])
def test_classify_title(text, expected):
    assert classify_title(text)[0] == expected


def test_roof_plan_is_noted_not_typed():
    vt, note = classify_title("ROOF PLAN")
    assert vt is None and "roof" in note


def _rooms_block(msp, ox, oy, layer_wall="A-WALL", labels=("OFFICE", "STORAGE"), door=True):
    """Two 30x40 ft rooms drawn with room polylines + walls + labels."""
    msp.add_lwpolyline([(ox, oy), (ox + 60, oy), (ox + 60, oy + 40), (ox, oy + 40)], close=True,
                       dxfattribs={"layer": layer_wall})
    msp.add_line((ox + 30, oy), (ox + 30, oy + 40), dxfattribs={"layer": layer_wall})
    for i, nm in enumerate(labels):
        x0 = ox + i * 30
        msp.add_lwpolyline([(x0, oy), (x0 + 30, oy), (x0 + 30, oy + 40), (x0, oy + 40)], close=True,
                           dxfattribs={"layer": "A-AREA"})
        msp.add_text(nm, height=1, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement((x0 + 10, oy + 20))
    if door:
        msp.add_blockref("DOOR", (ox + 30, oy + 10))


def make_plan_and_section(path: Path, section_title="BUILDING SECTION A-A", plan_title="FIRST FLOOR PLAN",
                          section_layer="A-WALL") -> Path:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    for n in ("A-WALL", "A-AREA", "A-AREA-IDEN", "A-ANNO-TTLB", "A-DOOR", "A-SECT-WALL"):
        doc.layers.add(n)
    blk = doc.blocks.new("DOOR")
    blk.add_arc((0, 0), 3, 0, 90, dxfattribs={"layer": "A-DOOR"})
    msp = doc.modelspace()
    _rooms_block(msp, 0, 0)
    msp.add_text(plan_title, height=2.5, dxfattribs={"layer": "A-ANNO-TTLB"}).set_placement((5, -8))
    # A "section" drawn to the right: floors/walls as closed rectangles with labels that would look like rooms
    _rooms_block(msp, 200, 0, layer_wall=section_layer, labels=("LEVEL 1", "LEVEL 2"))
    if section_title:
        msp.add_text(section_title, height=2.5, dxfattribs={"layer": "A-ANNO-TTLB"}).set_placement((205, -8))
    doc.saveas(path)
    return path


def regions_by_type(m):
    return {r["view_type"]: r for r in m.view_regions if r["significant"]}


def test_section_region_gets_no_room_logic(tmp_path):
    m = run_pipeline(make_plan_and_section(tmp_path / "ps.dxf"), tmp_path).model
    rt = regions_by_type(m)
    assert set(rt) == {"FLOOR_PLAN", "SECTION"}
    plan, sec = rt["FLOOR_PLAN"], rt["SECTION"]
    assert plan["room_logic"] == "applied" and sec["room_logic"] == "skipped"
    assert any("BUILDING SECTION A-A" in e for e in sec["view_type_evidence"])
    rooms = m.elements_of("room")
    assert sorted(r.label for r in rooms) == ["OFFICE", "STORAGE"]
    assert all(r.properties["view_region"] == plan["id"] for r in rooms)
    # the section's labels are not reported as orphaned room labels
    assert "UNASSOCIATED_ROOM_LABELS" not in {t.code for t in m.diagnostics.review_triggers}
    assert any(w.code == "ROOM_LOGIC_SKIPPED_FOR_NON_PLAN_VIEWS" for w in m.diagnostics.warnings)
    # walls in the section are still recorded as source-derived linework, tagged with their view type
    sec_walls = [w for w in m.elements_of("wall") if w.properties.get("view_region") == sec["id"]]
    assert sec_walls and all(w.properties["view_type"] == "SECTION" for w in sec_walls)


def test_every_region_has_id_bounds_sources_type_evidence_confidence_review(tmp_path):
    m = run_pipeline(make_plan_and_section(tmp_path / "ps.dxf"), tmp_path).model
    for r in m.view_regions:
        for k in ("id", "uid", "bbox_ft", "top_level_entity_ids", "view_type", "view_type_evidence",
                  "view_type_confidence", "review_state", "room_logic", "view_type_source"):
            assert k in r, k
        assert r["view_type_source"] == "fireai"
        assert 0.0 <= r["view_type_confidence"] <= 0.9


def test_region_uids_are_persistent(tmp_path):
    p = make_plan_and_section(tmp_path / "ps.dxf")
    a = [r["uid"] for r in run_pipeline(p, tmp_path).model.view_regions]
    b = [r["uid"] for r in run_pipeline(p, tmp_path).model.view_regions]
    assert a == b and len(set(a)) == len(a)


def test_layer_names_alone_classify_section(tmp_path):
    p = make_plan_and_section(tmp_path / "ps.dxf", section_title=None)
    doc = ezdxf.readfile(p)
    doc.layers.add("A-SECT-FLOR")
    for e in list(doc.modelspace()):          # redraw the right-hand block entirely on section layers
        pts = [v[:2] for v in e.get_points()] if e.dxftype() == "LWPOLYLINE" else None
        x = pts[0][0] if pts else (e.dxf.start.x if e.dxftype() == "LINE" else e.dxf.insert.x)
        if x >= 200:
            e.dxf.layer = "A-SECT-WALL" if e.dxftype() in ("LINE", "LWPOLYLINE") else "A-SECT-FLOR"
    doc.saveas(p)
    m = run_pipeline(p, tmp_path).model
    rt = regions_by_type(m)
    assert "SECTION" in rt and rt["SECTION"]["room_logic"] == "skipped"
    assert rt["SECTION"]["review_state"] == "review_required"     # 0.6 < 0.7: layer names alone need review
    assert "V-LAYER-TOKENS" in rt["SECTION"]["view_type_rules"]


def test_untitled_plain_region_stays_unknown_and_keeps_room_logic(tmp_path):
    m = run_pipeline(make_plan_and_section(tmp_path / "ps.dxf", section_title=None), tmp_path).model
    types = sorted(r["view_type"] for r in m.view_regions if r["significant"])
    # the untitled right-hand block has walls + room boundaries/labels + door: weak/strong plan evidence only
    assert types.count("FLOOR_PLAN") + types.count("UNKNOWN") == 2
    assert all(r["room_logic"] == "applied" for r in m.view_regions if r["significant"])


def test_conflicting_titles_are_unknown_and_flagged(tmp_path):
    p = make_plan_and_section(tmp_path / "ps.dxf", section_title="FIRST FLOOR PLAN")
    doc = ezdxf.readfile(p)
    doc.modelspace().add_text("WALL SECTION 3", height=2.5, dxfattribs={"layer": "A-ANNO-TTLB"}).set_placement((205, -12))
    doc.saveas(p)
    m = run_pipeline(p, tmp_path).model
    right = max((r for r in m.view_regions if r["significant"]), key=lambda r: r["bbox_ft"][0])
    assert right["view_type"] == "UNKNOWN" and right["review_state"] == "review_required"
    assert {"FLOOR_PLAN", "SECTION"} <= set(right["view_type_candidates"])
    assert "VIEW_TYPE_UNCERTAIN" in {t.code for t in m.diagnostics.review_triggers}


def test_two_floor_plans_are_not_auto_selected(tmp_path):
    m = run_pipeline(make_plan_and_section(tmp_path / "ps.dxf", section_title="SECOND FLOOR PLAN"), tmp_path).model
    codes = {t.code for t in m.diagnostics.review_triggers}
    assert {"MULTIPLE_DRAWING_REGIONS", "MULTIPLE_FLOOR_PLANS"} <= codes
    assert not any(r.get("selected_for_engineering") for r in m.view_regions)


def make_viewport_sheet(path: Path) -> Path:
    """Model space: two identical untitled room blocks. Paper space: a viewport on each with a title below."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    for n in ("A-WALL", "A-AREA", "A-AREA-IDEN", "A-DOOR", "PS-TITLE"):
        doc.layers.add(n)
    blk = doc.blocks.new("DOOR")
    blk.add_arc((0, 0), 3, 0, 90, dxfattribs={"layer": "A-DOOR"})
    msp = doc.modelspace()
    _rooms_block(msp, 0, 0)
    _rooms_block(msp, 200, 0, labels=("A", "B"))
    lay = doc.layouts.new("SHEET 1")
    # 1/8" = 1'-0": 1 paper inch shows 8 ft; windows 80 x 60 ft centred on each block
    lay.add_viewport(center=(6, 8), size=(10, 7.5), view_center_point=(30, 20), view_height=60)
    lay.add_viewport(center=(20, 8), size=(10, 7.5), view_center_point=(230, 20), view_height=60)
    lay.add_text("SECOND FLOOR PLAN", height=0.25, dxfattribs={"layer": "PS-TITLE"}).set_placement((2, 3.8))
    lay.add_text("LONGITUDINAL SECTION", height=0.25, dxfattribs={"layer": "PS-TITLE"}).set_placement((16, 3.8))
    doc.saveas(path)
    return path


def test_viewport_titles_classify_model_regions(tmp_path):
    m = run_pipeline(make_viewport_sheet(tmp_path / "vp.dxf"), tmp_path).model
    sig = sorted((r for r in m.view_regions if r["significant"]), key=lambda r: r["bbox_ft"][0])
    assert [r["view_type"] for r in sig] == ["FLOOR_PLAN", "SECTION"]
    assert "V-TITLE-VIEWPORT" in sig[1]["view_type_rules"]
    assert sig[1]["viewport_links"][0]["title"] == "LONGITUDINAL SECTION"
    assert sig[1]["room_logic"] == "skipped"
    assert {r.label for r in m.elements_of("room")} == {"OFFICE", "STORAGE"}


def test_title_block_only_region(tmp_path):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    doc.layers.add("A-WALL"); doc.layers.add("G-TTLB")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (60, 0), (60, 40), (0, 40)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline([(300, 0), (340, 0), (340, 30), (300, 30)], close=True, dxfattribs={"layer": "G-TTLB"})
    msp.add_text("PROJECT X", height=1, dxfattribs={"layer": "G-TTLB"}).set_placement((305, 5))
    doc.saveas(tmp_path / "tb.dxf")
    m = run_pipeline(tmp_path / "tb.dxf", tmp_path).model
    tb = [r for r in m.view_regions if r["view_type"] == "TITLE_BLOCK"]
    assert len(tb) == 1 and tb[0]["room_logic"] == "skipped"
