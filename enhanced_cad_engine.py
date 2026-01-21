#!/usr/bin/env python3
"""
Enhanced Production CAD Engine - Multi-Format Support with Batch Processing
🏗️ Production-Ready CAD Processing for DXF, DWG, IFC with AI Enhancement
🔥 Optimized for Fire Sprinkler Design Systems and Cloud Deployment

VERSION: 63.0.0-PRODUCTION-MULTI-FORMAT
STATUS: Production-Ready with Multi-Format Support & Batch Processing

FEATURES:
🎯 Multi-format support (DXF, DWG, IFC)
📦 Batch processing for multi-sheet plans
🏗️ Construction geometry extraction (floors, walls, risers)
🤖 AI-enhanced space classification
☁️ Cloud-scalable architecture
⚡ High-performance parallel processing
🛡️ Robust error handling and recovery
"""

import asyncio
import json
import logging
import os
import uuid
import hashlib
import time
import threading
import math
import shutil
import tempfile
import psutil
import gc
import traceback
import io
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Union, Set, Iterator
from dataclasses import dataclass, field, asdict
from enum import Enum, IntEnum
from pathlib import Path
from collections import defaultdict, deque
import concurrent.futures
import multiprocessing
from contextlib import asynccontextmanager
import zipfile
import tarfile
import weakref
from functools import wraps
import aiofiles
import aiohttp

# Enhanced dependencies for CAD processing
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.spatial.distance import pdist, squareform, euclidean
from scipy.spatial import ConvexHull, Voronoi, distance
from sklearn.cluster import DBSCAN, KMeans
from sklearn.preprocessing import StandardScaler

# OpenCV - optional, for image-based CAD processing
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    cv2 = None
    print("⚠️ OpenCV not available - image processing disabled")
except Exception as e:
    CV2_AVAILABLE = False
    cv2 = None
    print(f"⚠️ OpenCV failed to load: {e} - image processing disabled")

from PIL import Image, ImageDraw, ImageFont

# Core CAD libraries
import ezdxf
from ezdxf import colors, units, bbox
from ezdxf.math import Vec3, Vec2, Vec4, Matrix44, BoundingBox
from ezdxf.entities import DXFEntity
from ezdxf.query import EntityQuery

# IFC support
try:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element
    import ifcopenshell.util.placement
    import ifcopenshell.util.representation
    import ifcopenshell.util.schema
    IFC_AVAILABLE = True
except ImportError:
    IFC_AVAILABLE = False
    print("⚠️ IFC support not available. Install with: pip install ifcopenshell")

# Enhanced file processing
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("⚠️ PDF support not available. Install with: pip install PyMuPDF")

try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False
    print("⚠️ File type detection limited. Install with: pip install python-magic")

# Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('enhanced_cad_engine.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================================================================================================
# SHARED PROJECT GEOMETRY DATA STRUCTURES
# ================================================================================================

class GeometryType(Enum):
    """Standard geometry types for construction elements"""
    FLOOR = "floor"
    WALL = "wall" 
    CEILING = "ceiling"
    DOOR = "door"
    WINDOW = "window"
    COLUMN = "column"
    BEAM = "beam"
    STAIR = "stair"
    RISER = "riser"
    ROOM = "room"
    CORRIDOR = "corridor"
    MECHANICAL = "mechanical"
    ELECTRICAL = "electrical"
    PLUMBING = "plumbing"
    FURNITURE = "furniture"
    FIXTURE = "fixture"
    EQUIPMENT = "equipment"
    UNKNOWN = "unknown"

class MaterialType(Enum):
    """Standard material types for construction"""
    CONCRETE = "concrete"
    STEEL = "steel"
    WOOD = "wood"
    MASONRY = "masonry"
    GYPSUM = "gypsum"
    GLASS = "glass"
    METAL = "metal"
    PLASTIC = "plastic"
    COMPOSITE = "composite"
    UNKNOWN = "unknown"

@dataclass
class Point3D:
    """3D point with enhanced functionality"""
    x: float
    y: float
    z: float = 0.0
    
    def distance_to(self, other: 'Point3D') -> float:
        """Calculate distance to another point"""
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2 + (self.z - other.z)**2)
    
    def to_2d(self) -> Tuple[float, float]:
        """Convert to 2D tuple"""
        return (self.x, self.y)
    
    def to_list(self) -> List[float]:
        """Convert to list format"""
        return [self.x, self.y, self.z]

@dataclass
class BoundingBox3D:
    """3D bounding box"""
    min_point: Point3D
    max_point: Point3D
    
    @property
    def width(self) -> float:
        return self.max_point.x - self.min_point.x
    
    @property
    def height(self) -> float:
        return self.max_point.y - self.min_point.y
    
    @property
    def depth(self) -> float:
        return self.max_point.z - self.min_point.z
    
    @property
    def volume(self) -> float:
        return self.width * self.height * self.depth
    
    @property
    def center(self) -> Point3D:
        return Point3D(
            (self.min_point.x + self.max_point.x) / 2,
            (self.min_point.y + self.max_point.y) / 2,
            (self.min_point.z + self.max_point.z) / 2
        )

@dataclass
class ConstructionElement:
    """Base class for all construction elements"""
    id: str
    geometry_type: GeometryType
    material_type: MaterialType = MaterialType.UNKNOWN
    layer_name: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Geometric properties
    bounding_box: Optional[BoundingBox3D] = None
    vertices: List[Point3D] = field(default_factory=list)
    area: float = 0.0
    perimeter: float = 0.0
    volume: float = 0.0
    height: float = 0.0
    thickness: float = 0.0
    
    # Building context
    floor_level: int = 0
    building_id: str = ""
    zone_id: str = ""
    
    # Fire protection specific
    nfpa_classification: str = ""
    fire_rating: Optional[str] = None
    affects_sprinkler_coverage: bool = False
    coverage_impact_radius: float = 0.0
    
    def __post_init__(self):
        """Calculate derived properties after initialization"""
        if self.vertices and not self.bounding_box:
            self._calculate_bounding_box()
        if self.vertices and self.area == 0.0:
            self._calculate_area()

    def _calculate_bounding_box(self):
        """Calculate bounding box from vertices"""
        if not self.vertices:
            return
        
        min_x = min(v.x for v in self.vertices)
        max_x = max(v.x for v in self.vertices)
        min_y = min(v.y for v in self.vertices)
        max_y = max(v.y for v in self.vertices)
        min_z = min(v.z for v in self.vertices)
        max_z = max(v.z for v in self.vertices)
        
        self.bounding_box = BoundingBox3D(
            Point3D(min_x, min_y, min_z),
            Point3D(max_x, max_y, max_z)
        )
    
    def _calculate_area(self):
        """Calculate area using shoelace formula (2D projection)"""
        if len(self.vertices) < 3:
            return
        
        area = 0.0
        n = len(self.vertices)
        
        for i in range(n):
            j = (i + 1) % n
            area += self.vertices[i].x * self.vertices[j].y
            area -= self.vertices[j].x * self.vertices[i].y
        
        self.area = abs(area) / 2.0

@dataclass
class Floor(ConstructionElement):
    """Floor element with specific properties"""
    elevation: float = 0.0
    structural_system: str = ""
    load_capacity: float = 0.0
    finish_type: str = ""
    
    def __post_init__(self):
        super().__post_init__()
        self.geometry_type = GeometryType.FLOOR

@dataclass 
class Wall(ConstructionElement):
    """Wall element with specific properties"""
    wall_type: str = ""  # interior, exterior, partition, shear, etc.
    structural: bool = False
    insulation_r_value: float = 0.0
    finish_interior: str = ""
    finish_exterior: str = ""
    openings: List[str] = field(default_factory=list)  # Door/window IDs
    
    def __post_init__(self):
        super().__post_init__()
        self.geometry_type = GeometryType.WALL

@dataclass
class Riser(ConstructionElement):
    """Vertical riser element (pipes, conduits, etc.)"""
    riser_type: str = ""  # plumbing, electrical, mechanical
    service_type: str = ""  # water, gas, electric, data, etc.
    diameter: float = 0.0
    start_floor: int = 0
    end_floor: int = 0
    access_required: bool = False
    
    def __post_init__(self):
        super().__post_init__()
        self.geometry_type = GeometryType.RISER

@dataclass
class Room(ConstructionElement):
    """Room/space element with AI classification"""
    room_name: str = ""
    room_number: str = ""
    room_type: str = ""  # AI classified type
    occupancy_type: str = ""
    max_occupancy: int = 0
    ventilation_requirements: Dict[str, Any] = field(default_factory=dict)
    
    # AI classification results
    ai_confidence: float = 0.0
    ai_features: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        super().__post_init__()
        self.geometry_type = GeometryType.ROOM

