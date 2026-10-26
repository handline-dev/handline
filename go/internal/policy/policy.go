// Package policy holds how long things are kept or wait, in one place. Each value is a duration and says what it times; the packages
// that apply one import it, so a change of policy is one edit here (policy_test.go pins them). Limits that are the founder's
// settings (load, memory, heavy slots) are not here: they live in config.yaml.
package policy

import "time"

const (
	// StreamKeep is how long an event stays in the stream log for a client that resumes (rows are also capped by count).
	StreamKeep = 7 * 24 * time.Hour
	// ReplyKeep is how long a write's reply and effects are kept for a retry with the same request id.
	ReplyKeep = 24 * time.Hour
	// GateDeadline is a gate's time box when none is given.
	GateDeadline = 24 * time.Hour
	// HRSettledShown is how long a settled HR proposal stays in the HR list.
	HRSettledShown = 7 * 24 * time.Hour
	// ReadSayShown is how long a read say stays in the founder's inbox list, greyed, so it can be found again.
	ReadSayShown = 7 * 24 * time.Hour
	// HookKeep is how long a recorded hook event is kept.
	HookKeep = 24 * time.Hour
	// CreateOperationKeep is how long a finished agent.create operation is kept.
	CreateOperationKeep = 7 * 24 * time.Hour
	// UploadOpen is how long an upload may stay open before it is dropped.
	UploadOpen = time.Hour
	// ImageUnsent is how long an uploaded image that no message names is kept.
	ImageUnsent = 24 * time.Hour
	// ImageKept is how long an image is kept after the message that names it, or the last task that names it closes.
	ImageKept = 30 * 24 * time.Hour
)

// Seconds is d in whole seconds, for the stores that count in them.
func Seconds(d time.Duration) int64 { return int64(d / time.Second) }
