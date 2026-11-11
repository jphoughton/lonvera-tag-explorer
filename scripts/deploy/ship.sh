#!/usr/bin/env bash
# Ship merged code to Railway: ONE git archive of a ref, then one `railway up` per service that needs it, all in parallel.
#
# Usage: scripts/deploy/ship.sh [--ref <git ref>] [--services web,etl,feeds,live,alerts,backup | auto | all] [--dry-run] [--force] [--with-tests]
#   --ref       what to ship (default origin/build/v1). Must be an ancestor of (or equal to) origin/build/v1 unless --force: only merged code ships.
#   --services  auto (default) = deploy only the services whose code changed since the sha last shipped to them (see docs/DEPLOY.md);
#               all = every service; or a comma list. An explicit list or `all` deploys those services whatever the diff says.
#   --dry-run   do everything except `railway up`, the verification polls and the state write; print the exact commands instead.
#   --force     allow a ref that is not on origin/build/v1.
#   --with-tests  run scripts/test.sh --fast (else plain pytest) BEFORE any `railway up`; a failure aborts, nothing deploys. The checkout must be at the shipped sha.
#
# Last shipped sha per service lives in ~/lonvera-tag-explorer-data/shipped.json (override with SHIP_STATE=<path>), e.g. {"web": "<sha>", "etl": "<sha>"}.
# A service with no entry is deployed. Nothing here prints secret values; `railway up` uses the CLI's own login and the explicit -p/-e/-s flags (no `railway link`).
set -euo pipefail

PROJECT_ID="5ea06da2-cc55-4bb7-ac5c-8462d0d95338"
ENVIRONMENT="production"
HEALTH_URL="https://web-production-358da.up.railway.app/healthz"
HEALTH_TIMEOUT="${SHIP_HEALTH_TIMEOUT:-300}"   # seconds to wait for the new web revision / each cron deployment (env override is for tests)
POLL_SECONDS="${SHIP_POLL_SECONDS:-5}"
ALL_SERVICES="web etl feeds live alerts backup"
STATE_FILE="${SHIP_STATE:-$HOME/lonvera-tag-explorer-data/shipped.json}"
BASE_REF="origin/build/v1"

REF="$BASE_REF"
SERVICES_ARG="auto"
DRY_RUN=0
FORCE=0
WITH_TESTS=0

die() { echo "ship: $*" >&2; exit 1; }

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ref) [ "$#" -ge 2 ] || die "--ref needs a value"; REF="$2"; shift 2 ;;
    --services) [ "$#" -ge 2 ] || die "--services needs a value"; SERVICES_ARG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --with-tests) WITH_TESTS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "$REPO is not a git checkout"
command -v python3 >/dev/null 2>&1 || die "python3 not found"
if [ "$DRY_RUN" -eq 0 ]; then
  command -v railway >/dev/null 2>&1 || die "railway CLI not found"
  command -v curl >/dev/null 2>&1 || die "curl not found"
fi

# ---- resolve the ref -------------------------------------------------------
git -C "$REPO" fetch --quiet origin build/v1 2>/dev/null || echo "ship: warning: could not fetch origin/build/v1, using the local copy" >&2
git -C "$REPO" rev-parse --verify --quiet "$BASE_REF^{commit}" >/dev/null || die "$BASE_REF not found"
SHA="$(git -C "$REPO" rev-parse --verify --quiet "$REF^{commit}")" || die "cannot resolve ref: $REF"
SHORT="$(git -C "$REPO" rev-parse --short=8 "$SHA")"
if ! git -C "$REPO" merge-base --is-ancestor "$SHA" "$BASE_REF"; then
  if [ "$FORCE" -eq 1 ]; then
    echo "ship: warning: $REF ($SHORT) is not on $BASE_REF; shipping anyway because of --force" >&2
  else
    die "$REF ($SHORT) is not an ancestor of $BASE_REF. Only merged code ships. Merge it first, or pass --force."
  fi
