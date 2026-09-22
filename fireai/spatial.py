"""Coordinate frames and transforms.

Frames form a chain rooted at SRC:

    SRC ──(scale s)──► SRC_FT ──(translate −origin·s)──► LOCAL
                          └──(UNRESOLVED)──► PROJECT

Matrices are 4x4 homogeneous, row-major, mapping parent -> child. Z passes
through unchanged (scale applies to Z as well, because drawing units apply to
all axes); FireAI never invents a Z value.

Converting into or out of an unresolved frame raises FrameUnresolved — callers
must handle "we do not know where this is in the project" explicitly.
"""

from __future__ import annotations

from fireai.model import CoordinateFrame

IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


class FrameUnresolved(Exception):
    pass


def _scale(s: float) -> list[list[float]]:
    return [[s, 0, 0, 0], [0, s, 0, 0], [0, 0, s, 0], [0, 0, 0, 1.0]]


def _translate(tx: float, ty: float, tz: float = 0.0) -> list[list[float]]:
    return [[1.0, 0, 0, tx], [0, 1.0, 0, ty], [0, 0, 1.0, tz], [0, 0, 0, 1.0]]


def build_frames(source_units: str | None, scale: float | None, origin_src: tuple[float, float]) -> list[CoordinateFrame]:
    frames = [CoordinateFrame(id="SRC", units=source_units, matrix=IDENTITY,
                              description="Source drawing WCS in drawing units (as stored in the file).")]
    if scale is None:
        for fid, parent, desc in (("SRC_FT", "SRC", "Source WCS in feet — units unresolved."),
                                  ("LOCAL", "SRC_FT", "Display frame — units unresolved."),
                                  ("PROJECT", "SRC_FT", "Coordinated project frame — not established.")):
            frames.append(CoordinateFrame(id=fid, parent=parent, units="ft", status="unresolved", description=desc))
        return frames
    ox, oy = origin_src
    frames += [
        CoordinateFrame(id="SRC_FT", parent="SRC", units="ft", matrix=_scale(scale),
                        description="Source WCS scaled to feet, no translation. Stable across drawing edits and "
                                    "shared between files that share CAD coordinates."),
        CoordinateFrame(id="LOCAL", parent="SRC_FT", units="ft", matrix=_translate(-ox * scale, -oy * scale),
                        description="SRC_FT shifted so the lower-left of visible model-space geometry is (0,0). "
                                    "Display/analysis convenience only; origin depends on drawing content."),
        CoordinateFrame(id="PROJECT", parent="SRC_FT", units="ft", status="unresolved",
                        description="Coordinated project frame. Unresolved until a person or adapter establishes "
                                    "the SRC_FT -> PROJECT transform (e.g. shared coordinates, survey point)."),
    ]
    return frames


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _invert_affine(m):
    # Affine 4x4 with upper-left 3x3 R and translation t: inverse = [R^-1, -R^-1 t]
    r = [row[:3] for row in m[:3]]
    t = [m[i][3] for i in range(3)]
    det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1]) - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
           + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
    if abs(det) < 1e-300:
        raise ValueError("singular transform")
    inv = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            minor = [[r[a][b] for b in range(3) if b != i] for a in range(3) if a != j]
            inv[i][j] = ((-1) ** (i + j)) * (minor[0][0] * minor[1][1] - minor[0][1] * minor[1][0]) / det
    it = [-sum(inv[i][k] * t[k] for k in range(3)) for i in range(3)]
    return [inv[0] + [it[0]], inv[1] + [it[1]], inv[2] + [it[2]], [0.0, 0.0, 0.0, 1.0]]


def _to_root(frames: dict[str, CoordinateFrame], fid: str):
    """Matrix mapping frame `fid` -> SRC."""
    # p_parent = inv(M_child) @ p_child, applied up the chain: M = inv(M_top) ... inv(M_fid)
    m = IDENTITY
    cur = frames[fid]
    while cur.parent is not None:
        if cur.status != "defined" or cur.matrix is None:
            raise FrameUnresolved(f"frame {cur.id} is unresolved")
        m = _matmul(_invert_affine(cur.matrix), m)
        cur = frames[cur.parent]
    return m


def transform_point(frames: list[CoordinateFrame], p, from_frame: str, to_frame: str):
    """Transform a 2D or 3D point between frames. Z is returned only if given."""
    fr = {f.id: f for f in frames}
    to_src = _to_root(fr, from_frame)
    src_to = _invert_affine(_to_root(fr, to_frame))
    m = _matmul(src_to, to_src)
    x, y = float(p[0]), float(p[1])
    z = float(p[2]) if len(p) > 2 else 0.0
    out = [m[i][0] * x + m[i][1] * y + m[i][2] * z + m[i][3] for i in range(3)]
    return (out[0], out[1], out[2]) if len(p) > 2 else (out[0], out[1])
