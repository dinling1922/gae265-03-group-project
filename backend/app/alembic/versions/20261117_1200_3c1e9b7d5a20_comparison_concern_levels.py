"""comparison 加需求等級（concern_levels）與推薦來源（recommendation_source），清空舊規則的推薦（spec #457）

舊推薦是 ADR-0021 的規則選的，和新的推薦理由對不上：清空後下次 1-2 存檔時依新規則補算。

Revision ID: 3c1e9b7d5a20
Revises: 802dc39ae4a1
Create Date: 2026-11-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c1e9b7d5a20"
down_revision: str | Sequence[str] | None = "802dc39ae4a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("comparison", sa.Column("concern_levels", sa.JSON(), nullable=True))
    op.add_column(
        "comparison",
        sa.Column(
            "recommendation_source",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE comparison SET recommended_plan_code = NULL, recommended_sum_insured = NULL,"
        " alternative_plan_code = NULL, alternative_sum_insured = NULL"
    )


def downgrade() -> None:
    op.drop_column("comparison", "recommendation_source")
    op.drop_column("comparison", "concern_levels")