fi

# ---- service list ----------------------------------------------------------
valid_service() { case " $ALL_SERVICES " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
MODE="explicit"
case "$SERVICES_ARG" in
  auto) MODE="auto"; REQUESTED="$ALL_SERVICES" ;;
  all) REQUESTED="$ALL_SERVICES" ;;
  *)
    REQUESTED="$(printf '%s' "$SERVICES_ARG" | tr ',' ' ')"
    for s in $REQUESTED; do valid_service "$s" || die "unknown service '$s' (valid: $ALL_SERVICES)"; done
    ;;
esac

PHASES=""   # lines "label|seconds" for the final timing report
phase() { PHASES="${PHASES}$1|$2
"; }

# ---- optional tests, BEFORE any deploy ----------------------------------------
if [ "$WITH_TESTS" -eq 1 ]; then
  if [ -x "$REPO/scripts/test.sh" ]; then TEST_CMD="$REPO/scripts/test.sh --fast"
  elif [ -x "$REPO/.venv/bin/python" ]; then TEST_CMD="$REPO/.venv/bin/python -m pytest -q -x"
  else TEST_CMD="python3 -m pytest -q -x"; fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "ship: DRY RUN, would run tests first: (cd $REPO && $TEST_CMD)"
  else
    [ "$(git -C "$REPO" rev-parse HEAD)" = "$SHA" ] || die "--with-tests runs the checkout at $REPO, which is not at $SHORT. Check out the ref there, or run the tests yourself and omit --with-tests."
    echo "ship: running tests: $TEST_CMD"
    t_tests=$SECONDS
    # shellcheck disable=SC2086
    (cd "$REPO" && $TEST_CMD) || die "tests failed; nothing was deployed"
    phase "tests" "$((SECONDS - t_tests))"
    echo "ship: tests passed in $((SECONDS - t_tests))s"
  fi
fi

# ---- ONE archive, cleaned up on exit --------------------------------------
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ship.XXXXXX")"
TMP="${TMP//\/\//\/}"
trap 'rm -rf "$TMP"' EXIT
SRC="$TMP/src"
mkdir -p "$SRC"
t_arch=$SECONDS
git -C "$REPO" archive --format=tar "$SHA" -- . ':(exclude)tests' ':(exclude)docs' ':(exclude)analysis' | tar -x -C "$SRC"
[ -f "$SRC/Dockerfile" ] || die "archive of $SHORT has no Dockerfile"
printf '%s\n' "$SHA" > "$SRC/REVISION"     # /healthz reports this as `rev`, so the deploy check can tell the new container from the old one
phase "archive" "$((SECONDS - t_arch))"

# ---- which services need this sha -----------------------------------------
# Path relevance is computed from the code itself: the Python import graph of the archive (plus any `etl.<name>` mentioned in a service's run script).
# Anything we cannot prove irrelevant counts as relevant: stale code is worse than a 45 s deploy.
read -r -d '' PY_RELEVANT <<'PYEOF' || true
import ast, os, re, sys

src, service, changed_file = sys.argv[1], sys.argv[2], sys.argv[3]
changed = [l.strip() for l in open(changed_file) if l.strip()]

ROOT_IMAGE = {"Dockerfile", ".dockerignore", "requirements.txt"}
STATIC = {
    "web":    (ROOT_IMAGE | {"wsgi.py", "scripts/deploy/start_web.sh"}, ["migrations/"]),
    "backup": ({"scripts/deploy/run_backup.sh"}, ["scripts/deploy/backup/"]),
}
for s in ("etl", "feeds", "live", "alerts"):
    STATIC[s] = (ROOT_IMAGE | {"scripts/deploy/run_%s.sh" % s}, ["migrations/"])
exact, prefixes = STATIC[service]

def read(rel):
    with open(os.path.join(src, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()

def resolve(dotted):
    """dotted module -> (kind, relpath) or None; kind is 'pkg' or 'mod'."""
    base = dotted.replace(".", "/")
    if os.path.isfile(os.path.join(src, base + ".py")):
        return "mod", base + ".py"
    if os.path.isfile(os.path.join(src, base, "__init__.py")):
        return "pkg", base + "/__init__.py"
    return None

seen = set()
queue = []

def add_file(rel):
    if rel not in seen:
        seen.add(rel)
        queue.append(rel)

def add_module(dotted):
    parts = dotted.split(".")
    if parts[0] not in ("etl", "app"):
        return
    for i in range(1, len(parts) + 1):
        r = resolve(".".join(parts[:i]))
        if not r:
            break
        add_file(r[1])
    r = resolve(dotted)
    if r and r[0] == "pkg" and len(parts) > 1:
        # a package that is imported: take all of it (lazy imports and python -m <pkg> hide edges)
        pkgdir = os.path.dirname(r[1])
        for root, _, files in os.walk(os.path.join(src, pkgdir)):
            for f in files:
                if f.endswith(".py"):
                    add_file(os.path.relpath(os.path.join(root, f), src))

def pkg_of(rel):
    d = os.path.dirname(rel)
    return d.replace("/", ".")

def scan(rel):
    text = read(rel)
    # real imports (module level and inside functions: ast.walk sees both)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    pkg = pkg_of(rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                add_module(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = pkg.split(".") if pkg else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if not base:
                continue
            add_module(base)
            for a in node.names:
                add_module(base + "." + a.name)
    return True

seeds = []
if service == "web":
    seeds.append("wsgi.py")
    for root, _, files in os.walk(os.path.join(src, "app")):
        for f in files:
            if f.endswith(".py"):
                seeds.append(os.path.relpath(os.path.join(root, f), src))
    add_module("etl.migrate")
elif service == "backup":
    add_module("etl.backup")
else:
    add_module("etl.migrate")
    script = "scripts/deploy/run_%s.sh" % service
    for m in re.findall(r"\b(etl(?:\.[A-Za-z_][A-Za-z0-9_]*)+)", read(script)):
        add_module(m)
add_file("etl/__init__.py")
for s in seeds:
    add_file(s)

def all_py(under):
    out = []
    for root, _, files in os.walk(os.path.join(src, under)):
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.relpath(os.path.join(root, f), src))
    return out

# Edges the import graph cannot see (checked by hand 2026-11-10, written down in docs/DEPLOY.md). If you add another dynamic import, add it here:
#   app/projection/registry.py, freshness.py, stubs.py  load sibling modules and `etl.projection_<key>` by name (importlib)
#   etl/feeds/feed.py                                  loads feed parsers by dotted name from a table
#   etl/projection_*.py, app/projection/*              load scripts/projection/snapshot_read by name
def dynamic_edges():
    if any(f.startswith("app/projection/") for f in seen):
        for f in all_py("app/projection") + all_py("etl/feeds") + all_py("scripts/projection"):
            add_file(f)
        for f in sorted(os.listdir(os.path.join(src, "etl"))):
            if f.startswith("projection_") and f.endswith(".py"):
                add_file("etl/" + f)

fallback_all = False
while True:
    while queue:
        rel = queue.pop()
        if rel.endswith(".py") and not scan(rel):
            fallback_all = True  # unparsable file: cannot trust the graph
    before = len(seen)
    dynamic_edges()
    if len(seen) == before and not queue:
        break

for c in changed:
    deleted = not os.path.exists(os.path.join(src, c))
    if service != "backup" and c.startswith("scripts/projection/"):
        print("%s (loaded by name by the projection code)" % c); sys.exit(0)
    if c in exact or any(c.startswith(p) for p in prefixes):
        print("%s (service files)" % c); sys.exit(0)
    if service != "backup" and c.startswith("etl/") and not c.endswith(".py") and not deleted:
        print("%s (data file next to etl code)" % c); sys.exit(0)
    if service == "web" and c.startswith("app/"):
        print("%s (web code)" % c); sys.exit(0)
    if c in seen:
        print("%s (imported code)" % c); sys.exit(0)
    if deleted and (c.startswith("app/") or c.startswith("etl/")) and service != "backup":
        print("%s (deleted; cannot prove unused)" % c); sys.exit(0)
    if deleted and c.startswith("etl/") and service == "backup":
        print("%s (deleted; cannot prove unused)" % c); sys.exit(0)
    if fallback_all and (c.startswith("app/") or c.startswith("etl/")):
        print("%s (import graph unreadable, assuming relevant)" % c); sys.exit(0)
sys.exit(1)
PYEOF

read -r -d '' PY_STATE_GET <<'PYEOF' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    d = {}
print(d.get(sys.argv[2], "") if isinstance(d, dict) else "")
PYEOF

read -r -d '' PY_STATE_SET <<'PYEOF' || true
import json, os, sys, tempfile
path, sha, services = sys.argv[1], sys.argv[2], sys.argv[3:]
try:
    d = json.load(open(path))
    if not isinstance(d, dict):
        d = {}
except (OSError, ValueError):
    d = {}
for s in services:
    d[s] = sha
os.makedirs(os.path.dirname(path), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".shipped.")
with os.fdopen(fd, "w") as fh:
    json.dump(d, fh, indent=2, sort_keys=True)
    fh.write("\n")
os.replace(tmp, path)
PYEOF

echo "ship: ref $REF -> $SHORT ($(git -C "$REPO" log -1 --format=%s "$SHA" | cut -c1-70))"
echo "ship: mode $MODE, state file $STATE_FILE"

DEPLOY_LIST=""
DECISIONS=""   # lines "service|deploy|skip|reason"
for svc in $REQUESTED; do
  decision="deploy"; reason=""
  if [ "$MODE" = "auto" ]; then
    last="$(python3 -c "$PY_STATE_GET" "$STATE_FILE" "$svc")"
    if [ -z "$last" ]; then
      reason="no shipped sha recorded for $svc"
    elif [ "$last" = "$SHA" ]; then
      decision="skip"; reason="already shipped at $SHORT"
    elif ! git -C "$REPO" cat-file -e "$last^{commit}" 2>/dev/null; then
      reason="last shipped sha ${last:0:8} is not in this checkout"
    else
      git -C "$REPO" diff --name-only "$last..$SHA" > "$TMP/changed.$svc"
      if hit="$(python3 -c "$PY_RELEVANT" "$SRC" "$svc" "$TMP/changed.$svc")"; then
        n="$(wc -l < "$TMP/changed.$svc" | tr -d ' ')"
        reason="${last:0:8}..$SHORT touches $hit ($n files changed in total)"
      else
        n="$(wc -l < "$TMP/changed.$svc" | tr -d ' ')"
        decision="skip"; reason="none of the $n files changed since ${last:0:8} are used by $svc"
      fi
    fi
  else
    reason="requested with --services $SERVICES_ARG"
  fi
  printf 'ship:   %-7s %-6s %s\n' "$svc" "$decision" "$reason"
  DECISIONS="${DECISIONS}${svc}|${decision}|${reason}
"
  [ "$decision" = "deploy" ] && DEPLOY_LIST="$DEPLOY_LIST $svc"
done
DEPLOY_LIST="${DEPLOY_LIST# }"

# ---- deploy ----------------------------------------------------------------
T0=$SECONDS
up_cmd() {  # print the railway command for a service (for dry-run and the log header)
  printf 'railway up %s --path-as-root -p %s -e %s -s %s -c -m "ship %s"' "$SRC" "$PROJECT_ID" "$ENVIRONMENT" "$1" "$SHORT"
}

declare_result() { RESULTS="${RESULTS:-}$1|$2|$3
"; }
RESULTS=""
FAILED=""
OK_LIST=""
HEALTH_NOTE=""

if [ -z "$DEPLOY_LIST" ]; then
  echo "ship: nothing to deploy."
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "ship: DRY RUN, would run these in parallel (archive of $SHORT already extracted at $SRC):"
  for svc in $DEPLOY_LIST; do
    echo "  $(up_cmd "$svc")"
  done
  case " $DEPLOY_LIST " in
    *" web "*) echo "ship: DRY RUN, then poll $HEALTH_URL until HTTP 200 and rev == $SHA (timeout ${HEALTH_TIMEOUT}s)" ;;
  esac
  echo "ship: DRY RUN, then write $SHORT for [$DEPLOY_LIST] to $STATE_FILE"
else
  LOG_DIR="$(dirname "$STATE_FILE")/ship-logs/$(date -u +%Y%m%dT%H%M%SZ)-$SHORT"
  mkdir -p "$LOG_DIR"
  echo "ship: full logs in $LOG_DIR"
  PIDS=""
  for svc in $DEPLOY_LIST; do
    (
      start=$SECONDS
      date +%s > "$TMP/$svc.begin"
      set +e
      # each output line is prefixed with the epoch second it arrived ("<epoch><TAB><line>") so phases can be timed afterwards
      railway up "$SRC" --path-as-root -p "$PROJECT_ID" -e "$ENVIRONMENT" -s "$svc" -c -m "ship $SHORT" 2>&1 \
        | while IFS= read -r line; do printf '%s\t%s\n' "$(date +%s)" "$line"; done > "$LOG_DIR/$svc.log"
      rc="${PIPESTATUS[0]}"
      set -e
      echo "$(date +%s)" > "$TMP/$svc.end"
      secs=$((SECONDS - start))
      echo "$rc" > "$TMP/$svc.rc"
      echo "$secs" > "$TMP/$svc.secs"
      if [ "$rc" -eq 0 ]; then
        echo "ship: [$svc] done in ${secs}s"
      else
        echo "ship: [$svc] FAILED (exit $rc) after ${secs}s"
      fi
    ) &
    PIDS="$PIDS $!"
    echo "ship: [$svc] started"
  done
  for pid in $PIDS; do wait "$pid" || true; done

  for svc in $DEPLOY_LIST; do
    rc="$(cat "$TMP/$svc.rc" 2>/dev/null || echo 1)"
    secs="$(cat "$TMP/$svc.secs" 2>/dev/null || echo 0)"
    if [ "$rc" -eq 0 ]; then
      OK_LIST="$OK_LIST $svc"
    else
      FAILED="$FAILED $svc"
      echo "----- $svc: last 40 log lines -----" >&2
      tail -n 40 "$LOG_DIR/$svc.log" | cut -f2- >&2 || true
      echo "-----" >&2
    fi
  done
  OK_LIST="${OK_LIST# }"; FAILED="${FAILED# }"

  # Verify the NEW code is serving. `railway up -c` returns when the build ends, so:
  #   web  = poll /healthz until HTTP 200 AND its `rev` equals the shipped sha (the old container answers 200 without it); also stop early if web's newest deployment FAILED/CRASHED
  #   cron services (no HTTP) = poll the service's newest deployment (created after this ship started) until SUCCESS; FAILED/CRASHED fails
  # `railway deployment list` has no -p flag, so it runs from a directory linked once to the project (local CLI pointer only, changes nothing on Railway).
  LINK_DIR="$(dirname "$STATE_FILE")/ship-link"
  deployment_status() {  # $1 service -> newest deployment status created since this ship began (or NONE / UNKNOWN)
    ( cd "$LINK_DIR" && railway deployment list -s "$1" -e "$ENVIRONMENT" --limit 5 --json 2>/dev/null ) | python3 -c '
import json, sys, datetime
since = int(sys.argv[1]) - 10
try:
    rows = json.load(sys.stdin)
except ValueError:
    print("UNKNOWN"); sys.exit(0)
best = None
for r in rows:
    try:
        t = datetime.datetime.strptime(r["createdAt"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (KeyError, ValueError):
        continue
    if t >= since and (best is None or t > best[0]):
        best = (t, r.get("status", "UNKNOWN"))
print(best[1] if best else "NONE")
' "$VERIFY_SINCE" || echo UNKNOWN
  }
  web_rev() {  # prints "<http code> <rev or ->"
    local out code body rev
    out="$(curl -s --max-time 10 -w '\n%{http_code}' "$HEALTH_URL" 2>/dev/null || true)"
    code="$(printf '%s' "$out" | tail -n 1)"
    body="$(printf '%s' "$out" | sed '$d')"
    rev="$(printf '%s' "$body" | sed -n 's/.*"rev" *: *"\([0-9a-fA-F]*\)".*/\1/p')"
    echo "${code:-000} ${rev:--}"
  }
  web_fail_logs() {
    echo "----- web: last 40 lines of the railway up log -----" >&2
    tail -n 40 "$LOG_DIR/web.log" | cut -f2- >&2 || true
    echo "----- web: last 50 lines of the service log -----" >&2
    ( cd "$LINK_DIR" && railway logs -s web -e "$ENVIRONMENT" --lines 50 ) >&2 2>&1 || true
    echo "-----" >&2
  }

  if [ -n "$OK_LIST" ]; then
    mkdir -p "$LINK_DIR"
    ( cd "$LINK_DIR" && railway link -p "$PROJECT_ID" -e "$ENVIRONMENT" >/dev/null 2>&1 ) || echo "ship: warning: could not link $LINK_DIR; deployment status checks may be UNKNOWN" >&2
    VERIFY_SINCE="$(date +%s)"
    for svc in $OK_LIST; do b="$(cat "$TMP/$svc.begin" 2>/dev/null || true)"; [ -n "$b" ] && [ "$b" -lt "$VERIFY_SINCE" ] && VERIFY_SINCE="$b"; done
    PENDING="$OK_LIST"
    echo "ship: verifying [$PENDING]: web must report rev ${SHORT}, cron services must reach deployment status SUCCESS (up to ${HEALTH_TIMEOUT}s)"
    vstart=$SECONDS
    while [ -n "$PENDING" ] && [ $((SECONDS - vstart)) -lt "$HEALTH_TIMEOUT" ]; do
      STILL=""
      for svc in $PENDING; do
        if [ "$svc" = "web" ]; then
          read -r code rev <<EOF3
$(web_rev)
EOF3
          if [ "$code" = "200" ] && [ "$rev" = "$SHA" ]; then
            w=$((SECONDS - vstart)); echo "ship: web healthz 200 with rev ${SHORT} after ${w}s"; phase "healthcheck (web)" "$w"; continue
          fi
          st="$(deployment_status web)"
          case "$st" in FAILED|CRASHED) echo "ship: web deployment status $st" >&2; FAILED="$FAILED web"; continue ;; esac
          STILL="$STILL web"
        else
          st="$(deployment_status "$svc")"
          case "$st" in
            SUCCESS) echo "ship: $svc deployment SUCCESS after $((SECONDS - vstart))s" ;;
            FAILED|CRASHED|REMOVED) echo "ship: $svc deployment status $st" >&2; FAILED="$FAILED $svc" ;;
            *) STILL="$STILL $svc" ;;
          esac
        fi
      done
      PENDING="${STILL# }"
      [ -n "$PENDING" ] && sleep "$POLL_SECONDS"
    done
    for svc in $PENDING; do
      if [ "$svc" = "web" ]; then
        echo "ship: web did not serve rev ${SHORT} within ${HEALTH_TIMEOUT}s (last healthz: $(web_rev))" >&2
      else
        echo "ship: $svc deployment did not reach SUCCESS within ${HEALTH_TIMEOUT}s (last status $(deployment_status "$svc"))" >&2
      fi
      FAILED="$FAILED $svc"
    done
    FAILED="${FAILED# }"
    # only services that passed their check are recorded
    NEWOK=""
    for svc in $OK_LIST; do case " $FAILED " in *" $svc "*) ;; *) NEWOK="$NEWOK $svc" ;; esac; done
    OK_LIST="${NEWOK# }"
    # web failed: print its logs and leave the state file completely untouched (services that did pass get redeployed, harmlessly, on the next run)
    case " $FAILED " in *" web "*) web_fail_logs; echo "ship: web failed, state file not touched" >&2; OK_LIST="" ;; esac
  fi

  if [ -n "$OK_LIST" ]; then
    # shellcheck disable=SC2086
    python3 -c "$PY_STATE_SET" "$STATE_FILE" "$SHA" $OK_LIST
    echo "ship: recorded $SHORT for [$OK_LIST] in $STATE_FILE"
  fi
