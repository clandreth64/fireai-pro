"""Repository hygiene: no proprietary drawings, corpus intake safety (Milestone 1.6)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from intake_drawing import intake  # noqa: E402

from fixtures import builders as B  # noqa: E402


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("git") is None or _git("rev-parse").returncode != 0, reason="no git checkout")
def test_only_synthetic_drawings_are_tracked():
    tracked = [p for p in _git("ls-files").stdout.splitlines() if p.lower().endswith((".dwg", ".dxf"))]
    assert tracked and all(p.startswith("tests/golden/drawings/") for p in tracked), tracked
    for p in ("tests/real_drawings_local/drawings/x.dwg", "tests/real_drawings_local/xrefs/REAL_999/a.dwg",
              "tests/real_drawings_local/human_reviews/REAL_001.json", "tests/real_drawings_outputs_local/a/b.json",
              ".fireai_data/reviews/ab/x.json"):
        assert _git("check-ignore", "-q", p).returncode == 0, p


def _sandbox(tmp_path):
    (tmp_path / "tests" / "real_drawings").mkdir(parents=True)
    (tmp_path / "tests" / "real_drawings" / "corpus.json").write_text(json.dumps({"files": [
        {"id": "REAL_011", "file": "REAL_011.dxf", "sha256": "0" * 64}]}))
    return tmp_path


def test_intake_copies_locally_and_records_metadata_only(tmp_path):
    root = _sandbox(tmp_path / "repo")
    src = B.make_simple_rect(tmp_path / "Owner Project 12.dxf")
    xd = tmp_path / "x"
    xd.mkdir()
    B.make_simple_rect(xd / "ARCH-BASE.dxf")
    e = intake(src, "private", "tenant improvement floor plan", None, xd, root=root, check_ignored=lambda p: True)
    assert e["id"] == "REAL_012" and e["file"] == "REAL_012.dxf"
    assert (root / "tests/real_drawings_local/drawings/REAL_012.dxf").exists()
    assert (root / "tests/real_drawings_local/xrefs/REAL_012/ARCH-BASE.dxf").exists()
    corpus = (root / "tests/real_drawings/corpus.json").read_text()
    assert "Owner Project" not in corpus and "ARCH-BASE" not in corpus     # names never recorded
    gt = json.loads((root / "tests/real_drawings/ground_truth/REAL_012.json").read_text())
    assert gt["review_status"] == "PENDING_HUMAN_VERIFICATION" and gt["claude_draft"]["fields"] == {}
    with pytest.raises(SystemExit, match="already in the corpus"):
        intake(src, "private", "floor plan", None, None, root=root, check_ignored=lambda p: True)


def test_intake_refuses_unignored_destination_and_identifying_descriptions(tmp_path):
    root = _sandbox(tmp_path / "repo")
    src = B.make_simple_rect(tmp_path / "a.dxf")
    with pytest.raises(SystemExit, match="not git-ignored"):
        intake(src, "private", "floor plan", None, None, root=root, check_ignored=lambda p: False)
    with pytest.raises(SystemExit, match="generic description"):
        intake(src, "private", "plan for 123 Main St", None, None, root=root, check_ignored=lambda p: True)
    fake = tmp_path / "b.dwg"
    fake.write_bytes(b"not a dwg")
    with pytest.raises(SystemExit, match="not a DWG/DXF"):
        intake(fake, "private", "floor plan", None, None, root=root, check_ignored=lambda p: True)
