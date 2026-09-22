"""DWG conversion layer: interface, adapters, and failure modes."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from conftest import make_settings, run_pipeline
from fireai.errors import FailureCode, PipelineFailure
from fireai.ingest.dwg import ConversionResult, DwgConverter, LibreDwgConverter, OdaFileConverter, select_converter


class CopyConverter(DwgConverter):
    """Test double: 'converts' by copying a known DXF."""
    name = "test-copy"

    def __init__(self, dxf: Path | None, fail_with: Exception | None = None, garbage: bool = False):
        self.dxf, self.fail_with, self.garbage = dxf, fail_with, garbage

    def available(self):
        return True

    def _run(self, dwg_path, out_dir, timeout_s):
        if self.fail_with:
            raise self.fail_with
        out = out_dir / "converted.dxf"
        if self.garbage:
            out.write_bytes(b"not a dxf at all")
        else:
            shutil.copyfile(self.dxf, out)
        return ConversionResult(out, self.name, "1.0-test", ["copy", str(dwg_path)])


def test_no_converter_configured():
    s = make_settings(Path("/tmp/x"), dwg_converter="none")
    assert select_converter(s) is None


def test_auto_selection_does_not_invent_a_converter(tmp_path):
    s = make_settings(tmp_path, dwg_converter="auto", oda_converter_path=str(tmp_path / "missing"),
                      libredwg_path=str(tmp_path / "missing2"))
    conv = select_converter(s)
    if shutil.which("ODAFileConverter") or shutil.which("dwg2dxf"):
        pytest.skip("a real converter is installed")
    assert conv is None


def test_successful_conversion_goes_through_dxf_pipeline(fx, tmp_path):
    r = run_pipeline(fx["fake_dwg"], tmp_path, converter=CopyConverter(fx["office_ft"]))
    assert r.processing_status == "completed"
    src = r.model.source
    assert src.format == "dwg" and src.converted_from_dwg and src.converter["name"] == "test-copy"
    assert any(w.code == "DWG_CONVERTED" for w in r.model.diagnostics.warnings)
    assert len(r.model.elements_of("room")) == 3


@pytest.mark.parametrize("conv", [
    CopyConverter(None, fail_with=subprocess.TimeoutExpired("x", 1)),
    CopyConverter(None, fail_with=OSError("converter crashed")),
    CopyConverter(None, garbage=True),
])
def test_failed_conversion(fx, tmp_path, conv):
    r = run_pipeline(fx["fake_dwg"], tmp_path, converter=conv)
    assert r.processing_status == "failed"
    assert r.failure["code"] == "DWG_CONVERSION_FAILED"
    assert r.model is None


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.mark.skipif(os.name == "nt", reason="shell stub requires POSIX")
def test_libredwg_adapter_runs_subprocess(fx, tmp_path):
    good = _script(tmp_path / "dwg2dxf_ok", f'while [ "$1" != "-o" ]; do shift; done; cp "{fx["office_ft"]}" "$2"\n')
    res = LibreDwgConverter(str(good)).convert(fx["fake_dwg"], tmp_path / "out1", 30)
    assert res.dxf_path.exists() and res.command[0] == str(good) and "-o" in res.command
    bad = _script(tmp_path / "dwg2dxf_bad", "echo 'ERROR: unsupported version' >&2; exit 1\n")
    with pytest.raises(PipelineFailure) as e:
        LibreDwgConverter(str(bad)).convert(fx["fake_dwg"], tmp_path / "out2", 30)
    assert e.value.code == FailureCode.DWG_CONVERSION_FAILED
    assert "unsupported version" in e.value.details["log_tail"]


@pytest.mark.skipif(os.name == "nt", reason="shell stub requires POSIX")
def test_oda_adapter_command_and_failure(fx, tmp_path):
    ok = _script(tmp_path / "oda_ok", f'cp "{fx["office_ft"]}" "$2/source.dxf"\n')
    res = OdaFileConverter(str(ok), use_xvfb=False).convert(fx["fake_dwg"], tmp_path / "o1", 30)
    assert res.command[1:] == [str(tmp_path / "o1" / "oda_in"), str(tmp_path / "o1"), "ACAD2018", "DXF", "0", "1", "*.DWG"]
    silent = _script(tmp_path / "oda_silent", "exit 0\n")   # exits 0 but writes nothing
    with pytest.raises(PipelineFailure) as e:
        OdaFileConverter(str(silent), use_xvfb=False).convert(fx["fake_dwg"], tmp_path / "o2", 30)
    assert e.value.code == FailureCode.DWG_CONVERSION_FAILED


@pytest.mark.skipif(not (shutil.which("dwg2dxf") or shutil.which("ODAFileConverter")),
                    reason="no real DWG converter installed in this environment")
def test_real_dwg_conversion(tmp_path):
    real = sorted((Path(__file__).parent / "golden" / "drawings").glob("*.dwg"))
    real = [p for p in real if not p.name.startswith("fake")]
    if not real:
        pytest.skip("no real DWG fixtures present")
    for p in real:
        r = run_pipeline(p, tmp_path, dwg_converter="auto")
        assert r.processing_status in ("completed", "needs_human_input"), (p, r.failure)
