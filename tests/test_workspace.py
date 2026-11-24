"""Unit tests for scripts/workspace.py — the S4 workspace-lifecycle owner (architecture.md §6, §3,
§12; docs/implementation.md S4).

``workspace.py`` shells out only to real ``git`` (an allowed spawnable, architecture.md §1) against
a temp origin+clone built by ``tests/support/gitsandbox.py`` — per architecture.md §10, "Git
sandbox: ... no shim needed," these tests never touch ``tests/shim/gh`` and never invoke ``gh`` at
all. Hook-execution tests additionally author a small ``CLAUDE.md`` with
``<!-- worktree-setup/teardown -->`` marker blocks directly in the sandboxed clone (config-block
fixtures, per the S4 DoD's "hook cases use config-block fixtures" — authored inline here rather
than reusing the S21 interior-only fixture bodies, since every test already builds a full repo
root via gitsandbox and a marker block is a two-line addition to that same root).

Every subprocess invocation of ``workspace.py`` itself goes through :func:`_run_cli`, mirroring
``tests/test_config_block.py``'s ``_run_cli`` exactly (real subprocess, explicit UTF-8 decoding,
inherited environment) — the end-to-end path a caller (a v2 prep script, S6+) actually uses.

Design notes these tests pin down (see the implementor report's "Notes for the reviewer" for the
full rationale):

- ``remove --work`` counts commits reachable from ``HEAD`` but from no ``origin/*`` ref (the
  subcommand has no ``--base``, and this needs neither a base branch nor the network) — so
  reclaiming an abandoned workspace still works offline and on a clone with no recorded
  ``origin/HEAD``.
- The dirty/unpushed ``remove --work`` refusal uses the closed-set ``AMBIGUOUS`` code (no bespoke
  code exists for this hazard; prd.md itself groups "a dirty root" under "ambiguous state" as a
  mechanical blocker).
"""

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
WORKSPACE_PY = SCRIPTS_DIR / "workspace.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import config_block  # noqa: E402  (import after sys.path setup, by necessity)
import workspace  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR  # noqa: E402
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _run_cli(args, cwd=None):
    """Invoke ``workspace.py`` as a real subprocess. Returns ``(returncode, stdout_text,
    stderr_text)`` — mirrors ``tests/test_config_block.py``'s ``_run_cli`` exactly."""
    completed = subprocess.run(
        [sys.executable, str(WORKSPACE_PY)] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ),
    )
    return (
        completed.returncode,
        completed.stdout.decode("utf-8"),
        completed.stderr.decode("utf-8"),
    )


def _parse_one_envelope(stdout_text):
    """Parse exactly one JSON object from ``stdout_text`` (architecture.md §3's "exactly one JSON
    envelope on stdout"). Fails the test if it isn't exactly one non-blank line of valid JSON."""
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(
            "expected exactly one non-blank stdout line (the envelope), got %d: %r"
            % (len(lines), lines)
        )
    return json.loads(lines[0])


def _git(args, cwd):
    result = subprocess.run(
        ["git"] + args, cwd=str(cwd), capture_output=True, encoding="utf-8", check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("git %s (cwd=%s) failed: %s" % (" ".join(args), cwd, result.stderr))
    return result.stdout.strip()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class WorkspaceHelperUnitTests(unittest.TestCase):
    """Direct, in-process tests of workspace's pure helper functions — no git, no subprocess."""

    def test_slugify_ref_collapses_slashes(self):
        self.assertEqual(workspace._slugify_ref("epic/42-journal"), "epic-42-journal")

    def test_slugify_ref_leaves_simple_name_untouched(self):
        self.assertEqual(workspace._slugify_ref("main"), "main")

    def test_slugify_ref_replaces_other_unsafe_characters(self):
        self.assertEqual(workspace._slugify_ref("feature@x!y"), "feature-x-y")

    def test_work_path_is_dot_worktrees_branch(self):
        path = workspace._work_path("/repo", "feature-x")
        self.assertEqual(path, Path("/repo/.worktrees/feature-x").resolve())

    def test_read_path_is_dot_worktrees_ro_prefixed_slug(self):
        path = workspace._read_path("/repo", "epic/42-journal")
        self.assertEqual(path, Path("/repo/.worktrees/ro-epic-42-journal").resolve())

    def test_parse_command_list_extracts_backtick_span_ignoring_description(self):
        lines = [
            "- `echo one` — does a thing",
            "- `echo two` — does another thing",
            "not a list item",
        ]
        self.assertEqual(workspace._parse_command_list(lines), ["echo one", "echo two"])

    def test_parse_command_list_empty_interior_yields_empty_list(self):
        self.assertEqual(workspace._parse_command_list([]), [])

    def test_find_includes_matches_path_like_at_tokens_only(self):
        # @anthropic has no slash and no dotted extension, so it must NOT be treated as an
        # include (v1's find_includes: excludes bare @mentions). workspace.py carried its own
        # byte-identical copy of this scan until v3.x folded hook discovery onto
        # config_block.read_block_anywhere; the behavior is unchanged and still exercised here
        # because it is the hook-discovery candidate walk, which nothing else covers.
        text = "See @docs/COMMANDS.md and @anthropic and also @CONTRIBUTING.md for more."
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "f.md"
            _write(f, text)
            includes = config_block.find_includes_one_level(f)
        self.assertEqual(includes, ["docs/COMMANDS.md", "CONTRIBUTING.md"])

    def test_find_includes_nonexistent_file_returns_empty(self):
        self.assertEqual(config_block.find_includes_one_level(Path("/no/such/file.md")), [])

    def test_tail_lines_keeps_only_last_n(self):
        text = "\n".join("line%d" % i for i in range(1, 101))
        result = workspace._tail_lines(text, 50)
        self.assertEqual(result.splitlines()[0], "line51")
        self.assertEqual(result.splitlines()[-1], "line100")

    def test_tail_lines_handles_empty_text(self):
        self.assertEqual(workspace._tail_lines("", 50), "")

    def test_tail_lines_handles_none(self):
        self.assertEqual(workspace._tail_lines(None, 50), "")


class WorktreesExcludeMaintenanceTests(unittest.TestCase):
    """`.worktrees/` idempotent exclude maintenance (architecture.md §6, D6 fix: this now writes
    the repo's `info/exclude` — resolved via `git rev-parse --git-common-dir`, never
    `<root>/.gitignore` — so the write can never appear in `git status --porcelain` and can never
    trip root-freshness's `ROOT_DIRTY` check on its own idempotent bootstrap write (the D6 defect:
    a prep script calling `ensure` more than once in one clone, e.g. the planner's composite
    epic+story session, fell off prep mid-run). `_ensure_worktrees_excluded` needs a real git repo
    (it shells out to `git rev-parse`), so `setUp` runs a plain `git init` — no origin/clone needed
    for these pure exclude-mechanics tests."""

    def setUp(self):
        import tempfile
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp_ctx.name).resolve()
        self.addCleanup(self._tmp_ctx.cleanup)
        _git(["init", "-q"], self.tmp)
        self.exclude_path = self.tmp / ".git" / "info" / "exclude"

    def test_creates_exclude_from_scratch_when_info_dir_present(self):
        # `git init` already creates `.git/info/exclude` (with template comments) by default —
        # this asserts the append path, not the from-scratch-file-creation path (covered below by
        # deleting it first).
        changed = workspace._ensure_worktrees_excluded(self.tmp)
        self.assertTrue(changed)
        self.assertIn(".worktrees/\n", self.exclude_path.read_text(encoding="utf-8"))

    def test_creates_exclude_and_info_dir_from_scratch_when_both_absent(self):
        import shutil
        shutil.rmtree(self.tmp / ".git" / "info")
        self.assertFalse(self.exclude_path.exists())
        changed = workspace._ensure_worktrees_excluded(self.tmp)
        self.assertTrue(changed)
        self.assertEqual(self.exclude_path.read_text(encoding="utf-8"), ".worktrees/\n")

    def test_appends_to_existing_exclude_missing_the_entry(self):
        _write(self.exclude_path, "*.pyc\n")
        changed = workspace._ensure_worktrees_excluded(self.tmp)
        self.assertTrue(changed)
        self.assertEqual(self.exclude_path.read_text(encoding="utf-8"), "*.pyc\n.worktrees/\n")

    def test_appends_newline_first_when_file_has_no_trailing_newline(self):
        _write(self.exclude_path, "*.pyc")
        workspace._ensure_worktrees_excluded(self.tmp)
        self.assertEqual(self.exclude_path.read_text(encoding="utf-8"), "*.pyc\n.worktrees/\n")

    def test_second_call_is_a_byte_level_noop_and_leaves_mtime_untouched(self):
        workspace._ensure_worktrees_excluded(self.tmp)
        mtime_before = self.exclude_path.stat().st_mtime_ns

        changed = workspace._ensure_worktrees_excluded(self.tmp)
        self.assertFalse(changed)
        mtime_after = self.exclude_path.stat().st_mtime_ns
        self.assertEqual(
            mtime_before, mtime_after,
            "an already-present entry must leave the file byte-for-byte untouched (not even mtime)",
        )

    def test_entry_present_anywhere_in_file_is_recognized_not_just_at_end(self):
        _write(self.exclude_path, ".worktrees/\n*.pyc\n")
        changed = workspace._ensure_worktrees_excluded(self.tmp)
        self.assertFalse(changed)
        self.assertEqual(self.exclude_path.read_text(encoding="utf-8"), ".worktrees/\n*.pyc\n")

    def test_tolerates_surrounding_whitespace_on_the_existing_line(self):
        _write(self.exclude_path, "  .worktrees/  \n")
        changed = workspace._ensure_worktrees_excluded(self.tmp)
        self.assertFalse(changed)

    def test_never_touches_gitignore_working_tree_file(self):
        # D6's own regression: the write must land ONLY in info/exclude, never in a working-tree
        # .gitignore this function could see or create.
        changed = workspace._ensure_worktrees_excluded(self.tmp)
        self.assertTrue(changed)
        self.assertFalse((self.tmp / ".gitignore").exists())

    def test_leaves_a_pre_existing_gitignore_bootstrap_line_untouched(self):
        # A .gitignore an earlier (pre-fix) v2 run left behind is harmless dead weight — this
        # function must not migrate, rewrite, or remove it (D6 fix constraint 2).
        _write(self.tmp / ".gitignore", ".worktrees/\n")
        changed = workspace._ensure_worktrees_excluded(self.tmp)
        self.assertTrue(changed)  # info/exclude still gets its own entry
        self.assertEqual(
            (self.tmp / ".gitignore").read_text(encoding="utf-8"), ".worktrees/\n",
            "a pre-existing .gitignore bootstrap line must be left byte-for-byte alone",
        )

    def test_git_common_dir_resolves_correctly_from_inside_a_linked_worktree(self):
        # Verified live against real git behavior (not assumed): --git-common-dir from INSIDE a
        # linked worktree resolves to the MAIN repo's shared .git dir (never the worktree's own
        # private per-worktree admin dir, which --git-dir would return instead) — the property
        # this fix relies on for "applies to all linked worktrees."
        _git(["config", "user.email", "test@test.com"], self.tmp)
        _git(["config", "user.name", "test"], self.tmp)
        _write(self.tmp / "README.md", "seed\n")
        _git(["add", "README.md"], self.tmp)
        _git(["commit", "-q", "-m", "seed"], self.tmp)
        wt_path = self.tmp / "wt"
        _git(["worktree", "add", "-q", "-b", "feature-x", str(wt_path)], self.tmp)
        self.addCleanup(lambda: _git(["worktree", "remove", "-f", str(wt_path)], self.tmp))

        common_from_root = workspace._git_common_dir(self.tmp)
        common_from_worktree = workspace._git_common_dir(wt_path)
        self.assertEqual(common_from_root, common_from_worktree)
        self.assertEqual(common_from_root, (self.tmp / ".git").resolve())

        # A write from inside the worktree's context must land in the SAME shared file.
        changed = workspace._ensure_worktrees_excluded(wt_path)
        self.assertTrue(changed)
        self.assertIn(".worktrees/\n", self.exclude_path.read_text(encoding="utf-8"))


