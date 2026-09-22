"""Guards for the Milestone 1.6 performance changes (behaviour must not change)."""

from __future__ import annotations

import ezdxf

import fireai.render.overlay as O
from conftest import run_pipeline
from fixtures import builders as B


def test_renderers_reuse_the_loaded_document(fx, tmp_path, monkeypatch):
    calls = []
    real = O.recover.readfile
    monkeypatch.setattr(O.recover, "readfile", lambda *a, **k: calls.append(a) or real(*a, **k))
    r = run_pipeline(fx["office_ft"], tmp_path)
    assert r.processing_status == "completed"
    assert len(calls) == 1          # the pipeline's own load_dxf; renderers add no re-parse (M1.5: 4 parses)
    for d in ("source_png", "overlay_png", "overlay_svg", "overlay_dxf"):
        assert r.path(d).stat().st_size > 0


def test_overlay_dxf_still_contains_source_and_verification_layers(fx, tmp_path):
    r = run_pipeline(fx["office_ft"], tmp_path)
    doc = ezdxf.readfile(r.path("overlay_dxf"))
    names = {layer.dxf.name for layer in doc.layers}
    assert {"FAI-WALL", "FAI-ROOM"} <= names
    src = ezdxf.readfile(fx["office_ft"])
    assert len(list(src.modelspace())) < len(list(doc.modelspace()))


def test_overlay_underlay_draws_hatch_outlines_only(tmp_path, monkeypatch):
    seen = []
    real = O.Configuration
    monkeypatch.setattr(O, "Configuration", lambda **kw: seen.append(kw.get("hatch_policy")) or real(**kw))
    run_pipeline(B.make_office(tmp_path / "o.dxf", "ft"), tmp_path)
    assert seen == [O.HatchPolicy.NORMAL, O.HatchPolicy.SHOW_OUTLINE]   # source.png full, overlay outlines
