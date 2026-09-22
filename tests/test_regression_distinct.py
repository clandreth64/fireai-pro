"""CRITICAL REGRESSION: different drawings must produce materially different results.

v1 produced essentially identical synthetic buildings for any input (even a
garbage .dwg). This test fails if that behaviour ever returns.
"""

import hashlib

import numpy as np
from matplotlib import image as mpimg


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_two_different_drawings_produce_different_models(results):
    a, b = results["office_ft"], results["warehouse"]
    ma, mb = a.model, b.model
    assert ma.source.sha256 != mb.source.sha256
    rooms_a = sorted((e.label, round(e.properties["area_sf"], 1)) for e in ma.elements_of("room"))
    rooms_b = sorted((e.label, round(e.properties["area_sf"], 1)) for e in mb.elements_of("room"))
    assert rooms_a != rooms_b
    assert not set(r[0] for r in rooms_a) & set(r[0] for r in rooms_b)
    assert (round(ma.bounds_normalized.width, 3), round(ma.bounds_normalized.height, 3)) != \
           (round(mb.bounds_normalized.width, 3), round(mb.bounds_normalized.height, 3))
    total_a = sum(e.properties["area_sf"] for e in ma.elements_of("room"))
    total_b = sum(e.properties["area_sf"] for e in mb.elements_of("room"))
    assert abs(total_a - total_b) / max(total_a, total_b) > 0.5
    ca = {c: len(ma.elements_of(c)) for c in ("wall", "door", "room", "column")}
    cb = {c: len(mb.elements_of(c)) for c in ("wall", "door", "room", "column")}
    assert ca != cb


def test_two_different_drawings_produce_different_overlays(results):
    paths = [results[k].path("overlay_png") for k in ("office_ft", "warehouse")]
    assert _sha(paths[0]) != _sha(paths[1])
    ia, ib = mpimg.imread(paths[0]), mpimg.imread(paths[1])
    h, w = min(ia.shape[0], ib.shape[0]), min(ia.shape[1], ib.shape[1])
    diff = np.abs(ia[:h, :w, :3] - ib[:h, :w, :3]).sum(axis=2) > 0.1
    assert diff.mean() > 0.02, f"only {diff.mean():.2%} of pixels differ"


def test_same_building_in_different_units_is_the_same_building(results):
    """The converse: equivalent drawings must NOT differ materially."""
    ra = sorted((e.label, round(e.properties["area_sf"], 3)) for e in results["office_in"].model.elements_of("room"))
    rb = sorted((e.label, round(e.properties["area_sf"], 3)) for e in results["office_m"].model.elements_of("room"))
    assert ra == rb
