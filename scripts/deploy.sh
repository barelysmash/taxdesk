#!/usr/bin/env bash
#
# taxdesk deploy  —  runs ON rosencrantz (Git Bash / MINGW64)
#
# Pushes tracked source files to guildenstern and restarts only what needs it.
# Replaces the scp + `sudo install` dance that was being retyped by hand, and
# the /tmp/taxdesk-deploy staging in deploy/deploy.ps1, which predates the repo.
#
#   bash /c/bSmash-dev/taxdesk/scripts/deploy.sh scraper/scrape.py
#   bash /c/bSmash-dev/taxdesk/scripts/deploy.sh api/main.py sql/schema.sql
#   bash /c/bSmash-dev/taxdesk/scripts/deploy.sh --all
#   bash /c/bSmash-dev/taxdesk/scripts/deploy.sh --all --dry-run
#
# Paths are given relative to the repo root; everything else is absolute.
#
set -euo pipefail

REPO="${TAXDESK_REPO:-/c/bSmash-dev/taxdesk}"
REMOTE="${TAXDESK_REMOTE:-barelysmash@100.113.110.44}"
APP_DIR="${TAXDESK_APP:-/opt/taxdesk}"
DB="${TAXDESK_DB:-/var/lib/taxdesk/taxdesk.db}"
RUN_USER="${TAXDESK_USER:-ocelia}"
API_URL="${TAXDESK_API_URL:-http://100.113.110.44:8770}"

DRY=0
FILES=()

DEPLOYABLE=(
  scraper/scrape.py
  scraper/probe.py
  scraper/__init__.py
  api/main.py
  api/__init__.py
  sql/schema.sql
)

say()  { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '\033[1;33m   ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m   x %s\033[0m\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)     FILES=("${DEPLOYABLE[@]}") ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    -*)        die "unknown flag: $1" ;;
    *)         FILES+=("$1") ;;
  esac
  shift
done

((${#FILES[@]})) || die "nothing to deploy; pass file paths or --all"

# ---------------------------------------------------------------- 0. preflight
say "preflight"
[[ -d $REPO/.git ]] || die "not a git repo: $REPO"
info "repo   $REPO"
info "remote $REMOTE:$APP_DIR"

# Deploying a dirty or unpushed tree is how the box and the repo drift apart.
if [[ -n $(git -C "$REPO" status --porcelain) ]]; then
  warn "working tree has uncommitted changes:"
  git -C "$REPO" status --short | sed 's/^/     /'
  warn "deploying anyway — the box will not match any commit"
fi
if git -C "$REPO" rev-parse '@{upstream}' >/dev/null 2>&1; then
  ahead=$(git -C "$REPO" rev-list --count '@{upstream}..HEAD')
  ((ahead == 0)) || warn "$ahead commit(s) not pushed to origin"
fi
info "HEAD   $(git -C "$REPO" log -1 --format='%h %s' | cut -c1-64)"

# Validate every file before shipping any of them, so a typo can't leave the
# box half-updated.
NEED_API_RESTART=0
NEED_SCHEMA_APPLY=0
for rel in "${FILES[@]}"; do
  [[ -f $REPO/$rel ]] || die "missing: $REPO/$rel"
  case "$rel" in
    *.py)
      python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" \
        "$REPO/$rel" 2>/dev/null || die "python syntax error in $rel — not deploying"
      ;;
  esac
  if grep -qU $'\r' "$REPO/$rel" 2>/dev/null; then
    die "$rel has CRLF line endings; run: sed -i 's/\\r\$//' '$REPO/$rel'"
  fi
  [[ $rel == api/* ]] && NEED_API_RESTART=1
  [[ $rel == sql/schema.sql ]] && NEED_SCHEMA_APPLY=1
  info "ok     $rel"
done

if ((DRY)); then
  say "dry run — nothing sent"
  info "would deploy: ${FILES[*]}"
  ((NEED_API_RESTART)) && info "would restart taxdesk-api"
  ((NEED_SCHEMA_APPLY)) && info "would apply schema DDL"
  exit 0
fi

# -------------------------------------------------------------------- 1. push
say "push"
for rel in "${FILES[@]}"; do
  base=$(basename "$rel")
  scp -q "$REPO/$rel" "$REMOTE:/tmp/$base"
  ssh "$REMOTE" "sudo install -m 644 -o $RUN_USER -g $RUN_USER '/tmp/$base' '$APP_DIR/$rel' && rm -f '/tmp/$base'"
  info "$rel -> $APP_DIR/$rel"
done

# ------------------------------------------------------------------ 2. schema
# ensure_schema() runs executescript on every scrape and all DDL is
# IF NOT EXISTS, so this is only to avoid waiting for the 06:00 timer.
if ((NEED_SCHEMA_APPLY)); then
  say "apply schema"
  ssh "$REMOTE" "sudo -u $RUN_USER sqlite3 '$DB' < '$APP_DIR/sql/schema.sql'"
  info "applied (idempotent)"
fi

# ----------------------------------------------------------------- 3. restart
if ((NEED_API_RESTART)); then
  say "restart api"
  ssh "$REMOTE" "sudo systemctl restart taxdesk-api"
  sleep 2
  state=$(ssh "$REMOTE" "systemctl is-active taxdesk-api" || true)
  info "taxdesk-api: $state"
  [[ $state == active ]] || {
    warn "service did not come up — last 20 log lines:"
    ssh "$REMOTE" "sudo journalctl -u taxdesk-api -n 20 --no-pager" | sed 's/^/     /'
    die "deploy left the API down"
  }
fi

# ------------------------------------------------------------------ 4. verify
say "verify"
health=$(ssh "$REMOTE" "curl -s -o /dev/null -w '%{http_code}' '$API_URL/api/health'" || echo "000")
info "GET /api/health -> $health"

# This endpoint took 180s before the index fix; treat a slow response as a
# regression, not a hiccup.
# curl -w emits no trailing newline, so `read` returns non-zero at EOF even
# when it populated the variables. Under `set -e` that killed the script after
# a successful deploy, with no failure message. Capture then split instead.
top=$(ssh "$REMOTE" \
  "curl -s -o /dev/null -w '%{http_code} %{time_total}' '$API_URL/api/mb/austin/top?n=25&months=12'" \
  2>/dev/null) || top="000 0"
code=${top%% *}
secs=${top##* }
info "GET /api/mb/austin/top -> $code in ${secs}s"
if [[ $code == 200 ]] && awk "BEGIN{exit !($secs > 2)}"; then
  warn "that endpoint should answer in well under a second"
  warn "check EXPLAIN QUERY PLAN for idx_mb_upper_city_date; a SCAN means the index is missing"
fi

say "done"
