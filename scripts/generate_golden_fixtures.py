"""Regenerate the synthetic golden drawings in tests/golden/drawings/.

Expected results in tests/golden/expected/*.json are written BY HAND from each
fixture's known construction (tests/fixtures/builders.py) — never copied from
pipeline output, so the golden tests cannot silently bless a regression.

Usage:  python scripts/generate_golden_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fixtures import builders as B  # noqa: E402

OUT = ROOT / "tests" / "golden" / "drawings"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    B.make_office(OUT / "synthetic_office_in.dxf", "in")
    B.make_office(OUT / "synthetic_office_mm.dxf", "mm")
    B.make_warehouse(OUT / "synthetic_warehouse_ft.dxf")
    B.make_simple_rect(OUT / "synthetic_simple_rect_ft.dxf")
    B.make_units_code(OUT / "synthetic_unitless.dxf", 0)
    B.make_empty(OUT / "synthetic_empty.dxf")
    B.make_fake_dwg(OUT / "fake_not_a_drawing.dwg")
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:40} {p.stat().st_size:>8} bytes")


if __name__ == "__main__":
    main()
