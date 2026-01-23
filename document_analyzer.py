#!/usr/bin/env python3
"""
FireAI Pro - Document Analysis Engine
=====================================
Parses construction documents (PDF, DWG, images) to extract building information
for automated fire sprinkler system design.

CAPABILITIES:
- PDF architectural drawing analysis
- DWG/DXF file parsing
- Image-based plan recognition
- Text extraction for specifications
- Room/zone identification
- Dimension extraction
- Occupancy type detection

VERSION: 1.0.0
"""

import os
import json
import math
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.DocumentAnalysis")


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class OccupancyType(Enum):
    """IBC Occupancy Classifications"""
    ASSEMBLY_A1 = "A-1"  # Assembly with fixed seating (theaters)
    ASSEMBLY_A2 = "A-2"  # Assembly food/drink (restaurants)
    ASSEMBLY_A3 = "A-3"  # Assembly worship, recreation
    ASSEMBLY_A4 = "A-4"  # Assembly viewing (arenas)
    ASSEMBLY_A5 = "A-5"  # Assembly outdoor
    BUSINESS_B = "B"      # Business (offices)
    EDUCATIONAL_E = "E"   # Educational
    FACTORY_F1 = "F-1"    # Factory moderate hazard
    FACTORY_F2 = "F-2"    # Factory low hazard
    HIGH_HAZARD_H1 = "H-1"  # High hazard detonation
    HIGH_HAZARD_H2 = "H-2"  # High hazard deflagration
    HIGH_HAZARD_H3 = "H-3"  # High hazard physical
    HIGH_HAZARD_H4 = "H-4"  # High hazard health
    HIGH_HAZARD_H5 = "H-5"  # High hazard semiconductor
    INSTITUTIONAL_I1 = "I-1"  # Institutional supervised
    INSTITUTIONAL_I2 = "I-2"  # Institutional medical
    INSTITUTIONAL_I3 = "I-3"  # Institutional restrained
    INSTITUTIONAL_I4 = "I-4"  # Institutional daycare
    MERCANTILE_M = "M"    # Mercantile (retail)
    RESIDENTIAL_R1 = "R-1"  # Residential transient (hotels)
    RESIDENTIAL_R2 = "R-2"  # Residential permanent (apartments)
    RESIDENTIAL_R3 = "R-3"  # Residential 1-2 family
    RESIDENTIAL_R4 = "R-4"  # Residential care
    STORAGE_S1 = "S-1"    # Storage moderate hazard
    STORAGE_S2 = "S-2"    # Storage low hazard
    UTILITY_U = "U"       # Utility/miscellaneous


class HazardClass(Enum):
    """NFPA 13 Hazard Classifications"""
    LIGHT = "light_hazard"
    ORDINARY_1 = "ordinary_hazard_group_1"
    ORDINARY_2 = "ordinary_hazard_group_2"
    EXTRA_1 = "extra_hazard_group_1"
    EXTRA_2 = "extra_hazard_group_2"


class ConstructionType(Enum):
    """IBC Construction Types"""
    TYPE_IA = "I-A"   # Fire resistive (3hr)
    TYPE_IB = "I-B"   # Fire resistive (2hr)
    TYPE_IIA = "II-A"  # Non-combustible (1hr)
    TYPE_IIB = "II-B"  # Non-combustible (0hr)
    TYPE_IIIA = "III-A" # Ordinary (1hr)
    TYPE_IIIB = "III-B" # Ordinary (0hr)
    TYPE_IV = "IV"     # Heavy timber
    TYPE_VA = "V-A"    # Wood frame (1hr)
    TYPE_VB = "V-B"    # Wood frame (0hr)


