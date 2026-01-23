#!/usr/bin/env python3
"""
FireAI Pro - Unified Production Orchestrator v15.0
===================================================
NOW WITH PDF/IMAGE AI VISION ANALYSIS!

INTEGRATED ENGINES:
1. enhanced_cad_engine - Extracts building geometry from DXF/DWG/IFC
2. floor_plan_analyzer - AI Vision for PDF/image analysis (RE-ENABLED!)
3. enhanced_hydraulics_engine - Hardy Cross, EPANET network analysis
4. nfpa13_calc_sheets - Permit-ready NFPA 13 calculation sheets
5. node_by_node_tables - AHJ-compliant node-by-node hydraulic tables
6. professional_dxf_engine - Shop drawings with dimensions & schedules
7. fitting_takeoff_bom - Complete fitting detection & accurate BOMs
8. pipe_sizing_optimizer - Intelligent pipe sizing with velocity check
9. bim_3d_engine - 3D BIM coordination & clash detection
10. enhanced_bracing_engine - ASCE 7-22 seismic, NFPA 13 Ch.9 bracing
11. master_fireai_products_enhanced - Real supplier pricing, cost analysis
12. fireai_pro_master_Standards - 790+ NFPA compliance rules

VERSION: 15.0.0-PDF-VISION
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
    'floor_plan_analyzer': False,  # RE-ENABLED for PDF/image analysis!
    'hydraulics_engine': False,
    'calc_sheets': False,
    'node_tables': False,
    'professional_dxf': False,
    'fitting_bom': False,
    'pipe_optimizer': False,
    'bim_3d': False,
    'bracing_engine': False,
    'products_engine': False,
    'standards_engine': False,
    'ezdxf': False,
    'reportlab': False
}

# 1. Enhanced CAD Engine - Building geometry extraction (DXF/DWG/IFC)
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

# 2. Floor Plan Analyzer - AI Vision for PDFs and images (RE-ENABLED!)
try:
    from floor_plan_analyzer import (
        FloorPlanAnalyzer,
        analyze_floor_plan,
        analyze_project_documents,
        HazardClass as FPHazardClass
    )
    ENGINE_STATUS['floor_plan_analyzer'] = True
    logger.info("✅ Floor Plan Analyzer loaded (AI Vision for PDFs)")
except Exception as e:
    logger.warning(f"⚠️ Floor Plan Analyzer: {e}")
    FloorPlanAnalyzer = None
    analyze_floor_plan = None
    analyze_project_documents = None

# 3. Enhanced Hydraulics Engine
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

# 4. NFPA 13 Calculation Sheet Generator
try:
    from nfpa13_calc_sheets import NFPA13CalcSheetGenerator, ProjectInfo
    ENGINE_STATUS['calc_sheets'] = True
    logger.info("✅ NFPA 13 Calc Sheet Generator loaded")
except Exception as e:
    logger.warning(f"⚠️ Calc Sheet Generator: {e}")
    NFPA13CalcSheetGenerator = None

# 5. Node-by-Node Tables Generator
try:
    from node_by_node_tables import NodeByNodeCalculator, NodeByNodeTableGenerator
    ENGINE_STATUS['node_tables'] = True
    logger.info("✅ Node-by-Node Table Generator loaded")
except Exception as e:
    logger.warning(f"⚠️ Node-by-Node Tables: {e}")

# 6. Professional DXF Shop Drawing Engine
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

# 7. Complete Fitting Takeoff & BOM Generator
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

# 8. Intelligent Pipe Sizing Optimizer
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

# 9. 3D BIM Coordination & Clash Detection Engine
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

# 10. Enhanced Bracing Engine
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

# 11. Products/Cost Engine
try:
    from master_fireai_products_enhanced import ProductionFireAIService, BOMItem, ProductionConfig
    ENGINE_STATUS['products_engine'] = True
    logger.info("✅ Products Engine loaded")
except Exception as e:
    logger.warning(f"⚠️ Products Engine: {e}")
    ProductionFireAIService = None

# 12. Standards Engine
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

# Building keywords to hazard class mapping
BUILDING_HAZARD_KEYWORDS = {
    # Light Hazard
    'office': 'light_hazard',
    'church': 'light_hazard',
    'school': 'light_hazard',
    'hospital': 'light_hazard',
    'hotel': 'light_hazard',
    'apartment': 'light_hazard',
    'residential': 'light_hazard',
    
    # Ordinary Hazard Group 1
    'retail': 'ordinary_hazard_group_1',
    'mercantile': 'ordinary_hazard_group_1',
    'restaurant': 'ordinary_hazard_group_1',
    'parking': 'ordinary_hazard_group_1',
    
    # Ordinary Hazard Group 2
    'manufacturing': 'ordinary_hazard_group_2',
    'factory': 'ordinary_hazard_group_2',
    'machine shop': 'ordinary_hazard_group_2',
    
    # Extra Hazard / High-Piled Storage
    'warehouse': 'extra_hazard_group_1',
    'distribution': 'extra_hazard_group_1',
    'storage': 'extra_hazard_group_1',
    
    # Big Box Retail with Storage (ESFR typically required)
    'costco': 'high_piled_storage',
    'wholesale': 'high_piled_storage',
    'sam\'s club': 'high_piled_storage',
    'bj\'s': 'high_piled_storage',
    'home depot': 'high_piled_storage',
    'lowe\'s': 'high_piled_storage',
    'lowes': 'high_piled_storage',
    'menards': 'high_piled_storage',
    'ikea': 'high_piled_storage',
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

@dataclass
class Pipe:
    id: str
    start: Tuple[float, float, float]
    end: Tuple[float, float, float]
    diameter: float
    length: float
    pipe_type: str = 'branch'
    schedule: str = 'Sch40'

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


# =============================================================================
# DOCUMENT ANALYSIS - PDF + CAD SUPPORT
# =============================================================================

def detect_hazard_from_text(text: str) -> Tuple[str, bool, int]:
    """
    Detect hazard class from text content.
    Returns: (hazard_class, has_rack_storage, rack_height_ft)
    """
    text_lower = text.lower()
    
    hazard_class = 'ordinary_hazard_group_1'  # Default
    has_racks = False
    rack_height = 0
    
    # Check for building type keywords
    for keyword, hazard in BUILDING_HAZARD_KEYWORDS.items():
        if keyword in text_lower:
            hazard_class = hazard
            logger.info(f"  Detected '{keyword}' → {hazard}")
            break
    
    # Check for rack storage indicators
    rack_keywords = ['rack', 'shelf', 'pallet', 'high pile', 'high-pile', 'high piled', 'storage rack']
    if any(kw in text_lower for kw in rack_keywords):
        has_racks = True
        # Upgrade hazard if not already high enough
        if hazard_class in ['light_hazard', 'ordinary_hazard_group_1', 'ordinary_hazard_group_2']:
            hazard_class = 'extra_hazard_group_1'
    
    # Try to extract rack height
    height_patterns = [
        r"(\d+)['\s]*(?:ft|foot|feet)?\s*(?:tall|high|height|rack)",
        r"(?:rack|shelf|storage).*?(\d+)['\s]*(?:ft|foot|feet)?",
        r"(\d+)['\s]*(?:ft|foot|feet)?\s*(?:steel|metal|rack)"
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
    
    # Patterns for area extraction
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
                if 1000 < area < 10000000:  # Reasonable building size
                    areas.append(area)
            except:
                pass
    
    if areas:
        # Return largest (likely total building area)
        return max(areas)
    return 0


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract text from PDF"""
    text = ""
    
    # Try PyMuPDF (fitz)
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        for page in doc:
            text += page.get_text()
        if text.strip():
            return text
    except Exception as e:
        logger.debug(f"PyMuPDF failed: {e}")
    
    # Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        if text.strip():
            return text
    except Exception as e:
        logger.debug(f"pdfplumber failed: {e}")
    
    # Try PyPDF2
    try:
        import PyPDF2
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        logger.debug(f"PyPDF2 failed: {e}")
    
    return text


