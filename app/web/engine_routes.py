"""The engine's status fragment, and the button that starts an engine which is not there.

Two questions come from the top bar: is the engine answering, and — when it is not — is there
anything on this machine to start. The fragment answers both, and it answers on its own schedule:
the page is drawn first and fetches this afterwards, then every half minute, so a slow or absent
engine can never hold up a page or a form.

Starting one blocks until the engine opens its port, and that is deliberate: the person pressed the
button because nothing was running, and an indicator still reading "apagado" a second later would
look like the press had done nothing.
"""

import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.acestream import install
from app.acestream.client import EngineVersion
from app.services.settings import AppSettings
from app.web.deps import EngineProbe, EngineProbeDep, SettingsDep
from app.i18n import translate
from app.web.templating import language_in_force, templates

router = APIRouter(prefix="/engine")

# How long one request may wait for a freshly launched engine to answer, and how often it asks
# meanwhile. The wait is the length of a Windows start-up; a second between questions costs nothing.
START_TIMEOUT = 30.0
POLL_INTERVAL = 1.0

_NO_EXECUTABLE = "No se encontró ace_engine.exe: indica su ruta en Ajustes."


def engine_executable(settings: AppSettings) -> str | None:
    """The engine to launch: the configured one when the file is there, else the detected one."""
    configured = settings.acestream_path
    if configured and Path(configured).is_file():
        return configured
    return install.find_engine_executable()


def _indicator(
    request: Request,
    settings: AppSettings,
    version: EngineVersion | None,
    *,
    error: str | None = None,
) -> HTMLResponse:
    """The fragment as the top bar reads it, with the start button only where it could work."""
    return templates.TemplateResponse(
        request,
        "_engine_status.html",
        {
            "engine_checked": True,
            "engine_version": version,
            "engine_url": settings.engine_url,
            # A button that cannot launch anything is worse than no button: it invites a press that
            # ends in an error the person cannot act on.
            "engine_can_start": version is None and engine_executable(settings) is not None,
            "engine_error": error,
        },
    )


@router.get("/status", response_class=HTMLResponse, name="engine_status")
def engine_status(request: Request, probe: EngineProbeDep, settings: SettingsDep):
    """Whether the engine is answering; the page around it never asks."""
    return _indicator(request, settings, probe())


def _wait_for_answer(probe: EngineProbe) -> EngineVersion | None:
    """Ask until the engine answers or the patience runs out, whichever comes first."""
    deadline = time.monotonic() + START_TIMEOUT
    while True:
        version = probe()
        if version is not None or time.monotonic() >= deadline:
            return version
        time.sleep(POLL_INTERVAL)


def _launch(
    probe: EngineProbe, executable: str, language: str
) -> tuple[EngineVersion | None, str | None]:
    """Start it and wait for it to answer, or say which of the two things went wrong."""
    try:
        install.start_engine(executable)
    except install.EngineStartError as exc:
        return None, translate("No se pudo iniciar: {detail}", language).replace("{detail}", str(exc))
    version = _wait_for_answer(probe)
    if version is None:
        return None, translate("Ace Stream no respondió en {seconds} s.", language).replace(
            "{seconds}", f"{START_TIMEOUT:g}"
        )
    return version, None


@router.post("/start", response_class=HTMLResponse, name="engine_start")
def engine_start(request: Request, probe: EngineProbeDep, settings: SettingsDep):
    """Launch the engine and wait until it answers (this blocks a worker thread)."""
    version = probe()
    if version is not None:
        return _indicator(request, settings, version)  # already running: nothing to launch
    executable = engine_executable(settings)
    if executable is None:
        return _indicator(request, settings, None, error=_NO_EXECUTABLE)
    version, error = _launch(probe, executable, language_in_force(request))
    return _indicator(request, settings, version, error=error)
