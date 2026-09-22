"""Golden-file tests: every drawings/<stem>.* with expected/<stem>.json."""

import json
import math
import shutil
from pathlib import Path

import pytest

from conftest import run_pipeline

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted((GOLDEN / "expected").glob("*.json"))


def _drawing(stem: str) -> Path:
    found = [p for p in (GOLDEN / "drawings").glob(stem + ".*")]
    assert len(found) == 1, f"expected exactly one drawing for {stem}, found {found}"
    return found[0]


@pytest.mark.parametrize("case", CASES, ids=[c.stem for c in CASES])
def test_golden(case, tmp_path):
    exp = json.loads(case.read_text())
    path = _drawing(case.stem)
    converter_present = bool(shutil.which("ODAFileConverter") or shutil.which("dwg2dxf"))
    if path.suffix == ".dwg":
        if exp.get("skip_if_dwg_converter_available") and converter_present:
            pytest.skip("converter installed; unavailable-converter expectation does not apply")
        if not exp.get("skip_if_dwg_converter_available") and not converter_present:
            pytest.skip("DWG converter not installed")
    kw = {"dwg_converter": "auto"} if path.suffix == ".dwg" and converter_present else {}
    r = run_pipeline(path, tmp_path, units=exp.get("units_override"), **kw)

    assert r.processing_status == exp["processing_status"], r.failure
    assert (r.failure or {}).get("code") == exp.get("failure_code")
    assert r.report["geometry_is_synthetic"] is False
    if "requires_human_review" in exp:
        assert r.report["requires_human_review"] is exp["requires_human_review"]
    m = r.model
    if "units" in exp:
        assert m.units.resolved_units == exp["units"]
    if "bounds_ft" in exp:
        b = exp["bounds_ft"]
        assert math.isclose(m.bounds_normalized.width, b["width"], abs_tol=b.get("tol", 0.01))
        assert math.isclose(m.bounds_normalized.height, b["height"], abs_tol=b.get("tol", 0.01))
    if "entity_counts" in exp:
        ec = exp["entity_counts"]
        if "top_level" in ec:
            assert sum(1 for e in m.entities if e.parent_id is None and e.space == "model") == ec["top_level"]
        if "total" in ec:
            assert len(m.entities) == ec["total"]
    for cat, n in (exp.get("element_counts") or {}).items():
        assert len(m.elements_of(cat)) == n, cat
    for cat, n in (exp.get("min_element_counts") or {}).items():
        assert len(m.elements_of(cat)) >= n, cat
    rooms = {e.label: e.properties["area_sf"] for e in (m.elements_of("room") if m else [])}
    for room in exp.get("rooms", []):
        assert room["label"] in rooms, (room, sorted(rooms))
        assert math.isclose(rooms[room["label"]], room["area_sf"], abs_tol=room.get("tol", 0.5))
    texts = " | ".join(e.source.text for e in m.entities if e.source and e.source.text) if m else ""
    for t in exp.get("text_contains", []):
        assert t in texts, t
    codes = {t.code for t in m.diagnostics.review_triggers} if m else set()
    for c in exp.get("review_triggers_include", []):
        assert c in codes, (c, codes)
    for c in exp.get("review_triggers_exclude", []):
        assert c not in codes, (c, codes)
