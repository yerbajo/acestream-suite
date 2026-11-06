"""The mapping layer: which guide channel a catalogue channel points at.

`app/services/epg.py` is about the feed — fetch, parse, store the programmes. This module is about
the decision: does `#VAMOS FHD --> NEW ERA` mean `#Vamos`? The names come from different hands, so
the matcher is a start and the `epg_alias` table is the answer: a person's decision is applied
first and `--auto` never overrules it.

A score below the threshold maps nothing. An unmapped channel renders as a blank cell; a wrong
mapping would claim the wrong programme is on, and that is worse than an empty guide.
"""

from __future__ import annotations

import difflib
import getpass
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import env
from app.db import models
from app.services.errors import NotFoundError

# Exact and containment are structural matches; a fuzzy one has to clear this ratio on its own.
EXACT_SCORE = 1.0
CONTAINMENT_SCORE = 0.85
FUZZY_THRESHOLD = 0.95

# A one-token name shorter than this cannot be the whole basis of a containment match: `HD`, `24`
# or `Sport` appear inside unrelated channels, and a short brand is a guess, not a match.
MIN_SINGLE_TOKEN = 6

# What `created_by` records when the deployment cannot name the operator. Deliberately not a
# person's name: the mapping screen prints this value, and a fabricated name would be a lie about
# who made the decision.
ACTOR_FALLBACK = "unknown (no OS user in this deployment)"

# Quality and format noise a stream title carries and its guide name does not.
QUALITY_TOKENS = (
    "fhd",
    "fullhd",
    "uhd",
    "sd",
    "hd",
    "4k",
    "2160p",
    "1080p",
    "1080i",
    "1080",
    "720p",
    "720",
    "576p",
    "480p",
    "h265",
    "h264",
    "hevc",
    "x265",
    "x264",
    "hq",
    "lq",
)

_QUALITY_RE = re.compile(r"\b(" + "|".join(QUALITY_TOKENS) + r")\b", re.IGNORECASE)
# `[PL]`, `[RU]`, `(SAT)`: a place or a marker, not part of the name.
_BRACKETS_RE = re.compile(r"[\[(].*?[\])]")
# A letter meeting a digit is two words: `DAZN1` is `DAZN 1` and `La 1` is its guide spelling.
_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# A country or a marker: the feed and the catalogue disagree on these and still mean the same
# channel. `spain` is the one that shows up bare in this catalogue (`TELEDEPORTE Spain 1080`); the
# rest arrive when a bracket is not the only place a feed writes them.
COUNTRY_TOKENS = frozenset(
    {
        "spain",
        "espana",
        "es",
        "pl",
        "ru",
        "uk",
        "us",
        "latino",
        "polska",
        "portugal",
        "pt",
        "france",
        "fr",
        "germany",
        "de",
        "italy",
        "it",
        "mexico",
        "argentina",
        "chile",
        "colombia",
        "sat",
    }
)

# A stream brand or a mirror a title carries on top of the channel name: `DAZN 1 zeronet` is
# `DAZN 1`, and `#VAMOS FHD --> NEW ERA` is `#Vamos` even before the arrow.
STREAM_TOKENS = frozenset({"zeronet", "new", "era", "alt", "backup", "multi", "proxy", "stream"})

# A generic word that names the medium and not the channel: `Discovery` and `Discovery Channel`
# are one channel, but only because `discovery` is still there to carry the name.
GENERIC_TOKENS = frozenset({"channel", "canal", "tv"})

# Everything a pair may differ by and still be the same channel. Kept as words and not as one
# regex because these only mean anything as a whole token.
NOISE_TOKENS = COUNTRY_TOKENS | STREAM_TOKENS


def normalise(title: str) -> str:
    """A title reduced to the words a comparison can trust.

    Lowercased and stripped of accents so `Español` and `ESPAnOL` are one word; everything after
    `-->` is a stream brand and is dropped; quality tags, `[country]` markers and punctuation go
    the same way. `#VAMOS FHD --> NEW ERA` and `#Vamos` both become `vamos`.
    """
    text = title.split("-->")[0]
    text = _BRACKETS_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _QUALITY_RE.sub(" ", text.lower())
    text = _BOUNDARY_RE.sub(" ", text)
    return " ".join(_NON_ALNUM_RE.sub(" ", text).split())


