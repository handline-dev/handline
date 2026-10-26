// Package version holds the build's identity: the git sha the binary was made from, with the version for a release.
package version

import "runtime/debug"

// Sha is stamped at build time: -ldflags "-X github.com/handline/handline/go/internal/version.Sha=$(git rev-parse --short HEAD)"
// (tools/build-go.sh), or <version>+<sha>[-dirty] for a release (tools/build-darwin.sh).
// A plain `go build` in the checkout leaves it unstamped; init then takes the revision go records in the binary itself.
var Sha = "unknown"

func init() {
	if Sha == "unknown" {
		Sha = revision(debug.ReadBuildInfo())
	}
}

// revision is the commit go build recorded (vcs.revision), cut to 12 characters, or "unknown" when there is none.
func revision(info *debug.BuildInfo, ok bool) string {
	if ok {
		for _, s := range info.Settings {
			if s.Key == "vcs.revision" && s.Value != "" {
				return s.Value[:min(12, len(s.Value))]
			}
		}
	}
	return "unknown"
}
