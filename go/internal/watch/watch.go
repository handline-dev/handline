// Package watch is what a gate task can watch: one Vendor per outside tool, each answering only waiting, passed, failed or gone with a
// line of evidence. The gate state machine (package gates) holds no vendor code; a vendor holds no gate state.
package watch

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
)

// Verdict is a vendor's answer on one read.
type Verdict string

const (
	Waiting Verdict = "waiting"
	Passed  Verdict = "passed"
	Failed  Verdict = "failed"
	// Gone is a target that is deleted or that we can no longer see; a gate opens as failed only after GoneReads of them in a row.
	Gone Verdict = "gone"
)

// GoneReads is how many gone answers in a row open a gate as failed.
const GoneReads = 3

// ReadyWait is how long Resolve waits for a vendor to say it is set up.
const ReadyWait = 20 * time.Second

// Vendor is one outside tool. Untils lists what it can wait for, the default first. Ready says whether it is set up, and why not. Parse
// checks a target for an until without reading the tool, and answers it written the vendor's one way, with the label people read
// ("PR #418 CI"); its error refuses the watch. Check reads the target: an error means it could not be read, never a verdict.
type Vendor interface {
	Untils() []string
	Ready(ctx context.Context) (ok bool, why string)
	Parse(target, until string) (canon, label string, err error)
	Check(ctx context.Context, target, until string) (Verdict, string, error)
}

// Vendors are the tools a gate can watch, by the name a watch gives.
var Vendors = map[string]Vendor{"github": &GitHub{}}

// Parse is a watch made whole without reading anything: the vendor known, the until defaulted, and the target and label as the vendor
// writes them.
func Parse(vendor, target, until string) (db.Watch, error) {
	v, ok := Vendors[vendor]
	if !ok {
		return db.Watch{}, fmt.Errorf("no vendor %q: a watch's vendor is one of %s", vendor, strings.Join(names(), ", "))
	}
	untils := v.Untils()
	if until == "" {
		until = untils[0]
	}
	if !slices.Contains(untils, until) {
		return db.Watch{}, fmt.Errorf("%s waits for %s, not %q", vendor, strings.Join(untils, ", "), until)
	}
	canon, label, err := v.Parse(target, until)
	if err != nil {
		return db.Watch{}, err
	}
	return db.Watch{Vendor: vendor, Target: canon, Until: until, Label: label}, nil
}

// Ready refuses a watch whose vendor is not set up.
func Ready(ctx context.Context, w db.Watch) error {
	ctx, cancel := context.WithTimeout(ctx, ReadyWait)
	defer cancel()
	if ok, why := Vendors[w.Vendor].Ready(ctx); !ok {
		return fmt.Errorf("%s is not set up: %s", w.Vendor, why)
	}
	return nil
}

func names() []string {
	var out []string
	for n := range Vendors {
		out = append(out, n)
	}
	slices.Sort(out)
	return out
}
