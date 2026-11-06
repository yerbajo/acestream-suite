import gzip
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.db import models
from app.services import epg

XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="La1.TV">
    <display-name>La 1</display-name>
    <icon src="https://www.tdtchannels.com/images/La1.png"/>
  </channel>
  <channel id="DAZN1.TV"><display-name>DAZN 1</display-name></channel>
  <programme start="20261013120000 +0200" stop="20261013133000 +0200" channel="La1.TV">
    <title>Telediario</title>
    <desc>La actualidad del mediodía.</desc>
  </programme>
  <programme start="20261013140000 +0200" stop="20261013150000 +0200" channel="DAZN1.TV">
    <title>Fútbol</title>
  </programme>
</tv>
"""

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_a_document_gives_its_channels_and_its_programmes():
    channels, programmes = epg.parse_xmltv(XMLTV)

    assert [(c.id, c.name) for c in channels] == [("La1.TV", "La 1"), ("DAZN1.TV", "DAZN 1")]
    assert channels[0].icon == "https://www.tdtchannels.com/images/La1.png"
    # One of the two carries an icon and the other does not: a missing icon is normal in these
    # feeds, and nothing about it is worth refusing a whole document over.
    assert channels[1].icon is None
    assert [(p.channel_id, p.title) for p in programmes] == [
        ("La1.TV", "Telediario"),
        ("DAZN1.TV", "Fútbol"),
    ]
    assert programmes[0].description == "La actualidad del mediodía."
    assert programmes[1].description is None
    # 12:00 +0200 is 10:00 UTC. An offset is the only zone a source gives, so UTC is the form every
    # caller compares against and stores.
    assert programmes[0].start == datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
    assert programmes[0].stop - programmes[0].start == timedelta(minutes=90)


@pytest.mark.parametrize(
    "document",
    [
        b"",  # the download failed mid-transfer
        '<?xml version="1.0"?><tv></tv>',  # a source with nothing in it today
        "<tv><channel id=",  # truncated, not well formed
        "<html><body>maintenance</body></html>",  # a page where the feed should be
    ],
)
def test_a_broken_or_empty_document_parses_to_nothing_instead_of_raising(document):
    assert epg.parse_xmltv(document) == ([], [])


def test_the_source_offset_is_converted_to_utc_and_a_non_timestamp_is_rejected():
    for value, expected in [
        ("20261013120000 +0200", datetime(2026, 10, 13, 10, 0, tzinfo=UTC)),
        ("20261013120000 +0000", datetime(2026, 10, 13, 12, 0, tzinfo=UTC)),
        # A positive offset East of here, so a date that moves back a day: the source's own zone is
        # never what a window is compared against.
        ("20261013010000 +0530", datetime(2026, 10, 12, 19, 30, tzinfo=UTC)),
    ]:
        assert epg.parse_timestamp(value) == expected

    with pytest.raises(ValueError, match="not an XMLTV timestamp"):
        epg.parse_timestamp("2026-10-13 12:00:00 +0200")


def test_the_sources_come_from_the_environment_as_json():
    assert epg.xmltv_sources('["https://a/TV.xml.gz", " https://b/ES1.xml.gz "]') == [
        "https://a/TV.xml.gz",
        "https://b/ES1.xml.gz",
    ]

    # A typo must cost the guide, never the app: the catalogue is still browsable without it.
    assert epg.xmltv_sources("https://a/TV.xml.gz") == []
    assert epg.xmltv_sources('{"a": 1}') == []
    assert epg.xmltv_sources("") == []


def _programme(starts_in_hours: float, ends_in_hours: float) -> epg.Programme:
    return epg.Programme(
        channel_id="La1.TV",
        start=NOW + timedelta(hours=starts_in_hours),
        stop=NOW + timedelta(hours=ends_in_hours),
        title="x",
    )


def test_the_window_keeps_what_is_on_now_and_drops_the_far_past():
    on_now = _programme(-1, 1)
    starting_soon = _programme(2, 3)
    over_an_hour_ago = _programme(-2, -1)
    beyond_the_window = _programme(100, 101)

    kept = [
        p
        for p in (on_now, starting_soon, over_an_hour_ago, beyond_the_window)
        if epg.in_window(p, now=NOW, hours=72)
    ]

    assert kept == [on_now, starting_soon]


# --- the refresh --------------------------------------------------------------

SOURCE = "https://epg.test/TV.xml.gz"
OTHER = "https://epg.test/ES1.xml.gz"
DEAD = "https://epg.test/dead.xml.gz"

LA1 = '<channel id="La1.TV"><display-name>La 1</display-name></channel>'
OLD = '<channel id="Old.TV"><display-name>Antiguo</display-name></channel>'


# The fragments are written with the source's own offsets, but relative to NOW, so a test never
# depends on the day it runs on: `now=NOW` is what every refresh below is handed.
def programme(
    channel: str, starts_in: timedelta, ends_in: timedelta, *, title: str = "Programa"
) -> str:
    start = (NOW + starts_in).strftime("%Y%m%d%H%M%S +0000")
    stop = (NOW + ends_in).strftime("%Y%m%d%H%M%S +0000")
    return (
        f'<programme start="{start}" stop="{stop}" channel="{channel}">'
        f"<title>{title}</title></programme>"
    )


def document(*parts: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><tv>' + "".join(parts) + "</tv>"


def rows(db, model) -> list:
    return list(db.scalars(select(model)))


def test_a_refresh_stores_the_window_and_a_second_one_writes_nothing(db, respx_mock):
    body = document(LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=2)))
    respx_mock.get(SOURCE).respond(content=body.encode())

    first = epg.refresh(db, sources=[SOURCE], now=NOW)
    count = db.scalar(select(func.count()).select_from(models.Programme))
    second = epg.refresh(db, sources=[SOURCE], now=NOW)

    assert (first.sources[0].channels, first.sources[0].parsed, first.sources[0].stored) == (
        1,
        1,
        1,
    )
    assert count == 1
    # THE assertion of this test: UNIQUE (epg_channel_id, start) is what turns a second refresh of
    # the same document into an update instead of a second copy. The row count is the proof that
    # matters — the sources are refreshed every 12 h with mostly the same listings.
    assert db.scalar(select(func.count()).select_from(models.Programme)) == count
    assert second.sources[0].stored == 0
    assert not second.fatal


def test_a_refresh_updates_a_programme_that_changed_instead_of_adding_it_again(db, respx_mock):
    route = respx_mock.get(SOURCE)
    route.respond(
        content=document(
            LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=2), title="Antes")
        ).encode()
    )
    epg.refresh(db, sources=[SOURCE], now=NOW)

    route.respond(
        content=document(
            LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=3), title="Después")
        ).encode()
    )
    report = epg.refresh(db, sources=[SOURCE], now=NOW)

    assert [(r.title, r.stop - r.start) for r in rows(db, models.Programme)] == [
        ("Después", timedelta(hours=2))
    ]
    assert report.sources[0].stored == 1  # rewritten, not appended


def test_a_refresh_prunes_the_programmes_that_are_over(db, respx_mock):
    route = respx_mock.get(SOURCE)
    route.respond(
        content=document(LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=2))).encode()
    )
    epg.refresh(db, sources=[SOURCE], now=NOW)

    later = NOW + timedelta(hours=5)
    route.respond(
        content=document(
            LA1, programme("La1.TV", timedelta(hours=6), timedelta(hours=7), title="Hoy")
        ).encode()
    )
    report = epg.refresh(db, sources=[SOURCE], now=later)

    # The listing that ended five hours ago is nowhere in the document any more, which is what a
    # refresh looks like after a day: only the pruning takes it out of the table.
    assert report.sources[0].pruned == 1
    assert [p.title for p in rows(db, models.Programme)] == ["Hoy"]


def test_a_refresh_removes_a_channel_its_source_stopped_offering(db, respx_mock):
    route = respx_mock.get(SOURCE)
    listed = document(
        LA1,
        OLD,
        programme("La1.TV", timedelta(hours=1), timedelta(hours=2)),
        programme("Old.TV", timedelta(hours=1), timedelta(hours=2), title="Del canal que se va"),
    )
    route.respond(content=listed.encode())
    epg.refresh(db, sources=[SOURCE], now=NOW)

    # Half an hour on, `Old.TV` is still listed inside the window, so it is the channel going that
    # has to take it: nothing here is over yet.
    route.respond(
        content=document(LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=2))).encode()
    )
    report = epg.refresh(db, sources=[SOURCE], now=NOW + timedelta(minutes=30))

    assert report.sources[0].pruned == 0
    assert report.channels_removed == 1
    # The dropped channel takes its own programmes with it (ON DELETE CASCADE), which is why
    # `channels_removed` is counted apart from `pruned`.
    assert [c.source_channel_id for c in rows(db, models.EpgChannel)] == ["La1.TV"]
    assert [p.title for p in rows(db, models.Programme)] == ["Programa"]


def test_a_programme_that_ended_moments_ago_waits_out_the_grace(db, respx_mock):
    respx_mock.get(SOURCE).respond(
        content=document(
            LA1,
            programme("La1.TV", -timedelta(hours=1), timedelta(minutes=1), title="Corto"),
            programme("La1.TV", -timedelta(hours=2), timedelta(minutes=6), title="Largo"),
        ).encode()
    )
    epg.refresh(db, sources=[SOURCE], now=NOW)

    # Eight minutes on, both are over: `Largo` ended two minutes ago and stays, because a client
    # whose clock lags a little must not lose the programme it is showing the moment it ends.
    report = epg.refresh(db, sources=[SOURCE], now=NOW + timedelta(minutes=8))

    assert report.sources[0].pruned == 1
    assert [p.title for p in rows(db, models.Programme)] == ["Largo"]


def test_one_dead_provider_is_reported_and_the_rest_of_the_guide_still_lands(db, respx_mock):
    respx_mock.get(DEAD).mock(side_effect=httpx.ConnectError("no route"))
    respx_mock.get(OTHER).respond(status_code=404)
    respx_mock.get("https://epg.test/html.xml.gz").respond(
        content=b"<html><body>mantenimiento</body></html>"
    )
    respx_mock.get(SOURCE).respond(
        content=document(LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=2))).encode()
    )

    report = epg.refresh(db, sources=[DEAD, OTHER, "https://epg.test/html.xml.gz", SOURCE], now=NOW)

    assert [s.error is None for s in report.sources] == [False, False, False, True]
    assert [s.stored for s in report.sources] == [0, 0, 0, 1]
    # Three of the four sources are dead and the run is still a success: a guide with one working
    # provider is a guide, and the point of the whole layer is that it degrades instead of failing.
    assert not report.fatal
    assert [p.title for p in rows(db, models.Programme)] == ["Programa"]


def test_gzip_is_read_by_its_magic_and_never_by_its_url(db, respx_mock):
    body = document(LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=2))).encode()
    # tdtchannels answers the `.gz` path with a 301 to a file whose content-type says nothing, and
    # epgshare01 serves gzip from a path that does not mention it: the suffix must never decide.
    respx_mock.get("https://epg.test/plain.xml.gz").respond(content=body)
    respx_mock.get("https://epg.test/not-saying-so.xml").respond(content=gzip.compress(body))

    report = epg.refresh(
        db, sources=["https://epg.test/plain.xml.gz", "https://epg.test/not-saying-so.xml"], now=NOW
    )

    assert [s.error for s in report.sources] == [None, None]
    assert [s.stored for s in report.sources] == [1, 1]


def test_a_dry_run_fetches_and_parses_but_writes_nothing(db, respx_mock):
    respx_mock.get(SOURCE).respond(
        content=document(LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=2))).encode()
    )

    report = epg.refresh(db, sources=[SOURCE], now=NOW, dry_run=True)

    assert (report.sources[0].channels, report.sources[0].windowed) == (1, 1)
    assert report.sources[0].stored == 0
    assert rows(db, models.EpgChannel) == []
    assert rows(db, models.Programme) == []


def test_the_same_programme_twice_in_one_document_is_one_row_and_not_a_crash(db, respx_mock):
    listing = programme("La1.TV", timedelta(hours=1), timedelta(hours=2), title="Antes")
    respx_mock.get(SOURCE).respond(
        content=document(
            LA1,
            listing,
            programme("La1.TV", timedelta(hours=1), timedelta(hours=2), title="Después"),
        ).encode()
    )

    report = epg.refresh(db, sources=[SOURCE], now=NOW)

    # A duplicated listing would be two INSERTs of one key and the whole flush would fail; the
    # second one has to land on the row the first one already put in the session.
    assert not report.sources[0].error
    assert [p.title for p in rows(db, models.Programme)] == ["Después"]


def test_a_programme_of_a_channel_the_document_does_not_declare_is_not_invented(db, respx_mock):
    respx_mock.get(SOURCE).respond(
        content=document(
            LA1,
            programme("La1.TV", timedelta(hours=1), timedelta(hours=2)),
            programme("Fantasma.TV", timedelta(hours=1), timedelta(hours=2)),
        ).encode()
    )

    report = epg.refresh(db, sources=[SOURCE], now=NOW)

    assert report.sources[0].parsed == 2
    assert report.sources[0].stored == 1
    assert [c.source_channel_id for c in rows(db, models.EpgChannel)] == ["La1.TV"]


def test_a_source_that_answers_with_more_than_the_cap_is_refused(db, respx_mock, monkeypatch):
    # The aggregator's all-countries file is 196 MB gzipped: a source pasted in wrong must cost one
    # source's worth of memory, not the container's.
    monkeypatch.setattr(epg, "MAX_BYTES", 64)
    respx_mock.get(SOURCE).respond(
        content=document(LA1, programme("La1.TV", timedelta(hours=1), timedelta(hours=2))).encode()
    )

    report = epg.refresh(db, sources=[SOURCE], now=NOW)

    assert "MB" in report.sources[0].error
    assert report.fatal
    assert rows(db, models.Programme) == []
