"""Unit tests for scripts/board_history.py — the board's `history` fact (#156), its
workspace-opened point and its history cache (#157).

Pure: the query builder, the point builders and the PR pickers take plain dicts, and `read` is
exercised with `pipelib.process.run` patched, so nothing here touches the shim. The reflog read runs
real `git` in a sandbox clone; the cache reads and writes a temp dir.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

import board_history as bh  # noqa: E402
from pipelib.process import CommandResult  # noqa: E402
from tests.support import gitsandbox  # noqa: E402

READY, DRAFT = bh.READY_EVENT, bh.DRAFT_EVENT


def _pr(number=10, head="7-thing", created="2026-02-01T00:00:00Z", merged=None, draft=False, closing=(),
        commits=(), events=(), commit_total=None, event_total=None):
    return {"number": number, "headRefName": head, "isDraft": draft, "createdAt": created, "mergedAt": merged,
            "closingIssuesReferences": {"nodes": [{"number": n} for n in closing]},
            "commits": {"totalCount": len(commits) if commit_total is None else commit_total,
                        "nodes": [{"commit": {"committedDate": c}} for c in commits]},
            "timelineItems": {"totalCount": len(events) if event_total is None else event_total,
                              "nodes": [{"__typename": t, "createdAt": at} for t, at in events]}}


def _pairs(points):
    return [(p["point"], p["at"]) for p in points]


ISSUE = {"createdAt": "2026-01-01T00:00:00Z", "closedAt": None}


class PointsTests(unittest.TestCase):
    def test_a_full_story_is_ordered_by_time_with_the_tie_order(self):
        issue = {"createdAt": "2026-01-01T00:00:00Z", "closedAt": "2026-03-01T00:00:00Z"}
        pr = _pr(created="2026-02-01T00:00:00Z", merged="2026-03-01T00:00:00Z", draft=False,
                 commits=["2026-02-01T00:00:00Z", "2026-01-20T00:00:00Z"],
                 events=[(READY, "2026-02-05T00:00:00Z"), (DRAFT, "2026-02-03T00:00:00Z")])
        points = bh.points(issue, pr, "2026-01-10T00:00:00Z")
        self.assertEqual(_pairs(points), [
            ("eligible", "2026-01-01T00:00:00Z"),
            ("plan-posted", "2026-01-10T00:00:00Z"),
            ("workspace-opened", "2026-01-20T00:00:00Z"),
            ("commit", "2026-01-20T00:00:00Z"),
            ("pr-opened", "2026-02-01T00:00:00Z"),
            ("commit", "2026-02-01T00:00:00Z"),
            ("evaluate", "2026-02-01T00:00:00Z"),
            ("rework", "2026-02-03T00:00:00Z"),
            ("evaluate", "2026-02-05T00:00:00Z"),
            ("merged", "2026-03-01T00:00:00Z"),
            ("closed", "2026-03-01T00:00:00Z"),
        ])
        self.assertEqual({p["point"]: p["source"] for p in points}, {
            "eligible": "issue.createdAt", "plan-posted": "plan-comment.createdAt",
            "workspace-opened": "commit.committedDate", "commit": "commit.committedDate",
            "pr-opened": "pr.createdAt", "evaluate": READY, "rework": DRAFT, "merged": "pr.mergedAt",
            "closed": "issue.closedAt",
        })
        self.assertEqual([p["source"] for p in points if p["point"] == "evaluate"], ["pr.createdAt", READY])
        for point in points:
            self.assertEqual(set(point), {"point", "at", "source"})
            self.assertIn(point["point"], bh.POINTS)

    def test_eligible_takes_the_latest_merged_wait_after_creation(self):
        points = bh.points(ISSUE, None, None, ["2025-12-01T00:00:00Z", "2026-01-05T00:00:00Z", None])
        self.assertEqual(points, [{"point": "eligible", "at": "2026-01-05T00:00:00Z", "source": "start-gate.mergedAt"}])

    def test_an_earlier_or_unmerged_wait_leaves_eligible_at_creation(self):
        points = bh.points(ISSUE, None, None, ["2025-12-01T00:00:00Z", None])
        self.assertEqual(points, [{"point": "eligible", "at": ISSUE["createdAt"], "source": "issue.createdAt"}])

    def test_no_pr_is_eligible_and_plan_posted_only(self):
        self.assertEqual(_pairs(bh.points(ISSUE, None, "2026-01-02T00:00:00Z")),
                         [("eligible", ISSUE["createdAt"]), ("plan-posted", "2026-01-02T00:00:00Z")])

    def test_a_pr_opened_ready_evaluates_at_creation(self):
        points = bh.points(ISSUE, _pr(draft=False), None)
        self.assertEqual([p for p in points if p["point"] == "evaluate"],
                         [{"point": "evaluate", "at": "2026-02-01T00:00:00Z", "source": "pr.createdAt"}])

    def test_a_draft_never_flipped_has_no_evaluate(self):
        self.assertNotIn("evaluate", [p["point"] for p in bh.points(ISSUE, _pr(draft=True), None)])

    def test_opened_ready_then_converted_keeps_its_evaluate_and_gains_a_rework(self):
        pr = _pr(draft=True, events=[(DRAFT, "2026-02-02T00:00:00Z")])
        self.assertEqual([pair for pair in _pairs(bh.points(ISSUE, pr, None)) if pair[0] in ("evaluate", "rework")],
                         [("evaluate", "2026-02-01T00:00:00Z"), ("rework", "2026-02-02T00:00:00Z")])

    def test_opened_ready_converted_and_re_readied_evaluates_around_the_rework(self):
        pr = _pr(draft=False, events=[(DRAFT, "2026-02-02T00:00:00Z"), (READY, "2026-02-04T00:00:00Z")])
        self.assertEqual([pair for pair in _pairs(bh.points(ISSUE, pr, None)) if pair[0] in ("evaluate", "rework")],
                         [("evaluate", "2026-02-01T00:00:00Z"), ("rework", "2026-02-02T00:00:00Z"),
                          ("evaluate", "2026-02-04T00:00:00Z")])

    def test_opened_as_draft_evaluates_only_at_its_ready_event(self):
        pr = _pr(draft=False, events=[(READY, "2026-02-03T00:00:00Z")])
        self.assertEqual([p["at"] for p in bh.points(ISSUE, pr, None) if p["point"] == "evaluate"],
                         ["2026-02-03T00:00:00Z"])

    def test_a_missing_issue_node_still_builds_from_the_pr(self):
        """`points` tolerates a missing issue node (no `eligible`, no `closed`); the coordinator itself
        never draws such a line — an unresolved issue is an empty line there."""
        self.assertEqual(_pairs(bh.points(None, _pr(), "2026-01-02T00:00:00Z")),
                         [("plan-posted", "2026-01-02T00:00:00Z"), ("pr-opened", "2026-02-01T00:00:00Z"),
                          ("evaluate", "2026-02-01T00:00:00Z")])
        self.assertEqual(_pairs(bh.points(None, None, None)), [])


class WorkspaceOpenedTests(unittest.TestCase):
    """#157: `workspace-opened` from the reflog, else the PR's earliest commit, else nothing."""

    def test_the_reflog_time_ties_after_plan_posted(self):
        points = bh.points(ISSUE, None, "2026-01-10T00:00:00Z", workspace_opened="2026-01-10T00:00:00Z")
        self.assertEqual([(p["point"], p["source"]) for p in points],
                         [("eligible", "issue.createdAt"), ("plan-posted", "plan-comment.createdAt"),
                          ("workspace-opened", "reflog")])
        self.assertLess(bh.POINTS.index("plan-posted"), bh.POINTS.index("workspace-opened"))
        self.assertLess(bh.POINTS.index("workspace-opened"), bh.POINTS.index("pr-opened"))

    def test_a_reflog_time_before_the_first_commit_wins(self):
        pr = _pr(commits=["2026-01-20T00:00:00Z"])
        opened = [p for p in bh.points(ISSUE, pr, None, workspace_opened="2026-01-15T00:00:00Z")
                  if p["point"] == "workspace-opened"]
        self.assertEqual(opened, [{"point": "workspace-opened", "at": "2026-01-15T00:00:00Z", "source": "reflog"}])

    def test_no_reflog_time_falls_back_to_the_earliest_commit(self):
        pr = _pr(commits=["2026-02-01T00:00:00Z", "2026-01-20T00:00:00Z"])
        opened = [p for p in bh.points(ISSUE, pr, None) if p["point"] == "workspace-opened"]
        self.assertEqual(opened, [{"point": "workspace-opened", "at": "2026-01-20T00:00:00Z",
                                   "source": "commit.committedDate"}])

    def test_a_reflog_time_after_the_first_commit_falls_back_to_it(self):
        """A local branch created after the work began elsewhere dates this checkout, not the
        workspace the work started in."""
        pr = _pr(commits=["2026-01-20T00:00:00Z"])
        opened = [p for p in bh.points(ISSUE, pr, None, workspace_opened="2026-05-01T00:00:00Z")
                  if p["point"] == "workspace-opened"]
        self.assertEqual(opened, [{"point": "workspace-opened", "at": "2026-01-20T00:00:00Z",
                                   "source": "commit.committedDate"}])

    def test_neither_is_no_point(self):
        for pr in (None, _pr()):
            with self.subTest(pr=bool(pr)):
                self.assertNotIn("workspace-opened", [p["point"] for p in bh.points(ISSUE, pr, None)])


