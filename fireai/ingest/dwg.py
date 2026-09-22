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

import shutil
import subprocess
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
    # Independent audit of the source DWG vs the produced DXF (converter-neutral, JSON-serializable):
    # {"status": "ok"|"unavailable"|"failed", "method", "level", "source_counts", "output_counts", "lost",
    #  "lost_by_significance", "significance", ...} -- see fireai/ingest/dwg_audit.py
    audit: dict | None = None
    # Parsed source census kept until the converted DXF is loaded (not serialized).
    census: object | None = field(default=None, repr=False)
    audit_method: str = ""

    def finalize_audit(self, doc) -> dict:
        """Compare the source census with the DXF document the pipeline loaded."""
        from fireai.ingest.dwg_audit import compare, dxf_census
        if self.census is None:
            if self.audit is None:
                self.audit = {"status": "unavailable", "note": "no independent source census"}
            return self.audit
        try:
            self.audit = compare(self.census, dxf_census(doc), self.audit_method)
        except Exception as exc:  # reported, never ignored
            self.audit = {"status": "failed", "note": f"{type(exc).__name__}: {exc}"}
        self.census = None
        return self.audit

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
            census, note = self.source_census(dwg_path, out_dir, timeout_s)
            result.census = census
            if census is None:
                result.audit = {"status": "unavailable" if note.startswith("unavailable") else "failed", "note": note}
        except Exception as exc:  # an audit failure is reported, never ignored
            result.audit = {"status": "failed", "note": f"{type(exc).__name__}: {exc}"}
        return result

    def source_census(self, dwg_path: Path, work_dir: Path, timeout_s: int):
        """Independent parse of the source DWG (see dwg_audit). Returns (census|None, note).
        Adapters that cannot do this return (None, 'unavailable: ...')."""
        return None, f"unavailable: converter '{self.name}' provides no independent source census"


def _limit_memory(mb: int | None):
    if not mb:
        return None

    def fn():  # runs in the child before exec: cap its address space
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (mb * 1024 * 1024, mb * 1024 * 1024))
    return fn


def _run_cmd(cmd: list[str], timeout_s: int, cwd: Path | None = None,
             memory_mb: int | None = None) -> subprocess.CompletedProcess:
    # Argument list, no shell: file names can never be interpreted as commands.
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=cwd, check=False,
                          preexec_fn=_limit_memory(memory_mb))


class LibreDwgConverter(DwgConverter):
    name = "libredwg"

    def __init__(self, executable: str | None = None, memory_mb: int | None = None):
        self.executable = executable or shutil.which("dwg2dxf")
        self.memory_mb = memory_mb

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
        proc = _run_cmd(cmd, timeout_s, memory_mb=self.memory_mb)
        log = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            hint = (f" The converter may have exceeded its memory limit ({self.memory_mb} MB)."
                    if self.memory_mb and proc.returncode < 0 else "")
            raise PipelineFailure(FailureCode.DWG_CONVERSION_FAILED,
                                  f"dwg2dxf exited with code {proc.returncode}.{hint}", {"log_tail": log[-2000:]})
        return ConversionResult(out, self.name, self.version(), cmd, log,
                                audit_method="handle-level: libredwg dwgread JSON (streamed) vs converted DXF")

    def source_census(self, dwg_path: Path, work_dir: Path, timeout_s: int):
        from fireai.ingest.dwg_audit import dwgread_census
        dwgread = Path(self.executable).with_name("dwgread")
        if not dwgread.exists():
            return None, "unavailable: dwgread not installed next to dwg2dxf"
        return dwgread_census(dwgread, dwg_path, work_dir, timeout_s,
                              lambda cmd, t: _run_cmd(cmd, t, memory_mb=self.memory_mb))


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
        candidates.append(LibreDwgConverter(settings.libredwg_path, settings.dwg_max_memory_mb))
    for c in candidates:
        if c.available():
            return c
    return None
