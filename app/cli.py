"""The maintenance commands: what is done to the catalog from a shell, not from a page.

Each command is a function that returns an exit code, so a cron job or a host script reads the
outcome the way it reads any other program's. The sentences are Spanish because the reader is the
person who typed the command.

    python -m app.cli seed /tmp/export.json
    curl -s http://127.0.0.1:8083/export | python -m app.cli seed -
    docker exec acestream-suite python3 -m app.cli epg-refresh
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import make_engine, make_session_factory
from app.services import epg, epg_matching, exchange
from app.services.errors import NotFoundError, ServiceError
from app.services.importer import ImportResult, Reason

# THE POINT OF THE SEED COMMAND IS THAT IT IMPORTS NOTHING FROM `app.web`. `start_checks` lives in
# the import form and in the check routes, so a process that never loads the web layer cannot reach
# the queue: seeding the live service's 581 channels through the form would open 581 engine sessions
# on the engine and the tunnel at once, and the guide's task record in `odd/tasks/` keeps that
# queue to the one deployment on 8083.

# A whole catalogue fed in would bury the summary under one line per rejected channel.
MAX_SHOWN_REJECTIONS = 5

# How many unmapped channels the mapping report lists before it stops.
REPORT_LIMIT = 20

REASON_ES = {
    Reason.NO_LINK: "sin Content ID",
    Reason.INVALID: "datos inválidos",
    Reason.REPEATED: "el mismo Content ID ya había salido antes en el documento",
    Reason.EXISTING: "ya estaba en el catálogo",
}

# The matcher's names for its own scores, as the report has to say them.
MATCH_KIND_ES = {
    "exact": "exacta",
    "containment": "continencia",
    "fuzzy": "difusa",
    "below": "débil",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="acestream-suite: tareas de mantenimiento."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    seed_command = commands.add_parser(
        "seed",
        help=(
            "crea los canales de un export de acestream-suite, sin programar ninguna comprobación"
        ),
    )
    seed_command.add_argument(
        "path", help="el documento exportado, o - para leerlo de la entrada estándar"
    )
    seed_command.add_argument(
        "--dry-run", action="store_true", help="leer el documento e informar, sin escribir nada"
    )
    refresh = commands.add_parser(
        "epg-refresh",
        help="descarga las fuentes XMLTV y guarda la guía de las próximas horas",
    )
    refresh.add_argument(
        "--source",
        action="append",
        metavar="URL",
        help="una fuente a refrescar; se puede repetir, y sin ninguna se usan las configuradas",
    )
    refresh.add_argument(
        "--dry-run", action="store_true", help="descargar y contar, sin escribir nada"
    )
    mapping = commands.add_parser(
        "epg-map",
        help="mira o cambia a qué canal de la guía apunta cada canal del catálogo",
    )
    # One of these at a time: the command either looks or decides, never both.
    decision = mapping.add_mutually_exclusive_group()
    decision.add_argument(
        "--auto",
        action="store_true",
        help="aplica el matcher a todos los canales sin guía y sin decisión manual",
    )
    decision.add_argument(
        "--report",
        type=int,
        nargs="?",
        const=REPORT_LIMIT,
        metavar="N",
        help=(
            f"informa del estado y de los primeros N canales sin guía (por defecto {REPORT_LIMIT})"
        ),
    )
    decision.add_argument(
        "--set",
        type=int,
        nargs=2,
        metavar=("CANAL", "GUIA"),
        help="fija a mano la guía de un canal, guardado como decisión manual",
    )
    decision.add_argument(
        "--clear",
        type=int,
        metavar="CANAL",
        help="borra la decisión manual de un canal y lo deja sin guía",
    )
    return parser.parse_args(argv)


def read_document(path: str) -> str:
    """The exported document: the file at `path`, or the standard input when it is `-`.

    UTF-8 and a byte order mark tolerated, which is the reading the import form does too, so an
    export that form produced goes back in unchanged.
    """
    if path == "-":
        return sys.stdin.buffer.read().decode("utf-8-sig")
    return Path(path).read_bytes().decode("utf-8-sig")


def _factory_for(
    session_factory: sessionmaker[Session] | None,
) -> sessionmaker[Session]:
    """The factory the command was handed, or this process's own over `DATABASE_URL`.

    Tests and host scripts pass one in; a plain `python -m app.cli` builds it the way `app.main`
    does, from the same settings and the same engine.
    """
    if session_factory is None:
        return make_session_factory(make_engine())
    return session_factory


def seed(
    text: str, *, dry_run: bool = False, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Writes the channels of an acestream-suite export into this catalog; returns the exit code.

    Channels, plus the categories the document names, and no check: queueing is the caller's, and
    this caller has no way to ask for one.
    """
    if not exchange.is_export(text):
        print(
            'No es un export de acestream-suite: se espera un objeto JSON con "format": '
            '"acestream-suite".',
            file=sys.stderr,
        )
        return 1

    if dry_run:
        try:
            entries = exchange.parse_export(text)
        except ServiceError as exc:
            print(f"El documento no sirve: {exc}", file=sys.stderr)
            return 1
        print(f"Canales en el documento: {len(entries)}")
        print("Simulación: no se ha escrito nada.")
        return 0

    with _factory_for(session_factory)() as session:
        try:
            # Commits per channel inside, so an import that fails half way keeps what it wrote,
            # the same way the import form behaves.
            result = exchange.import_export(session, text)
        except ServiceError as exc:
            print(f"No se pudo importar: {exc}", file=sys.stderr)
            return 1

    report_import(result)
    return 0


