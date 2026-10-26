"""Unit tests for scripts/prep_resolver.py — the resolver's complete facts block in one call
(architecture.md §4; docs/implementation.md S9; docs/specs/resolver.md).

Test topology (mirrors tests/test_prep_evaluator.py — the S8-locked composition pattern this step
inherits): `gh` calls go through the offline shim (`tests/support/shimenv`); `git` calls (root
freshness, `ensure --work`/`ensure --read`, and the `git ls-remote` epic-branch/collision
discovery this step adds) go through a real temp origin+clone (`tests/support/gitsandbox`) — no
shim needed for those (architecture.md §10). `prep_resolver.py` is driven as a real subprocess so
every test exercises the full argv-in / subprocess / envelope-out path a real caller uses.

Coverage matrix (S9 DoD):
- One fixture per row of the v1 prior-PR table (7 rows) -> mode/vector derivation.
- Epic-branch discovery: zero / one / multiple matches (multiple -> AMBIGUOUS).
- Branch-collision suffixing: existing -v2 yields -v3 (exercised directly against a git sandbox,
  since the row fixtures' canonical path already covers the zero-collision case).
- Story parent-epic search: one match.
- Blocked-OQ both ways: open tracker + in-scope (blocked) sets the hard-gate; a
  question-decision:v1 comment clears it.
- Distiller bundle is staged paths, never above-threshold inline bytes.
- Envelope conformance + the single-invocation call-budget test (two-sided, as S6).
- Decision codes: AMBIGUOUS (epic-branch/story-parent multiplicity), AUTH_REQUIRED,
  MARKER_AMBIGUOUS (duplicate plan-marker comments), PHASES_MALFORMED, DOD_MALFORMED.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "prep_resolver.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import parse  # noqa: E402  (the shared `## Phase tracker` scanner the tracker fact composes)
import prep_resolver  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _git(args, cwd):
    result = subprocess.run(
        ["git"] + args, cwd=str(cwd), capture_output=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise RuntimeError("git %s (cwd=%s) failed: %s" % (" ".join(args), cwd, result.stderr))
    return result.stdout.strip()


def _parse_one_envelope(stdout_text):
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(
            "expected exactly one non-blank stdout line (the envelope), got %d: %r"
            % (len(lines), lines)
        )
    return json.loads(lines[0])


# The doc catalogue every sandbox starts configured with (skills/_shared/doc-catalogue.md) — one
# entry naming a document setUp also creates, so a baseline run reports one present grounding doc,
# no notice, and no attention line.
SEEDED_CATALOGUE_INTERIOR = "- `docs/prd.md` — prd — binding — What the product is.\n"


def _catalogue_block(interior):
    return "<!-- doc-catalogue -->\n%s<!-- /doc-catalogue -->\n" % interior


class PrepResolverSandboxTestCase(unittest.TestCase):
    """Shared setup: a real temp git origin+clone (the `--root`), pre-seeded with `.gitignore`
    (mirroring tests/test_prep_evaluator.py's identical rationale: an unseeded first `ensure`
    would spuriously trip ROOT_DIRTY on the very first call)."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        _write(self.root / ".gitignore", ".worktrees/\n")
        # A configured consuming repo: a doc catalogue naming one document that exists (the
        # test_prep_planner seed), so the grounding read is neutral for every test not about it —
        # without it each run's `attention` carries the catalogue-absent line.
        _write(self.root / "docs" / "README.md", _catalogue_block(SEEDED_CATALOGUE_INTERIOR))
        _write(self.root / "docs" / "prd.md", "# PRD\n")
        _git(["add", ".gitignore", "docs"], self.root)
        _git(["commit", "-m", "seed gitignore + doc catalogue"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    # v3 harness: a test whose flow reaches the workspace ASSERTION (fresh/continue rows) must run
    # the prep subprocess with its cwd INSIDE an ambient worktree — the operator posture the
    # inversion introduces. A class sets `ambient_default` (or a test passes `ambient=`) to the
    # branch name the worktree should be on; None (the base default) runs from the test process's
    # own cwd, which only flows that never reach the assertion (gated, comment-only, --refresh,
    # early decisions) may use.
    ambient_default = None

    def _mk_ambient(self, branch):
        """Create (or reuse) `.worktrees/<branch>` in the sandbox clone, forked at origin/main —
        the worktree the operator would have opened via workspace-open. Returns its Path."""
        wt = self.root / ".worktrees" / branch
        if not wt.is_dir():
            wt.parent.mkdir(parents=True, exist_ok=True)
            has_local = (
                subprocess.run(
                    ["git", "show-ref", "--verify", "--quiet", "refs/heads/%s" % branch],
                    cwd=str(self.root),
                ).returncode
                == 0
            )
            if has_local:
                _git(["worktree", "add", str(wt), branch], self.root)
            else:
                _git(["worktree", "add", "-b", branch, str(wt), "origin/main"], self.root)
        return wt

    def _run(self, args, fixture_case=None, extra_env=None, cwd=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            env=env,
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    _AMBIENT_UNSET = object()

    def _envelope(self, issue="100", repo="octo/widgets", fixture_case="prep_resolver_row_no_prior_pr", extra_args=None, fixtures_dir=None, ambient=_AMBIENT_UNSET):
        args = [issue, repo, "--root", str(self.root), "--scratch-dir", self.scratch]
        if extra_args:
            args += extra_args
        if ambient is self._AMBIENT_UNSET:
            ambient = self.ambient_default
        run_cwd = str(self._mk_ambient(ambient)) if ambient is not None else None
        # A `fixtures_dir` overrides the named case with an explicit (e.g. run-time-stamped) fixture dir
        # via GH_SHIM_FIXTURES — see PriorPrRowTests._stamped_active_fixture (the time-bomb fix).
        if fixtures_dir is not None:
            result = self._run(args, fixture_case=None, extra_env={"GH_SHIM_FIXTURES": str(fixtures_dir)}, cwd=run_cwd)
        else:
            result = self._run(args, fixture_case=fixture_case, cwd=run_cwd)
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope


class ScriptExistsTests(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_script_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPT)], capture_output=True, encoding="utf-8"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class HappyPathFactsSchemaTests(PrepResolverSandboxTestCase):
    """Facts schema matches architecture.md §4's common core + resolver extensions (v3: the
    workspace fact is the OBSERVED ambient checkout, asserted — not an ensured worktree)."""

    ambient_default = "100-fix-the-widget"

    def test_conformant_ok_envelope_with_resolver_facts(self):
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        for key in (
            "repo", "scratch", "root", "target", "vector", "suggested_playbook", "workspace",
            "config", "sections", "attention", "notices", "plan", "phases", "dod",
            "dod_coverage", "open_questions", "open_questions_gate", "audit_ref",
            "distiller_bundle", "tracker",
        ):
            self.assertIn(key, envelope, "missing architecture.md §4 facts-block key %r" % key)

    def test_root_facts_shape(self):
        # v3.x: root carries the main checkout's path and the DERIVED default branch. It used to
        # carry `sha`/`source: "origin/main"` — the retired pin; the SHA that matters now is
        # `config.source.sha`, the checkout the gate blocks were actually read from.
        envelope = self._envelope()
        self.assertEqual(envelope["root"]["path"], str(self.root))
        self.assertEqual(envelope["root"]["default_branch"], "main")
        self.assertNotIn("sha", envelope["root"])
        self.assertNotIn("source", envelope["root"])
        self.assertTrue(envelope["root"]["fresh"])

    def test_target_facts_shape(self):
        envelope = self._envelope()
        self.assertEqual(envelope["target"]["kind"], "issue")
        self.assertEqual(envelope["target"]["number"], 100)
        self.assertEqual(envelope["target"]["state"], "OPEN")
        self.assertEqual(envelope["target"]["labels"], ["bug"])

    def test_vector_type_standard_for_bug_labeled_issue(self):
        envelope = self._envelope()
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(envelope["suggested_playbook"], "standard.md")

    def test_workspace_fact_records_the_ambient_checkout(self):
        envelope = self._envelope()
        self.assertEqual(envelope["workspace"]["branch"], "100-fix-the-widget")
        self.assertEqual(envelope["workspace"]["base_ref"], "main")
        self.assertEqual(envelope["workspace"]["source"], "ambient")
        self.assertEqual(
            Path(envelope["workspace"]["path"]),
            (self.root / ".worktrees" / "100-fix-the-widget").resolve(),
        )
        self.assertFalse(envelope["workspace"]["dirty"])
        self.assertEqual(envelope["workspace"]["unpushed_commits"], 0)
        self.assertNotIn("reused", envelope["workspace"])
        self.assertNotIn("branch", envelope)  # the minted-branch fact is retired with the ladder

    def test_audit_ref_is_main_for_standard_type(self):
        envelope = self._envelope()
        self.assertEqual(envelope["audit_ref"], "main")
        self.assertNotIn("read_workspaces", envelope)

    def test_config_source_is_the_ambient_workspace(self):
        envelope = self._envelope()
        source = envelope["config"]["source"]
        self.assertEqual(source["path"], envelope["workspace"]["path"])
        self.assertEqual(source["branch"], envelope["workspace"]["branch"])
        self.assertEqual(len(source["sha"]), 40)
        self.assertFalse(source["dirty"])

    def test_config_reads_the_ambient_worktree_including_uncommitted_edits(self):
        # v3.x inversion, and the deliberate security trade the operator chose: a committed gate
        # block on the default branch + an UNCOMMITTED weakening in the work worktree. Through v3
        # the pinned blob won and the weakening was invisible ("a PR cannot weaken its own
        # gates"); now the working tree wins, and the compensating control is that
        # `config.source.dirty` says so and an attention line surfaces it.
        _write(
            self.root / "COMMANDS.md",
            "<!-- issue-resolver-fast-checks -->\n- `make lint` — hygiene\n<!-- /issue-resolver-fast-checks -->\n",
        )
        _git(["add", "COMMANDS.md"], self.root)
        _git(["commit", "-m", "add committed gate block"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)

        wt = self._mk_ambient("100-fix-the-widget")
        _write(
            wt / "COMMANDS.md",
            "<!-- issue-resolver-fast-checks -->\n- `true` — weakened\n<!-- /issue-resolver-fast-checks -->\n",
        )
        envelope = self._envelope(ambient="100-fix-the-widget")
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["config"]["static_checks"], ["true"])
        self.assertEqual(
            envelope["config"]["static_checks_source"], str(wt / "COMMANDS.md")
        )
        self.assertTrue(envelope["config"]["source"]["dirty"])
        self.assertTrue(
            any("uncommitted changes" in line for line in envelope["attention"]),
            "a dirty gate-config source must be surfaced, never silent: %r" % envelope["attention"],
        )

    def test_review_guidance_reads_the_ambient_worktree_with_flags_neutralised(self):
        # #99: the owner's guidance reaches S5.1's reviewer `<context>` from the same working tree
        # as the gate blocks, uncommitted edits included, with a bare reviewer flag made inert.
        _write(
            self.root / "COMMANDS.md",
            "<!-- issue-resolver-review-guidance -->\n- committed text\n<!-- /issue-resolver-review-guidance -->\n",
        )
        _git(["add", "COMMANDS.md"], self.root)
        _git(["commit", "-m", "add committed guidance block"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)

        wt = self._mk_ambient("100-fix-the-widget")
        _write(
            wt / "COMMANDS.md",
            "<!-- issue-resolver-review-guidance -->\n- never --fix generated code\n"
            "<!-- /issue-resolver-review-guidance -->\n",
        )
        envelope = self._envelope(ambient="100-fix-the-widget")
        guidance = envelope["config"]["review_guidance"]
        self.assertEqual(guidance, {
            "present": True,
            "source": str(wt / "COMMANDS.md"),
            "text": "- never `--fix` generated code",
            "neutralised": ["--fix"],
            "chars": len("- never `--fix` generated code"),
        })

    def test_review_guidance_absent_reads_not_present(self):
        envelope = self._envelope()
        self.assertEqual(
            envelope["config"]["review_guidance"],
            {"present": False, "source": None, "text": None, "neutralised": [], "chars": 0},
        )

    def test_config_fallback_chain_reads_pr_evaluator_blocks_when_resolver_blocks_absent(self):
        _write(
            self.root / "CLAUDE.md",
            "<!-- pr-evaluator-static-checks -->\n"
            "- `make lint` — hygiene\n"
            "<!-- /pr-evaluator-static-checks -->\n"
            "<!-- pr-evaluator-test-target -->\n"
            "- wrapper: `make test`\n"
            "<!-- /pr-evaluator-test-target -->\n",
        )
        _git(["add", "CLAUDE.md"], self.root)
        _git(["commit", "-m", "add pr-evaluator fallback blocks"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)

        envelope = self._envelope()
        self.assertTrue(envelope["config"]["static_checks_present"])
        self.assertEqual(envelope["config"]["static_checks"], ["make lint"])
        self.assertTrue(envelope["config"]["test_target_present"])
        self.assertTrue(envelope["config"]["canonical_suite_present"])
        self.assertIn("FAST_CHECKS_FALLBACK_STATIC_CHECKS", envelope["notices"])
        self.assertIn("TEST_TARGET_FALLBACK_PR_EVALUATOR", envelope["notices"])
        self.assertIn("CANONICAL_SUITE_FALLBACK_PR_EVALUATOR_TEST_TARGET", envelope["notices"])

    def test_sections_carries_gh_gather_spill_fields_through(self):
        envelope = self._envelope()
        self.assertIn("issue_body", envelope["sections"])
        self.assertEqual(envelope["sections"]["issue_body_mode"], "inline")

    def test_dod_parsed_from_issue_body(self):
        envelope = self._envelope()
        self.assertEqual(len(envelope["dod"]), 1)
        self.assertEqual(envelope["dod"][0]["text"], "Widget fixed")
        self.assertFalse(envelope["dod"][0]["checked"])

    def test_plan_absent_is_a_fact_not_a_decision(self):
        envelope = self._envelope()
        self.assertFalse(envelope["plan"]["present"])
        self.assertIsNone(envelope["plan"]["sha"])
        self.assertEqual(envelope["phases"], [])

    def test_distiller_bundle_paths_present(self):
        envelope = self._envelope()
        self.assertTrue(Path(envelope["distiller_bundle"]["issue_body_path"]).is_file())
        self.assertTrue(Path(envelope["distiller_bundle"]["thread_path"]).is_file())
        self.assertIsNone(envelope["distiller_bundle"]["plan_marker_path"])

    def test_attention_empty_on_a_clean_fresh_start(self):
        envelope = self._envelope()
        self.assertEqual(envelope["attention"], [])

    def test_setup_hooks_rerun_on_every_session_entry(self):
        # The hook cadence worktree-lifecycle.md requires: the setup hooks run on EVERY session
        # start (attach), fresh and re-entry alike — two prep runs, two hook executions. The block
        # must be committed+pushed: attach discovers hooks at the origin/main pin (blob reads).
        _write(
            self.root / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `echo ran >> .hook-log`\n<!-- /worktree-setup -->\n",
        )
        _git(["add", "CLAUDE.md"], self.root)
        _git(["commit", "-m", "add setup hook block"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)
        self._envelope()
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        wt = self.root / ".worktrees" / "100-fix-the-widget"
        log = (wt / ".hook-log").read_text(encoding="utf-8")
        self.assertEqual(log.count("ran"), 2)
        self.assertTrue(envelope["workspace"]["setup"]["succeeded"])


class PriorPrRowTests(PrepResolverSandboxTestCase):
    """One fixture per row of the v1 prior-PR table (docs/specs/resolver.md's 7-row table) —
    S9 DoD's first box."""

    def _stamped_active_fixture(self, source_case):
        """Copy a foreign-open-PR row fixture into a per-test temp fixture dir and re-stamp its open
        PR's `updatedAt` to `now(utc) - 2 days`, so the active/stale classification
        (`prep_resolver._classify_open_other_activity`, `_STALE_ACTIVITY_DAYS = 14`) is **time-
        invariant**. Returns the temp dir for `_envelope(fixtures_dir=...)`.

        Regression (fixed-date-rot across the stale boundary): the original row fixtures carried a
        static `updatedAt` (~2026-07-08, "recent" at S9 authoring) that aged past the 14-day cutoff on
        2026-07-23, flipping `open-pr-other-active` -> `open-pr-other-stale`. S9 made the BOUNDARY tests
        run-time-relative (`timedelta` at call time) but these subprocess-level row fixtures kept fixed
        dates. Rule: an **active-classified** fixture MUST be stamped at run time; the stale-row
        fixture's 18-month-old date (`2025-01-01`) is safe forever and stays static."""
        import datetime
        import shutil

        src = shimenv.fixture_case_dir(source_case)
        dst = Path(tempfile.mkdtemp(prefix="gh-resolver-stamp-"))
        self.addCleanup(lambda: shutil.rmtree(dst, ignore_errors=True))
        for f in src.iterdir():
            (dst / f.name).write_bytes(f.read_bytes())
        fresh = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        prs_path = dst / "open_prs.json"
        prs = json.loads(prs_path.read_text(encoding="utf-8"))
        for pr in prs:
            if "updatedAt" in pr:
                pr["updatedAt"] = fresh
        prs_path.write_text(json.dumps(prs), encoding="utf-8")
        return dst

    def test_open_pr_yours(self):
        envelope = self._envelope(
            fixture_case="prep_resolver_row_open_yours", ambient="100-fix-the-widget"
        )
        self.assertEqual(envelope["vector"]["prior_pr_row"], "open-pr-yours")
        self.assertEqual(envelope["vector"]["mode"], "continue")
        self.assertEqual(envelope["prior_pr"]["number"], 55)
        # continue mode asserts the ambient checkout is on the prior PR's own head branch.
        self.assertEqual(envelope["workspace"]["branch"], "100-fix-the-widget")
        self.assertEqual(envelope["workspace"]["expected_branch"], "100-fix-the-widget")

    def test_open_pr_other_active(self):
        # An open PR by ANOTHER author, actively worked, is operator-gated — SKILL.md:646:
        # "Do not open a competing PR" / "AskUserQuestion (header 'Open PR')". Prep must NOT
        # pre-empt that gate with mode: continue or a work-worktree side effect impersonating
        # carol's branch (S9 review finding).
        envelope = self._envelope(
            fixtures_dir=self._stamped_active_fixture("prep_resolver_row_open_other_active")
        )
        self.assertEqual(envelope["vector"]["prior_pr_row"], "open-pr-other-active")
        self.assertEqual(envelope["vector"]["mode"], "gated")
        self.assertNotEqual(envelope["vector"]["mode"], "continue")
        gate = envelope["vector"]["gate"]
        self.assertEqual(gate["reason"], "open-pr-other-active")
        self.assertEqual(gate["header"], "Open PR")
        self.assertEqual(gate["options"], ["Review it", "Leave a comment", "Wait"])
        # The gate-driving prior-PR facts are unmissable, both under vector.gate.prior_pr and the
        # top-level prior_pr fact.
        self.assertEqual(envelope["prior_pr"]["author"], "carol")
        self.assertEqual(envelope["prior_pr"]["headRefName"], "carol-fix-widget")
        self.assertFalse(envelope["prior_pr"]["isDraft"])
        self.assertIn("stale_cutoff_days", envelope["prior_pr"])
        self.assertEqual(gate["prior_pr"], envelope["prior_pr"])
        # No work-workspace side effect — never a worktree impersonating carol's branch.
        self.assertNotIn("workspace", envelope)
        self.assertFalse((self.root / ".worktrees").exists())
        self.assertNotIn("branch", envelope)
        self.assertTrue(
            any("actively worked" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_open_pr_other_stale(self):
        # Same gate discipline as the active row, but the "Stale PR" card (SKILL.md:647:
        # "Take it over" / "Start fresh") — still no worktree until the operator picks one.
        envelope = self._envelope(fixture_case="prep_resolver_row_open_other_stale")
        self.assertEqual(envelope["vector"]["prior_pr_row"], "open-pr-other-stale")
        self.assertEqual(envelope["vector"]["mode"], "gated")
        gate = envelope["vector"]["gate"]
        self.assertEqual(gate["reason"], "open-pr-other-stale")
        self.assertEqual(gate["header"], "Stale PR")
        self.assertEqual(gate["options"], ["Take it over", "Start fresh"])
        self.assertNotIn("workspace", envelope)
        self.assertFalse((self.root / ".worktrees").exists())
        self.assertNotIn("branch", envelope)
        self.assertTrue(
            any("stale" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_draft_by_same_author_is_continue(self):
        # v1 SKILL.md:648: "Draft PR ... Treat the same as an open PR by the same author" — this
        # row is scoped to YOUR OWN draft. Authorship assertion added per the S9 review finding so
        # the isDraft-first ordering bug (which routed ANY draft into continue) cannot regress.
        envelope = self._envelope(
            fixture_case="prep_resolver_row_draft", ambient="100-fix-the-widget"
        )
        self.assertEqual(envelope["vector"]["prior_pr_row"], "draft")
        self.assertEqual(envelope["vector"]["mode"], "continue")
        self.assertNotIn("gate", envelope["vector"])
        self.assertEqual(envelope["prior_pr"]["author"], "reviewer-bot")  # the fixture's current_user
        self.assertTrue(envelope["prior_pr"]["isDraft"])
        # continue mode DOES assert the ambient workspace, on the draft PR's own branch.
        self.assertEqual(envelope["workspace"]["branch"], "100-fix-the-widget")

    def test_draft_by_another_author_is_gated_not_continue(self):
        # The ordering-bug regression case itself: a FOREIGN draft must classify via the same
        # activity check as a foreign ready PR (SKILL.md:648's "Drafts are still claimed work" is
        # about protecting another author's draft, not a license to treat it as available).
        envelope = self._envelope(
            fixtures_dir=self._stamped_active_fixture("prep_resolver_row_draft_other_author")
        )
        self.assertEqual(envelope["vector"]["prior_pr_row"], "open-pr-other-active")
        self.assertEqual(envelope["vector"]["mode"], "gated")
        self.assertTrue(envelope["prior_pr"]["isDraft"])
        self.assertEqual(envelope["prior_pr"]["author"], "carol")
        self.assertNotIn("workspace", envelope)
        self.assertFalse((self.root / ".worktrees").exists())

    def test_closed_resolved(self):
        envelope = self._envelope(
            fixture_case="prep_resolver_row_closed_resolved", ambient="100-fix-the-widget"
        )
        self.assertEqual(envelope["vector"]["prior_pr_row"], "closed-resolved")
        self.assertEqual(envelope["vector"]["mode"], "fresh")
        self.assertTrue(envelope["prior_pr"]["resolved"])

    def test_closed_not_resolved(self):
        envelope = self._envelope(
            fixture_case="prep_resolver_row_closed_not_resolved", ambient="100-fix-the-widget"
        )
        self.assertEqual(envelope["vector"]["prior_pr_row"], "closed-not-resolved")
        self.assertEqual(envelope["vector"]["mode"], "fresh")
        self.assertFalse(envelope["prior_pr"]["resolved"])
        self.assertTrue(
            any("did not resolve it" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_no_prior_pr(self):
        envelope = self._envelope(
            fixture_case="prep_resolver_row_no_prior_pr", ambient="100-fix-the-widget"
        )
        self.assertEqual(envelope["vector"]["prior_pr_row"], "no-prior-pr")
        self.assertEqual(envelope["vector"]["mode"], "fresh")
        self.assertIsNone(envelope["prior_pr"])


class EpicBranchDiscoveryTests(PrepResolverSandboxTestCase):
    """Epic-branch discovery: zero / one / multiple matches (S9 DoD's second box)."""

    def test_zero_matches_computes_bootstrap_slug(self):
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_zero",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["vector"]["type"], "epic")
        self.assertEqual(envelope["epic"]["match_count"], 0)
        self.assertEqual(envelope["epic"]["bootstrap_slug"], "sandbox-fixture")
        self.assertEqual(envelope["audit_ref"], "main")
        self.assertEqual(envelope["suggested_playbook"], "epic.md")

    def test_one_match_uses_discovered_branch_verbatim(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)

        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_one",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["epic"]["match_count"], 1)
        self.assertEqual(envelope["epic"]["branch"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["audit_ref"], "epic/100-sandbox-fixture")
        self.assertIn("read_workspaces", envelope)
        self.assertEqual(envelope["read_workspaces"]["audit"]["ref"], "epic/100-sandbox-fixture")

    def test_multiple_matches_yields_ambiguous(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)
        _git(["branch", "epic/100-alt-name", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-alt-name"], self.root)

        result = self._run(
            ["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_epic_multiple",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")
        self.assertEqual(len(envelope["decision"]["context"]["candidates"]), 2)
        # No workspace side effect either — the decision is returned before any ensure runs.
        self.assertFalse((self.root / ".worktrees").exists())


class EpicIntegrationPrFactsTests(PrepResolverSandboxTestCase):
    """The integration PR opens EARLY (a draft, as soon as the epic branch is ahead of main), so
    the epic route consumes its trigger and its idempotency guard as facts: `commits_ahead` /
    `commits_ahead_base` / `integration_pr`.
    """

    def _push_epic_branch(self, extra_commit=False):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        if extra_commit:
            _git(["checkout", "epic/100-sandbox-fixture"], self.root)
            (self.root / "landed-story.txt").write_text("a story landed\n", encoding="utf-8")
            _git(["add", "landed-story.txt"], self.root)
            _git(["commit", "-m", "story #101"], self.root)
            _git(["checkout", "main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)

    def test_branch_ahead_of_main_reports_commits_ahead_and_base(self):
        self._push_epic_branch(extra_commit=True)
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_one",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["epic"]["commits_ahead"], 1)
        self.assertEqual(envelope["epic"]["commits_ahead_base"], "main")
        self.assertIsNone(envelope["epic"]["integration_pr"])

    def test_branch_level_with_main_reports_zero_and_says_so_in_attention(self):
        self._push_epic_branch()
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_one",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["epic"]["commits_ahead"], 0)
        self.assertTrue(
            any("no commits ahead of main" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_zero_matches_makes_no_pr_or_count_lookup_at_all(self):
        # A branch that does not exist on origin is 0 ahead by construction and cannot carry a PR:
        # neither lookup runs, so the bootstrap fixture's manifest stays untouched (an unlisted
        # argv MISSes in the shim and would fail this test loudly).
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_zero",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["epic"]["match_count"], 0)
        self.assertEqual(envelope["epic"]["commits_ahead"], 0)
        self.assertIsNone(envelope["epic"]["integration_pr"])

    def test_open_draft_integration_pr_lands_on_facts_epic(self):
        self._push_epic_branch(extra_commit=True)
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_integration_pr_draft",
            ambient="epic/100-sandbox-fixture",
        )
        integration_pr = envelope["epic"]["integration_pr"]
        self.assertEqual(integration_pr["number"], 300)
        self.assertTrue(integration_pr["is_draft"])
        self.assertTrue(integration_pr["is_yours"])
        self.assertEqual(integration_pr["base_ref"], "main")
        self.assertTrue(
            any("refreshes its body" in item for item in envelope["attention"]),
            envelope["attention"],
        )
        # S4.2 re-renders only the sections it owns, so it needs the current body — the PR's own
        # `## Polish` / `## Follow-ups` must survive the refresh.
        body = Path(integration_pr["body_path"]).read_text(encoding="utf-8")
        self.assertIn("## Polish\n- P1.1 · open", body)
        self.assertIn("## Follow-ups\n- #310", body)
        self.assertNotIn("INTEGRATION_PR_BODY_UNAVAILABLE", envelope["notices"])
        # #145: the integration PR is the one an epic run pushes to, so its review threads are read.
        self.assertTrue(envelope["review_threads"]["available"])
        # #139: a staged integration body in fresh mode continues no PR, so it seeds no resume.
        self.assertEqual(envelope["vector"]["mode"], "fresh")
        self.assertFalse(envelope["resume"]["present"])
        self.assertTrue(envelope["security"]["dismissals_trusted"])
        # #144: this account's integration PR — its ledger's dispositions are the operator's answers.
        self.assertTrue(envelope["polish"]["dispositions_trusted"])

    def test_an_unreadable_integration_pr_body_is_a_notice_and_a_null_path(self):
        # S4.2 skips its write on this notice: a refresh without the current body is the wipe.
        self._push_epic_branch(extra_commit=True)
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_integration_pr_body_unavailable",
            ambient="epic/100-sandbox-fixture",
        )
        integration_pr = envelope["epic"]["integration_pr"]
        self.assertEqual(integration_pr["number"], 300)
        self.assertIsNone(integration_pr["body_path"])
        self.assertIn("INTEGRATION_PR_BODY_UNAVAILABLE", envelope["notices"])

    def test_open_pr_lookup_failure_is_a_notice_never_a_decision(self):
        # The dangerous degradation: a null integration_pr carried by this notice means UNKNOWN,
        # not ABSENT — the playbook gates its create-pr on the notice's absence so an unavailable
        # lookup can never drive a duplicate integration PR.
        self._push_epic_branch(extra_commit=True)
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_open_pr_lookup_unavailable",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertIn("OPEN_PR_LOOKUP_UNAVAILABLE", envelope["notices"])
        self.assertIsNone(envelope["epic"]["integration_pr"])


class EpicDeliveredFactTests(PrepResolverSandboxTestCase):
    """#149: `facts.epic.delivered` — the files the PRs merged into the epic branch changed, staged as a
    path for the integration pass's `blast_radius.py assess --delivered`. A failed listing is a notice,
    and no branch means no fact and no notice."""

    def _push_epic_branch_with_story(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["checkout", "epic/100-sandbox-fixture"], self.root)
        (self.root / "landed-story.txt").write_text("a story landed\n", encoding="utf-8")
        _git(["add", "landed-story.txt"], self.root)
        _git(["commit", "-m", "story #101 (#201)"], self.root)
        oid = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.root), capture_output=True, encoding="utf-8", check=True
        ).stdout.strip()
        _git(["checkout", "main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)
        return oid

    def _stamped(self, source_case, oid):
        src = shimenv.fixture_case_dir(source_case)
        dst = Path(tempfile.mkdtemp(prefix="gh-resolver-delivered-"))
        self.addCleanup(lambda: shutil.rmtree(dst, ignore_errors=True))
        for f in src.iterdir():
            (dst / f.name).write_bytes(f.read_bytes())
        listing = json.loads((dst / "merged_prs.json").read_text(encoding="utf-8"))
        listing[0]["mergeCommit"]["oid"] = oid
        (dst / "merged_prs.json").write_text(json.dumps(listing), encoding="utf-8")
        return dst

    def test_the_merged_prs_files_are_staged_as_a_path(self):
        oid = self._push_epic_branch_with_story()
        envelope = self._envelope(
            issue="100", fixtures_dir=self._stamped("prep_resolver_epic_delivered", oid),
            ambient="epic/100-sandbox-fixture",
        )
        delivered = envelope["epic"]["delivered"]
        self.assertEqual((delivered["prs"], delivered["paths"], delivered["unreadable"]), (1, 1, []))
        staged = json.loads(Path(delivered["path"]).read_text(encoding="utf-8"))
        self.assertEqual(staged["paths"], ["landed-story.txt"])
        self.assertEqual(staged["prs"], [{"number": 101, "merge_commit": oid, "paths": ["landed-story.txt"]}])
        self.assertNotIn("EPIC_DELIVERED_UNAVAILABLE", envelope["notices"])

    def test_a_failed_listing_is_a_notice_and_a_null_fact(self):
        self._push_epic_branch_with_story()
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_delivered_unavailable",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertIsNone(envelope["epic"]["delivered"])
        self.assertIn("EPIC_DELIVERED_UNAVAILABLE", envelope["notices"])

    def test_an_empty_listing_stages_an_empty_set(self):
        self._push_epic_branch_with_story()
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_one", ambient="epic/100-sandbox-fixture",
        )
        delivered = envelope["epic"]["delivered"]
        self.assertEqual((delivered["prs"], delivered["paths"]), (0, 0))
        self.assertNotIn("EPIC_DELIVERED_UNAVAILABLE", envelope["notices"])

    def test_no_epic_branch_has_no_fact_and_no_notice(self):
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_zero", ambient="epic/100-sandbox-fixture",
        )
        self.assertIsNone(envelope["epic"]["delivered"])
        self.assertNotIn("EPIC_DELIVERED_UNAVAILABLE", envelope["notices"])

    def test_at_limit_and_unreadable_add_bounded_notices(self):
        from unittest import mock

        fact = {
            "prs": [{"number": 1, "merge_commit": "a" * 40, "paths": ["x.py"]}],
            "paths": ["x.py"],
            "unreadable": [2, 3, 4, 5, 6, 7, None],
            "at_limit": True,
        }
        notices = []
        with mock.patch.object(prep_resolver.branching, "delivered_paths", return_value=(fact, None)):
            delivered = prep_resolver._stage_delivered(".", "octo/widgets", "epic/1-x", self.scratch, notices)
        self.assertEqual((delivered["prs"], delivered["paths"]), (1, 1))
        self.assertEqual(delivered["unreadable"], [2, 3, 4, 5, 6, 7, None])
        self.assertEqual(json.loads(Path(delivered["path"]).read_text(encoding="utf-8")), fact)
        self.assertEqual(len(notices), 2)
        self.assertIn("500 merged PRs listed against epic/1-x", notices[0])
        self.assertIn("#2, #3, #4, #5, #6 (+2 more) into epic/1-x", notices[1])

    def test_the_fact_is_computed_on_refresh(self):
        oid = self._push_epic_branch_with_story()
        args = ["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch, "--refresh"]
        result = self._run(
            args, extra_env={"GH_SHIM_FIXTURES": str(self._stamped("prep_resolver_epic_delivered", oid))}
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self.assertEqual(envelope["epic"]["delivered"]["paths"], 1)


class EpicIntegrationPrGateDowngradeTests(PrepResolverSandboxTestCase):
    """An epic integration PR carries `Fixes #<epic>`, so the generic `#<N> in:body` search finds
    it — and a teammate-authored one classifies as a gated row, which suppresses the work-workspace
    assertion and offers a "start fresh" card whose answer on an epic is a SECOND integration PR.
    The epic branch is shared infrastructure, so that gate is downgraded — head-scoped, so a
    foreign PR on any other head still gates exactly as before.
    """

    def _push_epic_branch(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)

    def test_foreign_pr_on_the_epic_head_is_downgraded_to_continue(self):
        self._push_epic_branch()
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_foreign_pr_on_epic_head",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["vector"]["mode"], "continue")
        self.assertNotIn("gate", envelope["vector"])
        # The workspace assertion is the thing the gate would have suppressed — S1-S3 need it.
        self.assertIn("workspace", envelope)
        self.assertFalse(envelope["epic"]["integration_pr"]["is_yours"])
        # The row was gated when step 4b skipped the prior-PR fetch, so the ledger and the security
        # record come from the staged integration-PR body — else finalisation would re-mint P1.1/SEC-1.
        self.assertEqual([e["id"] for e in envelope["polish"]["entries"]], ["P1.1"])
        self.assertEqual([e["id"] for e in envelope["security"]["entries"]], ["SEC-1"])
        # #139: a teammate's record fails closed (the resolver reads no association), and the resume
        # seed comes from the integration PR's own thread, trusted authors only.
        self.assertFalse(envelope["security"]["dismissals_trusted"])
        # #144: its ledger's dispositions fail closed the same way.
        self.assertFalse(envelope["polish"]["dispositions_trusted"])
        resume = envelope["resume"]
        self.assertTrue(resume["present"])
        self.assertEqual((resume["trusted"], resume["skipped"]), (1, 1))
        staged = Path(resume["comments_path"]).read_text(encoding="utf-8")
        self.assertIn("R1.1 refuted", staged)
        self.assertNotIn("forged entry", staged)
        self.assertTrue(
            any("someone-else" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_foreign_pr_on_another_head_still_gates(self):
        self._push_epic_branch()
        envelope = self._envelope(
            issue="100", fixture_case="prep_resolver_epic_foreign_pr_other_head",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["vector"]["mode"], "gated")
        self.assertIn("gate", envelope["vector"])


class BranchCollisionSuffixTests(unittest.TestCase):
    """Collision fixture: existing -v2 branch yields -v3 (S9 DoD's third box) — exercised directly
    against `compute_branch_name` + a real git sandbox (the row fixtures' canonical path already
    covers the zero-collision case end-to-end)."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path

    def test_no_collision_is_unsuffixed(self):
        name, collided = prep_resolver.compute_branch_name(self.root, 42, "fix-thing")
        self.assertEqual(name, "42-fix-thing")
        self.assertIsNone(collided)

    def test_one_existing_branch_yields_v2(self):
        _git(["checkout", "-b", "42-fix-thing"], self.root)
        _git(["push", "origin", "42-fix-thing"], self.root)
        _git(["checkout", "main"], self.root)
        name, collided = prep_resolver.compute_branch_name(self.root, 42, "fix-thing")
        self.assertEqual(name, "42-fix-thing-v2")
        self.assertEqual(collided, ["42-fix-thing"])

    def test_existing_v2_yields_v3(self):
        _git(["checkout", "-b", "42-fix-thing"], self.root)
        _git(["push", "origin", "42-fix-thing"], self.root)
        _git(["checkout", "-b", "42-fix-thing-v2", "main"], self.root)
        _git(["push", "origin", "42-fix-thing-v2"], self.root)
        _git(["checkout", "main"], self.root)
        name, collided = prep_resolver.compute_branch_name(self.root, 42, "fix-thing")
        self.assertEqual(name, "42-fix-thing-v3")
        self.assertEqual(sorted(collided), ["42-fix-thing", "42-fix-thing-v2"])


class StoryParentEpicSearchTests(PrepResolverSandboxTestCase):
    def test_one_match_open_epic_ensures_read_workspace_on_its_branch(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)

        envelope = self._envelope(
            issue="101", fixture_case="prep_resolver_story_parent_search",
            ambient="101-story-work",
        )
        self.assertEqual(envelope["vector"]["type"], "story")
        self.assertEqual(envelope["story"]["parent_epic"]["number"], 100)
        self.assertEqual(envelope["audit_ref"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["suggested_playbook"], "story.md")
        self.assertIn("read_workspaces", envelope)


class GroundingDocsTests(PrepResolverSandboxTestCase):
    """The consuming repo's doc catalogue (skills/_shared/doc-catalogue.md), read at the AUDIT view and
    emitted with that vantage as `grounding_path` — the fitness audit's and S3 doc grounding's doc set,
    which until 4.27.0 was a hardcoded `docs/prd.md` / `docs/architecture.md` / … list."""

    ambient_default = "100-fix-the-widget"
    # Borrowed (not inherited, which would re-run that class's tests) for the time-stamped gated row.
    _stamped_active_fixture = PriorPrRowTests._stamped_active_fixture

    def _remove_catalogue(self):
        (self.root / "docs" / "README.md").unlink()
        _git(["add", "-A"], self.root)
        _git(["commit", "-m", "remove doc catalogue"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)

    def test_standard_route_reads_the_catalogue_at_the_work_workspace(self):
        """No read workspace on the standard route (audit_ref == main), so the worktree IS the audit
        view — and `grounding_path` names it, so nothing has to infer that."""
        envelope = self._envelope()
        self.assertNotIn("read_workspaces", envelope)
        self.assertEqual(envelope["grounding_path"], envelope["workspace"]["path"])
        (entry,) = envelope["grounding_docs"]
        self.assertEqual(entry["path"], "docs/prd.md")
        self.assertTrue(entry["present"])
        self.assertEqual(entry["abs_path"], str(Path(envelope["grounding_path"]) / "docs/prd.md"))
        self.assertEqual(envelope["attention"], [])

    def test_story_route_reads_the_catalogue_at_the_audit_read_workspace(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)
        envelope = self._envelope(
            issue="101", fixture_case="prep_resolver_story_parent_search", ambient="101-story-work"
        )
        self.assertEqual(envelope["grounding_path"], envelope["read_workspaces"]["audit"]["path"])
        self.assertTrue(envelope["grounding_docs"][0]["present"])

    def test_absent_catalogue_is_an_attention_line_and_a_notice_never_a_decision(self):
        self._remove_catalogue()
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertIn("DOC_CATALOGUE_ABSENT", envelope["notices"])
        self.assertTrue(
            any("no doc catalogue at the audit ref" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_declared_but_missing_doc_names_its_path(self):
        _write(
            self.root / "docs" / "README.md",
            _catalogue_block(
                SEEDED_CATALOGUE_INTERIOR
                + "- `docs/ui-design.md` — ui-design — binding — Not committed here.\n"
            ),
        )
        _git(["add", "-A"], self.root)
        _git(["commit", "-m", "catalogue names a missing doc"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)
        envelope = self._envelope()
        self.assertTrue(
            any("docs/ui-design.md" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_gated_row_reads_no_catalogue(self):
        """A gated row grounds nothing, so it reads nothing: an absent catalogue must not surface as
        attention on a run that will never audit."""
        self._remove_catalogue()
        envelope = self._envelope(
            fixtures_dir=self._stamped_active_fixture("prep_resolver_row_draft_other_author"),
            ambient=None,
        )
        self.assertEqual(envelope["vector"]["mode"], "gated")
        self.assertIsNone(envelope["grounding_path"])
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertNotIn("DOC_CATALOGUE_ABSENT", envelope["notices"])
        self.assertEqual([item for item in envelope["attention"] if "catalogue" in item], [])

    def test_refresh_reads_no_catalogue(self):
        self._remove_catalogue()
        envelope = self._envelope(extra_args=["--refresh"], ambient=None)
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertNotIn("DOC_CATALOGUE_ABSENT", envelope["notices"])
        self.assertEqual([item for item in envelope["attention"] if "catalogue" in item], [])


class UntypedSubIssueTests(PrepResolverSandboxTestCase):
    """Issue #31's resolver half. `base_ref` is what the create-pr step's `--base` slot consumes,
    so an untyped sub-issue did not merely get a mis-based worktree — absent operator intervention
    it opened a PR targeting the default branch, taking its parent epic's unmerged work with it."""

    def test_untyped_sub_issue_audits_and_bases_on_the_parent_epic_branch(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)

        envelope = self._envelope(
            issue="264", fixture_case="prep_resolver_untyped_subissue",
            ambient="264-stale-cache-key-survives-a-profile-rename",
        )
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(envelope["story"]["parent_epic"]["number"], 100)
        self.assertEqual(envelope["audit_ref"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["workspace"]["base_ref"], "epic/100-sandbox-fixture")
        # `standard.md` hardcodes the default branch as the base and omits the integration-branch
        # caveat the PR body owes its reviewer, so it is the wrong instruction set here.
        self.assertEqual(envelope["suggested_playbook"], "story.md")


class EpicPrMentionTests(PrepResolverSandboxTestCase):
    """Issue #29's resolver half. The resolver shares the prior-PR classifier, and its own
    continue short-circuit (`expected_branch = prior_pr_fact["headRefName"]`) agreed with
    workspace-open's — so the operator was already standing in the epic worktree and the
    `workspace.py attach` assertion PASSED. Nothing downstream caught it."""

    def _push_epic_branch(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)

    def test_an_epic_pr_listing_this_story_is_not_this_storys_prior_pr(self):
        self._push_epic_branch()
        envelope = self._envelope(
            issue="101", fixture_case="prep_resolver_story_epic_pr_mention",
            ambient="101-story-a-first-slice",
        )
        self.assertEqual(envelope["vector"]["mode"], "fresh")
        self.assertEqual(envelope["vector"]["prior_pr_row"], "no-prior-pr")
        self.assertIsNone(envelope["prior_pr"])
        self.assertEqual(
            envelope["prior_pr_rejected"],
            [{"number": 245, "headRefName": "epic/100-sandbox-fixture"}],
        )
        # The story is expected on its OWN branch, not the epic's integration branch.
        self.assertEqual(envelope["workspace"]["branch"], "101-story-a-first-slice")

    def test_a_merged_epic_pr_is_not_this_storys_closed_prior_pr(self):
        self._push_epic_branch()
        envelope = self._envelope(
            issue="101", fixture_case="prep_resolver_story_merged_epic_pr_mention",
            ambient="101-story-a-first-slice",
        )
        self.assertEqual(envelope["vector"]["prior_pr_row"], "no-prior-pr")
        self.assertIsNone(envelope["prior_pr"])
        # Not the `closed-not-resolved` row, so no "did not resolve it" attention line either.
        self.assertFalse(
            [line for line in envelope["attention"] if "did not resolve it" in line]
        )


class OpenQuestionHardGateTests(PrepResolverSandboxTestCase):
    """Blocked-OQ fixtures both ways (S9 DoD's fourth box): open tracker + in-scope (blocked)
    sets the hard-gate fact; a question-decision:v1 comment (Tier 1) clears it."""

    def test_open_tracker_in_scope_blocked_sets_hard_gate(self):
        envelope = self._envelope(fixture_case="prep_resolver_oq_blocked_open")
        self.assertEqual(len(envelope["open_questions"]), 1)
        entry = envelope["open_questions"][0]
        self.assertEqual(entry["disposition"], "in-scope (blocked)")
        self.assertEqual(entry["status"], "blocked")
        self.assertFalse(entry["tracker"]["resolved"])
        self.assertTrue(envelope["vector"]["comment_only"])
        self.assertEqual(envelope["suggested_playbook"], "comment-only.md")
        # One unmissable top-level boolean — S10 must not have to derive the gate from a
        # conjunction of comment_only + per-entry statuses (S9 review advisory #2).
        self.assertTrue(envelope["open_questions_gate"]["blocked"])
        self.assertEqual(len(envelope["open_questions_gate"]["blocking"]), 1)
        self.assertEqual(envelope["open_questions_gate"]["blocking"][0]["oq_id"], entry["oq_id"])

    def test_question_decision_comment_clears_the_gate(self):
        envelope = self._envelope(
            fixture_case="prep_resolver_oq_blocked_cleared", ambient="100-fix-the-widget"
        )
        entry = envelope["open_questions"][0]
        self.assertEqual(entry["disposition"], "in-scope (blocked)")
        self.assertEqual(entry["status"], "clear")
        self.assertTrue(entry["tracker"]["resolved"])
        self.assertTrue(entry["tracker"]["decision_recorded"])
        self.assertFalse(envelope["vector"]["comment_only"])
        self.assertEqual(envelope["suggested_playbook"], "standard.md")
        self.assertFalse(envelope["open_questions_gate"]["blocked"])
        self.assertEqual(envelope["open_questions_gate"]["blocking"], [])


class DistillerBundleThresholdTests(PrepResolverSandboxTestCase):
    """Distiller bundle is staged paths, never above-threshold inline bytes (S9 DoD's fifth box)."""

    ambient_default = "100-fix-the-widget"

    def test_above_threshold_body_is_staged_as_a_path_not_inline_bytes(self):
        envelope = self._envelope(fixture_case="prep_resolver_distiller_spilled")
        self.assertEqual(envelope["sections"]["issue_body_mode"], "path")
        self.assertNotIn("issue_body", envelope["sections"])
        bundle_path = envelope["distiller_bundle"]["issue_body_path"]
        self.assertTrue(Path(bundle_path).is_file())
        self.assertGreater(Path(bundle_path).stat().st_size, 25600)
        # Reuses gh_gather's own spilled file rather than writing a second copy.
        self.assertEqual(bundle_path, envelope["sections"]["issue_body_path"])


class PhasesParsingTests(PrepResolverSandboxTestCase):
    ambient_default = "100-fix-the-widget"

    def _with_issue_body(self, body):
        """`prep_resolver_plan_with_phases` (phases claim `(none)` then `1`) with the issue body swapped."""
        src = shimenv.fixture_case_dir("prep_resolver_plan_with_phases")
        dst = Path(tempfile.mkdtemp(prefix="gh-resolver-dodcov-"))
        self.addCleanup(lambda: shutil.rmtree(dst, ignore_errors=True))
        for f in src.iterdir():
            (dst / f.name).write_bytes(f.read_bytes())
        view = json.loads((dst / "issue_view.json").read_text(encoding="utf-8"))
        view["body"] = body
        (dst / "issue_view.json").write_text(json.dumps(view), encoding="utf-8")
        return self._envelope(fixtures_dir=dst)

    def test_dod_coverage_reads_the_issue_body_against_the_plan(self):
        envelope = self._envelope(fixture_case="prep_resolver_plan_with_phases")
        cov = envelope["dod_coverage"]
        self.assertEqual((cov["present"], cov["dod_count"], cov["max_claimed"]), (True, 1, 1))
        self.assertFalse(cov["drift"])
        self.assertFalse(any(a.startswith("DoD drift") for a in envelope["attention"]))

    def test_claims_against_acceptance_criteria_reach_the_envelope_as_drift(self):
        envelope = self._with_issue_body(
            "## Summary\nDo the thing.\n\n## Acceptance criteria\n- [ ] Widget fixed"
        )
        self.assertEqual(envelope["dod"], [])
        self.assertTrue(envelope["dod_coverage"]["drift"])
        self.assertTrue(
            any(a.startswith("DoD drift") and "`## Acceptance criteria`" in a
                for a in envelope["attention"]),
            envelope["attention"],
        )

    def test_plan_with_phases_is_parsed_and_sha_extracted(self):
        envelope = self._envelope(fixture_case="prep_resolver_plan_with_phases")
        self.assertTrue(envelope["plan"]["present"])
        self.assertEqual(envelope["plan"]["sha"], "1a2b3c4")
        self.assertEqual(len(envelope["phases"]), 2)
        self.assertEqual(envelope["phases"][0]["kind"], "code-shipping")
        self.assertEqual(envelope["phases"][1]["depends_on"], [1])
        # `checkpoint` passes through verbatim; an absent line is `None`, which the spine reads as
        # `pause` (references/phase-continuation.md).
        self.assertEqual(
            [p["checkpoint"] for p in envelope["phases"]], ["continue", None]
        )
        self.assertIsNotNone(envelope["distiller_bundle"]["plan_marker_path"])
        self.assertTrue(Path(envelope["distiller_bundle"]["plan_marker_path"]).is_file())


class MarkerAmbiguousTests(PrepResolverSandboxTestCase):
    """Duplicate plan-marker comments -> MARKER_AMBIGUOUS, forwarded verbatim from gh_gather's own
    decision (architecture.md §3's closed decision-code set is shared, not re-derived)."""

    def test_two_plan_marker_comments_yields_marker_ambiguous(self):
        result = self._run(
            ["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_marker_ambiguous",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        # No workspace side effect either — the decision short-circuits before any ensure runs.
        self.assertFalse((self.root / ".worktrees").exists())


class TargetIsPrTests(PrepResolverSandboxTestCase):
    """A PR number given as the issue target -> TARGET_IS_PR, forwarded verbatim from gh_gather's
    own decision BEFORE any workspace ensure (the live-incident regression: prep reported
    `target.kind: "issue"`, derived a fresh vector, and ensured a work worktree + branch for a
    target that doesn't exist as an issue — the operator had to diagnose it from the body text
    and a stray worktree was left on disk)."""

    def test_pr_number_yields_target_is_pr_with_no_workspace_side_effect(self):
        result = self._run(
            ["89", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_target_is_pr",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "TARGET_IS_PR")
        # The load-bearing half of the fix: NO worktree, NO branch — the decision fires before
        # any workspace side effect (the fixture manifest carries only the single `issue view`
        # entry, so any later gh call would be a loud shim miss too).
        self.assertFalse((self.root / ".worktrees").exists())

    def test_decision_context_offers_the_linked_origin_issue(self):
        result = self._run(
            ["89", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_target_is_pr",
        )
        envelope = _parse_one_envelope(result.stdout)
        context = envelope["decision"]["context"]
        self.assertEqual(context["number"], 89)
        self.assertEqual(context["linked_issues"], [62])
        self.assertTrue(
            any("#62" in option for option in envelope["decision"]["options"]),
            envelope["decision"]["options"],
        )


class AuthRequiredTests(PrepResolverSandboxTestCase):
    def test_auth_failure_on_first_gh_call_yields_auth_required(self):
        result = self._run(
            ["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_auth_required",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AUTH_REQUIRED")


class WorkspaceMismatchTests(PrepResolverSandboxTestCase):
    """v3: the ambient-checkout assertion — WORKSPACE_MISMATCH decisions (workspace.py's, forwarded
    verbatim through prep's composition), the linked-branch adoption rung, and the `-vN`
    no-recompute acceptance. These replace the retired ROOT_* propagation coverage: stage preps no
    longer police root state (that gate lives on the workspace-creating paths only)."""

    def _mismatch(self, envelope, reason):
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "WORKSPACE_MISMATCH")
        self.assertEqual(envelope["decision"]["context"]["reason"], reason)

    def test_wrong_branch_is_a_branch_mismatch(self):
        envelope = self._envelope(
            fixture_case="prep_resolver_row_no_prior_pr", ambient="999-unrelated-work"
        )
        self._mismatch(envelope, "branch_mismatch")
        self.assertEqual(envelope["decision"]["context"]["actual_branch"], "999-unrelated-work")

    def test_session_at_the_project_root_names_workspace_open(self):
        # The "operator forgot workspace-open" surface: prep run with the session sitting at the
        # main checkout itself.
        result = self._run(
            ["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_row_no_prior_pr",
            cwd=str(self.root),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self._mismatch(envelope, "at_project_root")
        self.assertTrue(
            any("workspace-open" in option for option in envelope["decision"]["options"])
        )

    def test_detached_head_is_a_mismatch(self):
        detached = self.root / ".worktrees" / "detached-view"
        detached.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "--detach", str(detached), "origin/main"], self.root)
        result = self._run(
            ["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_row_no_prior_pr",
            cwd=str(detached),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self._mismatch(envelope, "detached_head")

    def test_linked_branch_is_adopted_verbatim(self):
        # gh issue develop --list reports one linked branch whose name no slug computation would
        # ever produce — the ladder adopts it, and an ambient checkout on it passes exactly.
        envelope = self._envelope(
            fixture_case="prep_resolver_linked_adoption", ambient="100-custom-linked"
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["workspace"]["branch"], "100-custom-linked")
        self.assertEqual(envelope["workspace"]["expected_branch"], "100-custom-linked")

    def test_vn_suffix_is_never_recomputed_against_the_pushed_branch(self):
        # The self-mismatch drift: the ambient branch already exists on origin (workspace-open
        # pushed it). Re-running collision naming would count it and expect `-v2`; the ladder's
        # pattern acceptance (`^100-`) must pass the ambient branch as-is instead.
        wt = self._mk_ambient("100-fix-the-widget")
        _git(["push", "-u", "origin", "100-fix-the-widget"], wt)
        envelope = self._envelope(
            fixture_case="prep_resolver_row_no_prior_pr", ambient="100-fix-the-widget"
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["workspace"]["branch"], "100-fix-the-widget")

    def test_checkout_behind_the_remote_branch_is_stale(self):
        wt = self._mk_ambient("100-fix-the-widget")
        _git(["push", "-u", "origin", "100-fix-the-widget"], wt)
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        _git(["fetch", "origin", "100-fix-the-widget"], other.path)
        _git(["checkout", "100-fix-the-widget"], other.path)
        _write(Path(other.path) / "pushed.txt", "newer\n")
        _git(["add", "pushed.txt"], other.path)
        _git(["commit", "-m", "pushed elsewhere"], other.path)
        _git(["push", "origin", "100-fix-the-widget"], other.path)
        envelope = self._envelope(
            fixture_case="prep_resolver_row_no_prior_pr", ambient="100-fix-the-widget"
        )
        self._mismatch(envelope, "stale_checkout")

    def test_gh_linking_unsupported_degrades_with_a_notice(self):
        # The default row fixture's shim has no `gh issue develop` entry in older harness runs —
        # this fixture-level MISS (non-zero exit, no auth signature) must degrade to the computed
        # rung with the ISSUE_LINK_UNSUPPORTED notice, never crash. Simulated by pointing at a
        # fixture copy whose develop entry is a non-zero exit.
        import shutil

        src = shimenv.fixture_case_dir("prep_resolver_row_no_prior_pr")
        dst = Path(tempfile.mkdtemp(prefix="gh-resolver-nolink-"))
        self.addCleanup(lambda: shutil.rmtree(dst, ignore_errors=True))
        for f in src.iterdir():
            (dst / f.name).write_bytes(f.read_bytes())
        manifest = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
        for entry in manifest:
            if entry["argv"][:3] == ["issue", "develop", "--list"]:
                entry["exit_code"] = 1
                entry.pop("stdout_file", None)
        (dst / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        envelope = self._envelope(fixtures_dir=dst, ambient="100-fix-the-widget")
        self.assertEqual(envelope["status"], "ok")
        self.assertIn("ISSUE_LINK_UNSUPPORTED", envelope["notices"])
        self.assertEqual(envelope["workspace"]["branch"], "100-fix-the-widget")


class RefreshModeTests(PrepResolverSandboxTestCase):
    """--refresh re-derives volatile facts without re-running workspace-ensure hooks or
    re-reading gate config (mirrors tests/test_prep_evaluator.py's RefreshModeTests contract) —
    but UNLIKE prep_evaluator, prep_resolver's --refresh path still shells out to `git ls-remote`
    for epic-branch discovery / branch-collision suffixing (docs/specs/resolver.md names these as
    volatile facts a re-entrant run must re-derive, e.g. a branch collision that appeared since
    the first run). That means these tests need a REAL local git sandbox `--root` — passing none
    (prep_resolver.py defaults `--root` to `.`) would run those `git` calls against this test
    process's own cwd, which on a real contributor checkout is a clone of the actual GitHub
    remote: a live-network call the offline harness must never make (architecture.md §10; the
    orchestrator's Linux-container sanity check caught exactly this — `git ls-remote` inside the
    live repo checkout fails hard with no network, and silently succeeds instead of failing the
    test on a machine that does have network access to that remote, which is the worse failure
    mode). `PrepResolverSandboxTestCase.setUp` gives every test here the same local
    origin+clone every other test in this suite uses — never a real URL.
    """

    def test_refresh_never_touches_workspace_hooks_or_gate_config(self):
        envelope = self._envelope(fixture_case="prep_resolver_row_no_prior_pr", extra_args=["--refresh"])
        self.assertEqual(envelope["status"], "ok")
        self.assertNotIn("workspace", envelope)
        self.assertEqual(envelope["config"], {})
        self.assertFalse(envelope["root"]["fresh"])
        # No worktree was created — the workspace-ensure step (the part --refresh skips) never ran.
        self.assertFalse((self.root / ".worktrees").exists())

    def test_refresh_still_reports_vector_and_dod(self):
        envelope = self._envelope(fixture_case="prep_resolver_row_no_prior_pr", extra_args=["--refresh"])
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(len(envelope["dod"]), 1)

    def test_refresh_skips_the_expected_branch_ladder_entirely(self):
        # v3: --refresh omits the workspace assertion, so the ladder (and its gh issue develop
        # lookup / ls-remote naming calls) never runs — the branch already exists and is the
        # ambient checkout's own state, not a volatile fact to re-derive. The retired v2 test here
        # asserted --refresh re-ran branch-collision naming; the minted-branch fact is gone.
        envelope = self._envelope(fixture_case="prep_resolver_row_no_prior_pr", extra_args=["--refresh"])
        self.assertNotIn("branch", envelope)
        self.assertNotIn("workspace", envelope)


class SingleInvocationBudgetTests(PrepResolverSandboxTestCase):
    """Single-invocation budget: the canonical (standard/fresh/no-OQ/no-epic) path's shim call
    count is bounded and stable, both an upper AND a lower bound (S9 DoD's "conformance + call
    budget as S6")."""

    def test_shim_call_count_for_one_happy_run_is_exactly_six(self):
        # gh calls on the canonical no-prior-PR path: issue view (+deps), paginated comments,
        # open-PR search, current-user, closed-PR search (only reached because open_prs was
        # empty), and the linked-branch lookup (`gh issue develop --list`, v3's ladder rung 2) —
        # six, no more, no fewer. The shim exits 2 loudly on any un-fixtured argv
        # (tests/README.md), so if this test passes at all, every gh call prep made was one of
        # these six.
        result = self._run(
            ["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_row_no_prior_pr",
            cwd=str(self._mk_ambient("100-fix-the-widget")),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        manifest_path = shimenv.fixture_case_dir("prep_resolver_row_no_prior_pr") / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 6)

    def test_no_gh_call_is_repeated_within_one_run(self):
        manifest_path = shimenv.fixture_case_dir("prep_resolver_row_no_prior_pr") / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        argv_tuples = [tuple(entry["argv"]) for entry in manifest]
        self.assertEqual(len(argv_tuples), len(set(argv_tuples)), "duplicate argv in manifest")

    def test_open_pr_row_short_circuits_the_closed_pr_search(self):
        # When an open PR already exists, the closed-PR search must NOT run at all (mode
        # derivation is mutually exclusive across rows), and continue mode's ladder rung 1 means
        # the linked-branch lookup is skipped too — a strictly SMALLER manifest than the
        # no-prior-PR case proves the lower bound half of the two-sided budget.
        #
        # 5, not 4, since #46: continue mode adds ONE `gh pr view` for the prior PR's body, which is
        # the only way to reconcile its `## Phase tracker` row set against the plan's `## Phases`
        # (`gh_gather` fetches an open PR's body for reference filtering and then strips it, and
        # keeping it would widen the payload EVERY prep carries). Fresh mode still makes none — see
        # TrackerFactTests.test_fresh_row_has_no_tracker_and_makes_no_pr_view_call.
        #
        # 6, not 5, since #145: continue mode reads the prior PR's review threads (one `gh api
        # graphql` page; no permission lookup, since no thread is resolved). Fresh mode reads none.
        result = self._run(
            ["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_resolver_row_open_yours",
            cwd=str(self._mk_ambient("100-fix-the-widget")),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        manifest_path = shimenv.fixture_case_dir("prep_resolver_row_open_yours") / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 6)


class ShippedRecordFactsTests(PrepResolverSandboxTestCase):
    """`facts.plan.shipped` + `distiller_bundle.plan_shipped_path`
    (skills/_shared/plan-shipped-phases.md). Once a revise has relocated a shipped phase's entries,
    the plan comment alone is no longer the whole locked plan, so the distiller gets the records for
    the continuing PR — and never another PR's, which a Start-fresh left behind.
    """

    ambient_default = "100-fix-the-widget"

    def test_continue_mode_stages_this_prs_records_for_the_distiller(self):
        envelope = self._envelope(fixture_case="prep_resolver_plan_shipped")
        shipped = envelope["plan"]["shipped"]
        self.assertEqual(shipped["pr"], 55)
        self.assertEqual([e["phase"] for e in shipped["entries"]], [1])
        text = Path(envelope["distiller_bundle"]["plan_shipped_path"]).read_text(encoding="utf-8")
        self.assertEqual(shipped["body_path"], envelope["distiller_bundle"]["plan_shipped_path"])
        self.assertIn(":phase:1 -->", text)
        self.assertNotIn(":phase:2 -->", text)

    def test_records_follow_the_plans_pointer_not_the_continuing_pr(self):
        # The plan's pointer names #31 while the resolver continues #55 — the records on #31 are the
        # only copy of phase 2's entries, so they are read, and the mismatch is surfaced.
        envelope = self._envelope(fixture_case="prep_resolver_plan_shipped_pointer")
        shipped = envelope["plan"]["shipped"]
        self.assertEqual(shipped["pr"], 31)
        self.assertEqual([e["phase"] for e in shipped["entries"]], [2])
        self.assertEqual(envelope["distiller_bundle"]["plan_shipped_path"], shipped["body_path"])
        self.assertTrue(any("only copy" in line for line in envelope["attention"]))

    def test_no_records_leaves_the_bundle_path_empty(self):
        envelope = self._envelope(fixture_case="prep_resolver_tracker_clean")
        self.assertFalse(envelope["plan"]["shipped"]["present"])
        self.assertIsNone(envelope["distiller_bundle"]["plan_shipped_path"])

    def test_continue_mode_reads_the_prior_prs_review_threads(self):
        # #145: `available` proves the reader ran against the fixture rather than missing it.
        threads = self._envelope(fixture_case="prep_resolver_tracker_clean")["review_threads"]
        self.assertTrue(threads["present"])
        self.assertTrue(threads["available"])
        self.assertTrue(Path(threads["conversation_path"]).is_file())

    def test_fresh_mode_reads_no_review_threads(self):
        envelope = self._envelope(fixture_case="prep_resolver_row_no_prior_pr", ambient="100-fix-the-widget")
        self.assertEqual(envelope["review_threads"], {"present": False})


class TrackerFactTests(PrepResolverSandboxTestCase):
    """#46: `facts.tracker` — the PR's `## Phase tracker` row set diffed against the plan's phases.

    A planner revise can insert a phase and renumber the unshipped tail. The tracker is a separate
    artifact keyed by the same numbers, and the resolver's continue cursor selects BY ROW NUMBER — so
    without this diff the cursor silently points at different work, and a tracker with fewer rows than
    the plan reads as complete and flips the PR draft to ready early.

    The diff is fully determined by the two artifacts, so it is a fact (computed here), not a judgment
    the playbook re-derives.
    """

    ambient_default = "100-fix-the-widget"

    def test_continue_mode_carries_the_parsed_tracker_rows(self):
        tracker = self._envelope(fixture_case="prep_resolver_tracker_clean")["tracker"]
        self.assertTrue(tracker["present"])
        self.assertEqual(
            tracker["rows"][0],
            {
                "checked": True, "phase": 1, "title": "substrate", "sub_label": None,
                "commit_sha": "abc1111", "annotation": None,
            },
        )
        self.assertEqual([row["phase"] for row in tracker["rows"]], [1, 2, 3, 4, 5, 6])

    def test_an_inline_pr_body_is_staged_as_a_path(self):
        # Staged paths, always: a continue-mode PR-body write needs a fetched base to restage from,
        # and before this an inline (small) body was never emitted at all — only a spilled one was.
        envelope = self._envelope(fixture_case="prep_resolver_tracker_clean")
        tracker = envelope["tracker"]
        self.assertEqual(tracker["body_mode"], "inline")
        staged = Path(tracker["body_path"])
        self.assertTrue(staged.is_file())
        self.assertTrue(str(staged).startswith(envelope["scratch"]))
        self.assertIn("## Phase tracker", staged.read_text(encoding="utf-8"))

    def test_workspace_reports_the_pushed_tip(self):
        # Nothing unpushed in the sandbox: the pushed tip IS the local HEAD.
        workspace = self._envelope(fixture_case="prep_resolver_tracker_clean")["workspace"]
        self.assertEqual(workspace["unpushed_commits"], 0)
        self.assertEqual(workspace["upstream_sha"], workspace["sha"])

    def test_last_shipped_falls_back_unreachable_on_fixture_shas(self):
        # The fixture's SHAs are not commits in the sandbox, so nothing is an ancestor of HEAD: the
        # numerically last ticked row is reported with `reachable: false` and the spine falls back
        # to `facts.workspace.base_ref`. A convenience fact — never a decision, never a crash.
        tracker = self._envelope(fixture_case="prep_resolver_tracker_clean")["tracker"]
        self.assertEqual(tracker["last_shipped"]["phase"], 5)
        self.assertFalse(tracker["last_shipped"]["reachable"])
        self.assertRegex(tracker["last_shipped"]["commit_sha"], r"^[0-9a-f]{7}$")

    def test_last_shipped_picks_the_shipped_phase_nearest_head(self):
        # Phases ship in `depends-on` order, not numeric order: phase 2 shipped first, then phase 1.
        # The non-final review's diff base is the shipped commit nearest HEAD — phase 1 — not the
        # highest ticked number.
        wt = self._mk_ambient("100-fix-the-widget")
        shas = []
        for name in ("phase-two", "phase-one"):
            _write(wt / ("%s.txt" % name), name + "\n")
            _git(["add", "."], wt)
            _git(["commit", "-m", "ship " + name], wt)
            shas.append(_git(["rev-parse", "--short=7", "HEAD"], wt).strip())
        _write(wt / "wip.txt", "unshipped\n")
        _git(["add", "."], wt)
        _git(["commit", "-m", "in-flight phase 3 work"], wt)
        rows = [
            {"checked": True, "phase": 2, "title": "b", "sub_label": None, "commit_sha": shas[0], "annotation": None},
            {"checked": True, "phase": 1, "title": "a", "sub_label": None, "commit_sha": shas[1], "annotation": None},
            {"checked": True, "phase": 4, "title": "op", "sub_label": "measure", "commit_sha": None, "annotation": "operator phase 4, applied 2026-01-01"},
            {"checked": False, "phase": 3, "title": "c", "sub_label": None, "commit_sha": None, "annotation": None},
        ]
        self.assertEqual(
            prep_resolver.last_shipped_phase(rows, str(wt)),
            {"phase": 1, "commit_sha": shas[1], "reachable": True},
        )
        # A force-pushed-away SHA is skipped, not fatal; with no reachable candidate the fallback is
        # the numerically last ticked main row, flagged unreachable.
        gone = [dict(rows[0], commit_sha="0000000"), dict(rows[1], commit_sha="1111111")]
        self.assertEqual(
            prep_resolver.last_shipped_phase(gone, str(wt)),
            {"phase": 2, "commit_sha": "0000000", "reachable": False},
        )
        self.assertIsNone(prep_resolver.last_shipped_phase([rows[3]], str(wt)))

    def test_a_matching_tracker_reports_no_drift(self):
        diff = self._envelope(fixture_case="prep_resolver_tracker_clean")["tracker"]["diff"]
        self.assertEqual(diff["missing"], [])
        self.assertEqual(diff["dropped"], [])
        self.assertEqual(diff["retitled"], [])
        self.assertFalse(diff["conflict"])

    def test_fresh_row_has_no_tracker_and_makes_no_pr_view_call(self):
        # The no-prior-PR manifest has no `pr view` entry, so an extra call here would be a loud shim
        # miss rather than a silent pass — that is the assertion.
        tracker = self._envelope(fixture_case="prep_resolver_row_no_prior_pr")["tracker"]
        self.assertFalse(tracker["present"])
        self.assertEqual(tracker["rows"], [])
        self.assertIsNone(tracker["last_shipped"])
        self.assertFalse(tracker["diff"]["conflict"])

    def test_the_live_insert_shape_is_a_silent_rebuild(self):
        # Plan phases 1-7, tracker rows 1-6 with 1-5 ticked and row 6 unticked and titled for what is
        # now phase 7. This MUST NOT gate: nothing shipped has moved, so the resolver just rebuilds —
        # it adds row 7 and adopts the plan's title for row 6.
        #
        # Row 6 is NOT `dropped`: `dropped` keys on a row whose NUMBER has no plan phase, and 6 still
        # has one (the inserted work). An unticked row whose title drifted is rebuilt silently and
        # deliberately not reported — there is no separate action for it.
        diff = self._envelope(fixture_case="prep_resolver_tracker_insert")["tracker"]["diff"]
        self.assertEqual(diff["missing"], [7])
        self.assertEqual(diff["dropped"], [])
        self.assertEqual(diff["retitled"], [])
        self.assertFalse(diff["conflict"])

    def test_shifted_ticked_row_is_a_conflict_naming_its_destination(self):
        diff = self._envelope(fixture_case="prep_resolver_tracker_shifted")["tracker"]["diff"]
        self.assertEqual(
            diff["shifted"],
            [{"row_phase": 6, "plan_phase": 7, "title": "end-to-end proof", "commit_sha": "abc6666"}],
        )
        self.assertEqual(diff["removed_shipped"], [])
        self.assertTrue(diff["conflict"])

    def test_removed_shipped_row_is_a_conflict(self):
        diff = self._envelope(fixture_case="prep_resolver_tracker_removed_shipped")["tracker"]["diff"]
        self.assertEqual(
            diff["removed_shipped"],
            [{"phase": 8, "title": "the retired harness", "commit_sha": "abc8888"}],
        )
        self.assertTrue(diff["conflict"])

    def test_retitled_ticked_row_is_reported_but_is_not_a_conflict(self):
        # revise-reconciliation.md makes a shipped phase's ships/deliverable/kind change HARD and
        # deliberately omits `title`, so a cosmetic retitle is legitimately SOFT at the planner — a
        # title-only conflict would fire on every one of them.
        diff = self._envelope(fixture_case="prep_resolver_tracker_retitled")["tracker"]["diff"]
        self.assertEqual(len(diff["retitled"]), 1)
        entry = diff["retitled"][0]
        self.assertEqual(entry["phase"], 4)
        self.assertEqual(entry["row_title"], "the anchor read")
        self.assertEqual(entry["plan_title"], "the anchor read capability")
        self.assertFalse(diff["conflict"])

    def test_unticked_title_drift_is_not_reported(self):
        # Row 6 in the retitled fixture is unticked; an unticked row is rebuilt silently, so reporting
        # its drift would be noise the playbook has no action for.
        diff = self._envelope(fixture_case="prep_resolver_tracker_retitled")["tracker"]["diff"]
        self.assertEqual([entry["phase"] for entry in diff["retitled"]], [4])

    def test_refresh_recomputes_the_tracker(self):
        # The PR body is volatile state, not setup state — `--refresh` must not skip it.
        tracker = self._envelope(
            fixture_case="prep_resolver_tracker_insert", extra_args=["--refresh"]
        )["tracker"]
        self.assertTrue(tracker["present"])
        self.assertEqual(tracker["diff"]["missing"], [7])


class RevisionAndPolishFactTests(PrepResolverSandboxTestCase):
    """`facts.polish` (skills/_shared/polish-ledger.md) and `facts.revision` (spine S4's revision
    run): both come off the continue-mode PR fetch the tracker already makes, so neither costs a
    call. The revision run exists because S4's cursor — "the first unticked phase" — selects nothing
    once every code-shipping phase is ticked, which is exactly where an evaluator soft-reject lands.
    """

    ambient_default = "100-fix-the-widget"

    def test_all_ticked_continue_run_is_a_revision_with_its_reasons(self):
        facts = self._envelope(fixture_case="prep_resolver_revision")
        self.assertEqual(
            facts["revision"],
            {"active": True, "reasons": ["polish_apply", "review_threads", "review_at_head"]},
        )

    def test_a_thread_a_trusted_human_waits_on_is_reason_review_threads(self):
        # #145: the fixture's PR has an unanswered owner-rooted thread and an outside account's thread.
        # Only the first is iteration-1 input; the second gates nothing and is only counted.
        threads = self._envelope(fixture_case="prep_resolver_revision")["review_threads"]
        self.assertTrue(threads["available"])
        self.assertEqual(
            [t["id"] for t in prep_resolver.threads_waiting(threads)], ["PRRT_waiting"]
        )
        self.assertEqual(threads["untrusted"]["threads"], 1)

    def test_the_out_of_plan_section_is_scanned_from_the_same_pr_fetch(self):
        # #145: S5.2 keeps these lines across phases, so prep hands them over as a fact.
        out_of_plan = self._envelope(fixture_case="prep_resolver_revision")["out_of_plan"]
        self.assertTrue(out_of_plan["present"])
        self.assertEqual([e["path"] for e in out_of_plan["entries"]], ["lib/login/backoff.rb"])
        self.assertEqual(out_of_plan["unparsed"], ["- `lib/login.rb`"])

    def test_fresh_mode_has_no_out_of_plan_section(self):
        facts = self._envelope(fixture_case="prep_resolver_row_no_prior_pr")
        self.assertEqual(facts["out_of_plan"], parse.absent_out_of_plan())

    def test_the_polish_ledger_is_parsed_and_an_off_vocabulary_line_is_unparsed(self):
        polish = self._envelope(fixture_case="prep_resolver_revision")["polish"]
        self.assertTrue(polish["present"])
        self.assertEqual([e["id"] for e in polish["entries"]], ["P2.1", "P5.1"])
        self.assertEqual(polish["entries"][0]["disposition"], "apply")
        self.assertEqual(polish["entries"][0]["note"], "misleading to operators")
        self.assertEqual(len(polish["unparsed"]), 1)

    def test_the_security_record_is_scanned_from_the_same_pr_fetch(self):
        # skills/_shared/security-review.md: finalisation matches its pass's findings against these.
        security = self._envelope(fixture_case="prep_resolver_revision")["security"]
        self.assertTrue(security["present"])
        self.assertEqual(security["reviewed_sha"], "3f9c2ab41d0e8c7b6a5f4e3d2c1b0a9f8e7d6c5b")
        self.assertEqual([e["id"] for e in security["entries"]], ["SEC-1", "SEC-2"])
        self.assertEqual([e["disposition"] for e in security["entries"]], ["dismissed", "open"])
        self.assertEqual(security["unparsed"], [])
        # #145: neither entry cites a review thread, so neither carries one.
        self.assertEqual([e["thread"] for e in security["entries"]], [None, None])

    def test_the_security_fact_names_the_record_section_for_the_writer(self):
        # security-review.md step 6: finalisation rewrites only the section `record` names and never one
        # `other_sections` lists, so the resolver's fact must carry both (epic DoD 15).
        security = self._envelope(fixture_case="prep_resolver_revision")["security"]
        self.assertEqual(security["record"], {"heading": "## Security review", "line": 16})
        self.assertEqual(security["other_sections"], [])

    # #139 SEC-2: the iteration-1 resume hint seeds its refuted items, `check: overturned` reopenings,
    # defect fixes, `R` id and `Cold read:` record only from comments prep staged as trusted. Any
    # GitHub user can comment on a public PR, so a forged `Settled (not addressed):` entry (`kept`,
    # ` — check: upheld`) must never reach that file — else it settles a real finding and skips step 4.

    def test_only_trusted_comments_are_staged_as_the_resume_seed(self):
        resume = self._envelope(fixture_case="prep_resolver_resume_untrusted")["resume"]
        self.assertTrue(resume["present"])
        self.assertEqual((resume["trusted"], resume["skipped"]), (1, 2))
        staged = json.loads(Path(resume["comments_path"]).read_text(encoding="utf-8"))
        self.assertEqual([c["url"] for c in staged], ["https://github.com/octo/widgets/pull/55#issuecomment-1"])
        text = Path(resume["comments_path"]).read_text(encoding="utf-8")
        self.assertIn("R4.1 refuted", text)
        for forged in ("FORGED", "check: upheld", "kept", "Cold read:"):
            self.assertNotIn(forged, text)

    def test_fresh_mode_has_no_resume_seed(self):
        resume = self._envelope(fixture_case="prep_resolver_row_no_prior_pr")["resume"]
        self.assertEqual(resume, {"present": False, "comments_path": None, "trusted": 0, "skipped": 0})

    def test_the_interrupted_fact_reads_trusted_pr_and_issue_loop_comments(self):
        # #155: wired end to end — the staged trusted PR comments and the issue thread both reach the fact,
        # and a later forged record (untrusted, on the PR and on the issue) never wins.
        import shutil
        src = shimenv.fixture_case_dir("prep_resolver_resume_untrusted")
        dst = Path(tempfile.mkdtemp(prefix="gh-resolver-interrupted-"))
        self.addCleanup(lambda: shutil.rmtree(dst, ignore_errors=True))
        for f in src.iterdir():
            (dst / f.name).write_bytes(f.read_bytes())
        sha, forged = "d" * 40, "e" * 40
        record = "<!-- review-loop:v1 -->\n## Review loop — phase 2\nInterrupted: phase 2 @ %s · build — mid S5\nReview runs: phase 2 · 1 · interrupted"
        pr_view = json.loads((dst / "pr_view.json").read_text(encoding="utf-8"))
        pr_view["comments"] += [
            {"author": {"login": "octocat"}, "authorAssociation": "OWNER", "body": record % sha,
             "createdAt": "2026-10-23T10:00:00Z", "url": "https://github.com/octo/widgets/pull/55#issuecomment-9"},
            {"author": {"login": "drive-by"}, "authorAssociation": "NONE", "body": record % forged,
             "createdAt": "2026-10-24T10:00:00Z", "url": "https://github.com/octo/widgets/pull/55#issuecomment-10"},
        ]
        (dst / "pr_view.json").write_text(json.dumps(pr_view), encoding="utf-8")
        issue_comments = json.loads((dst / "comments.json").read_text(encoding="utf-8"))
        issue_comments.append(dict(issue_comments[0], id=7199, node_id="IC_forged", user={"login": "drive-by"},
                                   author_association="NONE", body=record % forged,
                                   created_at="2026-10-25T10:00:00Z", html_url="https://github.com/octo/widgets/issues/100#issuecomment-11"))
        (dst / "comments.json").write_text(json.dumps(issue_comments), encoding="utf-8")
        interrupted = self._envelope(fixtures_dir=dst)["interrupted"]
        self.assertEqual(
            {k: interrupted[k] for k in ("present", "phase", "sha", "kind", "active", "url")},
            {"present": True, "phase": 2, "sha": sha, "kind": "build", "active": True,
             "url": "https://github.com/octo/widgets/pull/55#issuecomment-9"},
        )

    def test_a_thread_with_no_interrupted_record_reports_it_absent(self):
        # #155: every envelope carries `facts.interrupted`; this fixture's thread records no interrupted pass.
        interrupted = self._envelope(fixture_case="prep_resolver_row_no_prior_pr")["interrupted"]
        self.assertEqual(interrupted, prep_resolver._absent_interrupted())

    def test_security_dismissals_are_trusted_only_on_this_accounts_pr(self):
        # The resolver reads no association: its own PR's record is the operator's, a teammate's is not.
        own = self._envelope(fixture_case="prep_resolver_tracker_clean")
        self.assertEqual(own["prior_pr"]["author"], "reviewer-bot")
        self.assertTrue(own["security"]["dismissals_trusted"])
        self.assertFalse(self._envelope(fixture_case="prep_resolver_row_no_prior_pr")["security"]["dismissals_trusted"])

    def test_polish_dispositions_are_trusted_only_on_this_accounts_pr(self):
        # #144: the same own-account test as the dismissals; fresh mode is the absent factory.
        own = self._envelope(fixture_case="prep_resolver_tracker_clean")
        self.assertTrue(own["polish"]["dispositions_trusted"])
        revision = self._envelope(fixture_case="prep_resolver_revision_ready")
        self.assertTrue(revision["polish"]["dispositions_trusted"])
        fresh = self._envelope(fixture_case="prep_resolver_row_no_prior_pr")
        self.assertEqual(fresh["polish"], parse.absent_polish())

    def test_a_commented_review_on_a_ready_pr_is_not_a_soft_reject(self):
        # The evaluator's self-approval downgrade posts an APPROVE as a COMMENTED review at head and
        # leaves the PR ready; only a real soft-reject flips it to draft.
        facts = self._envelope(fixture_case="prep_resolver_revision_ready")
        self.assertEqual(facts["revision"]["reasons"], ["polish_apply"])

    def test_dod_vetoes_is_published(self):
        self.assertEqual(self._envelope(fixture_case="prep_resolver_revision")["dod_vetoes"], [])

    def test_an_unshipped_phase_is_not_a_revision(self):
        facts = self._envelope(fixture_case="prep_resolver_tracker_clean")
        self.assertEqual(facts["revision"], {"active": False, "reasons": []})
        self.assertFalse(facts["polish"]["present"])
        self.assertFalse(facts["security"]["present"])

    def test_fresh_mode_has_no_security_record(self):
        facts = self._envelope(fixture_case="prep_resolver_row_no_prior_pr")
        self.assertEqual(facts["vector"]["mode"], "fresh")
        self.assertEqual(facts["security"], parse.absent_security())


class RevisionUnitTests(unittest.TestCase):
    """The pure cores behind `facts.revision`."""

    def _phase(self, number, kind="code-shipping"):
        return {"number": number, "title": "p%d" % number, "kind": kind}

    def _row(self, phase, checked=True):
        return {"checked": checked, "phase": phase, "title": "p", "sub_label": None,
                "commit_sha": "abc1234" if checked else None, "annotation": None}

    def _revision(self, phases, rows, dod=(), polish=None, at_head=False, mode=None, threads=None):
        return prep_resolver.build_revision(
            mode or prep_resolver.MODE_CONTINUE, phases, {"rows": rows}, {"number": 5},
            prep_resolver.dod_vetoes(list(dod)), polish or parse.absent_polish(), at_head, threads,
        )

    @staticmethod
    def _threads(*entries, available=True):
        return {"present": True, "available": available, "threads": [
            {"id": "T%d" % i, "state": state, "pending_reply": pending}
            for i, (state, pending) in enumerate(entries)
        ]}

    def test_review_threads_is_a_reason_only_for_a_thread_a_human_waits_on(self):
        # #145, skills/_shared/review-threads.md §4: `changes-requested`, or an `operator-thread` the
        # resolver has not answered yet (`pending_reply` false).
        one = [self._phase(1)]
        for entries, expected in (
            ((("changes-requested", False),), ["review_threads"]),
            ((("operator-thread", False),), ["review_threads"]),
            ((("operator-thread", True),), []),
            ((("unreviewed", False), ("accepted", False), ("superseded", False)), []),
        ):
            self.assertEqual(
                self._revision(one, [], threads=self._threads(*entries))["reasons"], expected, entries
            )

    def test_review_threads_fails_closed_on_an_absent_or_failed_read(self):
        one = [self._phase(1)]
        waiting = ("changes-requested", False)
        self.assertEqual(self._revision(one, [], threads=self._threads(waiting, available=False))["reasons"], [])
        self.assertEqual(self._revision(one, [], threads={"present": False})["reasons"], [])
        self.assertEqual(self._revision(one, [])["reasons"], [])

    def test_the_reasons_keep_their_closed_order(self):
        entry = {"id": "P1.1", "disposition": "apply"}
        polish = {"present": True, "entries": [entry], "unparsed": [], "dispositions_trusted": True}
        dod = [{"annotation": {"form": "resolver-claimed-evaluator-rejected", "reason": "no export"}}]
        reasons = self._revision(
            [self._phase(1)], [], dod=dod, polish=polish, at_head=True,
            threads=self._threads(("operator-thread", False)),
        )["reasons"]
        self.assertEqual(reasons, ["dod_rejected", "polish_apply", "review_threads", "review_at_head"])

    def test_a_pending_trailing_operator_phase_blocks_the_revision(self):
        # The #957 shape: the last code phase shipped, the operator phase has not. That is S4's
        # operator-phase handoff; a revision run would flip the PR ready before the operator's work.
        phases = [self._phase(1), self._phase(2, kind="operator")]
        self.assertFalse(self._revision(phases, [self._row(1), self._row(2, checked=False)])["active"])

    def test_every_phase_ticked_including_the_operator_one_is_a_revision(self):
        phases = [self._phase(1), self._phase(2, kind="operator")]
        self.assertTrue(self._revision(phases, [self._row(1), self._row(2)])["active"])

    def test_an_unticked_code_phase_is_not_a_revision(self):
        phases = [self._phase(1), self._phase(2)]
        self.assertFalse(self._revision(phases, [self._row(1), self._row(2, checked=False)])["active"])

    def test_single_phase_with_a_pr_is_a_revision(self):
        self.assertTrue(self._revision([self._phase(1)], [])["active"])

    def test_fresh_mode_is_never_a_revision(self):
        self.assertFalse(self._revision([self._phase(1)], [], mode="fresh")["active"])

    def test_a_vetoed_dod_bullet_is_reason_dod_rejected(self):
        dod = [{"annotation": {"form": "resolver-claimed-evaluator-rejected", "reason": "no export"}}]
        self.assertEqual(self._revision([self._phase(1)], [], dod=dod)["reasons"], ["dod_rejected"])

    def test_an_apply_counts_as_a_reason_only_when_the_ledger_is_trusted(self):
        # #144: an `apply` someone else could have typed into the body is not the operator's answer.
        entry = {"id": "P1.1", "disposition": "apply"}
        untrusted = {"present": True, "entries": [entry], "unparsed": [], "dispositions_trusted": False}
        trusted = dict(untrusted, dispositions_trusted=True)
        self.assertEqual(self._revision([self._phase(1)], [], polish=untrusted)["reasons"], [])
        self.assertEqual(self._revision([self._phase(1)], [], polish=trusted)["reasons"], ["polish_apply"])
        # A scan with no trust key at all fails closed.
        bare = {"present": True, "entries": [entry], "unparsed": []}
        self.assertEqual(self._revision([self._phase(1)], [], polish=bare)["reasons"], [])

    def test_a_veto_a_re_plan_reassigned_is_not_a_reason(self):
        # The single-phase loop: without the re-plan's mark, every continue run re-routed to the
        # planner, and the planner preserves the veto verbatim — planner -> resolver -> planner.
        dod = [{"annotation": {
            "form": "resolver-claimed-evaluator-rejected",
            "reason": "no export; re-plan reassigned to phase 1, awaiting its ship",
        }}]
        self.assertEqual(self._revision([self._phase(1)], [], dod=dod)["reasons"], [])

    def test_dod_vetoes_reads_the_re_plan_mark_and_the_annotation_still_parses(self):
        body = (
            "## Definition of done\n"
            "- [ ] Export works (resolver claimed phase 1, commit abc1234; evaluator rejected: no export;"
            " re-plan reassigned to phase 1, awaiting its ship)\n"
            "- [ ] Import works (resolver claimed phase 1, commit abc1234; evaluator rejected: no import)\n"
            "- [x] Docs (closed by commit abc1234)\n"
        )
        vetoes = prep_resolver.dod_vetoes(parse.parse_dod_bullets(body))
        self.assertEqual(
            vetoes,
            [
                {"index": 1, "commit_sha": "abc1234", "reassigned_to": 1},
                {"index": 2, "commit_sha": "abc1234", "reassigned_to": None},
            ],
        )

    def test_review_at_head_reads_reviews_not_latest_reviews(self):
        head = "f" * 40
        reviews = [{"state": "COMMENTED", "commit": {"oid": head}}]
        self.assertTrue(prep_resolver.review_at_head(reviews, head))
        self.assertFalse(prep_resolver.review_at_head(reviews, "e" * 40))
        self.assertFalse(prep_resolver.review_at_head([{"state": "APPROVED", "commit": {"oid": head}}], head))
        self.assertFalse(prep_resolver.review_at_head(reviews, None))


class TrackerDiffUnitTests(unittest.TestCase):
    """Direct tests of `build_tracker_diff`, the pure classifier — no subprocess, no shim."""

    @staticmethod
    def _row(phase, title, checked=True, sha="abc1234", annotation=None, sub_label=None):
        return {
            "phase": phase,
            "title": title,
            "checked": checked,
            "commit_sha": sha if checked else None,
            "annotation": annotation,
            "sub_label": sub_label,
        }

    @staticmethod
    def _phase(number, title):
        return {"number": number, "title": title}

    def test_a_ticked_operator_row_is_not_reported_as_drift(self):
        # #48 review, finding 1: the row's `(operator action <ISO-date>)` used to land inside `title`,
        # so EVERY operator phase read as `retitled` with no drift present — and the rebuild's "adopt
        # the plan's title" would then erase the date, the only record that the phase landed.
        rows = parse.scan_phase_tracker(
            "## Phase tracker\n- [x] Phase 3 — the measurement (operator action 2026-06-04)\n"
        )
        diff = prep_resolver.build_tracker_diff(
            rows["rows"], [self._phase(3, "the measurement")], rows["unparsed"]
        )
        self.assertEqual(diff["retitled"], [])
        self.assertFalse(diff["conflict"])
        self.assertEqual(rows["rows"][0]["annotation"], "operator action 2026-06-04")

    def test_unreadable_rows_gate_rather_than_rebuild(self):
        # #48 review, finding 2: a section whose rows do not parse has an UNKNOWN tick state. It must
        # not rebuild silently — that would guess what shipped, which is what this fact prevents.
        rows = parse.scan_phase_tracker("## Phase tracker\n- [x] Phase 5c — nope (commit abc1234)\n")
        diff = prep_resolver.build_tracker_diff(
            rows["rows"], [self._phase(1, "a")], rows["unparsed"]
        )
        self.assertEqual(diff["unparsed"], ["- [x] Phase 5c — nope (commit abc1234)"])
        self.assertTrue(diff["conflict"])

    def test_two_MAIN_rows_for_one_phase_number_gate(self):
        # The case the gate is actually for: two main rows claiming one phase cannot say what shipped.
        rows = parse.scan_phase_tracker(
            "## Phase tracker\n- [x] Phase 2 — harness (commit a1b2c3d)\n"
            "- [x] Phase 2 — something else (commit d4e5f6a)\n"
        )
        diff = prep_resolver.build_tracker_diff(rows["rows"], [self._phase(2, "harness")])
        self.assertEqual(diff["duplicated"], [2])
        self.assertTrue(diff["conflict"])

    def test_the_documented_operator_sub_row_does_not_gate(self):
        # #51, the regression #48 shipped: `dod-projection-rule.md` Example C's own tracker raised the
        # drift gate on EVERY continue-mode entry, because `Phase 2-measurement` folded into phase 2 as
        # a second row of it. A sub-row is bound to its phase and carried through instead.
        rows = parse.scan_phase_tracker(
            "## Phase tracker\n"
            "- [x] Phase 1 — substrate (commit abc1234)\n"
            "- [x] Phase 2 — harness (commit def5678)\n"
            "- [x] Phase 2-measurement (operator phase 2, applied 2026-06-04)\n"
            "- [ ] Phase 3 — decision write-up\n"
        )
        plan = [self._phase(1, "substrate"), self._phase(2, "harness"), self._phase(3, "decision write-up")]
        diff = prep_resolver.build_tracker_diff(rows["rows"], plan, rows["unparsed"])
        self.assertEqual(diff["duplicated"], [])
        self.assertFalse(diff["conflict"])
        # and it is reported, so a rebuild knows to carry it (with its annotation) rather than drop it
        self.assertEqual([row["sub_label"] for row in diff["sub_rows"]], ["measurement"])
        self.assertEqual(diff["sub_rows"][0]["annotation"], "operator phase 2, applied 2026-06-04")

    def test_a_sub_row_does_not_satisfy_its_phase_main_row(self):
        # A phase whose ONLY row is a sub-row still needs a main row added.
        rows = parse.scan_phase_tracker(
            "## Phase tracker\n- [x] Phase 2-measurement (operator phase 2, applied 2026-06-04)\n"
        )
        diff = prep_resolver.build_tracker_diff(rows["rows"], [self._phase(2, "harness")])
        self.assertEqual(diff["missing"], [2])
        self.assertFalse(diff["conflict"])

    def test_an_ambiguous_title_names_no_destination(self):
        # #48 review, finding 4: `setdefault` made this first-wins, so the gate could quote a
        # destination picked on nothing better than list order. Two candidates now yield none.
        row = [self._row(7, "integration tests")]
        diff = prep_resolver.build_tracker_diff(
            row, [self._phase(3, "integration tests"), self._phase(5, "Integration  Tests")]
        )
        self.assertEqual(diff["shifted"], [])
        self.assertEqual(diff["removed_shipped"], [{"phase": 7, "title": "integration tests", "commit_sha": "abc1234"}])
        self.assertTrue(diff["conflict"])

    def test_a_sole_matching_title_still_names_its_destination(self):
        # The uniqueness rule must not disable the useful case.
        row = [self._row(7, "the proof")]
        diff = prep_resolver.build_tracker_diff(row, [self._phase(1, "substrate"), self._phase(2, "the proof")])
        self.assertEqual(diff["shifted"][0]["plan_phase"], 2)

    def test_the_absent_tracker_is_a_factory_not_a_shared_constant(self):
        # #48 review, finding 5: `dict(CONSTANT)` is shallow, so every "absent" fact shared one
        # nested `diff` and `rows` with the constant. Preps compose in-process by design, so the
        # first such caller would see another call's rows on a fact reporting `present: False`.
        first, second = prep_resolver._absent_tracker(), prep_resolver._absent_tracker()
        first["diff"]["missing"].append(99)
        first["rows"].append({"phase": 1})
        self.assertEqual(second["diff"]["missing"], [])
        self.assertEqual(second["rows"], [])

    def test_shifted_wins_over_removed_shipped(self):
        # A ticked row whose number is gone but whose title matches another phase is work that MOVED.
        # Classifying it as "removed" would lose the destination number the operator gate must quote.
        diff = prep_resolver.build_tracker_diff(
            [self._row(8, "the proof")], [self._phase(1, "substrate"), self._phase(2, "the proof")]
        )
        self.assertEqual(diff["shifted"], [{"row_phase": 8, "plan_phase": 2, "title": "the proof", "commit_sha": "abc1234"}])
        self.assertEqual(diff["removed_shipped"], [])

    def test_title_compare_is_whitespace_and_case_insensitive(self):
        diff = prep_resolver.build_tracker_diff(
            [self._row(1, "End-to-end   Proof")], [self._phase(1, "end-to-end proof")]
        )
        self.assertEqual(diff["retitled"], [])
        self.assertFalse(diff["conflict"])

    def test_empty_rows_against_a_plan_reports_every_phase_missing(self):
        diff = prep_resolver.build_tracker_diff([], [self._phase(1, "a"), self._phase(2, "b")])
        self.assertEqual(diff["missing"], [1, 2])
        self.assertFalse(diff["conflict"])

    def test_no_plan_phases_leaves_ticked_rows_as_removed_shipped(self):
        diff = prep_resolver.build_tracker_diff([self._row(1, "a")], [])
        self.assertEqual(diff["removed_shipped"], [{"phase": 1, "title": "a", "commit_sha": "abc1234"}])
        self.assertTrue(diff["conflict"])

    def test_conflict_is_the_disjunction_of_the_two_shipped_classes(self):
        # The router must read one boolean, never re-derive the gate from a disjunction of lists.
        clean = prep_resolver.build_tracker_diff(
            [self._row(1, "a", checked=False)], [self._phase(1, "a")]
        )
        self.assertFalse(clean["conflict"])


class PureHelperUnitTests(unittest.TestCase):
    """Direct, in-process tests of prep_resolver's pure classification/derivation helpers — no
    subprocess, no shim, no git sandbox needed (mirrors test_prep_evaluator.py's
    PureHelperUnitTests style)."""

    def test_detect_type_epic_by_label(self):
        self.assertEqual(prep_resolver._detect_type(["epic"], "Sandbox fixture"), "epic")

    def test_detect_type_epic_by_title_prefix_case_insensitive(self):
        self.assertEqual(prep_resolver._detect_type([], "EPIC: sandbox fixture"), "epic")

    def test_detect_type_story_by_label(self):
        self.assertEqual(prep_resolver._detect_type(["story"], "Story A"), "story")

    def test_detect_type_standard_default(self):
        self.assertEqual(prep_resolver._detect_type(["bug"], "Fix the thing"), "standard")

    def test_compute_fresh_slug_worked_examples_from_the_spec(self):
        self.assertEqual(
            prep_resolver.compute_fresh_slug("Daily Journal Visual Redesign"),
            "daily-journal-visual-redesign",
        )
        self.assertEqual(
            prep_resolver.compute_fresh_slug("Epic: Macro Radials Data"), "macro-radials-data"
        )
        self.assertEqual(
            prep_resolver.compute_fresh_slug("Auth/Token Refresh (Phase 2)"),
            "auth-token-refresh-phase-2",
        )

    def test_compute_fresh_slug_truncates_on_a_dash_boundary(self):
        title = "A " * 40  # produces a long run of "a-a-a-..." past the 50-char cap
        slug = prep_resolver.compute_fresh_slug(title)
        self.assertLessEqual(len(slug), 50)
        self.assertFalse(slug.endswith("-"))

    def test_audit_ref_no_epic_context_is_the_default_branch(self):
        self.assertEqual(prep_resolver._audit_ref(None, "main"), "main")

    def test_audit_ref_uses_the_epic_branch_whenever_one_is_in_play(self):
        # #31: the branch fact alone decides. The retired `issue_type` parameter discarded this
        # branch whenever the target lacked a `story` label, even though the caller had just
        # discovered it from the target's own native parent.
        self.assertEqual(prep_resolver._audit_ref("epic/1-x", "main"), "epic/1-x")

    def test_suggested_playbook_comment_only_wins_over_type(self):
        self.assertEqual(prep_resolver._suggested_playbook("epic", True), "comment-only.md")

    def test_suggested_playbook_untyped_under_an_open_epic_routes_to_story(self):
        # #31: `standard.md` states the base is the default branch and omits the
        # integration-branch caveat, so it is the wrong instruction set once the PR bases on
        # `epic/<N>-<slug>`.
        self.assertEqual(
            prep_resolver._suggested_playbook("standard", False, "epic/181-x"), "story.md"
        )

    def test_suggested_playbook_untyped_with_no_epic_branch_stays_standard(self):
        self.assertEqual(prep_resolver._suggested_playbook("standard", False, None), "standard.md")

    def test_suggested_playbook_by_type(self):
        self.assertEqual(prep_resolver._suggested_playbook("epic", False), "epic.md")
        self.assertEqual(prep_resolver._suggested_playbook("story", False), "story.md")
        self.assertEqual(prep_resolver._suggested_playbook("standard", False), "standard.md")

    def test_extract_plan_sha_from_header_line(self):
        body = "**Implementation plan** — #1 x — planned 2026-01-01 at `main@abc1234`\n"
        self.assertEqual(prep_resolver._extract_plan_sha(body), "abc1234")

    def test_extract_plan_sha_missing_is_none(self):
        self.assertIsNone(prep_resolver._extract_plan_sha("no sha header here"))

    def test_stale_cutoff_boundary_just_under_is_active(self):
        # Computed relative to `datetime.now()` AT TEST-RUN TIME (never a fixture file with a
        # baked-in calendar date, which would silently drift wrong as real time passes) — the S9
        # review's "current fixture only tests 18 months" finding, closed with an exact boundary
        # pair rather than a value deep in one side's territory.
        import datetime

        just_under = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=prep_resolver._STALE_ACTIVITY_DAYS - 1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        pr = {"updatedAt": just_under}
        self.assertEqual(
            prep_resolver._classify_open_other_activity(pr),
            prep_resolver.PRIOR_PR_ROW_OPEN_OTHER_ACTIVE,
        )

    def test_stale_cutoff_boundary_just_over_is_stale(self):
        import datetime

        just_over = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=prep_resolver._STALE_ACTIVITY_DAYS + 1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        pr = {"updatedAt": just_over}
        self.assertEqual(
            prep_resolver._classify_open_other_activity(pr),
            prep_resolver.PRIOR_PR_ROW_OPEN_OTHER_STALE,
        )

    def test_stale_cutoff_days_fact_is_surfaced_on_the_prior_pr_fact(self):
        # docs/specs/resolver.md advisory: the operator/router must be able to see the exact
        # driver, not trust an opaque classification.
        # `closes_issue` (not the head name) is what makes this hand-named branch the issue's own
        # PR — the OR arm of `branching.pr_belongs_to_issue`.
        open_prs = [{"number": 1, "author": {"login": "carol"}, "isDraft": False,
                     "headRefName": "carol-x", "url": "u", "updatedAt": "2026-01-01T00:00:00Z",
                     "closes_issue": True}]
        _row, fact = prep_resolver._classify_prior_pr_row(open_prs, "reviewer-bot", None, "OPEN", 100)
        self.assertEqual(fact["stale_cutoff_days"], prep_resolver._STALE_ACTIVITY_DAYS)

    def test_draft_authorship_ordering_foreign_draft_is_not_the_draft_row(self):
        # The exact ordering-bug regression, isolated to the pure classifier: a draft PR by
        # someone else must classify via _classify_open_other_activity (gated), never
        # PRIOR_PR_ROW_DRAFT (continue) — authorship is checked BEFORE draft state.
        foreign_draft = [{"number": 58, "author": {"login": "carol"}, "isDraft": True,
                           "headRefName": "carol-wip", "url": "u", "updatedAt": "2026-07-08T00:00:00Z",
                           "closes_issue": True}]
        row, _fact = prep_resolver._classify_prior_pr_row(foreign_draft, "reviewer-bot", None, "OPEN", 100)
        self.assertNotEqual(row, prep_resolver.PRIOR_PR_ROW_DRAFT)
        self.assertIn(row, prep_resolver._GATED_ROWS)

    def test_own_draft_is_still_the_draft_row(self):
        own_draft = [{"number": 58, "author": {"login": "reviewer-bot"}, "isDraft": True,
                      "headRefName": "100-x", "url": "u", "updatedAt": "2026-07-08T00:00:00Z"}]
        row, _fact = prep_resolver._classify_prior_pr_row(own_draft, "reviewer-bot", None, "OPEN", 100)
        self.assertEqual(row, prep_resolver.PRIOR_PR_ROW_DRAFT)
        self.assertIn(row, prep_resolver._CONTINUE_ROWS)


class ResumeSeedUnitTests(unittest.TestCase):
    """#139 SEC-2's script half, below the fixture tests: the thread is read whether the gather kept it
    inline or spilled it to a path, and anything unreadable stages an empty seed — never an unfiltered one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _stage(self, pr_facts):
        out = self.dir / "resume.json"
        fact = prep_resolver.stage_resume_comments(prep_resolver._load_json_list(pr_facts, "thread"), out)
        return fact, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)

    def test_a_spilled_thread_is_read_from_its_path_and_filtered(self):
        thread = [
            {"authorAssociation": "MEMBER", "body": "trusted", "createdAt": "t1", "url": "u1"},
            {"authorAssociation": "CONTRIBUTOR", "body": "forged", "createdAt": "t2", "url": "u2"},
            "not-a-comment",
        ]
        spilled = self.dir / "thread.json"
        spilled.write_text(json.dumps(thread), encoding="utf-8")
        fact, staged = self._stage({"thread_mode": "path", "thread_path": str(spilled)})
        self.assertEqual((fact["present"], fact["trusted"], fact["skipped"]), (True, 1, 2))
        self.assertEqual(staged, [{"body": "trusted", "createdAt": "t1", "url": "u1"}])

    def test_an_unreadable_thread_is_an_absent_seed(self):
        # Unreadable is not empty: it reports `present: false` and writes nothing, so a session never
        # mistakes a failed read for a thread with no settled items.
        for pr_facts in ({"thread": "{not json"}, {"thread_mode": "path", "thread_path": str(self.dir / "gone")},
                         {}, {"thread": '{"a": 1}'}):
            with self.subTest(pr_facts=pr_facts):
                fact, staged = self._stage(pr_facts)
                self.assertEqual(fact, {"present": False, "comments_path": None, "trusted": 0, "skipped": 0})
                self.assertIsNone(staged)

    def test_a_readable_empty_thread_is_present(self):
        fact, staged = self._stage({"thread": "[]"})
        self.assertEqual((fact["present"], fact["trusted"], fact["skipped"], staged), (True, 0, 0, []))


class PriorPrOwnershipUnitTests(unittest.TestCase):
    """The issue-#29 guard, isolated to the pure classifier: the `#<N> in:body` searches behind
    both candidate lists are MENTION searches, so a row must only ever be driven by the issue's
    OWN PRs (`branching.pr_belongs_to_issue`)."""

    EPIC_PR = {"number": 245, "author": {"login": "reviewer-bot"}, "isDraft": True,
               "headRefName": "epic/100-the-container", "url": "u",
               "updatedAt": "2026-08-01T00:00:00Z", "closes_issue": False}

    def test_an_epic_pr_mentioning_a_story_is_not_that_storys_prior_pr(self):
        # An epic integration PR lists every story by number, so it surfaces once per story. For
        # the STORY it is a mention (no row); for the EPIC itself it is genuinely the prior PR.
        # Asserting the pair proves the guard is issue-scoped, not a blanket epic exclusion.
        story_row, story_fact = prep_resolver._classify_prior_pr_row(
            [dict(self.EPIC_PR)], "reviewer-bot", None, "OPEN", 101
        )
        self.assertEqual(story_row, prep_resolver.PRIOR_PR_ROW_NONE)
        self.assertIsNone(story_fact)

        epic_pr = dict(self.EPIC_PR, closes_issue=True)
        epic_row, _fact = prep_resolver._classify_prior_pr_row(
            [epic_pr], "reviewer-bot", None, "OPEN", 100
        )
        self.assertEqual(epic_row, prep_resolver.PRIOR_PR_ROW_DRAFT)

    def test_a_hand_named_branch_that_closes_the_issue_is_still_the_prior_pr(self):
        # Guards the OR arm against a future head-name-only simplification: dropping this PR would
        # mint a duplicate branch and a duplicate PR over live work.
        hand_named = [{"number": 70, "author": {"login": "reviewer-bot"}, "isDraft": False,
                       "headRefName": "reviewer-bot/quick-fix", "url": "u",
                       "updatedAt": "2026-08-01T00:00:00Z", "closes_issue": True}]
        row, _fact = prep_resolver._classify_prior_pr_row(hand_named, "reviewer-bot", None, "OPEN", 101)
        self.assertEqual(row, prep_resolver.PRIOR_PR_ROW_OPEN_YOURS)

    def test_a_vn_suffixed_head_still_belongs_without_a_close_link(self):
        collided = [{"number": 71, "author": {"login": "reviewer-bot"}, "isDraft": False,
                     "headRefName": "101-story-a-first-slice-v2", "url": "u",
                     "updatedAt": "2026-08-01T00:00:00Z", "closes_issue": False}]
        row, _fact = prep_resolver._classify_prior_pr_row(collided, "reviewer-bot", None, "OPEN", 101)
        self.assertEqual(row, prep_resolver.PRIOR_PR_ROW_OPEN_YOURS)

    def test_a_mention_only_open_pr_does_not_mask_a_real_closed_pr(self):
        # Why callers must narrow BEFORE their `if not open_prs` gate: that gate decides whether
        # the closed search runs at all, so a mention would otherwise hide a real closed PR.
        closed = [{"number": 60, "author": {"login": "alice"}, "state": "MERGED",
                   "mergedAt": "2026-05-01T00:00:00Z", "headRefName": "101-story-a", "url": "u",
                   "closes_issue": True}]
        row, fact = prep_resolver._classify_prior_pr_row(
            [dict(self.EPIC_PR)], "reviewer-bot", closed, "OPEN", 101
        )
        self.assertEqual(row, prep_resolver.PRIOR_PR_ROW_CLOSED_NOT_RESOLVED)
        self.assertEqual(fact["number"], 60)

    def test_a_merged_epic_pr_is_not_this_storys_closed_prior_pr(self):
        # The same defect one merge later — it would otherwise raise a spurious
        # `closed-not-resolved` row (and its "did not resolve it" attention line) on every story.
        merged_epic = [{"number": 245, "author": {"login": "reviewer-bot"}, "state": "MERGED",
                        "mergedAt": "2026-08-10T00:00:00Z",
                        "headRefName": "epic/100-the-container", "url": "u",
                        "closes_issue": False}]
        row, fact = prep_resolver._classify_prior_pr_row([], "reviewer-bot", merged_epic, "OPEN", 101)
        self.assertEqual(row, prep_resolver.PRIOR_PR_ROW_NONE)
        self.assertIsNone(fact)

    def test_prior_prs_for_issue_is_idempotent(self):
        # The property the caller-then-classifier double application rests on.
        prs = [dict(self.EPIC_PR),
               {"number": 71, "headRefName": "101-story-a", "closes_issue": False}]
        once = prep_resolver._prior_prs_for_issue(prs, 101)
        self.assertEqual(prep_resolver._prior_prs_for_issue(once, 101), once)
        self.assertEqual([pr["number"] for pr in once], [71])

    def test_partition_reports_the_dropped_prs_for_the_diagnostic_fact(self):
        own, mentions_only = prep_resolver._partition_prs_for_issue(
            [dict(self.EPIC_PR), {"number": 71, "headRefName": "101-story-a"}], 101
        )
        self.assertEqual([pr["number"] for pr in own], [71])
        self.assertEqual([pr["number"] for pr in mentions_only], [245])

    def test_none_partitions_to_two_empty_halves(self):
        self.assertEqual(prep_resolver._partition_prs_for_issue(None, 101), ([], []))


class UsageErrorTests(unittest.TestCase):
    def test_missing_required_positional_args_is_usage_error(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_missing_repo_arg_is_usage_error(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "100"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


class DodDriftAttentionTests(unittest.TestCase):
    """`facts.dod_coverage.drift` is the projection rule's bullet-count drift as a fact. Before it,
    `dod-projection-rule.md` assumed a multi-phase plan against a DoD-less issue could not get past
    the plan reviewer; a session that met one projected the plan's indexes onto `## Acceptance
    criteria`, where neither the evaluator nor the next resolver session could see them."""

    @staticmethod
    def _coverage(body, phases):
        return parse.dod_coverage(
            [{"number": n, "closes_dod": c} for n, c in phases], parse.dod_summary(body)
        )

    def test_claims_against_acceptance_criteria_name_the_heading_and_block(self):
        cov = self._coverage(
            "## Acceptance criteria\n- [ ] a\n- [ ] b\n", [(1, [1]), (2, [2])]
        )
        (line,) = prep_resolver._dod_drift_attention(cov)
        self.assertIn("`## Acceptance criteria` (2 bullets)", line)
        self.assertIn("re-route to the planner", line)
        self.assertIn("never project onto another heading", line)

    def test_a_dod_grown_past_the_plan_names_the_count(self):
        cov = self._coverage(
            "## Definition of done\n- [ ] a\n- [ ] b\n- [ ] c\n", [(1, [1]), (2, [2])]
        )
        (line,) = prep_resolver._dod_drift_attention(cov)
        self.assertIn("has 3 bullet(s)", line)

    def test_claims_against_a_body_with_no_checklist_at_all(self):
        cov = self._coverage("## Description\nprose only\n", [(1, [1]), (2, [2])])
        (line,) = prep_resolver._dod_drift_attention(cov)
        self.assertIn("the issue body has no `## Definition of done`", line)

    def test_a_plan_claiming_nothing_says_so(self):
        cov = self._coverage(
            "## Definition of done\n- [ ] a\n", [(1, "(none)"), (2, "(none)")]
        )
        (line,) = prep_resolver._dod_drift_attention(cov)
        self.assertIn("the plan claims no `closes-dod` index", line)

    def test_exact_coverage_and_the_single_phase_fallback_say_nothing(self):
        dod = "## Definition of done\n- [ ] a\n- [ ] b\n"
        self.assertEqual(
            prep_resolver._dod_drift_attention(self._coverage(dod, [(1, [1]), (2, [2])])), []
        )
        self.assertEqual(prep_resolver._dod_drift_attention(self._coverage(dod, [])), [])


class InterruptedFactTests(unittest.TestCase):
    """#155 `facts.interrupted`: the latest interrupted pass a trusted loop comment records, from the PR's
    staged (already trusted) comments and the issue thread (filtered here), with `head_contains` probed in
    a real git repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        _git(["init", "-q"], self.repo)
        for n in (1, 2):
            _git(["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", str(n)], self.repo)
        self.base = _git(["rev-parse", "HEAD~1"], self.repo)
        self.head = _git(["rev-parse", "HEAD"], self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def loop(phase, *lines, runs=None):
        body = [parse.REVIEW_LOOP_MARKER, "## Review loop — phase %d" % phase] + list(lines)
        if runs:
            body.append("Review runs: phase %d · %d · %s" % (phase, runs[0], runs[1]))
        return "\n".join(body)

    def build_line(self, phase, sha):
        return "Interrupted: phase %d @ %s · build — mid S5" % (phase, sha)

    @staticmethod
    def comment(body, at, url="u", association=None):
        comment = {"body": body, "createdAt": at, "url": url}
        if association is not None:
            comment["authorAssociation"] = association
        return comment

    def test_from_a_trusted_pr_loop_comment(self):
        pr = [self.comment(self.loop(1, self.build_line(1, self.head), runs=(2, "interrupted")), "t1", "pr-url")]
        fact = prep_resolver.build_interrupted(pr, [], str(self.repo))
        self.assertEqual(
            fact,
            {"present": True, "phase": 1, "sha": self.head, "kind": "build", "entry": None, "note": "mid S5",
             "url": "pr-url", "active": True, "head_contains": True},
        )

    def test_from_a_trusted_issue_comment_with_no_pr(self):
        issue = [self.comment(self.loop(1, self.build_line(1, self.head), runs=(0, "interrupted")), "t1",
                              "issue-url", "OWNER")]
        fact = prep_resolver.build_interrupted([], issue, str(self.repo))
        self.assertEqual((fact["present"], fact["url"], fact["active"]), (True, "issue-url", True))

    def test_an_untrusted_issue_comment_is_ignored(self):
        for association in ("NONE", "CONTRIBUTOR", None):
            issue = [self.comment(self.loop(1, self.build_line(1, self.head)), "t1", "u", association)]
            self.assertEqual(
                prep_resolver.build_interrupted([], issue, str(self.repo)), prep_resolver._absent_interrupted()
            )

    def test_a_later_settled_pass_of_the_phase_deactivates_it(self):
        pr = [
            self.comment(self.loop(1, self.build_line(1, self.head), runs=(2, "interrupted")), "t1"),
            self.comment(self.loop(1, runs=(1, "settled")), "t2"),
        ]
        self.assertFalse(prep_resolver.build_interrupted(pr, [], str(self.repo))["active"])
        # A later pass of ANOTHER phase does not.
        pr[1] = self.comment(self.loop(2, runs=(1, "settled")), "t2")
        self.assertTrue(prep_resolver.build_interrupted(pr, [], str(self.repo))["active"])

    def test_a_changes_record_stays_active_and_exposes_its_entry(self):
        # A pending round posted at the wrap-up threshold, on a phase whose own pass settled earlier
        # (its tracker row is ticked): only a LATER pass deactivates the record.
        line = "Interrupted: phase 1 @ %s · changes from %s — https://x/issues/9#issuecomment-1" % (self.head, self.base)
        pr = [
            self.comment(self.loop(1, runs=(3, "settled")), "t1"),
            self.comment(self.loop(1, line, runs=(0, "interrupted")), "t2", "rec"),
        ]
        fact = prep_resolver.build_interrupted(pr, [], str(self.repo))
        self.assertEqual((fact["kind"], fact["entry"], fact["active"], fact["url"]), ("changes", self.base, True, "rec"))

    def test_the_latest_record_wins_across_pr_and_issue(self):
        # A wrap-up during a changes-required round, after an earlier build interruption on the issue.
        line = "Interrupted: phase 1 @ %s · changes from %s — threads only" % (self.head, self.base)
        issue = [self.comment(self.loop(1, self.build_line(1, self.base), runs=(1, "interrupted")), "t1", "i", "MEMBER")]
        pr = [self.comment(self.loop(1, line, runs=(2, "interrupted")), "t3", "p")]
        fact = prep_resolver.build_interrupted(pr, issue, str(self.repo))
        self.assertEqual((fact["kind"], fact["url"]), ("changes", "p"))

    def test_a_build_record_already_pushed_is_inactive(self):
        # A closed, unmerged PR's branch reused on a fresh restart: fresh mode reads no PR comments, so the
        # pushed wip SHA (in `upstream_sha`) is what retires the issue-posted record.
        issue = [self.comment(self.loop(1, self.build_line(1, self.base), runs=(1, "interrupted")), "t1", "i", "OWNER")]
        fact = prep_resolver.build_interrupted([], issue, str(self.repo), upstream_sha=self.head)
        self.assertEqual((fact["present"], fact["active"], fact["head_contains"]), (True, False, True))
        # Not yet pushed (the upstream is behind the wip commit): still active.
        self.assertTrue(prep_resolver.build_interrupted(
            [], [self.comment(self.loop(1, self.build_line(1, self.head)), "t1", "i", "OWNER")], str(self.repo),
            upstream_sha=self.base,
        )["active"])

    def test_a_changes_record_is_not_retired_by_the_upstream(self):
        # Its SHA is the shipped phase's pushed head by construction.
        line = "Interrupted: phase 1 @ %s · changes from %s — threads only" % (self.base, self.base)
        pr = [self.comment(self.loop(1, line, runs=(0, "interrupted")), "t1")]
        self.assertTrue(prep_resolver.build_interrupted(pr, [], str(self.repo), upstream_sha=self.head)["active"])

    def test_head_contains_is_false_when_the_sha_is_not_an_ancestor(self):
        _git(["checkout", "-q", "--detach", self.base], self.repo)
        pr = [self.comment(self.loop(1, self.build_line(1, self.head)), "t1")]
        self.assertIs(prep_resolver.build_interrupted(pr, [], str(self.repo))["head_contains"], False)
        unknown = [self.comment(self.loop(1, self.build_line(1, "c" * 40)), "t1")]
        self.assertIsNone(prep_resolver.build_interrupted(unknown, [], str(self.repo))["head_contains"])
        self.assertIsNone(prep_resolver.build_interrupted(pr, [], None)["head_contains"])


if __name__ == "__main__":
    unittest.main()
