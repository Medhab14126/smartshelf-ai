import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk
from app.models.enums import ScanStatus, check_in


class ProductScan(UUIDPk, Timestamps, Base):
    __tablename__ = "product_scans"
    __table_args__ = (
        CheckConstraint(check_in("status", ScanStatus), name="status"),
        Index("ix_scans_business_created", "business_id", "created_at"),
        Index("ix_scans_status", "status"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), default=ScanStatus.UPLOADED)

    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(Integer)
    file_sha256: Mapped[str] = mapped_column(String(64))

    # Optional association chosen at upload time.
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_batches.id", ondelete="SET NULL"))
    invoice_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_items.id", ondelete="SET NULL"))
    quantity_hint: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))

    ocr_engine: Mapped[str | None] = mapped_column(String(40))
    raw_text: Mapped[str | None] = mapped_column(Text)
    barcode: Mapped[str | None] = mapped_column(String(64))
    barcode_format: Mapped[str | None] = mapped_column(String(30))
    # Full label-parser result: fields, date candidates and their statuses.
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Values the user explicitly confirmed (kept alongside the extraction).
    confirmed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
