"""Unit tests for scripts/prep_planner.py — the planner's complete facts block in one call
(architecture.md §4; docs/implementation.md S12; docs/specs/planner.md).

Test topology (mirrors tests/test_prep_resolver.py / tests/test_prep_evaluator.py — the S8-locked
composition pattern this step inherits): `gh` calls go through the offline shim
(`tests/support/shimenv`); `git` calls (root freshness, `ensure --read`, and the `git ls-remote`
epic-branch discovery this step's plan-ref table needs) go through a real temp origin+clone
(`tests/support/gitsandbox`) — no shim needed for those (architecture.md §10). `prep_planner.py`
is driven as a real subprocess so every test exercises the full argv-in / subprocess / envelope-out
path a real caller uses. Every fixture origin is a local git sandbox — hermetic per the S9 lesson
(no test resolves `.` against a real network-backed clone); `shimenv.intercepted_env`'s
`NETWORK_POISON_ENV` guard is never bypassed.

Coverage matrix (S12 DoD):
- Plan-ref fixtures: single issue no-PR, single issue open-PR (revise), story-under-epic, epic
  (branch found + bootstrap) — each asserts `plan_ref` + `read_workspaces.grounding.sha`.
- The seeded tracker-match fixture (Bug (a) regression) + a no-match fixture.
- Revise facts: prior plan body path (forced path-mode via an oversized plan body) + parsed
  `## Phase tracker`.
- Marker-detection variants: research only / plan only (= the open-PR-revise fixture) / both /
  neither (= the default row fixture).
- Conformance on every emitting path + the two-sided call-budget test (S6 style).
- Decision codes: MARKER_AMBIGUOUS (duplicate plan comments; duplicate research comments),
  AUTH_REQUIRED, WORKSPACE_MISMATCH, AMBIGUOUS (multiple epic-branch matches, multiple
  parent-epic matches, malformed `## Open questions`).
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "prep_planner.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import branching  # noqa: E402  (import after sys.path setup, by necessity)
import prep_planner  # noqa: E402
from pipelib import process  # noqa: E402
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


# The doc catalogue every sandbox starts configured with (skills/_shared/doc-catalogue.md) — one
# entry, naming a document the base setUp also creates, so a baseline run reports one present
# grounding doc, no notice, and no attention line.
SEEDED_CATALOGUE_INTERIOR = "- `docs/prd.md` — prd — binding — What the product is.\n"


def _catalogue_block(interior):
    return "<!-- doc-catalogue -->\n%s<!-- /doc-catalogue -->\n" % interior


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


class PrepPlannerSandboxTestCase(unittest.TestCase):
    """Shared setup: a real temp git origin+clone (the `--root`), pre-seeded with `.gitignore`
    (mirroring tests/test_prep_resolver.py's identical rationale: an unseeded first `ensure` would
    spuriously trip ROOT_DIRTY on the very first call)."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        _write(self.root / ".gitignore", ".worktrees/\n")
        # A configured consuming repo: a doc catalogue naming one document that exists. Seeded in the
        # BASE so the grounding dimension is neutral for every test not about grounding — without it
        # each such test's `attention` would carry the catalogue-absent line, and asserting that line
        # in tests about open questions would be noise that hides the real assertion.
        _write(self.root / "docs" / "README.md", _catalogue_block(SEEDED_CATALOGUE_INTERIOR))
        _write(self.root / "docs" / "prd.md", "# PRD\n")
        _git(["add", ".gitignore", "docs"], self.root)
        _git(["commit", "-m", "seed gitignore + doc catalogue"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    def _commit_push(self, message):
        _git(["add", "-A"], self.root)
        _git(["commit", "-m", message], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)

    def _replace_catalogue(self, interior):
        """Rewrite the seeded catalogue and land it on origin/main — the planner reads the catalogue
        at the ENSURED checkout, so an uncommitted edit here would not be seen."""
        _write(self.root / "docs" / "README.md", _catalogue_block(interior))
        self._commit_push("replace doc catalogue")

    def _remove_catalogue(self):
        (self.root / "docs" / "README.md").unlink()
        self._commit_push("remove doc catalogue")

    def _mk_ambient(self, branch):
        """Create (or reuse) `.worktrees/<branch>` in the sandbox clone — the worktree a
        non-main `plan_ref` grounding session would be started in. Attaches an existing local
        branch, else forks at origin/main. Returns its Path."""
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

    def _envelope(self, issue="200", repo="octo/widgets", fixture_case="prep_planner_row_default", extra_args=None, ambient=None):
        """v3: the prep asserts the AMBIENT checkout against the selected plan_ref. The default
        posture runs the subprocess from the sandbox root itself — a clean `main` checkout, which
        a `main` plan_ref accepts (`allow_main_root`, plan-before-open). A non-main plan_ref test
        passes `ambient=<branch>` to run from inside the matching worktree instead."""
        args = [issue, repo, "--root", str(self.root), "--scratch-dir", self.scratch]
        if extra_args:
            args += extra_args
        run_cwd = str(self._mk_ambient(ambient)) if ambient is not None else str(self.root)
        result = self._run(args, fixture_case=fixture_case, cwd=run_cwd)
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope

    def _push_branch(self, name, base="origin/main"):
        """`base` defaults to origin/main so every pre-existing call site is unchanged; a
        `story-own-branch` test passes the epic branch instead, so the story branch DESCENDS from
        it (the shape `workspace-open` actually produces) rather than forking at main."""
        _git(["fetch", "origin"], self.root)
        _git(["branch", name, base], self.root)
        _git(["push", "origin", name], self.root)

    def _commit_in(self, worktree, filename, message):
        """One commit inside a worktree, left UNPUSHED. Returns its sha."""
        _write(worktree / filename, "%s\n" % message)
        _git(["add", "-A"], worktree)
        _git(["commit", "-m", message], worktree)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(worktree),
            capture_output=True, encoding="utf-8", check=True,
        ).stdout.strip()


class ScriptExistsTests(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_script_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPT)], capture_output=True, encoding="utf-8"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class HappyPathFactsSchemaTests(PrepPlannerSandboxTestCase):
    """Facts schema matches architecture.md §4's common core + planner extensions."""

    def test_conformant_ok_envelope_with_planner_facts(self):
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        for key in (
            "repo", "scratch", "root", "target", "vector", "suggested_playbook", "plan_ref",
            "plan", "research", "grounding_docs", "open_questions", "open_question_candidates",
            "dod", "grounding", "sections", "attention", "notices",
        ):
            self.assertIn(key, envelope, "missing architecture.md §4 facts-block key %r" % key)

    def test_root_facts_shape(self):
        # v3.x: the retired origin/main pin took `sha`/`source` with it; `root` now carries the
        # main checkout's path and the DERIVED default branch.
        envelope = self._envelope()
        self.assertEqual(envelope["root"]["path"], str(self.root))
        self.assertEqual(envelope["root"]["default_branch"], "main")
        self.assertNotIn("sha", envelope["root"])
        self.assertNotIn("source", envelope["root"])
        self.assertTrue(envelope["root"]["fresh"])

    def test_target_facts_shape(self):
        envelope = self._envelope()
        self.assertEqual(envelope["target"]["kind"], "issue")
        self.assertEqual(envelope["target"]["number"], 200)
        self.assertEqual(envelope["target"]["state"], "OPEN")
        self.assertEqual(envelope["target"]["labels"], ["bug"])

    def test_vector_standard_fresh_default_row(self):
        envelope = self._envelope()
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(envelope["vector"]["mode"], "fresh")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_DEFAULT)
        self.assertEqual(envelope["suggested_playbook"], "single.md")

    def test_planner_never_gets_a_work_workspace(self):
        # architecture.md §6 / prd §8.4: grounding is read-only; the planner never has a `workspace`
        # key the way the resolver does — and under v3 no `read_workspaces` either: it grounds on
        # the AMBIENT checkout, reported as the top-level `grounding` fact.
        envelope = self._envelope()
        self.assertNotIn("workspace", envelope)
        self.assertNotIn("read_workspaces", envelope)
        self.assertIn("grounding", envelope)
        self.assertEqual(Path(envelope["grounding"]["path"]), Path(self.root).resolve())
        self.assertEqual(envelope["grounding"]["branch"], "main")
        self.assertFalse(envelope["grounding"]["dirty"])

    def test_no_open_questions_no_candidates(self):
        envelope = self._envelope()
        self.assertEqual(envelope["open_questions"], [])
        self.assertEqual(envelope["open_question_candidates"], [])
        self.assertEqual(envelope["attention"], [])

    def test_plan_absent_is_a_fact_not_a_decision(self):
        envelope = self._envelope()
        self.assertFalse(envelope["plan"]["present"])
        self.assertIsNone(envelope["plan"]["sha"])

    def test_research_absent(self):
        envelope = self._envelope()
        self.assertFalse(envelope["research"]["present"])

    def test_no_epic_or_story_key_for_standard_type(self):
        envelope = self._envelope()
        self.assertNotIn("epic", envelope)
        self.assertNotIn("story", envelope)
        self.assertNotIn("revise", envelope)

    def test_sections_carries_gh_gather_spill_fields_through(self):
        envelope = self._envelope()
        self.assertIn("issue_body", envelope["sections"])
        self.assertEqual(envelope["sections"]["issue_body_mode"], "inline")


