#!/bin/sh
# acestream-suite's container entrypoint: migrate, then serve.
#
# Migrations run through the application's own function, not through the `alembic` CLI. That is what
# upstream's launcher does, and it is the only path that sets `script_location` and `sqlalchemy.url`
# itself: `alembic.ini` holds a relative `script_location = migrations` and no URL at all, so the CLI
# only works from the right working directory with the URL injected.
#
# Skipping this step is a documented failure, not a theory: The upstream app answers 500 on the front page on
# a checkout that has never migrated (upstream docs/packaging.md), and `restart: unless-stopped`
# would turn that into a quiet loop.
set -e

python -c "from app.launcher import upgrade_database; upgrade_database()"

# 0.0.0.0 and not the app's own loopback default: inside a container, loopback is not reachable from
# the LAN, and this deployment is reached from the browser of whoever is watching.
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info
