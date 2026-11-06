"""The EPG screens: now/next on the list, one channel's day grid, and the mapping screen.

Every page here is read-only except the two mapping writes. `check_result` must stay empty
throughout: the duplicate's whole reason for existing is that it does not put a second queue on the
`.28` engine, and the guide is a read of stored listings.
"""

import getpass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import event, func, select

from app.cli import main
from app.db import models
from app.db.base import utcnow
from app.db.models import Check, EpgAlias, EpgChannel, Programme
from app.services import channels, epg_guide, epg_matching

MADRID = ZoneInfo("Europe/Madrid")
SOURCE = "https://epg.test/TV.xml.gz"


def make_channel(db, title, number=0):
    return channels.create_channel(db, title=title, content_id=f"{number:040x}")


def make_guide(db, name, source_channel_id, *, source=SOURCE):
    guide = EpgChannel(source=source, source_channel_id=source_channel_id, name=name)
    db.add(guide)
    db.commit()
    return guide


def add_programme(db, guide, title, start, stop, *, description=None):
    programme = Programme(
        epg_channel_id=guide.id, start=start, stop=stop, title=title, description=description
    )
    db.add(programme)
    db.commit()
    return programme


def count_checks(db) -> int:
    return db.scalar(select(func.count()).select_from(Check))


def test_local_reads_a_utc_row_in_madrid_time():
    # A summer date: Madrid is UTC+2, so the conversion is visible and not the machine's zone.
    value = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)

    assert epg_guide.local(value) == "2026-07-15 12:00"
    assert epg_guide.local(value, "%H:%M") == "12:00"


def test_list_shows_only_what_is_on_now_for_the_mapped_channel(web, db):
    now = datetime.now(UTC)
    mapped = make_channel(db, "Con guía", 1)
    make_channel(db, "Sin guía", 2)
    guide = make_guide(db, "Guía Uno", "g1")
    mapped.guide_channel_id = guide.id
    db.commit()
    current = add_programme(
        db, guide, "Programa actual", now - timedelta(minutes=10), now + timedelta(minutes=50)
    )
    add_programme(
        db, guide, "Programa siguiente", now + timedelta(minutes=50), now + timedelta(minutes=110)
    )

    page = web.get("/").text

    assert "Programa actual" in page
    # What comes next is not repeated here: the cell links to the grid, and that is where a person
    # reads what follows. Repeating it made the widest column out of a fact the guide page owns.
    assert "Programa siguiente" not in page
    expected = f"{current.start.astimezone(MADRID):%H:%M}–{current.stop.astimezone(MADRID):%H:%M}"
    assert expected in page
    # One `Guía` cell per channel; only the mapped one carries a programme.
    assert page.count('class="guide-cell"') == 2
    assert page.count("guide-now") == 1
    assert page.count("guide-next") == 0
    # An unmapped channel is a blank cell, never a wall of "sin datos".
    assert "Sin datos" not in page


def test_list_guide_column_shows_the_current_programme_and_the_guide_name(web, db):
    now = datetime.now(UTC)
    mapped = make_channel(db, "Con guía", 1)
    guide = make_guide(db, "Guía Uno", "g1")
    mapped.guide_channel_id = guide.id
    # A second mapping with no programmes in the window: the same column, a different state.
    quiet = make_channel(db, "Mapeado sin programas", 2)
    quiet.guide_channel_id = make_guide(db, "Guía sin programas", "g2").id
    db.commit()
    add_programme(
        db, guide, "Programa actual", now - timedelta(minutes=10), now + timedelta(minutes=50)
    )
    add_programme(
        db, guide, "Programa siguiente", now + timedelta(minutes=50), now + timedelta(minutes=110)
    )

    page = web.get("/").text

    assert page.count('class="guide-cell"') == 2
    assert '<th scope="col">Guía</th>' in page
    # `Ahora`/`Después` are gone as columns, and the one `Guía` cell carries the programme on now.
    assert ">Ahora<" not in page and ">Después<" not in page
    assert page.count("guide-now") == 1
    assert "Programa siguiente" not in page
    # Nothing in the window is blank, not a second message, while the name below still shows.
    quiet_row = page[page.index("Mapeado sin programas") :]
    quiet_row = quiet_row[: quiet_row.index("</td>", quiet_row.index("guide-cell"))]
    assert "guide-now" not in quiet_row


