"""合併提醒信與 main 的 migration

Revision ID: 2c2657fae791
Revises: 802dc39ae4a1, 43229ff118b4
Create Date: 2026-11-16 23:02:14.238318

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c2657fae791"
down_revision: str | Sequence[str] | None = ("802dc39ae4a1", "43229ff118b4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
