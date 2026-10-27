"""Tests of tools/bundle.py: python3 -m unittest tools/test_bundle.py"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bundle  # noqa: E402
import pr_checks  # noqa: E402


IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Repo(unittest.TestCase):
    """A bare origin whose main holds file shared.txt, and clone `work`. add(n, file, text) makes pull/n/head: a commit on main changing file."""

    def setUp(self):
        env = mock.patch.dict(os.environ, IDENTITY)
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.origin, self.work = (os.path.join(self.tmp.name, d) for d in ("origin.git", "work"))
        git(self.tmp.name, "init", "-q", "--bare", "-b", "main", self.origin)
        git(self.tmp.name, "clone", "-q", self.origin, self.work)
        self.write("shared.txt", "base\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "-qm", "base")
        git(self.work, "push", "-q", "origin", "HEAD:main")
        self.mainsha = git(self.work, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.work, name), "w") as f:
            f.write(text)

    def add(self, n: int, name: str, text: str) -> str:
        git(self.work, "switch", "-q", "--detach", self.mainsha)
        self.write(name, text)
        git(self.work, "add", ".")
        git(self.work, "commit", "-qm", f"PR {n}")
        sha = git(self.work, "rev-parse", "HEAD")
        git(self.work, "push", "-q", "origin", f"{sha}:refs/pull/{n}/head")
        return sha

    def start(self):
        git(self.work, "switch", "-q", "-C", "bundle-wip", self.mainsha)


class Building(Repo):
    def test_parts_that_do_not_touch_each_other_all_merge(self):
        parts = {1: self.add(1, "a.txt", "a\n"), 2: self.add(2, "b.txt", "b\n"), 3: self.add(3, "c.txt", "c\n")}
        self.start()
        kept, moved = bundle.fetch_heads(parts, self.work)
        merged, dropped = bundle.merge_parts(kept, self.work)
        self.assertEqual((moved, merged, dropped), ({}, [1, 2, 3], {}))
        self.assertTrue(all(os.path.exists(os.path.join(self.work, f)) for f in ("a.txt", "b.txt", "c.txt")))

    def test_a_conflicting_head_is_dropped_and_the_rest_still_go(self):
        parts = {1: self.add(1, "shared.txt", "one\n"), 2: self.add(2, "shared.txt", "two\n"), 3: self.add(3, "c.txt", "c\n")}
        self.start()
        merged, dropped = bundle.merge_parts(bundle.fetch_heads(parts, self.work)[0], self.work)
        self.assertEqual(merged, [1, 3])
        self.assertIn("conflicts", dropped[2])
        self.assertEqual(git(self.work, "status", "--porcelain"), "")  # the failed merge was aborted
        with open(os.path.join(self.work, "shared.txt")) as f:
            self.assertEqual(f.read(), "one\n")

    def test_a_head_that_moved_after_the_check_is_dropped(self):
        parts = {1: self.add(1, "a.txt", "a\n"), 2: self.add(2, "b.txt", "b\n")}
        stale = {**parts, 2: "0" * 40}
        kept, dropped = bundle.fetch_heads(stale, self.work)
        self.assertEqual(list(kept), [1])
        self.assertIn("moved", dropped[2])

    def test_a_red_part_is_left_out_when_the_bundle_is_rebuilt(self):
        parts = {1: self.add(1, "a.txt", "a\n"), 2: self.add(2, "b.txt", "b\n"), 3: self.add(3, "c.txt", "c\n")}
        self.start()
        without_2 = {n: s for n, s in parts.items() if n != 2}
        merged, _ = bundle.merge_parts(bundle.fetch_heads(without_2, self.work)[0], self.work)
        self.assertEqual(merged, [1, 3])
        self.assertFalse(os.path.exists(os.path.join(self.work, "b.txt")))


def fake_run(prs: dict):
    def run(*cmd):
        if cmd[:3] == ("gh", "pr", "list"):
            return json.dumps([{"number": n} for n in prs])
        if cmd[:3] == ("gh", "pr", "view"):
            return json.dumps(prs[int(cmd[3])])
        return ""
    return run


def pr(n, branch="t1-x"):
    return {"number": n, "headRefOid": f"{n:040d}", "headRefName": branch}


class Choosing(unittest.TestCase):
    def ready(self, prs, bad=None, without=()):
        with mock.patch.object(bundle, "run", fake_run(prs)), mock.patch.object(bundle.merge_ready, "required_checks", lambda: ["go"]), \
                mock.patch.object(bundle.merge_ready, "problems", lambda p, *_: (bad or {}).get(p["number"], [])):
            return bundle.ready_prs(set(without))

    def test_only_ready_prs_are_bundled_and_the_others_say_why(self):
        ready, skipped = self.ready({4: pr(4), 5: pr(5), 6: pr(6)}, bad={5: ["required check apps is failure"]})
        self.assertEqual(list(ready), [4, 6])
        self.assertEqual(skipped, {5: "required check apps is failure"})

    def test_without_leaves_a_pr_out(self):
        self.assertEqual(list(self.ready({4: pr(4), 5: pr(5), 6: pr(6)}, without=[5])[0]), [4, 6])

    def test_an_earlier_bundle_is_never_a_part(self):
        self.assertEqual(list(self.ready({4: pr(4), 9: pr(9, "bundle-4-5-6")})[0]), [4])

    def test_fewer_than_three_ready_builds_nothing(self):
        with mock.patch.object(bundle, "ready_prs", lambda w: ({1: "a" * 40, 2: "b" * 40}, {})), mock.patch.object(bundle, "git") as g, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bundle.main(["bundle.py"]), 0)
        g.assert_not_called()


class Text(unittest.TestCase):
    def test_the_body_passes_the_repo_pr_rules(self):
        body = bundle.body_of([1, 3], {1: "a" * 40, 3: "b" * 40}, {2: "it conflicts with main or an earlier part"})
        self.assertEqual(pr_checks.body_problems({"base": {"ref": "main"}, "title": bundle.title_of([1, 3]), "body": body}, []), [])
        self.assertIn("#2: it conflicts", body)
        self.assertEqual(bundle.title_of([1, 3]), "Bundle: #1 #3")


if __name__ == "__main__":
    unittest.main()
