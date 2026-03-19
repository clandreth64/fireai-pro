"""
FireAI Pro — Construction Drawing Engine
=========================================
Drop at repo root alongside your other .py engines.
Generates fully compliant fire protection construction drawings in DXF
meeting NFPA 13, NICET, and AHJ submittal standards.

Sheets:
  FP0.0  Cover sheet & sheet index
  FP1.x  Floor plan(s) — sprinkler layout, pipe runs, valves, equipment
  FP2.0  Riser diagram
  FP3.0  Hydraulic calculations
  FP4.0  Sprinkler & pipe schedules
  FP5.0  Installation details
  FP5.1  Section cuts & elevations
  FP6.0  Bill of materials

Requires:  pip install ezdxf reportlab matplotlib openpyxl
"""

import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import ezdxf
from ezdxf import colors
from ezdxf.enums import TextEntityAlignment
from ezdxf.document import Drawing


# ─── Constants ────────────────────────────────────────────────────────────────

SCALE_FACTOR = 96        # 1/8" = 1'-0"  (96 DXF units = 1 ft)
SHEET_W      = 3456      # 36" ANSI D
SHEET_H      = 2592      # 27" ANSI D
MARGIN       = 72        # 0.75"
TB_HEIGHT    = 216       # titleblock 2.25"
BORDER_X     = MARGIN
BORDER_Y     = MARGIN + TB_HEIGHT
DRAW_W       = SHEET_W - 2 * MARGIN
DRAW_H       = SHEET_H - MARGIN - TB_HEIGHT - MARGIN
FONT         = "ROMANS"
FONT_BOLD    = "ROMAND"
TEXT_SM      = 9
TEXT_MD      = 12
TEXT_LG      = 18
TEXT_XL      = 24

SHEET_BUILDER_MAP = {
    "sheet_fp00": ("FP0.0 — Cover",         "_build_cover",      "FP0_0_Cover.dxf"),
    "sheet_fp10": ("FP1.1 — Floor Plan",    "_build_floor_plan", "FP1_1_Floor_Plan.dxf"),
    "sheet_fp20": ("FP2.0 — Riser Diagram", "_build_riser",      "FP2_0_Riser_Diagram.dxf"),
    "sheet_fp30": ("FP3.0 — Hydraulics",    "_build_hydraulics", "FP3_0_Hydraulics.dxf"),
    "sheet_fp40": ("FP4.0 — Schedules",     "_build_schedules",  "FP4_0_Schedules.dxf"),
    "sheet_fp50": ("FP5.0 — Details",       "_build_details",    "FP5_0_Details.dxf"),
    "sheet_fp51": ("FP5.1 — Sections",      "_build_sections",   "FP5_1_Sections.dxf"),
    "sheet_fp60": ("FP6.0 — BOM",           "_build_bom_sheet",  "FP6_0_BOM.dxf"),
}

LAYER_DEFS = {
    "A-WALL-FULL":  {"color": colors.GRAY,   "desc": "Full-height walls"},
    "A-WALL-PART":  {"color": colors.GRAY,   "desc": "Partial-height walls"},
    "A-COLS":       {"color": colors.GRAY,   "desc": "Structural columns"},
    "A-BEAM":       {"color": colors.GRAY,   "desc": "Beams"},
    "A-SLAB":       {"color": colors.GRAY,   "desc": "Slab edges"},
    "A-CEIL":       {"color": 8,             "desc": "Ceiling boundary"},
    "A-ROOF":       {"color": 8,             "desc": "Roof outline"},
    "A-ROOM":       {"color": 253,           "desc": "Room boundaries"},
    "A-ROOM-IDEN":  {"color": 253,           "desc": "Room labels"},
    "A-DOOR":       {"color": colors.GRAY,   "desc": "Door swings"},
    "A-GLAZ":       {"color": colors.CYAN,   "desc": "Glazing"},
    "FP-PIPE-MAIN": {"color": colors.RED,    "desc": "Main pipe runs"},
    "FP-PIPE-XMAIN":{"color": 20,            "desc": "Cross mains"},
    "FP-PIPE-BRNCH":{"color": colors.YELLOW, "desc": "Branch lines"},
    "FP-PIPE-ARMOV":{"color": colors.YELLOW, "desc": "Armovers"},
    "FP-PIPE-DRAIN":{"color": colors.CYAN,   "desc": "Drain/test lines"},
    "FP-SPKR-UPRT": {"color": colors.BLUE,   "desc": "Upright sprinklers"},
    "FP-SPKR-PEND": {"color": colors.BLUE,   "desc": "Pendant sprinklers"},
    "FP-SPKR-SIDE": {"color": colors.BLUE,   "desc": "Sidewall sprinklers"},
    "FP-SPKR-CONC": {"color": colors.BLUE,   "desc": "Concealed sprinklers"},
    "FP-SPKR-ESFR": {"color": 30,            "desc": "ESFR sprinklers"},
    "FP-SPKR-CMSA": {"color": 30,            "desc": "CMSA sprinklers"},
    "FP-SPKR-COVR": {"color": 251,           "desc": "Coverage circles"},
    "FP-VALV":      {"color": colors.GREEN,  "desc": "All valves"},
    "FP-EQUP":      {"color": colors.GREEN,  "desc": "Equipment"},
    "FP-RISR":      {"color": colors.RED,    "desc": "Riser"},
    "FP-FDC":       {"color": 30,            "desc": "FDC"},
    "FP-HNGR":      {"color": 251,           "desc": "Hangers & bracing"},
    "FP-ANNO-DIMS": {"color": colors.WHITE,  "desc": "Dimensions"},
    "FP-ANNO-LABL": {"color": colors.WHITE,  "desc": "Tags"},
    "FP-ANNO-SYMB": {"color": colors.WHITE,  "desc": "Symbols"},
    "FP-ANNO-NOTE": {"color": colors.WHITE,  "desc": "Notes"},
    "FP-ANNO-REVS": {"color": colors.RED,    "desc": "Revisions"},
    "FP-TBLK":      {"color": colors.WHITE,  "desc": "Titleblock border"},
    "FP-TBLK-TEXT": {"color": colors.WHITE,  "desc": "Titleblock text"},
    "FP-VWPT":      {"color": 250,           "desc": "Viewports"},
    "FP-GRID":      {"color": 251,           "desc": "Grid"},
}


@dataclass
class SheetMeta:
    sheet_title:  str
    sheet_number: str
    scale:        str
    issue_date:   str
    revisions:    list = field(default_factory=list)


# ─── DXF document factory ─────────────────────────────────────────────────────

class DXFDocFactory:
    @staticmethod
    def new_doc() -> Drawing:
        doc = ezdxf.new("R2018", setup=True)
        for name, props in LAYER_DEFS.items():
            doc.layers.add(name, color=props["color"], linetype="CONTINUOUS")
        try:
            doc.styles.add("ROMANS", font="romans.shx")
            doc.styles.add("ROMAND", font="romand.shx")
        except Exception:
            pass
        try:
            ds = doc.dimstyles.new("FP_DIM")
            ds.dxf.dimscale = 96
            ds.dxf.dimasz   = 8
            ds.dxf.dimtxt   = 8
        except Exception:
            pass
        return doc


