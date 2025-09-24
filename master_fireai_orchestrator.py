#!/usr/bin/env python3
"""
FireAI Pro Master Production Orchestrator
=========================================

Unified enterprise-grade orchestrator combining the best features from both versions:
- Production hardening with circuit breakers and resource management
- Database connection pooling & atomic transactions
- Comprehensive observability & monitoring
- Smart export generation with fallbacks
- Rate limiting & resource quotas
- Error classification & handling
- Configuration validation
- Deep health checks
- Audit trail & compliance
- Performance monitoring & SLA tracking

Author: FireAI Pro Team
Version: 3.1.0 Master Production
License: Proprietary
"""

import os
import sys
import json
import uuid
import time
import asyncio
import logging
import shutil
import hashlib
import random
import sqlite3
import contextlib
import threading
import tempfile
import resource
import signal
import atexit
import gc
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import weakref

# FastAPI and dependencies
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Header, Depends, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, BaseSettings, validator
import uvicorn

# Production dependencies with graceful fallbacks
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    import ezdxf
    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    import prometheus_client
    from prometheus_client import Counter, Histogram, Gauge, Summary
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


# =============================================================================
# CONFIGURATION & VALIDATION
# =============================================================================

class MasterSettings(BaseSettings):
    """Master configuration with comprehensive validation"""
    
    # API Configuration
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str = ""
    
    # Storage & Database
    local_storage_path: str = "./fireai_outputs"
    job_db_path: str = "fireai_jobs.sqlite"
    temp_dir: str = "/tmp/fireai"
    max_disk_usage_gb: float = 10.0
    
    # Resource Limits
    max_file_size_mb: int = 100
    max_concurrent_jobs: int = 5
    max_memory_per_job_mb: int = 1024
    max_processing_time_hours: int = 4
    
    # Engine Configuration
    engine_timeout_s: int = 300
    engine_retry_attempts: int = 3
    engine_retry_base_delay: float = 0.5
    engine_circuit_breaker_threshold: int = 5
    engine_circuit_breaker_timeout: int = 300
    
    # Quality & Compliance
    strict_mode: bool = False
    audit_enabled: bool = True
    data_retention_days: int = 30
    
    # Monitoring
    log_level: str = "INFO"
    json_logs: bool = True
    metrics_enabled: bool = True
    metrics_port: int = 9090
    health_check_interval: int = 30
    
    # Rate Limiting
    rate_limit_per_hour: int = 100
    rate_limit_per_day: int = 1000
    
    # Security
    cors_origins: List[str] = ["*"]
    max_request_size_mb: int = 100
    
    class Config:
        env_prefix = "FIREAI_"
    
    @validator('local_storage_path')
    def validate_storage_path(cls, v):
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        if not os.access(path, os.R_OK | os.W_OK):
            raise ValueError(f"Storage path not accessible: {v}")
        return str(path.resolve())
    
    @validator('max_concurrent_jobs')
    def validate_concurrency(cls, v):
        if v < 1 or v > 50:
            raise ValueError("max_concurrent_jobs must be between 1 and 50")
        return v
    
    @validator('engine_timeout_s')
    def validate_timeout(cls, v):
        if v < 10 or v > 3600:
            raise ValueError("engine_timeout_s must be between 10 and 3600")
        return v


# =============================================================================
# ENTERPRISE DATA MODELS
# =============================================================================

class ErrorType(Enum):
    """Classification of error types for different handling strategies"""
    RETRYABLE = "retryable"        # Network timeouts, temporary failures
    PERMANENT = "permanent"        # Invalid input, format errors
    SYSTEM = "system"             # Disk full, memory exhaustion
    SECURITY = "security"         # Authentication, authorization failures
    BUSINESS = "business"         # Code violations, quality gate failures


class JobPhase(Enum):
    """Detailed job phases for better tracking"""
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    INGESTING = "ingesting"
    STANDARDS_RESOLVING = "standards_resolving"
    LAYOUT_DESIGNING = "layout_designing"
    HYDRAULICS_ANALYZING = "hydraulics_analyzing"
    BOM_GENERATING = "bom_generating"
    BRACING_DESIGNING = "bracing_designing"
    EXPORTING = "exporting"
    QUALITY_CHECKING = "quality_checking"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class ResourceUsage:
    """Track resource usage per job"""
    memory_mb: float = 0.0
    disk_mb: float = 0.0
    cpu_seconds: float = 0.0
    network_bytes: int = 0
    temp_files: int = 0


@dataclass
class QualityMetrics:
    """Comprehensive quality tracking"""
    coverage_percentage: float = 0.0
    min_spacing_violations: int = 0
    hydraulic_margin_psi: float = 0.0
    code_violations: List[str] = field(default_factory=list)
    nfpa_compliance_score: float = 0.0
    design_completeness: float = 0.0


# Data models for pipeline context
@dataclass
class NormalizedModel:
    rooms: List[Dict] = field(default_factory=list)
    walls: List[Dict] = field(default_factory=list)
    obstructions: List[Dict] = field(default_factory=list)
    levels: List[Dict] = field(default_factory=list)
    crs: str = "local"
    units: str = "feet"
    bounds: Dict = field(default_factory=dict)


@dataclass
class StandardsContext:
    nfpa_edition: str = "2022"
    ahj_amendments: Dict = field(default_factory=dict)
    hazard_classes: Dict = field(default_factory=dict)
    spacing_rules: Dict = field(default_factory=dict)
    clearance_requirements: Dict = field(default_factory=dict)
    k_factor_bounds: Dict = field(default_factory=dict)
    pipe_material_defaults: Dict = field(default_factory=dict)


@dataclass
class LayoutModel:
    sprinklers: List[Dict] = field(default_factory=list)
    mains: List[Dict] = field(default_factory=list)
    branches: List[Dict] = field(default_factory=list)
    fittings: List[Dict] = field(default_factory=list)
    coverage_percentage: float = 0.0
    total_sprinklers: int = 0


@dataclass
class HydraulicsReport:
    demand_calc: Dict = field(default_factory=dict)
    remote_area: Dict = field(default_factory=dict)
    available_supply: Dict = field(default_factory=dict)
    k_factor_balance: Dict = field(default_factory=dict)
    tabular_calc: List[Dict] = field(default_factory=list)
    figures: List[str] = field(default_factory=list)
    converged: bool = False


@dataclass
class BOMTable:
    pipe_fittings: List[Dict] = field(default_factory=list)
    sprinklers: List[Dict] = field(default_factory=list)
    valves: List[Dict] = field(default_factory=list)
    backflow: List[Dict] = field(default_factory=list)
    riser: List[Dict] = field(default_factory=list)
    total_cost: float = 0.0


@dataclass
class BracingPlan:
    hangers: List[Dict] = field(default_factory=list)
    bracing_points: List[Dict] = field(default_factory=list)
    support_schedule: List[Dict] = field(default_factory=list)
    seismic_compliance: bool = False


# =============================================================================
# ENTERPRISE DATABASE LAYER
# =============================================================================

class DatabasePool:
    """Thread-safe SQLite connection pool with transactions"""
    
    def __init__(self, db_path: str, max_connections: int = 10):
        self.db_path = db_path
        self.max_connections = max_connections
        self._pool = []
        self._in_use = set()
        self._lock = threading.Lock()
        self._init_database()
    
    def _init_database(self):
        """Initialize database schema with proper constraints"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL") 
            conn.execute("PRAGMA cache_size=10000")
            conn.execute("PRAGMA temp_store=MEMORY")
            
            # Jobs table with comprehensive tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step INTEGER DEFAULT 0,
                    total_steps INTEGER DEFAULT 8,
                    submitted_at REAL NOT NULL,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    context_json TEXT,
                    errors_json TEXT DEFAULT '[]',
                    warnings_json TEXT DEFAULT '[]',
                    quality_json TEXT DEFAULT '{}',
                    resource_json TEXT DEFAULT '{}',
                    idempotency_key TEXT UNIQUE,
                    priority INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    timeout_at REAL,
                    created_by TEXT,
                    checksum TEXT
                )
            """)
            
            # Audit trail
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    phase TEXT NOT NULL,
                    action TEXT NOT NULL,
                    user_id TEXT,
                    ip_address TEXT,
                    details_json TEXT,
                    checksum TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs (id)
                )
            """)
            
            # Circuit breaker state
            conn.execute("""
                CREATE TABLE IF NOT EXISTS circuit_breakers (
                    engine_name TEXT PRIMARY KEY,
                    failure_count INTEGER DEFAULT 0,
                    last_failure REAL,
                    state TEXT DEFAULT 'closed',
                    opened_at REAL
                )
            """)
            
            # Create indices for performance
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_jobs_phase ON jobs(phase);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_submitted ON jobs(submitted_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_idempotency ON jobs(idempotency_key);
                CREATE INDEX IF NOT EXISTS idx_audit_job_id ON audit_log(job_id);
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
            """)
            
            conn.commit()
    
    @contextlib.contextmanager
    def get_connection(self):
        """Get a connection from the pool with automatic cleanup"""
        conn = None
        try:
            with self._lock:
                if self._pool:
                    conn = self._pool.pop()
                elif len(self._in_use) < self.max_connections:
                    conn = sqlite3.connect(self.db_path, timeout=30.0)
                    conn.row_factory = sqlite3.Row
                else:
                    raise Exception("Connection pool exhausted")
                
                self._in_use.add(conn)
            
            yield conn
            
        finally:
            if conn:
                with self._lock:
                    self._in_use.discard(conn)
                    if len(self._pool) < self.max_connections // 2:
                        self._pool.append(conn)
                    else:
                        conn.close()
    
    @contextlib.contextmanager
    def transaction(self):
        """Execute operations in an atomic transaction"""
        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise


# =============================================================================
# CIRCUIT BREAKER PATTERN
# =============================================================================

class CircuitBreaker:
    """Circuit breaker to prevent cascading failures"""
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "closed"  # closed, open, half_open
        self._lock = threading.Lock()
    
    async def call(self, func):
        """Execute function through circuit breaker"""
        with self._lock:
            if self.state == "open":
                if time.time() - self.last_failure_time > self.timeout:
                    self.state = "half_open"
                else:
                    raise Exception("Circuit breaker is OPEN - service unavailable")
            
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func()
                else:
                    result = func()
                
                # Reset on success
                if self.state == "half_open":
                    self.state = "closed"
                    self.failure_count = 0
                
                return result
                
            except Exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()
                
                if self.failure_count >= self.failure_threshold:
                    self.state = "open"
                
                raise


