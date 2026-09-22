"""Regression tests for failure CLASSES found in the Milestone 1.5 real-drawing corpus.

Each test reproduces a class with a synthetic fixture (no proprietary data):
MULTIPLE_DRAWING_REGIONS, DOOR_OPENING_BREAKS_ROOM, TEXT_ASSOCIATION_FAILED (label
concatenation, finish notes), DYNAMIC_BLOCK_UNSUPPORTED, MULTILEADER_UNSUPPORTED,
wall qualifiers (above/below/demo), UNIT_METADATA_MISSING evidence, and DWG
conversion entity loss.
"""

import math
import shutil
from collections import Counter
from pathlib import Path

import pytest

from conftest import run_pipeline
from fireai.ingest.dwg import ConversionResult, DwgConverter, LibreDwgConverter
from fireai.ingest.dwg_audit import DwgCensus, compare, dxf_census, normalize_type
from fixtures import builders as B


def _codes(m):
    return {t.code for t in m.diagnostics.review_triggers}


# ── MULTIPLE_DRAWING_REGIONS ─────────────────────────────────────────────────

def test_two_plans_in_model_space_are_reported(tmp_path):
    m = run_pipeline(B.make_two_plans(tmp_path / "two.dxf"), tmp_path).model
    sig = [r for r in m.view_regions if r["significant"]]
    assert len(sig) == 2
    assert "MULTIPLE_DRAWING_REGIONS" in _codes(m)
    regions = {el.properties.get("view_region") for el in m.elements_of("room")}
    assert regions == {"V1", "V2"}


def test_single_plan_with_title_block_is_one_significant_region(results):
    m = results["office_in"].model
    assert len([r for r in m.view_regions if r["significant"]]) == 1
    assert "MULTIPLE_DRAWING_REGIONS" not in _codes(m)


# ── DOOR_OPENING_BREAKS_ROOM + TEXT_ASSOCIATION_FAILED ───────────────────────

def test_opening_without_door_yields_flagged_merged_region_not_a_concatenated_name(tmp_path):
    m = run_pipeline(B.make_walls_only(tmp_path / "open.dxf", with_door=False), tmp_path).model
    rooms = m.elements_of("room")
    assert len(rooms) == 1
    r = rooms[0]
    assert r.label is None and r.subtype == "suspected_merged_region"
    assert set(r.properties["name_candidates"]) == {"OFFICE", "SALES FLOOR"}
    assert r.requires_verification and r.confidence <= 0.3
    assert "ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS" in _codes(m)


@pytest.mark.parametrize("closed_pieces", [False, True])
def test_door_opening_closure_separates_rooms(tmp_path, closed_pieces):
    m = run_pipeline(B.make_walls_only(tmp_path / "door.dxf", with_door=True, closed_wall_pieces=closed_pieces),
                     tmp_path).model
    rooms = {r.label: r for r in m.elements_of("room")}
    assert set(rooms) == {"OFFICE", "SALES FLOOR"}, [r.properties for r in m.elements_of("room")]
    left, right = rooms["OFFICE"], rooms["SALES FLOOR"]
    assert math.isclose(left.properties["area_sf"], 19.25 * 19.0, abs_tol=0.01)
    assert math.isclose(right.properties["area_sf"], 19.25 * 19.0, abs_tol=0.01)
    for r in (left, right):
        assert "G-DOOR-OPENING-CLOSURE" in r.rules
        assert r.properties["door_closures"] and r.requires_verification
    # analysis lines are never walls
    wall_ids = {sid for w in m.elements_of("wall") for sid in w.source_entity_ids}
    assert all(m.entity(i).layer == "WALLS" for i in wall_ids)


def test_finish_notes_are_not_room_names_but_real_names_are_kept(tmp_path):
    m = run_pipeline(B.make_walls_only(tmp_path / "door.dxf", with_door=True), tmp_path).model
    office = next(r for r in m.elements_of("room") if r.label == "OFFICE")
    assert office.properties["finish_notes"] == ["HRWD FLOOR"]
    assert any(r.label == "SALES FLOOR" for r in m.elements_of("room"))  # SALES is not finish vocabulary