# ─── Titleblock renderer ──────────────────────────────────────────────────────

class TitleblockRenderer:
    def __init__(self, doc: Drawing, project: dict):
        self.doc = doc
        self.p   = project

    def _t(self, layout, text, x, y, h=TEXT_SM, layer="FP-TBLK-TEXT",
           bold=False, align=TextEntityAlignment.LEFT):
        layout.add_text(
            str(text or ""),
            dxfattribs={"layer": layer, "height": h,
                        "style": FONT_BOLD if bold else FONT}
        ).set_placement((x, y), align=align)

    def _line(self, layout, x1, y1, x2, y2, lw=25):
        layout.add_line((x1,y1),(x2,y2),
            dxfattribs={"layer":"FP-TBLK","lineweight":lw})

    def render(self, layout, meta: SheetMeta):
        bx=MARGIN; by=MARGIN; bw=DRAW_W; bh=TB_HEIGHT
        p=self.p
        layout.add_lwpolyline(
            [(bx,by),(bx+bw,by),(bx+bw,by+bh),(bx,by+bh),(bx,by)],
            dxfattribs={"layer":"FP-TBLK","lineweight":50})

        c1w=int(bw*0.18); cx=bx+c1w/2
        self._line(layout, bx+c1w,by, bx+c1w,by+bh)
        self._t(layout, p.get("company_name","FireAI Pro"), cx,by+bh-22, TEXT_LG, bold=True, align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, p.get("company_address",""),        cx,by+bh-40, TEXT_SM, align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, p.get("company_phone",""),          cx,by+bh-54, TEXT_SM, align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, p.get("company_email",""),          cx,by+bh-68, TEXT_SM, align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, "FIRE PROTECTION",                  cx,by+bh-88, TEXT_MD, bold=True, align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, "NFPA 13 — Current Edition",        cx,by+bh-104,TEXT_SM, align=TextEntityAlignment.MIDDLE_CENTER)
        self._line(layout, bx,by+80,bx+c1w,by+80)
        self._t(layout, "DESIGNED BY:", bx+6,by+72,TEXT_SM,bold=True)
        d=p.get("designer",{})
        self._t(layout, d.get("name","") if isinstance(d,dict) else str(d), bx+6,by+58,TEXT_SM)
        self._t(layout, d.get("cert","") if isinstance(d,dict) else "",     bx+6,by+44,TEXT_SM)
        self._line(layout, bx,by+40,bx+c1w,by+40)
        self._t(layout, "CHECKED BY:",  bx+6,by+32,TEXT_SM,bold=True)
        self._t(layout, p.get("checker_name",""), bx+6,by+18,TEXT_SM)

        c2w=int(bw*0.40); c2x=bx+c1w; px=c2x+10
        self._line(layout, c2x+c2w,by, c2x+c2w,by+bh)
        self._t(layout,"PROJECT:",                            px,by+bh-22, TEXT_SM,bold=True)
        self._t(layout,p.get("project_name",""),             px,by+bh-36, TEXT_MD,bold=True)
        self._line(layout,c2x,by+bh-44,c2x+c2w,by+bh-44)
        self._t(layout,"ADDRESS:",                            px,by+bh-56, TEXT_SM,bold=True)
        self._t(layout,p.get("location",""),                  px,by+bh-70, TEXT_SM)
        self._line(layout,c2x,by+bh-78,c2x+c2w,by+bh-78)
        self._t(layout,"PROJECT NO. (INTERNAL):",             px,by+bh-90, TEXT_SM,bold=True)
        self._t(layout,p.get("project_number_internal",""),   px,by+bh-104,TEXT_SM)
        self._t(layout,"PROJECT NO. (CUSTOMER):",             px+c2w//2,by+bh-90, TEXT_SM,bold=True)
        self._t(layout,p.get("project_number_customer",""),   px+c2w//2,by+bh-104,TEXT_SM)
        self._line(layout,c2x,by+bh-112,c2x+c2w,by+bh-112)
        self._t(layout,"OCCUPANCY:",                          px,by+bh-124,TEXT_SM,bold=True)
        self._t(layout,p.get("occupancy",""),                 px,by+bh-138,TEXT_SM)
        self._t(layout,"SYSTEM TYPE:",                        px+c2w//2,by+bh-124,TEXT_SM,bold=True)
        self._t(layout,p.get("system_type","WET").upper(),    px+c2w//2,by+bh-138,TEXT_SM)
        self._line(layout,c2x,by+bh-146,c2x+c2w,by+bh-146)
        self._t(layout,"AHJ:",                                px,by+bh-158,TEXT_SM,bold=True)
        self._t(layout,p.get("ahj_jurisdiction",""),          px,by+bh-172,TEXT_SM)

        c3x=c2x+c2w; c4x=bx+bw; sx=c3x+10; cw=c4x-c3x
        self._t(layout,"SHEET TITLE:", sx,by+bh-22,TEXT_SM,bold=True)
        self._t(layout,meta.sheet_title, sx,by+bh-38,TEXT_MD,bold=True)
        self._line(layout,c3x,by+bh-46,c4x,by+bh-46)
        self._t(layout,"SHEET NO.:", sx,by+bh-58,TEXT_SM,bold=True)
        self._t(layout,meta.sheet_number, sx,by+bh-76,TEXT_XL,bold=True)
        self._line(layout,c3x,by+bh-86,c4x,by+bh-86)
        self._t(layout,"DISCIPLINE:", sx,by+bh-98,TEXT_SM,bold=True)
        self._t(layout,"Fire Protection", sx,by+bh-112,TEXT_SM)
        self._t(layout,"SCALE:", sx+cw//2,by+bh-98,TEXT_SM,bold=True)
        self._t(layout,meta.scale, sx+cw//2,by+bh-112,TEXT_SM)
        self._line(layout,c3x,by+bh-120,c4x,by+bh-120)
        self._t(layout,"ISSUE DATE:", sx,by+bh-132,TEXT_SM,bold=True)
        self._t(layout,meta.issue_date, sx,by+bh-146,TEXT_SM)
        self._t(layout,"REVISION:", sx+cw//2,by+bh-132,TEXT_SM,bold=True)
        self._t(layout,str(len(meta.revisions)), sx+cw//2,by+bh-146,TEXT_SM)
        self._line(layout,c3x,by+bh-154,c4x,by+bh-154)
        self._t(layout,"REV",  sx,   by+bh-166,TEXT_SM,bold=True)
        self._t(layout,"DATE", sx+28,by+bh-166,TEXT_SM,bold=True)
        self._t(layout,"DESCRIPTION",sx+84,by+bh-166,TEXT_SM,bold=True)
        self._line(layout,c3x,by+bh-172,c4x,by+bh-172)
        ry=by+bh-184
        for rev in (meta.revisions or []):
            r = rev if isinstance(rev,dict) else rev.__dict__
            self._t(layout,r.get("number",""), sx,   ry,TEXT_SM)
            self._t(layout,r.get("date",""),   sx+28,ry,TEXT_SM)
            self._t(layout,r.get("description",""),sx+84,ry,TEXT_SM)
            ry-=14
            if ry<by+10: break


# ─── Symbol library ───────────────────────────────────────────────────────────

class SymbolLibrary:
    R = 6

    @classmethod
    def define_all(cls, doc):
        for bname, fn in [
            ("SPKR_UPRT",cls._upright),("SPKR_PEND",cls._pendant),
            ("SPKR_SIDE",cls._sidewall),("SPKR_CONC",cls._concealed),
            ("SPKR_ESFR",cls._esfr),("SPKR_CMSA",cls._cmsa),
            ("VALV_OSY",cls._osy),("VALV_BFV",cls._bfv),
            ("VALV_CV",cls._cv),("VALV_AV",cls._av),
            ("VALV_IT",cls._it),("VALV_DR",cls._dr),
            ("FP_RISER",cls._riser),("FP_FDC",cls._fdc),
            ("NORTH_ARROW",cls._north),
        ]:
            if bname not in doc.blocks:
                fn(doc, bname)

    @classmethod
    def _b(cls,doc,name): return doc.blocks.new(name)

    @classmethod
    def _upright(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-SPKR-UPRT"})
        b.add_line((-R,0),(R,0),dxfattribs={"layer":"FP-SPKR-UPRT"})
        b.add_line((0,-R),(0,R),dxfattribs={"layer":"FP-SPKR-UPRT"})
        b.add_text("U",dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _pendant(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-SPKR-PEND"})
        b.add_line((-R,0),(R,0),dxfattribs={"layer":"FP-SPKR-PEND"})
        b.add_text("P",dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _sidewall(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_arc((0,0),R,-90,90,dxfattribs={"layer":"FP-SPKR-SIDE"})
        b.add_line((0,-R),(0,R),dxfattribs={"layer":"FP-SPKR-SIDE"})
        b.add_text("SW",dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _concealed(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-SPKR-CONC"})
        h=b.add_hatch(color=colors.BLUE,dxfattribs={"layer":"FP-SPKR-CONC"})
        h.paths.add_edge_path().add_arc((0,0),R,0,360)
        b.add_text("C",dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _esfr(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-SPKR-ESFR"})
        b.add_circle((0,0),R*0.55,dxfattribs={"layer":"FP-SPKR-ESFR"})
        b.add_text("E",dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _cmsa(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_lwpolyline([(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)],dxfattribs={"layer":"FP-SPKR-CMSA"})
        b.add_text("M",dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _osy(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-VALV"})
        b.add_solid([(-R,0),(R,0),(0,R*1.5)],dxfattribs={"layer":"FP-VALV"})
        b.add_text("OS&Y",dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _bfv(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-VALV"})
        b.add_line((-R,0),(R,0),dxfattribs={"layer":"FP-VALV"})
        b.add_text("BFV",dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _cv(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-VALV"})
        b.add_line((0,-R),(0,R),dxfattribs={"layer":"FP-VALV"})
        b.add_solid([(0,0),(R,R//2),(R,-R//2)],dxfattribs={"layer":"FP-VALV"})
        b.add_text("CV",dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _av(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-VALV"})
        b.add_text("AV",dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _it(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_lwpolyline([(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)],dxfattribs={"layer":"FP-VALV"})
        b.add_text("IT",dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _dr(cls,doc,name):
        b=cls._b(doc,name); R=cls.R
        b.add_circle((0,0),R,dxfattribs={"layer":"FP-VALV"})
        b.add_line((-R,-R),(R,R),dxfattribs={"layer":"FP-VALV"})
        b.add_text("DR",dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _riser(cls,doc,name):
        b=cls._b(doc,name)
        b.add_circle((0,0),14,dxfattribs={"layer":"FP-RISR","lineweight":50})
        b.add_circle((0,0),10,dxfattribs={"layer":"FP-RISR"})
        b.add_text("RISER",dxfattribs={"layer":"FP-ANNO-LABL","height":6,"style":FONT_BOLD}
                   ).set_placement((0,-3),align=TextEntityAlignment.MIDDLE_CENTER)

    @classmethod
    def _fdc(cls,doc,name):
        b=cls._b(doc,name); R=10
        b.add_lwpolyline([(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)],dxfattribs={"layer":"FP-FDC","lineweight":50})
        b.add_text("FDC",dxfattribs={"layer":"FP-ANNO-LABL","height":7,"style":FONT_BOLD}
                   ).set_placement((0,-4),align=TextEntityAlignment.MIDDLE_CENTER)

    @classmethod
    def _north(cls,doc,name):
        b=cls._b(doc,name)
        b.add_line((0,0),(0,60),dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":35})
        b.add_solid([(0,60),(-8,40),(8,40)],dxfattribs={"layer":"FP-ANNO-SYMB"})
        b.add_circle((0,0),24,dxfattribs={"layer":"FP-ANNO-SYMB"})
        b.add_text("N",dxfattribs={"layer":"FP-ANNO-LABL","height":16,"style":FONT_BOLD}
                   ).set_placement((0,66),align=TextEntityAlignment.BOTTOM_CENTER)


# ─── Plan view renderer ───────────────────────────────────────────────────────

class PlanViewRenderer:
    def __init__(self, msp, project):
        self.msp=msp; self.project=project

    def _ft(self,v): return v*SCALE_FACTOR
    def _pt(self,x,y,ox=0,oy=0):
        return (BORDER_X+ox+self._ft(x), BORDER_Y+oy+self._ft(y))

    def draw_walls(self,walls,ox=0,oy=0):
        for w in walls:
            pts=[(BORDER_X+ox+self._ft(p["x"]),BORDER_Y+oy+self._ft(p["y"])) for p in w["points"]]
            self.msp.add_lwpolyline(pts,close=w.get("closed",False),
                dxfattribs={"layer":"A-WALL-PART" if w.get("partial") else "A-WALL-FULL",
                             "lineweight":35 if w.get("exterior") else 18})

    def draw_columns(self,cols,ox=0,oy=0):
        for c in cols:
            cx,cy=self._pt(c["x"],c["y"],ox,oy)
            w=self._ft(c.get("width",1.5)); d=self._ft(c.get("depth",1.5))
            pts=[(cx-w/2,cy-d/2),(cx+w/2,cy-d/2),(cx+w/2,cy+d/2),(cx-w/2,cy+d/2),(cx-w/2,cy-d/2)]
            self.msp.add_lwpolyline(pts,close=True,dxfattribs={"layer":"A-COLS","lineweight":50})

    def draw_rooms(self,rooms,ox=0,oy=0):
        for r in rooms:
            pts=[(BORDER_X+ox+self._ft(p["x"]),BORDER_Y+oy+self._ft(p["y"])) for p in r["boundary"]]
            self.msp.add_lwpolyline(pts,close=True,dxfattribs={"layer":"A-ROOM","lineweight":5})
            cx=sum(p[0] for p in pts)/len(pts); cy=sum(p[1] for p in pts)/len(pts)
            self.msp.add_text(r.get("name",""),dxfattribs={"layer":"A-ROOM-IDEN","height":TEXT_SM,"style":FONT}
            ).set_placement((cx,cy+TEXT_SM/2),align=TextEntityAlignment.MIDDLE_CENTER)
            self.msp.add_text(r.get("area",""),dxfattribs={"layer":"A-ROOM-IDEN","height":TEXT_SM*0.8,"style":FONT}
            ).set_placement((cx,cy-TEXT_SM*0.8),align=TextEntityAlignment.MIDDLE_CENTER)

    def draw_pipes(self,pipes,ox=0,oy=0):
        for s in pipes:
            fx,fy=self._pt(s["from"]["x"],s["from"]["y"],ox,oy)
            tx,ty=self._pt(s["to"]["x"],s["to"]["y"],ox,oy)
            pt=s.get("pipe_type","branch")
            layer={"main":"FP-PIPE-MAIN","cross":"FP-PIPE-XMAIN","branch":"FP-PIPE-BRNCH",
                   "armover":"FP-PIPE-ARMOV","drain":"FP-PIPE-DRAIN"}.get(pt,"FP-PIPE-BRNCH")
            lw=50 if pt=="main" else (35 if pt in("cross","xmain") else 18)
            self.msp.add_line((fx,fy),(tx,ty),dxfattribs={"layer":layer,"lineweight":lw})
            mx,my=(fx+tx)/2,(fy+ty)/2
            lbl=f'{s.get("diameter","")}\" {s.get("schedule","")} {s.get("material","")}'.strip()
            ang=math.degrees(math.atan2(ty-fy,tx-fx))
            self.msp.add_text(lbl,dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM*0.8,
                "style":FONT,"rotation":ang if abs(ang)<45 else 90}
            ).set_placement((mx,my+TEXT_SM),align=TextEntityAlignment.BOTTOM_CENTER)

    def draw_sprinklers(self,spkrs,ox=0,oy=0,show_coverage=True):
        bmap={"upright":"SPKR_UPRT","pendant":"SPKR_PEND","sidewall":"SPKR_SIDE",
              "concealed":"SPKR_CONC","esfr":"SPKR_ESFR","cmsa":"SPKR_CMSA"}
        for s in spkrs:
            px,py=self._pt(s["x"],s["y"],ox,oy)
            st=s.get("type","pendant").lower()
            self.msp.add_blockref(bmap.get(st,"SPKR_PEND"),(px,py),dxfattribs={"layer":"FP-SPKR-PEND"})
            if show_coverage and s.get("coverage_radius"):
                self.msp.add_circle((px,py),self._ft(s["coverage_radius"]),
                    dxfattribs={"layer":"FP-SPKR-COVR","linetype":"DASHED"})
            parts=[s.get("id","")]
            if s.get("k_factor"):    parts.append(f'K{s["k_factor"]}')
            if s.get("temp_rating"): parts.append(f'{s["temp_rating"]}°F')
            if s.get("hazard"):      parts.append(s["hazard"])
            self.msp.add_text(" / ".join(str(t) for t in parts if t),
                dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM*0.75,"style":FONT}
            ).set_placement((px+SymbolLibrary.R+4,py+SymbolLibrary.R+2))

    def draw_valves(self,valves,ox=0,oy=0):
        bmap={"osy":"VALV_OSY","butterfly":"VALV_BFV","check":"VALV_CV",
              "alarm":"VALV_AV","inspector_test":"VALV_IT","drain":"VALV_DR"}
        for v in valves:
            px,py=self._pt(v["x"],v["y"],ox,oy)
            self.msp.add_blockref(bmap.get(v.get("type","osy").lower(),"VALV_OSY"),
                (px,py),dxfattribs={"layer":"FP-VALV"})
            self.msp.add_text(v.get("id",""),dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM*0.75,"style":FONT}
            ).set_placement((px+SymbolLibrary.R+4,py+2))

    def draw_equipment(self,equip,ox=0,oy=0):
        bmap={"riser":"FP_RISER","fdc":"FP_FDC"}
        for e in equip:
            px,py=self._pt(e["x"],e["y"],ox,oy)
            self.msp.add_blockref(bmap.get(e.get("type","riser").lower(),"FP_RISER"),
                (px,py),dxfattribs={"layer":"FP-EQUP"})
            self.msp.add_text(e.get("label",""),
                dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT_BOLD}
            ).set_placement((px,py-20),align=TextEntityAlignment.TOP_CENTER)

    def draw_north_arrow(self,rot=0):
        nx=BORDER_X+DRAW_W-120; ny=BORDER_Y+DRAW_H-120
        self.msp.add_blockref("NORTH_ARROW",(nx,ny),dxfattribs={"layer":"FP-ANNO-SYMB","rotation":rot})
        self.msp.add_text(f"PROJ NORTH = {rot}° FROM TRUE",
            dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM*0.8,"style":FONT}
        ).set_placement((nx,ny-44),align=TextEntityAlignment.TOP_CENTER)

    def draw_scale_bar(self,scale_str):
        sx=BORDER_X+60; sy=BORDER_Y+30; bl=SCALE_FACTOR*10
        self.msp.add_line((sx,sy),(sx+bl,sy),dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":25})
        for i in range(11):
            tx=sx+i*SCALE_FACTOR; th=8 if i%5==0 else 4
            self.msp.add_line((tx,sy-th),(tx,sy+th),dxfattribs={"layer":"FP-ANNO-SYMB"})
            if i%5==0:
                self.msp.add_text(f"{i}'",dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM*0.8,"style":FONT}
                ).set_placement((tx,sy-14),align=TextEntityAlignment.TOP_CENTER)
        self.msp.add_text(f"SCALE: {scale_str}",dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((sx,sy+14))

    def draw_general_notes(self,extra=None):
        notes=["ALL WORK SHALL CONFORM TO NFPA 13, CURRENT EDITION.",
               "ALL PIPE SHALL BE SCHEDULE 40 STEEL UNLESS NOTED OTHERWISE.",
               "ALL HANGERS AND SWAY BRACES SHALL BE FM/UL LISTED.",
               "CONTRACTOR SHALL FIELD VERIFY ALL DIMENSIONS PRIOR TO FABRICATION.",
               "PROVIDE INSPECTOR'S TEST CONNECTION PER NFPA 13 §8.17.",
               "HYDRAULIC DESIGN INFORMATION SIGN REQUIRED PER NFPA 13 §27.2.",
               "CONTRACTOR SHALL COORDINATE WITH MEP AND STRUCTURAL TRADES.",
               "ALL PENETRATIONS THROUGH FIRE-RATED ASSEMBLIES SHALL BE FIRE STOPPED.",
               "DO NOT SCALE DRAWINGS — USE DIMENSIONS ONLY."]+(extra or [])
        nx=BORDER_X+DRAW_W-340; ny=BORDER_Y+40
        self.msp.add_text("GENERAL NOTES",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((nx,ny+len(notes)*14+20))
        for i,n in enumerate(notes):
            self.msp.add_text(f"{i+1}. {n}",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
            ).set_placement((nx,ny+(len(notes)-i)*14))

    def draw_legend(self):
        lx=BORDER_X+DRAW_W-340; ly=BORDER_Y+DRAW_H-160
        self.msp.add_text("SYMBOL LEGEND",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((lx,ly))
        entries=[("SPKR_PEND","Pendant sprinkler"),("SPKR_UPRT","Upright sprinkler"),
                 ("SPKR_SIDE","Sidewall sprinkler"),("SPKR_CONC","Concealed sprinkler"),
                 ("SPKR_ESFR","ESFR sprinkler"),("VALV_OSY","OS&Y gate valve"),
                 ("VALV_BFV","Butterfly valve"),("VALV_CV","Check valve"),
                 ("VALV_IT","Inspector's test"),("FP_RISER","Riser assembly"),("FP_FDC","FDC")]
        for i,(bn,lbl) in enumerate(entries):
            ey=ly-22-i*20
            self.msp.add_blockref(bn,(lx+10,ey),dxfattribs={"layer":"FP-ANNO-SYMB"})
            self.msp.add_text(lbl,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
            ).set_placement((lx+26,ey-4))


# ─── Schedule renderer ────────────────────────────────────────────────────────

class ScheduleRenderer:
    def __init__(self,msp): self.msp=msp

    def draw_sprinkler_schedule(self,spkrs,origin=(BORDER_X+20,BORDER_Y+DRAW_H-40)):
        hdrs=["TAG","TYPE","K-FACTOR","TEMP","COVERAGE","HAZARD","QTY"]
        cw=[40,70,55,55,70,80,30]; ox,oy=origin; total=sum(cw); rh=16
        self.msp.add_text("SPRINKLER HEAD SCHEDULE",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}).set_placement((ox,oy))
        oy-=rh+6; cx=ox
        for i,h in enumerate(hdrs):
            self.msp.add_text(h,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((cx+2,oy-10)); cx+=cw[i]
        self.msp.add_line((ox,oy),(ox+total,oy),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})
        tc=Counter(); ti={}
        for s in spkrs: t=s.get("type","pendant"); tc[t]+=1; ti[t]=s
        ry=oy-rh
        for st,qty in sorted(tc.items()):
            ry-=rh; inf=ti[st]
            cells=[st.upper()[:2],st.capitalize(),str(inf.get("k_factor","5.6")),
                   str(inf.get("temp_rating","155"))+"°F",str(inf.get("coverage_radius",""))+"r",
                   inf.get("hazard","Light"),str(qty)]
            cx=ox
            for i,c in enumerate(cells):
                self.msp.add_text(c,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}).set_placement((cx+2,ry+4)); cx+=cw[i]
            self.msp.add_line((ox,ry),(ox+total,ry),dxfattribs={"layer":"FP-ANNO-NOTE"})

    def draw_pipe_schedule(self,pipes,origin=(BORDER_X+20,BORDER_Y+220)):
        hdrs=["TAG","TYPE","DIA (in)","SCHEDULE","MATERIAL","LENGTH (ft)","FITTINGS"]
        cw=[40,60,50,60,60,65,115]; ox,oy=origin; total=sum(cw); rh=16
        self.msp.add_text("PIPE SCHEDULE",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}).set_placement((ox,oy))
        oy-=rh+6; cx=ox
        for i,h in enumerate(hdrs):
            self.msp.add_text(h,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((cx+2,oy-10)); cx+=cw[i]
        self.msp.add_line((ox,oy),(ox+total,oy),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})
        ry=oy-rh
        for p in pipes[:30]:
            ry-=rh
            cells=[p.get("id",""),p.get("pipe_type","branch").capitalize(),str(p.get("diameter","")),
                   p.get("schedule","Sch 40"),p.get("material","Steel"),f'{p.get("length",0):.1f}',
                   ", ".join(p.get("fittings",[])[:4])]
            cx=ox
            for i,c in enumerate(cells):
                self.msp.add_text(c,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}).set_placement((cx+2,ry+4)); cx+=cw[i]
            self.msp.add_line((ox,ry),(ox+total,ry),dxfattribs={"layer":"FP-ANNO-NOTE"})


# ─── Main engine ──────────────────────────────────────────────────────────────

class FireAIDrawingEngine:
    def __init__(self,project,cad_output,hydraulics_output,
                 bracing_output,compliance_result,extra_notes=None):
        self.project    = project
        self.cad        = cad_output        or {}
        self.hydraulics = hydraulics_output or {}
        self.bracing    = bracing_output    or {}
        self.compliance = compliance_result
        self.notes      = extra_notes or []
        self.revisions  = project.get("revisions",[])
        self.issue_date = project.get("issue_date",datetime.utcnow().strftime("%m/%d/%Y"))

    def _meta(self,title,code,scale="1/8\" = 1'-0\""):
        return SheetMeta(sheet_title=title,sheet_number=code,scale=scale,
                         issue_date=self.issue_date,revisions=self.revisions)

    def _new_sheet(self,title,code,scale="1/8\" = 1'-0\""):
        doc=DXFDocFactory.new_doc(); SymbolLibrary.define_all(doc)
        msp=doc.modelspace(); meta=self._meta(title,code,scale)
        msp.add_lwpolyline([(MARGIN,MARGIN),(SHEET_W-MARGIN,MARGIN),
            (SHEET_W-MARGIN,SHEET_H-MARGIN),(MARGIN,SHEET_H-MARGIN),(MARGIN,MARGIN)],
            dxfattribs={"layer":"FP-TBLK","lineweight":50})
        TitleblockRenderer(doc,self.project).render(msp,meta)
        return doc,msp,meta

    # ── FP0.0 Cover ───────────────────────────────────────────────────────────
    def _build_cover(self):
        doc,msp,meta=self._new_sheet("Cover Sheet","FP0.0",scale="N/A")
        cx=SHEET_W/2; cy=BORDER_Y+DRAW_H/2
        for txt,y,sz,b in [("FIRE PROTECTION SYSTEM",cy+200,TEXT_XL*2,True),
            ("AUTOMATIC SPRINKLER DESIGN",cy+140,TEXT_XL,True),
            (self.project.get("project_name",""),cy+80,TEXT_LG,False),
            (self.project.get("location",""),cy+40,TEXT_MD,False)]:
            msp.add_text(str(txt),dxfattribs={"layer":"FP-ANNO-NOTE","height":sz,"style":FONT_BOLD if b else FONT}
            ).set_placement((cx,y),align=TextEntityAlignment.MIDDLE_CENTER)
        ix=BORDER_X+40; iy=BORDER_Y+DRAW_H-60
        msp.add_text("SHEET INDEX",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}).set_placement((ix,iy))
        for i,(num,t) in enumerate([("FP0.0","Cover Sheet"),("FP1.x","Floor Plan(s)"),
            ("FP2.0","Riser Diagram"),("FP3.0","Hydraulic Calculations"),
            ("FP4.0","Schedules"),("FP5.0","Installation Details"),("FP6.0","Bill of Materials")]):
            msp.add_text(f"{num}    {t}",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
            ).set_placement((ix,iy-20-i*18))
        cx2=cx+100
        msp.add_text("CODE COMPLIANCE",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}).set_placement((cx2,iy))
        p=self.project
        for i,c in enumerate([f"NFPA 13 — Current Edition",f"IBC {p.get('ibc_year','2021')}",
            f"AHJ: {p.get('ahj_jurisdiction','')}",f"Occupancy: {p.get('occupancy','')}",
            f"System: {p.get('system_type','Wet').upper()}",f"Seismic: {p.get('seismic_zone','')}",
            f"Pipe: {p.get('pipe_material','Steel')}","Design: Density/Area §22"]):
            msp.add_text(c,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}).set_placement((cx2,iy-20-i*18))
        return doc

    # ── FP1.x Floor plan ──────────────────────────────────────────────────────
    def _build_floor_plan(self,floor_num=1):
        doc,msp,meta=self._new_sheet(f"Floor Plan — Level {floor_num}",f"FP1.{floor_num}")
        r=PlanViewRenderer(msp,self.project)
        if self.cad.get("walls"):   r.draw_walls(self.cad["walls"])
        if self.cad.get("columns"): r.draw_columns(self.cad["columns"])
        if self.cad.get("rooms"):   r.draw_rooms(self.cad["rooms"])
        r.draw_pipes(self.cad.get("pipe_sections",[]))
        r.draw_sprinklers(self.cad.get("sprinkler_placements",[]),show_coverage=True)
        r.draw_valves(self.cad.get("valves",[]))
        r.draw_equipment(self.cad.get("equipment",[]))
        r.draw_north_arrow(self.project.get("north_rotation",0))
        r.draw_scale_bar(meta.scale)
        r.draw_general_notes(self.notes)
        r.draw_legend()
        return doc

    # ── FP2.0 Riser diagram ───────────────────────────────────────────────────
    def _build_riser(self):
        doc,msp,meta=self._new_sheet("Riser Diagram","FP2.0",scale="N.T.S.")
        cx=BORDER_X+DRAW_W//2
        msp.add_line((cx,BORDER_Y+40),(cx,BORDER_Y+DRAW_H-40),dxfattribs={"layer":"FP-PIPE-MAIN","lineweight":50})
        p=self.project
        for frac,lbl,bn in [(0.85,"OS&Y GATE VALVE","VALV_OSY"),(0.72,"ALARM CHECK VALVE","VALV_CV"),
            (0.60,"FLOW SWITCH",None),(0.48,"INSPECTOR'S TEST","VALV_IT"),
            (0.36,"2\" MAIN DRAIN","VALV_DR"),(0.20,"FIRE DEPT. CONNECTION","FP_FDC")]:
            y=BORDER_Y+int(DRAW_H*frac)
            if bn: msp.add_blockref(bn,(cx,y),dxfattribs={"layer":"FP-VALV"})
            msp.add_line((cx,y),(cx+200,y),dxfattribs={"layer":"FP-ANNO-NOTE"})
            msp.add_text(lbl,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((cx+208,y-4))
        msp.add_text(f"WATER SUPPLY: {p.get('static_pressure',0)} PSI STATIC  {p.get('water_supply_flow',0)} GPM",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}).set_placement((BORDER_X+40,BORDER_Y+DRAW_H-40))
        msp.add_text("RISER DIAGRAM — NOT TO SCALE",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_LG,"style":FONT_BOLD}
        ).set_placement((cx,BORDER_Y+DRAW_H-60),align=TextEntityAlignment.BOTTOM_CENTER)
        return doc

    # ── FP3.0 Hydraulic calculations ─────────────────────────────────────────
    def _build_hydraulics(self):
        doc,msp,meta=self._new_sheet("Hydraulic Calculations","FP3.0",scale="N/A")
        h=self.hydraulics; ox=BORDER_X+40; oy=BORDER_Y+DRAW_H-40
        msp.add_text("HYDRAULIC CALCULATION SUMMARY",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_LG,"style":FONT_BOLD}).set_placement((ox,oy))
        rows=[("Design method","Density/Area — NFPA 13 §22"),
              ("Static pressure",f'{h.get("static_pressure","—")} psi'),
              ("Residual pressure",f'{h.get("residual_pressure","—")} psi'),
              ("Required pressure",f'{h.get("required_pressure","—")} psi'),
              ("Pressure delta",f'{h.get("pressure_delta","—")} psi'),
              ("Flow demand",f'{h.get("flow_demand","—")} gpm'),
              ("Design density",f'{h.get("density_area",{}).get("density","—")} gpm/ft²'),
              ("Design area",f'{h.get("density_area",{}).get("area","—")} ft²'),
              ("System type",self.project.get("system_type","Wet").upper()),
              ("Pipe material",self.project.get("pipe_material","Steel")),
              ("Seismic zone",self.project.get("seismic_zone","—")),
              ("NFPA 13 compliant","YES" if getattr(self.compliance,"compliant",False) else "PENDING"),]
        for i,(lbl,val) in enumerate(rows):
            y=oy-30-i*22
            msp.add_text(f"{lbl}:",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((ox,y))
            msp.add_text(str(val),dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}).set_placement((ox+260,y))
        curve=h.get("demand_curve",[])
        if curve:
            px0=ox+500; py0=oy-420; pw=600; ph=300
            msp.add_lwpolyline([(px0,py0),(px0+pw,py0),(px0+pw,py0+ph),(px0,py0+ph),(px0,py0)],dxfattribs={"layer":"FP-ANNO-NOTE"})
            msp.add_text("DEMAND CURVE",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
            ).set_placement((px0+pw//2,py0+ph+10),align=TextEntityAlignment.BOTTOM_CENTER)
            mf=max(pt.get("flow",1) for pt in curve) or 1
            mp=max(pt.get("pressure",1) for pt in curve) or 1
            pts=[(px0+pt.get("flow",0)/mf*pw,py0+pt.get("pressure",0)/mp*ph) for pt in curve]
            if len(pts)>=2: msp.add_lwpolyline(pts,dxfattribs={"layer":"FP-PIPE-MAIN","lineweight":25})
        return doc

    # ── FP4.0 Schedules ───────────────────────────────────────────────────────
    def _build_schedules(self):
        doc,msp,meta=self._new_sheet("Sprinkler & Pipe Schedules","FP4.0",scale="N/A")
        s=ScheduleRenderer(msp)
        s.draw_sprinkler_schedule(self.cad.get("sprinkler_placements",[]))
        s.draw_pipe_schedule(self.cad.get("pipe_sections",[]))
        return doc

    # ── FP5.0 Details ─────────────────────────────────────────────────────────
    def _build_details(self):
        doc,msp,meta=self._new_sheet("Installation Details","FP5.0",scale="VARIES")
        details=[("HANGER DETAIL — STANDARD","Per NFPA 13 §9.1. Rod hanger with listed bracket. Max 15ft spacing on branch lines."),
            ("SWAY BRACE DETAIL","Per NFPA 13 §9.3. 4-way brace at riser. Max 40ft longitudinal / 25ft lateral spacing."),
            ("MAIN DRAIN ASSEMBLY","2\" main drain to floor drain or exterior. Ball valve + sight glass. Test quarterly."),
            ("INSPECTOR'S TEST CONNECTION","1\" orifice equiv. to smallest sprinkler K-factor. Sight glass at most remote location."),
            ("RISER ASSEMBLY","OS&Y valve + alarm check valve + flow switch + pressure gauge + main drain."),
            ("ARMOVER DETAIL","Max 12\" armover from branch line centerline. No pipe size reduction at armover."),
            ("PIPE PENETRATION — RATED ASSEMBLY","UL-listed through-penetration firestop. Submit firestop submittal to AHJ."),
            ("CONCEALED SPACE PROTECTION","Per NFPA 13 §8.15. Upright or listed concealed-space sprinklers where required."),]
        c1x=BORDER_X+40; c2x=BORDER_X+DRAW_W//2+40; base_y=BORDER_Y+DRAW_H-40
        for i,(t,d) in enumerate(details):
            cx=c1x if i%2==0 else c2x; ry=base_y-(i//2)*90
            msp.add_text(t,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}).set_placement((cx,ry))
            msp.add_text(d,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}).set_placement((cx,ry-TEXT_MD-4))
            msp.add_line((cx,ry-TEXT_MD-22),(cx+DRAW_W//2-80,ry-TEXT_MD-22),dxfattribs={"layer":"FP-ANNO-NOTE"})
        return doc

    # ── FP5.1 Sections ────────────────────────────────────────────────────────
    def _build_sections(self):
        doc,msp,meta=self._new_sheet("Section Cuts & Elevations","FP5.1",scale="1/4\" = 1'-0\"")
        ch=self.project.get("ceiling_height",14)
        cx=BORDER_X+DRAW_W//2; fy=BORDER_Y+100; cy=fy+int(ch*SCALE_FACTOR*0.5)
        msp.add_line((BORDER_X+100,fy),(BORDER_X+DRAW_W-100,fy),dxfattribs={"layer":"A-SLAB","lineweight":50})
        msp.add_text("FLOOR SLAB",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}).set_placement((BORDER_X+104,fy+4))
        msp.add_line((BORDER_X+100,cy),(BORDER_X+DRAW_W-100,cy),dxfattribs={"layer":"A-CEIL","lineweight":25})
        msp.add_text(f"CEILING — {ch}'-0\" AFF",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}).set_placement((BORDER_X+104,cy+4))
        msp.add_line((cx-200,cy),(cx+200,cy),dxfattribs={"layer":"FP-PIPE-MAIN","lineweight":35})
        msp.add_text("2\" BRANCH LINE",dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT}).set_placement((cx+204,cy-4))
        drop=int(SCALE_FACTOR*0.5)
        msp.add_line((cx,cy),(cx,cy-drop),dxfattribs={"layer":"FP-PIPE-BRNCH"})
        msp.add_blockref("SPKR_PEND",(cx,cy-drop),dxfattribs={"layer":"FP-SPKR-PEND"})
        msp.add_text("PENDANT SPRINKLER",dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT}).set_placement((cx+12,cy-drop-4))
        dim_x=BORDER_X+DRAW_W-200
        msp.add_line((dim_x,fy),(dim_x,cy),dxfattribs={"layer":"FP-ANNO-DIMS"})
        msp.add_text(f"{ch}'-0\"",dxfattribs={"layer":"FP-ANNO-DIMS","height":TEXT_SM,"style":FONT}).set_placement((dim_x+8,(fy+cy)//2))
        msp.add_text("SECTION A-A — TYPICAL PENDANT INSTALLATION",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((cx,BORDER_Y+DRAW_H-40),align=TextEntityAlignment.BOTTOM_CENTER)
        return doc

    # ── FP6.0 BOM ─────────────────────────────────────────────────────────────
    def _build_bom_sheet(self):
        doc,msp,meta=self._new_sheet("Bill of Materials","FP6.0",scale="N/A")
        bom=self.bracing.get("bom",[]) if self.bracing else []
        if not bom:
            spkrs=self.cad.get("sprinkler_placements",[]); pipes=self.cad.get("pipe_sections",[])
            bom=[]
            if spkrs:
                tc=Counter(s.get("type","pendant") for s in spkrs)
                for st,qty in tc.items(): bom.append({"item":f"{st.upper()} SPRINKLER HEAD","part_number":"TBD","qty":qty,"unit":"EA","unit_cost":8.50})
            if pipes:
                pl: dict=defaultdict(float)
                for p in pipes:
                    k=f'{p.get("diameter","")} SCH {p.get("schedule","40")} {p.get("material","STEEL")}'
                    pl[k]+=p.get("length",0)
                for desc,length in pl.items(): bom.append({"item":f"PIPE — {desc}","part_number":"TBD","qty":round(length,1),"unit":"LF","unit_cost":4.20})
            bom+=[{"item":"OS&Y GATE VALVE 4\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":285.00},
                  {"item":"ALARM CHECK VALVE 4\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":420.00},
                  {"item":"FLOW SWITCH","part_number":"TBD","qty":1,"unit":"EA","unit_cost":95.00},
                  {"item":"INSPECTOR'S TEST CONN 1\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":45.00},
                  {"item":"MAIN DRAIN ASSEMBLY 2\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":120.00},
                  {"item":"FDC — 4\"x2.5\"x2.5\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":380.00},
                  {"item":"PIPE HANGER — STD ROD","part_number":"TBD","qty":0,"unit":"EA","unit_cost":12.50},
                  {"item":"SWAY BRACE — 4-WAY","part_number":"TBD","qty":0,"unit":"EA","unit_cost":185.00}]
        hdrs=["#","DESCRIPTION","PART NO.","QTY","UNIT","UNIT COST","TOTAL"]
        cw=[24,280,100,40,36,70,80]; ox=BORDER_X+20; oy=BORDER_Y+DRAW_H-40; rh=18; total=sum(cw)
        msp.add_text("BILL OF MATERIALS",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_LG,"style":FONT_BOLD}).set_placement((ox,oy))
        oy-=rh+8; cx=ox
        for i,h in enumerate(hdrs):
            msp.add_text(h,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((cx+2,oy-10)); cx+=cw[i]
        msp.add_line((ox,oy),(ox+total,oy),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":35})
        grand=0.0; ry=oy-rh
        for idx,item in enumerate(bom):
            ry-=rh; qty=item.get("qty",0); uc=item.get("unit_cost",0); tot=qty*uc; grand+=tot
            cells=[str(idx+1),item.get("item",""),item.get("part_number","TBD"),str(qty),item.get("unit","EA"),f"${uc:,.2f}",f"${tot:,.2f}"]
            cx=ox
            for i,c in enumerate(cells):
                msp.add_text(c,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}).set_placement((cx+2,ry+4)); cx+=cw[i]
            msp.add_line((ox,ry),(ox+total,ry),dxfattribs={"layer":"FP-ANNO-NOTE"})
        ry-=rh
        msp.add_text("ESTIMATED MATERIAL TOTAL (LABOR NOT INCLUDED):",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((ox,ry+4))
        msp.add_text(f"${grand:,.2f}",dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((ox+total,ry+4),align=TextEntityAlignment.RIGHT)
        return doc

    # ── PDF export ────────────────────────────────────────────────────────────
    def _export_dxf_to_pdf(self,dxf_path,pdf_path,sheet_title):
        try:
            import ezdxf
            from ezdxf.addons.drawing import RenderContext, Frontend
            from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_pdf import PdfPages
            doc=ezdxf.readfile(dxf_path); msp=doc.modelspace()
            fig=plt.figure(figsize=(34,22)); ax=fig.add_axes([0,0,1,1]); ax.set_aspect("equal")
            Frontend(RenderContext(doc),MatplotlibBackend(ax)).draw_layout(msp)
            with PdfPages(pdf_path) as pdf: pdf.savefig(fig,dpi=150)
            plt.close(fig)
        except ImportError:
            from reportlab.pdfgen import canvas as rlc
            from reportlab.lib.units import inch
            c=rlc.Canvas(pdf_path,pagesize=(34*inch,22*inch))
            c.setFont("Helvetica-Bold",24); c.drawCentredString(17*inch,11*inch,sheet_title)
            c.setFont("Helvetica",14); c.drawCentredString(17*inch,10*inch,"Install matplotlib for full rendering: pip install matplotlib")
            c.save()
        except Exception as e:
            print(f"[DrawingEngine] PDF warning for {sheet_title}: {e}")

    # ── generate_all() ────────────────────────────────────────────────────────
    def generate_all(self,output_dir="./outputs/drawings"):
        Path(output_dir).mkdir(parents=True,exist_ok=True)
        manifest=[]
        sheets=[("FP0.0 — Cover",self._build_cover,"FP0_0_Cover.dxf"),
                ("FP1.1 — Floor Plan",lambda:self._build_floor_plan(1),"FP1_1_Floor_Plan.dxf"),
                ("FP2.0 — Riser Diagram",self._build_riser,"FP2_0_Riser_Diagram.dxf"),
                ("FP3.0 — Hydraulics",self._build_hydraulics,"FP3_0_Hydraulics.dxf"),
                ("FP4.0 — Schedules",self._build_schedules,"FP4_0_Schedules.dxf"),
                ("FP5.0 — Details",self._build_details,"FP5_0_Details.dxf"),
                ("FP5.1 — Sections",self._build_sections,"FP5_1_Sections.dxf"),
                ("FP6.0 — BOM",self._build_bom_sheet,"FP6_0_BOM.dxf"),]
        for name,fn,filename in sheets:
            try:
                print(f"[DrawingEngine] Generating {name}...")
                doc=fn(); out=os.path.join(output_dir,filename); doc.saveas(out)
                size=os.path.getsize(out); print(f"[DrawingEngine] ✓ {name} — {size/1024:.1f} KB")
                manifest.append({"sheet":name,"filename":filename,"path":out,"size_kb":round(size/1024,1)})
            except Exception as e:
                print(f"[DrawingEngine] ✗ {name} failed: {e}")
                manifest.append({"sheet":name,"filename":filename,"path":None,"error":str(e)})
        done=len([m for m in manifest if not m.get("error")])
        print(f"\n[DrawingEngine] Complete — {done}/{len(manifest)} sheet(s) generated.")
        return manifest

    # ── generate_selected() ───────────────────────────────────────────────────
    def generate_selected(self,output_dir,selected_sheets,include_pdf=True,include_3d=False):
        Path(output_dir).mkdir(parents=True,exist_ok=True)
        manifest=[]
        for key,(name,builder_name,filename) in SHEET_BUILDER_MAP.items():
            if key not in selected_sheets: continue
            try:
                print(f"[DrawingEngine] Generating {name}...")
                builder=getattr(self,builder_name,None)
                if builder is None:
                    manifest.append({"sheet":name,"filename":filename,"path":None,"error":"builder not implemented"}); continue
                doc=builder(1) if builder_name=="_build_floor_plan" else builder()
                out=os.path.join(output_dir,filename); doc.saveas(out)
                size=os.path.getsize(out); print(f"[DrawingEngine] ✓ {name} — {size/1024:.1f} KB")
                manifest.append({"sheet":name,"filename":filename,"path":out,"size_kb":round(size/1024,1)})
                if include_pdf:
                    pf=filename.replace(".dxf",".pdf"); pp=os.path.join(output_dir,pf)
                    self._export_dxf_to_pdf(out,pp,name)
                    if os.path.exists(pp): manifest.append({"sheet":name+" (PDF)","filename":pf,"path":pp,"size_kb":round(os.path.getsize(pp)/1024,1)})
            except Exception as e:
                print(f"[DrawingEngine] ✗ {name} failed: {e}")
                manifest.append({"sheet":name,"filename":filename,"path":None,"error":str(e)})
        if include_3d:
            try:
                print("[DrawingEngine] Generating 3D DXF overlay...")
                doc3,msp3,_=self._new_sheet("3D Pipe Network","3D",scale="N.T.S.")
                cz=self.project.get("ceiling_height",14)*12
                for s in self.cad.get("pipe_sections",[]):
                    fx=BORDER_X+s["from"]["x"]*SCALE_FACTOR; fy=BORDER_Y+s["from"]["y"]*SCALE_FACTOR
                    tx=BORDER_X+s["to"]["x"]*SCALE_FACTOR;   ty=BORDER_Y+s["to"]["y"]*SCALE_FACTOR
                    msp3.add_line((fx,fy,cz),(tx,ty,cz),dxfattribs={"layer":"FP-PIPE-MAIN"})
                p3=os.path.join(output_dir,"layout_3d.dxf"); doc3.saveas(p3)
                manifest.append({"sheet":"3D DXF","filename":"layout_3d.dxf","path":p3,"size_kb":round(os.path.getsize(p3)/1024,1)})
            except Exception as e:
                manifest.append({"sheet":"3D DXF","filename":"layout_3d.dxf","path":None,"error":str(e)})
        done=len([m for m in manifest if not m.get("error")])
        print(f"[DrawingEngine] Complete — {done}/{len(manifest)} file(s) generated.")
        return manifest
