#!/usr/bin/env python3
"""
FireAI Pro - Professional DXF Generator v2.0
=============================================
Phase 5: Professional DXF Output

Generates AutoCAD-compatible DXF files with:
- Floor plan walls as background
- Sprinkler heads placed in actual positions
- Branch lines connecting heads
- Standard symbols and annotations
- Title block and schedules

VERSION: 2.0.0
"""

import math
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.DXFGenerator")

# Check for ezdxf
try:
    import ezdxf
    from ezdxf import units
    from ezdxf.enums import TextEntityAlignment
    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False
    logger.warning("ezdxf not available - DXF generation disabled")


# =============================================================================
# STANDARD LAYERS
# =============================================================================

LAYERS = {
    # Background layers
    'A-WALL': {'color': 8, 'linetype': 'CONTINUOUS', 'description': 'Building Walls'},
    'A-WALL-EXTR': {'color': 7, 'linetype': 'CONTINUOUS', 'description': 'Exterior Walls'},
    'A-GRID': {'color': 9, 'linetype': 'CONTINUOUS', 'description': 'Grid Lines'},
    
    # Fire protection layers
    'FP-SPKR': {'color': 1, 'linetype': 'CONTINUOUS', 'description': 'Sprinkler Heads'},
    'FP-SPKR-UPRT': {'color': 1, 'linetype': 'CONTINUOUS', 'description': 'Upright Sprinklers'},
    'FP-SPKR-ESFR': {'color': 1, 'linetype': 'CONTINUOUS', 'description': 'ESFR Sprinklers'},
    'FP-PIPE-BRCH': {'color': 1, 'linetype': 'CONTINUOUS', 'description': 'Branch Lines'},
    'FP-PIPE-MAIN': {'color': 1, 'linetype': 'CONTINUOUS', 'description': 'Main Lines'},
    'FP-PIPE-XMAIN': {'color': 1, 'linetype': 'CONTINUOUS', 'description': 'Cross Mains'},
    'FP-ANNO': {'color': 7, 'linetype': 'CONTINUOUS', 'description': 'Annotations'},
    'FP-NODE': {'color': 3, 'linetype': 'CONTINUOUS', 'description': 'Node Numbers'},
    'FP-DIM': {'color': 2, 'linetype': 'CONTINUOUS', 'description': 'Dimensions'},
    
    # Title block
    'TB-BORDER': {'color': 7, 'linetype': 'CONTINUOUS', 'description': 'Title Block Border'},
    'TB-TEXT': {'color': 7, 'linetype': 'CONTINUOUS', 'description': 'Title Block Text'},
    
    # Schedules
    'SCHED': {'color': 7, 'linetype': 'CONTINUOUS', 'description': 'Schedules'},
}


# =============================================================================
# DXF GENERATOR CLASS
# =============================================================================

