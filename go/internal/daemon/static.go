package daemon

import (
	"io"
	"mime"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"
)

// contentTypes are named here so a browser gets the right one whatever the machine's mime table says.
var contentTypes = map[string]string{
	".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
	".css": "text/css; charset=utf-8", ".json": "application/json", ".map": "application/json", ".svg": "image/svg+xml",
	".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".ico": "image/x-icon",
	".webp": "image/webp", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".wasm": "application/wasm",
	".txt": "text/plain; charset=utf-8", ".webmanifest": "application/manifest+json",
}

// webUI serves the built web UI from dir: files only (no listing), nothing outside dir (the root is opened with
// os.Root, which refuses a path or a symlink that leaves it), nothing hidden, and an index that is never cached.
// The files are the app and its pairing screen, not data: every call the app makes goes through the device gate.
func webUI(dir string) (http.Handler, error) {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return nil, err
	}
	root, err := os.OpenRoot(abs)
	if err != nil {
		return nil, err
	}
	files := root.FS()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if r.URL.Query().Has("token") {
			http.Error(w, "a token does not belong in a URL", http.StatusBadRequest)
			return
		}
		name := strings.TrimPrefix(path.Clean("/"+r.URL.Path), "/")
		if name == "" {
			name = "index.html"
		}
		for _, part := range strings.Split(name, "/") {
			if strings.HasPrefix(part, ".") {
				http.NotFound(w, r)
				return
			}
		}
		f, err := files.Open(name)
		if err == nil {
			if st, serr := f.Stat(); serr != nil || st.IsDir() {
				f.Close()
				name = path.Join(name, "index.html")
				f, err = files.Open(name)
			}
		}
		if err != nil {
			http.NotFound(w, r)
			return
		}
		defer f.Close()
		rs, ok := f.(io.ReadSeeker)
		st, serr := f.Stat()
		if !ok || serr != nil || !st.Mode().IsRegular() {
			http.NotFound(w, r)
			return
		}
		h := w.Header()
		ct, known := contentTypes[strings.ToLower(path.Ext(name))]
		if !known {
			ct = mime.TypeByExtension(path.Ext(name))
		}
		if ct == "" {
			ct = "application/octet-stream"
		}
		h.Set("Content-Type", ct)
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Content-Security-Policy", "frame-ancestors 'none'")
		modtime := st.ModTime()
		if path.Base(name) == "index.html" {
			h.Set("Cache-Control", "no-store")
			http.ServeContent(w, r, name, time.Time{}, rs) // a zero time: no Last-Modified to revalidate against
			return
		}
		h.Set("Cache-Control", "no-cache")
		http.ServeContent(w, r, name, modtime, rs)
	}), nil
}
