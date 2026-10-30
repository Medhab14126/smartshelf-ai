import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import ExpiryType
from app.schemas.common import ORMModel
from app.schemas.inventory import PackSizeFields, ProductCreate


class ScanOut(ORMModel):
    id: uuid.UUID
    status: str
    original_filename: str
    content_type: str
    product_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    invoice_item_id: uuid.UUID | None
    quantity_hint: Decimal | None
    ocr_engine: str | None
    raw_text: str | None
    barcode: str | None
    barcode_format: str | None
    extraction: dict | None
    warnings: list[str]
    error_message: str | None
    attempts: int
    confirmed: dict | None
    confirmed_at: datetime | None
    created_at: datetime
    processed_at: datetime | None
    matched_product_id: uuid.UUID | None = None
    matched_product_name: str | None = None


class ScanDeleted(BaseModel):
    # Batches whose remaining stock was removed because this scan created them.
    batches_cleared: int


class ScanConfirm(PackSizeFields):
    """Values the user reviewed. Expiry is only trusted when confirm_expiry=True."""

    # Target: update an existing batch, or create a new batch.
    batch_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    new_product: ProductCreate | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str = Field(default="pcs", min_length=1, max_length=30)
    supplier_name: str | None = Field(default=None, max_length=200)
    batch_number: str | None = Field(default=None, max_length=80)
    manufacturing_date: date | None = None
    packing_date: date | None = None
    expiry_date: date | None = None
    expiry_type: ExpiryType = ExpiryType.EXPIRY
    confirm_expiry: bool = False
    invoice_item_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _check(self) -> "ScanConfirm":
        if not self.batch_id:
            if bool(self.product_id) == bool(self.new_product):
                raise ValueError("choose an existing product or describe a new one")
            if self.quantity is None:
                raise ValueError("quantity is required to create a new batch")
        if self.confirm_expiry and not self.expiry_date:
            raise ValueError("enter the expiry date you are confirming")
        if self.manufacturing_date and self.expiry_date and self.expiry_date < self.manufacturing_date:
            raise ValueError("expiry date cannot be before the manufacturing date")
        return self
