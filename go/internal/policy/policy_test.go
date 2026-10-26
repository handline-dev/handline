package policy

import (
	"testing"
	"time"
)

// A value changes on purpose: this test is the place that says so.
func TestThePolicyValuesArePinned(t *testing.T) {
	const day = 24 * time.Hour
	for name, c := range map[string][2]time.Duration{
		"StreamKeep": {StreamKeep, 7 * day}, "ReplyKeep": {ReplyKeep, day}, "GateDeadline": {GateDeadline, day},
		"HRSettledShown": {HRSettledShown, 7 * day}, "ReadSayShown": {ReadSayShown, 7 * day}, "HookKeep": {HookKeep, day},
		"CreateOperationKeep": {CreateOperationKeep, 7 * day}, "UploadOpen": {UploadOpen, time.Hour},
		"ImageUnsent": {ImageUnsent, day}, "ImageKept": {ImageKept, 30 * day},
	} {
		if c[0] != c[1] {
			t.Errorf("%s is %v, was pinned at %v", name, c[0], c[1])
		}
	}
	if Seconds(StreamKeep) != 604800 {
		t.Error("a week is 604800 s")
	}
}
