#!/usr/bin/env python3
"""
FireAI Pro - Master Integrated Orchestrator v6.0
=================================================
Complete automated fire sprinkler design from construction documents.

WORKFLOW:
1. ANALYZE - Parse uploaded documents (PDF, DXF, images)
2. CLASSIFY - Determine occupancy, hazard class, code requirements
3. DESIGN - Create zone-by-zone sprinkler layout
4. VERIFY - Run compliance through standards engine (790+ rules)
5. DELIVER - Generate DXF, PDF reports, BOM with pricing

VERSION: 6.0.0-PRODUCTION
"""

import os
import json
import math
import csv
import uuid
import logging
import traceback
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI")


# =============================================================================
# IMPORTS
# =============================================================================

# Document analyzer
try:
    from document_analyzer import analyze_documents, BuildingAnalyzer
    ANALYZER_AVAILABLE = True
    logger.info("✅ Document analyzer loaded")
except Exception as e:
    ANALYZER_AVAILABLE = False
    logger.warning(f"⚠️ Document analyzer not available: {e}")

# Standards engine
STANDARDS_ENGINE_AVAILABLE = False
try:
    from fireai_pro_master_Standards import EnhancedFireAIProMaster
    STANDARDS_ENGINE_AVAILABLE = True
    logger.info("✅ Standards engine loaded (790+ NFPA rules)")
except Exception as e:
    logger.warning(f"⚠️ Standards engine not available: {e}")

# DXF generation
EZDXF_AVAILABLE = False
try:
    import ezdxf
    EZDXF_AVAILABLE = True
    logger.info("✅ ezdxf loaded")
except:
    logger.warning("⚠️ ezdxf not available")

# PDF generation
REPORTLAB_AVAILABLE = False
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.enums import TA_CENTER
    REPORTLAB_AVAILABLE = True
    logger.info("✅ reportlab loaded")
except:
    logger.warning("⚠️ reportlab not available")


# =============================================================================
# CONSTANTS
# =============================================================================

HAZARD_REQUIREMENTS = {
    'light_hazard': {'coverage': 225, 'spacing': 15, 'min_spacing': 6, 'density': 0.10, 'area': 1500, 'hose': 100, 'duration': 30},
    'ordinary_hazard_group_1': {'coverage': 130, 'spacing': 15, 'min_spacing': 6, 'density': 0.15, 'area': 1500, 'hose': 250, 'duration': 60},
    'ordinary_hazard_group_2': {'coverage': 130, 'spacing': 15, 'min_spacing': 6, 'density': 0.20, 'area': 1500, 'hose': 250, 'duration': 60},
    'extra_hazard_group_1': {'coverage': 100, 'spacing': 12, 'min_spacing': 6, 'density': 0.30, 'area': 2500, 'hose': 500, 'duration': 90},
    'extra_hazard_group_2': {'coverage': 100, 'spacing': 12, 'min_spacing': 6, 'density': 0.40, 'area': 2500, 'hose': 500, 'duration': 120},
}

HANGER_SPACING = {1.0: 12, 1.25: 12, 1.5: 12, 2.0: 12, 2.5: 12, 3.0: 15, 4.0: 15, 6.0: 15}
PIPE_ID = {1.0: 1.049, 1.25: 1.380, 1.5: 1.610, 2.0: 2.067, 2.5: 2.469, 3.0: 3.068, 4.0: 4.026, 6.0: 6.065}

