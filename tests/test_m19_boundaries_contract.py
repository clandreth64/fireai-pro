"""Milestone 1.9 (pre-M2 contract checkpoint): classified region boundaries, plan openings,
engineering_input/3, content fingerprints and one engine-version story.

Generated drawings only — no real-drawing names, coordinates, layers or counts."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Polygon

from conftest import run_pipeline
from fireai import pipeline as P
from fireai.contract import (ContractVersionError, build_engineering_input, engineering_input_blockers,
                             parse_engineering_input, read_legacy_engineering_input)
from fireai.contract.debug import cyclic_kinds, perimeter_rows
from fireai.review.content import content_fingerprint
from fireai.review.store import ReviewStore, verification_state
from fireai.schema import dump_model, load_model
from fixtures import builders as B

FIXTURES = Path(__file__).parent / "fixtures"
FULL = {c: {"status": "CONFIRMED"} for c in ("units", "drawing_type", "view_regions", "extents", "walls", "rooms")}
CAD_WORDS = ('"handle"', '"layer"', '"handle_path"', '"entities"', '"block_path"', "LWPOLYLINE", "INSERT",
             "DOOR-36", "WINDOW-48", "A-WALL", "A-DOOR", "A-GLAZ")


def _plan(store, m, path, tmp):
    r = next(r for r in m.view_regions if r["significant"])
    if r["view_type"] != "FLOOR_PLAN":
        store.add_correction(m, "view_type", {"region_uid": r["uid"], "view_type": "FLOOR_PLAN"}, "Owner")
        m = run_pipeline(path, tmp, review_store=store).model
    return m


def _verify(store, m):
    uid = next(r["uid"] for r in m.view_regions if r["significant"])
    store.record_verification(m, "Owner", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}), [uid])


def _segs(room):
    return [s for ring in room.boundary.rings for s in ring.segments]


def _rooms(m):
    return {r.label: r for r in m.elements_of("room")}


def _no_doors(path: Path) -> Path:
    doc = ezdxf.readfile(path)
    for e in list(doc.modelspace().query("INSERT")):
        if e.dxf.name.startswith("DOOR"):
            doc.modelspace().delete_entity(e)
    doc.saveas(path)
    return path


def _area_only(path: Path) -> Path:
    """A named area polygon with NO walls, doors or windows around it."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    for n in ("A-AREA", "A-AREA-IDEN"):
        doc.layers.add(n)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 15), (0, 15)], close=True, dxfattribs={"layer": "A-AREA"})
    msp.add_text("STORAGE", height=1, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement((8, 7))
    doc.saveas(path)
    return path


# ── 1. classified boundaries ─────────────────────────────────────────────────

def test_boundary_reproduces_the_region_polygon_exactly(results):
    for room in results["office_ft"].model.elements_of("room"):
        segs = _segs(room)
        assert room.boundary.rings[0].orientation == "ccw" and room.boundary.complete
        for a, b in zip(segs, segs[1:] + segs[:1], strict=True):
            assert math.dist(a.end, b.start) < 1e-9                                      # closed, ordered, gap-free
        assert sum(s.length_ft for s in segs) == pytest.approx(Polygon(room.geometry.points).length, abs=1e-6)
        assert [s.index for s in segs] == list(range(len(segs)))
        assert room.boundary.plane_z_status == "unknown" and room.boundary.representation == "plan_projection"


def test_doorways_windows_and_walls_are_distinguished(results):
    m = results["office_ft"].model
    office = _rooms(m)["OFFICE 101"]
    kinds = [s.kind for s in _segs(office)]
    assert kinds.count("door_opening") == 2 and kinds.count("window") == 1 and "unknown" not in kinds
    for s in _segs(office):
        assert s.encloses is {"wall": True, "window": True, "door_opening": False}[s.kind]
        if s.kind == "door_opening":
            assert s.length_ft == pytest.approx(3.0, abs=1e-6) and s.opening_uid and s.fill_element_uid
            assert s.fill_element_uid in {d.uid for d in m.elements_of("door")}
        if s.kind == "wall":
            assert s.derived_from and s.opening_uid is None                               # backed by wall linework
    assert cyclic_kinds_model(office) == cyclic_kinds_model(_rooms(results["office_in"].model)["OFFICE 101"])


def cyclic_kinds_model(room):
    seq = [s.kind for s in _segs(room)]
    col = [k for i, k in enumerate(seq) if i == 0 or k != seq[i - 1]]
    col = col[:-1] if len(col) > 1 and col[0] == col[-1] else col
    return min(col[i:] + col[:i] for i in range(len(col)))


def test_polygonized_rooms_use_the_door_closure_as_the_doorway(tmp_path):
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf", with_door=True), tmp_path).model
    for room in m.elements_of("room"):
        (door,) = [s for s in _segs(room) if s.kind == "door_opening"]
        assert door.rules == ["B-DOOR-CLOSURE"] and door.length_ft == pytest.approx(3.0)
        assert {tuple(round(c, 6) for c in p) for p in (door.start, door.end)} <= {
            tuple(map(float, p)) for p in room.properties["door_closures"][0].values() if isinstance(p, list)}
    (op,) = m.elements_of("opening")
    assert op.subtype == "door" and op.properties["passable"] is True and op.properties["depth_ft"] == pytest.approx(0.5)
    assert sorted(op.properties["connects_region_uids"]) == sorted(r.uid for r in m.elements_of("room"))


def test_a_doorless_gap_is_an_open_opening_and_no_door_is_invented(tmp_path):
    p = _no_doors(B.make_office(tmp_path / "o.dxf", "ft"))
    m = run_pipeline(p, tmp_path).model
    assert m.elements_of("door") == []
    kinds = {s.kind for r in m.elements_of("room") for s in _segs(r)}
    assert "door_opening" not in kinds and "open_opening" in kinds
    opens = [o for o in m.elements_of("opening") if o.subtype == "open"]
    assert opens and all(o.properties["fill_element_uid"] is None and o.properties["passable"] for o in opens)
    assert all(o.requires_verification for o in opens)


def test_boundary_without_evidence_stays_unknown(tmp_path):
    m = run_pipeline(_area_only(tmp_path / "a.dxf"), tmp_path).model
    (room,) = m.elements_of("room")
    assert room.boundary.complete is False and {s.kind for s in _segs(room)} == {"unknown"}
    assert all(s.encloses is None and s.requires_verification and s.confidence == 0.0 for s in _segs(room))
    trig = {t.code: t for t in m.diagnostics.review_triggers}["REGION_BOUNDARY_UNCLASSIFIED"]
    assert room.id in trig.element_ids and "not assumed to be walls" in trig.message
    assert m.elements_of("opening") == []


def test_human_room_boundaries_are_classified_too(tmp_path):
    store = ReviewStore(tmp_path / "r")
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=False)
    m = _plan(store, run_pipeline(p, tmp_path, review_store=store).model, p, tmp_path)
    merged = m.elements_of("room")[0]
    for label, poly in (("OFFICE", [[0.5, 0.5], [19.75, 0.5], [19.75, 19.5], [0.5, 19.5]]),
                        ("SALES FLOOR", [[20.25, 0.5], [39.5, 0.5], [39.5, 19.5], [20.25, 19.5]])):
        store.add_correction(m, "room_boundary", {"polygon_src": poly, "label": label,
                                                  "replaces_element_uid": merged.uid}, "Owner")
    m = run_pipeline(p, tmp_path, review_store=store).model
    humans = [r for r in m.elements_of("room") if r.provenance.origin == "human"]
    assert len(humans) == 2 and all(r.boundary is not None for r in humans)
    for r in humans:
        kinds = [s.kind for s in _segs(r)]
        assert "open_opening" in kinds and "unknown" not in kinds                         # the doorless gap is an opening
    (op,) = [o for o in m.elements_of("opening") if o.subtype == "open"]
    assert sorted(op.properties["connects_region_uids"]) == sorted(r.uid for r in humans)


