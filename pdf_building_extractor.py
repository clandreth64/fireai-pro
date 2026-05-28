"""
FireAI Pro — PDF Building Geometry Extractor
=============================================
Extracts building floor plan geometry from uploaded architectural PDF documents.

Strategy:
  1. Render the floor plan page as a high-quality image
  2. Use pdfplumber to detect the drawing scale from title block text
  3. Send to Claude Vision API to extract:
     - Structural grid (column/row labels + dimensions)
     - Room names + which grid cells they occupy
     - Building outline dimensions
  4. Convert grid-based coordinates to feet
  5. Return a geometry dict compatible with NFPA13DesignEngine

Called by api/app.py before running the design engine.
"""

from __future__ import annotations
import asyncio
import base64
import json
import logging
import math
import re
import tempfile
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger("fireai.pdf_extractor")

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    log.warning("pdfplumber not installed — scale detection unavailable")

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
    log.warning("anthropic not installed — Vision extraction unavailable")


# ── Scale detection from PDF text ─────────────────────────────────────────────

_SCALE_PATTERNS = [
    (r'1/8"?\s*=\s*1\'-0"?',    9.0),   # 1/8" = 1'-0"  → 9 pts/ft
    (r'3/16"?\s*=\s*1\'-0"?',  13.5),   # 3/16" = 1'-0" → 13.5 pts/ft
    (r'1/4"?\s*=\s*1\'-0"?',   18.0),   # 1/4" = 1'-0"  → 18 pts/ft
    (r'3/8"?\s*=\s*1\'-0"?',   27.0),   # 3/8" = 1'-0"  → 27 pts/ft
    (r'1/2"?\s*=\s*1\'-0"?',   36.0),   # 1/2" = 1'-0"  → 36 pts/ft
    (r'1"?\s*=\s*1\'-0"?',     72.0),   # 1" = 1'-0"    → 72 pts/ft
    (r'SCALE\s*1:96',           9.0),    # 1:96 = 1/8"   → 9 pts/ft
    (r'SCALE\s*1:48',          18.0),    # 1:48 = 1/4"   → 18 pts/ft
]

def detect_scale(pdf_path: str, page_index: int = 0) -> float:
    """Detect drawing scale from PDF text, return pts_per_ft."""
    if not HAS_PDFPLUMBER:
        return 9.0
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_index]
            text = page.extract_text() or ""
            for pattern, pts_per_ft in _SCALE_PATTERNS:
                if re.search(pattern, text, re.I):
                    log.info("[Extractor] Scale detected: %s → %.1f pts/ft",
                             pattern, pts_per_ft)
                    return pts_per_ft
    except Exception as e:
        log.warning("[Extractor] Scale detection failed: %s", e)
    log.info("[Extractor] No scale detected, defaulting to 1/8\" = 1'-0\"")
    return 9.0   # default: 1/8" = 1'-0"


# ── Image rendering ────────────────────────────────────────────────────────────

