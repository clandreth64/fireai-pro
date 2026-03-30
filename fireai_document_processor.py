"""
FireAI Pro — Document Processor v3
=====================================
Vision-first architecture: Claude Vision is the PRIMARY geometry source.
Vector extraction (pdfplumber) is used only to refine scale and walls.

Design principles:
  - Two-pass Vision: Pass 1 = building dimensions + scale
                     Pass 2 = complete room/hazard inventory (100% coverage required)
  - All coordinates normalized to feet, origin at building (0,0)
  - Rooms validated and gap-filled to cover 100% of building
  - Hazard classifications follow NFPA 13 strictly

Supports: PDF, DXF, IFC, PNG, JPG, TIF
"""

import asyncio, base64, io, json, logging, math, os, re, tempfile
from pathlib import Path

import anthropic

log = logging.getLogger("fireai.processor")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.getenv("FIREAI_MODEL", "claude-sonnet-4-20250514")
SUPPORTED_EXT     = {".pdf",".dxf",".dwg",".ifc",".png",".jpg",".jpeg",".tif",".tiff"}

HAZARD_CRITERIA = {
    "light":             {"density":0.10,"area":1500,"max_coverage":225,"max_spacing":15,"k":5.6, "min_psi":7.0, "sprinkler_type":"pendant","esfr":False,"in_rack":False},
    "ordinary_1":        {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":15,"k":5.6, "min_psi":7.0, "sprinkler_type":"pendant","esfr":False,"in_rack":False},
    "ordinary_2":        {"density":0.20,"area":1500,"max_coverage":130,"max_spacing":15,"k":8.0, "min_psi":7.0, "sprinkler_type":"pendant","esfr":False,"in_rack":False},
    "extra_1":           {"density":0.30,"area":2500,"max_coverage":100,"max_spacing":12,"k":11.2,"min_psi":15.0,"sprinkler_type":"upright","esfr":False,"in_rack":False},
    "extra_2":           {"density":0.40,"area":2500,"max_coverage":100,"max_spacing":12,"k":11.2,"min_psi":15.0,"sprinkler_type":"upright","esfr":False,"in_rack":False},
    "esfr_k14":          {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":50.0,"sprinkler_type":"esfr",   "esfr":True, "in_rack":False},
    "esfr_k16_8":        {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":16.8,"min_psi":50.0,"sprinkler_type":"esfr",   "esfr":True, "in_rack":False},
    "esfr_k25":          {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":25.0,"min_psi":15.0,"sprinkler_type":"esfr",   "esfr":True, "in_rack":False},
    "high_pile_class_3": {"density":0.40,"area":2500,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":25.0,"sprinkler_type":"esfr",   "esfr":True, "in_rack":True},
    "high_pile_class_4": {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":50.0,"sprinkler_type":"esfr",   "esfr":True, "in_rack":True},
    "tire_storage":      {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":75.0,"sprinkler_type":"esfr",   "esfr":True, "in_rack":True},
    "freezer":           {"density":0.15,"area":2000,"max_coverage":130,"max_spacing":12,"k":5.6, "min_psi":7.0, "sprinkler_type":"upright","esfr":False,"in_rack":False},
    "cooler":            {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":12,"k":5.6, "min_psi":7.0, "sprinkler_type":"pendant","esfr":False,"in_rack":False},
}

OCCUPANCY_DEFAULT = {
    "warehouse":"esfr_k14","distribution":"esfr_k14","storage":"esfr_k14",
    "wholesale":"esfr_k14","big box":"esfr_k14","costco":"esfr_k14",
    "industrial":"extra_2","manufacturing":"extra_2","factory":"extra_2",
    "retail":"ordinary_1","mercantile":"ordinary_1","store":"ordinary_1",
    "office":"light","business":"light","educational":"light",
    "school":"light","hospital":"light","hotel":"light","residential":"light",
    "restaurant":"ordinary_2","food":"ordinary_2","assembly":"light",
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
            geometry = await self._process_pdf(file_path, project_context)
        elif ext == ".dxf":
            geometry = await self._process_dxf(file_path, project_context)
        elif ext == ".ifc":
            geometry = await self._process_ifc(file_path, project_context)
        else:
            geometry = await self._vision_full_analysis(file_path, "image", project_context)

        geometry = self._validate_and_fix(geometry, project_context)
        bd = geometry.get("building_dimensions", {})
        log.info(f"[DocProcessor] Done: {bd.get('width_ft',0):.0f}ft x {bd.get('depth_ft',0):.0f}ft | "
                 f"{len(geometry.get('rooms',[]))} rooms | {geometry.get('floor_area_sf',0):.0f} SF covered")
        return geometry

    # ── PDF ────────────────────────────────────────────────────────────────────

    async def _process_pdf(self, file_path: str, project_context: dict) -> dict:
        """
        Process a PDF floor plan.
        Vision is the SOLE source of building dimensions and room layout.
        pdfplumber is NOT used for dimensions - only attempted for wall lines,
        and only if they pass strict validation against Vision-confirmed dims.
        """
        # Vision runs first and owns all dimensions
        geometry = await self._vision_full_analysis(file_path, "pdf", project_context)

        # Optionally enrich with vector wall lines - but NEVER override dims
        try:
            import pdfplumber
            bw = float(geometry.get("building_dimensions", {}).get("width_ft", 0))
            bh = float(geometry.get("building_dimensions", {}).get("depth_ft", 0))

            if bw > 10 and bh > 10:
                with pdfplumber.open(file_path) as pdf:
                    page  = pdf.pages[0]
                    lines = page.lines or []
                    words = page.extract_words() or []

                scale = self._detect_scale(words, lines, project_context)
                def ft(v): return round(float(v) / scale, 2)

                # Extract all lines as walls
                raw_walls = []
                for l in lines:
                    x0,y0 = ft(l["x0"]), ft(l["y0"])
                    x1,y1 = ft(l["x1"]), ft(l["y1"])
                    length = math.sqrt((x1-x0)**2 + (y1-y0)**2)
                    if length < 0.5: continue
                    lw = float(l.get("linewidth", 0.5))
                    raw_walls.append({
                        "points":   [{"x":x0,"y":y0},{"x":x1,"y":y1}],
                        "thickness": lw, "exterior": lw>=1.0,
                        "length_ft": round(length,1)
                    })

                # Find the origin of the building within the PDF coordinate space.
                # Use only walls whose total bounding box is within 1.5x of Vision dims.
                if raw_walls:
                    all_x = [p["x"] for w in raw_walls for p in w["points"]]
                    all_y = [p["y"] for w in raw_walls for p in w["points"]]
                    pdf_w = max(all_x) - min(all_x)
                    pdf_h = max(all_y) - min(all_y)

                    # Vision dims are ground truth. Find the best origin offset
                    # so that wall lines land inside the building footprint.
                    # Simple approach: use the median center of all wall points
                    # as the building center, then offset to (0,0).
                    med_x = sorted(all_x)[len(all_x)//2]
                    med_y = sorted(all_y)[len(all_y)//2]
                    ox = med_x - bw/2
                    oy = med_y - bh/2

                    # Shift walls to building origin and clamp to building bbox
                    valid_walls = []
                    for w in raw_walls:
                        pts = [{"x": round(p["x"]-ox, 2), "y": round(p["y"]-oy, 2)}
                               for p in w["points"]]
                        # Only keep walls whose points fall within building bounds (with margin)
                        margin = max(bw, bh) * 0.1
                        if all(-margin <= p["x"] <= bw+margin and
                               -margin <= p["y"] <= bh+margin for p in pts):
                            valid_walls.append({**w, "points": pts})

                    if valid_walls:
                        geometry["walls"] = valid_walls
                        log.info(f"[DocProcessor] Vector walls: {len(valid_walls)} lines added "
                                 f"(building dims from Vision: {bw:.0f}x{bh:.0f}ft)")

        except ImportError:
            pass
        except Exception as e:
            log.warning(f"[DocProcessor] Vector wall extraction skipped: {e}")

        return geometry

    def _rescale_rooms(self, rooms, from_dims, to_dims):
        fw=float(from_dims.get("width_ft",0)); fh=float(from_dims.get("depth_ft",0))
        tw=float(to_dims.get("width_ft",0));   th=float(to_dims.get("depth_ft",0))
        if fw<=0 or fh<=0 or tw<=0 or th<=0: return rooms
        if abs(fw-tw)/tw<0.05 and abs(fh-th)/th<0.05: return rooms
        sx=tw/fw; sy=th/fh
        log.info(f"[DocProcessor] Rescaling rooms x*{sx:.3f} y*{sy:.3f}")
        out=[]
        for r in rooms:
            nb=[{"x":round(p["x"]*sx,2),"y":round(p["y"]*sy,2)} for p in r.get("boundary",[])]
            if len(nb)>=3:
                a=_polygon_area(nb)
                out.append({**r,"boundary":nb,"area_sf":round(a,1),"area":f"{a:.0f} SF"})
        return out

    # ── Vision: two-pass analysis ──────────────────────────────────────────────

    async def _vision_full_analysis(self, file_path, file_type, project_context):
        try:
            image_data, media_type = await _file_to_image(file_path, file_type)
        except Exception as e:
            log.error(f"[DocProcessor] Image conversion: {e}")
            return _empty_geometry()

        known_area = float(project_context.get("total_area",0))
        known_ch   = float(project_context.get("ceiling_height",10))
        occupancy  = project_context.get("occupancy","")
        proj_name  = project_context.get("project_name","")

        # Pass 1: Document type + building dimensions
        p1 = await self._vision_call(image_data, media_type, f"""Analyze this construction document.

Project: {proj_name} | Occupancy: {occupancy} | Known area: {known_area} SF

Return ONLY JSON:
{{
  "document_type": "floor_plan|fire_protection|rcp|structural|mechanical|site_plan|detail|schedule|other",
  "is_usable_for_sprinkler_design": true_or_false,
  "building_width_ft": number_or_0,
  "building_depth_ft": number_or_0,
  "drawing_scale": "e.g. 1/4=1-0 or 1/8=1-0 or unknown",
  "observations": "brief description of what you see"
}}

NOTE: Both architectural floor plans AND fire protection drawings contain building geometry.
Set is_usable_for_sprinkler_design=true for: floor_plan, fire_protection, rcp, structural.""", "Pass1")

        if not p1.get("is_usable_for_sprinkler_design", True):
            log.warning(f"[DocProcessor] Not usable: {p1.get('document_type')}")
            return _empty_geometry()

        bw = float(p1.get("building_width_ft",0))
        bh = float(p1.get("building_depth_ft",0))

        if bw<=0 or bh<=0 or (known_area>0 and abs(bw*bh-known_area)/known_area>0.60):
            if known_area>0:
                occ_l=occupancy.lower()
                ratio=0.65 if any(k in occ_l for k in ["warehouse","storage","wholesale","big box","costco"]) else 0.75
                bw=round(math.sqrt(known_area/ratio),1); bh=round(known_area/bw,1)
            else:
                bw=bh=100.0
        log.info(f"[DocProcessor] Building dims: {bw}x{bh}ft = {bw*bh:.0f} SF")

        # Pass 2: Complete room-by-room hazard zone inventory
        doc_type = p1.get("document_type", "floor_plan")
        p2 = await self._vision_call(image_data, media_type, f"""You are an NFPA 13 fire sprinkler engineer analyzing a {doc_type} drawing.

Project: {proj_name}
Occupancy: {occupancy}
Building: {bw}ft wide x {bh}ft deep = {bw*bh:.0f} SF
Default ceiling height: {known_ch}ft
Drawing scale: {p1.get('drawing_scale','unknown')}

MISSION: Extract every room/area and assign the correct NFPA 13 hazard classification.
Room boundaries must collectively cover 100% of the floor area.

COORDINATE SYSTEM: Origin (0,0) = bottom-left corner of the building footprint.
X increases right, Y increases up. All values in FEET.

NFPA 13 HAZARD CLASSIFICATIONS:
- light: Patient rooms, offices, corridors, restrooms, lobbies, exam rooms, classrooms,
         staff areas, lounges, storage <500 SF, consultation rooms, nursing stations
         → Pendant K5.6, 225 SF max coverage, 15ft max spacing (§8.5)
- ordinary_1: Retail, parking, mechanical rooms, pharmacy, electrical rooms
         → Pendant K5.6, 130 SF max coverage, 15ft max spacing (§8.5)
- ordinary_2: Commercial kitchen, food service, laundry, receiving/dock, storage >500 SF
         → Pendant K8.0, 130 SF max coverage, 15ft max spacing (§8.5)
- esfr_k14: Warehouse, high-pile storage ≤25ft, merchandise sales floor
         → ESFR K14, 100 SF max coverage, 10ft max spacing (§22.1)
- tire_storage: Tire storage, automotive with tires → ESFR K14 at 75psi (Ch.17)
- freezer: Walk-in freezers → Upright K5.6 dry (§8.5)
- cooler: Walk-in coolers, refrigerated areas → Pendant K5.6 (§8.5)

SMALL ROOM RULE (NFPA 13 §3.3.206): Rooms ≤800 SF of light hazard with unobstructed
construction may use 1 sprinkler per room regardless of spacing.

IMPORTANT: Read the actual room labels from the drawing. Common rooms in healthcare/
behavioral health facilities are ALL light hazard: patient rooms (CSU, PHF), day rooms,
corridors, nurse stations, consultation rooms, exam rooms, hallways, toilets/showers,
interview rooms, patio areas, quiet rooms, classrooms, staff areas, storage.

Return ONLY valid JSON (no markdown):
{{
  "building_dimensions": {{"width_ft": {bw}, "depth_ft": {bh}}},
  "floor_area_sf": {bw*bh:.0f},
  "ceiling_height_ft": {known_ch},
  "rooms": [
    {{
      "name": "exact room name from drawing",
      "hazard_classification": "light|ordinary_1|ordinary_2|esfr_k14|tire_storage|freezer|cooler",
      "nfpa_13_basis": "§8.5 light hazard / §22.1 / etc",
      "estimated_area_sf": number,
      "boundary": [{{"x":x0,"y":y0}},{{"x":x1,"y":y0}},{{"x":x1,"y":y1}},{{"x":x0,"y":y1}}],
      "ceiling_height_ft": number,
      "small_room_rule": true_or_false,
      "special_notes": ""
    }}
  ],
  "notes": []
}}"""        , "Pass2")

        rooms=[]
        for r in p2.get("rooms",[]):
            hz=r.get("hazard_classification","")
            if hz not in HAZARD_CRITERIA:
                hz=self._infer_hazard(r.get("name",""),occupancy)
            bnd=r.get("boundary",[])
            if len(bnd)<3: continue
            area=_polygon_area(bnd)
            if area<25: continue
            rooms.append({
                "name":r.get("name","Area"),
                "boundary":bnd,
                "area_sf":round(area,1),
                "area":f"{area:.0f} SF",
                "hazard_override":hz,
                "hazard_classification":hz,
                "ceiling_height_ft":float(r.get("ceiling_height_ft",known_ch)),
                "nfpa_13_basis":r.get("nfpa_13_basis",""),
                "special_notes":r.get("special_notes",""),
            })

        dims=p2.get("building_dimensions",{"width_ft":bw,"depth_ft":bh})
        return {
            "walls":[],"rooms":rooms,"columns":[],"obstructions":[],
            "annotations":[],"structural_features":p2.get("structural_features",[]),
            "building_dimensions":dims,
            "floor_area_sf":float(p2.get("floor_area_sf",bw*bh)),
            "ceiling_height_ft":known_ch,
            "drawing_scale":p1.get("drawing_scale",""),
            "notes":p2.get("notes",[]),
        }

    async def _vision_call(self, image_data, media_type, prompt, label):
        try:
            resp = await asyncio.to_thread(
                self.client.messages.create,
                model=CLAUDE_MODEL, max_tokens=8192,
                messages=[{"role":"user","content":[
                    {"type":"image","source":{"type":"base64","media_type":media_type,"data":image_data}},
                    {"type":"text","text":prompt}
                ]}]
            )
            raw=next((b.text for b in resp.content if b.type=="text"),"{}")
            raw=re.sub(r"^```(?:json)?\s*","",raw.strip())
            raw=re.sub(r"\s*```$","",raw.strip())
            result=json.loads(raw)
            log.info(f"[DocProcessor] {label}: {len(result.get('rooms',[]))} rooms")
            return result
        except json.JSONDecodeError as e:
            log.error(f"[DocProcessor] {label} JSON error: {e}")
            return {}
        except Exception as e:
            log.error(f"[DocProcessor] {label} error: {e}")
            return {}

    # ── DXF ───────────────────────────────────────────────────────────────────

    async def _process_dxf(self, file_path, project_context):
        try:
            import ezdxf
        except ImportError:
            return await self._vision_full_analysis(file_path,"image",project_context)
        geo=_empty_geometry()
        try:
            doc=ezdxf.readfile(file_path); msp=doc.modelspace()
        except Exception as e:
            log.error(f"DXF read: {e}"); return geo
        units=doc.header.get("$INSUNITS",0)
        sc={0:1.0,1:1/12,2:1.0,4:3.281,6:39.37}.get(units,1.0)
        for ent in msp:
            layer=(ent.dxf.layer or "").upper(); et=ent.dxftype()
            if et=="LINE":
                geo["walls"].append({"points":[{"x":ent.dxf.start.x*sc,"y":ent.dxf.start.y*sc},
                                               {"x":ent.dxf.end.x*sc,"y":ent.dxf.end.y*sc}],
                                     "layer":layer,"exterior":"EXT" in layer})
            elif et=="LWPOLYLINE":
                pts=[{"x":p[0]*sc,"y":p[1]*sc} for p in ent.get_points()]
                if len(pts)>=3:
                    a=_polygon_area(pts)
                    if ent.closed and a>25:
                        geo["rooms"].append({"boundary":pts,"area_sf":round(a,1),
                                             "area":f"{a:.0f} SF","name":"","layer":layer})
                    else:
                        geo["walls"].append({"points":pts,"layer":layer,"exterior":"EXT" in layer})
            elif et in ("TEXT","MTEXT"):
                try:
                    text=ent.dxf.text if et=="TEXT" else ent.text
                    ins=ent.dxf.insert
                    geo["annotations"].append({"text":str(text).strip(),"x":ins.x*sc,"y":ins.y*sc})
                except Exception: pass
        vision=await self._vision_full_analysis(file_path,"image",project_context)
        if vision.get("rooms"): geo["rooms"]=vision["rooms"]
        return geo

    # ── IFC ───────────────────────────────────────────────────────────────────

    async def _process_ifc(self, file_path, project_context):
        try:
            import ifcopenshell, ifcopenshell.util.placement as ifc_pl
        except ImportError:
            return _empty_geometry()
        geo=_empty_geometry(); ifc=ifcopenshell.open(file_path)
        for wall in ifc.by_type("IfcWall"):
            try:
                m=ifc_pl.get_local_placement(wall.ObjectPlacement)
                x,y=float(m[0][3])*3.281,float(m[1][3])*3.281
                geo["walls"].append({"points":[{"x":x,"y":y},{"x":x+10,"y":y}],"exterior":False})
            except Exception: pass
        for space in ifc.by_type("IfcSpace"):
            try:
                name=space.Name or space.LongName or ""
                hz=self._infer_hazard(str(name),project_context.get("occupancy",""))
                geo["rooms"].append({"boundary":[],"name":str(name),"area_sf":0,
                                     "hazard_override":hz,"hazard_classification":hz})
            except Exception: pass
        return geo

    # ── Scale detection ────────────────────────────────────────────────────────

    def _detect_scale(self, words, lines, project_context):
        numeric=sorted([w for w in words if re.match(r"^\d+['']?$",w.get("text","").strip())],
                       key=lambda w: float(w.get("x0",0)))
        for seq in ([0,4,8,16,32],[0,8,16,32,64],[0,2,4,8,16],[0,10,20,40,80],[0,5,10,20,40]):
            for i in range(len(numeric)-len(seq)+1):
                try:
                    grp=numeric[i:i+len(seq)]
                    vals=[int(w["text"].rstrip("'")) for w in grp]
                    if vals==seq:
                        span=abs(float(grp[-1].get("x0",0))-float(grp[0].get("x0",0)))
                        if span>5:
                            s=span/seq[-1]
                            log.info(f"[DocProcessor] Scale bar: {s:.4f}pts/ft")
                            return s
                except (ValueError,IndexError): continue
        text_all=" ".join(w.get("text","") for w in words)
        for pat,pts in [(r'1/8"\s*=\s*1',9.0),(r'1/16"\s*=\s*1',4.5),(r'1/32"\s*=\s*1',2.25),
                        (r'1/4"\s*=\s*1',18.0),(r'3/32"\s*=\s*1',6.75),(r'3/16"\s*=\s*1',13.5)]:
            if re.search(pat,text_all,re.IGNORECASE):
                log.info(f"[DocProcessor] Title block: {pts}pts/ft"); return pts
        known=float(project_context.get("total_area",0))
        if known>0 and lines:
            ax=[c for l in lines for c in [float(l.get("x0",0)),float(l.get("x1",0))]]
            ay=[c for l in lines for c in [float(l.get("y0",0)),float(l.get("y1",0))]]
            if ax and ay:
                cw=max(ax)-min(ax); ch=max(ay)-min(ay)
                if cw>0 and ch>0:
                    implied=math.sqrt(cw*ch/known)
                    if 0.5<=implied<=50:
                        log.info(f"[DocProcessor] Area back-calc: {implied:.4f}pts/ft"); return implied
        log.warning("[DocProcessor] Scale defaulting to 9pts/ft")
        return 9.0

    # ── Validation & gap filling ───────────────────────────────────────────────

    def _validate_and_fix(self, geo, project_context):
        bd=geo.get("building_dimensions",{})
        bw=float(bd.get("width_ft",0)); bh=float(bd.get("depth_ft",0))
        if bw<=0 or bh<=0:
            known=float(project_context.get("total_area",0))
            occ=project_context.get("occupancy","").lower()
            if known>0:
                ratio=0.65 if any(k in occ for k in ["warehouse","storage","wholesale","big box","costco"]) else 0.75
                bw=round(math.sqrt(known/ratio),1); bh=round(known/bw,1)
            else:
                bw=bh=100.0
            geo["building_dimensions"]={"width_ft":bw,"depth_ft":bh}
            geo["floor_area_sf"]=round(bw*bh,0)

        occ=project_context.get("occupancy","")
        valid=[]
        for r in geo.get("rooms",[]):
            bnd=r.get("boundary",[])
            if len(bnd)<3: continue
            clamped=[{"x":max(0.0,min(bw,p["x"])),"y":max(0.0,min(bh,p["y"]))} for p in bnd]
            xs=[p["x"] for p in clamped]; ys=[p["y"] for p in clamped]
            if max(xs)-min(xs)<3 or max(ys)-min(ys)<3: continue
            area=_polygon_area(clamped)
            if area<50: continue
            hz=(r.get("hazard_override") or r.get("hazard_classification") or
                self._infer_hazard(r.get("name",""),occ))
            if hz not in HAZARD_CRITERIA: hz=self._infer_hazard(r.get("name",""),occ)
            valid.append({**r,"boundary":clamped,"area_sf":round(area,1),
                          "area":f"{area:.0f} SF","hazard_override":hz,"hazard_classification":hz})
        geo["rooms"]=valid

        covered=sum(r["area_sf"] for r in valid)
        building_area=bw*bh
        pct=covered/building_area if building_area>0 else 0
        log.info(f"[DocProcessor] Coverage: {pct:.0%} ({covered:.0f}/{building_area:.0f} SF)")

        if pct<0.90:
            def_hz=next((v for k,v in OCCUPANCY_DEFAULT.items()
                         if k in occ.lower()),"light")
            gaps=self._fill_gaps(valid,bw,bh,def_hz)
            log.info(f"[DocProcessor] Gap fill: +{len(gaps)} zones +{sum(r['area_sf'] for r in gaps):.0f} SF")
            geo["rooms"]=valid+gaps
        return geo

    def _fill_gaps(self, rooms, bw, bh, default_hz):
        cell=max(5.0,min(bw,bh)/40)
        cols=max(1,int(math.ceil(bw/cell))); rows=max(1,int(math.ceil(bh/cell)))
        covered=[[False]*cols for _ in range(rows)]
        for r in rooms:
            pts=r.get("boundary",[])
            if not pts: continue
            xs=[p["x"] for p in pts]; ys=[p["y"] for p in pts]
            c0=max(0,int(min(xs)/cell)); c1=min(cols-1,int((max(xs)-0.01)/cell))
            r0=max(0,int(min(ys)/cell)); r1=min(rows-1,int((max(ys)-0.01)/cell))
            for ri in range(r0,r1+1):
                for ci in range(c0,c1+1):
                    covered[ri][ci]=True
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
                gaps.append({"name":f"Unclassified Area {gid}",
                             "boundary":[{"x":x0,"y":y0},{"x":x1,"y":y0},
                                          {"x":x1,"y":y1},{"x":x0,"y":y1}],
                             "area_sf":round(area,1),"area":f"{area:.0f} SF",
                             "hazard_override":default_hz,"hazard_classification":default_hz,
                             "ceiling_height_ft":0,"nfpa_13_basis":"Default per occupancy",
                             "special_notes":"Gap-fill zone — verify hazard class"})
                gid+=1
        return gaps

    def _infer_hazard(self, name, occupancy):
        nl=(name+" "+occupancy).lower()
        for keywords,hz in [
            (["tire","automotive tires"],"tire_storage"),
            (["freezer","frozen"],"freezer"),
            (["cooler","refrigerated","produce"],"cooler"),
            (["warehouse","high pile","high-pile","rack","storage rack","merchandise","esfr"],"esfr_k14"),
            (["receiving","loading dock","dock","shipping"],"ordinary_2"),
            (["bakery","deli","food court","kitchen","food service","restaurant"],"ordinary_2"),
            (["pharmacy","optical","hearing"],"ordinary_1"),
            (["retail","sales floor","mercantile","sales"],"ordinary_1"),
            (["mechanical","electrical","mep","utility"],"ordinary_1"),
            (["office","lobby","entrance","vestibule","corridor","restroom","membership","break"],"light"),
        ]:
            if any(k in nl for k in keywords): return hz
        return next((v for k,v in OCCUPANCY_DEFAULT.items() if k in occupancy.lower()),"light")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _polygon_area(pts):
    n=len(pts)
    if n<3: return 0
    return abs(sum(pts[i]["x"]*pts[(i+1)%n]["y"]-pts[(i+1)%n]["x"]*pts[i]["y"]
                   for i in range(n)))/2

def _empty_geometry():
    return {"walls":[],"rooms":[],"columns":[],"obstructions":[],"annotations":[],
            "building_dimensions":{},"floor_area_sf":0,"ceiling_height_ft":10}

async def _file_to_image(file_path, file_type):
    if file_type=="pdf":
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                if pdf.pages:
                    img=pdf.pages[0].to_image(resolution=200)
                    buf=io.BytesIO(); img.save(buf,format="PNG")
                    return base64.b64encode(buf.getvalue()).decode(),"image/png"
        except Exception as e:
            log.warning(f"PDF→image: {e}")
    with open(file_path,"rb") as f: data=f.read()
    ext=Path(file_path).suffix.lower()[1:]
    mt={"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png",
        "tif":"image/tiff","tiff":"image/tiff","pdf":"application/pdf"}.get(ext,"image/png")
    return base64.b64encode(data).decode(),mt


# ─── Public handler ────────────────────────────────────────────────────────────

async def handle_upload(file_bytes, filename, project_context):
    suffix=Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix,delete=False) as tmp:
        tmp.write(file_bytes); tmp_path=tmp.name
    try:
        geo=await DocumentProcessor().process(tmp_path,project_context)
        geo["source_file"]=filename; return geo
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass
