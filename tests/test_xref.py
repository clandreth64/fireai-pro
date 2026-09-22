"""XREF ingestion (Milestone 1.6).

Ground truth for every case is computed by hand from the fixture definitions:
an XREF line from (0,0) to (10,0) in XREF units, inserted at P with rotation R
and scale S, must appear in the host at P + S*u*R(10,0) where u converts XREF
units to host units. Nothing is ever drawn for an XREF that is not loaded.
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import pytest

from conftest import run_pipeline
from fireai.schema import load_model

UNIT = {"in": 1, "ft": 2, "mm": 4, "m": 6}


def _doc(units: str | None):
    doc = ezdxf.new("R2018")
    if units:
        doc.header["$INSUNITS"] = UNIT[units]
    else:
        doc.header["$INSUNITS"] = 0
    doc.layers.add("A-WALL")
    return doc


def make_xref_file(path: Path, units: str | None, length: float = 10.0, refs: list[tuple[str, str]] = ()) -> Path:
    """XREF drawing: one wall line (0,0)-(length,0) on A-WALL, plus optional nested XREFs."""
    doc = _doc(units)
    msp = doc.modelspace()
    msp.add_line((0, 0), (length, 0), dxfattribs={"layer": "A-WALL"})
    for name, fname in refs:
        doc.add_xref_def(fname, name)
        msp.add_blockref(name, (0, 0))
    doc.saveas(path)
    return path


def make_host(path: Path, units: str | None = "ft", inserts=(("ARCH", "ARCH.dxf", (100, 50), 0, 1.0),),
              stored_path: str | None = None) -> Path:
    """Host drawing: its own 200 x 100 ft outline plus XREF inserts."""
    doc = _doc(units)
    msp = doc.modelspace()
    k = 1.0 if units in (None, "ft") else {"in": 12.0, "mm": 304.8, "m": 0.3048}[units]
    msp.add_lwpolyline([(0, 0), (200 * k, 0), (200 * k, 100 * k), (0, 100 * k)], close=True,
                       dxfattribs={"layer": "G-ANNO"})
    defined = set()
    for name, fname, ins, rot, sc in inserts:
        if name not in defined:
            doc.add_xref_def(stored_path or fname, name)
            defined.add(name)
        msp.add_blockref(name, ins, dxfattribs={"rotation": rot, "xscale": sc, "yscale": sc, "zscale": sc})
    doc.saveas(path)
    return path


def xref_children(model, file_name=None):
    return [e for e in model.entities if "xref_source" in e.attributes
            and (file_name is None or e.attributes["xref_source"]["file"] == file_name)]


def trig(model):
    return {t.code for t in model.diagnostics.review_triggers}


def _line_pts(e):
    return [tuple(round(c, 6) for c in p) for p in e.source.points]


def test_valid_xref_is_loaded_with_identity_and_placement(tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    host = make_host(tmp_path / "host.dxf")
    r = run_pipeline(host, tmp_path, xref_files=[x])
    assert r.processing_status == "completed"
    m = r.model
    (rec,) = m.xrefs
    assert rec.status == "resolved" and rec.resolved_file == "ARCH.dxf" and rec.matched_by == "exact_name"
    assert rec.unit_scale == 1.0 and rec.entity_count == 1
    (line,) = xref_children(m)
    assert _line_pts(line) == [(100.0, 50.0), (110.0, 50.0)]
    src = line.attributes["xref_source"]
    assert src["file"] == "ARCH.dxf" and src["sha256"] == rec.sha256 and src["handle"]
    assert line.layer == "ARCH|A-WALL"
    assert line.parent_id in rec.insert_entity_ids
    assert "XREF_NOT_RESOLVED" not in trig(m)
    assert any(w.code == "XREFS_LOADED" for w in m.diagnostics.warnings)
    # the XREF wall is interpreted and traceable back to the XREF entity
    walls = [w for w in m.elements_of("wall") if line.id in w.source_entity_ids]
    assert walls, "XREF wall line should be classified from its (prefixed) layer"


def test_missing_xref_never_invents_geometry(tmp_path):
    host = make_host(tmp_path / "host.dxf")
    r = run_pipeline(host, tmp_path)
    m = r.model
    (rec,) = m.xrefs
    assert rec.status == "missing" and not xref_children(m)
    assert "XREF_NOT_RESOLVED" in trig(m)
    t = next(t for t in m.diagnostics.review_triggers if t.code == "XREF_NOT_RESOLVED")
    assert "ARCH" in t.message and "ARCH.dxf" in t.message and t.entity_ids == rec.insert_entity_ids
    assert m.requires_human_review


def test_missing_when_supplied_files_do_not_match(tmp_path):
    other = make_xref_file(tmp_path / "OTHER.dxf", "ft")
    r = run_pipeline(make_host(tmp_path / "host.dxf"), tmp_path, xref_files=[other])
    assert r.model.xrefs[0].status == "missing" and not xref_children(r.model)


def test_stored_absolute_path_is_never_opened(tmp_path):
    """An existing file at the stored path must NOT be loaded unless supplied."""
    (tmp_path / "secret").mkdir()
    on_disk = make_xref_file(tmp_path / "secret" / "ARCH.dxf", "ft")
    host = make_host(tmp_path / "host.dxf", stored_path=str(on_disk))
    r = run_pipeline(host, tmp_path)
    assert r.model.xrefs[0].status == "missing" and not xref_children(r.model)
    assert r.model.xrefs[0].path_in_drawing == str(on_disk)


def test_rotated_xref(tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    host = make_host(tmp_path / "host.dxf", inserts=[("ARCH", "ARCH.dxf", (100, 50), 90, 1.0)])
    (line,) = xref_children(run_pipeline(host, tmp_path, xref_files=[x]).model)
    (a, b) = line.source.points
    assert a == pytest.approx((100, 50)) and b == pytest.approx((100, 60))


def test_scaled_xref(tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    host = make_host(tmp_path / "host.dxf", inserts=[("ARCH", "ARCH.dxf", (100, 50), 0, 2.0)])
    (line,) = xref_children(run_pipeline(host, tmp_path, xref_files=[x]).model)
    assert line.source.points[1] == pytest.approx((120, 50))


def test_two_inserts_of_one_xref_have_distinct_stable_uids(tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    ins = [("ARCH", "ARCH.dxf", (100, 50), 0, 1.0), ("ARCH", "ARCH.dxf", (20, 20), 0, 1.0)]
    host = make_host(tmp_path / "host.dxf", inserts=ins)
    m1 = run_pipeline(host, tmp_path, xref_files=[x]).model
    m2 = run_pipeline(host, tmp_path, xref_files=[x]).model
    u1 = sorted(e.uid for e in xref_children(m1))
    assert len(u1) == 2 and len(set(u1)) == 2
    assert u1 == sorted(e.uid for e in xref_children(m2))
    assert len(m1.xrefs) == 1 and len(m1.xrefs[0].insert_entity_ids) == 2


def test_nested_xref_composes_transforms(tmp_path):
    b = make_xref_file(tmp_path / "B.dxf", "ft", length=5.0)
    # A contains a 10 ft line and references B at its origin; place B inside A at (0,0).
    a = make_xref_file(tmp_path / "A.dxf", "ft", refs=[("B", "B.dxf")])
    host = make_host(tmp_path / "host.dxf", inserts=[("A", "A.dxf", (100, 50), 90, 1.0)])
    m = run_pipeline(host, tmp_path, xref_files=[a, b]).model
    st = {(x.name, x.depth): x.status for x in m.xrefs}
    assert st == {("A", 0): "resolved", ("B", 1): "resolved"}
    (lb,) = xref_children(m, "B.dxf")
    # B's line (0,0)-(5,0) -> rotated 90 with A -> (100,50)-(100,55)
    assert lb.source.points[0] == pytest.approx((100, 50)) and lb.source.points[1] == pytest.approx((100, 55))
    assert lb.block_path == ["A", "B"]


def test_circular_xref_detected_not_looped(tmp_path):
    make_xref_file(tmp_path / "A.dxf", "ft", refs=[("B", "B.dxf")])
    make_xref_file(tmp_path / "B.dxf", "ft", length=5.0, refs=[("A", "A.dxf")])
    host = make_host(tmp_path / "host.dxf", inserts=[("A", "A.dxf", (100, 50), 0, 1.0)])
    m = run_pipeline(host, tmp_path, xref_files=[tmp_path / "A.dxf", tmp_path / "B.dxf"]).model
    statuses = [(x.name, x.depth, x.status) for x in m.xrefs]
    assert ("A", 0, "resolved") in statuses and ("B", 1, "resolved") in statuses
    assert ("A", 2, "circular") in statuses
    assert "XREF_CIRCULAR" in trig(m)
    # A.dxf contributes its line and its INSERT of B exactly once — never re-loaded through the cycle
    assert sorted(e.type for e in xref_children(m, "A.dxf")) == ["INSERT", "LINE"]


def test_self_reference_is_circular(tmp_path):
    """A host that references a copy of itself (same bytes) is circular."""
    host = make_host(tmp_path / "host.dxf", inserts=[("SELF", "host.dxf", (0, 0), 0, 1.0)])
    m = run_pipeline(host, tmp_path, xref_files=[host]).model
    assert m.xrefs[0].status == "circular" and "XREF_CIRCULAR" in trig(m)


@pytest.mark.parametrize("host_u,xref_u,expected_end", [
    ("ft", "in", (100 + 10 / 12, 50)),     # 10 in -> 0.8333 ft
    ("in", "ft", (100 + 120, 50)),         # 10 ft -> 120 in
    ("ft", "mm", (100 + 10 / 304.8, 50)),
    ("m", "mm", (100 + 0.01, 50)),
])
def test_xref_with_differing_units_is_scaled(tmp_path, host_u, xref_u, expected_end):
    x = make_xref_file(tmp_path / "ARCH.dxf", xref_u)
    host = make_host(tmp_path / "host.dxf", units=host_u)
    m = run_pipeline(host, tmp_path, xref_files=[x]).model
    (line,) = xref_children(m)
    assert line.source.points[1][0] == pytest.approx(expected_end[0])
    assert line.source.points[1][1] == pytest.approx(expected_end[1])
    # normalized length in feet is the same physical 10 XREF units
    n = line.normalized.points
    ft = {"in": 1 / 12, "ft": 1.0, "mm": 1 / 304.8, "m": 1 / 0.3048}[xref_u] * 10
    assert math.dist(n[0], n[1]) == pytest.approx(ft)


def test_xref_with_undeclared_units_is_not_loaded(tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", None)
    m = run_pipeline(make_host(tmp_path / "host.dxf"), tmp_path, xref_files=[x]).model
    assert m.xrefs[0].status == "units_unresolved" and not xref_children(m)
    assert "XREF_UNITS_UNRESOLVED" in trig(m)


def test_xref_uses_host_unit_override(tmp_path):
    """Host header unitless + user override 'ft' -> XREF in inches scaled 1/12."""
    x = make_xref_file(tmp_path / "ARCH.dxf", "in")
    host = make_host(tmp_path / "host.dxf", units=None)
    m = run_pipeline(host, tmp_path, units="ft", xref_files=[x]).model
    assert m.xrefs[0].status == "resolved" and m.xrefs[0].unit_scale == pytest.approx(1 / 12)


def test_dxf_standing_in_for_referenced_dwg_is_flagged(tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    host = make_host(tmp_path / "host.dxf", inserts=[("ARCH", "ARCH.dwg", (100, 50), 0, 1.0)])
    m = run_pipeline(host, tmp_path, xref_files=[x]).model
    assert m.xrefs[0].status == "resolved" and m.xrefs[0].matched_by == "same_stem_other_extension"
    assert "XREF_SUBSTITUTED_FILE" in trig(m)


def test_unreadable_xref_is_load_failed(tmp_path):
    bad = tmp_path / "ARCH.dxf"
    bad.write_text("this is not a drawing")
    m = run_pipeline(make_host(tmp_path / "host.dxf"), tmp_path, xref_files=[bad]).model
    assert m.xrefs[0].status == "load_failed" and "XREF_NOT_RESOLVED" in trig(m)


def test_verification_fingerprint_depends_on_xref_content(tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    host = make_host(tmp_path / "host.dxf")
    f1 = run_pipeline(host, tmp_path, xref_files=[x]).model.verification.fingerprint
    make_xref_file(x, "ft", length=12.0)       # XREF revised
    f2 = run_pipeline(host, tmp_path, xref_files=[x]).model.verification.fingerprint
    assert f1 != f2


def test_xref_model_round_trips_through_schema(tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    m = run_pipeline(make_host(tmp_path / "host.dxf"), tmp_path, xref_files=[x]).model
    m2, applied = load_model(m.model_dump(mode="json"))
    assert applied == [] and m2.xrefs == m.xrefs