# Occupancy to Hazard Class mapping per NFPA 13
OCCUPANCY_TO_HAZARD = {
    OccupancyType.ASSEMBLY_A1: HazardClass.LIGHT,
    OccupancyType.ASSEMBLY_A2: HazardClass.ORDINARY_1,
    OccupancyType.ASSEMBLY_A3: HazardClass.LIGHT,
    OccupancyType.BUSINESS_B: HazardClass.LIGHT,
    OccupancyType.EDUCATIONAL_E: HazardClass.LIGHT,
    OccupancyType.FACTORY_F1: HazardClass.ORDINARY_2,
    OccupancyType.FACTORY_F2: HazardClass.ORDINARY_1,
    OccupancyType.MERCANTILE_M: HazardClass.ORDINARY_1,
    OccupancyType.RESIDENTIAL_R1: HazardClass.LIGHT,
    OccupancyType.RESIDENTIAL_R2: HazardClass.LIGHT,
    OccupancyType.STORAGE_S1: HazardClass.ORDINARY_2,
    OccupancyType.STORAGE_S2: HazardClass.ORDINARY_1,
}

# Room keywords to occupancy mapping
ROOM_KEYWORDS = {
    'office': OccupancyType.BUSINESS_B,
    'conference': OccupancyType.BUSINESS_B,
    'meeting': OccupancyType.BUSINESS_B,
    'lobby': OccupancyType.BUSINESS_B,
    'reception': OccupancyType.BUSINESS_B,
    'break room': OccupancyType.BUSINESS_B,
    'kitchen': OccupancyType.ASSEMBLY_A2,
    'restaurant': OccupancyType.ASSEMBLY_A2,
    'dining': OccupancyType.ASSEMBLY_A2,
    'cafeteria': OccupancyType.ASSEMBLY_A2,
    'classroom': OccupancyType.EDUCATIONAL_E,
    'school': OccupancyType.EDUCATIONAL_E,
    'library': OccupancyType.ASSEMBLY_A3,
    'gymnasium': OccupancyType.ASSEMBLY_A3,
    'gym': OccupancyType.ASSEMBLY_A3,
    'theater': OccupancyType.ASSEMBLY_A1,
    'auditorium': OccupancyType.ASSEMBLY_A1,
    'chapel': OccupancyType.ASSEMBLY_A3,
    'church': OccupancyType.ASSEMBLY_A3,
    'retail': OccupancyType.MERCANTILE_M,
    'store': OccupancyType.MERCANTILE_M,
    'shop': OccupancyType.MERCANTILE_M,
    'sales': OccupancyType.MERCANTILE_M,
    'warehouse': OccupancyType.STORAGE_S1,
    'storage': OccupancyType.STORAGE_S2,
    'mechanical': OccupancyType.STORAGE_S2,
    'electrical': OccupancyType.STORAGE_S2,
    'janitor': OccupancyType.STORAGE_S2,
    'hotel': OccupancyType.RESIDENTIAL_R1,
    'motel': OccupancyType.RESIDENTIAL_R1,
    'guest': OccupancyType.RESIDENTIAL_R1,
    'apartment': OccupancyType.RESIDENTIAL_R2,
    'residential': OccupancyType.RESIDENTIAL_R2,
    'dwelling': OccupancyType.RESIDENTIAL_R2,
    'manufacturing': OccupancyType.FACTORY_F1,
    'factory': OccupancyType.FACTORY_F1,
    'assembly': OccupancyType.FACTORY_F1,
    'production': OccupancyType.FACTORY_F1,
    'lab': OccupancyType.BUSINESS_B,
    'laboratory': OccupancyType.BUSINESS_B,
    'server': OccupancyType.BUSINESS_B,
    'data center': OccupancyType.BUSINESS_B,
    'parking': OccupancyType.STORAGE_S2,
    'garage': OccupancyType.STORAGE_S2,
    'loading': OccupancyType.STORAGE_S1,
    'dock': OccupancyType.STORAGE_S1,
    'restroom': OccupancyType.BUSINESS_B,
    'bathroom': OccupancyType.BUSINESS_B,
    'toilet': OccupancyType.BUSINESS_B,
    'corridor': OccupancyType.BUSINESS_B,
    'hallway': OccupancyType.BUSINESS_B,
    'stair': OccupancyType.BUSINESS_B,
    'elevator': OccupancyType.BUSINESS_B,
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Point:
    x: float
    y: float
    
    def distance_to(self, other: 'Point') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)


