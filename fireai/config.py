"""Runtime settings, read from the environment with secure defaults.

Defaults are chosen so that an unconfigured deployment is *restrictive*:
no cross-origin access, bounded uploads, and job data outside the source tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Where job records and job files live. Never served directly.
    data_dir: Path = field(default_factory=lambda: Path(
        os.getenv("FIREAI_DATA_DIR", str(REPO_ROOT / ".fireai_data"))).resolve())
    max_upload_bytes: int = field(default_factory=lambda: _int("FIREAI_MAX_UPLOAD_MB", 100) * 1024 * 1024)
    # Comma-separated list of allowed browser origins. Empty = same-origin only.
    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        o.strip() for o in os.getenv("FIREAI_CORS_ORIGINS", "").split(",") if o.strip()))
    # Optional shared bearer token. When unset the API runs UNAUTHENTICATED and
    # reports that fact in /health — not production-safe.
    api_token: str | None = field(default_factory=lambda: os.getenv("FIREAI_API_TOKEN") or None)
    # DWG converter selection: auto | oda | libredwg | none
    dwg_converter: str = field(default_factory=lambda: os.getenv("FIREAI_DWG_CONVERTER", "auto").lower())
    oda_converter_path: str | None = field(default_factory=lambda: os.getenv("FIREAI_ODA_CONVERTER") or None)
    libredwg_path: str | None = field(default_factory=lambda: os.getenv("FIREAI_LIBREDWG_DWG2DXF") or None)
    dwg_timeout_s: int = field(default_factory=lambda: _int("FIREAI_DWG_TIMEOUT_S", 180))
    # Address-space cap for each converter subprocess (0 = none). LibreDWG peaked at ~4.6 GB RSS on a 5 MB DWG.
    dwg_max_memory_mb: int = field(default_factory=lambda: _int("FIREAI_DWG_MAX_MEMORY_MB", 8192))
    max_entities: int = field(default_factory=lambda: _int("FIREAI_MAX_ENTITIES", 500_000))
    max_concurrent_jobs: int = field(default_factory=lambda: _int("FIREAI_MAX_CONCURRENT_JOBS", 2))

    @property
    def review_dir(self) -> Path:
        return self.data_dir / "reviews"

    @property
    def rules_dir(self) -> Path:          # M2.1: authoritative rule sets (human-authored, reviewed)
        return self.data_dir / "rules"

    @property
    def listings_dir(self) -> Path:       # M2.1: sprinkler listing data (human-authored, reviewed)
        return self.data_dir / "listings"

    @property
    def projects_dir(self) -> Path:       # M2.1: persistent project engineering model
        return self.data_dir / "projects"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "drawing_jobs.db"


def get_settings() -> Settings:
    return Settings()
