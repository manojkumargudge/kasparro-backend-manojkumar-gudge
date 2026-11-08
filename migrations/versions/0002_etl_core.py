"""ETL core: etl_runs, api_rate_limits, idempotent observation keys, coin identity.

Revision ID: 0002_etl_core
Revises: 0001_initial
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_etl_core"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- coins: merge duplicates (same symbol + case-insensitive name), then enforce identity
    op.execute(
        """
        CREATE TEMP TABLE coin_dupes ON COMMIT DROP AS
        SELECT id, min(id) OVER (PARTITION BY upper(symbol), lower(name)) AS keep FROM coins
        """
    )
    op.execute(
        "UPDATE coin_source_mapping m SET coin_id = d.keep FROM coin_dupes d WHERE m.coin_id = d.id AND d.id <> d.keep"
    )
    op.execute(
        "UPDATE price_snapshots p SET coin_id = d.keep FROM coin_dupes d WHERE p.coin_id = d.id AND d.id <> d.keep"
    )
    op.execute("DELETE FROM coins c USING coin_dupes d WHERE c.id = d.id AND d.id <> d.keep")
    op.execute("UPDATE coins SET symbol = upper(symbol) WHERE symbol <> upper(symbol)")
    op.add_column("coins", sa.Column("price_observed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("uq_coins_symbol_lower_name", "coins", ["symbol", sa.text("lower(name)")], unique=True)

    # --- coin_source_mapping: one row per (source, source_coin_id)
    op.execute(
        """
        DELETE FROM coin_source_mapping a USING coin_source_mapping b
        WHERE a.source = b.source AND a.source_coin_id = b.source_coin_id AND a.id > b.id
        """
    )
    op.create_unique_constraint("uq_coin_source_mapping_source_id", "coin_source_mapping", ["source", "source_coin_id"])

    # --- price_snapshots become idempotent observations keyed by (source, coin_id, observed_at)
    op.add_column("price_snapshots", sa.Column("source", sa.String(), nullable=True))
    op.add_column("price_snapshots", sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE price_snapshots SET source = 'legacy', observed_at = coalesce(captured_at, now())")
    op.execute(
        """
        DELETE FROM price_snapshots a USING price_snapshots b
        WHERE a.source = b.source AND a.coin_id IS NOT DISTINCT FROM b.coin_id
          AND a.observed_at = b.observed_at AND a.id > b.id
        """
    )
    op.alter_column("price_snapshots", "source", nullable=False)
    op.alter_column("price_snapshots", "observed_at", nullable=False)
    op.create_unique_constraint(
        "uq_price_snapshots_source_coin_observed", "price_snapshots", ["source", "coin_id", "observed_at"]
    )

    # --- etl_runs
    op.create_table(
        "etl_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_group", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("records_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_invalid", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("drift", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_etl_runs_run_group"), "etl_runs", ["run_group"], unique=False)
    op.create_index("ix_etl_runs_source_started_at", "etl_runs", ["source", "started_at"], unique=False)

    # --- api_rate_limits
    op.create_table(
        "api_rate_limits",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key_hash", "window_start"),
    )


def downgrade() -> None:
    op.drop_table("api_rate_limits")
    op.drop_index("ix_etl_runs_source_started_at", table_name="etl_runs")
    op.drop_index(op.f("ix_etl_runs_run_group"), table_name="etl_runs")
    op.drop_table("etl_runs")
    op.drop_constraint("uq_price_snapshots_source_coin_observed", "price_snapshots", type_="unique")
    op.drop_column("price_snapshots", "observed_at")
    op.drop_column("price_snapshots", "source")
    op.drop_constraint("uq_coin_source_mapping_source_id", "coin_source_mapping", type_="unique")
    op.drop_index("uq_coins_symbol_lower_name", table_name="coins")
    op.drop_column("coins", "price_observed_at")
