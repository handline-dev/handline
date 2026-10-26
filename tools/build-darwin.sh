#!/usr/bin/env bash
# Build a release's universal (arm64 + amd64) handlined and hl, for CI and local packaging: tools/build-darwin.sh <version> [outdir]
# Both are stamped <version>+<sha> (-dirty when the tree has changes), which `handlined -version` prints and `handlined service`
# names the version's folder by. outdir defaults to dist/darwin.
version="$1"
if [ -z "$version" ] || [ $# -gt 2 ] || [[ "$version" =~ [^A-Za-z0-9._-] ]]; then
    echo "usage: tools/build-darwin.sh <version> [outdir]  (version: letters, digits, . _ -)" >&2
    exit 2
fi
top="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
out="${2:-$top/dist/darwin}"
sha="$(git -C "$top" rev-parse --short HEAD)" || exit 1
[ -z "$(git -C "$top" status --porcelain)" ] || sha="$sha-dirty"
stamp="$version+$sha"
cd "$top/go" || exit 1
. "$top/tools/go-env.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/build-darwin.XXXXXX")" || exit 1
trap 'rm -rf "$work"' EXIT
for arch in arm64 amd64; do
    for cmd in handlined hl; do
        GOOS=darwin GOARCH="$arch" CGO_ENABLED=0 go build -trimpath \
            -ldflags "-X github.com/handline/handline/go/internal/version.Sha=$stamp" -o "$work/$cmd-$arch" "./cmd/$cmd" || exit 1
    done
done
mkdir -p "$out" || exit 1
for cmd in handlined hl; do
    lipo -create -output "$out/$cmd" "$work/$cmd-arm64" "$work/$cmd-amd64" || exit 1
done
[ "$("$out/handlined" -version)" = "$stamp" ] || { echo "tools/build-darwin.sh: $out/handlined does not say $stamp" >&2; exit 1; }
echo "$out: handlined, hl $stamp (arm64 + amd64)"