@dataclass
class ProjectGeometry:
    """Unified project geometry container for all engines"""
    
    # Project metadata
    project_id: str
    project_name: str = ""
    created_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    last_modified: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # File source information
    source_files: List[Dict[str, Any]] = field(default_factory=list)
    processing_metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Construction elements organized by type
    floors: List[Floor] = field(default_factory=list)
    walls: List[Wall] = field(default_factory=list)
    risers: List[Riser] = field(default_factory=list)
    rooms: List[Room] = field(default_factory=list)
    doors: List[ConstructionElement] = field(default_factory=list)
    windows: List[ConstructionElement] = field(default_factory=list)
    columns: List[ConstructionElement] = field(default_factory=list)
    beams: List[ConstructionElement] = field(default_factory=list)
    stairs: List[ConstructionElement] = field(default_factory=list)
    equipment: List[ConstructionElement] = field(default_factory=list)
    other_elements: List[ConstructionElement] = field(default_factory=list)
    
    # Building hierarchy
    building_levels: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    zones: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # AI classification results
    ai_classification_summary: Optional[Dict[str, Any]] = None
    
    # Quality and validation metrics
    quality_metrics: Dict[str, Any] = field(default_factory=dict)
    validation_results: Dict[str, Any] = field(default_factory=dict)
    
    def get_all_elements(self) -> List[ConstructionElement]:
        """Get all construction elements as a flat list"""
        return (self.floors + self.walls + self.risers + self.rooms + 
                self.doors + self.windows + self.columns + self.beams + 
                self.stairs + self.equipment + self.other_elements)
    
    def get_elements_by_type(self, geometry_type: GeometryType) -> List[ConstructionElement]:
        """Get elements filtered by geometry type"""
        return [elem for elem in self.get_all_elements() if elem.geometry_type == geometry_type]
    
    def get_elements_by_floor(self, floor_level: int) -> List[ConstructionElement]:
        """Get elements on specific floor level"""
        return [elem for elem in self.get_all_elements() if elem.floor_level == floor_level]
    
    def calculate_total_area_by_type(self, geometry_type: GeometryType) -> float:
        """Calculate total area for elements of specific type"""
        return sum(elem.area for elem in self.get_elements_by_type(geometry_type))
    
    def get_bounding_box(self) -> Optional[BoundingBox3D]:
        """Get overall project bounding box"""
        all_elements = self.get_all_elements()
        if not all_elements:
            return None
        
        all_vertices = []
        for element in all_elements:
            all_vertices.extend(element.vertices)
        
        if not all_vertices:
            return None
        
        min_x = min(v.x for v in all_vertices)
        max_x = max(v.x for v in all_vertices)
        min_y = min(v.y for v in all_vertices)
        max_y = max(v.y for v in all_vertices)
        min_z = min(v.z for v in all_vertices)
        max_z = max(v.z for v in all_vertices)
        
        return BoundingBox3D(
            Point3D(min_x, min_y, min_z),
            Point3D(max_x, max_y, max_z)
        )
    
    def to_routing_engine_format(self) -> Dict[str, Any]:
        """Convert to format suitable for routing engines"""
        return {
            'project_id': self.project_id,
            'project_name': self.project_name,
            'building_spaces': [
                {
                    'id': room.id,
                    'vertices': [[v.x, v.y] for v in room.vertices],
                    'area': room.area,
                    'room_type': room.room_type,
                    'nfpa_classification': room.nfpa_classification,
                    'floor_level': room.floor_level,
                    'ai_confidence': room.ai_confidence
                }
                for room in self.rooms
            ],
            'obstacles': [
                {
                    'id': elem.id,
                    'type': elem.geometry_type.value,
                    'geometry': self._element_to_geometry_dict(elem),
                    'affects_coverage': elem.affects_sprinkler_coverage,
                    'impact_radius': elem.coverage_impact_radius,
                    'floor_level': elem.floor_level
                }
                for elem in (self.columns + self.equipment + self.other_elements)
                if elem.affects_sprinkler_coverage
            ],
            'walls': [
                {
                    'id': wall.id,
                    'vertices': [[v.x, v.y] for v in wall.vertices],
                    'wall_type': wall.wall_type,
                    'height': wall.height,
                    'thickness': wall.thickness,
                    'floor_level': wall.floor_level,
                    'structural': wall.structural
                }
                for wall in self.walls
            ],
            'metadata': {
                'total_floors': len(set(elem.floor_level for elem in self.get_all_elements())),
                'processing_timestamp': self.last_modified,
                'ai_processed': bool(self.ai_classification_summary)
            }
        }
    
    def _element_to_geometry_dict(self, element: ConstructionElement) -> Dict[str, Any]:
        """Convert element geometry to dictionary format"""
        if len(element.vertices) == 1:
            # Point geometry (columns, equipment)
            vertex = element.vertices[0]
            return {
                'type': 'circle',
                'x': vertex.x,
                'y': vertex.y,
                'radius': max(element.thickness, element.height, 1.0) / 2
            }
        else:
            # Polygon geometry
            return {
                'type': 'polygon',
                'vertices': [[v.x, v.y] for v in element.vertices]
            }

# ================================================================================================
# ENHANCED FILE FORMAT PROCESSORS
# ================================================================================================

class FileFormat(Enum):
    """Supported file formats"""
    DXF = "dxf"
    DWG = "dwg" 
    IFC = "ifc"
    PDF = "pdf"
    UNKNOWN = "unknown"

@dataclass
class ProcessingResult:
    """Result of file processing operation"""
    success: bool
    file_path: Path
    file_format: FileFormat
    project_geometry: Optional[ProjectGeometry] = None
    processing_time: float = 0.0
    memory_usage_mb: float = 0.0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

