import shutil

import pytest

from tests.conftest import FIXTURES


def upload(client, headers, name, content_type="application/pdf", data=None):
    payload = data if data is not None else (FIXTURES / name).read_bytes()
    return client.post("/api/invoices/upload", files={"file": (name, payload, content_type)}, headers=headers)


def test_pdf_invoice_upload_extract_review_confirm_and_add(client, auth):
    response = upload(client, auth, "invoice_dairy.pdf")
    assert response.status_code == 201
    invoice_id = response.json()["invoice"]["id"]

    invoice = client.get(f"/api/invoices/{invoice_id}", headers=auth).json()
    assert invoice["status"] == "awaiting_review"
    assert invoice["supplier_name"] == "GreenLeaf Dairy Supplies"
    assert invoice["invoice_number"] == "GLD-2026-0457"
    assert invoice["invoice_date"] == "2026-10-27"
    assert invoice["total"] == "2775.16"
    assert invoice["raw_text"] and invoice["ocr_engine"] == "pdf-text"
    items = invoice["items"]
    assert [i["product_name"] for i in items] == [
        "Toned Milk 500ml",
        "Paneer 200g",
        "Fresh Cream 1L",
        "Greek Yogurt 400g",
    ]
    assert items[0]["extracted"]["quantity"] == "20"

    # Review: fix a line, reject another, then confirm.
    edited = client.patch(
        f"/api/invoices/{invoice_id}/items/{items[1]['id']}", json={"quantity": 6, "line_total": 540}, headers=auth
    ).json()
    assert edited["edited"] is True and edited["extracted"]["quantity"] == "5"  # original kept for audit
    confirmed = client.post(
        f"/api/invoices/{invoice_id}/confirm",
        json={"rejected_item_ids": [items[3]["id"]], "add_to_inventory": False},
        headers=auth,
    ).json()
    assert confirmed["status"] == "confirmed"
    assert [i["status"] for i in confirmed["items"]] == ["accepted", "accepted", "accepted", "rejected"]

    # One invoice line split into two batches with different expiry dates.
    added = client.post(
        f"/api/invoices/{invoice_id}/items/{items[0]['id']}/add-to-inventory",
        json={
            "new_product": {"name": "Toned Milk 500ml", "category": "Dairy"},
            "unit": "packet",
            "batches": [
                {"quantity": 10, "batch_number": "A", "expiry_date": "2026-11-02"},
                {"quantity": 10, "batch_number": "B", "expiry_date": "2026-11-05"},
            ],
        },
        headers=auth,
    )
    assert added.status_code == 201, added.text
    batches = added.json()
    assert [b["expiry_date"] for b in batches] == ["2026-11-02", "2026-11-05"]
    assert all(b["invoice_id"] == invoice_id and b["supplier"]["name"] == "GreenLeaf Dairy Supplies" for b in batches)
    assert batches[0]["purchase_price"] == "30.0000" and batches[0]["received_date"] == "2026-10-27"

    again = client.post(
        f"/api/invoices/{invoice_id}/items/{items[0]['id']}/add-to-inventory",
        json={"new_product": {"name": "Toned Milk 500ml"}, "unit": "packet", "batches": [{"quantity": 1}]},
        headers=auth,
    )
    assert again.status_code == 409
    over = client.post(
        f"/api/invoices/{invoice_id}/items/{items[2]['id']}/add-to-inventory",
        json={"new_product": {"name": "Fresh Cream 1L"}, "unit": "bottle", "batches": [{"quantity": 5}]},
        headers=auth,
    )
    assert over.status_code == 422
    rejected = client.post(
        f"/api/invoices/{invoice_id}/items/{items[3]['id']}/add-to-inventory",
        json={"new_product": {"name": "Yogurt"}, "unit": "tub", "batches": [{"quantity": 1}]},
        headers=auth,
    )
    assert rejected.status_code == 409


