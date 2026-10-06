// Package version holds the build's identity: the git sha the binary was made from.
package version

// Sha is stamped at build time: -ldflags "-X github.com/handline/handline/go/internal/version.Sha=$(git rev-parse --short HEAD)".
var Sha = "unknown"