def _leftover_tokens(shorter: tuple[str, ...], longer: tuple[str, ...]) -> tuple[str, ...] | None:
    """The tokens `longer` carries beyond the run where `shorter` sits, or `None` when `shorter`
    is not a run of whole words inside `longer`.

    This is the whole difference the rule is about: a containment maps only when every one of
    these is noise, and for the report it is the evidence a person reads.
    """
    size = len(shorter)
    if not size or size > len(longer):
        return None
    for i in range(len(longer) - size + 1):
        if longer[i : i + size] == shorter:
            return longer[:i] + longer[i + size :]
    return None


def _single_token_too_short(words: tuple[str, ...]) -> bool:
    """A lone word shorter than `MIN_SINGLE_TOKEN` is a guess, not a match (`HD`, `24`, `TV`)."""
    return len(words) == 1 and len(words[0]) < MIN_SINGLE_TOKEN


def _is_noise(token: str, other: tuple[str, ...]) -> bool:
    """Whether `token` is a difference without content, given the tokens on the other side.

    A country, a stream brand or a quality tag always is. A generic word (`channel`, `canal`,
    `tv`) is one only when the other side already carries a distinctive token: `Discovery` and
    `Discovery Channel` are one channel, but `channel` must not be the only reason two names look
    alike.
    """
    if token in QUALITY_TOKENS or token in NOISE_TOKENS:
        return True
    if token in GENERIC_TOKENS:
        return any(
            word not in QUALITY_TOKENS and word not in NOISE_TOKENS and word not in GENERIC_TOKENS
            for word in other
        )
    return False


def _explain(catalogue: str, guide: str, leftover: tuple[str, ...] | None) -> str:
    """The rejection as a person reads it: which tokens the longer name had to spare.

    Both names are already normalised: `'tv 4' ⊄ 'm laliga tv 4': sobra 'm laliga'`. A pair that
    is not even a containment says so instead of inventing a leftover.
    """
    if leftover is None:
        return f"'{catalogue}' ≠ '{guide}'"
    return f"'{catalogue}' ⊄ '{guide}': sobra '{' '.join(leftover)}'"


def _evaluate(catalogue: str, guide: str) -> tuple[float, str, tuple[str, ...] | None]:
    """`score` plus the leftover that a rejected containment leaves: the report's raw material."""
    if not catalogue or not guide:
        return 0.0, "below", None
    if catalogue == guide:
        return EXACT_SCORE, "exact", None
    catalogue_words, guide_words = tuple(catalogue.split()), tuple(guide.split())
    if len(catalogue_words) <= len(guide_words):
        shorter, longer = catalogue_words, guide_words
    else:
        shorter, longer = guide_words, catalogue_words
    leftover = _leftover_tokens(shorter, longer)
    if leftover is not None and not _single_token_too_short(shorter):
        # Containment is allowed only when every extra token is noise; one content token on either
        # side means the two names are about different channels and nothing is mapped.
        if all(_is_noise(token, shorter) for token in leftover):
            return CONTAINMENT_SCORE, "containment", None
        return CONTAINMENT_SCORE, "below", leftover
    ratio = difflib.SequenceMatcher(None, catalogue, guide).ratio()
    if ratio >= FUZZY_THRESHOLD:
        return ratio, "fuzzy", None
    return ratio, "below", leftover


def score(catalogue: str, guide: str) -> tuple[float, str]:
    """Rank one normalised catalogue title against one normalised guide name.

    Returns `(score, kind)` where `kind` is `exact`, `containment`, `fuzzy` or `below`. `below`
    means the similarity is reported but the pair must not be mapped: the caller's threshold is
    what decides, and this is the raw material for the report.
    """
    value, kind, _ = _evaluate(catalogue, guide)
    return value, kind


@dataclass(frozen=True)
class Candidate:
    """One guide channel as a possibility for one catalogue title."""

    epg_channel_id: int
    name: str
    score: float
    kind: str
    # The guide name normalised, for ranking only; not part of the decision the caller reads.
    normalised: str = ""
    # Why a near miss was rejected: the tokens the other name had to spare. Empty when the pair
    # was not a containment at all, or was good enough to map.
    leftover: str = ""


