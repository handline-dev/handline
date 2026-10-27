package housekeeping

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/sysres"
)

const (
	GB = int64(1) << 30
	// DiskLow is the free space under which the founder is told once (a notifications disk_low event, which a client shows as a toast beside
	// its status-bar warning; never a task). It is not told again until free space is back at DiskLow plus DiskMargin, so a volume that
	// hovers at the line does not tell again and again.
	DiskLow       = 10 * GB
	DiskMargin    = 2 * GB
	DiskEvery     = int64(600)
	diskToldLevel = "disk_low_told"
	// diskFullSince and diskFullFree are the cursors of the needs-you item kept while free space is under the floor: since when, and the
	// free bytes at the last check.
	diskFullSince = "disk_full_since"
	diskFullFree  = "disk_full_free"
	// DefaultFloor is the free space under which builds wait when the caller names none.
	DefaultFloor = 5 * GB
	// DiskLowEvent is the events row the notifications topic turns into a disk_low notification.
	DiskLowEvent = "disk_low"
)

// DiskWatch tells the founder when the volume the home is on runs low on free space. It reads and tells; it never deletes.
type DiskWatch struct {
	Home string
	Log  func(string)
	// Free is the free and total bytes of the volume holding a path; nil is statfs.
	Free func(path string) (free, total int64, err error)
	// Floor is the free bytes under which builds and tests wait and the founder has a needs-you item; nil is DefaultFloor.
	Floor func() int64

	checked int64
}

// Check runs at most every DiskEvery seconds. That the founder was told is kept in the database, so a restart does not tell again.
func (w *DiskWatch) Check(q db.Q, now int64) error {
	if w.checked != 0 && now-w.checked < DiskEvery {
		return nil
	}
	w.checked = now
	free, total, err := w.free()(w.Home)
	if err != nil {
		return err
	}
	if err := w.hold(q, now, free); err != nil {
		return err
	}
	told, err := db.Cursor(q, diskToldLevel)
	if err != nil {
		return err
	}
	if told != 0 && free >= DiskLow+DiskMargin {
		w.Log(fmt.Sprintf("disk: %d GB free again", free/GB))
		return db.ClearCursor(q, diskToldLevel)
	}
	if told != 0 || free >= DiskLow {
		return nil
	}
	if _, err := db.AddEvent(q, "", DiskLowEvent, db.KV{K: "free_bytes", V: free}, db.KV{K: "total_bytes", V: total}); err != nil {
		return err
	}
	w.Log(fmt.Sprintf("disk: %d GB free - told once", free/GB))
	return db.SetCursor(q, diskToldLevel, 1)
}

func (w *DiskWatch) free() func(string) (int64, int64, error) {
	if w.Free != nil {
		return w.Free
	}
	return sysres.StatfsFree
}

// hold keeps the needs-you item: raised once when free space falls under the floor, its figure refreshed each check, and taken away only
// once space is back over the floor plus DiskMargin, so a volume that hovers at the line neither repeats nor flickers it.
func (w *DiskWatch) hold(q db.Q, now, free int64) error {
	floor := DefaultFloor
	if w.Floor != nil {
		floor = w.Floor()
	}
	since, err := db.Cursor(q, diskFullSince)
	if err != nil {
		return err
	}
	switch {
	case since != 0 && free >= floor+DiskMargin:
		w.Log(fmt.Sprintf("disk: %d GB free, over the floor again", free/GB))
		if err := db.ClearCursor(q, diskFullSince); err != nil {
			return err
		}
		return db.ClearCursor(q, diskFullFree)
	case since == 0 && free < floor:
		w.Log(fmt.Sprintf("disk: %d GB free, under the floor of %d GB - the founder has a needs-you item", free/GB, floor/GB))
		if err := db.SetCursor(q, diskFullSince, now); err != nil {
			return err
		}
	case since == 0:
		return nil
	}
	return db.SetCursor(q, diskFullFree, free)
}

// DiskNeed is the needs-you item for a disk too full for builds: since when and with how many bytes free; ok false when there is none.
func DiskNeed(q db.Q) (since, free int64, ok bool) {
	since, err := db.Cursor(q, diskFullSince)
	if err != nil || since == 0 {
		return 0, 0, false
	}
	free, _ = db.Cursor(q, diskFullFree)
	return since, free, true
}

// DiskNeedText is the item in plain words.
func DiskNeedText(free int64) string {
	figure := fmt.Sprintf("%d MB", free/(GB/1024))
	if free >= GB {
		figure = fmt.Sprintf("%.1f GB", float64(free)/float64(GB))
	}
	return fmt.Sprintf("The disk is almost full (%s free). Builds and tests wait until space is freed.", figure)
}
