"""channel guide link and manual aliases

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13 23:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SET NULL, never CASCADE: a feed that drops a guide channel must leave the catalogue channel
    # alive and unmapped ("sin guía"), because a channel is not worth deleting over a listing.
    with op.batch_alter_table("channel") as batch:
        batch.add_column(sa.Column("guide_channel_id", sa.Integer(), nullable=True))
        batch.create_index(
            batch.f("ix_channel_guide_channel_id"), ["guide_channel_id"], unique=False
        )
        batch.create_foreign_key(
            op.f("fk_channel_guide_channel_id_epg_channel"),
            "epg_channel",
            ["guide_channel_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_table(
        "epg_alias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        # Nullable: the guide channel a person chose can vanish, and the decision row stays so the
        # channel is still human-owned instead of falling back to the matcher.
        sa.Column("epg_channel_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channel.id"],
            name=op.f("fk_epg_alias_channel_id_channel"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["epg_channel_id"],
            ["epg_channel.id"],
            name=op.f("fk_epg_alias_epg_channel_id_epg_channel"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_epg_alias")),
        # One decision per catalogue channel: a second one replaces the first.
        sa.UniqueConstraint("channel_id", name=op.f("uq_epg_alias_channel_id")),
    )


def downgrade() -> None:
    op.drop_table("epg_alias")
    with op.batch_alter_table("channel") as batch:
        batch.drop_constraint(op.f("fk_channel_guide_channel_id_epg_channel"), type_="foreignkey")
        batch.drop_index(batch.f("ix_channel_guide_channel_id"))
        batch.drop_column("guide_channel_id")
