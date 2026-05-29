"""
FireAI Pro — Construction Drawing Engine  v2
=============================================
Drop at repo root alongside other .py engines.

v2 fixes:
  - _build_riser: Complete rewrite — full valve assembly, floor connections,
    pipe sizes, water supply block, FDC detail, system data table
  - _export_dxf_to_pdf: Fixed axes limits from DXF extents, proper sheet size
  - _build_floor_plan: Coverage circles lighter, pipe labels readable
  - draw_pipes: Labels offset properly, always readable

Sheets:
  FP0.0 Cover sheet & sheet index
  FP1.x Floor plan(s) — sprinkler layout, pipe runs, valves, equipment
  FP2.0 Riser diagram
  FP3.0 Hydraulic calculations
  FP4.0 Sprinkler & pipe schedules
  FP5.0 Installation details
  FP5.1 Section cuts & elevations
  FP6.0 Bill of materials

Requires: pip install ezdxf reportlab matplotlib openpyxl
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

SCALE_FACTOR = 96       # 1/8" = 1'-0"  (96 DXF units = 1 ft)
SHEET_W      = 3456     # 36"  ANSI D
SHEET_H      = 2592     # 27"  ANSI D
MARGIN       = 72       # 0.75"
TB_HEIGHT    = 216      # title block 2.25"
BORDER_X     = MARGIN
BORDER_Y     = MARGIN + TB_HEIGHT
DRAW_W       = SHEET_W - 2 * MARGIN
DRAW_H       = SHEET_H - MARGIN - TB_HEIGHT - MARGIN

# Floor plan uses left 72% of drawing area; right 28% = details/tables panel
FP_PANEL_W   = int(DRAW_W * 0.72)
DTL_PANEL_X  = BORDER_X + FP_PANEL_W + 20
DTL_PANEL_W  = DRAW_W - FP_PANEL_W - 20

FONT      = "ROMANS"
FONT_BOLD = "ROMAND"
TEXT_SM   = 9
TEXT_MD   = 12
TEXT_LG   = 18
TEXT_XL   = 24

SHEET_BUILDER_MAP = {
    "sheet_fp00": ("FP0.0 — Cover",          "_build_cover",       "FP0_0_Cover.dxf"),
    "sheet_fp10": ("FP1.1 — Floor Plan",      "_build_floor_plan",  "FP1_1_Floor_Plan.dxf"),
    "sheet_fp20": ("FP2.0 — Riser Diagram",   "_build_riser",       "FP2_0_Riser_Diagram.dxf"),
    "sheet_fp30": ("FP3.0 — Hydraulics",      "_build_hydraulics",  "FP3_0_Hydraulics.dxf"),
    "sheet_fp40": ("FP4.0 — Schedules",       "_build_schedules",   "FP4_0_Schedules.dxf"),
    "sheet_fp50": ("FP5.0 — Details",         "_build_details",     "FP5_0_Details.dxf"),
    "sheet_fp51": ("FP5.1 — Sections",        "_build_sections",    "FP5_1_Sections.dxf"),
    "sheet_fp60": ("FP6.0 — BOM",             "_build_bom_sheet",   "FP6_0_BOM.dxf"),
    "sheet_fp70": ("FP7.0 — Isometric View",  "_build_isometric",   "FP7_0_Isometric.dxf"),
}

def _scale_annotation(scale_du_per_ft: float) -> str:
    """Convert DXF units/ft scale to human-readable annotation string."""
    STD = {96:'1in=1ft', 48:'1/2in=1ft', 32:'3/8in=1ft',
           24:'1/4in=1ft', 16:'3/16in=1ft', 12:'1/8in=1ft',
           8:'3/32in=1ft', 6:'1/16in=1ft', 4:'1/32in=1ft'}
    best = min(STD.keys(), key=lambda s: abs(s - scale_du_per_ft))
    return STD.get(best, f'{scale_du_per_ft:.0f}u/ft')

LAYER_DEFS = {
    "A-WALL-FULL":  {"color": 8,            "lineweight": 100, "desc": "Full-height walls"},
    "A-WALL-PART":  {"color": 8,            "desc": "Partial-height walls"},
    "A-COLS":       {"color": 8,            "desc": "Structural columns"},
    "A-BEAM":       {"color": 8,            "desc": "Beams"},
    "A-SLAB":       {"color": 8,            "desc": "Slab edges"},
    "A-CEIL":       {"color": 9,            "desc": "Ceiling boundary"},
    "A-ROOF":       {"color": 9,            "desc": "Roof outline"},
    "A-ROOM":       {"color": 9,            "desc": "Room boundaries"},
    "A-ROOM-IDEN":  {"color": 8,            "desc": "Room labels"},
    "A-DOOR":       {"color": colors.GRAY,  "desc": "Door swings"},
    "A-GLAZ":       {"color": colors.CYAN,  "desc": "Glazing"},
    "FP-PIPE-MAIN": {"color": colors.RED,   "desc": "Main pipe runs"},
    "FP-PIPE-XMAIN":{"color": 20,           "desc": "Cross mains"},
    "FP-PIPE-BRNCH":{"color": colors.YELLOW,"desc": "Branch lines"},
    "FP-PIPE-ARMOV":{"color": colors.YELLOW,"desc": "Armovers"},
    "FP-PIPE-DRAIN":{"color": colors.CYAN,  "desc": "Drain/test lines"},
    "FP-SPKR-UPRT": {"color": colors.BLUE,  "desc": "Upright sprinklers"},
    "FP-SPKR-PEND": {"color": colors.BLUE,  "desc": "Pendant sprinklers"},
    "FP-SPKR-SIDE": {"color": colors.BLUE,  "desc": "Sidewall sprinklers"},
    "FP-SPKR-CONC": {"color": colors.BLUE,  "desc": "Concealed sprinklers"},
    "FP-SPKR-ESFR": {"color": 30,           "desc": "ESFR sprinklers"},
    "FP-SPKR-CMSA": {"color": 30,           "desc": "CMSA sprinklers"},
    "FP-SPKR-COVR": {"color": 251,          "desc": "Coverage circles"},
    "FP-VALV":      {"color": colors.GREEN, "desc": "All valves"},
    "FP-EQUP":      {"color": colors.GREEN, "desc": "Equipment"},
    "FP-RISR":      {"color": colors.RED,   "desc": "Riser"},
    "FP-FDC":       {"color": 30,           "desc": "FDC"},
    "FP-HNGR":      {"color": colors.CYAN,  "desc": "Hangers & bracing"},
    "FP-ANNO-DIMS": {"color": colors.WHITE, "desc": "Dimensions"},
    "FP-ANNO-LABL": {"color": colors.WHITE, "desc": "Tags"},
    "FP-ANNO-SYMB": {"color": colors.WHITE, "desc": "Symbols"},
    "FP-ANNO-NOTE": {"color": colors.WHITE, "desc": "Notes"},
    "FP-ANNO-REVS": {"color": colors.RED,   "desc": "Revisions"},
    "FP-TBLK":      {"color": colors.WHITE, "desc": "Titleblock border"},
    "FP-TBLK-TEXT": {"color": colors.WHITE, "desc": "Titleblock text"},
    "FP-VWPT":      {"color": 250,          "desc": "Viewports"},
    "FP-GRID":      {"color": 9,            "desc": "Grid"},
}


@dataclass
class SheetMeta:
    sheet_title: str
    sheet_number: str
    scale: str
    issue_date: str
    revisions: list = field(default_factory=list)


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
            dxfattribs={"layer": layer, "height": h, "style": FONT_BOLD if bold else FONT}
        ).set_placement((x, y), align=align)

    def _line(self, layout, x1, y1, x2, y2, lw=25):
        layout.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "FP-TBLK", "lineweight": lw})

    def render(self, layout, meta: SheetMeta):
        bx = MARGIN; by = MARGIN; bw = DRAW_W; bh = TB_HEIGHT
        p  = self.p

        layout.add_lwpolyline(
            [(bx,by),(bx+bw,by),(bx+bw,by+bh),(bx,by+bh),(bx,by)],
            dxfattribs={"layer": "FP-TBLK", "lineweight": 50})

        c1w = int(bw * 0.18); cx = bx + c1w // 2
        self._line(layout, bx+c1w, by, bx+c1w, by+bh)
        self._t(layout, p.get("company_name","FireAI Pro"), cx, by+bh-22, TEXT_LG, bold=True)
        self._t(layout, p.get("company_address",""),        cx, by+bh-40, TEXT_SM)
        self._t(layout, p.get("company_phone",""),          cx, by+bh-54, TEXT_SM)
        self._t(layout, p.get("company_email",""),          cx, by+bh-68, TEXT_SM)
        self._t(layout, "FIRE PROTECTION",                  cx, by+bh-88, TEXT_MD, bold=True)
        self._t(layout, "NFPA 13 — Current Edition",        cx, by+bh-104, TEXT_SM)
        self._line(layout, bx, by+80, bx+c1w, by+80)
        self._t(layout, "DESIGNED BY:", bx+6, by+72, TEXT_SM, bold=True)
        d = p.get("designer", {})
        self._t(layout, d.get("name","") if isinstance(d,dict) else str(d), bx+6, by+58, TEXT_SM)
        self._t(layout, d.get("cert","") if isinstance(d,dict) else "",     bx+6, by+44, TEXT_SM)
        self._line(layout, bx, by+40, bx+c1w, by+40)
        self._t(layout, "CHECKED BY:", bx+6, by+32, TEXT_SM, bold=True)
        self._t(layout, p.get("checker_name",""), bx+6, by+18, TEXT_SM)

        c2w = int(bw * 0.40); c2x = bx + c1w; px = c2x + 10
        self._line(layout, c2x+c2w, by, c2x+c2w, by+bh)
        self._t(layout, "PROJECT:",  px, by+bh-22, TEXT_SM, bold=True)
        self._t(layout, p.get("project_name",""), px, by+bh-36, TEXT_MD, bold=True)
        self._line(layout, c2x, by+bh-44, c2x+c2w, by+bh-44)
        self._t(layout, "ADDRESS:", px, by+bh-56, TEXT_SM, bold=True)
        self._t(layout, p.get("location",""), px, by+bh-70, TEXT_SM)
        self._line(layout, c2x, by+bh-78, c2x+c2w, by+bh-78)
        self._t(layout, "PROJECT NO. (INTERNAL):",  px,            by+bh-90,  TEXT_SM, bold=True)
        self._t(layout, p.get("project_number_internal",""),  px,  by+bh-104, TEXT_SM)
        self._t(layout, "PROJECT NO. (CUSTOMER):",  px+c2w//2,     by+bh-90,  TEXT_SM, bold=True)
        self._t(layout, p.get("project_number_customer",""),  px+c2w//2, by+bh-104, TEXT_SM)
        self._line(layout, c2x, by+bh-112, c2x+c2w, by+bh-112)
        self._t(layout, "OCCUPANCY:",   px,         by+bh-124, TEXT_SM, bold=True)
        self._t(layout, p.get("occupancy",""),  px, by+bh-138, TEXT_SM)
        self._t(layout, "SYSTEM TYPE:", px+c2w//2,  by+bh-124, TEXT_SM, bold=True)
        self._t(layout, p.get("system_type","WET").upper(), px+c2w//2, by+bh-138, TEXT_SM)
        self._line(layout, c2x, by+bh-146, c2x+c2w, by+bh-146)
        self._t(layout, "AHJ:", px, by+bh-158, TEXT_SM, bold=True)
        self._t(layout, p.get("ahj_jurisdiction",""), px, by+bh-172, TEXT_SM)

        c3x = c2x + c2w; c4x = bx + bw; sx = c3x + 10; cw = c4x - c3x
        self._t(layout, "SHEET TITLE:", sx, by+bh-22, TEXT_SM, bold=True)
        self._t(layout, meta.sheet_title, sx, by+bh-38, TEXT_MD, bold=True)
        self._line(layout, c3x, by+bh-46, c4x, by+bh-46)
        self._t(layout, "SHEET NO.:", sx, by+bh-58, TEXT_SM, bold=True)
        self._t(layout, meta.sheet_number, sx, by+bh-76, TEXT_XL, bold=True)
        self._line(layout, c3x, by+bh-86, c4x, by+bh-86)
        self._t(layout, "DISCIPLINE:",  sx,           by+bh-98,  TEXT_SM, bold=True)
        self._t(layout, "Fire Protection", sx,        by+bh-112, TEXT_SM)
        self._t(layout, "SCALE:",        sx+cw//2,    by+bh-98,  TEXT_SM, bold=True)
        self._t(layout, meta.scale,      sx+cw//2,    by+bh-112, TEXT_SM)
        self._line(layout, c3x, by+bh-120, c4x, by+bh-120)
        self._t(layout, "ISSUE DATE:", sx,        by+bh-132, TEXT_SM, bold=True)
        self._t(layout, meta.issue_date, sx,      by+bh-146, TEXT_SM)
        self._t(layout, "REVISION:",   sx+cw//2,  by+bh-132, TEXT_SM, bold=True)
        self._t(layout, str(len(meta.revisions)), sx+cw//2, by+bh-146, TEXT_SM)
        self._line(layout, c3x, by+bh-154, c4x, by+bh-154)
        self._t(layout, "REV",         sx,     by+bh-166, TEXT_SM, bold=True)
        self._t(layout, "DATE",        sx+28,  by+bh-166, TEXT_SM, bold=True)
        self._t(layout, "DESCRIPTION", sx+84,  by+bh-166, TEXT_SM, bold=True)
        self._line(layout, c3x, by+bh-172, c4x, by+bh-172)
        ry = by + bh - 184
        for rev in (meta.revisions or []):
            r = rev if isinstance(rev, dict) else rev.__dict__
            self._t(layout, r.get("number",""),      sx,    ry, TEXT_SM)
            self._t(layout, r.get("date",""),        sx+28, ry, TEXT_SM)
            self._t(layout, r.get("description",""), sx+84, ry, TEXT_SM)
            ry -= 14
            if ry < by + 10:
                break


# ─── Symbol library ───────────────────────────────────────────────────────────

class SymbolLibrary:
    R = 6

    @classmethod
    def define_all(cls, doc):
        for bname, fn in [
            ("SPKR_UPRT", cls._upright), ("SPKR_PEND",  cls._pendant),
            ("SPKR_SIDE", cls._sidewall),("SPKR_CONC",  cls._concealed),
            ("SPKR_ESFR", cls._esfr),   ("SPKR_CMSA",  cls._cmsa),
            ("VALV_OSY",  cls._osy),    ("VALV_BFV",   cls._bfv),
            ("VALV_CV",   cls._cv),     ("VALV_AV",    cls._av),
            ("VALV_IT",   cls._it),     ("VALV_DR",    cls._dr),
            ("VALV_PRV",  cls._prv),    ("VALV_RPZ",   cls._rpz),
            ("FP_RISER",  cls._riser),  ("FP_FDC",     cls._fdc),
            ("NORTH_ARROW", cls._north),("FLOW_SWITCH", cls._flow_switch),
            ("PRESS_GAUGE", cls._press_gauge),
        ]:
            if bname not in doc.blocks:
                fn(doc, bname)

    @classmethod
    def _b(cls, doc, name): return doc.blocks.new(name)

    @classmethod
    def _upright(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-SPKR-UPRT"})
        b.add_line((-R,0),(R,0), dxfattribs={"layer":"FP-SPKR-UPRT"})
        b.add_line((0,-R),(0,R), dxfattribs={"layer":"FP-SPKR-UPRT"})

    @classmethod
    def _pendant(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-SPKR-PEND"})
        b.add_line((-R,0),(R,0), dxfattribs={"layer":"FP-SPKR-PEND"})

    @classmethod
    def _sidewall(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_arc((0,0), R, -90, 90, dxfattribs={"layer":"FP-SPKR-SIDE"})
        b.add_line((0,-R),(0,R), dxfattribs={"layer":"FP-SPKR-SIDE"})

    @classmethod
    def _concealed(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-SPKR-CONC"})
        h = b.add_hatch(color=colors.BLUE, dxfattribs={"layer":"FP-SPKR-CONC"})
        h.paths.add_edge_path().add_arc((0,0), R, 0, 360)

    @classmethod
    def _esfr(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R,      dxfattribs={"layer":"FP-SPKR-ESFR"})
        b.add_circle((0,0), R*0.55, dxfattribs={"layer":"FP-SPKR-ESFR"})

    @classmethod
    def _cmsa(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_lwpolyline([(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)], dxfattribs={"layer":"FP-SPKR-CMSA"})

    @classmethod
    def _osy(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-VALV"})
        b.add_solid([(-R,0),(R,0),(0,R*1.5)], dxfattribs={"layer":"FP-VALV"})
        b.add_text("OS&Y", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _bfv(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-VALV"})
        b.add_line((-R,0),(R,0), dxfattribs={"layer":"FP-VALV"})
        b.add_text("BFV", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _cv(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-VALV"})
        b.add_line((0,-R),(0,R), dxfattribs={"layer":"FP-VALV"})
        b.add_solid([(0,0),(R,R//2),(R,-R//2)], dxfattribs={"layer":"FP-VALV"})
        b.add_text("CV", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _av(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-VALV"})
        b.add_text("AV", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _it(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_lwpolyline([(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)], dxfattribs={"layer":"FP-VALV"})
        b.add_text("IT", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _dr(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-VALV"})
        b.add_line((-R,-R),(R,R), dxfattribs={"layer":"FP-VALV"})
        b.add_text("DR", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _prv(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_lwpolyline([(-R,0),(0,R),(R,0),(0,-R),(-R,0)], dxfattribs={"layer":"FP-VALV"})
        b.add_text("PRV", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))

    @classmethod
    def _rpz(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_lwpolyline([(-R*1.5,-R),(R*1.5,-R),(R*1.5,R),(-R*1.5,R),(-R*1.5,-R)], dxfattribs={"layer":"FP-VALV","lineweight":25})
        b.add_text("RPZ", dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT_BOLD}).set_placement((0,-3))

    @classmethod
    def _riser(cls, doc, name):
        b = cls._b(doc, name)
        b.add_circle((0,0), 14, dxfattribs={"layer":"FP-RISR","lineweight":50})
        b.add_circle((0,0), 10, dxfattribs={"layer":"FP-RISR"})
        b.add_text("RISER", dxfattribs={"layer":"FP-ANNO-LABL","height":6,"style":FONT_BOLD}).set_placement((0,-3))

    @classmethod
    def _fdc(cls, doc, name):
        b = cls._b(doc, name); R = 10
        b.add_lwpolyline([(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)], dxfattribs={"layer":"FP-FDC","lineweight":50})
        b.add_text("FDC", dxfattribs={"layer":"FP-ANNO-LABL","height":7,"style":FONT_BOLD}).set_placement((0,-4))

    @classmethod
    def _north(cls, doc, name):
        b = cls._b(doc, name)
        b.add_line((0,0),(0,60), dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":35})
        b.add_solid([(0,60),(-8,40),(8,40)], dxfattribs={"layer":"FP-ANNO-SYMB"})
        b.add_circle((0,0), 24, dxfattribs={"layer":"FP-ANNO-SYMB"})
        b.add_text("N", dxfattribs={"layer":"FP-ANNO-LABL","height":16,"style":FONT_BOLD}).set_placement((0,66))

    @classmethod
    def _flow_switch(cls, doc, name):
        b = cls._b(doc, name); R = cls.R
        b.add_lwpolyline([(-R*1.2,-R),(R*1.2,-R),(R*1.2,R),(-R*1.2,R),(-R*1.2,-R)], dxfattribs={"layer":"FP-VALV","lineweight":25})
        b.add_line((-R*0.6,0),(R*0.6,0), dxfattribs={"layer":"FP-VALV"})
        b.add_text("WFS", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R*1.2+3,3))

    @classmethod
    def _press_gauge(cls, doc, name):
        b = cls._b(doc, name); R = cls.R - 1
        b.add_circle((0,0), R, dxfattribs={"layer":"FP-VALV"})
        b.add_line((0,0),(R*0.7, R*0.7), dxfattribs={"layer":"FP-VALV"})
        b.add_text("PG", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}).set_placement((R+3,3))


# ─── Plan view renderer ───────────────────────────────────────────────────────

class PlanViewRenderer:
    def __init__(self, msp, project, scale=SCALE_FACTOR):
        self.msp     = msp
        self.project = project
        self.scale   = scale

    def _ft(self, v):
        return v * self.scale

    def _pt(self, x, y, ox=0, oy=0):
        return (BORDER_X + ox + self._ft(x), BORDER_Y + oy + self._ft(y))

    def draw_walls(self, walls, ox=0, oy=0):
        """
        Draw wall geometry. Two modes:
        - Detailed (from document intelligence): renders each wall segment as filled tile
        - Synthetic: renders building outline as thick closed polygon
        Walls must be visually prominent — thick lines that clearly bound the building.
        """
        import math
        if not walls:
            return
        for w in walls:
            pts_ft = w.get("points", [])
            if len(pts_ft) < 2:
                continue
            is_ext = w.get("exterior", False)
            pts = [(BORDER_X+ox+self._ft(p["x"]), BORDER_Y+oy+self._ft(p["y"])) for p in pts_ft]

            if len(pts) == 2:
                # Wall segment: filled rectangle with thickness
                half_ft = 0.67 if is_ext else 0.33
                half_du = self._ft(half_ft) / 2
                dx = pts[1][0]-pts[0][0]; dy = pts[1][1]-pts[0][1]
                length = math.hypot(dx, dy)
                if length < 0.1: continue
                px_n = -dy/length * half_du; py_n = dx/length * half_du
                corners = [
                    (pts[0][0]+px_n, pts[0][1]+py_n),
                    (pts[1][0]+px_n, pts[1][1]+py_n),
                    (pts[1][0]-px_n, pts[1][1]-py_n),
                    (pts[0][0]-px_n, pts[0][1]-py_n),
                ]
                self.msp.add_solid(corners, dxfattribs={"layer":"A-WALL-FULL","color":8})
                self.msp.add_lwpolyline(corners+[corners[0]],
                    dxfattribs={"layer":"A-WALL-FULL","lineweight":50 if is_ext else 25})
            else:
                # Polygon outline: thick closed boundary
                lw = 100 if is_ext else 50
                self.msp.add_lwpolyline(pts, close=True,
                    dxfattribs={"layer":"A-WALL-FULL","lineweight":lw})
                # Inset line for double-wall look on rectangular buildings
                if is_ext and len(pts) == 4:
                    wall_t = self._ft(0.67)
                    xs2=[p[0] for p in pts]; ys2=[p[1] for p in pts]
                    x0i,x1i = min(xs2)+wall_t, max(xs2)-wall_t
                    y0i,y1i = min(ys2)+wall_t, max(ys2)-wall_t
                    if x1i>x0i and y1i>y0i:
                        self.msp.add_lwpolyline(
                            [(x0i,y0i),(x1i,y0i),(x1i,y1i),(x0i,y1i),(x0i,y0i)],
                            close=True, dxfattribs={"layer":"A-WALL-FULL","lineweight":25})

    def draw_columns(self, cols, ox=0, oy=0):
        for c in cols:
            cx, cy = self._pt(c["x"], c["y"], ox, oy)
            w = self._ft(c.get("width", 1.5)); d = self._ft(c.get("depth", 1.5))
            pts = [(cx-w/2,cy-d/2),(cx+w/2,cy-d/2),(cx+w/2,cy+d/2),(cx-w/2,cy+d/2),(cx-w/2,cy-d/2)]
            self.msp.add_lwpolyline(pts, close=True, dxfattribs={"layer":"A-COLS","lineweight":50})

    def draw_rooms(self, rooms, ox=0, oy=0):
        for r in rooms:
            pts = [(BORDER_X+ox+self._ft(p["x"]), BORDER_Y+oy+self._ft(p["y"])) for p in r["boundary"]]
            if len(pts) < 3: continue
            self.msp.add_lwpolyline(pts, close=True, dxfattribs={"layer":"A-ROOM","lineweight":5})
            cx = sum(p[0] for p in pts)/len(pts)
            cy = sum(p[1] for p in pts)/len(pts)
            # Scale text to room size (larger rooms get larger labels)
            xs2 = [p[0] for p in pts]; ys2 = [p[1] for p in pts]
            room_w_du = max(xs2)-min(xs2); room_h_du = max(ys2)-min(ys2)
            room_min_du = min(room_w_du, room_h_du)
            # Label height: 3% of smallest room dimension, clamped to TEXT_SM–TEXT_LG
            ht_room = max(TEXT_SM, min(TEXT_LG, room_min_du * 0.07))

            name = r.get("name","").upper()
            if name:
                self.msp.add_text(name,
                    dxfattribs={"layer":"A-ROOM-IDEN","height":ht_room,"style":FONT_BOLD}
                ).set_placement((cx, cy+ht_room*0.5), align=TextEntityAlignment.MIDDLE_CENTER)
            tag = str(r.get("tag") or r.get("room_number",""))
            if tag:
                self.msp.add_text(tag,
                    dxfattribs={"layer":"A-ROOM-IDEN","height":ht_room*0.75,"style":FONT}
                ).set_placement((cx, cy-ht_room*0.3), align=TextEntityAlignment.MIDDLE_CENTER)

    def draw_pipes(self, pipes, ox=0, oy=0):
        """
        Draw pipes with professional annotations matching AutoSprink output:
        - Proper lineweights by pipe type
        - Pipe size labels in "2-1/2\" SCH 40" format — labeled ONCE per
          distinct (pipe_type, diameter, schedule) run, not every segment.
          Real drafters don't repeat the size label on every joint; convention
          carries it along the run until something changes.
        - Length labels only on MAINS and CROSS-MAINS (not branches), because
          stamping a length on every 1-2 ft branch segment produces unreadable
          clutter. Branch geometry is conveyed by the grid + sprinkler spacing.
        - Flow direction arrows on supply mains
        - Filled tee marker at branch junctions
        """
        # Track which (pipe_type, diameter, schedule) tuples we've already
        # labeled. The first long-enough segment of a new run gets a label;
        # subsequent same-run segments do not. Reset every call so labels
        # appear once per sheet, not once per session.
        labeled_runs: set = set()
        # Minimum displayed-length for any annotation (in feet). 6 ft on a
        # 1/8" = 1'-0" sheet is roughly 3/4" of paper — readable label space.
        MIN_ANNOTATED_FT = 6.0

        for s in pipes:
            fx, fy = self._pt(s["from"]["x"], s["from"]["y"], ox, oy)
            tx, ty = self._pt(s["to"]["x"],   s["to"]["y"],   ox, oy)
            pt     = s.get("pipe_type","branch")
            layer  = {"main":"FP-PIPE-MAIN","cross":"FP-PIPE-XMAIN","branch":"FP-PIPE-BRNCH",
                      "armover":"FP-PIPE-ARMOV","drain":"FP-PIPE-DRAIN"}.get(pt,"FP-PIPE-BRNCH")
            lw     = 50 if pt=="main" else (35 if pt in("cross","xmain") else 18)
            self.msp.add_line((fx,fy),(tx,ty), dxfattribs={"layer":layer,"lineweight":lw})

            # Flow direction arrow on supply mains (small filled triangle at midpoint)
            if pt == "main":
                mx, my = (fx+tx)/2, (fy+ty)/2
                ang_r  = math.atan2(ty-fy, tx-fx)
                aw     = 10   # arrow wing half-width
                al     = 18   # arrow length
                tip_x  = mx + al/2 * math.cos(ang_r)
                tip_y  = my + al/2 * math.sin(ang_r)
                w1x    = mx - al/2*math.cos(ang_r) + aw*math.sin(ang_r)
                w1y    = my - al/2*math.sin(ang_r) - aw*math.cos(ang_r)
                w2x    = mx - al/2*math.cos(ang_r) - aw*math.sin(ang_r)
                w2y    = my - al/2*math.sin(ang_r) + aw*math.cos(ang_r)
                self.msp.add_solid([(tip_x,tip_y),(w1x,w1y),(w2x,w2y)],
                                   dxfattribs={"layer":layer})

            # Tee marker at branch start (small filled circle)
            if pt == "branch":
                self.msp.add_circle((fx,fy), 5,
                                    dxfattribs={"layer":"FP-PIPE-BRNCH"})

            # Pipe SIZE label (FP-ANNO-LABL, ht=9, ROMANS, bylayer color)
            # Pipe LENGTH label (FP-ANNO-DIMS, ht=7.65, ROMANS, color=5/blue)
            #
            # Real drafters label ONCE per run, not every segment:
            #   - SIZE: only the first long-enough segment of each distinct
            #           (pipe_type, diameter, schedule) combination.
            #   - LENGTH: only on mains and cross-mains. Branch lines carry
            #             too many short segments to label cleanly; their
            #             lengths come from the grid + sprinkler spacing.
            dia   = s.get("diameter","")
            sched = s.get("schedule","")
            seg_len = s.get("length", 0) or math.hypot(tx-fx, ty-fy) / self.scale

            run_key = (pt, str(dia), str(sched))
            label_this_size = (
                dia
                and seg_len > MIN_ANNOTATED_FT
                and run_key not in labeled_runs
            )
            label_this_length = (
                pt in ("main", "cross", "xmain")
                and seg_len > MIN_ANNOTATED_FT
            )

            if label_this_size:
                labeled_runs.add(run_key)
                dia_str = self._format_dia(dia)
                lbl     = dia_str + '" ' + (sched or "")
                mx, my  = (fx+tx)/2, (fy+ty)/2
                ang_deg = math.degrees(math.atan2(ty-fy, tx-fx))
                rot     = ang_deg if -90 < ang_deg <= 90 else ang_deg + 180
                # Size above (perpendicular +)
                perp_ang = ang_deg + 90
                off = 15
                sx = mx + off*math.cos(math.radians(perp_ang))
                sy = my + off*math.sin(math.radians(perp_ang))
                self.msp.add_text(lbl.strip(),
                    dxfattribs={"layer":"FP-ANNO-LABL","height":9,"style":"ROMANS","rotation":rot}
                ).set_placement((sx, sy))

            if label_this_length:
                ang_deg = math.degrees(math.atan2(ty-fy, tx-fx))
                ft_l = int(seg_len); in_l = int(round((seg_len-ft_l)*12))
                len_lbl = "%d-%02d" % (ft_l, in_l) if in_l else "%d-0" % ft_l
                mx2,my2 = (fx+tx)/2,(fy+ty)/2
                perp_opp = ang_deg - 90
                lx2 = mx2 + 15*math.cos(math.radians(perp_opp))
                ly2 = my2 + 15*math.sin(math.radians(perp_opp))
                rot2 = ang_deg if -90<ang_deg<=90 else ang_deg+180
                self.msp.add_text(len_lbl,
                    dxfattribs={"layer":"FP-ANNO-DIMS","height":7.65,
                                "style":"ROMANS","rotation":rot2,"color":5}
                ).set_placement((lx2, ly2))

    @staticmethod
    def _format_dia(d) -> str:
        """Format pipe diameter as engineering fraction: 2.5 → 2-1/2, 1.25 → 1-1/4"""
        frac = {0.75:"3/4",1.0:"1",1.25:"1-1/4",1.5:"1-1/2",2.0:"2",
                2.5:"2-1/2",3.0:"3",3.5:"3-1/2",4.0:"4",5.0:"5",6.0:"6",8.0:"8"}
        try:
            return frac.get(float(d), str(d))
        except Exception:
            return str(d)

    def draw_sprinklers(self, spkrs, ox=0, oy=0, show_coverage=False,
                        hydraulic_remote_ids=None):
        """
        Draw sprinkler heads. Coverage circles OFF by default (matches AutoSprink default).
        hydraulic_remote_ids: set of head IDs marked as hydraulic reference (tagged HR).
        """
        bmap = {"upright":"SPKR_UPRT","pendant":"SPKR_PEND","sidewall":"SPKR_SIDE",
                "concealed":"SPKR_CONC","esfr":"SPKR_ESFR","cmsa":"SPKR_CMSA"}
        remote_ids = set(hydraulic_remote_ids or [])

        for i, s in enumerate(spkrs):
            px, py = self._pt(s["x"], s["y"], ox, oy)
            st     = s.get("type","pendant").lower()
            is_esfr = s.get("is_esfr") or st == "esfr"
            layer  = "FP-SPKR-ESFR" if is_esfr else "FP-SPKR-PEND"
            self.msp.add_blockref(bmap.get(st,"SPKR_PEND"), (px,py),
                                  dxfattribs={"layer":layer})

            # Coverage circle only when explicitly requested
            if show_coverage and s.get("coverage_radius"):
                self.msp.add_circle(
                    (px,py), self._ft(s["coverage_radius"]),
                    dxfattribs={"layer":"FP-SPKR-COVR","linetype":"DASHED","color":251})

            # Head labels suppressed in floor plan (per AutoSprink — shown in schedule only)

            # Hydraulic reference marker on remote area heads (HR tag in red)
            if s.get("id","") in remote_ids:
                r = SymbolLibrary.R
                self.msp.add_circle((px,py), r+4, dxfattribs={"layer":"FP-ANNO-LABL","color":colors.RED})
                self.msp.add_text(
                    "HR",
                    dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT_BOLD,"color":colors.RED}
                ).set_placement((px+r+18, py-r-10))

    def draw_valves(self, valves, ox=0, oy=0):
        bmap = {"osy":"VALV_OSY","butterfly":"VALV_BFV","check":"VALV_CV",
                "alarm":"VALV_AV","inspector_test":"VALV_IT","drain":"VALV_DR"}
        for v in valves:
            px, py = self._pt(v["x"], v["y"], ox, oy)
            self.msp.add_blockref(
                bmap.get(v.get("type","osy").lower(),"VALV_OSY"),
                (px,py), dxfattribs={"layer":"FP-VALV"})
            self.msp.add_text(
                v.get("id",""),
                dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM*0.75,"style":FONT}
            ).set_placement((px+SymbolLibrary.R+4, py+2))

    def draw_equipment(self, equip, ox=0, oy=0):
        bmap = {"riser":"FP_RISER","fdc":"FP_FDC"}
        for e in equip:
            px, py = self._pt(e["x"], e["y"], ox, oy)
            self.msp.add_blockref(
                bmap.get(e.get("type","riser").lower(),"FP_RISER"),
                (px,py), dxfattribs={"layer":"FP-EQUP"})
            self.msp.add_text(
                e.get("label",""),
                dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT_BOLD}
            ).set_placement((px, py-20))


    def draw_head_dimensions(self, spkrs, ox=0, oy=0):
        """
        Draw dimension strings between adjacent sprinkler heads and from heads to zone walls.
        Shows head-to-head spacing (e.g. "10'-0"") and wall offset (e.g. "5'-0"").
        Only labels the first few rows for clarity — avoids over-cluttering.
        """
        if not spkrs: return
        from collections import defaultdict

        SF = SCALE_FACTOR

        # Group ceiling heads by Y coordinate (rows)
        ceiling = [s for s in spkrs if not s.get("in_rack")]
        if not ceiling: return

        rows: dict = defaultdict(list)
        for s in ceiling:
            ry = round(s["y"] / 2) * 2   # snap to 2ft grid
            rows[ry].append(s)

        row_keys = sorted(rows.keys())
        # Only annotate first 3 rows to avoid clutter on large buildings
        for ri, ry in enumerate(row_keys[:3]):
            row_sp = sorted(rows[ry], key=lambda s: s["x"])
            if len(row_sp) < 2: continue

            dim_y_offset = -20 - ri * 0   # dimension line Y offset (below heads)

            # Head-to-head dimensions
            for i in range(len(row_sp)-1):
                s1, s2 = row_sp[i], row_sp[i+1]
                dx = abs(s2["x"] - s1["x"])
                px1, py1 = self._pt(s1["x"], s1["y"], ox, oy)
                px2, py2 = self._pt(s2["x"], s2["y"], ox, oy)
                mid_x = (px1+px2)/2
                dim_y = min(py1,py2) - 22

                # Dimension line
                self.msp.add_line((px1,dim_y),(px2,dim_y),
                                  dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":13})
                # Extension lines
                self.msp.add_line((px1,py1-SymbolLibrary.R-2),(px1,dim_y+2),
                                  dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
                self.msp.add_line((px2,py2-SymbolLibrary.R-2),(px2,dim_y+2),
                                  dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
                # Arrow ticks
                for px in [px1, px2]:
                    sign = 1 if px == px1 else -1
                    self.msp.add_solid(
                        [(px,dim_y),(px+sign*10,dim_y+4),(px+sign*10,dim_y-4)],
                        dxfattribs={"layer":"FP-ANNO-DIMS"})

                # Label: feet and inches
                ft  = int(dx)
                ins = int(round((dx - ft) * 12))
                lbl = f"{ft}'-{ins:02d}\"" if ins > 0 else f"{ft}'-0\""
                self.msp.add_text(
                    lbl,
                    dxfattribs={"layer":"FP-ANNO-DIMS","height":TEXT_SM*0.85,"style":FONT}
                ).set_placement((mid_x, dim_y+4))

    def draw_design_params_block(self, hydraulics: dict, project: dict):
        """
        Draw design parameters summary block in the top-left corner of the floor plan.
        Shows key design criteria: hazard, density, area, K-factor, pipe material.
        """
        bx = BORDER_X + 30
        by = BORDER_Y + DRAW_H - 30
        bw = 280; lh = 16

        ra    = hydraulics.get("remote_area_calcs",{})
        da    = hydraulics.get("density_area",{})
        p     = project

        self.msp.add_lwpolyline(
            [(bx,by),(bx+bw,by),(bx+bw,by-14*lh-10),(bx,by-14*lh-10),(bx,by)],
            dxfattribs={"layer":"FP-TBLK","lineweight":25})

        self.msp.add_text(
            "DESIGN INFORMATION",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((bx+4, by-TEXT_SM-2))

        rows = [
            ("Occupancy",  p.get("occupancy","")[:25]),
            ("System",     p.get("system_type","Wet Pipe")),
            ("Hazard",     ra.get("hazard","").replace("_"," ").title()),
            ("Density",    f'{da.get("density","—")} gpm/ft²'),
            ("Rem. Area",  f'{da.get("area","—")} ft²'),
            ("K-Factor",   str(ra.get("k_factor","5.6"))),
            ("Min. Psi",   f'{ra.get("min_sprinkler_psi","7")} psi'),
            ("Pipe",       p.get("pipe_material","Steel")[:20]),
            ("HW C",       str(ra.get("hw_c_factor",120))),
            ("Static",     f'{hydraulics.get("static_pressure","—")} psi'),
            ("Residual",   f'{hydraulics.get("residual_pressure","—")} psi'),
            ("Required",   f'{hydraulics.get("required_pressure","—")} psi'),
            ("Demand",     f'{hydraulics.get("flow_demand","—")} gpm'),
        ]
        ry = by - TEXT_SM - 18
        for lbl, val in rows:
            self.msp.add_text(
                lbl+":", dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT_BOLD}
            ).set_placement((bx+4, ry))
            self.msp.add_text(
                str(val), dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
            ).set_placement((bx+100, ry))
            ry -= lh

    
    def draw_grid(self, building_w_ft, building_d_ft, ox=0, oy=0, structural_grid=None):
        """
        Draw structural grid with bubble callouts.
        If structural_grid dict is provided (from Vision extraction), uses actual
        column/row positions from the drawings. Otherwise generates a synthetic grid.
        """
        bw    = building_w_ft * self.scale
        bd    = building_d_ft * self.scale
        bx0   = BORDER_X + ox
        by0   = BORDER_Y + oy
        r_bub = 20

        sg = structural_grid or {}
        sg_cols = sg.get("columns", [])  # [{"label":"A","x_ft":0}, ...]
        sg_rows = sg.get("rows",    [])  # [{"label":"1","y_ft":0}, ...]

        # Build column list — from actual grid if available, else synthetic
        if sg_cols:
            cols = [(c["label"], float(c["x_ft"])) for c in sg_cols
                    if 0 <= float(c["x_ft"]) <= building_w_ft + 1]
        else:
            import string
            x_interval = max(10.0, round(building_w_ft / 6 / 5) * 5)
            letters    = list(string.ascii_uppercase)
            cols       = [(letters[i], xi)
                          for i, xi in enumerate(
                              [j*x_interval for j in range(int(building_w_ft/x_interval)+2)]
                              ) if xi <= building_w_ft + 0.1]

        # Build row list
        if sg_rows:
            rows = [(str(r["label"]), float(r["y_ft"])) for r in sg_rows
                    if 0 <= float(r["y_ft"]) <= building_d_ft + 1]
        else:
            y_interval = max(10.0, round(building_d_ft / 6 / 5) * 5)
            rows       = [(str(i+1), yi)
                          for i, yi in enumerate(
                              [j*y_interval for j in range(int(building_d_ft/y_interval)+2)]
                              ) if yi <= building_d_ft + 0.1]

        # Draw vertical grid lines + bubbles (columns A, B, C...)
        for lbl, x_ft in cols:
            gx = bx0 + x_ft * self.scale
            self.msp.add_line((gx, by0-r_bub-4),(gx, by0+bd+r_bub+4),
                              dxfattribs={"layer":"FP-GRID","color":9})
            for gy in [by0-r_bub-4, by0+bd+r_bub+4]:
                self.msp.add_circle((gx,gy), r_bub,
                    dxfattribs={"layer":"FP-GRID","color":8,"lineweight":13})
                self.msp.add_text(lbl,
                    dxfattribs={"layer":"FP-GRID","height":TEXT_SM,"style":FONT_BOLD,"color":8}
                ).set_placement((gx,gy-TEXT_SM*0.4),
                                align=TextEntityAlignment.MIDDLE_CENTER)

        # Draw horizontal grid lines + bubbles (rows 1, 2, 3...)
        for lbl, y_ft in rows:
            gy = by0 + y_ft * self.scale
            self.msp.add_line((bx0-r_bub-4,gy),(bx0+bw+r_bub+4,gy),
                              dxfattribs={"layer":"FP-GRID","color":9})
            for gx in [bx0-r_bub-4, bx0+bw+r_bub+4]:
                self.msp.add_circle((gx,gy), r_bub,
                    dxfattribs={"layer":"FP-GRID","color":8,"lineweight":13})
                self.msp.add_text(lbl,
                    dxfattribs={"layer":"FP-GRID","height":TEXT_SM,"style":FONT_BOLD,"color":8}
                ).set_placement((gx,gy-TEXT_SM*0.4),
                                align=TextEntityAlignment.MIDDLE_CENTER)

    def draw_sway_braces(self, braces, ox=0, oy=0):
        """Draw seismic sway brace markers — LAT# and LNG# labels with cross symbol."""
        for b in braces:
            px, py = self._pt(b["x"], b["y"], ox, oy)
            direction = b.get("direction","4-way")
            # Cross symbol (+ shape)
            arm = 10
            self.msp.add_line((px-arm,py),(px+arm,py),
                              dxfattribs={"layer":"FP-HNGR","lineweight":25,"color":colors.YELLOW})
            self.msp.add_line((px,py-arm),(px,py+arm),
                              dxfattribs={"layer":"FP-HNGR","lineweight":25,"color":colors.YELLOW})
            # Brace label
            brace_id = b.get("id","SB")
            dia      = b.get("pipe_size",2.0)
            lbl_type = "LAT4" if dia >= 3 else "LAT3"
            self.msp.add_text(lbl_type,
                dxfattribs={"layer":"FP-HNGR","height":TEXT_SM*0.75,
                            "style":FONT,"color":colors.YELLOW}
            ).set_placement((px+arm+3, py+2))

    def draw_end_of_line_restraints(self, pipe_sections, ox=0, oy=0):
        """Draw end-of-line restraint symbol at branch line endpoints."""
        branches = [s for s in pipe_sections if s.get("pipe_type")=="branch"]
        for sec in branches:
            # End of branch = the 'to' node
            tx, ty = self._pt(sec["to"]["x"], sec["to"]["y"], ox, oy)
            # Small X mark
            d = 8
            self.msp.add_line((tx-d,ty-d),(tx+d,ty+d),
                              dxfattribs={"layer":"FP-HNGR","lineweight":18})
            self.msp.add_line((tx+d,ty-d),(tx-d,ty+d),
                              dxfattribs={"layer":"FP-HNGR","lineweight":18})


    def draw_hydraulic_info_block(self, hydraulics: dict, project: dict):
        ra    = hydraulics.get("remote_area_calcs", {})
        da    = hydraulics.get("density_area", {})
        hose  = float(ra.get("hose_stream_gpm", 0))
        bx    = BORDER_X + 20
        bw    = 340; lh = 18; pad = 3
        def _t(text, x, y, ht=TEXT_SM, bold=False, color=None):
            att = {"layer":"FP-ANNO-NOTE","height":ht,"style":FONT_BOLD if bold else FONT}
            if color: att["color"] = color
            self.msp.add_text(str(text), dxfattribs=att).set_placement((x, y))
        def _hl(y):
            self.msp.add_line((bx,y),(bx+bw,y),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":9})
        def _vl(x,y0,y1):
            self.msp.add_line((x,y0),(x,y1),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":9})

        req_psi   = float(hydraulics.get("required_pressure", 0))
        flow_dem  = float(hydraulics.get("flow_demand", 0))
        spkr_flow = max(0, flow_dem - hose)
        margin    = float(hydraulics.get("pressure_delta", 0))
        margin_pct= abs(margin/req_psi*100) if req_psi>0 else 0
        density   = da.get("density") or ""
        area      = da.get("area") or ""
        density_str = (f"{density} for {int(float(area))}ft2" if density and area else str(density))
        n_heads   = ra.get("remote_sprinkler_count", 0)
        k         = ra.get("k_factor", 5.6)
        hz        = ra.get("hazard","light").replace("_"," ").title()

        by = BORDER_Y + DRAW_H - 20

        # Title subtitle
        _t(f"{hz} * {density_str}", bx+2, by-TEXT_SM-2, TEXT_SM*0.8)
        by -= TEXT_SM + 8

        # Outer border
        n_rows = 11; bh = lh*(n_rows+2) + pad*2
        self.msp.add_lwpolyline(
            [(bx,by),(bx+bw,by),(bx+bw,by-bh),(bx,by-bh),(bx,by)],
            dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})

        # Title row (bold border, no fill — text visible on white bg)
        th = lh+4
        self.msp.add_lwpolyline(
            [(bx,by),(bx+bw,by),(bx+bw,by-th),(bx,by-th),(bx,by)],
            dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":35})
        self.msp.add_text("Hydraulic Information",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((bx+bw//2, by-th+pad), align=TextEntityAlignment.MIDDLE_CENTER)
        by -= th
        # Subtitle
        sh = lh-2
        self.msp.add_text("Remote Area 1",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.9,"style":FONT}
        ).set_placement((bx+bw//2, by-sh+pad), align=TextEntityAlignment.MIDDLE_CENTER)
        _hl(by-sh); by -= sh

        mid = bx + int(bw*0.60)
        _vl(mid, by, by - lh*n_rows)

        rows = [
            ("OCCUPANCY CLASSIFICATION", hz),
            ("DENSITY (gpm/ft2)",        density_str),
            ("TOTAL HOSE STREAMS",       f"{hose:.2f}"),
            ("DRY CAPACITY",             "0.00gal"),
            ("TOTAL HEADS FLOWING",      str(n_heads)),
            ("K-FACTOR",                 str(k)),
            ("TOTAL WATER REQUIRED",     f"{spkr_flow:.2f}"),
            ("TOTAL PRESSURE REQUIRED",  f"{req_psi:.3f}"),
            ("BASE of RISER (gpm)",      f"{spkr_flow:.2f}"),
            ("BASE of RISER (psi)",      f"{req_psi:.3f}"),
            ("SAFETY MARGIN (psi)",      f"{margin:+.3f} ({margin_pct:.1f}%)"),
        ]
        for label, val in rows:
            ry = by - lh; _hl(ry)
            _t(label, bx+3, ry+pad, TEXT_SM*0.85, bold=True)
            clr = (colors.GREEN if "SAFETY" in label and margin>=0 else
                   colors.RED   if "SAFETY" in label else None)
            _t(val, mid+3, ry+pad, TEXT_SM*0.85, color=clr)
            by = ry


    def draw_hangers(self, hangers, ox=0, oy=0):
        """
        Draw hanger designation circles on the plan.
        Number shown comes directly from the design engine's 'designation' field,
        which is determined by structural framing type and pipe size.
        No hardcoded values — every project gets its own hanger numbers.

        Annotation density note: only the SYMBOL is drawn per hanger.
        The per-hanger numeric designation was previously stamped at every
        location, producing 1700+ text entities on a Costco-scale plan that
        rendered the sheet unreadable. The hanger LEGEND (draw_hanger_legend)
        already explains the designation→type mapping; following standard
        drafting convention, the symbol on the sheet is enough.
        """
        for h in hangers:
            px, py = self._pt(h["x"], h["y"], ox, oy)
            r = 8
            self.msp.add_circle((px,py), r,
                dxfattribs={"layer":"FP-HNGR","lineweight":9,"color":colors.CYAN})

    def draw_hanger_legend(self, hangers):
        """
        Draw hanger designation legend.
        Built dynamically from the actual hangers in this project —
        only the types actually used appear in the legend.
        """
        # Collect unique (designation_num, description) pairs from actual hangers
        seen = {}
        for h in hangers:
            num  = h.get("designation") or {
                "clevis":12,"trapeze":9,"rod":1,"wood":24,"u_hook":19,"insert":1
            }.get(h.get("type","rod"), 1)
            desc = h.get("description") or f"HANGER TYPE {num}"
            if num not in seen:
                seen[num] = desc.upper()

        # Always include end-of-line restraint since it's on every project
        if 2 not in seen:
            seen[2] = "END OF LINE RESTRAINT"

        items = sorted(seen.items())
        lx = BORDER_X + 20; ly = BORDER_Y + 60; lh = 16
        self.msp.add_text("HANGER DESIGNATIONS:",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((lx, ly))
        ly -= lh + 2
        for num, desc in items:
            r = 6
            self.msp.add_circle((lx+r, ly-r), r,
                dxfattribs={"layer":"FP-HNGR","color":colors.CYAN,"lineweight":9})
            self.msp.add_text(str(num),
                dxfattribs={"layer":"FP-HNGR","height":TEXT_SM*0.7,
                            "style":FONT_BOLD,"color":colors.CYAN}
            ).set_placement((lx+r, ly-r-TEXT_SM*0.35), align=TextEntityAlignment.MIDDLE_CENTER)
            self.msp.add_text(desc,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.8,"style":FONT}
            ).set_placement((lx+r*2+4, ly-r-TEXT_SM*0.35+3))
            ly -= lh



    def draw_hanging_notes(self, project: dict, pipe_sections: list):
        """
        Draw hanging elevation notes on the floor plan.
        Matches Battalion One convention:
          'HANG BRANCH LINES AT 0\'-6\" BELOW BOTTOM CORD OF I-JOIST, UNO'
          'HANG MAINS AT 6\'-6\" BELOW BOTTOM CORD OF I-JOIST'
        Positioned in the drawing area nearest the piping runs.
        """
        framing = project.get("structural_framing","").lower().replace("-","_")
        ch      = float(project.get("ceiling_height",10))
        # Branch clearance from structure
        br_clr  = "0'-6\"" if ch <= 15 else "1'-0\""
        mn_clr  = "6'-6\"" if ch <= 15 else "8'-0\""

        if "joist" in framing or "truss" in framing:
            notes = [
                f"HANG BRANCH LINES AT {br_clr} BELOW BOTTOM CORD OF I-JOIST, UNO",
                f"HANG MAINS AT {mn_clr} BELOW BOTTOM CORD OF I-JOIST",
            ]
        elif "beam" in framing or "steel" in framing:
            notes = [
                f"HANG BRANCH LINES AT {br_clr} BELOW BOTTOM FLANGE OF STEEL BEAM",
                f"HANG MAINS AT {mn_clr} BELOW BOTTOM FLANGE OF STEEL BEAM",
            ]
        else:
            notes = [
                f"HANG BRANCH LINES AT {br_clr} BELOW CEILING STRUCTURE",
                f"HANG MAINS AT {mn_clr} BELOW CEILING STRUCTURE",
            ]

        # Place in the upper-left zone of the floor plan drawing area
        nx = BORDER_X + 30
        ny = BORDER_Y + int(DRAW_H * 0.92)
        bw2 = 350
        for i, note in enumerate(notes):
            y = ny - i * 18
            self.msp.add_lwpolyline(
                [(nx-2,y-2),(nx+bw2,y-2),(nx+bw2,y+14),(nx-2,y+14),(nx-2,y-2)],
                dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":9})
            self.msp.add_text(note,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.82,"style":FONT_BOLD}
            ).set_placement((nx+2, y+2))


    def draw_north_arrow(self, rot=0):
        nx = BORDER_X + DRAW_W - 120
        ny = BORDER_Y + DRAW_H - 120
        self.msp.add_blockref("NORTH_ARROW", (nx,ny), dxfattribs={"layer":"FP-ANNO-SYMB","rotation":rot})
        self.msp.add_text(
            f"PROJ NORTH = {rot}° FROM TRUE",
            dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM*0.8,"style":FONT}
        ).set_placement((nx, ny-44))


    def draw_wall_dimensions(self, walls, building_w_ft, building_d_ft, ox=0, oy=0):
        """
        Draw exterior dimension strings at building perimeter.
        Shows overall building width (bottom) and depth (left side).
        """
        if not (building_w_ft > 0 and building_d_ft > 0):
            return

        bw_du = building_w_ft * self.scale
        bd_du = building_d_ft * self.scale
        bx0   = BORDER_X + ox
        by0   = BORDER_Y + oy
        off   = 28   # offset from building outline

        def dim_horiz(x0, x1, y, label, lw=9):
            """Horizontal dimension string."""
            y_dim = y - off
            self.msp.add_line((x0,y_dim),(x1,y_dim),
                              dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":lw})
            self.msp.add_line((x0,y),(x0,y_dim),
                              dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
            self.msp.add_line((x1,y),(x1,y_dim),
                              dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
            # Arrow ticks
            for xi in [x0, x1]:
                sign = 1 if xi == x0 else -1
                self.msp.add_solid([(xi,y_dim),(xi+sign*12,y_dim+4),(xi+sign*12,y_dim-4)],
                                   dxfattribs={"layer":"FP-ANNO-DIMS"})
            self.msp.add_text(label,
                dxfattribs={"layer":"FP-ANNO-DIMS","height":TEXT_SM*0.85,"style":FONT,"color":colors.BLUE}
            ).set_placement(((x0+x1)/2, y_dim+4),
                            align=TextEntityAlignment.MIDDLE_CENTER)

        def dim_vert(y0, y1, x, label, lw=9):
            """Vertical dimension string."""
            x_dim = x - off
            self.msp.add_line((x_dim,y0),(x_dim,y1),
                              dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":lw})
            self.msp.add_line((x,y0),(x_dim,y0),
                              dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
            self.msp.add_line((x,y1),(x_dim,y1),
                              dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
            for yi in [y0, y1]:
                sign = 1 if yi == y0 else -1
                self.msp.add_solid([(x_dim,yi),(x_dim+4,yi+sign*12),(x_dim-4,yi+sign*12)],
                                   dxfattribs={"layer":"FP-ANNO-DIMS"})
            ft = int(building_d_ft); inch = int(round((building_d_ft-ft)*12))
            self.msp.add_text(label,
                dxfattribs={"layer":"FP-ANNO-DIMS","height":TEXT_SM*0.85,
                            "style":FONT,"rotation":90,"color":colors.BLUE}
            ).set_placement((x_dim-4, (y0+y1)/2),
                            align=TextEntityAlignment.MIDDLE_CENTER)

        # Format feet-inches
        def fmt(ft_val):
            f = int(ft_val); i = int(round((ft_val-f)*12))
            if i: return str(f) + "'-%02d\"" % i
            return str(f) + "'-0\""

        # Overall building width (bottom dimension)
        dim_horiz(bx0, bx0+bw_du, by0, fmt(building_w_ft))
        # Overall building depth (left dimension)
        dim_vert(by0, by0+bd_du, bx0, fmt(building_d_ft))


    def draw_scale_bar(self, scale_str):
        """Alternating black/white sawtooth scale bar matching AutoSprink style."""
        sx   = BORDER_X + FP_PANEL_W//2 - 200
        sy   = BORDER_Y + 44
        segs = [0, 2, 4, 8, 12]
        seg_du = [self.scale * (segs[i+1]-segs[i]) for i in range(len(segs)-1)]
        bar_h  = 10
        cur_x  = sx
        for i, w in enumerate(seg_du):
            fill_c = colors.WHITE if i % 2 == 0 else 0
            self.msp.add_solid(
                [(cur_x,sy),(cur_x+w,sy),(cur_x+w,sy+bar_h),(cur_x,sy+bar_h)],
                dxfattribs={"layer":"FP-ANNO-SYMB","color":fill_c})
            self.msp.add_lwpolyline(
                [(cur_x,sy),(cur_x+w,sy),(cur_x+w,sy+bar_h),(cur_x,sy+bar_h),(cur_x,sy)],
                dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":13})
            cur_x += w
        for i, ft in enumerate(segs):
            tx = sx + sum(seg_du[:i])
            self.msp.add_text(str(ft) + "'",
                dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM*0.75,"style":FONT}
            ).set_placement((tx, sy-12), align=TextEntityAlignment.MIDDLE_CENTER)
        self.msp.add_text("SCALE: " + str(scale_str),
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((sx, sy+bar_h+6))

    def draw_general_notes(self, extra=None):
        """Full 20-item NFPA 13 general notes matching AHJ submittal requirements."""
        p = self.project
        sched   = "SCHEDULE 10" if "10" in p.get("pipe_material","").lower() else "SCHEDULE 40"
        seismic = p.get("seismic_zone","D1")
        sys_type= p.get("system_type","WET PIPE").upper()
        occ     = p.get("occupancy","").upper() or "ALL"
        ct      = p.get("construction_type","") or "SEE STRUCTURAL DRAWINGS"
        spares  = p.get("spare_heads", 6)
        notes = [
            f"ALL MATERIAL & INSTALLATION SHALL CONFORM TO NFPA 13 CURRENT EDITION AND THE AUTHORITY HAVING JURISDICTION.",
            f"SYSTEM SHALL BE DESIGNED PER NFPA 13 FOR {occ} HAZARD OCCUPANCY.",
            f"BUILDING CONSTRUCTION TYPE: {ct}.",
            f"EARTHQUAKE BRACING SHALL CONFORM TO NFPA 13, SEISMIC ZONE {seismic}, INSTALLED PER SWAY BRACE DETAILS ON THIS SHEET.",
            "UNDERGROUND DESIGN SHALL CONFORM TO NFPA 24. UNDERGROUND SHALL BE FLUSHED AND TESTED PRIOR TO TIE-IN OF OVERHEAD SYSTEMS.",
            f"THIS IS A {sys_type} SYSTEM. OWNER MUST MAINTAIN PROTECTION AGAINST FREEZING AT 40 DEGREES F MINIMUM.",
            "SYSTEMS SHALL BE HYDROSTATICALLY TESTED AT 200 PSI FOR 2 HOURS PER NFPA 13.",
            "24-HOUR SUPERVISION TO BE PROVIDED BY OTHERS.",
            "ALL ELECTRICAL, PIPE PAINTING, AND ACCESS PANELS ARE BY OTHERS.",
            "SPRINKLER HEADS AT REFLECTED CEILINGS ARE NOT INTENDED TO BE CENTERED IN ROOMS, BAYS, OR CEILING TILES OR ALIGNED WITH LIGHT FIXTURES.",
            f"ALL WELDED MAIN PIPING IS {sched}. ALL BRANCH LINE PIPING SHALL BE {sched}.",
            "ALL SCREWED PIPING IS SCHEDULE 40 WITH THREADED ENDS AND CAST IRON FITTINGS.",
            "ALL PENETRATIONS OF RATED ASSEMBLIES SHALL BE FIRE CAULKED PER UL GUIDELINES. FIRE CAULKING IS BY OTHERS.",
            "PERMIT AND PLAN CHECK FEES ARE PAID BY THE GENERAL CONTRACTOR.",
            f"BUILDING IS PROVIDED WITH A SPARE HEAD CABINET WITH A MINIMUM OF {spares} SPRINKLER HEADS AND WRENCH.",
            "LOW POINTS OF SYSTEM SHALL HAVE PROVISIONS FOR PROPER DRAINAGE. PROVIDE DRAIN HUB CONNECTED TO SANITARY SEWER. SIGN PLACARDS REQUIRED: DO NOT DRAIN FIRE SPRINKLER SYSTEM INTO STORM DRAIN. NFPA 13.",
            "A MEANS OF VENTING TRAPPED AIR SHALL BE PROVIDED PER NFPA 13 SEC. 16.7.",
            "BRANCH LINES WITH PRESSURE OVER 100 PSI SUPPLYING PENDANT SPRINKLERS: END SPRINKLER HANGER SHALL PREVENT UPWARD MOVEMENT. NFPA 13 SEC. 17.4.3.4.4.1.",
            "TESTING AND MAINTENANCE: SYSTEM SHALL BE INSPECTED, TESTED, AND MAINTAINED PER NFPA 25. NFPA 13 SEC. 32.1.",
            "GENERAL INFORMATION SIGN: CONTRACTOR SHALL PROVIDE A SIGN WITH SYSTEM DESIGN BASIS AND NFPA 25 REQUIREMENTS. NFPA 13 SEC. 29.6.1.",
        ] + (extra or [])
        nx = BORDER_X + 40; ny = BORDER_Y + 40; lh = 13; max_chars = 100
        self.msp.add_text("GENERAL NOTES:",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((nx, ny + len(notes)*lh + 20))
        for i, n in enumerate(notes):
            words = n.split(); lines2 = []; cur = ""
            for w in words:
                if len(cur)+len(w)+1 > max_chars:
                    lines2.append(cur.strip()); cur = w
                else:
                    cur += (" " if cur else "") + w
            if cur: lines2.append(cur.strip())
            ry = ny + (len(notes)-i)*lh
            for j, line in enumerate(lines2):
                prefix = str(i+1) + ". " if j == 0 else "    "
                self.msp.add_text(prefix + line,
                    dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.82,"style":FONT}
                ).set_placement((nx, ry - j*9))

    def draw_legend(self):
        lx = BORDER_X + DRAW_W - 340; ly = BORDER_Y + DRAW_H - 160
        self.msp.add_text("SYMBOL LEGEND",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((lx, ly))
        entries = [
            ("SPKR_PEND","Pendant sprinkler"),("SPKR_UPRT","Upright sprinkler"),
            ("SPKR_SIDE","Sidewall sprinkler"),("SPKR_ESFR","ESFR sprinkler"),
            ("VALV_OSY","OS&Y gate valve"),("VALV_BFV","Butterfly valve"),
            ("VALV_CV","Check valve"),("VALV_IT","Inspector's test"),
            ("FP_RISER","Riser assembly"),("FP_FDC","FDC"),
        ]
        for i, (bn, lbl) in enumerate(entries):
            ey = ly - 22 - i*20
            self.msp.add_blockref(bn, (lx+10, ey), dxfattribs={"layer":"FP-ANNO-SYMB"})
            self.msp.add_text(lbl, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
            ).set_placement((lx+26, ey-4))


# ─── Schedule renderer ────────────────────────────────────────────────────────

# ── Sprinkler model database ──────────────────────────────────────────────────
# Indexed by (manufacturer_lower, type, k_factor, temp_rating)
# Extend per project by injecting entries or overriding manufacturer.
# All values per published manufacturer catalogs (Viking, Tyco, Victaulic, etc.)
SPRINKLER_MODEL_DB = {
    # Viking
    ("viking","pendant",  5.6, 155): ("Viking","VK302","VK302 Microfast",  "Chrome",  "Quick"),
    ("viking","pendant",  5.6, 175): ("Viking","VK302","VK302 Microfast",  "Chrome",  "Quick"),
    ("viking","pendant",  5.6, 200): ("Viking","VK300","VK300 Microfast",  "Brass",   "Quick"),
    ("viking","pendant",  8.0, 155): ("Viking","VK302","VK302 K8.0",       "Chrome",  "Quick"),
    ("viking","pendant",  8.0, 200): ("Viking","VK300","VK300 K8.0",       "Brass",   "Quick"),
    ("viking","upright",  5.6, 155): ("Viking","VK300","VK300 Microfast",  "Brass",   "Quick"),
    ("viking","upright",  5.6, 175): ("Viking","VK300","VK300 Microfast",  "Brass",   "Quick"),
    ("viking","upright",  5.6, 200): ("Viking","VK300","VK300 Microfast",  "Brass",   "Quick"),
    ("viking","upright", 11.2, 155): ("Viking","VK530","VK530 K11.2",      "Brass",   "Quick"),
    ("viking","sidewall", 5.6, 155): ("Viking","VK178","VK178",            "Brass",   "Quick"),
    ("viking","sidewall", 5.6, 175): ("Viking","VK178","VK178",            "Brass",   "Quick"),
    ("viking","esfr",    14.0, 155): ("Viking","VK500","VK500 ESFR K14",   "Brass",   "Standard"),
    ("viking","esfr",    14.0, 165): ("Viking","VK500","VK500 ESFR K14",   "Brass",   "Standard"),
    ("viking","esfr",    16.8, 165): ("Viking","VK515","VK515 ESFR K16.8", "Brass",   "Standard"),
    ("viking","esfr",    25.2, 165): ("Viking","VK520","VK520 ESFR K25.2", "Brass",   "Standard"),
    ("viking","concealed",5.6, 155): ("Viking","VK462","VK462 Concealed",  "White",   "Quick"),
    # Tyco
    ("tyco","pendant",    5.6, 155): ("Tyco","TY323","TY323 Pendent",      "Chrome",  "Quick"),
    ("tyco","pendant",    5.6, 175): ("Tyco","TY323","TY323 Pendent",      "Chrome",  "Quick"),
    ("tyco","pendant",    5.6, 200): ("Tyco","TY323","TY323 Pendent",      "Brass",   "Quick"),
    ("tyco","upright",    5.6, 155): ("Tyco","TY313","TY313 Upright",      "Brass",   "Quick"),
    ("tyco","sidewall",   5.6, 155): ("Tyco","TY3251","TY3251 H/W Sidewall","Brass",  "Quick"),
    ("tyco","esfr",      14.0, 165): ("Tyco","TY7221","TY7221 ESFR K14",   "Brass",   "Standard"),
    # Central / Reliable (generic K5.6 pendant)
    ("central","pendant", 5.6, 155): ("Central","G3FR","G3FR Pendant",     "Chrome",  "Quick"),
    ("central","upright", 5.6, 155): ("Central","G3FR","G3FR Upright",     "Brass",   "Quick"),
    ("reliable","pendant",5.6, 155): ("Reliable","F1FR","F1FR Pendant",    "Chrome",  "Quick"),
    ("reliable","upright",5.6, 155): ("Reliable","F1FR","F1FR Upright",    "Brass",   "Quick"),
}

def _lookup_sprinkler(stype: str, k: float, temp: int, manufacturer: str = "Viking") -> dict:
    """
    Look up sprinkler model from the database.
    manufacturer comes from the sprinkler placement data (set per project in design engine).
    Falls back to generic description if specific model not found — does NOT invent a model.
    """
    mfr_key = manufacturer.lower().strip() if manufacturer else "viking"
    k_f     = float(k)
    temp_i  = int(temp)
    stype_l = stype.lower()

    # Exact match first
    key = (mfr_key, stype_l, k_f, temp_i)
    if key in SPRINKLER_MODEL_DB:
        mfr,sin,model,finish,resp = SPRINKLER_MODEL_DB[key]
        return {"manufacturer":mfr,"sin":sin,"model":model,"finish":finish,"response":resp}

    # Match by manufacturer + type, closest K
    candidates = [(t,k2,t3) for (m,t,k2,t3) in SPRINKLER_MODEL_DB
                  if m==mfr_key and t==stype_l]
    if candidates:
        best = min(candidates, key=lambda c: abs(c[1]-k_f))
        mfr,sin,model,finish,resp = SPRINKLER_MODEL_DB[(mfr_key,best[0],best[1],best[2])]
        return {"manufacturer":mfr,"sin":sin,"model":model,"finish":finish,"response":resp}

    # No match: return honest generic — don't fabricate a model number
    finish = "Chrome" if stype_l=="pendant" else "Brass"
    resp   = "Standard" if stype_l in ("esfr","cmsa") else "Quick"
    return {"manufacturer":manufacturer,"sin":"—","model":f"K{k_f} {stype.capitalize()}",
            "finish":finish,"response":resp}


class ScheduleRenderer:
    def __init__(self, msp):
        self.msp = msp

    def draw_sprinkler_schedule(self, spkrs, origin=(BORDER_X+20, BORDER_Y+DRAW_H-40)):
        """Professional sprinkler legend: Symbol|Mfr|SIN|Model|Qty|K|Type|Size|Response|Finish|Temp|Note"""
        from collections import Counter as _C
        ox, oy = origin; rh = 18
        # Group by (type, k, temp, manufacturer) — all come from design engine output
        groups = {}
        for s in spkrs:
            mfr = s.get("manufacturer","Viking")
            key = (s.get("type","pendant"), float(s.get("k_factor",5.6)),
                   int(s.get("temp_rating",155)), mfr)
            if key not in groups: groups[key] = {"count":0,"sample":s}
            groups[key]["count"] += 1
        hdrs = ["Symbol","Manufacturer","SIN","Model","Qty","K","Type","Size","Response","Finish","Temp","Note"]
        cws  = [32,70,45,80,28,28,50,28,52,40,40,70]
        total= sum(cws)
        self.msp.add_text("Sprinkler Legend",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((ox, oy))
        oy -= TEXT_MD + 6
        # Header
        # Header row border (no fill for white bg visibility)
        self.msp.add_lwpolyline([(ox,oy),(ox+total,oy),(ox+total,oy-rh-2),(ox,oy-rh-2),(ox,oy)],
                                dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})
        cx2 = ox
        for i,h in enumerate(hdrs):
            self.msp.add_text(h, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.8,"style":FONT_BOLD}
            ).set_placement((cx2+2, oy-rh+2))
            self.msp.add_line((cx2,oy),(cx2,oy-rh-2),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":9})
            cx2 += cws[i]
        self.msp.add_line((cx2,oy),(cx2,oy-rh-2),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":9})
        self.msp.add_line((ox,oy),(ox+total,oy),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})
        self.msp.add_line((ox,oy-rh-2),(ox+total,oy-rh-2),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":18})
        oy -= rh+2
        bmap = {"pendant":"SPKR_PEND","upright":"SPKR_UPRT","sidewall":"SPKR_SIDE","esfr":"SPKR_ESFR","concealed":"SPKR_CONC"}
        for (stype,k,temp),g in sorted(groups.items()):
            mfr_name = g["sample"].get("manufacturer","Viking")
            info = _lookup_sprinkler(stype, k, temp, mfr_name)
            sym_x = ox + cws[0]//2
            try:
                self.msp.add_blockref(bmap.get(stype,"SPKR_PEND"),(sym_x,oy-rh//2),
                    dxfattribs={"layer":"FP-SPKR-PEND","xscale":0.8,"yscale":0.8})
            except Exception: pass
            cells = ["",info["manufacturer"],info["sin"],info["model"],str(g["count"]),str(k),
                     stype.capitalize(),str(g["sample"].get("size","1/2") or "1/2"),
                     info["response"],info["finish"],f"{temp}F",""]
            cx2 = ox
            for i,c in enumerate(cells):
                if c: self.msp.add_text(c,dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.82,"style":FONT}).set_placement((cx2+2,oy-rh+3))
                self.msp.add_line((cx2,oy),(cx2,oy-rh),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":9})
                cx2 += cws[i]
            self.msp.add_line((cx2,oy),(cx2,oy-rh),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":9})
            self.msp.add_line((ox,oy-rh),(ox+total,oy-rh),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":9})
            oy -= rh
        total_qty = sum(g["count"] for g in groups.values())
        self.msp.add_text(f"Total = {total_qty}",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((ox+sum(cws[:5])-15, oy-rh+3))
        self.msp.add_line((ox,oy-rh),(ox+total,oy-rh),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":18})


    def draw_pipe_schedule(self, pipes, origin=(BORDER_X+20, BORDER_Y+220)):
        hdrs = ["TAG","TYPE","DIA (in)","SCHEDULE","MATERIAL","LENGTH (ft)","FITTINGS"]
        cw   = [40,60,50,60,60,65,115]; ox, oy = origin; total = sum(cw); rh = 16
        self.msp.add_text("PIPE SCHEDULE",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((ox, oy))
        oy -= rh + 6; cx = ox
        for i, h in enumerate(hdrs):
            self.msp.add_text(h, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((cx+2, oy-10))
            cx += cw[i]
        self.msp.add_line((ox,oy),(ox+total,oy), dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})
        ry = oy - rh
        for p in pipes[:30]:
            ry -= rh
            cells = [p.get("id",""), p.get("pipe_type","branch").capitalize(),
                     str(p.get("diameter","")), p.get("schedule","Sch 40"),
                     p.get("material","Steel"), f'{p.get("length",0):.1f}',
                     ", ".join(p.get("fittings",[])[:4])]
            cx = ox
            for i, c in enumerate(cells):
                self.msp.add_text(c, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}).set_placement((cx+2, ry+4))
                cx += cw[i]
            self.msp.add_line((ox,ry),(ox+total,ry), dxfattribs={"layer":"FP-ANNO-NOTE"})


# ─── Helper: add annotated riser component ───────────────────────────────────

def _riser_component(msp, cx, y, label, size_label="", desc="",
                     block_name=None, line_left=True, leader_len=300):
    """Draw one component on the riser with a callout leader line."""
    # Block symbol (valve, etc.)
    if block_name:
        msp.add_blockref(block_name, (cx, y), dxfattribs={"layer":"FP-VALV","xscale":1.5,"yscale":1.5})

    # Horizontal leader to annotation
    lx = cx + leader_len
    msp.add_line((cx+12, y), (lx, y), dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":13})
    msp.add_line((lx, y), (lx+20, y), dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":13})

    # Callout text — label on top, description below
    tx = lx + 24
    if label:
        msp.add_text(label, dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((tx, y+4))
    if size_label:
        msp.add_text(size_label, dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT}
        ).set_placement((tx, y-14))
    if desc:
        msp.add_text(desc, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
        ).set_placement((tx, y-28))


# ─── Main engine ──────────────────────────────────────────────────────────────

class FireAIDrawingEngine:
    def __init__(self, project, cad_output, hydraulics_output,
                 bracing_output, compliance_result, extra_notes=None):
        self.project    = project
        self.cad        = cad_output   or {}
        self.hydraulics = hydraulics_output or {}
        self.bracing    = bracing_output    or {}
        self.compliance = compliance_result
        self.notes      = extra_notes or []
        self.revisions  = project.get("revisions", [])
        self.issue_date = project.get("issue_date", datetime.utcnow().strftime("%m/%d/%Y"))

    def _meta(self, title, code, scale='1/8" = 1\'-0"'):
        return SheetMeta(sheet_title=title, sheet_number=code, scale=scale,
                         issue_date=self.issue_date, revisions=self.revisions)

    def _new_sheet(self, title, code, scale='1/8" = 1\'-0"'):
        doc  = DXFDocFactory.new_doc()
        SymbolLibrary.define_all(doc)
        msp  = doc.modelspace()
        meta = self._meta(title, code, scale)
        msp.add_lwpolyline(
            [(MARGIN,MARGIN),(SHEET_W-MARGIN,MARGIN),
             (SHEET_W-MARGIN,SHEET_H-MARGIN),(MARGIN,SHEET_H-MARGIN),(MARGIN,MARGIN)],
            dxfattribs={"layer":"FP-TBLK","lineweight":50})
        TitleblockRenderer(doc, self.project).render(msp, meta)
        return doc, msp, meta

    # ── Details panel (right side of floor plan sheet) ──────────────────────

    def _draw_details_panel(self, msp, p):
        """
        Draw the right-side details panel matching AutoSprink/Battalion One quality.
        Includes: symbols legend, unsupported lengths table, max hanger spacing table,
        bracing criteria table, and NFPA 13 code excerpts.
        """
        px  = DTL_PANEL_X
        pw  = DTL_PANEL_W - 20
        py  = BORDER_Y + DRAW_H - 320   # start below north arrow and symbol legend
        lh  = 16   # line height
        col = 8    # color (gray) for tables

        def t(text, x, y, ht=TEXT_SM, bold=False, color=None):
            att = {"layer":"FP-ANNO-NOTE","height":ht,"style":FONT_BOLD if bold else FONT}
            if color: att["color"] = color
            msp.add_text(str(text),dxfattribs=att).set_placement((x,y))

        def hline(y, lw=13):
            msp.add_line((px,y),(px+pw,y),dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":lw})

        def box(x,y,w,h,lw=13):
            msp.add_lwpolyline([(x,y),(x+w,y),(x+w,y-h),(x,y-h),(x,y)],
                               dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":lw})

        def section_title(title, y):
            box(px, y, pw, lh+6, lw=25)
            msp.add_solid([(px,y),(px+pw,y),(px+pw,y-lh-6),(px,y-lh-6)],
                          dxfattribs={"layer":"FP-TBLK","color":8})
            t(title, px+4, y-lh-1, TEXT_SM, bold=True)
            return y - lh - 10

        # ── Unsupported Armover Lengths ───────────────────────────────────────
        py = section_title("UNSUPPORTED ARMOVER LENGTHS", py)
        for line in [
            "The cumulative horizontal length of an unsupported armover to a sprinkler,",
            "drop nipple, or sprig shall not exceed 24 in. (600 mm) for steel pipe or",
            "12 in. (300 mm) for copper tube. [NFPA 13 §9.1.3]",
        ]:
            t(line, px+2, py, TEXT_SM*0.8); py -= lh*0.9
        py -= 4

        py = section_title("UNSUPPORTED ARMOVER LENGTHS >100-PSI", py)
        for line in [
            "Where max static or flowing pressure at the sprinkler exceeds 100 psi",
            "and a branch line above a ceiling supplies sprinklers in pendant position,",
            "cumulative horizontal armover length shall not exceed 12 in. steel / 6 in. copper.",
        ]:
            t(line, px+2, py, TEXT_SM*0.8); py -= lh*0.9
        py -= 4

        py = section_title("UNSUPPORTED LENGTHS", py)
        for line in [
            "For steel pipe, the unsupported horizontal length between the end sprinkler and",
            "the last hanger: 36in for 1in, 48in for 1-1/4in, 60in for 1-1/2in or larger pipe.",
        ]:
            t(line, px+2, py, TEXT_SM*0.8); py -= lh*0.9
        py -= 4

        # ── Max Hanger Spacing table ──────────────────────────────────────────
        py = section_title("MAXIMUM DISTANCE BETWEEN HANGERS", py)
        col_w = pw // 13
        sizes = ["3/4","1","1-1/4","1-1/2","2","2-1/2","3","3-1/2","4","5","6","8"]
        steel = ["N/A","12-0","12-0","15-0","15-0","15-0","15-0","15-0","15-0","15-0","15-0","15-0"]
        cpvc  = ["5-6", "6-0","8-0","10-0","N/A","N/A","N/A","N/A","N/A","N/A","N/A","N/A"]
        # Header row
        t("NOMINAL PIPE SIZE", px+2, py, TEXT_SM*0.75, bold=True); py -= lh
        hline(py)
        t("STEEL PIPE", px+2, py-lh+2, TEXT_SM*0.75, bold=True)
        for i, (s, v) in enumerate(zip(sizes, steel)):
            cx2 = px + (i+1)*col_w
            t(s, cx2, py, TEXT_SM*0.7)
            t(v, cx2, py-lh, TEXT_SM*0.7)
        py -= lh*2; hline(py)
        t("CPVC", px+2, py-lh+2, TEXT_SM*0.75, bold=True)
        for i, v in enumerate(cpvc):
            t(v, px+(i+1)*col_w, py-lh, TEXT_SM*0.7)
        py -= lh*2; hline(py); py -= 8

        # ── Bracing Criteria ─────────────────────────────────────────────────
        py = section_title("BRACING CRITERIA", py)
        brace_rows = [
            ("LAT4",   "LATERALS: 12-0 MAX."),
            ("LAT4-A", "LATERALS: 12-0 MAX."),
            ("LAT4-B", "LATERALS: 25-0 MAX."),
            ("LAT3-A", "LATERALS: 12-0 MAX."),
            ("LNG4",   "LONGITUDINAL: 60-0 MAX."),
            ("LNG4-A", "LONGITUDINAL: 30-0 MAX."),
            ("LNG4-B", "LONGITUDINAL: 40-0 MAX."),
            ("LNG3-A", "LONGITUDINAL: 30-0 MAX."),
        ]
        for code, desc in brace_rows:
            t(code, px+2, py, TEXT_SM*0.8, bold=True)
            t("—  "+desc, px+60, py, TEXT_SM*0.8)
            py -= lh
        py -= 4

        # ── Symbols Legend ────────────────────────────────────────────────────
        py = section_title("SYMBOLS LEGEND", py)
        legends = [
            ("★ 0'-0 FF↑", "PIPE ELEVATION"),
            ("——",          "GROOVED COUPLING"),
            ("—⊣—",         "FLEXIBLE COUPLING"),
            ("⊠",           "GATE VALVE"),
            ("⊡",           "BUTTERFLY VALVE"),
            ("◁",           "CHECK VALVE"),
            ("⊢⊣",          "END OF LINE RESTRAINT"),
            ("LAT/LNG→",    "SWAY BRACE"),
            ("+",           "4-WAY BRACE"),
        ]
        for sym, desc in legends:
            t(sym,  px+2,  py, TEXT_SM*0.85, bold=True, color=colors.GREEN)
            t(desc, px+70, py, TEXT_SM*0.85)
            py -= lh
        py -= 8

        # ── NFPA 13 Code Notes ────────────────────────────────────────────────
        py = section_title("NFPA 13 CODE NOTES", py)
        notes = [
            "1. All work per NFPA 13 current edition.",
            "2. Contractor shall field-verify all dimensions prior to fabrication.",
            "3. All pipe shall be schedule 40/10 steel unless noted.",
            "4. All hangers and braces shall be FM/UL listed.",
            "5. Provide inspector's test per §8.17 at most remote location.",
            "6. Hydraulic design information sign required per §27.2.",
            "7. Seismic bracing per §9.3 — zone "+str(p.get("seismic_zone","D1"))+".",
            "8. All penetrations through fire-rated assemblies shall be fire-stopped.",
        ]
        for n in notes:
            if py < BORDER_Y + 40: break
            t(n, px+2, py, TEXT_SM*0.78); py -= lh*0.9

        # Vertical separator line between floor plan and details panel
        msp.add_line((DTL_PANEL_X-10, BORDER_Y), (DTL_PANEL_X-10, BORDER_Y+DRAW_H),
                     dxfattribs={"layer":"FP-TBLK","lineweight":18})


    # ── FP0.0 Cover ───────────────────────────────────────────────────────────

    def _build_cover(self):
        doc, msp, meta = self._new_sheet("Cover Sheet","FP0.0", scale="N/A")
        p   = self.project
        ra  = self.hydraulics.get("remote_area_calcs", {})

        def T(text, x, y, ht=TEXT_SM, bold=False, color=None):
            att = {"layer":"FP-ANNO-NOTE","height":ht,
                   "style":FONT_BOLD if bold else FONT}
            if color: att["color"] = color
            msp.add_text(str(text), dxfattribs=att).set_placement((x,y))

        def box(x,y,w,h,lw=18):
            msp.add_lwpolyline([(x,y),(x+w,y),(x+w,y-h),(x,y-h),(x,y)],
                               dxfattribs={"layer":"FP-TBLK","lineweight":lw})

        def sect(title, x, y, w):
            box(x,y,w,TEXT_MD+8,lw=25)
            T(title, x+4, y-TEXT_MD-2, TEXT_MD, bold=True)
            return y - TEXT_MD - 12

        # ── Sheet title ──────────────────────────────────────────────────────
        cx = SHEET_W//2; cy = BORDER_Y + DRAW_H - 120
        T("FIRE PROTECTION SYSTEM", cx, cy, TEXT_XL, bold=True)
        T("AUTOMATIC SPRINKLER DESIGN", cx, cy-50, TEXT_LG, bold=True)
        T(p.get("project_name",""), cx, cy-90, TEXT_LG)
        T(p.get("location",""), cx, cy-120, TEXT_MD)

        # ── Left column: Sheet index + General Notes ─────────────────────────
        lx = BORDER_X + 40; lw = int(DRAW_W * 0.30); ly = cy - 180

        # Sheet Index
        ly = sect("SHEET INDEX:", lx, ly, lw)
        for num, title in [
            ("FP0.0","Cover Sheet & General Notes"),
            ("FP1.x","Fire Sprinkler Piping Plan"),
            ("FP2.0","Riser Diagram"),
            ("FP3.0","Hydraulic Calculations"),
            ("FP4.0","Sprinkler & Pipe Schedules"),
            ("FP5.0","Installation Details"),
            ("FP6.0","Bill of Materials"),
            ("FP7.0","Isometric View"),
        ]:
            T(f"{num}   {title}", lx+4, ly, TEXT_SM)
            ly -= 16

        # Applicable codes
        ly -= 10
        ly = sect("APPLICABLE CODES:", lx, ly, lw)
        ibc = p.get("ibc_year","2021")
        cbc = p.get("cbc_year","2022")
        for code in [
            f"NFPA 13 ({p.get('nfpa_edition','CURRENT')}) — INSTALLATION OF SPRINKLER SYSTEMS",
            f"{ibc} INTERNATIONAL BUILDING CODE (IBC)",
            f"2022 CALIFORNIA BUILDING CODE (CBC) — WHERE APPLICABLE",
            f"2022 CALIFORNIA FIRE CODE (CFC) — WHERE APPLICABLE",
            f"LOCAL AHJ: {p.get('ahj_jurisdiction','')}",
        ]:
            T(code, lx+4, ly, TEXT_SM*0.85)
            ly -= 14

        # General notes (using PlanViewRenderer to access the method)
        ly -= 10
        _r = PlanViewRenderer(msp, p, scale=8)
        _r.draw_general_notes()

        # ── Center column: Project data + Sprinkler spec ─────────────────────
        mx = BORDER_X + lw + 60; mw = int(DRAW_W * 0.28); my = cy - 180

        my = sect("PROJECT DATA:", mx, my, mw)
        occ_class = p.get("occupancy","")
        rows_pd = [
            ("PROJECT NAME",    p.get("project_name","")),
            ("ADDRESS",         p.get("location","")),
            ("OWNER",           p.get("owner","")),
            ("OCCUPANCY CLASS", occ_class),
            ("CONSTR. TYPE",    p.get("construction_type","")),
            ("SYSTEM TYPE",     p.get("system_type","Wet Pipe")),
            ("PIPE MATERIAL",   p.get("pipe_material","")),
            ("SEISMIC ZONE",    str(p.get("seismic_zone",""))),
            ("DESIGNER",        (p.get("designer",{}) or {}).get("name","")
                                 if isinstance(p.get("designer"),dict)
                                 else str(p.get("designer",""))),
            ("CERT./LICENSE",   (p.get("designer",{}) or {}).get("cert","")
                                 if isinstance(p.get("designer"),dict) else ""),
            ("COMPANY",         p.get("company_name","")),
            ("DATE",            p.get("issue_date","")),
            ("DSA TRACKING",    p.get("dsa_tracking_no","")),
            ("CONTRACT NO.",    p.get("contract_no","")),
        ]
        mid = mx + int(mw*0.45)
        for label, val in rows_pd:
            if not str(val).strip(): continue
            T(label+":", mx+4, my, TEXT_SM*0.85, bold=True)
            T(str(val)[:35], mid, my, TEXT_SM*0.85)
            msp.add_line((mx, my-3),(mx+mw, my-3),
                         dxfattribs={"layer":"FP-TBLK","lineweight":9})
            my -= 15

        # Sprinkler types specified
        my -= 10
        my = sect("SPRINKLER SPECIFICATIONS:", mx, my, mw)
        sp = self.cad.get("sprinkler_placements",[])
        from collections import Counter as _C2
        sp_groups = {}
        for s in sp:
            k = (s.get("manufacturer","Viking"),
                 s.get("type","pendant"),
                 float(s.get("k_factor",5.6)),
                 int(s.get("temp_rating",155)))
            sp_groups[k] = sp_groups.get(k,0) + 1
        for (mfr,typ,k,temp),qty in sorted(sp_groups.items()):
            T(f"{qty} — {mfr} K{k} {typ.capitalize()} {temp}F",
              mx+4, my, TEXT_SM*0.85)
            my -= 14

        # ── Right column: Spare head cabinet + Seismic table ─────────────────
        rx = BORDER_X + lw + mw + 80; rw = int(DRAW_W * 0.28); ry = cy - 180

        ry = sect("SPARE SPRINKLER CABINET:", rx, ry, rw)
        T("FOREMAN'S CABINET SHALL CONTAIN:", rx+4, ry, TEXT_SM*0.85, bold=True); ry -= 15
        T("SPRINKLERS IN SYSTEM  |  # OF SPARE SPRINKLERS", rx+4, ry, TEXT_SM*0.82, bold=True); ry -= 13
        for range_str, spare in [("0 — 300","6"),("301 — 1000","12"),("MORE THAN 1000","24")]:
            msp.add_line((rx,ry-2),(rx+rw,ry-2),dxfattribs={"layer":"FP-TBLK","lineweight":9})
            T(range_str, rx+4, ry, TEXT_SM*0.85)
            T(spare, rx+int(rw*0.65), ry, TEXT_SM*0.85, bold=True)
            ry -= 13
        box(rx, cy-180, rw, cy-180-ry+10)

        # Seismic coefficient table (NFPA 13 §9.3.5)
        ry -= 20
        ry = sect("SEISMIC COEFFICIENT Cp (NFPA 13 TABLE 9.3.5.1):", rx, ry, rw)
        T("PIPE SIZE", rx+4, ry, TEXT_SM*0.82, bold=True)
        sizes_col = ["3/4","1","1-1/4","1-1/2","2","2-1/2","3","4","5","6"]
        # Cp values per NFPA 13 Table 9.3.5.1 for Zone D (0.5<Cp≤0.71 @ 1.40)
        cp_map = {
            "3/4":"—",  "1":"—",    "1-1/4":"—",  "1-1/2":"—",
            "2":"0.50", "2-1/2":"0.50","3":"0.71","4":"0.71",
            "5":"0.71", "6":"1.40",
        }
        col_w2 = rw // (len(sizes_col)+1)
        for i, sz in enumerate(sizes_col):
            T(sz, rx+4+(i+1)*col_w2, ry, TEXT_SM*0.7)
        msp.add_line((rx,ry-4),(rx+rw,ry-4),dxfattribs={"layer":"FP-TBLK","lineweight":9})
        ry -= 14
        T("Cp", rx+4, ry, TEXT_SM*0.82, bold=True)
        for i, sz in enumerate(sizes_col):
            T(cp_map.get(sz,"—"), rx+4+(i+1)*col_w2, ry, TEXT_SM*0.7)
        ry -= 14
        box(rx, cy-180, rw, cy-180-ry+24)

        return doc

    def _build_cover_OLD_DELETE(self):  # old version archived
        pass

        cx = SHEET_W / 2; cy = BORDER_Y + DRAW_H / 2
        for txt, y, sz, b in [
            ("FIRE PROTECTION SYSTEM",        cy+200, TEXT_XL*2, True),
            ("AUTOMATIC SPRINKLER DESIGN",    cy+140, TEXT_XL,   True),
            (self.project.get("project_name",""), cy+80, TEXT_LG, False),
            (self.project.get("location",""),    cy+40, TEXT_MD, False),
        ]:
            msp.add_text(str(txt), dxfattribs={"layer":"FP-ANNO-NOTE","height":sz,
                "style":FONT_BOLD if b else FONT}
            ).set_placement((cx, y))

        ix = BORDER_X + 40; iy = BORDER_Y + DRAW_H - 60
        msp.add_text("SHEET INDEX", dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((ix, iy))
        for i, (num, t) in enumerate([
            ("FP0.0","Cover Sheet"),("FP1.x","Floor Plan(s)"),
            ("FP2.0","Riser Diagram"),("FP3.0","Hydraulic Calculations"),
            ("FP4.0","Schedules"),("FP5.0","Installation Details"),("FP6.0","Bill of Materials"),
        ]):
            msp.add_text(f"{num}  {t}", dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
            ).set_placement((ix, iy-20-i*18))

        cx2 = cx + 100
        msp.add_text("CODE COMPLIANCE", dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((cx2, iy))
        p = self.project
        for i, c in enumerate([
            "NFPA 13 — Current Edition",
            f"IBC {p.get('ibc_year','2021')}",
            f"AHJ: {p.get('ahj_jurisdiction','')}",
            f"Occupancy: {p.get('occupancy','')}",
            f"System: {p.get('system_type','Wet').upper()}",
            f"Seismic: {p.get('seismic_zone','')}",
            f"Pipe: {p.get('pipe_material','Steel')}",
            "Design: Density/Area §22",
        ]):
            msp.add_text(c, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
            ).set_placement((cx2, iy-20-i*18))
        return doc

    # ── FP1.x Floor plan ──────────────────────────────────────────────────────

    def _build_floor_plan(self, floor_num=1):
        doc, msp, meta = self._new_sheet(f"Floor Plan — Level {floor_num}", f"FP1.{floor_num}")
        meta_dm      = self.cad.get("design_metadata", {})
        cad          = self.cad   # shorthand
        bw_ft        = float(meta_dm.get("building_w_ft") or
                             (self.project.get("total_area",10000) / 0.65) ** 0.5)
        bd_ft        = float(meta_dm.get("building_d_ft") or bw_ft * 0.65)

        # Dynamic scale: fit building within floor plan panel with margins
        margin_du = 100
        scale_x   = (FP_PANEL_W - margin_du*2) / max(bw_ft, 1)
        scale_y   = (DRAW_H     - margin_du*2) / max(bd_ft, 1)
        fp_scale  = min(scale_x, scale_y)
        # Snap to nearest standard below computed value
        STD_SCALES = [4,6,8,12,16,20,24,32,48,64,96,128,192]
        fp_scale   = max((s for s in STD_SCALES if s <= fp_scale), default=4)
        scale_str  = _scale_annotation(fp_scale)

        r = PlanViewRenderer(msp, self.project, scale=fp_scale)

        # Identify hydraulic remote heads for HR marker
        ra_calcs   = self.hydraulics.get("remote_area_calcs", {})
        node_calcs = ra_calcs.get("node_calculations", [])
        remote_ids = {n.get("node","") for n in node_calcs}

        # 1. Grid bubbles (architectural background layer)
        # Use actual structural grid from document intelligence if available
        structural_grid = cad.get("structural_grid") or cad.get("design_metadata",{}).get("structural_grid")
        r.draw_grid(bw_ft, bd_ft, structural_grid=structural_grid)

        # 2. Architectural background: walls, columns, rooms + dimensions
        if self.cad.get("walls"):   r.draw_walls(self.cad["walls"])
        r.draw_wall_dimensions(self.cad.get("walls",[]), bw_ft, bd_ft)
        if self.cad.get("columns"): r.draw_columns(self.cad["columns"])
        if self.cad.get("rooms"):   r.draw_rooms(self.cad["rooms"])

        # 3. Fire protection: pipes (with size + elevation labels)
        r.draw_pipes(self.cad.get("pipe_sections", []))

        # 4. Arm-over dimension strings
        r.draw_head_dimensions(self.cad.get("sprinkler_placements", []))

        # 5. Sway braces + end-of-line restraints
        r.draw_sway_braces(self.cad.get("sway_braces") or
                           self.bracing.get("sway_braces", []))
        r.draw_end_of_line_restraints(self.cad.get("pipe_sections", []))

        # 6. Sprinkler heads — NO coverage circles
        r.draw_sprinklers(
            self.cad.get("sprinkler_placements", []),
            show_coverage=False,
            hydraulic_remote_ids=remote_ids,
        )
        r.draw_valves(self.cad.get("valves", []))
        r.draw_equipment(self.cad.get("equipment", []))

        # 7. Annotations
        r.draw_north_arrow(self.project.get("north_rotation", 0))
        r.draw_hanging_notes(self.project, self.cad.get("pipe_sections",[]))
        r.draw_scale_bar(scale_str)
        r.draw_design_params_block(self.hydraulics, self.project)

        # 8. Sheet title at bottom of floor plan area
        msp.add_text(
            "FIRE SPRINKLER PIPING PLAN",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_LG,"style":FONT_BOLD}
        ).set_placement((BORDER_X + FP_PANEL_W//2, BORDER_Y + 30),
                        align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text(
            meta.scale,
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
        ).set_placement((BORDER_X + FP_PANEL_W//2, BORDER_Y + 16),
                        align=TextEntityAlignment.MIDDLE_CENTER)

        # 9. Hanger designations on plan + legend
        all_hangers = (self.cad.get("hangers") or
                       self.bracing.get("hanger_schedule") or [])
        r.draw_hangers(all_hangers)
        r.draw_hanger_legend(all_hangers)

        # 10. Hydraulic information block (top-left, per remote area)
        r.draw_hydraulic_info_block(self.hydraulics, self.project)

        # 11. Details panel (right 28% of sheet)
        self._draw_details_panel(msp, self.project)

        return doc

    # ── FP2.0 Riser diagram — COMPLETE REWRITE ────────────────────────────────

    def _build_riser(self):
        """
        FP2.0 — Riser Diagram.
        Matches Battalion One quality:
        - AFF dimension labels at every component
        - Color-coded components (orange flex couplings, blue valve bodies)
        - Leader lines with precise callout text
        - Test & drain, head box, electric bell
        - System data block on left side
        """
        doc, msp, meta = self._new_sheet(
            "Riser Diagram — NFPA 13 §23", "FP2.0", scale="1/2\"=1'-0\"")
        p   = self.project
        h   = self.hydraulics

        # ── Layout ───────────────────────────────────────────────────────────
        cx     = BORDER_X + int(DRAW_W * 0.40)   # riser CL
        y_grd  = BORDER_Y + 80                    # grade/floor level
        ch     = float(p.get("ceiling_height", 12))
        # Auto-scale riser to fill ~80% of available sheet height
        # Riser spans from 0 (floor) to ch+2 ft (just above ceiling)
        avail_h  = DRAW_H - 200   # leave margin top + bottom
        RIS_SCALE = max(36, int(avail_h / (ch + 3)))
        def ry(ft_aff): return y_grd + int(ft_aff * RIS_SCALE)

        riser_w = 22    # riser pipe half-width
        riser_d = str(p.get("riser_diameter","4"))
        mat     = p.get("pipe_material","Schedule 40 Steel")
        sched   = "SCH. 10" if "10" in mat.lower() else "SCH. 40"
        leader_x= cx + riser_w + 200   # callout leader endpoint X

        def leader(y, text, sub="", color=colors.WHITE):
            """Draw leader line from riser to callout text."""
            msp.add_line((cx+riser_w, y),(leader_x, y),
                         dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":13})
            msp.add_line((leader_x, y),(leader_x+15, y),
                         dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":13})
            msp.add_text("◄  "+text,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,
                            "style":FONT_BOLD,"color":color}
            ).set_placement((leader_x+20, y+3))
            if sub:
                msp.add_text("      "+sub,
                    dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.8,"style":FONT}
                ).set_placement((leader_x+20, y-12))

        def aff_label(y, ft_aff):
            """Blue AFF dimension label left of riser."""
            ft_i = int(ft_aff); in_i = int(round((ft_aff-ft_i)*12))
            label = "%d'-%02d\" A.F.F." % (ft_i, in_i) if in_i else "%d'-0\" A.F.F." % ft_i
            msp.add_line((cx-riser_w-50, y),(cx-riser_w, y),
                         dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9,"color":colors.BLUE})
            msp.add_text(label,
                dxfattribs={"layer":"FP-ANNO-DIMS","height":TEXT_SM*0.85,
                            "style":FONT,"color":colors.BLUE}
            ).set_placement((cx-riser_w-55, y+4),
                            align=TextEntityAlignment.RIGHT)

        def dim_between(y0, y1, label):
            """Vertical dimension string between two components."""
            dx = cx - riser_w - 80
            msp.add_line((dx,y0),(dx,y1),dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
            msp.add_line((dx-6,y0),(dx+6,y0),dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
            msp.add_line((dx-6,y1),(dx+6,y1),dxfattribs={"layer":"FP-ANNO-DIMS","lineweight":9})
            mid_y = (y0+y1)//2
            msp.add_text(label,
                dxfattribs={"layer":"FP-ANNO-DIMS","height":TEXT_SM*0.8,
                            "style":FONT,"rotation":90,"color":colors.BLUE}
            ).set_placement((dx-10, mid_y), align=TextEntityAlignment.MIDDLE_CENTER)

        # ── IBR stub below floor slab ─────────────────────────────────────────
        ibr_y = y_grd - 30
        msp.add_line((cx-riser_w//2, ibr_y),(cx-riser_w//2, y_grd),
                     dxfattribs={"layer":"A-WALL-FULL","color":8,"lineweight":35})
        msp.add_line((cx+riser_w//2, ibr_y),(cx+riser_w//2, y_grd),
                     dxfattribs={"layer":"A-WALL-FULL","color":8,"lineweight":35})
        leader(ibr_y+8, '6" IBR — IN BUILDING RISER (BY OTHERS)', color=8)

        # ── Floor slab ────────────────────────────────────────────────────────
        msp.add_solid([(cx-200,y_grd),(cx+200,y_grd),(cx+200,y_grd+12),(cx-200,y_grd+12)],
                      dxfattribs={"layer":"A-SLAB","color":8})
        msp.add_text("FINISHED FLOOR",
            dxfattribs={"layer":"A-ROOM-IDEN","height":TEXT_SM*0.8,"style":FONT}
        ).set_placement((cx-200, y_grd+16))

        # ── Component heights (ft AFF) ────────────────────────────────────────
        # Per typical riser assembly — actual values vary by project/AHJ
        H_FLANGE    = 0.50   # 6"x4" reducer flange
        H_FLEX_BOT  = 1.0    # bottom flexible coupling
        H_BFV       = 2.33   # butterfly valve w/ tamper (2'-4")
        H_CHECK     = 3.00   # check valve
        H_FLOWSWITCH= 3.5    # flow switch
        H_HEAD_BOX  = 4.00   # head box / pressure gauge
        H_DRAIN     = 4.50   # 2" main drain
        H_TEST_DRAIN= 5.00   # test & drain
        H_FLEX_TOP  = ch - 0.5   # top flexible coupling (within 24" of top)
        H_BRACE_TOP = ch - 0.17  # 4-way seismic brace (24" max from top)
        H_BRANCH    = ch + 0.0   # riser top / branch connection

        # ── Riser pipe ────────────────────────────────────────────────────────
        y_riser_bot = ry(0); y_riser_top = ry(H_BRANCH + 0.5)
        msp.add_line((cx-riser_w//2, y_riser_bot),(cx-riser_w//2, y_riser_top),
                     dxfattribs={"layer":"FP-PIPE-MAIN","lineweight":50})
        msp.add_line((cx+riser_w//2, y_riser_bot),(cx+riser_w//2, y_riser_top),
                     dxfattribs={"layer":"FP-PIPE-MAIN","lineweight":50})
        # Riser label
        msp.add_text(f'{riser_d}" {sched} RISER',
            dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,
                        "style":FONT_BOLD,"rotation":90}
        ).set_placement((cx-riser_w//2-16, (y_riser_bot+y_riser_top)//2),
                        align=TextEntityAlignment.MIDDLE_CENTER)

        # ── Individual components ─────────────────────────────────────────────
        def valve_rect(y, h=24, w=30, color=colors.GREEN):
            """Draw a simple valve rectangle symbol."""
            msp.add_solid([(cx-w//2,y-h//2),(cx+w//2,y-h//2),
                           (cx+w//2,y+h//2),(cx-w//2,y+h//2)],
                          dxfattribs={"layer":"FP-VALV","color":color})
            msp.add_lwpolyline([(cx-w//2,y-h//2),(cx+w//2,y-h//2),
                                (cx+w//2,y+h//2),(cx-w//2,y+h//2),(cx-w//2,y-h//2)],
                               dxfattribs={"layer":"FP-VALV","lineweight":18})

        def coupling(y, color=30):
            """Orange grooved coupling band."""
            msp.add_solid([(cx-riser_w//2-4,y-4),(cx+riser_w//2+4,y-4),
                           (cx+riser_w//2+4,y+4),(cx-riser_w//2-4,y+4)],
                          dxfattribs={"layer":"FP-VALV","color":color})

        # 6"x4" Flange
        yc = ry(H_FLANGE); coupling(yc)
        leader(yc, f'{riser_d}" FLEXIBLE COUPLING (24" MAX. A.F.F.)',
               f'6"x4" FLANGE BELOW', color=30)
        aff_label(yc, H_FLANGE)

        # Butterfly valve w/ tamper
        yc = ry(H_BFV); valve_rect(yc, h=28, w=35, color=colors.BLUE)
        leader(yc, f'{riser_d}" BUTTERFLY VALVE W/ TAMPER', color=colors.BLUE)
        aff_label(yc, H_BFV)
        dim_between(ry(H_FLANGE), ry(H_BFV), f"{H_BFV-H_FLANGE:.1f}'")

        # Check valve
        yc = ry(H_CHECK); valve_rect(yc, h=22, w=30, color=colors.RED)
        leader(yc, f'{riser_d}" CHECK VALVE')
        aff_label(yc, H_CHECK)
        dim_between(ry(H_BFV), ry(H_CHECK), f"{H_CHECK-H_BFV:.1f}'")

        # Flow switch
        yc = ry(H_FLOWSWITCH)
        msp.add_circle((cx, yc), 10, dxfattribs={"layer":"FP-VALV","color":colors.CYAN})
        leader(yc, "FLOW SWITCH", color=colors.CYAN)
        aff_label(yc, H_FLOWSWITCH)

        # Head box + pressure gauge
        yc = ry(H_HEAD_BOX)
        msp.add_lwpolyline([(cx+riser_w,yc-10),(cx+riser_w+30,yc-10),
                            (cx+riser_w+30,yc+10),(cx+riser_w,yc+10)],
                           dxfattribs={"layer":"FP-VALV"})
        leader(yc+10, "HEAD BOX", "2 INCH SCHEDULE 40")
        aff_label(yc, H_HEAD_BOX)

        # 2" main drain
        yc = ry(H_DRAIN)
        msp.add_line((cx-riser_w//2-60, yc),(cx-riser_w//2, yc),
                     dxfattribs={"layer":"FP-PIPE-MAIN","lineweight":18})
        msp.add_text("2\" MAIN DRAIN",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
        ).set_placement((cx-riser_w//2-65, yc+4), align=TextEntityAlignment.RIGHT)
        aff_label(yc, H_DRAIN)

        # Test & drain
        yc = ry(H_TEST_DRAIN)
        msp.add_line((cx-riser_w//2-80, yc),(cx-riser_w//2, yc),
                     dxfattribs={"layer":"FP-PIPE-MAIN","lineweight":18})
        msp.add_text("2\" TEST-AND-DRAIN",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT_BOLD}
        ).set_placement((cx-riser_w//2-85, yc+5), align=TextEntityAlignment.RIGHT)
        msp.add_text("MODEL 1011A",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.75,"style":FONT}
        ).set_placement((cx-riser_w//2-85, yc-8), align=TextEntityAlignment.RIGHT)
        aff_label(yc, H_TEST_DRAIN)

        # Top flexible coupling
        yc = ry(H_FLEX_TOP); coupling(yc)
        leader(yc, f'4" FLEXIBLE COUPLING', f'WITHIN 24" OF TOP OF RISER', color=30)
        aff_label(yc, H_FLEX_TOP)
        dim_between(ry(H_TEST_DRAIN), yc, f"{H_FLEX_TOP-H_TEST_DRAIN:.1f}'")

        # 4-way seismic brace marker
        yc = ry(H_BRACE_TOP)
        s  = 12
        msp.add_line((cx-s,yc-s),(cx+s,yc+s),dxfattribs={"layer":"FP-HNGR","color":colors.YELLOW,"lineweight":25})
        msp.add_line((cx+s,yc-s),(cx-s,yc+s),dxfattribs={"layer":"FP-HNGR","color":colors.YELLOW,"lineweight":25})
        msp.add_text("4-WAY SEISMIC BRACE",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT_BOLD,"color":colors.YELLOW}
        ).set_placement((cx-riser_w//2-85, yc+5), align=TextEntityAlignment.RIGHT)
        msp.add_text("SHALL BE LOCATED 24\" MAX. FROM TOP OF RISER",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.75,"style":FONT,"color":colors.YELLOW}
        ).set_placement((cx-riser_w//2-85, yc-8), align=TextEntityAlignment.RIGHT)

        # Branch stub at ceiling level
        yc = ry(H_BRANCH)
        msp.add_line((cx-riser_w//2, yc),(cx-200, yc),
                     dxfattribs={"layer":"FP-PIPE-XMAIN","lineweight":35})
        leader(yc, str(p.get("riser_diameter","4")) + '" FEED MAIN',
               "%.0f GPM @ %.1f PSI" % (h.get("flow_demand",0), h.get("required_pressure",0)))
        aff_label(yc, H_BRANCH)
        dim_between(ry(H_FLEX_TOP), yc,
                    f"{H_BRANCH-H_FLEX_TOP:.1f}'")

        # 10" Electric bell (exterior)
        bell_y = ry(ch + 0.5)
        msp.add_circle((cx+100, bell_y), 18,
                       dxfattribs={"layer":"FP-VALV","color":colors.YELLOW,"lineweight":18})
        msp.add_text('10" ELEC. BELL',
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT_BOLD}
        ).set_placement((cx+125, bell_y+4))
        msp.add_text("MOUNT TO EXTERIOR",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.75,"style":FONT}
        ).set_placement((cx+125, bell_y-10))

        # ── Section callout bubble ────────────────────────────────────────────
        bub_x = BORDER_X + 100; bub_y = BORDER_Y + 30
        msp.add_circle((bub_x, bub_y), 18,
                       dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":25})
        msp.add_line((bub_x-18,bub_y),(bub_x+18,bub_y),
                     dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":18})
        msp.add_text("B",
            dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((bub_x, bub_y+4), align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text("FP2",
            dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM*0.75,"style":FONT}
        ).set_placement((bub_x, bub_y-12), align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text('RISER DETAIL',
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((bub_x+30, bub_y+6))
        msp.add_text("SCALE: 1/2 inch = 1 foot",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
        ).set_placement((bub_x+30, bub_y-10))

        # ── System information block ──────────────────────────────────────────
        bx = BORDER_X + 40; info_y = BORDER_Y + DRAW_H - 40; bw2 = 380; lh2 = 16
        msp.add_lwpolyline(
            [(bx,info_y),(bx+bw2,info_y),(bx+bw2,info_y-14*lh2),(bx,info_y-14*lh2),(bx,info_y)],
            dxfattribs={"layer":"FP-TBLK","lineweight":25})
        msp.add_text("SYSTEM DATA",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((bx+4, info_y-4))
        info_y -= TEXT_MD + 8
        ra = h.get("remote_area_calcs",{})
        da = h.get("density_area",{})
        system_info = [
            ("SYSTEM TYPE",        p.get("system_type","Wet Pipe")),
            ("OCCUPANCY",          p.get("occupancy","")),
            ("HAZARD CLASS",       ra.get("hazard","").replace("_"," ").title()),
            ("DESIGN DENSITY",     f'{da.get("density","—")} gpm/ft²'),
            ("DESIGN AREA",        f'{da.get("area","—")} ft²'),
            ("REMOTE HEADS",       str(ra.get("remote_sprinkler_count","—"))),
            ("K-FACTOR",           str(ra.get("k_factor","5.6"))),
            ("TOTAL WATER REQ.",   f'{h.get("flow_demand",0):.1f} gpm'),
            ("TOTAL PRESSURE REQ.",f'{h.get("required_pressure",0):.1f} psi'),
            ("HOSE ALLOWANCE",     f'{ra.get("hose_stream_gpm",0):.0f} gpm'),
            ("BASE OF RISER",      f'{h.get("flow_demand",0):.1f} gpm @ {h.get("required_pressure",0):.1f} psi'),
            ("STATIC PRESSURE",    f'{h.get("static_pressure",0):.0f} psi'),
            ("RESIDUAL PRESSURE",  f'{h.get("residual_pressure",0):.0f} psi'),
            ("SAFETY MARGIN",      f'{h.get("pressure_delta",0):+.1f} psi'),
        ]
        mid2 = bx + int(bw2 * 0.52)
        for label, val in system_info:
            msp.add_text(label+":",
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT_BOLD}
            ).set_placement((bx+4, info_y))
            msp.add_text(str(val),
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
            ).set_placement((mid2, info_y))
            msp.add_line((bx,info_y-2),(bx+bw2,info_y-2),
                         dxfattribs={"layer":"FP-TBLK","lineweight":9})
            info_y -= lh2

        return doc


    # ── FP3.0 Hydraulic calculations ─────────────────────────────────────────

    def _build_hydraulics(self):
        """
        FP3.0 — Full NFPA 13 Hydraulic Calculation Worksheet.
        Includes: system info header, node-by-node table, supply vs demand curve.
        Matches AutoSprink/HydraCALC output format.
        """
        from hydraulic_worksheet import build_hydraulic_worksheet
        doc, msp, meta = self._new_sheet("Hydraulic Calculations — NFPA 13 §28",
                                         "FP3.0", scale="N/A")
        p   = self.project
        h   = self.hydraulics
        ox  = BORDER_X + 30
        oy  = BORDER_Y + DRAW_H - 20

        def txt(text, x, y, ht=TEXT_SM, bold=False, color=None):
            att = {"layer":"FP-ANNO-NOTE","height":ht,"style":FONT_BOLD if bold else FONT}
            if color: att["color"] = color
            msp.add_text(str(text), dxfattribs=att).set_placement((x,y))

        def hline(x0, x1, y, lw=13):
            msp.add_line((x0,y),(x1,y), dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":lw})

        def vline(x, y0, y1, lw=13):
            msp.add_line((x,y0),(x,y1), dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":lw})

        # ── Title ──────────────────────────────────────────────────────────────
        txt("HYDRAULIC CALCULATION WORKSHEET", ox, oy, TEXT_LG, bold=True)
        txt("NFPA 13 — Current Edition  |  §28", ox+800, oy, TEXT_SM)
        oy -= TEXT_LG + 6

        # ── Header blocks: project info, system info, water supply ─────────────
        BW = int(DRAW_W * 0.32)   # block width

        def info_block(x, y, title, rows_kv):
            txt(title, x+4, y-2, TEXT_SM, bold=True)
            by = y - TEXT_SM - 8
            msp.add_lwpolyline(
                [(x,y),(x+BW,y),(x+BW,y-len(rows_kv)*18-20),(x,y-len(rows_kv)*18-20),(x,y)],
                dxfattribs={"layer":"FP-TBLK","lineweight":25})
            for lbl, val in rows_kv:
                txt(lbl+":", x+4,  by-2, TEXT_SM, bold=True)
                txt(val,     x+130, by-2, TEXT_SM)
                by -= 18
            return y - len(rows_kv)*18 - 24

        ra  = h.get("remote_area_calcs", {})
        da  = h.get("density_area", {})
        compliant = ra.get("pressure_delta", h.get("pressure_delta", 0)) >= 0 or h.get("compliant")

        project_rows = [
            ("Project",   p.get("project_name","")),
            ("Address",   p.get("location","")),
            ("Owner",     p.get("owner","")),
            ("Designer",  (p.get("designer",{}) or {}).get("name","") if isinstance(p.get("designer"),dict) else str(p.get("designer",""))),
            ("Cert.",     (p.get("designer",{}) or {}).get("cert","") if isinstance(p.get("designer"),dict) else ""),
            ("AHJ",       p.get("ahj_jurisdiction","")),
            ("Date",      p.get("issue_date", datetime.utcnow().strftime("%m/%d/%Y"))),
        ]
        system_rows = [
            ("Occupancy",    p.get("occupancy","")),
            ("System type",  p.get("system_type","Wet Pipe")),
            ("Pipe material",p.get("pipe_material","")),
            ("Seismic zone", str(p.get("seismic_zone",""))),
            ("Constr. type", str(p.get("construction_type",""))),
            ("NFPA edition", "NFPA 13 — Current"),
            ("Compliant",    "YES ✓" if compliant else "PENDING — Fire Pump Required"),
        ]
        design_rows = [
            ("Design method", ra.get("design_method","Density/Area §22")),
            ("Hazard class",  ra.get("hazard","").replace("_"," ").title()),
            ("K-factor",      str(ra.get("k_factor","5.6"))),
            ("Min head psi",  f'{ra.get("min_sprinkler_psi","7.0")} psi'),
            ("Design density",f'{da.get("density","—")} gpm/ft²'),
            ("Design area",   f'{da.get("area","—")} ft²'),
            ("Remote heads",  str(ra.get("remote_sprinkler_count","—"))),
            ("Hose stream",   f'{ra.get("hose_stream_gpm","—")} gpm'),
            ("HW C-factor",   str(ra.get("hw_c_factor",120))),
        ]
        ws_data_rows = [
            ("Static",    f'{h.get("static_pressure","—")} psi'),
            ("Residual",  f'{h.get("residual_pressure","—")} psi @ {p.get("water_supply_flow","—")} gpm'),
            ("Required",  f'{h.get("required_pressure","—")} psi'),
            ("Available", f'{h.get("pressure_delta","—"):+} psi margin'),
            ("Total flow",f'{h.get("flow_demand","—")} gpm'),
        ]

        bottom1 = info_block(ox,           oy, "PROJECT INFORMATION", project_rows)
        bottom2 = info_block(ox+BW+10,     oy, "SYSTEM & CODE",       system_rows)
        bottom3 = info_block(ox+2*(BW+10), oy, "DESIGN CRITERIA",     design_rows)
        oy      = min(bottom1, bottom2, bottom3) - 20

        # ── Worksheet table ────────────────────────────────────────────────────
        try:
            ws     = build_hydraulic_worksheet(
                {
                    "sprinkler_placements": self.cad.get("sprinkler_placements",[]),
                    "pipe_sections":        self.cad.get("pipe_sections",[]),
                    "remote_area_calcs":    ra,
                    "static_pressure":      h.get("static_pressure",72),
                    "residual_pressure":    h.get("residual_pressure",60),
                    "pressure_delta":       h.get("pressure_delta",0),
                    "flow_demand":          h.get("flow_demand",0),
                    "density_area":         da,
                },
                p
            )
            ws_rows = ws["rows"]
            summary = ws["summary"]
            curve   = ws["supply_curve"]
            demand_pt = ws["demand_point"]
        except Exception as e:
            txt(f"Worksheet generation error: {e}", ox, oy-20, TEXT_SM, color=colors.RED)
            ws_rows = []; summary = {}; curve = []; demand_pt = {}

        # Table column layout: (header, width, attr_name, format_str)
        cols = [
            ("From",     65,  "node_from",   "{}"),
            ("To",       75,  "node_to",     "{}"),
            ("Elev\nFrom", 40, "elev_from", "{:.0f}'"),
            ("Elev\nTo",   40, "elev_to",   "{:.0f}'"),
            ("K",        36,  "k_factor",    "{:.1f}"),
            ("Q dis\n(gpm)", 50,"q_discharge","{:.2f}"),
            ("Q total\n(gpm)",55,"q_total",  "{:.2f}"),
            ("Hose\n(gpm)", 45,"q_hose",    "{:.0f}"),
            ("Pipe\nType",  52,"pipe_type",  "{}"),
            ("Nom\nDia(in)",  38,"nom_dia",    "{:.2f}"),
            ("Int\nDia(in)",  42,"int_dia",    "{:.3f}"),
            ("Fittings\n(ft)",52,"fit_ft",  "{:.1f}"),
            ("Pipe\n(ft)",  48,"pipe_ft",   "{:.1f}"),
            ("C",        30,  "c_factor",    "{:.0f}"),
            ("hf/ft\n(psi)",52,"hf_per_ft", "{:.4f}"),
            ("P_start\n(psi)",52,"p_start", "{:.2f}"),
            ("P_elev\n(psi)",52,"p_elev",   "{:.3f}"),
            ("P_fric\n(psi)",52,"p_fric",   "{:.2f}"),
            ("P_end\n(psi)", 52,"p_end",    "{:.2f}"),
        ]
        TOTAL_W   = sum(c[1] for c in cols)
        ROW_H     = 20
        HDR_H     = 24
        tbl_x     = ox
        tbl_y     = oy

        # Table title
        txt("HYDRAULIC CALCULATION TABLE — NODE BY NODE", tbl_x, tbl_y, TEXT_MD, bold=True)
        txt("Per NFPA 13 §28.2 | Hazen-Williams: hf = 4.52 × Q¹·⁸⁵ / (C¹·⁸⁵ × d⁴·⁸⁷)",
            tbl_x, tbl_y-TEXT_MD-2, TEXT_SM)
        tbl_y -= TEXT_MD + TEXT_SM + 12

        # Header row
        cx2 = tbl_x
        hdr_top = tbl_y; hdr_bot = tbl_y - HDR_H
        msp.add_solid([(tbl_x,hdr_top),(tbl_x+TOTAL_W,hdr_top),
                       (tbl_x+TOTAL_W,hdr_bot),(tbl_x,hdr_bot)],
                      dxfattribs={"layer":"FP-TBLK","color":8})
        for hdr, cw, _, _ in cols:
            txt(hdr.replace("\n","\n"), cx2+2, hdr_bot+14, TEXT_SM*0.8, bold=True)
            vline(cx2, hdr_bot, tbl_y+6, lw=25)
            cx2 += cw
        vline(cx2, hdr_bot, tbl_y+6, lw=25)
        hline(tbl_x, tbl_x+TOTAL_W, hdr_top, lw=35)
        hline(tbl_x, tbl_x+TOTAL_W, hdr_bot, lw=25)
        tbl_y = hdr_bot

        # Data rows
        for ri, row in enumerate(ws_rows):
            row_y    = tbl_y - (ri+1)*ROW_H
            bg_color = 250 if ri%2==0 else 251  # alternating row shading
            # Alternate row background
            if ri%2==0:
                msp.add_solid([(tbl_x,tbl_y-ri*ROW_H),(tbl_x+TOTAL_W,tbl_y-ri*ROW_H),
                               (tbl_x+TOTAL_W,row_y),(tbl_x,row_y)],
                              dxfattribs={"layer":"FP-TBLK","color":250})
            cx2 = tbl_x
            for hdr, cw, attr, fmt in cols:
                val = getattr(row, attr, "")
                try:
                    cell_txt = fmt.format(val) if val != "" else ""
                except Exception:
                    cell_txt = str(val)
                # Highlight P_end column in last row
                bold_cell = (attr == "p_end" and ri == len(ws_rows)-1)
                txt(cell_txt, cx2+2, row_y+6, TEXT_SM*0.85, bold=bold_cell)
                vline(cx2, row_y, tbl_y-ri*ROW_H, lw=13)
                cx2 += cw
            vline(cx2, row_y, tbl_y-ri*ROW_H, lw=13)
            hline(tbl_x, tbl_x+TOTAL_W, row_y, lw=13)

        tbl_y = tbl_y - (len(ws_rows)+1)*ROW_H - 20

        # ── Summary row ────────────────────────────────────────────────────────
        if summary:
            compliant_str = "YES — NO FIRE PUMP REQUIRED" if summary.get("compliant") else "NO — FIRE PUMP REQUIRED"
            comp_color    = colors.GREEN if summary.get("compliant") else colors.RED
            smry_rows = [
                ("Total system demand:",   f'{summary.get("total_flow_gpm","—")} gpm  (sprinklers {summary.get("sprinkler_flow_gpm","—")} gpm + hose {summary.get("hose_stream_gpm","—")} gpm)'),
                ("Required pressure (source):", f'{summary.get("required_pressure_psi","—")} psi'),
                ("Available pressure (source):",f'{summary.get("available_pressure_psi","—")} psi'),
                ("Pressure margin:",         f'{summary.get("pressure_margin_psi","—"):+.1f} psi'),
                ("NFPA 13 compliant:",       compliant_str),
            ]
            txt("CALCULATION SUMMARY", ox, tbl_y, TEXT_MD, bold=True)
            tbl_y -= TEXT_MD + 6
            for lbl, val in smry_rows:
                is_comp = "compliant" in lbl.lower()
                txt(lbl, ox, tbl_y, TEXT_SM, bold=True)
                txt(val,  ox+330, tbl_y, TEXT_SM, color=comp_color if is_comp else None)
                tbl_y -= 18

        # ── Supply vs Demand Curve ─────────────────────────────────────────────
        if curve:
            tbl_y -= 20
            gx = ox; gy = tbl_y
            gw = min(600, TOTAL_W//2); gh = 280

            # Axis box
            msp.add_lwpolyline([(gx,gy),(gx+gw,gy),(gx+gw,gy-gh),(gx,gy-gh),(gx,gy)],
                               dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})

            # Axis labels
            txt("SUPPLY vs DEMAND CURVE", gx+gw//2, gy+16, TEXT_MD, bold=True)
            txt("FLOW (GPM)", gx+gw//2, gy-gh-20, TEXT_SM)
            txt("PRESSURE (PSI)", gx-40, gy-gh//2, TEXT_SM)

            max_q = max(pt["flow"]     for pt in curve if pt["flow"]     > 0) * 1.1
            max_p = max(pt["pressure"] for pt in curve if pt["pressure"] > 0) * 1.2
            if max_q <= 0: max_q = 1000
            if max_p <= 0: max_p = 100

            def to_graph(q, pr):
                return (gx + q/max_q*gw, gy - pr/max_p*gh)

            # Grid lines
            for qi in range(0, int(max_q), max(50, int(max_q//8))):
                gpt = to_graph(qi, 0)
                msp.add_line((gpt[0],gy),(gpt[0],gy-gh),
                             dxfattribs={"layer":"FP-GRID","linetype":"DASHED"})
                txt(str(qi), gpt[0], gy-gh-12, TEXT_SM*0.75)
            for pi2 in range(0, int(max_p), max(10, int(max_p//8))):
                gpt = to_graph(0, pi2)
                msp.add_line((gx,gpt[1]),(gx+gw,gpt[1]),
                             dxfattribs={"layer":"FP-GRID","linetype":"DASHED"})
                txt(str(pi2), gx-6, gpt[1]-4, TEXT_SM*0.75)

            # Supply curve (green)
            supply_pts = [to_graph(pt["flow"], pt["pressure"]) for pt in curve if pt["flow"] >= 0]
            if len(supply_pts) >= 2:
                msp.add_lwpolyline(supply_pts, dxfattribs={"layer":"FP-VALV","lineweight":25})
                txt("SUPPLY", supply_pts[-2][0]+10, supply_pts[-2][1]+10, TEXT_SM, color=colors.GREEN)

            # Demand point (red circle + label)
            if demand_pt:
                dq, dp = demand_pt["flow"], demand_pt["pressure"]
                dpx, dpy = to_graph(dq, dp)
                msp.add_circle((dpx,dpy), 8, dxfattribs={"layer":"FP-PIPE-MAIN"})
                msp.add_solid([(dpx-6,dpy),(dpx+6,dpy),(dpx,dpy-10)],
                              dxfattribs={"layer":"FP-PIPE-MAIN"})
                txt(f"DEMAND POINT\n{dq:.0f} gpm @ {dp:.1f} psi",
                    dpx+12, dpy-4, TEXT_SM, bold=True, color=colors.RED)

                # Demand line (vertical dashed from x-axis to demand point)
                msp.add_line((dpx,gy),(dpx,dpy),
                             dxfattribs={"layer":"FP-ANNO-DIMS","linetype":"DASHED"})
                msp.add_line((gx,dpy),(dpx,dpy),
                             dxfattribs={"layer":"FP-ANNO-DIMS","linetype":"DASHED"})

            txt("Graph: Supply curve vs system demand — operating point shown",
                gx, gy-gh-32, TEXT_SM*0.8)

        return doc

    # ── FP4.0 Schedules ───────────────────────────────────────────────────────

    def _build_schedules(self):
        doc, msp, meta = self._new_sheet("Sprinkler & Pipe Schedules","FP4.0", scale="N/A")
        s = ScheduleRenderer(msp)
        s.draw_sprinkler_schedule(self.cad.get("sprinkler_placements",[]))
        s.draw_pipe_schedule(self.cad.get("pipe_sections",[]))
        return doc

    # ── FP5.0 Details — Hanger + Sway Brace Drawings ────────────────────────

    def _build_details(self):
        """
        FP5.0 — Installation Details.
        Seismic sway brace assemblies + hanger detail drawings (TOLCO figures).
        """
        from detail_drawings import build_details_sheet
        doc, msp, meta = self._new_sheet(
            "Installation Details", "FP5.0", scale="N.T.S.")
        hangers = (self.bracing.get("hanger_schedule") or
                   self.cad.get("hangers", []))
        braces  = (self.bracing.get("sway_braces") or
                   self.cad.get("sway_braces", []))
        build_details_sheet(msp, self.project, hangers, braces)
        return doc

    # ── FP5.1 Sections ────────────────────────────────────────────────────────

    def _build_sections(self):
        doc, msp, meta = self._new_sheet("Section Cuts & Elevations","FP5.1", scale='1/4" = 1\'-0"')
        ch = self.project.get("ceiling_height",14)
        cx = BORDER_X + DRAW_W//2; fy = BORDER_Y + 100; cy = fy + int(ch*SCALE_FACTOR*0.5)
        msp.add_line((BORDER_X+100,fy),(BORDER_X+DRAW_W-100,fy), dxfattribs={"layer":"A-SLAB","lineweight":50})
        msp.add_text("FLOOR SLAB", dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}).set_placement((BORDER_X+104, fy+4))
        msp.add_line((BORDER_X+100,cy),(BORDER_X+DRAW_W-100,cy), dxfattribs={"layer":"A-CEIL","lineweight":25})
        msp.add_text(f"CEILING — {ch}'-0\" AFF", dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}).set_placement((BORDER_X+104, cy+4))
        msp.add_line((cx-200,cy),(cx+200,cy), dxfattribs={"layer":"FP-PIPE-MAIN","lineweight":35})
        msp.add_text("2\" BRANCH LINE", dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT}).set_placement((cx+204, cy-4))
        drop = int(SCALE_FACTOR*0.5)
        msp.add_line((cx,cy),(cx,cy-drop), dxfattribs={"layer":"FP-PIPE-BRNCH"})
        msp.add_blockref("SPKR_PEND",(cx,cy-drop), dxfattribs={"layer":"FP-SPKR-PEND"})
        msp.add_text("PENDANT SPRINKLER", dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT}).set_placement((cx+12, cy-drop-4))
        dim_x = BORDER_X + DRAW_W - 200
        msp.add_line((dim_x,fy),(dim_x,cy), dxfattribs={"layer":"FP-ANNO-DIMS"})
        msp.add_text(f"{ch}'-0\"", dxfattribs={"layer":"FP-ANNO-DIMS","height":TEXT_SM,"style":FONT}).set_placement((dim_x+8,(fy+cy)//2))
        msp.add_text("SECTION A-A — TYPICAL PENDANT INSTALLATION",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((cx, BORDER_Y+DRAW_H-40))
        return doc

    # ── FP6.0 BOM ─────────────────────────────────────────────────────────────

    def _build_bom_sheet(self):
        doc, msp, meta = self._new_sheet("Bill of Materials","FP6.0", scale="N/A")
        bom = self.bracing.get("bom",[]) if self.bracing else []
        if not bom:
            spkrs = self.cad.get("sprinkler_placements",[]); pipes = self.cad.get("pipe_sections",[])
            bom = []
            if spkrs:
                tc = Counter(s.get("type","pendant") for s in spkrs)
                for st, qty in tc.items():
                    bom.append({"item":f"{st.upper()} SPRINKLER HEAD","part_number":"TBD","qty":qty,"unit":"EA","unit_cost":8.50})
            if pipes:
                pl: dict = defaultdict(float)
                for p2 in pipes:
                    k = f'{p2.get("diameter","")} SCH {p2.get("schedule","40")} {p2.get("material","STEEL")}'
                    pl[k] += p2.get("length",0)
                for desc, length in pl.items():
                    bom.append({"item":f"PIPE — {desc}","part_number":"TBD","qty":round(length,1),"unit":"LF","unit_cost":4.20})
            bom += [
                {"item":"OS&Y GATE VALVE 4\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":285.00},
                {"item":"ALARM CHECK VALVE 4\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":420.00},
                {"item":"FLOW SWITCH","part_number":"TBD","qty":1,"unit":"EA","unit_cost":95.00},
                {"item":"INSPECTOR'S TEST CONN 1\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":45.00},
                {"item":"MAIN DRAIN ASSEMBLY 2\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":120.00},
                {"item":"FDC — 4\"x2.5\"x2.5\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":380.00},
                {"item":"RPZ BACKFLOW PREVENTER 4\"","part_number":"TBD","qty":1,"unit":"EA","unit_cost":780.00},
                {"item":"PIPE HANGER — STD ROD","part_number":"TBD","qty":0,"unit":"EA","unit_cost":12.50},
                {"item":"SWAY BRACE — 4-WAY","part_number":"TBD","qty":0,"unit":"EA","unit_cost":185.00},
            ]
        hdrs = ["#","DESCRIPTION","PART NO.","QTY","UNIT","UNIT COST","TOTAL"]
        cw   = [24,280,100,40,36,70,80]; ox = BORDER_X+20; oy = BORDER_Y+DRAW_H-40; rh = 18; total = sum(cw)
        msp.add_text("BILL OF MATERIALS", dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_LG,"style":FONT_BOLD}).set_placement((ox, oy))
        oy -= rh + 8; cx = ox
        for i, h2 in enumerate(hdrs):
            msp.add_text(h2, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((cx+2, oy-10)); cx += cw[i]
        msp.add_line((ox,oy),(ox+total,oy), dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":35})
        grand = 0.0; ry = oy - rh
        for idx, item in enumerate(bom):
            ry -= rh; qty = item.get("qty",0); uc = item.get("unit_cost",0); tot = qty*uc; grand += tot
            cells = [str(idx+1), item.get("item",""), item.get("part_number","TBD"),
                     str(qty), item.get("unit","EA"), f"${uc:,.2f}", f"${tot:,.2f}"]
            cx = ox
            for i, c in enumerate(cells):
                msp.add_text(c, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}).set_placement((cx+2, ry+4)); cx += cw[i]
            msp.add_line((ox,ry),(ox+total,ry), dxfattribs={"layer":"FP-ANNO-NOTE"})
        ry -= rh
        msp.add_text("ESTIMATED MATERIAL TOTAL (LABOR NOT INCLUDED):",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}).set_placement((ox, ry+4))
        msp.add_text(f"${grand:,.2f}", dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((ox+total, ry+4))
        return doc

    # ── PDF export — FIXED: proper axes limits from DXF extents ──────────────

    def _export_dxf_to_pdf(self, dxf_path, pdf_path, sheet_title):
        """
        Convert DXF to PDF at full sheet size (36"×27").
        Uses ezdxf drawing addon with correct extents so nothing is cropped.
        """
        try:
            import ezdxf
            from ezdxf.addons.drawing import RenderContext, Frontend
            from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_pdf import PdfPages

            doc2 = ezdxf.readfile(dxf_path)
            msp2 = doc2.modelspace()

            # Get model extents to set correct axes limits
            try:
                ext = msp2.get_extension_dict()
            except Exception:
                ext = None

            fig = plt.figure(figsize=(36, 27))          # exact sheet size in inches
            ax  = fig.add_axes([0, 0, 1, 1])
            ax.set_aspect("equal")
            ax.set_facecolor("white")                   # white for AHJ print submittals
            fig.patch.set_facecolor("white")

            ctx     = RenderContext(doc2)
            backend = MatplotlibBackend(ax)

            # White-background rendering: LayoutProperties maps color 7 → black
            try:
                from ezdxf.addons.drawing.properties import LayoutProperties
                lp = LayoutProperties.from_layout(msp2)
                lp.set_colors("#ffffff")
                frontend = Frontend(ctx, backend)
                frontend.draw_layout(msp2, finalize=True, layout_properties=lp)
            except Exception:
                frontend = Frontend(ctx, backend)
                frontend.draw_layout(msp2, finalize=True)


            # Force axes limits to match the full sheet
            ax.set_xlim(0, SHEET_W)
            ax.set_ylim(0, SHEET_H)
            ax.axis("off")

            with PdfPages(pdf_path) as pdf:
                pdf.savefig(fig, dpi=200, bbox_inches="tight",
                            facecolor="white")
            plt.close(fig)
            print(f"[DrawingEngine] PDF: {os.path.getsize(pdf_path)//1024}KB — {sheet_title}")

        except ImportError:
            self._fallback_pdf(pdf_path, sheet_title)
        except Exception as e:
            print(f"[DrawingEngine] PDF warning for {sheet_title}: {e}")
            self._fallback_pdf(pdf_path, sheet_title)

    def _fallback_pdf(self, pdf_path, sheet_title):
        """Minimal PDF if matplotlib rendering fails."""
        try:
            from reportlab.pdfgen import canvas as rlc
            from reportlab.lib.units import inch
            c = rlc.Canvas(pdf_path, pagesize=(36*inch, 27*inch))
            c.setFont("Helvetica-Bold", 28)
            c.drawCentredString(18*inch, 14*inch, sheet_title)
            c.setFont("Helvetica", 16)
            c.drawCentredString(18*inch, 13*inch, "Open the .dxf file in AutoCAD or AutoSprink for full drawing")
            c.save()
        except Exception:
            pass


    # ── FP7.0 Isometric view ──────────────────────────────────────────────────

    def _build_isometric(self):
        """
        FP7.0 — Fire Sprinkler Piping Plan: Isometric View.
        Matches AutoSprink / Battalion One FP-3 quality.
        3D axonometric projection showing full pipe network,
        all labels, symbols, sway braces, hangers, and sprinkler drops.
        """
        from isometric_builder import build_isometric, _define_blocks, _fmt_dia, _fmt_len

        doc, msp, meta = self._new_sheet(
            "Fire Sprinkler Piping Plan — Isometric View",
            "FP7.0", scale="NO SCALE")

        _define_blocks(doc)

        p        = self.project
        cad      = self.cad
        ps       = cad.get("pipe_sections", [])
        sp       = cad.get("sprinkler_placements", [])
        valves   = cad.get("valves", [])
        hangers  = self.bracing.get("hanger_schedule") or cad.get("hangers", [])
        braces   = self.bracing.get("sway_braces") or cad.get("sway_braces", [])
        meta_dm  = cad.get("design_metadata", {})

        if not ps:
            msp.add_text("No pipe data available",
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_LG,"style":FONT_BOLD}
            ).set_placement((SHEET_W//2, SHEET_H//2),
                            align=TextEntityAlignment.MIDDLE_CENTER)
            return doc

        # ── Compute scale to fit building on sheet ────────────────────────────
        bw_ft  = float(meta_dm.get("building_w_ft",100) or 100)
        bd_ft  = float(meta_dm.get("building_d_ft",65)  or 65)
        ch_ft  = float(p.get("ceiling_height", 12))

        # Isometric projected extents
        import math
        rad   = math.radians(30)
        # Bounding box of projected building
        corners_w = [(0,0,0),(bw_ft,0,0),(bw_ft,bd_ft,0),(0,bd_ft,0),
                     (0,0,ch_ft),(bw_ft,0,ch_ft),(bw_ft,bd_ft,ch_ft),(0,bd_ft,ch_ft)]
        def iso_raw(wx,wy,wz):
            return ((wx-wy)*math.cos(rad), (wx+wy)*math.sin(rad) + wz)
        proj = [iso_raw(wx,wy,wz) for wx,wy,wz in corners_w]
        proj_xs = [p2[0] for p2 in proj]; proj_ys = [p2[1] for p2 in proj]
        raw_w = max(proj_xs) - min(proj_xs)
        raw_h = max(proj_ys) - min(proj_ys)

        # Drawing area (leave margins for legend, title)
        avail_w  = DRAW_W * 0.90
        avail_h  = DRAW_H * 0.78
        if raw_w > 0 and raw_h > 0:
            scale    = min(avail_w/raw_w, avail_h/raw_h)
        else:
            scale    = 12.0
        scale_z  = scale * 1.2   # vertical slightly exaggerated for clarity

        # Centre the projected building in the drawing area
        min_px   = min(proj_xs)*scale; min_py = min(proj_ys)*scale
        cx_sheet = BORDER_X + DRAW_W * 0.45
        cy_sheet = BORDER_Y + DRAW_H * 0.45
        origin_x = cx_sheet - (min_px + (max(proj_xs)-min(proj_xs))*scale/2)
        origin_y = cy_sheet - (min_py + (max(proj_ys)-min(proj_ys))*scale/2)

        # ── Render isometric ─────────────────────────────────────────────────
        build_isometric(
            msp          = msp,
            pipe_sections= ps,
            sprinklers   = sp,
            valves       = valves,
            hangers      = hangers,
            sway_braces  = braces,
            cad_output   = cad,
            project      = p,
            origin_x     = origin_x,
            origin_y     = origin_y,
            iso_scale    = scale,
            iso_scale_z  = scale_z,
        )

        # ── Sheet title ───────────────────────────────────────────────────────
        msp.add_text(
            "FIRE SPRINKLER PIPING PLAN — ISOMETRIC VIEW",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_LG,"style":FONT_BOLD}
        ).set_placement((BORDER_X + DRAW_W//2, BORDER_Y + 30),
                        align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text(
            "NO SCALE",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
        ).set_placement((BORDER_X + DRAW_W//2, BORDER_Y + 16),
                        align=TextEntityAlignment.MIDDLE_CENTER)

        # ── Hanger designations legend (bottom-left) ──────────────────────────
        lx = BORDER_X + 30; ly = BORDER_Y + 200
        msp.add_lwpolyline(
            [(lx,ly),(lx+360,ly),(lx+360,ly-170),(lx,ly-170),(lx,ly)],
            dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":18})
        msp.add_text("HANGER DESIGNATIONS:",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((lx+6, ly-8))

        # Build legend dynamically from actual hangers
        seen = {}
        for h in hangers:
            num  = h.get("designation",1)
            desc = h.get("description","HANGER")
            if num not in seen: seen[num] = desc.upper()
        if 2 not in seen: seen[2] = "END OF LINE RESTRAINT"

        hy = ly - 24
        for num, desc in sorted(seen.items()):
            msp.add_circle((lx+10, hy-4), 7,
                           dxfattribs={"layer":"FP-HNGR","color":colors.CYAN,"lineweight":9})
            msp.add_text(str(num),
                dxfattribs={"layer":"FP-HNGR","height":TEXT_SM*0.75,
                            "style":FONT_BOLD,"color":colors.CYAN}
            ).set_placement((lx+10, hy-4-TEXT_SM*0.35),
                            align=TextEntityAlignment.MIDDLE_CENTER)
            msp.add_text(desc,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.82,"style":FONT}
            ).set_placement((lx+24, hy-8))
            hy -= 18

        # ── Symbols legend (bottom-center) ───────────────────────────────────
        sx = BORDER_X + 420; sy = ly
        msp.add_lwpolyline(
            [(sx,sy),(sx+380,sy),(sx+380,sy-170),(sx,sy-170),(sx,sy)],
            dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":18})
        msp.add_text("SYMBOLS LEGEND",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((sx+6, sy-8))

        sym_items = [
            ("★ 0'-0 FF↑", colors.CYAN,  "PIPE ELEVATION"),
            ("──────",     30,            "GROOVED COUPLING"),
            ("──|──",      30,            "FLEXIBLE COUPLING"),
            ("◆",          colors.GREEN,  "GATE VALVE"),
            ("◆",          colors.GREEN,  "BUTTERFLY VALVE"),
            ("◆",          colors.RED,    "CHECK VALVE"),
            ("╳",          colors.WHITE,  "END OF LINE RESTRAINT"),
            ("LAT/LNG →",  colors.CYAN,   "SWAY BRACE"),
            ("╋",          colors.RED,    "4-WAY BRACE"),
        ]
        syy = sy - 24
        for sym, clr, desc in sym_items:
            msp.add_text(sym,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.9,
                            "style":FONT_BOLD,"color":clr}
            ).set_placement((sx+10, syy-4))
            msp.add_text(desc,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.82,"style":FONT}
            ).set_placement((sx+46, syy-4))
            syy -= 17

        return doc


    # ── generate_all() ─────────────────────────────────────────────────────────

    def generate_all(self, output_dir="./outputs/drawings"):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = []
        sheets = [
            ("FP0.0 — Cover",       self._build_cover,         "FP0_0_Cover.dxf"),
            ("FP1.1 — Floor Plan",  lambda: self._build_floor_plan(1), "FP1_1_Floor_Plan.dxf"),
            ("FP2.0 — Riser Diagram",self._build_riser,        "FP2_0_Riser_Diagram.dxf"),
            ("FP3.0 — Hydraulics",  self._build_hydraulics,    "FP3_0_Hydraulics.dxf"),
            ("FP4.0 — Schedules",   self._build_schedules,     "FP4_0_Schedules.dxf"),
            ("FP5.0 — Details",     self._build_details,       "FP5_0_Details.dxf"),
            ("FP5.1 — Sections",    self._build_sections,      "FP5_1_Sections.dxf"),
            ("FP6.0 — BOM",         self._build_bom_sheet,     "FP6_0_BOM.dxf"),
        ]
        for name, fn, filename in sheets:
            try:
                print(f"[DrawingEngine] Generating {name}...")
                doc = fn(); out = os.path.join(output_dir, filename)
                doc.saveas(out)
                size = os.path.getsize(out)
                print(f"[DrawingEngine] ✓ {name} — {size/1024:.1f} KB")
                manifest.append({"sheet":name,"filename":filename,"path":out,"size_kb":round(size/1024,1)})
            except Exception as e:
                print(f"[DrawingEngine] ✗ {name} failed: {e}")
                manifest.append({"sheet":name,"filename":filename,"path":None,"error":str(e)})
        done = len([m for m in manifest if not m.get("error")])
        print(f"\n[DrawingEngine] Complete — {done}/{len(manifest)} sheet(s) generated.")
        return manifest

    # ── generate_selected() ───────────────────────────────────────────────────

    def generate_selected(self, output_dir, selected_sheets, include_pdf=True, include_3d=False):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = []
        for key, (name, builder_name, filename) in SHEET_BUILDER_MAP.items():
            if key not in selected_sheets:
                continue
            try:
                print(f"[DrawingEngine] Generating {name}...")
                builder = getattr(self, builder_name, None)
                if builder is None:
                    manifest.append({"sheet":name,"filename":filename,"path":None,"error":"builder not implemented"})
                    continue
                doc = builder(1) if builder_name == "_build_floor_plan" else builder()
                out = os.path.join(output_dir, filename)
                doc.saveas(out)
                size = os.path.getsize(out)
                print(f"[DrawingEngine] ✓ {name} — {size/1024:.1f} KB")
                manifest.append({"sheet":name,"filename":filename,"path":out,"size_kb":round(size/1024,1)})
                if include_pdf:
                    pf = filename.replace(".dxf",".pdf")
                    pp = os.path.join(output_dir, pf)
                    self._export_dxf_to_pdf(out, pp, name)
                    if os.path.exists(pp):
                        manifest.append({"sheet":name+" (PDF)","filename":pf,"path":pp,
                                         "size_kb":round(os.path.getsize(pp)/1024,1)})
            except Exception as e:
                print(f"[DrawingEngine] ✗ {name} failed: {e}")
                manifest.append({"sheet":name,"filename":filename,"path":None,"error":str(e)})

        if include_3d:
            try:
                print("[DrawingEngine] Generating 3D DXF overlay...")
                doc3, msp3, _ = self._new_sheet("3D Pipe Network","3D", scale="N.T.S.")
                cz = self.project.get("ceiling_height",14) * 12
                for s in self.cad.get("pipe_sections",[]):
                    fx = BORDER_X + s["from"]["x"]*SCALE_FACTOR
                    fy = BORDER_Y + s["from"]["y"]*SCALE_FACTOR
                    tx = BORDER_X + s["to"]["x"]*SCALE_FACTOR
                    ty = BORDER_Y + s["to"]["y"]*SCALE_FACTOR
                    msp3.add_line((fx,fy,cz),(tx,ty,cz), dxfattribs={"layer":"FP-PIPE-MAIN"})
                p3 = os.path.join(output_dir, "layout_3d.dxf")
                doc3.saveas(p3)
                manifest.append({"sheet":"3D DXF","filename":"layout_3d.dxf","path":p3,
                                  "size_kb":round(os.path.getsize(p3)/1024,1)})
            except Exception as e:
                manifest.append({"sheet":"3D DXF","filename":"layout_3d.dxf","path":None,"error":str(e)})

        done = len([m for m in manifest if not m.get("error")])
        print(f"[DrawingEngine] Complete — {done}/{len(manifest)} file(s) generated.")
        return manifest