def _seed_repo_with_hooks(seed_dir, setup_block=None, teardown_block=None, in_file="CLAUDE.md"):
    """Write a small ``CLAUDE.md`` (or ``COMMANDS.md``) with the given marker blocks into an
    already-cloned seed working directory, then commit + push it — a config-block fixture built
    inline (S4 DoD: "hook cases use config-block fixtures ... or author small ones")."""
    lines = []
    if setup_block is not None:
        lines.append("<!-- worktree-setup -->")
        lines.extend(setup_block)
        lines.append("<!-- /worktree-setup -->")
        lines.append("")
    if teardown_block is not None:
        lines.append("<!-- worktree-teardown -->")
        lines.extend(teardown_block)
        lines.append("<!-- /worktree-teardown -->")
        lines.append("")
    _write(Path(seed_dir) / in_file, "\n".join(lines) + "\n")
    _git(["add", in_file], seed_dir)
    _git(["commit", "-m", "add worktree hook blocks"], seed_dir)
    _git(["push", "origin", "HEAD:main"], seed_dir)


class WorkspaceGitSandboxTestCase(unittest.TestCase):
    """Shared setup for every test below that needs a real temp origin+clone."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        # No .gitignore pre-seed needed (D6 fix): the `.worktrees/` exclusion now lands in
        # info/exclude, outside the working tree, so it can never appear in `git status
        # --porcelain` and can never trip ROOT_DIRTY on its own idempotent write — a fresh clone's
        # very first ensure/root-freshness check is clean with no bootstrap workaround required.

    def _run(self, args):
        rc, out, err = _run_cli(args + ["--root", str(self.root)])
        return rc, out, err

    def _envelope(self, args):
        rc, out, err = self._run(args)
        self.assertEqual(rc, EXIT_OK, "stderr: %s" % err)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope


class DefaultBranchTests(unittest.TestCase):
    """``workspace.default_branch`` — the derivation that replaced the hardcoded ``"main"``.

    Order is the contract, not an implementation detail: ``git symbolic-ref
    refs/remotes/origin/HEAD`` answers first and offline, so no fixtured prep case ever reaches the
    ``gh`` fallback (the shim matches argv by exact equality and exits 2 on a miss, and
    ``prep_workspace_close``'s branch resolution is deliberately gh-free)."""

    def setUp(self):
        workspace._DEFAULT_BRANCH_CACHE.clear()
        self.addCleanup(workspace._DEFAULT_BRANCH_CACHE.clear)

    def _sandbox(self, initial_branch="main"):
        origin = gitsandbox.mk_origin(initial_branch=initial_branch)
        self.addCleanup(origin.cleanup)
        clone = gitsandbox.mk_clone(origin)
        self.addCleanup(clone.cleanup)
        return origin, clone

    def test_derives_main_from_origin_head(self):
        _origin, clone = self._sandbox()
        self.assertEqual(workspace.default_branch(str(clone.path)), "main")

    def test_derives_a_non_main_default_branch(self):
        _origin, clone = self._sandbox(initial_branch="trunk")
        self.assertEqual(workspace.default_branch(str(clone.path)), "trunk")

    def test_resolves_from_inside_a_linked_worktree(self):
        _origin, clone = self._sandbox(initial_branch="trunk")
        envelope = _parse_one_envelope(
            _run_cli(["ensure", "--work", "f-x", "--base", "trunk", "--root", str(clone.path)])[1]
        )
        self.assertEqual(workspace.default_branch(envelope["path"]), "trunk")

    def test_is_cached_per_main_root(self):
        _origin, clone = self._sandbox()
        workspace.default_branch(str(clone.path))
        self.assertEqual(
            workspace._DEFAULT_BRANCH_CACHE[str(clone.path.resolve())], "main"
        )

    def test_origin_head_absent_and_no_gh_is_a_hard_fail_naming_the_remedy(self):
        _origin, clone = self._sandbox()
        subprocess.run(
            ["git", "remote", "set-head", "origin", "--delete"],
            cwd=str(clone.path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        # No `gh` on PATH at all (the poison dir would answer with a failure too, but an empty
        # PATH proves the derivation cannot silently succeed by some other route).
        completed = subprocess.run(
            [sys.executable, str(WORKSPACE_PY), "root-status", "--root", str(clone.path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )
        self.assertNotEqual(completed.returncode, EXIT_OK)
        stderr = completed.stderr.decode("utf-8")
        self.assertIn("default branch", stderr)
        self.assertIn("git remote set-head origin --auto", stderr)

    def test_gh_answers_when_origin_head_is_absent(self):
        """The rung-2 fallback: an old clone with no ``origin/HEAD`` still resolves, via `gh`.
        This is the ONLY test in the suite that lets the derivation reach `gh` — every other path
        is answered offline by rung 1, which is what keeps the fixtured prep cases gh-free."""
        _origin, clone = self._sandbox(initial_branch="trunk")
        subprocess.run(
            ["git", "remote", "set-head", "origin", "--delete"],
            cwd=str(clone.path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        completed = subprocess.run(
            [sys.executable, str(WORKSPACE_PY), "root-status", "--root", str(clone.path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=shimenv.intercepted_env(fixture_case="workspace_default_branch_gh_fallback"),
        )
        self.assertEqual(completed.returncode, EXIT_OK, completed.stderr.decode("utf-8"))
        envelope = _parse_one_envelope(completed.stdout.decode("utf-8"))
        self.assertEqual(envelope["branch"], "trunk")


class ContainsCommitTests(WorkspaceGitSandboxTestCase):
    """`contains_commit` is tri-state on purpose: `prep_planner`'s `story-own-branch` row must be
    able to tell "this branch is behind" from "I cannot tell", because the two get different
    notices and neither may be answered by fetching. `_build_attach` folds both onto its
    pre-refactor `is True`, so this behaviour is additive there, not a change."""

    def test_head_contains_its_own_commit_and_its_ancestors(self):
        head = _git(["rev-parse", "HEAD"], self.root)
        # A commit is its own ancestor — the identical-SHA case the planner row turns on.
        self.assertIs(workspace.contains_commit(self.root, head), True)

    def test_a_commit_only_on_a_sibling_branch_is_not_contained(self):
        _git(["checkout", "-b", "sibling"], self.root)
        _write(self.root / "sibling.txt", "sibling\n")
        _git(["add", "-A"], self.root)
        _git(["commit", "-m", "sibling commit"], self.root)
        sibling_head = _git(["rev-parse", "HEAD"], self.root)
        _git(["checkout", "main"], self.root)
        self.assertIs(workspace.contains_commit(self.root, sibling_head), False)

    def test_an_object_absent_from_the_repo_is_unanswerable_not_false(self):
        # The never-fetched tip: git exits 128, which must NOT be reported as "not contained".
        absent = "0" * 39 + "1"
        self.assertIsNone(workspace.contains_commit(self.root, absent))


class EnsureWorkTests(WorkspaceGitSandboxTestCase):
    def test_create_reports_facts_and_is_not_reused(self):
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["op"], "ensure")
        self.assertEqual(envelope["kind"], "work")
        self.assertEqual(envelope["branch"], "feature-x")
        self.assertEqual(envelope["base_ref"], "main")
        self.assertFalse(envelope["reused"])
        self.assertFalse(envelope["dirty"])
        self.assertEqual(envelope["unpushed_commits"], 0)
        self.assertTrue((self.root / ".worktrees" / "feature-x").is_dir())
        self.assertEqual(len(envelope["sha"]), 40)

    def test_reuse_of_existing_worktree_reports_reused_true(self):
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertTrue(envelope["reused"])

    def test_existing_origin_branch_checks_out_at_the_branch_head_not_base(self):
        # The regression test (S6-pilot fixup): an EXISTING branch on origin (e.g. a PR head) with
        # no worktree yet and no local ref in this clone — the exact cross-compat/fresh-clone shape
        # an evaluator hits. Before the fix this unconditionally created the worktree at
        # origin/main's head; it must land at origin/feature-x's head instead.
        other_clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other_clone.cleanup)
        _git(["checkout", "-b", "feature-x"], other_clone.path)
        _write(other_clone.path / "pr-only.txt", "content only on the PR branch\n")
        _git(["add", "pr-only.txt"], other_clone.path)
        _git(["commit", "-m", "PR branch commit main does not have"], other_clone.path)
        _git(["push", "origin", "feature-x"], other_clone.path)
        expected_sha = _git(["rev-parse", "feature-x"], other_clone.path)
        main_sha = _git(["rev-parse", "origin/main"], self.root)
        self.assertNotEqual(expected_sha, main_sha, "the fixture must actually diverge from main")

        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["reused"])
        self.assertEqual(envelope["base_ref"], "main", "base_ref stays the PR base for unpushed-count semantics")
        self.assertEqual(
            envelope["sha"], expected_sha,
            "worktree HEAD must be origin/feature-x's head, not origin/main's",
        )
        wt = self.root / ".worktrees" / "feature-x"
        self.assertEqual(_git(["rev-parse", "HEAD"], wt), expected_sha)
        self.assertTrue((wt / "pr-only.txt").exists(), "must have the PR branch's actual content")
        self.assertEqual(envelope["unpushed_commits"], 0, "worktree HEAD == origin/feature-x, nothing unpushed")

    def test_existing_local_branch_with_no_worktree_is_attached_not_recreated(self):
        # A local `feature-x` ref exists (e.g. left behind by a worktree removed earlier) but no
        # worktree currently has it checked out. `git worktree add -b` would fail with "branch
        # already exists"; the fix must attach the existing branch instead of crashing, and still
        # land it at origin/feature-x's head (not leave it at whatever stale commit the local ref
        # pointed to).
        other_clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other_clone.cleanup)
        _git(["checkout", "-b", "feature-x"], other_clone.path)
        _write(other_clone.path / "pr-only.txt", "content only on the PR branch\n")
        _git(["add", "pr-only.txt"], other_clone.path)
        _git(["commit", "-m", "PR branch commit main does not have"], other_clone.path)
        _git(["push", "origin", "feature-x"], other_clone.path)
        expected_sha = _git(["rev-parse", "feature-x"], other_clone.path)

        # Create a LOCAL branch ref in self.root pointing at the stale pre-push main tip, with no
        # worktree attached to it (plain `git branch`, not `checkout -b`, never attaches HEAD).
        stale_sha = _git(["rev-parse", "origin/main"], self.root)
        _git(["branch", "feature-x", stale_sha], self.root)
        self.assertNotEqual(stale_sha, expected_sha)

        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["reused"])
        self.assertEqual(
            envelope["sha"], expected_sha,
            "must fast-forward/reset the pre-existing local branch to origin/feature-x's head",
        )
        wt = self.root / ".worktrees" / "feature-x"
        self.assertEqual(_git(["rev-parse", "HEAD"], wt), expected_sha)
        self.assertTrue((wt / "pr-only.txt").exists())

    def test_reused_worktree_reports_dirty_true_when_it_has_uncommitted_changes(self):
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        _write(self.root / ".worktrees" / "feature-x" / "scratch.txt", "uncommitted\n")
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertTrue(envelope["reused"])
        self.assertTrue(envelope["dirty"])

    def test_reports_unpushed_commit_count_relative_to_base_when_never_pushed(self):
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        wt = self.root / ".worktrees" / "feature-x"
        _write(wt / "new.txt", "content\n")
        _git(["add", "new.txt"], wt)
        _git(["commit", "-m", "one new commit"], wt)
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["unpushed_commits"], 1)

    def test_unpushed_count_relative_to_remote_branch_once_pushed(self):
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        wt = self.root / ".worktrees" / "feature-x"
        _write(wt / "new.txt", "content\n")
        _git(["add", "new.txt"], wt)
        _git(["commit", "-m", "first commit"], wt)
        _git(["push", "origin", "feature-x"], wt)
        _write(wt / "new2.txt", "content2\n")
        _git(["add", "new2.txt"], wt)
        _git(["commit", "-m", "second commit, not pushed"], wt)
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["unpushed_commits"], 1)

    def test_branch_in_use_elsewhere_returns_decision_and_creates_nothing(self):
        import tempfile
        elsewhere = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-elsewhere-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(elsewhere, ignore_errors=True))
        _git(["worktree", "add", str(elsewhere), "-b", "feature-y"], self.root)

        envelope = self._envelope(["ensure", "--work", "feature-y", "--base", "main"])
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "BRANCH_IN_USE")
        self.assertEqual(envelope["decision"]["context"]["existing_path"], str(elsewhere))
        self.assertFalse((self.root / ".worktrees" / "feature-y").exists())

    def test_ensures_worktrees_excluded_via_info_exclude_after_create(self):
        # D6 fix: the exclusion lands in info/exclude, never <root>/.gitignore. Two ensures in a
        # row (D6's own regression) must not duplicate the entry, and — the core of D6 — must not
        # leave the working tree dirty (the self-inflicted-ROOT_DIRTY bug this replaces).
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self._envelope(["ensure", "--work", "feature-y", "--base", "main"])
        text = (self.root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertEqual(text.count(".worktrees/"), 1)
        self.assertFalse((self.root / ".gitignore").exists())
        self.assertEqual(_git(["status", "--porcelain"], self.root), "", "root must stay clean")

    def test_an_off_default_branch_invoker_is_not_gated(self):
        """v3.x: ROOT_NOT_ON_MAIN is retired — the operator picks the branch they stand on, and
        that checkout is what supplies hook config, so gating on it would forbid the very workflow
        this change exists for."""
        _git(["checkout", "-b", "not-main"], self.root)
        self.addCleanup(lambda: _git(["checkout", "main"], self.root))
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue((self.root / ".worktrees" / "feature-x").is_dir())

    def test_a_dirty_invoker_is_not_gated_and_is_reported(self):
        """v3.x: ROOT_DIRTY is retired as a gate. An uncommitted hook edit is the tightest test
        loop there is, so a dirty tree may supply commands — and says so."""
        _write(self.root / "dirty.txt", "uncommitted\n")
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue((self.root / ".worktrees" / "feature-x").is_dir())
        self.assertIn("HOOK_SOURCE_DIRTY", envelope["notices"])
        self.assertTrue(envelope["setup"]["source"]["dirty"])

    def test_a_diverged_invoker_is_not_gated_and_the_worktree_forks_from_origin(self):
        # Diverge the ORIGIN ahead of root, then commit something local to root too. Through v3
        # this was ROOT_DIVERGED; now it proceeds, and the load-bearing assertion is that the new
        # worktree forks from ORIGIN's tip — ensure fetches origin/<base> itself, so the local
        # branch's divergence never leaks into the workspace.
        other_clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other_clone.cleanup)
        _write(other_clone.path / "origin-side.txt", "origin moved\n")
        _git(["add", "origin-side.txt"], other_clone.path)
        _git(["commit", "-m", "origin-side commit"], other_clone.path)
        _git(["push", "origin", "HEAD:main"], other_clone.path)

        origin_sha = _git(["rev-parse", "HEAD"], other_clone.path)
        _write(self.root / "root-side.txt", "root moved locally\n")
        _git(["add", "root-side.txt"], self.root)
        _git(["commit", "-m", "root-side local commit"], self.root)

        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["sha"], origin_sha, "worktree must fork from origin's tip")

    def test_ensure_never_mutates_the_invokers_checkout(self):
        """v3.x replaced the freshness protocol's `git merge --ff-only` (which WROTE the operator's
        checkout) with nothing: ensure fetches origin/<base> and forks the worktree from it, and
        the invoking checkout's HEAD is left exactly where the operator left it."""
        other_clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other_clone.cleanup)
        _write(other_clone.path / "origin-side.txt", "origin moved\n")
        _git(["add", "origin-side.txt"], other_clone.path)
        _git(["commit", "-m", "origin-side commit"], other_clone.path)
        _git(["push", "origin", "HEAD:main"], other_clone.path)
        expected_sha = _git(["rev-parse", "HEAD"], other_clone.path)
        root_sha_before = _git(["rev-parse", "HEAD"], self.root)

        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["sha"], expected_sha, "worktree forks from origin's new HEAD")
        self.assertEqual(
            _git(["rev-parse", "HEAD"], self.root), root_sha_before,
            "the invoker's checkout must be left exactly as the operator left it",
        )

    def test_missing_base_flag_is_a_usage_error(self):
        rc, out, err = self._run(["ensure", "--work", "feature-x"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")


class EnsureReadTests(WorkspaceGitSandboxTestCase):
    def test_create_is_detached_at_origin_ref_and_reports_sha(self):
        expected_sha = _git(["rev-parse", "origin/main"], self.root)
        envelope = self._envelope(["ensure", "--read", "main"])
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["kind"], "read")
        self.assertEqual(envelope["ref"], "main")
        self.assertFalse(envelope["reused"])
        self.assertEqual(envelope["sha"], expected_sha)
        path = Path(envelope["path"])
        self.assertTrue(path.is_dir())
        # `rev-parse --abbrev-ref HEAD` returns the literal string "HEAD" for a detached
        # checkout (unlike `symbolic-ref`, it does not fail, so no non-zero-exit special-casing
        # is needed here).
        head_ref = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
        self.assertEqual(head_ref, "HEAD", "a read workspace must be detached, not on a branch")

    def test_path_uses_ro_prefixed_slug(self):
        envelope = self._envelope(["ensure", "--read", "main"])
        self.assertTrue(Path(envelope["path"]).name, "ro-main")

    def test_reensure_resets_to_current_origin_sha_discarding_local_changes(self):
        first = self._envelope(["ensure", "--read", "main"])
        read_path = Path(first["path"])
        _write(read_path / "local-scratch.txt", "will be discarded\n")

        other_clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other_clone.cleanup)
        _write(other_clone.path / "moved.txt", "origin moved on\n")
        _git(["add", "moved.txt"], other_clone.path)
        _git(["commit", "-m", "origin advances"], other_clone.path)
        _git(["push", "origin", "HEAD:main"], other_clone.path)
        expected_sha = _git(["rev-parse", "HEAD"], other_clone.path)

        second = self._envelope(["ensure", "--read", "main"])
        self.assertTrue(second["reused"])
        self.assertEqual(second["sha"], expected_sha)
        self.assertFalse((read_path / "local-scratch.txt").exists(), "reset --hard must discard local changes")
        self.assertTrue((read_path / "moved.txt").exists(), "must reflect origin's new content")

    def test_ensure_read_does_not_require_root_freshness(self):
        # Root is dirty/off-main; ensure --read must still succeed (architecture.md §6 scoping:
        # read grounds directly at a fetched origin ref, not off root's own HEAD).
        _git(["checkout", "-b", "not-main"], self.root)
        self.addCleanup(lambda: _git(["checkout", "main"], self.root))
        envelope = self._envelope(["ensure", "--read", "main"])
        self.assertEqual(envelope["status"], "ok")

    def test_ensure_read_also_maintains_worktrees_exclude(self):
        self._envelope(["ensure", "--read", "main"])
        text = (self.root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(".worktrees/", text)
        self.assertFalse((self.root / ".gitignore").exists())

    def test_ensure_read_root_freshness_check_survives_a_prior_ensure_in_the_same_clone(self):
        # D6's own regression, at the ensure --read call site: `ensure --read` writes info/exclude
        # (no root-freshness gate of its own), but a LATER `ensure --work`/`root-status` call in
        # the SAME clone must not see that prior write as ROOT_DIRTY -- the exact composite-session
        # symptom (a second prep call fell off prep onto gh_gather mid-run).
        self._envelope(["ensure", "--read", "main"])
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(envelope["status"], "ok")
        root_status = self._envelope(["root-status"])
        self.assertEqual(root_status["status"], "ok")

    def test_info_exclude_write_never_dirties_the_operators_checkout(self):
        """D6's mechanic outlives D6's gate. ROOT_DIRTY is retired, so nothing trips on a
        self-inflicted dirty root any more — but a plugin-authored write must still never appear
        in the operator's `git status`, which is why the exclusion lives in info/exclude (outside
        the working tree) and not in .gitignore."""
        self._envelope(["ensure", "--read", "main"])
        self.assertEqual(_git(["status", "--porcelain"], self.root), "")
        self.assertFalse((self.root / ".gitignore").exists())


class RemoveWorkTests(WorkspaceGitSandboxTestCase):
    def test_not_found_is_a_clean_noop(self):
        envelope = self._envelope(["remove", "--work", "never-created"])
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["removed"])
        self.assertEqual(envelope["reason"], "not_found")

    def test_teardown_comes_from_the_worktree_being_closed(self):
        """v3.x: teardown is discovered in the worktree being closed — that branch's teardown runs
        for that branch's workspace, matching where the commands execute. (An *uncommitted*
        teardown edit can never run: the dirty gate refuses the removal first. `lint --phase
        teardown --root <worktree>` is the preview for that case.)"""
        _seed_repo_with_hooks(
            self.clone.path, teardown_block=["- `echo default-branch >> %s/.teardown-log`" % self.root],
        )
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        wt = Path(envelope["path"])
        _write(
            wt / "CLAUDE.md",
            "<!-- worktree-teardown -->\n- `echo branch-version >> %s/.teardown-log`\n"
            "<!-- /worktree-teardown -->\n" % self.root,
        )
        _git(["add", "CLAUDE.md"], wt)
        _git(["commit", "-m", "this branch versions its own teardown"], wt)
        _git(["push", "origin", "HEAD:feature-x"], wt)

        removal = self._envelope(["remove", "--work", "feature-x"])
        self.assertTrue(removal["removed"])
        log = (self.root / ".teardown-log").read_text(encoding="utf-8")
        self.assertIn("branch-version", log)
        self.assertNotIn("default-branch", log)
        self.assertEqual(removal["teardown"]["source"]["path"], str(wt))

    def test_clean_worktree_runs_teardown_then_removes(self):
        _seed_repo_with_hooks(
            self.clone.path,
            teardown_block=["- `echo teardown-ran` — best-effort marker"],
        )
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        envelope = self._envelope(["remove", "--work", "feature-x"])
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(envelope["removed"])
        self.assertEqual(envelope["teardown"]["commands_run"], 1)
        self.assertTrue(envelope["teardown"]["succeeded"])
        self.assertFalse((self.root / ".worktrees" / "feature-x").exists())
        worktrees_after = _git(["worktree", "list"], self.root)
        self.assertNotIn("feature-x", worktrees_after)

    def test_dirty_worktree_returns_ambiguous_decision_and_does_not_remove(self):
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        _write(self.root / ".worktrees" / "feature-x" / "dirty.txt", "uncommitted\n")
        envelope = self._envelope(["remove", "--work", "feature-x"])
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")
        self.assertTrue(envelope["decision"]["context"]["dirty"])
        self.assertTrue((self.root / ".worktrees" / "feature-x").exists(), "must not remove on a dirty refusal")

    def test_unpushed_worktree_returns_ambiguous_decision_and_does_not_remove(self):
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        wt = self.root / ".worktrees" / "feature-x"
        _write(wt / "new.txt", "content\n")
        _git(["add", "new.txt"], wt)
        _git(["commit", "-m", "unpushed commit"], wt)
        envelope = self._envelope(["remove", "--work", "feature-x"])
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")
        self.assertEqual(envelope["decision"]["context"]["unpushed_commits"], 1)
        self.assertTrue(wt.exists(), "must not remove on an unpushed refusal")

    def test_clean_and_pushed_worktree_with_zero_unpushed_removes_successfully(self):
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        wt = self.root / ".worktrees" / "feature-x"
        _write(wt / "new.txt", "content\n")
        _git(["add", "new.txt"], wt)
        _git(["commit", "-m", "a commit"], wt)
        _git(["push", "origin", "feature-x"], wt)
        envelope = self._envelope(["remove", "--work", "feature-x"])
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(envelope["removed"])

    def test_teardown_failure_is_best_effort_and_does_not_block_removal(self):
        _seed_repo_with_hooks(
            self.clone.path,
            teardown_block=[
                "- `exit 7` — always fails",
                "- `echo second-step-still-ran` — must still run",
            ],
        )
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        envelope = self._envelope(["remove", "--work", "feature-x"])
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(envelope["removed"])
        self.assertTrue(envelope["teardown"]["succeeded"], "teardown always reports succeeded=true")
        self.assertEqual(envelope["teardown"]["commands_run"], 2, "best-effort continues past a failure")
        self.assertEqual(len(envelope["teardown"]["failures"]), 1)
        self.assertFalse((self.root / ".worktrees" / "feature-x").exists())