def test_confirm_adds_accepted_lines_to_inventory(client, auth):
    # An existing catalogue entry with the same name is reused, not duplicated.
    existing = client.post("/api/products", json={"name": "Paneer 200g", "category": "Dairy"}, headers=auth).json()
    invoice_id = upload(client, auth, "invoice_dairy.pdf").json()["invoice"]["id"]
    items = client.get(f"/api/invoices/{invoice_id}/items", headers=auth).json()
    # Pack sizes are read from the product names; quantities count packs.
    assert [(i["pack_size"], i["pack_unit"]) for i in items] == [
        ("500.000", "ml"), ("200.000", "g"), ("1.000", "ltr"), ("400.000", "g"),
    ]  # fmt: skip
    client.patch(f"/api/invoices/{invoice_id}/items/{items[2]['id']}", json={"unit": None}, headers=auth)
    client.patch(f"/api/invoices/{invoice_id}/items/{items[0]['id']}", json={"quantity": None}, headers=auth)

    confirmed = client.post(
        f"/api/invoices/{invoice_id}/confirm", json={"rejected_item_ids": [items[3]["id"]]}, headers=auth
    ).json()
    statuses = [i["status"] for i in confirmed["items"]]
    assert statuses == ["accepted", "added_to_inventory", "added_to_inventory", "rejected"]
    # A line missing its quantity is not guessed; it stays for the user to add by hand.
    assert any("quantity missing" in w for w in confirmed["items"][0]["warnings"])
    assert not any("unit" in w for w in confirmed["items"][2]["warnings"])  # counts need no warning
    assert confirmed["items"][2]["extracted"]["unit"] == "bottle"  # extracted value kept for audit

    batches = client.get("/api/inventory/batches", headers=auth).json()["items"]
    by_name = {b["product"]["name"]: b for b in batches}
    assert set(by_name) == {"Paneer 200g", "Fresh Cream 1L"}
    cream = by_name["Fresh Cream 1L"]
    assert cream["current_quantity"] == "4.000" and cream["unit"] == "pcs"
    assert (cream["pack_size"], cream["pack_unit"]) == ("1.000", "ltr")
    assert cream["invoice_id"] == invoice_id and cream["purchase_price"] == "210.0000"
    assert cream["received_date"] == "2026-10-27" and cream["supplier"]["name"] == "GreenLeaf Dairy Supplies"
    # Invoices carry no expiry dates, so none is invented.
    assert cream["expiry_date"] is None and cream["status"] == "no_expiry_date"
    paneer = by_name["Paneer 200g"]
    assert paneer["product"]["id"] == existing["id"] and (paneer["pack_size"], paneer["pack_unit"]) == ("200.000", "g")
    movements = client.get(f"/api/inventory/batches/{cream['id']}/movements", headers=auth).json()["items"]
    assert [m["movement_type"] for m in movements] == ["received"]

    # The skipped line can still be added manually once its quantity is known, with an edited pack size.
    manual = client.post(
        f"/api/invoices/{invoice_id}/items/{items[0]['id']}/add-to-inventory",
        json={
            "new_product": {"name": "Toned Milk 500ml"},
            "pack_size": 1,
            "pack_unit": "ltr",
            "batches": [{"quantity": 20}],
        },
        headers=auth,
    )
    assert manual.status_code == 201, manual.text
    added = manual.json()[0]
    assert (added["unit"], float(added["pack_size"]), added["pack_unit"]) == ("pcs", 1.0, "ltr")
    half = client.post(
        f"/api/invoices/{invoice_id}/items/{items[0]['id']}/add-to-inventory",
        json={"new_product": {"name": "Toned Milk 500ml"}, "pack_size": 500, "batches": [{"quantity": 1}]},
        headers=auth,
    )
    assert half.status_code == 422  # a size needs its unit


