"""Run the Milestone 2.0/2.1 design path on a REAL verified job — READ-ONLY.

    job model → REAL verification gate (the review store as it is) → engineering_input/3
      → DesignRequest (mode "engineering", only the inputs that actually exist) → placement engine

It never records, simulates or bypasses a verification and never supplies engineering inputs that do
not exist; with no approved NFPA 13 rule set it must REFUSE (that refusal is the expected outcome).
M2.1: the real request also carries the registered NFPA 13-2025 base identity as an IN-MEMORY, EMPTY,
DRAFT rule set (nothing is written to the data directory) — it must still refuse.
``--synthetic`` additionally runs the same verified geometry through the TEST ONLY synthetic rule set
from the test fixtures; that output is marked TEST ONLY / NOT FOR ENGINEERING USE in its fields.

Usage (inside the product container or with FIREAI_DATA_DIR pointing at the data):
    python scripts/m2_design_check.py --job <job_id> --space <semantic space uid> [--synthetic]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _summary(r) -> dict:
    return {"status": r.status, "basis": r.basis, "engineering_use": r.engineering_use, "disclaimers": r.disclaimers,
            "refusals": [{"code": i.code, "message": i.message, "detail": i.detail} for i in r.refusals], "limitations": r.limitations,
            "space": r.context.get("space"), "regions": r.context.get("regions"), "frame": r.context.get("frame"),
            "z_status": r.context.get("z_status"), "verified_by": r.context.get("verified_by"),
            "package_content_fingerprint": r.context.get("package_content_fingerprint"),
            "search": r.search, "valid_layouts": len(r.valid_layouts),
            "valid_set_count": r.valid_set.count if r.valid_set else None,
            "constraints": [{"key": c["key"], "limit": c["limit"], "unit": c["unit"], "governing": c["governing_rule_id"]}
                            for c in (r.rules.get("constraints") or [])],
            "first_valid_layout": ([{"x": p.position.x, "y": p.position.y, "z": p.position.z.status,
                                     "room_uv": p.position.room_uv} for p in r.valid_layouts[0].placements]
                                   if r.valid_layouts else None),
            "result_fingerprint": r.result_fingerprint}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--space", required=True, help="semantic space uid (or physical region uid) from the package")
    ap.add_argument("--synthetic", action="store_true")
    a = ap.parse_args()
    from fireai.config import get_settings
    from fireai.contract import ContractViolation, build_engineering_input
    from fireai.engineering import DesignRequest, run_design
    from fireai.review.store import ReviewStore
    from fireai.schema import load_model
    st = get_settings()
    model, _ = load_model(json.loads((st.data_dir / "jobs" / a.job / "deliverables" / "building_model.json").read_text()))
    try:
        pkg = build_engineering_input(model, ReviewStore(st.review_dir))       # the REAL gate; nothing written
    except ContractViolation as exc:
        print(json.dumps({"gate": "REFUSED", "blockers": exc.blockers}, indent=1))
        return 2
    out = {"gate": "PASSED", "contract_version": pkg.contract_version, "verified_by": pkg.verified_by}
    from fireai.rules import RuleSet
    from fireai.rules.catalog import NFPA13_2025_BASE as ID
    draft = RuleSet(rule_set_id=ID.rule_set_id, version=ID.first_version, layer=ID.layer,       # EMPTY + DRAFT, in memory
                    governing_standard=ID.governing_standard, edition=ID.edition, content_basis="authoritative",
                    review_status="draft", rules=[], description=ID.description)
    real = DesignRequest(package=pkg, space_uid=a.space, mode="engineering", rule_sets=[draft],
                         requested_by="m2_design_check")
    t0 = time.perf_counter()
    out["engineering"] = _summary(run_design(real))
    out["engineering"]["seconds"] = round(time.perf_counter() - t0, 3)
    if a.synthetic:
        sys.path.insert(0, str(ROOT / "tests"))
        from fixtures import synthetic_design as S           # TEST ONLY fixture
        req = S.request(pkg, space_uid=a.space,
                        rule_sets=[S.base_ruleset(max_boundary=7.08, min_wall=0.5, max_spacing=10.0, max_cell=100.0)],
                        srch=S.search(step=0.5, max_n=4, max_axis=4))
        t0 = time.perf_counter()
        out["synthetic_TEST_ONLY"] = _summary(run_design(req))
        out["synthetic_TEST_ONLY"]["seconds"] = round(time.perf_counter() - t0, 3)
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
