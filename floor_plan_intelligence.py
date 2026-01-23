#!/usr/bin/env python3
"""
FireAI Pro - Floor Plan Intelligence Module v1.0
==================================================
Integrated geometry extraction, wall detection, and room detection.

Combines Steps 1.1 → 1.3 into a single module:
- PDF geometry extraction with color awareness
- Wall/pipe differentiation (by color)
- Room polygon detection
- Sprinkler head placement preparation

This module bridges the gap between raw floor plan PDFs and 
intelligent fire sprinkler system design.

VERSION: 1.0.0
"""

import math
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict
from enum import Enum

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.FloorPlanIntelligence")

# Check for pdfplumber
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logger.error("pdfplumber required - pip install pdfplumber")


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class ElementType(Enum):
    """Type of drawing element"""
    WALL = "wall"
    PIPE = "pipe"
    STRUCTURE = "structure"
    GRID = "grid"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass
class Point:
    """2D Point"""
    x: float
    y: float
    
    def distance_to(self, other: 'Point') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def __hash__(self):
        return hash((round(self.x, 1), round(self.y, 1)))


@dataclass
class Line:
    """Line segment with color classification"""
    x0: float
    y0: float
    x1: float
    y1: float
    element_type: ElementType = ElementType.UNKNOWN
    color_rgb: Tuple = (0, 0, 0)
    width: float = 1.0
    
    @property
    def length(self) -> float:
        return math.sqrt((self.x1 - self.x0)**2 + (self.y1 - self.y0)**2)
    
    @property
    def is_horizontal(self) -> bool:
        return abs(self.y1 - self.y0) < 1.0
    
    @property
    def is_vertical(self) -> bool:
        return abs(self.x1 - self.x0) < 1.0
    
    @property
    def midpoint(self) -> Point:
        return Point((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)


@dataclass
class TextLabel:
    """Text element"""
    text: str
    x: float
    y: float
    width: float = 0
    height: float = 0


@dataclass
class Room:
    """Detected room"""
    id: str
    area_sqft: float
    width_ft: float
    height_ft: float
    centroid: Point
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    label: str = ""
    room_type: str = "unknown"
    hazard_class: str = "Light"
    
    def contains_point(self, p: Point) -> bool:
        return self.min_x <= p.x <= self.max_x and self.min_y <= p.y <= self.max_y


@dataclass
class FloorPlanData:
    """Complete floor plan analysis"""
    # Source
    source_file: str
    page_number: int = 1
    
    # Page dimensions
    page_width_pts: float = 0
    page_height_pts: float = 0
    
    # Scale
    scale_text: str = ""
    scale_factor: float = 9.0  # pts per foot (1/8" = 1'-0")
    
    # Floor plan bounds (actual drawing area, not whole page)
    fp_min_x: float = 0
    fp_max_x: float = 0
    fp_min_y: float = 0
    fp_max_y: float = 0
    
    # Elements
    walls: List[Line] = field(default_factory=list)
    pipes: List[Line] = field(default_factory=list)
    texts: List[TextLabel] = field(default_factory=list)
    rooms: List[Room] = field(default_factory=list)
    
    # Statistics
    total_lines: int = 0
    wall_count: int = 0
    pipe_count: int = 0
    room_count: int = 0
    total_area_sqft: float = 0
    
    @property
    def fp_width_ft(self) -> float:
        return (self.fp_max_x - self.fp_min_x) / self.scale_factor
    
    @property
    def fp_height_ft(self) -> float:
        return (self.fp_max_y - self.fp_min_y) / self.scale_factor
    
    @property
    def fp_area_sqft(self) -> float:
        return self.fp_width_ft * self.fp_height_ft


# =============================================================================
# COLOR CLASSIFICATION
# =============================================================================

def classify_color(rgb) -> ElementType:
    """Classify line by its color"""
    if rgb is None or rgb == 0:
        return ElementType.WALL
    
    if isinstance(rgb, (int, float)):
        rgb = (rgb, rgb, rgb)
    
    if not isinstance(rgb, (tuple, list)) or len(rgb) < 3:
        return ElementType.UNKNOWN
    
    r, g, b = rgb[0], rgb[1], rgb[2]
    
    # Red = Fire sprinkler piping
    if r > 0.7 and g < 0.3 and b < 0.3:
        return ElementType.PIPE
    
    # Black = Walls
    if r < 0.2 and g < 0.2 and b < 0.2:
        return ElementType.WALL
    
    # Medium gray = Structure
    if 0.4 <= r <= 0.6 and abs(r - g) < 0.1 and abs(r - b) < 0.1:
        return ElementType.STRUCTURE
    
    # Light gray = Grid lines
    if r > 0.7 and abs(r - g) < 0.1 and abs(r - b) < 0.1:
        return ElementType.GRID
    
    return ElementType.UNKNOWN


# =============================================================================
# SCALE DETECTION
# =============================================================================

def detect_scale(texts: List[TextLabel]) -> Tuple[str, float]:
    """Detect drawing scale from text labels"""
    import re
    
    scale_patterns = [
        (r'1/8["\s]*=\s*1[\'\-]', 9.0, '1/8" = 1\'-0"'),
        (r'1/4["\s]*=\s*1[\'\-]', 18.0, '1/4" = 1\'-0"'),
        (r'1/16["\s]*=\s*1[\'\-]', 4.5, '1/16" = 1\'-0"'),
        (r'3/32["\s]*=\s*1[\'\-]', 6.75, '3/32" = 1\'-0"'),
        (r'3/16["\s]*=\s*1[\'\-]', 13.5, '3/16" = 1\'-0"'),
    ]
    
    for text in texts:
        for pattern, factor, display in scale_patterns:
            if re.search(pattern, text.text, re.IGNORECASE):
                return display, factor
    
    return "assumed 1/8\" = 1'-0\"", 9.0


# =============================================================================
# FLOOR PLAN REGION DETECTION
# =============================================================================

def detect_floor_plan_region(texts: List[TextLabel], 
                              page_width: float, 
                              page_height: float) -> Tuple[float, float, float, float]:
    """
    Detect the actual floor plan region (excluding title block, schedules).
    
    Returns (min_x, min_y, max_x, max_y)
    """
    # Look for grid labels (1, 2, 3... A, B, C...) which indicate floor plan area
    grid_labels = []
    for text in texts:
        t = text.text.strip()
        # Single letters A-Z or numbers 1-9
        if len(t) <= 3 and (t.isalpha() or t.isdigit()):
            grid_labels.append(text)
    
    if grid_labels:
        xs = [t.x for t in grid_labels]
        ys = [t.y for t in grid_labels]
        
        # Floor plan is bounded by grid labels with some margin
        margin = 50
        return (
            min(xs) - margin,
            min(ys) - margin,
            max(xs) + margin,
            max(ys) + margin
        )
    
    # Fallback: use middle portion of page (exclude title block)
    return (
        page_width * 0.05,
        page_height * 0.15,  # Skip title block at bottom
        page_width * 0.85,
        page_height * 0.95
    )


# =============================================================================
# MAIN EXTRACTOR CLASS
# =============================================================================

class FloorPlanIntelligence:
    """
    Complete floor plan analysis pipeline.
    
    Extracts:
    1. All lines with color classification
    2. Walls (black/gray) separated from pipes (red)
    3. Room polygons from wall boundaries
    4. Room labels and hazard classification
    """
    
    def __init__(self):
        if not PDFPLUMBER_AVAILABLE:
            raise RuntimeError("pdfplumber required")
        
        self.grid_resolution = 5.0  # pts per grid cell
        self.min_wall_length = 15.0  # pts
        self.merge_tolerance = 5.0   # pts
        self.min_room_area = 10.0    # sqft
        self.max_room_area = 5000.0  # sqft (filter exterior)
    
    def analyze(self, pdf_path: str, page_num: int = 0) -> FloorPlanData:
        """
        Analyze a floor plan PDF.
        
        Args:
            pdf_path: Path to PDF file
            page_num: Page number (0-indexed)
        
        Returns:
            FloorPlanData with complete analysis
        """
        result = FloorPlanData(
            source_file=pdf_path,
            page_number=page_num + 1
        )
        
        logger.info(f"🏗️ Analyzing floor plan: {Path(pdf_path).name}")
        
        with pdfplumber.open(pdf_path) as pdf:
            if page_num >= len(pdf.pages):
                logger.error(f"Page {page_num} not found")
                return result
            
            page = pdf.pages[page_num]
            result.page_width_pts = page.width
            result.page_height_pts = page.height
            
            # Step 1: Extract all lines with color
            all_lines = self._extract_lines_with_color(page, result)
            
            # Step 2: Extract text
            self._extract_text(page, result)
            
            # Step 3: Detect scale
            result.scale_text, result.scale_factor = detect_scale(result.texts)
            logger.info(f"   Scale: {result.scale_text}")
            
            # Step 4: Detect floor plan region
            fp_bounds = detect_floor_plan_region(
                result.texts, 
                result.page_width_pts, 
                result.page_height_pts
            )
            result.fp_min_x, result.fp_min_y, result.fp_max_x, result.fp_max_y = fp_bounds
            
            # Step 5: Separate walls from pipes
            self._classify_elements(all_lines, result, fp_bounds)
            
            # Step 6: Merge wall segments
            merged_walls = self._merge_walls(result.walls)
            
            # Step 7: Detect rooms
            self._detect_rooms(merged_walls, result)
            
            # Step 8: Assign labels and hazard classes
            self._classify_rooms(result)
        
        # Final statistics
        result.total_lines = len(all_lines)
        result.wall_count = len(result.walls)
        result.pipe_count = len(result.pipes)
        result.room_count = len(result.rooms)
        result.total_area_sqft = sum(r.area_sqft for r in result.rooms)
        
        logger.info(f"✅ Analysis complete:")
        logger.info(f"   Floor plan: {result.fp_width_ft:.0f} x {result.fp_height_ft:.0f} ft")
        logger.info(f"   Walls: {result.wall_count}, Pipes: {result.pipe_count}")
        logger.info(f"   Rooms: {result.room_count}, Total area: {result.total_area_sqft:,.0f} sqft")
        
        return result
    
    def _extract_lines_with_color(self, page, result: FloorPlanData) -> List[Line]:
        """Extract all lines with color classification"""
        all_lines = []
        page_height = page.height
        
        # Process PDF lines
        for line in (page.lines or []):
            color = line.get('stroking_color')
            
            l = Line(
                x0=line['x0'],
                y0=page_height - line['top'],
                x1=line['x1'],
                y1=page_height - line['bottom'],
                element_type=classify_color(color),
                color_rgb=tuple(color) if isinstance(color, (list, tuple)) else (0, 0, 0),
                width=line.get('linewidth', 1.0)
            )
            
            if l.length > 1:
                all_lines.append(l)
        
        # Process curves (convert to line segments)
        for curve in (page.curves or []):
            color = curve.get('stroking_color')
            element_type = classify_color(color)
            pts = curve.get('pts', [])
            
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i + 1]
                
                l = Line(
                    x0=p1[0],
                    y0=page_height - p1[1],
                    x1=p2[0],
                    y1=page_height - p2[1],
                    element_type=element_type,
                    color_rgb=tuple(color) if isinstance(color, (list, tuple)) else (0, 0, 0),
                    width=curve.get('linewidth', 1.0)
                )
                
                if l.length > 1:
                    all_lines.append(l)
        
        logger.info(f"   Extracted {len(all_lines):,} lines")
        return all_lines
    
    def _extract_text(self, page, result: FloorPlanData):
        """Extract text elements"""
        page_height = page.height
        
        for word in (page.extract_words() or []):
            result.texts.append(TextLabel(
                text=word['text'],
                x=word['x0'],
                y=page_height - word['top'],
                width=word['x1'] - word['x0'],
                height=word['bottom'] - word['top']
            ))
        
        logger.info(f"   Extracted {len(result.texts)} text labels")
    
    def _classify_elements(self, all_lines: List[Line], 
                           result: FloorPlanData, 
                           fp_bounds: Tuple):
        """Separate walls from pipes based on color and location"""
        min_x, min_y, max_x, max_y = fp_bounds
        
        for line in all_lines:
            # Check if line is in floor plan area
            in_fp = ((min_x <= line.x0 <= max_x or min_x <= line.x1 <= max_x) and
                     (min_y <= line.y0 <= max_y or min_y <= line.y1 <= max_y))
            
            if not in_fp:
                continue
            
            # Filter by minimum length
            if line.length < self.min_wall_length:
                continue
            
            # Classify
            if line.element_type == ElementType.PIPE:
                result.pipes.append(line)
            elif line.element_type in (ElementType.WALL, ElementType.STRUCTURE):
                # Only keep horizontal/vertical walls
                if line.is_horizontal or line.is_vertical:
                    result.walls.append(line)
        
        logger.info(f"   Classified: {len(result.walls)} walls, {len(result.pipes)} pipes")
    
    def _merge_walls(self, walls: List[Line]) -> List[Tuple]:
        """Merge collinear wall segments"""
        h_walls = [w for w in walls if w.is_horizontal]
        v_walls = [w for w in walls if w.is_vertical]
        
        def merge_group(wall_list, horizontal=True):
            if not wall_list:
                return []
            
            groups = defaultdict(list)
            for w in wall_list:
                if horizontal:
                    key = round(w.y0 / self.merge_tolerance) * self.merge_tolerance
                else:
                    key = round(w.x0 / self.merge_tolerance) * self.merge_tolerance
                groups[key].append(w)
            
            merged = []
            for key, group in groups.items():
                if horizontal:
                    group = sorted(group, key=lambda w: min(w.x0, w.x1))
                    cs = min(group[0].x0, group[0].x1)
                    ce = max(group[0].x0, group[0].x1)
                    cy = group[0].y0
                    
                    for w in group[1:]:
                        ws = min(w.x0, w.x1)
                        we = max(w.x0, w.x1)
                        if ws <= ce + self.merge_tolerance:
                            ce = max(ce, we)
                        else:
                            merged.append((cs, cy, ce, cy))
                            cs, ce = ws, we
                    merged.append((cs, cy, ce, cy))
                else:
                    group = sorted(group, key=lambda w: min(w.y0, w.y1))
                    cs = min(group[0].y0, group[0].y1)
                    ce = max(group[0].y0, group[0].y1)
                    cx = group[0].x0
                    
                    for w in group[1:]:
                        ws = min(w.y0, w.y1)
                        we = max(w.y0, w.y1)
                        if ws <= ce + self.merge_tolerance:
                            ce = max(ce, we)
                        else:
                            merged.append((cx, cs, cx, ce))
                            cs, ce = ws, we
                    merged.append((cx, cs, cx, ce))
            
            return merged
        
        merged_h = merge_group(h_walls, horizontal=True)
        merged_v = merge_group(v_walls, horizontal=False)
        
        logger.info(f"   Merged to {len(merged_h) + len(merged_v)} wall segments")
        return merged_h + merged_v
    
    def _detect_rooms(self, walls: List[Tuple], result: FloorPlanData):
        """Detect rooms using grid-based flood fill"""
        bounds = (result.fp_min_x, result.fp_min_y, result.fp_max_x, result.fp_max_y)
        min_x, min_y, max_x, max_y = bounds
        scale = result.scale_factor
        
        # Create grid
        width = int((max_x - min_x) / self.grid_resolution) + 1
        height = int((max_y - min_y) / self.grid_resolution) + 1
        grid = [[0] * width for _ in range(height)]
        
        # Draw walls on grid
        for wall in walls:
            self._draw_wall_on_grid(grid, wall, min_x, min_y, width, height)
        
        # Flood fill to find rooms
        room_id = 1
        for y in range(height):
            for x in range(width):
                if grid[y][x] == 0:
                    pixels = self._flood_fill(grid, x, y, room_id, width, height)
                    
                    if pixels and len(pixels) >= 5:
                        # Calculate room properties
                        xs = [p[0] for p in pixels]
                        ys = [p[1] for p in pixels]
                        
                        # Area
                        cell_area = (self.grid_resolution ** 2) / (scale ** 2)
                        area_sqft = len(pixels) * cell_area
                        
                        # Filter by area
                        if self.min_room_area <= area_sqft <= self.max_room_area:
                            # Bounds
                            room_min_x = min_x + min(xs) * self.grid_resolution
                            room_max_x = min_x + (max(xs) + 1) * self.grid_resolution
                            room_min_y = min_y + min(ys) * self.grid_resolution
                            room_max_y = min_y + (max(ys) + 1) * self.grid_resolution
                            
                            # Centroid
                            cx = min_x + (sum(xs) / len(xs)) * self.grid_resolution
                            cy = min_y + (sum(ys) / len(ys)) * self.grid_resolution
                            
                            room = Room(
                                id=f"ROOM-{room_id:03d}",
                                area_sqft=area_sqft,
                                width_ft=(room_max_x - room_min_x) / scale,
                                height_ft=(room_max_y - room_min_y) / scale,
                                centroid=Point(cx, cy),
                                min_x=room_min_x,
                                max_x=room_max_x,
                                min_y=room_min_y,
                                max_y=room_max_y
                            )
                            result.rooms.append(room)
                        
                        room_id += 1
        
        logger.info(f"   Detected {len(result.rooms)} rooms")
    
    def _draw_wall_on_grid(self, grid, wall, min_x, min_y, width, height):
        """Draw wall on grid using Bresenham's algorithm"""
        x1, y1, x2, y2 = wall
        gx1 = int((x1 - min_x) / self.grid_resolution)
        gy1 = int((y1 - min_y) / self.grid_resolution)
        gx2 = int((x2 - min_x) / self.grid_resolution)
        gy2 = int((y2 - min_y) / self.grid_resolution)
        
        dx = abs(gx2 - gx1)
        dy = abs(gy2 - gy1)
        sx = 1 if gx1 < gx2 else -1
        sy = 1 if gy1 < gy2 else -1
        err = dx - dy
        
        while True:
            if 0 <= gx1 < width and 0 <= gy1 < height:
                grid[gy1][gx1] = -1
            if gx1 == gx2 and gy1 == gy2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                gx1 += sx
            if e2 < dx:
                err += dx
                gy1 += sy
    
    def _flood_fill(self, grid, sx, sy, rid, width, height):
        """Flood fill from starting point"""
        if grid[sy][sx] != 0:
            return []
        
        pixels = []
        stack = [(sx, sy)]
        
        while stack:
            x, y = stack.pop()
            if not (0 <= x < width and 0 <= y < height):
                continue
            if grid[y][x] != 0:
                continue
            
            grid[y][x] = rid
            pixels.append((x, y))
            stack.extend([(x+1, y), (x-1, y), (x, y+1), (x, y-1)])
        
        return pixels
    
    def _classify_rooms(self, result: FloorPlanData):
        """Assign labels and hazard classes to rooms"""
        # Room type keywords
        room_types = {
            'office': ('office', 'Light'),
            'conf': ('conference', 'Light'),
            'meeting': ('conference', 'Light'),
            'lobby': ('lobby', 'Light'),
            'reception': ('lobby', 'Light'),
            'corridor': ('corridor', 'Light'),
            'hall': ('corridor', 'Light'),
            'restroom': ('restroom', 'Light'),
            'bathroom': ('restroom', 'Light'),
            'kitchen': ('kitchen', 'Ordinary I'),
            'break': ('break_room', 'Light'),
            'storage': ('storage', 'Ordinary II'),
            'closet': ('closet', 'Light'),
            'mechanical': ('mechanical', 'Ordinary I'),
            'electrical': ('electrical', 'Ordinary I'),
            'elec': ('electrical', 'Ordinary I'),
            'mech': ('mechanical', 'Ordinary I'),
            'stair': ('stair', 'Light'),
            'elevator': ('elevator', 'Light'),
            'server': ('server_room', 'Ordinary I'),
            'data': ('data_center', 'Ordinary I'),
            'parking': ('parking', 'Ordinary I'),
            'warehouse': ('warehouse', 'Ordinary II'),
        }
        
        # Assign labels from text
        for text in result.texts:
            point = Point(text.x, text.y)
            
            for room in result.rooms:
                if room.contains_point(point) and not room.label:
                    room.label = text.text
                    
                    # Determine room type and hazard
                    text_lower = text.text.lower()
                    for keyword, (rtype, hazard) in room_types.items():
                        if keyword in text_lower:
                            room.room_type = rtype
                            room.hazard_class = hazard
                            break
                    break
        
        # Default hazard based on room size
        for room in result.rooms:
            if room.hazard_class == "Light" and room.area_sqft > 1000:
                room.hazard_class = "Ordinary I"


