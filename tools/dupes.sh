#!/usr/bin/env bash
# Fails when the apps and packages hold more copied code than today: the share of duplicated lines may only go down.
# Lower MAX when a slice of the list lands (jscpd: 18 clones, 0.68% of lines, 2026-10-15).
cd "$(dirname "$0")/.." || exit 1
MAX=0.7
exec npx --yes jscpd@4.0.5 apps packages --min-lines 8 --min-tokens 60 --threshold "$MAX" --silent \
    --ignore "**/node_modules/**,**/dist/**,**/test/**,**/*.json,**/fixtures.ts" --format typescript,tsx
