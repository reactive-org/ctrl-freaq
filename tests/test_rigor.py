"""#198: `scripts/rigor.py` — the rigor level, its floor rules, the strictest-label rule and the step table.

`resolve` is the pure core and carries most cases. `read_block` runs against a temp checkout, `read_labels`
and `build_rigor` against the `rigor_labels` shim case (the exact GraphQL query `read_labels` sends).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import rigor
from scripts import review_threads
from tests.support import shimenv

REPO = "octo/widgets"
BLOCK = "<!-- ctrl-freaq-rigor -->\n%s<!-- /ctrl-freaq-rigor -->\n"


def block(default=None, floor=None):
    """A parsed, well-formed block as `read_block` returns it."""
    return {"path": "/repo/CLAUDE.md", "default": default, "floor": floor, "malformed": False}


def labels(issue=(), ancestors=()):
    """One issue's label set, as `resolve` takes it."""
    return [entry(issue, ancestors)]


def entry(issue=(), ancestors=()):
    return {"issue": list(issue), "ancestors": [list(ancestors)] if ancestors else []}


def codes(notices):
    return [notice.split(":", 1)[0] for notice in notices]


class StepsTests(unittest.TestCase):
    def test_every_level_has_every_key(self):
        self.assertEqual(set(rigor.STEPS), set(rigor.LEVELS))
        keys = set(rigor.STEPS["full"])
        for level in rigor.LEVELS:
            self.assertEqual(set(rigor.STEPS[level]), keys, level)

    def test_each_levels_values(self):
        expected = {
            "plan_review_passes": (3, 2, 1),
            "plan_review_blockers_only": (False, False, True),
            "review_rounds": (None, 4, 2),
            "unplanned_level": ("high", "medium", "low"),
            "fixed_level": (None, None, "low"),
            "judgment": (True, False, False),
            "stall_card": (True, False, False),
            "polish_in_loop": ("fix", "list", "list"),
            "polish_card": ("resolver+backstop", "resolver+open-or-unfixed-apply", "none"),
            "cold_read": ("every-phase-loop", "every-phase-once", "final-once"),
            "security_round": ("loop", "once-pass-level", "once-low"),
            "dismissal_check": (True, True, False),
            "defect_injection": (True, True, False),
            "fix_design": ("on", "precheck-only", "precheck-only"),
            "out_of_plan_triage": (True, True, False),
            "thread_sources": (
                ["security", "design", "sensitive", "judgment", "out-of-plan", "routine", "plan"],
                ["security", "design", "out-of-plan", "routine", "plan"],
                ["security", "design"],
            ),
            "pause_signals": ("all", "all-but-judgment", "checkpoint-and-warning"),
        }
        self.assertEqual(set(expected), set(rigor.STEPS["full"]))
        for key, values in expected.items():
            self.assertEqual(tuple(rigor.STEPS[level][key] for level in ("full", "medium", "light")), values, key)

    def test_the_marker_is_the_self_edit_matchers(self):
        from scripts import sensitive_paths

        self.assertEqual(rigor.MARKER, sensitive_paths.RIGOR_MARKER)

    def test_full_thread_sources_are_every_flag_kind(self):
        self.assertEqual(set(rigor.STEPS["full"]["thread_sources"]), set(review_threads.FLAG_KINDS))

    def test_every_level_keeps_design_threads(self):
        # #206 [user decision 2026-11-14]: a design difference posts at every rigor level, so `--rigor` never
        # drops one.
        for level in rigor.LEVELS:
            with self.subTest(level=level):
                self.assertIn("design", rigor.STEPS[level]["thread_sources"])

    def test_the_resolved_steps_are_a_copy(self):
        resolved, _ = rigor.resolve(None, labels())
        resolved["steps"]["thread_sources"].append("x")
        self.assertNotIn("x", rigor.STEPS["full"]["thread_sources"])


