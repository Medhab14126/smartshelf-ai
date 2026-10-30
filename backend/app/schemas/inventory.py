import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import ExpiryType, MovementType, PackUnit
from app.schemas.common import ORMModel

Quantity = Decimal


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    default_unit: str | None = Field(default=None, max_length=30)
    package_size: str | None = Field(default=None, max_length=40)
    barcode: str | None = Field(default=None, max_length=64)
    product_code: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("brand", "category", "default_unit", "package_size", "barcode", "product_code", "notes")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return _clean(value)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    default_unit: str | None = Field(default=None, max_length=30)
    package_size: str | None = Field(default=None, max_length=40)
    barcode: str | None = Field(default=None, max_length=64)
    product_code: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)


class ProductOut(ORMModel):
    id: uuid.UUID
    name: str
    brand: str | None
    category: str | None
    default_unit: str | None
    package_size: str | None
    barcode: str | None
    product_code: str | None
    notes: str | None
    created_at: datetime
    active_batches: int = 0
    total_quantity: Decimal = Decimal(0)


class SupplierOut(ORMModel):
    id: uuid.UUID
    name: str


class PackSizeFields(BaseModel):
    """Optional size of one pack; the quantity counts packs (20 × 500 ml)."""

    pack_size: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    pack_unit: PackUnit | None = None

    @model_validator(mode="after")
    def _pack_pair(self):
        if (self.pack_size is None) != (self.pack_unit is None):
            raise ValueError("enter both the pack size and its unit (g, kg, ml or ltr), or neither")
        return self


class BatchCreate(PackSizeFields):
    """Manual batch entry. Either an existing product or a new one."""

    product_id: uuid.UUID | None = None
    new_product: ProductCreate | None = None
    supplier_name: str | None = Field(default=None, max_length=200)
    batch_number: str | None = Field(default=None, max_length=80)
    quantity: Quantity = Field(gt=0, max_digits=14, decimal_places=3)
    unit: str = Field(default="pcs", min_length=1, max_length=30)
    manufacturing_date: date | None = None
    expiry_date: date | None = None
    expiry_type: ExpiryType = ExpiryType.EXPIRY
    # A date typed by a person is considered confirmed unless they say otherwise.
    expiry_confirmed: bool = True
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    received_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self) -> "BatchCreate":
        if bool(self.product_id) == bool(self.new_product):
            raise ValueError("provide exactly one of product_id or new_product")
        if self.manufacturing_date and self.expiry_date and self.expiry_date < self.manufacturing_date:
            raise ValueError("expiry date cannot be before the manufacturing date")
        self.batch_number = _clean(self.batch_number)
        self.supplier_name = _clean(self.supplier_name)
        return self


class BatchUpdate(PackSizeFields):
    batch_number: str | None = Field(default=None, max_length=80)
    unit: str | None = Field(default=None, min_length=1, max_length=30)
    manufacturing_date: date | None = None
    expiry_date: date | None = None
    expiry_type: ExpiryType | None = None
    expiry_confirmed: bool | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0)
    received_date: date | None = None
    supplier_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class StockAdjustment(BaseModel):
    movement_type: MovementType
    # Always positive for consumed/sold/discarded/returned/received.
    quantity: Quantity | None = Field(default=None, gt=0, max_digits=14, decimal_places=3)
    # Only for "correction": the counted quantity actually on the shelf.
    new_quantity: Quantity | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check(self) -> "StockAdjustment":
        if self.movement_type == MovementType.CORRECTION:
            if self.new_quantity is None:
                raise ValueError("new_quantity is required for a correction")
            if not (self.reason and self.reason.strip()):
                raise ValueError("a reason is required for a correction")
        elif self.quantity is None:
            raise ValueError("quantity is required")
        return self


class BatchOut(ORMModel):
    id: uuid.UUID
    product: ProductOut
    supplier: SupplierOut | None
    invoice_id: uuid.UUID | None
    invoice_item_id: uuid.UUID | None
    invoice_number: str | None = None
    batch_number: str | None
    quantity_received: Decimal
    current_quantity: Decimal
    unit: str
    pack_size: Decimal | None = None
    pack_unit: str | None = None
    manufacturing_date: date | None
    expiry_date: date | None
    expiry_type: str
    expiry_date_status: str
    expiry_confirmed_at: datetime | None
    purchase_price: Decimal | None
    received_date: date
    notes: str | None
    created_at: datetime
    updated_at: datetime
    status: str
    days_until_expiry: int | None
    image_scan_id: uuid.UUID | None = None
    created_by_name: str | None = None


class MovementOut(ORMModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    movement_type: str
    quantity_delta: Decimal
    quantity_after: Decimal
    reason: str | None
    created_at: datetime
    created_by_name: str | None = None
    product_name: str | None = None
