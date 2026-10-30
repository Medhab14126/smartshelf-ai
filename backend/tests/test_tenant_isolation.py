from pathlib import Path

from tests.conftest import FIXTURES, create_batch, register


def test_businesses_cannot_access_each_other(client):
    owner_a = register(client, "a@example.com", "Cafe A")
    owner_b = register(client, "b@example.com", "Cafe B")
    batch = create_batch(client, owner_a, expiry_date="2026-12-01")
    with Path(FIXTURES / "invoice_dairy.pdf").open("rb") as fh:
        invoice = client.post(
            "/api/invoices/upload", files={"file": ("inv.pdf", fh, "application/pdf")}, headers=owner_a
        ).json()["invoice"]
    with Path(FIXTURES / "label_milk.png").open("rb") as fh:
        scan = client.post("/api/scans/product", files={"file": ("label.png", fh, "image/png")}, headers=owner_a).json()

    # Lists only show own data.
    assert client.get("/api/inventory/batches", headers=owner_b).json()["total"] == 0
    assert client.get("/api/invoices", headers=owner_b).json()["total"] == 0
    assert client.get("/api/products", headers=owner_b).json()["total"] == 0
    assert client.get("/api/dashboard/summary", headers=owner_b).json()["active_batches"] == 0

    # Direct access by ID returns 404 (no information leak).
    batch_id, product_id, invoice_id = batch["id"], batch["product"]["id"], invoice["id"]
    for method, url, body in [
        ("get", f"/api/inventory/batches/{batch_id}", None),
        ("patch", f"/api/inventory/batches/{batch_id}", {"notes": "hacked"}),
        ("post", f"/api/inventory/batches/{batch_id}/adjust-stock", {"movement_type": "discarded", "quantity": 1}),
        ("get", f"/api/inventory/batches/{batch_id}/movements", None),
        ("get", f"/api/products/{product_id}", None),
        ("patch", f"/api/products/{product_id}", {"name": "x"}),
        ("get", f"/api/invoices/{invoice_id}", None),
        ("get", f"/api/invoices/{invoice_id}/file", None),
        ("post", f"/api/invoices/{invoice_id}/confirm", {}),
        ("delete", f"/api/invoices/{invoice_id}", None),
        ("get", f"/api/scans/{scan['id']}", None),
        ("delete", f"/api/scans/{scan['id']}", None),
    ]:
        response = getattr(client, method)(url, headers=owner_b, **({"json": body} if body is not None else {}))
        assert response.status_code == 404, (url, response.status_code)

    # Cross-tenant references in create payloads are rejected too.
    response = client.post(
        "/api/inventory/batches", json={"product_id": product_id, "quantity": 1, "unit": "pcs"}, headers=owner_b
    )
    assert response.status_code == 404
    # Owner A's data is untouched.
    assert client.get(f"/api/scans/{scan['id']}", headers=owner_a).status_code == 200
    assert client.get(f"/api/inventory/batches/{batch_id}", headers=owner_a).json()["current_quantity"] == "10.000"
