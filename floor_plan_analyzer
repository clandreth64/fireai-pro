#!/usr/bin/env python3
"""
FireAI Pro - AI Floor Plan Analyzer
====================================
Uses AI vision to analyze uploaded construction documents and extract
building data for automated fire sprinkler system design.

CAPABILITIES:
- Analyzes PDF floor plans (converts to images)
- Analyzes uploaded images (PNG, JPG)
- Parses DXF/DWG files directly
- Extracts: rooms, dimensions, occupancies, obstructions
- Returns structured data for sprinkler design

VERSION: 2.0.0
"""

import os
import json
import base64
import re
import math
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
import traceback

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.FloorPlanAnalyzer")


# =============================================================================
# CONFIGURATION
# =============================================================================

# Anthropic API for vision analysis
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# OpenAI API as fallback
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class OccupancyType(Enum):
    """IBC Occupancy Classifications"""
    ASSEMBLY = "A"
    BUSINESS = "B"
    EDUCATIONAL = "E"
    FACTORY = "F"
    HIGH_HAZARD = "H"
    INSTITUTIONAL = "I"
    MERCANTILE = "M"
    RESIDENTIAL = "R"
    STORAGE = "S"
    UTILITY = "U"


class HazardClass(Enum):
    """NFPA 13 Hazard Classifications"""
    LIGHT = "light_hazard"
    OH1 = "ordinary_hazard_group_1"
    OH2 = "ordinary_hazard_group_2"
    EH1 = "extra_hazard_group_1"
    EH2 = "extra_hazard_group_2"


# Room type to hazard class mapping
ROOM_HAZARD_MAP = {
    # Light Hazard
    'office': HazardClass.LIGHT,
    'conference': HazardClass.LIGHT,
    'lobby': HazardClass.LIGHT,
    'reception': HazardClass.LIGHT,
    'restroom': HazardClass.LIGHT,
    'bathroom': HazardClass.LIGHT,
    'corridor': HazardClass.LIGHT,
    'hallway': HazardClass.LIGHT,
    'classroom': HazardClass.LIGHT,
    'hotel room': HazardClass.LIGHT,
    'apartment': HazardClass.LIGHT,
    'residential': HazardClass.LIGHT,
    'church': HazardClass.LIGHT,
    'library': HazardClass.LIGHT,
    
    # Ordinary Hazard Group 1
    'kitchen': HazardClass.OH1,
    'dining': HazardClass.OH1,
    'restaurant': HazardClass.OH1,
    'cafeteria': HazardClass.OH1,
    'retail': HazardClass.OH1,
    'store': HazardClass.OH1,
    'parking': HazardClass.OH1,
    'garage': HazardClass.OH1,
    'laundry': HazardClass.OH1,
    'mechanical': HazardClass.OH1,
    'electrical': HazardClass.OH1,
    'server': HazardClass.OH1,
    'data center': HazardClass.OH1,
    
    # Ordinary Hazard Group 2
    'warehouse': HazardClass.OH2,
    'storage': HazardClass.OH2,
    'manufacturing': HazardClass.OH2,
    'workshop': HazardClass.OH2,
    'loading dock': HazardClass.OH2,
    'machine shop': HazardClass.OH2,
    
    # Extra Hazard
    'paint shop': HazardClass.EH1,
    'chemical storage': HazardClass.EH1,
    'flammable': HazardClass.EH2,
}


@dataclass
class Room:
    """Extracted room data"""
    id: str
    name: str
    room_type: str
    area_sqft: float
    width_ft: float = 0
    length_ft: float = 0
    ceiling_height_ft: float = 10
    hazard_class: str = "ordinary_hazard_group_1"
    occupancy: str = "B"
    x_position: float = 0
    y_position: float = 0
    notes: str = ""


@dataclass
class Obstruction:
    """Extracted obstruction data"""
    id: str
    type: str  # column, beam, duct, equipment
    x: float
    y: float
    width: float
    depth: float
    height: float = 0
    notes: str = ""


