package version

import (
	"runtime/debug"
	"testing"
)

func TestRevisionIsTheRecordedCommitCutShort(t *testing.T) {
	info := &debug.BuildInfo{Settings: []debug.BuildSetting{
		{Key: "vcs", Value: "git"},
		{Key: "vcs.revision", Value: "1435627631419a818781bb833bedcc2204997fdc"},
		{Key: "vcs.modified", Value: "true"},
	}}
	if got := revision(info, true); got != "143562763141" {
		t.Fatalf("revision = %q, want the commit's first 12 characters", got)
	}
}

func TestRevisionWithoutARecordedCommitIsUnknown(t *testing.T) {
	if got := revision(&debug.BuildInfo{}, true); got != "unknown" {
		t.Fatalf("no vcs settings: revision = %q", got)
	}
	if got := revision(nil, false); got != "unknown" {
		t.Fatalf("no build info: revision = %q", got)
	}
}
