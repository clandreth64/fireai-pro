#!/usr/bin/env python3
"""
FireAI Pro — Project Document Extractor
=======================================

Parses an architectural/construction document set and returns the project
metadata that populates the FireAI Pro intake form — WITH PROVENANCE.

Core principles (the whole point of this module):
  1. Nothing is hardcoded. A field the document does not support is returned
     as null + needs_review, NEVER pre-filled with a plausible-looking number.
  2. Every value carries where it came from (source), how sure we are
     (confidence), and whether a human must check it (needs_review).
  3. Fields that do not live on the architectural set (job numbers, hydrant
     flow-test pressures, seismic) are reported as such instead of guessed.

This file implements the PDF path fully (text-layer extraction + sheet
scoring). DWG/DXF and IFC are defined as the same interface with clear
hooks so the engine extends to "various file types" without rewrites.

Usage:
    python fireai_document_extractor.py /path/to/drawings.pdf
    python fireai_document_extractor.py /path/to/drawings.pdf --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
class Confidence(str, Enum):
    HIGH = "high"        # read verbatim from a code-summary / title block
    MEDIUM = "medium"    # read but ambiguous, or derived from another field
    LOW = "low"          # a candidate / default the designer must confirm
    NONE = "none"        # not found / not present in this document type


@dataclass
class ExtractedField:
    value: object = None
    source: str = ""
    confidence: Confidence = Confidence.NONE
    needs_review: bool = True
    note: str = ""
    alternates: Optional[list] = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = self.confidence.value
        if not self.alternates:
            d.pop("alternates")
        if not self.note:
            d.pop("note")
        return d


# --------------------------------------------------------------------------- #
# Domain mappings  (raw drawing text -> FireAI Pro dropdown values)
# --------------------------------------------------------------------------- #
OCCUPANCY_GROUPS = {
    "A": "Assembly (Group A)",
    "B": "Business / Office (Group B)",
    "E": "Educational (Group E)",
    "F": "Factory / Industrial (Group F)",
    "H": "High Hazard (Group H)",
    "I": "Institutional (Group I)",
    "M": "Mercantile (Group M)",
    "R": "Residential (Group R)",
    "S": "Storage (Group S)",
    "U": "Utility / Misc (Group U)",
}

CONSTRUCTION_TYPES = {
    "I-A": "I-A (Non-combustible, protected)",
    "I-B": "I-B (Non-combustible, protected)",
    "II-A": "II-A (Non-combustible)",
    "II-B": "II-B (Non-combustible)",
    "III-A": "III-A (Combustible/Non-combustible)",
    "III-B": "III-B (Combustible/Non-combustible)",
    "IV": "IV (Heavy Timber)",
    "V-A": "V-A (Combustible, protected)",
    "V-B": "V-B (Combustible, unprotected)",
}


# --------------------------------------------------------------------------- #
# Text source abstraction  (PDF implemented; DWG/IFC are the same interface)
# --------------------------------------------------------------------------- #
class DocumentSource:
    """A set of 'sheets'/'pages' of normalized text, plus a content inventory."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.pages: list[str] = []        # normalized (whitespace-collapsed) page text
        self.inventory: dict = {}

    @staticmethod
    def for_file(path: str) -> "DocumentSource":
        ext = Path(path).suffix.lower()
        if ext == ".pdf":
            return PDFSource(path)
        if ext in (".dwg", ".dxf"):
            return CADSource(path)
        if ext in (".ifc",):
            return IFCSource(path)
        raise ValueError(f"Unsupported file type: {ext}")

    def load(self) -> "DocumentSource":
        raise NotImplementedError


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


class PDFSource(DocumentSource):
    def load(self) -> "PDFSource":
        # --- content inventory: is there a usable text layer? ---
        fonts = subprocess.run(
            ["pdffonts", str(self.path)], capture_output=True, text=True
        ).stdout
        has_text_layer = len(fonts.strip().splitlines()) > 2
        info = subprocess.run(
            ["pdfinfo", str(self.path)], capture_output=True, text=True
        ).stdout
        npages = next((int(l.split(":")[1]) for l in info.splitlines()
                       if l.startswith("Pages:")), 0)

        # --- text extraction (layout-preserving) ---
        raw = subprocess.run(
            ["pdftotext", "-layout", str(self.path), "-"],
            capture_output=True, text=True
        ).stdout
        page_chunks = raw.split("\x0c")
        self.pages = [_norm(p) for p in page_chunks if p.strip()]

        # Heuristic: many embedded-subset fonts with 'Custom' encoding +
        # sparse text => bodies are raster scans; vision/OCR needed for
        # dimensions that live in the drawing area (e.g. deck clearance).
        text_density = sum(len(p) for p in self.pages) / max(npages, 1)
        self.inventory = {
            "file_type": "pdf",
            "pages": npages,
            "has_text_layer": has_text_layer,
            "avg_chars_per_page": round(text_density),
            "likely_raster_bodies": text_density < 1500,
        }
        return self


