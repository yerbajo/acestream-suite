#!/bin/bash
# ---------------------------------------------------------------------------
# crons-install.sh - Builds and installs root's crontab on a host over ssh, from this
# repository's scripts/*.cron files and from
# suite-aar/finanzas.
#
# WHY THIS EXISTS. A cron panel that reads /etc/cron.d only sees a
# user's crontab (`crontab -l -u root`), never /etc/cron.d. This house deploys
# its jobs to /etc/cron.d/<job>, so without migrating them the panel shows
# nothing. Git stays the source of truth: the .cron file in the repository is
# edited and reinstalled; the host crontab is never edited by hand.
#
# WHAT IT DOES. Copies the comment header of each .cron file and its job line,
# dropping the user field (`root`) and hoisting SHELL=/PATH= into a single
# header. The output ends with a newline and is idempotent.
#
# HOUSE RULES: fail-closed (a missing .cron file, or one redefining PATH or SHELL
# with a different value, aborts instead of generating something half-baked);
# the migrated /etc/cron.d files are MOVED, never deleted; the live crontab and
# /etc/cron.d are backed up on the host before anything is written.
#
# MODES: no argument / --dry-run (print), --diff (compare with the host),
# --install (back up and write), --help.
#
# Paths can be overridden through the environment so the script can be exercised
# without touching the production host:
#   CRONS_TARGET (default root@127.0.0.1), CRONS_SUDO (default empty: we
#   already land as root), CRONS_SOURCE_DIR (default <repo>/scripts),
#   CRONS_EXTRA_SOURCE_DIR (default <repo>/../suite-aar/finanzas)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TARGET="${CRONS_TARGET:-root@127.0.0.1}"   # destination host (an ssh key must land as root)
SUDO="${CRONS_SUDO:-}"                         # already root on the host: no prefix
SOURCE_DIR="${CRONS_SOURCE_DIR:-$REPO_ROOT/scripts}"  # where the *.cron files come from
# Second directory to look into for a .cron file not found in SOURCE_DIR. Needed here: the canonical
# finanzas-backup .cron (Firefly III) lives in suite-aar/finanzas/, because that stack is deployed
# from the other repository onto this same host.
EXTRA_SOURCE_DIR="${CRONS_EXTRA_SOURCE_DIR:-$REPO_ROOT/../suite-aar/finanzas}"

# Explicit, ordered set of jobs to migrate. Not a glob: if a name has no .cron
# file the script fails instead of generating an incomplete crontab.
# acestream-suite-guide-playlist and acestream-suite-guide-refresh are here since 2026-10-15. They
# were the reason the crontab drifted: the two jobs existed as .cron files (and ran on this host while
# the guide was built in the retired duplicate deployment) but were never added to this array, so the
# generated crontab was two jobs short of the live one and the guide's list and refresh had no
# schedule.
#
# acestream-suite-partido comes next to acestream-suite-playlist since 2026-11-04: it imports the
# channels of a Real Madrid match while it is on air and writes the live set both the `Real Madrid`
# folder and madrid.m3u come from. It is the third job of the lists and it reads the guide the first
# two publish, so a missing entry here would leave the folder without its source.
#
# The five leading jobs belong to acestream-suite: since the 2026-11-04 rename their scripts, their
# .cron files and the binaries they run carry that prefix (`/usr/local/bin/acestream-suite-*`).
JOBS=(acestream-suite-playlist acestream-suite-partido acestream-suite-prune \
      acestream-suite-guide-playlist acestream-suite-guide-refresh \
      db-backup finanzas-backup frankmd-backup gitea-backup \
      homepage-backup immich-backup infra-inventory-backup linkwarden-backup nextcloud-backup \
      open-webui-backup pihole-backup planka-backup ubooquity-backup vaultwarden-backup \
      vscode-backup-fujitsu vscode-backup-itx vscode-backup-opensource)

# SHELL/PATH live in the header. If a .cron file carries other values the script
# aborts so the value is not lost silently.
HEADER_SHELL='SHELL=/bin/bash'
HEADER_PATH='PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/bin'

GENERATED="$(mktemp)" || { echo "cannot create a temporary file" >&2; exit 1; }
trap 'rm -f "$GENERATED"' EXIT

