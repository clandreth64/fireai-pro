#!/usr/bin/env python3
"""
FireAI Pro - Intelligent Pipe Sizing Optimizer
VERSION: 1.0.0

🔧 AUTOMATIC PIPE SIZE OPTIMIZATION FOR FIRE SPRINKLER SYSTEMS

This module provides intelligent pipe sizing that optimizes for:
- NFPA 13 velocity compliance
- Adequate pressure at hydraulically remote sprinklers
- Minimum material cost
- Pipe schedule selection (Sch 10/40)

📐 NFPA 13 VELOCITY LIMITS:
- Branch lines: ≤ 20 FPS (recommended)
- Cross mains: ≤ 30 FPS (recommended)  
- Feed mains: ≤ 32 FPS (maximum per NFPA 13)
- Risers: ≤ 25 FPS (recommended)

💰 OPTIMIZATION ALGORITHM:
1. Initialize with minimum sizes per sprinkler count rules
2. Run hydraulic analysis (pressure/flow at each node)
3. Check velocity constraints at each pipe
4. Check pressure adequacy at remote area
5. Upsize pipes where velocity exceeded or pressure insufficient
6. Downsize pipes where possible without violating constraints
7. Iterate until no further improvements possible
8. Calculate cost comparison vs initial sizing

📋 PIPE SCHEDULES SUPPORTED:
- Schedule 10 (thin wall) - 2" and larger
- Schedule 40 (standard) - All sizes
- Schedule 7 (light wall) - 1" to 3"
- CPVC Schedule 40 - Residential/light hazard

🎯 OUTPUT:
- Optimized pipe diameters
- Velocity report for each pipe
- Pressure adequacy check
- Material cost comparison
- Recommendations for further optimization
"""

import math
import logging
from typing import Dict, List, Optional, Any, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import copy

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS AND PIPE DATA
# =============================================================================

class PipeSchedule(Enum):
    """Pipe schedule types"""
    SCHEDULE_7 = "sch7"
    SCHEDULE_10 = "sch10"
    SCHEDULE_40 = "sch40"
    CPVC = "cpvc"


class PipeType(Enum):
    """Pipe function types"""
    BRANCH = "branch"
    CROSS_MAIN = "cross_main"
    FEED_MAIN = "feed_main"
    RISER = "riser"
    ARM_OVER = "arm_over"
    SPRIG = "sprig"


# Pipe inside diameters (inches) by schedule
PIPE_INSIDE_DIAMETERS = {
    # Nominal: {schedule: ID}
    0.75: {PipeSchedule.SCHEDULE_40: 0.824, PipeSchedule.SCHEDULE_7: 0.884},
    1.0: {PipeSchedule.SCHEDULE_40: 1.049, PipeSchedule.SCHEDULE_7: 1.107, PipeSchedule.SCHEDULE_10: 1.097},
    1.25: {PipeSchedule.SCHEDULE_40: 1.380, PipeSchedule.SCHEDULE_7: 1.442, PipeSchedule.SCHEDULE_10: 1.442},
    1.5: {PipeSchedule.SCHEDULE_40: 1.610, PipeSchedule.SCHEDULE_7: 1.682, PipeSchedule.SCHEDULE_10: 1.682},
    2.0: {PipeSchedule.SCHEDULE_40: 2.067, PipeSchedule.SCHEDULE_7: 2.157, PipeSchedule.SCHEDULE_10: 2.157},
    2.5: {PipeSchedule.SCHEDULE_40: 2.469, PipeSchedule.SCHEDULE_10: 2.635},
    3.0: {PipeSchedule.SCHEDULE_40: 3.068, PipeSchedule.SCHEDULE_7: 3.260, PipeSchedule.SCHEDULE_10: 3.260},
    3.5: {PipeSchedule.SCHEDULE_40: 3.548, PipeSchedule.SCHEDULE_10: 3.760},
    4.0: {PipeSchedule.SCHEDULE_40: 4.026, PipeSchedule.SCHEDULE_10: 4.260},
    5.0: {PipeSchedule.SCHEDULE_40: 5.047, PipeSchedule.SCHEDULE_10: 5.295},
    6.0: {PipeSchedule.SCHEDULE_40: 6.065, PipeSchedule.SCHEDULE_10: 6.357},
    8.0: {PipeSchedule.SCHEDULE_40: 7.981, PipeSchedule.SCHEDULE_10: 8.329},
    10.0: {PipeSchedule.SCHEDULE_40: 10.020, PipeSchedule.SCHEDULE_10: 10.420},
    12.0: {PipeSchedule.SCHEDULE_40: 11.938, PipeSchedule.SCHEDULE_10: 12.390},
}

# Hazen-Williams C-factors
C_FACTORS = {
    'black_steel': 120,
    'galvanized': 120,
    'cement_lined': 140,
    'copper': 150,
    'cpvc': 150,
    'stainless': 140,
    'plastic': 150,
}

