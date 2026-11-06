"""The mapping layer: normalisation, scoring, aliases, and the `epg-map` command.

The titles in these tests are the ones the real catalogue actually carries: `#VAMOS FHD --> NEW
ERA`, `DAZN1`, `TELEDEPORTE Spain 1080`, `beIN Movies Turk`. They are messy by nature, and the
whole point of the matcher is that a person can overrule it once and never see the guess again.
"""

import getpass

import pytest
from sqlalchemy import select

from app.cli import main
from app.db import models
from app.services.epg_matching import ACTOR_FALLBACK, Matcher, actor_name, normalise, score

SOURCES = "https://epg.test/TV.xml.gz"


def test_the_actor_name_prefers_the_deployment_then_the_environment(monkeypatch):
    """`ACESTREAM_SUITE_EPG_ACTOR` is the host's own name for the operator and outranks the shell's."""
    monkeypatch.setenv("ACESTREAM_SUITE_EPG_ACTOR", "host-set")
    monkeypatch.setenv("USER", "shell-user")
    monkeypatch.setenv("LOGNAME", "shell-login")
    monkeypatch.setattr(getpass, "getuser", lambda: "from-passwd")
    assert actor_name() == "host-set"

    monkeypatch.delenv("ACESTREAM_SUITE_EPG_ACTOR")
    assert actor_name() == "shell-user"

    monkeypatch.delenv("USER")
    assert actor_name() == "shell-login"

    monkeypatch.delenv("LOGNAME")
    assert actor_name() == "from-passwd"


def test_an_os_error_from_getpass_is_the_fallback_too(monkeypatch):
    # Some platforms raise OSError where the uid lookup raises KeyError; both are a failed lookup.
    monkeypatch.delenv("ACESTREAM_SUITE_EPG_ACTOR", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)

    def no_lookup() -> str:
        raise OSError("no pwd module")

    monkeypatch.setattr(getpass, "getuser", no_lookup)
    assert actor_name() == ACTOR_FALLBACK


def make_channel(session, title: str, number: int = 0) -> models.Channel:
    # 40 lowercase hex characters, the form the content_id check constraint enforces.
    channel = models.Channel(title=title, content_id=f"{number:040x}")
    session.add(channel)
    session.flush()
    return channel


def make_guide(
    session, name: str, source_channel_id: str, *, source: str = SOURCES
) -> models.EpgChannel:
    guide = models.EpgChannel(source=source, source_channel_id=source_channel_id, name=name)
    session.add(guide)
    session.flush()
    return guide


def test_normalise_strips_the_noise_the_real_catalogue_carries():
    assert normalise("#VAMOS FHD --> NEW ERA") == "vamos"
    assert normalise("DAZN1") == "dazn 1"
    assert normalise("TELEDEPORTE Spain 1080") == "teledeporte spain"
    assert normalise("Animal Planet HD [PL]") == "animal planet"
    assert normalise("24 HORAS Spain") == "24 horas spain"
    assert normalise("DAZN 1 BAR FHD --> NEW ERA") == "dazn 1 bar"
    assert normalise(".red [RU]") == "red"


def test_the_matcher_reads_the_messy_title_and_finds_the_guide():
    matcher = Matcher([(1, "#Vamos"), (2, "DAZN 1"), (3, "DAZN 1 Bar"), (4, "Teledeporte")])

    assert matcher.best("#VAMOS FHD --> NEW ERA").name == "#Vamos"
    assert matcher.best("DAZN1").name == "DAZN 1"
    assert matcher.best("TELEDEPORTE Spain 1080").name == "Teledeporte"
    # `BAR` is a word the other guide names do not carry: containment must not swallow it.
    assert matcher.best("DAZN 1 BAR FHD --> NEW ERA").name == "DAZN 1 Bar"


def test_the_three_kinds_are_exact_containment_and_fuzzy():
    assert score(normalise("#VAMOS FHD --> NEW ERA"), normalise("#Vamos")) == (1.0, "exact")
    assert score(normalise("TELEDEPORTE Spain 1080"), normalise("Teledeporte")) == (
        0.85,
        "containment",
    )
    fuzzy_score, fuzzy_kind = score(normalise("Discovery Chanel"), normalise("Discovery Channel"))
    assert fuzzy_kind == "fuzzy"
    assert 0.95 <= fuzzy_score < 1.0