def test_is_finish_note_vocabulary():
    from fireai.interpret.text import is_finish_note
    for note in ("HRWD FLOOR", "TILE FLOOR", "CARPET", "9'-0\" CLG", "VCT & RUBBER BASE", "GFI"):
        assert is_finish_note(note), note
    for name in ("SALES FLOOR", "OFFICE", "MASTER BEDROOM", "FLOOR 2 LOBBY", "TILE STORAGE"):
        assert not is_finish_note(name), name


# ── DYNAMIC_BLOCK_UNSUPPORTED ────────────────────────────────────────────────

def test_dynamic_block_effective_name_drives_classification(tmp_path):
    m = run_pipeline(B.make_dynamic_door(tmp_path / "dyn.dxf"), tmp_path).model
    blk = next(b for b in m.blocks if b.name.startswith("*U"))
    assert blk.effective_name == "Door" and blk.inferred_role == "door"
    doors = m.elements_of("door")
    assert len(doors) == 1
    assert any("effective name of anonymous dynamic block" in ev for ev in doors[0].evidence)


# ── MULTILEADER_UNSUPPORTED ──────────────────────────────────────────────────

def test_multileader_text_is_extracted(tmp_path):
    m = run_pipeline(B.make_multileader(tmp_path / "ml.dxf"), tmp_path).model
    ml = next(e for e in m.entities if e.type == "MULTILEADER")
    assert ml.supported and ml.source.text == "GYP BD CEILING"
    assert ml.attributes["partial"].startswith("text only")
    assert "UNSUPPORTED_ENTITIES" not in {i.code for i in m.diagnostics.warnings + m.diagnostics.review_triggers}


# ── Wall qualifiers ──────────────────────────────────────────────────────────

def test_walls_above_or_demolished_are_qualified_not_plain_walls(tmp_path):
    m = run_pipeline(B.make_wall_qualifiers(tmp_path / "q.dxf"), tmp_path).model
    subtypes = Counter(w.subtype for w in m.elements_of("wall"))
    assert subtypes == {"wall_linework": 1, "qualified_above": 1, "qualified_demo": 1}
    for w in m.elements_of("wall"):
        if w.subtype.startswith("qualified_"):
            assert w.requires_verification and w.confidence <= 0.5 and "L-WALL-QUALIFIER" in w.rules


# ── UNIT_METADATA_MISSING: evidence offered, never applied ───────────────────

def test_unit_evidence_is_suggested_but_not_applied(tmp_path):
    r = run_pipeline(B.make_unitless_with_scale_evidence(tmp_path / "ev.dxf"), tmp_path)
    assert r.processing_status == "needs_human_input"            # still blocked
    ev = r.report["unit_resolution_required"]["evidence"]
    assert ev["status"] == "suggestion_only_not_applied"
    assert ev["suggested_units"] == "in" and not ev["conflicting"]
    assert r.model.units.resolved is False and r.model.units.scale_to_normalized is None
    assert all(e.normalized is None for e in r.model.entities)


def test_conflicting_unit_evidence_gives_no_suggestion():
    from fireai.ingest.units import unit_evidence
    vps = [{"layout": "A", "paper_units_per_model_unit": 1 / 96, "paper_units": "in"}]
    ev = unit_evidence(vps, ["SCALE: 1/8\" = 1'-0\"", "SCALE: 1/96\" = 1'-0\""])
    assert ev["conflicting"] and ev["suggested_units"] is None


# ── DWG conversion entity loss ───────────────────────────────────────────────

def test_entity_type_normalization():
    assert normalize_type("DIMENSION_LINEAR") == "DIMENSION"
    assert normalize_type("VERTEX_2D") is None and normalize_type("TABLE") == "ACAD_TABLE"