# Pricing (typical 2024 prices)
PRICING = {
    'sprinkler_pendant': 45.00,
    'sprinkler_upright': 48.00,
    'sprinkler_sidewall': 55.00,
    'pipe_1': 4.50,      # per foot
    'pipe_1.25': 5.25,
    'pipe_1.5': 6.00,
    'pipe_2': 8.50,
    'pipe_2.5': 12.00,
    'pipe_3': 16.00,
    'pipe_4': 24.00,
    'pipe_6': 45.00,
    'tee': 18.00,
    'elbow': 12.00,
    'coupling': 8.00,
    'reducer': 15.00,
    'hanger': 12.00,
    'brace_lateral': 85.00,
    'brace_longitudinal': 95.00,
    'brace_4way': 175.00,
    'valve_os_y': 450.00,
    'valve_alarm_check': 1200.00,
    'valve_flow_switch': 350.00,
    'valve_drain': 125.00,
    'valve_test': 85.00,
    'valve_fdc': 650.00,
    'labor_rate': 85.00,  # per hour
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Zone:
    id: str
    name: str
    area: float
    height: float
    hazard: str
    x_offset: float = 0
    y_offset: float = 0
    width: float = 0
    length: float = 0


@dataclass
class Sprinkler:
    id: str
    x: float
    y: float
    z: float
    zone_id: str
    k_factor: float = 5.6
    temp: int = 165
    coverage: float = 130
    flow: float = 0
    pressure: float = 0


@dataclass
class Pipe:
    id: str
    x1: float
    y1: float
    z1: float
    x2: float
    y2: float
    z2: float
    diameter: float
    type: str
    zone_id: str = ""
    length: float = 0
    flow: float = 0
    velocity: float = 0
    
    def __post_init__(self):
        if self.length == 0:
            self.length = math.sqrt((self.x2-self.x1)**2 + (self.y2-self.y1)**2 + (self.z2-self.z1)**2)


@dataclass
class Fitting:
    id: str
    x: float
    y: float
    z: float
    type: str
    size: float


@dataclass
class Valve:
    id: str
    x: float
    y: float
    z: float
    type: str
    size: float


@dataclass
class Hanger:
    id: str
    x: float
    y: float
    z: float
    size: float


@dataclass
class Brace:
    id: str
    x: float
    y: float
    z: float
    type: str
    size: float


@dataclass
class Design:
    project_id: str
    project_name: str
    building_area: float
    building_height: float
    
    zones: List[Zone] = field(default_factory=list)
    sprinklers: List[Sprinkler] = field(default_factory=list)
    pipes: List[Pipe] = field(default_factory=list)
    fittings: List[Fitting] = field(default_factory=list)
    valves: List[Valve] = field(default_factory=list)
    hangers: List[Hanger] = field(default_factory=list)
    braces: List[Brace] = field(default_factory=list)
    
    demand_gpm: float = 0
    pressure_psi: float = 0
    
    compliant: bool = True
    score: float = 100
    violations: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    
    # Cost estimate
    material_cost: float = 0
    labor_cost: float = 0
    total_cost: float = 0
    
    # Analysis info
    analysis_confidence: float = 0
    jurisdiction: str = ""
    codes_applied: List[str] = field(default_factory=list)


# =============================================================================
# MULTI-ZONE DESIGN ENGINE
# =============================================================================

class MultiZoneDesigner:
    """Designs sprinkler systems for multi-zone buildings"""
    
    def __init__(self):
        self.c_factor = 120
    
    def design(self, project_data: Dict) -> Design:
        """Design complete multi-zone sprinkler system"""
        
        design = Design(
            project_id=project_data.get('project_id', f'FP-{uuid.uuid4().hex[:8].upper()}'),
            project_name=project_data.get('project_name', 'Fire Sprinkler Project'),
            building_area=project_data.get('building_area_sqft', 10000),
            building_height=project_data.get('ceiling_height_ft', 12)
        )
        
        # Create zones from project data or use single zone
        zones_data = project_data.get('zones', [])
        if not zones_data:
            # Single zone covering whole building
            zones_data = [{
                'zone_id': 'ZONE-001',
                'zone_name': 'Main Area',
                'area_sqft': design.building_area,
                'ceiling_height_ft': design.building_height,
                'hazard_class': project_data.get('hazard_class', 'ordinary_hazard_group_1')
            }]
        
        # Create zone objects and calculate positions
        total_area = sum(z.get('area_sqft', 0) for z in zones_data)
        if total_area == 0:
            total_area = design.building_area
        
        x_offset = 0
        for i, zd in enumerate(zones_data):
            zone_area = zd.get('area_sqft', 0)
            if zone_area == 0:
                zone_area = design.building_area / len(zones_data)
            
            # Calculate zone dimensions (assume rectangular)
            zone_width = math.sqrt(zone_area)
            zone_length = zone_area / zone_width if zone_width > 0 else zone_width
            
            zone = Zone(
                id=zd.get('zone_id', f'ZONE-{i+1:03d}'),
                name=zd.get('zone_name', f'Zone {i+1}'),
                area=zone_area,
                height=zd.get('ceiling_height_ft', design.building_height),
                hazard=zd.get('hazard_class', 'ordinary_hazard_group_1'),
                x_offset=x_offset,
                y_offset=0,
                width=zone_width,
                length=zone_length
            )
            design.zones.append(zone)
            x_offset += zone_width + 5  # 5' gap between zones
        
        logger.info(f"Created {len(design.zones)} zones")
        
        # Design each zone
        for zone in design.zones:
            self._design_zone(design, zone)
        
        # Design main piping (connects all zones)
        self._design_main_piping(design)
        
        # Add valves
        self._add_valves(design)
        
        # Calculate hydraulics
        self._calculate_hydraulics(design)
        
        # Calculate costs
        self._calculate_costs(design)
        
        return design
    
    def _design_zone(self, design: Design, zone: Zone):
        """Design sprinkler layout for a single zone"""
        
        req = HAZARD_REQUIREMENTS.get(zone.hazard, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
        
        # Calculate spacing
        spacing = min(req['spacing'] * 0.80, math.sqrt(req['coverage'] * 0.85))
        coverage = spacing * spacing
        offset = spacing / 2
        
        # Calculate grid
        num_x = max(1, int((zone.width - offset) / spacing) + 1)
        num_y = max(1, int((zone.length - offset) / spacing) + 1)
        
        spk_z = zone.height - 0.5
        pipe_z = zone.height - 1.0
        
        # Create sprinklers
        spk_start = len(design.sprinklers)
        for i in range(num_x):
            for j in range(num_y):
                x = zone.x_offset + min(offset + i * spacing, zone.width - 1)
                y = zone.y_offset + min(offset + j * spacing, zone.length - 1)
                
                design.sprinklers.append(Sprinkler(
                    id=f"SP-{len(design.sprinklers)+1:03d}",
                    x=x, y=y, z=spk_z,
                    zone_id=zone.id,
                    k_factor=5.6, temp=165, coverage=coverage
                ))
        
        logger.info(f"  Zone {zone.name}: {len(design.sprinklers) - spk_start} sprinklers")
        
        # Create branch lines for this zone
        zone_sprinklers = [s for s in design.sprinklers if s.zone_id == zone.id]
        unique_x = sorted(set(round(s.x, 0) for s in zone_sprinklers))
        
        for bx in unique_x:
            branch_spks = [s for s in zone_sprinklers if abs(s.x - bx) < 2]
            if branch_spks:
                min_y = min(s.y for s in branch_spks)
                max_y = max(s.y for s in branch_spks)
                
                # Size branch
                num = len(branch_spks)
                dia = 1.0 if num <= 2 else (1.25 if num <= 4 else (1.5 if num <= 6 else (2.0 if num <= 10 else 2.5)))
                
                design.pipes.append(Pipe(
                    id=f"P-{len(design.pipes)+1:03d}-BR",
                    x1=bx, y1=min_y - 2, z1=pipe_z,
                    x2=bx, y2=max_y + 2, z2=pipe_z,
                    diameter=dia, type="branch", zone_id=zone.id
                ))
                
                # Add fittings for sprinkler tees
                for spk in branch_spks:
                    design.fittings.append(Fitting(
                        id=f"F-{len(design.fittings)+1:03d}",
                        x=spk.x, y=spk.y, z=pipe_z,
                        type="tee", size=dia
                    ))
        
        # Add hangers for branch lines
        zone_pipes = [p for p in design.pipes if p.zone_id == zone.id]
        for pipe in zone_pipes:
            max_space = HANGER_SPACING.get(pipe.diameter, 12)
            num_hangers = max(1, int(math.ceil(pipe.length / max_space)))
            for i in range(num_hangers):
                frac = (i + 0.5) / num_hangers
                design.hangers.append(Hanger(
                    id=f"H-{len(design.hangers)+1:03d}",
                    x=pipe.x1 + (pipe.x2 - pipe.x1) * frac,
                    y=pipe.y1 + (pipe.y2 - pipe.y1) * frac,
                    z=pipe.z1,
                    size=pipe.diameter
                ))
    
    def _design_main_piping(self, design: Design):
        """Design main piping connecting all zones"""
        
        if not design.zones:
            return
        
        # Riser location
        rx, ry = 5.0, 5.0
        pipe_z = design.building_height - 1.0
        
        # Riser
        design.pipes.append(Pipe(
            id=f"P-{len(design.pipes)+1:03d}-RISER",
            x1=rx, y1=ry, z1=0, x2=rx, y2=ry, z2=pipe_z,
            diameter=4.0, type="riser"
        ))
        
        # Feed main (spans all zones)
        max_x = max(z.x_offset + z.width for z in design.zones)
        design.pipes.append(Pipe(
            id=f"P-{len(design.pipes)+1:03d}-MAIN",
            x1=rx, y1=ry, z1=pipe_z, x2=max_x, y2=ry, z2=pipe_z,
            diameter=4.0, type="main"
        ))
        
        # Cross mains to each zone
        for zone in design.zones:
            zone_center_x = zone.x_offset + zone.width / 2
            
            # Cross main from feed main to zone
            design.pipes.append(Pipe(
                id=f"P-{len(design.pipes)+1:03d}-XMAIN",
                x1=zone_center_x, y1=ry, z1=pipe_z,
                x2=zone_center_x, y2=zone.y_offset + zone.length / 2, z2=pipe_z,
                diameter=3.0, type="cross_main", zone_id=zone.id
            ))
            
            # Tee at connection
            design.fittings.append(Fitting(
                id=f"F-{len(design.fittings)+1:03d}",
                x=zone_center_x, y=ry, z=pipe_z,
                type="tee", size=4.0
            ))
        
        # Elbow at riser
        design.fittings.append(Fitting(
            id=f"F-{len(design.fittings)+1:03d}",
            x=rx, y=ry, z=pipe_z,
            type="elbow", size=4.0
        ))
        
        # Main piping hangers
        main_pipes = [p for p in design.pipes if p.type in ["main", "cross_main"]]
        for pipe in main_pipes:
            max_space = HANGER_SPACING.get(pipe.diameter, 15)
            num = max(1, int(math.ceil(pipe.length / max_space)))
            for i in range(num):
                frac = (i + 0.5) / num
                design.hangers.append(Hanger(
                    id=f"H-{len(design.hangers)+1:03d}",
                    x=pipe.x1 + (pipe.x2 - pipe.x1) * frac,
                    y=pipe.y1 + (pipe.y2 - pipe.y1) * frac,
                    z=pipe.z1,
                    size=pipe.diameter
                ))
        
        # Seismic bracing for large pipes
        for pipe in design.pipes:
            if pipe.diameter < 2.5:
                continue
            
            # Lateral braces
            num_lat = max(1, int(math.ceil(pipe.length / 40)))
            for i in range(num_lat):
                frac = (i + 0.5) / num_lat
                design.braces.append(Brace(
                    id=f"B-{len(design.braces)+1:03d}",
                    x=pipe.x1 + (pipe.x2 - pipe.x1) * frac,
                    y=pipe.y1 + (pipe.y2 - pipe.y1) * frac,
                    z=pipe.z1, type="lateral", size=pipe.diameter
                ))
            
            # Longitudinal for mains
            if pipe.type in ["main", "riser"]:
                num_long = max(1, int(math.ceil(pipe.length / 80)))
                for i in range(num_long):
                    frac = (i + 0.5) / num_long
                    design.braces.append(Brace(
                        id=f"B-{len(design.braces)+1:03d}",
                        x=pipe.x1 + (pipe.x2 - pipe.x1) * frac,
                        y=pipe.y1 + (pipe.y2 - pipe.y1) * frac,
                        z=pipe.z1, type="longitudinal", size=pipe.diameter
                    ))
    
    def _add_valves(self, design: Design):
        """Add required valves"""
        rx, ry = 5.0, 5.0
        
        design.valves = [
            Valve(id="V-001", x=rx, y=ry, z=2.0, type="os_y", size=4.0),
            Valve(id="V-002", x=rx, y=ry, z=3.0, type="alarm_check", size=4.0),
            Valve(id="V-003", x=rx, y=ry, z=4.0, type="flow_switch", size=4.0),
            Valve(id="V-004", x=rx+1, y=ry, z=1.5, type="drain", size=2.0),
            Valve(id="V-005", x=50, y=50, z=design.building_height-1, type="test", size=1.0),
            Valve(id="V-006", x=rx-3, y=ry, z=3.0, type="fdc", size=4.0),
        ]
    
    def _calculate_hydraulics(self, design: Design):
        """Calculate system hydraulics"""
        
        # Get most demanding zone
        max_density = 0
        max_hose = 0
        for zone in design.zones:
            req = HAZARD_REQUIREMENTS.get(zone.hazard, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
            if req['density'] > max_density:
                max_density = req['density']
                max_hose = req['hose']
        
        if max_density == 0:
            req = HAZARD_REQUIREMENTS['ordinary_hazard_group_1']
            max_density = req['density']
            max_hose = req['hose']
        
        # Calculate sprinkler flows
        active_count = min(len(design.sprinklers), 15)  # Typical remote area
        for i, spk in enumerate(design.sprinklers):
            if i < active_count:
                spk.flow = max(max_density * spk.coverage, 15)
                spk.pressure = (spk.flow / spk.k_factor) ** 2
        
        # Calculate pipe flows
        for pipe in design.pipes:
            if pipe.type == "riser" or pipe.type == "main":
                pipe.flow = sum(s.flow for s in design.sprinklers)
            elif pipe.type == "branch":
                branch_spks = [s for s in design.sprinklers if abs(s.x - pipe.x1) < 2]
                pipe.flow = sum(s.flow for s in branch_spks)
            elif pipe.type == "cross_main":
                zone_spks = [s for s in design.sprinklers if s.zone_id == pipe.zone_id]
                pipe.flow = sum(s.flow for s in zone_spks)
            
            if pipe.diameter > 0:
                inner = PIPE_ID.get(pipe.diameter, pipe.diameter)
                pipe.velocity = 0.4085 * pipe.flow / (inner ** 2) if inner > 0 else 0
        
        design.demand_gpm = sum(s.flow for s in design.sprinklers if s.flow > 0) + max_hose
        design.pressure_psi = max((s.pressure for s in design.sprinklers if s.pressure > 0), default=7) + 25
    
    def _calculate_costs(self, design: Design):
        """Calculate material and labor costs"""
        
        material = 0
        labor_hours = 0
        
        # Sprinklers
        material += len(design.sprinklers) * PRICING['sprinkler_pendant']
        labor_hours += len(design.sprinklers) * 0.5  # 30 min per head
        
        # Pipe
        for pipe in design.pipes:
            key = f"pipe_{int(pipe.diameter)}" if pipe.diameter == int(pipe.diameter) else f"pipe_{pipe.diameter}"
            price = PRICING.get(key, PRICING.get('pipe_2', 8.50))
            material += pipe.length * price
            labor_hours += pipe.length * 0.1  # 6 min per foot
        
        # Fittings
        for fit in design.fittings:
            material += PRICING.get(fit.type, 15)
            labor_hours += 0.25  # 15 min per fitting
        
        # Valves
        for v in design.valves:
            material += PRICING.get(f"valve_{v.type}", 200)
            labor_hours += 1.0  # 1 hour per valve
        
        # Hangers
        material += len(design.hangers) * PRICING['hanger']
        labor_hours += len(design.hangers) * 0.25
        
        # Braces
        for b in design.braces:
            material += PRICING.get(f"brace_{b.type}", PRICING['brace_lateral'])
            labor_hours += 0.5
        
        design.material_cost = material
        design.labor_cost = labor_hours * PRICING['labor_rate']
        design.total_cost = design.material_cost + design.labor_cost


# =============================================================================
# COMPLIANCE CHECKER
# =============================================================================

def check_compliance(design: Design, zip_code: str = "") -> Design:
    """Run compliance check"""
    
    if STANDARDS_ENGINE_AVAILABLE and zip_code:
        try:
            master = EnhancedFireAIProMaster()
            
            # Find primary hazard
            primary_hazard = 'ordinary_hazard_group_1'
            if design.zones:
                primary_hazard = design.zones[0].hazard
            
            project_data = {
                'project_id': design.project_id,
                'project_name': design.project_name,
                'building_area': design.building_area,
                'building_height': design.building_height,
                'stories': 1,
                'construction_type': 'Type II-B',
                'hazard_classification': primary_hazard,
                'sprinkler_required': True,
                'system_types': ['wet_pipe_sprinkler'],
            }
            
            result = master.analyze_project(project_data, zip_code)
            
            design.score = result.overall_compliance_score
            design.compliant = design.score >= 80 and len(result.critical_violations) == 0
            design.violations = [f"{v.rule_id}: {v.notes}" for v in result.critical_violations]
            design.recommendations = result.recommendations
            
            if result.jurisdiction_info:
                design.jurisdiction = f"{result.jurisdiction_info.city}, {result.jurisdiction_info.state_code}"
            
            design.codes_applied = ['NFPA 13', 'IBC', 'IFC']
            
            logger.info(f"Standards engine: {design.score:.1f}% score")
            
        except Exception as e:
            logger.warning(f"Standards engine error: {e}")
            design = _basic_compliance(design)
    else:
        design = _basic_compliance(design)
    
    return design


def _basic_compliance(design: Design) -> Design:
    """Basic compliance check"""
    
    for zone in design.zones:
        req = HAZARD_REQUIREMENTS.get(zone.hazard, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
        
        zone_spks = [s for s in design.sprinklers if s.zone_id == zone.id]
        for i, s1 in enumerate(zone_spks):
            for s2 in zone_spks[i+1:]:
                dist = math.sqrt((s1.x - s2.x)**2 + (s1.y - s2.y)**2)
                if dist < req['min_spacing']:
                    design.violations.append(f"Spacing: {s1.id} and {s2.id} at {dist:.1f}' (min {req['min_spacing']}')")
    
    for pipe in design.pipes:
        if pipe.velocity > 32 and pipe.length > 1:
            design.violations.append(f"Velocity: {pipe.id} at {pipe.velocity:.1f} fps (max 32)")
    
    design.compliant = len(design.violations) == 0
    design.score = 100.0 if design.compliant else max(0, 100 - len(design.violations) * 10)
    design.codes_applied = ['NFPA 13']
    
    return design


# =============================================================================
# OUTPUT GENERATORS
# =============================================================================

def generate_dxf(design: Design, path: str) -> bool:
    """Generate DXF drawing"""
    
    if not EZDXF_AVAILABLE:
        logger.error("ezdxf not available")
        return False
    
    try:
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        # Layers
        layers = [
            ('FIRE-PIPE', 1), ('FIRE-SPRINKLER', 4), ('FIRE-FITTING', 6),
            ('FIRE-VALVE', 3), ('FIRE-HANGER', 8), ('FIRE-BRACE', 5),
            ('FIRE-TEXT', 7), ('FIRE-ZONE', 2)
        ]
        for name, color in layers.items() if isinstance(layers, dict) else layers:
            try:
                doc.layers.add(name, color=color)
            except:
                pass
        
        # Draw zone boundaries
        for zone in design.zones:
            pts = [
                (zone.x_offset, zone.y_offset),
                (zone.x_offset + zone.width, zone.y_offset),
                (zone.x_offset + zone.width, zone.y_offset + zone.length),
                (zone.x_offset, zone.y_offset + zone.length),
                (zone.x_offset, zone.y_offset)
            ]
            msp.add_lwpolyline(pts, dxfattribs={'layer': 'FIRE-ZONE'})
            msp.add_text(zone.name, dxfattribs={'layer': 'FIRE-ZONE', 'height': 1.5}).set_placement(
                (zone.x_offset + zone.width/2, zone.y_offset + zone.length + 2))
        
        # Draw pipes
        for pipe in design.pipes:
            if pipe.type == "riser":
                msp.add_circle((pipe.x1, pipe.y1), radius=1.5, dxfattribs={'layer': 'FIRE-PIPE'})
                msp.add_line((pipe.x1-1, pipe.y1-1), (pipe.x1+1, pipe.y1+1), dxfattribs={'layer': 'FIRE-PIPE'})
                msp.add_line((pipe.x1-1, pipe.y1+1), (pipe.x1+1, pipe.y1-1), dxfattribs={'layer': 'FIRE-PIPE'})
            else:
                msp.add_line((pipe.x1, pipe.y1), (pipe.x2, pipe.y2), dxfattribs={'layer': 'FIRE-PIPE'})
                mid_x, mid_y = (pipe.x1 + pipe.x2) / 2, (pipe.y1 + pipe.y2) / 2
                msp.add_text(f'{pipe.diameter}"', dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.6}).set_placement((mid_x, mid_y + 0.8))
        
        # Draw sprinklers
        for spk in design.sprinklers:
            msp.add_circle((spk.x, spk.y), radius=0.6, dxfattribs={'layer': 'FIRE-SPRINKLER'})
            msp.add_line((spk.x-0.4, spk.y), (spk.x+0.4, spk.y), dxfattribs={'layer': 'FIRE-SPRINKLER'})
            msp.add_line((spk.x, spk.y-0.4), (spk.x, spk.y+0.4), dxfattribs={'layer': 'FIRE-SPRINKLER'})
        
        # Draw fittings
        for fit in design.fittings:
            if fit.type == "tee":
                msp.add_circle((fit.x, fit.y), radius=0.15, dxfattribs={'layer': 'FIRE-FITTING'})
            elif fit.type == "elbow":
                msp.add_arc((fit.x, fit.y), radius=0.3, start_angle=0, end_angle=90, dxfattribs={'layer': 'FIRE-FITTING'})
        
        # Draw valves
        valve_labels = {"os_y": "OS&Y", "alarm_check": "ACV", "flow_switch": "FS", "drain": "MD", "test": "IT", "fdc": "FDC"}
        for v in design.valves:
            msp.add_lwpolyline([(v.x, v.y+0.5), (v.x+0.5, v.y), (v.x, v.y-0.5), (v.x-0.5, v.y), (v.x, v.y+0.5)], dxfattribs={'layer': 'FIRE-VALVE'})
            msp.add_text(valve_labels.get(v.type, "V"), dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.4}).set_placement((v.x+0.8, v.y))
        
        # Draw hangers
        for h in design.hangers:
            msp.add_lwpolyline([(h.x-0.25, h.y+0.25), (h.x+0.25, h.y+0.25), (h.x, h.y)], dxfattribs={'layer': 'FIRE-HANGER'})
        
        # Draw braces
        for b in design.braces:
            msp.add_circle((b.x, b.y), radius=0.3, dxfattribs={'layer': 'FIRE-BRACE'})
        
        # Legend
        lx, ly = -30, 10
        msp.add_text("LEGEND", dxfattribs={'layer': 'FIRE-TEXT', 'height': 1.0}).set_placement((lx, ly))
        msp.add_circle((lx+1, ly-3), radius=0.6, dxfattribs={'layer': 'FIRE-SPRINKLER'})
        msp.add_text("Sprinkler (K5.6, 165F)", dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.5}).set_placement((lx+3, ly-3))
        msp.add_line((lx, ly-5), (lx+2, ly-5), dxfattribs={'layer': 'FIRE-PIPE'})
        msp.add_text("Fire Pipe", dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.5}).set_placement((lx+3, ly-5))
        msp.add_lwpolyline([(lx+1, ly-6.5), (lx+1.5, ly-7), (lx+1, ly-7.5), (lx+0.5, ly-7), (lx+1, ly-6.5)], dxfattribs={'layer': 'FIRE-VALVE'})
        msp.add_text("Valve", dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.5}).set_placement((lx+3, ly-7))
        
        # Title block
        tbx, tby = -30, -20
        pipe_length = sum(p.length for p in design.pipes)
        msp.add_lwpolyline([(tbx, tby), (tbx+50, tby), (tbx+50, tby+12), (tbx, tby+12), (tbx, tby)], dxfattribs={'layer': 'FIRE-TEXT'})
        msp.add_text(design.project_name[:40], dxfattribs={'layer': 'FIRE-TEXT', 'height': 1.0}).set_placement((tbx+2, tby+9))
        msp.add_text(f"Project: {design.project_id}", dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.7}).set_placement((tbx+2, tby+7))
        status = "COMPLIANT" if design.compliant else "NON-COMPLIANT"
        msp.add_text(f"Status: {status} ({design.score:.0f}%)", dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.6}).set_placement((tbx+2, tby+5))
        msp.add_text(f"Demand: {design.demand_gpm:.0f} GPM @ {design.pressure_psi:.1f} PSI", dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.5}).set_placement((tbx+2, tby+3))
        msp.add_text(f"Sprinklers: {len(design.sprinklers)} | Pipe: {pipe_length:.0f} LF | Est: ${design.total_cost:,.0f}", dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.5}).set_placement((tbx+2, tby+1))
        
        doc.saveas(path)
        logger.info(f"DXF saved: {path}")
        return True
        
    except Exception as e:
        logger.error(f"DXF error: {e}")
        traceback.print_exc()
        return False