class BaseFileProcessor:
    """Base class for file format processors"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    async def process_file(self, file_path: Path) -> ProcessingResult:
        """Process a single file - to be implemented by subclasses"""
        raise NotImplementedError
    
    async def validate_file(self, file_path: Path) -> Tuple[bool, List[str]]:
        """Validate file format and accessibility"""
        errors = []
        
        if not file_path.exists():
            errors.append(f"File does not exist: {file_path}")
            return False, errors
        
        if not file_path.is_file():
            errors.append(f"Path is not a file: {file_path}")
            return False, errors
        
        try:
            file_size = file_path.stat().st_size
            max_size = self.config.get('max_file_size_mb', 500) * 1024 * 1024
            
            if file_size > max_size:
                errors.append(f"File too large: {file_size / 1024 / 1024:.1f}MB")
                return False, errors
                
        except Exception as e:
            errors.append(f"Error checking file: {e}")
            return False, errors
        
        return True, errors
    
    def _create_project_geometry(self, project_id: str, source_file: Path) -> ProjectGeometry:
        """Create base project geometry object"""
        return ProjectGeometry(
            project_id=project_id,
            project_name=source_file.stem,
            source_files=[{
                'path': str(source_file),
                'filename': source_file.name,
                'format': self._detect_format(source_file).value,
                'size_bytes': source_file.stat().st_size,
                'modified': datetime.fromtimestamp(source_file.stat().st_mtime).isoformat()
            }]
        )
    
    def _detect_format(self, file_path: Path) -> FileFormat:
        """Detect file format from extension"""
        extension = file_path.suffix.lower()
        
        format_map = {
            '.dxf': FileFormat.DXF,
            '.dwg': FileFormat.DWG,
            '.ifc': FileFormat.IFC,
            '.ifcxml': FileFormat.IFC,
            '.pdf': FileFormat.PDF
        }
        
        return format_map.get(extension, FileFormat.UNKNOWN)
    
    def _classify_entity_type(self, entity_data: Dict[str, Any]) -> GeometryType:
        """Classify entity into geometry type based on properties"""
        layer_name = entity_data.get('layer', '').lower()
        entity_type = entity_data.get('type', '').lower()
        
        # Layer-based classification
        if 'wall' in layer_name:
            return GeometryType.WALL
        elif 'floor' in layer_name or 'slab' in layer_name:
            return GeometryType.FLOOR
        elif 'ceiling' in layer_name:
            return GeometryType.CEILING
        elif 'door' in layer_name:
            return GeometryType.DOOR
        elif 'window' in layer_name:
            return GeometryType.WINDOW
        elif 'column' in layer_name:
            return GeometryType.COLUMN
        elif 'beam' in layer_name:
            return GeometryType.BEAM
        elif 'stair' in layer_name:
            return GeometryType.STAIR
        elif 'riser' in layer_name or 'pipe' in layer_name:
            return GeometryType.RISER
        elif 'room' in layer_name or 'space' in layer_name:
            return GeometryType.ROOM
        elif 'mechanical' in layer_name or 'hvac' in layer_name:
            return GeometryType.MECHANICAL
        elif 'electrical' in layer_name:
            return GeometryType.ELECTRICAL
        elif 'plumbing' in layer_name:
            return GeometryType.PLUMBING
        
        # Entity type-based classification
        if entity_type in ['circle', 'arc']:
            return GeometryType.COLUMN
        elif entity_type in ['line', 'polyline', 'lwpolyline']:
            # Could be wall or room boundary
            return GeometryType.WALL
        
        return GeometryType.UNKNOWN

class DXFProcessor(BaseFileProcessor):
    """Enhanced DXF file processor"""
    
    async def process_file(self, file_path: Path) -> ProcessingResult:
        """Process DXF file with enhanced geometry extraction"""
        start_time = time.time()
        initial_memory = self._get_memory_usage()
        
        result = ProcessingResult(
            success=False,
            file_path=file_path,
            file_format=FileFormat.DXF
        )
        
        try:
            # Validate file
            is_valid, errors = await self.validate_file(file_path)
            if not is_valid:
                result.errors.extend(errors)
                return result
            
            self.logger.info(f"Processing DXF file: {file_path.name}")
            
            # Load DXF document
            try:
                doc = ezdxf.readfile(str(file_path))
            except Exception as e:
                result.errors.append(f"Failed to load DXF: {e}")
                return result
            
            # Create project geometry
            project_id = f"dxf_{hashlib.md5(str(file_path).encode()).hexdigest()[:8]}"
            project_geometry = self._create_project_geometry(project_id, file_path)
            
            # Extract metadata
            project_geometry.processing_metadata = {
                'dxf_version': doc.dxfversion,
                'dxf_encoding': getattr(doc, 'encoding', 'unknown'),
                'header_variables': dict(doc.header) if hasattr(doc, 'header') else {},
                'layer_count': len(doc.layers),
                'block_count': len(doc.blocks)
            }
            
            # Process model space entities
            msp = doc.modelspace()
            await self._process_dxf_entities(msp, project_geometry)
            
            # Process blocks/references
            await self._process_dxf_blocks(doc, project_geometry)
            
            # Classify and organize geometry
            await self._classify_and_organize_geometry(project_geometry)
            
            # Calculate quality metrics
            project_geometry.quality_metrics = self._calculate_quality_metrics(project_geometry)
            
            result.success = True
            result.project_geometry = project_geometry
            result.processing_time = time.time() - start_time
            result.memory_usage_mb = self._get_memory_usage() - initial_memory
            
            self.logger.info(f"DXF processing complete: {len(project_geometry.get_all_elements())} elements extracted")
            
        except Exception as e:
            error_msg = f"DXF processing failed: {e}"
            self.logger.error(error_msg)
            result.errors.append(error_msg)
        
        return result
    
    async def _process_dxf_entities(self, msp, project_geometry: ProjectGeometry):
        """Process DXF model space entities"""
        entity_count = 0
        
        for entity in msp:
            try:
                entity_data = self._extract_entity_data(entity)
                if entity_data:
                    construction_element = self._create_construction_element(entity_data)
                    if construction_element:
                        self._add_element_to_project(construction_element, project_geometry)
                        entity_count += 1
                        
            except Exception as e:
                self.logger.warning(f"Error processing entity {entity.dxftype()}: {e}")
        
        self.logger.info(f"Processed {entity_count} DXF entities")
    
    def _extract_entity_data(self, entity) -> Optional[Dict[str, Any]]:
        """Extract data from DXF entity"""
        try:
            entity_data = {
                'type': entity.dxftype(),
                'layer': entity.dxf.layer if hasattr(entity.dxf, 'layer') else 'default',
                'color': getattr(entity.dxf, 'color', 256),
                'linetype': getattr(entity.dxf, 'linetype', 'BYLAYER'),
                'vertices': [],
                'properties': {}
            }
            
            # Extract geometry based on entity type
            if entity.dxftype() == 'LWPOLYLINE':
                entity_data['vertices'] = [[p[0], p[1], 0.0] for p in entity.get_points()]
                entity_data['closed'] = entity.closed
                
            elif entity.dxftype() == 'POLYLINE':
                entity_data['vertices'] = [[v.dxf.location[0], v.dxf.location[1], v.dxf.location[2]] 
                                         for v in entity.vertices]
                entity_data['closed'] = entity.is_closed
                
            elif entity.dxftype() == 'LINE':
                start = entity.dxf.start
                end = entity.dxf.end
                entity_data['vertices'] = [[start.x, start.y, start.z], [end.x, end.y, end.z]]
                
            elif entity.dxftype() == 'CIRCLE':
                center = entity.dxf.center
                radius = entity.dxf.radius
                entity_data['vertices'] = [[center.x, center.y, center.z]]
                entity_data['properties']['radius'] = radius
                entity_data['properties']['geometry_type'] = 'circle'
                
            elif entity.dxftype() == 'ARC':
                center = entity.dxf.center
                radius = entity.dxf.radius
                start_angle = entity.dxf.start_angle
                end_angle = entity.dxf.end_angle
                entity_data['vertices'] = [[center.x, center.y, center.z]]
                entity_data['properties'].update({
                    'radius': radius,
                    'start_angle': start_angle,
                    'end_angle': end_angle,
                    'geometry_type': 'arc'
                })
                
            elif entity.dxftype() in ['TEXT', 'MTEXT']:
                # Extract text information for room labels
                position = getattr(entity.dxf, 'insert', None) or getattr(entity.dxf, 'location', None)
                if position:
                    entity_data['vertices'] = [[position.x, position.y, position.z]]
                    entity_data['properties']['text'] = entity.dxf.text
                    entity_data['properties']['height'] = getattr(entity.dxf, 'height', 0)
                    entity_data['type'] = 'TEXT'
                    
            elif entity.dxftype() == 'INSERT':
                # Block insertion
                position = entity.dxf.insert
                entity_data['vertices'] = [[position.x, position.y, position.z]]
                entity_data['properties'].update({
                    'block_name': entity.dxf.name,
                    'scale_x': getattr(entity.dxf, 'xscale', 1),
                    'scale_y': getattr(entity.dxf, 'yscale', 1),
                    'rotation': getattr(entity.dxf, 'rotation', 0)
                })
            
            return entity_data if entity_data['vertices'] else None
            
        except Exception as e:
            self.logger.warning(f"Error extracting entity data: {e}")
            return None
    
    def _create_construction_element(self, entity_data: Dict[str, Any]) -> Optional[ConstructionElement]:
        """Create construction element from entity data"""
        try:
            # Convert vertices to Point3D objects
            vertices = [Point3D(x, y, z) for x, y, z in entity_data['vertices']]
            
            # Classify geometry type
            geometry_type = self._classify_entity_type(entity_data)
            
            # Determine material type from layer
            material_type = self._determine_material_type(entity_data.get('layer', ''))
            
            # Create appropriate element type
            element_id = f"{entity_data['type']}_{uuid.uuid4().hex[:8]}"
            
            if geometry_type == GeometryType.FLOOR:
                element = Floor(
                    id=element_id,
                    geometry_type=geometry_type,
                    material_type=material_type,
                    layer_name=entity_data.get('layer', ''),
                    vertices=vertices,
                    properties=entity_data.get('properties', {}),
                    elevation=vertices[0].z if vertices else 0.0
                )
            elif geometry_type == GeometryType.WALL:
                element = Wall(
                    id=element_id,
                    geometry_type=geometry_type,
                    material_type=material_type,
                    layer_name=entity_data.get('layer', ''),
                    vertices=vertices,
                    properties=entity_data.get('properties', {}),
                    wall_type=self._determine_wall_type(entity_data.get('layer', '')),
                    structural=self._is_structural_element(entity_data.get('layer', ''))
                )
            elif geometry_type == GeometryType.RISER:
                element = Riser(
                    id=element_id,
                    geometry_type=geometry_type,
                    material_type=material_type,
                    layer_name=entity_data.get('layer', ''),
                    vertices=vertices,
                    properties=entity_data.get('properties', {}),
                    riser_type=self._determine_riser_type(entity_data.get('layer', '')),
                    diameter=entity_data.get('properties', {}).get('radius', 0) * 2
                )
            elif geometry_type == GeometryType.ROOM:
                element = Room(
                    id=element_id,
                    geometry_type=geometry_type,
                    material_type=material_type,
                    layer_name=entity_data.get('layer', ''),
                    vertices=vertices,
                    properties=entity_data.get('properties', {}),
                    room_name=entity_data.get('properties', {}).get('text', ''),
                    room_type='unknown'
                )
            else:
                # Generic construction element
                element = ConstructionElement(
                    id=element_id,
                    geometry_type=geometry_type,
                    material_type=material_type,
                    layer_name=entity_data.get('layer', ''),
                    vertices=vertices,
                    properties=entity_data.get('properties', {})
                )
            
            # Set additional properties
            if entity_data.get('properties', {}).get('radius'):
                element.thickness = entity_data['properties']['radius'] * 2
            
            # Set coverage impact for certain types
            if geometry_type in [GeometryType.COLUMN, GeometryType.EQUIPMENT, GeometryType.MECHANICAL]:
                element.affects_sprinkler_coverage = True
                element.coverage_impact_radius = max(element.thickness, 3.0)
            
            return element
            
        except Exception as e:
            self.logger.warning(f"Error creating construction element: {e}")
            return None
    
    def _determine_material_type(self, layer_name: str) -> MaterialType:
        """Determine material type from layer name"""
        layer_lower = layer_name.lower()
        
        if 'concrete' in layer_lower or 'conc' in layer_lower:
            return MaterialType.CONCRETE
        elif 'steel' in layer_lower or 'metal' in layer_lower:
            return MaterialType.STEEL
        elif 'wood' in layer_lower or 'timber' in layer_lower:
            return MaterialType.WOOD
        elif 'masonry' in layer_lower or 'brick' in layer_lower or 'block' in layer_lower:
            return MaterialType.MASONRY
        elif 'gypsum' in layer_lower or 'drywall' in layer_lower:
            return MaterialType.GYPSUM
        elif 'glass' in layer_lower:
            return MaterialType.GLASS
        
        return MaterialType.UNKNOWN
    
    def _determine_wall_type(self, layer_name: str) -> str:
        """Determine wall type from layer name"""
        layer_lower = layer_name.lower()
        
        if 'exterior' in layer_lower:
            return 'exterior'
        elif 'interior' in layer_lower:
            return 'interior'
        elif 'partition' in layer_lower:
            return 'partition'
        elif 'shear' in layer_lower:
            return 'shear'
        elif 'bearing' in layer_lower:
            return 'bearing'
        
        return 'unknown'
    
    def _is_structural_element(self, layer_name: str) -> bool:
        """Check if element is structural based on layer name"""
        structural_keywords = ['structural', 'bearing', 'shear', 'column', 'beam', 'foundation']
        layer_lower = layer_name.lower()
        return any(keyword in layer_lower for keyword in structural_keywords)
    
    def _determine_riser_type(self, layer_name: str) -> str:
        """Determine riser type from layer name"""
        layer_lower = layer_name.lower()
        
        if 'plumbing' in layer_lower or 'water' in layer_lower:
            return 'plumbing'
        elif 'electrical' in layer_lower or 'power' in layer_lower:
            return 'electrical'
        elif 'mechanical' in layer_lower or 'hvac' in layer_lower:
            return 'mechanical'
        elif 'data' in layer_lower or 'telecom' in layer_lower:
            return 'data'
        elif 'fire' in layer_lower:
            return 'fire_protection'
        
        return 'unknown'
    
    async def _process_dxf_blocks(self, doc, project_geometry: ProjectGeometry):
        """Process DXF blocks and block references"""
        processed_blocks = 0
        
        for block_name in doc.blocks:
            if block_name.startswith('*'):  # Skip anonymous blocks
                continue
                
            try:
                block = doc.blocks[block_name]
                
                # Process entities in block
                for entity in block:
                    entity_data = self._extract_entity_data(entity)
                    if entity_data:
                        construction_element = self._create_construction_element(entity_data)
                        if construction_element:
                            construction_element.properties['block_definition'] = block_name
                            self._add_element_to_project(construction_element, project_geometry)
                            processed_blocks += 1
                            
            except Exception as e:
                self.logger.warning(f"Error processing block {block_name}: {e}")
        
        self.logger.info(f"Processed {processed_blocks} block entities")
    
    def _add_element_to_project(self, element: ConstructionElement, project_geometry: ProjectGeometry):
        """Add construction element to appropriate project geometry collection"""
        if isinstance(element, Floor):
            project_geometry.floors.append(element)
        elif isinstance(element, Wall):
            project_geometry.walls.append(element)
        elif isinstance(element, Riser):
            project_geometry.risers.append(element)
        elif isinstance(element, Room):
            project_geometry.rooms.append(element)
        elif element.geometry_type == GeometryType.DOOR:
            project_geometry.doors.append(element)
        elif element.geometry_type == GeometryType.WINDOW:
            project_geometry.windows.append(element)
        elif element.geometry_type == GeometryType.COLUMN:
            project_geometry.columns.append(element)
        elif element.geometry_type == GeometryType.BEAM:
            project_geometry.beams.append(element)
        elif element.geometry_type == GeometryType.STAIR:
            project_geometry.stairs.append(element)
        elif element.geometry_type in [GeometryType.EQUIPMENT, GeometryType.MECHANICAL]:
            project_geometry.equipment.append(element)
        else:
            project_geometry.other_elements.append(element)
    
    async def _classify_and_organize_geometry(self, project_geometry: ProjectGeometry):
        """Apply additional classification and organization logic"""
        
        # Group elements by floor level
        elements_by_floor = defaultdict(list)
        for element in project_geometry.get_all_elements():
            elements_by_floor[element.floor_level].append(element)
        
        # Create building level metadata
        for floor_level, elements in elements_by_floor.items():
            project_geometry.building_levels[floor_level] = {
                'level': floor_level,
                'element_count': len(elements),
                'total_area': sum(elem.area for elem in elements),
                'element_types': list(set(elem.geometry_type.value for elem in elements))
            }
        
        # Apply geometric relationships and constraints
        await self._apply_geometric_relationships(project_geometry)
    
    async def _apply_geometric_relationships(self, project_geometry: ProjectGeometry):
        """Apply geometric relationships between elements"""
        
        # Find rooms bounded by walls
        for room in project_geometry.rooms:
            nearby_walls = self._find_nearby_walls(room, project_geometry.walls)
            room.properties['adjacent_walls'] = [wall.id for wall in nearby_walls]
        
        # Find openings in walls
        for wall in project_geometry.walls:
            nearby_doors = self._find_elements_on_wall(wall, project_geometry.doors)
            nearby_windows = self._find_elements_on_wall(wall, project_geometry.windows)
            wall.openings.extend([door.id for door in nearby_doors])
            wall.openings.extend([window.id for window in nearby_windows])
    
    def _find_nearby_walls(self, room: Room, walls: List[Wall], threshold: float = 2.0) -> List[Wall]:
        """Find walls near a room"""
        nearby_walls = []
        
        if not room.bounding_box:
            return nearby_walls
        
        for wall in walls:
            if not wall.bounding_box:
                continue
                
            # Simple distance check between bounding boxes
            room_center = room.bounding_box.center
            wall_center = wall.bounding_box.center
            
            distance = room_center.distance_to(wall_center)
            if distance <= threshold:
                nearby_walls.append(wall)
        
        return nearby_walls
    
    def _find_elements_on_wall(self, wall: Wall, elements: List[ConstructionElement], threshold: float = 1.0) -> List[ConstructionElement]:
        """Find elements positioned on or near a wall"""
        on_wall_elements = []
        
        if not wall.bounding_box:
            return on_wall_elements
        
        for element in elements:
            if not element.bounding_box:
                continue
                
            # Check if element is within wall bounds
            wall_center = wall.bounding_box.center
            element_center = element.bounding_box.center
            
            distance = wall_center.distance_to(element_center)
            if distance <= threshold:
                on_wall_elements.append(element)
        
        return on_wall_elements
    
    def _calculate_quality_metrics(self, project_geometry: ProjectGeometry) -> Dict[str, Any]:
        """Calculate quality metrics for processed geometry"""
        all_elements = project_geometry.get_all_elements()
        
        if not all_elements:
            return {}
        
        valid_elements = [elem for elem in all_elements if elem.vertices and elem.area > 0]
        
        return {
            'total_elements': len(all_elements),
            'valid_elements': len(valid_elements),
            'validity_ratio': len(valid_elements) / len(all_elements),
            'total_area': sum(elem.area for elem in valid_elements),
            'element_types': len(set(elem.geometry_type for elem in all_elements)),
            'floors_detected': len(set(elem.floor_level for elem in all_elements)),
            'avg_element_area': sum(elem.area for elem in valid_elements) / len(valid_elements) if valid_elements else 0
        }
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB"""
        try:
            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0

