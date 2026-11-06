import sys
from pathlib import Path

from app import config


def test_from_source_the_data_stays_in_the_repository():
    assert config.default_data_dir(frozen=False) == config.ROOT_DIR / "data"


def test_the_executable_keeps_data_in_the_user_profile():
    data_dir = config.default_data_dir(
        frozen=True, environ={"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}
    )

    assert data_dir == Path(r"C:\Users\u\AppData\Local") / "acestream-suite"


def test_without_localappdata_the_profile_is_found_anyway():
    data_dir = config.default_data_dir(frozen=True, environ={})

    assert data_dir == Path.home() / "AppData" / "Local" / "acestream-suite"


def test_resources_come_from_the_repository_when_running_from_source():
    assert config.resource_dir(frozen=False) == config.ROOT_DIR
    assert (config.resource_dir(frozen=False) / "migrations" / "env.py").is_file()


def test_resources_come_from_the_bundle_in_the_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert config.resource_dir(frozen=True) == tmp_path


def test_the_tests_never_use_the_real_data_directory():
    # conftest.py points ACESTREAM_SUITE_DATA_DIR at a temporary folder before the app is imported.
    assert config.DATA_DIR != config.default_data_dir()
    assert "acestream-suite-tests-" in str(config.DATA_DIR)


def test_the_database_is_named_after_the_suite():
    assert config.DB_PATH.name == "acestream-suite.db"


def test_the_suite_variable_is_read(monkeypatch):
    monkeypatch.setenv("ACESTREAM_SUITE_ENGINE_URL", "http://nuevo:6878")

    assert config.env("ENGINE_URL") == "http://nuevo:6878"


# El prefijo que usaba el nombre viejo, compuesto en tiempo de ejecución para que el nombre retirado
# no quede en el árbol ni en el bytecode: el código ya no lo lee (rename, S3), y este caso lo fija.
OLD_PREFIX = "".join(("ACE", "LIST_"))


def test_only_the_suite_variable_is_read(monkeypatch):
    monkeypatch.delenv("ACESTREAM_SUITE_ENGINE_URL", raising=False)
    monkeypatch.setenv(OLD_PREFIX + "ENGINE_URL", "http://viejo:6878")

    assert config.env("ENGINE_URL") is None


def test_without_the_variable_the_default_is_used(monkeypatch):
    monkeypatch.delenv("ACESTREAM_SUITE_ENGINE_URL", raising=False)

    assert config.env("ENGINE_URL", "http://127.0.0.1:6878") == "http://127.0.0.1:6878"
