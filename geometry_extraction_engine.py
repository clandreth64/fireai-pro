#!/usr/bin/env python3
"""
FireAI Pro - Geometry Extraction Engine v1.0
=============================================
Step 1.1: PDF to Vector Conversion

Extracts geometric data from floor plan PDFs:
- Vector paths (lines, polylines, curves)
- Text with positions
- Coordinate system detection
- Scale detection

This is the foundation for wall detection, room extraction,
and intelligent sprinkler placement.

VERSION: 1.0.0
"""

import os
import json
import math
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Tuple, Optional, Any, Set
from collections import defaultdict
from enum import Enum
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.GeometryExtractor")

# Try to import required libraries
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    logger.warning("numpy not available - some features disabled")

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDF not available")

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logger.warning("pdfplumber not available")

try:
    from shapely.geometry import Point, LineString, Polygon, MultiPolygon, box
    from shapely.ops import unary_union, linemerge, polygonize
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False
    logger.warning("shapely not available - polygon operations disabled")

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV not available - image processing disabled")


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class GeometryType(Enum):
    """Types of geometric elements"""
    LINE = "line"
    POLYLINE = "polyline"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    ARC = "arc"
    POLYGON = "polygon"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass
class Point2D:
    """2D Point"""
    x: float
    y: float
    
    def distance_to(self, other: 'Point2D') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)
    
    def __hash__(self):
        return hash((round(self.x, 2), round(self.y, 2)))


@dataclass
class Line2D:
    """2D Line segment"""
    start: Point2D
    end: Point2D
    layer: str = ""
    color: int = 0
    width: float = 0
    
    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)
    
    @property
    def midpoint(self) -> Point2D:
        return Point2D(
            (self.start.x + self.end.x) / 2,
            (self.start.y + self.end.y) / 2
        )
    
    @property
    def angle(self) -> float:
        """Angle in degrees from horizontal"""
        dx = self.end.x - self.start.x
        dy = self.end.y - self.start.y
        return math.degrees(math.atan2(dy, dx))
    
    @property
    def is_horizontal(self) -> bool:
        return abs(self.end.y - self.start.y) < 0.5
    
    @property
    def is_vertical(self) -> bool:
        return abs(self.end.x - self.start.x) < 0.5


@dataclass
class TextElement:
    """Text element with position"""
    text: str
    x: float
    y: float
    width: float = 0
    height: float = 0
    font_size: float = 0
    rotation: float = 0
    
    @property
    def center(self) -> Point2D:
        return Point2D(self.x + self.width/2, self.y + self.height/2)


@dataclass
class Circle2D:
    """Circle element"""
    center: Point2D
    radius: float
    layer: str = ""


@dataclass
class Rectangle2D:
    """Rectangle element"""
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    layer: str = ""
    
    @property
    def width(self) -> float:
        return self.max_x - self.min_x
    
    @property
    def height(self) -> float:
        return self.max_y - self.min_y
    
    @property
    def area(self) -> float:
        return self.width * self.height
    
    @property
    def center(self) -> Point2D:
        return Point2D(
            (self.min_x + self.max_x) / 2,
            (self.min_y + self.max_y) / 2
        )


@dataclass
class ExtractedGeometry:
    """Complete extracted geometry from a document"""
    # Source info
    source_file: str
    page_number: int = 1
    
    # Bounds
    bounds: Rectangle2D = None
    
    # Scale info
    scale_text: str = ""  # e.g., "1/8\" = 1'-0\""
    scale_factor: float = 1.0  # multiplier to get real-world feet
    
    # Geometric elements
    lines: List[Line2D] = field(default_factory=list)
    circles: List[Circle2D] = field(default_factory=list)
    rectangles: List[Rectangle2D] = field(default_factory=list)
    texts: List[TextElement] = field(default_factory=list)
    
    # Layers found
    layers: Dict[str, int] = field(default_factory=dict)  # layer name -> line count
    
    # Statistics
    total_lines: int = 0
    total_texts: int = 0
    extraction_method: str = ""
    confidence: float = 0


# =============================================================================
# PDF VECTOR EXTRACTION
# =============================================================================

