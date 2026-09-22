"""Fail-closed behaviour: bad inputs fail with explicit codes and never look successful."""

import json

import pytest

from conftest import run_pipeline
from fireai import pipeline as P

CASES = [
    ("fake_dwg", "DWG_CONVERSION_UNAVAILABLE"),
    ("empty", "EMPTY_DRAWING"),
    ("renamed_pdf", "INVALID_DRAWING"),
    ("txt", "UNSUPPORTED_FORMAT"),
    ("pdf", "UNSUPPORTED_FORMAT"),
    ("zero", "INVALID_DRAWING"),
    ("dxf_as_dwg", "INVALID_DRAWING"),
    ("hidden_only", "GEOMETRY_EXTRACTION_FAILED"),
]


def _assert_not_successful(r):
    assert r.processing_status == "failed"
    rep = r.report
    assert rep["requires_human_review"] is True
    assert rep["geometry_is_synthetic"] is False
    assert rep["ready_for_design"] is False
    assert "overlay_png" not in r.deliverables
    text = json.dumps(rep).lower()
    assert '"completed"' not in text and "compliant" not in text


@pytest.mark.parametrize("key,code", CASES)
def test_bad_inputs_fail_explicitly(fx, tmp_path, key, code):
    r = run_pipeline(fx[key], tmp_path)
    assert r.failure["code"] == code, r.failure
    _assert_not_successful(r)


def test_corrupted_dxf_fails(fx, tmp_path):
    r = run_pipeline(fx["corrupted"], tmp_path)
    assert r.failure["code"] in ("INVALID_DRAWING", "GEOMETRY_EXTRACTION_FAILED"), r.failure
    _assert_not_successful(r)


def test_fake_dwg_never_reaches_a_model(fx, tmp_path):
    r = run_pipeline(fx["fake_dwg"], tmp_path)
    assert r.model is None
    assert r.failure["code"] == "DWG_CONVERSION_UNAVAILABLE"
    assert "DXF" in r.failure["message"]


def test_internal_error_is_a_failure(fx, tmp_path, monkeypatch):
    def boom(self):
        raise RuntimeError("simulated interpreter crash")
    monkeypatch.setattr(P.Interpreter, "run", boom)
    r = run_pipeline(fx["office_ft"], tmp_path)
    assert r.failure["code"] == "INTERNAL_ERROR"
    _assert_not_successful(r)


def test_overlay_failure_is_a_failure(fx, tmp_path, monkeypatch):
    import fireai.render.overlay as O

    def boom(*a, **k):
        raise RuntimeError("renderer crashed")
    monkeypatch.setattr(O, "render_overlay", boom)
    r = run_pipeline(fx["office_ft"], tmp_path)
    assert r.failure["code"] == "OVERLAY_GENERATION_FAILED"
    _assert_not_successful(r)


def test_entity_limit_fails_closed(fx, tmp_path):
    r = run_pipeline(fx["office_ft"], tmp_path, max_entities=10)
    assert r.failure["code"] == "GEOMETRY_EXTRACTION_FAILED"
    _assert_not_successful(r)


def test_fake_dwg_with_real_converter_fails_conversion(fx, tmp_path):
    """With LibreDWG installed, a garbage .dwg must fail in conversion — never produce a model."""
    import shutil
    if not shutil.which("dwg2dxf"):
        pytest.skip("dwg2dxf not installed")
    r = run_pipeline(fx["fake_dwg"], tmp_path, dwg_converter="libredwg")
    assert r.failure["code"] == "DWG_CONVERSION_FAILED", r.failure
    assert r.model is None
    _assert_not_successful(r)