# =============================================================================
# EXPORT FUNCTIONS
# =============================================================================

def floor_plan_to_dict(data: FloorPlanData) -> Dict:
    """Convert FloorPlanData to JSON-serializable dict"""
    return {
        'source': {
            'file': data.source_file,
            'page': data.page_number
        },
        'page_size': {
            'width_pts': data.page_width_pts,
            'height_pts': data.page_height_pts
        },
        'scale': {
            'text': data.scale_text,
            'factor': data.scale_factor
        },
        'floor_plan': {
            'bounds': {
                'min_x': data.fp_min_x,
                'min_y': data.fp_min_y,
                'max_x': data.fp_max_x,
                'max_y': data.fp_max_y
            },
            'width_ft': data.fp_width_ft,
            'height_ft': data.fp_height_ft,
            'area_sqft': data.fp_area_sqft
        },
        'statistics': {
            'total_lines': data.total_lines,
            'wall_count': data.wall_count,
            'pipe_count': data.pipe_count,
            'room_count': data.room_count,
            'total_area_sqft': data.total_area_sqft
        },
        'rooms': [
            {
                'id': r.id,
                'area_sqft': r.area_sqft,
                'width_ft': r.width_ft,
                'height_ft': r.height_ft,
                'centroid': {'x': r.centroid.x, 'y': r.centroid.y},
                'bounds': {
                    'min_x': r.min_x,
                    'max_x': r.max_x,
                    'min_y': r.min_y,
                    'max_y': r.max_y
                },
                'label': r.label,
                'room_type': r.room_type,
                'hazard_class': r.hazard_class
            }
            for r in data.rooms
        ]
    }


