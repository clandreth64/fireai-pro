"""
FireAI Pro — Hanger & Sway Brace Detail Drawings
===================================================
Generates FP5.0 Installation Details sheet containing:
  - Seismic sway brace assembly (Lateral LAT4, Longitudinal LNG4)
  - 4-way seismic brace
  - U-Hook through I-Joist (designation 19)
  - Trapeze #10 (designation 9)
  - TOLCO Fig. 78 end-of-line restraint (designation 2)
  - TOLCO Fig. 130 beam clamp (designation 12)
  - RED-I Joists detail
  - All Redbuilt open-web trusses detail

Matches Battalion One cover sheet detail panel quality.
"""
from __future__ import annotations
import math
import ezdxf
from ezdxf import colors
from ezdxf.enums import TextEntityAlignment

FONT      = "ROMANS"
FONT_BOLD = "ROMAND"
TXT_SM    = 7
TXT_MD    = 9
TXT_LG    = 12


# ─── Low-level helpers ────────────────────────────────────────────────────────

def _line(msp, x0,y0,x1,y1, layer="FP-ANNO-SYMB", lw=9, color=None):
    att = {"layer":layer,"lineweight":lw}
    if color: att["color"] = color
    msp.add_line((x0,y0),(x1,y1), dxfattribs=att)

