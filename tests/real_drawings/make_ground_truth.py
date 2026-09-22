"""Writes tests/real_drawings/ground_truth/REAL_###.json (committed, anonymized).

Ground truth was produced by AI-assisted visual review of each drawing's source
rendering and file tables (NOT from FireAI's interpretation), and is PENDING HUMAN
CONFIRMATION. Assertions: EXACT (verifiable fact from the file or unambiguous
drawing content), APPROXIMATE (visual count/estimate), NOT_EVALUATED (cannot be
established from the drawing — never guessed).
"""

import json
from pathlib import Path

OUT = Path(__file__).parent / "ground_truth"
REVIEW = {"method": "AI-assisted visual review of source.png + DXF tables; independent of FireAI output",
          "status": "PENDING_HUMAN_CONFIRMATION", "date": "2026-09-22"}


def A(value, assertion, note=None):
    d = {"value": value, "assertion": assertion}
    if note:
        d["note"] = note
    return d


GT = {
    "REAL_001": {
        "drawing_type": A("fire sprinkler / fire alarm drawing: tenant-suite key plan + piping/riser diagrams", "APPROXIMATE"),
        "is_floor_plan_of_building": A("partial (key plan only; architectural background is in XREFs)", "APPROXIMATE"),
        "units": A("in", "APPROXIMATE", "Header unitless ($INSUNITS=0). Paper-space viewport scale 1:96 and title text "
                                      "'SCALE: 1/8\" = 1'-0\"' are both consistent with inches. Evidence, not declaration."),
        "xrefs": A(2, "EXACT", "two external references in the block table; files not supplied"),
        "separate_views_in_model_space": A(">=3 (key plan, piping diagram groups)", "APPROXIMATE"),
        "rooms": A("6 tenant suites + common area on key plan (hatched boundaries with labels)", "APPROXIMATE"),
        "walls": A("NOT_PRESENT_IN_FILE (in unresolved XREFs)", "APPROXIMATE"),
        "existing_fire_protection": A("present: sprinkler piping, heads, risers", "APPROXIMATE", "counts NOT_EVALUATED"),
        "expected_behavior": ["needs_human_input: UNIT_DETECTION_FAILED (units undeclared)",
                              "with units supplied: XREF_NOT_RESOLVED and MULTIPLE_DRAWING_REGIONS"],
    },
    "REAL_002": {
        "drawing_type": A("two-storey residential floor plans (floor 1 and floor 2 side by side)", "EXACT"),
        "units": A("in", "EXACT", "header $INSUNITS=1; plan size (~33 x 40 ft per floor) is plausible"),
        "separate_views_in_model_space": A(2, "EXACT"),
        "rooms_labeled": A(["LAUNDRY", "B/R", "HALL", "KITCHEN", "STUDY", "DINING ROOM", "FORUM", "BEDROOM", "BEDROOM",
                            "WALK-IN CLOSET", "B/R", "HALL", "HALL", "MASTER BEDROOM"], "APPROXIMATE",
                           "visible room labels; one first-floor label partly obscured (NOT_EVALUATED)"),
        "finish_notes_present": A(["HRWD FLOOR", "TILE FLOOR"], "EXACT"),
        "walls": A("double-line exterior and interior walls on layer 'Walls'", "EXACT"),
        "doors": A(16, "APPROXIMATE", "Door dynamic-block copies plus bifold/french door blocks"),
        "windows": A(42, "APPROXIMATE", "Window dynamic-block copies (each includes a tag hexagon). Initial visual "
                                        "reading 'tags are not windows' was WRONG — corrected by effective block names"),
        "columns": A(5, "APPROXIMATE", "porch posts"),
        "stairs": A(2, "EXACT", "one per floor"),
        "fixtures_and_equipment": A("receptacles, switches, lighting, appliances, sinks, toilets (not building elements)",
                                    "EXACT"),
        "expected_behavior": ["two drawing regions reported", "rooms must not be merged across doors with concatenated names"],
    },
    "REAL_003": {
        "drawing_type": A("same plan as REAL_002, metric edition", "EXACT"),
        "units": A("m", "EXACT", "header $INSUNITS=6"),
        "extents_match_REAL_002_within": A("1%", "APPROXIMATE"),
        "structural_difference_from_REAL_002": A("walls drawn as exploded LINEs (not polylines); porch posts not on a "
                                                 "column layer", "EXACT"),
        "separate_views_in_model_space": A(2, "EXACT"),
        "stairs": A(2, "EXACT"),
    },
    "REAL_004": {
        "drawing_type": A("building/stair SECTIONS and details (4 views) — not a floor plan", "EXACT"),
        "units": A("in", "EXACT", "header $INSUNITS=1"),
        "separate_views_in_model_space": A(4, "APPROXIMATE"),
        "rooms": A(0, "EXACT", "no plan view; rooms cannot exist in a section"),
        "multileaders": A(36, "EXACT", "from entity census"),
        "doors": A("door ELEVATIONS in sections (not plan doors)", "APPROXIMATE"),
        "expected_behavior": ["no rooms", "multiple regions", "section-view content must not be presented as a plan"],
    },
    "REAL_005": {
        "drawing_type": A("civil site plan: parcel, pond, road, one building footprint", "EXACT"),
        "units": A("NOT_EVALUATED", "NOT_EVALUATED",
                   "header says inches, which makes the parcel ~66 ft wide — implausible for a site with a road and pond; "
                   "likely feet, but no dimension or scale text exists to verify"),
        "rooms": A(0, "EXACT"),
        "building_footprint": A(1, "APPROXIMATE"),
    },
    "REAL_006": {
        "drawing_type": A("3D visualization model (solids/meshes); no 2D plan linework", "APPROXIMATE"),
        "expected_behavior": ["fail closed (GEOMETRY_EXTRACTION_FAILED) — no invented 2D geometry"],
    },
    "REAL_007": {
        "drawing_type": A("LibreDWG 'example' feature test drawing (not a building)", "EXACT"),
        "units": A("mm", "EXACT", "header $INSUNITS=4"),
        "extents": A("~11 km zig-zag polyline + giant text", "EXACT"),
        "rooms": A(0, "EXACT"),
        "dwg_dxf_equivalence": A("geometry identical for shared entities; converter omits ACAD_TABLE", "EXACT"),
    },
    "REAL_008": {
        "drawing_type": A("same 'example' drawing, R14 format", "EXACT"),
        "units": A("NOT_DECLARED (R14 predates $INSUNITS)", "EXACT"),
        "dwg_dxf_equivalence": A("converter omits 2 WIPEOUT, 2 INSERT, 1 proxy entity", "EXACT"),
    },
    "REAL_009": {
        "drawing_type": A("same 'example' drawing, 2018 format", "EXACT"),
        "units": A("mm", "EXACT"),
        "dwg_dxf_equivalence": A("geometry identical for shared entities; converter omits ACAD_TABLE", "EXACT"),
    },
    "REAL_010": {
        "drawing_type": A("dynamic-block library sheet (cars, trees, furniture, north arrows, wall/door blocks, test dims)",
                          "EXACT"),
        "is_building": A(False, "EXACT"),
        "rooms": A(0, "EXACT"),
        "notes": A("north arrow is on layer 'Title Box' (title-block classification is layer-driven and defensible)",
                   "EXACT"),
    },
    "REAL_011": {
        "drawing_type": A("nested block transform test matrix (rotation 0/45/90 x mirror X/Y)", "EXACT"),
        "is_building": A(False, "EXACT"),
        "nested_block_placement": A("FireAI-extracted linework coincides with ezdxf rendering for all inserts",
                                    "EXACT", "visual check of overlay"),
    },
}


def main():
    OUT.mkdir(exist_ok=True)
    for k, v in GT.items():
        (OUT / f"{k}.json").write_text(json.dumps({"id": k, "review": REVIEW, **v}, indent=2), encoding="utf-8")
    print(f"wrote {len(GT)} ground-truth records")


if __name__ == "__main__":
    main()
