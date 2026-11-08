"""Initial schema: coins, coin_source_mapping, raw_coins, checkpoints, price_snapshots.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("canonical_id", sa.String(), nullable=True),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("price_usd", sa.Float(), nullable=True),
        sa.Column("market_cap", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_coins_canonical_id"), "coins", ["canonical_id"], unique=True)
    op.create_index(op.f("ix_coins_id"), "coins", ["id"], unique=False)
    op.create_index(op.f("ix_coins_symbol"), "coins", ["symbol"], unique=False)

    op.create_table(
        "checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("last_run", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_value", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_checkpoints_id"), "checkpoints", ["id"], unique=False)
    op.create_index(op.f("ix_checkpoints_source"), "checkpoints", ["source"], unique=True)

    op.create_table(
        "raw_coins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("row_hash", sa.String(), nullable=True),
        sa.Column("raw", sa.Text(), nullable=True),
        sa.Column("processed", sa.Boolean(), server_default="false", nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_raw_coins_id"), "raw_coins", ["id"], unique=False)
    op.create_index(op.f("ix_raw_coins_row_hash"), "raw_coins", ["row_hash"], unique=True)
    op.create_index(op.f("ix_raw_coins_source"), "raw_coins", ["source"], unique=False)

    op.create_table(
        "coin_source_mapping",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("coin_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("source_coin_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["coin_id"], ["coins.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_coin_source_mapping_id"), "coin_source_mapping", ["id"], unique=False)
    op.create_index(op.f("ix_coin_source_mapping_source"), "coin_source_mapping", ["source"], unique=False)
    op.create_index(
        op.f("ix_coin_source_mapping_source_coin_id"), "coin_source_mapping", ["source_coin_id"], unique=False
    )

    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("coin_id", sa.Integer(), nullable=True),
        sa.Column("price_usd", sa.Float(), nullable=True),
        sa.Column("market_cap", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["coin_id"], ["coins.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_price_snapshots_captured_at"), "price_snapshots", ["captured_at"], unique=False)
    op.create_index(op.f("ix_price_snapshots_coin_id"), "price_snapshots", ["coin_id"], unique=False)
    op.create_index(op.f("ix_price_snapshots_id"), "price_snapshots", ["id"], unique=False)


def downgrade() -> None:
    op.drop_table("price_snapshots")
    op.drop_table("coin_source_mapping")
    op.drop_table("raw_coins")
    op.drop_table("checkpoints")
    op.drop_table("coins")
