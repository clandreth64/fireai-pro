#!/usr/bin/env python3
"""
FireAI Pro - Production Orchestrator v4.0
==========================================
Integrates sprinkler system design with the comprehensive standards engine
for full NFPA compliance checking including jurisdiction-specific requirements.

This orchestrator:
1. Designs the sprinkler system (sprinklers, pipes, fittings, hangers, braces)
2. Converts design to FireProtectionProject format
3. Runs compliance through EnhancedFireAIProMaster (790+ NFPA rules)
4. Generates DXF drawings, PDF reports, and BOM

VERSION: 4.0.0-PRODUCTION
"""

import os
import json
import math
import csv
import uuid
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI")

# =============================================================================
# IMPORT STANDARDS ENGINE
# =============================================================================

STANDARDS_ENGINE_AVAILABLE = False
try:
    from fireai_pro_master_Standards import (
        EnhancedFireAIProMaster,
        FireProtectionProject,
        ZoneData,
        HazardClassification,
        OccupancyType,
        SystemType,
        ComplianceLevel,
        ValidationResult,
        ProjectResult
    )
    STANDARDS_ENGINE_AVAILABLE = True
    logger.info("✅ Standards engine loaded (790+ NFPA rules)")
except ImportError as e:
    logger.warning(f"⚠️ Standards engine not available: {e}")
    # Define fallback enums
    class HazardClassification(Enum):
        LIGHT_HAZARD = "light_hazard"
        ORDINARY_HAZARD_GROUP_1 = "ordinary_hazard_group_1"
        ORDINARY_HAZARD_GROUP_2 = "ordinary_hazard_group_2"
        EXTRA_HAZARD_GROUP_1 = "extra_hazard_group_1"
        EXTRA_HAZARD_GROUP_2 = "extra_hazard_group_2"
    
    class ComplianceLevel(Enum):
        COMPLIANT = "compliant"
        NON_COMPLIANT = "non_compliant"
        REQUIRES_REVIEW = "requires_review"

# Import other libraries
try:
    import ezdxf
    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False
    logger.warning("⚠️ ezdxf not available")

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.enums import TA_CENTER
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    logger.warning("⚠️ reportlab not available")


# =============================================================================
# NFPA 13 DESIGN PARAMETERS
# =============================================================================

HAZARD_REQUIREMENTS = {
    'light_hazard': {
        'max_coverage_sqft': 225, 'max_spacing_ft': 15, 'min_spacing_ft': 6,
        'density_gpm_sqft': 0.10, 'design_area_sqft': 1500, 'hose_stream_gpm': 100, 'duration_min': 30
    },
    'ordinary_hazard_group_1': {
        'max_coverage_sqft': 130, 'max_spacing_ft': 15, 'min_spacing_ft': 6,
        'density_gpm_sqft': 0.15, 'design_area_sqft': 1500, 'hose_stream_gpm': 250, 'duration_min': 60
    },
    'ordinary_hazard_group_2': {
        'max_coverage_sqft': 130, 'max_spacing_ft': 15, 'min_spacing_ft': 6,
        'density_gpm_sqft': 0.20, 'design_area_sqft': 1500, 'hose_stream_gpm': 250, 'duration_min': 60
    },
    'extra_hazard_group_1': {
        'max_coverage_sqft': 100, 'max_spacing_ft': 12, 'min_spacing_ft': 6,
        'density_gpm_sqft': 0.30, 'design_area_sqft': 2500, 'hose_stream_gpm': 500, 'duration_min': 90
    },
    'extra_hazard_group_2': {
        'max_coverage_sqft': 100, 'max_spacing_ft': 12, 'min_spacing_ft': 6,
        'density_gpm_sqft': 0.40, 'design_area_sqft': 2500, 'hose_stream_gpm': 500, 'duration_min': 120
    }
}

HANGER_SPACING = {1.0: 12, 1.25: 12, 1.5: 12, 2.0: 12, 2.5: 12, 3.0: 15, 4.0: 15, 6.0: 15, 8.0: 15}

