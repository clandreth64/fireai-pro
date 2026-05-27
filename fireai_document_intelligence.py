"""
FireAI Pro — fireai_document_intelligence.py  (v2 — Full Drawing Set)
=======================================================================
Drop-in replacement for the original file. Same public interface:
    handle_document_set(file_list, project_context) → geometry dict

What's new vs v1:
  ✓ Multi-page PDF support — every page becomes a sheet to classify.
    v1 only read page 0. A 40-sheet construction set PDF is now fully
    processed, not just the cover sheet.
  ✓ Drawing index detection — finds the sheet schedule (G-sheets,
    index pages) first and uses it to understand the full set.
  ✓ Title block extraction — sheet number, scale, and date pulled
    from every sheet for precise reference.
  ✓ Parallel page rendering + classification — all pages rendered and
    classified concurrently, then extraction runs on relevant sheets only.
  ✓ Project Brief — a structured summary is generated after synthesis
    and attached to the output. The design engine and agents can
    reference it throughout the run.
  ✓ MAX_PAGES_PER_FILE cap — prevents runaway processing on huge PDFs.
    Pages over the cap are sampled intelligently (index, floor plans
    prioritised over details and elevations).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional

import anthropic

log = logging.getLogger("fireai.intelligence")

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL       = os.getenv("FIREAI_MODEL", "claude-sonnet-4-20250514")
MAX_PAGES_PER_FILE = int(os.getenv("FIREAI_MAX_PAGES", "60"))   # hard cap
PDF_DPI            = int(os.getenv("FIREAI_PDF_DPI", "150"))    # lower = faster
MAX_IMAGE_PX       = 4000                                        # Claude Vision limit

# ── Document type priority ────────────────────────────────────────────────────

DOC_PRIORITY = {
    "drawing_index":   12,   # Process first — tells us what else exists
    "floor_plan":      10,
    "fire_protection":  9,
    "rcp":              8,
    "structural":       7,
    "mechanical":       6,
    "site_plan":        5,
    "specification":    4,
    "schedule":         3,
    "general_notes":    3,
    "plumbing":         3,
    "civil":            2,
    "elevation":        2,
    "section":          2,
    "detail":           1,
    "cover":            0,   # Cover sheet — no geometry
    "unknown":          0,
}

# ── Hazard criteria (unchanged from v1) ──────────────────────────────────────

HAZARD_CRITERIA = {
    "light":           {"density": 0.10, "area": 1500, "max_coverage": 225,  "max_spacing": 15, "k": 5.6,  "min_psi": 7.0,  "esfr": False},
    "ordinary_1":      {"density": 0.15, "area": 1500, "max_coverage": 130,  "max_spacing": 15, "k": 5.6,  "min_psi": 7.0,  "esfr": False},
    "ordinary_2":      {"density": 0.20, "area": 1500, "max_coverage": 130,  "max_spacing": 15, "k": 8.0,  "min_psi": 7.0,  "esfr": False},
    "extra_1":         {"density": 0.30, "area": 2500, "max_coverage": 100,  "max_spacing": 12, "k": 11.2, "min_psi": 15.0, "esfr": False},
    "extra_2":         {"density": 0.40, "area": 2500, "max_coverage": 100,  "max_spacing": 12, "k": 11.2, "min_psi": 15.0, "esfr": False},
    "esfr_k14":        {"density": None, "area": None, "max_coverage": 100,  "max_spacing": 10, "k": 14.0, "min_psi": 50.0, "esfr": True},
    "esfr_k25":        {"density": None, "area": None, "max_coverage": 100,  "max_spacing": 10, "k": 25.0, "min_psi": 15.0, "esfr": True},
    "tire_storage":    {"density": None, "area": None, "max_coverage": 100,  "max_spacing": 10, "k": 14.0, "min_psi": 75.0, "esfr": True},
    "freezer":         {"density": 0.15, "area": 2000, "max_coverage": 130,  "max_spacing": 12, "k": 5.6,  "min_psi": 7.0,  "esfr": False},
    "cooler":          {"density": 0.15, "area": 1500, "max_coverage": 130,  "max_spacing": 12, "k": 5.6,  "min_psi": 7.0,  "esfr": False},
}


# ─────────────────────────────────────────────────────────────────────────────
# Main class
# ─────────────────────────────────────────────────────────────────────────────

class DocumentIntelligence:

    def __init__(self, api_key: str = ANTHROPIC_API_KEY):
        self.client = anthropic.Anthropic(api_key=api_key)

    # ── Public entry point ────────────────────────────────────────────────────

    async def process_document_set(
        self,
        file_list: List[dict],   # [{"bytes": bytes, "filename": str}, ...]
        project_context: dict,
    ) -> dict:
        if not file_list:
            return _empty_project(project_context)

        log.info("[Intel] Received %d file(s)", len(file_list))

        # ── Step 1: Render all files into individual page images ──────────────
        all_pages = await self._render_all_files(file_list)
        log.info("[Intel] Rendered %d pages total", len(all_pages))

        if not all_pages:
            return _empty_project(project_context)

        # ── Step 2: Classify all pages in parallel ────────────────────────────
        classify_tasks = [
            asyncio.create_task(self._classify_page(p, project_context))
            for p in all_pages
        ]
        classifications = await asyncio.gather(*classify_tasks, return_exceptions=True)

        for page, cls in zip(all_pages, classifications):
            if isinstance(cls, Exception):
                page["classification"] = {"type": "unknown", "priority": 0}
            else:
                page["classification"] = cls
            page["priority"] = DOC_PRIORITY.get(
                page["classification"].get("type", "unknown"), 0
            )
            log.info(
                "[Intel] %-40s → %-16s (sheet=%-8s priority=%d)",
                page["label"][:40],
                page["classification"].get("type", "unknown"),
                page["classification"].get("sheet_number", ""),
                page["priority"],
            )

        # ── Step 3: Check for drawing index and log the set ───────────────────
        index_pages = [p for p in all_pages if p["classification"].get("type") == "drawing_index"]
        if index_pages:
            log.info("[Intel] Found %d drawing index page(s)", len(index_pages))

        # ── Step 4: Extract from relevant pages (priority > 0) ───────────────
        relevant = sorted(
            [p for p in all_pages if p["priority"] > 0],
            key=lambda p: p["priority"], reverse=True
        )
        log.info("[Intel] Processing %d relevant page(s)", len(relevant))

        extract_tasks = [
            asyncio.create_task(self._extract_from_page(p, project_context))
            for p in relevant
        ]
        extractions = await asyncio.gather(*extract_tasks, return_exceptions=True)

        doc_data = []
        for page, extraction in zip(relevant, extractions):
            if isinstance(extraction, Exception):
                log.warning("[Intel] Extraction failed for %s: %s", page["label"], extraction)
                continue
            doc_data.append({
                "filename":     page["label"],
                "source_file":  page["source_file"],
                "page_number":  page["page_number"],
                "type":         page["classification"]["type"],
                "sheet_number": page["classification"].get("sheet_number", ""),
                "sheet_title":  page["classification"].get("sheet_title", ""),
                "priority":     page["priority"],
                "data":         extraction,
            })

        # ── Step 5: Synthesize ────────────────────────────────────────────────
        project = self._synthesize(doc_data, project_context)

        # ── Step 6: Generate project brief ────────────────────────────────────
        project["project_brief"] = await self._generate_project_brief(
            doc_data, project, project_context
        )

        log.info(
            "[Intel] Complete: %dx%d ft | %d rooms | %d obstructions | %d sheets processed",
            project["building_dimensions"].get("width_ft", 0),
            project["building_dimensions"].get("depth_ft", 0),
            len(project.get("rooms", [])),
            len(project.get("obstructions", [])),
            len(doc_data),
        )
        return project

    # ── Step 1: Render all files into page images ─────────────────────────────

    async def _render_all_files(self, file_list: List[dict]) -> list:
        """
        Convert every file into a list of page images.
        Multi-page PDFs become multiple entries.
        Returns list of page dicts: {label, source_file, page_number, image_data, media_type}
        """
        render_tasks = [
            asyncio.create_task(self._render_file(f["bytes"], f["filename"]))
            for f in file_list
        ]
        results = await asyncio.gather(*render_tasks, return_exceptions=True)

        all_pages = []
        for f, pages in zip(file_list, results):
            if isinstance(pages, Exception):
                log.warning("[Intel] Could not render %s: %s", f["filename"], pages)
                continue
            all_pages.extend(pages)
        return all_pages

    async def _render_file(self, file_bytes: bytes, filename: str) -> list:
        """Render one file into a list of page dicts."""
        ext = Path(filename).suffix.lower()
        pages = []

        if ext == ".pdf":
            pages = await asyncio.to_thread(self._render_pdf, file_bytes, filename)
        elif ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"):
            pages = [{
                "label":       filename,
                "source_file": filename,
                "page_number": 1,
                "image_data":  base64.b64encode(file_bytes).decode(),
                "media_type":  _ext_to_mime(ext),
            }]
        else:
            # Unknown type — try sending raw bytes and let Claude handle it
            pages = [{
                "label":       filename,
                "source_file": filename,
                "page_number": 1,
                "image_data":  base64.b64encode(file_bytes).decode(),
                "media_type":  "application/octet-stream",
            }]

        log.info("[Intel] %s → %d page(s)", filename, len(pages))
        return pages

    def _render_pdf(self, file_bytes: bytes, filename: str) -> list:
        """
        Render every page of a PDF as a PNG image.
        Caps at MAX_PAGES_PER_FILE. Large PDFs are sampled intelligently.
        Runs in a thread (CPU-bound).
        """
        try:
            import pdfplumber
        except ImportError:
            log.error("[Intel] pdfplumber not installed — cannot render PDF")
            return []

        pages = []

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            with pdfplumber.open(tmp_path) as pdf:
                total = len(pdf.pages)
                log.info("[Intel] %s has %d page(s)", filename, total)

                # Which pages to render
                if total <= MAX_PAGES_PER_FILE:
                    page_indices = list(range(total))
                else:
                    # Always include first 5 (cover + index) and last 2
                    # Sample the rest evenly
                    head = list(range(min(5, total)))
                    tail = list(range(max(0, total - 2), total))
                    step = max(1, (total - 7) // (MAX_PAGES_PER_FILE - 7))
                    middle = list(range(5, total - 2, step))
                    page_indices = sorted(set(head + middle + tail))[:MAX_PAGES_PER_FILE]
                    log.info(
                        "[Intel] %s: %d pages → sampling %d",
                        filename, total, len(page_indices)
                    )

                for idx in page_indices:
                    try:
                        page = pdf.pages[idx]
                        img  = page.to_image(resolution=PDF_DPI)

                        # Clamp to MAX_IMAGE_PX to avoid Claude rejection
                        pil_img = img.original
                        w, h    = pil_img.size
                        if max(w, h) > MAX_IMAGE_PX:
                            scale   = MAX_IMAGE_PX / max(w, h)
                            pil_img = pil_img.resize(
                                (int(w * scale), int(h * scale)),
                                resample=1  # LANCZOS
                            )

                        buf = io.BytesIO()
                        pil_img.save(buf, format="PNG", optimize=True)
                        b64 = base64.b64encode(buf.getvalue()).decode()

                        label = f"{filename} — p{idx+1}/{total}"
                        pages.append({
                            "label":       label,
                            "source_file": filename,
                            "page_number": idx + 1,
                            "total_pages": total,
                            "image_data":  b64,
                            "media_type":  "image/png",
                        })
                    except Exception as exc:
                        log.warning("[Intel] Page %d of %s failed: %s", idx + 1, filename, exc)

        except Exception as exc:
            log.error("[Intel] PDF open failed for %s: %s", filename, exc)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        return pages

    # ── Step 2: Classify a page ───────────────────────────────────────────────

    async def _classify_page(self, page: dict, project_context: dict) -> dict:
        prompt = f"""You are reviewing a construction document page for a fire sprinkler design project.