def generate_pdf(design: Design, path: str) -> bool:
    """Generate PDF compliance report"""
    
    if not REPORTLAB_AVAILABLE:
        return False
    
    try:
        doc = SimpleDocTemplate(path, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, alignment=TA_CENTER)
        story = []
        
        # Title
        story.append(Paragraph("FIRE SPRINKLER SYSTEM", title_style))
        story.append(Paragraph("COMPLIANCE REPORT", title_style))
        story.append(Spacer(1, 15))
        
        # Project info
        story.append(Paragraph("PROJECT INFORMATION", styles['Heading2']))
        info = [
            ["Project Name:", design.project_name],
            ["Project ID:", design.project_id],
            ["Building Area:", f"{design.building_area:,.0f} sq ft"],
            ["Zones:", str(len(design.zones))],
        ]
        if design.jurisdiction:
            info.append(["Jurisdiction:", design.jurisdiction])
        t = Table(info, colWidths=[2.5*inch, 4*inch])
        t.setStyle(TableStyle([('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold')]))
        story.append(t)
        story.append(Spacer(1, 15))
        
        # Zone breakdown
        if design.zones:
            story.append(Paragraph("ZONE ANALYSIS", styles['Heading2']))
            zone_data = [["Zone", "Area (sqft)", "Hazard Class", "Sprinklers"]]
            for zone in design.zones:
                zone_spks = len([s for s in design.sprinklers if s.zone_id == zone.id])
                zone_data.append([zone.name, f"{zone.area:,.0f}", zone.hazard.replace('_', ' ').title(), str(zone_spks)])
            zt = Table(zone_data, colWidths=[1.5*inch, 1.5*inch, 2*inch, 1*inch])
            zt.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            story.append(zt)
            story.append(Spacer(1, 15))
        
        # Compliance
        story.append(Paragraph("COMPLIANCE STATUS", styles['Heading2']))
        status_color = colors.green if design.compliant else colors.red
        status = [
            ["Status:", "COMPLIANT" if design.compliant else "NON-COMPLIANT"],
            ["Score:", f"{design.score:.1f}%"],
            ["Codes Applied:", ", ".join(design.codes_applied) if design.codes_applied else "NFPA 13"],
            ["Violations:", str(len(design.violations))]
        ]
        st = Table(status, colWidths=[2.5*inch, 4*inch])
        st.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (1, 0), (1, 0), status_color),
            ('TEXTCOLOR', (1, 0), (1, 0), colors.white)
        ]))
        story.append(st)
        story.append(Spacer(1, 15))
        
        # System summary
        story.append(Paragraph("SYSTEM SUMMARY", styles['Heading2']))
        pipe_length = sum(p.length for p in design.pipes)
        comp = [
            ["Component", "Qty", "Notes"],
            ["Sprinklers", str(len(design.sprinklers)), "K=5.6, 165°F"],
            ["Pipe", f"{pipe_length:.0f} LF", "Sch 40 Black Steel"],
            ["Fittings", str(len(design.fittings)), "Malleable Iron"],
            ["Valves", str(len(design.valves)), "Per NFPA 13 Ch.12"],
            ["Hangers", str(len(design.hangers)), "Per NFPA 13 Sec.16"],
            ["Seismic Braces", str(len(design.braces)), "Per NFPA 13 Ch.18"]
        ]
        ct = Table(comp, colWidths=[2*inch, 1.5*inch, 2.5*inch])
        ct.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(ct)
        story.append(Spacer(1, 15))
        
        # Hydraulics
        story.append(Paragraph("HYDRAULIC SUMMARY", styles['Heading2']))
        hyd = [
            ["Parameter", "Value"],
            ["System Demand", f"{design.demand_gpm:.0f} GPM"],
            ["System Pressure", f"{design.pressure_psi:.1f} PSI"],
        ]
        ht = Table(hyd, colWidths=[3*inch, 3*inch])
        ht.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(ht)
        story.append(Spacer(1, 15))
        
        # Cost estimate
        story.append(Paragraph("COST ESTIMATE", styles['Heading2']))
        cost = [
            ["Category", "Amount"],
            ["Materials", f"${design.material_cost:,.2f}"],
            ["Labor", f"${design.labor_cost:,.2f}"],
            ["TOTAL", f"${design.total_cost:,.2f}"]
        ]
        costt = Table(cost, colWidths=[3*inch, 3*inch])
        costt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(costt)
        
        # Violations
        if design.violations:
            story.append(PageBreak())
            story.append(Paragraph("VIOLATIONS", styles['Heading2']))
            for v in design.violations[:15]:
                story.append(Paragraph(f"• {v}", styles['Normal']))
        
        doc.build(story)
        logger.info(f"PDF saved: {path}")
        return True
        
    except Exception as e:
        logger.error(f"PDF error: {e}")
        return False


