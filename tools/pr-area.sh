#!/usr/bin/env bash
# Prints which area auditor takes a PR, from the paths it changes, and "ux" after it when the PR changes what an app draws.
#   tools/pr-area.sh N         reads gh pr diff N --name-only
#   tools/pr-area.sh -         reads the paths from stdin
# daemon: any go/ or contract/ path. Else web or phone, whichever has more paths (a tie goes to web):
# phone is apps/mobile; web is apps/web, apps/electron, packages/ and design/. Else tools (tools/, .github/, docs, the rest).
set -uo pipefail
case "${1:-}" in
    -) paths="$(cat)" ;;
    [0-9]*) paths="$(gh pr diff "$1" --name-only)" || exit 1 ;;
    *) echo "usage: tools/pr-area.sh <pr number> | -" >&2; exit 2 ;;
esac
count() { grep -cE "$1" <<<"$paths"; }

if [ "$(count '^(go|contract)/')" -gt 0 ]; then
    area=daemon
else
    phone="$(count '^apps/mobile/')"
    web="$(count '^(apps/(web|electron)|packages|design)/')"
    if [ "$phone" -gt 0 ] && [ "$phone" -gt "$web" ]; then area=phone
    elif [ "$web" -gt 0 ]; then area=web
    else area=tools
    fi
fi
ui="$(grep -E '^(apps/(web|mobile|electron)|packages/(app-ui|app-react|tokens))/' <<<"$paths" | grep -vcE '\.test\.|/(test|tests|__tests__|e2e)/')"
if [ "$ui" -gt 0 ]; then echo "$area ux"; else echo "$area"; fi
