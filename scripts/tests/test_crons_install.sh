#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_crons_install.sh - Checks crons-install.sh without touching any host.
#
# `ssh` and `sudo` stubs in a temporary directory prepended to PATH: they record
# every invocation so the test can assert what ran, in which order and what did not.
# The test .cron files live in a temporary directory pointed at by CRONS_SOURCE_DIR.
# No network, no real ssh, no writes outside $TMP.
#
# Usage: scripts/tests/test_crons_install.sh
# ---------------------------------------------------------------------------
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../crons-install.sh"

FAILED=0
CASES=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAILED=1; }
check() { # <name> <condition: 0/1>
  CASES=$((CASES + 1))
  if [ "$2" -eq 0 ]; then pass "$1"; else fail "$1"; fi
}

[ -f "$SCRIPT" ] || { echo "FAIL: $SCRIPT does not exist"; exit 1; }

# The job list is read from the script itself instead of being repeated here (one line or
# several): that way the test cannot drift from JOBS, which is what case 12 watches.
JOBS_ARR=($(
  awk '/^JOBS=\(/{f=1} f{gsub(/\\$/,""); printf "%s ", $0} f && /\)[[:space:]]*$/{exit}' "$SCRIPT" \
    | sed -e 's/^JOBS=(//' -e 's/)[[:space:]]*$//'
))
[ "${#JOBS_ARR[@]}" -gt 0 ] || { echo "FAIL: could not read JOBS from $SCRIPT"; exit 1; }
J1="${JOBS_ARR[0]}"   # fixture with flock and output to /dev/null
J2="${JOBS_ARR[1]}"   # fixture with its own SHELL/PATH, comma schedule and VAR= prefix
J3="${JOBS_ARR[2]}"   # fixture with flock and compound redirection to a log

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
# ssh and sudo stubs
# ---------------------------------------------------------------------------
STUB="$TMP/bin"
mkdir -p "$STUB"
: >"$TMP/invocations"

cat >"$STUB/ssh" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
prog="$(basename "$0")"
printf '%s %s\n' "$prog" "$*" >>"${CRONS_TEST_LOG:?}"
cmd="${*: -1}"
if [ "${CRONS_TEST_FAIL_TAR:-0}" = 1 ]; then
  case "$cmd" in *tar*) exit 1 ;; esac
fi
if [ -n "${CRONS_TEST_FAIL_MV:-}" ]; then
  case "$cmd" in *mv*"$CRONS_TEST_FAIL_MV"*) exit 1 ;; esac
fi
if [ "${CRONS_TEST_NO_ROOT_CRONTAB:-0}" = 1 ]; then
  case "$cmd" in
    'crontab -l -u root'|'sudo crontab -l -u root')
      echo 'no crontab for root' >&2
      exit 1
      ;;
  esac
fi
case "$cmd" in
  'crontab -l -u root'|'sudo crontab -l -u root')
    if [ -n "${CRONS_TEST_CRONTAB_FILE:-}" ]; then cat "$CRONS_TEST_CRONTAB_FILE"; fi
    exit 0
    ;;
esac
case "$cmd" in *'crontab -u root -'*) cat >/dev/null ;; esac
case "$cmd" in *'cat > '*) cat >/dev/null ;; esac
exit 0
SH
cp "$STUB/ssh" "$STUB/sudo"
chmod +x "$STUB/ssh" "$STUB/sudo"

export PATH="$STUB:$PATH"
export CRONS_TEST_LOG="$TMP/invocations"
export CRONS_TARGET="test@host.invalid"
export CRONS_SUDO="sudo"

run() { # <args...>: runs the script in isolation; leaves stdout/stderr in $TMP/out|err and the code in $RC
  : >"$TMP/invocations"
  "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"
  RC=$?
}