class ResolveBlockTests(unittest.TestCase):
    def test_no_block_is_full(self):
        resolved, notices = rigor.resolve(None, labels())
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"], notices), ("full", "default", []))
        self.assertEqual(resolved["source"]["block"],
                         {"present": False, "path": None, "default": "full", "floor": "full", "changed": False})
        self.assertFalse(resolved["full_rules"])
        self.assertEqual(resolved["steps"], rigor.STEPS["full"])

    def test_default_only_sets_the_floor_too(self):
        resolved, notices = rigor.resolve(block(default="medium"), labels())
        self.assertEqual((resolved["level"], resolved["source"]["block"]["floor"], notices), ("medium", "medium", []))
        # The floor is the default, so a label below it is ignored.
        resolved, notices = rigor.resolve(block(default="medium"), labels(["rigor:light"]))
        self.assertEqual((resolved["level"], codes(notices)), ("medium", [rigor.RIGOR_LABEL_BELOW_FLOOR]))

    def test_no_default_is_full(self):
        resolved, _ = rigor.resolve(block(floor="light"), labels())
        self.assertEqual((resolved["level"], resolved["source"]["block"]["default"]), ("full", "full"))

    def test_both_keys(self):
        resolved, notices = rigor.resolve(block(default="light", floor="light"), labels())
        self.assertEqual((resolved["level"], resolved["steps"], notices), ("light", rigor.STEPS["light"], []))

    def test_a_default_below_the_floor_resolves_to_the_floor(self):
        resolved, notices = rigor.resolve(block(default="light", floor="medium"), labels())
        self.assertEqual((resolved["level"], codes(notices)), ("medium", [rigor.RIGOR_DEFAULT_BELOW_FLOOR]))
        self.assertEqual(resolved["source"]["block"]["default"], "medium")

    def test_a_malformed_block_is_full_by_fallback(self):
        malformed = {"path": "/repo/CLAUDE.md", "default": None, "floor": None, "malformed": True}
        resolved, _ = rigor.resolve(malformed, labels(["rigor:light"]))
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "fallback"))
        self.assertFalse(resolved["source"]["block"]["present"])


class ResolveLabelTests(unittest.TestCase):
    OPEN = block(default="light", floor="light")

    def test_no_labels_is_the_default(self):
        resolved, _ = rigor.resolve(self.OPEN, labels(["bug"]))
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("light", "default"))

    def test_an_issue_full_label_beats_a_light_default(self):
        resolved, _ = rigor.resolve(self.OPEN, labels(["rigor:full"]))
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "label:issue"))

    def test_a_label_below_the_floor_is_ignored(self):
        resolved, notices = rigor.resolve(block(default="full", floor="medium"), labels(["rigor:light"]))
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "default"))
        self.assertEqual(codes(notices), [rigor.RIGOR_LABEL_BELOW_FLOOR])
        # A declared floor names no missing block.
        self.assertNotIn("block", notices[0])

    def test_a_floor_no_block_declared_says_why(self):
        # No block, or a malformed one, is a `full` floor the repo never set: the notice says where it came from.
        resolved, notices = rigor.resolve(None, labels(["rigor:light"]))
        self.assertEqual((resolved["level"], codes(notices)), ("full", [rigor.RIGOR_LABEL_BELOW_FLOOR]))
        self.assertIn("declares no `<!-- ctrl-freaq-rigor -->` block, so the floor is `full`", notices[0])
        malformed = dict(block(), malformed=True, fault="lines")
        resolved, notices = rigor.resolve(malformed, labels(["rigor:medium"]))
        self.assertEqual((resolved["level"], codes(notices)), ("full", [rigor.RIGOR_LABEL_BELOW_FLOOR]))
        self.assertIn("block is malformed, so the floor is `full`", notices[0])

    def test_an_unknown_label_is_ignored(self):
        resolved, notices = rigor.resolve(self.OPEN, labels(["rigor:fast"]))
        self.assertEqual((resolved["level"], codes(notices)), ("light", [rigor.RIGOR_LABEL_UNKNOWN]))
        resolved, notices = rigor.resolve(self.OPEN, labels(["rigor:fast", "rigor:medium"]))
        self.assertEqual((resolved["level"], codes(notices)), ("medium", [rigor.RIGOR_LABEL_UNKNOWN]))

    def test_the_strictest_label_wins_across_the_issue_and_its_ancestors(self):
        resolved, _ = rigor.resolve(self.OPEN, labels(["rigor:light"], ["rigor:medium"]))
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("medium", "label:ancestor"))
        resolved, _ = rigor.resolve(self.OPEN, labels(["rigor:medium", "rigor:light"], ["rigor:medium"]))
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("medium", "label:issue"))

    def test_an_unread_label_set_is_full(self):
        resolved, notices = rigor.resolve(self.OPEN, None)
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "fallback"))
        self.assertEqual(codes(notices), [rigor.RIGOR_LABELS_UNREAD])
        self.assertIsNone(resolved["source"]["labels"])

    def test_an_epic_integration_target_is_full(self):
        resolved, notices = rigor.resolve(self.OPEN, labels(["rigor:light"]), epic_integration=True)
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"], notices), ("full", "epic-integration", []))
        self.assertIsNone(resolved["source"]["labels"])  # it reads no labels
        # It wins over a changed block.
        resolved, notices = rigor.resolve(self.OPEN, labels(), epic_integration=True, block_changed=True)
        self.assertEqual((resolved["source"]["chosen_by"], notices), ("epic-integration", []))

    def test_a_changed_block_is_full_whatever_the_labels_and_the_default(self):
        for sets in (labels(), labels(["rigor:light"]), labels([], ["rigor:medium"]), None, []):
            with self.subTest(sets=sets):
                resolved, notices = rigor.resolve(self.OPEN, sets, block_changed=True)
                self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "block-changed"))
                self.assertIn(rigor.RIGOR_BLOCK_CHANGED, codes(notices))
                self.assertTrue(resolved["source"]["block"]["changed"])
                self.assertEqual(resolved["steps"], rigor.STEPS["full"])
        resolved, notices = rigor.resolve(self.OPEN, labels(), block_changed="the fetch failed")
        self.assertEqual(notices, ["%s: the fetch failed; the level is `full`" % rigor.RIGOR_BLOCK_CHANGED])


