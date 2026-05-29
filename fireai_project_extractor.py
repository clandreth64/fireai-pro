"""
FireAI Pro — Project Document Extractor
========================================
Automatically extracts all fire sprinkler design parameters from an uploaded
architectural document set (PDF, DXF, IFC, spec section).

Pipeline:
  1. TEXT PASS    — extract all text, regex-match every known field
  2. PAGE FINDER  — identify cover/code-data/floor-plan/structural pages
  3. VISION PASS  — render key pages, call Claude Vision for rooms + grid
  4. SYNTHESIZER  — merge into complete project_context dict

Called by api/app.py before NFPA13DesignEngine.
Designed to work on any project — no hardcoded values.
"""

from __future__ import annotations
import asyncio
import base64
import json
import logging
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("fireai.project_extractor")

# ─── Optional dependencies ────────────────────────────────────────────────────
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# ─── Text extraction ──────────────────────────────────────────────────────────

def extract_text_all_pages(pdf_path: str, max_pages: int = 15) -> str:
    """
    Extract text from a PDF for project data extraction.

    Strategy:
      - For large multi-discipline sets (>20 pages), extract only the first
        max_pages pages. Architectural/code data sheets are almost always in
        the first 1/6 of the document.
      - For small single-discipline specs (≤20 pages), extract everything.

    This avoids noise from civil/mechanical/electrical sheets that share the
    same PDF file and can corrupt field extraction (wrong system type, wrong
    pipe material, wrong project name).
    """
    page_count = get_page_count(pdf_path)
    is_large = page_count > 20

    if is_large:
        # Extract first max_pages pages (cover + general notes + arch floor plan)
        result = subprocess.run(
            ["pdftotext", "-layout", "-f", "1", "-l", str(max_pages), pdf_path, "-"],
            capture_output=True, timeout=60
        )
    else:
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, timeout=60
        )

    if result.returncode == 0:
        text = result.stdout.decode("utf-8", errors="replace")
        log.info("[Extractor] Text: %d chars, pages 1-%d of %d",
                 len(text), max_pages if is_large else page_count, page_count)
        return text

    # Fallback: pdfplumber on first max_pages
    if HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                pages = []
                limit = min(len(pdf.pages), max_pages if is_large else 999)
                for p in pdf.pages[:limit]:
                    t = p.extract_text()
                    if t: pages.append(t)
                return "\n".join(pages)
        except Exception as e:
            log.warning("[Extractor] pdfplumber fallback failed: %s", e)
    return ""


def get_page_count(pdf_path: str) -> int:
    result = subprocess.run(["pdfinfo", pdf_path], capture_output=True)
    for line in result.stdout.decode().splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    return 0


# ─── Text-based field extractors ─────────────────────────────────────────────