# ---------------------------------------------------------------------------
# Fixtures: one .cron per job in the JOBS list, plus three with the shapes worth watching
# ---------------------------------------------------------------------------
write_fixtures() { # <dir>
  local d="$1" j
  mkdir -p "$d"

  # One generic fixture per job in JOBS: the test does not repeat the list.
  for j in "${JOBS_ARR[@]}"; do
    cat >"$d/$j.cron" <<EOF
# Test job: $j (fixture).
0 0 * * * root /usr/local/bin/$j >/dev/null 2>&1
EOF
  done

  # Three fixtures with the shapes worth watching.
  cat >"$d/$J1.cron" <<EOF
# Download cap (fixture).
*/2 * * * * root flock -n /var/lock/$J1.lock /usr/local/bin/$J1 >/dev/null 2>&1
EOF

  cat >"$d/$J2.cron" <<EOF
# Test backup (fixture).

# Second comment paragraph.

SHELL=/bin/bash
PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/bin
5,20,35,50 * * * * root SPANISH_RENTAL_MODE=report /usr/local/bin/$J2 >>/var/log/$J2.log 2>&1
EOF

  cat >"$d/$J3.cron" <<EOF
# Importer (fixture).
*/15 * * * * root flock -n /var/lock/$J3.lock /usr/local/bin/$J3 >>/var/log/$J3.log 2>&1
EOF
}

write_fixtures "$TMP/src"
export CRONS_SOURCE_DIR="$TMP/src"

# ---------------------------------------------------------------------------
# 1-9. Generation: transformation, headers, order, trailing newline and idempotence
# ---------------------------------------------------------------------------
run
check "generates: exit 0" "$RC"
cp "$TMP/out" "$TMP/gen1"

check "root field dropped, */2 schedule intact" \
  "$(grep -qxF "*/2 * * * * flock -n /var/lock/$J1.lock /usr/local/bin/$J1 >/dev/null 2>&1" "$TMP/out"; echo $?)"
check "comma schedule 5,20,35,50 intact" \
  "$(grep -qxF "5,20,35,50 * * * * SPANISH_RENTAL_MODE=report /usr/local/bin/$J2 >>/var/log/$J2.log 2>&1" "$TMP/out"; echo $?)"
check "no job line keeps the root field" \
  "$(! grep -qE '^[^#[:space:]]+([[:space:]]+[^[:space:]]+){4}[[:space:]]+root([[:space:]]|$)' "$TMP/out"; echo $?)"

check "command verbatim: flock and compound redirection" \
  "$(grep -qxF "*/15 * * * * flock -n /var/lock/$J3.lock /usr/local/bin/$J3 >>/var/log/$J3.log 2>&1" "$TMP/out"; echo $?)"

check "SPANISH_RENTAL_MODE=report prefix intact" \
  "$(grep -qxF "5,20,35,50 * * * * SPANISH_RENTAL_MODE=report /usr/local/bin/$J2 >>/var/log/$J2.log 2>&1" "$TMP/out"; echo $?)"

check "SHELL=/bin/bash appears exactly once" \
  "$([ "$(grep -c '^SHELL=' "$TMP/out")" = 1 ]; echo $?)"
check "PATH= appears exactly once" \
  "$([ "$(grep -c '^PATH=' "$TMP/out")" = 1 ]; echo $?)"
check "no block repeats SHELL=/PATH=" \
  "$(sed -n '/^# --- /,$p' "$TMP/out" | grep -qE '^(SHELL|PATH)=' 2>/dev/null; [ $? -ne 0 ]; echo $?)"

check "comment header of $J1 kept" \
  "$(grep -qxF '# Download cap (fixture).' "$TMP/out"; echo $?)"
check "comment header of $J2 kept" \
  "$(grep -qxF '# Test backup (fixture).' "$TMP/out"; echo $?)"

order=0
for j in "${JOBS_ARR[@]}"; do
  l="$(grep -n -- "# --- $j " "$TMP/out" | head -1 | cut -d: -f1)"
  if [ -n "$l" ] && [ "$l" -gt "$order" ]; then order="$l"; else order=-1; break; fi
done
check "blocks follow the JOBS order" "$([ "$order" -gt 0 ]; echo $?)"

check "output ends with a newline" \
  "$([ "$(tail -c1 "$TMP/out" | od -An -tx1 | tr -d ' ')" = "0a" ]; echo $?)"

run
cp "$TMP/out" "$TMP/gen2"
check "idempotence: two runs give identical output" "$(cmp -s "$TMP/gen1" "$TMP/gen2"; echo $?)"