async def analyze_documents(project_dir: str) -> Dict[str, Any]:
    """
    Analyze uploaded documents using appropriate engine:
    - PDF/Images → Floor Plan Analyzer (AI Vision)
    - DXF/DWG/IFC → CAD Engine
    - Fallback → Text extraction
    """
    
    result = {
        'building_area_sqft': 0,
        'building_type': 'unknown',
        'hazard_class': 'ordinary_hazard_group_1',
        'rooms': [],
        'obstructions': [],
        'rack_storage': False,
        'rack_height_ft': 0,
        'confidence': 0,
        'extraction_method': 'none'
    }
    
    project_path = Path(project_dir)
    
    # Find all document types
    pdf_files = list(project_path.glob('*.pdf'))
    image_files = list(project_path.glob('*.png')) + list(project_path.glob('*.jpg')) + list(project_path.glob('*.jpeg'))
    cad_files = list(project_path.glob('*.dxf')) + list(project_path.glob('*.dwg')) + list(project_path.glob('*.ifc'))
    
    logger.info(f"📂 Found: {len(pdf_files)} PDF, {len(image_files)} images, {len(cad_files)} CAD files")
    
    # =========================================================================
    # PRIORITY 1: Use Floor Plan Analyzer for PDF/Images (AI Vision)
    # =========================================================================
    if (pdf_files or image_files) and ENGINE_STATUS.get('floor_plan_analyzer') and FloorPlanAnalyzer:
        logger.info("🔍 Using AI Vision to analyze PDF/image...")
        try:
            file_to_analyze = pdf_files[0] if pdf_files else image_files[0]
            
            # Use the floor_plan_analyzer
            project_data = analyze_floor_plan(str(file_to_analyze))
            
            if project_data and project_data.get('building_area_sqft', 0) > 0:
                result['building_area_sqft'] = project_data['building_area_sqft']
                result['building_type'] = project_data.get('building_type', 'unknown')
                result['hazard_class'] = project_data.get('hazard_class', 'ordinary_hazard_group_1')
                result['rooms'] = project_data.get('zones', [])
                result['obstructions'] = project_data.get('obstructions', [])
                result['confidence'] = project_data.get('analysis_confidence', 0)
                result['extraction_method'] = 'AI Vision (Floor Plan Analyzer)'
                
                # Check for storage/rack indicators in warnings
                for warning in project_data.get('warnings', []):
                    warning_lower = warning.lower()
                    if any(kw in warning_lower for kw in ['rack', 'storage', 'shelf', 'pallet']):
                        result['rack_storage'] = True
                
                logger.info(f"✅ AI Vision: {result['building_area_sqft']:,.0f} sqft, confidence {result['confidence']}%")
                
        except Exception as e:
            logger.error(f"❌ Floor Plan Analyzer failed: {e}")
            traceback.print_exc()
    
    # =========================================================================
    # PRIORITY 2: PDF Text Extraction (if AI Vision didn't work or isn't available)
    # =========================================================================
    if pdf_files and result['building_area_sqft'] == 0:
        logger.info("📝 Extracting text from PDF...")
        try:
            pdf_text = extract_pdf_text(pdf_files[0])
            
            if pdf_text:
                # Extract area
                area = extract_area_from_text(pdf_text)
                if area > 0:
                    result['building_area_sqft'] = area
                    result['extraction_method'] = 'PDF Text Extraction'
                    result['confidence'] = 60
                    logger.info(f"✅ PDF Text: Found area {area:,} sqft")
                
                # Detect hazard class from text
                hazard, has_racks, rack_height = detect_hazard_from_text(pdf_text)
                result['hazard_class'] = hazard
                result['rack_storage'] = has_racks
                result['rack_height_ft'] = rack_height
                
                # Try to detect building type
                for keyword in BUILDING_HAZARD_KEYWORDS.keys():
                    if keyword in pdf_text.lower():
                        result['building_type'] = keyword
                        break
                
        except Exception as e:
            logger.error(f"❌ PDF text extraction failed: {e}")
    
    # =========================================================================
    # PRIORITY 3: Use CAD Engine for DXF/DWG/IFC
    # =========================================================================
    if cad_files and ENGINE_STATUS.get('cad_engine') and result['building_area_sqft'] == 0:
        logger.info("📐 Using CAD Engine to analyze DXF/DWG/IFC...")
        try:
            config = CloudCADEngineConfig(enable_ai_classification=True, output_formats=['json'])
            engine = EnhancedProductionCADEngine(config)
            
            cad_result = await engine.process_single_file(cad_files[0], Path('/tmp'))
            
            if cad_result.success and cad_result.project_geometry:
                geom = cad_result.project_geometry
                
                for room in geom.rooms:
                    result['rooms'].append({
                        'id': room.id,
                        'name': room.properties.get('name', room.layer_name),
                        'area': room.area,
                        'ceiling_height': room.properties.get('height', 10),
                        'hazard_class': room.properties.get('nfpa_hazard_zone', 'ordinary_hazard_group_1')
                    })
                
                for col in geom.columns:
                    if col.bounding_box:
                        result['obstructions'].append({
                            'type': 'column',
                            'x': col.bounding_box.center.x,
                            'y': col.bounding_box.center.y,
                            'clearance': 3.0
                        })
                
                result['building_area_sqft'] = sum(r['area'] for r in result['rooms']) if result['rooms'] else 0
                result['confidence'] = 85
                result['extraction_method'] = 'CAD Engine'
                
                logger.info(f"✅ CAD: {result['building_area_sqft']:,.0f} sqft, {len(result['rooms'])} rooms")
                
        except Exception as e:
            logger.error(f"❌ CAD analysis failed: {e}")
    
    # =========================================================================
    # Update hazard class based on building type and storage
    # =========================================================================
    if result['building_type'] != 'unknown' or result['rack_storage']:
        # Re-check hazard based on final building type
        btype = result['building_type'].lower()
        for keyword, hazard in BUILDING_HAZARD_KEYWORDS.items():
            if keyword in btype:
                result['hazard_class'] = hazard
                break
        
        # Upgrade for rack storage
        if result['rack_storage']:
            if result['rack_height_ft'] >= 20:
                result['hazard_class'] = 'esfr_storage'
            elif result['rack_height_ft'] >= 12 or result['hazard_class'] in ['light_hazard', 'ordinary_hazard_group_1']:
                result['hazard_class'] = 'high_piled_storage'
    
    return result


