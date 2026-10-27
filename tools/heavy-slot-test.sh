#!/usr/bin/env bash
# A mutated slot helper in a worktree must not be the one that takes part in the shared queue (T6736). Works on a copy: HEAD archived into a
# temp git repo whose origin/main is the pristine tree, with the copy's cmd/heavyslot then broken so it never lets a run through. The copy's
# tools/heavy-slot.sh must still run a command (on the helper built from origin/main), keep at most 3 pinned helpers, and say when it
# falls back. Own slot and bin dirs; nothing of this worktree is touched.
set -u
top="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
copy="$tmp/copy"; mkdir "$copy"
git -C "$top" archive HEAD go | tar -x -C "$copy"; cp -R "$top/tools" "$copy/tools"   # the wrapper as it is now, the helper as committed
(cd "$copy" && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -qm base && git update-ref refs/remotes/origin/main HEAD)
sed -i.bak 's/^func main() {$/func main() { select {}/' "$copy/go/cmd/heavyslot/main.go"
grep -q 'select {}' "$copy/go/cmd/heavyslot/main.go" || { echo "FAIL: could not mutate the copy's helper"; exit 1; }
export HANDLINE_HEAVY_DIR="$tmp/slots" HANDLINE_HEAVY_BIN="$tmp/bin" HANDLINE_HEAVY_SLOTS=1 TMPDIR="$tmp"
out="$(timeout 300 "$copy/tools/heavy-slot.sh" echo ran 2>&1)"
[ "$out" = "ran" ] || { echo "FAIL: the mutated worktree helper took part in the queue: $out"; exit 1; }
ls "$tmp/bin"/heavyslot-* >/dev/null 2>&1 || { echo "FAIL: no pinned helper was built"; exit 1; }
ls -d "$tmp"/heavyslot-src.* >/dev/null 2>&1 && { echo "FAIL: the temp source was left behind"; exit 1; }
for n in 1 2 3 4 5; do : > "$tmp/bin/heavyslot-old$n"; touch -t 20200101000$n "$tmp/bin/heavyslot-old$n"; done
git -C "$copy" -c user.name=t -c user.email=t@t commit -q --allow-empty -m next && git -C "$copy" update-ref refs/remotes/origin/main HEAD
timeout 300 "$copy/tools/heavy-slot.sh" echo ran >/dev/null 2>&1
n="$(ls "$tmp/bin"/heavyslot-* | wc -l | tr -d ' ')"
[ "$n" = 3 ] || { echo "FAIL: $n pinned helpers kept, want 3"; exit 1; }
out="$(HANDLINE_HEAVY_LOCAL=1 timeout 20 "$copy/tools/heavy-slot.sh" echo ran 2>&1)"
[ "$out" = "ran" ] && { echo "FAIL: HANDLINE_HEAVY_LOCAL=1 did not use the worktree helper"; exit 1; }
# a TERM during the pinned build ends the wrapper non-zero, and the command never runs (the fallback must not run what was told to stop)
mkdir "$tmp/stub"   # a go whose build takes 3 s and yields a helper that just runs the command, so a wrapper that goes on after the TERM would run it
cat > "$tmp/stub/go" <<'GO'
#!/bin/sh
[ "$1" = env ] && { echo "$STUB_CACHE"; exit 0; }
if [ "$1" = build ]; then sleep 3; printf '#!/bin/sh\nshift\nexec "$@"\n' > "$3"; chmod +x "$3"; fi
exit 0
GO
chmod +x "$tmp/stub/go"; export STUB_CACHE="$tmp/gocache"
git -C "$copy" -c user.name=t -c user.email=t@t commit -q --allow-empty -m third && git -C "$copy" update-ref refs/remotes/origin/main HEAD
PATH="$tmp/stub:$PATH" "$copy/tools/heavy-slot.sh" touch "$tmp/ran" >/dev/null 2>&1 &
wpid=$!; sleep 1; kill -TERM "$wpid"; wait "$wpid" 2>/dev/null; rc=$?
[ "$rc" != 0 ] && [ ! -e "$tmp/ran" ] || { echo "FAIL: a TERM during the build exited $rc, command ran: $([ -e "$tmp/ran" ] && echo yes || echo no)"; exit 1; }
ls -d "$tmp"/heavyslot-src.* >/dev/null 2>&1 && { echo "FAIL: the temp source was left after a TERM"; exit 1; }
rm -rf "$copy/.git"
out="$(timeout 20 "$copy/tools/heavy-slot.sh" echo ran 2>&1)"
case "$out" in *"origin/main could not be read or built"*) ;; *) echo "FAIL: the fallback was silent: $out"; exit 1 ;; esac
echo "ok: a mutated worktree helper stays out of the queue, 3 pinned helpers are kept, the fallback says so"
