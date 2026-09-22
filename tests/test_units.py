"""Unit handling: exact conversion, imperial/metric equivalence, no silent defaults."""

import math

import ezdxf
import pytest

from conftest import run_pipeline
from fireai.errors import FailureCode, PipelineFailure
from fireai.ingest.units import FEET_PER_UNIT, parse_user_units, resolve_units
from fixtures.builders import OFFICE_ROOM_AREAS

UNITS = ["in", "ft", "mm", "cm", "m"]


def test_conversion_factors_are_exact():
    assert FEET_PER_UNIT["in"] * 12 == 1.0
    assert math.isclose(FEET_PER_UNIT["mm"] * 304.8, 1.0, rel_tol=0, abs_tol=1e-15)
    assert math.isclose(FEET_PER_UNIT["cm"] * 30.48, 1.0, abs_tol=1e-15)
    assert math.isclose(FEET_PER_UNIT["m"] * 0.3048, 1.0, abs_tol=1e-15)


@pytest.mark.parametrize("code,unit", [(1, "in"), (2, "ft"), (4, "mm"), (5, "cm"), (6, "m")])
def test_header_codes_resolve(code, unit):
    r = resolve_units(code, None, None)
    assert r.resolved and r.resolved_units == unit and r.method == "drawing_header"


@pytest.mark.parametrize("code", [None, 0, 3, 7, 10, 21, 99, -1])
def test_unknown_or_unsupported_codes_do_not_resolve(code):
    r = resolve_units(code, 0, None)
    assert not r.resolved and r.scale_to_ft is None and r.method == "unresolved"
    assert r.note


def test_measurement_hint_is_never_used_to_resolve():
    r = resolve_units(0, 1, None)  # $MEASUREMENT=1 (metric) must not imply mm
    assert not r.resolved and r.measurement_hint == "metric"


def test_user_override_and_bad_override():
    assert resolve_units(0, None, "feet").resolved_units == "ft"
    with pytest.raises(PipelineFailure) as e:
        parse_user_units("furlongs")
    assert e.value.code == FailureCode.UNIT_DETECTION_FAILED


@pytest.mark.parametrize("unit", UNITS)
def test_each_unit_normalizes_to_true_size(results, unit):
    r = results[f"office_{unit}"]
    assert r.processing_status == "completed"
    m = r.model
    assert m.units.resolved_units == unit
    walls = m.elements_of("wall")
    xs = [p[0] for w in walls for p in w.geometry.points]
    ys = [p[1] for w in walls for p in w.geometry.points]
    assert math.isclose(max(xs) - min(xs), 100.0, abs_tol=1e-6)   # never 1000x off
    assert math.isclose(max(ys) - min(ys), 60.0, abs_tol=1e-6)
    areas = {e.label: e.properties["area_sf"] for e in m.elements_of("room")}
    for name, expected in OFFICE_ROOM_AREAS.items():
        assert math.isclose(areas[name], expected, abs_tol=0.01), (unit, name)


def test_imperial_and_metric_models_are_geometrically_equivalent(results):
    ref = results["office_in"].model
    for unit in ("ft", "mm", "cm", "m"):
        other = results[f"office_{unit}"].model
        assert len(other.entities) == len(ref.entities)
        assert math.isclose(other.bounds_normalized.width, ref.bounds_normalized.width, abs_tol=1e-6)
        assert math.isclose(other.bounds_normalized.height, ref.bounds_normalized.height, abs_tol=1e-6)
        for a, b in zip(ref.entities, other.entities, strict=True):
            assert a.type == b.type and a.layer == b.layer
            if a.normalized is None:
                continue
            for p, q in zip(a.normalized.points, b.normalized.points, strict=True):
                assert math.isclose(p[0], q[0], abs_tol=1e-6) and math.isclose(p[1], q[1], abs_tol=1e-6), (unit, a.id)
            if a.normalized.radius is not None:
                assert math.isclose(a.normalized.radius, b.normalized.radius, abs_tol=1e-6)
        assert [e.category for e in ref.elements] == [e.category for e in other.elements]


def test_metric_drawing_is_not_scaled_1000x(results):
    b = results["office_mm"].model.bounds_normalized
    assert 100 < b.width < 200 and 60 < b.height < 120   # includes title block & dims; ~143.7 x 81.5 ft


def test_source_coordinates_are_preserved_and_traceable(results):
    m = results["office_mm"].model
    line = next(e for e in m.entities if e.type == "LINE" and e.layer == "A-WALL")
    # builder origin is (1000 ft, 400 ft) -> source coordinates are in mm at that offset
    assert line.source.points[0][0] > 300_000
    ox, oy = m.transform.origin
    for (sx, sy), (nx, ny) in zip(line.source.points, line.normalized.points, strict=True):
        assert math.isclose(nx, (sx - ox) * m.transform.scale, abs_tol=1e-9)
        assert math.isclose(ny, (sy - oy) * m.transform.scale, abs_tol=1e-9)


@pytest.mark.parametrize("key", ["missing_units", "unitless", "miles", "bogus_units"])
def test_unknown_units_require_human_input(fx, tmp_path, key):
    r = run_pipeline(fx[key], tmp_path)
    assert r.processing_status == "needs_human_input"
    assert r.failure["code"] == "UNIT_DETECTION_FAILED"
    assert r.report["requires_human_review"] is True
    req = r.report["unit_resolution_required"]
    assert req and set(req["accepted_units"]) == set(FEET_PER_UNIT)
    assert r.model is not None and r.model.units.resolved is False
    assert all(e.normalized is None for e in r.model.entities)   # nothing normalized with guessed units
    assert r.model.elements == []
    assert "overlay_png" not in r.deliverables and "source_png" in r.deliverables


def test_missing_units_fixture_really_has_no_header_var(fx):
    doc = ezdxf.readfile(fx["missing_units"])
    assert "$INSUNITS" not in doc.header


def test_user_override_resolves_unitless_drawing(fx, tmp_path):
    r = run_pipeline(fx["unitless"], tmp_path, units="ft")
    assert r.processing_status == "completed"
    assert r.model.units.resolution_method == "user_override"
    assert any(w.code == "UNITS_FROM_USER" for w in r.model.diagnostics.warnings)
    assert math.isclose(r.model.elements_of("room")[0].properties["area_sf"], 39 * 29, abs_tol=0.01)


def test_mislabeled_units_trigger_review(fx, tmp_path):
    """Drawn in inches but header says mm: extents become ~4 ft — must be flagged, not accepted."""
    r = run_pipeline(fx["mislabeled_units"], tmp_path)
    assert r.processing_status == "completed"
    codes = {t.code for t in r.model.diagnostics.review_triggers}
    assert "EXTENTS_IMPLAUSIBLE" in codes
    assert "DIMENSION_TEXT_DISAGREES" in codes
    assert r.model.requires_human_review


def test_override_conflicting_with_header_triggers_review(fx, tmp_path):
    r = run_pipeline(fx["office_in"], tmp_path, units="mm")
    codes = {t.code for t in r.model.diagnostics.review_triggers}
    assert "UNITS_OVERRIDE_CONFLICTS_WITH_HEADER" in codes and "EXTENTS_IMPLAUSIBLE" in codes


def test_dimension_text_verifies_scale(results):
    for unit in UNITS:
        checks = results[f"office_{unit}"].model.scale.dimension_checks
        assert len(checks) == 2 and all(c.agrees for c in checks), unit