class PDFVectorExtractor:
    """Extract vector geometry from PDF files"""
    
    def __init__(self):
        self.tolerance = 0.5  # Tolerance for point matching
    
    def extract(self, pdf_path: str, page_num: int = 0) -> ExtractedGeometry:
        """
        Extract all vector geometry from a PDF page.
        
        Args:
            pdf_path: Path to PDF file
            page_num: Page number (0-indexed)
        
        Returns:
            ExtractedGeometry object with all extracted elements
        """
        result = ExtractedGeometry(
            source_file=pdf_path,
            page_number=page_num + 1
        )
        
        # Try PyMuPDF first
        if PYMUPDF_AVAILABLE:
            try:
                return self._extract_with_pymupdf(pdf_path, page_num)
            except Exception as e:
                logger.warning(f"PyMuPDF extraction failed: {e}")
        
        # Fall back to pdfplumber
        if PDFPLUMBER_AVAILABLE:
            try:
                return self._extract_with_pdfplumber(pdf_path, page_num)
            except Exception as e:
                logger.error(f"pdfplumber extraction failed: {e}")
        
        logger.error("No PDF extraction library available")
        result.extraction_method = "failed"
        return result
    
    def _extract_with_pdfplumber(self, pdf_path: str, page_num: int = 0) -> ExtractedGeometry:
        """Extract using pdfplumber"""
        result = ExtractedGeometry(
            source_file=pdf_path,
            page_number=page_num + 1
        )
        
        with pdfplumber.open(pdf_path) as pdf:
            if page_num >= len(pdf.pages):
                logger.error(f"Page {page_num} not found")
                return result
            
            page = pdf.pages[page_num]
            page_height = page.height
            page_width = page.width
            
            result.bounds = Rectangle2D(
                min_x=0,
                min_y=0,
                max_x=page_width,
                max_y=page_height
            )
            
            logger.info(f"Page size: {page_width:.0f} x {page_height:.0f} pts")
            
            lines = []
            rectangles = []
            circles = []
            layers = defaultdict(int)
            
            # Extract lines
            for line in (page.lines or []):
                x0, y0 = line['x0'], page_height - line['top']
                x1, y1 = line['x1'], page_height - line['bottom']
                
                l = Line2D(
                    start=Point2D(x0, y0),
                    end=Point2D(x1, y1),
                    width=line.get('linewidth', 1),
                    layer='lines'
                )
                if l.length > 1:  # Skip tiny lines
                    lines.append(l)
                    layers['lines'] += 1
            
            # Extract rectangles
            for rect in (page.rects or []):
                x0 = rect['x0']
                y0 = page_height - rect['bottom']
                x1 = rect['x1']
                y1 = page_height - rect['top']
                
                r = Rectangle2D(
                    min_x=min(x0, x1),
                    min_y=min(y0, y1),
                    max_x=max(x0, x1),
                    max_y=max(y0, y1),
                    layer='rects'
                )
                if r.width > 2 and r.height > 2:
                    rectangles.append(r)
                    layers['rects'] += 1
            
            # Extract curves as lines
            for curve in (page.curves or []):
                pts = curve.get('pts', [])
                if len(pts) >= 2:
                    for i in range(len(pts) - 1):
                        p1, p2 = pts[i], pts[i+1]
                        l = Line2D(
                            start=Point2D(p1[0], page_height - p1[1]),
                            end=Point2D(p2[0], page_height - p2[1]),
                            layer='curves'
                        )
                        if l.length > 1:
                            lines.append(l)
                            layers['curves'] += 1
            
            # Extract text
            texts = []
            for word in (page.extract_words() or []):
                texts.append(TextElement(
                    text=word['text'],
                    x=word['x0'],
                    y=page_height - word['top'],
                    width=word['x1'] - word['x0'],
                    height=word['bottom'] - word['top']
                ))
            
            result.lines = lines
            result.rectangles = rectangles
            result.circles = circles
            result.texts = texts
            result.layers = dict(layers)
            result.total_lines = len(lines)
            result.total_texts = len(texts)
            result.extraction_method = "pdfplumber"
            result.confidence = 75 if len(lines) > 100 else 50
            
            # Detect scale
            scale_info = self._detect_scale(texts)
            result.scale_text = scale_info['text']
            result.scale_factor = scale_info['factor']
            
            logger.info(f"Extracted: {len(lines)} lines, {len(rectangles)} rects, {len(texts)} texts")
        
        return result
    
    def _extract_with_pymupdf(self, pdf_path: str, page_num: int = 0) -> ExtractedGeometry:
        """Extract using PyMuPDF (fitz)"""
        result = ExtractedGeometry(
            source_file=pdf_path,
            page_number=page_num + 1
        )
        
        doc = fitz.open(pdf_path)
        if page_num >= len(doc):
            logger.error(f"Page {page_num} not found in PDF")
            return result
        
        page = doc[page_num]
        
        # Get page bounds
        rect = page.rect
        result.bounds = Rectangle2D(
            min_x=rect.x0,
            min_y=rect.y0,
            max_x=rect.x1,
            max_y=rect.y1
        )
        
        logger.info(f"Page bounds: {rect.width:.0f} x {rect.height:.0f} pts")
        
        # Extract vector paths
        paths = self._extract_paths(page)
        result.lines = paths['lines']
        result.circles = paths['circles']
        result.rectangles = paths['rectangles']
        result.layers = paths['layers']
        result.total_lines = len(result.lines)
        
        # Extract text
        result.texts = self._extract_text(page)
        result.total_texts = len(result.texts)
        
        # Detect scale
        scale_info = self._detect_scale(result.texts)
        result.scale_text = scale_info['text']
        result.scale_factor = scale_info['factor']
        
        result.extraction_method = "PyMuPDF vector"
        result.confidence = 85 if result.total_lines > 100 else 50
        
        doc.close()
        
        logger.info(f"Extracted: {result.total_lines} lines, {len(result.circles)} circles, {result.total_texts} texts")
        
        return result
    
    def _extract_paths(self, page) -> Dict:
        """Extract all paths from a PDF page"""
        lines = []
        circles = []
        rectangles = []
        layers = defaultdict(int)
        
        try:
            # Get all drawings on the page
            paths = page.get_drawings()
            
            for path in paths:
                color = path.get('color', (0, 0, 0))
                width = path.get('width', 1)
                
                # Determine "layer" based on color
                if color:
                    layer = f"color_{int(color[0]*255)}_{int(color[1]*255)}_{int(color[2]*255)}"
                else:
                    layer = "default"
                
                # Process each item in the path
                for item in path.get('items', []):
                    item_type = item[0]
                    
                    if item_type == 'l':  # Line
                        p1, p2 = item[1], item[2]
                        line = Line2D(
                            start=Point2D(p1.x, p1.y),
                            end=Point2D(p2.x, p2.y),
                            layer=layer,
                            width=width
                        )
                        if line.length > 1:  # Skip tiny lines
                            lines.append(line)
                            layers[layer] += 1
                    
                    elif item_type == 're':  # Rectangle
                        rect = item[1]
                        rectangles.append(Rectangle2D(
                            min_x=rect.x0,
                            min_y=rect.y0,
                            max_x=rect.x1,
                            max_y=rect.y1,
                            layer=layer
                        ))
                    
                    elif item_type == 'c':  # Cubic Bezier curve
                        # Approximate curves with line segments
                        points = [item[1], item[2], item[3], item[4]]
                        for i in range(len(points) - 1):
                            p1, p2 = points[i], points[i+1]
                            line = Line2D(
                                start=Point2D(p1.x, p1.y),
                                end=Point2D(p2.x, p2.y),
                                layer=layer,
                                width=width
                            )
                            if line.length > 1:
                                lines.append(line)
                                layers[layer] += 1
                    
                    elif item_type == 'qu':  # Quadratic Bezier
                        points = [item[1], item[2], item[3]]
                        for i in range(len(points) - 1):
                            p1, p2 = points[i], points[i+1]
                            line = Line2D(
                                start=Point2D(p1.x, p1.y),
                                end=Point2D(p2.x, p2.y),
                                layer=layer,
                                width=width
                            )
                            if line.length > 1:
                                lines.append(line)
                                layers[layer] += 1
            
            # Detect circles from small rectangles or paths
            # (PDF often represents circles as bezier curves)
            circles = self._detect_circles_from_lines(lines)
            
        except Exception as e:
            logger.error(f"Path extraction error: {e}")
        
        return {
            'lines': lines,
            'circles': circles,
            'rectangles': rectangles,
            'layers': dict(layers)
        }
    
    def _detect_circles_from_lines(self, lines: List[Line2D]) -> List[Circle2D]:
        """Detect circles from curved line segments"""
        circles = []
        
        # Group short lines that might form circles
        # This is a simplified approach - circles in PDFs are often
        # represented as multiple bezier curves
        
        if not NUMPY_AVAILABLE:
            return circles
        
        # Find clusters of short lines
        short_lines = [l for l in lines if l.length < 20]
        
        # Group by proximity
        # (Full implementation would use clustering algorithm)
        
        return circles
    
    def _extract_text(self, page) -> List[TextElement]:
        """Extract all text elements with positions"""
        texts = []
        
        try:
            # Get text blocks with positions
            blocks = page.get_text("dict")["blocks"]
            
            for block in blocks:
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text = span.get("text", "").strip()
                            if text:
                                bbox = span.get("bbox", (0, 0, 0, 0))
                                texts.append(TextElement(
                                    text=text,
                                    x=bbox[0],
                                    y=bbox[1],
                                    width=bbox[2] - bbox[0],
                                    height=bbox[3] - bbox[1],
                                    font_size=span.get("size", 0)
                                ))
        except Exception as e:
            logger.error(f"Text extraction error: {e}")
        
        return texts
    
    def _detect_scale(self, texts: List[TextElement]) -> Dict:
        """Detect drawing scale from text elements"""
        result = {
            'text': '',
            'factor': 1.0
        }
        
        # Common scale patterns
        # Format: (pattern, pts_per_foot)
        # At 72 dpi: 1/8" = 1'-0" means 72/8 = 9 pts = 1 foot
        scale_patterns = [
            # 1/8" = 1'-0"
            (r'1/8["\s]*=\s*1[\'\-]', 9.0),
            # 1/4" = 1'-0"
            (r'1/4["\s]*=\s*1[\'\-]', 18.0),
            # 1/16" = 1'-0"
            (r'1/16["\s]*=\s*1[\'\-]', 4.5),
            # 3/32" = 1'-0"
            (r'3/32["\s]*=\s*1[\'\-]', 6.75),
            # 1" = 10'
            (r'1["\s]*=\s*10[\'\-]', 7.2),
            # 1" = 20'
            (r'1["\s]*=\s*20[\'\-]', 3.6),
            # SCALE: 1/8" = 1'-0"
            (r'SCALE[:\s]*1/8', 9.0),
            (r'SCALE[:\s]*1/4', 18.0),
        ]
        
        for text_elem in texts:
            text_upper = text_elem.text.upper()
            for pattern, factor in scale_patterns:
                if re.search(pattern, text_upper, re.IGNORECASE):
                    result['text'] = text_elem.text
                    result['factor'] = factor
                    logger.info(f"Detected scale: {text_elem.text} (factor: {factor})")
                    return result
        
        # Default assumption: 1/8" = 1'-0" (common for floor plans)
        result['factor'] = 9.0
        result['text'] = "assumed 1/8\" = 1'-0\""
        
        return result


