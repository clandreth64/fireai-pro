# s3_uploader.py
from __future__ import annotations
import os, mimetypes
from pathlib import Path
from datetime import datetime, timezone

try:
    from fireai_schemas import Deliverables, Artifact
except Exception:
    # very small fallback if fireai_schemas isn't present
    class Deliverables(dict): ...
    class Artifact(dict): ...

import boto3
from botocore.client import Config

# --- Env vars: accept both old and new names ---
BUCKET   = os.getenv("AWS_S3_BUCKET") or os.getenv("S3_BUCKET")
REGION   = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
ENDPOINT = os.getenv("AWS_S3_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL")  # optional
PREFIX   = (os.getenv("AWS_S3_PREFIX") or os.getenv("S3_PREFIX") or "fireai/").rstrip("/") + "/"
EXPIRES  = int(os.getenv("AWS_URL_EXPIRY") or os.getenv("S3_URL_EXPIRES") or "604800")  # default 7 days

def _client():
    return boto3.client(
        "s3",
        region_name=REGION,
        endpoint_url=ENDPOINT or None,
        config=Config(s3={"addressing_style": "virtual"}),
    )

def _ctype(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"

def _upload_and_sign(s3, local_path: str | None, key: str) -> str | None:
    if not local_path or not os.path.exists(local_path):
        return None
    s3.upload_file(local_path, BUCKET, key, ExtraArgs={"ContentType": _ctype(local_path)})
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=EXPIRES
    )

def _to_dict(obj):
    if hasattr(obj, "model_dump"): return obj.model_dump()
    if hasattr(obj, "dict"):       return obj.dict()
    if isinstance(obj, dict):      return obj
    return getattr(obj, "__dict__", {})

def upload_deliverables_to_s3(delivs, project_id: str):
    """Uploads files referenced in deliverables and returns same structure with S3 URLs."""
    if not BUCKET:
        return delivs  # S3 not configured
    s3 = _client()

    d  = _to_dict(delivs)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{PREFIX}{project_id}/{ts}/"

    out = {"ifc": None, "dxf": None, "pdfs": {}, "extras": []}

    if d.get("ifc"):
        out["ifc"] = _upload_and_sign(s3, d["ifc"], base + Path(d["ifc"]).name)
    if d.get("dxf"):
        out["dxf"] = _upload_and_sign(s3, d["dxf"], base + Path(d["dxf"]).name)

    pdfs = d.get("pdfs") or {}
    if isinstance(pdfs, dict):
        for name, path in pdfs.items():
            url = _upload_and_sign(s3, path, base + Path(path).name)
            if url:
                out["pdfs"][str(name).lower()] = url

    extras = d.get("extras") or []
    new_extras = []
    for a in extras:
        ad = _to_dict(a)
        p = ad.get("path")
        fname = ad.get("name") or (Path(p).name if p else "extra.bin")
        url = _upload_and_sign(s3, p, base + "extras/" + fname)
        new_extras.append({
            "kind": ad.get("kind", "other"),
            "name": fname,
            "path": url or "",
            "meta": ad.get("meta", {}) or {},
        })
    out["extras"] = new_extras

    try:
        return Deliverables(**out)
    except Exception:
        return out
