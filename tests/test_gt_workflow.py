"""Human ground-truth workflow (Milestones 1.6 / 1.6 checkpoint)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "real_drawings"))
sys.path.insert(0, str(ROOT / "scripts"))
import fireai_values as FV  # noqa: E402
import gt as G  # noqa: E402
from gt_review_server import create_review_app  # noqa: E402
from gt_review_summary import build_summary, to_markdown  # noqa: E402


def test_all_committed_records_are_pending_claude_drafts():
    ids = G.list_ids()
    assert len(ids) == 11
    for gid in ids:
        rec = G.load_record(gid)
        assert rec["schema"] == "ground_truth/2"
        assert rec["review_status"] == G.PENDING
        assert rec["claude_draft"]["author"].startswith("Claude")
        assert "independent of FireAI output" in rec["claude_draft"]["method"]
        assert "human_review" not in rec          # human truth is never stored in the draft record
        assert rec["source_sha256s"] == G.corpus_entry(gid)["sha256"]
        mapped = {f for fs in rec["claude_draft"]["by_category"].values() for f in fs}
        assert mapped == set(rec["claude_draft"]["fields"])
        assert set(rec["claude_draft"]["by_category"]) <= set(G.CATEGORIES)


def test_committed_human_reviews_if_any_are_valid():
    d = G.PUBLIC_REVIEWS
    for p in (d.glob("*.json") if d.is_dir() else []):
        rv = json.loads(p.read_text())
        rec = G.load_record(rv["id"])
        assert G.validate_review(rv, rec, rec["source_sha256s"]) == []


@pytest.fixture()
def env(tmp_path):
    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    for gid in ("REAL_001", "REAL_004"):
        (gt_dir / f"{gid}.json").write_text((G.GT_DIR / f"{gid}.json").read_text())
    pub, priv, outputs = tmp_path / "pub", tmp_path / "priv", tmp_path / "outputs"
    app = create_review_app(gt_dir, pub, priv, outputs)
    return TestClient(app), gt_dir, pub, priv, outputs


def _form(items, reviewer="Owner Reviewer"):
    f = {"reviewer": reviewer}
    for cat, spec in items.items():
        st, basis = spec[0], spec[1]
        f[f"{cat}__decision"] = st
        if basis:
            f[f"{cat}__basis"] = basis
        for k, v in (spec[2] if len(spec) > 2 else {}).items():
            f[f"{cat}__{k}"] = v
    return f


def test_review_page_shows_three_sources_and_never_prefills(env):
    c = env[0]
    page = c.get("/review/REAL_004").text
    for s in ("SOURCE DRAWING", "FIREAI INTERPRETATION", "CLAUDE DRAFT (not truth)", "YOUR DECISION"):
        assert s in page
    assert "checked" not in page.split("<form")[1].split("overall_note")[0].replace("unchecked", "")


def test_structured_corrections_roundtrip(env):
    c, gt_dir, pub, priv, _o = env
    r = c.post("/review/REAL_004", data=_form({
        "units": ("CONFIRMED", "cad_file_inspection"),
        "room_count": ("CONFIRMED", "visual_review_of_source_rendering"),
        "view_count": ("CORRECTED", "visual_review_of_source_rendering", {"value": "6"}),
        "view_types": ("CORRECTED", "visual_review_of_source_rendering",
                       {"value__SECTION": "4", "value__DETAIL": "2"}),
        "extents": ("CORRECTED", "cad_file_inspection", {"value__width_ft": "112.5", "value__height_ft": "48"}),
        "doors": ("CORRECTED", "visual_review_of_source_rendering", {"value__count": "0",
                                                                     "value__description": "door elevations only"}),
        "title_block": ("CORRECTED", "visual_review_of_source_rendering", {"value": "sheet: A-301\nscale: varies"}),
        "grids": ("NOT_EVALUATED", None, {"question": "are the dashed lines grids?"}),
    }), follow_redirects=False)
    assert r.status_code == 303, r.text
    rv = json.loads((pub / "REAL_004.json").read_text())
    assert not (priv / "REAL_004.json").exists()
    assert rv["schema"] == "human_review/2" and rv["reviewer_identity"] == "unauthenticated_name"
    it = rv["items"]["view_types"]
    assert it["human_decision"] == "CORRECTED" and it["human_corrected_value"] == {"SECTION": 4, "DETAIL": 2}
    assert it["claude_draft"] is None and it["decided_at"]
    assert rv["items"]["units"]["claude_draft"]["units"]["value"] == "in"      # draft snapshot preserved
    assert rv["items"]["title_block"]["human_corrected_value"] == {"sheet": "A-301", "scale": "varies"}
    rec = G.load_record("REAL_004", gt_dir)
    eff = G.effective(rec, rv, rec["source_sha256s"])
    assert eff["status"] == "PARTIALLY_HUMAN_REVIEWED"
    assert eff["truth"]["view_count"]["value"] == 6 and eff["truth"]["units"]["comparable"] == "in"
    assert eff["truth"]["room_count"]["comparable"] == 0
    assert eff["open_questions"] == {"grids": "are the dashed lines grids?"}
    assert json.loads((gt_dir / "REAL_004.json").read_text()) == rec             # draft untouched


def test_unchanged_decisions_keep_their_timestamp(env):
    c, _g, pub, *_ = env
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", "cad_file_inspection")}))
    t1 = json.loads((pub / "REAL_004.json").read_text())["items"]["units"]["decided_at"]
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", "cad_file_inspection"),
                                           "room_count": ("CONFIRMED", "cad_file_inspection")}))
    rv = json.loads((pub / "REAL_004.json").read_text())
    assert rv["items"]["units"]["decided_at"] == t1 and "room_count" in rv["items"]


def test_private_drawing_review_stays_local(env):
    c, _g, pub, priv, _o = env
    r = c.post("/review/REAL_001", data=_form({"units": ("CONFIRMED", "project_documents")}), follow_redirects=False)
    assert r.status_code == 303 and (priv / "REAL_001.json").exists() and not (pub / "REAL_001.json").exists()


@pytest.mark.parametrize("items,msg", [
    ({"units": ("CONFIRMED", "fireai_output")}, "FireAI output cannot be the basis"),
    ({"grids": ("CONFIRMED", "cad_file_inspection")}, "nothing in the draft to confirm"),
    ({"units": ("CORRECTED", "cad_file_inspection", {"value": ""})}, "choose one of"),
    ({"view_count": ("CORRECTED", "cad_file_inspection", {"value": "-2"})}, "must be 0 or more"),
    ({"room_boundaries": ("CORRECTED", "cad_file_inspection", {"value": '[{"name": "A", "polygon_src": [[0, 0]]}]'})},
     "at least 3 points"),
    ({"room_areas": ("CORRECTED", "cad_file_inspection", {"value": "OFFICE 200"})}, "NAME = number"),
    ({"units": ("CONFIRMED", None)}, "basis must be one of"),
])
def test_invalid_reviews_are_not_saved(env, items, msg):
    c, _g, pub, *_ = env
    r = c.post("/review/REAL_004", data=_form(items))
    assert r.status_code == 422 and msg in r.text and not (pub / "REAL_004.json").exists()


def test_review_invalidated_when_drawing_or_draft_changes(env):
    c, gt_dir, pub, *_ = env
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", "cad_file_inspection")}))
    rv = json.loads((pub / "REAL_004.json").read_text())
    rec = G.load_record("REAL_004", gt_dir)
    assert G.effective(rec, rv, ["0" * 64])["status"] == "INVALIDATED"
    rec["claude_draft"]["fields"]["units"]["value"] = "ft"
    assert G.effective(rec, rv, rec["source_sha256s"])["reason"] == "draft changed since review"


def test_pending_without_review_and_path_safety(env):
    c = env[0]
    assert "PENDING_HUMAN_VERIFICATION" in c.get("/").text
    assert c.get("/review/REAL_999").status_code == 404
    assert c.get("/img/../etc/source").status_code == 404


# ── evaluation uses HUMAN truth only ─────────────────────────────────────────

def test_comparison_uses_only_human_truth():
    machine = {"units": "in", "room_count": 0, "doors": {"count": 4}}
    assert FV.compare("units", machine["units"], None) == "no_human_truth"          # draft alone is not truth
    assert FV.compare("units", "in", {"comparable": "in"}) == "agree"
    assert FV.compare("units", "ft", {"comparable": "in"}) == "disagree"
    assert FV.compare("doors", {"count": 4}, {"comparable": {"count": 0, "description": "x"}}) == "disagree"
    assert FV.compare("drawing_type", None, {"comparable": "sections"}) == "not_comparable"
    assert FV.compare("room_areas", [{"name": "Office", "value": 201.0}],
                      {"comparable": [{"name": "OFFICE", "value": 200.0}]}) == "agree"
    assert FV.compare("extents", {"width_ft": 100, "height_ft": 50},
                      {"comparable": {"width_ft": 103, "height_ft": 50}}) == "disagree"


def test_summary_reports_by_category_without_overall_score(env, tmp_path):
    c, gt_dir, pub, priv, outputs = env
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", "cad_file_inspection"),
                                           "room_count": ("CORRECTED", "cad_file_inspection", {"value": "5"}),
                                           "grids": ("NOT_EVALUATED", None, {"question": "grid?"})}))
    # a fake FireAI model output for REAL_004 (units in, 0 rooms)
    from conftest import run_pipeline
    from fixtures import builders as B
    r = run_pipeline(B.make_office(tmp_path / "o.dxf", "in"), tmp_path)
    d = outputs / "run_x" / "REAL_004_dwg"
    d.mkdir(parents=True)
    (d / "building_model.json").write_text(r.path("model_json").read_text())
    s = build_summary(gt_dir, pub, priv, outputs)
    by = {x["id"]: x for x in s["drawings"]}
    assert by["REAL_004"]["status"] == "PARTIALLY_HUMAN_REVIEWED" and by["REAL_001"]["status"] == G.PENDING
    assert s["decisions"]["units"]["CONFIRMED"] == 1 and s["decisions"]["room_count"]["CORRECTED"] == 1
    assert s["agreement"]["units"]["agree"] == 1
    assert s["agreement"]["room_count"]["disagree"] == 1           # fixture has 3 rooms; human says 5
    assert [d["category"] for d in s["disagreements"]] == ["room_count"]
    md = to_markdown(s)
    assert "no overall accuracy score" in md and "grid?" in md
    assert "%" not in md.split("## FireAI vs human")[1].split("## Disagreements")[0]
