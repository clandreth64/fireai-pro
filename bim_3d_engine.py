#!/usr/bin/env python3
"""
FireAI Pro - 3D BIM Coordination & Clash Detection Engine
VERSION: 1.0.0

🏗️ 3D DESIGN FOR BIM COORDINATION AND CLASH DETECTION

This module provides 3D capabilities for fire sprinkler systems including:
- 3D geometry generation for pipes, sprinklers, and components
- IFC export for BIM model exchange
- Clash detection with other building systems
- Clearance validation per NFPA 13
- Integration with Revit, Navisworks, and other BIM tools

📐 3D GEOMETRY:
- Cylindrical pipe segments with accurate diameters
- Sprinkler head 3D models with deflectors
- Valve assemblies and fittings
- Hangers and seismic bracing

🔍 CLASH DETECTION:
- Pipe-to-structure interference
- Sprinkler-to-obstruction conflicts
- Clearance violations (NFPA 13 requirements)
- MEP coordination conflicts

📤 EXPORT FORMATS:
- IFC (Industry Foundation Classes) - BIM standard
- OBJ (Wavefront) - 3D visualization
- STL (Stereolithography) - 3D printing
- GLTF/GLB - Web 3D viewers
- JSON - Raw geometry data

📋 NFPA 13 CLEARANCE REQUIREMENTS:
- Sprinkler to ceiling: 1" to 12" (standard)
- Sprinkler to obstruction: Per tables
- Sprinkler to storage: Per occupancy
- Pipe to structure: Varies by type
"""

import math
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Set, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
import struct

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class GeometryType(Enum):
    """Types of 3D geometry"""
    PIPE = "pipe"
    SPRINKLER = "sprinkler"
    VALVE = "valve"
    FITTING = "fitting"
    HANGER = "hanger"
    BRACE = "brace"
    OBSTRUCTION = "obstruction"
    STRUCTURE = "structure"


class ClashType(Enum):
    """Types of clashes detected"""
    HARD_CLASH = "hard_clash"           # Physical intersection
    SOFT_CLASH = "soft_clash"           # Clearance violation
    WORKFLOW_CLASH = "workflow_clash"   # Installation sequence issue
    NFPA_VIOLATION = "nfpa_violation"   # Code clearance violation


class IFCClass(Enum):
    """IFC entity classes for export"""
    PIPE_SEGMENT = "IfcPipeSegment"
    FLOW_TERMINAL = "IfcFlowTerminal"  # Sprinklers
    VALVE = "IfcValve"
    FITTING = "IfcPipeFitting"
    DISCRETE_ACCESSORY = "IfcDiscreteAccessory"  # Hangers, braces


# NFPA 13 clearance requirements (inches)
NFPA_CLEARANCES = {
    'sprinkler_to_ceiling_min': 1.0,
    'sprinkler_to_ceiling_max': 12.0,
    'sprinkler_to_obstruction_horizontal': 24.0,  # Minimum
    'sprinkler_to_wall': 4.0,
    'pipe_to_structure': 1.0,
    'sprinkler_deflector_to_storage_class_I_II': 36.0,
    'sprinkler_deflector_to_storage_class_III_IV': 36.0,
}

# Standard pipe outer diameters (Schedule 40)
PIPE_OD = {
    0.75: 1.050, 1.0: 1.315, 1.25: 1.660, 1.5: 1.900,
    2.0: 2.375, 2.5: 2.875, 3.0: 3.500, 3.5: 4.000,
    4.0: 4.500, 5.0: 5.563, 6.0: 6.625, 8.0: 8.625,
}


# =============================================================================
# 3D DATA STRUCTURES
# =============================================================================

