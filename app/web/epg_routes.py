"""The guide's screens and its two published documents.

The catalogue's pages live in `app/web/routes.py`; what is here is the guide on top of them: one
channel's day grid, the mapping screen, and the XMLTV and JSON that a player and the playlist
builder fetch. Nothing in this module imports `app.web.check_routes` and no page queues a check: the
guide is a read of stored listings, and putting a second queue on the engine is the exact
mistake `app/cli.py` exists to avoid. The only writes here are the mapping screen's two, and they
touch `epg_alias` and `channel.guide_channel_id` and nothing else.

Two empty states are ordinary and neither is an error: a catalogue channel with no guide, and a
guide that has not been refreshed yet. Both answer 200 with a sentence in Spanish, because a dead or
half-loaded feed must not take the catalogue down with it.

`/epg.xml` and `/epg.json` are read-only and rebuilt from the store on every request. A player
fetches the first on its own schedule and the playlist builder reads the second, so neither is
cached and neither can go stale.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.services import epg_guide, epg_matching, epg_output
from app.services.channels import get_channel
from app.services.errors import NotFoundError
from app.web.deps import SessionDep
from app.web.templating import templates

router = APIRouter()

FormField = Annotated[str, Form()]

# Unmapped channels per page. The matcher scores every one of them against every guide name, so this
# number is what bounds one page's rendering.
PER_PAGE = 25

# Guide names the picker offers at once. Hundreds of them cannot go into one select.
GUIDES_SHOWN = 50

# How many proposals one unmapped row shows: enough to choose from, few enough to stay readable.
PROPOSALS = 3


def _back_to_mapping(request: Request) -> RedirectResponse:
    """Send the browser back to the mapping screen as a GET, so a reload resubmits nothing."""
    return RedirectResponse(request.url_for("epg_mapping"), status_code=303)


def _existing_channel(session: Session, channel_id: int):
    """The catalogue channel, or a 404 for the page that asked for it."""
    try:
        return get_channel(session, channel_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canal no encontrado") from exc


def _page_number(asked: int) -> int:
    """The page to render: the first one for anything a URL may carry below it."""
    return max(asked, 1)


def _page_count(unmapped_total: int) -> int:
    """How many pages of unmapped channels there are, one even when there are none."""
    return max(1, (unmapped_total + PER_PAGE - 1) // PER_PAGE)


@router.get("/channels/{channel_id}/guide", response_class=HTMLResponse, name="channel_guide")
def channel_guide(request: Request, session: SessionDep, channel_id: int):
    """One channel's next 24 h in Madrid time, with whatever is on now marked.

    A channel with no guide and a guide with nothing inside the window both answer 200 with the same
    title and a different reason line: the two look alike on screen and are not the same thing to
    fix.
    """
    channel = _existing_channel(session, channel_id)
    now = datetime.now(UTC)
    guide_id = channel.guide_channel_id
    rows = epg_guide.day_grid(session, guide_id, now=now) if guide_id is not None else []
    return templates.TemplateResponse(
        request,
        "channels/guide.html",
        {"channel": channel, "rows": rows, "now": now},
    )


@router.get("/epg/mapping", response_class=HTMLResponse, name="epg_mapping")
def epg_mapping(request: Request, session: SessionDep, page: int = 1):
    """The unmapped channels with the proposals the matcher rejected, and the manual decisions.

    Paginated because the matcher's report is bounded by design: every unmapped row on the page
    scores each guide name the store holds.
    """
    page = _page_number(page)
    mapping = epg_matching.report(
        session, limit=PER_PAGE, candidates=PROPOSALS, offset=(page - 1) * PER_PAGE
    )
    return templates.TemplateResponse(
        request,
        "epg/mapping.html",
        {
            "mapping": mapping,
            "decisions": epg_matching.manual_decisions(session),
            "page": page,
            "total_pages": _page_count(mapping.unmapped_total),
        },
    )


@router.get("/epg/mapping/guides", response_class=HTMLResponse, name="epg_mapping_guides")
def epg_mapping_guides(request: Request, session: SessionDep, q: str = ""):
    """The `<option>`s for one row's picker, narrowed by the search box.

    A partial and not a page: htmx swaps it into the row's own `<select>`.
    """
    guides = epg_matching.search_guides(session, q, limit=GUIDES_SHOWN)
    return templates.TemplateResponse(
        request, "epg/_guide_options.html", {"guides": guides, "q": q}
    )


@router.post("/epg/mapping/{channel_id}", name="epg_mapping_set")
def epg_mapping_set(
    request: Request, session: SessionDep, channel_id: int, guide_id: FormField = ""
):
    """Store one manual decision: the channel points at `guide_id` and `--auto` leaves it alone.

    A submitted picker with nothing chosen carries an empty value, which is not a decision to store.
    """
    if not guide_id.isdigit():
        return _back_to_mapping(request)
    try:
        epg_matching.set_alias(session, channel_id, int(guide_id))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canal o guía no encontrados") from exc
    return _back_to_mapping(request)


@router.post("/epg/mapping/{channel_id}/clear", name="epg_mapping_clear")
def epg_mapping_clear(request: Request, session: SessionDep, channel_id: int):
    """Undo the decision and leave the channel unmapped, where the matcher may propose again."""
    try:
        epg_matching.clear_alias(session, channel_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canal no encontrado") from exc
    return _back_to_mapping(request)


@router.get("/epg.xml", name="epg_xml")
def epg_xml(session: SessionDep):
    """The mapped guide as XMLTV, the document a playlist's `url-tvg` points at.

    Built from the store on every request: it is a few hundred programmes, a player fetches it on
    its own schedule, and there is no cache here that could go stale.
    """
    return Response(epg_output.build_xmltv(session), media_type="application/xml; charset=utf-8")


@router.get("/epg.json", name="epg_json")
def epg_json(session: SessionDep):
    """`content_id -> guide id -> display name`, for the mapped channels and no others.

    This is the link to the catalogue that the XMLTV cannot carry; an unmapped channel is absent
    here exactly as it is absent there.
    """
    return JSONResponse(epg_output.build_mapping(session))
