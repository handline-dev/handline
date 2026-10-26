"""Tests of tools/pr_checks.py; the pr-rules workflow runs them before the checks: python3 -m unittest tools/test_pr_checks.py"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pr_checks  # noqa: E402

GOOD = """## Why
The founder asked for it.

## What changed
- One thing.

## Evidence
![after](https://github.com/user-attachments/assets/x.png)

## Risk
None.
"""


def pr(body: str, base: str = "main", title: str = "A change") -> dict:
    return {"title": title, "body": body, "base": {"ref": base, "sha": "x"}}


class Body(unittest.TestCase):
    def test_a_good_body_passes(self):
        self.assertEqual(pr_checks.body_problems(pr(GOOD), ["apps/web/src/A.tsx"]), [])

    def test_base_main_or_story(self):
        self.assertEqual(pr_checks.body_problems(pr(GOOD, "story/pacing"), []), [])
        self.assertIn("the base is feature-x", pr_checks.body_problems(pr(GOOD, "feature-x"), [])[0])

    def test_each_section_needs_text(self):
        got = pr_checks.body_problems(pr(GOOD.replace("None.\n", "")), [])
        self.assertEqual(got, ["the body has no '## Risk' section with text under it"])
        self.assertEqual(len(pr_checks.body_problems(pr(""), [])), 4)

    def test_no_korean_and_no_message_ids(self):
        self.assertEqual(len(pr_checks.body_problems(pr(GOOD, title="통계 고침"), [])), 1)
        made_up = GOOD + "\nFrom msg_0123abc.\n"  # local-ref-ok: a made-up id the check must refuse
        self.assertEqual(len(pr_checks.body_problems(pr(made_up), [])), 1)

    def test_a_body_from_a_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
            f.write(GOOD.replace("## Risk\nNone.\n", ""))
            f.flush()
            run = lambda base: subprocess.run([sys.executable, pr_checks.__file__, "body-file", f.name, "A change", base], capture_output=True, text=True)
            got = run("main")
            self.assertEqual((got.returncode, got.stdout), (1, "the body has no '## Risk' section with text under it\n"))
            self.assertIn("the base is dev", run("dev").stdout)


class Comments(unittest.TestCase):
    def test_runs_of_comment_lines(self):
        lines = ["// a", "// b", "x := 1", "/**", " * c", " */", "y"]
        self.assertEqual(pr_checks.comment_blocks(lines, "//"), [(1, 2), (4, 6)])
        self.assertEqual(pr_checks.comment_blocks(["#!/bin/sh", "# a", "run"], "#"), [(2, 2)])
        self.assertEqual(pr_checks.comment_blocks(["// a", "  * b", "x"], "//"), [(1, 1)])  # a `*` line outside /* */ is code


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.dir.name)
        for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *c], check=True)
        self.write("go/a.go", "// one\n// two\npackage a\n")
        self.commit()
        self.base = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def tearDown(self):
        os.chdir(self.old)
        self.dir.cleanup()

    def write(self, path: str, text: str | bytes):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(text if isinstance(text, bytes) else text.encode())

    def commit(self):
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-qm", "c"], check=True)

    def test_a_clean_change_passes(self):
        self.write("go/a.go", "// one\n// two\n// three\npackage a\n")
        self.commit()
        self.assertEqual(pr_checks.file_problems(self.base), [])

    def test_a_long_comment_it_grows_fails(self):
        self.write("go/a.go", "// one\n// two\n// 3\n// 4\n// 5\n// 6\n// 7\npackage a\n")
        self.commit()
        self.assertEqual(pr_checks.file_problems(self.base), ["go/a.go:1: a comment of 7 lines (at most 6); say it shorter"])

    def test_a_long_comment_it_does_not_touch_passes(self):
        self.write("go/a.go", "// 1\n// 2\n// 3\n// 4\n// 5\n// 6\n// 7\npackage a\n")
        self.commit()
        base = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.write("go/a.go", "// 1\n// 2\n// 3\n// 4\n// 5\n// 6\n// 7\npackage b\n")
        self.commit()
        self.assertEqual(pr_checks.file_problems(base), [])

    def test_a_renamed_file_is_checked(self):
        self.write("notes.txt", "x\n")
        self.commit()
        base = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "mv", "notes.txt", "notes.orig"], check=True)
        self.commit()
        self.assertEqual(len(pr_checks.file_problems(base)), 1)

    def test_a_large_binary_in_an_asset_path_fails(self):
        self.write("apps/mobile/assets/big.bin", b"\x00" * (pr_checks.MAX_BINARY_BYTES + 1))
        self.commit()
        self.assertTrue(pr_checks.file_problems(self.base)[0].startswith("apps/mobile/assets/big.bin: a binary file"))

    def test_images_binaries_and_scratch(self):
        png = b"\x89PNG\r\n\x1a\n\x00\x00"
        self.write("docs/img/shot.png", png)
        self.write("apps/mobile/shots/ios/ok.png", png)
        self.write("tools/handlined", b"\x7fELF\x00\x00\x01")
        self.write("notes.orig", "x\n")
        self.commit()
        got = pr_checks.file_problems(self.base)
        self.assertEqual(len(got), 3, got)
        self.assertTrue(got[0].startswith("docs/img/shot.png: an image"), got)
        self.assertTrue(got[1].startswith("notes.orig: a scratch file"), got)
        self.assertTrue(got[2].startswith("tools/handlined: a binary file"), got)


if __name__ == "__main__":
    unittest.main()