fail() { echo "ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Building (pure: no network, no ssh)
# ---------------------------------------------------------------------------

emit_job() { # <job>: print the job's block on stdout
  local job="$1" file line last_blank=0
  file="$SOURCE_DIR/$job.cron"
  if [ ! -f "$file" ] && [ -n "$EXTRA_SOURCE_DIR" ] && [ -f "$EXTRA_SOURCE_DIR/$job.cron" ]; then
    file="$EXTRA_SOURCE_DIR/$job.cron"
  fi
  [ -f "$file" ] || fail "no $job.cron in $SOURCE_DIR${EXTRA_SOURCE_DIR:+ nor in $EXTRA_SOURCE_DIR} (check JOBS or the repository)"

  printf '# --- %s (migrated from /etc/cron.d/%s) ---\n' "$job" "$job"

  while IFS= read -r line || [ -n "$line" ]; do
    if [ -z "$line" ]; then
      if [ "$last_blank" -eq 1 ]; then continue; fi
      printf '\n'
      last_blank=1
      continue
    fi
    last_blank=0

    case "$line" in
      '#'*) printf '%s\n' "$line"; continue ;;
    esac

    case "$line" in
      SHELL=*|PATH=*)
        if [ "$line" = "$HEADER_SHELL" ] || [ "$line" = "$HEADER_PATH" ]; then
          continue
        fi
        fail "$file redefines '$line', which differs from the header ($HEADER_SHELL / $HEADER_PATH); aborting so the value is not lost"
        ;;
    esac

    # Any other VAR=value line is kept as it is (there is none today).
    if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      printf '%s\n' "$line"
      continue
    fi

    # Job line in cron.d format: 'min hour dom month dow user command'.
    local -a fields=()
    read -r -a fields <<<"$line"
    if [ "${#fields[@]}" -lt 6 ]; then
      printf 'WARNING: %s: line with fewer than 6 fields, left verbatim: %s\n' "$file" "$line" >&2
      printf '%s\n' "$line"
      continue
    fi
    # Drop only the sixth field (the user) and leave the command verbatim.
    printf '%s\n' "$line" | sed -E 's/^([[:space:]]*[^[:space:]]+([[:space:]]+[^[:space:]]+){4}[[:space:]]+)[^[:space:]]+[[:space:]]+/\1/'
  done < "$file"
}

generate() { # print the generated crontab on stdout
  printf '%s\n' \
    '# Root crontab of this host, generated by scripts/crons-install.sh from' \
    "# this repo's scripts/*.cron files and from suite-aar/finanzas. Do not edit it by hand on the" \
    '# host: edit the .cron file in git and reinstall. A cron panel that reads /etc/cron.d sees it.' \
    "$HEADER_SHELL" \
    "$HEADER_PATH"
  local job
  for job in "${JOBS[@]}"; do
    printf '\n'
    emit_job "$job"
  done
}

# ---------------------------------------------------------------------------
# Modes that touch the host
# ---------------------------------------------------------------------------

