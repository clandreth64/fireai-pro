"""Semantic interpretation against known ground truth."""

import json
import math

from fixtures.builders import OFFICE_ROOM_AREAS, WAREHOUSE_ROOMS

TEMPLATE_NAMES = ["Open Office", "Lobby", "Main Warehouse", "Tire Center", "Food Service", "Unclassified Area",
                  "Entrance & Lobby", "Main Area", "Support", "Sales Floor"]


def test_office_rooms(results):
    m = results["office_in"].model
    rooms = {e.label: e for e in m.elements_of("room")}
    assert set(rooms) == set(OFFICE_ROOM_AREAS)
    for name, area in OFFICE_ROOM_AREAS.items():
        r = rooms[name]
        assert math.isclose(r.properties["area_sf"], area, abs_tol=0.01)
        assert r.properties["detection_method"] == "area_layer_polyline"
        assert r.confidence == 0.9 and not r.requires_verification
        assert r.properties["label_entity_ids"], name
    office = rooms["OFFICE 101"]
    assert office.properties["name"] == "OFFICE" and office.properties["number"] == "101"
    assert office.properties["stated_area_sf"] == 2918.0
    assert office.properties["stated_vs_computed_error"] < 0.001


def test_office_elements(results):
    m = results["office_in"].model
    count = lambda c: len(m.elements_of(c))  # noqa: E731
    assert count("wall") == 12
    assert count("door") == 3 and all(d.subtype == "block_reference" and d.confidence >= 0.9 for d in m.elements_of("door"))
    assert all(math.isclose(d.properties["nominal_width_ft"], 3.0, abs_tol=1e-6) for d in m.elements_of("door"))
    assert count("window") == 2
    assert count("column") == 3
    assert count("stair") == 1
    assert count("grid_line") == 4
    assert count("existing_fire_protection") == 3
    assert count("dimension") == 3
    assert count("title_block") == 1


def test_title_block_fields(results):
    tb = results["office_in"].model.title_block
    f = {k: v["value"] for k, v in tb["fields"].items()}
    assert f["project_name"] == "AUDIT TEST OFFICE"
    assert f["sheet_number"] == "A1.01"
    assert f["date"] == "09/22/2026"
    assert results["office_in"].model.elements_of("title_block")[0].requires_verification


def test_every_element_is_traceable(results):
    for key in ("office_in", "warehouse", "simple_rect"):
        m = results[key].model
        ids = {e.id for e in m.entities}
        for el in m.elements:
            assert el.source_entity_ids, el.id
            assert set(el.source_entity_ids) <= ids, el.id
            assert el.evidence and el.rules, el.id
            assert 0.0 < el.confidence <= 1.0
            if el.confidence < 0.8:
                assert el.requires_verification, el.id


def test_unrecognized_content_stays_unclassified(results):
    m = results["office_in"].model
    layers = {m.entity(i).layer for i in m.unclassified_entity_ids}
    assert {"A-FURN", "MISC-STUFF"} <= layers
    classified_ids = {sid for el in m.elements for sid in el.source_entity_ids}
    assert not classified_ids & set(m.unclassified_entity_ids)


def test_warehouse_rooms_from_wall_linework(results):
    m = results["warehouse"].model
    rooms = {e.label: e for e in m.elements_of("room")}
    assert set(rooms) == set(WAREHOUSE_ROOMS)
    for name, area in WAREHOUSE_ROOMS.items():
        assert math.isclose(rooms[name].properties["area_sf"], area, abs_tol=0.01)
        assert rooms[name].properties["detection_method"] == "polygonized_wall_linework"
        assert rooms[name].requires_verification         # lower-confidence method is surfaced
    assert len(m.elements_of("column")) == 4
    assert all(w.confidence == 0.75 and w.requires_verification for w in m.elements_of("wall"))
    assert m.requires_human_review


def test_no_template_or_synthetic_spaces(results):
    for key in ("office_in", "warehouse", "simple_rect"):
        m = results[key].model
        assert m.geometry_is_synthetic is False
        dumped = json.dumps(m.model_dump(mode="json"))
        for name in TEMPLATE_NAMES:
            assert name not in dumped, (key, name)


def test_no_compliance_claims_in_model(results):
    for key in ("office_in", "warehouse"):
        d = results[key].model.model_dump(mode="json")
        assert d["ready_for_design"] is False and d["engineering_review_status"] == "not_performed"
        assert "compliant" not in json.dumps(d).lower()
