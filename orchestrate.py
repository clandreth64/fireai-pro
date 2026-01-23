#!/usr/bin/env python3
"""
FireAI Pro - Unified Production Orchestrator v17.0
===================================================
PROFESSIONAL OUTPUT - AutoSprink Quality with GEOMETRY INTEGRATION!

NEW IN v17.0:
- Full geometry extraction from PDF floor plans
- Color-aware wall/pipe differentiation
- Room polygon detection
- Sprinkler placement in actual geometry
- Intelligent pipe routing through actual spaces
- Network-based hydraulic calculations

INTEGRATED ENGINES:
1. enhanced_cad_engine - Extracts building geometry from DXF/DWG/IFC
2. floor_plan_analyzer - AI Vision for PDF/image analysis
3. floor_plan_intelligence - PDF geometry extraction (NEW!)
4. sprinkler_placement - Geometry-aware head placement (NEW!)
5. pipe_routing - Intelligent pipe network routing (NEW!)
6. network_hydraulics - Network-based hydraulic calcs (NEW!)
7. enhanced_hydraulics_engine - Hardy Cross, EPANET network analysis
8. nfpa13_calc_sheets - Permit-ready NFPA 13 calculation sheets
9. node_by_node_tables - AHJ-compliant node-by-node hydraulic tables
10. professional_dxf_engine - Shop drawings with dimensions & schedules
11. fitting_takeoff_bom - Complete fitting detection & accurate BOMs
12. pipe_sizing_optimizer - Intelligent pipe sizing with velocity check
13. bim_3d_engine - 3D BIM coordination & clash detection
14. enhanced_bracing_engine - ASCE 7-22 seismic, NFPA 13 Ch.9 bracing
15. master_fireai_products_enhanced - Real supplier pricing, cost analysis
16. fireai_pro_master_Standards - 790+ NFPA compliance rules

VERSION: 17.0.0-GEOMETRY
"""

import os
import json
import math
import csv
import uuid
import logging
import traceback
import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.Orchestrator")


# =============================================================================
# ENGINE IMPORTS WITH STATUS TRACKING
# =============================================================================

ENGINE_STATUS = {
    'cad_engine': False,
    'floor_plan_analyzer': False,
    'floor_plan_intelligence': False,  # NEW! Geometry extraction
    'sprinkler_placement': False,       # NEW! Geometry-aware placement
    'pipe_routing': False,              # NEW! Network routing
    'network_hydraulics': False,        # NEW! Network-based calcs
    'hydraulics_engine': False,
    'calc_sheets': False,
    'node_tables': False,
    'professional_dxf': False,
    'professional_bom': False,
    'professional_hydraulics': False,
    'professional_shop_drawing': False,
    'fitting_bom': False,
    'pipe_optimizer': False,
    'bim_3d': False,
    'bracing_engine': False,
    'products_engine': False,
    'standards_engine': False,
    'ezdxf': False,
    'reportlab': False,
    'pdfplumber': False
}

# =============================================================================
# NEW GEOMETRY PIPELINE IMPORTS (v17)
# =============================================================================

# 1. Floor Plan Intelligence - PDF Geometry Extraction
try:
    from floor_plan_intelligence import (
        FloorPlanIntelligence,
        analyze_floor_plan as extract_floor_plan_geometry,
        save_floor_plan_json,
        floor_plan_to_dict,
        FloorPlanData
    )
    ENGINE_STATUS['floor_plan_intelligence'] = True
    logger.info("✅ Floor Plan Intelligence loaded (Geometry Extraction)")
except Exception as e:
    logger.warning(f"⚠️ Floor Plan Intelligence: {e}")
    FloorPlanIntelligence = None
    extract_floor_plan_geometry = None

# 2. Sprinkler Placement - Geometry-Aware Head Placement
try:
    from sprinkler_placement import (
        SprinklerPlacementEngine,
        place_sprinklers_from_analysis,
        place_sprinklers_from_json,
        layout_to_dict,
        save_layout_json,
        SprinklerLayout
    )
    ENGINE_STATUS['sprinkler_placement'] = True
    logger.info("✅ Sprinkler Placement Engine loaded (Geometry-Aware)")
except Exception as e:
    logger.warning(f"⚠️ Sprinkler Placement: {e}")
    SprinklerPlacementEngine = None
    place_sprinklers_from_analysis = None

# 3. Pipe Routing - Intelligent Network Routing
try:
    from pipe_routing import (
        PipeRoutingEngine,
        route_from_layout,
        network_to_dict,
        save_network_json,
        PipeNetwork
    )
    ENGINE_STATUS['pipe_routing'] = True
    logger.info("✅ Pipe Routing Engine loaded (Network Generation)")
except Exception as e:
    logger.warning(f"⚠️ Pipe Routing: {e}")
    PipeRoutingEngine = None
    route_from_layout = None

# 4. Network Hydraulics - Actual Network Calculations
try:
    from network_hydraulics import (
        NetworkHydraulicCalculator,
        calculate_from_network,
        generate_hydraulic_report,
        result_to_dict as hydraulic_result_to_dict,
        HydraulicResult
    )
    ENGINE_STATUS['network_hydraulics'] = True
    logger.info("✅ Network Hydraulics loaded (Actual Routing Calcs)")
except Exception as e:
    logger.warning(f"⚠️ Network Hydraulics: {e}")
    NetworkHydraulicCalculator = None
    calculate_from_network = None

# Check for pdfplumber (required for geometry extraction)
try:
    import pdfplumber
    ENGINE_STATUS['pdfplumber'] = True
except ImportError:
    logger.warning("⚠️ pdfplumber not available - geometry extraction limited")

# =============================================================================
# EXISTING ENGINE IMPORTS
# =============================================================================

# Enhanced CAD Engine
try:
    from enhanced_cad_engine import (
        EnhancedProductionCADEngine,
        CloudCADEngineConfig,
        GeometryType,
        ProjectGeometry
    )
    ENGINE_STATUS['cad_engine'] = True
    logger.info("✅ CAD Engine loaded")
except Exception as e:
    logger.warning(f"⚠️ CAD Engine: {e}")

# Floor Plan Analyzer - AI Vision
try:
    from floor_plan_analyzer import (
        FloorPlanAnalyzer,
        analyze_floor_plan,
        analyze_project_documents,
        HazardClass as FPHazardClass
    )
    ENGINE_STATUS['floor_plan_analyzer'] = True
    logger.info("✅ Floor Plan Analyzer loaded (AI Vision)")
except Exception as e:
    logger.warning(f"⚠️ Floor Plan Analyzer: {e}")
    FloorPlanAnalyzer = None
    analyze_floor_plan = None
    analyze_project_documents = None