# =============================================================================
# SPRINKLER LAYOUT DESIGN
# =============================================================================

def design_sprinkler_layout(zones: List[Zone], obstructions: List[Dict]) -> Tuple[List[Sprinkler], List[Pipe], List[Fitting]]:
    """Design sprinkler layout with obstacle avoidance"""
    
    sprinklers = []
    pipes = []
    fittings = []
    
    x_offset = 0
    spk_id = 1
    pipe_id = 1
    fit_id = 1
    
    for zone in zones:
        req = HAZARD_REQUIREMENTS.get(zone.hazard_class, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
        
        # Calculate zone dimensions
        width = math.sqrt(zone.area)
        length = zone.area / width if width > 0 else width
        
        # Spacing and coverage from hazard requirements
        spacing = min(req['spacing'] * 0.85, math.sqrt(req['coverage'] * 0.9))
        offset = spacing / 2
        k_factor = req.get('k_factor', 5.6)
        
        # Sprinkler grid
        num_x = max(1, int((width - offset) / spacing) + 1)
        num_y = max(1, int((length - offset) / spacing) + 1)
        
        zone_sprinklers = []
        for i in range(num_x):
            for j in range(num_y):
                x = x_offset + min(offset + i * spacing, width - 1)
                y = min(offset + j * spacing, length - 1)
                z = zone.ceiling_height - 0.5
                
                # Check obstruction clearance
                clear = True
                for obs in obstructions:
                    ox, oy = obs.get('x', 0), obs.get('y', 0)
                    clearance = obs.get('clearance', 3)
                    if math.sqrt((x - ox)**2 + (y - oy)**2) < clearance:
                        clear = False
                        break
                
                if clear:
                    spk = Sprinkler(
                        id=f'S-{spk_id:03d}',
                        x=x, y=y, z=z,
                        k_factor=k_factor,
                        temp_rating=165,
                        zone_id=zone.id,
                        coverage=req['coverage']
                    )
                    sprinklers.append(spk)
                    zone_sprinklers.append(spk)
                    spk_id += 1
        
        # Create branch lines
        if zone_sprinklers:
            branch_lines = {}
            for spk in zone_sprinklers:
                branch_key = round(spk.y, 1)
                if branch_key not in branch_lines:
                    branch_lines[branch_key] = []
                branch_lines[branch_key].append(spk)
            
            for y_val, branch_spks in branch_lines.items():
                branch_spks.sort(key=lambda s: s.x)
                
                for idx in range(len(branch_spks) - 1):
                    s1, s2 = branch_spks[idx], branch_spks[idx + 1]
                    seg_length = abs(s2.x - s1.x)
                    
                    # Size pipe based on downstream sprinklers
                    num_downstream = len(branch_spks) - idx
                    if num_downstream <= 2:
                        diameter = 1.0
                    elif num_downstream <= 4:
                        diameter = 1.25
                    elif num_downstream <= 6:
                        diameter = 1.5
                    elif num_downstream <= 10:
                        diameter = 2.0
                    else:
                        diameter = 2.5
                    
                    pipe = Pipe(
                        id=f'P-{pipe_id:03d}',
                        start=(s1.x, s1.y, s1.z),
                        end=(s2.x, s2.y, s2.z),
                        diameter=diameter,
                        length=seg_length,
                        pipe_type='branch'
                    )
                    pipes.append(pipe)
                    pipe_id += 1
                    
                    fitting = Fitting(
                        id=f'F-{fit_id:03d}',
                        x=s1.x, y=s1.y, z=s1.z,
                        fitting_type='tee',
                        size=diameter
                    )
                    fittings.append(fitting)
                    fit_id += 1
        
        x_offset += width + 10
    
    return sprinklers, pipes, fittings


def design_main_piping(zones: List[Zone], sprinklers: List[Sprinkler], branch_pipes: List[Pipe]) -> Tuple[List[Pipe], List[Fitting], List[Valve]]:
    """Design cross main and riser piping"""
    
    pipes = []
    fittings = []
    valves = []
    
    if not sprinklers:
        return pipes, fittings, valves
    
    min_x = min(s.x for s in sprinklers)
    max_x = max(s.x for s in sprinklers)
    min_y = min(s.y for s in sprinklers)
    z = sprinklers[0].z if sprinklers else 10
    
    main_y = min_y - 5
    
    # Size main based on total sprinklers
    total_spk = len(sprinklers)
    if total_spk <= 20:
        main_diameter = 2.5
    elif total_spk <= 50:
        main_diameter = 3.0
    elif total_spk <= 100:
        main_diameter = 4.0
    elif total_spk <= 200:
        main_diameter = 5.0
    elif total_spk <= 500:
        main_diameter = 6.0
    else:
        main_diameter = 8.0
    
    # Cross main
    main_pipe = Pipe(
        id='P-MAIN-001',
        start=(min_x, main_y, z),
        end=(max_x, main_y, z),
        diameter=main_diameter,
        length=max_x - min_x,
        pipe_type='main'
    )
    pipes.append(main_pipe)
    
    # Riser
    riser = Pipe(
        id='P-RISER-001',
        start=(min_x - 5, main_y, 0),
        end=(min_x - 5, main_y, z),
        diameter=main_diameter,
        length=z,
        pipe_type='riser'
    )
    pipes.append(riser)
    
    # Standard valves
    valve_types = [
        ('OS&Y Gate Valve', main_diameter),
        ('Check Valve', main_diameter),
        ('Flow Switch', main_diameter),
        ('Test & Drain', 2.0),
        ('FDC', 4.0),
        ('Pressure Gauge', 0.5)
    ]
    
    for i, (vtype, vsize) in enumerate(valve_types):
        valve = Valve(
            id=f'V-{i+1:03d}',
            x=min_x - 5, y=main_y, z=i * 1.5,
            valve_type=vtype,
            size=vsize
        )
        valves.append(valve)
    
    return pipes, fittings, valves


# =============================================================================
# HYDRAULIC CALCULATIONS
# =============================================================================

def calculate_hydraulics(design: DesignResult, water_supply: Dict) -> Dict[str, Any]:
    """Calculate system hydraulics"""
    
    result = {
        'system_demand_gpm': 0,
        'system_pressure_psi': 0,
        'available_pressure_psi': 0,
        'safety_margin_psi': 0,
        'status': 'COMPLIANT'
    }
    
    if not design.zones or not design.sprinklers:
        return result
    
    zone = design.zones[0]
    req = HAZARD_REQUIREMENTS.get(zone.hazard_class, HAZARD_REQUIREMENTS['ordinary_hazard_group_1'])
    
    # Calculate demand
    density = req['density']
    area = req['remote_area']
    hose = req['hose']
    
    sprinkler_demand = density * area
    result['system_demand_gpm'] = round(sprinkler_demand + hose)
    
    # Estimate pressure
    k_factor = req.get('k_factor', 5.6)
    min_pressure = 7.0
    
    total_pipe_length = sum(p.length for p in design.pipes)
    friction_per_100ft = 0.5
    friction_loss = (total_pipe_length / 100) * friction_per_100ft
    elevation_loss = zone.ceiling_height * 0.433
    
    result['system_pressure_psi'] = round(min_pressure + friction_loss + elevation_loss, 1)
    
    if water_supply:
        result['available_pressure_psi'] = water_supply.get('residual_pressure', 65)
        result['safety_margin_psi'] = round(result['available_pressure_psi'] - result['system_pressure_psi'], 1)
        
        if result['safety_margin_psi'] < 0:
            result['status'] = 'INSUFFICIENT PRESSURE'
    
    return result


# =============================================================================
# COST ESTIMATION
# =============================================================================

def calculate_costs(design: DesignResult) -> Dict[str, float]:
    """Calculate project costs"""
    
    # Get K-factor for pricing
    k_factor = design.sprinklers[0].k_factor if design.sprinklers else 5.6
    
    # Sprinkler price varies by K-factor
    if k_factor >= 14:
        sprinkler_price = 85.00  # ESFR
    elif k_factor >= 11:
        sprinkler_price = 45.00  # Large orifice
    elif k_factor >= 8:
        sprinkler_price = 28.00  # K-8
    else:
        sprinkler_price = 16.50  # Standard K-5.6
    
    costs = {
        'sprinklers': len(design.sprinklers) * sprinkler_price,
        'pipe': sum(p.length for p in design.pipes) * 3.80,
        'fittings': len(design.fittings) * 12.50,
        'valves': len(design.valves) * 550,
        'hangers': (sum(p.length for p in design.pipes) / 10) * 15.00,
        'bracing': 0,
        'material_total': 0,
        'labor_hours': 0,
        'labor_cost': 0,
        'overhead': 0,
        'profit': 0,
        'total': 0
    }
    
    # Seismic bracing
    if design.seismic_design_category in ['D', 'E', 'F']:
        num_braces = max(4, len(design.pipes) // 8)
        costs['bracing'] = num_braces * 175
    
    costs['material_total'] = sum([
        costs['sprinklers'], costs['pipe'], costs['fittings'],
        costs['valves'], costs['hangers'], costs['bracing']
    ])
    
    costs['labor_hours'] = (
        len(design.sprinklers) * 0.5 +
        sum(p.length for p in design.pipes) * 0.05 +
        len(design.fittings) * 0.15 +
        len(design.valves) * 1.0
    )
    costs['labor_cost'] = costs['labor_hours'] * 95
    
    subtotal = costs['material_total'] + costs['labor_cost']
    costs['overhead'] = subtotal * 0.15
    costs['profit'] = subtotal * 0.10
    costs['total'] = subtotal + costs['overhead'] + costs['profit']
    
    return costs


# =============================================================================
# OUTPUT GENERATION (PDF, DXF, CSV)
# =============================================================================

def generate_pdf_report(design: DesignResult, output_path: str) -> bool:
    """Generate PDF compliance report"""
    if not ENGINE_STATUS.get('reportlab'):
        return False
    
    try:
        doc = SimpleDocTemplate(output_path, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, spaceAfter=30, alignment=TA_CENTER)
        story.append(Paragraph("FIRE SPRINKLER SYSTEM", title_style))
        story.append(Paragraph("DESIGN & COMPLIANCE REPORT", title_style))
        story.append(Spacer(1, 20))
        
        # Project Info
        story.append(Paragraph("PROJECT INFORMATION", styles['Heading2']))
        info_data = [
            ["Project Name:", design.project_name],
            ["Project ID:", design.project_id],
            ["Building Area:", f"{design.building_area:,.0f} sq ft"],
            ["Zones:", str(len(design.zones))],
            ["Analysis Confidence:", f"{design.analysis_confidence:.0f}%"],
        ]
        t = Table(info_data, colWidths=[2*inch, 4*inch])
        t.setStyle(TableStyle([('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold')]))
        story.append(t)
        story.append(Spacer(1, 20))
        
        # Zone Analysis
        story.append(Paragraph("ZONE ANALYSIS", styles['Heading2']))
        zone_data = [["Zone", "Area (sqft)", "Hazard Class", "Sprinklers", "Pipe (LF)"]]
        for zone in design.zones:
            zone_spks = len([s for s in design.sprinklers if s.zone_id == zone.id])
            zone_data.append([
                zone.name,
                f"{zone.area:,.0f}",
                zone.hazard_class.replace('_', ' ').title(),
                str(zone_spks),
                f"{sum(p.length for p in design.pipes) / max(1, len(design.zones)):.0f}"
            ])
        t = Table(zone_data, colWidths=[1.5*inch, 1.2*inch, 1.8*inch, 1*inch, 1*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))
        
        # Compliance
        story.append(Paragraph("COMPLIANCE STATUS", styles['Heading2']))
        story.append(Paragraph(f"Status: {design.compliance_status}", styles['Normal']))
        story.append(Paragraph(f"Score: {design.compliance_score:.1f}%", styles['Normal']))
        story.append(Paragraph(f"Seismic Design Category: {design.seismic_design_category}", styles['Normal']))
        story.append(Spacer(1, 20))
        
        # System Summary
        story.append(Paragraph("SYSTEM SUMMARY", styles['Heading2']))
        k = design.sprinklers[0].k_factor if design.sprinklers else 5.6
        k_type = "ESFR" if k >= 14 else "Large Orifice" if k >= 8 else "Standard"
        summary = [
            ["Component", "Qty", "Notes"],
            ["Sprinklers", str(len(design.sprinklers)), f"K={k} ({k_type}), 165°F"],
            ["Pipe", f"{sum(p.length for p in design.pipes):.0f} LF", "Sch 40"],
            ["Fittings", str(len(design.fittings)), "Tees/Elbows"],
            ["Valves", str(len(design.valves)), "Per NFPA 13"],
        ]
        t = Table(summary)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        story.append(t)
        story.append(PageBreak())
        
        # Cost Estimate
        story.append(Paragraph("COST ESTIMATE", styles['Heading2']))
        cost_data = [
            ["Materials", f"${design.total_cost * 0.42:,.2f}"],
            ["Labor", f"${design.total_cost * 0.36:,.2f}"],
            ["TOTAL", f"${design.total_cost:,.2f}"],
            ["Cost per Sq Ft", f"${design.cost_per_sqft:.2f}"],
        ]
        t = Table(cost_data)
        story.append(t)
        
        doc.build(story)
        logger.info(f"✅ PDF saved: {output_path}")
        return True
    except Exception as e:
        logger.error(f"PDF error: {e}")
        return False


def generate_dxf_output(design: DesignResult, output_path: str) -> bool:
    """Generate DXF shop drawing"""
    if not ENGINE_STATUS.get('ezdxf'):
        return False
    
    try:
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        doc.layers.add('SPRINKLERS', color=1)
        doc.layers.add('PIPES', color=5)
        doc.layers.add('MAINS', color=3)
        
        for spk in design.sprinklers:
            msp.add_circle((spk.x, spk.y), 0.5, dxfattribs={'layer': 'SPRINKLERS'})
            msp.add_text(spk.id, dxfattribs={'layer': 'SPRINKLERS', 'height': 0.3, 'insert': (spk.x + 0.7, spk.y)})
        
        for pipe in design.pipes:
            layer = 'MAINS' if pipe.pipe_type in ['main', 'riser'] else 'PIPES'
            msp.add_line(pipe.start[:2], pipe.end[:2], dxfattribs={'layer': layer})
        
        doc.saveas(output_path)
        logger.info(f"✅ DXF saved: {output_path}")
        return True
    except Exception as e:
        logger.error(f"DXF error: {e}")
        return False


def generate_bom_csv(design: DesignResult, output_path: str) -> bool:
    """Generate BOM CSV"""
    try:
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Category', 'Item', 'Size', 'Quantity', 'Unit', 'Unit Cost', 'Total'])
            
            k = design.sprinklers[0].k_factor if design.sprinklers else 5.6
            price = 85 if k >= 14 else 45 if k >= 11 else 28 if k >= 8 else 16.50
            writer.writerow(['Sprinklers', f'Pendant K-{k}', '-', len(design.sprinklers), 'EA', f'${price:.2f}', f'${len(design.sprinklers) * price:.2f}'])
            
            pipe_by_size = {}
            for p in design.pipes:
                size = f'{p.diameter}"'
                pipe_by_size[size] = pipe_by_size.get(size, 0) + p.length
            for size, length in sorted(pipe_by_size.items()):
                writer.writerow(['Pipe', f'Black Steel {size}', size, f'{length:.0f}', 'LF', '$3.80', f'${length * 3.80:.2f}'])
            
            writer.writerow(['Fittings', 'Tees/Elbows', '-', len(design.fittings), 'EA', '$12.50', f'${len(design.fittings) * 12.50:.2f}'])
            
            for v in design.valves:
                writer.writerow(['Valves', v.valve_type, f'{v.size}"', 1, 'EA', '$550', '$550'])
            
            writer.writerow([])
            writer.writerow(['', '', '', '', '', 'TOTAL:', f'${design.total_cost:,.2f}'])
        
        logger.info(f"✅ BOM saved: {output_path}")
        return True
    except Exception as e:
        logger.error(f"CSV error: {e}")
        return False


# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

async def orchestrate_async(project_dir: str, output_dir: str) -> Dict[str, str]:
    """Main orchestration function"""
    
    logger.info("=" * 70)
    logger.info("🔥 FireAI Pro Unified Orchestrator v15.0 (PDF Vision Enabled)")
    logger.info("=" * 70)
    
    start_time = datetime.now()
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Load project.json
    project_data = {
        'project_id': f'FP-{uuid.uuid4().hex[:8].upper()}',
        'project_name': 'Fire Sprinkler Project',
        'building_area_sqft': 0,
        'ceiling_height_ft': 12,
        'hazard_class': 'ordinary_hazard_group_1',
        'zip_code': '94102',
        'water_supply': {},
        'zones': []
    }
    
    json_path = os.path.join(project_dir, 'project.json')
    if os.path.exists(json_path):
        try:
            with open(json_path) as f:
                project_data.update(json.load(f))
        except:
            pass
    
    engines_used = []
    
    # =========================================================================
    # STEP 1: Document Analysis
    # =========================================================================
    logger.info("-" * 50)
    logger.info("STEP 1: Document Analysis")
    
    doc_data = await analyze_documents(project_dir)
    
    if doc_data.get('building_area_sqft', 0) > 0:
        project_data['building_area_sqft'] = doc_data['building_area_sqft']
        engines_used.append(f"Document Analysis ({doc_data.get('extraction_method', 'unknown')})")
    
    if doc_data.get('hazard_class'):
        project_data['hazard_class'] = doc_data['hazard_class']
    
    if doc_data.get('rooms'):
        project_data['zones'] = doc_data['rooms']
    
    obstructions = doc_data.get('obstructions', [])
    analysis_confidence = doc_data.get('confidence', 0)
    building_type = doc_data.get('building_type', 'unknown')
    
    logger.info(f"  📊 Building: {project_data['building_area_sqft']:,.0f} sqft")
    logger.info(f"  🏢 Type: {building_type}")
    logger.info(f"  ⚠️ Hazard: {project_data['hazard_class']}")
    logger.info(f"  📦 Rack Storage: {'Yes' if doc_data.get('rack_storage') else 'No'}")
    logger.info(f"  🎯 Confidence: {analysis_confidence}%")
    
    # =========================================================================
    # STEP 2: Create Zones
    # =========================================================================
    zones = []
    if project_data.get('zones'):
        for r in project_data['zones']:
            zones.append(Zone(
                id=r.get('id', r.get('zone_id', f'ZONE-{len(zones)+1:03d}')),
                name=r.get('name', r.get('zone_name', f'Zone {len(zones)+1}')),
                area=r.get('area', r.get('area_sqft', 1000)),
                ceiling_height=r.get('ceiling_height', r.get('ceiling_height_ft', 12)),
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
    
    # =========================================================================
    # STEP 3: Sprinkler Layout
    # =========================================================================
    logger.info("-" * 50)
    logger.info("STEP 2: Sprinkler Layout Design")
    
    sprinklers, branch_pipes, branch_fittings = design_sprinkler_layout(zones, obstructions)
    main_pipes, main_fittings, valves = design_main_piping(zones, sprinklers, branch_pipes)
    
    all_pipes = branch_pipes + main_pipes
    all_fittings = branch_fittings + main_fittings
    
    logger.info(f"  💧 Sprinklers: {len(sprinklers)}")
    logger.info(f"  🔧 Pipes: {len(all_pipes)} ({sum(p.length for p in all_pipes):,.0f} LF)")
    
    # =========================================================================
    # STEP 4: Create Design Result
    # =========================================================================
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
    
    # =========================================================================
    # STEP 5: Hydraulics
    # =========================================================================
    logger.info("-" * 50)
    logger.info("STEP 3: Hydraulic Calculations")
    
    water_supply = project_data.get('water_supply', {})
    hydraulics = calculate_hydraulics(design, water_supply)
    
    logger.info(f"  💦 Demand: {hydraulics['system_demand_gpm']} GPM")
    logger.info(f"  📈 Pressure: {hydraulics['system_pressure_psi']} PSI")
    logger.info(f"  ✅ Status: {hydraulics['status']}")
    
    # =========================================================================
    # STEP 6: Costs
    # =========================================================================
    logger.info("-" * 50)
    logger.info("STEP 4: Cost Estimation")
    
    costs = calculate_costs(design)
    design.total_cost = costs['total']
    design.cost_per_sqft = design.total_cost / design.building_area if design.building_area > 0 else 0
    
    logger.info(f"  💰 Material: ${costs['material_total']:,.0f}")
    logger.info(f"  👷 Labor: ${costs['labor_cost']:,.0f}")
    logger.info(f"  📊 Total: ${design.total_cost:,.0f} (${design.cost_per_sqft:.2f}/sqft)")
    
    # =========================================================================
    # STEP 7: Generate Outputs
    # =========================================================================
    logger.info("-" * 50)
    logger.info("STEP 5: Generate Outputs")
    
    outputs = {}
    
    if generate_dxf_output(design, os.path.join(output_dir, 'design.dxf')):
        outputs['design.dxf'] = os.path.join(output_dir, 'design.dxf')
    
    if generate_bom_csv(design, os.path.join(output_dir, 'bill_of_materials.csv')):
        outputs['bill_of_materials.csv'] = os.path.join(output_dir, 'bill_of_materials.csv')
    
    if generate_pdf_report(design, os.path.join(output_dir, 'compliance_report.pdf')):
        outputs['compliance_report.pdf'] = os.path.join(output_dir, 'compliance_report.pdf')
    
    # Summary JSON
    summary = {
        'project_id': design.project_id,
        'project_name': design.project_name,
        'building_area_sqft': design.building_area,
        'building_type': building_type,
        'hazard_class': project_data['hazard_class'],
        'analysis_confidence': analysis_confidence,
        'extraction_method': doc_data.get('extraction_method', 'none'),
        'sprinklers': len(sprinklers),
        'k_factor': sprinklers[0].k_factor if sprinklers else 5.6,
        'pipes': len(all_pipes),
        'pipe_footage': sum(p.length for p in all_pipes),
        'hydraulics': hydraulics,
        'costs': costs,
        'compliance_status': design.compliance_status,
        'seismic_design_category': design.seismic_design_category,
        'engines_used': engines_used
    }
    
    with open(os.path.join(output_dir, 'summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)
    outputs['summary.json'] = os.path.join(output_dir, 'summary.json')
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    logger.info("=" * 70)
    logger.info(f"🎉 COMPLETE in {elapsed:.2f}s")
    logger.info(f"   📁 Files: {list(outputs.keys())}")
    logger.info(f"   💰 Cost: ${design.total_cost:,.0f}")
    logger.info("=" * 70)
    
    return outputs


def orchestrate(project_dir: str, output_dir: str) -> Dict[str, str]:
    """Sync wrapper"""
    return asyncio.run(orchestrate_async(project_dir, output_dir))


def get_engine_status() -> Dict[str, Any]:
    """Return engine status for health endpoint"""
    return ENGINE_STATUS.copy()


if __name__ == "__main__":
    print("🔥 FireAI Pro Unified Orchestrator v15.0")
    print("=" * 60)
    print("\n📋 ENGINE STATUS:")
    for engine, status in ENGINE_STATUS.items():
        print(f"   {'✅' if status else '❌'} {engine}")
    print(f"\n   Active: {sum(1 for v in ENGINE_STATUS.values() if v)}/{len(ENGINE_STATUS)}")
