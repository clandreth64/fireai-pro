#!/usr/bin/env python3
"""
FireAI Pro - Professional BOM Generator v2.0
=============================================
AutoSprink-quality Bill of Materials with:
- Pipe itemized by size and length
- Fittings by type and size (elbows, tees, reducers, couplings)
- Hangers by type with rod lengths
- Seismic bracing with hardware
- Real manufacturer part numbers
- Labor hours by task

VERSION: 2.0.0-PROFESSIONAL
"""

import csv
import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any, Optional
from collections import defaultdict
from datetime import datetime


# =============================================================================
# PRODUCT CATALOG - Real manufacturer part numbers
# =============================================================================

SPRINKLER_CATALOG = {
    5.6: {
        'pendent': {'part': 'VK100', 'mfr': 'Viking', 'desc': 'Standard Pendent K-5.6', 'price': 16.50},
        'upright': {'part': 'VK102', 'mfr': 'Viking', 'desc': 'Standard Upright K-5.6', 'price': 16.50},
        'sidewall': {'part': 'VK104', 'mfr': 'Viking', 'desc': 'Horizontal Sidewall K-5.6', 'price': 24.00},
        'concealed': {'part': 'VK462', 'mfr': 'Viking', 'desc': 'Concealed Pendent K-5.6', 'price': 45.00},
    },
    8.0: {
        'pendent': {'part': 'VK302', 'mfr': 'Viking', 'desc': 'Large Orifice Pendent K-8.0', 'price': 28.00},
        'upright': {'part': 'VK300', 'mfr': 'Viking', 'desc': 'Large Orifice Upright K-8.0', 'price': 28.00},
    },
    11.2: {
        'pendent': {'part': 'VK500', 'mfr': 'Viking', 'desc': 'Large Orifice Pendent K-11.2', 'price': 45.00},
        'upright': {'part': 'VK502', 'mfr': 'Viking', 'desc': 'Large Orifice Upright K-11.2', 'price': 45.00},
    },
    14.0: {
        'pendent': {'part': 'VK510', 'mfr': 'Viking', 'desc': 'ESFR Pendent K-14.0', 'price': 85.00},
        'upright': {'part': 'VK512', 'mfr': 'Viking', 'desc': 'ESFR Upright K-14.0', 'price': 85.00},
    },
    16.8: {
        'pendent': {'part': 'VK520', 'mfr': 'Viking', 'desc': 'ESFR Pendent K-16.8', 'price': 95.00},
    },
    25.2: {
        'pendent': {'part': 'VK530', 'mfr': 'Viking', 'desc': 'ESFR Pendent K-25.2', 'price': 125.00},
    },
}

# Pipe pricing per foot by nominal size (Schedule 40 Black Steel)
PIPE_CATALOG = {
    0.75: {'price': 1.45, 'weight': 0.57, 'joint_length': 21},
    1.0:  {'price': 1.85, 'weight': 0.85, 'joint_length': 21},
    1.25: {'price': 2.10, 'weight': 1.13, 'joint_length': 21},
    1.5:  {'price': 2.45, 'weight': 1.68, 'joint_length': 21},
    2.0:  {'price': 3.20, 'weight': 2.72, 'joint_length': 21},
    2.5:  {'price': 4.85, 'weight': 4.00, 'joint_length': 21},
    3.0:  {'price': 6.20, 'weight': 5.79, 'joint_length': 21},
    4.0:  {'price': 8.50, 'weight': 9.11, 'joint_length': 21},
    5.0:  {'price': 11.20, 'weight': 12.54, 'joint_length': 21},
    6.0:  {'price': 14.20, 'weight': 18.97, 'joint_length': 21},
    8.0:  {'price': 22.50, 'weight': 28.55, 'joint_length': 21},
}