def _census_of(dxf_path, extra=(), drop=()) -> DwgCensus:
    """A DWG-side census that mirrors a DXF, plus extra (lost) and minus dropped (added) entities."""
    import ezdxf
    d = dxf_census(ezdxf.readfile(dxf_path))
    c = DwgCensus()
    for h, x in d.entities.items():
        if h in drop:
            continue
        c.entities[h] = {"type": x["type"], "owner": None, "space": x["space"] if x["space"] != "block" else "block",
                         "fp": dict(x["fp"])}
        c.counts[x["type"]] += 1
    for h, t, space in extra:
        c.entities[h] = {"type": t, "owner": None, "space": space, "fp": {}}
        c.counts[t] += 1
    c.layers = set(d.layers)
    c.block_names = {i: n for i, n in enumerate(sorted(d.block_names))}
    return c


def test_handle_level_loss_is_not_masked_by_counts(fx):
    """Same per-type COUNTS on both sides, but two INSERTs replaced by two others: the count-level
    audit of M1.5 reported no loss (the R14 REAL_008 masking case); the handle-level audit must."""
    import ezdxf
    doc = ezdxf.readfile(fx["office_ft"])
    d = dxf_census(doc)
    dropped = [h for h, x in d.entities.items() if x["type"] == "LINE"][:2]
    census = _census_of(fx["office_ft"], extra=[(0xFFFF01, "LINE", "model"), (0xFFFF02, "LINE", "model")],
                        drop=dropped)
    assert census.counts == d.counts                                     # counts identical
    res = compare(census, d, "t")
    assert res["lost"] == {"LINE": 2} and res["significance"] == "material"
    assert res["added_in_dxf"] == {"LINE": 2}
    assert res["lost_by_significance"]["material"]["LINE"]["handles_sample"] == ["FFFF01", "FFFF02"]


def test_loss_classification(fx):
    import ezdxf
    d = dxf_census(ezdxf.readfile(fx["office_ft"]))
    minor = compare(_census_of(fx["office_ft"], extra=[(0xFFFF10, "WIPEOUT", "model")]), d, "t")
    assert minor["significance"] == "minor"
    unref = compare(_census_of(fx["office_ft"], extra=[(0xFFFF11, "LINE", "block")]), d, "t")
    assert unref["significance"] == "minor"                               # only in an unreferenced block
    review = compare(_census_of(fx["office_ft"], extra=[(0xFFFF12, "ACAD_TABLE", "paper")]), d, "t")
    assert review["significance"] == "review"
    none = compare(_census_of(fx["office_ft"]), d, "t")
    assert none["significance"] == "none" and none["lost"] == {}


def test_geometry_change_is_material(fx):
    import ezdxf
    d = dxf_census(ezdxf.readfile(fx["office_ft"]))
    c = _census_of(fx["office_ft"])
    h = next(h for h, x in c.entities.items() if x["type"] == "LINE")
    c.entities[h]["fp"]["end"] = [c.entities[h]["fp"]["end"][0] + 1.0, c.entities[h]["fp"]["end"][1], 0.0]
    res = compare(c, d, "t")
    assert res["significance"] == "material" and res["geometry_mismatch_count"] == 1


class _AuditConverter(DwgConverter):
    name = "test-audit"

    def __init__(self, dxf, census_fn):
        self.dxf, self._census_fn = dxf, census_fn

    def available(self):
        return True

    def _run(self, dwg_path, out_dir, timeout_s):
        out = out_dir / "c.dxf"
        shutil.copyfile(self.dxf, out)
        return ConversionResult(out, self.name, "1", ["x"], audit_method="test")

    def source_census(self, dwg_path, work_dir, timeout_s):
        return self._census_fn()