def test_list_guide_cell_opens_the_channel_guide(web, db):
    now = datetime.now(UTC)
    channel = make_channel(db, "Con guía", 1)
    guide = make_guide(db, "Guía Uno", "g1")
    channel.guide_channel_id = guide.id
    db.commit()
    add_programme(
        db, guide, "Programa actual", now - timedelta(minutes=10), now + timedelta(minutes=50)
    )
    add_programme(
        db, guide, "Programa siguiente", now + timedelta(minutes=50), now + timedelta(minutes=110)
    )

    page = web.get("/").text

    href = f'href="http://127.0.0.1/channels/{channel.id}/guide"'
    # One anchor: the programme on now, named for a screen reader. The next one is not in the list.
    assert page.count(href) == 1
    assert page.count('title="Ver la guía de Con guía"') == 1
    assert 'aria-label="Ver la guía de Con guía: Programa actual"' in page
    assert "Programa siguiente" not in page

    # And the click pays off: the grid this cell links to is where the next programme is read.
    grid = web.get(f"/channels/{channel.id}/guide").text
    assert "Programa siguiente" in grid


def test_list_unmapped_channel_offers_the_mapping_screen(web, db):
    make_channel(db, "Sin guía", 2)

    page = web.get("/").text

    # One discreet line, not a dead cell and not a wall of text.
    assert page.count('class="guide-none hint"') == 1
    assert 'href="http://127.0.0.1/epg/mapping"' in page
    assert 'title="Asignar una guía a Sin guía"' in page
    assert "guide-now" not in page


def test_list_does_not_repeat_the_guide_channel_name(web, db):
    """The list shows the event and its hours, not the name of the guide behind it.

    The row is that channel's own catalogue entry, so `M+ Deportes` next to its programme said
    nothing the row did not already say. What still tells the two blanks apart is the other state's
    link: mapped-with-nothing-on is empty on purpose, unmapped offers `Sin guía · Asignar`.
    """
    mapped = make_channel(db, "Con guía", 1)
    guide = make_guide(db, "M+ Deportes", "g1")
    mapped.guide_channel_id = guide.id
    db.commit()
    make_channel(db, "Sin guía", 2)

    page = web.get("/").text

    assert "M+ Deportes" not in page
    assert 'class="guide-channel' not in page
    # Mapped and quiet: blank. Unmapped: the one link that says so.
    assert page.count('class="guide-none hint"') == 1
    assert 'title="Asignar una guía a Sin guía"' in page


def test_list_shows_when_the_channel_was_added(web, db):
    channel = make_channel(db, "Nuevo", 1)
    channel.created_at = utcnow() - timedelta(days=3)
    db.commit()

    page = web.get("/").text

    assert f'datetime="{channel.created_at.isoformat()}"' in page
    assert "hace 3 d" in page


def test_list_renders_with_no_guide_rows_at_all(web, db):
    """No `epg_channel` and no `programme`: the list still renders, and every row points at the
    mapping screen instead of a dead guide cell."""
    make_channel(db, "Solo catálogo", 1)
    make_channel(db, "Otro", 2)

    page = web.get("/").text

    assert "Solo catálogo" in page and "Otro" in page
    assert "guide-now" not in page
    assert page.count('class="guide-none hint"') == 2
    assert 'href="http://127.0.0.1/epg/mapping"' in page


def test_list_does_not_query_the_guide_once_per_row(web, db):
    """The guide name is loaded with the page, not lazily: one `epg_channel` query for five rows
    is the same cost as one for a single row."""
    now = datetime.now(UTC)
    guide = make_guide(db, "Guía Uno", "g1")
    for number in range(1, 6):
        channel = make_channel(db, f"Canal {number}", number)
        channel.guide_channel_id = guide.id
    add_programme(db, guide, "Programa", now, now + timedelta(hours=1))
    db.commit()

    engine = db.get_bind()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        web.get("/?text=Canal 1")
        one_row = [s for s in statements if "FROM epg_channel" in s]
        statements.clear()
        web.get("/")
        five_rows = [s for s in statements if "FROM epg_channel" in s]
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(one_row) == 1
    assert len(five_rows) == len(one_row)


