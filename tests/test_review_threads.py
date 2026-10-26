"""Tests for `scripts/review_threads.py` (#145): the stamp grammar, the pure state derivation and range
mapping, the git hunk check against a real sandbox repo, and `read_threads` through the `gh` shim.

The trust properties are the security half of this module, so each has its own test: a marked comment
by an untrusted author, or by a trusted one without a resolution-grade permission (SEC-3), neither resets a
state nor moves a thread's reference commit, and a resolution
counts only for the viewer or a write+ permission (`read`, `none` and a failed lookup read unresolved).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import review_threads as rt
from tests.support import gitsandbox, shimenv

HEAD = "a" * 40
OTHER = "b" * 40


def _c(marked, trusted):
    return {"marked": marked, "trusted": trusted}


def _never():
    raise AssertionError("the hunk check must run only on a trusted resolution")


class StampTests(unittest.TestCase):
    def test_root_round_trip(self):
        text = rt.stamp_root(3, HEAD, "scripts/x.py", "line", 4, 9, ["sensitive:scripts/x.py", "security:SEC-2"],
                             "body text", supersedes="F1")
        lines = text.split("\n")
        self.assertEqual(lines[0], rt.MARKER)
        self.assertEqual(lines[2], "**F3**")
        self.assertTrue(text.endswith("body text"))
        self.assertEqual(
            rt.parse_stamp(text),
            {"kind": "root", "f": 3, "head": HEAD, "path": "scripts/x.py", "subject": "line", "range": [4, 9],
             "flags": ["sensitive:scripts/x.py", "security:SEC-2"], "supersedes": "F1"},
        )

    def test_file_anchor_and_url_supersedes(self):
        url = "https://github.com/o/r/pull/5#discussion_r12"
        stamp = rt.parse_stamp(rt.stamp_root(1, HEAD, "a.md", "file", None, None, [], "x", supersedes=url))
        self.assertEqual((stamp["subject"], stamp["range"], stamp["flags"], stamp["supersedes"]),
                         ("file", None, [], url))

    def test_a_flag_key_whose_path_carries_a_space_or_comma_round_trips(self):
        keys = ["out-of-plan:docs/My Notes, v2.md", "judgment:trust:a%b.py"]
        stamp = rt.parse_stamp(rt.stamp_root(1, HEAD, "docs/My Notes, v2.md", "file", None, None, keys, "x",
                                             supersedes="F2"))
        self.assertEqual((stamp["path"], stamp["flags"], stamp["supersedes"]),
                         ("docs/My Notes, v2.md", keys, "F2"))

    def test_reply_round_trip(self):
        self.assertEqual(rt.parse_stamp(rt.stamp_reply(HEAD, "fixed", f=2)), {"kind": "reply", "f": 2, "head": HEAD})
        self.assertEqual(rt.parse_stamp(rt.stamp_reply(HEAD, "fixed")), {"kind": "reply", "f": None, "head": HEAD})

    def test_values_the_grammar_cannot_carry_are_refused(self):
        for kwargs in (
            dict(f=0), dict(head="abc"), dict(path="a --> b"), dict(path="a · b"), dict(subject="hunk"),
            dict(start=5, end=2), dict(flags=["a --> b"]), dict(flags=["a · b"]), dict(supersedes="#3"),
        ):
            args = dict(f=1, head=HEAD, path="p", subject="line", start=1, end=2, flags=[], body="x")
            args.update(kwargs)
            with self.assertRaises(ValueError, msg=kwargs):
                rt.stamp_root(args["f"], args["head"], args["path"], args["subject"], args["start"], args["end"],
                              args["flags"], args["body"], supersedes=args.get("supersedes"))

    def test_wrong_types_raise_value_error_not_type_error(self):
        for kwargs in (dict(path=None), dict(head=None), dict(head=1), dict(flags="abc"), dict(flags=[None]),
                       dict(supersedes=3), dict(f=True), dict(start=True, end=True)):
            args = dict(f=1, head=HEAD, path="p", subject="line", start=1, end=2, flags=[], body="x")
            args.update(kwargs)
            with self.assertRaises(ValueError, msg=kwargs):
                rt.stamp_root(args["f"], args["head"], args["path"], args["subject"], args["start"], args["end"],
                              args["flags"], args["body"], supersedes=args.get("supersedes"))
        with self.assertRaises(ValueError):
            rt.stamp_reply(None, "x")

    def test_review_stamp_round_trip(self):
        self.assertEqual(rt.parse_stamp(rt.stamp_review(HEAD, "two threads")), {"kind": "review", "head": HEAD})

    def test_unmarked_or_unreadable_stamp_is_none(self):
        self.assertIsNone(rt.parse_stamp("plain comment"))
        self.assertIsNone(rt.parse_stamp(rt.MARKER + "\nno stamp here"))
        self.assertIsNone(rt.parse_stamp(rt.MARKER))

    def test_reason_line(self):
        self.assertEqual(rt.reason_line("  not  exploitable —\n behind auth  "), "not exploitable - behind auth")
        self.assertIsNone(rt.reason_line(None))


class DeriveStateTests(unittest.TestCase):
    def test_unreviewed_pipeline_root(self):
        self.assertEqual(rt.derive_state([_c(True, True)], "pipeline", False, _never), ("unreviewed", False))

    def test_trusted_human_reply_requests_changes(self):
        state = rt.derive_state([_c(True, True), _c(False, True)], "pipeline", False, _never)
        self.assertEqual(state, ("changes-requested", False))

    def test_a_marked_reply_makes_it_unreviewed_again(self):
        state = rt.derive_state([_c(True, True), _c(False, True), _c(True, True)], "pipeline", False, _never)
        self.assertEqual(state, ("unreviewed", False))

    def test_an_untrusted_marked_reply_does_not_reset_changes_requested(self):
        # The forged-marker property: only a trusted author's marked comment is the pipeline's.
        state = rt.derive_state([_c(True, True), _c(False, True), _c(True, False)], "pipeline", False, _never)
        self.assertEqual(state, ("changes-requested", False))

    def test_an_untrusted_unmarked_reply_requests_nothing(self):
        self.assertEqual(rt.derive_state([_c(True, True), _c(False, False)], "pipeline", False, _never),
                         ("unreviewed", False))

    def test_operator_thread_outranks_changes_requested(self):
        state = rt.derive_state([_c(False, True), _c(True, True), _c(False, True)], "operator", False, _never)
        self.assertEqual(state, ("operator-thread", False))

    def test_pending_reply_flips_with_a_marked_reply(self):
        self.assertEqual(rt.derive_state([_c(False, True)], "operator", False, _never), ("operator-thread", False))
        self.assertEqual(rt.derive_state([_c(False, True), _c(True, True)], "operator", False, _never),
                         ("operator-thread", True))
        # A forged marked reply is not the pipeline's answer.
        self.assertEqual(rt.derive_state([_c(False, True), _c(True, False)], "operator", False, _never),
                         ("operator-thread", False))

    def test_trusted_resolution_is_accepted_or_superseded_by_the_hunk(self):
        self.assertEqual(rt.derive_state([_c(True, True), _c(False, True)], "pipeline", True, lambda: False),
                         ("accepted", False))
        self.assertEqual(rt.derive_state([_c(True, True)], "pipeline", True, lambda: True), ("superseded", False))
        self.assertEqual(rt.derive_state([_c(False, True)], "operator", True, lambda: False), ("accepted", False))

    def test_states_are_the_closed_set(self):
        self.assertEqual(rt.THREAD_STATES,
                         ("unreviewed", "accepted", "changes-requested", "operator-thread", "superseded"))


class ResolutionTrustTests(unittest.TestCase):
    def test_viewer_resolution_needs_no_permission(self):
        self.assertTrue(rt.resolution_trusted(True, "me", "me", {}))

    def test_write_and_above_count(self):
        for permission in ("admin", "maintain", "write"):
            self.assertTrue(rt.resolution_trusted(True, "them", "me", {"them": permission}), permission)

    def test_read_none_unknown_and_failed_lookups_do_not(self):
        for permissions in ({"them": "read"}, {"them": "none"}, {"them": "triage"}, {"them": None}, {}):
            self.assertFalse(rt.resolution_trusted(True, "them", "me", permissions), permissions)

    def test_unresolved_is_never_a_trusted_resolution(self):
        self.assertFalse(rt.resolution_trusted(False, "me", "me", {}))
        self.assertFalse(rt.resolution_trusted(True, None, "me", {}))


class MapRangeTests(unittest.TestCase):
    def test_lines_below_a_hunk_shift_by_its_delta(self):
        # Two lines inserted after line 2, one line deleted at 4 (old) — a range at 10..12 moves +1.
        hunks = rt.parse_hunks("@@ -2,0 +3,2 @@\n+a\n+b\n@@ -4 +5,0 @@\n-c\n")
        self.assertEqual(hunks, [(2, 0, 3, 2), (4, 1, 5, 0)])
        self.assertEqual(rt.map_range(hunks, [10, 12]), [11, 13])

    def test_lines_above_every_hunk_stay(self):
        self.assertEqual(rt.map_range([(20, 2, 20, 5)], [3, 6]), [3, 6])

    def test_an_intersecting_hunk_widens_to_its_new_side(self):
        self.assertEqual(rt.map_range([(5, 2, 5, 6)], [4, 5]), [4, 10])

    def test_an_insertion_inside_the_range_widens_it(self):
        self.assertEqual(rt.map_range([(5, 0, 6, 3)], [4, 7]), [4, 10])

    def test_file_anchor_stays_none(self):
        self.assertIsNone(rt.map_range([(1, 1, 1, 1)], None))


class HunkChangedTests(unittest.TestCase):
    """`hunk_changed` against a real git repo (tests.support.gitsandbox)."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.path = "src/a.txt"
        self.base = self._commit({self.path: "".join("line %d\n" % i for i in range(1, 21))})

    def _git(self, *args):
        return gitsandbox._run_git(list(args), cwd=self.clone.path)

    def _commit(self, files, delete=()):
        for name, text in files.items():
            target = self.clone.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            self._git("add", name)
        for name in delete:
            self._git("rm", "-q", name)
        self._git("commit", "-q", "-m", "change")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _edit_line(self, number, text):
        lines = (self.clone.path / self.path).read_text(encoding="utf-8").splitlines(True)
        lines[number - 1] = text + "\n"
        return self._commit({self.path: "".join(lines)})

    def test_unchanged(self):
        head = self._commit({"other.txt": "x\n"})
        self.assertFalse(rt.hunk_changed(self.clone.path, self.base, head, self.path, [5, 8]))
        self.assertFalse(rt.hunk_changed(self.clone.path, self.base, self.base, self.path, [5, 8]))

    def test_changed_inside_the_range(self):
        head = self._edit_line(6, "edited")
        self.assertTrue(rt.hunk_changed(self.clone.path, self.base, head, self.path, [5, 8]))

    def test_changed_outside_the_range(self):
        head = self._edit_line(15, "edited")
        self.assertFalse(rt.hunk_changed(self.clone.path, self.base, head, self.path, [5, 8]))
        # A file anchor counts any hunk.
        self.assertTrue(rt.hunk_changed(self.clone.path, self.base, head, self.path, None, subject="file"))

    def test_deleted_file(self):
        head = self._commit({}, delete=[self.path])
        self.assertTrue(rt.hunk_changed(self.clone.path, self.base, head, self.path, [5, 8]))

    def test_a_binary_change_counts_as_changed(self):
        # `git diff -U0` prints no `@@` hunk for a binary file, which must not read as "unchanged".
        target = self.clone.path / "img.bin"
        target.write_bytes(b"\x00\x01\x02" * 50)
        self._git("add", "img.bin")
        self._git("commit", "-q", "-m", "binary")
        before = self._git("rev-parse", "HEAD").stdout.strip()
        target.write_bytes(b"\x00\x09\x02" * 50)
        self._git("commit", "-q", "-am", "binary change")
        after = self._git("rev-parse", "HEAD").stdout.strip()
        self.assertTrue(rt.hunk_changed(self.clone.path, before, after, "img.bin", [1, 1]))
        self.assertTrue(rt.hunk_changed(self.clone.path, before, after, "img.bin", None, subject="file"))
        self.assertIsNone(rt.map_range_between(self.clone.path, before, after, "img.bin", [1, 1]))

    def test_the_path_is_matched_literally_not_as_a_pathspec(self):
        # `src/a[1].txt` as a glob would match `src/a1.txt`; only a literal match is the thread's file.
        base = self._commit({"src/a[1].txt": "x\n", "src/a1.txt": "y\n"})
        head = self._commit({"src/a1.txt": "changed\n"})
        self.assertFalse(rt.hunk_changed(self.clone.path, base, head, "src/a[1].txt", [1, 1]))
        self.assertFalse(rt.hunk_changed(self.clone.path, base, head, "src/a[1].txt", None, subject="file"))
        self.assertTrue(rt.hunk_changed(self.clone.path, base, head, "src/a1.txt", None, subject="file"))

    def test_a_file_check_opened_on_a_deleted_path(self):
        gone = self._commit({}, delete=[self.path])
        head = self._commit({"other.txt": "x\n"})
        f = lambda ref, hd, anchor: rt.hunk_changed(self.clone.path, ref, hd, self.path, None, "file", anchor=anchor)
        self.assertFalse(f(gone, head, gone))
        self.assertFalse(f(gone, gone, gone))
        self.assertTrue(f(gone, head, self.base))   # the path existed when the thread was opened
        self.assertTrue(f(gone, head, None))
        self.assertTrue(f(gone, head, "c" * 40))
        self.assertTrue(rt.hunk_changed(self.clone.path, gone, head, self.path, [1, 2], "line", anchor=gone))
        restored = self._commit({self.path: "back\n"})
        self.assertTrue(f(gone, restored, gone))

    def test_missing_reference_commit(self):
        head = self._commit({"other.txt": "x\n"})
        self.assertTrue(rt.hunk_changed(self.clone.path, "c" * 40, head, self.path, [5, 8]))
        self.assertTrue(rt.hunk_changed(self.clone.path, None, head, self.path, [5, 8]))

    def test_a_line_thread_with_no_range_counts_as_changed(self):
        head = self._commit({"other.txt": "x\n"})
        self.assertTrue(rt.hunk_changed(self.clone.path, self.base, head, self.path, None))

    def test_map_range_between_two_commits(self):
        lines = (self.clone.path / self.path).read_text(encoding="utf-8").splitlines(True)
        head = self._commit({self.path: "new 1\nnew 2\n" + "".join(lines)})
        self.assertEqual(rt.map_range_between(self.clone.path, self.base, head, self.path, [5, 8]), [7, 10])
        self.assertIsNone(rt.map_range_between(self.clone.path, "c" * 40, head, self.path, [5, 8]))


