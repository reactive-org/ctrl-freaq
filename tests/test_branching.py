"""Offline tests for `scripts/branching.py`'s legacy parent-epic tier (`search_parent_epic`), with the `gh`
listing mocked.

The native tier is covered in tests/test_prep_planner.py (`ParentEpicNativeShortCircuitTests`). Here
`branching.process.run` is mocked to return the `gh issue list --label epic` candidate set, so the
membership rule is tested on epic bodies directly — the 10 sandbox fixtures that run the search all
list the story in `## Stories`, so none of them exercises a mention-only epic.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import branching  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib import process  # noqa: E402


def _epic(number, body, state="OPEN"):
    return {"number": number, "title": "Epic %d" % number, "state": state, "body": body}


LISTS_12 = "## Goal\nShip it.\n\n## Stories\n- [ ] #12 — the story\n- [x] #11 — done\n"
MENTIONS_12 = "## Goal\nUnlike #12, this epic rebuilds the importer.\n\n## Stories\n- [ ] #40 — x\n"


class SearchParentEpicLegacyTierTests(unittest.TestCase):
    def _search(self, epics, story=12):
        result = process.CommandResult(0, json.dumps(epics), "")
        with mock.patch.object(branching.process, "run", return_value=result) as run:
            matches, decision = branching.search_parent_epic("octo/widgets", story)
        self.assertEqual(run.call_count, 1)
        return matches, decision

    def test_an_epic_that_only_mentions_the_story_is_not_its_parent(self):
        matches, decision = self._search([_epic(100, MENTIONS_12)])
        self.assertIsNone(decision)
        self.assertEqual(matches, [])

    def test_an_epic_listing_the_story_in_its_checklist_is_its_parent(self):
        matches, decision = self._search([_epic(100, LISTS_12)])
        self.assertIsNone(decision)
        self.assertEqual([m["number"] for m in matches], [100])
        # Same result shape as before: the filter still strips `body` and adds `closes_issue`.
        self.assertNotIn("body", matches[0])
        self.assertIn("closes_issue", matches[0])

    def test_a_mentioning_epic_beside_the_real_parent_is_not_ambiguous(self):
        matches, decision = self._search([_epic(100, LISTS_12), _epic(101, MENTIONS_12)])
        self.assertIsNone(decision)
        self.assertEqual([m["number"] for m in matches], [100])

    def test_two_epics_listing_the_story_are_ambiguous(self):
        matches, decision = self._search([_epic(100, LISTS_12), _epic(101, LISTS_12)])
        self.assertIsNone(matches)
        self.assertEqual(decision["code"], "AMBIGUOUS")
        self.assertIn("`## Stories` checklist", decision["summary"])
        self.assertEqual([c["number"] for c in decision["context"]["candidates"]], [100, 101])

    def test_a_title_less_ticked_bullet_still_lists_the_story(self):
        matches, _ = self._search([_epic(100, "## Stories\n- [x] #12\n")])
        self.assertEqual([m["number"] for m in matches], [100])

    def test_a_checklist_under_another_heading_is_not_membership(self):
        matches, _ = self._search([_epic(100, "## Related\n- [ ] #12 — tracked elsewhere\n")])
        self.assertEqual(matches, [])

    def test_an_annotated_stories_heading_still_lists_the_story(self):
        matches, _ = self._search([_epic(100, "## Stories (wave 1)\n- [ ] #12 — the story\n")])
        self.assertEqual([m["number"] for m in matches], [100])

    def test_neighbouring_numbers_are_not_membership(self):
        matches, _ = self._search([_epic(100, "## Stories\n- [ ] #120 — x\n- [ ] #1 — y\n")])
        self.assertEqual(matches, [])


class IssueForBranchTests(unittest.TestCase):
    def test_reads_both_grammars_and_nothing_else(self):
        self.assertEqual(branching.issue_for_branch("88-fix-widget"), 88)
        self.assertEqual(branching.issue_for_branch("88-fix-widget-v2"), 88)
        self.assertEqual(branching.issue_for_branch("epic/42-journal"), 42)
        for branch in ("main", "fix/88-widget", "88", "", None):
            self.assertIsNone(branching.issue_for_branch(branch), branch)


class ResolveParentEpicBranchTests(unittest.TestCase):
    """The shared entry prep_resolver and prep_workspace_open reach the legacy tier through: a
    mention-only epic yields no parent, and so no epic-branch discovery and no epic base."""

    def test_a_mention_only_epic_gives_no_parent_and_no_branch_lookup(self):
        calls = []

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            calls.append(list(argv))
            return process.CommandResult(0, json.dumps([_epic(100, MENTIONS_12)]), "")

        with mock.patch.object(branching.process, "run", side_effect=fake_run):
            facts, notices, decision = branching.resolve_parent_epic_branch(
                ".", "octo/widgets", 12, "story", None
            )
        self.assertIsNone(decision)
        self.assertEqual(notices, [])
        self.assertEqual(facts, {"parent_epic": None, "branch_facts": None, "parent_kind": None})
        # Only the candidate search ran: no `git ls-remote` for an epic branch to base on.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["gh", "issue"])


if __name__ == "__main__":
    unittest.main()
