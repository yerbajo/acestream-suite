"""One notice carried across a redirect.

A write that knows why the next page must explain itself cannot render that page: it answers with a
redirect, and the page that follows is a separate request. So the reason travels in a cookie for a
minute, and the page that follows spends it.

The cookie holds a **code**, never the sentence: only the codes listed here are ever shown, so a
cookie this module did not write displays nothing, and the wording of a notice can change without
touching whatever wrote it.
"""

from fastapi import Request, Response

FLASH_COOKIE = "acestream_suite_notice"

ENGINE_DOWN = "engine_down"
ENGINE_DOWN_SAVED = "engine_down_saved"

NOTICES = {
    ENGINE_DOWN: (
        "Ace Stream no está abierto o no responde: no se verificó ningún canal. "
        "Ábrelo y vuelve a intentarlo."
    ),
    ENGINE_DOWN_SAVED: (
        "Se guardó el canal, pero Ace Stream no responde: queda sin verificar hasta que lo abras."
    ),
}


def flash(response: Response, code: str) -> Response:
    """Leave a notice for the page this response sends the browser to."""
    response.set_cookie(FLASH_COOKIE, code, max_age=60, httponly=True, samesite="strict")
    return response


def flash_message(request: Request) -> str | None:
    """The pending notice, marked as shown so the response that renders it can clear it."""
    notice = NOTICES.get(request.cookies.get(FLASH_COOKIE, ""))
    if notice is not None:
        request.state.notice_shown = True
    return notice


def clear_shown_flash(request: Request, response: Response) -> None:
    """Drop the cookie once a page has actually displayed it.

    Only that page consumes the notice. A poll from some other page can land in between — a batch
    progress bar asking for its fragment — and it must not swallow a notice nobody has read yet.
    """
    if getattr(request.state, "notice_shown", False):
        response.delete_cookie(FLASH_COOKIE, samesite="strict")
