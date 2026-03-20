"""
FireAI Pro — Document Processor
=================================
Ingests uploaded construction documents (PDF, DXF, IFC) and extracts
structured geometry that feeds directly into the NFPA 13 design engine.

Supports:
  PDF  — architectural floor plans, stamped drawing sets
  DXF  — AutoCAD drawings, exported from any CAD tool
  IFC  — Revit BIM models, Navisworks exports
  IMG  — scanned drawings (PNG/JPG) via vision AI

Extracted data:
  - Room boundaries + dimensions + occupancy classification
  - Wall geometry (interior/exterior, fire ratings)
  - Structural elements (columns, beams, slabs)
  - Ceiling heights per room
  - Obstructions (HVAC ducts, beams below ceiling)
  - Building footprint and orientation
  - Floor count and floor-to-floor heights

Requires:
  pip install pypdf pdfplumber pillow ezdxf anthropic httpx

Usage:
  from fireai_document_processor import DocumentProcessor
  processor = DocumentProcessor(api_key=ANTHROPIC_API_KEY)
  geometry  = await processor.process("path/to/floor_plan.pdf", project_context)
"""

import asyncio
import base64
import io
import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Optional

import anthropic

log = logging.getLogger("fireai.processor")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.getenv("FIREAI_MODEL", "claude-sonnet-4-20250514")