class TextExtractor:
    """
    Extracts structured project data from raw PDF text using regex patterns.
    Patterns are ordered by specificity — first match wins.
    Designed to work for any standard construction document set.
    """

    def __init__(self, text: str):
        self.text = text
        self.upper = text.upper()

    def _first(self, *patterns, group=1, default=None, flags=re.I):
        for p in patterns:
            m = re.search(p, self.text, flags)
            if m:
                try:
                    return m.group(group).strip()
                except IndexError:
                    return m.group(0).strip()
        return default

    def _number(self, *patterns, default=None):
        raw = self._first(*patterns, default=None)
        if raw is None: return default
        # Strip commas, extract first number-like string
        nums = re.findall(r'[\d,]+\.?\d*', raw.replace(",",""))
        if nums:
            try: return float(nums[0].replace(",",""))
            except: pass
        return default

    def extract_project_name(self) -> str:
        # Look for "PROJECT:" followed by text, or large title text
        name = self._first(
            r'(?:PROJECT\s*NAME|PROJECT):\s*([^\n]{5,80})',
            r'(?:FOR|PROJECT):\s*([A-Z][^\n]{5,60})',
        )
        # Also try: first line with "Costco" or similar brand
        if not name:
            m = re.search(r'^([A-Z][A-Za-z ]+(?:Wholesale|Store|Warehouse|School|Hospital|Office)[^\n]{0,40})', self.text, re.M)
            if m: name = m.group(1).strip()
        return (name or "").strip(", ") or "Unknown Project"

    def extract_address(self) -> str:
        # Common patterns for addresses in AE docs
        addr = self._first(
            r'(?:PROJECT ADDRESS|ADDRESS|LOCATION):\s*([^\n]{10,100})',
            r'(\d+\s+[A-Z][^\n]{10,60}(?:ST|AVE|BLVD|DR|RD|WAY|LANE|LN|PKWY)[^\n]{0,40})',
            flags=re.I
        )
        return (addr or "").strip()

    def extract_owner(self) -> str:
        return (self._first(
            r'(?:OWNER|CLIENT|DEVELOPER):\s*([^\n]{5,80})',
            r'(?:PROPERTY OWNER|BUILDING OWNER):\s*([^\n]{5,80})',
        ) or "").strip()

    def extract_occupancy(self) -> str:
        # IBC occupancy group. The previous regexes were too permissive:
        # "OCCUPANCY ... JULY 30, 2019" matched and stamped "JULY 30, 2019"
        # as the occupancy because (a) the patterns ran across line breaks
        # and (b) there was no validation of the captured value.
        #
        # Fix: anchor patterns to a single line (no \n in the value capture),
        # then validate that the result actually looks like an IBC occupancy
        # group ('A', 'B', 'S-1', 'Mercantile (Group M)', etc.).
        raw = self._first(
            r'OCCUPANCY\s*(?:GROUP|CLASS(?:IFICATION)?)\s*[:=]\s*([^\n\r]{2,80})',
            r'(?:USE\s*&?\s*OCCUPANCY|OCCUPANCY\s+TYPE)\s*[:=]\s*([^\n\r]{2,80})',
            r'\bGROUP\s+((?:[A-HRS]-?\d?)\b[^\n\r]{0,30})',
        )
        if not raw:
            return ""
        candidate = raw.strip().strip(",.;").strip()

        # Reject obvious false positives: a date, a year, a state name,
        # a code reference, or anything that doesn't contain an occupancy
        # signal (a single IBC group letter A-H/I/M/R/S/U or a known word).
        if re.search(r'\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\s+\d',
                     candidate, re.I):
            return ""
        if re.fullmatch(r'\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', candidate):
            return ""
        if re.search(r'\b(IBC|CBC|CFC|NFPA|TABLE|CHAPTER|SECTION)\b', candidate, re.I):
            return ""

        # Must contain at least one IBC occupancy letter or known word.
        # Single-letter groups: A, B, E, F, H, I, M, R, S, U
        OCCUPANCY_WORDS = (
            "MERCANTILE", "BUSINESS", "ASSEMBLY", "EDUCATIONAL", "FACTORY",
            "HAZARD", "INSTITUTIONAL", "RESIDENTIAL", "STORAGE", "UTILITY",
            "OFFICE", "RETAIL", "WAREHOUSE", "INDUSTRIAL", "GROUP",
        )
        has_letter = bool(re.search(r'\b(GROUP\s+)?([ABEFHIMRSU])(?:-\d)?\b', candidate))
        has_word   = any(w in candidate.upper() for w in OCCUPANCY_WORDS)
        if not (has_letter or has_word):
            return ""
        return candidate

    def extract_construction_type(self) -> str:
        ct = self._first(
            r'(?:TYPE\s+OF\s+CONSTRUCTION|CONSTRUCTION\s+TYPE)\s*[:\.]?\s*([IVA-Z-]+(?:\s*[AB])?)',
            r'TYPE\s+((?:I|II|III|IV|V)-?[A-B])',
            r'TABLE\s+601.*?TYPE\s+((?:I|II|III|IV|V)-?[A-B])',
        )
        return (ct or "").strip()

    def extract_total_area(self) -> Optional[float]:
        # Look for gross building area, total sq ft, etc.
        for pat in [
            r'(?:TOTAL\s+BUILDING\s*[-–]\s*GROSS|GROSS\s+(?:BUILDING\s+)?AREA|TOTAL\s+SQ\s*FT)[^\d]*?([\d,]+)\s*SF',
            r'(?:BUILDING\s+AREA|GROSS\s+FLOOR\s+AREA)[^\d]*?([\d,]+)\s*(?:SF|SQ\s*FT)',
            r'([\d,]{5,})\s*(?:SF|SQ\s*FT)[^\n]*?(?:TOTAL|GROSS|BUILDING)',
            r'(?:IS\s+A|PROJECT\s+IS)\s+A?\s*([\d,]+)\s*SQUARE\s*FEET',
        ]:
            m = re.search(pat, self.text, re.I)
            if m:
                try:
                    return float(m.group(1).replace(",",""))
                except: pass
        return None

    def extract_building_height(self) -> Optional[float]:
        # Max building height above floor
        for pat in [
            r'(?:MAX(?:IMUM)?\s+BUILDING\s+HEIGHT|ACTUAL|BUILDING\s+HEIGHT)\s*(?:ABOVE\s+FLOOR)?[:\s]*(\d+)[\'`][\s-]*(\d+)?',
            r'(\d+)[\'`]-?\d*\s*FEET?\s*[-–]\s*\d+\s*STOR',
        ]:
            m = re.search(pat, self.text, re.I)
            if m:
                ft = int(m.group(1))
                inch = int(m.group(2)) if m.lastindex >= 2 and m.group(2) else 0
                return round(ft + inch/12, 1)
        return None

    def extract_number_of_stories(self) -> int:
        m = re.search(r'(\d+)\s*STOR(?:Y|IES)', self.text, re.I)
        return int(m.group(1)) if m else 1

    def extract_system_type(self) -> str:
        text = self.upper
        if "DRY PIPE" in text: return "Dry Pipe"
        if "DELUGE" in text: return "Deluge"
        if "PRE-ACTION" in text or "PREACTION" in text: return "Pre-Action"
        return "Wet Pipe"

    def extract_pipe_material(self) -> str:
        text = self.upper
        # CPVC, copper, or steel
        if "SCHEDULE 10" in text and "SCHEDULE 40" in text:
            return "Schedule 10 Steel (welded mains) / Schedule 40 Steel (screwed branches)"
        if "SCHEDULE 10" in text: return "Schedule 10 Steel"
        if "CPVC" in text: return "CPVC"
        if "COPPER" in text: return "Type L Copper"
        if "SCHEDULE 40" in text: return "Schedule 40 Steel"
        return "Schedule 40 Steel"

    def extract_structural_framing(self) -> str:
        text = self.upper
        # Ordered by specificity
        if "OPEN WEB STEEL JOIST" in text or "OWSJ" in text: return "open_web_steel_joist"
        if "METAL BUILDING" in text or "PRE-ENGINEERED" in text or "MBS" in text or "BUTLER" in text: return "open_web_steel_joist"
        if "I-JOIST" in text or "TJI" in text or "RED-I" in text: return "i_joist"
        if "GLU-LAM" in text or "GLULAM" in text or "CLT" in text: return "glulam"
        if "WOOD JOIST" in text or "WOOD FRAMING" in text: return "wood_joist"
        if "STEEL BEAM" in text or "W-SHAPE" in text: return "steel_beam"
        if "CONCRETE" in text and "JOIST" not in text: return "concrete_deck"
        if "JOIST" in text: return "open_web_steel_joist"
        return "default"

    def extract_ceiling_height(self) -> Optional[float]:
        # Look for clearance heights, AFF dimensions
        # Priority: "XX'-Y" AFF" patterns near "clear" or "height"
        best = None
        for pat in [
            # "22'-0" AFF MIN CLEAR" type
            r'(\d+)[\'`]-?(\d+)?[\""]?\s*(?:A\.?F\.?F\.?|AFF)\s*(?:MIN|CLEAR|TYP)',
            # "CEILING HEIGHT: 12'-0""
            r'(?:CEILING\s+HEIGHT|CLEAR\s+HEIGHT)[:\s]+(\d+)[\'`]-?(\d+)?',
            # "32'-6" FEET" (max building height)
            r'MAX(?:IMUM)?\s+(?:BUILDING\s+)?HEIGHT[:\s]+(\d+)[\'`]-?(\d+)?',
        ]:
            m = re.search(pat, self.text, re.I)
            if m:
                ft = int(m.group(1))
                inch = int(m.group(2)) if m.lastindex >= 2 and m.group(2) else 0
                h = round(ft + inch/12, 1)
                if best is None or h < best:  # use the minimum height (most conservative)
                    best = h
        return best

    def extract_seismic_zone(self) -> str:
        m = re.search(r'SEISMIC\s*(?:ZONE|DESIGN\s*CATEGORY|SDC)[:\s]*([A-Fa-f0-9.]+)', self.text, re.I)
        if m: return m.group(1).strip().upper()
        # State-based defaults
        state_zones = {
            "CA": "D1", "OR": "D1", "WA": "D1", "NV": "D1", "AK": "D2",
            "UT": "C",  "AZ": "C",  "MT": "B",  "ID": "C",
            "ND": "B",  "SD": "B",  "MN": "B",  "WI": "B",  "MI": "B",
            "IL": "B",  "OH": "B",  "IN": "B",  "IA": "B",  "NE": "B",
            "TX": "B",  "OK": "C",  "KS": "B",  "MO": "C",
            "SC": "D1", "TN": "C",  "AL": "B",  "GA": "B",  "FL": "B",
        }
        for state, zone in state_zones.items():
            if re.search(r'\b' + state + r'\b', self.text[:2000]):
                return zone
        return "D1"  # conservative default

    def extract_ahj(self) -> str:
        ahj = self._first(
            r'(?:AUTHORITY\s+HAVING\s+JURISDICTION|AHJ)[:\s]+([^\n]{5,80})',
            r'(?:BUILDING\s+DEPARTMENT|FIRE\s+DEPARTMENT)[:\s]+([^\n]{5,60})',
            r'(?:CITY|COUNTY)\s+OF\s+([A-Za-z ]+)(?:\s+(?:BUILDING|FIRE))',
        )
        return (ahj or "").strip()

    def extract_codes(self) -> list:
        codes = []
        code_patterns = [
            r'(20\d\d\s+(?:CALIFORNIA|INTERNATIONAL)\s+BUILDING\s+CODE[^\n]*)',
            r'(20\d\d\s+(?:CALIFORNIA|INTERNATIONAL)\s+FIRE\s+CODE[^\n]*)',
            r'(NFPA\s+13[^\n]{0,50})',
            r'(IBC\s+20\d\d[^\n]{0,40})',
            r'(IFC\s+20\d\d[^\n]{0,40})',
            r'(CBC\s+20\d\d[^\n]{0,40})',
            r'(CFC\s+20\d\d[^\n]{0,40})',
        ]
        seen = set()
        for pat in code_patterns:
            for m in re.finditer(pat, self.text, re.I):
                code = m.group(1).strip()
                key = re.sub(r'\s+', ' ', code[:30]).upper()
                if key not in seen:
                    seen.add(key)
                    codes.append(code)
        return codes[:8]

    def extract_spare_heads(self) -> int:
        # NFPA 13: 0-300 = 6, 300-1000 = 12, >1000 = 24
        m = re.search(r'(\d+)\s*SPARE\s*SPRINKLER', self.text, re.I)
        if m: return int(m.group(1))
        # Infer from system size
        area = self.extract_total_area() or 0
        heads_est = area / 100  # rough heads per area
        if heads_est < 300: return 6
        if heads_est < 1000: return 12
        return 24

    def extract_sprinkler_manufacturer(self) -> str:
        text = self.upper
        # Check for specific manufacturer mentions
        for mfr in ["VIKING", "TYCO", "VICTAULIC", "CENTRAL", "RELIABLE", "SENJU"]:
            if mfr in text: return mfr.capitalize()
        return "Viking"  # industry default

    def extract_hazard_from_text(self) -> str:
        text = self.upper
        # ESFR indicators
        if any(kw in text for kw in ["ESFR", "EC-25", "EC25", "HIGH-PILED", "HIGH PILED",
                                      "HIGH-PILE", "K-25", "K25", "K-17", "K17",
                                      "STORAGE RACK", "PALLET RACK", "HIGH STORAGE"]):
            return "esfr_k14"  # may be upgraded below
        # ESFR K25 specifically
        if "K25" in text or "K-25" in text or "25.2" in text:
            return "esfr_k25"
        # Ordinary indicators
        if any(kw in text for kw in ["ORDINARY GROUP 2", "ORD. GROUP 2", "OHH", "ORD 2",
                                      "KITCHEN", "FOOD SERVICE", "LAUNDRY"]):
            return "ordinary_2"
        if any(kw in text for kw in ["ORDINARY GROUP 1", "ORD. GROUP 1", "OHH", "ORD 1",
                                      "RETAIL", "MERCANTILE", "WAREHOUSE WITHOUT STORAGE",
                                      "PARKING", "MANUFACTURING"]):
            return "ordinary_1"
        # Occupancy-based inference
        if any(kw in text for kw in ["MERCANTILE", "COSTCO", "WALMART", "HOME DEPOT",
                                      "WAREHOUSE STORE", "DISTRIBUTION"]):
            return "esfr_k14"
        if any(kw in text for kw in ["OFFICE", "EDUCATIONAL", "SCHOOL", "HOSPITAL",
                                      "HEALTHCARE", "RESIDENTIAL"]):
            return "light"
        return "ordinary_1"

    def extract_rack_height(self) -> Optional[float]:
        m = re.search(r'(?:RACK|STORAGE|PALLET)\s+(?:HEIGHT|RACK\s+TO)\s*[:\s]*(\d+)[\'`]', self.text, re.I)
        if m: return float(m.group(1))
        # Also look for "15 ft" type patterns near rack keywords
        m2 = re.search(r'(?:RACK|STORAGE)\s+(?:TO|HEIGHT)\s+(\d+)[\'`\s]+(?:AFF|HIGH|MAX)', self.text, re.I)
        if m2: return float(m2.group(1))
        return None

    def extract_nfpa_edition(self) -> str:
        m = re.search(r'NFPA\s*(?:#?\s*)?13\s*[\(\-]?\s*(20\d\d)[^\)]*(?:ED(?:ITION)?\.?)?', self.text, re.I)
        if m: return m.group(1) + " Edition"
        m2 = re.search(r'NFPA\s*13\s*\(?(20\d\d)', self.text, re.I)
        if m2: return m2.group(1) + " Edition"
        return "Current Edition"

    def extract_ibc_year(self) -> str:
        m = re.search(r'(20\d\d)\s+(?:INTERNATIONAL|IBC)', self.text, re.I)
        if m: return m.group(1)
        m2 = re.search(r'IBC\s+(20\d\d)', self.text, re.I)
        if m2: return m2.group(1)
        return "2021"

    def extract_designer_info(self) -> dict:
        name = self._first(
            r'(?:DESIGNER|DRAWN BY|PREPARED BY)[:\s]+([A-Za-z\s,\.]+(?:PE|NICET|P\.E\.)[^\n]*)',
            r'(?:DESIGNER|ENGINEER OF RECORD)[:\s]+([^\n]{5,60})',
        )
        cert = self._first(
            r'(?:NICET\s+(?:LEVEL|CERT|CERTIFICATION)?[:\s#]*(\w+[^\n]{0,30}))',
            r'(?:LICENSE|CERT(?:IFICATION)?)[:\s#]+([^\n]{5,40})',
        )
        return {"name": (name or "").strip(), "cert": (cert or "").strip()}

    def extract_construction_type_fr(self) -> str:
        """Extract construction type from fire resistance table or code data."""
        m = re.search(
            r'(?:TABLE\s+601|CONSTRUCTION\s+TYPE|TYPE\s+OF\s+CONSTRUCTION)[^\n]*\n?[^\n]*'
            r'TYPE\s+((?:I|II|III|IV|V)-?[A-B])',
            self.text, re.I
        )
        if m: return "Type " + m.group(1)
        m2 = re.search(r'\bTYPE\s+((?:I|II|III|IV|V)[AB]?)\b', self.text, re.I)
        if m2: return "Type " + m2.group(1)
        return ""

    def extract_all(self) -> dict:
        """Run all extractors and return partial project_context dict."""
        ct = self.extract_construction_type() or self.extract_construction_type_fr()
        return {
            "project_name":       self.extract_project_name(),
            "location":           self.extract_address(),
            "owner":              self.extract_owner(),
            "occupancy":          self.extract_occupancy(),
            "construction_type":  ct,
            "number_of_stories":  self.extract_number_of_stories(),
            "total_area":         self.extract_total_area(),
            "ceiling_height":     self.extract_ceiling_height(),
            "system_type":        self.extract_system_type(),
            "pipe_material":      self.extract_pipe_material(),
            "structural_framing": self.extract_structural_framing(),
            "seismic_zone":       self.extract_seismic_zone(),
            "ahj_jurisdiction":   self.extract_ahj(),
            "applicable_codes":   self.extract_codes(),
            "spare_heads":        self.extract_spare_heads(),
            "sprinkler_manufacturer": self.extract_sprinkler_manufacturer(),
            "warehouse_hazard":   self.extract_hazard_from_text(),
            "rack_height":        self.extract_rack_height(),
            "nfpa_edition":       self.extract_nfpa_edition(),
            "ibc_year":           self.extract_ibc_year(),
            "designer":           self.extract_designer_info(),
        }


