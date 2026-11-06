"""Searching the engine's index, and the three things a person can do with what it finds.

The index is a second service, not the catalogue: it answers with names and hashes of channels
that, by definition, are not in this database yet. What this module decides is what happens next —
add them (which is an ordinary import, background check included), measure one for real, or try to
play it — plus the page that puts the results in front of somebody.

No measurement is ever invented here. The index publishes no peer count, so a row's peers are
whatever the catalogue's own last check said, shown with the date of that check; a channel nobody
has measured says so instead of showing a zero.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.acestream.content_id import InvalidContentIdError
from app.acestream.errors import ContentNotFoundError, EngineError, EngineUnavailableError
from app.config import PLAYBACK_MODE
from app.db.models import Category, Channel
from app.services import categories, checks, importer, player, verification
from app.services.catalog import Screenshots
from app.services.errors import ServiceError, ValidationError
from app.services.iso import normalize_country, normalize_language
from app.services.search import SearchClient, SearchError
from app.web.check_routes import start_checks
from app.web.deps import (
    CheckRunnerDep,
    EngineDep,
    EngineFactoryDep,
    EngineProbeDep,
    SessionDep,
    SessionFactoryDep,
    SettingsDep,
)
from app.web.forms import resolve_category
from app.web.templating import templates

router = APIRouter(prefix="/search")

FormField = Annotated[str, Form()]

# A POST can carry half a screen of marked rows, and this is the same ceiling an import of links
# gets: the form is a screen, not a bulk door.
MAX_PICKS = 60
# What one checkbox sends: `Title | acestream://hash`, which is a single line of an import.
LINK_PREFIX = "acestream://"
# A checkbox value is one line of text. Anything far longer was not made by this form.
_LINE_CEILING = 300

# The two ISO codes the add form posts, each with the normaliser that judges it.
_CODE_FIELDS = (("language", normalize_language), ("country", normalize_country))


def _page(
    request: Request,
    session,
    *,
    query: str = "",
    hits=None,
    problem: str | None = None,
    result=None,
    values=None,
    errors=None,
    known=None,
    engine_down: bool = False,
) -> HTMLResponse:
    """The search page with everything the template reads, and 422 whenever it has errors to show.

    One builder for every answer of the three GET/POST paths, so a context key cannot exist on one
    of them and be missing on another; a missing key is a 500 when the template reads it.
    """
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "query": query,
            "hits": hits or [],
            "problem": problem,
            "result": result,
            "values": values or {},
            "errors": errors or {},
            "known": known or {},
            "engine_down": engine_down,
            "categories": categories.list_categories(session),
        },
        status_code=422 if errors else 200,
    )


def _catalogue_measurements(session, content_ids: list[str]) -> dict[str, object]:
    """The newest stored check of every hit that is already in the catalogue, by Content ID.

    The index publishes no peers, so without opening a session the only count that exists is the one
    *Comprobar* last measured. It travels with its date because it is a photograph of an earlier
    moment, not a reading taken now.
    """
    if not content_ids:
        return {}
    latest = checks.latest_checks(session)
    rows = session.execute(
        select(Channel.id, Channel.content_id).where(Channel.content_id.in_(content_ids))
    ).all()
    return {
        content_id: latest[channel_id] for channel_id, content_id in rows if channel_id in latest
    }


def _ask_index(settings, query: str, limit: int) -> tuple[list, str | None]:
    """The hits for this query, or the sentence that replaces them when the index did not answer."""
    try:
        with SearchClient(settings.resolved_search_url, timeout=settings.engine_timeout) as client:
            return client.search(query, limit=limit), None
    except SearchError as exc:
        return [], f"No se pudo buscar: {exc}"


@router.get("", response_class=HTMLResponse, name="search_page")
def search_page(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    q: str = "",
    limit: int = 60,
):
    """The search page: with `q` it searches the index, without `q` it only draws the form.

    A search that fails is not an error page — the box keeps what was typed and the reason is shown
    under it — because the usual cause is the URL in the settings, which is fixable right there.
    """
    query = " ".join(q.split())
    values = {"limit": limit}
    if not query:
        return _page(request, session, values=values)
    hits, problem = _ask_index(settings, query, limit)
    if problem:
        return _page(request, session, query=query, problem=problem, values=values)
    known = _catalogue_measurements(session, [hit.content_id for hit in hits])
    return _page(request, session, query=query, hits=hits, values=values, known=known)


def _picked_lines(picks: list[str]) -> str:
    """The marked rows as the text the importer already reads: one link per line.

    A pick that carries no link, or one far longer than a line, is not something this form sent and
    is dropped rather than passed on to be rejected one row at a time.
    """
    lines = []
    for pick in picks[:MAX_PICKS]:
        line = " ".join(str(pick or "").split())
        if LINK_PREFIX in line and len(line) <= _LINE_CEILING:
            lines.append(line)
    return "\n".join(lines)


def _category_pick(session, category_id: str, new_category: str) -> tuple[int | None, str | None]:
    """The category to store, and the complaint when the posted one names no category.

    A name typed by hand wins over the list, exactly as on the channel form: it is the more specific
    of the two answers, and `resolve_category` either creates it or reuses the one already called
    that. What is refused is a posted id with no row behind it — the catalogue has no such category.
    """
    if new_category.strip():
        return None, None
    posted = int(category_id) if category_id.isdigit() else None
    unknown = category_id and (posted is None or session.get(Category, posted) is None)
    return posted, "La categoría elegida no existe." if unknown else None


def _code_problems(values: dict[str, str]) -> dict[str, str]:
    """The language and country fields that are not codes the catalogue knows."""
    problems = {}
    for field, normalize in _CODE_FIELDS:
        try:
            normalize(values[field])
        except ValidationError:
            problems[field] = "Código desconocido."
    return problems


def _queue_first_checks(
    created: list[int], runner, session_factory, engine_factory, settings, probe
) -> bool:
    """Start the background check of the new channels, and report whether the engine was there.

    A channel is checked from the page that created it, like any other creation, so an engine that
    does not answer must not cost the rows: the import stays written and only the checks wait.
    """
    if not created:
        return True
    if probe() is None:
        return False
    start_checks(
        runner,
        created,
        session_factory,
        engine_factory,
        settings,
        screenshots=Screenshots.IF_MISSING,
    )
    return True


@router.post("/add", response_class=HTMLResponse, name="search_add")
def search_add(
    request: Request,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    engine_factory: EngineFactoryDep,
    settings: SettingsDep,
    runner: CheckRunnerDep,
    probe: EngineProbeDep,
    query: FormField = "",
    pick: Annotated[list[str] | None, Form()] = None,
    category_id: FormField = "",
    new_category: FormField = "",
    language: FormField = "",
    country: FormField = "",
):
    """The marked results, added to the catalogue: the same import a pasted list of links gets."""
    values = {
        "category_id": category_id,
        "new_category": new_category,
        "language": language,
        "country": country,
    }
    text = _picked_lines(pick or [])
    chosen, category_problem = _category_pick(session, category_id, new_category)
    problems = {} if text else {"pick": "Marca al menos un resultado."}
    if category_problem:
        problems["category_id"] = category_problem
    problems |= _code_problems(values)
    if problems:
        return _page(request, session, query=query, errors=problems, values=values)

    try:
        result = importer.import_links(
            session,
            text,
            category_id=resolve_category(session, chosen, new_category.strip() or None),
            language=language,
            country=country,
        )
    except ServiceError as exc:
        return _page(request, session, query=query, errors={"pick": f"No se pudo añadir: {exc}"})

    created = [channel.id for channel in result.created]
    reachable = _queue_first_checks(
        created, runner, session_factory, engine_factory, settings, probe
    )
    return _page(request, session, query=query, result=result, engine_down=not reachable)


@router.post("/peers", response_class=HTMLResponse, name="search_peers")
def search_peers(
    request: Request,
    client: EngineDep,
    settings: SettingsDep,
    content_id: FormField = "",
):
    """Measure the peers of that hash for real: open an engine session and close it again.

    It is *Comprobar* with nothing stored, because the index is not the catalogue. It costs a few
    seconds and one engine session, which is why it is a button on a row rather than a column.
    """
    result = None
    error = None
    try:
        result = verification.verify(client, content_id, settings.verification())
    except InvalidContentIdError:
        error = "Ese Content ID no es válido."
    except EngineUnavailableError:
        error = f"El engine de Ace Stream no responde en {settings.engine_url}."
    return templates.TemplateResponse(
        request,
        "_search_peers.html",
        {"result": result, "error": error},
        status_code=503 if error else 200,
    )


def _start_playback(client, settings, content_id: str, name: str):
    """Start that link in whatever plays it, with the sentence to show when it does not.

    A missing VLC, a dead link and an engine that is not there are three different fixes for the
    person reading the page, so each one keeps its own message instead of one "no se pudo abrir".
    """
    try:
        stream = player.open_in_player(
            client, content_id, title=name or None, vlc_path=settings.vlc_path
        )
    except player.VlcNotFoundError:
        return None, "No se encontró VLC. Instálalo o indica la ruta de vlc.exe en Ajustes."
    except ContentNotFoundError:
        return None, "El engine no pudo cargar este Content ID: puede que el enlace haya muerto."
    except EngineError:
        return None, (
            f"El engine de Ace Stream no responde en {settings.engine_url}. "
            "¿Está abierto Ace Stream?"
        )
    except player.PlayerError as exc:
        return None, f"No se pudo abrir VLC: {exc}"
    return stream, None


@router.post("/play", response_class=HTMLResponse, name="search_play")
def search_play(
    request: Request,
    client: EngineDep,
    settings: SettingsDep,
    content_id: FormField = "",
    name: FormField = "",
):
    """Play that link in the engine and hand the URL over, without adding it to the catalogue.

    This is the only test that really answers the question: the index may well list a link that is
    dead, and opening a session is what tells the two apart.
    """
    stream, error = _start_playback(client, settings, content_id, name)
    return templates.TemplateResponse(
        request,
        "_search_play.html",
        {
            "name": name,
            "error": error,
            # None in "vlc" mode, where the URL went to the player and never to the browser.
            "playback_url": stream.playback_url if stream else None,
            "mode": PLAYBACK_MODE,
        },
        status_code=503 if error else 200,
    )