def generate_bom(design: Design, path: str) -> bool:
    """Generate bill of materials with pricing"""
    
    try:
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(["Item", "Description", "Size", "Material", "Qty", "Unit", "Unit Price", "Total", "NFPA Ref"])
            
            item = 1
            
            # Sprinklers
            price = PRICING['sprinkler_pendant']
            total = len(design.sprinklers) * price
            w.writerow([item, "Sprinkler Head, Pendant, QR, K5.6, 165F", '1/2"', "Brass/Chrome", 
                        len(design.sprinklers), "EA", f"${price:.2f}", f"${total:.2f}", "Sec 8.5"])
            item += 1
            
            # Pipes by size
            pipe_groups = {}
            for p in design.pipes:
                pipe_groups[p.diameter] = pipe_groups.get(p.diameter, 0) + p.length
            for dia, length in sorted(pipe_groups.items()):
                key = f"pipe_{int(dia)}" if dia == int(dia) else f"pipe_{dia}"
                price = PRICING.get(key, 8.50)
                total = length * price
                w.writerow([item, "Pipe, Schedule 40, Black Steel", f'{dia}"', "Steel", 
                            f"{length:.1f}", "LF", f"${price:.2f}", f"${total:.2f}", "Ch 22"])
                item += 1
            
            # Fittings
            fit_groups = {}
            for f in design.fittings:
                fit_groups[(f.type, f.size)] = fit_groups.get((f.type, f.size), 0) + 1
            for (ftype, size), qty in fit_groups.items():
                price = PRICING.get(ftype, 15)
                total = qty * price
                w.writerow([item, f"{ftype.title()} Fitting", f'{size}"', "Malleable Iron", 
                            qty, "EA", f"${price:.2f}", f"${total:.2f}", "Ch 22"])
                item += 1
            
            # Valves
            for v in design.valves:
                price = PRICING.get(f"valve_{v.type}", 200)
                w.writerow([item, f"{v.type.replace('_', ' ').title()} Valve", f'{v.size}"', 
                            "Various", 1, "EA", f"${price:.2f}", f"${price:.2f}", "Ch 12"])
                item += 1
            
            # Hangers
            hanger_groups = {}
            for h in design.hangers:
                hanger_groups[h.size] = hanger_groups.get(h.size, 0) + 1
            for size, qty in hanger_groups.items():
                price = PRICING['hanger']
                total = qty * price
                w.writerow([item, "Clevis Hanger", f'{size}" pipe', "Steel/Zinc", 
                            qty, "EA", f"${price:.2f}", f"${total:.2f}", "Sec 16.4"])
                item += 1
            
            # Braces
            brace_groups = {}
            for b in design.braces:
                brace_groups[b.type] = brace_groups.get(b.type, 0) + 1
            for btype, qty in brace_groups.items():
                price = PRICING.get(f"brace_{btype}", 85)
                total = qty * price
                w.writerow([item, f"Seismic Brace, {btype.title()}", "Per Design", "Steel", 
                            qty, "EA", f"${price:.2f}", f"${total:.2f}", "Ch 18"])
                item += 1
            
            # Totals
            w.writerow([])
            w.writerow(["", "", "", "", "", "", "Material Total:", f"${design.material_cost:,.2f}", ""])
            w.writerow(["", "", "", "", "", "", "Labor:", f"${design.labor_cost:,.2f}", ""])
            w.writerow(["", "", "", "", "", "", "GRAND TOTAL:", f"${design.total_cost:,.2f}", ""])
        
        logger.info(f"BOM saved: {path}")
        return True
        
    except Exception as e:
        logger.error(f"BOM error: {e}")
        return False


# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def orchestrate(project_dir: str, output_dir: str) -> Dict[str, str]:
    """
    Main orchestration function.
    Analyzes documents, designs system, checks compliance, generates outputs.
    """
    logger.info("=" * 60)
    logger.info("🔥 FireAI Pro Master Orchestrator v6.0")
    logger.info("=" * 60)
    start = datetime.now()
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Load project data
    project_data = {
        'project_id': f'FP-{uuid.uuid4().hex[:8].upper()}',
        'project_name': 'Fire Sprinkler Project',
        'building_area_sqft': 10000,
        'ceiling_height_ft': 12,
        'hazard_class': 'ordinary_hazard_group_1',
        'zip_code': ''
    }
    
    json_path = os.path.join(project_dir, 'project.json')
    if os.path.exists(json_path):
        try:
            with open(json_path) as f:
                project_data.update(json.load(f))
        except Exception as e:
            logger.warning(f"Could not load project.json: {e}")
    
    # STEP 1: Analyze documents
    logger.info("-" * 40)
    logger.info("STEP 1: Document Analysis")
    
    if ANALYZER_AVAILABLE:
        try:
            analyzed = analyze_documents(project_dir, project_data)
            # Merge analyzed data with manual overrides
            for key in ['building_area_sqft', 'ceiling_height_ft', 'hazard_class', 'zones']:
                if key in analyzed and analyzed[key]:
                    if key not in project_data or not project_data[key]:
                        project_data[key] = analyzed[key]
            
            project_data['analysis_confidence'] = analyzed.get('analysis_confidence', 0)
            logger.info(f"  Confidence: {project_data.get('analysis_confidence', 0):.0f}%")
        except Exception as e:
            logger.warning(f"  Document analysis failed: {e}")
    else:
        logger.info("  Document analyzer not available, using manual data")
    
    logger.info(f"  Area: {project_data.get('building_area_sqft')} sqft")
    logger.info(f"  Hazard: {project_data.get('hazard_class')}")
    
    # STEP 2: Design system
    logger.info("-" * 40)
    logger.info("STEP 2: System Design")
    
    designer = MultiZoneDesigner()
    design = designer.design(project_data)
    
    logger.info(f"  Zones: {len(design.zones)}")
    logger.info(f"  Sprinklers: {len(design.sprinklers)}")
    logger.info(f"  Pipe: {sum(p.length for p in design.pipes):.0f} LF")
    logger.info(f"  Demand: {design.demand_gpm:.0f} GPM @ {design.pressure_psi:.1f} PSI")
    
    # STEP 3: Compliance check
    logger.info("-" * 40)
    logger.info("STEP 3: Compliance Check")
    
    design = check_compliance(design, project_data.get('zip_code', ''))
    
    logger.info(f"  Score: {design.score:.1f}%")
    logger.info(f"  Status: {'✅ COMPLIANT' if design.compliant else '❌ NON-COMPLIANT'}")
    
    # STEP 4: Generate outputs
    logger.info("-" * 40)
    logger.info("STEP 4: Generate Outputs")
    
    outputs = {}
    
    if EZDXF_AVAILABLE:
        dxf_path = os.path.join(output_dir, 'design.dxf')
        if generate_dxf(design, dxf_path):
            outputs['design.dxf'] = dxf_path
    
    if REPORTLAB_AVAILABLE:
        pdf_path = os.path.join(output_dir, 'compliance_report.pdf')
        if generate_pdf(design, pdf_path):
            outputs['compliance_report.pdf'] = pdf_path
    
    bom_path = os.path.join(output_dir, 'bill_of_materials.csv')
    if generate_bom(design, bom_path):
        outputs['bill_of_materials.csv'] = bom_path
    
    # Summary JSON
    summary_path = os.path.join(output_dir, 'summary.json')
    try:
        summary = {
            'project_id': design.project_id,
            'project_name': design.project_name,
            'compliant': design.compliant,
            'score': design.score,
            'jurisdiction': design.jurisdiction,
            'codes_applied': design.codes_applied,
            'zones': [{'id': z.id, 'name': z.name, 'area': z.area, 'hazard': z.hazard} for z in design.zones],
            'system': {
                'sprinklers': len(design.sprinklers),
                'pipe_ft': round(sum(p.length for p in design.pipes), 1),
                'fittings': len(design.fittings),
                'valves': len(design.valves),
                'hangers': len(design.hangers),
                'braces': len(design.braces)
            },
            'hydraulics': {'demand_gpm': round(design.demand_gpm, 1), 'pressure_psi': round(design.pressure_psi, 1)},
            'cost': {'material': round(design.material_cost, 2), 'labor': round(design.labor_cost, 2), 'total': round(design.total_cost, 2)},
            'violations': design.violations
        }
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        outputs['summary.json'] = summary_path
    except Exception as e:
        logger.error(f"Summary error: {e}")
    
    elapsed = (datetime.now() - start).total_seconds()
    logger.info("=" * 60)
    logger.info(f"🎉 COMPLETE in {elapsed:.2f}s")
    logger.info(f"   Est. Cost: ${design.total_cost:,.2f}")
    logger.info("=" * 60)
    
    return outputs


def get_engine_status() -> Dict[str, Any]:
    """Get engine status for health checks"""
    return {
        'document_analyzer': ANALYZER_AVAILABLE,
        'standards_engine': STANDARDS_ENGINE_AVAILABLE,
        'ezdxf': EZDXF_AVAILABLE,
        'reportlab': REPORTLAB_AVAILABLE,
        'routing': True,
        'hydraulics': True,
        'codes': STANDARDS_ENGINE_AVAILABLE
    }


if __name__ == "__main__":
    print("🔥 FireAI Pro Master Orchestrator v6.0")
    print("=" * 50)
    status = get_engine_status()
    for k, v in status.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print("\nReady!")