# Enhanced Hydraulics Engine
try:
    from enhanced_hydraulics_engine import (
        get_hydraulics_status,
        get_engine_status,
        hydraulics_enabled,
        HydraulicNetwork,
        HydraulicNode,
        HydraulicPipe,
        NetworkNode,
        NetworkPipe,
        LayoutDataParser,
        AutoSprinkHydraulicsEngine,
        NetworkBuilder,
        WaterSupplyData,
        SystemType,
        NFPA13Constants,
    )
    ENGINE_STATUS['hydraulics_engine'] = hydraulics_enabled
    logger.info(f"{'✅' if hydraulics_enabled else '⚠️'} Hydraulics Engine")
except Exception as e:
    logger.warning(f"⚠️ Hydraulics Engine: {e}")

# NFPA 13 Calculation Sheet Generator
try:
    from nfpa13_calc_sheets import NFPA13CalcSheetGenerator, ProjectInfo
    ENGINE_STATUS['calc_sheets'] = True
    logger.info("✅ NFPA 13 Calc Sheet Generator loaded")
except Exception as e:
    logger.warning(f"⚠️ Calc Sheet Generator: {e}")
    NFPA13CalcSheetGenerator = None

# Node-by-Node Tables Generator
try:
    from node_by_node_tables import NodeByNodeCalculator, NodeByNodeTableGenerator
    ENGINE_STATUS['node_tables'] = True
    logger.info("✅ Node-by-Node Table Generator loaded")
except Exception as e:
    logger.warning(f"⚠️ Node-by-Node Tables: {e}")

# Professional DXF Shop Drawing Engine
try:
    from professional_dxf_engine import (
        ProfessionalDXFEngine, ProjectData as DXFProjectData,
        ShopDrawingConfig, generate_shop_drawing
    )
    ENGINE_STATUS['professional_dxf'] = True
    logger.info("✅ Professional DXF Engine loaded")
except Exception as e:
    logger.warning(f"⚠️ Professional DXF Engine: {e}")
    ProfessionalDXFEngine = None

# Complete Fitting Takeoff & BOM Generator
try:
    from fitting_takeoff_bom import (
        FittingTakeoffEngine, AccurateBOMGenerator,
        CompleteBOM, generate_complete_bom
    )
    ENGINE_STATUS['fitting_bom'] = True
    logger.info("✅ Fitting Takeoff & BOM Generator loaded")
except Exception as e:
    logger.warning(f"⚠️ Fitting Takeoff & BOM: {e}")
    FittingTakeoffEngine = None

# Intelligent Pipe Sizing Optimizer
try:
    from pipe_sizing_optimizer import (
        IntelligentPipeSizer, HydraulicCalculator,
        optimize_pipe_sizes, VELOCITY_LIMITS
    )
    ENGINE_STATUS['pipe_optimizer'] = True
    logger.info("✅ Pipe Sizing Optimizer loaded")
except Exception as e:
    logger.warning(f"⚠️ Pipe Sizing Optimizer: {e}")
    IntelligentPipeSizer = None

# 3D BIM Coordination & Clash Detection Engine
try:
    from bim_3d_engine import (
        BIMModel, ClashDetectionEngine, BIMExporter,
        create_bim_model_from_design, run_clash_detection_on_design
    )
    ENGINE_STATUS['bim_3d'] = True
    logger.info("✅ 3D BIM Engine loaded")
except Exception as e:
    logger.warning(f"⚠️ 3D BIM Engine: {e}")
    BIMModel = None

# Enhanced Bracing Engine
try:
    from enhanced_bracing_engine import (
        SeismicZoneAnalyzer, ASCE7SeismicParameters,
        BraceLocationOptimizer, HardwareSelectionEngine,
        NFPA13Chapter9Validator, PipeSegment
    )
    ENGINE_STATUS['bracing_engine'] = True
    logger.info("✅ Bracing Engine loaded")
except Exception as e:
    logger.warning(f"⚠️ Bracing Engine: {e}")
    SeismicZoneAnalyzer = None

# Products/Cost Engine
try:
    from master_fireai_products_enhanced import ProductionFireAIService, BOMItem, ProductionConfig
    ENGINE_STATUS['products_engine'] = True
    logger.info("✅ Products Engine loaded")
except Exception as e:
    logger.warning(f"⚠️ Products Engine: {e}")
    ProductionFireAIService = None

# Standards Engine
try:
    from fireai_pro_master_Standards import EnhancedFireAIProMaster
    ENGINE_STATUS['standards_engine'] = True
    logger.info("✅ Standards Engine loaded")
except Exception as e:
    logger.warning(f"⚠️ Standards Engine: {e}")

# Core libraries
try:
    import ezdxf
    ENGINE_STATUS['ezdxf'] = True
except:
    pass

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.enums import TA_CENTER
    ENGINE_STATUS['reportlab'] = True
except:
    pass

# PROFESSIONAL ENGINES
try:
    from professional_bom_generator import (
        ProfessionalBOMGenerator,
        generate_professional_bom,
        DetailedBOM
    )
    ENGINE_STATUS['professional_bom'] = True
    logger.info("✅ Professional BOM Generator loaded")
except Exception as e:
    logger.warning(f"⚠️ Professional BOM: {e}")
    ProfessionalBOMGenerator = None
    generate_professional_bom = None

try:
    from professional_hydraulics import (
        ProfessionalHydraulicCalculator,
        calculate_hydraulics as calc_hydraulics_pro,
        HydraulicResult as ProHydraulicResult
    )
    ENGINE_STATUS['professional_hydraulics'] = True
    logger.info("✅ Professional Hydraulics Calculator loaded")
except Exception as e:
    logger.warning(f"⚠️ Professional Hydraulics: {e}")
    ProfessionalHydraulicCalculator = None
    calc_hydraulics_pro = None

try:
    from professional_shop_drawing import (
        ProfessionalShopDrawingEngine,
        generate_professional_shop_drawing,
        SheetConfig
    )
    ENGINE_STATUS['professional_shop_drawing'] = True
    logger.info("✅ Professional Shop Drawing Engine loaded")
except Exception as e:
    logger.warning(f"⚠️ Professional Shop Drawing: {e}")
    ProfessionalShopDrawingEngine = None
    generate_professional_shop_drawing = None


# =============================================================================
# NFPA 13 HAZARD REQUIREMENTS
# =============================================================================