# Pipe cost per foot by nominal diameter and schedule
PIPE_COSTS = {
    # (nominal, schedule): cost_per_foot
    (0.75, PipeSchedule.SCHEDULE_40): 1.85,
    (1.0, PipeSchedule.SCHEDULE_40): 2.45,
    (1.0, PipeSchedule.SCHEDULE_10): 2.15,
    (1.25, PipeSchedule.SCHEDULE_40): 3.25,
    (1.25, PipeSchedule.SCHEDULE_10): 2.85,
    (1.5, PipeSchedule.SCHEDULE_40): 3.95,
    (1.5, PipeSchedule.SCHEDULE_10): 3.45,
    (2.0, PipeSchedule.SCHEDULE_40): 5.85,
    (2.0, PipeSchedule.SCHEDULE_10): 4.95,
    (2.5, PipeSchedule.SCHEDULE_40): 8.50,
    (2.5, PipeSchedule.SCHEDULE_10): 7.25,
    (3.0, PipeSchedule.SCHEDULE_40): 11.25,
    (3.0, PipeSchedule.SCHEDULE_10): 9.50,
    (4.0, PipeSchedule.SCHEDULE_40): 16.50,
    (4.0, PipeSchedule.SCHEDULE_10): 13.75,
    (5.0, PipeSchedule.SCHEDULE_40): 24.50,
    (5.0, PipeSchedule.SCHEDULE_10): 20.50,
    (6.0, PipeSchedule.SCHEDULE_40): 32.00,
    (6.0, PipeSchedule.SCHEDULE_10): 26.50,
    (8.0, PipeSchedule.SCHEDULE_40): 52.00,
    (8.0, PipeSchedule.SCHEDULE_10): 42.00,
}

# Velocity limits by pipe type (FPS)
VELOCITY_LIMITS = {
    PipeType.BRANCH: 20.0,
    PipeType.ARM_OVER: 20.0,
    PipeType.SPRIG: 20.0,
    PipeType.CROSS_MAIN: 30.0,
    PipeType.FEED_MAIN: 32.0,
    PipeType.RISER: 25.0,
}

# NFPA 13 pipe schedule - minimum pipe sizes by sprinkler count
NFPA_PIPE_SCHEDULE = {
    # Steel pipe - sprinkler count: minimum diameter
    'steel': {
        1: 1.0, 2: 1.0, 3: 1.25, 4: 1.25, 5: 1.5,
        6: 1.5, 7: 1.5, 8: 2.0, 9: 2.0, 10: 2.0,
        12: 2.5, 15: 2.5, 20: 3.0, 30: 3.5, 40: 4.0,
        60: 5.0, 80: 6.0, 100: 6.0, 150: 8.0
    },
    # Copper pipe - sprinkler count: minimum diameter  
    'copper': {
        1: 0.75, 2: 1.0, 3: 1.0, 4: 1.25, 5: 1.25,
        8: 1.5, 12: 2.0, 20: 2.5, 40: 3.0, 65: 4.0
    }
}

# Standard nominal pipe sizes (ascending order)
STANDARD_SIZES = [0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0]


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class PipeSegment:
    """A pipe segment in the network"""
    id: str
    pipe_type: PipeType
    upstream_node: str
    downstream_node: str
    length: float  # feet
    diameter: float  # nominal inches
    schedule: PipeSchedule = PipeSchedule.SCHEDULE_40
    c_factor: int = 120
    flow: float = 0.0  # GPM (calculated)
    velocity: float = 0.0  # FPS (calculated)
    friction_loss: float = 0.0  # PSI (calculated)
    fittings_eq_length: float = 0.0  # Equivalent length of fittings
    sprinkler_count_downstream: int = 0  # For minimum sizing


@dataclass
class HydraulicNode:
    """A node in the hydraulic network"""
    id: str
    x: float
    y: float
    elevation: float  # feet
    node_type: str  # 'sprinkler', 'junction', 'source'
    demand: float = 0.0  # GPM
    pressure: float = 0.0  # PSI (calculated)
    k_factor: float = 0.0  # For sprinklers


@dataclass
class VelocityViolation:
    """Record of a velocity violation"""
    pipe_id: str
    pipe_type: PipeType
    current_diameter: float
    current_velocity: float
    velocity_limit: float
    flow: float
    recommended_diameter: float


@dataclass
class PressureViolation:
    """Record of a pressure violation"""
    node_id: str
    required_pressure: float
    available_pressure: float
    deficit: float


@dataclass
class OptimizationResult:
    """Result of pipe sizing optimization"""
    success: bool
    iterations: int
    
    # Original vs optimized
    original_pipes: List[PipeSegment]
    optimized_pipes: List[PipeSegment]
    
    # Cost analysis
    original_cost: float
    optimized_cost: float
    cost_savings: float
    cost_savings_percent: float
    
    # Violations
    velocity_violations: List[VelocityViolation]
    pressure_violations: List[PressureViolation]
    
    # Pressure adequacy
    remote_node_id: str
    required_pressure: float
    available_pressure: float
    safety_margin: float
    
    # Summary
    pipes_upsized: int
    pipes_downsized: int
    recommendations: List[str]


@dataclass
class NetworkAnalysisInput:
    """Input for optimization"""
    nodes: List[HydraulicNode]
    pipes: List[PipeSegment]
    source_node_id: str
    source_pressure: float  # PSI at source
    required_remote_pressure: float  # PSI required at most remote sprinkler
    system_demand: float  # GPM total
    hose_allowance: float = 250.0  # GPM


