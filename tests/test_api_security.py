"""API behaviour and security: deliverable-only downloads, limits, CORS, auth, retired v1 routes."""


import pytest
from fastapi.testclient import TestClient

from conftest import make_settings
from fireai.api.app import create_app, current_store

BANNED_KEYS = ("compliant",)


@pytest.fixture()
def client(tmp_path):
    app = create_app(make_settings(tmp_path / "data", max_upload_bytes=2 * 1024 * 1024,
                                   cors_origins=("https://app.example",)))
    # A decoy database and "secret" beside the job store, plus a v1-style DB in the data dir.
    (tmp_path / "data" / "fireai_jobs.db").write_text("DECOY-CUSTOMER-DATA")
    (tmp_path / "data" / ".env").write_text("SECRET=DECOY")
    with TestClient(app) as c:
        yield c


def upload(client, path, name=None, units=None, headers=None):
    files = {"file": (name or path.name, path.read_bytes(), "application/octet-stream")}
    data = {"units": units} if units else {}
    return client.post("/api/v2/drawings", files=files, data=data, headers=headers or {})


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def test_upload_to_completed_with_deliverables(client, fx):
    r = upload(client, fx["office_ft"])
    assert r.status_code == 202
    jid = r.json()["job_id"]
    job = client.get(f"/api/v2/drawings/{jid}").json()
    assert job["processing_status"] == "completed"
    assert job["engineering_review_status"] == "not_performed" and job["ready_for_design"] is False
    ids = {d["id"] for d in job["deliverables"]}
    assert {"overlay_png", "overlay_svg", "overlay_dxf", "source_png", "model_json", "report_json", "summary_md"} <= ids
    png = client.get(f"/api/v2/drawings/{jid}/deliverables/overlay_png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert png.headers["x-content-type-options"] == "nosniff"
    svg = client.get(f"/api/v2/drawings/{jid}/deliverables/overlay_svg")
    assert svg.headers["content-disposition"].startswith("attachment")
    assert not [k for k in _walk_keys(job) if k in BANNED_KEYS]


def test_failed_job_is_reported_as_failed(client, fx):
    r = upload(client, fx["fake_dwg"])
    job = client.get(f"/api/v2/drawings/{r.json()['job_id']}").json()
    assert job["processing_status"] == "failed"
    assert job["failure"]["code"] == "DWG_CONVERSION_UNAVAILABLE"
    assert job["requires_human_review"] is True


def test_units_needed_then_resolved(client, fx):
    jid = upload(client, fx["unitless"]).json()["job_id"]
    job = client.get(f"/api/v2/drawings/{jid}").json()
    assert job["processing_status"] == "needs_human_input"
    assert job["unit_resolution_required"]["accepted_units"]
    r = client.post(f"/api/v2/drawings/{jid}/resolve-units", json={"units": "ft"})
    assert r.status_code == 202
    new = client.get(f"/api/v2/drawings/{r.json()['job_id']}").json()
    assert new["processing_status"] == "completed" and new["parent_job_id"] == jid


@pytest.mark.parametrize("path", [
    "/api/jobs/ANYJOB/download/fireai_jobs.db",
    "/api/jobs",
])
def test_legacy_download_routes_are_gone(client, path):
    r = client.get(path)
    assert r.status_code == 410
    assert "DECOY" not in r.text


def test_cannot_download_arbitrary_files(client, fx):
    jid = upload(client, fx["office_ft"]).json()["job_id"]
    for target in ["fireai_jobs.db", "drawing_jobs.db", ".env", "..%2F..%2Fdrawing_jobs.db", "..%2F..%2F.env",
                   "%2Fetc%2Fpasswd", "building_model.json", "overlay.png", "source%2Fupload.dxf",
                   "..%2Fsource%2Fupload.dxf"]:
        r = client.get(f"/api/v2/drawings/{jid}/deliverables/{target}")
        assert r.status_code == 404, target
        assert "DECOY" not in r.text
    for bad_job in ["..", "..%2F..", "fireai_jobs.db", "0" * 31, "g" * 32, "ANYJOB"]:
        assert client.get(f"/api/v2/drawings/{bad_job}/deliverables/overlay_png").status_code == 404


def test_cannot_access_another_jobs_files(client, fx):
    a = upload(client, fx["office_ft"]).json()["job_id"]
    b = upload(client, fx["fake_dwg"]).json()["job_id"]       # failed job: no overlay
    assert client.get(f"/api/v2/drawings/{a}/deliverables/overlay_png").status_code == 200
    assert client.get(f"/api/v2/drawings/{b}/deliverables/overlay_png").status_code == 404
    assert client.get(f"/api/v2/drawings/{'f' * 32}/deliverables/overlay_png").status_code == 404


def test_tampered_registry_cannot_escape_job_dir(client, fx):
    store = current_store()
    jid = upload(client, fx["office_ft"]).json()["job_id"]
    job = store.get(jid)
    for evil in ("../../drawing_jobs.db", "../source/upload.dxf", "/etc/passwd", "../../fireai_jobs.db"):
        job["deliverables"]["overlay_png"]["filename"] = evil
        store.update(jid, deliverables=job["deliverables"])
        assert client.get(f"/api/v2/drawings/{jid}/deliverables/overlay_png").status_code == 404, evil


def test_upload_size_limit(client, tmp_path):
    big = tmp_path / "big.dxf"
    big.write_bytes(b"0\nSECTION\n" + b"x" * (3 * 1024 * 1024))
    r = upload(client, big)
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.parametrize("key,name,status,code", [
    ("txt", "notes.txt", 415, "UNSUPPORTED_FORMAT"),
    ("pdf", "plan.pdf", 415, "UNSUPPORTED_FORMAT"),
    ("renamed_pdf", "plan.dxf", 422, "INVALID_DRAWING"),
    ("dxf_as_dwg", "plan.dwg", 422, "INVALID_DRAWING"),
    ("zero", "zero.dxf", 422, "INVALID_DRAWING"),
])
def test_upload_content_validation(client, fx, key, name, status, code):
    r = upload(client, fx[key], name=name)
    assert r.status_code == status
    assert r.json()["detail"]["code"] == code


def test_filename_is_sanitized_and_not_used_for_storage(client, fx):
    r = upload(client, fx["office_ft"], name="../../..\\evil<script>.dxf")
    jid = r.json()["job_id"]
    job = client.get(f"/api/v2/drawings/{jid}").json()
    assert job["source"]["filename"] == "evil_script_.dxf"
    src = list(current_store().source_dir(jid).iterdir())
    assert [p.name for p in src] == ["upload.dxf"]


def test_cors_is_restricted(client):
    evil = client.options("/api/v2/drawings", headers={"Origin": "https://evil.example",
                                                       "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in evil.headers
    good = client.options("/api/v2/drawings", headers={"Origin": "https://app.example",
                                                       "Access-Control-Request-Method": "POST"})
    assert good.headers.get("access-control-allow-origin") == "https://app.example"


def test_token_auth_when_configured(tmp_path, fx):
    app = create_app(make_settings(tmp_path / "d2", api_token="s3cret-token"))
    with TestClient(app) as c:
        assert upload(c, fx["office_ft"]).status_code == 401
        r = upload(c, fx["office_ft"], headers={"Authorization": "Bearer s3cret-token"})
        assert r.status_code == 202
        jid = r.json()["job_id"]
        assert c.get(f"/api/v2/drawings/{jid}").status_code == 401
        assert c.get(f"/api/v2/drawings/{jid}", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/health").json()["authentication"] == "bearer-token"


@pytest.mark.parametrize("method,path", [
    ("post", "/api/generate"), ("post", "/api/generate/upload"), ("post", "/api/analyze"),
    ("get", "/design"), ("post", "/api/improvement/run"), ("get", "/api/improvement/status"),
])
def test_v1_design_endpoints_retired(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "ENDPOINT_RETIRED"


def test_health_is_honest(client):
    h = client.get("/health").json()
    assert h["checks"]["database"] and h["checks"]["data_dir_writable"]
    assert h["authentication"].startswith("NONE")
    assert "unavailable" in h["dwg_conversion"]
    assert "code_compliance" in h["not_provided"]


def test_no_repo_database_is_reachable(client):
    # Even a legacy DB in the repo root can never be served.
    for p in ("/fireai_jobs.db", "/api/jobs/x/download/fireai_jobs.db", "/api/v2/drawings/fireai_jobs.db"):
        assert client.get(p).status_code in (404, 405, 410)
        assert "DECOY" not in client.get(p).text
