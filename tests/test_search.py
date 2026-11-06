"""El buscador: qué significa su respuesta, cómo se construye su URL y cómo se configura."""

import re
from datetime import UTC, datetime, timedelta
from html import unescape

import httpx

from app.db.models import CheckStatus
from app.services import channels, checks

from app.services.search import SearchClient, SearchError, parse_hits, search_url
from app.services.settings import AppSettings
from app.web.forms import SettingsForm

HASH = "8c1c3eae077f3a786ed2f0a426197ea93fdf7373"
OTRO = "32cd9fcaf7702abd27e6f89f1c48fbe958855f8c"


def test_search_url_is_the_playlist_endpoint_with_json():
    assert search_url("http://buscador:8081/", "liga de campeones") == (
        "http://buscador:8081/search.m3u?query=liga+de+campeones&json=1"
    )


def test_parse_hits_walks_the_answer_and_ignores_the_empty_tail():
    # La última entrada del buscador es un `{}`, no un canal (medido el 2026-11-04).
    payload = [
        {"name": "M. Liga de Campeones", "infohash": HASH, "bitrate": 902408, "availability": 0.8,
         "categories": ["sport"], "countries": ["es"]},
        {},
    ]
    hits = parse_hits(payload)
    assert len(hits) == 1
    assert hits[0].content_id == HASH
    assert hits[0].name == "M. Liga de Campeones"
    assert hits[0].bitrate_text == "881 KB/s"
    assert hits[0].availability_text == "80 %"
    assert hits[0].tags_text == "sport · es"


def test_the_same_channel_twice_is_one_hit():
    payload = [{"name": "Uno", "infohash": HASH}, {"name": "Uno (copia)", "infohash": HASH.upper()}]
    assert len(parse_hits(payload)) == 1


def test_a_hash_that_is_not_a_content_id_is_dropped():
    assert parse_hits([{"name": "Malo", "infohash": "nope"}, {"name": "Bien", "infohash": OTRO}]) == [
        parse_hits([{"name": "Bien", "infohash": OTRO}])[0]
    ]


def test_the_limit_is_respected():
    payload = [{"name": f"Canal {n}", "infohash": f"{n:040x}"} for n in range(1, 11)]
    assert len(parse_hits(payload, limit=3)) == 3


def test_a_searcher_without_a_url_says_so_instead_of_crashing():
    try:
        SearchClient("").search("dazn")
    except SearchError as exc:
        assert "no hay URL" in str(exc)
    else:
        raise AssertionError("tenía que haber lanzado SearchError")


def test_an_empty_query_never_leaves_the_process():
    assert SearchClient("http://buscador:8081").search("   ") == []


def test_the_engine_address_is_the_default_searcher():
    settings = AppSettings(engine_url="http://10.0.0.3:7000")
    assert settings.resolved_search_url == "http://10.0.0.3:7000"


def test_a_saved_search_url_wins_over_the_engine():
    settings = AppSettings(engine_url="http://10.0.0.3:7000", search_url="http://otro:9000")
    assert settings.resolved_search_url == "http://otro:9000"


def _settings(**overrides):
    """El formulario de ajustes completo, que es lo que se postea desde la página."""
    values = {
        "engine_url": "http://engine.invalid:6878",
        "engine_timeout": "15",
        "min_peers": "1",
        "check_timeout": "30",
        "screenshot_timeout": "30",
        "check_concurrency": "2",
    }
    values.update(overrides)
    return values


def _form(**overrides):
    values = {
        "engine_url": "http://10.0.0.3:7000",
        "engine_timeout": "15",
        "min_peers": "1",
        "check_timeout": "30",
        "screenshot_timeout": "30",
        "check_concurrency": "2",
    }
    values.update(overrides)
    return SettingsForm(**values)


def test_an_empty_search_url_falls_back_to_the_engine():
    settings = _form().validate()
    assert settings is not None
    assert settings.search_url is None
    assert settings.resolved_search_url == "http://10.0.0.3:7000"


def test_a_search_url_that_is_not_http_is_rejected():
    form = _form(search_url="ftp://buscador:8081")
    assert form.validate() is None
    assert "search_url" in form.errors


def test_a_good_search_url_is_kept_without_the_trailing_slash():
    settings = _form(search_url="http://buscador:8081/").validate()
    assert settings is not None and settings.search_url == "http://buscador:8081"


def test_the_search_page_renders_the_results(web, respx_mock):
    # La página se dibuja de verdad: un macro que falta en la plantilla es un 500, y así se vio.
    respx_mock.get(url__startswith="http://engine.invalid:6878/search.m3u").respond(
        json=[{"name": "M. Liga de Campeones", "infohash": HASH, "bitrate": 902408,
               "availability": 0.8, "categories": ["sport"], "countries": ["es"]}, {}]
    )
    response = web.get("/search", params={"q": "campeones"})
    assert response.status_code == 200, response.text
    assert "M. Liga de Campeones" in response.text
    assert "881 KB/s" in response.text