# =============================================================================
# DXF VECTOR EXTRACTION
# =============================================================================

class DXFVectorExtractor:
    """Extract vector geometry from DXF files"""
    
    def __init__(self):
        try:
            import ezdxf
            self.ezdxf = ezdxf
            self.available = True
        except ImportError:
            self.available = False
            logger.warning("ezdxf not available - DXF extraction disabled")
    
    def extract(self, dxf_path: str) -> ExtractedGeometry:
        """Extract geometry from DXF file"""
        result = ExtractedGeometry(
            source_file=dxf_path,
            page_number=1
        )
        
        if not self.available:
            result.extraction_method = "failed"
            return result
        
        try:
            doc = self.ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
            
            lines = []
            circles = []
            rectangles = []
            texts = []
            layers = defaultdict(int)
            
            min_x = min_y = float('inf')
            max_x = max_y = float('-inf')
            
            for entity in msp:
                etype = entity.dxftype()
                layer = entity.dxf.layer if hasattr(entity.dxf, 'layer') else 'default'
                
                if etype == 'LINE':
                    start = entity.dxf.start
                    end = entity.dxf.end
                    line = Line2D(
                        start=Point2D(start.x, start.y),
                        end=Point2D(end.x, end.y),
                        layer=layer
                    )
                    lines.append(line)
                    layers[layer] += 1
                    
                    # Update bounds
                    min_x = min(min_x, start.x, end.x)
                    max_x = max(max_x, start.x, end.x)
                    min_y = min(min_y, start.y, end.y)
                    max_y = max(max_y, start.y, end.y)
                
                elif etype == 'LWPOLYLINE':
                    points = list(entity.get_points())
                    for i in range(len(points) - 1):
                        p1, p2 = points[i], points[i+1]
                        line = Line2D(
                            start=Point2D(p1[0], p1[1]),
                            end=Point2D(p2[0], p2[1]),
                            layer=layer
                        )
                        lines.append(line)
                        layers[layer] += 1
                        
                        min_x = min(min_x, p1[0], p2[0])
                        max_x = max(max_x, p1[0], p2[0])
                        min_y = min(min_y, p1[1], p2[1])
                        max_y = max(max_y, p1[1], p2[1])
                    
                    # Close polyline if needed
                    if entity.closed and len(points) > 2:
                        p1, p2 = points[-1], points[0]
                        line = Line2D(
                            start=Point2D(p1[0], p1[1]),
                            end=Point2D(p2[0], p2[1]),
                            layer=layer
                        )
                        lines.append(line)
                
                elif etype == 'CIRCLE':
                    center = entity.dxf.center
                    radius = entity.dxf.radius
                    circles.append(Circle2D(
                        center=Point2D(center.x, center.y),
                        radius=radius,
                        layer=layer
                    ))
                    
                    min_x = min(min_x, center.x - radius)
                    max_x = max(max_x, center.x + radius)
                    min_y = min(min_y, center.y - radius)
                    max_y = max(max_y, center.y + radius)
                
                elif etype in ('TEXT', 'MTEXT'):
                    text = entity.dxf.text if etype == 'TEXT' else entity.text
                    insert = entity.dxf.insert
                    texts.append(TextElement(
                        text=text,
                        x=insert.x,
                        y=insert.y,
                        height=entity.dxf.height if hasattr(entity.dxf, 'height') else 0
                    ))
            
            result.lines = lines
            result.circles = circles
            result.texts = texts
            result.layers = dict(layers)
            result.total_lines = len(lines)
            result.total_texts = len(texts)
            
            if min_x != float('inf'):
                result.bounds = Rectangle2D(min_x, min_y, max_x, max_y)
            
            result.extraction_method = "ezdxf"
            result.confidence = 90
            
            logger.info(f"DXF extracted: {len(lines)} lines, {len(circles)} circles, {len(texts)} texts")
            logger.info(f"Layers: {list(layers.keys())}")
            
        except Exception as e:
            logger.error(f"DXF extraction failed: {e}")
            result.extraction_method = "failed"
        
        return result