class SetupHookExecutionTests(WorkspaceGitSandboxTestCase):
    """`ensure --work`'s fail-fast setup-hook execution, via config-block fixtures."""

    def test_an_uncommitted_hook_edit_is_seen_and_run(self):
        """The workflow the whole v3.x change exists to enable: edit the block, run the tool, see
        it execute — no commit, no push, no merge to the default branch first."""
        _write(
            Path(self.root) / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `echo uncommitted >> .hook-source`\n<!-- /worktree-setup -->\n",
        )
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        log = (Path(envelope["path"]) / ".hook-source").read_text(encoding="utf-8")
        self.assertIn("uncommitted", log)
        self.assertIn("HOOK_SOURCE_DIRTY", envelope["notices"])
        self.assertTrue(envelope["setup"]["source"]["dirty"])
        self.assertEqual(envelope["setup"]["source"]["path"], str(self.root))

    def test_hooks_come_from_the_invokers_branch(self):
        """"The operator decides which branch they are in before running workspace-open": the
        invoking checkout's branch supplies the block, not the default branch's version."""
        _seed_repo_with_hooks(self.clone.path, setup_block=["- `echo default-branch >> .hook-source`"])
        _git(["checkout", "-b", "hook-experiment"], self.root)
        self.addCleanup(lambda: _git(["checkout", "main"], self.root))
        _write(
            Path(self.root) / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `echo experiment >> .hook-source`\n<!-- /worktree-setup -->\n",
        )
        _git(["add", "CLAUDE.md"], self.root)
        _git(["commit", "-m", "try a new setup hook on a branch"], self.root)

        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        log = (Path(envelope["path"]) / ".hook-source").read_text(encoding="utf-8")
        self.assertIn("experiment", log)
        self.assertNotIn("default-branch", log)

    def test_no_block_present_is_a_clean_noop(self):
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertFalse(envelope["setup"]["phase_present"])
        self.assertEqual(envelope["setup"]["commands_run"], 0)
        self.assertTrue(envelope["setup"]["succeeded"])

    def test_all_commands_succeed_and_run_in_declared_order(self):
        _seed_repo_with_hooks(
            self.clone.path,
            setup_block=[
                "- `echo one >> order.txt` — first",
                "- `echo two >> order.txt` — second",
            ],
        )
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertTrue(envelope["setup"]["phase_present"])
        self.assertEqual(envelope["setup"]["commands_run"], 2)
        self.assertTrue(envelope["setup"]["succeeded"])
        order_file = Path(envelope["path"]) / "order.txt"
        self.assertEqual(order_file.read_text(encoding="utf-8"), "one\ntwo\n")

    def test_failure_stops_before_later_commands_and_reports_first_failure(self):
        _seed_repo_with_hooks(
            self.clone.path,
            setup_block=[
                "- `echo before-fail` — runs",
                "- `exit 9` — fails",
                "- `echo should-not-run > should-not-exist.txt` — must never run",
            ],
        )
        rc, out, err = self._run(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(rc, 1, "a failing setup hook is a hard error, matching v1 exit 1")
        envelope = _parse_one_envelope(out)
        self.assertFalse(envelope["setup"]["succeeded"])
        self.assertEqual(envelope["setup"]["commands_run"], 2)
        self.assertEqual(envelope["setup"]["first_failure"]["step"], 2)
        self.assertEqual(envelope["setup"]["first_failure"]["command"], "exit 9")
        self.assertFalse((Path(envelope["path"]) / "should-not-exist.txt").exists())
        # The worktree itself is NOT auto-removed on a failed setup — matching v1: the caller
        # decides what to do with a worktree that exists but isn't ready.
        self.assertTrue(Path(envelope["path"]).is_dir())

    def test_setup_reruns_on_every_reuse_idempotently(self):
        _seed_repo_with_hooks(
            self.clone.path,
            setup_block=["- `echo ran >> marker.txt` — appends every entry"],
        )
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertTrue(envelope["reused"])
        self.assertTrue(envelope["setup"]["phase_present"])
        self.assertEqual(envelope["setup"]["commands_run"], 1)
        marker = Path(envelope["path"]) / "marker.txt"
        self.assertEqual(marker.read_text(encoding="utf-8"), "ran\nran\n")

    def test_block_discovered_via_commands_md_takes_priority_over_claude_md(self):
        _seed_repo_with_hooks(
            self.clone.path,
            setup_block=["- `echo from-claude-md >> which.txt` — should be shadowed"],
            in_file="CLAUDE.md",
        )
        _seed_repo_with_hooks(
            self.clone.path,
            setup_block=["- `echo from-commands-md >> which.txt` — should win"],
            in_file="COMMANDS.md",
        )
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        which = Path(envelope["path"]) / "which.txt"
        self.assertEqual(which.read_text(encoding="utf-8"), "from-commands-md\n")

    def test_block_discovered_via_at_include_from_claude_md(self):
        _write(
            self.clone.path / "CLAUDE.md",
            "See @docs/HOOKS.md for the worktree hook configuration.\n",
        )
        (self.clone.path / "docs").mkdir(exist_ok=True)
        _write(
            self.clone.path / "docs" / "HOOKS.md",
            "<!-- worktree-setup -->\n- `echo from-include >> which.txt` — via @include\n"
            "<!-- /worktree-setup -->\n",
        )
        _git(["add", "CLAUDE.md", "docs/HOOKS.md"], self.clone.path)
        _git(["commit", "-m", "hooks via include"], self.clone.path)
        _git(["push", "origin", "HEAD:main"], self.clone.path)

        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertTrue(envelope["setup"]["phase_present"])
        which = Path(envelope["path"]) / "which.txt"
        self.assertEqual(which.read_text(encoding="utf-8"), "from-include\n")


class GcTests(WorkspaceGitSandboxTestCase):
    def _backdate(self, path, days):
        old = time.time() - days * 86400
        os.utime(str(path), (old, old))

    def test_removes_only_aged_ro_star_worktrees(self):
        young = self._envelope(["ensure", "--read", "main"])
        self.assertTrue(True)  # young stays default-age (just created)
        # Create a second, distinct ref-slug so we have two ro-* worktrees to distinguish.
        other_clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other_clone.cleanup)
        _git(["branch", "other-ref"], other_clone.path)
        _git(["push", "origin", "other-ref"], other_clone.path)
        old = self._envelope(["ensure", "--read", "other-ref"])

        self._backdate(old["path"], 10)

        envelope = self._envelope(["gc"])
        self.assertEqual(envelope["removed"], [old["path"]])
        self.assertEqual(envelope["skipped"], [young["path"]])
        self.assertFalse(Path(old["path"]).exists())
        self.assertTrue(Path(young["path"]).exists())

    def test_adversarial_aged_work_worktree_survives_gc(self):
        work = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self._backdate(work["path"], 30)
        envelope = self._envelope(["gc", "--max-age", "0"])
        self.assertNotIn(work["path"], envelope["removed"])
        self.assertTrue(Path(work["path"]).is_dir(), "a work worktree must survive gc regardless of age")

    def test_max_age_zero_removes_a_freshly_created_ro_worktree(self):
        read = self._envelope(["ensure", "--read", "main"])
        envelope = self._envelope(["gc", "--max-age", "0"])
        self.assertIn(read["path"], envelope["removed"])
        self.assertFalse(Path(read["path"]).exists())

    def test_fresh_ro_worktree_survives_default_max_age(self):
        read = self._envelope(["ensure", "--read", "main"])
        envelope = self._envelope(["gc"])
        self.assertEqual(envelope["removed"], [])
        self.assertIn(read["path"], envelope["skipped"])
        self.assertTrue(Path(read["path"]).exists())