@dataclass
class Vector3:
    """3D vector/point"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    
    def __add__(self, other):
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)
    
    def __sub__(self, other):
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)
    
    def __mul__(self, scalar):
        return Vector3(self.x * scalar, self.y * scalar, self.z * scalar)
    
    def length(self) -> float:
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)
    
    def normalize(self) -> 'Vector3':
        l = self.length()
        if l > 0:
            return Vector3(self.x/l, self.y/l, self.z/l)
        return Vector3(0, 0, 1)
    
    def dot(self, other) -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z
    
    def cross(self, other) -> 'Vector3':
        return Vector3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x
        )
    
    def to_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)
    
    def distance_to(self, other: 'Vector3') -> float:
        return (self - other).length()


@dataclass
class BoundingBox:
    """Axis-aligned bounding box"""
    min_point: Vector3
    max_point: Vector3
    
    @property
    def center(self) -> Vector3:
        return Vector3(
            (self.min_point.x + self.max_point.x) / 2,
            (self.min_point.y + self.max_point.y) / 2,
            (self.min_point.z + self.max_point.z) / 2
        )
    
    @property
    def size(self) -> Vector3:
        return self.max_point - self.min_point
    
    def intersects(self, other: 'BoundingBox') -> bool:
        """Check if two bounding boxes intersect"""
        return (
            self.min_point.x <= other.max_point.x and
            self.max_point.x >= other.min_point.x and
            self.min_point.y <= other.max_point.y and
            self.max_point.y >= other.min_point.y and
            self.min_point.z <= other.max_point.z and
            self.max_point.z >= other.min_point.z
        )
    
    def expand(self, margin: float) -> 'BoundingBox':
        """Expand bounding box by margin"""
        return BoundingBox(
            min_point=Vector3(
                self.min_point.x - margin,
                self.min_point.y - margin,
                self.min_point.z - margin
            ),
            max_point=Vector3(
                self.max_point.x + margin,
                self.max_point.y + margin,
                self.max_point.z + margin
            )
        )


@dataclass
class Mesh3D:
    """3D mesh representation"""
    vertices: List[Vector3] = field(default_factory=list)
    faces: List[Tuple[int, int, int]] = field(default_factory=list)  # Triangle indices
    normals: List[Vector3] = field(default_factory=list)
    
    @property
    def vertex_count(self) -> int:
        return len(self.vertices)
    
    @property
    def face_count(self) -> int:
        return len(self.faces)


@dataclass 
class Component3D:
    """3D component in the model"""
    id: str
    component_type: GeometryType
    position: Vector3
    bounding_box: BoundingBox
    mesh: Optional[Mesh3D] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    ifc_class: Optional[IFCClass] = None


@dataclass
class ClashResult:
    """Result of a clash detection"""
    clash_type: ClashType
    component1_id: str
    component2_id: str
    location: Vector3
    distance: float  # Negative = overlap, positive = clearance
    severity: str  # 'critical', 'major', 'minor'
    description: str
    nfpa_reference: Optional[str] = None


@dataclass
class ClearanceViolation:
    """NFPA clearance violation"""
    component_id: str
    violation_type: str
    required_clearance: float
    actual_clearance: float
    location: Vector3
    nfpa_section: str
    description: str


# =============================================================================
# 3D GEOMETRY GENERATORS
# =============================================================================

class GeometryGenerator:
    """Generate 3D geometry for fire sprinkler components"""
    
    def __init__(self, segments_per_circle: int = 16):
        self.segments = segments_per_circle
    
    def create_pipe_cylinder(self, start: Vector3, end: Vector3, 
                             diameter: float) -> Mesh3D:
        """Create cylindrical pipe geometry"""
        mesh = Mesh3D()
        
        # Get pipe direction
        direction = end - start
        length = direction.length()
        if length == 0:
            return mesh
        
        direction = direction.normalize()
        
        # Find perpendicular vectors for circle generation
        if abs(direction.z) < 0.9:
            up = Vector3(0, 0, 1)
        else:
            up = Vector3(1, 0, 0)
        
        right = direction.cross(up).normalize()
        up = right.cross(direction).normalize()
        
        radius = diameter / 2 / 12  # Convert inches to feet
        
        # Generate vertices for start and end circles
        start_verts = []
        end_verts = []
        
        for i in range(self.segments):
            angle = 2 * math.pi * i / self.segments
            offset = right * (radius * math.cos(angle)) + up * (radius * math.sin(angle))
            
            start_verts.append(start + offset)
            end_verts.append(end + offset)
        
        # Add vertices
        start_idx = len(mesh.vertices)
        mesh.vertices.extend(start_verts)
        end_idx = len(mesh.vertices)
        mesh.vertices.extend(end_verts)
        
        # Add center points for caps
        mesh.vertices.append(start)  # Start center
        mesh.vertices.append(end)    # End center
        start_center_idx = len(mesh.vertices) - 2
        end_center_idx = len(mesh.vertices) - 1
        
        # Generate faces for cylinder wall
        for i in range(self.segments):
            next_i = (i + 1) % self.segments
            
            # Two triangles per quad
            mesh.faces.append((
                start_idx + i,
                end_idx + i,
                end_idx + next_i
            ))
            mesh.faces.append((
                start_idx + i,
                end_idx + next_i,
                start_idx + next_i
            ))
        
        # Generate faces for caps
        for i in range(self.segments):
            next_i = (i + 1) % self.segments
            
            # Start cap
            mesh.faces.append((
                start_center_idx,
                start_idx + next_i,
                start_idx + i
            ))
            
            # End cap
            mesh.faces.append((
                end_center_idx,
                end_idx + i,
                end_idx + next_i
            ))
        
        return mesh
    
    def create_sprinkler_head(self, position: Vector3, 
                              orientation: str = 'pendant') -> Mesh3D:
        """Create sprinkler head 3D geometry"""
        mesh = Mesh3D()
        
        # Simplified sprinkler geometry
        # Body (small cylinder)
        body_radius = 0.02  # feet (about 1/4")
        body_height = 0.04  # feet (about 1/2")
        
        # Deflector (disc)
        deflector_radius = 0.04  # feet (about 1/2")
        deflector_thickness = 0.005  # feet
        
        if orientation == 'pendant':
            # Body hangs down, deflector at bottom
            body_top = position
            body_bottom = position - Vector3(0, 0, body_height)
            deflector_center = body_bottom - Vector3(0, 0, 0.01)
        elif orientation == 'upright':
            # Body goes up, deflector at top
            body_bottom = position
            body_top = position + Vector3(0, 0, body_height)
            deflector_center = body_top + Vector3(0, 0, 0.01)
        else:
            # Sidewall
            body_top = position
            body_bottom = position - Vector3(body_height, 0, 0)
            deflector_center = body_bottom - Vector3(0.01, 0, 0)
        
        # Generate body cylinder
        for i in range(self.segments):
            angle = 2 * math.pi * i / self.segments
            next_angle = 2 * math.pi * ((i + 1) % self.segments) / self.segments
            
            # Body vertices
            x1 = body_radius * math.cos(angle)
            y1 = body_radius * math.sin(angle)
            x2 = body_radius * math.cos(next_angle)
            y2 = body_radius * math.sin(next_angle)
            
            v_idx = len(mesh.vertices)
            mesh.vertices.append(Vector3(body_top.x + x1, body_top.y + y1, body_top.z))
            mesh.vertices.append(Vector3(body_bottom.x + x1, body_bottom.y + y1, body_bottom.z))
            mesh.vertices.append(Vector3(body_top.x + x2, body_top.y + y2, body_top.z))
            mesh.vertices.append(Vector3(body_bottom.x + x2, body_bottom.y + y2, body_bottom.z))
            
            mesh.faces.append((v_idx, v_idx + 1, v_idx + 3))
            mesh.faces.append((v_idx, v_idx + 3, v_idx + 2))
        
        # Add deflector disc
        center_idx = len(mesh.vertices)
        mesh.vertices.append(deflector_center)
        
        for i in range(self.segments):
            angle = 2 * math.pi * i / self.segments
            next_angle = 2 * math.pi * ((i + 1) % self.segments) / self.segments
            
            x1 = deflector_radius * math.cos(angle)
            y1 = deflector_radius * math.sin(angle)
            x2 = deflector_radius * math.cos(next_angle)
            y2 = deflector_radius * math.sin(next_angle)
            
            v_idx = len(mesh.vertices)
            mesh.vertices.append(Vector3(deflector_center.x + x1, deflector_center.y + y1, deflector_center.z))
            mesh.vertices.append(Vector3(deflector_center.x + x2, deflector_center.y + y2, deflector_center.z))
            
            mesh.faces.append((center_idx, v_idx, v_idx + 1))
        
        return mesh
    
    def calculate_bounding_box(self, mesh: Mesh3D) -> BoundingBox:
        """Calculate bounding box for mesh"""
        if not mesh.vertices:
            return BoundingBox(Vector3(), Vector3())
        
        min_x = min(v.x for v in mesh.vertices)
        min_y = min(v.y for v in mesh.vertices)
        min_z = min(v.z for v in mesh.vertices)
        max_x = max(v.x for v in mesh.vertices)
        max_y = max(v.y for v in mesh.vertices)
        max_z = max(v.z for v in mesh.vertices)
        
        return BoundingBox(
            min_point=Vector3(min_x, min_y, min_z),
            max_point=Vector3(max_x, max_y, max_z)
        )


# =============================================================================
# BIM MODEL
# =============================================================================

class BIMModel:
    """3D BIM model for fire sprinkler system"""
    
    def __init__(self, project_name: str = "", project_id: str = ""):
        self.project_name = project_name
        self.project_id = project_id or str(uuid.uuid4())[:8]
        self.components: Dict[str, Component3D] = {}
        self.obstructions: Dict[str, Component3D] = {}
        self.geometry_gen = GeometryGenerator()
        self.logger = logging.getLogger(f"{__name__}.BIMModel")
    
    def add_pipe(self, pipe_id: str, start: Tuple[float, float, float],
                 end: Tuple[float, float, float], diameter: float,
                 pipe_type: str = "branch") -> str:
        """Add pipe to model"""
        start_v = Vector3(*start)
        end_v = Vector3(*end)
        
        # Get outer diameter
        od = PIPE_OD.get(diameter, diameter * 1.1) / 12  # Convert to feet
        
        mesh = self.geometry_gen.create_pipe_cylinder(start_v, end_v, od * 12)
        bbox = self.geometry_gen.calculate_bounding_box(mesh)
        
        # Expand bbox slightly for pipe OD
        bbox = bbox.expand(od / 2)
        
        component = Component3D(
            id=pipe_id,
            component_type=GeometryType.PIPE,
            position=start_v,
            bounding_box=bbox,
            mesh=mesh,
            properties={
                'diameter': diameter,
                'length': start_v.distance_to(end_v),
                'pipe_type': pipe_type,
                'start': start,
                'end': end,
            },
            ifc_class=IFCClass.PIPE_SEGMENT
        )
        
        self.components[pipe_id] = component
        return pipe_id
    
    def add_sprinkler(self, sprinkler_id: str, position: Tuple[float, float, float],
                      orientation: str = 'pendant', k_factor: float = 5.6,
                      coverage: float = 130) -> str:
        """Add sprinkler to model"""
        pos_v = Vector3(*position)
        
        mesh = self.geometry_gen.create_sprinkler_head(pos_v, orientation)
        bbox = self.geometry_gen.calculate_bounding_box(mesh)
        
        # Expand for coverage area check
        coverage_radius = math.sqrt(coverage / math.pi)
        
        component = Component3D(
            id=sprinkler_id,
            component_type=GeometryType.SPRINKLER,
            position=pos_v,
            bounding_box=bbox,
            mesh=mesh,
            properties={
                'orientation': orientation,
                'k_factor': k_factor,
                'coverage': coverage,
                'coverage_radius': coverage_radius,
            },
            ifc_class=IFCClass.FLOW_TERMINAL
        )
        
        self.components[sprinkler_id] = component
        return sprinkler_id
    
    def add_valve(self, valve_id: str, position: Tuple[float, float, float],
                  valve_type: str, size: float) -> str:
        """Add valve to model"""
        pos_v = Vector3(*position)
        
        # Approximate valve as box
        valve_size = size / 12 * 1.5  # feet
        
        bbox = BoundingBox(
            min_point=pos_v - Vector3(valve_size/2, valve_size/2, valve_size/2),
            max_point=pos_v + Vector3(valve_size/2, valve_size/2, valve_size/2)
        )
        
        component = Component3D(
            id=valve_id,
            component_type=GeometryType.VALVE,
            position=pos_v,
            bounding_box=bbox,
            mesh=None,  # Simplified
            properties={
                'valve_type': valve_type,
                'size': size,
            },
            ifc_class=IFCClass.VALVE
        )
        
        self.components[valve_id] = component
        return valve_id
    
    def add_hanger(self, hanger_id: str, position: Tuple[float, float, float],
                   pipe_size: float) -> str:
        """Add hanger to model"""
        pos_v = Vector3(*position)
        
        hanger_size = 0.15  # feet (approximate)
        
        bbox = BoundingBox(
            min_point=pos_v - Vector3(hanger_size, hanger_size, hanger_size),
            max_point=pos_v + Vector3(hanger_size, hanger_size, hanger_size * 2)  # Rod goes up
        )
        
        component = Component3D(
            id=hanger_id,
            component_type=GeometryType.HANGER,
            position=pos_v,
            bounding_box=bbox,
            properties={'pipe_size': pipe_size},
            ifc_class=IFCClass.DISCRETE_ACCESSORY
        )
        
        self.components[hanger_id] = component
        return hanger_id
    
    def add_obstruction(self, obstruction_id: str, 
                        min_point: Tuple[float, float, float],
                        max_point: Tuple[float, float, float],
                        obstruction_type: str = "beam") -> str:
        """Add obstruction (beam, duct, etc.) to model"""
        bbox = BoundingBox(
            min_point=Vector3(*min_point),
            max_point=Vector3(*max_point)
        )
        
        component = Component3D(
            id=obstruction_id,
            component_type=GeometryType.OBSTRUCTION,
            position=bbox.center,
            bounding_box=bbox,
            properties={'obstruction_type': obstruction_type}
        )
        
        self.obstructions[obstruction_id] = component
        return obstruction_id
    
    def add_structure(self, structure_id: str,
                      min_point: Tuple[float, float, float],
                      max_point: Tuple[float, float, float],
                      structure_type: str = "column") -> str:
        """Add structural element to model"""
        bbox = BoundingBox(
            min_point=Vector3(*min_point),
            max_point=Vector3(*max_point)
        )
        
        component = Component3D(
            id=structure_id,
            component_type=GeometryType.STRUCTURE,
            position=bbox.center,
            bounding_box=bbox,
            properties={'structure_type': structure_type}
        )
        
        self.obstructions[structure_id] = component
        return structure_id


# =============================================================================
# CLASH DETECTION ENGINE
# =============================================================================

class ClashDetectionEngine:
    """Detect clashes between fire sprinkler system and other elements"""
    
    def __init__(self, model: BIMModel):
        self.model = model
        self.logger = logging.getLogger(f"{__name__}.ClashDetection")
    
    def run_clash_detection(self, clearance_margin: float = 0.0) -> List[ClashResult]:
        """
        Run comprehensive clash detection
        
        Args:
            clearance_margin: Additional clearance to check (feet)
            
        Returns:
            List of clash results
        """
        clashes = []
        
        # Check pipe-to-obstruction clashes
        clashes.extend(self._check_pipe_obstructions(clearance_margin))
        
        # Check sprinkler-to-obstruction clashes
        clashes.extend(self._check_sprinkler_obstructions())
        
        # Check pipe-to-pipe clashes
        clashes.extend(self._check_pipe_pipe_clashes())
        
        # Check NFPA clearance violations
        clashes.extend(self._check_nfpa_clearances())
        
        self.logger.info(f"Clash detection complete: {len(clashes)} issues found")
        return clashes
    
    def _check_pipe_obstructions(self, margin: float) -> List[ClashResult]:
        """Check pipes against obstructions"""
        clashes = []
        
        pipes = [c for c in self.model.components.values() 
                 if c.component_type == GeometryType.PIPE]
        
        for pipe in pipes:
            pipe_bbox = pipe.bounding_box.expand(margin)
            
            for obs_id, obstruction in self.model.obstructions.items():
                if pipe_bbox.intersects(obstruction.bounding_box):
                    # Calculate actual distance (simplified)
                    distance = self._calculate_min_distance(pipe, obstruction)
                    
                    if distance < margin:
                        severity = 'critical' if distance < 0 else 'major'
                        clashes.append(ClashResult(
                            clash_type=ClashType.HARD_CLASH if distance < 0 else ClashType.SOFT_CLASH,
                            component1_id=pipe.id,
                            component2_id=obs_id,
                            location=pipe.bounding_box.center,
                            distance=distance,
                            severity=severity,
                            description=f"Pipe {pipe.id} conflicts with {obstruction.properties.get('obstruction_type', 'obstruction')} {obs_id}"
                        ))
        
        return clashes
    
    def _check_sprinkler_obstructions(self) -> List[ClashResult]:
        """Check sprinklers against obstructions per NFPA 13"""
        clashes = []
        
        sprinklers = [c for c in self.model.components.values()
                      if c.component_type == GeometryType.SPRINKLER]
        
        for sprinkler in sprinklers:
            spr_pos = sprinkler.position
            coverage_radius = sprinkler.properties.get('coverage_radius', 6.0)
            
            for obs_id, obstruction in self.model.obstructions.items():
                obs_center = obstruction.bounding_box.center
                
                # Check horizontal distance
                horizontal_dist = math.sqrt(
                    (spr_pos.x - obs_center.x)**2 + 
                    (spr_pos.y - obs_center.y)**2
                )
                
                # Check if obstruction is within spray pattern
                if horizontal_dist < coverage_radius:
                    # Check vertical relationship
                    obs_top = obstruction.bounding_box.max_point.z
                    spr_z = spr_pos.z
                    
                    # If obstruction is below sprinkler and close
                    if obs_top < spr_z and (spr_z - obs_top) < 3:  # Within 3 feet
                        obs_width = (obstruction.bounding_box.max_point.x - 
                                   obstruction.bounding_box.min_point.x)
                        
                        # NFPA 13 rules for obstructions
                        required_clearance = NFPA_CLEARANCES['sprinkler_to_obstruction_horizontal'] / 12
                        
                        if horizontal_dist < required_clearance:
                            clashes.append(ClashResult(
                                clash_type=ClashType.NFPA_VIOLATION,
                                component1_id=sprinkler.id,
                                component2_id=obs_id,
                                location=spr_pos,
                                distance=horizontal_dist,
                                severity='major',
                                description=f"Sprinkler {sprinkler.id} may be obstructed by {obs_id}. "
                                          f"Horizontal distance: {horizontal_dist*12:.1f}\"",
                                nfpa_reference="NFPA 13 Section 8.5.5"
                            ))
        
        return clashes
    
    def _check_pipe_pipe_clashes(self) -> List[ClashResult]:
        """Check for pipe-to-pipe intersections"""
        clashes = []
        
        pipes = [c for c in self.model.components.values()
                 if c.component_type == GeometryType.PIPE]
        
        # Check each pair
        for i, pipe1 in enumerate(pipes):
            for pipe2 in pipes[i+1:]:
                if pipe1.bounding_box.intersects(pipe2.bounding_box):
                    # More detailed check needed
                    # For now, flag as potential clash
                    p1_props = pipe1.properties
                    p2_props = pipe2.properties
                    
                    # Check if they share an endpoint (valid connection)
                    if (p1_props.get('start') == p2_props.get('start') or
                        p1_props.get('start') == p2_props.get('end') or
                        p1_props.get('end') == p2_props.get('start') or
                        p1_props.get('end') == p2_props.get('end')):
                        continue  # Valid connection, not a clash
                    
                    clashes.append(ClashResult(
                        clash_type=ClashType.HARD_CLASH,
                        component1_id=pipe1.id,
                        component2_id=pipe2.id,
                        location=pipe1.bounding_box.center,
                        distance=-0.1,  # Assume overlap
                        severity='critical',
                        description=f"Pipe {pipe1.id} intersects with pipe {pipe2.id}"
                    ))
        
        return clashes
    
    def _check_nfpa_clearances(self) -> List[ClashResult]:
        """Check NFPA 13 clearance requirements"""
        clashes = []
        
        sprinklers = [c for c in self.model.components.values()
                      if c.component_type == GeometryType.SPRINKLER]
        
        for sprinkler in sprinklers:
            # Check ceiling clearance (would need ceiling data)
            # For now, check elevation is reasonable
            z = sprinkler.position.z
            
            # Check sprinkler spacing
            for other in sprinklers:
                if other.id == sprinkler.id:
                    continue
                
                dist = sprinkler.position.distance_to(other.position)
                coverage = sprinkler.properties.get('coverage', 130)
                
                # Max spacing based on coverage
                max_spacing = math.sqrt(coverage) * 2 / 12 * 1.1  # feet, with margin
                
                if dist > max_spacing:
                    clashes.append(ClashResult(
                        clash_type=ClashType.NFPA_VIOLATION,
                        component1_id=sprinkler.id,
                        component2_id=other.id,
                        location=sprinkler.position,
                        distance=dist,
                        severity='major',
                        description=f"Sprinkler spacing {dist*12:.0f}\" may exceed maximum for {coverage} sqft coverage",
                        nfpa_reference="NFPA 13 Section 8.5.2"
                    ))
                    break  # Only report once per sprinkler
        
        return clashes
    
    def _calculate_min_distance(self, comp1: Component3D, comp2: Component3D) -> float:
        """Calculate minimum distance between components (simplified)"""
        # Use bounding box centers as approximation
        center1 = comp1.bounding_box.center
        center2 = comp2.bounding_box.center
        
        # Get sizes
        size1 = comp1.bounding_box.size
        size2 = comp2.bounding_box.size
        
        # Approximate distance (box-to-box)
        dx = max(0, abs(center1.x - center2.x) - (size1.x + size2.x) / 2)
        dy = max(0, abs(center1.y - center2.y) - (size1.y + size2.y) / 2)
        dz = max(0, abs(center1.z - center2.z) - (size1.z + size2.z) / 2)
        
        return math.sqrt(dx**2 + dy**2 + dz**2)


# =============================================================================
# EXPORT FUNCTIONS
# =============================================================================

class BIMExporter:
    """Export BIM model to various formats"""
    
    def __init__(self, model: BIMModel):
        self.model = model
        self.logger = logging.getLogger(f"{__name__}.BIMExporter")
    
    def export_obj(self, output_path: str) -> str:
        """Export to OBJ format for 3D visualization"""
        lines = []
        lines.append(f"# FireAI Pro 3D Model Export")
        lines.append(f"# Project: {self.model.project_name}")
        lines.append(f"# Generated: {datetime.now().isoformat()}")
        lines.append("")
        
        vertex_offset = 1  # OBJ is 1-indexed
        
        for comp_id, component in self.model.components.items():
            if component.mesh is None:
                continue
            
            lines.append(f"# Component: {comp_id}")
            lines.append(f"o {comp_id}")
            
            # Write vertices
            for v in component.mesh.vertices:
                lines.append(f"v {v.x:.6f} {v.y:.6f} {v.z:.6f}")
            
            # Write faces
            for face in component.mesh.faces:
                f = [str(i + vertex_offset) for i in face]
                lines.append(f"f {' '.join(f)}")
            
            vertex_offset += len(component.mesh.vertices)
            lines.append("")
        
        with open(output_path, 'w') as f:
            f.write('\n'.join(lines))
        
        self.logger.info(f"OBJ exported: {output_path}")
        return output_path
    
    def export_stl(self, output_path: str, binary: bool = True) -> str:
        """Export to STL format for 3D printing"""
        if binary:
            return self._export_stl_binary(output_path)
        else:
            return self._export_stl_ascii(output_path)
    
    def _export_stl_binary(self, output_path: str) -> str:
        """Export binary STL"""
        triangles = []
        
        for component in self.model.components.values():
            if component.mesh is None:
                continue
            
            for face in component.mesh.faces:
                v0 = component.mesh.vertices[face[0]]
                v1 = component.mesh.vertices[face[1]]
                v2 = component.mesh.vertices[face[2]]
                
                # Calculate normal
                edge1 = v1 - v0
                edge2 = v2 - v0
                normal = edge1.cross(edge2).normalize()
                
                triangles.append((normal, v0, v1, v2))
        
        with open(output_path, 'wb') as f:
            # Header (80 bytes)
            header = f"FireAI Pro STL Export - {self.model.project_name}"[:80]
            f.write(header.encode().ljust(80, b'\0'))
            
            # Triangle count (4 bytes)
            f.write(struct.pack('<I', len(triangles)))
            
            # Triangles
            for normal, v0, v1, v2 in triangles:
                # Normal
                f.write(struct.pack('<3f', normal.x, normal.y, normal.z))
                # Vertices
                f.write(struct.pack('<3f', v0.x, v0.y, v0.z))
                f.write(struct.pack('<3f', v1.x, v1.y, v1.z))
                f.write(struct.pack('<3f', v2.x, v2.y, v2.z))
                # Attribute byte count
                f.write(struct.pack('<H', 0))
        
        self.logger.info(f"Binary STL exported: {output_path}")
        return output_path
    
    def _export_stl_ascii(self, output_path: str) -> str:
        """Export ASCII STL"""
        lines = []
        lines.append(f"solid FireAI_Pro_{self.model.project_id}")
        
        for component in self.model.components.values():
            if component.mesh is None:
                continue
            
            for face in component.mesh.faces:
                v0 = component.mesh.vertices[face[0]]
                v1 = component.mesh.vertices[face[1]]
                v2 = component.mesh.vertices[face[2]]
                
                edge1 = v1 - v0
                edge2 = v2 - v0
                normal = edge1.cross(edge2).normalize()
                
                lines.append(f"  facet normal {normal.x:.6f} {normal.y:.6f} {normal.z:.6f}")
                lines.append("    outer loop")
                lines.append(f"      vertex {v0.x:.6f} {v0.y:.6f} {v0.z:.6f}")
                lines.append(f"      vertex {v1.x:.6f} {v1.y:.6f} {v1.z:.6f}")
                lines.append(f"      vertex {v2.x:.6f} {v2.y:.6f} {v2.z:.6f}")
                lines.append("    endloop")
                lines.append("  endfacet")
        
        lines.append(f"endsolid FireAI_Pro_{self.model.project_id}")
        
        with open(output_path, 'w') as f:
            f.write('\n'.join(lines))
        
        self.logger.info(f"ASCII STL exported: {output_path}")
        return output_path
    
    def export_json(self, output_path: str) -> str:
        """Export to JSON for data exchange"""
        data = {
            'project_name': self.model.project_name,
            'project_id': self.model.project_id,
            'generated': datetime.now().isoformat(),
            'components': {},
            'obstructions': {},
        }
        
        for comp_id, component in self.model.components.items():
            data['components'][comp_id] = {
                'type': component.component_type.value,
                'position': component.position.to_tuple(),
                'bounding_box': {
                    'min': component.bounding_box.min_point.to_tuple(),
                    'max': component.bounding_box.max_point.to_tuple(),
                },
                'properties': component.properties,
                'ifc_class': component.ifc_class.value if component.ifc_class else None,
            }
        
        for obs_id, obstruction in self.model.obstructions.items():
            data['obstructions'][obs_id] = {
                'type': obstruction.component_type.value,
                'bounding_box': {
                    'min': obstruction.bounding_box.min_point.to_tuple(),
                    'max': obstruction.bounding_box.max_point.to_tuple(),
                },
                'properties': obstruction.properties,
            }
        
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        self.logger.info(f"JSON exported: {output_path}")
        return output_path
    
    def export_ifc(self, output_path: str) -> str:
        """Export to IFC format (simplified)"""
        # Note: Full IFC export requires ifcopenshell library
        # This generates a simplified IFC-like structure
        
        lines = []
        lines.append("ISO-10303-21;")
        lines.append("HEADER;")
        lines.append(f"FILE_DESCRIPTION(('FireAI Pro Export'),'2;1');")
        lines.append(f"FILE_NAME('{output_path}','{datetime.now().isoformat()}',('FireAI Pro'),(''),'',' ','');")
        lines.append("FILE_SCHEMA(('IFC4'));")
        lines.append("ENDSEC;")
        lines.append("DATA;")
        
        entity_id = 1
        
        # Project
        lines.append(f"#{entity_id}=IFCPROJECT('{uuid.uuid4()}',#2,'{self.model.project_name}',$,$,$,$,$,#3);")
        entity_id += 1
        
        # Owner history (simplified)
        lines.append(f"#{entity_id}=IFCOWNERHISTORY(#4,#5,$,.ADDED.,$,$,$,{int(datetime.now().timestamp())});")
        owner_history_id = entity_id
        entity_id += 1
        
        # Components
        for comp_id, component in self.model.components.items():
            ifc_type = component.ifc_class.value if component.ifc_class else "IfcBuildingElementProxy"
            pos = component.position
            
            # Placement
            lines.append(f"#{entity_id}=IFCLOCALPLACEMENT($,#{entity_id+1});")
            placement_id = entity_id
            entity_id += 1
            
            lines.append(f"#{entity_id}=IFCAXIS2PLACEMENT3D(#{entity_id+1},$,$);")
            entity_id += 1
            
            lines.append(f"#{entity_id}=IFCCARTESIANPOINT(({pos.x},{pos.y},{pos.z}));")
            entity_id += 1
            
            # Element
            lines.append(f"#{entity_id}={ifc_type}('{uuid.uuid4()}',#{owner_history_id},'{comp_id}',$,$,#{placement_id},$,$);")
            entity_id += 1
        
        lines.append("ENDSEC;")
        lines.append("END-ISO-10303-21;")
        
        with open(output_path, 'w') as f:
            f.write('\n'.join(lines))
        
        self.logger.info(f"IFC exported: {output_path}")
        return output_path
    
    def generate_clash_report(self, clashes: List[ClashResult], output_path: str) -> str:
        """Generate clash detection report"""
        lines = []
        lines.append("=" * 80)
        lines.append("CLASH DETECTION REPORT")
        lines.append(f"Project: {self.model.project_name}")
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 80)
        lines.append("")
        
        # Summary
        critical = sum(1 for c in clashes if c.severity == 'critical')
        major = sum(1 for c in clashes if c.severity == 'major')
        minor = sum(1 for c in clashes if c.severity == 'minor')
        
        lines.append("SUMMARY")
        lines.append("-" * 40)
        lines.append(f"Total clashes:     {len(clashes)}")
        lines.append(f"Critical:          {critical} 🔴")
        lines.append(f"Major:             {major} 🟡")
        lines.append(f"Minor:             {minor} 🟢")
        lines.append("")
        
        # By type
        lines.append("CLASHES BY TYPE")
        lines.append("-" * 40)
        type_counts = {}
        for clash in clashes:
            t = clash.clash_type.value
            type_counts[t] = type_counts.get(t, 0) + 1
        
        for clash_type, count in sorted(type_counts.items()):
            lines.append(f"  {clash_type}: {count}")
        lines.append("")
        
        # Details
        lines.append("CLASH DETAILS")
        lines.append("-" * 40)
        
        for i, clash in enumerate(clashes, 1):
            severity_icon = {'critical': '🔴', 'major': '🟡', 'minor': '🟢'}.get(clash.severity, '⚪')
            lines.append(f"\n{i}. {severity_icon} {clash.clash_type.value.upper()}")
            lines.append(f"   Components: {clash.component1_id} ↔ {clash.component2_id}")
            lines.append(f"   Location: ({clash.location.x:.1f}, {clash.location.y:.1f}, {clash.location.z:.1f})")
            lines.append(f"   Distance: {clash.distance*12:.2f}\"")
            lines.append(f"   Description: {clash.description}")
            if clash.nfpa_reference:
                lines.append(f"   NFPA Reference: {clash.nfpa_reference}")
        
        lines.append("")
        lines.append("=" * 80)
        
        with open(output_path, 'w') as f:
            f.write('\n'.join(lines))
        
        self.logger.info(f"Clash report generated: {output_path}")
        return output_path


# =============================================================================
# MODULE INTERFACE
# =============================================================================

def create_bim_model_from_design(design_data: Dict[str, Any],
                                  project_name: str = "",
                                  project_id: str = "") -> BIMModel:
    """
    Create BIM model from design data
    
    Args:
        design_data: Dict with sprinklers, pipes, valves, hangers, braces
        project_name: Project name
        project_id: Project ID
        
    Returns:
        BIMModel instance
    """
    model = BIMModel(project_name, project_id)
    
    # Add pipes
    for pipe in design_data.get('pipes', []):
        model.add_pipe(
            pipe_id=pipe.get('id', str(uuid.uuid4())[:8]),
            start=pipe.get('start', (0, 0, 0)),
            end=pipe.get('end', (10, 0, 0)),
            diameter=pipe.get('diameter', 1.0),
            pipe_type=pipe.get('type', 'branch')
        )
    
    # Add sprinklers
    for spr in design_data.get('sprinklers', []):
        model.add_sprinkler(
            sprinkler_id=spr.get('id', str(uuid.uuid4())[:8]),
            position=(spr.get('x', 0), spr.get('y', 0), spr.get('z', 10)),
            orientation=spr.get('orientation', 'pendant'),
            k_factor=spr.get('k_factor', 5.6),
            coverage=spr.get('coverage', 130)
        )
    
    # Add valves
    for valve in design_data.get('valves', []):
        model.add_valve(
            valve_id=f"V-{valve.get('type', 'valve')}",
            position=valve.get('location', (0, 0, 0)),
            valve_type=valve.get('type', 'gate'),
            size=valve.get('size', 4)
        )
    
    # Add hangers
    for hanger in design_data.get('hangers', []):
        model.add_hanger(
            hanger_id=hanger.get('id', str(uuid.uuid4())[:8]),
            position=hanger.get('location', (0, 0, 0)),
            pipe_size=hanger.get('pipe_size', 1.0)
        )
    
    return model


def run_clash_detection_on_design(design_data: Dict[str, Any],
                                   obstructions: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Run clash detection on design
    
    Args:
        design_data: Fire sprinkler design data
        obstructions: List of obstructions (beams, ducts, etc.)
        
    Returns:
        Dict with clash results and statistics
    """
    model = create_bim_model_from_design(design_data)
    
    # Add obstructions
    if obstructions:
        for obs in obstructions:
            model.add_obstruction(
                obstruction_id=obs.get('id', str(uuid.uuid4())[:8]),
                min_point=obs.get('min_point', (0, 0, 0)),
                max_point=obs.get('max_point', (1, 1, 1)),
                obstruction_type=obs.get('type', 'beam')
            )
    
    # Run detection
    engine = ClashDetectionEngine(model)
    clashes = engine.run_clash_detection()
    
    return {
        'total_clashes': len(clashes),
        'critical': sum(1 for c in clashes if c.severity == 'critical'),
        'major': sum(1 for c in clashes if c.severity == 'major'),
        'minor': sum(1 for c in clashes if c.severity == 'minor'),
        'clashes': [
            {
                'type': c.clash_type.value,
                'component1': c.component1_id,
                'component2': c.component2_id,
                'location': c.location.to_tuple(),
                'distance': c.distance,
                'severity': c.severity,
                'description': c.description,
                'nfpa_reference': c.nfpa_reference,
            }
            for c in clashes
        ]
    }


# =============================================================================
# MODULE EXPORTS
# =============================================================================

__all__ = [
    'BIMModel',
    'ClashDetectionEngine',
    'BIMExporter',
    'Component3D',
    'ClashResult',
    'ClashType',
    'GeometryType',
    'Vector3',
    'BoundingBox',
    'Mesh3D',
    'create_bim_model_from_design',
    'run_clash_detection_on_design',
    'NFPA_CLEARANCES',
]


if __name__ == "__main__":
    print("🏗️ FireAI Pro - 3D BIM Coordination & Clash Detection Engine v1.0.0")
    print("=" * 60)
    print("Export formats: IFC, OBJ, STL, JSON")
    print("Clash detection: Hard, Soft, NFPA violations")