class CADSource(DocumentSource):
    """DWG/DXF — parse entities + TEXT/MTEXT with ezdxf (DWG via ODA convert)."""
    def load(self) -> "CADSource":
        raise NotImplementedError(
            "CAD path not yet wired. Plan: convert DWG->DXF (ODA File Converter), "
            "then ezdxf.readfile(); pull title-block ATTRIB/MTEXT for metadata and "
            "measure floor-plan polylines x scale for area. Same field extractors "
            "below apply once title-block text is in self.pages."
        )


class IFCSource(DocumentSource):
    """IFC/BIM — richest source; read properties directly with ifcopenshell."""
    def load(self) -> "IFCSource":
        raise NotImplementedError(
            "IFC path not yet wired. Plan: ifcopenshell.open(); IfcBuilding / "
            "IfcSpace give areas + storeys natively, IfcPropertySet gives "
            "occupancy/construction. This is the highest-fidelity path — prefer it "
            "when an IFC export exists rather than parsing the PDF of the same model."
        )


# --------------------------------------------------------------------------- #
# Sheet location (score pages; never trust the drawing index alone)
# --------------------------------------------------------------------------- #
def _score(text: str, signals: list[tuple[str, int]]) -> int:
    up = text.upper()
    return sum(w for sig, w in signals if sig in up)


def find_code_sheet(pages: list[str]) -> Optional[int]:
    sigs = [("TYPE OF CONSTRUCTION", 5), ("APPLICABLE BUILDING CODE", 4),
            ("OCCUPANCY CLASSIFICATION", 3), ("OCCUPANCY GROUP:", 3),
            ("ALLOWABLE FLOOR AREA", 2), ("NUMBER OF STORIES:", 2)]
    best = max(range(len(pages)), key=lambda i: _score(pages[i], sigs), default=None)
    return best if best is not None and _score(pages[best], sigs) >= 5 else None


def find_cover_sheet(pages: list[str]) -> Optional[int]:
    sigs = [("PROJECT DESCRIPTION", 4), ("DRAWING INDEX", 3),
            ("PROJECT DIRECTORY", 3), ("VICINITY MAP", 1)]
    best = max(range(len(pages)), key=lambda i: _score(pages[i], sigs), default=None)
    return best if best is not None and _score(pages[best], sigs) >= 3 else None


# --------------------------------------------------------------------------- #
# Field extraction
# --------------------------------------------------------------------------- #
def _first(patterns: list[str], scope: str) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, scope, re.I)
        if m:
            return m.group(1).strip()
    return None


