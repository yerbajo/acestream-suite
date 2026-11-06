"""What a request is handed, and how FastAPI builds it.

These are functions and not module-level objects because the app is assembled around that fact: a
test replaces one of them for the length of one test and nothing below the route can tell. Their
names are surface, not detail.

Two of them own something — the session and the engine client — so they are generators: however a
route ends, what it was given is closed afterwards.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.acestream.client import EngineClient, EngineVersion
from app.acestream.errors import EngineError
from app.services.batch import CheckRunner
from app.services.settings import AppSettings, load_settings

# A new engine client, for a caller that cannot borrow the request's: a background check runs in
# its own thread, and the request's client is closed when the request ends.
EngineFactory = Callable[[], EngineClient]
# The engine's version, or None when nothing is there to answer.
EngineProbe = Callable[[], EngineVersion | None]


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """The factory the app was started with: one engine per process, one session per request."""
    return request.app.state.session_factory


SessionFactoryDep = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def get_session(factory: SessionFactoryDep) -> Iterator[Session]:
    """This request's session, closed with it."""
    with factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_settings(session: SessionDep) -> AppSettings:
    """What the settings page last saved, read once so one page never sees two versions."""
    return load_settings(session)


SettingsDep = Annotated[AppSettings, Depends(get_settings)]


def get_engine_factory(settings: SettingsDep) -> EngineFactory:
    """Builds clients against the engine the settings point at."""
    return lambda: EngineClient(settings.engine_url, timeout=settings.engine_timeout)


EngineFactoryDep = Annotated[EngineFactory, Depends(get_engine_factory)]


def get_engine_client(factory: EngineFactoryDep) -> Iterator[EngineClient]:
    """This request's client, closed with it."""
    with factory() as client:
        yield client


EngineDep = Annotated[EngineClient, Depends(get_engine_client)]


def get_engine_probe(factory: EngineFactoryDep) -> EngineProbe:
    """One question with two useful answers: the version, or None if the engine is not there.

    Every caller only needs to know whether a check or a capture can be started right now, so the
    reasons there is no engine — never launched, wrong port, refused, timing out — are collapsed on
    purpose. Which one it was is a question for the engine test in the settings page, which keeps
    the error and shows it.
    """

    def probe() -> EngineVersion | None:
        try:
            with factory() as client:
                return client.version()
        except EngineError:
            return None

    return probe


EngineProbeDep = Annotated[EngineProbe, Depends(get_engine_probe)]


def get_check_runner(request: Request) -> CheckRunner:
    """The app's single run of background checks: every page that polls one polls this."""
    return request.app.state.check_runner


CheckRunnerDep = Annotated[CheckRunner, Depends(get_check_runner)]
