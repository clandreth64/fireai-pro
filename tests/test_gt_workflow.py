"""Human review workflow (human_review/3): professional questions, visual flags, human-only truth."""

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

from conftest import run_pipeline  # noqa: E402
from fixtures import builders as B  # noqa: E402

CAD_WORDS = ("polygon", "coordinate", "entity", "entities", "handle", "layer", "count of walls", "wall count")


def test_all_committed_records_are_pending_claude_drafts():
    ids = G.list_ids()
    assert len(ids) == 11
    for gid in ids:
        rec = G.load_record(gid)
        assert rec["schema"] == "ground_truth/2" and rec["review_status"] == G.PENDING
        assert rec["claude_draft"]["author"].startswith("Claude")
        assert "human_review" not in rec
        assert rec["source_sha256s"] == G.corpus_entry(gid)["sha256"]


def test_committed_human_reviews_are_valid():
    d = G.PUBLIC_REVIEWS
    for p in (d.glob("*.json") if d.is_dir() else []):
        rv = json.loads(p.read_text())
        rec = G.load_record(rv["id"])
        assert G.validate_review(rv, rec, rec["source_sha256s"]) == [], p.name


def test_questions_need_no_cad_expertise():
    texts = [s["question"] for s in G.FACTS.values()] + list(G.EVALUATIONS.values())
    for t in texts:
        assert not any(w in t.lower() for w in CAD_WORDS), t
    assert set(G.EVALUATIONS) >= {"rooms_recognized", "room_labels", "room_boundaries", "walls", "doors_openings",
                                  "windows", "structure", "fire_protection", "excluded_content", "missing_content",
                                  "confident_errors"}


@pytest.fixture()
def env(tmp_path):
    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    for gid in ("REAL_001", "REAL_004"):
        (gt_dir / f"{gid}.json").write_text((G.GT_DIR / f"{gid}.json").read_text())
    pub, priv, outputs = tmp_path / "pub", tmp_path / "priv", tmp_path / "outputs"
    # a FireAI output for REAL_004 (synthetic building: 3 rooms, units in)
    r = run_pipeline(B.make_office(tmp_path / "o.dxf", "in"), tmp_path)
    d = outputs / "run_x" / "REAL_004_dwg"
    d.mkdir(parents=True)
    (d / "building_model.json").write_text(r.path("model_json").read_text())
    app = create_review_app(gt_dir, pub, priv, outputs)
    return TestClient(app), gt_dir, pub, priv, outputs, d


def _form(answers, flags=None, reviewer="Owner", em=None):
    f = {"reviewer": reviewer, "visual_flags": json.dumps(flags or [])}
    if em:
        f["evaluated_model"] = json.dumps(em)
    for q, spec in answers.items():
        f[f"{q}__decision"] = spec[0]
        for k, v in (spec[1] if len(spec) > 1 else {}).items():
            f[f"{q}__{k}"] = v
    return f


def _canvas(c, gid="REAL_004"):
    return c.get(f"/canvas/{gid}.json").json()


def test_page_is_professional_and_never_prefilled(env):
    c = env[0]
    page = c.get("/review/REAL_004").text
    for s in ("SOURCE DRAWING", "FIREAI INTERPRETATION", "Flag a FireAI item", "Mark something missing",
              "The major rooms/spaces are recognized.", "Nothing needs CAD knowledge"):
        assert s in page
    form = page.split("<form")[1].split("</form>")[0]
    assert " checked" not in form.replace("type='checkbox'", "")


def test_facts_and_evaluations_roundtrip_with_visual_flags(env):
    c, gt_dir, pub, priv, outputs, d = env
    cv = _canvas(c)
    room = next(i for i in cv["items"] if i["category"] == "room")
    flags = [{"flag": "merged_spaces", "target_uid": room["uid"], "note": "two rooms"},
             {"flag": "missing_room", "point_local": [5.0, 5.0]}]
    r = c.post("/review/REAL_004", data=_form({
        "units": ("CONFIRMED", {"basis": "cad_file_inspection"}),
        "view_count": ("CORRECTED", {"value": "4", "basis": "visual_review_of_source_rendering"}),
        "view_types": ("CORRECTED", {"value__SECTION": "2", "value__DETAIL": "2", "basis": "visual_review_of_source_rendering"}),
        "rooms_recognized": ("CORRECTED", {"severity": "critical", "description": "it is a section, no rooms"}),
        "review_flags": ("CONFIRMED", {}),
        "windows": ("NOT_EVALUATED", {"question": "are those glazing?"}),
    }, flags), follow_redirects=False)
    assert r.status_code == 303, r.text
    rv = json.loads((pub / "REAL_004.json").read_text())
    assert rv["schema"] == "human_review/3" and rv["reviewer_identity"] == "unauthenticated_name"
    assert rv["evaluated_model"]["model_sha256"] == FV.model_sha(d / "building_model.json")
    assert rv["items"]["units"]["confirmed_value"] == "in" and rv["items"]["units"]["confirmed_value_source"] == "fireai"
    assert rv["items"]["view_types"]["human_corrected_value"] == {"SECTION": 2, "DETAIL": 2}
    assert rv["items"]["rooms_recognized"]["human_corrected_value"]["severity"] == "critical"
    f0 = rv["visual_flags"][0]
    assert f0["target_category"] == "room" and "target_confident" in f0 and "target_flagged_by_fireai" in f0
    assert rv["visual_flags"][1]["point_src"]
    rec = G.load_record("REAL_004", gt_dir)
    eff = G.effective(rec, rv, rec["source_sha256s"], FV.model_sha(d / "building_model.json"))
    assert eff["truth"]["units"]["value"] == "in" and eff["truth"]["view_count"]["value"] == 4
    assert eff["evaluations"]["rooms_recognized"]["decision"] == "CORRECTED"
    assert eff["open_questions"] == {"windows": "are those glazing?"}
    assert json.loads((gt_dir / "REAL_004.json").read_text()) == rec                   # draft untouched


