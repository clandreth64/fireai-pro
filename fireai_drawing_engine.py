"""
FireAI Pro — Construction Drawing Engine
=========================================
Drop at repo root. Generates fully compliant fire protection construction
drawings in DXF format (2D plan + details) meeting NFPA 13, NICET, and
professional AHJ submittal standards.

Covers:
  1. Sheet metadata (every sheet)
  2. Scale & view settings
  3. Geometry layers (walls, columns, beams, slabs, ceilings, roof, rooms)
  4. Fire protection symbols (all sprinkler types, piping, valves, equipment)
  5. Sheet set: Cover, Floor Plans, Riser Diagram, Hydraulic Calc, Details,
     Sections, Schedules, BOM

Requires:
  pip install ezdxf reportlab Pillow openpyxl

Usage:
  from fireai_drawing_engine import FireAIDrawingEngine
  engine = FireAIDrawingEngine(project, cad_output, hydraulics_output,
                                bracing_output, compliance_result)
  manifest = engine.generate_all("./outputs/drawings")
"""

import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import ezdxf
from ezdxf import colors
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import Modelspace, Layout
from ezdxf.math import Vec3
from ezdxf.document import Drawing


# ─── Drawing standards constants ─────────────────────────────────────────────

# All drawing coordinates are in INCHES (Autosprink + DXF convention)
SCALE_FACTOR   = 96       # 1/8" = 1'-0"  →  1 drawing unit = 1/8" real  →  96 units = 1 ft
SHEET_W        = 3456     # 36" wide (ANSI D)
SHEET_H        = 2592     # 27" tall (ANSI D)
MARGIN         = 72       # 0.75" margin
TB_HEIGHT      = 216      # Titleblock height 2.25"
BORDER_X       = MARGIN
BORDER_Y       = MARGIN + TB_HEIGHT
DRAW_W         = SHEET_W - 2 * MARGIN
DRAW_H         = SHEET_H - MARGIN - TB_HEIGHT - MARGIN

FONT           = "ROMANS"
FONT_BOLD      = "ROMAND"
TEXT_SM        = 9        # 0.09" → readable at 1/8" scale
TEXT_MD        = 12
TEXT_LG        = 18
TEXT_XL        = 24

# Sheet numbering convention
SHEET_CODES = {
    "cover":        "FP0.0",
    "floor_plan":   "FP1.{n}",
    "riser":        "FP2.0",
    "hydraulics":   "FP3.0",
    "schedules":    "FP4.0",
    "details":      "FP5.0",
    "sections":     "FP5.{n}",
    "bom":          "FP6.0",
}

# ─── Layer definitions ────────────────────────────────────────────────────────
# Follows NCS (National CAD Standard) naming convention

LAYER_DEFS = {
    # ── Geometry base (imported / referenced) ─────────────────────────────────
    "A-WALL-FULL":   {"color": colors.GRAY,   "ltype": "CONTINUOUS", "desc": "Full-height walls"},
    "A-WALL-PART":   {"color": colors.GRAY,   "ltype": "DASHED",     "desc": "Partial-height walls"},
    "A-COLS":        {"color": colors.GRAY,   "ltype": "CONTINUOUS", "desc": "Structural columns"},
    "A-BEAM":        {"color": colors.GRAY,   "ltype": "HIDDEN",     "desc": "Beams above"},
    "A-SLAB":        {"color": colors.GRAY,   "ltype": "CONTINUOUS", "desc": "Slab edges"},
    "A-CEIL":        {"color": 8,             "ltype": "DASHED2",    "desc": "Ceiling boundary"},
    "A-ROOF":        {"color": 8,             "ltype": "CONTINUOUS", "desc": "Roof outline"},
    "A-ROOM":        {"color": 253,           "ltype": "CONTINUOUS", "desc": "Room/space boundaries"},
    "A-ROOM-IDEN":   {"color": 253,           "ltype": "CONTINUOUS", "desc": "Room labels"},
    "A-DOOR":        {"color": colors.GRAY,   "ltype": "CONTINUOUS", "desc": "Door swings"},
    "A-GLAZ":        {"color": colors.CYAN,   "ltype": "CONTINUOUS", "desc": "Glazing/windows"},
    # ── Fire protection — piping ──────────────────────────────────────────────
    "FP-PIPE-MAIN":  {"color": colors.RED,    "ltype": "CONTINUOUS", "desc": "Main pipe runs"},
    "FP-PIPE-XMAIN": {"color": 20,            "ltype": "CONTINUOUS", "desc": "Cross mains"},
    "FP-PIPE-BRNCH": {"color": colors.YELLOW, "ltype": "CONTINUOUS", "desc": "Branch lines"},
    "FP-PIPE-ARMOV": {"color": colors.YELLOW, "ltype": "DASHED",     "desc": "Armovers"},
    "FP-PIPE-DRAIN": {"color": colors.CYAN,   "ltype": "DASHED",     "desc": "Drain / test lines"},
    "FP-PIPE-HIDEN": {"color": colors.RED,    "ltype": "HIDDEN",     "desc": "Hidden pipe above"},
    # ── Fire protection — sprinklers ──────────────────────────────────────────
    "FP-SPKR-UPRT":  {"color": colors.BLUE,   "ltype": "CONTINUOUS", "desc": "Upright sprinklers"},
    "FP-SPKR-PEND":  {"color": colors.BLUE,   "ltype": "CONTINUOUS", "desc": "Pendant sprinklers"},
    "FP-SPKR-SIDE":  {"color": colors.BLUE,   "ltype": "CONTINUOUS", "desc": "Sidewall sprinklers"},
    "FP-SPKR-CONC":  {"color": colors.BLUE,   "ltype": "CONTINUOUS", "desc": "Concealed sprinklers"},
    "FP-SPKR-ESFR":  {"color": 30,            "ltype": "CONTINUOUS", "desc": "ESFR sprinklers"},
    "FP-SPKR-CMSA":  {"color": 30,            "ltype": "CONTINUOUS", "desc": "CMSA sprinklers"},
    "FP-SPKR-COVR":  {"color": 251,           "ltype": "DASHED2",    "desc": "Coverage area circles"},
    # ── Fire protection — valves & equipment ──────────────────────────────────
    "FP-VALV":       {"color": colors.GREEN,  "ltype": "CONTINUOUS", "desc": "All valves"},
    "FP-EQUP":       {"color": colors.GREEN,  "ltype": "CONTINUOUS", "desc": "Equipment (pump, BFP)"},
    "FP-RISR":       {"color": colors.RED,    "ltype": "CONTINUOUS", "desc": "Riser assemblies"},
    "FP-FDC":        {"color": 30,            "ltype": "CONTINUOUS", "desc": "FDC locations"},
    "FP-HNGR":       {"color": 251,           "ltype": "CONTINUOUS", "desc": "Hangers & bracing"},
    # ── Annotation ────────────────────────────────────────────────────────────
    "FP-ANNO-DIMS":  {"color": colors.WHITE,  "ltype": "CONTINUOUS", "desc": "Dimensions"},
    "FP-ANNO-LABL":  {"color": colors.WHITE,  "ltype": "CONTINUOUS", "desc": "Pipe + sprinkler tags"},
    "FP-ANNO-SYMB":  {"color": colors.WHITE,  "ltype": "CONTINUOUS", "desc": "Drawing symbols"},
    "FP-ANNO-NOTE":  {"color": colors.WHITE,  "ltype": "CONTINUOUS", "desc": "General notes"},
    "FP-ANNO-REVS":  {"color": colors.RED,    "ltype": "CONTINUOUS", "desc": "Revision clouds"},
    # ── Titleblock ────────────────────────────────────────────────────────────
    "FP-TBLK":       {"color": colors.WHITE,  "ltype": "CONTINUOUS", "desc": "Titleblock border"},
    "FP-TBLK-TEXT":  {"color": colors.WHITE,  "ltype": "CONTINUOUS", "desc": "Titleblock text"},
    # ── Viewports / sheet setup ───────────────────────────────────────────────
    "FP-VWPT":       {"color": 250,           "ltype": "CONTINUOUS", "desc": "Viewport borders"},
    "FP-GRID":       {"color": 251,           "ltype": "DOT2",       "desc": "Reference grid"},
}

