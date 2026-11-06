"""The app object: what the server loads, put together once at import.

Two guards come before every route and neither one covers for the other. `TrustedHostMiddleware`
drops a request whose `Host` names an address this app does not answer to, which is what keeps a
page on some other site from reaching it through DNS rebinding even though the listener is on
loopback. The write guard answers what a host check cannot see: a browser posts a form into this app
from somebody else's page with this app's own address in the request, so the headers have to say
where the request started.

A LAN deployment adds the address it is reached by through `ACESTREAM_SUITE_ALLOWED_HOSTS`, and the
same list feeds both checks on purpose: whatever can be opened has to be writable as well.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import ALLOWED_HOSTS as DEPLOYED_HOSTS
from app.config import DATA_DIR, env
from app.db.session import make_engine, make_session_factory
from app.services.batch import CheckRunner
from app.services.screenshots import SCREENSHOTS_SUBDIR
from app.web import (
    check_routes,
    engine_routes,
    epg_routes,
    import_routes,
    search_routes,
    settings_routes,
)
from app.web.flash import clear_shown_flash
from app.web.routes import router
from app.web.templating import STATIC_DIR

# Loopback: the app is for the machine it runs on, and a deployment that must be reachable says so
# through the environment instead of moving this.
HOST = "127.0.0.1"
PORT = int(env("PORT", "8000"))

# The host check's list: this machine, the name it answers to, and whatever the deployment added.
ALLOWED_HOSTS = [HOST, "localhost", *DEPLOYED_HOSTS]

# Methods that change nothing, so the write guard has nothing to refuse in them.
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Everything the app serves, in the order it was written down: the catalogue and its pages from
# `app.web.routes`, then one module per console area.
ROUTERS = (
    router,
    settings_routes.router,
    check_routes.router,
    import_routes.router,
    search_routes.router,
    engine_routes.router,
    epg_routes.router,
)


class LazyStaticFiles(StaticFiles):
    """Static files from a folder that may not exist yet: until it does, its paths are 404s."""

    async def check_config(self) -> None:
        if Path(self.directory).is_dir():
            await super().check_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Whatever is queued when the process stops goes with it; a check already running is left to
    # finish on its own, because nothing here can interrupt the engine session it holds.
    app.state.check_runner.shutdown()


app = FastAPI(title="acestream-suite", lifespan=lifespan)
# Added first, so it ends up innermost: Starlette runs the middleware added last first, which puts
# the two guards below in front of the host list.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def release_flash(request: Request, call_next):
    """Spend the notice a redirect left behind, on the one page it was meant for."""
    response = await call_next(request)
    clear_shown_flash(request, response)
    return response


def _from_another_site(origin: str | None, fetch_site: str | None) -> bool:
    """Whether these two headers say the write started somewhere else.

    `Sec-Fetch-Site` is the browser's own word for it. `Origin` is read as well, because a browser
    too old for that header sends only this one, and an origin this app does not answer to is a
    foreign origin however it arrived. An absent header (None) and an empty one are both nothing to
    go on, so they are not a refusal: some clients simply never send either.
    """
    if fetch_site == "cross-site":
        return True
    return bool(origin) and urlsplit(origin).hostname not in ALLOWED_HOSTS


@app.middleware("http")
async def refuse_writes_from_other_sites(request: Request, call_next):
    """Answer 403 to a write that did not come from this app's own pages."""
    if request.method in READ_ONLY_METHODS:
        return await call_next(request)
    if _from_another_site(request.headers.get("origin"), request.headers.get("sec-fetch-site")):
        return PlainTextResponse("cross-site request rejected", status_code=403)
    return await call_next(request)


# One engine and one session factory for the process; a page borrows a session from it.
app.state.session_factory = make_session_factory(make_engine())
# The app's single run of background checks, the one every progress page polls.
app.state.check_runner = CheckRunner()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# Captures only, never the rest of the data folder, and the folder appears with the first one.
app.mount(
    "/screenshots",
    LazyStaticFiles(directory=DATA_DIR / SCREENSHOTS_SUBDIR, check_dir=False),
    name="screenshots",
)

for mounted in ROUTERS:
    app.include_router(mounted)


@app.get("/health")
def health() -> dict[str, str]:
    """That this program is up, and which program it is: the launcher reads both answers."""
    return {"status": "ok", "app": "acestream-suite", "version": __version__}


def run() -> None:
    """Serve on loopback, which is the whole point of a local catalog."""
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    run()
