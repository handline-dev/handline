// Package housekeeping is what keeps a daemon that runs for weeks from filling the disk or running out of descriptors: log rotation, the
// descriptor count and a stack dump on SIGUSR1.
package housekeeping

import (
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

const (
	LogMaxBytes = 10 * 1024 * 1024
	LogBackups  = 5
)

// RotateLog is copy-truncate rotation (infra/service.rotate_log). launchd holds the log open with O_APPEND for the job's whole life, so
// renaming it would not move where the next write lands; truncating it in place does, at the cost of what is written between the copy
// and the truncate. False when the file is under max.
func RotateLog(path string, max int64, backups int) (bool, error) {
	st, err := os.Stat(path)
	if err != nil || st.Size() < max {
		return false, nil
	}
	name := func(n int) string { return fmt.Sprintf("%s.%d.gz", path, n) }
	if err := os.Remove(name(backups)); err != nil && !os.IsNotExist(err) {
		return false, err
	}
	for n := backups - 1; n >= 1; n-- {
		if err := os.Rename(name(n), name(n+1)); err != nil && !os.IsNotExist(err) {
			return false, err
		}
	}
	if err := gzipCopy(path, name(1)); err != nil {
		return false, err
	}
	f, err := os.OpenFile(path, os.O_WRONLY, 0)
	if err != nil {
		return false, err
	}
	defer f.Close()
	return true, f.Truncate(0)
}

func gzipCopy(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	zw := gzip.NewWriter(out)
	_, err = io.Copy(zw, in)
	if e := zw.Close(); err == nil {
		err = e
	}
	if e := out.Close(); err == nil {
		err = e
	}
	return err
}

// RotateLogs rotates the daemon's two logs under <home>/logs.
func RotateLogs(home string) error {
	var first error
	for _, name := range []string{"daemon.out.log", "daemon.err.log"} {
		if _, err := RotateLog(filepath.Join(home, "logs", name), LogMaxBytes, LogBackups); err != nil && first == nil {
			first = err
		}
	}
	return first
}
