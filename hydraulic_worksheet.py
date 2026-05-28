"""
FireAI Pro — NFPA 13 Hydraulic Calculation Worksheet Generator
===============================================================
Produces the complete NFPA 13 hydraulic calculation worksheet in the
standard industry format used by AutoSprink/HydraCALC.

Columns per NFPA 13 §28.2:
  From → To | Elev | K | Q_discharge | Q_total | Hose |
  Pipe type | Nom.Dia | Int.Dia | Fittings(ft) | Pipe(ft) | C | hf/ft |
  P_start | P_elev | P_friction → P_end
"""
from __future__ import annotations
import math
from dataclasses import dataclass

# Internal pipe diameters — NFPA 13 Table A.16.3.2
INTERNAL_DIA = {
    ("sch40",0.75):0.824, ("sch40",1.0):1.049, ("sch40",1.25):1.380,
    ("sch40",1.5):1.610,  ("sch40",2.0):2.067, ("sch40",2.5):2.469,
    ("sch40",3.0):3.068,  ("sch40",3.5):3.548, ("sch40",4.0):4.026,
    ("sch40",5.0):5.047,  ("sch40",6.0):6.065,
    ("sch10",1.0):1.097,  ("sch10",1.25):1.442,("sch10",1.5):1.682,
    ("sch10",2.0):2.157,  ("sch10",2.5):2.635, ("sch10",3.0):3.260,
    ("sch10",4.0):4.260,  ("sch10",5.0):5.295, ("sch10",6.0):6.357,
    ("cpvc", 0.75):0.874, ("cpvc", 1.0):1.101, ("cpvc", 1.25):1.394,
    ("cpvc", 1.5):1.602,  ("cpvc", 2.0):2.049,
    ("copper",0.75):0.785,("copper",1.0):1.025,("copper",1.25):1.265,
    ("copper",1.5):1.505, ("copper",2.0):1.985,
}

# Equivalent lengths — NFPA 13 Table 28.2.3.1.1 (Sch 40, C=120)
EQUIV_LEN = {
    "90_elbow":  {0.75:1,1.0:1,1.25:1,1.5:2,2.0:2,2.5:3,3.0:4,3.5:5,4.0:5,5.0:7,6.0:9},
    "45_elbow":  {0.75:1,1.0:1,1.25:1,1.5:1,2.0:1,2.5:2,3.0:2,3.5:3,4.0:3,5.0:4,6.0:5},
    "tee_branch":{0.75:4,1.0:4,1.25:5,1.5:5,2.0:8,2.5:10,3.0:12,3.5:14,4.0:15,5.0:20,6.0:25},
    "tee_run":   {0.75:1,1.0:1,1.25:1,1.5:1,2.0:2,2.5:2,3.0:3,3.5:3,4.0:4,5.0:5,6.0:6},
    "gate_valve":{2.0:1,2.5:1,3.0:2,4.0:2,5.0:3,6.0:3},
    "alarm_check":{2.0:10,2.5:12,3.0:14,4.0:18,5.0:22,6.0:28},
    "check_valve":{2.0:4,3.0:5,4.0:7,5.0:9,6.0:11},
    "backflow":  {2.0:68,3.0:72,4.0:76,5.0:80,6.0:88},
}

HW_C = {
    "schedule 40 steel":120,"sch40":120,"schedule 10 steel":120,"sch10":120,
    "cpvc":150,"copper":150,"stainless":140,"ci":140,"di":140,
}

# NFPA 13 pipe schedule — max sprinklers per diameter (Table 12.1/12.2)
SCHEDULE_LIGHT = [(1.0,2),(1.25,3),(1.5,5),(2.0,10),(2.5,20),(3.0,40),(3.5,65),(4.0,100),(5.0,999)]
SCHEDULE_ORD   = [(1.0,2),(1.25,3),(1.5,5),(2.0,10),(2.5,20),(3.0,40),(3.5,65),(4.0,100),(5.0,999)]


@dataclass
class CalcRow:
    """One row in the NFPA 13 hydraulic worksheet."""
    node_from:     str   = ""
    node_to:       str   = ""
    elev_from:     float = 0.0
    elev_to:       float = 0.0
    k_factor:      float = 0.0
    q_discharge:   float = 0.0   # flow leaving from-node sprinkler (gpm)
    q_total:       float = 0.0   # total flow through this section (gpm)
    q_hose:        float = 0.0   # hose added at this node
    pipe_type:     str   = "Sch 40"
    nom_dia:       float = 1.0
    int_dia:       float = 1.049
    fittings_desc: str   = ""
    fit_ft:        float = 0.0
    pipe_ft:       float = 0.0
    c_factor:      float = 120.0
    hf_per_ft:     float = 0.0
    p_start:       float = 0.0
    p_elev:        float = 0.0
    p_fric:        float = 0.0
    note:          str   = ""

    @property
    def total_ft(self):  return self.pipe_ft + self.fit_ft
    @property
    def p_end(self):     return self.p_start + self.p_elev + self.p_fric