class Matcher:
    """The catalogue's titles scored against a fixed set of guide channels.

    The guide names are normalised once here: a report scores hundreds of titles against hundreds
    of names, and normalising `La 1 Barcelona` once per title would be the only cost that shows.
    """

    def __init__(self, guides: Iterable[tuple[int, str]]) -> None:
        self._guides = [(cid, name, normalise(name)) for cid, name in guides]

    def candidates(self, title: str, *, limit: int | None = None) -> list[Candidate]:
        """Every guide channel ranked for one title, best first, thresholded or not.

        The below-threshold ones are kept because the report needs them: a person looking at an
        unmapped channel wants to see the near misses the matcher rejected, not just the void.
        """
        catalogue = normalise(title)
        ranked = []
        for cid, name, guide in self._guides:
            value, kind, leftover = _evaluate(catalogue, guide)
            ranked.append(
                Candidate(
                    cid,
                    name,
                    value,
                    kind,
                    normalised=guide,
                    # A pair below the line explains itself; a mapped one has nothing to explain.
                    leftover="" if kind != "below" else _explain(catalogue, guide, leftover),
                )
            )
        ranked.sort(key=lambda item: (-item.score, -len(item.normalised.split()), item.name))
        return ranked if limit is None else ranked[:limit]

    def best(self, title: str) -> Candidate | None:
        """The guide channel to map this title to, or `None` when nothing is good enough.

        Two guide channels that fit equally well are not a match: a wrong mapping is a lie about
        what is on, and a tie is exactly the case the alias table exists for.
        """
        hits = [candidate for candidate in self.candidates(title) if candidate.kind != "below"]
        if not hits:
            return None
        best_score = hits[0].score
        winners = [candidate for candidate in hits if candidate.score == best_score]
        most_words = max(len(candidate.normalised.split()) for candidate in winners)
        winners = [c for c in winners if len(c.normalised.split()) == most_words]
        if len({candidate.normalised for candidate in winners}) > 1:
            return None
        return winners[0]


def matcher_for(session: Session) -> Matcher:
    """The matcher over every guide channel in the store."""
    rows = session.execute(select(models.EpgChannel.id, models.EpgChannel.name)).all()
    return Matcher(rows)


def actor_name() -> str:
    """Who is making a manual decision.

    `ACESTREAM_SUITE_EPG_ACTOR` first, so the host can name the operator; then `USER`/`LOGNAME`, which the
    process inherits when a shell launched it; then `getpass.getuser()`, guarded. The guard is
    the whole reason this function is more than one line: the deployment runs the container as
    `user: "1000:1000"` with no `/etc/passwd` entry and no `HOME`, so `getpass.getuser()` raises
    `KeyError: 'getpwuid(): uid not found: 1000'` there. Unguarded, that exception escaped
    `set_alias` before the alias row was written, and assigning a guide from the mapping screen
    answered HTTP 500 while `clear` — which names no actor — answered 303. When nothing names the
    operator, `ACTOR_FALLBACK` says so instead of inventing a person.
    """
    actor = (
        env("EPG_ACTOR")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
    )
    if actor:
        return actor
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return ACTOR_FALLBACK


def alias_for(session: Session, channel_id: int) -> models.EpgAlias | None:
    return session.scalar(select(models.EpgAlias).where(models.EpgAlias.channel_id == channel_id))


def search_guides(session: Session, term: str, *, limit: int = 50) -> list[models.EpgChannel]:
    """Guide channels whose name contains `term`, A-Z; a blank term is the first `limit` names.

    This is the mapping screen's search box. The store holds hundreds of names and a select with
    all of them would be megabytes per page, so the list starts at the beginning of the alphabet
    and narrows as the person types.
    """
    stmt = select(models.EpgChannel).order_by(
        func.lower(models.EpgChannel.name), models.EpgChannel.id
    )
    term = term.strip()
    if term:
        # LIKE wildcards typed by the user are literal characters, not patterns.
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(models.EpgChannel.name.ilike(f"%{escaped}%", escape="\\"))
    return list(session.scalars(stmt.limit(limit)))


@dataclass(frozen=True)
class Decision:
    """One manual decision: the catalogue channel, and the guide it was pointed at (or none)."""

    channel_id: int
    channel_title: str
    guide_name: str | None
    created_by: str


def manual_decisions(session: Session) -> list[Decision]:
    """Every `epg_alias` row, which is what the mapping screen offers to undo.

    A decision whose guide channel vanished stays on this list with no name: the row is still
    what keeps the channel human-owned instead of falling back to the matcher, so it is the person
    who has to clear it.
    """
    rows = session.execute(
        select(models.EpgAlias, models.Channel, models.EpgChannel)
        .join(models.Channel, models.EpgAlias.channel_id == models.Channel.id)
        .outerjoin(models.EpgChannel, models.EpgAlias.epg_channel_id == models.EpgChannel.id)
        .order_by(models.Channel.title, models.Channel.id)
    ).all()
    return [
        Decision(
            channel_id=channel.id,
            channel_title=channel.title,
            guide_name=guide.name if guide is not None else None,
            created_by=alias.created_by,
        )
        for alias, channel, guide in rows
    ]


