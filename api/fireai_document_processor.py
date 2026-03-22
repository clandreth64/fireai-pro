"""
FireAI Pro — Document Processor v2
=====================================
Ingests PDF, DXF, and IFC construction documents and extracts
structured geometry for the NFPA 13 design engine.

Key fix in v2:
  - Accurate scale detection from scale bar numbers in drawing
  - Correct coordinate conversion for all PDF scales (1/8, 1/16, 1/32, etc.)
  - Building boundary identification from exterior thick walls
  - Origin shift so all geometry starts at (0, 0)

Supports: PDF, DXF, DWG, IFC, PNG, JPG
Requires: pip install pdfplumber Pillow anthropic
"""

import asyncio, base64, io, json, logging, math, os, re, tempfile
from pathlib import Path

import anthropic

log = logging.getLogger("fireai.processor")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.getenv("FIREAI_MODEL", "claude-sonnet-4-20250514")
SUPPORTED_EXT     = {".pdf",".dxf",".dwg",".ifc",".png",".jpg",".jpeg",".tif",".tiff"}

OCCUPANCY_HAZARD_MAP = {
    "office":"light","business":"light","residential":"light","educational":"light",
    "assembly":"light","hotel":"light","corridor":"light","lobby":"light",
    "retail":"ordinary_1","mercantile":"ordinary_1","restaurant":"ordinary_1",
    "parking":"ordinary_1","mechanical":"ordinary_1",
    "warehouse":"esfr_k14","high pile":"esfr_k14","storage":"high_pile_class_3",
    "tire":"tire_storage","tires":"tire_storage","tire center":"tire_storage",
    "bakery":"ordinary_2","deli":"ordinary_2","food court":"ordinary_2","kitchen":"ordinary_2",
    "pharmacy":"ordinary_1","freezer":"freezer","cooler":"cooler",
    "receiving":"ordinary_2","loading":"ordinary_2","dock":"ordinary_2",
}

