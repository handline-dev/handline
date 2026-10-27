"""Tests of tools/localrefs.py; the pr-rules workflow runs them: python3 -m unittest tools/test_localrefs.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import localrefs  # noqa: E402


def diff(path: str, *lines: str) -> str:
    return f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{l}\n" for l in lines)


class Mentions(unittest.TestCase):
    def test_what_github_would_notify(self):
        self.assertEqual(localrefs.mentions("ask @human, (@vitejs and @types/node"), ["human", "vitejs", "types"])
        self.assertEqual(localrefs.mentions("`@human` a@human.com x/@y @@z"), [])


class Messages(unittest.TestCase):
    def test_a_bare_mention_outside_code_is_refused(self):
        got = localrefs.scan_message("Ask @human first\n\nthen `@human`\n```\n@human\n```\n", "m")
        self.assertEqual(got, ["m:1: mention: @human"])

    def test_our_own_handles_pass(self):
        self.assertEqual(localrefs.scan_message("thanks @handline-dev and @nightee99", "m"), [])

    def test_task_and_message_ids_are_refused(self):
        got = localrefs.scan_message("My turn: ask cards (T6721)\n\nfrom msg_3e39bfa", "m")
        self.assertEqual(got, ["m:1: task id: T6721", "m:3: message id: msg_3e39bfa"])

    def test_the_subject_git_writes_for_a_merge_passes(self):
        self.assertEqual(localrefs.scan_message("Merge branch 'story/t6670-x' into main", "m"), [])
        self.assertEqual(localrefs.scan_message("Merge origin/main into story/t6670-x", "m"), ["m:1: task id: t6670"])


class Diff(unittest.TestCase):
    def test_a_mention_in_prose_is_refused(self):
        got = localrefs.scan_diff(diff("docs/a.md", "a comment naming @human opens an ask", "write `@human`"), [])
        self.assertEqual(got, ["docs/a.md:1: mention in prose: @human"])
        got = localrefs.scan_diff(diff("contract/spec/tasks.json", '"description": "names @human"'), [])
        self.assertEqual(got, ["contract/spec/tasks.json:1: mention in prose: @human"])
        self.assertEqual(localrefs.scan_diff(diff("contract/fixtures/a.json", '"text": "@human which one?"'), []), [])

    def test_a_comment_is_prose_and_a_literal_is_code(self):
        lines = ('const AT = /@human\\b/; // a comment that names @human', 'if s == "@human" {', "   * asks @human",
                 'x = "@human"  # names @human')
        got = localrefs.scan_diff(diff("a.ts", *lines[:1]) + diff("a.go", *lines[1:3]) + diff("a.py", lines[3]), [])
        self.assertEqual(got, ["a.ts:1: mention in prose: @human", "a.go:2: mention in prose: @human",
                               "a.py:1: mention in prose: @human"])

    def test_only_the_names_that_collide(self):
        self.assertEqual(localrefs.scan_diff(diff("docs/a.md", "see @types/node"), []), [])


if __name__ == "__main__":
    unittest.main()