def render_floor_plan(pdf_path: str, page_index: int = 0,
                      dpi: int = 100) -> Optional[bytes]:
    """Render a PDF page to JPEG bytes. Page 0-indexed."""
    with tempfile.TemporaryDirectory() as tmp:
        page_num = page_index + 1   # pdftoppm is 1-indexed
        out_base = str(Path(tmp) / "page")
        result   = subprocess.run(
            ["pdftoppm", "-jpeg", "-r", str(dpi),
             "-f", str(page_num), "-l", str(page_num),
             pdf_path, out_base],
            capture_output=True, timeout=60
        )
        if result.returncode != 0:
            log.warning("[Extractor] pdftoppm failed: %s", result.stderr[:200])
            return None
        out_files = sorted(Path(tmp).glob("*.jpg"))
        if not out_files:
            return None
        data = out_files[0].read_bytes()
        log.info("[Extractor] Rendered page %d: %dKB", page_index, len(data)//1024)
        return data


# ── Claude Vision extraction ──────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are analyzing a fire sprinkler engineering drawing showing a building floor plan.
Extract the building geometry needed for NFPA 13 fire sprinkler design.

COORDINATE SYSTEM: Origin (0,0) = bottom-left of building. X = right, Y = up. ALL IN FEET.
The structural column grid (A, B, C... across the top; 1, 2, 3... down the side) defines the space.

EXTRACT:
1. BUILDING DIMENSIONS — read from the title block or dimension strings (overall width × depth in feet)
2. DRAWING SCALE — read from title block (e.g., "SCALE: 1/8\" = 1'-0\"")
3. STRUCTURAL GRID — read the column letter labels (A, B, C...) and row number labels (1, 2, 3...)
   and the DIMENSIONS shown between them in feet-inches (e.g., "4'-5", "10'-0", "24'-7")
4. ALL ROOMS — every labeled area with its grid cell range and approximate boundary in feet
5. WALLS — the major exterior and interior wall lines (as line segments from/to in feet)

For rooms, use the grid dimensions to compute exact boundaries:
- If columns A=0, B=4.4, C=8.8, D=18.8, E=43.3...
- A room labeled "GYMNASIUM" spanning D-F, rows 2-9 → boundary from D's X to F's X, row 2's Y to row 9's Y

Return ONLY valid JSON — no markdown:
{
  "building_dimensions": {"width_ft": NUMBER, "depth_ft": NUMBER},
  "drawing_scale": "1/8\\" = 1'-0\\"",
  "structural_grid": {
    "columns": [
      {"label": "A", "x_ft": 0},
      {"label": "B", "x_ft": 4.4},
      {"label": "C", "x_ft": 8.8}
    ],
    "rows": [
      {"label": "1", "y_ft": 0},
      {"label": "2", "y_ft": 8.3},
      {"label": "3", "y_ft": 18.5}
    ]
  },
  "rooms": [
    {
      "name": "GYMNASIUM",
      "tag": "108",
      "hazard_classification": "light",
      "boundary": [{"x": 43.3, "y": 0}, {"x": 150.2, "y": 0}, {"x": 150.2, "y": 87.5}, {"x": 43.3, "y": 87.5}],
      "area_sf": 9000,
      "ceiling_height_ft": 28,
      "notes": ""
    }
  ],
  "walls": [
    {"points": [{"x": 0, "y": 0}, {"x": 200, "y": 0}], "exterior": true},
    {"points": [{"x": 0, "y": 0}, {"x": 0, "y": 100}], "exterior": true}
  ],
  "columns": []
}

IMPORTANT:
- Read ALL room labels visible on the drawing (GYMNASIUM, STORAGE, KITCHEN, OFFICE, MEN, WOMEN, etc.)
- Use the DIMENSION STRINGS between grid lines to compute exact coordinates, not estimates
- Every square foot of the building must be assigned to a room
- Exterior walls form the perimeter rectangle of the building"""


async def _vision_extract(image_bytes: bytes, media_type: str = "image/jpeg",
                           project_context: dict = None) -> dict:
    """Call Claude Vision API to extract floor plan geometry."""
    if not HAS_ANTHROPIC:
        return {}
    ctx = project_context or {}
    client = anthropic.AsyncAnthropic()
    prompt = EXTRACTION_PROMPT
    if ctx.get("total_area"):
        prompt += f"\n\nNote: Known total building area ≈ {ctx['total_area']:,.0f} SF as reference check."
    if ctx.get("ceiling_height"):
        prompt += f"\nKnown typical ceiling height: {ctx['ceiling_height']} ft."

    # Resize if too large (Vision limit ~5MB)
    if len(image_bytes) > 4_500_000:
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            scale = min(2500/img.width, 2500/img.height, 1.0)
            if scale < 1.0:
                img = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            image_bytes = buf.getvalue()
            log.info("[Extractor] Resized image to %dKB", len(image_bytes)//1024)
        except Exception as e:
            log.warning("[Extractor] Resize failed: %s", e)

    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    try:
        response = await client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": prompt},
                ]
            }]
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.M)
        raw = re.sub(r'```\s*$', '', raw, flags=re.M)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("[Extractor] JSON parse failed: %s", e)
        return {}
    except Exception as e:
        log.warning("[Extractor] Vision API error: %s", e)
        return {}


# ── Grid coordinate resolver ───────────────────────────────────────────────────

def _resolve_grid_coordinates(extracted: dict) -> dict:
    """
    Convert grid-based room definitions to absolute feet coordinates.
    If Vision returned rooms with grid references (e.g., "columns D-F, rows 2-8"),
    resolve them to absolute x,y coordinates using the structural grid.
    """
    grid = extracted.get("structural_grid", {})
    cols = {c["label"]: c["x_ft"] for c in grid.get("columns", [])}
    rows = {str(r["label"]): r["y_ft"] for r in grid.get("rows", [])}

    if not cols or not rows:
        return extracted  # no grid to resolve

    rooms = extracted.get("rooms", [])
    resolved = []
    for room in rooms:
        bnd = room.get("boundary", [])
        if bnd and isinstance(bnd[0], dict) and "x" in bnd[0]:
            resolved.append(room)   # already in feet
            continue
        # Try to resolve from grid_range if present
        gr = room.get("grid_range", {})
        if gr:
            c0 = cols.get(gr.get("col_start",""), None)
            c1 = cols.get(gr.get("col_end",""), None)
            r0 = rows.get(str(gr.get("row_start","")), None)
            r1 = rows.get(str(gr.get("row_end","")), None)
            if all(v is not None for v in [c0,c1,r0,r1]):
                x0,x1 = min(c0,c1), max(c0,c1)
                y0,y1 = min(r0,r1), max(r0,r1)
                room["boundary"] = [{"x":x0,"y":y0},{"x":x1,"y":y0},
                                     {"x":x1,"y":y1},{"x":x0,"y":y1}]
        resolved.append(room)
    extracted["rooms"] = resolved
    return extracted


# ── Synthetic wall generation from rooms ─────────────────────────────────────

def _generate_walls_from_rooms(rooms: list, bw: float, bd: float) -> list:
    """
    Generate wall segments from room boundaries.
    Creates exterior walls from building outline + interior walls from room edges.
    """
    walls = []

    # Exterior walls (building perimeter)
    walls.append({"points":[{"x":0,"y":0},{"x":bw,"y":0}],"exterior":True})   # south
    walls.append({"points":[{"x":bw,"y":0},{"x":bw,"y":bd}],"exterior":True}) # east
    walls.append({"points":[{"x":bw,"y":bd},{"x":0,"y":bd}],"exterior":True}) # north
    walls.append({"points":[{"x":0,"y":bd},{"x":0,"y":0}],"exterior":True})   # west

    # Interior walls: edges of rooms that aren't on the exterior
    edge_tol = 0.5   # ft tolerance
    seen_edges = set()
    for r in rooms:
        bnd = r.get("boundary", [])
        n   = len(bnd)
        for i in range(n):
            p0 = bnd[i]; p1 = bnd[(i+1)%n]
            # Skip edges on exterior wall
            on_ext = (abs(p0["x"]) < edge_tol and abs(p1["x"]) < edge_tol) or \
                     (abs(p0["x"]-bw) < edge_tol and abs(p1["x"]-bw) < edge_tol) or \
                     (abs(p0["y"]) < edge_tol and abs(p1["y"]) < edge_tol) or \
                     (abs(p0["y"]-bd) < edge_tol and abs(p1["y"]-bd) < edge_tol)
            if on_ext:
                continue
            # Deduplicate (same edge from adjacent rooms)
            key = (round(min(p0["x"],p1["x"]),1), round(min(p0["y"],p1["y"]),1),
                   round(max(p0["x"],p1["x"]),1), round(max(p0["y"],p1["y"]),1))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            walls.append({"points":[p0, p1], "exterior": False})

    return walls


# ── Main public entry point ───────────────────────────────────────────────────

async def extract_building_geometry(
    pdf_path: str,
    floor_plan_page_index: int = 0,
    project_context: dict = None,
    dpi: int = 100,
) -> dict:
    """
    Extract building geometry from an architectural floor plan PDF.

    Returns a geometry dict compatible with NFPA13DesignEngine:
    {
        "walls": [...],
        "rooms": [...],
        "columns": [...],
        "building_dimensions": {"width_ft": ..., "depth_ft": ...},
        "drawing_scale": "1/8\" = 1'-0\"",
    }

    Falls back to empty dict (triggering synthetic layout) if extraction fails.
    """
    ctx = project_context or {}
    log.info("[Extractor] Processing: %s (page %d)", pdf_path, floor_plan_page_index)

    # ── Step 1: Detect scale from PDF text ───────────────────────────────────
    pts_per_ft = detect_scale(pdf_path, floor_plan_page_index)

    # ── Step 2: Render floor plan as image ────────────────────────────────────
    image_bytes = render_floor_plan(pdf_path, floor_plan_page_index, dpi=dpi)
    if not image_bytes:
        log.warning("[Extractor] Render failed — returning empty geometry")
        return {}

    # ── Step 3: Vision extraction ─────────────────────────────────────────────
    log.info("[Extractor] Calling Vision API for geometry extraction...")
    extracted = await _vision_extract(image_bytes, "image/jpeg", ctx)

    if not extracted:
        log.warning("[Extractor] Vision returned empty result")
        return {}

    # ── Step 4: Resolve grid coordinates ─────────────────────────────────────
    extracted = _resolve_grid_coordinates(extracted)

    # ── Step 5: Validate and build result ────────────────────────────────────
    bd_dict = extracted.get("building_dimensions", {})
    bw = float(bd_dict.get("width_ft", 0))
    bd = float(bd_dict.get("depth_ft", 0))

    if bw <= 0 or bd <= 0:
        log.warning("[Extractor] No valid building dimensions extracted")
        return {}

    rooms  = extracted.get("rooms", [])
    walls  = extracted.get("walls", [])
    cols   = extracted.get("columns", [])

    # If no walls were extracted, generate them from rooms
    if not walls:
        walls = _generate_walls_from_rooms(rooms, bw, bd)

    result = {
        "walls":               walls,
        "rooms":               rooms,
        "columns":             cols,
        "obstructions":        [],
        "building_dimensions": {"width_ft": round(bw,1), "depth_ft": round(bd,1)},
        "drawing_scale":       extracted.get("drawing_scale", "1/8\" = 1'-0\""),
        "structural_grid":     extracted.get("structural_grid", {}),
        "_pts_per_ft":         pts_per_ft,
        "_source":             "pdf_vision_extraction",
    }

    log.info("[Extractor] Complete: %.0fx%.0fft | %d rooms | %d walls",
             bw, bd, len(rooms), len(walls))
    return result


# ── Floor plan page finder ────────────────────────────────────────────────────

def find_floor_plan_page(pdf_path: str) -> int:
    """
    Find the main floor plan page in a multi-page PDF.

    Priority:
    1. Page with both a SCALE annotation AND the most geometry (lines + curves)
    2. Page with the most geometry overall (most detail = floor plan)
    3. Fall back to page 0

    This correctly handles fire sprinkler drawing sets where page 0 is a cover
    sheet and page 1 is the actual floor plan.
    """
    if not HAS_PDFPLUMBER:
        return 0
    try:
        with pdfplumber.open(pdf_path) as pdf:
            best_page = 0
            best_score = -1
            for i, page in enumerate(pdf.pages):
                text   = (page.extract_text() or "").upper()
                lines  = len(page.lines)
                curves = len(page.curves)
                # Score: geometry density weighted by presence of scale text
                has_scale = any(p in text for p in ["1/8","1/4","3/16","SCALE"])
                has_plan  = any(p in text for p in ["PIPING PLAN","FLOOR PLAN","SPRINKLER"])
                score = (lines + curves) * (2.0 if has_scale else 1.0) * (1.5 if has_plan else 1.0)
                log.debug("[Extractor] Page %d: %d lines %d curves scale=%s score=%.0f",
                          i, lines, curves, has_scale, score)
                if score > best_score:
                    best_score = score
                    best_page  = i
            log.info("[Extractor] Floor plan page: %d (score %.0f)", best_page, best_score)
            return best_page
    except Exception as e:
        log.warning("[Extractor] Page detection error: %s", e)
    return 0


# ── Synchronous wrapper for API use ──────────────────────────────────────────

def extract_building_geometry_sync(
    pdf_path: str,
    project_context: dict = None,
    dpi: int = 100,
) -> dict:
    """Synchronous wrapper around extract_building_geometry."""
    page_idx = find_floor_plan_page(pdf_path)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    extract_building_geometry(pdf_path, page_idx, project_context, dpi)
                )
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(
                extract_building_geometry(pdf_path, page_idx, project_context, dpi)
            )
    except Exception as e:
        log.warning("[Extractor] Sync extraction failed: %s", e)
        return {}
