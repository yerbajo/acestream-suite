"""The EPG screens and the two published artefacts: the day grid, the mapping screen, the XMLTV
and the JSON mapping.

`app/web/routes.py` keeps the catalogue; this module is the guide on top of it. Nothing here
imports `app.web.check_routes`, and no page queues a check: the guide is a read of stored listings,
and the duplicate must never put a second queue on the `.28` engine (the reason `app/cli.py` exists
at all). The mapping screen's writes touch `epg_alias` and `channel.guide_channel_id` and nothing
else.

Two empty states are normal and neither is an error: a catalogue channel with no guide, and a guide
that has not been refreshed yet. Both answer 200 with a sentence in Spanish, because a dead or
half-loaded feed must not take the catalogue down with it.

`/epg.xml` and `/epg.json` are read-only and computed from the store on every request; a player
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

# Unmapped channels per page. The matcher scores every one of them against every guide name, so
# this is the number that keeps one page's rendering bounded.
PER_PAGE = 25

# Guide names offered at once by the search box. Hundreds of names cannot go into one select.
GUIDES_SHOWN = 50


def _redirect(request: Request, name: str, **path_params) -> RedirectResponse:
    # 303 turns the POST into a GET, so reloading the page never resubmits the form.
    return RedirectResponse(request.url_for(name, **path_params), status_code=303)


def _channel_or_404(session: Session, channel_id: int):
    try:
        return get_channel(session, channel_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canal no encontrado") from exc


@router.get("/channels/{channel_id}/guide", response_class=HTMLResponse, name="channel_guide")
def channel_guide(request: Request, session: SessionDep, channel_id: int):
    """One channel's next 24 h in Madrid time, with what is on now marked.

    A channel with no guide and a guide with nothing in the window both answer 200 with the same
    title and a different reason line: they look alike and they are not the same thing to fix.
    """
    channel = _channel_or_404(session, channel_id)
    now = datetime.now(UTC)
    guide = channel.guide_channel_id is not None
    rows = epg_guide.day_grid(session, channel.guide_channel_id, now=now) if guide else []
    return templates.TemplateResponse(
        request,
        "channels/guide.html",
        {"channel": channel, "rows": rows, "now": now},
    )


@router.get("/epg/mapping", response_class=HTMLResponse, name="epg_mapping")
def epg_mapping(request: Request, session: SessionDep, page: int = 1):
    """Unmapped catalogue channels with the matcher's rejected candidates, and the manual fixes.

    Paginated because the matcher's own report is bounded: each unmapped row scores every guide
    name it holds.
    """
    page = max(page, 1)
    mapping = epg_matching.report(
        session, limit=PER_PAGE, candidates=3, offset=(page - 1) * PER_PAGE
    )
    total_pages = max(1, (mapping.unmapped_total + PER_PAGE - 1) // PER_PAGE)
    return templates.TemplateResponse(
        request,
        "epg/mapping.html",
        {
            "mapping": mapping,
            "decisions": epg_matching.manual_decisions(session),
            "page": page,
            "total_pages": total_pages,
        },
    )


@router.get("/epg/mapping/guides", response_class=HTMLResponse, name="epg_mapping_guides")
def epg_mapping_guides(request: Request, session: SessionDep, q: str = ""):
    """The `<option>`s for one row's guide picker, narrowed by the search box.

    A partial, not a page: htmx swaps it into the row's own `<select>`.
    """
    guides = epg_matching.search_guides(session, q, limit=GUIDES_SHOWN)
    return templates.TemplateResponse(
        request, "epg/_guide_options.html", {"guides": guides, "q": q}
    )


@router.post("/epg/mapping/{channel_id}", name="epg_mapping_set")
def epg_mapping_set(
    request: Request, session: SessionDep, channel_id: int, guide_id: FormField = ""
):
    """Stores one manual decision: it points at `guide_id` and `--auto` will leave it alone."""
    if not guide_id.isdigit():
        return _redirect(request, "epg_mapping")
    try:
        epg_matching.set_alias(session, channel_id, int(guide_id))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canal o guía no encontrados") from exc
    return _redirect(request, "epg_mapping")


@router.post("/epg/mapping/{channel_id}/clear", name="epg_mapping_clear")
def epg_mapping_clear(request: Request, session: SessionDep, channel_id: int):
    """Undoes the decision and leaves the channel unmapped, where the matcher may propose again."""
    try:
        epg_matching.clear_alias(session, channel_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canal no encontrado") from exc
    return _redirect(request, "epg_mapping")


@router.get("/epg.xml", name="epg_xml")
def epg_xml(session: SessionDep):
    """The mapped guide as XMLTV, the document the playlist's `url-tvg` points at.

    Read-only and built from the store on every request: it is a few hundred programmes, a player
    fetches it on its own schedule, and there is no cache here to go stale.
    """
    return Response(epg_output.build_xmltv(session), media_type="application/xml; charset=utf-8")


@router.get("/epg.json", name="epg_json")
def epg_json(session: SessionDep):
    """`content_id -> guide id -> display name` for the mapped channels only.

    The mapping the playlist builder needs and the XMLTV does not carry. Unmapped channels are
    absent, exactly as they are in the XMLTV.
    """
    return JSONResponse(epg_output.build_mapping(session))
