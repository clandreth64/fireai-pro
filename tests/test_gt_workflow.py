"""Human ground-truth workflow (Milestone 1.6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "real_drawings"))
sys.path.insert(0, str(ROOT / "scripts"))
import gt as G  # noqa: E402
from gt_review_server import create_review_app  # noqa: E402


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
        # every draft field is mapped to a review category
        mapped = {f for fs in rec["claude_draft"]["by_category"].values() for f in fs}
        assert mapped == set(rec["claude_draft"]["fields"])
        assert set(rec["claude_draft"]["by_category"]) <= set(G.CATEGORIES)


def test_no_committed_human_review_yet_and_none_claims_verification():
    d = G.PUBLIC_REVIEWS
    reviews = list(d.glob("*.json")) if d.is_dir() else []
    for p in reviews:                         # if a person has added reviews, they must validate
        rv = json.loads(p.read_text())
        rec = G.load_record(rv["id"])
        assert G.validate_review(rv, rec, rec["source_sha256s"]) == []


@pytest.fixture()
def env(tmp_path):
    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    for gid in ("REAL_001", "REAL_004"):
        (gt_dir / f"{gid}.json").write_text((G.GT_DIR / f"{gid}.json").read_text())
    pub, priv = tmp_path / "pub", tmp_path / "priv"
    app = create_review_app(gt_dir, pub, priv, tmp_path / "outputs")
    return TestClient(app), gt_dir, pub, priv


def _form(items, reviewer="Human Reviewer"):
    f = {"reviewer": reviewer}
    for cat, (st, basis, value) in items.items():
        f[f"{cat}__status"] = st
        if basis:
            f[f"{cat}__basis"] = basis
        if value is not None:
            f[f"{cat}__value"] = value
    return f


def test_review_roundtrip_public(env):
    c, gt_dir, pub, priv = env
    page = c.get("/review/REAL_004").text
    assert "Claude draft (not truth)" in page and "NOT ground truth" in page
    r = c.post("/review/REAL_004", data=_form({
        "units": ("CONFIRMED", "cad_file_inspection", None),
        "room_count": ("CONFIRMED", "visual_review_of_source_rendering", None),
        "view_count": ("CORRECTED", "visual_review_of_source_rendering", "6"),
        "grids": ("NOT_EVALUATED", None, None)}), follow_redirects=False)
    assert r.status_code == 303
    rv = json.loads((pub / "REAL_004.json").read_text())
    assert not (priv / "REAL_004.json").exists()
    rec = G.load_record("REAL_004", gt_dir)
    eff = G.effective(rec, rv, rec["source_sha256s"])
    assert eff["status"] == "PARTIALLY_HUMAN_REVIEWED"
    assert eff["truth"]["view_count"]["value"] == 6 and eff["truth"]["view_count"]["draft"]
    assert eff["truth"]["units"]["value"]["units"]["value"] == "in"
    assert "grids" in eff["not_evaluated"] and "grids" not in eff["truth"]
    # draft record untouched
    assert json.loads((gt_dir / "REAL_004.json").read_text()) == rec


def test_private_drawing_review_stays_local(env):
    c, _gt, pub, priv = env
    r = c.post("/review/REAL_001", data=_form({"units": ("CONFIRMED", "project_documents", None)}),
               follow_redirects=False)
    assert r.status_code == 303 and (priv / "REAL_001.json").exists() and not (pub / "REAL_001.json").exists()


@pytest.mark.parametrize("items,msg", [
    ({"units": ("CONFIRMED", "fireai_output", None)}, "FireAI output cannot be the basis"),
    ({"grids": ("CONFIRMED", "cad_file_inspection", None)}, "nothing in the draft to confirm"),
    ({"units": ("CORRECTED", "cad_file_inspection", "")}, "CORRECTED needs the correct value"),
    ({"units": ("CONFIRMED", None, None)}, "basis must be one of"),
])
def test_invalid_reviews_are_not_saved(env, items, msg):
    c, _gt, pub, _priv = env
    r = c.post("/review/REAL_004", data=_form(items))
    assert r.status_code == 422 and msg in r.text and not (pub / "REAL_004.json").exists()


def test_review_invalidated_when_drawing_or_draft_changes(env):
    c, gt_dir, pub, _priv = env
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", "cad_file_inspection", None)}))
    rv = json.loads((pub / "REAL_004.json").read_text())
    rec = G.load_record("REAL_004", gt_dir)
    assert G.effective(rec, rv, ["0" * 64])["status"] == "INVALIDATED"
    rec["claude_draft"]["fields"]["units"]["value"] = "ft"
    assert G.effective(rec, rv, rec["source_sha256s"])["reason"] == "draft changed since review"


def test_pending_without_review(env):
    c, _gt, _pub, _priv = env
    assert "PENDING_HUMAN_VERIFICATION" in c.get("/").text
    assert c.get("/review/REAL_999").status_code == 404
    assert c.get("/img/../etc/source").status_code == 404
