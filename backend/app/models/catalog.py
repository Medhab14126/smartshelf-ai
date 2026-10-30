import uuid

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Supplier(UUIDPk, Timestamps, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("business_id", "normalized_name"),)

    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))


class Product(UUIDPk, Timestamps, Base):
    __tablename__ = "products"
    __table_args__ = (
        # One catalogue entry per (name, brand) within a business.
        UniqueConstraint("business_id", "match_key"),
        Index("ix_products_business_barcode", "business_id", "barcode"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(120))
    match_key: Mapped[str] = mapped_column(String(330))
    category: Mapped[str | None] = mapped_column(String(80))
    default_unit: Mapped[str | None] = mapped_column(String(30))
    package_size: Mapped[str | None] = mapped_column(String(40))
    barcode: Mapped[str | None] = mapped_column(String(64))
    product_code: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