SUPPORTED_EXTENSIONS = {".pdf", ".dxf", ".dwg", ".ifc", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

# NFPA 13 occupancy hazard classification mapping
OCCUPANCY_HAZARD_MAP = {
    "office":           "light",
    "business":         "light",
    "residential":      "light",
    "educational":      "light",
    "assembly":         "light",
    "retail":           "ordinary_1",
    "mercantile":       "ordinary_1",
    "restaurant":       "ordinary_1",
    "hotel":            "light",
    "warehouse":        "ordinary_2",
    "manufacturing":    "ordinary_2",
    "storage":          "extra",
    "parking":          "ordinary_1",
    "mechanical":       "ordinary_1",
    "electrical":       "ordinary_1",
    "kitchen":          "ordinary_2",
    "laboratory":       "ordinary_2",
    "corridor":         "light",
    "lobby":            "light",
    "stairwell":        "light",
    "restroom":         "light",
}

# NFPA 13 density/area by hazard (gpm/sqft, sqft)
HAZARD_DESIGN_CRITERIA = {
    "light":      {"density": 0.10, "area": 1500, "max_coverage": 225,  "max_spacing": 15},
    "ordinary_1": {"density": 0.15, "area": 1500, "max_coverage": 130,  "max_spacing": 15},
    "ordinary_2": {"density": 0.20, "area": 1500, "max_coverage": 130,  "max_spacing": 15},
    "extra_1":    {"density": 0.30, "area": 2500, "max_coverage": 100,  "max_spacing": 12},
    "extra_2":    {"density": 0.40, "area": 2500, "max_coverage": 100,  "max_spacing": 12},
    "extra":      {"density": 0.40, "area": 2500, "max_coverage": 100,  "max_spacing": 12},
}


# ─── Document processor ───────────────────────────────────────────────────────

class DocumentProcessor:
    def __init__(self, api_key: str = ANTHROPIC_API_KEY):
        self.client = anthropic.Anthropic(api_key=api_key)

    async def process(self, file_path: str, project_context: dict) -> dict:
        """
        Main entry point. Detects file type and routes to appropriate processor.
        Returns structured geometry dict ready for the NFPA 13 design engine.
        """
        path = Path(file_path)
        ext  = path.suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext}. Supported: {SUPPORTED_EXTENSIONS}")

        log.info(f"[DocumentProcessor] Processing {path.name} ({ext})")

        if ext == ".pdf":
            raw = await self._process_pdf(file_path, project_context)
        elif ext == ".dxf":
            raw = await self._process_dxf(file_path, project_context)
        elif ext == ".ifc":
            raw = await self._process_ifc(file_path, project_context)
        elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
            raw = await self._process_image(file_path, project_context)
        else:
            raw = await self._process_pdf(file_path, project_context)

        # Enrich with hazard classification
        enriched = self._classify_hazards(raw, project_context)
        log.info(f"[DocumentProcessor] Extracted {len(enriched.get('rooms',[]))} rooms, "
                 f"{len(enriched.get('walls',[]))} walls, "
                 f"{len(enriched.get('columns',[]))} columns")
        return enriched

    # ── PDF processor ─────────────────────────────────────────────────────────

    async def _process_pdf(self, file_path: str, project_context: dict) -> dict:
        """
        Extracts geometry from PDF floor plans using:
        1. pdfplumber for vector geometry extraction
        2. Claude Vision for intelligent interpretation
        """
        try:
            import pdfplumber
        except ImportError:
            log.warning("pdfplumber not installed — falling back to vision-only. pip install pdfplumber")
            return await self._process_image(file_path, project_context)

        geometry = {"walls": [], "rooms": [], "columns": [], "dimensions": [], "annotations": []}

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_w = float(page.width)
                page_h = float(page.height)
                scale  = _detect_scale(page, project_context)

                # Extract lines (walls)
                for line in (page.lines or []):
                    x0 = float(line.get("x0", 0)) * scale
                    y0 = float(line.get("y0", 0)) * scale
                    x1 = float(line.get("x1", 0)) * scale
                    y1 = float(line.get("y1", 0)) * scale
                    lw = float(line.get("linewidth", 1))
                    length = math.sqrt((x1-x0)**2 + (y1-y0)**2)
                    if length > 0.5:  # ignore very short lines
                        geometry["walls"].append({
                            "points": [{"x": x0, "y": y0}, {"x": x1, "y": y1}],
                            "thickness": lw,
                            "exterior":  lw > 2.0,
                            "page":      page_num + 1,
                        })

                # Extract rectangles (rooms, columns)
                for rect in (page.rects or []):
                    x0 = float(rect.get("x0", 0)) * scale
                    y0 = float(rect.get("y0", 0)) * scale
                    x1 = float(rect.get("x1", 0)) * scale
                    y1 = float(rect.get("y1", 0)) * scale
                    w  = abs(x1 - x0)
                    h  = abs(y1 - y0)
                    area = w * h
                    if area < 0.25:   # tiny — skip
                        continue
                    if w < 3 and h < 3:  # likely a column
                        geometry["columns"].append({"x": (x0+x1)/2, "y": (y0+y1)/2, "width": w, "depth": h})
                    else:
                        geometry["rooms"].append({
                            "boundary":  [{"x":x0,"y":y0},{"x":x1,"y":y0},{"x":x1,"y":y1},{"x":x0,"y":y1}],
                            "area":      f"{area:.0f} SF",
                            "area_sf":   area,
                            "name":      "",
                            "page":      page_num + 1,
                        })

                # Extract text (room names, dimensions)
                for word in (page.extract_words() or []):
                    text = word.get("text", "").strip()
                    if text:
                        geometry["annotations"].append({
                            "text": text,
                            "x":    float(word.get("x0", 0)) * scale,
                            "y":    float(word.get("top", 0)) * scale,
                        })

        # Use Claude Vision to interpret the PDF page images
        vision_result = await self._vision_interpret(file_path, "pdf", project_context)
        return _merge_geometry(geometry, vision_result)

    # ── DXF processor ─────────────────────────────────────────────────────────

    async def _process_dxf(self, file_path: str, project_context: dict) -> dict:
        """Extracts geometry from DXF files using ezdxf."""
        import ezdxf

        geometry = {"walls": [], "rooms": [], "columns": [], "obstructions": [], "annotations": []}

        try:
            doc = ezdxf.readfile(file_path)
            msp = doc.modelspace()
        except Exception as e:
            log.error(f"DXF read error: {e}")
            return geometry

        # Detect unit scale
        units    = doc.header.get("$INSUNITS", 0)
        scale    = {0: 1.0, 1: 1/12, 2: 1.0, 4: 3.281, 6: 39.37}.get(units, 1.0)

        wall_layers   = {"A-WALL", "WALL", "WALLS", "A-WALL-FULL", "A-WALL-PART", "ARCH"}
        column_layers = {"A-COLS", "COLUMNS", "COLUMN", "STRUCT"}
        obstruct_layers= {"MECH", "HVAC", "M-DUCT", "DUCT", "BEAM", "A-BEAM", "STRUCT-BEAM"}

        for entity in msp:
            layer = (entity.dxf.layer or "").upper()
            etype = entity.dxftype()

            # Lines → walls
            if etype == "LINE":
                sx = entity.dxf.start.x * scale
                sy = entity.dxf.start.y * scale
                ex = entity.dxf.end.x   * scale
                ey = entity.dxf.end.y   * scale
                target = "walls" if any(wl in layer for wl in wall_layers) else "walls"
                geometry[target].append({
                    "points":   [{"x": sx, "y": sy}, {"x": ex, "y": ey}],
                    "layer":    layer,
                    "exterior": "EXT" in layer or "EXTERIOR" in layer,
                })

            # LWPolylines → room boundaries or walls
            elif etype == "LWPOLYLINE":
                pts = [{"x": p[0]*scale, "y": p[1]*scale} for p in entity.get_points()]
                if len(pts) >= 3:
                    closed = entity.closed
                    area   = _polygon_area(pts)
                    if closed and area > 25:   # large closed shape → room
                        geometry["rooms"].append({
                            "boundary": pts,
                            "area":     f"{area:.0f} SF",
                            "area_sf":  area,
                            "name":     "",
                            "layer":    layer,
                        })
                    else:
                        geometry["walls"].append({
                            "points": pts, "layer": layer,
                            "exterior": "EXT" in layer,
                            "closed": closed,
                        })

            # Circles/arcs for columns
            elif etype in ("CIRCLE", "INSERT") and any(cl in layer for cl in column_layers):
                x = entity.dxf.center.x * scale if etype == "CIRCLE" else entity.dxf.insert.x * scale
                y = entity.dxf.center.y * scale if etype == "CIRCLE" else entity.dxf.insert.y * scale
                geometry["columns"].append({"x": x, "y": y, "width": 1.5, "depth": 1.5, "layer": layer})

            # Text → annotations
            elif etype in ("TEXT", "MTEXT"):
                try:
                    text = entity.dxf.text if etype == "TEXT" else entity.text
                    ins  = entity.dxf.insert
                    geometry["annotations"].append({
                        "text": str(text).strip(),
                        "x":    ins.x * scale,
                        "y":    ins.y * scale,
                        "layer": layer,
                    })
                except Exception:
                    pass

            # HATCH → room fill areas
            elif etype == "HATCH" and any(rl in layer for rl in {"A-ROOM", "ROOM", "SPACE"}):
                pass  # handled via polyline boundaries above

        # Match annotations to rooms
        geometry = _match_room_names(geometry)
        return geometry

    # ── IFC processor ─────────────────────────────────────────────────────────

    async def _process_ifc(self, file_path: str, project_context: dict) -> dict:
        """Extracts geometry from IFC BIM models using ifcopenshell."""
        try:
            import ifcopenshell
            import ifcopenshell.util.placement as ifc_placement
        except ImportError:
            log.warning("ifcopenshell not installed. pip install ifcopenshell")
            return {"walls": [], "rooms": [], "columns": [], "obstructions": [], "annotations": []}

        geometry = {"walls": [], "rooms": [], "columns": [], "obstructions": [], "annotations": []}
        ifc      = ifcopenshell.open(file_path)

        # Walls
        for wall in ifc.by_type("IfcWall"):
            try:
                matrix = ifc_placement.get_local_placement(wall.ObjectPlacement)
                x = float(matrix[0][3]) * 3.281  # mm → ft
                y = float(matrix[1][3]) * 3.281
                geometry["walls"].append({
                    "points":   [{"x": x, "y": y}, {"x": x+10, "y": y}],
                    "ifc_guid": wall.GlobalId,
                    "exterior": "exterior" in (wall.Name or "").lower(),
                })
            except Exception:
                pass

        # Spaces (rooms)
        for space in ifc.by_type("IfcSpace"):
            try:
                name = space.Name or space.LongName or ""
                area = 0
                for qset in ifc.by_type("IfcElementQuantity"):
                    for q in qset.Quantities:
                        if "Area" in q.Name:
                            area = float(q.AreaValue) * 10.764  # m² → ft²
                geometry["rooms"].append({
                    "boundary": [],
                    "name":     str(name),
                    "area":     f"{area:.0f} SF",
                    "area_sf":  area,
                    "ifc_guid": space.GlobalId,
                })
            except Exception:
                pass

        # Columns
        for col in ifc.by_type("IfcColumn"):
            try:
                matrix = ifc_placement.get_local_placement(col.ObjectPlacement)
                geometry["columns"].append({
                    "x":     float(matrix[0][3]) * 3.281,
                    "y":     float(matrix[1][3]) * 3.281,
                    "width": 1.5, "depth": 1.5,
                })
            except Exception:
                pass

        # Slabs → ceiling heights
        for slab in ifc.by_type("IfcSlab"):
            try:
                matrix = ifc_placement.get_local_placement(slab.ObjectPlacement)
                z = float(matrix[2][3]) * 3.281
                if z > 0:
                    geometry.setdefault("ceiling_elevations", []).append(z)
            except Exception:
                pass

        return geometry

    # ── Vision (image) processor ──────────────────────────────────────────────

    async def _process_image(self, file_path: str, project_context: dict) -> dict:
        """Uses Claude Vision to extract geometry from scanned or image-based drawings."""
        return await self._vision_interpret(file_path, "image", project_context)

    async def _vision_interpret(self, file_path: str, file_type: str, project_context: dict) -> dict:
        """
        Uses Claude's vision capability to intelligently interpret a floor plan
        and extract structured geometry including room names, dimensions, and layout.
        """
        try:
            # Convert to image for vision
            if file_type == "pdf":
                image_data, media_type = await _pdf_to_image(file_path)
            else:
                with open(file_path, "rb") as f:
                    image_data = base64.b64encode(f.read()).decode()
                ext = Path(file_path).suffix.lower()
                media_type = {"jpg":"image/jpeg","jpeg":"image/jpeg",
                              "png":"image/png","tif":"image/tiff","tiff":"image/tiff"}.get(ext[1:], "image/png")

            prompt = f"""Analyze this architectural floor plan drawing for a fire sprinkler design project.

Project context:
- Building: {project_context.get('project_name','')}
- Occupancy: {project_context.get('occupancy','')}
- Floors: {project_context.get('floors',1)}
- Ceiling height: {project_context.get('ceiling_height',10)} ft

Extract ALL of the following and return ONLY a JSON object:

{{
  "building_dimensions": {{"width_ft": number, "depth_ft": number}},
  "floor_area_sf": number,
  "ceiling_height_ft": number,
  "rooms": [
    {{
      "name": string,
      "occupancy_type": string,
      "estimated_area_sf": number,
      "boundary": [{{"x": number, "y": number}}],
      "ceiling_height_ft": number,
      "obstructions": [string]
    }}
  ],
  "walls": [
    {{"points": [{{"x": number, "y": number}}], "exterior": boolean, "fire_rated": boolean}}
  ],
  "columns": [{{"x": number, "y": number, "width_ft": number, "depth_ft": number}}],
  "structural_beams": [{{"from": {{"x":number,"y":number}}, "to": {{"x":number,"y":number}}, "depth_in": number}}],
  "obstructions": [{{"type": string, "x": number, "y": number, "width_ft": number, "depth_ft": number, "height_ft": number}}],
  "doors": [{{"x": number, "y": number, "width_ft": number, "swing": string}}],
  "drawing_scale": string,
  "north_rotation_deg": number,
  "notes": [string]
}}

Use feet for all dimensions. If you cannot determine exact dimensions, estimate based on typical construction.
For boundary coordinates, use the lower-left corner of the drawing as origin (0,0).
Return ONLY the JSON object, no other text."""

            response = await asyncio.to_thread(
                self.client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                        {"type": "text",  "text": prompt},
                    ]
                }]
            )
            raw  = next((b.text for b in response.content if b.type == "text"), "{}")
            data = json.loads(raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip())
            return _normalize_vision_output(data)

        except Exception as e:
            log.error(f"[DocumentProcessor] Vision interpretation failed: {e}")
            return {"walls": [], "rooms": [], "columns": [], "obstructions": [], "annotations": []}

    # ── Hazard classification ─────────────────────────────────────────────────

    def _classify_hazards(self, geometry: dict, project_context: dict) -> dict:
        """
        Assigns NFPA 13 hazard classification to each room based on occupancy type.
        Adds design criteria (density, area, coverage, spacing) per room.
        """
        default_occupancy = project_context.get("occupancy", "Business (Group B)").lower()
        default_hazard    = "light"
        for key in OCCUPANCY_HAZARD_MAP:
            if key in default_occupancy:
                default_hazard = OCCUPANCY_HAZARD_MAP[key]
                break

        for room in geometry.get("rooms", []):
            room_name    = (room.get("name", "") or "").lower()
            room_hazard  = default_hazard
            for key, hazard in OCCUPANCY_HAZARD_MAP.items():
                if key in room_name:
                    room_hazard = hazard
                    break
            room["hazard_classification"] = room_hazard
            room["design_criteria"]       = HAZARD_DESIGN_CRITERIA.get(room_hazard, HAZARD_DESIGN_CRITERIA["light"])

        geometry["default_hazard"]    = default_hazard
        geometry["default_criteria"]  = HAZARD_DESIGN_CRITERIA.get(default_hazard, HAZARD_DESIGN_CRITERIA["light"])
        geometry["ceiling_height_ft"] = project_context.get("ceiling_height", 10)
        return geometry


