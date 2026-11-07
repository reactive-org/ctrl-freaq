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
    return {"issues": [list(issue)], "ancestors": [list(ancestors)] if ancestors else []}


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
                ["security", "sensitive", "judgment", "out-of-plan", "routine", "plan"],
                ["security", "out-of-plan", "routine", "plan"],
                ["security"],
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

    def test_the_resolved_steps_are_a_copy(self):
        resolved, _ = rigor.resolve(None, labels())
        resolved["steps"]["thread_sources"].append("x")
        self.assertNotIn("x", rigor.STEPS["full"]["thread_sources"])


class ResolveBlockTests(unittest.TestCase):
    def test_no_block_is_full(self):
        resolved, notices = rigor.resolve(None, labels())
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"], notices), ("full", "default", []))
        self.assertEqual(resolved["source"]["block"], {"present": False, "path": None, "default": "full", "floor": "full"})
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

    def test_a_prose_mention_of_the_marker_does_not_count(self):
        self.write("CLAUDE.md", "The `<!-- ctrl-freaq-rigor -->` block sets the level.\n")
        self.assertEqual(rigor.read_block(self.root), (None, []))

    def test_every_malformed_form_is_full_with_a_notice(self):
        forms = {
            "a bad value": BLOCK % "- default: fast\n",
            "a repeated key": BLOCK % "- default: light\n- default: medium\n",
            "a stray line": BLOCK % "- default: light\nlower it for prototypes\n",
            "an unknown key": BLOCK % "- ceiling: full\n",
            "a lone delimiter": "<!-- ctrl-freaq-rigor -->\n- default: light\n",
            "a lone closing delimiter": "- default: light\n<!-- /ctrl-freaq-rigor -->\n",
        }
        for name, text in forms.items():
            with self.subTest(form=name):
                self.write("CLAUDE.md", "# Guide\n" + text)
                found, notices = rigor.read_block(self.root)
                self.assertTrue(found["malformed"])
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
        # The same file included directly is in the repo: read, and the self-edit comparison sees it.
        (repo / "CLAUDE.md").write_text("# Guide\nSee @evil.md\n", encoding="utf-8")
        found, notices = rigor.read_block(repo)
        self.assertEqual((found["default"], found["malformed"], notices), ("light", False, []))

    def test_an_unreadable_candidate_is_full_with_a_notice(self):
        (self.root / "CLAUDE.md").write_bytes(b"\xff\xfe not utf-8\n")
        found, notices = rigor.read_block(self.root)
        self.assertTrue(found["malformed"])
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
        self.assertEqual(read, {"issue": {"number": 13, "labels": ["rigor:medium"]}, "ancestors": []})

    def test_build_rigor_resolves_the_strictest_and_names_each_set(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "CLAUDE.md").write_text(BLOCK % "- default: light\n- floor: light\n", encoding="utf-8")
        resolved, notices = rigor.build_rigor(root, REPO, [10])
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"], notices), ("medium", "label:ancestor", []))
        self.assertEqual(resolved["source"]["labels"]["issues"], [{"number": 10, "labels": ["bug", "rigor:light"]}])
        resolved, _ = rigor.build_rigor(root, REPO, [11])
        self.assertEqual(resolved["level"], "full")  # the slice's epic carries `rigor:full`
        resolved, _ = rigor.build_rigor(root, REPO, [14, 10])  # several issues: the strictest wins
        self.assertEqual(resolved["level"], "medium")
        resolved, notices = rigor.build_rigor(root, REPO, [14, 12])  # one unread issue: all unread
        self.assertEqual((resolved["level"], codes(notices)), ("full", [rigor.RIGOR_LABELS_UNREAD]))
        self.assertIsNone(resolved["source"]["labels"])
        resolved, notices = rigor.build_rigor(root, REPO, [])  # no issue: only the block applies
        self.assertEqual((resolved["level"], resolved["source"]["chosen_by"]), ("light", "default"))
        resolved, notices = rigor.build_rigor(root, REPO, [12], epic_integration=True)  # reads no labels
        self.assertEqual((resolved["level"], notices), ("full", []))
        self.assertIsNone(resolved["source"]["labels"])  # not read is null, never an empty read


if __name__ == "__main__":
    unittest.main()
