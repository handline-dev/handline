#!/usr/bin/env bash
# Switch the live launchd service from the Python daemon to the Go one (docs/daemon-plan.md "Switching the service").
#
#   tools/go-cutover.sh [--dry-run]
#
# Run it by the founder from a shell outside the agent sandbox (launchd, ~/Library/LaunchAgents and the daemon socket are denied inside it),
# with OCP_LAND_REPO set to the checkout `hl land` merges into. It reads no credential and makes no model call.
# --dry-run checks the preconditions and prints the steps; it changes nothing.
set -eu

dry=0
case "${1:-}" in
    --dry-run) dry=1 ;;
    "") ;;
    *) echo "usage: tools/go-cutover.sh [--dry-run]" >&2; exit 2 ;;
esac

top="$(cd "$(dirname "$0")/.." && pwd)"
fail() { echo "go-cutover: $*" >&2; exit 1; }

# --- (1) preconditions
# `hl service install` builds the job's PATH from the PATH of this shell plus python3's directory, and takes claude from this PATH.
command -v hl >/dev/null 2>&1 || fail "hl is not on PATH"
[ -z "${OCP_SERVICE_LABEL:-}" ] || fail "OCP_SERVICE_LABEL is set ($OCP_SERVICE_LABEL): that is a rehearsal's label, not the live service. Unset it."
[ -n "${OCP_LAND_REPO:-}" ] || fail "OCP_LAND_REPO is not set: export it as the checkout hl land merges into"
git -C "$OCP_LAND_REPO" rev-parse --git-dir >/dev/null 2>&1 || fail "OCP_LAND_REPO=$OCP_LAND_REPO is not a git checkout"
command -v python3 >/dev/null 2>&1 || fail "python3 is not on PATH: the land verbs run tools/localrefs.py with it"
command -v claude >/dev/null 2>&1 || fail "claude is not on PATH: the Go job takes it as -structured-cli"
[ -x "$top/go/handlined" ] || fail "$top/go/handlined is not built: run tools/build-go.sh"

steps="  2. record the current daemon (hl service status)
  3. hl service use go; hl service install --allow-live-home
  4. verify: daemon: go, loaded, a pid, no crash-loop warning, hl ps answers; again after 10 s
  5. on a failure after 3: hl service use python; hl service install; verify daemon: python and hl ps"
if [ "$dry" = 1 ]; then
    echo "go-cutover --dry-run: preconditions hold (OCP_LAND_REPO=$OCP_LAND_REPO, python3=$(command -v python3), claude=$(command -v claude), $top/go/handlined built)."
    echo "Steps:"
    echo "$steps"
    exit 0
fi

# --- (2) where we came from
before="$(hl service status)" || fail "hl service status failed: $before"
echo "$before"
was="$(printf '%s\n' "$before" | sed -n 's/^daemon: *//p')"
[ -n "$was" ] || fail "hl service status shows no daemon line"
echo "go-cutover: coming from daemon: $was"

# verify_daemon <python|go>: the service is chosen, loaded, has a pid, is not crash-looping, and hl ps answers.
verify_daemon() {
    want="$1"
    st="$(hl service status 2>&1)" || { echo "hl service status failed: $st"; return 1; }
    printf '%s\n' "$st"
    printf '%s\n' "$st" | grep -q "^daemon: *$want\$" || { echo "daemon is not $want"; return 1; }
    printf '%s\n' "$st" | grep -q "^loaded: *True\$" || { echo "the job is not loaded"; return 1; }
    printf '%s\n' "$st" | grep -q "^pid: *[0-9][0-9]*\$" || { echo "the job has no pid"; return 1; }
    ! printf '%s\n' "$st" | grep -q "crash-looping" || { echo "the job is crash-looping"; return 1; }
    hl ps >/dev/null 2>&1 || { echo "hl ps does not answer"; return 1; }
}

rollback() {
    echo "go-cutover: FAILED: $1" >&2
    echo "go-cutover: rolling back to python" >&2
    hl service use python && hl service install || echo "go-cutover: the rollback install itself failed" >&2
    sleep 5
    if verify_daemon python; then
        echo "go-cutover: rolled back; the service runs the Python daemon again." >&2
    else
        echo "go-cutover: ROLLBACK DID NOT VERIFY. Look at: hl service status; hl service logs --err" >&2
    fi
    exit 1
}

# --- (3) the swap
hl service use go || rollback "hl service use go"
hl service install --allow-live-home || rollback "hl service install --allow-live-home"

# --- (4) verify, twice, so a respawn loop shows
sleep 5
why="$(verify_daemon go)" || rollback "first check: $why"
echo "go-cutover: first check ok; waiting 10 s for a respawn"
sleep 10
why="$(verify_daemon go)" || rollback "second check: $why"

# --- (6)
cat <<EOF

go-cutover: the service runs the Go daemon (was: $was).

One step is left, and only you can do it: prove the peer inbox on a real terminal agent.
  1. Pick an agent with a live terminal session (hl ps) and leave its terminal window visible: <agent>.
  2. hl say <agent> "cutover check: reply with the word seen"
  3. The text should appear in that agent's terminal within seconds, typed by the peer inbox. Then:
       hl inbox <agent> --all     # the message is listed
       hl ps                      # the agent is not held ("no live session" / "peer_inbox is off")
       hl service logs --err      # no "peer inbox" delivery error lines
  Nothing in the terminal, or a held reason in hl ps, means the peer inbox is not working: roll back.

Rollback, any time:
  hl service use python && hl service install
  hl service status          # daemon: python, loaded, a new pid
  hl ps
EOF
