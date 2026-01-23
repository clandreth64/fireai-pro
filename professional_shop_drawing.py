#!/usr/bin/env python3
"""
FireAI Pro - Professional Shop Drawing Engine v2.0
==================================================
AutoSprink-quality shop drawings with:
- Title block with project info
- Legend with symbols
- Sprinkler schedule
- Pipe schedule by size
- Dimensions and labels
- North arrow and scale
- Layer organization per industry standards

VERSION: 2.0.0-PROFESSIONAL
"""

import math
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass

try:
    import ezdxf
    from ezdxf import colors
    from ezdxf.math import Vec2
    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False
    print("⚠️ ezdxf not available - shop drawings disabled")


# =============================================================================
# LAYER STANDARDS
# =============================================================================

# Standard fire protection CAD layers
LAYERS = {
    'FP-SPKR': {'color': 1, 'desc': 'Sprinkler heads'},           # Red
    'FP-SPKR-LABL': {'color': 7, 'desc': 'Sprinkler labels'},     # White
    'FP-PIPE-BRCH': {'color': 5, 'desc': 'Branch piping'},        # Blue
    'FP-PIPE-MAIN': {'color': 3, 'desc': 'Main piping'},          # Green
    'FP-PIPE-RISER': {'color': 6, 'desc': 'Riser piping'},        # Magenta
    'FP-PIPE-LABL': {'color': 7, 'desc': 'Pipe labels'},          # White
    'FP-VALV': {'color': 6, 'desc': 'Valves'},                    # Magenta
    'FP-VALV-LABL': {'color': 7, 'desc': 'Valve labels'},         # White
    'FP-HANG': {'color': 8, 'desc': 'Hangers'},                   # Gray
    'FP-BRAC': {'color': 4, 'desc': 'Seismic bracing'},           # Cyan
    'FP-DIMS': {'color': 2, 'desc': 'Dimensions'},                # Yellow
    'FP-NOTE': {'color': 7, 'desc': 'Notes and annotations'},     # White
    'FP-SYMB': {'color': 7, 'desc': 'Symbols'},                   # White
    'FP-TITL': {'color': 7, 'desc': 'Title block'},               # White
    'FP-GRID': {'color': 8, 'desc': 'Grid lines'},                # Gray
    'FP-BORD': {'color': 7, 'desc': 'Border'},                    # White
}

