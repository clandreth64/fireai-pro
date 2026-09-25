"""Runs a known-answer case through the deterministic engine and records the outcome (M2.2B).

The case's EXPECTED values are the person's; this harness only measures and compares. A mismatch is
recorded as a failed verification — it never rewrites the case."""

from __future__ import annotations

from pathlib import Path

from fireai.engineering.design import PLACEMENT_ENGINE_VERSION, CandidateProposal
from fireai.engineering.geometry import room_frame
from fireai.engineering.inputs import DeflectorPosition, Elevation, LayoutOrientation
from fireai.engineering.placement import evaluate_proposal
from fireai.rules.known_answers import KnownAnswerCase, KnownAnswerStore, rule_content_digest
from fixtures import builders as B
from fixtures import commercial_2019 as C
from fixtures import synthetic_design as S

_PACKAGES: dict = {}


def package_for(fixture: dict, tmp: Path):
    key = tuple(sorted((k, str(v)) for k, v in fixture.items() if k != "note"))
    if key not in _PACKAGES:
        if fixture["builder"] == "commercial_2019":
            _PACKAGES[key] = C.space(tmp)
        elif fixture["builder"] == "rectangular_room":
            d = tmp / f"ka_{len(_PACKAGES)}"
            d.mkdir(parents=True, exist_ok=True)
            _PACKAGES[key] = S.verified_package(d, B.make_room(d / "r.dxf", fixture.get("unit", "ft"),
                                                               w=fixture["width_ft"], h=fixture["depth_ft"]))
        else:
            raise ValueError(f"unknown fixture builder {fixture['builder']!r}")
    return _PACKAGES[key]


def run_case(case: KnownAnswerCase, rule_set, tmp: Path, *, mode: str = "rule_review") -> dict:
    pkg = package_for(case.fixture, tmp)
    fr = room_frame([tuple(p) for p in pkg.spaces[0].polygon_local_ft])
    o = case.orientation or {}
    over = {"orientation": LayoutOrientation(branch_line_direction=tuple(o["branch_line_direction"]),
                                             frame=o.get("frame", "LOCAL"), strategy="explicit_design_input",
                                             source=C.FIXTURE_SOURCE)} if o else {"orientation": None}
    if "deflector_elevation_ft" in case.inputs:
        over["deflector"] = DeflectorPosition(elevation=Elevation(status="known", value_ft=case.inputs["deflector_elevation_ft"],
                                                                  datum=C.DATUM, source=C.FIXTURE_SOURCE))
    req = C.request(pkg, [rule_set], mode=mode, **over)
    positions = [fr.to_xy(u, v) for u, v in case.inputs["positions_room_ft"]]
    prop = CandidateProposal(proposed_by=f"known-answer case {case.case_id}",
                             package_content_fingerprint=pkg.content_fingerprint,
                             package_verification_fingerprint=pkg.verification_fingerprint,
                             rule_sets=[{"rule_set_id": rule_set.rule_set_id, "version": rule_set.version,
                                         "digest": rule_set.digest()}],
                             listing={"listing_id": req.listing.listing_id, "version": req.listing.version,
                                      "digest": req.listing.digest()}, positions=positions)
    ev = evaluate_proposal(req, prop)
    e = next((x for x in ev.evaluations if x.constraint_key == case.constraint_key), None)
    return {"verdict": ev.verdict, "reasons": [r.code for r in ev.reasons],
            "measured": e.measured if e else None, "limit": e.limit if e else None,
            "outcome": ("PASS" if e.outcome == "pass" else "FAIL" if e.outcome == "fail" else "NOT_EVALUABLE")
            if e else "REFUSED"}


def verify(store: KnownAnswerStore, case_id: str, rule_set, tmp: Path, tolerance: float = 1e-6) -> dict:
    case = store.get(case_id)
    rule = next(r for r in rule_set.rules if r.rule_id == case.rule_id)
    got = run_case(case, rule_set, tmp)
    return store.record_verification(case_id, measured=got["measured"], limit=got["limit"], outcome=got["outcome"],
                                     engine=PLACEMENT_ENGINE_VERSION, rule_set_digest=rule_set.digest(),
                                     tolerance=tolerance, rule_digest=rule_content_digest(rule))
