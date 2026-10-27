"""Tests of tools/merge_ready.py: python3 -m unittest tools/test_merge_ready.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_ready  # noqa: E402

HEAD = "abc1234def5678abc1234def5678abc1234def56"
REQUIRED = ["go", "apps"]
GREEN = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"} for n in REQUIRED]
LISTING = "\n".join([
    "T10 Audit PR 7 (T1)  [handline.auditor] done  DONE: PASS #7 @abc1234de",
    "T11 UX PR 7 (T1)  [handline.ux] done  DONE: PASS live @abc1234de",
    "T12 Audit PR 70 (T2)  [handline.auditor] doing",
])
SHOWS = {
    10: "T10  Audit PR 7\nstate done\ndone 2026-10-27 00:24: PASS #7 @abc1234de (diff read)\n",
    11: "T11  UX PR 7\nstate done\ndone 2026-10-27 00:25: PASS live check on the phone @abc1234def\n",
}


def pr(**kw) -> dict:
    base = {"number": 7, "state": "OPEN", "isDraft": False, "baseRefName": "main", "headRefOid": HEAD, "mergeable": "MERGEABLE",
            "statusCheckRollup": GREEN, "files": [{"path": "go/internal/x.go"}]}
    return {**base, **kw}


def check(p=None, listing=LISTING, shows=None):
    return merge_ready.problems(p or pr(), REQUIRED, listing, lambda tid: (shows or SHOWS)[tid])


class Ready(unittest.TestCase):
    def test_a_ready_go_pr_has_no_problems(self):
        self.assertEqual(check(), [])

    def test_a_ready_ui_pr_needs_its_ux_pass_too(self):
        self.assertEqual(check(pr(files=[{"path": "apps/web/src/A.tsx"}])), [])


class Refuses(unittest.TestCase):
    def one(self, got, text):
        self.assertEqual(len(got), 1, got)
        self.assertIn(text, got[0])

    def test_a_red_check(self):
        red = [GREEN[0], {"name": "apps", "status": "COMPLETED", "conclusion": "FAILURE"}]
        self.one(check(pr(statusCheckRollup=red)), "required check apps is failure")

    def test_a_check_still_running_or_missing(self):
        self.one(check(pr(statusCheckRollup=[GREEN[0], {"name": "apps", "status": "IN_PROGRESS", "conclusion": ""}])), "still running")
        self.one(check(pr(statusCheckRollup=[GREEN[0]])), "required check apps has not run")

    def test_a_conflict_or_an_unknown_merge_state(self):
        self.one(check(pr(mergeable="CONFLICTING")), "conflicts")
        self.one(check(pr(mergeable="UNKNOWN")), "has not worked out")

    def test_no_audit_task(self):
        self.one(check(listing=LISTING.replace("Audit PR 7 ", "Audit PR 8 ")), "no audit task for PR 7")

    def test_PR_70_is_not_PR_7(self):
        self.one(check(listing="T12 Audit PR 70 (T2)  [handline.auditor] done  DONE: PASS @abc1234de"), "no audit task")

    def test_an_audit_still_open(self):
        self.one(check(listing=LISTING + "\nT13 Audit PR 7 again  [handline.auditor] doing"), "T13 is doing")

    def test_a_stale_audit_head(self):
        got = check(shows={**SHOWS, 10: "done 2026-10-27 00:24: PASS #7 @0000000aa"})
        self.one(got, "not the head abc123")

    def test_findings_are_not_a_pass(self):
        self.one(check(shows={**SHOWS, 10: "done 2026-10-27 00:24: FINDINGS #7 @abc1234de"}), "does not start with PASS")

    def test_evidence_without_a_sha(self):
        self.one(check(shows={**SHOWS, 10: "done 2026-10-27 00:24: PASS (re-audit)"}), "names no head sha")

    def test_the_newest_audit_decides(self):
        listing = "T10 Audit PR 7  [a] done  DONE: x\nT14 Audit PR 7 re-audit  [a] done  DONE: x"
        self.one(check(listing=listing, shows={10: SHOWS[10], 14: "done 2026-10-27 01:00: PASS @1111111"}), "T14")

    def test_no_ux_pass_for_a_ui_pr(self):
        ui = pr(files=[{"path": "apps/mobile/src/B.tsx"}])
        self.one(check(ui, listing=LISTING.replace("T11 UX", "T11 Other")), "no ux task")
        self.one(check(ui, shows={**SHOWS, 11: "done 2026-10-27 00:25: PASS on the diff @abc1234de"}), "not say it was checked live")
        self.one(check(ui, shows={**SHOWS, 11: "done 2026-10-27 00:25: PASS live @9999999"}), "not the head")

    def test_a_ui_change_in_a_package(self):
        self.one(check(pr(files=[{"path": "packages/app-ui/src/C.tsx"}]), listing=LISTING.replace("T11 UX", "T11 Other")), "no ux task")

    def test_closed_draft_and_other_base(self):
        got = check(pr(state="MERGED", isDraft=True, baseRefName="story/x"))
        self.assertEqual(len(got), 3, got)


if __name__ == "__main__":
    unittest.main()
