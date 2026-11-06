"""The seed command: it fills the catalog from an export, and queues nothing.

The engine-protecting assertion is the check table being empty at the end of every test here.
"""

import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.cli import main
from app.db.models import Check, EpgChannel, Programme, Resolution
from app.services import channels
from app.services.exchange import FORMAT, VERSION

ROOT = Path(__file__).resolve().parent.parent

HASH_A = "78266c15035d0ad8cbc58f821733931e1de434ab"
HASH_B = "ecc85e5d2b6088d2d307fd0b5de4f09f089e762f"


def document(*entries):
    return json.dumps(
        {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": "2026-10-13T00:00:00+00:00",
            "channels": list(entries),
        },
        ensure_ascii=False,
    )


def checks(db) -> int:
    return db.scalar(select(func.count()).select_from(Check))


def test_the_cli_module_never_pulls_in_the_web_layer():
    """The interpreter that runs a seed must have no `app.web` module in it at all.

    `start_checks` is imported by `app/web/import_routes.py`. A process that cannot import the web
    layer cannot reach the queue, which is the whole point of this command.
    """
    code = (
        "import sys, app.cli; "
        "loaded = [m for m in sys.modules if m.startswith('app.web')]; "
        "assert not loaded, loaded"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)

    assert done.returncode == 0, done.stderr


def test_seeding_an_export_keeps_the_channel_data_and_queues_no_check(
    db, db_factory, tmp_path, capsys
):
    source = tmp_path / "export.json"
    source.write_text(
        document(
            {
                "title": "DAZN1 FHD",
                "content_id": HASH_A,
                "category": "Deportes",
                "language": "es",
                "country": "ES",
                "resolution": "1080p",
            },
            {"title": "La 1", "content_id": HASH_B, "country": "ES"},
        ),
        encoding="utf-8",
    )

    assert main(["seed", str(source)], session_factory=db_factory) == 0

    first = channels.find_channel_by_content_id(db, HASH_A)
    assert (first.title, first.category.name, first.language, first.country) == (
        "DAZN1 FHD",
        "Deportes",
        "es",
        "ES",
    )
    assert first.resolution is Resolution.FULL_HD
    assert channels.find_channel_by_content_id(db, HASH_B).category is None
    assert "Creados: 2" in capsys.readouterr().out
    # THE test of this command. The same 581 channels through the import form would have queued
    # 581 live checks on the .28 engine and the tunnel; the duplicate must never put a second
    # queue there, and a row in this table is the only thing a check ever leaves behind.
    assert checks(db) == 0


def test_seeding_the_same_document_twice_creates_nothing_the_second_time(
    db, db_factory, tmp_path, capsys
):
    source = tmp_path / "export.json"
    source.write_text(document({"title": "Uno", "content_id": HASH_A}), encoding="utf-8")

    assert main(["seed", str(source)], session_factory=db_factory) == 0
    assert main(["seed", str(source)], session_factory=db_factory) == 0

    out = capsys.readouterr().out
    assert "Creados: 0" in out
    assert "Rechazados: 1" in out
    assert "ya estaba en el catálogo" in out
    assert len(channels.list_channels(db)) == 1
    assert checks(db) == 0


@pytest.mark.parametrize(
    "text",
    [
        f"acestream://{HASH_A}\n",  # a bare list of links: what the import form accepts
        "#EXTM3U\n#EXTINF:-1,DAZN1\n",  # an M3U list
        f'[{{"content_id": "{HASH_A}"}}]',  # JSON, but a list and not an export
        # truncated JSON: a valid prefix with no body
        '{"format": "acestream-suite", "version": 1, "channels":',
        '{"format": "other", "version": 1, "channels": []}',  # another program's document
    ],
)
def test_a_document_that_is_not_an_export_is_refused_and_writes_nothing(
    db, db_factory, tmp_path, capsys, text
):
    source = tmp_path / "not-an-export.json"
    source.write_text(text, encoding="utf-8")

    assert main(["seed", str(source)], session_factory=db_factory) != 0

    assert channels.list_channels(db) == []
    assert checks(db) == 0
    assert capsys.readouterr().err  # the reason is on stderr, not only the exit code


def test_a_missing_file_is_an_error_not_a_traceback(db, db_factory, capsys):
    assert main(["seed", "/no/existe.json"], session_factory=db_factory) == 1

    assert "No se pudo leer" in capsys.readouterr().err
    assert channels.list_channels(db) == []


def test_dry_run_reports_the_document_and_writes_nothing(db, db_factory, tmp_path, capsys):
    source = tmp_path / "export.json"
    source.write_text(
        document({"title": "Uno", "content_id": HASH_A}, {"content_id": HASH_B}), encoding="utf-8"
    )

    assert main(["seed", str(source), "--dry-run"], session_factory=db_factory) == 0

    out = capsys.readouterr().out
    assert "Canales en el documento: 2" in out
    assert "Simulación" in out
    assert channels.list_channels(db) == []
    assert checks(db) == 0


