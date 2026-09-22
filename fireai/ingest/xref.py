"""External reference (XREF) resolution.

Safety rules:
* XREF paths stored in a drawing are NEVER opened as filesystem paths. They are
  matched by *file name* against files the user explicitly supplied with the
  job (case-insensitive; a .dxf may stand in for a referenced .dwg of the same
  name, recorded as such).
* Missing, circular, unit-ambiguous or unreadable XREFs are recorded and raise
  review triggers; their geometry is never invented.
* XREF geometry keeps its identity: every entity records the XREF file, its
  sha256 and its ORIGINAL handle inside that file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ezdxf import recover

from fireai.ingest.filetype import sanitize_filename, sniff_content

MAX_XREF_DEPTH = 8


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class XrefLoadError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class LoadedXref:
    doc: object
    file_name: str            # sanitized name of the supplied file
    sha256: str
    format: str
    matched_by: str           # "exact_name" | "same_stem_other_extension"
    converter: dict | None = None
    conversion_audit: dict | None = None


@dataclass
class XrefResolver:
    files: list[Path]
    converter: object | None = None     # DwgConverter or None
    work_dir: Path | None = None
    timeout_s: int = 180
    _cache: dict = field(default_factory=dict)

    def __post_init__(self):
        self._by_name = {p.name.lower(): p for p in self.files}
        self._by_stem: dict[str, list[Path]] = {}
        for p in self.files:
            self._by_stem.setdefault(p.stem.lower(), []).append(p)

    @staticmethod
    def referenced_name(xref_path: str) -> str:
        return re.split(r"[\\/]", xref_path or "")[-1]

    def find(self, xref_path: str) -> tuple[Path | None, str | None]:
        name = self.referenced_name(xref_path).lower()
        if name in self._by_name:
            return self._by_name[name], "exact_name"
        stem = Path(name).stem
        cands = [p for p in self._by_stem.get(stem, []) if p.suffix.lower() in (".dxf", ".dwg")]
        if len(cands) == 1:
            return cands[0], "same_stem_other_extension"
        return None, None

    def load(self, path: Path, matched_by: str) -> LoadedXref:
        key = str(path)
        if key in self._cache:
            return self._cache[key]
        with path.open("rb") as fh:
            kind = sniff_content(fh.read(4096))
        if kind is None:
            raise XrefLoadError("load_failed", f"{path.name} is not a DXF/DWG file")
        dxf_path, conv_info, audit = path, None, None
        if kind == "dwg":
            if self.converter is None:
                raise XrefLoadError("conversion_unavailable", f"{path.name} is a DWG and no DWG converter is available")
            out = (self.work_dir or path.parent) / "xref_convert" / _sha256(path)[:16]
            res = self.converter.convert(path, out, self.timeout_s)
            dxf_path, conv_info, audit = res.dxf_path, res.provenance(), res.audit
        try:
            doc, _auditor = recover.readfile(str(dxf_path))
        except Exception as exc:
            raise XrefLoadError("load_failed", f"{path.name} could not be read: {type(exc).__name__}: {exc}") from exc
        if kind == "dwg":
            audit = res.finalize_audit(doc)
        loaded = LoadedXref(doc, sanitize_filename(path.name), _sha256(path), kind, matched_by, conv_info, audit)
        self._cache[key] = loaded
        return loaded