def _rect(msp, x,y,w,h, layer="FP-ANNO-SYMB", lw=9, fill=False, fill_color=8):
    pts = [(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]
    msp.add_lwpolyline(pts, dxfattribs={"layer":layer,"lineweight":lw})
    if fill:
        msp.add_solid([(x,y),(x+w,y),(x+w,y+h),(x,y+h)],
                      dxfattribs={"layer":layer,"color":fill_color})

def _txt(msp, text, x, y, ht=TXT_SM, layer="FP-ANNO-NOTE", bold=False,
         rot=0, align=None, color=None):
    att = {"layer":layer,"height":ht,"style":FONT_BOLD if bold else FONT,"rotation":rot}
    if color: att["color"] = color
    t = msp.add_text(str(text), dxfattribs=att)
    if align:
        t.set_placement((x,y), align=align)
    else:
        t.set_placement((x,y))

def _leader(msp, x0,y0, x1,y1, text, sub="", layer="FP-ANNO-NOTE"):
    _line(msp,x0,y0,x1,y1,"FP-ANNO-DIMS",9)
    _txt(msp,"◄ "+text, x1+4, y1+3, TXT_SM, layer, bold=True)
    if sub:
        _txt(msp,"  "+sub, x1+4, y1-9, TXT_SM*0.85, layer)

def _callout(msp, num, x, y, r=8):
    """Circled designation number."""
    msp.add_circle((x,y),r,dxfattribs={"layer":"FP-HNGR","color":colors.CYAN,"lineweight":9})
    msp.add_text(str(num),dxfattribs={"layer":"FP-HNGR","height":TXT_SM*0.75,
        "style":FONT_BOLD,"color":colors.CYAN}
    ).set_placement((x,y-TXT_SM*0.35),align=TextEntityAlignment.MIDDLE_CENTER)

def _box_border(msp, x,y,w,h, title="", lw=25):
    """Draw a detail box with optional title bar."""
    pts = [(x,y),(x+w,y),(x+w,y-h),(x,y-h),(x,y)]
    msp.add_lwpolyline(pts,dxfattribs={"layer":"FP-TBLK","lineweight":lw})
    if title:
        _txt(msp, title, x+w//2, y-TXT_LG-4, TXT_LG, bold=True,
             align=TextEntityAlignment.MIDDLE_CENTER)
    return y - (TXT_LG+10 if title else 0)


# ─── Item 5: Seismic sway brace assemblies ────────────────────────────────────

def draw_sway_brace_lateral(msp, ox, oy, bw=560, bh=360,
                             pipe_size="1\" SCH. 40", main_size="4\"",
                             structural="open_web_truss"):
    """
    SEISMIC SWAY BRACE ASSEMBLY — LATERAL (LAT4)
    Diagonal brace pipe at 45° from structural attachment to sprinkler main.
    """
    # Box
    _box_border(msp,ox,oy,bw,bh)
    # Title at bottom
    _txt(msp,"SEISMIC SWAY BRACE ASSEMBLY",ox+bw//2,oy-bh+20,TXT_MD,bold=True,
         align=TextEntityAlignment.MIDDLE_CENTER,color=colors.GREEN)
    _txt(msp,"NTS.        LATERAL    ▸LAT4",ox+bw//2,oy-bh+8,TXT_SM*0.9,
         align=TextEntityAlignment.MIDDLE_CENTER)

    # Structural member at top (I-joist / truss bottom chord)
    struct_y = oy - 55
    _rect(msp,ox+40,struct_y,bw-80,14,lw=18,fill=True,fill_color=8)
    _txt(msp,"STRUCTURAL MEMBER",ox+bw//2,struct_y+18,TXT_SM*0.85,
         align=TextEntityAlignment.MIDDLE_CENTER,color=8)

    # Attach point on structural
    ax = ox + bw//2 - 40; ay = struct_y
    # TOLCO Fig. 980 fitting (small square)
    _rect(msp,ax-8,ay-8,16,8,"FP-VALV",lw=13)
    _txt(msp,"TOLCO FIG. 980",ax+20,ay-6,TXT_SM*0.8)

    # 3/8"x3" lag bolt annotation
    _txt(msp,'3/8" X 3" LAG BOLT',ox+50,struct_y+18,TXT_SM*0.8)
    _txt(msp,"TYP.",ox+50,struct_y+8,TXT_SM*0.8)

    # Diagonal brace pipe — 45° angle
    # Goes from structural attach down-right to sprinkler main
    main_y = oy - bh + 95
    main_x0 = ox+80; main_x1 = ox+bw-80

    # 4" sprinkler main (heavy horizontal pipe)
    _line(msp,main_x0,main_y,main_x1,main_y,"FP-PIPE-MAIN",35)
    _txt(msp,f'{main_size} SPRINKLER MAIN',main_x0,main_y-14,TXT_SM)
    _txt(msp,"TOLCO FIG. 1001",main_x0+80,main_y+10,TXT_SM*0.85)

    # Brace pipe: from attach point on structure to main at 45°
    # Calculate brace endpoint on main
    brace_dx = ay - main_y  # height differential
    brace_ex = ax + brace_dx   # end x = start x + height (45° means equal)
    brace_ex = min(brace_ex, main_x1-60)
    _line(msp,ax,ay,brace_ex,main_y,"FP-PIPE-XMAIN",18)
    # Brace pipe label (along the diagonal)
    mid_bx = (ax+brace_ex)//2; mid_by = (ay+main_y)//2
    ang = math.degrees(math.atan2(main_y-ay, brace_ex-ax))
    _txt(msp,f'1" SCH. 40 PIPE TO 7\'-0"',mid_bx-10,mid_by+8,TXT_SM*0.8,rot=ang+5)

    # RED-H @ 24" O.C. note
    _txt(msp,'RED-H @ 24" O.C.',ax-10,ay-45,TXT_SM*0.9,bold=True)

    # Angle bracket at top
    ang_y = struct_y - 4
    _txt(msp,'2½"x2½"x3/16" ANGLE STEEL',ox+55,ang_y-16,TXT_SM*0.8)
    _line(msp,ax-20,ang_y,ax+20,ang_y,"FP-ANNO-SYMB",13)
    _line(msp,ax,ang_y,ax,ang_y-20,"FP-ANNO-SYMB",13)

    # 1/2" machine bolt note
    _txt(msp,'1/2" MACHINE BOLT',ax+30,ang_y-10,TXT_SM*0.8)
    _txt(msp,"NUT & WASHER",ax+30,ang_y-20,TXT_SM*0.8)
    _txt(msp,"CENTERED BETWEEN TRUSSES",ax+30,ang_y-30,TXT_SM*0.8)


def draw_sway_brace_longitudinal(msp, ox, oy, bw=560, bh=360):
    """
    SEISMIC SWAY BRACE ASSEMBLY — LONGITUDINAL (LNG4)
    V-shaped brace from web member on both sides.
    """
    _box_border(msp,ox,oy,bw,bh)
    _txt(msp,"SEISMIC SWAY BRACE ASSEMBLY",ox+bw//2,oy-bh+20,TXT_MD,bold=True,
         align=TextEntityAlignment.MIDDLE_CENTER,color=colors.GREEN)
    _txt(msp,"NTS.    LONGITUDINAL    ▸LNG4",ox+bw//2,oy-bh+8,TXT_SM*0.9,
         align=TextEntityAlignment.MIDDLE_CENTER)

    # I-Joist at top
    struct_y = oy - 55
    _rect(msp,ox+80,struct_y,bw-160,14,lw=18,fill=True,fill_color=8)

    # Web member (vertical line in center of joist)
    web_x = ox+bw//2
    _line(msp,web_x,struct_y,web_x,struct_y-50,"FP-ANNO-SYMB",13)
    _txt(msp,"WEB MEMBER",web_x+10,struct_y-30,TXT_SM*0.85)

    # Two TOLCO Fig. 980 fittings on web
    for sx, lbl in [(web_x-10,"TOLCO FIG. 980"),(web_x+10,"")]:
        _rect(msp,sx-5,struct_y-10,10,10,"FP-VALV",13)
    _txt(msp,"TOLCO FIG. 980",web_x+16,struct_y-8,TXT_SM*0.8)

    # Two brace pipes forming a V
    main_y = oy - bh + 95
    main_x0 = ox+80; main_x1 = ox+bw-80
    _line(msp,main_x0,main_y,main_x1,main_y,"FP-PIPE-MAIN",35)
    _txt(msp,'4" SPRINKLER MAIN',main_x0,main_y-14,TXT_SM)

    for ex, label in [(main_x0+100,""), (main_x1-100,"")]:
        _line(msp,web_x,struct_y,ex,main_y,"FP-PIPE-XMAIN",18)

    mid_bx1 = (web_x+main_x0+100)//2
    _txt(msp,'1" SCH. 40 PIPE TO 7\'-0"',mid_bx1-20,oy-bh+180,TXT_SM*0.8,rot=35)

    # TOLCO Fig 4L at base
    _txt(msp,"TOLCO FIG. 4L",main_x0+85,main_y+12,TXT_SM*0.85)

    # RED-H label
    _txt(msp,'RED-H @ 24" O.C.',web_x-40,struct_y-45,TXT_SM*0.9,bold=True)
    _txt(msp,"ORIENTATION:B",ox+bw-80,oy-bh+8,TXT_SM*0.8)

    # 1/2" machine bolt
    _txt(msp,'1/2" MACHINE BOLT',web_x+16,struct_y+20,TXT_SM*0.8)
    _txt(msp,"NUT & WASHER CENTERED BETWEEN TRUSSES",web_x+16,struct_y+10,TXT_SM*0.8)
    _txt(msp,'2½"x2½"x3/16" ANGLE STEEL',ox+85,struct_y+20,TXT_SM*0.8)


# ─── Item 5: Hanger detail drawings ──────────────────────────────────────────

def draw_uhook_joist(msp, ox, oy, bw=380, bh=320):
    """
    U-HOOK THROUGH JOIST (designation 19)
    Shows inverted U-hook through the web of an I-joist.
    """
    _box_border(msp,ox,oy,bw,bh)
    _txt(msp,"U-HOOK THROUGH JOIST",ox+bw//2,oy-20,TXT_LG,bold=True,
         align=TextEntityAlignment.MIDDLE_CENTER,color=colors.GREEN)

    # I-Joist cross-section (isometric-ish view)
    jy = oy - 80
    # Top flange
    _rect(msp,ox+60,jy,260,10,lw=18,fill=True,fill_color=9)
    # Web
    _line(msp,ox+180,jy,ox+180,jy-120,"FP-ANNO-SYMB",18)
    # Bottom flange
    _rect(msp,ox+60,jy-120,260,10,lw=18,fill=True,fill_color=9)

    # Hole in web for U-hook
    _line(msp,ox+172,jy-55,ox+188,jy-55,"FP-ANNO-SYMB",9)
    _line(msp,ox+172,jy-65,ox+188,jy-65,"FP-ANNO-SYMB",9)

    # U-hook (inverted U shape)
    hk_x = ox+180; hk_y = jy-60
    _line(msp,hk_x-12,hk_y,hk_x-12,hk_y-80,"FP-ANNO-SYMB",13)
    _line(msp,hk_x+12,hk_y,hk_x+12,hk_y-80,"FP-ANNO-SYMB",13)
    msp.add_arc((hk_x,hk_y),12,0,180,dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":13})

    # Pipe
    pipe_y = jy-150
    _rect(msp,ox+80,pipe_y,240,16,"FP-PIPE-BRNCH",18)

    # Callout numbers
    callouts = [
        (1, ox+110, jy-25,   "2x6 x 18\" LONG MINIMUM"),
        (2, ox+100, pipe_y+8,"INVERTED U-HOOK, SIZE AND SHAPE PER NFPA 13"),
        (3, ox+180, jy-40,   "HOLE CUT NEATLY IN WEB PER HOLE CHART"),
        (4, ox+220, pipe_y+8,"TWO 1/2\" DIAMETER MACHINE BOLTS W/ WASHERS"),
    ]
    for num, cx2, cy2, desc in callouts:
        _callout(msp,num,ox+30+num*20,oy-bh+20+num*18)
        _txt(msp,f"{num}. {desc}",ox+50+num*20+18,oy-bh+18+num*18,TXT_SM*0.8)

    # Hanger designation circle
    _callout(msp,19,ox+16,oy-bh+12,r=9)
    _txt(msp,'Pipe size at maximum hanger spacing: 4" with block on one side; 6" with blocks on both sides.',
         ox+32,oy-bh+12,TXT_SM*0.8)

    _txt(msp,"18\" I-JOIST",ox+90,jy+14,TXT_SM*0.85,bold=True)


def draw_trapeze_10(msp, ox, oy, bw=380, bh=280):
    """
    TRAPEZE #10 (designation 9)
    Angle iron cross-piece on open-web truss with hanger rod support.
    """
    _box_border(msp,ox,oy,bw,bh)
    _txt(msp,"TRAPEZE #10",ox+bw//2,oy-20,TXT_LG,bold=True,
         align=TextEntityAlignment.MIDDLE_CENTER,color=colors.GREEN)

    # Open-web truss outline
    ty = oy - 60
    # Top chord
    _line(msp,ox+40,ty,ox+bw-40,ty,"FP-ANNO-SYMB",18)
    # Bottom chord
    _line(msp,ox+40,ty-60,ox+bw-40,ty-60,"FP-ANNO-SYMB",18)
    # Web diagonals
    for i in range(4):
        x0 = ox+40+i*70
        _line(msp,x0,ty,x0+35,ty-60,"FP-ANNO-SYMB",9)
        _line(msp,x0+35,ty,x0+70,ty-60,"FP-ANNO-SYMB",9)

    # Angle iron cross piece (trapeze)
    az = ty - 60
    _rect(msp,ox+80,az-8,bw-160,8,"FP-PIPE-XMAIN",18)
    _txt(msp,"ANGLE IRON TRAPEZE PIPE",ox+bw//2,az-20,TXT_SM,
         align=TextEntityAlignment.MIDDLE_CENTER)

    # Hanger rod drop from chord to pipe
    rod_x = ox+bw//2
    _line(msp,rod_x,ty-60,rod_x,az+16,"FP-ANNO-SYMB",13)
    _txt(msp,"3/8\" X 3\" LAG SCREW",ox+40,ty-40,TXT_SM*0.85)
    _txt(msp,"ON OUTSIDE CHORDS",ox+40,ty-52,TXT_SM*0.85)

    # Pipe below trapeze
    pipe_y = az - 50
    _rect(msp,ox+80,pipe_y,bw-160,18,"FP-PIPE-BRNCH",18)
    _txt(msp,"SPRINKLER BRANCH LINE",ox+bw//2,pipe_y-14,TXT_SM,
         align=TextEntityAlignment.MIDDLE_CENTER)

    # Notes and designation
    _callout(msp,9,ox+16,oy-bh+20,r=9)
    notes = [
        "1. Angle iron, trapeze pipe, or other approved cross piece per NFPA 13",
        "2. On outside chords, 3/8\"x3\" lag screws into 3/16\" lead hole for 6\" max pipe",
        "3. Hanger rod support per NFPA 13",
    ]
    for i,n in enumerate(notes):
        _txt(msp,n,ox+32,oy-bh+18+i*13,TXT_SM*0.8)
    _txt(msp,'Pipe size at maximum hanger spacing: 2½" (may be increased to 6" with special truss design)',
         ox+32,oy-bh+12,TXT_SM*0.8)


def draw_beam_clamp(msp, ox, oy, bw=280, bh=200):
    """
    TOLCO FIG. 130 — BEAM CLAMP (designation 12)
    """
    _box_border(msp,ox,oy,bw,bh)
    _txt(msp,"TOLCO FIG. 130",ox+bw//2,oy-20,TXT_LG,bold=True,
         align=TextEntityAlignment.MIDDLE_CENTER)

    # Steel beam (W-section simplified)
    beam_y = oy-65
    _rect(msp,ox+40,beam_y,bw-80,10,lw=18,fill=True,fill_color=9)  # top flange
    _line(msp,ox+bw//2-4,beam_y,ox+bw//2-4,beam_y-50,"FP-ANNO-SYMB",18)  # web
    _line(msp,ox+bw//2+4,beam_y,ox+bw//2+4,beam_y-50,"FP-ANNO-SYMB",18)
    _rect(msp,ox+40,beam_y-60,bw-80,10,lw=18,fill=True,fill_color=9)  # bot flange

    # Beam clamp on bottom flange
    clamp_y = beam_y-60
    _rect(msp,ox+bw//2-20,clamp_y-20,40,20,"FP-VALV",18)
    _txt(msp,"BEAM CLAMP",ox+bw//2,clamp_y-28,TXT_SM,align=TextEntityAlignment.MIDDLE_CENTER)
    _txt(msp,"(EQUIV. TO TOLCO FIG. 130)",ox+bw//2,clamp_y-38,TXT_SM*0.8,
         align=TextEntityAlignment.MIDDLE_CENTER)

    # 3/8" eye rod
    _line(msp,ox+bw//2,clamp_y-40,ox+bw//2,clamp_y-80,"FP-ANNO-SYMB",13)
    _txt(msp,"3/8\" DIAMETER EYE ROD OR L-ROD",ox+bw//2+8,clamp_y-60,TXT_SM*0.8)

    _callout(msp,12,ox+16,oy-bh+20,r=9)
    _txt(msp,'Pipe size at maximum hanger spacing: 4"',ox+32,oy-bh+20,TXT_SM*0.8)


def draw_tolco_78_eol(msp, ox, oy, bw=280, bh=220):
    """
    TOLCO FIG. 78 — END OF LINE RESTRAINT (designation 2)
    """
    _box_border(msp,ox,oy,bw,bh)
    _txt(msp,"TOLCO FIG. 78",ox+bw//2,oy-20,TXT_LG,bold=True,
         align=TextEntityAlignment.MIDDLE_CENTER)
    _txt(msp,"END OF LINE RESTRAINT",ox+bw//2,oy-34,TXT_SM*0.9,
         align=TextEntityAlignment.MIDDLE_CENTER)

    # Branch line pipe (horizontal)
    py = oy-90
    _rect(msp,ox+40,py,bw-80,14,"FP-PIPE-BRNCH",18)

    # ATR restraint at 45° (shown as diagonal line + small fitting)
    fx = ox+bw-80; fy = py+7
    ang_r = math.radians(45)
    ex2 = fx-40*math.cos(ang_r); ey2 = fy+40*math.sin(ang_r)
    _line(msp,fx,fy,ex2,ey2,"FP-ANNO-SYMB",13)
    _txt(msp,"3/8\" ATR",ex2-30,ey2+4,TXT_SM*0.8)
    _txt(msp,"45° ANGLE FROM VERTICAL",ox+40,oy-bh+70,TXT_SM*0.8)

    # End of pipe
    msp.add_circle((ox+bw-40,py+7),8,dxfattribs={"layer":"FP-PIPE-BRNCH"})
    _txt(msp,"END SPRINKLER",ox+bw-80,py+24,TXT_SM*0.8)

    # Notes
    _callout(msp,2,ox+16,oy-bh+28,r=9)
    notes = [
        "49\" MAX. RESTRAINT SPACING FOR 1½\" PIPE",
        "53\" MAX. RESTRAINT SPACING FOR 2\" PIPE",
        "NFPA 13 TABLE 18.5.4(b)",
    ]
    for i,n in enumerate(notes):
        _txt(msp,n,ox+32,oy-bh+26-i*11,TXT_SM*0.8)


# ─── Master builder ───────────────────────────────────────────────────────────

# Sheet layout constants — must match fireai_drawing_engine.py
_BORDER_X = 72
_BORDER_Y = 288
_DRAW_W   = 3312
_DRAW_H   = 2232


def build_details_sheet(msp, project: dict, hangers: list, braces: list):
    """
    Lay out all detail drawings on the FP5.0 sheet.
    Organizes panels in a grid:
      Top row:    Seismic brace LATERAL | Seismic brace LONGITUDINAL
      Middle row: U-Hook through joist  | Trapeze #10
      Bottom row: TOLCO Fig. 78 EOL    | Beam clamp
    """
    BORDER_X = _BORDER_X; BORDER_Y = _BORDER_Y
    DRAW_W   = _DRAW_W;   DRAW_H   = _DRAW_H

    # Panel layout: 2 columns, 3 rows
    col_w = DRAW_W // 2 - 20
    row_h = DRAW_H // 3 - 20
    ox1 = BORDER_X + 10; ox2 = BORDER_X + col_w + 30
    oy1 = BORDER_Y + DRAW_H - 10
    oy2 = BORDER_Y + DRAW_H - row_h - 20
    oy3 = BORDER_Y + DRAW_H - row_h*2 - 30

    framing = project.get("structural_framing","").lower()

    # Row 1: Sway brace assemblies
    draw_sway_brace_lateral(msp, ox1, oy1, col_w-20, row_h-10,
                             structural=framing)
    draw_sway_brace_longitudinal(msp, ox2, oy1, col_w-20, row_h-10)

    # Row 2: Hanger details
    draw_uhook_joist(msp, ox1, oy2, col_w-20, row_h-10)
    draw_trapeze_10(msp, ox2, oy2, col_w-20, row_h-10)

    # Row 3: TOLCO details
    draw_tolco_78_eol(msp, ox1, oy3, (col_w-20)//2-10, row_h-10)
    draw_beam_clamp(msp, ox1+(col_w-20)//2+10, oy3, (col_w-20)//2-10, row_h-10)

    # Sheet title
    cx = BORDER_X + DRAW_W//2
    msp.add_text("INSTALLATION DETAILS",
        dxfattribs={"layer":"FP-ANNO-NOTE","height":14,"style":FONT_BOLD}
    ).set_placement((cx, BORDER_Y+30), align=TextEntityAlignment.MIDDLE_CENTER)
    msp.add_text("NTS — ALL DETAILS",
        dxfattribs={"layer":"FP-ANNO-NOTE","height":8,"style":FONT}
    ).set_placement((cx, BORDER_Y+16), align=TextEntityAlignment.MIDDLE_CENTER)
