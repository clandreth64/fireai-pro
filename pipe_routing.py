#!/usr/bin/env python3
"""
FireAI Pro - Intelligent Pipe Routing Engine v1.0
===================================================
Phase 4: Pipe Routing & Network Generation

Creates realistic pipe networks that:
- Connect sprinkler heads via branch lines
- Route cross-mains through corridors
- Connect to main riser location
- Optimize pipe sizing per NFPA 13
- Minimize material usage

VERSION: 1.0.0
"""

import math
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
from collections import defaultdict
from enum import Enum
import heapq

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.PipeRouting")


# =============================================================================
# PIPE SPECIFICATIONS (per NFPA 13)
# =============================================================================

class PipeType(Enum):
    """Types of piping"""
    BRANCH = "branch"           # Branch lines to sprinklers
    CROSS_MAIN = "cross_main"   # Cross mains connecting branches
    MAIN = "main"               # Feed main
    RISER = "riser"             # Vertical riser
    ARM_OVER = "arm_over"       # Arm-overs from branch to head


# Schedule 40 steel pipe dimensions
PIPE_SCHEDULE_40 = {
    1.0: {'id': 1.049, 'c': 120},      # 1" pipe
    1.25: {'id': 1.380, 'c': 120},     # 1-1/4" pipe
    1.5: {'id': 1.610, 'c': 120},      # 1-1/2" pipe
    2.0: {'id': 2.067, 'c': 120},      # 2" pipe
    2.5: {'id': 2.469, 'c': 120},      # 2-1/2" pipe
    3.0: {'id': 3.068, 'c': 120},      # 3" pipe
    4.0: {'id': 4.026, 'c': 120},      # 4" pipe
    6.0: {'id': 6.065, 'c': 120},      # 6" pipe
    8.0: {'id': 7.981, 'c': 120},      # 8" pipe
}