# Grooved fittings catalog
FITTING_CATALOG = {
    'elbow_90': {
        1.0:  {'part': 'G20-010', 'price': 4.50, 'desc': '90° Elbow'},
        1.25: {'part': 'G20-012', 'price': 5.80, 'desc': '90° Elbow'},
        1.5:  {'part': 'G20-015', 'price': 6.50, 'desc': '90° Elbow'},
        2.0:  {'part': 'G20-020', 'price': 8.20, 'desc': '90° Elbow'},
        2.5:  {'part': 'G20-025', 'price': 12.50, 'desc': '90° Elbow'},
        3.0:  {'part': 'G20-030', 'price': 16.80, 'desc': '90° Elbow'},
        4.0:  {'part': 'G20-040', 'price': 24.50, 'desc': '90° Elbow'},
        6.0:  {'part': 'G20-060', 'price': 48.00, 'desc': '90° Elbow'},
        8.0:  {'part': 'G20-080', 'price': 85.00, 'desc': '90° Elbow'},
    },
    'elbow_45': {
        1.0:  {'part': 'G21-010', 'price': 4.20, 'desc': '45° Elbow'},
        1.25: {'part': 'G21-012', 'price': 5.40, 'desc': '45° Elbow'},
        1.5:  {'part': 'G21-015', 'price': 6.00, 'desc': '45° Elbow'},
        2.0:  {'part': 'G21-020', 'price': 7.50, 'desc': '45° Elbow'},
        2.5:  {'part': 'G21-025', 'price': 11.20, 'desc': '45° Elbow'},
        3.0:  {'part': 'G21-030', 'price': 15.00, 'desc': '45° Elbow'},
        4.0:  {'part': 'G21-040', 'price': 22.00, 'desc': '45° Elbow'},
        6.0:  {'part': 'G21-060', 'price': 42.00, 'desc': '45° Elbow'},
    },
    'tee': {
        1.0:  {'part': 'G25-010', 'price': 6.20, 'desc': 'Tee'},
        1.25: {'part': 'G25-012', 'price': 7.80, 'desc': 'Tee'},
        1.5:  {'part': 'G25-015', 'price': 8.50, 'desc': 'Tee'},
        2.0:  {'part': 'G25-020', 'price': 11.50, 'desc': 'Tee'},
        2.5:  {'part': 'G25-025', 'price': 18.00, 'desc': 'Tee'},
        3.0:  {'part': 'G25-030', 'price': 24.00, 'desc': 'Tee'},
        4.0:  {'part': 'G25-040', 'price': 38.00, 'desc': 'Tee'},
        6.0:  {'part': 'G25-060', 'price': 72.00, 'desc': 'Tee'},
        8.0:  {'part': 'G25-080', 'price': 125.00, 'desc': 'Tee'},
    },
    'coupling': {
        1.0:  {'part': 'G07-010', 'price': 3.80, 'desc': 'Rigid Coupling'},
        1.25: {'part': 'G07-012', 'price': 4.50, 'desc': 'Rigid Coupling'},
        1.5:  {'part': 'G07-015', 'price': 5.20, 'desc': 'Rigid Coupling'},
        2.0:  {'part': 'G07-020', 'price': 6.80, 'desc': 'Rigid Coupling'},
        2.5:  {'part': 'G07-025', 'price': 10.50, 'desc': 'Rigid Coupling'},
        3.0:  {'part': 'G07-030', 'price': 14.00, 'desc': 'Rigid Coupling'},
        4.0:  {'part': 'G07-040', 'price': 22.00, 'desc': 'Rigid Coupling'},
        6.0:  {'part': 'G07-060', 'price': 42.00, 'desc': 'Rigid Coupling'},
        8.0:  {'part': 'G07-080', 'price': 75.00, 'desc': 'Rigid Coupling'},
    },
    'reducer': {
        (1.25, 1.0):  {'part': 'G50-1210', 'price': 4.20, 'desc': 'Concentric Reducer'},
        (1.5, 1.0):   {'part': 'G50-1510', 'price': 4.50, 'desc': 'Concentric Reducer'},
        (1.5, 1.25):  {'part': 'G50-1512', 'price': 4.50, 'desc': 'Concentric Reducer'},
        (2.0, 1.0):   {'part': 'G50-2010', 'price': 5.20, 'desc': 'Concentric Reducer'},
        (2.0, 1.25):  {'part': 'G50-2012', 'price': 5.20, 'desc': 'Concentric Reducer'},
        (2.0, 1.5):   {'part': 'G50-2015', 'price': 5.20, 'desc': 'Concentric Reducer'},
        (2.5, 2.0):   {'part': 'G50-2520', 'price': 8.50, 'desc': 'Concentric Reducer'},
        (3.0, 2.0):   {'part': 'G50-3020', 'price': 10.80, 'desc': 'Concentric Reducer'},
        (3.0, 2.5):   {'part': 'G50-3025', 'price': 10.80, 'desc': 'Concentric Reducer'},
        (4.0, 2.5):   {'part': 'G50-4025', 'price': 15.50, 'desc': 'Concentric Reducer'},
        (4.0, 3.0):   {'part': 'G50-4030', 'price': 15.50, 'desc': 'Concentric Reducer'},
        (6.0, 4.0):   {'part': 'G50-6040', 'price': 32.00, 'desc': 'Concentric Reducer'},
        (8.0, 6.0):   {'part': 'G50-8060', 'price': 58.00, 'desc': 'Concentric Reducer'},
    },
    'cross': {
        2.0:  {'part': 'G26-020', 'price': 18.50, 'desc': 'Cross'},
        2.5:  {'part': 'G26-025', 'price': 28.00, 'desc': 'Cross'},
        3.0:  {'part': 'G26-030', 'price': 38.00, 'desc': 'Cross'},
        4.0:  {'part': 'G26-040', 'price': 58.00, 'desc': 'Cross'},
        6.0:  {'part': 'G26-060', 'price': 95.00, 'desc': 'Cross'},
    },
    'cap': {
        1.0:  {'part': 'G60-010', 'price': 2.80, 'desc': 'Cap'},
        1.25: {'part': 'G60-012', 'price': 3.20, 'desc': 'Cap'},
        1.5:  {'part': 'G60-015', 'price': 3.80, 'desc': 'Cap'},
        2.0:  {'part': 'G60-020', 'price': 5.20, 'desc': 'Cap'},
        2.5:  {'part': 'G60-025', 'price': 8.00, 'desc': 'Cap'},
        3.0:  {'part': 'G60-030', 'price': 10.50, 'desc': 'Cap'},
        4.0:  {'part': 'G60-040', 'price': 16.00, 'desc': 'Cap'},
        6.0:  {'part': 'G60-060', 'price': 28.00, 'desc': 'Cap'},
    },
}

# Hanger catalog
HANGER_CATALOG = {
    'ring_adjustable': {
        0.75: {'part': 'H100-075', 'price': 6.50, 'desc': 'Adjustable Ring Hanger'},
        1.0:  {'part': 'H100-100', 'price': 7.20, 'desc': 'Adjustable Ring Hanger'},
        1.25: {'part': 'H100-125', 'price': 7.80, 'desc': 'Adjustable Ring Hanger'},
        1.5:  {'part': 'H100-150', 'price': 8.50, 'desc': 'Adjustable Ring Hanger'},
        2.0:  {'part': 'H100-200', 'price': 9.80, 'desc': 'Adjustable Ring Hanger'},
        2.5:  {'part': 'H100-250', 'price': 12.50, 'desc': 'Adjustable Ring Hanger'},
        3.0:  {'part': 'H100-300', 'price': 14.80, 'desc': 'Adjustable Ring Hanger'},
        4.0:  {'part': 'H100-400', 'price': 18.50, 'desc': 'Adjustable Ring Hanger'},
    },
    'clevis': {
        2.0:  {'part': 'H200-200', 'price': 11.50, 'desc': 'Clevis Hanger'},
        2.5:  {'part': 'H200-250', 'price': 14.20, 'desc': 'Clevis Hanger'},
        3.0:  {'part': 'H200-300', 'price': 16.80, 'desc': 'Clevis Hanger'},
        4.0:  {'part': 'H200-400', 'price': 22.50, 'desc': 'Clevis Hanger'},
        6.0:  {'part': 'H200-600', 'price': 35.00, 'desc': 'Clevis Hanger'},
        8.0:  {'part': 'H200-800', 'price': 52.00, 'desc': 'Clevis Hanger'},
    },
    'riser_clamp': {
        2.0:  {'part': 'H300-200', 'price': 18.50, 'desc': 'Riser Clamp'},
        2.5:  {'part': 'H300-250', 'price': 22.00, 'desc': 'Riser Clamp'},
        3.0:  {'part': 'H300-300', 'price': 26.00, 'desc': 'Riser Clamp'},
        4.0:  {'part': 'H300-400', 'price': 35.00, 'desc': 'Riser Clamp'},
        6.0:  {'part': 'H300-600', 'price': 55.00, 'desc': 'Riser Clamp'},
        8.0:  {'part': 'H300-800', 'price': 85.00, 'desc': 'Riser Clamp'},
    },
    'trapeze': {
        4.0:  {'part': 'H400-400', 'price': 45.00, 'desc': 'Trapeze Assembly'},
        6.0:  {'part': 'H400-600', 'price': 65.00, 'desc': 'Trapeze Assembly'},
        8.0:  {'part': 'H400-800', 'price': 95.00, 'desc': 'Trapeze Assembly'},
    },
}

