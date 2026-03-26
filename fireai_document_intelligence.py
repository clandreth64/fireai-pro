"""
FireAI Pro — Document Intelligence v1
========================================
Processes a full construction document set and extracts everything
needed to design a 100% code-compliant fire sprinkler system.

Workflow:
  1. Classify every uploaded document (what sheet type is it?)
  2. Extract relevant data from each document type in parallel
  3. Synthesize all extracted data into a unified ProjectModel
  4. Return geometry + specs ready for the NFPA 13 design engine

Document types handled:
  - Architectural floor plans (A-sheets)     → building geometry, room layout
  - Reflected ceiling plans (RCP)            → ceiling heights, soffits, obstructions
  - Structural drawings (S-sheets)           → beams, columns, joists, deck
  - Mechanical/HVAC (M-sheets)              → duct obstructions, equipment
  - Plumbing (P-sheets)                     → existing pipes, drain locations
  - Civil / site plans (C-sheets)           → site utilities, water supply
  - Division 21 specifications               → pipe material, system type, standards
  - General notes / legend sheets            → AHJ requirements, special conditions
  - Fire protection drawings (FP-sheets)     → existing system info if any

Usage:
    from fireai_document_intelligence import DocumentIntelligence
    intel = DocumentIntelligence()
    project = await intel.process_document_set(files, project_context)
    # project is a dict ready to pass to NFPA13DesignEngine
"""

import asyncio, base64, io, json, logging, math, os, re, tempfile
from pathlib import Path
from typing import List

import anthropic

log = logging.getLogger("fireai.intelligence")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.getenv("FIREAI_MODEL", "claude-sonnet-4-20250514")

# Document type → priority for geometry extraction
DOC_PRIORITY = {
    "floor_plan":        10,   # Primary geometry source
    "rcp":               8,    # Ceiling heights
    "structural":        7,    # Beams/columns = obstructions
    "mechanical":        6,    # HVAC = obstructions
    "site_plan":         5,    # Water supply
    "fire_protection":   9,    # Existing FP info
    "specification":     4,    # Pipe material, system type
    "general_notes":     3,
    "plumbing":          3,
    "civil":             2,
    "elevation":         2,
    "section":           2,
    "detail":            1,
    "schedule":          3,
    "unknown":           0,
}

