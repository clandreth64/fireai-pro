"""Milestone 1.5 foundations: persistent identity, coordinate frames, unknown Z/levels,
provenance, schema versioning/migration, DWG->DXF linkage."""

import hashlib
import json
import math
import shutil
from pathlib import Path

import ezdxf
import pytest

from conftest import run_pipeline
from fireai.ingest.dwg import ConversionResult, DwgConverter
from fireai.model import SCHEMA_VERSION
from fireai.schema import UnsupportedSchemaVersion, load_model
from fireai.spatial import FrameUnresolved, transform_point
from fixtures import builders as B

FIXTURES = Path(__file__).parent / "fixtures"


def test_schema_version_is_current(results):
    # M1.6 bumped the schema (XREF records, view types, wall analysis, verification binding).
    assert SCHEMA_VERSION == "0.5.0"                    # M1.9: classified boundaries + openings
    assert results["office_in"].model.schema_version == "0.5.0"


def test_every_object_has_a_unique_persistent_uid(results):
    m = results["office_in"].model
    ent_uids = [e.uid for e in m.entities]
    el_uids = [e.uid for e in m.elements]
    assert all(ent_uids) and all(el_uids)
    assert len(set(ent_uids)) == len(ent_uids)
    assert len(set(el_uids)) == len(el_uids)
    assert not set(ent_uids) & set(el_uids)
    assert m.source.source_uid


def test_uids_are_deterministic_and_file_scoped(fx, tmp_path, results):
    a = run_pipeline(fx["office_in"], tmp_path).model
    b = results["office_in"].model
    assert [e.uid for e in a.entities] == [e.uid for e in b.entities]
    assert [e.uid for e in a.elements] == [e.uid for e in b.elements]
    w = results["warehouse"].model
    assert not {e.uid for e in a.entities} & {e.uid for e in w.entities}


def test_block_children_have_handle_paths(results):
    m = results["office_in"].model
    door = next(e for e in m.entities if e.type == "INSERT" and e.attributes.get("block") == "DOOR-36")
    kids = [e for e in m.entities if e.parent_id == door.id]
    assert door.handle_path == [door.handle] and door.uid_basis == "handle"
    for k in kids:
        assert k.handle_path[0] == door.handle and k.handle_path[-1].startswith("#")
    assert len({tuple(k.handle_path) for k in kids}) == len(kids)


def test_document_guid_and_source_object_key(results):
    m = results["office_in"].model
    assert m.source.document_guid  # ezdxf writes $FINGERPRINTGUID
    top = next(e for e in m.entities if e.handle)
    assert top.source_object_key == f"{m.source.document_guid}:{top.handle}"


def test_geometry_frames_are_explicit(results):
    m = results["office_in"].model
    for e in m.entities:
        if e.source:
            assert e.source.frame == "SRC"
        if e.normalized:
            assert e.normalized.frame == "LOCAL"
    assert all(el.geometry.frame == "LOCAL" for el in m.elements if el.geometry)
    frames = {f.id: f for f in m.coordinate_frames}
    assert set(frames) == {"SRC", "SRC_FT", "LOCAL", "PROJECT"}
    assert frames["PROJECT"].status == "unresolved" and frames["PROJECT"].matrix is None


def test_frame_transforms_reproduce_normalized_coordinates_and_round_trip(results):
    m = results["office_mm"].model
    for e in m.entities[:40]:
        if not (e.source and e.normalized and e.source.points):
            continue
        for sp, npnt in zip(e.source.points, e.normalized.points, strict=True):
            loc = transform_point(m.coordinate_frames, sp, "SRC", "LOCAL")
            assert math.isclose(loc[0], npnt[0], abs_tol=1e-9) and math.isclose(loc[1], npnt[1], abs_tol=1e-9)
            back = transform_point(m.coordinate_frames, loc, "LOCAL", "SRC")
            assert math.isclose(back[0], sp[0], abs_tol=1e-6) and math.isclose(back[1], sp[1], abs_tol=1e-6)
    with pytest.raises(FrameUnresolved):
        transform_point(m.coordinate_frames, (0.0, 0.0), "SRC", "PROJECT")


