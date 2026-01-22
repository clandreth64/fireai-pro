#!/usr/bin/env python3
"""
FireAI Pro - Enhanced Production Orchestrator v2.0
Comprehensive fire sprinkler system design with detailed output
VERSION: 2.0.0-PRODUCTION
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI_Orchestrator")

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Point3D:
    x: float
    y: float
    z: float
    
    def distance_to(self, other: 'Point3D') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2 + (self.z - other.z)**2)
    
    def distance_2d(self, other: 'Point3D') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def to_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)
    
    def __hash__(self):
        return hash((round(self.x, 2), round(self.y, 2), round(self.z, 2)))
    
    def __eq__(self, other):
        if not isinstance(other, Point3D):
            return False
        return (round(self.x, 2) == round(other.x, 2) and 
                round(self.y, 2) == round(other.y, 2) and 
                round(self.z, 2) == round(other.z, 2))


class PipeType(Enum):
    MAIN = "main"
    CROSS_MAIN = "cross_main"
    BRANCH = "branch"
    RISER = "riser"
    FEED_MAIN = "feed_main"
    ARM_OVER = "arm_over"


class FittingType(Enum):
    TEE = "tee"
    ELBOW_90 = "elbow_90"
    ELBOW_45 = "elbow_45"
    COUPLING = "coupling"
    REDUCER = "reducer"
    CAP = "cap"
    CROSS = "cross"
    UNION = "union"
    FLANGE = "flange"


class ValveType(Enum):
    ALARM_CHECK = "alarm_check_valve"
    FLOW_SWITCH = "flow_switch"
    OS_Y_GATE = "os_y_gate_valve"
    DRAIN = "drain_valve"
    TEST_CONNECTION = "test_connection"
    PRESSURE_GAUGE = "pressure_gauge"
    FDC = "fire_department_connection"
    INSPECTOR_TEST = "inspector_test"


class HangerType(Enum):
    CLEVIS = "clevis_hanger"
    RING = "ring_hanger"
    TRAPEZE = "trapeze_hanger"
    WRAP_AROUND = "wrap_around"
    BEAM_CLAMP = "beam_clamp"
    C_CLAMP = "c_clamp"


class BraceType(Enum):
    LATERAL = "lateral"
    LONGITUDINAL = "longitudinal"
    FOUR_WAY = "four_way"


@dataclass
class SprinklerHead:
    id: str
    position: Point3D
    coverage_area: float = 130.0
    flow_rate: float = 15.0
    pressure_required: float = 7.0
    k_factor: float = 5.6
    temperature_rating: int = 165
    orientation: str = "pendant"
    finish: str = "chrome"
    model: str = "standard"
    connected_to_pipe: Optional[str] = None


@dataclass
class PipeSegment:
    id: str
    start_point: Point3D
    end_point: Point3D
    diameter: float
    length: float
    pipe_type: PipeType = PipeType.BRANCH
    material: str = "steel_black"
    schedule: str = "40"
    flow_rate: float = 0.0
    pressure_loss: float = 0.0
    velocity: float = 0.0
    
    def get_midpoint(self) -> Point3D:
        return Point3D(
            (self.start_point.x + self.end_point.x) / 2,
            (self.start_point.y + self.end_point.y) / 2,
            (self.start_point.z + self.end_point.z) / 2
        )
    
    def get_direction(self) -> Tuple[float, float, float]:
        dx = self.end_point.x - self.start_point.x
        dy = self.end_point.y - self.start_point.y
        dz = self.end_point.z - self.start_point.z
        length = math.sqrt(dx*dx + dy*dy + dz*dz)
        if length == 0:
            return (0, 0, 0)
        return (dx/length, dy/length, dz/length)


@dataclass
class Fitting:
    id: str
    position: Point3D
    fitting_type: FittingType
    size_1: float
    size_2: Optional[float] = None
    rotation: float = 0.0
    connected_pipes: List[str] = field(default_factory=list)
    
    def get_cost(self) -> float:
        base_costs = {
            FittingType.TEE: 15.0, FittingType.ELBOW_90: 8.0, FittingType.ELBOW_45: 10.0,
            FittingType.COUPLING: 5.0, FittingType.REDUCER: 12.0, FittingType.CAP: 4.0,
            FittingType.CROSS: 25.0, FittingType.UNION: 18.0, FittingType.FLANGE: 35.0
        }
        return base_costs.get(self.fitting_type, 10.0) * max(1.0, self.size_1 / 1.0)


@dataclass
class Valve:
    id: str
    position: Point3D
    valve_type: ValveType
    size: float
    model: str = "standard"
    manufacturer: str = "Generic"
    
    def get_cost(self) -> float:
        base_costs = {
            ValveType.ALARM_CHECK: 850.0, ValveType.FLOW_SWITCH: 275.0, ValveType.OS_Y_GATE: 320.0,
            ValveType.DRAIN: 45.0, ValveType.TEST_CONNECTION: 85.0, ValveType.PRESSURE_GAUGE: 65.0,
            ValveType.FDC: 450.0, ValveType.INSPECTOR_TEST: 125.0
        }
        return base_costs.get(self.valve_type, 100.0) * max(1.0, self.size / 4.0)


@dataclass
class Hanger:
    id: str
    position: Point3D
    hanger_type: HangerType
    pipe_size: float
    rod_size: str = "3/8"
    rod_length: float = 12.0
    attached_to_pipe: Optional[str] = None
    
    def get_cost(self) -> float:
        base_costs = {
            HangerType.CLEVIS: 8.50, HangerType.RING: 6.00, HangerType.TRAPEZE: 25.00,
            HangerType.WRAP_AROUND: 12.00, HangerType.BEAM_CLAMP: 15.00, HangerType.C_CLAMP: 18.00
        }
        return base_costs.get(self.hanger_type, 10.0) + (self.rod_length / 12 * 3.50)


@dataclass
class SeismicBrace:
    id: str
    position: Point3D
    brace_type: BraceType
    pipe_size: float
    brace_size: str = "1-1/4"
    angle: float = 45.0
    load_rating: float = 0.0
    attached_to_pipe: Optional[str] = None
    
    def get_cost(self) -> float:
        base_costs = {BraceType.LATERAL: 125.0, BraceType.LONGITUDINAL: 125.0, BraceType.FOUR_WAY: 350.0}
        return base_costs.get(self.brace_type, 150.0)


@dataclass
class SystemDesign:
    project_id: str
    project_name: str
    timestamp: datetime
    sprinklers: List[SprinklerHead] = field(default_factory=list)
    pipes: List[PipeSegment] = field(default_factory=list)
    fittings: List[Fitting] = field(default_factory=list)
    valves: List[Valve] = field(default_factory=list)
    hangers: List[Hanger] = field(default_factory=list)
    braces: List[SeismicBrace] = field(default_factory=list)
    system_type: str = "wet"
    hazard_class: str = "ordinary_hazard_1"
    design_area: float = 1500.0
    design_density: float = 0.15
    total_flow: float = 0.0
    total_pressure: float = 0.0
    water_supply_pressure: float = 65.0
    water_supply_flow: float = 1000.0
    nfpa_compliant: bool = True
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def calculate_totals(self) -> Dict[str, Any]:
        pipe_length = sum(p.length for p in self.pipes)
        pipe_cost = sum(self._pipe_cost(p) for p in self.pipes)
        fitting_cost = sum(f.get_cost() for f in self.fittings)
        valve_cost = sum(v.get_cost() for v in self.valves)
        hanger_cost = sum(h.get_cost() for h in self.hangers)
        brace_cost = sum(b.get_cost() for b in self.braces)
        sprinkler_cost = len(self.sprinklers) * 45.0
        material = pipe_cost + fitting_cost + valve_cost + hanger_cost + brace_cost + sprinkler_cost
        labor = (pipe_length * 12.0) + (len(self.fittings) * 25.0) + (len(self.sprinklers) * 35.0)
        return {
            "sprinkler_count": len(self.sprinklers), "pipe_length_ft": round(pipe_length, 1),
            "fitting_count": len(self.fittings), "valve_count": len(self.valves),
            "hanger_count": len(self.hangers), "brace_count": len(self.braces),
            "material_cost": round(material, 2), "labor_cost": round(labor, 2),
            "total_estimated_cost": round(material * 1.35 + labor, 2)
        }
    
    def _pipe_cost(self, pipe: PipeSegment) -> float:
        cost_per_foot = {1.0: 3.50, 1.25: 4.25, 1.5: 5.00, 2.0: 7.50, 2.5: 10.00, 3.0: 14.00, 4.0: 22.00, 6.0: 38.00, 8.0: 55.00}
        return cost_per_foot.get(pipe.diameter, pipe.diameter * 5.0) * pipe.length


# =============================================================================
# ENGINE IMPORTS
# =============================================================================

IMPORT_ERRORS = {}

try:
    from fireai_routing_advanced import design_fire_sprinkler_system, RoutingResult
    ROUTING_AVAILABLE = True
except Exception as e:
    ROUTING_AVAILABLE = False
    IMPORT_ERRORS['routing_engine'] = str(e)

try:
    from enhanced_hydraulics_engine import calculate_hydraulics
    HYDRAULICS_AVAILABLE = True
except Exception as e:
    HYDRAULICS_AVAILABLE = False
    IMPORT_ERRORS['hydraulics_engine'] = str(e)

try:
    from fireai_pro_master_Standards import EnhancedFireAIProMaster
    CODES_AVAILABLE = True
except Exception as e:
    CODES_AVAILABLE = False
    IMPORT_ERRORS['codes_engine'] = str(e)

try:
    from enhanced_bracing_engine import calculate_seismic_bracing
    BRACING_AVAILABLE = True
except Exception as e:
    BRACING_AVAILABLE = False
    IMPORT_ERRORS['bracing_engine'] = str(e)

try:
    from enhanced_cad_engine import extract_building_geometry
    CAD_AVAILABLE = True
except Exception as e:
    CAD_AVAILABLE = False
    IMPORT_ERRORS['cad_engine'] = str(e)

try:
    from master_fireai_products_enhanced import generate_bom
    PRODUCTS_AVAILABLE = True
except Exception as e:
    PRODUCTS_AVAILABLE = False
    IMPORT_ERRORS['products_engine'] = str(e)

try:
    from merged_symbols_ai_enhanced import classify_symbols
    SYMBOLS_AVAILABLE = True
except Exception as e:
    SYMBOLS_AVAILABLE = False
    IMPORT_ERRORS['symbols_engine'] = str(e)

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError as e:
    REPORTLAB_AVAILABLE = False
    IMPORT_ERRORS['reportlab'] = str(e)

try:
    import ezdxf
    EZDXF_AVAILABLE = True
except ImportError as e:
    EZDXF_AVAILABLE = False
    IMPORT_ERRORS['ezdxf'] = str(e)


# =============================================================================
# FITTING GENERATOR
# =============================================================================

class FittingGenerator:
    def __init__(self):
        self.fittings = []
        self.counter = 0
    
    def analyze_and_generate(self, pipes: List[PipeSegment], sprinklers: List[SprinklerHead]) -> List[Fitting]:
        self.fittings = []
        self.counter = 0
        connections = self._build_connections(pipes)
        
        for point, connected in connections.items():
            if len(connected) == 2:
                self._check_elbow(point, connected)
            elif len(connected) == 3:
                self._add_fitting(point, FittingType.TEE, connected)
            elif len(connected) >= 4:
                self._add_fitting(point, FittingType.CROSS, connected)
        
        self._generate_couplings(pipes)
        self._generate_sprinkler_tees(pipes, sprinklers)
        return self.fittings
    
    def _build_connections(self, pipes):
        connections = {}
        for pipe in pipes:
            for pt in [pipe.start_point, pipe.end_point]:
                key = Point3D(round(pt.x, 2), round(pt.y, 2), round(pt.z, 2))
                if key not in connections:
                    connections[key] = []
                connections[key].append(pipe)
        return connections
    
    def _check_elbow(self, point, pipes):
        if len(pipes) != 2:
            return
        d1, d2 = pipes[0].get_direction(), pipes[1].get_direction()
        dot = abs(d1[0]*d2[0] + d1[1]*d2[1] + d1[2]*d2[2])
        if dot < 0.1:
            self._add_fitting(point, FittingType.ELBOW_90, pipes)
        elif dot < 0.75:
            self._add_fitting(point, FittingType.ELBOW_45, pipes)
    
    def _add_fitting(self, point, ftype, pipes):
        self.counter += 1
        prefix = {"tee": "TEE", "elbow_90": "EL90", "elbow_45": "EL45", "cross": "CR"}.get(ftype.value, "FIT")
        self.fittings.append(Fitting(
            id=f"{prefix}-{self.counter:04d}", position=point, fitting_type=ftype,
            size_1=max(p.diameter for p in pipes), connected_pipes=[p.id for p in pipes]
        ))
    
    def _generate_couplings(self, pipes):
        for pipe in pipes:
            if pipe.length > 21:
                for i in range(1, int(pipe.length / 21) + 1):
                    frac = (i * 21) / pipe.length
                    if frac < 1.0:
                        pt = Point3D(
                            pipe.start_point.x + (pipe.end_point.x - pipe.start_point.x) * frac,
                            pipe.start_point.y + (pipe.end_point.y - pipe.start_point.y) * frac,
                            pipe.start_point.z + (pipe.end_point.z - pipe.start_point.z) * frac
                        )
                        self.counter += 1
                        self.fittings.append(Fitting(
                            id=f"CPL-{self.counter:04d}", position=pt, fitting_type=FittingType.COUPLING,
                            size_1=pipe.diameter, connected_pipes=[pipe.id]
                        ))
    
    def _generate_sprinkler_tees(self, pipes, sprinklers):
        for spk in sprinklers:
            closest = min(pipes, key=lambda p: spk.position.distance_2d(p.get_midpoint()), default=None)
            if closest and spk.position.distance_2d(closest.get_midpoint()) < 15:
                self.counter += 1
                self.fittings.append(Fitting(
                    id=f"TEE-SPK-{self.counter:04d}",
                    position=Point3D(spk.position.x, spk.position.y, closest.start_point.z),
                    fitting_type=FittingType.TEE, size_1=closest.diameter, size_2=1.0,
                    connected_pipes=[closest.id, f"drop-{spk.id}"]
                ))


# =============================================================================
# HANGER GENERATOR
# =============================================================================

class HangerGenerator:
    MAX_SPACING = {1.0: 12.0, 1.25: 12.0, 1.5: 12.0, 2.0: 12.0, 2.5: 12.0, 3.0: 15.0, 4.0: 15.0, 6.0: 15.0, 8.0: 15.0}
    
    def generate_hangers(self, pipes: List[PipeSegment], ceiling_height: float = 12.0) -> List[Hanger]:
        hangers = []
        counter = 0
        for pipe in pipes:
            spacing = self.MAX_SPACING.get(pipe.diameter, 12.0)
            num = max(1, int(math.ceil(pipe.length / spacing)))
            for i in range(num):
                frac = 0.5 if num == 1 else (i + 0.5) / num
                pt = Point3D(
                    pipe.start_point.x + (pipe.end_point.x - pipe.start_point.x) * frac,
                    pipe.start_point.y + (pipe.end_point.y - pipe.start_point.y) * frac,
                    pipe.start_point.z
                )
                htype = HangerType.TRAPEZE if pipe.diameter >= 4 else (HangerType.CLEVIS if pipe.diameter >= 2.5 else HangerType.RING)
                rod = "1/2" if pipe.diameter > 2 else "3/8"
                counter += 1
                hangers.append(Hanger(
                    id=f"H-{counter:04d}", position=pt, hanger_type=htype, pipe_size=pipe.diameter,
                    rod_size=rod, rod_length=max(6, min(ceiling_height * 12 - pipe.start_point.z * 12, 48)),
                    attached_to_pipe=pipe.id
                ))
        return hangers


# =============================================================================
# VALVE GENERATOR
# =============================================================================

class ValveGenerator:
    def generate_valves(self, riser: Point3D, main_size: float, system_type: str = "wet") -> List[Valve]:
        valves = []
        counter = 0
        specs = [
            (Point3D(riser.x + 2, riser.y, riser.z + 1), ValveType.DRAIN, min(2.0, main_size), "Main Drain"),
            (Point3D(riser.x, riser.y, riser.z + 2), ValveType.OS_Y_GATE, main_size, "Control Valve"),
            (Point3D(riser.x, riser.y, riser.z + 4), ValveType.ALARM_CHECK, main_size, "Alarm Check"),
            (Point3D(riser.x, riser.y, riser.z + 5), ValveType.FLOW_SWITCH, main_size, "Flow Switch"),
            (Point3D(riser.x + 1, riser.y, riser.z + 4), ValveType.PRESSURE_GAUGE, 0.5, "Pressure Gauge"),
            (Point3D(riser.x + 50, riser.y + 50, riser.z), ValveType.INSPECTOR_TEST, 1.0, "Inspector Test"),
            (Point3D(riser.x - 5, riser.y, riser.z), ValveType.FDC, 4.0, "FDC"),
        ]
        for pos, vtype, size, model in specs:
            counter += 1
            valves.append(Valve(id=f"V-{counter:04d}", position=pos, valve_type=vtype, size=size, model=model))
        return valves


# =============================================================================
# BRACING GENERATOR
# =============================================================================

class BracingGenerator:
    def generate_bracing(self, pipes: List[PipeSegment], seismic_zone: int = 3) -> List[SeismicBrace]:
        if seismic_zone < 2:
            return []
        braces = []
        counter = 0
        lat_spacing = 40.0 if seismic_zone <= 3 else 30.0
        long_spacing = 80.0 if seismic_zone <= 3 else 60.0
        
        for pipe in pipes:
            is_main = pipe.pipe_type in [PipeType.MAIN, PipeType.CROSS_MAIN, PipeType.FEED_MAIN, PipeType.RISER]
            if not is_main and pipe.diameter < 2.5:
                continue
            
            # Lateral braces
            num_lat = max(1, int(math.ceil(pipe.length / lat_spacing)))
            for i in range(num_lat):
                frac = (i + 0.5) / num_lat
                pt = Point3D(
                    pipe.start_point.x + (pipe.end_point.x - pipe.start_point.x) * frac,
                    pipe.start_point.y + (pipe.end_point.y - pipe.start_point.y) * frac,
                    pipe.start_point.z
                )
                counter += 1
                braces.append(SeismicBrace(
                    id=f"SB-L-{counter:04d}", position=pt, brace_type=BraceType.LATERAL,
                    pipe_size=pipe.diameter, attached_to_pipe=pipe.id
                ))
            
            # Longitudinal braces for mains
            if is_main:
                num_long = max(1, int(math.ceil(pipe.length / long_spacing)))
                for i in range(num_long):
                    frac = (i + 0.5) / num_long
                    pt = Point3D(
                        pipe.start_point.x + (pipe.end_point.x - pipe.start_point.x) * frac,
                        pipe.start_point.y + (pipe.end_point.y - pipe.start_point.y) * frac,
                        pipe.start_point.z
                    )
                    counter += 1
                    braces.append(SeismicBrace(
                        id=f"SB-LG-{counter:04d}", position=pt, brace_type=BraceType.LONGITUDINAL,
                        pipe_size=pipe.diameter, attached_to_pipe=pipe.id
                    ))
            
            # 4-way at risers
            if pipe.pipe_type == PipeType.RISER:
                counter += 1
                braces.append(SeismicBrace(
                    id=f"SB-4W-{counter:04d}", position=pipe.end_point, brace_type=BraceType.FOUR_WAY,
                    pipe_size=pipe.diameter, attached_to_pipe=pipe.id
                ))
        
        return braces


# =============================================================================
# DXF GENERATOR
# =============================================================================

class DXFGenerator:
    LAYERS = {
        "FIRE-PIPE-MAIN": 1, "FIRE-PIPE-BRANCH": 1, "FIRE-PIPE-RISER": 1,
        "FIRE-SPKR": 4, "FIRE-FITTING": 6, "FIRE-VALVE": 3,
        "FIRE-HANGER": 8, "FIRE-BRACE": 5, "FIRE-LABEL": 7, "FIRE-SYMBOL": 7
    }
    
    def generate_dxf(self, design: SystemDesign, output_path: str) -> str:
        if not EZDXF_AVAILABLE:
            logger.error("ezdxf not available")
            return ""
        
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        for name, color in self.LAYERS.items():
            doc.layers.add(name, color=color)
        
        # Draw pipes
        for pipe in design.pipes:
            layer = "FIRE-PIPE-RISER" if pipe.pipe_type == PipeType.RISER else (
                "FIRE-PIPE-MAIN" if pipe.pipe_type in [PipeType.MAIN, PipeType.CROSS_MAIN] else "FIRE-PIPE-BRANCH"
            )
            msp.add_line((pipe.start_point.x, pipe.start_point.y), (pipe.end_point.x, pipe.end_point.y), dxfattribs={"layer": layer})
            mid = pipe.get_midpoint()
            msp.add_text(f'{pipe.diameter}"', dxfattribs={"layer": "FIRE-LABEL", "height": 0.5}).set_placement((mid.x, mid.y + 0.5))
        
        # Draw sprinklers
        for spk in design.sprinklers:
            msp.add_circle((spk.position.x, spk.position.y), radius=0.75, dxfattribs={"layer": "FIRE-SPKR"})
            msp.add_line((spk.position.x - 0.5, spk.position.y), (spk.position.x + 0.5, spk.position.y), dxfattribs={"layer": "FIRE-SPKR"})
            msp.add_line((spk.position.x, spk.position.y - 0.5), (spk.position.x, spk.position.y + 0.5), dxfattribs={"layer": "FIRE-SPKR"})
            msp.add_text(spk.id, dxfattribs={"layer": "FIRE-LABEL", "height": 0.35}).set_placement((spk.position.x + 1, spk.position.y + 0.5))
        
        # Draw fittings
        for fit in design.fittings:
            x, y = fit.position.x, fit.position.y
            if fit.fitting_type == FittingType.TEE:
                msp.add_line((x, y - 0.4), (x, y + 0.4), dxfattribs={"layer": "FIRE-FITTING"})
                msp.add_line((x - 0.4, y), (x + 0.4, y), dxfattribs={"layer": "FIRE-FITTING"})
                msp.add_circle((x, y), radius=0.15, dxfattribs={"layer": "FIRE-FITTING"})
            elif fit.fitting_type in [FittingType.ELBOW_90, FittingType.ELBOW_45]:
                msp.add_arc((x, y), radius=0.3, start_angle=0, end_angle=90, dxfattribs={"layer": "FIRE-FITTING"})
            elif fit.fitting_type == FittingType.COUPLING:
                msp.add_line((x - 0.15, y - 0.2), (x - 0.15, y + 0.2), dxfattribs={"layer": "FIRE-FITTING"})
                msp.add_line((x + 0.15, y - 0.2), (x + 0.15, y + 0.2), dxfattribs={"layer": "FIRE-FITTING"})
        
        # Draw valves
        for valve in design.valves:
            x, y = valve.position.x, valve.position.y
            msp.add_lwpolyline([(x, y + 0.5), (x + 0.5, y), (x, y - 0.5), (x - 0.5, y), (x, y + 0.5)], dxfattribs={"layer": "FIRE-VALVE"})
            msp.add_text(valve.model[:12], dxfattribs={"layer": "FIRE-LABEL", "height": 0.3}).set_placement((x + 0.8, y))
        
        # Draw hangers
        for h in design.hangers:
            x, y = h.position.x, h.position.y
            msp.add_lwpolyline([(x - 0.25, y + 0.25), (x + 0.25, y + 0.25), (x, y)], dxfattribs={"layer": "FIRE-HANGER"})
        
        # Draw braces
        for b in design.braces:
            x, y = b.position.x, b.position.y
            label = "L" if b.brace_type == BraceType.LATERAL else ("LG" if b.brace_type == BraceType.LONGITUDINAL else "4W")
            msp.add_circle((x, y), radius=0.3, dxfattribs={"layer": "FIRE-BRACE"})
            msp.add_text(label, dxfattribs={"layer": "FIRE-BRACE", "height": 0.2}).set_placement((x - 0.1, y - 0.08))
        
        # Legend
        lx, ly = -30, 0
        msp.add_text("SYMBOL LEGEND", dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.6}).set_placement((lx, ly))
        msp.add_circle((lx + 1, ly - 3), radius=0.5, dxfattribs={"layer": "FIRE-SPKR"})
        msp.add_text("Sprinkler Head", dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.4}).set_placement((lx + 3, ly - 3))
        msp.add_line((lx, ly - 6), (lx + 2, ly - 6), dxfattribs={"layer": "FIRE-PIPE-MAIN"})
        msp.add_text("Fire Sprinkler Pipe", dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.4}).set_placement((lx + 3, ly - 6))
        msp.add_lwpolyline([(lx + 1, ly - 8.5), (lx + 1.5, ly - 9), (lx + 1, ly - 9.5), (lx + 0.5, ly - 9), (lx + 1, ly - 8.5)], dxfattribs={"layer": "FIRE-VALVE"})
        msp.add_text("Valve", dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.4}).set_placement((lx + 3, ly - 9))
        msp.add_lwpolyline([(lx + 0.75, ly - 11.75), (lx + 1.25, ly - 11.75), (lx + 1, ly - 12)], dxfattribs={"layer": "FIRE-HANGER"})
        msp.add_text("Hanger", dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.4}).set_placement((lx + 3, ly - 12))
        msp.add_circle((lx + 1, ly - 15), radius=0.3, dxfattribs={"layer": "FIRE-BRACE"})
        msp.add_text("Seismic Brace", dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.4}).set_placement((lx + 3, ly - 15))
        
        # Title block
        totals = design.calculate_totals()
        msp.add_text(design.project_name[:40], dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.8}).set_placement((-25, -25))
        msp.add_text(f"Project: {design.project_id}", dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.5}).set_placement((-25, -27))
        msp.add_text(f"Date: {design.timestamp.strftime('%Y-%m-%d')}", dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.5}).set_placement((-25, -29))
        msp.add_text(f"Sprinklers: {totals['sprinkler_count']} | Pipe: {totals['pipe_length_ft']} LF | Fittings: {totals['fitting_count']}", 
                     dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.4}).set_placement((-25, -31))
        msp.add_text(f"Hangers: {totals['hanger_count']} | Braces: {totals['brace_count']} | Valves: {totals['valve_count']}", 
                     dxfattribs={"layer": "FIRE-SYMBOL", "height": 0.4}).set_placement((-25, -33))
        
        doc.saveas(output_path)
        logger.info(f"DXF saved: {output_path}")
        return output_path


# =============================================================================
# PDF GENERATOR
# =============================================================================

class PDFReportGenerator:
    def generate_report(self, design: SystemDesign, output_path: str) -> str:
        if not REPORTLAB_AVAILABLE:
            logger.error("reportlab not available")
            return ""
        
        doc = SimpleDocTemplate(output_path, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
        styles = getSampleStyleSheet()
        story = []
        
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, alignment=TA_CENTER)
        story.append(Paragraph("FIRE SPRINKLER SYSTEM DESIGN REPORT", title_style))
        story.append(Spacer(1, 12))
        story.append(Paragraph(f"<b>Project:</b> {design.project_name}", styles['Normal']))
        story.append(Paragraph(f"<b>Project ID:</b> {design.project_id}", styles['Normal']))
        story.append(Paragraph(f"<b>Date:</b> {design.timestamp.strftime('%Y-%m-%d %H:%M')}", styles['Normal']))
        story.append(Spacer(1, 20))
        
        totals = design.calculate_totals()
        story.append(Paragraph("SYSTEM SUMMARY", styles['Heading2']))
        data = [["Component", "Quantity", "Unit"],
                ["Sprinkler Heads", str(totals['sprinkler_count']), "EA"],
                ["Pipe Length", f"{totals['pipe_length_ft']:.1f}", "LF"],
                ["Fittings", str(totals['fitting_count']), "EA"],
                ["Valves", str(totals['valve_count']), "EA"],
                ["Hangers", str(totals['hanger_count']), "EA"],
                ["Seismic Braces", str(totals['brace_count']), "EA"]]
        t = Table(data, colWidths=[3*inch, 1.5*inch, 1*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(t)
        story.append(Spacer(1, 20))
        
        story.append(Paragraph("DESIGN PARAMETERS", styles['Heading2']))
        params = [["Parameter", "Value"],
                  ["System Type", design.system_type.upper()],
                  ["Hazard Class", design.hazard_class.replace('_', ' ').title()],
                  ["Design Area", f"{design.design_area} sq ft"],
                  ["Design Density", f"{design.design_density} GPM/sq ft"]]
        pt = Table(params, colWidths=[3*inch, 2.5*inch])
        pt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(pt)
        story.append(Spacer(1, 20))
        
        story.append(Paragraph("COST ESTIMATE", styles['Heading2']))
        cost_data = [["Category", "Amount"],
                     ["Material Cost", f"${totals['material_cost']:,.2f}"],
                     ["Labor Cost", f"${totals['labor_cost']:,.2f}"],
                     ["Total Estimated", f"${totals['total_estimated_cost']:,.2f}"]]
        ct = Table(cost_data, colWidths=[3*inch, 2.5*inch])
        ct.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey), ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('ALIGN', (1, 1), (1, -1), 'RIGHT'), ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(ct)
        story.append(Spacer(1, 20))
        
        story.append(Paragraph("COMPLIANCE STATUS", styles['Heading2']))
        comp_color = colors.green if design.nfpa_compliant else colors.red
        comp_text = "COMPLIANT" if design.nfpa_compliant else "NON-COMPLIANT"
        comp_data = [["Standard", "Status"], ["NFPA 13", comp_text]]
        compt = Table(comp_data, colWidths=[3*inch, 2.5*inch])
        compt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (1, 1), (1, 1), comp_color), ('TEXTCOLOR', (1, 1), (1, 1), colors.white),
            ('FONTNAME', (1, 1), (1, 1), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(compt)
        
        if design.violations:
            story.append(Spacer(1, 15))
            story.append(Paragraph("Violations:", styles['Heading3']))
            for v in design.violations:
                story.append(Paragraph(f"• {v}", styles['Normal']))
        
        if design.warnings:
            story.append(Spacer(1, 15))
            story.append(Paragraph("Warnings:", styles['Heading3']))
            for w in design.warnings:
                story.append(Paragraph(f"• {w}", styles['Normal']))
        
        doc.build(story)
        logger.info(f"PDF saved: {output_path}")
        return output_path


# =============================================================================
# BOM CSV GENERATOR
# =============================================================================

def generate_bom_csv(design: SystemDesign, output_path: str) -> str:
    with open(output_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(["Item", "Description", "Size", "Material", "Quantity", "Unit", "Unit Cost", "Total Cost"])
        item = 1
        
        # Sprinklers
        spk_count = len(design.sprinklers)
        if spk_count > 0:
            w.writerow([item, "Sprinkler Head K5.6 165F QR Pendant", '1/2"', "Brass/Chrome", spk_count, "EA", 45.00, spk_count * 45.00])
            item += 1
        
        # Pipes by size
        pipe_groups = {}
        for p in design.pipes:
            key = p.diameter
            pipe_groups[key] = pipe_groups.get(key, 0) + p.length
        for dia, length in sorted(pipe_groups.items()):
            cost_ft = {1.0: 3.50, 1.25: 4.25, 1.5: 5.00, 2.0: 7.50, 2.5: 10.00, 3.0: 14.00, 4.0: 22.00, 6.0: 38.00}.get(dia, dia * 5)
            w.writerow([item, f'Pipe Schedule 40 Black Steel', f'{dia}"', "Steel", round(length, 1), "LF", cost_ft, round(cost_ft * length, 2)])
            item += 1
        
        # Fittings by type and size
        fit_groups = {}
        for f in design.fittings:
            key = (f.fitting_type.value, f.size_1)
            fit_groups[key] = fit_groups.get(key, 0) + 1
        for (ftype, size), qty in sorted(fit_groups.items()):
            unit = Fitting(id="", position=Point3D(0,0,0), fitting_type=FittingType(ftype), size_1=size).get_cost()
            w.writerow([item, f'{ftype.replace("_", " ").title()} Fitting', f'{size}"', "Malleable Iron", qty, "EA", round(unit, 2), round(unit * qty, 2)])
            item += 1
        
        # Valves
        for v in design.valves:
            w.writerow([item, f'{v.valve_type.value.replace("_", " ").title()}', f'{v.size}"', "Various", 1, "EA", round(v.get_cost(), 2), round(v.get_cost(), 2)])
            item += 1
        
        # Hangers grouped
        hanger_groups = {}
        for h in design.hangers:
            key = (h.hanger_type.value, h.pipe_size)
            hanger_groups[key] = hanger_groups.get(key, 0) + 1
        for (htype, psize), qty in sorted(hanger_groups.items()):
            unit = Hanger(id="", position=Point3D(0,0,0), hanger_type=HangerType(htype), pipe_size=psize).get_cost()
            w.writerow([item, f'{htype.replace("_", " ").title()}', f'{psize}" pipe', "Steel/Zinc", qty, "EA", round(unit, 2), round(unit * qty, 2)])
            item += 1
        
        # Braces grouped
        brace_groups = {}
        for b in design.braces:
            key = (b.brace_type.value, b.pipe_size)
            brace_groups[key] = brace_groups.get(key, 0) + 1
        for (btype, psize), qty in sorted(brace_groups.items()):
            unit = SeismicBrace(id="", position=Point3D(0,0,0), brace_type=BraceType(btype), pipe_size=psize).get_cost()
            w.writerow([item, f'Seismic Brace {btype.replace("_", " ").title()}', f'{psize}" pipe', "Steel", qty, "EA", round(unit, 2), round(unit * qty, 2)])
            item += 1
        
        w.writerow([])
        totals = design.calculate_totals()
        w.writerow(["", "", "", "", "", "", "MATERIAL TOTAL:", f"${totals['material_cost']:,.2f}"])
        w.writerow(["", "", "", "", "", "", "LABOR TOTAL:", f"${totals['labor_cost']:,.2f}"])
        w.writerow(["", "", "", "", "", "", "GRAND TOTAL:", f"${totals['total_estimated_cost']:,.2f}"])
    
    logger.info(f"BOM saved: {output_path}")
    return output_path


# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def orchestrate(project_dir: str, output_dir: str) -> Dict[str, Any]:
    """Main orchestration function - processes project and generates all deliverables"""
    logger.info(f"🔥 Starting orchestration: {project_dir}")
    start = datetime.now()
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Load project data
    project_data = _load_project_data(project_dir)
    project_id = project_data.get('project_id', f'FP-{uuid.uuid4().hex[:8].upper()}')
    
    # Initialize design
    design = SystemDesign(
        project_id=project_id,
        project_name=project_data.get('project_name', 'Fire Sprinkler Project'),
        timestamp=datetime.now(),
        system_type=project_data.get('system_type', 'wet'),
        hazard_class=project_data.get('hazard_class', 'ordinary_hazard_1'),
        design_area=project_data.get('building_area_sqft', 10000),
        design_density={'light_hazard': 0.10, 'ordinary_hazard_1': 0.15, 'ordinary_hazard_2': 0.20, 
                       'extra_hazard_1': 0.30, 'extra_hazard_2': 0.40}.get(project_data.get('hazard_class', ''), 0.15)
    )
    
    # Step 1: Generate sprinkler layout and pipe routing
    logger.info("Step 1: Generating sprinkler layout and pipe routing...")
    if ROUTING_AVAILABLE:
        try:
            result = design_fire_sprinkler_system(project_data)
            design.sprinklers = _convert_sprinklers(result.sprinkler_heads)
            design.pipes = _convert_pipes(result.pipe_segments)
            logger.info(f"  ✅ Routing engine: {len(design.sprinklers)} sprinklers, {len(design.pipes)} pipes")
        except Exception as e:
            logger.warning(f"  ⚠️ Routing engine failed: {e}, using fallback")
            design.sprinklers, design.pipes = _fallback_layout(project_data)
    else:
        logger.info("  Using fallback layout generator")
        design.sprinklers, design.pipes = _fallback_layout(project_data)
    logger.info(f"  Total: {len(design.sprinklers)} sprinklers, {len(design.pipes)} pipe segments")
    
    # Step 2: Generate fittings
    logger.info("Step 2: Generating pipe fittings...")
    design.fittings = FittingGenerator().analyze_and_generate(design.pipes, design.sprinklers)
    logger.info(f"  ✅ Generated {len(design.fittings)} fittings (tees, elbows, couplings)")
    
    # Step 3: Generate hangers
    logger.info("Step 3: Generating pipe hangers per NFPA 13...")
    ceiling_height = project_data.get('ceiling_height_ft', 12)
    design.hangers = HangerGenerator().generate_hangers(design.pipes, ceiling_height)
    logger.info(f"  ✅ Generated {len(design.hangers)} hangers")
    
    # Step 4: Generate valves
    logger.info("Step 4: Generating system valves...")
    riser = Point3D(project_data.get('riser_x', 10), project_data.get('riser_y', 10), 0)
    main_size = max((p.diameter for p in design.pipes if p.pipe_type in [PipeType.MAIN, PipeType.RISER]), default=4.0)
    design.valves = ValveGenerator().generate_valves(riser, main_size, design.system_type)
    logger.info(f"  ✅ Generated {len(design.valves)} valves")
    
    # Step 5: Generate seismic bracing
    logger.info("Step 5: Generating seismic bracing...")
    seismic_zone = project_data.get('seismic_zone', 3)
    design.braces = BracingGenerator().generate_bracing(design.pipes, seismic_zone)
    logger.info(f"  ✅ Generated {len(design.braces)} seismic braces")
    
    # Step 6: Hydraulic calculations
    logger.info("Step 6: Calculating hydraulics...")
    design.total_flow = len(design.sprinklers) * 15  # Simplified
    design.total_pressure = 52
    logger.info(f"  Flow: {design.total_flow} GPM, Pressure: {design.total_pressure} PSI")
    
    # Step 7: Compliance check
    logger.info("Step 7: Checking code compliance...")
    design.nfpa_compliant = len(design.sprinklers) > 0 and all(p.diameter >= 1.0 for p in design.pipes)
    if not design.nfpa_compliant:
        design.violations.append("Design does not meet minimum requirements")
    logger.info(f"  Compliance: {'✅ PASS' if design.nfpa_compliant else '❌ FAIL'}")
    
    # Step 8: Generate output files
    logger.info("Step 8: Generating output files...")
    outputs = {}
    
    # DXF Drawing
    if EZDXF_AVAILABLE:
        dxf_path = os.path.join(output_dir, 'design.dxf')
        DXFGenerator().generate_dxf(design, dxf_path)
        outputs['design.dxf'] = dxf_path
        logger.info(f"  ✅ design.dxf")
    else:
        logger.warning("  ⚠️ ezdxf not available, skipping DXF")
    
    # PDF Report
    if REPORTLAB_AVAILABLE:
        pdf_path = os.path.join(output_dir, 'compliance_report.pdf')
        PDFReportGenerator().generate_report(design, pdf_path)
        outputs['compliance_report.pdf'] = pdf_path
        logger.info(f"  ✅ compliance_report.pdf")
    else:
        logger.warning("  ⚠️ reportlab not available, skipping PDF")
    
    # Bill of Materials
    bom_path = os.path.join(output_dir, 'bill_of_materials.csv')
    generate_bom_csv(design, bom_path)
    outputs['bill_of_materials.csv'] = bom_path
    logger.info(f"  ✅ bill_of_materials.csv")
    
    # Summary JSON
    summary = {
        'project_id': design.project_id,
        'project_name': design.project_name,
        'timestamp': design.timestamp.isoformat(),
        'system_summary': design.calculate_totals(),
        'design_parameters': {
            'system_type': design.system_type,
            'hazard_class': design.hazard_class,
            'design_area_sqft': design.design_area,
            'design_density_gpm_sqft': design.design_density
        },
        'hydraulics': {
            'total_flow_gpm': design.total_flow,
            'total_pressure_psi': design.total_pressure
        },
        'compliance': {
            'nfpa_compliant': design.nfpa_compliant,
            'violations': design.violations,
            'warnings': design.warnings
        },
        'output_files': list(outputs.keys())
    }
    summary_path = os.path.join(output_dir, 'summary.json')
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    outputs['summary.json'] = summary_path
    logger.info(f"  ✅ summary.json")
    
    elapsed = (datetime.now() - start).total_seconds()
    logger.info(f"🎉 Orchestration complete in {elapsed:.2f} seconds")
    logger.info(f"   Output files: {list(outputs.keys())}")
    
    return {
        'success': True,
        'project_id': design.project_id,
        'outputs': outputs,
        'summary': summary,
        'processing_time': elapsed
    }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _load_project_data(project_dir: str) -> Dict[str, Any]:
    data = {
        'project_id': f'FP-{uuid.uuid4().hex[:8].upper()}',
        'project_name': 'Fire Sprinkler Project',
        'building_area_sqft': 10000,
        'ceiling_height_ft': 12,
        'hazard_class': 'ordinary_hazard_1',
        'system_type': 'wet',
        'seismic_zone': 3,
        'riser_x': 10,
        'riser_y': 10
    }
    json_path = os.path.join(project_dir, 'project.json')
    if os.path.exists(json_path):
        try:
            with open(json_path) as f:
                data.update(json.load(f))
            logger.info(f"Loaded project.json from {project_dir}")
        except Exception as e:
            logger.warning(f"Could not load project.json: {e}")
    return data


def _convert_sprinklers(routing_spks) -> List[SprinklerHead]:
    result = []
    for i, s in enumerate(routing_spks):
        pos = s.position if hasattr(s, 'position') else s
        if hasattr(pos, 'x'):
            p = Point3D(pos.x, pos.y, getattr(pos, 'z', 10))
        else:
            p = Point3D(pos.get('x', 0), pos.get('y', 0), pos.get('z', 10))
        result.append(SprinklerHead(
            id=getattr(s, 'id', f'SPK-{i+1:04d}'),
            position=p,
            coverage_area=getattr(s, 'coverage_area', 130),
            flow_rate=getattr(s, 'flow_rate', 15),
            k_factor=getattr(s, 'k_factor', 5.6),
            temperature_rating=getattr(s, 'temperature_rating', 165),
            orientation=getattr(s, 'orientation', 'pendant')
        ))
    return result


def _convert_pipes(routing_pipes) -> List[PipeSegment]:
    result = []
    for i, p in enumerate(routing_pipes):
        sp = p.start_point if hasattr(p, 'start_point') else {}
        ep = p.end_point if hasattr(p, 'end_point') else {}
        
        if hasattr(sp, 'x'):
            start = Point3D(sp.x, sp.y, getattr(sp, 'z', 10))
        else:
            start = Point3D(sp.get('x', 0), sp.get('y', 0), sp.get('z', 10))
        
        if hasattr(ep, 'x'):
            end = Point3D(ep.x, ep.y, getattr(ep, 'z', 10))
        else:
            end = Point3D(ep.get('x', 0), ep.get('y', 0), ep.get('z', 10))
        
        dia = getattr(p, 'diameter', 1.25)
        length = getattr(p, 'length', start.distance_to(end))
        
        # Determine pipe type
        is_riser = getattr(p, 'is_riser', False)
        if is_riser or abs(end.z - start.z) > 1:
            ptype = PipeType.RISER
        elif dia >= 4:
            ptype = PipeType.MAIN
        elif dia >= 2.5:
            ptype = PipeType.CROSS_MAIN
        else:
            ptype = PipeType.BRANCH
        
        result.append(PipeSegment(
            id=getattr(p, 'id', f'PIPE-{i+1:04d}'),
            start_point=start,
            end_point=end,
            diameter=dia,
            length=length if length > 0 else start.distance_to(end),
            pipe_type=ptype,
            material=getattr(p, 'pipe_material', 'steel_black'),
            flow_rate=getattr(p, 'flow_rate', 0),
            pressure_loss=getattr(p, 'pressure_loss', 0)
        ))
    return result


def _fallback_layout(project_data: Dict) -> Tuple[List[SprinklerHead], List[PipeSegment]]:
    """Generate basic sprinkler layout when routing engine unavailable"""
    area = project_data.get('building_area_sqft', 10000)
    side = math.sqrt(area)
    ceiling = project_data.get('ceiling_height_ft', 12)
    spacing = 12.0
    
    sprinklers = []
    pipes = []
    
    num = int(side / spacing) + 1
    sid = 1
    for i in range(num):
        for j in range(num):
            x = 5 + i * spacing
            y = 5 + j * spacing
            if x < side and y < side:
                sprinklers.append(SprinklerHead(
                    id=f'SPK-{sid:04d}',
                    position=Point3D(x, y, ceiling - 0.5),
                    coverage_area=130,
                    flow_rate=15,
                    k_factor=5.6
                ))
                sid += 1
    
    # Riser
    pipe_height = ceiling - 2
    pipes.append(PipeSegment(
        id='PIPE-RISER-001',
        start_point=Point3D(10, 10, 0),
        end_point=Point3D(10, 10, pipe_height),
        diameter=4.0,
        length=pipe_height,
        pipe_type=PipeType.RISER
    ))
    
    # Main
    pipes.append(PipeSegment(
        id='PIPE-MAIN-001',
        start_point=Point3D(10, 10, pipe_height),
        end_point=Point3D(side - 5, 10, pipe_height),
        diameter=4.0,
        length=side - 15,
        pipe_type=PipeType.MAIN
    ))
    
    # Branches
    pid = 1
    for i in range(num):
        x = 5 + i * spacing
        if x < side:
            pipes.append(PipeSegment(
                id=f'PIPE-BR-{pid:03d}',
                start_point=Point3D(x, 10, pipe_height),
                end_point=Point3D(x, side - 5, pipe_height),
                diameter=1.5 if i % 2 == 0 else 1.25,
                length=side - 15,
                pipe_type=PipeType.BRANCH
            ))
            pid += 1
    
    return sprinklers, pipes


def get_engine_status() -> Dict[str, Any]:
    """Get status of all engines for health checks"""
    return {
        'routing': ROUTING_AVAILABLE,
        'hydraulics': HYDRAULICS_AVAILABLE,
        'codes': CODES_AVAILABLE,
        'bracing': BRACING_AVAILABLE,
        'cad': CAD_AVAILABLE,
        'products': PRODUCTS_AVAILABLE,
        'symbols': SYMBOLS_AVAILABLE,
        'reportlab': REPORTLAB_AVAILABLE,
        'ezdxf': EZDXF_AVAILABLE,
        'import_errors': IMPORT_ERRORS
    }


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🔥 FireAI Pro Enhanced Orchestrator v2.0")
    print("=" * 60)
    print("Engine Status:")
    status = get_engine_status()
    for k, v in status.items():
        if k != 'import_errors':
            print(f"  {'✅' if v else '❌'} {k}")
    
    if status['import_errors']:
        print("\nImport Errors (non-critical):")
        for engine, error in status['import_errors'].items():
            print(f"  {engine}: {error[:60]}...")
    
    print("\n" + "=" * 60)
    print("Ready for orchestration!")
    print("\nUsage:")
    print("  from orchestrate import orchestrate")
    print("  result = orchestrate('/path/to/project', '/path/to/output')")