def test_grid_shows_madrid_times_and_marks_what_is_on_now(web, db):
    channel = make_channel(db, "Uno")
    guide = make_guide(db, "Guía Uno", "g1")
    channel.guide_channel_id = guide.id
    db.commit()
    now = datetime.now(UTC)
    current = add_programme(
        db, guide, "En emisión", now - timedelta(minutes=20), now + timedelta(minutes=40)
    )
    add_programme(db, guide, "Después", now + timedelta(minutes=40), now + timedelta(minutes=100))
    add_programme(db, guide, "Ya terminó", now - timedelta(hours=2), now - timedelta(hours=1))

    response = web.get(f"/channels/{channel.id}/guide")
    page = response.text

    assert response.status_code == 200
    assert "En emisión" in page and "Después" in page
    assert "Ya terminó" not in page
    expected = f"{current.start.astimezone(MADRID):%H:%M}–{current.stop.astimezone(MADRID):%H:%M}"
    assert expected in page
    marked = page[page.index('<tr class="grid-current">') : page.index("Después")]
    assert "En emisión" in marked
    assert "badge-now" in marked


def test_the_grid_gives_the_description_room_to_wrap(web, db):
    """A synopsis is prose, and it must reach the page whole and in a cell that can wrap.

    What makes it multi-line is `.channels.guide .message` in style.css, which lifts the
    `white-space: nowrap` every other cell in this table keeps. A test cannot measure rendering, so
    it pins the two things that make wrapping possible: the class the rule hooks onto and the
    untouched text. A description trimmed in the template, or a cell that loses its class, would
    silently go back to one clipped line.
    """
    channel = make_channel(db, "Uno")
    guide = make_guide(db, "Guía Uno", "g1")
    channel.guide_channel_id = guide.id
    db.commit()
    now = datetime.now(UTC)
    synopsis = (
        "Un resumen largo, con más palabras de las que caben en una sola línea de la columna, "
        "que tiene que poder partirse en varias.\n"
        "Y un segundo párrafo, porque la fuente también los manda así."
    )
    add_programme(
        db,
        guide,
        "Programa con sinopsis",
        now,
        now + timedelta(hours=1),
        description=synopsis,
    )

    page = web.get(f"/channels/{channel.id}/guide").text

    assert 'class="message"' in page
    assert synopsis in page
    # The paragraph break is the source's, and `pre-line` is what keeps it without escaping to HTML.
    assert "\nY un segundo párrafo" in page


def test_detail_links_to_the_guide(web, db):
    channel = make_channel(db, "Uno")

    page = web.get(f"/channels/{channel.id}").text

    assert f'href="http://127.0.0.1/channels/{channel.id}/guide"' in page


def test_grid_of_a_mapped_channel_with_nothing_in_the_window_says_so(web, db):
    channel = make_channel(db, "Uno")
    guide = make_guide(db, "Guía Uno", "g1")
    channel.guide_channel_id = guide.id
    db.commit()

    response = web.get(f"/channels/{channel.id}/guide")

    assert response.status_code == 200
    assert "Sin guía para este canal" in response.text
    assert "no hay programas en las próximas 24 horas" in response.text


def test_mapping_shows_the_rejected_candidate_and_stores_a_decision(web, db):
    channel = make_channel(db, "TV4 HD [PL]")
    make_guide(db, "M+ LALIGA TV 4", "d4")
    target = make_guide(db, "TV4", "t4")

    page = web.get("/epg/mapping").text

    assert "TV4 HD [PL]" in page
    assert "M+ LALIGA TV 4" in page
    # The evidence a person decides on: the tokens that rejected the near miss.
    assert "m laliga tv 4" in page
    assert "sobra" in page

    response = web.post(
        f"/epg/mapping/{channel.id}", data={"guide_id": str(target.id)}, follow_redirects=False
    )

    assert response.status_code == 303
    db.expire_all()
    assert db.get(models.Channel, channel.id).guide_channel_id == target.id
    alias = db.scalar(select(EpgAlias).where(EpgAlias.channel_id == channel.id))
    assert alias.epg_channel_id == target.id
    assert count_checks(db) == 0


def test_a_manual_decision_is_listed_and_can_be_undone(web, db):
    channel = make_channel(db, "TV4 HD [PL]")
    guide = make_guide(db, "TV4", "t4")
    web.post(f"/epg/mapping/{channel.id}", data={"guide_id": str(guide.id)})

    page = web.get("/epg/mapping").text
    assert "Decisiones manuales" in page
    assert "TV4 HD [PL]" in page and "TV4" in page

    response = web.post(f"/epg/mapping/{channel.id}/clear", follow_redirects=False)

    assert response.status_code == 303
    db.expire_all()
    assert db.get(models.Channel, channel.id).guide_channel_id is None
    assert db.scalar(select(EpgAlias).where(EpgAlias.channel_id == channel.id)) is None
    assert count_checks(db) == 0