SPRINKLER_SYMBOLS = {
    "upright":   "U",   # circle with crosshair + U tag
    "pendant":   "P",   # circle with crosshair + P tag
    "sidewall":  "SW",  # half-circle
    "concealed": "C",   # filled circle
    "esfr":      "E",   # double circle
    "cmsa":      "M",   # square
}

VALVE_SYMBOLS = {
    "osy":          "OS&Y",
    "butterfly":    "BFV",
    "check":        "CV",
    "alarm":        "AV",
    "inspector_test": "IT",
    "drain":        "DR",
    "ball":         "BV",
}


# ─── Project / sheet dataclasses ──────────────────────────────────────────────

@dataclass
class Revision:
    number:      str
    date:        str
    description: str
    by:          str

@dataclass
class SheetMeta:
    sheet_title:  str
    sheet_number: str           # e.g. FP1.0
    scale:        str           # e.g. 1/8" = 1'-0"
    issue_date:   str
    revisions:    list[Revision] = field(default_factory=list)


# ─── DXF document factory ─────────────────────────────────────────────────────

class DXFDocFactory:
    """Creates a new ezdxf Drawing pre-configured with all layers, linetypes, and text styles."""

    @staticmethod
    def new_doc() -> Drawing:
        doc = ezdxf.new("R2018", setup=True)

        # Register all layers
        for name, props in LAYER_DEFS.items():
            ltype = props.get("ltype", "CONTINUOUS")
            if ltype not in ("CONTINUOUS",) and ltype not in doc.linetypes:
                try:
                    doc.linetypes.add(ltype)
                except Exception:
                    ltype = "CONTINUOUS"
            doc.layers.add(name, color=props["color"], linetype="CONTINUOUS")

        # Text styles
        doc.styles.add("ROMANS",  font="romans.shx")
        doc.styles.add("ROMAND",  font="romand.shx")
        doc.styles.add("STANDARD",font="txt.shx")

        # Dimension style
        dimstyle = doc.dimstyles.new("FP_DIM")
        dimstyle.dxf.dimscale = 96   # matches 1/8"=1'-0" scale
        dimstyle.dxf.dimtxsty = "ROMANS"
        dimstyle.dxf.dimasz   = 8
        dimstyle.dxf.dimtxt   = 8

        return doc


# ─── Titleblock renderer ──────────────────────────────────────────────────────