class RootStatusTests(WorkspaceGitSandboxTestCase):
    """v3.x: `root-status` is pure reporting. It ran the root-freshness protocol through v3 and
    could return ROOT_NOT_ON_MAIN / ROOT_DIRTY / ROOT_DIVERGED; all three codes are retired, so
    every state below is reported, never decided."""

    def test_happy_path_reports_branch_sha_and_default_branch(self):
        envelope = self._envelope(["root-status"])
        self.assertEqual(envelope["op"], "root-status")
        self.assertEqual(envelope["branch"], "main")
        self.assertEqual(envelope["default_branch"], "main")
        self.assertEqual(envelope["sha"], _git(["rev-parse", "HEAD"], self.root))
        self.assertFalse(envelope["dirty"])

    def test_reports_an_off_default_branch_checkout_without_deciding(self):
        _git(["checkout", "-b", "not-main"], self.root)
        self.addCleanup(lambda: _git(["checkout", "main"], self.root))
        envelope = self._envelope(["root-status"])
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["branch"], "not-main")
        self.assertEqual(envelope["default_branch"], "main")

    def test_reports_a_dirty_checkout_without_deciding(self):
        _write(self.root / "dirty.txt", "x\n")
        envelope = self._envelope(["root-status"])
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(envelope["dirty"])