def set_alias(
    session: Session, channel_id: int, epg_channel_id: int, *, actor: str | None = None
) -> models.EpgAlias:
    """Record a manual decision and point the channel at the chosen guide channel.

    A second decision on the same channel replaces the first row, which is what the UNIQUE
    constraint on `channel_id` means. `--auto` skips any channel that has one of these.
    """
    channel = session.get(models.Channel, channel_id)
    if channel is None:
        raise NotFoundError(f"channel not found: {channel_id}")
    guide = session.get(models.EpgChannel, epg_channel_id)
    if guide is None:
        raise NotFoundError(f"guide channel not found: {epg_channel_id}")
    alias = alias_for(session, channel_id)
    if alias is None:
        alias = models.EpgAlias(channel_id=channel_id)
        session.add(alias)
    alias.epg_channel_id = epg_channel_id
    alias.created_by = actor if actor is not None else actor_name()
    channel.guide_channel_id = epg_channel_id
    session.commit()
    return alias


def clear_alias(session: Session, channel_id: int) -> bool:
    """Forget the manual decision and leave the channel unmapped; returns whether one existed."""
    channel = session.get(models.Channel, channel_id)
    if channel is None:
        raise NotFoundError(f"channel not found: {channel_id}")
    alias = alias_for(session, channel_id)
    if alias is not None:
        session.delete(alias)
    channel.guide_channel_id = None
    session.commit()
    return alias is not None


@dataclass
class AutoReport:
    """What `--auto` did: scanned, mapped, and left to a person."""

    examined: int = 0
    mapped: int = 0
    human: int = 0


def auto_map(session: Session) -> AutoReport:
    """Apply the matcher to every unmapped channel that has no manual decision.

    A channel already pointing at a guide is left alone, and so is one with an alias row: that row
    is the whole reason `--auto` is safe to re-run.
    """
    matcher = matcher_for(session)
    owned = set(session.scalars(select(models.EpgAlias.channel_id)))
    report = AutoReport()
    channels = session.scalars(
        select(models.Channel).where(models.Channel.guide_channel_id.is_(None))
    ).all()
    for channel in channels:
        if channel.id in owned:
            report.human += 1
            continue
        report.examined += 1
        candidate = matcher.best(channel.title)
        if candidate is None:
            continue
        channel.guide_channel_id = candidate.epg_channel_id
        report.mapped += 1
    session.commit()
    return report


@dataclass(frozen=True)
class Unmapped:
    """One channel with no guide, and what the matcher would have proposed for it."""

    channel_id: int
    title: str
    manual: bool
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class MappingReport:
    total: int = 0
    mapped: int = 0
    manual: int = 0
    guides: int = 0
    unmapped_total: int = 0
    unmapped: list[Unmapped] = field(default_factory=list)


def report(
    session: Session, *, limit: int = 20, candidates: int = 3, offset: int = 0
) -> MappingReport:
    """The state of the mapping and the `limit` unmapped titles from `offset` on.

    A manually owned channel is shown as such and without proposals: the matcher has nothing to
    say about a decision a person already made. `offset` is what pages the mapping screen; the
    command-line report leaves it at the first page.
    """
    matcher = matcher_for(session)
    owned = set(session.scalars(select(models.EpgAlias.channel_id)))
    channels = list(
        session.scalars(select(models.Channel).order_by(models.Channel.title, models.Channel.id))
    )
    unmapped = [channel for channel in channels if channel.guide_channel_id is None]
    shown = [
        Unmapped(
            channel_id=channel.id,
            title=channel.title,
            manual=channel.id in owned,
            candidates=[]
            if channel.id in owned
            else matcher.candidates(channel.title, limit=candidates),
        )
        for channel in unmapped[offset : offset + limit]
    ]
    return MappingReport(
        total=len(channels),
        mapped=len(channels) - len(unmapped),
        manual=len(owned),
        # The guide's own size: the mapping screen says when there is nothing to map onto at all.
        guides=session.scalar(select(func.count()).select_from(models.EpgChannel)) or 0,
        unmapped_total=len(unmapped),
        unmapped=shown,
    )
