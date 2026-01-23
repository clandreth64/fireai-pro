#!/usr/bin/env python3
"""
FireAI Pro - Color-Aware Geometry Extractor v2.0
=================================================
Step 1.2b: Wall/Pipe Differentiation

Extracts geometry from floor plans WITH COLOR INFORMATION:
- Separates building walls (black/gray) from fire sprinkler piping (red)
- Identifies structural elements by color
- Supports filtering by color category

VERSION: 2.0.0
"""

import math
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any, Set
from collections import defaultdict, Counter
from enum import Enum

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.ColorGeometry")

# Check for pdfplumber
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logger.warning("pdfplumber not available")


# =============================================================================
# COLOR CLASSIFICATION
# =============================================================================

class ElementCategory(Enum):
    """Categories of drawing elements by color"""
    WALL = "wall"           # Black, dark gray
    PIPE = "pipe"           # Red
    STRUCTURE = "structure" # Gray
    HIGHLIGHT = "highlight" # Cyan, magenta
    TEXT = "text"           # Black (thin)
    GRID = "grid"           # Light gray
    UNKNOWN = "unknown"


# Color classification rules (RGB tuples)
# Note: Colors are normalized 0-1 from PDF
COLOR_RULES = {
    # Red variants → PIPE
    ((0.8, 1.0), (0.0, 0.2), (0.0, 0.2)): ElementCategory.PIPE,
    
    # Black/dark gray → WALL
    ((0.0, 0.15), (0.0, 0.15), (0.0, 0.15)): ElementCategory.WALL,
    
    # Medium gray → STRUCTURE (could be walls or structural)
    ((0.4, 0.6), (0.4, 0.6), (0.4, 0.6)): ElementCategory.STRUCTURE,
    
    # Light gray → GRID
    ((0.7, 0.95), (0.7, 0.95), (0.7, 0.95)): ElementCategory.GRID,
    
    # Cyan → HIGHLIGHT
    ((0.0, 0.2), (0.8, 1.0), (0.8, 1.0)): ElementCategory.HIGHLIGHT,
    
    # Blue → HIGHLIGHT
    ((0.0, 0.2), (0.0, 0.2), (0.8, 1.0)): ElementCategory.HIGHLIGHT,
    
    # Green variants → could be existing systems
    ((0.0, 0.3), (0.6, 1.0), (0.0, 0.5)): ElementCategory.HIGHLIGHT,
}


def classify_color(rgb: Tuple) -> ElementCategory:
    """Classify RGB color into element category"""
    if rgb is None or rgb == 0:
        return ElementCategory.WALL  # Default black
    
    if not isinstance(rgb, (tuple, list)) or len(rgb) < 3:
        return ElementCategory.UNKNOWN
    
    r, g, b = rgb[0], rgb[1], rgb[2]
    
    # Check each rule
    for (r_range, g_range, b_range), category in COLOR_RULES.items():
        if (r_range[0] <= r <= r_range[1] and
            g_range[0] <= g <= g_range[1] and
            b_range[0] <= b <= b_range[1]):
            return category
    
    return ElementCategory.UNKNOWN


def rgb_to_hex(rgb: Tuple) -> str:
    """Convert RGB tuple to hex string"""
    if rgb is None or rgb == 0:
        return "#000000"
    if not isinstance(rgb, (tuple, list)) or len(rgb) < 3:
        return "#000000"
    
    r = int(rgb[0] * 255) if isinstance(rgb[0], float) else rgb[0]
    g = int(rgb[1] * 255) if isinstance(rgb[1], float) else rgb[1]
    b = int(rgb[2] * 255) if isinstance(rgb[2], float) else rgb[2]
    
    return f"#{r:02x}{g:02x}{b:02x}"


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Point2D:
    """2D Point"""
    x: float
    y: float
    
    def distance_to(self, other: 'Point2D') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def __hash__(self):
        return hash((round(self.x, 1), round(self.y, 1)))


@dataclass
class ColorLine:
    """Line with color information"""
    start: Point2D
    end: Point2D
    color_rgb: Tuple = (0, 0, 0)
    color_hex: str = "#000000"
    category: ElementCategory = ElementCategory.UNKNOWN
    width: float = 1.0
    
    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)
    
    @property
    def is_horizontal(self) -> bool:
        return abs(self.end.y - self.start.y) < 1.0
    
    @property
    def is_vertical(self) -> bool:
        return abs(self.end.x - self.start.x) < 1.0
    
    @property
    def midpoint(self) -> Point2D:
        return Point2D(
            (self.start.x + self.end.x) / 2,
            (self.start.y + self.end.y) / 2
        )


@dataclass
class TextElement:
    """Text element"""
    text: str
    x: float
    y: float
    width: float = 0
    height: float = 0
    font_size: float = 0