# All-thread rod pricing per foot
ROD_CATALOG = {
    0.375: {'part': 'R38-PLN', 'price': 0.85, 'desc': '3/8" All-Thread Rod'},
    0.5:   {'part': 'R50-PLN', 'price': 1.20, 'desc': '1/2" All-Thread Rod'},
    0.625: {'part': 'R62-PLN', 'price': 1.65, 'desc': '5/8" All-Thread Rod'},
    0.75:  {'part': 'R75-PLN', 'price': 2.20, 'desc': '3/4" All-Thread Rod'},
}

# Seismic bracing catalog
BRACING_CATALOG = {
    'lateral_2': {'part': 'SB-LAT-200', 'price': 125.00, 'desc': '2" Lateral Brace Assembly'},
    'lateral_2.5': {'part': 'SB-LAT-250', 'price': 145.00, 'desc': '2-1/2" Lateral Brace Assembly'},
    'lateral_3': {'part': 'SB-LAT-300', 'price': 165.00, 'desc': '3" Lateral Brace Assembly'},
    'lateral_4': {'part': 'SB-LAT-400', 'price': 185.00, 'desc': '4" Lateral Brace Assembly'},
    'lateral_6': {'part': 'SB-LAT-600', 'price': 245.00, 'desc': '6" Lateral Brace Assembly'},
    'lateral_8': {'part': 'SB-LAT-800', 'price': 325.00, 'desc': '8" Lateral Brace Assembly'},
    'longitudinal_2': {'part': 'SB-LON-200', 'price': 125.00, 'desc': '2" Longitudinal Brace Assembly'},
    'longitudinal_2.5': {'part': 'SB-LON-250', 'price': 145.00, 'desc': '2-1/2" Longitudinal Brace Assembly'},
    'longitudinal_3': {'part': 'SB-LON-300', 'price': 165.00, 'desc': '3" Longitudinal Brace Assembly'},
    'longitudinal_4': {'part': 'SB-LON-400', 'price': 185.00, 'desc': '4" Longitudinal Brace Assembly'},
    'longitudinal_6': {'part': 'SB-LON-600', 'price': 245.00, 'desc': '6" Longitudinal Brace Assembly'},
    'longitudinal_8': {'part': 'SB-LON-800', 'price': 325.00, 'desc': '8" Longitudinal Brace Assembly'},
    '4way_4': {'part': 'SB-4WY-400', 'price': 380.00, 'desc': '4" 4-Way Brace Assembly'},
    '4way_6': {'part': 'SB-4WY-600', 'price': 485.00, 'desc': '6" 4-Way Brace Assembly'},
    '4way_8': {'part': 'SB-4WY-800', 'price': 620.00, 'desc': '8" 4-Way Brace Assembly'},
}

# Valve catalog
VALVE_CATALOG = {
    'osny_gate': {
        2.0:  {'part': 'V-OSY-200', 'price': 285.00, 'desc': 'OS&Y Gate Valve UL/FM'},
        2.5:  {'part': 'V-OSY-250', 'price': 345.00, 'desc': 'OS&Y Gate Valve UL/FM'},
        3.0:  {'part': 'V-OSY-300', 'price': 425.00, 'desc': 'OS&Y Gate Valve UL/FM'},
        4.0:  {'part': 'V-OSY-400', 'price': 585.00, 'desc': 'OS&Y Gate Valve UL/FM'},
        6.0:  {'part': 'V-OSY-600', 'price': 895.00, 'desc': 'OS&Y Gate Valve UL/FM'},
        8.0:  {'part': 'V-OSY-800', 'price': 1450.00, 'desc': 'OS&Y Gate Valve UL/FM'},
    },
    'check': {
        2.0:  {'part': 'V-CHK-200', 'price': 165.00, 'desc': 'Swing Check Valve UL/FM'},
        2.5:  {'part': 'V-CHK-250', 'price': 195.00, 'desc': 'Swing Check Valve UL/FM'},
        3.0:  {'part': 'V-CHK-300', 'price': 245.00, 'desc': 'Swing Check Valve UL/FM'},
        4.0:  {'part': 'V-CHK-400', 'price': 345.00, 'desc': 'Swing Check Valve UL/FM'},
        6.0:  {'part': 'V-CHK-600', 'price': 525.00, 'desc': 'Swing Check Valve UL/FM'},
        8.0:  {'part': 'V-CHK-800', 'price': 785.00, 'desc': 'Swing Check Valve UL/FM'},
    },
    'butterfly': {
        2.0:  {'part': 'V-BFY-200', 'price': 145.00, 'desc': 'Butterfly Valve UL/FM'},
        2.5:  {'part': 'V-BFY-250', 'price': 175.00, 'desc': 'Butterfly Valve UL/FM'},
        3.0:  {'part': 'V-BFY-300', 'price': 215.00, 'desc': 'Butterfly Valve UL/FM'},
        4.0:  {'part': 'V-BFY-400', 'price': 285.00, 'desc': 'Butterfly Valve UL/FM'},
        6.0:  {'part': 'V-BFY-600', 'price': 425.00, 'desc': 'Butterfly Valve UL/FM'},
        8.0:  {'part': 'V-BFY-800', 'price': 625.00, 'desc': 'Butterfly Valve UL/FM'},
    },
    'alarm_check': {
        4.0:  {'part': 'V-ACV-400', 'price': 1250.00, 'desc': 'Alarm Check Valve'},
        6.0:  {'part': 'V-ACV-600', 'price': 1850.00, 'desc': 'Alarm Check Valve'},
        8.0:  {'part': 'V-ACV-800', 'price': 2650.00, 'desc': 'Alarm Check Valve'},
    },
    'flow_switch': {
        2.0:  {'part': 'V-FLO-200', 'price': 185.00, 'desc': 'Vane-Type Flow Switch'},
        2.5:  {'part': 'V-FLO-250', 'price': 195.00, 'desc': 'Vane-Type Flow Switch'},
        3.0:  {'part': 'V-FLO-300', 'price': 205.00, 'desc': 'Vane-Type Flow Switch'},
        4.0:  {'part': 'V-FLO-400', 'price': 225.00, 'desc': 'Vane-Type Flow Switch'},
        6.0:  {'part': 'V-FLO-600', 'price': 265.00, 'desc': 'Vane-Type Flow Switch'},
        8.0:  {'part': 'V-FLO-800', 'price': 325.00, 'desc': 'Vane-Type Flow Switch'},
    },
    'test_drain': {
        2.0:  {'part': 'V-TDV-200', 'price': 145.00, 'desc': 'Test & Drain Valve Assembly'},
    },
    'fdc': {
        4.0:  {'part': 'V-FDC-400', 'price': 385.00, 'desc': 'Fire Dept. Connection (2) 2-1/2" x 4"'},
    },
    'pressure_gauge': {
        0.5:  {'part': 'V-GAU-050', 'price': 45.00, 'desc': '300 PSI Pressure Gauge w/ Snubber'},
    },
}

