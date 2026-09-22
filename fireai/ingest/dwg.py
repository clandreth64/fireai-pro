"""DWG -> DXF conversion behind a replaceable interface.

DWG is a closed binary format; FireAI never parses it directly and never sends
DWG bytes to an AI model. A converter produces a DXF, which then goes through
the same deterministic DXF pipeline as a native DXF upload.

If no converter is installed/configured the pipeline fails with
DWG_CONVERSION_UNAVAILABLE. There is no fallback.

Adapters:
* OdaFileConverter  — Open Design Alliance "ODA File Converter" (free, closed
  source, license terms apply). Headless Linux needs ``xvfb-run``.
* LibreDwgConverter — GNU LibreDWG ``dwg2dxf`` (GPL; coverage of newer DWG
  versions is less complete).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from fireai.config import Settings
from fireai.errors import FailureCode, PipelineFailure
from fireai.ingest.filetype import sniff_content


@dataclass
class ConversionResult:
    dxf_path: Path
    converter: str
    version: str | None
    command: list[str]
    log_tail: str = ""
    warnings: list[str] = field(default_factory=list)
    # Independent count of entities in the source DWG vs the produced DXF (converter-neutral shape):
    # {"status": "ok"|"unavailable"|"failed", "method", "source_counts", "output_counts", "lost", "note"}
    audit: dict | None = None

    def provenance(self) -> dict:
        return {"name": self.converter, "version": self.version, "command": self.command}


class DwgConverter(ABC):
    name: str = "abstract"

    @abstractmethod
    def available(self) -> bool: ...

    def version(self) -> str | None:
        return None

    @abstractmethod
    def _run(self, dwg_path: Path, out_dir: Path, timeout_s: int) -> ConversionResult: ...

    def convert(self, dwg_path: Path, out_dir: Path, timeout_s: int) -> ConversionResult:
        """Convert and verify the output is a real DXF. Raises PipelineFailure."""
        if not self.available():
            raise PipelineFailure(FailureCode.DWG_CONVERSION_UNAVAILABLE,
                                  f"DWG converter '{self.name}' is not available on this server.")
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = self._run(dwg_path, out_dir, timeout_s)
        except subprocess.TimeoutExpired:
            raise PipelineFailure(FailureCode.DWG_CONVERSION_FAILED,
                                  f"DWG conversion with '{self.name}' timed out after {timeout_s}s.") from None
        except PipelineFailure:
            raise
        except Exception as exc:  # converter crashed — fail, never continue
            raise PipelineFailure(FailureCode.DWG_CONVERSION_FAILED,
                                  f"DWG conversion with '{self.name}' failed: {type(exc).__name__}: {exc}") from exc
        dxf = result.dxf_path
        if not dxf.is_file() or dxf.stat().st_size == 0:
            raise PipelineFailure(FailureCode.DWG_CONVERSION_FAILED,
                                  f"Converter '{self.name}' produced no DXF output.",
                                  {"log_tail": result.log_tail[-2000:]})
        with dxf.open("rb") as fh:
            if sniff_content(fh.read(4096)) != "dxf":
                raise PipelineFailure(FailureCode.DWG_CONVERSION_FAILED,
                                      f"Converter '{self.name}' output is not a valid DXF.",
                                      {"log_tail": result.log_tail[-2000:]})
        try:
            result.audit = self.audit(dwg_path, dxf, out_dir, timeout_s)
        except Exception as exc:  # an audit failure is reported, never ignored
            result.audit = {"status": "failed", "note": f"{type(exc).__name__}: {exc}"}
        return result

    def audit(self, dwg_path: Path, dxf_path: Path, work_dir: Path, timeout_s: int) -> dict:
        """Count entities in the source DWG independently of the conversion and compare
        with the produced DXF. Adapters that cannot do this return status 'unavailable'."""
        return {"status": "unavailable", "note": f"converter '{self.name}' provides no independent entity count"}


def _run_cmd(cmd: list[str], timeout_s: int, cwd: Path | None = None) -> subprocess.CompletedProcess:
    # Argument list, no shell: file names can never be interpreted as commands.
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=cwd, check=False)


class LibreDwgConverter(DwgConverter):
    name = "libredwg"

    def __init__(self, executable: str | None = None):
        self.executable = executable or shutil.which("dwg2dxf")

    def available(self) -> bool:
        return bool(self.executable) and Path(self.executable).exists()

    def version(self) -> str | None:
        try:
            out = _run_cmd([self.executable, "--version"], 15)
            return (out.stdout or out.stderr).strip().splitlines()[0][:120]
        except Exception:
            return None

    def _run(self, dwg_path: Path, out_dir: Path, timeout_s: int) -> ConversionResult:
        out = out_dir / (dwg_path.stem + ".dxf")
        cmd = [self.executable, "-y", "-o", str(out), str(dwg_path)]
        proc = _run_cmd(cmd, timeout_s)
        log = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise PipelineFailure(FailureCode.DWG_CONVERSION_FAILED,
                                  f"dwg2dxf exited with code {proc.returncode}.", {"log_tail": log[-2000:]})
        return ConversionResult(out, self.name, self.version(), cmd, log)

    def audit(self, dwg_path: Path, dxf_path: Path, work_dir: Path, timeout_s: int) -> dict:
        dwgread = Path(self.executable).with_name("dwgread")
        if not dwgread.exists():
            return {"status": "unavailable", "note": "dwgread not installed next to dwg2dxf"}
        out = work_dir / "dwgread.json"
        proc = _run_cmd([str(dwgread), "-O", "JSON", "-o", str(out), str(dwg_path)], timeout_s)
        if not out.is_file():
            return {"status": "failed", "note": f"dwgread exited {proc.returncode} without output"}
        data = json.loads(out.read_text(encoding="utf-8", errors="replace"))
        src = Counter()
        for o in data.get("OBJECTS", []):
            t = normalize_dwg_entity_type(o.get("entity"))
            if t:
                src[t] += 1
        return compare_entity_counts(src, count_dxf_entities(dxf_path), "libredwg dwgread JSON object census")


# LibreDWG object names that are structural records, not drawable entities in DXF terms.
_DWG_STRUCTURAL = {"BLOCK", "ENDBLK", "SEQEND", "VERTEX_2D", "VERTEX_3D", "VERTEX_MESH", "VERTEX_PFACE",
                   "VERTEX_PFACE_FACE"}
_DWG_TO_DXF = {"POLYLINE_2D": "POLYLINE", "POLYLINE_3D": "POLYLINE", "POLYLINE_PFACE": "POLYLINE",
               "POLYLINE_MESH": "POLYLINE", "_3DFACE": "3DFACE", "_3DSOLID": "3DSOLID", "PROXY_ENTITY": "ACAD_PROXY_ENTITY",
               "TABLE": "ACAD_TABLE", "MINSERT": "INSERT", "LARGE_RADIAL_DIMENSION": "LARGE_RADIAL_DIMENSION"}


def normalize_dwg_entity_type(name: str | None) -> str | None:
    if not name or name in _DWG_STRUCTURAL:
        return None
    if name.startswith("DIMENSION_"):
        return "DIMENSION"
    return _DWG_TO_DXF.get(name, name)


def count_dxf_entities(dxf_path: Path) -> Counter:
    """Entity census of a DXF across ALL block definitions and layouts (incl. ATTRIBs)."""
    from ezdxf import recover
    doc, _ = recover.readfile(str(dxf_path))
    c = Counter()
    for block in doc.blocks:
        for e in block:
            c[e.dxftype()] += 1
            if e.dxftype() == "INSERT":
                c["ATTRIB"] += len(e.attribs)
    return c


def compare_entity_counts(source: Counter, output: Counter, method: str) -> dict:
    lost = {t: n - output.get(t, 0) for t, n in source.items() if n > output.get(t, 0)}
    return {"status": "ok", "method": method, "source_counts": dict(source), "output_counts": dict(output),
            "lost": dict(sorted(lost.items())),
            "note": "entity types present in the DWG but missing from the converted DXF" if lost else "no losses detected"}


class OdaFileConverter(DwgConverter):
    name = "oda"

    def __init__(self, executable: str | None = None, use_xvfb: bool | None = None):
        self.executable = executable or shutil.which("ODAFileConverter")
        self.xvfb = shutil.which("xvfb-run") if use_xvfb in (None, True) else None

    def available(self) -> bool:
        return bool(self.executable) and Path(self.executable).exists()

    def _run(self, dwg_path: Path, out_dir: Path, timeout_s: int) -> ConversionResult:
        # ODA converts whole folders: isolate the single input file.
        in_dir = out_dir / "oda_in"
        in_dir.mkdir(parents=True, exist_ok=True)
        staged = in_dir / "source.dwg"
        shutil.copyfile(dwg_path, staged)
        cmd = [self.executable, str(in_dir), str(out_dir), "ACAD2018", "DXF", "0", "1", "*.DWG"]
        if self.xvfb:
            cmd = [self.xvfb, "-a"] + cmd
        proc = _run_cmd(cmd, timeout_s)
        log = (proc.stdout or "") + (proc.stderr or "")
        err_files = list(out_dir.glob("*.err"))
        if err_files:
            log += "\n" + "\n".join(p.read_text(errors="replace") for p in err_files)
        out = out_dir / "source.dxf"
        if proc.returncode != 0 or not out.exists():
            raise PipelineFailure(FailureCode.DWG_CONVERSION_FAILED,
                                  f"ODA File Converter failed (exit {proc.returncode}).", {"log_tail": log[-2000:]})
        return ConversionResult(out, self.name, None, cmd, log)


def select_converter(settings: Settings) -> DwgConverter | None:
    """Return the configured converter, or None if DWG conversion is unavailable."""
    choice = settings.dwg_converter
    if choice == "none":
        return None
    candidates: list[DwgConverter] = []
    if choice in ("auto", "oda"):
        candidates.append(OdaFileConverter(settings.oda_converter_path))
    if choice in ("auto", "libredwg"):
        candidates.append(LibreDwgConverter(settings.libredwg_path))
    for c in candidates:
        if c.available():
            return c
    return None
