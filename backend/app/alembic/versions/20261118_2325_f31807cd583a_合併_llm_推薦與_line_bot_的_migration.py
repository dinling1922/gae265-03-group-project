"""合併 LLM 推薦與 LINE bot 的 migration

Revision ID: f31807cd583a
Revises: 0b8a8018cfae, c41d7e0a9b52
Create Date: 2026-11-18 23:25:27.287328

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f31807cd583a"
down_revision: str | Sequence[str] | None = ("0b8a8018cfae", "c41d7e0a9b52")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
