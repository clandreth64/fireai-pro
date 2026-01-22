#!/usr/bin/env python3
"""
FireAI Pro - Professional DXF Shop Drawing Engine
VERSION: 1.0.0

🏗️ AHJ-COMPLIANT SHOP DRAWINGS FOR PERMIT SUBMISSION

This module generates professional fire sprinkler shop drawings that meet
AHJ (Authority Having Jurisdiction) requirements for permit approval.

📐 FEATURES:
✅ NFPA-compliant symbols (blocks) for all components
✅ Automatic dimensioning with proper DIMSTYLE
✅ Sprinkler coverage area visualization
✅ Embedded schedules (sprinkler, pipe, valve, hanger, brace)
✅ Professional title block with signature lines
✅ Symbol legend with descriptions
✅ General notes and NFPA references
✅ Riser diagram (isometric view)
✅ NCS-compliant layer standards
✅ Proper lineweights and colors

📋 LAYER STANDARDS (NCS-Based):
- FP-PIPE-MAIN      Red (1)     Main/cross main piping
- FP-PIPE-BRANCH    Red (1)     Branch line piping
- FP-PIPE-RISER     Red (1)     Riser piping
- FP-SPKR          Cyan (4)    Sprinkler heads
- FP-VALVE         Green (3)   Valves
- FP-HANGER        Gray (8)    Hangers/supports
- FP-BRACE         Magenta (6) Seismic bracing
- FP-DIM           White (7)   Dimensions
- FP-TEXT          White (7)   Text/annotations
- FP-SCHED         White (7)   Schedules
- FP-TITLE         White (7)   Title block
- FP-COVERAGE      Yellow (2)  Coverage areas (dashed)

🎯 OUTPUT COMPLIANCE:
- NFPA 13 shop drawing requirements
- Local AHJ submittal standards
- Industry-standard symbols
- Professional presentation quality
"""

import math
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Check for ezdxf
EZDXF_AVAILABLE = False
try:
    import ezdxf
    from ezdxf import units
    from ezdxf.enums import TextEntityAlignment
    from ezdxf.math import Vec3
    EZDXF_AVAILABLE = True
    logger.info("✅ ezdxf available for DXF generation")
except ImportError:
    logger.warning("⚠️ ezdxf not available - DXF generation disabled")


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class ShopDrawingConfig:
    """Configuration for shop drawing generation"""
    # Drawing units (1 = inches, 12 = feet)
    units_scale: float = 12.0  # 1 unit = 1 foot
    
    # Text heights (in drawing units)
    title_text_height: float = 0.25
    schedule_text_height: float = 0.10
    dim_text_height: float = 0.08
    note_text_height: float = 0.10
    label_text_height: float = 0.06
    
    # Symbol sizes
    sprinkler_symbol_size: float = 0.5
    valve_symbol_size: float = 0.4
    hanger_symbol_size: float = 0.3
    brace_symbol_size: float = 0.35
    
    # Dimension settings
    dim_arrow_size: float = 0.1
    dim_offset: float = 1.5
    dim_extension: float = 0.3
    
    # Coverage display
    show_coverage_areas: bool = True
    coverage_line_pattern: str = "DASHED"
    
    # Schedule positions (relative to drawing origin)
    sprinkler_schedule_pos: Tuple[float, float] = (0, -20)
    pipe_schedule_pos: Tuple[float, float] = (40, -20)
    valve_schedule_pos: Tuple[float, float] = (0, -35)
    hanger_schedule_pos: Tuple[float, float] = (40, -35)
    
    # Title block
    title_block_pos: Tuple[float, float] = (0, -55)
    title_block_width: float = 80
    title_block_height: float = 12
    
    # Legend
    legend_pos: Tuple[float, float] = (85, 0)
    
    # Notes
    notes_pos: Tuple[float, float] = (85, -25)


@dataclass
class ProjectData:
    """Project information for title block"""
    project_name: str = ""
    project_number: str = ""
    address: str = ""
    city_state_zip: str = ""
    
    contractor_name: str = ""
    contractor_license: str = ""
    contractor_address: str = ""
    contractor_phone: str = ""
    
    engineer_name: str = ""
    engineer_license: str = ""
    
    drawing_number: str = "FP-001"
    drawing_title: str = "FIRE SPRINKLER PLAN"
    scale: str = '1/8" = 1\'-0"'
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    revision: str = "0"
    
    # System info
    system_type: str = "WET"
    hazard_class: str = "Ordinary Hazard Group 1"
    design_density: str = "0.15 GPM/sqft over 1,500 sqft"
    water_supply: str = ""


# =============================================================================
# PROFESSIONAL DXF SHOP DRAWING ENGINE
# =============================================================================

