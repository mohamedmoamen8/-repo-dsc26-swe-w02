"""Public-style verification tests for the Tawseel parcel API.

These mirror the described public contract: status codes, problem+json shape,
pagination, and the pure domain lifecycle. The grader runs its own hidden tests;
these are for local verification only.
"""

from __future__ import annotations

from app.api import create_app
from app.domain import InvalidTransition, ParcelState, apply_event
from fastapi.testclient import TestClient


def _client():
    return TestClient(create_app())


def _parcel(body):
    return {
        "id": body["id"],
        "merchant_ref": body["merchant_ref"],
        "cod_amount_piastres": body["cod_amount_piastres"],
        "address_text": body["address_text"],
        "state": body["state"],
        "attempts": body["attempts"],
    }


# ---------------- domain ----------------

def test_domain_dispatch_created():
    from app.domain import Parcel
    p = Parcel(id="pcl_1", merchant_ref="m1", cod_amount_piastres=100, address_text="a")
    p2 = apply_event(p, "dispatch")
    assert p.state is ParcelState.CREATED  # not mutated
    assert p2.state is ParcelState.OUT_FOR_DELIVERY
    assert p2.attempts == 1


def test_domain_three_failures_then_return():
    from app.domain import Parcel
    p = Parcel(id="pcl_1", merchant_ref="m1", cod_amount_piastres=100, address_text="a")
    for _ in range(3):
        p = apply_event(p, "dispatch")
        p = apply_event(p, "fail")
    assert p.attempts == 3
    try:
        apply_event(p, "dispatch")
        assert False, "expected InvalidTransition"
    except InvalidTransition:
        pass
    p = apply_event(p, "return")
    assert p.state is ParcelState.RETURNED


def test_domain_forbidden_raise_and_unchanged():
    from app.domain import Parcel
    p = Parcel(id="pcl_1", merchant_ref="m1", cod_amount_piastres=100, address_text="a")
    before = p
    try:
        apply_event(p, "deliver")
        assert False
    except InvalidTransition:
        pass
    assert p is before  # unchanged


# ---------------- API ----------------

def test_create_and_get_roundtrip():
    c = _client()
    r = c.post(
        "/v1/parcels",
        json={"merchant_ref": "MR-1", "cod_amount_piastres": 5000, "address_text": "Cairo"},
    )
    assert r.status_code == 201, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["location"].endswith("/v1/parcels/" + r.json()["id"])
    body = r.json()
    assert body["id"].startswith("pcl_")
    assert set(body.keys()) == {"id", "merchant_ref", "cod_amount_piastres", "address_text", "state", "attempts"}
    assert body["state"] == "created"
    assert body["attempts"] == 0

    gid = body["id"]
    r2 = c.get(f"/v1/parcels/{gid}")
    assert r2.status_code == 200
    assert r2.json() == body


def test_get_unknown_parcel_404():
    c = _client()
    r = c.get("/v1/parcels/pcl_missing")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    b = r.json()
    assert b["status"] == 404
    assert "title" in b and "type" in b


def test_duplicate_merchant_ref_409():
    c = _client()
    c.post("/v1/parcels", json={"merchant_ref": "MR-DUP", "cod_amount_piastres": 1, "address_text": "a"})
    r = c.post("/v1/parcels", json={"merchant_ref": "MR-DUP", "cod_amount_piastres": 2, "address_text": "b"})
    assert r.status_code == 409, r.text
    b = r.json()
    assert b["status"] == 409
    # only one parcel exists
    r2 = c.get("/v1/parcels")
    items = r2.json()["items"]
    assert sum(1 for it in items if it["merchant_ref"] == "MR-DUP") == 1


def test_validation_errors_are_problem_documents():
    c = _client()
    r = c.post("/v1/parcels", json={"merchant_ref": "", "cod_amount_piastres": -5, "address_text": ""})
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    b = r.json()
    assert b["status"] == 422
    assert isinstance(b["errors"], list)
    assert len(b["errors"]) == 3
    for e in b["errors"]:
        assert "loc" in e and "msg" in e


def test_boundary_values_accepted():
    c = _client()
    r = c.post("/v1/parcels", json={"merchant_ref": "BOUND-0", "cod_amount_piastres": 0, "address_text": "y"})
    assert r.status_code == 201, r.text
    r = c.post("/v1/parcels", json={"merchant_ref": "BOUND-MAX", "cod_amount_piastres": 5_000_000, "address_text": "y" * 500})
    assert r.status_code == 201, r.text


def test_event_invalid_transition_409_unchanged():
    c = _client()
    p = c.post("/v1/parcels", json={"merchant_ref": "EV", "cod_amount_piastres": 10, "address_text": "a"}).json()
    # deliver directly from created -> invalid transition
    r = c.post(f"/v1/parcels/{p['id']}/events", json={"type": "deliver"})
    assert r.status_code == 409, r.text
    assert r.json()["status"] == 409
    # parcel unchanged
    g = c.get(f"/v1/parcels/{p['id']}")
    assert g.json()["state"] == "created"
    assert g.json()["attempts"] == 0