fi

# ---- per-phase timing per service ---------------------------------------------
# Lines of the log carry the epoch second they arrived. Phase markers are matched on the text of railway's output (best effort, the CLI's wording is not a contract):
#   upload/queue = start -> first build line, build = first build line -> first deploy line (or the end), deploy = first deploy line -> end.
# A phase with no marker in the output prints "n/a"; the full log is kept for reading by hand.
read -r -d '' PY_PHASES <<'PYEOF' || true
import re, sys
log, begin, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
build = re.compile(r"(build|\[internal\]|dockerfile|#\d+ )", re.I)
deploy = re.compile(r"(deploy|healthcheck|health check|starting container|replica)", re.I)
tb = td = None
for raw in open(log, errors="replace"):
    ts, _, line = raw.rstrip("\n").partition("\t")
    if not ts.isdigit():
        continue
    t = int(ts)
    if tb is None and build.search(line):
        tb = t
    if td is None and tb is not None and deploy.search(line) and not build.search(line):
        td = t
def fmt(a, b):
    return "n/a" if a is None or b is None else "%ds" % (b - a)
print("upload/queue %s, build %s, deploy %s, total %ds" % (fmt(begin, tb), fmt(tb, td if td else end), fmt(td, end), end - begin))
PYEOF
if [ "$DRY_RUN" -eq 0 ] && [ -n "$DEPLOY_LIST" ]; then
  echo
  echo "phase timing (seconds):"
  while IFS='|' read -r label secs; do
    [ -n "$label" ] && printf '  %-18s %ss\n' "$label" "$secs"
  done <<EOF2
