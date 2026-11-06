"""The mapping layer: which guide channel each catalogue channel points at.

`app/services/epg.py` owns the feed; this module owns the decision. A stream title
(`#VAMOS FHD --> NEW ERA`) and a guide name (`#Vamos`) come from different hands, so the matcher
only proposes and the `epg_alias` table decides: a person's choice is applied first, and `--auto`
never overrules it.

Nothing below the threshold is ever mapped. An unmapped channel is a blank cell, while a wrong
mapping claims the wrong programme is on, and that is the worse of the two.
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

# What a pair can be. Exact and containment are structural; fuzzy has to clear its own ratio.
EXACT = "exact"
CONTAINMENT = "containment"
FUZZY = "fuzzy"
BELOW = "below"

EXACT_SCORE = 1.0
CONTAINMENT_SCORE = 0.85
FUZZY_THRESHOLD = 0.95

# A lone word shorter than this may not carry a containment on its own: `HD`, `24` or `Sport` sit
# inside unrelated channels, so a short single token is a guess and not a match.
MIN_SINGLE_TOKEN = 6

# What `created_by` records when the deployment cannot name the operator. Not a person's name on
# purpose: the mapping screen prints this value, and a made-up name would lie about who decided.
ACTOR_FALLBACK = "unknown (no OS user in this deployment)"

# What a stream title carries about the picture and the guide name does not.
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

# A country or a marker: the two sides disagree about these and still mean the same channel.
# `spain` shows up bare in this catalogue (`TELEDEPORTE Spain 1080`); the rest arrive whenever a
# feed writes them outside a bracket.
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

# A stream brand or a mirror added on top of the channel name: `DAZN 1 zeronet` is `DAZN 1`, and
# `#VAMOS FHD --> NEW ERA` is `#Vamos` even before the arrow is cut.
STREAM_TOKENS = frozenset({"zeronet", "new", "era", "alt", "backup", "multi", "proxy", "stream"})

# A word that names the medium instead of the channel. `Discovery` and `Discovery Channel` are one
# channel, but only because `discovery` is left carrying the name.
GENERIC_TOKENS = frozenset({"channel", "canal", "tv"})

# Every difference a pair may carry and still be the same channel. Words and not one regex, because
# these only mean anything as a whole token.
NOISE_TOKENS = COUNTRY_TOKENS | STREAM_TOKENS

ARROW = "-->"
_QUALITY_TAG_RE = re.compile(r"\b(" + "|".join(QUALITY_TOKENS) + r")\b", re.IGNORECASE)
# `[PL]`, `[RU]`, `(SAT)`: a place or a marker, never part of the name.
_MARKER_RE = re.compile(r"[\[(].*?[\])]")
# A letter meeting a digit is two words: `DAZN1` splits into `DAZN 1`, which is how the guide
# spells it.
_LETTER_DIGIT_RE = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")
_PUNCTUATION_RE = re.compile(r"[^a-z0-9]+")


def normalise(title: str) -> str:
    """A title reduced to the words a comparison can trust.

    Lowercased and stripped of accents, so `Español` and `ESPAnOL` are one word. The stream brand
    after `-->` goes, and so do quality tags, `[country]` markers, punctuation and the boundaries
    inside `DAZN1`. `#VAMOS FHD --> NEW ERA` and `#Vamos` both come out as `vamos`.
    """
    text = title.split(ARROW)[0]
    text = _MARKER_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _QUALITY_TAG_RE.sub(" ", text.lower())
    text = _LETTER_DIGIT_RE.sub(" ", text)
    return " ".join(_PUNCTUATION_RE.sub(" ", text).split())


def _tokens_beyond(shorter: tuple[str, ...], longer: tuple[str, ...]) -> tuple[str, ...] | None:
    """The tokens `longer` carries outside the run where `shorter` sits.

    Returns None when `shorter` is not a run of whole words inside `longer`, which is not a
    containment at all. Otherwise this is the entire difference the containment rule judges, and the
    tuple is also the evidence a person is shown when the pair is rejected.
    """
    size = len(shorter)
    if not size or size > len(longer):
        return None
    for first in range(len(longer) - size + 1):
        if longer[first : first + size] == shorter:
            return longer[:first] + longer[first + size :]
    return None


def _lone_word_too_short(words: tuple[str, ...]) -> bool:
    """Whether these words are a single word too short to be a match (`HD`, `24`, `TV`)."""
    return len(words) == 1 and len(words[0]) < MIN_SINGLE_TOKEN


def _is_noise(token: str, other: tuple[str, ...]) -> bool:
    """Whether this token is a difference without content, given the tokens on the other side.

    A quality tag, a country or a stream brand always is. A generic word is one only when the other
    side still says what the channel is: `Discovery` and `Discovery Channel` are one channel, while
    `channel` must never be the only thing two names agree on.
    """
    if token in QUALITY_TOKENS or token in NOISE_TOKENS:
        return True
    if token in GENERIC_TOKENS:
        return any(_distinctive(word) for word in other)
    return False


def _distinctive(token: str) -> bool:
    """Whether a token names the channel, rather than its format, its place or its medium."""
    return token not in QUALITY_TOKENS and token not in NOISE_TOKENS and token not in GENERIC_TOKENS


def _rejection(catalogue: str, guide: str, leftover: tuple[str, ...] | None) -> str:
    """A rejected pair as a person reads it: which tokens the longer name had to spare.

    Both names arrive normalised, so this is the report's own line — `'tv 4' ⊄ 'm laliga tv 4':
    sobra 'm laliga'`. A pair that is not a containment at all says that, instead of inventing
    leftover tokens to blame.
    """
    if leftover is None:
        return f"'{catalogue}' ≠ '{guide}'"
    return f"'{catalogue}' ⊄ '{guide}': sobra '{' '.join(leftover)}'"


@dataclass(frozen=True)
class _Verdict:
    """How one pair of normalised names compares, before any caller's threshold applies.

    `leftover` is what a failed containment had to spare, and None when the pair was never a
    containment in the first place.
    """

    score: float
    kind: str
    leftover: tuple[str, ...] | None = None


def _shorter_then_longer(catalogue: str, guide: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Both names as word tuples, the shorter one first."""
    catalogue_words, guide_words = tuple(catalogue.split()), tuple(guide.split())
    if len(catalogue_words) <= len(guide_words):
        return catalogue_words, guide_words
    return guide_words, catalogue_words


def _verdict(catalogue: str, guide: str) -> _Verdict:
    """How one catalogue title and one guide name compare, both already normalised."""
    if not catalogue or not guide:
        return _Verdict(0.0, BELOW)
    if catalogue == guide:
        return _Verdict(EXACT_SCORE, EXACT)
    shorter, longer = _shorter_then_longer(catalogue, guide)
    leftover = _tokens_beyond(shorter, longer)
    if leftover is not None and not _lone_word_too_short(shorter):
        # A containment stands only when every extra token is noise: a single content token on
        # either side means the two names are about different channels.
        if all(_is_noise(token, shorter) for token in leftover):
            return _Verdict(CONTAINMENT_SCORE, CONTAINMENT)
        return _Verdict(CONTAINMENT_SCORE, BELOW, leftover)
    ratio = difflib.SequenceMatcher(None, catalogue, guide).ratio()
    if ratio >= FUZZY_THRESHOLD:
        return _Verdict(ratio, FUZZY)
    return _Verdict(ratio, BELOW, leftover)


def score(catalogue: str, guide: str) -> tuple[float, str]:
    """One normalised catalogue title against one normalised guide name: `(score, kind)`.

    `kind` is `exact`, `containment`, `fuzzy` or `below`. `below` reports the similarity but says
    the pair must not be mapped: the threshold belongs to the caller, and this is the raw material
    the report is built from.
    """
    verdict = _verdict(catalogue, guide)
    return verdict.score, verdict.kind


@dataclass(frozen=True)
class Candidate:
    """One guide channel offered as a possibility for one catalogue title."""

    epg_channel_id: int
    name: str
    score: float
    kind: str
    # The guide name normalised: ranking only, never part of what the caller reads.
    normalised: str = ""
    # Why a near miss was rejected — the tokens the other name had to spare. Empty when the pair
    # was not a containment at all, or was good enough to map.
    leftover: str = ""


def _candidate(catalogue: str, guide_id: int, name: str, guide: str) -> Candidate:
    """One guide channel scored against one already-normalised title."""
    verdict = _verdict(catalogue, guide)
    return Candidate(
        guide_id,
        name,
        verdict.score,
        verdict.kind,
        normalised=guide,
        # A pair below the line explains itself; a mapped one has nothing to explain.
        leftover="" if verdict.kind != BELOW else _rejection(catalogue, guide, verdict.leftover),
    )


def _preference(candidate: Candidate) -> tuple[float, int, str]:
    """The ranking order: better score, then the more specific name, then the name itself."""
    return (-candidate.score, -len(candidate.normalised.split()), candidate.name)


class Matcher:
    """The catalogue's titles scored against a fixed set of guide channels.

    Every guide name is normalised once, when the matcher is built: a report scores hundreds of
    titles against hundreds of names, and doing that per title is the only cost that would show.
    """

    def __init__(self, guides: Iterable[tuple[int, str]]) -> None:
        self._guides = [(channel_id, name, normalise(name)) for channel_id, name in guides]

    def candidates(self, title: str, *, limit: int | None = None) -> list[Candidate]:
        """That title's guide channels, best first, thresholded or not.

        The ones below the line stay in because the report is built on them: somebody looking at an
        unmapped channel wants the near misses the matcher rejected, not an empty space.
        """
        catalogue = normalise(title)
        ranked = [
            _candidate(catalogue, guide_id, name, guide) for guide_id, name, guide in self._guides
        ]
        ranked.sort(key=_preference)
        return ranked if limit is None else ranked[:limit]

    def best(self, title: str) -> Candidate | None:
        """The one guide channel to map this title to, or None when no single one is good enough.

        Two names that fit equally well are not a match: a wrong mapping lies about what is on, and
        an ambiguous title is exactly the case the alias table exists for.
        """
        hits = [candidate for candidate in self.candidates(title) if candidate.kind != BELOW]
        if not hits:
            return None
        winners = [candidate for candidate in hits if candidate.score == hits[0].score]
        most_words = max(len(candidate.normalised.split()) for candidate in winners)
        winners = [item for item in winners if len(item.normalised.split()) == most_words]
        if len({candidate.normalised for candidate in winners}) > 1:
            return None
        return winners[0]


def matcher_for(session: Session) -> Matcher:
    """The matcher over every guide channel the store holds."""
    rows = session.execute(select(models.EpgChannel.id, models.EpgChannel.name)).all()
    return Matcher(rows)


def actor_name() -> str:
    """Who is making a manual decision, as far as this process can tell.

    The host can name the operator with `ACESTREAM_SUITE_EPG_ACTOR`. Otherwise the shell's `USER` or
    `LOGNAME` carries over, and only then does the passwd lookup get a turn — and it needs its
    guard. The deployed container runs as `user: "1000:1000"` with no `/etc/passwd` entry and no
    `HOME`, so `getpass.getuser()` raises `KeyError: 'getpwuid(): uid not found: 1000'` there.
    Unguarded, that exception escaped `set_alias` before the alias row was written: assigning a
    guide from the mapping screen answered HTTP 500 while `clear`, which names no actor, answered
    303. When nothing can name the operator, `ACTOR_FALLBACK` says so instead of inventing a person.
    """
    named = env("EPG_ACTOR") or os.environ.get("USER") or os.environ.get("LOGNAME")
    if named:
        return named
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return ACTOR_FALLBACK


def alias_for(session: Session, channel_id: int) -> models.EpgAlias | None:
    """The manual decision recorded for one catalogue channel, if there is one."""
    return session.scalar(select(models.EpgAlias).where(models.EpgAlias.channel_id == channel_id))


def _contains(term: str) -> str | None:
    """`term` as a LIKE pattern for "contains it", or None for a term with nothing in it.

    The backslash, `%` and `_` a person types are characters to look for, not wildcards that would
    quietly widen the search.
    """
    term = term.strip()
    if not term:
        return None
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_guides(session: Session, term: str, *, limit: int = 50) -> list[models.EpgChannel]:
    """Guide channels whose name contains `term`, A-Z; a blank term is the first `limit` names.

    This is the mapping screen's picker. Hundreds of names cannot travel inside one page, so the
    list starts at the top of the alphabet and narrows while the person types.
    """
    stmt = select(models.EpgChannel).order_by(
        func.lower(models.EpgChannel.name), models.EpgChannel.id
    )
    pattern = _contains(term)
    if pattern is not None:
        stmt = stmt.where(models.EpgChannel.name.ilike(pattern, escape="\\"))
    return list(session.scalars(stmt.limit(limit)))


@dataclass(frozen=True)
class Decision:
    """One manual decision: the catalogue channel, and the guide it was pointed at (or none)."""

    channel_id: int
    channel_title: str
    guide_name: str | None
    created_by: str


def manual_decisions(session: Session) -> list[Decision]:
    """Every alias row, which is the list the mapping screen offers to undo.

    A decision whose guide channel has vanished stays on the list with no name. The row is what
    keeps the channel human-owned instead of falling back to the matcher, so clearing it is the
    person's call and never a side effect of a refresh.
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


def _known_channel(session: Session, channel_id: int) -> models.Channel:
    """The catalogue channel, or `NotFoundError`: a decision about nothing is not a decision."""
    channel = session.get(models.Channel, channel_id)
    if channel is None:
        raise NotFoundError(f"channel not found: {channel_id}")
    return channel


def _known_guide(session: Session, epg_channel_id: int) -> models.EpgChannel:
    """The guide channel, or `NotFoundError` when the feed no longer offers that id."""
    guide = session.get(models.EpgChannel, epg_channel_id)
    if guide is None:
        raise NotFoundError(f"guide channel not found: {epg_channel_id}")
    return guide


def set_alias(
    session: Session, channel_id: int, epg_channel_id: int, *, actor: str | None = None
) -> models.EpgAlias:
    """Record a manual decision and point the channel at the chosen guide channel.

    A second decision on the same channel replaces the first row, which is what the UNIQUE key on
    `channel_id` means, and `--auto` then leaves the channel alone for good.
    """
    channel = _known_channel(session, channel_id)
    guide = _known_guide(session, epg_channel_id)
    alias = alias_for(session, channel_id)
    if alias is None:
        alias = models.EpgAlias(channel_id=channel_id)
        session.add(alias)
    alias.epg_channel_id = guide.id
    alias.created_by = actor if actor is not None else actor_name()
    channel.guide_channel_id = guide.id
    session.commit()
    return alias


def clear_alias(session: Session, channel_id: int) -> bool:
    """Forget the manual decision and leave the channel unmapped; whether one existed."""
    channel = _known_channel(session, channel_id)
    alias = alias_for(session, channel_id)
    if alias is not None:
        session.delete(alias)
    channel.guide_channel_id = None
    session.commit()
    return alias is not None


@dataclass
class AutoReport:
    """What `--auto` did: what it looked at, what it mapped, and what it left to a person."""

    examined: int = 0
    mapped: int = 0
    human: int = 0


def auto_map(session: Session) -> AutoReport:
    """Offer the matcher to every unmapped channel that no person has decided about.

    A channel that already points at a guide is left alone, and so is one with an alias row: those
    rows are the whole reason this is safe to run again on a later refresh.
    """
    matcher = matcher_for(session)
    owned = set(session.scalars(select(models.EpgAlias.channel_id)))
    report = AutoReport()
    unmapped = session.scalars(
        select(models.Channel).where(models.Channel.guide_channel_id.is_(None))
    ).all()
    for channel in unmapped:
        if channel.id in owned:
            report.human += 1
            continue
        report.examined += 1
        candidate = matcher.best(channel.title)
        if candidate is not None:
            channel.guide_channel_id = candidate.epg_channel_id
            report.mapped += 1
    session.commit()
    return report


@dataclass(frozen=True)
class Unmapped:
    """One channel with no guide, and the proposals the matcher would make for it."""

    channel_id: int
    title: str
    manual: bool
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class MappingReport:
    """The mapping's state and one page of the channels it has not covered."""

    total: int = 0
    mapped: int = 0
    manual: int = 0
    guides: int = 0
    unmapped_total: int = 0
    unmapped: list[Unmapped] = field(default_factory=list)


def _unmapped_row(
    matcher: Matcher, owned: set[int], channel: models.Channel, candidates: int
) -> Unmapped:
    """One unmapped channel as the screen shows it, with proposals unless a person already chose."""
    manual = channel.id in owned
    return Unmapped(
        channel_id=channel.id,
        title=channel.title,
        manual=manual,
        candidates=[] if manual else matcher.candidates(channel.title, limit=candidates),
    )


def report(
    session: Session, *, limit: int = 20, candidates: int = 3, offset: int = 0
) -> MappingReport:
    """The state of the mapping, and `limit` unmapped titles from `offset` on.

    A manually owned channel is shown as such and without proposals: the matcher has nothing to add
    to a decision a person already made. `offset` is what pages the mapping screen; the
    command-line report stays on the first page.
    """
    matcher = matcher_for(session)
    owned = set(session.scalars(select(models.EpgAlias.channel_id)))
    channels = list(
        session.scalars(select(models.Channel).order_by(models.Channel.title, models.Channel.id))
    )
    unmapped = [channel for channel in channels if channel.guide_channel_id is None]
    return MappingReport(
        total=len(channels),
        mapped=len(channels) - len(unmapped),
        manual=len(owned),
        # The guide's own size: the mapping screen says so when there is nothing to map onto yet.
        guides=session.scalar(select(func.count()).select_from(models.EpgChannel)) or 0,
        unmapped_total=len(unmapped),
        unmapped=[
            _unmapped_row(matcher, owned, channel, candidates)
            for channel in unmapped[offset : offset + limit]
        ],
    )