def test_loose_goods_become_packs_of_one_unit():
    from decimal import Decimal

    from app.services.units import pack_from_line

    assert pack_from_line("Sugar", "Kgs") == (Decimal(1), "kg")
    assert pack_from_line("Milk 500ml", "packet") == (Decimal(500), "ml")
    assert pack_from_line("Sourdough Loaf", None) is None and pack_from_line("Bun", "pcs") is None


def test_must_confirm_before_adding_and_duplicate_invoice_detection(client, auth):
    first = upload(client, auth, "invoice_dairy.pdf").json()
    invoice_id = first["invoice"]["id"]
    item_id = client.get(f"/api/invoices/{invoice_id}/items", headers=auth).json()[0]["id"]
    early = client.post(
        f"/api/invoices/{invoice_id}/items/{item_id}/add-to-inventory",
        json={"new_product": {"name": "Milk"}, "unit": "packet", "batches": [{"quantity": 1}]},
        headers=auth,
    )
    assert early.status_code == 409

    same_file = upload(client, auth, "invoice_dairy.pdf").json()
    assert same_file["duplicate"] is True and same_file["invoice"]["id"] == invoice_id

    # Same invoice number re-scanned as a different file.
    other = upload(client, auth, "copy.pdf", data=(FIXTURES / "invoice_dairy.pdf").read_bytes() + b"\n%copy").json()[
        "invoice"
    ]
    detail = client.get(f"/api/invoices/{other['id']}", headers=auth).json()
    assert any("already been uploaded" in w for w in detail["warnings"])
    assert client.post(f"/api/invoices/{invoice_id}/confirm", json={}, headers=auth).status_code == 200
    blocked = client.post(f"/api/invoices/{other['id']}/confirm", json={}, headers=auth)
    assert blocked.status_code == 409
    assert (
        client.post(f"/api/invoices/{other['id']}/confirm", json={"allow_duplicate": True}, headers=auth).status_code
        == 200
    )


def test_multipage_pdf(client, auth):
    invoice_id = upload(client, auth, "invoice_multipage.pdf").json()["invoice"]["id"]
    invoice = client.get(f"/api/invoices/{invoice_id}", headers=auth).json()
    assert invoice["page_count"] == 2 and len(invoice["items"]) == 30
    assert invoice["total"] == "9930.00"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract OCR not installed")
def test_image_invoice_uses_ocr(client, auth):
    invoice_id = upload(client, auth, "invoice_bakery.png", "image/png").json()["invoice"]["id"]
    invoice = client.get(f"/api/invoices/{invoice_id}", headers=auth).json()
    assert invoice["status"] == "awaiting_review" and invoice["ocr_engine"] in ("tesseract", "paddleocr")
    assert invoice["invoice_number"] == "FB-88213"
    assert [i["product_name"] for i in invoice["items"]] == ["Sourdough Loaf", "Butter Croissant", "Multigrain Bun"]


def test_rejects_invalid_files(client, auth):
    fake_pdf = upload(client, auth, "evil.pdf", data=b"MZ\x90\x00 not a pdf")
    assert fake_pdf.status_code == 422 and "Unsupported" in fake_pdf.json()["detail"]
    broken = upload(client, auth, "broken.pdf", data=b"%PDF-1.4 garbage")
    assert broken.status_code == 422
    assert upload(client, auth, "empty.pdf", data=b"").status_code == 422


def test_rejects_oversized_upload(client, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    assert upload(client, auth, "invoice_dairy.pdf").status_code == 413


def test_unreadable_document_fails_with_manual_entry(client, auth):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buffer, format="PNG")
    invoice_id = upload(client, auth, "blank.png", "image/png", buffer.getvalue()).json()["invoice"]["id"]
    invoice = client.get(f"/api/invoices/{invoice_id}", headers=auth).json()
    assert invoice["status"] == "failed" and "manually" in invoice["error_message"]

    # Manual entry on the failed invoice, then confirm.
    client.patch(
        f"/api/invoices/{invoice_id}", json={"supplier_name": "Local Market", "invoice_number": "M-1"}, headers=auth
    )
    item = client.post(
        f"/api/invoices/{invoice_id}/items",
        json={"product_name": "Bananas", "quantity": 12, "unit": "pcs"},
        headers=auth,
    )
    assert item.status_code == 201
    confirmed = client.post(f"/api/invoices/{invoice_id}/confirm", json={}, headers=auth).json()
    assert confirmed["status"] == "confirmed" and confirmed["supplier_name"] == "Local Market"


