"""The served product must not claim capabilities it has not earned."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import make_settings
from fireai.api.app import create_app

ROOT = Path(__file__).resolve().parent.parent
BANNED = [r"AHJ[- ]ready", r"\bstamped\b", r"NFPA[- ]compliant", r"code[- ]compliant", r"hydraulically compliant",
          r"compliance cert", r"permit[- ]ready", r"permit package"]


def _scan(text: str) -> list[str]:
    return [p for p in BANNED if re.search(p, text, re.I)]


def test_served_ui_has_no_unsafe_claims(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        html = c.get("/").text
    assert "Not for design or permit" in html
    assert _scan(html) == []


def test_new_package_has_no_unsafe_claims():
    for p in (ROOT / "fireai").rglob("*"):
        if p.suffix in (".py", ".html"):
            assert _scan(p.read_text(encoding="utf-8")) == [], p


def test_reports_have_no_unsafe_claims(results):
    import json
    for key in ("office_in", "warehouse"):
        r = results[key]
        assert _scan(json.dumps(r.report)) == []
        assert _scan(r.path("summary_md").read_text()) == []


def test_legacy_ui_files_are_not_served(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        assert c.get("/design").status_code == 410
        assert "AHJ" not in c.get("/").text