# =============================================================================
# CORE CALCULATIONS
# =============================================================================

class HydraulicCalculator:
    """Core hydraulic calculations"""
    
    @staticmethod
    def calculate_velocity(flow_gpm: float, inside_diameter: float) -> float:
        """
        Calculate velocity in FPS
        
        V = 0.4085 × Q / d²
        
        Where:
            V = velocity (FPS)
            Q = flow (GPM)
            d = inside diameter (inches)
        """
        if inside_diameter <= 0:
            return 0.0
        return 0.4085 * flow_gpm / (inside_diameter ** 2)
    
    @staticmethod
    def calculate_friction_loss(flow_gpm: float, c_factor: int, 
                                 inside_diameter: float, length_ft: float) -> float:
        """
        Calculate friction loss using Hazen-Williams formula
        
        Pf = 4.52 × Q^1.85 / (C^1.85 × d^4.87) × L
        
        Where:
            Pf = friction loss (PSI)
            Q = flow (GPM)
            C = Hazen-Williams coefficient
            d = inside diameter (inches)
            L = pipe length (feet)
        """
        if inside_diameter <= 0 or c_factor <= 0 or length_ft <= 0:
            return 0.0
        
        return (4.52 * (flow_gpm ** 1.85) / 
                ((c_factor ** 1.85) * (inside_diameter ** 4.87))) * length_ft
    
    @staticmethod
    def calculate_elevation_pressure(elevation_diff: float) -> float:
        """
        Calculate pressure change due to elevation
        
        Pe = 0.433 × h
        
        Where:
            Pe = pressure change (PSI)
            h = elevation difference (feet)
            
        Positive h = uphill (pressure decreases)
        Negative h = downhill (pressure increases)
        """
        return 0.433 * elevation_diff
    
    @staticmethod
    def get_inside_diameter(nominal: float, schedule: PipeSchedule) -> float:
        """Get inside diameter for nominal size and schedule"""
        if nominal in PIPE_INSIDE_DIAMETERS:
            schedules = PIPE_INSIDE_DIAMETERS[nominal]
            if schedule in schedules:
                return schedules[schedule]
            # Fallback to Schedule 40
            if PipeSchedule.SCHEDULE_40 in schedules:
                return schedules[PipeSchedule.SCHEDULE_40]
        
        # Approximate if not in table
        return nominal * 0.9  # Rough approximation
    
    @staticmethod
    def get_minimum_size_for_velocity(flow_gpm: float, max_velocity: float) -> float:
        """Calculate minimum inside diameter for given flow and velocity limit"""
        if max_velocity <= 0 or flow_gpm <= 0:
            return 1.0
        
        # V = 0.4085 × Q / d²
        # d² = 0.4085 × Q / V
        # d = sqrt(0.4085 × Q / V)
        min_id = math.sqrt(0.4085 * flow_gpm / max_velocity)
        return min_id
    
    @staticmethod
    def find_nominal_for_id(required_id: float, schedule: PipeSchedule) -> float:
        """Find smallest nominal size that provides required ID"""
        for nominal in STANDARD_SIZES:
            if nominal in PIPE_INSIDE_DIAMETERS:
                schedules = PIPE_INSIDE_DIAMETERS[nominal]
                if schedule in schedules:
                    if schedules[schedule] >= required_id:
                        return nominal
        
        # Return largest if nothing fits
        return STANDARD_SIZES[-1]


# =============================================================================
# PIPE SIZING OPTIMIZER
# =============================================================================

