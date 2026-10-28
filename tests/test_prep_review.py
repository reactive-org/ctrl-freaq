"""Tests for `scripts/prep_review.py` (#171): the threads still waiting on a person, their order, the
script-cut excerpt, and the prep end to end.

The pure core (`is_waiting`, `rank`, `order_items`, `cut_excerpt`) is tested on hand-built inputs. The prep
runs in-process against a real sandbox repo with a work worktree on the issue's branch. `gh pr list` and
the review-thread read go through the `gh` shim with a runtime manifest. The issue gather and the PR view
are stubbed: their own suites cover their `gh` calls, and the prep only reads their returned facts.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import prep_review as pr
import review_threads as rt
from tests.support import envelope_asserts, gitsandbox, shimenv
from tests.test_review_threads import _comment, _page, _thread

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "prep_review.py"
BRANCH = "171-review-threads"
PR_URL = "https://github.com/o/r/pull/5"


def _t(state, f=None, o=None, flags=(), replaced=False, pending_reply=False):
    return {"state": state, "f": f, "o": o, "flags": list(flags), "replaced": replaced,
            "pending_reply": pending_reply, "origin": "operator" if f is None else "pipeline"}


def _headers_consistent(test, lines):
    """Every `@@` header in an excerpt numbers exactly the lines under it."""
    header, old, new = None, 0, 0

    def check():
        if header is not None:
            m = re.match(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", header)
            test.assertIsNotNone(m, header)
            test.assertEqual((int(m.group(1) or 1), int(m.group(2) or 1)), (old, new), header)

    for line in lines:
        if line.startswith("@@"):
            check()
            header, old, new = line, 0, 0
        elif line[:1] == " ":
            old, new = old + 1, new + 1
        elif line[:1] == "-":
            old += 1
        elif line[:1] == "+":
            new += 1
    check()


class SelectionTests(unittest.TestCase):
    def test_only_unreviewed_and_answered_operator_threads_wait(self):
        threads = [
            _t("unreviewed", f=1),
            _t("unreviewed", f=2, replaced=True),
            _t("accepted", f=3),
            _t("changes-requested", f=4),
            _t("superseded", f=5),
            _t("operator-thread", o=1, pending_reply=True),
            _t("operator-thread", o=2, pending_reply=False),
            _t("superseded", f=6, replaced=True),
        ]
        self.assertEqual([pr.is_waiting(t) for t in threads], [True, False, False, False, False, True, False, False])
        self.assertEqual([pr.left_out_key(t) for t in threads if not pr.is_waiting(t)],
                         ["replaced", "accepted", "changes-requested", "superseded", "operator_awaiting_resolver",
                          "replaced"])


class OrderTests(unittest.TestCase):
    def test_rank_order_then_f_then_o(self):
        threads = [
            _t("unreviewed", f=7, flags=["routine:a.txt", "routine:b.txt"]),
            _t("unreviewed", f=6, flags=["out-of-plan:x.py"]),
            _t("operator-thread", o=2, pending_reply=True),
            _t("unreviewed", f=5, flags=["plan:x.py:3 — renamed"]),
            _t("unreviewed", f=4, flags=["judgment:raise:x.py:3"]),
            _t("unreviewed", f=3, flags=["sensitive:x.py"]),
            _t("unreviewed", f=9, flags=["plan:y.py:1 — moved", "security:SEC-2"]),
            _t("unreviewed", f=2, flags=["security:SEC-1"]),
            _t("operator-thread", o=1, pending_reply=True),
            _t("accepted", f=1, flags=["security:SEC-3"]),
        ]
        ordered = pr.order_items(threads)
        self.assertEqual([(t["f"], t["o"]) for t in ordered],
                         [(2, None), (9, None), (3, None), (4, None), (5, None), (6, None), (None, 1), (None, 2),
                          (7, None)])
        self.assertEqual([pr.rank(t) for t in ordered], [1, 1, 2, 2, 3, 4, 5, 5, 6])


def _diff(*hunks):
    return "diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n" + "".join(hunks)


TWO_HUNKS = _diff(
    "@@ -1,4 +1,4 @@ def top():\n l1\n-l2\n+L2\n l3\n l4\n",
    "@@ -20,3 +20,3 @@\n l20\n-l21\n+L21\n l22\n",
)


def _long_hunk():
    body = ["@@ -1,60 +1,60 @@ ctx\n"] + [" c%d\n" % i for i in range(1, 30)] + ["-x30\n", "+y30\n"]
    body += [" c%d\n" % i for i in range(31, 61)]
    return _diff("".join(body))


class CutExcerptTests(unittest.TestCase):
    def test_a_line_anchor_keeps_only_the_intersecting_hunk_with_its_header(self):
        lines, truncated = pr.cut_excerpt(TWO_HUNKS, [21, 21], "line", 40)
        self.assertEqual(lines, ["@@ -20,3 +20,3 @@", " l20", "-l21", "+L21", " l22"])
        self.assertFalse(truncated)
        lines, _ = pr.cut_excerpt(TWO_HUNKS, [2, 2], "line", 40)
        self.assertEqual(lines[0], "@@ -1,4 +1,4 @@ def top():")
        self.assertNotIn("+L21", lines)

    def test_a_file_anchor_or_a_range_outside_every_hunk_takes_the_hunks_from_the_top(self):
        whole = ["@@ -1,4 +1,4 @@ def top():", " l1", "-l2", "+L2", " l3", " l4",
                 "@@ -20,3 +20,3 @@", " l20", "-l21", "+L21", " l22"]
        self.assertEqual(pr.cut_excerpt(TWO_HUNKS, None, "file", 40), (whole, False))
        self.assertEqual(pr.cut_excerpt(TWO_HUNKS, [50, 52], "line", 40), (whole, False))
        lines, truncated = pr.cut_excerpt(TWO_HUNKS, None, "file", 8)
        # The second hunk lost lines, so its header is recomputed for the one line kept.
        self.assertEqual(lines, whole[:6] + ["@@ -20 +20 @@", " l20"])
        self.assertTrue(truncated)
        _headers_consistent(self, lines)

    def test_a_long_hunk_is_cut_to_the_cap_centred_on_the_range(self):
        lines, truncated = pr.cut_excerpt(_long_hunk(), [30, 30], "line", 40)
        self.assertEqual(len(lines), 40)
        self.assertTrue(truncated)
        self.assertTrue(lines[0].startswith("@@ "))
        middle = lines.index("+y30")
        self.assertLess(abs(middle - 20), 3)
        self.assertNotIn(" c1", lines)
        self.assertNotIn(" c60", lines)
        _headers_consistent(self, lines)
        # The recomputed header names the first kept line, never the hunk's own start.
        first = int(re.match(r"^@@ -(\d+)", lines[0]).group(1))
        self.assertEqual(" c%d" % first, lines[1])

    def test_a_window_spanning_two_hunks_keeps_a_header_for_each(self):
        lines, _ = pr.cut_excerpt(TWO_HUNKS, None, "file", 9)
        self.assertEqual(sum(1 for line in lines if line.startswith("@@")), 2)
        self.assertLessEqual(len(lines), 9)
        _headers_consistent(self, lines)

    def test_a_form_feed_or_lone_cr_in_a_line_never_splits_it(self):
        text = _diff("@@ -1,4 +1,4 @@\n a\x0cb\n c\rd\n-x\n+y\n e\n")
        lines, truncated = pr.cut_excerpt(text, [3, 3], "line", 40)
        self.assertEqual(lines, ["@@ -1,4 +1,4 @@", " a\x0cb", " c\rd", "-x", "+y", " e"])
        self.assertFalse(truncated)
        _headers_consistent(self, lines)

    def test_no_hunk_is_an_empty_excerpt(self):
        self.assertEqual(pr.cut_excerpt("diff --git a/x b/x\nold mode 100644\nnew mode 100755\n", None, "file", 40),
                         ([], False))


class PrepSandboxTestCase(unittest.TestCase):
    """A sandbox origin and clone with a work worktree on the issue's branch, whose HEAD is the PR head."""

    BASE_FILES = {
        "src/a.txt": "".join("line %d\n" % i for i in range(1, 81)),
        "bin.dat": "\x00\x01base\x00",
    }

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self._commit(self.clone.path, self.BASE_FILES)
        self._git(self.clone.path, "push", "-q", "origin", "main")
        self._git(self.clone.path, "checkout", "-q", "-b", BRANCH)
        a = ["line %d\n" % i for i in range(1, 81)]
        a[9] = "line 10 changed\n"
        for i in range(39, 72):
            a[i] = "line %d rewritten\n" % (i + 1)
        files = {"src/a.txt": "".join(a), "bin.dat": "\x00\x02head\x00"}
        for n in range(1, 5):
            files["docs/m%d.txt" % n] = "".join("member %d line %d\n" % (n, i) for i in range(1, 21))
        self.first = self._commit(self.clone.path, {"src/a.txt": "".join(a[:30]) + "".join(
            "line %d\n" % i for i in range(31, 81))})
        self.head = self._commit(self.clone.path, files)
        self._git(self.clone.path, "push", "-q", "origin", BRANCH)
        self._git(self.clone.path, "checkout", "-q", "main")
        self.worktree = self.clone.path / ".worktrees" / BRANCH
        self._git(self.clone.path, "worktree", "add", "-q", str(self.worktree), BRANCH)
        self.fixtures = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-review-"))
        self.addCleanup(shutil.rmtree, str(self.fixtures), True)
        self.scratch = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-review-scratch-"))
        self.addCleanup(shutil.rmtree, str(self.scratch), True)
        self.entries = []
        self.threads = []
        self.pr_list = [{"number": 5, "title": "t", "author": {"login": "me"}, "isDraft": True,
                         "headRefName": BRANCH, "baseRefName": "main", "url": PR_URL,
                         "updatedAt": "2026-10-28T00:00:00Z"}]
        self.pr_list_exit = 0
        self.gather = (0, {"status": "ok", "title": "Review threads", "marker_comment_present": False,
                           "notices": []})
        self.pr_head = self.head

    def _git(self, cwd, *args):
        return gitsandbox._run_git(list(args), cwd=cwd)

    def _commit(self, cwd, files):
        for name, text in files.items():
            path = Path(cwd) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self._git(cwd, "add", name)
        self._git(cwd, "commit", "-q", "-m", "c")
        return self._git(cwd, "rev-parse", "HEAD").stdout.strip()

    def _answer(self, argv, payload=None, exit_code=0, text=None):
        name = "out%d.txt" % len(self.entries)
        (self.fixtures / name).write_text(text if text is not None else json.dumps(payload), encoding="utf-8")
        self.entries.append({"argv": argv, "stdout_file": name, "exit_code": exit_code})

    def _pr_facts(self, number, repo, scratch_dir=None, cwd=None, **_kw):
        return ({"number": number, "url": PR_URL, "headRefName": BRANCH, "baseRefName": "main",
                 "headRefOid": self.pr_head, "state": "OPEN"}, [], None)

    def _build(self, refresh=False, head=None, cwd=None):
        self._answer(["pr", "list", "--repo", "o/r", "--head", BRANCH, "--state", "open", "--json",
                      "number,title,author,isDraft,headRefName,baseRefName,url,updatedAt"],
                     self.pr_list, exit_code=self.pr_list_exit)
        self._answer(rt.query_argv("o/r", 5)[1:], _page(self.threads))
        (self.fixtures / "manifest.json").write_text(json.dumps(self.entries), encoding="utf-8")
        env = shimenv.intercepted_env(base_env=os.environ)
        env["GH_SHIM_FIXTURES"] = str(self.fixtures)
        with mock.patch.dict(os.environ, {"GH_SHIM_FIXTURES": str(self.fixtures), "PATH": env["PATH"]}), \
                mock.patch.object(pr.gh_gather, "run", return_value=self.gather), \
                mock.patch.object(pr.gh_pr_gather, "build_pr_facts", side_effect=self._pr_facts):
            return pr.build_review(171, "o/r", str(self.scratch), cwd=str(cwd or self.worktree), refresh=refresh,
                                   head=head, env=env)

    def _root(self, f, flags, path="src/a.txt", rng=(40, 42), head=None, phase=None, **kw):
        subject = "line" if rng is not None else "file"
        start, end = rng if rng is not None else (None, None)
        return _comment(rt.stamp_root(f, head or self.head, path, subject, start, end, list(flags),
                                      "Claim: c%d\nwhy %d" % (f, f), phase=phase), **kw)