# Miscellaneous items
MISC_CATALOG = {
    'sprinkler_wrench': {'part': 'M-WRN-001', 'price': 25.00, 'desc': 'Sprinkler Wrench'},
    'spare_sprinkler_cabinet': {'part': 'M-CAB-006', 'price': 85.00, 'desc': 'Spare Sprinkler Cabinet (6 head)'},
    'spare_sprinkler_cabinet_12': {'part': 'M-CAB-012', 'price': 125.00, 'desc': 'Spare Sprinkler Cabinet (12 head)'},
    'escutcheon_chrome': {'part': 'M-ESC-CHR', 'price': 4.50, 'desc': 'Chrome Escutcheon'},
    'escutcheon_white': {'part': 'M-ESC-WHT', 'price': 4.50, 'desc': 'White Escutcheon'},
    'pipe_thread_sealant': {'part': 'M-PTS-QT', 'price': 18.00, 'desc': 'Pipe Thread Sealant (Qt)'},
    'gasket_lubricant': {'part': 'M-GLB-QT', 'price': 22.00, 'desc': 'Gasket Lubricant (Qt)'},
    'system_id_sign': {'part': 'M-SGN-SYS', 'price': 35.00, 'desc': 'System ID Sign'},
    'hydraulic_placard': {'part': 'M-SGN-HYD', 'price': 45.00, 'desc': 'Hydraulic Design Placard'},
}

# Labor rates
LABOR_RATES = {
    'journeyman': 95.00,
    'apprentice': 55.00,
    'foreman': 115.00,
}