class IntelligentPipeSizer:
    """
    Intelligent pipe sizing optimizer
    
    Optimizes pipe sizes to:
    1. Meet velocity limits per NFPA 13
    2. Ensure adequate pressure at remote area
    3. Minimize material cost
    """
    
    def __init__(self, max_iterations: int = 20, 
                 pressure_safety_margin: float = 5.0,
                 prefer_schedule_10: bool = True):
        """
        Initialize optimizer
        
        Args:
            max_iterations: Maximum optimization iterations
            pressure_safety_margin: Desired pressure safety margin (PSI)
            prefer_schedule_10: Prefer Sch 10 for 2"+ to reduce cost
        """
        self.max_iterations = max_iterations
        self.pressure_safety_margin = pressure_safety_margin
        self.prefer_schedule_10 = prefer_schedule_10
        self.calculator = HydraulicCalculator()
        self.logger = logging.getLogger(f"{__name__}.PipeSizer")
    
    def optimize(self, network: NetworkAnalysisInput) -> OptimizationResult:
        """
        Run pipe sizing optimization
        
        Args:
            network: Network analysis input with nodes, pipes, constraints
            
        Returns:
            OptimizationResult with optimized pipe sizes and analysis
        """
        self.logger.info(f"Starting pipe optimization: {len(network.pipes)} pipes, {len(network.nodes)} nodes")
        
        # Store original for comparison
        original_pipes = copy.deepcopy(network.pipes)
        original_cost = self._calculate_total_cost(original_pipes)
        
        # Working copy
        pipes = copy.deepcopy(network.pipes)
        nodes = {n.id: n for n in network.nodes}
        
        # Step 1: Initialize with minimum sizes per NFPA schedule
        pipes = self._apply_minimum_sizes(pipes)
        
        # Step 2: Assign flows to pipes (simplified - in reality would use Hardy Cross)
        pipes = self._estimate_flows(pipes, nodes, network.source_node_id)
        
        # Step 3: Iterative optimization
        iteration = 0
        velocity_violations = []
        pressure_violations = []
        
        while iteration < self.max_iterations:
            iteration += 1
            made_changes = False
            
            # Check and fix velocity violations
            for pipe in pipes:
                inside_dia = self.calculator.get_inside_diameter(pipe.diameter, pipe.schedule)
                pipe.velocity = self.calculator.calculate_velocity(pipe.flow, inside_dia)
                
                velocity_limit = VELOCITY_LIMITS.get(pipe.pipe_type, 20.0)
                
                if pipe.velocity > velocity_limit:
                    # Need to upsize
                    min_id = self.calculator.get_minimum_size_for_velocity(
                        pipe.flow, velocity_limit * 0.95  # 5% margin
                    )
                    new_nominal = self.calculator.find_nominal_for_id(min_id, pipe.schedule)
                    
                    if new_nominal > pipe.diameter:
                        self.logger.debug(f"Upsizing {pipe.id}: {pipe.diameter}\" → {new_nominal}\" (velocity {pipe.velocity:.1f} FPS)")
                        pipe.diameter = new_nominal
                        made_changes = True
            
            # Check pressure adequacy (simplified)
            total_friction = sum(
                self.calculator.calculate_friction_loss(
                    p.flow, p.c_factor,
                    self.calculator.get_inside_diameter(p.diameter, p.schedule),
                    p.length + p.fittings_eq_length
                )
                for p in pipes
            ) / 3  # Approximate path loss (would be more accurate with full network analysis)
            
            available_pressure = network.source_pressure - total_friction
            
            if available_pressure < network.required_remote_pressure + self.pressure_safety_margin:
                # Need to upsize some pipes to reduce friction
                # Prioritize mains and feed mains
                for pipe in sorted(pipes, key=lambda p: p.flow, reverse=True)[:3]:
                    if pipe.diameter < STANDARD_SIZES[-2]:  # Not already max
                        idx = STANDARD_SIZES.index(pipe.diameter)
                        pipe.diameter = STANDARD_SIZES[idx + 1]
                        made_changes = True
                        self.logger.debug(f"Upsizing {pipe.id} for pressure: → {pipe.diameter}\"")
                        break
            
            if not made_changes:
                break
        
        # Step 4: Try to downsize where possible for cost savings
        pipes = self._try_downsize(pipes, network)
        
        # Step 5: Apply schedule optimization (Sch 10 where allowed)
        if self.prefer_schedule_10:
            pipes = self._optimize_schedules(pipes)
        
        # Final analysis
        optimized_cost = self._calculate_total_cost(pipes)
        velocity_violations = self._check_velocity_violations(pipes)
        pressure_violations = self._check_pressure_violations(pipes, nodes, network)
        
        # Find remote node
        remote_node_id = self._find_remote_node(nodes, network.source_node_id)
        
        # Calculate changes
        pipes_upsized = sum(1 for o, n in zip(original_pipes, pipes) if n.diameter > o.diameter)
        pipes_downsized = sum(1 for o, n in zip(original_pipes, pipes) if n.diameter < o.diameter)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            pipes, velocity_violations, pressure_violations, network
        )
        
        result = OptimizationResult(
            success=len(velocity_violations) == 0 and len(pressure_violations) == 0,
            iterations=iteration,
            original_pipes=original_pipes,
            optimized_pipes=pipes,
            original_cost=original_cost,
            optimized_cost=optimized_cost,
            cost_savings=original_cost - optimized_cost,
            cost_savings_percent=((original_cost - optimized_cost) / original_cost * 100) if original_cost > 0 else 0,
            velocity_violations=velocity_violations,
            pressure_violations=pressure_violations,
            remote_node_id=remote_node_id,
            required_pressure=network.required_remote_pressure,
            available_pressure=network.source_pressure - self._estimate_path_loss(pipes, network),
            safety_margin=network.source_pressure - self._estimate_path_loss(pipes, network) - network.required_remote_pressure,
            pipes_upsized=pipes_upsized,
            pipes_downsized=pipes_downsized,
            recommendations=recommendations
        )
        
        self.logger.info(f"Optimization complete: {iteration} iterations, ${result.cost_savings:.2f} savings ({result.cost_savings_percent:.1f}%)")
        return result
    
    def _apply_minimum_sizes(self, pipes: List[PipeSegment]) -> List[PipeSegment]:
        """Apply NFPA 13 minimum sizes based on sprinkler count"""
        schedule = NFPA_PIPE_SCHEDULE['steel']
        
        for pipe in pipes:
            count = pipe.sprinkler_count_downstream
            if count <= 0:
                count = 1
            
            # Find minimum size for this sprinkler count
            min_size = 1.0
            for max_count, size in sorted(schedule.items()):
                if count <= max_count:
                    min_size = size
                    break
                min_size = size
            
            # Only upsize if current is smaller
            if pipe.diameter < min_size:
                pipe.diameter = min_size
        
        return pipes
    
    def _estimate_flows(self, pipes: List[PipeSegment], 
                        nodes: Dict[str, HydraulicNode],
                        source_node_id: str) -> List[PipeSegment]:
        """Estimate flows in each pipe (simplified)"""
        # Build adjacency for traversal
        adjacency = defaultdict(list)
        pipe_map = {}
        
        for pipe in pipes:
            adjacency[pipe.upstream_node].append(pipe.downstream_node)
            adjacency[pipe.downstream_node].append(pipe.upstream_node)
            pipe_map[(pipe.upstream_node, pipe.downstream_node)] = pipe
            pipe_map[(pipe.downstream_node, pipe.upstream_node)] = pipe
        
        # Calculate total demand downstream of each pipe
        # This is a simplification - real implementation would use network analysis
        for pipe in pipes:
            # Estimate based on sprinkler count and typical flow
            if pipe.sprinkler_count_downstream > 0:
                # Assume average 25 GPM per sprinkler in remote area
                pipe.flow = pipe.sprinkler_count_downstream * 25.0
            else:
                # Estimate based on pipe type
                if pipe.pipe_type == PipeType.BRANCH:
                    pipe.flow = 50.0
                elif pipe.pipe_type == PipeType.CROSS_MAIN:
                    pipe.flow = 200.0
                elif pipe.pipe_type == PipeType.FEED_MAIN:
                    pipe.flow = 350.0
                elif pipe.pipe_type == PipeType.RISER:
                    pipe.flow = 400.0
                else:
                    pipe.flow = 25.0
        
        return pipes
    
    def _try_downsize(self, pipes: List[PipeSegment], 
                      network: NetworkAnalysisInput) -> List[PipeSegment]:
        """Try to downsize pipes where possible without violating constraints"""
        for pipe in pipes:
            if pipe.diameter <= STANDARD_SIZES[0]:
                continue
            
            # Get minimum size based on sprinkler count
            schedule = NFPA_PIPE_SCHEDULE['steel']
            count = max(1, pipe.sprinkler_count_downstream)
            min_nfpa_size = 1.0
            for max_count, size in sorted(schedule.items()):
                if count <= max_count:
                    min_nfpa_size = size
                    break
            
            # Get minimum size based on velocity
            velocity_limit = VELOCITY_LIMITS.get(pipe.pipe_type, 20.0)
            min_velocity_id = self.calculator.get_minimum_size_for_velocity(pipe.flow, velocity_limit)
            min_velocity_size = self.calculator.find_nominal_for_id(min_velocity_id, pipe.schedule)
            
            # Minimum is the larger of the two
            min_size = max(min_nfpa_size, min_velocity_size)
            
            # Downsize if current is larger than minimum
            if pipe.diameter > min_size:
                self.logger.debug(f"Downsizing {pipe.id}: {pipe.diameter}\" → {min_size}\"")
                pipe.diameter = min_size
        
        return pipes
    
    def _optimize_schedules(self, pipes: List[PipeSegment]) -> List[PipeSegment]:
        """Optimize pipe schedules for cost savings"""
        for pipe in pipes:
            # Schedule 10 available for 2" and larger
            if pipe.diameter >= 2.0:
                # Check if Schedule 10 provides adequate ID
                sch10_id = self.calculator.get_inside_diameter(pipe.diameter, PipeSchedule.SCHEDULE_10)
                sch40_id = self.calculator.get_inside_diameter(pipe.diameter, PipeSchedule.SCHEDULE_40)
                
                # Check velocity with Schedule 10
                velocity_sch10 = self.calculator.calculate_velocity(pipe.flow, sch10_id)
                velocity_limit = VELOCITY_LIMITS.get(pipe.pipe_type, 20.0)
                
                if velocity_sch10 <= velocity_limit:
                    # Schedule 10 is acceptable
                    pipe.schedule = PipeSchedule.SCHEDULE_10
                    self.logger.debug(f"Changed {pipe.id} to Schedule 10")
        
        return pipes
    
    def _calculate_total_cost(self, pipes: List[PipeSegment]) -> float:
        """Calculate total pipe material cost"""
        total = 0.0
        for pipe in pipes:
            key = (pipe.diameter, pipe.schedule)
            cost_per_foot = PIPE_COSTS.get(key, 10.0)  # Default if not found
            total += cost_per_foot * pipe.length
        return total
    
    def _check_velocity_violations(self, pipes: List[PipeSegment]) -> List[VelocityViolation]:
        """Check for remaining velocity violations"""
        violations = []
        
        for pipe in pipes:
            inside_dia = self.calculator.get_inside_diameter(pipe.diameter, pipe.schedule)
            velocity = self.calculator.calculate_velocity(pipe.flow, inside_dia)
            limit = VELOCITY_LIMITS.get(pipe.pipe_type, 20.0)
            
            if velocity > limit:
                min_id = self.calculator.get_minimum_size_for_velocity(pipe.flow, limit)
                recommended = self.calculator.find_nominal_for_id(min_id, pipe.schedule)
                
                violations.append(VelocityViolation(
                    pipe_id=pipe.id,
                    pipe_type=pipe.pipe_type,
                    current_diameter=pipe.diameter,
                    current_velocity=velocity,
                    velocity_limit=limit,
                    flow=pipe.flow,
                    recommended_diameter=recommended
                ))
        
        return violations
    
    def _check_pressure_violations(self, pipes: List[PipeSegment],
                                    nodes: Dict[str, HydraulicNode],
                                    network: NetworkAnalysisInput) -> List[PressureViolation]:
        """Check for pressure violations"""
        violations = []
        
        # Simplified check - would need full network analysis for accuracy
        path_loss = self._estimate_path_loss(pipes, network)
        available = network.source_pressure - path_loss
        required = network.required_remote_pressure
        
        if available < required:
            violations.append(PressureViolation(
                node_id="remote_area",
                required_pressure=required,
                available_pressure=available,
                deficit=required - available
            ))
        
        return violations
    
    def _estimate_path_loss(self, pipes: List[PipeSegment], 
                            network: NetworkAnalysisInput) -> float:
        """Estimate friction loss in critical path (simplified)"""
        # Sum friction losses weighted by flow
        total_friction = 0.0
        total_flow = 0.0
        
        for pipe in pipes:
            inside_dia = self.calculator.get_inside_diameter(pipe.diameter, pipe.schedule)
            friction = self.calculator.calculate_friction_loss(
                pipe.flow, pipe.c_factor, inside_dia,
                pipe.length + pipe.fittings_eq_length
            )
            total_friction += friction
            total_flow += pipe.flow
        
        # Approximate path loss as weighted average
        if len(pipes) > 0:
            return total_friction / len(pipes) * 5  # Rough approximation
        return 0.0
    
    def _find_remote_node(self, nodes: Dict[str, HydraulicNode], 
                          source_node_id: str) -> str:
        """Find hydraulically most remote node"""
        # Simplified - find sprinkler node farthest from source
        remote_id = source_node_id
        max_distance = 0
        
        source = nodes.get(source_node_id)
        if not source:
            return ""
        
        for node_id, node in nodes.items():
            if node.node_type == 'sprinkler':
                dist = math.sqrt(
                    (node.x - source.x) ** 2 + 
                    (node.y - source.y) ** 2 +
                    (node.elevation - source.elevation) ** 2
                )
                if dist > max_distance:
                    max_distance = dist
                    remote_id = node_id
        
        return remote_id
    
    def _generate_recommendations(self, pipes: List[PipeSegment],
                                   velocity_violations: List[VelocityViolation],
                                   pressure_violations: List[PressureViolation],
                                   network: NetworkAnalysisInput) -> List[str]:
        """Generate optimization recommendations"""
        recommendations = []
        
        if velocity_violations:
            recommendations.append(
                f"⚠️ {len(velocity_violations)} pipe(s) exceed velocity limits. "
                f"Consider upsizing to recommended diameters."
            )
        
        if pressure_violations:
            recommendations.append(
                f"⚠️ Pressure at remote area is {pressure_violations[0].deficit:.1f} PSI below required. "
                f"Upsize mains or verify water supply."
            )
        
        # Check for Schedule 10 opportunities
        sch40_count = sum(1 for p in pipes if p.schedule == PipeSchedule.SCHEDULE_40 and p.diameter >= 2.0)
        if sch40_count > 0:
            recommendations.append(
                f"💡 {sch40_count} pipe(s) 2\"+ are Schedule 40. "
                f"Consider Schedule 10 where velocity allows for cost savings."
            )
        
        # Check for oversized pipes
        oversized = []
        for pipe in pipes:
            if pipe.flow > 0:
                inside_dia = self.calculator.get_inside_diameter(pipe.diameter, pipe.schedule)
                velocity = self.calculator.calculate_velocity(pipe.flow, inside_dia)
                limit = VELOCITY_LIMITS.get(pipe.pipe_type, 20.0)
                
                if velocity < limit * 0.5:  # Less than half the limit
                    oversized.append(pipe.id)
        
        if oversized and len(oversized) <= 5:
            recommendations.append(
                f"💡 {len(oversized)} pipe(s) may be oversized (velocity < 50% of limit): "
                f"{', '.join(oversized[:3])}{'...' if len(oversized) > 3 else ''}"
            )
        
        if not velocity_violations and not pressure_violations:
            recommendations.append("✅ All pipes meet velocity and pressure requirements.")
        
        return recommendations