def test_a_pairing_the_house_paid_for_before_is_not_a_match():
    matcher = Matcher([(1, "beIN Sports HD 1"), (2, "Premier Sports 1"), (3, "DAZN 1")])

    # `beIN Movies` is not sport and `Premiere` is not `Premier Sports`; a wrong mapping here is a
    # lie about what is on, so the matcher has to answer "nothing", not the nearest spelling.
    assert matcher.best("bein Movies Turk") is None
    assert matcher.best("BCU Premiere HD [RU]") is None
    assert score(normalise("BCU Premiere HD [RU]"), normalise("Premier Sports 1"))[1] == "below"


def test_two_equally_good_guide_channels_are_not_a_guess():
    # A bare `Eurosport` fits `Eurosport 1` and `Eurosport 2` equally: the alias table is the place
    # for that decision, not the matcher.
    assert Matcher([(1, "Eurosport 1"), (2, "Eurosport 2")]).best("Eurosport [PL]") is None


@pytest.mark.parametrize(
    ("title", "guide"),
    [
        ("#VAMOS FHD --> NEW ERA", "#Vamos"),
        ("DAZN1", "DAZN 1"),
        ("TELEDEPORTE Spain 1080", "Teledeporte"),
        ("ANTENA 3 Spain", "Antena 3"),
        ("M+ Deportes 2 1080 zeronet", "M+ Deportes 2"),
        ("Discovery [PL]", "Discovery Channel"),
    ],
)
def test_when_the_difference_is_only_noise_the_pair_maps(title, guide):
    """The difference between the two names is a quality tag, a country or a stream brand."""
    assert Matcher([(1, guide)]).best(title).name == guide


@pytest.mark.parametrize(
    ("title", "guide"),
    [
        # The four pairs the symmetric containment produced for real on 2026-10-13.
        ("Star TV HD", "TVE_STAR.TV"),
        ("TV4 HD [PL]", "M+ LALIGA TV 4"),
        ("Laliga Hypermotion 1 720 --> NEW ERA", "LaLiga+"),
        ("HISTORY [PL]", "BBC History"),
        # A paid-for false positive, and a second-division channel against the general one.
        ("beIN Movies", "beIN Sports HD 1"),
        ("Premiere", "Premier Sports 1"),
        ("Deportes 2 720 zeronet", "M+ Deportes 2"),
    ],
)
def test_a_content_token_on_either_side_is_never_the_same_channel(title, guide):
    """`tve`, `laliga`, `hypermotion`, `m` and a channel number are content, not noise."""
    assert Matcher([(1, guide)]).best(title) is None


def test_a_generic_word_alone_is_not_enough_to_be_the_same_channel():
    # `channel`/`canal`/`tv` is noise only when the other side carries a distinctive token:
    # `channel` on its own names nothing, so `Channel TV` is not `Channel`...
    assert Matcher([(1, "Channel TV")]).best("Channel") is None
    # ...while `discovery` does carry the name, so the generic suffix may be ignored.
    assert Matcher([(1, "Discovery Channel")]).best("Discovery").name == "Discovery Channel"


def test_a_short_single_token_is_never_the_basis_of_a_containment():
    # `24` is two characters and swallows `24 Horas`; a short lone word is a guess, not a match.
    assert Matcher([(1, "24 Horas")]).best("24") is None


def test_the_report_shows_the_leftover_tokens_that_rejected_a_pair(db_factory, capsys):
    with db_factory() as session:
        make_channel(session, "TV4 HD [PL]")
        make_guide(session, "M+ LALIGA TV 4", "d4")
        session.commit()

    assert main(["epg-map", "--report", "5"], session_factory=db_factory) == 0

    out = capsys.readouterr().out
    assert "TV4 HD [PL]" in out
    # The evidence a person needs in the mapping screen: which tokens were left over, by name.
    assert "'tv 4' ⊄ 'm laliga tv 4': sobra 'm laliga'" in out


def test_a_manual_decision_is_not_overwritten_by_auto(db_factory, capsys):
    with db_factory() as session:
        channel = make_channel(session, "TELEDEPORTE Spain 1080")
        matcher_choice = make_guide(session, "Teledeporte", "t1")
        manual_choice = make_guide(session, "Teledeporte 2", "t2")
        session.commit()
        channel_id, matcher_id, manual_id = channel.id, matcher_choice.id, manual_choice.id

    # The matcher alone picks `matcher_id`; the person picks `manual_id` once and it sticks.
    assert main(["epg-map", "--auto"], session_factory=db_factory) == 0
    with db_factory() as session:
        assert session.get(models.Channel, channel_id).guide_channel_id == matcher_id

    assert main(["epg-map", "--clear", str(channel_id)], session_factory=db_factory) == 0
    assert (
        main(["epg-map", "--set", str(channel_id), str(manual_id)], session_factory=db_factory) == 0
    )
    assert main(["epg-map", "--auto"], session_factory=db_factory) == 0

    with db_factory() as session:
        assert session.get(models.Channel, channel_id).guide_channel_id == manual_id
        alias = session.scalar(
            select(models.EpgAlias).where(models.EpgAlias.channel_id == channel_id)
        )
        assert alias.epg_channel_id == manual_id
    capsys.readouterr()