class PrepReviewTests(PrepSandboxTestCase):
    def test_items_come_in_rank_order_with_excerpts_links_and_left_out_counts(self):
        self.threads = [
            _thread("T1", [self._root(1, ["plan:src/a.txt:10 — renamed"], rng=(10, 10))]),
            _thread("T2", [self._root(2, ["security:SEC-1"])]),
            _thread("T3", [self._root(3, ["sensitive:src/a.txt"]),
                           _comment(rt.stamp_reply(self.head, "accept: fine", f=3, decision="accept"))]),
            _thread("T4", [self._root(4, ["sensitive:src/a.txt"]),
                           _comment(rt.stamp_reply(self.head, "changes: guard it", f=4, decision="changes"))]),
            _thread("T5", [self._root(5, ["out-of-plan:bin.dat"], path="bin.dat", rng=None)]),
        ]
        payload, notices, decision = self._build()
        self.assertIsNone(decision)
        self.assertEqual(notices, [])
        self.assertEqual(payload["pr"], {"number": 5, "url": PR_URL, "head_ref": BRANCH, "base_ref": "main",
                                         "head": self.head})
        self.assertEqual(payload["review"]["head"], self.head)
        self.assertTrue(payload["review"]["available"])
        items = payload["items"]
        self.assertEqual([i["key"] for i in items], ["F2", "F1", "F5"])
        self.assertEqual([(i["seq"], i["of"]) for i in items], [(1, 3), (2, 3), (3, 3)])
        self.assertEqual([i["security"] for i in items], [True, False, False])
        self.assertEqual(items[0]["claim"], "c2")
        # DoD 2 through the reader: the viewer's accept decision reads accepted, a changes decision
        # changes-requested — neither is an item.
        self.assertEqual(payload["left_out"], {"replaced": 0, "changes-requested": 1, "superseded": 0,
                                               "accepted": 1, "operator_awaiting_resolver": 0})
        security = items[0]["excerpt"]
        self.assertEqual(security["kind"], "diff")
        text = Path(security["path"]).read_text(encoding="utf-8").splitlines()
        self.assertTrue(text[0].startswith("@@ "))
        self.assertIn("+line 41 rewritten", text)
        self.assertNotIn("+line 10 changed", text)
        self.assertLessEqual(security["lines"], pr.EXCERPT_LINES)
        self.assertEqual(security["lines"], len(text))
        plan = Path(items[1]["excerpt"]["path"]).read_text(encoding="utf-8").splitlines()
        self.assertIn("+line 10 changed", plan)
        self.assertNotIn("+line 41 rewritten", plan)
        self.assertEqual(items[2]["excerpt"], {"path": None, "kind": "none", "reason": "binary"})
        for item in items:
            self.assertTrue(item["link"].endswith("#diff-" + hashlib.sha256(item["path"].encode()).hexdigest()))
        self.assertEqual(Path(items[0]["root_text_path"]).read_text(encoding="utf-8"), "**F2**\n\nClaim: c2\nwhy 2\n")

    def test_a_pause_thread_and_an_answered_operator_thread_are_items(self):
        self.threads = [
            _thread("P", [self._root(1, ["sensitive:src/a.txt"], phase=2)]),
            _thread("O", [_comment("please rename", commit=self.head),
                          _comment(rt.stamp_reply(self.head, "renamed"), created="2026-10-02T00:00:00Z")],
                    line=10, start=10),
            _thread("Q", [_comment("is this right?", commit=self.head)], line=10, start=10),
        ]
        payload, _, _ = self._build()
        self.assertEqual([(i["key"], i["phase"], i["origin"]) for i in payload["items"]],
                         [("F1", 2, "pipeline"), ("O1", None, "operator")])
        self.assertEqual(payload["left_out"]["operator_awaiting_resolver"], 1)
        operator = Path(payload["items"][1]["excerpt"]["path"]).read_text(encoding="utf-8")
        self.assertIn("+line 10 changed", operator)

    def _answered(self, commit, line):
        return _thread("O", [_comment("is this right?", commit=commit),
                             _comment(rt.stamp_reply(self.head, "yes"), created="2026-10-02T00:00:00Z")],
                       line=line, start=line)

    def test_an_unmappable_line_anchor_takes_the_file_from_the_top(self):
        # An operator's comment on a commit missing locally (a force-push): its range cannot be mapped. (A
        # pipeline root there reads `superseded` and is never an item.)
        self.threads = [self._answered("c" * 40, 41)]
        payload, _, _ = self._build()
        text = Path(payload["items"][0]["excerpt"]["path"]).read_text(encoding="utf-8").splitlines()
        self.assertIn("+line 10 changed", text)
        self.assertTrue(text[0].startswith("@@ -7,7 "), text[0])
        # The positive control: the same anchor at a commit that exists centres on line 41.
        self.entries = []
        self.threads = [self._answered(self.head, 41)]
        payload, _, _ = self._build()
        text = Path(payload["items"][0]["excerpt"]["path"]).read_text(encoding="utf-8").splitlines()
        self.assertNotIn("+line 10 changed", text)
        self.assertIn("+line 41 rewritten", text)

    def test_a_line_anchor_mapped_from_an_earlier_commit_follows_its_code(self):
        # Rooted at `first` (before line 10 changed), anchored on 10: still line 10 at the PR head.
        self.threads = [_thread("M", [self._root(1, ["plan:src/a.txt:10 — x"], head=self.first, rng=(10, 10))])]
        payload, _, _ = self._build()
        self.assertIn("+line 10 changed",
                      Path(payload["items"][0]["excerpt"]["path"]).read_text(encoding="utf-8").splitlines())

    def test_a_batch_gives_each_member_a_capped_excerpt_until_the_budget_is_spent(self):
        members = ["routine:docs/m%d.txt" % n for n in (3, 1, 4, 2)]
        self.threads = [_thread("B", [self._root(1, members, path="docs/m3.txt", rng=None)])]
        payload, _, _ = self._build()
        item = payload["items"][0]
        self.assertEqual(item["rank"], 6)
        self.assertIsNone(item["excerpt"])
        self.assertEqual([m["path"] for m in item["members"]], ["docs/m%d.txt" % n for n in (1, 2, 3, 4)])
        self.assertEqual([m["elided"] for m in item["members"]], [False, False, False, True])
        self.assertEqual([m["excerpt"]["lines"] for m in item["members"][:3]], [pr.MEMBER_LINES] * 3)
        self.assertTrue(all(m["excerpt"]["truncated"] for m in item["members"][:3]))
        self.assertIsNone(item["members"][3]["excerpt"])
        self.assertEqual(sorted(p.name for p in self.scratch.glob("excerpt-*")),
                         ["excerpt-1-F1-m%d.diff" % n for n in (1, 2, 3)])
        for member in item["members"]:
            self.assertTrue(member["link"].endswith("#diff-" + hashlib.sha256(member["path"].encode()).hexdigest()))

    def test_a_file_that_is_not_utf8_reads_unread_and_the_rest_still_cut(self):
        self._git(self.worktree, "checkout", "-q", BRANCH)
        (self.worktree / "latin.txt").write_bytes(b"caf\xe9\n")
        self._git(self.worktree, "add", "latin.txt")
        self._git(self.worktree, "commit", "-q", "-m", "latin")
        self.head = self.pr_head = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()
        self._git(self.worktree, "push", "-q", "origin", BRANCH)
        self.threads = [_thread("L", [self._root(1, ["out-of-plan:latin.txt"], path="latin.txt", rng=None)]),
                        _thread("A", [self._root(2, ["out-of-plan:src/a.txt"], rng=None)])]
        payload, _, _ = self._build()
        by_key = {i["key"]: i for i in payload["items"]}
        self.assertEqual(by_key["F1"]["excerpt"], {"path": None, "kind": "none", "reason": "unread"})
        self.assertEqual(by_key["F2"]["excerpt"]["kind"], "diff")

    def test_two_threads_sharing_an_f_id_keep_their_own_staged_files(self):
        self.threads = [_thread("T1", [self._root(1, ["sensitive:src/a.txt"], rng=(10, 10))]),
                        _thread("T2", [self._root(1, ["sensitive:src/a.txt"], rng=(41, 41))])]
        payload, _, _ = self._build()
        first, second = payload["items"]
        self.assertEqual((first["key"], second["key"]), ("F1", "F1"))
        self.assertNotEqual(first["excerpt"]["path"], second["excerpt"]["path"])
        self.assertNotEqual(first["root_text_path"], second["root_text_path"])
        texts = [Path(i["excerpt"]["path"]).read_text(encoding="utf-8") for i in (first, second)]
        self.assertEqual(sorted("+line 10 changed" in t for t in texts), [False, True])

    def test_an_unreadable_pr_diff_keeps_the_items_with_unread_excerpts_and_a_notice(self):
        self.threads = [_thread("T", [self._root(1, ["sensitive:src/a.txt"])])]
        with mock.patch.object(pr.sensitive_paths, "changed_paths", return_value=([], None, "git fetch failed")):
            payload, notices, _ = self._build()
        self.assertEqual(payload["items"][0]["excerpt"], {"path": None, "kind": "none", "reason": "unread"})
        self.assertTrue(notices[0].startswith(pr.REVIEW_EXCERPTS_UNAVAILABLE), notices)

    def test_an_unreadable_thread_read_is_unavailable_with_zero_items(self):
        self.threads = [_thread("T", [self._root(1, ["sensitive:src/a.txt"])])]
        with mock.patch.object(pr.review_threads, "read_threads",
                               return_value=(rt._empty_payload(self.head), ["%s: x" % rt.REVIEW_THREADS_UNAVAILABLE])):
            payload, notices, _ = self._build()
        self.assertFalse(payload["review"]["available"])
        self.assertEqual(payload["items"], [])
        self.assertTrue(notices[0].startswith(rt.REVIEW_THREADS_UNAVAILABLE))


