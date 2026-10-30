from datetime import date, timedelta

from tests.conftest import create_batch


def test_manual_batch_creation_records_received_movement(client, auth):
    batch = create_batch(client, auth, batch_number="A", expiry_date="2026-11-02", supplier_name="GreenLeaf Dairy")
    assert batch["status"] in {"expiring_soon", "fresh", "expiring_today", "expired"}
    assert batch["expiry_date_status"] == "confirmed"
    assert batch["supplier"]["name"] == "GreenLeaf Dairy"
    movements = client.get(f"/api/inventory/batches/{batch['id']}/movements", headers=auth).json()
    assert movements["total"] == 1 and movements["items"][0]["movement_type"] == "received"
    assert movements["items"][0]["created_by_name"] == "Asha Rao"


def test_same_product_multiple_batches_with_different_expiry(client, auth):
    first = create_batch(client, auth, batch_number="A", expiry_date="2026-11-02")
    second = client.post(
        "/api/inventory/batches",
        json={
            "product_id": first["product"]["id"],
            "quantity": 10,
            "unit": "packet",
            "batch_number": "B",
            "expiry_date": "2026-11-05",
        },
        headers=auth,
    ).json()
    assert second["product"]["id"] == first["product"]["id"]
    listing = client.get("/api/inventory/batches", params={"product_id": first["product"]["id"]}, headers=auth).json()
    assert [b["expiry_date"] for b in listing["items"]] == ["2026-11-02", "2026-11-05"]
    product = client.get(f"/api/products/{first['product']['id']}", headers=auth).json()
    assert product["active_batches"] == 2 and product["total_quantity"] == "20.000"


def test_stock_adjustments_and_negative_stock_prevention(client, auth):
    batch = create_batch(client, auth)
    url = f"/api/inventory/batches/{batch['id']}/adjust-stock"
    assert (
        client.post(url, json={"movement_type": "consumed", "quantity": 3}, headers=auth).json()["current_quantity"]
        == "7.000"
    )
    assert (
        client.post(url, json={"movement_type": "sold", "quantity": 2}, headers=auth).json()["current_quantity"]
        == "5.000"
    )
    too_much = client.post(url, json={"movement_type": "discarded", "quantity": 6}, headers=auth)
    assert too_much.status_code == 422 and "only 5" in too_much.json()["detail"]
    assert (
        client.post(url, json={"movement_type": "correction", "new_quantity": 4}, headers=auth).status_code == 422
    )  # reason required
    corrected = client.post(
        url, json={"movement_type": "correction", "new_quantity": 4, "reason": "Shelf count"}, headers=auth
    ).json()
    assert corrected["current_quantity"] == "4.000"
    depleted = client.post(
        url, json={"movement_type": "returned", "quantity": 4, "reason": "Damaged"}, headers=auth
    ).json()
    assert depleted["current_quantity"] == "0.000" and depleted["status"] == "depleted"
    history = client.get(f"/api/inventory/batches/{batch['id']}/movements", headers=auth).json()["items"]
    assert [m["movement_type"] for m in history] == ["returned", "correction", "sold", "consumed", "received"]
    assert [m["quantity_after"] for m in history] == ["0.000", "4.000", "5.000", "7.000", "10.000"]
    assert client.post(url, json={"movement_type": "consumed", "quantity": 0}, headers=auth).status_code == 422


def test_audit_events_recorded(client, auth, db):
    from app.models.audit import AuditEvent

    batch = create_batch(client, auth)
    client.post(
        f"/api/inventory/batches/{batch['id']}/adjust-stock",
        json={"movement_type": "discarded", "quantity": 1},
        headers=auth,
    )
    actions = {e.action for e in db.query(AuditEvent).all()}
    assert {"batch.created", "stock.discarded"} <= actions


