"""Remove unit warnings from stored invoice lines

Revision ID: 5b7c1d2e9f40
Revises: e94f68a9a61d
Create Date: 2026-10-30 10:00:00

Missing units are no longer warned about (counts are received as pcs). Older lines still carry the
notes; drop them so existing invoices look like new ones. `warnings` is display data, not an audit
record (the extracted values live in `extracted`), so this is not reversible and does not need to be.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b7c1d2e9f40"
down_revision: str | None = "e94f68a9a61d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE invoice_items
        SET warnings = COALESCE(
            (
                SELECT jsonb_agg(w)
                FROM jsonb_array_elements_text(warnings) AS w
                WHERE w <> 'unit of measure not detected'
                  AND w NOT LIKE 'no unit on the invoice;%'
                  AND w NOT LIKE 'not added automatically: unit missing%'
            ),
            '[]'::jsonb
        )
        WHERE warnings::text LIKE '%unit%'
        """
    )


def downgrade() -> None:
    pass
