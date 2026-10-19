package sysres

import (
	"strconv"
	"strings"
)

// vmStatFree is the bytes of the free, inactive and speculative pages vm_stat prints.
func vmStatFree(out string) int64 {
	page := int64(4096)
	var pages int64
	for _, line := range strings.Split(out, "\n") {
		if i := strings.Index(line, "page size of "); i >= 0 {
			if n, err := strconv.ParseInt(strings.Fields(line[i+len("page size of "):])[0], 10, 64); err == nil {
				page = n
			}
			continue
		}
		name, val, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		switch strings.TrimSpace(name) {
		case "Pages free", "Pages inactive", "Pages speculative":
			if n, err := strconv.ParseInt(strings.TrimSuffix(strings.TrimSpace(val), "."), 10, 64); err == nil {
				pages += n
			}
		}
	}
	return pages * page
}
