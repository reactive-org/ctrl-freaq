"""Offline tests for `scripts/branching.py`'s legacy parent-epic tier (`search_parent_epic`), and for
`delivered_paths` (#149), against a real git sandbox with the `gh` listing mocked.

The native tier is covered in tests/test_prep_planner.py (`ParentEpicNativeShortCircuitTests`). Here
`branching.process.run` is mocked to return the `gh issue list --label epic` candidate set, so the
membership rule is tested on epic bodies directly — the 10 sandbox fixtures that run the search all
list the story in `## Stories`, so none of them exercises a mention-only epic.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import branching  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib import process  # noqa: E402
from tests.support import gitsandbox  # noqa: E402


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



def _git(args, cwd):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), result.stderr))
    return result.stdout.strip()


class DeliveredPathsTests(unittest.TestCase):
    """#149: `delivered_paths` — the files the PRs merged into an epic branch changed, read by git from
    each PR's merge commit. A real git sandbox; only the `gh pr list` listing is shimmed."""

    BRANCH = "epic/1-sandbox"

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        _git(["checkout", "-q", "-b", self.BRANCH], self.root)
        self.squash_a = self._commit({"src/a.py": "a", "docs/caf\u00e9.md": "c"}, "story #2 (#11)")
        self.squash_b = self._commit({"src/b.py": "b"}, "story #3 (#12)")
        self._commit({"direct/edit.py": "d"}, "a direct commit on the epic branch")
        _git(["push", "-q", "origin", self.BRANCH], self.root)

    def _commit(self, files, message):
        for rel, text in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
        _git(["add", "-A"], self.root)
        _git(["commit", "-q", "-m", message], self.root)
        return _git(["rev-parse", "HEAD"], self.root)

    def _delivered(self, listing, returncode=0):
        """Run the core with `gh` answering `listing` (a list, or raw text) and git real."""
        real_run = process.run
        calls = []

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            calls.append(list(argv))
            if argv[0] == "gh":
                stdout = listing if isinstance(listing, str) else json.dumps(listing)
                return process.CommandResult(returncode, stdout, "")
            return real_run(argv, cwd=cwd, env=env, input_text=input_text, check=check)

        with mock.patch.object(branching.process, "run", side_effect=fake_run):
            fact, notice = branching.delivered_paths(self.root, "octo/widgets", self.BRANCH)
        return fact, notice, calls

    def test_only_the_merged_prs_files_are_delivered(self):
        fact, notice, calls = self._delivered([
            {"number": 12, "mergeCommit": {"oid": self.squash_b}},
            {"number": 11, "mergeCommit": {"oid": self.squash_a}},
        ])
        self.assertIsNone(notice)
        self.assertEqual(fact["paths"], ["docs/caf\u00e9.md", "src/a.py", "src/b.py"])
        self.assertNotIn("direct/edit.py", fact["paths"])
        self.assertEqual([pr["number"] for pr in fact["prs"]], [11, 12])
        self.assertEqual(fact["prs"][0]["merge_commit"], self.squash_a)
        self.assertEqual((fact["unreadable"], fact["at_limit"]), ([], False))
        self.assertEqual(
            calls[0],
            ["gh", "pr", "list", "--repo", "octo/widgets", "--base", self.BRANCH, "--state", "merged",
             "--limit", "500", "--json", "number,mergeCommit"],
        )
        # Every commit was local: no fetch.
        self.assertFalse([c for c in calls if c[:2] == ["git", "fetch"]])

    def test_a_non_ascii_path_is_spelled_as_changed_paths_spells_it(self):
        import sensitive_paths

        fact, _, _ = self._delivered([{"number": 11, "mergeCommit": {"oid": self.squash_a}}])
        changed, _, error = sensitive_paths.changed_paths(self.root, "main")
        self.assertIsNone(error)
        self.assertIn("docs/caf\u00e9.md", changed)
        self.assertIn("docs/caf\u00e9.md", fact["paths"])

    def test_an_absent_or_unknown_merge_commit_is_unreadable_after_one_fetch(self):
        fact, notice, calls = self._delivered([
            {"number": 11, "mergeCommit": {"oid": self.squash_a}},
            {"number": 13, "mergeCommit": None},
            {"number": 14, "mergeCommit": {"oid": "0" * 40}},
            {"number": 15, "mergeCommit": {"oid": "f" * 40}},
            {"number": 16, "mergeCommit": {"oid": "--output=/tmp/x"}},
        ])
        self.assertIsNone(notice)
        self.assertEqual(fact["unreadable"], [13, 14, 15, 16])
        # A non-hex oid never reaches git, where a leading `-` would read as an option.
        self.assertFalse([c for c in calls if any("--output" in arg for arg in c)])
        self.assertEqual(fact["paths"], ["docs/caf\u00e9.md", "src/a.py"])
        self.assertEqual(
            [c for c in calls if c[:2] == ["git", "fetch"]], [["git", "fetch", "--quiet", "origin", self.BRANCH]]
        )

    def test_a_merge_commit_only_on_origin_is_fetched(self):
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        _git(["fetch", "-q", "origin", self.BRANCH], other.path)
        _git(["checkout", "-q", "-b", self.BRANCH, "origin/" + self.BRANCH], other.path)
        (other.path / "src").mkdir(exist_ok=True)
        (other.path / "src" / "late.py").write_text("late\n", encoding="utf-8")
        _git(["add", "-A"], other.path)
        _git(["commit", "-q", "-m", "story #4 (#16)"], other.path)
        late = _git(["rev-parse", "HEAD"], other.path)
        _git(["push", "-q", "origin", self.BRANCH], other.path)
        fact, notice, _ = self._delivered([{"number": 16, "mergeCommit": {"oid": late}}])
        self.assertIsNone(notice)
        self.assertEqual((fact["paths"], fact["unreadable"]), (["src/late.py"], []))

    def test_a_failed_or_unparseable_listing_is_the_notice(self):
        for listing, returncode in (([], 1), ("not json", 0), ('{"number": 1}', 0)):
            fact, notice, _ = self._delivered(listing, returncode=returncode)
            self.assertIsNone(fact, listing)
            self.assertEqual(notice, branching.EPIC_DELIVERED_UNAVAILABLE, listing)

    def test_the_list_limit_sets_at_limit(self):
        listing = [{"number": n, "mergeCommit": None} for n in range(1, branching.DELIVERED_PR_LIST_LIMIT + 1)]
        fact, notice, _ = self._delivered(listing)
        self.assertIsNone(notice)
        self.assertIs(fact["at_limit"], True)
        self.assertEqual(len(fact["unreadable"]), branching.DELIVERED_PR_LIST_LIMIT)


if __name__ == "__main__":
    unittest.main()
