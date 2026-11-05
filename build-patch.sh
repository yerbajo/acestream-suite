#!/bin/sh
# acestream-suite-guide.patch and acestream-suite-search.patch, reproducible.
#
# `acestream-suite/Dockerfile` applies this patch on top of `acestream-suite-lan.patch`. This script builds the two
# source trees that patch is a diff of, so the patch is regenerated instead of hand-edited — the same
# posture as `acestream-suite/acestream-suite-lan.patch`, which is likewise the only copy of its own edits.
#
#   $WORK/base — the pinned upstream tarball with ../acestream-suite/acestream-suite-lan.patch already applied. Never
#                edited, rebuilt from scratch on every `--prepare`: it is the reference the diff is
#                taken against, and it must be the tree the base image was built from.
#   $WORK/epg  — the same tree with the EPG edits on top. This is the one you edit.
#
#   acestream-suite/build-patch.sh                 # build base, create epg/search from it if missing
#   $EDITOR $WORK/epg/app/services/epg.py     # ...and every other file the EPG work touches
#   acestream-suite/build-patch.sh --generate     # rewrite acestream-suite-guide.patch from base vs epg
#
#   $WORK/search — the epg tree with the acesearch work on top (the search page and its settings).
#   acestream-suite/build-patch.sh --generate-search  # rewrite acestream-suite-search.patch from epg vs search
#
# An existing `epg` tree is never overwritten: a second `--prepare` would silently throw away the
# edits you are in the middle of. Remove $WORK/epg yourself to start it over from base.
#
# The version and the sha256 are read from ../acestream-suite/Dockerfile, so the pin lives in exactly one
# place and this script cannot drift from the image the patch is applied to.
#
#   --work DIR       work root, default /tmp/acestream-suite-src (or $ACESTREAM_SUITE_WORK,
#                    or the old $ACELIST_EPG_WORK)
#   --tarball FILE   reuse an already-downloaded tarball instead of downloading (sha256 still checked)
#   --prepare        only build the trees; this is the default
#   --generate       only write acestream-suite-guide.patch from the trees already in place
#   --generate-search only write acestream-suite-search.patch from epg vs search
#   --help           this text
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dockerfile=$here/Dockerfile
lan_patch=$here/acestream-suite-lan.patch

work=${ACESTREAM_SUITE_WORK:-${ACELIST_EPG_WORK:-/tmp/acestream-suite-src}}
mode=prepare
tarball=

usage() {
    # The comment block at the top of this file is the help text: one copy, not two.
    sed -n '2,/^set -eu$/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case $1 in
        --work) work=$2; shift 2 ;;
        --tarball) tarball=$2; shift 2 ;;
        --prepare) mode=prepare; shift ;;
        --generate) mode=generate; shift ;;
        --generate-search) mode=generate_search; shift ;;
        -h|--help) usage ;;
        *) echo "$0: unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

case $work in
    ""|/) echo "$0: refusing a work root of '$work'" >&2; exit 1 ;;
esac
[ -f "$dockerfile" ] || { echo "$0: no $dockerfile" >&2; exit 1; }
command -v patch >/dev/null || { echo "$0: patch is not installed" >&2; exit 1; }

# One pin, in acestream-suite/Dockerfile.
version=$(sed -n 's/^ARG ACELIST_VERSION=//p' "$dockerfile" | head -n 1)
sha=$(sed -n 's/^ARG ACELIST_SHA256=//p' "$dockerfile" | head -n 1)
if [ -z "$version" ] || [ -z "$sha" ]; then
    echo "$0: no ACELIST_VERSION/ACELIST_SHA256 in $dockerfile" >&2
    exit 1
fi

tarball_file=