# ─── Page classifier ──────────────────────────────────────────────────────────

class PageClassifier:
    """
    Identifies the most useful pages in a multi-page PDF for:
    - cover / code data page (project data, occupancy, construction type)
    - main floor plan (room layout, dimensions)
    - structural / roof framing plan
    - fire sprinkler plan (if present — for reference)
    """

    COVER_KEYWORDS   = {"DRAWING INDEX","SHEET INDEX","CODE DATA","PROJECT DATA",
                        "OCCUPANCY","EGRESS PLAN","GENERAL NOTES","BUILDING STATISTICS"}
    FLOOR_KEYWORDS   = {"FLOOR PLAN","SALES FLOOR","PLAN VIEW","GROUND FLOOR",
                        "SCALE: 1/8","SCALE: 1/16","A101","A-101","FP-1","FP1"}
    STRUCT_KEYWORDS  = {"FRAMING PLAN","ROOF FRAMING","FOUNDATION PLAN","JOIST",
                        "STRUCTURAL PLAN","S1.","S1-","STEEL FRAMING"}
    FIRE_KEYWORDS    = {"FIRE SPRINKLER","SPRINKLER PLAN","FA101","FA-101",
                        "SPRINKLER PIPING PLAN","PIPING PLAN","FP-3","FP3"}

    def __init__(self, pdf_path: str):
        self.pdf_path   = pdf_path
        self.page_count = get_page_count(pdf_path)

    def _page_text(self, page_num: int) -> str:
        """Extract text from a single page (1-indexed)."""
        result = subprocess.run(
            ["pdftotext", "-f", str(page_num), "-l", str(page_num), self.pdf_path, "-"],
            capture_output=True, timeout=30
        )
        return result.stdout.decode("utf-8", errors="replace").upper() if result.returncode == 0 else ""

    def _score_page(self, text: str, keywords: set) -> int:
        return sum(1 for kw in keywords if kw in text)

    def _score_geometry(self, page_num: int) -> int:
        """Score a page by its line/curve count (more geometry = more likely a plan)."""
        if not HAS_PDFPLUMBER:
            return 0
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                if page_num-1 >= len(pdf.pages): return 0
                p = pdf.pages[page_num-1]
                return len(p.lines) + len(p.curves)
        except Exception:
            return 0

    def find_pages(self) -> dict:
        """
        Scan pages efficiently and return:
          cover / floor_plan / structural / fire_sprinkler page numbers (1-indexed).

        Strategy:
          1. Text-score every page quickly (pdftotext is fast)
          2. Get geometry score ONLY for top-5 floor-plan text candidates
             (geometry scan is slow on large PDFs)
          3. Combine text + geometry to pick final winner
        """
        scan_limit = min(self.page_count, 80)

        cover_scores   = []
        floor_scores   = []
        struct_scores  = []
        fire_scores    = []

        for pg in range(1, scan_limit + 1):
            text = self._page_text(pg)
            c  = self._score_page(text, self.COVER_KEYWORDS)
            f  = self._score_page(text, self.FLOOR_KEYWORDS)
            s  = self._score_page(text, self.STRUCT_KEYWORDS)
            fp = self._score_page(text, self.FIRE_KEYWORDS)
            cover_scores.append((c, pg))
            floor_scores.append((f, pg))
            struct_scores.append((s, pg))
            fire_scores.append((fp, pg))

        # Geometry pass on top-10 floor-plan text candidates
        top_floor_pgs = [pg for _, pg in sorted(floor_scores, reverse=True)[:10]]
        geo_scores = {}
        for pg in top_floor_pgs:
            geo_scores[pg] = self._score_geometry(pg)

        # Re-score floor plan candidates with geometry bonus
        floor_combined = []
        for text_score, pg in floor_scores:
            geo = geo_scores.get(pg, 0)
            floor_combined.append((text_score * 2 + geo // 500, pg))

        # Same for fire sprinkler pages
        top_fire_pgs = [pg for _, pg in sorted(fire_scores, reverse=True)[:5]]
        for pg in top_fire_pgs:
            if pg not in geo_scores:
                geo_scores[pg] = self._score_geometry(pg)

        fire_combined = []
        for text_score, pg in fire_scores:
            geo = geo_scores.get(pg, 0)
            fire_combined.append((text_score * 2 + geo // 1000, pg))

        result = {}
        for key, scored in [
            ("cover",         cover_scores),
            ("floor_plan",    floor_combined),
            ("structural",    struct_scores),
            ("fire_sprinkler",fire_combined),
        ]:
            best = max(scored, key=lambda x: x[0])
            result[key] = best[1] if best[0] > 0 else None
            log.info("[PageClassifier] %-15s → page %-4s (score %d)",
                     key, best[1], best[0])

        return result


# ─── Vision extractor ─────────────────────────────────────────────────────────

FLOOR_PLAN_VISION_PROMPT = """You are a fire protection engineer reading an architectural floor plan.
Extract EVERY room, space, and area visible in this drawing.

Return ONLY valid JSON (no markdown):
{
  "building_dimensions": {
    "width_ft": NUMBER,
    "depth_ft": NUMBER,
    "notes": "any dimension string you can read from the drawing"
  },
  "drawing_scale": "e.g. 1/8\\" = 1'-0\\"",
  "structural_grid": {
    "columns": [{"label": "1", "x_ft": 0}, {"label": "2", "x_ft": 50}],
    "rows":    [{"label": "A", "y_ft": 0}, {"label": "B", "y_ft": 40}]
  },
  "rooms": [
    {
      "name": "WAREHOUSE",
      "tag": "101",
      "hazard_classification": "esfr_k14",
      "boundary": [{"x": 0,"y": 0},{"x": 200,"y": 0},{"x": 200,"y": 150},{"x": 0,"y": 150}],
      "area_sf": 30000,
      "ceiling_height_ft": 30,
      "notes": "high-pile rack storage"
    }
  ],
  "walls": []
}

HAZARD RULES:
- warehouse / distribution / bulk retail with racks → esfr_k14 (or esfr_k25 if K25 mentioned)
- high-pile storage (racks > 12 ft) → esfr_k14
- mercantile sales floor (no high racks) → ordinary_1
- kitchen / food service / laundry → ordinary_2
- office / corridor / classroom / restroom → light
- parking / mechanical / receiving → ordinary_1
- walk-in cooler / freezer → cooler or freezer
- tire installation / auto service → ordinary_1

COORDINATE SYSTEM: (0,0) = bottom-left, X = right, Y = up, all in FEET.
Read dimension strings and column/row grid labels to compute real coordinates.
Cover the ENTIRE building footprint — no gaps."""

PROJECT_DATA_VISION_PROMPT = """Read this architectural cover sheet or code data page.
Extract every piece of project information visible.

Return ONLY valid JSON:
{
  "project_name": "",
  "project_number": "",
  "address": "",
  "city_state": "",
  "owner": "",
  "architect": "",
  "occupancy_group": "",
  "construction_type": "",
  "number_of_stories": 1,
  "building_area_sf": 0,
  "building_height_ft": 0,
  "automatic_sprinkler": true,
  "applicable_codes": [],
  "general_notes": [],
  "structural_framing": "",
  "seismic_zone": "",
  "ahj": "",
  "spare_heads": 0,
  "ibc_year": "",
  "nfpa_13_edition": "",
  "pipe_material": "",
  "issue_date": "",
  "drawn_by": "",
  "project_manager": ""
}
Return null for any field not visible in the drawing."""


async def _call_vision(image_bytes: bytes, prompt: str, model: str = "claude-opus-4-5") -> dict:
    """Send an image to Claude Vision API and parse JSON response."""
    if not HAS_ANTHROPIC:
        log.warning("[Vision] anthropic not installed")
        return {}

    # Resize if needed (Vision limit ~5MB)
    if len(image_bytes) > 4_500_000:
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            scale = min(2800/img.width, 2800/img.height, 1.0)
            if scale < 1.0:
                img = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=85)
                image_bytes = buf.getvalue()
        except Exception as e:
            log.warning("[Vision] Resize failed: %s", e)

    b64 = base64.standard_b64encode(image_bytes).decode()
    client = anthropic.AsyncAnthropic()
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt},
                ]
            }]
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.M)
        raw = re.sub(r'```\s*$', '', raw, flags=re.M)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("[Vision] JSON parse error: %s", e)
        return {}
    except Exception as e:
        log.warning("[Vision] API error: %s", e)
        return {}


