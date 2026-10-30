import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import ExpiryType
from app.schemas.common import ORMModel
from app.schemas.inventory import PackSizeFields, ProductCreate


class InvoiceItemOut(ORMModel):
    id: uuid.UUID
    line_number: int
    product_name: str
    product_code: str | None
    quantity: Decimal | None
    unit: str | None
    pack_size: Decimal | None = None
    pack_unit: str | None = None
    unit_price: Decimal | None
    line_total: Decimal | None
    confidence: float | None
    warnings: list[str]
    extracted: dict | None
    edited: bool
    status: str
    product_id: uuid.UUID | None
    quantity_added: Decimal
    suggested_product_id: uuid.UUID | None = None
    suggested_product_name: str | None = None


class InvoiceSummary(ORMModel):
    id: uuid.UUID
    status: str
    original_filename: str
    supplier_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    currency: str | None
    total: Decimal | None
    created_at: datetime
    item_count: int = 0
    error_message: str | None


class InvoiceOut(InvoiceSummary):
    content_type: str
    file_size: int
    page_count: int | None
    ocr_engine: str | None
    extraction_method: str | None
    raw_text: str | None
    warnings: list[str]
    confidence: float | None
    attempts: int
    subtotal: Decimal | None
    tax: Decimal | None
    processed_at: datetime | None
    confirmed_at: datetime | None
    items: list[InvoiceItemOut] = []


class InvoiceUploadResponse(BaseModel):
    invoice: InvoiceSummary
    duplicate: bool = False


class InvoiceItemUpdate(PackSizeFields):
    product_name: str | None = Field(default=None, min_length=1, max_length=300)
    product_code: str | None = Field(default=None, max_length=64)
    quantity: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=30)
    unit_price: Decimal | None = Field(default=None, ge=0)
    line_total: Decimal | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, pattern="^(pending|accepted|rejected)$")


class InvoiceItemCreate(PackSizeFields):
    product_name: str = Field(min_length=1, max_length=300)
    product_code: str | None = Field(default=None, max_length=64)
    quantity: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=30)
    unit_price: Decimal | None = Field(default=None, ge=0)
    line_total: Decimal | None = Field(default=None, ge=0)


class InvoiceHeaderUpdate(BaseModel):
    supplier_name: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax: Decimal | None = Field(default=None, ge=0)
    total: Decimal | None = Field(default=None, ge=0)


class InvoiceConfirm(InvoiceHeaderUpdate):
    # Lines not listed keep their current status; pending lines become accepted.
    rejected_item_ids: list[uuid.UUID] = Field(default_factory=list)
    allow_duplicate: bool = False
    # Confirming means the delivery was received: accepted lines become batches straight away.
    add_to_inventory: bool = True


class InvoiceDeleted(BaseModel):
    # Batches whose remaining stock was removed because they came from the deleted invoice.
    batches_cleared: int


class BatchSplit(BaseModel):
    quantity: Decimal = Field(gt=0)
    batch_number: str | None = Field(default=None, max_length=80)
    manufacturing_date: date | None = None
    expiry_date: date | None = None
    expiry_type: ExpiryType = ExpiryType.EXPIRY
    expiry_confirmed: bool = True

    @model_validator(mode="after")
    def _dates(self) -> "BatchSplit":
        if self.manufacturing_date and self.expiry_date and self.expiry_date < self.manufacturing_date:
            raise ValueError("expiry date cannot be before the manufacturing date")
        return self


class AddToInventoryRequest(PackSizeFields):
    product_id: uuid.UUID | None = None
    new_product: ProductCreate | None = None
    unit: str = Field(default="pcs", min_length=1, max_length=30)
    received_date: date | None = None
    # One invoice line may become several batches with different expiry dates.
    batches: list[BatchSplit] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _product(self) -> "AddToInventoryRequest":
        if bool(self.product_id) == bool(self.new_product):
            raise ValueError("provide exactly one of product_id or new_product")
        return self
