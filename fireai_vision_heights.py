#!/usr/bin/env python3
"""
FireAI Pro — Vision/OCR pass for deck & clearance heights
=========================================================

Some of the most design-critical numbers (roof-deck height, minimum vertical
clearance, top-of-storage) are NOT labeled text on the code sheet — they are
dimension callouts inside the drawing area of the clearance-heights/section
sheet (e.g. G301). Text extraction is blind to them. This module:

  1. Locates the clearance-heights sheet WITHOUT being fooled by drawing-index
     or sheet-list references to it (the mistake that wasted two passes during
     development: the cover and the electrical sheet both name-drop "clearance
     heights"). It scores a page on its own title-block identity and penalizes
     index/list pages.
  2. Rasterizes that sheet.
  3. Extracts heights with one of two backends:
       - tesseract  : local, offline, free. Good first pass.
       - anthropic  : Claude vision via /v1/messages. Higher accuracy on noisy
                      architectural sheets; recommended for production.

Returns a provenance-tagged dict in the same shape as the main extractor, so
the orchestrator can merge it straight into the intake form.

Heights are intentionally returned as a STRUCTURE, not one number, because a
warehouse has no single "ceiling height": heads mount tight to a sloping deck,
a minimum clearance line is held below it, and storage tops out lower still.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# 1. Locate the clearance-heights sheet (title-block aware)
# --------------------------------------------------------------------------- #
_TITLE_SIGNALS = [
    ("WAREHOUSE CLEARANCE HEIGHTS", 6),
    ("CLEARANCE HEIGHTS, SECTIONS", 6),
    ("MIN VERTICAL CLEARANCE", 4),
    ("HIGH BAY LIGHT MOUNTING", 3),
    ("AFF MIN CLEAR", 3),
    ("TIGHT TO STRUCTURE", 2),
]
# Pages that merely *reference* the sheet — must not win.
_INDEX_PENALTY = [
    ("DRAWING INDEX", 8), ("SHEET LIST", 8), ("ELECTRICAL SHEET LIST", 8),
    ("PROJECT DIRECTORY", 6), ("LIGHTING FIXTURE SCHEDULE", 5),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def locate_clearance_sheet(pages: list[str]) -> Optional[int]:
    """pages: normalized per-page text. Returns 0-based page index or None."""
    def score(t: str) -> int:
        up = t.upper()
        s = sum(w for sig, w in _TITLE_SIGNALS if sig in up)
        s -= sum(w for sig, w in _INDEX_PENALTY if sig in up)
        return s
    best, best_s = None, 0
    for i, t in enumerate(pages):
        sc = score(t)
        if sc > best_s:
            best, best_s = i, sc
    return best if best_s >= 4 else None


# --------------------------------------------------------------------------- #
# 2. Rasterize
# --------------------------------------------------------------------------- #
def rasterize(pdf_path: str, page_1based: int, dpi: int = 200,
              out_prefix: str = "/tmp/fa_sheet") -> str:
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi), "-f", str(page_1based),
         "-l", str(page_1based), pdf_path, out_prefix],
        check=True, capture_output=True,
    )
    hits = sorted(Path(out_prefix).parent.glob(Path(out_prefix).name + "*"))
    if not hits:
        raise FileNotFoundError("rasterize produced no output")
    return str(hits[-1])


# --------------------------------------------------------------------------- #
# 3a. Backend: tesseract (local)
# --------------------------------------------------------------------------- #
_HEIGHT = r"(\d{1,2})\s*['’°`\u2019]?\s*-\s*(\d{1,2})\s*[\"”]?"


def _clean_ht(m) -> str:
    """Normalize an OCR'd feet-inches match to a clean string like 22'-0\"."""
    return f"{m.group(1)}'-{m.group(2)}\""


def _parse_heights(text: str) -> dict:
    """Pull the clearance-relevant statements out of OCR text."""
    out = {"min_vertical_clearance": None, "service_min_aff": None,
           "storage_top_aff": None, "raw_callouts": []}
    for ln in text.splitlines():
        s = _norm(ln).strip()
        if not re.search(r"\d", s):
            continue
        up = s.upper()
        if "CLEAR" in up and "AFF" in up:
            m = re.search(_HEIGHT, s)
            if m and not out["min_vertical_clearance"]:
                out["min_vertical_clearance"] = _clean_ht(m)
            out["raw_callouts"].append(s)
        elif "AFF MIN" in up:
            m = re.search(_HEIGHT, s)
            if m and not out["service_min_aff"]:
                out["service_min_aff"] = _clean_ht(m)
            out["raw_callouts"].append(s)
        elif "RACK" in up or "STORAGE" in up:
            m = re.search(_HEIGHT, s)
            if m and not out["storage_top_aff"]:
                out["storage_top_aff"] = _clean_ht(m)
            out["raw_callouts"].append(s)
    return out


