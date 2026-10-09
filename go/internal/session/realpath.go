package session

import (
	"os"
	"strings"
)

// realpath is Python's os.path.realpath (3.14, strict=False), part by part: a link is followed where it is met, so `link/..`
// is the link target's parent, not the folder the link sits in, and what does not exist is kept as written. The guards
// compare these paths with what the Python hooks compared, so a path through a link reads the same in both.
func realpath(filename string) string {
	var rest []*string // the unresolved parts, last first; nil marks a resolved link, whose path is the next pop
	push := func(s string) {
		parts := strings.Split(s, "/")
		for i := len(parts) - 1; i >= 0; i-- {
			p := parts[i]
			rest = append(rest, &p)
		}
	}
	push(filename)
	count := len(rest)
	path := "/"
	if !strings.HasPrefix(filename, "/") {
		if wd, err := os.Getwd(); err == nil {
			path = wd
		}
	}
	seen := map[string]*string{}
	pop := func() *string { p := rest[len(rest)-1]; rest = rest[:len(rest)-1]; return p }
	for count > 0 {
		name := pop()
		if name == nil {
			resolved := path
			seen[*pop()] = &resolved
			continue
		}
		count--
		switch *name {
		case "", ".":
			continue
		case "..":
			if i := strings.LastIndex(path, "/"); i > 0 {
				path = path[:i]
			} else {
				path = "/"
			}
			continue
		}
		newpath := path + "/" + *name
		if path == "/" {
			newpath = path + *name
		}
		st, err := os.Lstat(newpath)
		if err != nil || st.Mode()&os.ModeSymlink == 0 {
			path = newpath
			continue
		}
		if done, ok := seen[newpath]; ok {
			if done != nil {
				path = *done
			} else {
				path = newpath // a loop
			}
			continue
		}
		target, err := os.Readlink(newpath)
		if err != nil {
			path = newpath
			continue
		}
		if strings.HasPrefix(target, "/") {
			path = "/"
		}
		seen[newpath] = nil
		link := newpath
		rest = append(rest, &link, nil)
		before := len(rest)
		push(target)
		count += len(rest) - before
	}
	return path
}