class PlanRefRowTests(PrepPlannerSandboxTestCase):
    """Plan-ref fixtures: single issue no-PR, single issue open-PR, story-under-epic, epic — each
    asserting `plan_ref` + `read_workspaces.grounding.sha` (S12 DoD's first box)."""

    def test_row_default_single_issue_no_pr(self):
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_DEFAULT)
        self.assertEqual(envelope["plan_ref"], "main")
        self.assertEqual(envelope["grounding"]["ref"], "main")
        self.assertEqual(len(envelope["grounding"]["sha"]), 40)
        self.assertTrue(Path(envelope["grounding"]["path"]).is_dir())

    def test_row_open_pr_head_wins_revise_mode(self):
        _git(["fetch", "origin"], self.root)
        _git(["checkout", "-b", "201-fix-gadget"], self.root)
        _write(self.root / "gadget.txt", "fixed\n")
        _git(["add", "gadget.txt"], self.root)
        _git(["commit", "-m", "fix gadget"], self.root)
        _git(["push", "origin", "201-fix-gadget"], self.root)
        _git(["checkout", "main"], self.root)

        envelope = self._envelope(
            issue="201", fixture_case="prep_planner_row_open_pr_revise", ambient="201-fix-gadget"
        )
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_OPEN_PR_HEAD)
        self.assertEqual(envelope["plan_ref"], "201-fix-gadget")
        self.assertEqual(envelope["vector"]["mode"], "revise")
        self.assertEqual(envelope["grounding"]["ref"], "201-fix-gadget")
        self.assertEqual(len(envelope["grounding"]["sha"]), 40)

    def test_revise_carries_the_open_prs_polish_ledger_from_an_inline_body(self):
        # An evaluator soft-reject routed to `planner revise` (skills/_shared/polish-ledger.md): the
        # ledger's `apply` entries are the work the re-plan absorbs, and an inline PR body is
        # otherwise not forwarded — so the scanned ledger is the planner's only view of them.
        _git(["fetch", "origin"], self.root)
        _git(["checkout", "-b", "201-fix-gadget"], self.root)
        _write(self.root / "gadget.txt", "fixed\n")
        _git(["add", "gadget.txt"], self.root)
        _git(["commit", "-m", "fix gadget"], self.root)
        _git(["push", "origin", "201-fix-gadget"], self.root)
        _git(["checkout", "main"], self.root)

        envelope = self._envelope(
            issue="201", fixture_case="prep_planner_revise_polish", ambient="201-fix-gadget"
        )
        polish = envelope["revise"]["open_pr"]["polish"]
        self.assertTrue(polish["present"])
        self.assertEqual(
            [(e["id"], e["disposition"]) for e in polish["entries"]], [("P1.1", "apply")]
        )

    def test_row_story_under_open_epic(self):
        self._push_branch("epic/100-sandbox-fixture")
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["vector"]["type"], "story")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_PARENT_BRANCH)
        self.assertEqual(envelope["plan_ref"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["grounding"]["ref"], "epic/100-sandbox-fixture")
        self.assertEqual(len(envelope["grounding"]["sha"]), 40)
        self.assertEqual(envelope["story"]["parent_epic"]["number"], 100)
        self.assertTrue(envelope["story"]["parent_epic_open"])
        self.assertEqual(envelope["story"]["epic_branch"]["branch"], "epic/100-sandbox-fixture")
        # The session sits in the EPIC worktree, which is not this story's own branch, so the
        # `story-own-branch` probe is suppressed and this row is reached unchanged.
        self.assertIsNone(envelope["story"]["ambient_contains_base_tip"])
        self.assertTrue(envelope["story"]["epic_plan"]["present"])
        log = envelope["story"]["epic_delivery_log"]
        self.assertTrue(log["present"])
        # This fixture's log is the LEGACY monolithic comment, so the two-tier read reports that
        # tier and exposes its id under `legacy_comment_id` — there is no single `comment_id` any
        # more, because a per-story log is a SET of comments (#41).
        self.assertEqual(log["log_source"], "legacy")
        # #34: located by a thread scan, so the id must be the REST numeric one (fixture node id
        # "IC_epic_log"), never the node id the REST delete endpoint 404s on.
        self.assertEqual(log["legacy_comment_id"], 9102)
        self.assertEqual(envelope["suggested_playbook"], "story-jit.md")

    def test_untyped_sub_issue_of_an_open_epic_grounds_on_the_epic_branch(self):
        # #31's planner half: an untyped sub-issue grounded on the default branch while the
        # resolver built it on `epic/<N>-<slug>` — planning against a tree missing every
        # predecessor story's merged work.
        self._push_branch("epic/100-sandbox-fixture")
        envelope = self._envelope(
            issue="264", fixture_case="prep_planner_untyped_subissue",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_PARENT_BRANCH
        )
        self.assertEqual(envelope["plan_ref"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["grounding"]["ref"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["story"]["parent_epic"]["number"], 100)
        # Same suppression as the typed sibling: `epic/100-…` is not #264's own branch.
        self.assertIsNone(envelope["story"]["ambient_contains_base_tip"])
        self.assertTrue(envelope["story"]["epic_plan"]["present"])
        self.assertTrue(envelope["story"]["epic_delivery_log"]["present"])
        # The epic's `## Story contracts` and delivery log are exactly what this target needs, so
        # it plans just-in-time against current epic HEAD like its `story`-labelled siblings.
        self.assertEqual(envelope["suggested_playbook"], "story-jit.md")

    def test_a_slice_is_refused_rather_than_planned_standalone(self):
        # #266's parent #103 is a STORY, so #266 is a deliverable slice by construction. A slice is
        # planned as a PHASE of its parent's plan (the phase's `sub-issue:` key names it), so a
        # standalone plan here would be a second, competing decomposition of the same work.
        result = self._run(
            ["266", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_slice",
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "TARGET_IS_SLICE")
        self.assertEqual(envelope["decision"]["context"]["parent"]["number"], 103)
        # The parent's own gather must NOT have run — the refusal precedes it, and the fixture
        # manifest carries no entry for it, so the shim would have failed the run otherwise.
        options = envelope["decision"]["options"]
        self.assertEqual(len(options), 3)
        # External remedies only: this prep has no override flag, so a "plan anyway" option would
        # raise the identical card on the re-run that was supposed to act on it.
        self.assertNotIn("anyway", " ".join(options))

    def test_a_slice_with_its_own_open_pr_is_not_refused(self):
        # The open-PR-head row wins when more than one applies (docs/specs/planner.md Step 4.5).
        # A slice carrying its OWN open PR was promoted to a story sessions ago — a fact on GitHub
        # that this card cannot unwind — so refusing would gate every planner session on work
        # already in flight, and would disagree with prep_workspace_open, whose continue row
        # short-circuits ahead of its own copy of this refusal.
        _git(["fetch", "origin"], self.root)
        _git(["checkout", "-b", "266-add-the-rename-generation-column"], self.root)
        _git(["push", "origin", "266-add-the-rename-generation-column"], self.root)
        _git(["checkout", "main"], self.root)
        # The fixture's parent classification WOULD answer "non-epic" — the suppression is proved
        # by the refusal not firing, not by a missing manifest entry (a shim miss is non-fatal here,
        # so an absent entry would let this pass with the guard removed).
        envelope = self._envelope(
            issue="266", fixture_case="prep_planner_slice_with_own_pr",
            ambient="266-add-the-rename-generation-column",
        )
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_OPEN_PR_HEAD)
        self.assertEqual(envelope["plan_ref"], "266-add-the-rename-generation-column")

    def test_untyped_sub_issue_without_a_parent_epic_branch_grounds_on_main(self):
        # No `epic/100-*` pushed. The parent may be an epic not yet opened, or a STORY — which
        # would make this target a deliverable slice, planned as its parent's phases, never as a
        # just-in-time story. Unproven, it grounds exactly as it did before.
        envelope = self._envelope(issue="264", fixture_case="prep_planner_untyped_subissue")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_DEFAULT)
        self.assertEqual(envelope["plan_ref"], "main")
        self.assertIsNone(envelope.get("story"))
        self.assertIn("PARENT_HAS_NO_INTEGRATION_BRANCH", envelope["notices"])
        # The parent read answered "epic" (unbootstrapped), so this is the not-yet-opened case and
        # not a degraded one — a missing manifest entry would show up as this notice instead.
        self.assertNotIn("PARENT_KIND_UNAVAILABLE", envelope["notices"])
        self.assertNotEqual(envelope["suggested_playbook"], "story-jit.md")

    def test_an_epic_pr_mentioning_the_story_does_not_supply_the_plan_ref(self):
        # Issue #29's planner half: `_select_plan_ref` checks the open-PR head FIRST and
        # unconditionally, and `_build_revise_facts` parses THAT PR's `## Phase tracker`. Fed the
        # parent epic's integration PR (which lists every story by number), a story would be
        # planned against the epic's ref by the wrong row, carrying the EPIC's phase tracker.
        self._push_branch("epic/100-sandbox-fixture")
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_story_epic_pr_mention",
            ambient="epic/100-sandbox-fixture",
        )
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_PARENT_BRANCH
        )
        self.assertEqual(envelope["plan_ref"], "epic/100-sandbox-fixture")

    def test_row_story_under_open_epic_bootstrap(self):
        # D4 regression fixture: story under an OPEN parent epic, but zero `git ls-remote` matches
        # for that epic's integration branch (the epic itself hasn't bootstrapped a branch yet --
        # no sibling story has been resolved for it). Deliberately does NOT push an epic branch.
        envelope = self._envelope(issue="203", fixture_case="prep_planner_row_story_parent_bootstrap")
        self.assertEqual(envelope["vector"]["type"], "story")
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_PARENT_BOOTSTRAP
        )
        # The row must be truthful, never the "no open parent" label a self-contradictory D4 run
        # produced (parent_epic_open: true alongside a row name that denies it).
        self.assertNotEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_NO_PARENT
        )
        self.assertEqual(envelope["plan_ref"], "main")
        self.assertEqual(envelope["grounding"]["ref"], "main")
        self.assertEqual(len(envelope["grounding"]["sha"]), 40)
        self.assertEqual(envelope["story"]["parent_epic"]["number"], 101)
        self.assertTrue(envelope["story"]["parent_epic_open"])
        self.assertEqual(envelope["story"]["epic_branch"]["match_count"], 0)
        self.assertIsNone(envelope["story"]["epic_branch"]["branch"])
        self.assertFalse(envelope["story"]["epic_plan"]["present"])
        self.assertFalse(envelope["story"]["epic_delivery_log"]["present"])
        # Routing is UNCHANGED by the D4 fix: story + parent_epic_open -> story-jit.md regardless
        # of which of the two "no branch" rows fired.
        self.assertEqual(envelope["suggested_playbook"], "story-jit.md")

    def test_row_epic_as_target_branch_found(self):
        self._push_branch("epic/300-sandbox-epic")
        envelope = self._envelope(
            issue="300", fixture_case="prep_planner_row_epic", ambient="epic/300-sandbox-epic"
        )
        self.assertEqual(envelope["vector"]["type"], "epic")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_EPIC_BRANCH)
        self.assertEqual(envelope["plan_ref"], "epic/300-sandbox-epic")
        self.assertEqual(envelope["grounding"]["ref"], "epic/300-sandbox-epic")
        self.assertEqual(envelope["epic"]["branch"]["match_count"], 1)
        self.assertEqual(len(envelope["epic"]["branch"]["sha"]), 40)
        self.assertTrue(envelope["epic"]["stories_filed"])
        self.assertEqual(len(envelope["epic"]["stories"]), 2)
        numbers = {s["number"]: s["state"] for s in envelope["epic"]["stories"]}
        self.assertEqual(numbers, {301: "OPEN", 302: "CLOSED"})
        self.assertEqual(envelope["suggested_playbook"], "epic.md")

    def test_epic_with_per_story_log_entries_reports_the_entries_tier(self):
        """#41: the planner reads the log across both tiers. This epic's entries are all per-story
        comments, so a v1-only lookup would find nothing at all.
        """
        self._push_branch("epic/300-sandbox-epic")
        envelope = self._envelope(
            issue="300",
            fixture_case="prep_planner_row_epic_log_entries",
            ambient="epic/300-sandbox-epic",
        )
        log = envelope["epic"]["delivery_log"]
        self.assertTrue(log["present"])
        self.assertEqual(log["log_source"], "entries")
        self.assertEqual(log["entry_count"], 2)
        self.assertEqual([e["story"] for e in log["entries"]], [301, 302])
        # The whole log is staged to ONE path, so the plan reviewer's `<<epic_delivery_log>>` stays a
        # single file and Dimension 8 needs no change.
        body = log.get("body")
        if body is None:
            body = Path(log["body_path"]).read_text(encoding="utf-8")
        self.assertIn("#301 \u2014 delivered:", body)
        self.assertIn("#302 \u2014 delivered:", body)

    def test_a_v2_only_log_never_reports_that_no_story_has_merged(self):
        """The silent regression this pins (#41). The `attention` claim is about MERGES, and it was
        derived from a v1-marker lookup — so an epic whose entries are all per-story comments would
        be told "no story has merged" while every story had. It regressed green: no test named the
        string, and the operator is the only one who would notice.
        """
        self._push_branch("epic/300-sandbox-epic")
        envelope = self._envelope(
            issue="300",
            fixture_case="prep_planner_row_epic_log_entries",
            ambient="epic/300-sandbox-epic",
        )
        self.assertFalse(
            [a for a in envelope["attention"] if "no story has merged" in a],
            "a log made of per-story entries is a COMPLETE log — got %r" % (envelope["attention"],),
        )

    def test_an_epic_with_no_log_at_all_still_reports_that_no_story_has_merged(self):
        """The other half: the attention line must still fire when it is true, or fixing the false
        positive would just delete the signal."""
        self._push_branch("epic/300-sandbox-epic")
        envelope = self._envelope(
            issue="300",
            fixture_case="prep_planner_row_epic_native",
            ambient="epic/300-sandbox-epic",
        )
        self.assertFalse(envelope["epic"]["delivery_log"]["present"])
        self.assertTrue(
            [a for a in envelope["attention"] if "no story has merged" in a],
            "expected the no-merges attention line, got %r" % (envelope["attention"],),
        )

    def test_row_epic_as_target_reports_checklist_source_for_a_legacy_epic(self):
        self._push_branch("epic/300-sandbox-epic")
        envelope = self._envelope(
            issue="300", fixture_case="prep_planner_row_epic", ambient="epic/300-sandbox-epic"
        )
        self.assertEqual(envelope["epic"]["stories_source"], "checklist")

    def test_row_epic_as_target_reads_the_native_relation_when_the_body_has_no_stories(self):
        """The regression this guards: a fresh epic body has NO `## Stories` section, so a
        body-only read reports `stories_filed: false` and the planner's handoff routes to the
        drafter to file stories that are already filed (skills/_shared/epic-story-hierarchy.md).
        The fixture also omits the per-story `gh issue view` entries, so live state must come from
        the relation itself.
        """
        self._push_branch("epic/300-sandbox-epic")
        envelope = self._envelope(
            issue="300",
            fixture_case="prep_planner_row_epic_native",
            ambient="epic/300-sandbox-epic",
        )
        self.assertEqual(envelope["epic"]["stories_source"], "sub-issues")
        self.assertTrue(envelope["epic"]["stories_filed"])
        numbers = {s["number"]: s["state"] for s in envelope["epic"]["stories"]}
        self.assertEqual(numbers, {301: "OPEN", 302: "CLOSED"})
        # `checked` mirrors live state on the native tier.
        checked = {s["number"]: s["checked"] for s in envelope["epic"]["stories"]}
        self.assertEqual(checked, {301: False, 302: True})

    def test_row_epic_as_target_bootstrap(self):
        envelope = self._envelope(issue="310", fixture_case="prep_planner_row_epic_bootstrap")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_EPIC_BOOTSTRAP)
        self.assertEqual(envelope["plan_ref"], "main")
        self.assertEqual(envelope["epic"]["branch"]["match_count"], 0)
        self.assertIsNone(envelope["epic"]["branch"]["branch"])
        self.assertFalse(envelope["epic"]["stories_filed"])
        # one unfiled placeholder bullet
        self.assertEqual(envelope["epic"]["stories"], [
            {"number": None, "title": "Story placeholder one", "checked": False, "state": None, "live_title": None}
        ])
        self.assertTrue(
            any("bootstrap" in item for item in envelope["attention"]), envelope["attention"]
        )


