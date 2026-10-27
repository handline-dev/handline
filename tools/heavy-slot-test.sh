#!/usr/bin/env bash
# A mutated slot helper in a worktree must not be the one that takes part in the shared queue (T6736): break go/cmd/heavyslot so it never
# lets a run through, and see tools/heavy-slot.sh still run a command, on the helper built from origin/main. Uses its own slot and bin dirs.
set -u
top="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"; git -C "$top" checkout -q -- go/cmd/heavyslot/main.go' EXIT
export HANDLINE_HEAVY_DIR="$tmp/slots" HANDLINE_HEAVY_BIN="$tmp/bin" HANDLINE_HEAVY_SLOTS=1
sed -i.bak 's/^func main() {$/func main() { select {}/' "$top/go/cmd/heavyslot/main.go" && rm -f "$top/go/cmd/heavyslot/main.go.bak"
grep -q 'select {}' "$top/go/cmd/heavyslot/main.go" || { echo "FAIL: could not mutate the helper"; exit 1; }
out="$(timeout 300 "$top/tools/heavy-slot.sh" echo ran 2>&1)"
[ "$out" = "ran" ] || { echo "FAIL: the mutated worktree helper took part in the queue: $out"; exit 1; }
ls "$tmp/bin"/heavyslot-* >/dev/null 2>&1 || { echo "FAIL: no pinned helper was built"; exit 1; }
out="$(HANDLINE_HEAVY_LOCAL=1 timeout 20 "$top/tools/heavy-slot.sh" echo ran 2>&1)"
[ "$out" = "ran" ] && { echo "FAIL: HANDLINE_HEAVY_LOCAL=1 did not use the worktree helper"; exit 1; }
echo "ok: the worktree helper is used only with HANDLINE_HEAVY_LOCAL=1"
