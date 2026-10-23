#!/usr/bin/env bash
# Build the Go daemon, one binary with the git sha stamped in and no cgo: tools/build-go.sh [output]
top="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
sha="$(git -C "$top" rev-parse --short HEAD)" || exit 1
cd "$top/go" || exit 1
. "$top/tools/go-env.sh"
out="${1:-$top/go/handlined}"
CGO_ENABLED=0 go build -trimpath -ldflags "-X github.com/handline/handline/go/internal/version.Sha=$sha" \
    -o "$out" ./cmd/handlined || exit 1
# On macOS, sign with a fixed identifier and a designated requirement naming only it: the default ad-hoc requirement is the binary's
# hash, so each build was a new program to macOS and lost its Files and Folders grants. HANDLINED_SIGN_ID names a keychain identity
# to sign with instead of ad-hoc.
command -v codesign >/dev/null || exit 0
id=dev.handline.handlined
if [ -n "$HANDLINED_SIGN_ID" ]; then
    codesign -f -s "$HANDLINED_SIGN_ID" -i "$id" "$out"
else
    codesign -f -s - -i "$id" -r="designated => identifier \"$id\"" "$out"
fi
