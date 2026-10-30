#!/usr/bin/env bash
# Runs a branch of Handline (default story/prototype-apply) as a second app next to the installed one, on the live daemon and the founder's data.
#   tools/try-prototype.sh [branch]
# It keeps its own clone, its own app data (settings, device token, single-instance lock) and its own page port, so the installed app is
# neither touched nor replaced, and quitting this one leaves the daemon running. Run it again to bring the clone to the branch's head.
set -euo pipefail

branch="${1:-story/prototype-apply}"
dir="${HANDLINE_TRY_DIR:-$HOME/.local/share/handline/try-prototype}"
remote="$(git -C "$(dirname "$0")" remote get-url origin)"

if [ ! -d "$dir/.git" ]; then
  mkdir -p "$(dirname "$dir")"
  git clone --quiet "$remote" "$dir"
fi
git -C "$dir" fetch --quiet origin "$branch"
git -C "$dir" checkout --quiet --detach FETCH_HEAD
echo "prototype: $branch at $(git -C "$dir" log -1 --format='%h %s')"

cd "$dir"
pnpm install --frozen-lockfile --silent
pnpm --filter @handline/web build
cd apps/electron
node build.ts
# Connect mode attaches to the daemon already running; the page is served on a port the installed app does not use.
HANDLINE_MODE=connect HANDLINE_DATA="$HOME/.local/share/handline/try-prototype-data" HANDLINE_WEB_PORT="${HANDLINE_WEB_PORT:-17445}" \
  HANDLINE_BACKGROUND=0 exec pnpm exec electron .
