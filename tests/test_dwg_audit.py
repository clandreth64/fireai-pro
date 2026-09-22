"""Streaming dwgread-JSON census (Milestone 1.6). Synthetic JSON in LibreDWG's pretty-printed layout."""

from __future__ import annotations

from fireai.ingest.dwg_audit import DxfCensus, compare, stream_dwgread_json

SAMPLE = """{
  "created_by": "LibreDWG test",
  "HEADER": {
    "INSUNITS": 2,
    "EXTMIN": [ 0.0, 0.0, 0.0 ] ,
    "EXTMAX": [ 10.0, 5.0, 0.0 ] ,
    "FINGERPRINTGUID": "{X}"
  },
  "CLASSES": [
    {
      "number": 528,
      "dxfname": "ACAD_TABLE",
      "cppname": "AcDbTable"
    }
  ],
  "OBJECTS": [
    {
      "object": "BLOCK_HEADER",
      "index": 1,
      "handle": [0,1,31],
      "name": "*Model_Space"
    },
    {
      "object": "BLOCK_HEADER",
      "index": 2,
      "handle": [0,1,80],
      "name": "DOOR"
    },
    {
      "object": "LAYER",
      "index": 3,
      "handle": [0,1,16],
      "name": "A-WALL"
    },
    {
      "entity": "LINE",
      "index": 4,
      "type": 19,
      "handle": [0,1,139],
      "layer": [5,1,16,16],
      "entmode": 2,
      "color": {
        "index": 256,
        "rgb": "000000"
      },
      "start": [ 1.0, 2.0, 0.0 ] ,
      "end": [ 4.0, 2.0, 0.0 ] ,
    },
    {
      "entity": "LWPOLYLINE",
      "index": 5,
      "handle": [0,1,141],
      "entmode": 2,
      "points": [
        [ 0.0, 0.0 ] ,
        [ 10.0, 0.0 ] ,
        [ 10.0, 5.0 ]
      ],
      "bulges": [
        0.0
      ]
    },
    {
      "entity": "ARC",
      "index": 6,
      "handle": [0,1,200],
      "ownerhandle": [4,1,80,80],
      "entmode": 0,
      "center": [ 0.0, 0.0, 0.0 ] ,
      "radius": 3.0
    },
    {
      "entity": "UNKNOWN_ENT",
      "index": 7,
      "type": 528,
      "handle": [0,2,1266],
      "entmode": 1
    },
    {
      "entity": "VERTEX_2D",
      "index": 8,
      "handle": [0,1,300],
      "entmode": 2
    }
  ],
  "THUMBNAILIMAGE": {
    "size": 0
  }
}
"""


def test_stream_parser_extracts_compact_census(tmp_path):
    p = tmp_path / "o.json"
    p.write_text(SAMPLE)
    c = stream_dwgread_json(p)
    assert c.header["INSUNITS"] == 2 and c.header["EXTMAX"] == [10.0, 5.0, 0.0]
    assert c.classes == {528: "ACAD_TABLE"}
    assert c.block_names == {31: "*Model_Space", 80: "DOOR"} and c.layers == {"A-WALL"}
    assert dict(c.counts) == {"LINE": 1, "LWPOLYLINE": 1, "ARC": 1, "ACAD_TABLE": 1}     # VERTEX is structural
    line = c.entities[139]
    assert line["space"] == "model" and line["fp"] == {"start": [1.0, 2.0, 0.0], "end": [4.0, 2.0, 0.0]}
    assert c.entities[141]["fp"]["points"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0]]
    assert c.entities[200]["space"] == "block" and c.entities[200]["owner"] == 80
    assert c.entities[1266] == {"type": "ACAD_TABLE", "owner": None, "space": "paper", "fp": {}}


def test_block_ownership_decides_significance(tmp_path):
    p = tmp_path / "o.json"
    p.write_text(SAMPLE)
    c = stream_dwgread_json(p)
    dxf = DxfCensus()
    dxf.layers, dxf.block_names = {"A-WALL"}, {"*Model_Space", "DOOR"}
    for h in (139, 141, 1266):
        dxf.entities[h] = {"type": c.entities[h]["type"], "space": c.entities[h]["space"], "block": "",
                           "fp": dict(c.entities[h]["fp"])}
    # ARC (in block DOOR) missing: minor while DOOR is never inserted ...
    r = compare(c, dxf, "t")
    assert r["significance"] == "minor" and r["lost_by_significance"]["minor"]["ARC"]["handles_sample"] == ["C8"]
    # ... material once DOOR is referenced
    dxf.referenced_blocks = {"DOOR"}
    assert compare(c, dxf, "t")["significance"] == "material"