class LintTests(WorkspaceGitSandboxTestCase):
    def test_lint_setup_no_block_present(self):
        envelope = self._envelope(["lint", "setup"])
        self.assertEqual(envelope["op"], "lint")
        self.assertEqual(envelope["phase"], "setup")
        self.assertFalse(envelope["phase_present"])
        self.assertEqual(envelope["command_count"], 0)
        self.assertEqual(envelope["would_run"], [])

    def test_lint_setup_reports_discovered_commands_without_running_them(self):
        _seed_repo_with_hooks(
            self.clone.path,
            setup_block=["- `echo would-run-1` — one", "- `echo would-run-2` — two"],
        )
        envelope = self._envelope(["lint", "setup"])
        self.assertTrue(envelope["phase_present"])
        self.assertEqual(envelope["command_count"], 2)
        self.assertEqual(envelope["would_run"], ["echo would-run-1", "echo would-run-2"])
        # lint performs NO worktree operation and runs NOTHING.
        self.assertFalse((self.root / ".worktrees").exists())

    def test_lint_teardown_independent_of_setup_block(self):
        _seed_repo_with_hooks(
            self.clone.path,
            setup_block=["- `echo s` — setup"],
            teardown_block=["- `echo t` — teardown"],
        )
        setup_lint = self._envelope(["lint", "setup"])
        teardown_lint = self._envelope(["lint", "teardown"])
        self.assertEqual(setup_lint["would_run"], ["echo s"])
        self.assertEqual(teardown_lint["would_run"], ["echo t"])

    def test_lint_invalid_phase_is_a_usage_error(self):
        rc, out, err = self._run(["lint", "bogus"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")


class UsageErrorTests(unittest.TestCase):
    """Bare usage-error paths not requiring a git sandbox."""

    def test_no_args_exits_2_with_no_envelope(self):
        rc, out, err = _run_cli([])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")

    def test_unknown_subcommand_exits_2_with_no_envelope(self):
        rc, out, err = _run_cli(["bogus"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")

    def test_ensure_without_work_or_read_is_a_usage_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = _run_cli(["ensure", "--root", tmp])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")

    def test_ensure_with_both_work_and_read_is_a_usage_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = _run_cli(
                ["ensure", "--work", "x", "--read", "y", "--base", "main", "--root", tmp]
            )
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")


class MainRootNormalizationTests(WorkspaceGitSandboxTestCase):
    """v3: every --root-taking subcommand normalizes to the MAIN checkout via _resolve_main_root,
    so `--root .` from inside a linked worktree still lands `.worktrees/` under the main checkout
    (sessions now run inside worktrees; the scripts must behave identically from there)."""

    def test_resolve_main_root_is_identity_at_the_main_checkout(self):
        self.assertEqual(workspace._resolve_main_root(self.root), Path(self.root).resolve())

    def test_ensure_read_from_inside_a_linked_worktree_lands_ro_under_the_main_checkout(self):
        work_env = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        rc, out, err = _run_cli(
            ["ensure", "--read", "main", "--root", work_env["path"]]
        )
        self.assertEqual(rc, EXIT_OK, "stderr: %s" % err)
        envelope = _parse_one_envelope(out)
        self.assertEqual(
            Path(envelope["path"]),
            (Path(self.root) / ".worktrees" / "ro-main").resolve(),
        )

    def test_ensure_work_from_inside_a_linked_worktree_lands_under_the_main_checkout(self):
        first = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        rc, out, err = _run_cli(
            ["ensure", "--work", "feature-y", "--base", "main", "--root", first["path"]]
        )
        self.assertEqual(rc, EXIT_OK, "stderr: %s" % err)
        envelope = _parse_one_envelope(out)
        self.assertEqual(
            Path(envelope["path"]),
            (Path(self.root) / ".worktrees" / "feature-y").resolve(),
        )


class AttachTests(WorkspaceGitSandboxTestCase):
    """v3 `attach`: verify the ambient checkout, record its facts, re-run setup hooks at the
    origin/main pin — every mismatch a WORKSPACE_MISMATCH decision with a closed-set reason."""

    def _mk_worktree(self, branch="feature-x"):
        envelope = self._envelope(["ensure", "--work", branch, "--base", "main"])
        return Path(envelope["path"])

    def _attach(self, args):
        rc, out, err = _run_cli(["attach"] + args)
        return rc, out, err

    def _attach_envelope(self, args, expect_rc=EXIT_OK):
        rc, out, err = self._attach(args)
        self.assertEqual(rc, expect_rc, "stderr: %s" % err)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope

    def _assert_mismatch(self, envelope, reason):
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "WORKSPACE_MISMATCH")
        self.assertEqual(envelope["decision"]["context"]["reason"], reason)

    def test_happy_attach_records_the_ambient_checkout_facts(self):
        wt = self._mk_worktree()
        envelope = self._attach_envelope(
            ["--expect-branch", "feature-x", "--path", str(wt), "--no-hooks"]
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["op"], "attach")
        self.assertEqual(envelope["branch"], "feature-x")
        self.assertEqual(Path(envelope["path"]), wt)
        self.assertEqual(Path(envelope["main_root"]), Path(self.root).resolve())
        self.assertEqual(envelope["sha"], _git(["rev-parse", "HEAD"], wt))
        self.assertFalse(envelope["dirty"])
        self.assertEqual(envelope["unpushed_commits"], 0)
        self.assertEqual(envelope["upstream_sha"], envelope["sha"])
        self.assertNotIn("setup", envelope)

    def test_attach_records_dirty_and_unpushed_state(self):
        wt = self._mk_worktree()
        _write(wt / "new.txt", "local\n")
        _git(["add", "new.txt"], wt)
        _git(["commit", "-m", "local work"], wt)
        _write(wt / "uncommitted.txt", "dirt\n")
        envelope = self._attach_envelope(
            ["--expect-branch", "feature-x", "--path", str(wt), "--no-hooks"]
        )
        self.assertTrue(envelope["dirty"])
        self.assertEqual(envelope["unpushed_commits"], 1)
        # The pushed tip stays where the remote is, so a diff anchored on it keeps the local commit.
        # `feature-x` was never pushed, so the remote it is measured against is the base's.
        self.assertEqual(envelope["upstream_sha"], _git(["rev-parse", "origin/main"], wt))
        self.assertNotEqual(envelope["upstream_sha"], envelope["sha"])

    def test_branch_mismatch_is_a_workspace_mismatch_decision(self):
        wt = self._mk_worktree("feature-x")
        envelope = self._attach_envelope(
            ["--expect-branch", "other-branch", "--path", str(wt), "--no-hooks"]
        )
        self._assert_mismatch(envelope, "branch_mismatch")
        self.assertEqual(envelope["decision"]["context"]["actual_branch"], "feature-x")
        self.assertEqual(envelope["decision"]["context"]["expected_branch"], "other-branch")

    def test_at_project_root_is_a_workspace_mismatch_decision_naming_workspace_open(self):
        envelope = self._attach_envelope(
            ["--expect-branch", "feature-x", "--path", str(self.root), "--no-hooks"]
        )
        self._assert_mismatch(envelope, "at_project_root")
        self.assertTrue(
            any("workspace-open" in option for option in envelope["decision"]["options"]),
            "the at_project_root card must name workspace-open: %r"
            % envelope["decision"]["options"],
        )

    def test_allow_root_permits_a_main_checkout_at_the_project_root(self):
        envelope = self._attach_envelope(
            ["--expect-branch", "main", "--path", str(self.root), "--no-hooks", "--allow-root"]
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["branch"], "main")

    def test_detached_head_is_a_workspace_mismatch_decision(self):
        detached = Path(self.root) / ".worktrees" / "detached-view"
        detached.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "--detach", str(detached), "main"], self.root)
        self.addCleanup(lambda: _git(["worktree", "remove", "-f", str(detached)], self.root))
        envelope = self._attach_envelope(
            ["--expect-branch", "feature-x", "--path", str(detached), "--no-hooks"]
        )
        self._assert_mismatch(envelope, "detached_head")

    def test_behind_the_expected_remote_sha_is_stale_checkout(self):
        wt = self._mk_worktree()
        _git(["push", "-u", "origin", "feature-x"], wt)
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        _git(["fetch", "origin", "feature-x"], other.path)
        _git(["checkout", "feature-x"], other.path)
        _write(Path(other.path) / "pushed.txt", "newer\n")
        _git(["add", "pushed.txt"], other.path)
        _git(["commit", "-m", "pushed elsewhere"], other.path)
        _git(["push", "origin", "feature-x"], other.path)
        new_tip = _git(["rev-parse", "HEAD"], other.path)
        _git(["fetch", "origin", "feature-x"], wt)  # the sha must be known locally to compare
        envelope = self._attach_envelope(
            [
                "--expect-branch", "feature-x", "--path", str(wt), "--no-hooks",
                "--expect-remote-sha", new_tip,
            ]
        )
        self._assert_mismatch(envelope, "stale_checkout")
        self.assertFalse(envelope["decision"]["context"]["ahead_of_remote"])

    def test_ahead_of_the_expected_remote_sha_passes(self):
        wt = self._mk_worktree()
        remote_tip = _git(["rev-parse", "HEAD"], wt)
        _write(wt / "ahead.txt", "local\n")
        _git(["add", "ahead.txt"], wt)
        _git(["commit", "-m", "unpushed local work"], wt)
        envelope = self._attach_envelope(
            [
                "--expect-branch", "feature-x", "--path", str(wt), "--no-hooks",
                "--expect-remote-sha", remote_tip,
            ]
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["unpushed_commits"], 1)

    def test_hooks_rerun_on_every_attach(self):
        _seed_repo_with_hooks(self.root, setup_block=["- `echo ran >> .hook-log`"])
        wt = self._mk_worktree()  # ensure itself runs the hook once (working-tree discovery)
        self._attach_envelope(["--expect-branch", "feature-x", "--path", str(wt)])
        self._attach_envelope(["--expect-branch", "feature-x", "--path", str(wt)])
        log = (wt / ".hook-log").read_text(encoding="utf-8")
        self.assertEqual(log.count("ran"), 3, "ensure once + two attaches must each run the hook")

    def test_attach_hooks_read_the_worktrees_own_working_tree(self):
        """v3.x inversion. Through v3 attach discovered hooks from origin/main BLOBS at a pin, so
        an uncommitted edit was invisible and a branch could not supply hook commands. Now the
        worktree the session runs in is the source — uncommitted edits included — which is what
        makes a hook change testable before it merges.

        The mutation goes in the WORKTREE, not the root: the root's tree was never attach's source
        under either regime, so mutating it there would pass without proving anything."""
        _seed_repo_with_hooks(self.root, setup_block=["- `echo committed >> .hook-source`"])
        wt = self._mk_worktree()
        _write(
            wt / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `echo working-tree >> .hook-source`\n<!-- /worktree-setup -->\n",
        )
        (wt / ".hook-source").unlink(missing_ok=True)  # drop the ensure-time run; assert on attach's
        envelope = self._attach_envelope(["--expect-branch", "feature-x", "--path", str(wt)])
        log = (wt / ".hook-source").read_text(encoding="utf-8")
        self.assertIn("working-tree", log)
        self.assertNotIn("committed", log)
        self.assertEqual(envelope["setup"]["source"]["path"], str(wt))
        self.assertTrue(envelope["setup"]["source"]["dirty"])

    def test_attach_runs_hooks_committed_on_the_worktrees_own_branch(self):
        """The committed half of the same rule: a branch may version its own setup hooks, and the
        session entering that branch's worktree runs that branch's version."""
        _seed_repo_with_hooks(self.root, setup_block=["- `echo default-branch >> .hook-source`"])
        wt = self._mk_worktree()
        _write(
            wt / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `echo branch-version >> .hook-source`\n<!-- /worktree-setup -->\n",
        )
        _git(["add", "CLAUDE.md"], wt)
        _git(["commit", "-m", "this branch versions its own setup hook"], wt)
        (wt / ".hook-source").unlink(missing_ok=True)  # drop the ensure-time run; assert on attach's
        envelope = self._attach_envelope(["--expect-branch", "feature-x", "--path", str(wt)])
        log = (wt / ".hook-source").read_text(encoding="utf-8")
        self.assertIn("branch-version", log)
        self.assertNotIn("default-branch", log)
        self.assertEqual(envelope["setup"]["source"]["path"], str(wt))
        self.assertTrue(envelope["setup"]["source"]["file"].endswith("CLAUDE.md"))

    def test_attach_setup_hook_failure_is_partial_but_honest_exit_1(self):
        _seed_repo_with_hooks(self.root, setup_block=["- `false`"])
        wt = self._mk_worktree_expect_hook_failure()
        rc, out, err = self._attach(["--expect-branch", "feature-x", "--path", str(wt)])
        self.assertEqual(rc, 1)
        envelope = _parse_one_envelope(out)
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["setup"]["succeeded"])
        self.assertNotIn("sha", envelope)

    def _mk_worktree_expect_hook_failure(self, branch="feature-x"):
        rc, out, err = self._run(["ensure", "--work", branch, "--base", "main"])
        self.assertEqual(rc, 1, "seeded failing hook: ensure exits 1; stderr: %s" % err)
        return Path(_parse_one_envelope(out)["path"])