# ─── Helper functions ─────────────────────────────────────────────────────────

def _detect_scale(page, project_context: dict) -> float:
    """Attempts to detect drawing scale from title block annotations."""
    try:
        words = [w.get("text","") for w in (page.extract_words() or [])]
        text  = " ".join(words)
        import re
        m = re.search(r'1\s*/\s*(\d+)', text)
        if m:
            denom = int(m.group(1))
            return 12.0 / denom  # convert PDF points to feet
        m2 = re.search(r'(\d+)\s*"?\s*=\s*1\s*[\'-]', text)
        if m2:
            inches_per_foot = int(m2.group(1))
            return 1.0 / inches_per_foot
    except Exception:
        pass
    return 1.0 / 96.0  # default: 1/8" = 1'-0"

def _polygon_area(pts: list) -> float:
    """Shoelace formula for polygon area in sq ft."""
    n = len(pts)
    if n < 3:
        return 0
    area = 0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i]["x"] * pts[j]["y"]
        area -= pts[j]["x"] * pts[i]["y"]
    return abs(area) / 2

def _match_room_names(geometry: dict) -> dict:
    """Matches text annotations to the nearest room boundary."""
    for ann in geometry.get("annotations", []):
        text = ann.get("text", "").strip()
        if not text or len(text) < 2 or text.replace(".", "").isdigit():
            continue
        ax, ay = ann.get("x", 0), ann.get("y", 0)
        best_room = None
        best_dist = float("inf")
        for room in geometry.get("rooms", []):
            if room.get("name"):
                continue
            bnd = room.get("boundary", [])
            if not bnd:
                continue
            cx = sum(p["x"] for p in bnd) / len(bnd)
            cy = sum(p["y"] for p in bnd) / len(bnd)
            dist = math.sqrt((ax-cx)**2 + (ay-cy)**2)
            if dist < best_dist:
                best_dist = dist
                best_room = room
        if best_room and best_dist < 50:
            best_room["name"] = text
    return geometry

