from datetime import date

from app.services.barcode_service import gtin_checksum_valid
from app.services.ocr_service import OcrLine, OcrResult, Word, group_words_into_lines
from app.services.product_label_service import parse_label


def ocr(lines):
    return OcrResult(engine="test", lines=[OcrLine(text, height, 0.9) for text, height in lines])


def test_label_fields_extracted_and_never_auto_confirmed():
    result = parse_label(
        ocr(
            [
                ("Brand: GreenLeaf", 20),
                ("Toned Milk", 60),
                ("Net Qty: 500 ml", 22),
                ("MFG: 25/10/2026", 22),
                ("EXP: 01/11/2026", 22),
                ("Batch No: A123", 22),
            ]
        ),
        today=date(2026, 10, 29),
        use_model=False,
    )
    fields = result["fields"]
    assert fields["product_name"]["value"] == "Toned Milk"
    assert fields["brand"]["value"] == "GreenLeaf"
    assert fields["package_size"]["value"] == "500 ml"
    assert fields["batch_number"]["value"] == "A123"
    assert fields["manufacturing_date"]["value"] == "2026-10-25"
    assert fields["expiry_date"]["value"] == "2026-11-01"
    # Suggestions only: everything except a clean labelled date needs review.
    assert fields["product_name"]["status"] == "uncertain"
    assert fields["expiry_date"]["status"] in {"uncertain", "detected"}


def test_text_only_llm_hints_must_be_grounded(monkeypatch):
    from app.schemas.extraction import LabelLLMHints
    from app.services import product_label_service as pls

    monkeypatch.setattr(pls.get_settings(), "llm_enabled", True)
    monkeypatch.setattr(
        pls,
        "_llm_hints",
        lambda text, image: (LabelLLMHints(product_name="Organic Almond Milk", brand="Invented Brand"), "llm", None),
    )
    result = pls.parse_label(ocr([("Toned Milk", 60), ("EXP: 01/11/2026", 20)]), today=date(2026, 10, 29))
    assert result["fields"]["product_name"]["value"] == "Toned Milk"
    assert result["fields"]["brand"]["value"] is None


def test_gtin_checksum():
    assert gtin_checksum_valid("8901234567890")
    assert not gtin_checksum_valid("8901234567894")
    assert gtin_checksum_valid("036000291452")  # UPC-A
    assert not gtin_checksum_valid("12345")


def test_words_grouped_into_rows_with_column_gaps():
    words = [Word("Milk", 10, 100, 40, 20), Word("20", 300, 102, 20, 20), Word("Total", 10, 200, 50, 20)]
    lines = group_words_into_lines(words)
    assert [line.text for line in lines] == ["Milk  20", "Total"]
