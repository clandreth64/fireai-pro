"""REAL_002 regression (tests 36-38) against the owner's PERSISTENT data, READ-ONLY.

Runs only when the private data is mounted and named by environment (it is never committed):
    FIREAI_REAL002_DATA_DIR=/data  FIREAI_REAL002_JOB=<job id>  FIREAI_REAL002_SPACE=<space uid>
Mount the data read-only (``-v .fireai_data:/data:ro``). Nothing is written, nothing is supplied that the
owner has not decided: no hazard, envelope, construction classification, design method, ceiling
height, Z, branch-line orientation, listing, small-room status or rule value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fireai.contract import build_engineering_input
from fireai.engineering import DesignRequest, run_design
from fireai.review.store import ReviewStore
from fireai.rules.catalog import NFPA13_2019_BASE, NFPA13_2025_BASE, empty_draft
from fireai.schema import load_model

DATA, JOB, SPACE = (os.getenv(k) for k in ("FIREAI_REAL002_DATA_DIR", "FIREAI_REAL002_JOB", "FIREAI_REAL002_SPACE"))
pytestmark = pytest.mark.skipif(not (DATA and JOB and SPACE), reason="REAL_002 persistent data not mounted")

MISSING = {"MISSING_SPRINKLER_LISTING", "MISSING_CEILING_CONDITION", "MISSING_DESIGN_CLASSIFICATION",
           "MISSING_TOLERANCES", "MISSING_SEARCH_SPACE", "RULESET_NOT_APPROVED", "JURISDICTION_NOT_SPECIFIED"}


@pytest.fixture(scope="module")
def package():
    root = Path(DATA)
    model, _ = load_model(json.loads((root / "jobs" / JOB / "deliverables" / "building_model.json").read_text()))
    return build_engineering_input(model, ReviewStore(root / "reviews"))    # the REAL gate (raises if unverified)


def test_36_real002_remains_human_verified(package):
    assert package.contract_version == "engineering_input/3"
    assert package.verified_by and package.verification_fingerprint


def test_37_38_real002_remains_unclassified_and_real_engineering_refuses(package):
    for ident in (NFPA13_2025_BASE, NFPA13_2019_BASE):
        req = DesignRequest(package=package, space_uid=SPACE, mode="engineering", rule_sets=[empty_draft(ident)],
                            requested_by="m2.2a.1 regression")
        assert (req.classification, req.system, req.ceiling, req.orientation, req.deflector, req.listing) == \
            (None, None, None, None, None, None) and req.eligibility == {}
        r = run_design(req)
        codes = {i.code for i in r.refusals}
        assert r.status == "REFUSED" and MISSING <= codes, codes
        assert r.valid_set is None and r.valid_layouts == []
