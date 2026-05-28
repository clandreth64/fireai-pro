"""
FireAI Pro — Isometric View Builder
=====================================
Generates FP-3 Isometric Sheet from pipe_sections + sprinkler_placements.
Matches Battalion One / AutoSprink isometric output quality.

Projection: Standard 30° trimetric (X-right, Y at 30°, Z vertical)
  iso_x = (world_x + world_y) * cos(30°) * scale
  iso_y = (world_y - world_x) * sin(30°) * scale + world_z * scale_z

Color convention (matches AutoSprink):
  Mains / cross-mains  : colors.RED  (lineweight 50)
  Branch lines         : colors.RED  (lineweight 18)
  Riser / verticals    : color 8     (dark gray, lineweight 50)
  Sway brace tags      : colors.CYAN (filled box)
  Pipe labels          : colors.WHITE for size, colors.BLUE for length/elevation
  Branch line IDs      : colors.CYAN boxes
  Sprinkler drops      : color 0/black (lineweight 9)
"""
from __future__ import annotations
import math
from collections import defaultdict

import ezdxf
from ezdxf import colors
from ezdxf.enums import TextEntityAlignment

# ── Projection ────────────────────────────────────────────────────────────────

def _iso(wx: float, wy: float, wz: float,
         scale: float, scale_z: float,
         ox: float = 0, oy: float = 0) -> tuple[float,float]:
    """
    Project 3D world coords → 2D isometric coords.
    Origin (0,0,0) maps to (ox,oy) on sheet.
    X-axis: going right-downward  (right face)
    Y-axis: going left-upward     (left face)
    Z-axis: going straight up
    """
    rad = math.radians(30)
    ix  = (wx - wy) * math.cos(rad) * scale
    iy  = (wx + wy) * math.sin(rad) * scale + wz * scale_z
    return ox + ix, oy + iy


def _pipe_ang(p1: tuple, p2: tuple) -> float:
    """2D angle from p1 to p2 in degrees."""
    return math.degrees(math.atan2(p2[1]-p1[1], p2[0]-p1[0]))


def _perp_offset(p1, p2, off: float) -> tuple:
    """Point offset perpendicular from midpoint of p1→p2."""
    mx, my = (p1[0]+p2[0])/2, (p1[1]+p2[1])/2
    ang    = math.atan2(p2[1]-p1[1], p2[0]-p1[0]) + math.pi/2
    return mx + off*math.cos(ang), my + off*math.sin(ang)


def _fmt_len(ft: float) -> str:
    """Format length in feet as 'ft-in' string: 21.5 → '21-6'"""
    f  = int(ft)
    i  = int(round((ft - f) * 12))
    if i >= 12: f += 1; i = 0
    return f"{f}-{i}" if i else f"{f}-0"


def _fmt_dia(d) -> str:
    """Format diameter as fraction string: 2.5 → '2½'"""
    m = {0.75:"¾",1.0:"1",1.25:"1¼",1.5:"1½",2.0:"2",
         2.5:"2½",3.0:"3",3.5:"3½",4.0:"4",5.0:"5",6.0:"6",8.0:"8"}
    try: return m.get(float(d), str(d))
    except: return str(d)


# ── Block definitions ─────────────────────────────────────────────────────────

def _define_blocks(doc):
    """Define reusable blocks for isometric symbols."""
    # Sprinkler head (small circle + cross)
    if "ISO_SPKR" not in doc.blocks:
        b = doc.blocks.new("ISO_SPKR")
        b.add_circle((0,0), 4, dxfattribs={"color":0})
        b.add_line((-4,0),(4,0),  dxfattribs={"color":0,"lineweight":9})
        b.add_line((0,-4),(0,4),  dxfattribs={"color":0,"lineweight":9})

    # End of line restraint (X mark)
    if "ISO_EOL" not in doc.blocks:
        b = doc.blocks.new("ISO_EOL")
        s = 5
        b.add_line((-s,-s),(s,s), dxfattribs={"color":0,"lineweight":13})
        b.add_line((s,-s),(-s,s), dxfattribs={"color":0,"lineweight":13})

    # Sway brace tick (diagonal slash)
    if "ISO_BRACE" not in doc.blocks:
        b = doc.blocks.new("ISO_BRACE")
        b.add_line((-6,-6),(6,6), dxfattribs={"color":colors.YELLOW,"lineweight":18})

    # Hanger tick (√ shape)
    if "ISO_HANG" not in doc.blocks:
        b = doc.blocks.new("ISO_HANG")
        b.add_line((0,0),(-3,-4), dxfattribs={"color":colors.CYAN,"lineweight":9})
        b.add_line((0,0),(3,-4),  dxfattribs={"color":colors.CYAN,"lineweight":9})

    # Grooved coupling (orange X)
    if "ISO_GRVD" not in doc.blocks:
        b = doc.blocks.new("ISO_GRVD")
        s = 4
        b.add_line((-s,-s),(s,s), dxfattribs={"color":30,"lineweight":13})
        b.add_line((s,-s),(-s,s), dxfattribs={"color":30,"lineweight":13})