# =============================================================================
# IMAGE-BASED EXTRACTION (Fallback)
# =============================================================================

class ImageVectorExtractor:
    """Extract geometry from raster images using computer vision"""
    
    def __init__(self):
        self.available = CV2_AVAILABLE and NUMPY_AVAILABLE
    
    def extract_from_image(self, image_path: str) -> ExtractedGeometry:
        """Extract lines from image using edge detection"""
        result = ExtractedGeometry(
            source_file=image_path,
            page_number=1
        )
        
        if not self.available:
            result.extraction_method = "failed"
            return result
        
        try:
            # Load image
            img = cv2.imread(image_path)
            if img is None:
                logger.error(f"Could not load image: {image_path}")
                return result
            
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            height, width = gray.shape
            
            result.bounds = Rectangle2D(0, 0, width, height)
            
            # Edge detection
            edges = cv2.Canny(gray, 50, 150, apertureSize=3)
            
            # Hough Line Transform
            lines_detected = cv2.HoughLinesP(
                edges,
                rho=1,
                theta=np.pi/180,
                threshold=50,
                minLineLength=20,
                maxLineGap=10
            )
            
            lines = []
            if lines_detected is not None:
                for line in lines_detected:
                    x1, y1, x2, y2 = line[0]
                    lines.append(Line2D(
                        start=Point2D(x1, y1),
                        end=Point2D(x2, y2),
                        layer="detected"
                    ))
            
            result.lines = lines
            result.total_lines = len(lines)
            result.extraction_method = "OpenCV Hough"
            result.confidence = 60
            
            logger.info(f"Image extraction: {len(lines)} lines detected")
            
        except Exception as e:
            logger.error(f"Image extraction failed: {e}")
            result.extraction_method = "failed"
        
        return result
    
    def extract_from_pdf_image(self, pdf_path: str, page_num: int = 0, dpi: int = 150) -> ExtractedGeometry:
        """Convert PDF page to image and extract"""
        result = ExtractedGeometry(
            source_file=pdf_path,
            page_number=page_num + 1
        )
        
        if not PYMUPDF_AVAILABLE:
            result.extraction_method = "failed"
            return result
        
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_num]
            
            # Render page to image
            mat = fitz.Matrix(dpi/72, dpi/72)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to numpy array
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            
            if pix.n == 4:  # RGBA
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif pix.n == 1:  # Grayscale
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            
            # Save temp image and extract
            temp_path = "/tmp/temp_floor_plan.png"
            cv2.imwrite(temp_path, img)
            
            result = self.extract_from_image(temp_path)
            result.source_file = pdf_path
            result.page_number = page_num + 1
            
            # Adjust scale for DPI
            result.scale_factor = 72 / dpi * 96  # Assuming 1/8" = 1'-0"
            
            doc.close()
            
        except Exception as e:
            logger.error(f"PDF image extraction failed: {e}")
            result.extraction_method = "failed"
        
        return result