class SyncWorkTests(WorkspaceGitSandboxTestCase):
    """`sync --work`: fast-forward a work worktree to its own branch's remote tip — the
    coordinator's pre-plan sync of the epic worktree (prd.md §8.5's named carve-out). Fast-forward
    only; dirty, ahead and diverged refuse before anything moves; the setup hooks re-run."""

    def _mk_pushed_worktree(self, branch="epic/7-journal"):
        envelope = self._envelope(["ensure", "--work", branch, "--base", "main"])
        wt = Path(envelope["path"])
        _git(["push", "-u", "origin", branch], wt)
        return wt

    def _push_elsewhere(self, branch="epic/7-journal", name="merged.txt"):
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        _git(["fetch", "origin", branch], other.path)
        _git(["checkout", "-b", branch, "origin/%s" % branch], other.path)
        _write(Path(other.path) / name, "merged story\n")
        _git(["add", name], other.path)
        _git(["commit", "-m", "story merged into the epic"], other.path)
        _git(["push", "origin", branch], other.path)
        return _git(["rev-parse", "HEAD"], other.path)

    def _assert_refused(self, envelope):
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")

    def test_behind_fast_forwards_to_the_remote_tip(self):
        wt = self._mk_pushed_worktree()
        before = _git(["rev-parse", "HEAD"], wt)
        root_head = _git(["rev-parse", "HEAD"], self.root)
        tip = self._push_elsewhere()
        envelope = self._envelope(["sync", "--work", "epic/7-journal"])
        self.assertEqual(envelope["op"], "sync")
        self.assertEqual(envelope["sync"], {"before": before, "after": tip, "fast_forwarded": True})
        self.assertEqual(_git(["rev-parse", "HEAD"], wt), tip)
        self.assertTrue((wt / "merged.txt").exists())
        self.assertEqual(_git(["rev-parse", "HEAD"], self.root), root_head, "the main checkout is never written")

    def test_already_at_the_tip_is_a_noop(self):
        wt = self._mk_pushed_worktree()
        head = _git(["rev-parse", "HEAD"], wt)
        envelope = self._envelope(["sync", "--work", "epic/7-journal"])
        self.assertEqual(envelope["sync"], {"before": head, "after": head, "fast_forwarded": False})

    def test_dirty_refuses_and_moves_nothing(self):
        wt = self._mk_pushed_worktree()
        before = _git(["rev-parse", "HEAD"], wt)
        self._push_elsewhere()
        _write(wt / "scratch.txt", "uncommitted\n")
        envelope = self._envelope(["sync", "--work", "epic/7-journal"])
        self._assert_refused(envelope)
        self.assertTrue(envelope["decision"]["context"]["dirty"])
        self.assertEqual(_git(["rev-parse", "HEAD"], wt), before)

    def test_local_commits_the_remote_lacks_refuse(self):
        wt = self._mk_pushed_worktree()
        _write(wt / "local.txt", "local\n")
        _git(["add", "local.txt"], wt)
        _git(["commit", "-m", "unpushed"], wt)
        envelope = self._envelope(["sync", "--work", "epic/7-journal"])
        self._assert_refused(envelope)
        self.assertEqual(envelope["decision"]["context"]["unpushed_commits"], 1)
        self.assertFalse(envelope["decision"]["context"]["diverged"])

    def test_diverged_refuses_and_moves_nothing(self):
        wt = self._mk_pushed_worktree()
        self._push_elsewhere()
        _write(wt / "local.txt", "local\n")
        _git(["add", "local.txt"], wt)
        _git(["commit", "-m", "local work"], wt)
        before = _git(["rev-parse", "HEAD"], wt)
        envelope = self._envelope(["sync", "--work", "epic/7-journal"])
        self._assert_refused(envelope)
        self.assertTrue(envelope["decision"]["context"]["diverged"])
        self.assertEqual(_git(["rev-parse", "HEAD"], wt), before)

    def test_no_worktree_for_the_branch_refuses(self):
        envelope = self._envelope(["sync", "--work", "epic/7-journal"])
        self._assert_refused(envelope)

    def test_the_main_checkout_is_never_a_target(self):
        envelope = self._envelope(["sync", "--work", "main"])
        self._assert_refused(envelope)

    def _hook_log(self):
        # Outside the worktree: a hook writing an untracked file inside it would make it dirty,
        # which sync (like remove) refuses.
        import tempfile
        log_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(log_dir, ignore_errors=True))
        return Path(log_dir) / "hook.log"

    def test_setup_hooks_re_run_from_the_worktree(self):
        log = self._hook_log()
        _seed_repo_with_hooks(self.clone.path, setup_block=["- `echo synced >> %s`" % log])
        self._mk_pushed_worktree()
        self._push_elsewhere()
        envelope = self._envelope(["sync", "--work", "epic/7-journal"])
        self.assertTrue(envelope["setup"]["succeeded"])
        self.assertEqual(log.read_text(encoding="utf-8").count("synced"), 2, "once at ensure, once at sync")

    def test_a_failing_setup_hook_exits_one_with_an_ok_envelope(self):
        marker = self._hook_log()
        # Succeeds the first time (at ensure), fails every time after (at sync).
        _seed_repo_with_hooks(self.clone.path, setup_block=["- `test ! -f %s && touch %s`" % (marker, marker)])
        self._mk_pushed_worktree()
        rc, out, err = self._run(["sync", "--work", "epic/7-journal"])
        self.assertEqual(rc, 1, "stderr: %s" % err)
        envelope = _parse_one_envelope(out)
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["setup"]["succeeded"])

    def test_invoked_from_inside_the_worktree_behaves_identically(self):
        wt = self._mk_pushed_worktree()
        tip = self._push_elsewhere()
        rc, out, err = _run_cli(["sync", "--work", "epic/7-journal", "--root", "."], cwd=str(wt))
        self.assertEqual(rc, EXIT_OK, "stderr: %s" % err)
        self.assertEqual(_parse_one_envelope(out)["sync"]["after"], tip)


