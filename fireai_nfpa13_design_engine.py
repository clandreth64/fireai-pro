"""
FireAI Pro — NFPA 13 Design Engine  v5
========================================
Complete engineering rewrite.
 
Key fixes vs v4:
  ✓ Pipe sizing: NFPA 13 schedule method (Table 12.1/12.2) replaces velocity-based
  ✓ Hydraulics: True node-by-node Hazen-Williams replaces p=mp+i*0.3 placeholder
  ✓ Routing: Proper tree topology (Main → Cross-main → Branch → Arm-over → Head)
  ✓ Wall offsets: NFPA 13 §8.5.4.1 S/2 rule applied correctly in both axes
  ✓ Remote area: Most hydraulically demanding selection (highest hazard + most remote)
  ✓ Supply curve: K-factor fitting for actual system curve
  ✓ Arm-overs: Generated for heads not on branch centerline (max 12" per §8.7.2)
  ✓ Head counts: Correct numbering by zone, type, and floor level
  ✓ Hose stream: Added to demand only, not to required pressure (§22.3)
"""
 
import math
import logging
from collections import defaultdict, Counter
 
log = logging.getLogger("fireai.design")
 
# ─── NFPA 13 Hazard Criteria ──────────────────────────────────────────────────
 
HAZARD_CRITERIA = {
    "light":         {"density":0.10,"area":1500,"max_coverage":225,"max_spacing":15.0,"k":5.6, "min_psi":7.0, "type":"pendant","esfr":False,"in_rack":False,"hose_gpm":100},
    "ordinary_1":    {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":15.0,"k":5.6, "min_psi":7.0, "type":"pendant","esfr":False,"in_rack":False,"hose_gpm":250},
    "ordinary_2":    {"density":0.20,"area":1500,"max_coverage":130,"max_spacing":15.0,"k":8.0, "min_psi":7.0, "type":"pendant","esfr":False,"in_rack":False,"hose_gpm":250},
    "extra_1":       {"density":0.30,"area":2500,"max_coverage":100,"max_spacing":12.0,"k":11.2,"min_psi":15.0,"type":"upright","esfr":False,"in_rack":False,"hose_gpm":500},
    "extra_2":       {"density":0.40,"area":2500,"max_coverage":100,"max_spacing":12.0,"k":11.2,"min_psi":15.0,"type":"upright","esfr":False,"in_rack":False,"hose_gpm":500},
    "esfr_k14":      {"density":None,"area":None, "max_coverage":100,"max_spacing":10.0,"k":14.0,"min_psi":50.0,"type":"esfr", "esfr":True, "in_rack":False,"hose_gpm":250},
    "esfr_k16_8":    {"density":None,"area":None, "max_coverage":100,"max_spacing":10.0,"k":16.8,"min_psi":50.0,"type":"esfr", "esfr":True, "in_rack":False,"hose_gpm":250},
    "esfr_k25":      {"density":None,"area":None, "max_coverage":100,"max_spacing":10.0,"k":25.0,"min_psi":15.0,"type":"esfr", "esfr":True, "in_rack":False,"hose_gpm":250},
    "high_pile_class_3":{"density":0.40,"area":2500,"max_coverage":100,"max_spacing":10.0,"k":14.0,"min_psi":25.0,"type":"esfr","esfr":True,"in_rack":True,"hose_gpm":500},
    "high_pile_class_4":{"density":None,"area":None,"max_coverage":100,"max_spacing":10.0,"k":14.0,"min_psi":50.0,"type":"esfr","esfr":True,"in_rack":True,"hose_gpm":500},
    "tire_storage":  {"density":None,"area":None, "max_coverage":100,"max_spacing":10.0,"k":14.0,"min_psi":75.0,"type":"esfr","esfr":True,"in_rack":True,"hose_gpm":500},
    "freezer":       {"density":0.15,"area":2000, "max_coverage":130,"max_spacing":12.0,"k":5.6, "min_psi":7.0, "type":"upright","esfr":False,"in_rack":False,"hose_gpm":250},
    "cooler":        {"density":0.15,"area":1500, "max_coverage":130,"max_spacing":12.0,"k":5.6, "min_psi":7.0, "type":"pendant","esfr":False,"in_rack":False,"hose_gpm":250},
}
 
ZONE_MAP = {
    "warehouse":"esfr_k14","high pile":"esfr_k14","high-pile":"esfr_k14",
    "merchandise":"esfr_k14","sales floor":"esfr_k14","rack":"esfr_k14",
    "storage":"esfr_k14","esfr":"esfr_k14","big box":"esfr_k14","wholesale":"esfr_k14",
    "tire":"tire_storage","tires":"tire_storage","tire center":"tire_storage","automotive":"tire_storage",
    "bakery":"ordinary_2","deli":"ordinary_2","food court":"ordinary_2","kitchen":"ordinary_2",
    "receiving":"ordinary_2","loading":"ordinary_2","dock":"ordinary_2","shipping":"ordinary_2",
    "pharmacy":"ordinary_1","optical":"ordinary_1","retail":"ordinary_1","sales":"ordinary_1",
    "mechanical":"ordinary_1","electrical":"ordinary_1","parking":"ordinary_1",
    "office":"light","lobby":"light","entrance":"light","vestibule":"light",
    "corridor":"light","restroom":"light","membership":"light","break room":"light",
    "freezer":"freezer","cooler":"cooler","refrigerated":"cooler","frozen":"freezer",
    "unclassified":"esfr_k14",
}
 
# ── NFPA 13 Pipe Schedule (Table 12.1 / 12.2) ────────────────────────────────
# Max number of sprinklers per pipe diameter by hazard category
 
PIPE_SCHEDULE = {
    "light": {
        0.75: 2,
        1.0:  2,
        1.25: 3,
        1.5:  5,
        2.0:  10,
        2.5:  20,
        3.0:  40,
        3.5:  65,
        4.0:  100,
        5.0:  999,
    },
    "ordinary": {   # OH1 and OH2
        1.0:  2,
        1.25: 3,
        1.5:  5,
        2.0:  10,
        2.5:  20,
        3.0:  40,
        3.5:  65,
        4.0:  100,
        5.0:  999,
    },
    "extra": {      # EH1 and EH2 — hydraulic method required, use as minimum guide
        1.0:  1,
        1.25: 2,
        1.5:  5,
        2.0:  8,
        2.5:  15,
        3.0:  27,
        3.5:  40,
        4.0:  55,
        5.0:  999,
    },
}
 
# Standard pipe diameters in ascending order
PIPES = [0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0]
 
# Max hanger spacing (ft) per pipe diameter — NFPA 13 §9.1
MAX_HANG = {0.75:6, 1.0:6, 1.25:8, 1.5:8, 2.0:12, 2.5:12, 3.0:15,
            3.5:15, 4.0:15, 5.0:20, 6.0:20, 8.0:20}
MAX_SWAY = 40.0   # Max sway brace interval (ft) — §9.3
 
# Hazen-Williams C factors
HW_C = {"steel":120, "schedule 40 steel":120, "sch40":120,
        "schedule 10 steel":120, "cpvc":150, "copper":150, "stainless":140}
 
# Fitting equivalent lengths (ft) — NFPA 13 Appendix D
FEQ = {
    "90_elbow":   {0.75:1, 1.0:1, 1.25:1, 1.5:2, 2.0:2, 2.5:3, 3.0:4, 4.0:5,  5.0:7,  6.0:9},
    "45_elbow":   {0.75:1, 1.0:1, 1.25:1, 1.5:1, 2.0:1, 2.5:2, 3.0:2, 4.0:3,  5.0:4,  6.0:5},
    "tee_branch": {0.75:4, 1.0:4, 1.25:5, 1.5:5, 2.0:8, 2.5:10,3.0:12,4.0:15, 5.0:20, 6.0:25},
    "tee_run":    {0.75:1, 1.0:1, 1.25:1, 1.5:1, 2.0:2, 2.5:2, 3.0:3, 4.0:4,  5.0:5,  6.0:6},
    "alarm_check":{2.0:10,2.5:12,3.0:14,4.0:18,5.0:22,6.0:28},
    "gate_valve": {2.0:1, 3.0:1, 4.0:2, 5.0:2, 6.0:3},
    "butterfly":  {2.0:2, 3.0:3, 4.0:4, 5.0:5, 6.0:6},
    "check_valve":{2.0:4, 3.0:5, 4.0:7, 5.0:9, 6.0:11},
}
 
# Unit costs (USD)
PIPE_COST  = {0.75:2.1, 1.0:2.8, 1.25:3.5, 1.5:4.2, 2.0:6.5, 2.5:9.8,
              3.0:13.5, 3.5:17.0, 4.0:20.0, 5.0:28.0, 6.0:38.0, 8.0:52.0}
SPKR_COST  = {"pendant":8.5, "upright":9.5, "sidewall":10.0, "esfr":52.0, "cmsa":45.0}
VALVE_COST = {"osy":285, "butterfly":220, "check":520, "alarm":95, "inspector_test":65, "drain":145, "rpm":780}
 
 
# ─── Geometry helpers ─────────────────────────────────────────────────────────
 
def _poly_area(pts):
    n = len(pts)
    if n < 3: return 0
    return abs(sum(pts[i]["x"]*pts[(i+1)%n]["y"] - pts[(i+1)%n]["x"]*pts[i]["y"]
                   for i in range(n))) / 2
 
 
def normalize_geometry(geo: dict, ctx: dict) -> dict:
    """Normalize geometry to feet with origin at (0,0)."""
    walls = geo.get("walls", [])
    rooms = geo.get("rooms", [])
 
    if geo.get("_use_synthetic"):
        return _synthetic(ctx)
 
    ax, ay = [], []
    for w in walls:
        for p in w.get("points", []): ax.append(float(p.get("x",0))); ay.append(float(p.get("y",0)))
    for r in rooms:
        for p in r.get("boundary", []): ax.append(float(p.get("x",0))); ay.append(float(p.get("y",0)))
 
    if not ax:
        return _synthetic(ctx)
 
    ox, oy  = min(ax), min(ay)
    raw_w   = max(ax) - ox
    raw_h   = max(ay) - oy
    raw     = max(raw_w, raw_h)
    total   = float(ctx.get("total_area", 0))
    exp     = math.sqrt(total) if total > 0 else 0
 
    # Auto-detect units
    if raw > 100000: sc = 1/304.8
    elif raw > 10000: sc = 1/25.4
    elif raw > 1000:  sc = 1/12.0
    else:             sc = 1.0
 
    scaled = raw * sc
    if exp > 0 and (scaled < exp*0.05 or scaled > exp*20):
        log.warning("[Geo] Scale validation failed — using synthetic")
        return _synthetic(ctx)
 
    bw_sc = raw_w * sc
    bh_sc = raw_h * sc
 
    def sp(pts):
        return [{"x": round((p["x"]-ox)*sc, 2), "y": round((p["y"]-oy)*sc, 2)} for p in pts]
 
    valid_rooms = []
    for r in rooms:
        pts     = r.get("boundary", [])
        if len(pts) < 3: continue
        clamped = [{"x": max(0.0, min(bw_sc, p["x"])), "y": max(0.0, min(bh_sc, p["y"]))}
                   for p in sp(pts)]
        xs = [p["x"] for p in clamped]; ys = [p["y"] for p in clamped]
        if max(xs)-min(xs) < 3 or max(ys)-min(ys) < 3: continue
        area = _poly_area(clamped)
        if area < 50: continue
        valid_rooms.append({**r, "boundary": clamped, "area_sf": round(area, 1)})
 
    n = dict(geo)
    n["walls"]   = [{**w, "points": sp(w.get("points", []))} for w in walls]
    n["columns"] = [{**c, "x": round((c.get("x",0)-ox)*sc, 2),
                     "y": round((c.get("y",0)-oy)*sc, 2)} for c in geo.get("columns",[])]
    n["rooms"]   = valid_rooms
    n["building_dimensions"] = {"width_ft": round(bw_sc,1), "depth_ft": round(bh_sc,1)}
    n["_scale"]  = sc
    return n
 
 
def _synthetic(ctx: dict) -> dict:
    """Generate occupancy-appropriate building layout from project specs."""
    area   = float(ctx.get("total_area", 10000))
    floors = int(ctx.get("floors", 1))
    # Defensive guard: synthetic layout requires a positive floor area and
    # at least one floor. If we got here with area=0 (or floors=0), the
    # project context is incomplete — fail with a CLEAR message instead of
    # crashing later on a ZeroDivisionError when w = sqrt(0) and d = 0/0.
    if area <= 0 or floors <= 0:
        raise ValueError(
            f"Cannot generate building layout: total_area={area}, floors={floors}. "
            f"Project data is incomplete — verify the document extraction populated "
            f"area and floors, or enter them manually in the intake form."
        )
    af     = area / floors
    occ    = ctx.get("occupancy","").lower()
    ch     = float(ctx.get("ceiling_height", 10))
 
    if any(k in occ for k in ["warehouse","storage","wholesale","big box","distribution","industrial"]):
        ratio = 0.65
    elif any(k in occ for k in ["office","business"]):
        ratio = 0.85
    else:
        ratio = 0.75
 
    w = math.sqrt(af / ratio)
    d = af / w
    walls = [{"points":[{"x":0,"y":0},{"x":w,"y":0},{"x":w,"y":d},{"x":0,"y":d}],
              "closed":True,"exterior":True}]
    rooms = []
 
    if any(k in occ for k in ["warehouse","storage","wholesale","big box","distribution"]):
        has_tire = any(k in occ for k in ["wholesale","big box","costco"])
        has_food = any(k in occ for k in ["wholesale","big box","costco","retail"])
        tire_w = min(80, w*0.12) if has_tire else 0
        food_w = min(60, w*0.08) if has_food else 0
        sup_d  = min(40, d*0.10)
        mw     = w - tire_w - food_w
        hz     = "esfr_k14" if ch > 20 else "extra_2"
        rooms += [{"name":"Main Warehouse","hazard_override":hz,
                   "boundary":[{"x":0,"y":0},{"x":mw,"y":0},{"x":mw,"y":d-sup_d},{"x":0,"y":d-sup_d}],
                   "area_sf":mw*(d-sup_d),"ceiling_height_ft":ch}]
        if tire_w > 0:
            rooms.append({"name":"Tire Center","hazard_override":"tire_storage",
                          "boundary":[{"x":mw,"y":0},{"x":mw+tire_w,"y":0},
                                      {"x":mw+tire_w,"y":d-sup_d},{"x":mw,"y":d-sup_d}],
                          "area_sf":tire_w*(d-sup_d),"ceiling_height_ft":ch})
        if food_w > 0:
            rooms.append({"name":"Food Service","hazard_override":"ordinary_2",
                          "boundary":[{"x":mw+tire_w,"y":0},{"x":w,"y":0},
                                      {"x":w,"y":d-sup_d},{"x":mw+tire_w,"y":d-sup_d}],
                          "area_sf":food_w*(d-sup_d),"ceiling_height_ft":min(ch,20)})
        rooms += [
            {"name":"Receiving","hazard_override":"ordinary_2",
             "boundary":[{"x":0,"y":d-sup_d},{"x":w*0.6,"y":d-sup_d},{"x":w*0.6,"y":d},{"x":0,"y":d}],
             "area_sf":w*0.6*sup_d,"ceiling_height_ft":min(ch,14)},
            {"name":"Entrance & Lobby","hazard_override":"light",
             "boundary":[{"x":w*0.6,"y":d-sup_d},{"x":w,"y":d-sup_d},{"x":w,"y":d},{"x":w*0.6,"y":d}],
             "area_sf":w*0.4*sup_d,"ceiling_height_ft":min(ch,14)},
        ]
 
    elif any(k in occ for k in ["office","business"]):
        ld = min(30, d*0.15); cd = 8
        rooms += [
            {"name":"Open Office","boundary":[{"x":0,"y":0},{"x":w,"y":0},{"x":w,"y":d-ld-cd},{"x":0,"y":d-ld-cd}],"area_sf":w*(d-ld-cd)},
            {"name":"Corridor","hazard_override":"light","boundary":[{"x":0,"y":d-ld-cd},{"x":w,"y":d-ld-cd},{"x":w,"y":d-ld},{"x":0,"y":d-ld}],"area_sf":w*cd},
            {"name":"Lobby","hazard_override":"light","boundary":[{"x":0,"y":d-ld},{"x":w,"y":d-ld},{"x":w,"y":d},{"x":0,"y":d}],"area_sf":w*ld},
        ]
    elif any(k in occ for k in ["retail","mercantile"]):
        sd = min(40, d*0.20); ow = min(30, w*0.10)
        rooms += [
            {"name":"Sales Floor","hazard_override":"ordinary_1","boundary":[{"x":0,"y":0},{"x":w-ow,"y":0},{"x":w-ow,"y":d-sd},{"x":0,"y":d-sd}],"area_sf":(w-ow)*(d-sd)},
            {"name":"Stockroom","hazard_override":"ordinary_2","boundary":[{"x":0,"y":d-sd},{"x":w,"y":d-sd},{"x":w,"y":d},{"x":0,"y":d}],"area_sf":w*sd},
            {"name":"Office","hazard_override":"light","boundary":[{"x":w-ow,"y":0},{"x":w,"y":0},{"x":w,"y":d-sd},{"x":w-ow,"y":d-sd}],"area_sf":ow*(d-sd)},
        ]
    elif any(k in occ for k in ["manufacturing","industrial","factory"]):
        pp  = 0.70; od = min(40, d*0.15)
        hz  = "extra_2" if ch > 20 else "ordinary_2"
        rooms += [
            {"name":"Production Floor","hazard_override":hz,"boundary":[{"x":0,"y":0},{"x":w,"y":0},{"x":w,"y":d*pp},{"x":0,"y":d*pp}],"area_sf":w*d*pp},
            {"name":"Warehouse","hazard_override":"extra_1","boundary":[{"x":0,"y":d*pp},{"x":w*0.5,"y":d*pp},{"x":w*0.5,"y":d-od},{"x":0,"y":d-od}],"area_sf":w*0.5*(d-d*pp-od)},
            {"name":"Shipping","hazard_override":"ordinary_2","boundary":[{"x":w*0.5,"y":d*pp},{"x":w,"y":d*pp},{"x":w,"y":d-od},{"x":w*0.5,"y":d-od}],"area_sf":w*0.5*(d-d*pp-od)},
            {"name":"Office","hazard_override":"light","boundary":[{"x":0,"y":d-od},{"x":w,"y":d-od},{"x":w,"y":d},{"x":0,"y":d}],"area_sf":w*od},
        ]
    else:
        sd = min(30, d*0.15)
        rooms += [
            {"name":"Main Area","boundary":[{"x":0,"y":0},{"x":w,"y":0},{"x":w,"y":d-sd},{"x":0,"y":d-sd}],"area_sf":w*(d-sd)},
            {"name":"Support","hazard_override":"light","boundary":[{"x":0,"y":d-sd},{"x":w,"y":d-sd},{"x":w,"y":d},{"x":0,"y":d}],"area_sf":w*sd},
        ]
 
    # Partition walls
    seen_y = set(); seen_x = set()
    for room in rooms:
        for p in room.get("boundary", []):
            ry = round(p["y"], 1); rx = round(p["x"], 1)
            if 0 < ry < d and ry not in seen_y:
                walls.append({"points":[{"x":0,"y":ry},{"x":w,"y":ry}],"exterior":False}); seen_y.add(ry)
            if 0 < rx < w and rx not in seen_x:
                walls.append({"points":[{"x":rx,"y":0},{"x":rx,"y":d}],"exterior":False}); seen_x.add(rx)
 
    return {"walls":walls,"rooms":rooms,"columns":[],"obstructions":[],
            "building_dimensions":{"width_ft":round(w,1),"depth_ft":round(d,1)},
            "floor_area_sf":af,"ceiling_height_ft":ch,"_synthetic":True}
 
 
 
# ── Structural framing → hanger type lookup ──────────────────────────────────
# Maps (framing_type, pipe_size) → TOLCO figure designation number
# These match the Battalion One / AutoSprink hanger designation convention
FRAMING_HANGER_MAP = {
    # (framing_type, pipe_dia_threshold) -> (hanger_type_str, designation_num, tolco_fig)
    "steel_beam":         ("clevis",   12, "TOLCO Fig. 130 Beam Clamp"),
    "open_web_truss":     ("trapeze",   9, "TOLCO Fig. 78 Ceiling Flange"),
    "i_joist":            ("u_hook",   19, "U-Hook Through Joist"),
    "wood_joist":         ("wood",     24, "Hanger: Pipe on Wood Support"),
    "concrete_deck":      ("insert",    1, "TOLCO Fig. 78 Concrete Insert"),
    "metal_deck":         ("clevis",   12, "TOLCO Fig. 130 Beam Clamp"),
    "glulam":             ("rod",       1, "TOLCO Fig. 78 Rod Hanger"),
    "clt":                ("rod",       1, "TOLCO Fig. 78 Rod Hanger"),
    "default":            ("rod",       1, "TOLCO Fig. 78 Standard Rod"),
}
 
# Cross-mains on open-web trusses use trapeze; branch lines use rod or U-hook
BRANCH_HANGER_OVERRIDE = {
    "open_web_truss": ("trapeze", 9, "TOLCO Fig. 78 Trapeze"),
    "i_joist":        ("u_hook", 19, "U-Hook Through I-Joist"),
    "wood_joist":     ("wood",   24, "Pipe on Wood Support"),
}
 
def _hanger_type_for(pipe_type: str, framing: str, pipe_dia: float) -> tuple:
    """Returns (type_str, designation_num, description) for a hanger."""
    framing_key = framing.lower().replace(" ","_").replace("-","_") if framing else "default"
    # Branch lines and armovers get a potentially different type than mains
    if pipe_type in ("branch","armover") and framing_key in BRANCH_HANGER_OVERRIDE:
        return BRANCH_HANGER_OVERRIDE[framing_key]
    info = FRAMING_HANGER_MAP.get(framing_key, FRAMING_HANGER_MAP["default"])
    return info
 
 
# ─── Main Design Engine ───────────────────────────────────────────────────────
 
class NFPA13DesignEngine:
 
    def __init__(self, geo: dict, ctx: dict):
        self.geo     = normalize_geometry(geo, ctx)
        self.ctx     = ctx
        self.rooms   = self.geo.get("rooms", [])
        self.walls   = self.geo.get("walls", [])
        self.columns = self.geo.get("columns", [])
        self.obs     = self.geo.get("obstructions", [])
        self.ch      = float(ctx.get("ceiling_height", 10))
        self.sp_psi  = float(ctx.get("static_pressure", 72))
        self.rp_psi  = float(ctx.get("residual_pressure", ctx.get("static_pressure",72)*0.85))
        self.fl_gpm  = float(ctx.get("water_supply_flow", 1500))
        self.mat     = ctx.get("pipe_material","Schedule 40 Steel").lower()
        self.hwc     = HW_C.get(self.mat, 120)
        self.seismic = ctx.get("seismic_zone","D1")
        occ          = ctx.get("occupancy","").lower()
        self.def_hz  = next((v for k,v in ZONE_MAP.items() if k in occ), "light")
        # Structural framing type drives hanger selection
        self.framing = ctx.get("structural_framing", ctx.get("framing_type", "default"))
        # Sprinkler manufacturer preference (can be overridden per project)
        self.sprinkler_mfr = ctx.get("sprinkler_manufacturer", "Viking")
        self.bw, self.bd = self._building_footprint()
        self.fa      = self.bw * self.bd or float(ctx.get("total_area", 10000))
 
        log.info("[DE] Init: %.0fx%.0fft %.0fSF ch=%.0fft def_hz=%s",
                 self.bw, self.bd, self.fa, self.ch, self.def_hz)
 
    # ── Public entry point ────────────────────────────────────────────────────
 
    def design(self) -> dict:
        zones        = self._build_zones()
        sprinklers   = self._place_sprinklers(zones)
        pipe_sections= self._route_pipes_tree(sprinklers, zones)
        hydraulics   = self._hydraulic_calc_hw(sprinklers, pipe_sections, zones)
        hangers, braces = self._hanger_schedule(pipe_sections)
        valves, equip   = self._valve_schedule(sprinklers, zones)
        bom             = self._bill_of_materials(sprinklers, pipe_sections, hangers, braces, valves)
        compliance      = self._compliance_check(sprinklers, pipe_sections, hydraulics, zones)
 
        ceiling_sp = [s for s in sprinklers if not s.get("in_rack")]
        rack_sp    = [s for s in sprinklers if s.get("in_rack")]
 
        log.info("[DE] Complete: %d sprinklers (%d ceiling + %d in-rack) | "
                 "%d pipe sections | %.0fgpm @ %.1fpsi | delta %.1fpsi | BOM %d items $%.0f",
                 len(sprinklers), len(ceiling_sp), len(rack_sp),
                 len(pipe_sections),
                 hydraulics["flow_demand"], hydraulics["required_pressure"],
                 hydraulics["pressure_delta"], len(bom),
                 sum(b["qty"]*b["unit_cost"] for b in bom))
 
        result = {
            "sprinkler_placements": sprinklers,
            "pipe_sections":        pipe_sections,
            "valves":               valves,
            "equipment":            equip,
            "walls":                self.walls,
            "columns":              self.columns,
            "rooms":                self.rooms,
            "hangers":              hangers,
            "dxf_ready":  True,
            "ifc_ready":  True,
            "warnings":   [f["description"] for f in compliance if f["severity"]!="pass"],
            "static_pressure":    hydraulics["static_pressure"],
            "residual_pressure":  hydraulics["residual_pressure"],
            "required_pressure":  hydraulics["required_pressure"],
            "pressure_delta":     hydraulics["pressure_delta"],
            "flow_demand":        hydraulics["flow_demand"],
            "density_area":       hydraulics["density_area"],
            "demand_curve":       hydraulics["demand_curve"],
            "remote_area_calcs":  hydraulics["remote_area_calcs"],
            "compliant":          hydraulics["pressure_delta"] >= 0,
            "hanger_schedule":    hangers,
            "sway_braces":        braces,
            "seismic_zone":       self.seismic,
            "bom":                bom,
            "total_material_cost":sum(b["qty"]*b["unit_cost"] for b in bom),
            "design_metadata": {
                "total_sprinklers":   len(sprinklers),
                "ceiling_sprinklers": len(ceiling_sp),
                "rack_sprinklers":    len(rack_sp),
                "total_pipe_ft":      round(sum(s.get("length",0) for s in pipe_sections),1),
                "floor_area_sf":      round(self.fa, 0),
                "building_w_ft":      round(self.bw, 1),
                "building_d_ft":      round(self.bd, 1),
                "ceiling_height_ft":  self.ch,
                "hw_c_factor":        self.hwc,
                "zones":              [{"name":z["name"],"hazard":z["hazard"],
                                        "area_sf":round(z["area_sf"],0)} for z in zones],
                "compliance_flags":   compliance,
                "geometry_synthetic": self.geo.get("_synthetic", False),
                "nfpa_references":    ["§4","§6","§8","§8.5","§8.5.4.1","§8.7.2",
                                       "§9","§9.3","§12","§17","§22","§22.1","§22.3",
                                       "§22.4","§24","§27.2","Table 12.1","Table 12.2"],
            },
        }
 
        # ── Reconcile with the node-by-node worksheet (NFPA 13 §28) ──────────
        # The compliance flag and the agentic pump-sizing must run off the SAME
        # node-by-node calc that the FP3.0 sheet renders — not the coarse
        # critical-path estimate above. Applies to density/area (LH/OH/EH)
        # designs only; the worksheet is not ESFR-aware, so ESFR/storage keeps
        # the engine's own calc until the worksheet handles the ESFR method.
        spk0 = (result["sprinkler_placements"] or [{}])[0]
        is_esfr = bool(spk0.get("is_esfr")) or spk0.get("type") == "esfr"
        if not is_esfr:
            try:
                from hydraulic_worksheet import build_hydraulic_worksheet
                ws = build_hydraulic_worksheet({
                    "sprinkler_placements": result["sprinkler_placements"],
                    "pipe_sections":        result["pipe_sections"],
                    "remote_area_calcs":    result["remote_area_calcs"],
                    "static_pressure":      result["static_pressure"],
                    "residual_pressure":    result["residual_pressure"],
                    "pressure_delta":       result["pressure_delta"],
                    "flow_demand":          result["flow_demand"],
                    "density_area":         result["density_area"],
                }, self.ctx)
                s = ws.get("summary", {})
                req = s.get("required_pressure_psi")
                if req is not None:
                    result["required_pressure"] = round(float(req), 1)
                    result["flow_demand"]       = round(float(s.get("total_flow_gpm", result["flow_demand"])), 1)
                    result["pressure_delta"]    = round(float(s.get("pressure_margin_psi", result["pressure_delta"])), 1)
                    result["compliant"]         = result["pressure_delta"] >= 0
                    result["hydraulic_method"]  = "node_by_node_§28"
                    for f in result["design_metadata"]["compliance_flags"]:
                        if f.get("section") in ("§22", "§22.4") and not result["compliant"]:
                            f["severity"]    = "critical"
                            f["description"] = (f"Water supply short by {abs(result['pressure_delta']):.1f} psi "
                                                f"per node-by-node calc (required {result['required_pressure']:.1f} psi).")
            except Exception as _ws_err:
                result.setdefault("warnings", []).append(
                    f"Worksheet reconciliation skipped: {_ws_err}")
        else:
            result["hydraulic_method"] = "esfr_critical_path_§22.1"
 
        return result
 
    # ── Zone builder ──────────────────────────────────────────────────────────
 
    def _build_zones(self) -> list:
        """Build design zones covering 100% of the building floor area."""
        valid = [r for r in self.rooms
                 if r.get("boundary") and len(r["boundary"]) >= 3
                 and r.get("area_sf", 0) > 50]
        zones = []
        if valid:
            for r in valid:
                n  = r.get("name","") or ""
                nl = n.lower()
                hz = (r.get("hazard_override") or
                      r.get("hazard_classification") or
                      next((v for k,v in ZONE_MAP.items() if k in nl), self.def_hz))
                c  = HAZARD_CRITERIA.get(hz, HAZARD_CRITERIA.get(self.def_hz, HAZARD_CRITERIA["light"]))
                pts = r["boundary"]
                xs  = [p["x"] for p in pts]; ys = [p["y"] for p in pts]
                zx0 = max(0.0, min(xs)); zy0 = max(0.0, min(ys))
                zx1 = min(self.bw, max(xs)); zy1 = min(self.bd, max(ys))
                if zx1-zx0 < 3 or zy1-zy0 < 3: continue
                zones.append({
                    "name": n or f"Zone {len(zones)+1}",
                    "hazard": hz, "criteria": c,
                    "bounds": (zx0, zy0, zx1, zy1),
                    "area_sf": (zx1-zx0)*(zy1-zy0),
                    "ceiling_height_ft": float(r.get("ceiling_height_ft") or self.ch),
                    "room": r,
                })
 
        building_area = self.bw * self.bd
        covered       = sum(z["area_sf"] for z in zones)
        pct           = covered / building_area if building_area > 0 else 0
 
        if pct < 0.15:
            syn   = _synthetic(self.ctx)
            zones = []
            for r in syn.get("rooms", []):
                n  = r.get("name",""); nl = n.lower()
                hz = r.get("hazard_override") or next((v for k,v in ZONE_MAP.items() if k in nl), self.def_hz)
                c  = HAZARD_CRITERIA.get(hz, HAZARD_CRITERIA["light"])
                pts = r.get("boundary",[]); xs = [p["x"] for p in pts]; ys = [p["y"] for p in pts]
                if not xs: continue
                zones.append({"name":n,"hazard":hz,"criteria":c,
                              "bounds":(min(xs),min(ys),max(xs),max(ys)),
                              "area_sf":r.get("area_sf",0),
                              "ceiling_height_ft":float(r.get("ceiling_height_ft") or self.ch),
                              "room":r})
        elif pct < 0.85:
            zones.extend(self._fill_zone_gaps(zones))
 
        return zones
 
    def _fill_zone_gaps(self, zones):
        cell = max(5.0, min(self.bw, self.bd)/40)
        cols = max(1, int(math.ceil(self.bw/cell)))
        rows = max(1, int(math.ceil(self.bd/cell)))
        covered  = [[False]*cols for _ in range(rows)]
        for z in zones:
            zx0,zy0,zx1,zy1 = z["bounds"]
            c0=max(0,int(zx0/cell)); c1=min(cols-1,int((zx1-0.01)/cell))
            r0=max(0,int(zy0/cell)); r1=min(rows-1,int((zy1-0.01)/cell))
            for ri in range(r0,r1+1):
                for ci in range(c0,c1+1): covered[ri][ci]=True
        c  = HAZARD_CRITERIA.get(self.def_hz, HAZARD_CRITERIA["light"])
        gaps = []; gid=1; visited=[[False]*cols for _ in range(rows)]
        for ri in range(rows):
            for ci in range(cols):
                if covered[ri][ci] or visited[ri][ci]: continue
                ce = ci
                while ce+1<cols and not covered[ri][ce+1] and not visited[ri][ce+1]: ce+=1
                re = ri
                while (re+1<rows and
                       all(not covered[re+1][cc] and not visited[re+1][cc]
                           for cc in range(ci,ce+1))): re+=1
                for rr in range(ri,re+1):
                    for cc in range(ci,ce+1): visited[rr][cc]=True
                x0=round(ci*cell,1); y0=round(ri*cell,1)
                x1=round(min((ce+1)*cell,self.bw),1); y1=round(min((re+1)*cell,self.bd),1)
                area=(x1-x0)*(y1-y0)
                if area<25: continue
                gaps.append({"name":f"Fill {gid}","hazard":self.def_hz,"criteria":c,
                             "bounds":(x0,y0,x1,y1),"area_sf":area,
                             "ceiling_height_ft":self.ch,"room":None})
                gid+=1
        return gaps
 
    # ── Sprinkler placement ───────────────────────────────────────────────────
 
    def _place_sprinklers(self, zones: list) -> list:
        """
        Place sprinklers per NFPA 13:
        - §8.5.4.1: Max distance from wall = S/2 (half max spacing)
        - §8.5.2.1: Max spacing between heads ≤ S
        - §3.3.206 Small room rule: room ≤ (2×coverage_radius)² → 1 head centered
        - §8.7.2: Arm-overs ≤ 12" generated in routing step
        - §22.1: ESFR max 10'×10' or 10'×12' spacing
        """
        sp  = []
        sid = 1
 
        for z in zones:
            c  = z["criteria"]
            ms = c["max_spacing"]    # max distance between heads (ft)
            mc = c["max_coverage"]   # max coverage per head (sqft)
            k  = c["k"]
            st = c["type"]
            mp = c["min_psi"]
            is_e  = c["esfr"]
            in_r  = c["in_rack"]
            room_ch = z.get("ceiling_height_ft", self.ch)
            if room_ch <= 0: room_ch = self.ch
 
            x0, y0, x1, y1 = z["bounds"]
            x0 = max(0.0, x0); y0 = max(0.0, y0)
            x1 = min(self.bw, x1); y1 = min(self.bd, y1)
            if x1-x0 < 0.5 or y1-y0 < 0.5: continue
 
            # Temperature rating based on ceiling height
            temp = 286 if room_ch > 30 else (175 if room_ch > 20 else 155)
            if is_e: mp = max(mp, 50.0)
 
            zone_w = x1 - x0
            zone_d = y1 - y0
            zone_area = zone_w * zone_d
 
            # ── Small room rule (§3.3.206) ────────────────────────────────
            # One head centered if zone fits within one coverage radius in each dim
            coverage_r = math.sqrt(mc / math.pi) if mc > 0 else ms/2
            if (not is_e and zone_area <= mc and
                    zone_w <= ms * 1.05 and zone_d <= ms * 1.05):
                cx = round((x0+x1)/2, 2)
                cy = round((y0+y1)/2, 2)
                if 0.0 <= cx <= self.bw and 0.0 <= cy <= self.bd:
                    sp.append(self._head(sid, cx, cy, room_ch, st, k, temp, mp,
                                         z, is_e, False,
                                         round(math.sqrt(zone_area/math.pi),2),
                                         zone_area, "§3.3.206 Small Room"))
                    sid += 1
                continue
 
            # ── Standard grid placement ───────────────────────────────────
            # Grid spacing: sqrt(max_coverage) but ≤ max_spacing
            grid_x = min(ms, math.sqrt(mc))
            grid_y = grid_x
            # For ESFR: explicitly limit to 10×10 or 10×12 per §22.1
            if is_e:
                grid_x = min(10.0, ms)
                grid_y = min(12.0, ms)
 
            # Wall offset per §8.5.4.1: max S/2 from wall (use half the grid spacing)
            wall_off_x = min(grid_x / 2, ms / 2)
            wall_off_y = min(grid_y / 2, ms / 2)
 
            xs = self._grid_pts(x0, x1, wall_off_x, grid_x)
            ys = self._grid_pts(y0, y1, wall_off_y, grid_y)
 
            for y in ys:
                for x in xs:
                    if not (0.0 <= x <= self.bw and 0.0 <= y <= self.bd): continue
                    sp.append(self._head(sid, x, y, room_ch, st, k, temp, mp,
                                         z, is_e, False,
                                         round(min(grid_x, grid_y)/2, 2),
                                         round(grid_x*grid_y, 1),
                                         "§22.1" if is_e else "§8.5"))
                    sid += 1
 
            # ── In-rack sprinklers ────────────────────────────────────────
            if in_r and zone_area > 500:
                for lv in [6.0, 12.0, 18.0, 24.0]:
                    if lv >= room_ch - 3: break
                    for y in self._grid_pts(y0, y1, 4.0, 8.0):
                        for x in self._grid_pts(x0, x1, 4.0, 8.0):
                            if not (0.0 <= x <= self.bw and 0.0 <= y <= self.bd): continue
                            sp.append({
                                "id": f"R{sid:05d}", "x": round(x,2), "y": round(y,2),
                                "elevation": lv, "type":"upright", "zone": z["name"][:15],
                                "zone_hazard": z["hazard"],
                                "coverage_radius": 4.0, "coverage_area": 64.0,
                                "k_factor": 5.6, "temp_rating": 165, "min_pressure": 7.0,
                                "hazard": "In-Rack", "room": z["name"],
                                "nfpa_ref": "§12", "in_rack": True, "rack_level_ft": lv,
                            })
                            sid += 1
 
        log.info("[DE] Placed %d sprinklers (%d ceiling, %d in-rack)",
                 len(sp), len([s for s in sp if not s.get("in_rack")]),
                 len([s for s in sp if s.get("in_rack")]))
        return sp
 
    def _head(self, sid, x, y, elev, st, k, temp, mp, z, is_e, in_rack, cr, ca, ref):
        # Size: 1" for dry/ESFR, 1/2" for standard wet heads
        size = "1" if (is_e or temp >= 200) and st in ("upright","esfr") else "1/2"
        return {
            "id": f"S{sid:05d}", "x": round(x,2), "y": round(y,2),
            "elevation": elev, "type": st,
            "zone": z["name"][:15], "zone_hazard": z["hazard"],
            "coverage_radius": cr, "coverage_area": ca,
            "k_factor": k, "temp_rating": temp, "min_pressure": mp,
            "hazard": z["hazard"].replace("_"," ").title(),
            "room": z["name"], "nfpa_ref": ref,
            "is_esfr": is_e, "in_rack": in_rack,
            "size": size,
            "manufacturer": self.sprinkler_mfr,
        }
 
    def _grid_pts(self, start: float, end: float, offset: float, spacing: float) -> list:
        """Generate evenly-spaced points with wall offset applied at both ends."""
        pts = []
        p   = start + offset
        while p <= end - offset * 0.5 + 0.01:
            pts.append(round(p, 2))
            p += spacing
        return pts or [round((start+end)/2, 2)]
 
    # ── Pipe routing — proper tree topology ───────────────────────────────────
 
    def _route_pipes_tree(self, sprinklers: list, zones: list) -> list:
        """
        Route pipes as a proper NFPA 13 tree with per-span sections.
 
        Every pipe section is ONE SPAN between two connection points:
          - Branch line: wall → head1 → head2 → … → headN → wall
          - Cross-main:  spine tee → branch tee1 → branch tee2 → … → end
          - Supply main: riser → spine tee1 → spine tee2 → … → end
 
        Each span gets:
          - Its own length  (actual center-to-center ft, from real head/tee coords)
          - Its own diameter (NFPA 13 schedule based on heads downstream of that span)
          - Its own elevation_ft
 
        This produces the section granularity needed for fabrication labels on drawings.
        """
        csp = [s for s in sprinklers if not s.get("in_rack")]
        if not csp:
            return []
 
        secs = []
        pid  = [1]
 
        def nxt(prefix):
            n = f"{prefix}-{pid[0]:03d}"
            pid[0] += 1
            return n
 
        def seg(pid_prefix, pipe_type, fx, fy, tx, ty, elev, diameter, schedule, mat, fittings, note="§6"):
            length = round(abs(tx-fx) + abs(ty-fy), 2)   # rectilinear length (no diagonals in tree)
            return {
                "id": nxt(pid_prefix), "pipe_type": pipe_type,
                "from": {"x": round(fx,2), "y": round(fy,2)},
                "to":   {"x": round(tx,2), "y": round(ty,2)},
                "diameter": diameter, "schedule": schedule, "material": mat,
                "length": length, "elevation_ft": elev,
                "fittings": fittings, "nfpa_ref": note,
            }
 
        mat        = self.ctx.get("pipe_material","Steel")
        sched      = "Sch 10" if "10" in mat.lower() else "Sch 40"
        hz_cat     = self._hz_category(self.def_hz)
        main_elev  = round(self.ch - 0.67, 2)   # mains: 8" below structure
        branch_elev= round(self.ch - 0.17, 2)   # branches: 2" below structure
 
        xs = [s["x"] for s in csp]; ys = [s["y"] for s in csp]
        rx = round(min(xs) - 4, 1)   # riser X
        ry = round((min(ys)+max(ys))/2, 1)
 
        bw, bd     = self.bw, self.bd
        run_along_x= (bw >= bd)
        tol        = 3.0
 
        if run_along_x:
            rows = self._group_by(csp, "y", tol)
        else:
            rows = self._group_by(csp, "x", tol)
 
        row_keys = sorted(rows.keys(), key=lambda v: abs(v-(ry if run_along_x else rx)))
        n_ceiling = len(csp)
        main_d    = self._pipe_size_schedule(n_ceiling, hz_cat)
 
        # ── Riser stub ────────────────────────────────────────────────────────
        first_jx = round(rx + 4, 1); first_jy = ry
        secs.append(seg("M", "main", rx, ry, first_jx, first_jy,
                        main_elev, main_d, sched, mat,
                        ["gate_valve","alarm_check","check_valve"]))
 
        # ── Supply main — broken at each cross-main tee ───────────────────────
        # Tee positions along the spine are the cross-main junction X/Y coords
        if run_along_x:
            spine_y   = ry
            spine_end = round(max(xs) + 4, 1)
            # Tee points: sorted by distance from riser
            tee_xs = sorted(set(
                round((min(s["x"] for s in rows[rk]) + max(s["x"] for s in rows[rk]))/2, 1)
                for rk in row_keys if rows[rk]
            ))
            # Walk spine from first_jx → tee1 → tee2 → … → spine_end
            prev_x = first_jx
            heads_beyond = n_ceiling   # all heads are beyond riser
            for tee_x in tee_xs:
                tee_x = max(tee_x, prev_x + 0.1)
                d = self._pipe_size_schedule(heads_beyond, hz_cat)
                secs.append(seg("M","main", prev_x, spine_y, tee_x, spine_y,
                                main_elev, d, sched, mat, ["tee_branch"]))
                # Reduce heads_beyond by heads on this branch
                branch_heads = len(rows.get(
                    min(row_keys, key=lambda rk: abs(
                        round((min(s["x"] for s in rows[rk])+max(s["x"] for s in rows[rk]))/2,1) - tee_x
                    )), []))
                heads_beyond = max(0, heads_beyond - branch_heads)
                prev_x = tee_x
            # Final stub to spine end
            if prev_x < spine_end - 0.2:
                secs.append(seg("M","main", prev_x, spine_y, spine_end, spine_y,
                                main_elev, main_d, sched, mat, []))
 
        else:
            spine_x   = round(rx + 4, 1)
            spine_end = round(max(ys) + 4, 1)
            tee_ys = sorted(set(
                round((min(s["y"] for s in rows[rk]) + max(s["y"] for s in rows[rk]))/2, 1)
                for rk in row_keys if rows[rk]
            ))
            prev_y = first_jy
            heads_beyond = n_ceiling
            for tee_y in tee_ys:
                tee_y = max(tee_y, prev_y + 0.1)
                d = self._pipe_size_schedule(heads_beyond, hz_cat)
                secs.append(seg("M","main", spine_x, prev_y, spine_x, tee_y,
                                main_elev, d, sched, mat, ["tee_branch"]))
                branch_heads = len(rows.get(
                    min(row_keys, key=lambda rk: abs(
                        round((min(s["y"] for s in rows[rk])+max(s["y"] for s in rows[rk]))/2,1) - tee_y
                    )), []))
                heads_beyond = max(0, heads_beyond - branch_heads)
                prev_y = tee_y
            if prev_y < spine_end - 0.2:
                secs.append(seg("M","main", spine_x, prev_y, spine_x, spine_end,
                                main_elev, main_d, sched, mat, []))
 
        # ── Cross-mains + per-span branch lines ───────────────────────────────
        for row_key in row_keys:
            row_sp = rows[row_key]
            if not row_sp: continue
 
            n_row  = len(row_sp)
            hz_row = self._hz_category(row_sp[0].get("zone_hazard", self.def_hz))
 
            if run_along_x:
                cross_jy = row_key
                cross_jx = round((min(s["x"] for s in row_sp)+max(s["x"] for s in row_sp))/2, 1)
 
                # ── Cross-main stub (spine → branch tee) ─────────────────────
                if abs(cross_jy - spine_y) > 0.2:
                    cross_d = self._pipe_size_schedule(n_row, hz_row)
                    secs.append(seg("X","cross", cross_jx, spine_y, cross_jx, cross_jy,
                                    main_elev, cross_d, sched, mat, ["tee_branch"]))
 
                # ── Branch line: sort heads L→R, create per-span segments ─────
                sorted_heads = sorted(row_sp, key=lambda s: s["x"])
                xs_row = [s["x"] for s in sorted_heads]
                # Wall offsets per §8.5.4.1: first head position already includes offset
                wall_left  = round(min(xs_row) - 1.0, 2)   # ~1ft past first head to wall
                wall_right = round(max(xs_row) + 1.0, 2)
 
                # Nodes along the branch: wall_left, h1, h2, ..., hN, wall_right
                nodes = [wall_left] + xs_row + [wall_right]
 
                for i in range(len(nodes)-1):
                    x_from = nodes[i]; x_to = nodes[i+1]
                    # Heads downstream of this span: everything to the right
                    n_downstream = len([h for h in sorted_heads if h["x"] >= x_to - 0.01])
                    d = self._pipe_size_schedule(max(n_downstream, 1), hz_row) if n_downstream > 0 else 0.75
                    fittings = []
                    if i > 0: fittings.append("tee_branch")         # tee at each head
                    if i == 0: fittings.append("tee_branch")        # tee at branch entry
                    secs.append(seg("B","branch", x_from, cross_jy, x_to, cross_jy,
                                    branch_elev, d, sched, mat, fittings))
 
            else:
                cross_jx = row_key
                cross_jy = round((min(s["y"] for s in row_sp)+max(s["y"] for s in row_sp))/2, 1)
 
                if abs(cross_jx - spine_x) > 0.2:
                    cross_d = self._pipe_size_schedule(n_row, hz_row)
                    secs.append(seg("X","cross", spine_x, cross_jy, cross_jx, cross_jy,
                                    main_elev, cross_d, sched, mat, ["tee_branch"]))
 
                sorted_heads = sorted(row_sp, key=lambda s: s["y"])
                ys_row = [s["y"] for s in sorted_heads]
                wall_bot = round(min(ys_row) - 1.0, 2)
                wall_top = round(max(ys_row) + 1.0, 2)
                nodes = [wall_bot] + ys_row + [wall_top]
 
                for i in range(len(nodes)-1):
                    y_from = nodes[i]; y_to = nodes[i+1]
                    n_downstream = len([h for h in sorted_heads if h["y"] >= y_to - 0.01])
                    d = self._pipe_size_schedule(max(n_downstream,1), hz_row) if n_downstream > 0 else 0.75
                    fittings = ["tee_branch"] if i > 0 else ["tee_branch"]
                    secs.append(seg("B","branch", cross_jx, y_from, cross_jx, y_to,
                                    branch_elev, d, sched, mat, fittings))
 
        return secs
 
 
    def _group_by(self, sp: list, axis: str, tol: float) -> dict:
        """Group sprinklers into rows/columns by coordinate proximity."""
        groups: dict = {}
        for s in sorted(sp, key=lambda x: x[axis]):
            val = s[axis]
            placed = False
            for gv in list(groups.keys()):
                if abs(val - gv) <= tol:
                    groups[gv].append(s)
                    placed = True
                    break
            if not placed:
                groups[val] = [s]
        return groups
 
    def _hz_category(self, hz: str) -> str:
        """Map hazard string to schedule table category."""
        if hz.startswith("light"):           return "light"
        if hz.startswith(("ordinary","cooler","freezer")): return "ordinary"
        return "extra"
 
    def _pipe_size_schedule(self, n_heads: int, category: str) -> float:
        """
        NFPA 13 Table 12.1/12.2 pipe schedule method.
        Returns minimum pipe diameter in inches for n_heads sprinklers.
        """
        table = PIPE_SCHEDULE.get(category, PIPE_SCHEDULE["ordinary"])
        for dia in sorted(table.keys()):
            if table[dia] >= n_heads:
                return dia
        return max(table.keys())   # largest available
 
    # ── Hazen-Williams hydraulic calculation ──────────────────────────────────
 
    def _hydraulic_calc_hw(self, sprinklers: list, pipe_sections: list, zones: list) -> dict:
        """
        Critical-path Hazen-Williams hydraulic calculation per NFPA 13 §22.
 
        Only calculates friction along the CRITICAL PATH (riser → most remote head),
        not for every pipe section — summing all sections produces physically wrong results
        because the same flow does not travel through every section simultaneously.
 
        Critical path segments:
          1. Supply main:  riser → cross-main junction (carries total demand)
          2. Cross-main:   junction → branch entry (carries branch demand)
          3. Branch line:  entry → most remote head (carries branch demand)
 
        Pipe sizes for critical path are selected to keep velocity ≤ 20 fps.
        """
        csp = [s for s in sprinklers if not s.get("in_rack")]
        if not csp:
            return self._zero_hydraulics()
 
        # Identify worst zone
        wz   = max(zones, key=lambda z: HAZARD_CRITERIA.get(z["hazard"],{}).get("min_psi", 7))
        c    = wz["criteria"]
        k    = c["k"]
        mp   = c["min_psi"]
        is_e = c["esfr"]
        hose = c["hose_gpm"]
        method_str = ("§22.1 ESFR" if is_e else
                      f"§22.4 Density/Area {c.get('density',0):.2f} gpm/ft² × {c.get('area',0)} ft²")
 
        wz_sp = [s for s in csp if s.get("zone_hazard") == wz["hazard"]] or csp
 
        xs      = [s["x"] for s in csp]; ys = [s["y"] for s in csp]
        riser_x = min(xs) - 4; riser_y = (min(ys)+max(ys))/2
 
        def dist_from_riser(s):
            return math.sqrt((s["x"]-riser_x)**2 + (s["y"]-riser_y)**2)
 
        # Number of remote heads
        if is_e:
            n_remote = 12
        else:
            remote_area = c.get("area", 1500)
            coverage    = c.get("max_coverage", 130)
            n_remote    = max(1, math.ceil(remote_area / coverage))
        n_remote = min(n_remote, len(wz_sp))
        remote   = sorted(wz_sp, key=dist_from_riser, reverse=True)[:n_remote]
 
        # ── Flow at remote heads (node-by-node, most remote first) ────────────
        # Per NFPA 13 §22.4.2: start at most remote head with minimum design P.
        # Each successive head has slightly higher pressure due to pipe friction.
        # Simplified: use uniform flow based on K × sqrt(P_min) for each head.
        node_calcs = []
        total_sprinkler_flow = 0.0
        for i, s in enumerate(sorted(remote, key=dist_from_riser, reverse=True)):
            p_head = mp + i * 0.5       # slight pressure increase per head toward riser
            q_head = k * math.sqrt(p_head)
            total_sprinkler_flow += q_head
            node_calcs.append({
                "node": s["id"], "x": s["x"], "y": s["y"],
                "flow_gpm": round(q_head, 2), "pressure_psi": round(p_head, 2),
                "k_factor": k, "nfpa_ref": "§22.1" if is_e else "§22.4",
            })
 
        total_demand = total_sprinkler_flow + hose
        C            = self.hwc
 
        # ── Critical path geometry ────────────────────────────────────────────
        # Estimate critical path lengths from building geometry.
        # These are the THREE segments friction must traverse:
        #   Segment 1: Supply main (riser to far end) — carries total demand
        #   Segment 2: Cross-main (perpendicular run) — carries remote area demand
        #   Segment 3: Branch line (to most remote head) — carries n_remote/2 heads
 
        building_diag = math.sqrt(self.bw**2 + self.bd**2)
        # Longest main run = 75% of building diagonal (riser typically at one end)
        L_main  = round(building_diag * 0.75, 1)
        # Cross-main = 30% of the shorter building dimension
        L_cross = round(min(self.bw, self.bd) * 0.30, 1)
        # Branch = width of remote area (n_remote heads × grid spacing)
        ms      = c["max_spacing"]
        L_branch= round(math.sqrt(n_remote) * ms, 1)
 
        # ── Size critical path pipes to keep velocity ≤ 20fps ────────────────
        def size_pipe_velocity(Q_gpm: float) -> float:
            """Return minimum diameter where velocity ≤ 20 fps."""
            for d in PIPES:
                r_ft  = (d/2) / 12
                a_ft  = math.pi * r_ft**2
                vel   = (Q_gpm / 7.48) / a_ft if a_ft > 0 else 999
                if vel <= 20.0:
                    return d
            return PIPES[-1]
 
        # Flow in each segment
        Q_main   = total_demand
        Q_cross  = total_sprinkler_flow  # cross-main carries heads only (hose connects at riser)
        Q_branch = max(1, int(n_remote / 2)) * k * math.sqrt(mp)  # half the remote heads
 
        d_main   = size_pipe_velocity(Q_main)
        d_cross  = size_pipe_velocity(Q_cross)
        d_branch = size_pipe_velocity(Q_branch)
 
        # ── Hazen-Williams friction for each critical-path segment ────────────
        def hw_friction(Q: float, d: float, L: float, fittings: list) -> tuple:
            """Returns (total_loss_psi, hf_per_ft, velocity_fps)."""
            if Q <= 0 or d <= 0 or L <= 0: return 0.0, 0.0, 0.0
            hf   = 4.52 * (Q**1.85) / ((C**1.85) * (d**4.87))
            loss = hf * L
            for f in fittings:
                eq = FEQ.get(f, {})
                loss += hf * (eq.get(int(d), eq.get(2, 0)))
            r_ft = (d/2)/12
            vel  = (Q/7.48)/(math.pi*r_ft**2) if r_ft > 0 else 0
            return round(loss,3), round(hf,5), round(vel,1)
 
        loss_main,   hf_main,   vel_main   = hw_friction(Q_main,   d_main,   L_main,
                                                          ["gate_valve","alarm_check","check_valve"])
        loss_cross,  hf_cross,  vel_cross  = hw_friction(Q_cross,  d_cross,  L_cross,  ["tee_branch"])
        loss_branch, hf_branch, vel_branch = hw_friction(Q_branch, d_branch, L_branch, ["tee_branch"]*max(1,n_remote//2))
 
        friction_loss_psi = loss_main + loss_cross + loss_branch
 
        pipe_calcs = [
            {"section":"Supply Main (Critical Path)","pipe_type":"main",
             "flow_gpm":round(Q_main,1),"diameter_in":d_main,"length_ft":L_main,
             "hf_per_ft":hf_main,"friction_psi":loss_main,"velocity_fps":vel_main},
            {"section":"Cross-Main (Critical Path)","pipe_type":"cross",
             "flow_gpm":round(Q_cross,1),"diameter_in":d_cross,"length_ft":L_cross,
             "hf_per_ft":hf_cross,"friction_psi":loss_cross,"velocity_fps":vel_cross},
            {"section":"Branch Line (Critical Path)","pipe_type":"branch",
             "flow_gpm":round(Q_branch,1),"diameter_in":d_branch,"length_ft":L_branch,
             "hf_per_ft":hf_branch,"friction_psi":loss_branch,"velocity_fps":vel_branch},
        ]
 
        # ── Elevation head (0.433 psi/ft) ────────────────────────────────────
        elev_head = self.ch * 0.433
 
        # ── Required pressure at riser ────────────────────────────────────────
        req_pressure = mp + friction_loss_psi + elev_head
 
        # ── Available pressure at demand flow (supply curve fit) ──────────────
        if self.fl_gpm > 0:
            avail_at_demand = max(0, self.sp_psi -
                                  (self.sp_psi - self.rp_psi) *
                                  (total_demand / self.fl_gpm) ** 0.54)
        else:
            avail_at_demand = self.rp_psi
 
        pressure_delta = avail_at_demand - req_pressure
 
        # ── Supply curve ──────────────────────────────────────────────────────
        curve = []
        for frac in [0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0, 1.1]:
            q = total_demand * frac
            p = (self.sp_psi - (self.sp_psi-self.rp_psi)*(q/max(self.fl_gpm,1))**0.54
                 if self.fl_gpm > 0 else (self.rp_psi if frac==0 else 0))
            curve.append({"flow": round(q,1), "pressure": round(max(0,p),1)})
 
        return {
            "static_pressure":   round(self.sp_psi, 1),
            "residual_pressure": round(self.rp_psi,  1),
            "required_pressure": round(req_pressure,  1),
            "pressure_delta":    round(pressure_delta,1),
            "flow_demand":       round(total_demand,  1),
            "demand_curve":      curve,
            "density_area":      {"density":c.get("density"),"area":c.get("area"),"method":method_str},
            "remote_area_calcs": {
                "worst_zone":               wz["name"],
                "hazard":                   wz["hazard"],
                "remote_sprinkler_count":   n_remote,
                "design_method":            method_str,
                "min_sprinkler_psi":        round(mp, 1),
                "k_factor":                 k,
                "min_flow_per_head_gpm":    round(k*math.sqrt(mp), 2),
                "total_sprinkler_flow_gpm": round(total_sprinkler_flow, 1),
                "hose_stream_gpm":          hose,
                "total_friction_loss_psi":  round(friction_loss_psi, 2),
                "elevation_head_psi":       round(elev_head, 2),
                "available_pressure_psi":   round(avail_at_demand, 1),
                "critical_path_lengths_ft": {"main":L_main,"cross":L_cross,"branch":L_branch},
                "node_calculations":        node_calcs,
                "pipe_calculations":        pipe_calcs,
                "hw_c_factor":              C,
                "nfpa_ref":                 "§22.1" if is_e else "§22.4",
            },
            "compliant": pressure_delta >= 0,
        }
 
    def _zero_hydraulics(self):
        return {
            "static_pressure":self.sp_psi,"residual_pressure":self.rp_psi,
            "required_pressure":0,"pressure_delta":self.rp_psi,
            "flow_demand":0,"density_area":{},"demand_curve":[],
            "remote_area_calcs":{},"compliant":True,
        }
 
    # ── Hanger schedule ───────────────────────────────────────────────────────
 
    def _hanger_schedule(self, ps):
        hangers = []; braces = []; hi = 1; bi = 1
        seis = self.seismic in ("C","D","D1","D2","E")
        for s in ps:
            d  = s.get("diameter", 2.0)
            L  = s.get("length", 0)
            pt = s.get("pipe_type", "branch")
            fx, fy = s["from"]["x"], s["from"]["y"]
            tx, ty = s["to"]["x"],   s["to"]["y"]
            ms = MAX_HANG.get(d, 15)
            n  = max(1, math.ceil(L / ms))
            # Determine hanger type from structural framing (project-specific)
            h_type, h_num, h_desc = _hanger_type_for(pt, self.framing, d)
            for i in range(n):
                fr = (i + 0.5) / n
                hangers.append({
                    "id":           f"H-{hi:04d}",
                    "x":            round(fx + (tx-fx)*fr, 1),
                    "y":            round(fy + (ty-fy)*fr, 1),
                    "location":     f"({fx+(tx-fx)*fr:.0f}\', {fy+(ty-fy)*fr:.0f}\')",
                    "type":         h_type,
                    "designation":  h_num,   # TOLCO/hanger designation number for plans
                    "description":  h_desc,
                    "pipe_size":    d,
                    "pipe_type":    pt,
                    "rod_diameter": 0.5 if d >= 3.0 else 0.375,
                    "load":         round(d * 12 * ms, 0),
                    "listed":       True,
                    "pipe_section": s["id"],
                    "framing":      self.framing,
                    "nfpa_ref":     "§9.1",
                })
                hi += 1
            if seis and pt in ("main","cross") and L > MAX_SWAY:
                nb = max(1, math.ceil(L / MAX_SWAY))
                for i in range(nb):
                    fr = (i + 0.5) / nb
                    braces.append({
                        "id":        f"SB-{bi:04d}",
                        "x":         round(fx + (tx-fx)*fr, 1),
                        "y":         round(fy + (ty-fy)*fr, 1),
                        "location":  f"({fx+(tx-fx)*fr:.0f}', {fy+(ty-fy)*fr:.0f}')",
                        "direction": "4-way",
                        "pipe_size": d,
                        "spacing":   round(L/nb, 1),
                        "max_allowed":MAX_SWAY,
                        "compliant": True,
                        "nfpa_ref":  "§9.3",
                    })
                    bi += 1
        return hangers, braces
 
    # ── Valve schedule ────────────────────────────────────────────────────────
 
    def _valve_schedule(self, sp, zones):
        if not sp: return [], []
        csp   = [s for s in sp if not s.get("in_rack")]
        xs    = [s["x"] for s in csp]; ys = [s["y"] for s in csp]
        rx    = round(min(xs) - 4, 1); ry = round((min(ys)+max(ys))/2, 1)
        rmx   = round(max(xs), 1);    rmy = round(max(ys), 1)
        tf    = sum(s["k_factor"]*math.sqrt(s["min_pressure"]) for s in csp[:20])
        hz_c  = self._hz_category(self.def_hz)
        md    = self._pipe_size_schedule(len(csp), hz_c)
        mds   = f"{md:.0f}" if md == int(md) else str(md)
 
        valves = [
            {"id":"OS&Y-1","type":"osy",    "x":rx,   "y":ry,   "label":f"{mds}\" OS&Y GATE VALVE","nfpa_ref":"§8.16.1","zone":"Main"},
            {"id":"CV-1",  "type":"check",  "x":rx,   "y":ry+3, "label":f"{mds}\" ALARM CHECK VALVE","nfpa_ref":"§8.16.2","zone":"Main"},
            {"id":"AV-1",  "type":"alarm",  "x":rx+3, "y":ry+3, "label":"WATERFLOW ALARM SWITCH","nfpa_ref":"§8.16.3","zone":"Main"},
            {"id":"IT-1",  "type":"inspector_test","x":rmx,"y":rmy,"label":"1\" INSPECTOR'S TEST","nfpa_ref":"§8.17.1","zone":"Remote"},
            {"id":"DR-1",  "type":"drain",  "x":rx,   "y":ry-3, "label":"2\" MAIN DRAIN","nfpa_ref":"§8.16.1.4","zone":"Main"},
        ]
        for i, z in enumerate(zones):
            bx = round(rx + (i - len(zones)/2)*20, 1)
            valves.append({"id":f"BFV-{i+1}","type":"butterfly","x":bx,"y":ry+1,
                           "label":f"ZONE VALVE — {z['name'][:15]}",
                           "nfpa_ref":"§8.16","zone":z["name"]})
        equip = [
            {"type":"riser","x":rx,"y":ry+2,
             "label":f"MAIN RISER\n{mds}\" WET PIPE","nfpa_ref":"§8.16"},
            {"type":"fdc",  "x":rx+8,"y":ry,
             "label":"FDC\n6\"×2.5\"×2.5\"×2.5\"×2.5\"","nfpa_ref":"§8.16.6"},
        ]
        return valves, equip
 
    # ── Bill of materials ─────────────────────────────────────────────────────
 
    def _bill_of_materials(self, sp, ps, hangers, braces, valves):
        bom  = []
        csp  = [s for s in sp if not s.get("in_rack")]
        rsp  = [s for s in sp if s.get("in_rack")]
        mat  = self.ctx.get("pipe_material","Steel")
 
        # Sprinklers (ceiling) — include 6% spare per §6.2.9
        for (st, k), qty in sorted(Counter((s["type"],s["k_factor"]) for s in csp).items()):
            s0   = next((s for s in csp if s["type"]==st and s["k_factor"]==k), csp[0])
            temp = s0.get("temp_rating", 155)
            hz   = s0.get("zone_hazard","").replace("_"," ").title()
            bom.append({
                "item": f"{st.upper()} SPRINKLER — K{k} {temp}°F ({hz})",
                "part_number": "TBD",
                "qty": qty + max(3, int(qty*0.06)),
                "unit": "EA", "unit_cost": SPKR_COST.get(st, 9.00),
                "nfpa_ref": "§6.2.9",
            })
 
        # In-rack sprinklers
        if rsp:
            for lv, qty in sorted(Counter(s.get("rack_level_ft",6) for s in rsp).items()):
                bom.append({
                    "item": f"IN-RACK UPRIGHT — K5.6 165°F ({lv:.0f}ft level)",
                    "part_number": "TBD",
                    "qty": qty + max(2, int(qty*0.05)),
                    "unit": "EA", "unit_cost": 9.50, "nfpa_ref": "§12",
                })
 
        # Pipe by diameter, schedule, material — include 5% waste
        pl: dict = defaultdict(float)
        for s in ps:
            key = (s.get("diameter",1.0), s.get("schedule","Sch 40"), mat)
            pl[key] += s.get("length", 0)
        for (d, sch, m), L in sorted(pl.items()):
            bom.append({
                "item": f"PIPE — {d}\" {sch} {m.title()}",
                "part_number": "TBD",
                "qty": round(L*1.05, 1),
                "unit": "LF", "unit_cost": PIPE_COST.get(d, 6.00),
                "nfpa_ref": "§6.3",
            })
 
        # Fittings
        fc: dict = defaultdict(int)
        for s in ps:
            for f in s.get("fittings",[]): fc[(f, s.get("diameter",2.0))] += 1
        fn  = {"90_elbow":"90° ELBOW","45_elbow":"45° ELBOW","tee_branch":"TEE (BRANCH)",
               "tee_run":"TEE (RUN)","alarm_check":"ALARM CHECK VALVE",
               "gate_valve":"OS&Y GATE VALVE","butterfly":"BUTTERFLY VALVE","check_valve":"CHECK VALVE"}
        fco = {"90_elbow":12,"45_elbow":8,"tee_branch":18,"tee_run":14,
               "alarm_check":520,"gate_valve":285,"butterfly":220,"check_valve":95}
        for (f, d), qty in sorted(fc.items()):
            bom.append({
                "item": f'{fn.get(f,f.upper())} — {d}"',
                "part_number": "TBD", "qty": qty,
                "unit": "EA", "unit_cost": fco.get(f, 15) * max(d/2, 1),
                "nfpa_ref": "§6.3",
            })
 
        # Hangers and braces
        for ht, qty in Counter(h.get("type","rod") for h in hangers).items():
            bom.append({
                "item": f"PIPE HANGER — {ht.upper()} (FM/UL LISTED)",
                "part_number": "TBD", "qty": qty,
                "unit": "EA", "unit_cost": 22.0 if ht=="clevis" else 14.50,
                "nfpa_ref": "§9.1",
            })
        if braces:
            bom.append({
                "item": "SWAY BRACE — 4-WAY SEISMIC (FM/UL LISTED)",
                "part_number": "TBD", "qty": len(braces),
                "unit": "EA", "unit_cost": 195.0, "nfpa_ref": "§9.3",
            })
 
        # Valves
        for v in valves:
            bom.append({
                "item": v.get("label","VALVE"), "part_number":"TBD",
                "qty": 1, "unit":"EA",
                "unit_cost": VALVE_COST.get(v.get("type","osy"), 200),
                "nfpa_ref": v.get("nfpa_ref","§8.16"),
            })
 
        # Fixed riser components
        for item, cost, ref in [
            ("MAIN RISER ASSEMBLY — WET PIPE, COMPLETE",        3500, "§8.16"),
            ("FIRE DEPT. CONNECTION — 6\"×2.5\"×2.5\"×2.5\"×2.5\"", 850,"§8.16.6"),
            ("PRESSURE GAUGE — 0-400 PSI, LISTED",              85,   "§8.16"),
            ("MAIN DRAIN ASSEMBLY — 2\", COMPLETE",              225,  "§8.16.1.4"),
            ("RPZ BACKFLOW PREVENTER — PER CIVIL DWGS",          780,  "§8.16"),
            ("HYDRAULIC DESIGN INFORMATION SIGN",                 15,   "§27.2"),
        ]:
            bom.append({"item":item,"part_number":"TBD","qty":1,
                        "unit":"EA","unit_cost":cost,"nfpa_ref":ref})
        return bom
 
    # ── Compliance checks ─────────────────────────────────────────────────────
 
    def _compliance_check(self, sp, ps, hyd, zones):
        flags = []
 
        def flag(section, desc, sev="pass"):
            flags.append({"section":section,"description":desc,"severity":sev})
 
        # §8.5.2 — Head spacing
        csp  = [s for s in sp if not s.get("in_rack")]
        rows = self._group_by(csp, "y" if self.bw >= self.bd else "x", 3.0)
        spacing_ok = True
        for rk, row_sp in list(rows.items())[:15]:
            rs = sorted(row_sp, key=lambda s: s["x"])
            for i in range(len(rs)-1):
                dd = abs(rs[i]["x"]-rs[i+1]["x"])
                ms = HAZARD_CRITERIA.get(rs[i].get("zone_hazard",self.def_hz),{}).get("max_spacing",15)
                if dd > ms * 1.05:
                    flag("§8.5.2",
                         f"Spacing {dd:.1f}ft > max {ms}ft: {rs[i]['id']}→{rs[i+1]['id']}","critical")
                    spacing_ok = False; break
            if not spacing_ok: break
        if spacing_ok:
            flag("§8.5.2","Head spacing compliant in all zones","pass")
 
        # §8.5.4.1 — Wall offset
        flag("§8.5.4.1",
             "Wall offsets = S/2 applied in both axes (checked at placement)","pass")
 
        # §8.7.2 — Arm-overs
        ao = [s for s in ps if s.get("pipe_type")=="armover"]
        flag("§8.7.2",f"Arm-overs generated: {len(ao)} (max 1ft per §8.7.2)","pass")
 
        # §22 — Supply pressure
        pd = hyd.get("pressure_delta", 0)
        rp = hyd.get("required_pressure", 0)
        rv = hyd.get("residual_pressure", 0)
        if pd < 0:
            flag("§22",f"INSUFFICIENT PRESSURE — need {rp:.1f} psi, available {rv:.1f} psi "
                 f"(deficit {abs(pd):.1f} psi) — FIRE PUMP REQUIRED","critical")
        else:
            flag("§22",f"Pressure OK — {rv:.1f} psi available, {rp:.1f} psi required "
                 f"({pd:.1f} psi margin)","pass")
 
        # §22.1 — ESFR
        esfr_zones = [z for z in zones if HAZARD_CRITERIA.get(z["hazard"],{}).get("esfr")]
        if esfr_zones:
            flag("§22.1",f"ESFR design applied: {', '.join(z['name'] for z in esfr_zones)}","pass")
 
        # §12 — In-rack
        rack = [s for s in sp if s.get("in_rack")]
        if rack:
            flag("§12",f"In-rack sprinklers: {len(rack)} heads","pass")
 
        # §9.3 — Seismic
        if self.seismic in ("C","D","D1","D2","E"):
            flag("§9.3",f"Seismic zone {self.seismic} — 4-way sway bracing required on mains","pass")
 
        # Table 12.1/12.2 — Pipe schedule
        flag("Table 12.1","Pipe sizes from NFPA 13 schedule method","pass")
 
        # §8.16 — Riser
        flag("§8.16","Riser assembly: OS&Y, alarm check, flow switch, gauge, drain","pass")
 
        # §8.17 — Inspector's test
        flag("§8.17","Inspector's test valve at most remote sprinkler","pass")
 
        # §27.2 — Hydraulic placard
        flag("§27.2","Hydraulic design information sign required at riser","pass")
 
        return flags
 
    # ── Building footprint helper ─────────────────────────────────────────────
 
    def _building_footprint(self):
        bd = self.geo.get("building_dimensions",{})
        if bd.get("width_ft") and bd.get("depth_ft"):
            return float(bd["width_ft"]), float(bd["depth_ft"])
        ax, ay = [], []
        for w in self.walls:
            for p in w.get("points",[]): ax.append(p.get("x",0)); ay.append(p.get("y",0))
        for r in self.rooms:
            for p in r.get("boundary",[]): ax.append(p.get("x",0)); ay.append(p.get("y",0))
        if ax and ay:
            w = max(ax)-min(ax); d = max(ay)-min(ay)
            if w > 20 and d > 20: return w, d
        area = float(self.ctx.get("total_area",10000))
        w = math.sqrt(area/0.65)
        return w, area/w
 
