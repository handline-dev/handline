package housekeeping

import (
	"errors"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	// Near is the share of the soft limit past which the inventory is logged, once per crossing.
	Near = 0.8
	// ExhaustionEvery is how often running out of descriptors is said.
	ExhaustionEvery = time.Minute
)

// FDs is daemon/fds.py: the open-descriptor count, logged when it first reads and whenever it moves by a twentieth of the limit, and named
// by type the first time it is past Near of the soft limit. A daemon that ran out of descriptors answered nothing and said nothing for
// 18 minutes; this is how the next one names what holds them.
type FDs struct {
	Log func(string)
	Now func() time.Time // nil is time.Now

	mu        sync.Mutex
	near      bool
	last      int
	haveLast  bool
	said      time.Time
	listDir   string // where the descriptors are listed; /dev/fd
	softLimit func() uint64
}

func (f *FDs) now() time.Time {
	if f.Now != nil {
		return f.Now()
	}
	return time.Now()
}

func (f *FDs) dir() string {
	if f.listDir != "" {
		return f.listDir
	}
	return "/dev/fd"
}

func (f *FDs) limit() uint64 {
	if f.softLimit != nil {
		return f.softLimit()
	}
	var r syscall.Rlimit
	if syscall.Getrlimit(syscall.RLIMIT_NOFILE, &r) != nil || r.Cur == 0 || r.Cur > 1<<20 {
		return 1 << 20
	}
	return uint64(r.Cur)
}

// IsExhaustion is whether err is the process or the machine being out of descriptors.
func IsExhaustion(err error) bool {
	return errors.Is(err, syscall.EMFILE) || errors.Is(err, syscall.ENFILE)
}

func (f *FDs) listed() ([]int, error) {
	d, err := os.Open(f.dir())
	if err != nil {
		return nil, err
	}
	defer d.Close()
	names, err := d.Readdirnames(-1)
	if err != nil {
		return nil, err
	}
	var out []int
	for _, n := range names {
		if fd, err := strconv.Atoi(n); err == nil {
			out = append(out, fd)
		}
	}
	return out, nil
}

func kind(fd int) string {
	var st syscall.Stat_t
	if syscall.Fstat(fd, &st) != nil {
		return "closed"
	}
	switch st.Mode & syscall.S_IFMT {
	case syscall.S_IFSOCK:
		return "socket"
	case syscall.S_IFIFO:
		return "pipe"
	case syscall.S_IFCHR:
		return "char"
	case syscall.S_IFDIR:
		return "dir"
	}
	return "file"
}

// Check runs once per tick and never fails: it runs when descriptors are scarcest. It returns the count, -1 when it could not be read.
func (f *FDs) Check() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	fds, err := f.listed()
	if err != nil {
		f.sayExhaustion(err, "counting descriptors")
		return -1
	}
	n := len(fds) - 1 // the listing's own descriptor
	if n < 0 {
		n = 0
	}
	limit := int(f.limit())
	if !f.haveLast || abs(n-f.last) >= max(limit/20, 1) {
		f.last, f.haveLast = n, true
		f.Log(fmt.Sprintf("  fds: %d/%d", n, limit))
	}
	if float64(n) < Near*float64(limit) {
		f.near = false
	} else if !f.near {
		f.near = true
		count := map[string]int{}
		for _, fd := range fds {
			count[kind(fd)]++
		}
		kinds := make([]string, 0, len(count))
		for k := range count {
			kinds = append(kinds, k)
		}
		sort.Slice(kinds, func(i, j int) bool {
			return count[kinds[i]] > count[kinds[j]] || (count[kinds[i]] == count[kinds[j]] && kinds[i] < kinds[j])
		})
		parts := make([]string, len(kinds))
		for i, k := range kinds {
			parts[i] = fmt.Sprintf("%s %d", k, count[k])
		}
		f.Log("  fds: near the limit - " + strings.Join(parts, ", "))
	}
	return n
}

// SayExhaustion says that the process ran out of descriptors while doing something, at most once a minute so a spell of it does not become
// the log.
func (f *FDs) SayExhaustion(err error, doing string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sayExhaustion(err, doing)
}

func (f *FDs) sayExhaustion(err error, doing string) {
	if now := f.now(); now.Sub(f.said) >= ExhaustionEvery {
		f.said = now
		f.Log(fmt.Sprintf("out of file descriptors while %s: %v", doing, err))
	}
}

func abs(n int) int {
	if n < 0 {
		return -n
	}
	return n
}