@dataclass
class BuildingData:
    """Complete extracted building data"""
    project_name: str = "Extracted Project"
    total_area_sqft: float = 0
    num_floors: int = 1
    building_height_ft: float = 12
    typical_ceiling_height_ft: float = 10
    
    # Location
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    
    # Construction
    construction_type: str = "Type II-B"
    year_built: int = 0
    
    # Rooms
    rooms: List[Room] = field(default_factory=list)
    obstructions: List[Obstruction] = field(default_factory=list)
    
    # Detected info
    scale: str = ""
    drawing_type: str = ""  # floor_plan, reflected_ceiling, site_plan
    
    # Analysis metadata
    confidence: float = 0
    warnings: List[str] = field(default_factory=list)
    raw_extraction: str = ""


# =============================================================================
# IMAGE PROCESSING
# =============================================================================

def pdf_to_images(pdf_path: str, max_pages: int = 5) -> List[str]:
    """Convert PDF pages to base64 images"""
    images = []
    
    try:
        # Try pdf2image first
        from pdf2image import convert_from_path
        pages = convert_from_path(pdf_path, dpi=150, first_page=1, last_page=max_pages)
        
        for i, page in enumerate(pages):
            import io
            buffer = io.BytesIO()
            page.save(buffer, format='PNG')
            b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            images.append(b64)
            logger.info(f"  Converted PDF page {i+1} to image")
        
        return images
        
    except ImportError:
        logger.warning("pdf2image not available, trying alternative")
    
    try:
        # Try PyMuPDF (fitz)
        import fitz
        doc = fitz.open(pdf_path)
        
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            b64 = base64.b64encode(pix.tobytes("png")).decode('utf-8')
            images.append(b64)
            logger.info(f"  Converted PDF page {i+1} to image")
        
        return images
        
    except ImportError:
        logger.warning("PyMuPDF not available")
    
    try:
        # Try pdfplumber with PIL
        import pdfplumber
        from PIL import Image
        import io
        
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages[:max_pages]):
                img = page.to_image(resolution=150)
                buffer = io.BytesIO()
                img.original.save(buffer, format='PNG')
                b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                images.append(b64)
                logger.info(f"  Converted PDF page {i+1} to image")
        
        return images
        
    except Exception as e:
        logger.error(f"PDF conversion failed: {e}")
    
    return images


def load_image_as_base64(image_path: str) -> Optional[str]:
    """Load image file as base64"""
    try:
        with open(image_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to load image: {e}")
        return None


def get_image_media_type(file_path: str) -> str:
    """Get media type for image"""
    ext = Path(file_path).suffix.lower()
    media_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.pdf': 'application/pdf'
    }
    return media_types.get(ext, 'image/png')


# =============================================================================
# AI VISION ANALYSIS
# =============================================================================

FLOOR_PLAN_ANALYSIS_PROMPT = """Analyze this architectural floor plan drawing and extract building information for fire sprinkler system design.

EXTRACT THE FOLLOWING:

1. **BUILDING OVERVIEW**
   - Total building area (estimate in square feet)
   - Number of floors shown
   - Typical ceiling height (if noted, otherwise estimate based on building type)
   - Drawing scale (if shown)
   - Building type/use

2. **ROOMS/SPACES** - For EACH room or space visible:
   - Room name/label (exactly as shown)
   - Room type (office, warehouse, retail, restroom, corridor, etc.)
   - Estimated area in square feet
   - Estimated dimensions (width x length)
   - Ceiling height if different from typical
   - Any special conditions (high piled storage, cooking equipment, etc.)

3. **OBSTRUCTIONS** - Identify any:
   - Columns (note approximate locations)
   - Large beams
   - Mechanical equipment
   - Ductwork that may affect sprinkler placement

4. **IMPORTANT NOTES**
   - Any fire-related notes on the drawing
   - Construction type if indicated
   - Occupancy classification if noted
   - Any areas that appear to be high hazard

RESPOND IN THIS EXACT JSON FORMAT:
```json
{
  "building": {
    "name": "Building name if shown",
    "total_area_sqft": 0,
    "num_floors": 1,
    "ceiling_height_ft": 10,
    "scale": "1/8\" = 1'-0\"",
    "building_type": "office/warehouse/retail/etc",
    "construction_type": "if noted"
  },
  "rooms": [
    {
      "name": "Room Name",
      "type": "office",
      "area_sqft": 500,
      "width_ft": 20,
      "length_ft": 25,
      "ceiling_height_ft": 10,
      "notes": "any special conditions"
    }
  ],
  "obstructions": [
    {
      "type": "column",
      "location": "grid line A-1",
      "size": "12x12 inches"
    }
  ],
  "warnings": [
    "Any concerns or uncertainties"
  ],
  "confidence": 85
}
```

Be thorough - identify ALL rooms visible on the plan. If dimensions aren't shown, estimate based on typical sizes and any reference dimensions you can find. For confidence, use 0-100 where 100 means all data was clearly visible and extracted."""