HAZARD_CRITERIA = {
    "light":             {"density":0.10,"area":1500,"max_coverage":225,"max_spacing":15,"k":5.6, "min_psi":7.0, "type":"pendant","esfr":False,"in_rack":False},
    "ordinary_1":        {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":15,"k":5.6, "min_psi":7.0, "type":"pendant","esfr":False,"in_rack":False},
    "ordinary_2":        {"density":0.20,"area":1500,"max_coverage":130,"max_spacing":15,"k":8.0, "min_psi":7.0, "type":"pendant","esfr":False,"in_rack":False},
    "extra_1":           {"density":0.30,"area":2500,"max_coverage":100,"max_spacing":12,"k":11.2,"min_psi":15.0,"type":"upright","esfr":False,"in_rack":False},
    "extra_2":           {"density":0.40,"area":2500,"max_coverage":100,"max_spacing":12,"k":11.2,"min_psi":15.0,"type":"upright","esfr":False,"in_rack":False},
    "esfr_k14":          {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":50.0,"type":"esfr",   "esfr":True, "in_rack":False},
    "esfr_k25":          {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":25.0,"min_psi":15.0,"type":"esfr",   "esfr":True, "in_rack":False},
    "high_pile_class_3": {"density":0.40,"area":2500,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":25.0,"type":"esfr",   "esfr":True, "in_rack":True},
    "tire_storage":      {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":75.0,"type":"esfr",   "esfr":True, "in_rack":True},
    "freezer":           {"density":0.15,"area":2000,"max_coverage":130,"max_spacing":12,"k":5.6, "min_psi":7.0, "type":"upright","esfr":False,"in_rack":False},
    "cooler":            {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":12,"k":5.6, "min_psi":7.0, "type":"pendant","esfr":False,"in_rack":False},
}


class DocumentIntelligence:
    def __init__(self, api_key: str = ANTHROPIC_API_KEY):
        self.client = anthropic.Anthropic(api_key=api_key)

    async def process_document_set(
        self,
        file_list: List[dict],   # [{"bytes": bytes, "filename": str}, ...]
        project_context: dict
    ) -> dict:
        """
        Process a full construction document set.
        Returns a unified geometry + specs dict for the design engine.

        file_list items: {"bytes": bytes, "filename": str}
        """
        log.info(f"[Intel] Processing {len(file_list)} documents")

        if not file_list:
            return _empty_project(project_context)

        # Step 1: Convert all files to images concurrently
        image_tasks = [
            asyncio.create_task(self._to_image(f["bytes"], f["filename"]))
            for f in file_list
        ]
        images = await asyncio.gather(*image_tasks, return_exceptions=True)

        docs = []
        for i, (f, img) in enumerate(zip(file_list, images)):
            if isinstance(img, Exception):
                log.warning(f"[Intel] Could not convert {f['filename']}: {img}")
                continue
            docs.append({
                "filename":   f["filename"],
                "image_data": img[0],
                "media_type": img[1],
                "index":      i,
            })

        if not docs:
            return _empty_project(project_context)

        # Step 2: Classify all documents concurrently
        classify_tasks = [
            asyncio.create_task(self._classify_document(d, project_context))
            for d in docs
        ]
        classifications = await asyncio.gather(*classify_tasks, return_exceptions=True)

        for doc, cls in zip(docs, classifications):
            if isinstance(cls, Exception):
                log.warning(f"[Intel] Classification failed for {doc['filename']}: {cls}")
                doc["classification"] = {"type": "unknown", "priority": 0, "sheet_number": ""}
            else:
                doc["classification"] = cls
            log.info(f"[Intel] {doc['filename']} → {doc['classification']['type']} "
                     f"(priority={doc['classification'].get('priority',0)}, "
                     f"sheet={doc['classification'].get('sheet_number','')})")

        # Step 3: Sort by priority — process most important documents first
        docs.sort(key=lambda d: d["classification"].get("priority", 0), reverse=True)

        # Step 4: Extract data from each relevant document
        relevant = [d for d in docs if d["classification"].get("priority", 0) > 0]
        log.info(f"[Intel] {len(relevant)} relevant documents to process")

        extract_tasks = [
            asyncio.create_task(self._extract_from_document(d, project_context))
            for d in relevant
        ]
        extractions = await asyncio.gather(*extract_tasks, return_exceptions=True)

        doc_data = []
        for doc, extraction in zip(relevant, extractions):
            if isinstance(extraction, Exception):
                log.warning(f"[Intel] Extraction failed for {doc['filename']}: {extraction}")
                continue
            doc_data.append({
                "filename":       doc["filename"],
                "type":           doc["classification"]["type"],
                "priority":       doc["classification"].get("priority", 0),
                "data":           extraction,
            })

        # Step 5: Synthesize all extracted data
        project = await self._synthesize(doc_data, project_context)

        log.info(f"[Intel] Synthesis complete: "
                 f"{project.get('building_dimensions',{}).get('width_ft',0):.0f}ft x "
                 f"{project.get('building_dimensions',{}).get('depth_ft',0):.0f}ft | "
                 f"{len(project.get('rooms',[]))} rooms | "
                 f"{len(project.get('obstructions',[]))} obstructions")
        return project

    # ── Document classification ────────────────────────────────────────────────

    async def _classify_document(self, doc: dict, project_context: dict) -> dict:
        """Identify what type of construction document this is."""
        prompt = f"""You are reviewing a construction document for a fire sprinkler design project.
Project: {project_context.get('project_name','')} | Occupancy: {project_context.get('occupancy','')}
Filename: {doc['filename']}

Identify this document. Return ONLY valid JSON:
{{
  "type": "floor_plan|rcp|structural|mechanical|plumbing|civil|site_plan|fire_protection|specification|general_notes|elevation|section|detail|schedule|unknown",
  "sheet_number": "e.g. A1.1 or S-2 or empty string",
  "sheet_title": "e.g. FIRST FLOOR PLAN",
  "floor_level": "ground|second|basement|roof|all|unknown",
  "is_relevant_for_sprinkler_design": true_or_false,
  "key_info_visible": ["list of key items you can see, e.g. room labels, dimensions, duct layout"],
  "confidence": "high|medium|low"
}}

Type guide:
- floor_plan: architectural plan view showing walls, rooms, doors
- rcp: reflected ceiling plan showing ceiling grid, heights, soffits
- structural: beams, columns, joists, steel framing
- mechanical: HVAC ductwork, equipment, diffusers
- plumbing: drain pipes, water lines, fixtures
- civil/site_plan: site layout, utilities, water supply connections
- fire_protection: sprinkler, standpipe, or fire alarm drawings
- specification: written spec sections (Division 21, 22, etc.)
- general_notes: legend, abbreviations, general conditions
- elevation/section/detail: vertical views, details"""

        result = await self._vision_call(doc["image_data"], doc["media_type"], prompt, "Classify")
        doc_type = result.get("type", "unknown")
        result["priority"] = DOC_PRIORITY.get(doc_type, 0)
        return result

    # ── Data extraction per document type ─────────────────────────────────────

    async def _extract_from_document(self, doc: dict, project_context: dict) -> dict:
        """Extract fire-sprinkler-relevant data from a single document."""
        doc_type = doc["classification"]["type"]
        extractors = {
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
        return await extractor(doc, project_context)

    async def _extract_floor_plan(self, doc: dict, project_context: dict) -> dict:
        """Extract building geometry, rooms, and hazard zones from a floor plan."""
        known_area = float(project_context.get("total_area", 0))
        known_ch   = float(project_context.get("ceiling_height", 10))
        occupancy  = project_context.get("occupancy", "")

        prompt = f"""You are an NFPA 13 fire sprinkler engineer analyzing an architectural floor plan.

Project: {project_context.get('project_name','')}
Occupancy: {occupancy}
Known total area: {known_area} SF
Known ceiling height: {known_ch} ft
Sheet: {doc['classification'].get('sheet_number','')} {doc['classification'].get('sheet_title','')}

EXTRACT all of the following for fire sprinkler design:

1. BUILDING DIMENSIONS — overall width and depth in feet
2. ALL ROOMS — every labeled area with its boundary coordinates
3. HAZARD CLASSIFICATIONS — assign NFPA 13 hazard to every room:
   - light: offices, corridors, lobbies, restrooms, vestibules, membership
   - ordinary_1: retail sales, parking, mechanical, pharmacy, optical
   - ordinary_2: receiving/dock, food service, bakery, deli, food court, kitchen
   - esfr_k14: warehouse floor, high-pile storage, merchandise sales, rack areas
   - esfr_k25: high-pile storage >25ft or Class IV plastics without in-rack
   - tire_storage: tire center, automotive with tires
   - freezer: walk-in freezers; cooler: walk-in coolers

COORDINATE SYSTEM: Origin (0,0) = bottom-left of building. X = right, Y = up. FEET.

CRITICAL: Every square foot must be assigned. No gaps in room coverage.

Return ONLY valid JSON:
{{
  "building_dimensions": {{"width_ft": number, "depth_ft": number}},
  "floor_area_sf": number,
  "drawing_scale": "e.g. 1/8=1-0",
  "rooms": [
    {{
      "name": "area name",
      "hazard_classification": "light|ordinary_1|ordinary_2|esfr_k14|esfr_k25|tire_storage|freezer|cooler",
      "nfpa_13_basis": "§8.5/§22.1/Ch.17",
      "boundary": [{{"x":0,"y":0}},{{"x":W,"y":0}},{{"x":W,"y":D}},{{"x":0,"y":D}}],
      "estimated_area_sf": number,
      "ceiling_height_ft": {known_ch},
      "floor_level": "ground|second|basement",
      "notes": ""
    }}
  ],
  "columns": [{{"x": number, "y": number, "size": "e.g. 12x12"}}],
  "walls": [],
  "doors": [{{"x": number, "y": number, "width_ft": number}}],
  "notes": []
}}"""

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "FloorPlan")

    async def _extract_rcp(self, doc: dict, project_context: dict) -> dict:
        """Extract ceiling heights, soffits, and obstructions from RCP."""
        prompt = f"""Analyze this Reflected Ceiling Plan for fire sprinkler obstruction analysis.

Extract:
1. Ceiling heights in each area (feet)
2. Soffits, bulkheads, dropped ceilings with dimensions
3. Ceiling grid type (exposed structure, T-bar, drywall)
4. Any obstructions >4" wide that could affect sprinkler placement

Return ONLY valid JSON:
{{
  "ceiling_areas": [
    {{
      "name": "area name",
      "height_ft": number,
      "ceiling_type": "exposed_structure|t_bar|drywall|open",
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
      "type": "soffit|beam|duct|column|other",
      "description": "e.g. 24-inch duct",
      "height_ft": number,
      "boundary": [{{"x":number,"y":number}}]
    }}
  ],
  "notes": []
}}"""

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "RCP")

    async def _extract_structural(self, doc: dict, project_context: dict) -> dict:
        """Extract beams, columns, joists — all potential sprinkler obstructions."""
        prompt = f"""Analyze this structural drawing for fire sprinkler obstruction analysis per NFPA 13 §8.6.

Extract all structural members that could require sprinkler relocation or additional heads:

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
      "to":   {{"x":number,"y":number}},
      "depth_in": number,
      "flange_width_in": number,
      "bottom_of_beam_ft": number
    }}
  ],
  "columns": [
    {{"x":number,"y":number,"size":"W8x31","base_plate_in":12}}
  ],
  "deck_type": "metal_deck|concrete|wood|unknown",
  "deck_flute_depth_in": number,
  "mezzanine": false,
  "notes": []
}}"""

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "Structural")

    async def _extract_mechanical(self, doc: dict, project_context: dict) -> dict:
        """Extract HVAC ducts and equipment — major sprinkler obstructions per §8.6.5."""
        prompt = f"""Analyze this mechanical/HVAC drawing for fire sprinkler obstruction analysis per NFPA 13 §8.6.5.

Ducts wider than 4 feet require sprinklers on both sides.
Ducts wider than 1 foot may deflect sprinkler discharge.

Extract all major HVAC elements:

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
  "air_handling_units": [
    {{
      "id": "AHU-1",
      "boundary": [{{"x":number,"y":number}}],
      "height_ft": number
    }}
  ],
  "equipment": [
    {{
      "type": "AHU|RTU|fan_coil|other",
      "location": {{"x":number,"y":number}},
      "footprint_ft": {{"w":number,"d":number}}
    }}
  ],
  "notes": []
}}"""

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "Mechanical")

    async def _extract_civil(self, doc: dict, project_context: dict) -> dict:
        """Extract water supply information from civil/site plans."""
        prompt = f"""Analyze this civil/site plan for fire sprinkler water supply information.

Extract:
1. Water main locations and sizes
2. Fire hydrant locations and distances from building
3. Fire department connection (FDC) location
4. Water utility notes or flow test data if visible
5. Site utilities that may affect sprinkler design

Return ONLY valid JSON:
{{
  "water_mains": [
    {{"diameter_in":number,"material":"CI|DI|PVC","location":"description","pressure_psi":number}}
  ],
  "hydrants": [
    {{"id":"H-1","distance_from_building_ft":number,"flow_gpm":number,"x":number,"y":number}}
  ],
  "fdc_location": {{"x":number,"y":number,"description":""}},
  "static_pressure_psi": number,
  "residual_pressure_psi": number,
  "flow_test_gpm": number,
  "utility_notes": [],
  "notes": []
}}"""

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "Civil")

    async def _extract_fire_protection(self, doc: dict, project_context: dict) -> dict:
        """Extract existing fire protection system information."""
        prompt = f"""Analyze this fire protection drawing.

Extract existing system information relevant to the new design:

Return ONLY valid JSON:
{{
  "system_type": "wet_pipe|dry_pipe|pre_action|deluge|none",
  "existing_riser_location": {{"x":number,"y":number}},
  "existing_pipe_sizes": {{"main_in":number,"branch_in":number}},
  "sprinkler_type": "pendant|upright|esfr|other",
  "coverage_notes": [],
  "hydraulic_reference_point": {{"x":number,"y":number,"psi":number,"gpm":number}},
  "notes": []
}}"""

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "FP")

    async def _extract_specification(self, doc: dict, project_context: dict) -> dict:
        """Extract project specifications from Division 21 or other spec sections."""
        prompt = f"""Analyze this project specification document for fire sprinkler design requirements.

Look for:
- Division 21 (Fire Suppression) requirements
- Pipe material specifications
- Sprinkler type requirements
- System type requirements
- AHJ-specific requirements
- NFPA 13 edition referenced
- Water supply requirements
- Special design requirements

Return ONLY valid JSON:
{{
  "nfpa_13_edition": "2022|2019|2016|unknown",
  "pipe_material": "Schedule 40 Steel|Schedule 10 Steel|CPVC|Copper|unknown",
  "pipe_joining": "threaded|grooved|welded|unknown",
  "system_type": "wet_pipe|dry_pipe|pre_action|antifreeze|unknown",
  "sprinkler_listing_required": "FM|UL|both|either",
  "minimum_design_density": number,
  "seismic_zone": "A|B|C|D|D1|D2|E|unknown",
  "special_requirements": [],
  "ahj_amendments": [],
  "water_supply_requirements": {{"static_psi":number,"residual_psi":number,"flow_gpm":number}},
  "notes": []
}}"""

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "Spec")

    async def _extract_general_notes(self, doc: dict, project_context: dict) -> dict:
        """Extract general notes, legends, and project info."""
        prompt = f"""Analyze this general notes or legend sheet.

Extract any information relevant to fire sprinkler design:
- Building code edition
- Occupancy classification
- Construction type
- Special requirements
- AHJ information
- Project-specific notes

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

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "Notes")

    async def _extract_plumbing(self, doc: dict, project_context: dict) -> dict:
        """Extract plumbing info relevant to water supply."""
        prompt = f"""Analyze this plumbing drawing for fire sprinkler water supply information.

