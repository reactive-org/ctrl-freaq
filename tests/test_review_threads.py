"""Tests for `scripts/review_threads.py` (#145): the stamp grammar, the pure state derivation and range
mapping, the git hunk check against a real sandbox repo, and `read_threads` through the `gh` shim.

The trust properties are the security half of this module, so each has its own test: a marked comment
by an untrusted author neither resets a state nor moves a thread's reference commit, and a resolution
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
        self.head = gitsandbox._run_git(["rev-parse", "HEAD"], cwd=self.clone.path).stdout.strip()
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
        self.assertEqual((by_id["T2"]["state"], by_id["T3"]["state"]), ("operator-thread", "operator-thread"))
        # O<n> by root createdAt among human-rooted threads; never on a pipeline thread.
        self.assertEqual((by_id["T3"]["o"], by_id["T2"]["o"], by_id["T1"]["o"]), (1, 2, None))
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
        gitsandbox._run_git(["commit", "-q", "-m", "c"], cwd=self.clone.path)
        return gitsandbox._run_git(["rev-parse", "HEAD"], cwd=self.clone.path).stdout.strip()

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


if __name__ == "__main__":
    unittest.main()
