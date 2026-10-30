"""Pack sizes on batches and invoice lines

Revision ID: 8d2a4f6b1c37
Revises: 5b7c1d2e9f40
Create Date: 2026-10-30 12:00:00

Quantities count packs; pack_size/pack_unit say what one pack holds (500 ml). Existing invoice lines
get the size printed in their product name, and batches received from those lines inherit it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.units import pack_from_line

revision: str = "8d2a4f6b1c37"
down_revision: str | None = "5b7c1d2e9f40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("inventory_batches", "invoice_items")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("pack_size", sa.Numeric(12, 3), nullable=True))
        op.add_column(table, sa.Column("pack_unit", sa.String(10), nullable=True))
        op.create_check_constraint(op.f(f"ck_{table}_pack_unit"), table, "pack_unit IN ('g', 'kg', 'ml', 'ltr')")
        op.create_check_constraint(op.f(f"ck_{table}_pack_size_positive"), table, "pack_size > 0")
        op.create_check_constraint(
            op.f(f"ck_{table}_pack_size_has_unit"), table, "(pack_size IS NULL) = (pack_unit IS NULL)"
        )

    # Backfill from text already stored (deterministic, nothing guessed).
    conn = op.get_bind()
    for item_id, name, unit in conn.execute(sa.text("SELECT id, product_name, unit FROM invoice_items")).all():
        pack = pack_from_line(name, unit)
        if pack:
            conn.execute(
                sa.text("UPDATE invoice_items SET pack_size = :size, pack_unit = :unit WHERE id = :id"),
                {"size": pack[0], "unit": pack[1], "id": item_id},
            )
    conn.execute(
        sa.text(
            "UPDATE inventory_batches b SET pack_size = i.pack_size, pack_unit = i.pack_unit "
            "FROM invoice_items i WHERE b.invoice_item_id = i.id AND i.pack_size IS NOT NULL"
        )
    )


def downgrade() -> None:
    for table in TABLES:
        for name in ("pack_size_has_unit", "pack_size_positive", "pack_unit"):
            op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
        op.drop_column(table, "pack_unit")
        op.drop_column(table, "pack_size")