# ---------------------------------------------------------------------------
# 10. A PATH= differing from the header aborts
# ---------------------------------------------------------------------------
cp -r "$TMP/src" "$TMP/src-path"
sed -i 's#^PATH=/sbin.*#PATH=/usr/bin#' "$TMP/src-path/$J2.cron"
export CRONS_SOURCE_DIR="$TMP/src-path"
run
check "different PATH: exit != 0" "$([ "$RC" -ne 0 ]; echo $?)"
check "different PATH: explained on stderr" "$(grep -q 'redefine' "$TMP/err"; echo $?)"
check "different PATH: prints no crontab" "$([ ! -s "$TMP/out" ]; echo $?)"
export CRONS_SOURCE_DIR="$TMP/src"

# ---------------------------------------------------------------------------
# 11. An @reboot line (fewer than 6 fields): warning and verbatim
# ---------------------------------------------------------------------------
cp -r "$TMP/src" "$TMP/src-reboot"
printf '@reboot root /usr/local/bin/%s >/dev/null 2>&1\n' "$J3" >>"$TMP/src-reboot/$J3.cron"
export CRONS_SOURCE_DIR="$TMP/src-reboot"
run
check "@reboot: exit 0" "$RC"
check "@reboot: warning on stderr" "$(grep -q 'fewer than 6 fields' "$TMP/err"; echo $?)"
check "@reboot: line left verbatim in the output" \
  "$(grep -qxF "@reboot root /usr/local/bin/$J3 >/dev/null 2>&1" "$TMP/out"; echo $?)"
export CRONS_SOURCE_DIR="$TMP/src"

# ---------------------------------------------------------------------------
# 12. A job in the list without its .cron file aborts without printing a crontab
# ---------------------------------------------------------------------------
cp -r "$TMP/src" "$TMP/src-missing"
rm -f "$TMP/src-missing/$J3.cron"
export CRONS_SOURCE_DIR="$TMP/src-missing"
run
check "job without a .cron: exit != 0" "$([ "$RC" -ne 0 ]; echo $?)"
check "job without a .cron: prints no crontab" "$([ ! -s "$TMP/out" ]; echo $?)"
export CRONS_SOURCE_DIR="$TMP/src"

# ---------------------------------------------------------------------------
# 13. --dry-run does not invoke ssh
# ---------------------------------------------------------------------------
run --dry-run
check "--dry-run: exit 0" "$RC"
check "--dry-run: does not invoke ssh (empty log)" "$([ ! -s "$TMP/invocations" ]; echo $?)"
check "--dry-run: prints the crontab" "$(grep -q '^# --- ' "$TMP/out"; echo $?)"

# ---------------------------------------------------------------------------
# 14. --diff: 0 when it matches the host, 1 when it differs
# ---------------------------------------------------------------------------
run
cp "$TMP/out" "$TMP/generated"
export CRONS_TEST_CRONTAB_FILE="$TMP/generated"
run --diff
check "--diff: exit 0 when the live crontab matches" "$RC"
printf '# otra crontab\n0 0 * * * root /bin/true\n' >"$TMP/otra"
export CRONS_TEST_CRONTAB_FILE="$TMP/otra"
run --diff
check "--diff: exit 1 when it differs" "$([ "$RC" -ne 0 ]; echo $?)"
unset CRONS_TEST_CRONTAB_FILE

# ---------------------------------------------------------------------------
# 15. --install: strict order and abort when the tar fails
# ---------------------------------------------------------------------------
run --install
check "--install: exit 0" "$RC"
i_backup="$(grep -n 'crontab -l -u root' "$TMP/invocations" | head -1 | cut -d: -f1)"
i_tar="$(grep -n 'tar czf' "$TMP/invocations" | head -1 | cut -d: -f1)"
i_write="$(grep -n 'crontab -u root -' "$TMP/invocations" | head -1 | cut -d: -f1)"
i_move="$(grep -n ' mv ' "$TMP/invocations" | head -1 | cut -d: -f1)"
check "--install: backs the crontab up before the tar" \
  "$([ -n "$i_backup" ] && [ -n "$i_tar" ] && [ "$i_backup" -lt "$i_tar" ]; echo $?)"