class ResolvePerIssueTests(unittest.TestCase):
    """Each issue resolves on its own, then the strictest issue wins — the case PR #199's cold read found."""

    def test_an_unlabelled_issue_keeps_a_full_default(self):
        sets = [entry(), entry(["rigor:light"])]
        resolved, _ = rigor.resolve(block(default="full", floor="light"), sets)
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "default"))
        self.assertEqual([(e["level"], e["chosen_by"]) for e in resolved["source"]["labels"]],
                         [("full", "default"), ("light", "label:issue")])

    def test_the_same_two_under_a_light_default(self):
        resolved, _ = rigor.resolve(block(default="light", floor="light"), [entry(), entry(["rigor:light"])])
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("light", "label:issue"))

    def test_the_strictest_issue_wins(self):
        sets = [entry(["rigor:medium"]), entry(["rigor:light"])]
        resolved, _ = rigor.resolve(block(default="light", floor="light"), sets)
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("medium", "label:issue"))

    def test_a_tie_prefers_an_issue_label_then_an_ancestor_label_then_the_default(self):
        open_block = block(default="medium", floor="light")
        resolved, _ = rigor.resolve(open_block, [entry(), entry([], ["rigor:medium"]), entry(["rigor:medium"])])
        self.assertEqual(resolved["source"]["chosen_by"], "label:issue")
        resolved, _ = rigor.resolve(open_block, [entry(), entry([], ["rigor:medium"])])
        self.assertEqual(resolved["source"]["chosen_by"], "label:ancestor")

    def test_one_notice_however_many_issues_carry_the_label(self):
        sets = [entry(["rigor:light"]), entry(["rigor:light"]), entry(["rigor:fast"]), entry(["rigor:fast"])]
        _, notices = rigor.resolve(block(default="full", floor="medium"), sets)
        self.assertEqual(codes(notices), [rigor.RIGOR_LABEL_BELOW_FLOOR, rigor.RIGOR_LABEL_UNKNOWN])

    def test_no_issue_is_the_default(self):
        resolved, _ = rigor.resolve(block(default="light", floor="light"), [])
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"], resolved["source"]["labels"]),
                         ("light", "default", []))


class ReadBlockTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def test_absent(self):
        self.write("CLAUDE.md", "# Guide\n")
        self.assertEqual(rigor.read_block(self.root), (None, []))

    def test_well_formed_with_blank_lines(self):
        self.write("CLAUDE.md", "# Guide\n\n" + BLOCK % "\n- default: medium\n\n- floor: light\n\n")
        found, notices = rigor.read_block(self.root)
        self.assertEqual(notices, [])
        self.assertEqual((found["default"], found["floor"], found["malformed"]), ("medium", "light", False))
        self.assertEqual(found["path"], str(self.root / "CLAUDE.md"))
        self.assertIsNone(found["fault"])

    def test_a_prose_mention_of_the_marker_does_not_count(self):
        self.write("CLAUDE.md", "The `<!-- ctrl-freaq-rigor -->` block sets the level.\n")
        self.assertEqual(rigor.read_block(self.root), (None, []))

    def test_every_malformed_form_is_full_with_a_notice(self):
        # The `fault` each form reads as: only `lines` is one a rewrite of the block's lines repairs.
        forms = {
            "a bad value": (BLOCK % "- default: fast\n", "lines"),
            "a repeated key": (BLOCK % "- default: light\n- default: medium\n", "lines"),
            "a stray line": (BLOCK % "- default: light\nlower it for prototypes\n", "lines"),
            "an unknown key": (BLOCK % "- ceiling: full\n", "lines"),
            "a lone delimiter": ("<!-- ctrl-freaq-rigor -->\n- default: light\n", "fence"),
            "a lone closing delimiter": ("- default: light\n<!-- /ctrl-freaq-rigor -->\n", "fence"),
        }
        for name, (text, fault) in forms.items():
            with self.subTest(form=name):
                self.write("CLAUDE.md", "# Guide\n" + text)
                found, notices = rigor.read_block(self.root)
                self.assertTrue(found["malformed"])
                self.assertEqual(found["fault"], fault)
                self.assertEqual(codes(notices), [rigor.RIGOR_BLOCK_MALFORMED])
                resolved, _ = rigor.resolve(found, labels(["rigor:light"]))
                self.assertEqual(resolved["level"], "full")

    def test_a_block_reached_only_outside_the_repo_is_full(self):
        # An out-and-back symlinked include: the OS read follows it, the self-edit comparison cannot, so a PR
        # could lower its own level unseen (the SEC-1 class). The level is `full`.
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "evil.md").write_text(BLOCK % "- default: light\n- floor: light\n", encoding="utf-8")
        os.symlink("../repo/evil.md", str(repo / "inc.md"))
        (repo / "CLAUDE.md").write_text("# Guide\nSee @inc.md\n", encoding="utf-8")
        found, notices = rigor.read_block(repo)
        self.assertTrue(found["malformed"])
        self.assertEqual(codes(notices), [rigor.RIGOR_BLOCK_MALFORMED])
        self.assertIn("in-repo read does not reach", notices[0])
        self.assertEqual(found["fault"], "unreached")
        # The same file included directly is in the repo: read, and the self-edit comparison sees it.
        (repo / "CLAUDE.md").write_text("# Guide\nSee @evil.md\n", encoding="utf-8")
        found, notices = rigor.read_block(repo)
        self.assertEqual((found["default"], found["malformed"], found["fault"], notices), ("light", False, None, []))

    def test_an_unreadable_candidate_is_full_with_a_notice(self):
        (self.root / "CLAUDE.md").write_bytes(b"\xff\xfe not utf-8\n")
        found, notices = rigor.read_block(self.root)
        self.assertTrue(found["malformed"])
        self.assertEqual(found["fault"], "unreadable")
        self.assertEqual(codes(notices), [rigor.RIGOR_BLOCK_MALFORMED])