class BranchCreatedAtTests(unittest.TestCase):
    def _run(self, stdout, returncode=0):
        result = CommandResult(returncode=returncode, stdout=stdout, stderr="")
        with mock.patch.object(bh.process, "run", return_value=result) as run:
            value = bh.branch_created_at("/repo", "7-thing")
        run.assert_called_once_with(["git", "reflog", "show", "--date=iso-strict", "--format=%gd%x09%gs",
                                     "refs/heads/7-thing"], cwd="/repo")
        return value

    def test_a_sandbox_branchs_creation_entry_is_utc(self):
        origin = gitsandbox.mk_origin()
        self.addCleanup(origin.cleanup)
        clone = gitsandbox.mk_clone(origin)
        self.addCleanup(clone.cleanup)
        subprocess.run(["git", "branch", "7-thing"], cwd=str(clone.path), check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-q", "7-thing"], cwd=str(clone.path), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "work"], cwd=str(clone.path), check=True,
                       capture_output=True)
        created = bh.branch_created_at(str(clone.path), "7-thing")
        self.assertRegex(created or "", r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertIsNone(bh.branch_created_at(str(clone.path), "main"), "a cloned branch has no creation entry")
        self.assertIsNone(bh.branch_created_at(str(clone.path), "no-such-branch"))

    def test_the_oldest_creation_entry_is_normalised_to_utc(self):
        stdout = ("7-thing@{2026-10-28T12:00:00+11:00}\tcommit: work\n"
                  "7-thing@{2026-10-28T10:48:55+11:00}\tbranch: Created from origin/7-thing\n"
                  "7-thing@{2026-10-01T09:00:00+00:00}\tbranch: Created from main\n")
        self.assertEqual(self._run(stdout), "2026-10-01T09:00:00Z")
        self.assertEqual(self._run("7-thing@{2026-10-28T10:48:55+11:00}\tbranch: Created from HEAD\n"),
                         "2026-10-27T23:48:55Z")

    def test_no_creation_entry_or_a_failed_call_is_none(self):
        self.assertIsNone(self._run("7-thing@{2026-10-28T12:00:00+11:00}\tcommit (initial): work\n"))
        self.assertIsNone(self._run("", returncode=128))
        self.assertIsNone(self._run("7-thing@{not a date}\tbranch: Created from main\n"))
        self.assertIsNone(bh.branch_created_at("/repo", None))


class CacheTests(unittest.TestCase):
    LINE = [{"point": "eligible", "at": "2026-01-01T00:00:00Z", "source": "issue.createdAt"},
            {"point": "closed", "at": "2026-03-01T00:00:00Z", "source": "issue.closedAt"}]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "history-cache.json"

    def test_save_then_load_round_trips(self):
        bh.save_cache(self.path, "octo/widgets", {151: self.LINE})
        self.assertEqual(bh.load_cache(self.path, "octo/widgets"), {151: self.LINE})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["v"], 1)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["history-cache.json"], "no temp file left")

    def test_a_cold_start_is_empty(self):
        self.assertEqual(bh.load_cache(self.path, "octo/widgets"), {}, "missing")
        bh.save_cache(self.path, "octo/widgets", {151: self.LINE})
        self.assertEqual(bh.load_cache(self.path, "octo/other"), {}, "another repo")
        for text in ("not json", "[]", json.dumps({"v": 2, "repo": "octo/widgets", "lines": {"151": self.LINE}}),
                     json.dumps({"v": 1, "repo": "octo/widgets", "lines": {"x": self.LINE}}),
                     json.dumps({"v": 1, "repo": "octo/widgets", "lines": {"151": [{"point": "teleported"}]}})):
            with self.subTest(text=text[:40]):
                self.path.write_text(text, encoding="utf-8")
                self.assertEqual(bh.load_cache(self.path, "octo/widgets"), {})

    def test_a_failed_write_raises_and_leaves_no_temp_file(self):
        with mock.patch.object(bh.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                bh.save_cache(self.path, "octo/widgets", {151: self.LINE})
        self.assertEqual(list(self.path.parent.iterdir()), [])


class LineTruncatedTests(unittest.TestCase):
    """#157: whether one story's own line misses points — what the history cache keeps out."""

    def test_each_clause_cuts_only_its_own_line(self):
        long_pr = _pr(commit_total=101)
        self.assertTrue(bh.line_truncated(ISSUE, long_pr))
        self.assertFalse(bh.line_truncated(ISSUE, _pr()), "a sibling's long PR is not this line's")
        self.assertTrue(bh.line_truncated(ISSUE, _pr(event_total=101)))
        unmarked = {"comments": {"totalCount": 101, "nodes": [{"createdAt": "x", "body": "hi"}]}}
        marked = {"comments": {"totalCount": 101, "nodes": [{"createdAt": "x", "body": "<!-- implementation-plan:v1 -->"}]}}
        self.assertTrue(bh.line_truncated(unmarked, None))
        self.assertFalse(bh.line_truncated(marked, None))

    def test_a_long_stories_connection_cuts_only_a_line_with_no_pr(self):
        stories = {"totalCount": 101, "nodes": []}
        self.assertTrue(bh.line_truncated(ISSUE, None, stories))
        self.assertFalse(bh.line_truncated(ISSUE, _pr(), stories))
        self.assertFalse(bh.line_truncated(ISSUE, None, None), "the cached path has no stories connection")


class IntegrationTests(unittest.TestCase):
    def test_the_integration_line_carries_no_commits(self):
        pr = _pr(head="epic/5-x", draft=True, merged="2026-03-01T00:00:00Z",
                 commits=["2026-02-02T00:00:00Z"], events=[(READY, "2026-02-20T00:00:00Z")])
        self.assertEqual([p["point"] for p in bh.integration_points(pr)], ["pr-opened", "evaluate", "merged"])

    def test_no_pr_is_an_empty_line(self):
        self.assertEqual(bh.integration_points(None), [])
        self.assertEqual(bh.integration_points(bh.integration_pr([])), [])

    def test_the_merged_node_wins_else_the_latest_open_one(self):
        old, new = _pr(1, created="2026-01-01T00:00:00Z"), _pr(2, created="2026-02-01T00:00:00Z")
        merged = _pr(3, created="2025-12-01T00:00:00Z", merged="2026-01-15T00:00:00Z")
        self.assertEqual(bh.integration_pr([old, merged, new])["number"], 3)
        self.assertEqual(bh.integration_pr([old, new])["number"], 2)


class StoryPrsTests(unittest.TestCase):
    def test_the_head_branch_names_the_story_when_no_closing_reference_does(self):
        prs = [_pr(1, head="7-thing"), _pr(2, head="8-other")]
        self.assertEqual({n: (pr or {}).get("number") for n, pr in bh.story_prs(prs, [7, 8, 9]).items()},
                         {7: 1, 8: 2, 9: None})

    def test_every_story_number_is_a_key(self):
        self.assertEqual(bh.story_prs([], [9, 7]), {7: None, 9: None})
        self.assertEqual(bh.story_prs(None, [7]), {7: None})

    def test_a_null_node_is_skipped(self):
        self.assertEqual(bh.story_prs([None, _pr(1, head="7-thing")], [7])[7]["number"], 1)

    def test_a_closing_reference_names_the_story(self):
        self.assertEqual(bh.story_prs([_pr(1, head="odd-name", closing=[7])], [7])[7]["number"], 1)

    def test_a_pr_maps_to_one_story_only(self):
        """A PR on head `8-x` that closes #7 is #7's, as the evaluator's residue maps it — never also
        #8's by its head branch (R1.13)."""
        prs = bh.story_prs([_pr(1, head="8-x", closing=[7])], [7, 8])
        self.assertEqual(prs[7]["number"], 1)
        self.assertIsNone(prs[8])

    def test_the_merged_pr_wins_else_the_latest_open_one(self):
        open_old = _pr(1, head="7-a", created="2026-01-01T00:00:00Z")
        open_new = _pr(2, head="7-a-v2", created="2026-02-01T00:00:00Z")
        merged = _pr(3, head="7-a-v3", created="2025-12-01T00:00:00Z", merged="2026-01-10T00:00:00Z")
        self.assertEqual(bh.story_prs([open_old, merged, open_new], [7])[7]["number"], 3)
        self.assertEqual(bh.story_prs([open_old, open_new], [7])[7]["number"], 2)


class PlanCreatedAtTests(unittest.TestCase):
    def test_the_earliest_main_marker_comment_and_never_a_part(self):
        comments = [
            {"createdAt": "2026-01-01T00:00:00Z", "body": "<!-- implementation-plan-part:v1:build -->\n## Changes"},
            {"createdAt": "2026-01-03T00:00:00Z", "body": "<!-- implementation-plan:v1 -->\nlater"},
            {"createdAt": "2026-01-02T00:00:00Z", "body": "<!-- implementation-plan:v1 -->\nfirst"},
            {"createdAt": "2025-12-01T00:00:00Z", "body": "a remark"},
        ]
        self.assertEqual(bh.plan_created_at(comments), "2026-01-02T00:00:00Z")
        self.assertIsNone(bh.plan_created_at([]))
        self.assertIsNone(bh.plan_created_at(None))


class BuildQueryTests(unittest.TestCase):
    KW = dict(comment_issues=[3], pr_numbers=[12, 9], story_base="epic/1-x", integration_head="epic/1-x",
              default_branch="main")

    def test_the_string_is_deterministic_whatever_the_input_order(self):
        first = bh.build_query("octo/widgets", [3, 1, 2], **self.KW)
        again = bh.build_query("octo/widgets", [2, 3, 1, 3], **dict(self.KW, pr_numbers=[9, 12]))
        self.assertEqual(first, again)
        self.assertLess(first.index("i1:"), first.index("i2:"))
        self.assertLess(first.index("p9:"), first.index("p12:"))

    def test_it_names_every_field_the_builders_read(self):
        query = bh.build_query("octo/widgets", [3, 1], **self.KW)
        self.assertTrue(query.startswith('query { repository(owner: "octo", name: "widgets") { '))
        for fragment in (
            "i1: issue(number: 1) { createdAt closedAt }",
            "i3: issue(number: 3) { createdAt closedAt comments(first: 100) { totalCount nodes { createdAt body } } }",
            "p9: pullRequest(number: 9) { number headRefName isDraft createdAt mergedAt ",
            "closingIssuesReferences(first: 10) { nodes { number } }",
            "commits(first: 100) { totalCount nodes { commit { committedDate } } }",
            "timelineItems(first: 100, itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT]) "
            "{ totalCount nodes { __typename ... on ReadyForReviewEvent { createdAt } "
            "... on ConvertToDraftEvent { createdAt } } }",
            'stories: pullRequests(baseRefName: "epic/1-x", first: 100, states: [OPEN, MERGED]) { totalCount nodes {',
            'integration: pullRequests(headRefName: "epic/1-x", baseRefName: "main", first: 5, '
            'states: [OPEN, MERGED]) { nodes {',
        ):
            self.assertIn(fragment, query)

    def test_the_cached_shape_names_prs_and_no_stories_connection(self):
        """#157: with every closed story cached, the query names the open stories and their PRs."""
        query = bh.build_query("octo/widgets", [152], comment_issues=[152], pr_numbers=[160, 159],
                               integration_head="epic/1-x", default_branch="main")
        self.assertEqual(query, bh.build_query("octo/widgets", [152], comment_issues=[152], pr_numbers=[159, 160],
                                               integration_head="epic/1-x", default_branch="main"))
        self.assertNotIn("stories:", query)
        self.assertNotIn("i151:", query)
        self.assertEqual(re.findall(r"p\d+:", query), ["p159:", "p160:"])
        self.assertIn("integration:", query)

    def test_no_branch_asks_for_neither_connection(self):
        query = bh.build_query("octo/widgets", [1])
        self.assertNotIn("stories:", query)
        self.assertNotIn("integration:", query)
        self.assertNotIn("comments(", query)


class ReadTests(unittest.TestCase):
    def _read(self, stdout, returncode=0):
        result = CommandResult(returncode=returncode, stdout=stdout, stderr="")
        with mock.patch.object(bh.process, "run", return_value=result) as run:
            value = bh.read("octo/widgets", "query { }")
        run.assert_called_once_with(["gh", "api", "graphql", "-f", "query=query { }"], cwd=None)
        return value

    def test_a_non_zero_exit_still_reads_the_data_it_carries(self):
        body = {"data": {"repository": {"i1": {"createdAt": "x", "closedAt": None}, "i2": None}},
                "errors": [{"message": "Could not resolve to an Issue with the number of 2."}]}
        self.assertEqual(self._read(json.dumps(body), returncode=1), body["data"]["repository"])

    def test_an_empty_repository_is_present_not_missing(self):
        self.assertEqual(self._read('{"data": {"repository": {}}}'), {})

    def test_no_repository_is_none(self):
        self.assertIsNone(self._read('{"errors": [{"message": "boom"}]}', returncode=1))
        self.assertIsNone(self._read("", returncode=1))
        self.assertIsNone(self._read("not json", returncode=1))
        self.assertIsNone(self._read('{"data": {"repository": null}}', returncode=1))


class TruncatedTests(unittest.TestCase):
    """A gap in a drawn line, never a connection the board does not draw (R1.12)."""

    PLAN = {"createdAt": "2026-01-02T00:00:00Z", "body": "<!-- implementation-plan:v1 -->\nThe plan."}

    def test_a_drawn_line_past_its_page_is_truncated(self):
        self.assertTrue(bh.truncated({"stories": {"totalCount": 101, "nodes": []}}, []))
        self.assertTrue(bh.truncated({}, [_pr(commit_total=101)]))
        self.assertTrue(bh.truncated({}, [_pr(event_total=101)]))
        self.assertTrue(bh.truncated({}, [], _pr(event_total=101)))
        self.assertTrue(bh.truncated({"i1": {"comments": {"totalCount": 140, "nodes": []}}}, []))

    def test_a_full_page_is_not_truncated(self):
        self.assertFalse(bh.truncated({"stories": {"totalCount": 100, "nodes": []}},
                                      [_pr(commit_total=100, event_total=100)], _pr(event_total=100)))
        self.assertFalse(bh.truncated({}, []))
        self.assertFalse(bh.truncated(None, None))

    def test_the_integration_prs_commits_never_count(self):
        self.assertFalse(bh.truncated({}, [], _pr(commit_total=250)))

    def test_an_undrawn_story_base_pr_never_counts(self):
        """A superseded or unmapped PR rides in `stories.nodes` but is not in `drawn_prs`."""
        long_pr = _pr(head="odd-name", commit_total=250, event_total=250)
        self.assertFalse(bh.truncated({"stories": {"totalCount": 2, "nodes": [long_pr, _pr()]}}, [_pr()]))

    def test_long_comments_count_only_without_the_marker_in_their_page(self):
        self.assertFalse(bh.truncated({"i1": {"comments": {"totalCount": 140, "nodes": [self.PLAN]}}}, []))
        part = {"createdAt": "2026-01-01T00:00:00Z", "body": "<!-- implementation-plan-part:v1:build -->"}
        self.assertTrue(bh.truncated({"i1": {"comments": {"totalCount": 140, "nodes": [part]}}}, []))

    def test_only_an_issue_alias_is_read_for_comments(self):
        long_page = {"comments": {"totalCount": 140, "nodes": []}}
        self.assertFalse(bh.truncated({"integration": dict(long_page, nodes=[])}, []))
        self.assertFalse(bh.truncated({"p1": dict(long_page)}, []))
        self.assertTrue(bh.truncated({"i12": dict(long_page)}, []))


if __name__ == "__main__":
    unittest.main()