def _merge_geometry(vector: dict, vision: dict) -> dict:
    """Merges vector-extracted geometry with vision-interpreted geometry."""
    merged = dict(vector)
    # If vision found rooms with names but vector didn't, use vision rooms
    if not any(r.get("name") for r in merged.get("rooms", [])):
        if vision.get("rooms"):
            merged["rooms"] = vision["rooms"]
    # Add obstructions from vision (vector extraction doesn't detect HVAC)
    if vision.get("obstructions"):
        merged["obstructions"] = vision["obstructions"]
    if vision.get("structural_beams"):
        merged["structural_beams"] = vision["structural_beams"]
    if vision.get("building_dimensions"):
        merged["building_dimensions"] = vision["building_dimensions"]
    if vision.get("north_rotation_deg") is not None:
        merged["north_rotation_deg"] = vision["north_rotation_deg"]
    if vision.get("drawing_scale"):
        merged["drawing_scale"] = vision["drawing_scale"]
    return merged

def _normalize_vision_output(data: dict) -> dict:
    """Normalizes vision output to match geometry schema."""
    return {
        "walls":              data.get("walls", []),
        "rooms":              data.get("rooms", []),
        "columns":            data.get("columns", []),
        "obstructions":       data.get("obstructions", []),
        "structural_beams":   data.get("structural_beams", []),
        "doors":              data.get("doors", []),
        "annotations":        [],
        "building_dimensions":data.get("building_dimensions", {}),
        "floor_area_sf":      data.get("floor_area_sf", 0),
        "ceiling_height_ft":  data.get("ceiling_height_ft", 10),
        "north_rotation_deg": data.get("north_rotation_deg", 0),
        "drawing_scale":      data.get("drawing_scale", "1/8\" = 1'-0\""),
        "notes":              data.get("notes", []),
    }

async def _pdf_to_image(pdf_path: str) -> tuple[str, str]:
    """Converts first page of PDF to base64 PNG for vision processing."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if pdf.pages:
                img = pdf.pages[0].to_image(resolution=150)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode(), "image/png"
    except Exception:
        pass
    # Fallback: read raw PDF bytes
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode(), "application/pdf"


# ─── File upload handler ──────────────────────────────────────────────────────

async def handle_upload(file_bytes: bytes, filename: str, project_context: dict) -> dict:
    """
    Saves uploaded file to temp storage and processes it.
    Called from api/app.py upload endpoint.
    """
    suffix = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        processor = DocumentProcessor()
        geometry  = await processor.process(tmp_path, project_context)
        geometry["source_file"] = filename
        return geometry
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