def test_the_guide_search_narrows_the_names(web, db):
    make_guide(db, "TV4", "t4")
    make_guide(db, "Antena 3", "a3")

    page = web.get("/epg/mapping/guides?q=tv4").text

    assert "TV4" in page
    assert "Antena 3" not in page


def test_a_container_uid_with_no_passwd_entry_still_stores_a_mapping(
    web, db, db_factory, monkeypatch
):
    """The deployed container's condition, reproduced: uid 1000 has no passwd entry and the
    process carries no `USER`, `LOGNAME` or `ACESTREAM_SUITE_EPG_ACTOR`, so `getpass.getuser()` raises
    `KeyError: 'getpwuid(): uid not found: 1000'`. `HOME` is cleared too, because the container has
    none and nothing in this path may need one.

    Before the guard this was an HTTP 500 on `POST /epg/mapping/{id}` — the alias was never
    written — while `clear`, which names no actor, answered 303. The assertion that catches it is
    the POST's own status code, not a unit test on the name resolution.
    """
    for name in ("USER", "LOGNAME", "HOME", "ACESTREAM_SUITE_EPG_ACTOR"):
        monkeypatch.delenv(name, raising=False)

    def no_passwd_entry() -> str:
        raise KeyError("getpwuid(): uid not found: 1000")

    monkeypatch.setattr(getpass, "getuser", no_passwd_entry)

    # (a) The name resolution answers instead of raising...
    assert epg_matching.actor_name() == epg_matching.ACTOR_FALLBACK

    channel = make_channel(db, "TV4 HD [PL]")
    guide = make_guide(db, "TV4", "t4")

    # (b) ...and the write the mapping screen makes answers 303 and stores the alias.
    response = web.post(
        f"/epg/mapping/{channel.id}", data={"guide_id": str(guide.id)}, follow_redirects=False
    )

    assert response.status_code == 303
    db.expire_all()
    assert db.get(models.Channel, channel.id).guide_channel_id == guide.id
    alias = db.scalar(select(EpgAlias).where(EpgAlias.channel_id == channel.id))
    assert alias.epg_channel_id == guide.id
    assert alias.created_by == epg_matching.ACTOR_FALLBACK

    # (c) `epg-map --set` names the actor through the same function, so the CLI is covered too.
    other = make_channel(db, "La 1", 1)
    assert main(["epg-map", "--set", str(other.id), str(guide.id)], session_factory=db_factory) == 0
    db.expire_all()
    cli_alias = db.scalar(select(EpgAlias).where(EpgAlias.channel_id == other.id))
    assert cli_alias.created_by == epg_matching.ACTOR_FALLBACK


def test_mapping_with_no_catalogue_keeps_its_empty_state(web):
    page = web.get("/epg/mapping").text

    assert "Todavía no hay canales en el catálogo." in page


def test_every_page_answers_with_an_empty_guide(web, db):
    """No `epg_channel` and no `programme`: the guide is empty, dead or half-loaded, and it is
    still a normal state for the catalogue, not a 500."""
    channel = make_channel(db, "Solo catálogo")

    assert "Solo catálogo" in web.get("/").text

    grid = web.get(f"/channels/{channel.id}/guide")
    assert grid.status_code == 200
    assert "Sin guía para este canal" in grid.text
    assert "no está asignado" in grid.text

    mapping = web.get("/epg/mapping")
    assert mapping.status_code == 200
    assert "Todavía no hay canales de guía" in mapping.text
    assert "epg-refresh" in mapping.text

    options = web.get("/epg/mapping/guides?q=tv")
    assert options.status_code == 200
    assert "Sin coincidencias" in options.text


def test_rendering_the_guide_pages_queues_no_check(web, db):
    channel = make_channel(db, "Uno")
    guide = make_guide(db, "Guía Uno", "g1")
    channel.guide_channel_id = guide.id
    db.commit()
    now = datetime.now(UTC)
    add_programme(db, guide, "Programa", now, now + timedelta(hours=1))

    assert web.get("/").status_code == 200
    assert web.get(f"/channels/{channel.id}/guide").status_code == 200
    assert web.get("/epg/mapping").status_code == 200
    assert web.get("/epg/mapping/guides?q=gu").status_code == 200

    # THE assertion of this file: a rendered page is a read, never a queued live check.
    assert count_checks(db) == 0
