"""acestream-suite's maintenance commands: they run before the app is serving.

Also runs from source with `python -m app.cli ...`. Messages are for the person who runs the
command on the host, so they are in Spanish.

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

# THE POINT OF THIS COMMAND IS AN ABSENCE OF IMPORTS: `start_checks` is called from
# `app/web/import_routes.py` (the import form) and from the check routes, and nothing in this
# module imports `app.web`, so the queue is not reachable from here. Seeding the 581 channels of
# the live service through the UI would put a second verification queue — 581 live engine
# sessions — on the .28 engine and the WireGuard tunnel, which the guide's task record in
# `odd/tasks/` forbids: `acestream-suite` on 8083 stays the only component that verifies anything.

# One line per rejected channel would bury the summary when a whole catalogue is fed in.
SHOWN_REASONS = 5

# How many unmapped channels the mapping report shows before it stops printing.
DEFAULT_REPORT = 20

_REASON_ES = {
    Reason.NO_LINK: "sin Content ID",
    Reason.INVALID: "datos inválidos",
    Reason.REPEATED: "el mismo Content ID ya había salido antes en el documento",
    Reason.EXISTING: "ya estaba en el catálogo",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="acestream-suite: tareas de mantenimiento."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser(
        "seed",
        help=(
            "crea los canales de un export de acestream-suite, sin programar ninguna comprobación"
        ),
    )
    seed.add_argument("path", help="el documento exportado, o - para leerlo de la entrada estándar")
    seed.add_argument(
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
    action = mapping.add_mutually_exclusive_group()
    action.add_argument(
        "--auto",
        action="store_true",
        help="aplica el matcher a todos los canales sin guía y sin decisión manual",
    )
    action.add_argument(
        "--report",
        type=int,
        nargs="?",
        const=DEFAULT_REPORT,
        metavar="N",
        help=(
            "informa del estado y de los primeros N canales sin guía "
            f"(por defecto {DEFAULT_REPORT})"
        ),
    )
    action.add_argument(
        "--set",
        type=int,
        nargs=2,
        metavar=("CANAL", "GUIA"),
        help="fija a mano la guía de un canal, guardado como decisión manual",
    )
    action.add_argument(
        "--clear",
        type=int,
        metavar="CANAL",
        help="borra la decisión manual de un canal y lo deja sin guía",
    )
    return parser.parse_args(argv)


def read_document(path: str) -> str:
    """The exported document: the file at `path`, or the standard input when it is `-`.

    UTF-8 with the byte order mark allowed, the same reading the import form does for an
    uploaded file, so an export written by that form can be fed back in unchanged.
    """
    if path == "-":
        return sys.stdin.buffer.read().decode("utf-8-sig")
    return Path(path).read_bytes().decode("utf-8-sig")


def seed(
    text: str, *, dry_run: bool = False, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Imports an acestream-suite export into this catalog; the returned int is the exit code.

    Only channels are written. `session_factory` defaults to the app's own session over
    `DATABASE_URL`, built the way `app.main` builds it.
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

    if session_factory is None:
        session_factory = make_session_factory(make_engine())
    with session_factory() as session:
        try:
            # One commit per channel inside; an import that fails half way keeps what it wrote,
            # exactly as the import form behaves.
            result = exchange.import_export(session, text)
        except ServiceError as exc:
            print(f"No se pudo importar: {exc}", file=sys.stderr)
            return 1

    report(result)
    return 0


def report(result: ImportResult) -> None:
    """The summary the person reads: what was created and why the rest was not."""
    rejected = result.duplicates + result.invalid
    print(f"Creados: {len(result.created)}")
    print(f"Rechazados: {len(rejected)}")
    for item in rejected[:SHOWN_REASONS]:
        detail = f" ({item.detail})" if item.detail else ""
        print(f"  canal {item.line}: {_REASON_ES.get(item.reason, item.reason.value)}{detail}")
    if len(rejected) > SHOWN_REASONS:
        print(f"  …y {len(rejected) - SHOWN_REASONS} más")
    print("No se ha programado ninguna comprobación.")


def epg_refresh(
    sources: list[str] | None = None,
    *,
    dry_run: bool = False,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Refreshes the guide; the returned int is the exit code.

    Non-zero only when every source failed. One dead provider of four leaves a guide that is worth
    serving, and the exit code is read by a cron job that must not write a failure notice every
    twelve hours because a community feed moved.
    """
    if session_factory is None:
        session_factory = make_session_factory(make_engine())
    with session_factory() as session:
        report = epg.refresh(session, sources=sources, dry_run=dry_run)
    return report_epg(report, dry_run=dry_run)


def report_epg(report: epg.Report, *, dry_run: bool = False) -> int:
    """The summary the person reads, and the exit code: the same shape as `report` for the seed."""
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


# The matcher's own names for its scores, in the language the person reading the report speaks.
_KIND_ES = {"exact": "exacta", "containment": "continencia", "fuzzy": "difusa", "below": "débil"}


def print_mapping(report: epg_matching.MappingReport) -> None:
    """The mapping state and the best unmapped titles to look at, with what the matcher saw."""
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
            f"{candidate.name} ({candidate.score:.2f} {_KIND_ES[candidate.kind]})"
            for candidate in item.candidates
        )
        print(f"      propuestas: {proposals}")
        # The evidence behind the rejection: the tokens that stopped the best candidate, e.g.
        # `'tv 4' ⊄ 'm laliga tv 4': sobra 'm laliga'`.
        if item.candidates[0].leftover:
            print(f"      descartado: {item.candidates[0].leftover}")


def print_auto(report: epg_matching.AutoReport) -> None:
    print(f"Canales sin guía examinados: {report.examined}")
    print(f"Asignados por el matcher: {report.mapped}")
    print(f"Sin confianza suficiente: {report.examined - report.mapped}")
    print(f"Dejados a una decisión manual: {report.human}")


def epg_map(
    args: argparse.Namespace, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """The mapping command: report, auto-map, or one manual decision; returns the exit code."""
    if session_factory is None:
        session_factory = make_session_factory(make_engine())
    with session_factory() as session:
        if args.auto:
            print_auto(epg_matching.auto_map(session))
            return 0
        if args.set is not None:
            channel_id, guide_id = args.set
            try:
                epg_matching.set_alias(session, channel_id, guide_id)
            except NotFoundError:
                print("No se pudo asignar: el canal o la guía no existen.", file=sys.stderr)
                return 1
            print(f"Canal {channel_id} -> guía {guide_id}: decisión manual guardada.")
            return 0
        if args.clear is not None:
            try:
                had_alias = epg_matching.clear_alias(session, args.clear)
            except NotFoundError:
                print(f"No existe el canal {args.clear}.", file=sys.stderr)
                return 1
            if had_alias:
                print(f"Decisión manual del canal {args.clear} borrada; queda sin guía.")
            else:
                print(f"El canal {args.clear} no tenía decisión manual; queda sin guía.")
            return 0
        print_mapping(epg_matching.report(session, limit=args.report or DEFAULT_REPORT))
        return 0


def main(
    argv: list[str] | None = None, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    args = parse_args(argv)
    if args.command == "epg-refresh":
        return epg_refresh(args.source, dry_run=args.dry_run, session_factory=session_factory)
    if args.command == "epg-map":
        return epg_map(args, session_factory=session_factory)
    try:
        text = read_document(args.path)
    except (OSError, UnicodeError) as exc:
        print(f"No se pudo leer {args.path}: {exc}", file=sys.stderr)
        return 1
    return seed(text, dry_run=args.dry_run, session_factory=session_factory)


def run() -> None:  # pragma: no cover - process entry point
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