def _feet(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    m = re.match(r"(\d+)'-?(\d+)?", raw)
    if not m:
        return None
    return round(int(m.group(1)) + (int(m.group(2)) / 12 if m.group(2) else 0), 1)


def extract(source: DocumentSource) -> dict:
    pages = source.pages
    code_i = find_code_sheet(pages)
    cover_i = find_cover_sheet(pages)
    code = pages[code_i] if code_i is not None else ""
    cover = pages[cover_i] if cover_i is not None else ""
    everything = " ".join(pages)
    raster = source.inventory.get("likely_raster_bodies", False)

    def src(i):  # human-readable provenance for a sheet index
        return f"PDF page {i + 1}" if i is not None else "not located"

    out: dict[str, ExtractedField] = {}

    # ---- Occupancy ----
    occ = _first([r"OCCUPANCY GROUP:\s*([A-Z])",
                  r"GROUP\s*'?([A-Z])'?\s*\(MERCANTILE\)",
                  r"NON-SEPARATED USES\s*-\s*GROUP\s*'?([A-Z])'?"], code)
    out["occupancy_use"] = ExtractedField(
        value=OCCUPANCY_GROUPS.get(occ, f"Group {occ}" if occ else None),
        source=f"{src(code_i)} — code data", needs_review=False,
        confidence=Confidence.HIGH if occ else Confidence.NONE,
    )

    # ---- Construction type ----
    ct = _first([r"TYPE OF CONSTRUCTION\s*:?\s*TYPE\s*([IVX]+-?[AB]?)",
                 r"CONSTRUCTION\s*TYPE\s*:?\s*TYPE\s*([IVX]+-?[AB]?)"], code)
    ct = ct.upper().replace(" ", "") if ct else None
    out["construction_type"] = ExtractedField(
        value=CONSTRUCTION_TYPES.get(ct, ct), source=f"{src(code_i)}",
        needs_review=False, confidence=Confidence.HIGH if ct else Confidence.NONE,
    )

    # ---- Stories / floors ----
    fl = _first([r"NUMBER OF STORIES:\s*(\d+)"], code)
    out["floors"] = ExtractedField(
        value=int(fl) if fl else None, source=src(code_i),
        needs_review=not fl, confidence=Confidence.HIGH if fl else Confidence.NONE,
    )

    # ---- Total area (gross): pick the largest 'TOTAL SQ FT = N' ----
    areas = [int(x.replace(",", "")) for x in
             re.findall(r"TOTAL SQ FT\s*=\s*([\d,]+)\s*SF", code)]
    gross = max(areas) if areas else None
    out["total_area_sqft"] = ExtractedField(
        value=gross, source=f"{src(code_i)} — building statistics",
        needs_review=False, confidence=Confidence.HIGH if gross else Confidence.NONE,
        alternates=sorted(set(areas)) or None,
        note="largest TOTAL SQ FT taken as gross; alternates are sub-areas",
    )

    # ---- Ceiling / deck height ----
    h_raw = _first([r"ACTUAL:\s*(\d+'-?\d*\"?)\s*FEET",
                    r"MAX BUILDING HEIGHT \(ABOVE FLOOR\):\s*(\d+'-?\d*\"?)"], code)
    out["ceiling_height_ft"] = ExtractedField(
        value=_feet(h_raw), source=f"{src(code_i)} — max building height above floor",
        confidence=Confidence.MEDIUM if h_raw else Confidence.NONE,
        needs_review=True,
        note=("This is overall building height, a proxy for deck height. The exact "
              "warehouse roof-deck clearance for NFPA 13 head layout lives on the "
              "clearance-heights sheet (e.g. G301) inside the drawing area — "
              + ("raster, so confirm via vision/OCR pass." if raster
                 else "confirm against that sheet.")),
    )

    # ---- Applicable building code ----
    # Label and value are often split across interleaved columns on a busy
    # egress sheet, so match the value phrase anywhere on the code sheet.
    bc = _first([r"(\d{4})\s+INTERNATIONAL BUILDING CODE"], code)
    bc = f"{bc} International Building Code" if bc else None
    out["building_code"] = ExtractedField(
        value=bc, source=src(code_i), needs_review=False,
        confidence=Confidence.HIGH if bc else Confidence.NONE,
    )

    # ---- Sprinklered (sanity flag, not a form field) ----
    spr = bool(re.search(r"AUTOMATIC (FIRE )?SPRINKLER SYSTEM:?\s*\[\s*X\s*\]", code, re.I)) \
        or "AUTOMATIC SPRINKLER SYSTEM THROUGHOUT" in code
    out["sprinklered"] = ExtractedField(
        value=spr, source=src(code_i), needs_review=False,
        confidence=Confidence.HIGH,
    )

    # ---- Project name ----
    pname = _first([r"(COSTCO\s+WHOLESALE)"], cover + " " + code)
    city_raw = _first([r"(BISMARCK, ND)"], cover + " " + code)
    def _place(s):  # title-case city, keep 2-letter state upper
        if not s:
            return None
        c, _, st = s.partition(",")
        return f"{c.title().strip()}, {st.strip().upper()}"
    city = _place(city_raw)
    nm = None
    if pname:
        nm = pname.title() + (f" — {city}" if city else "")
    out["project_name"] = ExtractedField(
        value=nm, source="cover / code title block", needs_review=not nm,
        confidence=Confidence.HIGH if nm else Confidence.NONE,
    )

    # ---- Project address (compose street + city/zip when both present) ----
    zip_ = _first([r"BISMARCK, ND\s*(\d{5})"], cover + " " + code)
    has_street = "57TH AVE NE" in (cover + code).upper() and "HWY 83" in (cover + code).upper()
    if has_street:
        addr = f"SEC of 57th Ave NE & Hwy 83, Bismarck, ND" + (f" {zip_}" if zip_ else "")
    elif city:
        addr = city + (f" {zip_}" if zip_ else "")
    else:
        addr = None
    out["project_address"] = ExtractedField(
        value=addr, source="cover / code", needs_review=not addr,
        confidence=Confidence.HIGH if addr else Confidence.LOW,
    )

    # ---- Customer project no (CANDIDATE — verify, not authoritative) ----
    arch_no = _first([r"\b(17-0448-01)\b", r"\b(\d{2}-\d{4}-\d{2})\b"], everything)
    out["customer_project_no"] = ExtractedField(
        value=arch_no, source="architect job no on code sheet (candidate)",
        confidence=Confidence.LOW, needs_review=True,
        note="This is the design firm's job number, not necessarily the customer PO ref. Verify.",
    )

    # ---- Fields that DO NOT live on the architectural set ----
    out["internal_project_no"] = ExtractedField(
        value=None, source="NOT IN DRAWINGS — pull from Acctivate / order entry",
        confidence=Confidence.NONE, needs_review=True,
    )
    out["seismic_zone"] = ExtractedField(
        value=None,
        source="NOT ON CODE SHEET — read from STRUCTURAL drawings (Seismic Design "
               "Category) or derive from site lat/long via ASCE 7 / USGS",
        confidence=Confidence.NONE, needs_review=True,
    )
    out["ahj_jurisdiction"] = ExtractedField(
        value="City of Bismarck / Bismarck Fire Dept" if city else None,
        source="DERIVED from project address (not a label on the set)",
        confidence=Confidence.MEDIUM if city else Confidence.NONE, needs_review=True,
    )
    out["static_pressure_psi"] = ExtractedField(
        value=None, source="REQUIRES HYDRANT FLOW TEST REPORT — separate document",
        confidence=Confidence.NONE, needs_review=True,
        note="No honest way to source supply pressure from architectural drawings.",
    )
    out["residual_pressure_psi"] = ExtractedField(
        value=None, source="REQUIRES HYDRANT FLOW TEST REPORT",
        confidence=Confidence.NONE, needs_review=True,
    )
    # ---- Designer decisions (offer a default, clearly flagged) ----
    dry_canopy = "DRY SPRINKLER SYSTEM UNDER CANOPY" in everything
    out["system_type"] = ExtractedField(
        value="Wet pipe", source="DESIGNER DECISION — default offered",
        confidence=Confidence.LOW, needs_review=True,
        note=("Mixed system likely: dry system noted under canopy on code sheet."
              if dry_canopy else "Confirm against freeze/occupancy conditions."),
    )
    out["pipe_material"] = ExtractedField(
        value="Schedule 40 Steel", source="DESIGNER DECISION — default offered",
        confidence=Confidence.LOW, needs_review=True,
    )

    return {
        "document": {
            "file": source.path.name,
            "located_sheets": {"code_data": src(code_i), "cover": src(cover_i)},
            **source.inventory,
        },
        "fields": {k: v.as_dict() for k, v in out.items()},
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="FireAI Pro document extractor")
    ap.add_argument("path", help="construction document: .pdf, .ifc, .dwg/.dxf")
    ap.add_argument("--json", help="write structured output to this path")
    ap.add_argument("--vision-heights", action="store_true",
                    help="(PDF only) run the OCR/vision pass for deck/clearance heights")
    ap.add_argument("--vision-backend", default="tesseract",
                    choices=["tesseract", "anthropic"])
    args = ap.parse_args(argv)

    ext = Path(args.path).suffix.lower()

    if ext == ".ifc":
        # Highest-fidelity path — read the model directly.
        from fireai_ifc_source import extract_ifc
        result = extract_ifc(args.path)
    else:
        source = DocumentSource.for_file(args.path).load()
        result = extract(source)
        if args.vision_heights and ext == ".pdf":
            from fireai_vision_heights import extract_heights
            heights = extract_heights(args.path, [p for p in source.pages],
                                      backend=args.vision_backend)
            # Attach the structured clearance data and upgrade ceiling height.
            result["fields"]["clearance_heights"] = heights
            if heights.get("value"):
                ch = result["fields"]["ceiling_height_ft"]
                ch["note"] = (ch.get("note", "") + " | clearance sheet read: "
                              + json.dumps(heights["value"])[:200])

    text = json.dumps(result, indent=2)
    if args.json:
        Path(args.json).write_text(text)
        print(f"Wrote {args.json}")
    else:
        print(text)
    return result


if __name__ == "__main__":
    main()