def report_import(result: ImportResult) -> None:
    """The summary the person reads: what was created, and why the rest was not."""
    rejected = result.duplicates + result.invalid
    print(f"Creados: {len(result.created)}")
    print(f"Rechazados: {len(rejected)}")
    for item in rejected[:MAX_SHOWN_REJECTIONS]:
        detail = f" ({item.detail})" if item.detail else ""
        print(f"  canal {item.line}: {REASON_ES.get(item.reason, item.reason.value)}{detail}")
    if len(rejected) > MAX_SHOWN_REJECTIONS:
        print(f"  …y {len(rejected) - MAX_SHOWN_REJECTIONS} más")
    print("No se ha programado ninguna comprobación.")


def epg_refresh(
    sources: list[str] | None = None,
    *,
    dry_run: bool = False,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Refreshes the guide; returns the exit code.

    Non-zero only when every source failed. One dead provider out of four still leaves a guide
    worth serving, and a cron job reads this code: it must not write a failure notice every twelve
    hours because a community feed moved.
    """
    with _factory_for(session_factory)() as session:
        report = epg.refresh(session, sources=sources, dry_run=dry_run)
    return report_refresh(report, dry_run=dry_run)


def report_refresh(report: epg.Report, *, dry_run: bool = False) -> int:
    """The summary for the person, and the exit code: the same shape `report_import` has."""
    if not report.sources:
        print(f"No hay fuentes configuradas ({epg.SOURCES_ENV} o --source).")
        return 0
    print(f"Fuentes: {len(report.sources)}")
    for item in report.sources:
        if item.error:
            print(f"  {item.source}: no ha respondido ({item.error})")
            continue
        print(
            f"  {item.source}: {item.channels} canales, {item.parsed} programas leídos, "
            f"{item.windowed} en la ventana, {item.stored} guardados, {item.pruned} borrados"
        )
    if dry_run:
        print("Simulación: no se ha escrito nada.")
    else:
        print(f"Canales retirados: {report.channels_removed}")
    if report.fatal:
        print("Ninguna fuente ha respondido: no hay guía que guardar.", file=sys.stderr)
        return 1
    return 0


def report_mapping(report: epg_matching.MappingReport) -> None:
    """The mapping state, and for the best unmapped titles what the matcher saw."""
    print(f"Canales del catálogo: {report.total}")
    print(f"Con guía: {report.mapped}")
    print(f"Sin guía: {report.unmapped_total}")
    print(f"Decisiones manuales: {report.manual}")
    if not report.unmapped:
        return
    print(f"Sin guía (primeros {len(report.unmapped)} de {report.unmapped_total}):")
    for item in report.unmapped:
        print(f"  [{item.channel_id}] {item.title}")
        if item.manual:
            print("      decisión manual, el matcher no propone nada")
            continue
        if not item.candidates:
            print("      propuestas: ninguna")
            continue
        proposals = ", ".join(
            f"{candidate.name} ({candidate.score:.2f} {MATCH_KIND_ES[candidate.kind]})"
            for candidate in item.candidates
        )
        print(f"      propuestas: {proposals}")
        # The evidence behind the rejection: the tokens that stopped the best candidate, e.g.
        # `'tv 4' ⊄ 'm laliga tv 4': sobra 'm laliga'`.
        if item.candidates[0].leftover:
            print(f"      descartado: {item.candidates[0].leftover}")


def report_auto(report: epg_matching.AutoReport) -> None:
    print(f"Canales sin guía examinados: {report.examined}")
    print(f"Asignados por el matcher: {report.mapped}")
    print(f"Sin confianza suficiente: {report.examined - report.mapped}")
    print(f"Dejados a una decisión manual: {report.human}")


def _map_auto(session: Session) -> int:
    report_auto(epg_matching.auto_map(session))
    return 0


def _map_decided(session: Session, channel_id: int, guide_id: int) -> int:
    """A person's decision, which from now on the matcher leaves alone."""
    try:
        epg_matching.set_alias(session, channel_id, guide_id)
    except NotFoundError:
        print("No se pudo asignar: el canal o la guía no existen.", file=sys.stderr)
        return 1
    print(f"Canal {channel_id} -> guía {guide_id}: decisión manual guardada.")
    return 0


def _map_forgotten(session: Session, channel_id: int) -> int:
    """Drops a decision, so the channel goes back to having no guide until something maps it."""
    try:
        had_alias = epg_matching.clear_alias(session, channel_id)
    except NotFoundError:
        print(f"No existe el canal {channel_id}.", file=sys.stderr)
        return 1
    if had_alias:
        print(f"Decisión manual del canal {channel_id} borrada; queda sin guía.")
    else:
        print(f"El canal {channel_id} no tenía decisión manual; queda sin guía.")
    return 0


def _map_report(session: Session, limit: int) -> int:
    report_mapping(epg_matching.report(session, limit=limit))
    return 0


def epg_map(
    args: argparse.Namespace, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """The mapping command: report, auto-map, or one manual decision; returns the exit code."""
    with _factory_for(session_factory)() as session:
        if args.auto:
            return _map_auto(session)
        if args.set is not None:
            return _map_decided(session, *args.set)
        if args.clear is not None:
            return _map_forgotten(session, args.clear)
        return _map_report(session, args.report or REPORT_LIMIT)


def _seed_command(args: argparse.Namespace, session_factory: sessionmaker[Session] | None) -> int:
    """The document read from where the arguments said, then handed to `seed`."""
    try:
        text = read_document(args.path)
    except (OSError, UnicodeError) as exc:
        print(f"No se pudo leer {args.path}: {exc}", file=sys.stderr)
        return 1
    return seed(text, dry_run=args.dry_run, session_factory=session_factory)


def _refresh_command(
    args: argparse.Namespace, session_factory: sessionmaker[Session] | None
) -> int:
    return epg_refresh(args.source, dry_run=args.dry_run, session_factory=session_factory)


def _map_command(args: argparse.Namespace, session_factory: sessionmaker[Session] | None) -> int:
    return epg_map(args, session_factory=session_factory)


# `parse_args` refuses any command that is not one of these, so the lookup always finds its row.
HANDLERS = {
    "seed": _seed_command,
    "epg-refresh": _refresh_command,
    "epg-map": _map_command,
}


def main(
    argv: list[str] | None = None, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Runs the command the arguments name; returns its exit code."""
    args = parse_args(argv)
    return HANDLERS[args.command](args, session_factory)


def run() -> None:  # pragma: no cover - process entry point
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