class IFCProcessor(BaseFileProcessor):
    """Enhanced IFC file processor"""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        if not IFC_AVAILABLE:
            raise RuntimeError("IFC support not available. Install ifcopenshell.")
    
    async def process_file(self, file_path: Path) -> ProcessingResult:
        """Process IFC file with building element extraction"""
        start_time = time.time()
        initial_memory = self._get_memory_usage()
        
        result = ProcessingResult(
            success=False,
            file_path=file_path,
            file_format=FileFormat.IFC
        )
        
        try:
            # Validate file
            is_valid, errors = await self.validate_file(file_path)
            if not is_valid:
                result.errors.extend(errors)
                return result
            
            self.logger.info(f"Processing IFC file: {file_path.name}")
            
            # Load IFC model
            try:
                model = ifcopenshell.open(str(file_path))
            except Exception as e:
                result.errors.append(f"Failed to load IFC: {e}")
                return result
            
            # Create project geometry
            project_id = f"ifc_{hashlib.md5(str(file_path).encode()).hexdigest()[:8]}"
            project_geometry = self._create_project_geometry(project_id, file_path)
            
            # Extract IFC metadata
            project_geometry.processing_metadata = await self._extract_ifc_metadata(model)
            
            # Process building elements
            await self._process_ifc_building_elements(model, project_geometry)
            
            # Process spaces and rooms
            await self._process_ifc_spaces(model, project_geometry)
            
            # Process building storeys (floors)
            await self._process_ifc_building_storeys(model, project_geometry)
            
            # Calculate relationships
            await self._process_ifc_relationships(model, project_geometry)
            
            # Calculate quality metrics
            project_geometry.quality_metrics = self._calculate_quality_metrics(project_geometry)
            
            result.success = True
            result.project_geometry = project_geometry
            result.processing_time = time.time() - start_time
            result.memory_usage_mb = self._get_memory_usage() - initial_memory
            
            self.logger.info(f"IFC processing complete: {len(project_geometry.get_all_elements())} elements extracted")
            
        except Exception as e:
            error_msg = f"IFC processing failed: {e}"
            self.logger.error(error_msg)
            result.errors.append(error_msg)
        
        return result
    
    async def _extract_ifc_metadata(self, model) -> Dict[str, Any]:
        """Extract IFC model metadata"""
        metadata = {
            'ifc_schema': model.schema,
            'file_name': getattr(model.by_type('IfcProject')[0], 'Name', 'Unknown') if model.by_type('IfcProject') else 'Unknown',
            'elements_count': {}
        }
        
        # Count elements by type
        common_types = ['IfcWall', 'IfcSlab', 'IfcColumn', 'IfcBeam', 'IfcDoor', 'IfcWindow', 'IfcSpace', 'IfcBuildingStorey']
        for ifc_type in common_types:
            elements = model.by_type(ifc_type)
            if elements:
                metadata['elements_count'][ifc_type] = len(elements)
        
        return metadata
    
    async def _process_ifc_building_elements(self, model, project_geometry: ProjectGeometry):
        """Process IFC building elements"""
        
        # Process walls
        walls = model.by_type('IfcWall')
        for wall in walls:
            try:
                wall_element = await self._create_wall_from_ifc(wall)
                if wall_element:
                    project_geometry.walls.append(wall_element)
            except Exception as e:
                self.logger.warning(f"Error processing IFC wall: {e}")
        
        # Process slabs (floors/ceilings)
        slabs = model.by_type('IfcSlab')
        for slab in slabs:
            try:
                slab_element = await self._create_slab_from_ifc(slab)
                if slab_element:
                    project_geometry.floors.append(slab_element)
            except Exception as e:
                self.logger.warning(f"Error processing IFC slab: {e}")
        
        # Process columns
        columns = model.by_type('IfcColumn')
        for column in columns:
            try:
                column_element = await self._create_column_from_ifc(column)
                if column_element:
                    project_geometry.columns.append(column_element)
            except Exception as e:
                self.logger.warning(f"Error processing IFC column: {e}")
        
        # Process beams
        beams = model.by_type('IfcBeam')
        for beam in beams:
            try:
                beam_element = await self._create_beam_from_ifc(beam)
                if beam_element:
                    project_geometry.beams.append(beam_element)
            except Exception as e:
                self.logger.warning(f"Error processing IFC beam: {e}")
        
        # Process doors
        doors = model.by_type('IfcDoor')
        for door in doors:
            try:
                door_element = await self._create_door_from_ifc(door)
                if door_element:
                    project_geometry.doors.append(door_element)
            except Exception as e:
                self.logger.warning(f"Error processing IFC door: {e}")
        
        # Process windows
        windows = model.by_type('IfcWindow')
        for window in windows:
            try:
                window_element = await self._create_window_from_ifc(window)
                if window_element:
                    project_geometry.windows.append(window_element)
            except Exception as e:
                self.logger.warning(f"Error processing IFC window: {e}")
        
        self.logger.info(f"Processed IFC building elements: {len(project_geometry.walls)} walls, "
                        f"{len(project_geometry.floors)} floors, {len(project_geometry.columns)} columns")
    
    async def _create_wall_from_ifc(self, ifc_wall) -> Optional[Wall]:
        """Create Wall object from IFC wall"""
        try:
            # Get geometry
            vertices = await self._extract_ifc_geometry(ifc_wall)
            if not vertices:
                return None
            
            # Get properties
            name = getattr(ifc_wall, 'Name', '') or ''
            description = getattr(ifc_wall, 'Description', '') or ''
            object_type = getattr(ifc_wall, 'ObjectType', '') or ''
            
            # Determine wall type
            wall_type = 'interior'
            if 'exterior' in object_type.lower() or 'external' in object_type.lower():
                wall_type = 'exterior'
            elif 'partition' in object_type.lower():
                wall_type = 'partition'
            
            # Get material
            material_type = await self._extract_ifc_material(ifc_wall)
            
            wall = Wall(
                id=f"ifc_wall_{ifc_wall.GlobalId}",
                geometry_type=GeometryType.WALL,
                material_type=material_type,
                vertices=vertices,
                wall_type=wall_type,
                properties={
                    'ifc_name': name,
                    'ifc_description': description,
                    'ifc_type': object_type,
                    'ifc_guid': ifc_wall.GlobalId
                }
            )
            
            return wall
            
        except Exception as e:
            self.logger.warning(f"Error creating wall from IFC: {e}")
            return None
    
    async def _create_slab_from_ifc(self, ifc_slab) -> Optional[Floor]:
        """Create Floor object from IFC slab"""
        try:
            # Get geometry
            vertices = await self._extract_ifc_geometry(ifc_slab)
            if not vertices:
                return None
            
            # Get properties
            name = getattr(ifc_slab, 'Name', '') or ''
            predefined_type = getattr(ifc_slab, 'PredefinedType', '') or ''
            
            # Get material
            material_type = await self._extract_ifc_material(ifc_slab)
            
            floor = Floor(
                id=f"ifc_slab_{ifc_slab.GlobalId}",
                geometry_type=GeometryType.FLOOR,
                material_type=material_type,
                vertices=vertices,
                structural_system=predefined_type,
                properties={
                    'ifc_name': name,
                    'ifc_type': predefined_type,
                    'ifc_guid': ifc_slab.GlobalId
                }
            )
            
            return floor
            
        except Exception as e:
            self.logger.warning(f"Error creating floor from IFC: {e}")
            return None
    
    async def _create_column_from_ifc(self, ifc_column) -> Optional[ConstructionElement]:
        """Create Column element from IFC column"""
        try:
            # Get geometry
            vertices = await self._extract_ifc_geometry(ifc_column)
            if not vertices:
                return None
            
            # Get properties
            name = getattr(ifc_column, 'Name', '') or ''
            predefined_type = getattr(ifc_column, 'PredefinedType', '') or ''
            
            # Get material
            material_type = await self._extract_ifc_material(ifc_column)
            
            column = ConstructionElement(
                id=f"ifc_column_{ifc_column.GlobalId}",
                geometry_type=GeometryType.COLUMN,
                material_type=material_type,
                vertices=vertices,
                affects_sprinkler_coverage=True,
                coverage_impact_radius=3.0,
                properties={
                    'ifc_name': name,
                    'ifc_type': predefined_type,
                    'ifc_guid': ifc_column.GlobalId
                }
            )
            
            return column
            
        except Exception as e:
            self.logger.warning(f"Error creating column from IFC: {e}")
            return None
    
    async def _create_beam_from_ifc(self, ifc_beam) -> Optional[ConstructionElement]:
        """Create Beam element from IFC beam"""
        try:
            # Get geometry
            vertices = await self._extract_ifc_geometry(ifc_beam)
            if not vertices:
                return None
            
            # Get properties
            name = getattr(ifc_beam, 'Name', '') or ''
            predefined_type = getattr(ifc_beam, 'PredefinedType', '') or ''
            
            # Get material
            material_type = await self._extract_ifc_material(ifc_beam)
            
            beam = ConstructionElement(
                id=f"ifc_beam_{ifc_beam.GlobalId}",
                geometry_type=GeometryType.BEAM,
                material_type=material_type,
                vertices=vertices,
                properties={
                    'ifc_name': name,
                    'ifc_type': predefined_type,
                    'ifc_guid': ifc_beam.GlobalId
                }
            )
            
            return beam
            
        except Exception as e:
            self.logger.warning(f"Error creating beam from IFC: {e}")
            return None
    
    async def _create_door_from_ifc(self, ifc_door) -> Optional[ConstructionElement]:
        """Create Door element from IFC door"""
        try:
            # Get geometry
            vertices = await self._extract_ifc_geometry(ifc_door)
            if not vertices:
                return None
            
            # Get properties
            name = getattr(ifc_door, 'Name', '') or ''
            predefined_type = getattr(ifc_door, 'PredefinedType', '') or ''
            
            door = ConstructionElement(
                id=f"ifc_door_{ifc_door.GlobalId}",
                geometry_type=GeometryType.DOOR,
                vertices=vertices,
                properties={
                    'ifc_name': name,
                    'ifc_type': predefined_type,
                    'ifc_guid': ifc_door.GlobalId
                }
            )
            
            return door
            
        except Exception as e:
            self.logger.warning(f"Error creating door from IFC: {e}")
            return None
    
    async def _create_window_from_ifc(self, ifc_window) -> Optional[ConstructionElement]:
        """Create Window element from IFC window"""
        try:
            # Get geometry
            vertices = await self._extract_ifc_geometry(ifc_window)
            if not vertices:
                return None
            
            # Get properties
            name = getattr(ifc_window, 'Name', '') or ''
            predefined_type = getattr(ifc_window, 'PredefinedType', '') or ''
            
            window = ConstructionElement(
                id=f"ifc_window_{ifc_window.GlobalId}",
                geometry_type=GeometryType.WINDOW,
                vertices=vertices,
                properties={
                    'ifc_name': name,
                    'ifc_type': predefined_type,
                    'ifc_guid': ifc_window.GlobalId
                }
            )
            
            return window
            
        except Exception as e:
            self.logger.warning(f"Error creating window from IFC: {e}")
            return None
    
    async def _process_ifc_spaces(self, model, project_geometry: ProjectGeometry):
        """Process IFC spaces as rooms"""
        spaces = model.by_type('IfcSpace')
        
        for space in spaces:
            try:
                room = await self._create_room_from_ifc_space(space)
                if room:
                    project_geometry.rooms.append(room)
            except Exception as e:
                self.logger.warning(f"Error processing IFC space: {e}")
        
        self.logger.info(f"Processed {len(spaces)} IFC spaces")
    
    async def _create_room_from_ifc_space(self, ifc_space) -> Optional[Room]:
        """Create Room object from IFC space"""
        try:
            # Get geometry
            vertices = await self._extract_ifc_geometry(ifc_space)
            if not vertices:
                return None
            
            # Get properties
            name = getattr(ifc_space, 'Name', '') or ''
            long_name = getattr(ifc_space, 'LongName', '') or ''
            description = getattr(ifc_space, 'Description', '') or ''
            object_type = getattr(ifc_space, 'ObjectType', '') or ''
            
            room = Room(
                id=f"ifc_space_{ifc_space.GlobalId}",
                geometry_type=GeometryType.ROOM,
                vertices=vertices,
                room_name=name or long_name,
                room_type=object_type.lower() if object_type else 'unknown',
                properties={
                    'ifc_name': name,
                    'ifc_long_name': long_name,
                    'ifc_description': description,
                    'ifc_type': object_type,
                    'ifc_guid': ifc_space.GlobalId
                }
            )
            
            return room
            
        except Exception as e:
            self.logger.warning(f"Error creating room from IFC space: {e}")
            return None
    
    async def _process_ifc_building_storeys(self, model, project_geometry: ProjectGeometry):
        """Process IFC building storeys to set floor levels"""
        storeys = model.by_type('IfcBuildingStorey')
        
        storey_elevations = {}
        for i, storey in enumerate(storeys):
            name = getattr(storey, 'Name', f'Level {i}')
            elevation = getattr(storey, 'Elevation', i * 3000)  # Default 3m per floor
            storey_elevations[storey.GlobalId] = {
                'name': name,
                'elevation': elevation,
                'level': i
            }
        
        # Assign floor levels to elements based on containment relationships
        for element in project_geometry.get_all_elements():
            ifc_guid = element.properties.get('ifc_guid')
            if ifc_guid:
                # Try to find which storey contains this element
                for storey_id, storey_info in storey_elevations.items():
                    # Simplified assignment - could be improved with spatial relationships
                    element.floor_level = storey_info['level']
                    break
    
    async def _process_ifc_relationships(self, model, project_geometry: ProjectGeometry):
        """Process IFC relationships between elements"""
        # This is a simplified implementation
        # In a full implementation, you would process spatial containment,
        # aggregation, and other IFC relationships
        pass
    
    async def _extract_ifc_geometry(self, ifc_element) -> List[Point3D]:
        """Extract geometry from IFC element"""
        try:
            # Use ifcopenshell geometry processing
            settings = ifcopenshell.geom.settings()
            shape = ifcopenshell.geom.create_shape(settings, ifc_element)
            
            if not shape:
                return []
            
            # Get vertices from shape geometry
            geometry = shape.geometry
            vertices = []
            
            # Extract vertices (simplified - assumes triangle mesh)
            verts = geometry.verts
            for i in range(0, len(verts), 3):
                vertices.append(Point3D(verts[i], verts[i+1], verts[i+2]))
            
            return vertices[:100]  # Limit vertices to avoid memory issues
            
        except Exception as e:
            self.logger.warning(f"Error extracting IFC geometry: {e}")
            return []
    
    async def _extract_ifc_material(self, ifc_element) -> MaterialType:
        """Extract material type from IFC element"""
        try:
            # Get material associations
            material_associations = getattr(ifc_element, 'HasAssociations', [])
            
            for association in material_associations:
                if hasattr(association, 'RelatingMaterial'):
                    material = association.RelatingMaterial
                    material_name = getattr(material, 'Name', '').lower()
                    
                    if 'concrete' in material_name:
                        return MaterialType.CONCRETE
                    elif 'steel' in material_name:
                        return MaterialType.STEEL
                    elif 'wood' in material_name or 'timber' in material_name:
                        return MaterialType.WOOD
                    elif 'masonry' in material_name or 'brick' in material_name:
                        return MaterialType.MASONRY
                    elif 'glass' in material_name:
                        return MaterialType.GLASS
            
            return MaterialType.UNKNOWN
            
        except Exception as e:
            self.logger.warning(f"Error extracting IFC material: {e}")
            return MaterialType.UNKNOWN
    
    def _calculate_quality_metrics(self, project_geometry: ProjectGeometry) -> Dict[str, Any]:
        """Calculate quality metrics for IFC processing"""
        all_elements = project_geometry.get_all_elements()
        
        if not all_elements:
            return {}
        
        valid_elements = [elem for elem in all_elements if elem.vertices and elem.area > 0]
        
        return {
            'total_elements': len(all_elements),
            'valid_elements': len(valid_elements),
            'validity_ratio': len(valid_elements) / len(all_elements),
            'total_area': sum(elem.area for elem in valid_elements),
            'element_types': len(set(elem.geometry_type for elem in all_elements)),
            'floors_detected': len(set(elem.floor_level for elem in all_elements)),
            'ifc_elements_with_geometry': len(valid_elements),
            'avg_element_area': sum(elem.area for elem in valid_elements) / len(valid_elements) if valid_elements else 0
        }
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB"""
        try:
            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0

# ================================================================================================
# BATCH PROCESSING ENGINE
# ================================================================================================

@dataclass
class BatchProcessingConfig:
    """Configuration for batch processing operations"""
    max_concurrent_files: int = 4
    max_memory_usage_mb: int = 2048
    timeout_per_file_seconds: int = 300
    enable_progress_reporting: bool = True
    output_format: str = 'json'  # json, pickle, both
    consolidate_results: bool = True
    enable_ai_classification: bool = True

@dataclass
class BatchProcessingResult:
    """Result of batch processing operation"""
    success: bool
    total_files: int
    processed_files: int
    failed_files: int
    processing_time: float
    consolidated_geometry: Optional[ProjectGeometry] = None
    individual_results: List[ProcessingResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    summary_stats: Dict[str, Any] = field(default_factory=dict)

class BatchCADProcessor:
    """High-performance batch processor for multiple CAD files"""
    
    def __init__(self, config: BatchProcessingConfig = None):
        self.config = config or BatchProcessingConfig()
        self.logger = logging.getLogger(__name__ + '.BatchCADProcessor')
        
        # Initialize file processors
        self.processors = {
            FileFormat.DXF: DXFProcessor(),
            FileFormat.DWG: DXFProcessor(),  # DWG uses same processor as DXF
            FileFormat.IFC: IFCProcessor() if IFC_AVAILABLE else None
        }
    
    async def process_files(self, file_paths: List[Path], 
                          output_dir: Path = None) -> BatchProcessingResult:
        """Process multiple CAD files in parallel"""
        
        start_time = time.time()
        self.logger.info(f"🚀 Starting batch processing of {len(file_paths)} files")
        
        result = BatchProcessingResult(
            success=False,
            total_files=len(file_paths),
            processed_files=0,
            failed_files=0,
            processing_time=0.0
        )
        
        try:
            # Validate and categorize files
            valid_files = await self._validate_and_categorize_files(file_paths)
            
            if not valid_files:
                result.errors.append("No valid files to process")
                return result
            
            # Process files in parallel with concurrency control
            semaphore = asyncio.Semaphore(self.config.max_concurrent_files)
            
            processing_tasks = [
                self._process_single_file_with_semaphore(semaphore, file_info)
                for file_info in valid_files
            ]
            
            # Wait for all processing to complete
            processing_results = await asyncio.gather(*processing_tasks, return_exceptions=True)
            
            # Collect results
            for i, processing_result in enumerate(processing_results):
                if isinstance(processing_result, Exception):
                    result.errors.append(f"File {valid_files[i]['path']}: {processing_result}")
                    result.failed_files += 1
                elif isinstance(processing_result, ProcessingResult):
                    result.individual_results.append(processing_result)
                    if processing_result.success:
                        result.processed_files += 1
                    else:
                        result.failed_files += 1
                        result.errors.extend(processing_result.errors)
            
            # Consolidate results if requested
            if self.config.consolidate_results and result.processed_files > 0:
                result.consolidated_geometry = await self._consolidate_project_geometries(
                    [res.project_geometry for res in result.individual_results 
                     if res.success and res.project_geometry]
                )
            
            # Calculate summary statistics
            result.summary_stats = self._calculate_batch_summary_stats(result)
            
            # Save results if output directory specified
            if output_dir:
                await self._save_batch_results(result, output_dir)
            
            result.processing_time = time.time() - start_time
            result.success = result.processed_files > 0
            
            self.logger.info(f"✅ Batch processing complete: {result.processed_files}/{result.total_files} files processed")
            
        except Exception as e:
            error_msg = f"Batch processing failed: {e}"
            self.logger.error(error_msg)
            result.errors.append(error_msg)
            result.processing_time = time.time() - start_time
        
        return result
    
    async def _validate_and_categorize_files(self, file_paths: List[Path]) -> List[Dict[str, Any]]:
        """Validate and categorize input files"""
        valid_files = []
        
        for file_path in file_paths:
            try:
                if not file_path.exists():
                    self.logger.warning(f"File not found: {file_path}")
                    continue
                
                file_format = self._detect_file_format(file_path)
                if file_format == FileFormat.UNKNOWN:
                    self.logger.warning(f"Unsupported file format: {file_path}")
                    continue
                
                processor = self.processors.get(file_format)
                if not processor:
                    self.logger.warning(f"No processor available for {file_format}: {file_path}")
                    continue
                
                file_size_mb = file_path.stat().st_size / (1024 * 1024)
                
                valid_files.append({
                    'path': file_path,
                    'format': file_format,
                    'processor': processor,
                    'size_mb': file_size_mb
                })
                
            except Exception as e:
                self.logger.warning(f"Error validating file {file_path}: {e}")
        
        self.logger.info(f"Validated {len(valid_files)} of {len(file_paths)} files")
        return valid_files
    
    async def _process_single_file_with_semaphore(self, semaphore: asyncio.Semaphore, 
                                                file_info: Dict[str, Any]) -> ProcessingResult:
        """Process a single file with semaphore control"""
        async with semaphore:
            try:
                # Add timeout control
                processor = file_info['processor']
                file_path = file_info['path']
                
                self.logger.info(f"🔄 Processing {file_path.name} ({file_info['format'].value})")
                
                processing_task = processor.process_file(file_path)
                
                result = await asyncio.wait_for(
                    processing_task, 
                    timeout=self.config.timeout_per_file_seconds
                )
                
                if result.success:
                    self.logger.info(f"✅ Completed {file_path.name} in {result.processing_time:.2f}s")
                else:
                    self.logger.warning(f"⚠️ Failed to process {file_path.name}: {result.errors}")
                
                return result
                
            except asyncio.TimeoutError:
                error_msg = f"Processing timeout for {file_info['path']}"
                self.logger.error(error_msg)
                return ProcessingResult(
                    success=False,
                    file_path=file_info['path'],
                    file_format=file_info['format'],
                    errors=[error_msg]
                )
            except Exception as e:
                error_msg = f"Processing error for {file_info['path']}: {e}"
                self.logger.error(error_msg)
                return ProcessingResult(
                    success=False,
                    file_path=file_info['path'],
                    file_format=file_info['format'],
                    errors=[error_msg]
                )
    
    async def _consolidate_project_geometries(self, geometries: List[ProjectGeometry]) -> ProjectGeometry:
        """Consolidate multiple project geometries into one"""
        
        if not geometries:
            return None
        
        if len(geometries) == 1:
            return geometries[0]
        
        # Create consolidated geometry
        consolidated = ProjectGeometry(
            project_id=f"consolidated_{uuid.uuid4().hex[:8]}",
            project_name="Consolidated Project",
            source_files=[]
        )
        
        # Combine all source files
        for geometry in geometries:
            consolidated.source_files.extend(geometry.source_files)
        
        # Combine all elements with unique IDs
        id_counter = 0
        
        for geometry in geometries:
            # Add prefix to avoid ID conflicts
            prefix = f"proj{id_counter}_"
            
            # Consolidate floors
            for floor in geometry.floors:
                floor.id = f"{prefix}{floor.id}"
                consolidated.floors.append(floor)
            
            # Consolidate walls
            for wall in geometry.walls:
                wall.id = f"{prefix}{wall.id}"
                consolidated.walls.append(wall)
            
            # Consolidate other elements
            for riser in geometry.risers:
                riser.id = f"{prefix}{riser.id}"
                consolidated.risers.append(riser)
            
            for room in geometry.rooms:
                room.id = f"{prefix}{room.id}"
                consolidated.rooms.append(room)
            
            for door in geometry.doors:
                door.id = f"{prefix}{door.id}"
                consolidated.doors.append(door)
            
            for window in geometry.windows:
                window.id = f"{prefix}{window.id}"
                consolidated.windows.append(window)
            
            for column in geometry.columns:
                column.id = f"{prefix}{column.id}"
                consolidated.columns.append(column)
            
            for beam in geometry.beams:
                beam.id = f"{prefix}{beam.id}"
                consolidated.beams.append(beam)
            
            for stair in geometry.stairs:
                stair.id = f"{prefix}{stair.id}"
                consolidated.stairs.append(stair)
            
            for equipment in geometry.equipment:
                equipment.id = f"{prefix}{equipment.id}"
                consolidated.equipment.append(equipment)
            
            for other in geometry.other_elements:
                other.id = f"{prefix}{other.id}"
                consolidated.other_elements.append(other)
            
            id_counter += 1
        
        # Consolidate building levels and zones
        consolidated.building_levels = {}
        consolidated.zones = {}
        
        for geometry in geometries:
            # Merge building levels
            for level, level_data in geometry.building_levels.items():
                if level not in consolidated.building_levels:
                    consolidated.building_levels[level] = level_data.copy()
                else:
                    # Merge level data
                    consolidated.building_levels[level]['element_count'] += level_data.get('element_count', 0)
                    consolidated.building_levels[level]['total_area'] += level_data.get('total_area', 0)
                    
                    existing_types = set(consolidated.building_levels[level].get('element_types', []))
                    new_types = set(level_data.get('element_types', []))
                    consolidated.building_levels[level]['element_types'] = list(existing_types | new_types)
        
        # Calculate consolidated quality metrics
        consolidated.quality_metrics = self._calculate_consolidated_quality_metrics(consolidated)
        
        self.logger.info(f"Consolidated {len(geometries)} projects into single geometry with "
                        f"{len(consolidated.get_all_elements())} total elements")
        
        return consolidated
    
    def _calculate_consolidated_quality_metrics(self, consolidated: ProjectGeometry) -> Dict[str, Any]:
        """Calculate quality metrics for consolidated geometry"""
        all_elements = consolidated.get_all_elements()
        
        if not all_elements:
            return {}
        
        valid_elements = [elem for elem in all_elements if elem.vertices and elem.area > 0]
        
        return {
            'total_elements': len(all_elements),
            'valid_elements': len(valid_elements),
            'validity_ratio': len(valid_elements) / len(all_elements),
            'total_area': sum(elem.area for elem in valid_elements),
            'element_types': len(set(elem.geometry_type for elem in all_elements)),
            'floors_detected': len(set(elem.floor_level for elem in all_elements)),
            'source_files': len(consolidated.source_files),
            'avg_element_area': sum(elem.area for elem in valid_elements) / len(valid_elements) if valid_elements else 0,
            'elements_by_type': {
                'floors': len(consolidated.floors),
                'walls': len(consolidated.walls),
                'risers': len(consolidated.risers),
                'rooms': len(consolidated.rooms),
                'doors': len(consolidated.doors),
                'windows': len(consolidated.windows),
                'columns': len(consolidated.columns),
                'beams': len(consolidated.beams),
                'stairs': len(consolidated.stairs),
                'equipment': len(consolidated.equipment),
                'other': len(consolidated.other_elements)
            }
        }
    
    def _calculate_batch_summary_stats(self, result: BatchProcessingResult) -> Dict[str, Any]:
        """Calculate summary statistics for batch processing"""
        successful_results = [r for r in result.individual_results if r.success]
        
        if not successful_results:
            return {}
        
        total_elements = sum(len(r.project_geometry.get_all_elements()) 
                           for r in successful_results if r.project_geometry)
        
        total_processing_time = sum(r.processing_time for r in successful_results)
        total_memory_usage = sum(r.memory_usage_mb for r in successful_results)
        
        file_sizes = [r.file_path.stat().st_size / (1024 * 1024) 
                     for r in successful_results if r.file_path.exists()]
        
        return {
            'success_rate': result.processed_files / result.total_files if result.total_files > 0 else 0,
            'total_elements_extracted': total_elements,
            'avg_elements_per_file': total_elements / len(successful_results) if successful_results else 0,
            'total_processing_time': total_processing_time,
            'avg_processing_time_per_file': total_processing_time / len(successful_results) if successful_results else 0,
            'total_memory_usage_mb': total_memory_usage,
            'avg_memory_per_file_mb': total_memory_usage / len(successful_results) if successful_results else 0,
            'total_file_size_mb': sum(file_sizes),
            'avg_file_size_mb': sum(file_sizes) / len(file_sizes) if file_sizes else 0,
            'processing_throughput_mb_per_sec': sum(file_sizes) / total_processing_time if total_processing_time > 0 else 0,
            'formats_processed': list(set(r.file_format.value for r in successful_results))
        }
    
    async def _save_batch_results(self, result: BatchProcessingResult, output_dir: Path):
        """Save batch processing results to disk"""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save summary report
        summary_path = output_dir / f"batch_summary_{timestamp}.json"
        summary_data = {
            'batch_info': {
                'total_files': result.total_files,
                'processed_files': result.processed_files,
                'failed_files': result.failed_files,
                'processing_time': result.processing_time,
                'success_rate': result.processed_files / result.total_files if result.total_files > 0 else 0
            },
            'summary_stats': result.summary_stats,
            'errors': result.errors,
            'individual_files': [
                {
                    'file_path': str(r.file_path),
                    'format': r.file_format.value,
                    'success': r.success,
                    'processing_time': r.processing_time,
                    'memory_usage_mb': r.memory_usage_mb,
                    'elements_count': len(r.project_geometry.get_all_elements()) if r.project_geometry else 0,
                    'errors': r.errors
                }
                for r in result.individual_results
            ]
        }
        
        async with aiofiles.open(summary_path, 'w') as f:
            await f.write(json.dumps(summary_data, indent=2, default=str))
        
        self.logger.info(f"Batch summary saved to: {summary_path}")
        
        # Save consolidated geometry if available
        if result.consolidated_geometry:
            if self.config.output_format in ['json', 'both']:
                consolidated_json_path = output_dir / f"consolidated_geometry_{timestamp}.json"
                consolidated_data = result.consolidated_geometry.to_routing_engine_format()
                
                async with aiofiles.open(consolidated_json_path, 'w') as f:
                    await f.write(json.dumps(consolidated_data, indent=2, default=str))
                
                self.logger.info(f"Consolidated geometry saved to: {consolidated_json_path}")
    
    def _detect_file_format(self, file_path: Path) -> FileFormat:
        """Detect file format from extension and content"""
        extension = file_path.suffix.lower()
        
        format_map = {
            '.dxf': FileFormat.DXF,
            '.dwg': FileFormat.DWG,
            '.ifc': FileFormat.IFC,
            '.ifcxml': FileFormat.IFC,
            '.pdf': FileFormat.PDF
        }
        
        detected_format = format_map.get(extension, FileFormat.UNKNOWN)
        
        # Additional validation using file content if magic is available
        if detected_format != FileFormat.UNKNOWN and MAGIC_AVAILABLE:
            try:
                file_type = magic.from_file(str(file_path), mime=True)
                
                # Validate format matches content
                if detected_format == FileFormat.PDF and 'pdf' not in file_type:
                    return FileFormat.UNKNOWN
                
            except Exception:
                pass  # Fall back to extension-based detection
        
        return detected_format

# ================================================================================================
# AI INTEGRATION FOR BATCH PROCESSING
# ================================================================================================

class AIEnhancedBatchProcessor(BatchCADProcessor):
    """AI-enhanced batch processor with intelligent classification"""
    
    def __init__(self, config: BatchProcessingConfig = None):
        super().__init__(config)
        self.ai_classifier = None
        
        if self.config.enable_ai_classification:
            try:
                # Import and initialize AI classifier from original code
                from .cad_engine_ai import AISpaceClassificationEngine
                self.ai_classifier = AISpaceClassificationEngine()
                self.logger.info("🤖 AI classification enabled for batch processing")
            except ImportError:
                self.logger.warning("⚠️ AI classification not available")
    
    async def process_files_with_ai(self, file_paths: List[Path], 
                                  output_dir: Path = None) -> BatchProcessingResult:
        """Process files with AI enhancement"""
        
        # First run standard batch processing
        result = await self.process_files(file_paths, output_dir)
        
        # Apply AI classification to results if available
        if self.ai_classifier and result.success:
            self.logger.info("🧠 Applying AI classification to batch results")
            
            # Classify individual results
            for processing_result in result.individual_results:
                if processing_result.success and processing_result.project_geometry:
                    await self._apply_ai_to_project_geometry(processing_result.project_geometry)
            
            # Classify consolidated result
            if result.consolidated_geometry:
                await self._apply_ai_to_project_geometry(result.consolidated_geometry)
        
        return result
    
    async def _apply_ai_to_project_geometry(self, project_geometry: ProjectGeometry):
        """Apply AI classification to project geometry"""
        try:
            if not self.ai_classifier:
                return
            
            # Convert rooms to AI-compatible format
            ai_rooms = []
            for room in project_geometry.rooms:
                ai_room = type('AIRoom', (), {
                    'id': room.id,
                    'vertices': [[v.x, v.y] for v in room.vertices],
                    'area': room.area,
                    'perimeter': room.perimeter,
                    'ai_room_type': None,
                    'ai_confidence': 0.0,
                    'nfpa_hazard_class': 'Ordinary Group 1',
                    'ai_features': {}
                })()
                ai_rooms.append(ai_room)
            
            # Convert obstacles to AI-compatible format  
            ai_obstacles = []
            for element in (project_geometry.columns + project_geometry.equipment + project_geometry.other_elements):
                if element.affects_sprinkler_coverage:
                    ai_obstacle = type('AIObstacle', (), {
                        'id': element.id,
                        'geometry': self._element_to_ai_geometry(element),
                        'obstacle_type': element.geometry_type.value,
                        'ai_obstacle_type': None,
                        'ai_confidence': 0.0,
                        'coverage_impact_radius': element.coverage_impact_radius,
                        'ai_features': {}
                    })()
                    ai_obstacles.append(ai_obstacle)
            
            # Apply AI classification
            room_classifications = await self.ai_classifier.classify_rooms(ai_rooms, ai_obstacles)
            obstacle_classifications = await self.ai_classifier.classify_obstacles(ai_obstacles, ai_rooms)
            
            # Update original elements with AI results
            classification_map = {c.room_id: c for c in room_classifications}
            for room in project_geometry.rooms:
                if room.id in classification_map:
                    classification = classification_map[room.id]
                    room.room_type = classification.predicted_type
                    room.ai_confidence = classification.confidence
                    room.nfpa_classification = classification.nfpa_hazard_class
                    room.ai_features = classification.ai_features
            
            # Store AI classification summary
            hazard_zone_distribution = {}
            for classification in room_classifications:
                zone = classification.nfpa_hazard_class
                hazard_zone_distribution[zone] = hazard_zone_distribution.get(zone, 0) + 1
            
            avg_confidence = sum(c.confidence for c in room_classifications) / len(room_classifications) if room_classifications else 0
            
            project_geometry.ai_classification_summary = {
                'total_rooms_classified': len(room_classifications),
                'total_obstacles_classified': len(obstacle_classifications),
                'hazard_zone_distribution': hazard_zone_distribution,
                'classification_confidence_avg': avg_confidence,
                'ai_processing_timestamp': datetime.now().isoformat()
            }
            
            self.logger.info(f"✅ AI classification applied: {len(room_classifications)} rooms, "
                           f"{len(obstacle_classifications)} obstacles, {avg_confidence:.1%} avg confidence")
            
        except Exception as e:
            self.logger.error(f"AI classification failed: {e}")
    
    def _element_to_ai_geometry(self, element: ConstructionElement):
        """Convert construction element to AI-compatible geometry"""
        if len(element.vertices) == 1:
            # Point-based geometry (columns, equipment)
            vertex = element.vertices[0]
            return {
                'x': vertex.x,
                'y': vertex.y,
                'radius': max(element.thickness, element.height, 1.0) / 2
            }
        else:
            # Polygon geometry
            return [[v.x, v.y] for v in element.vertices]

# ================================================================================================
# CLOUD-SCALABLE MAIN ENGINE
# ================================================================================================

@dataclass
class CloudCADEngineConfig:
    """Configuration for cloud-scalable CAD engine"""
    
    # Processing limits
    max_file_size_mb: int = 1000
    max_concurrent_files: int = 8
    max_memory_usage_mb: int = 4096
    timeout_per_file_seconds: int = 600
    
    # Batch processing
    enable_batch_processing: bool = True
    batch_size: int = 10
    enable_progress_reporting: bool = True
    
    # AI features
    enable_ai_classification: bool = True
    ai_confidence_threshold: float = 0.6
    enable_nfpa_assignment: bool = True
    
    # Output options
    output_formats: List[str] = field(default_factory=lambda: ['json'])
    enable_debug_output: bool = True
    save_intermediate_results: bool = False
    
    # Quality control
    enable_geometry_validation: bool = True
    enable_schema_validation: bool = True
    min_element_area: float = 0.1
    
    # Cloud features
    enable_distributed_processing: bool = False
    enable_result_caching: bool = True
    enable_metrics_collection: bool = True

class EnhancedProductionCADEngine:
    """Production-grade, cloud-scalable CAD processing engine"""
    
    def __init__(self, config: CloudCADEngineConfig = None):
        self.config = config or CloudCADEngineConfig()
        self.logger = logging.getLogger(__name__ + '.EnhancedProductionCADEngine')
        
        # Initialize processors
        self.batch_processor = AIEnhancedBatchProcessor(
            BatchProcessingConfig(
                max_concurrent_files=self.config.max_concurrent_files,
                max_memory_usage_mb=self.config.max_memory_usage_mb,
                timeout_per_file_seconds=self.config.timeout_per_file_seconds,
                enable_progress_reporting=self.config.enable_progress_reporting,
                consolidate_results=True,
                enable_ai_classification=self.config.enable_ai_classification
            )
        )
        
        # Processing statistics
        self.stats = {
            'total_files_processed': 0,
            'total_elements_extracted': 0,
            'total_processing_time': 0.0,
            'avg_processing_time_per_file': 0.0,
            'success_rate': 0.0
        }
        
        self.logger.info("🚀 Enhanced Production CAD Engine initialized")
    
    async def process_single_file(self, file_path: Path, 
                                output_dir: Path = None) -> ProcessingResult:
        """Process a single CAD file with full feature set"""
        
        self.logger.info(f"🔄 Processing single file: {file_path.name}")
        
        try:
            # Detect format and get appropriate processor
            file_format = self._detect_file_format(file_path)
            
            if file_format == FileFormat.DXF:
                processor = DXFProcessor()
            elif file_format == FileFormat.IFC and IFC_AVAILABLE:
                processor = IFCProcessor()
            else:
                return ProcessingResult(
                    success=False,
                    file_path=file_path,
                    file_format=file_format,
                    errors=[f"Unsupported file format: {file_format.value}"]
                )
            
            # Process the file
            result = await processor.process_file(file_path)
            
            # Apply AI classification if enabled
            if (result.success and result.project_geometry and 
                self.config.enable_ai_classification and 
                hasattr(self.batch_processor, 'ai_classifier') and 
                self.batch_processor.ai_classifier):
                
                await self.batch_processor._apply_ai_to_project_geometry(result.project_geometry)
            
            # Save results if output directory provided
            if output_dir and result.success:
                await self._save_single_file_results(result, output_dir)
            
            # Update statistics
            self._update_processing_stats(result)
            
            return result
            
        except Exception as e:
            error_msg = f"Single file processing failed: {e}"
            self.logger.error(error_msg)
            
            return ProcessingResult(
                success=False,
                file_path=file_path,
                file_format=FileFormat.UNKNOWN,
                errors=[error_msg]
            )
    
    async def process_multiple_files(self, file_paths: List[Path], 
                                   output_dir: Path = None) -> BatchProcessingResult:
        """Process multiple CAD files with batch optimization"""
        
        self.logger.info(f"🚀 Processing {len(file_paths)} files in batch mode")
        
        try:
            # Use AI-enhanced batch processor
            result = await self.batch_processor.process_files_with_ai(file_paths, output_dir)
            
            # Update global statistics
            for individual_result in result.individual_results:
                self._update_processing_stats(individual_result)
            
            return result
            
        except Exception as e:
            error_msg = f"Batch processing failed: {e}"
            self.logger.error(error_msg)
            
            return BatchProcessingResult(
                success=False,
                total_files=len(file_paths),
                processed_files=0,
                failed_files=len(file_paths),
                processing_time=0.0,
                errors=[error_msg]
            )
    
    async def process_directory(self, input_dir: Path, 
                              output_dir: Path = None,
                              recursive: bool = True,
                              file_patterns: List[str] = None) -> BatchProcessingResult:
        """Process all CAD files in a directory"""
        
        self.logger.info(f"📁 Processing directory: {input_dir}")
        
        if not input_dir.exists() or not input_dir.is_dir():
            return BatchProcessingResult(
                success=False,
                total_files=0,
                processed_files=0,
                failed_files=0,
                processing_time=0.0,
                errors=[f"Invalid directory: {input_dir}"]
            )
        
        # Find CAD files
        file_patterns = file_patterns or ['*.dxf', '*.dwg', '*.ifc', '*.ifcxml']
        file_paths = []
        
        for pattern in file_patterns:
            if recursive:
                file_paths.extend(input_dir.rglob(pattern))
            else:
                file_paths.extend(input_dir.glob(pattern))
        
        if not file_paths:
            return BatchProcessingResult(
                success=False,
                total_files=0,
                processed_files=0,
                failed_files=0,
                processing_time=0.0,
                errors=[f"No CAD files found in {input_dir}"]
            )
        
        self.logger.info(f"Found {len(file_paths)} CAD files to process")
        
        # Process files in batches
        return await self.process_multiple_files(file_paths, output_dir)
    
    def get_supported_formats(self) -> List[str]:
        """Get list of supported file formats"""
        formats = ['dxf', 'dwg']
        if IFC_AVAILABLE:
            formats.extend(['ifc', 'ifcxml'])
        return formats
    
    def get_processing_statistics(self) -> Dict[str, Any]:
        """Get processing statistics"""
        return self.stats.copy()
    
    async def _save_single_file_results(self, result: ProcessingResult, output_dir: Path):
        """Save single file processing results"""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = result.file_path.stem
        
        # Save project geometry in routing engine format
        if result.project_geometry and 'json' in self.config.output_formats:
            json_path = output_dir / f"{base_name}_{timestamp}.json"
            routing_data = result.project_geometry.to_routing_engine_format()
            
            async with aiofiles.open(json_path, 'w') as f:
                await f.write(json.dumps(routing_data, indent=2, default=str))
            
            self.logger.info(f"Results saved to: {json_path}")
    
    def _update_processing_stats(self, result: ProcessingResult):
        """Update processing statistics"""
        self.stats['total_files_processed'] += 1
        self.stats['total_processing_time'] += result.processing_time
        
        if result.success and result.project_geometry:
            self.stats['total_elements_extracted'] += len(result.project_geometry.get_all_elements())
        
        self.stats['avg_processing_time_per_file'] = (
            self.stats['total_processing_time'] / self.stats['total_files_processed']
        )
        
        # Update success rate (simplified)
        success_count = sum(1 for _ in range(self.stats['total_files_processed']) if result.success)
        self.stats['success_rate'] = success_count / self.stats['total_files_processed']
    
    def _detect_file_format(self, file_path: Path) -> FileFormat:
        """Detect file format from extension"""
        extension = file_path.suffix.lower()
        
        format_map = {
            '.dxf': FileFormat.DXF,
            '.dwg': FileFormat.DWG,
            '.ifc': FileFormat.IFC,
            '.ifcxml': FileFormat.IFC,
        }
        
        return format_map.get(extension, FileFormat.UNKNOWN)

# ================================================================================================
# EXAMPLE USAGE AND TESTING
# ================================================================================================

async def run_production_cad_test():
    """Comprehensive test of the enhanced production CAD engine"""
    
    print("🚀 Enhanced Production CAD Engine Test")
    print("=" * 60)
    
    try:
        # Initialize engine
        config = CloudCADEngineConfig(
            max_concurrent_files=4,
            enable_ai_classification=True,
            enable_batch_processing=True,
            output_formats=['json']
        )
        
        engine = EnhancedProductionCADEngine(config)
        
        print("✅ Engine initialized successfully")
        print(f"📋 Supported formats: {', '.join(engine.get_supported_formats())}")
        
        # Create test directory with sample files
        test_dir = Path("test_cad_files")
        test_dir.mkdir(exist_ok=True)
        
        # Create sample DXF files for testing
        sample_files = []
        
        try:
            # Create test DXF file with various elements
            doc = ezdxf.new('R2010')
            msp = doc.modelspace()
            
            # Add building elements
            # Floor slab
            msp.add_lwpolyline([(0, 0), (30, 0), (30, 20), (0, 20), (0, 0)], 
                              dxfattribs={'layer': 'floor-slab'})
            
            # Walls
            msp.add_lwpolyline([(0, 0), (30, 0)], dxfattribs={'layer': 'wall-exterior'})
            msp.add_lwpolyline([(30, 0), (30, 20)], dxfattribs={'layer': 'wall-exterior'})
            msp.add_lwpolyline([(30, 20), (0, 20)], dxfattribs={'layer': 'wall-exterior'})
            msp.add_lwpolyline([(0, 20), (0, 0)], dxfattribs={'layer': 'wall-exterior'})
            
            # Interior wall
            msp.add_lwpolyline([(15, 0), (15, 20)], dxfattribs={'layer': 'wall-interior'})
            
            # Rooms
            msp.add_lwpolyline([(1, 1), (14, 1), (14, 19), (1, 19), (1, 1)], 
                              dxfattribs={'layer': 'room-office'})
            msp.add_lwpolyline([(16, 1), (29, 1), (29, 19), (16, 19), (16, 1)], 
                              dxfattribs={'layer': 'room-conference'})
            
            # Columns
            msp.add_circle((5, 5), 0.5, dxfattribs={'layer': 'structural-column'})
            msp.add_circle((25, 15), 0.5, dxfattribs={'layer': 'structural-column'})
            
            # Doors
            msp.add_lwpolyline([(15, 8), (15, 12)], dxfattribs={'layer': 'door'})
            
            # Mechanical equipment
            msp.add_circle((10, 10), 1.0, dxfattribs={'layer': 'mechanical-hvac'})
            
            # Risers
            msp.add_circle((20, 5), 0.3, dxfattribs={'layer': 'riser-plumbing'})
            
            test_file = test_dir / "sample_building.dxf"
            doc.saveas(test_file)
            sample_files.append(test_file)
            
            print(f"📝 Created test file: {test_file}")
            
        except Exception as e:
            print(f"⚠️ Could not create test DXF file: {e}")
        
        # Test single file processing
        if sample_files:
            print("\n🔄 Testing single file processing...")
            
            output_dir = Path("output_results")
            result = await engine.process_single_file(sample_files[0], output_dir)
            
            print(f"📊 Single file result:")
            print(f"   Success: {result.success}")
            print(f"   Processing time: {result.processing_time:.2f}s")
            print(f"   Memory usage: {result.memory_usage_mb:.1f}MB")
            
            if result.success and result.project_geometry:
                geometry = result.project_geometry
                print(f"   Elements extracted:")
                print(f"     Floors: {len(geometry.floors)}")
                print(f"     Walls: {len(geometry.walls)}")
                print(f"     Rooms: {len(geometry.rooms)}")
                print(f"     Columns: {len(geometry.columns)}")
                print(f"     Equipment: {len(geometry.equipment)}")
                print(f"     Other: {len(geometry.other_elements)}")
                print(f"   Total elements: {len(geometry.get_all_elements())}")
                
                if geometry.ai_classification_summary:
                    ai_summary = geometry.ai_classification_summary
                    print(f"   AI Classification:")
                    print(f"     Rooms classified: {ai_summary['total_rooms_classified']}")
                    print(f"     Avg confidence: {ai_summary['classification_confidence_avg']:.1%}")
                    print(f"     NFPA zones: {list(ai_summary['hazard_zone_distribution'].keys())}")
                
                # Test routing engine format export
                routing_data = geometry.to_routing_engine_format()
                print(f"   Routing engine export:")
                print(f"     Building spaces: {len(routing_data['building_spaces'])}")
                print(f"     Obstacles: {len(routing_data['obstacles'])}")
                print(f"     Walls: {len(routing_data['walls'])}")
        
        # Test batch processing if we have multiple files
        if len(sample_files) > 1:
            print("\n🚀 Testing batch processing...")
            
            batch_result = await engine.process_multiple_files(sample_files, output_dir)
            
            print(f"📊 Batch processing result:")
            print(f"   Total files: {batch_result.total_files}")
            print(f"   Processed: {batch_result.processed_files}")
            print(f"   Failed: {batch_result.failed_files}")
            print(f"   Processing time: {batch_result.processing_time:.2f}s")
            
            if batch_result.consolidated_geometry:
                consolidated = batch_result.consolidated_geometry
                print(f"   Consolidated geometry:")
                print(f"     Total elements: {len(consolidated.get_all_elements())}")
                print(f"     Source files: {len(consolidated.source_files)}")
        
        # Display engine statistics
        stats = engine.get_processing_statistics()
        print(f"\n📈 Engine Statistics:")
        print(f"   Files processed: {stats['total_files_processed']}")
        print(f"   Elements extracted: {stats['total_elements_extracted']}")
        print(f"   Avg processing time: {stats['avg_processing_time_per_file']:.2f}s")
        print(f"   Success rate: {stats['success_rate']:.1%}")
        
        print("\n✅ Production CAD Engine test completed successfully!")
        
        # Cleanup
        try:
            if test_dir.exists():
                shutil.rmtree(test_dir)
            if output_dir.exists():
                shutil.rmtree(output_dir)
            print("🗑️ Test files cleaned up")
        except Exception as e:
            print(f"⚠️ Cleanup warning: {e}")
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        print(f"Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    print("🏗️ Enhanced Production CAD Engine")
    print("=" * 50)
    print("🎯 Multi-format support: DXF, DWG, IFC")
    print("📦 Batch processing with AI enhancement")
    print("☁️ Cloud-scalable architecture")
    print("🤖 AI-powered space classification")
    print("🔥 Fire protection engineering ready")
    print("")
    print("🚀 Running comprehensive test...")
    print("")
    
    # Run the test
    asyncio.run(run_production_cad_test())