# =============================================================================
# UNIFIED GEOMETRY EXTRACTOR
# =============================================================================

class GeometryExtractor:
    """
    Unified geometry extraction from any supported format.
    
    Automatically selects the best extraction method based on file type.
    """
    
    def __init__(self):
        self.pdf_extractor = PDFVectorExtractor()
        self.dxf_extractor = DXFVectorExtractor()
        self.image_extractor = ImageVectorExtractor()
    
    def extract(self, file_path: str, **kwargs) -> ExtractedGeometry:
        """
        Extract geometry from file.
        
        Supports: PDF, DXF, DWG, PNG, JPG
        
        Args:
            file_path: Path to file
            **kwargs: Additional options (page_num for PDF, etc.)
        
        Returns:
            ExtractedGeometry object
        """
        file_path = str(file_path)
        ext = Path(file_path).suffix.lower()
        
        logger.info(f"Extracting geometry from: {file_path}")
        
        if ext == '.pdf':
            page_num = kwargs.get('page_num', 0)
            
            # Try vector extraction first
            result = self.pdf_extractor.extract(file_path, page_num)
            
            # If not enough lines, fall back to image extraction
            if result.total_lines < 50 and self.image_extractor.available:
                logger.info("Few vectors found, trying image extraction...")
                img_result = self.image_extractor.extract_from_pdf_image(file_path, page_num)
                if img_result.total_lines > result.total_lines:
                    result = img_result
            
            return result
        
        elif ext in ['.dxf', '.dwg']:
            return self.dxf_extractor.extract(file_path)
        
        elif ext in ['.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp']:
            return self.image_extractor.extract_from_image(file_path)
        
        else:
            logger.error(f"Unsupported file type: {ext}")
            return ExtractedGeometry(
                source_file=file_path,
                extraction_method="unsupported"
            )
    
    def analyze_geometry(self, geometry: ExtractedGeometry) -> Dict[str, Any]:
        """
        Analyze extracted geometry to understand the drawing.
        
        Returns statistics and detected features.
        """
        analysis = {
            'total_lines': geometry.total_lines,
            'total_texts': geometry.total_texts,
            'bounds': None,
            'layers': geometry.layers,
            'line_length_stats': {},
            'orientation_stats': {},
            'potential_walls': 0,
            'potential_columns': 0,
            'room_labels': [],
            'dimension_texts': [],
        }
        
        if geometry.bounds:
            analysis['bounds'] = {
                'width': geometry.bounds.width,
                'height': geometry.bounds.height,
                'area': geometry.bounds.area
            }
        
        if not geometry.lines:
            return analysis
        
        # Line length statistics
        lengths = [line.length for line in geometry.lines]
        analysis['line_length_stats'] = {
            'min': min(lengths),
            'max': max(lengths),
            'avg': sum(lengths) / len(lengths),
            'total': sum(lengths)
        }
        
        # Orientation statistics
        horizontal = sum(1 for line in geometry.lines if line.is_horizontal)
        vertical = sum(1 for line in geometry.lines if line.is_vertical)
        diagonal = len(geometry.lines) - horizontal - vertical
        
        analysis['orientation_stats'] = {
            'horizontal': horizontal,
            'vertical': vertical,
            'diagonal': diagonal,
            'h_v_ratio': horizontal / max(1, vertical)
        }
        
        # Potential walls (long lines)
        avg_length = analysis['line_length_stats']['avg']
        analysis['potential_walls'] = sum(
            1 for line in geometry.lines 
            if line.length > avg_length * 2 and (line.is_horizontal or line.is_vertical)
        )
        
        # Potential columns (circles)
        analysis['potential_columns'] = len(geometry.circles)
        
        # Analyze texts
        for text in geometry.texts:
            text_upper = text.text.upper()
            
            # Room labels
            room_keywords = ['OFFICE', 'CONF', 'ROOM', 'LOBBY', 'CORRIDOR', 
                           'RESTROOM', 'KITCHEN', 'STORAGE', 'ELEC', 'MECH',
                           'STAIR', 'ELEV', 'CLOSET', 'BREAK']
            if any(kw in text_upper for kw in room_keywords):
                analysis['room_labels'].append({
                    'text': text.text,
                    'x': text.x,
                    'y': text.y
                })
            
            # Dimension texts (numbers with ' or ")
            if re.search(r'\d+[\'\"-]', text.text):
                analysis['dimension_texts'].append({
                    'text': text.text,
                    'x': text.x,
                    'y': text.y
                })
        
        return analysis
    
    def export_to_json(self, geometry: ExtractedGeometry, output_path: str) -> bool:
        """Export geometry to JSON file"""
        try:
            data = {
                'source_file': geometry.source_file,
                'page_number': geometry.page_number,
                'extraction_method': geometry.extraction_method,
                'confidence': geometry.confidence,
                'scale_text': geometry.scale_text,
                'scale_factor': geometry.scale_factor,
                'bounds': asdict(geometry.bounds) if geometry.bounds else None,
                'statistics': {
                    'total_lines': geometry.total_lines,
                    'total_circles': len(geometry.circles),
                    'total_texts': geometry.total_texts,
                    'layers': geometry.layers
                },
                'lines': [
                    {
                        'start': [l.start.x, l.start.y],
                        'end': [l.end.x, l.end.y],
                        'length': l.length,
                        'layer': l.layer
                    }
                    for l in geometry.lines[:1000]  # Limit for file size
                ],
                'circles': [
                    {
                        'center': [c.center.x, c.center.y],
                        'radius': c.radius,
                        'layer': c.layer
                    }
                    for c in geometry.circles
                ],
                'texts': [
                    {
                        'text': t.text,
                        'x': t.x,
                        'y': t.y,
                        'font_size': t.font_size
                    }
                    for t in geometry.texts
                ]
            }
            
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"Geometry exported to: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return False


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def extract_geometry(file_path: str, **kwargs) -> ExtractedGeometry:
    """
    Extract geometry from a floor plan file.
    
    Args:
        file_path: Path to PDF, DXF, or image file
        **kwargs: Additional options
    
    Returns:
        ExtractedGeometry object
    """
    extractor = GeometryExtractor()
    return extractor.extract(file_path, **kwargs)