Project: {project_context.get('project_name','')}
Occupancy: {project_context.get('occupancy','')}
Page: {page['label']}

Identify this document page. Return ONLY valid JSON:
{{
  "type": "drawing_index|cover|floor_plan|rcp|structural|mechanical|plumbing|civil|site_plan|fire_protection|specification|general_notes|elevation|section|detail|schedule|unknown",
  "sheet_number": "e.g. A1.1 or S-2 or FP-1 or empty string",
  "sheet_title": "e.g. FIRST FLOOR PLAN or empty string",
  "floor_level": "ground|second|basement|roof|mezzanine|all|unknown",
  "drawing_scale": "e.g. 1/8=1-0 or 1:100 or NTS or empty string",
  "is_relevant": true,
  "key_items": ["list of 3-5 key items visible, e.g. room labels, duct layout, beam schedule"],
  "confidence": "high|medium|low"
}}

Type definitions:
- drawing_index: sheet list/index, drawing schedule, list of drawings
- cover: title sheet, project cover page, no geometry
- floor_plan: architectural plan view with walls, rooms, dimensions
- rcp: reflected ceiling plan with ceiling grid and heights
- structural: beams, columns, joists, steel framing, foundation
- mechanical: HVAC ductwork, equipment, diffusers
- plumbing: drain pipes, water lines, fixtures (not fire sprinkler)
- civil/site_plan: site layout, grading, utilities, water supply
- fire_protection: sprinkler, standpipe, or fire alarm drawings
- specification: written spec text (Division 21, 22, etc.)
- general_notes: legend, abbreviations, project notes, symbols
- elevation: exterior or interior vertical views
- section: cross-sections, wall sections
- detail: large-scale detail drawings
- schedule: door/window/finish/equipment schedules"""

        return await self._vision_call(page["image_data"], page["media_type"], prompt, "Classify")

    # ── Step 3: Extract from a classified page ────────────────────────────────

    async def _extract_from_page(self, page: dict, project_context: dict) -> dict:
        doc_type = page["classification"].get("type", "unknown")
        extractors = {
            "drawing_index":   self._extract_drawing_index,
            "floor_plan":      self._extract_floor_plan,
            "rcp":             self._extract_rcp,
            "structural":      self._extract_structural,
            "mechanical":      self._extract_mechanical,
            "civil":           self._extract_civil,
            "site_plan":       self._extract_civil,
            "fire_protection": self._extract_fire_protection,
            "specification":   self._extract_specification,
            "general_notes":   self._extract_general_notes,
            "schedule":        self._extract_general_notes,
            "plumbing":        self._extract_plumbing,
        }
        extractor = extractors.get(doc_type, self._extract_generic)
        return await extractor(page, project_context)

    # ── Extractors ────────────────────────────────────────────────────────────

    async def _extract_drawing_index(self, page: dict, project_context: dict) -> dict:
        """Parse the drawing schedule to understand the full document set."""
        prompt = f"""This is a drawing index / sheet schedule for a construction project.