PIPE_DATA = {
    1.0: {'id': 1.049}, 1.25: {'id': 1.380}, 1.5: {'id': 1.610}, 2.0: {'id': 2.067},
    2.5: {'id': 2.469}, 3.0: {'id': 3.068}, 4.0: {'id': 4.026}, 6.0: {'id': 6.065}
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Sprinkler:
    id: str
    x: float
    y: float
    z: float
    k_factor: float = 5.6
    temp_rating: int = 165
    coverage_sqft: float = 130
    flow_gpm: float = 0.0
    pressure_psi: float = 0.0


@dataclass
class Pipe:
    id: str
    start_x: float
    start_y: float
    start_z: float
    end_x: float
    end_y: float
    end_z: float
    diameter: float
    pipe_type: str  # riser, main, branch
    length: float = 0.0
    flow_gpm: float = 0.0
    velocity_fps: float = 0.0
    pressure_loss_psi: float = 0.0
    
    def __post_init__(self):
        if self.length == 0:
            self.length = math.sqrt(
                (self.end_x - self.start_x)**2 + 
                (self.end_y - self.start_y)**2 + 
                (self.end_z - self.start_z)**2
            )


@dataclass
class Fitting:
    id: str
    x: float
    y: float
    z: float
    fitting_type: str
    size: float


@dataclass
class Valve:
    id: str
    x: float
    y: float
    z: float
    valve_type: str
    size: float


@dataclass
class Hanger:
    id: str
    x: float
    y: float
    z: float
    pipe_size: float


@dataclass
class Brace:
    id: str
    x: float
    y: float
    z: float
    brace_type: str
    pipe_size: float


@dataclass
class SystemDesign:
    project_id: str
    project_name: str
    hazard_class: str
    building_area: float
    ceiling_height: float
    zip_code: str = ""
    
    sprinklers: List[Sprinkler] = field(default_factory=list)
    pipes: List[Pipe] = field(default_factory=list)
    fittings: List[Fitting] = field(default_factory=list)
    valves: List[Valve] = field(default_factory=list)
    hangers: List[Hanger] = field(default_factory=list)
    braces: List[Brace] = field(default_factory=list)
    
    # Hydraulics
    system_demand_gpm: float = 0.0
    system_pressure_psi: float = 0.0
    
    # Compliance (from standards engine)
    compliance_score: float = 0.0
    is_compliant: bool = False
    validation_results: List[Any] = field(default_factory=list)
    critical_violations: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    jurisdiction_info: Optional[Any] = None


# =============================================================================
# SPRINKLER SYSTEM DESIGNER
# =============================================================================

class SprinklerSystemDesigner:
    """Designs sprinkler system per NFPA 13 requirements"""
    
    def __init__(self, hazard_class: str = 'ordinary_hazard_group_1'):
        self.hazard_class = hazard_class
        self.req = HAZARD_REQUIREMENTS.get(hazard_class, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
        self.c_factor = 120  # Hazen-Williams C for steel
    
    def design(self, building_area: float, ceiling_height: float, 
               project_id: str, project_name: str, zip_code: str = "") -> SystemDesign:
        """Design complete sprinkler system"""
        
        design = SystemDesign(
            project_id=project_id,
            project_name=project_name,
            hazard_class=self.hazard_class,
            building_area=building_area,
            ceiling_height=ceiling_height,
            zip_code=zip_code
        )
        
        side = math.sqrt(building_area)
        
        # Design sprinkler layout
        design.sprinklers = self._design_sprinklers(side, ceiling_height)
        
        # Design pipe network
        design.pipes = self._design_pipes(design.sprinklers, side, ceiling_height)
        
        # Calculate hydraulics
        self._calculate_hydraulics(design)
        
        # Generate fittings
        design.fittings = self._generate_fittings(design.pipes, design.sprinklers)
        
        # Generate hangers
        design.hangers = self._generate_hangers(design.pipes)
        
        # Generate valves
        design.valves = self._generate_valves(ceiling_height)
        
        # Generate bracing
        design.braces = self._generate_bracing(design.pipes)
        
        return design
    
    def _design_sprinklers(self, side: float, ceiling_height: float) -> List[Sprinkler]:
        """Design sprinkler layout per NFPA 13"""
        sprinklers = []
        
        # Calculate spacing (80% of max for margin)
        spacing = min(self.req['max_spacing_ft'] * 0.80, math.sqrt(self.req['max_coverage_sqft'] * 0.85))
        coverage = spacing * spacing
        
        # Start offset (half spacing from walls per NFPA 13 8.5.2.1)
        offset = spacing / 2
        
        num_x = int((side - offset) / spacing) + 1
        num_y = int((side - offset) / spacing) + 1
        
        sprinkler_z = ceiling_height - 0.5  # 6" below ceiling
        
        counter = 1
        for i in range(num_x):
            for j in range(num_y):
                x = min(offset + i * spacing, side - 1)
                y = min(offset + j * spacing, side - 1)
                
                sprinklers.append(Sprinkler(
                    id=f"SP-{counter:03d}",
                    x=x, y=y, z=sprinkler_z,
                    k_factor=5.6,
                    temp_rating=165,
                    coverage_sqft=coverage
                ))
                counter += 1
        
        return sprinklers
    
    def _design_pipes(self, sprinklers: List[Sprinkler], side: float, ceiling_height: float) -> List[Pipe]:
        """Design tree-type pipe network"""
        pipes = []
        counter = 1
        
        pipe_z = ceiling_height - 1.0
        riser_x, riser_y = 5.0, 5.0
        
        # Riser
        pipes.append(Pipe(
            id=f"P-{counter:03d}-RISER", pipe_type="riser",
            start_x=riser_x, start_y=riser_y, start_z=0,
            end_x=riser_x, end_y=riser_y, end_z=pipe_z,
            diameter=4.0
        ))
        counter += 1
        
        # Feed main
        pipes.append(Pipe(
            id=f"P-{counter:03d}-MAIN", pipe_type="main",
            start_x=riser_x, start_y=riser_y, start_z=pipe_z,
            end_x=side - 5, end_y=riser_y, end_z=pipe_z,
            diameter=4.0
        ))
        counter += 1
        
        # Branch lines based on sprinkler positions
        if sprinklers:
            unique_x = sorted(set(round(s.x, 1) for s in sprinklers))
            
            for bx in unique_x:
                branch_spks = [s for s in sprinklers if abs(s.x - bx) < 2]
                if branch_spks:
                    min_y = min(s.y for s in branch_spks)
                    max_y = max(s.y for s in branch_spks)
                    
                    # Size based on sprinkler count
                    num_spk = len(branch_spks)
                    if num_spk <= 2:
                        dia = 1.0
                    elif num_spk <= 4:
                        dia = 1.25
                    elif num_spk <= 6:
                        dia = 1.5
                    elif num_spk <= 10:
                        dia = 2.0
                    else:
                        dia = 2.5
                    
                    # Cross main connection
                    pipes.append(Pipe(
                        id=f"P-{counter:03d}-XMAIN", pipe_type="cross_main",
                        start_x=bx, start_y=riser_y, start_z=pipe_z,
                        end_x=bx, end_y=riser_y, end_z=pipe_z,
                        diameter=3.0, length=0.5
                    ))
                    counter += 1
                    
                    # Branch line
                    pipes.append(Pipe(
                        id=f"P-{counter:03d}-BR", pipe_type="branch",
                        start_x=bx, start_y=riser_y, start_z=pipe_z,
                        end_x=bx, end_y=max_y + 2, end_z=pipe_z,
                        diameter=dia
                    ))
                    counter += 1
        
        return pipes
    
    def _calculate_hydraulics(self, design: SystemDesign):
        """Calculate hydraulics using Hazen-Williams"""
        density = self.req['density_gpm_sqft']
        hose_stream = self.req['hose_stream_gpm']
        
        # Calculate sprinkler flows
        sprinklers_in_area = min(len(design.sprinklers), 
                                 int(self.req['design_area_sqft'] / self.req['max_coverage_sqft']))
        
        for i, spk in enumerate(design.sprinklers):
            if i < sprinklers_in_area:
                spk.flow_gpm = max(density * spk.coverage_sqft, 15)
                spk.pressure_psi = (spk.flow_gpm / spk.k_factor) ** 2
        
        # Calculate pipe flows and pressure losses
        for pipe in design.pipes:
            if pipe.pipe_type == "riser":
                pipe.flow_gpm = sum(s.flow_gpm for s in design.sprinklers)
            elif pipe.pipe_type == "main":
                pipe.flow_gpm = sum(s.flow_gpm for s in design.sprinklers)
            elif pipe.pipe_type == "branch":
                # Find sprinklers on this branch
                branch_spks = [s for s in design.sprinklers if abs(s.x - pipe.start_x) < 2]
                pipe.flow_gpm = sum(s.flow_gpm for s in branch_spks)
            
            if pipe.diameter > 0 and pipe.length > 0:
                inner_dia = PIPE_DATA.get(pipe.diameter, {'id': pipe.diameter})['id']
                # Hazen-Williams
                pipe.pressure_loss_psi = (4.52 * (pipe.flow_gpm ** 1.85) * pipe.length) / \
                                         ((self.c_factor ** 1.85) * (inner_dia ** 4.87))
                pipe.velocity_fps = 0.4085 * pipe.flow_gpm / (inner_dia ** 2) if inner_dia > 0 else 0
        
        # System totals
        active_spks = [s for s in design.sprinklers if s.flow_gpm > 0]
        design.system_demand_gpm = sum(s.flow_gpm for s in active_spks) + hose_stream
        design.system_pressure_psi = sum(p.pressure_loss_psi for p in design.pipes) + \
                                     max((s.pressure_psi for s in active_spks), default=7)
    
    def _generate_fittings(self, pipes: List[Pipe], sprinklers: List[Sprinkler]) -> List[Fitting]:
        """Generate fittings at connections"""
        fittings = []
        counter = 1
        
        # Tee at branch connections
        for pipe in pipes:
            if pipe.pipe_type == "branch":
                fittings.append(Fitting(
                    id=f"F-{counter:03d}", fitting_type="tee",
                    x=pipe.start_x, y=pipe.start_y, z=pipe.start_z,
                    size=3.0
                ))
                counter += 1
        
        # Elbow at riser top
        riser = next((p for p in pipes if p.pipe_type == "riser"), None)
        if riser:
            fittings.append(Fitting(
                id=f"F-{counter:03d}", fitting_type="elbow_90",
                x=riser.end_x, y=riser.end_y, z=riser.end_z,
                size=riser.diameter
            ))
            counter += 1
        
        # Tees for sprinkler drops
        for spk in sprinklers:
            branch = next((p for p in pipes if p.pipe_type == "branch" and abs(p.start_x - spk.x) < 2), None)
            if branch:
                fittings.append(Fitting(
                    id=f"F-{counter:03d}", fitting_type="tee",
                    x=spk.x, y=spk.y, z=branch.start_z,
                    size=branch.diameter
                ))
                counter += 1
        
        return fittings
    
    def _generate_hangers(self, pipes: List[Pipe]) -> List[Hanger]:
        """Generate hangers per NFPA 13 Section 16"""
        hangers = []
        counter = 1
        
        for pipe in pipes:
            if pipe.pipe_type == "riser":
                continue
            
            max_spacing = HANGER_SPACING.get(pipe.diameter, 12)
            num_hangers = max(1, int(math.ceil(pipe.length / max_spacing)))
            
            for i in range(num_hangers):
                frac = (i + 0.5) / num_hangers
                hangers.append(Hanger(
                    id=f"H-{counter:03d}",
                    x=pipe.start_x + (pipe.end_x - pipe.start_x) * frac,
                    y=pipe.start_y + (pipe.end_y - pipe.start_y) * frac,
                    z=pipe.start_z,
                    pipe_size=pipe.diameter
                ))
                counter += 1
        
        return hangers
    
    def _generate_valves(self, ceiling_height: float) -> List[Valve]:
        """Generate valves per NFPA 13 Chapter 12"""
        valves = []
        rx, ry = 5.0, 5.0
        
        valves.append(Valve(id="V-001", valve_type="os_y_gate", x=rx, y=ry, z=2.0, size=4.0))
        valves.append(Valve(id="V-002", valve_type="alarm_check", x=rx, y=ry, z=3.0, size=4.0))
        valves.append(Valve(id="V-003", valve_type="flow_switch", x=rx, y=ry, z=4.0, size=4.0))
        valves.append(Valve(id="V-004", valve_type="main_drain", x=rx+1, y=ry, z=1.5, size=2.0))
        valves.append(Valve(id="V-005", valve_type="inspector_test", x=50, y=50, z=ceiling_height-1, size=1.0))
        valves.append(Valve(id="V-006", valve_type="fdc", x=rx-3, y=ry, z=3.0, size=4.0))
        
        return valves
    
    def _generate_bracing(self, pipes: List[Pipe]) -> List[Brace]:
        """Generate seismic bracing per NFPA 13 Chapter 18"""
        braces = []
        counter = 1
        
        lateral_spacing = 40.0
        longitudinal_spacing = 80.0
        
        for pipe in pipes:
            if pipe.diameter < 2.5:
                continue
            
            # Lateral braces
            num_lat = max(1, int(math.ceil(pipe.length / lateral_spacing)))
            for i in range(num_lat):
                frac = (i + 0.5) / num_lat
                braces.append(Brace(
                    id=f"B-{counter:03d}", brace_type="lateral",
                    x=pipe.start_x + (pipe.end_x - pipe.start_x) * frac,
                    y=pipe.start_y + (pipe.end_y - pipe.start_y) * frac,
                    z=pipe.start_z, pipe_size=pipe.diameter
                ))
                counter += 1
            
            # Longitudinal for mains
            if pipe.pipe_type in ["main", "riser"]:
                num_long = max(1, int(math.ceil(pipe.length / longitudinal_spacing)))
                for i in range(num_long):
                    frac = (i + 0.5) / num_long
                    braces.append(Brace(
                        id=f"B-{counter:03d}", brace_type="longitudinal",
                        x=pipe.start_x + (pipe.end_x - pipe.start_x) * frac,
                        y=pipe.start_y + (pipe.end_y - pipe.start_y) * frac,
                        z=pipe.start_z, pipe_size=pipe.diameter
                    ))
                    counter += 1
            
            # 4-way at riser
            if pipe.pipe_type == "riser":
                braces.append(Brace(
                    id=f"B-{counter:03d}", brace_type="four_way",
                    x=pipe.end_x, y=pipe.end_y, z=pipe.end_z,
                    pipe_size=pipe.diameter
                ))
                counter += 1
        
        return braces


# =============================================================================
# STANDARDS ENGINE INTEGRATION
# =============================================================================

class ComplianceChecker:
    """Integrates with EnhancedFireAIProMaster for compliance checking"""
    
    def __init__(self):
        self.master = None
        if STANDARDS_ENGINE_AVAILABLE:
            try:
                self.master = EnhancedFireAIProMaster()
                logger.info("✅ Standards engine initialized")
            except Exception as e:
                logger.warning(f"⚠️ Could not initialize standards engine: {e}")
    
    def check_compliance(self, design: SystemDesign) -> SystemDesign:
        """Run compliance check through standards engine"""
        
        if not self.master:
            logger.warning("Standards engine not available, using basic compliance check")
            return self._basic_compliance_check(design)
        
        try:
            # Convert design to FireProtectionProject format
            project_data = self._convert_to_project_data(design)
            
            # Run comprehensive analysis
            result = self.master.analyze_project_comprehensive(
                project_data=project_data,
                zip_code=design.zip_code if design.zip_code else None,
                include_standards=['NFPA_13']  # Primary standard for sprinklers
            )
            
            # Extract results
            design.compliance_score = result.overall_compliance_score
            design.is_compliant = result.overall_compliance_score >= 80 and len(result.critical_violations) == 0
            design.validation_results = result.all_validation_results
            design.critical_violations = [
                f"{v.rule_id}: {v.notes}" for v in result.critical_violations
            ]
            design.recommendations = result.recommendations
            design.jurisdiction_info = result.jurisdiction_info
            
            logger.info(f"✅ Compliance check complete: {design.compliance_score:.1f}% score")
            if result.jurisdiction_info:
                logger.info(f"   Jurisdiction: {result.jurisdiction_info.city}, {result.jurisdiction_info.state_code}")
            
            return design
            
        except Exception as e:
            logger.error(f"Standards engine error: {e}, falling back to basic check")
            return self._basic_compliance_check(design)
    
    def _convert_to_project_data(self, design: SystemDesign) -> Dict[str, Any]:
        """Convert SystemDesign to FireProtectionProject format"""
        
        # Map hazard class string to enum value
        hazard_map = {
            'light_hazard': 'light_hazard',
            'ordinary_hazard_1': 'ordinary_hazard_group_1',
            'ordinary_hazard_group_1': 'ordinary_hazard_group_1',
            'ordinary_hazard_2': 'ordinary_hazard_group_2',
            'ordinary_hazard_group_2': 'ordinary_hazard_group_2',
            'extra_hazard_1': 'extra_hazard_group_1',
            'extra_hazard_group_1': 'extra_hazard_group_1',
            'extra_hazard_2': 'extra_hazard_group_2',
            'extra_hazard_group_2': 'extra_hazard_group_2'
        }
        
        hazard_class = hazard_map.get(design.hazard_class, 'ordinary_hazard_group_1')
        req = HAZARD_REQUIREMENTS.get(hazard_class, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
        
        # Calculate spacing from sprinkler positions
        spacing_x = spacing_y = 12.0
        if len(design.sprinklers) >= 2:
            x_vals = sorted(set(s.x for s in design.sprinklers))
            y_vals = sorted(set(s.y for s in design.sprinklers))
            if len(x_vals) >= 2:
                spacing_x = x_vals[1] - x_vals[0]
            if len(y_vals) >= 2:
                spacing_y = y_vals[1] - y_vals[0]
        
        return {
            'project_id': design.project_id,
            'project_name': design.project_name,
            'building_area': design.building_area,
            'building_height': design.ceiling_height,
            'stories': 1,
            'construction_type': 'Type II-B',
            'hazard_classification': hazard_class,
            'design_density': req['density_gpm_sqft'],
            'design_area': req['design_area_sqft'],
            'ceiling_height': design.ceiling_height,
            'sprinkler_spacing_x': spacing_x,
            'sprinkler_spacing_y': spacing_y,
            'sprinkler_required': True,
            'system_types': ['wet_pipe_sprinkler'],
            'water_supply_static_pressure': 80,
            'water_supply_flow_pressure': 65,
            'water_supply_flow_rate': design.system_demand_gpm * 1.2,
            'zones': [{
                'zone_id': 'zone_1',
                'zone_name': 'Main Area',
                'area': design.building_area,
                'hazard_classification': hazard_class,
                'occupancy_type': 'business_b',
                'ceiling_height': design.ceiling_height,
                'sprinkler_spacing_x': spacing_x,
                'sprinkler_spacing_y': spacing_y,
                'design_density': req['density_gpm_sqft']
            }]
        }
    
    def _basic_compliance_check(self, design: SystemDesign) -> SystemDesign:
        """Basic compliance check when standards engine unavailable"""
        violations = []
        
        req = HAZARD_REQUIREMENTS.get(design.hazard_class, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
        
        # Check sprinkler spacing
        for i, s1 in enumerate(design.sprinklers):
            for s2 in design.sprinklers[i+1:]:
                dist = math.sqrt((s1.x - s2.x)**2 + (s1.y - s2.y)**2)
                if dist < req['min_spacing_ft']:
                    violations.append(f"Sprinkler spacing violation: {s1.id} and {s2.id} at {dist:.1f}' (min {req['min_spacing_ft']}')")
        
        # Check pipe velocity
        for pipe in design.pipes:
            if pipe.velocity_fps > 32 and pipe.length > 1:
                violations.append(f"Velocity violation: {pipe.id} at {pipe.velocity_fps:.1f} fps (max 32 fps)")
        
        # Check coverage
        total_coverage = sum(s.coverage_sqft for s in design.sprinklers)
        if total_coverage < design.building_area * 0.98:
            violations.append(f"Coverage violation: {total_coverage:.0f} sq ft vs {design.building_area:.0f} sq ft building")
        
        design.is_compliant = len(violations) == 0
        design.compliance_score = 100.0 if design.is_compliant else max(0, 100 - len(violations) * 10)
        design.critical_violations = violations
        
        return design


# =============================================================================
# OUTPUT GENERATORS
# =============================================================================

def generate_dxf(design: SystemDesign, output_path: str) -> bool:
    """Generate detailed DXF drawing"""
    if not EZDXF_AVAILABLE:
        logger.error("ezdxf not available")
        return False
    
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # Layers
    layers = {
        'FIRE-PIPE-RISER': 1, 'FIRE-PIPE-MAIN': 1, 'FIRE-PIPE-BRANCH': 1,
        'FIRE-SPRINKLER': 4, 'FIRE-FITTING': 6, 'FIRE-VALVE': 3,
        'FIRE-HANGER': 8, 'FIRE-BRACE': 5, 'FIRE-TEXT': 7, 'FIRE-BORDER': 7
    }
    for name, color in layers.items():
        doc.layers.add(name, color=color)
    
    # Draw pipes
    for pipe in design.pipes:
        if pipe.pipe_type == "riser":
            msp.add_circle((pipe.start_x, pipe.start_y), radius=1.5, dxfattribs={'layer': 'FIRE-PIPE-RISER'})
            msp.add_line((pipe.start_x-1, pipe.start_y-1), (pipe.start_x+1, pipe.start_y+1), dxfattribs={'layer': 'FIRE-PIPE-RISER'})
            msp.add_line((pipe.start_x-1, pipe.start_y+1), (pipe.start_x+1, pipe.start_y-1), dxfattribs={'layer': 'FIRE-PIPE-RISER'})
        else:
            layer = 'FIRE-PIPE-MAIN' if pipe.pipe_type in ["main", "cross_main"] else 'FIRE-PIPE-BRANCH'
            msp.add_line((pipe.start_x, pipe.start_y), (pipe.end_x, pipe.end_y), dxfattribs={'layer': layer})
            mid_x, mid_y = (pipe.start_x + pipe.end_x) / 2, (pipe.start_y + pipe.end_y) / 2
            msp.add_text(f'{pipe.diameter}"', dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.8}).set_placement((mid_x, mid_y + 1))
    
    # Draw sprinklers
    for spk in design.sprinklers:
        msp.add_circle((spk.x, spk.y), radius=0.8, dxfattribs={'layer': 'FIRE-SPRINKLER'})
        msp.add_line((spk.x-0.5, spk.y), (spk.x+0.5, spk.y), dxfattribs={'layer': 'FIRE-SPRINKLER'})
        msp.add_line((spk.x, spk.y-0.5), (spk.x, spk.y+0.5), dxfattribs={'layer': 'FIRE-SPRINKLER'})
        msp.add_text(spk.id, dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.5}).set_placement((spk.x+1.2, spk.y))
    
    # Draw fittings
    for fit in design.fittings:
        if fit.fitting_type == "tee":
            msp.add_line((fit.x-0.5, fit.y), (fit.x+0.5, fit.y), dxfattribs={'layer': 'FIRE-FITTING'})
            msp.add_line((fit.x, fit.y-0.5), (fit.x, fit.y+0.5), dxfattribs={'layer': 'FIRE-FITTING'})
            msp.add_circle((fit.x, fit.y), radius=0.2, dxfattribs={'layer': 'FIRE-FITTING'})
        elif fit.fitting_type == "elbow_90":
            msp.add_arc((fit.x, fit.y), radius=0.4, start_angle=0, end_angle=90, dxfattribs={'layer': 'FIRE-FITTING'})
    
    # Draw valves
    valve_labels = {"os_y_gate": "OS&Y", "alarm_check": "ACV", "flow_switch": "FS", 
                   "main_drain": "MD", "inspector_test": "IT", "fdc": "FDC"}
    for v in design.valves:
        msp.add_lwpolyline([(v.x, v.y+0.6), (v.x+0.6, v.y), (v.x, v.y-0.6), (v.x-0.6, v.y), (v.x, v.y+0.6)], 
                          dxfattribs={'layer': 'FIRE-VALVE'})
        msp.add_text(valve_labels.get(v.valve_type, "V"), dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.4}).set_placement((v.x+1, v.y))
    
    # Draw hangers
    for h in design.hangers:
        msp.add_lwpolyline([(h.x-0.3, h.y+0.3), (h.x+0.3, h.y+0.3), (h.x, h.y)], dxfattribs={'layer': 'FIRE-HANGER'})
    
    # Draw braces
    brace_labels = {"lateral": "L", "longitudinal": "LG", "four_way": "4W"}
    for b in design.braces:
        msp.add_circle((b.x, b.y), radius=0.4, dxfattribs={'layer': 'FIRE-BRACE'})
        msp.add_text(brace_labels.get(b.brace_type, "B"), dxfattribs={'layer': 'FIRE-BRACE', 'height': 0.3}).set_placement((b.x-0.15, b.y-0.12))
    
    # Legend
    lx, ly = -35, 0
    msp.add_text("SYMBOL LEGEND", dxfattribs={'layer': 'FIRE-BORDER', 'height': 1.0}).set_placement((lx, ly))
    legend_items = [("Sprinkler Head", -3), ("Fire Pipe", -6), ("Tee Fitting", -9), 
                    ("Valve", -12), ("Hanger", -15), ("Seismic Brace", -18)]
    for text, offset in legend_items:
        msp.add_text(text, dxfattribs={'layer': 'FIRE-TEXT', 'height': 0.6}).set_placement((lx+4, ly+offset))
    
    # Title block
    tbx, tby = -35, -30
    msp.add_lwpolyline([(tbx, tby), (tbx+50, tby), (tbx+50, tby+12), (tbx, tby+12), (tbx, tby)], dxfattribs={'layer': 'FIRE-BORDER'})
    msp.add_text(design.project_name, dxfattribs={'layer': 'FIRE-BORDER', 'height': 1.2}).set_placement((tbx+2, tby+9))
    msp.add_text(f"Project: {design.project_id}", dxfattribs={'layer': 'FIRE-BORDER', 'height': 0.8}).set_placement((tbx+2, tby+6))
    
    compliance_text = f"COMPLIANT ({design.compliance_score:.0f}%)" if design.is_compliant else f"NON-COMPLIANT ({design.compliance_score:.0f}%)"
    msp.add_text(f"Compliance: {compliance_text}", dxfattribs={'layer': 'FIRE-BORDER', 'height': 0.6}).set_placement((tbx+2, tby+4))
    msp.add_text(f"Demand: {design.system_demand_gpm:.0f} GPM @ {design.system_pressure_psi:.1f} PSI", 
                 dxfattribs={'layer': 'FIRE-BORDER', 'height': 0.5}).set_placement((tbx+2, tby+2))
    
    pipe_length = sum(p.length for p in design.pipes)
    msp.add_text(f"Sprinklers: {len(design.sprinklers)} | Pipe: {pipe_length:.0f} LF | Fittings: {len(design.fittings)}", 
                 dxfattribs={'layer': 'FIRE-BORDER', 'height': 0.5}).set_placement((tbx+2, tby+0.5))
    
    doc.saveas(output_path)
    logger.info(f"DXF saved: {output_path}")
    return True


def generate_pdf(design: SystemDesign, output_path: str) -> bool:
    """Generate comprehensive PDF compliance report"""
    if not REPORTLAB_AVAILABLE:
        logger.error("reportlab not available")
        return False
    
    doc = SimpleDocTemplate(output_path, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, alignment=TA_CENTER)
    story = []
    
    # Title
    story.append(Paragraph("FIRE SPRINKLER SYSTEM", title_style))
    story.append(Paragraph("COMPLIANCE REPORT", title_style))
    story.append(Spacer(1, 20))
    
    # Project info
    story.append(Paragraph("PROJECT INFORMATION", styles['Heading2']))
    info = [
        ["Project Name:", design.project_name],
        ["Project ID:", design.project_id],
        ["Hazard Class:", design.hazard_class.replace('_', ' ').title()],
        ["Building Area:", f"{design.building_area:,.0f} sq ft"],
        ["Ceiling Height:", f"{design.ceiling_height} ft"]
    ]
    if design.zip_code:
        info.append(["ZIP Code:", design.zip_code])
    if design.jurisdiction_info:
        info.append(["Jurisdiction:", f"{design.jurisdiction_info.city}, {design.jurisdiction_info.state_code}"])
    
    t = Table(info, colWidths=[2.5*inch, 4*inch])
    t.setStyle(TableStyle([('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold')]))
    story.append(t)
    story.append(Spacer(1, 20))
    
    # Compliance Status
    story.append(Paragraph("COMPLIANCE STATUS", styles['Heading2']))
    status_color = colors.green if design.is_compliant else colors.red
    status_text = "COMPLIANT" if design.is_compliant else "NON-COMPLIANT"
    
    status_data = [
        ["Overall Status:", status_text],
        ["Compliance Score:", f"{design.compliance_score:.1f}%"],
        ["Rules Evaluated:", str(len(design.validation_results)) if design.validation_results else "N/A"],
        ["Critical Violations:", str(len(design.critical_violations))]
    ]
    st = Table(status_data, colWidths=[2.5*inch, 4*inch])
    st.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (1, 0), (1, 0), status_color),
        ('TEXTCOLOR', (1, 0), (1, 0), colors.white)
    ]))
    story.append(st)
    story.append(Spacer(1, 15))
    
    # Critical violations
    if design.critical_violations:
        story.append(Paragraph("CRITICAL VIOLATIONS", styles['Heading3']))
        for v in design.critical_violations[:10]:
            story.append(Paragraph(f"❌ {v}", styles['Normal']))
        story.append(Spacer(1, 15))
    
    # Recommendations
    if design.recommendations:
        story.append(Paragraph("RECOMMENDATIONS", styles['Heading3']))
        for r in design.recommendations[:10]:
            story.append(Paragraph(f"• {r}", styles['Normal']))
        story.append(Spacer(1, 15))
    
    # System summary
    story.append(Paragraph("SYSTEM SUMMARY", styles['Heading2']))
    pipe_length = sum(p.length for p in design.pipes)
    summary_data = [
        ["Component", "Quantity", "Notes"],
        ["Sprinklers", str(len(design.sprinklers)), f"K={design.sprinklers[0].k_factor if design.sprinklers else 'N/A'}"],
        ["Pipe", f"{pipe_length:.0f} LF", "Schedule 40 Black Steel"],
        ["Fittings", str(len(design.fittings)), "Malleable Iron"],
        ["Valves", str(len(design.valves)), "Per NFPA 13 Ch. 12"],
        ["Hangers", str(len(design.hangers)), "Per NFPA 13 Sec. 16"],
        ["Seismic Braces", str(len(design.braces)), "Per NFPA 13 Ch. 18"]
    ]
    st = Table(summary_data, colWidths=[2*inch, 1.5*inch, 2.5*inch])
    st.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(st)
    story.append(Spacer(1, 20))
    
    # Hydraulic summary
    story.append(Paragraph("HYDRAULIC SUMMARY", styles['Heading2']))
    req = HAZARD_REQUIREMENTS.get(design.hazard_class, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
    hyd_data = [
        ["Parameter", "Value"],
        ["System Demand", f"{design.system_demand_gpm:.0f} GPM"],
        ["System Pressure", f"{design.system_pressure_psi:.1f} PSI"],
        ["Design Density", f"{req['density_gpm_sqft']} GPM/sq ft"],
        ["Hose Stream Allowance", f"{req['hose_stream_gpm']} GPM"],
        ["Duration", f"{req['duration_min']} minutes"]
    ]
    ht = Table(hyd_data, colWidths=[3*inch, 3*inch])
    ht.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(ht)
    
    doc.build(story)
    logger.info(f"PDF saved: {output_path}")
    return True


def generate_bom(design: SystemDesign, output_path: str):
    """Generate bill of materials CSV"""
    with open(output_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(["Item", "Description", "Size", "Material", "Qty", "Unit", "NFPA Ref"])
        
        item = 1
        
        # Sprinklers
        w.writerow([item, "Sprinkler Head, Pendant, QR, K5.6, 165F", '1/2"', "Brass/Chrome", 
                    len(design.sprinklers), "EA", "Sec 8.5"])
        item += 1
        
        # Pipes
        pipe_groups = {}
        for p in design.pipes:
            pipe_groups[p.diameter] = pipe_groups.get(p.diameter, 0) + p.length
        for dia, length in sorted(pipe_groups.items()):
            w.writerow([item, "Pipe, Schedule 40, Black Steel", f'{dia}"', "Steel", 
                        round(length, 1), "LF", "Ch 22"])
            item += 1
        
        # Fittings
        fit_groups = {}
        for f in design.fittings:
            key = (f.fitting_type, f.size)
            fit_groups[key] = fit_groups.get(key, 0) + 1
        for (ftype, size), qty in fit_groups.items():
            w.writerow([item, ftype.replace('_', ' ').title(), f'{size}"', "Malleable Iron", qty, "EA", "Ch 22"])
            item += 1
        
        # Valves
        for v in design.valves:
            w.writerow([item, v.valve_type.replace('_', ' ').title(), f'{v.size}"', "Various", 1, "EA", "Ch 12"])
            item += 1
        
        # Hangers
        hanger_groups = {}
        for h in design.hangers:
            hanger_groups[h.pipe_size] = hanger_groups.get(h.pipe_size, 0) + 1
        for psize, qty in hanger_groups.items():
            w.writerow([item, "Clevis Hanger", f'{psize}" pipe', "Steel/Zinc", qty, "EA", "Sec 16.4"])
            item += 1
        
        # Braces
        brace_groups = {}
        for b in design.braces:
            brace_groups[b.brace_type] = brace_groups.get(b.brace_type, 0) + 1
        for btype, qty in brace_groups.items():
            w.writerow([item, f"Seismic Brace, {btype.replace('_', ' ').title()}", "Per Design", "Steel", qty, "EA", "Ch 18"])
            item += 1
    
    logger.info(f"BOM saved: {output_path}")


# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def orchestrate(project_dir: str, output_dir: str) -> Dict[str, Any]:
    """Main orchestration function"""
    logger.info("🔥 FireAI Pro Orchestrator v4.0 - Standards Engine Integration")
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
    
    # Step 1: Design the system
    logger.info("Step 1: Designing sprinkler system...")
    designer = SprinklerSystemDesigner(project_data.get('hazard_class', 'ordinary_hazard_group_1'))
    design = designer.design(
        building_area=project_data.get('building_area_sqft', 10000),
        ceiling_height=project_data.get('ceiling_height_ft', 12),
        project_id=project_data.get('project_id'),
        project_name=project_data.get('project_name', 'Fire Sprinkler Project'),
        zip_code=project_data.get('zip_code', '')
    )
    
    logger.info(f"  Sprinklers: {len(design.sprinklers)}")
    logger.info(f"  Pipes: {len(design.pipes)} ({sum(p.length for p in design.pipes):.0f} LF)")
    logger.info(f"  Fittings: {len(design.fittings)}")
    logger.info(f"  Demand: {design.system_demand_gpm:.0f} GPM @ {design.system_pressure_psi:.1f} PSI")
    
    # Step 2: Run compliance check through standards engine
    logger.info("Step 2: Running compliance check through standards engine...")
    checker = ComplianceChecker()
    design = checker.check_compliance(design)
    
    logger.info(f"  Compliance Score: {design.compliance_score:.1f}%")
    logger.info(f"  Status: {'✅ COMPLIANT' if design.is_compliant else '❌ NON-COMPLIANT'}")
    if design.critical_violations:
        logger.info(f"  Critical Violations: {len(design.critical_violations)}")
    
    # Step 3: Generate outputs
    logger.info("Step 3: Generating output files...")
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
    generate_bom(design, bom_path)
    outputs['bill_of_materials.csv'] = bom_path
    
    # Summary JSON
    summary = {
        'project_id': design.project_id,
        'project_name': design.project_name,
        'hazard_class': design.hazard_class,
        'zip_code': design.zip_code,
        'compliance': {
            'is_compliant': design.is_compliant,
            'score': design.compliance_score,
            'critical_violations': design.critical_violations,
            'recommendations': design.recommendations,
            'rules_evaluated': len(design.validation_results) if design.validation_results else 0
        },
        'system_summary': {
            'sprinklers': len(design.sprinklers),
            'pipe_length_ft': round(sum(p.length for p in design.pipes), 1),
            'fittings': len(design.fittings),
            'valves': len(design.valves),
            'hangers': len(design.hangers),
            'braces': len(design.braces)
        },
        'hydraulics': {
            'system_demand_gpm': round(design.system_demand_gpm, 1),
            'system_pressure_psi': round(design.system_pressure_psi, 1)
        }
    }
    
    if design.jurisdiction_info:
        summary['jurisdiction'] = {
            'city': design.jurisdiction_info.city,
            'state': design.jurisdiction_info.state_code,
            'seismic_zone': design.jurisdiction_info.seismic_zone
        }
    
    summary_path = os.path.join(output_dir, 'summary.json')
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    outputs['summary.json'] = summary_path
    
    elapsed = (datetime.now() - start).total_seconds()
    logger.info(f"🎉 Complete in {elapsed:.2f}s")
    
    return {
        'success': True,
        'project_id': design.project_id,
        'is_compliant': design.is_compliant,
        'compliance_score': design.compliance_score,
        'outputs': outputs,
        'summary': summary
    }


def get_engine_status():
    """Get engine status for health checks"""
    return {
        'standards_engine': STANDARDS_ENGINE_AVAILABLE,
        'ezdxf': EZDXF_AVAILABLE,
        'reportlab': REPORTLAB_AVAILABLE,
        'routing': True,
        'hydraulics': True,
        'codes': STANDARDS_ENGINE_AVAILABLE
    }


if __name__ == "__main__":
    print("🔥 FireAI Pro Orchestrator v4.0")
    print("=" * 50)
    print("Standards Engine Integration")
    print()
    status = get_engine_status()
    for k, v in status.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print("\nReady!")