def test_the_search_page_says_it_when_nothing_is_found(web, respx_mock):
    respx_mock.get(url__startswith="http://engine.invalid:6878/search.m3u").respond(json=[{}])
    response = web.get("/search", params={"q": "zzzz"})
    assert response.status_code == 200
    assert "no devolvió nada" in response.text


def test_the_search_page_says_it_when_the_searcher_is_down(web, respx_mock):
    respx_mock.get(url__startswith="http://engine.invalid:6878/search.m3u").mock(
        side_effect=httpx.ConnectError("refused")
    )
    response = web.get("/search", params={"q": "dazn"})
    assert response.status_code == 200
    assert "No se pudo buscar" in response.text


def test_the_settings_field_and_its_test_button_are_there(web):
    response = web.get("/settings")
    assert response.status_code == 200
    assert 'name="search_url"' in response.text
    assert "Probar búsqueda" in response.text


def test_the_search_test_button_asks_the_searcher(web, respx_mock):
    respx_mock.get(url__startswith="http://buscador:9000/search.m3u").respond(
        json=[{"name": "DAZN 1", "infohash": HASH}, {}]
    )
    response = web.post("/settings/test-search",
                        data=dict(_settings(), search_url="http://buscador:9000"))
    assert response.status_code == 200
    assert "1 resultado(s)" in response.text


def test_a_result_whose_name_starts_with_a_hash_reaches_the_catalogue(web, engine_down, respx_mock, db):
    # Hay canales del índice que se llaman así (`#VAMOS FHD --> NEW ERA`), y el importador lee como
    # comentario cualquier línea que empiece por `#`. Con el título delante del enlace, marcar ese
    # canal no añadía nada y la página contestaba «0 añadidos, 0 duplicados, 0 inválidos» (visto en
    # producción el 2026-11-05). El enlace va delante para que la línea no sea un comentario.
    name = "#VAMOS FHD --> NEW ERA"
    respx_mock.get(url__startswith="http://engine.invalid:6878/search.m3u").respond(
        json=[{"name": name, "infohash": HASH}, {}]
    )
    pick = unescape(
        re.search(r'name="pick"\s+value="([^"]*)"', web.get("/search", params={"q": "vamos"}).text).group(1)
    )

    response = web.post("/search/add", data={"query": "vamos", "pick": pick})

    assert "1 canal(es) añadidos" in response.text
    channel = channels.find_channel_by_content_id(db, HASH)
    assert channel is not None and channel.title == name


def test_the_page_shows_the_peers_the_catalogue_already_measured(web, db, respx_mock):
    # El índice de acesearch no publica pares: el único dato que existe es el de la última
    # comprobación del catálogo, y va con su fecha porque es una foto.
    known = channels.create_channel(db, title="Conocido", content_id=HASH)
    checks.add_check(
        db, known.id, status=CheckStatus.ALIVE, peers=4,
        checked_at=datetime.now(UTC) - timedelta(hours=2),
    )
    respx_mock.get(url__startswith="http://engine.invalid:6878/search.m3u").respond(
        json=[{"name": "Conocido", "infohash": HASH}, {"name": "Desconocido", "infohash": OTRO}, {}]
    )

    page = web.get("/search", params={"q": "x"}).text

    assert "4</strong> pares" in page
    assert "hace 2 h" in page
    # El que no está en el catálogo no tiene pares que enseñar, y no se inventa un cero.
    assert page.count("sin dato") == 1


def test_measuring_the_peers_opens_an_engine_session(web, respx_mock):
    # Medir de verdad cuesta una sesión del motor: por eso es un botón por fila y no una columna.
    stat_url = f"http://engine.invalid:6878/ace/stat/{HASH}/f4283e11"
    respx_mock.get(url__startswith="http://engine.invalid:6878/ace/getstream").respond(
        json={
            "response": {
                "stat_url": stat_url,
                "playback_url": "http://engine.invalid:6878/ace/r/x",
                "command_url": "http://engine.invalid:6878/ace/cmd/x",
                "infohash": HASH,
            },
            "error": None,
        }
    )
    respx_mock.get(stat_url).respond(
        json={
            "response": {"status": "dl", "peers": 7, "speed_down": 900, "downloaded": 1024},
            "error": None,
        }
    )
    respx_mock.get(url__startswith="http://engine.invalid:6878/ace/cmd").respond(
        json={"response": "ok", "error": None}
    )

    response = web.post("/search/peers", data={"content_id": HASH})

    assert response.status_code == 200
    assert "7</strong> pares" in response.text
    assert "Activo" in response.text