class ReadLabelsTests(unittest.TestCase):
    def setUp(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="rigor_labels")
        patcher = mock.patch.dict(os.environ, {"GH_SHIM_FIXTURES": env["GH_SHIM_FIXTURES"], "PATH": env["PATH"]})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_story_counts_its_epic_and_stops_there(self):
        read = rigor.read_labels(REPO, 10)
        self.assertEqual(read["issue"], {"number": 10, "labels": ["bug", "rigor:light"]})
        self.assertEqual(read["ancestors"], [{"number": 5, "labels": ["epic", "rigor:medium"]}])

    def test_a_slice_counts_its_story_and_that_storys_epic(self):
        read = rigor.read_labels(REPO, 11)
        self.assertEqual([a["number"] for a in read["ancestors"]], [10, 5])

    def test_a_failed_or_unreadable_read_is_none(self):
        self.assertIsNone(rigor.read_labels(REPO, 12))
        self.assertIsNone(rigor.read_labels(REPO, 15))

    def test_a_label_page_cut_short_is_unread(self):
        # More labels than the page holds (`totalCount`): a `rigor:full` past it must not read as absent.
        self.assertIsNone(rigor.read_labels(REPO, 16))
        self.assertIsNone(rigor.read_labels(REPO, 17))  # the ancestor's page is the one cut short

    def test_no_sub_issues_omits_the_parent(self):
        self.assertNotIn("parent", rigor.labels_query(REPO, 13, subissues_available=False))
        read = rigor.read_labels(REPO, 13, subissues_available=False)
        self.assertEqual(read, {"issue": {"number": 13, "labels": ["rigor:medium"]}, "ancestors": [], "epic_unread": False})

    def test_no_sub_issues_with_no_fallback_counts_only_the_own_labels(self):
        read = rigor.read_labels(REPO, 13, subissues_available=False)
        self.assertFalse(read["epic_unread"])

    def test_a_legacy_epic_counts_through_the_fallback_parent(self):
        # #20 has no native parent; its epic #30 lists it only on a `## Stories` checklist.
        read = rigor.read_labels(REPO, 20, fallback_parent=30)
        self.assertEqual(read["ancestors"], [{"number": 30, "labels": ["epic", "rigor:full"]}])
        self.assertFalse(read["epic_unread"])
        self.assertIsNone(rigor.read_labels(REPO, 20, fallback_parent=31))  # its read failed
        self.assertEqual(rigor.read_labels(REPO, 20)["ancestors"], [])  # no fallback: nothing more read

    def test_the_fallback_is_not_read_when_the_walk_reaches_an_epic(self):
        # #31's read would fail; the native walk reaches epic #5, so it is never made.
        read = rigor.read_labels(REPO, 10, fallback_parent=31)
        self.assertEqual([a["number"] for a in read["ancestors"]], [5])
        read = rigor.read_labels(REPO, 11, fallback_parent=31)
        self.assertEqual([a["number"] for a in read["ancestors"]], [10, 5])
        # Already an ancestor (a prep passing the native parent): not read twice.
        self.assertEqual([a["number"] for a in rigor.read_labels(REPO, 10, fallback_parent=5)["ancestors"]], [5])

    def test_the_fallback_is_read_past_a_parentless_non_epic_parent(self):
        read = rigor.read_labels(REPO, 23, fallback_parent=30)
        self.assertEqual([a["number"] for a in read["ancestors"]], [24, 30])
        self.assertFalse(read["epic_unread"])

    def test_a_walk_ending_at_a_parentless_story_is_epic_unread(self):
        read = rigor.read_labels(REPO, 21)
        self.assertEqual(([a["number"] for a in read["ancestors"]], read["epic_unread"]), ([22], True))
        read = rigor.read_labels(REPO, 21, fallback_parent=30)
        self.assertEqual(([a["number"] for a in read["ancestors"]], read["epic_unread"]), ([22, 30], False))
        self.assertFalse(rigor.read_labels(REPO, 23)["epic_unread"])  # a parentless bug is not a story