class GroundingDocInventoryTests(PrepPlannerSandboxTestCase):
    """`grounding_docs` is now the consuming repo's OWN declaration (the `<!-- doc-catalogue -->`
    block in `docs/README.md`, per skills/_shared/doc-catalogue.md), read at the ensured grounding
    checkout — not a path list this prep asserts. What these tests protect is the three-state
    distinction the migration introduced: declared-and-present, declared-but-missing, and no
    declaration at all."""

    def test_declared_docs_are_reported_with_paths_inside_the_workspace(self):
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        entries = envelope["grounding_docs"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["path"], "docs/prd.md")
        self.assertEqual(entry["role"], "prd")
        self.assertEqual(entry["authority"], "binding")
        self.assertTrue(entry["present"])
        self.assertIn(envelope["grounding"]["path"], entry["abs_path"])
        self.assertEqual(envelope["notices"], [])
        self.assertEqual(envelope["attention"], [])

    def test_multiple_entries_keep_declaration_order_and_both_authorities(self):
        _write(self.root / "docs" / "architecture.md", "# Architecture\n")
        self._replace_catalogue(
            "- `docs/prd.md` — prd — binding — What the product is.\n"
            "- `docs/architecture.md` — architecture — informative — Layer rules.\n"
        )
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        entries = envelope["grounding_docs"]
        self.assertEqual([e["path"] for e in entries], ["docs/prd.md", "docs/architecture.md"])
        self.assertEqual([e["authority"] for e in entries], ["binding", "informative"])
        self.assertEqual(envelope["attention"], [])

    def test_absent_catalogue_yields_no_entries_the_notice_and_an_attention_line(self):
        self._remove_catalogue()
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertIn("DOC_CATALOGUE_ABSENT", envelope["notices"])
        self.assertTrue(
            any("no doc catalogue" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_empty_catalogue_is_not_the_absent_notice_but_still_attention(self):
        """Absent ≠ empty: a repo declaring no documents made a declaration, so no notice fires —
        but the operator still needs to know planning is ungrounded."""
        self._replace_catalogue("")
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertEqual(envelope["notices"], [])
        self.assertTrue(
            any("declares no documents" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_declared_but_missing_doc_is_reported_not_dropped(self):
        self._replace_catalogue(
            "- `docs/prd.md` — prd — binding — What the product is.\n"
            "- `docs/ui-design.md` — ui-design — binding — Not committed on this ref.\n"
        )
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        by_path = {e["path"]: e for e in envelope["grounding_docs"]}
        self.assertEqual(len(by_path), 2)
        self.assertFalse(by_path["docs/ui-design.md"]["present"])
        self.assertIsNone(by_path["docs/ui-design.md"]["abs_path"])
        self.assertTrue(
            any("docs/ui-design.md" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_declared_directory_is_present_with_members_and_raises_no_attention(self):
        """The 4.26.0 regression end to end: a catalogue entry naming a committed directory (a
        document set) is not "absent at the grounding ref" — it is a `dir` entry whose members the
        planner reads, and no stale-entry attention line fires."""
        _write(self.root / "docs" / "architecture" / "view-layer.md", "# View layer\n")
        self._replace_catalogue(
            "- `docs/prd.md` — prd — binding — What the product is.\n"
            "- `docs/architecture/` — architecture — binding — The architecture spokes.\n"
        )
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        by_path = {e["path"]: e for e in envelope["grounding_docs"]}
        spokes = by_path["docs/architecture/"]
        self.assertTrue(spokes["present"])
        self.assertEqual(spokes["kind"], "dir")
        self.assertEqual(spokes["members"], ["docs/architecture/view-layer.md"])
        self.assertEqual(envelope["attention"], [])

    def test_malformed_entries_are_skipped_without_losing_their_neighbours(self):
        self._replace_catalogue(
            "Prose that is not an entry.\n"
            "- `docs/prd.md` — prd — binding — What the product is.\n"
            "- `docs/x.md` — prd — authoritative — authority outside the closed pair\n"
        )
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertEqual([e["path"] for e in envelope["grounding_docs"]], ["docs/prd.md"])

    def test_the_catalogue_is_not_read_from_commands_or_claude_md(self):
        """The fixed-home rule — `docs/README.md` only, never `read_block_anywhere`'s defaults."""
        self._remove_catalogue()
        _write(self.root / "CLAUDE.md", _catalogue_block(SEEDED_CATALOGUE_INTERIOR))
        self._commit_push("catalogue in the wrong file")
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertIn("DOC_CATALOGUE_ABSENT", envelope["notices"])

    def test_refresh_reads_no_catalogue_and_raises_no_grounding_attention(self):
        """`--refresh` skips the whole grounding assertion, so "we didn't look" must not render as
        "the repo declared nothing" — neither the notice nor an attention line may appear."""
        envelope = self._envelope(
            issue="200", fixture_case="prep_planner_row_default", extra_args=["--refresh"]
        )
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertNotIn("DOC_CATALOGUE_ABSENT", envelope["notices"])
        self.assertEqual(
            [item for item in envelope["attention"] if "catalogue" in item], []
        )


class OpenQuestionTrackerSearchTests(PrepPlannerSandboxTestCase):
    """Bug (a) regression: a "(not filed)" OQ whose companion question issue actually EXISTS must
    surface in `open_question_candidates` (S12 DoD's second box) — plus the legitimately-open
    no-match counterpart."""

    def test_seeded_tracker_match_surfaces_the_candidate(self):
        envelope = self._envelope(issue="400", fixture_case="prep_planner_oq_tracker_match")
        self.assertEqual(len(envelope["open_questions"]), 1)
        self.assertEqual(envelope["open_questions"][0]["question"], "(not filed)")
        self.assertEqual(len(envelope["open_question_candidates"]), 1)
        group = envelope["open_question_candidates"][0]
        self.assertEqual(group["oq_id"], "tag case sensitivity")
        self.assertEqual(group["query"], "tag case sensitivity")
        self.assertEqual(len(group["candidates"]), 1)
        self.assertEqual(group["candidates"][0]["number"], 51)
        self.assertEqual(group["candidates"][0]["state"], "OPEN")
        self.assertIn("question", group["candidates"][0]["labels"])
        self.assertTrue(
            any("tag case sensitivity" in item and "do not record it as (not filed)" in item
                for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_no_match_leaves_not_filed_path_legitimately_open(self):
        envelope = self._envelope(issue="401", fixture_case="prep_planner_oq_no_match")
        self.assertEqual(len(envelope["open_questions"]), 1)
        self.assertEqual(envelope["open_question_candidates"], [])
        self.assertEqual(envelope["attention"], [])


class OqQueryOneShotTests(PrepPlannerSandboxTestCase):
    """S13 authorized additive `--oq-query` mode: the one-shot tracker de-dup lookup a playbook runs
    for an OQ it detects ANEW during grounding (not in the issue body). Fast path — one `gh issue
    list` call, no facts assembly, conformant envelope."""

    def test_oq_query_emits_candidates_without_full_facts(self):
        result = self._run(
            ["400", "octo/widgets", "--oq-query", "cache eviction policy"],
            fixture_case="prep_planner_oq_query",
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        # It is the one-shot payload, NOT a full facts block (no vector / suggested_playbook).
        self.assertNotIn("vector", envelope)
        self.assertNotIn("suggested_playbook", envelope)
        self.assertIn("oq_query_candidates", envelope)
        groups = envelope["oq_query_candidates"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["query"], "cache eviction policy")
        self.assertEqual(groups[0]["candidates"][0]["number"], 88)
        self.assertEqual(groups[0]["candidates"][0]["state"], "OPEN")

    def test_oq_query_empty_list_makes_no_call(self):
        # Zero queries -> empty candidate list, no gh call (build core, no network).
        payload, notices, decision = prep_planner.build_oq_query("octo/widgets", [], cwd=None)
        self.assertIsNone(decision)
        self.assertEqual(payload, {"repo": "octo/widgets", "oq_query_candidates": []})
        self.assertEqual(notices, [])


class MarkerDetectionVariantTests(PrepPlannerSandboxTestCase):
    """Marker-detection variants: research only / plan only / both / neither."""

    def test_neither_marker_present(self):
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertFalse(envelope["plan"]["present"])
        self.assertFalse(envelope["research"]["present"])
        self.assertEqual(envelope["vector"]["mode"], "fresh")

    def test_plan_only(self):
        _git(["fetch", "origin"], self.root)
        _git(["checkout", "-b", "201-fix-gadget"], self.root)
        _write(self.root / "gadget.txt", "fixed\n")
        _git(["add", "gadget.txt"], self.root)
        _git(["commit", "-m", "fix gadget"], self.root)
        _git(["push", "origin", "201-fix-gadget"], self.root)
        _git(["checkout", "main"], self.root)

        envelope = self._envelope(
            issue="201", fixture_case="prep_planner_row_open_pr_revise", ambient="201-fix-gadget"
        )
        self.assertTrue(envelope["plan"]["present"])
        self.assertFalse(envelope["research"]["present"])
        self.assertEqual(envelope["vector"]["mode"], "revise")

    def test_research_only(self):
        envelope = self._envelope(issue="500", fixture_case="prep_planner_marker_research_only")
        self.assertFalse(envelope["plan"]["present"])
        self.assertTrue(envelope["research"]["present"])
        self.assertEqual(envelope["vector"]["mode"], "fresh")
        self.assertIn("comment_url", envelope["research"])

    def test_an_untrusted_dossier_reads_absent_with_a_notice(self):
        # #179: the same dossier by an untrusted author is skipped, never staged.
        envelope = self._envelope(issue="500", fixture_case="prep_planner_marker_research_only_untrusted")
        self.assertFalse(envelope["research"]["present"])
        notices = [n for n in envelope["notices"] if n.startswith("MARKER_UNTRUSTED")]
        self.assertEqual(len(notices), 1)
        self.assertIn("<!-- issue-research:v1 -->", notices[0])

    def test_research_comment_id_is_the_rest_numeric_id_not_the_node_id(self):
        """#34: this module locates the dossier by scanning the normalized thread, whose `id` is
        the GraphQL node id. Any id a caller could hand to `--delete-marker-id` must be the REST
        numeric one (`databaseId`) -- a node id there is a guaranteed 404.
        """
        envelope = self._envelope(issue="500", fixture_case="prep_planner_marker_research_only")
        self.assertEqual(envelope["research"]["comment_id"], 9501)

    def test_ambiguous_marker_context_reports_numeric_ids(self):
        result = self._run(
            ["602", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_research_marker_ambiguous",
        )
        envelope = _parse_one_envelope(result.stdout)
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        # The fixture's node ids are "IC_res1"/"IC_res2" and must never appear here.
        self.assertEqual(sorted(envelope["decision"]["context"]["comment_ids"]), [9801, 9802])

    def test_both_markers_present(self):
        envelope = self._envelope(issue="501", fixture_case="prep_planner_marker_both")
        self.assertTrue(envelope["plan"]["present"])
        self.assertTrue(envelope["research"]["present"])
        self.assertEqual(envelope["vector"]["mode"], "revise")


class ReviseFactsTests(PrepPlannerSandboxTestCase):
    """Revise facts include prior plan body path + phase tracker (S12 DoD's third box)."""

    def test_prior_plan_body_is_staged_to_a_path_and_phase_tracker_parsed(self):
        _git(["fetch", "origin"], self.root)
        _git(["checkout", "-b", "900-large-plan"], self.root)
        _write(self.root / "large.txt", "x\n")
        _git(["add", "large.txt"], self.root)
        _git(["commit", "-m", "large plan work"], self.root)
        _git(["push", "origin", "900-large-plan"], self.root)
        _git(["checkout", "main"], self.root)

        envelope = self._envelope(
            issue="900", fixture_case="prep_planner_revise_large_plan", ambient="900-large-plan"
        )
        self.assertEqual(envelope["vector"]["mode"], "revise")
        self.assertEqual(envelope["plan"]["body_mode"], "path")
        self.assertTrue(Path(envelope["plan"]["body_path"]).is_file())
        self.assertGreater(Path(envelope["plan"]["body_path"]).stat().st_size, 25600)

        revise = envelope["revise"]
        self.assertEqual(revise["prior_plan_sha"], "9999999")
        self.assertEqual(len(revise["grounding_sha"]), 40)
        self.assertIsNotNone(revise["open_pr"])
        self.assertEqual(revise["open_pr"]["headRefName"], "900-large-plan")
        # The polish ledger rides on the open-PR fact (absent here: the body has no `## Polish`).
        self.assertEqual(
            revise["open_pr"]["polish"],
            {"present": False, "entries": [], "unparsed": [], "dispositions_trusted": False},
        )
        self.assertEqual(len(revise["phase_tracker"]), 2)
        # `annotation` (#48 review): the row's trailing `(operator action <ISO-date>)` when it has
        # one, so a rebuild can preserve the only record that an operator phase landed. `None` here.
        self.assertEqual(revise["phase_tracker"][0], {
            "checked": True, "phase": 1, "title": "substrate", "commit_sha": "9999999",
            "annotation": None, "sub_label": None,
        })
        self.assertEqual(revise["phase_tracker"][1], {
            "checked": False, "phase": 2, "title": "harness", "commit_sha": None,
            "annotation": None, "sub_label": None,
        })

    def test_fresh_mode_has_no_revise_key(self):
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertNotIn("revise", envelope)


class ShippedRecordFactsTests(PrepPlannerSandboxTestCase):
    """`facts.plan.shipped` (skills/_shared/plan-shipped-phases.md): the legacy shipped-phase records
    the plan still points at — the source the revise folds back. Nothing writes a record any more,
    so there is no move list. The `shipped_records` fixture's plan carries no pointer, so the open
    PR's (#55) records are read; a second phase-1 record names another PR and is not. The `restore`
    fixture's pointer names #40, a PR that is not the open one.
    """

    def _shipped_envelope(self, fixture_case="prep_planner_shipped_records"):
        _git(["fetch", "origin"], self.root)
        _git(["checkout", "-b", "201-fix-gadget"], self.root)
        _write(self.root / "gadget.txt", "fixed\n")
        _git(["add", "gadget.txt"], self.root)
        _git(["commit", "-m", "fix gadget"], self.root)
        _git(["push", "origin", "201-fix-gadget"], self.root)
        _git(["checkout", "main"], self.root)
        return self._envelope(
            issue="201", fixture_case=fixture_case, ambient="201-fix-gadget"
        )

    def test_with_no_pointer_the_open_prs_records_are_read(self):
        shipped = self._shipped_envelope()["plan"]["shipped"]
        self.assertTrue(shipped["present"])
        self.assertEqual(shipped["pr"], 55)
        self.assertEqual([e["phase"] for e in shipped["entries"]], [1])
        self.assertEqual(shipped["entries"][0]["comment_id"], 9003)
        self.assertTrue(Path(shipped["body_path"]).is_file())

    def test_a_closed_prs_records_are_the_fold_back_source(self):
        # The plan's pointer names #40 — a Start-fresh or a hand-closed PR. #40's records are the
        # only copy of their entries, so prep stages them as the one fold-back source.
        envelope = self._shipped_envelope(fixture_case="prep_planner_shipped_restore")
        shipped = envelope["plan"]["shipped"]
        self.assertEqual(shipped["prs"], [40])
        self.assertEqual([e["phase"] for e in shipped["entries"]], [1])
        body = Path(shipped["body_path"]).read_text(encoding="utf-8")
        self.assertIn("**Shipped on:** #40", body)
        self.assertIn("`gadget.rb` — fixes the gadget", body)
        self.assertTrue(any("only copy" in line for line in envelope["attention"]))

    def test_one_fact_shape_and_no_move_list(self):
        shipped = self._shipped_envelope()["plan"]["shipped"]
        for key in ("present", "pr", "prs", "entries", "unkeyed"):
            self.assertIn(key, shipped)
        for retired in ("to_relocate", "restore", "other_pr_entries"):
            self.assertNotIn(retired, shipped)

    def test_an_inline_prior_plan_is_still_staged_to_a_path(self):
        plan = self._shipped_envelope()["plan"]
        self.assertEqual(plan["body_mode"], "inline")
        self.assertTrue(
            Path(plan["body_path"]).read_text(encoding="utf-8").startswith("<!-- implementation-plan:v1 -->")
        )


class DecisionCodeTests(PrepPlannerSandboxTestCase):
    def test_auth_required_on_first_gh_call(self):
        result = self._run(
            ["600", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_auth_required",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AUTH_REQUIRED")

    def test_duplicate_plan_comments_yields_marker_ambiguous(self):
        result = self._run(
            ["601", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_marker_ambiguous",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        self.assertFalse((self.root / ".worktrees").exists())

    def test_duplicate_research_comments_yields_marker_ambiguous(self):
        # This module's OWN thread-scan MARKER_AMBIGUOUS (research dossier), not gh_gather's.
        result = self._run(
            ["602", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_research_marker_ambiguous",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        self.assertIn("research-dossier", envelope["decision"]["summary"])

    def test_multiple_epic_branch_matches_yields_ambiguous(self):
        self._push_branch("epic/320-a")
        self._push_branch("epic/320-b")
        result = self._run(
            ["320", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_epic_branch_multiple",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")
        self.assertEqual(len(envelope["decision"]["context"]["candidates"]), 2)

    def test_multiple_parent_epic_matches_yields_ambiguous(self):
        result = self._run(
            ["700", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_story_parent_multiple",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")
        self.assertEqual(len(envelope["decision"]["context"]["candidates"]), 2)

    def test_malformed_open_questions_section_yields_ambiguous(self):
        _write(self.root / "issue_body_unused.txt", "")  # no-op, keeps root clean
        entries_or_decision = None
        try:
            prep_planner.parse.parse_oq_links(
                "## Open questions\n<!-- open-question-links:v1 -->\n"
                "- OQ: `x` (issue body) — gates: y\n  — disposition: not-a-real-disposition\n"
            )
        except prep_planner.parse._OqLinksMalformed:
            entries_or_decision = "raised"
        self.assertEqual(entries_or_decision, "raised")


class GroundingMismatchTests(PrepPlannerSandboxTestCase):
    """v3: the ambient-grounding assertion — WORKSPACE_MISMATCH decisions replace the retired
    ROOT_* propagation coverage (the planner no longer polices root state; a `main` plan_ref
    accepts any clean up-to-date main checkout including the project root)."""

    def _mismatch(self, envelope, reason):
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "WORKSPACE_MISMATCH")
        self.assertEqual(envelope["decision"]["context"]["reason"], reason)

    def test_main_plan_ref_from_the_project_root_passes(self):
        # plan-before-open: fresh standard planning happens on a main checkout — the project root
        # itself is the canonical posture, not a mismatch.
        envelope = self._envelope(fixture_case="prep_planner_row_default")
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["plan_ref"], "main")

    def _advance(self, branch):
        """Push one commit onto `origin/<branch>` from its worktree, so that tip is in the shared
        object DB but no other checkout contains it."""
        wt = self._mk_ambient(branch)
        self._commit_in(wt, "advanced.txt", "advance %s" % branch)
        _git(["push", "origin", branch], wt)

    def test_another_branch_at_the_identical_main_sha_passes(self):
        # Content, not names: a just-opened worktree for another issue holds exactly main's tip,
        # so grounding there reads the same code and the footer (`main@<sha>`) stays true.
        envelope = self._envelope(
            fixture_case="prep_planner_row_default", ambient="999-unrelated-work"
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["plan_ref"], "main")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_DEFAULT)
        self.assertEqual(envelope["grounding"]["basis"], "identical")
        self.assertEqual(envelope["grounding"]["ref"], "main")
        self.assertEqual(envelope["grounding"]["branch"], "999-unrelated-work")
        self.assertTrue(
            any("identical to 'main' tip" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_another_branch_ahead_of_main_is_a_card_offering_ground_ambient(self):
        # Another issue's commits on top of main: code that may never ship with THIS issue, so the
        # operator decides — the card says how many commits and how to accept them.
        wt = self._mk_ambient("999-unrelated-work")
        self._commit_in(wt, "other.txt", "someone else's work")
        envelope = self._envelope(
            fixture_case="prep_planner_row_default", ambient="999-unrelated-work"
        )
        self._mismatch(envelope, "branch_mismatch")
        context = envelope["decision"]["context"]
        self.assertEqual(context["expected_branch"], "main")
        self.assertEqual(context["actual_branch"], "999-unrelated-work")
        self.assertEqual(context["ahead_commits"], 1)
        self.assertEqual(len(context["base_tip_sha"]), 40)
        self.assertIn("--ground-ambient", envelope["decision"]["options"][0])

    def test_ground_ambient_accepts_a_checkout_ahead_of_main(self):
        wt = self._mk_ambient("999-unrelated-work")
        head = self._commit_in(wt, "other.txt", "someone else's work")
        envelope = self._envelope(
            fixture_case="prep_planner_row_default", ambient="999-unrelated-work",
            extra_args=["--ground-ambient"],
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_AMBIENT_ACCEPTED
        )
        self.assertEqual(envelope["plan_ref"], "999-unrelated-work")
        self.assertEqual(envelope["grounding"]["basis"], "accepted")
        self.assertEqual(envelope["grounding"]["base_ref"], "main")
        self.assertEqual(envelope["grounding"]["sha"], head)
        self.assertTrue(
            any("operator's answer" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_ground_ambient_never_admits_a_checkout_behind_main(self):
        # The flag answers "extra commits are fine", never "stale code is fine".
        self._mk_ambient("999-unrelated-work")
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        _write(Path(other.path) / "moved.txt", "origin moved\n")
        _git(["add", "moved.txt"], other.path)
        _git(["commit", "-m", "origin-side commit"], other.path)
        _git(["push", "origin", "HEAD:main"], other.path)
        envelope = self._envelope(
            fixture_case="prep_planner_row_default", ambient="999-unrelated-work",
            extra_args=["--ground-ambient"],
        )
        self._mismatch(envelope, "branch_mismatch")
        self.assertNotIn("ahead_commits", envelope["decision"]["context"])

    def test_refresh_keeps_an_accepted_plan_ref_and_re_asks_without_the_flag(self):
        # A refresh that silently fell back to `main` would flip plan_ref mid-session — the footer
        # lie the probe-on-refresh invariant exists to prevent. With the flag it holds; without it
        # the card comes back rather than a quiet fallback.
        wt = self._mk_ambient("999-unrelated-work")
        self._commit_in(wt, "other.txt", "someone else's work")
        held = self._envelope(
            fixture_case="prep_planner_row_default", ambient="999-unrelated-work",
            extra_args=["--refresh", "--ground-ambient"],
        )
        self.assertEqual(held["plan_ref"], "999-unrelated-work")
        self.assertEqual(
            held["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_AMBIENT_ACCEPTED
        )
        dropped = self._envelope(
            fixture_case="prep_planner_row_default", ambient="999-unrelated-work",
            extra_args=["--refresh"],
        )
        self._mismatch(dropped, "branch_mismatch")

    def test_identical_root_checkout_passes_for_a_freshly_bootstrapped_epic(self):
        # The epic branch was just cut from main and carries nothing yet: the project root holds
        # the same code, so it is an honest vantage for `epic/<N>-<slug>@<sha>`.
        self._push_branch("epic/100-sandbox-fixture")
        envelope = self._envelope(issue="202", fixture_case="prep_planner_row_story_under_epic")
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["plan_ref"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["grounding"]["basis"], "identical")
        self.assertEqual(envelope["grounding"]["branch"], "main")

    def test_non_main_plan_ref_from_the_project_root_is_a_mismatch(self):
        # A story under an open epic grounds on the epic branch — sitting at the root's main
        # checkout is the wrong vantage (a stale footer SHA / wrong-tree read otherwise). The
        # at-root check fires first, and its card carries the expected branch the operator
        # should open. The epic carries a commit `main` lacks — at the identical sha the root
        # checkout holds the same code and passes (see the `identical` test below).
        self._push_branch("epic/100-sandbox-fixture")
        self._advance("epic/100-sandbox-fixture")
        envelope = self._envelope(issue="202", fixture_case="prep_planner_row_story_under_epic")
        self._mismatch(envelope, "at_project_root")
        self.assertEqual(
            envelope["decision"]["context"]["expected_branch"], "epic/100-sandbox-fixture"
        )

    def test_grounding_behind_origin_is_stale(self):
        # The ambient main checkout is strictly behind origin/main (someone pushed) — a stale
        # footer SHA would be an immediate `plan: stale` downstream, so this is a decision, not
        # the silent auto-ff the v2 root-freshness protocol performed.
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        _write(Path(other.path) / "moved.txt", "origin moved\n")
        _git(["add", "moved.txt"], other.path)
        _git(["commit", "-m", "origin-side commit"], other.path)
        _git(["push", "origin", "HEAD:main"], other.path)
        envelope = self._envelope(fixture_case="prep_planner_row_default")
        self._mismatch(envelope, "stale_checkout")

    def test_dirty_grounding_is_reported_as_attention_not_a_block(self):
        _write(self.root / "dirty.txt", "uncommitted\n")
        envelope = self._envelope(fixture_case="prep_planner_row_default")
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(envelope["grounding"]["dirty"])
        self.assertTrue(
            any("plan footer SHA" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_second_prep_planner_run_in_the_same_clone_is_not_root_dirty(self):
        # D6 regression at the prep-script level: the live-observed symptom was a composite
        # epic+story planner session's SECOND prep_planner.py call falling off prep onto gh_gather
        # mid-run because the FIRST call's workspace.py ensure --read had written <root>/.gitignore
        # (a working-tree file), which the second call's root-freshness check then read as
        # ROOT_DIRTY. Fixed by moving that write to info/exclude (workspace.py's
        # `_ensure_worktrees_excluded`) — two prep_planner.py runs against the same clone must both
        # come back `status: ok`, never a self-inflicted ROOT_DIRTY on the second.
        # (This harness's own setUp pre-seeds a COMMITTED .gitignore for an unrelated, still-valid
        # reason — a fresh consuming repo's first-ever ensure previously left one uncommitted; that
        # pre-seed predates this fix and stays harmless, so its presence here is expected, not a
        # sign the fix regressed.)
        gitignore_before = (self.root / ".gitignore").read_text(encoding="utf-8")
        first = self._envelope(fixture_case="prep_planner_row_default")
        self.assertEqual(first["status"], "ok")
        second = self._envelope(fixture_case="prep_planner_row_default")
        self.assertEqual(second["status"], "ok")
        self.assertTrue(second["root"]["fresh"])
        # The pre-seeded, already-committed .gitignore must be left byte-for-byte untouched by
        # either run — the fix writes ONLY info/exclude, never this file.
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"), gitignore_before)
        self.assertEqual(_git(["status", "--porcelain"], self.root), "", "root must stay clean")


class StoryOwnBranchPlanRefTests(PrepPlannerSandboxTestCase):
    """Row 4 (`story-own-branch`): a story session standing in its OWN worktree grounds on that
    branch, provided the branch already CONTAINS the tip of the ref rows 5/6 would have chosen.

    The incident this closes: both worktrees sat at the identical SHA and the attach gate refused
    anyway, because its branch-name equality check stands in for a content check it never performs.
    Row 1 (`open-pr-head`) already grounds a story on its own branch once a PR exists; this row
    closes the window before the first PR.
    """

    EPIC_BRANCH = "epic/100-sandbox-fixture"
    STORY_BRANCH = "202-returning-login"

    def _story_worktree_on_epic(self, story_branch=None):
        """The shape `workspace-open` produces: the story branch DESCENDS from the epic branch."""
        story_branch = story_branch or self.STORY_BRANCH
        self._push_branch(self.EPIC_BRANCH)
        self._push_branch(story_branch, base=self.EPIC_BRANCH)
        return story_branch

    def _advance_epic_branch(self, from_clone=None):
        """Move `origin/<EPIC_BRANCH>` forward. From the sandbox clone the new object lands in the
        shared object DB (the `behind` case); from a SECOND clone it does not (the `unanswerable`
        case) — that difference is the whole point of the tri-state probe."""
        if from_clone is None:
            wt = self._mk_ambient(self.EPIC_BRANCH)
            self._commit_in(wt, "epic-moved.txt", "predecessor story landed")
            _git(["push", "origin", self.EPIC_BRANCH], wt)
            return
        _git(["fetch", "origin"], from_clone)
        _git(["checkout", "-B", self.EPIC_BRANCH, "origin/%s" % self.EPIC_BRANCH], from_clone)
        _write(Path(from_clone) / "epic-moved.txt", "landed elsewhere\n")
        _git(["add", "-A"], from_clone)
        _git(["commit", "-m", "predecessor story landed elsewhere"], from_clone)
        _git(["push", "origin", self.EPIC_BRANCH], from_clone)

    # --- the incident, and the ahead case -----------------------------------------------

    def test_story_branch_at_the_identical_epic_sha_grounds_on_itself(self):
        # The live incident verbatim: story worktree and epic worktree at the SAME sha. A commit is
        # its own ancestor, so containment holds and the row fires — where today's gate refused
        # with zero information gain.
        story = self._story_worktree_on_epic()
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic", ambient=story,
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_OWN_BRANCH
        )
        self.assertEqual(envelope["plan_ref"], story)
        self.assertEqual(envelope["grounding"]["ref"], story)
        self.assertEqual(envelope["grounding"]["branch"], story)
        self.assertEqual(len(envelope["grounding"]["sha"]), 40)
        self.assertTrue(envelope["story"]["ambient_contains_base_tip"])
        self.assertEqual(envelope["story"]["ambient_branch"], story)
        self.assertEqual(envelope["story"]["own_branch_base_ref"], self.EPIC_BRANCH)
        self.assertTrue(
            any("own branch" in item for item in envelope["attention"]), envelope["attention"]
        )
        # Routing is unaffected — the row names the grounding vantage, never the playbook.
        self.assertEqual(envelope["suggested_playbook"], "story-jit.md")

    def test_story_branch_ahead_of_the_epic_tip_grounds_on_its_own_head(self):
        # The normal mid-story posture: unpushed local work on top of the epic tip. The footer must
        # record THIS head, which is the reality the operator is planning against.
        story = self._story_worktree_on_epic()
        wt = self._mk_ambient(story)
        head = self._commit_in(wt, "story-wip.txt", "work in progress")
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic", ambient=story,
        )
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_OWN_BRANCH
        )
        self.assertEqual(envelope["grounding"]["sha"], head)

    def test_collision_suffixed_story_branch_still_belongs_to_the_issue(self):
        # `-vN` suffixing is how workspace-open resolves a branch-name collision; such a branch is
        # still this issue's, so a reopened workspace must not lose the row.
        story = self._story_worktree_on_epic(story_branch="202-returning-login-v2")
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic", ambient=story,
        )
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_OWN_BRANCH
        )
        self.assertEqual(envelope["plan_ref"], "202-returning-login-v2")

    def test_bootstrap_story_branch_containing_main_grounds_on_itself(self):
        # The pre-bootstrap half: the parent epic has no integration branch yet, so workspace-open
        # bases the first story on `main` and rows 5/6 choose `main` as the base. Same argument,
        # same row — the base it was measured against is what `own_branch_base_ref` records.
        self._push_branch("203-first-story")
        envelope = self._envelope(
            issue="203", fixture_case="prep_planner_row_story_parent_bootstrap",
            ambient="203-first-story",
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_OWN_BRANCH
        )
        self.assertEqual(envelope["plan_ref"], "203-first-story")
        self.assertEqual(envelope["story"]["own_branch_base_ref"], "main")

    # --- the two fall-through paths -----------------------------------------------------

    def test_story_branch_behind_the_epic_tip_refuses_exactly_as_today(self):
        # A predecessor story merged into the epic since this branch was cut. Grounding here would
        # plan against code the predecessor has since moved — the invariant the row must not break.
        story = self._story_worktree_on_epic()
        self._mk_ambient(story)
        self._advance_epic_branch()
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic", ambient=story,
        )
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "WORKSPACE_MISMATCH")
        self.assertEqual(envelope["decision"]["context"]["reason"], "branch_mismatch")
        self.assertEqual(envelope["decision"]["context"]["expected_branch"], self.EPIC_BRANCH)
        # The notice is the ONLY machine-readable channel that survives a needs_decision exit —
        # the mismatch card is built generically inside workspace.py and forwarded verbatim.
        self.assertIn(prep_planner.OWN_BRANCH_BEHIND_BASE_TIP, envelope["notices"])
        self.assertNotIn(prep_planner.BASE_TIP_CONTAINMENT_UNAVAILABLE, envelope["notices"])

    def test_unfetched_epic_tip_falls_through_with_the_unanswerable_notice(self):
        # `ls-remote` reports a tip this checkout has never fetched, so containment is unanswerable.
        # A prep does not fetch to find out — it refuses, and says which of the two causes applied.
        story = self._story_worktree_on_epic()
        self._mk_ambient(story)
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        self._advance_epic_branch(from_clone=other.path)
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic", ambient=story,
        )
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["context"]["reason"], "branch_mismatch")
        self.assertIn(prep_planner.BASE_TIP_CONTAINMENT_UNAVAILABLE, envelope["notices"])
        self.assertNotIn(prep_planner.OWN_BRANCH_BEHIND_BASE_TIP, envelope["notices"])

    # --- another story's checkout under the same epic ------------------------------------

    def test_sibling_story_ahead_of_the_epic_tip_is_a_card_naming_the_epic_worktree(self):
        # The content rule under a non-default plan_ref: the sibling contains the epic tip plus
        # its own unmerged commit, so planning #202 there would read code that is not on the epic.
        self._push_branch(self.EPIC_BRANCH)
        self._push_branch("203-sibling-story", base=self.EPIC_BRANCH)
        wt = self._mk_ambient("203-sibling-story")
        self._commit_in(wt, "sibling.txt", "sibling work")
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic",
            ambient="203-sibling-story",
        )
        self.assertEqual(envelope["status"], "needs_decision")
        context = envelope["decision"]["context"]
        self.assertEqual(context["reason"], "branch_mismatch")
        self.assertEqual(context["expected_branch"], self.EPIC_BRANCH)
        self.assertEqual(context["ahead_commits"], 1)
        self.assertEqual(Path(context["path"]).resolve(), wt.resolve())
        self.assertIn(self.EPIC_BRANCH, envelope["decision"]["options"][1])

    def test_sibling_story_accepted_with_ground_ambient(self):
        self._push_branch(self.EPIC_BRANCH)
        self._push_branch("203-sibling-story", base=self.EPIC_BRANCH)
        wt = self._mk_ambient("203-sibling-story")
        self._commit_in(wt, "sibling.txt", "sibling work")
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic",
            ambient="203-sibling-story", extra_args=["--ground-ambient"],
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_AMBIENT_ACCEPTED
        )
        self.assertEqual(envelope["plan_ref"], "203-sibling-story")
        self.assertEqual(envelope["grounding"]["base_ref"], self.EPIC_BRANCH)
        # The epic's own routing is untouched by the vantage.
        self.assertEqual(envelope["suggested_playbook"], "story-jit.md")

    # --- the probe must not fire where it has no business firing -------------------------

    def test_project_root_never_probes_and_still_refuses(self):
        # The companion to test_non_main_plan_ref_from_the_project_root_is_a_mismatch: `main` is
        # not this issue's branch, so neither own-branch notice can appear. The epic branch is
        # advanced first: at the identical sha the root would pass (`identical`, content-equal).
        self._push_branch(self.EPIC_BRANCH)
        self._advance_epic_branch()
        envelope = self._envelope(issue="202", fixture_case="prep_planner_row_story_under_epic")
        self.assertEqual(envelope["decision"]["context"]["reason"], "at_project_root")
        self.assertNotIn(prep_planner.OWN_BRANCH_BEHIND_BASE_TIP, envelope["notices"])
        self.assertNotIn(prep_planner.BASE_TIP_CONTAINMENT_UNAVAILABLE, envelope["notices"])

    def test_sitting_in_the_epic_worktree_is_unchanged(self):
        # #31's shape and the pre-existing happy path: the epic branch is not the STORY's branch,
        # so the probe is suppressed and row 5 answers exactly as before.
        self._push_branch(self.EPIC_BRANCH)
        envelope = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic", ambient=self.EPIC_BRANCH,
        )
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_PARENT_BRANCH
        )
        self.assertIsNone(envelope["story"]["ambient_contains_base_tip"])
        self.assertIsNone(envelope["story"]["own_branch_base_ref"])

    def test_refresh_reports_the_same_row_as_a_full_run(self):
        # --refresh skips the grounding assertion but still emits plan_ref. If the row applied on
        # the full run and not here, plan_ref would flip mid-session and the rendered footer would
        # go dishonest — the exact failure this change exists to prevent. The probe is local git
        # only, so it costs refresh nothing.
        story = self._story_worktree_on_epic()
        full = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic", ambient=story,
        )
        refreshed = self._envelope(
            issue="202", fixture_case="prep_planner_row_story_under_epic", ambient=story,
            extra_args=["--refresh"],
        )
        self.assertEqual(
            refreshed["vector"]["plan_ref_row"], full["vector"]["plan_ref_row"],
        )
        self.assertEqual(refreshed["plan_ref"], story)
        self.assertNotIn("grounding", refreshed)


class OwnBranchDefaultRowTests(PrepPlannerSandboxTestCase):
    """Rows `own-branch` (standalone issue) and `story-own-branch` over `story-no-open-parent-epic`:
    the 4.33.0 widening. Before it, a standalone issue's own worktree was refused for a `main`
    plan_ref it already contained — the attach gate's branch-name check standing in for the content
    check it never performed (seen live on #1275, "main plus 13 docs commits")."""

    OWN = "200-fix-the-gadget"

    def test_own_branch_with_unpushed_work_grounds_on_itself(self):
        self._push_branch(self.OWN)
        wt = self._mk_ambient(self.OWN)
        head = self._commit_in(wt, "gadget.txt", "fixing the gadget")
        envelope = self._envelope(fixture_case="prep_planner_row_default_own_branch", ambient=self.OWN)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_OWN_BRANCH)
        self.assertEqual(envelope["plan_ref"], self.OWN)
        self.assertEqual(envelope["grounding"]["basis"], "own_branch")
        self.assertEqual(envelope["grounding"]["base_ref"], "main")
        self.assertEqual(envelope["grounding"]["sha"], head)
        self.assertEqual(envelope["notices"], [])
        self.assertTrue(
            any("this issue's own branch" in item for item in envelope["attention"]),
            envelope["attention"],
        )
        self.assertNotIn("story", envelope)
        self.assertEqual(envelope["suggested_playbook"], "single.md")

    def test_own_branch_behind_main_refuses_with_the_rebase_notice(self):
        self._push_branch(self.OWN)
        self._mk_ambient(self.OWN)
        # Move main forward from a worktree of the sandbox clone, so the new tip is in the shared
        # object DB: genuinely `behind`, not `unanswerable`.
        mover = self._mk_ambient("mover")
        self._commit_in(mover, "main-moved.txt", "main moved on")
        _git(["push", "origin", "HEAD:main"], mover)
        envelope = self._envelope(fixture_case="prep_planner_row_default", ambient=self.OWN)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["context"]["reason"], "branch_mismatch")
        self.assertEqual(envelope["decision"]["context"]["expected_branch"], "main")
        self.assertIn(prep_planner.OWN_BRANCH_BEHIND_BASE_TIP, envelope["notices"])

    def test_own_branch_heading_a_closed_pr_re_grounds_in_place_and_says_so(self):
        # After a Start-fresh the closed PR's branch is still the issue's own: the operator chose
        # re-grounding in place, so the row stands and an attention line names the PR.
        self._push_branch(self.OWN)
        envelope = self._envelope(
            fixture_case="prep_planner_row_default_closed_pr", ambient=self.OWN
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_OWN_BRANCH)
        self.assertTrue(
            any("closed PR #210" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_closed_pr_lookup_failure_is_a_notice_never_a_stop(self):
        # The canonical default case has no closed-PR entry, so the shim's un-fixtured miss stands
        # in for any lookup failure.
        self._push_branch(self.OWN)
        envelope = self._envelope(fixture_case="prep_planner_row_default", ambient=self.OWN)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_OWN_BRANCH)
        self.assertIn(prep_planner.CLOSED_PR_LOOKUP_UNAVAILABLE, envelope["notices"])

    def test_refresh_skips_the_closed_pr_lookup(self):
        # The default case has no closed-PR entry: a lookup on refresh would surface as the
        # unavailable notice. Refresh keeps the row (the probe still runs) and pays no gh call.
        self._push_branch(self.OWN)
        envelope = self._envelope(
            fixture_case="prep_planner_row_default", ambient=self.OWN, extra_args=["--refresh"]
        )
        self.assertEqual(envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_OWN_BRANCH)
        self.assertNotIn(prep_planner.CLOSED_PR_LOOKUP_UNAVAILABLE, envelope["notices"])

    def test_story_with_no_parent_grounds_on_its_own_branch_without_claiming_main(self):
        # Row 7 was deliberately left out of 4.12.0 because its "grounding at main" attention line
        # would contradict a firing own-branch row; the widening reconciles the two.
        self._push_branch("204-orphaned-slice")
        envelope = self._envelope(
            issue="204", fixture_case="prep_planner_row_story_no_parent",
            ambient="204-orphaned-slice",
        )
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_OWN_BRANCH
        )
        self.assertEqual(envelope["story"]["own_branch_base_ref"], "main")
        self.assertIn("no parent epic found referencing this story", envelope["attention"])
        self.assertFalse(
            any("grounding at main" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_an_epic_that_only_mentions_the_story_is_not_its_parent(self):
        # The legacy tier's candidate search returns an OPEN epic whose body says "Unlike #204" and
        # whose `## Stories` lists #205 only. Before 4.33.1 that made it #204's parent — grounding on
        # its branch and routing to story-jit.md. The fixture deliberately answers none of a parent
        # epic's follow-up calls, so a regression is a shim miss, not a quiet wrong row.
        envelope = self._envelope(issue="204", fixture_case="prep_planner_story_epic_mention_only")
        self.assertEqual(envelope["status"], "ok")
        self.assertIsNone(envelope["story"]["parent_epic"])
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_NO_PARENT
        )
        self.assertEqual(envelope["suggested_playbook"], "single.md")

    def test_story_with_no_parent_from_main_still_says_grounding_at_main(self):
        envelope = self._envelope(issue="204", fixture_case="prep_planner_row_story_no_parent")
        self.assertEqual(
            envelope["vector"]["plan_ref_row"], prep_planner.PLAN_REF_ROW_STORY_NO_PARENT
        )
        self.assertIn(
            "no parent epic found referencing this story — grounding at main", envelope["attention"]
        )


class RefreshModeTests(PrepPlannerSandboxTestCase):
    """--refresh re-derives volatile facts without re-running root freshness or re-ensuring the
    grounding read workspace (mirrors tests/test_prep_resolver.py's / tests/test_prep_evaluator.py's
    identical `--refresh` contract; architecture.md §4)."""

    def test_refresh_never_touches_the_grounding_assertion(self):
        envelope = self._envelope(fixture_case="prep_planner_row_default", extra_args=["--refresh"])
        self.assertEqual(envelope["status"], "ok")
        self.assertNotIn("grounding", envelope)
        self.assertNotIn("read_workspaces", envelope)
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertFalse(envelope["root"]["fresh"])
        self.assertFalse((self.root / ".worktrees").exists())

    def test_refresh_still_reports_vector_and_oq_candidates(self):
        envelope = self._envelope(issue="400", fixture_case="prep_planner_oq_tracker_match", extra_args=["--refresh"])
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(len(envelope["open_question_candidates"]), 1)


class SingleInvocationBudgetTests(PrepPlannerSandboxTestCase):
    """Single-invocation budget: the canonical (standard/fresh/no-OQ/no-epic/no-PR) path's shim
    call count is bounded and stable, both an upper AND a lower bound (S12 DoD's fourth box:
    "conformance + call budget as S6")."""

    def test_shim_call_count_for_canonical_run_is_exactly_three(self):
        # gh calls on the canonical path: issue view (+deps), paginated comments, open-PR search —
        # three, no more, no fewer. No epic/story branch discovery (git, not gh, and only for
        # epic/story types), no OQ tracker search (no `## Open questions` section), no PR gather
        # (fresh mode, no open PR).
        result = self._run(
            ["200", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_row_default",
            cwd=str(self.root),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        manifest_path = shimenv.fixture_case_dir("prep_planner_row_default") / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 3)

    def test_no_gh_call_is_repeated_within_one_run(self):
        manifest_path = shimenv.fixture_case_dir("prep_planner_row_default") / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        argv_tuples = [tuple(entry["argv"]) for entry in manifest]
        self.assertEqual(len(argv_tuples), len(set(argv_tuples)), "duplicate argv in manifest")

    def test_revise_with_open_pr_costs_strictly_more_calls_than_canonical(self):
        # Upper-bound half of the two-sided budget: revise mode with an open PR adds the
        # gh_pr_gather `gh pr view` call on top of the canonical three.
        _git(["fetch", "origin"], self.root)
        _git(["checkout", "-b", "201-fix-gadget"], self.root)
        _write(self.root / "gadget.txt", "fixed\n")
        _git(["add", "gadget.txt"], self.root)
        _git(["commit", "-m", "fix gadget"], self.root)
        _git(["push", "origin", "201-fix-gadget"], self.root)
        _git(["checkout", "main"], self.root)

        result = self._run(
            ["201", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_planner_row_open_pr_revise",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        manifest_path = shimenv.fixture_case_dir("prep_planner_row_open_pr_revise") / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 4)
        self.assertGreater(len(manifest), 3)


class DeliverableSubIssueFactsTests(PrepPlannerSandboxTestCase):
    """#18: `facts.slices` — the live deliverable-sub-issue set and the plan-versus-live diff,
    computed IN-SCRIPT so no prompt re-derives it. A non-epic target's sub-issues are its slices by
    construction, so the only gate is the target's type plus a non-empty node list.
    """

    def _slices(self, fixture_case):
        envelope = self._envelope(issue="250", fixture_case=fixture_case)
        self.assertIn("slices", envelope, "expected facts.slices on a non-epic target with children")
        return envelope, envelope["slices"]

    def test_set_carries_panel_order_state_and_staged_bodies(self):
        envelope, slices = self._slices("prep_planner_slices_fresh")
        self.assertTrue(slices["detail_available"])
        self.assertEqual(slices["source"], "sub_issues_rest")
        self.assertEqual([e["number"] for e in slices["set"]], [251, 252])
        # `position` is the sub-issue panel's own order — the sequencing source of truth.
        self.assertEqual([e["position"] for e in slices["set"]], [0, 1])
        self.assertEqual([e["state"] for e in slices["set"]], ["OPEN", "OPEN"])
        self.assertEqual(slices["open_count"], 2)
        for entry in slices["set"]:
            # force_path: uniform paths keep envelope size independent of child count.
            self.assertEqual(entry["body_mode"], "path")
            self.assertTrue(Path(entry["body_path"]).is_file())

    def test_fresh_path_has_no_diff_but_still_reports_the_set(self):
        # A finding *about a phase map* cannot exist before a plan does; the coverage obligation on
        # the fresh path is carried by the playbook and reviewer Dimension 7, not by this diff.
        _envelope, slices = self._slices("prep_planner_slices_fresh")
        self.assertFalse(slices["diff"]["computed"])
        self.assertEqual(slices["diff"]["uncovered_open"], [])
        # `unavailable` would claim THIS HOST cannot answer, which is false here — there is simply
        # no plan to compare against yet.
        self.assertEqual(slices["rescope_basis"], "no_prior_plan")

    def test_uncovered_open_sub_issue_is_surfaced_in_attention(self):
        envelope, slices = self._slices("prep_planner_slices_added")
        diff = slices["diff"]
        self.assertTrue(diff["computed"])
        self.assertEqual(diff["uncovered_open"], [252])
        self.assertEqual(diff["mapped"], [{"phase": 2, "sub_issue": 251}])
        self.assertEqual(diff["substrate_phases"], [1])
        self.assertTrue(
            any("#252" in line and "no phase" in line for line in envelope["attention"]),
            envelope["attention"],
        )

    def test_uncovered_open_is_the_only_key_for_that_fact(self):
        # A genuinely newly-ADDED sub-issue is indistinguishable from one the plan never mapped (no
        # prior snapshot of the set exists), so one fact gets exactly one key — two would invite a
        # consumer to double-report.
        _envelope, slices = self._slices("prep_planner_slices_added")
        self.assertNotIn("added", slices["diff"])

    def test_closed_sub_issue_points_at_the_shipped_phase_rules(self):
        envelope, slices = self._slices("prep_planner_slices_closed")
        self.assertEqual(slices["diff"]["closed"], [251])
        self.assertEqual(slices["diff"]["uncovered_open"], [])
        self.assertTrue(
            any("revise-reconciliation.md" in line for line in envelope["attention"]),
            envelope["attention"],
        )

    def test_a_closed_sub_issue_does_not_also_read_as_rescoped(self):
        # Closing an issue bumps its `updated_at`, so without the CLOSED exclusion every closure
        # would ALSO land in `rescoped` — and since `rescoped` gates while `closed` does not, the
        # non-gating case would be unreachable in production. The fixture's #251 is closed with a
        # POST-plan timestamp precisely to exercise that.
        envelope, slices = self._slices("prep_planner_slices_closed")
        by_number = {e["number"]: e for e in slices["set"]}
        self.assertTrue(by_number[251]["maybe_rescoped"], "fixture must have a post-plan timestamp")
        self.assertEqual(slices["diff"]["rescoped"], [])
        self.assertFalse(
            any("may have changed" in line for line in envelope["attention"]),
            envelope["attention"],
        )

    def test_removed_sub_issue_is_reported(self):
        envelope, slices = self._slices("prep_planner_slices_removed")
        self.assertEqual(slices["diff"]["removed"], [299])
        self.assertTrue(
            any("#299" in line for line in envelope["attention"]), envelope["attention"]
        )

    def test_rescoped_is_reported_as_a_suspicion_not_proof(self):
        envelope, slices = self._slices("prep_planner_slices_rescoped")
        self.assertEqual(slices["rescope_basis"], "updated_at")
        self.assertEqual(slices["diff"]["rescoped"], [252])
        by_number = {e["number"]: e for e in slices["set"]}
        self.assertTrue(by_number[252]["maybe_rescoped"])
        self.assertFalse(by_number[251]["maybe_rescoped"])
        line = next(line for line in envelope["attention"] if "#252 may have changed" in line)
        self.assertIn("not proof", line)
        self.assertIn("slice-252-body.md", line)

    def test_order_changed_is_surfaced_not_corrected(self):
        envelope, slices = self._slices("prep_planner_slices_order_changed")
        self.assertEqual(
            slices["diff"]["order_changed"],
            [
                {
                    "phase": 3,
                    "depends_on_phase": 2,
                    "sub_issue": 251,
                    "after_sub_issue": 252,
                    "live_order": [251, 252],
                }
            ],
        )
        self.assertTrue(
            any("not corrected" in line for line in envelope["attention"]), envelope["attention"]
        )

    def test_pre_contract_plan_reports_unmapped_phases(self):
        # A plan authored before the key existed: phases parse, every `sub_issue` is None (unmapped
        # — NOT `(none)`, which is an explicit substrate claim), and every open sub-issue is
        # therefore uncovered.
        _envelope, slices = self._slices("prep_planner_slices_pre_contract_plan")
        self.assertEqual(slices["diff"]["unmapped_phases"], [1, 2])
        self.assertEqual(slices["diff"]["substrate_phases"], [])
        self.assertEqual(slices["diff"]["uncovered_open"], [251, 252])

    def test_slice_fetch_actually_ran(self):
        # Non-tautological companion to the manifest budget check below: these two facts can only be
        # true if the REST call was made and its payload consumed.
        _envelope, slices = self._slices("prep_planner_slices_fresh")
        self.assertEqual(slices["source"], "sub_issues_rest")
        self.assertTrue(all(Path(e["body_path"]).is_file() for e in slices["set"]))

    def test_malformed_prior_phases_is_best_effort_not_a_decision(self):
        # A revise run exists to REPAIR a bad plan: hard-failing here would mean the only tool that
        # can rewrite the section refuses to start because the section is broken.
        envelope, slices = self._slices("prep_planner_slices_prior_plan_malformed")
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(slices["diff"]["prior_phases_parsed"])
        self.assertFalse(slices["diff"]["computed"])
        self.assertIsNotNone(slices["diff"]["prior_phases_error"])
        self.assertTrue(
            any("does not parse" in line for line in envelope["attention"]), envelope["attention"]
        )

    def test_plan_updated_at_rides_in_the_plan_facts(self):
        envelope = self._envelope(issue="250", fixture_case="prep_planner_slices_rescoped")
        self.assertEqual(envelope["plan"]["updated_at"], "2026-03-01T00:00:00Z")

    def test_a_plan_posted_before_parts_reports_none(self):
        # skills/_shared/plan-parts.md: a legacy single-comment plan has no parts, so its revise posts
        # them fresh rather than editing ids that do not exist.
        envelope = self._envelope(issue="250", fixture_case="prep_planner_slices_rescoped")
        self.assertEqual(envelope["plan"]["parts"], [])

    def test_the_live_plan_size_is_no_longer_a_prep_fact(self):
        # The cap is per comment since 4.35.0 and a revise re-cuts the plan, so the whole live plan's
        # size predicts nothing; the size fact is `plan_parts.py split`'s, at staging.
        plan = self._envelope(issue="250", fixture_case="prep_planner_slices_rescoped")["plan"]
        for key in ("body_chars", "body_bytes", "body_limit_chars"):
            self.assertNotIn(key, plan)

    def test_parts_are_reported_even_with_no_plan(self):
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertFalse(envelope["plan"]["present"])
        self.assertEqual(envelope["plan"]["parts"], [])

    def test_childless_target_has_no_slices_key_and_no_extra_gh_call(self):
        envelope = self._envelope(issue="200", fixture_case="prep_planner_row_default")
        self.assertNotIn("slices", envelope)
        manifest = json.loads(
            (shimenv.fixture_case_dir("prep_planner_row_default") / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(manifest), 3)

    def test_epic_target_has_no_slices_key(self):
        # An epic's sub-issues are STORIES, not slices — they reach the planner as
        # `facts.epic.stories`, and an epic plan carries no `## Phases` at all.
        envelope = self._envelope(issue="300", fixture_case="prep_planner_row_epic_native")
        self.assertNotIn("slices", envelope)

    def test_slice_fetch_costs_exactly_one_extra_gh_call(self):
        manifest = json.loads(
            (shimenv.fixture_case_dir("prep_planner_slices_fresh") / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        # One paginated REST call for the WHOLE set — not one per child, which would make the
        # budget a function of the child count.
        self.assertEqual(len(manifest), 4)
        self.assertEqual(
            manifest[-1]["argv"],
            ["api", "--paginate", "repos/octo/widgets/issues/250/sub_issues"],
        )


class SubIssueDetailUnsupportedTests(unittest.TestCase):
    """The capability degradation. Driven with `process.run` stubbed rather than a fixture, because
    the endpoint's unavailability is a *stderr* signal the fixture shim cannot produce (the same
    reason gh_gather's own relation-ladder tests mock instead of fixturing)."""

    def _stub(self, stderr, returncode=1):
        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            return process.CommandResult(returncode=returncode, stdout="", stderr=stderr)

        return fake_run

    def test_404_degrades_to_the_node_data_with_a_notice(self):
        with mock.patch.object(
            prep_planner.process, "run", side_effect=self._stub("gh: Not Found (HTTP 404)\n")
        ):
            objects, notices, decision = prep_planner._fetch_sub_issue_details("o/r", "250")
        self.assertIsNone(objects)
        self.assertIsNone(decision)
        self.assertEqual(notices, ["SUBISSUE_DETAIL_UNSUPPORTED"])

    def test_auth_failure_is_a_decision_not_a_degradation(self):
        result = process.CommandResult(
            returncode=4, stdout="", stderr="gh auth login required", auth_required=True
        )
        with mock.patch.object(prep_planner.process, "run", return_value=result):
            objects, notices, decision = prep_planner._fetch_sub_issue_details("o/r", "250")
        self.assertIsNone(objects)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["code"], "AUTH_REQUIRED")

    def test_a_non_capability_failure_is_still_a_hard_failure(self):
        # A 500 or a network error must NOT silently degrade to the node data — only the
        # endpoint-unavailable shapes do.
        with mock.patch.object(
            prep_planner.process, "run", side_effect=self._stub("gh: Server Error (HTTP 500)\n")
        ):
            with self.assertRaises(SystemExit) as ctx:
                prep_planner._fetch_sub_issue_details("o/r", "250")
        self.assertEqual(ctx.exception.code, 1)

    def _entry(self, number, state="OPEN", updated=None, position=0):
        return {
            "number": number,
            "title": "s%d" % number,
            "state": state,
            "position": position,
            "url": "u",
            "updated_at": updated,
            "maybe_rescoped": False,
        }

    def test_a_plan_with_no_phases_section_is_not_reported_as_uncovering_everything(self):
        # `parse_phases` returns `[]` for a plan that legitimately omits `## Phases` (a single-phase
        # plan). Reporting every open sub-issue as "unserved" against a plan that has no phases would
        # gate on a shape reviewer Dimension 7 is never even asked to check.
        diff = prep_planner._build_slice_diff([self._entry(252)], [], True, "updated_at")
        self.assertFalse(diff["computed"])
        self.assertFalse(diff["prior_phases_present"])
        self.assertEqual(diff["uncovered_open"], [])

    def test_a_plan_with_phases_reports_prior_phases_present(self):
        phases = [
            {"number": 1, "sub_issue": 252, "depends_on": "(none)"},
        ]
        diff = prep_planner._build_slice_diff([self._entry(252)], phases, True, "updated_at")
        self.assertTrue(diff["computed"])
        self.assertTrue(diff["prior_phases_present"])

    def test_an_unknown_state_fails_safe_into_the_coverage_set(self):
        # Better to over-report an uncovered sub-issue than to silently drop one from the set the
        # phase map has to cover.
        diff = prep_planner._build_slice_diff(
            [self._entry(252, state="")],
            [{"number": 1, "sub_issue": "(none)", "depends_on": "(none)"}],
            True,
            "updated_at",
        )
        self.assertEqual(diff["uncovered_open"], [252])

    def test_order_changed_reports_one_row_per_pair_not_per_edge(self):
        # Two phases both serving #252, and a third serving #251 depending on both: one panel-order
        # disagreement, so one row.
        phases = [
            {"number": 1, "sub_issue": 252, "depends_on": "(none)"},
            {"number": 2, "sub_issue": 252, "depends_on": [1]},
            {"number": 3, "sub_issue": 251, "depends_on": [1, 2]},
        ]
        slice_set = [self._entry(251, position=0), self._entry(252, position=1)]
        diff = prep_planner._build_slice_diff(slice_set, phases, True, "updated_at")
        self.assertEqual(len(diff["order_changed"]), 1)
        self.assertEqual(diff["order_changed"][0]["live_order"], [251, 252])

    def test_position_is_compacted_over_kept_entries(self):
        # A node with no number cannot appear in a phase map; consuming its index would leave a hole
        # in the very ordering `order_changed` compares against.
        nodes = [
            {"id": "I_x", "number": None, "state": "OPEN", "title": "junk", "url": "u"},
            {"id": "I_1", "number": 251, "state": "OPEN", "title": "Writer", "url": "u251"},
        ]
        entries = prep_planner._build_slice_set(nodes, None, None, "/tmp")
        self.assertEqual([e["number"] for e in entries], [251])
        self.assertEqual(entries[0]["position"], 0)

    def _one(self, updated_at, plan_updated_at):
        nodes = [{"id": "I_1", "number": 251, "state": "OPEN", "title": "W", "url": "u"}]
        details = [
            {
                "number": 251,
                "state": "open",
                "title": "W",
                "updated_at": updated_at,
                "body": "b",
            }
        ]
        return prep_planner._build_slice_set(nodes, details, plan_updated_at, "/tmp")[0]

    def test_a_non_utc_offset_does_not_produce_a_spurious_rescope(self):
        # `2026-03-01T01:00:00+02:00` is 2026-02-28T23:00:00Z — an hour BEFORE the plan comment. A raw
        # string comparison reads it as later (the digits diverge before the offset is ever reached)
        # and would gate the operator on an edit that predates the plan.
        plan = "2026-03-01T00:00:00Z"
        before_in_offset_form = "2026-03-01T01:00:00+02:00"
        self.assertGreater(before_in_offset_form, plan)  # the raw-string trap
        self.assertLess(
            prep_planner._parse_iso8601(before_in_offset_form),
            prep_planner._parse_iso8601(plan),
        )
        self.assertFalse(self._one(before_in_offset_form, plan)["maybe_rescoped"])

    def test_a_genuine_post_plan_edit_in_offset_form_is_still_detected(self):
        entry = self._one("2026-03-05T00:00:00+00:00", "2026-03-01T00:00:00Z")
        self.assertTrue(entry["maybe_rescoped"])

    def test_unparseable_timestamp_is_not_a_crash_and_not_a_rescope(self):
        self.assertIsNone(prep_planner._parse_iso8601("not a date"))
        self.assertIsNone(prep_planner._parse_iso8601(None))
        self.assertFalse(self._one("not a date", "2026-03-01T00:00:00Z")["maybe_rescoped"])

    def test_node_fallback_set_has_no_timestamps_and_no_bodies(self):
        nodes = [
            {"id": "I_1", "number": 251, "state": "OPEN", "title": "Writer", "url": "u251"},
        ]
        entries = prep_planner._build_slice_set(nodes, None, "2026-03-01T00:00:00Z", "/tmp")
        self.assertEqual(entries[0]["number"], 251)
        self.assertIsNone(entries[0]["updated_at"])
        self.assertFalse(entries[0]["maybe_rescoped"])
        self.assertNotIn("body_mode", entries[0])


class ComposedCoreNoticesTests(PrepPlannerSandboxTestCase):
    """#18: a composed core's non-blocking degradations must reach prep's OWN envelope. Before the
    fix, `facts["notices"]` was hardwired `[]` and every core's notices were dropped on the ok path
    — so on a host that doesn't serve the native parent/sub-issue relation, `SUBISSUES_UNSUPPORTED`
    never surfaced and an empty sub-issue set read as "no children" rather than "unavailable",
    silently skipping the sub-issue reconciliation.

    Driven in-process with `gh_gather.run` stubbed, because the notice originates inside
    gh_gather's capability ladder off a *stderr* signal the fixture shim cannot produce.
    """

    def _stub_envelope(self, notices):
        return {
            "status": "ok",
            "notices": list(notices),
            "number": 200,
            "title": "Fix the gadget",
            "state": "OPEN",
            "labels": [],
            "issue_body": "Small body.\n\n## Definition of done\n- [ ] Gadget fixed\n",
            "issue_body_mode": "inline",
            "thread": "[]",
            "thread_mode": "inline",
            "marker_comment_present": False,
            "marker_comment_count": 0,
            "deps_available": True,
            "blocked_by": [],
            "blocking": [],
            "subissues_available": False,
            "parent": None,
            "sub_issues": [],
            "sub_issues_summary": {},
            "open_prs": [],
        }

    def _facts_with_notices(self, notices):
        with mock.patch.object(
            prep_planner.gh_gather, "run", return_value=(0, self._stub_envelope(notices))
        ):
            # --refresh skips the ambient-grounding assert and the origin/main pin, so this exercises
            # the notice plumbing without needing a worktree posture.
            return prep_planner.build_facts(
                "200", "octo/widgets", root=str(self.root), scratch_dir=self.scratch, refresh=True
            )

    def test_gather_notice_reaches_the_facts_block(self):
        facts = self._facts_with_notices(["SUBISSUES_UNSUPPORTED"])
        self.assertEqual(facts["notices"], ["SUBISSUES_UNSUPPORTED"])

    def test_multiple_gather_notices_are_preserved_in_order(self):
        facts = self._facts_with_notices(["DEPS_UNSUPPORTED", "SUBISSUES_UNSUPPORTED"])
        self.assertEqual(facts["notices"], ["DEPS_UNSUPPORTED", "SUBISSUES_UNSUPPORTED"])

    def test_clean_run_still_reports_an_empty_notices_list(self):
        facts = self._facts_with_notices([])
        self.assertEqual(facts["notices"], [])

    def test_notices_survive_a_needs_decision_exit(self):
        # The `ok` path is only half the contract: a degradation reported by an EARLY core must still
        # reach the operator when a LATER step returns a decision. Otherwise the same silent-skip the
        # fix exists to prevent returns through the decision door.
        envelope = self._stub_envelope(["SUBISSUES_UNSUPPORTED"])
        envelope["status"] = "needs_decision"
        envelope["decision"] = {
            "code": "MARKER_AMBIGUOUS",
            "summary": "two plan markers",
            "context": {},
            "options": ["delete one"],
        }
        buffer = io.StringIO()
        with mock.patch.object(prep_planner.gh_gather, "run", return_value=(0, envelope)):
            with contextlib.redirect_stdout(buffer):
                facts = prep_planner.build_facts(
                    "200", "octo/widgets", root=str(self.root), scratch_dir=self.scratch, refresh=True
                )
        self.assertIsNone(facts, "a forwarded decision returns None")
        emitted = json.loads(buffer.getvalue().strip())
        self.assertEqual(emitted["status"], "needs_decision")
        self.assertEqual(emitted["notices"], ["SUBISSUES_UNSUPPORTED"])


class DodFactsTests(PrepPlannerSandboxTestCase):
    """`facts.dod`: the issue DoD's shape + the staged working copy of the body. Before it, the
    planner indexed `closes-dod` by eye against whatever checklist the body had — including
    `## Acceptance criteria`, which no DoD reader parses — and nothing downstream could tell."""

    def _facts_for_body(self, body):
        # The same stubbed gather envelope ComposedCoreNoticesTests drives (not inherited, so its
        # tests don't run twice).
        envelope = ComposedCoreNoticesTests._stub_envelope(self, [])
        envelope["issue_body"] = body
        with mock.patch.object(prep_planner.gh_gather, "run", return_value=(0, envelope)):
            return prep_planner.build_facts(
                "200", "octo/widgets", root=str(self.root), scratch_dir=self.scratch, refresh=True
            )

    def test_a_dod_body_reports_its_shape_and_no_attention(self):
        facts = self._facts_for_body("Small body.\n\n## Definition of done\n- [ ] a\n- [ ] b\n")
        self.assertTrue(facts["dod"]["present"])
        self.assertEqual(facts["dod"]["count"], 2)
        self.assertEqual(facts["dod"]["other_checklists"], [])
        self.assertFalse(any("Definition of done" in a for a in facts["attention"]))

    def test_body_path_holds_the_exact_body(self):
        # The working copy every issue-body edit this session starts from (S1 rename, S8 pointer).
        body = "Body with CRLF\r\n\r\n## Definition of done\r\n- [ ] a\r\n"
        facts = self._facts_for_body(body)
        with open(facts["dod"]["body_path"], encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), body)
        self.assertTrue(facts["dod"]["body_path"].startswith(self.scratch))

    def test_acceptance_criteria_body_raises_the_card_attention(self):
        facts = self._facts_for_body(
            "## User story\nx\n\n## Acceptance criteria\n- [ ] a\n- [ ] b\n"
        )
        self.assertFalse(facts["dod"]["present"])
        self.assertEqual(
            facts["dod"]["other_checklists"], [{"heading": "Acceptance criteria", "count": 2}]
        )
        self.assertTrue(
            any("`## Acceptance criteria` (2 bullets)" in a and "DoD-heading card" in a
                for a in facts["attention"]),
            facts["attention"],
        )

    def test_a_legacy_epic_stories_checklist_raises_nothing(self):
        # 12 planner fixtures carry `## Goal` + `## Stories` and no DoD; none may gain a line.
        facts = self._facts_for_body("## Goal\nx\n\n## Stories\n- [ ] #12 — one\n")
        self.assertEqual(facts["dod"]["other_checklists"], [])
        self.assertFalse(any("Definition of done" in a for a in facts["attention"]))

    def test_a_malformed_annotation_never_stops_the_planner(self):
        # Lenient like the prior plan's `## Phases` parse: a revise is how annotations get repaired.
        facts = self._facts_for_body(
            "## Definition of done\n- [x] a (closed by phase one, commit zzz)\n"
        )
        self.assertIsNotNone(facts)
        self.assertEqual(facts["dod"]["count"], 1)


class PureHelperUnitTests(unittest.TestCase):
    """Direct, in-process tests of prep_planner's pure classification/derivation helpers — no
    subprocess, no shim, no git sandbox needed (mirrors test_prep_resolver.py's
    PureHelperUnitTests style)."""

    def test_merge_notices_dedupes_preserving_first_seen_order(self):
        acc = []
        prep_planner._merge_notices(acc, ["DEPS_UNSUPPORTED", "SUBISSUES_UNSUPPORTED"])
        # The same code arrives twice when both gh_gather calls degrade identically (target + parent
        # epic) — it must appear once.
        prep_planner._merge_notices(acc, ["SUBISSUES_UNSUPPORTED"])
        self.assertEqual(acc, ["DEPS_UNSUPPORTED", "SUBISSUES_UNSUPPORTED"])

    def test_merge_notices_tolerates_none(self):
        acc = ["A"]
        self.assertEqual(prep_planner._merge_notices(acc, None), ["A"])

    def test_detect_type_epic_by_label(self):
        self.assertEqual(prep_planner._detect_type(["epic"], "Sandbox fixture"), "epic")

    def test_detect_type_epic_by_title_prefix_case_insensitive(self):
        self.assertEqual(prep_planner._detect_type([], "EPIC: sandbox fixture"), "epic")

    def test_detect_type_story_by_label(self):
        self.assertEqual(prep_planner._detect_type(["story"], "Story A"), "story")

    def test_detect_type_standard_default(self):
        self.assertEqual(prep_planner._detect_type(["bug"], "Fix the thing"), "standard")

    def test_select_plan_ref_default_row(self):
        self.assertEqual(
            prep_planner._select_plan_ref("standard", None, None, "main"),
            ("main", prep_planner.PLAN_REF_ROW_DEFAULT),
        )

    def test_select_plan_ref_epic_branch_row(self):
        self.assertEqual(
            prep_planner._select_plan_ref("epic", "epic/1-x", None, "main"),
            ("epic/1-x", prep_planner.PLAN_REF_ROW_EPIC_BRANCH),
        )

    def test_select_plan_ref_epic_bootstrap_row(self):
        self.assertEqual(
            prep_planner._select_plan_ref("epic", None, None, "main"),
            ("main", prep_planner.PLAN_REF_ROW_EPIC_BOOTSTRAP),
        )

    def test_select_plan_ref_story_parent_branch_row(self):
        self.assertEqual(
            prep_planner._select_plan_ref("story", "epic/1-x", None, "main"),
            ("epic/1-x", prep_planner.PLAN_REF_ROW_STORY_PARENT_BRANCH),
        )

    def test_select_plan_ref_story_no_parent_row(self):
        # parent_epic_open defaults False -> the genuinely-parentless/closed-parent row.
        self.assertEqual(
            prep_planner._select_plan_ref("story", None, None, "main"),
            ("main", prep_planner.PLAN_REF_ROW_STORY_NO_PARENT),
        )
        self.assertEqual(
            prep_planner._select_plan_ref("story", None, None, "main", parent_epic_open=False),
            ("main", prep_planner.PLAN_REF_ROW_STORY_NO_PARENT),
        )

    def test_select_plan_ref_story_parent_bootstrap_row(self):
        # D4 fix: an OPEN parent whose integration branch hasn't bootstrapped yet gets its own
        # truthful row -- same plan_ref (main) as STORY_NO_PARENT, but a label that doesn't
        # contradict a simultaneously-true `story.parent_epic_open` fact.
        self.assertEqual(
            prep_planner._select_plan_ref("story", None, None, "main", parent_epic_open=True),
            ("main", prep_planner.PLAN_REF_ROW_STORY_PARENT_BOOTSTRAP),
        )

    def test_select_plan_ref_story_parent_open_but_no_branch_never_yields_no_parent_row(self):
        # Direct regression guard for D4: parent_epic_open=True must NEVER produce the
        # "no-open-parent-epic" row label, regardless of branch presence.
        plan_ref, row = prep_planner._select_plan_ref("story", None, None, "main", parent_epic_open=True)
        self.assertNotEqual(row, prep_planner.PLAN_REF_ROW_STORY_NO_PARENT)
        self.assertEqual(row, prep_planner.PLAN_REF_ROW_STORY_PARENT_BOOTSTRAP)
        self.assertEqual(plan_ref, "main")

    def test_select_plan_ref_open_pr_head_wins_over_epic_branch(self):
        # docs/specs/planner.md Step 4.5: "the open-PR-head row wins" when more than one applies.
        self.assertEqual(
            prep_planner._select_plan_ref("story", "epic/1-x", "44-fix-thing", "main"),
            ("44-fix-thing", prep_planner.PLAN_REF_ROW_OPEN_PR_HEAD),
        )

    def test_select_plan_ref_story_own_branch_row(self):
        # Row 4. The caller has already verified both preconditions; the table just honours them.
        self.assertEqual(
            prep_planner._select_plan_ref(
                "story", "epic/1-x", None, "main",
                parent_epic_open=True, own_branch_at_base_tip="42-thing",
            ),
            ("42-thing", prep_planner.PLAN_REF_ROW_STORY_OWN_BRANCH),
        )

    def test_select_plan_ref_story_own_branch_row_over_the_bootstrap_base(self):
        # The pre-bootstrap half: no epic branch, so rows 5/6 would have chosen `main` — the row
        # fires the same way, and the table needs no knowledge of WHICH base it displaced.
        self.assertEqual(
            prep_planner._select_plan_ref(
                "story", None, None, "main",
                parent_epic_open=True, own_branch_at_base_tip="42-thing",
            ),
            ("42-thing", prep_planner.PLAN_REF_ROW_STORY_OWN_BRANCH),
        )

    def test_select_plan_ref_open_pr_head_wins_over_own_branch(self):
        # Row 1 keeps its precedence structurally: its check returns before the story arm is
        # reached, so the own-branch kwarg cannot displace it even if a caller passed one.
        self.assertEqual(
            prep_planner._select_plan_ref(
                "story", "epic/1-x", "42-thing-pr", "main", own_branch_at_base_tip="42-thing",
            ),
            ("42-thing-pr", prep_planner.PLAN_REF_ROW_OPEN_PR_HEAD),
        )

    def test_select_plan_ref_epic_target_ignores_the_own_branch_kwarg(self):
        # The epic arm returns ahead of the story arm — an epic-as-target is never own-branch.
        self.assertEqual(
            prep_planner._select_plan_ref(
                "epic", "epic/1-x", None, "main", own_branch_at_base_tip="1-x",
            ),
            ("epic/1-x", prep_planner.PLAN_REF_ROW_EPIC_BRANCH),
        )

    def test_select_plan_ref_own_branch_is_keyword_only(self):
        # The tripwire protecting the eight positional calls above: a sixth positional argument
        # would silently land in `own_branch_at_base_tip` and rewrite every one of their rows.
        with self.assertRaises(TypeError):
            prep_planner._select_plan_ref("story", "epic/1-x", None, "main", True, "42-thing")

    def test_eligible_base_rows_are_every_row_whose_ref_is_a_base(self):
        # 4.33.0 widened the set past the two live-epic story rows: a standalone issue's own branch
        # descends from `main` exactly as a story's descends from its epic branch, so the same
        # containment argument holds. `open-pr-head` and the epic rows stay out — there `plan_ref`
        # already IS the issue's own branch, or none exists yet.
        self.assertEqual(
            prep_planner.ROWS_ELIGIBLE_FOR_OWN_BRANCH,
            frozenset(
                (
                    prep_planner.PLAN_REF_ROW_STORY_PARENT_BRANCH,
                    prep_planner.PLAN_REF_ROW_STORY_PARENT_BOOTSTRAP,
                    prep_planner.PLAN_REF_ROW_STORY_NO_PARENT,
                    prep_planner.PLAN_REF_ROW_DEFAULT,
                )
            ),
        )

    def test_select_plan_ref_default_arm_own_branch_row(self):
        # A standalone issue's own branch gets its OWN label — never `story-own-branch`, which
        # would claim a story where there is none (the D4 row-truthfulness rule).
        self.assertEqual(
            prep_planner._select_plan_ref(
                "standard", None, None, "main", own_branch_at_base_tip="42-thing",
            ),
            ("42-thing", prep_planner.PLAN_REF_ROW_OWN_BRANCH),
        )

    def test_select_plan_ref_story_with_no_parent_keeps_the_story_label(self):
        self.assertEqual(
            prep_planner._select_plan_ref(
                "story", None, None, "main", own_branch_at_base_tip="42-thing",
            ),
            ("42-thing", prep_planner.PLAN_REF_ROW_STORY_OWN_BRANCH),
        )

    def test_select_plan_ref_accepted_ambient_outranks_every_row(self):
        # The operator's explicit answer about this session's vantage — even an open PR's head
        # does not override it.
        self.assertEqual(
            prep_planner._select_plan_ref(
                "story", "epic/1-x", "42-thing-pr", "main", accepted_ambient="99-other",
            ),
            ("99-other", prep_planner.PLAN_REF_ROW_AMBIENT_ACCEPTED),
        )

    def test_parse_phase_tracker_ticked_and_unticked(self):
        body = "## Phase tracker\n- [x] Phase 1 — substrate (commit abc1234)\n- [ ] Phase 2 — harness\n"
        self.assertEqual(
            prep_planner._parse_phase_tracker(body),
            [
                {
                    "checked": True, "phase": 1, "title": "substrate", "sub_label": None,
                    "commit_sha": "abc1234", "annotation": None,
                },
                {
                    "checked": False, "phase": 2, "title": "harness", "sub_label": None,
                    "commit_sha": None, "annotation": None,
                },
            ],
        )

    def test_parse_phase_tracker_absent(self):
        self.assertEqual(prep_planner._parse_phase_tracker("no tracker here"), [])

    def test_suggested_playbook_by_type(self):
        # The four real S13 playbook names, keyed on (type, mode, parent_epic_open).
        self.assertEqual(prep_planner._suggested_playbook("epic", "fresh"), "epic.md")
        self.assertEqual(prep_planner._suggested_playbook("standard", "fresh"), "single.md")
        # A story under an OPEN parent epic -> story-jit (owns both fresh and revise).
        self.assertEqual(
            prep_planner._suggested_playbook("story", "fresh", parent_epic_open=True), "story-jit.md"
        )
        self.assertEqual(
            prep_planner._suggested_playbook("story", "revise", parent_epic_open=True), "story-jit.md"
        )
        # A story with no open parent epic -> the single-issue path (v1 "Everything else").
        self.assertEqual(
            prep_planner._suggested_playbook("story", "fresh", parent_epic_open=False), "single.md"
        )
        # revise mode short-circuits to revise.md for a standalone issue or an epic.
        self.assertEqual(prep_planner._suggested_playbook("standard", "revise"), "revise.md")
        self.assertEqual(prep_planner._suggested_playbook("epic", "revise"), "revise.md")

    def test_extract_plan_sha_from_header_line(self):
        body = "**Implementation plan** — #1 x — planned 2026-01-01T00:00:00Z at `main@abc1234`\n"
        self.assertEqual(prep_planner._extract_plan_sha(body), "abc1234")

    def test_extract_plan_sha_missing_is_none(self):
        self.assertIsNone(prep_planner._extract_plan_sha("no sha header here"))

    def test_find_one_marker_no_match(self):
        comment, untrusted, decision = prep_planner._find_one_marker([{"body": "unrelated"}], "<!-- x -->", "x")
        self.assertIsNone(comment)
        self.assertEqual(untrusted, 0)
        self.assertIsNone(decision)

    def test_find_one_marker_single_match(self):
        thread = [{"body": "unrelated"}, {"id": 1, "authorAssociation": "MEMBER", "body": "<!-- x -->\nfound"}]
        comment, untrusted, decision = prep_planner._find_one_marker(thread, "<!-- x -->", "x")
        self.assertEqual(comment["id"], 1)
        self.assertEqual(untrusted, 0)
        self.assertIsNone(decision)

    def test_find_one_marker_multiple_matches_yields_ambiguous(self):
        thread = [
            {"id": 1, "authorAssociation": "OWNER", "body": "<!-- x -->\na"},
            {"id": 2, "authorAssociation": "COLLABORATOR", "body": "<!-- x -->\nb"},
        ]
        comment, _, decision = prep_planner._find_one_marker(thread, "<!-- x -->", "x")
        self.assertIsNone(comment)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["code"], "MARKER_AMBIGUOUS")

    def test_find_one_marker_skips_an_untrusted_author(self):
        # #179: an untrusted dossier is neither the dossier nor a duplicate of the trusted one.
        thread = [
            {"id": 1, "authorAssociation": "NONE", "body": "<!-- x -->\nforged"},
            {"id": 2, "authorAssociation": "MEMBER", "body": "<!-- x -->\nreal"},
            {"id": 3, "body": "<!-- x -->\nno association"},
        ]
        comment, untrusted, decision = prep_planner._find_one_marker(thread, "<!-- x -->", "x")
        self.assertEqual(comment["id"], 2)
        self.assertEqual(untrusted, 2)
        self.assertIsNone(decision)
        comment, untrusted, decision = prep_planner._find_one_marker(thread[:1], "<!-- x -->", "x")
        self.assertIsNone(comment)
        self.assertEqual(untrusted, 1)

    def test_merge_notices_drops_none(self):
        self.assertEqual(prep_planner._merge_notices(["A"], [None, "B", "A"]), ["A", "B"])


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
            [sys.executable, str(SCRIPT), "200"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


class ParentEpicNativeShortCircuitTests(unittest.TestCase):
    """The story→epic lookup prefers the native `parent` relation
    (skills/_shared/epic-story-hierarchy.md). Asserted on BOTH copies of the helper — prep_planner's
    local one and the shared `branching` core — because the "restated locally, no prep-to-prep
    imports" convention means a fix to one does not reach the other.

    Two properties matter: no `gh` round-trip (the fact is already in hand), and no `AMBIGUOUS`
    exposure (an issue has at most one parent, where the full-text search can match many epics).
    """

    NATIVE_PARENT = {
        "id": "I_kw1",
        "number": 95,
        "state": "OPEN",
        "title": "Epic: Public patient funnel",
        "url": "https://github.com/o/r/issues/95",
    }

    def _assert_short_circuits(self, module):
        def fail_on_call(*args, **kwargs):
            raise AssertionError("native parent must not trigger a gh call")

        with mock.patch.object(module.process, "run", side_effect=fail_on_call):
            matches, decision = module._search_parent_epic(
                "o/r", 96, native_parent=self.NATIVE_PARENT
            ) if module is prep_planner else module.search_parent_epic(
                "o/r", 96, native_parent=self.NATIVE_PARENT
            )
        self.assertIsNone(decision)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["number"], 95)
        self.assertEqual(matches[0]["state"], "OPEN")
        self.assertEqual(matches[0]["title"], "Epic: Public patient funnel")

    def test_prep_planner_uses_the_branching_core_not_a_copy(self):
        # The planner carried a byte-for-byte copy of this search until 4.33.1 — how the
        # mention-is-parent bug lived in two places. One implementation now.
        self.assertIs(prep_planner._search_parent_epic, branching.search_parent_epic)

    def test_branching_core_short_circuits_without_a_gh_call(self):
        self._assert_short_circuits(branching)

    def test_absent_native_parent_falls_through_to_the_search(self):
        """A story with no native parent (filed before the relation existed) must still reach the
        legacy search rather than silently reporting "no epic"."""
        calls = []

        def fake_run(argv, cwd=None, env=None, input_text=None, check=False):
            calls.append(list(argv))
            return process.CommandResult(returncode=0, stdout="[]", stderr="")

        with mock.patch.object(branching.process, "run", side_effect=fake_run):
            matches, decision = branching.search_parent_epic("o/r", 96, native_parent=None)
        self.assertIsNone(decision)
        self.assertEqual(matches, [])
        self.assertEqual(len(calls), 1)
        self.assertIn("--search", calls[0])
        # gh's default cap is 30; membership is filtered client-side, so the candidate set must not
        # be truncated behind mention-only epics.
        self.assertEqual(
            calls[0][calls[0].index("--limit") + 1], str(branching.PARENT_EPIC_SEARCH_LIMIT)
        )


if __name__ == "__main__":
    unittest.main()