class ProfessionalDXFGenerator:
    """
    Generate professional fire sprinkler DXF drawings.
    """
    
    def __init__(self):
        if not EZDXF_AVAILABLE:
            raise RuntimeError("ezdxf required - pip install ezdxf")
        
        self.doc = None
        self.msp = None
        self.scale_factor = 9.0  # pts per foot
    
    def create_drawing(self,
                       floor_plan_data: Dict,
                       sprinkler_layout: Dict,
                       output_path: str,
                       project_info: Dict = None) -> bool:
        """
        Create complete fire sprinkler DXF drawing.
        
        Args:
            floor_plan_data: Floor plan analysis (from floor_plan_intelligence)
            sprinkler_layout: Sprinkler layout (from sprinkler_placement)
            output_path: Path for DXF file
            project_info: Project metadata (name, address, etc.)
        
        Returns:
            True if successful
        """
        if project_info is None:
            project_info = {
                'name': 'Fire Sprinkler System',
                'address': '',
                'drawn_by': 'FireAI Pro',
                'date': datetime.now().strftime('%Y-%m-%d'),
                'scale': '1/8" = 1\'-0"',
                'sheet': 'FP-1'
            }
        
        try:
            # Create new DXF document
            self.doc = ezdxf.new('R2010')
            self.msp = self.doc.modelspace()
            
            # Get scale factor
            self.scale_factor = floor_plan_data.get('scale', {}).get('factor', 9.0)
            
            # Setup layers
            self._setup_layers()
            
            # Draw floor plan walls
            self._draw_walls(floor_plan_data)
            
            # Draw rooms (optional - for reference)
            self._draw_room_boundaries(floor_plan_data)
            
            # Draw sprinkler heads
            self._draw_sprinklers(sprinkler_layout)
            
            # Draw branch lines
            self._draw_branch_lines(sprinkler_layout)
            
            # Add title block
            self._add_title_block(floor_plan_data, project_info)
            
            # Add sprinkler schedule
            self._add_sprinkler_schedule(sprinkler_layout)
            
            # Add symbol legend
            self._add_legend()
            
            # Save DXF
            self.doc.saveas(output_path)
            logger.info(f"✅ DXF saved to: {output_path}")
            
            return True
            
        except Exception as e:
            logger.error(f"DXF generation failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _setup_layers(self):
        """Create standard layers"""
        for name, props in LAYERS.items():
            try:
                self.doc.layers.add(
                    name,
                    color=props['color'],
                    linetype=props['linetype']
                )
            except Exception:
                pass  # Layer may already exist
    
    def _draw_walls(self, floor_plan_data: Dict):
        """Draw building walls from floor plan data"""
        # We need the raw wall data - check if available
        # For now, draw the room boundaries as walls
        
        rooms = floor_plan_data.get('rooms', [])
        
        for room in rooms:
            bounds = room.get('bounds', {})
            min_x = bounds.get('min_x', 0)
            max_x = bounds.get('max_x', 0)
            min_y = bounds.get('min_y', 0)
            max_y = bounds.get('max_y', 0)
            
            # Convert from pts to feet for drawing
            # Note: DXF is in drawing units (feet)
            min_x_ft = min_x / self.scale_factor
            max_x_ft = max_x / self.scale_factor
            min_y_ft = min_y / self.scale_factor
            max_y_ft = max_y / self.scale_factor
            
            # Draw room boundary
            points = [
                (min_x_ft, min_y_ft),
                (max_x_ft, min_y_ft),
                (max_x_ft, max_y_ft),
                (min_x_ft, max_y_ft),
                (min_x_ft, min_y_ft)
            ]
            
            self.msp.add_lwpolyline(
                points,
                dxfattribs={'layer': 'A-WALL'}
            )
            
            # Add room label if available
            label = room.get('label', '')
            if label and len(label) < 20:
                cx = (min_x_ft + max_x_ft) / 2
                cy = (min_y_ft + max_y_ft) / 2
                
                self.msp.add_text(
                    label,
                    dxfattribs={
                        'layer': 'FP-ANNO',
                        'height': 0.5,
                        'insert': (cx, cy)
                    }
                )
    
    def _draw_room_boundaries(self, floor_plan_data: Dict):
        """Draw dashed room boundaries for reference"""
        pass  # Already covered by _draw_walls
    
    def _draw_sprinklers(self, sprinkler_layout: Dict):
        """Draw sprinkler head symbols"""
        sprinklers = sprinkler_layout.get('sprinklers', [])
        
        for head in sprinklers:
            x = head['x'] / self.scale_factor
            y = head['y'] / self.scale_factor
            head_type = head.get('head_type', 'pendent')
            node = head.get('node', 0)
            
            # Draw symbol based on type
            if head_type == 'pendent':
                self._draw_pendent_symbol(x, y)
            elif head_type == 'upright':
                self._draw_upright_symbol(x, y)
            elif head_type == 'ESFR':
                self._draw_esfr_symbol(x, y)
            else:
                self._draw_pendent_symbol(x, y)
            
            # Add node number
            if node > 0:
                self.msp.add_text(
                    str(node),
                    dxfattribs={
                        'layer': 'FP-NODE',
                        'height': 0.25,
                        'insert': (x + 0.3, y + 0.3)
                    }
                )
        
        logger.info(f"   Drew {len(sprinklers)} sprinkler heads")
    
    def _draw_pendent_symbol(self, x: float, y: float):
        """Draw pendent sprinkler symbol (circle with cross)"""
        radius = 0.25
        
        # Circle
        self.msp.add_circle(
            (x, y), radius,
            dxfattribs={'layer': 'FP-SPKR'}
        )
        
        # Cross lines
        self.msp.add_line(
            (x - radius, y), (x + radius, y),
            dxfattribs={'layer': 'FP-SPKR'}
        )
        self.msp.add_line(
            (x, y - radius), (x, y + radius),
            dxfattribs={'layer': 'FP-SPKR'}
        )
    
    def _draw_upright_symbol(self, x: float, y: float):
        """Draw upright sprinkler symbol (circle with square)"""
        radius = 0.25
        
        # Circle
        self.msp.add_circle(
            (x, y), radius,
            dxfattribs={'layer': 'FP-SPKR-UPRT'}
        )
        
        # Small square inside
        s = radius * 0.5
        points = [
            (x - s, y - s),
            (x + s, y - s),
            (x + s, y + s),
            (x - s, y + s),
            (x - s, y - s)
        ]
        self.msp.add_lwpolyline(
            points,
            dxfattribs={'layer': 'FP-SPKR-UPRT'}
        )
    
    def _draw_esfr_symbol(self, x: float, y: float):
        """Draw ESFR sprinkler symbol (filled circle)"""
        radius = 0.35
        
        # Outer circle
        self.msp.add_circle(
            (x, y), radius,
            dxfattribs={'layer': 'FP-SPKR-ESFR'}
        )
        
        # Inner circle (to suggest filled)
        self.msp.add_circle(
            (x, y), radius * 0.7,
            dxfattribs={'layer': 'FP-SPKR-ESFR'}
        )
        
        # Cross
        self.msp.add_line(
            (x - radius, y), (x + radius, y),
            dxfattribs={'layer': 'FP-SPKR-ESFR'}
        )
        self.msp.add_line(
            (x, y - radius), (x, y + radius),
            dxfattribs={'layer': 'FP-SPKR-ESFR'}
        )
    
    def _draw_branch_lines(self, sprinkler_layout: Dict):
        """Draw branch lines connecting sprinklers"""
        branches = sprinkler_layout.get('branch_lines', [])
        sprinklers = {h['id']: h for h in sprinkler_layout.get('sprinklers', [])}
        
        for branch in branches:
            # Get sprinklers on this branch
            head_ids = branch.get('sprinklers', [])
            
            if len(head_ids) < 2:
                continue
            
            # Draw line through all sprinklers
            points = []
            for hid in head_ids:
                head = sprinklers.get(hid)
                if head:
                    x = head['x'] / self.scale_factor
                    y = head['y'] / self.scale_factor
                    points.append((x, y))
            
            if len(points) >= 2:
                self.msp.add_lwpolyline(
                    points,
                    dxfattribs={'layer': 'FP-PIPE-BRCH'}
                )
                
                # Add pipe size label at midpoint
                pipe_size = branch.get('pipe_size', 1.0)
                mid_idx = len(points) // 2
                if mid_idx < len(points):
                    mx, my = points[mid_idx]
                    self.msp.add_text(
                        f'{pipe_size}"',
                        dxfattribs={
                            'layer': 'FP-ANNO',
                            'height': 0.3,
                            'insert': (mx, my + 0.5)
                        }
                    )
        
        logger.info(f"   Drew {len(branches)} branch lines")
    
    def _add_title_block(self, floor_plan_data: Dict, project_info: Dict):
        """Add title block in lower right corner"""
        # Get floor plan bounds
        fp = floor_plan_data.get('floor_plan', {})
        max_x = fp.get('width_ft', 100) + 10
        min_y = -30  # Below floor plan
        
        # Title block box
        tb_width = 40
        tb_height = 20
        tb_x = max_x - tb_width
        tb_y = min_y
        
        # Border
        self.msp.add_lwpolyline(
            [
                (tb_x, tb_y),
                (tb_x + tb_width, tb_y),
                (tb_x + tb_width, tb_y + tb_height),
                (tb_x, tb_y + tb_height),
                (tb_x, tb_y)
            ],
            dxfattribs={'layer': 'TB-BORDER'}
        )
        
        # Divider lines
        self.msp.add_line(
            (tb_x, tb_y + 15), (tb_x + tb_width, tb_y + 15),
            dxfattribs={'layer': 'TB-BORDER'}
        )
        self.msp.add_line(
            (tb_x, tb_y + 10), (tb_x + tb_width, tb_y + 10),
            dxfattribs={'layer': 'TB-BORDER'}
        )
        self.msp.add_line(
            (tb_x, tb_y + 5), (tb_x + tb_width, tb_y + 5),
            dxfattribs={'layer': 'TB-BORDER'}
        )
        
        # Text
        self.msp.add_text(
            project_info.get('name', 'FIRE SPRINKLER SYSTEM'),
            dxfattribs={
                'layer': 'TB-TEXT',
                'height': 1.0,
                'insert': (tb_x + 2, tb_y + 16)
            }
        )
        
        self.msp.add_text(
            project_info.get('address', ''),
            dxfattribs={
                'layer': 'TB-TEXT',
                'height': 0.6,
                'insert': (tb_x + 2, tb_y + 11)
            }
        )
        
        self.msp.add_text(
            f"SCALE: {project_info.get('scale', '1/8\" = 1\'-0\"')}",
            dxfattribs={
                'layer': 'TB-TEXT',
                'height': 0.5,
                'insert': (tb_x + 2, tb_y + 6)
            }
        )
        
        self.msp.add_text(
            f"DATE: {project_info.get('date', '')}   DRAWN BY: {project_info.get('drawn_by', '')}",
            dxfattribs={
                'layer': 'TB-TEXT',
                'height': 0.4,
                'insert': (tb_x + 2, tb_y + 2)
            }
        )
        
        self.msp.add_text(
            project_info.get('sheet', 'FP-1'),
            dxfattribs={
                'layer': 'TB-TEXT',
                'height': 1.5,
                'insert': (tb_x + tb_width - 8, tb_y + 2)
            }
        )
    
    def _add_sprinkler_schedule(self, sprinkler_layout: Dict):
        """Add sprinkler schedule table"""
        stats = sprinkler_layout.get('statistics', {})
        
        # Position schedule above title block
        x = 10
        y = -25
        
        # Header
        self.msp.add_text(
            "SPRINKLER SCHEDULE",
            dxfattribs={
                'layer': 'SCHED',
                'height': 0.6,
                'insert': (x, y + 10)
            }
        )
        
        # Box
        self.msp.add_lwpolyline(
            [(x, y), (x + 30, y), (x + 30, y + 9), (x, y + 9), (x, y)],
            dxfattribs={'layer': 'SCHED'}
        )
        
        # Lines
        for i in range(3):
            self.msp.add_line(
                (x, y + 3 * i), (x + 30, y + 3 * i),
                dxfattribs={'layer': 'SCHED'}
            )
        
        # Data
        total_heads = stats.get('total_heads', 0)
        coverage = stats.get('total_coverage_sqft', 0)
        by_hazard = stats.get('by_hazard', {})
        
        self.msp.add_text(
            f"TOTAL HEADS: {total_heads}",
            dxfattribs={
                'layer': 'SCHED',
                'height': 0.4,
                'insert': (x + 1, y + 6.5)
            }
        )
        
        self.msp.add_text(
            f"TOTAL COVERAGE: {coverage:,.0f} SQFT",
            dxfattribs={
                'layer': 'SCHED',
                'height': 0.4,
                'insert': (x + 1, y + 3.5)
            }
        )
        
        hazard_text = ", ".join([f"{h}: {c}" for h, c in by_hazard.items()])
        self.msp.add_text(
            f"BY HAZARD: {hazard_text}",
            dxfattribs={
                'layer': 'SCHED',
                'height': 0.35,
                'insert': (x + 1, y + 0.5)
            }
        )
    
    def _add_legend(self):
        """Add symbol legend"""
        x = 10
        y = -10
        
        self.msp.add_text(
            "LEGEND",
            dxfattribs={
                'layer': 'FP-ANNO',
                'height': 0.6,
                'insert': (x, y + 6)
            }
        )
        
        # Pendent symbol
        self._draw_pendent_symbol(x + 1, y + 4)
        self.msp.add_text(
            "PENDENT SPRINKLER",
            dxfattribs={
                'layer': 'FP-ANNO',
                'height': 0.4,
                'insert': (x + 3, y + 3.8)
            }
        )
        
        # Upright symbol
        self._draw_upright_symbol(x + 1, y + 2)
        self.msp.add_text(
            "UPRIGHT SPRINKLER",
            dxfattribs={
                'layer': 'FP-ANNO',
                'height': 0.4,
                'insert': (x + 3, y + 1.8)
            }
        )
        
        # ESFR symbol
        self._draw_esfr_symbol(x + 1, y)
        self.msp.add_text(
            "ESFR SPRINKLER",
            dxfattribs={
                'layer': 'FP-ANNO',
                'height': 0.4,
                'insert': (x + 3, y - 0.2)
            }
        )


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_sprinkler_dxf(floor_plan_json: str,
                          sprinkler_json: str,
                          output_dxf: str,
                          project_info: Dict = None) -> bool:
    """
    Create DXF from JSON files.
    
    Args:
        floor_plan_json: Path to floor_plan_analysis.json
        sprinkler_json: Path to sprinkler_layout.json
        output_dxf: Output DXF path
        project_info: Optional project metadata
    
    Returns:
        True if successful
    """
    with open(floor_plan_json, 'r') as f:
        floor_plan_data = json.load(f)
    
    with open(sprinkler_json, 'r') as f:
        sprinkler_layout = json.load(f)
    
    generator = ProfessionalDXFGenerator()
    return generator.create_drawing(
        floor_plan_data,
        sprinkler_layout,
        output_dxf,
        project_info
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("📐 FireAI Pro - Professional DXF Generator v2.0")
    print("=" * 60)
    print("\nPhase 5: Professional DXF Output")
    print("\nCapabilities:")
    print("  ✅ Floor plan walls as background")
    print("  ✅ Sprinkler head symbols at actual positions")
    print("  ✅ Branch line connections")
    print("  ✅ Title block with project info")
    print("  ✅ Sprinkler schedule")
    print("  ✅ Symbol legend")
    print("\nUsage:")
    print("  from dxf_generator_v2 import create_sprinkler_dxf")
    print("  create_sprinkler_dxf('floor_plan.json', 'layout.json', 'output.dxf')")
