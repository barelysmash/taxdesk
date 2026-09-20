#!/usr/bin/env bash
#
# taxdesk ship  —  runs ON rosencrantz (Git Bash / MINGW64)
#
# branch -> commit -> test -> push -> pull request -> squash merge -> cleanup
#
#   bash scripts/ship.sh -m "Short subject line"
#   bash scripts/ship.sh -F msg.txt                # message from a file
#   bash scripts/ship.sh -m "Fix thing" --dry-run  # show the plan, change nothing
#   bash scripts/ship.sh -m "Fix thing" --no-merge # stop at the open PR
#
# Exists because every push to this repo has been bypassing the ruleset that
# requires changes to arrive through a pull request. Admin rights make the
# bypass silent, so the rule was doing nothing. This satisfies it instead.
#
# check.sh runs TWICE on purpose: once before the commit, and once more on the
# merged result. The second run is the one that matters — a branch that passes
# alone can still break master if something else landed meanwhile.
#
set -uo pipefail

REPO="${TAXDESK_REPO:-/c/bSmash-dev/taxdesk}"
BASE="${TAXDESK_BASE:-master}"
CHECK="$REPO/scripts/check.sh"

MSG=""
MSG_FILE=""
BRANCH=""
DRY=0
DO_MERGE=1
ADD_ALL=0

say()  { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '\033[1;33m   ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m   x %s\033[0m\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--message) MSG="${2:?-m needs a message}"; shift ;;
    -F|--file)    MSG_FILE="${2:?-F needs a path}"; shift ;;
    -b|--branch)  BRANCH="${2:?-b needs a name}"; shift ;;
    -a|--all)     ADD_ALL=1 ;;
    --dry-run)    DRY=1 ;;
    --no-merge)   DO_MERGE=0 ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown flag: $1" ;;
  esac
  shift
done

cd "$REPO" || die "cannot cd to $REPO"

# ---------------------------------------------------------------- 0. preflight
say "preflight"
[[ -d .git ]] || die "not a git repo: $REPO"
command -v gh >/dev/null || die "gh not on PATH; install the GitHub CLI"
gh auth status >/dev/null 2>&1 || die "gh not authenticated; run: gh auth login"
[[ -f $CHECK ]] || die "missing $CHECK"
info "repo   $REPO"
info "base   $BASE"

if [[ -z $MSG && -z $MSG_FILE ]]; then
  die "no commit message; pass -m \"subject\" or -F msg.txt"
fi
if [[ -n $MSG_FILE ]]; then
  [[ -f $MSG_FILE ]] || die "no such file: $MSG_FILE"
  SUBJECT=$(head -1 "$MSG_FILE")
else
  SUBJECT=$MSG
fi
info "subject $SUBJECT"

