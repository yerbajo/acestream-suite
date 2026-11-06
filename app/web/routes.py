"""The catalogue's pages: the list, the gallery, one channel, and the file that leaves with them.

Four things happen here and nowhere else. A page reads through `app.services.listing`, so the list
and the gallery are the same rows drawn twice. A write goes through `app.services.catalog`, so the
page obeys the same rules as the console. A check is queued and never run inside the request. And
every redirect that follows a POST is a 303, because a page that reposts itself when somebody
reloads it is how the same check gets asked for twice.

Captures are served from the rows that hold them, and only from inside the screenshots folder: a
stored path is a filename this app wrote, and one `../` left in an old row must not become a way to
download the rest of the data directory.
"""

import json
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from app.acestream.client import EngineClient, StreamSession
from app.acestream.errors import ContentNotFoundError, EngineError
from app.db.base import utcnow
from app.db.models import Channel, Check
from app.i18n import translate
from app.services import (
    catalog,
    categories,
    channels,
    checks,
    epg_guide,
    exchange,
    listing,
    player,
)
from app.services.batch import CheckRunner
from app.services.catalog import Screenshots
from app.services.errors import NotFoundError, ServiceError
from app.services.iso import country_name, language_name
from app.services.listing import DEFAULT_DESCENDING, ChannelQuery, SortKey
from app.services.screenshots import SCREENSHOTS_SUBDIR
from app.services.settings import AppSettings
from app.web.check_routes import start_checks
from app.web.deps import (
    CheckRunnerDep,
    EngineDep,
    EngineFactory,
    EngineFactoryDep,
    EngineProbe,
    EngineProbeDep,
    SessionDep,
    SessionFactoryDep,
    SettingsDep,
)
from app.web.flash import ENGINE_DOWN, ENGINE_DOWN_SAVED, flash
from app.web.forms import ChannelForm, resolve_category
from app.web.query import parse_query
from app.web.templating import language_in_force, templates

router = APIRouter()

FormField = Annotated[str, Form()]

# The gallery's last section. It is a heading, not a category: there is no row by that name.
NO_CATEGORY = "Sin categoría"


@dataclass(frozen=True)
class ChannelRow:
    """One channel as a row of the list or a tile of the gallery draws it."""

    channel: Channel
    check: Check | None  # the latest one: what the channel's state is now
    screenshot_url: str | None
    # What the guide has on right now, and what follows. None for a channel without a guide, which
    # is an empty cell and not a mistake.
    now_next: epg_guide.NowNext | None = None


@dataclass(frozen=True)
class CheckRow:
    """One entry of the history table, with the picture that check took."""

    check: Check
    screenshot_url: str | None


@dataclass(frozen=True)
class GallerySection:
    """A gallery heading and the tiles gathered under it."""

    title: str
    rows: list[ChannelRow]


def screenshot_url(request: Request, path: str | None) -> str | None:
    """The URL a stored capture is served at, or None when that path is not one this app wrote."""
    if not path:
        return None
    folder, _, name = path.partition("/")
    # One filename, directly inside the folder: no subdirectories and no way back out of it.
    if folder != SCREENSHOTS_SUBDIR or not name or "/" in name:
        return None
    return request.app.url_path_for("screenshots", path=name)


def _channel_or_404(session: Session, channel_id: int, language: str) -> Channel:
    """The channel the page asked for, with the same 404 every page gives for a missing one."""
    try:
        return channels.get_channel(session, channel_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=translate("Canal no encontrado", language)
        ) from exc


def _same_app_path(target: str) -> str | None:
    """`target` when it is a path inside this app, else None.

    The value arrives in a form field, so it is the browser's to choose: following it blindly would
    hand this app's origin to whoever wrote the field. A leading `//` and any backslash are refused
    along with the rest, because a browser reads both as "a different host".
    """
    parts = urlsplit(target)
    if (
        not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or parts.scheme
        or parts.netloc
    ):
        return None
    return target


def _see_other(request: Request, name: str, **path_params) -> RedirectResponse:
    """Send the browser to a page as a GET, so a reload of it repeats nothing that was written."""
    return RedirectResponse(request.url_for(name, **path_params), status_code=303)