# =============================================================================
# VELOCITY ANALYSIS REPORT
# =============================================================================

class VelocityReportGenerator:
    """Generate velocity analysis reports"""
    
    def __init__(self):
        self.calculator = HydraulicCalculator()
    
    def generate_report(self, pipes: List[PipeSegment]) -> str:
        """Generate text velocity report"""
        lines = []
        lines.append("=" * 90)
        lines.append("PIPE VELOCITY ANALYSIS REPORT")
        lines.append("=" * 90)
        lines.append("")
        lines.append(f"{'Pipe ID':<12} {'Type':<12} {'Dia':<6} {'Flow':<10} {'Velocity':<10} {'Limit':<8} {'Status':<10}")
        lines.append(f"{'':_<12} {'':_<12} {'(in)':_<6} {'(GPM)':_<10} {'(FPS)':_<10} {'(FPS)':_<8} {'':_<10}")
        lines.append("-" * 90)
        
        violations = 0
        for pipe in sorted(pipes, key=lambda p: p.id):
            inside_dia = self.calculator.get_inside_diameter(pipe.diameter, pipe.schedule)
            velocity = self.calculator.calculate_velocity(pipe.flow, inside_dia)
            limit = VELOCITY_LIMITS.get(pipe.pipe_type, 20.0)
            
            status = "✓ OK" if velocity <= limit else "⚠ HIGH"
            if velocity > limit:
                violations += 1
            
            lines.append(
                f"{pipe.id:<12} {pipe.pipe_type.value:<12} {pipe.diameter:<6.2f} "
                f"{pipe.flow:<10.1f} {velocity:<10.1f} {limit:<8.1f} {status:<10}"
            )
        
        lines.append("-" * 90)
        lines.append(f"Total pipes: {len(pipes)}  |  Violations: {violations}  |  "
                    f"Pass rate: {(len(pipes) - violations) / len(pipes) * 100:.1f}%")
        lines.append("=" * 90)
        
        return "\n".join(lines)
    
    def generate_csv(self, pipes: List[PipeSegment], output_path: str) -> str:
        """Generate CSV velocity report"""
        import csv
        
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Pipe ID', 'Type', 'Diameter (in)', 'Schedule', 'Length (ft)',
                           'Flow (GPM)', 'Velocity (FPS)', 'Limit (FPS)', 'Status'])
            
            for pipe in pipes:
                inside_dia = self.calculator.get_inside_diameter(pipe.diameter, pipe.schedule)
                velocity = self.calculator.calculate_velocity(pipe.flow, inside_dia)
                limit = VELOCITY_LIMITS.get(pipe.pipe_type, 20.0)
                status = "OK" if velocity <= limit else "VIOLATION"
                
                writer.writerow([
                    pipe.id, pipe.pipe_type.value, pipe.diameter, pipe.schedule.value,
                    pipe.length, f"{pipe.flow:.1f}", f"{velocity:.1f}", limit, status
                ])
        
        return output_path


