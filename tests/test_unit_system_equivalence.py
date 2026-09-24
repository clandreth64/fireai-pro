"""Unit-system semantic regression: the same architecture drawn in different units must be
understood the same way (view types, physical regions, semantic spaces, openings, connectivity,
engineering blockers, and normalized geometry within tolerance).

* Generated drawings in every supported unit always run (no private data).
* The real imperial/metric pair in ``real_drawings/equivalence_pairs.json`` runs when the local
  corpus and a DWG converter are present; declared, evidence-backed differences must match exactly
  and must still be observed (a stale declaration fails).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import ezdxf
import pytest

from conftest import run_pipeline
from fixtures import builders as B

sys.path.insert(0, str(Path(__file__).parent / "real_drawings"))
from semantic_pair import compare, full_signature, undeclared  # noqa: E402

HERE = Path(__file__).parent
PAIRS = json.loads((HERE / "real_drawings" / "equivalence_pairs.json").read_text(encoding="utf-8"))["pairs"]
LOCAL = HERE / "real_drawings_local" / "drawings"
EXACT = {"extent_rel": 1e-6, "area_rel": 1e-6, "wall_length_rel": 1e-6}


def _scaled(src: Path, dst: Path, insunits: int, factor: float) -> Path:
    doc = ezdxf.readfile(src)
    doc.header["$INSUNITS"] = insunits
    for e in list(doc.modelspace()):
        e.transform(ezdxf.math.Matrix44.scale(factor, factor, 1))
    doc.saveas(dst)
    return dst


@pytest.mark.parametrize("unit", ["in", "mm", "cm", "m"])
def test_office_in_every_unit_means_the_same_as_feet(results, unit):
    ref = full_signature(results["office_ft"].model)
    other = full_signature(results[f"office_{unit}"].model)
    assert ref["views"] and ref["views"][0]["known_spaces"]
    assert compare(ref, other, EXACT) == []


@pytest.mark.parametrize("unit,code,factor", [("in", 1, 12.0), ("mm", 4, 304.8), ("m", 6, 0.3048)])
@pytest.mark.parametrize("with_door", [True, False])
def test_wall_polygonized_plan_is_unit_independent(tmp_path, unit, code, factor, with_door):
    """Rooms derived from wall linework (door closures, open regions, unresolved spaces)."""
    ft = B.make_walls_only(tmp_path / "ft.dxf", with_door=with_door)
    other = _scaled(ft, tmp_path / f"{unit}.dxf", code, factor)
    sa = full_signature(run_pipeline(ft, tmp_path).model)
    sb = full_signature(run_pipeline(other, tmp_path).model)
    assert compare(sa, sb, EXACT) == []
    if not with_door:
        assert sa["views"][0]["unresolved_spaces"] and sa["views"][0]["blockers"]


def test_a_unit_misreading_is_detected(fx, results, tmp_path):
    """Drawn in inches but declared mm: read as-declared, the building is 25.4x too small. The
    regression must report it (this is the failure it exists to catch)."""
    ref = full_signature(results["office_in"].model)
    wrong = run_pipeline(fx["mislabeled_units"], tmp_path, units="mm").model
    fields = {d["field"] for d in compare(ref, full_signature(wrong))}
    assert "extent_ft[0]" in fields and any(f.startswith("area_sf[") for f in fields)


def test_a_semantic_divergence_is_detected(tmp_path):
    a = B.make_walls_only(tmp_path / "a.dxf", with_door=True)
    b = B.make_walls_only(tmp_path / "b.dxf", with_door=True, right_label="STORAGE")
    d = compare(full_signature(run_pipeline(a, tmp_path).model), full_signature(run_pipeline(b, tmp_path).model))
    assert {"field": "known_spaces", "view": 0, "a_only": ["SALES FLOOR"], "b_only": ["STORAGE"]} in d


def test_declarations_match_exactly_and_go_stale():
    diffs = [{"view": 0, "field": "counts.column", "a": 5, "b": 0}]
    ok = [{"view": 0, "field": "counts.column", "a": 5, "b": 0, "evidence": "x"}]
    assert undeclared(diffs, ok) == ([], [])
    other = [{"view": 0, "field": "counts.column", "a": 6, "b": 0}]
    assert undeclared(other, ok) == (other, ok)                   # a new divergence is never hidden
    assert undeclared([], ok) == ([], ok)                         # a fixed difference makes the declaration stale


@pytest.mark.parametrize("pair", PAIRS, ids=[f"{p['a']}~{p['b']}" for p in PAIRS])
def test_real_unit_system_pair(tmp_path, pair):
    pa, pb = LOCAL / pair["a"], LOCAL / pair["b"]
    if not (pa.is_file() and pb.is_file()):
        pytest.skip("local real-drawing corpus not present")
    if any(p.suffix.lower() == ".dwg" for p in (pa, pb)) and not shutil.which("dwg2dxf"):
        pytest.skip("LibreDWG not installed")
    ma = run_pipeline(pa, tmp_path, dwg_converter="auto").model
    mb = run_pipeline(pb, tmp_path, dwg_converter="auto").model
    new, stale = undeclared(compare(full_signature(ma), full_signature(mb)), pair["declared_differences"])
    assert new == [], f"undeclared semantic differences between {pair['a']} and {pair['b']}: {new}"
    assert stale == [], f"declared differences no longer observed (remove them): {stale}"
