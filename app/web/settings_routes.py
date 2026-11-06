"""The settings screen: what the app is pointed at, and two buttons that prove it.

Saving is one POST of every field, and each field is refused with its own sentence when it makes no
sense — an address without a scheme, a timeout of zero, a path that is not on this disk. All of them
at once, since the page shows all of them at once.

Two values cannot be judged by reading them: whether the engine answers at that address, and
whether the searcher does. Each gets a button that tries what is typed and saves none of it, because
the point of the button is to find out before committing.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.acestream.client import EngineClient
from app.acestream.errors import EngineError
from app.acestream.install import find_engine_executable
from app.i18n import translate
from app.services import player, screenshots
from app.services.search import SearchClient, SearchError
from app.services.settings import save_settings
from app.web.deps import SessionDep, SettingsDep
from app.web.forms import SettingsForm
from app.web.templating import language_in_force, templates

router = APIRouter(prefix="/settings")

FormField = Annotated[str, Form()]

# What the searcher is asked when somebody wants to know whether it works: short, and with results
# in any sports index. A failure should mean the searcher is down, not that the query was obscure.
_PROBE_QUERY = "dazn"

# What each test button needs before it can try anything. A timeout of "abc" makes both tests
# meaningless; a URL without a scheme makes one of them. The order is the reporting order.
_ENGINE_FIELDS = ("engine_url", "engine_timeout")
_SEARCH_FIELDS = ("search_url", "engine_timeout")

# What a probe waits when the typed timeout cannot be read as a number.
_FALLBACK_TIMEOUT = 15.0


def _posted_form(
    engine_url: FormField = "",
    engine_timeout: FormField = "",
    search_url: FormField = "",
    min_peers: FormField = "",
    check_timeout: FormField = "",
    screenshot_timeout: FormField = "",
    check_concurrency: FormField = "",
    vlc_path: FormField = "",
    ffmpeg_path: FormField = "",
    acestream_path: FormField = "",
    language: FormField = "",
) -> SettingsForm:
    """The eleven boxes of the page, turned into the form object the template and the checks
    share."""
    return SettingsForm(
        engine_url,
        engine_timeout,
        search_url,
        min_peers,
        check_timeout,
        screenshot_timeout,
        check_concurrency,
        vlc_path,
        ffmpeg_path,
        acestream_path,
        language,
    )


TypedForm = Annotated[SettingsForm, Depends(_posted_form)]


def detect_paths() -> dict[str, str | None]:
    """What autodetection finds, ignoring what is saved and every environment variable.

    Only a suggestion: the page shows these beside the boxes, and the button copies them into the
    empty ones. Nothing here writes anything.
    """
    return {
        "vlc_path": player.find_vlc(None),
        "ffmpeg_path": screenshots.find_ffmpeg(None),
        "acestream_path": find_engine_executable(),
    }


def _page(request: Request, form: SettingsForm, *, notice: str | None = None) -> HTMLResponse:
    """The page with the form as it stands, and what this machine has to offer for it."""
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"form": form, "detected": detect_paths(), "notice": notice},
        # 422 marks a rejected form; htmx is configured to show it (see base.html).
        status_code=422 if form.errors else 200,
    )


@router.get("", response_class=HTMLResponse, name="settings")
def settings_page(request: Request, settings: SettingsDep, saved: bool = False):
    """The settings as saved, with a word of confirmation when the redirect says one is due."""
    return _page(
        request,
        SettingsForm.from_settings(settings),
        notice=translate("Ajustes guardados.", language_in_force(request)) if saved else None,
    )


@router.post("", response_class=HTMLResponse, name="settings_save")
def settings_save(request: Request, session: SessionDep, form: TypedForm):
    """Store the form, or show it again with every complaint about it."""
    settings = form.validate(language=language_in_force(request))
    if settings is None:
        return _page(request, form)
    save_settings(session, settings)
    # 303 turns the POST into a GET that says it was saved, so reloading never saves twice.
    url = request.url_for("settings").include_query_params(saved="true")
    return RedirectResponse(url, status_code=303)


@router.post("/detect", response_class=HTMLResponse, name="settings_detect")
def settings_detect(request: Request, form: TypedForm):
    """Copy what was found into the empty path boxes; save nothing, so nothing changes yet."""
    detected = detect_paths()
    # Only the empty boxes: a path somebody typed is a decision, not a gap to fill.
    filled = [name for name, path in detected.items() if path and not getattr(form, name).strip()]
    for name in filled:
        setattr(form, name, detected[name])
    notice = (
        translate(
            "Rutas detectadas. Revisa y pulsa «Guardar» para aplicarlas.",
            language_in_force(request),
        )
        if filled
        else translate("No se detectaron rutas nuevas.", language_in_force(request))
    )
    response = _page(request, form, notice=notice)
    # Keeps /settings in the address bar: reloading it is a GET, and this page is a POST.
    response.headers["HX-Push-Url"] = str(request.url_for("settings"))
    return response


def _first_problem(form: SettingsForm, fields: tuple[str, ...]) -> str | None:
    """The first reason a test cannot go ahead, taken from the verdicts `validate` just reached."""
    return next((form.errors[name] for name in fields if form.errors.get(name)), None)


def _seconds(raw: str) -> float:
    """A timeout typed by hand, in seconds; a box left empty or unreadable gets the fallback."""
    try:
        return float(raw.strip().replace(",", "."))
    except ValueError:
        return _FALLBACK_TIMEOUT


def _search_base(form: SettingsForm) -> str:
    """Where the searcher lives: its own address, or the engine's, which serves it as well."""
    return form.search_url.strip().rstrip("/") or form.engine_url.strip().rstrip("/")


@router.post("/test-search", response_class=HTMLResponse, name="settings_test_search")
def settings_test_search(request: Request, form: TypedForm):
    """Ask the searcher one real question, as typed, before any of it is saved.

    The search is a service of its own (`/search.m3u`), so an engine that answers its version has
    proved nothing here: the thing being configured is the searcher, and the searcher is what this
    asks.
    """
    form.validate(language=language_in_force(request))
    problem = _first_problem(form, _SEARCH_FIELDS)
    base = _search_base(form)
    hits = None
    if problem is None:
        try:
            with SearchClient(
                base, timeout=_seconds(form.engine_timeout), language=language_in_force(request)
            ) as client:
                hits = client.search(_PROBE_QUERY, limit=5)
        except SearchError as exc:
            problem = translate(
                "La búsqueda no responde: {detail}", language_in_force(request)
            ).format(detail=exc)
    return templates.TemplateResponse(
        request,
        "_search_test.html",
        {"hits": hits, "problem": problem, "query": _PROBE_QUERY, "base": base},
        status_code=503 if problem else 200,
    )


@router.post("/test-engine", response_class=HTMLResponse, name="settings_test_engine")
def settings_test_engine(request: Request, form: TypedForm):
    """Ask the engine at the typed address for its version, before any of it is saved."""
    form.validate(language=language_in_force(request))
    problem = _first_problem(form, _ENGINE_FIELDS)
    version = None
    if problem is None:
        url = form.engine_url.strip().rstrip("/")
        try:
            with EngineClient(url, timeout=_seconds(form.engine_timeout)) as client:
                version = client.version()
        except EngineError as exc:
            problem = translate(
                "El engine no responde en {url}: {detail}", language_in_force(request)
            ).format(url=url, detail=exc)
    return templates.TemplateResponse(
        request,
        "_engine_test.html",
        {"version": version, "problem": problem},
        status_code=503 if problem else 200,
    )