class BuildRigorTests(unittest.TestCase):
    def setUp(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="rigor_labels")
        patcher = mock.patch.dict(os.environ, {"GH_SHIM_FIXTURES": env["GH_SHIM_FIXTURES"], "PATH": env["PATH"]})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "CLAUDE.md").write_text(BLOCK % "- default: light\n- floor: light\n", encoding="utf-8")

    def test_it_resolves_per_issue_and_names_each_set(self):
        resolved, notices = rigor.build_rigor(self.root, REPO, [10])
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"], notices), ("medium", "label:ancestor", []))
        self.assertEqual(resolved["source"]["labels"], [{
            "number": 10, "labels": ["bug", "rigor:light"], "ancestors": [{"number": 5, "labels": ["epic", "rigor:medium"]}],
            "level": "medium", "chosen_by": "label:ancestor",
        }])
        self.assertFalse(resolved["full_rules"])
        resolved, _ = rigor.build_rigor(self.root, REPO, [11])
        self.assertEqual(resolved["level"], "full")  # the slice's epic carries `rigor:full`
        resolved, _ = rigor.build_rigor(self.root, REPO, [14, 10])  # several issues: the strictest wins
        self.assertEqual((resolved["level"], [e["level"] for e in resolved["source"]["labels"]]),
                         ("medium", ["light", "medium"]))
        resolved, notices = rigor.build_rigor(self.root, REPO, [14, 12])  # one unread issue: all unread
        self.assertEqual((resolved["level"], codes(notices)), ("full", [rigor.RIGOR_LABELS_UNREAD]))
        self.assertIsNone(resolved["source"]["labels"])
        resolved, notices = rigor.build_rigor(self.root, REPO, [])  # no issue: only the block applies
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("light", "default"))
        resolved, notices = rigor.build_rigor(self.root, REPO, [12], epic_integration=True)  # reads no labels
        self.assertEqual((resolved["level"], notices), ("full", []))
        self.assertIsNone(resolved["source"]["labels"])  # not read is null, never an empty read

    def test_a_fallback_parent_number_applies_to_the_issue(self):
        resolved, notices = rigor.build_rigor(self.root, REPO, [20], fallback_parent=30)
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"], notices), ("full", "label:ancestor", []))

    def test_a_fallback_parent_mapping_reaches_only_the_issues_it_names(self):
        # The cold read's case: default `full`, the legacy epic #32 carries `rigor:light`, and the PR closes its
        # story #20 and an unlabelled standalone #14. #14 must not inherit the epic's label: it keeps the default.
        (self.root / "CLAUDE.md").write_text(BLOCK % "- default: full\n- floor: light\n", encoding="utf-8")
        resolved, _ = rigor.build_rigor(self.root, REPO, [20, 14], fallback_parent={20: 32})
        self.assertEqual([(e["number"], [a["number"] for a in e["ancestors"]], e["level"])
                          for e in resolved["source"]["labels"]], [(20, [32], "light"), (14, [], "full")])
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "default"))
        # A host with no sub-issues names only the issue no fallback covers.
        _, notices = rigor.build_rigor(self.root, REPO, [13], subissues_available=False, fallback_parent={20: 32})
        self.assertEqual(codes(notices), [rigor.RIGOR_ANCESTORS_UNSUPPORTED])
        self.assertIn("#13", notices[0])

    def test_ancestors_unsupported(self):
        _, notices = rigor.build_rigor(self.root, REPO, [13], subissues_available=False)
        self.assertEqual(codes(notices), [rigor.RIGOR_ANCESTORS_UNSUPPORTED])
        self.assertIn("#13", notices[0])
        _, notices = rigor.build_rigor(self.root, REPO, [21])  # a slice under a parentless story
        self.assertEqual(codes(notices), [rigor.RIGOR_ANCESTORS_UNSUPPORTED])
        self.assertIn("#22", notices[0])
        _, notices = rigor.build_rigor(self.root, REPO, [21], fallback_parent=30)
        self.assertEqual(notices, [])
        _, notices = rigor.build_rigor(self.root, REPO, [], subissues_available=False)  # no issue names none
        self.assertEqual(notices, [])

    def test_full_rules_takes_fulls_steps_and_keeps_the_level(self):
        resolved, _ = rigor.build_rigor(self.root, REPO, [14], full_rules=True)
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"], resolved["full_rules"]),
                         ("light", "default", True))
        self.assertEqual(resolved["steps"], rigor.STEPS["full"])

    def test_a_changed_block_or_a_failed_comparison_is_full(self):
        with mock.patch.object(rigor.sensitive_paths, "rigor_block_changed", return_value=(True, None)) as check:
            resolved, notices = rigor.build_rigor(self.root, REPO, [14], base_ref="main")
        check.assert_called_once_with(self.root, "main")
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "block-changed"))
        self.assertTrue(resolved["source"]["block"]["changed"])
        self.assertEqual(codes(notices), [rigor.RIGOR_BLOCK_CHANGED])
        with mock.patch.object(rigor.sensitive_paths, "rigor_block_changed", return_value=("base", None)):
            resolved, notices = rigor.build_rigor(self.root, REPO, [14], base_ref="main")
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "block-changed"))
        self.assertIn("the base changed it since this checkout's commit", notices[0])
        with mock.patch.object(rigor.sensitive_paths, "rigor_block_changed", return_value=(False, "fetch failed")):
            resolved, notices = rigor.build_rigor(self.root, REPO, [14], base_ref="main")
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("full", "block-changed"))
        self.assertIn("fetch failed", notices[0])
        with mock.patch.object(rigor.sensitive_paths, "rigor_block_changed", return_value=(False, None)):
            resolved, notices = rigor.build_rigor(self.root, REPO, [14], base_ref="main")
        self.assertEqual((resolved["level"], resolved["source"]["block"]["changed"], notices), ("light", False, []))
        with mock.patch.object(rigor.sensitive_paths, "rigor_block_changed") as check:
            rigor.build_rigor(self.root, REPO, [14])  # no base: no comparison
        check.assert_not_called()


