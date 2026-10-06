"""Tests: tools/check-go-tidy.sh fails on a go.mod or go.sum that `go mod tidy` would change, passes on a tidy
one, and says SKIPPED (exit 0) when the modules cannot be fetched; tools/test-go.sh runs it.

Run on its own: tools/test.sh tests/infra/test_go_tidy.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check-go-tidy.sh"


def _module(into: Path) -> Path:
    """A copy of the Go module, source and all (tidy reads the imports)."""
    dest = into / "go"
    shutil.copytree(ROOT / "go", dest, ignore=shutil.ignore_patterns("*.test"))
    return dest


def _check(mod: Path, **env) -> subprocess.CompletedProcess:
    return subprocess.run([str(SCRIPT), str(mod)], capture_output=True, text=True, timeout=300,
                          env={**os.environ, **env})


def test_the_tidy_check_fails_an_untidy_module_and_passes_a_tidy_one(home: Path) -> None:
    if not shutil.which("go"):
        check("go tidy: no go on PATH, nothing to run", True)
        return
    mod = _module(home)
    ok = _check(mod)
    if "SKIPPED" in ok.stderr:
        check("go tidy: the modules cannot be fetched here, so the check says SKIPPED and exits 0", ok.returncode == 0, ok.stderr)
        return
    check("go tidy: the module as it is on main passes", ok.returncode == 0, ok.stderr)
    with open(mod / "go.sum", "a") as f:
        f.write("github.com/not/used v1.0.0 h1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n")
    bad = _check(mod)
    check("go tidy: a go.sum with a line tidy would drop fails, showing the diff",
          bad.returncode == 1 and "not tidy" in bad.stderr and "github.com/not/used" in bad.stderr, bad.stderr)
    shutil.copy(ROOT / "go" / "go.sum", mod / "go.sum")
    # A module the cache already holds and nothing imports (go.sum lists it): tidy drops the require with no network.
    (mod / "go.mod").write_text((mod / "go.mod").read_text() + "\nrequire golang.org/x/tools v0.47.0\n")
    bad = _check(mod, GOPROXY="off")
    check("go tidy: a go.mod requiring a cached module nothing imports fails, offline",
          bad.returncode == 1 and "not tidy" in bad.stderr and "golang.org/x/tools" in bad.stderr, bad.stderr)


def _classify(text: str) -> str:
    return subprocess.run([str(SCRIPT), "--classify"], input=text, capture_output=True, text=True, timeout=30).stdout.strip()


def test_only_a_transport_failure_is_skipped(home: Path) -> None:
    for label, text, want in [
            ("no host", 'go: x@v1: Get "https://proxy.golang.org/x/@v/v1.mod": dial tcp: lookup proxy.golang.org: no such host', "skip"),
            ("a timeout", 'go: x@v1: Get "https://proxy.golang.org/x/@v/v1.mod": i/o timeout', "skip"),
            ("the sandbox proxy", 'go: x@v1: Get "https://proxy.golang.org/x/@v/v1.mod": Forbidden', "skip"),
            ("GOPROXY=off", "go: downloading a v1\n\tgithub.com/g/w: module lookup disabled by GOPROXY=off", "skip"),
            ("a 404 from the proxy", "go: x@v1: reading https://proxy.golang.org/x/@v/v1.mod: 404 Not Found", "fail"),
            ("a 410 for a retracted version", "go: x@v1: reading https://proxy.golang.org/x/@v/v1.mod: 410 Gone", "fail"),
            ("cannot find module", "go: cannot find module providing package x: module lookup disabled by GOPROXY=off", "fail"),
            ("a transport line beside a real one", "go: a@v1: dial tcp: i/o timeout\ngo: b@v1: reading https://proxy.golang.org/b/@v/v1.mod: 404 Not Found", "fail"),
            ("nothing said", "", "fail")]:
        check(f"go tidy: {label} is {want}ed", _classify(text) == want, _classify(text))
    if not shutil.which("go"):
        return
    empty = home / "emptyproxy"
    empty.mkdir(parents=True)
    mod = _module(home)
    (mod / "go.mod").write_text((mod / "go.mod").read_text() + "\nrequire github.com/not/used v1.0.0\n")
    res = _check(mod, GOPROXY=f"file://{empty}", GOFLAGS="-mod=mod")
    check("go tidy: a module the proxy does not have is a failure, not a SKIP",
          res.returncode == 1 and "SKIPPED" not in res.stderr, res.stderr)


def test_the_tidy_check_skips_when_the_modules_cannot_be_fetched(home: Path) -> None:
    if not shutil.which("go"):
        check("go tidy: no go on PATH, nothing to run", True)
        return
    cache = home / "empty-modcache"
    cache.mkdir(parents=True)
    res = _check(_module(home), GOMODCACHE=str(cache), GOPROXY="off", GOFLAGS="-modcacherw")
    check("go tidy: with no module cache and no proxy it says SKIPPED and exits 0, naming why",
          res.returncode == 0 and "SKIPPED" in res.stderr and "GOPROXY" in res.stderr, res.stderr)


def test_the_go_check_runs_it() -> None:
    text = (ROOT / "tools" / "test-go.sh").read_text()
    check("go tidy: tools/test-go.sh runs the tidy check on a whole run", "check-go-tidy.sh" in text)
    check("go tidy: and the script is executable", os.access(SCRIPT, os.X_OK))


def main() -> int:
    def tests(td):
        run(lambda: test_the_tidy_check_fails_an_untidy_module_and_passes_a_tidy_one(Path(td) / "a"))
        run(lambda: test_only_a_transport_failure_is_skipped(Path(td) / "c"))
        run(lambda: test_the_tidy_check_skips_when_the_modules_cannot_be_fetched(Path(td) / "b"))
        run(lambda: test_the_go_check_runs_it())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