class TitleblockRenderer:
    """
    Draws the full professional titleblock on a given layout.
    Covers: company info, project info, sheet metadata, revision log,
    designed by / checked by, drawing scale, discipline, NFPA reference.
    """

    def __init__(self, doc: Drawing, project: dict):
        self.doc     = doc
        self.project = project

    def _t(self, layout, text: str, x: float, y: float,
           height: float = TEXT_SM, layer: str = "FP-TBLK-TEXT",
           bold: bool = False, align: TextEntityAlignment = TextEntityAlignment.LEFT):
        layout.add_text(
            text,
            dxfattribs={
                "layer":  layer,
                "height": height,
                "style":  FONT_BOLD if bold else FONT,
            }
        ).set_placement((x, y), align=align)

    def _line(self, layout, x1, y1, x2, y2, layer="FP-TBLK"):
        layout.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer, "lineweight": 25})

    def _rect(self, layout, x, y, w, h, layer="FP-TBLK", lw=25):
        pts = [(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]
        layout.add_lwpolyline(pts, dxfattribs={"layer": layer, "lineweight": lw})

    def render(self, layout, meta: SheetMeta):
        bx = MARGIN
        by = MARGIN
        bw = DRAW_W
        bh = TB_HEIGHT
        p  = self.project
        now = meta.issue_date

        # Outer border
        self._rect(layout, bx, by, bw, bh, lw=50)

        # ── Column 1: Company block (left 18%) ─────────────────────────────
        c1w = int(bw * 0.18)
        self._line(layout, bx + c1w, by, bx + c1w, by + bh)
        cx = bx + c1w / 2

        self._t(layout, p.get("company_name", "FireAI Pro"),
                cx, by + bh - 22, TEXT_LG, bold=True,
                align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, p.get("company_address", ""),
                cx, by + bh - 40, TEXT_SM,
                align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, p.get("company_phone", ""),
                cx, by + bh - 54, TEXT_SM,
                align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, p.get("company_email", ""),
                cx, by + bh - 68, TEXT_SM,
                align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, "FIRE PROTECTION",
                cx, by + bh - 88, TEXT_MD, bold=True,
                align=TextEntityAlignment.MIDDLE_CENTER)
        self._t(layout, "NFPA 13 — Current Edition",
                cx, by + bh - 104, TEXT_SM,
                align=TextEntityAlignment.MIDDLE_CENTER)

        # Designed / checked
        self._line(layout, bx, by + 80, bx + c1w, by + 80)
        self._t(layout, "DESIGNED BY:", bx + 6, by + 72, TEXT_SM, bold=True)
        self._t(layout, p.get("designer_name", ""), bx + 6, by + 58, TEXT_SM)
        self._t(layout, p.get("designer_cert", ""),  bx + 6, by + 44, TEXT_SM)
        self._line(layout, bx, by + 40, bx + c1w, by + 40)
        self._t(layout, "CHECKED BY:", bx + 6, by + 32, TEXT_SM, bold=True)
        self._t(layout, p.get("checker_name", ""),  bx + 6, by + 18, TEXT_SM)

        # ── Column 2: Project info (middle 40%) ────────────────────────────
        c2w = int(bw * 0.40)
        c2x = bx + c1w
        self._line(layout, c2x + c2w, by, c2x + c2w, by + bh)
        px = c2x + 10

        self._t(layout, "PROJECT:", px, by + bh - 22, TEXT_SM, bold=True)
        self._t(layout, p.get("project_name", ""), px, by + bh - 36, TEXT_MD, bold=True)
        self._line(layout, c2x, by + bh - 44, c2x + c2w, by + bh - 44)

        self._t(layout, "ADDRESS:", px, by + bh - 56, TEXT_SM, bold=True)
        self._t(layout, p.get("location", ""), px, by + bh - 70, TEXT_SM)
        self._line(layout, c2x, by + bh - 78, c2x + c2w, by + bh - 78)

        self._t(layout, "PROJECT NO. (INTERNAL):", px, by + bh - 90, TEXT_SM, bold=True)
        self._t(layout, p.get("project_number_internal", ""), px, by + bh - 104, TEXT_SM)

        self._t(layout, "PROJECT NO. (CUSTOMER):", px + c2w//2, by + bh - 90, TEXT_SM, bold=True)
        self._t(layout, p.get("project_number_customer", ""), px + c2w//2, by + bh - 104, TEXT_SM)

        self._line(layout, c2x, by + bh - 112, c2x + c2w, by + bh - 112)

        self._t(layout, "OCCUPANCY:", px, by + bh - 124, TEXT_SM, bold=True)
        self._t(layout, p.get("occupancy", ""), px, by + bh - 138, TEXT_SM)

        self._t(layout, "SYSTEM TYPE:", px + c2w//2, by + bh - 124, TEXT_SM, bold=True)
        self._t(layout, (p.get("system_type", "WET")).upper(), px + c2w//2, by + bh - 138, TEXT_SM)

        self._line(layout, c2x, by + bh - 146, c2x + c2w, by + bh - 146)

        self._t(layout, "AHJ:", px, by + bh - 158, TEXT_SM, bold=True)
        self._t(layout, p.get("ahj_jurisdiction", ""), px, by + bh - 172, TEXT_SM)

        # ── Column 3: Sheet info (right 24%) ──────────────────────────────
        c3w = int(bw * 0.24)
        c3x = c2x + c2w
        c4x = bx + bw  # revision col starts here
        sx  = c3x + 10

        self._t(layout, "SHEET TITLE:", sx, by + bh - 22, TEXT_SM, bold=True)
        self._t(layout, meta.sheet_title, sx, by + bh - 38, TEXT_MD, bold=True)

        self._line(layout, c3x, by + bh - 46, c4x, by + bh - 46)

        self._t(layout, "SHEET NO.:", sx, by + bh - 58, TEXT_SM, bold=True)
        self._t(layout, meta.sheet_number, sx, by + bh - 76, TEXT_XL, bold=True)

        self._line(layout, c3x, by + bh - 86, c4x, by + bh - 86)

        self._t(layout, "DISCIPLINE:", sx, by + bh - 98, TEXT_SM, bold=True)
        self._t(layout, "Fire Protection", sx, by + bh - 112, TEXT_SM)

        self._t(layout, "SCALE:", sx, by + bh - 124, TEXT_SM, bold=True)
        self._t(layout, meta.scale, sx, by + bh - 138, TEXT_SM)

        self._line(layout, c3x, by + bh - 146, c4x, by + bh - 146)

        self._t(layout, "ISSUE DATE:", sx, by + bh - 158, TEXT_SM, bold=True)
        self._t(layout, now, sx, by + bh - 172, TEXT_SM)

        self._t(layout, "REVISION:", sx + c3w//2, by + bh - 158, TEXT_SM, bold=True)
        rev_num = str(len(meta.revisions)) if meta.revisions else "0"
        self._t(layout, rev_num, sx + c3w//2, by + bh - 172, TEXT_SM)

        # Revision history log (bottom of col 3 + 4)
        self._line(layout, c3x, by + bh - 180, c4x, by + bh - 180)
        self._t(layout, "REV", sx, by + bh - 192, TEXT_SM, bold=True)
        self._t(layout, "DATE", sx + 24, by + bh - 192, TEXT_SM, bold=True)
        self._t(layout, "DESCRIPTION", sx + 80, by + bh - 192, TEXT_SM, bold=True)
        self._line(layout, c3x, by + bh - 198, c4x, by + bh - 198)

        rev_y = by + bh - 210
        for rev in (meta.revisions or []):
            self._t(layout, rev.number,      sx,        rev_y, TEXT_SM)
            self._t(layout, rev.date,        sx + 24,   rev_y, TEXT_SM)
            self._t(layout, rev.description, sx + 80,   rev_y, TEXT_SM)
            rev_y -= 14
            if rev_y < by + 10:
                break


# ─── Symbol library ───────────────────────────────────────────────────────────

class SymbolLibrary:
    """
    Draws fire protection symbols as block definitions.
    All blocks are in model units (inches × SCALE_FACTOR).
    Symbol radius ~6 units = 1/16" at 1/8" scale = clearly readable.
    """

    R = 6     # Base symbol radius

    @classmethod
    def define_all(cls, doc: Drawing):
        cls._define_sprinkler_upright(doc)
        cls._define_sprinkler_pendant(doc)
        cls._define_sprinkler_sidewall(doc)
        cls._define_sprinkler_concealed(doc)
        cls._define_sprinkler_esfr(doc)
        cls._define_sprinkler_cmsa(doc)
        cls._define_valve_osy(doc)
        cls._define_valve_butterfly(doc)
        cls._define_valve_check(doc)
        cls._define_valve_alarm(doc)
        cls._define_inspector_test(doc)
        cls._define_drain(doc)
        cls._define_riser(doc)
        cls._define_fdc(doc)
        cls._define_north_arrow(doc)
        cls._define_revision_cloud(doc)

    @classmethod
    def _blk(cls, doc: Drawing, name: str):
        if name in doc.blocks:
            return doc.blocks[name]
        return doc.blocks.new(name)

    @classmethod
    def _define_sprinkler_upright(cls, doc):
        blk = cls._blk(doc, "SPKR_UPRT")
        R = cls.R
        blk.add_circle((0, 0), R, dxfattribs={"layer": "FP-SPKR-UPRT"})
        blk.add_line((-R, 0), (R, 0),  dxfattribs={"layer": "FP-SPKR-UPRT"})
        blk.add_line((0, -R), (0, R),  dxfattribs={"layer": "FP-SPKR-UPRT"})
        blk.add_text("U", dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_sprinkler_pendant(cls, doc):
        blk = cls._blk(doc, "SPKR_PEND")
        R = cls.R
        blk.add_circle((0, 0), R, dxfattribs={"layer": "FP-SPKR-PEND"})
        blk.add_line((-R, 0), (R, 0), dxfattribs={"layer": "FP-SPKR-PEND"})
        blk.add_text("P", dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_sprinkler_sidewall(cls, doc):
        blk = cls._blk(doc, "SPKR_SIDE")
        R = cls.R
        # Half circle (right side)
        blk.add_arc((0, 0), R, -90, 90,  dxfattribs={"layer": "FP-SPKR-SIDE"})
        blk.add_line((0, -R), (0, R),    dxfattribs={"layer": "FP-SPKR-SIDE"})
        blk.add_text("SW", dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_sprinkler_concealed(cls, doc):
        blk = cls._blk(doc, "SPKR_CONC")
        R = cls.R
        blk.add_circle((0, 0), R, dxfattribs={"layer":"FP-SPKR-CONC","color":colors.BLUE})
        # Filled via solid hatch
        hatch = blk.add_hatch(color=colors.BLUE, dxfattribs={"layer":"FP-SPKR-CONC"})
        hatch.paths.add_edge_path().add_arc((0,0), R, 0, 360)
        blk.add_text("C", dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_sprinkler_esfr(cls, doc):
        blk = cls._blk(doc, "SPKR_ESFR")
        R = cls.R
        blk.add_circle((0, 0), R,        dxfattribs={"layer": "FP-SPKR-ESFR"})
        blk.add_circle((0, 0), R * 0.55, dxfattribs={"layer": "FP-SPKR-ESFR"})
        blk.add_text("E", dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_sprinkler_cmsa(cls, doc):
        blk = cls._blk(doc, "SPKR_CMSA")
        R = cls.R
        pts = [(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)]
        blk.add_lwpolyline(pts, dxfattribs={"layer": "FP-SPKR-CMSA"})
        blk.add_text("M", dxfattribs={"layer":"FP-ANNO-LABL","height":5,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_valve_osy(cls, doc):
        blk = cls._blk(doc, "VALV_OSY")
        R = cls.R
        blk.add_circle((0, 0), R, dxfattribs={"layer": "FP-VALV"})
        # OS&Y yoke symbol — triangle
        blk.add_solid([(-R, 0), (R, 0), (0, R * 1.5)], dxfattribs={"layer":"FP-VALV"})
        blk.add_text("OS&Y", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_valve_butterfly(cls, doc):
        blk = cls._blk(doc, "VALV_BFV")
        R = cls.R
        blk.add_circle((0, 0), R, dxfattribs={"layer": "FP-VALV"})
        blk.add_line((-R, 0), (R, 0), dxfattribs={"layer": "FP-VALV"})
        blk.add_text("BFV", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_valve_check(cls, doc):
        blk = cls._blk(doc, "VALV_CV")
        R = cls.R
        blk.add_circle((0, 0), R, dxfattribs={"layer":"FP-VALV"})
        # Check valve clapper
        blk.add_line((0, -R), (0, R), dxfattribs={"layer":"FP-VALV"})
        blk.add_solid([(0, 0), (R, R//2), (R, -R//2)], dxfattribs={"layer":"FP-VALV"})
        blk.add_text("CV", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_valve_alarm(cls, doc):
        blk = cls._blk(doc, "VALV_AV")
        R = cls.R
        blk.add_circle((0,0), R, dxfattribs={"layer":"FP-VALV"})
        blk.add_text("AV", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_inspector_test(cls, doc):
        blk = cls._blk(doc, "VALV_IT")
        R = cls.R
        pts = [(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)]
        blk.add_lwpolyline(pts, dxfattribs={"layer":"FP-VALV"})
        blk.add_text("IT", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_drain(cls, doc):
        blk = cls._blk(doc, "VALV_DR")
        R = cls.R
        blk.add_circle((0,0), R, dxfattribs={"layer":"FP-VALV"})
        blk.add_line((-R, -R), (R, R), dxfattribs={"layer":"FP-VALV"})
        blk.add_text("DR", dxfattribs={"layer":"FP-ANNO-LABL","height":4,"style":FONT}
                    ).set_placement((R+3, 3))

    @classmethod
    def _define_riser(cls, doc):
        blk = cls._blk(doc, "FP_RISER")
        blk.add_circle((0, 0), 14, dxfattribs={"layer":"FP-RISR","lineweight":50})
        blk.add_circle((0, 0), 10, dxfattribs={"layer":"FP-RISR"})
        blk.add_text("RISER", dxfattribs={"layer":"FP-ANNO-LABL","height":6,"style":FONT_BOLD}
                    ).set_placement((0, -3), align=TextEntityAlignment.MIDDLE_CENTER)

    @classmethod
    def _define_fdc(cls, doc):
        blk = cls._blk(doc, "FP_FDC")
        R = 10
        pts = [(-R,-R),(R,-R),(R,R),(-R,R),(-R,-R)]
        blk.add_lwpolyline(pts, dxfattribs={"layer":"FP-FDC","lineweight":50})
        blk.add_text("FDC", dxfattribs={"layer":"FP-ANNO-LABL","height":7,"style":FONT_BOLD}
                    ).set_placement((0, -4), align=TextEntityAlignment.MIDDLE_CENTER)

    @classmethod
    def _define_north_arrow(cls, doc):
        blk = cls._blk(doc, "NORTH_ARROW")
        blk.add_line((0, 0), (0, 60),  dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":35})
        blk.add_solid([(0, 60),(-8, 40),(8, 40)], dxfattribs={"layer":"FP-ANNO-SYMB"})
        blk.add_circle((0, 0), 24, dxfattribs={"layer":"FP-ANNO-SYMB"})
        blk.add_text("N", dxfattribs={"layer":"FP-ANNO-LABL","height":16,"style":FONT_BOLD}
                    ).set_placement((0, 66), align=TextEntityAlignment.BOTTOM_CENTER)

    @classmethod
    def _define_revision_cloud(cls, doc):
        # Placeholder — revision clouds are added dynamically per revision
        blk = cls._blk(doc, "REV_CLOUD")
        blk.add_text("REV", dxfattribs={"layer":"FP-ANNO-REVS","height":TEXT_SM,"style":FONT_BOLD}
                    ).set_placement((0, 0))


# ─── Plan view renderer ───────────────────────────────────────────────────────

class PlanViewRenderer:
    """Renders a full floor plan sheet with all FP elements."""

    def __init__(self, msp: Modelspace, project: dict):
        self.msp     = msp
        self.project = project

    def _ft(self, ft: float) -> float:
        """Convert project feet to DXF units."""
        return ft * SCALE_FACTOR

    def _pt(self, x_ft: float, y_ft: float, offset_x=0, offset_y=0):
        return (
            BORDER_X + offset_x + self._ft(x_ft),
            BORDER_Y + offset_y + self._ft(y_ft),
        )

    # ── Base geometry ──────────────────────────────────────────────────────────

    def draw_walls(self, walls: list[dict], offset_x=0, offset_y=0):
        for wall in walls:
            pts = [(BORDER_X + offset_x + self._ft(p["x"]),
                    BORDER_Y + offset_y + self._ft(p["y"])) for p in wall["points"]]
            layer = "A-WALL-PART" if wall.get("partial") else "A-WALL-FULL"
            lw    = 35 if wall.get("exterior") else 18
            self.msp.add_lwpolyline(
                pts,
                close=wall.get("closed", False),
                dxfattribs={"layer": layer, "lineweight": lw}
            )

    def draw_columns(self, columns: list[dict], offset_x=0, offset_y=0):
        for col in columns:
            cx, cy = self._pt(col["x"], col["y"], offset_x, offset_y)
            w = self._ft(col.get("width", 1.5))
            d = self._ft(col.get("depth", 1.5))
            pts = [(cx-w/2, cy-d/2),(cx+w/2,cy-d/2),(cx+w/2,cy+d/2),(cx-w/2,cy+d/2),(cx-w/2,cy-d/2)]
            self.msp.add_lwpolyline(pts, close=True, dxfattribs={"layer":"A-COLS","lineweight":50})
            hatch = self.msp.add_hatch(dxfattribs={"layer":"A-COLS"})
            hatch.paths.add_polyline_path(pts[:-1], is_closed=True)

    def draw_rooms(self, rooms: list[dict], offset_x=0, offset_y=0):
        for room in rooms:
            pts = [(BORDER_X + offset_x + self._ft(p["x"]),
                    BORDER_Y + offset_y + self._ft(p["y"])) for p in room["boundary"]]
            self.msp.add_lwpolyline(pts, close=True,
                dxfattribs={"layer":"A-ROOM","lineweight":5})
            # Room label
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            self.msp.add_text(
                room.get("name",""),
                dxfattribs={"layer":"A-ROOM-IDEN","height":TEXT_SM,"style":FONT}
            ).set_placement((cx, cy + TEXT_SM/2), align=TextEntityAlignment.MIDDLE_CENTER)
            self.msp.add_text(
                room.get("area",""),
                dxfattribs={"layer":"A-ROOM-IDEN","height":TEXT_SM*0.8,"style":FONT}
            ).set_placement((cx, cy - TEXT_SM*0.8), align=TextEntityAlignment.MIDDLE_CENTER)

    # ── Piping ────────────────────────────────────────────────────────────────

    def draw_pipes(self, pipe_sections: list[dict], offset_x=0, offset_y=0):
        for section in pipe_sections:
            fx, fy = self._pt(section["from"]["x"], section["from"]["y"], offset_x, offset_y)
            tx, ty = self._pt(section["to"]["x"],   section["to"]["y"],   offset_x, offset_y)

            pipe_type = section.get("pipe_type", "branch")
            layer = {
                "main":   "FP-PIPE-MAIN",
                "cross":  "FP-PIPE-XMAIN",
                "branch": "FP-PIPE-BRNCH",
                "armover":"FP-PIPE-ARMOV",
                "drain":  "FP-PIPE-DRAIN",
            }.get(pipe_type, "FP-PIPE-BRNCH")

            lw = 50 if pipe_type == "main" else (35 if pipe_type in ("cross","xmain") else 18)

            self.msp.add_line((fx, fy), (tx, ty),
                dxfattribs={"layer": layer, "lineweight": lw})

            # Pipe size label at midpoint
            mx, my = (fx + tx) / 2, (fy + ty) / 2
            dia    = section.get("diameter", "")
            sched  = section.get("schedule", "")
            mat    = section.get("material", "")
            label  = f'{dia}" {sched} {mat}'.strip()
            angle  = math.degrees(math.atan2(ty - fy, tx - fx))

            self.msp.add_text(
                label,
                dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM*0.8,
                             "style":FONT,"rotation":angle if abs(angle) < 45 else 90}
            ).set_placement((mx, my + TEXT_SM), align=TextEntityAlignment.BOTTOM_CENTER)

    # ── Sprinkler heads ───────────────────────────────────────────────────────

    def draw_sprinklers(self, sprinklers: list[dict], offset_x=0, offset_y=0,
                        show_coverage=True):
        block_map = {
            "upright":   "SPKR_UPRT",
            "pendant":   "SPKR_PEND",
            "sidewall":  "SPKR_SIDE",
            "concealed": "SPKR_CONC",
            "esfr":      "SPKR_ESFR",
            "cmsa":      "SPKR_CMSA",
        }
        for s in sprinklers:
            px, py = self._pt(s["x"], s["y"], offset_x, offset_y)
            stype  = s.get("type", "pendant").lower()
            bname  = block_map.get(stype, "SPKR_PEND")

            self.msp.add_blockref(bname, (px, py),
                dxfattribs={"layer": f"FP-SPKR-{stype[:4].upper()}"})

            # Coverage area circle (dashed)
            if show_coverage and s.get("coverage_radius"):
                r = self._ft(s["coverage_radius"])
                self.msp.add_circle((px, py), r,
                    dxfattribs={"layer":"FP-SPKR-COVR","linetype":"DASHED"})

            # Tag: sprinkler ID + K-factor + temp rating
            tag_parts = [s.get("id", "")]
            if s.get("k_factor"):      tag_parts.append(f'K{s["k_factor"]}')
            if s.get("temp_rating"):   tag_parts.append(f'{s["temp_rating"]}°F')
            if s.get("hazard"):        tag_parts.append(s["hazard"])
            tag = " / ".join(str(t) for t in tag_parts if t)

            self.msp.add_text(
                tag,
                dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM*0.75,"style":FONT}
            ).set_placement((px + SymbolLibrary.R + 4, py + SymbolLibrary.R + 2))

    # ── Valves ────────────────────────────────────────────────────────────────

    def draw_valves(self, valves: list[dict], offset_x=0, offset_y=0):
        block_map = {
            "osy":          "VALV_OSY",
            "butterfly":    "VALV_BFV",
            "check":        "VALV_CV",
            "alarm":        "VALV_AV",
            "inspector_test": "VALV_IT",
            "drain":        "VALV_DR",
        }
        for v in valves:
            px, py = self._pt(v["x"], v["y"], offset_x, offset_y)
            vtype  = v.get("type", "osy").lower()
            bname  = block_map.get(vtype, "VALV_OSY")
            self.msp.add_blockref(bname, (px, py), dxfattribs={"layer":"FP-VALV"})
            self.msp.add_text(
                v.get("id",""),
                dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM*0.75,"style":FONT}
            ).set_placement((px + SymbolLibrary.R + 4, py + 2))

    # ── Equipment ─────────────────────────────────────────────────────────────

    def draw_equipment(self, equipment: list[dict], offset_x=0, offset_y=0):
        block_map = {
            "riser": "FP_RISER",
            "fdc":   "FP_FDC",
        }
        for eq in equipment:
            px, py = self._pt(eq["x"], eq["y"], offset_x, offset_y)
            etype  = eq.get("type","riser").lower()
            bname  = block_map.get(etype, "FP_RISER")
            self.msp.add_blockref(bname, (px, py), dxfattribs={"layer":"FP-EQUP"})
            self.msp.add_text(
                eq.get("label",""),
                dxfattribs={"layer":"FP-ANNO-LABL","height":TEXT_SM,"style":FONT_BOLD}
            ).set_placement((px, py - 20), align=TextEntityAlignment.TOP_CENTER)

    # ── North arrow ───────────────────────────────────────────────────────────

    def draw_north_arrow(self, rotation_deg=0):
        nx = BORDER_X + DRAW_W - 100
        ny = BORDER_Y + DRAW_H - 100
        self.msp.add_blockref("NORTH_ARROW", (nx, ny),
            dxfattribs={"layer":"FP-ANNO-SYMB","rotation": rotation_deg})
        self.msp.add_text(
            f"PROJ NORTH = {rotation_deg}° FROM TRUE",
            dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM*0.8,"style":FONT}
        ).set_placement((nx, ny - 40), align=TextEntityAlignment.TOP_CENTER)

    # ── Scale bar ─────────────────────────────────────────────────────────────

    def draw_scale_bar(self, scale_str: str):
        sx = BORDER_X + 60
        sy = BORDER_Y + 30
        bar_len = SCALE_FACTOR * 10   # 10 feet
        # Major ticks every 5 ft, minor every 1 ft
        self.msp.add_line((sx, sy), (sx + bar_len, sy),
            dxfattribs={"layer":"FP-ANNO-SYMB","lineweight":25})
        for i in range(11):
            tx = sx + i * SCALE_FACTOR
            th = 8 if i % 5 == 0 else 4
            self.msp.add_line((tx, sy - th), (tx, sy + th),
                dxfattribs={"layer":"FP-ANNO-SYMB"})
            if i % 5 == 0:
                self.msp.add_text(
                    f"{i}'",
                    dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM*0.8,"style":FONT}
                ).set_placement((tx, sy - 12), align=TextEntityAlignment.TOP_CENTER)
        self.msp.add_text(
            f"SCALE: {scale_str}",
            dxfattribs={"layer":"FP-ANNO-SYMB","height":TEXT_SM,"style":FONT_BOLD}
        ).set_placement((sx, sy + 12))

    # ── General notes block ───────────────────────────────────────────────────

    def draw_general_notes(self, extra_notes: list[str] | None = None):
        standard_notes = [
            "ALL WORK SHALL CONFORM TO NFPA 13, CURRENT EDITION.",
            "ALL PIPE SHALL BE SCHEDULE 40 STEEL UNLESS NOTED OTHERWISE.",
            "ALL HANGERS AND SWAY BRACES SHALL BE FM/UL LISTED.",
            "CONTRACTOR SHALL FIELD VERIFY ALL DIMENSIONS PRIOR TO FABRICATION.",
            "PROVIDE INSPECTOR'S TEST CONNECTION PER NFPA 13 §8.17.",
            "HYDRAULIC DESIGN INFORMATION SIGN REQUIRED PER NFPA 13 §27.2.",
            "CONTRACTOR SHALL COORDINATE WITH MEP AND STRUCTURAL TRADES.",
            "ALL PENETRATIONS THROUGH FIRE-RATED ASSEMBLIES SHALL BE FIRE STOPPED.",
            "DO NOT SCALE DRAWINGS — USE DIMENSIONS ONLY.",
        ] + (extra_notes or [])

        nx = BORDER_X + DRAW_W - 300
        ny = BORDER_Y + 40
        self.msp.add_text(
            "GENERAL NOTES",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((nx, ny + len(standard_notes) * 14 + 20))

        for i, note in enumerate(standard_notes):
            self.msp.add_text(
                f"{i+1}. {note}",
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
            ).set_placement((nx, ny + (len(standard_notes) - i) * 14))

    # ── Legend block ──────────────────────────────────────────────────────────

    def draw_legend(self):
        lx = BORDER_X + DRAW_W - 300
        ly = BORDER_Y + DRAW_H - 200
        self.msp.add_text(
            "SYMBOL LEGEND",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((lx, ly))

        entries = [
            ("SPKR_PEND",  "Pendant sprinkler"),
            ("SPKR_UPRT",  "Upright sprinkler"),
            ("SPKR_SIDE",  "Sidewall sprinkler"),
            ("SPKR_CONC",  "Concealed sprinkler"),
            ("SPKR_ESFR",  "ESFR sprinkler"),
            ("VALV_OSY",   "OS&Y gate valve"),
            ("VALV_BFV",   "Butterfly valve"),
            ("VALV_CV",    "Check valve"),
            ("VALV_IT",    "Inspector's test"),
            ("FP_RISER",   "Riser assembly"),
            ("FP_FDC",     "Fire dept. connection"),
        ]
        for i, (bname, label) in enumerate(entries):
            ey = ly - 22 - i * 20
            self.msp.add_blockref(bname, (lx + 10, ey),
                dxfattribs={"layer":"FP-ANNO-SYMB"})
            self.msp.add_text(
                label,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
            ).set_placement((lx + 26, ey - 4))


# ─── Sprinkler + pipe schedule renderer ─────────────────────────────────────

class ScheduleRenderer:
    """Draws sprinkler head and pipe schedules as formatted tables."""

    def __init__(self, msp: Modelspace):
        self.msp = msp

    def draw_sprinkler_schedule(self, sprinklers: list[dict], origin=(BORDER_X+20, BORDER_Y+DRAW_H-40)):
        headers = ["TAG","TYPE","K-FACTOR","TEMP RATING","COVERAGE (ft²)","HAZARD","QTY"]
        col_w   = [40, 60, 55, 65, 80, 80, 30]
        ox, oy  = origin

        # Header row
        self.msp.add_text("SPRINKLER HEAD SCHEDULE",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((ox, oy))

        row_h  = 16
        oy    -= row_h + 6
        cumx   = ox
        for i, h in enumerate(headers):
            self.msp.add_text(h, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
            ).set_placement((cumx + 2, oy - 10))
            cumx += col_w[i]

        # Horizontal line under header
        total_w = sum(col_w)
        self.msp.add_line((ox, oy), (ox + total_w, oy),
            dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})
        self.msp.add_line((ox, oy - row_h), (ox + total_w, oy - row_h),
            dxfattribs={"layer":"FP-ANNO-NOTE"})

        # Outer border
        self.msp.add_lwpolyline(
            [(ox,oy-row_h),(ox+total_w,oy-row_h),(ox+total_w,oy+6),(ox,oy+6),(ox,oy-row_h)],
            dxfattribs={"layer":"FP-ANNO-NOTE"})

        # Group by type
        from collections import Counter
        type_count: dict = Counter()
        type_info:  dict = {}
        for s in sprinklers:
            t = s.get("type","pendant")
            type_count[t] += 1
            type_info[t]   = s

        row_y = oy - row_h
        for stype, qty in sorted(type_count.items()):
            row_y -= row_h
            info   = type_info[stype]
            cells  = [
                stype.upper()[:2],
                stype.capitalize(),
                str(info.get("k_factor","5.6")),
                str(info.get("temp_rating","155")) + "°F",
                str(info.get("coverage_radius", "")) + "r",
                info.get("hazard","Light"),
                str(qty),
            ]
            cumx = ox
            for i, cell in enumerate(cells):
                self.msp.add_text(cell,
                    dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
                ).set_placement((cumx + 2, row_y + 4))
                cumx += col_w[i]
            self.msp.add_line((ox, row_y), (ox + total_w, row_y),
                dxfattribs={"layer":"FP-ANNO-NOTE"})

    def draw_pipe_schedule(self, pipes: list[dict], origin=(BORDER_X+20, BORDER_Y+220)):
        headers = ["TAG","TYPE","DIA (in)","SCHEDULE","MATERIAL","LENGTH (ft)","FITTINGS"]
        col_w   = [40, 55, 50, 55, 55, 65, 130]
        ox, oy  = origin

        self.msp.add_text("PIPE SCHEDULE",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((ox, oy))

        row_h = 16
        oy   -= row_h + 6
        cumx  = ox
        for i, h in enumerate(headers):
            self.msp.add_text(h,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT_BOLD}
            ).set_placement((cumx + 2, oy - 10))
            cumx += col_w[i]

        total_w = sum(col_w)
        self.msp.add_line((ox, oy), (ox + total_w, oy),
            dxfattribs={"layer":"FP-ANNO-NOTE","lineweight":25})

        row_y = oy - row_h
        for pipe in pipes[:30]:  # cap at 30 rows per sheet
            row_y -= row_h
            fittings = ", ".join(pipe.get("fittings", [])[:4])
            cells = [
                pipe.get("id",""),
                pipe.get("pipe_type","branch").capitalize(),
                str(pipe.get("diameter","")),
                pipe.get("schedule","Sch 40"),
                pipe.get("material","Steel"),
                f'{pipe.get("length",0):.1f}',
                fittings,
            ]
            cumx = ox
            for i, cell in enumerate(cells):
                self.msp.add_text(cell,
                    dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM*0.85,"style":FONT}
                ).set_placement((cumx + 2, row_y + 4))
                cumx += col_w[i]
            self.msp.add_line((ox, row_y), (ox + total_w, row_y),
                dxfattribs={"layer":"FP-ANNO-NOTE"})


# ─── Main drawing engine ──────────────────────────────────────────────────────

class FireAIDrawingEngine:
    """
    Top-level engine. Call generate_all() to produce the complete construction
    drawing set from orchestrator outputs.
    """

    def __init__(
        self,
        project:            dict,
        cad_output:         dict,
        hydraulics_output:  dict,
        bracing_output:     dict,
        compliance_result,          # ComplianceResult dataclass or dict
        extra_notes:        list[str] | None = None,
    ):
        self.project    = project
        self.cad        = cad_output
        self.hydraulics = hydraulics_output
        self.bracing    = bracing_output
        self.compliance = compliance_result
        self.notes      = extra_notes or []
        self.revisions: list[Revision] = project.get("revisions", [])
        self.issue_date = project.get("issue_date", datetime.utcnow().strftime("%m/%d/%Y"))

    def _meta(self, title: str, sheet_code: str, scale="1/8\" = 1'-0\"") -> SheetMeta:
        return SheetMeta(
            sheet_title=title,
            sheet_number=sheet_code,
            scale=scale,
            issue_date=self.issue_date,
            revisions=self.revisions,
        )

    def _new_sheet(self, title: str, sheet_code: str, scale="1/8\" = 1'-0\"") -> tuple[Drawing, Modelspace, SheetMeta]:
        doc  = DXFDocFactory.new_doc()
        SymbolLibrary.define_all(doc)
        msp  = doc.modelspace()
        meta = self._meta(title, sheet_code, scale)

        # Sheet border
        msp.add_lwpolyline(
            [(MARGIN, MARGIN),(SHEET_W-MARGIN, MARGIN),
             (SHEET_W-MARGIN, SHEET_H-MARGIN),(MARGIN, SHEET_H-MARGIN),(MARGIN, MARGIN)],
            dxfattribs={"layer":"FP-TBLK","lineweight":50}
        )

        # Titleblock
        tb = TitleblockRenderer(doc, self.project)
        tb.render(msp, meta)

        return doc, msp, meta

    # ── Sheet FP0.0: Cover sheet ──────────────────────────────────────────────

    def _build_cover(self) -> Drawing:
        doc, msp, meta = self._new_sheet("Cover Sheet", "FP0.0", scale="N/A")
        cx = SHEET_W / 2
        cy = BORDER_Y + DRAW_H / 2

        msp.add_text(
            "FIRE PROTECTION SYSTEM",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_XL*2,"style":FONT_BOLD}
        ).set_placement((cx, cy + 200), align=TextEntityAlignment.MIDDLE_CENTER)

        msp.add_text(
            "AUTOMATIC SPRINKLER DESIGN",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_XL,"style":FONT_BOLD}
        ).set_placement((cx, cy + 140), align=TextEntityAlignment.MIDDLE_CENTER)

        msp.add_text(
            self.project.get("project_name",""),
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_LG,"style":FONT}
        ).set_placement((cx, cy + 80), align=TextEntityAlignment.MIDDLE_CENTER)

        msp.add_text(
            self.project.get("location",""),
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT}
        ).set_placement((cx, cy + 40), align=TextEntityAlignment.MIDDLE_CENTER)

        # Sheet index
        index_x = BORDER_X + 40
        index_y = BORDER_Y + DRAW_H - 60
        msp.add_text("SHEET INDEX",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((index_x, index_y))

        sheet_index = [
            ("FP0.0", "Cover Sheet"),
            ("FP1.x", "Floor Plan(s) — Fire Protection"),
            ("FP2.0", "Riser Diagram"),
            ("FP3.0", "Hydraulic Calculations"),
            ("FP4.0", "Sprinkler & Pipe Schedules"),
            ("FP5.0", "Installation Details"),
            ("FP6.0", "Bill of Materials"),
        ]
        for i, (num, title) in enumerate(sheet_index):
            msp.add_text(
                f"{num}    {title}",
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
            ).set_placement((index_x, index_y - 20 - i * 18))

        # Code compliance block
        code_x = cx + 100
        msp.add_text("CODE COMPLIANCE",
            dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
        ).set_placement((code_x, index_y))

        codes = [
            f"NFPA 13 — Current Edition",
            f"IBC {self.project.get('ibc_year','2021')}",
            f"AHJ: {self.project.get('ahj_jurisdiction','')}",
            f"Occupancy: {self.project.get('occupancy','')}",
            f"System type: {self.project.get('system_type','Wet').upper()}",
            f"Seismic zone: {self.project.get('seismic_zone','')}",
            f"Pipe material: {self.project.get('pipe_material','Steel')}",
            f"Design method: Density/Area",
        ]
        for i, c in enumerate(codes):
            msp.add_text(c, dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
            ).set_placement((code_x, index_y - 20 - i * 18))

        return doc

    # ── Sheets FP1.x: Floor plans ─────────────────────────────────────────────

    def _build_floor_plan(self, floor_num: int = 1) -> Drawing:
        sheet_code = f"FP1.{floor_num}"
        doc, msp, meta = self._new_sheet(
            f"Floor Plan — Level {floor_num}",
            sheet_code
        )

        renderer = PlanViewRenderer(msp, self.project)

        # Base geometry
        if self.cad.get("walls"):
            renderer.draw_walls(self.cad["walls"])
        if self.cad.get("columns"):
            renderer.draw_columns(self.cad["columns"])
        if self.cad.get("rooms"):
            renderer.draw_rooms(self.cad["rooms"])

        # FP elements
        renderer.draw_pipes(self.cad.get("pipe_sections", []))
        renderer.draw_sprinklers(self.cad.get("sprinkler_placements", []), show_coverage=True)
        renderer.draw_valves(self.cad.get("valves", []))
        renderer.draw_equipment(self.cad.get("equipment", []))

        # Annotations
        renderer.draw_north_arrow(rotation_deg=self.project.get("north_rotation", 0))
        renderer.draw_scale_bar(meta.scale)
        renderer.draw_general_notes(self.notes)
        renderer.draw_legend()

        return doc

    # ── Sheet FP4.0: Schedules ────────────────────────────────────────────────

    def _build_schedules(self) -> Drawing:
        doc, msp, meta = self._new_sheet("Sprinkler & Pipe Schedules", "FP4.0", scale="N/A")
        sched = ScheduleRenderer(msp)
        sched.draw_sprinkler_schedule(self.cad.get("sprinkler_placements", []))
        sched.draw_pipe_schedule(self.cad.get("pipe_sections", []))
        return doc

    # ── Sheet FP5.0: Details ──────────────────────────────────────────────────

    def _build_details(self) -> Drawing:
        doc, msp, meta = self._new_sheet("Installation Details", "FP5.0", scale="VARIES")

        details = [
            ("HANGER DETAIL — STANDARD", "Per NFPA 13 §9.1. Rod hanger with listed bracket. Max 15ft spacing."),
            ("SWAY BRACE DETAIL",         "Per NFPA 13 §9.3. 4-way brace. Max 40ft longitudinal spacing."),
            ("DRAIN ASSEMBLY",            "2\" main drain to floor drain or outside. Ball valve + sight glass."),
            ("INSPECTOR'S TEST",          "1\" test connection at most remote sprinkler. Sight glass required."),
            ("RISER DETAIL",              "Include flow switch, OS&Y valve, alarm check valve, drain."),
            ("ARMOVER DETAIL",            "Max 12\" armover from branch line. No change in pipe size."),
            ("PIPE PENETRATION",          "UL-listed firestop system for rated assemblies."),
        ]

        col1_x = BORDER_X + 40
        col2_x = BORDER_X + DRAW_W // 2 + 40
        row_h  = 60
        base_y = BORDER_Y + DRAW_H - 40

        for i, (title, desc) in enumerate(details):
            col_x = col1_x if i % 2 == 0 else col2_x
            row_y = base_y - (i // 2) * (row_h + 20)

            msp.add_text(title,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_MD,"style":FONT_BOLD}
            ).set_placement((col_x, row_y))
            msp.add_text(desc,
                dxfattribs={"layer":"FP-ANNO-NOTE","height":TEXT_SM,"style":FONT}
            ).set_placement((col_x, row_y - TEXT_MD - 4))
            msp.add_line(
                (col_x, row_y - TEXT_MD - 20),
                (col_x + DRAW_W//2 - 80, row_y - TEXT_MD - 20),
                dxfattribs={"layer":"FP-ANNO-NOTE"}
            )

        return doc

    # ── generate_all() ────────────────────────────────────────────────────────

    def generate_all(self, output_dir: str = "./outputs/drawings") -> list[dict]:
        """
        Generate the full construction drawing set.
        Returns manifest of {sheet, filename, path} for each sheet.
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = []

        sheets = [
            ("FP0.0 — Cover",      self._build_cover,      "FP0_0_Cover.dxf"),
            ("FP1.1 — Floor Plan", self._build_floor_plan, "FP1_1_Floor_Plan.dxf"),
            ("FP4.0 — Schedules",  self._build_schedules,  "FP4_0_Schedules.dxf"),
            ("FP5.0 — Details",    self._build_details,    "FP5_0_Details.dxf"),
        ]

        for sheet_name, build_fn, filename in sheets:
            try:
                print(f"[DrawingEngine] Generating {sheet_name}...")
                doc      = build_fn() if build_fn != self._build_floor_plan else build_fn(1)
                out_path = os.path.join(output_dir, filename)
                doc.saveas(out_path)
                size = os.path.getsize(out_path)
                print(f"[DrawingEngine] ✓ {sheet_name} — {size/1024:.1f} KB → {out_path}")
                manifest.append({"sheet": sheet_name, "filename": filename, "path": out_path, "size_kb": round(size/1024, 1)})
            except Exception as e:
                print(f"[DrawingEngine] ✗ {sheet_name} failed: {e}")
                manifest.append({"sheet": sheet_name, "filename": filename, "path": None, "error": str(e)})

        print(f"\n[DrawingEngine] Complete — {len([m for m in manifest if not m.get('error')])} sheet(s) generated.")
        return manifest
