"""Drawing Understanding -> Verified Normalized Model -> Engineering boundary (contract draft 1)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from conftest import run_pipeline
from fireai.contract import ContractViolation, build_engineering_input, engineering_input_blockers
from fireai.review.store import ReviewStore
from fixtures import builders as B

ROOT = Path(__file__).resolve().parent.parent
FULL = {c: {"status": "CONFIRMED"} for c in ("units", "drawing_type", "view_regions", "extents", "walls", "rooms")}
FORBIDDEN = ("ezdxf", "fireai.ingest", "fireai.interpret", "fireai.render", "fireai.pipeline", "fireai.jobs",
             "fireai.api")


def _verify(store, m):
    uid = next(r["uid"] for r in m.view_regions if r["significant"])
    store.record_verification(m, "Owner", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}),
                              [uid])


def _human_plan(store, m, path, tmp):
    """A person confirms the (untitled, evidence-poor) region is a floor plan, then reprocesses."""
    r = next(r for r in m.view_regions if r["significant"])
    if r["view_type"] != "FLOOR_PLAN":
        store.add_correction(m, "view_type", {"region_uid": r["uid"], "view_type": "FLOOR_PLAN"}, "Owner")
        m = run_pipeline(path, tmp, review_store=store).model
    return m


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


@pytest.mark.parametrize("pkg", ["fireai/contract", "fireai/engineering"])
def test_contract_and_future_engineering_code_cannot_reach_cad_internals(pkg):
    """Engineering may depend only on the contract. fireai/engineering does not exist yet; this
    guard applies automatically when it is created."""
    for f in (ROOT / pkg).rglob("*.py") if (ROOT / pkg).is_dir() else []:
        bad = [m for m in _imports(f) if any(m == x or m.startswith(x + ".") for x in FORBIDDEN)]
        assert not bad, f"{f.relative_to(ROOT)} imports {bad}"
        if pkg == "fireai/engineering":
            assert not [m for m in _imports(f) if m.startswith("fireai.model")], \
                f"{f} must consume fireai.contract.EngineeringInput, not the raw BuildingModel"


def test_unverified_model_is_blocked_by_the_unchanged_gate(tmp_path):
    store = ReviewStore(tmp_path / "r")
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf"), tmp_path, review_store=store).model
    blockers = engineering_input_blockers(m, store)
    assert any("REVIEW_REQUIRED" in b or "UNREVIEWED" in b for b in blockers)
    with pytest.raises(ContractViolation):
        build_engineering_input(m, store)


def test_verified_model_yields_format_independent_input(tmp_path):
    store = ReviewStore(tmp_path / "r")
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf"), tmp_path, review_store=store).model
    _verify(store, m)
    ei = build_engineering_input(m, store)
    assert ei.contract_version == "engineering_input/1-draft" and ei.frame == "LOCAL" and ei.units == "ft"
    assert ei.verification_fingerprint == m.verification.fingerprint and ei.z_status == "unknown"
    assert "ceiling_height_and_ceiling_geometry" in ei.not_provided
    assert len(ei.spaces) == 2 and {s.label for s in ei.spaces} == {"OFFICE", "SALES FLOOR"}
    assert len(ei.walls_analysis) == 6 and all(w.derived for w in ei.walls_analysis)
    # traceability by uid; no CAD internals anywhere in the payload
    uids = {e.uid for e in m.entities} | {e.uid for e in m.elements}
    assert all(u in uids for s in ei.spaces for u in s.derived_from)
    raw = json.loads(ei.model_dump_json())
    text = json.dumps(raw)
    for internal in ('"handle"', '"layer"', '"handle_path"', '"entities"', '"block_path"', "LWPOLYLINE"):
        assert internal not in text, internal


def test_merged_room_blocks_engineering(tmp_path):
    store = ReviewStore(tmp_path / "r")
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=False)
    m = _human_plan(store, run_pipeline(p, tmp_path, review_store=store).model, p, tmp_path)
    _verify(store, m)
    assert any("merged-room" in b for b in engineering_input_blockers(m, store))


def test_human_room_boundaries_resolve_the_merged_room(tmp_path):
    store = ReviewStore(tmp_path / "r")
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=False)
    m = _human_plan(store, run_pipeline(p, tmp_path, review_store=store).model, p, tmp_path)
    (merged,) = m.elements_of("room")
    o = m.transform.origin
    left = [[0.5 + o[0], 0.5 + o[1]], [19.75 + o[0], 0.5 + o[1]], [19.75 + o[0], 19.5 + o[1]], [0.5 + o[0], 19.5 + o[1]]]
    right = [[20.25 + o[0], 0.5 + o[1]], [39.5 + o[0], 0.5 + o[1]], [39.5 + o[0], 19.5 + o[1]], [20.25 + o[0], 19.5 + o[1]]]
    store.add_correction(m, "room_boundary", {"polygon_src": left, "label": "OFFICE",
                                              "replaces_element_uid": merged.uid}, "Owner")
    store.add_correction(m, "room_boundary", {"polygon_src": right, "label": "SALES FLOOR"}, "Owner")
    m2 = run_pipeline(p, tmp_path, review_store=store).model
    _verify(store, m2)
    ei = build_engineering_input(m2, store)
    assert sorted((s.label, s.origin) for s in ei.spaces) == [("OFFICE", "human"), ("SALES FLOOR", "human")]
    assert len(ei.human_corrections_applied) == 3          # view type + two room boundaries


def test_invalid_transform_blocks(tmp_path):
    store = ReviewStore(tmp_path / "r")
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf"), tmp_path, review_store=store).model
    _verify(store, m)
    m.transform.scale = float("nan")
    assert "coordinate transform SRC -> LOCAL is missing or invalid" in engineering_input_blockers(m, store)


def test_missing_xref_still_blocks_at_the_contract(tmp_path):
    from test_xref import make_host
    store = ReviewStore(tmp_path / "r")
    m = run_pipeline(make_host(tmp_path / "h.dxf"), tmp_path, review_store=store).model
    _verify(store, m)
    assert any("external references not loaded" in b for b in engineering_input_blockers(m, store))
