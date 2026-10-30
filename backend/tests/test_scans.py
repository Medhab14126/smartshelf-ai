import shutil

import pytest

from tests.conftest import FIXTURES, create_batch

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract OCR not installed")


def upload_scan(client, headers, name="label_milk.png", **form):
    with (FIXTURES / name).open("rb") as fh:
        return client.post("/api/scans/product", files={"file": (name, fh, "image/png")}, data=form, headers=headers)


def test_scan_extracts_label_and_requires_confirmation(client, auth, db):
    scan = upload_scan(client, auth).json()
    scan = client.get(f"/api/scans/{scan['id']}", headers=auth).json()
    assert scan["status"] == "awaiting_confirmation"
    fields = scan["extraction"]["fields"]
    assert fields["expiry_date"]["value"] == "2026-11-01"
    assert fields["manufacturing_date"]["value"] == "2026-10-25"
    assert fields["batch_number"]["value"] == "A123"
    assert fields["barcode"]["value"] == "8901234567890"
    assert "EXP" in scan["raw_text"]

    # Saving without confirming the expiry keeps the date unverified.
    saved = client.post(
        f"/api/scans/{scan['id']}/confirm",
        json={
            "new_product": {"name": "Toned Milk"},
            "quantity": 12,
            "unit": "packet",
            "expiry_date": "2026-11-01",
            "confirm_expiry": False,
        },
        headers=auth,
    ).json()
    assert saved["status"] == "confirmed" and saved["confirmed"]["confirm_expiry"] is False
    batch = client.get(f"/api/inventory/batches/{saved['batch_id']}", headers=auth).json()
    assert batch["status"] == "requires_confirmation"
    assert batch["product"]["barcode"] == "8901234567890"
    assert batch["image_scan_id"] == scan["id"]
    assert client.get(f"/api/scans/{scan['id']}/image", headers=auth).status_code == 200


def test_scan_confirms_expiry_on_existing_batch_and_matches_barcode(client, auth):
    batch = create_batch(client, auth, name="Toned Milk")
    first = upload_scan(client, auth, batch_id=batch["id"]).json()
    assert first["batch_id"] == batch["id"]
    confirmed = client.post(
        f"/api/scans/{first['id']}/confirm",
        json={
            "batch_id": batch["id"],
            "expiry_date": "2026-11-01",
            "manufacturing_date": "2026-10-25",
            "batch_number": "A123",
            "confirm_expiry": True,
        },
        headers=auth,
    )
    assert confirmed.status_code == 200, confirmed.text
    updated = client.get(f"/api/inventory/batches/{batch['id']}", headers=auth).json()
    assert updated["expiry_date_status"] == "confirmed" and updated["batch_number"] == "A123"

    # A second photo of the same product is matched via the stored barcode.
    second = upload_scan(client, auth).json()
    assert client.get(f"/api/scans/{second['id']}", headers=auth).json()["matched_product_id"] == batch["product"]["id"]


def test_confirm_requires_date_when_confirming(client, auth):
    scan = upload_scan(client, auth).json()
    response = client.post(
        f"/api/scans/{scan['id']}/confirm",
        json={"new_product": {"name": "Milk"}, "quantity": 1, "unit": "pcs", "confirm_expiry": True},
        headers=auth,
    )
    assert response.status_code == 422


def test_ambiguous_label_is_flagged(client, auth):
    scan = upload_scan(client, auth, "label_ambiguous.png").json()
    fields = client.get(f"/api/scans/{scan['id']}", headers=auth).json()["extraction"]["fields"]
    assert fields["expiry_date"]["status"] == "ambiguous"
    assert set(fields["expiry_date"]["options"]) == {"2027-04-05", "2027-05-04"}


def test_rejects_non_image(client, auth):
    response = client.post(
        "/api/scans/product",
        files={"file": ("x.pdf", (FIXTURES / "invoice_dairy.pdf").read_bytes(), "application/pdf")},
        headers=auth,
    )
    assert response.status_code == 422


def test_scan_pack_size_and_delete(client, auth, db):
    from app.models.scan import ProductScan
    from app.services.storage_service import resolve_path

    scan = upload_scan(client, auth).json()
    confirmed = client.post(
        f"/api/scans/{scan['id']}/confirm",
        json={"new_product": {"name": "Toned Milk"}, "quantity": 12, "pack_size": 500, "pack_unit": "ml"},
        headers=auth,
    )
    assert confirmed.status_code == 200, confirmed.text
    batch_id = confirmed.json()["batch_id"]
    batch = client.get(f"/api/inventory/batches/{batch_id}", headers=auth).json()
    assert (batch["current_quantity"], batch["unit"], batch["pack_size"], batch["pack_unit"]) == (
        "12.000", "pcs", "500.000", "ml",
    )  # fmt: skip

    path = resolve_path(db.get(ProductScan, scan["id"]).storage_path)
    response = client.delete(f"/api/scans/{scan['id']}", headers=auth)
    assert response.status_code == 200 and response.json() == {"batches_cleared": 1}
    assert client.get(f"/api/scans/{scan['id']}", headers=auth).status_code == 404 and not path.exists()
    after = client.get(f"/api/inventory/batches/{batch_id}", headers=auth).json()
    assert after["current_quantity"] == "0.000" and after["status"] == "depleted"


def test_deleting_a_date_check_scan_keeps_the_batch(client, auth):
    batch = create_batch(client, auth, expiry_date=None)
    scan = upload_scan(client, auth).json()
    client.post(
        f"/api/scans/{scan['id']}/confirm",
        json={"batch_id": batch["id"], "expiry_date": "2026-11-01", "confirm_expiry": True},
        headers=auth,
    )
    assert client.delete(f"/api/scans/{scan['id']}", headers=auth).json() == {"batches_cleared": 0}
    kept = client.get(f"/api/inventory/batches/{batch['id']}", headers=auth).json()
    assert kept["current_quantity"] == "10.000" and kept["expiry_date_status"] == "confirmed"