def _id(nom):
    s = "sch40"
    return INTERNAL_DIA.get((s, nom), nom * 0.96)

def _eq(fitting, nom):
    t = EQUIV_LEN.get(fitting, {})
    for k in sorted(t.keys()):
        if k >= nom: return t[k]
    return list(t.values())[-1] if t else 0.0

def _hw(Q, C, d):
    if Q <= 0 or d <= 0 or C <= 0: return 0.0
    return 4.52 * (Q**1.85) / ((C**1.85) * (d**4.87))

def _sched(n_heads, hazard="light"):
    tbl = SCHEDULE_LIGHT if "light" in hazard else SCHEDULE_ORD
    for d, mx in tbl:
        if n_heads <= mx: return d
    return 5.0

def _pipe_type_str(mat):
    ml = mat.lower()
    if "10" in ml: return "Sch 10"
    if "cpvc" in ml: return "CPVC"
    if "copper" in ml: return "Copper Type L"
    return "Sch 40"

def _C(mat):
    ml = mat.lower().replace(" ","")
    for k, v in HW_C.items():
        if k.replace(" ","") in ml: return v
    return 120


def build_hydraulic_worksheet(design_result: dict, project: dict) -> dict:
    """
    Build the complete NFPA 13 hydraulic calculation worksheet.
    Returns rows (list of CalcRow), summary, supply_curve, design_criteria.
    """
    ra      = design_result.get("remote_area_calcs", {})
    h       = design_result
    mat     = project.get("pipe_material","Schedule 40 Steel")
    ptype   = _pipe_type_str(mat)
    C_above = _C(mat)
    C_below = 140    # underground CI/DI
    ch      = float(project.get("ceiling_height", 10))
    sp_psi  = float(h.get("static_pressure", 72))
    res_psi = float(h.get("residual_pressure", 60))
    fl_gpm  = float(project.get("water_supply_flow", 1500))
    K       = float(ra.get("k_factor", 5.6))
    P_min   = float(ra.get("min_sprinkler_psi", 7.0))
    n_rem   = int(ra.get("remote_sprinkler_count", 8))
    hose    = float(ra.get("hose_stream_gpm", 100))
    hz      = ra.get("hazard","light")

    # Geometry from design
    cpl    = ra.get("critical_path_lengths_ft", {})
    L_main = float(cpl.get("main",  150.0))
    L_xmain= float(cpl.get("cross",  40.0))

    # How many sprinklers per branch, how many branches
    n_per_br = max(1, int(math.ceil(math.sqrt(n_rem))))
    n_br     = max(1, int(math.ceil(n_rem / n_per_br)))
    # Actual head spacing
    head_sp  = min(15.0, max(6.0, L_xmain / max(n_per_br-1,1)))

    rows: list[CalcRow] = []

    # ── BRANCH LINE: most remote n_per_br sprinklers ──────────────────────────
    # Walk from most remote head (S01) toward cross-main junction (C-01)
    # Pipe between node i and node i+1 carries flow from heads 1..i+1
    p_cur = 0.0   # running pressure
    q_acc = 0.0   # accumulated flow in this branch

    for i in range(n_per_br):
        nf  = f"S{str(i+1).zfill(2)}"
        nt  = f"S{str(i+2).zfill(2)}" if i < n_per_br-1 else "C-01"

        # Pressure and flow at this head
        if i == 0:
            p_head = P_min
        else:
            p_head = p_cur    # use end pressure of previous section

        p_head = max(p_head, P_min)
        q_head = K * math.sqrt(p_head)
        q_acc += q_head
        p_cur  = p_head      # starting pressure for the pipe leaving this node

        # Pipe size: feeds q_acc through (i+1) sprinklers
        # Per NFPA 13 schedule table
        nom_d = _sched(i+1, hz)   # i+1 = number of heads fed through this pipe
        int_d = _id(nom_d)
        C     = C_above

        # Pipe length to next head
        seg_len = head_sp if i < n_per_br-1 else max(4.0, head_sp/2)

        # Fittings
        if i == 0:
            fdesc = "Tee (branch), 90° Elbow"
            fft   = _eq("tee_branch", nom_d) + _eq("90_elbow", nom_d)
        elif i == n_per_br-1:
            fdesc = "Tee (branch)"
            fft   = _eq("tee_branch", nom_d)
        else:
            fdesc = "Tee (branch)"
            fft   = _eq("tee_branch", nom_d)

        hf    = _hw(q_acc, C, int_d)
        pfric = hf * (seg_len + fft)
        pelev = 0.0

        rows.append(CalcRow(
            node_from=nf, node_to=nt,
            elev_from=ch, elev_to=ch,
            k_factor=K, q_discharge=round(q_head,2),
            q_total=round(q_acc,2), q_hose=0.0,
            pipe_type=ptype, nom_dia=nom_d, int_dia=int_d,
            fittings_desc=fdesc, fit_ft=round(fft,1),
            pipe_ft=round(seg_len,1),
            c_factor=C, hf_per_ft=round(hf,4),
            p_start=round(p_cur,2), p_elev=0.0,
            p_fric=round(pfric,2),
            note="Hydraulic reference — most remote" if i==0 else "",
        ))
        p_cur = rows[-1].p_end

    # ── CROSS-MAIN: picks up additional branch flows ───────────────────────────
    # Each additional branch is assumed similar to branch 1.
    # Use equivalent K factor: K_branch_eq = q_branch / sqrt(P_junction)
    # Then Q_from_branch_n = K_eq × sqrt(P_at_that_junction)
    cross_node_in = "C-01"
    branch_K_eq   = q_acc / math.sqrt(p_cur) if p_cur > 0 else q_acc

    cross_dia = _sched(n_rem, hz)   # size for all remote heads
    xmain_seg = L_xmain / max(n_br-1, 1)

    for br in range(1, n_br):   # additional branches beyond branch 1
        cross_node_out = f"C-{str(br+1).zfill(2)}"
        n_in_this_br   = min(n_per_br, n_rem - n_per_br*br)
        if n_in_this_br <= 0: break

        q_this_br  = n_in_this_br * K * math.sqrt(p_cur)
        q_new_total= q_acc + q_this_br
        nom_d      = cross_dia
        int_d      = _id(nom_d)

        fft  = _eq("tee_run", nom_d) + (
               _eq("tee_branch", nom_d) if br < n_br-1 else 0)
        fdesc= "Tee (run)" + (", Tee (branch)" if br < n_br-1 else "")
        hf   = _hw(q_new_total, C_above, int_d)
        pfric= hf * (xmain_seg + fft)

        rows.append(CalcRow(
            node_from=cross_node_in, node_to=cross_node_out,
            elev_from=ch, elev_to=ch,
            k_factor=round(branch_K_eq,2), q_discharge=round(q_this_br,2),
            q_total=round(q_new_total,2), q_hose=0.0,
            pipe_type=ptype, nom_dia=nom_d, int_dia=int_d,
            fittings_desc=fdesc, fit_ft=round(fft,1),
            pipe_ft=round(xmain_seg,1),
            c_factor=C_above, hf_per_ft=round(hf,4),
            p_start=round(p_cur,2), p_elev=0.0,
            p_fric=round(pfric,2),
            note=f"Branch {br+1}: {n_in_this_br} head(s) × K={branch_K_eq:.1f}",
        ))
        p_cur  = rows[-1].p_end
        q_acc  = q_new_total
        cross_node_in = cross_node_out

    # ── SUPPLY MAIN: last cross-main junction → Top of Riser ──────────────────
    main_nom  = max(cross_dia, 4.0)
    main_int  = _id(main_nom)
    main_fft  = (_eq("tee_run", main_nom)*2 + _eq("tee_branch", main_nom))
    hf_main   = _hw(q_acc, C_above, main_int)
    pfric_main= hf_main * (L_main + main_fft)

    rows.append(CalcRow(
        node_from=cross_node_in, node_to="TOR",
        elev_from=ch, elev_to=ch,
        k_factor=0.0, q_discharge=0.0,
        q_total=round(q_acc,2), q_hose=0.0,
        pipe_type=ptype, nom_dia=main_nom, int_dia=main_int,
        fittings_desc="Tee (run) ×2, Tee (branch)",
        fit_ft=round(main_fft,1), pipe_ft=round(L_main,1),
        c_factor=C_above, hf_per_ft=round(hf_main,4),
        p_start=round(p_cur,2), p_elev=0.0,
        p_fric=round(pfric_main,2),
        note="Supply main to top of riser",
    ))
    p_cur = rows[-1].p_end

    # ── RISER: Top of Riser → Bottom of Riser (elevation gain) ───────────────
    riser_fft  = (_eq("gate_valve",   main_nom) +
                  _eq("alarm_check",  main_nom) +
                  _eq("check_valve",  main_nom))
    hf_riser   = _hw(q_acc, C_above, main_int)
    pfric_riser= hf_riser * (10.0 + riser_fft)
    pelev_riser= ch * 0.433   # elevation gain — water flowing down

    rows.append(CalcRow(
        node_from="TOR", node_to="BOR",
        elev_from=ch, elev_to=0.0,
        k_factor=0.0, q_discharge=0.0,
        q_total=round(q_acc,2), q_hose=0.0,
        pipe_type=ptype, nom_dia=main_nom, int_dia=main_int,
        fittings_desc="OS&Y Gate Valve, Alarm Check Valve, Check Valve",
        fit_ft=round(riser_fft,1), pipe_ft=10.0,
        c_factor=C_above, hf_per_ft=round(hf_riser,4),
        p_start=round(p_cur,2),
        p_elev=round(pelev_riser,2),    # positive = pressure gain
        p_fric=round(pfric_riser,2),
        note="Riser assembly — includes elevation gain",
    ))
    p_cur = rows[-1].p_end

    # ── SUPPLY NODE: Add hose stream + underground pipe to hydrant ────────────
    q_total_hose = q_acc + hose
    ug_nom    = max(main_nom, 4.0)
    ug_int    = _id(ug_nom) * 1.02   # DI slightly larger ID
    hf_ug     = _hw(q_total_hose, C_below, ug_int)
    fft_ug    = _eq("90_elbow", ug_nom) + _eq("gate_valve", ug_nom)
    pfric_ug  = hf_ug * (20.0 + fft_ug)
    pelev_ug  = -2.0 * 0.433   # service tap ~2ft below grade

    rows.append(CalcRow(
        node_from="BOR", node_to="SUPPLY",
        elev_from=0.0, elev_to=-2.0,
        k_factor=0.0, q_discharge=0.0,
        q_total=round(q_total_hose,2), q_hose=round(hose,0),
        pipe_type="CI/DI Class 52", nom_dia=ug_nom, int_dia=round(ug_int,3),
        fittings_desc=f"90° Elbow, Gate Valve (+{hose:.0f} gpm hose stream)",
        fit_ft=round(fft_ug,1), pipe_ft=20.0,
        c_factor=C_below, hf_per_ft=round(hf_ug,4),
        p_start=round(p_cur,2),
        p_elev=round(pelev_ug,3),
        p_fric=round(pfric_ug,2),
        note=f"Add {hose:.0f} gpm hose stream allowance (NFPA 13 §22.3)",
    ))
    p_required = rows[-1].p_end

    # ── Supply curve & summary ────────────────────────────────────────────────
    q_demand = q_total_hose
    if fl_gpm > 0:
        p_avail = max(0, sp_psi - (sp_psi-res_psi)*(q_demand/fl_gpm)**0.54)
    else:
        p_avail = res_psi

    supply_curve = []
    for frac in [0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.1,1.2]:
        q = q_demand * frac
        p = max(0, sp_psi-(sp_psi-res_psi)*(q/max(fl_gpm,1))**0.54) if fl_gpm>0 else sp_psi
        supply_curve.append({"flow":round(q,1),"pressure":round(p,1)})

    delta    = p_avail - p_required
    compliant= delta >= 0

    return {
        "rows":   rows,
        "summary":{
            "total_flow_gpm":        round(q_demand,1),
            "sprinkler_flow_gpm":    round(q_acc,1),
            "hose_stream_gpm":       hose,
            "required_pressure_psi": round(p_required,1),
            "available_pressure_psi":round(p_avail,1),
            "pressure_margin_psi":   round(delta,1),
            "compliant":             compliant,
            "fire_pump_required":    not compliant,
        },
        "supply_curve":  supply_curve,
        "demand_point":  {"flow":round(q_demand,1),"pressure":round(p_required,1)},
        "design_criteria":{
            "occupancy":   project.get("occupancy",""),
            "system_type": project.get("system_type","Wet Pipe"),
            "hazard":      hz,
            "design_method":ra.get("design_method",""),
            "k_factor":    K,
            "min_psi":     P_min,
            "min_flow_gpm":round(K*math.sqrt(P_min),2),
            "n_remote":    n_rem,
            "hose_gpm":    hose,
            "hw_c_factor": C_above,
            "pipe_material":mat,
            "nfpa_edition":"NFPA 13 — Current Edition",
        },
    }
