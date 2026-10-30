"""Schemas for AI/OCR extraction output.

Every LLM response is validated against these models. Values are optional:
a missing value is always preferred over an invented one.
"""

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if isinstance(value, str):
        from app.services.number_parsing import parse_amount

        parsed = parse_amount(value)
        if parsed is None:
            raise ValueError(f"not a number: {value!r}")
        return parsed
    raise ValueError("not a number")


class ExtractedInvoiceItem(BaseModel):
    product_name: str = Field(min_length=1, max_length=300)
    product_code: str | None = Field(default=None, max_length=64)
    quantity: Decimal | None = None
    unit: str | None = Field(default=None, max_length=30)
    unit_price: Decimal | None = None
    line_total: Decimal | None = None

    _num = field_validator("quantity", "unit_price", "line_total", mode="before")(lambda cls, v: _to_decimal(v))

    @field_validator("product_name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("blank product name")
        return value

    @field_validator("quantity", "unit_price", "line_total")
    @classmethod
    def _non_negative(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("must not be negative")
        return value


class InvoiceExtraction(BaseModel):
    supplier_name: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=100)
    # Kept as text: the deterministic date parser interprets it afterwards.
    invoice_date: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=3)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    items: list[ExtractedInvoiceItem] = Field(default_factory=list, max_length=300)

    _num = field_validator("subtotal", "tax", "total", mode="before")(lambda cls, v: _to_decimal(v))

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().upper()
            symbols = {"₹": "INR", "RS": "INR", "RS.": "INR", "$": "USD", "€": "EUR", "£": "GBP"}
            value = symbols.get(value, value)
            if len(value) != 3 or not value.isalpha():
                return None
        return value


class LabelLLMHints(BaseModel):
    """What the language/vision model may contribute for packaging.

    Dates are deliberately NOT accepted here: they are always extracted by
    the deterministic parser from text. The model may only transcribe text.
    """

    product_name: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    package_size: str | None = Field(default=None, max_length=40)
    visible_date_text: list[str] = Field(default_factory=list, max_length=10)