HAZARD_DESIGN_CRITERIA = {
    "light":      {"density":0.10,"area":1500,"max_coverage":225,"max_spacing":15},
    "ordinary_1": {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":15},
    "ordinary_2": {"density":0.20,"area":1500,"max_coverage":130,"max_spacing":15},
    "esfr_k14":   {"density":None,"area":None,"max_coverage":100,"max_spacing":10},
    "tire_storage":{"density":None,"area":None,"max_coverage":100,"max_spacing":10},
    "freezer":    {"density":0.15,"area":2000,"max_coverage":130,"max_spacing":12},
    "cooler":     {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":12},
}


class DocumentProcessor:
    def __init__(self, api_key: str = ANTHROPIC_API_KEY):
        self.client = anthropic.Anthropic(api_key=api_key)

    async def process(self, file_path: str, project_context: dict) -> dict:
        path = Path(file_path)
        ext  = path.suffix.lower()
        if ext not in SUPPORTED_EXT:
            raise ValueError(f"Unsupported file type: {ext}")
        log.info(f"[DocProcessor] Processing {path.name}")
        if ext == ".pdf":
            raw = await self._process_pdf(file_path, project_context)
        elif ext == ".dxf":
            raw = await self._process_dxf(file_path, project_context)
        elif ext == ".ifc":
            raw = await self._process_ifc(file_path, project_context)
        else:
            raw = await self._process_image(file_path, project_context)
        enriched = self._classify_hazards(raw, project_context)
        log.info(f"[DocProcessor] Done — {len(enriched.get('rooms',[]))} rooms, "
                 f"{len(enriched.get('walls',[]))} walls, "
                 f"footprint {enriched.get('building_dimensions',{}).get('width_ft',0):.0f}ft × "
                 f"{enriched.get('building_dimensions',{}).get('depth_ft',0):.0f}ft")
        return enriched

    # ── PDF processor ─────────────────────────────────────────────────────────

    async def _process_pdf(self, file_path: str, project_context: dict) -> dict:
        try:
            import pdfplumber
        except ImportError:
            log.warning("pdfplumber not installed — pip install pdfplumber")
            return await self._process_image(file_path, project_context)

        try:
            with pdfplumber.open(file_path) as pdf:
                page  = pdf.pages[0]
                lines = page.lines or []
                rects = page.rects or []
                words = page.extract_words() or []

                # Step 1: Detect scale from scale bar or title block
                scale = self._detect_scale(words, lines, project_context)
                log.info(f"[DocProcessor] Scale: {scale:.4f} pts/ft "
                         f"(equiv 1/{72/scale:.1f}\" = 1'-0\")")

                def ft(v): return round(float(v) / scale, 2)

                # Step 2: Extract walls from lines
                walls = []
                for l in lines:
                    x0, y0 = ft(l["x0"]), ft(l["y0"])
                    x1, y1 = ft(l["x1"]), ft(l["y1"])
                    length = math.sqrt((x1-x0)**2 + (y1-y0)**2)
                    if length < 0.3:
                        continue
                    lw = float(l.get("linewidth", 0.5))
                    walls.append({
                        "points":   [{"x":x0,"y":y0},{"x":x1,"y":y1}],
                        "thickness": lw,
                        "exterior":  lw >= 1.0,
                        "length_ft": round(length, 1),
                    })

                # Step 3: Find building boundary from exterior (thick) walls
                ext = [w for w in walls if w["exterior"] and w["length_ft"] > 30]
                if ext:
                    all_x = [p["x"] for w in ext for p in w["points"]]
                    all_y = [p["y"] for w in ext for p in w["points"]]
                    bx0, by0 = min(all_x), min(all_y)
                    bx1, by1 = max(all_x), max(all_y)
                else:
                    bx0, by0 = 0, 0
                    bx1, by1 = ft(page.width), ft(page.height)

                bw = bx1 - bx0
                bh = by1 - by0
                log.info(f"[DocProcessor] Building boundary: {bw:.0f}ft × {bh:.0f}ft = {bw*bh:.0f} SF")

                # Step 4: Extract rooms from large closed rectangles
                rooms = []
                for r in rects:
                    rx0, ry0 = ft(r["x0"]), ft(r["y0"])
                    rx1, ry1 = ft(r["x1"]), ft(r["y1"])
                    rw = abs(rx1-rx0); rh = abs(ry1-ry0)
                    area = rw * rh
                    if area < 200:
                        continue
                    cx, cy = (rx0+rx1)/2, (ry0+ry1)/2
                    if bx0-20 <= cx <= bx1+20 and by0-20 <= cy <= by1+20:
                        rooms.append({
                            "boundary": [{"x":rx0,"y":ry0},{"x":rx1,"y":ry0},
                                         {"x":rx1,"y":ry1},{"x":rx0,"y":ry1}],
                            "area_sf":  round(area, 1),
                            "area":     f"{area:.0f} SF",
                            "name":     "",
                        })

                # Step 5: Annotations
                annotations = [{"text": w.get("text","").strip(),
                                 "x": ft(w.get("x0",0)),
                                 "y": ft(w.get("top",0))} for w in words if w.get("text","").strip()]

                geometry = {
                    "walls":   walls,
                    "rooms":   rooms,
                    "columns": [],
                    "obstructions": [],
                    "annotations":  annotations,
                    "building_dimensions": {"width_ft": round(bw,1), "depth_ft": round(bh,1)},
                    "floor_area_sf": round(bw*bh, 0),
                    "ceiling_height_ft": float(project_context.get("ceiling_height", 10)),
                    "_scale_pts_per_ft": scale,
                }

                # Step 6: Shift origin to (0,0)
                geometry = self._shift_origin(geometry, bx0, by0)

                # Step 7: Claude Vision for room identification
                vision = await self._vision_interpret(file_path, "pdf", project_context)
                result = _merge_geometry(geometry, vision)

                log.info(f"[DocProcessor] Extracted {len(result.get('walls',[]))} walls, "
                         f"{len(result.get('rooms',[]))} rooms from PDF")
                return result

        except Exception as e:
            log.error(f"[DocProcessor] PDF error: {e}")
            import traceback; traceback.print_exc()
            return {"walls":[],"rooms":[],"columns":[],"obstructions":[],"annotations":[]}

    def _detect_scale(self, words: list, lines: list, project_context: dict) -> float:
        """
        Detects drawing scale using three methods in priority order:
        1. Scale bar numbers (0, 4, 8, 16, 32 markers)
        2. Title block notation (1/8" = 1'-0")
        3. Back-calculation from known building area
        """
        # Method 1: Scale bar detection
        # Scale bars typically show: 0  4'  8'  16'  32'
        numeric = sorted(
            [w for w in words if re.match(r"^\d+['']?$", w.get("text","").strip())],
            key=lambda w: float(w.get("x0", 0))
        )
        scale_seq = [0, 4, 8, 16, 32]
        for i in range(len(numeric) - 4):
            try:
                grp  = numeric[i:i+5]
                vals = [int(w["text"].rstrip("'")) for w in grp]
                if vals == scale_seq:
                    x0   = float(grp[0].get("x0", 0))
                    x32  = float(grp[-1].get("x0", 0))
                    span = abs(x32 - x0)
                    if span > 10:
                        scale = span / 32.0
                        log.info(f"[DocProcessor] Scale bar: {span:.1f}pts=32ft → {scale:.4f}pts/ft")
                        return scale
            except (ValueError, IndexError):
                continue

        # Method 2: Title block scale notation
        text_joined = " ".join(w.get("text","") for w in words)
        title_scales = [
            (r'1/8"\s*=\s*1',   9.000),
            (r'1/16"\s*=\s*1',  4.500),
            (r'1/32"\s*=\s*1',  2.250),
            (r'1/4"\s*=\s*1',   18.00),
            (r'3/32"\s*=\s*1',  6.750),
            (r'1/20"\s*=\s*1',  3.600),
        ]
        for pattern, pts_per_ft in title_scales:
            if re.search(pattern, text_joined, re.IGNORECASE):
                log.info(f"[DocProcessor] Title block scale: {pts_per_ft} pts/ft")
                return pts_per_ft

        # Method 3: Back-calculate from known building area
        total_area = float(project_context.get("total_area", 0))
        if total_area > 0 and lines:
            all_x = [c for l in lines for c in [float(l.get("x0",0)), float(l.get("x1",0))]]
            all_y = [c for l in lines for c in [float(l.get("y0",0)), float(l.get("y1",0))]]
            if all_x and all_y:
                coord_w = max(all_x) - min(all_x)
                coord_h = max(all_y) - min(all_y)
                coord_area = coord_w * coord_h
                if coord_area > 0:
                    implied = math.sqrt(coord_area / total_area)
                    # Only use if it gives a reasonable scale (0.5 to 50 pts/ft)
                    if 0.5 <= implied <= 50:
                        log.info(f"[DocProcessor] Area back-calc scale: {implied:.4f} pts/ft")
                        return implied

        # Default: 1/8" = 1'-0"
        log.warning("[DocProcessor] Scale detection failed — defaulting to 9 pts/ft (1/8\"=1'-0\")")
        return 9.0

    def _shift_origin(self, geometry: dict, ox: float, oy: float) -> dict:
        """Shifts all coordinates so building starts at (0,0)."""
        def sp(pts): return [{"x":round(p["x"]-ox,2),"y":round(p["y"]-oy,2)} for p in pts]
        geometry["walls"]   = [{**w,"points":  sp(w.get("points",  []))} for w in geometry.get("walls",  [])]
        geometry["rooms"]   = [{**r,"boundary":sp(r.get("boundary",[]))} for r in geometry.get("rooms",  [])]
        geometry["columns"] = [{**c,"x":round(c.get("x",0)-ox,2),"y":round(c.get("y",0)-oy,2)} for c in geometry.get("columns",[])]
        return geometry

    # ── DXF processor ─────────────────────────────────────────────────────────

    async def _process_dxf(self, file_path: str, project_context: dict) -> dict:
        try:
            import ezdxf
        except ImportError:
            return {"walls":[],"rooms":[],"columns":[],"obstructions":[],"annotations":[]}

        geometry = {"walls":[],"rooms":[],"columns":[],"obstructions":[],"annotations":[]}
        try:
            doc = ezdxf.readfile(file_path)
            msp = doc.modelspace()
        except Exception as e:
            log.error(f"DXF read error: {e}"); return geometry

        units = doc.header.get("$INSUNITS", 0)
        scale = {0:1.0, 1:1/12, 2:1.0, 4:3.281, 6:39.37}.get(units, 1.0)

        wall_layers = {"A-WALL","WALL","WALLS","A-WALL-FULL","A-WALL-PART","ARCH"}

        for entity in msp:
            layer = (entity.dxf.layer or "").upper()
            etype = entity.dxftype()
            if etype == "LINE":
                sx,sy = entity.dxf.start.x*scale, entity.dxf.start.y*scale
                ex,ey = entity.dxf.end.x*scale,   entity.dxf.end.y*scale
                geometry["walls"].append({"points":[{"x":sx,"y":sy},{"x":ex,"y":ey}],
                                          "layer":layer,"exterior":"EXT" in layer})
            elif etype == "LWPOLYLINE":
                pts = [{"x":p[0]*scale,"y":p[1]*scale} for p in entity.get_points()]
                if len(pts) >= 3:
                    area = _polygon_area(pts)
                    if entity.closed and area > 25:
                        geometry["rooms"].append({"boundary":pts,"area_sf":round(area,1),"area":f"{area:.0f} SF","name":"","layer":layer})
                    else:
                        geometry["walls"].append({"points":pts,"layer":layer,"exterior":"EXT" in layer,"closed":entity.closed})
            elif etype in ("TEXT","MTEXT"):
                try:
                    text = entity.dxf.text if etype=="TEXT" else entity.text
                    ins  = entity.dxf.insert
                    geometry["annotations"].append({"text":str(text).strip(),"x":ins.x*scale,"y":ins.y*scale,"layer":layer})
                except Exception: pass

        geometry = _match_room_names(geometry)
        return geometry

    # ── IFC processor ─────────────────────────────────────────────────────────

    async def _process_ifc(self, file_path: str, project_context: dict) -> dict:
        try:
            import ifcopenshell
            import ifcopenshell.util.placement as ifc_placement
        except ImportError:
            return {"walls":[],"rooms":[],"columns":[],"obstructions":[],"annotations":[]}

        geometry = {"walls":[],"rooms":[],"columns":[],"obstructions":[],"annotations":[]}
        ifc = ifcopenshell.open(file_path)

        for wall in ifc.by_type("IfcWall"):
            try:
                m = ifc_placement.get_local_placement(wall.ObjectPlacement)
                x,y = float(m[0][3])*3.281, float(m[1][3])*3.281
                geometry["walls"].append({"points":[{"x":x,"y":y},{"x":x+10,"y":y}],
                                          "ifc_guid":wall.GlobalId,"exterior":False})
            except Exception: pass

        for space in ifc.by_type("IfcSpace"):
            try:
                name = space.Name or space.LongName or ""
                geometry["rooms"].append({"boundary":[],"name":str(name),"area_sf":0,"area":"","ifc_guid":space.GlobalId})
            except Exception: pass

        return geometry

    # ── Image / Vision processor ──────────────────────────────────────────────

    async def _process_image(self, file_path: str, project_context: dict) -> dict:
        return await self._vision_interpret(file_path, "image", project_context)

    async def _vision_interpret(self, file_path: str, file_type: str, project_context: dict) -> dict:
        """Uses Claude Vision to intelligently interpret a floor plan."""
        try:
            if file_type == "pdf":
                image_data, media_type = await _pdf_to_image(file_path)
            else:
                with open(file_path,"rb") as f: image_data = base64.b64encode(f.read()).decode()
                ext = Path(file_path).suffix.lower()[1:]
                media_type = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","tif":"image/tiff","tiff":"image/tiff"}.get(ext,"image/png")

            prompt = f"""Analyze this architectural floor plan for a fire sprinkler design.

Project: {project_context.get('project_name','')}
Occupancy: {project_context.get('occupancy','')}
Total area: {project_context.get('total_area','')} SF
Ceiling height: {project_context.get('ceiling_height','')} ft

Return ONLY a JSON object with this structure:
{{
  "building_dimensions": {{"width_ft": number, "depth_ft": number}},
  "floor_area_sf": number,
  "ceiling_height_ft": number,
  "rooms": [
    {{
      "name": "room name",
      "occupancy_type": "warehouse/office/retail/etc",
      "estimated_area_sf": number,
      "boundary": [{{"x": number, "y": number}}],
      "ceiling_height_ft": number,
      "special_hazard": "high_pile/tire_storage/freezer/none"
    }}
  ],
  "structural_features": ["columns", "beams", "mezzanine", "etc"],
  "north_rotation_deg": number,
  "drawing_scale": "1/8 = 1-0 or similar",
  "notes": ["any important notes from the drawing"]
}}

Use feet. Origin at bottom-left. Be specific about room names and hazard types."""

            response = await asyncio.to_thread(
                self.client.messages.create,
                model=CLAUDE_MODEL, max_tokens=4096,
                messages=[{"role":"user","content":[
                    {"type":"image","source":{"type":"base64","media_type":media_type,"data":image_data}},
                    {"type":"text","text":prompt}
                ]}]
            )
            raw  = next((b.text for b in response.content if b.type=="text"), "{}")
            data = json.loads(raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip())
            result = _normalize_vision(data)
            log.info(f"[DocProcessor] Vision: {len(result.get('rooms',[]))} rooms identified")
            return result

        except Exception as e:
            log.error(f"[DocProcessor] Vision error: {e}")
            return {"walls":[],"rooms":[],"columns":[],"obstructions":[],"annotations":[]}

    # ── Hazard classification ─────────────────────────────────────────────────

    def _classify_hazards(self, geometry: dict, project_context: dict) -> dict:
        occ = project_context.get("occupancy","Business").lower()
        default_hazard = next((v for k,v in OCCUPANCY_HAZARD_MAP.items() if k in occ), "light")

        for room in geometry.get("rooms", []):
            name = (room.get("name","") or "").lower()
            hazard = room.get("hazard_override") or next((v for k,v in OCCUPANCY_HAZARD_MAP.items() if k in name), default_hazard)
            room["hazard_classification"] = hazard
            room["design_criteria"]       = HAZARD_DESIGN_CRITERIA.get(hazard, HAZARD_DESIGN_CRITERIA["light"])

        geometry["default_hazard"]   = default_hazard
        geometry["default_criteria"] = HAZARD_DESIGN_CRITERIA.get(default_hazard, HAZARD_DESIGN_CRITERIA["light"])
        geometry["ceiling_height_ft"]= float(project_context.get("ceiling_height", 10))
        return geometry


# ─── Helper functions ─────────────────────────────────────────────────────────

def _polygon_area(pts: list) -> float:
    n = len(pts)
    if n < 3: return 0
    area = 0
    for i in range(n):
        j = (i+1) % n
        area += pts[i]["x"]*pts[j]["y"] - pts[j]["x"]*pts[i]["y"]
    return abs(area) / 2

def _match_room_names(geometry: dict) -> dict:
    import math
    for ann in geometry.get("annotations", []):
        text = ann.get("text","").strip()
        if not text or len(text) < 2 or text.replace(".","").isdigit():
            continue
        ax, ay = ann.get("x",0), ann.get("y",0)
        best, best_dist = None, float("inf")
        for room in geometry.get("rooms", []):
            if room.get("name"): continue
            bnd = room.get("boundary",[])
            if not bnd: continue
            cx = sum(p["x"] for p in bnd)/len(bnd)
            cy = sum(p["y"] for p in bnd)/len(bnd)
            dist = math.sqrt((ax-cx)**2+(ay-cy)**2)
            if dist < best_dist: best_dist=dist; best=room
        if best and best_dist < 50:
            best["name"] = text
    return geometry

def _merge_geometry(vector: dict, vision: dict) -> dict:
    merged = dict(vector)
    # Use vision rooms if vector has none with names
    if not any(r.get("name") for r in merged.get("rooms",[])):
        if vision.get("rooms"):
            merged["rooms"] = vision["rooms"]
    if vision.get("obstructions"):      merged["obstructions"]     = vision["obstructions"]
    if vision.get("structural_beams"):  merged["structural_beams"] = vision["structural_beams"]
    if vision.get("building_dimensions") and not merged.get("building_dimensions",{}).get("width_ft"):
        merged["building_dimensions"] = vision["building_dimensions"]
    if vision.get("north_rotation_deg") is not None:
        merged["north_rotation_deg"] = vision["north_rotation_deg"]
    if vision.get("drawing_scale"):
        merged["drawing_scale"] = vision["drawing_scale"]
    return merged

def _normalize_vision(data: dict) -> dict:
    return {
        "walls":               data.get("walls",[]),
        "rooms":               data.get("rooms",[]),
        "columns":             data.get("columns",[]),
        "obstructions":        data.get("obstructions",[]),
        "structural_beams":    data.get("structural_beams",[]),
        "annotations":         [],
        "building_dimensions": data.get("building_dimensions",{}),
        "floor_area_sf":       data.get("floor_area_sf",0),
        "ceiling_height_ft":   data.get("ceiling_height_ft",10),
        "north_rotation_deg":  data.get("north_rotation_deg",0),
        "drawing_scale":       data.get("drawing_scale",""),
        "notes":               data.get("notes",[]),
    }

async def _pdf_to_image(pdf_path: str) -> tuple:
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if pdf.pages:
                img = pdf.pages[0].to_image(resolution=150)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode(), "image/png"
    except Exception: pass
    with open(pdf_path,"rb") as f:
        return base64.b64encode(f.read()).decode(), "application/pdf"


# ─── Public upload handler ────────────────────────────────────────────────────

async def handle_upload(file_bytes: bytes, filename: str, project_context: dict) -> dict:
    """Called from api/app.py — saves file, processes it, returns geometry."""
    suffix = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes); tmp_path = tmp.name
    try:
        processor = DocumentProcessor()
        geometry  = await processor.process(tmp_path, project_context)
        geometry["source_file"] = filename
        return geometry
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass
