"""Human corrections, verification gate and invalidation (Milestone 1.6)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import fireai.pipeline as P
from conftest import make_settings, run_pipeline
from fireai.review.gate import ModelNotVerified, engineering_readiness, require_verified_model
from fireai.review.store import ReviewError, ReviewStore, verification_state
from fixtures import builders as B
from test_views import make_plan_and_section
from test_xref import make_host, make_xref_file

FULL = {c: {"status": "CONFIRMED"} for c in ("units", "drawing_type", "view_regions", "extents", "walls", "rooms")}


def _run(path, tmp, store, **kw):
    return run_pipeline(path, tmp, review_store=store, **kw)


@pytest.fixture()
def store(tmp_path):
    return ReviewStore(tmp_path / "reviews")


def _plan_uid(m):
    return next(r["uid"] for r in m.view_regions if r["view_type"] == "FLOOR_PLAN")


def _verify(store, m, **kw):
    args = dict(reviewer="J. Reviewer", decision="verify", checklist=FULL,
                acknowledged_triggers=sorted({t.code for t in m.diagnostics.review_triggers}),
                selected_region_uids=[_plan_uid(m)])
    args.update(kw)
    return store.record_verification(m, **args)


def test_unreviewed_model_cannot_be_used_for_engineering(tmp_path, store):
    m = _run(B.make_walls_only(tmp_path / "w.dxf"), tmp_path, store).model
    assert m.verification.status in ("REVIEW_REQUIRED", "UNREVIEWED")
    with pytest.raises(ModelNotVerified) as ei:
        require_verified_model(m, store)
    assert any("REVIEW_REQUIRED" in b or "UNREVIEWED" in b for b in ei.value.readiness["blockers"])


def test_verification_requires_full_checklist_triggers_and_scope(tmp_path, store):
    m = _run(B.make_walls_only(tmp_path / "w.dxf"), tmp_path, store).model
    with pytest.raises(ReviewError, match="required categories"):
        _verify(store, m, checklist={"units": {"status": "CONFIRMED"}})
    with pytest.raises(ReviewError, match="not acknowledged"):
        _verify(store, m, acknowledged_triggers=[])
    with pytest.raises(ReviewError, match="select the drawing region"):
        _verify(store, m, selected_region_uids=[])
    with pytest.raises(ReviewError, match="CORRECTED item needs"):
        _verify(store, m, checklist={**FULL, "rooms": {"status": "CORRECTED"}})
    with pytest.raises(ReviewError, match="reviewer"):
        _verify(store, m, reviewer=" ")
    assert store.verification(m.source.sha256) is None


def test_verified_model_passes_gate_and_survives_reprocessing(tmp_path, store):
    p = B.make_walls_only(tmp_path / "w.dxf")
    m = _run(p, tmp_path, store).model
    _verify(store, m)
    assert verification_state(m, store)["status"] == "HUMAN_VERIFIED"
    assert require_verified_model(m, store)["ready"]
    m2 = _run(p, tmp_path, store).model                 # same source, same engine: still verified
    assert m2.verification.status == "HUMAN_VERIFIED"
    assert m2.verification.fingerprint == m.verification.fingerprint


def test_source_change_invalidates(tmp_path, store):
    import ezdxf
    p = B.make_walls_only(tmp_path / "w.dxf")
    m = _run(p, tmp_path, store).model
    _verify(store, m)
    doc = ezdxf.readfile(p)                               # revise the drawing (same document GUID)
    doc.modelspace().add_line((0, 0), (0, -5), dxfattribs={"layer": "WALLS"})
    doc.saveas(p)
    m2 = _run(p, tmp_path, store).model
    assert m2.source.document_guid == m.source.document_guid and m2.source.sha256 != m.source.sha256
    assert m2.verification.status == "INVALIDATED"
    assert "source drawing changed" in m2.verification.status_reasons[0]
    assert not engineering_readiness(m2, store)["ready"]


def test_xref_change_invalidates(tmp_path, store):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    host = make_host(tmp_path / "host.dxf")
    m = _run(host, tmp_path, store, xref_files=[x]).model
    uid = m.view_regions[0]["uid"]
    store.record_verification(m, "J", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}), [uid])
    make_xref_file(x, "ft", length=12.0)
    m2 = _run(host, tmp_path, store, xref_files=[x]).model
    assert m2.verification.status == "INVALIDATED"
    assert "XREF set or content changed" in m2.verification.status_reasons


def test_engine_change_invalidates(tmp_path, store, monkeypatch):
    p = B.make_walls_only(tmp_path / "w.dxf")
    m = _run(p, tmp_path, store).model
    _verify(store, m)
    monkeypatch.setattr(P, "ENGINE_VERSION", P.ENGINE_VERSION + "-next")
    m2 = _run(p, tmp_path, store).model
    assert m2.verification.status == "INVALIDATED"
    assert any("interpretation engine changed" in r for r in m2.verification.status_reasons)


def test_missing_xref_blocks_engineering_even_if_verified(tmp_path, store):
    m = _run(make_host(tmp_path / "host.dxf"), tmp_path, store).model
    uid = m.view_regions[0]["uid"]
    store.record_verification(m, "J", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}), [uid])
    r = engineering_readiness(m, store)
    assert r["verification"]["status"] == "HUMAN_VERIFIED" and not r["ready"]
    assert any("external references not loaded" in b for b in r["blockers"])


def test_view_type_correction_persists_and_changes_room_logic(tmp_path, store):
    p = make_plan_and_section(tmp_path / "ps.dxf")
    m = _run(p, tmp_path, store).model
    sec = next(r for r in m.view_regions if r["view_type"] == "SECTION")
    assert sec["room_logic"] == "skipped" and len(m.elements_of("room")) == 2
    store.add_correction(m, "view_type", {"region_uid": sec["uid"], "view_type": "FLOOR_PLAN"}, "J. Reviewer",
                         "it is actually the second floor")
    m2 = _run(p, tmp_path, store).model
    r2 = next(r for r in m2.view_regions if r["uid"] == sec["uid"])
    assert r2["view_type"] == "FLOOR_PLAN" and r2["view_type_source"] == "human"
    assert r2["machine_view_type"] == "SECTION"            # machine interpretation preserved
    assert r2["room_logic"] == "applied" and len(m2.elements_of("room")) == 4
    (a,) = m2.human_corrections_applied
    assert a["status"] == "applied" and a["kind"] == "view_type"
    assert m2.verification.corrections_digest is not None


def test_room_boundary_correction_replaces_but_keeps_machine_room(tmp_path, store):
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=False)
    m = _run(p, tmp_path, store).model
    (merged,) = m.elements_of("room")
    t = m.transform
    # left room interior in SRC coordinates (drawing is in feet, origin at 0,0)
    poly_src = [[0.5 + t.origin[0], 0.5 + t.origin[1]], [19.75 + t.origin[0], 0.5 + t.origin[1]],
                [19.75 + t.origin[0], 19.5 + t.origin[1]], [0.5 + t.origin[0], 19.5 + t.origin[1]]]
    store.add_correction(m, "room_boundary", {"polygon_src": poly_src, "label": "OFFICE",
                                              "replaces_element_uid": merged.uid}, "J. Reviewer")
    for _ in range(2):                                    # persists across reprocessing
        m2 = _run(p, tmp_path, store).model
        rooms = m2.elements_of("room")
        human = [r for r in rooms if r.provenance.origin == "human"]
        machine = [r for r in rooms if r.provenance.origin == "deterministic_inference"]
        assert len(human) == 1 and human[0].label == "OFFICE"
        assert human[0].properties["area_sf"] == pytest.approx(19.25 * 19)
        assert len(machine) == 1 and machine[0].uid == merged.uid
        assert machine[0].provenance.review.status == "rejected"


def test_stale_correction_is_reported_not_dropped(tmp_path, store):
    p = B.make_walls_only(tmp_path / "w.dxf")
    m = _run(p, tmp_path, store).model
    wall = m.elements_of("wall")[0]
    store.add_correction(m, "element_reject", {"element_uid": wall.uid}, "J")
    # simulate the element disappearing from a later interpretation
    items = store.corrections(m.source.sha256)
    items[0]["data"]["element_uid"] = "00000000-0000-0000-0000-000000000000"
    store._write(m.source.sha256, "corrections.json", items)
    m2 = _run(p, tmp_path, store).model
    assert "HUMAN_CORRECTIONS_NOT_APPLIED" in {t.code for t in m2.diagnostics.review_triggers}


def test_pending_correction_blocks_verification(tmp_path, store):
    p = B.make_walls_only(tmp_path / "w.dxf")
    m = _run(p, tmp_path, store).model
    store.add_correction(m, "element_confirm", {"element_uid": m.elements_of("wall")[0].uid}, "J")
    with pytest.raises(ReviewError, match="reprocess"):
        _verify(store, m)
    m2 = _run(p, tmp_path, store).model
    _verify(store, m2)
    assert verification_state(m2, store)["status"] == "HUMAN_VERIFIED"
    # verification of the corrected model does not carry over to the uncorrected one
    assert verification_state(m, store)["status"] == "INVALIDATED"


def test_invalid_corrections_rejected(tmp_path, store):
    m = _run(B.make_walls_only(tmp_path / "w.dxf"), tmp_path, store).model
    with pytest.raises(ReviewError):
        store.add_correction(m, "view_type", {"region_uid": "nope", "view_type": "SECTION"}, "J")
    with pytest.raises(ReviewError):
        store.add_correction(m, "view_type", {"region_uid": m.view_regions[0]["uid"], "view_type": "PLAN"}, "J")
    with pytest.raises(ReviewError):
        store.add_correction(m, "room_boundary", {"polygon_src": [[0, 0], [1, 1]]}, "J")
    with pytest.raises(ReviewError):
        store.add_correction(m, "delete_everything", {}, "J")
    with pytest.raises(ReviewError):
        store._dir("../../etc")


def test_review_api_flow(tmp_path):
    from fireai.api.app import create_app
    app = create_app(make_settings(tmp_path / "data"))
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=False)
    with TestClient(app) as c:
        jid = c.post("/api/v2/drawings", files={"file": (p.name, p.read_bytes(), "application/octet-stream")}).json()["job_id"]
        v = c.get(f"/api/v2/drawings/{jid}/verification").json()
        assert v["readiness"]["ready"] is False
        region = next(r for r in v["regions"] if r["significant"])
        model = c.get(f"/api/v2/drawings/{jid}/deliverables/model_json").json()
        merged = next(e for e in model["elements"] if e["category"] == "room")
        r = c.post(f"/api/v2/drawings/{jid}/corrections",
                   json={"kind": "element_reject", "data": {"element_uid": merged["uid"]}, "reviewer": "J"})
        assert r.status_code == 201
        bad = c.post(f"/api/v2/drawings/{jid}/verification",
                     json={"reviewer": "J", "decision": "verify", "checklist": FULL,
                           "acknowledged_triggers": v["review_trigger_codes"],
                           "selected_region_uids": [region["uid"]]})
        assert bad.status_code == 422 and "reprocess" in bad.json()["detail"]["message"]
        j2 = c.post(f"/api/v2/drawings/{jid}/reprocess").json()["job_id"]
        v2 = c.get(f"/api/v2/drawings/{j2}/verification").json()
        assert v2["human_corrections_applied"][0]["status"] == "applied"
        ok = c.post(f"/api/v2/drawings/{j2}/verification",
                    json={"reviewer": "J", "decision": "verify", "checklist": FULL,
                          "acknowledged_triggers": v2["review_trigger_codes"],
                          "selected_region_uids": [region["uid"]]})
        assert ok.status_code == 200 and ok.json()["recorded"] == "HUMAN_VERIFIED"
        assert ok.json()["readiness"]["verification"]["status"] == "HUMAN_VERIFIED"