_URL_IDS = iter(range(1, 10 ** 9))


def _comment(body, association="OWNER", login="me", created="2026-10-01T00:00:00Z", url=None, commit=None,
             state="SUBMITTED"):
    return {"author": {"login": login}, "authorAssociation": association, "body": body, "createdAt": created,
            "url": url or "https://github.com/o/r/pull/5#discussion_r%d" % next(_URL_IDS),
            "state": state, "originalCommit": {"oid": commit} if commit else None}


def _thread(tid, comments, resolved=False, resolved_by=None, path="src/a.txt", subject="LINE",
            line=8, start=5, total=None):
    return {"id": tid, "isResolved": resolved, "resolvedBy": {"login": resolved_by} if resolved_by else None,
            "path": path, "subjectType": subject, "line": line, "startLine": start, "originalLine": line,
            "originalStartLine": start, "root": {"nodes": comments[:1]},
            "recent": {"totalCount": total if total is not None else len(comments), "nodes": comments}}


def _page(threads, has_next=False, cursor=None, comments=(), reviews=(), comments_total=None):
    return {"data": {"viewer": {"login": "me"}, "repository": {"pullRequest": {
        "reviewThreads": {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": threads},
        "comments": {"totalCount": comments_total if comments_total is not None else len(comments),
                     "nodes": list(comments)},
        "reviews": {"totalCount": len(reviews), "nodes": list(reviews)},
    }}}}


class ReadThreadsTests(unittest.TestCase):
    """`read_threads` through the shim, with a runtime manifest per test; the hunk check runs in a real
    sandbox repo whose HEAD is the judged head."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        # The threads below sit on `src/a.txt`; a path absent at the judged head reads as changed.
        self.head = self._commit("".join("line %d\n" % i for i in range(1, 21)))
        self.fixtures = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-threads-"))
        self.addCleanup(shutil.rmtree, str(self.fixtures), True)
        self.scratch = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-threads-scratch-"))
        self.addCleanup(shutil.rmtree, str(self.scratch), True)
        self.entries = []

    def _answer(self, argv, payload=None, exit_code=0, text=None):
        name = "out%d.txt" % len(self.entries)
        data = text if text is not None else json.dumps(payload)
        (self.fixtures / name).write_text(data, encoding="utf-8")
        self.entries.append({"argv": argv[1:], "stdout_file": name, "exit_code": exit_code})

    def _read(self):
        (self.fixtures / "manifest.json").write_text(json.dumps(self.entries), encoding="utf-8")
        env = shimenv.intercepted_env(base_env=os.environ)
        env["GH_SHIM_FIXTURES"] = str(self.fixtures)
        return rt.read_threads("o/r", 5, str(self.clone.path), self.head, str(self.scratch), env=env)

    def _root(self, f, flags=("sensitive:src/a.txt",), head=None, **kw):
        return _comment(rt.stamp_root(f, head or self.head, "src/a.txt", "line", 5, 8, list(flags), "why"), **kw)

    def test_a_pipeline_roots_claim_line_is_read(self):
        root = _comment(rt.stamp_root(1, self.head, "src/a.txt", "line", 5, 8, ["security:SEC-1"],
                                      "Claim:  token  logged\nwhy"), url="https://x/f1")
        self._answer(rt.query_argv("o/r", 5), _page([_thread("T1", [root])]))
        payload, _ = self._read()
        self.assertEqual(payload["threads"][0]["claim"], "token logged")

    def test_two_cursor_pages_states_ordinals_and_next_f(self):
        human_root = _comment("please look at this", login="mate", association="MEMBER",
                              created="2026-10-02T00:00:00Z", url="https://x/human1")
        later_human = _comment("and this", created="2026-10-03T00:00:00Z", url="https://x/human2")
        page1 = _page([
            _thread("T1", [self._root(1, url="https://x/f1"), _comment("fix it", created="2026-10-04T00:00:00Z")]),
            _thread("T2", [later_human]),
        ], has_next=True, cursor="CUR1",
            comments=[_comment("top-level note"), _comment("drive-by", association="NONE", login="rando")],
            reviews=[dict(_comment("soft reject"), state="COMMENTED")])
        page2 = _page([_thread("T3", [human_root]),
                       _thread("T4", [self._root(4, url="https://x/f4"), _comment(rt.stamp_reply(self.head, "done", f=4))],
                               resolved=True, resolved_by="me")])
        self._answer(rt.query_argv("o/r", 5), page1)
        self._answer(rt.query_argv("o/r", 5, after="CUR1"), page2)
        payload, notices = self._read()
        self.assertEqual(notices, [])
        self.assertTrue(payload["available"])
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertEqual(by_id["T1"]["state"], "changes-requested")
        self.assertEqual(by_id["T1"]["f"], 1)
        self.assertEqual(by_id["T1"]["reply_reason"], "fix it")
        self.assertEqual(by_id["T1"]["flags"], ["sensitive:src/a.txt"])
        self.assertEqual(by_id["T4"]["state"], "accepted")
        # The pipeline's reply is the thread's last word, so it has no human reply to read as a reason.
        self.assertIsNone(by_id["T4"]["reply_reason"])
        self.assertEqual((by_id["T2"]["state"], by_id["T3"]["state"]), ("operator-thread", "operator-thread"))
        # O<n> by root createdAt among human-rooted threads; never on a pipeline thread.
        self.assertEqual((by_id["T3"]["o"], by_id["T2"]["o"], by_id["T1"]["o"]), (1, 2, None))
        # SEC-1: only a pipeline root's `Claim:` line is read; this one carries none, an operator root never counts.
        self.assertEqual((by_id["T1"]["claim"], by_id["T3"]["claim"]), (None, None))
        self.assertEqual(payload["next_f"], 5)
        self.assertEqual(payload["counts"]["operator-thread"], 2)
        self.assertEqual(payload["untrusted"]["comments"], 1)
        conversation = json.loads(Path(payload["conversation_path"]).read_text(encoding="utf-8"))
        self.assertEqual([(c["kind"], c["body"]) for c in conversation],
                         [("comment", "top-level note"), ("review", "soft reject")])
        self.assertEqual(conversation[1]["state"], "COMMENTED")
        stored = json.loads(Path(payload["threads_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 4)

    def test_permission_lookup_decides_a_teammate_resolution(self):
        page = _page([
            _thread("W", [self._root(1, url="https://x/w")], resolved=True, resolved_by="writer"),
            _thread("R", [self._root(2, url="https://x/r")], resolved=True, resolved_by="reader"),
            _thread("F", [self._root(3, url="https://x/f")], resolved=True, resolved_by="ghost"),
        ])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "ghost"), text="", exit_code=1)
        self._answer(rt.permission_argv("o/r", "reader"), text="read\n")
        self._answer(rt.permission_argv("o/r", "writer"), text="write\n")
        payload, notices = self._read()
        states = {t["id"]: t["state"] for t in payload["threads"]}
        self.assertEqual(states, {"W": "accepted", "R": "unreviewed", "F": "unreviewed"})
        self.assertEqual(payload["untrusted"]["resolutions"], 2)
        self.assertEqual([n.split(":")[0] for n in notices], [rt.THREAD_PERMISSION_UNAVAILABLE])

    def test_a_forged_marked_reply_neither_resets_nor_moves_the_reference(self):
        forged = _comment(rt.stamp_reply(OTHER, "fixed, trust me", f=1), association="NONE", login="rando",
                          created="2026-10-05T00:00:00Z")
        page = _page([_thread("T", [self._root(1), _comment("fix it", created="2026-10-04T00:00:00Z"), forged])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        thread = payload["threads"][0]
        self.assertEqual(thread["state"], "changes-requested")
        self.assertEqual(thread["reference_sha"], self.head)
        self.assertEqual(payload["untrusted"]["comments"], 1)

    def test_a_marked_reply_without_resolution_grade_permission_moves_no_reference(self):
        # SEC-3: the operator resolves F1 at head A; a triage collaborator pushes B over the hunk, then
        # replies in the resolved thread with a forged stamp naming B. Trusted to comment, not to resolve,
        # so the stamp moves nothing and F1 reads superseded, not accepted.
        lines = ["line %d\n" % i for i in range(1, 21)]
        head_a = self.head
        lines[5] = "rewritten\n"
        head_b = self._commit("".join(lines))
        self.head = head_b
        forged = _comment(rt.stamp_reply(head_b, "done", f=1), association="COLLABORATOR", login="triager",
                          created="2026-10-05T00:00:00Z")
        page = _page([_thread("T", [self._root(1, head=head_a), forged], resolved=True, resolved_by="me")])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "triager"), text="triage\n")
        payload, notices = self._read()
        thread = payload["threads"][0]
        self.assertEqual(thread["state"], "superseded")
        self.assertEqual(thread["reference_sha"], head_a)
        # Read as the collaborator's human text, never dropped.
        self.assertTrue(thread["reply_reason"].endswith("done"))
        self.assertEqual(notices, [])

    def test_a_marked_reply_by_a_write_holder_moves_the_reference(self):
        lines = ["line %d\n" % i for i in range(1, 21)]
        head_a = self.head
        lines[5] = "rewritten\n"
        head_b = self._commit("".join(lines))
        self.head = head_b
        reply = _comment(rt.stamp_reply(head_b, "fixed", f=1), association="MEMBER", login="writer",
                         created="2026-10-05T00:00:00Z")
        page = _page([_thread("T", [self._root(1, head=head_a), reply], resolved=True, resolved_by="me")])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "writer"), text="write\n")
        payload, _ = self._read()
        thread = payload["threads"][0]
        self.assertEqual((thread["state"], thread["reference_sha"]), ("accepted", head_b))

    def test_a_deleted_authors_marked_reply_is_demoted_without_a_lookup(self):
        ghost = dict(_comment(rt.stamp_reply(self.head, "fixed", f=1), association="MEMBER",
                              created="2026-10-05T00:00:00Z"), author=None)
        reply = _comment(rt.stamp_reply(self.head, "fixed", f=1), association="MEMBER", login="writer",
                         created="2026-10-04T00:00:00Z")
        page = _page([_thread("T", [self._root(1), reply, ghost])])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "writer"), text="write\n")
        payload, notices = self._read()
        self.assertEqual(payload["threads"][0]["state"], "changes-requested")
        self.assertEqual(notices, [])

    def test_a_failed_permission_lookup_for_a_marked_author_makes_the_read_unavailable(self):
        # Demoting their roots would reuse their F ids and re-post their signals: the read gates nothing.
        root = self._root(3, login="mate", association="MEMBER")
        page = _page([_thread("T", [root])])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "mate"), text="", exit_code=1)
        payload, notices = self._read()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["threads"], [])
        self.assertEqual([n.split(":")[0] for n in notices], [rt.REVIEW_THREADS_UNAVAILABLE])

    def test_a_demoted_roots_f_id_is_never_reused(self):
        root = self._root(4, login="triager", association="COLLABORATOR")
        page = _page([_thread("T", [root])])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "triager"), text="triage\n")
        payload, _ = self._read()
        self.assertEqual((payload["threads"][0]["origin"], payload["threads"][0]["f"]), ("operator", None))
        self.assertEqual(payload["next_f"], 5)

    def test_a_successor_naming_a_demoted_root_supersedes_it(self):
        old = self._root(4, login="former", association="MEMBER", url="https://x/f4")
        succ = _comment(rt.stamp_root(9, self.head, "src/a.txt", "line", 5, 8, [], "y", supersedes="F4"),
                        url="https://x/f9")
        page = _page([_thread("O", [old], resolved=True, resolved_by="me"), _thread("N", [succ])])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "former"), text="read\n")
        payload, _ = self._read()
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertEqual((by_id["O"]["origin"], by_id["O"]["superseded_by"]), ("operator", "F9"))
        self.assertEqual(payload["next_f"], 10)

    def test_a_dismiss_reply_counts_only_from_a_resolution_grade_author(self):
        def read(login, permission):
            self.entries = []
            root = self._root(1, flags=("security:SEC-1",))
            reply = _comment("dismiss: admin only", login=login, association="COLLABORATOR",
                             created="2026-10-05T00:00:00Z")
            self._answer(rt.query_argv("o/r", 5), _page([_thread("T", [root, reply], resolved=True, resolved_by="me")]))
            self._answer(rt.permission_argv("o/r", login), text=permission)
            payload, notices = self._read()
            return payload["threads"][0], notices
        thread, _ = read("triager", "triage\n")
        self.assertEqual((thread["reply_reason"], thread["dismiss_reason"]), ("dismiss: admin only", None))
        thread, _ = read("writer", "write\n")
        self.assertEqual(thread["dismiss_reason"], "admin only")
        # A failed lookup only means they cannot dismiss: the read stays available.
        self.entries = []
        thread, notices = read("ghost", "")
        self.assertIsNone(thread["dismiss_reason"])

    def test_an_untrusted_root_gates_nothing(self):
        page = _page([_thread("U", [_comment("hi", association="NONE", login="rando")]),
                      _thread("M", [_comment(rt.stamp_root(9, self.head, "src/a.txt", "file", None, None, [], "x"),
                                             association="CONTRIBUTOR", login="rando")])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        self.assertEqual(payload["threads"], [])
        self.assertEqual(payload["untrusted"]["threads"], 2)
        # A forged marked root mints no F id either.
        self.assertEqual(payload["next_f"], 1)

    def test_a_resolved_thread_whose_reference_is_missing_is_superseded(self):
        page = _page([_thread("S", [self._root(1, head="c" * 40, url="https://x/s")], resolved=True, resolved_by="me"),
                      _thread("N", [self._root(2, url="https://x/n", flags=()), ], )])
        page["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][1]["root"]["nodes"][0]["body"] = (
            rt.stamp_root(2, self.head, "src/a.txt", "line", 5, 8, [], "y", supersedes="F1"))
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertEqual(by_id["S"]["state"], "superseded")
        self.assertEqual(by_id["S"]["superseded_by"], "F2")
        self.assertIsNone(by_id["N"]["superseded_by"])

    def test_truncation_notice(self):
        page = _page([_thread("T", [self._root(1)], total=80)], comments_total=500)
        self._answer(rt.query_argv("o/r", 5), page)
        payload, notices = self._read()
        self.assertTrue(payload["available"])
        self.assertTrue(payload["truncated"])
        self.assertEqual([n.split(":")[0] for n in notices], [rt.REVIEW_THREADS_TRUNCATED])

    def test_failed_read_is_unavailable(self):
        self._answer(rt.query_argv("o/r", 5), text="", exit_code=1)
        payload, notices = self._read()
        self.assertTrue(payload["present"])
        self.assertFalse(payload["available"])
        self.assertEqual(payload["threads"], [])
        self.assertEqual([n.split(":")[0] for n in notices], [rt.REVIEW_THREADS_UNAVAILABLE])

    def test_a_failed_second_page_is_unavailable_not_partial(self):
        self._answer(rt.query_argv("o/r", 5), _page([_thread("T", [self._root(1)])], has_next=True, cursor="C"))
        self._answer(rt.query_argv("o/r", 5, after="C"), text="", exit_code=1)
        payload, notices = self._read()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["threads"], [])

    def _commit(self, text):
        (self.clone.path / "src").mkdir(exist_ok=True)
        (self.clone.path / "src" / "a.txt").write_text(text, encoding="utf-8")
        gitsandbox._run_git(["add", "src/a.txt"], cwd=self.clone.path)
        gitsandbox._run_git(["commit", "-q", "--allow-empty", "-m", "c"], cwd=self.clone.path)
        return gitsandbox._run_git(["rev-parse", "HEAD"], cwd=self.clone.path).stdout.strip()

    def test_a_resolved_batch_is_superseded_by_a_hunk_in_any_member(self):
        (self.clone.path / "src" / "b.txt").write_text("b1\n", encoding="utf-8")
        gitsandbox._run_git(["add", "src/b.txt"], cwd=self.clone.path)
        stamped = self._commit("".join("line %d\n" % i for i in range(1, 21)))
        root = _comment(rt.stamp_root(1, stamped, "src/a.txt", "file", None, None,
                                      ["routine:src/a.txt", "routine:src/b.txt"], "batch"), url="https://x/b1")
        self._answer(rt.query_argv("o/r", 5), _page([_thread("B", [root], resolved=True, resolved_by="me")]))
        self.head = stamped
        payload, _ = self._read()
        self.assertEqual(payload["threads"][0]["state"], "accepted")
        # A hunk in the non-anchor member after the resolution supersedes the whole batch.
        (self.clone.path / "src" / "b.txt").write_text("b2\n", encoding="utf-8")
        gitsandbox._run_git(["add", "src/b.txt"], cwd=self.clone.path)
        self.head = self._commit("".join("line %d\n" % i for i in range(1, 21)))
        self.entries = []
        self._answer(rt.query_argv("o/r", 5), _page([_thread("B", [root], resolved=True, resolved_by="me")]))
        payload, _ = self._read()
        self.assertEqual(payload["threads"][0]["state"], "superseded")

    def test_a_base_side_thread_is_checked_as_a_file_anchor(self):
        lines = ["line %d\n" % i for i in range(1, 21)]
        base = self._commit("".join(lines))
        lines[14] = "edited\n"  # line 15: outside the comment's 5..8, which number the BASE file
        self.head = self._commit("".join(lines))
        left = _thread("L", [_comment("why delete these?", commit=base, url="https://x/l")], resolved=True,
                       resolved_by="me")
        right = _thread("R", [_comment("fine", commit=base, url="https://x/r")], resolved=True, resolved_by="me")
        left["diffSide"], right["diffSide"] = "LEFT", "RIGHT"
        self._answer(rt.query_argv("o/r", 5), _page([left, right]))
        payload, _ = self._read()
        states = {t["id"]: t["state"] for t in payload["threads"]}
        self.assertEqual(states, {"L": "superseded", "R": "accepted"})

    def test_a_successor_supersedes_every_copy_of_a_duplicated_f_id(self):
        gone = "c" * 40
        copies = [_thread(tid, [self._root(1, head=gone, url="https://x/%s" % tid)], resolved=True, resolved_by="me")
                  for tid in ("A", "B")]
        successor = _thread("S", [_comment(rt.stamp_root(2, self.head, "src/a.txt", "line", 5, 8, [], "y",
                                                         supersedes="F1"))])
        self._answer(rt.query_argv("o/r", 5), _page(copies + [successor]))
        payload, _ = self._read()
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertEqual((by_id["A"]["superseded_by"], by_id["B"]["superseded_by"]), ("F2", "F2"))
        self.assertEqual(payload["head"], self.head)

    def test_a_stamp_out_of_position_is_human_text(self):
        # A root stamp as a reply, or a review stamp in a thread, neither resets the state nor moves
        # the reference commit.
        page = _page([_thread("T", [self._root(1), _comment("fix it", created="2026-10-04T00:00:00Z"),
                                    _comment(rt.stamp_root(7, OTHER, "src/a.txt", "file", None, None, [], "x"),
                                             created="2026-10-05T00:00:00Z"),
                                    _comment(rt.stamp_review(OTHER, "y"), created="2026-10-06T00:00:00Z")])])
        self._answer(rt.query_argv("o/r", 5), page)
        thread = self._read()[0]["threads"][0]
        self.assertEqual((thread["state"], thread["reference_sha"]), ("changes-requested", self.head))

    def test_a_path_gone_before_the_reference_reads_superseded(self):
        # The pipeline replied at the commit that deleted the file; the human then resolved.
        base = self._commit("".join("line %d\n" % i for i in range(1, 21)))
        gitsandbox._run_git(["rm", "-q", "src/a.txt"], cwd=self.clone.path)
        gitsandbox._run_git(["commit", "-q", "-m", "rm"], cwd=self.clone.path)
        self.head = gitsandbox._run_git(["rev-parse", "HEAD"], cwd=self.clone.path).stdout.strip()
        thread = _thread("D", [self._root(1, head=base, url="https://x/d"),
                               _comment(rt.stamp_reply(self.head, "removed it", f=1))], resolved=True, resolved_by="me")
        self._answer(rt.query_argv("o/r", 5), _page([thread]))
        self.assertEqual(self._read()[0]["threads"][0]["state"], "superseded")

    def test_a_file_thread_whose_path_is_gone_at_the_reference_reads_superseded(self):
        # A file anchor skips the range mapping, so the path check in `hunk_changed` is what decides.
        base = self.head
        gitsandbox._run_git(["rm", "-q", "src/a.txt"], cwd=self.clone.path)
        gitsandbox._run_git(["commit", "-q", "-m", "rm"], cwd=self.clone.path)
        self.head = gitsandbox._run_git(["rev-parse", "HEAD"], cwd=self.clone.path).stdout.strip()
        root = _comment(rt.stamp_root(1, base, "src/a.txt", "file", None, None, [], "x"), url="https://x/f")
        thread = _thread("F", [root, _comment(rt.stamp_reply(self.head, "removed it", f=1))], resolved=True,
                         resolved_by="me", subject="FILE")
        self._answer(rt.query_argv("o/r", 5), _page([thread]))
        self.assertEqual(self._read()[0]["threads"][0]["state"], "superseded")

    def test_a_base_side_thread_on_a_file_the_pr_deletes(self):
        # Opened on the removed lines, at the deleting head: absent at both is its normal state.
        gitsandbox._run_git(["rm", "-q", "src/a.txt"], cwd=self.clone.path)
        gitsandbox._run_git(["commit", "-q", "-m", "rm"], cwd=self.clone.path)
        gone = gitsandbox._run_git(["rev-parse", "HEAD"], cwd=self.clone.path).stdout.strip()
        thread = _thread("L", [_comment("why remove this?", commit=gone, url="https://x/l")], resolved=True,
                         resolved_by="me")
        thread["diffSide"] = "LEFT"
        self.head = gone
        self._answer(rt.query_argv("o/r", 5), _page([thread]))
        self.assertEqual(self._read()[0]["threads"][0]["state"], "accepted")
        self.head = self._commit("restored\n")
        self.entries = []
        self._answer(rt.query_argv("o/r", 5), _page([thread]))
        self.assertEqual(self._read()[0]["threads"][0]["state"], "superseded")

    def test_a_review_body_in_the_conversation_is_marked_only_by_its_stamp(self):
        page = _page([], reviews=[dict(_comment(rt.stamp_review(self.head, "threads")), state="COMMENTED"),
                                  dict(_comment(rt.MARKER + "\npasted"), state="COMMENTED")])
        self._answer(rt.query_argv("o/r", 5), page)
        conversation = json.loads(Path(self._read()[0]["conversation_path"]).read_text(encoding="utf-8"))
        self.assertEqual([c["marked"] for c in conversation], [True, False])

    def test_a_review_stamp_marks_only_for_the_viewer_or_a_resolution_grade_author(self):
        page = _page([], reviews=[
            dict(_comment(rt.stamp_review(self.head, "a"), login="triager", association="COLLABORATOR"),
                 state="COMMENTED", createdAt="2026-10-01T00:00:01Z"),
            dict(_comment(rt.stamp_review(self.head, "b"), login="writer", association="MEMBER"),
                 state="COMMENTED", createdAt="2026-10-01T00:00:02Z")])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "triager"), text="triage\n")
        self._answer(rt.permission_argv("o/r", "writer"), text="write\n")
        conversation = json.loads(Path(self._read()[0]["conversation_path"]).read_text(encoding="utf-8"))
        self.assertEqual([(c["author"], c["marked"]) for c in conversation], [("triager", False), ("writer", True)])

    def test_absent_shape(self):
        self.assertEqual(rt.absent_review_threads(), {"present": False})

    def test_this_viewers_pending_review_is_not_read(self):
        # A stranded pipeline review is visible only to its author; its threads must not gate.
        stranded = self._root(1, state="PENDING")
        page = _page([_thread("P", [stranded]),
                      _thread("L", [self._root(2), _comment("pending reply", state="PENDING")])],
                     reviews=[dict(_comment(rt.MARKER + "\nstranded"), state="PENDING"),
                              dict(_comment("submitted"), state="COMMENTED")])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        self.assertEqual([(t["id"], t["state"]) for t in payload["threads"]], [("L", "unreviewed")])
        self.assertEqual(payload["next_f"], 3)
        conversation = json.loads(Path(payload["conversation_path"]).read_text(encoding="utf-8"))
        self.assertEqual([c["body"] for c in conversation], ["submitted"])

    def test_reaching_the_page_backstop_is_unavailable(self):
        original = rt.MAX_THREAD_PAGES
        rt.MAX_THREAD_PAGES = 1
        self.addCleanup(setattr, rt, "MAX_THREAD_PAGES", original)
        self._answer(rt.query_argv("o/r", 5), _page([_thread("T", [self._root(1)])], has_next=True, cursor="C"))
        payload, notices = self._read()
        self.assertFalse(payload["available"])
        self.assertEqual([n.split(":")[0] for n in notices], [rt.REVIEW_THREADS_UNAVAILABLE])

    def test_a_marked_root_with_no_root_stamp_is_the_humans_thread(self):
        pasted = _comment(rt.MARKER + "\nno stamp, pasted by the operator")
        reply_rooted = _comment(rt.stamp_reply(self.head, "a reply stamp opening a thread"))
        page = _page([_thread("A", [pasted]), _thread("B", [reply_rooted])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        for thread in payload["threads"]:
            self.assertEqual((thread["origin"], thread["f"], thread["state"], thread["pending_reply"]),
                             ("operator", None, "operator-thread", False), thread["id"])
        self.assertEqual(payload["next_f"], 1)



# ---------------------------------------------------------------------------
# Phase 3: flags and thread-cited dismissals
# ---------------------------------------------------------------------------


def _signals(**kw):
    base = {"sensitive": [], "out_of_plan": [], "judgment": [], "security": [], "plan": [], "reasons": {},
            "rejected": []}
    base.update(kw)
    return base


def _pthread(f, path, rng, flags, state="unreviewed", superseded_by=None, subject=None, head=HEAD, url=None,
             supersedes=None):
    """A listed pipeline thread as `threads_path` holds it, its root comment stamped at ``head``."""
    subject = subject or ("line" if rng else "file")
    body = rt.stamp_root(f, head, path, subject, rng[0] if rng else None, rng[1] if rng else None, flags, "b",
                         supersedes=supersedes)
    return {"id": "T%d" % f, "url": url or "https://github.com/o/r/pull/5#discussion_r%d" % (1000 + f),
            "f": f, "o": None, "origin": "pipeline", "state": state, "pending_reply": False, "path": path,
            "subject": subject, "range": list(rng) if rng else None, "reference_sha": head, "flags": list(flags),
            "supersedes": supersedes, "superseded_by": superseded_by, "reply_reason": None,
            "comments": [{"author": "me", "body": body, "createdAt": "", "url": "", "marked": True}],
            "root_commit": head, "side": "RIGHT"}


def _othread(o, path, rng, state="operator-thread", side="RIGHT", body="please rename this"):
    return {"id": "O%d" % o, "url": "https://github.com/o/r/pull/5#discussion_r%d" % (2000 + o), "f": None, "o": o,
            "origin": "operator", "state": state, "pending_reply": False, "path": path,
            "subject": "line" if rng else "file", "range": list(rng) if rng else None, "reference_sha": OTHER,
            "flags": [], "supersedes": None, "superseded_by": None, "reply_reason": None,
            "comments": [{"author": "op", "body": body, "createdAt": "", "url": "", "marked": False}],
            "root_commit": OTHER, "side": side}


def _identity(from_sha, path, rng):
    return list(rng)


class BuildFlagsTests(unittest.TestCase):
    """`build_flags`, pure: anchoring, grouping, the cap, idempotence and successors (#145 phase 3)."""

    FILES = {"a.py": {"hunks": [(10, 5), (40, 3)]}, "b.py": {"hunks": [(1, 2)]}, "gone.py": {"hunks": []}}

    def flags(self, signals, threads=(), files=None, mapper=_identity, next_f=1):
        return rt.build_flags(files or self.FILES, list(threads), signals, mapper, next_f)

    def test_file_level_signals_anchor_on_the_whole_file(self):
        # SEC-2: a first-hunk anchor let a later hunk in the same file pass under a resolved thread; a file
        # thread's hunk check reads every change to the file.
        out = self.flags(_signals(sensitive=["a.py"], out_of_plan=["gone.py"], reasons={"gone.py": "needed"}))
        by_path = {t["path"]: t for t in out["new"]}
        self.assertEqual((by_path["a.py"]["subject"], by_path["a.py"]["start_line"], by_path["a.py"]["line"]),
                         ("file", None, None))
        self.assertEqual(by_path["gone.py"]["subject"], "file")
        self.assertEqual(by_path["gone.py"]["reason"], "needed")
        self.assertEqual([t["f"] for t in out["new"]], [1, 2])

    def test_flags_in_one_hunk_share_a_thread(self):
        out = self.flags(_signals(sensitive=["a.py"], judgment=[("contract", "a.py", [12, 13])],
                                  plan=[("a.py", [41, 42], "renamed the op")]))
        # The file-level sensitive flag takes the file thread; the two sites keep their own hunks.
        self.assertEqual(len(out["new"]), 3)
        by_flags = {tuple(t["flags"]): t for t in out["new"]}
        self.assertEqual(by_flags[("sensitive:a.py",)]["subject"], "file")
        judged = by_flags[("judgment:contract:a.py",)]
        self.assertEqual((judged["subject"], judged["start_line"], judged["line"]), ("line", 12, 13))
        self.assertEqual(by_flags[("plan:a.py",)]["plan"], ["renamed the op"])

    def test_sites_in_one_hunk_share_a_thread(self):
        out = self.flags(_signals(judgment=[("contract", "a.py", [12, 13])], plan=[("a.py", [11, 14], "renamed")]))
        self.assertEqual(len(out["new"]), 1)
        self.assertEqual(out["new"][0]["flags"], ["judgment:contract:a.py", "plan:a.py"])
        self.assertEqual((out["new"][0]["start_line"], out["new"][0]["line"]), (11, 14))

    def test_a_security_thread_carries_its_findings_claim(self):
        out = self.flags(_signals(security=[("SEC-1", "a.py", [12, 12])], claims={"SEC-1": "token logged"}))
        self.assertEqual(out["new"][0]["claims"], {"SEC-1": "token logged"})

    def test_a_site_outside_every_hunk_anchors_on_its_file_and_one_outside_the_diff_is_rejected(self):
        out = self.flags(_signals(plan=[("a.py", [100, 101], "x"), ("nope.py", [1, 1], "y")],
                                  security=[("SEC-4", "nope.py", None)]))
        self.assertEqual([(t["path"], t["subject"]) for t in out["new"]], [("a.py", "file")])
        self.assertEqual([r["reason"] for r in out["rejected"]], ["not-in-diff"])
        self.assertEqual(out["unanchored_security"], ["SEC-4"])

    def test_the_cap_collapses_files_then_keeps_the_priority_order(self):
        files = {"f%d.py" % i: {"hunks": [(1, 1), (10, 1)]} for i in range(10)}
        sig = _signals(
            judgment=[("seam", "f%d.py" % i, [1, 1]) for i in range(10)],
            plan=[("f%d.py" % i, [10, 10], "p") for i in range(10)],
            security=[("SEC-1", "f9.py", [10, 10])],
        )
        out = self.flags(sig, files=files)
        self.assertEqual(len(out["new"]), rt.THREAD_CAP)
        self.assertTrue(all(t["subject"] == "file" for t in out["new"]))
        # The security flag's file outranks the rest, so it is kept.
        self.assertEqual(out["new"][0]["path"], "f9.py")
        self.assertEqual(len({o["path"] for o in out["overflow"]}), 2)
        self.assertEqual(out["unanchored_security"], [])

    def test_security_overflow_joins_unanchored_security(self):
        files = {"f%d.py" % i: {"hunks": [(1, 1)]} for i in range(10)}
        sig = _signals(security=[("SEC-%d" % i, "f%d.py" % i, [1, 1]) for i in range(1, 10)])
        out = self.flags(sig, files=files)
        self.assertEqual(len(out["new"]), rt.THREAD_CAP)
        self.assertEqual(len(out["unanchored_security"]), 1)

    def test_a_re_run_on_an_unchanged_hunk_posts_nothing(self):
        head = _pthread(1, "a.py", [10, 14], ["sensitive:a.py", "judgment:contract:a.py"])
        out = self.flags(_signals(sensitive=["a.py"], judgment=[("contract", "a.py", [12, 13])]), threads=[head])
        self.assertEqual(out["new"], [])
        self.assertEqual({m["thread"] for m in out["matched"]}, {"F1"})

    def test_a_file_level_flag_matches_on_kind_and_path_after_its_first_hunk_moved(self):
        head = _pthread(1, "a.py", [2, 3], ["sensitive:a.py"])
        out = self.flags(_signals(sensitive=["a.py"]), threads=[head])
        self.assertEqual(out["new"], [])

    def test_a_site_outside_the_head_range_is_new_and_takes_the_next_id(self):
        head = _pthread(3, "a.py", [10, 11], ["judgment:contract:a.py"])
        out = self.flags(_signals(judgment=[("contract", "a.py", [41, 41])]), threads=[head], next_f=4)
        self.assertEqual([t["f"] for t in out["new"]], [4])

    def test_an_unmappable_head_matches_on_kind_and_path(self):
        head = _pthread(1, "a.py", [10, 11], ["judgment:seam:a.py"])
        out = self.flags(_signals(judgment=[("seam", "a.py", [41, 41])]), threads=[head], mapper=lambda *a: None)
        self.assertEqual(out["new"], [])

    def test_a_superseded_head_gets_one_successor_carrying_the_matched_flag(self):
        old = _pthread(1, "a.py", [10, 14], ["sensitive:a.py"], state="superseded")
        out = self.flags(_signals(sensitive=["a.py"], judgment=[("data", "b.py", [1, 1])]), threads=[old], next_f=2)
        self.assertEqual(len(out["new"]), 2)
        succ = out["new"][0]
        self.assertEqual((succ["f"], succ["supersedes"], succ["flags"]), (2, "F1", ["sensitive:a.py"]))
        self.assertEqual(out["new"][1]["flags"], ["judgment:data:b.py"])
        self.assertTrue(out["matched"][0]["rides_successor"])

    def test_successors_are_exempt_from_the_cap(self):
        files = {"f%d.py" % i: {"hunks": [(1, 1)]} for i in range(12)}
        old = [_pthread(i + 1, "f%d.py" % i, [1, 1], ["plan:f%d.py" % i], state="superseded") for i in range(10)]
        out = self.flags(_signals(judgment=[("seam", "f%d.py" % i, [1, 1]) for i in range(10, 12)]), threads=old,
                         files=files, next_f=11)
        self.assertEqual(len(out["new"]), 12)
        self.assertEqual(sum(1 for t in out["new"] if t["supersedes"]), 10)

    def test_a_chain_matches_only_its_head(self):
        f1 = _pthread(1, "a.py", [10, 14], ["sensitive:a.py"], state="superseded", superseded_by="F2")
        f2 = _pthread(2, "a.py", [10, 14], ["sensitive:a.py"], state="superseded", superseded_by="F3",
                      supersedes="F1")
        f3 = _pthread(3, "a.py", [10, 14], ["sensitive:a.py"], supersedes="F2")
        out = self.flags(_signals(sensitive=["a.py"]), threads=[f1, f2, f3], next_f=4)
        self.assertEqual(out["new"], [])
        self.assertEqual(out["matched"], [{"key": "sensitive:a.py", "thread": "F3"}])

    def test_a_superseded_human_thread_gets_a_successor_with_no_flags(self):
        human = _othread(1, "a.py", [12, 12], state="superseded", body="Is this safe?\nmore")
        out = self.flags(_signals(), threads=[human], next_f=5)
        succ = out["new"][0]
        self.assertEqual((succ["f"], succ["flags"], succ["supersedes"]), (5, [], human["url"]))
        self.assertEqual(succ["predecessor"]["first_line"], "Is this safe?")

    def test_a_demoted_stamped_root_maps_its_range_from_its_root_commit(self):
        # SEC-3: a trusted author without a resolution-grade permission opened the thread with a root stamp;
        # read_threads read it as an operator thread, whose range numbers its root comment's commit.
        forged = rt.stamp_root(7, HEAD, "a.py", "line", 12, 12, [], "b")
        human = _othread(1, "a.py", [12, 12], state="superseded", body=forged)
        seen = []
        self.flags(_signals(), threads=[human], mapper=lambda sha, path, rng: seen.append(sha) or list(rng))
        self.assertEqual(seen, [OTHER])

    def test_a_demoted_roots_successor_quotes_its_text_not_its_stamp(self):
        forged = rt.stamp_root(7, HEAD, "a.py", "line", 12, 12, [], "Is this safe?")
        human = _othread(1, "a.py", [12, 12], state="superseded", body=forged)
        out = self.flags(_signals(), threads=[human])
        self.assertEqual(out["new"][0]["predecessor"]["first_line"], "Is this safe?")

    def test_a_base_side_human_thread_successor_anchors_on_the_file(self):
        human = _othread(1, "a.py", [12, 12], state="superseded", side="LEFT")
        out = self.flags(_signals(), threads=[human])
        self.assertEqual(out["new"][0]["subject"], "file")

    def test_a_fixed_security_head_that_is_superseded_still_gets_its_successor(self):
        old = _pthread(1, "a.py", [10, 11], ["security:SEC-2"], state="superseded")
        out = self.flags(_signals(), threads=[old], next_f=2)
        self.assertEqual([(t["supersedes"], t["security"]) for t in out["new"]], [("F1", ["SEC-2"])])

    def test_a_head_whose_path_left_the_diff_retires_or_joins_unanchored_security(self):
        sec = _pthread(1, "left.py", [1, 1], ["security:SEC-3"], state="superseded")
        plan = _pthread(2, "left.py", [5, 5], ["plan:left.py"], state="superseded")
        out = self.flags(_signals(), threads=[sec, plan])
        self.assertEqual(out["new"], [])
        self.assertEqual(out["unanchored_security"], ["SEC-3"])
        # Both stop gating — no successor can replace them — and the security one goes to the card.
        self.assertEqual(out["retired"], [{"thread": "T1", "ref": "F1", "reason": "path-left-diff"},
                                          {"thread": "T2", "ref": "F2", "reason": "path-left-diff"}])

    def test_a_flag_on_a_renamed_files_old_path_anchors_on_its_new_name(self):
        files = {"lib/keys.py": {"hunks": [], "renamed_from": "secrets/keys.py"}}
        out = self.flags(_signals(sensitive=["secrets/keys.py"]), files=files)
        self.assertEqual([(t["path"], t["subject"], t["flags"]) for t in out["new"]],
                         [("lib/keys.py", "file", ["sensitive:secrets/keys.py"])])
        self.assertEqual(out["rejected"], [])

    def test_a_rider_whose_successor_cannot_post_takes_its_own_thread(self):
        old = _pthread(1, "left.py", [1, 1], ["security:SEC-2"], state="superseded")
        out = self.flags(_signals(security=[("SEC-2", "b.py", [1, 1])]), threads=[old], next_f=2)
        self.assertEqual([(t["path"], t["security"]) for t in out["new"]], [("b.py", ["SEC-2"])])
        # It has a thread now, so it is not unanchored, though its old head's path left the diff.
        self.assertEqual(out["unanchored_security"], [])

    def test_a_security_site_that_moved_file_matches_its_live_thread_by_key(self):
        live = _pthread(1, "a.py", [10, 11], ["security:SEC-2"])
        out = self.flags(_signals(security=[("SEC-2", "b.py", [1, 1])]), threads=[live])
        self.assertEqual(out["new"], [])
        self.assertEqual(out["matched"], [{"key": "security:SEC-2", "thread": "F1"}])

    def test_a_moved_security_site_does_not_ride_a_superseded_head_on_the_old_file(self):
        old = _pthread(1, "a.py", [10, 11], ["security:SEC-2"], state="superseded")
        out = self.flags(_signals(security=[("SEC-2", "b.py", [1, 1])]), threads=[old], next_f=2)
        self.assertEqual(sorted((t["path"], t["supersedes"]) for t in out["new"]), [("a.py", "F1"), ("b.py", None)])

    def test_a_security_site_outside_the_diff_is_rejected_and_falls_back_to_the_anchor_path(self):
        out = self.flags(_signals(security=[("SEC-5", "nope.py", [3, 3], "a.py")]))
        self.assertEqual([(t["path"], t["security"]) for t in out["new"]], [("a.py", ["SEC-5"])])
        self.assertEqual([r["source"] for r in out["rejected"]], ["security-sites"])

    def test_a_security_key_matches_a_live_head_wherever_its_site_moved_in_the_file(self):
        live = _pthread(1, "a.py", [10, 11], ["security:SEC-2"])
        out = self.flags(_signals(security=[("SEC-2", "a.py", [41, 41])]), threads=[live])
        self.assertEqual(out["new"], [])

    def test_a_head_on_a_renamed_file_gets_its_successor_on_the_new_name(self):
        files = {"lib/new.py": {"hunks": [(1, 3)], "renamed_from": "lib/old.py"}}
        old = _pthread(1, "lib/old.py", [1, 1], ["plan:lib/old.py"], state="superseded")
        out = self.flags(_signals(plan=[("lib/new.py", [2, 2], "x")]), threads=[old], files=files,
                         mapper=lambda *a: None, next_f=2)
        self.assertEqual(out["retired"], [])
        self.assertEqual([(t["path"], t["supersedes"], t["flags"]) for t in out["new"]],
                         [("lib/new.py", "F1", ["plan:lib/old.py", "plan:lib/new.py"])])

    def test_a_head_range_is_mapped_once(self):
        calls = []
        head = _pthread(1, "a.py", [10, 14], ["judgment:seam:a.py", "plan:a.py"])

        def mapper(*args):
            calls.append(args)
            return [10, 14]

        self.flags(_signals(judgment=[("seam", "a.py", [11, 11])], plan=[("a.py", [12, 12], "x")]), threads=[head],
                   mapper=mapper)
        self.assertEqual(len(calls), 1)

    def test_a_missing_reference_with_the_path_in_the_diff_yields_a_file_successor(self):
        old = _pthread(1, "a.py", [10, 11], ["judgment:seam:a.py"], state="superseded")
        out = self.flags(_signals(), threads=[old], mapper=lambda *a: None)
        self.assertEqual(out["new"][0]["subject"], "file")

    def test_a_mapped_range_that_leaves_every_hunk_falls_back_to_the_file(self):
        old = _pthread(1, "a.py", [10, 11], ["judgment:seam:a.py"], state="superseded")
        out = self.flags(_signals(), threads=[old], mapper=lambda *a: [30, 31])
        self.assertEqual(out["new"][0]["subject"], "file")
        out = self.flags(_signals(), threads=[old], mapper=lambda *a: [40, 41])
        self.assertEqual((out["new"][0]["subject"], out["new"][0]["start_line"]), ("line", 40))


def _rsig(routine, kinds=None, **kw):
    return _signals(routine=list(routine), routine_kinds=kinds or {p: "test" for p in routine}, **kw)


class BatchFlagsTests(unittest.TestCase):
    """`build_flags`' routine batch (#163): one thread for every routine out-of-plan file, re-cut through
    successors, never dropping a file from a person's view."""

    FILES = {"a_test.py": {"hunks": [(1, 2)]}, "b_test.py": {"hunks": [(3, 1)]}, "c_test.py": {"hunks": [(5, 1)]},
             "x.py": {"hunks": [(1, 1)]}, "z_gone.py": {"hunks": []}}

    def flags(self, signals, threads=(), files=None, next_f=1):
        return rt.build_flags(files or self.FILES, list(threads), signals, _identity, next_f)

    def test_routine_files_share_one_batch_anchored_on_the_first_member_with_hunks(self):
        out = self.flags(_rsig(["z_gone.py", "b_test.py", "a_test.py"], kinds={"z_gone.py": "fallout",
                                                                              "b_test.py": "test", "a_test.py": "test"},
                               reasons={"a_test.py": "covers the job"}, reasons_trusted=True))
        self.assertEqual(len(out["new"]), 1)
        batch = out["new"][0]
        self.assertEqual((batch["path"], batch["subject"]), ("a_test.py", "file"))
        self.assertEqual(batch["flags"], ["routine:a_test.py", "routine:b_test.py", "routine:z_gone.py"])
        self.assertEqual(batch["members"], [{"path": "a_test.py", "kind": "test", "reason": "covers the job"},
                                            {"path": "b_test.py", "kind": "test", "reason": None},
                                            {"path": "z_gone.py", "kind": "fallout", "reason": None}])
        self.assertEqual(rt.batch_members(batch["flags"]), ["a_test.py", "b_test.py", "z_gone.py"])

    def test_untrusted_reasons_never_reach_the_members(self):
        out = self.flags(_rsig(["a_test.py"], reasons={"a_test.py": "trust me"}, reasons_trusted=False))
        self.assertEqual(out["new"][0]["members"], [{"path": "a_test.py", "kind": "test", "reason": None}])

    def test_a_deleted_member_anchors_only_when_no_member_has_hunks(self):
        out = self.flags(_rsig(["z_gone.py"]))
        self.assertEqual(out["new"][0]["path"], "z_gone.py")

    def test_a_routine_file_with_its_own_out_of_plan_head_posts_nothing(self):
        head = _pthread(1, "a_test.py", None, ["out-of-plan:a_test.py"], state="accepted")
        out = self.flags(_rsig(["a_test.py"]), threads=[head], next_f=2)
        self.assertEqual(out["new"], [])
        self.assertEqual(out["matched"], [{"key": "routine:a_test.py", "thread": "F1"}])

    def test_a_routine_file_riding_its_own_superseded_head_adds_no_routine_key(self):
        head = _pthread(1, "a_test.py", None, ["out-of-plan:a_test.py"], state="superseded")
        out = self.flags(_rsig(["a_test.py"]), threads=[head], next_f=2)
        self.assertEqual([t["flags"] for t in out["new"]], [["out-of-plan:a_test.py"]])

    def test_an_unreviewed_batch_plus_a_new_routine_file_is_re_cut(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py"])
        out = self.flags(_rsig(["a_test.py", "b_test.py"]), threads=[batch], next_f=4)
        self.assertEqual(len(out["new"]), 1)
        succ = out["new"][0]
        self.assertEqual((succ["f"], succ["supersedes"], succ["flags"]),
                         (4, "F3", ["routine:a_test.py", "routine:b_test.py"]))
        self.assertEqual([m["path"] for m in succ["members"]], ["a_test.py", "b_test.py"])

    def test_an_unchanged_unreviewed_batch_posts_nothing(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"])
        out = self.flags(_rsig(["a_test.py", "b_test.py"]), threads=[batch], next_f=4)
        self.assertEqual((out["new"], len(out["matched"])), ([], 2))

    def test_an_accepted_batch_is_never_re_cut_and_a_new_routine_file_forms_a_new_batch(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py"], state="accepted")
        out = self.flags(_rsig(["a_test.py", "b_test.py"]), threads=[batch], next_f=4)
        self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]], [(["routine:b_test.py"], None)])
        self.assertEqual(out["matched"], [{"key": "routine:a_test.py", "thread": "F3"}])

    def test_a_split_member_leaves_the_successor_and_gets_its_own_thread(self):
        # The split path reaches build_flags as an out-of-plan file (gather_signals drops it from routine).
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"], state="changes-requested")
        out = self.flags(_rsig(["a_test.py"], out_of_plan=["b_test.py"], splits=[(3, "b_test.py")]),
                         threads=[batch], next_f=4)
        by_flags = {tuple(t["flags"]): t for t in out["new"]}
        self.assertEqual(by_flags[("routine:a_test.py",)]["supersedes"], "F3")
        self.assertIsNone(by_flags[("out-of-plan:b_test.py",)]["supersedes"])
        self.assertEqual(len(out["new"]), 2)

    def test_a_changes_requested_batch_no_split_names_is_left_as_it_is(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py"], state="changes-requested")
        out = self.flags(_rsig(["a_test.py", "b_test.py"]), threads=[batch], next_f=4)
        self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]], [(["routine:b_test.py"], None)])

    def test_a_member_reclassified_consequential_gets_its_own_thread(self):
        for state in ("unreviewed", "superseded"):
            batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"], state=state)
            out = self.flags(_rsig(["a_test.py"], out_of_plan=["b_test.py"]), threads=[batch], next_f=4)
            by_flags = {tuple(t["flags"]): t for t in out["new"]}
            self.assertEqual(by_flags[("routine:a_test.py",)]["supersedes"], "F3", state)
            self.assertIn(("out-of-plan:b_test.py",), by_flags, state)

    def test_a_superseded_batch_re_anchors_and_retires_only_when_empty(self):
        batch = _pthread(3, "gone_test.py", None, ["routine:gone_test.py", "routine:b_test.py"], state="superseded")
        out = self.flags(_rsig(["b_test.py"]), threads=[batch], next_f=4)
        self.assertEqual((out["new"][0]["path"], out["new"][0]["flags"]), ("b_test.py", ["routine:b_test.py"]))
        out = self.flags(_rsig([]), threads=[batch], next_f=4)
        self.assertEqual((out["new"], [r["reason"] for r in out["retired"]]), ([], ["batch-emptied"]))

    def test_a_superseded_batch_with_an_unchanged_set_still_gets_a_successor(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py"], state="superseded")
        out = self.flags(_rsig(["a_test.py"]), threads=[batch], next_f=4)
        self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]], [(["routine:a_test.py"], "F3")])

    def test_an_unknown_split_line_is_rejected(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py"])
        single = _pthread(5, "x.py", None, ["out-of-plan:x.py"])
        out = self.flags(_rsig(["a_test.py"], splits=[(3, "b_test.py"), (5, "x.py"), (9, "a_test.py")]),
                         threads=[batch, single], next_f=6)
        self.assertEqual([(r["line"], r["reason"]) for r in out["rejected"]],
                         [("F3 b_test.py", "not-a-member"), ("F5 x.py", "not-a-member"), ("F9 a_test.py", "not-a-member")])

    def test_the_batch_counts_once_ranks_after_out_of_plan_and_overflows_as_one_entry(self):
        files = dict(self.FILES, **{"o%d.py" % i: {"hunks": [(1, 1)]} for i in range(8)})
        sig = _rsig(["a_test.py", "b_test.py"], out_of_plan=["o%d.py" % i for i in range(7)])
        out = self.flags(sig, files=files)
        self.assertEqual(len(out["new"]), 8)
        self.assertEqual(out["new"][-1]["flags"], ["routine:a_test.py", "routine:b_test.py"])
        out = self.flags(dict(sig, out_of_plan=["o%d.py" % i for i in range(8)]), files=files)
        self.assertEqual(out["overflow"], [{"key": "routine", "paths": ["a_test.py", "b_test.py"], "reason": "cap"}])

    def test_no_routine_signal_gives_per_file_threads_as_today(self):
        out = self.flags(_signals(out_of_plan=["a_test.py", "b_test.py"]))
        self.assertEqual([t["flags"] for t in out["new"]], [["out-of-plan:a_test.py"], ["out-of-plan:b_test.py"]])
        self.assertTrue(all(t["members"] is None for t in out["new"]))


class GatherTriageTests(unittest.TestCase):
    """`gather_signals`' triage and split inputs (#163): routine only on an explicit, kind-carrying verdict."""

    ASSESS = {"payload": {"out_of_plan": ["a_test.py", "b_test.py", "s.py", "j.py", "x.py"],
                          "sensitive": {"matches": [{"path": "s.py"}], "error": None},
                          "judgment": {"signals": [{"dimension": "seam", "site": "j.py:1", "evidence": "e"}]}}}

    def triage(self, text):
        import parse
        return parse.scan_out_of_plan_triage(text)

    def test_routine_needs_a_routine_verdict_and_no_other_signal_or_split(self):
        verdict = self.triage("## Triage\n- routine · a_test.py — test: t\n- routine · b_test.py — fixture: f\n"
                              "- routine · s.py — fallout: f\n- routine · j.py — fallout: f\n")
        sig = rt.gather_signals("", self.ASSESS, [], [], triage=verdict, split_lines=["F3 b_test.py", "nonsense"])
        self.assertEqual(sig["routine"], ["a_test.py"])
        self.assertEqual(sig["routine_kinds"], {"a_test.py": "test"})
        self.assertEqual(sig["out_of_plan"], ["b_test.py", "s.py", "j.py", "x.py"])
        self.assertEqual(sig["splits"], [(3, "b_test.py")])
        self.assertEqual([r["reason"] for r in sig["rejected"]], ["unparsed"])

    def test_no_triage_ambiguous_or_absent_batches_nothing(self):
        for verdict in (None, self.triage("## Triage\ncode: AMBIGUOUS\n- routine · a_test.py — test: t\n"),
                        self.triage("no section")):
            sig = rt.gather_signals("", self.ASSESS, [], [], triage=verdict)
            self.assertEqual(sig["routine"], [])
            self.assertEqual(len(sig["out_of_plan"]), 5)

    def test_an_omitted_or_consequential_file_keeps_its_own_flag(self):
        verdict = self.triage("## Triage\n- consequential · a_test.py — changes behaviour\n")
        sig = rt.gather_signals("", self.ASSESS, [], [], triage=verdict)
        self.assertEqual(sig["routine"], [])


class GatherSignalsTests(unittest.TestCase):
    BODY = (
        "## Out-of-plan changes\n- `src/x.py` — needed a helper\n\n"
        "## Security review\n- Reviewed: %s\n"
        "- SEC-1 · open — token logged — src/net.py / Client.send — exposure · high · exploit: log read\n"
        "- SEC-2 · fixed (commit abc1234) — sql — app/q.py / build\n"
        "- SEC-3 · open — path join — app/up.rb / Upload#path — traversal · high · exploit: ../\n"
    ) % ("c" * 40)

    def test_inputs_parse_and_a_failed_sensitive_read_gives_no_flag(self):
        assess = {"status": "ok", "payload": {
            "sensitive": {"matches": [{"pattern": "src/*", "path": "src/x.py"}], "error": None},
            "out_of_plan": ["src/x.py"],
            "judgment": {"signals": [{"dimension": "seam", "site": "src/y.py:3-4", "evidence": "e"}]},
        }}
        sig = rt.gather_signals(self.BODY, assess, ["SEC-1 src/net.py:7", "garbage"], ["src/y.py:9 — renamed", "bad"])
        self.assertEqual(sig["sensitive"], ["src/x.py"])
        self.assertEqual(sig["judgment"], [("seam", "src/y.py", [3, 4])])
        self.assertEqual(sig["security"], [("SEC-1", "src/net.py", [7, 7], "src/net.py"),
                                           ("SEC-3", "app/up.rb", None, "app/up.rb")])
        self.assertEqual(sig["plan"], [("src/y.py", [9, 9], "renamed")])
        self.assertEqual(sig["reasons"], {"src/x.py": "needed a helper"})
        self.assertEqual(len(sig["rejected"]), 2)
        bad = {"payload": {"judgment": {"signals": [{"dimension": "seam", "site": "src/y.py", "evidence": "e"}]}}}
        self.assertEqual([r["source"] for r in rt.gather_signals("", bad, [], [])["rejected"]], ["judgment"])
        failed = {"payload": {"sensitive": {"matches": [], "error": "unreadable declaration"}, "out_of_plan": []}}
        self.assertEqual(rt.gather_signals("", failed, [], [])["sensitive"], [])


class ConfirmDismissalsTests(unittest.TestCase):
    URL = "https://github.com/o/r/pull/5#discussion_r9"

    def security(self, reason="the route is admin-only", cite=True):
        note = "operator: %s%s" % (reason, " · thread: %s" % self.URL if cite else "")
        body = "## Security review\n- Reviewed: %s\n- SEC-1 · dismissed — c — a.py / f — %s\n" % ("c" * 40, note)
        import parse
        return parse.scan_security(body)

    def threads(self, state="accepted", reply="dismiss: the route is admin-only", available=True,
                origin="pipeline", flags=("security:SEC-1",), claim="c", path="a.py", may_dismiss=True):
        # `dismiss_reason` as read_threads derives it: the reply's, only from a resolution-grade author (SEC-4).
        return {"present": True, "available": available,
                "threads": [{"url": self.URL, "state": state, "reply_reason": rt.reason_line(reply),
                             "dismiss_reason": rt.dismissal_reason(reply) if may_dismiss else None,
                             "origin": origin, "flags": list(flags), "claim": claim, "path": path}]}

    def test_a_reply_by_an_author_who_cannot_resolve_never_confirms(self):
        # SEC-4: a triage collaborator's `dismiss:` after a write-holder's plain resolution is not a dismissal.
        self.assertIs(rt.confirm_dismissals(self.security(), self.threads(may_dismiss=False))
                      ["entries"][0]["thread"]["confirmed"], False)

    def test_an_anchor_on_another_file_never_confirms(self):
        # SEC-5: a body anchor re-pointed at another file cannot borrow the thread.
        self.assertIs(rt.confirm_dismissals(self.security(), self.threads(path="b.py"))
                      ["entries"][0]["thread"]["confirmed"], False)

    def test_a_matching_trusted_reply_and_resolution_confirms_on_any_pr(self):
        out = rt.confirm_dismissals(dict(self.security(), dismissals_trusted=False), self.threads())
        self.assertEqual(out["entries"][0]["thread"], {"url": self.URL, "confirmed": True})
        self.assertFalse(out["dismissals_trusted"])  # untouched: the citation, not the PR, decides

    def test_a_body_reason_differing_from_the_reply_is_not_confirmed(self):
        out = rt.confirm_dismissals(self.security(reason="it is fine"), self.threads())
        self.assertFalse(out["entries"][0]["thread"]["confirmed"])

    def test_an_unresolved_thread_or_no_reply_is_not_confirmed(self):
        for threads in (self.threads(state="changes-requested"), self.threads(reply=None)):
            self.assertIs(rt.confirm_dismissals(self.security(), threads)["entries"][0]["thread"]["confirmed"], False)

    def test_another_findings_or_an_operator_thread_never_confirms(self):
        for threads in (self.threads(flags=("security:SEC-9",)), self.threads(origin="operator", flags=())):
            self.assertIs(rt.confirm_dismissals(self.security(), threads)["entries"][0]["thread"]["confirmed"], False)

    def test_a_failed_read_is_unknown_not_false(self):
        for threads in (self.threads(available=False), {"present": False}):
            self.assertIsNone(rt.confirm_dismissals(self.security(), threads)["entries"][0]["thread"]["confirmed"])

    def test_a_non_citing_dismissal_carries_no_thread(self):
        out = rt.confirm_dismissals(self.security(cite=False), self.threads())
        self.assertIsNone(out["entries"][0]["thread"])

    def test_normalised_whitespace_and_dashes_still_match(self):
        out = rt.confirm_dismissals(self.security(reason="admin - only"), self.threads(reply="Dismiss:  admin —  only"))
        self.assertTrue(out["entries"][0]["thread"]["confirmed"])

    def test_a_reply_that_is_not_a_dismissal_never_confirms(self):
        # SEC-1: the operator's fix request, cited by a body line rewritten to `dismissed`, is not a dismissal.
        for reply in ("the route is admin-only", "please fix this", "dismiss:"):
            out = rt.confirm_dismissals(self.security(reason=reply), self.threads(reply=reply))
            self.assertIs(out["entries"][0]["thread"]["confirmed"], False, reply)

    def test_a_claim_the_thread_was_not_posted_for_never_confirms(self):
        # SEC-1: a dismissed line rewritten to describe another finding cannot borrow this thread.
        for claim in ("another flaw", None):
            out = rt.confirm_dismissals(self.security(), self.threads(claim=claim))
            self.assertIs(out["entries"][0]["thread"]["confirmed"], False, claim)

    def test_dismissal_reason_and_root_claim_read_their_lines(self):
        self.assertEqual(rt.dismissal_reason("DISMISS:  admin only"), "admin only")
        self.assertIsNone(rt.dismissal_reason("fix it, do not dismiss: later"))
        self.assertIsNone(rt.dismissal_reason(None))
        self.assertEqual(rt.root_claim("intro\nClaim:  token  logged \nmore"), "token logged")
        self.assertIsNone(rt.root_claim("no claim here"))


class FlagsCliTests(unittest.TestCase):
    """`run_flags` against a real sandbox repo: the gated diff's files, and the spec it writes."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.base = self._commit({"src/a.txt": "".join("line %d\n" % i for i in range(1, 21)),
                                  "src/old.txt": "same\n", "src/del.txt": "x\n"})
        self.head = self._commit({"src/a.txt": "".join(("LINE %d\n" if i == 5 else "line %d\n") % i
                                                       for i in range(1, 21))},
                                 delete=("src/del.txt",), move=("src/old.txt", "src/new.txt"))

    def _git(self, *args):
        return gitsandbox._run_git(list(args), cwd=self.clone.path)

    def _commit(self, files, delete=(), move=None):
        for name, text in files.items():
            target = self.clone.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            self._git("add", name)
        for name in delete:
            self._git("rm", "-q", name)
        if move:
            self._git("mv", move[0], move[1])
        self._git("commit", "-q", "-m", "c")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def test_diff_files_reads_hunks_deletions_and_pure_renames(self):
        files = rt._diff_files(self.clone.path, self.base, self.head)
        self.assertEqual(files["src/a.txt"], {"hunks": [(5, 1)]})
        self.assertEqual(files["src/del.txt"], {"hunks": []})
        self.assertEqual(files["src/new.txt"], {"hunks": [], "renamed_from": "src/old.txt"})

    def test_an_unreadable_diff_posts_nothing_and_retires_nothing(self):
        body = self.scratch / "body.md"
        body.write_text("", encoding="utf-8")
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"payload": {"range": {"base": "f" * 40}, "sensitive": {"matches": []},
                                                  "out_of_plan": ["src/a.txt"]}}), encoding="utf-8")
        threads = self.scratch / "threads.json"
        threads.write_text(json.dumps([_pthread(1, "src/a.txt", [5, 5], ["plan:src/a.txt"], state="superseded")]),
                           encoding="utf-8")
        out, notices = rt.run_flags(self.clone.path, str(body), str(assess), str(threads), "main", self.head,
                                    str(self.scratch))
        self.assertFalse(out["diff_available"])
        self.assertEqual((out["new"], out["retired"], out["spec_path"]), ([], [], None))
        self.assertTrue(notices[0].startswith("REVIEW_THREADS_DIFF_UNAVAILABLE"))

    def test_no_merge_base_is_a_diff_that_cannot_be_trusted(self):
        body = self.scratch / "body.md"
        body.write_text("", encoding="utf-8")
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"payload": {"range": {"base": None}, "out_of_plan": ["src/a.txt"]}}),
                          encoding="utf-8")
        threads = self.scratch / "threads.json"
        threads.write_text("[]", encoding="utf-8")
        out, notices = rt.run_flags(self.clone.path, str(body), str(assess), str(threads), "main", self.head,
                                    str(self.scratch))
        self.assertFalse(out["diff_available"])
        self.assertEqual(out["new"], [])

    def test_no_threads_file_is_refused_rather_than_read_as_no_threads(self):
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"payload": {"range": {"base": self.base}}}), encoding="utf-8")
        with self.assertRaises(SystemExit) as raised:
            rt.run_flags(self.clone.path, "x", str(assess), "", "main", self.head, str(self.scratch))
        self.assertEqual(raised.exception.code, 2)

    def test_run_flags_writes_a_spec_with_one_body_per_thread(self):
        body = self.scratch / "body.md"
        body.write_text("## Out-of-plan changes\n- `src/del.txt` — dropped the fixture\n", encoding="utf-8")
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"status": "ok", "payload": {
            "range": {"base": self.base}, "sensitive": {"matches": [{"path": "src/a.txt"}], "error": None},
            "out_of_plan": ["src/del.txt"], "judgment": None}}), encoding="utf-8")
        threads = self.scratch / "threads.json"
        threads.write_text("[]", encoding="utf-8")
        out, notices = rt.run_flags(self.clone.path, str(body), str(assess), str(threads), "main", self.head,
                                    str(self.scratch / "s"))
        self.assertEqual(notices, [])
        spec = json.loads(Path(out["spec_path"]).read_text(encoding="utf-8"))
        self.assertEqual(spec["head"], self.head)
        self.assertEqual([(t["path"], t["subject"]) for t in spec["threads"]],
                         [("src/a.txt", "file"), ("src/del.txt", "file")])
        self.assertEqual(len({t["body"] for t in spec["threads"]}), 2)
        self.assertEqual(out["new"][1]["reason"], "dropped the fixture")

    def test_nothing_new_writes_no_spec(self):
        body = self.scratch / "body.md"
        body.write_text("", encoding="utf-8")
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"payload": {"range": {"base": self.base}, "sensitive": {"matches": []},
                                                  "out_of_plan": []}}), encoding="utf-8")
        threads = self.scratch / "threads.json"
        threads.write_text("[]", encoding="utf-8")
        out, _ = rt.run_flags(self.clone.path, str(body), str(assess), str(threads), "main", self.head,
                              str(self.scratch))
        self.assertIsNone(out["spec_path"])
        self.assertEqual(out["new"], [])


class NarrowOutOfPlanTests(unittest.TestCase):
    """`narrow_out_of_plan` (#163): the basename and generated exemptions, pure."""

    def test_a_unique_basename_matching_a_slash_free_span_is_exempt(self):
        out = rt.narrow_out_of_plan(["app/lib/guard.rb", "src/x.py"], ["app/lib/guard.rb", "src/x.py"],
                                    ["guard.rb"], set())
        self.assertEqual(out, {"remaining": ["src/x.py"], "exempt": [{"path": "app/lib/guard.rb", "rule": "basename"}]})

    def test_a_shared_basename_keeps_every_thread(self):
        changed = ["a/index.ts", "b/index.ts"]
        out = rt.narrow_out_of_plan(changed, changed, ["index.ts"], set())
        self.assertEqual(out, {"remaining": changed, "exempt": []})
        # The count is over every changed path, not only the out-of-plan ones.
        out = rt.narrow_out_of_plan(["a/index.ts"], changed, ["index.ts", "b/index.ts"], set())
        self.assertEqual(out["remaining"], ["a/index.ts"])

    def test_a_span_naming_a_root_file_is_a_literal_path_not_a_basename(self):
        out = rt.narrow_out_of_plan(["docs/README.md"], ["docs/README.md"], ["README.md"], set(), root_files={"README.md"})
        self.assertEqual(out, {"remaining": ["docs/README.md"], "exempt": []})
        # An unlistable root exempts nothing by basename.
        out = rt.narrow_out_of_plan(["x/guard.rb"], ["x/guard.rb"], ["guard.rb"], set(), root_files=None)
        self.assertEqual(out["remaining"], ["x/guard.rb"])

    def test_a_span_with_a_slash_never_triggers_the_basename_rule(self):
        out = rt.narrow_out_of_plan(["lib/b/guard.rb"], ["lib/b/guard.rb"], ["lib/a/guard.rb", "a/guard.rb"], set())
        self.assertEqual(out["remaining"], ["lib/b/guard.rb"])

    def test_no_spans_or_no_changed_paths_exempts_nothing_by_basename(self):
        self.assertEqual(rt.narrow_out_of_plan(["a/g.rb"], ["a/g.rb"], None, set())["remaining"], ["a/g.rb"])
        self.assertEqual(rt.narrow_out_of_plan(["a/g.rb"], None, ["g.rb"], set())["remaining"], ["a/g.rb"])

    def test_generated_at_both_sides_is_exempt(self):
        out = rt.narrow_out_of_plan(["sorbet/rbi/dsl/a.rbi"], ["sorbet/rbi/dsl/a.rbi"], [], {"sorbet/rbi/dsl/a.rbi"})
        self.assertEqual(out["exempt"], [{"path": "sorbet/rbi/dsl/a.rbi", "rule": "generated"}])

    def test_generated_only_at_head_is_kept(self):
        # `narrow` intersects the two sides: a file whose attribute only the head adds is not exempt.
        sides = {"b" * 40: {"gen/a.rbi"}, "h" * 40: {"gen/a.rbi", "api/client.ts"}}
        original = rt._generated_paths
        rt._generated_paths = lambda root, rev, paths, scratch: set(sides[rev])
        self.addCleanup(setattr, rt, "_generated_paths", original)
        payload = {"range": {"base": "b" * 40}, "out_of_plan": ["api/client.ts", "gen/a.rbi"],
                   "changed_paths": ["api/client.ts", "gen/a.rbi"], "planned_spans": []}
        result, read, _ = rt.narrow("/unused", payload, "h" * 40, "/unused")
        self.assertEqual(read, "ok")
        self.assertEqual(result, {"remaining": ["api/client.ts"], "exempt": [{"path": "gen/a.rbi", "rule": "generated"}]})

    def test_a_gitattributes_file_is_never_exempt(self):
        paths = [".gitattributes", "vendor/.gitattributes"]
        out = rt.narrow_out_of_plan(paths, paths, [], set(paths))
        self.assertEqual(out, {"remaining": paths, "exempt": []})
        # Nor by the basename rule: a plan naming `.gitattributes` bare does not exempt a nested one.
        out = rt.narrow_out_of_plan(["vendor/.gitattributes"], ["vendor/.gitattributes"], [".gitattributes"], None)
        self.assertEqual(out, {"remaining": ["vendor/.gitattributes"], "exempt": []})

    def test_a_failed_generated_read_exempts_nothing_by_that_rule(self):
        out = rt.narrow_out_of_plan(["gen/a.rbi", "x/guard.rb"], ["gen/a.rbi", "x/guard.rb"], ["guard.rb"], None)
        self.assertEqual(out, {"remaining": ["gen/a.rbi"], "exempt": [{"path": "x/guard.rb", "rule": "basename"}]})

    def test_a_path_both_rules_exempt_is_reported_as_basename(self):
        out = rt.narrow_out_of_plan(["gen/a.rbi"], ["gen/a.rbi"], ["a.rbi"], {"gen/a.rbi"})
        self.assertEqual(out["exempt"], [{"path": "gen/a.rbi", "rule": "basename"}])


class NarrowCliTests(unittest.TestCase):
    """`_generated_paths`, `narrow` and `run_narrow` against a real sandbox repo, and `run_flags`' exemptions."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.base = self._commit({".gitattributes": "gen/*.rbi linguist-generated\napi/off.ts -linguist-generated\n",
                                  "gen/a.rbi": "a\n", "api/client.ts": "c\n", "api/off.ts": "o\n",
                                  "app/guard.rb": "g\n"})
        # The head adds an attribute for api/client.ts: it is generated at the head only.
        self.head = self._commit({".gitattributes": "gen/*.rbi linguist-generated\napi/off.ts -linguist-generated\n"
                                                    "api/client.ts linguist-generated=true\n",
                                  "gen/a.rbi": "a2\n", "api/client.ts": "c2\n", "api/off.ts": "o2\n",
                                  "app/guard.rb": "g2\n"})
        self.oop = [".gitattributes", "api/client.ts", "api/off.ts", "app/guard.rb", "gen/a.rbi"]

    def _git(self, *args):
        return gitsandbox._run_git(list(args), cwd=self.clone.path)

    def _commit(self, files):
        for name, text in files.items():
            target = self.clone.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            self._git("add", name)
        self._git("commit", "-q", "-m", "c")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _payload(self, base=None, spans=None):
        return {"range": {"base": base or self.base}, "changed_paths": list(self.oop), "out_of_plan": list(self.oop),
                "planned_spans": spans if spans is not None else ["guard.rb"], "sensitive": {"matches": []}}

    def test_generated_paths_reads_each_side_from_its_commit(self):
        at_base = rt._generated_paths(self.clone.path, self.base, self.oop, str(self.scratch))
        at_head = rt._generated_paths(self.clone.path, self.head, self.oop, str(self.scratch))
        self.assertEqual(at_base, {"gen/a.rbi"})
        self.assertEqual(at_head, {"gen/a.rbi", "api/client.ts"})
        self.assertEqual(sorted(p.name for p in self.scratch.iterdir()), [])  # the temp indexes are removed

    def test_user_attribute_files_are_not_read(self):
        attrs = self.scratch / "global-attributes"
        attrs.write_text("* linguist-generated\n", encoding="utf-8")
        config = self.scratch / "global-config"
        config.write_text("[core]\n\tattributesFile = %s\n" % attrs, encoding="utf-8")
        old = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = str(config)
        self.addCleanup(lambda: os.environ.pop("GIT_CONFIG_GLOBAL") if old is None
                        else os.environ.__setitem__("GIT_CONFIG_GLOBAL", old))
        # Positive control: git itself reads the global attributes file.
        self.assertIn("linguist-generated: set", self._git("check-attr", "linguist-generated", "app/guard.rb").stdout)
        self.assertEqual(rt._generated_paths(self.clone.path, self.base, self.oop, str(self.scratch)), {"gen/a.rbi"})

    def test_a_dirty_attribute_file_changes_nothing(self):
        (self.clone.path / ".gitattributes").write_text("* linguist-generated\n", encoding="utf-8")
        self.assertEqual(rt._generated_paths(self.clone.path, self.base, self.oop, str(self.scratch)), {"gen/a.rbi"})
        result, read, notices = rt.narrow(self.clone.path, self._payload(), self.head, str(self.scratch))
        self.assertEqual((read, notices), ("ok", []))
        self.assertEqual(result["exempt"], [{"path": "app/guard.rb", "rule": "basename"},
                                            {"path": "gen/a.rbi", "rule": "generated"}])
        self.assertEqual(result["remaining"], [".gitattributes", "api/client.ts", "api/off.ts"])
        # The checkout's own index is untouched by the attribute reads.
        self.assertEqual(self._git("diff", "--cached", "--name-only").stdout, "")

    def test_an_unreadable_side_exempts_nothing_as_generated(self):
        result, read, notices = rt.narrow(self.clone.path, self._payload(base="f" * 40), self.head, str(self.scratch))
        self.assertEqual(read, "failed")
        self.assertTrue(notices[0].startswith(rt.OUT_OF_PLAN_GENERATED_UNREAD))
        self.assertEqual(result["exempt"], [{"path": "app/guard.rb", "rule": "basename"}])

    def test_an_unlistable_root_exempts_nothing_by_basename(self):
        result, read, notices = rt.narrow(self.clone.path, self._payload(), "e" * 40, str(self.scratch))
        self.assertTrue(any(n.startswith(rt.OUT_OF_PLAN_ROOT_UNREAD) for n in notices))
        self.assertEqual(result["exempt"], [])
        self.assertEqual(read, "failed")  # the head is unreadable for the attribute read too

    def _narrow_cli(self, trusted, body):
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"status": "ok", "payload": self._payload()}), encoding="utf-8")
        pr_body = self.scratch / "body.md"
        pr_body.write_text(body, encoding="utf-8")
        out_dir = self.scratch / ("s-%s" % trusted)
        return rt.run_narrow(self.clone.path, str(assess), self.head, str(pr_body), trusted, str(out_dir))

    def test_a_root_file_span_exempts_no_same_named_file_elsewhere(self):
        self._commit({"README.md": "root\n"})  # at the head; the PR leaves it otherwise unchanged
        head = self._git("rev-parse", "HEAD").stdout.strip()
        payload = dict(self._payload(spans=["README.md", "guard.rb"]),
                       out_of_plan=["docs/README.md", "app/guard.rb"], changed_paths=["docs/README.md", "app/guard.rb"])
        result, _, notices = rt.narrow(self.clone.path, payload, head, str(self.scratch))
        self.assertEqual(notices, [])
        self.assertEqual(result, {"remaining": ["docs/README.md"], "exempt": [{"path": "app/guard.rb", "rule": "basename"}]})

    def test_reasons_reach_the_file_list_only_when_trusted(self):
        body = "## Out-of-plan changes\n- `api/client.ts` — regenerated the client\n"
        out, _ = self._narrow_cli(False, body)
        self.assertEqual(Path(out["files_path"]).read_text(encoding="utf-8"),
                         ".gitattributes\napi/client.ts\napi/off.ts\n")
        out, _ = self._narrow_cli(True, body)
        self.assertEqual(Path(out["files_path"]).read_text(encoding="utf-8"),
                         ".gitattributes\napi/client.ts — regenerated the client\napi/off.ts\n")
        self.assertEqual((out["generated_read"], len(out["exempt"])), ("ok", 2))

    def test_nothing_remaining_writes_no_file_list(self):
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"payload": dict(self._payload(), out_of_plan=["gen/a.rbi"])}), encoding="utf-8")
        body = self.scratch / "body.md"
        body.write_text("", encoding="utf-8")
        out, _ = rt.run_narrow(self.clone.path, str(assess), self.head, str(body), True, str(self.scratch))
        self.assertEqual((out["remaining"], out["files_path"]), ([], None))
        self.assertFalse((self.scratch / rt.OUT_OF_PLAN_FILES).exists())

    def test_an_unreadable_pr_body_is_refused_whether_or_not_reasons_are_trusted(self):
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"payload": self._payload()}), encoding="utf-8")
        for trusted in (True, False):
            with self.assertRaises(SystemExit) as raised:
                rt.run_narrow(self.clone.path, str(assess), self.head, "/nonexistent", trusted, str(self.scratch))
            self.assertEqual(raised.exception.code, 2)

    def test_a_relative_scratch_leaves_no_index_behind(self):
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.scratch)
        self.assertEqual(rt._generated_paths(self.clone.path, self.base, self.oop, "rel"), {"gen/a.rbi"})
        self.assertEqual(list((self.scratch / "rel").iterdir()), [])
        self.assertEqual(self._git("status", "--porcelain", "--ignored").stdout, "")

    def test_flags_posts_no_out_of_plan_thread_for_an_exempt_file(self):
        body = self.scratch / "body.md"
        body.write_text("", encoding="utf-8")
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"status": "ok", "payload": self._payload()}), encoding="utf-8")
        threads = self.scratch / "threads.json"
        threads.write_text("[]", encoding="utf-8")
        out, notices = rt.run_flags(self.clone.path, str(body), str(assess), str(threads), "main", self.head,
                                    str(self.scratch / "s"))
        self.assertEqual(notices, [])
        self.assertEqual([t["flags"] for t in out["new"]],
                         [["out-of-plan:.gitattributes"], ["out-of-plan:api/client.ts"], ["out-of-plan:api/off.ts"]])
        self.assertEqual(out["exempt"], [{"path": "app/guard.rb", "rule": "basename"},
                                         {"path": "gen/a.rbi", "rule": "generated"}])
        self.assertEqual(out["generated_read"], "ok")


if __name__ == "__main__":
    unittest.main()