check "--install: /etc/cron.d tar before writing" \
  "$([ -n "$i_tar" ] && [ -n "$i_write" ] && [ "$i_tar" -lt "$i_write" ]; echo $?)"
check "--install: writes before moving the /etc/cron.d files" \
  "$([ -n "$i_write" ] && [ -n "$i_move" ] && [ "$i_write" -lt "$i_move" ]; echo $?)"
check "--install: moves /etc/cron.d into retired-cronmaster-<stamp>" \
  "$(grep -q 'mv .*retired-cronmaster-' "$TMP/invocations"; echo $?)"
check "--install: summary with the stamp and the backup" \
  "$(grep -q 'stamp:' "$TMP/out" && grep -q 'backup:' "$TMP/out"; echo $?)"

export CRONS_TEST_FAIL_TAR=1
run --install
check "--install with a failing tar: exit != 0" "$([ "$RC" -ne 0 ]; echo $?)"
check "--install with a failing tar: does not write the crontab" \
  "$(! grep -q 'crontab -u root -' "$TMP/invocations"; echo $?)"
check "--install with a failing tar: moves no /etc/cron.d file" \
  "$(! grep -q ' mv ' "$TMP/invocations"; echo $?)"
unset CRONS_TEST_FAIL_TAR

# ---------------------------------------------------------------------------
# 16. --install on a host with NO root crontab (defect 1)
# ---------------------------------------------------------------------------
export CRONS_TEST_NO_ROOT_CRONTAB=1
run --install
check "--install with no root crontab: exit 0" "$RC"
check "--install with no root crontab: writes the crontab anyway" \
  "$(grep -q 'crontab -u root -' "$TMP/invocations"; echo $?)"
check "--install with no root crontab: the backup is recorded as empty" \
  "$(grep -q 'empty: the host had no root crontab' "$TMP/out"; echo $?)"
check "--install with no root crontab: verifies the backup by existence (test -e)" \
  "$(grep -q "test -e '/var/backups/crontab-root-" "$TMP/invocations"; echo $?)"
check "--install with no root crontab: no longer requires size (no test -s on the backup)" \
  "$(! grep -q "test -s '/var/backups/crontab-root-" "$TMP/invocations"; echo $?)"
unset CRONS_TEST_NO_ROOT_CRONTAB

# ---------------------------------------------------------------------------
# 17. --install creates the retirement directory before the first mv (defect 2)
# ---------------------------------------------------------------------------
run --install
i_mkdir_ret="$(grep -n "mkdir -p '/etc/cron.d/retired-cronmaster-" "$TMP/invocations" | head -1 | cut -d: -f1)"
i_move="$(grep -n ' mv ' "$TMP/invocations" | head -1 | cut -d: -f1)"
check "--install: creates the retirement directory before the first mv" \
  "$([ -n "$i_mkdir_ret" ] && [ -n "$i_move" ] && [ "$i_mkdir_ret" -lt "$i_move" ]; echo $?)"

# ---------------------------------------------------------------------------
# 18. --diff against a host with NO root crontab (defect 1)
# ---------------------------------------------------------------------------
run
export CRONS_TEST_NO_ROOT_CRONTAB=1
run --diff
check "--diff with no root crontab: exits 1, does not abort" "$([ "$RC" -eq 1 ]; echo $?)"
check "--diff with no root crontab: shows the difference against empty content" \
  "$(grep -q '^+SHELL=/bin/bash' "$TMP/out"; echo $?)"
check "--diff with no root crontab: does not claim it could not read" \
  "$(! grep -q 'no se pudo leer' "$TMP/err"; echo $?)"
unset CRONS_TEST_NO_ROOT_CRONTAB

# ---------------------------------------------------------------------------
# 19. --install with a failing mv: honest summary and exit != 0 (defect 2)
# ---------------------------------------------------------------------------
export CRONS_TEST_FAIL_MV="$J1"
run --install
check "--install with a failing mv: exit != 0" "$([ "$RC" -ne 0 ]; echo $?)"
check "--install with a failing mv: the summary names the pending job" \
  "$(grep -q "could not retire:.*$J1" "$TMP/out"; echo $?)"
