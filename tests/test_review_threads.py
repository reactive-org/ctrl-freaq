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
from unittest import mock

import review_threads as rt
from tests.support import gitsandbox, shimenv

HEAD = "a" * 40
OTHER = "b" * 40


def _c(marked, trusted, decision=None):
    return {"marked": marked, "trusted": trusted, "decision": decision}


def _never():
    raise AssertionError("the hunk check must not run on a changes-requested or operator thread (#169)")


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
             "flags": ["sensitive:scripts/x.py", "security:SEC-2"], "supersedes": "F1", "group": None,
             "advisory": False, "phase": None},
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
        self.assertEqual(rt.parse_stamp(rt.stamp_reply(HEAD, "fixed", f=2)),
                         {"kind": "reply", "f": 2, "head": HEAD, "decision": None})
        self.assertEqual(rt.parse_stamp(rt.stamp_reply(HEAD, "fixed")),
                         {"kind": "reply", "f": None, "head": HEAD, "decision": None})

    def test_a_decision_reply_round_trips_and_no_decision_is_todays_stamp(self):
        for decision in rt.DECISIONS:
            for f in (3, None):
                self.assertEqual(rt.parse_stamp(rt.stamp_reply(HEAD, "x", f=f, decision=decision)),
                                 {"kind": "reply", "f": f, "head": HEAD, "decision": decision})
        self.assertEqual(rt.stamp_reply(HEAD, "fixed", f=2),
                         "%s\n<!-- reply F2 · head %s -->\n\nfixed" % (rt.MARKER, HEAD))
        self.assertEqual(rt.stamp_reply(HEAD, "fixed", f=2, decision="accept"),
                         "%s\n<!-- reply F2 · head %s · decision accept -->\n\nfixed" % (rt.MARKER, HEAD))
        for bad in ("approve", "", "Accept", 1):
            with self.assertRaises(ValueError):
                rt.stamp_reply(HEAD, "x", decision=bad)
        # A decision outside the closed set is not a stamp the grammar reads.
        self.assertIsNone(rt.parse_stamp("%s\n<!-- reply · head %s · decision approve -->\n\nx" % (rt.MARKER, HEAD)))

    def test_decision_text_is_the_text_under_the_stamp(self):
        self.assertEqual(rt.decision_text(rt.stamp_reply(HEAD, "changes: tighten the guard\nplease",
                                                         decision="changes")),
                         "changes: tighten the guard\nplease")
        self.assertIsNone(rt.decision_text("changes: an unmarked comment"))
        self.assertIsNone(rt.decision_text(rt.stamp_reply(HEAD, "  ")))

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
        self.assertEqual(rt.parse_stamp(rt.stamp_review(HEAD, "two threads")), {"kind": "review", "head": HEAD, "phase": None})

    def test_a_pause_phase_round_trips_on_root_and_review_stamps(self):
        # #170: ` · phase <N>` is stamped last, after any `supersedes`, and read back as an int.
        text = rt.stamp_root(3, HEAD, "x.py", "line", 4, 9, ["plan:x.py"], "b", supersedes="F1", phase=2)
        self.assertIn(" · supersedes F1 · phase 2 -->", text.split("\n")[1])
        self.assertEqual((rt.parse_stamp(text)["phase"], rt.parse_stamp(text)["supersedes"]), (2, "F1"))
        file_root = rt.stamp_root(4, HEAD, "x.py", "file", None, None, [], "b", phase=7)
        self.assertEqual(rt.parse_stamp(file_root)["phase"], 7)
        review = rt.stamp_review(HEAD, "pause", phase=2)
        self.assertEqual(review.split("\n")[1], "<!-- review · head %s · phase 2 -->" % HEAD)
        self.assertEqual(rt.parse_stamp(review), {"kind": "review", "head": HEAD, "phase": 2})

    def test_no_phase_is_todays_stamp_byte_for_byte(self):
        self.assertEqual(rt.stamp_review(HEAD, "x"), "%s\n<!-- review · head %s -->\n\nx" % (rt.MARKER, HEAD))
        self.assertEqual(rt.stamp_root(1, HEAD, "a.py", "line", 1, 2, ["plan:a.py"], "x", supersedes="F2"),
                         "%s\n<!-- F1 · head %s · anchor a.py:1-2 · flags plan:a.py · supersedes F2 -->\n**F1**\n\nx"
                         % (rt.MARKER, HEAD))

    def test_a_phase_the_grammar_cannot_carry_is_refused(self):
        for phase in (0, -1, True, "2", 1.0):
            with self.assertRaises(ValueError):
                rt.stamp_review(HEAD, "x", phase=phase)
            with self.assertRaises(ValueError):
                rt.stamp_root(1, HEAD, "a.py", "file", None, None, [], "x", phase=phase)
        self.assertIsNone(rt.parse_stamp("%s\n<!-- review · head %s · phase 0 -->" % (rt.MARKER, HEAD)))

    def test_group_and_advisory_round_trip_between_supersedes_and_phase(self):
        # #181: ` · group sensitive` then ` · advisory`, after `supersedes` and before `phase`.
        group = rt.stamp_root(4, HEAD, "config/a.yml", "file", None, None,
                              ["sensitive:config/a.yml", "sensitive:config/b.yml"], "b", supersedes="F2",
                              group="sensitive", phase=3)
        self.assertEqual(group.split("\n")[1],
                         "<!-- F4 · head %s · anchor config/a.yml:file · flags sensitive:config/a.yml,"
                         "sensitive:config/b.yml · supersedes F2 · group sensitive · phase 3 -->" % HEAD)
        stamp = rt.parse_stamp(group)
        self.assertEqual((stamp["group"], stamp["advisory"], stamp["supersedes"], stamp["phase"]),
                         ("sensitive", False, "F2", 3))
        batch = rt.stamp_root(5, HEAD, "a_test.py", "file", None, None, ["routine:a_test.py"], "b", advisory=True)
        self.assertTrue(batch.split("\n")[1].endswith(" · flags routine:a_test.py · advisory -->"))
        self.assertEqual((rt.parse_stamp(batch)["group"], rt.parse_stamp(batch)["advisory"]), (None, True))

    def test_no_group_or_advisory_is_todays_stamp_byte_for_byte(self):
        plain = rt.stamp_root(1, HEAD, "a.py", "file", None, None, ["routine:a.py"], "x", supersedes="F2", phase=1)
        self.assertEqual(rt.stamp_root(1, HEAD, "a.py", "file", None, None, ["routine:a.py"], "x", supersedes="F2",
                                       phase=1, group=None, advisory=False), plain)
        self.assertEqual(plain, "%s\n<!-- F1 · head %s · anchor a.py:file · flags routine:a.py · supersedes F2 · "
                                "phase 1 -->\n**F1**\n\nx" % (rt.MARKER, HEAD))
        self.assertEqual((rt.parse_stamp(plain)["group"], rt.parse_stamp(plain)["advisory"]), (None, False))

    def test_a_group_or_advisory_the_key_set_cannot_carry_is_refused(self):
        # Advisory only on a batch, a sensitive group only over `sensitive:` keys, nothing outside the closed set.
        for flags, kwargs in ((["sensitive:a.py"], dict(advisory=True)),
                              (["routine:a.py", "out-of-plan:b.py"], dict(advisory=True)),
                              (["out-of-plan:a.py"], dict(group="sensitive")),
                              (["sensitive:a.py", "judgment:seam:a.py"], dict(group="sensitive")),
                              ([], dict(group="sensitive")), (["routine:a.py"], dict(advisory=[])),
                              (["sensitive:a.py"], dict(group="routine")), (["sensitive:a.py"], dict(advisory=1))):
            with self.assertRaises(ValueError, msg=(flags, kwargs)):
                rt.stamp_root(1, HEAD, "a.py", "file", None, None, flags, "x", **kwargs)

    def test_unmarked_or_unreadable_stamp_is_none(self):
        self.assertIsNone(rt.parse_stamp("plain comment"))
        self.assertIsNone(rt.parse_stamp(rt.MARKER + "\nno stamp here"))
        self.assertIsNone(rt.parse_stamp(rt.MARKER))

    def test_reason_line(self):
        self.assertEqual(rt.reason_line("  not  exploitable —\n behind auth  "), "not exploitable - behind auth")
        self.assertIsNone(rt.reason_line(None))