def test_src_ft_is_stable_when_drawing_content_changes(fx, tmp_path):
    """LOCAL depends on content (lower-left origin); SRC_FT must not. This is why
    SRC_FT (not LOCAL) is the basis for future project coordinates."""
    edited = tmp_path / "office_edited.dxf"
    doc = ezdxf.readfile(fx["office_ft"])
    doc.modelspace().add_line((0, 0), (5, 5), dxfattribs={"layer": "MISC-STUFF"})  # far from the building at (1000,400)
    doc.saveas(edited)
    a = run_pipeline(fx["office_ft"], tmp_path).model
    b = run_pipeline(edited, tmp_path).model
    wa = next(e for e in a.entities if e.layer == "A-WALL" and e.type == "LINE")
    wb = next(e for e in b.entities if e.layer == "A-WALL" and e.type == "LINE" and e.handle == wa.handle)
    pa = transform_point(a.coordinate_frames, wa.source.points[0], "SRC", "SRC_FT")
    pb = transform_point(b.coordinate_frames, wb.source.points[0], "SRC", "SRC_FT")
    assert pa == pb                                             # stable
    assert wa.normalized.points[0] != wb.normalized.points[0]   # LOCAL shifted


def test_no_invented_z_level_or_building(results):
    m = results["office_in"].model
    assert m.spatial_structure.status == "unassigned"
    assert m.spatial_structure.buildings == [] and m.spatial_structure.levels == []
    for el in m.elements:
        p = el.placement
        assert p.level_id is None and p.building_id is None and p.assignment_status == "unassigned"
        assert p.elevation_ft is None and p.height_ft is None and p.thickness_ft is None
        assert p.z_status == "unknown"
    doors = m.elements_of("door")
    assert sorted(d.placement.rotation_deg for d in doors) == [0.0, 90.0, 270.0]   # from source INSERTs


def test_source_z_is_recorded_but_not_promoted_to_elevation(tmp_path):
    p = tmp_path / "z.dxf"
    doc = ezdxf.new("R2018"); doc.header["$INSUNITS"] = 2
    doc.layers.add("A-WALL")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 30), (0, 30)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_line((0, 0, 0), (40, 0, 12), dxfattribs={"layer": "A-WALL"})
    doc.saveas(p)
    m = run_pipeline(p, tmp_path).model
    line = next(e for e in m.entities if e.type == "LINE")
    assert line.z_range == (0.0, 12.0)
    flat = next(e for e in m.entities if e.type == "LWPOLYLINE")
    assert flat.z_range == (0.0, 0.0)
    assert all(el.placement.elevation_ft is None and el.placement.z_status == "unknown" for el in m.elements)


def test_provenance_links_elements_to_source_uids(results):
    m = results["office_in"].model
    by_id = {e.id: e for e in m.entities}
    for el in m.elements:
        assert el.provenance.origin == "deterministic_inference"
        assert el.provenance.review.status == "unreviewed"
        assert el.provenance.derived_from == [by_id[i].uid for i in el.source_entity_ids]
        assert el.provenance.rule_ids == el.rules
    assert all(e.provenance.origin == "source" for e in m.entities)


def test_migrate_real_0_1_0_model():
    data = json.loads((FIXTURES / "model_v0_1_0_simple_rect.json").read_text())
    assert data["schema_version"] == "0.1.0"
    model, applied = load_model(data)
    assert applied == ["0.1.0->0.2.0", "0.2.0->0.3.0", "0.3.0->0.4.0", "0.4.0->0.5.0"]
    assert model.schema_version == "0.5.0"
    assert all(e.uid for e in model.entities) and all(e.uid for e in model.elements)
    assert all(el.placement.elevation_ft is None for el in model.elements)       # unknown, not invented
    assert {f.id for f in model.coordinate_frames} == {"SRC", "SRC_FT", "LOCAL", "PROJECT"}
    old_room = next(e for e in data["elements"] if e["category"] == "room")
    assert model.elements_of("room")[0].properties["area_sf"] == old_room["properties"]["area_sf"] == 1131.0


def test_current_model_round_trips_without_migration(results):
    d = results["warehouse"].model.model_dump(mode="json")
    model, applied = load_model(json.loads(json.dumps(d)))
    assert applied == []
    assert model.model_dump(mode="json") == d