class RemoveWorkGuardTests(WorkspaceGitSandboxTestCase):
    """v3 remove --work guard: refuse (typed) when invoked from inside the target worktree; keep
    working when invoked from inside a SIBLING worktree (root normalization)."""

    def test_remove_from_inside_the_target_worktree_is_a_workspace_mismatch(self):
        envelope = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        wt = Path(envelope["path"])
        rc, out, err = _run_cli(
            ["remove", "--work", "feature-x", "--root", str(self.root)], cwd=str(wt)
        )
        self.assertEqual(rc, EXIT_OK, "stderr: %s" % err)
        result = _parse_one_envelope(out)
        self.assertEqual(result["status"], "needs_decision")
        self.assertEqual(result["decision"]["code"], "WORKSPACE_MISMATCH")
        self.assertEqual(result["decision"]["context"]["reason"], "cwd_inside_target")
        self.assertTrue(wt.is_dir(), "the worktree must be left in place")

    def test_remove_from_a_sibling_worktree_normalizes_root_and_removes(self):
        target = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        sibling = self._envelope(["ensure", "--work", "feature-y", "--base", "main"])
        rc, out, err = _run_cli(
            ["remove", "--work", "feature-x", "--root", "."], cwd=sibling["path"]
        )
        self.assertEqual(rc, EXIT_OK, "stderr: %s" % err)
        result = _parse_one_envelope(out)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["removed"])
        self.assertFalse(Path(target["path"]).is_dir())


