"""epg channel and programme

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13 22:40:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "epg_channel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("source_channel_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("icon", sa.String(length=500), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_epg_channel")),
        # A source and its own id together. Providers reuse each other's ids for different
        # channels, so an id by itself identifies nothing.
        sa.UniqueConstraint(
            "source", "source_channel_id", name="uq_epg_channel_source_source_channel_id"
        ),
    )
    op.create_table(
        "programme",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("epg_channel_id", sa.Integer(), nullable=False),
        sa.Column("start", sa.DateTime(), nullable=False),
        sa.Column("stop", sa.DateTime(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["epg_channel_id"],
            ["epg_channel.id"],
            name=op.f("fk_programme_epg_channel_id_epg_channel"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_programme")),
        # What makes refreshing the same listings an update instead of a second copy of every
        # programme: one row per channel and start time. The name is spelled out in full because
        # the convention would take only the first column, and `uq_programme_epg_channel_id` reads
        # as if a channel had a single programme.
        sa.UniqueConstraint("epg_channel_id", "start", name="uq_programme_epg_channel_id_start"),
    )


def downgrade() -> None:
    op.drop_table("programme")
    op.drop_table("epg_channel")