@dataclass
class ColorExtractedGeometry:
    """Geometry with color classification"""
    source_file: str
    page_number: int = 1
    
    # Page bounds
    page_width: float = 0
    page_height: float = 0
    
    # All lines with color info
    all_lines: List[ColorLine] = field(default_factory=list)
    
    # Lines separated by category
    wall_lines: List[ColorLine] = field(default_factory=list)
    pipe_lines: List[ColorLine] = field(default_factory=list)
    structure_lines: List[ColorLine] = field(default_factory=list)
    grid_lines: List[ColorLine] = field(default_factory=list)
    other_lines: List[ColorLine] = field(default_factory=list)
    
    # Text elements
    texts: List[TextElement] = field(default_factory=list)
    
    # Scale info
    scale_text: str = ""
    scale_factor: float = 9.0  # pts per foot at 1/8" = 1'-0"
    
    # Statistics
    color_distribution: Dict[str, int] = field(default_factory=dict)
    category_counts: Dict[str, int] = field(default_factory=dict)
    
    # Confidence
    confidence: float = 0


# =============================================================================
# COLOR-AWARE EXTRACTOR
# =============================================================================

class ColorAwareGeometryExtractor:
    """
    Extract geometry with color classification.
    Separates building elements from fire sprinkler piping.
    """
    
    def __init__(self):
        if not PDFPLUMBER_AVAILABLE:
            raise RuntimeError("pdfplumber required for color extraction")
    
    def extract(self, pdf_path: str, page_num: int = 0) -> ColorExtractedGeometry:
        """
        Extract geometry with color classification.
        
        Args:
            pdf_path: Path to PDF file
            page_num: Page number (0-indexed)
        
        Returns:
            ColorExtractedGeometry with categorized elements
        """
        result = ColorExtractedGeometry(
            source_file=pdf_path,
            page_number=page_num + 1
        )
        
        logger.info(f"🎨 Color extraction from: {Path(pdf_path).name}")
        
        with pdfplumber.open(pdf_path) as pdf:
            if page_num >= len(pdf.pages):
                logger.error(f"Page {page_num} not found")
                return result
            
            page = pdf.pages[page_num]
            result.page_width = page.width
            result.page_height = page.height
            
            logger.info(f"   Page size: {page.width:.0f} x {page.height:.0f} pts")
            
            # Extract lines with color
            self._extract_lines(page, result)
            
            # Extract curves with color
            self._extract_curves(page, result)
            
            # Classify all lines by color
            self._classify_lines(result)
            
            # Extract text
            self._extract_text(page, result)
            
            # Detect scale
            self._detect_scale(result)
            
            # Calculate statistics
            self._calculate_stats(result)
        
        logger.info(f"✅ Extraction complete:")
        logger.info(f"   Total lines: {len(result.all_lines):,}")
        logger.info(f"   Walls: {len(result.wall_lines):,}")
        logger.info(f"   Pipes: {len(result.pipe_lines):,}")
        logger.info(f"   Structure: {len(result.structure_lines):,}")
        logger.info(f"   Texts: {len(result.texts):,}")
        
        return result
    
    def _extract_lines(self, page, result: ColorExtractedGeometry):
        """Extract lines with color info"""
        lines = page.lines or []
        
        for line in lines:
            color = line.get('stroking_color', line.get('non_stroking_color'))
            
            # Flip Y coordinate (PDF origin is bottom-left)
            y0 = result.page_height - line['top']
            y1 = result.page_height - line['bottom']
            
            color_line = ColorLine(
                start=Point2D(line['x0'], y0),
                end=Point2D(line['x1'], y1),
                color_rgb=tuple(color) if isinstance(color, (list, tuple)) else (0, 0, 0),
                color_hex=rgb_to_hex(color),
                category=classify_color(color),
                width=line.get('linewidth', 1.0)
            )
            
            # Only keep lines with meaningful length
            if color_line.length > 1:
                result.all_lines.append(color_line)
    
    def _extract_curves(self, page, result: ColorExtractedGeometry):
        """Extract curves as line segments"""
        curves = page.curves or []
        
        for curve in curves:
            color = curve.get('stroking_color', curve.get('non_stroking_color'))
            pts = curve.get('pts', [])
            
            if len(pts) < 2:
                continue
            
            # Convert curve points to line segments
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i + 1]
                
                # Flip Y
                y1 = result.page_height - p1[1]
                y2 = result.page_height - p2[1]
                
                color_line = ColorLine(
                    start=Point2D(p1[0], y1),
                    end=Point2D(p2[0], y2),
                    color_rgb=tuple(color) if isinstance(color, (list, tuple)) else (0, 0, 0),
                    color_hex=rgb_to_hex(color),
                    category=classify_color(color),
                    width=curve.get('linewidth', 1.0)
                )
                
                if color_line.length > 1:
                    result.all_lines.append(color_line)
    
    def _classify_lines(self, result: ColorExtractedGeometry):
        """Sort lines into category buckets"""
        for line in result.all_lines:
            if line.category == ElementCategory.WALL:
                result.wall_lines.append(line)
            elif line.category == ElementCategory.PIPE:
                result.pipe_lines.append(line)
            elif line.category == ElementCategory.STRUCTURE:
                result.structure_lines.append(line)
            elif line.category == ElementCategory.GRID:
                result.grid_lines.append(line)
            else:
                result.other_lines.append(line)
    
    def _extract_text(self, page, result: ColorExtractedGeometry):
        """Extract text elements"""
        words = page.extract_words() or []
        
        for word in words:
            result.texts.append(TextElement(
                text=word['text'],
                x=word['x0'],
                y=result.page_height - word['top'],
                width=word['x1'] - word['x0'],
                height=word['bottom'] - word['top']
            ))
    
    def _detect_scale(self, result: ColorExtractedGeometry):
        """Detect drawing scale from text"""
        import re
        
        scale_patterns = [
            (r'1/8["\s]*=\s*1[\'\-]', 9.0, '1/8" = 1\'-0"'),
            (r'1/4["\s]*=\s*1[\'\-]', 18.0, '1/4" = 1\'-0"'),
            (r'1/16["\s]*=\s*1[\'\-]', 4.5, '1/16" = 1\'-0"'),
            (r'3/32["\s]*=\s*1[\'\-]', 6.75, '3/32" = 1\'-0"'),
        ]
        
        for text in result.texts:
            text_upper = text.text.upper()
            for pattern, factor, display in scale_patterns:
                if re.search(pattern, text_upper, re.IGNORECASE):
                    result.scale_factor = factor
                    result.scale_text = display
                    logger.info(f"   Scale detected: {display} ({factor} pts/ft)")
                    return
        
        # Default
        result.scale_factor = 9.0
        result.scale_text = "assumed 1/8\" = 1'-0\""
    
    def _calculate_stats(self, result: ColorExtractedGeometry):
        """Calculate statistics"""
        # Color distribution
        colors = Counter(line.color_hex for line in result.all_lines)
        result.color_distribution = dict(colors.most_common(20))
        
        # Category counts
        result.category_counts = {
            'wall': len(result.wall_lines),
            'pipe': len(result.pipe_lines),
            'structure': len(result.structure_lines),
            'grid': len(result.grid_lines),
            'other': len(result.other_lines)
        }
        
        # Confidence based on successful categorization
        categorized = sum(1 for line in result.all_lines if line.category != ElementCategory.UNKNOWN)
        if result.all_lines:
            result.confidence = (categorized / len(result.all_lines)) * 100