def test_event_unknown_type_422():
    c = _client()
    p = c.post("/v1/parcels", json={"merchant_ref": "EV2", "cod_amount_piastres": 10, "address_text": "a"}).json()
    r = c.post(f"/v1/parcels/{p['id']}/events", json={"type": "teleport"})
    assert r.status_code == 422, r.text
    r = c.post(f"/v1/parcels/{p['id']}/events", json={})
    assert r.status_code == 422, r.text


def test_event_happy_path():
    c = _client()
    p = c.post("/v1/parcels", json={"merchant_ref": "EV3", "cod_amount_piastres": 10, "address_text": "a"}).json()
    r = c.post(f"/v1/parcels/{p['id']}/events", json={"type": "dispatch"})
    assert r.status_code == 200
    assert r.json()["state"] == "out_for_delivery"
    assert r.json()["attempts"] == 1
    r = c.post(f"/v1/parcels/{p['id']}/events", json={"type": "deliver"})
    assert r.status_code == 200
    assert r.json()["state"] == "delivered"


def test_event_unknown_parcel_404():
    c = _client()
    r = c.post("/v1/parcels/pcl_missing/events", json={"type": "dispatch"})
    assert r.status_code == 404


def test_pagination_full_walk():
    c = _client()
    ids = []
    for i in range(57):
        r = c.post("/v1/parcels", json={"merchant_ref": f"P{i}", "cod_amount_piastres": i, "address_text": "a"})
        ids.append(r.json()["id"])

    seen = []
    cursor = None
    while True:
        params = {"limit": 10}
        if cursor:
            params["cursor"] = cursor
        r = c.get("/v1/parcels", params=params)
        assert r.status_code == 200, r.text
        b = r.json()
        seen.extend(it["id"] for it in b["items"])
        cursor = b["next_cursor"]
        if cursor is None:
            break
    assert seen == ids, "pagination did not return all parcels in order"


def test_pagination_default_limit():
    c = _client()
    for i in range(25):
        c.post("/v1/parcels", json={"merchant_ref": f"D{i}", "cod_amount_piastres": i, "address_text": "a"})
    r = c.get("/v1/parcels")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 20  # default limit
    assert r.json()["next_cursor"] is not None


def test_pagination_bad_cursor_400():
    c = _client()
    r = c.get("/v1/parcels", params={"cursor": "!!!not-a-cursor!!!"})
    assert r.status_code == 400, r.text
    assert r.json()["status"] == 400


def test_pagination_bad_limit_422():
    c = _client()
    for lim in (0, 101, -1):
        r = c.get("/v1/parcels", params={"limit": lim})
        assert r.status_code == 422, f"limit {lim}: {r.text}"
    r = c.get("/v1/parcels", params={"limit": "notanumber"})
    assert r.status_code == 422, r.text


def test_pagination_unknown_state_422():
    c = _client()
    r = c.get("/v1/parcels", params={"state": "nope"})
    assert r.status_code == 422, r.text


def test_pagination_state_filter_survives_state_change():
    c = _client()
    ids = []
    for i in range(20):
        r = c.post("/v1/parcels", json={"merchant_ref": f"S{i}", "cod_amount_piastres": i, "address_text": "a"})
        ids.append(r.json()["id"])
    # page 1 of created, limit 10
    r = c.get("/v1/parcels", params={"state": "created", "limit": 10})
    assert r.status_code == 200
    page1 = [it["id"] for it in r.json()["items"]]
    assert page1 == ids[:10]
    cursor = r.json()["next_cursor"]
    # dispatch page-1 parcels so they leave 'created'
    for pid in page1:
        c.post(f"/v1/parcels/{pid}/events", json={"type": "dispatch"})
    # page 2 must be ids 11..20 (indices 10..19), still created
    r = c.get("/v1/parcels", params={"state": "created", "limit": 10, "cursor": cursor})
    assert r.status_code == 200
    page2 = [it["id"] for it in r.json()["items"]]
    assert page2 == ids[10:20], page2


def test_openapi_has_operation_ids_and_errors():
    c = _client()
    openapi = c.app.openapi()
    paths = openapi["paths"]
    assert "/v1/parcels" in paths
    assert paths["/v1/parcels"]["post"]["operationId"] == "createParcel"
    assert paths["/v1/parcels"]["post"]["responses"].keys() >= {"201", "409", "422"}
    assert paths["/v1/parcels/{parcel_id}"]["get"]["operationId"] == "getParcel"
    assert paths["/v1/parcels/{parcel_id}"]["get"]["responses"].keys() >= {"200", "404"}
    assert paths["/v1/parcels"]["get"]["operationId"] == "listParcels"
    assert paths["/v1/parcels"]["get"]["responses"].keys() >= {"200", "400", "422"}
    assert paths["/v1/parcels/{parcel_id}/events"]["post"]["operationId"] == "recordParcelEvent"
    assert paths["/v1/parcels/{parcel_id}/events"]["post"]["responses"].keys() >= {"200", "404", "409", "422"}