def test_conversion_loss_triggers_review(fx, tmp_path):
    conv = _AuditConverter(fx["office_ft"], lambda: (_census_of(fx["office_ft"], extra=[
        (0xFFFF01, "INSERT", "model"), (0xFFFF02, "INSERT", "model")]), ""))
    m = run_pipeline(fx["fake_dwg"], tmp_path, converter=conv).model
    assert "DWG_CONVERSION_LOST_ENTITIES" in _codes(m)
    assert m.source.conversion_audit["lost"] == {"INSERT": 2}
    assert m.source.conversion_audit["significance"] == "material"
    t = next(t for t in m.diagnostics.review_triggers if t.code == "DWG_CONVERSION_LOST_ENTITIES")
    assert t.severity == "error"


def test_material_conversion_loss_blocks_engineering(fx, tmp_path):
    from fireai.review.gate import engineering_readiness
    conv = _AuditConverter(fx["office_ft"], lambda: (_census_of(fx["office_ft"], extra=[
        (0xFFFF01, "LINE", "model")]), ""))
    m = run_pipeline(fx["fake_dwg"], tmp_path, converter=conv).model
    assert any("material loss" in b for b in engineering_readiness(m, None)["blockers"])


def test_missing_conversion_audit_triggers_review(fx, tmp_path):
    m = run_pipeline(fx["fake_dwg"], tmp_path,
                     converter=_AuditConverter(fx["office_ft"], lambda: (None, "unavailable: n/a"))).model
    assert "DWG_CONVERSION_AUDIT_UNAVAILABLE" in _codes(m)


@pytest.mark.skipif(not (shutil.which("dwg2dxf") and shutil.which("dxf2dwg") and shutil.which("dwgread")),
                    reason="LibreDWG tools not installed")
def test_real_libredwg_roundtrip_dwg_equals_dxf(fx, tmp_path):
    """Real converter integration: DXF -> (dxf2dwg) -> DWG -> (dwg2dxf) -> FireAI model must equal
    the DXF's model geometrically. dxf2dwg is LibreDWG's experimental writer, so this proves the
    conversion plumbing and audit, not fidelity on AutoCAD-authored DWGs (see real corpus pairs)."""
    import subprocess
    dwg = tmp_path / "office.dwg"
    subprocess.run(["dxf2dwg", "-y", "-o", str(dwg), str(fx["office_in"])], capture_output=True, timeout=120)
    assert dwg.exists() and dwg.read_bytes()[:4] == b"AC10"
    a = run_pipeline(dwg, tmp_path, dwg_converter="libredwg").model
    b = run_pipeline(fx["office_in"], tmp_path).model
    assert a.source.format == "dwg" and a.source.converter["name"] == "libredwg"
    assert a.source.conversion_audit["status"] == "ok" and a.source.conversion_audit["lost"] == {}
    assert a.units.resolved_units == b.units.resolved_units == "in"
    assert math.isclose(a.bounds_normalized.width, b.bounds_normalized.width, abs_tol=1e-6)
    ra = sorted((r.label, r.properties["area_sf"]) for r in a.elements_of("room"))
    rb = sorted((r.label, r.properties["area_sf"]) for r in b.elements_of("room"))
    assert ra == rb
    assert Counter(e.category for e in a.elements) == Counter(e.category for e in b.elements)


def test_libredwg_adapter_reports_version():
    c = LibreDwgConverter()
    if not c.available():
        pytest.skip("dwg2dxf not installed")
    assert c.version().startswith("dwg2dxf 0.14")
    assert Path(c.executable).with_name("dwgread").exists()


# ── VIEW TITLE vs TITLE BLOCK (found after resolving dynamic-block names) ────

@pytest.mark.parametrize("name,role", [
    ("Drawing Title", "view_title"), ("Drawing Block Title", "view_title"), ("SECTION TITLE", "view_title"),
    ("View-Title", "view_title"), ("TITLEBLOCK", "title_block"), ("A1-TTLB", "title_block"), ("title", "title_block"),
])
def test_view_titles_are_not_title_blocks(name, role):
    from fireai.interpret.rules import classify_block
    assert classify_block(name).role == role
