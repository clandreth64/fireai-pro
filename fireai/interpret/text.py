"""Text parsing helpers: room labels, stated areas, dimension strings, title blocks."""

from __future__ import annotations

import re

ROOM_NUMBER = re.compile(r"^(?:RM\.?\s*|ROOM\s*)?([A-Z]?\d{1,4}[A-Z]?)$", re.I)
AREA_TEXT = re.compile(
    r"^([\d,]+(?:\.\d+)?)\s*(SF|S\.F\.|SQ\.?\s*FT\.?|SQFT|FT2|FT²|M2|M²|SQ\.?\s*M\.?|SQM)$", re.I)
SQFT_PER_M2 = 10.763910416709722

# Imperial length strings: 25'-0", 25' 6", 25'-6 1/2", 6", 8'
_IMPERIAL = re.compile(
    r"""^\s*(?:(?P<ft>\d+(?:\.\d+)?)\s*')?\s*-?\s*
         (?:(?P<in>\d+(?:\.\d+)?)?\s*(?:(?P<num>\d+)\s*/\s*(?P<den>\d+))?\s*"?)?\s*$""", re.X)
_PLAIN_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?\s*$", re.I)


# Rule T-FINISH-NOTE: a label line made ONLY of finish/annotation vocabulary is a note,
# not a room name ("HRWD FLOOR", "TILE FLOOR", "9'-0\" CLG"). "SALES FLOOR" stays a name
# because SALES is not finish vocabulary.
FINISH_VOCAB = {
    "HRWD", "HARDWOOD", "WOOD", "TILE", "CERAMIC", "PORCELAIN", "VCT", "LVT", "VINYL", "LINO", "LINOLEUM",
    "CARPET", "CPT", "CONC", "CONCRETE", "EPOXY", "SEALED", "STAINED", "POLISHED", "RUBBER", "BASE",
    "FLOOR", "FLR", "FLOORING", "FIN", "FINISH", "CLG", "CEILING", "ACT", "GYP", "GWB", "EXPOSED",
    "GFI", "GFCI", "TYP", "&", "/", "-", "AND", "W", "ON",
}
_HEIGHT_TOKEN = re.compile(r"^\d+'?(-?\d+(\s*\d+/\d+)?\"?)?$|^A\.?F\.?F\.?$")


def is_finish_note(line: str) -> bool:
    toks = [t for t in re.split(r"[\s,.:]+", line.upper()) if t]
    return bool(toks) and all(t in FINISH_VOCAB or _HEIGHT_TOKEN.match(t) or t.isdigit() for t in toks) \
        and any(t in FINISH_VOCAB for t in toks)


def split_lines(text: str) -> list[str]:
    return [ln.strip() for ln in re.split(r"[\r\n]+|\\P", text or "") if ln.strip()]


def parse_stated_area_sf(line: str) -> float | None:
    m = AREA_TEXT.match(line.strip())
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    unit = m.group(2).upper().replace(" ", "")
    return value * SQFT_PER_M2 if unit.startswith(("M2", "M²", "SQM", "SQ.M")) else value


def parse_room_label(lines: list[str]) -> dict:
    """Split label lines into name / number / stated area. Returns found parts only."""
    out: dict = {"name_parts": [], "number": None, "stated_area_sf": None, "unparsed": []}
    for ln in lines:
        area = parse_stated_area_sf(ln)
        if area is not None:
            out["stated_area_sf"] = area
            continue
        m = ROOM_NUMBER.match(ln)
        if m:
            out["number"] = m.group(1).upper()
            continue
        # "OFFICE 101" -> name + trailing number
        parts = ln.rsplit(" ", 1)
        if len(parts) == 2 and ROOM_NUMBER.match(parts[1]) and re.search(r"[A-Za-z]", parts[0]):
            out["name_parts"].append(parts[0].strip())
            out["number"] = out["number"] or ROOM_NUMBER.match(parts[1]).group(1).upper()
            continue
        if re.search(r"[A-Za-z]", ln) and len(ln) <= 40:
            out["name_parts"].append(ln)
        else:
            out["unparsed"].append(ln)
    return out


def parse_length_text_ft(text: str, drawing_units_ft: float | None) -> float | None:
    """Parse a dimension override string to feet. Plain numbers are read in the
    drawing's own units (only meaningful when units are resolved)."""
    t = (text or "").strip()
    if not t or t == "<>" or "<>" in t:
        return None
    t = t.replace("\\P", " ").replace("''", '"')
    if "'" in t or '"' in t:
        m = _IMPERIAL.match(t)
        if not m or not any(m.group(g) for g in ("ft", "in", "num")):
            return None
        ft = float(m.group("ft") or 0)
        inch = float(m.group("in") or 0)
        if m.group("num") and m.group("den") and float(m.group("den")) != 0:
            inch += float(m.group("num")) / float(m.group("den"))
        return ft + inch / 12.0
    m = _PLAIN_NUMBER.match(t)
    if m:
        value = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit:
            return value * {"mm": 1 / 304.8, "cm": 10 / 304.8, "m": 1000 / 304.8}[unit]
        return value * drawing_units_ft if drawing_units_ft else None
    return None


# ── title block ──────────────────────────────────────────────────────────────

_TB_PATTERNS = {
    "sheet_number": [r"SHEET\s*(?:NO\.?|NUMBER|#)?\s*[:#]?\s*([A-Z]{1,3}[-.]?\d{1,3}(?:\.\d{1,2})?)\b",
                     r"\bDWG\.?\s*(?:NO\.?)?\s*[:#]?\s*([A-Z]{1,3}[-.]?\d{1,3}(?:\.\d{1,2})?)\b"],
    "scale_text": [r"SCALE\s*[:=]?\s*([^\n]{3,30})"],
    "date": [r"DATE\s*[:=]?\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})"],
    "drawing_title": [r"(?:SHEET|DRAWING)\s*TITLE\s*[:=]?\s*([^\n]{3,60})"],
}


def parse_title_block(text: str) -> dict:
    """Regex extraction of title-block fields.

    Reuses the legacy v1 ``TextExtractor`` patterns for project name / address /
    owner (fireai_project_extractor.py) — but its "Unknown Project" fallback is
    treated as NOT FOUND. Every value is a regex match on drawing text, not a
    verified fact; the caller marks it for human verification.
    """
    fields: dict[str, dict] = {}
    try:
        from fireai_project_extractor import TextExtractor  # legacy, pure-regex part only
        te = TextExtractor(text)
        for key, fn in (("project_name", te.extract_project_name), ("address", te.extract_address),
                        ("owner", te.extract_owner)):
            val = (fn() or "").strip()
            if val and val != "Unknown Project":
                fields[key] = {"value": val, "method": "regex (legacy TextExtractor)"}
    except Exception as exc:  # legacy module unavailable -> fields simply not found
        fields["_legacy_extractor_error"] = {"value": f"{type(exc).__name__}: {exc}", "method": "n/a"}
    for key, pats in _TB_PATTERNS.items():
        for p in pats:
            m = re.search(p, text, re.I)
            if m:
                fields[key] = {"value": m.group(1).strip(), "method": "regex"}
                break
    return fields