class OfferTests(unittest.TestCase):
    """`offer`: what the coordinator may offer (#198) — the levels at or above the floor, the recommendation, the
    first-run card's trigger, and the target's own labels."""

    @staticmethod
    def fact(default="full", floor="full", own=(), ancestor=(), unread=False):
        labels = None if unread else [{"number": 7, "labels": list(own),
                                       "ancestors": [{"number": 3, "labels": list(ancestor)}] if ancestor else [],
                                       "level": default, "chosen_by": "default"}]
        return {"level": default, "full_rules": False, "steps": rigor.STEPS[default],
                "source": {"block": {"present": True, "path": "/repo/CLAUDE.md", "default": default, "floor": floor,
                                     "changed": False}, "labels": labels, "chosen_by": "default"}}

    def test_none_is_none(self):
        self.assertIsNone(rigor.offer(None))

    def test_the_levels_stop_at_the_floor_strictest_first(self):
        self.assertEqual(rigor.offer(self.fact("light", "light"))["levels"], ["full", "medium", "light"])
        self.assertEqual(rigor.offer(self.fact("medium", "medium"))["levels"], ["full", "medium"])
        self.assertEqual(rigor.offer(self.fact())["levels"], ["full"])

    def test_the_first_run_card_needs_a_choice_a_read_and_no_label(self):
        self.assertTrue(rigor.offer(self.fact("light", "light", own=["bug"]))["first_run"])
        self.assertFalse(rigor.offer(self.fact())["first_run"])  # nothing below `full` to choose
        self.assertFalse(rigor.offer(self.fact("light", "light", own=["rigor:full"]))["first_run"])
        self.assertFalse(rigor.offer(self.fact("light", "light", ancestor=["rigor:medium"]))["first_run"])
        self.assertFalse(rigor.offer(self.fact("light", "light", unread=True))["first_run"])  # never on an unread set

    def test_only_the_targets_own_rigor_labels_are_removable(self):
        offer = rigor.offer(self.fact("light", "light", own=["bug", "rigor:medium"], ancestor=["rigor:full"]))
        self.assertEqual(offer["target_labels"], ["rigor:medium"])
        self.assertIsNone(rigor.offer(self.fact("light", "light", unread=True))["target_labels"])  # unread: no write

    def test_only_labels_naming_a_level_are_removable(self):
        # SEC-3 (PR #199): the coordinator's write puts these names in a shell command, so a `rigor:*` label naming
        # no level — text anyone who can create a label wrote — never reaches it; a case variant keeps its name.
        own = ["bug", "rigor:$(curl x|sh)", "rigor:bogus", "Rigor:Light", "rigor:medium"]
        self.assertEqual(rigor.offer(self.fact("light", "light", own=own))["target_labels"],
                         ["Rigor:Light", "rigor:medium"])
        # Any `rigor:*` label still suppresses the first-run card, as before.
        self.assertFalse(rigor.offer(self.fact("light", "light", own=["rigor:bogus"]))["first_run"])

    def test_differs_names_each_offered_levels_steps_off_full(self):
        differs = rigor.offer(self.fact("light", "light"))["differs"]
        self.assertEqual(sorted(differs), ["full", "light", "medium"])
        self.assertEqual(differs["full"], {})
        self.assertEqual(differs["medium"]["review_rounds"], 4)
        self.assertNotIn("dismissal_check", differs["medium"])  # medium keeps full's value
        self.assertEqual(differs["light"]["dismissal_check"], False)

    def test_the_recommendation_is_the_effective_default(self):
        self.assertEqual(rigor.offer(self.fact("medium", "light"))["recommended"], "medium")


if __name__ == "__main__":
    unittest.main()