def _form_page(
    request: Request,
    session: Session,
    form: ChannelForm,
    *,
    channel: Channel | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """The channel form as it was typed, with whatever sends the person back to it."""
    return templates.TemplateResponse(
        request,
        "channels/form.html",
        {
            "form": form,
            "channel": channel,
            "error": error,
            "categories": categories.list_categories(session),
        },
        # 422 marks a rejected form; htmx is configured to show it (see base.html).
        status_code=422 if form.errors or error else 200,
    )


def _header_links(request: Request, query: ChannelQuery) -> dict[str, str]:
    """What clicking each column head does: this column flips, the others start at their default."""
    links = {}
    for key in SortKey:
        descending = not query.descending if key == query.sort else DEFAULT_DESCENDING[key]
        links[key.value] = str(
            request.url.include_query_params(sort=key.value, dir="desc" if descending else "asc")
        )
    return links


def _rows_for(request: Request, session: Session, query: ChannelQuery) -> list[ChannelRow]:
    """The channels the query selects, each with the three things a cell shows about it.

    Three queries for the page whatever its length: the rows and their latest check, the newest
    picture of each, and the guide of the channels that have one. A lazy load per row would draw the
    same page at a cost that grows with the catalogue.
    """
    found = listing.search_channels(session, query)
    # The newest capture, which is usually older than the latest check: a check that found nothing
    # leaves the picture of the last one that did.
    shots = checks.latest_screenshots(session)
    mapped = {channel.guide_channel_id for channel, _ in found if channel.guide_channel_id}
    on_air = epg_guide.now_next(session, mapped)
    return [
        ChannelRow(
            channel=channel,
            check=check,
            screenshot_url=screenshot_url(request, shots.get(channel.id)),
            now_next=on_air.get(channel.guide_channel_id) if channel.guide_channel_id else None,
        )
        for channel, check in found
    ]


def _filter_options(session: Session) -> dict:
    """What the filter bar offers: every category, and only the codes the catalogue uses.

    A language or a country nobody has would be a filter that can only ever empty the page, so it is
    not offered. Both are sorted by the name a reader looks them up by, not by their code.
    """
    return {
        "categories": categories.list_categories(session),
        "languages": sorted(
            listing.used_languages(session), key=lambda code: language_name(code) or code
        ),
        "countries": sorted(
            listing.used_countries(session), key=lambda code: country_name(code) or code
        ),
    }


def _browse_context(request: Request, session: Session) -> dict:
    """What the list and the gallery share: the rows for this URL, and how they were chosen."""
    query = parse_query(request.query_params)
    rows = _rows_for(request, session, query)
    filtered = any(
        (
            query.text,
            query.category_id,
            query.language,
            query.country,
            query.status,
            query.resolution,
        )
    )
    return {
        "rows": rows,
        "query": query,
        "filtered": filtered,
        # "Nothing matches" and "nothing yet" are different pages, and only one of them offers to
        # clear filters nobody set.
        "catalog_empty": not rows and not filtered,
        **_filter_options(session),
    }


def _gallery_sections(rows: list[ChannelRow], language: str) -> list[GallerySection]:
    """One section per category, A-Z, with the uncategorised channels last.

    The rows keep the order they arrived in: choosing an order in the gallery means the same thing
    as clicking a column head in the list.
    """
    by_category: dict[str | None, list[ChannelRow]] = {}
    for row in rows:
        name = row.channel.category.name if row.channel.category else None
        by_category.setdefault(name, []).append(row)
    named = sorted((name for name in by_category if name is not None), key=str.casefold)
    sections = [GallerySection(name, by_category[name]) for name in named]
    if None in by_category:
        sections.append(GallerySection(translate(NO_CATEGORY, language), by_category[None]))
    return sections


def _after_save(request: Request, channel_id: int, *, engine_up: bool) -> Response:
    """Send the browser to the channel, saying so when no engine was there to verify it."""
    response = _see_other(request, "channel_detail", channel_id=channel_id)
    return response if engine_up else flash(response, ENGINE_DOWN_SAVED)


def _queued(
    response: Response,
    channel_ids: list[int],
    *,
    probe: EngineProbe,
    runner: CheckRunner,
    session_factory: sessionmaker[Session],
    engine_factory: EngineFactory,
    settings: AppSettings,
    screenshots: Screenshots,
) -> Response:
    """Queue those channels behind `response`, or let the missing engine be the answer.

    A page that says "Ace Stream is not open" is worth more than checks that fail for a reason
    nobody can see, and finding out costs one request.
    """
    if probe() is None:
        return flash(response, ENGINE_DOWN)
    start_checks(
        runner, channel_ids, session_factory, engine_factory, settings, screenshots=screenshots
    )
    return response


def _open_in_player(
    client: EngineClient, settings: AppSettings, channel: Channel, language: str
) -> tuple[StreamSession | None, str | None]:
    """Open that stream in the player, or say which of the four things went wrong.

    The four are kept apart on purpose: no VLC, a link the engine cannot load, an engine that is not
    running and a player that refused the URL need four different fixes, and one "no se pudo abrir"
    would hide which of them it was.
    """
    try:
        stream = player.open_in_player(
            client, channel.content_id, title=channel.title, vlc_path=settings.vlc_path
        )
    except player.VlcNotFoundError:
        return None, translate(
            "No se encontró VLC. Instálalo o indica la ruta de vlc.exe en Ajustes.", language
        )
    except ContentNotFoundError:
        return None, translate(
            "El engine no pudo cargar este Content ID: puede que el enlace haya muerto.", language
        )
    except EngineError:
        return None, translate(
            "El engine de Ace Stream no responde en {url}. ¿Está abierto Ace Stream?", language
        ).format(url=settings.engine_url)
    except player.PlayerError as exc:
        return None, translate("No se pudo abrir VLC: {detail}", language).format(detail=exc)
    return stream, None


@router.get("/", response_class=HTMLResponse, name="channel_list")
def channel_list(request: Request, session: SessionDep):
    """Every channel the filters select, one to a row, in the order the column heads ask for."""
    context = _browse_context(request, session)
    context["sort_links"] = _header_links(request, context["query"])
    return templates.TemplateResponse(request, "channels/list.html", context)


@router.get("/export", name="channel_export")
def channel_export(request: Request, session: SessionDep):
    """The channels the list is showing, as a document another install can import.

    The download takes the filters of the page it was asked from, so "export what I am looking at"
    needs no second screen to say it again.
    """
    query = parse_query(request.query_params)
    rows = [channel for channel, _ in listing.search_channels(session, query)]
    stamp = utcnow().astimezone().strftime("%Y%m%d-%H%M")
    return Response(
        json.dumps(exchange.export_channels(rows), ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="acestream-suite-{stamp}.json"'},
    )


@router.get("/gallery", response_class=HTMLResponse, name="channel_gallery")
def channel_gallery(request: Request, session: SessionDep):
    """The same rows as tiles, gathered under the category each one belongs to."""
    context = _browse_context(request, session)
    context["sections"] = _gallery_sections(context["rows"], language_in_force(request))
    return templates.TemplateResponse(request, "channels/gallery.html", context)


@router.get("/channels/new", response_class=HTMLResponse, name="channel_new")
def channel_new(request: Request, session: SessionDep):
    """The empty form."""
    return _form_page(request, session, ChannelForm())


@router.post("/channels/new", response_class=HTMLResponse, name="channel_create")
def channel_create(
    request: Request,
    session: SessionDep,
    client: EngineDep,
    probe: EngineProbeDep,
    settings: SettingsDep,
    title: FormField = "",
    link: FormField = "",
    category_id: FormField = "",
    new_category: FormField = "",
    language: FormField = "",
    country: FormField = "",
    resolution: FormField = "",
):
    """Add a channel, checked by the engine first when the engine answers.

    With the engine off the channel is saved anyway and left unverified. The link was somebody's
    decision and losing it would be the worse answer; the redirect carries the notice that says so.
    """
    form = ChannelForm(title, link, category_id, new_category, language, country, resolution)
    data = form.validate(session, language=language_in_force(request))
    if data is None:
        return _form_page(request, session, form)
    engine_up = probe() is not None
    try:
        # Blocks while the engine verifies it; FastAPI runs the handler in a worker thread.
        channel = catalog.add_channel(
            session,
            client,
            title=data.title,
            link=data.content_id,
            category_id=resolve_category(session, data.category_id, data.new_category),
            language=data.language,
            country=data.country,
            resolution=data.resolution,
            settings=settings.verification(),
            capture=settings.capture(),
            verify=engine_up,
        )
    except ServiceError as exc:
        return _form_page(
            request,
            session,
            form,
            error=translate("No se pudo guardar: {detail}", language_in_force(request)).format(
                detail=exc
            ),
        )
    return _after_save(request, channel.id, engine_up=engine_up)


@router.get("/channels/{channel_id}", response_class=HTMLResponse, name="channel_detail")
def channel_detail(request: Request, session: SessionDep, channel_id: int):
    """One channel: its facts, its newest picture, and every check ever filed against it."""
    channel = _channel_or_404(session, channel_id, language_in_force(request))
    history = [
        CheckRow(check, screenshot_url(request, check.screenshot_path))
        for check in checks.list_checks(session, channel_id)
    ]
    return templates.TemplateResponse(
        request,
        "channels/detail.html",
        {
            "channel": channel,
            "history": history,
            "latest": history[0].check if history else None,
            # The newest picture, even when a later check found the channel dead.
            "last_capture": next((row for row in history if row.screenshot_url), None),
        },
    )


@router.post("/channels/{channel_id}/check", name="channel_check")
def channel_check(
    request: Request,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    engine_factory: EngineFactoryDep,
    settings: SettingsDep,
    runner: CheckRunnerDep,
    probe: EngineProbeDep,
    channel_id: int,
    next: FormField = "",
):
    """Check one channel and go back to the page that asked for it.

    `next` is the filtered list the row's own button came from, so the person lands where they were;
    anything that is not a path of this app gives way to the channel's own page.
    """
    _channel_or_404(session, channel_id, language_in_force(request))
    target = _same_app_path(next)
    response = (
        RedirectResponse(target, status_code=303)
        if target is not None
        else _see_other(request, "channel_detail", channel_id=channel_id)
    )
    # In the background: the page shows the progress and reloads itself when it is done.
    return _queued(
        response,
        [channel_id],
        probe=probe,
        runner=runner,
        session_factory=session_factory,
        engine_factory=engine_factory,
        settings=settings,
        screenshots=Screenshots.NEVER,
    )


@router.post("/channels/{channel_id}/screenshot", name="channel_screenshot")
def channel_screenshot(
    request: Request,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    engine_factory: EngineFactoryDep,
    settings: SettingsDep,
    runner: CheckRunnerDep,
    probe: EngineProbeDep,
    channel_id: int,
):
    """Check the channel again and take a fresh picture of it, whatever it had before."""
    _channel_or_404(session, channel_id, language_in_force(request))
    return _queued(
        _see_other(request, "channel_detail", channel_id=channel_id),
        [channel_id],
        probe=probe,
        runner=runner,
        session_factory=session_factory,
        engine_factory=engine_factory,
        settings=settings,
        screenshots=Screenshots.ALWAYS,
    )


@router.post("/channels/{channel_id}/play", response_class=HTMLResponse, name="channel_play")
def channel_play(
    request: Request, session: SessionDep, client: EngineDep, settings: SettingsDep, channel_id: int
):
    """Open the channel in whatever plays it, and answer with a fragment for the page's notices."""
    channel = _channel_or_404(session, channel_id, language_in_force(request))
    stream, error = _open_in_player(client, settings, channel, language_in_force(request))
    return templates.TemplateResponse(
        request,
        "channels/_play_result.html",
        {
            "channel": channel,
            "error": error,
            # None in "vlc" mode, where the URL went to the player and never to the browser.
            "playback_url": stream.playback_url if stream else None,
        },
        # 503: the player or the engine is not there; htmx is configured to show it (see base.html).
        status_code=503 if error else 200,
    )


@router.get("/channels/{channel_id}/edit", response_class=HTMLResponse, name="channel_edit")
def channel_edit(request: Request, session: SessionDep, channel_id: int):
    """The form filled with the channel as it is stored."""
    channel = _channel_or_404(session, channel_id, language_in_force(request))
    return _form_page(request, session, ChannelForm.from_channel(channel), channel=channel)


@router.post("/channels/{channel_id}/edit", response_class=HTMLResponse, name="channel_update")
def channel_update(
    request: Request,
    session: SessionDep,
    client: EngineDep,
    probe: EngineProbeDep,
    settings: SettingsDep,
    channel_id: int,
    title: FormField = "",
    link: FormField = "",
    category_id: FormField = "",
    new_category: FormField = "",
    language: FormField = "",
    country: FormField = "",
    resolution: FormField = "",
):
    """Save an edit; only a new Content ID is worth asking the engine about.

    A title, a category, a language or a resolution are facts about the row, and the checks already
    stored still describe the same stream, so an edit of those never costs an engine request.
    """
    channel = _channel_or_404(session, channel_id, language_in_force(request))
    form = ChannelForm(title, link, category_id, new_category, language, country, resolution)
    data = form.validate(session, channel_id=channel_id, language=language_in_force(request))
    if data is None:
        return _form_page(request, session, form, channel=channel)
    engine_up = data.content_id == channel.content_id or probe() is not None
    try:
        catalog.edit_channel(
            session,
            client,
            channel_id,
            link=data.content_id,
            title=data.title,
            category_id=resolve_category(session, data.category_id, data.new_category),
            language=data.language,
            country=data.country,
            resolution=data.resolution,
            settings=settings.verification(),
            capture=settings.capture(),
            verify=engine_up,
        )
    except ServiceError as exc:
        return _form_page(
            request,
            session,
            form,
            channel=channel,
            error=translate("No se pudo guardar: {detail}", language_in_force(request)).format(
                detail=exc
            ),
        )
    return _after_save(request, channel_id, engine_up=engine_up)


@router.get("/channels/{channel_id}/delete", response_class=HTMLResponse, name="channel_delete")
def channel_delete_confirm(request: Request, session: SessionDep, channel_id: int):
    """Ask first, and say how much history goes with the channel."""
    channel = _channel_or_404(session, channel_id, language_in_force(request))
    return templates.TemplateResponse(
        request,
        "channels/delete.html",
        {"channel": channel, "check_count": len(channel.checks)},
    )


@router.post("/channels/{channel_id}/delete", name="channel_destroy")
def channel_destroy(request: Request, session: SessionDep, channel_id: int):
    """Delete the channel with its history and its pictures."""
    _channel_or_404(session, channel_id, language_in_force(request))
    channels.delete_channel(session, channel_id)
    return _see_other(request, "channel_list")