# Stray downloads in the repo root have been swept into commits twice.
mapfile -t strays < <(ls -1 ./*.py ./*.sh ./*.sql ./*.jsx ./*.zip ./*.xlsx ./*.csv 2>/dev/null || true)
if ((${#strays[@]})); then
  warn "loose files in the repo root:"
  for f in "${strays[@]}"; do warn "    $f"; done
  warn "these are almost always browser downloads that belong in a subdirectory"
  read -r -p "   continue anyway? [y/N] " a
  [[ ${a,,} == y ]] || die "stopped"
fi

# ------------------------------------------------------------------ 1. changes
say "changes"
if ((ADD_ALL)); then
  git add -A
fi
if git diff --cached --quiet && git diff --quiet; then
  die "nothing to commit; stage changes first or pass --all"
fi
if git diff --cached --quiet; then
  info "nothing staged; staging tracked modifications"
  git add -u
fi
git diff --cached --stat | sed 's/^/   /'

# ------------------------------------------------------------------- 2. branch
if [[ -z $BRANCH ]]; then
  slug=$(printf '%s' "$SUBJECT" | tr '[:upper:]' '[:lower:]' \
         | sed 's/[^a-z0-9]\+/-/g; s/^-//; s/-$//' | cut -c1-40)
  BRANCH="ship/${slug:-change}-$(date +%m%d-%H%M)"
fi
say "branch"
info "$BRANCH"

if ((DRY)); then
  say "dry run"
  info "would branch from $BASE, commit, run check.sh, push, open a PR"
  ((DO_MERGE)) && info "would squash-merge and delete the branch"
  exit 0
fi

CURRENT=$(git rev-parse --abbrev-ref HEAD)
[[ $CURRENT == "$BASE" ]] || warn "starting from $CURRENT, not $BASE"
BASE_SHA=$(git rev-parse "$BASE")
git switch -c "$BRANCH" >/dev/null 2>&1 || die "could not create branch $BRANCH"

# ------------------------------------------------------------------- 3. commit
say "commit"
if [[ -n $MSG_FILE ]]; then
  git commit -F "$MSG_FILE" -q || die "commit failed"
else
  git commit -m "$MSG" -q || die "commit failed"
fi
info "$(git log -1 --format='%h %s')"

# --------------------------------------------------------------------- 4. test
say "test (branch)"
if ! bash "$CHECK"; then
  warn "invariants failed — the commit stands on $BRANCH but nothing was pushed"
  warn "fix, commit again, then re-run with: -b $BRANCH"
  exit 1
fi

# --------------------------------------------------------------------- 5. push
say "push"
git push -u origin "$BRANCH" -q || die "push failed"
info "pushed $BRANCH"

# ----------------------------------------------------------------------- 6. PR
say "pull request"
if [[ -n $MSG_FILE ]]; then
  PR_URL=$(gh pr create --base "$BASE" --head "$BRANCH" \
           --title "$SUBJECT" --body-file "$MSG_FILE" 2>&1 | tail -1)
else
  PR_URL=$(gh pr create --base "$BASE" --head "$BRANCH" \
           --title "$SUBJECT" --body "$SUBJECT" 2>&1 | tail -1)
fi
[[ $PR_URL == https://* ]] || die "could not open a PR: $PR_URL"
info "$PR_URL"

if ((DO_MERGE == 0)); then
  say "stopped before merge"
  info "merge it yourself with: gh pr merge --squash --delete-branch"
  exit 0
fi

# -------------------------------------------------------------------- 7. merge
say "squash merge"
gh pr merge "$PR_URL" --squash --delete-branch --admin >/dev/null 2>&1 \
  || gh pr merge "$PR_URL" --squash --delete-branch >/dev/null \
  || die "merge failed; the PR is open at $PR_URL"
info "merged and branch deleted"

git switch "$BASE" -q || die "could not return to $BASE"

# Fatal, not a warning. The merge happened on the remote, so without this pull
# the local $BASE is still the pre-merge commit — and the check below would
# then pass against code that is not what was merged. A false green here is
# worse than no check at all.
if ! git pull --ff-only -q 2>/dev/null; then
    die "merged on the remote but could not fast-forward local $BASE.
       Run: git -C $REPO pull --ff-only
       Then re-run check.sh before deploying. Nothing is lost — the merge is
       on origin — but the local tree does not yet match it."
fi
git branch -D "$BRANCH" >/dev/null 2>&1 || true

# If $BASE has not moved, the merge did not land however cheerfully the
# previous step reported. Without this the script says "shipped" over an
# unchanged tree, and the check below passes because it is testing the same
# code that was already there.
if [[ "$(git rev-parse "$BASE")" == "$BASE_SHA" ]]; then
    die "$BASE is still at ${BASE_SHA:0:7} — the squash merge did not land.
       Check the pull request, and do not deploy."
fi
info "$(git log -1 --format='%h %s')"

# -------------------------------------------------------- 8. test the result
# The branch passing alone is not the same as master passing. This is the run
# that decides whether what is now on master is sound.
say "test (merged $BASE)"
if ! bash "$CHECK"; then
  die "invariants FAIL on $BASE after merge — fix before deploying"
fi

say "shipped"
info "$BASE is at $(git rev-parse --short HEAD)"
info "deploy with: bash scripts/deploy.sh <paths>"
