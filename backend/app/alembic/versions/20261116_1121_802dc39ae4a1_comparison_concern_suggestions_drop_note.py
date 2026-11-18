"""comparison 加推薦的在意事項（concern_suggestions），移除沒人讀的 note（spec #427）

Revision ID: 802dc39ae4a1
Revises: 1fa3ff204fc2
Create Date: 2026-11-16 11:21:33.172948

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "802dc39ae4a1"
down_revision: str | Sequence[str] | None = "1fa3ff204fc2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comparison", sa.Column("concern_suggestions", sa.JSON(), nullable=True)
    )
    op.drop_column("comparison", "note")


def downgrade() -> None:
    op.add_column(
        "comparison",
        sa.Column(
            "note",
            sa.VARCHAR(),
            server_default=sa.text("''::character varying"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.drop_column("comparison", "concern_suggestions")