def ocr_tesseract(image_path: str) -> dict:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    import pytesseract
    img = Image.open(image_path)
    # Downscale very large rasters so tesseract stays fast and accurate.
    if img.width > 5000:
        ratio = 5000 / img.width
        img = img.resize((5000, int(img.height * ratio)))
    text = pytesseract.image_to_string(img)
    return _parse_heights(text)


# --------------------------------------------------------------------------- #
# 3b. Backend: Anthropic vision (recommended for production)
# --------------------------------------------------------------------------- #
VISION_PROMPT = (
    "This is an architectural WAREHOUSE CLEARANCE HEIGHTS / building section "
    "sheet for a fire-sprinkler design. Read the dimension callouts and return "
    "ONLY JSON, no prose:\n"
    "{\n"
    '  "roof_deck_or_structure_height_aff": "<e.g. 32\'-8\\" or null>",\n'
    '  "min_vertical_clearance_aff": "<the minimum clear height maintained>",\n'
    '  "sprinkler_mains_position": "<e.g. tight to structure>",\n'
    '  "top_of_storage_aff": "<rack/storage height or null>",\n'
    '  "service_min_aff": "<conduit/piping min AFF or null>",\n'
    '  "notes": "<any clearance note relevant to head layout>"\n'
    "}\n"
    "Use the exact feet-inches strings shown on the drawing. If a value is not "
    "shown, use null. Do not guess."
)


def vision_anthropic(image_path: str,
                     model: str = "claude-opus-4-7",
                     api_key_env: str = "ANTHROPIC_API_KEY") -> dict:
    """Send the rasterized sheet to Claude vision. Requires ANTHROPIC_API_KEY.

    In a FireAI Pro Artifact, call the in-browser /v1/messages endpoint instead
    (no key needed) using the same prompt and image block — see README.
    """
    import urllib.request
    key = os.environ.get(api_key_env)
    if not key:
        raise RuntimeError(f"{api_key_env} not set; cannot call vision backend")
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    body = json.dumps({
        "model": model,
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")
    text = re.sub(r"```json|```", "", text).strip()
    return json.loads(text)


# --------------------------------------------------------------------------- #
# 4. Public entry point
# --------------------------------------------------------------------------- #
def extract_heights(pdf_path: str, pages_text: list[str], *,
                    backend: str = "tesseract", dpi: int = 200) -> dict:
    """Locate the clearance sheet, rasterize, and extract heights with provenance."""
    norm_pages = [_norm(p) for p in pages_text]
    idx = locate_clearance_sheet(norm_pages)
    if idx is None:
        return {"value": None, "confidence": "none", "needs_review": True,
                "source": "clearance-heights sheet not located",
                "note": "No sheet matched WAREHOUSE CLEARANCE HEIGHTS title block."}
    page_1 = idx + 1
    img = rasterize(pdf_path, page_1, dpi=dpi)
    if backend == "anthropic":
        heights = vision_anthropic(img)
        conf = "high"
    else:
        heights = ocr_tesseract(img)
        conf = "medium"
    return {
        "value": heights,
        "source": f"PDF page {page_1} (clearance-heights sheet) via {backend}",
        "confidence": conf,
        "needs_review": True,
        "note": ("Warehouse has no single ceiling height: heads mount tight to a "
                 "sloping deck, with the listed minimum clearance held below. "
                 "Designer confirms head elevations per NFPA 13."),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--backend", default="tesseract", choices=["tesseract", "anthropic"])
    ap.add_argument("--alltext", help="optional pre-extracted text file (form-feed split)")
    a = ap.parse_args()
    if a.alltext:
        pages = Path(a.alltext).read_text(errors="replace").split("\x0c")
    else:
        raw = subprocess.run(["pdftotext", "-layout", a.pdf, "-"],
                             capture_output=True, text=True).stdout
        pages = raw.split("\x0c")
    print(json.dumps(extract_heights(a.pdf, pages, backend=a.backend), indent=2))