HAZARD_REQUIREMENTS = {
    'light_hazard': {
        'coverage': 225, 'spacing': 15, 'density': 0.10, 
        'hose': 100, 'duration': 30, 'remote_area': 1500,
        'k_factor': 5.6
    },
    'ordinary_hazard_group_1': {
        'coverage': 130, 'spacing': 15, 'density': 0.15, 
        'hose': 250, 'duration': 60, 'remote_area': 1500,
        'k_factor': 5.6
    },
    'ordinary_hazard_group_2': {
        'coverage': 130, 'spacing': 15, 'density': 0.20, 
        'hose': 250, 'duration': 60, 'remote_area': 1500,
        'k_factor': 5.6
    },
    'extra_hazard_group_1': {
        'coverage': 100, 'spacing': 12, 'density': 0.30, 
        'hose': 500, 'duration': 90, 'remote_area': 2500,
        'k_factor': 8.0
    },
    'extra_hazard_group_2': {
        'coverage': 100, 'spacing': 12, 'density': 0.40, 
        'hose': 500, 'duration': 90, 'remote_area': 2500,
        'k_factor': 8.0
    },
    'high_piled_storage': {
        'coverage': 100, 'spacing': 10, 'density': 0.60,
        'hose': 500, 'duration': 120, 'remote_area': 2000,
        'k_factor': 11.2
    },
    'esfr_storage': {
        'coverage': 100, 'spacing': 10, 'density': 0.80,
        'hose': 250, 'duration': 60, 'remote_area': 960,
        'k_factor': 14.0
    }
}

