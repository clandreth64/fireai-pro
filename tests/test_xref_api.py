"""XREF uploads through the API (Milestone 1.6)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_settings
from fireai.api.app import create_app, current_store
from test_xref import make_host, make_xref_file


@pytest.fixture()
def client(tmp_path):
    app = create_app(make_settings(tmp_path / "data", max_upload_bytes=2 * 1024 * 1024))
    with TestClient(app) as c:
        yield c


def post(client, host, xrefs=(), names=None):
    files = [("file", (host.name, host.read_bytes(), "application/octet-stream"))]
    for i, x in enumerate(xrefs):
        files.append(("xrefs", ((names or {}).get(i, x.name), x.read_bytes(), "application/octet-stream")))
    return client.post("/api/v2/drawings", files=files)


def test_xref_upload_resolves(client, tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    r = post(client, make_host(tmp_path / "host.dxf"), [x])
    assert r.status_code == 202 and r.json()["xref_files"] == ["ARCH.dxf"]
    job = client.get(f"/api/v2/drawings/{r.json()['job_id']}").json()
    assert job["processing_status"] == "completed"
    assert "XREF_NOT_RESOLVED" not in job["summary"]["review_trigger_codes"]
    model = client.get(f"/api/v2/drawings/{job['job_id']}/deliverables/model_json").json()
    assert model["xrefs"][0]["status"] == "resolved"


def test_without_xref_upload_trigger_is_raised(client, tmp_path):
    r = post(client, make_host(tmp_path / "host.dxf"))
    job = client.get(f"/api/v2/drawings/{r.json()['job_id']}").json()
    assert "XREF_NOT_RESOLVED" in job["summary"]["review_trigger_codes"]


def test_xref_name_is_sanitized_into_job_dir(client, tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    r = post(client, make_host(tmp_path / "host.dxf"), [x], names={0: "../../ARCH.dxf"})
    assert r.status_code == 202 and r.json()["xref_files"] == ["ARCH.dxf"]
    xdir = current_store().xref_dir(r.json()["job_id"])
    assert [p.name for p in xdir.iterdir()] == ["ARCH.dxf"]


def test_duplicate_xref_names_rejected(client, tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "ft")
    r = post(client, make_host(tmp_path / "host.dxf"), [x, x], names={1: "arch.DXF"})
    assert r.status_code == 422


def test_invalid_xref_content_rejected(client, tmp_path):
    bad = tmp_path / "ARCH.dxf"
    bad.write_bytes(b"%PDF-1.7 not a drawing")
    r = post(client, make_host(tmp_path / "host.dxf"), [bad])
    assert r.status_code == 422 and "XREF 'ARCH.dxf'" in r.json()["detail"]["message"]


def test_oversize_xref_rejected(client, tmp_path):
    big = tmp_path / "ARCH.dxf"
    big.write_bytes(b"0\nSECTION\n" + b" " * (3 * 1024 * 1024))
    r = post(client, make_host(tmp_path / "host.dxf"), [big])
    assert r.status_code == 413


def test_resolve_units_rerun_keeps_xrefs(client, tmp_path):
    x = make_xref_file(tmp_path / "ARCH.dxf", "in")
    r = post(client, make_host(tmp_path / "host.dxf", units=None), [x])
    jid = r.json()["job_id"]
    assert client.get(f"/api/v2/drawings/{jid}").json()["processing_status"] == "needs_human_input"
    r2 = client.post(f"/api/v2/drawings/{jid}/resolve-units", json={"units": "ft"})
    job = client.get(f"/api/v2/drawings/{r2.json()['job_id']}").json()
    assert job["processing_status"] == "completed" and job["xref_files"] == ["ARCH.dxf"]
    model = client.get(f"/api/v2/drawings/{job['job_id']}/deliverables/model_json").json()
    assert model["xrefs"][0]["status"] == "resolved" and model["xrefs"][0]["unit_scale"] == pytest.approx(1 / 12)
