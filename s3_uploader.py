# s3_uploader.py
from __future__ import annotations
from pathlib import Path
import os, mimetypes
import boto3
from botocore.client import Config
from fireai_schemas import Deliverables, Artifact

_BUCKET = os.getenv("S3_BUCKET")
_PREFIX = os.getenv("S3_PREFIX", "fireai/")
_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
_ENDPOINT = os.getenv("S3_ENDPOINT_URL")  # optional (R2/MinIO)
_EXPIRES = int(os.getenv("S3_URL_EXPIRES", "604800"))  # 7 days

if not _BUCKET:
    _S3 = None
else:
    _S3 = boto3.client(
        "s3",
        region_name=_REGION,
        endpoint_url=_ENDPOINT,
        config=Config(s3={"addressing_style": "virtual"})
    )

def _ctype(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"

def _upload_and_sign(local_path: str, key: str) -> str:
    assert _S3 and _BUCKET
    _S3.upload_file(local_path, _BUCKET, key, ExtraArgs={"ContentType": _ctype(local_path)})
    return _S3.generate_presigned_url(
        "get_object",
        Params={"Bucket": _BUCKET, "Key": key},
        ExpiresIn=_EXPIRES,
    )

def upload_deliverables_to_s3(delivs: Deliverables, project_id: str) -> Deliverables:
    """If S3 env vars are set, upload files and return a Deliverables with presigned URLs."""
    if not _S3 or not _BUCKET:
        return delivs

    base = f"{_PREFIX.rstrip('/')}/{project_id}"
    new = Deliverables(pdfs={}, extras=[])

    if delivs.ifc:
        new.ifc = _upload_and_sign(delivs.ifc, f"{base}/model.ifc")
    if delivs.dxf:
        new.dxf = _upload_and_sign(delivs.dxf, f"{base}/design.dxf")

    for name, path in (delivs.pdfs or {}).items():
        new.pdfs[name] = _upload_and_sign(path, f"{base}/{name}.pdf")

    for art in (delivs.extras or []):
        filename = art.name or os.path.basename(art.path or "extra.bin")
        url = _upload_and_sign(art.path, f"{base}/extras/{filename}")
        new.extras.append(Artifact(kind=art.kind, name=filename, path=url, meta=art.meta))

    return new
