# Third-party notices

acestream-suite is MIT-licensed (see `LICENSE`). That license covers this repository's own work —
the deployment files, the diffs under `app-patches/` (or `*.patch` at the root), the scripts, the
documentation and the logo. It does not cover the software listed below.

## The upstream base: the AceList project

The image is built from the **AceList** project, release **1.3.0**:
`https://github.com/fabiomb/AceList`.

- AceList publishes **no LICENSE** in its repository. Nothing in that repository grants a right to
  redistribute it.
- Therefore this repository **does not redistribute AceList's source**. Neither the tarball nor an
  unpacked copy of it is vendored here. The `Dockerfile` downloads the pinned release tarball
  (`https://github.com/fabiomb/AceList/archive/refs/tags/v1.3.0.tar.gz`, verified against a sha256 at
  build time) and applies this project's own diffs (`*.patch`) to it, inside the build.
- The source shipped in any **image** built from this `Dockerfile` is AceList's source, patched. If
  you distribute such an image, you are redistributing upstream code, and that is between you and
  upstream's author. The absence of a license is not permission.
- Running the software, and modifying it for your own use, is a different question from
  redistributing it. This notice states the facts about upstream; it is not legal advice.

## The AceStream name and mark

**AceStream** is a third-party product. Its name and its mark belong to their owner.

- The product in this repository is called **acestream-suite**. The wordmark and the logo
  (`assets/logo.svg`, `assets/logo-mark.svg`, `assets/favicon-64.png`) are this project's own.
- The logo **contains the official AceStream application icon**, copied unchanged except for
  dropping the blurred shadow and glow copies of the ring. Its origin is recorded in
  `assets/README.md`: the `awesome-dashboard-icons` collection, file `icons/acestream.svg`, itself
  `apps_acestream.svg` according to its own Inkscape metadata.
- The AceStream mark is used only to say what acestream-suite plays through — a statement of fact
  about compatibility. It is not used to claim the brand, to suggest endorsement, or to pass this
  suite off as AceStream's own product. The mark inside the logo is not ours.
- The wordmark is set in Lato SemiBold converted to outlines. Lato is under the SIL Open Font
  License 1.1 (© Łukasz Dziedzic); no font file ships here, only the resulting paths.

## Software the image installs

The image is `python:3.12-slim` (Debian) plus:

| Component | License | Why it is there |
|---|---|---|
| Debian base packages (`ca-certificates`, `curl`, `patch`) | respective Debian licenses (mostly GPL/LGPL, some permissive) | TLS roots, downloading the pinned tarball, applying the patches |
| `ffmpeg` (Debian package) | LGPL/GPL, depending on the build options Debian compiled | Captures a frame from a live stream for the channel screenshots and the availability check |
| Python 3.12 and the app's Python dependencies | PSF-2.0 and the respective licenses of each dependency, resolved from the upstream project's own `pyproject.toml` at build time | Running the application |
| The AceList release tarball, patched | no license published upstream, see above | The application itself |

The HLS bridge (`hls-bridge.py`) adds nothing to the image: it runs on the same image and drives the
`ffmpeg` already installed there.

## Playlist and guide data

The playlists are built from the engine's own index and the catalogue the operator imported. The
guide is built from whatever XMLTV sources the operator configures (`ACESTREAM_SUITE_EPG_SOURCES`).
Those sources are third-party data with their own terms, and nothing in this repository redistributes
them: the app downloads them at run time, into its own database.