@dataclass
class Room:
    """Represents a room/zone in the building"""
    id: str
    name: str
    vertices: List[Point]  # Polygon vertices
    area_sqft: float = 0.0
    ceiling_height_ft: float = 10.0
    occupancy: Optional[OccupancyType] = None
    hazard_class: Optional[HazardClass] = None
    
    # Special conditions
    has_drop_ceiling: bool = False
    has_obstructions: bool = False
    is_sprinklered: bool = True  # Assume yes unless exempted
    
    # Calculated
    centroid: Optional[Point] = None
    
    def __post_init__(self):
        if self.vertices and len(self.vertices) >= 3:
            self.area_sqft = self._calculate_area()
            self.centroid = self._calculate_centroid()
    
    def _calculate_area(self) -> float:
        """Shoelace formula for polygon area"""
        n = len(self.vertices)
        if n < 3:
            return 0.0
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += self.vertices[i].x * self.vertices[j].y
            area -= self.vertices[j].x * self.vertices[i].y
        return abs(area) / 2.0
    
    def _calculate_centroid(self) -> Point:
        """Calculate centroid of polygon"""
        n = len(self.vertices)
        if n == 0:
            return Point(0, 0)
        cx = sum(v.x for v in self.vertices) / n
        cy = sum(v.y for v in self.vertices) / n
        return Point(cx, cy)


@dataclass
class Obstruction:
    """Represents an obstruction (column, beam, duct, etc.)"""
    id: str
    type: str  # column, beam, duct, equipment
    x: float
    y: float
    width: float
    depth: float
    height: float = 0.0
    clearance_below: float = 0.0


@dataclass
class BuildingAnalysis:
    """Complete building analysis result"""
    project_id: str
    project_name: str
    analysis_timestamp: datetime
    
    # Building geometry
    total_area_sqft: float = 0.0
    building_length_ft: float = 0.0
    building_width_ft: float = 0.0
    num_stories: int = 1
    typical_ceiling_height_ft: float = 10.0
    building_height_ft: float = 0.0
    
    # Classifications
    primary_occupancy: Optional[OccupancyType] = None
    construction_type: Optional[ConstructionType] = None
    primary_hazard_class: Optional[HazardClass] = None
    
    # Location
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    
    # Rooms/zones
    rooms: List[Room] = field(default_factory=list)
    obstructions: List[Obstruction] = field(default_factory=list)
    
    # Extracted data
    extracted_text: List[str] = field(default_factory=list)
    drawing_scale: str = ""
    
    # Code requirements determined
    sprinkler_required: bool = True
    standpipe_required: bool = False
    fire_alarm_required: bool = True
    fire_pump_required: bool = False
    
    # Confidence
    analysis_confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)
    

# =============================================================================
# DOCUMENT PARSERS
# =============================================================================