def _rewrite_model(d, fn):
    m = json.loads((d / "building_model.json").read_text())
    fn(m)
    (d / "building_model.json").write_text(json.dumps(m, indent=1))


def test_evaluations_go_stale_when_fireai_interpretation_changes_but_facts_persist(env):
    c, gt_dir, pub, _priv, outputs, d = env
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", {"basis": "cad_file_inspection"}),
                                           "walls": ("CONFIRMED", {})}))
    rv = json.loads((pub / "REAL_004.json").read_text())
    assert rv["evaluated_model"]["content_fingerprint"] == FV.content_fp(d / "building_model.json")
    room = lambda m: next(e for e in m["elements"] if e["category"] == "room")   # noqa: E731
    _rewrite_model(d, lambda m: room(m)["geometry"]["points"].__setitem__(0, [0.0, 0.0]))   # a boundary moved
    rec = G.load_record("REAL_004", gt_dir)
    eff = G.effective(rec, rv, rec["source_sha256s"], **FV.currency(d / "building_model.json", rv, outputs))
    assert eff["currency_basis"] == "content_fingerprint"
    assert "units" in eff["truth"] and eff["evaluations"] == {} and "walls" in eff["stale_evaluations"]
    assert "re-check" in c.get("/").text


def test_run_metadata_alone_does_not_make_evaluations_stale(env):
    """Same interpretation, new run: new model id and timestamp, re-serialized file (other bytes)."""
    c, gt_dir, pub, _priv, outputs, d = env
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", {"basis": "cad_file_inspection"}),
                                           "walls": ("CONFIRMED", {})}))
    rv = json.loads((pub / "REAL_004.json").read_text())
    before = FV.model_sha(d / "building_model.json")
    _rewrite_model(d, lambda m: m.update(model_id="f" * 32, created_at="2030-01-01T00:00:00+00:00"))
    assert FV.model_sha(d / "building_model.json") != before
    rec = G.load_record("REAL_004", gt_dir)
    eff = G.effective(rec, rv, rec["source_sha256s"], **FV.currency(d / "building_model.json", rv, outputs))
    assert eff["evaluations"] and not eff["stale_evaluations"] and eff["currency_basis"] == "content_fingerprint"
    assert "re-check" not in c.get("/").text
    # the byte-level comparison remains the conservative fallback when no content fingerprint is known
    legacy = G.effective(rec, rv, rec["source_sha256s"], FV.model_sha(d / "building_model.json"))
    assert legacy["stale_evaluations"] and legacy["currency_basis"] == "file_bytes"


def test_legacy_review_is_judged_by_the_content_of_the_exact_artifact_it_evaluated(env, tmp_path):
    """A review recorded before content fingerprints: the reviewed artifact is located, verified by its
    sha256 and fingerprinted; a re-run with identical content stays current, other content goes stale."""
    c, gt_dir, pub, _priv, outputs, d = env
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", {"basis": "cad_file_inspection"}),
                                           "walls": ("CONFIRMED", {})}))
    rv = json.loads((pub / "REAL_004.json").read_text())
    rv["evaluated_model"].pop("content_fingerprint")                                      # an older record
    rec = G.load_record("REAL_004", gt_dir)
    rerun = outputs / "run_y" / "REAL_004_dwg"
    rerun.mkdir(parents=True)
    m = json.loads((d / "building_model.json").read_text())
    m.update(model_id="e" * 32, created_at="2031-01-01T00:00:00+00:00")
    (rerun / "building_model.json").write_text(json.dumps(m))
    eff = G.effective(rec, rv, rec["source_sha256s"], **FV.currency(rerun / "building_model.json", rv, outputs))
    assert not eff["stale_evaluations"] and eff["currency_basis"] == "content_fingerprint"
    m["elements"][0]["label"] = "SOMETHING ELSE"
    (rerun / "building_model.json").write_text(json.dumps(m))
    eff = G.effective(rec, rv, rec["source_sha256s"], **FV.currency(rerun / "building_model.json", rv, outputs))
    assert eff["stale_evaluations"]
    # the reviewed artifact changed on disk -> it can no longer be trusted as the reference -> bytes decide
    (d / "building_model.json").write_text((d / "building_model.json").read_text() + " ")
    eff = G.effective(rec, rv, rec["source_sha256s"], **FV.currency(rerun / "building_model.json", rv, outputs))
    assert eff["stale_evaluations"] and eff["currency_basis"] == "file_bytes"


