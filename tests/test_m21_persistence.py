"""Milestone 2.1: persistent project engineering model — revisions, invalidation, downstream use.
TEST ONLY synthetic design runs on generated rooms."""

from __future__ import annotations

import pytest

from fireai.engineering import run_design
from fireai.project import ProjectError, ProjectStore, SpaceRef, currency, dependencies_of
from fixtures import builders as B
from fixtures import synthetic_design as S


@pytest.fixture()
def env(tmp_path):
    d = tmp_path / "room"
    d.mkdir()
    pkg = S.verified_package(d, B.make_room(d / "r.dxf", w=20, h=10))
    ps = ProjectStore(tmp_path / "projects")
    prj = ps.create_project("fixture project", "Alice")
    bld = ps.add_building(prj.uid, "Building A", "Alice")
    lvl = ps.add_level(prj.uid, bld.uid, "Level 2", "Alice")
    sp = pkg.semantic_spaces[0]
    area = ps.add_design_area(prj.uid, "Area 1", "Alice", [SpaceRef(source_sha256=pkg.source_sha256, space_uid=sp.uid,
                                                                   region_uid=pkg.regions[0].uid, label=sp.label)],
                              building_uid=bld.uid, level_uid=lvl.uid)
    req = S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08)], srch=S.search(1.0, 2))
    return ps, prj, area, pkg, req, tmp_path


def test_01_02_design_persists_and_history_is_kept(env):
    ps, prj, area, _pkg, req, _t = env
    r1 = run_design(req)
    rev1 = ps.record_design(prj.uid, area.uid, req, r1, "Alice")
    rev2 = ps.record_design(prj.uid, area.uid, req, run_design(req), "Bob", design_uid=rev1.design_uid)
    revs = ps.revisions(prj.uid, rev1.design_uid)
    assert [r.revision for r in revs] == [1, 2] and revs[1].uid == rev2.uid and revs[1].supersedes == rev1.uid
    assert ps.load_result(prj.uid, rev1).result_fingerprint == r1.result_fingerprint     # history intact
    assert ps.load_request(prj.uid, rev1).package.content_fingerprint == req.package.content_fingerprint
    assert currency(rev1, dependencies_of(req))["status"] == "CURRENT"
    with pytest.raises(ProjectError, match="never overwritten"):
        ps._write_once(ps.root / prj.uid / "designs" / rev1.design_uid / "rev-0001" / "result.json", r1)


def test_03_verified_model_change_invalidates(env):
    ps, prj, area, pkg, req, tmp = env
    rev = ps.record_design(prj.uid, area.uid, req, run_design(req), "Alice")
    d = tmp / "room2"
    d.mkdir()
    other = S.verified_package(d, B.make_room(d / "r.dxf", w=20, h=10, window=("left", 3, 6)))   # the drawing changed
    now = dependencies_of(S.request(other, rule_sets=req.rule_sets, srch=req.search))
    cur = currency(rev, now)
    assert cur["status"] == "INVALIDATED" and "verified building model changed" in cur["reasons"][0]
    corrected = pkg.model_copy(update={"verification_fingerprint": "f" * 64})                   # re-verification / correction
    assert currency(rev, dependencies_of(req.model_copy(update={"package": corrected})))["status"] == "INVALIDATED"


@pytest.mark.parametrize("what", ["rules", "listing", "input"])
def test_04_05_06_rules_listing_and_input_changes_stale(env, what):
    ps, prj, area, _pkg, req, _t = env
    rev = ps.record_design(prj.uid, area.uid, req, run_design(req), "Alice")
    if what == "rules":
        new = req.model_copy(update={"rule_sets": [S.base_ruleset(max_boundary=7.5, version="2")]})
    elif what == "listing":
        new = req.model_copy(update={"listing": S.listing().model_copy(update={"version": "2"})})
    else:
        new = req.model_copy(update={"ceiling": S.ceiling(req.space_uid, elevation_ft=10.0)})
    cur = currency(rev, dependencies_of(new))
    assert cur["status"] == "STALE"
    assert {"rules": "rule sets", "listing": "listing", "input": "input 'ceiling'"}[what] in " ".join(cur["reasons"])


def test_selected_layout_is_consumable_without_cad(env):
    ps, prj, area, _pkg, req, _t = env
    rev = ps.record_design(prj.uid, area.uid, req, run_design(req), "Alice")
    fam = ps.load_result(prj.uid, rev).valid_set.families[0]
    iu = [fam.offsets[0][0] + k * fam.ds_u for k in range(fam.n_u)]
    iv = [fam.offsets[0][1] + k * fam.ds_v for k in range(fam.n_v)]
    with pytest.raises(ProjectError, match="VALID set"):
        ps.select_layout(prj.uid, rev, [1], [1], "Alice", "not valid")
    sel = ps.select_layout(prj.uid, rev, iu, iv, "Alice", "fixture selection")
    placements = ps.selection_placements(prj.uid, rev, sel)
    assert [round(p.position.x - 0.5, 9) for p in placements] == [5.0, 15.0]         # (5,5),(15,5) — see M2.0 test 2
    assert all(p.position.frame == "LOCAL" and p.position.z.status == "unknown" for p in placements)


def test_inputs_are_versioned_and_hierarchy_supports_scopes(env):
    ps, prj, area, _pkg, req, _t = env
    a = ps.record_input(prj.uid, area.uid, "ceiling", req.ceiling, "Alice")
    b = ps.record_input(prj.uid, area.uid, "ceiling", S.ceiling(req.space_uid, 10.0), "Bob")
    assert (a.version, b.version, b.supersedes) == (1, 2, a.uid) and a.digest != b.digest
    assert [r.version for r in ps.inputs(prj.uid, area.uid, "ceiling")] == [1, 2]
    other = ps.add_design_area(prj.uid, "Area 2 (other criteria)", "Alice", [])
    assert other.uid != area.uid and ps.design_area(prj.uid, area.uid).level_uid is not None