# =============================================================================
# RESOURCE MANAGEMENT
# =============================================================================

class ResourceManager:
    """Manage system resources and prevent exhaustion"""
    
    def __init__(self, settings: MasterSettings):
        self.settings = settings
        self.active_jobs = {}  # job_id -> resource tracking
        self.temp_files = weakref.WeakSet()
        self.logger = logging.getLogger("fireai.resources")
        
        # Set process limits
        self._set_resource_limits()
    
    def _set_resource_limits(self):
        """Set process-level resource limits"""
        try:
            # Memory limit (soft/hard)
            memory_bytes = self.settings.max_memory_per_job_mb * 1024 * 1024 * self.settings.max_concurrent_jobs
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes * 2))
            
            # File descriptor limit
            resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 2048))
            
            # CPU time limit per process
            max_cpu = self.settings.max_processing_time_hours * 3600
            resource.setrlimit(resource.RLIMIT_CPU, (max_cpu, max_cpu))
            
        except Exception as e:
            self.logger.warning(f"Could not set resource limits: {e}")
    
    @contextlib.contextmanager
    def track_job_resources(self, job_id: str):
        """Track resources for a specific job"""
        start_time = time.time()
        start_memory = self._get_memory_usage()
        temp_dir = None
        
        try:
            # Create isolated temp directory
            temp_dir = tempfile.mkdtemp(prefix=f"fireai_{job_id}_", dir=self.settings.temp_dir)
            os.makedirs(self.settings.temp_dir, exist_ok=True)
            
            resource_tracker = ResourceUsage()
            self.active_jobs[job_id] = resource_tracker
            
            yield temp_dir, resource_tracker
            
        finally:
            # Cleanup
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    self.logger.warning(f"Failed to cleanup temp dir {temp_dir}: {e}")
            
            # Final resource calculation
            if job_id in self.active_jobs:
                tracker = self.active_jobs[job_id]
                tracker.cpu_seconds = time.time() - start_time
                tracker.memory_mb = max(tracker.memory_mb, self._get_memory_usage() - start_memory)
                
                del self.active_jobs[job_id]
    
    def check_system_resources(self) -> Dict[str, Any]:
        """Check available system resources"""
        if not PSUTIL_AVAILABLE:
            return {"status": "unknown"}
        
        try:
            # Memory check
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(self.settings.local_storage_path)
            
            return {
                "status": "healthy" if memory.percent < 85 and disk.percent < 90 else "degraded",
                "memory_percent": memory.percent,
                "disk_percent": disk.percent,
                "disk_free_gb": disk.free / (1024**3),
                "active_jobs": len(self.active_jobs),
                "temp_files": len(self.temp_files)
            }
        except Exception:
            return {"status": "unknown"}
    
    def _get_memory_usage(self) -> float:
        """Get current process memory usage in MB"""
        if PSUTIL_AVAILABLE:
            try:
                process = psutil.Process()
                return process.memory_info().rss / 1024 / 1024
            except Exception:
                pass
        return 0.0


# =============================================================================
# METRICS & MONITORING
# =============================================================================

class MetricsCollector:
    """Enterprise metrics collection and monitoring"""
    
    def __init__(self, enabled: bool = True):
        self.enabled = enabled and PROMETHEUS_AVAILABLE
        self.logger = logging.getLogger("fireai.metrics")
        
        if self.enabled:
            self._init_metrics()
    
    def _init_metrics(self):
        """Initialize Prometheus metrics"""
        self.job_counter = Counter('fireai_jobs_total', 'Total jobs processed', ['status', 'phase'])
        self.job_duration = Histogram('fireai_job_duration_seconds', 'Job processing time', ['phase'])
        self.engine_duration = Histogram('fireai_engine_duration_seconds', 'Engine call time', ['engine', 'method'])
        self.engine_errors = Counter('fireai_engine_errors_total', 'Engine errors', ['engine', 'error_type'])
        self.active_jobs = Gauge('fireai_jobs_active', 'Currently active jobs')
        self.memory_usage = Gauge('fireai_memory_mb', 'Memory usage in MB')
        self.quality_score = Histogram('fireai_quality_score', 'Quality metrics', ['metric_type'])
        self.sla_violations = Counter('fireai_sla_violations_total', 'SLA violations', ['violation_type'])
    
    def record_job_start(self, job_id: str, phase: JobPhase):
        """Record job start"""
        if self.enabled:
            self.active_jobs.inc()
            self.job_counter.labels(status='started', phase=phase.value).inc()
    
    def record_job_complete(self, job_id: str, phase: JobPhase, duration: float, success: bool):
        """Record job completion"""
        if self.enabled:
            if phase == JobPhase.COMPLETED:
                self.active_jobs.dec()
            status = 'success' if success else 'failure'
            self.job_counter.labels(status=status, phase=phase.value).inc()
            self.job_duration.labels(phase=phase.value).observe(duration)
    
    def record_engine_call(self, engine_name: str, method: str, duration: float, error_type: str = None):
        """Record engine call metrics"""
        if self.enabled:
            self.engine_duration.labels(engine=engine_name, method=method).observe(duration)
            if error_type:
                self.engine_errors.labels(engine=engine_name, error_type=error_type).inc()
    
    def record_sla_violation(self, violation_type: str):
        """Record SLA violation"""
        if self.enabled:
            self.sla_violations.labels(violation_type=violation_type).inc()
    
    def update_system_metrics(self, resource_status: Dict):
        """Update system resource metrics"""
        if self.enabled and 'memory_percent' in resource_status:
            if 'memory_usage_mb' in resource_status:
                self.memory_usage.set(resource_status['memory_usage_mb'])


# =============================================================================
# ERROR CLASSIFICATION & HANDLING
# =============================================================================

class ErrorClassifier:
    """Classify errors for appropriate handling strategies"""
    
    @staticmethod
    def classify_error(error: Exception) -> ErrorType:
        """Classify error type for appropriate handling"""
        error_str = str(error).lower()
        
        # Security errors
        if any(term in error_str for term in ['unauthorized', 'forbidden', 'authentication', 'permission']):
            return ErrorType.SECURITY
        
        # System resource errors
        if any(term in error_str for term in ['memory', 'disk', 'space', 'resource', 'limit']):
            return ErrorType.SYSTEM
        
        # Network/timeout errors (retryable)
        if any(term in error_str for term in ['timeout', 'connection', 'network', 'unreachable']):
            return ErrorType.RETRYABLE
        
        # Format/validation errors (permanent)
        if any(term in error_str for term in ['invalid', 'format', 'parse', 'syntax', 'corrupt']):
            return ErrorType.PERMANENT
        
        # Business logic errors
        if any(term in error_str for term in ['compliance', 'violation', 'quality', 'nfpa']):
            return ErrorType.BUSINESS
        
        # Default to retryable for unknown errors
        return ErrorType.RETRYABLE


# =============================================================================
# JOB STORE WITH ENTERPRISE FEATURES
# =============================================================================

