# Third-party notices

acestream-suite is a web front end for an AceStream engine. It is distributed under the MIT license
(`LICENSE`), which covers the code in this repository. This file records where that code came from,
so that nobody has to guess.

## The application code

The application began as the **AceList** project — `https://github.com/fabiomb/AceList`, release
**1.3.0** — and that project publishes **no license**. To stop shipping code whose terms nobody
granted, this project **rewrote the application**: every module, template and static file was written
again — docstrings, comments, internal names, decomposition and strategy are ours — and the work is
recorded, phase by phase, in `odd/tasks/acestream-suite-rewrite.md`.

What that rewrite did **not** change, on purpose, because it is the contract with the data and with
the programs around this one:

- the HTTP surface: same routes, parameters, status codes and redirects;
- the database schema: same tables, columns, constraints and migrations;
- the user-facing strings: the Spanish text on the screens and in the console;
- the public names the rest of the code and the test suite call.

**It is honest to call this a rewrite, not a clean-room reimplementation.** It was done with the
original in view: the behaviour is modelled on that project and the file layout follows it. What is
ours is the expression. If that distinction matters for how you intend to use this repository, that
is the paragraph to weigh.

## The AceStream name and mark

**AceStream** is the P2P engine this suite is a front end for. Its name and its logo belong to their
owner. The mark appears inside `assets/logo.svg`, `assets/logo-mark.svg`, `assets/logo-3d.png` and
`assets/logo-wordmark.png` — the last two are the owner's own artwork and use it to say what the suite
plays through. It is not used to claim the AceStream brand, and no endorsement is implied or claimed.

The copy of the mark kept for provenance as `assets/ace-stream-mark.svg` comes from the
`awesome-dashboard-icons` collection (`icons/acestream.svg`, itself named `apps_acestream.svg`); the
exact URL is in `assets/README.md`.

## Software the image installs

| Component | License |
|---|---|
| `python:3.12-slim` (base image) | PSF-2.0 plus the Debian packages it ships |
| `ffmpeg`, `curl`, `ca-certificates` (apt) | LGPL/GPL, depending on the build; Debian's packaging |
| FastAPI, Uvicorn, SQLAlchemy, Alembic, httpx, Jinja2, pycountry, python-multipart | MIT / BSD / Apache-2.0, per each project |
| htmx (`app/web/static/htmx.min.js`, vendored) | BSD-2-Clause |
| The Lucide icon strokes in `app/web/templates/_icons.html` | ISC |
| The Lato letterforms traced into `assets/logo.svg` | SIL Open Font License 1.1 |

The engine itself is not part of this image: it runs elsewhere and is reached over HTTP.