# Sets `tarball_file`; echoing it instead would lose it inside the command substitution.
fetch() {
    if [ -n "$tarball" ]; then
        tarball_file=$tarball
    elif [ -f "$work/AceList-$version.tar.gz" ]; then
        tarball_file=$work/AceList-$version.tar.gz
        echo "using the tarball already in $work" >&2
    else
        tarball_file=$work/AceList-$version.tar.gz
        echo "downloading https://github.com/fabiomb/AceList/archive/refs/tags/v$version.tar.gz" >&2
        curl -fsSL -o "$tarball_file" \
            "https://github.com/fabiomb/AceList/archive/refs/tags/v$version.tar.gz"
    fi
    # The checksum is the whole point of pinning: the tag is a moving URL, the sha256 is not.
    printf '%s  %s\n' "$sha" "$tarball_file" | sha256sum -c - >&2
}

prepare() {
    mkdir -p "$work"
    fetch
    # Only ever this one directory, and only ever one the script created itself.
    rm -rf "$work/base"
    mkdir -p "$work/base"
    tar -xzf "$tarball_file" --strip-components=1 -C "$work/base"
    ( cd "$work/base" && patch -p1 < "$lan_patch" >&2 )
    echo "base ready: $work/base = upstream v$version + acestream-suite-lan.patch" >&2
    if [ -d "$work/epg" ]; then
        echo "epg kept as it is: $work/epg (remove it to start over from base)" >&2
    else
        cp -a "$work/base" "$work/epg"
        echo "epg ready: $work/epg (edit here, then run --generate)" >&2
    fi
    if [ -d "$work/search" ]; then
        echo "search kept as it is: $work/search (remove it to start over from epg)" >&2
    else
        cp -a "$work/epg" "$work/search"
        echo "search ready: $work/search (edit here, then run --generate-search)" >&2
    fi
}

generate() {
    [ -d "$work/base" ] && [ -d "$work/epg" ] || {
        echo "$0: no trees in $work; run --prepare first" >&2
        exit 1
    }
    out=$here/acestream-suite-guide.patch
    # `-N` so a file that exists on one side only still shows up as a whole-file hunk, which is what
    # `patch -p1` needs to create it. The two prefixes become the a/ and b/ a git-style patch uses,
    # and the `diff -r` lines are dropped: `patch` reads the ---/+++ pair and nothing else.
    # Generated directories are excluded: __pycache__ of a tree that has been imported, `*.egg-info`
    # and a venv left behind by a `pip install -e .` (make the venv in /tmp instead, see the checks in
    # odd/tasks/acelist-epg.md). None of them belongs in the patch.
    diff -ruN -x __pycache__ -x '*.egg-info' -x .venv -x .pytest_cache -x .ruff_cache \
        "$work/base" "$work/epg" \
        | sed -e '/^diff /d' \
              -e "s|^--- $work/base/|--- a/|" \
              -e "s|^+++ $work/epg/|+++ b/|" > "$out.tmp"
    if [ ! -s "$out.tmp" ]; then
        rm -f "$out.tmp"
        echo "$0: base and epg are identical, nothing written" >&2
        exit 1
    fi
    mv "$out.tmp" "$out"
    echo "wrote $out" >&2
}

generate_search() {
    [ -d "$work/epg" ] && [ -d "$work/search" ] || {
        echo "$0: no epg/search trees in $work; run --prepare first" >&2
        exit 1
    }
    out=$here/acestream-suite-search.patch
    # El parche de la búsqueda es un diff de `epg` a `search`, o sea el tercero de la cadena: la imagen
    # aplica lan, luego epg, luego este. Mismos prefijos y mismas exclusiones que el otro.
    diff -ruN -x __pycache__ -x '*.egg-info' -x .venv -x .pytest_cache -x .ruff_cache \
        "$work/epg" "$work/search" \
        | sed -e '/^diff /d' \
              -e "s|^--- $work/epg/|--- a/|" \
              -e "s|^+++ $work/search/|+++ b/|" > "$out.tmp"
    if [ ! -s "$out.tmp" ]; then
        rm -f "$out.tmp"
        echo "$0: epg and search are identical, nothing written" >&2
        exit 1
    fi
    mv "$out.tmp" "$out"
    echo "wrote $out" >&2
}

if [ "$mode" = generate ]; then
    generate
elif [ "$mode" = generate_search ]; then
    generate_search
else
    prepare
fi
