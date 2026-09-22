"""Add a drawing (and optional XREF files) to the LOCAL real-drawing corpus.

    python scripts/intake_drawing.py <file.dwg|dxf> --source private \\
        --description "tenant improvement floor plan (owner-supplied)" [--xrefs <dir>] [--license-note ...]

* Refuses to run unless the destination folders are git-ignored (checked with
  ``git check-ignore``), so drawings can never be committed by accident.
* Copies the drawing to tests/real_drawings_local/drawings/REAL_###.<ext> and
  XREFs to tests/real_drawings_local/xrefs/REAL_###/ (XREFs keep their file
  names because references are matched by name).
* Appends METADATA ONLY to tests/real_drawings/corpus.json (committed): id,
  format, DWG version magic, size, sha256, generic description, source,
  license note. For private drawings the description must be generic — no
  client, address, project number or person names.
* Creates tests/real_drawings/ground_truth/REAL_###.json with an EMPTY Claude
  draft and review_status PENDING_HUMAN_VERIFICATION; a person records the
  truth with scripts/gt_review_server.py.
* Refuses duplicates (same sha256 already in the corpus).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOCAL = ROOT / "tests" / "real_drawings_local"
CORPUS = ROOT / "tests" / "real_drawings" / "corpus.json"
GT_DIR = ROOT / "tests" / "real_drawings" / "ground_truth"
SUSPICIOUS = re.compile(r"\d{2,6}\s+\w+\s+(st|street|ave|avenue|rd|road|blvd|dr|drive|way|ln|lane)\b|\bapn\b|"
                        r"\b\d{3}-\d{3}-\d{2,3}\b|@|\bLLC\b|\bInc\b", re.I)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_git_ignored(path: Path) -> bool:
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(path)], capture_output=True)
    return r.returncode == 0


def next_id(files: list[dict]) -> str:
    nums = [int(f["id"].split("_")[1]) for f in files if re.match(r"^REAL_\d{3}$", f["id"])]
    return f"REAL_{(max(nums) + 1 if nums else 1):03d}"


def intake(src: Path, source: str, description: str, license_note: str | None, xref_dir: Path | None,
           root: Path = ROOT, check_ignored=is_git_ignored) -> dict:
    from fireai.ingest.filetype import sniff_content
    local = root / "tests" / "real_drawings_local"
    corpus_p = root / "tests" / "real_drawings" / "corpus.json"
    gt_dir = root / "tests" / "real_drawings" / "ground_truth"
    if not src.is_file():
        raise SystemExit(f"not a file: {src}")
    with src.open("rb") as fh:
        head = fh.read(4096)
    kind = sniff_content(head)
    if kind is None or src.suffix.lower() not in (".dwg", ".dxf") or kind != src.suffix.lower()[1:]:
        raise SystemExit("file is not a DWG/DXF whose content matches its extension")
    if source == "private" and SUSPICIOUS.search(description):
        raise SystemExit("description looks like it contains an address, parcel number, company or e-mail; "
                         "use a generic description for private drawings")
    for d in (local / "drawings", local / "xrefs"):
        if not check_ignored(d / "probe.dwg"):
            raise SystemExit(f"{d} is not git-ignored; refusing to place drawings there")
    corpus = json.loads(corpus_p.read_text(encoding="utf-8"))
    digest = sha256(src)
    if any(f["sha256"] == digest for f in corpus["files"]):
        raise SystemExit("this exact file is already in the corpus")
    gid = next_id(corpus["files"])
    dest = local / "drawings" / f"{gid}{src.suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    xrefs = []
    if xref_dir:
        xd = local / "xrefs" / gid
        xd.mkdir(parents=True, exist_ok=True)
        for x in sorted(xref_dir.iterdir()):
            if x.is_file() and x.suffix.lower() in (".dwg", ".dxf"):
                shutil.copyfile(x, xd / x.name)
                xrefs.append({"sha256": sha256(x), "size_bytes": x.stat().st_size, "format": x.suffix.lower()[1:]})
    entry = {"file": dest.name, "id": gid, "format": kind,
             "dwg_version_magic": head[:6].decode("ascii", "replace") if kind == "dwg" else None,
             "size_bytes": src.stat().st_size, "sha256": digest, "description": description,
             "source": source, "license_note": license_note or ("private source withheld; owner-authorized for "
                                                                "local validation only" if source == "private" else ""),
             "xref_files": xrefs}                      # hashes only; XREF names can identify a project
    corpus["files"].append(entry)
    corpus_p.write_text(json.dumps(corpus, indent=2), encoding="utf-8")
    gt_dir.mkdir(parents=True, exist_ok=True)
    (gt_dir / f"{gid}.json").write_text(json.dumps({
        "schema": "ground_truth/2", "id": gid, "review_status": "PENDING_HUMAN_VERIFICATION",
        "source_sha256s": [digest],
        "note": "No Claude draft for this drawing: the truth is recorded directly by a person in the review tool.",
        "claude_draft": {"author": "Claude (AI-assisted draft) — not produced", "method": "none; independent of "
                         "FireAI output", "date": None, "fields": {}, "by_category": {}, "expected_behavior": []},
    }, indent=2), encoding="utf-8")
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--source", required=True, help="'private' or a public URL")
    ap.add_argument("--description", required=True)
    ap.add_argument("--license-note")
    ap.add_argument("--xrefs", type=Path)
    a = ap.parse_args()
    e = intake(a.file, a.source, a.description, a.license_note, a.xrefs)
    print(f"added {e['id']} ({e['format']}, {e['size_bytes']} bytes); run "
          f"python scripts/validate_corpus.py --label <new label> --only {e['id']}")


if __name__ == "__main__":
    main()
