"""合併日程活動與 incident 現況確認

Revision ID: 1fa3ff204fc2
Revises: f6d82cc90f4a, e028b3b6a6ec
Create Date: 2026-11-16 09:51:13.349104

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1fa3ff204fc2"
down_revision: str | Sequence[str] | None = ("f6d82cc90f4a", "e028b3b6a6ec")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
