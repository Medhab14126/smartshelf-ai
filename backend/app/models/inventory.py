import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk
from app.models.catalog import Product, Supplier
from app.models.enums import ExpiryDateStatus, ExpiryType, MovementType, PackUnit, check_in


class InventoryBatch(UUIDPk, Timestamps, Base):
    """A physical lot of one product. Each batch carries its own expiry date."""

    __tablename__ = "inventory_batches"
    __table_args__ = (
        CheckConstraint("current_quantity >= 0", name="current_quantity_non_negative"),
        CheckConstraint("quantity_received >= 0", name="quantity_received_non_negative"),
        CheckConstraint(check_in("expiry_date_status", ExpiryDateStatus), name="expiry_date_status"),
        CheckConstraint(check_in("expiry_type", ExpiryType), name="expiry_type"),
        CheckConstraint(check_in("pack_unit", PackUnit), name="pack_unit"),
        CheckConstraint("pack_size > 0", name="pack_size_positive"),
        CheckConstraint("(pack_size IS NULL) = (pack_unit IS NULL)", name="pack_size_has_unit"),
        # A batch has a date exactly when its status is not "missing".
        CheckConstraint("(expiry_date IS NULL) = (expiry_date_status = 'missing')", name="expiry_date_matches_status"),
        Index("ix_batches_business_expiry", "business_id", "expiry_date"),
        Index("ix_batches_business_product", "business_id", "product_id"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), index=True)
    invoice_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_items.id", ondelete="SET NULL"))

    batch_number: Mapped[str | None] = mapped_column(String(80))
    quantity_received: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    current_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit: Mapped[str] = mapped_column(String(30))
    # Size of each pack ("500 ml"); `current_quantity` counts packs. Both null when not known.
    pack_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    pack_unit: Mapped[str | None] = mapped_column(String(10))
    manufacturing_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    expiry_type: Mapped[str] = mapped_column(String(20), default=ExpiryType.EXPIRY)
    expiry_date_status: Mapped[str] = mapped_column(String(20), default=ExpiryDateStatus.MISSING)
    expiry_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    received_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    product: Mapped[Product] = relationship(lazy="joined")
    supplier: Mapped[Supplier | None] = relationship(lazy="joined")


class InventoryMovement(UUIDPk, Base):
    """Append-only stock ledger; every quantity change is recorded here."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint(check_in("movement_type", MovementType), name="movement_type"),
        CheckConstraint("quantity_after >= 0", name="quantity_after_non_negative"),
        Index("ix_movements_batch_created", "batch_id", "created_at"),
        Index("ix_movements_business_created", "business_id", "created_at"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_batches.id", ondelete="CASCADE"))
    movement_type: Mapped[str] = mapped_column(String(20))
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    quantity_after: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped[InventoryBatch] = relationship()
