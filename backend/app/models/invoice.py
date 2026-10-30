import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk
from app.models.enums import InvoiceItemStatus, InvoiceStatus, PackUnit, check_in


class Invoice(UUIDPk, Timestamps, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        # Identical file uploaded twice to the same business is rejected.
        UniqueConstraint("business_id", "file_sha256"),
        CheckConstraint(check_in("status", InvoiceStatus), name="status"),
        Index("ix_invoices_business_created", "business_id", "created_at"),
        Index("ix_invoices_status", "status"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.UPLOADED)

    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(Integer)
    file_sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)

    # Processing / audit trail of the AI pipeline.
    ocr_engine: Mapped[str | None] = mapped_column(String(40))
    extraction_method: Mapped[str | None] = mapped_column(String(40))
    raw_text: Mapped[str | None] = mapped_column(Text)
    extraction_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    confidence: Mapped[float | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Header fields (extracted, then editable during review).
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice", order_by="InvoiceItem.line_number", cascade="all, delete-orphan"
    )


class InvoiceItem(UUIDPk, Timestamps, Base):
    __tablename__ = "invoice_items"
    __table_args__ = (
        CheckConstraint(check_in("status", InvoiceItemStatus), name="status"),
        CheckConstraint("quantity_added >= 0", name="quantity_added_non_negative"),
        CheckConstraint(check_in("pack_unit", PackUnit), name="pack_unit"),
        CheckConstraint("pack_size > 0", name="pack_size_positive"),
        CheckConstraint("(pack_size IS NULL) = (pack_unit IS NULL)", name="pack_size_has_unit"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    product_name: Mapped[str] = mapped_column(String(300))
    product_code: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    unit: Mapped[str | None] = mapped_column(String(30))
    # Size of each pack, read from the product name ("Milk 500ml") or entered in review.
    pack_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    pack_unit: Mapped[str | None] = mapped_column(String(10))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    confidence: Mapped[float | None]
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # Values exactly as extracted, kept for auditing after the user edits.
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    edited: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default=InvoiceItemStatus.PENDING)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    quantity_added: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)

    invoice: Mapped[Invoice] = relationship(back_populates="items")