def test_invoice_file_is_served_only_through_api(client, auth):
    invoice_id = upload(client, auth, "invoice_dairy.pdf").json()["invoice"]["id"]
    response = client.get(f"/api/invoices/{invoice_id}/file", headers=auth)
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    client.cookies.clear()
    assert client.get(f"/api/invoices/{invoice_id}/file").status_code == 401


def test_delete_unconfirmed_invoice_removes_lines_and_file(client, auth, db):
    from app.models.invoice import Invoice, InvoiceItem
    from app.services.storage_service import resolve_path

    invoice_id = upload(client, auth, "invoice_dairy.pdf").json()["invoice"]["id"]
    path = resolve_path(db.get(Invoice, invoice_id).storage_path)
    assert path.exists()

    response = client.delete(f"/api/invoices/{invoice_id}", headers=auth)
    assert response.status_code == 200 and response.json() == {"batches_cleared": 0}
    assert client.get(f"/api/invoices/{invoice_id}", headers=auth).status_code == 404
    assert not path.exists()
    db.expire_all()
    assert db.query(InvoiceItem).filter_by(invoice_id=invoice_id).count() == 0
    # The same file can be uploaded again afterwards instead of being reported as a duplicate.
    assert upload(client, auth, "invoice_dairy.pdf").json()["duplicate"] is False


def test_delete_confirmed_invoice_removes_its_remaining_stock(client, auth, db):
    from app.models.audit import AuditEvent

    invoice_id = upload(client, auth, "invoice_dairy.pdf").json()["invoice"]["id"]
    client.post(f"/api/invoices/{invoice_id}/confirm", json={}, headers=auth)
    batches = client.get("/api/inventory/batches", headers=auth).json()["items"]
    assert len(batches) == 4
    milk = next(b for b in batches if b["product"]["name"] == "Toned Milk 500ml")
    client.post(
        f"/api/inventory/batches/{milk['id']}/adjust-stock",
        json={"movement_type": "consumed", "quantity": 20},
        headers=auth,
    )  # already used up: nothing left to remove

    response = client.delete(f"/api/invoices/{invoice_id}", headers=auth)
    assert response.status_code == 200 and response.json() == {"batches_cleared": 3}

    # Batches stay for the history, emptied by a recorded correction and unlinked from the invoice.
    for batch in batches:
        after = client.get(f"/api/inventory/batches/{batch['id']}", headers=auth).json()
        assert after["current_quantity"] == "0.000" and after["status"] == "depleted" and after["invoice_id"] is None
    paneer = next(b for b in batches if b["product"]["name"] == "Paneer 200g")
    movements = client.get(f"/api/inventory/batches/{paneer['id']}/movements", headers=auth).json()["items"]
    correction = next(m for m in movements if m["movement_type"] == "correction")
    assert correction["quantity_delta"] == "-5.000" and correction["reason"] == "Invoice GLD-2026-0457 deleted"
    assert db.query(AuditEvent).filter_by(action="invoice.deleted").count() == 1


def test_cannot_delete_invoice_while_it_is_being_read(client, auth, db):
    from app.models.invoice import Invoice

    invoice_id = upload(client, auth, "invoice_dairy.pdf").json()["invoice"]["id"]
    db.get(Invoice, invoice_id).status = "processing"
    db.commit()
    assert client.delete(f"/api/invoices/{invoice_id}", headers=auth).status_code == 409