class AttachTests(PrepSandboxTestCase):
    def _mismatch(self, result, reason):
        payload, _, decision = result
        self.assertIsNone(payload)
        self.assertEqual((decision["code"], decision["context"]["reason"]), ("WORKSPACE_MISMATCH", reason))

    def test_a_checkout_ahead_of_the_pr_head_is_stale(self):
        self._commit(self.worktree, {"local.txt": "x\n"})
        self._mismatch(self._build(), "stale_checkout")

    def test_refresh_skips_the_exact_head_check_and_judges_at_head(self):
        self._commit(self.worktree, {"local.txt": "x\n"})
        self.threads = [_thread("T", [self._root(1, ["sensitive:src/a.txt"])])]
        payload, _, decision = self._build(refresh=True, head=self.head)
        self.assertIsNone(decision)
        self.assertEqual(payload["review"]["head"], self.head)
        self.assertEqual([i["key"] for i in payload["items"]], ["F1"])

    def test_a_siblings_branch_is_a_branch_mismatch(self):
        self._git(self.clone.path, "branch", "172-other", BRANCH)
        other = self.clone.path / ".worktrees" / "172-other"
        self._git(self.clone.path, "worktree", "add", "-q", str(other), "172-other")
        self._mismatch(self._build(cwd=other), "branch_mismatch")


