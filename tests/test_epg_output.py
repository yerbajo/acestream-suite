"""The two published artefacts: `/epg.xml` and `/epg.json`.

Both are reads of the store, and both are built by `app.services.epg_output` from the same join:
which catalogue channel points at which guide channel. The mapping is the input a playlist builder
needs; the XMLTV is the document a player consumes. `check_result` must stay empty — neither the
builders nor the endpoints queue a check.
"""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.db.models import Check, EpgChannel, Programme
from app.services import epg_output
from app.services.channels import create_channel

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SOURCE = "https://epg.test/TV.xml.gz"


def make_channel(db, title, number=0):
    return create_channel(db, title=title, content_id=f"{number:040x}")


def make_guide(db, name, source_channel_id, *, icon=None, source=SOURCE):
    guide = EpgChannel(
        source=source, source_channel_id=source_channel_id, name=name, icon=icon
    )
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


def mapped_channel(db, title, guide, number=0):
    channel = make_channel(db, title, number)
    channel.guide_channel_id = guide.id
    db.commit()
    return channel


def count_checks(db) -> int:
    return db.scalar(select(func.count()).select_from(Check))


def test_xmltv_names_the_guide_channel_and_keeps_the_unmapped_out(db):
    mapped = make_guide(db, "La 1", "La1.TV", icon="https://epg.test/la1.png")
    empty = make_guide(db, "Sin nada", "Empty.TV")
    mapped_channel(db, "La 1 HD", mapped, number=1)
    make_channel(db, "Canal sin guía", 2)
    add_programme(db, mapped, "Telediario", NOW, NOW + timedelta(hours=1))
    # A guide channel with a catalogue channel but no listing: it must not become an empty
    # `<channel>` in the document.
    mapped_channel(db, "Sin nada también", empty, number=3)

    document = epg_output.build_xmltv(db, now=NOW)

    assert '<channel id="La1.TV">' in document
    assert "<display-name>La 1</display-name>" in document
    assert '<icon src="https://epg.test/la1.png" />' in document
    assert "Empty.TV" not in document
    assert "Canal sin guía" not in document
    # Exactly one channel element, and no programme pretending to be one.
    root = ET.fromstring(document)
    assert [element.get("id") for element in root.findall("channel")] == ["La1.TV"]
    assert [element.get("channel") for element in root.findall("programme")] == ["La1.TV"]


def test_xmltv_publishes_a_shared_guide_once_and_deterministically(db):
    guide = make_guide(db, "DAZN 2", "DAZN2.TV")
    mapped_channel(db, "DAZN 2 FHD", guide, number=1)
    mapped_channel(db, "DAZN 2 720", guide, number=2)
    add_programme(db, guide, "Carrera", NOW + timedelta(hours=1), NOW + timedelta(hours=3))

    first = epg_output.build_xmltv(db, now=NOW)
    second = epg_output.build_xmltv(db, now=NOW)

    assert first == second
    root = ET.fromstring(first)
    assert len(root.findall("channel")) == 1
    assert len(root.findall("programme")) == 1


def test_xmltv_stamps_are_utc_and_xmltv_shaped(db):
    guide = make_guide(db, "La 1", "La1.TV")
    mapped_channel(db, "La 1", guide, number=1)
    # 14:00 Madrid in October is 12:00 UTC, so a naive local read of the store would be caught.
    madrid = datetime(2026, 10, 13, 14, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    add_programme(db, guide, "Programa", madrid, madrid + timedelta(minutes=45))

    document = epg_output.build_xmltv(db, now=NOW)

    assert 'start="20261013120000 +0000"' in document
    assert 'stop="20261013124500 +0000"' in document


def test_xmltv_keeps_only_the_window(db):
    guide = make_guide(db, "La 1", "La1.TV")
    mapped_channel(db, "La 1", guide, number=1)
    # Finished before now, on now, and beyond the 72 h horizon.
    add_programme(db, guide, "Ya pasó", NOW - timedelta(hours=2), NOW - timedelta(hours=1))
    add_programme(db, guide, "En emisión", NOW - timedelta(minutes=10), NOW + timedelta(minutes=20))
    add_programme(
        db, guide, "Demasiado lejos", NOW + timedelta(hours=80), NOW + timedelta(hours=81)
    )

    document = epg_output.build_xmltv(db, now=NOW)

    root = ET.fromstring(document)
    assert [element.findtext("title") for element in root.findall("programme")] == ["En emisión"]


def test_xmltv_escapes_markup_in_titles_and_descriptions(db):
    guide = make_guide(db, "A & B", "Amp.TV", icon="https://epg.test/i?a=1&b=2")
    mapped_channel(db, "Canal", guide, number=1)
    add_programme(db, guide, "Tom & Jerry <clásico>", NOW, NOW + timedelta(hours=1),
                  description="5 < 6 & 7 > 6")

    document = epg_output.build_xmltv(db, now=NOW)

    # The serializer escapes, so the raw document carries entities and not markup...
    assert "Tom &amp; Jerry &lt;clásico&gt;" in document
    assert "5 &lt; 6 &amp; 7 &gt; 6" in document
    # ...and a parser reads back the original text, which is the whole point.
    root = ET.fromstring(document)
    assert root.find("programme/title").text == "Tom & Jerry <clásico>"
    assert root.find("programme/desc").text == "5 < 6 & 7 > 6"
    assert root.find("channel/display-name").text == "A & B"
    assert root.find("channel/icon").get("src") == "https://epg.test/i?a=1&b=2"


def test_mapping_carries_content_id_guide_id_and_name(db):
    guide = make_guide(db, "M+ Deportes 2", "M+ Deportes 2")
    channel = mapped_channel(db, "M. Deportes 2 HD", guide, number=1)
    make_channel(db, "Canal sin guía", 2)

    document = epg_output.build_mapping(db)

    assert document["format"] == "acestream-suite-guide"
    assert document["version"] == 1
    entry = document["mapping"][channel.content_id]
    assert entry == {"guide_id": "M+ Deportes 2", "guide_name": "M+ Deportes 2"}
    assert len(document["mapping"]) == 1


def test_the_endpoints_publish_the_store_and_queue_no_check(web, db):
    guide = make_guide(db, "La 1", "La1.TV")
    channel = mapped_channel(db, "La 1", guide, number=1)
    now = datetime.now(UTC)
    add_programme(db, guide, "Telediario", now, now + timedelta(hours=1))

    xml = web.get("/epg.xml")
    assert xml.status_code == 200
    assert xml.headers["content-type"].startswith("application/xml")
    assert '<channel id="La1.TV">' in xml.text

    mapping = web.get("/epg.json")
    assert mapping.status_code == 200
    assert mapping.headers["content-type"].startswith("application/json")
    assert mapping.json()["mapping"][channel.content_id]["guide_id"] == "La1.TV"

    assert count_checks(db) == 0


def test_empty_store_still_answers_with_a_valid_document(web):
    xml = web.get("/epg.xml")
    assert xml.status_code == 200
    root = ET.fromstring(xml.text)
    assert root.tag == "tv"
    assert root.findall("channel") == []

    assert web.get("/epg.json").json()["mapping"] == {}
