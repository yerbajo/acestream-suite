"""Taking a list of links, or a document from another install, into the catalogue.

Two shapes come through the same door and are told apart by their content, never by a file name: an
acestream-suite export carries each channel's own data — title, category, language, country — while
anything else is one link per line, with whatever text came beside the link as its title. An upload
wins over the pasted text, because choosing a file is the more deliberate of the two acts.

The channels are written before anything is checked, and the checks run in the background, so the
page can answer with the summary of what was added while the engine is still working through it.
"""

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.db.models import Category
from app.services import categories, exchange, importer
from app.services.catalog import Screenshots
from app.services.errors import ServiceError, ValidationError
from app.services.iso import normalize_country, normalize_language
from app.web.check_routes import start_checks
from app.web.deps import (
    CheckRunnerDep,
    EngineFactoryDep,
    EngineProbeDep,
    SessionDep,
    SessionFactoryDep,
    SettingsDep,
)
from app.web.forms import resolve_category
from app.web.templating import templates

router = APIRouter(prefix="/import")

FormField = Annotated[str, Form()]

# One upload is a list of links, not a library. This is what the page promises to read; anything
# larger is most likely the wrong file, and reading it whole would cost the process its memory.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# The two ISO codes the form posts, each with the normaliser that judges it.
_CODE_FIELDS = (("language", normalize_language), ("country", normalize_country))


def _page(
    request: Request, session, values: dict, *, errors=None, result=None, engine_down=False
) -> HTMLResponse:
    """The page: the form as it stands, plus the summary of a run that has just finished."""
    return templates.TemplateResponse(
        request,
        "import.html",
        {
            "values": values,
            "errors": errors or {},
            "result": result,
            "engine_down": engine_down,
            "categories": categories.list_categories(session),
            "max_lines": importer.MAX_LINES,
        },
        status_code=422 if errors else 200,
    )


def _posted(
    links: str, category_id: str, new_category: str, language: str, country: str
) -> dict[str, str]:
    """The form as typed, so a refusal hands back the same text somebody pasted."""
    return {
        "links": links,
        "category_id": category_id,
        "new_category": new_category,
        "language": language,
        "country": country,
    }


def _text_problems(text: str) -> dict[str, str]:
    """What is wrong with the text itself: nothing to import, or more lines than one run takes.

    An export is exempt from the line ceiling. It is a document rather than a list, and its length
    counts channels nobody typed.
    """
    if not text.strip():
        return {"links": "Pega al menos un enlace o Content ID, o elige un archivo."}
    if not exchange.is_export(text) and len(text.splitlines()) > importer.MAX_LINES:
        return {"links": f"Como máximo {importer.MAX_LINES} líneas por importación."}
    return {}


def _chosen_category(session, category_id: str, new_category: str) -> tuple[int | None, str | None]:
    """The category the new channels get, and the complaint when the posted one names no row.

    A name typed by hand wins, exactly as on the channel form: it is the more specific answer, and
    the import creates it. That is why a typed name means the posted id is not read at all.
    """
    if new_category.strip():
        return None, None
    chosen = int(category_id) if category_id.isdigit() else None
    if category_id and (chosen is None or session.get(Category, chosen) is None):
        return None, "La categoría elegida no existe."
    return chosen, None


def _code_problems(values: dict[str, str]) -> dict[str, str]:
    """The language and country boxes that are not codes the catalogue knows."""
    problems = {}
    for field, normalize in _CODE_FIELDS:
        try:
            normalize(values[field])
        except ValidationError:
            problems[field] = "Código desconocido."
    return problems


@router.get("", response_class=HTMLResponse, name="import_page")
def import_page(request: Request, session: SessionDep):
    """The empty form."""
    return _page(request, session, {})


@router.post("", response_class=HTMLResponse, name="import_run")
def import_run(
    request: Request,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    engine_factory: EngineFactoryDep,
    settings: SettingsDep,
    runner: CheckRunnerDep,
    probe: EngineProbeDep,
    links: FormField = "",
    category_id: FormField = "",
    new_category: FormField = "",
    language: FormField = "",
    country: FormField = "",
    file: Annotated[UploadFile | None, File()] = None,
):
    """Read what was posted — a pasted list or an uploaded file — and add what it holds."""
    values = _posted(links, category_id, new_category, language, country)
    text = links
    upload_problem = None
    if file is not None and file.filename:
        text, upload_problem = _uploaded_text(file)

    # A file that could not be read has nothing to judge the text for: the file is the answer.
    errors = {"file": upload_problem} if upload_problem else _text_problems(text)
    chosen, category_problem = _chosen_category(session, category_id, new_category)
    if category_problem:
        errors["category_id"] = category_problem
    errors |= _code_problems(values)
    if errors:
        return _page(request, session, values, errors=errors)

    read = exchange.import_export if exchange.is_export(text) else importer.import_links
    try:
        result = read(
            session,
            text,
            category_id=resolve_category(session, chosen, new_category.strip() or None),
            language=language,
            country=country,
        )
    except ServiceError as exc:
        return _page(request, session, values, errors={"links": f"No se pudo importar: {exc}"})

    created = [channel.id for channel in result.created]
    if created and probe() is None:
        # The import stands; only the checks wait for an engine that is not there.
        return _page(request, session, {}, result=result, engine_down=True)
    # Channels nobody has seen yet have no picture, so their first check takes one.
    start_checks(
        runner,
        created,
        session_factory,
        engine_factory,
        settings,
        screenshots=Screenshots.IF_MISSING,
    )
    # The form comes back empty for the next list; the summary of this one stays below it.
    return _page(request, session, {}, result=result)


def _uploaded_text(file: UploadFile) -> tuple[str, str | None]:
    """The uploaded file as text, or the sentence that says why it is not one."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return "", f"El archivo supera los {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
    try:
        # A byte-order mark is what Notepad writes; the document is the same without it.
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return "", "El archivo no es texto en UTF-8."