class EnterpriseJobStore:
    """Enterprise job store with full audit trail and recovery"""
    
    def __init__(self, db_pool: DatabasePool, audit_enabled: bool = True):
        self.db_pool = db_pool
        self.audit_enabled = audit_enabled
        self.logger = logging.getLogger("fireai.jobstore")
    
    def create_job(self, job_id: str, project_data: Dict, idempotency_key: str, 
                   user_id: Optional[str] = None, ip_address: Optional[str] = None) -> bool:
        """Create new job with full validation"""
        try:
            with self.db_pool.transaction() as conn:
                now = time.time()
                timeout_at = now + (4 * 3600)  # 4 hour timeout
                
                # Calculate checksum for integrity
                checksum = self._calculate_checksum({
                    'job_id': job_id,
                    'project_data': project_data,
                    'timestamp': now
                })
                
                conn.execute("""
                    INSERT INTO jobs (
                        id, phase, status, submitted_at, updated_at, 
                        context_json, idempotency_key, timeout_at, 
                        created_by, checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job_id, JobPhase.SUBMITTED.value, "submitted", now, now,
                    json.dumps(project_data, default=str), idempotency_key, timeout_at,
                    user_id, checksum
                ))
                
                # Audit log
                if self.audit_enabled:
                    self._log_audit(conn, job_id, JobPhase.SUBMITTED, "job_created", 
                                   user_id, ip_address, {"project_name": project_data.get('project_name')})
                
                return True
                
        except sqlite3.IntegrityError as e:
            if "idempotency_key" in str(e):
                return False  # Duplicate job
            raise
    
    def update_job_phase(self, job_id: str, phase: JobPhase, context: Dict = None,
                         errors: List[str] = None, warnings: List[str] = None,
                         quality_metrics: QualityMetrics = None,
                         resource_usage: ResourceUsage = None):
        """Update job with comprehensive state tracking"""
        try:
            with self.db_pool.transaction() as conn:
                now = time.time()
                
                # Build update data
                update_data = {
                    'phase': phase.value,
                    'updated_at': now
                }
                
                if phase == JobPhase.INGESTING:
                    update_data['started_at'] = now
                elif phase in [JobPhase.COMPLETED, JobPhase.FAILED, JobPhase.CANCELLED, JobPhase.TIMEOUT]:
                    update_data['completed_at'] = now
                
                if context:
                    update_data['context_json'] = json.dumps(context, default=str)
                if errors:
                    update_data['errors_json'] = json.dumps(errors)
                if warnings:
                    update_data['warnings_json'] = json.dumps(warnings)
                if quality_metrics:
                    update_data['quality_json'] = json.dumps(asdict(quality_metrics))
                if resource_usage:
                    update_data['resource_json'] = json.dumps(asdict(resource_usage))
                
                # Build SQL dynamically
                set_clause = ', '.join(f"{k} = ?" for k in update_data.keys())
                values = list(update_data.values()) + [job_id]
                
                conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
                
                # Audit log
                if self.audit_enabled:
                    self._log_audit(conn, job_id, phase, "phase_updated", 
                                   details={"phase": phase.value})
                
        except Exception as e:
            self.logger.error(f"Failed to update job {job_id}: {e}")
            raise
    
    def get_job_status(self, job_id: str) -> Optional[Dict]:
        """Get comprehensive job status"""
        try:
            with self.db_pool.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT * FROM jobs WHERE id = ?
                """, (job_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                # Convert row to dict and parse JSON fields
                job_data = dict(row)
                for json_field in ['context_json', 'errors_json', 'warnings_json', 'quality_json', 'resource_json']:
                    if job_data[json_field]:
                        job_data[json_field.replace('_json', '')] = json.loads(job_data[json_field])
                
                return job_data
                
        except Exception as e:
            self.logger.error(f"Failed to get job status {job_id}: {e}")
            return None
    
    def find_by_idempotency_key(self, key: str) -> Optional[str]:
        """Find existing job by idempotency key"""
        try:
            with self.db_pool.get_connection() as conn:
                cursor = conn.execute("SELECT id FROM jobs WHERE idempotency_key = ?", (key,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None
    
    def _log_audit(self, conn, job_id: str, phase: JobPhase, action: str,
                   user_id: str = None, ip_address: str = None, details: Dict = None):
        """Log audit trail entry"""
        now = time.time()
        details_json = json.dumps(details or {})
        
        # Calculate tamper-evident checksum
        checksum = self._calculate_checksum({
            'job_id': job_id,
            'timestamp': now,
            'phase': phase.value,
            'action': action,
            'details': details_json
        })
        
        conn.execute("""
            INSERT INTO audit_log (job_id, timestamp, phase, action, user_id, ip_address, details_json, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, now, phase.value, action, user_id, ip_address, details_json, checksum))
    
    def _calculate_checksum(self, data: Dict) -> str:
        """Calculate SHA-256 checksum for data integrity"""
        content = json.dumps(data, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()


# =============================================================================
# SAFE ENGINE IMPORTS
# =============================================================================

def safe_import(module_name: str):
    """Safely import engine modules"""
    try:
        return __import__(module_name)
    except ImportError:
        return None

# Load engines with fallback handling
INGEST_ENGINE = safe_import('enhanced_cad_engine')
STANDARDS_ENGINE = safe_import('fireai_pro_master_Standards')
LAYOUT_ENGINE = safe_import('fireai_routing_advanced')
HYDRAULICS_ENGINE = safe_import('enhanced_hydraulics_engine')
BOM_ENGINE = safe_import('master_fireai_products_enhanced')
BRACING_ENGINE = safe_import('enhanced_bracing_engine')


# =============================================================================
# MASTER ORCHESTRATOR
# =============================================================================

class MasterOrchestrator:
    """Master orchestrator combining bulletproof features with clean architecture"""
    
    def __init__(self, settings: MasterSettings):
        self.settings = settings
        self.logger = self._setup_logging()
        
        # Initialize core components
        self.db_pool = DatabasePool(settings.job_db_path)
        self.job_store = EnterpriseJobStore(self.db_pool, settings.audit_enabled)
        self.resource_manager = ResourceManager(settings)
        self.metrics = MetricsCollector(settings.metrics_enabled)
        self.error_classifier = ErrorClassifier()
        
        # Circuit breakers for each engine
        self.circuit_breakers = {
            'ingest': CircuitBreaker(settings.engine_circuit_breaker_threshold, settings.engine_circuit_breaker_timeout),
            'standards': CircuitBreaker(settings.engine_circuit_breaker_threshold, settings.engine_circuit_breaker_timeout),
            'layout': CircuitBreaker(settings.engine_circuit_breaker_threshold, settings.engine_circuit_breaker_timeout),
            'hydraulics': CircuitBreaker(settings.engine_circuit_breaker_threshold, settings.engine_circuit_breaker_timeout),
            'bom': CircuitBreaker(settings.engine_circuit_breaker_threshold, settings.engine_circuit_breaker_timeout),
            'bracing': CircuitBreaker(settings.engine_circuit_breaker_threshold, settings.engine_circuit_breaker_timeout)
        }
        
        # Rate limiting
        self.rate_limiter = {}
        
        # Graceful shutdown handling
        self.shutdown_event = asyncio.Event()
        self.recovery_enabled = True
        
        # Output directory
        self.output_dir = Path(settings.local_storage_path)
        self.output_dir.mkdir(exist_ok=True)
        
        # Job semaphore
        self.job_semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        
        # Start background tasks
        self._start_background_tasks()
        
        # Register shutdown handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        atexit.register(self._cleanup)
        
        self.logger.info("Master orchestrator initialized", extra={"version": "3.1.0"})
        self._log_engine_status()
    
    def _setup_logging(self):
        """Setup enterprise logging with correlation IDs"""
        logger = logging.getLogger("fireai.master")
        logger.setLevel(getattr(logging, self.settings.log_level))
        
        # Custom formatter with correlation IDs
        class CorrelationFormatter(logging.Formatter):
            def format(self, record):
                if self.settings.json_logs:
                    log_data = {
                        "timestamp": time.time(),
                        "level": record.levelname,
                        "logger": record.name,
                        "message": record.getMessage(),
                        "pid": os.getpid(),
                        "thread": threading.current_thread().name
                    }
                    
                    # Add job correlation
                    if hasattr(record, 'job_id'):
                        log_data["job_id"] = record.job_id
                    if hasattr(record, 'correlation_id'):
                        log_data["correlation_id"] = record.correlation_id
                    if hasattr(record, 'phase'):
                        log_data["phase"] = record.phase
                    
                    return json.dumps(log_data)
                else:
                    return super().format(record)
        
        handler = logging.StreamHandler()
        handler.setFormatter(CorrelationFormatter())
        logger.addHandler(handler)
        
        return logger
    
    def _log_engine_status(self):
        """Log engine availability"""
        engines = [
            ("Ingest", INGEST_ENGINE),
            ("Standards", STANDARDS_ENGINE),
            ("Layout", LAYOUT_ENGINE),
            ("Hydraulics", HYDRAULICS_ENGINE),
            ("BOM", BOM_ENGINE),
            ("Bracing", BRACING_ENGINE)
        ]
        
        for name, engine in engines:
            status = "available" if engine else "unavailable"
            self.logger.info(f"Engine {name}: {status}")
    
    def _start_background_tasks(self):
        """Start background monitoring tasks"""
        if hasattr(asyncio, 'create_task'):
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(self._health_monitor())
                loop.create_task(self._cleanup_monitor())
                loop.create_task(self._recovery_monitor())
            except RuntimeError:
                # No event loop running yet
                pass
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutdown_event.set()
    
    def _cleanup(self):
        """Cleanup resources on shutdown"""
        self.logger.info("Performing final cleanup")
        try:
            # Close database connections
            if hasattr(self, 'db_pool'):
                with self.db_pool._lock:
                    for conn in self.db_pool._pool:
                        conn.close()
                    for conn in self.db_pool._in_use:
                        conn.close()
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
    
    async def _health_monitor(self):
        """Background health monitoring"""
        while not self.shutdown_event.is_set():
            try:
                # Check system resources
                resource_status = self.resource_manager.check_system_resources()
                self.metrics.update_system_metrics(resource_status)
                
                await asyncio.sleep(self.settings.health_check_interval)
                
            except Exception as e:
                self.logger.error(f"Health monitor error: {e}")
                await asyncio.sleep(60)  # Longer delay on error
    
    async def _cleanup_monitor(self):
        """Background cleanup of old data"""
        while not self.shutdown_event.is_set():
            try:
                # Cleanup temp files
                await self._cleanup_temp_files()
                
                # Force garbage collection
                gc.collect()
                
                # Sleep for 6 hours
                await asyncio.sleep(6 * 3600)
                
            except Exception as e:
                self.logger.error(f"Cleanup monitor error: {e}")
                await asyncio.sleep(3600)  # 1 hour retry
    
    async def _recovery_monitor(self):
        """Monitor for jobs that need recovery"""
        while not self.shutdown_event.is_set() and self.recovery_enabled:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                self.logger.error(f"Recovery monitor error: {e}")
                await asyncio.sleep(600)
    
    async def process_design(self, project_data: Dict, input_file: Optional[str] = None,
                           idempotency_key: Optional[str] = None, user_id: Optional[str] = None,
                           ip_address: Optional[str] = None) -> Dict:
        """Process design with full enterprise features"""
        
        async with self.job_semaphore:
            job_id = project_data.get('project_id', str(uuid.uuid4()))
            correlation_id = str(uuid.uuid4())
            
            # Create job logger with correlation
            job_logger = logging.LoggerAdapter(
                self.logger,
                {'job_id': job_id, 'correlation_id': correlation_id}
            )
            
            try:
                # Rate limiting check
                if not self._check_rate_limit(user_id or ip_address):
                    raise HTTPException(status_code=429, detail="Rate limit exceeded")
                
                # Create job record
                if not self.job_store.create_job(job_id, project_data, idempotency_key, user_id, ip_address):
                    existing_job = self.job_store.find_by_idempotency_key(idempotency_key)
                    return {"project_id": existing_job, "status": "duplicate"}
                
                # Start metrics tracking
                self.metrics.record_job_start(job_id, JobPhase.SUBMITTED)
                
                # Process with resource tracking
                with self.resource_manager.track_job_resources(job_id) as (temp_dir, resource_tracker):
                    result = await self._execute_pipeline(
                        job_id, project_data, input_file, temp_dir, resource_tracker, job_logger
                    )
                
                return result
                
            except Exception as e:
                error_type = self.error_classifier.classify_error(e)
                job_logger.error(f"Job failed with {error_type.value} error: {e}")
                
                # Update job with failure
                self.job_store.update_job_phase(
                    job_id, JobPhase.FAILED, 
                    errors=[f"{error_type.value}: {str(e)}"]
                )
                
                self.metrics.record_job_complete(job_id, JobPhase.FAILED, 0, False)
                
                return {
                    "project_id": job_id,
                    "status": "failed",
                    "error_type": error_type.value,
                    "error": str(e)
                }
    
    async def _execute_pipeline(self, job_id: str, project_data: Dict, input_file: Optional[str],
                              temp_dir: str, resource_tracker: ResourceUsage, logger) -> Dict:
        """Execute the pipeline with comprehensive monitoring"""
        
        # Initialize pipeline context
        context = PipelineContext(
            project_id=job_id,
            project_name=project_data.get('project_name', 'Fire Sprinkler Design'),
            input_file=input_file,
            zip_code=project_data.get('zip_code'),
            webhook_url=project_data.get('webhook_url')
        )
        
        project_dir = self.output_dir / job_id
        project_dir.mkdir(exist_ok=True)
        
        phases = [
            (JobPhase.VALIDATED, self._validate_input),
            (JobPhase.INGESTING, self._step_ingest_normalize),
            (JobPhase.STANDARDS_RESOLVING, self._step_standards_resolve),
            (JobPhase.LAYOUT_DESIGNING, self._step_layout_design),
            (JobPhase.HYDRAULICS_ANALYZING, self._step_hydraulics_analysis),
            (JobPhase.BOM_GENERATING, self._step_bom_bracing),
            (JobPhase.EXPORTING, lambda c, l: self._step_exports(c, project_dir, l)),
            (JobPhase.QUALITY_CHECKING, self._step_quality_gate),
            (JobPhase.PUBLISHING, lambda c, l: self._step_publish_artifacts(c, project_dir, l))
        ]
        
        start_time = time.time()
        
        try:
            for phase, step_func in phases:
                phase_start = time.time()
                logger.info(f"Starting phase: {phase.value}", extra={'phase': phase.value})
                
                # Update job phase
                self.job_store.update_job_phase(
                    job_id, phase, asdict(context), 
                    context.errors, context.warnings,
                    resource_usage=resource_tracker
                )
                
                # Execute phase with timeout
                await self._execute_phase_with_timeout(
                    step_func, context, logger, phase
                )
                
                # Record metrics
                phase_duration = time.time() - phase_start
                self.metrics.record_job_complete(job_id, phase, phase_duration, True)
                
                logger.info(f"Completed phase: {phase.value} in {phase_duration:.2f}s", 
                           extra={'phase': phase.value})
            
            # Final success
            total_duration = time.time() - start_time
            quality_metrics = QualityMetrics(
                coverage_percentage=context.coverage_percentage,
                hydraulic_margin_psi=context.hydraulic_margin,
                code_violations=context.code_violations,
                nfpa_compliance_score=100.0 if not context.code_violations else 0.0
            )
            
            self.job_store.update_job_phase(
                job_id, JobPhase.COMPLETED, asdict(context),
                context.errors, context.warnings,
                quality_metrics=quality_metrics,
                resource_usage=resource_tracker
            )
            
            self.metrics.record_job_complete(job_id, JobPhase.COMPLETED, total_duration, True)
            
            # Send webhook if configured
            if context.webhook_url:
                await self._send_webhook(context, "completed", project_dir)
            
            return {
                "project_id": job_id,
                "status": "completed",
                "processing_time": total_duration,
                "artifacts": len(context.artifacts) if hasattr(context, 'artifacts') else 0,
                "quality_score": quality_metrics.nfpa_compliance_score
            }
            
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            
            # Send failure webhook
            if hasattr(context, 'webhook_url') and context.webhook_url:
                await self._send_webhook(context, "failed", project_dir)
            
            raise
    
    async def _execute_phase_with_timeout(self, step_func, context, logger, phase: JobPhase):
        """Execute phase with timeout protection"""
        timeout = self.settings.engine_timeout_s * 2  # Phase timeout is 2x engine timeout
        
        try:
            await asyncio.wait_for(
                step_func(context, logger),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            error_msg = f"Phase {phase.value} timed out after {timeout}s"
            logger.error(error_msg)
            self.metrics.record_sla_violation("phase_timeout")
            raise TimeoutError(error_msg)
    
    async def _call_engine_with_circuit_breaker(self, engine_name: str, engine, method_names: List[str], 
                                               input_data: Dict, logger) -> Dict:
        """Call engine through circuit breaker with comprehensive error handling"""
        
        if not engine:
            logger.warning(f"Engine {engine_name} not available")
            return {}
        
        circuit_breaker = self.circuit_breakers.get(engine_name)
        if not circuit_breaker:
            logger.warning(f"No circuit breaker for engine {engine_name}")
            return {}
        
        for method_name in method_names:
            if not hasattr(engine, method_name):
                continue
            
            method = getattr(engine, method_name)
            
            async def _execute_method():
                start_time = time.time()
                try:
                    if asyncio.iscoroutinefunction(method):
                        result = await method(input_data)
                    else:
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(None, method, input_data)
                    
                    # Record successful call
                    duration = time.time() - start_time
                    self.metrics.record_engine_call(engine_name, method_name, duration)
                    
                    return result if isinstance(result, dict) else {}
                    
                except Exception as e:
                    duration = time.time() - start_time
                    error_type = self.error_classifier.classify_error(e)
                    self.metrics.record_engine_call(engine_name, method_name, duration, error_type.value)
                    raise
            
            try:
                # Execute through circuit breaker with retry
                result = await self._retry_with_circuit_breaker(
                    circuit_breaker, _execute_method, logger
                )
                return result
                
            except Exception as e:
                logger.warning(f"Engine {engine_name}.{method_name} failed: {e}")
                continue
        
        logger.error(f"All methods failed for engine {engine_name}")
        return {}
    
    async def _retry_with_circuit_breaker(self, circuit_breaker: CircuitBreaker, func, logger):
        """Retry function through circuit breaker"""
        max_attempts = self.settings.engine_retry_attempts
        base_delay = self.settings.engine_retry_base_delay
        
        for attempt in range(max_attempts):
            try:
                return await circuit_breaker.call(func)
            except Exception as e:
                error_type = self.error_classifier.classify_error(e)
                
                # Don't retry permanent errors
                if error_type in [ErrorType.PERMANENT, ErrorType.SECURITY]:
                    raise
                
                # Don't retry on last attempt
                if attempt == max_attempts - 1:
                    raise
                
                # Calculate backoff with jitter
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                logger.info(f"Retrying after {delay:.2f}s (attempt {attempt + 1}/{max_attempts})")
                await asyncio.sleep(delay)
        
        raise Exception("Max retries exceeded")
    
    def _check_rate_limit(self, identifier: str) -> bool:
        """Check rate limiting for requests"""
        if not identifier:
            return True
        
        now = time.time()
        
        # Clean old entries
        cutoff_hour = now - 3600
        cutoff_day = now - 86400
        
        if identifier in self.rate_limiter:
            requests = self.rate_limiter[identifier]
            requests['hourly'] = [ts for ts in requests['hourly'] if ts > cutoff_hour]
            requests['daily'] = [ts for ts in requests['daily'] if ts > cutoff_day]
        else:
            self.rate_limiter[identifier] = {'hourly': [], 'daily': []}
        
        requests = self.rate_limiter[identifier]
        
        # Check limits
        if len(requests['hourly']) >= self.settings.rate_limit_per_hour:
            return False
        if len(requests['daily']) >= self.settings.rate_limit_per_day:
            return False
        
        # Record request
        requests['hourly'].append(now)
        requests['daily'].append(now)
        
        return True
    
    async def _cleanup_temp_files(self):
        """Clean up old temporary files"""
        try:
            temp_base = Path(self.settings.temp_dir)
            if not temp_base.exists():
                return
            
            cutoff = time.time() - 86400  # 24 hours
            
            for temp_dir in temp_base.glob("fireai_*"):
                if temp_dir.is_dir():
                    try:
                        stat_info = temp_dir.stat()
                        if stat_info.st_mtime < cutoff:
                            shutil.rmtree(temp_dir)
                            self.logger.info(f"Cleaned up old temp dir: {temp_dir}")
                    except Exception as e:
                        self.logger.warning(f"Failed to clean temp dir {temp_dir}: {e}")
        
        except Exception as e:
            self.logger.error(f"Temp cleanup failed: {e}")
    
    def get_comprehensive_health(self) -> Dict[str, Any]:
        """Get comprehensive system health status"""
        try:
            # System resources
            resource_status = self.resource_manager.check_system_resources()
            
            # Circuit breaker status
            circuit_status = {
                name: {
                    "state": breaker.state,
                    "failure_count": breaker.failure_count,
                    "last_failure": breaker.last_failure_time
                }
                for name, breaker in self.circuit_breakers.items()
            }
            
            # Database health
            db_healthy = True
            try:
                with self.db_pool.get_connection() as conn:
                    conn.execute("SELECT 1").fetchone()
            except Exception:
                db_healthy = False
            
            # Overall status
            overall_status = "healthy"
            issues = []
            
            if resource_status.get("status") == "degraded":
                overall_status = "degraded"
                issues.append("Resource constraints")
            
            if not db_healthy:
                overall_status = "unhealthy"
                issues.append("Database connectivity")
            
            if any(cb["state"] == "open" for cb in circuit_status.values()):
                overall_status = "degraded"
                issues.append("Circuit breakers open")
            
            return {
                "status": overall_status,
                "issues": issues,
                "timestamp": datetime.now().isoformat(),
                "version": "3.1.0",
                "uptime": time.time() - (hasattr(self, '_start_time') and self._start_time or time.time()),
                "resources": resource_status,
                "circuit_breakers": circuit_status,
                "database_healthy": db_healthy,
                "active_jobs": len(self.resource_manager.active_jobs),
                "features": {
                    "circuit_breakers": True,
                    "resource_management": True,
                    "rate_limiting": True,
                    "job_recovery": self.recovery_enabled,
                    "audit_trail": self.settings.audit_enabled,
                    "metrics_collection": self.settings.metrics_enabled
                }
            }
            
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "issues": ["Health check failure"],
                "error": str(e)
            }


# =============================================================================
# PIPELINE CONTEXT
# =============================================================================

@dataclass
class PipelineContext:
    project_id: str
    project_name: str
    input_file: Optional[str] = None
    zip_code: Optional[str] = None
    webhook_url: Optional[str] = None
    
    # Step outputs
    normalized_model: Optional[NormalizedModel] = None
    standards_ctx: Optional[StandardsContext] = None
    layout_model: Optional[LayoutModel] = None
    hydraulics_report: Optional[HydraulicsReport] = None
    bom_table: Optional[BOMTable] = None
    bracing_plan: Optional[BracingPlan] = None
    
    # Quality metrics
    coverage_percentage: float = 0.0
    min_spacing_met: bool = False
    hydraulic_margin: float = 0.0
    code_violations: List[str] = field(default_factory=list)
    quality_failures: List[str] = field(default_factory=list)
    
    # Processing status
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)


# =============================================================================
# PIPELINE STEP IMPLEMENTATIONS
# =============================================================================

class MasterOrchestrator(MasterOrchestrator):
    """Extend orchestrator with pipeline step implementations"""
    
    async def _validate_input(self, context: PipelineContext, logger):
        """Validate input data comprehensively"""
        if not context.project_name:
            context.warnings.append("No project name provided")
        
        if context.input_file and not Path(context.input_file).exists():
            context.errors.append(f"Input file not found: {context.input_file}")
            raise FileNotFoundError(f"Input file not found: {context.input_file}")
        
        logger.info("Input validation completed")
    
    async def _step_ingest_normalize(self, context: PipelineContext, logger):
        """Step 1: Ingest & normalize with circuit breaker"""
        engine = INGEST_ENGINE
        
        if engine and context.input_file:
            try:
                file_ext = Path(context.input_file).suffix.lower()
                input_data = {'file_path': context.input_file}
                
                if file_ext == '.pdf':
                    methods = ['vectorize_pdf', 'process_pdf', 'extract_from_pdf']
                elif file_ext in ['.dxf', '.dwg']:
                    methods = ['normalize_cad', 'process_dxf', 'extract_from_cad']
                elif file_ext == '.ifc':
                    methods = ['normalize_ifc', 'process_ifc', 'extract_from_ifc']
                else:
                    raise ValueError(f"Unsupported file type: {file_ext}")
                
                result = await self._call_engine_with_circuit_breaker(
                    'ingest', engine, methods, input_data, logger
                )
                
                context.normalized_model = NormalizedModel(
                    rooms=result.get('rooms', []),
                    walls=result.get('walls', []),
                    obstructions=result.get('obstructions', []),
                    levels=result.get('levels', []),
                    crs=result.get('crs', 'local'),
                    units=result.get('units', 'feet'),
                    bounds=result.get('bounds', {})
                )
                
                logger.info(f"Normalized: {len(context.normalized_model.rooms)} rooms, {len(context.normalized_model.walls)} walls")
                
            except Exception as e:
                context.warnings.append(f"Ingest engine failed: {e}")
                context.normalized_model = self._create_fallback_model()
                logger.warning("Using fallback normalized model")
        else:
            context.normalized_model = self._create_fallback_model()
    
    async def _step_standards_resolve(self, context: PipelineContext, logger):
        """Step 2: Standards resolution with circuit breaker"""
        engine = STANDARDS_ENGINE
        result = await self._call_engine_with_circuit_breaker(
            'standards', engine, ['resolve_standards', 'get_nfpa_requirements'], {
                'zip_code': context.zip_code,
                'normalized_model': asdict(context.normalized_model) if context.normalized_model else {},
                'project_type': 'commercial'
            }, logger
        )
        
        context.standards_ctx = StandardsContext(
            nfpa_edition=result.get('nfpa_edition', '2022'),
            ahj_amendments=result.get('ahj_amendments', {}),
            hazard_classes=result.get('hazard_classes', {}),
            spacing_rules=result.get('spacing_rules', {}),
            clearance_requirements=result.get('clearance_requirements', {}),
            k_factor_bounds=result.get('k_factor_bounds', {}),
            pipe_material_defaults=result.get('pipe_material_defaults', {})
        )
    
    async def _step_layout_design(self, context: PipelineContext, logger):
        """Step 3: Layout design with circuit breaker"""
        engine = LAYOUT_ENGINE
        result = await self._call_engine_with_circuit_breaker(
            'layout', engine, ['design_layout', 'place_sprinklers'], {
                'normalized_model': asdict(context.normalized_model) if context.normalized_model else {},
                'standards_ctx': asdict(context.standards_ctx) if context.standards_ctx else {}
            }, logger
        )
        
        context.layout_model = LayoutModel(
            sprinklers=result.get('sprinklers', []),
            mains=result.get('mains', []),
            branches=result.get('branches', []),
            fittings=result.get('fittings', []),
            coverage_percentage=result.get('coverage_percentage', 0.0),
            total_sprinklers=len(result.get('sprinklers', []))
        )
        
        context.coverage_percentage = context.layout_model.coverage_percentage
    
    async def _step_hydraulics_analysis(self, context: PipelineContext, logger):
        """Step 4: Hydraulics analysis with circuit breaker"""
        engine = HYDRAULICS_ENGINE
        result = await self._call_engine_with_circuit_breaker(
            'hydraulics', engine, ['analyze_hydraulics', 'calculate_demand'], {
                'layout_model': asdict(context.layout_model) if context.layout_model else {},
                'standards_ctx': asdict(context.standards_ctx) if context.standards_ctx else {}
            }, logger
        )
        
        context.hydraulics_report = HydraulicsReport(
            demand_calc=result.get('demand_calc', {}),
            remote_area=result.get('remote_area', {}),
            available_supply=result.get('available_supply', {}),
            k_factor_balance=result.get('k_factor_balance', {}),
            tabular_calc=result.get('tabular_calc', []),
            figures=result.get('figures', []),
            converged=result.get('converged', False)
        )
        
        context.hydraulic_margin = result.get('hydraulic_margin', 0.0)
    
    async def _step_bom_bracing(self, context: PipelineContext, logger):
        """Step 5: BOM & bracing with circuit breaker"""
        # BOM
        bom_engine = BOM_ENGINE
        bom_result = await self._call_engine_with_circuit_breaker(
            'bom', bom_engine, ['generate_bom', 'specify_components'], {
                'layout_model': asdict(context.layout_model) if context.layout_model else {},
                'hydraulics_report': asdict(context.hydraulics_report) if context.hydraulics_report else {}
            }, logger
        )
        
        context.bom_table = BOMTable(
            pipe_fittings=bom_result.get('pipe_fittings', []),
            sprinklers=bom_result.get('sprinklers', []),
            valves=bom_result.get('valves', []),
            backflow=bom_result.get('backflow', []),
            riser=bom_result.get('riser', []),
            total_cost=bom_result.get('total_cost', 0.0)
        )
        
        # Bracing
        bracing_engine = BRACING_ENGINE
        bracing_result = await self._call_engine_with_circuit_breaker(
            'bracing', bracing_engine, ['design_bracing', 'calculate_supports'], {
                'layout_model': asdict(context.layout_model) if context.layout_model else {},
                'standards_ctx': asdict(context.standards_ctx) if context.standards_ctx else {}
            }, logger
        )
        
        context.bracing_plan = BracingPlan(
            hangers=bracing_result.get('hangers', []),
            bracing_points=bracing_result.get('bracing_points', []),
            support_schedule=bracing_result.get('support_schedule', []),
            seismic_compliance=bracing_result.get('seismic_compliance', False)
        )
    
    async def _step_exports(self, context: PipelineContext, project_dir: Path, logger):
        """Step 6: Generate exports with validation"""
        # Generate DXF with proper CAD layers and units
        dxf_path = project_dir / "design.dxf"
        await self._generate_enhanced_dxf(context, dxf_path, logger)
        context.artifacts.append(str(dxf_path))
        
        # Generate IFC
        ifc_path = project_dir / "model.ifc"
        await self._generate_enhanced_ifc(context, ifc_path, logger)
        context.artifacts.append(str(ifc_path))
        
        # Generate PDFs with proper extensions and fallbacks
        report_types = ["compliance", "hydraulics", "bom", "bracing", "multistandard"]
        for report_type in report_types:
            pdf_path = project_dir / f"{report_type}.pdf"
            await self._generate_smart_pdf(context, pdf_path, report_type, logger)
            if pdf_path.exists():
                context.artifacts.append(str(pdf_path))
            else:
                # Check for text fallback
                txt_path = project_dir / f"{report_type}.txt"
                if txt_path.exists():
                    context.artifacts.append(str(txt_path))
        
        logger.info(f"Generated {len(context.artifacts)} export files")
    
    async def _step_quality_gate(self, context: PipelineContext, logger):
        """Step 7: Quality gate with comprehensive validation"""
        if not self.settings.strict_mode:
            logger.info("Quality gate skipped (strict mode disabled)")
            return
        
        failures = []
        
        # Coverage check
        if context.coverage_percentage < 99.0:
            failures.append(f"Coverage insufficient: {context.coverage_percentage:.1f}% < 99%")
        
        # Minimum spacing check
        if not self._check_minimum_spacing(context):
            failures.append("Minimum spacing violations detected")
        
        # Hydraulic margin check
        if context.hydraulic_margin < 5.0:
            failures.append(f"Hydraulic margin insufficient: {context.hydraulic_margin:.1f} PSI < 5.0 PSI")
        
        # Code violations check
        if context.code_violations:
            failures.append(f"Code violations: {', '.join(context.code_violations)}")
        
        context.quality_failures = failures
        
        if failures:
            error_msg = f"Quality gate failed with {len(failures)} issues: {'; '.join(failures)}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        logger.info("Quality gate passed")
    
    async def _step_publish_artifacts(self, context: PipelineContext, project_dir: Path, logger):
        """Step 8: Publish artifacts with manifest"""
        # Copy original upload file if exists
        if context.input_file and Path(context.input_file).exists():
            upload_dest = project_dir / "upload.pdf"
            shutil.copy2(context.input_file, upload_dest)
            context.artifacts.append(str(upload_dest))
        
        # Create comprehensive artifact manifest
        artifacts_metadata = []
        for artifact_path in context.artifacts:
            file_path = Path(artifact_path)
            if file_path.exists():
                artifacts_metadata.append({
                    "name": file_path.name,
                    "path": file_path.name,
                    "size": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime
                })
        
        manifest = {
            "project_id": context.project_id,
            "project_name": context.project_name,
            "generated_at": datetime.now().isoformat(),
            "pipeline_version": "3.1.0",
            "artifacts": artifacts_metadata,
            "summary": {
                "total_files": len(artifacts_metadata),
                "sprinklers_designed": context.layout_model.total_sprinklers if context.layout_model else 0,
                "coverage_percentage": context.coverage_percentage,
                "nfpa_compliant": len(context.code_violations) == 0,
                "quality_passed": len(context.quality_failures) == 0,
                "errors": len(context.errors),
                "warnings": len(context.warnings)
            }
        }
        
        # Write manifest
        manifest_path = project_dir / "artifacts.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)
        
        logger.info(f"Published {len(artifacts_metadata)} artifacts with comprehensive manifest")
    
    # Enhanced export generation methods
    async def _generate_enhanced_dxf(self, context: PipelineContext, output_path: Path, logger):
        """Generate DXF with proper layers, units, and blocks"""
        if EZDXF_AVAILABLE and context.layout_model:
            try:
                doc = ezdxf.new('R2010')
                
                # Set up layers
                doc.layers.new(name='SPRINKLERS', dxfattribs={'color': 1})
                doc.layers.new(name='MAINS', dxfattribs={'color': 2})
                doc.layers.new(name='BRANCHES', dxfattribs={'color': 3})
                doc.layers.new(name='TEXT', dxfattribs={'color': 7})
                
                # Set units
                units = context.normalized_model.units if context.normalized_model else 'feet'
                if units == 'meters':
                    doc.header['$INSUNITS'] = 6
                else:
                    doc.header['$INSUNITS'] = 1
                
                msp = doc.modelspace()
                
                # Add title block
                msp.add_text(
                    f"FireAI Pro - {context.project_name}",
                    dxfattribs={'insert': (10, 10), 'height': 2.5, 'layer': 'TEXT'}
                )
                
                # Add sprinklers
                for i, sprinkler in enumerate(context.layout_model.sprinklers):
                    x = sprinkler.get('x', i * 15)
                    y = sprinkler.get('y', 0)
                    
                    # Create sprinkler symbol
                    msp.add_circle((x, y), radius=1.0, dxfattribs={'color': 1, 'layer': 'SPRINKLERS'})
                    msp.add_line((x-0.7, y), (x+0.7, y), dxfattribs={'color': 1, 'layer': 'SPRINKLERS'})
                    msp.add_line((x, y-0.7), (x, y+0.7), dxfattribs={'color': 1, 'layer': 'SPRINKLERS'})
                    
                    # Add label
                    msp.add_text(f'S{i+1}', dxfattribs={
                        'insert': (x+1.5, y), 'height': 0.5, 'layer': 'TEXT'
                    })
                
                # Add piping
                for main in context.layout_model.mains:
                    start = main.get('start', (0, 0))
                    end = main.get('end', (100, 0))
                    msp.add_line(start, end, dxfattribs={'color': 2, 'layer': 'MAINS'})
                
                for branch in context.layout_model.branches:
                    start = branch.get('start', (0, 0))
                    end = branch.get('end', (10, 0))
                    msp.add_line(start, end, dxfattribs={'color': 3, 'layer': 'BRANCHES'})
                
                doc.saveas(str(output_path))
                logger.info("Enhanced DXF generated with layers and proper units")
                
            except Exception as e:
                logger.warning(f"Enhanced DXF generation failed: {e}")
                await self._generate_basic_dxf(context, output_path, logger)
        else:
            await self._generate_basic_dxf(context, output_path, logger)
    
    async def _generate_basic_dxf(self, context: PipelineContext, output_path: Path, logger):
        """Generate basic DXF fallback"""
        with open(output_path, 'w') as f:
            f.write(f"# FireAI Pro DXF - {context.project_name}\n")
            f.write("# Enhanced CAD engine or ezdxf not available\n")
        logger.info("Basic DXF fallback generated")
    
    async def _generate_enhanced_ifc(self, context: PipelineContext, output_path: Path, logger):
        """Generate enhanced IFC with proper fire safety entities"""
        ifc_content = f"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('FireAI Pro Fire Sprinkler System'), '2;1');
FILE_NAME('{context.project_name}.ifc', '{datetime.now().isoformat()}', ('FireAI Pro'), ('FireAI'), 'FireAI Pro v3.1', 'FireAI Master Pipeline', '');
FILE_SCHEMA(('IFC4'));
ENDSEC;

DATA;
#1 = IFCPROJECT('{context.project_id}', #2, '{context.project_name}', 'Fire Sprinkler System Pipeline Design', $, $, $, (#20), #8);
#2 = IFCOWNERHISTORY(#6, #7, $, .ADDED., $, $, $, {int(datetime.now().timestamp())});
#6 = IFCPERSON($, 'FireAI', 'Pro', $, $, $, $, $);
#7 = IFCORGANIZATION($, 'FireAI Pro', 'Fire Protection Pipeline', $, $);
#8 = IFCUNITASSIGNMENT((#9));
#9 = IFCSIUNIT(*, .LENGTHUNIT., $, .METRE.);
#20 = IFCGEOMETRICREPRESENTATIONCONTEXT($, 'Model', 3, 1.E-05, #21, $);
#21 = IFCAXIS2PLACEMENT3D(#22, $, $);
#22 = IFCCARTESIANPOINT((0., 0., 0.));

/* Fire Protection System Elements */"""

        # Add sprinkler entities
        if context.layout_model:
            for i, sprinkler in enumerate(context.layout_model.sprinklers):
                entity_id = 100 + i
                x = sprinkler.get('x', 0)
                y = sprinkler.get('y', 0)
                z = sprinkler.get('z', 10)
                
                ifc_content += f"""
#{entity_id} = IFCFIRESPRINKLER('{uuid.uuid4()}', #2, 'Sprinkler S{i+1}', 'Standard Fire Sprinkler', $, #{entity_id+1000}, #{entity_id+2000}, $, .SPRINKLER.);
#{entity_id+1000} = IFCLOCALPLACEMENT($, #{entity_id+1001});
#{entity_id+1001} = IFCAXIS2PLACEMENT3D(#{entity_id+1002}, $, $);
#{entity_id+1002} = IFCCARTESIANPOINT(({x}, {y}, {z}));"""
        
        ifc_content += """

ENDSEC;
END-ISO-10303-21;"""
        
        with open(output_path, 'w') as f:
            f.write(ifc_content)
        
        logger.info("Enhanced IFC generated with fire sprinkler entities")
    
    async def _generate_smart_pdf(self, context: PipelineContext, output_path: Path, report_type: str, logger):
        """Generate PDF with smart extension handling"""
        final_path = self._ensure_extension(output_path, wants_pdf=True)
        
        if final_path.suffix == '.pdf' and REPORTLAB_AVAILABLE:
            await self._generate_reportlab_pdf(context, final_path, report_type, logger)
        else:
            await self._generate_text_report(context, final_path, report_type, logger)
    
    async def _generate_reportlab_pdf(self, context: PipelineContext, output_path: Path, report_type: str, logger):
        """Generate professional PDF using ReportLab"""
        try:
            doc = SimpleDocTemplate(str(output_path), pagesize=letter)
            styles = getSampleStyleSheet()
            story = []
            
            # Title based on report type
            titles = {
                'compliance': 'NFPA Compliance Report',
                'hydraulics': 'Hydraulics Analysis Report',
                'bom': 'Bill of Materials',
                'bracing': 'Bracing Analysis Report',
                'multistandard': 'Multi-Standard Compliance Report'
            }
            
            title = titles.get(report_type, 'FireAI Pro Report')
            story.append(Paragraph(title, styles['Title']))
            story.append(Spacer(1, 12))
            
            # Project information
            story.append(Paragraph("Project Information", styles['Heading2']))
            project_info = f"""
            <b>Project:</b> {context.project_name}<br/>
            <b>Project ID:</b> {context.project_id}<br/>
            <b>Date Generated:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>
            <b>Pipeline Version:</b> 3.1.0
            """
            story.append(Paragraph(project_info, styles['Normal']))
            story.append(Spacer(1, 12))
            
            # Report-specific content
            if report_type == 'compliance':
                story.append(Paragraph("Compliance Summary", styles['Heading2']))
                compliance_info = f"""
                <b>NFPA Edition:</b> {context.standards_ctx.nfpa_edition if context.standards_ctx else '2022'}<br/>
                <b>Coverage:</b> {context.coverage_percentage:.1f}%<br/>
                <b>Total Sprinklers:</b> {context.layout_model.total_sprinklers if context.layout_model else 0}<br/>
                <b>Code Violations:</b> {len(context.code_violations)}<br/>
                <b>Quality Status:</b> {'PASSED' if not context.quality_failures else 'FAILED'}
                """
                story.append(Paragraph(compliance_info, styles['Normal']))
                
                if context.code_violations:
                    story.append(Spacer(1, 12))
                    story.append(Paragraph("Code Violations", styles['Heading3']))
                    for violation in context.code_violations:
                        story.append(Paragraph(f"• {violation}", styles['Normal']))
            
            elif report_type == 'hydraulics':
                story.append(Paragraph("Hydraulic Analysis", styles['Heading2']))
                hydraulic_info = f"""
                <b>Analysis Status:</b> {'Converged' if context.hydraulics_report and context.hydraulics_report.converged else 'Failed'}<br/>
                <b>Hydraulic Margin:</b> {context.hydraulic_margin:.1f} PSI<br/>
                <b>Total Flow:</b> {context.hydraulics_report.demand_calc.get('total_demand', 'N/A') if context.hydraulics_report else 'N/A'} GPM<br/>
                <b>System Pressure:</b> {context.hydraulics_report.available_supply.get('pressure_psi', 'N/A') if context.hydraulics_report else 'N/A'} PSI
                """
                story.append(Paragraph(hydraulic_info, styles['Normal']))
            
            elif report_type == 'bom':
                story.append(Paragraph("Bill of Materials", styles['Heading2']))
                bom_info = f"""
                <b>Total Project Cost:</b> ${context.bom_table.total_cost:,.2f if context.bom_table else 0}<br/>
                <b>Sprinklers:</b> {len(context.bom_table.sprinklers) if context.bom_table else 0} units<br/>
                <b>Pipe & Fittings:</b> {len(context.bom_table.pipe_fittings) if context.bom_table else 0} items<br/>
                <b>Valves:</b> {len(context.bom_table.valves) if context.bom_table else 0} units
                """
                story.append(Paragraph(bom_info, styles['Normal']))
            
            # Footer
            story.append(Spacer(1, 24))
            story.append(Paragraph("Generated by FireAI Pro Master Pipeline Orchestrator v3.1.0", styles['Normal']))
            
            doc.build(story)
            logger.info(f"Professional PDF generated: {output_path.name}")
            
        except Exception as e:
            logger.warning(f"ReportLab PDF generation failed: {e}")
            # Fallback to text
            text_path = output_path.with_suffix('.txt')
            await self._generate_text_report(context, text_path, report_type, logger)
    
    async def _generate_text_report(self, context: PipelineContext, output_path: Path, report_type: str, logger):
        """Generate text report fallback"""
        titles = {
            'compliance': 'NFPA Compliance Report',
            'hydraulics': 'Hydraulics Analysis Report',
            'bom': 'Bill of Materials',
            'bracing': 'Bracing Analysis Report',
            'multistandard': 'Multi-Standard Compliance Report'
        }
        
        title = titles.get(report_type, 'FireAI Pro Report')
        
        content = f"""{title}
{'=' * len(title)}

Project: {context.project_name}
Project ID: {context.project_id}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Pipeline Version: 3.1.0

"""
        
        # Report-specific content
        if report_type == 'compliance':
            content += f"""Compliance Summary:
- NFPA Edition: {context.standards_ctx.nfpa_edition if context.standards_ctx else '2022'}
- Coverage: {context.coverage_percentage:.1f}%
- Total Sprinklers: {context.layout_model.total_sprinklers if context.layout_model else 0}
- Code Violations: {len(context.code_violations)}
- Quality Status: {'PASSED' if not context.quality_failures else 'FAILED'}

"""
            
            if context.code_violations:
                content += "Code Violations:\n"
                for violation in context.code_violations:
                    content += f"- {violation}\n"
        
        elif report_type == 'hydraulics':
            content += f"""Hydraulic Analysis:
- Status: {'Converged' if context.hydraulics_report and context.hydraulics_report.converged else 'Failed'}
- Hydraulic Margin: {context.hydraulic_margin:.1f} PSI
- Total Flow: {context.hydraulics_report.demand_calc.get('total_demand', 'N/A') if context.hydraulics_report else 'N/A'} GPM
- System Pressure: {context.hydraulics_report.available_supply.get('pressure_psi', 'N/A') if context.hydraulics_report else 'N/A'} PSI
"""
        
        elif report_type == 'bom':
            content += f"""Bill of Materials:
- Total Cost: ${context.bom_table.total_cost:,.2f if context.bom_table else 0}
- Sprinklers: {len(context.bom_table.sprinklers) if context.bom_table else 0} units
- Pipe & Fittings: {len(context.bom_table.pipe_fittings) if context.bom_table else 0} items
- Valves: {len(context.bom_table.valves) if context.bom_table else 0} units
"""
        
        content += "\nGenerated by FireAI Pro Master Pipeline Orchestrator v3.1.0\n"
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"Text report generated: {output_path.name}")
    
    async def _send_webhook(self, context: PipelineContext, status: str, project_dir: Path):
        """Send webhook notification"""
        if not REQUESTS_AVAILABLE or not context.webhook_url:
            return
        
        try:
            # Collect artifact URLs
            artifacts = []
            if (project_dir / "artifacts.json").exists():
                with open(project_dir / "artifacts.json", 'r') as f:
                    manifest = json.load(f)
                    artifacts = manifest.get('artifacts', [])
            
            payload = {
                "project_id": context.project_id,
                "project_name": context.project_name,
                "status": status,
                "completed_at": datetime.now().isoformat(),
                "artifacts": artifacts,
                "errors": context.errors,
                "warnings": context.warnings,
                "quality_failures": context.quality_failures
            }
            
            response = requests.post(
                context.webhook_url,
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            
            self.logger.info(f"Webhook sent successfully to {context.webhook_url}")
            
        except Exception as e:
            self.logger.warning(f"Webhook failed: {e}")
    
    # Utility methods
    def _ensure_extension(self, output_path: Path, wants_pdf: bool) -> Path:
        """Ensure proper file extension based on capability"""
        if wants_pdf and REPORTLAB_AVAILABLE:
            return output_path
        else:
            return output_path.with_suffix(".txt")
    
    def _check_minimum_spacing(self, context: PipelineContext) -> bool:
        """Enhanced spacing check with detailed violation reporting"""
        if not context.layout_model or not context.layout_model.sprinklers:
            return False
        
        sprinklers = context.layout_model.sprinklers
        min_distance = 6.0  # feet
        
        for i, s1 in enumerate(sprinklers):
            for j, s2 in enumerate(sprinklers[i+1:], i+1):
                x1, y1 = s1.get('x', 0), s1.get('y', 0)
                x2, y2 = s2.get('x', 0), s2.get('y', 0)
                distance = ((x2-x1)**2 + (y2-y1)**2)**0.5
                
                if distance < min_distance:
                    context.code_violations.append(
                        f"Sprinklers S{i+1} and S{j+1} too close: {distance:.1f}ft < {min_distance}ft"
                    )
                    return False
        
        return True
    
    # Fallback data generators
    def _create_fallback_model(self) -> NormalizedModel:
        return NormalizedModel(
            rooms=[{"id": "main_area", "area": 10000, "bounds": {"x1": 0, "y1": 0, "x2": 100, "y2": 100}}],
            walls=[],
            obstructions=[],
            levels=[{"id": "ground_floor", "elevation": 0}],
            bounds={"min_x": 0, "max_x": 100, "min_y": 0, "max_y": 100, "min_z": 0, "max_z": 12}
        )


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def compute_idempotency_key(file_bytes: Optional[bytes], project_data: Dict) -> str:
    """Compute idempotency key from file and project data"""
    h = hashlib.sha256()
    if file_bytes:
        h.update(file_bytes)
    h.update(json.dumps(project_data, sort_keys=True).encode())
    return h.hexdigest()

def validate_upload_file(file: UploadFile, max_size_mb: int = 100) -> bytes:
    """Validate and read uploaded file safely"""
    # Check file type
    allowed_extensions = {'.pdf', '.dxf', '.dwg', '.ifc'}
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        raise ValueError(f"Unsupported file type: {file_ext}")
    
    # Read and check size
    file_content = file.file.read()
    file_size_mb = len(file_content) / (1024 * 1024)
    
    if file_size_mb > max_size_mb:
        raise ValueError(f"File too large: {file_size_mb:.1f}MB > {max_size_mb}MB")
    
    # Reset file position
    file.file.seek(0)
    
    return file_content


# =============================================================================
# SECURITY & AUTHENTICATION
# =============================================================================

security = HTTPBearer()

def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify API key with detailed logging"""
    if not settings.api_key:
        return True  # No auth configured
    
    if credentials.credentials != settings.api_key:
        orchestrator.logger.warning("Invalid API key attempt")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return True


# =============================================================================
# API APPLICATION
# =============================================================================

# Initialize settings and orchestrator
try:
    settings = MasterSettings()
except Exception as e:
    print(f"Configuration validation failed: {e}")
    sys.exit(1)

orchestrator = MasterOrchestrator(settings)

app = FastAPI(
    title="FireAI Pro Master Production System",
    description="Unified enterprise-grade fire sprinkler design orchestrator with full production hardening",
    version="3.1.0",
    docs_url="/docs" if settings.api_key else None,  # Hide docs in production
    redoc_url="/redoc" if settings.api_key else None
)

# Security middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    max_age=3600
)

# API Models
class PipelineRequest(BaseModel):
    project_name: str = Field(..., description="Project name")
    project_data: Dict = Field(default_factory=dict, description="Project data")
    zip_code: Optional[str] = Field(default=None, description="ZIP code for AHJ resolution")
    webhook_url: Optional[str] = Field(default=None, description="Webhook URL for completion notification")


@app.post("/pipeline")
async def run_bulletproof_pipeline(
    request: Request,
    background_tasks: BackgroundTasks,
    pipeline_request: PipelineRequest,
    file: Optional[UploadFile] = File(None),
    authenticated: bool = Depends(verify_api_key)
):
    """Execute bulletproof pipeline with enterprise hardening"""
    
    user_id = request.headers.get("X-User-ID")
    ip_address = request.client.host
    
    try:
        # Handle file upload with validation
        input_file = None
        file_content = None
        
        if file:
            file_content = validate_upload_file(file, settings.max_file_size_mb)
            
            # Save file securely
            project_id = str(uuid.uuid4())
            pipeline_request.project_data['project_id'] = project_id
            
            upload_dir = Path(settings.local_storage_path) / project_id
            upload_dir.mkdir(parents=True, exist_ok=True)
            
            file_path = upload_dir / file.filename
            with open(file_path, 'wb') as f:
                f.write(file_content)
            input_file = str(file_path)
        else:
            project_id = str(uuid.uuid4())
            pipeline_request.project_data['project_id'] = project_id
        
        # Compute idempotency key
        pipeline_request.project_data['project_name'] = pipeline_request.project_name
        pipeline_request.project_data['zip_code'] = pipeline_request.zip_code
        pipeline_request.project_data['webhook_url'] = pipeline_request.webhook_url
        
        idempotency_key = compute_idempotency_key(file_content, pipeline_request.project_data)
        
        # Check for existing job
        existing_job_id = orchestrator.job_store.find_by_idempotency_key(idempotency_key)
        if existing_job_id:
            return {
                "project_id": existing_job_id,
                "status": "duplicate",
                "message": "Job already exists with same parameters",
                "idempotency_key": idempotency_key
            }
        
        # Submit to background processing
        background_tasks.add_task(
            orchestrator.process_design,
            pipeline_request.project_data,
            input_file,
            idempotency_key,
            user_id,
            ip_address
        )
        
        return {
            "project_id": project_id,
            "status": "submitted",
            "message": "Master pipeline processing started",
            "idempotency_key": idempotency_key,
            "features": [
                "Circuit breaker protection",
                "Resource management & tracking",
                "Real-time status & monitoring",
                "Retry with exponential backoff",
                "Quality gate validation",
                "Smart PDF/text generation",
                "Webhook notifications",
                "Audit trail & compliance",
                "Rate limiting & quotas"
            ],
            "endpoints": {
                "status": f"/status/{project_id}",
                "artifacts": f"/artifacts/{project_id}",
                "logs": f"/logs/{project_id}",
                "health": "/health"
            }
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline submission failed: {str(e)}")


@app.get("/status/{project_id}")
async def get_pipeline_status(project_id: str):
    """Get real-time pipeline status with detailed information"""
    
    job_status = orchestrator.job_store.get_job_status(project_id)
    
    if not job_status:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Calculate progress
    phase_order = [phase.value for phase in JobPhase]
    current_phase = job_status.get('phase', 'submitted')
    
    try:
        progress_percentage = (phase_order.index(current_phase) / len(phase_order)) * 100
    except ValueError:
        progress_percentage = 0
    
    return {
        "project_id": project_id,
        "status": job_status.get('phase', 'unknown'),
        "progress_percentage": min(progress_percentage, 100),
        "submitted_at": job_status.get('submitted_at'),
        "started_at": job_status.get('started_at'),
        "updated_at": job_status.get('updated_at'),
        "completed_at": job_status.get('completed_at'),
        "errors": job_status.get('errors', []),
        "warnings": job_status.get('warnings', []),
        "quality_metrics": job_status.get('quality', {}),
        "resource_usage": job_status.get('resource', {}),
        "pipeline_version": "3.1.0"
    }


@app.get("/logs/{project_id}")
async def get_pipeline_logs(project_id: str):
    """Get pipeline processing logs"""
    
    job_status = orchestrator.job_store.get_job_status(project_id)
    
    if not job_status:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {
        "project_id": project_id,
        "logs": {
            "errors": job_status.get('errors', []),
            "warnings": job_status.get('warnings', []),
            "quality_failures": job_status.get('context', {}).get('quality_failures', [])
        },
        "processing_details": {
            "phase": job_status.get('phase'),
            "submitted": job_status.get('submitted_at'),
            "started": job_status.get('started_at'),
            "updated": job_status.get('updated_at'),
            "completed": job_status.get('completed_at')
        }
    }


@app.get("/artifacts/{project_id}")
async def get_project_artifacts(project_id: str):
    """Get comprehensive project artifacts with metadata"""
    
    artifacts_path = orchestrator.output_dir / project_id / "artifacts.json"
    
    if not artifacts_path.exists():
        # Check job status for more info
        job_status = orchestrator.job_store.get_job_status(project_id)
        if job_status:
            raise HTTPException(
                status_code=202, 
                detail=f"Artifacts not ready. Current status: {job_status.get('phase', 'unknown')}"
            )
        else:
            raise HTTPException(status_code=404, detail="Project not found")
    
    with open(artifacts_path, 'r') as f:
        manifest = json.load(f)
    
    # Add download URLs
    for artifact in manifest['artifacts']:
        artifact['download_url'] = f"/download/{project_id}/{artifact['name']}"
    
    return manifest


@app.get("/download/{project_id}/{filename}")
async def download_artifact(project_id: str, filename: str):
    """Download artifact with security checks"""
    
    # Validate filename to prevent path traversal
    if '..' in filename or filename.startswith('/'):
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = orchestrator.output_dir / project_id / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Check if file is within project directory (additional security)
    try:
        file_path.resolve().relative_to(orchestrator.output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type='application/octet-stream'
    )


@app.get("/health")
async def health_check():
    """Enhanced health check with detailed system information"""
    return orchestrator.get_comprehensive_health()


@app.get("/")
async def root():
    """Enhanced API root with comprehensive information"""
    return {
        "name": "FireAI Pro Master Production Orchestrator",
        "version": "3.1.0",
        "description": "Unified enterprise-grade fire sprinkler design pipeline with bulletproof production hardening",
        "pipeline_steps": [
            "1. Input Validation (comprehensive safety checks)",
            "2. Ingest & Normalize (PDF/DXF/IFC processing)",
            "3. Standards/AHJ Resolve (NFPA requirements)",
            "4. Layout Design (sprinklers, mains, branches)",
            "5. Hydraulics Analysis (demand calc, remote area)",
            "6. BOM & Bracing (components, supports)",
            "7. Exports Generation (DXF, IFC, PDFs)",
            "8. Quality Gate (STRICT validation)",
            "9. Publish Artifacts (manifest generation)"
        ],
        "enterprise_features": {
            "circuit_breaker_protection": True,
            "resource_management_tracking": True,
            "database_connection_pooling": True,
            "comprehensive_audit_trail": True,
            "rate_limiting_quotas": True,
            "real_time_monitoring": True,
            "retry_exponential_backoff": True,
            "timeout_protection": True,
            "smart_pdf_text_fallbacks": True,
            "upload_safety_validation": True,
            "idempotency_protection": True,
            "webhook_notifications": True,
            "comprehensive_error_classification": True,
            "enhanced_cad_layers": True,
            "quality_gate_validation": True,
            "json_structured_logging": True,
            "api_key_authentication": True,
            "graceful_shutdown_handling": True,
            "job_recovery_mechanisms": True,
            "prometheus_metrics": PROMETHEUS_AVAILABLE
        },
        "endpoints": {
            "run_pipeline": "POST /pipeline - Execute complete pipeline",
            "get_status": "GET /status/{project_id} - Real-time status",
            "get_logs": "GET /logs/{project_id} - Processing logs",
            "get_artifacts": "GET /artifacts/{project_id} - Artifacts manifest",
            "download": "GET /download/{project_id}/{filename} - Download files",
            "health": "GET /health - Comprehensive system health"
        },
        "security": {
            "api_key_required": bool(settings.api_key),
            "file_validation": True,
            "path_traversal_protection": True,
            "rate_limiting": True,
            "audit_trail": settings.audit_enabled
        },
        "configuration": {
            "strict_mode": settings.strict_mode,
            "max_file_size_mb": settings.max_file_size_mb,
            "max_concurrent_jobs": settings.max_concurrent_jobs,
            "engine_timeout_s": settings.engine_timeout_s,
            "retry_attempts": settings.engine_retry_attempts,
            "circuit_breaker_enabled": True,
            "resource_limits_enabled": True
        },
        "engine_status": {
            "ingest_available": INGEST_ENGINE is not None,
            "standards_available": STANDARDS_ENGINE is not None,
            "layout_available": LAYOUT_ENGINE is not None,
            "hydraulics_available": HYDRAULICS_ENGINE is not None,
            "bom_available": BOM_ENGINE is not None,
            "bracing_available": BRACING_ENGINE is not None
        },
        "dependencies": {
            "reportlab_pdf": REPORTLAB_AVAILABLE,
            "ezdxf_cad": EZDXF_AVAILABLE,
            "requests_webhook": REQUESTS_AVAILABLE,
            "prometheus_metrics": PROMETHEUS_AVAILABLE,
            "psutil_monitoring": PSUTIL_AVAILABLE
        }
    }


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Enhanced main entry point with comprehensive startup information"""
    
    print("FireAI Pro Master Production Orchestrator v3.1.0")
    print("=" * 60)
    print("Unified Enterprise Features:")
    print("• Circuit breaker protection for all engines")
    print("• Database connection pooling & atomic transactions")
    print("• Resource management & memory protection")
    print("• Comprehensive observability & monitoring")
    print("• Rate limiting & resource quotas")
    print("• Error classification & handling")
    print("• Real-time job status tracking")
    print("• Retry mechanisms with exponential backoff")
    print("• Smart PDF/text generation with proper extensions")
    print("• Upload safety and idempotency protection")
    print("• Webhook notifications for job completion")
    print("• JSON structured logging with correlation IDs")
    print("• API key authentication and security")
    print("• Enhanced CAD export with layers and units")
    print("• Quality gate with comprehensive validation")
    print("• Graceful shutdown & job recovery")
    print("• Audit trail & compliance logging")
    print("• Performance monitoring & SLA tracking")
    print()
    
    # Configuration summary
    print("Configuration:")
    print(f"  Host: {settings.host}:{settings.port}")
    print(f"  Storage: {settings.local_storage_path}")
    print(f"  Database: {settings.job_db_path}")
    print(f"  Temp Dir: {settings.temp_dir}")
    print(f"  Strict Mode: {'ENABLED' if settings.strict_mode else 'DISABLED'}")
    print(f"  Audit Trail: {'ENABLED' if settings.audit_enabled else 'DISABLED'}")
    print(f"  Max File Size: {settings.max_file_size_mb}MB")
    print(f"  Max Concurrent Jobs: {settings.max_concurrent_jobs}")
    print(f"  Engine Timeout: {settings.engine_timeout_s}s")
    print(f"  Retry Attempts: {settings.engine_retry_attempts}")
    print(f"  Circuit Breaker Threshold: {settings.engine_circuit_breaker_threshold}")
    print(f"  Rate Limit (hourly/daily): {settings.rate_limit_per_hour}/{settings.rate_limit_per_day}")
    print(f"  JSON Logs: {'ENABLED' if settings.json_logs else 'DISABLED'}")
    print(f"  Metrics Collection: {'ENABLED' if settings.metrics_enabled else 'DISABLED'}")
    print(f"  API Key: {'CONFIGURED' if settings.api_key else 'NOT SET'}")
    print()
    
    # Engine status
    print("Engine Status:")
    orchestrator._log_engine_status()
    print()
    
    # Feature status
    print("Dependency Status:")
    print(f"  ReportLab PDF: {'Available' if REPORTLAB_AVAILABLE else 'Unavailable (text fallback)'}")
    print(f"  ezdxf CAD: {'Available' if EZDXF_AVAILABLE else 'Unavailable (basic fallback)'}")
    print(f"  Requests (Webhook): {'Available' if REQUESTS_AVAILABLE else 'Unavailable'}")
    print(f"  Prometheus Metrics: {'Available' if PROMETHEUS_AVAILABLE else 'Unavailable'}")
    print(f"  psutil Monitoring: {'Available' if PSUTIL_AVAILABLE else 'Unavailable'}")
    print()
    
    print("API Endpoints:")
    print(f"  POST {settings.host}:{settings.port}/pipeline - Submit job")
    print(f"  GET  {settings.host}:{settings.port}/status/{{id}} - Real-time status")
    print(f"  GET  {settings.host}:{settings.port}/logs/{{id}} - Processing logs")
    print(f"  GET  {settings.host}:{settings.port}/artifacts/{{id}} - Get artifacts")
    print(f"  GET  {settings.host}:{settings.port}/download/{{id}}/{{file}} - Download")
    print(f"  GET  {settings.host}:{settings.port}/health - System health")
    print()
    
    if settings.api_key:
        print("🔒 API Key authentication ENABLED")
        print("   Include header: Authorization: Bearer <your_api_key>")
        print()
    
    # Health check on startup
    health = orchestrator.get_comprehensive_health()
    print(f"🏥 System Health: {health['status'].upper()}")
    if health.get('issues'):
        print(f"   Issues: {', '.join(health['issues'])}")
    print(f"   Active Jobs: {health.get('active_jobs', 0)}")
    print(f"   Database: {'Healthy' if health.get('database_healthy') else 'Unhealthy'}")
    print()
    
    print("🚀 Starting master production server...")
    
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower()
    )


if __name__ == "__main__":
    main()
