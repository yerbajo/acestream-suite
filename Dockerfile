# acestream-suite: the image of the suite, built from the source in this directory.
#
#   docker compose build && docker compose up -d
#
# There is nothing to download and nothing to patch: `app/`, `migrations/`, `pyproject.toml` and
# `alembic.ini` are this repository, and the build copies them in and installs the app in editable
# mode. The only network it needs is the base image and the distro packages.

FROM python:3.12-slim

# 3.12 is the version this image runs; the app itself targets 3.11+ (`requires-python`).
#
# ffmpeg is NOT optional: the app grabs a frame from a live stream for the channel screenshots and
# the availability verification, and it looks for ffmpeg on PATH (`ACESTREAM_SUITE_FFMPEG_PATH` is unset).
# It is the largest layer in this image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# El código de la aplicación está en este repositorio: no se descarga ninguna release ni se aplica
# ningún parche. Estas tres rutas son todo lo que la app necesita para arrancar desde el árbol.
COPY pyproject.toml alembic.ini /app/
COPY app /app/app
COPY migrations /app/migrations

# Los ficheros que no son texto —el favicon y el logotipo— no pueden vivir en el código de la app si
# alguien los edita a mano: van aparte, en assets/, y entran aquí.
COPY assets/favicon.ico assets/logo-wordmark.png /app/app/web/static/

# Editable on purpose. The app reads its templates, its static files AND `alembic.ini` +
# `migrations/` relative to the `app` package's parent (`app/config.py:ROOT_DIR`, via
# `resource_dir()`), so it must run from a source tree; a plain install would move the package into
# site-packages and depend on `package-data` being configured exactly right for the templates to
# come along. This is also how upstream itself runs it.
RUN pip install --no-cache-dir -e .

COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh

# The catalogue (SQLite) and the screenshots are the whole application state, in one directory.
# PYTHONDONTWRITEBYTECODE: the source tree is root-owned and the container runs as 1000:1000, so
# there is no point in trying to write __pycache__ on every start.
ENV ACESTREAM_SUITE_DATA_DIR=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

# The app listens on 8000 inside the container; the published port is decided in docker-compose.yml.
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