# ── Cyan tag box ──────────────────────────────────────────────────────────────

def _draw_tag(msp, cx: float, cy: float, text: str,
              ht: float = 8, pad: float = 4,
              bg_color: int = colors.CYAN, txt_color: int = 0):
    """Draw a filled cyan box with text — matches AutoSprink brace/BL tags."""
    tw  = len(text) * ht * 0.65
    bx0 = cx - tw/2 - pad
    bx1 = cx + tw/2 + pad
    by0 = cy - ht/2 - pad/2
    by1 = cy + ht/2 + pad/2
    msp.add_solid([(bx0,by0),(bx1,by0),(bx1,by1),(bx0,by1)],
                  dxfattribs={"layer":"FP-HNGR","color":bg_color})
    msp.add_text(text, dxfattribs={"layer":"FP-ANNO-LABL","height":ht,
                                    "style":"ROMAND","color":txt_color}
    ).set_placement((cx,cy-ht*0.35), align=TextEntityAlignment.MIDDLE_CENTER)


# ── Main builder ──────────────────────────────────────────────────────────────

def build_isometric(
    msp,
    pipe_sections:  list,
    sprinklers:     list,
    valves:         list,
    hangers:        list,
    sway_braces:    list,
    cad_output:     dict,
    project:        dict,
    origin_x:       float,
    origin_y:       float,
    iso_scale:      float,
    iso_scale_z:    float,
):
    """
    Render the full isometric pipe network into msp.

    All 3D world coordinates are in feet.
    iso_scale     = DXF units per foot (X/Y)
    iso_scale_z   = DXF units per foot (Z/vertical — typically 1.2× horizontal)
    origin_x/y    = sheet position of world (0,0,0)
    """
    def pt(wx, wy, wz=0):
        return _iso(wx, wy, wz, iso_scale, iso_scale_z, origin_x, origin_y)

    def T(text, x, y, ht=7, rot=0, color=colors.WHITE, bold=False):
        style = "ROMAND" if bold else "ROMANS"
        msp.add_text(str(text), dxfattribs={"layer":"FP-ANNO-LABL",
            "height":ht,"style":style,"rotation":rot,"color":color}
        ).set_placement((x,y))

    # ── Building footprint (floor level, z=0) ────────────────────────────────
    bw = float(cad_output.get("design_metadata",{}).get("building_w_ft",100))
    bd = float(cad_output.get("design_metadata",{}).get("building_d_ft",65))
    outline_pts = [pt(0,0,0), pt(bw,0,0), pt(bw,bd,0), pt(0,bd,0)]
    msp.add_lwpolyline(outline_pts + [outline_pts[0]],
                       dxfattribs={"layer":"A-SLAB","color":9,"lineweight":18})

    # ── Sprinkler ceiling level outline (faint dashed) ───────────────────────
    ch   = float(project.get("ceiling_height",12))
    ceil_pts = [pt(0,0,ch), pt(bw,0,ch), pt(bw,bd,ch), pt(0,bd,ch)]
    msp.add_lwpolyline(ceil_pts + [ceil_pts[0]],
                       dxfattribs={"layer":"A-CEIL","color":8,
                                   "lineweight":9,"linetype":"DASHED"})

    # ── Group branch lines for BL numbering ──────────────────────────────────
    # With per-span routing, each branch row has many sections.
    # Group by Y coordinate to identify distinct branch lines,
    # then tag only the longest span per line (most visible span).
    branches = [s for s in pipe_sections if s.get("pipe_type")=="branch"]
    mains    = [s for s in pipe_sections if s.get("pipe_type") in ("main","cross")]
    armovers = [s for s in pipe_sections if s.get("pipe_type")=="armover"]

    # Group branch spans by their row coordinate (Y or X of the branch line)
    from collections import defaultdict
    import math as _m
    bl_rows: dict = defaultdict(list)
    for br in branches:
        row_coord = round(br["from"]["y"] if abs(br["to"]["x"]-br["from"]["x"]) > 0.1
                         else br["from"]["x"])
        bl_rows[row_coord].append(br)

    bl_num: dict = {}  # section_id → BL number (only longest span per row tagged)
    for i, (row_coord, row_spans) in enumerate(
            sorted(bl_rows.items(), key=lambda kv: kv[0])):
        if not row_spans: continue
        # Tag only the longest span in this row
        longest = max(row_spans, key=lambda s: s.get("length",0))
        bl_num[longest["id"]] = i + 1

    # ── Draw all pipe sections ───────────────────────────────────────────────
    for sec in pipe_sections:
        pt_type = sec.get("pipe_type","branch")
        fx, fy  = sec["from"]["x"], sec["from"]["y"]
        tx, ty  = sec["to"]["x"],   sec["to"]["y"]
        fz      = sec.get("elevation_ft", ch - 0.5)
        tz      = sec.get("elevation_ft", ch - 0.5)

        p1 = pt(fx, fy, fz)
        p2 = pt(tx, ty, tz)

        # Line weight and color by pipe type
        if pt_type == "main":
            lw, col = 50, colors.RED
        elif pt_type == "cross":
            lw, col = 35, colors.RED
        elif pt_type == "branch":
            lw, col = 18, colors.RED
        elif pt_type == "armover":
            lw, col = 13, colors.RED
        else:
            lw, col = 13, 8

        msp.add_line(p1, p2, dxfattribs={"layer":"FP-PIPE-MAIN" if pt_type in("main","cross")
                                          else "FP-PIPE-BRNCH",
                                          "color":col,"lineweight":lw})

        seg_len = sec.get("length", 0) or math.hypot(tx-fx, ty-fy)
        if seg_len < 0.1: continue

        dia     = sec.get("diameter","")
        dia_str = _fmt_dia(dia) if dia else ""
        len_str = _fmt_len(seg_len)
        ang     = _pipe_ang(p1, p2)
        rot     = ang if -90 < ang <= 90 else ang + 180

        # Only label sections longer than wall stubs (> 1.5ft) to avoid clutter
        label_this = seg_len > 1.5

        if label_this:
            # Pipe SIZE label: above the pipe, larger text, farther offset
            if dia_str:
                sx, sy = _perp_offset(p1, p2, +18)
                T(dia_str, sx, sy, ht=8, rot=rot, color=colors.WHITE)

            # Pipe LENGTH label: below the pipe (blue, matching AutoSprink)
            lx, ly = _perp_offset(p1, p2, -18)
            T(len_str, lx, ly, ht=7, rot=rot, color=colors.BLUE)

        # Pipe ELEVATION label on main spans only (one per main, not every span)
        elev_ft = sec.get("elevation_ft")
        if elev_ft and pt_type == "main" and seg_len > 8:
            mx, my = (p1[0]+p2[0])/2, (p1[1]+p2[1])/2
            ft_e = int(elev_ft); in_e = int(round((elev_ft-ft_e)*12))
            elev_str = f"★ {ft_e}'-{in_e} FF↑" if in_e else f"★ {ft_e}'-0 FF↑"
            T(elev_str, mx+4, my+28, ht=6.5, rot=0, color=colors.CYAN)

        # Branch line number tag: show ONCE on the longest span per branch
        # (longest span = the middle head-to-head runs, not the wall stubs)
        if pt_type == "branch" and sec["id"] in bl_num and seg_len > 8:
            bln = bl_num[sec["id"]]
            mx, my = (p1[0]+p2[0])/2, (p1[1]+p2[1])/2
            _draw_tag(msp, mx, my+32, f"BL{bln}", ht=6.5, pad=3,
                      bg_color=colors.CYAN, txt_color=0)

        # Grooved coupling marks — only on sections long enough to warrant them
        # (pipes come in 21' lengths; show coupling only when section > 21')
        if float(dia or 0) >= 2.0 and seg_len > 21.5:
            # One coupling mark per full stick length
            n_couplings = int(seg_len / 21)
            for ci in range(1, n_couplings+1):
                fr  = ci * 21 / seg_len
                if fr >= 1.0: break
                gx  = p1[0] + (p2[0]-p1[0])*fr
                gy  = p1[1] + (p2[1]-p1[1])*fr
                msp.add_blockref("ISO_GRVD", (gx,gy),
                                 dxfattribs={"layer":"FP-VALV","rotation":rot})

        # End-of-line mark at branch endpoints
        if pt_type == "branch":
            msp.add_blockref("ISO_EOL", p2,
                             dxfattribs={"layer":"FP-HNGR"})

    # ── Sprinkler heads with drop nipples ────────────────────────────────────
    for sp in sprinklers:
        if sp.get("in_rack"): continue
        wx, wy  = sp["x"], sp["y"]
        ceil_z  = sp.get("elevation", ch)
        head_z  = ceil_z - 0.5   # head hangs 6" below ceiling

        # Drop nipple (vertical line from branch to head)
        pc = pt(wx, wy, ceil_z)
        ph = pt(wx, wy, head_z)
        msp.add_line(pc, ph, dxfattribs={"layer":"FP-PIPE-ARMOV","color":0,"lineweight":9})

        # Head symbol
        msp.add_blockref("ISO_SPKR", ph, dxfattribs={"layer":"FP-SPKR-PEND"})

    # ── Hanger markers: small tick marks only (numbers stay in legend) ──────
    # AutoSprink convention: individual hangers shown as tick marks on drawing,
    # designation numbers only in the legend table — keeps drawing readable.
    for h in hangers[:150]:  # cap at 150 to prevent visual overload
        wx, wy = h["x"], h["y"]
        elev   = ch - 0.5
        pp     = pt(wx, wy, elev)
        msp.add_blockref("ISO_HANG", pp, dxfattribs={"layer":"FP-HNGR"})
        # NO number label on individual hangers — number lives in legend only

    # ── Sway brace tags ──────────────────────────────────────────────────────
    for sb in sway_braces:
        wx, wy = sb["x"], sb["y"]
        elev   = ch - 0.5
        pp     = pt(wx, wy, elev)
        # Tag type depends on pipe type
        btype  = "LAT4" if sb.get("direction","4-way")=="lateral" else "LAT4"
        _draw_tag(msp, pp[0], pp[1]+16, btype, ht=6, pad=3,
                  bg_color=colors.CYAN, txt_color=0)
        msp.add_blockref("ISO_BRACE", pp, dxfattribs={"layer":"FP-HNGR"})

    # ── Valve symbols (simplified rectangles in isometric) ───────────────────
    for v in valves:
        wx, wy = v["x"], v["y"]
        pp     = pt(wx, wy, ch - 0.5)
        vtype  = v.get("type","osy")
        # Draw a small diamond for valves in isometric
        s = 8
        pts_v = [(pp[0],pp[1]+s),(pp[0]+s,pp[1]),
                 (pp[0],pp[1]-s),(pp[0]-s,pp[1]),(pp[0],pp[1]+s)]
        msp.add_lwpolyline(pts_v, dxfattribs={"layer":"FP-VALV",
                                               "color":colors.GREEN,"lineweight":18})
        # Label
        vlbl = {"osy":"OS&Y","butterfly":"BFV","check":"CV",
                "alarm":"WFS","inspector_test":"IT","drain":"DR"}.get(vtype,"V")
        T(vlbl, pp[0]+10, pp[1], ht=6, color=colors.GREEN)

    # ── Riser entry (vertical pipe at entry point) ───────────────────────────
    rx  = min(s["from"]["x"] for s in pipe_sections) - 4
    ry  = (min(s["from"]["y"] for s in pipe_sections) +
           max(s["from"]["y"] for s in pipe_sections)) / 2
    p_bot = pt(rx, ry, 0)
    p_top = pt(rx, ry, ch)
    msp.add_line(p_bot, p_top, dxfattribs={"layer":"FP-RISR","color":8,"lineweight":50})
    # Riser label
    riser_dia = project.get("riser_diameter","4")
    T(f'{riser_dia}" SCH 10 RISER', p_top[0]+8, p_top[1]+8, ht=7, color=colors.WHITE, bold=True)
    T(f'{ch:.0f}\'-0" A.F.F.', p_top[0]+8, p_top[1]-4, ht=6, color=colors.CYAN)