def analyze_floor_plan_geometry(file_path: str) -> Dict[str, Any]:
    """
    Extract and analyze floor plan geometry.
    
    Returns both raw geometry and analysis.
    """
    extractor = GeometryExtractor()
    geometry = extractor.extract(file_path)
    analysis = extractor.analyze_geometry(geometry)
    
    return {
        'geometry': geometry,
        'analysis': analysis
    }


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🔧 FireAI Pro - Geometry Extraction Engine v1.0")
    print("=" * 60)
    print("\n📋 CAPABILITIES:")
    print(f"   {'✅' if PYMUPDF_AVAILABLE else '❌'} PDF Vector Extraction (PyMuPDF)")
    print(f"   {'✅' if NUMPY_AVAILABLE else '❌'} Numerical Processing (NumPy)")
    print(f"   {'✅' if SHAPELY_AVAILABLE else '❌'} Polygon Operations (Shapely)")
    print(f"   {'✅' if CV2_AVAILABLE else '❌'} Image Processing (OpenCV)")
    
    print("\n📄 SUPPORTED FORMATS:")
    print("   • PDF (vector extraction + image fallback)")
    print("   • DXF/DWG (native CAD)")
    print("   • PNG/JPG (image-based extraction)")
    
    print("\n🔍 EXTRACTS:")
    print("   • Lines and polylines")
    print("   • Circles and arcs")
    print("   • Text with positions")
    print("   • Drawing scale")
    print("   • Layer information")
    
    print("\n💡 USAGE:")
    print("   geometry = extract_geometry('floor_plan.pdf')")
    print("   analysis = analyze_floor_plan_geometry('floor_plan.pdf')")
