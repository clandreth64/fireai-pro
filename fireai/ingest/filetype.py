"""Upload validation: extension AND actual content must agree.

The file extension alone is never trusted. A PDF renamed to ``.dxf`` or a
text file renamed to ``.dwg`` is rejected before any parser sees it.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from fireai.errors import FailureCode, PipelineFailure

SUPPORTED_EXTENSIONS = {".dxf": "dxf", ".dwg": "dwg"}

BINARY_DXF_SENTINEL = b"AutoCAD Binary DXF\r\n\x1a\x00"
DWG_MAGIC = re.compile(rb"^AC(10\d\d|1\.\d\d|2\.\d\d)")

_KNOWN_OTHER = [
    (b"%PDF", "PDF document"),
    (b"\x89PNG", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"PK\x03\x04", "ZIP archive (or Office/IFC-zip document)"),
    (b"GIF8", "GIF image"),
    (b"ISO-10303-21", "STEP/IFC file"),
    (b"II*\x00", "TIFF image"),
    (b"MM\x00*", "TIFF image"),
]


def sanitize_filename(name: str | None, max_len: int = 120) -> str:
    """Return a display-safe basename. Never used to build filesystem paths."""
    raw = (name or "").replace("\\", "/").split("/")[-1]
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    raw = re.sub(r"[^A-Za-z0-9._ -]", "_", raw).strip(" .")
    raw = re.sub(r"\.{2,}", ".", raw)
    if not raw:
        raw = "upload"
    if len(raw) > max_len:
        stem, dot, ext = raw.rpartition(".")
        raw = (stem[: max_len - len(ext) - 1] + "." + ext) if dot and len(ext) <= 8 else raw[:max_len]
    return raw


def _describe_other(head: bytes) -> str | None:
    for magic, label in _KNOWN_OTHER:
        if head.startswith(magic):
            return label
    return None


def _looks_like_ascii_dxf(head: bytes) -> bool:
    text = head.lstrip(b"\xef\xbb\xbf").decode("latin-1", errors="replace")
    lines = [ln.strip() for ln in text.splitlines()][:40]
    i = 0
    # Skip leading comment pairs (group code 999).
    while i + 1 < len(lines) and lines[i] == "999":
        i += 2
    while i < len(lines) and lines[i] == "":
        i += 1
    return i + 1 < len(lines) and lines[i] == "0" and lines[i + 1].upper() == "SECTION"


def sniff_content(head: bytes) -> str | None:
    """Return 'dxf', 'dwg', or None based on file content only."""
    if head.startswith(BINARY_DXF_SENTINEL):
        return "dxf"
    if DWG_MAGIC.match(head):
        return "dwg"
    if _looks_like_ascii_dxf(head):
        return "dxf"
    return None


def validate_upload(path: Path, original_filename: str | None) -> str:
    """Validate extension and content of an uploaded file. Returns 'dxf' or 'dwg'."""
    display = sanitize_filename(original_filename)
    ext = Path(display).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise PipelineFailure(
            FailureCode.UNSUPPORTED_FORMAT,
            f"File extension '{ext or '(none)'}' is not supported. This milestone accepts .dxf and .dwg only.",
            {"filename": display, "supported": sorted(SUPPORTED_EXTENSIONS)},
        )
    declared = SUPPORTED_EXTENSIONS[ext]
    size = path.stat().st_size
    if size == 0:
        raise PipelineFailure(FailureCode.INVALID_DRAWING, "The uploaded file is empty (0 bytes).", {"filename": display})
    with path.open("rb") as fh:
        head = fh.read(4096)
    actual = sniff_content(head)
    if actual is None:
        other = _describe_other(head)
        raise PipelineFailure(
            FailureCode.INVALID_DRAWING,
            f"File content is not a valid {declared.upper()} drawing"
            + (f" (it looks like a {other})." if other else "."),
            {"filename": display, "declared_format": declared, "detected_content": other},
        )
    if actual != declared:
        raise PipelineFailure(
            FailureCode.INVALID_DRAWING,
            f"File extension says {declared.upper()} but the content is {actual.upper()}. Rename the file correctly and re-upload.",
            {"filename": display, "declared_format": declared, "detected_content": actual},
        )
    return actual