class ProfessionalDXFEngine:
    """
    Generates professional, AHJ-compliant DXF shop drawings
    
    Creates complete fire sprinkler shop drawings with:
    - Proper symbols and blocks
    - Automatic dimensioning
    - Schedules and legends
    - Title block
    - NFPA-compliant formatting
    """
    
    def __init__(self, config: Optional[ShopDrawingConfig] = None):
        self.config = config or ShopDrawingConfig()
        self.doc = None
        self.msp = None
        self.blocks_created = set()
        
    def generate_shop_drawing(self, 
                              design_data: Dict[str, Any],
                              project_data: ProjectData,
                              output_path: str) -> bool:
        """
        Generate complete shop drawing DXF
        
        Args:
            design_data: Dict containing sprinklers, pipes, valves, hangers, braces
            project_data: Project information for title block
            output_path: Output file path
            
        Returns:
            True if successful
        """
        if not EZDXF_AVAILABLE:
            logger.error("ezdxf not available - cannot generate DXF")
            return False
        
        logger.info(f"Generating professional shop drawing: {output_path}")
        
        try:
            # Create new DXF document
            self.doc = ezdxf.new('R2013', setup=True)
            self.msp = self.doc.modelspace()
            
            # Set up drawing
            self._setup_layers()
            self._setup_dimension_style()
            self._setup_text_styles()
            self._create_blocks()
            
            # Draw components
            self._draw_pipes(design_data.get('pipes', []))
            self._draw_sprinklers(design_data.get('sprinklers', []))
            self._draw_valves(design_data.get('valves', []))
            self._draw_hangers(design_data.get('hangers', []))
            self._draw_braces(design_data.get('braces', []))
            
            # Add dimensions
            self._add_pipe_dimensions(design_data.get('pipes', []))
            self._add_sprinkler_spacing_dimensions(design_data.get('sprinklers', []))
            
            # Add coverage areas
            if self.config.show_coverage_areas:
                self._draw_coverage_areas(design_data.get('sprinklers', []))
            
            # Add schedules
            self._draw_sprinkler_schedule(design_data.get('sprinklers', []))
            self._draw_pipe_schedule(design_data.get('pipes', []))
            self._draw_valve_schedule(design_data.get('valves', []))
            self._draw_hanger_brace_schedule(
                design_data.get('hangers', []),
                design_data.get('braces', [])
            )
            
            # Add legend
            self._draw_legend()
            
            # Add general notes
            self._draw_general_notes(design_data)
            
            # Add title block
            self._draw_title_block(project_data, design_data)
            
            # Add riser diagram
            self._draw_riser_diagram(design_data, project_data)
            
            # Save
            self.doc.saveas(output_path)
            logger.info(f"✅ Shop drawing saved: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"DXF generation error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # =========================================================================
    # SETUP METHODS
    # =========================================================================
    
    def _setup_layers(self):
        """Create NCS-compliant layers"""
        layers = [
            # (name, color, linetype, lineweight)
            ('FP-PIPE-MAIN', 1, 'CONTINUOUS', 50),      # Red, 0.50mm
            ('FP-PIPE-BRANCH', 1, 'CONTINUOUS', 35),    # Red, 0.35mm
            ('FP-PIPE-RISER', 1, 'CONTINUOUS', 70),     # Red, 0.70mm
            ('FP-SPKR', 4, 'CONTINUOUS', 35),           # Cyan
            ('FP-SPKR-COV', 2, 'DASHED', 18),           # Yellow, dashed
            ('FP-VALVE', 3, 'CONTINUOUS', 35),          # Green
            ('FP-HANGER', 8, 'CONTINUOUS', 25),         # Gray
            ('FP-BRACE', 6, 'CONTINUOUS', 35),          # Magenta
            ('FP-DIM', 7, 'CONTINUOUS', 18),            # White
            ('FP-TEXT', 7, 'CONTINUOUS', 25),           # White
            ('FP-SCHED', 7, 'CONTINUOUS', 18),          # White
            ('FP-TITLE', 7, 'CONTINUOUS', 35),          # White
            ('FP-NOTES', 7, 'CONTINUOUS', 18),          # White
            ('FP-LEGEND', 7, 'CONTINUOUS', 25),         # White
            ('FP-RISER', 1, 'CONTINUOUS', 35),          # Red
            ('FP-BORDER', 7, 'CONTINUOUS', 70),         # White, heavy
        ]
        
        for name, color, linetype, lineweight in layers:
            try:
                self.doc.layers.add(name, color=color, linetype=linetype)
                layer = self.doc.layers.get(name)
                layer.dxf.lineweight = lineweight
            except Exception:
                pass  # Layer might already exist
    
    def _setup_dimension_style(self):
        """Create dimension style for shop drawings"""
        dim_style = self.doc.dimstyles.new('FP_DIM')
        
        # Text settings
        dim_style.dxf.dimtxt = self.config.dim_text_height
        dim_style.dxf.dimtxsty = 'Standard'
        
        # Arrow settings
        dim_style.dxf.dimasz = self.config.dim_arrow_size
        dim_style.dxf.dimblk = 'CLOSED'  # Closed filled arrow
        
        # Extension lines
        dim_style.dxf.dimexe = self.config.dim_extension
        dim_style.dxf.dimexo = 0.05
        
        # Dimension line
        dim_style.dxf.dimdli = 0.5
        
        # Units - architectural
        dim_style.dxf.dimlunit = 4  # Architectural
        dim_style.dxf.dimlfac = 1.0
        
        # Precision
        dim_style.dxf.dimdec = 2
        
        # Color
        dim_style.dxf.dimclrd = 7  # White
        dim_style.dxf.dimclre = 7
        dim_style.dxf.dimclrt = 7
    
    def _setup_text_styles(self):
        """Create text styles"""
        # Standard text style
        if 'FP_STANDARD' not in self.doc.styles:
            self.doc.styles.new('FP_STANDARD', dxfattribs={
                'font': 'simplex.shx',
                'height': 0.1
            })
        
        # Title text style
        if 'FP_TITLE' not in self.doc.styles:
            self.doc.styles.new('FP_TITLE', dxfattribs={
                'font': 'romans.shx',
                'height': 0.25
            })
    
    def _create_blocks(self):
        """Create reusable symbol blocks"""
        self._create_sprinkler_blocks()
        self._create_valve_blocks()
        self._create_hanger_block()
        self._create_brace_blocks()
        self._create_fitting_blocks()
    
    def _create_sprinkler_blocks(self):
        """Create sprinkler symbol blocks"""
        s = self.config.sprinkler_symbol_size
        
        # Pendant sprinkler (circle with cross)
        if 'SPKR_PEND' not in self.doc.blocks:
            blk = self.doc.blocks.new('SPKR_PEND')
            blk.add_circle((0, 0), radius=s/2)
            blk.add_line((-s/2, 0), (s/2, 0))
            blk.add_line((0, -s/2), (0, s/2))
        
        # Upright sprinkler (circle with cross and U)
        if 'SPKR_UP' not in self.doc.blocks:
            blk = self.doc.blocks.new('SPKR_UP')
            blk.add_circle((0, 0), radius=s/2)
            blk.add_line((-s/2, 0), (s/2, 0))
            blk.add_line((0, -s/2), (0, s/2))
            blk.add_text('U', dxfattribs={'height': s/4}).set_placement(
                (s/2 + 0.05, 0), align=TextEntityAlignment.LEFT
            )
        
        # Sidewall sprinkler (circle with arrow)
        if 'SPKR_SW' not in self.doc.blocks:
            blk = self.doc.blocks.new('SPKR_SW')
            blk.add_circle((0, 0), radius=s/2)
            blk.add_line((0, 0), (s/2 + 0.1, 0))
            blk.add_line((s/2, 0.05), (s/2 + 0.1, 0))
            blk.add_line((s/2, -0.05), (s/2 + 0.1, 0))
        
        # Concealed sprinkler (circle with C)
        if 'SPKR_CONC' not in self.doc.blocks:
            blk = self.doc.blocks.new('SPKR_CONC')
            blk.add_circle((0, 0), radius=s/2)
            blk.add_text('C', dxfattribs={'height': s/3}).set_placement(
                (0, 0), align=TextEntityAlignment.MIDDLE_CENTER
            )
        
        # ESFR sprinkler (larger circle with E)
        if 'SPKR_ESFR' not in self.doc.blocks:
            blk = self.doc.blocks.new('SPKR_ESFR')
            blk.add_circle((0, 0), radius=s*0.7)
            blk.add_text('E', dxfattribs={'height': s/2}).set_placement(
                (0, 0), align=TextEntityAlignment.MIDDLE_CENTER
            )
    
    def _create_valve_blocks(self):
        """Create valve symbol blocks"""
        s = self.config.valve_symbol_size
        
        # OS&Y Gate Valve
        if 'VALVE_OSY' not in self.doc.blocks:
            blk = self.doc.blocks.new('VALVE_OSY')
            # Diamond shape
            blk.add_lwpolyline([
                (0, s/2), (s/2, 0), (0, -s/2), (-s/2, 0), (0, s/2)
            ], close=True)
            blk.add_text('OS&Y', dxfattribs={'height': s/4}).set_placement(
                (s/2 + 0.1, 0), align=TextEntityAlignment.LEFT
            )
        
        # Alarm Check Valve
        if 'VALVE_ACV' not in self.doc.blocks:
            blk = self.doc.blocks.new('VALVE_ACV')
            blk.add_circle((0, 0), radius=s/2)
            blk.add_line((-s/2, -s/2), (s/2, s/2))
            blk.add_text('ACV', dxfattribs={'height': s/4}).set_placement(
                (s/2 + 0.1, 0), align=TextEntityAlignment.LEFT
            )
        
        # Flow Switch
        if 'VALVE_FS' not in self.doc.blocks:
            blk = self.doc.blocks.new('VALVE_FS')
            blk.add_lwpolyline([
                (-s/3, s/3), (s/3, s/3), (s/3, -s/3), (-s/3, -s/3), (-s/3, s/3)
            ], close=True)
            blk.add_text('FS', dxfattribs={'height': s/4}).set_placement(
                (0, 0), align=TextEntityAlignment.MIDDLE_CENTER
            )
        
        # Test & Drain
        if 'VALVE_TD' not in self.doc.blocks:
            blk = self.doc.blocks.new('VALVE_TD')
            blk.add_lwpolyline([
                (0, s/2), (s/2, 0), (0, -s/2), (-s/2, 0), (0, s/2)
            ], close=True)
            blk.add_text('T&D', dxfattribs={'height': s/4}).set_placement(
                (s/2 + 0.1, 0), align=TextEntityAlignment.LEFT
            )
        
        # FDC (Fire Department Connection)
        if 'VALVE_FDC' not in self.doc.blocks:
            blk = self.doc.blocks.new('VALVE_FDC')
            # Siamese connection symbol
            blk.add_circle((-s/4, 0), radius=s/3)
            blk.add_circle((s/4, 0), radius=s/3)
            blk.add_text('FDC', dxfattribs={'height': s/4}).set_placement(
                (0, -s/2 - 0.1), align=TextEntityAlignment.TOP_CENTER
            )
    
    def _create_hanger_block(self):
        """Create hanger symbol block"""
        s = self.config.hanger_symbol_size
        
        if 'HANGER' not in self.doc.blocks:
            blk = self.doc.blocks.new('HANGER')
            # Inverted triangle
            blk.add_lwpolyline([
                (-s/2, s/3), (s/2, s/3), (0, -s/3), (-s/2, s/3)
            ], close=True)
    
    def _create_brace_blocks(self):
        """Create seismic brace symbol blocks"""
        s = self.config.brace_symbol_size
        
        # Lateral brace
        if 'BRACE_LAT' not in self.doc.blocks:
            blk = self.doc.blocks.new('BRACE_LAT')
            blk.add_circle((0, 0), radius=s/2)
            blk.add_text('L', dxfattribs={'height': s/2}).set_placement(
                (0, 0), align=TextEntityAlignment.MIDDLE_CENTER
            )
        
        # Longitudinal brace
        if 'BRACE_LONG' not in self.doc.blocks:
            blk = self.doc.blocks.new('BRACE_LONG')
            blk.add_circle((0, 0), radius=s/2)
            blk.add_text('LG', dxfattribs={'height': s/3}).set_placement(
                (0, 0), align=TextEntityAlignment.MIDDLE_CENTER
            )
        
        # 4-way brace
        if 'BRACE_4WAY' not in self.doc.blocks:
            blk = self.doc.blocks.new('BRACE_4WAY')
            blk.add_circle((0, 0), radius=s/2)
            blk.add_text('4W', dxfattribs={'height': s/3}).set_placement(
                (0, 0), align=TextEntityAlignment.MIDDLE_CENTER
            )
    
    def _create_fitting_blocks(self):
        """Create pipe fitting symbol blocks"""
        s = 0.2
        
        # Tee
        if 'FIT_TEE' not in self.doc.blocks:
            blk = self.doc.blocks.new('FIT_TEE')
            blk.add_line((-s, 0), (s, 0))
            blk.add_line((0, 0), (0, s))
        
        # Elbow
        if 'FIT_ELL' not in self.doc.blocks:
            blk = self.doc.blocks.new('FIT_ELL')
            blk.add_arc((0, 0), radius=s, start_angle=0, end_angle=90)
        
        # Cross
        if 'FIT_CROSS' not in self.doc.blocks:
            blk = self.doc.blocks.new('FIT_CROSS')
            blk.add_line((-s, 0), (s, 0))
            blk.add_line((0, -s), (0, s))
        
        # Reducer
        if 'FIT_RED' not in self.doc.blocks:
            blk = self.doc.blocks.new('FIT_RED')
            blk.add_lwpolyline([
                (-s, s/2), (s, s/3), (s, -s/3), (-s, -s/2), (-s, s/2)
            ])
    
    # =========================================================================
    # DRAWING METHODS
    # =========================================================================
    
    def _draw_pipes(self, pipes: List[Dict]):
        """Draw all pipes with proper layers and lineweights"""
        for pipe in pipes:
            start = pipe.get('start', (0, 0, 0))
            end = pipe.get('end', (0, 0, 0))
            pipe_type = pipe.get('type', 'branch')
            diameter = pipe.get('diameter', 1.0)
            
            # Select layer based on pipe type
            if pipe_type == 'riser':
                layer = 'FP-PIPE-RISER'
                # Draw riser symbol (circle with X)
                self.msp.add_circle(
                    (start[0], start[1]), radius=1.0,
                    dxfattribs={'layer': layer}
                )
                self.msp.add_line(
                    (start[0]-0.7, start[1]-0.7),
                    (start[0]+0.7, start[1]+0.7),
                    dxfattribs={'layer': layer}
                )
                self.msp.add_line(
                    (start[0]-0.7, start[1]+0.7),
                    (start[0]+0.7, start[1]-0.7),
                    dxfattribs={'layer': layer}
                )
            elif pipe_type in ['main', 'cross_main', 'feed_main']:
                layer = 'FP-PIPE-MAIN'
            else:
                layer = 'FP-PIPE-BRANCH'
            
            # Draw pipe line
            if pipe_type != 'riser':
                self.msp.add_line(
                    (start[0], start[1]),
                    (end[0], end[1]),
                    dxfattribs={'layer': layer}
                )
            
            # Add pipe size label at midpoint
            if pipe_type != 'riser':
                mid_x = (start[0] + end[0]) / 2
                mid_y = (start[1] + end[1]) / 2
                
                # Calculate angle for text rotation
                dx = end[0] - start[0]
                dy = end[1] - start[1]
                angle = math.degrees(math.atan2(dy, dx))
                
                # Adjust angle to keep text readable
                if angle > 90:
                    angle -= 180
                elif angle < -90:
                    angle += 180
                
                self.msp.add_text(
                    f'{diameter}"',
                    dxfattribs={
                        'layer': 'FP-TEXT',
                        'height': self.config.label_text_height,
                        'rotation': angle
                    }
                ).set_placement((mid_x, mid_y + 0.15), align=TextEntityAlignment.BOTTOM_CENTER)
    
    def _draw_sprinklers(self, sprinklers: List[Dict]):
        """Draw all sprinklers using blocks"""
        for i, spr in enumerate(sprinklers):
            x = spr.get('x', 0)
            y = spr.get('y', 0)
            orientation = spr.get('orientation', 'pendant').lower()
            spr_id = spr.get('id', f'S-{i+1:03d}')
            
            # Select block based on orientation
            if 'upright' in orientation or 'up' in orientation:
                block_name = 'SPKR_UP'
            elif 'sidewall' in orientation or 'sw' in orientation:
                block_name = 'SPKR_SW'
            elif 'conceal' in orientation:
                block_name = 'SPKR_CONC'
            elif 'esfr' in orientation.lower():
                block_name = 'SPKR_ESFR'
            else:
                block_name = 'SPKR_PEND'
            
            # Insert block
            self.msp.add_blockref(
                block_name,
                (x, y),
                dxfattribs={'layer': 'FP-SPKR'}
            )
            
            # Add sprinkler ID label
            self.msp.add_text(
                spr_id,
                dxfattribs={
                    'layer': 'FP-TEXT',
                    'height': self.config.label_text_height
                }
            ).set_placement((x, y - 0.5), align=TextEntityAlignment.TOP_CENTER)
    
    def _draw_valves(self, valves: List[Dict]):
        """Draw all valves using blocks"""
        valve_blocks = {
            'os_y': 'VALVE_OSY',
            'os&y': 'VALVE_OSY',
            'gate': 'VALVE_OSY',
            'alarm_check': 'VALVE_ACV',
            'acv': 'VALVE_ACV',
            'check': 'VALVE_ACV',
            'flow_switch': 'VALVE_FS',
            'fs': 'VALVE_FS',
            'test': 'VALVE_TD',
            'drain': 'VALVE_TD',
            't&d': 'VALVE_TD',
            'fdc': 'VALVE_FDC',
        }
        
        for valve in valves:
            loc = valve.get('location', (0, 0, 0))
            valve_type = valve.get('type', 'gate').lower()
            
            block_name = valve_blocks.get(valve_type, 'VALVE_OSY')
            
            self.msp.add_blockref(
                block_name,
                (loc[0], loc[1]),
                dxfattribs={'layer': 'FP-VALVE'}
            )
    
    def _draw_hangers(self, hangers: List[Dict]):
        """Draw all hangers"""
        for hanger in hangers:
            loc = hanger.get('location', (0, 0, 0))
            
            self.msp.add_blockref(
                'HANGER',
                (loc[0], loc[1]),
                dxfattribs={'layer': 'FP-HANGER'}
            )
    
    def _draw_braces(self, braces: List[Dict]):
        """Draw all seismic braces"""
        for brace in braces:
            loc = brace.get('location', (0, 0, 0))
            brace_type = brace.get('type', 'lateral').lower()
            
            if 'longitudinal' in brace_type or 'long' in brace_type:
                block_name = 'BRACE_LONG'
            elif '4' in brace_type or 'four' in brace_type:
                block_name = 'BRACE_4WAY'
            else:
                block_name = 'BRACE_LAT'
            
            self.msp.add_blockref(
                block_name,
                (loc[0], loc[1]),
                dxfattribs={'layer': 'FP-BRACE'}
            )
    
    def _draw_coverage_areas(self, sprinklers: List[Dict]):
        """Draw sprinkler coverage areas"""
        for spr in sprinklers:
            x = spr.get('x', 0)
            y = spr.get('y', 0)
            coverage = spr.get('coverage', 130)  # sqft
            
            # Calculate coverage radius (assuming square coverage converted to circle)
            radius = math.sqrt(coverage / math.pi)
            
            self.msp.add_circle(
                (x, y),
                radius=radius,
                dxfattribs={
                    'layer': 'FP-SPKR-COV',
                    'linetype': 'DASHED'
                }
            )
    
    # =========================================================================
    # DIMENSION METHODS
    # =========================================================================
    
    def _add_pipe_dimensions(self, pipes: List[Dict]):
        """Add dimensions to pipes"""
        for pipe in pipes:
            if pipe.get('type') == 'riser':
                continue
            
            start = pipe.get('start', (0, 0, 0))
            end = pipe.get('end', (0, 0, 0))
            length = pipe.get('length', 0)
            
            if length == 0:
                length = math.sqrt(
                    (end[0] - start[0])**2 + 
                    (end[1] - start[1])**2
                )
            
            # Only dimension longer pipes (avoid clutter)
            if length < 3:
                continue
            
            # Calculate dimension line offset
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            pipe_length = math.sqrt(dx*dx + dy*dy)
            
            if pipe_length > 0:
                # Perpendicular offset
                offset = self.config.dim_offset
                nx = -dy / pipe_length * offset
                ny = dx / pipe_length * offset
                
                # Add aligned dimension
                self.msp.add_aligned_dim(
                    p1=(start[0], start[1]),
                    p2=(end[0], end[1]),
                    distance=offset,
                    dimstyle='FP_DIM',
                    override={'dimtxt': self.config.dim_text_height},
                    dxfattribs={'layer': 'FP-DIM'}
                ).render()
    
    def _add_sprinkler_spacing_dimensions(self, sprinklers: List[Dict]):
        """Add spacing dimensions between adjacent sprinklers"""
        if len(sprinklers) < 2:
            return
        
        # Group sprinklers by approximate Y coordinate (branch lines)
        branches = {}
        tolerance = 1.0
        
        for spr in sprinklers:
            y = round(spr.get('y', 0) / tolerance) * tolerance
            if y not in branches:
                branches[y] = []
            branches[y].append(spr)
        
        # Sort each branch by X and add dimensions
        for y, branch_sprs in branches.items():
            if len(branch_sprs) < 2:
                continue
            
            # Sort by X
            branch_sprs.sort(key=lambda s: s.get('x', 0))
            
            # Add dimension between first two sprinklers only (to avoid clutter)
            s1 = branch_sprs[0]
            s2 = branch_sprs[1]
            
            self.msp.add_aligned_dim(
                p1=(s1.get('x', 0), s1.get('y', 0)),
                p2=(s2.get('x', 0), s2.get('y', 0)),
                distance=-1.0,  # Below
                dimstyle='FP_DIM',
                override={'dimtxt': self.config.dim_text_height},
                dxfattribs={'layer': 'FP-DIM'}
            ).render()
    
    # =========================================================================
    # SCHEDULE METHODS
    # =========================================================================
    
    def _draw_sprinkler_schedule(self, sprinklers: List[Dict]):
        """Draw sprinkler schedule table"""
        pos = self.config.sprinkler_schedule_pos
        th = self.config.schedule_text_height
        
        # Title
        self.msp.add_text(
            'SPRINKLER SCHEDULE',
            dxfattribs={'layer': 'FP-SCHED', 'height': th * 1.5}
        ).set_placement((pos[0], pos[1]), align=TextEntityAlignment.TOP_LEFT)
        
        # Column headers
        headers = ['QTY', 'TYPE', 'K-FACTOR', 'TEMP', 'COVERAGE', 'MODEL']
        col_widths = [4, 8, 6, 5, 7, 10]
        
        y = pos[1] - 0.8
        x = pos[0]
        
        # Header row
        for i, (header, width) in enumerate(zip(headers, col_widths)):
            self.msp.add_text(
                header,
                dxfattribs={'layer': 'FP-SCHED', 'height': th}
            ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
            x += width
        
        # Header line
        total_width = sum(col_widths)
        self.msp.add_line(
            (pos[0], y - 0.3), (pos[0] + total_width, y - 0.3),
            dxfattribs={'layer': 'FP-SCHED'}
        )
        
        # Group sprinklers by type
        spr_groups = {}
        for spr in sprinklers:
            key = (
                spr.get('orientation', 'pendant'),
                spr.get('k_factor', 5.6),
                spr.get('temp_rating', 155)
            )
            if key not in spr_groups:
                spr_groups[key] = []
            spr_groups[key].append(spr)
        
        # Data rows
        y -= 0.6
        for (orientation, k, temp), group in spr_groups.items():
            x = pos[0]
            row_data = [
                str(len(group)),
                orientation.upper()[:7],
                str(k),
                f'{temp}°F',
                f'{group[0].get("coverage", 130)} SF',
                group[0].get('model', 'TBD')[:9]
            ]
            
            for value, width in zip(row_data, col_widths):
                self.msp.add_text(
                    value,
                    dxfattribs={'layer': 'FP-SCHED', 'height': th * 0.9}
                ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
                x += width
            y -= 0.5
        
        # Border
        self.msp.add_lwpolyline([
            (pos[0] - 0.2, pos[1] + 0.2),
            (pos[0] + total_width + 0.2, pos[1] + 0.2),
            (pos[0] + total_width + 0.2, y - 0.2),
            (pos[0] - 0.2, y - 0.2),
            (pos[0] - 0.2, pos[1] + 0.2)
        ], dxfattribs={'layer': 'FP-SCHED'})
    
    def _draw_pipe_schedule(self, pipes: List[Dict]):
        """Draw pipe schedule table"""
        pos = self.config.pipe_schedule_pos
        th = self.config.schedule_text_height
        
        # Title
        self.msp.add_text(
            'PIPE SCHEDULE',
            dxfattribs={'layer': 'FP-SCHED', 'height': th * 1.5}
        ).set_placement((pos[0], pos[1]), align=TextEntityAlignment.TOP_LEFT)
        
        # Headers
        headers = ['SIZE', 'TYPE', 'LENGTH (FT)', 'MATERIAL']
        col_widths = [5, 8, 8, 10]
        
        y = pos[1] - 0.8
        x = pos[0]
        
        for header, width in zip(headers, col_widths):
            self.msp.add_text(
                header,
                dxfattribs={'layer': 'FP-SCHED', 'height': th}
            ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
            x += width
        
        # Header line
        total_width = sum(col_widths)
        self.msp.add_line(
            (pos[0], y - 0.3), (pos[0] + total_width, y - 0.3),
            dxfattribs={'layer': 'FP-SCHED'}
        )
        
        # Group pipes by diameter
        pipe_groups = {}
        for pipe in pipes:
            dia = pipe.get('diameter', 1.0)
            ptype = pipe.get('type', 'branch')
            key = (dia, ptype)
            if key not in pipe_groups:
                pipe_groups[key] = 0
            pipe_groups[key] += pipe.get('length', 0)
        
        # Data rows
        y -= 0.6
        for (dia, ptype), total_length in sorted(pipe_groups.items()):
            x = pos[0]
            row_data = [
                f'{dia}"',
                ptype.upper()[:7],
                f'{total_length:.0f}',
                'BLACK STEEL'
            ]
            
            for value, width in zip(row_data, col_widths):
                self.msp.add_text(
                    value,
                    dxfattribs={'layer': 'FP-SCHED', 'height': th * 0.9}
                ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
                x += width
            y -= 0.5
        
        # Total
        y -= 0.2
        total_pipe = sum(p.get('length', 0) for p in pipes)
        self.msp.add_text(
            f'TOTAL: {total_pipe:.0f} LF',
            dxfattribs={'layer': 'FP-SCHED', 'height': th}
        ).set_placement((pos[0], y), align=TextEntityAlignment.TOP_LEFT)
    
    def _draw_valve_schedule(self, valves: List[Dict]):
        """Draw valve schedule table"""
        pos = self.config.valve_schedule_pos
        th = self.config.schedule_text_height
        
        # Title
        self.msp.add_text(
            'VALVE SCHEDULE',
            dxfattribs={'layer': 'FP-SCHED', 'height': th * 1.5}
        ).set_placement((pos[0], pos[1]), align=TextEntityAlignment.TOP_LEFT)
        
        # Headers
        headers = ['QTY', 'TYPE', 'SIZE', 'LOCATION']
        col_widths = [4, 12, 5, 12]
        
        y = pos[1] - 0.8
        x = pos[0]
        
        for header, width in zip(headers, col_widths):
            self.msp.add_text(
                header,
                dxfattribs={'layer': 'FP-SCHED', 'height': th}
            ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
            x += width
        
        # Header line
        total_width = sum(col_widths)
        self.msp.add_line(
            (pos[0], y - 0.3), (pos[0] + total_width, y - 0.3),
            dxfattribs={'layer': 'FP-SCHED'}
        )
        
        # Group valves by type
        valve_types = {
            'os_y': 'OS&Y GATE VALVE',
            'alarm_check': 'ALARM CHECK VALVE',
            'flow_switch': 'FLOW SWITCH',
            'drain': 'MAIN DRAIN',
            'test': 'INSPECTOR TEST',
            'fdc': 'FDC (2½" × 2½")'
        }
        
        valve_groups = {}
        for valve in valves:
            vtype = valve.get('type', 'gate')
            if vtype not in valve_groups:
                valve_groups[vtype] = []
            valve_groups[vtype].append(valve)
        
        y -= 0.6
        for vtype, group in valve_groups.items():
            x = pos[0]
            size = group[0].get('size', 4)
            row_data = [
                str(len(group)),
                valve_types.get(vtype, vtype.upper())[:11],
                f'{size}"',
                'RISER ROOM'
            ]
            
            for value, width in zip(row_data, col_widths):
                self.msp.add_text(
                    value,
                    dxfattribs={'layer': 'FP-SCHED', 'height': th * 0.9}
                ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
                x += width
            y -= 0.5
    
    def _draw_hanger_brace_schedule(self, hangers: List[Dict], braces: List[Dict]):
        """Draw hanger and brace schedule"""
        pos = self.config.hanger_schedule_pos
        th = self.config.schedule_text_height
        
        # Title
        self.msp.add_text(
            'HANGER & BRACE SCHEDULE',
            dxfattribs={'layer': 'FP-SCHED', 'height': th * 1.5}
        ).set_placement((pos[0], pos[1]), align=TextEntityAlignment.TOP_LEFT)
        
        y = pos[1] - 0.8
        
        # Hangers by pipe size
        self.msp.add_text(
            f'HANGERS: {len(hangers)} TOTAL',
            dxfattribs={'layer': 'FP-SCHED', 'height': th}
        ).set_placement((pos[0], y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.5
        
        # Braces
        lateral = sum(1 for b in braces if 'lateral' in b.get('type', '').lower())
        longitudinal = sum(1 for b in braces if 'long' in b.get('type', '').lower())
        fourway = sum(1 for b in braces if '4' in b.get('type', ''))
        
        self.msp.add_text(
            f'SEISMIC BRACES:',
            dxfattribs={'layer': 'FP-SCHED', 'height': th}
        ).set_placement((pos[0], y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            f'  LATERAL (L): {lateral}',
            dxfattribs={'layer': 'FP-SCHED', 'height': th * 0.9}
        ).set_placement((pos[0], y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            f'  LONGITUDINAL (LG): {longitudinal}',
            dxfattribs={'layer': 'FP-SCHED', 'height': th * 0.9}
        ).set_placement((pos[0], y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            f'  4-WAY (4W): {fourway}',
            dxfattribs={'layer': 'FP-SCHED', 'height': th * 0.9}
        ).set_placement((pos[0], y), align=TextEntityAlignment.TOP_LEFT)
    
    # =========================================================================
    # LEGEND AND NOTES
    # =========================================================================
    
    def _draw_legend(self):
        """Draw symbol legend"""
        pos = self.config.legend_pos
        th = self.config.schedule_text_height
        
        # Title
        self.msp.add_text(
            'SYMBOL LEGEND',
            dxfattribs={'layer': 'FP-LEGEND', 'height': th * 1.5}
        ).set_placement((pos[0], pos[1]), align=TextEntityAlignment.TOP_LEFT)
        
        y = pos[1] - 1.0
        spacing = 1.2
        
        symbols = [
            ('SPKR_PEND', 'PENDANT SPRINKLER'),
            ('SPKR_UP', 'UPRIGHT SPRINKLER'),
            ('SPKR_SW', 'SIDEWALL SPRINKLER'),
            ('VALVE_OSY', 'OS&Y GATE VALVE'),
            ('VALVE_ACV', 'ALARM CHECK VALVE'),
            ('VALVE_FS', 'FLOW SWITCH'),
            ('VALVE_FDC', 'FIRE DEPT. CONNECTION'),
            ('HANGER', 'PIPE HANGER'),
            ('BRACE_LAT', 'LATERAL BRACE'),
            ('BRACE_LONG', 'LONGITUDINAL BRACE'),
        ]
        
        for block_name, description in symbols:
            # Symbol
            self.msp.add_blockref(
                block_name,
                (pos[0] + 0.5, y),
                dxfattribs={'layer': 'FP-LEGEND'}
            )
            
            # Description
            self.msp.add_text(
                description,
                dxfattribs={'layer': 'FP-LEGEND', 'height': th}
            ).set_placement((pos[0] + 1.5, y), align=TextEntityAlignment.MIDDLE_LEFT)
            
            y -= spacing
        
        # Border
        self.msp.add_lwpolyline([
            (pos[0] - 0.3, pos[1] + 0.3),
            (pos[0] + 15, pos[1] + 0.3),
            (pos[0] + 15, y - 0.3),
            (pos[0] - 0.3, y - 0.3),
            (pos[0] - 0.3, pos[1] + 0.3)
        ], dxfattribs={'layer': 'FP-LEGEND'})
    
    def _draw_general_notes(self, design_data: Dict):
        """Draw general notes"""
        pos = self.config.notes_pos
        th = self.config.note_text_height
        
        # Title
        self.msp.add_text(
            'GENERAL NOTES',
            dxfattribs={'layer': 'FP-NOTES', 'height': th * 1.5}
        ).set_placement((pos[0], pos[1]), align=TextEntityAlignment.TOP_LEFT)
        
        notes = [
            "1. ALL WORK SHALL COMPLY WITH NFPA 13 (2022 EDITION)",
            "   AND ALL LOCAL CODES AND AMENDMENTS.",
            "",
            "2. CONTRACTOR TO VERIFY ALL DIMENSIONS IN FIELD",
            "   PRIOR TO FABRICATION.",
            "",
            "3. ALL PIPE TO BE BLACK STEEL SCHEDULE 40 UNLESS",
            "   OTHERWISE NOTED.",
            "",
            "4. PROVIDE ESCUTCHEONS AT ALL SPRINKLERS.",
            "",
            "5. ALL HANGERS PER NFPA 13 CHAPTER 9.",
            "",
            "6. SEISMIC BRACING PER NFPA 13 & ASCE 7-22.",
            "",
            "7. HYDRAULIC CALCULATIONS ON FILE.",
            "",
            "8. OBTAIN ALL REQUIRED PERMITS BEFORE",
            "   STARTING WORK.",
            "",
            "9. COORDINATE ALL PENETRATIONS WITH OTHER",
            "   TRADES BEFORE INSTALLATION.",
        ]
        
        y = pos[1] - 0.6
        for note in notes:
            self.msp.add_text(
                note,
                dxfattribs={'layer': 'FP-NOTES', 'height': th * 0.9}
            ).set_placement((pos[0], y), align=TextEntityAlignment.TOP_LEFT)
            y -= 0.35
    
    # =========================================================================
    # TITLE BLOCK
    # =========================================================================
    
    def _draw_title_block(self, project_data: ProjectData, design_data: Dict):
        """Draw professional title block"""
        pos = self.config.title_block_pos
        w = self.config.title_block_width
        h = self.config.title_block_height
        
        # Outer border
        self.msp.add_lwpolyline([
            (pos[0], pos[1]),
            (pos[0] + w, pos[1]),
            (pos[0] + w, pos[1] + h),
            (pos[0], pos[1] + h),
            (pos[0], pos[1])
        ], dxfattribs={'layer': 'FP-BORDER'})
        
        # Internal divisions
        # Left section (project info) - 40% width
        left_w = w * 0.4
        self.msp.add_line(
            (pos[0] + left_w, pos[1]),
            (pos[0] + left_w, pos[1] + h),
            dxfattribs={'layer': 'FP-TITLE'}
        )
        
        # Middle section (contractor) - 30% width
        mid_w = w * 0.3
        self.msp.add_line(
            (pos[0] + left_w + mid_w, pos[1]),
            (pos[0] + left_w + mid_w, pos[1] + h),
            dxfattribs={'layer': 'FP-TITLE'}
        )
        
        # Horizontal divisions
        self.msp.add_line(
            (pos[0], pos[1] + h * 0.6),
            (pos[0] + left_w, pos[1] + h * 0.6),
            dxfattribs={'layer': 'FP-TITLE'}
        )
        
        th = self.config.title_text_height
        
        # Project info (left section)
        x = pos[0] + 0.3
        y = pos[1] + h - 0.3
        
        self.msp.add_text(
            project_data.project_name[:30] or 'PROJECT NAME',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 1.2}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.6
        self.msp.add_text(
            project_data.address[:35] or 'ADDRESS',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.8}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            project_data.city_state_zip[:35] or 'CITY, STATE ZIP',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.8}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        # System info (lower left)
        y = pos[1] + h * 0.6 - 0.3
        self.msp.add_text(
            f'SYSTEM TYPE: {project_data.system_type}',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.35
        self.msp.add_text(
            f'HAZARD: {project_data.hazard_class}',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.35
        demand = design_data.get('system_demand', 0)
        pressure = design_data.get('system_pressure', 0)
        self.msp.add_text(
            f'DEMAND: {demand:.0f} GPM @ {pressure:.1f} PSI',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        # Contractor info (middle section)
        x = pos[0] + left_w + 0.3
        y = pos[1] + h - 0.3
        
        self.msp.add_text(
            'CONTRACTOR:',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            project_data.contractor_name[:25] or '________________________',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.8}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            f'LIC: {project_data.contractor_license}' if project_data.contractor_license else 'LIC: ____________',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.6
        self.msp.add_text(
            'DESIGNER: ___________________',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            'DATE: ___________________',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        # Drawing info (right section)
        x = pos[0] + left_w + mid_w + 0.3
        y = pos[1] + h - 0.3
        
        self.msp.add_text(
            project_data.drawing_title,
            dxfattribs={'layer': 'FP-TITLE', 'height': th}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.5
        self.msp.add_text(
            f'DWG NO: {project_data.drawing_number}',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.8}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            f'DATE: {project_data.date}',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.8}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            f'SCALE: {project_data.scale}',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.8}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.4
        self.msp.add_text(
            f'REV: {project_data.revision}',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.8}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        # Sprinkler/pipe counts
        y -= 0.6
        num_spkr = len(design_data.get('sprinklers', []))
        num_pipe = sum(p.get('length', 0) for p in design_data.get('pipes', []))
        self.msp.add_text(
            f'SPRINKLERS: {num_spkr}',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
        
        y -= 0.35
        self.msp.add_text(
            f'PIPE: {num_pipe:.0f} LF',
            dxfattribs={'layer': 'FP-TITLE', 'height': th * 0.7}
        ).set_placement((x, y), align=TextEntityAlignment.TOP_LEFT)
    
    # =========================================================================
    # RISER DIAGRAM
    # =========================================================================
    
    def _draw_riser_diagram(self, design_data: Dict, project_data: ProjectData):
        """Draw isometric riser diagram"""
        # Position riser diagram to the right of main drawing
        pos = (110, 0)
        
        th = self.config.schedule_text_height
        
        # Title
        self.msp.add_text(
            'RISER DIAGRAM',
            dxfattribs={'layer': 'FP-RISER', 'height': th * 1.5}
        ).set_placement((pos[0], pos[1]), align=TextEntityAlignment.TOP_LEFT)
        
        # Simple isometric representation
        # Water supply
        y = pos[1] - 2
        self.msp.add_text(
            'WATER SUPPLY',
            dxfattribs={'layer': 'FP-RISER', 'height': th}
        ).set_placement((pos[0], y), align=TextEntityAlignment.TOP_LEFT)
        
        # Underground
        y -= 1
        self.msp.add_line(
            (pos[0] + 2, y), (pos[0] + 2, y + 0.5),
            dxfattribs={'layer': 'FP-RISER'}
        )
        self.msp.add_text(
            'UNDERGROUND',
            dxfattribs={'layer': 'FP-RISER', 'height': th * 0.8}
        ).set_placement((pos[0] + 3, y + 0.2), align=TextEntityAlignment.MIDDLE_LEFT)
        
        # OS&Y
        y -= 1.5
        self.msp.add_line(
            (pos[0] + 2, y + 1.5), (pos[0] + 2, y),
            dxfattribs={'layer': 'FP-RISER'}
        )
        self.msp.add_blockref('VALVE_OSY', (pos[0] + 2, y), dxfattribs={'layer': 'FP-RISER'})
        self.msp.add_text(
            'OS&Y VALVE',
            dxfattribs={'layer': 'FP-RISER', 'height': th * 0.8}
        ).set_placement((pos[0] + 3, y), align=TextEntityAlignment.MIDDLE_LEFT)
        
        # Check valve
        y -= 1.5
        self.msp.add_line(
            (pos[0] + 2, y + 1.5), (pos[0] + 2, y),
            dxfattribs={'layer': 'FP-RISER'}
        )
        self.msp.add_blockref('VALVE_ACV', (pos[0] + 2, y), dxfattribs={'layer': 'FP-RISER'})
        self.msp.add_text(
            f'ALARM CHECK ({project_data.system_type})',
            dxfattribs={'layer': 'FP-RISER', 'height': th * 0.8}
        ).set_placement((pos[0] + 3, y), align=TextEntityAlignment.MIDDLE_LEFT)
        
        # Flow switch
        y -= 1.5
        self.msp.add_line(
            (pos[0] + 2, y + 1.5), (pos[0] + 2, y),
            dxfattribs={'layer': 'FP-RISER'}
        )
        self.msp.add_blockref('VALVE_FS', (pos[0] + 2, y), dxfattribs={'layer': 'FP-RISER'})
        self.msp.add_text(
            'FLOW SWITCH',
            dxfattribs={'layer': 'FP-RISER', 'height': th * 0.8}
        ).set_placement((pos[0] + 3, y), align=TextEntityAlignment.MIDDLE_LEFT)
        
        # Main drain branch
        self.msp.add_line(
            (pos[0] + 2, y), (pos[0] + 5, y),
            dxfattribs={'layer': 'FP-RISER'}
        )
        self.msp.add_text(
            'MAIN DRAIN',
            dxfattribs={'layer': 'FP-RISER', 'height': th * 0.8}
        ).set_placement((pos[0] + 5.5, y), align=TextEntityAlignment.MIDDLE_LEFT)
        
        # Riser
        y -= 2
        self.msp.add_line(
            (pos[0] + 2, y + 2), (pos[0] + 2, y),
            dxfattribs={'layer': 'FP-RISER'}
        )
        
        # To system
        self.msp.add_line(
            (pos[0] + 2, y), (pos[0] + 8, y),
            dxfattribs={'layer': 'FP-RISER'}
        )
        self.msp.add_text(
            'TO SPRINKLER SYSTEM',
            dxfattribs={'layer': 'FP-RISER', 'height': th * 0.8}
        ).set_placement((pos[0] + 8.5, y), align=TextEntityAlignment.MIDDLE_LEFT)
        
        # FDC
        fdc_y = pos[1] - 5
        self.msp.add_line(
            (pos[0] + 2, fdc_y), (pos[0] - 1, fdc_y),
            dxfattribs={'layer': 'FP-RISER'}
        )
        self.msp.add_blockref('VALVE_FDC', (pos[0] - 2, fdc_y), dxfattribs={'layer': 'FP-RISER'})
        self.msp.add_text(
            'FDC',
            dxfattribs={'layer': 'FP-RISER', 'height': th * 0.8}
        ).set_placement((pos[0] - 3, fdc_y), align=TextEntityAlignment.MIDDLE_RIGHT)
        
        # Test connection
        test_y = y + 0.5
        self.msp.add_line(
            (pos[0] + 8, test_y), (pos[0] + 10, test_y),
            dxfattribs={'layer': 'FP-RISER'}
        )
        self.msp.add_text(
            "INSPECTOR'S TEST",
            dxfattribs={'layer': 'FP-RISER', 'height': th * 0.8}
        ).set_placement((pos[0] + 10.5, test_y), align=TextEntityAlignment.MIDDLE_LEFT)
        
        # Border
        self.msp.add_lwpolyline([
            (pos[0] - 4, pos[1] + 0.5),
            (pos[0] + 18, pos[1] + 0.5),
            (pos[0] + 18, y - 1),
            (pos[0] - 4, y - 1),
            (pos[0] - 4, pos[1] + 0.5)
        ], dxfattribs={'layer': 'FP-RISER'})


# =============================================================================
# MODULE INTERFACE
# =============================================================================

def generate_shop_drawing(design_data: Dict[str, Any],
                          project_data: Optional[ProjectData] = None,
                          output_path: str = 'shop_drawing.dxf',
                          config: Optional[ShopDrawingConfig] = None) -> bool:
    """
    Generate professional shop drawing DXF
    
    Args:
        design_data: Dict with keys: sprinklers, pipes, valves, hangers, braces
        project_data: Project information (optional)
        output_path: Output file path
        config: Drawing configuration (optional)
    
    Returns:
        True if successful
    """
    engine = ProfessionalDXFEngine(config)
    project = project_data or ProjectData()
    return engine.generate_shop_drawing(design_data, project, output_path)


# =============================================================================
# MODULE EXPORTS
# =============================================================================

__all__ = [
    'ProfessionalDXFEngine',
    'ShopDrawingConfig',
    'ProjectData',
    'generate_shop_drawing',
    'EZDXF_AVAILABLE',
]


if __name__ == "__main__":
    print("🏗️ FireAI Pro - Professional DXF Shop Drawing Engine v1.0.0")
    print("=" * 60)
    print(f"ezdxf available: {'✅' if EZDXF_AVAILABLE else '❌'}")
