#!/usr/bin/env bash
#
# taxdesk pull  —  runs ON rosencrantz (Git Bash / MINGW64)
#
# Streams scripts/refresh.sh to guildenstern over stdin, runs it there, and
# brings the export tarball back. Nothing is installed on the remote, so the
# version in this repo is always the version that runs.
#
#   ./scripts/pull.sh                # auto: full if ratchet detected
#   ./scripts/pull.sh --full         # force full backfill
#   ./scripts/pull.sh --no-scrape    # export current DB, don't hit Socrata
#
set -euo pipefail

REMOTE="${TAXDESK_REMOTE:-barelysmash@100.113.110.44}"
BUNDLE="${TAXDESK_BUNDLE:-/tmp/taxdesk-export.tgz}"
DEST="${TAXDESK_DEST:-$HOME/Downloads}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m   x %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$HERE/refresh.sh" ]] || die "refresh.sh not found beside pull.sh"
mkdir -p "$DEST"

# Two ssh operations total, so at most two passphrase prompts. Load the key into
# ssh-agent first and it drops to zero:
#   eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519
say "running refresh on $REMOTE"
ssh "$REMOTE" "sudo bash -s -- $*" < "$HERE/refresh.sh"

say "pulling export"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$DEST/taxdesk-export-$STAMP.tgz"
scp "$REMOTE:$BUNDLE" "$OUT"

say "done"
printf '   %s (%s)\n' "$OUT" "$(du -h "$OUT" | cut -f1)"
printf '   upload that file to rebuild the workbook\n'