check "--install with a failing mv: warns about the duplication in /etc/cron.d" \
  "$(grep -q 'still in /etc/cron.d' "$TMP/out"; echo $?)"
unset CRONS_TEST_FAIL_MV

# ---------------------------------------------------------------------------
# 20. The product rename (S3/S4): the jobs, and the .cron files behind them, carry the new names
# ---------------------------------------------------------------------------
REPO_SCRIPTS="$(cd "$HERE/.." && pwd)"
nuevos=(acestream-suite-playlist acestream-suite-partido acestream-suite-prune \
        acestream-suite-guide-playlist acestream-suite-guide-refresh)
for j in "${nuevos[@]}"; do
  check "JOBS carries $j" "$(printf '%s\n' "${JOBS_ARR[@]}" | grep -qx "$j"; echo $?)"
  check "the repository has $j.cron" "$([ -f "$REPO_SCRIPTS/$j.cron" ]; echo $?)"
done
# No .cron file in the repository belongs to a job that is not in JOBS: a renamed job whose old
# file stayed behind would still be migrated (and run) on the host.
for f in "$REPO_SCRIPTS"/*.cron; do
  job="$(basename "$f" .cron)"
  check "every .cron in scripts/ is a job in JOBS ($job)" \
    "$(printf '%s\n' "${JOBS_ARR[@]}" | grep -qx "$job"; echo $?)"
done

# The repository's own .cron files: every job in JOBS resolves (a missing file aborts) and the
# crontab it builds runs the new binaries. `CRONS_EXTRA_SOURCE_DIR` keeps its default, which is
# where finanzas-backup.cron lives.
export CRONS_SOURCE_DIR="$REPO_SCRIPTS"
run
check "the repository's own .cron files generate (exit 0)" "$RC"
for j in acestream-suite-playlist acestream-suite-partido acestream-suite-prune \
         acestream-suite-guide-playlist; do
  check "the generated crontab runs /usr/local/bin/$j" \
    "$(grep -qF "/usr/local/bin/$j " "$TMP/out"; echo $?)"
done
check "the generated crontab has the guide-refresh block" \
  "$(grep -qF '# --- acestream-suite-guide-refresh ' "$TMP/out"; echo $?)"

# The list aliases (S4, as data): the two playlist jobs load the host's own env file, so an alias
# for another consumer lives outside the repository. Only those two, and the line is harmless when
# the file is absent (POSIX sh; the crontab runs /bin/sh).
ENV_LINE='[ -r /etc/acestream-suite.env ] && . /etc/acestream-suite.env;'
# Los cinco trabajos del producto cargan el fichero del host: el repositorio trae valores genéricos
# y los de cada despliegue viven ahí, así que ninguno puede depender del entorno por su cuenta.
for j in acestream-suite-playlist acestream-suite-guide-playlist; do
  check "$j loads /etc/acestream-suite.env" \
    "$(grep -qF "$ENV_LINE /usr/local/bin/$j " "$TMP/out"; echo $?)"
done
for j in acestream-suite-partido acestream-suite-prune; do
  check "$j loads /etc/acestream-suite.env" \
    "$(grep -qF "$ENV_LINE /usr/local/bin/$j " "$TMP/out"; echo $?)"
done
check "the guide refresh loads /etc/acestream-suite.env" \
  "$(grep -qF "$ENV_LINE /usr/bin/flock" "$TMP/out"; echo $?)"
check "all five product jobs load /etc/acestream-suite.env" \
  "$([ "$(grep -cF "$ENV_LINE" "$TMP/out")" = 5 ]; echo $?)"
check "no job outside the product loads it" \
  "$([ "$(grep -F "$ENV_LINE" "$TMP/out" | grep -vc 'acestream-suite')" = 0 ]; echo $?)"
check "the env-file load does not break when the file is absent" \
  "$(sh -c '[ -r "$1" ] && . "$1"; exit 0' sh "$TMP/no-existe.env"; echo $?)"
export CRONS_SOURCE_DIR="$TMP/src"

# ---------------------------------------------------------------------------
echo
if [ "$FAILED" -eq 0 ]; then
  echo "OK: $CASES assertions, all pass"
  exit 0
fi
echo "FAILED: some assertion did not pass"
exit 1
