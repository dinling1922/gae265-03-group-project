"""新增 line_binding、line_bind_code、line_bind_failure

Revision ID: c41d7e0a9b52
Revises: ab25f730e136
Create Date: 2026-11-18 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c41d7e0a9b52"
down_revision: str | Sequence[str] | None = "ab25f730e136"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "line_binding",
        *_base_columns(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("line_user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("api_token_id", sa.Uuid(), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["api_token_id"],
            ["api_token.id"],
            name=op.f("fk_line_binding_api_token_id_api_token"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_line_binding_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_line_binding")),
        sa.UniqueConstraint("line_user_id", name=op.f("uq_line_binding_line_user_id")),
    )
    op.create_index(
        op.f("ix_line_binding_api_token_id"), "line_binding", ["api_token_id"]
    )
    op.create_index(
        op.f("ix_line_binding_user_id"), "line_binding", ["user_id"], unique=True
    )

    op.create_table(
        "line_bind_code",
        *_base_columns(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_line_bind_code_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_line_bind_code")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_line_bind_code_code_hash")),
    )
    op.create_index(
        op.f("ix_line_bind_code_user_id"), "line_bind_code", ["user_id"], unique=True
    )

    op.create_table(
        "line_bind_failure",
        *_base_columns(),
        sa.Column("line_user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_line_bind_failure")),
    )
    op.create_index(
        op.f("ix_line_bind_failure_line_user_id"),
        "line_bind_failure",
        ["line_user_id"],
    )
    op.create_index(
        "ix_line_bind_failure_created_at", "line_bind_failure", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_line_bind_failure_created_at", table_name="line_bind_failure")
    op.drop_index(
        op.f("ix_line_bind_failure_line_user_id"), table_name="line_bind_failure"
    )
    op.drop_table("line_bind_failure")
    op.drop_index(op.f("ix_line_bind_code_user_id"), table_name="line_bind_code")
    op.drop_table("line_bind_code")
    op.drop_index(op.f("ix_line_binding_user_id"), table_name="line_binding")
    op.drop_index(op.f("ix_line_binding_api_token_id"), table_name="line_binding")
    op.drop_table("line_binding")