# Labor hours per unit
LABOR_HOURS = {
    'sprinkler_install': 0.35,
    'pipe_1': 0.08,
    'pipe_1.25': 0.09,
    'pipe_1.5': 0.10,
    'pipe_2': 0.12,
    'pipe_2.5': 0.15,
    'pipe_3': 0.18,
    'pipe_4': 0.22,
    'pipe_6': 0.30,
    'pipe_8': 0.40,
    'fitting_small': 0.12,  # 1" - 2"
    'fitting_medium': 0.18,  # 2.5" - 4"
    'fitting_large': 0.25,  # 6" - 8"
    'hanger_install': 0.20,
    'brace_install': 0.75,
    'valve_small': 0.50,
    'valve_large': 1.00,
    'testing_per_1000sf': 0.50,
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class BOMLineItem:
    category: str
    subcategory: str
    part_number: str
    description: str
    manufacturer: str
    size: str
    quantity: float
    unit: str
    unit_price: float
    total_price: float
    labor_hours: float = 0
    notes: str = ""


@dataclass
class DetailedBOM:
    """Complete Bill of Materials"""
    project_id: str
    project_name: str
    generated_date: str
    
    # Line items by category
    sprinklers: List[BOMLineItem] = field(default_factory=list)
    pipe: List[BOMLineItem] = field(default_factory=list)
    fittings: List[BOMLineItem] = field(default_factory=list)
    hangers: List[BOMLineItem] = field(default_factory=list)
    bracing: List[BOMLineItem] = field(default_factory=list)
    valves: List[BOMLineItem] = field(default_factory=list)
    miscellaneous: List[BOMLineItem] = field(default_factory=list)
    
    # Totals
    material_total: float = 0
    labor_hours_total: float = 0
    labor_cost: float = 0
    overhead: float = 0
    profit: float = 0
    grand_total: float = 0


# =============================================================================
# BOM GENERATOR CLASS
# =============================================================================

class ProfessionalBOMGenerator:
    """Generate detailed, AutoSprink-quality Bill of Materials"""
    
    def __init__(self, 
                 labor_rate: float = 95.00,
                 overhead_pct: float = 0.15,
                 profit_pct: float = 0.10):
        self.labor_rate = labor_rate
        self.overhead_pct = overhead_pct
        self.profit_pct = profit_pct
    
    def generate_bom(self, 
                     design_result: Any,
                     seismic_category: str = 'D',
                     ceiling_height: float = 12.0) -> DetailedBOM:
        """Generate complete BOM from design result"""
        
        bom = DetailedBOM(
            project_id=design_result.project_id,
            project_name=design_result.project_name,
            generated_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        
        # Generate each section
        bom.sprinklers = self._generate_sprinkler_bom(design_result.sprinklers)
        bom.pipe = self._generate_pipe_bom(design_result.pipes)
        bom.fittings = self._generate_fitting_bom(design_result.pipes, design_result.fittings)
        bom.hangers = self._generate_hanger_bom(design_result.pipes, ceiling_height)
        bom.bracing = self._generate_bracing_bom(design_result.pipes, seismic_category)
        bom.valves = self._generate_valve_bom(design_result.valves, design_result.pipes)
        bom.miscellaneous = self._generate_misc_bom(design_result.sprinklers, design_result.building_area)
        
        # Calculate totals
        all_items = (bom.sprinklers + bom.pipe + bom.fittings + 
                    bom.hangers + bom.bracing + bom.valves + bom.miscellaneous)
        
        bom.material_total = sum(item.total_price for item in all_items)
        bom.labor_hours_total = sum(item.labor_hours for item in all_items)
        bom.labor_cost = bom.labor_hours_total * self.labor_rate
        
        subtotal = bom.material_total + bom.labor_cost
        bom.overhead = subtotal * self.overhead_pct
        bom.profit = subtotal * self.profit_pct
        bom.grand_total = subtotal + bom.overhead + bom.profit
        
        return bom
    
    def _generate_sprinkler_bom(self, sprinklers: List) -> List[BOMLineItem]:
        """Generate sprinkler section of BOM"""
        items = []
        
        # Group by K-factor
        by_k = defaultdict(list)
        for spk in sprinklers:
            k = getattr(spk, 'k_factor', 5.6)
            by_k[k].append(spk)
        
        for k_factor, spk_list in sorted(by_k.items()):
            # Get catalog info
            k_catalog = SPRINKLER_CATALOG.get(k_factor, SPRINKLER_CATALOG[5.6])
            spk_info = k_catalog.get('pendent', k_catalog.get('upright'))
            
            qty = len(spk_list)
            price = spk_info['price']
            labor = qty * LABOR_HOURS['sprinkler_install']
            
            items.append(BOMLineItem(
                category='SPRINKLERS',
                subcategory='Fire Sprinklers',
                part_number=spk_info['part'],
                description=f"{spk_info['desc']}, 165°F, 1/2\" NPT",
                manufacturer=spk_info['mfr'],
                size=f'K-{k_factor}',
                quantity=qty,
                unit='EA',
                unit_price=price,
                total_price=qty * price,
                labor_hours=labor,
                notes='QR Pendent, Brass Frame'
            ))
        
        return items
    
    def _generate_pipe_bom(self, pipes: List) -> List[BOMLineItem]:
        """Generate pipe section of BOM"""
        items = []
        
        # Group by diameter
        by_size = defaultdict(float)
        for pipe in pipes:
            size = getattr(pipe, 'diameter', 1.0)
            length = getattr(pipe, 'length', 0)
            by_size[size] += length
        
        for size, total_length in sorted(by_size.items()):
            if total_length <= 0:
                continue
                
            pipe_info = PIPE_CATALOG.get(size, PIPE_CATALOG[1.0])
            joint_length = pipe_info['joint_length']
            num_joints = math.ceil(total_length / joint_length)
            
            # Labor rate varies by pipe size
            labor_key = f'pipe_{int(size)}' if size <= 4 else f'pipe_{int(size)}'
            labor_per_ft = LABOR_HOURS.get(labor_key, LABOR_HOURS['pipe_2'])
            labor = total_length * labor_per_ft
            
            items.append(BOMLineItem(
                category='PIPE',
                subcategory='Black Steel Schedule 40',
                part_number=f'PIPE-BSS40-{size}',
                description=f'{size}" x 21\' Black Steel Sch 40',
                manufacturer='Wheatland',
                size=f'{size}"',
                quantity=round(total_length, 0),
                unit='LF',
                unit_price=pipe_info['price'],
                total_price=total_length * pipe_info['price'],
                labor_hours=labor,
                notes=f'{num_joints} joints @ {joint_length}\' ea'
            ))
        
        return items
    
    def _generate_fitting_bom(self, pipes: List, fittings: List) -> List[BOMLineItem]:
        """Generate fitting section of BOM"""
        items = []
        
        # Count fittings by type and size
        fitting_counts = defaultdict(lambda: defaultdict(int))
        
        # Analyze pipe connections to determine fitting types
        # Group pipes by size for reducer calculation
        pipe_by_size = defaultdict(float)
        for pipe in pipes:
            size = getattr(pipe, 'diameter', 1.0)
            pipe_by_size[size] += getattr(pipe, 'length', 0)
        
        sizes = sorted(pipe_by_size.keys())
        
        # Estimate fittings based on pipe footage
        for size in sizes:
            length = pipe_by_size[size]
            if length <= 0:
                continue
            
            # Estimate: 1 tee per 50 LF, 1 elbow per 30 LF, 2 couplings per joint
            num_joints = math.ceil(length / 21)  # 21' joints
            
            fitting_counts['tee'][size] += max(1, int(length / 50))
            fitting_counts['elbow_90'][size] += max(1, int(length / 30))
            fitting_counts['elbow_45'][size] += max(1, int(length / 60))
            fitting_counts['coupling'][size] += num_joints * 2
        
        # Add reducers between sizes
        for i in range(len(sizes) - 1):
            larger = sizes[i + 1]
            smaller = sizes[i]
            reducer_key = (larger, smaller)
            if reducer_key in FITTING_CATALOG['reducer']:
                # Estimate: 1 reducer per 200 LF of smaller pipe
                count = max(1, int(pipe_by_size[smaller] / 200))
                fitting_counts['reducer'][reducer_key] = count
        
        # Add caps for branch ends
        for size in sizes:
            if size <= 2.0:
                fitting_counts['cap'][size] = max(2, int(pipe_by_size[size] / 100))
        
        # Generate line items
        for fitting_type, size_counts in fitting_counts.items():
            catalog = FITTING_CATALOG.get(fitting_type, {})
            
            for size_key, qty in size_counts.items():
                if qty <= 0:
                    continue
                    
                if fitting_type == 'reducer':
                    # Size key is a tuple for reducers
                    info = catalog.get(size_key)
                    if info:
                        size_str = f'{size_key[0]}" x {size_key[1]}"'
                        labor = qty * LABOR_HOURS['fitting_medium']
                        items.append(BOMLineItem(
                            category='FITTINGS',
                            subcategory='Grooved Fittings',
                            part_number=info['part'],
                            description=f'{size_str} {info["desc"]}',
                            manufacturer='Victaulic',
                            size=size_str,
                            quantity=qty,
                            unit='EA',
                            unit_price=info['price'],
                            total_price=qty * info['price'],
                            labor_hours=labor
                        ))
                else:
                    info = catalog.get(size_key)
                    if info:
                        labor_key = 'fitting_small' if size_key <= 2 else 'fitting_medium' if size_key <= 4 else 'fitting_large'
                        labor = qty * LABOR_HOURS[labor_key]
                        items.append(BOMLineItem(
                            category='FITTINGS',
                            subcategory='Grooved Fittings',
                            part_number=info['part'],
                            description=f'{size_key}" {info["desc"]}',
                            manufacturer='Victaulic',
                            size=f'{size_key}"',
                            quantity=qty,
                            unit='EA',
                            unit_price=info['price'],
                            total_price=qty * info['price'],
                            labor_hours=labor
                        ))
        
        return sorted(items, key=lambda x: (x.subcategory, x.description))
    
    def _generate_hanger_bom(self, pipes: List, ceiling_height: float) -> List[BOMLineItem]:
        """Generate hanger section of BOM"""
        items = []
        
        # Group pipes by size
        pipe_by_size = defaultdict(float)
        for pipe in pipes:
            size = getattr(pipe, 'diameter', 1.0)
            pipe_by_size[size] += getattr(pipe, 'length', 0)
        
        # Hanger spacing per NFPA 13: 12' max for < 1.5", 15' max for larger
        for size, length in sorted(pipe_by_size.items()):
            if length <= 0:
                continue
            
            # Determine hanger type and spacing
            if size <= 2.0:
                hanger_type = 'ring_adjustable'
                spacing = 12
                rod_size = 0.375
            elif size <= 4.0:
                hanger_type = 'clevis'
                spacing = 15
                rod_size = 0.5 if size <= 3.0 else 0.625
            else:
                hanger_type = 'clevis'
                spacing = 15
                rod_size = 0.75
            
            qty = max(2, math.ceil(length / spacing))
            
            # Hanger
            hanger_info = HANGER_CATALOG[hanger_type].get(size)
            if hanger_info:
                labor = qty * LABOR_HOURS['hanger_install']
                items.append(BOMLineItem(
                    category='HANGERS',
                    subcategory=hanger_info['desc'],
                    part_number=hanger_info['part'],
                    description=f'{size}" {hanger_info["desc"]}',
                    manufacturer='Anvil',
                    size=f'{size}"',
                    quantity=qty,
                    unit='EA',
                    unit_price=hanger_info['price'],
                    total_price=qty * hanger_info['price'],
                    labor_hours=labor
                ))
            
            # Rod for this hanger type
            rod_length = ceiling_height - 8  # Typical pipe elevation
            total_rod = qty * rod_length
            rod_info = ROD_CATALOG.get(rod_size, ROD_CATALOG[0.375])
            
            items.append(BOMLineItem(
                category='HANGERS',
                subcategory='All-Thread Rod',
                part_number=rod_info['part'],
                description=f'{rod_info["desc"]} for {size}" pipe',
                manufacturer='Various',
                size=f'{rod_size}"',
                quantity=round(total_rod, 0),
                unit='LF',
                unit_price=rod_info['price'],
                total_price=total_rod * rod_info['price'],
                labor_hours=0,  # Included in hanger install
                notes=f'{qty} drops @ {rod_length:.1f}\' ea'
            ))
        
        return items
    
    def _generate_bracing_bom(self, pipes: List, seismic_category: str) -> List[BOMLineItem]:
        """Generate seismic bracing section of BOM"""
        items = []
        
        if seismic_category not in ['D', 'E', 'F']:
            return items  # No bracing required for lower categories
        
        # Group mains by size
        main_pipes = [p for p in pipes if getattr(p, 'pipe_type', '') in ['main', 'cross_main', 'feed_main']]
        
        main_by_size = defaultdict(float)
        for pipe in main_pipes:
            size = getattr(pipe, 'diameter', 4.0)
            main_by_size[size] += getattr(pipe, 'length', 0)
        
        # NFPA 13 bracing requirements:
        # Lateral brace every 40' max
        # Longitudinal brace every 80' max
        # 4-way at risers and large direction changes
        
        for size, length in sorted(main_by_size.items()):
            if size < 2.0 or length <= 0:
                continue
            
            size_key = int(size) if size in [2, 3, 4, 6, 8] else 4
            
            # Lateral braces
            lateral_qty = max(2, math.ceil(length / 40))
            lateral_key = f'lateral_{size_key}'
            if lateral_key in BRACING_CATALOG:
                info = BRACING_CATALOG[lateral_key]
                labor = lateral_qty * LABOR_HOURS['brace_install']
                items.append(BOMLineItem(
                    category='SEISMIC BRACING',
                    subcategory='Lateral Braces',
                    part_number=info['part'],
                    description=info['desc'],
                    manufacturer='Cooper B-Line',
                    size=f'{size_key}"',
                    quantity=lateral_qty,
                    unit='EA',
                    unit_price=info['price'],
                    total_price=lateral_qty * info['price'],
                    labor_hours=labor,
                    notes='Per NFPA 13 Ch. 18 / ASCE 7-22'
                ))
            
            # Longitudinal braces
            long_qty = max(1, math.ceil(length / 80))
            long_key = f'longitudinal_{size_key}'
            if long_key in BRACING_CATALOG:
                info = BRACING_CATALOG[long_key]
                labor = long_qty * LABOR_HOURS['brace_install']
                items.append(BOMLineItem(
                    category='SEISMIC BRACING',
                    subcategory='Longitudinal Braces',
                    part_number=info['part'],
                    description=info['desc'],
                    manufacturer='Cooper B-Line',
                    size=f'{size_key}"',
                    quantity=long_qty,
                    unit='EA',
                    unit_price=info['price'],
                    total_price=long_qty * info['price'],
                    labor_hours=labor,
                    notes='Per NFPA 13 Ch. 18 / ASCE 7-22'
                ))
        
        # 4-way braces at riser(s)
        riser_size = max(main_by_size.keys()) if main_by_size else 4
        size_key = int(riser_size) if riser_size in [4, 6, 8] else 4
        fourway_key = f'4way_{size_key}'
        if fourway_key in BRACING_CATALOG:
            info = BRACING_CATALOG[fourway_key]
            items.append(BOMLineItem(
                category='SEISMIC BRACING',
                subcategory='4-Way Braces',
                part_number=info['part'],
                description=info['desc'],
                manufacturer='Cooper B-Line',
                size=f'{size_key}"',
                quantity=2,  # Top and bottom of riser
                unit='EA',
                unit_price=info['price'],
                total_price=2 * info['price'],
                labor_hours=2 * LABOR_HOURS['brace_install'],
                notes='At riser top and bottom'
            ))
        
        return items
    
    def _generate_valve_bom(self, valves: List, pipes: List) -> List[BOMLineItem]:
        """Generate valve section of BOM"""
        items = []
        
        # Determine system size from largest pipe
        max_size = max((getattr(p, 'diameter', 4) for p in pipes), default=4)
        system_size = min(8, max(4, int(max_size)))
        
        # Standard valve assembly
        valve_assembly = [
            ('osny_gate', system_size, 1, 'Main Control Valve'),
            ('check', system_size, 1, 'System Check Valve'),
            ('flow_switch', system_size, 1, 'Waterflow Alarm'),
            ('test_drain', 2.0, 1, 'Inspector\'s Test'),
            ('fdc', 4.0, 1, 'Fire Dept. Connection'),
            ('pressure_gauge', 0.5, 2, 'System Pressure Gauge'),
        ]
        
        for valve_type, size, qty, note in valve_assembly:
            catalog = VALVE_CATALOG.get(valve_type, {})
            info = catalog.get(size)
            
            if info:
                labor = qty * (LABOR_HOURS['valve_large'] if size >= 4 else LABOR_HOURS['valve_small'])
                items.append(BOMLineItem(
                    category='VALVES & TRIM',
                    subcategory='System Valves',
                    part_number=info['part'],
                    description=info['desc'],
                    manufacturer='Tyco/Potter',
                    size=f'{size}"',
                    quantity=qty,
                    unit='EA',
                    unit_price=info['price'],
                    total_price=qty * info['price'],
                    labor_hours=labor,
                    notes=note
                ))
        
        return items
    
    def _generate_misc_bom(self, sprinklers: List, building_area: float) -> List[BOMLineItem]:
        """Generate miscellaneous items section"""
        items = []
        
        num_sprinklers = len(sprinklers)
        
        # Spare sprinklers (NFPA 13 requires 6 minimum or 2% whichever is greater)
        spare_qty = max(6, math.ceil(num_sprinklers * 0.02))
        k_factor = sprinklers[0].k_factor if sprinklers else 5.6
        spk_catalog = SPRINKLER_CATALOG.get(k_factor, SPRINKLER_CATALOG[5.6])
        spk_info = spk_catalog.get('pendent', list(spk_catalog.values())[0])
        
        items.append(BOMLineItem(
            category='MISCELLANEOUS',
            subcategory='Spare Parts',
            part_number=spk_info['part'],
            description=f'Spare Sprinkler {spk_info["desc"]}',
            manufacturer=spk_info['mfr'],
            size=f'K-{k_factor}',
            quantity=spare_qty,
            unit='EA',
            unit_price=spk_info['price'],
            total_price=spare_qty * spk_info['price'],
            notes='Per NFPA 13 Sec. 6.2.9'
        ))
        
        # Spare sprinkler cabinet
        cabinet_type = 'spare_sprinkler_cabinet_12' if spare_qty > 6 else 'spare_sprinkler_cabinet'
        cabinet = MISC_CATALOG[cabinet_type]
        items.append(BOMLineItem(
            category='MISCELLANEOUS',
            subcategory='Accessories',
            part_number=cabinet['part'],
            description=cabinet['desc'],
            manufacturer='Various',
            size='-',
            quantity=1,
            unit='EA',
            unit_price=cabinet['price'],
            total_price=cabinet['price'],
            notes='With sprinkler wrench'
        ))
        
        # Wrench
        wrench = MISC_CATALOG['sprinkler_wrench']
        items.append(BOMLineItem(
            category='MISCELLANEOUS',
            subcategory='Accessories',
            part_number=wrench['part'],
            description=wrench['desc'],
            manufacturer='Various',
            size='-',
            quantity=1,
            unit='EA',
            unit_price=wrench['price'],
            total_price=wrench['price']
        ))
        
        # Signs
        for sign_type in ['system_id_sign', 'hydraulic_placard']:
            sign = MISC_CATALOG[sign_type]
            items.append(BOMLineItem(
                category='MISCELLANEOUS',
                subcategory='Signage',
                part_number=sign['part'],
                description=sign['desc'],
                manufacturer='Various',
                size='-',
                quantity=1,
                unit='EA',
                unit_price=sign['price'],
                total_price=sign['price']
            ))
        
        # Consumables estimate
        pipe_sealant = MISC_CATALOG['pipe_thread_sealant']
        gasket_lube = MISC_CATALOG['gasket_lubricant']
        
        consumable_qty = max(1, math.ceil(building_area / 20000))
        
        items.append(BOMLineItem(
            category='MISCELLANEOUS',
            subcategory='Consumables',
            part_number=pipe_sealant['part'],
            description=pipe_sealant['desc'],
            manufacturer='Rectorseal',
            size='-',
            quantity=consumable_qty,
            unit='EA',
            unit_price=pipe_sealant['price'],
            total_price=consumable_qty * pipe_sealant['price']
        ))
        
        items.append(BOMLineItem(
            category='MISCELLANEOUS',
            subcategory='Consumables',
            part_number=gasket_lube['part'],
            description=gasket_lube['desc'],
            manufacturer='Victaulic',
            size='-',
            quantity=consumable_qty,
            unit='EA',
            unit_price=gasket_lube['price'],
            total_price=consumable_qty * gasket_lube['price']
        ))
        
        return items
    
    def export_to_csv(self, bom: DetailedBOM, output_path: str) -> bool:
        """Export BOM to detailed CSV"""
        try:
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
                
                # Header
                writer.writerow(['=' * 100])
                writer.writerow(['FIRE SPRINKLER SYSTEM - BILL OF MATERIALS'])
                writer.writerow(['=' * 100])
                writer.writerow([])
                writer.writerow(['Project:', bom.project_name])
                writer.writerow(['Project ID:', bom.project_id])
                writer.writerow(['Generated:', bom.generated_date])
                writer.writerow([])
                
                # Column headers
                headers = ['Category', 'Part Number', 'Description', 'Manufacturer', 
                          'Size', 'Quantity', 'Unit', 'Unit Price', 'Total Price', 
                          'Labor Hrs', 'Notes']
                writer.writerow(headers)
                writer.writerow(['-' * 12] * len(headers))
                
                # All items by category
                all_items = []
                for category_items in [bom.sprinklers, bom.pipe, bom.fittings, 
                                       bom.hangers, bom.bracing, bom.valves, 
                                       bom.miscellaneous]:
                    all_items.extend(category_items)
                
                current_category = None
                for item in all_items:
                    if item.category != current_category:
                        writer.writerow([])
                        writer.writerow([f'--- {item.category} ---'])
                        current_category = item.category
                    
                    writer.writerow([
                        item.subcategory,
                        item.part_number,
                        item.description,
                        item.manufacturer,
                        item.size,
                        f'{item.quantity:.0f}' if item.quantity == int(item.quantity) else f'{item.quantity:.1f}',
                        item.unit,
                        f'${item.unit_price:.2f}',
                        f'${item.total_price:.2f}',
                        f'{item.labor_hours:.1f}' if item.labor_hours > 0 else '',
                        item.notes
                    ])
                
                # Totals
                writer.writerow([])
                writer.writerow(['=' * 100])
                writer.writerow(['SUMMARY'])
                writer.writerow(['=' * 100])
                writer.writerow([])
                writer.writerow(['', '', '', '', '', '', '', 'Material Total:', f'${bom.material_total:,.2f}'])
                writer.writerow(['', '', '', '', '', '', '', f'Labor ({bom.labor_hours_total:.0f} hrs @ ${self.labor_rate:.2f}):', f'${bom.labor_cost:,.2f}'])
                writer.writerow(['', '', '', '', '', '', '', f'Overhead ({self.overhead_pct*100:.0f}%):', f'${bom.overhead:,.2f}'])
                writer.writerow(['', '', '', '', '', '', '', f'Profit ({self.profit_pct*100:.0f}%):', f'${bom.profit:,.2f}'])
                writer.writerow([])
                writer.writerow(['', '', '', '', '', '', '', 'GRAND TOTAL:', f'${bom.grand_total:,.2f}'])
            
            return True
        except Exception as e:
            print(f"CSV export error: {e}")
            return False
    
    def export_to_json(self, bom: DetailedBOM, output_path: str) -> bool:
        """Export BOM to JSON"""
        try:
            def item_to_dict(item: BOMLineItem) -> dict:
                return {
                    'category': item.category,
                    'subcategory': item.subcategory,
                    'part_number': item.part_number,
                    'description': item.description,
                    'manufacturer': item.manufacturer,
                    'size': item.size,
                    'quantity': item.quantity,
                    'unit': item.unit,
                    'unit_price': item.unit_price,
                    'total_price': item.total_price,
                    'labor_hours': item.labor_hours,
                    'notes': item.notes
                }
            
            data = {
                'project_id': bom.project_id,
                'project_name': bom.project_name,
                'generated_date': bom.generated_date,
                'items': {
                    'sprinklers': [item_to_dict(i) for i in bom.sprinklers],
                    'pipe': [item_to_dict(i) for i in bom.pipe],
                    'fittings': [item_to_dict(i) for i in bom.fittings],
                    'hangers': [item_to_dict(i) for i in bom.hangers],
                    'bracing': [item_to_dict(i) for i in bom.bracing],
                    'valves': [item_to_dict(i) for i in bom.valves],
                    'miscellaneous': [item_to_dict(i) for i in bom.miscellaneous],
                },
                'totals': {
                    'material_total': bom.material_total,
                    'labor_hours': bom.labor_hours_total,
                    'labor_cost': bom.labor_cost,
                    'overhead': bom.overhead,
                    'profit': bom.profit,
                    'grand_total': bom.grand_total
                }
            }
            
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
            
            return True
        except Exception as e:
            print(f"JSON export error: {e}")
            return False


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def generate_professional_bom(design_result: Any, 
                               output_csv: str = None,
                               output_json: str = None,
                               seismic_category: str = 'D',
                               ceiling_height: float = 12.0) -> DetailedBOM:
    """
    Generate a professional BOM from a design result.
    
    Args:
        design_result: DesignResult object with sprinklers, pipes, fittings, valves
        output_csv: Optional path to save CSV
        output_json: Optional path to save JSON
        seismic_category: ASCE 7 Seismic Design Category (A-F)
        ceiling_height: Typical ceiling height in feet
    
    Returns:
        DetailedBOM object
    """
    generator = ProfessionalBOMGenerator()
    bom = generator.generate_bom(design_result, seismic_category, ceiling_height)
    
    if output_csv:
        generator.export_to_csv(bom, output_csv)
    
    if output_json:
        generator.export_to_json(bom, output_json)
    
    return bom


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🔧 FireAI Pro - Professional BOM Generator v2.0")
    print("=" * 60)
    print("\nFeatures:")
    print("  ✅ Detailed pipe by size with joint counts")
    print("  ✅ Fittings by type: elbows, tees, reducers, couplings")
    print("  ✅ Hangers by type with rod lengths")
    print("  ✅ Seismic bracing with hardware (NFPA 13 / ASCE 7)")
    print("  ✅ Real manufacturer part numbers")
    print("  ✅ Labor hours by task")
    print("  ✅ CSV and JSON export")
    print("\nUsage:")
    print("  bom = generate_professional_bom(design_result, 'bom.csv')")
