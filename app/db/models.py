from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, utcnow


class CheckStatus(StrEnum):
    ALIVE = "alive"
    NO_PEERS = "no_peers"
    NOT_FOUND = "not_found"
    ERROR = "error"


class Resolution(StrEnum):
    """Video resolution class of a channel, detected from its screenshots or set by hand."""

    SD = "480p"
    HD = "720p"
    FULL_HD = "1080p"
    UHD = "4k"
    OTHER = "other"


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

    # "all": leave the FK alone on delete so the database RESTRICT rule applies.
    channels: Mapped[list["Channel"]] = relationship(
        back_populates="category", passive_deletes="all"
    )


class Channel(Base):
    __tablename__ = "channel"
    __table_args__ = (
        # Content ID is stored normalized: 40 lowercase hex characters.
        CheckConstraint(
            "length(content_id) = 40 AND content_id NOT GLOB '*[^0-9a-f]*'",
            name="content_id_format",
        ),
        Index("ix_channel_title", "title"),
        Index("ix_channel_language", "language"),
        Index("ix_channel_country", "country"),
        Index("ix_channel_created_at", "created_at"),
        Index("ix_channel_resolution", "resolution"),
        CheckConstraint(
            "resolution IN (" + ", ".join(f"'{r.value}'" for r in Resolution) + ")",
            name="resolution_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    # The user gave no title: this one is provisional until the stream's name is known.
    title_pending: Mapped[bool] = mapped_column(default=False, server_default=false())
    content_id: Mapped[str] = mapped_column(String(40), unique=True)
    # RESTRICT: a category in use cannot be deleted without reassigning its channels.
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id", ondelete="RESTRICT"), index=True
    )
    # SET NULL, never CASCADE: a feed that drops a guide channel must degrade this catalogue
    # channel to "sin guía", not delete the channel itself. A channel outlives any guide.
    guide_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("epg_channel.id", ondelete="SET NULL"), index=True
    )
    language: Mapped[str | None] = mapped_column(String(2))  # ISO 639-1, lowercase
    country: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-1 alpha-2, uppercase
    resolution: Mapped[Resolution | None] = mapped_column(
        Enum(
            Resolution,
            native_enum=False,
            create_constraint=False,
            length=8,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    category: Mapped[Category | None] = relationship(back_populates="channels")
    guide_channel: Mapped["EpgChannel | None"] = relationship()
    checks: Mapped[list["Check"]] = relationship(
        back_populates="channel",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Check.checked_at.desc()",
    )


class Check(Base):
    __tablename__ = "check_result"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s.value}'" for s in CheckStatus) + ")",
            name="status_valid",
        ),
        Index("ix_check_result_channel_id_checked_at", "channel_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[CheckStatus] = mapped_column(
        Enum(
            CheckStatus,
            native_enum=False,
            create_constraint=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    peers: Mapped[int | None]
    speed_down: Mapped[int | None]
    # The engine resolves a Content ID to a different infohash, so it is kept separately.
    infohash: Mapped[str | None] = mapped_column(String(40))
    screenshot_path: Mapped[str | None] = mapped_column(String(500))
    # Frame size of the screenshot, when one was taken.
    width: Mapped[int | None]
    height: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(String(500))

    channel: Mapped[Channel] = relationship(back_populates="checks")


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(1000))


class EpgChannel(Base):
    """One channel as one guide names it. Never one row per catalogue channel.

    Two providers use the same id for different channels, and the same channel differently, so a
    guide channel is only ever identified by its source *and* the id the source gave it. Which
    catalogue channel points at which of these — and the aliases that decision needs — is the
    mapping layer's table, not this one.
    """

    __tablename__ = "epg_channel"
    __table_args__ = (
        # The name spells out both columns: the repo's naming convention would take only the first
        # one, `uq_epg_channel_source`, which reads like `source` alone is unique.
        UniqueConstraint(
            "source", "source_channel_id", name="uq_epg_channel_source_source_channel_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The feed URL this channel was read from. A source is a cache that gets replaced, and this is
    # what says which cache a row came from and which one dropped it.
    source: Mapped[str] = mapped_column(String(500))
    source_channel_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    icon: Mapped[str | None] = mapped_column(String(500))
    # The last refresh that found this channel in its source. An older stamp is how a channel the
    # source stopped offering is told apart from one whose source did not answer at all.
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    programmes: Mapped[list["Programme"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan", passive_deletes=True
    )


class Programme(Base):
    """One listing of one guide channel, both bounds in UTC."""

    __tablename__ = "programme"
    __table_args__ = (
        # A refresh is a re-read of mostly the same listings, so the second read of a programme is
        # an update of this row and not a second copy of it. Spelled out for the same reason as the
        # unique above: `uq_programme_epg_channel_id` alone would claim a channel has one programme.
        UniqueConstraint("epg_channel_id", "start", name="uq_programme_epg_channel_id_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # No index of its own: the unique (epg_channel_id, start) above already answers everything that
    # looks a programme up by its channel, foreign-key cascade included.
    epg_channel_id: Mapped[int] = mapped_column(ForeignKey("epg_channel.id", ondelete="CASCADE"))
    start: Mapped[datetime] = mapped_column(UTCDateTime)
    stop: Mapped[datetime] = mapped_column(UTCDateTime)
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str | None] = mapped_column(String(2000))

    channel: Mapped[EpgChannel] = relationship(back_populates="programmes")


class EpgAlias(Base):
    """A person's decision: this catalogue channel belongs to that guide channel.

    One row per channel: a second decision on the same channel replaces the first. The matcher
    reads this table first and `--auto` never writes here, so a manual decision always wins. The
    target is nullable because the guide channel it named can disappear; the row stays so the
    channel remains human-owned instead of falling back to the matcher.
    """

    __tablename__ = "epg_alias"
    __table_args__ = (
        # One decision per catalogue channel, spelled out: `uq_epg_alias_channel_id` says which
        # channel the decision is about, not that the row has one of anything else.
        UniqueConstraint("channel_id", name="uq_epg_alias_channel_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    epg_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("epg_channel.id", ondelete="SET NULL")
    )
    # Who decided: the OS user of the CLI run, or `ACESTREAM_SUITE_EPG_ACTOR` when the host sets one.
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
