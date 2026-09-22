from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from fireai.config import Settings
from fireai.pipeline import _USE_CONFIGURED, understand_drawing
from fixtures import builders as B

_counter = itertools.count()


def make_settings(tmp: Path, **kw) -> Settings:
    base = dict(data_dir=tmp, dwg_converter="none", max_upload_bytes=20 * 1024 * 1024,
                cors_origins=(), api_token=None)
    base.update(kw)
    return Settings(**base)


@pytest.fixture(scope="session")
def fx(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("fixtures")
    out = {}
    for unit in ("in", "ft", "mm", "cm", "m"):
        out[f"office_{unit}"] = B.make_office(d / f"office_{unit}.dxf", unit)
    out["warehouse"] = B.make_warehouse(d / "warehouse.dxf")
    out["simple_rect"] = B.make_simple_rect(d / "simple_rect.dxf")
    out["empty"] = B.make_empty(d / "empty.dxf")
    out["missing_units"] = B.make_missing_units(d / "missing_units.dxf")
    out["unitless"] = B.make_units_code(d / "unitless.dxf", 0)
    out["miles"] = B.make_units_code(d / "miles.dxf", 3)
    out["bogus_units"] = B.make_units_code(d / "bogus_units.dxf", 99)
    out["mislabeled_units"] = B.make_office(d / "mislabeled.dxf", "in", insunits=4)  # drawn in inches, header says mm
    out["corrupted"] = B.make_corrupted(d / "corrupted.dxf")
    out["fake_dwg"] = B.make_fake_dwg(d / "fake.dwg")
    out["renamed_pdf"] = B.make_renamed_pdf(d / "renamed_pdf.dxf")
    out["hidden_only"] = B.make_hidden_only(d / "hidden_only.dxf")
    (d / "notes.txt").write_text("not a drawing")
    out["txt"] = d / "notes.txt"
    out["pdf"] = B.make_renamed_pdf(d / "plan.pdf")
    zero = d / "zero.dxf"; zero.write_bytes(b""); out["zero"] = zero
    dxf_as_dwg = d / "really_dxf.dwg"; dxf_as_dwg.write_bytes(out["office_ft"].read_bytes()); out["dxf_as_dwg"] = dxf_as_dwg
    return out


def run_pipeline(path: Path, tmp: Path, name: str | None = None, units: str | None = None,
                 converter=_USE_CONFIGURED, **settings_kw):
    n = next(_counter)
    s = make_settings(tmp / f"data{n}", **settings_kw)
    return understand_drawing(path, name or path.name, tmp / f"work{n}", tmp / f"out{n}", units, s, converter)


@pytest.fixture(scope="session")
def results(fx, tmp_path_factory):
    """Pipeline results for the main fixtures (computed once)."""
    tmp = tmp_path_factory.mktemp("runs")
    keys = ["office_in", "office_ft", "office_mm", "office_cm", "office_m", "warehouse", "simple_rect"]
    return {k: run_pipeline(fx[k], tmp) for k in keys} | {"_tmp": tmp}
