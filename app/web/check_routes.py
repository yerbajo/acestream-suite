"""Checks asked for from a page: what runs them, where the page goes, and how it watches them.

A check costs the engine seconds, so no page waits for one. A POST queues the work and answers with
a redirect; the run it started is shared state, and every page draws the progress bar for it from
that one object. The page that started a run is also the page that reloads when it ends, which it
learns by polling with the batch id it is showing.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from app.acestream.errors import EngineUnavailableError
from app.db.models import CheckStatus
from app.services import catalog, listing
from app.services.batch import AbortBatch, CheckRunner, CheckWork
from app.services.catalog import Screenshots
from app.services.settings import AppSettings
from app.web.deps import (
    CheckRunnerDep,
    EngineFactory,
    EngineFactoryDep,
    EngineProbe,
    EngineProbeDep,
    SessionDep,
    SessionFactoryDep,
    SettingsDep,
)
from app.web.flash import ENGINE_DOWN, flash
from app.web.query import parse_query
from app.web.templating import templates

router = APIRouter(prefix="/checks")

# Why a batch stopped early. It is not one channel's fault, so it never becomes the stored status of
# the channels that did not get their turn.
ENGINE_UNAVAILABLE = "engine_unavailable"


def _background_check(
    session_factory: sessionmaker[Session],
    engine_factory: EngineFactory,
    settings: AppSettings,
    *,
    screenshots: Screenshots = Screenshots.NEVER,
) -> CheckWork:
    """One channel's check, prepared for a thread that is not the request's.

    Nothing of the request comes along: its session and its engine client belong to the request and
    are closed with it, so each check opens its own pair and closes them again.

    An engine that cannot be reached calls the whole run off instead of returning a status. Every
    remaining channel would fail for that same reason and none of them is to blame, so filing
    "error" against each one would be a lie that outlives the outage.
    """

    def work(channel_id: int) -> CheckStatus:
        with session_factory() as session, engine_factory() as client:
            try:
                check = catalog.check_channel(
                    session,
                    client,
                    channel_id,
                    settings=settings.verification(),
                    capture=settings.capture(),
                    screenshots=screenshots,
                )
            except EngineUnavailableError as exc:
                raise AbortBatch(ENGINE_UNAVAILABLE) from exc
            return check.status

    return work


def start_checks(
    runner: CheckRunner,
    channel_ids: list[int],
    session_factory: sessionmaker[Session],
    engine_factory: EngineFactory,
    settings: AppSettings,
    *,
    screenshots: Screenshots = Screenshots.NEVER,
) -> None:
    """Queue those channels with the app's runner, at the concurrency the settings ask for."""
    work = _background_check(session_factory, engine_factory, settings, screenshots=screenshots)
    runner.submit(channel_ids, work, max_workers=settings.check_concurrency)


def _back_to(request: Request) -> RedirectResponse:
    """The view these checks were asked from, with its filters, as a GET.

    `view=gallery` is how the gallery's own buttons say where they are; it picks the page and says
    nothing about which channels were selected, so it is read once and dropped from the URL.
    """
    params = dict(request.query_params)
    view = "channel_gallery" if params.pop("view", None) == "gallery" else "channel_list"
    # 303 turns the POST into a GET, so reloading the list never queues the batch again.
    return RedirectResponse(request.url_for(view).include_query_params(**params), status_code=303)


def _queue_list(
    request: Request,
    session: Session,
    *,
    probe: EngineProbe,
    runner: CheckRunner,
    session_factory: sessionmaker[Session],
    engine_factory: EngineFactory,
    settings: AppSettings,
    force_screenshots: bool,
) -> Response:
    """Queue every channel the URL selects, then show the page that asked.

    With the engine off nothing is queued and the page says why. A batch that cannot run would fill
    the list with failures, and a list of dead channels is exactly what the person would believe.
    """
    response = _back_to(request)
    if probe() is None:
        return flash(response, ENGINE_DOWN)
    selected = listing.search_channels(session, parse_query(request.query_params))
    start_checks(
        runner,
        [channel.id for channel, _ in selected],
        session_factory,
        engine_factory,
        settings,
        screenshots=Screenshots.ALWAYS if force_screenshots else Screenshots.NEVER,
    )
    return response


@router.post("", name="checks_start")
def checks_start(
    request: Request,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    engine_factory: EngineFactoryDep,
    probe: EngineProbeDep,
    settings: SettingsDep,
    runner: CheckRunnerDep,
):
    """Check every channel the list is showing."""
    return _queue_list(
        request,
        session,
        probe=probe,
        runner=runner,
        session_factory=session_factory,
        engine_factory=engine_factory,
        settings=settings,
        force_screenshots=False,
    )


@router.post("/screenshots", name="checks_screenshots")
def checks_screenshots(
    request: Request,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    engine_factory: EngineFactoryDep,
    probe: EngineProbeDep,
    settings: SettingsDep,
    runner: CheckRunnerDep,
):
    """The same batch, with a fresh picture of every channel that turns out to be alive."""
    return _queue_list(
        request,
        session,
        probe=probe,
        runner=runner,
        session_factory=session_factory,
        engine_factory=engine_factory,
        settings=settings,
        force_screenshots=True,
    )


def _panel(request: Request, batch, watch: str) -> HTMLResponse:
    """The progress fragment. `watch` is the batch whose end reloads the page, or "" for none."""
    return templates.TemplateResponse(
        request, "_batch_status.html", {"batch": batch, "watch": watch}
    )


@router.get("/status", response_class=HTMLResponse, name="checks_status")
def checks_status(request: Request, runner: CheckRunnerDep, watching: str = ""):
    """The fragment every page polls while a batch runs."""
    batch = runner.current()
    response = _panel(request, batch, watching)
    if batch is not None and batch.finished and batch.id == watching:
        # The page that watched this run is showing states from before it: have it reload once.
        response.headers["HX-Refresh"] = "true"
    return response


@router.post("/dismiss", response_class=HTMLResponse, name="checks_dismiss")
def checks_dismiss(request: Request, runner: CheckRunnerDep):
    """Take a finished run off the screens."""
    runner.dismiss()
    return _panel(request, runner.current(), "")
