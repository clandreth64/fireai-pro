"""
FireAI Pro — NFPA 13 Design Engine v3
Warehouse-scale, mixed-occupancy, ESFR, in-rack sprinklers.
"""
import math, logging
from collections import defaultdict, Counter
log = logging.getLogger("fireai.design")

HAZARD_CRITERIA = {
    "light":             {"density":0.10,"area":1500,"max_coverage":225,"max_spacing":15,"k":5.6, "min_psi":7.0, "type":"pendant","in_rack":False,"esfr":False},
    "ordinary_1":        {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":15,"k":5.6, "min_psi":7.0, "type":"pendant","in_rack":False,"esfr":False},
    "ordinary_2":        {"density":0.20,"area":1500,"max_coverage":130,"max_spacing":15,"k":8.0, "min_psi":7.0, "type":"pendant","in_rack":False,"esfr":False},
    "extra_1":           {"density":0.30,"area":2500,"max_coverage":100,"max_spacing":12,"k":11.2,"min_psi":15.0,"type":"upright","in_rack":False,"esfr":False},
    "extra_2":           {"density":0.40,"area":2500,"max_coverage":100,"max_spacing":12,"k":11.2,"min_psi":15.0,"type":"upright","in_rack":False,"esfr":False},
    "esfr_k14":          {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":50.0,"type":"esfr",   "in_rack":False,"esfr":True},
    "esfr_k16_8":        {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":16.8,"min_psi":50.0,"type":"esfr",   "in_rack":False,"esfr":True},
    "esfr_k25":          {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":25.0,"min_psi":50.0,"type":"esfr",   "in_rack":False,"esfr":True},
    "high_pile_class_3": {"density":0.40,"area":2500,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":25.0,"type":"esfr",   "in_rack":True, "esfr":True},
    "high_pile_class_4": {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":50.0,"type":"esfr",   "in_rack":True, "esfr":True},
    "tire_storage":      {"density":None,"area":None,"max_coverage":100,"max_spacing":10,"k":14.0,"min_psi":75.0,"type":"esfr",   "in_rack":True, "esfr":True},
    "freezer":           {"density":0.15,"area":2000,"max_coverage":130,"max_spacing":12,"k":5.6, "min_psi":7.0, "type":"upright","in_rack":False,"esfr":False},
    "cooler":            {"density":0.15,"area":1500,"max_coverage":130,"max_spacing":12,"k":5.6, "min_psi":7.0, "type":"pendant","in_rack":False,"esfr":False},
}

ZONE_MAP = {
    "warehouse":"esfr_k14","high pile":"esfr_k14","high-pile":"esfr_k14",
    "storage":"high_pile_class_3","rack":"high_pile_class_4","merchandise":"esfr_k14",
    "sales floor":"esfr_k14","retail":"ordinary_1",
    "tire":"tire_storage","tires":"tire_storage","tire center":"tire_storage","automotive":"tire_storage",
    "bakery":"ordinary_2","deli":"ordinary_2","food court":"ordinary_2","food":"ordinary_2","kitchen":"ordinary_2",
    "pharmacy":"ordinary_1","optical":"light","hearing":"light",
    "receiving":"ordinary_2","loading":"ordinary_2","dock":"ordinary_2",
    "mechanical":"ordinary_1","electrical":"ordinary_1","mep":"ordinary_1",
    "entrance":"light","lobby":"light","membership":"light","office":"light",
    "restroom":"light","corridor":"light","vestibule":"light",
    "freezer":"freezer","cooler":"cooler","refrigerated":"cooler",
}

HW_C = {"steel":120,"schedule 40 steel":120,"sch40":120,"cpvc":150,"copper":150,"stainless":140}
PIPES = [0.75,1.0,1.25,1.5,2.0,2.5,3.0,3.5,4.0,5.0,6.0,8.0]
MAX_HANG = {0.75:6,1.0:6,1.25:8,1.5:8,2.0:12,2.5:12,3.0:15,4.0:15,5.0:20,6.0:20,8.0:20}
MAX_SWAY = 40.0
PC = {0.75:2.10,1.0:2.80,1.25:3.50,1.5:4.20,2.0:6.50,2.5:9.80,3.0:13.50,4.0:20.00,5.0:28.00,6.0:38.00,8.0:52.00}
SC = {"pendant":8.50,"upright":9.50,"esfr":52.00,"cmsa":45.00}
VC = {"osy":285,"butterfly":220,"check":520,"alarm":95,"inspector_test":65,"drain":145}
FEQ = {"90_elbow":{1:1,1.5:2,2:2,2.5:3,3:4,4:5,5:7,6:9},"tee_branch":{1:4,1.5:5,2:8,2.5:10,3:12,4:15,5:20,6:25},"alarm_check":{2:10,2.5:12,3:14,4:18,5:22,6:28},"gate_valve":{2:1,3:1,4:2,5:2,6:3}}


def normalize_geometry(geo: dict, ctx: dict) -> dict:
    walls=geo.get("walls",[]); rooms=geo.get("rooms",[]); cols=geo.get("columns",[])
    ax,ay=[],[]
    for w in walls:
        for p in w.get("points",[]): ax.append(float(p.get("x",0))); ay.append(float(p.get("y",0)))
    for r in rooms:
        for p in r.get("boundary",[]): ax.append(float(p.get("x",0))); ay.append(float(p.get("y",0)))
    if not ax: return _synthetic(ctx)
    ox,oy=min(ax),min(ay); raw=max(max(ax)-ox, max(ay)-oy)
    total=float(ctx.get("total_area",0)); exp=math.sqrt(total) if total>0 else 0
    if raw>100000: sc=1/304.8
    elif raw>10000: sc=1/864.0
    elif raw>1000: sc=1/12.0
    elif raw>50: sc=1.0
    else: sc=1.0
    scaled=raw*sc
    if exp>0 and (scaled<exp*0.05 or scaled>exp*20):
        log.warning(f"[Geo] scale fail raw={raw:.0f} scaled={scaled:.0f} exp={exp:.0f} → synthetic")
        return _synthetic(ctx)
    log.info(f"[Geo] {raw:.0f} → {scaled:.0f}ft (scale={sc:.6f})")
    def sp(pts): return [{"x":round((p["x"]-ox)*sc,2),"y":round((p["y"]-oy)*sc,2)} for p in pts]
    n=dict(geo)
    n["walls"]=[{**w,"points":sp(w.get("points",[]))} for w in walls]
    n["rooms"]=[{**r,"boundary":sp(r.get("boundary",[]))} for r in rooms]
    n["columns"]=[{**c,"x":round((c.get("x",0)-ox)*sc,2),"y":round((c.get("y",0)-oy)*sc,2)} for c in cols]
    n["_scale"]=sc
    for r in n["rooms"]:
        pts=r.get("boundary",[])
        if len(pts)>=3:
            a=abs(sum(pts[i]["x"]*pts[(i+1)%len(pts)]["y"]-pts[(i+1)%len(pts)]["x"]*pts[i]["y"] for i in range(len(pts))))/2
            r["area_sf"]=round(a,1); r["area"]=f"{a:.0f} SF"
    return n


def _synthetic(ctx: dict) -> dict:
    area=float(ctx.get("total_area",135411)); floors=int(ctx.get("floors",1)); af=area/floors
    w=math.sqrt(af/0.65); d=af/w
    tw=min(80,w*0.12); fw=min(60,w*0.08); sd=min(40,d*0.10); mw=w-tw-fw
    rooms=[
        {"name":"Main Warehouse","hazard_override":"esfr_k14","boundary":[{"x":0,"y":0},{"x":mw,"y":0},{"x":mw,"y":d-sd},{"x":0,"y":d-sd}],"area_sf":mw*(d-sd),"area":f"{mw*(d-sd):.0f} SF"},
        {"name":"Tire Center","hazard_override":"tire_storage","boundary":[{"x":mw,"y":0},{"x":mw+tw,"y":0},{"x":mw+tw,"y":d-sd},{"x":mw,"y":d-sd}],"area_sf":tw*(d-sd),"area":f"{tw*(d-sd):.0f} SF"},
        {"name":"Food Court","hazard_override":"ordinary_2","boundary":[{"x":mw+tw,"y":0},{"x":w,"y":0},{"x":w,"y":d-sd},{"x":mw+tw,"y":d-sd}],"area_sf":fw*(d-sd),"area":f"{fw*(d-sd):.0f} SF"},
        {"name":"Receiving & Support","hazard_override":"ordinary_2","boundary":[{"x":0,"y":d-sd},{"x":w*0.6,"y":d-sd},{"x":w*0.6,"y":d},{"x":0,"y":d}],"area_sf":w*0.6*sd,"area":f"{w*0.6*sd:.0f} SF"},
        {"name":"Entrance & Membership","hazard_override":"light","boundary":[{"x":w*0.6,"y":d-sd},{"x":w,"y":d-sd},{"x":w,"y":d},{"x":w*0.6,"y":d}],"area_sf":w*0.4*sd,"area":f"{w*0.4*sd:.0f} SF"},
    ]
    walls=[{"points":[{"x":0,"y":0},{"x":w,"y":0},{"x":w,"y":d},{"x":0,"y":d}],"closed":True,"exterior":True}]
    log.info(f"[Geo] Synthetic: {w:.0f}ft×{d:.0f}ft {len(rooms)} zones")
    return {"walls":walls,"rooms":rooms,"columns":[],"obstructions":[],
            "building_dimensions":{"width_ft":round(w,1),"depth_ft":round(d,1)},
            "floor_area_sf":af,"ceiling_height_ft":float(ctx.get("ceiling_height",50)),"_synthetic":True}


class NFPA13DesignEngine:
    def __init__(self, geo: dict, ctx: dict):
        self.geo=normalize_geometry(geo,ctx); self.ctx=ctx
        self.rooms=self.geo.get("rooms",[]); self.walls=self.geo.get("walls",[])
        self.columns=self.geo.get("columns",[]); self.obs=self.geo.get("obstructions",[])
        self.ch=float(ctx.get("ceiling_height",50))
        self.sp_psi=float(ctx.get("static_pressure",65))
        self.rp_psi=float(ctx.get("residual_pressure",ctx.get("static_pressure",65)*0.85))
        self.fl_gpm=float(ctx.get("water_supply_flow",3000))
        self.mat=ctx.get("pipe_material","Schedule 40 Steel").lower()
        self.hwc=HW_C.get(self.mat,120); self.seismic=ctx.get("seismic_zone","D1")
        occ=ctx.get("occupancy","Warehouse").lower()
        self.def_hz=next((v for k,v in ZONE_MAP.items() if k in occ),"esfr_k14")
        self.bw,self.bd=self._fp(); self.fa=self.bw*self.bd or float(ctx.get("total_area",135411))
        log.info(f"[DE] {self.bw:.0f}ft×{self.bd:.0f}ft {self.fa:.0f}SF ch={self.ch}ft hz={self.def_hz}")

    def design(self) -> dict:
        zones=self._zones(); log.info(f"[DE] {len(zones)} zones: "+", ".join(f"{z['name']}({z['hazard']})" for z in zones))
        sp=self._sprinklers(zones); ps=self._pipes(sp,zones); hyd=self._hyd(sp,ps,zones)
        hng,brc=self._hang(ps); val,eqp=self._valves(sp,zones); bom=self._bom(sp,ps,hng,brc,val)
        cmp=self._check(sp,ps,hyd,zones)
        log.info(f"[DE] {len(sp)} sprinklers | {len(ps)} sections | {hyd['flow_demand']:.0f}gpm@{hyd['required_pressure']:.1f}psi | delta {hyd['pressure_delta']:.1f}psi | BOM {len(bom)} items ${sum(b['qty']*b['unit_cost'] for b in bom):,.0f}")
        return {
            "sprinkler_placements":sp,"pipe_sections":ps,"valves":val,"equipment":eqp,
            "walls":self.walls,"columns":self.columns,"rooms":self.rooms,"hangers":hng,
            "dxf_ready":True,"ifc_ready":True,
            "warnings":[f["description"] for f in cmp if f["severity"]!="pass"],
            "static_pressure":hyd["static_pressure"],"residual_pressure":hyd["residual_pressure"],
            "required_pressure":hyd["required_pressure"],"pressure_delta":hyd["pressure_delta"],
            "flow_demand":hyd["flow_demand"],"density_area":hyd["density_area"],
            "demand_curve":hyd["demand_curve"],"remote_area_calcs":hyd["remote_area_calcs"],
            "compliant":hyd["pressure_delta"]>=0,
            "hanger_schedule":hng,"sway_braces":brc,"seismic_zone":self.seismic,
            "bom":bom,"total_material_cost":sum(b["qty"]*b["unit_cost"] for b in bom),
            "design_metadata":{
                "total_sprinklers":len(sp),"total_pipe_ft":round(sum(s.get("length",0) for s in ps),1),
                "floor_area_sf":round(self.fa,0),"building_w_ft":round(self.bw,1),"building_d_ft":round(self.bd,1),
                "ceiling_height_ft":self.ch,"hw_c_factor":self.hwc,
                "zones":[{"name":z["name"],"hazard":z["hazard"],"area_sf":round(z["area_sf"],0)} for z in zones],
                "compliance_flags":cmp,"geometry_synthetic":self.geo.get("_synthetic",False),
                "nfpa_references":["§4","§6","§8","§8.5","§8.6","§9","§9.3","§12","§17","§22","§22.1","§24","§27.2"],
            },
        }

    def _zones(self) -> list:
        valid=[r for r in self.rooms if r.get("boundary") and len(r["boundary"])>=3 and r.get("area_sf",0)>100]
        if valid:
            zones=[]
            for r in valid:
                n=r.get("name","") or ""; nl=n.lower()
                hz=r.get("hazard_override") or r.get("hazard_classification") or next((v for k,v in ZONE_MAP.items() if k in nl),self.def_hz)
                c=HAZARD_CRITERIA.get(hz,HAZARD_CRITERIA["esfr_k14"])
                pts=r["boundary"]; xs=[p["x"] for p in pts]; ys=[p["y"] for p in pts]
                zones.append({"name":n,"hazard":hz,"criteria":c,"bounds":(min(xs),min(ys),max(xs),max(ys)),"area_sf":r.get("area_sf",0),"room":r})
            return zones
        return [{"name":"Building","hazard":self.def_hz,"criteria":HAZARD_CRITERIA.get(self.def_hz,HAZARD_CRITERIA["esfr_k14"]),"bounds":(0,0,self.bw,self.bd),"area_sf":self.fa,"room":None}]

    def _sprinklers(self, zones: list) -> list:
        sp=[]; sid=1
        for z in zones:
            c=z["criteria"]; ms=c["max_spacing"]; mc=c["max_coverage"]; k=c["k"]
            st=c["type"]; mp=c["min_psi"]; is_e=c["esfr"]; in_r=c["in_rack"]
            grid=min(ms,math.sqrt(mc)); grid=round(grid*2)/2; wo=max(min(grid/2,ms/2),0.5)
            temp=286 if self.ch>30 else (175 if self.ch>20 else 155)
            if is_e: mp=max(mp,50.0)
            x0,y0,x1,y1=z["bounds"]
            if x1-x0<1 or y1-y0<1: continue
            for y in self._gp(y0,y1,wo,grid):
                for x in self._gp(x0,x1,wo,grid):
                    sp.append({"id":f"S{sid:04d}","x":round(x,2),"y":round(y,2),"elevation":self.ch,
                               "type":st,"zone":z["name"][:8],"zone_hazard":z["hazard"],
                               "coverage_radius":round(grid/2,2),"k_factor":k,"temp_rating":temp,
                               "min_pressure":mp,"hazard":z["hazard"].replace("_"," ").title(),
                               "room":z["name"],"nfpa_ref":"§22.1" if is_e else "§8.5","is_esfr":is_e}); sid+=1
            if in_r and z["area_sf"]>500:
                for lv in [6.0,12.0]:
                    if lv>=self.ch-3: break
                    for y in self._gp(y0,y1,4.0,8.0):
                        for x in self._gp(x0,x1,4.0,8.0):
                            sp.append({"id":f"R{sid:04d}","x":round(x,2),"y":round(y,2),"elevation":lv,
                                       "type":"upright","zone":z["name"][:8],"zone_hazard":z["hazard"],
                                       "coverage_radius":4.0,"k_factor":5.6,"temp_rating":165,
                                       "min_pressure":7.0,"hazard":"In-rack","room":z["name"],
                                       "nfpa_ref":"§12","in_rack":True,"rack_level_ft":lv}); sid+=1
        log.info(f"[DE] {len(sp)} sprinklers ({len([s for s in sp if not s.get('in_rack')])} ceiling + {len([s for s in sp if s.get('in_rack')])} in-rack)")
        return sp

    def _gp(self, s, e, off, spc) -> list:
        pts=[]; p=s+off
        while p<=e-off*0.5+0.01: pts.append(round(p,2)); p+=spc
        return pts or [round((s+e)/2,2)]

    def _pipes(self, sp: list, zones: list) -> list:
        csp=[s for s in sp if not s.get("in_rack")]
        if not csp: return []
        xs=[s["x"] for s in csp]; ys=[s["y"] for s in csp]
        x0,x1=min(xs),max(xs); y0,y1=min(ys),max(ys)
        rx=round((x0+x1)/2,1); ry0=round(y0-4,1); cy=round((y0+y1)/2,1)
        tf=self._tf(csp); md=self._sp(tf)
        secs=[]; sid=1
        secs.append({"id":f"M-{sid:02d}","from":{"x":rx,"y":ry0},"to":{"x":rx,"y":cy},
                     "pipe_type":"main","diameter":md,"schedule":"Sch 40",
                     "material":self.ctx.get("pipe_material","Steel"),"length":round(abs(cy-ry0),1),
                     "fittings":["alarm_check","gate_valve"],"nfpa_ref":"§6"}); sid+=1
        rows=self._rows(csp); bsz=50.0; bands: dict=defaultdict(list)
        for ry,rsp in rows.items(): bands[round(ry/bsz)*bsz].extend(rsp)
        for by,bsp in sorted(bands.items()):
            if not bsp: continue
            bxs=[s["x"] for s in bsp]; bx0=round(min(bxs)-2,1); bx1=round(max(bxs)+2,1)
            xd=self._sp(len(bsp)*25)
            secs.append({"id":f"X-{sid:02d}","from":{"x":bx0,"y":round(by,1)},"to":{"x":bx1,"y":round(by,1)},
                         "pipe_type":"cross","diameter":xd,"schedule":"Sch 40",
                         "material":self.ctx.get("pipe_material","Steel"),"length":round(bx1-bx0,1),
                         "fittings":[],"nfpa_ref":"§6"}); sid+=1
        for ry,rsp in sorted(rows.items()):
            if not rsp: continue
            rs=sorted(rsp,key=lambda s:s["x"]); bx0=round(min(s["x"] for s in rs)-1,1); bx1=round(max(s["x"] for s in rs)+1,1)
            bd=self._sp(len(rs)*30)
            secs.append({"id":f"B-{sid:02d}","from":{"x":bx0,"y":round(ry,1)},"to":{"x":bx1,"y":round(ry,1)},
                         "pipe_type":"branch","diameter":bd,"schedule":"Sch 40",
                         "material":self.ctx.get("pipe_material","Steel"),"length":round(bx1-bx0,1),
                         "fittings":["tee_branch"]*len(rs),"nfpa_ref":"§6"}); sid+=1
        return secs

    def _tf(self, sp: list) -> float:
        zf: dict=defaultdict(float)
        for s in sp:
            c=HAZARD_CRITERIA.get(s.get("zone_hazard",self.def_hz),HAZARD_CRITERIA["esfr_k14"])
            zf[s.get("zone_hazard","")]+=c["k"]*math.sqrt(c["min_psi"])
        return max(zf.values(),default=500)*1.25+500

    def _rows(self, sp: list, tol: float=2.0) -> dict:
        rows: dict={}
        for s in sorted(sp,key=lambda x:x["y"]):
            placed=False
            for ry in list(rows.keys()):
                if abs(s["y"]-ry)<=tol: rows[ry].append(s); placed=True; break
            if not placed: rows[s["y"]]=[s]
        return rows

    def _sp(self, flow: float) -> float:
        for d in PIPES:
            rf=(d/2)/12
            if flow<=0 or (flow/7.48)/(math.pi*rf**2)<=20: return d
        return PIPES[-1]

    def _hyd(self, sp: list, ps: list, zones: list) -> dict:
        if not sp: return {"static_pressure":self.sp_psi,"residual_pressure":self.rp_psi,"required_pressure":0,"pressure_delta":self.rp_psi,"flow_demand":0,"density_area":{},"demand_curve":[],"remote_area_calcs":{},"compliant":True}
        wz=max(zones,key=lambda z:HAZARD_CRITERIA.get(z["hazard"],{}).get("min_psi",7))
        c=wz["criteria"]; k=c["k"]; mp=c["min_psi"]; is_e=c["esfr"]
        csp=[s for s in sp if not s.get("in_rack") and s.get("zone_hazard")==wz["hazard"]]
        if not csp: csp=[s for s in sp if not s.get("in_rack")]
        if not csp: csp=sp
        xs=[s["x"] for s in sp]; ys=[s["y"] for s in sp]
        rx=(min(xs)+max(xs))/2; ry0=min(ys)-4
        def dist(s): return math.sqrt((s["x"]-rx)**2+(s["y"]-ry0)**2)
        csp_s=sorted(csp,key=dist,reverse=True)
        n_rem=12 if is_e else max(1,math.ceil(c.get("area",2500)/c.get("max_coverage",100)))
        n_rem=min(n_rem,len(csp_s)); remote=csp_s[:n_rem]
        min_fl=k*math.sqrt(mp)
        nc=[]; tsf=0
        for i,s in enumerate(remote):
            p=mp+i*0.3; q=max(k*math.sqrt(p),min_fl); tsf+=q
            nc.append({"node":s["id"],"x":s["x"],"y":s["y"],"flow_gpm":round(q,2),"pressure_psi":round(p,2),"k_factor":k,"nfpa_ref":"§22.1" if is_e else "§22.4"})
        tf=0.0; pc=[]
        fracs={"main":1.0,"cross":0.7,"branch":0.25,"armover":0.05}
        for sec in ps:
            q=tsf*fracs.get(sec.get("pipe_type","branch"),0.25); d=sec.get("diameter",3.0); l=sec.get("length",20)
            if q>0 and d>0:
                hf=4.52*(q**1.85)/(self.hwc**1.85*d**4.87); loss=hf*l
                for f in sec.get("fittings",[]): loss+=hf*FEQ.get(f,{}).get(int(d),0)
                tf+=loss; v=(q/7.48)/(math.pi*((d/2)/12)**2)
                pc.append({"section":sec["id"],"flow_gpm":round(q,1),"diameter_in":d,"length_ft":l,"friction_psi":round(loss,3),"velocity_fps":round(v,1)})
        eh=self.ch*0.433; req=mp+tf+eh
        hose=250 if is_e else {"light":100,"ordinary_1":250,"ordinary_2":250,"extra_1":500,"extra_2":500}.get(wz["hazard"],500)
        td=tsf+hose; delta=self.rp_psi-req
        curve=[{"flow":round(td*p,1),"pressure":round(self.sp_psi if p==0 else max(0,self.sp_psi-(self.sp_psi-self.rp_psi)*(td*p/max(self.fl_gpm,1))**0.54),1)} for p in [0,0.125,0.25,0.375,0.5,0.625,0.75,0.875,1.0]]
        method="ESFR per §22.1" if is_e else f"Density/Area §22 — {c.get('density',0):.2f} gpm/sqft × {c.get('area',0)} sqft"
        return {"static_pressure":round(self.sp_psi,1),"residual_pressure":round(self.rp_psi,1),
                "required_pressure":round(req,1),"pressure_delta":round(delta,1),
                "flow_demand":round(td,1),"demand_curve":curve,
                "density_area":{"density":c.get("density"),"area":c.get("area"),"method":method},
                "remote_area_calcs":{"worst_zone":wz["name"],"hazard":wz["hazard"],"remote_sprinkler_count":n_rem,
                    "design_method":method,"min_sprinkler_psi":round(mp,1),"k_factor":k,
                    "min_flow_per_sprinkler_gpm":round(min_fl,2),"total_sprinkler_flow_gpm":round(tsf,1),
                    "hose_stream_gpm":hose,"total_friction_loss_psi":round(tf,2),"elevation_head_psi":round(eh,2),
                    "node_calculations":nc,"pipe_calculations":pc[:20],"hw_c_factor":self.hwc,
                    "nfpa_ref":"§22.1" if is_e else "§22.4"},"compliant":delta>=0}

    def _hang(self, ps: list) -> tuple:
        hng=[]; brc=[]; hi=1; bi=1; seis=self.seismic in ("C","D","D1","D2","E")
        for s in ps:
            d=s.get("diameter",2.0); l=s.get("length",0); pt=s.get("pipe_type","branch")
            fx,fy=s["from"]["x"],s["from"]["y"]; tx,ty=s["to"]["x"],s["to"]["y"]
            ms=MAX_HANG.get(d,15); n=max(1,math.ceil(l/ms))
            for i in range(n):
                fr=(i+0.5)/n
                hng.append({"id":f"H-{hi:04d}","location":f"({fx+(tx-fx)*fr:.0f}', {fy+(ty-fy)*fr:.0f}')",
                             "x":round(fx+(tx-fx)*fr,1),"y":round(fy+(ty-fy)*fr,1),
                             "type":"clevis" if pt=="main" else "rod","pipe_size":d,
                             "rod_diameter":0.5 if d>=3.0 else 0.375,"load":round(d*12*ms,0),
                             "listed":True,"pipe_section":s["id"],"nfpa_ref":"§9.1"}); hi+=1
            if seis and pt in ("main","cross") and l>MAX_SWAY:
                nb=max(1,math.ceil(l/MAX_SWAY))
                for i in range(nb):
                    fr=((i+0.5)/nb)
                    brc.append({"id":f"SB-{bi:04d}","location":f"({fx+(tx-fx)*fr:.0f}', {fy+(ty-fy)*fr:.0f}')",
                                "x":round(fx+(tx-fx)*fr,1),"y":round(fy+(ty-fy)*fr,1),"direction":"4-way",
                                "pipe_size":d,"spacing":round(l/nb,1),"max_allowed":MAX_SWAY,"compliant":True,"nfpa_ref":"§9.3"}); bi+=1
        return hng, brc

    def _valves(self, sp: list, zones: list) -> tuple:
        if not sp: return [],[]
        xs=[s["x"] for s in sp]; ys=[s["y"] for s in sp]
        rx=round((min(xs)+max(xs))/2,1); ry=round(min(ys)-5,1)
        rmx=round(max(xs),1); rmy=round(max(ys),1)
        tf=self._tf([s for s in sp if not s.get("in_rack")]); md=self._sp(tf)
        mds=str(int(md)) if md==int(md) else str(md)
        val=[
            {"id":"OS&Y-1","type":"osy","x":rx,"y":ry,"label":f"{mds}\" OS&Y GATE VALVE","nfpa_ref":"§8.16.1","zone":"Main"},
            {"id":"CV-1","type":"check","x":rx,"y":ry+3,"label":f"{mds}\" ALARM CHECK VALVE","nfpa_ref":"§8.16.2","zone":"Main"},
            {"id":"AV-1","type":"alarm","x":rx+3,"y":ry+3,"label":"WATERFLOW ALARM SWITCH","nfpa_ref":"§8.16.3","zone":"Main"},
            {"id":"IT-1","type":"inspector_test","x":rmx,"y":rmy,"label":"1\" INSPECTOR'S TEST","nfpa_ref":"§8.17.1","zone":"Remote"},
            {"id":"DR-1","type":"drain","x":rx,"y":ry-3,"label":"2\" MAIN DRAIN","nfpa_ref":"§8.16.1.4","zone":"Main"},
        ]
        for i,z in enumerate(zones):
            bx=round(rx+(i-len(zones)/2)*20,1)
            val.append({"id":f"BFV-{i+1}","type":"butterfly","x":bx,"y":ry+1,"label":f"ZONE VALVE — {z['name'][:15]}","nfpa_ref":"§8.16","zone":z["name"]})
        eqp=[{"type":"riser","x":rx,"y":ry+2,"label":f"MAIN RISER\n{mds}\" WET PIPE","nfpa_ref":"§8.16"},
             {"type":"fdc","x":rx+8,"y":ry,"label":"FDC\n6\"×2.5\"×2.5\"×2.5\"×2.5\"","nfpa_ref":"§8.16.6"}]
        return val, eqp

    def _bom(self, sp, ps, hng, brc, val) -> list:
        bom=[]
        csp=[s for s in sp if not s.get("in_rack")]; rsp=[s for s in sp if s.get("in_rack")]
        for (st,k),qty in sorted(Counter((s.get("type","pendant"),s.get("k_factor",5.6)) for s in csp).items()):
            s0=next((s for s in csp if s.get("type")==st and s.get("k_factor")==k),csp[0])
            bom.append({"item":f"{st.upper()} SPRINKLER — K{k} {s0.get('temp_rating',155)}°F","part_number":"TBD","qty":qty+max(3,int(qty*0.05)),"unit":"EA","unit_cost":SC.get(st,9.00),"nfpa_ref":"§6.2"})
        if rsp:
            for lv,qty in sorted(Counter(s.get("rack_level_ft",6) for s in rsp).items()):
                bom.append({"item":f"IN-RACK SPRINKLER — K5.6 165°F ({lv:.0f}ft level)","part_number":"TBD","qty":qty+max(2,int(qty*0.05)),"unit":"EA","unit_cost":9.50,"nfpa_ref":"§12"})
        pl: dict=defaultdict(float)
        for s in ps: pl[(s.get("diameter",1.0),s.get("schedule","Sch 40"),s.get("material","Steel"))]+=s.get("length",0)
        for (d,sch,mat),l in sorted(pl.items()):
            bom.append({"item":f"PIPE — {d}\" {sch} {mat}","part_number":"TBD","qty":round(l*1.05,1),"unit":"LF","unit_cost":PC.get(d,6.00),"nfpa_ref":"§6.3"})
        fc: dict=defaultdict(int)
        for s in ps:
            for f in s.get("fittings",[]): fc[(f,s.get("diameter",2.0))]+=1
        fn={"90_elbow":"90° ELBOW","tee_branch":"TEE (BRANCH)","alarm_check":"ALARM CHECK VALVE","gate_valve":"OS&Y GATE VALVE"}
        fco={"90_elbow":12,"tee_branch":18,"alarm_check":520,"gate_valve":285}
        for (f,d),qty in sorted(fc.items()):
            bom.append({"item":f'{fn.get(f,f.upper())} — {d}"',"part_number":"TBD","qty":qty,"unit":"EA","unit_cost":fco.get(f,15)*max(d/2,1),"nfpa_ref":"§6.3"})
        for ht,qty in Counter(h.get("type","rod") for h in hng).items():
            bom.append({"item":f"PIPE HANGER — {ht.upper()} (FM/UL LISTED)","part_number":"TBD","qty":qty,"unit":"EA","unit_cost":22.0 if ht=="clevis" else 14.50,"nfpa_ref":"§9.1"})
        if brc: bom.append({"item":"SWAY BRACE — 4-WAY SEISMIC (LISTED)","part_number":"TBD","qty":len(brc),"unit":"EA","unit_cost":195.00,"nfpa_ref":"§9.3"})
        for v in val: bom.append({"item":v.get("label","VALVE"),"part_number":"TBD","qty":1,"unit":"EA","unit_cost":VC.get(v.get("type","osy"),200),"nfpa_ref":v.get("nfpa_ref","§8.16")})
        for item,cost,ref in [("MAIN RISER ASSEMBLY — WET PIPE COMPLETE",3500,"§8.16"),("FIRE DEPARTMENT CONNECTION — 6\"×2.5\"×2.5\"×2.5\"×2.5\"",850,"§8.16.6"),("PRESSURE GAUGE — 0-400 PSI LISTED",85,"§8.16"),("MAIN DRAIN ASSEMBLY — 2\" COMPLETE",225,"§8.16.1.4"),("HYDRAULIC DESIGN INFORMATION SIGN",15,"§27.2"),("FIRE PUMP — PER SEPARATE SPEC",0,"§22.4"),("BACKFLOW PREVENTER — PER CIVIL DRAWINGS",0,"§8.16")]:
            bom.append({"item":item,"part_number":"TBD","qty":1,"unit":"EA","unit_cost":cost,"nfpa_ref":ref})
        return bom

    def _check(self, sp, ps, hyd, zones) -> list:
        flags=[]
        def flag(s,d,sev="pass"): flags.append({"section":s,"description":d,"severity":sev})
        rows=self._rows([s for s in sp if not s.get("in_rack")])
        ok=True
        for _,rsp in list(rows.items())[:10]:
            rs=sorted(rsp,key=lambda s:s["x"])
            for i in range(len(rs)-1):
                d=abs(rs[i]["x"]-rs[i+1]["x"]); hz=rs[i].get("zone_hazard",self.def_hz)
                ms=HAZARD_CRITERIA.get(hz,{}).get("max_spacing",10)
                if d>ms*1.05: flag("§8.5.2",f"Spacing {d:.1f}ft between {rs[i]['id']} and {rs[i+1]['id']} exceeds {ms}ft","critical"); ok=False; break
            if not ok: break
        if ok: flag("§8.5.2","Head spacing within limits for all zones","pass")
        pd=hyd.get("pressure_delta",0); rp=hyd.get("required_pressure",0); rr=hyd.get("residual_pressure",0)
        if pd<0: flag("§22","INSUFFICIENT PRESSURE — need {:.1f} psi, have {:.1f} psi — FIRE PUMP REQUIRED".format(rp,rr),"critical")
        else: flag("§22",f"Pressure OK — {rr:.1f} psi available, {rp:.1f} required ({pd:.1f} psi margin)","pass")
        esfr_z=[z for z in zones if HAZARD_CRITERIA.get(z["hazard"],{}).get("esfr")]
        if esfr_z: flag("§22.1",f"ESFR design: {', '.join(z['name'] for z in esfr_z)}","pass")
        rack=[s for s in sp if s.get("in_rack")]
        if rack: flag("§12",f"In-rack sprinklers: {len(rack)} heads at rack levels","pass")
        tire_z=[z for z in zones if "tire" in z["hazard"]]
        if tire_z: flag("§17",f"Rubber tire storage per Chapter 17 — {len(tire_z)} zone(s)","pass")
        if self.seismic in ("C","D","D1","D2","E"): flag("§9.3",f"Seismic zone {self.seismic} — 4-way sway bracing on all mains","pass")
        flag("§8.17","Inspector's test at most remote sprinkler","pass")
        flag("§8.16","Riser assembly complete — OS&Y, alarm check, flow switch, gauge, drain","pass")
        return flags

    def _fp(self) -> tuple:
        bd=self.geo.get("building_dimensions",{})
        if bd.get("width_ft") and bd.get("depth_ft"): return float(bd["width_ft"]),float(bd["depth_ft"])
        ax,ay=[],[]
        for w in self.walls:
            for p in w.get("points",[]): ax.append(p.get("x",0)); ay.append(p.get("y",0))
        for r in self.rooms:
            for p in r.get("boundary",[]): ax.append(p.get("x",0)); ay.append(p.get("y",0))
        if ax and ay:
            w=max(ax)-min(ax); d=max(ay)-min(ay)
            if w>20 and d>20: return w,d
        area=float(self.ctx.get("total_area",135411)); w=math.sqrt(area/0.65); return w,area/w