def test_one_opening_per_physical_opening(results):
    m = results["office_ft"].model
    doors = m.elements_of("door")
    door_openings = [o for o in m.elements_of("opening") if o.subtype == "door"]
    assert len(door_openings) == len(doors) == 3
    assert len({o.properties["fill_element_uid"] for o in door_openings}) == 3
    for o in door_openings:                                  # a door between two regions: both boundaries, one opening
        assert len(o.properties["connects_region_uids"]) == 2 and len(o.properties["boundary_segment_uids"]) == 2
    segs = {s.uid: s for r in m.elements_of("room") for s in _segs(r)}
    for o in m.elements_of("opening"):
        assert all(segs[u].opening_uid == o.uid for u in o.properties["boundary_segment_uids"])


# ── 2. engineering_input/3 ───────────────────────────────────────────────────

def _office_package(tmp_path, unit="ft"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = ReviewStore(tmp_path / "r")
    p = B.make_office(tmp_path / f"o_{unit}.dxf", unit)
    m = _plan(store, run_pipeline(p, tmp_path, review_store=store).model, p, tmp_path)
    _verify(store, m)
    return m, build_engineering_input(m, store)


def test_contract_v3_tells_doorways_from_walls_without_cad(tmp_path):
    m, ei = _office_package(tmp_path)
    assert ei.contract_version == "engineering_input/3" and ei.content_fingerprint == m.verification.content_fingerprint
    office = next(s for s in ei.spaces if s.label == "OFFICE 101")
    ops = {o.uid: o for o in ei.openings}
    rows = perimeter_rows(office, ei)
    assert [r["index"] for r in rows] == list(range(len(rows)))
    doorway = [r for r in rows if r["kind"] == "door_opening"]
    assert len(doorway) == 2 and all(r["encloses"] is False and r["opening"]["kind"] == "door" for r in doorway)
    walls = [r for r in rows if r["kind"] == "wall"]
    assert walls and all(r["encloses"] is True and r["opening"] is None for r in walls)
    for s in office.boundary.rings[0].segments:
        if s.opening_uid:
            assert s.opening_uid in ops and office.uid in ops[s.opening_uid].space_uids
    assert {o.kind for o in ei.openings} == {"door", "window"}
    assert all(o.vertical_extent.status == "unknown" for o in ei.openings)
    text = ei.model_dump_json()
    for w in CAD_WORDS:
        assert w not in text, w


def test_contract_v3_is_unit_independent(tmp_path):
    _m, ft = _office_package(tmp_path / "ft", "ft")
    _m, mm = _office_package(tmp_path / "mm", "mm")
    for a in ft.spaces:
        b = next(s for s in mm.spaces if s.label == a.label)
        assert cyclic_kinds(a) == cyclic_kinds(b)
        for k, v in a.boundary.length_by_kind_ft.items():
            assert b.boundary.length_by_kind_ft[k] == pytest.approx(v, abs=1e-6)
    assert sorted((o.kind, round(o.width_ft, 6)) for o in ft.openings) == \
           sorted((o.kind, round(o.width_ft, 6)) for o in mm.openings)


def test_contract_versions_are_explicit(tmp_path):
    _m, ei = _office_package(tmp_path)
    data = json.loads(ei.model_dump_json())
    assert parse_engineering_input(data) == ei
    v2 = copy.deepcopy(data)
    v2["contract_version"] = "engineering_input/2-draft"
    for k in ("openings", "content_fingerprint"):
        v2.pop(k)
    for s in v2["spaces"]:
        s.pop("boundary")
    with pytest.raises(ContractVersionError, match="regenerate"):
        parse_engineering_input(v2)                           # never reinterpreted as version 3
    legacy = read_legacy_engineering_input(v2)                # still readable for audit
    assert legacy.contract_version == "engineering_input/2-draft" and not hasattr(legacy, "openings")
    with pytest.raises(ContractVersionError):
        read_legacy_engineering_input(data)
    with pytest.raises(ContractVersionError):
        parse_engineering_input({**data, "contract_version": "engineering_input/4"})


def test_a_migrated_0_4_0_model_must_be_reprocessed_before_engineering(tmp_path):
    """Fixture produced by the M1.8 code (a8450a2, schema 0.4.0)."""
    data = json.loads((FIXTURES / "model_v0_4_0_two_rooms.json").read_text())
    assert data["schema_version"] == "0.4.0"
    m, applied = load_model(data)
    assert applied == ["0.4.0->0.5.0"] and m.schema_version == "0.5.0"
    assert all(r.boundary is None for r in m.elements_of("room")) and m.elements_of("opening") == []
    assert [e.provenance.engine_version for e in m.elements] == [e["provenance"]["engine_version"] for e in data["elements"]]
    assert m.verification.content_fingerprint is None and m.verification.fingerprint == data["verification"]["fingerprint"]
    store = ReviewStore(tmp_path / "r")
    r = next(r for r in m.view_regions if r["significant"])
    r["view_type"] = "FLOOR_PLAN"             # isolate the new blockers from the view-type blocker
    _verify(store, m)
    blockers = engineering_input_blockers(m, store)
    assert any("no classified boundary" in b for b in blockers)
    assert any("content fingerprint" in b for b in blockers)


# ── 3. content fingerprint (review validity) ─────────────────────────────────

def test_A_identical_reprocessing_keeps_content_and_verification(tmp_path):
    store = ReviewStore(tmp_path / "r")
    p = B.make_walls_only(tmp_path / "w.dxf")
    m1 = run_pipeline(p, tmp_path, review_store=store, name="first name.dxf").model
    _verify(store, m1)
    m2 = run_pipeline(p, tmp_path, review_store=store, name="another name.dxf").model
    assert m1.model_id != m2.model_id and m1.created_at != m2.created_at
    assert content_fingerprint(m1) == content_fingerprint(m2) == m2.verification.content_fingerprint
    assert verification_state(m2, store)["status"] == "HUMAN_VERIFIED"


def _fp(m):
    return content_fingerprint(json.loads(dump_model(m)))


@pytest.fixture()
def door_model(tmp_path):
    return run_pipeline(B.make_walls_only(tmp_path / "w.dxf"), tmp_path).model


def test_run_metadata_does_not_change_content(door_model):
    base = _fp(door_model)
    m = door_model.model_copy(deep=True)
    m.model_id, m.created_at = "0" * 32, "2040-01-01T00:00:00+00:00"
    m.source.filename, m.source.converter = "elsewhere.dxf", {"name": "x", "version": "y", "command": "z"}
    m.source.converter_log_tail, m.source.converted_dxf_sha256 = "log", "f" * 64
    m.source.document_guid, m.source.version_guid = "{00000000-0000-0000-0000-000000000000}", "{1}"
    for el in m.elements:
        el.provenance.engine_version = "some-other-label"
    assert _fp(m) == base


@pytest.mark.parametrize("change", [
    "B_geometry", "C_classification", "C_view_type", "D_semantic_space", "E_opening", "E_segment_kind",
    "F_source", "F_xref", "corrections", "units"])
def test_meaningful_changes_change_content(door_model, change):
    base = _fp(door_model)
    m = door_model.model_copy(deep=True)
    room = m.elements_of("room")[0]
    if change == "B_geometry":
        room.geometry.points[0] = (room.geometry.points[0][0] + 0.01, room.geometry.points[0][1])
    elif change == "C_classification":
        room.subtype = "suspected_merged_region"
    elif change == "C_view_type":
        m.view_regions[0]["view_type"] = "SECTION"
    elif change == "D_semantic_space":
        m.elements_of("space")[0].label = "RENAMED"
    elif change == "E_opening":
        m.elements_of("opening")[0].properties["width_ft"] += 0.5
    elif change == "E_segment_kind":
        next(s for s in _segs(room) if s.kind == "door_opening").kind = "wall"
    elif change == "F_source":
        m.source.sha256 = "0" * 64
    elif change == "F_xref":
        from fireai.model import XrefRecord
        m.xrefs.append(XrefRecord(name="BASE", status="resolved", sha256="1" * 64))
    elif change == "corrections":
        m.human_corrections_applied.append({"correction_id": "C1", "kind": "element_reject", "status": "applied"})
    elif change == "units":
        m.units.scale_to_normalized = 1.0 / 12
    assert _fp(m) != base


def test_engine_only_change_keeps_content_but_the_gate_stays_strict(tmp_path, monkeypatch):
    """Content decides review currency; the ENGINEERING gate additionally binds the engine version."""
    store = ReviewStore(tmp_path / "r")
    p = B.make_walls_only(tmp_path / "w.dxf")
    m1 = run_pipeline(p, tmp_path, review_store=store).model
    _verify(store, m1)
    monkeypatch.setattr(P, "ENGINE_VERSION", P.ENGINE_VERSION + "-next")
    m2 = run_pipeline(p, tmp_path, review_store=store).model
    assert content_fingerprint(m2) == content_fingerprint(m1)
    st = verification_state(m2, store)
    assert st["status"] == "INVALIDATED" and any("engine" in r for r in st["reasons"])


def test_content_change_under_the_same_engine_invalidates_verification(tmp_path, monkeypatch):
    store = ReviewStore(tmp_path / "r")
    p = B.make_walls_only(tmp_path / "w.dxf")
    m1 = run_pipeline(p, tmp_path, review_store=store).model
    _verify(store, m1)
    import fireai.interpret.boundaries as BD
    monkeypatch.setitem(BD.CONF, "B-WALL-FACE", 0.5)           # same engine label, different interpretation
    m2 = run_pipeline(p, tmp_path, review_store=store).model
    st = verification_state(m2, store)
    assert st["status"] == "INVALIDATED" and any("content" in r for r in st["reasons"])


# ── 4. one engine-version story ──────────────────────────────────────────────

def test_every_derived_object_carries_the_model_engine_version(results):
    m = results["office_ft"].model
    ev = m.verification.engine_version
    assert ev == P.ENGINE_VERSION and "+interp." in ev
    derived = [e for e in m.elements if e.provenance.origin == "deterministic_inference"]
    assert derived and {e.provenance.engine_version for e in derived} == {ev}
    assert {r.boundary.engine_version for r in m.elements_of("room")} == {ev}
    assert m.wall_model["engine_version"] == ev


def test_engine_version_has_a_single_source(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ENGINE_VERSION", "9.9.9+interp.test")
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf"), tmp_path).model
    assert m.verification.engine_version == "9.9.9+interp.test"
    assert {e.provenance.engine_version for e in m.elements if e.provenance.origin != "human"} == {"9.9.9+interp.test"}


# ── 5. 3D / BIM forward compatibility (placeholders stay UNKNOWN) ────────────

def test_boundaries_and_openings_leave_z_unknown(results):
    m = results["office_ft"].model
    for r in m.elements_of("room"):
        assert r.boundary.plane_z_status == "unknown"
        assert all(s.vertical_extent.status == "unknown" and s.vertical_extent.bottom_ft is None for s in _segs(r))
    for o in m.elements_of("opening"):
        assert o.properties["vertical_extent"]["status"] == "unknown" and o.placement.z_status == "unknown"
