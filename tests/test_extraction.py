"""Deterministic source-geometry extraction and traceability."""

import math

import ezdxf

from conftest import run_pipeline


def test_inventory_counts_match_construction(results):
    m = results["office_in"].model
    top = [e for e in m.entities if e.parent_id is None and e.space == "model"]
    assert len(top) == 59          # counted from tests/fixtures/builders.make_office
    assert len(m.entities) == 77   # + 18 block-content entities (doors, windows, columns, title block)
    assert all(e.handle for e in top)
    assert all(e.handle is None and e.parent_id for e in m.entities if e.parent_id)


def test_block_children_carry_lineage(results):
    m = results["office_in"].model
    doors = [e for e in m.entities if e.type == "INSERT" and e.attributes.get("block") == "DOOR-36"]
    assert len(doors) == 3
    for d in doors:
        kids = [e for e in m.entities if e.parent_id == d.id]
        assert {k.type for k in kids} == {"LINE", "ARC"}
        assert all(k.block_path == ["DOOR-36"] and k.layer == "A-DOOR" for k in kids)  # layer-0 inheritance


def test_mirrored_block_arc_is_in_wcs(results):
    m = results["office_in"].model
    mirrored = [e for e in m.entities if e.type == "ARC" and e.attributes.get("mirrored_ocs")]
    assert len(mirrored) == 1
    arc = mirrored[0]
    parent = m.entity(arc.parent_id)
    ix, iy = parent.normalized.insert
    # every flattened point lies within the 3 ft swing radius of the insertion point
    assert all(math.dist((ix, iy), p) <= 3.0 + 1e-3 for p in arc.normalized.points)  # Bezier flattening ~0.03%


def test_text_and_mtext_are_read(results):
    texts = {e.source.text for e in results["office_in"].model.entities if e.source and e.source.kind == "text"}
    assert "OFFICE\n101" in texts
    assert "STORAGE 102" in texts
    assert "PROJECT: AUDIT TEST OFFICE" in texts   # block content


def test_dimension_measurement_normalized(results):
    dims = [e for e in results["office_mm"].model.entities if e.type == "DIMENSION"]
    measurements = sorted(round(d.normalized.measurement, 6) for d in dims)
    assert measurements == [60.0, 100.0, 100.0]


def test_layers_inventory(results):
    layers = {l.name: l for l in results["office_in"].model.layers}
    assert layers["A-WALL"].inferred_role == "wall" and layers["A-WALL"].role_confidence == 0.9
    assert layers["A-FURN"].inferred_role is None
    assert layers["A-WALL"].entity_count == 12


def test_unsupported_entities_are_kept_and_reported(tmp_path):
    p = tmp_path / "xline.dxf"
    doc = ezdxf.new("R2018"); doc.header["$INSUNITS"] = 2
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 30), (0, 30)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_xline((0, 0), (1, 1))
    msp.add_ray((0, 0), (1, 0))
    doc.saveas(p)
    r = run_pipeline(p, tmp_path)
    assert r.processing_status == "completed"
    unsupported = [e for e in r.model.entities if not e.supported]
    assert {e.type for e in unsupported} == {"XLINE", "RAY"}
    codes = {i.code for i in r.model.diagnostics.warnings + r.model.diagnostics.review_triggers}
    assert "UNSUPPORTED_ENTITIES" in codes


def test_hidden_layers_excluded_but_reported(tmp_path):
    p = tmp_path / "hidden.dxf"
    doc = ezdxf.new("R2018"); doc.header["$INSUNITS"] = 2
    doc.layers.add("A-WALL"); doc.layers.add("A-WALL-HIDDEN").freeze()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 30), (0, 30)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_line((0, 0), (5000, 5000), dxfattribs={"layer": "A-WALL-HIDDEN"})
    doc.saveas(p)
    r = run_pipeline(p, tmp_path)
    assert r.model.bounds_normalized.width == 40.0          # frozen geometry does not stretch bounds
    assert any(w.code == "HIDDEN_GEOMETRY_EXCLUDED" for w in r.model.diagnostics.warnings)
    assert len(r.model.elements_of("wall")) == 1


def test_deterministic_output(fx, tmp_path):
    a = run_pipeline(fx["office_ft"], tmp_path).model.model_dump(mode="json")
    b = run_pipeline(fx["office_ft"], tmp_path).model.model_dump(mode="json")
    for d in (a, b):
        d.pop("model_id"); d.pop("created_at")
    assert a == b
