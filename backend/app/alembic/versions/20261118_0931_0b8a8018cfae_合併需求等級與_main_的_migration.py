"""合併需求等級與 main 的 migration

Revision ID: 0b8a8018cfae
Revises: 2c2657fae791, 3c1e9b7d5a20
Create Date: 2026-11-18 09:31:12.707842

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0b8a8018cfae"
down_revision: str | Sequence[str] | None = ("2c2657fae791", "3c1e9b7d5a20")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
