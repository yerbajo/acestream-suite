# Third-party notices

acestream-suite is a web front end for an AceStream engine. It is distributed under the MIT license
(`LICENSE`), which covers the work authored here. This file records what that work is built on, so
that nobody has to guess.

## The application code

The application started life as the **AceList** project — `https://github.com/fabiomb/AceList`,
release **1.3.0** — and its source now lives in this repository, heavily changed: the guide (EPG)
layer, the search page over the engine's index, the LAN deployment behaviour, the HLS bridge's
companion jobs and the product's own name and logo are not in that project.

**AceList publishes no LICENSE.** Its repository grants no right to redistribute its source, and this
note does not invent one:

- The code in `app/` and `migrations/` is derived from that unlicensed work. It is kept here because
  this project grew out of it, not because a license was granted.
- **If you redistribute this repository or an image built from it, that is a distribution of AceList's
  source too.** Whether you may do that is a question for AceList's author, not for this file. The
  safe reading is: ask first.
- The owner of this repository (the account that publishes it) is the only one who should decide to
  make it public, and should do so knowing the paragraph above.

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
| The Lato letterforms traced into `assets/logo.svg` | SIL Open Font License 1.1 |

The engine itself is not part of this image: it runs elsewhere and is reached over HTTP.
