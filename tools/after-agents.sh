#!/usr/bin/env bash
# The founder's steps for when every agent is idle: close the sandbox gaps in the shared settings.
#
#   tools/after-agents.sh            show what would change and what is still open (changes nothing)
#   tools/after-agents.sh --apply    back the settings up, then make the changes
#
# Run it from your own terminal, not from an agent: the settings file is the founder's.
# It changes two things in ~/.orcapipe/shared/.claude/settings.json (HANDLINE_HOME or OCP_HOME if set):
#   - removes the ocp entries from sandbox.excludedCommands, so ocp runs inside the sandbox
#   - removes the main checkout and ~/.local/bin from permissions.additionalDirectories
# It leaves the sandbox write entry for the main checkout's .git alone: agents commit through it.
# Sessions already running keep the old settings; relaunch them afterwards (hl close on each idle one).
set -euo pipefail

apply=0
[ "${1:-}" = "--apply" ] && apply=1
home="${HANDLINE_HOME:-${OCP_HOME:-}}"
if [ -z "$home" ]; then
    if [ -d "$HOME/.handline" ]; then home="$HOME/.handline"; else home="$HOME/.orcapipe"; fi
fi
settings="$home/shared/.claude/settings.json"
[ -f "$settings" ] || { echo "no settings file at $settings" >&2; exit 1; }

busy="$(hl ps | awk 'NR>3 && $1!="AGENT" && $3!="IDLE" && $3!="PENDING" && $3!="" && $1 ~ /^[A-Za-z0-9._-]+$/ && $3!="CLOSED" {print $1" "$3}' | head -20 || true)"
if [ -n "$busy" ]; then
    echo "These agents are not idle. Wait for them, or accept that their sessions keep the old settings:"
    echo "$busy" | sed 's/^/  /'
fi

python3 - "$settings" "$apply" <<'PY'
import json, shutil, sys
path, apply = sys.argv[1], sys.argv[2] == "1"
data = json.load(open(path))
sb = data.get("sandbox", {})
perm = data.get("permissions", {})
excluded = sb.get("excludedCommands", [])
dirs = perm.get("additionalDirectories", [])
drop_ex = [e for e in excluded if isinstance(e, str) and e.split(" ", 1)[0] in ("hl", "handline", "ocp")]
home_bin = {d for d in dirs if d.endswith("/.local/bin") or d.endswith("/workspaces/orcapipe")}
print("excludedCommands to remove:", drop_ex or "none")
print("additionalDirectories to remove:", sorted(home_bin) or "none")
if not (drop_ex or home_bin):
    print("settings already closed")
    sys.exit(0)
if not apply:
    print("(dry run: nothing changed; add --apply)")
    sys.exit(0)
shutil.copy2(path, path + ".bak")
sb["excludedCommands"] = [e for e in excluded if e not in drop_ex]
perm["additionalDirectories"] = [d for d in dirs if d not in home_bin]
json.dump(data, open(path, "w"), indent=2)
open(path, "a").write("\n")
print("done; backup at", path + ".bak")
PY

echo
echo "Still manual, in this order:"
echo "  1. Relaunch the team: for a in \$(hl ps | awk '\$3==\"IDLE\"{print \$1}'); do hl close \"\$a\"; done"
echo "  2. hl doctor            (expect: ocp runs inside the agents' sandbox)"
echo "  3. Ask one relaunched agent to run the probe from the rollout guide."
echo "  4. The rename to Handline is planned, not built: nothing to run for it yet."
