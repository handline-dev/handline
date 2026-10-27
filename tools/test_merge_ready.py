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
    "T11 Live check PR #7 web row  [handline.ux] done  DONE: UX live PASS on #7 @abc1234de",
    "T12 Audit PR 70 (T2)  [handline.auditor] doing",
])
SHOWS = {
    10: "T10  Audit PR 7\nstate done\ndone 2026-10-27 00:24: PASS #7 @abc1234de (diff read)\n",
    11: "T11  Live check PR #7\nstate done\ndone 2026-10-27 00:25: UX live PASS on #7 @abc1234def: no wrap\n",
}
UI = [{"path": "apps/web/src/A.tsx"}]


def pr(**kw) -> dict:
    base = {"number": 7, "state": "OPEN", "isDraft": False, "baseRefName": "main", "headRefOid": HEAD, "mergeable": "MERGEABLE",
            "statusCheckRollup": GREEN, "files": [{"path": "go/internal/x.go"}]}
    return {**base, **kw}


def check(p=None, listing=LISTING, shows=None):
    return merge_ready.problems(p or pr(), REQUIRED, listing, lambda tid: (shows or SHOWS)[tid])


def said(text: str) -> str:
    return f"done 2026-10-27 00:24: {text}"


class Ready(unittest.TestCase):
    def test_a_ready_go_pr_has_no_problems(self):
        self.assertEqual(check(), [])

    def test_a_ready_ui_pr_needs_its_ux_pass_too(self):
        self.assertEqual(check(pr(files=UI)), [])

    def test_the_real_audit_forms(self):
        for text in ["PASS #7 @abc1234de (diff read)", "PASS: PR #7 @abc1234de. Diff read", "PASS on PR #7 @abc1234de", "PR #7 @abc1234de PASS (diff read)",
                     "@abc1234de PASS", "PASS @abc1234def5678abc1234def5678abc1234def56"]:
            self.assertEqual(check(shows={**SHOWS, 10: said(text)}), [], text)

    def test_the_real_ux_forms(self):
        ui = pr(files=UI)
        for text in ["UX live PASS on #7 @abc1234de: words", "Web half PASS on #7 @abc1234de (no wrap); nit: x", "UX live PASS @abc1234de"]:
            self.assertEqual(check(ui, shows={**SHOWS, 11: said(text)}), [], text)


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
        self.one(check(shows={**SHOWS, 10: said("PASS #7 @0000000aa")}), "not the head abc123")

    def test_only_the_first_sha_counts(self):
        self.one(check(shows={**SHOWS, 10: said("PASS #7 @0000000aa (earlier @abc1234de)")}), "not the head")

    def test_what_is_not_a_pass_is_refused_wherever_the_word_PASS_stands(self):
        for text in ["Findings, no PASS @abc1234de", "fail: PASS on web @abc1234de", "PR #7 @abc1234de: not PASS yet", "NOT PASS @abc1234de",
                     "FINDINGS #7 @abc1234de", "FAIL #7 @abc1234de", "pass #7 @abc1234de", "PASS (re-audit)", "PASSED #7 @abc1234de"]:
            self.one(check(shows={**SHOWS, 10: said(text)}), "does not say PASS first", )

    def test_an_audit_of_another_pr(self):
        self.one(check(shows={**SHOWS, 10: said("PASS #8 @abc1234de")}), "for PR 8, not 7")

    def test_a_re_audit_counts_and_the_highest_numbered_decides(self):
        listing = LISTING + "\nT15 Re-audit PR 7 (T1)  [handline.auditor] done  DONE: x"
        self.one(check(listing=listing, shows={**SHOWS, 15: said("PASS @1111111")}), "T15")
        self.assertEqual(check(listing=listing, shows={**SHOWS, 15: said("PASS @abc1234")}), [])

    def test_a_title_names_the_pr_of_its_first_number_only(self):
        self.one(check(listing="T16 Audit PR 8 (follows PR 7)  [handline.auditor] done  DONE: x"), "no audit task for PR 7")

    def test_branch_protection_with_no_required_checks(self):
        self.assertEqual(len(merge_ready.problems(pr(), [], LISTING, lambda t: SHOWS[t])), 1)

    def test_a_hundred_files_cannot_be_told_apart(self):
        self.one(check(pr(files=[{"path": f"go/f{i}.go"} for i in range(100)])), "100 or more files")

    def test_the_newest_run_of_a_check_name_decides(self):
        old_red = {"name": "go", "status": "COMPLETED", "conclusion": "FAILURE", "completedAt": "2026-10-27T01:00:00Z"}
        new_green = {"name": "go", "status": "COMPLETED", "conclusion": "SUCCESS", "completedAt": "2026-10-27T02:00:00Z"}
        self.assertEqual(check(pr(statusCheckRollup=[old_red, new_green, GREEN[1]])), [])
        self.assertEqual(check(pr(statusCheckRollup=[new_green, old_red, GREEN[1]])), [])
        new_red = {**old_red, "completedAt": "2026-10-27T03:00:00Z"}
        self.one(check(pr(statusCheckRollup=[new_green, new_red, GREEN[1]])), "required check go is failure")
        rerun = {"name": "go", "status": "IN_PROGRESS", "conclusion": ""}
        self.one(check(pr(statusCheckRollup=[new_green, rerun, GREEN[1]])), "still running")

    def test_no_ux_task_for_a_ui_pr(self):
        self.one(check(pr(files=UI), listing=LISTING.replace("Live check", "Other").replace("[handline.ux]", "[handline.pc]")), "no ux task")

    def test_the_ux_task_is_found_by_its_assignee_or_its_title(self):
        by_assignee = LISTING.replace("Live check PR #7 web row", "Look at PR #7")
        self.assertEqual(check(pr(files=UI), listing=by_assignee), [])
        by_title = LISTING.replace("[handline.ux]", "[handline.pc]").replace("Live check", "Re-check")
        self.assertEqual(check(pr(files=UI), listing=by_title), [])

    def test_a_ux_fail_or_anything_but_a_ux_pass_is_refused(self):
        ui = pr(files=UI)
        for text in ["UX live FAIL on #7 @abc1234de: 2 numbers remain", "PASS live @abc1234de", "PASS on the diff @abc1234de",
                     "Phone half PASS on #7 @abc1234de", "UX live PASS but not on the phone @abc1234de", "Not UX live PASS on #7 @abc1234de"]:
            self.one(check(ui, shows={**SHOWS, 11: said(text)}), "does not start with", )

    def test_a_ux_pass_for_another_pr_or_head(self):
        ui = pr(files=UI)
        self.one(check(ui, shows={**SHOWS, 11: said("UX live PASS on #8 @abc1234de")}), "for PR 8, not 7")
        self.one(check(ui, shows={**SHOWS, 11: said("UX live PASS on #7 @9999999")}), "not the head")

    def test_the_real_ux_texts_of_T6298_T6299_T6359(self):
        ui = pr(number=648, headRefOid="7b4d3705f" + "0" * 31, files=UI)
        listing = "T6298 Re-check PR #648 resource area (T6288 fixes)  [handline.ux] done  DONE: x\nT6300 Audit PR 648 (T6288)  [handline.auditor] done  DONE: x"
        shows = {6298: said("UX live PASS on #648 @7b4d3705f: words for low memory / full load, one form '12 GB free (37%)'. Image in memo."),
                 6300: said("PASS #648 @7b4d3705f0")}
        self.assertEqual(merge_ready.problems(ui, REQUIRED, listing, lambda t: shows[t]), [])
        ui = pr(number=681, headRefOid="a6a1fe196" + "0" * 31, files=UI)
        listing = "T6299 Live check PR #681 web row fix (T6283) and the phone half  [handline.ux] done  DONE: x\nT6301 Audit PR 681  [handline.auditor] done  DONE: x"
        shows = {6299: said("Web half PASS on #681 @a6a1fe196 (no wrap, time fixed); nit: at 280px the Restarting word pushes out the blocked count."),
                 6301: said("PASS #681 @a6a1fe196")}
        self.assertEqual(merge_ready.problems(ui, REQUIRED, listing, lambda t: shows[t]), [])
        ui = pr(number=698, headRefOid="ee27722ed" + "0" * 31, files=UI)
        listing = "T6359 Live check PR #698: My turn, one number  [handline.ux] done  DONE: x\nT6360 Audit PR 698  [handline.auditor] done  DONE: x"
        shows = {6359: said("UX live FAIL on #698 @ee27722ed: My turn 4 is the one sidebar number, but 2 numbers remain."), 6360: said("PASS #698 @ee27722ed")}
        got = merge_ready.problems(ui, REQUIRED, listing, lambda t: shows[t])
        self.assertEqual(len(got), 1, got)
        self.assertIn("T6359", got[0])

    def test_closed_draft_and_other_base(self):
        got = check(pr(state="MERGED", isDraft=True, baseRefName="story/x"))
        self.assertEqual(len(got), 3, got)


if __name__ == "__main__":
    unittest.main()