install_crontab() {
  local stamp backup tarball retired job pre live pending="" had_crontab=1 note=""
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup="/var/backups/crontab-root-$stamp.txt"
  tarball="/var/backups/cron.d-$stamp.tar.gz"
  retired="/etc/cron.d/retired-cronmaster-$stamp"

  # Build first: a missing .cron file or a redefined PATH/SHELL aborts before the host is touched.
  generate >"$GENERATED"

  # Pre-check: a retirement directory with the same stamp already exists.
  pre="$(ssh "$TARGET" "if $SUDO test -e '$retired'; then echo __RETIRED_EXISTS__; fi")"
  case "$pre" in
    *__RETIRED_EXISTS__*)
      fail "the host already has $retired; wait a second or remove it by hand" ;;
  esac
  # The retirement directory is created BEFORE writing: the `mv` calls below land
  # inside it, and if it could not be created we abort without leaving the new
  # crontab living next to the old /etc/cron.d files (duplicate runs).
  if ! ssh "$TARGET" "$SUDO mkdir -p '$retired'"; then
    fail "could not create $retired on the host; nothing is written"
  fi

  # 1) Back up the live crontab. A host with NO root crontab makes
  #    `crontab -l -u root` exit 1 with "no crontab for root": that is not a
  #    failure, the backup is left empty (a record that there was no crontab).
  live="$(mktemp)"
  if ssh "$TARGET" "$SUDO crontab -l -u root" >"$live" 2>/dev/null; then
    had_crontab=1
  else
    had_crontab=0
    : >"$live"
  fi
  # EXISTENCE of the backup is verified, not its size: on a host with no crontab
  # the file is empty on purpose.
  if ! ssh "$TARGET" "$SUDO mkdir -p /var/backups" \
     || ! ssh "$TARGET" "$SUDO sh -c 'cat > $backup'" <"$live" \
     || ! ssh "$TARGET" "$SUDO test -e '$backup'"; then
    rm -f "$live"
    fail "could not back up the live crontab to $backup; nothing is written"
  fi
  rm -f "$live"

  # 2) Back up /etc/cron.d.
  if ! ssh "$TARGET" "$SUDO tar czf '$tarball' /etc/cron.d"; then
    fail "could not back up /etc/cron.d to $tarball; nothing is written"
  fi

  # 3) Write the new crontab through stdin.
  if ! ssh "$TARGET" "$SUDO crontab -u root -" <"$GENERATED"; then
    fail "could not write root's crontab on $TARGET"
  fi

  # 4) Retire (move, never delete) the migrated /etc/cron.d files. If a `mv`
  #    fails there are duplicates already: this does not abort silently, it
  #    records the failure, names it in the summary and exits non-zero at the end.
  for job in "${JOBS[@]}"; do
    if ! ssh "$TARGET" "if $SUDO test -e '/etc/cron.d/$job'; then $SUDO mv '/etc/cron.d/$job' '$retired/'; fi"; then
      pending="$pending $job"
    fi
  done

  # 5) Summary.
  if [ "$had_crontab" -eq 0 ]; then note=" (empty: the host had no root crontab)"; fi
  echo "stamp:     $stamp"
  echo "backup:    $backup$note"
  echo "cron.d:    $tarball"
  echo "retired:   $retired/"
  echo "--- crontab -l -u root on $TARGET ---"
  ssh "$TARGET" "$SUDO crontab -l -u root" || echo "(no root crontab on the host)"

  if [ -n "$pending" ]; then
    echo "PENDING: could not retire:$pending"
    echo "Those jobs are still in /etc/cron.d and are already in the new crontab: THEY RUN TWICE. Check them by hand."
    exit 1
  fi
}

usage() {
  cat <<'EOF'
Usage: scripts/crons-install.sh [MODE]

Builds root's crontab of the target host from this repo's
scripts/*.cron files and from suite-aar/finanzas, and installs it when asked.

Modes:
  (no argument)    Print the generated crontab on stdout. Does not touch the host.
  --dry-run        Same as no argument: prints and touches neither the host nor the network.
  --diff           Fetch root's live crontab over ssh and show the diff against the
                   generated one. Exits 0 when they match and 1 when they do not.
  --install        Back up the live crontab and /etc/cron.d on the host, write the new
                   crontab and move the migrated /etc/cron.d files into
                   /etc/cron.d/retired-cronmaster-<stamp>/ (they are never deleted).
  --help, -h       Show this help.

Environment: CRONS_TARGET, CRONS_SUDO, CRONS_SOURCE_DIR, CRONS_EXTRA_SOURCE_DIR.

Note: --install rewrites root's crontab WHOLE. A job in the list with no file in
the repository aborts before the host is touched.
EOF
}

main() {
  local mode="${1:-}"
  case "$mode" in
    ''|--dry-run)
      generate >"$GENERATED"
      cat "$GENERATED"
      ;;
    --diff)
      generate >"$GENERATED"
      local live rc=0
      live="$(mktemp)"
      # A host with no root crontab exits 1 here: compare against empty (the whole
      # generated content shows up as a difference), do not abort.
      if ! ssh "$TARGET" "$SUDO crontab -l -u root" >"$live" 2>/dev/null; then
        : >"$live"
      fi
      # Order: live first, then generated, so what the host is missing shows as
      # '+' lines in the diff.
      diff -u "$live" "$GENERATED" || rc=$?
      rm -f "$live"
      exit "$rc"
      ;;
    --install)
      install_crontab
      ;;
    --help|-h)
      usage
      ;;
    *)
      echo "ERROR: unrecognised argument: $mode" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
