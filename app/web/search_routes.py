"""Buscador de canales contra acesearch, y lo que se puede hacer con lo que encuentra.

El buscador no es el catálogo: devuelve lo que el índice del motor conoce, con su nombre y su hash, y
aquí se decide qué hacer con ello — añadirlo al catálogo (con su comprobación en segundo plano, como
cualquier alta) o probar a reproducirlo antes, que es lo único que dice si el enlace está vivo.
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
# Un POST puede marcar media pantalla; el mismo tope que una importación de enlaces.
MAX_PICKS = 60
# Lo que viaja en cada casilla: `Título | acestream://hash`, que es una línea del importador.
LINK_PREFIX = "acestream://"


def _render(
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


def _known_checks(session, content_ids: list[str]) -> dict[str, object]:
    """La última comprobación de los hashes que ya están en el catálogo, por Content ID.

    El índice de acesearch no publica pares, así que sin abrir sesión lo único que hay es lo que
    midió *Comprobar* la última vez. Se enseña con su fecha porque es una foto, no un dato de ahora.
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


@router.get("", response_class=HTMLResponse, name="search_page")
def search_page(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    q: str = "",
    limit: int = 60,
):
    """La página de búsqueda: con `q` busca, sin `q` solo se dibuja."""
    query = " ".join(q.split())
    if not query:
        return _render(request, session, values={"limit": limit})
    try:
        with SearchClient(settings.resolved_search_url, timeout=settings.engine_timeout) as client:
            hits = client.search(query, limit=limit)
    except SearchError as exc:
        return _render(
            request,
            session,
            query=query,
            problem=f"No se pudo buscar: {exc}",
            values={"limit": limit},
        )
    known = _known_checks(session, [hit.content_id for hit in hits])
    return _render(
        request, session, query=query, hits=hits, values={"limit": limit}, known=known
    )


def _picks_to_links(picks: list[str]) -> str:
    """Una línea por casilla marcada, en el formato que ya lee el importador."""
    lines = []
    for pick in picks[:MAX_PICKS]:
        line = " ".join(str(pick or "").split())
        if LINK_PREFIX in line and len(line) <= 300:
            lines.append(line)
    return "\n".join(lines)


@router.post("/peers", response_class=HTMLResponse, name="search_peers")
def search_peers(
    request: Request,
    client: EngineDep,
    settings: SettingsDep,
    content_id: FormField = "",
):
    """Mide los pares de ese hash de verdad: abre una sesión en el motor y la cierra.

    Es la comprobación de *Comprobar*, sin guardar nada: el buscador no es el catálogo. Cuesta unos
    segundos y una sesión del motor, por eso es un botón por fila y no una columna.
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
    """Los resultados marcados, al catálogo: la misma alta que una importación de enlaces."""
    values = {"category_id": category_id, "new_category": new_category, "language": language,
              "country": country}
    text = _picks_to_links(pick or [])
    errors = {}
    if not text:
        errors["pick"] = "Marca al menos un resultado."
    chosen = int(category_id) if category_id.isdigit() else None
    if new_category.strip():
        chosen = None  # el nombre escrito gana, como en el formulario de canal
    elif category_id and (chosen is None or session.get(Category, chosen) is None):
        errors["category_id"] = "La categoría elegida no existe."
    for name, normalize in (("language", normalize_language), ("country", normalize_country)):
        try:
            normalize(values[name])
        except ValidationError:
            errors[name] = "Código desconocido."
    if errors:
        return _render(request, session, query=query, errors=errors, values=values)

    try:
        result = importer.import_links(
            session,
            text,
            category_id=resolve_category(session, chosen, new_category.strip() or None),
            language=language,
            country=country,
        )
    except ServiceError as exc:
        return _render(request, session, query=query, errors={"pick": f"No se pudo añadir: {exc}"})

    # Un canal nuevo se verifica en segundo plano, como cualquier alta; si el engine no está, el alta
    # se queda hecha y solo esperan las comprobaciones.
    created = [channel.id for channel in result.created]
    if created and probe() is None:
        return _render(request, session, query=query, result=result, engine_down=True)
    if created:
        start_checks(
            runner,
            created,
            session_factory,
            engine_factory,
            settings,
            screenshots=Screenshots.IF_MISSING,
        )
    return _render(request, session, query=query, result=result)


@router.post("/play", response_class=HTMLResponse, name="search_play")
def search_play(
    request: Request,
    client: EngineDep,
    settings: SettingsDep,
    content_id: FormField = "",
    name: FormField = "",
):
    """Arranca ese enlace en el engine y devuelve la URL (o abre VLC), sin darlo de alta.

    Es la prueba que vale: el índice puede tener un enlace muerto, y esto abre una sesión real.
    """
    stream = None
    try:
        stream = player.open_in_player(
            client, content_id, title=name or None, vlc_path=settings.vlc_path
        )
    except player.VlcNotFoundError:
        error = "No se encontró VLC. Instálalo o indica la ruta de vlc.exe en Ajustes."
    except ContentNotFoundError:
        error = "El engine no pudo cargar este Content ID: puede que el enlace haya muerto."
    except EngineError:
        error = (
            f"El engine de Ace Stream no responde en {settings.engine_url}. "
            "¿Está abierto Ace Stream?"
        )
    except player.PlayerError as exc:
        error = f"No se pudo abrir VLC: {exc}"
    else:
        error = None
    return templates.TemplateResponse(
        request,
        "_search_play.html",
        {
            "name": name,
            "error": error,
            # None en modo "vlc", donde la URL fue al reproductor y no al navegador.
            "playback_url": stream.playback_url if stream else None,
            "mode": PLAYBACK_MODE,
        },
        status_code=503 if error else 200,
    )