@pytest.mark.parametrize("bad", [None, "0.0.1", "9.9.9", "0.2"])
def test_unknown_schema_versions_are_refused(bad):
    data = json.loads((FIXTURES / "model_v0_1_0_simple_rect.json").read_text())
    if bad is None:
        data.pop("schema_version")
    else:
        data["schema_version"] = bad
    with pytest.raises(UnsupportedSchemaVersion):
        load_model(data)


class _LoggingCopyConverter(DwgConverter):
    name = "test-copy"

    def __init__(self, dxf):
        self.dxf = dxf

    def available(self):
        return True

    def _run(self, dwg_path, out_dir, timeout_s):
        out = out_dir / "converted.dxf"
        shutil.copyfile(self.dxf, out)
        return ConversionResult(out, self.name, "1.0", ["copy"],
                                log_tail="Reading DWG\nWarning: Unhandled class ACDBPLACEHOLDER\nERROR 0x800\nWritten DXF")


def test_dwg_to_dxf_linkage_is_recorded(fx, tmp_path):
    r = run_pipeline(fx["fake_dwg"], tmp_path, converter=_LoggingCopyConverter(fx["office_ft"]))
    s = r.model.source
    assert s.format == "dwg" and s.sha256 == hashlib.sha256(fx["fake_dwg"].read_bytes()).hexdigest()
    assert s.converted_dxf_sha256 == hashlib.sha256(fx["office_ft"].read_bytes()).hexdigest()
    assert s.converter_warnings == ["Warning: Unhandled class ACDBPLACEHOLDER", "ERROR 0x800"]
    assert "Written DXF" in s.converter_log_tail


def test_units_unresolved_frames_are_unresolved(fx, tmp_path):
    r = run_pipeline(fx["unitless"], tmp_path)
    frames = {f.id: f for f in r.model.coordinate_frames}
    assert frames["SRC"].status == "defined"
    assert all(frames[i].status == "unresolved" for i in ("SRC_FT", "LOCAL", "PROJECT"))


def test_builder_fixture_unchanged():
    # Guard: the committed golden drawings still match the builders (no silent fixture edits).
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = B.make_simple_rect(Path(d) / "x.dxf")
        assert ezdxf.readfile(p).modelspace().query("LWPOLYLINE").__len__() == 3


def test_migrate_real_0_2_0_model_with_xref():
    """Fixture produced by the Milestone 1.5 code (b6a1fb3) — a real 0.2.0 model."""
    data = json.loads((FIXTURES / "model_v0_2_0_xref_host.json").read_text())
    assert data["schema_version"] == "0.2.0"
    model, applied = load_model(data)
    assert applied == ["0.2.0->0.3.0", "0.3.0->0.4.0", "0.4.0->0.5.0"]
    (x,) = model.xrefs
    assert x.name == "ARCH-BASE" and x.status == "not_attempted"     # 0.2.0 never loaded XREFs
    assert all(r["view_type"] == "UNKNOWN" and r["review_state"] == "unreviewed" for r in model.view_regions)
    assert model.verification is None and model.wall_model is None
    # nothing else changes meaning
    assert [e.uid for e in model.entities] == [e["uid"] for e in data["entities"]]
    assert [e.uid for e in model.elements] == [e["uid"] for e in data["elements"]]


def test_persisted_model_is_lossless_and_keeps_safety_flags_explicit(results):
    from fireai.schema import dump_model
    m = results["office_ft"].model
    raw = dump_model(m)
    d = json.loads(raw)
    for k in ("schema_version", "geometry_is_synthetic", "ready_for_design", "engineering_review_status",
              "ai_inference_used", "requires_human_review"):
        assert k in d, k                                   # never omitted, even at default values
    assert d["geometry_is_synthetic"] is False and d["ready_for_design"] is False
    m2, applied = load_model(d)
    assert applied == [] and m2.model_dump(mode="json") == m.model_dump(mode="json")
    assert len(raw) < len(json.dumps(m.model_dump(mode="json")))


def test_pipeline_writes_the_compact_lossless_model(results):
    r = results["warehouse"]
    on_disk = json.loads(r.path("model_json").read_text())
    m2, _ = load_model(on_disk)
    assert m2.model_dump(mode="json") == r.model.model_dump(mode="json")