class PDFParser:
    """Parse PDF construction documents"""
    
    def __init__(self):
        self.pdf_available = False
        try:
            import PyPDF2
            self.pdf_available = True
            logger.info("✅ PyPDF2 loaded for PDF parsing")
        except ImportError:
            logger.warning("⚠️ PyPDF2 not available")
        
        try:
            import pdfplumber
            self.pdfplumber_available = True
            logger.info("✅ pdfplumber loaded for PDF parsing")
        except ImportError:
            self.pdfplumber_available = False
    
    def parse(self, pdf_path: str) -> Dict[str, Any]:
        """Parse PDF and extract text and basic info"""
        result = {
            'text': [],
            'num_pages': 0,
            'dimensions': [],
            'room_names': [],
            'occupancy_hints': [],
            'scale': None
        }
        
        if not os.path.exists(pdf_path):
            logger.error(f"PDF file not found: {pdf_path}")
            return result
        
        # Try pdfplumber first (better for drawings)
        if self.pdfplumber_available:
            try:
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    result['num_pages'] = len(pdf.pages)
                    for page in pdf.pages:
                        text = page.extract_text() or ""
                        result['text'].append(text)
                        
                        # Extract dimensions
                        dims = self._extract_dimensions(text)
                        result['dimensions'].extend(dims)
                        
                        # Extract room names
                        rooms = self._extract_room_names(text)
                        result['room_names'].extend(rooms)
                        
                        # Extract scale
                        scale = self._extract_scale(text)
                        if scale:
                            result['scale'] = scale
                
                logger.info(f"Parsed PDF: {result['num_pages']} pages, {len(result['room_names'])} rooms found")
                return result
            except Exception as e:
                logger.warning(f"pdfplumber parsing failed: {e}")
        
        # Fall back to PyPDF2
        if self.pdf_available:
            try:
                import PyPDF2
                with open(pdf_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    result['num_pages'] = len(reader.pages)
                    for page in reader.pages:
                        text = page.extract_text() or ""
                        result['text'].append(text)
                        
                        dims = self._extract_dimensions(text)
                        result['dimensions'].extend(dims)
                        
                        rooms = self._extract_room_names(text)
                        result['room_names'].extend(rooms)
                
                logger.info(f"Parsed PDF with PyPDF2: {result['num_pages']} pages")
                return result
            except Exception as e:
                logger.error(f"PyPDF2 parsing failed: {e}")
        
        return result
    
    def _extract_dimensions(self, text: str) -> List[Dict]:
        """Extract dimension annotations from text"""
        dimensions = []
        
        # Pattern: XX'-YY" or XX' or XXft or XX.XX'
        patterns = [
            r"(\d+)'-(\d+)\"",  # 10'-6"
            r"(\d+(?:\.\d+)?)'",  # 10' or 10.5'
            r"(\d+(?:\.\d+)?)\s*(?:ft|FT|feet|FEET)",  # 10 ft
            r"(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)",  # 100 x 50
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    dimensions.append({'raw': match, 'pattern': pattern})
                else:
                    dimensions.append({'raw': match, 'pattern': pattern})
        
        return dimensions
    
    def _extract_room_names(self, text: str) -> List[str]:
        """Extract room names from text"""
        rooms = []
        
        # Common room name patterns
        patterns = [
            r"(?:ROOM|RM|SPACE)\s*(?:#|NO\.?)?\s*(\d+[A-Z]?)",
            r"(\w+(?:\s+\w+)*)\s*(?:ROOM|RM)",
        ]
        
        for keyword in ROOM_KEYWORDS.keys():
            if keyword.lower() in text.lower():
                # Find the full room name context
                pattern = rf"({keyword}(?:\s+\w+)?(?:\s+\d+)?)"
                matches = re.findall(pattern, text, re.IGNORECASE)
                rooms.extend(matches)
        
        return list(set(rooms))
    
    def _extract_scale(self, text: str) -> Optional[str]:
        """Extract drawing scale"""
        patterns = [
            r"SCALE[:\s]*(\d+\/\d+\"\s*=\s*\d+'-\d+\")",
            r"SCALE[:\s]*(\d+\"\s*=\s*\d+'-\d+\")",
            r"(\d+\/\d+\"\s*=\s*\d+')",
            r"1[:\s]*(\d+)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None


class DXFParser:
    """Parse DXF/DWG files"""
    
    def __init__(self):
        self.ezdxf_available = False
        try:
            import ezdxf
            self.ezdxf_available = True
            logger.info("✅ ezdxf loaded for DXF parsing")
        except ImportError:
            logger.warning("⚠️ ezdxf not available for DXF parsing")
    
    def parse(self, dxf_path: str) -> Dict[str, Any]:
        """Parse DXF file and extract geometry"""
        result = {
            'layers': [],
            'lines': [],
            'polylines': [],
            'circles': [],
            'text': [],
            'dimensions': [],
            'blocks': [],
            'bounds': None
        }
        
        if not self.ezdxf_available:
            logger.error("ezdxf not available for DXF parsing")
            return result
        
        if not os.path.exists(dxf_path):
            logger.error(f"DXF file not found: {dxf_path}")
            return result
        
        try:
            import ezdxf
            doc = ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
            
            # Get layers
            for layer in doc.layers:
                result['layers'].append(layer.dxf.name)
            
            # Get entities
            min_x = min_y = float('inf')
            max_x = max_y = float('-inf')
            
            for entity in msp:
                etype = entity.dxftype()
                
                if etype == 'LINE':
                    start = (entity.dxf.start.x, entity.dxf.start.y)
                    end = (entity.dxf.end.x, entity.dxf.end.y)
                    result['lines'].append({'start': start, 'end': end, 'layer': entity.dxf.layer})
                    
                    min_x = min(min_x, start[0], end[0])
                    min_y = min(min_y, start[1], end[1])
                    max_x = max(max_x, start[0], end[0])
                    max_y = max(max_y, start[1], end[1])
                
                elif etype == 'LWPOLYLINE':
                    points = [(p[0], p[1]) for p in entity.get_points()]
                    result['polylines'].append({'points': points, 'layer': entity.dxf.layer, 'closed': entity.closed})
                    
                    for p in points:
                        min_x = min(min_x, p[0])
                        min_y = min(min_y, p[1])
                        max_x = max(max_x, p[0])
                        max_y = max(max_y, p[1])
                
                elif etype == 'CIRCLE':
                    center = (entity.dxf.center.x, entity.dxf.center.y)
                    result['circles'].append({'center': center, 'radius': entity.dxf.radius, 'layer': entity.dxf.layer})
                
                elif etype in ('TEXT', 'MTEXT'):
                    text_content = entity.dxf.text if etype == 'TEXT' else entity.text
                    pos = (entity.dxf.insert.x, entity.dxf.insert.y)
                    result['text'].append({'text': text_content, 'position': pos, 'layer': entity.dxf.layer})
                
                elif etype == 'DIMENSION':
                    result['dimensions'].append({'layer': entity.dxf.layer})
                
                elif etype == 'INSERT':
                    result['blocks'].append({'name': entity.dxf.name, 'position': (entity.dxf.insert.x, entity.dxf.insert.y)})
            
            if min_x != float('inf'):
                result['bounds'] = {
                    'min_x': min_x, 'min_y': min_y,
                    'max_x': max_x, 'max_y': max_y,
                    'width': max_x - min_x,
                    'height': max_y - min_y
                }
            
            logger.info(f"Parsed DXF: {len(result['lines'])} lines, {len(result['polylines'])} polylines, {len(result['text'])} text entities")
            return result
            
        except Exception as e:
            logger.error(f"DXF parsing failed: {e}")
            return result


class ImageAnalyzer:
    """Analyze floor plan images using pattern recognition"""
    
    def __init__(self):
        self.pillow_available = False
        try:
            from PIL import Image
            self.pillow_available = True
            logger.info("✅ Pillow loaded for image analysis")
        except ImportError:
            logger.warning("⚠️ Pillow not available")
    
    def analyze(self, image_path: str) -> Dict[str, Any]:
        """Analyze floor plan image"""
        result = {
            'width': 0,
            'height': 0,
            'aspect_ratio': 0,
            'detected_rooms': [],
            'detected_text': []
        }
        
        if not self.pillow_available:
            return result
        
        try:
            from PIL import Image
            img = Image.open(image_path)
            result['width'] = img.width
            result['height'] = img.height
            result['aspect_ratio'] = img.width / img.height if img.height > 0 else 0
            
            logger.info(f"Image analyzed: {img.width}x{img.height}")
            return result
            
        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            return result


# =============================================================================
# BUILDING ANALYZER
# =============================================================================

class BuildingAnalyzer:
    """
    Main building analysis engine.
    Combines data from multiple document sources to create a complete building model.
    """
    
    def __init__(self):
        self.pdf_parser = PDFParser()
        self.dxf_parser = DXFParser()
        self.image_analyzer = ImageAnalyzer()
    
    def analyze(self, project_dir: str, project_data: Dict = None) -> BuildingAnalysis:
        """
        Analyze all documents in project directory and create building model.
        """
        project_data = project_data or {}
        
        analysis = BuildingAnalysis(
            project_id=project_data.get('project_id', 'UNKNOWN'),
            project_name=project_data.get('project_name', 'Unknown Project'),
            analysis_timestamp=datetime.now()
        )
        
        # Find and process all documents
        project_path = Path(project_dir)
        
        pdf_data = []
        dxf_data = []
        image_data = []
        
        for file_path in project_path.iterdir():
            if file_path.is_file():
                ext = file_path.suffix.lower()
                
                if ext == '.pdf':
                    logger.info(f"Processing PDF: {file_path.name}")
                    data = self.pdf_parser.parse(str(file_path))
                    pdf_data.append(data)
                
                elif ext in ['.dxf', '.dwg']:
                    logger.info(f"Processing DXF: {file_path.name}")
                    data = self.dxf_parser.parse(str(file_path))
                    dxf_data.append(data)
                
                elif ext in ['.png', '.jpg', '.jpeg', '.tif', '.tiff']:
                    logger.info(f"Processing image: {file_path.name}")
                    data = self.image_analyzer.analyze(str(file_path))
                    image_data.append(data)
        
        # Combine extracted data
        self._process_pdf_data(analysis, pdf_data)
        self._process_dxf_data(analysis, dxf_data)
        
        # Apply manual overrides from project_data
        if project_data.get('building_area_sqft'):
            analysis.total_area_sqft = project_data['building_area_sqft']
        if project_data.get('ceiling_height_ft'):
            analysis.typical_ceiling_height_ft = project_data['ceiling_height_ft']
        if project_data.get('num_stories'):
            analysis.num_stories = project_data['num_stories']
        if project_data.get('zip_code'):
            analysis.zip_code = project_data['zip_code']
        if project_data.get('hazard_class'):
            analysis.primary_hazard_class = HazardClass(project_data['hazard_class'])
        
        # Determine building classifications
        self._determine_classifications(analysis)
        
        # Create rooms/zones if none extracted
        if not analysis.rooms:
            self._create_default_zones(analysis)
        
        # Determine code requirements
        self._determine_code_requirements(analysis)
        
        # Calculate confidence
        analysis.analysis_confidence = self._calculate_confidence(analysis, pdf_data, dxf_data)
        
        logger.info(f"Building analysis complete: {analysis.total_area_sqft:.0f} sqft, "
                   f"{len(analysis.rooms)} zones, {analysis.analysis_confidence:.0f}% confidence")
        
        return analysis
    
    def _process_pdf_data(self, analysis: BuildingAnalysis, pdf_data: List[Dict]):
        """Process data extracted from PDFs"""
        for data in pdf_data:
            # Collect all text
            analysis.extracted_text.extend(data.get('text', []))
            
            # Process room names
            for room_name in data.get('room_names', []):
                room = self._create_room_from_name(room_name, len(analysis.rooms))
                if room:
                    analysis.rooms.append(room)
            
            # Set scale if found
            if data.get('scale') and not analysis.drawing_scale:
                analysis.drawing_scale = data['scale']
    
    def _process_dxf_data(self, analysis: BuildingAnalysis, dxf_data: List[Dict]):
        """Process data extracted from DXF files"""
        for data in dxf_data:
            # Get building bounds
            bounds = data.get('bounds')
            if bounds:
                # Assume dimensions are in feet (or convert from inches/units)
                width = bounds['width']
                height = bounds['height']
                
                # Heuristic: if values seem too large, assume inches
                if width > 10000 or height > 10000:
                    width /= 12
                    height /= 12
                
                analysis.building_length_ft = max(analysis.building_length_ft, width)
                analysis.building_width_ft = max(analysis.building_width_ft, height)
                
                if analysis.total_area_sqft == 0:
                    analysis.total_area_sqft = width * height
            
            # Extract text for room names
            for text_entity in data.get('text', []):
                text = text_entity.get('text', '')
                analysis.extracted_text.append(text)
                
                # Try to identify rooms
                room = self._create_room_from_name(text, len(analysis.rooms))
                if room:
                    # Set position from DXF
                    pos = text_entity.get('position', (0, 0))
                    room.centroid = Point(pos[0], pos[1])
                    analysis.rooms.append(room)
            
            # Process closed polylines as potential rooms
            for polyline in data.get('polylines', []):
                if polyline.get('closed') and len(polyline.get('points', [])) >= 3:
                    points = [Point(p[0], p[1]) for p in polyline['points']]
                    room = Room(
                        id=f"ZONE-{len(analysis.rooms)+1:03d}",
                        name=f"Zone {len(analysis.rooms)+1}",
                        vertices=points
                    )
                    if room.area_sqft > 50:  # Filter out tiny shapes
                        analysis.rooms.append(room)
    
    def _create_room_from_name(self, name: str, index: int) -> Optional[Room]:
        """Create a room object from a room name"""
        name_lower = name.lower().strip()
        
        # Skip if too short or doesn't look like a room name
        if len(name_lower) < 3:
            return None
        
        # Determine occupancy from keywords
        occupancy = None
        for keyword, occ_type in ROOM_KEYWORDS.items():
            if keyword in name_lower:
                occupancy = occ_type
                break
        
        if occupancy is None:
            return None
        
        # Determine hazard class
        hazard = OCCUPANCY_TO_HAZARD.get(occupancy, HazardClass.ORDINARY_1)
        
        return Room(
            id=f"ROOM-{index+1:03d}",
            name=name.strip(),
            vertices=[],  # Will be set later if geometry available
            occupancy=occupancy,
            hazard_class=hazard
        )
    
    def _create_default_zones(self, analysis: BuildingAnalysis):
        """Create default zones when no rooms are extracted"""
        if analysis.total_area_sqft > 0:
            # Create a single zone covering the whole building
            side = math.sqrt(analysis.total_area_sqft)
            vertices = [
                Point(0, 0),
                Point(side, 0),
                Point(side, side),
                Point(0, side)
            ]
            
            room = Room(
                id="ZONE-001",
                name="Main Area",
                vertices=vertices,
                ceiling_height_ft=analysis.typical_ceiling_height_ft,
                occupancy=analysis.primary_occupancy or OccupancyType.BUSINESS_B,
                hazard_class=analysis.primary_hazard_class or HazardClass.ORDINARY_1
            )
            analysis.rooms.append(room)
    
    def _determine_classifications(self, analysis: BuildingAnalysis):
        """Determine building classifications from extracted data"""
        
        # Determine primary occupancy from rooms
        if analysis.rooms:
            occupancy_counts = {}
            for room in analysis.rooms:
                if room.occupancy:
                    occupancy_counts[room.occupancy] = occupancy_counts.get(room.occupancy, 0) + 1
            
            if occupancy_counts:
                analysis.primary_occupancy = max(occupancy_counts, key=occupancy_counts.get)
        
        # Default occupancy if not determined
        if not analysis.primary_occupancy:
            analysis.primary_occupancy = OccupancyType.BUSINESS_B
        
        # Determine hazard class from occupancy
        if not analysis.primary_hazard_class:
            analysis.primary_hazard_class = OCCUPANCY_TO_HAZARD.get(
                analysis.primary_occupancy, HazardClass.ORDINARY_1
            )
        
        # Assign hazard class to rooms that don't have one
        for room in analysis.rooms:
            if not room.hazard_class:
                if room.occupancy:
                    room.hazard_class = OCCUPANCY_TO_HAZARD.get(room.occupancy, analysis.primary_hazard_class)
                else:
                    room.hazard_class = analysis.primary_hazard_class
        
        # Determine construction type (default to Type II-B if not found)
        if not analysis.construction_type:
            analysis.construction_type = ConstructionType.TYPE_IIB
        
        # Calculate building height
        analysis.building_height_ft = analysis.typical_ceiling_height_ft * analysis.num_stories
    
    def _determine_code_requirements(self, analysis: BuildingAnalysis):
        """Determine code requirements based on building characteristics"""
        
        # Sprinklers required per IBC 903.2
        # (simplified - actual requirements are more complex)
        analysis.sprinkler_required = True  # Conservative default
        
        # Standpipe required per IBC 905.3
        # Required if building height > 30' or area > 10,000 sqft per floor
        if analysis.building_height_ft > 30 or analysis.total_area_sqft > 10000:
            analysis.standpipe_required = True
        
        # Fire pump potentially required if building height > 75'
        if analysis.building_height_ft > 75:
            analysis.fire_pump_required = True
            analysis.warnings.append("Building height may require fire pump - verify water supply")
        
        # Fire alarm required for most occupancies
        analysis.fire_alarm_required = True
    
    def _calculate_confidence(self, analysis: BuildingAnalysis, pdf_data: List, dxf_data: List) -> float:
        """Calculate confidence score for analysis"""
        confidence = 50.0  # Base confidence
        
        # Add confidence for extracted data
        if pdf_data:
            confidence += 10
        if dxf_data:
            confidence += 15
        if analysis.rooms:
            confidence += 10
        if analysis.total_area_sqft > 0:
            confidence += 5
        if analysis.zip_code:
            confidence += 5
        if analysis.drawing_scale:
            confidence += 5
        
        return min(100.0, confidence)
    
    def to_project_data(self, analysis: BuildingAnalysis) -> Dict[str, Any]:
        """Convert BuildingAnalysis to project_data dict for orchestrator"""
        return {
            'project_id': analysis.project_id,
            'project_name': analysis.project_name,
            'building_area_sqft': analysis.total_area_sqft,
            'ceiling_height_ft': analysis.typical_ceiling_height_ft,
            'num_stories': analysis.num_stories,
            'building_height_ft': analysis.building_height_ft,
            'hazard_class': analysis.primary_hazard_class.value if analysis.primary_hazard_class else 'ordinary_hazard_group_1',
            'occupancy_type': analysis.primary_occupancy.value if analysis.primary_occupancy else 'B',
            'construction_type': analysis.construction_type.value if analysis.construction_type else 'II-B',
            'zip_code': analysis.zip_code,
            'sprinkler_required': analysis.sprinkler_required,
            'standpipe_required': analysis.standpipe_required,
            'fire_pump_required': analysis.fire_pump_required,
            'zones': [
                {
                    'zone_id': room.id,
                    'zone_name': room.name,
                    'area_sqft': room.area_sqft,
                    'ceiling_height_ft': room.ceiling_height_ft,
                    'hazard_class': room.hazard_class.value if room.hazard_class else 'ordinary_hazard_group_1',
                    'occupancy': room.occupancy.value if room.occupancy else 'B'
                }
                for room in analysis.rooms
            ],
            'analysis_confidence': analysis.analysis_confidence,
            'warnings': analysis.warnings
        }


# =============================================================================
# MAIN FUNCTION
# =============================================================================

def analyze_documents(project_dir: str, project_data: Dict = None) -> Dict[str, Any]:
    """
    Main entry point for document analysis.
    Returns project data dict ready for orchestrator.
    """
    logger.info("=" * 60)
    logger.info("🔍 FireAI Pro Document Analysis Engine v1.0")
    logger.info("=" * 60)
    
    analyzer = BuildingAnalyzer()
    analysis = analyzer.analyze(project_dir, project_data)
    
    result = analyzer.to_project_data(analysis)
    
    logger.info(f"Analysis complete:")
    logger.info(f"  Building: {result['building_area_sqft']:.0f} sqft")
    logger.info(f"  Occupancy: {result['occupancy_type']}")
    logger.info(f"  Hazard: {result['hazard_class']}")
    logger.info(f"  Zones: {len(result['zones'])}")
    logger.info(f"  Confidence: {result['analysis_confidence']:.0f}%")
    
    return result


if __name__ == "__main__":
    print("🔍 FireAI Pro Document Analysis Engine v1.0")
    print("=" * 50)
    print("\nCapabilities:")
    print("  - PDF architectural drawing parsing")
    print("  - DXF/DWG file geometry extraction")
    print("  - Room/zone identification")
    print("  - Occupancy type detection")
    print("  - Hazard classification")
    print("  - Code requirement determination")
    print("\nReady for document analysis!")