class PrLookupTests(PrepSandboxTestCase):
    def test_no_open_pr_is_a_null_pr_with_zero_items(self):
        self.pr_list = []
        payload, _, decision = self._build()
        self.assertIsNone(decision)
        self.assertEqual((payload["pr"], payload["review"], payload["items"]), (None, None, []))

    def test_a_failed_lookup_is_a_hard_failure_never_no_pr(self):
        self.pr_list_exit = 1
        with self.assertRaises(pr.LookupFailed) as raised:
            self._build()
        self.assertIn("OPEN_PR_LOOKUP_UNAVAILABLE", str(raised.exception))

    def test_target_is_pr_is_forwarded_before_any_checkout_read(self):
        decision = {"code": "TARGET_IS_PR", "summary": "x", "context": {}, "options": []}
        self.gather = (0, {"status": "needs_decision", "decision": decision, "notices": []})
        with mock.patch.object(pr.workspace, "_build_attach") as attach:
            payload, _, forwarded = self._build()
        self.assertIsNone(payload)
        self.assertEqual(forwarded, decision)
        attach.assert_not_called()

    def test_the_plan_is_staged_when_present(self):
        self.gather = (0, {"status": "ok", "title": "t", "marker_comment_present": True, "notices": [],
                           "marker_comment_body": "<!-- implementation-plan:v1 -->\nplan\n"})
        payload, _, _ = self._build()
        self.assertTrue(payload["plan"]["present"])
        self.assertEqual(Path(payload["plan"]["body_path"]).read_text(encoding="utf-8"),
                         "<!-- implementation-plan:v1 -->\nplan\n")


class CliTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT)] + list(args), capture_output=True, encoding="utf-8",
                              env=shimenv.intercepted_env(base_env=os.environ), check=False)

    def test_usage_errors_exit_2(self):
        self.assertEqual(self._run("171", "o/r", "--refresh").returncode, 2)
        self.assertEqual(self._run("171", "o/r", "--head", "a" * 40).returncode, 2)
        self.assertEqual(self._run("171", "o/r", "--refresh", "--head", "abc").returncode, 2)
        self.assertEqual(self._run("#171", "o/r").returncode, 2)

    def test_the_emitted_envelope_conforms(self):
        payload = {"issue": {"number": 171, "title": "t"}, "pr": None, "workspace": {}, "plan": {"present": False,
                   "body_path": None}, "review": None, "items": [], "left_out": {k: 0 for k in pr.LEFT_OUT_KEYS},
                   "scratch": "/tmp/x"}
        out = io.StringIO()
        with mock.patch.object(pr, "build_review", return_value=(payload, ["N: x"], None)), \
                mock.patch.object(sys, "stdout", out):
            self.assertEqual(pr.main(["171", "o/r"]), 0)
        envelope = json.loads(out.getvalue())
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual((envelope["status"], envelope["notices"], envelope["pr"]), ("ok", ["N: x"], None))

    def test_a_hard_failure_exits_1_with_stderr_and_no_envelope(self):
        with mock.patch.object(pr, "build_review", side_effect=pr.LookupFailed("OPEN_PR_LOOKUP_UNAVAILABLE: x")):
            with mock.patch.object(sys, "stdout") as out, mock.patch.object(sys, "stderr") as err:
                code = pr.main(["171", "o/r", "--scratch-dir", tempfile.gettempdir()])
        self.assertEqual(code, 1)
        out.write.assert_not_called()
        self.assertIn("OPEN_PR_LOOKUP_UNAVAILABLE", "".join(c.args[0] for c in err.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
