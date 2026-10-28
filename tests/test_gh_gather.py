"""Unit tests for scripts/gh_gather.py — the GATHER_ISSUE envelope port (S21) of v1's
gh-gather.sh.

Drives the real script as a subprocess (matching this codebase's harness convention: real
subprocess + the `gh` shim, never a mocked pipelib) via `tests/support/shimenv.intercepted_env`,
per fixture case under `tests/fixtures/gh_gather_*/`. Envelope shape is asserted with
`tests/support/envelope_asserts`.

One exception: the deps-capability-gate's "unknown field" detection reads real gh's stderr (v1's
own `2>errfile` capture, confirmed empirically against a live `gh issue view --json <bad-field>`
call — the error text is on stderr with empty stdout). `tests/shim/gh` (frozen for this step; see
tests/README.md) only replays a fixture's `stdout_file` to stdout — it has no stderr-injection
mechanism — so the retry-without-deps *behavioral* flow (second call made, DEPS_UNSUPPORTED
notice attached, empty dep lists) is proven with `unittest.mock.patch` on `pipelib.process.run`
instead of a fixture, and the stderr *classification* itself is proven separately as a pure-function
unit test against `gh_gather._is_unknown_field_error` (mirroring `test_pipelib.py`'s
`_is_gh_auth_failure` pattern: a pure classifier tested directly, without a subprocess). Every
other case in this file is a real subprocess through the real shim.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gh_gather.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

import gh_gather  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib import process  # noqa: E402
from tests.support import envelope_asserts, shimenv  # noqa: E402


def _run_script(args, env):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def _parse_envelope(result):
    lines = result.stdout.splitlines()
    return json.loads(lines[0]) if lines else None


class ScriptExistsTests(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(SCRIPT, os.X_OK))


class HappyPathInlineTests(unittest.TestCase):
    """Legacy inline envelope (no scratch_dir): small body/thread, no marker_prefix given ->
    zero markers, deps available, empty open_prs.
    """

    def setUp(self):
        self.env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_happy_inline")

    def test_exit_code_zero_and_conformant_envelope(self):
        result = _run_script(["42", "o/r"], env=self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        envelope_asserts.assert_exit_code_contract(result.returncode, envelope_present=True)
        self.assertEqual(envelope["status"], "ok")

    def test_scalar_fields_match_the_fixture(self):
        result = _run_script(["42", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["number"], 42)
        self.assertEqual(envelope["title"], "Fix the thing")
        self.assertEqual(envelope["state"], "OPEN")
        self.assertEqual(envelope["url"], "https://github.com/o/r/issues/42")
        self.assertEqual(envelope["thread_comment_count"], 1)
        self.assertEqual(envelope["open_prs"], [])

    def test_no_marker_prefix_means_zero_markers(self):
        result = _run_script(["42", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        self.assertFalse(envelope["marker_comment_present"])
        self.assertEqual(envelope["marker_comment_count"], 0)
        self.assertIsNone(envelope["marker_comment"])

    def test_deps_available_true_with_empty_lists(self):
        result = _run_script(["42", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        self.assertTrue(envelope["deps_available"])
        self.assertEqual(envelope["blocked_by"], [])
        self.assertEqual(envelope["blocking"], [])
        self.assertEqual(envelope["notices"], [])

    def test_subissues_available_true_with_no_hierarchy(self):
        """An ordinary issue: the relation is served, this issue just has no parent and no children.
        `subissues_available: true` with an empty set is the "supported, nothing there" state — the
        thing a reader must not confuse with a capability miss."""
        result = _run_script(["42", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        self.assertTrue(envelope["subissues_available"])
        self.assertIsNone(envelope["parent"])
        self.assertEqual(envelope["sub_issues"], [])
        self.assertEqual(envelope["sub_issues_summary"], {"completed": 0, "percentCompleted": 0, "total": 0})
        self.assertEqual(envelope["notices"], [])

    def test_legacy_inline_shape_carries_full_issue_object_with_complete_thread(self):
        result = _run_script(["42", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        self.assertIn("issue", envelope)
        self.assertEqual(envelope["issue"]["number"], 42)
        self.assertEqual(len(envelope["issue"]["comments"]), 1)
        # Thread comment `id` is the GraphQL node id (v1's `{id: .node_id, ...}` shape) — not the
        # REST numeric id.
        self.assertEqual(envelope["issue"]["comments"][0]["id"], "IC_abc1")
        self.assertEqual(envelope["issue"]["comments"][0]["author"]["login"], "bob")

    def test_no_scratch_dir_means_no_spill_fields_present(self):
        result = _run_script(["42", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        for key in ("issue_body_mode", "thread_mode", "issue_body_path", "thread_path"):
            self.assertNotIn(key, envelope)


class MarkerPresentSingleTests(unittest.TestCase):
    """One marker comment matches marker_prefix -> marker_comment_present true, count 1, no
    MARKER_AMBIGUOUS.
    """

    def setUp(self):
        self.env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_happy_inline")

    def test_marker_prefix_with_no_match_is_absent(self):
        # gh_gather_happy_inline's one comment does not start with this prefix.
        result = _run_script(["42", "o/r", "<!-- implementation-plan:v1 -->"], env=self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["marker_comment_present"])
        self.assertEqual(envelope["marker_comment_count"], 0)


class MarkerAmbiguousTests(unittest.TestCase):
    """DoD invariant: duplicate markers -> MARKER_AMBIGUOUS needs_decision, not a crash and not a
    silent pick-the-first.
    """

    def setUp(self):
        self.env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_marker_ambiguous"
        )

    def test_two_markers_produce_needs_decision_marker_ambiguous(self):
        result = _run_script(["88", "o/r", "<!-- implementation-plan:v1 -->"], env=self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)  # needs_decision is exit 0
        envelope = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")

    def test_decision_context_names_both_marker_comments(self):
        result = _run_script(["88", "o/r", "<!-- implementation-plan:v1 -->"], env=self.env)
        envelope = _parse_envelope(result)
        context = envelope["decision"]["context"]
        self.assertEqual(context["issue"], "88")
        self.assertEqual(len(context["marker_comment_ids"]), 2)
        self.assertIn(111, context["marker_comment_ids"])
        self.assertIn(112, context["marker_comment_ids"])

    def test_decision_options_are_nonempty_actionable_strings(self):
        result = _run_script(["88", "o/r", "<!-- implementation-plan:v1 -->"], env=self.env)
        envelope = _parse_envelope(result)
        options = envelope["decision"]["options"]
        self.assertGreaterEqual(len(options), 1)
        for option in options:
            self.assertIsInstance(option, str)
            self.assertTrue(option)


class PlanPartsAssemblyTests(unittest.TestCase):
    """The plan-marker lookup hands every caller the WHOLE plan — the main comment with its part
    comments put back (skills/_shared/plan-parts.md) — so no prep or prompt ever sees the split."""

    PLAN = "<!-- implementation-plan:v1 -->"

    def run_case(self, case, *extra):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=case)
        result = _run_script(["88", "o/r", self.PLAN] + list(extra), env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return _parse_envelope(result)

    def test_the_marker_body_is_the_whole_plan(self):
        envelope = self.run_case("gh_gather_plan_parts")
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["marker_comment_count"], 1)
        body = envelope["marker_comment"]["body"]
        self.assertIn("- `a.rb` — adds A", body)
        self.assertIn("- architecture.md §3", body)
        self.assertNotIn("build part](", body)
        self.assertLess(body.index("## Doc grounding"), body.index("## Changes (file-level)"))

    def test_the_main_comment_keeps_its_own_id(self):
        envelope = self.run_case("gh_gather_plan_parts")
        self.assertEqual(envelope["marker_comment"]["id"], 200)

    def test_marker_parts_name_each_part_for_the_writer(self):
        envelope = self.run_case("gh_gather_plan_parts")
        self.assertEqual(
            [(p["slug"], p["id"]) for p in envelope["marker_parts"]], [("build", 201), ("references", 202)]
        )

    def test_the_spilled_body_is_the_whole_plan_too(self):
        with tempfile.TemporaryDirectory() as scratch:
            envelope = self.run_case("gh_gather_plan_parts", scratch)
            body = envelope.get("marker_comment_body")
            if body is None:
                body = Path(envelope["marker_comment_path"]).read_text(encoding="utf-8")
        self.assertIn("- `a.rb` — adds A", body)

    def test_another_marker_prefix_is_untouched(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_plan_parts")
        envelope = _parse_envelope(_run_script(["88", "o/r", "<!-- implementation-plan-part:v1:build -->"], env=env))
        self.assertNotIn("marker_parts", envelope)

    def test_a_duplicated_part_is_marker_ambiguous(self):
        envelope = self.run_case("gh_gather_plan_parts_duplicate")
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        self.assertEqual(sorted(envelope["decision"]["context"]["comment_ids"]), [201, 204])


class UntrustedMarkerTests(unittest.TestCase):
    """#179: a marked comment counts only when its author is trusted (``pipelib.trust``). The comments
    carrying the marker are partitioned before the count: an untrusted one is skipped, counted in
    ``marker_untrusted_count`` and reported as a ``MARKER_UNTRUSTED`` notice — never read."""

    PLAN = "<!-- implementation-plan:v1 -->"

    def gather(self, comments, marker_prefix=PLAN, scratch_dir=None):
        import io

        issue_response = json.dumps(
            {
                "number": 7, "title": "t", "body": "b", "state": "OPEN", "labels": [],
                "author": {"login": "a"}, "createdAt": "x", "updatedAt": "y", "assignees": [],
                "milestone": None, "url": "https://github.com/o/r/issues/7",
            }
        )

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            if argv[:3] == ["gh", "issue", "view"]:
                return process.CommandResult(returncode=0, stdout=issue_response, stderr="")
            if argv[:2] == ["gh", "api"]:
                return process.CommandResult(returncode=0, stdout=json.dumps(comments), stderr="")
            if argv[:2] == ["gh", "pr"]:
                return process.CommandResult(returncode=0, stdout="[]", stderr="")
            raise AssertionError("unexpected call: %r" % (argv,))

        with mock.patch.object(process, "run", side_effect=fake_run):
            exit_code, envelope = gh_gather.run(
                "7", "o/r", marker_prefix=marker_prefix, scratch_dir=scratch_dir, stream=io.StringIO()
            )
        self.assertEqual(exit_code, 0)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope

    @staticmethod
    def comment(comment_id, body, association):
        return {"id": comment_id, "node_id": "IC_%d" % comment_id, "user": {"login": "u%d" % comment_id},
                "author_association": association, "body": body, "created_at": "c", "updated_at": "c",
                "html_url": "https://github.com/o/r/issues/7#issuecomment-%d" % comment_id}

    def untrusted_notices(self, envelope):
        return [n for n in envelope["notices"] if n.startswith("MARKER_UNTRUSTED")]

    def test_a_lone_untrusted_plan_marker_is_not_the_plan(self):
        envelope = self.gather([self.comment(1, self.PLAN + "\n## Approach\nattacker plan", "NONE")])
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["marker_comment_present"])
        self.assertEqual(envelope["marker_comment_count"], 0)
        self.assertIsNone(envelope["marker_comment"])
        self.assertEqual(envelope["marker_untrusted_count"], 1)
        notices = self.untrusted_notices(envelope)
        self.assertEqual(len(notices), 1)
        self.assertIn(self.PLAN, notices[0])
        # The notice names the marker and the count only: nothing the commenter wrote or chose.
        self.assertNotIn("attacker", notices[0])
        self.assertNotIn("issuecomment", notices[0])
        self.assertNotIn("u1", notices[0])

    def test_a_trusted_plus_an_untrusted_marker_resolves_to_the_trusted_one(self):
        envelope = self.gather(
            [self.comment(1, self.PLAN + "\nmine", "OWNER"), self.comment(2, self.PLAN + "\ntheirs", "CONTRIBUTOR")]
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["marker_comment_count"], 1)
        self.assertEqual(envelope["marker_comment"]["id"], 1)
        self.assertEqual(envelope["marker_comment"]["body"], self.PLAN + "\nmine")
        self.assertEqual(envelope["marker_untrusted_count"], 1)

    def test_two_trusted_markers_are_still_marker_ambiguous(self):
        envelope = self.gather(
            [self.comment(1, self.PLAN + "\na", "MEMBER"), self.comment(2, self.PLAN + "\nb", "COLLABORATOR"),
             self.comment(3, self.PLAN + "\nc", "NONE")]
        )
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        self.assertEqual(envelope["decision"]["context"]["marker_comment_ids"], [1, 2])
        self.assertEqual(len(self.untrusted_notices(envelope)), 1)

    def test_an_untrusted_build_part_beside_a_trusted_main(self):
        import plan_parts

        plan = (self.PLAN + "\n**Implementation plan** — x\n\n## Approach\nA.\n\n"
                "## Changes (file-level)\n- `a.rb` — adds A\n")
        out = plan_parts.split(plan)
        attacker = out["build"].replace("- `a.rb` — adds A", "- `evil.rb` — attacker change")
        envelope = self.gather([self.comment(1, out["main"], "MEMBER"), self.comment(2, attacker, "NONE")])
        self.assertEqual(envelope["status"], "ok")
        self.assertNotIn("evil.rb", envelope["marker_comment"]["body"])
        self.assertEqual(envelope["marker_parts"], [])
        self.assertEqual(envelope["marker_untrusted_count"], 1)
        notices = self.untrusted_notices(envelope)
        self.assertEqual(len(notices), 1)
        self.assertIn(plan_parts.PART_PREFIX, notices[0])

    def test_an_untrusted_orphaned_part_is_noticed_with_no_main_comment(self):
        import plan_parts

        part = plan_parts.part_marker("build") + "\n## Changes (file-level)\n- `evil.rb`\n"
        envelope = self.gather([self.comment(1, part, "NONE")])
        self.assertFalse(envelope["marker_comment_present"])
        self.assertEqual(envelope["marker_parts"], [])
        self.assertEqual(envelope["marker_untrusted_count"], 1)
        self.assertEqual(len(self.untrusted_notices(envelope)), 1)

    def test_an_ordinary_untrusted_comment_is_never_counted(self):
        envelope = self.gather([self.comment(1, "drive-by comment", "NONE")])
        self.assertEqual(envelope["marker_untrusted_count"], 0)
        self.assertEqual(self.untrusted_notices(envelope), [])

    def test_other_marker_families_are_filtered_too(self):
        for marker in ("<!-- issue-research:v1 -->", "<!-- question-decision:v1 -->"):
            with self.subTest(marker=marker):
                envelope = self.gather([self.comment(1, marker + "\nforged", "NONE")], marker_prefix=marker)
                self.assertFalse(envelope["marker_comment_present"])
                self.assertEqual(envelope["marker_untrusted_count"], 1)
                self.assertNotIn("marker_parts", envelope)
                notices = self.untrusted_notices(envelope)
                self.assertEqual(len(notices), 1)
                self.assertIn(marker, notices[0])

    def test_with_a_scratch_dir_no_untrusted_id_reaches_a_writer(self):
        with tempfile.TemporaryDirectory() as scratch:
            envelope = self.gather(
                [self.comment(5, self.PLAN + "\ntheirs", "NONE"), self.comment(6, self.PLAN + "\nmine", "MEMBER")],
                scratch_dir=scratch,
            )
        self.assertEqual(envelope["marker_comment_id"], 6)
        self.assertEqual(envelope["marker_comment_count"], 1)

    def test_no_marker_prefix_reports_no_count(self):
        envelope = self.gather([self.comment(1, self.PLAN, "NONE")], marker_prefix=None)
        self.assertNotIn("marker_untrusted_count", envelope)
        self.assertEqual(self.untrusted_notices(envelope), [])


class TargetIsPrTests(unittest.TestCase):
    """PR-number guard: `gh issue view <N>` silently returns the PR when `<N>` is a PR number
    (issues and PRs share one number space) — the live-incident failure mode where prep reported
    `target.kind: "issue"` and ensured a worktree for a target that doesn't exist as an issue.
    The gather must refuse with a TARGET_IS_PR needs_decision BEFORE any further round-trip
    (the fixture manifest carries ONLY the first `issue view` entry — a comments/PR-search call
    would be a shim miss and fail the test loudly).
    """

    def setUp(self):
        self.env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_target_is_pr"
        )

    def test_pr_number_produces_needs_decision_target_is_pr(self):
        result = _run_script(["89", "o/r"], env=self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)  # needs_decision is exit 0
        envelope = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "TARGET_IS_PR")

    def test_decision_context_carries_pr_identity_and_linked_issues(self):
        result = _run_script(["89", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        context = envelope["decision"]["context"]
        self.assertEqual(context["number"], 89)
        self.assertEqual(context["url"], "https://github.com/o/r/pull/89")
        # Body carries "Fixes #62" twice + "Closes: #7" — de-duplicated, order-preserving.
        self.assertEqual(context["linked_issues"], [62, 7])

    def test_options_offer_resolving_the_linked_issue_instead(self):
        result = _run_script(["89", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        options = envelope["decision"]["options"]
        self.assertTrue(any("#62" in option for option in options), options)
        for option in options:
            self.assertIsInstance(option, str)
            self.assertTrue(option)

    def test_marker_prefix_and_scratch_dir_do_not_bypass_the_guard(self):
        with tempfile.TemporaryDirectory() as scratch:
            result = _run_script(
                ["89", "o/r", "<!-- implementation-plan:v1 -->", scratch], env=self.env
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            envelope = _parse_envelope(result)
            self.assertEqual(envelope["status"], "needs_decision")
            self.assertEqual(envelope["decision"]["code"], "TARGET_IS_PR")
            # Nothing was spilled — the guard fires before any section staging.
            self.assertEqual(os.listdir(scratch), [])


class LinkedIssueNumbersPureFunctionTests(unittest.TestCase):
    """gh_gather.linked_issue_numbers — the closing-keyword parse feeding TARGET_IS_PR's context
    (pure-function tests, mirroring the _is_unknown_field_error pattern)."""

    def test_closing_keyword_forms(self):
        self.assertEqual(gh_gather.linked_issue_numbers("Fixes #62"), [62])
        self.assertEqual(gh_gather.linked_issue_numbers("closes  #7."), [7])
        self.assertEqual(gh_gather.linked_issue_numbers("Resolved: #12\nfix #3"), [12, 3])

    def test_dedup_preserves_first_seen_order(self):
        self.assertEqual(gh_gather.linked_issue_numbers("Fixes #62, closes #7, fixes #62"), [62, 7])

    def test_non_closing_references_do_not_match(self):
        self.assertEqual(gh_gather.linked_issue_numbers("See #62 and relates to #7"), [])
        self.assertEqual(gh_gather.linked_issue_numbers("Phase 2 prefixes #"), [])
        self.assertEqual(gh_gather.linked_issue_numbers(None), [])


class IsPullRequestPureFunctionTests(unittest.TestCase):
    """gh_gather._is_pull_request — the /pull/-vs-/issues/ URL discriminator."""

    def test_pull_url_is_a_pr(self):
        self.assertTrue(gh_gather._is_pull_request({"url": "https://github.com/o/r/pull/89"}))

    def test_issue_url_is_not_a_pr(self):
        self.assertFalse(gh_gather._is_pull_request({"url": "https://github.com/o/r/issues/89"}))

    def test_missing_url_is_not_a_pr(self):
        self.assertFalse(gh_gather._is_pull_request({}))
        self.assertFalse(gh_gather._is_pull_request({"url": None}))


class ThresholdSpillTests(unittest.TestCase):
    """Threshold spill: body/thread/marker each go to path mode when > threshold, with a
    scratch_dir supplied; small sections in the SAME run still report their own *_mode
    independently (mixed inline/path is legitimate, not all-or-nothing).
    """

    def setUp(self):
        self.env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_spilled_scratch"
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch_dir = self._tmp.name

    def test_large_body_and_thread_and_marker_all_spill_to_path(self):
        result = _run_script(
            ["77", "o/r", "<!-- implementation-plan:v1 -->", self.scratch_dir], env=self.env
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(
            envelope, spill_section_names=["issue_body", "thread", "marker_comment"]
        )
        self.assertEqual(envelope["issue_body_mode"], "path")
        self.assertEqual(envelope["thread_mode"], "path")
        self.assertEqual(envelope["marker_comment_mode"], "path")

    def test_spilled_files_are_actually_written_with_correct_byte_counts(self):
        result = _run_script(
            ["77", "o/r", "<!-- implementation-plan:v1 -->", self.scratch_dir], env=self.env
        )
        envelope = _parse_envelope(result)
        body_path = Path(envelope["issue_body_path"])
        self.assertTrue(body_path.is_file())
        self.assertEqual(body_path.stat().st_size, envelope["issue_body_bytes"])
        marker_path = Path(envelope["marker_comment_path"])
        self.assertTrue(marker_path.is_file())
        self.assertEqual(marker_path.stat().st_size, envelope["marker_comment_bytes"])

    def test_no_bare_content_field_present_when_mode_is_path(self):
        result = _run_script(
            ["77", "o/r", "<!-- implementation-plan:v1 -->", self.scratch_dir], env=self.env
        )
        envelope = _parse_envelope(result)
        self.assertNotIn("issue_body", envelope)
        self.assertNotIn("thread", envelope)
        self.assertNotIn("marker_comment_body", envelope)
        # Path-mode envelope also omits the legacy-inline-only `issue`/`marker_comment` keys.
        self.assertNotIn("issue", envelope)

    def test_marker_comment_id_and_url_still_present_as_scalars_in_path_mode(self):
        result = _run_script(
            ["77", "o/r", "<!-- implementation-plan:v1 -->", self.scratch_dir], env=self.env
        )
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["marker_comment_id"], 5001)
        self.assertEqual(
            envelope["marker_comment_url"], "https://github.com/o/r/issues/77#issuecomment-5001"
        )

    def test_marker_comment_updated_at_is_reported(self):
        # #18: the planner compares a sub-issue's last-modified time against the plan comment's, so
        # the marker carries its own `updated_at`. It rides along free — the REST comment objects
        # already have it, so there is no extra field selection and no extra round-trip.
        result = _run_script(
            ["77", "o/r", "<!-- implementation-plan:v1 -->", self.scratch_dir], env=self.env
        )
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["marker_comment_updated_at"], "2026-02-03T00:00:00Z")

    def test_marker_comment_created_at_is_reported(self):
        # #156: the board history's plan-posted point is the plan comment's own creation time, read
        # from the raw comment beside `updated_at` (the fixture's plan was edited after it was posted).
        result = _run_script(
            ["77", "o/r", "<!-- implementation-plan:v1 -->", self.scratch_dir], env=self.env
        )
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["marker_comment_created_at"], "2026-02-01T00:00:00Z")
        self.assertEqual(envelope["marker_comment_updated_at"], "2026-02-03T00:00:00Z")

    def test_inline_threshold_bytes_scalar_is_reported_with_scratch_dir(self):
        result = _run_script(
            ["77", "o/r", "<!-- implementation-plan:v1 -->", self.scratch_dir], env=self.env
        )
        envelope = _parse_envelope(result)
        self.assertIn("inline_threshold_bytes", envelope)
        self.assertIsInstance(envelope["inline_threshold_bytes"], int)


class ThresholdInlineSmallSectionTests(unittest.TestCase):
    """With a scratch_dir supplied but small sections (well under threshold), everything still
    reports inline mode with the bare content field present — a scratch_dir alone doesn't force
    path mode.
    """

    def setUp(self):
        self.env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_happy_inline")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch_dir = self._tmp.name

    def test_small_sections_stay_inline_even_with_scratch_dir_present(self):
        result = _run_script(["42", "o/r", "", self.scratch_dir], env=self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        # Both `issue_body` (genuine prose) and `thread` (a JSON-serialized structural section,
        # same as gh_pr_gather.py's `thread`/`reviews`) are uniform spill_bytes sections: the
        # generic conformance helper applies to both, since the inline bare field contract is "str,
        # decoded UTF-8 text" regardless of whether that text happens to be prose or JSON.
        envelope_asserts.assert_full_envelope_conformance(
            envelope, spill_section_names=["issue_body", "thread"]
        )
        self.assertEqual(envelope["issue_body_mode"], "inline")
        self.assertEqual(envelope["issue_body"], "Small body text.")
        self.assertEqual(envelope["thread_mode"], "inline")
        self.assertIsInstance(envelope["thread_bytes"], int)
        self.assertNotIn("thread_path", envelope)
        # The inline `thread` bare field is a JSON-encoded STRING (matching gh_pr_gather.py and the
        # spill contract), not a pre-parsed list — a consumer does its own json.loads, exactly as
        # it would on a spilled thread_path file's contents.
        self.assertIsInstance(envelope["thread"], str)
        parsed_thread = json.loads(envelope["thread"])
        self.assertIsInstance(parsed_thread, list)
        self.assertEqual(len(parsed_thread), 1)

    def test_threshold_mode_envelope_has_no_legacy_issue_key(self):
        result = _run_script(["42", "o/r", "", self.scratch_dir], env=self.env)
        envelope = _parse_envelope(result)
        self.assertNotIn("issue", envelope)
        self.assertNotIn("marker_comment", envelope)


class ExtraJsonTests(unittest.TestCase):
    """The v1 executor agent's `extra_json=<fields>` caller knob, ported as `--extra-json`: a
    supplementary `gh issue view --json <fields>` call whose scalar fields fold into the same
    envelope.
    """

    def setUp(self):
        self.env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_extra_json")

    def test_extra_json_fields_are_folded_into_the_envelope(self):
        result = _run_script(
            ["33", "o/r", "", "", "--extra-json", "closedByPullRequestsReferences,projectItems"],
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(
            envelope["closedByPullRequestsReferences"], [{"number": 34, "state": "MERGED"}]
        )
        self.assertEqual(envelope["projectItems"], [])

    def test_extra_json_never_clobbers_a_base_scalar(self):
        # Defensive: even if extra_json accidentally re-requested "number", the base scalar wins.
        result = _run_script(
            ["33", "o/r", "", "", "--extra-json", "closedByPullRequestsReferences,projectItems"],
            env=self.env,
        )
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["number"], 33)

    def test_without_extra_json_flag_no_extra_fields_present(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_happy_inline")
        result = _run_script(["42", "o/r"], env=env)
        envelope = _parse_envelope(result)
        self.assertNotIn("closedByPullRequestsReferences", envelope)
        self.assertNotIn("projectItems", envelope)


class PaginationTests(unittest.TestCase):
    """Thread pagination: the complete comment thread comes back regardless of how many top-level
    JSON values `gh api --paginate` prints on stdout — one real-world-shaped case (gh auto-merges
    REST list pages into a single array; confirmed empirically) and one defensive case (multiple
    concatenated arrays, gh's own documented general contract), both flattened identically.
    """

    def test_single_merged_array_yields_the_full_flattened_thread(self):
        env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_pagination_single_merged_array"
        )
        result = _run_script(["67", "o/r"], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["thread_comment_count"], 5)
        self.assertEqual(envelope["issue"]["comments"][0]["body"], "comment 1")
        self.assertEqual(envelope["issue"]["comments"][-1]["body"], "comment 5")

    def test_multiple_concatenated_arrays_are_flattened_into_one_thread(self):
        env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_pagination_concatenated_arrays"
        )
        result = _run_script(["66", "o/r"], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        # 3 comments on page1 + 2 on page2 = 5, proving both top-level arrays were consumed, not
        # just the first.
        self.assertEqual(envelope["thread_comment_count"], 5)
        bodies = [c["body"] for c in envelope["issue"]["comments"]]
        self.assertIn("page1 comment 1", bodies)
        self.assertIn("page2 comment 5", bodies)


class HardFailureTests(unittest.TestCase):
    """architecture.md §3: "any other non-zero — hard failure ...; no envelope is guaranteed." A
    genuine gh failure (not the unknown-field capability-gate signature) must exit non-zero with
    NO envelope on stdout — never a clean-but-wrong "ok" or a silently degraded deps_available.
    """

    def test_non_capability_gate_failure_exits_nonzero_with_no_envelope(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_hard_failure")
        result = _run_script(["999", "o/r"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_hard_failure_exit_code_is_not_the_usage_error_code(self):
        # Distinguish "hard runtime failure" (this case) from "usage error" (missing args) —
        # architecture.md §3 reserves exit 2 for usage errors with no envelope; a hard failure is
        # "any OTHER non-zero", so it must not collide with 2.
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_hard_failure")
        result = _run_script(["999", "o/r"], env=env)
        self.assertNotEqual(result.returncode, 2)


class AuthRequiredClassificationTests(unittest.TestCase):
    """architecture.md §3: "AUTH_REQUIRED ... detected by the pipelib runner, any script" — proves
    gh_gather.py surfaces it as a needs_decision envelope (exit 0) rather than the undifferentiated
    hard failure (exit 1) HardFailureTests exercises above, mirroring
    test_gh_persist.py's identical AuthRequiredClassificationTests and gh_pr_gather.py's own
    AUTH_REQUIRED handling. The shim only replays exit_code (no stderr channel — see
    tests/shim/gh's module docstring), but that's sufficient: pipelib.process._is_gh_auth_failure's
    PRIMARY signal is gh's own documented exit code 4 (`gh help exit-codes`), so a bare
    exit_code=4 fixture on the very first gh call (the deps-inclusive `gh issue view`) exercises
    the exact same classification path a real auth failure would, with no stdout needed at all.
    """

    def test_auth_required_on_first_call_is_a_needs_decision_not_a_hard_failure(self):
        env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_auth_required"
        )
        result = _run_script(["42", "o/r"], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AUTH_REQUIRED")

    def test_auth_required_decision_options_mention_gh_auth_login(self):
        env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_auth_required"
        )
        result = _run_script(["42", "o/r"], env=env)
        envelope = _parse_envelope(result)
        self.assertIn("gh auth login", " ".join(envelope["decision"]["options"]))

    def test_auth_required_never_retries_as_an_unknown_field_capability_miss(self):
        # An auth failure (exit 4) must not be misclassified as the "unknown field" deps-capability
        # signature -- there is no second manifest entry for the base-fields-only retry call, so if
        # the script wrongly treated this as a capability miss and retried, the shim would MISS
        # (exit 2, malformed/no envelope) and this test would fail.
        env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_auth_required"
        )
        result = _run_script(["42", "o/r"], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["decision"]["code"], "AUTH_REQUIRED")
        self.assertNotEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")

    def test_auth_required_is_also_caught_on_a_later_gh_call_not_just_the_first(self):
        # The auth check is applied uniformly at every gh call site in the module (per
        # _fail_hard_or_auth's docstring), not just the first issue-view fetch -- this fixture
        # succeeds on the FIRST call (issue view) and only fails auth on the SECOND (the paginated
        # comments fetch), proving the classification isn't hardcoded to one call site.
        env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_auth_required_on_comments_call"
        )
        result = _run_script(["42", "o/r"], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AUTH_REQUIRED")


class UsageErrorTests(unittest.TestCase):
    """architecture.md §3: exit 2 == usage error, no envelope."""

    def test_missing_required_positional_args_exits_two_with_no_envelope(self):
        result = _run_script([], env=dict(os.environ))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_missing_repo_exits_two_with_no_envelope(self):
        result = _run_script(["42"], env=dict(os.environ))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


class UnknownFieldClassifierPureFunctionTests(unittest.TestCase):
    """gh_gather._is_unknown_field_error: the pure classifier behind the deps capability-gate,
    tested directly (mirrors test_pipelib.py's ProcessAuthRequiredClassificationTests pattern for
    the analogous AUTH_REQUIRED classifier). Real gh's exact phrasing, confirmed empirically
    against a live `gh issue view --json <bad-field>` call: `Unknown JSON field: "x"` on stderr.
    """

    def test_real_gh_unknown_json_field_phrasing_is_classified(self):
        self.assertTrue(
            gh_gather._is_unknown_field_error('Unknown JSON field: "blockedBy"\nAvailable fields:\n')
        )

    def test_lowercase_variant_is_classified_case_insensitively(self):
        self.assertTrue(gh_gather._is_unknown_field_error('unknown json field: "blockedBy"'))

    def test_bare_unknown_field_phrasing_is_also_classified(self):
        self.assertTrue(gh_gather._is_unknown_field_error("unknown field: blockedBy"))

    def test_unrelated_error_text_is_not_classified(self):
        self.assertFalse(
            gh_gather._is_unknown_field_error(
                "GraphQL: Could not resolve to an issue or pull request with the number of 999."
            )
        )

    def test_empty_or_none_stderr_is_not_classified(self):
        self.assertFalse(gh_gather._is_unknown_field_error(""))
        self.assertFalse(gh_gather._is_unknown_field_error(None))


class NativeHierarchyPayloadTests(unittest.TestCase):
    """The parent/sub-issue relation surfaces verbatim in the envelope, including GitHub's own
    progress rollup — the fact `skills/_shared/epic-story-hierarchy.md` makes tier 1 of the
    epic story-set read. Node shape matches a live `gh issue view --json subIssues` response.
    """

    def setUp(self):
        self.env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_native_hierarchy"
        )

    def test_sub_issues_and_summary_surface_in_order(self):
        result = _run_script(["95", "o/r"], env=self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertTrue(envelope["subissues_available"])
        self.assertEqual([node["number"] for node in envelope["sub_issues"]], [93, 94])
        self.assertEqual([node["state"] for node in envelope["sub_issues"]], ["CLOSED", "OPEN"])
        # Read progress from GitHub's rollup rather than counting states.
        self.assertEqual(
            envelope["sub_issues_summary"], {"completed": 1, "percentCompleted": 50, "total": 2}
        )
        self.assertEqual(envelope["notices"], [])

    def test_epic_body_carries_no_stories_section(self):
        """The fresh-epic body shape: the DoD names the bar, nothing lists the stories."""
        result = _run_script(["95", "o/r"], env=self.env)
        envelope = _parse_envelope(result)
        self.assertNotIn("## Stories", envelope["issue"]["body"])
        self.assertIn("All stories are closed", envelope["issue"]["body"])


class DepsCapabilityGateRetryWithoutFlowTests(unittest.TestCase):
    """DoD invariant: deps-unsupported -> DEPS_UNSUPPORTED notice + retry-without, and the read
    still succeeds with empty dep lists. Proven end to end through
    gh_gather._fetch_issue_with_relation_capability_gates using unittest.mock.patch on
    pipelib.process.run, because tests/shim/gh (frozen for this step) has no stderr-injection
    mechanism and the classification this flow depends on is stderr-based (see this module's
    docstring). The mocked CommandResult objects mirror exactly what a real `gh` unknown-field
    failure followed by a successful reduced-field retry look like (both confirmed empirically).
    """

    ISSUE_RESPONSE = json.dumps(
        {
            "number": 55,
            "title": "Old gh repo",
            "body": "body text",
            "state": "OPEN",
            "labels": [],
            "author": {"login": "frank"},
            "createdAt": "2026-04-01T00:00:00Z",
            "updatedAt": "2026-04-02T00:00:00Z",
            "assignees": [],
            "milestone": None,
            "url": "https://github.com/o/r/issues/55",
        }
    )

    def test_unknown_field_failure_triggers_retry_without_deps_and_succeeds(self):
        """Deps unsupported, sub-issues supported: the ladder skips the deps-bearing rungs and
        lands on the sub-issue-only rung, reporting ONLY DEPS_UNSUPPORTED — the point of gating the
        two relations independently is that losing one doesn't cost you the other."""
        calls = []

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            calls.append(list(argv))
            if "blockedBy,blocking" in argv[-1]:
                return process.CommandResult(
                    returncode=1, stdout="", stderr='Unknown JSON field: "blockedBy"\n'
                )
            return process.CommandResult(returncode=0, stdout=self.ISSUE_RESPONSE, stderr="")

        with mock.patch.object(process, "run", side_effect=fake_run):
            (
                issue_obj,
                deps_available,
                subissues_available,
                result,
            ) = gh_gather._fetch_issue_with_relation_capability_gates("55", "o/r", None)

        self.assertIsNotNone(issue_obj)
        self.assertFalse(deps_available)
        self.assertTrue(subissues_available)
        self.assertEqual(result, ["DEPS_UNSUPPORTED"])
        self.assertEqual(issue_obj["number"], 55)
        # Rung 1 (both) and rung 2 (deps only) carry blockedBy and fail; rung 3 (sub-issues only)
        # succeeds.
        self.assertEqual(len(calls), 3)
        self.assertIn("blockedBy,blocking", calls[0][-1])
        self.assertIn("subIssues", calls[0][-1])
        self.assertNotIn("blockedBy", calls[2][-1])
        self.assertIn("subIssues", calls[2][-1])

    def test_subissues_unsupported_keeps_deps_and_reports_only_subissues_notice(self):
        """The mirror case: sub-issues unsupported, deps supported. Lands on rung 2 after one
        failure, reports ONLY SUBISSUES_UNSUPPORTED."""
        calls = []

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            calls.append(list(argv))
            if "subIssues" in argv[-1]:
                return process.CommandResult(
                    returncode=1, stdout="", stderr='Unknown JSON field: "subIssues"\n'
                )
            return process.CommandResult(returncode=0, stdout=self.ISSUE_RESPONSE, stderr="")

        with mock.patch.object(process, "run", side_effect=fake_run):
            (
                issue_obj,
                deps_available,
                subissues_available,
                result,
            ) = gh_gather._fetch_issue_with_relation_capability_gates("55", "o/r", None)

        self.assertIsNotNone(issue_obj)
        self.assertTrue(deps_available)
        self.assertFalse(subissues_available)
        self.assertEqual(result, ["SUBISSUES_UNSUPPORTED"])
        self.assertEqual(len(calls), 2)
        self.assertIn("blockedBy,blocking", calls[1][-1])
        self.assertNotIn("subIssues", calls[1][-1])

    def test_both_relations_unsupported_reports_both_notices_on_the_base_rung(self):
        calls = []

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            calls.append(list(argv))
            if "blockedBy" in argv[-1] or "subIssues" in argv[-1]:
                return process.CommandResult(
                    returncode=1, stdout="", stderr="unknown JSON field\n"
                )
            return process.CommandResult(returncode=0, stdout=self.ISSUE_RESPONSE, stderr="")

        with mock.patch.object(process, "run", side_effect=fake_run):
            (
                issue_obj,
                deps_available,
                subissues_available,
                result,
            ) = gh_gather._fetch_issue_with_relation_capability_gates("55", "o/r", None)

        self.assertIsNotNone(issue_obj)
        self.assertFalse(deps_available)
        self.assertFalse(subissues_available)
        self.assertEqual(result, ["DEPS_UNSUPPORTED", "SUBISSUES_UNSUPPORTED"])
        self.assertEqual(len(calls), 4)

    def test_capability_shaped_failure_on_the_base_rung_is_a_hard_failure(self):
        """An "unknown field" error on the rung that asked for NO optional fields is not a
        capability miss — there is nothing left to drop, so it must surface rather than loop or
        return a clean-but-wrong envelope."""

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            return process.CommandResult(returncode=1, stdout="", stderr="unknown JSON field\n")

        with mock.patch.object(process, "run", side_effect=fake_run):
            (
                issue_obj,
                deps_available,
                subissues_available,
                result,
            ) = gh_gather._fetch_issue_with_relation_capability_gates("55", "o/r", None)

        self.assertIsNone(issue_obj)
        self.assertIsNone(deps_available)
        self.assertIsNone(subissues_available)
        self.assertEqual(result.returncode, 1)

    def test_full_run_surfaces_deps_unsupported_notice_with_empty_dep_lists(self):
        """The same flow, exercised through the full `run()` function (not just the capability-gate
        helper) so the DoD's "the read still succeeds with empty dep lists" half is proven against
        the actual emitted envelope, not just the intermediate tuple.
        """
        base_only_response = json.dumps(
            {
                "number": 55, "title": "Old gh repo", "body": "body text", "state": "OPEN",
                "labels": [], "author": {"login": "frank"}, "createdAt": "2026-04-01T00:00:00Z",
                "updatedAt": "2026-04-02T00:00:00Z", "assignees": [], "milestone": None,
                "url": "https://github.com/o/r/issues/55",
            }
        )

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            if argv[:2] == ["gh", "issue"] and "blockedBy,blocking" in argv[-1]:
                return process.CommandResult(
                    returncode=1, stdout="", stderr='Unknown JSON field: "blockedBy"\n'
                )
            if argv[:2] == ["gh", "issue"] and "--json" in argv:
                return process.CommandResult(returncode=0, stdout=base_only_response, stderr="")
            if argv[:2] == ["gh", "api"]:
                return process.CommandResult(returncode=0, stdout="[]", stderr="")
            if argv[:2] == ["gh", "pr"]:
                return process.CommandResult(returncode=0, stdout="[]", stderr="")
            raise AssertionError("unexpected call: %r" % (argv,))

        import io

        stream = io.StringIO()
        with mock.patch.object(process, "run", side_effect=fake_run):
            exit_code, envelope = gh_gather.run("55", "o/r", stream=stream)

        self.assertEqual(exit_code, 0)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["deps_available"])
        self.assertEqual(envelope["blocked_by"], [])
        self.assertEqual(envelope["blocking"], [])
        self.assertIn("DEPS_UNSUPPORTED", envelope["notices"])
        # in-process emission also wrote to the passed stream, not just returned the dict.
        self.assertEqual(json.loads(stream.getvalue().splitlines()[0]), envelope)

    def test_deps_unsupported_notice_and_marker_ambiguous_decision_can_co_occur(self):
        """A `needs_decision` envelope still carries non-blocking `notices` independently of the
        decision (architecture.md §3: notices "ride in notices: [] ... regardless of status") —
        proven with a scenario that genuinely triggers both at once, not just asserted in the
        abstract.
        """
        issue_response = json.dumps(
            {
                "number": 1, "title": "t", "body": "b", "state": "OPEN", "labels": [],
                "author": {"login": "a"}, "createdAt": "x", "updatedAt": "y", "assignees": [],
                "milestone": None, "url": "u",
            }
        )
        comments_response = json.dumps(
            [
                {"id": 1, "node_id": "n1", "user": {"login": "a"}, "author_association": "MEMBER",
                 "body": "<!-- m -->1", "created_at": "c", "html_url": "h1"},
                {"id": 2, "node_id": "n2", "user": {"login": "a"}, "author_association": "MEMBER",
                 "body": "<!-- m -->2", "created_at": "c", "html_url": "h2"},
            ]
        )

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            if "blockedBy,blocking" in argv[-1]:
                return process.CommandResult(returncode=1, stdout="", stderr="Unknown JSON field: x")
            if argv[:3] == ["gh", "issue", "view"] and "--json" in argv:
                return process.CommandResult(returncode=0, stdout=issue_response, stderr="")
            if argv[:2] == ["gh", "api"]:
                return process.CommandResult(returncode=0, stdout=comments_response, stderr="")
            if argv[:2] == ["gh", "pr"]:
                return process.CommandResult(returncode=0, stdout="[]", stderr="")
            raise AssertionError("unexpected call: %r" % (argv,))

        import io

        with mock.patch.object(process, "run", side_effect=fake_run):
            exit_code, envelope = gh_gather.run(
                "1", "o/r", marker_prefix="<!-- m -->", stream=io.StringIO()
            )

        self.assertEqual(exit_code, 0)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        self.assertEqual(envelope["notices"], ["DEPS_UNSUPPORTED"])


class MarkerIdUsesRestNumericIdNotNodeIdTests(unittest.TestCase):
    """Load-bearing v1 quirk: a marker comment's `id` is the REST numeric id (what
    gh-persist.sh's --delete-marker-id needs for `gh api -X DELETE .../comments/<id>`), NOT the
    GraphQL node id the thread's own comment shape uses. Getting this backwards would silently
    break marker-comment deletion on replacement.
    """

    def test_marker_id_is_the_numeric_rest_id(self):
        env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="gh_gather_marker_ambiguous"
        )
        result = _run_script(["88", "o/r", "<!-- implementation-plan:v1 -->"], env=env)
        envelope = _parse_envelope(result)
        ids = envelope["decision"]["context"]["marker_comment_ids"]
        # 111 / 112 are the fixture's REST numeric ids; the node ids are "IC_m1"/"IC_m2" and must
        # never appear here.
        self.assertEqual(sorted(ids), [111, 112])

    def test_thread_comment_id_is_the_graphql_node_id(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_happy_inline")
        result = _run_script(["42", "o/r"], env=env)
        envelope = _parse_envelope(result)
        self.assertEqual(envelope["issue"]["comments"][0]["id"], "IC_abc1")

    def test_thread_comment_also_carries_the_rest_numeric_id_as_database_id(self):
        # Additive to the v1 shape above (#34): `id` stays the node id for every existing reader,
        # and `databaseId` carries the numeric id the raw REST payload already held -- the only id
        # space `gh_persist.py comment --delete-marker-id` can delete. A consumer that locates a
        # marker by scanning this thread (prep_planner) reads it from here.
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_happy_inline")
        result = _run_script(["42", "o/r"], env=env)
        envelope = _parse_envelope(result)
        comment = envelope["issue"]["comments"][0]
        self.assertEqual(comment["id"], "IC_abc1")
        self.assertEqual(comment["databaseId"], 9001)


class RepoPassedExplicitlyNeverAmbientCwdTests(unittest.TestCase):
    """S21 brief's cwd-discipline advisory: every gh call passes --repo explicitly rather than
    relying on ambient cwd. Proven by running the script from a cwd that is NOT a git repo at all
    (a bare temp dir) and confirming the gather still succeeds via the shim.
    """

    def test_gather_succeeds_when_invoked_from_a_non_repo_cwd(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_happy_inline")
        with tempfile.TemporaryDirectory() as bare_cwd:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "42", "o/r"],
                cwd=bare_cwd,
                env=env,
                capture_output=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(envelope["number"], 42)


class ReferencesIssueTests(unittest.TestCase):
    """`gh_gather.references_issue` — the false-positive fix's pure filter function (S12
    follow-up round). Live evidence recorded in the module docstring / docs/specs/resolver.md +
    docs/specs/planner.md "Known bugs/gaps": `--search "<N> in:body"` (bare digit OR `#N` form —
    both proven identical against the sandbox repo) is a GitHub full-text search, not a
    literal-string containment check, so a client-side post-filter is the actual correctness gate.
    """

    def test_hash_reference_matches(self):
        self.assertTrue(gh_gather.references_issue("Fixes #2 today", 2))

    def test_longer_number_sharing_the_leading_digit_does_not_match(self):
        # "#20" must never match a search for issue 2.
        self.assertFalse(gh_gather.references_issue("See #20 for details", 2))

    def test_hash_two_is_not_found_inside_hash_twelve(self):
        # "#12" must never match a search for issue 2 (no "#2" substring is even present).
        self.assertFalse(gh_gather.references_issue("See #12 for details", 2))

    def test_bare_phase_number_prose_does_not_match(self):
        # The exact live false positive: "## Phase tracker\n- [x] Phase 2 — ..." with no "#".
        self.assertFalse(gh_gather.references_issue("## Phase tracker\n- [x] Phase 2 — harness", 2))

    def test_bare_issue_word_prose_does_not_match(self):
        self.assertFalse(gh_gather.references_issue("this mentions issue 2 casually", 2))

    def test_trailing_punctuation_after_the_reference_still_matches(self):
        self.assertTrue(gh_gather.references_issue("Fixes #2.", 2))

    def test_closing_issue_numbers_matches_even_when_the_body_lacks_the_keyword(self):
        # A PR whose body never spells out "#2" but whose closingIssuesReferences names it.
        self.assertTrue(gh_gather.references_issue("no keyword in here at all", 2, closing_issue_numbers=[2]))

    def test_closing_issue_numbers_present_but_for_a_different_issue_is_false(self):
        self.assertFalse(gh_gather.references_issue("no keyword in here at all", 2, closing_issue_numbers=[20]))

    def test_none_body_text_with_no_closing_numbers_is_false(self):
        self.assertFalse(gh_gather.references_issue(None, 2))

    def test_adjacent_word_character_before_the_hash_does_not_match(self):
        self.assertFalse(gh_gather.references_issue("seeR#2 tracked separately", 2))

    def test_trailing_alpha_glued_to_the_digits_does_not_match(self):
        # S12 acceptance-review advisory: the trailing guard must reject alphanumeric glued onto
        # the digits, not just another digit.
        self.assertFalse(gh_gather.references_issue("see #2abc for the ticket", 2))

    def test_hex_color_glued_to_the_digits_does_not_match(self):
        # The reviewer-reproduced false positive: a hex color code starting with the issue number.
        self.assertFalse(gh_gather.references_issue("color: #2E8B57", 2))

    def test_reference_immediately_followed_by_close_paren_still_matches(self):
        self.assertTrue(gh_gather.references_issue("(see #2)", 2))

    def test_reference_immediately_followed_by_comma_still_matches(self):
        self.assertTrue(gh_gather.references_issue("blocks #2, #3", 2))

    def test_reference_at_end_of_string_still_matches(self):
        self.assertTrue(gh_gather.references_issue("Fixes #2", 2))


class OpenPrFalsePositiveFilterIntegrationTests(unittest.TestCase):
    """`_fetch_open_prs` (via `run()`) applies the reference filter end-to-end: a stranger PR
    (body only contains "Phase 2" prose, never "#2") is excluded from `open_prs`; a genuine PR
    survives alongside it."""

    def test_stranger_pr_excluded_genuine_pr_survives(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_open_pr_false_positive")
        result = _run_script(["2", "o/r"], env=env)
        envelope = _parse_envelope(result)
        numbers = [pr["number"] for pr in envelope["open_prs"]]
        self.assertEqual(numbers, [46])  # only the genuine "Fixes #2" PR, never the stranger

    def test_filtered_out_fields_are_stripped_and_closes_issue_is_kept(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_open_pr_false_positive")
        result = _run_script(["2", "o/r"], env=env)
        envelope = _parse_envelope(result)
        pr = envelope["open_prs"][0]
        self.assertNotIn("body", pr)
        self.assertNotIn("closingIssuesReferences", pr)
        self.assertEqual(
            sorted(pr.keys()),
            sorted(["number", "title", "author", "isDraft", "headRefName", "url", "updatedAt",
                    "closes_issue"]),
        )
        # The one derived field that survives the strip: PR #46 carries
        # `closingIssuesReferences: [{"number": 2}]`, so it CLOSES the issue, not merely mentions
        # it — the distinction `branching.pr_belongs_to_issue` keys on.
        self.assertIs(pr["closes_issue"], True)


class ClosesIssueDerivationTests(unittest.TestCase):
    """`closes_issue` is derived from GitHub's resolved link set, never from `Fixes #<N>` body
    prose — the layer below `branching.pr_belongs_to_issue`."""

    def test_a_mentioning_pr_survives_the_filter_with_closes_issue_false(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="gh_gather_epic_pr_mention")
        result = _run_script(["2", "o/r"], env=env)
        envelope = _parse_envelope(result)
        # The epic integration PR genuinely mentions `#2` (it lists its stories), so it MUST still
        # survive `references_issue` — this filter's job is false-positive removal, not ownership.
        self.assertEqual([pr["number"] for pr in envelope["open_prs"]], [245])
        # Ownership is the separate question, and the answer is no: it closes the EPIC, not #2.
        self.assertIs(envelope["open_prs"][0]["closes_issue"], False)


if __name__ == "__main__":
    unittest.main()