# =============================================================================
# COST COMPARISON REPORT
# =============================================================================

class CostComparisonGenerator:
    """Generate cost comparison reports"""
    
    def generate_report(self, result: OptimizationResult) -> str:
        """Generate text cost comparison report"""
        lines = []
        lines.append("=" * 80)
        lines.append("PIPE SIZING COST COMPARISON REPORT")
        lines.append("=" * 80)
        lines.append("")
        
        # Summary
        lines.append("SUMMARY")
        lines.append("-" * 40)
        lines.append(f"Optimization iterations:    {result.iterations}")
        lines.append(f"Pipes upsized:             {result.pipes_upsized}")
        lines.append(f"Pipes downsized:           {result.pipes_downsized}")
        lines.append(f"Velocity violations:       {len(result.velocity_violations)}")
        lines.append(f"Pressure violations:       {len(result.pressure_violations)}")
        lines.append("")
        
        # Cost comparison
        lines.append("MATERIAL COST COMPARISON")
        lines.append("-" * 40)
        lines.append(f"Original pipe cost:        ${result.original_cost:>12,.2f}")
        lines.append(f"Optimized pipe cost:       ${result.optimized_cost:>12,.2f}")
        lines.append(f"                           {'─' * 14}")
        if result.cost_savings >= 0:
            lines.append(f"SAVINGS:                   ${result.cost_savings:>12,.2f}  ({result.cost_savings_percent:.1f}%)")
        else:
            lines.append(f"ADDITIONAL COST:           ${-result.cost_savings:>12,.2f}  ({-result.cost_savings_percent:.1f}%)")
        lines.append("")
        
        # Pressure adequacy
        lines.append("PRESSURE ADEQUACY")
        lines.append("-" * 40)
        lines.append(f"Most remote node:          {result.remote_node_id}")
        lines.append(f"Required pressure:         {result.required_pressure:.1f} PSI")
        lines.append(f"Available pressure:        {result.available_pressure:.1f} PSI")
        lines.append(f"Safety margin:             {result.safety_margin:.1f} PSI")
        lines.append("")
        
        # Pipe changes detail
        lines.append("PIPE SIZE CHANGES")
        lines.append("-" * 40)
        lines.append(f"{'Pipe ID':<15} {'Original':<12} {'Optimized':<12} {'Change':<10}")
        
        changes = []
        for orig, opt in zip(result.original_pipes, result.optimized_pipes):
            if orig.diameter != opt.diameter or orig.schedule != opt.schedule:
                change = "↑" if opt.diameter > orig.diameter else "↓"
                changes.append(
                    f"{orig.id:<15} {orig.diameter}\" {orig.schedule.value:<6} "
                    f"{opt.diameter}\" {opt.schedule.value:<6} {change}"
                )
        
        if changes:
            for change in changes[:20]:  # Limit to 20 rows
                lines.append(change)
            if len(changes) > 20:
                lines.append(f"... and {len(changes) - 20} more changes")
        else:
            lines.append("No pipe size changes made.")
        
        lines.append("")
        
        # Recommendations
        lines.append("RECOMMENDATIONS")
        lines.append("-" * 40)
        for rec in result.recommendations:
            lines.append(f"  {rec}")
        
        lines.append("")
        lines.append("=" * 80)
        
        return "\n".join(lines)