def _render_page(pdf_path: str, page_num: int, dpi: int = 100) -> Optional[bytes]:
    """Render a PDF page to JPEG bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page"
        result = subprocess.run(
            ["pdftoppm", "-jpeg", "-r", str(dpi),
             "-f", str(page_num), "-l", str(page_num),
             pdf_path, str(out)],
            capture_output=True, timeout=60
        )
        if result.returncode != 0:
            return None
        files = sorted(Path(tmp).glob("*.jpg"))
        return files[0].read_bytes() if files else None


# ─── Data synthesizer ─────────────────────────────────────────────────────────

def _merge(base: dict, override: dict) -> dict:
    """Merge override into base, skipping None/empty values."""
    result = dict(base)
    for k, v in override.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        result[k] = v
    return result


def _infer_hazard_from_rooms(rooms: list) -> str:
    """Infer primary hazard classification from room list."""
    if not rooms: return "ordinary_1"
    # Check for ESFR rooms
    for r in rooms:
        hz = r.get("hazard_classification","").lower()
        if "esfr" in hz or "high" in hz:
            return hz
    # Most common non-light hazard
    hz_counts = {}
    for r in rooms:
        hz = r.get("hazard_classification","ordinary_1")
        area = r.get("area_sf",0) or 0
        hz_counts[hz] = hz_counts.get(hz,0) + area
    if not hz_counts: return "ordinary_1"
    return max(hz_counts, key=hz_counts.get)


def synthesize_context(text_data: dict, cover_data: dict, floor_data: dict) -> dict:
    """
    Merge text extraction, vision cover sheet data, and vision floor plan data
    into a single complete project_context dict.
    Priority: floor_plan_vision > cover_vision > text_extraction
    """
    # Start with text extraction (lowest priority)
    ctx = dict(text_data)

    # Merge cover sheet vision data
    if cover_data:
        overrides = {
            "project_name":      cover_data.get("project_name"),
            "location":          cover_data.get("address") or cover_data.get("city_state"),
            "owner":             cover_data.get("owner"),
            "occupancy":         cover_data.get("occupancy_group"),
            "construction_type": cover_data.get("construction_type"),
            "total_area":        cover_data.get("building_area_sf"),
            "ceiling_height":    cover_data.get("building_height_ft"),
            "number_of_stories": cover_data.get("number_of_stories"),
            "applicable_codes":  cover_data.get("applicable_codes"),
            "ahj_jurisdiction":  cover_data.get("ahj"),
            "spare_heads":       cover_data.get("spare_heads"),
            "ibc_year":          cover_data.get("ibc_year"),
            "nfpa_edition":      cover_data.get("nfpa_13_edition"),
            "pipe_material":     cover_data.get("pipe_material"),
            "issue_date":        cover_data.get("issue_date"),
            "structural_framing":cover_data.get("structural_framing"),
        }
        ctx = _merge(ctx, {k: v for k, v in overrides.items() if v})

    # Merge floor plan vision data (highest priority for geometry)
    if floor_data:
        rooms  = floor_data.get("rooms",  [])
        walls  = floor_data.get("walls",  [])
        bd     = floor_data.get("building_dimensions", {})
        sg     = floor_data.get("structural_grid", {})

        if rooms:
            ctx["rooms"] = rooms
            # Infer overall hazard from room data
            if ctx.get("warehouse_hazard") in (None,"","ordinary_1"):
                ctx["warehouse_hazard"] = _infer_hazard_from_rooms(rooms)
        if walls:
            ctx["walls"] = walls
        if bd.get("width_ft"):
            ctx["building_width_ft"]  = bd["width_ft"]
            ctx["building_depth_ft"]  = bd.get("depth_ft", bd["width_ft"] * 0.65)
        if sg:
            ctx["structural_grid"] = sg

    # Derived fields
    if not ctx.get("warehouse_hazard"):
        ctx["warehouse_hazard"] = ctx.get("default_hazard", "ordinary_1")

    # Designer-decision defaults — these are reasonable starting points the
    # designer will confirm; they're labeled clearly downstream as defaults.
    designer_defaults = {
        "system_type":         "Wet Pipe",
        "floors":              ctx.get("number_of_stories", 1),
        "spare_heads":         12,
        "sprinkler_manufacturer": "Viking",
        "pipe_material":       "Schedule 40 Steel",
        "structural_framing":  "default",
        "nfpa_edition":        "Current Edition",
        "ibc_year":            "2021",
    }
    for k, v in designer_defaults.items():
        if not ctx.get(k):
            ctx[k] = v

    # CRITICAL — water supply values must NEVER be auto-populated.
    # The previous version stamped static_pressure=72, residual_pressure=60,
    # water_supply_flow=1500 on every project regardless of source data.
    # These numbers come from a hydrant flow-test report, which is a separate
    # document the contractor commissions on site. Inventing them is the
    # single most dangerous default in a fire-sprinkler design: a downstream
    # hydraulic calc fed with fake supply pressure produces a system that
    # looks compliant on paper but fails on the real water main.
    # If they're not extracted, leave them missing and flag for PE review.
    needs_review = ctx.setdefault("_needs_review", [])
    for key in ("static_pressure", "residual_pressure", "water_supply_flow"):
        if not ctx.get(key) and key not in needs_review:
            needs_review.append(key)

    # Seismic zone — derive from address state if not in the drawings,
    # don't fabricate "D1" on a North Dakota project.
    if not ctx.get("seismic_zone"):
        loc = (ctx.get("location") or "") + " " + (ctx.get("address") or "")
        state_zones = {
            "CA":"D1","OR":"D1","WA":"D1","NV":"D1","AK":"D2",
            "UT":"C","AZ":"C","MT":"B","ID":"C",
            "ND":"B","SD":"B","MN":"B","WI":"B","MI":"B",
            "IL":"B","OH":"B","IN":"B","IA":"B","NE":"B",
            "TX":"B","OK":"C","KS":"B","MO":"C",
            "SC":"D1","TN":"C","AL":"B","GA":"B","FL":"B",
        }
        for state, zone in state_zones.items():
            if re.search(r'\b' + state + r'\b', loc):
                ctx["seismic_zone"] = zone
                ctx["_seismic_source"] = f"derived from state ({state})"
                break

    # AHJ — derive from address city if not extracted.
    # Was hardcoded to 'Sacramento'-style fallback elsewhere; here we
    # produce a city-based AHJ from the location field, or flag for review.
    if not ctx.get("ahj_jurisdiction"):
        loc = (ctx.get("location") or ctx.get("address") or "").strip()
        # Try "City, ST" pattern
        m = re.search(r'\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}),\s*([A-Z]{2})\b', loc)
        if m:
            ctx["ahj_jurisdiction"] = f"{m.group(1).strip()} Fire Department"
            ctx["_ahj_source"] = "derived from address"

    return ctx


# ─── Public entry point ───────────────────────────────────────────────────────

async def extract_project_context(
    pdf_path: str,
    run_vision: bool = True,
) -> dict:
    """
    Main entry point. Given a PDF (architectural set, spec, or contract),
    returns a complete project_context dict ready for NFPA13DesignEngine.

    Steps:
      1. Extract full text → TextExtractor.extract_all()
      2. Classify pages → find cover, floor plan, structural pages
      3. Vision pass on cover page → project data
      4. Vision pass on floor plan page → room layout + geometry
      5. Synthesize all data

    run_vision=False skips Vision API calls (for testing or when no API key).
    """
    log.info("[ProjectExtractor] Processing: %s", pdf_path)

    # ── 1. Full text extraction ────────────────────────────────────────────────
    all_text = extract_text_all_pages(pdf_path)
    te = TextExtractor(all_text)
    text_data = te.extract_all()
    log.info("[ProjectExtractor] Text extraction: occupancy=%s area=%s framing=%s",
             text_data.get("occupancy"), text_data.get("total_area"), text_data.get("structural_framing"))

    cover_data  = {}
    floor_data  = {}

    if run_vision and HAS_ANTHROPIC:
        # ── 2. Page classification ─────────────────────────────────────────────
        pc = PageClassifier(pdf_path)
        pages = pc.find_pages()
        log.info("[ProjectExtractor] Key pages: %s", pages)

        # ── 3. Vision on cover / code data page ───────────────────────────────
        cover_pg = pages.get("cover")
        if cover_pg:
            log.info("[ProjectExtractor] Running Vision on cover page %d", cover_pg)
            img = _render_page(pdf_path, cover_pg, dpi=120)
            if img:
                cover_data = await _call_vision(img, PROJECT_DATA_VISION_PROMPT)
                log.info("[ProjectExtractor] Cover vision: project=%s",
                         cover_data.get("project_name","?"))

        # ── 4. Vision on floor plan page ──────────────────────────────────────
        fp_pg = pages.get("floor_plan")
        if fp_pg:
            log.info("[ProjectExtractor] Running Vision on floor plan page %d", fp_pg)
            img = _render_page(pdf_path, fp_pg, dpi=100)
            if img:
                floor_data = await _call_vision(img, FLOOR_PLAN_VISION_PROMPT)
                rooms = floor_data.get("rooms",[])
                log.info("[ProjectExtractor] Floor plan vision: %d rooms", len(rooms))
    else:
        log.info("[ProjectExtractor] Vision skipped (run_vision=%s, anthropic=%s)",
                 run_vision, HAS_ANTHROPIC)

    # ── 5. Synthesize ──────────────────────────────────────────────────────────
    ctx = synthesize_context(text_data, cover_data, floor_data)
    log.info("[ProjectExtractor] Final context: %s rooms, hazard=%s, area=%s SF",
             len(ctx.get("rooms",[])), ctx.get("warehouse_hazard"), ctx.get("total_area"))
    return ctx


def extract_project_context_sync(pdf_path: str, run_vision: bool = True) -> dict:
    """Synchronous wrapper for use in FastAPI background tasks."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run,
                    extract_project_context(pdf_path, run_vision)
                ).result(timeout=300)
        return loop.run_until_complete(extract_project_context(pdf_path, run_vision))
    except Exception as e:
        log.warning("[ProjectExtractor] Sync extraction failed: %s", e)
        return {}