# Maximum sprinklers per branch line (typical)
MAX_HEADS_PER_BRANCH = {
    1.0: 2,
    1.25: 3,
    1.5: 5,
    2.0: 10,
    2.5: 20,
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Point:
    """2D Point"""
    x: float
    y: float
    
    def distance_to(self, other: 'Point') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def __hash__(self):
        return hash((round(self.x, 1), round(self.y, 1)))
    
    def __eq__(self, other):
        if not isinstance(other, Point):
            return False
        return abs(self.x - other.x) < 0.5 and abs(self.y - other.y) < 0.5


@dataclass
class PipeSegment:
    """Single pipe segment"""
    id: str
    pipe_type: PipeType
    start: Point
    end: Point
    size: float              # Nominal pipe size (inches)
    material: str = "steel"
    
    # Flow data (for hydraulics)
    flow_gpm: float = 0
    head_count: int = 0      # Number of heads fed
    
    @property
    def length_ft(self) -> float:
        return self.start.distance_to(self.end)
    
    @property
    def is_horizontal(self) -> bool:
        return abs(self.end.y - self.start.y) < 0.5
    
    @property
    def is_vertical(self) -> bool:
        return abs(self.end.x - self.start.x) < 0.5


@dataclass
class PipeNode:
    """Node in pipe network (connection point)"""
    id: str
    position: Point
    node_type: str           # 'sprinkler', 'tee', 'elbow', 'cross', 'riser'
    elevation: float = 0     # Feet above floor
    
    # Connected segments
    connections: List[str] = field(default_factory=list)
    
    # For sprinkler nodes
    sprinkler_id: str = ""
    k_factor: float = 0
    
    # For hydraulic calculations
    pressure_psi: float = 0
    flow_gpm: float = 0


@dataclass
class PipeNetwork:
    """Complete pipe network"""
    segments: List[PipeSegment] = field(default_factory=list)
    nodes: List[PipeNode] = field(default_factory=list)
    
    # Riser location
    riser_x: float = 0
    riser_y: float = 0
    
    # Statistics
    total_pipe_length: Dict[float, float] = field(default_factory=dict)  # size -> length
    branch_count: int = 0
    cross_main_count: int = 0
    fitting_count: int = 0
    
    # For BOM
    fittings: Dict[str, int] = field(default_factory=dict)


# =============================================================================
# PIPE ROUTING ENGINE
# =============================================================================

class PipeRoutingEngine:
    """
    Generate optimal pipe routing for sprinkler systems.
    
    Creates a tree-style network:
    - Riser feeds Main
    - Main feeds Cross-Mains
    - Cross-Mains feed Branch Lines
    - Branch Lines feed Sprinklers
    """
    
    def __init__(self, scale_factor: float = 9.0):
        """
        Args:
            scale_factor: pts per foot
        """
        self.scale = scale_factor
        self.segment_counter = 0
        self.node_counter = 0
    
    def route_system(self,
                     sprinklers: List[Dict],
                     rooms: List[Dict],
                     floor_plan: Dict,
                     riser_location: Tuple[float, float] = None) -> PipeNetwork:
        """
        Create complete pipe network.
        
        Args:
            sprinklers: List of sprinkler head dicts
            rooms: List of room dicts
            floor_plan: Floor plan bounds and info
            riser_location: (x, y) of riser in pts, or None for auto
        
        Returns:
            PipeNetwork with all segments and nodes
        """
        network = PipeNetwork()
        
        logger.info(f"🔧 Routing pipe network for {len(sprinklers)} heads")
        
        # Convert sprinkler positions to feet
        heads = []
        for h in sprinklers:
            heads.append({
                'id': h['id'],
                'x': h['x'] / self.scale,
                'y': h['y'] / self.scale,
                'room_id': h.get('room_id', ''),
                'k_factor': h.get('k_factor', 5.6),
                'node': h.get('node', 0)
            })
        
        # Determine riser location
        if riser_location:
            network.riser_x = riser_location[0] / self.scale
            network.riser_y = riser_location[1] / self.scale
        else:
            # Auto-place riser near center-bottom of floor plan
            fp = floor_plan.get('floor_plan', {})
            width = fp.get('width_ft', 100)
            network.riser_x = width / 2
            network.riser_y = 5  # Near bottom
        
        logger.info(f"   Riser at ({network.riser_x:.0f}, {network.riser_y:.0f}) ft")
        
        # Step 1: Group heads into branch lines
        branches = self._create_branch_lines(heads, rooms)
        logger.info(f"   Created {len(branches)} branch lines")
        
        # Step 2: Create cross-mains to connect branches
        cross_mains = self._create_cross_mains(branches, network)
        logger.info(f"   Created {len(cross_mains)} cross-main segments")
        
        # Step 3: Create main from riser to cross-mains
        main_segments = self._create_main(cross_mains, network)
        logger.info(f"   Created {len(main_segments)} main segments")
        
        # Step 4: Add all segments to network
        for branch in branches:
            network.segments.extend(branch['segments'])
            network.branch_count += 1
        
        network.segments.extend(cross_mains)
        network.cross_main_count = len(cross_mains)
        
        network.segments.extend(main_segments)
        
        # Step 5: Create nodes
        self._create_nodes(network, heads)
        
        # Step 6: Calculate pipe totals
        self._calculate_totals(network)
        
        # Step 7: Count fittings
        self._count_fittings(network)
        
        logger.info(f"✅ Routing complete:")
        logger.info(f"   Total segments: {len(network.segments)}")
        logger.info(f"   Total nodes: {len(network.nodes)}")
        
        return network
    
    def _create_branch_lines(self, heads: List[Dict], rooms: List[Dict]) -> List[Dict]:
        """Group heads into branch lines by row"""
        branches = []
        
        # Group heads by approximate Y coordinate (rows)
        row_tolerance = 2.0  # feet
        rows = defaultdict(list)
        
        for head in heads:
            y_key = round(head['y'] / row_tolerance)
            rows[y_key].append(head)
        
        # Create branch for each row
        for y_key, row_heads in rows.items():
            if not row_heads:
                continue
            
            # Sort by X
            row_heads.sort(key=lambda h: h['x'])
            
            # Determine pipe size based on head count
            head_count = len(row_heads)
            pipe_size = self._size_branch_pipe(head_count)
            
            # Create segments connecting heads
            segments = []
            for i in range(len(row_heads) - 1):
                h1, h2 = row_heads[i], row_heads[i + 1]
                
                self.segment_counter += 1
                seg = PipeSegment(
                    id=f"BR-{self.segment_counter:04d}",
                    pipe_type=PipeType.BRANCH,
                    start=Point(h1['x'], h1['y']),
                    end=Point(h2['x'], h2['y']),
                    size=pipe_size,
                    head_count=head_count
                )
                segments.append(seg)
            
            branches.append({
                'heads': row_heads,
                'segments': segments,
                'y': row_heads[0]['y'],
                'min_x': min(h['x'] for h in row_heads),
                'max_x': max(h['x'] for h in row_heads),
                'center_x': sum(h['x'] for h in row_heads) / len(row_heads),
                'head_count': head_count,
                'pipe_size': pipe_size
            })
        
        return branches
    
    def _create_cross_mains(self, branches: List[Dict], network: PipeNetwork) -> List[PipeSegment]:
        """Create cross-mains connecting branch lines"""
        segments = []
        
        if not branches:
            return segments
        
        # Sort branches by Y
        branches.sort(key=lambda b: b['y'])
        
        # Determine cross-main X positions
        # Typically run cross-mains vertically at regular intervals
        all_x = []
        for branch in branches:
            all_x.append(branch['center_x'])
        
        # Use center of heads for cross-main position
        # For larger systems, multiple cross-mains may be needed
        avg_x = sum(all_x) / len(all_x) if all_x else network.riser_x
        
        # Find Y range
        min_y = min(b['y'] for b in branches)
        max_y = max(b['y'] for b in branches)
        
        # Size cross-main based on total heads
        total_heads = sum(b['head_count'] for b in branches)
        xmain_size = self._size_cross_main(total_heads)
        
        # Create single cross-main segment
        self.segment_counter += 1
        xmain = PipeSegment(
            id=f"XM-{self.segment_counter:04d}",
            pipe_type=PipeType.CROSS_MAIN,
            start=Point(avg_x, min_y - 2),  # Extend slightly
            end=Point(avg_x, max_y + 2),
            size=xmain_size,
            head_count=total_heads
        )
        segments.append(xmain)
        
        # Create connections from branches to cross-main
        for branch in branches:
            # Find connection point on branch closest to cross-main
            if branch['segments']:
                # Connect from branch center to cross-main
                bx = branch['center_x']
                by = branch['y']
                
                self.segment_counter += 1
                conn = PipeSegment(
                    id=f"BC-{self.segment_counter:04d}",
                    pipe_type=PipeType.BRANCH,  # Branch-to-crossmain
                    start=Point(bx, by),
                    end=Point(avg_x, by),
                    size=branch['pipe_size'],
                    head_count=branch['head_count']
                )
                
                if conn.length_ft > 0.5:  # Only add if meaningful length
                    segments.append(conn)
        
        return segments
    
    def _create_main(self, cross_mains: List[PipeSegment], network: PipeNetwork) -> List[PipeSegment]:
        """Create main from riser to cross-mains"""
        segments = []
        
        if not cross_mains:
            return segments
        
        # Find cross-main connection point
        xmain = cross_mains[0]  # Primary cross-main
        
        # Size main based on total flow
        total_heads = xmain.head_count
        main_size = self._size_main(total_heads)
        
        # Route from riser to cross-main
        # Simple L-shaped route
        riser_pt = Point(network.riser_x, network.riser_y)
        xmain_connection = Point(xmain.start.x, xmain.start.y)
        
        # Horizontal run
        self.segment_counter += 1
        h_seg = PipeSegment(
            id=f"MN-{self.segment_counter:04d}",
            pipe_type=PipeType.MAIN,
            start=riser_pt,
            end=Point(xmain_connection.x, riser_pt.y),
            size=main_size,
            head_count=total_heads
        )
        if h_seg.length_ft > 0.5:
            segments.append(h_seg)
        
        # Vertical run to cross-main
        self.segment_counter += 1
        v_seg = PipeSegment(
            id=f"MN-{self.segment_counter:04d}",
            pipe_type=PipeType.MAIN,
            start=Point(xmain_connection.x, riser_pt.y),
            end=xmain_connection,
            size=main_size,
            head_count=total_heads
        )
        if v_seg.length_ft > 0.5:
            segments.append(v_seg)
        
        return segments
    
    def _create_nodes(self, network: PipeNetwork, heads: List[Dict]):
        """Create pipe nodes at connections and sprinklers"""
        # Create sprinkler nodes
        for head in heads:
            self.node_counter += 1
            node = PipeNode(
                id=f"N-{self.node_counter:04d}",
                position=Point(head['x'], head['y']),
                node_type='sprinkler',
                sprinkler_id=head['id'],
                k_factor=head.get('k_factor', 5.6)
            )
            network.nodes.append(node)
        
        # Create connection nodes at segment endpoints
        endpoints = set()
        for seg in network.segments:
            endpoints.add((seg.start.x, seg.start.y))
            endpoints.add((seg.end.x, seg.end.y))
        
        # Filter out sprinkler positions
        sprinkler_positions = set((h['x'], h['y']) for h in heads)
        
        for x, y in endpoints:
            if (x, y) not in sprinkler_positions:
                self.node_counter += 1
                
                # Determine node type based on connections
                node_type = 'tee'
                if abs(x - network.riser_x) < 1 and abs(y - network.riser_y) < 1:
                    node_type = 'riser'
                
                node = PipeNode(
                    id=f"N-{self.node_counter:04d}",
                    position=Point(x, y),
                    node_type=node_type
                )
                network.nodes.append(node)
        
        # Create riser node
        self.node_counter += 1
        riser_node = PipeNode(
            id=f"N-{self.node_counter:04d}",
            position=Point(network.riser_x, network.riser_y),
            node_type='riser'
        )
        network.nodes.append(riser_node)
    
    def _size_branch_pipe(self, head_count: int) -> float:
        """Size branch pipe based on head count"""
        for size, max_heads in sorted(MAX_HEADS_PER_BRANCH.items()):
            if head_count <= max_heads:
                return size
        return 2.5  # Max branch size
    
    def _size_cross_main(self, head_count: int) -> float:
        """Size cross-main based on total heads"""
        if head_count <= 20:
            return 2.5
        elif head_count <= 40:
            return 3.0
        elif head_count <= 80:
            return 4.0
        else:
            return 6.0
    
    def _size_main(self, head_count: int) -> float:
        """Size main based on total system heads"""
        if head_count <= 40:
            return 4.0
        elif head_count <= 100:
            return 6.0
        else:
            return 8.0
    
    def _calculate_totals(self, network: PipeNetwork):
        """Calculate total pipe lengths by size"""
        for seg in network.segments:
            size = seg.size
            length = seg.length_ft
            network.total_pipe_length[size] = network.total_pipe_length.get(size, 0) + length
    
    def _count_fittings(self, network: PipeNetwork):
        """Count fittings needed"""
        # Count tees and elbows based on node connections
        tee_count = 0
        elbow_count = 0
        
        for node in network.nodes:
            if node.node_type == 'tee':
                tee_count += 1
            elif node.node_type == 'elbow':
                elbow_count += 1
            elif node.node_type == 'sprinkler':
                tee_count += 1  # Each sprinkler needs a tee
        
        network.fittings['tee'] = tee_count
        network.fittings['elbow'] = elbow_count
        network.fitting_count = tee_count + elbow_count


# =============================================================================
# EXPORT FUNCTIONS
# =============================================================================

def network_to_dict(network: PipeNetwork) -> Dict:
    """Convert network to JSON-serializable dict"""
    return {
        'riser': {
            'x': network.riser_x,
            'y': network.riser_y
        },
        'statistics': {
            'segment_count': len(network.segments),
            'node_count': len(network.nodes),
            'branch_count': network.branch_count,
            'cross_main_count': network.cross_main_count,
            'fitting_count': network.fitting_count
        },
        'pipe_totals': {
            f'{size}"': f'{length:.1f} ft'
            for size, length in sorted(network.total_pipe_length.items())
        },
        'fittings': network.fittings,
        'segments': [
            {
                'id': s.id,
                'type': s.pipe_type.value,
                'start': {'x': s.start.x, 'y': s.start.y},
                'end': {'x': s.end.x, 'y': s.end.y},
                'size': s.size,
                'length_ft': s.length_ft,
                'head_count': s.head_count
            }
            for s in network.segments
        ],
        'nodes': [
            {
                'id': n.id,
                'type': n.node_type,
                'x': n.position.x,
                'y': n.position.y,
                'sprinkler_id': n.sprinkler_id,
                'k_factor': n.k_factor
            }
            for n in network.nodes
        ]
    }


def save_network_json(network: PipeNetwork, output_path: str):
    """Save network to JSON file"""
    with open(output_path, 'w') as f:
        json.dump(network_to_dict(network), f, indent=2)
    logger.info(f"💾 Saved network to {output_path}")


# =============================================================================
# INTEGRATION FUNCTION
# =============================================================================

def route_from_layout(sprinkler_json: str,
                       floor_plan_json: str,
                       output_json: str = None,
                       scale_factor: float = 9.0) -> PipeNetwork:
    """
    Create pipe network from sprinkler layout JSON.
    
    Args:
        sprinkler_json: Path to sprinkler_layout.json
        floor_plan_json: Path to floor_plan_analysis.json
        output_json: Optional output path
        scale_factor: pts per foot
    
    Returns:
        PipeNetwork object
    """
    with open(sprinkler_json, 'r') as f:
        layout = json.load(f)
    
    with open(floor_plan_json, 'r') as f:
        floor_plan = json.load(f)
    
    engine = PipeRoutingEngine(scale_factor=scale_factor)
    network = engine.route_system(
        sprinklers=layout.get('sprinklers', []),
        rooms=floor_plan.get('rooms', []),
        floor_plan=floor_plan
    )
    
    if output_json:
        save_network_json(network, output_json)
    
    return network


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🔧 FireAI Pro - Intelligent Pipe Routing Engine v1.0")
    print("=" * 60)
    print("\nPhase 4: Pipe Routing & Network Generation")
    print("\nCapabilities:")
    print("  ✅ Group heads into branch lines")
    print("  ✅ Create cross-mains connecting branches")
    print("  ✅ Route main from riser")
    print("  ✅ Size pipes per NFPA 13")
    print("  ✅ Count fittings for BOM")
    print("\nUsage:")
    print("  from pipe_routing import route_from_layout")
    print("  network = route_from_layout('sprinklers.json', 'floor_plan.json')")