def test_a_second_manual_decision_replaces_the_first(db_factory):
    with db_factory() as session:
        channel = make_channel(session, "TELEDEPORTE Spain 1080")
        first = make_guide(session, "Teledeporte", "t1")
        second = make_guide(session, "Teledeporte 2", "t2")
        session.commit()
        channel_id, first_id, second_id = channel.id, first.id, second.id

    assert (
        main(["epg-map", "--set", str(channel_id), str(first_id)], session_factory=db_factory) == 0
    )
    assert (
        main(["epg-map", "--set", str(channel_id), str(second_id)], session_factory=db_factory) == 0
    )

    with db_factory() as session:
        aliases = session.scalars(select(models.EpgAlias)).all()
        assert len(aliases) == 1
        assert aliases[0].epg_channel_id == second_id
        assert session.get(models.Channel, channel_id).guide_channel_id == second_id


def test_a_guide_channel_that_vanishes_leaves_the_catalogue_channel(db):
    channel = make_channel(db, "La 1")
    guide = make_guide(db, "La 1", "la1")
    channel.guide_channel_id = guide.id
    db.commit()

    db.delete(guide)
    db.commit()
    db.expire_all()

    # ON DELETE SET NULL, never CASCADE: the feed dropping a channel is not a reason to delete a
    # catalogue channel the house may still be watching.
    fresh = db.get(models.Channel, channel.id)
    assert fresh is not None
    assert fresh.guide_channel_id is None


def test_the_report_counts_and_shows_what_the_matcher_considered(db_factory, capsys):
    with db_factory() as session:
        mapped = make_channel(session, "DAZN1")
        make_channel(session, "Telemadrid Internacional HD", number=1)
        guide = make_guide(session, "DAZN 1", "d1")
        mapped.guide_channel_id = guide.id
        session.commit()

    assert main(["epg-map"], session_factory=db_factory) == 0

    out = capsys.readouterr().out
    assert "Canales del catálogo: 2" in out
    assert "Con guía: 1" in out
    assert "Sin guía: 1" in out
    assert "Telemadrid Internacional HD" in out
    # The evidence a person needs to decide: the guide names the matcher looked at, even the ones
    # it rejected.
    assert "propuestas:" in out


def test_auto_maps_only_what_it_trusts(db_factory, capsys):
    with db_factory() as session:
        channel = make_channel(session, "DAZN1")
        guide = make_guide(session, "DAZN 1", "d1")
        session.commit()
        channel_id, guide_id = channel.id, guide.id

    assert main(["epg-map", "--auto"], session_factory=db_factory) == 0

    out = capsys.readouterr().out
    assert "Asignados por el matcher: 1" in out
    with db_factory() as session:
        assert session.get(models.Channel, channel_id).guide_channel_id == guide_id


def test_set_then_clear_round_trips_the_alias(db_factory, capsys):
    with db_factory() as session:
        channel = make_channel(session, "TELEDEPORTE Spain 1080")
        guide = make_guide(session, "Teledeporte", "t1")
        session.commit()
        channel_id, guide_id = channel.id, guide.id

    assert (
        main(["epg-map", "--set", str(channel_id), str(guide_id)], session_factory=db_factory) == 0
    )
    with db_factory() as session:
        assert session.get(models.Channel, channel_id).guide_channel_id == guide_id
        assert session.scalar(select(models.EpgAlias)).epg_channel_id == guide_id

    assert main(["epg-map", "--clear", str(channel_id)], session_factory=db_factory) == 0
    with db_factory() as session:
        assert session.get(models.Channel, channel_id).guide_channel_id is None
        assert session.scalars(select(models.EpgAlias)).all() == []


def test_setting_an_unknown_channel_or_guide_is_an_error(db_factory, capsys):
    assert main(["epg-map", "--set", "1", "1"], session_factory=db_factory) == 1
    assert "no existen" in capsys.readouterr().err


@pytest.mark.parametrize("command", [["--auto"], ["--report", "5"], ["--set", "1", "2"]])
def test_the_mapping_actions_are_mutually_exclusive(command):
    # `--clear` cannot be combined with the rest either; argparse is what enforces it, so the
    # exit code is 2 for every combination.
    with pytest.raises(SystemExit) as caught:
        main(["epg-map", *command, "--clear", "3"])

    assert caught.value.code == 2