def test_filters_search_and_sorting(client, auth):
    today = date.today()
    create_batch(client, auth, name="Milk 500ml", expiry_date=str(today + timedelta(days=2)))
    create_batch(client, auth, name="Croissant", category="Bakery", expiry_date=str(today - timedelta(days=1)))
    create_batch(client, auth, name="Sugar", category="Dry goods")
    create_batch(client, auth, name="Cream", expiry_date=str(today + timedelta(days=30)), expiry_confirmed=False)

    def statuses(**params):
        return [
            b["product"]["name"]
            for b in client.get("/api/inventory/batches", params=params, headers=auth).json()["items"]
        ]

    assert statuses(status="expired") == ["Croissant"]
    assert statuses(status="no_expiry_date") == ["Sugar"]
    assert statuses(status="requires_confirmation") == ["Cream"]
    assert statuses(q="crois") == ["Croissant"]
    assert statuses(category="Bakery") == ["Croissant"]
    assert statuses(expires_within_days=7) == ["Milk 500ml"]
    assert statuses(sort="expiry_asc")[:2] == ["Croissant", "Milk 500ml"]
    assert statuses(sort="name_asc") == ["Cream", "Croissant", "Milk 500ml", "Sugar"]


def test_edit_batch_and_expiry_validation(client, auth):
    batch = create_batch(client, auth, manufacturing_date="2026-10-01")
    url = f"/api/inventory/batches/{batch['id']}"
    bad = client.patch(url, json={"expiry_date": "2026-09-01"}, headers=auth)
    assert bad.status_code == 422
    updated = client.patch(url, json={"expiry_date": "2026-12-01", "batch_number": "LOT9"}, headers=auth).json()
    assert updated["expiry_date_status"] == "confirmed" and updated["batch_number"] == "LOT9"
    unconfirmed = client.patch(url, json={"expiry_confirmed": False}, headers=auth).json()
    assert unconfirmed["status"] == "requires_confirmation"
    cleared = client.patch(url, json={"expiry_date": None}, headers=auth).json()
    assert cleared["status"] == "no_expiry_date"


def test_product_create_conflict_and_validation(client, auth):
    assert client.post("/api/products", json={"name": "Milk 500 ml", "brand": "Amul"}, headers=auth).status_code == 201
    assert client.post("/api/products", json={"name": "milk 500ml", "brand": "amul"}, headers=auth).status_code == 409
    assert (
        client.post("/api/products", json={"name": "Milk 500ml", "brand": "Nandini"}, headers=auth).status_code == 201
    )
    assert client.post("/api/products", json={"name": "  "}, headers=auth).status_code == 422
    assert client.get("/api/products", params={"q": "milk"}, headers=auth).json()["total"] == 2


def test_dashboard_counts_come_from_data(client, auth):
    today = date.today()
    empty = client.get("/api/dashboard/summary", headers=auth).json()
    assert empty["is_empty"] is True and empty["active_batches"] == 0
    create_batch(client, auth, expiry_date=str(today))
    create_batch(client, auth, name="Bread", expiry_date=str(today + timedelta(days=3)))
    create_batch(client, auth, name="Old Bread", expiry_date=str(today - timedelta(days=2)))
    create_batch(client, auth, name="Cream", expiry_date=str(today + timedelta(days=3)), expiry_confirmed=False)
    summary = client.get("/api/dashboard/summary", headers=auth).json()
    assert summary["active_batches"] == 4
    assert summary["expiring_today"] == 1
    assert summary["expiring_within_7_days"] == 2
    assert summary["expired_needing_attention"] == 1
    assert summary["requires_confirmation"] == 1
    upcoming = client.get("/api/dashboard/upcoming-expiry", headers=auth).json()
    assert [u["product_name"] for u in upcoming] == ["Old Bread", "Milk 500ml", "Bread"]
    assert [
        u["product_name"]
        for u in client.get("/api/dashboard/upcoming-expiry", params={"window": "unconfirmed"}, headers=auth).json()
    ] == ["Cream"]
