from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, utcnow


class CheckStatus(StrEnum):
    """How a check of a channel ended."""

    ALIVE = "alive"
    NO_PEERS = "no_peers"
    NOT_FOUND = "not_found"
    ERROR = "error"


class Resolution(StrEnum):
    """Size class of a channel's picture, read from its screenshots or chosen by hand."""

    SD = "480p"
    HD = "720p"
    FULL_HD = "1080p"
    UHD = "4k"
    OTHER = "other"


def _enum_column(enum_class: type[StrEnum], *, width: int):
    """A text column holding the enum's value, with the length the old schema gave it."""
    return Enum(
        enum_class,
        native_enum=False,
        create_constraint=False,
        length=width,
        values_callable=lambda members: [member.value for member in members],
    )


def _allowed_values(enum_class: type[StrEnum]) -> str:
    """The enum's values as the SQL list a SQLite check constraint compares against."""
    return ", ".join(f"'{member.value}'" for member in enum_class)


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

    # `all`: let the database decide on delete, so its RESTRICT rule is the one that speaks.
    channels: Mapped[list["Channel"]] = relationship(
        back_populates="category", passive_deletes="all"
    )


class Channel(Base):
    __tablename__ = "channel"
    __table_args__ = (
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
            f"resolution IN ({_allowed_values(Resolution)})",
            name="resolution_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    # Set while the title is ours and not the user's: a later check may replace it.
    title_pending: Mapped[bool] = mapped_column(default=False, server_default=false())
    content_id: Mapped[str] = mapped_column(String(40), unique=True)
    # RESTRICT: a category still in use cannot disappear from under its channels.
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id", ondelete="RESTRICT"), index=True
    )
    # SET NULL and never CASCADE: when a guide feed stops offering a channel, the catalogue
    # channel loses its guide and stays, because it is the catalogue that is the asset.
    guide_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("epg_channel.id", ondelete="SET NULL"), index=True
    )
    language: Mapped[str | None] = mapped_column(String(2))  # ISO 639-1, lowercase
    country: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-1 alpha-2, uppercase
    resolution: Mapped[Resolution | None] = mapped_column(_enum_column(Resolution, width=8))
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
            f"status IN ({_allowed_values(CheckStatus)})",
            name="status_valid",
        ),
        Index("ix_check_result_channel_id_checked_at", "channel_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[CheckStatus] = mapped_column(_enum_column(CheckStatus, width=16))
    peers: Mapped[int | None]
    speed_down: Mapped[int | None]
    # The engine turns a Content ID into a different infohash, so both are kept.
    infohash: Mapped[str | None] = mapped_column(String(40))
    screenshot_path: Mapped[str | None] = mapped_column(String(500))
    # Size of that screenshot, when the check took one.
    width: Mapped[int | None]
    height: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(String(500))

    channel: Mapped[Channel] = relationship(back_populates="checks")


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(1000))


class EpgChannel(Base):
    """A channel as one guide names it.

    Guides disagree: two of them use one id for different channels, and give one channel
    two different ids. So a guide channel is identified by its source together with the id
    that source used, and never by the name a person would recognise. Which catalogue
    channel follows which guide channel, and the aliases that takes, lives in
    `EpgAlias`, not here.
    """

    __tablename__ = "epg_channel"
    __table_args__ = (
        # Both columns are named in full: the repo's convention would produce
        # `uq_epg_channel_source`, which reads as if the source alone were unique.
        UniqueConstraint(
            "source", "source_channel_id", name="uq_epg_channel_source_source_channel_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The feed this channel was read from. A source is a cache that gets replaced, and this
    # is what tells which cache a row came from and which one stopped offering it.
    source: Mapped[str] = mapped_column(String(500))
    source_channel_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    icon: Mapped[str | None] = mapped_column(String(500))
    # The last refresh that still found this channel in its source. An older stamp is how
    # "the source dropped it" is told apart from "the source did not answer".
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    programmes: Mapped[list["Programme"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan", passive_deletes=True
    )


class Programme(Base):
    """One entry of a guide channel's schedule, both bounds in UTC."""

    __tablename__ = "programme"
    __table_args__ = (
        # A refresh reads mostly the same listings again, so the same programme has to land
        # on the same row. Spelled out in full for the same reason as the unique above:
        # `uq_programme_epg_channel_id` would claim a channel has a single programme.
        UniqueConstraint("epg_channel_id", "start", name="uq_programme_epg_channel_id_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # No index of its own: the unique pair above already answers every lookup by channel,
    # the foreign-key cascade included.
    epg_channel_id: Mapped[int] = mapped_column(ForeignKey("epg_channel.id", ondelete="CASCADE"))
    start: Mapped[datetime] = mapped_column(UTCDateTime)
    stop: Mapped[datetime] = mapped_column(UTCDateTime)
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str | None] = mapped_column(String(2000))

    channel: Mapped[EpgChannel] = relationship(back_populates="programmes")


class EpgAlias(Base):
    """A person's decision that this catalogue channel belongs to that guide channel.

    One row per catalogue channel, so a second decision replaces the first. The matcher
    consults this table before anything else and `--auto` never writes to it, which is what
    makes a manual decision final. `epg_channel_id` can be NULL because the guide channel
    behind it may be gone; the row is kept so the channel stays human-owned instead of
    quietly becoming the matcher's again.
    """

    __tablename__ = "epg_alias"
    __table_args__ = (
        # Written out in full: the convention would call this `uq_epg_alias_channel_id`,
        # which sounds like a statement about the channel rather than about the decision.
        UniqueConstraint("channel_id", name="uq_epg_alias_channel_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    epg_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("epg_channel.id", ondelete="SET NULL")
    )
    # Who decided: the OS user of the CLI run, or `ACESTREAM_SUITE_EPG_ACTOR` if the host sets one.
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
