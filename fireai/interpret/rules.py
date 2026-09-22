"""Deterministic layer-name and block-name classification rules.

Rules are data, each with a stable id that is cited as evidence on every
element it produces. Layer names are matched on tokens (split on - _ space . |)
after stripping external-reference prefixes (``XREF|A-WALL``, ``XREF$0$A-WALL``).

Confidence levels:
* 0.90 — layer follows the US National CAD Standard (NCS/AIA) form ``D-MAJOR[-MINOR]``
         with a recognized discipline designator and major group
* 0.75 — a recognized keyword token appears anywhere in the name
* 0.60 — only the NCS discipline designator is recognized (e.g. ``S-xxxx`` -> structural)

These are CAD conventions, not facts about a particular drawing. Offices use
private layer standards; anything unmatched stays unclassified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONF_NCS = 0.90
CONF_KEYWORD = 0.75
CONF_DISCIPLINE = 0.60


@dataclass(frozen=True)
class Rule:
    id: str
    role: str
    tokens: frozenset[str]
    description: str


# Ordered: first match wins. More specific roles come first.
LAYER_RULES: list[Rule] = [
    Rule("L-IGNORE-DEFPOINTS", "ignore", frozenset({"DEFPOINTS"}), "non-plotting definition points layer"),
    Rule("L-TITLEBLOCK", "title_block", frozenset({"TTLB", "TITLE", "TITLEBLOCK", "TBLK", "BORDER", "TB", "SHEET"}), "title block / sheet border"),
    Rule("L-DIMENSION", "dimension", frozenset({"DIMS", "DIM", "DIMENSION", "DIMENSIONS"}), "dimensions"),
    Rule("L-ROOM-LABEL", "room_label", frozenset({"IDEN", "RMNO", "ROOMNAME", "RMNAME", "RMTAG", "ROOMTAG", "RMID"}), "room identification text"),
    Rule("L-ROOM", "room_boundary", frozenset({"AREA", "ROOM", "ROOMS", "SPACE", "SPACES", "SPAC", "RM"}), "room / area boundaries"),
    Rule("L-STAIR", "stair", frozenset({"STRS", "STAIR", "STAIRS"}), "stairs"),
    Rule("L-SHAFT", "shaft", frozenset({"SHAFT", "SHFT", "CHASE", "SHAFTS"}), "shafts / chases"),
    Rule("L-COLUMN", "column", frozenset({"COLS", "COL", "COLUMN", "COLUMNS", "COLM"}), "columns"),
    Rule("L-GRID", "grid", frozenset({"GRID", "GRIDS", "GRIDLINE", "GRIDLINES"}), "structural grid"),
    Rule("L-DOOR", "door", frozenset({"DOOR", "DOORS", "DR", "DRS"}), "doors"),
    Rule("L-WINDOW", "window", frozenset({"GLAZ", "WIND", "WINDOW", "WINDOWS", "WDW", "WIN"}), "windows / glazing"),
    Rule("L-WALL", "wall", frozenset({"WALL", "WALLS", "WAL"}), "walls"),
    Rule("L-FIRE-SPRINKLER", "existing_fire_protection", frozenset({"SPRN", "SPRK", "SPKR", "SPRINKLER", "SPRINKLERS"}), "existing sprinkler system"),
    Rule("L-CEILING", "ceiling", frozenset({"CLNG", "CEIL", "CEILING", "RCP"}), "ceiling / reflected ceiling plan"),
    Rule("L-TEXT", "text", frozenset({"TEXT", "ANNO", "NOTE", "NOTES", "TXT"}), "annotation text"),
]

# NCS discipline designators (first token) -> role, used only when no keyword matched.
DISCIPLINE_RULES: dict[str, tuple[str, str]] = {
    "S": ("L-DISC-STRUCTURAL", "structural"),
    "F": ("L-DISC-FIRE", "existing_fire_protection"),
    "FP": ("L-DISC-FIRE", "existing_fire_protection"),
    "FA": ("L-DISC-FIRE-ALARM", "existing_fire_protection"),
    "M": ("L-DISC-MECH", "existing_mep"),
    "P": ("L-DISC-PLUMB", "existing_mep"),
    "E": ("L-DISC-ELEC", "existing_mep"),
}

NCS_DISCIPLINES = {"A", "S", "F", "FP", "FA", "M", "P", "E", "G", "C", "L", "I", "Q", "T", "D", "H", "V", "X", "Z"}

BLOCK_RULES: list[Rule] = [
    Rule("B-TITLEBLOCK", "title_block", frozenset({"TITLE", "TITLEBLOCK", "TTLB", "TBLK", "TB", "BORDER"}), "title block"),
    Rule("B-DOOR", "door", frozenset({"DOOR", "DOORS", "DR"}), "door block"),
    Rule("B-WINDOW", "window", frozenset({"WINDOW", "WIN", "WDW", "GLAZ"}), "window block"),
    Rule("B-COLUMN", "column", frozenset({"COL", "COLUMN", "COLS", "COLM"}), "column block"),
    Rule("B-STAIR", "stair", frozenset({"STAIR", "STAIRS", "STRS"}), "stair block"),
    Rule("B-SPRINKLER", "existing_fire_protection", frozenset({"SPRINKLER", "SPRK", "SPKR", "SPRINK", "HEAD"}), "sprinkler / FP symbol"),
]

_SPLIT = re.compile(r"[-_ .|]+")
_DIGIT_SUFFIX = re.compile(r"^([A-Z]+?)\d+[A-Z]?$")


def strip_xref_prefix(name: str) -> str:
    if "|" in name:
        name = name.split("|", 1)[1]
    if "$0$" in name:
        name = name.split("$0$", 1)[1]
    return name


def tokens(name: str) -> list[str]:
    out = []
    for tok in _SPLIT.split(strip_xref_prefix(name).upper()):
        if not tok:
            continue
        out.append(tok)
        m = _DIGIT_SUFFIX.match(tok)  # "DOOR36" -> also "DOOR"
        if m:
            out.append(m.group(1))
    return out


@dataclass(frozen=True)
class RoleMatch:
    role: str
    confidence: float
    rule_id: str
    evidence: str


def classify_layer(name: str) -> RoleMatch | None:
    toks = tokens(name)
    if not toks:
        return None
    ncs_form = len(toks) >= 2 and toks[0] in NCS_DISCIPLINES
    for rule in LAYER_RULES:
        hit = next((t for t in toks if t in rule.tokens), None)
        if hit:
            conf = CONF_NCS if (ncs_form and hit in toks[1:3]) else CONF_KEYWORD
            return RoleMatch(rule.role, conf, rule.id,
                             f"layer '{name}' token '{hit}' matches rule {rule.id} ({rule.description})")
    if ncs_form and toks[0] in DISCIPLINE_RULES:
        rid, role = DISCIPLINE_RULES[toks[0]]
        return RoleMatch(role, CONF_DISCIPLINE, rid,
                         f"layer '{name}' has NCS discipline designator '{toks[0]}' (rule {rid})")
    return None


def classify_block(name: str) -> RoleMatch | None:
    if name.startswith("*"):  # anonymous blocks (dimensions, hatches, dynamic block copies)
        return None
    toks = tokens(name)
    for rule in BLOCK_RULES:
        hit = next((t for t in toks if t in rule.tokens), None)
        if hit:
            return RoleMatch(rule.role, CONF_KEYWORD + 0.05, rule.id,
                             f"block name '{name}' token '{hit}' matches rule {rule.id} ({rule.description})")
    return None