def test_a_broken_document_is_refused_by_dry_run_too(db, db_factory, tmp_path, capsys):
    source = tmp_path / "export.json"
    source.write_text('{"format": "acestream-suite", "channels": [', encoding="utf-8")

    assert main(["seed", str(source), "--dry-run"], session_factory=db_factory) == 1

    assert "El documento no sirve" in capsys.readouterr().err


def test_the_document_can_come_from_the_standard_input(db, db_factory, monkeypatch):
    # `docker exec -i acestream-suite python3 -m app.cli seed -` is how the host script feeds it.
    text = document({"title": "Uno", "content_id": HASH_A})
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode()), encoding="utf-8"))

    assert main(["seed", "-"], session_factory=db_factory) == 0

    assert channels.find_channel_by_content_id(db, HASH_A).title == "Uno"
    assert checks(db) == 0


# --- epg-refresh ----------------------------------------------------------------

EPG_SOURCE = "https://epg.test/TV.xml.gz"
EPG_DEAD = "https://epg.test/dead.xml.gz"


def epg_document() -> str:
    """A document with one channel and one programme that starts in five minutes.

    Built from the clock and not from a fixed date: the command reads the real `now`, and a
    document dated in the past is windowed out and stores nothing.
    """
    start = datetime.now(UTC) + timedelta(minutes=5)
    stop = start + timedelta(hours=1)
    return (
        '<?xml version="1.0" encoding="UTF-8"?><tv>'
        '<channel id="La1.TV"><display-name>La 1</display-name></channel>'
        f'<programme start="{start:%Y%m%d%H%M%S} +0000" stop="{stop:%Y%m%d%H%M%S} +0000"'
        ' channel="La1.TV"><title>Telediario</title></programme></tv>'
    )


def epg_rows(db_factory, model) -> list:
    with db_factory() as session:
        return list(session.scalars(select(model)))


def test_the_refresh_command_fetches_the_configured_sources_and_stores_the_guide(
    db_factory, monkeypatch, respx_mock, capsys
):
    respx_mock.get(EPG_SOURCE).respond(content=epg_document().encode())
    respx_mock.get(EPG_DEAD).respond(status_code=404)
    monkeypatch.setenv("ACESTREAM_SUITE_EPG_SOURCES", json.dumps([EPG_DEAD, EPG_SOURCE]))

    assert main(["epg-refresh"], session_factory=db_factory) == 0

    out = capsys.readouterr().out
    assert EPG_DEAD in out and "error" in out
    assert "1 canales" in out and "1 guardados" in out
    assert len(epg_rows(db_factory, Programme)) == 1
    assert checks(db_factory()) == 0


def test_the_refresh_command_takes_the_sources_given_in_the_arguments_instead(
    db_factory, monkeypatch, respx_mock, capsys
):
    # No route for the configured one: were it fetched, respx would fail this test instead.
    monkeypatch.setenv("ACESTREAM_SUITE_EPG_SOURCES", json.dumps(["https://epg.test/ignored.xml.gz"]))
    respx_mock.get(EPG_SOURCE).respond(content=epg_document().encode())

    assert main(["epg-refresh", "--source", EPG_SOURCE], session_factory=db_factory) == 0

    assert "ignored" not in capsys.readouterr().out
    assert len(epg_rows(db_factory, Programme)) == 1


def test_the_refresh_command_fails_only_when_no_source_at_all_answers(
    db_factory, monkeypatch, respx_mock, capsys
):
    respx_mock.get(EPG_DEAD).respond(status_code=500)
    monkeypatch.setenv("ACESTREAM_SUITE_EPG_SOURCES", json.dumps([EPG_DEAD]))

    assert main(["epg-refresh"], session_factory=db_factory) == 1

    assert "Ninguna fuente" in capsys.readouterr().err
    assert epg_rows(db_factory, Programme) == []


def test_the_refresh_command_dry_run_touches_nothing(db_factory, monkeypatch, respx_mock, capsys):
    respx_mock.get(EPG_SOURCE).respond(content=epg_document().encode())
    monkeypatch.setenv("ACESTREAM_SUITE_EPG_SOURCES", json.dumps([EPG_SOURCE]))

    assert main(["epg-refresh", "--dry-run"], session_factory=db_factory) == 0

    assert "Simulación" in capsys.readouterr().out
    assert epg_rows(db_factory, Programme) == []
    assert epg_rows(db_factory, EpgChannel) == []


def test_no_configured_sources_is_not_a_failure(db_factory, monkeypatch, capsys):
    monkeypatch.setenv("ACESTREAM_SUITE_EPG_SOURCES", "")

    assert main(["epg-refresh"], session_factory=db_factory) == 0

    assert "No hay fuentes" in capsys.readouterr().out