# =============================================================================
# WALL EXTRACTOR (Using Color)
# =============================================================================

class ColorBasedWallExtractor:
    """
    Extract building walls using color classification.
    Filters out fire sprinkler piping (red) to get only structural elements.
    """
    
    def __init__(self, 
                 min_wall_length: float = 15.0,
                 merge_tolerance: float = 3.0):
        self.min_wall_length = min_wall_length
        self.merge_tolerance = merge_tolerance
    
    def extract_walls(self, geometry: ColorExtractedGeometry) -> List[Tuple]:
        """
        Extract wall segments from color-classified geometry.
        
        Returns list of wall tuples: (x1, y1, x2, y2)
        """
        # Use wall_lines + structure_lines (gray/black elements)
        wall_candidates = geometry.wall_lines + geometry.structure_lines
        
        logger.info(f"🧱 Wall extraction from {len(wall_candidates)} wall/structure lines")
        
        # Filter by minimum length
        long_lines = [l for l in wall_candidates if l.length >= self.min_wall_length]
        
        # Separate horizontal and vertical
        h_lines = [l for l in long_lines if l.is_horizontal]
        v_lines = [l for l in long_lines if l.is_vertical]
        
        logger.info(f"   Candidates: {len(h_lines)} horizontal, {len(v_lines)} vertical")
        
        # Merge collinear segments
        merged_h = self._merge_lines(h_lines, horizontal=True)
        merged_v = self._merge_lines(v_lines, horizontal=False)
        
        all_walls = merged_h + merged_v
        
        logger.info(f"✅ Extracted {len(all_walls)} wall segments")
        
        return all_walls
    
    def _merge_lines(self, lines: List[ColorLine], horizontal: bool) -> List[Tuple]:
        """Merge collinear line segments"""
        if not lines:
            return []
        
        # Group by perpendicular coordinate
        groups = defaultdict(list)
        for line in lines:
            if horizontal:
                key = round(line.start.y / self.merge_tolerance) * self.merge_tolerance
            else:
                key = round(line.start.x / self.merge_tolerance) * self.merge_tolerance
            groups[key].append(line)
        
        merged = []
        
        for key, group in groups.items():
            # Sort along primary axis
            if horizontal:
                group = sorted(group, key=lambda l: min(l.start.x, l.end.x))
            else:
                group = sorted(group, key=lambda l: min(l.start.y, l.end.y))
            
            if not group:
                continue
            
            # Merge adjacent segments
            if horizontal:
                cs = min(group[0].start.x, group[0].end.x)
                ce = max(group[0].start.x, group[0].end.x)
                cy = group[0].start.y
                
                for line in group[1:]:
                    ls = min(line.start.x, line.end.x)
                    le = max(line.start.x, line.end.x)
                    
                    if ls <= ce + self.merge_tolerance:
                        ce = max(ce, le)
                    else:
                        merged.append((cs, cy, ce, cy))
                        cs, ce, cy = ls, le, line.start.y
                
                merged.append((cs, cy, ce, cy))
            else:
                cs = min(group[0].start.y, group[0].end.y)
                ce = max(group[0].start.y, group[0].end.y)
                cx = group[0].start.x
                
                for line in group[1:]:
                    ls = min(line.start.y, line.end.y)
                    le = max(line.start.y, line.end.y)
                    
                    if ls <= ce + self.merge_tolerance:
                        ce = max(ce, le)
                    else:
                        merged.append((cx, cs, cx, ce))
                        cs, ce, cx = ls, le, line.start.x
                
                merged.append((cx, cs, cx, ce))
        
        return merged
    
    def extract_pipes(self, geometry: ColorExtractedGeometry) -> List[Tuple]:
        """
        Extract fire sprinkler piping from red lines.
        
        Returns list of pipe tuples: (x1, y1, x2, y2)
        """
        pipe_lines = geometry.pipe_lines
        
        logger.info(f"🔴 Pipe extraction from {len(pipe_lines)} red lines")
        
        # Filter by minimum length (pipes tend to be longer runs)
        long_pipes = [l for l in pipe_lines if l.length >= 10]
        
        # Convert to tuples
        pipes = []
        for line in long_pipes:
            pipes.append((
                line.start.x, line.start.y,
                line.end.x, line.end.y
            ))
        
        logger.info(f"✅ Extracted {len(pipes)} pipe segments")
        
        return pipes


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def extract_with_colors(pdf_path: str, page_num: int = 0) -> ColorExtractedGeometry:
    """Extract geometry with color classification"""
    extractor = ColorAwareGeometryExtractor()
    return extractor.extract(pdf_path, page_num)