def test_private_drawing_review_stays_local(env):
    c, _g, pub, priv, *_ = env
    r = c.post("/review/REAL_001", data=_form({"units": ("CORRECTED", {"value": "in", "basis": "project_documents"})}),
               follow_redirects=False)
    assert r.status_code == 303 and (priv / "REAL_001.json").exists() and not (pub / "REAL_001.json").exists()


@pytest.mark.parametrize("answers,flags,msg", [
    ({"units": ("CONFIRMED", {"basis": "fireai_output"})}, None, "FireAI output cannot be the basis"),
    ({"units": ("CONFIRMED", {})}, None, "basis must be one of"),
    ({"view_count": ("CORRECTED", {"value": "-2", "basis": "other"})}, None, "must be 0 or more"),
    ({"walls": ("CORRECTED", {"severity": "fatal", "description": "x"})}, None, "severity must be one of"),
    ({"walls": ("CORRECTED", {"severity": "major", "description": ""})}, None, "describe what is wrong"),
    ({}, [{"flag": "wrong_label"}], "select the FireAI item"),
    ({}, [{"flag": "missing_room"}], "needs the clicked location"),
    ({}, [{"flag": "wrong_label", "target_uid": "not-in-this-output"}], "not in this FireAI output"),
])
def test_invalid_reviews_are_not_saved(env, answers, flags, msg):
    c, _g, pub, *_ = env
    r = c.post("/review/REAL_004", data=_form(answers, flags))
    assert r.status_code == 422 and msg in r.text and not (pub / "REAL_004.json").exists()


def test_fireai_output_changed_during_review_is_rejected(env):
    c, _g, pub, *_ = env
    r = c.post("/review/REAL_004", data=_form({"walls": ("CONFIRMED", {})}, em={"model_sha256": "0" * 64}))
    assert r.status_code == 422 and "changed while you were reviewing" in r.text


def test_invalidated_when_drawing_or_draft_changes(env):
    c, gt_dir, pub, *_ = env
    c.post("/review/REAL_004", data=_form({"units": ("CONFIRMED", {"basis": "cad_file_inspection"})}))
    rv = json.loads((pub / "REAL_004.json").read_text())
    rec = G.load_record("REAL_004", gt_dir)
    assert G.effective(rec, rv, ["0" * 64])["status"] == "INVALIDATED"
    rec["claude_draft"]["fields"]["units"]["value"] = "ft"
    assert G.effective(rec, rv, rec["source_sha256s"])["reason"] == "draft changed since review"


def test_path_safety(env):
    c = env[0]
    assert c.get("/review/REAL_999").status_code == 404
    assert c.get("/img/../etc/source").status_code == 404
    assert c.get("/canvas/REAL_999.json").status_code == 404


def test_metrics_are_engineering_meaning_and_not_one_score(env):
    c, gt_dir, pub, priv, outputs, d = env
    cv = _canvas(c)
    rooms = [i for i in cv["items"] if i["category"] == "room"]
    flags = [{"flag": "not_a_room", "target_uid": rooms[0]["uid"]},
             {"flag": "wrong_label", "target_uid": rooms[1]["uid"]},
             {"flag": "missing_room", "point_local": [1.0, 1.0]}]
    c.post("/review/REAL_004", data=_form({
        "view_types": ("CORRECTED", {"value__FLOOR_PLAN": "1", "basis": "visual_review_of_source_rendering"}),
        "doors_openings": ("CONFIRMED", {}), "walls": ("CORRECTED", {"severity": "minor", "description": "one wall"})},
        flags))
    s = build_summary(gt_dir, pub, priv, outputs)
    m = next(x for x in s["drawings"] if x["id"] == "REAL_004")["metrics"]
    n_rooms = len(rooms)
    assert m["false_room_rate"] == {"n": 1, "d": n_rooms, "text": f"1/{n_rooms}"}
    assert m["missed_room_rate"]["n"] == 1 and m["missed_room_rate"]["d"] == n_rooms - 1 + 1
    assert m["room_label_association"]["n"] == 1
    assert m["opening_recognition"]["evaluation"] == "CONFIRMED" and m["major_wall_geometry"]["evaluation"] == "CORRECTED"
    assert m["false_confident_interpretation_rate"]["d"] == 2 and m["critical_unflagged_error_rate"]["d"] == 2
    assert m["view_classification"] in ("agree", "disagree")
    md = to_markdown(s)
    assert "no overall accuracy score" in md and "false rooms" in md
    assert "%" not in md.split("## Engineering-meaning metrics")[1]


def test_comparison_uses_only_human_truth():
    assert FV.compare_fact("units", "in", None) == "no_human_truth"
    assert FV.compare_fact("units", "in", {"comparable": "in"}) == "agree"
    assert FV.compare_fact("view_types", {"FLOOR_PLAN": 2}, {"comparable": {"FLOOR_PLAN": 1}}) == "disagree"
    assert FV.compare_fact("drawing_type", None, {"comparable": "plans"}) == "not_comparable"