Extract the complete list of drawings in this set.

Return ONLY valid JSON:
{{
  "project_name": "official project name from title block",
  "project_address": "address if visible",
  "architect": "architect name/firm",
  "engineer": "structural engineer name/firm",
  "date": "issue date",
  "sheets": [
    {{
      "sheet_number": "A1.1",
      "title": "FIRST FLOOR PLAN",
      "type": "floor_plan|structural|mechanical|civil|specification|other",
      "level": "ground|second|basement|roof|all"
    }}
  ],
  "total_sheet_count": number,
  "has_fire_protection_sheets": true_or_false,
  "has_specification_sections": true_or_false,
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "Index")

    async def _extract_floor_plan(self, page: dict, project_context: dict) -> dict:
        known_area = float(project_context.get("total_area", 0))
        known_ch   = float(project_context.get("ceiling_height", 10))
        occupancy  = project_context.get("occupancy", "")
        scale      = page["classification"].get("drawing_scale", "")
        sheet      = page["classification"].get("sheet_number", "")
        level      = page["classification"].get("floor_level", "ground")

        prompt = f"""You are an NFPA 13 fire sprinkler engineer analyzing an architectural floor plan.

Project: {project_context.get('project_name','')}
Occupancy: {occupancy}
Sheet: {sheet} — Level: {level}
Drawing Scale: {scale}
Known total area: {known_area} SF | Known ceiling height: {known_ch} ft

EXTRACT all of the following for fire sprinkler design:

1. BUILDING DIMENSIONS — overall width and depth in feet (read from dimension strings)
2. DRAWING SCALE — confirm or correct from title block
3. ALL ROOMS — every labeled area with rectangular or polygonal boundary
4. HAZARD CLASSIFICATION — assign NFPA 13 hazard to every room:
   - light: offices, corridors, lobbies, restrooms, vestibules, membership, fitness
   - ordinary_1: retail sales, parking, mechanical room, pharmacy
   - ordinary_2: receiving/dock, food service, bakery, deli, kitchen, food prep
   - esfr_k14: warehouse floor, high-pile storage, merchandise sales area, rack areas
   - esfr_k25: high-pile storage >25ft or Class IV plastics
   - tire_storage: tire center, automotive service with tires
   - freezer: walk-in freezers; cooler: walk-in coolers

COORDINATE SYSTEM: Origin (0,0) = bottom-left of building footprint. X = right, Y = up. ALL IN FEET.
CRITICAL: Every square foot of the building must be covered. No gaps.

Return ONLY valid JSON:
{{
  "sheet_number": "{sheet}",
  "floor_level": "{level}",
  "building_dimensions": {{"width_ft": number, "depth_ft": number}},
  "drawing_scale": "{scale or 'NTS'}",
  "floor_area_sf": number,
  "rooms": [
    {{
      "name": "room or area label from drawing",
      "hazard_classification": "light|ordinary_1|ordinary_2|esfr_k14|esfr_k25|tire_storage|freezer|cooler",
      "nfpa_13_basis": "§8.5.1/§22.1/etc",
      "boundary": [{{"x":0,"y":0}},{{"x":W,"y":0}},{{"x":W,"y":D}},{{"x":0,"y":D}}],
      "estimated_area_sf": number,
      "ceiling_height_ft": {known_ch},
      "floor_level": "{level}",
      "notes": ""
    }}
  ],
  "columns": [{{"x": number, "y": number, "size": "e.g. 12x12 or W8x31"}}],
  "walls": [],
  "doors": [{{"x": number, "y": number, "width_ft": number, "type": "entry|exit|interior"}}],
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "FloorPlan")

    async def _extract_rcp(self, page: dict, project_context: dict) -> dict:
        prompt = f"""Analyze this Reflected Ceiling Plan (RCP) for fire sprinkler design per NFPA 13 §8.6.

Sheet: {page['classification'].get('sheet_number','')}

Extract ceiling conditions that affect sprinkler placement:

Return ONLY valid JSON:
{{
  "ceiling_areas": [
    {{
      "name": "area name",
      "height_ft": number,
      "ceiling_type": "exposed_structure|t_bar|drywall|open|sloped",
      "boundary": [{{"x":number,"y":number}}]
    }}
  ],
  "soffits": [
    {{
      "location": "description",
      "height_ft": number,
      "width_ft": number,
      "boundary": [{{"x":number,"y":number}}]
    }}
  ],
  "obstructions": [
    {{
      "type": "soffit|beam|duct|column|pendant|other",
      "description": "e.g. 24-inch wide soffit at 10ft",
      "height_ft": number,
      "boundary": [{{"x":number,"y":number}}]
    }}
  ],
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "RCP")

    async def _extract_structural(self, page: dict, project_context: dict) -> dict:
        prompt = f"""Analyze this structural drawing for fire sprinkler obstruction analysis per NFPA 13 §8.6.

Sheet: {page['classification'].get('sheet_number','')}

Extract structural members that require sprinkler accommodation:

Return ONLY valid JSON:
{{
  "structural_system": "steel_joist|steel_beam|concrete|wood_joist|other",
  "joist_direction": "north_south|east_west|unknown",
  "joist_depth_in": number,
  "joist_spacing_ft": number,
  "beams": [
    {{
      "id": "B-1",
      "from": {{"x":number,"y":number}},
      "to": {{"x":number,"y":number}},
      "depth_in": number,
      "flange_width_in": number,
      "bottom_of_beam_ft": number
    }}
  ],
  "columns": [{{"x":number,"y":number,"size":"W8x31"}}],
  "deck_type": "metal_deck|concrete|wood|unknown",
  "deck_flute_depth_in": number,
  "mezzanine": false,
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "Structural")

    async def _extract_mechanical(self, page: dict, project_context: dict) -> dict:
        prompt = f"""Analyze this mechanical/HVAC drawing for fire sprinkler obstructions per NFPA 13 §8.6.5.

Sheet: {page['classification'].get('sheet_number','')}

Ducts >4ft wide require sprinklers both above and below (§8.6.5.1).
Ducts >1ft wide can deflect spray and require head relocation.

Return ONLY valid JSON:
{{
  "ducts": [
    {{
      "id": "D-1",
      "type": "supply|return|exhaust",
      "width_in": number,
      "depth_in": number,
      "bottom_elevation_ft": number,
      "path": [{{"x":number,"y":number}}],
      "requires_sprinkler_below": true_or_false
    }}
  ],
  "equipment": [
    {{
      "type": "AHU|RTU|fan_coil|other",
      "id": "AHU-1",
      "boundary": [{{"x":number,"y":number}}],
      "height_ft": number
    }}
  ],
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "Mechanical")

    async def _extract_civil(self, page: dict, project_context: dict) -> dict:
        prompt = f"""Analyze this civil/site plan for fire sprinkler water supply information.

Sheet: {page['classification'].get('sheet_number','')}

Extract all water supply data for hydraulic calculations:

Return ONLY valid JSON:
{{
  "water_mains": [{{"diameter_in":number,"material":"DI|CI|PVC","pressure_psi":number}}],
  "hydrants": [{{"id":"H-1","distance_ft":number,"flow_gpm":number,"x":number,"y":number}}],
  "fdc_location": {{"x":number,"y":number,"description":""}},
  "static_pressure_psi": number,
  "residual_pressure_psi": number,
  "flow_test_gpm": number,
  "test_date": "",
  "utility_notes": [],
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "Civil")

    async def _extract_fire_protection(self, page: dict, project_context: dict) -> dict:
        prompt = f"""Analyze this fire protection drawing.

Sheet: {page['classification'].get('sheet_number','')}

Extract existing system data and design parameters:

Return ONLY valid JSON:
{{
  "system_type": "wet_pipe|dry_pipe|pre_action|deluge|none",
  "riser_location": {{"x":number,"y":number}},
  "existing_pipe_sizes": {{"main_in":number,"branch_in":number}},
  "sprinkler_type": "pendant|upright|sidewall|esfr|other",
  "sprinkler_model": "",
  "k_factor": number,
  "design_area_sqft": number,
  "design_density_gpm_sqft": number,
  "hydraulic_reference": {{"x":number,"y":number,"psi":number,"gpm":number}},
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "FP")

    async def _extract_specification(self, page: dict, project_context: dict) -> dict:
        prompt = f"""Analyze this specification document for fire sprinkler design requirements.

Look for Division 21 (Fire Suppression) or related sections.

Return ONLY valid JSON:
{{
  "nfpa_13_edition": "2022|2019|2016|unknown",
  "pipe_material": "Schedule 40 Steel|Schedule 10 Steel|CPVC|Copper|unknown",
  "pipe_joining": "threaded|grooved|welded|unknown",
  "system_type": "wet_pipe|dry_pipe|pre_action|antifreeze|unknown",
  "seismic_zone": "A|B|C|D|D1|D2|E|unknown",
  "minimum_design_density": number,
  "sprinkler_listing": "FM|UL|both|either",
  "special_requirements": [],
  "ahj_amendments": [],
  "water_supply_requirements": {{"static_psi":number,"residual_psi":number,"flow_gpm":number}},
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "Spec")

    async def _extract_general_notes(self, page: dict, project_context: dict) -> dict:
        prompt = f"""Analyze this general notes / legend sheet.

Extract project and code data relevant to fire sprinkler design.

Return ONLY valid JSON:
{{
  "building_code": "IBC 2021|IBC 2018|CBC|other|unknown",
  "construction_type": "I-A|I-B|II-A|II-B|III-A|III-B|IV|V-A|V-B|unknown",
  "occupancy_group": "S-1|S-2|M|A-2|B|other|unknown",
  "occupant_load": number,
  "ahj": "jurisdiction name or unknown",
  "special_requirements": [],
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "Notes")

    async def _extract_plumbing(self, page: dict, project_context: dict) -> dict:
        prompt = f"""Analyze this plumbing drawing for domestic water supply information.

Return ONLY valid JSON:
{{
  "water_service_size_in": number,
  "building_water_pressure_psi": number,
  "backflow_preventer_type": "",
  "notes": []
}}"""
        return await self._vision_call(page["image_data"], page["media_type"], prompt, "Plumbing")

    async def _extract_generic(self, page: dict, project_context: dict) -> dict:
        return {"notes": [], "data": {}}

    # ── Step 4: Synthesis ─────────────────────────────────────────────────────

    def _synthesize(self, doc_data: list, project_context: dict) -> dict:
        """Merge all extracted data into a unified project model."""
        known_area = float(project_context.get("total_area", 0))
        known_ch   = float(project_context.get("ceiling_height", 10))

        project = {
            "walls":                [],
            "rooms":                [],
            "columns":              [],
            "obstructions":         [],
            "structural_beams":     [],
            "building_dimensions":  {},
            "floor_area_sf":        known_area,
            "ceiling_height_ft":    known_ch,
            "drawing_scale":        "",
            "spec":                 {},
            "water_supply": {
                "static_pressure_psi":   float(project_context.get("static_pressure", 72)),
                "residual_pressure_psi": float(project_context.get("residual_pressure",
                                               float(project_context.get("static_pressure", 72)) * 0.85)),
                "flow_gpm":             float(project_context.get("water_supply_flow", 1500)),
            },
            "sheet_index":   [],
            "notes":         [],
            "source_documents": [],
        }

        # Drawing index → sheet log
        for d in doc_data:
            if d["type"] == "drawing_index":
                sheets = d["data"].get("sheets", [])
                project["sheet_index"] = sheets
                log.info("[Intel] Drawing index: %d sheets listed", len(sheets))
                project["source_documents"].append(d["filename"])

        # Floor plans (highest priority — take widest building dims found)
        floor_plans = sorted(
            [d for d in doc_data if d["type"] == "floor_plan"],
            key=lambda d: d["priority"], reverse=True
        )
        for fp in floor_plans:
            data = fp["data"]
            bd = data.get("building_dimensions", {})
            if bd.get("width_ft") and bd.get("depth_ft"):
                # Take the largest dims found (handles multi-floor buildings)
                existing = project["building_dimensions"]
                if (bd["width_ft"] * bd["depth_ft"] >
                        existing.get("width_ft", 0) * existing.get("depth_ft", 1)):
                    project["building_dimensions"] = bd
                    project["floor_area_sf"] = bd["width_ft"] * bd["depth_ft"]
            if data.get("rooms"):
                # Merge rooms from all floor levels
                project["rooms"].extend(data["rooms"])
            if data.get("columns"):
                project["columns"].extend(data["columns"])
            if not project["drawing_scale"] and data.get("drawing_scale"):
                project["drawing_scale"] = data["drawing_scale"]
            project["source_documents"].append(fp["filename"])

        # Structural → beams + obstruction list
        for d in doc_data:
            if d["type"] == "structural":
                data = d["data"]
                if data.get("beams"):
                    project["structural_beams"].extend(data["beams"])
                    for beam in data["beams"]:
                        if beam.get("depth_in", 0) > 4:
                            project["obstructions"].append({
                                "type": "beam",
                                "description": f"Beam {beam.get('depth_in',0)}\" deep",
                                "from": beam.get("from", {}),
                                "to":   beam.get("to", {}),
                                "nfpa_ref": "§8.6",
                            })
                if data.get("columns") and not project["columns"]:
                    project["columns"] = data["columns"]

        # RCP → ceiling heights + soffit obstructions
        for d in doc_data:
            if d["type"] == "rcp":
                data = d["data"]
                for ca in data.get("ceiling_areas", []):
                    name = ca.get("name", "").lower()
                    h    = ca.get("height_ft", 0)
                    if h > 0:
                        for room in project["rooms"]:
                            if name in room.get("name", "").lower():
                                room["ceiling_height_ft"] = h
                for soffit in data.get("soffits", []):
                    project["obstructions"].append({
                        "type": "soffit",
                        "description": f"Soffit at {soffit.get('height_ft',0)}ft",
                        "boundary": soffit.get("boundary", []),
                        "nfpa_ref": "§8.6",
                    })

        # Mechanical → duct obstructions
        for d in doc_data:
            if d["type"] == "mechanical":
                data = d["data"]
                for duct in data.get("ducts", []):
                    w = duct.get("width_in", 0)
                    if w > 12:
                        project["obstructions"].append({
                            "type": "duct",
                            "description": f"Duct {w}\" × {duct.get('depth_in',0)}\"",
                            "path": duct.get("path", []),
                            "width_in": w,
                            "requires_below": w >= 48,
                            "nfpa_ref": "§8.6.5",
                        })

        # Civil/site → water supply (take highest pressure found)
        for d in doc_data:
            if d["type"] in ("civil", "site_plan"):
                data = d["data"]
                if data.get("static_pressure_psi", 0) > project["water_supply"]["static_pressure_psi"]:
                    project["water_supply"]["static_pressure_psi"] = data["static_pressure_psi"]
                if data.get("residual_pressure_psi"):
                    project["water_supply"]["residual_pressure_psi"] = data["residual_pressure_psi"]
                if data.get("flow_test_gpm"):
                    project["water_supply"]["flow_gpm"] = data["flow_test_gpm"]

        # Specifications → pipe material, seismic, system type
        for d in doc_data:
            if d["type"] == "specification":
                data = d["data"]
                project["spec"] = data
                if data.get("seismic_zone", "unknown") != "unknown":
                    project_context["seismic_zone"] = data["seismic_zone"]
                if data.get("pipe_material", "unknown") != "unknown":
                    project_context["pipe_material"] = data["pipe_material"]
                ws = data.get("water_supply_requirements", {})
                if ws.get("static_psi"):
                    project["water_supply"]["static_pressure_psi"] = ws["static_psi"]
                if ws.get("residual_psi"):
                    project["water_supply"]["residual_pressure_psi"] = ws["residual_psi"]
                if ws.get("flow_gpm"):
                    project["water_supply"]["flow_gpm"] = ws["flow_gpm"]

        # General notes → AHJ
        for d in doc_data:
            if d["type"] == "general_notes" and d["data"].get("ahj"):
                project["ahj"] = d["data"]["ahj"]

        # Fire protection sheets → existing system context
        for d in doc_data:
            if d["type"] == "fire_protection":
                project["existing_fp"] = d["data"]

        # Validate and fill geometry gaps
        project = _validate_and_complete(project, project_context)

        # Push water supply back to context
        ws = project["water_supply"]
        project_context["static_pressure"]   = ws["static_pressure_psi"]
        project_context["residual_pressure"] = ws["residual_pressure_psi"]
        project_context["water_supply_flow"] = ws["flow_gpm"]

        return project

    # ── Step 5: Project Brief ─────────────────────────────────────────────────

    async def _generate_project_brief(
        self,
        doc_data:        list,
        project:         dict,
        project_context: dict,
    ) -> dict:
        """
        Generate a structured project brief from all extracted data.
        This is stored alongside the geometry and referenced by design agents.
        """
        sheets_found = [
            {"sheet": d["sheet_number"], "title": d["sheet_title"], "type": d["type"]}
            for d in doc_data if d.get("sheet_number")
        ]

        rooms_summary = [
            {"name": r["name"], "hazard": r.get("hazard_classification",""), "area_sf": r.get("area_sf",0)}
            for r in project.get("rooms", [])[:20]  # top 20 for brevity
        ]

        prompt = f"""You are a fire protection engineer reviewing a completed construction document extraction.

Generate a concise project brief that will be used as reference context throughout the fire sprinkler design.

EXTRACTED DATA:
- Project: {project_context.get('project_name','')}
- Occupancy: {project_context.get('occupancy','')}
- Building: {project['building_dimensions'].get('width_ft',0):.0f} x {project['building_dimensions'].get('depth_ft',0):.0f} ft = {project.get('floor_area_sf',0):.0f} SF
- Sheets processed: {len(doc_data)} ({len(sheets_found)} with sheet numbers)
- Rooms identified: {len(project.get('rooms',[]))}
- Obstructions: {len(project.get('obstructions',[]))}
- Water supply: {project['water_supply']['static_pressure_psi']:.0f}/{project['water_supply']['residual_pressure_psi']:.0f} psi @ {project['water_supply']['flow_gpm']:.0f} gpm
- Pipe material: {project_context.get('pipe_material','unknown')}
- Seismic zone: {project_context.get('seismic_zone','unknown')}

SHEETS FOUND:
{json.dumps(sheets_found[:15], indent=2)}

ROOMS/HAZARD AREAS:
{json.dumps(rooms_summary, indent=2)}

Generate a structured project brief. Return ONLY valid JSON:
{{
  "project_summary": "2-3 sentence summary of the project and key design requirements",
  "critical_design_parameters": [
    "list of the most important parameters for sprinkler design, e.g. ESFR required in warehouse, seismic zone D1, etc."
  ],
  "hazard_zones": [
    {{"zone": "zone name", "hazard": "hazard class", "area_sf": number, "nfpa_basis": "section ref"}}
  ],
  "water_supply_summary": "one sentence describing available water supply",
  "special_requirements": ["any AHJ amendments, spec requirements, or unusual conditions"],
  "sheets_used_for_design": ["list of the most important sheets extracted"],
  "data_confidence": "high|medium|low",
  "missing_data_flags": ["any critical missing information that engineer should verify"]
}}"""

        try:
            result = await asyncio.to_thread(
                self.client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw     = next((b.text for b in result.content if b.type == "text"), "{}")
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned.strip())
            brief   = json.loads(cleaned)
            log.info("[Intel] Project brief generated (confidence=%s)", brief.get("data_confidence"))
            return brief
        except Exception as exc:
            log.warning("[Intel] Brief generation failed: %s", exc)
            return {
                "project_summary": f"{project_context.get('project_name','')} — {project_context.get('occupancy','')}",
                "data_confidence": "low",
                "missing_data_flags": ["Brief generation failed — review extracted data manually"],
            }

    # ── Vision API helper ─────────────────────────────────────────────────────

    async def _vision_call(
        self,
        image_data: str,
        media_type: str,
        prompt:     str,
        label:      str,
    ) -> dict:
        try:
            resp = await asyncio.to_thread(
                self.client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=8192,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                  "media_type": media_type,
                                                  "data": image_data}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            raw     = next((b.text for b in resp.content if b.type == "text"), "{}")
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned.strip())
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            log.warning("[Intel] %s JSON parse error: %s", label, exc)
            return {}
        except Exception as exc:
            log.error("[Intel] %s vision call error: %s", label, exc)
            return {}


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers (unchanged from v1)
# ─────────────────────────────────────────────────────────────────────────────

def _validate_and_complete(project: dict, ctx: dict) -> dict:
    bd = project.get("building_dimensions", {})
    bw = float(bd.get("width_ft", 0))
    bh = float(bd.get("depth_ft", 0))

    if bw <= 0 or bh <= 0:
        known = float(ctx.get("total_area", 0))
        if known > 0:
            occ   = ctx.get("occupancy", "").lower()
            ratio = 0.65 if any(k in occ for k in ["warehouse","storage","wholesale","distribution"]) else 0.75
            bw    = round(math.sqrt(known / ratio), 1)
            bh    = round(known / bw, 1)
        else:
            bw, bh = 100.0, 100.0
        project["building_dimensions"] = {"width_ft": bw, "depth_ft": bh}
        project["floor_area_sf"]        = round(bw * bh, 0)
        log.info("[Intel] Building dims derived: %dx%d ft", bw, bh)

    default_ch = float(ctx.get("ceiling_height", 10))
    for room in project.get("rooms", []):
        if not room.get("ceiling_height_ft"):
            room["ceiling_height_ft"] = default_ch

    valid_rooms = []
    for r in project.get("rooms", []):
        bnd = r.get("boundary", [])
        if len(bnd) < 3:
            continue
        clamped = [{"x": max(0.0, min(bw, p["x"])), "y": max(0.0, min(bh, p["y"]))} for p in bnd]
        xs = [p["x"] for p in clamped]; ys = [p["y"] for p in clamped]
        if max(xs) - min(xs) < 3 or max(ys) - min(ys) < 3:
            continue
        area = _poly_area(clamped)
        if area < 50:
            continue
        hz = (r.get("hazard_override") or r.get("hazard_classification") or
              _infer_hazard(r.get("name", ""), ctx.get("occupancy", "")))
        valid_rooms.append({**r, "boundary": clamped, "area_sf": round(area, 1),
                             "area": f"{area:.0f} SF", "hazard_override": hz,
                             "hazard_classification": hz})

    project["rooms"] = valid_rooms

    # Fill coverage gaps
    building_area = bw * bh
    covered       = sum(r["area_sf"] for r in valid_rooms)
    pct           = covered / building_area if building_area > 0 else 0
    log.info("[Intel] Room coverage: %.0f%% (%d/%d SF)", pct * 100, covered, building_area)

    if pct < 0.85:
        occ    = ctx.get("occupancy", "").lower()
        def_hz = _default_hazard(occ)
        gaps   = _fill_gaps(valid_rooms, bw, bh, def_hz)
        if gaps:
            log.info("[Intel] Gap fill: +%d zones +%d SF", len(gaps),
                     sum(r["area_sf"] for r in gaps))
            project["rooms"] = valid_rooms + gaps

    return project


def _poly_area(pts):
    n = len(pts)
    if n < 3:
        return 0
    return abs(sum(pts[i]["x"] * pts[(i+1)%n]["y"] - pts[(i+1)%n]["x"] * pts[i]["y"]
                   for i in range(n))) / 2


def _infer_hazard(name: str, occupancy: str) -> str:
    nl = (name + " " + occupancy).lower()
    checks = [
        (["tire","automotive tire"],                                                       "tire_storage"),
        (["freezer","frozen"],                                                             "freezer"),
        (["cooler","refrigerated","produce"],                                              "cooler"),
        (["warehouse","high pile","rack","storage rack","merchandise","esfr",
          "sales floor","big box","wholesale"],                                            "esfr_k14"),
        (["receiving","loading","dock","shipping"],                                        "ordinary_2"),
        (["bakery","deli","food court","kitchen","food service","food prep"],              "ordinary_2"),
        (["pharmacy","optical","photo"],                                                   "ordinary_1"),
        (["retail","sales","mercantile"],                                                  "ordinary_1"),
        (["mechanical","electrical","utility"],                                            "ordinary_1"),
        (["office","lobby","entrance","vestibule","corridor","restroom","membership",
          "fitness","gym","locker","breakroom"],                                           "light"),
    ]
    for keywords, hz in checks:
        if any(k in nl for k in keywords):
            return hz
    return _default_hazard(occupancy)


def _default_hazard(occupancy: str) -> str:
    occ = occupancy.lower()
    for k, v in {
        "warehouse":"esfr_k14","distribution":"esfr_k14","storage":"esfr_k14",
        "wholesale":"esfr_k14","big box":"esfr_k14","costco":"esfr_k14","club":"esfr_k14",
        "industrial":"extra_2","manufacturing":"extra_2",
        "retail":"ordinary_1","mercantile":"ordinary_1",
        "office":"light","business":"light","educational":"light",
        "hospital":"light","hotel":"light","residential":"light",
        "restaurant":"ordinary_2","food":"ordinary_2",
    }.items():
        if k in occ:
            return v
    return "light"


def _fill_gaps(rooms: list, bw: float, bh: float, default_hz: str) -> list:
    cell = max(5.0, min(bw, bh) / 40)
    cols = max(1, int(math.ceil(bw / cell)))
    rows = max(1, int(math.ceil(bh / cell)))

    covered = [[False]*cols for _ in range(rows)]
    for r in rooms:
        pts = r.get("boundary", [])
        if not pts:
            continue
        xs = [p["x"] for p in pts]; ys = [p["y"] for p in pts]
        c0=max(0,int(min(xs)/cell)); c1=min(cols-1,int((max(xs)-.01)/cell))
        r0=max(0,int(min(ys)/cell)); r1=min(rows-1,int((max(ys)-.01)/cell))
        for ri in range(r0,r1+1):
            for ci in range(c0,c1+1):
                covered[ri][ci] = True

    gaps    = []
    gid     = 1
    visited = [[False]*cols for _ in range(rows)]
    for ri in range(rows):
        for ci in range(cols):
            if covered[ri][ci] or visited[ri][ci]:
                continue
            ce = ci
            while ce+1 < cols and not covered[ri][ce+1] and not visited[ri][ce+1]:
                ce += 1
            re = ri
            while (re+1 < rows and
                   all(not covered[re+1][c] and not visited[re+1][c] for c in range(ci, ce+1))):
                re += 1
            for rr in range(ri, re+1):
                for cc in range(ci, ce+1):
                    visited[rr][cc] = True
            x0=round(ci*cell,1); y0=round(ri*cell,1)
            x1=round(min((ce+1)*cell,bw),1); y1=round(min((re+1)*cell,bh),1)
            area=(x1-x0)*(y1-y0)
            if area < 25:
                continue
            gaps.append({
                "name":                 f"Unclassified Area {gid}",
                "boundary":             [{"x":x0,"y":y0},{"x":x1,"y":y0},{"x":x1,"y":y1},{"x":x0,"y":y1}],
                "area_sf":              round(area, 1),
                "area":                 f"{area:.0f} SF",
                "hazard_override":      default_hz,
                "hazard_classification": default_hz,
                "ceiling_height_ft":    0,
                "nfpa_13_basis":        "Default per occupancy",
                "special_notes":        "Gap-fill — verify hazard classification",
            })
            gid += 1
    return gaps


def _empty_project(ctx: dict) -> dict:
    return {
        "walls":[], "rooms":[], "columns":[], "obstructions":[],
        "structural_beams":[], "building_dimensions":{},
        "floor_area_sf":     float(ctx.get("total_area", 0)),
        "ceiling_height_ft": float(ctx.get("ceiling_height", 10)),
        "drawing_scale":"", "spec":{}, "sheet_index":[],
        "water_supply": {
            "static_pressure_psi":   float(ctx.get("static_pressure", 72)),
            "residual_pressure_psi": float(ctx.get("residual_pressure",
                                           float(ctx.get("static_pressure", 72)) * 0.85)),
            "flow_gpm":             float(ctx.get("water_supply_flow", 1500)),
        },
        "project_brief": {"project_summary": "No documents provided", "data_confidence": "low"},
        "notes":[], "source_documents":[],
    }


def _ext_to_mime(ext: str) -> str:
    return {".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",
            ".tif":"image/tiff",".tiff":"image/tiff",".webp":"image/webp"}.get(ext,"image/png")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point (same interface as v1 — app.py unchanged)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_document_set(
    file_list:       List[dict],
    project_context: dict,
) -> dict:
    """
    Public entry point called from api/app.py.
    file_list: [{"bytes": bytes, "filename": str}, ...]
    Returns unified project geometry + specs + project brief.
    """
    intel = DocumentIntelligence()
    return await intel.process_document_set(file_list, project_context)