class DeriveStateTests(unittest.TestCase):
    def test_unreviewed_pipeline_root(self):
        self.assertEqual(rt.derive_state([_c(True, True)], "pipeline", False, lambda: False), ("unreviewed", False))

    def test_an_unresolved_pipeline_thread_whose_hunk_moved_is_superseded(self):
        # #169: a thread posted mid-flight cannot go stale at its old anchor.
        self.assertEqual(rt.derive_state([_c(True, True)], "pipeline", False, lambda: True), ("superseded", False))
        self.assertEqual(rt.derive_state([_c(True, True), _c(False, True), _c(True, True)], "pipeline", False,
                                         lambda: True), ("superseded", False))

    def test_trusted_human_reply_requests_changes(self):
        state = rt.derive_state([_c(True, True), _c(False, True)], "pipeline", False, _never)
        self.assertEqual(state, ("changes-requested", False))

    def test_a_marked_reply_makes_it_unreviewed_again(self):
        state = rt.derive_state([_c(True, True), _c(False, True), _c(True, True)], "pipeline", False, lambda: False)
        self.assertEqual(state, ("unreviewed", False))

    def test_an_untrusted_marked_reply_does_not_reset_changes_requested(self):
        # The forged-marker property: only a trusted author's marked comment is the pipeline's.
        state = rt.derive_state([_c(True, True), _c(False, True), _c(True, False)], "pipeline", False, _never)
        self.assertEqual(state, ("changes-requested", False))

    def test_an_untrusted_unmarked_reply_requests_nothing(self):
        self.assertEqual(rt.derive_state([_c(True, True), _c(False, False)], "pipeline", False, lambda: False),
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

    def test_a_standing_accept_or_dismiss_is_accepted_until_its_hunk_moves(self):
        for decision in ("accept", "dismiss"):
            for origin in ("pipeline", "operator"):
                comments = [_c(True, True), _c(True, True, decision)]
                self.assertEqual(rt.derive_state(comments, origin, False, lambda: False), ("accepted", False))
                self.assertEqual(rt.derive_state(comments, origin, False, lambda: True), ("superseded", False))

    def test_a_standing_changes_decision_requests_changes_on_either_origin(self):
        for origin in ("pipeline", "operator"):
            self.assertEqual(rt.derive_state([_c(False, True), _c(True, True, "changes")], origin, False, _never),
                             ("changes-requested", False))

    def test_a_later_trusted_comment_ends_a_decision(self):
        accepted = [_c(True, True), _c(True, True, "accept")]
        # A person's later comment is their newer request.
        self.assertEqual(rt.derive_state(accepted + [_c(False, True)], "pipeline", False, _never),
                         ("changes-requested", False))
        # A later plain marked reply is the pipeline's, so the thread is back under review.
        self.assertEqual(rt.derive_state(accepted + [_c(True, True)], "pipeline", False, lambda: False),
                         ("unreviewed", False))
        # An untrusted comment ends nothing.
        self.assertEqual(rt.derive_state(accepted + [_c(False, False)], "pipeline", False, lambda: False),
                         ("accepted", False))

    def test_a_trusted_resolution_outranks_a_later_changes_decision(self):
        self.assertEqual(rt.derive_state([_c(True, True), _c(True, True, "changes")], "pipeline", True,
                                         lambda: False), ("accepted", False))

    def test_changes_requested_and_operator_threads_never_run_the_hunk_check(self):
        # The resolver's fix commit lands before its fix reply: a request it is about to answer must not
        # go superseded in that window (the `_never` check fails the test if the hunk is consulted).
        self.assertEqual(rt.derive_state([_c(True, True), _c(False, True)], "pipeline", False, _never),
                         ("changes-requested", False))
        self.assertEqual(rt.derive_state([_c(False, True), _c(True, True)], "operator", False, _never),
                         ("operator-thread", True))

    def test_standing_decision_and_annotation(self):
        viewer_reply = {"author": "me", "trusted": True, "marked": True,
                        "stamp": {"kind": "reply", "decision": "accept"}}
        others = [
            dict(viewer_reply, author="writer"),  # a resolution-grade account's decision stamp
            dict(viewer_reply, trusted=False),  # an untrusted copy
            dict(viewer_reply, marked=False, stamp=None),  # demoted
            dict(viewer_reply, stamp={"kind": "root", "flags": []}),
        ]
        self.assertEqual([c["decision"] for c in rt.annotate_decisions([viewer_reply] + others, "me")],
                         ["accept", None, None, None, None])
        self.assertEqual([c["decision"] for c in rt.annotate_decisions([viewer_reply], None)], [None])
        self.assertIsNone(rt.standing_decision([]))
        self.assertEqual(rt.standing_decision([_c(True, True, "dismiss"), _c(False, False)]), "dismiss")
        self.assertIsNone(rt.standing_decision([_c(True, True, "dismiss"), _c(False, True)]))

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

    def test_pause_threads_gate_like_evaluator_threads_and_report_their_phase(self):
        # DoD 3 (#170): a phase-stamped root reads by the same states; `phase` rides on the thread and on the
        # marked review entry, so a reader selects one pause's threads and body by it.
        def root(f):
            return _comment(rt.stamp_root(f, self.head, "src/a.txt", "line", 5, 8, ["sensitive:src/a.txt"], "why",
                                          phase=2), url="https://x/p%d" % f)
        page = _page([
            _thread("R", [root(1)], resolved=True, resolved_by="me"),
            _thread("C", [root(2), _comment("not this way", created="2026-10-04T00:00:00Z")]),
            _thread("U", [root(3)]),
            _thread("E", [self._root(4, url="https://x/e4")]),
        ], reviews=[dict(_comment(rt.stamp_review(self.head, "Review pause — phase 2", phase=2)), state="COMMENTED"),
                    dict(_comment(rt.stamp_review(self.head, "evaluator")), state="COMMENTED")])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, notices = self._read()
        self.assertEqual(notices, [])
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertEqual([by_id[k]["state"] for k in "RCU"], ["accepted", "changes-requested", "unreviewed"])
        self.assertEqual([by_id[k]["phase"] for k in "RCUE"], [2, 2, 2, None])
        conversation = json.loads(Path(payload["conversation_path"]).read_text(encoding="utf-8"))
        self.assertEqual([(c["marked"], c["phase"]) for c in conversation], [(True, 2), (True, None)])

    def test_a_pause_thread_whose_code_a_later_phase_moved_is_superseded(self):
        # DoD 2 (#170) end to end: rule 7 reads a phase-stamped unreviewed root like any pipeline root, and a
        # changes-requested one keeps gating until the fix reply re-bases it.
        posted = self.head
        def root(f):
            return _comment(rt.stamp_root(f, posted, "src/a.txt", "line", 5, 8, ["sensitive:src/a.txt"], "why",
                                          phase=1), url="https://x/m%d" % f)
        self.head = self._commit("".join(("MOVED %d\n" if i == 6 else "line %d\n") % i for i in range(1, 21)))
        page = _page([_thread("U", [root(1)]),
                      _thread("C", [root(2), _comment("not this way", created="2026-10-04T00:00:00Z")])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertEqual((by_id["U"]["state"], by_id["U"]["phase"]), ("superseded", 1))
        self.assertEqual(by_id["C"]["state"], "changes-requested")

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

    def _decision(self, decision, text, head=None, login="me", association="OWNER", created="2026-10-06T00:00:00Z"):
        return _comment(rt.stamp_reply(head or self.head, "%s: %s" % (decision, text), f=1, decision=decision),
                        login=login, association=association, created=created)

    def _move_hunk(self):
        """A new judged head whose line 6 changed: inside every `_root`'s 5-8 anchor."""
        self.head = self._commit("".join("line %d%s\n" % (i, " moved" if i == 6 else "") for i in range(1, 21)))

    def test_the_viewers_accept_decision_accepts_until_its_hunk_moves(self):
        page = _page([_thread("T", [self._root(1), self._decision("accept", "looks right")])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        thread = payload["threads"][0]
        self.assertEqual((thread["state"], thread["reply_reason"], thread["dismiss_reason"]),
                         ("accepted", None, None))
        base = self.head
        self.entries = []
        self._move_hunk()
        page = _page([_thread("T", [self._root(1, head=base), self._decision("accept", "looks right", head=base)])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        self.assertEqual((payload["threads"][0]["state"], payload["threads"][0]["reference_sha"]), ("superseded", base))

    def test_the_viewers_changes_decision_carries_its_notes_as_the_reply(self):
        page = _page([_thread("T", [self._root(1), self._decision("changes", "guard  the\nempty case")])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        thread = payload["threads"][0]
        self.assertEqual((thread["state"], thread["reply_reason"]), ("changes-requested", "guard the empty case"))

    def test_another_accounts_decision_stamp_is_a_plain_marked_reply(self):
        # A resolution-grade teammate's stamp may mark (it moves the reference), but carries no decision.
        page = _page([_thread("T", [self._root(1), self._decision("accept", "fine", login="writer",
                                                                    association="MEMBER")])])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "writer"), text="write\n")
        payload, _ = self._read()
        self.assertEqual(payload["threads"][0]["state"], "unreviewed")

    def test_an_untrusted_decision_stamp_is_ignored(self):
        page = _page([_thread("T", [self._root(1), self._decision("accept", "fine", login="rando",
                                                                    association="NONE")])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        self.assertEqual(payload["threads"][0]["state"], "unreviewed")
        self.assertEqual(payload["untrusted"]["comments"], 1)

    def test_a_fix_reply_after_a_changes_decision_rebases_the_thread(self):
        base = self.head
        self._move_hunk()
        fix = _comment(rt.stamp_reply(self.head, "fixed in this commit", f=1), created="2026-10-07T00:00:00Z")
        page = _page([_thread("T", [self._root(1, head=base), self._decision("changes", "fix it", head=base), fix])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        thread = payload["threads"][0]
        # The fix reply's head is the reference commit: the hunk is unchanged since, so nothing is re-posted.
        self.assertEqual((thread["state"], thread["reference_sha"]), ("unreviewed", self.head))
        self.assertIsNone(thread["reply_reason"])

    def test_a_changes_requested_thread_whose_hunk_moved_keeps_its_state(self):
        # The resolver's fix commit lands before its fix reply: in that window the request still gates as
        # `changes-requested`, never `superseded` (a successor would carry keys, never the person's words).
        base = self.head
        self._move_hunk()
        human = _comment("guard the empty case", created="2026-10-05T00:00:00Z")
        page = _page([_thread("H", [self._root(1, head=base), human]),
                      _thread("D", [self._root(2, head=base), self._decision("changes", "fix it", head=base)])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        self.assertEqual({t["id"]: t["state"] for t in payload["threads"]},
                         {"H": "changes-requested", "D": "changes-requested"})

    def test_the_viewers_dismiss_decision_dismisses_the_finding_on_its_own_thread(self):
        root = _comment(rt.stamp_root(1, self.head, "src/a.txt", "line", 5, 8, ["security:SEC-1"],
                                      "Claim: token logged\nwhy"), url="https://x/f1")
        page = _page([_thread("T", [root, self._decision("dismiss", "admin  only")])])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        thread = payload["threads"][0]
        self.assertEqual((thread["state"], thread["dismiss_reason"], thread["reply_reason"]),
                         ("accepted", "admin only", None))
        security = {"entries": [{"id": "SEC-1", "disposition": "dismissed", "thread_url": "https://x/f1",
                                 "claim": "token logged", "anchor": "src/a.txt:5", "reason": "admin only"}]}
        confirmed = rt.confirm_dismissals(security, payload)
        self.assertEqual(confirmed["entries"][0]["thread"], {"url": "https://x/f1", "confirmed": True})

    def test_a_moved_unreviewed_or_decision_accepted_head_gets_exactly_one_successor(self):
        base = self.head
        self._move_hunk()
        page = _page([
            _thread("U", [self._root(1, head=base, url="https://x/u")]),
            _thread("A", [_comment(rt.stamp_root(2, base, "src/a.txt", "line", 5, 8, ["judgment:data:src/a.txt"],
                                                 "why"), url="https://x/a"),
                          _comment(rt.stamp_reply(base, "accept: ok", f=2, decision="accept"),
                                   created="2026-10-06T00:00:00Z")]),
        ])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        self.assertEqual({t["id"]: t["state"] for t in payload["threads"]}, {"U": "superseded", "A": "superseded"})
        threads = json.loads(Path(payload["threads_path"]).read_text(encoding="utf-8"))
        files = {"src/a.txt": {"hunks": [(5, 4)]}}
        signals = {"sensitive": ["src/a.txt"], "out_of_plan": [], "judgment": [("data", "src/a.txt", [5, 8])],
                   "security": [], "plan": [], "reasons": {}, "rejected": []}
        out = rt.build_flags(files, threads, signals, lambda sha, path, rng: list(rng), payload["next_f"])
        successors = sorted((n["supersedes"], n["f"]) for n in out["new"] if n["supersedes"])
        self.assertEqual(successors, [("F1", 3), ("F2", 4)])
        self.assertEqual(len(out["new"]), 2)

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
        # A named superseded thread is replaced; a thread no successor names never is.
        self.assertEqual((by_id["S"]["replaced"], by_id["N"]["replaced"]), (True, False))

    def test_replaced_holds_only_while_no_trusted_human_comment_postdates_the_successor(self):
        def batch(f, created="2026-10-01T00:00:00Z", supersedes=None):
            return _comment(rt.stamp_root(f, self.head, "src/a.txt", "file", None, None,
                                          ["routine:src/a.txt", "routine:src/b.txt"], "batch", supersedes=supersedes),
                            created=created, url="https://x/f%d" % f)

        def successor(f, of, created):
            return _thread("S%d" % f, [batch(f, created=created, supersedes="F%d" % of)])

        page = _page([
            # F1: a pure-split reply, then the re-cut F2. Every human comment predates the successor.
            _thread("B1", [batch(1), _comment("split src/b.txt", created="2026-10-02T00:00:00Z")]),
            successor(2, 1, "2026-10-03T00:00:00Z"),
            # F3: unreviewed, named by F4 — replaced.
            _thread("B3", [batch(3)]), successor(4, 3, "2026-10-03T00:00:00Z"),
            # F5: a person's newer request after its re-cut F6 still gates.
            _thread("B5", [batch(5), _comment("and fix the fixture", created="2026-10-09T00:00:00Z")]),
            successor(6, 5, "2026-10-04T00:00:00Z"),
            # F7: an untrusted comment after the successor F8 changes nothing.
            _thread("B7", [batch(7), _comment("drive-by", association="NONE", login="rando",
                                              created="2026-10-09T00:00:00Z")]),
            successor(8, 7, "2026-10-04T00:00:00Z"),
            # F9: a named non-batch thread a person commented on is not replaced, whatever the order.
            _thread("P9", [self._root(9, url="https://x/p9"), _comment("fix it", created="2026-10-02T00:00:00Z")]),
            _thread("S10", [_comment(rt.stamp_root(10, self.head, "src/a.txt", "line", 5, 8, ["sensitive:src/a.txt"],
                                                   "y", supersedes="F9"), created="2026-10-05T00:00:00Z")]),
        ])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        by_id = {t["id"]: t for t in payload["threads"]}
        states = {tid: (by_id[tid]["state"], by_id[tid]["replaced"]) for tid in ("B1", "B3", "B5", "B7", "P9")}
        self.assertEqual(states, {"B1": ("changes-requested", True), "B3": ("unreviewed", True),
                                  "B5": ("changes-requested", False), "B7": ("unreviewed", True),
                                  "P9": ("changes-requested", False)})
        self.assertTrue(all(by_id[tid]["superseded_by"] for tid in states))
        self.assertFalse(any(by_id[tid]["replaced"] for tid in ("S2", "S4", "S6", "S8", "S10")))

    def test_a_viewers_changes_decision_after_a_re_cut_keeps_the_batch_gating(self):
        # #169: a successor carries keys, never words — the operator's `changes` decision is a person's word.
        def batch(f, created="2026-10-01T00:00:00Z", supersedes=None):
            return _comment(rt.stamp_root(f, self.head, "src/a.txt", "file", None, None,
                                          ["routine:src/a.txt", "routine:src/b.txt"], "batch", supersedes=supersedes),
                            created=created, url="https://x/f%d" % f)

        def successor(f, of, created):
            return _thread("S%d" % f, [batch(f, created=created, supersedes="F%d" % of)])

        def changes(f, created, login="me", association="OWNER"):
            return _comment(rt.stamp_reply(self.head, "changes: fix b", f=f, decision="changes"), login=login,
                            association=association, created=created)

        page = _page([
            # F11: the viewer's decision after its re-cut F12 still gates.
            _thread("B11", [batch(11), changes(11, "2026-10-09T00:00:00Z")]), successor(12, 11, "2026-10-04T00:00:00Z"),
            # F13: a teammate's decision stamp is a plain marked reply, so the batch stays replaced.
            _thread("B13", [batch(13), changes(13, "2026-10-09T00:00:00Z", login="writer", association="MEMBER")]),
            successor(14, 13, "2026-10-04T00:00:00Z"),
            # F15: a decision older than the re-cut F16 is what the successor already answered.
            _thread("B15", [batch(15), changes(15, "2026-10-02T00:00:00Z")]), successor(16, 15, "2026-10-04T00:00:00Z"),
        ])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "writer"), text="write\n")
        payload, _ = self._read()
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertEqual({tid: (by_id[tid]["state"], by_id[tid]["replaced"]) for tid in ("B11", "B13", "B15")},
                         {"B11": ("changes-requested", False), "B13": ("unreviewed", True),
                          "B15": ("changes-requested", True)})
        stored = {t["id"]: t["comments"] for t in json.loads(Path(payload["threads_path"]).read_text(encoding="utf-8"))}
        self.assertEqual([c["decision"] for c in stored["B11"]], [None, "changes"])
        self.assertEqual([c["decision"] for c in stored["B13"]], [None, None])

    def test_with_several_successors_the_earliest_root_decides_replaced(self):
        def batch(f, created, supersedes=None):
            return _comment(rt.stamp_root(f, self.head, "src/a.txt", "file", None, None, ["routine:src/a.txt"],
                                          "batch", supersedes=supersedes), created=created, url="https://x/f%d" % f)

        # F1's human comment (10-05) postdates F2's root (10-03) but not F3's (10-07): the earliest root decides,
        # whichever successor is listed first, so the person's request still gates.
        older, later = (_thread("S2", [batch(2, "2026-10-03T00:00:00Z", supersedes="F1")]),
                        _thread("S3", [batch(3, "2026-10-07T00:00:00Z", supersedes="F1")]))
        for successors in ((older, later), (later, older)):
            self.entries = []
            self._answer(rt.query_argv("o/r", 5), _page([
                _thread("B1", [batch(1, "2026-10-01T00:00:00Z"),
                               _comment("and fix the fixture", created="2026-10-05T00:00:00Z")]),
            ] + list(successors)))
            payload, _ = self._read()
            b1 = {t["id"]: t for t in payload["threads"]}["B1"]
            self.assertEqual((b1["state"], b1["replaced"]), ("changes-requested", False))

    def test_a_human_comment_with_no_time_keeps_a_named_batch_gating(self):
        root = _comment(rt.stamp_root(1, self.head, "src/a.txt", "file", None, None, ["routine:src/a.txt"], "batch"),
                        url="https://x/f1")
        successor = _comment(rt.stamp_root(2, self.head, "src/a.txt", "file", None, None, ["routine:src/a.txt"],
                                           "batch", supersedes="F1"), created="2026-10-03T00:00:00Z", url="https://x/f2")
        self._answer(rt.query_argv("o/r", 5), _page([_thread("B1", [root, _comment("split it", created=None)]),
                                                    _thread("S2", [successor])]))
        payload, _ = self._read()
        b1 = {t["id"]: t for t in payload["threads"]}["B1"]
        self.assertEqual((b1["superseded_by"], b1["replaced"]), ("F2", False))

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

    # -- #181: groups, advisory batches and riders, read back ------------------------------------------------

    def _two_files(self, b_text="b1\n"):
        (self.clone.path / "src" / "b.txt").write_text(b_text, encoding="utf-8")
        gitsandbox._run_git(["add", "src/b.txt"], cwd=self.clone.path)
        return self._commit("".join("line %d\n" % i for i in range(1, 21)))

    def _file_root(self, f, flags, head, url, **kw):
        return _comment(rt.stamp_root(f, head, "src/a.txt", "file", None, None, list(flags), "why", **kw), url=url)

    def _raw_root(self, f, tail, url, **kw):
        """A root whose stamp a script never writes: a field on a key set that cannot carry it."""
        return _comment("%s\n<!-- F%d · head %s · anchor src/a.txt:file · flags %s -->\n**F%d**\n\nwhy"
                        % (rt.MARKER, f, self.head, tail, f), url=url, **kw)

    def test_advisory_and_group_are_read_only_from_a_trusted_roots_stamp_field(self):
        # Never inferred: the field names it and the key set must carry it; an untrusted or demoted root reads neither.
        page = _page([
            _thread("ADV", [self._file_root(1, ["routine:src/a.txt"], self.head, "https://x/1", advisory=True)]),
            _thread("BAT", [self._file_root(2, ["routine:src/a.txt"], self.head, "https://x/2")]),
            _thread("RAW", [self._raw_root(3, "sensitive:src/a.txt · advisory", "https://x/3")]),
            _thread("GRP", [self._file_root(4, ["sensitive:src/a.txt"], self.head, "https://x/4", group="sensitive")]),
            _thread("OLD", [self._file_root(5, ["sensitive:src/a.txt"], self.head, "https://x/5")]),
            _thread("MIX", [self._raw_root(6, "sensitive:src/a.txt,out-of-plan:src/a.txt · group sensitive",
                                           "https://x/6")]),
            _thread("DEM", [self._file_root(7, ["routine:src/a.txt"], self.head, "https://x/7", advisory=True)
                            | {"author": {"login": "tri"}, "authorAssociation": "MEMBER"}]),
            _thread("UNT", [self._file_root(8, ["routine:src/a.txt"], self.head, "https://x/8", advisory=True)
                            | {"author": {"login": "rando"}, "authorAssociation": "NONE"}]),
        ])
        self._answer(rt.query_argv("o/r", 5), page)
        self._answer(rt.permission_argv("o/r", "tri"), text="triage\n")
        payload, _ = self._read()
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertNotIn("UNT", by_id)
        self.assertEqual({tid: (t["advisory"], t["group"]) for tid, t in by_id.items()},
                         {"ADV": (True, None), "BAT": (False, None), "RAW": (False, None),
                          "GRP": (False, "sensitive"), "OLD": (False, None), "MIX": (False, None),
                          "DEM": (False, None)})
        self.assertEqual(by_id["DEM"]["origin"], "operator")

    def test_a_group_is_accepted_for_every_member_until_any_member_changes(self):
        stamped = self._two_files()
        flags = ["sensitive:src/a.txt", "sensitive:src/b.txt"]
        resolved = _thread("R", [self._file_root(1, flags, stamped, "https://x/r", group="sensitive")],
                           resolved=True, resolved_by="me")
        decided = _thread("D", [self._file_root(2, flags, stamped, "https://x/d", group="sensitive"),
                                _comment(rt.stamp_reply(stamped, "accept: fine", f=2, decision="accept"),
                                         created="2026-10-06T00:00:00Z")])
        self.head = stamped
        self._answer(rt.query_argv("o/r", 5), _page([resolved, decided]))
        payload, _ = self._read()
        self.assertEqual([t["state"] for t in payload["threads"]], ["accepted", "accepted"])
        # A hunk in the non-anchor member supersedes the whole group, on either acceptance.
        self.head = self._two_files("b2\n")
        self.entries = []
        self._answer(rt.query_argv("o/r", 5), _page([resolved, decided]))
        payload, _ = self._read()
        self.assertEqual([t["state"] for t in payload["threads"]], ["superseded", "superseded"])

    def test_a_riders_later_change_reads_its_subjects_thread_as_changed(self):
        stamped = self._two_files()
        flags = ["out-of-plan:src/a.txt", "out-of-plan:src/b.txt"]
        accepted = _thread("A", [self._file_root(1, flags, stamped, "https://x/a")], resolved=True, resolved_by="me")
        waiting = _thread("U", [self._file_root(2, flags, stamped, "https://x/u")])
        self.head = stamped
        self._answer(rt.query_argv("o/r", 5), _page([accepted, waiting]))
        payload, _ = self._read()
        self.assertEqual([t["state"] for t in payload["threads"]], ["accepted", "unreviewed"])
        self.head = self._two_files("b2\n")
        self.entries = []
        self._answer(rt.query_argv("o/r", 5), _page([accepted, waiting]))
        payload, _ = self._read()
        self.assertEqual([t["state"] for t in payload["threads"]], ["superseded", "superseded"])

    def test_an_unreviewed_group_a_successor_re_cut_is_replaced_until_a_person_comments_after_it(self):
        def group(f, created="2026-10-01T00:00:00Z", supersedes=None, flags=("sensitive:src/a.txt",)):
            return self._file_root(f, flags, self.head, "https://x/g%d" % f, group="sensitive", supersedes=supersedes) \
                | {"createdAt": created}

        page = _page([
            _thread("G1", [group(1)]),
            _thread("S2", [group(2, "2026-10-03T00:00:00Z", "F1", ("sensitive:src/a.txt", "sensitive:src/b.txt"))]),
            _thread("G3", [group(3), _comment("why is this sensitive?", created="2026-10-09T00:00:00Z")]),
            _thread("S4", [group(4, "2026-10-03T00:00:00Z", "F3")]),
            # An old per-file sensitive head (no group field) a successor names is not replaced while unreviewed.
            _thread("P5", [self._file_root(5, ["sensitive:src/a.txt"], self.head, "https://x/p5")]),
            _thread("S6", [self._file_root(6, ["sensitive:src/a.txt"], self.head, "https://x/s6", supersedes="F5")
                           | {"createdAt": "2026-10-03T00:00:00Z"}]),
        ])
        self._answer(rt.query_argv("o/r", 5), page)
        payload, _ = self._read()
        by_id = {t["id"]: t for t in payload["threads"]}
        self.assertEqual({tid: (by_id[tid]["state"], by_id[tid]["replaced"]) for tid in ("G1", "G3", "P5")},
                         {"G1": ("unreviewed", True), "G3": ("changes-requested", False), "P5": ("unreviewed", False)})

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
             supersedes=None, group=None, advisory=False):
    """A listed pipeline thread as `threads_path` holds it, its root comment stamped at ``head``."""
    subject = subject or ("line" if rng else "file")
    body = rt.stamp_root(f, head, path, subject, rng[0] if rng else None, rng[1] if rng else None, flags, "b",
                         supersedes=supersedes, group=group, advisory=advisory)
    return {"id": "T%d" % f, "url": url or "https://github.com/o/r/pull/5#discussion_r%d" % (1000 + f),
            "f": f, "o": None, "origin": "pipeline", "state": state, "pending_reply": False, "path": path,
            "subject": subject, "range": list(rng) if rng else None, "reference_sha": head, "flags": list(flags),
            "supersedes": supersedes, "superseded_by": superseded_by, "reply_reason": None,
            "group": group, "advisory": advisory,
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

    def test_only_an_all_routine_key_set_is_a_batch(self):
        self.assertEqual(rt.batch_members(["routine:a.py", "routine:b.py"]), ["a.py", "b.py"])
        for keys in ([], None, ["routine:a.py", "out-of-plan:b.py"], ["out-of-plan:a.py"], ["security:SEC-1"]):
            self.assertEqual(rt.batch_members(keys), [], keys)
        # A mixed head is not re-cut as a batch: its routine member matches it like any per-file head.
        mixed = _pthread(3, "a_test.py", None, ["routine:a_test.py", "out-of-plan:b_test.py"])
        out = self.flags(_rsig(["a_test.py"]), threads=[mixed], next_f=4)
        self.assertEqual((out["new"], out["matched"]), ([], [{"key": "routine:a_test.py", "thread": "F3"}]))

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
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"], state="changes-requested")
        out = self.flags(_rsig(["a_test.py", "b_test.py"], splits=[(3, "b_test.py")]), threads=[batch], next_f=4)
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
        # Retired only when every member left: here b_test.py takes its own thread (reclassified consequential).
        out = self.flags(_rsig([], out_of_plan=["b_test.py"]), threads=[batch], next_f=4)
        self.assertEqual(([t["flags"] for t in out["new"]], [r["reason"] for r in out["retired"]]),
                         ([["out-of-plan:b_test.py"]], ["batch-emptied"]))

    def test_with_no_plan_a_batch_keeps_every_member_still_in_the_diff(self):
        # No plan: no out-of-plan signal and no routine one. An absent signal never empties a batch (R2.17).
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"])
        out = self.flags(_signals(), threads=[batch], next_f=4)
        self.assertEqual((out["new"], out["retired"]), ([], []))
        superseded = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py", "routine:gone.py"],
                              state="superseded")
        out = self.flags(_signals(), threads=[superseded], next_f=4)
        self.assertEqual(out["retired"], [])
        self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]],
                         [(["routine:a_test.py", "routine:b_test.py"], "F3")])
        self.assertEqual(out["new"][0]["members"], [{"path": "a_test.py", "kind": None, "reason": None},
                                                    {"path": "b_test.py", "kind": None, "reason": None}])

    def test_a_member_now_planned_or_exempt_stays_in_its_batch(self):
        # b_test.py is no longer out of plan (the plan names it, or an exemption covers it): no signal at all.
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"])
        out = self.flags(_rsig(["a_test.py"]), threads=[batch], next_f=4)
        self.assertEqual((out["new"], out["retired"]), ([], []))
        self.assertEqual(out["matched"], [{"key": "routine:a_test.py", "thread": "F3"}])
        batch["state"] = "superseded"
        out = self.flags(_rsig(["a_test.py"]), threads=[batch], next_f=4)
        self.assertEqual(out["new"][0]["members"], [{"path": "a_test.py", "kind": "test", "reason": None},
                                                    {"path": "b_test.py", "kind": None, "reason": None}])

    def test_a_split_member_with_no_routine_signal_still_gets_its_own_thread(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"], state="changes-requested")
        out = self.flags(_signals(splits=[(3, "b_test.py")]), threads=[batch], next_f=4)
        self.assertEqual(sorted((t["flags"], t["supersedes"]) for t in out["new"]),
                         [(["out-of-plan:b_test.py"], None), (["routine:a_test.py"], "F3")])

    def test_a_member_of_an_accepted_batch_that_takes_its_own_thread_gets_one_now(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"], state="accepted")
        out = self.flags(_rsig(["a_test.py"], out_of_plan=["b_test.py"]), threads=[batch], next_f=4)
        self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]], [(["out-of-plan:b_test.py"], None)])
        # A repeated split line on the accepted batch posts nothing twice: the member's own head matches it.
        own = _pthread(4, "b_test.py", None, ["out-of-plan:b_test.py"])
        out = self.flags(_rsig(["a_test.py"], splits=[(3, "b_test.py")]), threads=[batch, own], next_f=5)
        self.assertEqual(out["new"], [])

    def test_a_superseded_batch_with_an_unchanged_set_still_gets_a_successor(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py"], state="superseded")
        out = self.flags(_rsig(["a_test.py"]), threads=[batch], next_f=4)
        self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]], [(["routine:a_test.py"], "F3")])

    def test_a_rejected_split_line_moves_nothing(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"])
        out = self.flags(_rsig(["a_test.py", "b_test.py"], splits=[(9, "b_test.py")]), threads=[batch], next_f=4)
        self.assertEqual(out["new"], [])
        self.assertEqual([r["reason"] for r in out["rejected"]], ["not-a-member"])

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

    def test_routine_needs_a_routine_verdict_and_no_other_signal(self):
        verdict = self.triage("## Triage\n- routine · a_test.py — test: t\n- routine · b_test.py — fixture: f\n"
                              "- routine · s.py — fallout: f\n- routine · j.py — fallout: f\n")
        # A split line is applied by build_flags once it is checked against the batch, never here.
        sig = rt.gather_signals("", self.ASSESS, [], [], triage=verdict, split_lines=["F3 b_test.py", "nonsense"])
        self.assertEqual(sig["routine"], ["a_test.py", "b_test.py"])
        self.assertEqual(sig["routine_kinds"], {"a_test.py": "test", "b_test.py": "fixture"})
        self.assertEqual(sig["out_of_plan"], ["s.py", "j.py", "x.py"])
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

    def test_flags_batches_from_the_triage_file_and_fails_closed_without_one(self):
        body = self.scratch / "body.md"
        body.write_text("## Out-of-plan changes\n- `api/off.ts` — regenerated\n", encoding="utf-8")
        assess = self.scratch / "assess.json"
        assess.write_text(json.dumps({"payload": self._payload(spans=[])}), encoding="utf-8")
        threads = self.scratch / "threads.json"
        threads.write_text("[]", encoding="utf-8")
        triage = self.scratch / "triage.md"
        triage.write_text("## Triage\n- routine · `api/off.ts` — fallout: regenerated client\n"
                          "- routine · app/guard.rb — test: x\n", encoding="utf-8")

        def run(**kw):
            return rt.run_flags(self.clone.path, str(body), str(assess), str(threads), "main", self.head,
                                str(self.scratch / "s"), **kw)
        out, notices = run(triage=str(triage), reasons_trusted=True)
        batch = [t for t in out["new"] if t["members"]]
        self.assertEqual(batch[0]["members"], [{"path": "api/off.ts", "kind": "fallout", "reason": "regenerated"},
                                               {"path": "app/guard.rb", "kind": "test", "reason": None}])
        out, _ = run(triage=str(triage), reasons_trusted=False)
        self.assertIsNone([t for t in out["new"] if t["members"]][0]["members"][0]["reason"])
        # An unreadable verdict batches nothing, with its notice.
        out, notices = run(triage=str(self.scratch / "missing.md"))
        self.assertTrue(all(t["members"] is None for t in out["new"]))
        self.assertTrue(any(n.startswith(rt.OUT_OF_PLAN_TRIAGE_UNREAD) for n in notices))
        # An unreadable split list is a usage error, as for the other model inputs.
        with self.assertRaises(SystemExit) as raised:
            run(triage=str(triage), splits=str(self.scratch / "missing-splits.txt"))
        self.assertEqual(raised.exception.code, 2)

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



class PauseTests(unittest.TestCase):
    """`run_pause` (#170) against a real sandbox repo, the thread read stubbed: the phase's signals anchored on
    the PR's whole diff, the spec it always writes, and its failures."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)
        lines = ["line %d\n" % i for i in range(1, 21)]
        # Phase 1: a.py's line 5 and a new c.py, then a fix moving a.py's line 5 again; phase 2: b.py only.
        self.p1a = self._commit({"a.py": "".join(lines[:4] + ["ONE\n"] + lines[5:]), "c.py": "c\n"})
        self.p1b = self._commit({"a.py": "".join(lines[:4] + ["TWO\n"] + lines[5:])})
        self.head = self._commit({"b.py": "b\n"})
        self.body = self.scratch / "pr.md"
        self.body.write_text("Fixes #1\n", encoding="utf-8")
        self.assess = self.scratch / "assess.json"
        self.assess.write_text(json.dumps({"payload": {
            "range": {"base": self.p1b}, "sensitive": {"matches": [{"path": "b.py"}], "error": None},
            "out_of_plan": [], "judgment": None}}), encoding="utf-8")
        # An earlier pause's superseded chain head on a.py, and a batch head whose member c.py phase 2 left alone.
        self.threads = [
            _pthread(1, "a.py", [5, 5], ["plan:a.py"], state="superseded", head=self.p1a),
            _pthread(2, "c.py", None, ["routine:c.py"], head=self.p1a),
        ]

    def _git(self, *args):
        return gitsandbox._run_git(list(args), cwd=self.clone.path)

    def _commit(self, files):
        for name, text in files.items():
            (self.clone.path / name).write_text(text, encoding="utf-8")
            self._git("add", name)
        self._git("commit", "-q", "-m", "c")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _read(self, available=True):
        threads_path = self.scratch / "read-threads.json"
        threads_path.write_text(json.dumps(self.threads), encoding="utf-8")
        payload = dict(rt._empty_payload(self.head), available=available,
                       threads_path=str(threads_path) if available else None)
        notices = [] if available else ["%s: unread" % rt.REVIEW_THREADS_UNAVAILABLE]
        return mock.patch.object(rt, "read_threads", return_value=(payload, notices))

    def _pause(self, assess=None):
        return rt.run_pause(str(self.clone.path), "o/r", 5, self.head, "main", 2, str(assess or self.assess),
                            str(self.body), str(self.scratch / "s"), True)

    def test_pause_anchors_on_the_whole_pr_diff(self):
        with self._read():
            out, notices = self._pause()
        self.assertTrue(out["threads_available"])
        self.assertEqual(out["phase"], 2)
        by_path = {t["path"]: t for t in out["new"]}
        # The phase's signal: b.py, which only phase 2 changed.
        self.assertEqual(by_path["b.py"]["flags"], ["sensitive:b.py"])
        # a.py left phase 2's diff but not the PR's: its superseded head gets a successor, not `path-left-diff`.
        self.assertEqual(by_path["a.py"]["supersedes"], "F1")
        # c.py stays in its batch: nothing retires or re-cuts it.
        self.assertEqual(out["retired"], [])
        self.assertNotIn("c.py", by_path)
        spec = json.loads(Path(out["spec_path"]).read_text(encoding="utf-8"))
        self.assertEqual((spec["head"], spec["phase"]), (self.head, 2))

    def test_a_phase_scoped_anchor_diff_would_retire_both(self):
        # The same signals anchored on assess's phase-scoped `range.base`: what `pause` must not do.
        out, _ = rt.run_flags(str(self.clone.path), str(self.body), str(self.assess), None, "main", self.head,
                              str(self.scratch / "f"), threads=self.threads)
        self.assertEqual(sorted(r["reason"] for r in out["retired"]), ["batch-emptied", "path-left-diff"])

    def test_nothing_anchored_still_writes_a_spec_carrying_the_phase(self):
        self.threads = []
        quiet = self.scratch / "quiet.json"
        quiet.write_text(json.dumps({"payload": {"range": {"base": self.p1b}, "sensitive": {"matches": []},
                                                 "out_of_plan": []}}), encoding="utf-8")
        with self._read():
            out, _ = self._pause(assess=quiet)
        self.assertEqual(out["new"], [])
        spec = json.loads(Path(out["spec_path"]).read_text(encoding="utf-8"))
        self.assertEqual((spec["phase"], spec["threads"]), (2, []))
        self.assertTrue(spec["review_body"].endswith("review-body.md"))

    def test_an_unreadable_pr_range_writes_no_spec(self):
        with self._read():
            out, notices = rt.run_pause(str(self.clone.path), "o/r", 5, self.head, "no-such-branch", 2,
                                        str(self.assess), str(self.body), str(self.scratch / "s"), True)
        self.assertEqual((out["threads_available"], out["diff_available"], out["spec_path"]), (True, False, None))
        self.assertTrue(notices[-1].startswith("REVIEW_THREADS_DIFF_UNAVAILABLE"))
        self.assertNotIn("evaluator", notices[-1])

    def test_a_pause_flags_notice_names_no_evaluator(self):
        out, notices = rt.run_flags(str(self.clone.path), str(self.body), str(self.assess), None, "main", self.head,
                                    str(self.scratch / "f"), threads=[], anchor_base="f" * 40, phase=2)
        self.assertFalse(out["diff_available"])
        self.assertNotIn("evaluator", notices[0])
        # Control: the evaluator's `flags` keeps its wording.
        _, evaluator = rt.run_flags(str(self.clone.path), str(self.body), str(self.assess), None, "main", self.head,
                                    str(self.scratch / "f"), threads=[], anchor_base="f" * 40)
        self.assertIn("the evaluator must not approve", evaluator[0])

    def test_the_merge_base_is_the_pushed_heads_not_the_checkouts(self):
        # A local commit after the push moves HEAD; the pause still reads the pushed head's range.
        pushed = self.head
        self._commit({"late.py": "x\n"})
        merge_base, paths, error = rt._pr_range(str(self.clone.path), "main", pushed)
        self.assertIsNone(error)
        self.assertEqual(sorted(paths), ["a.py", "b.py", "c.py"])
        self.assertEqual(merge_base, self._git("merge-base", pushed, "origin/main").stdout.strip())

    def test_an_earlier_phase_cannot_exempt_a_file_as_generated(self):
        # The exemptions read the PR's range, as the evaluator's do: an attribute the PR added in an earlier
        # phase is not set at the PR's merge base, so the file keeps its out-of-plan thread at the pause.
        self.threads = []
        attr = self._commit({".gitattributes": "g.py linguist-generated\n", "g.py": "x\n"})
        self.head = self._commit({"g.py": "y\n"})
        assess = self.scratch / "gen.json"
        assess.write_text(json.dumps({"payload": {"range": {"base": attr}, "sensitive": {"matches": []},
                                                  "out_of_plan": ["g.py"], "changed_paths": ["g.py"],
                                                  "planned_spans": []}}), encoding="utf-8")
        with self._read():
            out, _ = self._pause(assess=assess)
        self.assertEqual(out["exempt"], [])
        self.assertEqual([t["flags"] for t in out["new"]], [["out-of-plan:g.py"]])
        # Control: the phase-scoped range alone would have exempted it.
        phase_only, _ = rt.run_flags(str(self.clone.path), str(self.body), str(assess), None, "main", self.head,
                                     str(self.scratch / "f"), threads=[])
        self.assertEqual([e["path"] for e in phase_only["exempt"]], ["g.py"])

    def test_a_failed_thread_read_writes_no_spec(self):
        with self._read(available=False):
            out, notices = self._pause()
        self.assertEqual((out["threads_available"], out["spec_path"], out["new"]), (False, None, []))
        self.assertTrue(notices[0].startswith(rt.REVIEW_THREADS_UNAVAILABLE))
        self.assertFalse((self.scratch / "s" / "review-spec.json").exists())

    def test_an_unreadable_input_is_a_usage_error_before_any_read(self):
        with self._read() as read, self.assertRaises(SystemExit) as raised:
            rt.run_pause(str(self.clone.path), "o/r", 5, self.head, "main", 2, str(self.assess),
                         str(self.scratch / "missing.md"), str(self.scratch / "s"), True)
        self.assertEqual(raised.exception.code, 2)
        read.assert_not_called()

    def test_the_cli_refuses_a_phase_that_is_not_positive(self):
        for bad in ("0", "x", "-1"):
            with self.assertRaises(SystemExit) as raised:
                rt.main(["pause", str(self.clone.path), "--repo", "o/r", "--pr", "5", "--head", self.head,
                         "--base-ref", "main", "--phase", bad, "--assess", str(self.assess), "--pr-body",
                         str(self.body), "--scratch", str(self.scratch), "--reasons-trusted", "true"])
            self.assertEqual(raised.exception.code, 2)


class PauseThreadLifecycleTests(unittest.TestCase):
    """DoD 2 (#170): the evaluator's later flags against a pause thread, which is an ordinary chain head."""

    FILES = {"b.py": {"hunks": [(1, 3)]}}

    def _pause_thread(self, state):
        thread = _pthread(3, "b.py", None, ["sensitive:b.py"], state=state)
        stamped = rt.stamp_root(3, HEAD, "b.py", "file", None, None, ["sensitive:b.py"], "b", phase=1)
        thread["comments"][0]["body"] = stamped
        return thread

    def test_the_evaluator_posts_no_duplicate_of_a_live_pause_thread(self):
        for state in ("unreviewed", "accepted", "changes-requested"):
            out = rt.build_flags(self.FILES, [self._pause_thread(state)], _signals(sensitive=["b.py"]), _identity, 4)
            self.assertEqual(out["new"], [], state)
            self.assertEqual([m["key"] for m in out["matched"]], ["sensitive:b.py"], state)

    def test_a_superseded_pause_thread_gets_one_successor(self):
        out = rt.build_flags(self.FILES, [self._pause_thread("superseded")], _signals(sensitive=["b.py"]), _identity, 4)
        self.assertEqual([(t["f"], t["supersedes"], t["flags"]) for t in out["new"]], [(4, "F3", ["sensitive:b.py"])])

    def test_a_changes_requested_pause_thread_keeps_gating_when_its_hunk_moves(self):
        # Rule 7 excludes it: the hunk check never runs, so it stays `changes-requested` until a fix reply.
        comments = [{"marked": True, "trusted": True, "decision": None},
                    {"marked": False, "trusted": True, "decision": None}]
        self.assertEqual(rt.derive_state(comments, "pipeline", False, _never)[0], "changes-requested")




# ---------------------------------------------------------------------------
# #181 phase 4: riders, advisory batches, sensitive groups
# ---------------------------------------------------------------------------


def _as_heads(new, state="unreviewed"):
    """``flags``' ``new`` entries read back as the next run's chain heads (the idempotence check)."""
    return [_pthread(t["f"], t["path"], [t["start_line"], t["line"]] if t["subject"] == "line" else None,
                     t["flags"], state=state, supersedes=t["supersedes"], group=t["group"], advisory=t["advisory"])
            for t in new]


class RiderFlagsTests(unittest.TestCase):
    """A consequential test or fixture rides its subject's thread when that thread posts this run (#181)."""

    FILES = {"app.py": {"hunks": [(1, 3)]}, "app_test.py": {"hunks": [(1, 9)]}, "lib.py": {"hunks": [(4, 1)]}}

    def flags(self, signals, threads=(), next_f=1):
        return rt.build_flags(self.FILES, list(threads), signals, _identity, next_f)

    def test_a_test_with_a_subject_rides_the_subjects_new_thread(self):
        out = self.flags(_signals(out_of_plan=["app.py", "app_test.py"], subjects={"app_test.py": "app.py"},
                                  reasons={"app.py": "needed a hook"}))
        self.assertEqual(len(out["new"]), 1)
        thread = out["new"][0]
        self.assertEqual((thread["path"], thread["flags"]), ("app.py", ["out-of-plan:app.py", "out-of-plan:app_test.py"]))
        self.assertEqual(thread["riders"], [{"path": "app_test.py", "subject": "app.py"}])
        self.assertEqual(thread["reason"], "needed a hook")
        # Re-run over the unchanged PR: the rider matches its subject's thread, so nothing posts.
        again = self.flags(_signals(out_of_plan=["app.py", "app_test.py"], subjects={"app_test.py": "app.py"}),
                           threads=_as_heads(out["new"]), next_f=2)
        self.assertEqual(again["new"], [])
        self.assertIn({"key": "out-of-plan:app_test.py", "thread": "F1"}, again["matched"])

    def test_a_riders_reason_is_carried_only_when_trusted(self):
        sig = dict(out_of_plan=["app.py", "app_test.py"], subjects={"app_test.py": "app.py"},
                   reasons={"app.py": "needed a hook", "app_test.py": "pins the hook's refusal"})
        trusted = self.flags(_signals(reasons_trusted=True, **sig))["new"][0]
        self.assertEqual(trusted["riders"], [{"path": "app_test.py", "subject": "app.py",
                                              "reason": "pins the hook's refusal"}])
        untrusted = self.flags(_signals(reasons_trusted=False, **sig))["new"][0]
        self.assertEqual(untrusted["riders"], [{"path": "app_test.py", "subject": "app.py"}])

    def test_a_test_with_no_subject_or_one_with_no_thread_this_run_keeps_its_own(self):
        for subjects in ({}, {"app_test.py": "lib.py"}):
            out = self.flags(_signals(out_of_plan=["app.py", "app_test.py"], subjects=subjects))
            self.assertEqual([t["flags"] for t in out["new"]], [["out-of-plan:app.py"], ["out-of-plan:app_test.py"]],
                             subjects)
            self.assertTrue(all(t["riders"] == [] for t in out["new"]))

    def test_a_test_carrying_a_sensitive_signal_keeps_its_own_thread(self):
        out = self.flags(_signals(out_of_plan=["app.py", "app_test.py"], sensitive=["app_test.py"],
                                  subjects={"app_test.py": "app.py"}))
        self.assertIn(["out-of-plan:app.py"], [t["flags"] for t in out["new"]])
        self.assertTrue(any("out-of-plan:app_test.py" in t["flags"] and t["path"] == "app_test.py" for t in out["new"]))

    def test_a_live_subject_head_takes_no_late_rider(self):
        head = _pthread(1, "app.py", None, ["out-of-plan:app.py"])
        out = self.flags(_signals(out_of_plan=["app.py", "app_test.py"], subjects={"app_test.py": "app.py"}),
                         threads=[head], next_f=2)
        self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]], [(["out-of-plan:app_test.py"], None)])

    def test_a_test_rides_its_subjects_successor(self):
        head = _pthread(1, "app.py", None, ["out-of-plan:app.py"], state="superseded")
        out = self.flags(_signals(out_of_plan=["app.py", "app_test.py"], subjects={"app_test.py": "app.py"}),
                         threads=[head], next_f=2)
        self.assertEqual(len(out["new"]), 1)
        succ = out["new"][0]
        self.assertEqual((succ["supersedes"], succ["flags"], succ["riders"]),
                         ("F1", ["out-of-plan:app.py", "out-of-plan:app_test.py"],
                          [{"path": "app_test.py", "subject": "app.py"}]))

    def test_a_generic_successor_lists_its_kept_and_joining_riders(self):
        # A rider from an earlier run stays on the successor's keys; with no subject this run its subject falls
        # back to the anchor path, and its reason rides only when the reasons are trusted.
        head = _pthread(1, "app.py", None, ["out-of-plan:app.py", "out-of-plan:lib.py"], state="superseded")
        out = self.flags(_signals(out_of_plan=["app.py", "app_test.py"], subjects={"app_test.py": "app.py"},
                                  reasons={"lib.py": "hook helper", "app_test.py": "asserts it"},
                                  reasons_trusted=True), threads=[head], next_f=2)
        self.assertEqual(out["new"][0]["riders"],
                         [{"path": "lib.py", "subject": "app.py", "reason": "hook helper"},
                          {"path": "app_test.py", "subject": "app.py", "reason": "asserts it"}])
        out = self.flags(_signals(out_of_plan=["app.py"]), threads=[head], next_f=2)
        self.assertEqual(out["new"][0]["riders"], [{"path": "lib.py", "subject": "app.py"}])

    def test_a_test_rides_a_subject_candidate_that_does_not_itself_ride(self):
        # app_test.py's subject lib.py is a rider candidate whose own subject has no thread this run, so lib.py
        # keeps its thread and app_test.py rides it.
        out = self.flags(_signals(out_of_plan=["lib.py", "app_test.py"],
                                  subjects={"app_test.py": "lib.py", "lib.py": "gone.py"}))
        self.assertEqual([(t["path"], t["flags"]) for t in out["new"]],
                         [("lib.py", ["out-of-plan:lib.py", "out-of-plan:app_test.py"])])
        # A chain: lib.py rides app.py, so app_test.py, whose subject rides, keeps its own thread.
        out = self.flags(_signals(out_of_plan=["app.py", "lib.py", "app_test.py"],
                                  subjects={"app_test.py": "lib.py", "lib.py": "app.py"}))
        self.assertEqual(sorted((t["path"], tuple(t["flags"])) for t in out["new"]),
                         [("app.py", ("out-of-plan:app.py", "out-of-plan:lib.py")),
                          ("app_test.py", ("out-of-plan:app_test.py",))])

    def test_a_riders_overflow_entry_names_its_subject(self):
        files = dict(self.FILES, **{"o%d.py" % i: {"hunks": [(1, 1)]} for i in range(8)})
        sig = _signals(out_of_plan=["o%d.py" % i for i in range(8)] + ["app.py", "app_test.py"],
                       subjects={"app_test.py": "app.py"})
        out = rt.build_flags(files, [], sig, _identity, 1)
        self.assertIn({"key": "out-of-plan:app_test.py", "path": "app_test.py", "subject": "app.py", "reason": "cap"},
                      out["overflow"])

    def test_a_split_test_keeps_its_own_thread(self):
        batch = _pthread(3, "app_test.py", None, ["routine:app_test.py", "routine:lib.py"], state="changes-requested")
        out = self.flags(_signals(out_of_plan=["app.py"], routine=["lib.py"], routine_kinds={"lib.py": "fallout"},
                                  subjects={"app_test.py": "app.py"}, splits=[(3, "app_test.py")]),
                         threads=[batch], next_f=4)
        self.assertIn(["out-of-plan:app_test.py"], [t["flags"] for t in out["new"]])
        self.assertIn(["out-of-plan:app.py"], [t["flags"] for t in out["new"]])

    def test_an_old_per_file_out_of_plan_head_is_never_re_covered_by_a_rider(self):
        own = _pthread(1, "app_test.py", None, ["out-of-plan:app_test.py"])
        out = self.flags(_signals(out_of_plan=["app.py", "app_test.py"], subjects={"app_test.py": "app.py"}),
                         threads=[own], next_f=2)
        self.assertEqual([t["flags"] for t in out["new"]], [["out-of-plan:app.py"]])
        self.assertIn({"key": "out-of-plan:app_test.py", "thread": "F1"}, out["matched"])

    def test_gather_signals_reads_a_subject_only_for_an_unflagged_consequential_file(self):
        import parse
        assess = {"payload": {"out_of_plan": ["app.py", "app_test.py", "s_test.py", "p_test.py", "q_test.py"],
                              "sensitive": {"matches": [{"pattern": "s*", "path": "s_test.py"}], "error": None}}}
        verdict = parse.scan_out_of_plan_triage(
            "## Triage\n- consequential · app.py — adds a hook\n"
            "- consequential · app_test.py — subject `app.py`; asserts the hook\n"
            "- consequential · s_test.py — subject `app.py`; asserts it\n"
            "- routine · p_test.py — test: covers the planned module\n"
            "- consequential · q_test.py — subject `p_test.py`; odd\n")
        sig = rt.gather_signals("", assess, [], [], triage=verdict)
        # A planned-subject test is routine and joins the batch; a routine subject takes no rider.
        self.assertEqual((sig["subjects"], sig["routine"]), ({"app_test.py": "app.py"}, ["p_test.py"]))


class AdvisoryBatchTests(unittest.TestCase):
    """A batch whose every target member is a test or fixture this run is stamped advisory (#181)."""

    FILES = BatchFlagsTests.FILES

    def flags(self, signals, threads=(), next_f=1):
        return rt.build_flags(self.FILES, list(threads), signals, _identity, next_f)

    def test_an_all_test_or_fixture_batch_is_advisory_and_a_fallout_or_unclassified_member_is_not(self):
        out = self.flags(_rsig(["a_test.py", "b_test.py"], kinds={"a_test.py": "test", "b_test.py": "fixture"}))
        self.assertEqual([(t["flags"], t["advisory"]) for t in out["new"]],
                         [(["routine:a_test.py", "routine:b_test.py"], True)])
        out = self.flags(_rsig(["a_test.py", "z_gone.py"], kinds={"a_test.py": "test", "z_gone.py": "fallout"}))
        self.assertFalse(out["new"][0]["advisory"])
        # A member kept with no routine verdict this run (kind null) makes its successor not advisory.
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"], state="superseded")
        out = self.flags(_rsig(["a_test.py"]), threads=[batch], next_f=4)
        self.assertEqual((out["new"][0]["supersedes"], out["new"][0]["advisory"]), ("F3", False))

    def test_a_successors_advisory_is_read_over_its_target_set(self):
        # b_test.py leaves (reclassified consequential); the target {a_test, c_test} is all tests.
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"])
        out = self.flags(_rsig(["a_test.py", "c_test.py"], out_of_plan=["b_test.py"]), threads=[batch], next_f=4)
        succ = [t for t in out["new"] if t["supersedes"] == "F3"][0]
        self.assertEqual((succ["flags"], succ["advisory"]), (["routine:a_test.py", "routine:c_test.py"], True))
        self.assertFalse([t for t in out["new"] if t["supersedes"] is None][0]["advisory"])

    def test_only_a_batch_is_ever_advisory_and_a_rerun_posts_nothing(self):
        out = self.flags(_rsig(["a_test.py"], out_of_plan=["x.py"], sensitive=["x.py"]))
        self.assertEqual({tuple(t["flags"]): t["advisory"] for t in out["new"]},
                         {("routine:a_test.py",): True, ("sensitive:x.py", "out-of-plan:x.py"): False})
        again = self.flags(_rsig(["a_test.py"], out_of_plan=["x.py"], sensitive=["x.py"]),
                           threads=_as_heads(out["new"]), next_f=3)
        self.assertEqual((again["new"], again["retired"]), ([], []))

    def test_a_stale_advisory_stamp_is_re_cut_only_on_a_run_with_a_readable_triage(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py", "routine:b_test.py"], advisory=True)
        sig = _rsig(["a_test.py", "b_test.py"], kinds={"a_test.py": "test", "b_test.py": "fallout"}, triage_read=True)
        out = self.flags(sig, threads=[batch], next_f=4)
        self.assertEqual([(t["flags"], t["supersedes"], t["advisory"]) for t in out["new"]],
                         [(["routine:a_test.py", "routine:b_test.py"], "F3", False)])
        # Still advisory: nothing posts. No readable triage (a pause): nothing posts either.
        out = self.flags(dict(sig, routine_kinds={"a_test.py": "test", "b_test.py": "fixture"}), threads=[batch],
                         next_f=4)
        self.assertEqual(out["new"], [])
        out = self.flags(_signals(), threads=[batch], next_f=4)
        self.assertEqual(out["new"], [])
        verdict = __import__("parse").scan_out_of_plan_triage("## Triage\n- routine · a.py — test: t\n")
        self.assertTrue(rt.gather_signals("", {"payload": {"out_of_plan": []}}, [], [], triage=verdict)["triage_read"])
        self.assertFalse(rt.gather_signals("", {"payload": {"out_of_plan": []}}, [], [])["triage_read"])

    def test_the_pause_passes_no_triage_so_its_re_cut_is_never_advisory(self):
        batch = _pthread(3, "a_test.py", None, ["routine:a_test.py"], state="superseded", advisory=True)
        sig = rt.gather_signals("", {"payload": {"out_of_plan": []}}, [], [])
        self.assertEqual(self.flags(sig, threads=[batch], next_f=4)["new"][0]["advisory"], False)


class SensitiveGroupTests(unittest.TestCase):
    """Sensitive-only files share one thread per declared pattern, re-cut like the batch (#181)."""

    FILES = {"config/a.yml": {"hunks": [(1, 1)]}, "config/b.yml": {"hunks": [(2, 1)]},
             "config/c.yml": {"hunks": [(3, 1)]}, "db/schema.rb": {"hunks": [(4, 1)]},
             "app.py": {"hunks": [(1, 5)]}}
    PATTERNS = {"config/a.yml": "config/**", "config/b.yml": "config/**", "config/c.yml": "config/**",
                "db/schema.rb": "db/**", "app.py": "*.py"}

    def sig(self, paths, **kw):
        return _signals(sensitive=list(paths), sensitive_patterns={p: self.PATTERNS[p] for p in paths}, **kw)

    def flags(self, signals, threads=(), next_f=1, files=None):
        return rt.build_flags(files or self.FILES, list(threads), signals, _identity, next_f)

    def group(self, f, members, state="unreviewed", **kw):
        return _pthread(f, sorted(members)[0], None, ["sensitive:%s" % m for m in sorted(members)], state=state,
                        group="sensitive", **kw)

    def test_sensitive_only_files_under_one_pattern_share_one_thread(self):
        out = self.flags(self.sig(["config/c.yml", "config/a.yml", "config/b.yml", "db/schema.rb"]))
        self.assertEqual([(t["path"], t["flags"], t["group"], t["pattern"]) for t in out["new"]], [
            ("config/a.yml", ["sensitive:config/a.yml", "sensitive:config/b.yml", "sensitive:config/c.yml"],
             "sensitive", "config/**"),
            ("db/schema.rb", ["sensitive:db/schema.rb"], "sensitive", "db/**"),
        ])
        self.assertFalse(any(t["advisory"] for t in out["new"]))

    def test_a_file_matching_two_entries_joins_the_first_in_declaration_order(self):
        assess = {"payload": {"sensitive": {"error": None, "matches": [
            {"pattern": "config/**", "path": "config/a.yml"}, {"pattern": "config/**", "path": "config/b.yml"},
            {"pattern": "**/*.yml", "path": "config/b.yml"}, {"pattern": "**/*.yml", "path": "config/c.yml"}]}}}
        sig = rt.gather_signals("", assess, [], [])
        self.assertEqual(sig["sensitive_patterns"],
                         {"config/a.yml": "config/**", "config/b.yml": "config/**", "config/c.yml": "**/*.yml"})
        out = self.flags(sig)
        self.assertEqual([t["flags"] for t in out["new"]],
                         [["sensitive:config/a.yml", "sensitive:config/b.yml"], ["sensitive:config/c.yml"]])

    def test_a_file_with_another_signal_or_no_pattern_keeps_its_own_thread(self):
        out = self.flags(self.sig(["config/a.yml", "config/b.yml", "app.py"],
                                  judgment=[("trust", "config/b.yml", [2, 2])]))
        by_flags = {tuple(t["flags"]): t for t in out["new"]}
        self.assertEqual(by_flags[("sensitive:config/a.yml",)]["group"], "sensitive")
        self.assertIsNone(by_flags[("sensitive:config/b.yml",)]["group"])
        out = self.flags(_signals(sensitive=["config/a.yml", "config/b.yml"]))
        self.assertEqual([(t["flags"], t["group"]) for t in out["new"]],
                         [(["sensitive:config/a.yml"], None), (["sensitive:config/b.yml"], None)])

    def test_a_member_flagged_with_no_pattern_leaves_its_group(self):
        # It takes a per-file thread, so it must not also stay a key of its group: one file, one live thread.
        head = self.group(3, ["config/a.yml", "config/b.yml"])
        sig = _signals(sensitive=["config/a.yml", "config/b.yml"], sensitive_patterns={"config/a.yml": "config/**"})
        out = self.flags(sig, threads=[head], next_f=4)
        self.assertEqual(sorted((t["flags"], t["supersedes"]) for t in out["new"]),
                         [(["sensitive:config/a.yml"], "F3"), (["sensitive:config/b.yml"], None)])

    def test_a_group_counts_once_against_the_cap_and_ranks_sensitive(self):
        files = dict(self.FILES, **{"o%d.py" % i: {"hunks": [(1, 1)]} for i in range(8)})
        out = self.flags(self.sig(["config/a.yml", "config/b.yml", "config/c.yml"],
                                  out_of_plan=["o%d.py" % i for i in range(7)]), files=files)
        self.assertEqual(len(out["new"]), 8)
        out = self.flags(self.sig(["config/a.yml", "config/b.yml", "config/c.yml"],
                                  out_of_plan=["o%d.py" % i for i in range(8)]), files=files)
        self.assertEqual(out["new"][0]["group"], "sensitive")
        self.assertEqual(out["overflow"], [{"key": "out-of-plan:o7.py", "path": "o7.py", "reason": "cap"}])

    def test_unreviewed_and_superseded_groups_re_cut_and_accepted_ones_never_do(self):
        for state in ("unreviewed", "superseded"):
            head = self.group(3, ["config/a.yml", "config/b.yml"], state=state)
            out = self.flags(self.sig(["config/a.yml", "config/b.yml", "config/c.yml"]), threads=[head], next_f=4)
            self.assertEqual([(t["flags"], t["supersedes"], t["group"]) for t in out["new"]], [
                (["sensitive:config/a.yml", "sensitive:config/b.yml", "sensitive:config/c.yml"], "F3", "sensitive")],
                state)
        for state in ("accepted", "changes-requested"):
            head = self.group(3, ["config/a.yml", "config/b.yml"], state=state)
            out = self.flags(self.sig(["config/a.yml", "config/b.yml", "config/c.yml"]), threads=[head], next_f=4)
            self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]],
                             [(["sensitive:config/c.yml"], None)], state)
            self.assertEqual(sorted(m["key"] for m in out["matched"]),
                             ["sensitive:config/a.yml", "sensitive:config/b.yml"], state)
        # An unchanged unreviewed group posts nothing.
        head = self.group(3, ["config/a.yml", "config/b.yml"])
        out = self.flags(self.sig(["config/a.yml", "config/b.yml"]), threads=[head], next_f=4)
        self.assertEqual((out["new"], len(out["matched"])), ([], 2))

    def test_a_member_gaining_a_signal_leaves_and_an_emptied_group_retires(self):
        head = self.group(3, ["config/a.yml", "config/b.yml"])
        out = self.flags(self.sig(["config/a.yml", "config/b.yml"], out_of_plan=["config/b.yml"]),
                         threads=[head], next_f=4)
        self.assertEqual(sorted((t["flags"], t["supersedes"]) for t in out["new"]),
                         [(["sensitive:config/a.yml"], "F3"), (["sensitive:config/b.yml", "out-of-plan:config/b.yml"],
                                                               None)])
        head = self.group(3, ["config/a.yml"], state="superseded")
        out = self.flags(self.sig(["config/a.yml"], judgment=[("trust", "config/a.yml", [1, 1])]), threads=[head],
                         next_f=4)
        self.assertEqual(out["retired"], [{"thread": "T3", "ref": "F3", "reason": "group-emptied"}])
        # A member that left the diff leaves too; one merely no longer sensitive stays.
        head = self.group(3, ["config/a.yml", "config/b.yml", "gone.yml"], state="superseded")
        out = self.flags(self.sig(["config/a.yml"]), threads=[head], next_f=4)
        self.assertEqual(out["new"][0]["flags"], ["sensitive:config/a.yml", "sensitive:config/b.yml"])

    def test_an_old_per_file_sensitive_head_keeps_gating_and_no_group_re_covers_it(self):
        old = _pthread(1, "config/a.yml", None, ["sensitive:config/a.yml"])
        out = self.flags(self.sig(["config/a.yml", "config/b.yml", "config/c.yml"]), threads=[old], next_f=2)
        self.assertEqual([(t["flags"], t["supersedes"]) for t in out["new"]],
                         [(["sensitive:config/b.yml", "sensitive:config/c.yml"], None)])
        self.assertIn({"key": "sensitive:config/a.yml", "thread": "F1"}, out["matched"])
        # Superseded, it gets the generic one-file successor — never a group.
        old["state"] = "superseded"
        out = self.flags(self.sig(["config/a.yml"]), threads=[old], next_f=2)
        self.assertEqual([(t["flags"], t["supersedes"], t["group"]) for t in out["new"]],
                         [(["sensitive:config/a.yml"], "F1", None)])

    def test_a_rerun_over_an_unchanged_pr_posts_nothing(self):
        files = dict(self.FILES, **BatchFlagsTests.FILES, **RiderFlagsTests.FILES)
        sig = self.sig(["config/a.yml", "config/b.yml", "db/schema.rb"], out_of_plan=["lib.py", "app_test.py"],
                       subjects={"app_test.py": "lib.py"}, routine=["a_test.py", "b_test.py"],
                       routine_kinds={"a_test.py": "test", "b_test.py": "fixture"})
        out = self.flags(sig, files=files)
        self.assertEqual(sorted((str(t["group"]), t["advisory"], len(t["riders"])) for t in out["new"]),
                         [("None", False, 1), ("None", True, 0), ("sensitive", False, 0), ("sensitive", False, 0)])
        again = self.flags(sig, threads=_as_heads(out["new"]), next_f=out["next_f"], files=files)
        self.assertEqual((again["new"], again["retired"], again["overflow"]), ([], [], []))


class SpecGroupAdvisoryTests(unittest.TestCase):
    """`run_flags` writes each new thread's `group` and `advisory` into the review-post spec (#181)."""

    def test_the_spec_carries_group_and_advisory(self):
        origin = gitsandbox.mk_origin()
        self.addCleanup(origin.cleanup)
        clone = gitsandbox.mk_clone(origin)
        self.addCleanup(clone.cleanup)
        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, scratch, True)

        def commit(files):
            for name, text in files.items():
                (clone.path / name).parent.mkdir(parents=True, exist_ok=True)
                (clone.path / name).write_text(text, encoding="utf-8")
                gitsandbox._run_git(["add", name], cwd=clone.path)
            gitsandbox._run_git(["commit", "-q", "-m", "c"], cwd=clone.path)
            return gitsandbox._run_git(["rev-parse", "HEAD"], cwd=clone.path).stdout.strip()

        base = commit({"seed.txt": "x\n"})
        head = commit({"config/a.yml": "a\n", "config/b.yml": "b\n", "a_test.py": "t\n"})
        body = scratch / "body.md"
        body.write_text("", encoding="utf-8")
        (scratch / "triage.md").write_text("## Triage\n- routine · a_test.py — test: covers it\n", encoding="utf-8")
        assess = scratch / "assess.json"
        assess.write_text(json.dumps({"payload": {"range": {"base": base}, "changed_paths": [], "out_of_plan": ["a_test.py"],
                                                  "sensitive": {"error": None, "matches": [
                                                      {"pattern": "config/**", "path": "config/a.yml"},
                                                      {"pattern": "config/**", "path": "config/b.yml"}]}}}),
                          encoding="utf-8")
        threads = scratch / "threads.json"
        threads.write_text("[]", encoding="utf-8")
        out, _ = rt.run_flags(clone.path, str(body), str(assess), str(threads), "main", head, str(scratch / "s"),
                              triage=str(scratch / "triage.md"))
        spec = json.loads(Path(out["spec_path"]).read_text(encoding="utf-8"))
        self.assertEqual([(t["flags"], t["group"], t["advisory"]) for t in spec["threads"]], [
            (["sensitive:config/a.yml", "sensitive:config/b.yml"], "sensitive", False),
            (["routine:a_test.py"], None, True)])


if __name__ == "__main__":
    unittest.main()