$PHASES
EOF2
  for svc in $DEPLOY_LIST; do
    printf '  %-18s ' "$svc"
    python3 -c "$PY_PHASES" "$LOG_DIR/$svc.log" "$(cat "$TMP/$svc.begin")" "$(cat "$TMP/$svc.end")" || echo "n/a"
  done
  echo "  logs: $LOG_DIR"
fi

# ---- final table -----------------------------------------------------------
TOTAL=$SECONDS
echo
printf '%-8s %-10s %8s\n' service result seconds
printf '%s' "$DECISIONS" | while IFS='|' read -r svc decision _reason; do
  [ -n "$svc" ] || continue
  if [ "$decision" = "skip" ]; then
    printf '%-8s %-10s %8s\n' "$svc" skipped -
  elif [ "$DRY_RUN" -eq 1 ]; then
    printf '%-8s %-10s %8s\n' "$svc" "would deploy" -
  else
    s="$(cat "$TMP/$svc.secs" 2>/dev/null || echo -)"
    case " $FAILED " in
      *" $svc "*) printf '%-8s %-10s %8s\n' "$svc" FAILED "$s" ;;
      *) printf '%-8s %-10s %8s\n' "$svc" deployed "$s" ;;
    esac
  fi
done
echo "total wall clock: ${TOTAL}s at $SHORT"

if [ -n "$FAILED" ]; then
  echo "ship: FAILED: $FAILED" >&2
  exit 1
fi