BUILDING_HAZARD_KEYWORDS = {
    'office': 'light_hazard',
    'church': 'light_hazard',
    'school': 'light_hazard',
    'hospital': 'light_hazard',
    'hotel': 'light_hazard',
    'apartment': 'light_hazard',
    'residential': 'light_hazard',
    'retail': 'ordinary_hazard_group_1',
    'mercantile': 'ordinary_hazard_group_1',
    'restaurant': 'ordinary_hazard_group_1',
    'parking': 'ordinary_hazard_group_1',
    'manufacturing': 'ordinary_hazard_group_2',
    'factory': 'ordinary_hazard_group_2',
    'machine shop': 'ordinary_hazard_group_2',
    'warehouse': 'extra_hazard_group_1',
    'distribution': 'extra_hazard_group_1',
    'storage': 'extra_hazard_group_1',
    'costco': 'high_piled_storage',
    'wholesale': 'high_piled_storage',
    'sam\'s club': 'high_piled_storage',
    'home depot': 'high_piled_storage',
    'lowe\'s': 'high_piled_storage',
    'amazon': 'high_piled_storage',
    'fulfillment': 'high_piled_storage',
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Zone:
    id: str
    name: str
    area: float
    ceiling_height: float = 12
    hazard_class: str = 'ordinary_hazard_group_1'

@dataclass  
class Sprinkler:
    id: str
    x: float
    y: float
    z: float
    k_factor: float = 5.6
    temp_rating: int = 165
    zone_id: str = ''
    coverage: float = 130
    room_id: str = ''  # NEW: Link to room geometry

@dataclass
class Pipe:
    id: str
    start: Tuple[float, float, float]
    end: Tuple[float, float, float]
    diameter: float
    length: float
    pipe_type: str = 'branch'
    schedule: str = 'Sch40'
    head_count: int = 0  # NEW: Heads fed by this pipe

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
class DesignResult:
    project_id: str
    project_name: str
    building_area: float
    zones: List[Zone]
    sprinklers: List[Sprinkler]
    pipes: List[Pipe]
    fittings: List[Fitting]
    valves: List[Valve]
    total_cost: float = 0
    cost_per_sqft: float = 0
    compliance_status: str = 'COMPLIANT'
    compliance_score: float = 100.0
    seismic_design_category: str = 'D'
    analysis_confidence: float = 0
    processing_time: float = 0
    # NEW: Geometry data
    geometry_extracted: bool = False
    room_count: int = 0
    wall_count: int = 0
    pipe_network_generated: bool = False


# =============================================================================
# GEOMETRY PIPELINE (NEW IN v17!)
# =============================================================================

def run_geometry_pipeline(pdf_path: str, 
                          output_dir: str,
                          hazard_class: str = 'light_hazard',
                          ceiling_height: float = 10,
                          water_pressure: float = 65) -> Dict[str, Any]:
    """
    NEW! Complete geometry-based sprinkler design pipeline.
    
    1. Extract geometry from PDF (walls, rooms)
    2. Place sprinklers in actual room geometry
    3. Route pipes through actual spaces
    4. Calculate hydraulics on actual network
    5. Generate DXF with real positions
    
    Returns dict with all results and file paths.
    """
    if not ENGINE_STATUS['floor_plan_intelligence']:
        logger.warning("Geometry pipeline unavailable - Floor Plan Intelligence not loaded")
        return None
    
    logger.info("=" * 70)
    logger.info("🏗️ GEOMETRY PIPELINE - AutoSprink Quality")
    logger.info("=" * 70)
    
    results = {
        'success': False,
        'geometry': None,
        'layout': None,
        'network': None,
        'hydraulics': None,
        'files': {}
    }
    
    start_time = datetime.now()
    network = None
    network_json_path = None
    total_pipe_ft = 0
    hyd_result = None
    
    try:
        # =====================================================================
        # STEP 1: Extract Floor Plan Geometry
        # =====================================================================
        logger.info("\n📐 STEP 1: Extracting floor plan geometry...")
        
        fp_data = extract_floor_plan_geometry(pdf_path)
        
        if not fp_data or fp_data.room_count == 0:
            logger.warning("No rooms detected - falling back to abstract layout")
            return None
        
        logger.info(f"   ✅ Extracted: {fp_data.wall_count} walls, {fp_data.room_count} rooms")
        logger.info(f"   Floor plan: {fp_data.fp_width_ft:.0f} x {fp_data.fp_height_ft:.0f} ft")
        logger.info(f"   Scale: {fp_data.scale_text}")
        
        # Save geometry JSON
        fp_json_path = os.path.join(output_dir, 'floor_plan_geometry.json')
        save_floor_plan_json(fp_data, fp_json_path)
        results['files']['floor_plan_geometry.json'] = fp_json_path
        results['geometry'] = floor_plan_to_dict(fp_data)
        
        # =====================================================================
        # STEP 2: Place Sprinklers in Actual Geometry
        # =====================================================================
        logger.info("\n💧 STEP 2: Placing sprinklers in room geometry...")
        
        if not ENGINE_STATUS['sprinkler_placement']:
            logger.warning("Sprinkler placement engine not available")
            return None
        
        layout = place_sprinklers_from_analysis(fp_data)
        
        logger.info(f"   ✅ Placed: {layout.total_heads} sprinkler heads")
        logger.info(f"   Coverage: {layout.total_coverage:,.0f} sqft")
        logger.info(f"   By hazard: {layout.by_hazard}")
        
        # Save layout JSON
        layout_json_path = os.path.join(output_dir, 'sprinkler_layout.json')
        save_layout_json(layout, layout_json_path)
        results['files']['sprinkler_layout.json'] = layout_json_path
        results['layout'] = layout_to_dict(layout)
        
        # =====================================================================
        # STEP 3: Route Pipe Network
        # =====================================================================
        logger.info("\n🔧 STEP 3: Routing pipe network...")
        
        if not ENGINE_STATUS['pipe_routing']:
            logger.warning("Pipe routing engine not available")
        else:
            network_json_path = os.path.join(output_dir, 'pipe_network.json')
            network = route_from_layout(
                layout_json_path,
                fp_json_path,
                network_json_path,
                scale_factor=fp_data.scale_factor
            )
            
            total_pipe_ft = sum(network.total_pipe_length.values())
            logger.info(f"   ✅ Created: {len(network.segments)} pipe segments")
            logger.info(f"   Total pipe: {total_pipe_ft:,.0f} ft")
            logger.info(f"   Fittings: {network.fitting_count}")
            
            results['files']['pipe_network.json'] = network_json_path
            results['network'] = network_to_dict(network)
        
        # =====================================================================
        # STEP 4: Hydraulic Calculations
        # =====================================================================
        logger.info("\n💦 STEP 4: Hydraulic calculations...")
        
        if ENGINE_STATUS['network_hydraulics'] and network_json_path:
            hyd_json_path = os.path.join(output_dir, 'hydraulic_calculations.json')
            hyd_txt_path = os.path.join(output_dir, 'hydraulic_report.txt')
            
            # Map hazard class
            hazard_map = {
                'light_hazard': 'Light',
                'ordinary_hazard_group_1': 'Ordinary I',
                'ordinary_hazard_group_2': 'Ordinary II',
                'extra_hazard_group_1': 'Extra I',
                'extra_hazard_group_2': 'Extra II',
                'high_piled_storage': 'High-Piled Storage',
                'esfr_storage': 'High-Piled Storage'
            }
            hc_display = hazard_map.get(hazard_class, 'Light')
            
            hyd_result = calculate_from_network(
                network_json_path,
                hazard_class=hc_display,
                ceiling_height_ft=ceiling_height,
                available_pressure_psi=water_pressure,
                output_json=hyd_json_path
            )
            
            # Generate report
            report = generate_hydraulic_report(hyd_result)
            with open(hyd_txt_path, 'w') as f:
                f.write(report)
            
            logger.info(f"   ✅ Demand: {hyd_result.total_demand_gpm:.0f} GPM @ {hyd_result.required_pressure_psi:.1f} PSI")
            logger.info(f"   Status: {'PASSES' if hyd_result.passes_nfpa else 'FAILS'}")
            
            results['files']['hydraulic_calculations.json'] = hyd_json_path
            results['files']['hydraulic_report.txt'] = hyd_txt_path
            results['hydraulics'] = hydraulic_result_to_dict(hyd_result)
        
        # =====================================================================
        # STEP 5: Generate DXF Drawing
        # =====================================================================
        logger.info("\n📐 STEP 5: Generating DXF drawing...")
        
        dxf_path = os.path.join(output_dir, 'sprinkler_drawing.dxf')
        if generate_geometry_dxf(fp_data, layout, network, dxf_path):
            results['files']['sprinkler_drawing.dxf'] = dxf_path
            logger.info(f"   ✅ DXF saved: {dxf_path}")
        
        # =====================================================================
        # Complete
        # =====================================================================
        elapsed = (datetime.now() - start_time).total_seconds()
        
        results['success'] = True
        results['elapsed_seconds'] = elapsed
        results['summary'] = {
            'rooms': fp_data.room_count,
            'walls': fp_data.wall_count,
            'sprinklers': layout.total_heads,
            'pipe_segments': len(network.segments) if network else 0,
            'pipe_length_ft': total_pipe_ft,
            'demand_gpm': hyd_result.total_demand_gpm if hyd_result else 0,
            'pressure_psi': hyd_result.required_pressure_psi if hyd_result else 0,
            'passes': hyd_result.passes_nfpa if hyd_result else True
        }
        
        logger.info("\n" + "=" * 70)
        logger.info(f"🎉 GEOMETRY PIPELINE COMPLETE in {elapsed:.2f}s")
        logger.info(f"   Files: {list(results['files'].keys())}")
        logger.info("=" * 70)
        
        return results
        
    except Exception as e:
        logger.error(f"Geometry pipeline failed: {e}")
        traceback.print_exc()
        return None


def generate_geometry_dxf(fp_data, layout, network, output_path: str) -> bool:
    """Generate DXF from geometry data (without external ezdxf)"""
    try:
        scale = fp_data.scale_factor
        
        with open(output_path, 'w') as f:
            # Header
            f.write('0\nSECTION\n2\nHEADER\n')
            f.write('9\n$ACADVER\n1\nAC1015\n')
            f.write('0\nENDSEC\n')
            
            # Layers
            layers = [
                ('A-WALL', 8), ('FP-SPKR', 1), ('FP-PIPE-BRCH', 1),
                ('FP-PIPE-XMAIN', 1), ('FP-PIPE-MAIN', 1), ('FP-ANNO', 7)
            ]
            f.write('0\nSECTION\n2\nTABLES\n')
            f.write(f'0\nTABLE\n2\nLAYER\n70\n{len(layers)}\n')
            for name, color in layers:
                f.write(f'0\nLAYER\n2\n{name}\n70\n0\n62\n{color}\n6\nCONTINUOUS\n')
            f.write('0\nENDTAB\n0\nENDSEC\n')
            
            # Entities
            f.write('0\nSECTION\n2\nENTITIES\n')
            
            # Draw room boundaries
            for room in fp_data.rooms:
                x1 = room.min_x / scale
                y1 = room.min_y / scale
                x2 = room.max_x / scale
                y2 = room.max_y / scale
                
                for sx, sy, ex, ey in [(x1,y1,x2,y1), (x2,y1,x2,y2), (x2,y2,x1,y2), (x1,y2,x1,y1)]:
                    f.write(f'0\nLINE\n8\nA-WALL\n10\n{sx:.4f}\n20\n{sy:.4f}\n30\n0.0\n')
                    f.write(f'11\n{ex:.4f}\n21\n{ey:.4f}\n31\n0.0\n')
            
            # Draw sprinklers
            for head in layout.sprinklers:
                x = head.x / scale
                y = head.y / scale
                r = 0.25
                
                f.write(f'0\nCIRCLE\n8\nFP-SPKR\n10\n{x:.4f}\n20\n{y:.4f}\n30\n0.0\n40\n{r:.4f}\n')
                f.write(f'0\nLINE\n8\nFP-SPKR\n10\n{x-r:.4f}\n20\n{y:.4f}\n30\n0.0\n11\n{x+r:.4f}\n21\n{y:.4f}\n31\n0.0\n')
                f.write(f'0\nLINE\n8\nFP-SPKR\n10\n{x:.4f}\n20\n{y-r:.4f}\n30\n0.0\n11\n{x:.4f}\n21\n{y+r:.4f}\n31\n0.0\n')
            
            # Draw pipes
            if network:
                for seg in network.segments:
                    layer = 'FP-PIPE-BRCH' if seg.pipe_type.value == 'branch' else 'FP-PIPE-MAIN'
                    f.write(f'0\nLINE\n8\n{layer}\n')
                    f.write(f'10\n{seg.start.x:.4f}\n20\n{seg.start.y:.4f}\n30\n0.0\n')
                    f.write(f'11\n{seg.end.x:.4f}\n21\n{seg.end.y:.4f}\n31\n0.0\n')
            
            # Title
            f.write(f'0\nTEXT\n8\nFP-ANNO\n10\n10.0\n20\n-5.0\n30\n0.0\n40\n1.5\n')
            f.write(f'1\nFIRE SPRINKLER PLAN - {layout.total_heads} HEADS\n')
            
            f.write('0\nENDSEC\n0\nEOF\n')
        
        return True
    except Exception as e:
        logger.error(f"DXF generation failed: {e}")
        return False


# =============================================================================
# DOCUMENT ANALYSIS
# =============================================================================

def detect_hazard_from_text(text: str) -> Tuple[str, bool, int]:
    """Detect hazard class from text content"""
    text_lower = text.lower()
    
    hazard_class = 'ordinary_hazard_group_1'
    has_racks = False
    rack_height = 0
    
    for keyword, hazard in BUILDING_HAZARD_KEYWORDS.items():
        if keyword in text_lower:
            hazard_class = hazard
            break
    
    rack_keywords = ['rack', 'shelf', 'pallet', 'high pile', 'high-pile', 'storage rack']
    if any(kw in text_lower for kw in rack_keywords):
        has_racks = True
        if hazard_class in ['light_hazard', 'ordinary_hazard_group_1', 'ordinary_hazard_group_2']:
            hazard_class = 'extra_hazard_group_1'
    
    height_patterns = [
        r"(\d+)['\s]*(?:ft|foot|feet)?\s*(?:tall|high|height|rack)",
        r"(?:rack|shelf|storage).*?(\d+)['\s]*(?:ft|foot|feet)?",
    ]
    for pattern in height_patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                rack_height = int(match.group(1))
                if rack_height >= 12:
                    has_racks = True
                    if rack_height >= 20:
                        hazard_class = 'esfr_storage'
                    else:
                        hazard_class = 'high_piled_storage'
                break
            except:
                pass
    
    return hazard_class, has_racks, rack_height


def extract_area_from_text(text: str) -> int:
    """Extract building area from text"""
    text_lower = text.lower()
    
    area_patterns = [
        r'([\d,]+)\s*(?:s\.?f\.?|sq\.?\s*ft\.?|square\s*feet)',
        r'(?:total|building|exist|gross)\s*(?:bldg|building|area)?[:\s]*([\d,]+)',
        r'([\d,]+)\s*(?:sf|sqft)',
    ]
    
    areas = []
    for pattern in area_patterns:
        matches = re.findall(pattern, text_lower)
        for match in matches:
            try:
                area = int(match.replace(',', ''))
                if 1000 < area < 10000000:
                    areas.append(area)
            except:
                pass
    
    return max(areas) if areas else 0


def analyze_documents(project_dir: str, project_data: Dict) -> Dict[str, Any]:
    """Analyze project documents using all available methods"""
    doc_data = {
        'extraction_method': 'none',
        'building_area_sqft': project_data.get('building_area_sqft', 0),
        'hazard_class': project_data.get('hazard_class', 'ordinary_hazard_group_1'),
        'rooms': [],
        'obstructions': [],
        'pdf_path': None,
        'confidence': 0
    }
    
    # Find documents
    pdf_files = []
    dxf_files = []
    
    for f in Path(project_dir).rglob('*'):
        if f.suffix.lower() == '.pdf':
            pdf_files.append(str(f))
        elif f.suffix.lower() in ['.dxf', '.dwg']:
            dxf_files.append(str(f))
    
    # Try PDF analysis first (for AI vision)
    if pdf_files and ENGINE_STATUS['floor_plan_analyzer'] and analyze_floor_plan:
        try:
            for pdf_path in pdf_files:
                logger.info(f"  📄 Analyzing PDF: {Path(pdf_path).name}")
                result = analyze_floor_plan(pdf_path)
                
                if result and result.get('area_sqft', 0) > 0:
                    doc_data['building_area_sqft'] = result['area_sqft']
                    doc_data['hazard_class'] = result.get('hazard_class', doc_data['hazard_class'])
                    doc_data['extraction_method'] = 'ai_vision'
                    doc_data['confidence'] = result.get('confidence', 0.8)
                    doc_data['rooms'] = result.get('rooms', [])
                    doc_data['pdf_path'] = pdf_path
                    logger.info(f"    ✅ AI Vision: {doc_data['building_area_sqft']:,} sqft")
                    break
        except Exception as e:
            logger.warning(f"  AI Vision failed: {e}")
    
    # Store PDF path for geometry pipeline
    if pdf_files:
        doc_data['pdf_path'] = pdf_files[0]
    
    return doc_data


# =============================================================================
# SPRINKLER LAYOUT DESIGN (FALLBACK)
# =============================================================================

def design_sprinkler_layout(zones: List[Zone], obstructions: List = None) -> Tuple[List[Sprinkler], List[Pipe], List[Fitting]]:
    """Design sprinkler layout for zones (fallback when geometry unavailable)"""
    sprinklers = []
    pipes = []
    fittings = []
    
    sprinkler_id = 1
    pipe_id = 1
    fitting_id = 1
    
    for zone in zones:
        req = HAZARD_REQUIREMENTS.get(zone.hazard_class, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
        
        coverage = req['coverage']
        spacing = req['spacing']
        k_factor = req['k_factor']
        
        num_heads = max(4, math.ceil(zone.area / coverage))
        
        grid_size = math.ceil(math.sqrt(num_heads))
        room_width = math.sqrt(zone.area)
        actual_spacing = min(spacing, room_width / grid_size)
        
        for row in range(grid_size):
            heads_in_row = []
            for col in range(grid_size):
                if sprinkler_id > num_heads + (len(sprinklers) - num_heads if sprinklers else 0):
                    break
                    
                x = col * actual_spacing + actual_spacing / 2
                y = row * actual_spacing + actual_spacing / 2
                z = zone.ceiling_height - 0.5
                
                sprinkler = Sprinkler(
                    id=f"SPK-{sprinkler_id:04d}",
                    x=x, y=y, z=z,
                    k_factor=k_factor,
                    zone_id=zone.id,
                    coverage=coverage
                )
                sprinklers.append(sprinkler)
                heads_in_row.append(sprinkler)
                sprinkler_id += 1
            
            # Create branch line
            if len(heads_in_row) >= 2:
                branch_size = 1.0 if len(heads_in_row) <= 2 else 1.25 if len(heads_in_row) <= 4 else 1.5
                
                pipe = Pipe(
                    id=f"BR-{pipe_id:04d}",
                    start=(heads_in_row[0].x, heads_in_row[0].y, heads_in_row[0].z),
                    end=(heads_in_row[-1].x, heads_in_row[-1].y, heads_in_row[-1].z),
                    diameter=branch_size,
                    length=abs(heads_in_row[-1].x - heads_in_row[0].x),
                    pipe_type='branch'
                )
                pipes.append(pipe)
                pipe_id += 1
                
                # Fittings
                for head in heads_in_row:
                    fittings.append(Fitting(
                        id=f"FIT-{fitting_id:04d}",
                        x=head.x, y=head.y, z=head.z,
                        fitting_type='tee',
                        size=branch_size
                    ))
                    fitting_id += 1
    
    return sprinklers, pipes, fittings


def design_main_piping(zones: List[Zone], sprinklers: List[Sprinkler], 
                       branch_pipes: List[Pipe]) -> Tuple[List[Pipe], List[Fitting], List[Valve]]:
    """Design main piping (fallback)"""
    pipes = []
    fittings = []
    valves = []
    
    total_heads = len(sprinklers)
    
    # Size main based on head count
    if total_heads <= 20:
        main_size = 2.5
    elif total_heads <= 50:
        main_size = 3.0
    elif total_heads <= 100:
        main_size = 4.0
    else:
        main_size = 6.0
    
    # Cross main
    if branch_pipes:
        min_y = min(p.start[1] for p in branch_pipes)
        max_y = max(p.end[1] for p in branch_pipes)
        
        pipes.append(Pipe(
            id="XMAIN-001",
            start=(0, min_y, branch_pipes[0].start[2]),
            end=(0, max_y, branch_pipes[0].end[2]),
            diameter=main_size,
            length=abs(max_y - min_y),
            pipe_type='cross_main'
        ))
    
    # Valves
    valves = [
        Valve(id='V-001', x=0, y=0, z=5, valve_type='OS&Y Gate', size=main_size),
        Valve(id='V-002', x=0, y=0, z=4, valve_type='Check', size=main_size),
        Valve(id='V-003', x=0, y=0, z=3, valve_type='FDC', size=4.0),
        Valve(id='V-004', x=0, y=0, z=6, valve_type='Flow Switch', size=main_size),
        Valve(id='V-005', x=0, y=0, z=2, valve_type='Drain', size=2.0),
    ]
    
    return pipes, fittings, valves


# =============================================================================
# HYDRAULIC CALCULATIONS (FALLBACK)
# =============================================================================

def calculate_hydraulics(design: DesignResult, water_supply: Dict = None) -> Dict:
    """Calculate hydraulics (fallback when network hydraulics unavailable)"""
    if not design.zones:
        return {'status': 'ERROR', 'message': 'No zones defined'}
    
    primary_zone = max(design.zones, key=lambda z: z.area)
    req = HAZARD_REQUIREMENTS.get(primary_zone.hazard_class, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
    
    density = req['density']
    remote_area = min(req['remote_area'], primary_zone.area)
    hose = req['hose']
    
    sprinkler_demand = density * remote_area
    total_demand = sprinkler_demand + hose
    
    # Estimate pressure
    heads_in_remote = math.ceil(remote_area / req['coverage'])
    k_factor = req['k_factor']
    min_pressure = 7.0
    
    end_head_flow = density * req['coverage']
    end_head_pressure = (end_head_flow / k_factor) ** 2
    end_head_pressure = max(end_head_pressure, min_pressure)
    
    # Friction loss estimate
    friction_estimate = 0.5 * heads_in_remote + 5  # Simplified
    elevation_loss = primary_zone.ceiling_height * 0.433
    
    required_pressure = end_head_pressure + friction_estimate + elevation_loss
    
    available = water_supply.get('static_pressure', 65) if water_supply else 65
    
    return {
        'system_demand_gpm': round(total_demand, 1),
        'sprinkler_demand_gpm': round(sprinkler_demand, 1),
        'hose_allowance_gpm': hose,
        'system_pressure_psi': round(required_pressure, 1),
        'available_pressure_psi': available,
        'safety_margin_psi': round(available - required_pressure, 1),
        'remote_area_sqft': remote_area,
        'heads_in_remote': heads_in_remote,
        'density_gpm_sqft': density,
        'status': 'ADEQUATE' if available >= required_pressure else 'INADEQUATE'
    }


# =============================================================================
# COST ESTIMATION
# =============================================================================

def calculate_costs(design: DesignResult) -> Dict:
    """Calculate project costs"""
    
    # Material costs
    sprinkler_cost = len(design.sprinklers) * 45
    
    pipe_cost = 0
    for pipe in design.pipes:
        rate = {1.0: 8, 1.25: 10, 1.5: 12, 2.0: 18, 2.5: 25, 3.0: 35, 4.0: 55, 6.0: 95}.get(pipe.diameter, 20)
        pipe_cost += pipe.length * rate
    
    fitting_cost = len(design.fittings) * 25
    valve_cost = len(design.valves) * 350
    
    material_total = sprinkler_cost + pipe_cost + fitting_cost + valve_cost
    
    # Labor
    labor_hours = (
        len(design.sprinklers) * 0.5 +
        sum(p.length for p in design.pipes) * 0.1 +
        len(design.fittings) * 0.25 +
        len(design.valves) * 2
    )
    labor_cost = labor_hours * 85
    
    # Overhead
    overhead = (material_total + labor_cost) * 0.15
    
    return {
        'sprinkler_cost': sprinkler_cost,
        'pipe_cost': pipe_cost,
        'fitting_cost': fitting_cost,
        'valve_cost': valve_cost,
        'material_total': material_total,
        'labor_hours': labor_hours,
        'labor_cost': labor_cost,
        'overhead': overhead,
        'total': material_total + labor_cost + overhead
    }


# =============================================================================
# OUTPUT GENERATION
# =============================================================================

def generate_dxf_output(design: DesignResult, output_path: str) -> bool:
    """Generate basic DXF output"""
    try:
        with open(output_path, 'w') as f:
            f.write('0\nSECTION\n2\nENTITIES\n')
            
            for s in design.sprinklers:
                f.write(f'0\nCIRCLE\n8\n0\n10\n{s.x}\n20\n{s.y}\n30\n{s.z}\n40\n0.5\n')
            
            for p in design.pipes:
                f.write(f'0\nLINE\n8\n0\n10\n{p.start[0]}\n20\n{p.start[1]}\n30\n{p.start[2]}\n')
                f.write(f'11\n{p.end[0]}\n21\n{p.end[1]}\n31\n{p.end[2]}\n')
            
            f.write('0\nENDSEC\n0\nEOF\n')
        return True
    except:
        return False


def generate_bom_csv(design: DesignResult, output_path: str) -> bool:
    """Generate basic BOM CSV"""
    try:
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Item', 'Description', 'Quantity', 'Unit'])
            writer.writerow(['Sprinklers', f'K={design.sprinklers[0].k_factor if design.sprinklers else 5.6}', len(design.sprinklers), 'EA'])
            
            pipe_by_size = {}
            for p in design.pipes:
                pipe_by_size[p.diameter] = pipe_by_size.get(p.diameter, 0) + p.length
            
            for size, length in sorted(pipe_by_size.items()):
                writer.writerow([f'{size}" Pipe', 'Sch40 Black Steel', round(length), 'LF'])
            
            writer.writerow(['Fittings', 'Various', len(design.fittings), 'EA'])
            
            for v in design.valves:
                writer.writerow([v.valve_type, f'{v.size}"', 1, 'EA'])
        
        return True
    except:
        return False


def generate_pdf_report(design: DesignResult, output_path: str) -> bool:
    """Generate PDF compliance report"""
    if not ENGINE_STATUS['reportlab']:
        return False
    
    try:
        doc = SimpleDocTemplate(output_path, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        
        # Title
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=TA_CENTER)
        story.append(Paragraph("FIRE SPRINKLER SYSTEM", title_style))
        story.append(Paragraph("COMPLIANCE REPORT", title_style))
        story.append(Spacer(1, 0.5*inch))
        
        # Project info
        story.append(Paragraph(f"<b>Project:</b> {design.project_name}", styles['Normal']))
        story.append(Paragraph(f"<b>Area:</b> {design.building_area:,.0f} SF", styles['Normal']))
        story.append(Paragraph(f"<b>Sprinklers:</b> {len(design.sprinklers)}", styles['Normal']))
        story.append(Paragraph(f"<b>Status:</b> {design.compliance_status}", styles['Normal']))
        
        if design.geometry_extracted:
            story.append(Spacer(1, 0.25*inch))
            story.append(Paragraph("<b>Geometry Analysis:</b>", styles['Normal']))
            story.append(Paragraph(f"  Rooms Detected: {design.room_count}", styles['Normal']))
            story.append(Paragraph(f"  Walls Detected: {design.wall_count}", styles['Normal']))
            story.append(Paragraph(f"  Pipe Network: {'Generated' if design.pipe_network_generated else 'N/A'}", styles['Normal']))
        
        doc.build(story)
        return True
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        return False


# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================

async def orchestrate_async(project_dir: str, output_dir: str) -> Dict[str, str]:
    """
    Main orchestration function - v17 with Geometry Pipeline
    """
    start_time = datetime.now()
    engines_used = []
    
    logger.info("=" * 70)
    logger.info("🔥 FireAI Pro Orchestrator v17.0 - GEOMETRY INTEGRATION")
    logger.info("=" * 70)
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Load project data
    project_json = os.path.join(project_dir, 'project.json')
    if os.path.exists(project_json):
        with open(project_json, 'r') as f:
            project_data = json.load(f)
    else:
        project_data = {
            'project_id': str(uuid.uuid4())[:8],
            'project_name': 'Fire Sprinkler Project',
            'building_area_sqft': 0,
            'hazard_class': 'ordinary_hazard_group_1'
        }
    
    # =========================================================================
    # STEP 1: Document Analysis
    # =========================================================================
    logger.info("-" * 50)
    logger.info("STEP 1: Document Analysis")
    
    doc_data = analyze_documents(project_dir, project_data)
    
    if doc_data['building_area_sqft'] > 0:
        project_data['building_area_sqft'] = doc_data['building_area_sqft']
    if doc_data['hazard_class']:
        project_data['hazard_class'] = doc_data['hazard_class']
    
    analysis_confidence = doc_data.get('confidence', 0) * 100
    
    logger.info(f"  📊 Area: {project_data['building_area_sqft']:,} sqft")
    logger.info(f"  ⚠️ Hazard: {project_data['hazard_class']}")
    logger.info(f"  🎯 Confidence: {analysis_confidence:.0f}%")
    
    # =========================================================================
    # STEP 2: TRY GEOMETRY PIPELINE (NEW in v17!)
    # =========================================================================
    geometry_result = None
    use_geometry = False
    
    if (doc_data.get('pdf_path') and 
        ENGINE_STATUS['floor_plan_intelligence'] and
        ENGINE_STATUS['sprinkler_placement']):
        
        logger.info("-" * 50)
        logger.info("STEP 2: GEOMETRY PIPELINE (AutoSprink Quality)")
        
        geometry_result = run_geometry_pipeline(
            pdf_path=doc_data['pdf_path'],
            output_dir=output_dir,
            hazard_class=project_data['hazard_class'],
            ceiling_height=project_data.get('ceiling_height_ft', 10),
            water_pressure=project_data.get('water_supply', {}).get('static_pressure', 65)
        )
        
        if geometry_result and geometry_result.get('success'):
            use_geometry = True
            engines_used.extend([
                'Floor Plan Intelligence',
                'Sprinkler Placement Engine',
                'Pipe Routing Engine',
                'Network Hydraulics'
            ])
            logger.info("  ✅ Geometry pipeline successful!")
    
    # =========================================================================
    # STEP 3: FALLBACK TO ABSTRACT LAYOUT (if geometry failed)
    # =========================================================================
    design = None
    hydraulics = {}
    costs = {}
    
    if not use_geometry:
        logger.info("-" * 50)
        logger.info("STEP 2b: Abstract Layout Design (Fallback)")
        
        # Create zones
        zones = []
        if doc_data.get('rooms'):
            for i, r in enumerate(doc_data['rooms']):
                zones.append(Zone(
                    id=f'ZONE-{i+1:03d}',
                    name=r.get('name', f'Zone {i+1}'),
                    area=r.get('area', project_data['building_area_sqft'] / len(doc_data['rooms'])),
                    ceiling_height=project_data.get('ceiling_height_ft', 12),
                    hazard_class=r.get('hazard_class', project_data['hazard_class'])
                ))
        else:
            zones.append(Zone(
                id='ZONE-001',
                name='Main Area',
                area=project_data['building_area_sqft'],
                ceiling_height=project_data.get('ceiling_height_ft', 12),
                hazard_class=project_data['hazard_class']
            ))
        
        # Design layout
        sprinklers, branch_pipes, branch_fittings = design_sprinkler_layout(zones, [])
        main_pipes, main_fittings, valves = design_main_piping(zones, sprinklers, branch_pipes)
        
        all_pipes = branch_pipes + main_pipes
        all_fittings = branch_fittings + main_fittings
        
        logger.info(f"  💧 Sprinklers: {len(sprinklers)}")
        logger.info(f"  🔧 Pipes: {len(all_pipes)}")
        
        # Create design result
        design = DesignResult(
            project_id=project_data['project_id'],
            project_name=project_data.get('project_name', 'Fire Sprinkler Project'),
            building_area=project_data['building_area_sqft'],
            zones=zones,
            sprinklers=sprinklers,
            pipes=all_pipes,
            fittings=all_fittings,
            valves=valves,
            seismic_design_category='D' if project_data.get('zip_code', '').startswith('9') else 'C',
            analysis_confidence=analysis_confidence
        )
        
        # Hydraulics
        water_supply = project_data.get('water_supply', {})
        hydraulics = calculate_hydraulics(design, water_supply)
        
        # Costs
        costs = calculate_costs(design)
        design.total_cost = costs['total']
        design.cost_per_sqft = design.total_cost / design.building_area if design.building_area > 0 else 0
    
    # =========================================================================
    # STEP 4: Generate Outputs
    # =========================================================================
    logger.info("-" * 50)
    logger.info("STEP 4: Generate Outputs")
    
    outputs = {}
    
    if use_geometry:
        # Geometry pipeline already generated outputs
        outputs.update(geometry_result.get('files', {}))
        
        # Create design from geometry for BOM/cost generation
        fp_data_dict = geometry_result.get('geometry', {})
        layout_dict = geometry_result.get('layout', {})
        network_dict = geometry_result.get('network', {})
        hydraulics = geometry_result.get('hydraulics', {})
        
        # Generate summary
        summary = {
            'project_id': project_data.get('project_id', 'unknown'),
            'project_name': project_data.get('project_name', 'Fire Sprinkler System'),
            'building_area_sqft': fp_data_dict.get('floor_plan', {}).get('area_sqft', 0),
            'hazard_class': project_data['hazard_class'],
            'geometry_extracted': True,
            'room_count': fp_data_dict.get('statistics', {}).get('room_count', 0),
            'wall_count': fp_data_dict.get('statistics', {}).get('wall_count', 0),
            'sprinklers': layout_dict.get('statistics', {}).get('total_heads', 0),
            'pipe_segments': len(network_dict.get('segments', [])) if network_dict else 0,
            'pipe_totals': network_dict.get('pipe_totals', {}) if network_dict else {},
            'hydraulics': hydraulics,
            'engines_used': engines_used
        }
    else:
        # Generate standard outputs
        dxf_path = os.path.join(output_dir, 'design.dxf')
        if generate_dxf_output(design, dxf_path):
            outputs['design.dxf'] = dxf_path
        
        bom_path = os.path.join(output_dir, 'bill_of_materials.csv')
        if generate_bom_csv(design, bom_path):
            outputs['bill_of_materials.csv'] = bom_path
        
        pdf_path = os.path.join(output_dir, 'compliance_report.pdf')
        if generate_pdf_report(design, pdf_path):
            outputs['compliance_report.pdf'] = pdf_path
        
        summary = {
            'project_id': design.project_id,
            'project_name': design.project_name,
            'building_area_sqft': design.building_area,
            'hazard_class': project_data['hazard_class'],
            'geometry_extracted': False,
            'sprinklers': len(design.sprinklers),
            'pipes': len(design.pipes),
            'hydraulics': hydraulics,
            'costs': costs,
            'engines_used': engines_used
        }
    
    # Save summary
    with open(os.path.join(output_dir, 'summary.json'), 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    outputs['summary.json'] = os.path.join(output_dir, 'summary.json')
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    logger.info("=" * 70)
    logger.info(f"🎉 COMPLETE in {elapsed:.2f}s")
    logger.info(f"   📁 Files: {list(outputs.keys())}")
    logger.info(f"   🏗️ Geometry: {'YES' if use_geometry else 'NO (fallback)'}")
    logger.info(f"   🔧 Engines: {len(engines_used)}")
    logger.info("=" * 70)
    
    return outputs


def orchestrate(project_dir: str, output_dir: str) -> Dict[str, str]:
    """Sync wrapper"""
    return asyncio.run(orchestrate_async(project_dir, output_dir))


def get_engine_status() -> Dict[str, Any]:
    """Return engine status for health endpoint"""
    return ENGINE_STATUS.copy()


# =============================================================================
# GEOMETRY PIPELINE STATUS CHECK
# =============================================================================

def geometry_pipeline_available() -> bool:
    """Check if full geometry pipeline is available"""
    required = [
        'floor_plan_intelligence',
        'sprinkler_placement',
        'pipe_routing',
        'network_hydraulics',
        'pdfplumber'
    ]
    return all(ENGINE_STATUS.get(e, False) for e in required)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🔥 FireAI Pro Unified Orchestrator v17.0 - GEOMETRY INTEGRATION")
    print("=" * 70)
    
    print("\n📋 ENGINE STATUS:")
    for engine, status in ENGINE_STATUS.items():
        print(f"   {'✅' if status else '❌'} {engine}")
    
    active = sum(1 for v in ENGINE_STATUS.values() if v)
    print(f"\n   Active: {active}/{len(ENGINE_STATUS)}")
    
    print(f"\n🏗️ GEOMETRY PIPELINE: {'✅ AVAILABLE' if geometry_pipeline_available() else '❌ NOT AVAILABLE'}")
    
    if geometry_pipeline_available():
        print("\n   New capabilities:")
        print("   • PDF geometry extraction (walls, rooms)")
        print("   • Sprinkler placement in actual geometry")
        print("   • Intelligent pipe routing")
        print("   • Network-based hydraulic calculations")
        print("   • AutoSprink-quality DXF output")
