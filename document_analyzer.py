"""
FireAI Pro — document_analyzer.py
===================================
Fast project parameter extraction from a construction document set.

Reads only the first 3 pages of each uploaded file to extract title
block data and project metadata. Returns structured parameters to
auto-populate the UI form in ~10–20 seconds.

The full geometry extraction (rooms, walls, obstructions) still happens
later in fireai_document_intelligence.py when the design job runs.

Called from the /api/analyze endpoint in api/app.py.
Drop this file at the repo root.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import List

import anthropic

log = logging.getLogger("fireai.analyzer")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.getenv("FIREAI_MODEL", "claude-sonnet-4-20250514")
QUICK_SCAN_PAGES  = int(os.getenv("FIREAI_QUICK_SCAN_PAGES", "3"))  # pages per file


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_document_set(file_list: List[dict]) -> dict:
    """
    Quick-scan a document set to extract project parameters for UI auto-fill.
    file_list: [{"bytes": bytes, "filename": str}, ...]
    Returns a flat dict of project parameters. Missing fields are omitted.
    """
    log.info("[Analyzer] Quick-scanning %d file(s) (%d pages max each)",
             len(file_list), QUICK_SCAN_PAGES)

    # Render first N pages of each file in parallel
    render_tasks = [
        asyncio.create_task(_render_first_pages(f["bytes"], f["filename"]))
        for f in file_list
    ]
    page_sets = await asyncio.gather(*render_tasks, return_exceptions=True)

    all_pages = []
    for pages in page_sets:
        if isinstance(pages, Exception):
            continue
        all_pages.extend(pages)

    if not all_pages:
        log.warning("[Analyzer] No pages rendered — returning empty params")
        return {}

    # Extract metadata from all pages in parallel
    extract_tasks = [
        asyncio.create_task(_extract_page_metadata(p))
        for p in all_pages
    ]
    results = await asyncio.gather(*extract_tasks, return_exceptions=True)

    # Merge — first non-null value wins for each field
    # Exception: numeric fields take the max (e.g. total_area from multiple sheets)
    merged: dict = {}
    numeric_max  = {"total_area_sf", "floors"}

    for result in results:
        if isinstance(result, Exception) or not isinstance(result, dict):
            continue
        for key, val in result.items():
            if val in (None, "", 0, "unknown", "Unknown", "null"):
                continue
            if key in numeric_max:
                try:
                    val_num = float(val)
                    existing = float(merged.get(key, 0))
                    if val_num > existing:
                        merged[key] = val_num if isinstance(val, float) else int(val_num)
                except (ValueError, TypeError):
                    pass
            elif key not in merged:
                merged[key] = val

    log.info("[Analyzer] Extracted %d parameter(s): %s",
             len(merged), list(merged.keys()))
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# File rendering
# ─────────────────────────────────────────────────────────────────────────────

async def _render_first_pages(file_bytes: bytes, filename: str) -> list:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return await asyncio.to_thread(_render_pdf_pages, file_bytes, filename)
    elif ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"):
        return [{
            "label":      filename,
            "image_data": base64.b64encode(file_bytes).decode(),
            "media_type": _ext_mime(ext),
        }]
    # Unknown type — skip
    return []


def _render_pdf_pages(file_bytes: bytes, filename: str) -> list:
    """Render the first QUICK_SCAN_PAGES pages of a PDF at low DPI for speed."""
    try:
        import pdfplumber
    except ImportError:
        log.error("[Analyzer] pdfplumber not installed — cannot render PDF")
        return []

    pages = []
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        with pdfplumber.open(tmp_path) as pdf:
            total       = len(pdf.pages)
            scan_indices = list(range(min(QUICK_SCAN_PAGES, total)))
            log.info("[Analyzer] %s — scanning pages %s of %d",
                     filename, scan_indices, total)

            for idx in scan_indices:
                try:
                    page = pdf.pages[idx]
                    img  = page.to_image(resolution=120)   # low DPI = fast
                    pil  = img.original
                    w, h = pil.size

                    # Clamp to 2000px — enough for title block reading, fast to upload
                    if max(w, h) > 2000:
                        scale = 2000 / max(w, h)
                        pil   = pil.resize(
                            (int(w * scale), int(h * scale)),
                            resample=1  # LANCZOS
                        )

                    buf = io.BytesIO()
                    pil.save(buf, format="PNG", optimize=True)
                    pages.append({
                        "label":      f"{filename} p{idx+1}/{total}",
                        "image_data": base64.b64encode(buf.getvalue()).decode(),
                        "media_type": "image/png",
                    })
                except Exception as exc:
                    log.warning("[Analyzer] Page %d of %s failed: %s",
                                idx + 1, filename, exc)
    except Exception as exc:
        log.error("[Analyzer] PDF open failed %s: %s", filename, exc)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return pages


# ─────────────────────────────────────────────────────────────────────────────
# Metadata extraction per page
# ─────────────────────────────────────────────────────────────────────────────

async def _extract_page_metadata(page: dict) -> dict:
    """
    Send one page image to Claude and extract all visible project parameters.
    Title blocks, cover sheets, general notes, and spec sections are the
    richest sources — the first 3 pages usually contain all of them.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = """You are reading a construction drawing page to extract project information.

Look for ANY of the following — they may appear in the title block, cover sheet,
general notes, specifications, project data table, or stamp/seal area:

TARGET FIELDS:
- project_name: official building or project name
- project_address: full street address including city, state, zip
- occupancy_group: IBC occupancy classification (e.g. "Business (Group B)", "Storage (Group S-1)", "Mercantile (Group M)")
- ahj: authority having jurisdiction (e.g. "Austin Fire Department", "Los Angeles Fire Dept")
- floors: number of floors or stories (integer)
- ceiling_height_ft: typical ceiling height in feet (number)
- total_area_sf: total building area in square feet (number)
- construction_type: IBC construction type (e.g. "II-B", "V-A", "I-A")
- seismic_zone: seismic design category or zone (e.g. "D1", "D2", "C")
- system_type: fire suppression system type ("wet pipe", "dry pipe", "pre-action", "deluge")
- pipe_material: sprinkler pipe material ("Schedule 40 Steel", "Schedule 10 Steel", "CPVC", "Copper")
- static_pressure_psi: static water pressure in PSI (number)
- residual_pressure_psi: residual water pressure in PSI (number)
- flow_gpm: fire flow in GPM (number)
- designer_name: engineer or designer name with credentials
- company_name: fire protection company or engineering firm name

Return ONLY valid JSON with the fields you found.
OMIT any field you cannot find — do not guess or invent values.
For numeric fields, return numbers not strings.
{
  "project_name": "Riverside Office Complex",
  "project_address": "4200 Riverside Dr, Austin TX 78741",
  "occupancy_group": "Business (Group B)",
  ...only fields actually visible on this page...
}"""

    try:
        resp = await asyncio.to_thread(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type":       "base64",
                    "media_type": page["media_type"],
                    "data":       page["image_data"],
                }},
                {"type": "text", "text": prompt},
            ]}],
        )
        raw     = next((b.text for b in resp.content if b.type == "text"), "{}")
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$",          "", cleaned.strip())
        data    = json.loads(cleaned)

        # Strip null/empty values
        clean = {k: v for k, v in data.items()
                 if v is not None and v != "" and v != 0}

        if clean:
            log.info("[Analyzer] %s → %s", page["label"], list(clean.keys()))
        return clean

    except json.JSONDecodeError as exc:
        log.warning("[Analyzer] JSON parse failed for %s: %s", page["label"], exc)
        return {}
    except Exception as exc:
        log.warning("[Analyzer] Extraction failed for %s: %s", page["label"], exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ext_mime(ext: str) -> str:
    return {
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".tif":  "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }.get(ext, "image/png")