def analyze_with_anthropic(images: List[str], media_type: str = "image/png") -> Optional[Dict]:
    """Analyze floor plan images using Anthropic Claude API"""
    
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set")
        return None
    
    try:
        import requests
        
        # Build content with images
        content = []
        for i, img_b64 in enumerate(images[:5]):  # Max 5 images
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": img_b64
                }
            })
        
        content.append({
            "type": "text",
            "text": FLOOR_PLAN_ANALYSIS_PROMPT
        })
        
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4096,
                "messages": [
                    {"role": "user", "content": content}
                ]
            },
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            text = result.get('content', [{}])[0].get('text', '')
            
            # Extract JSON from response
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            
            # Try parsing entire response as JSON
            try:
                return json.loads(text)
            except:
                logger.warning("Could not parse JSON from response")
                return {"raw_text": text, "confidence": 50}
        else:
            logger.error(f"Anthropic API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Anthropic analysis failed: {e}")
        traceback.print_exc()
        return None


def analyze_with_openai(images: List[str], media_type: str = "image/png") -> Optional[Dict]:
    """Analyze floor plan images using OpenAI GPT-4 Vision API"""
    
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set")
        return None
    
    try:
        import requests
        
        # Build content with images
        content = []
        for img_b64 in images[:5]:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{img_b64}",
                    "detail": "high"
                }
            })
        
        content.append({
            "type": "text",
            "text": FLOOR_PLAN_ANALYSIS_PROMPT
        })
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}"
            },
            json={
                "model": "gpt-4o",
                "max_tokens": 4096,
                "messages": [
                    {"role": "user", "content": content}
                ]
            },
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            
            # Extract JSON
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            
            try:
                return json.loads(text)
            except:
                return {"raw_text": text, "confidence": 50}
        else:
            logger.error(f"OpenAI API error: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"OpenAI analysis failed: {e}")
        return None


# =============================================================================
# DXF/DWG PARSING
# =============================================================================

def parse_dxf(dxf_path: str) -> Optional[Dict]:
    """Parse DXF file and extract building data"""
    
    try:
        import ezdxf
    except ImportError:
        logger.warning("ezdxf not available for DXF parsing")
        return None
    
    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        
        rooms = []
        obstructions = []
        texts = []
        polylines = []
        
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        
        for entity in msp:
            etype = entity.dxftype()
            
            # Collect text for room labels
            if etype in ('TEXT', 'MTEXT'):
                text = entity.dxf.text if etype == 'TEXT' else entity.text
                pos = (entity.dxf.insert.x, entity.dxf.insert.y)
                texts.append({'text': text, 'x': pos[0], 'y': pos[1]})
            
            # Collect closed polylines as potential rooms
            elif etype == 'LWPOLYLINE':
                if entity.closed:
                    points = [(p[0], p[1]) for p in entity.get_points()]
                    if len(points) >= 3:
                        # Calculate area
                        area = 0
                        n = len(points)
                        for i in range(n):
                            j = (i + 1) % n
                            area += points[i][0] * points[j][1]
                            area -= points[j][0] * points[i][1]
                        area = abs(area) / 2
                        
                        # Calculate bounds
                        xs = [p[0] for p in points]
                        ys = [p[1] for p in points]
                        
                        polylines.append({
                            'points': points,
                            'area': area,
                            'min_x': min(xs),
                            'max_x': max(xs),
                            'min_y': min(ys),
                            'max_y': max(ys),
                            'width': max(xs) - min(xs),
                            'height': max(ys) - min(ys)
                        })
                        
                        min_x = min(min_x, min(xs))
                        min_y = min(min_y, min(ys))
                        max_x = max(max_x, max(xs))
                        max_y = max(max_y, max(ys))
            
            # Circles might be columns
            elif etype == 'CIRCLE':
                center = (entity.dxf.center.x, entity.dxf.center.y)
                radius = entity.dxf.radius
                if 0.25 <= radius <= 2:  # Likely a column
                    obstructions.append({
                        'type': 'column',
                        'x': center[0],
                        'y': center[1],
                        'diameter': radius * 2
                    })
        
        # Match text labels to polylines (rooms)
        for poly in polylines:
            # Find text inside this polyline
            cx = (poly['min_x'] + poly['max_x']) / 2
            cy = (poly['min_y'] + poly['max_y']) / 2
            
            for text in texts:
                if (poly['min_x'] <= text['x'] <= poly['max_x'] and
                    poly['min_y'] <= text['y'] <= poly['max_y']):
                    poly['label'] = text['text']
                    break
            
            if poly.get('area', 0) > 50:  # Filter tiny shapes
                room_name = poly.get('label', f'Room {len(rooms)+1}')
                room_type = identify_room_type(room_name)
                hazard = get_hazard_for_room(room_type)
                
                rooms.append({
                    'name': room_name,
                    'type': room_type,
                    'area_sqft': poly['area'],
                    'width_ft': poly['width'],
                    'length_ft': poly['height'],
                    'hazard_class': hazard.value
                })
        
        # Calculate total area
        total_width = max_x - min_x if max_x > min_x else 0
        total_height = max_y - min_y if max_y > min_y else 0
        total_area = total_width * total_height
        
        return {
            'building': {
                'total_area_sqft': total_area,
                'width_ft': total_width,
                'length_ft': total_height
            },
            'rooms': rooms,
            'obstructions': obstructions,
            'confidence': 70
        }
        
    except Exception as e:
        logger.error(f"DXF parsing failed: {e}")
        traceback.print_exc()
        return None


def identify_room_type(name: str) -> str:
    """Identify room type from name"""
    name_lower = name.lower()
    
    for keyword in ROOM_HAZARD_MAP.keys():
        if keyword in name_lower:
            return keyword
    
    # Default mappings
    if any(x in name_lower for x in ['wh', 'whse', 'stor']):
        return 'warehouse'
    if any(x in name_lower for x in ['off', 'admin']):
        return 'office'
    if any(x in name_lower for x in ['bath', 'wc', 'toilet']):
        return 'restroom'
    if any(x in name_lower for x in ['corr', 'hall']):
        return 'corridor'
    
    return 'office'  # Default


def get_hazard_for_room(room_type: str) -> HazardClass:
    """Get hazard class for room type"""
    return ROOM_HAZARD_MAP.get(room_type.lower(), HazardClass.OH1)


# =============================================================================
# MAIN ANALYZER CLASS
# =============================================================================

class FloorPlanAnalyzer:
    """Main analyzer class that coordinates all extraction methods"""
    
    def __init__(self):
        self.has_anthropic = bool(ANTHROPIC_API_KEY)
        self.has_openai = bool(OPENAI_API_KEY)
        
        logger.info(f"FloorPlanAnalyzer initialized")
        logger.info(f"  Anthropic API: {'✅' if self.has_anthropic else '❌'}")
        logger.info(f"  OpenAI API: {'✅' if self.has_openai else '❌'}")
    
    def analyze(self, file_path: str, project_data: Dict = None) -> BuildingData:
        """
        Analyze a floor plan file and extract building data.
        
        Args:
            file_path: Path to PDF, DXF, or image file
            project_data: Optional manual overrides
        
        Returns:
            BuildingData object with extracted information
        """
        project_data = project_data or {}
        file_path = Path(file_path)
        
        logger.info(f"Analyzing: {file_path.name}")
        
        result = BuildingData(
            project_name=project_data.get('project_name', file_path.stem)
        )
        
        ext = file_path.suffix.lower()
        extracted = None
        
        # Route to appropriate parser
        if ext in ['.dxf', '.dwg']:
            logger.info("  Using DXF parser")
            extracted = parse_dxf(str(file_path))
            
        elif ext == '.pdf':
            logger.info("  Converting PDF to images")
            images = pdf_to_images(str(file_path))
            
            if images:
                logger.info(f"  Analyzing {len(images)} page(s) with AI vision")
                extracted = self._analyze_with_ai(images, 'image/png')
            else:
                result.warnings.append("Could not convert PDF to images")
                
        elif ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
            logger.info("  Loading image")
            img_b64 = load_image_as_base64(str(file_path))
            
            if img_b64:
                media_type = get_image_media_type(str(file_path))
                logger.info("  Analyzing image with AI vision")
                extracted = self._analyze_with_ai([img_b64], media_type)
            else:
                result.warnings.append("Could not load image")
        
        else:
            result.warnings.append(f"Unsupported file type: {ext}")
        
        # Process extracted data
        if extracted:
            result = self._process_extraction(extracted, result, project_data)
        else:
            result.warnings.append("No data could be extracted")
            result.confidence = 0
        
        # Apply manual overrides
        result = self._apply_overrides(result, project_data)
        
        logger.info(f"Analysis complete: {result.total_area_sqft:.0f} sqft, {len(result.rooms)} rooms, {result.confidence:.0f}% confidence")
        
        return result
    
    def _analyze_with_ai(self, images: List[str], media_type: str) -> Optional[Dict]:
        """Try AI vision analysis with available APIs"""
        
        # Try Anthropic first
        if self.has_anthropic:
            logger.info("  Trying Anthropic Claude...")
            result = analyze_with_anthropic(images, media_type)
            if result:
                return result
        
        # Fall back to OpenAI
        if self.has_openai:
            logger.info("  Trying OpenAI GPT-4o...")
            result = analyze_with_openai(images, media_type)
            if result:
                return result
        
        logger.warning("  No AI API available for vision analysis")
        return None
    
    def _process_extraction(self, extracted: Dict, result: BuildingData, project_data: Dict) -> BuildingData:
        """Process extracted data into BuildingData"""
        
        # Store raw extraction
        result.raw_extraction = json.dumps(extracted, indent=2)
        
        # Building info
        building = extracted.get('building', {})
        result.total_area_sqft = building.get('total_area_sqft', 0)
        result.num_floors = building.get('num_floors', 1)
        result.typical_ceiling_height_ft = building.get('ceiling_height_ft', 10)
        result.scale = building.get('scale', '')
        result.construction_type = building.get('construction_type', 'Type II-B')
        
        # Rooms
        for i, room_data in enumerate(extracted.get('rooms', [])):
            room_type = room_data.get('type', 'office').lower()
            hazard = get_hazard_for_room(room_type)
            
            room = Room(
                id=f"ROOM-{i+1:03d}",
                name=room_data.get('name', f'Room {i+1}'),
                room_type=room_type,
                area_sqft=room_data.get('area_sqft', 0),
                width_ft=room_data.get('width_ft', 0),
                length_ft=room_data.get('length_ft', 0),
                ceiling_height_ft=room_data.get('ceiling_height_ft', result.typical_ceiling_height_ft),
                hazard_class=hazard.value,
                notes=room_data.get('notes', '')
            )
            result.rooms.append(room)
        
        # Calculate total area from rooms if not provided
        if result.total_area_sqft == 0 and result.rooms:
            result.total_area_sqft = sum(r.area_sqft for r in result.rooms)
        
        # Obstructions
        for i, obs_data in enumerate(extracted.get('obstructions', [])):
            obs = Obstruction(
                id=f"OBS-{i+1:03d}",
                type=obs_data.get('type', 'column'),
                x=obs_data.get('x', 0),
                y=obs_data.get('y', 0),
                width=obs_data.get('width', 1),
                depth=obs_data.get('depth', 1),
                notes=obs_data.get('location', '')
            )
            result.obstructions.append(obs)
        
        # Warnings and confidence
        result.warnings.extend(extracted.get('warnings', []))
        result.confidence = extracted.get('confidence', 50)
        
        return result
    
    def _apply_overrides(self, result: BuildingData, project_data: Dict) -> BuildingData:
        """Apply manual overrides from project_data"""
        
        if project_data.get('building_area_sqft'):
            result.total_area_sqft = project_data['building_area_sqft']
        if project_data.get('ceiling_height_ft'):
            result.typical_ceiling_height_ft = project_data['ceiling_height_ft']
        if project_data.get('num_stories'):
            result.num_floors = project_data['num_stories']
        if project_data.get('zip_code'):
            result.zip_code = project_data['zip_code']
        if project_data.get('address'):
            result.address = project_data['address']
        
        return result
    
    def to_project_data(self, building: BuildingData) -> Dict[str, Any]:
        """Convert BuildingData to project_data dict for orchestrator"""
        
        # Determine primary hazard (most common or highest)
        hazard_counts = {}
        for room in building.rooms:
            hazard_counts[room.hazard_class] = hazard_counts.get(room.hazard_class, 0) + room.area_sqft
        
        primary_hazard = max(hazard_counts, key=hazard_counts.get) if hazard_counts else 'ordinary_hazard_group_1'
        
        return {
            'project_name': building.project_name,
            'building_area_sqft': building.total_area_sqft,
            'ceiling_height_ft': building.typical_ceiling_height_ft,
            'num_stories': building.num_floors,
            'building_height_ft': building.typical_ceiling_height_ft * building.num_floors,
            'hazard_class': primary_hazard,
            'construction_type': building.construction_type,
            'zip_code': building.zip_code,
            'address': building.address,
            'zones': [
                {
                    'zone_id': room.id,
                    'zone_name': room.name,
                    'area_sqft': room.area_sqft,
                    'ceiling_height_ft': room.ceiling_height_ft,
                    'hazard_class': room.hazard_class
                }
                for room in building.rooms
            ],
            'obstructions': [
                {
                    'id': obs.id,
                    'type': obs.type,
                    'x': obs.x,
                    'y': obs.y,
                    'width': obs.width,
                    'depth': obs.depth
                }
                for obs in building.obstructions
            ],
            'analysis_confidence': building.confidence,
            'warnings': building.warnings
        }


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def analyze_floor_plan(file_path: str, project_data: Dict = None) -> Dict[str, Any]:
    """
    Main entry point - analyze a floor plan and return project data.
    
    Args:
        file_path: Path to PDF, DXF, or image file
        project_data: Optional manual overrides
    
    Returns:
        Dict ready for orchestrator
    """
    analyzer = FloorPlanAnalyzer()
    building = analyzer.analyze(file_path, project_data)
    return analyzer.to_project_data(building)


def analyze_project_documents(project_dir: str, project_data: Dict = None) -> Dict[str, Any]:
    """
    Analyze all documents in a project directory.
    
    Args:
        project_dir: Directory containing uploaded documents
        project_data: Optional manual overrides
    
    Returns:
        Dict ready for orchestrator
    """
    project_data = project_data or {}
    project_path = Path(project_dir)
    
    # Find analyzable files
    supported_ext = ['.pdf', '.dxf', '.dwg', '.png', '.jpg', '.jpeg']
    files = [f for f in project_path.iterdir() 
             if f.is_file() and f.suffix.lower() in supported_ext]
    
    if not files:
        logger.warning("No analyzable documents found")
        return project_data
    
    analyzer = FloorPlanAnalyzer()
    
    # Analyze first suitable file (prioritize PDF, then DXF, then images)
    files.sort(key=lambda f: (
        0 if f.suffix.lower() == '.pdf' else
        1 if f.suffix.lower() in ['.dxf', '.dwg'] else
        2
    ))
    
    primary_file = files[0]
    logger.info(f"Primary document: {primary_file.name}")
    
    building = analyzer.analyze(str(primary_file), project_data)
    return analyzer.to_project_data(building)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🔍 FireAI Pro Floor Plan Analyzer v2.0")
    print("=" * 50)
    
    analyzer = FloorPlanAnalyzer()
    print(f"\nAI Vision: {'✅ Available' if (analyzer.has_anthropic or analyzer.has_openai) else '❌ No API keys set'}")
    print(f"  - Anthropic: {'✅' if analyzer.has_anthropic else '❌'}")
    print(f"  - OpenAI: {'✅' if analyzer.has_openai else '❌'}")
    
    print("\nSupported file types:")
    print("  - PDF (architectural drawings)")
    print("  - DXF/DWG (CAD files)")
    print("  - PNG/JPG (floor plan images)")
    
    print("\nUsage:")
    print("  result = analyze_floor_plan('floor_plan.pdf')")
    print("  # Returns project_data dict for orchestrator")
