import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ai.output_validation import OutputValidationError, parse_structured
from app.schemas.extraction import InvoiceExtraction
from app.services import invoice_extraction_service as ies
from app.services.number_parsing import parse_amount

INVOICE_TEXT = """GreenLeaf Dairy Supplies
TAX INVOICE
Invoice No: GLD-2026-0457      Date: 27/10/2026
S.No  Description          Qty  Unit    Rate    Amount
1     Toned Milk 500ml      20  packet  30.00   600.00
2     Paneer 200g            5  pcs     90.00   450.00
Subtotal                                      1,050.00
CGST @ 2.5%                                      26.25
SGST @ 2.5%                                      26.25
Grand Total                                Rs. 1,102.50
"""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.50", "1234.50"),
        ("1.234,50", "1234.50"),
        ("1,23,456.00", "123456.00"),
        ("₹600/-", "600"),
        ("12,50", "12.50"),
        ("abc", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == (Decimal(expected) if expected else None)


def test_rule_parser_extracts_header_and_items():
    data = ies.parse_invoice_rules(INVOICE_TEXT)
    assert data.supplier_name == "GreenLeaf Dairy Supplies"
    assert data.invoice_number == "GLD-2026-0457"
    assert data.invoice_date == "27/10/2026"
    assert data.currency == "INR"
    assert data.subtotal == Decimal("1050.00")
    assert data.tax == Decimal("52.50")
    assert data.total == Decimal("1102.50")
    assert [(i.product_name, i.quantity, i.unit, i.unit_price, i.line_total) for i in data.items] == [
        ("Toned Milk 500ml", Decimal(20), "packet", Decimal("30.00"), Decimal("600.00")),
        ("Paneer 200g", Decimal(5), "pcs", Decimal("90.00"), Decimal("450.00")),
    ]


def test_rule_parser_handles_other_layouts():
    text = """FRESH BAKE CO.     INVOICE #FB-88213
Invoice Date: October 26, 2026
Item Code  Item              Qty  Price  Total
BR-001     Sourdough Loaf     10  4.50   45.00
Eggs Tray 30  3 trays 180 540
Total $585.00"""
    data = ies.parse_invoice_rules(text)
    assert data.supplier_name == "FRESH BAKE CO."
    assert data.invoice_number == "FB-88213"
    assert data.currency == "USD"
    assert data.items[0].product_code == "BR-001"
    assert data.items[0].product_name == "Sourdough Loaf"
    assert data.items[1].product_name == "Eggs Tray 30"
    assert data.items[1].quantity == 3 and data.items[1].unit == "trays"


def test_validation_flags_wrong_line_totals_and_totals():
    text = INVOICE_TEXT.replace("450.00", "460.00")
    outcome = ies.extract_invoice(text, use_llm=False)
    assert any("quantity × unit price" in w for w in outcome.item_warnings[1])
    assert any("subtotal" in w.lower() for w in outcome.warnings)
    assert outcome.item_confidence[1] < outcome.item_confidence[0]


def test_ambiguous_invoice_date_is_reported():
    outcome = ies.extract_invoice(INVOICE_TEXT.replace("27/10/2026", "03/04/2026"), use_llm=False)
    assert any("ambiguous" in w for w in outcome.warnings)


def test_missing_values_are_not_invented():
    outcome = ies.extract_invoice("Some random note without numbers", use_llm=False)
    data = outcome.extraction
    assert data.items == [] and data.total is None and data.invoice_number is None and data.currency is None
    assert any("No line items" in w for w in outcome.warnings)


def test_schema_rejects_negative_quantities_and_parses_strings():
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate({"items": [{"product_name": "Milk", "quantity": -1}]})
    data = InvoiceExtraction.model_validate(
        {"currency": "₹", "total": "1,102.50", "items": [{"product_name": " Milk  500ml ", "quantity": "20"}]}
    )
    assert data.currency == "INR" and data.total == Decimal("1102.50")
    assert data.items[0].product_name == "Milk 500ml"


def test_parse_structured_rejects_invalid_json():
    with pytest.raises(OutputValidationError):
        parse_structured("not json", InvoiceExtraction)
    assert parse_structured('```json\n{"items": []}\n```', InvoiceExtraction).items == []


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _llm_answer(**overrides):
    answer = {
        "supplier_name": "GreenLeaf Dairy Supplies",
        "invoice_number": "GLD-2026-0457",
        "invoice_date": "27/10/2026",
        "currency": "INR",
        "subtotal": 1050.0,
        "tax": 52.5,
        "total": 1102.5,
        "items": [
            {
                "product_name": "Toned Milk 500ml",
                "quantity": 20,
                "unit": "packet",
                "unit_price": 30.0,
                "line_total": 600.0,
            },
            {"product_name": "Paneer 200g", "quantity": 5, "unit": "pcs", "unit_price": 90.0, "line_total": 450.0},
        ],
    }
    answer.update(overrides)
    return json.dumps(answer)


def test_llm_path_used_when_valid(monkeypatch):
    fake = FakeLLM([_llm_answer()])
    monkeypatch.setattr(ies, "get_llm_client", lambda: fake)
    outcome = ies.extract_invoice(INVOICE_TEXT, use_llm=True)
    assert outcome.method.startswith("llm:")
    assert len(outcome.extraction.items) == 2
    # Untrusted document text is passed inside delimiters, with the safety rules in the system prompt.
    assert "<document>" in fake.calls[0]["user"] and "untrusted" in fake.calls[0]["system"]


def test_llm_retries_once_after_invalid_output(monkeypatch):
    fake = FakeLLM(["{not json", _llm_answer()])
    monkeypatch.setattr(ies, "get_llm_client", lambda: fake)
    outcome = ies.extract_invoice(INVOICE_TEXT, use_llm=True)
    assert len(fake.calls) == 2 and "rejected by validation" in fake.calls[1]["user"]
    assert outcome.method.startswith("llm:")


def test_llm_invented_values_are_discarded(monkeypatch):
    invented = _llm_answer(
        invoice_number="INV-9999",
        total=5000.0,
        items=[
            {
                "product_name": "Toned Milk 500ml",
                "quantity": 20,
                "unit": "packet",
                "unit_price": 30.0,
                "line_total": 600.0,
            },
            {"product_name": "Paneer 200g", "quantity": 5, "unit": "pcs", "unit_price": 90.0, "line_total": 450.0},
            {
                "product_name": "Saffron Imported",
                "quantity": 1,
                "unit": "box",
                "unit_price": 999.0,
                "line_total": 999.0,
            },
        ],
    )
    monkeypatch.setattr(ies, "get_llm_client", lambda: FakeLLM([invented]))
    outcome = ies.extract_invoice(INVOICE_TEXT, use_llm=True)
    data = outcome.extraction
    assert [i.product_name for i in data.items] == ["Toned Milk 500ml", "Paneer 200g"]
    # The invented invoice number was dropped and replaced by the value actually in the text.
    assert data.invoice_number == "GLD-2026-0457"
    assert data.total == Decimal("1102.50")
    assert any("Discarded invoice number 'INV-9999'" in w for w in outcome.warnings)


def test_llm_unavailable_falls_back_to_rules(monkeypatch):
    from app.ai.llm_client import LLMUnavailableError

    class Down:
        def chat_json(self, **_):
            raise LLMUnavailableError("Ollama is not reachable")

    monkeypatch.setattr(ies, "get_llm_client", lambda: Down())
    outcome = ies.extract_invoice(INVOICE_TEXT, use_llm=True)
    assert outcome.method == "rules" and len(outcome.extraction.items) == 2
    assert "not reachable" in outcome.warnings[0]


def test_arithmetically_inconsistent_llm_result_is_not_preferred(monkeypatch):
    # Small models sometimes read the package size ("500ml") as the quantity.
    confused = _llm_answer(
        items=[
            {"product_name": "Toned Milk", "quantity": 500, "unit": "ml", "unit_price": 30.0, "line_total": 600.0},
            {"product_name": "Paneer", "quantity": 200, "unit": "g", "unit_price": 90.0, "line_total": 450.0},
        ]
    )
    monkeypatch.setattr(ies, "get_llm_client", lambda: FakeLLM([confused]))
    outcome = ies.extract_invoice(INVOICE_TEXT, use_llm=True)
    assert outcome.method == "rules"
    assert outcome.extraction.items[0].quantity == Decimal(20)
    # The AI worked; the better rule result needs no "please check" note, only an audit record.
    assert not any("rule-based" in w for w in outcome.warnings) and outcome.llm_error


def test_numbers_in_space_separated_columns_are_grounded():
    from app.ai.output_validation import number_grounded, numbers_in_text

    # Columns separated only by spaces used to merge into one number, so correct LLM values looked invented.
    numbers = numbers_in_text("BR-001  Sourdough Loaf  10  45.00  450.00\nSubtotal  1,665.00\nDate: October 26, 2026")
    for value in ("10", "45", "450", "1665", "26", "2026"):
        assert number_grounded(Decimal(value), numbers), value
    assert not number_grounded(Decimal("46"), numbers)


def test_units_are_normalised_to_picker_spellings():
    from app.services.units import canonical_unit

    assert [canonical_unit(u) for u in ("Ltr", "litres", "L", "GMS", "Kgs", "ML", "Nos.", "pkt")] == [
        "ltr", "ltr", "ltr", "g", "kg", "ml", "pcs", "packet",
    ]  # fmt: skip
    assert canonical_unit("tub") == "tub" and canonical_unit("  ") is None and canonical_unit(None) is None