Extract domestic water service information that may inform fire sprinkler supply:

Return ONLY valid JSON:
{{
  "water_service_size_in": number,
  "water_meter_location": {{"x":number,"y":number}},
  "backflow_preventer": {{"type":"","location":{{"x":0,"y":0}}}},
  "building_water_pressure_psi": number,
  "notes": []
}}"""

        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "Plumbing")

    async def _extract_generic(self, doc: dict, project_context: dict) -> dict:
        """Generic extraction for unclassified but relevant documents."""
        prompt = f"""This is a {doc['classification'].get('type','unknown')} construction document.
Extract any information relevant to fire sprinkler system design.
Return ONLY valid JSON: {{"notes": [], "data": {{}}}}"""
        return await self._vision_call(doc["image_data"], doc["media_type"], prompt, "Generic")

    # ── Synthesis ──────────────────────────────────────────────────────────────

    async def _synthesize(self, doc_data: list, project_context: dict) -> dict:
        """
        Synthesize all extracted document data into a unified project model.
        Priority order: floor_plan > fire_protection > structural > rcp > mechanical > spec
        """
        # Initialize with project context defaults
        known_area = float(project_context.get("total_area", 0))
        known_ch   = float(project_context.get("ceiling_height", 10))
        occupancy  = project_context.get("occupancy", "")

        project = {
            "walls":               [],
            "rooms":               [],
            "columns":             [],
            "obstructions":        [],
            "structural_beams":    [],
            "building_dimensions": {},
            "floor_area_sf":       known_area,
            "ceiling_height_ft":   known_ch,
            "drawing_scale":       "",
            "spec":                {},
            "water_supply":        {
                "static_pressure_psi":   float(project_context.get("static_pressure", 72)),
                "residual_pressure_psi": float(project_context.get("residual_pressure",
                                               project_context.get("static_pressure",72)*0.85)),
                "flow_gpm":             float(project_context.get("water_supply_flow", 1500)),
            },
            "notes":               [],
            "source_documents":    [],
        }

        # --- Floor plan data (highest priority) ---
        floor_plans = sorted(
            [d for d in doc_data if d["type"] == "floor_plan"],
            key=lambda d: d["priority"], reverse=True
        )
        for fp in floor_plans:
            data = fp["data"]
            if not project["building_dimensions"].get("width_ft"):
                bd = data.get("building_dimensions", {})
                if bd.get("width_ft") and bd.get("depth_ft"):
                    project["building_dimensions"] = bd
                    project["floor_area_sf"] = bd["width_ft"] * bd["depth_ft"]

            if not project["rooms"] and data.get("rooms"):
                project["rooms"] = data["rooms"]

            if not project["columns"] and data.get("columns"):
                project["columns"] = data["columns"]

            if not project["drawing_scale"] and data.get("drawing_scale"):
                project["drawing_scale"] = data["drawing_scale"]

            project["source_documents"].append(fp["filename"])

        # --- Structural data → beams + columns ---
        for d in doc_data:
            if d["type"] == "structural":
                data = d["data"]
                if data.get("beams"):
                    project["structural_beams"].extend(data["beams"])
                if data.get("columns") and not project["columns"]:
                    project["columns"] = data["columns"]
                # Add structural obstructions
                for beam in data.get("beams", []):
                    if beam.get("depth_in", 0) > 4:
                        project["obstructions"].append({
                            "type":        "beam",
                            "description": f"Steel beam {beam.get('depth_in',0)}\" deep",
                            "from":        beam.get("from", {}),
                            "to":          beam.get("to", {}),
                            "nfpa_ref":    "§8.6",
                        })

        # --- RCP → ceiling heights per room ---
        for d in doc_data:
            if d["type"] == "rcp":
                data = d["data"]
                # Update room ceiling heights from RCP
                for ceiling_area in data.get("ceiling_areas", []):
                    name = ceiling_area.get("name", "").lower()
                    height = ceiling_area.get("height_ft", 0)
                    if height > 0:
                        for room in project["rooms"]:
                            if name in room.get("name", "").lower():
                                room["ceiling_height_ft"] = height
                # Add soffit obstructions
                for soffit in data.get("soffits", []):
                    project["obstructions"].append({
                        "type":        "soffit",
                        "description": f"Soffit at {soffit.get('height_ft',0)}ft",
                        "boundary":    soffit.get("boundary", []),
                        "nfpa_ref":    "§8.6",
                    })

        # --- Mechanical → duct obstructions ---
        for d in doc_data:
            if d["type"] == "mechanical":
                data = d["data"]
                for duct in data.get("ducts", []):
                    w = duct.get("width_in", 0)
                    if w > 12:  # ducts >1ft affect sprinkler placement
                        project["obstructions"].append({
                            "type":          "duct",
                            "description":   f"HVAC duct {w}\" wide × {duct.get('depth_in',0)}\"",
                            "path":          duct.get("path", []),
                            "width_in":      w,
                            "requires_below": w >= 48,  # §8.6.5: >4ft needs heads below
                            "nfpa_ref":      "§8.6.5",
                        })

        # --- Civil/Site → water supply ---
        for d in doc_data:
            if d["type"] in ("civil", "site_plan"):
                data = d["data"]
                if data.get("static_pressure_psi"):
                    project["water_supply"]["static_pressure_psi"]   = data["static_pressure_psi"]
                if data.get("residual_pressure_psi"):
                    project["water_supply"]["residual_pressure_psi"] = data["residual_pressure_psi"]
                if data.get("flow_test_gpm"):
                    project["water_supply"]["flow_gpm"] = data["flow_test_gpm"]

        # --- Specifications ---
        for d in doc_data:
            if d["type"] == "specification":
                data = d["data"]
                project["spec"] = data
                # Apply spec overrides
                if data.get("seismic_zone") and data["seismic_zone"] != "unknown":
                    project_context["seismic_zone"] = data["seismic_zone"]
                if data.get("pipe_material") and data["pipe_material"] != "unknown":
                    project_context["pipe_material"] = data["pipe_material"]
                if data.get("water_supply_requirements", {}).get("static_psi"):
                    ws = data["water_supply_requirements"]
                    if ws.get("static_psi"):
                        project["water_supply"]["static_pressure_psi"]   = ws["static_psi"]
                    if ws.get("residual_psi"):
                        project["water_supply"]["residual_pressure_psi"] = ws["residual_psi"]
                    if ws.get("flow_gpm"):
                        project["water_supply"]["flow_gpm"] = ws["flow_gpm"]

        # --- General notes → occupancy/code data ---
        for d in doc_data:
            if d["type"] == "general_notes":
                data = d["data"]
                if data.get("ahj"):
                    project["ahj"] = data["ahj"]

        # --- Validate and fill gaps ---
        project = self._validate_and_complete(project, project_context)

        # Update project_context with synthesized data
        ws = project["water_supply"]
        project_context["static_pressure"]   = ws["static_pressure_psi"]
        project_context["residual_pressure"]  = ws["residual_pressure_psi"]
        project_context["water_supply_flow"]  = ws["flow_gpm"]

        return project

    def _validate_and_complete(self, project: dict, ctx: dict) -> dict:
        """Ensure the project model is complete and consistent."""
        bd = project.get("building_dimensions", {})
        bw = float(bd.get("width_ft", 0))
        bh = float(bd.get("depth_ft", 0))

        # Set building dims from known area if missing
        if bw <= 0 or bh <= 0:
            known = float(ctx.get("total_area", 0))
            if known > 0:
                occ = ctx.get("occupancy", "").lower()
                ratio = 0.65 if any(k in occ for k in
                    ["warehouse","storage","wholesale","big box","distribution"]) else 0.75
                bw = round(math.sqrt(known / ratio), 1)
                bh = round(known / bw, 1)
            else:
                bw, bh = 100.0, 100.0
            project["building_dimensions"] = {"width_ft": bw, "depth_ft": bh}
            project["floor_area_sf"] = round(bw * bh, 0)
            log.info(f"[Intel] Building dims derived: {bw}x{bh}ft")

        # Set default ceiling height on all rooms
        default_ch = float(ctx.get("ceiling_height", 10))
        for room in project.get("rooms", []):
            if not room.get("ceiling_height_ft"):
                room["ceiling_height_ft"] = default_ch

        # Clamp rooms to building boundary
        valid_rooms = []
        for r in project.get("rooms", []):
            bnd = r.get("boundary", [])
            if len(bnd) < 3:
                continue
            clamped = [{"x": max(0.0, min(bw, p["x"])),
                        "y": max(0.0, min(bh, p["y"]))} for p in bnd]
            xs = [p["x"] for p in clamped]; ys = [p["y"] for p in clamped]
            if max(xs)-min(xs) < 3 or max(ys)-min(ys) < 3:
                continue
            area = _poly_area(clamped)
            if area < 50:
                continue
            hz = (r.get("hazard_override") or
                  r.get("hazard_classification") or
                  _infer_hazard(r.get("name", ""), ctx.get("occupancy", "")))
            valid_rooms.append({**r, "boundary": clamped,
                                "area_sf": round(area, 1),
                                "area": f"{area:.0f} SF",
                                "hazard_override": hz,
                                "hazard_classification": hz})
        project["rooms"] = valid_rooms

        # Check coverage and fill gaps
        building_area = bw * bh
        covered = sum(r["area_sf"] for r in valid_rooms)
        pct = covered / building_area if building_area > 0 else 0
        log.info(f"[Intel] Room coverage: {pct:.0%} ({covered:.0f}/{building_area:.0f} SF)")

        if pct < 0.85:
            occ = ctx.get("occupancy", "").lower()
            def_hz = _default_hazard(occ)
            gaps = _fill_gaps(valid_rooms, bw, bh, def_hz)
            if gaps:
                log.info(f"[Intel] Gap fill: +{len(gaps)} zones "
                         f"+{sum(r['area_sf'] for r in gaps):.0f} SF")
            project["rooms"] = valid_rooms + gaps

        return project

    # ── Vision API call ────────────────────────────────────────────────────────

    async def _vision_call(self, image_data: str, media_type: str,
                            prompt: str, label: str) -> dict:
        try:
            resp = await asyncio.to_thread(
                self.client.messages.create,
                model=CLAUDE_MODEL, max_tokens=8192,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                  "media_type": media_type,
                                                  "data": image_data}},
                    {"type": "text", "text": prompt}
                ]}]
            )
            raw = next((b.text for b in resp.content if b.type == "text"), "{}")
            raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
            raw = re.sub(r"\s*```$", "", raw.strip())
            result = json.loads(raw)
            return result
        except json.JSONDecodeError as e:
            log.error(f"[Intel] {label} JSON error: {e}")
            return {}
        except Exception as e:
            log.error(f"[Intel] {label} error: {e}")
            return {}

    # ── File → image conversion ────────────────────────────────────────────────

    async def _to_image(self, file_bytes: bytes, filename: str) -> tuple:
        """Convert a file (PDF, DXF, image) to base64 image for Vision."""
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            try:
                import pdfplumber, io
                import tempfile, os
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name
                try:
                    with pdfplumber.open(tmp_path) as pdf:
                        if pdf.pages:
                            img = pdf.pages[0].to_image(resolution=200)
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            return base64.b64encode(buf.getvalue()).decode(), "image/png"
                finally:
                    try: os.unlink(tmp_path)
                    except: pass
            except Exception as e:
                log.warning(f"[Intel] PDF render failed for {filename}: {e}")

        # Fallback: send raw bytes
        mt_map = {
            ".pdf": "application/pdf", ".png": "image/png",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".tif": "image/tiff", ".tiff": "image/tiff",
        }
        mt = mt_map.get(ext, "image/png")
        return base64.b64encode(file_bytes).decode(), mt


# ── Helper functions ───────────────────────────────────────────────────────────

def _poly_area(pts):
    n = len(pts)
    if n < 3: return 0
    return abs(sum(pts[i]["x"]*pts[(i+1)%n]["y"] - pts[(i+1)%n]["x"]*pts[i]["y"]
                   for i in range(n))) / 2

def _infer_hazard(name: str, occupancy: str) -> str:
    nl = (name + " " + occupancy).lower()
    checks = [
        (["tire","automotive tire"],                                     "tire_storage"),
        (["freezer","frozen"],                                           "freezer"),
        (["cooler","refrigerated","produce"],                            "cooler"),
        (["warehouse","high pile","rack","storage rack","merchandise",
          "esfr","sales floor","big box","wholesale"],                   "esfr_k14"),
        (["receiving","loading","dock","shipping"],                      "ordinary_2"),
        (["bakery","deli","food court","kitchen","food service"],        "ordinary_2"),
        (["pharmacy","optical"],                                         "ordinary_1"),
        (["retail","sales","mercantile"],                                "ordinary_1"),
        (["mechanical","electrical"],                                    "ordinary_1"),
        (["office","lobby","entrance","vestibule","corridor",
          "restroom","membership"],                                      "light"),
    ]
    for keywords, hz in checks:
        if any(k in nl for k in keywords):
            return hz
    return _default_hazard(occupancy)

def _default_hazard(occupancy: str) -> str:
    occ = occupancy.lower()
    defaults = {
        "warehouse":"esfr_k14","distribution":"esfr_k14","storage":"esfr_k14",
        "wholesale":"esfr_k14","big box":"esfr_k14",
        "industrial":"extra_2","manufacturing":"extra_2",
        "retail":"ordinary_1","mercantile":"ordinary_1",
        "office":"light","business":"light","educational":"light",
        "hospital":"light","hotel":"light","residential":"light",
        "restaurant":"ordinary_2","food":"ordinary_2",
    }
    return next((v for k, v in defaults.items() if k in occ), "light")

def _fill_gaps(rooms: list, bw: float, bh: float, default_hz: str) -> list:
    """Fill uncovered areas with the default hazard classification."""
    cell = max(5.0, min(bw, bh) / 40)
    cols = max(1, int(math.ceil(bw / cell)))
    rows = max(1, int(math.ceil(bh / cell)))
    covered = [[False]*cols for _ in range(rows)]
    for r in rooms:
        pts = r.get("boundary", [])
        if not pts: continue
        xs=[p["x"] for p in pts]; ys=[p["y"] for p in pts]
        c0=max(0,int(min(xs)/cell)); c1=min(cols-1,int((max(xs)-.01)/cell))
        r0=max(0,int(min(ys)/cell)); r1=min(rows-1,int((max(ys)-.01)/cell))
        for ri in range(r0,r1+1):
            for ci in range(c0,c1+1): covered[ri][ci]=True
    gaps=[]; gid=1; visited=[[False]*cols for _ in range(rows)]
    for ri in range(rows):
        for ci in range(cols):
            if covered[ri][ci] or visited[ri][ci]: continue
            ce=ci
            while ce+1<cols and not covered[ri][ce+1] and not visited[ri][ce+1]: ce+=1
            re=ri
            while re+1<rows and all(not covered[re+1][c] and not visited[re+1][c]
                                    for c in range(ci,ce+1)): re+=1
            for rr in range(ri,re+1):
                for cc in range(ci,ce+1): visited[rr][cc]=True
            x0=round(ci*cell,1); y0=round(ri*cell,1)
            x1=round(min((ce+1)*cell,bw),1); y1=round(min((re+1)*cell,bh),1)
            area=(x1-x0)*(y1-y0)
            if area<25: continue
            gaps.append({
                "name": f"Unclassified Area {gid}",
                "boundary": [{"x":x0,"y":y0},{"x":x1,"y":y0},
                              {"x":x1,"y":y1},{"x":x0,"y":y1}],
                "area_sf": round(area,1), "area": f"{area:.0f} SF",
                "hazard_override": default_hz,
                "hazard_classification": default_hz,
                "ceiling_height_ft": 0,
                "nfpa_13_basis": "Default per occupancy",
                "special_notes": "Gap-fill — verify hazard class",
            })
            gid += 1
    return gaps

def _empty_project(ctx: dict) -> dict:
    return {
        "walls":[], "rooms":[], "columns":[], "obstructions":[],
        "structural_beams":[], "building_dimensions":{},
        "floor_area_sf": float(ctx.get("total_area",0)),
        "ceiling_height_ft": float(ctx.get("ceiling_height",10)),
        "drawing_scale":"", "spec":{},
        "water_supply": {
            "static_pressure_psi":   float(ctx.get("static_pressure",72)),
            "residual_pressure_psi": float(ctx.get("residual_pressure",
                                           float(ctx.get("static_pressure",72))*0.85)),
            "flow_gpm":             float(ctx.get("water_supply_flow",1500)),
        },
        "notes":[], "source_documents":[],
    }


# ── Public handler ─────────────────────────────────────────────────────────────

async def handle_document_set(
    file_list: List[dict],
    project_context: dict
) -> dict:
    """
    Public entry point called from api/app.py.
    file_list: [{"bytes": bytes, "filename": str}, ...]
    Returns unified project geometry + specs for NFPA13DesignEngine.
    """
    intel = DocumentIntelligence()
    return await intel.process_document_set(file_list, project_context)