def extract_walls_only(geometry: ColorExtractedGeometry) -> List[Tuple]:
    """Extract only building walls (excluding pipes)"""
    wall_extractor = ColorBasedWallExtractor()
    return wall_extractor.extract_walls(geometry)


def extract_pipes_only(geometry: ColorExtractedGeometry) -> List[Tuple]:
    """Extract only fire sprinkler piping"""
    wall_extractor = ColorBasedWallExtractor()
    return wall_extractor.extract_pipes(geometry)


def geometry_to_dict(geometry: ColorExtractedGeometry) -> Dict:
    """Convert to JSON-serializable dict"""
    return {
        'source_file': geometry.source_file,
        'page_number': geometry.page_number,
        'page_size': {
            'width': geometry.page_width,
            'height': geometry.page_height
        },
        'scale': {
            'text': geometry.scale_text,
            'factor': geometry.scale_factor
        },
        'counts': {
            'total_lines': len(geometry.all_lines),
            'wall_lines': len(geometry.wall_lines),
            'pipe_lines': len(geometry.pipe_lines),
            'structure_lines': len(geometry.structure_lines),
            'grid_lines': len(geometry.grid_lines),
            'other_lines': len(geometry.other_lines),
            'texts': len(geometry.texts)
        },
        'color_distribution': geometry.color_distribution,
        'confidence': geometry.confidence
    }


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🎨 FireAI Pro - Color-Aware Geometry Extractor v2.0")
    print("=" * 60)
    print("\nStep 1.2b: Wall/Pipe Differentiation")
    print("\nCapabilities:")
    print("  ✅ Extract lines with color information")
    print("  ✅ Classify by color: walls vs pipes vs structure")
    print("  ✅ Filter building walls (black/gray)")
    print("  ✅ Filter fire sprinkler piping (red)")
    print("  ✅ Merge collinear wall segments")
    print("\nUsage:")
    print("  geometry = extract_with_colors('floor_plan.pdf')")
    print("  walls = extract_walls_only(geometry)")
    print("  pipes = extract_pipes_only(geometry)")