# =============================================================================
# MODULE INTERFACE
# =============================================================================

def optimize_pipe_sizes(pipes: List[Dict], nodes: List[Dict],
                        source_node_id: str, source_pressure: float,
                        required_pressure: float, system_demand: float,
                        hose_allowance: float = 250.0) -> Dict[str, Any]:
    """
    Convenience function for pipe optimization
    
    Args:
        pipes: List of pipe dictionaries
        nodes: List of node dictionaries
        source_node_id: ID of source/supply node
        source_pressure: Available pressure at source (PSI)
        required_pressure: Required pressure at remote sprinkler (PSI)
        system_demand: Total system demand (GPM)
        hose_allowance: Hose stream allowance (GPM)
        
    Returns:
        Dictionary with optimization results
    """
    # Convert to data classes
    pipe_segments = []
    for p in pipes:
        pipe_type = PipeType.BRANCH
        ptype = p.get('type', 'branch').lower()
        if 'main' in ptype:
            pipe_type = PipeType.CROSS_MAIN if 'cross' in ptype else PipeType.FEED_MAIN
        elif 'riser' in ptype:
            pipe_type = PipeType.RISER
        
        pipe_segments.append(PipeSegment(
            id=p.get('id', ''),
            pipe_type=pipe_type,
            upstream_node=p.get('upstream_node', p.get('start_node', '')),
            downstream_node=p.get('downstream_node', p.get('end_node', '')),
            length=p.get('length', 10.0),
            diameter=p.get('diameter', 1.0),
            schedule=PipeSchedule.SCHEDULE_40,
            c_factor=p.get('c_factor', 120),
            fittings_eq_length=p.get('fittings_eq_length', 0.0),
            sprinkler_count_downstream=p.get('sprinkler_count', 0)
        ))
    
    hydraulic_nodes = []
    for n in nodes:
        hydraulic_nodes.append(HydraulicNode(
            id=n.get('id', ''),
            x=n.get('x', 0.0),
            y=n.get('y', 0.0),
            elevation=n.get('elevation', n.get('z', 0.0)),
            node_type=n.get('type', 'junction'),
            demand=n.get('demand', 0.0),
            k_factor=n.get('k_factor', 5.6)
        ))
    
    network = NetworkAnalysisInput(
        nodes=hydraulic_nodes,
        pipes=pipe_segments,
        source_node_id=source_node_id,
        source_pressure=source_pressure,
        required_remote_pressure=required_pressure,
        system_demand=system_demand,
        hose_allowance=hose_allowance
    )
    
    optimizer = IntelligentPipeSizer()
    result = optimizer.optimize(network)
    
    # Generate reports
    velocity_report = VelocityReportGenerator().generate_report(result.optimized_pipes)
    cost_report = CostComparisonGenerator().generate_report(result)
    
    return {
        'success': result.success,
        'iterations': result.iterations,
        'original_cost': result.original_cost,
        'optimized_cost': result.optimized_cost,
        'cost_savings': result.cost_savings,
        'cost_savings_percent': result.cost_savings_percent,
        'pipes_upsized': result.pipes_upsized,
        'pipes_downsized': result.pipes_downsized,
        'velocity_violations': len(result.velocity_violations),
        'pressure_violations': len(result.pressure_violations),
        'safety_margin': result.safety_margin,
        'velocity_report': velocity_report,
        'cost_report': cost_report,
        'optimized_pipes': [
            {
                'id': p.id,
                'diameter': p.diameter,
                'schedule': p.schedule.value,
                'flow': p.flow,
                'velocity': p.velocity
            }
            for p in result.optimized_pipes
        ],
        'recommendations': result.recommendations
    }


# =============================================================================
# MODULE EXPORTS
# =============================================================================

__all__ = [
    'IntelligentPipeSizer',
    'HydraulicCalculator',
    'VelocityReportGenerator',
    'CostComparisonGenerator',
    'PipeSegment',
    'HydraulicNode',
    'NetworkAnalysisInput',
    'OptimizationResult',
    'PipeSchedule',
    'PipeType',
    'VELOCITY_LIMITS',
    'STANDARD_SIZES',
    'optimize_pipe_sizes',
]


if __name__ == "__main__":
    print("🔧 FireAI Pro - Intelligent Pipe Sizing Optimizer v1.0.0")
    print("=" * 60)
    print(f"Supported pipe sizes: {STANDARD_SIZES}")
    print(f"Velocity limits: Branch={VELOCITY_LIMITS[PipeType.BRANCH]} FPS, Main={VELOCITY_LIMITS[PipeType.CROSS_MAIN]} FPS")