class ListWorkBranchesTests(WorkspaceGitSandboxTestCase):
    """`list_work_branches` — the local, offline "which branches have a workspace open" answer
    `prep_workspace_close.py` resolves an issue number against. Imported in-process (no CLI
    subcommand: it is a core, not an operation)."""

    def test_lists_work_branches_and_never_ro_or_the_main_checkout(self):
        self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self._envelope(["ensure", "--work", "feature-y", "--base", "main"])
        self._envelope(["ensure", "--read", "main"])
        self.assertEqual(
            workspace.list_work_branches(str(self.root)), ["feature-x", "feature-y"]
        )

    def test_an_epic_branchs_nested_path_is_still_a_work_branch(self):
        # `epic/<N>-<slug>` lives at `.worktrees/epic/<N>-<slug>` — the `ro-` test is on the first
        # segment below `.worktrees/`, so a nested path must not be misread.
        self._envelope(["ensure", "--work", "epic/150-chat-ux", "--base", "main"])
        self.assertEqual(workspace.list_work_branches(str(self.root)), ["epic/150-chat-ux"])

    def test_empty_when_no_workspace_is_open(self):
        self.assertEqual(workspace.list_work_branches(str(self.root)), [])

    def test_normalizes_root_from_inside_a_linked_worktree(self):
        opened = self._envelope(["ensure", "--work", "feature-x", "--base", "main"])
        self.assertEqual(workspace.list_work_branches(opened["path"]), ["feature-x"])


class TreeStateTests(WorkspaceGitSandboxTestCase):
    """`tree-state` (#244) — the working tree's fingerprint the resolver's §8 gate compares before and after
    its static checks, so a verdict the test-selection sub-agent read against a tree a check then rewrote is
    never acted on. The scratch dir is a temp dir outside the sandbox repo: one inside it would be swept up
    by `git add -A` and make the fingerprint unstable."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(str(self.scratch), ignore_errors=True))

    def _tree_state(self, path=None):
        rc, out, err = _run_cli(["tree-state", "--path", str(path or self.root), "--scratch", str(self.scratch)])
        self.assertEqual(rc, EXIT_OK, "stderr: %s" % err)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        return envelope

    def _index_snapshot(self):
        index = Path(_git(["rev-parse", "--absolute-git-dir"], self.root)) / "index"
        return (
            index.read_bytes() if index.exists() else None,
            _git(["ls-files", "-s"], self.root),
            _git(["diff", "--cached"], self.root),
        )

    def test_unchanged_tree_is_stable_and_head_is_reported(self):
        first = self._tree_state()
        second = self._tree_state()
        self.assertRegex(first["tree"], r"^[0-9a-f]{40}$")
        self.assertEqual(first["tree"], second["tree"])
        self.assertEqual(first["head"], _git(["rev-parse", "HEAD"], self.root))
        self.assertEqual(first["path"], str(self.root))
        self.assertEqual(first["notices"], [])

    def test_rewriting_an_already_modified_tracked_file_changes_the_tree(self):
        # The case `git status --porcelain` cannot see: the file reads ` M` before and after.
        _write(self.root / "tracked.txt", "one\n")
        _git(["add", "tracked.txt"], self.root)
        _git(["commit", "-q", "-m", "seed tracked"], self.root)
        _write(self.root / "tracked.txt", "two\n")
        before = self._tree_state()["tree"]
        _write(self.root / "tracked.txt", "three\n")
        after = self._tree_state()["tree"]
        self.assertNotEqual(before, after)

    def test_an_untracked_file_changes_the_tree_and_an_ignored_one_does_not(self):
        _write(self.root / ".gitignore", "build/\n")
        _git(["add", ".gitignore"], self.root)
        _git(["commit", "-q", "-m", "ignore build"], self.root)
        base = self._tree_state()["tree"]
        (self.root / "build").mkdir()
        _write(self.root / "build" / "out.bin", "artefact\n")
        self.assertEqual(self._tree_state()["tree"], base)
        _write(self.root / "new.txt", "new\n")
        self.assertNotEqual(self._tree_state()["tree"], base)

    def test_the_checkouts_index_is_untouched_and_no_temporary_index_is_left(self):
        _write(self.root / "staged.txt", "staged\n")
        _git(["add", "staged.txt"], self.root)
        _write(self.root / "untracked.txt", "untracked\n")
        before = self._index_snapshot()
        self._tree_state()
        self.assertEqual(self._index_snapshot(), before)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_a_non_repository_path_is_a_null_tree_and_a_notice_never_a_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            envelope = self._tree_state(path=tmp)
        self.assertIsNone(envelope["tree"])
        self.assertIsNone(envelope["head"])
        self.assertEqual(len(envelope["notices"]), 1)
        self.assertTrue(envelope["notices"][0].startswith("TREE_STATE_UNAVAILABLE"))

    def test_a_missing_path_is_a_null_tree_and_a_notice(self):
        envelope = self._tree_state(path=self.scratch / "does-not-exist")
        self.assertIsNone(envelope["tree"])
        self.assertTrue(envelope["notices"][0].startswith("TREE_STATE_UNAVAILABLE"))

    def test_the_core_is_import_only_and_returns_no_decision(self):
        payload, notices, decision = workspace.build_tree_state(str(self.root), str(self.scratch))
        self.assertIsNone(decision)
        self.assertEqual(notices, [])
        self.assertRegex(payload["tree"], r"^[0-9a-f]{40}$")

    def test_missing_scratch_is_a_usage_error(self):
        rc, out, _err = _run_cli(["tree-state", "--path", str(self.root)])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