# Sprinkler symbols by type
SPRINKLER_SYMBOLS = {
    'pendent': 'circle_filled',
    'upright': 'circle_open',
    'sidewall': 'square_filled',
    'concealed': 'circle_crossed',
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SheetConfig:
    """Drawing sheet configuration"""
    size: str = 'D'  # A, B, C, D, E
    scale: float = 0.125  # 1/8" = 1'-0"
    title_block: bool = True
    legend: bool = True
    schedules: bool = True
    dimensions: bool = True
    north_arrow: bool = True


# Standard sheet sizes (inches)
SHEET_SIZES = {
    'A': (11, 8.5),
    'B': (17, 11),
    'C': (22, 17),
    'D': (34, 22),
    'E': (44, 34),
}


# =============================================================================
# PROFESSIONAL SHOP DRAWING GENERATOR
# =============================================================================

class ProfessionalShopDrawingEngine:
    """Generate AutoSprink-quality shop drawings"""
    
    def __init__(self, config: SheetConfig = None):
        self.config = config or SheetConfig()
        self.doc = None
        self.msp = None
    
    def generate_shop_drawing(self, 
                               design_result: Any,
                               output_path: str,
                               project_info: Dict = None) -> bool:
        """
        Generate complete shop drawing.
        
        Args:
            design_result: DesignResult with sprinklers, pipes, fittings, valves
            output_path: Path to save DXF file
            project_info: Optional project metadata
        
        Returns:
            True if successful
        """
        if not EZDXF_AVAILABLE:
            print("ezdxf not available")
            return False
        
        try:
            # Create new DXF document
            self.doc = ezdxf.new('R2010')
            self.msp = self.doc.modelspace()
            
            # Setup layers
            self._create_layers()
            
            # Draw building elements
            bounds = self._draw_sprinkler_system(design_result)
            
            # Add annotations and dimensions
            if self.config.dimensions:
                self._add_dimensions(design_result, bounds)
            
            # Add title block
            if self.config.title_block:
                self._draw_title_block(design_result, project_info, bounds)
            
            # Add legend
            if self.config.legend:
                self._draw_legend(bounds)
            
            # Add schedules
            if self.config.schedules:
                self._draw_schedules(design_result, bounds)
            
            # Add north arrow
            if self.config.north_arrow:
                self._draw_north_arrow(bounds)
            
            # Save DXF file
            self.doc.saveas(output_path)
            return True
            
        except Exception as e:
            print(f"Shop drawing error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _create_layers(self):
        """Create standard layers"""
        for layer_name, props in LAYERS.items():
            self.doc.layers.add(layer_name, color=props['color'])
    
    def _draw_sprinkler_system(self, design_result: Any) -> Dict:
        """Draw all sprinkler system elements, return bounds"""
        
        bounds = {
            'min_x': float('inf'), 'max_x': float('-inf'),
            'min_y': float('inf'), 'max_y': float('-inf')
        }
        
        # Draw pipes first (under sprinklers)
        for pipe in design_result.pipes:
            self._draw_pipe(pipe)
            
            # Update bounds
            bounds['min_x'] = min(bounds['min_x'], pipe.start[0], pipe.end[0])
            bounds['max_x'] = max(bounds['max_x'], pipe.start[0], pipe.end[0])
            bounds['min_y'] = min(bounds['min_y'], pipe.start[1], pipe.end[1])
            bounds['max_y'] = max(bounds['max_y'], pipe.start[1], pipe.end[1])
        
        # Draw sprinklers
        for spk in design_result.sprinklers:
            self._draw_sprinkler(spk)
            
            # Update bounds
            bounds['min_x'] = min(bounds['min_x'], spk.x)
            bounds['max_x'] = max(bounds['max_x'], spk.x)
            bounds['min_y'] = min(bounds['min_y'], spk.y)
            bounds['max_y'] = max(bounds['max_y'], spk.y)
        
        # Draw valves
        for valve in design_result.valves:
            self._draw_valve(valve)
        
        # Draw fittings (at pipe intersections)
        for fitting in design_result.fittings:
            self._draw_fitting(fitting)
        
        # Add margin
        margin = 20
        bounds['min_x'] -= margin
        bounds['max_x'] += margin
        bounds['min_y'] -= margin
        bounds['max_y'] += margin
        
        return bounds
    
    def _draw_sprinkler(self, spk):
        """Draw sprinkler symbol with label"""
        x, y = spk.x, spk.y
        
        # Symbol size based on K-factor
        k = getattr(spk, 'k_factor', 5.6)
        if k >= 14:
            radius = 0.6  # ESFR - larger symbol
        elif k >= 8:
            radius = 0.5  # Large orifice
        else:
            radius = 0.4  # Standard
        
        # Draw symbol (filled circle for pendent)
        circle = self.msp.add_circle(
            (x, y), radius,
            dxfattribs={'layer': 'FP-SPKR'}
        )
        
        # Add crosshairs for ESFR
        if k >= 14:
            self.msp.add_line(
                (x - radius * 1.2, y), (x + radius * 1.2, y),
                dxfattribs={'layer': 'FP-SPKR'}
            )
            self.msp.add_line(
                (x, y - radius * 1.2), (x, y + radius * 1.2),
                dxfattribs={'layer': 'FP-SPKR'}
            )
        
        # Add label
        label = getattr(spk, 'id', 'S')
        self.msp.add_text(
            label,
            dxfattribs={
                'layer': 'FP-SPKR-LABL',
                'height': 0.25,
                'insert': (x + radius + 0.3, y - 0.1)
            }
        )
    
    def _draw_pipe(self, pipe):
        """Draw pipe with size label"""
        start = pipe.start[:2]
        end = pipe.end[:2]
        
        # Determine layer based on pipe type
        pipe_type = getattr(pipe, 'pipe_type', 'branch')
        if pipe_type in ['main', 'cross_main', 'feed_main']:
            layer = 'FP-PIPE-MAIN'
        elif pipe_type == 'riser':
            layer = 'FP-PIPE-RISER'
        else:
            layer = 'FP-PIPE-BRCH'
        
        # Draw pipe line
        self.msp.add_line(start, end, dxfattribs={'layer': layer})
        
        # Add size label at midpoint
        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2
        
        diameter = getattr(pipe, 'diameter', 1.0)
        length = getattr(pipe, 'length', 0)
        
        # Only label pipes > 5' long
        if length > 5:
            label = f'{diameter}"'
            
            # Rotate label for vertical pipes
            angle = 0
            if abs(end[0] - start[0]) < 0.1:  # Vertical
                angle = 90
            
            self.msp.add_text(
                label,
                dxfattribs={
                    'layer': 'FP-PIPE-LABL',
                    'height': 0.2,
                    'insert': (mid_x + 0.2, mid_y + 0.2),
                    'rotation': angle
                }
            )
    
    def _draw_valve(self, valve):
        """Draw valve symbol"""
        x, y = valve.x, valve.y
        size = getattr(valve, 'size', 4.0) / 4  # Scale symbol
        valve_type = getattr(valve, 'valve_type', 'gate')
        
        # Different symbols for different valve types
        if 'gate' in valve_type.lower() or 'os&y' in valve_type.lower():
            # Gate valve: rectangle with stem
            self.msp.add_lwpolyline(
                [(x - size/2, y - size/2), (x + size/2, y - size/2),
                 (x + size/2, y + size/2), (x - size/2, y + size/2)],
                close=True,
                dxfattribs={'layer': 'FP-VALV'}
            )
            self.msp.add_line(
                (x, y + size/2), (x, y + size),
                dxfattribs={'layer': 'FP-VALV'}
            )
        elif 'check' in valve_type.lower():
            # Check valve: triangle
            self.msp.add_lwpolyline(
                [(x - size/2, y - size/2), (x + size/2, y),
                 (x - size/2, y + size/2)],
                close=True,
                dxfattribs={'layer': 'FP-VALV'}
            )
        elif 'butterfly' in valve_type.lower():
            # Butterfly: circle with line
            self.msp.add_circle((x, y), size/2, dxfattribs={'layer': 'FP-VALV'})
            self.msp.add_line(
                (x - size/2, y - size/2), (x + size/2, y + size/2),
                dxfattribs={'layer': 'FP-VALV'}
            )
        else:
            # Generic: diamond
            self.msp.add_lwpolyline(
                [(x, y - size/2), (x + size/2, y),
                 (x, y + size/2), (x - size/2, y)],
                close=True,
                dxfattribs={'layer': 'FP-VALV'}
            )
        
        # Add label
        self.msp.add_text(
            valve_type[:3].upper(),
            dxfattribs={
                'layer': 'FP-VALV-LABL',
                'height': 0.2,
                'insert': (x + size, y)
            }
        )
    
    def _draw_fitting(self, fitting):
        """Draw fitting symbol"""
        x, y = fitting.x, fitting.y
        fitting_type = getattr(fitting, 'fitting_type', 'tee')
        size = getattr(fitting, 'size', 1.0) * 0.3
        
        # Tee: small filled circle
        if 'tee' in fitting_type.lower():
            self.msp.add_circle(
                (x, y), size/2,
                dxfattribs={'layer': 'FP-PIPE-BRCH'}
            )
    
    def _add_dimensions(self, design_result: Any, bounds: Dict):
        """Add dimensions to drawing"""
        
        # Find branch line spacings
        sprinklers = design_result.sprinklers
        if len(sprinklers) < 2:
            return
        
        # Group sprinklers by Y coordinate (branch lines)
        branches = {}
        for spk in sprinklers:
            y_key = round(spk.y, 0)
            if y_key not in branches:
                branches[y_key] = []
            branches[y_key].append(spk)
        
        # Add dimension for sprinkler spacing on first branch
        branch_ys = sorted(branches.keys())
        if branch_ys:
            first_branch = sorted(branches[branch_ys[0]], key=lambda s: s.x)
            if len(first_branch) >= 2:
                s1, s2 = first_branch[0], first_branch[1]
                self._draw_dimension(
                    (s1.x, s1.y - 3), (s2.x, s2.y - 3),
                    f"{abs(s2.x - s1.x):.1f}'"
                )
        
        # Add dimension for branch spacing
        if len(branch_ys) >= 2:
            y1, y2 = branch_ys[0], branch_ys[1]
            x = bounds['min_x'] + 5
            self._draw_dimension(
                (x, y1), (x, y2),
                f"{abs(y2 - y1):.1f}'",
                vertical=True
            )
    
    def _draw_dimension(self, start: Tuple, end: Tuple, text: str, vertical: bool = False):
        """Draw dimension line with text"""
        # Extension lines
        ext_len = 0.5
        
        if vertical:
            # Vertical dimension
            self.msp.add_line(
                (start[0] - ext_len, start[1]), (start[0] + ext_len, start[1]),
                dxfattribs={'layer': 'FP-DIMS'}
            )
            self.msp.add_line(
                (end[0] - ext_len, end[1]), (end[0] + ext_len, end[1]),
                dxfattribs={'layer': 'FP-DIMS'}
            )
            # Dimension line
            self.msp.add_line(start, end, dxfattribs={'layer': 'FP-DIMS'})
            # Text
            mid_y = (start[1] + end[1]) / 2
            self.msp.add_text(
                text,
                dxfattribs={
                    'layer': 'FP-DIMS',
                    'height': 0.2,
                    'insert': (start[0] - 1.5, mid_y),
                    'rotation': 90
                }
            )
        else:
            # Horizontal dimension
            self.msp.add_line(
                (start[0], start[1] - ext_len), (start[0], start[1] + ext_len),
                dxfattribs={'layer': 'FP-DIMS'}
            )
            self.msp.add_line(
                (end[0], end[1] - ext_len), (end[0], end[1] + ext_len),
                dxfattribs={'layer': 'FP-DIMS'}
            )
            # Dimension line
            self.msp.add_line(start, end, dxfattribs={'layer': 'FP-DIMS'})
            # Text
            mid_x = (start[0] + end[0]) / 2
            self.msp.add_text(
                text,
                dxfattribs={
                    'layer': 'FP-DIMS',
                    'height': 0.2,
                    'insert': (mid_x - 0.5, start[1] - 1)
                }
            )
    
    def _draw_title_block(self, design_result: Any, project_info: Dict, bounds: Dict):
        """Draw title block in lower right"""
        project_info = project_info or {}
        
        # Position title block
        tb_width = 40
        tb_height = 20
        tb_x = bounds['max_x'] + 10
        tb_y = bounds['min_y']
        
        # Border
        self.msp.add_lwpolyline(
            [(tb_x, tb_y), (tb_x + tb_width, tb_y),
             (tb_x + tb_width, tb_y + tb_height), (tb_x, tb_y + tb_height)],
            close=True,
            dxfattribs={'layer': 'FP-TITL'}
        )
        
        # Horizontal dividers
        for i in range(1, 6):
            y = tb_y + i * (tb_height / 6)
            self.msp.add_line(
                (tb_x, y), (tb_x + tb_width, y),
                dxfattribs={'layer': 'FP-TITL'}
            )
        
        # Title text
        row_height = tb_height / 6
        text_height = 0.4
        
        rows = [
            ("FIRE SPRINKLER SHOP DRAWING", 1.0),
            (f"Project: {design_result.project_name}", 0.5),
            (f"Project ID: {design_result.project_id}", 0.4),
            (f"Area: {design_result.building_area:,.0f} SF", 0.4),
            (f"Date: {datetime.now().strftime('%Y-%m-%d')}", 0.4),
            (f"Scale: 1/8\" = 1'-0\"", 0.4),
        ]
        
        for i, (text, size_mult) in enumerate(rows):
            self.msp.add_text(
                text,
                dxfattribs={
                    'layer': 'FP-TITL',
                    'height': text_height * size_mult,
                    'insert': (tb_x + 1, tb_y + tb_height - (i + 0.7) * row_height)
                }
            )
    
    def _draw_legend(self, bounds: Dict):
        """Draw symbol legend"""
        leg_x = bounds['max_x'] + 10
        leg_y = bounds['min_y'] + 25
        
        # Title
        self.msp.add_text(
            "LEGEND",
            dxfattribs={
                'layer': 'FP-SYMB',
                'height': 0.5,
                'insert': (leg_x, leg_y)
            }
        )
        
        # Legend items
        items = [
            ('●', 'Pendent Sprinkler', 'FP-SPKR'),
            ('○', 'Upright Sprinkler', 'FP-SPKR'),
            ('⊕', 'ESFR Sprinkler', 'FP-SPKR'),
            ('—', 'Branch Piping', 'FP-PIPE-BRCH'),
            ('═', 'Main Piping', 'FP-PIPE-MAIN'),
            ('◇', 'Valve', 'FP-VALV'),
            ('×', 'Hanger', 'FP-HANG'),
        ]
        
        for i, (symbol, desc, layer) in enumerate(items):
            y = leg_y - (i + 1) * 1.5
            
            # Symbol
            self.msp.add_text(
                symbol,
                dxfattribs={
                    'layer': layer,
                    'height': 0.4,
                    'insert': (leg_x, y)
                }
            )
            
            # Description
            self.msp.add_text(
                desc,
                dxfattribs={
                    'layer': 'FP-SYMB',
                    'height': 0.3,
                    'insert': (leg_x + 2, y)
                }
            )
    
    def _draw_schedules(self, design_result: Any, bounds: Dict):
        """Draw sprinkler and pipe schedules"""
        sch_x = bounds['max_x'] + 55
        sch_y = bounds['min_y'] + 45
        
        # SPRINKLER SCHEDULE
        self.msp.add_text(
            "SPRINKLER SCHEDULE",
            dxfattribs={
                'layer': 'FP-NOTE',
                'height': 0.5,
                'insert': (sch_x, sch_y)
            }
        )
        
        # Get sprinkler info
        sprinklers = design_result.sprinklers
        k_factor = sprinklers[0].k_factor if sprinklers else 5.6
        
        spk_rows = [
            ("Type:", "Pendent"),
            ("K-Factor:", f"{k_factor}"),
            ("Temp Rating:", "165°F"),
            ("Finish:", "Brass"),
            ("Quantity:", f"{len(sprinklers)}"),
        ]
        
        for i, (label, value) in enumerate(spk_rows):
            y = sch_y - (i + 1) * 1.2
            self.msp.add_text(
                f"{label} {value}",
                dxfattribs={
                    'layer': 'FP-NOTE',
                    'height': 0.3,
                    'insert': (sch_x, y)
                }
            )
        
        # PIPE SCHEDULE
        pipe_y = sch_y - 10
        self.msp.add_text(
            "PIPE SCHEDULE",
            dxfattribs={
                'layer': 'FP-NOTE',
                'height': 0.5,
                'insert': (sch_x, pipe_y)
            }
        )
        
        # Summarize pipe by size
        pipe_by_size = {}
        for pipe in design_result.pipes:
            size = getattr(pipe, 'diameter', 1.0)
            length = getattr(pipe, 'length', 0)
            pipe_by_size[size] = pipe_by_size.get(size, 0) + length
        
        self.msp.add_text(
            "Size    Length (LF)",
            dxfattribs={
                'layer': 'FP-NOTE',
                'height': 0.3,
                'insert': (sch_x, pipe_y - 1.2)
            }
        )
        
        for i, (size, length) in enumerate(sorted(pipe_by_size.items())):
            y = pipe_y - (i + 2) * 1.2
            self.msp.add_text(
                f'{size}"      {length:.0f}',
                dxfattribs={
                    'layer': 'FP-NOTE',
                    'height': 0.3,
                    'insert': (sch_x, y)
                }
            )
        
        # Total
        total_pipe = sum(pipe_by_size.values())
        self.msp.add_text(
            f"TOTAL:  {total_pipe:.0f} LF",
            dxfattribs={
                'layer': 'FP-NOTE',
                'height': 0.35,
                'insert': (sch_x, pipe_y - (len(pipe_by_size) + 3) * 1.2)
            }
        )
    
    def _draw_north_arrow(self, bounds: Dict):
        """Draw north arrow"""
        na_x = bounds['min_x'] + 5
        na_y = bounds['max_y'] - 5
        size = 3
        
        # Arrow
        self.msp.add_lwpolyline(
            [(na_x, na_y), (na_x - size/3, na_y - size),
             (na_x, na_y - size * 0.7), (na_x + size/3, na_y - size)],
            close=True,
            dxfattribs={'layer': 'FP-SYMB'}
        )
        
        # N label
        self.msp.add_text(
            "N",
            dxfattribs={
                'layer': 'FP-SYMB',
                'height': 0.6,
                'insert': (na_x - 0.3, na_y + 0.5)
            }
        )


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def generate_professional_shop_drawing(design_result: Any,
                                        output_path: str,
                                        project_info: Dict = None,
                                        config: SheetConfig = None) -> bool:
    """
    Generate a professional shop drawing.
    
    Args:
        design_result: DesignResult with sprinklers, pipes, etc.
        output_path: Path to save DXF file
        project_info: Optional project metadata
        config: Optional SheetConfig for customization
    
    Returns:
        True if successful
    """
    engine = ProfessionalShopDrawingEngine(config)
    return engine.generate_shop_drawing(design_result, output_path, project_info)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🔧 FireAI Pro - Professional Shop Drawing Engine v2.0")
    print("=" * 60)
    print("\nFeatures:")
    print("  ✅ Title block with project info")
    print("  ✅ Symbol legend")
    print("  ✅ Sprinkler schedule")
    print("  ✅ Pipe schedule by size")
    print("  ✅ Dimensions and labels")
    print("  ✅ North arrow")
    print("  ✅ Layer organization per industry standards")
    print("\nUsage:")
    print("  generate_professional_shop_drawing(design, 'drawing.dxf')")
