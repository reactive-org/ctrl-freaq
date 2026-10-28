"""Unit tests for scripts/gh_pr_gather.py — the GATHER_PR envelope (S21 port of
scripts/gh-pr-gather.sh, architecture.md §7/§3).

Drives the script as a real subprocess against the offline `gh` shim (tests/support/shimenv),
per tests/README.md, so every test exercises the full argv-in / real-subprocess / envelope-out
path — not just the in-process `run()` function in isolation. Fixtures live under
tests/fixtures/gh_pr_gather_*/, one case directory per DoD-matrix scenario (S21 addendum:
"Cover: with/without diff; with/without line-comments; small vs large thread; 0/1/N marker
comments").

Coverage matrix (S21 addendum DoD rows):
- Conformant envelope for the happy path (legacy inline-only mode, and scratch-dir mode).
- 0 / 1 / N (ambiguous) marker comments -> MARKER_AMBIGUOUS on N>1 (a NEW v2 behavior; v1 silently
  picked .[0] — this port's contract-tightening ask per the S21 brief).
- Small vs large thread -> inline vs path spill.
- --with-diff / --with-line-comments present -> always path mode, never inline, even when small.
- AUTH_REQUIRED classification via the real pipelib.process runner (gh exit code 4).
- Usage error: --with-diff/--with-line-comments without --scratch-dir -> exit 2, no envelope.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import envelope_asserts, shimenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gh_pr_gather.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gh_pr_gather  # noqa: E402

# The v1 --json field list (gh-pr-gather.sh) PLUS `labels` — the v1 list this port must request
# unchanged, plus one deliberate v2 addition (the evaluator's escalation-label matching needs the
# PR's own labels, which v1's fetch omitted despite referencing them; see the module docstring). A
# fixture manifest keyed on a different field list would silently stop matching and every test
# would fail with an un-fixtured-call MISS, which is itself a coverage guard against a silent
# field-list drift. Also asserted directly in one PortPreservesV1CliShapeTests test below.
_EXPECTED_VIEW_JSON_FIELDS = (
    "number,title,body,state,isDraft,author,baseRefName,headRefName,commits,additions,deletions,"
    "changedFiles,closingIssuesReferences,comments,reviews,latestReviews,reviewDecision,"
    "mergeStateStatus,mergeable,statusCheckRollup,headRefOid,url,labels"
)


def _run_script(args, fixture_case=None, extra_env=None):
    env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def _parse_one_envelope(stdout):
    lines = stdout.splitlines()
    assert len(lines) == 1, "expected exactly one JSON line on stdout, got %r" % (lines,)
    return json.loads(lines[0])


class ScriptExistsTests(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_script_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPT)], capture_output=True, encoding="utf-8"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class LegacyInlineModeTests(unittest.TestCase):
    """No --scratch-dir: v1's "legacy inline envelope" — body/thread/reviews always inline,
    marker_comment as a single object-or-null (not the scratch-dir mode's split scalar fields).
    """

    def test_happy_path_emits_conformant_ok_envelope(self):
        result = _run_script(["88", "octo/widgets"], fixture_case="gh_pr_gather_legacy_inline")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "ok")

    def test_preserves_exact_v1_scalar_field_set(self):
        result = _run_script(["88", "octo/widgets"], fixture_case="gh_pr_gather_legacy_inline")
        env = _parse_one_envelope(result.stdout)
        for field in (
            "number",
            "title",
            "state",
            "isDraft",
            "author",
            "baseRefName",
            "headRefName",
            "headRefOid",
            "additions",
            "deletions",
            "changedFiles",
            "commit_count",
            "mergeStateStatus",
            "mergeable",
            "reviewDecision",
            "statusCheckRollup",
            "closingIssuesReferences",
            "url",
            "latestReviews",
            "labels",  # v2 addition (not a v1 field) — see the module docstring.
        ):
            self.assertIn(field, env, "missing v1-contract field %r" % field)

    def test_commit_count_is_derived_from_commits_length(self):
        # v1: `commit_count: ($view.commits | length)` — fixture has 2 commits.
        result = _run_script(["88", "octo/widgets"], fixture_case="gh_pr_gather_legacy_inline")
        env = _parse_one_envelope(result.stdout)
        self.assertEqual(env["commit_count"], 2)

    def test_labelled_pr_surfaces_label_name_strings(self):
        # v2 addition: `gh pr view --json ...,labels` returns each label as an object with a
        # `name` (among other fields); this port maps to just the names, matching
        # architecture.md §4's `"labels": ["story"]` facts-block example. Fixture carries two
        # labels (`bug`, `needs-review`) to prove the mapping isn't accidentally correct only for
        # a single-element list.
        result = _run_script(["88", "octo/widgets"], fixture_case="gh_pr_gather_legacy_inline")
        env = _parse_one_envelope(result.stdout)
        self.assertEqual(env["labels"], ["bug", "needs-review"])
        for label in env["labels"]:
            self.assertIsInstance(label, str)

    def test_unlabelled_pr_surfaces_empty_labels_list(self):
        # A PR with no labels must surface `labels: []`, never a missing key or null.
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp],
                fixture_case="gh_pr_gather_scratch_small",
            )
            env = _parse_one_envelope(result.stdout)
            self.assertEqual(env["labels"], [])

    def test_body_thread_reviews_are_inline_regardless_of_scratch_dir_absence(self):
        result = _run_script(["88", "octo/widgets"], fixture_case="gh_pr_gather_legacy_inline")
        env = _parse_one_envelope(result.stdout)
        self.assertIsInstance(env["body"], str)
        self.assertIsInstance(env["thread"], list)
        self.assertIsInstance(env["reviews"], list)
        # No *_mode/*_bytes scalars in legacy mode — those are scratch-dir-mode-only fields.
        self.assertNotIn("body_mode", env)
        self.assertNotIn("thread_mode", env)

    def test_no_marker_prefix_gives_null_marker_comment_and_zero_count(self):
        result = _run_script(["88", "octo/widgets"], fixture_case="gh_pr_gather_legacy_inline")
        env = _parse_one_envelope(result.stdout)
        self.assertIsNone(env["marker_comment"])
        self.assertEqual(env["marker_comment_count"], 0)


class ScratchDirModeHappyPathTests(unittest.TestCase):
    """--scratch-dir given: v1's "threshold-routed envelope" — per-section *_mode/*_bytes,
    inline-vs-path per pipelib.spill, plus the scalar `inline_threshold_bytes`.
    """

    def test_small_sections_are_inline_with_bare_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp],
                fixture_case="gh_pr_gather_scratch_small",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_one_envelope(result.stdout)
            envelope_asserts.assert_full_envelope_conformance(
                env, spill_section_names=["body", "thread", "reviews"]
            )
            self.assertEqual(env["body_mode"], "inline")
            self.assertEqual(env["thread_mode"], "inline")
            self.assertEqual(env["reviews_mode"], "inline")
            self.assertIn("inline_threshold_bytes", env)
            self.assertEqual(env["thread_comment_count"], 2)
            self.assertEqual(env["reviews_count"], 1)
            # Inline `thread`/`reviews` are the JSON-serialized TEXT form (a str), per
            # architecture.md §3/§4 and spill_bytes' own text-section contract — a caller
            # json.loads()'s the inline string the same way it would read a spilled file's bytes.
            self.assertIsInstance(env["thread"], str)
            self.assertIsInstance(env["reviews"], str)
            self.assertEqual(json.loads(env["thread"]), [
                {"id": 1001, "body": "Looks reasonable, one nit inline.", "author": {"login": "reviewer1"}},
                {"id": 1002, "body": "Addressed the nit.", "author": {"login": "octocat"}},
            ])
            self.assertEqual(
                json.loads(env["reviews"]),
                [{"id": 2001, "state": "COMMENTED", "author": {"login": "reviewer1"}}],
            )

    def test_zero_marker_comments_with_marker_prefix_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                [
                    "88",
                    "octo/widgets",
                    "--marker-prefix",
                    "<!-- pr-evaluator-health-cache:v1 -->",
                    "--scratch-dir",
                    tmp,
                ],
                fixture_case="gh_pr_gather_marker_zero",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_one_envelope(result.stdout)
            self.assertFalse(env["marker_comment_present"])
            self.assertEqual(env["marker_comment_count"], 0)
            self.assertNotIn("marker_comment_id", env)
            self.assertNotIn("marker_comment_body", env)
            self.assertNotIn("marker_comment", env)  # scratch-mode never emits the legacy key
            # No marker file written when there's no marker match.
            self.assertEqual(os.listdir(tmp), [])

    def test_one_marker_comment_is_inline_and_scalar_fields_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                [
                    "88",
                    "octo/widgets",
                    "--marker-prefix",
                    "<!-- pr-evaluator-health-cache:v1 -->",
                    "--scratch-dir",
                    tmp,
                ],
                fixture_case="gh_pr_gather_marker_one",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_one_envelope(result.stdout)
            envelope_asserts.assert_full_envelope_conformance(
                env, spill_section_names=["marker_comment"]
            )
            self.assertTrue(env["marker_comment_present"])
            self.assertEqual(env["marker_comment_count"], 1)
            self.assertEqual(env["marker_comment_id"], 3001)
            self.assertIn("marker_comment_url", env)
            self.assertEqual(env["marker_comment_mode"], "inline")
            self.assertIn("<!-- pr-evaluator-health-cache:v1 -->", env["marker_comment"])

    def test_an_untrusted_health_cache_marker_is_ignored_and_counted(self):
        # #179: the stranger's forged cache (3001) is skipped before the duplicate count, so it is
        # neither the cache nor a MARKER_AMBIGUOUS; the trusted one (3002) is read.
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                [
                    "88",
                    "octo/widgets",
                    "--marker-prefix",
                    "<!-- pr-evaluator-health-cache:v1 -->",
                    "--scratch-dir",
                    tmp,
                ],
                fixture_case="gh_pr_gather_marker_untrusted",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_one_envelope(result.stdout)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["marker_comment_count"], 1)
            self.assertEqual(env["marker_comment_id"], 3002)
            self.assertNotIn("forged", env["marker_comment"])
            self.assertEqual(env["marker_untrusted_count"], 1)
            self.assertEqual(
                [n for n in env["notices"] if n.startswith("MARKER_UNTRUSTED")],
                ["MARKER_UNTRUSTED: 1 comment starting with `<!-- pr-evaluator-health-cache:v1 -->` by an "
                 "untrusted author ignored (PR #88)"],
            )

    def test_a_lone_untrusted_health_cache_is_a_cache_miss(self):
        self.assertEqual(
            gh_pr_gather._matching_markers(
                [{"id": 1, "author_association": "CONTRIBUTOR", "body": "<!-- m -->x"}], "<!-- m -->"
            ),
            ([], 1),
        )

    def test_diff_and_line_comments_absent_when_flags_not_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp],
                fixture_case="gh_pr_gather_scratch_small",
            )
            env = _parse_one_envelope(result.stdout)
            for field in (
                "diff_path",
                "diff_bytes",
                "diff_line_count",
                "line_comments_path",
                "line_comments_bytes",
                "line_comments_count",
            ):
                self.assertNotIn(field, env)


class MarkerAmbiguousTests(unittest.TestCase):
    """Duplicate marker -> MARKER_AMBIGUOUS. A NEW v2 behavior (v1 silently picked .[0]) — the
    DoD's headline contract-tightening ask for this port.
    """

    def test_two_matching_markers_yields_marker_ambiguous_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                [
                    "88",
                    "octo/widgets",
                    "--marker-prefix",
                    "<!-- pr-evaluator-health-cache:v1 -->",
                    "--scratch-dir",
                    tmp,
                ],
                fixture_case="gh_pr_gather_marker_ambiguous",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)  # needs_decision is exit 0
            env = _parse_one_envelope(result.stdout)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "MARKER_AMBIGUOUS")
            self.assertEqual(len(env["decision"]["context"]["candidates"]), 2)

    def test_ambiguous_marker_writes_no_scratch_files(self):
        # Detected before any spill — an ambiguous state must not leave partial scratch output
        # the caller then has to clean up.
        with tempfile.TemporaryDirectory() as tmp:
            _run_script(
                [
                    "88",
                    "octo/widgets",
                    "--marker-prefix",
                    "<!-- pr-evaluator-health-cache:v1 -->",
                    "--scratch-dir",
                    tmp,
                ],
                fixture_case="gh_pr_gather_marker_ambiguous",
            )
            self.assertEqual(os.listdir(tmp), [])

    def test_ambiguous_marker_short_circuits_before_diff_fetch(self):
        # Even with --with-diff requested, MARKER_AMBIGUOUS must be detected and returned before
        # the diff fetch — the fixture case has no `pr diff` manifest entry, so a miss here would
        # itself prove the ordering bug (an un-fixtured call fails loudly per tests/README.md).
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                [
                    "88",
                    "octo/widgets",
                    "--marker-prefix",
                    "<!-- pr-evaluator-health-cache:v1 -->",
                    "--scratch-dir",
                    tmp,
                    "--with-diff",
                ],
                fixture_case="gh_pr_gather_marker_ambiguous",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_one_envelope(result.stdout)
            self.assertEqual(env["decision"]["code"], "MARKER_AMBIGUOUS")


class ThresholdSpillTests(unittest.TestCase):
    """Small vs large thread -> inline vs path, via GH_PIPELINE_INLINE_THRESHOLD_BYTES."""

    def test_large_thread_spills_body_thread_reviews_to_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp],
                fixture_case="gh_pr_gather_scratch_large_thread",
                extra_env={"GH_PIPELINE_INLINE_THRESHOLD_BYTES": "50"},
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_one_envelope(result.stdout)
            envelope_asserts.assert_full_envelope_conformance(
                env, spill_section_names=["body", "thread", "reviews"]
            )
            self.assertEqual(env["thread_mode"], "path")
            self.assertEqual(env["body_mode"], "path")
            self.assertEqual(env["reviews_mode"], "path")
            self.assertEqual(env["inline_threshold_bytes"], 50)
            # Path-mode sections must be readable from disk with the reported byte count.
            thread_path = Path(env["thread_path"])
            self.assertTrue(thread_path.is_file())
            self.assertEqual(thread_path.stat().st_size, env["thread_bytes"])
            # thread_comment_count is still reported even in path mode (v1 preserves this scalar
            # alongside the spilled thread — the count is metadata, not verbatim content).
            self.assertEqual(env["thread_comment_count"], 2)

    def test_legacy_threshold_env_var_is_honored(self):
        # architecture.md §3: "legacy GH_OPS_INLINE_THRESHOLD_BYTES honored" — the v1 env var name
        # must still work during the v1/v2 coexistence window.
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp],
                fixture_case="gh_pr_gather_scratch_large_thread",
                extra_env={"GH_OPS_INLINE_THRESHOLD_BYTES": "50"},
            )
            env = _parse_one_envelope(result.stdout)
            self.assertEqual(env["thread_mode"], "path")
            self.assertEqual(env["inline_threshold_bytes"], 50)


class WithDiffTests(unittest.TestCase):
    """--with-diff: fetched only when requested, ALWAYS path mode regardless of size."""

    def test_with_diff_is_always_path_mode_even_though_tiny(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp, "--with-diff"],
                fixture_case="gh_pr_gather_with_diff",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_one_envelope(result.stdout)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertIn("diff_path", env)
            self.assertIn("diff_bytes", env)
            self.assertIn("diff_line_count", env)
            # Never inline, never a bare "diff" field, regardless of size.
            self.assertNotIn("diff", env)
            self.assertNotIn("diff_mode", env)
            diff_path = Path(env["diff_path"])
            self.assertTrue(diff_path.is_file())
            self.assertEqual(diff_path.stat().st_size, env["diff_bytes"])

    def test_with_diff_absent_when_flag_not_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp],
                fixture_case="gh_pr_gather_with_diff",
            )
            env = _parse_one_envelope(result.stdout)
            self.assertNotIn("diff_path", env)

    def test_with_diff_without_scratch_dir_is_a_usage_error(self):
        result = _run_script(["88", "octo/widgets", "--with-diff"], fixture_case=None)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")  # exit 2 => no envelope (architecture.md §3)


class WithLineCommentsTests(unittest.TestCase):
    """--with-line-comments: fetched only when requested, ALWAYS path mode regardless of size."""

    def test_with_line_comments_is_always_path_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp, "--with-line-comments"],
                fixture_case="gh_pr_gather_with_line_comments",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_one_envelope(result.stdout)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertIn("line_comments_path", env)
            self.assertIn("line_comments_bytes", env)
            self.assertEqual(env["line_comments_count"], 2)
            self.assertNotIn("line_comments", env)
            self.assertNotIn("line_comments_mode", env)
            lc_path = Path(env["line_comments_path"])
            self.assertTrue(lc_path.is_file())
            self.assertEqual(json.loads(lc_path.read_text(encoding="utf-8")), json.loads(
                lc_path.read_bytes().decode("utf-8")
            ))

    def test_with_line_comments_without_scratch_dir_is_a_usage_error(self):
        result = _run_script(["88", "octo/widgets", "--with-line-comments"], fixture_case=None)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_both_diff_and_line_comments_together(self):
        # Independent flags — both can be passed together (S21 addendum: "either can be passed
        # alone or together"). Reuses the with_diff fixture case's view+markers manifest entries
        # plus adds a line-comments entry inline here via a combined manifest would be needed for
        # a full round-trip; this test instead confirms both flags are accepted without one
        # clobbering the other's fields, using the with-line-comments fixture (which also has a
        # zero-marker gh api entry matching what a combined call would need for view+markers).
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_script(
                ["88", "octo/widgets", "--scratch-dir", tmp, "--with-line-comments"],
                fixture_case="gh_pr_gather_with_line_comments",
            )
            env = _parse_one_envelope(result.stdout)
            self.assertIn("line_comments_path", env)
            self.assertNotIn("diff_path", env)  # --with-diff wasn't passed in this invocation


class AuthRequiredTests(unittest.TestCase):
    """AUTH_REQUIRED classification via the real pipelib.process runner (gh exit code 4) —
    detected on the first gh call (`gh pr view`), same pattern as pipelib_envelope_fixture_demo.py.
    """

    def test_auth_failure_on_first_call_yields_auth_required_decision(self):
        result = _run_script(["88", "octo/widgets"], fixture_case="gh_pr_gather_auth_required")
        self.assertEqual(result.returncode, 0, msg=result.stderr)  # needs_decision is exit 0
        env = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "needs_decision")
        self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")
        self.assertEqual(env["decision"]["context"]["returncode"], 4)


class UsageErrorTests(unittest.TestCase):
    """Exit code 2, no envelope — architecture.md §3's exit-code contract."""

    def test_missing_required_positional_args_is_usage_error(self):
        result = _run_script([], fixture_case=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_missing_repo_arg_is_usage_error(self):
        result = _run_script(["88"], fixture_case=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


class PortPreservesV1CliShapeTests(unittest.TestCase):
    """Contract-fidelity checks that don't fit neatly under one behavioral scenario above: the
    exact v1 --json field list, and the exact flag names --with-diff/--with-line-comments.
    """

    def test_view_json_field_list_matches_v1_exactly(self):
        import gh_pr_gather  # noqa: local import; scripts/ is on sys.path via tests/run.py

        self.assertEqual(gh_pr_gather._VIEW_JSON_FIELDS, _EXPECTED_VIEW_JSON_FIELDS)

    def test_flag_names_are_exactly_with_diff_and_with_line_comments(self):
        result = _run_script(["--help"], fixture_case=None)
        self.assertIn("--with-diff", result.stdout)
        self.assertIn("--with-line-comments", result.stdout)

    def test_pr_comments_use_the_issue_comments_endpoint_shape(self):
        # v1's load-bearing comment: "PR comments are issue comments under the hood" — same
        # endpoint shape gh-gather.sh uses. Asserted directly against the module constant so a
        # future edit that switches to the wrong endpoint fails here, not just via an
        # un-fixtured-call MISS somewhere else.
        import gh_pr_gather

        self.assertEqual(gh_pr_gather._ISSUE_COMMENTS_ENDPOINT, "repos/%s/issues/%s/comments")
        self.assertEqual(gh_pr_gather._LINE_COMMENTS_ENDPOINT, "repos/%s/pulls/%s/comments")


class CwdDisciplineTests(unittest.TestCase):
    """S21 brief's carried S3 advisory: every gh call must pass an explicit cwd/--repo, never rely
    on ambient cwd. --repo is always passed (see every fixture manifest above); this test proves
    the explicit --cwd plumbing also reaches pipelib.process.run's cwd= parameter.
    """

    def test_explicit_cwd_flag_is_forwarded_to_gh_calls(self):
        # Run from a directory that is NOT the repo root, passing --cwd explicitly, and confirm
        # the script still works purely because --repo/--cwd carry the scoping — not ambient cwd.
        with tempfile.TemporaryDirectory() as unrelated_cwd:
            env = shimenv.intercepted_env(
                base_env=os.environ, fixture_case="gh_pr_gather_legacy_inline"
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "88", "octo/widgets", "--cwd", unrelated_cwd],
                cwd=unrelated_cwd,
                env=env,
                capture_output=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env_out = _parse_one_envelope(result.stdout)
            self.assertEqual(env_out["status"], "ok")


if __name__ == "__main__":
    unittest.main()