def save_floor_plan_json(data: FloorPlanData, output_path: str):
    """Save analysis to JSON file"""
    with open(output_path, 'w') as f:
        json.dump(floor_plan_to_dict(data), f, indent=2)
    logger.info(f"💾 Saved to {output_path}")


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def analyze_floor_plan(pdf_path: str, page_num: int = 0) -> FloorPlanData:
    """
    Analyze a floor plan PDF.
    
    This is the main entry point for floor plan intelligence.
    
    Args:
        pdf_path: Path to PDF file
        page_num: Page number (0-indexed)
    
    Returns:
        FloorPlanData with complete analysis
    """
    analyzer = FloorPlanIntelligence()
    return analyzer.analyze(pdf_path, page_num)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🏗️ FireAI Pro - Floor Plan Intelligence Module v1.0")
    print("=" * 60)
    print("\nIntegrated pipeline:")
    print("  ✅ Step 1.1: PDF geometry extraction")
    print("  ✅ Step 1.2: Wall/pipe color differentiation")  
    print("  ✅ Step 1.3: Room polygon detection")
    print("  ✅ Room labeling and hazard classification")
    print("\nUsage:")
    print("  from floor_plan_intelligence import analyze_floor_plan")
    print("  data = analyze_floor_plan('floor_plan.pdf')")
    print("  print(f'Found {data.room_count} rooms')")
