"""Unit tests for scripts/prep_workspace_open.py — the v3 operator-side workspace opener.

Topology mirrors tests/test_prep_resolver.py: `gh` through the offline shim, `git` through a real
temp origin+clone, the prep driven as a real subprocess. The opener's contract under test:
linked-first branch derivation (adopt, never re-mint), `gh issue develop` create with the
ISSUE_LINK_UNSUPPORTED degradation, the gated-row zero-side-effect rule, epic bootstrap ownership,
the continue row's no-link rule, and ROOT_*/BRANCH_IN_USE propagation from the composed ensure.
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
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "prep_workspace_open.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_workspace_open  # noqa: E402
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _write(path, text):
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


class PrepWorkspaceOpenSandboxTestCase(unittest.TestCase):
    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    def _run(self, args, fixture_case=None, cwd=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            env=env,
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def _envelope(self, issue="100", fixture_case="prep_workspace_open_fresh", expect_rc=0, cwd=None):
        result = self._run(
            [issue, "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case=fixture_case,
            cwd=cwd,
        )
        self.assertEqual(result.returncode, expect_rc, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope


class ScriptExistsTests(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(SCRIPT, os.X_OK))


class FreshOpenTests(PrepWorkspaceOpenSandboxTestCase):
    def test_an_untrusted_plan_marker_reads_absent_with_only_its_notice(self):
        # #179: plan-before-open routing must not count a stranger's marked comment as the plan, and
        # says why it reads absent; the gather's other success-path notices stay dropped.
        envelope = self._envelope(fixture_case="prep_workspace_open_plan_untrusted")
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["plan"]["present"])
        found = [n for n in envelope["notices"] if n.startswith("MARKER_UNTRUSTED")]
        self.assertEqual(len(found), 1)
        self.assertIn("issue #100", found[0])

    def test_fresh_create_links_and_opens_the_worktree(self):
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["branch"]["name"], "100-fix-the-widget")
        self.assertEqual(envelope["branch"]["base"], "main")
        self.assertEqual(envelope["branch"]["source"], "computed")
        self.assertTrue(envelope["link"]["attempted"])
        self.assertTrue(envelope["link"]["created"])
        wt = Path(envelope["workspace"]["path"])
        self.assertTrue(wt.is_dir())
        self.assertEqual(
            wt, (self.root / ".worktrees" / "100-fix-the-widget").resolve()
        )
        self.assertEqual(envelope["workspace"]["branch"], "100-fix-the-widget")
        self.assertFalse(envelope["workspace"]["reused"])
        self.assertIn("start the next session", envelope["next_step"])

    def test_plan_absence_is_a_fact_for_the_summary_routing(self):
        envelope = self._envelope()
        self.assertFalse(envelope["plan"]["present"])

    def test_reopen_is_idempotent_and_reports_reused(self):
        self._envelope()
        envelope = self._envelope()
        self.assertTrue(envelope["workspace"]["reused"])

    def test_setup_hooks_run_at_open(self):
        _write(
            self.root / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `echo ran >> .hook-log`\n<!-- /worktree-setup -->\n",
        )
        _git(["add", "CLAUDE.md"], self.root)
        _git(["commit", "-m", "add setup hook block"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)
        envelope = self._envelope()
        self.assertTrue(envelope["workspace"]["setup"]["succeeded"])
        wt = Path(envelope["workspace"]["path"])
        self.assertIn("ran", (wt / ".hook-log").read_text(encoding="utf-8"))

    def test_hooks_come_from_the_worktree_the_operator_invoked_from(self):
        """The invoker's checkout supplies the hook block even when that checkout is a DIFFERENT
        worktree from the main one. `build_facts` normalizes `root` to the main checkout on its
        first line, so without threading the pre-normalization cwd through to `ensure --work` this
        silently falls back to the main checkout's version — the opposite of the documented rule
        that the operator picks the supplying branch by choosing where they stand."""
        _write(
            self.root / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `echo main-checkout >> .hook-log`\n<!-- /worktree-setup -->\n",
        )
        _git(["add", "CLAUDE.md"], self.root)
        _git(["commit", "-m", "default-branch setup hook"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)

        other = self.root / ".worktrees" / "hook-experiment"
        other.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "-b", "hook-experiment", str(other), "origin/main"], self.root)
        _write(
            other / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `echo other-worktree >> .hook-log`\n<!-- /worktree-setup -->\n",
        )

        envelope = self._envelope(cwd=str(other))
        wt = Path(envelope["workspace"]["path"])
        log = (wt / ".hook-log").read_text(encoding="utf-8")
        self.assertIn("other-worktree", log)
        self.assertNotIn("main-checkout", log)
        self.assertEqual(envelope["workspace"]["setup"]["source"]["branch"], "hook-experiment")


class LinkedBranchTests(PrepWorkspaceOpenSandboxTestCase):
    def test_existing_linked_branch_is_adopted_verbatim(self):
        envelope = self._envelope(fixture_case="prep_workspace_open_adopt_linked")
        self.assertEqual(envelope["branch"]["name"], "100-custom-linked")
        self.assertEqual(envelope["branch"]["source"], "linked")
        self.assertEqual(envelope["link"]["existing"], ["100-custom-linked"])
        self.assertFalse(envelope["link"]["created"])
        self.assertIsNone(envelope["branch"]["collided_with"])
        self.assertEqual(envelope["workspace"]["branch"], "100-custom-linked")

    def test_multiple_linked_branches_is_ambiguous_with_no_side_effect(self):
        envelope = self._envelope(fixture_case="prep_workspace_open_multi_linked")
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")
        self.assertEqual(len(envelope["decision"]["context"]["candidates"]), 2)
        self.assertFalse((self.root / ".worktrees").exists())

    def test_link_unsupported_degrades_to_a_local_branch_with_a_notice(self):
        envelope = self._envelope(fixture_case="prep_workspace_open_link_unsupported")
        self.assertEqual(envelope["status"], "ok")
        self.assertIn("ISSUE_LINK_UNSUPPORTED", envelope["notices"])
        self.assertEqual(envelope["branch"]["name"], "100-fix-the-widget")
        self.assertFalse(envelope["link"]["created"])
        self.assertTrue(Path(envelope["workspace"]["path"]).is_dir())


class GatedRowTests(PrepWorkspaceOpenSandboxTestCase):
    def test_foreign_pr_row_reports_the_gate_with_zero_side_effects(self):
        envelope = self._envelope(fixture_case="prep_workspace_open_gated")
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["vector"]["mode"], "gated")
        gate = envelope["vector"]["gate"]
        self.assertEqual(gate["header"], "Stale PR")
        self.assertEqual(gate["options"], ["Take it over", "Start fresh"])
        self.assertNotIn("workspace", envelope)
        self.assertNotIn("branch", envelope)
        self.assertNotIn("link", envelope)
        self.assertFalse((self.root / ".worktrees").exists())


class ContinueRowTests(PrepWorkspaceOpenSandboxTestCase):
    def test_own_open_pr_reuses_its_head_branch_without_linking(self):
        # The manifest carries NO `gh issue develop` entries — the shim exits loudly on any
        # un-fixtured argv, so passing at all proves the continue row never touches linking.
        envelope = self._envelope(fixture_case="prep_workspace_open_continue")
        self.assertEqual(envelope["vector"]["mode"], "continue")
        self.assertEqual(envelope["branch"]["name"], "100-fix-the-widget")
        self.assertEqual(envelope["branch"]["source"], "pr-head")
        self.assertFalse(envelope["link"]["attempted"])
        self.assertTrue(Path(envelope["workspace"]["path"]).is_dir())


class EpicBootstrapTests(PrepWorkspaceOpenSandboxTestCase):
    def test_epic_bootstrap_creates_the_integration_branch(self):
        # v3: workspace-open owns epic integration-branch creation.
        envelope = self._envelope(fixture_case="prep_workspace_open_epic_bootstrap")
        self.assertEqual(envelope["vector"]["type"], "epic")
        self.assertEqual(envelope["branch"]["name"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["branch"]["source"], "epic-bootstrap")
        self.assertEqual(envelope["branch"]["base"], "main")
        self.assertTrue(Path(envelope["workspace"]["path"]).is_dir())


class StoryBaseTests(PrepWorkspaceOpenSandboxTestCase):
    def test_story_under_open_epic_bases_on_the_epic_branch(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)
        envelope = self._envelope(issue="101", fixture_case="prep_workspace_open_story_adopt")
        self.assertEqual(envelope["vector"]["type"], "story")
        self.assertEqual(envelope["branch"]["base"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["branch"]["name"], "101-linked-story")
        self.assertEqual(envelope["branch"]["source"], "linked")
        self.assertEqual(envelope["workspace"]["base_ref"], "epic/100-sandbox-fixture")


class UntypedSubIssueBaseTests(PrepWorkspaceOpenSandboxTestCase):
    """Issue #31: the hierarchy lookup sat behind `issue_type == "story"`, a lexical label gate, so
    a sub-issue labelled by KIND of work (`bug` / `tech-debt` / `follow-up`) read as `standard` and
    based on the default branch — even though its native `parent` was already in the gather
    envelope, and is the routing's own preferred tier. The parent was fetched, then never
    consulted; `epic: null` reported "no parent" where the truth was "never asked"."""

    def _open_epic_branch(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)

    def test_untyped_sub_issue_bases_on_its_parents_integration_branch(self):
        self._open_epic_branch()
        envelope = self._envelope(
            issue="264", fixture_case="prep_workspace_open_untyped_subissue"
        )
        # Type is untouched — the fix moved the gate, it did not widen `detect_type`.
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(envelope["branch"]["base"], "epic/100-sandbox-fixture")
        self.assertEqual(envelope["workspace"]["base_ref"], "epic/100-sandbox-fixture")
        # It still gets its OWN branch — basing on the epic never means adopting it (#29/#30).
        self.assertEqual(envelope["branch"]["name"], "264-stale-cache-key-survives-a-profile-rename")
        self.assertEqual(envelope["branch"]["source"], "computed")
        # The receipt now names the parent it based on, instead of `epic: null`.
        self.assertEqual(envelope["epic"]["parent_epic"]["number"], 100)
        self.assertEqual(envelope["epic"]["branch_facts"]["branch"], "epic/100-sandbox-fixture")

    def test_untyped_sub_issue_whose_parent_has_no_integration_branch_notices(self):
        # No `epic/100-*` pushed: the parent may be an epic whose workspace is not open yet, or a
        # STORY (which would make this target a deliverable slice). The `parent` node carries no
        # labels, so the two are indistinguishable here — the notice asserts only what was
        # established, and the base falls back to the default branch as before.
        envelope = self._envelope(
            issue="264", fixture_case="prep_workspace_open_untyped_subissue"
        )
        self.assertEqual(envelope["branch"]["base"], "main")
        self.assertIn("PARENT_HAS_NO_INTEGRATION_BRANCH", envelope["notices"])
        self.assertEqual(envelope["epic"]["parent_epic"]["number"], 100)
        self.assertIsNone(envelope["epic"]["branch_facts"]["branch"])
        # The story-only attention line must NOT fire here — it calls the parent an epic, which is
        # true by construction for a story and unknown for an untyped target.
        self.assertFalse(
            [line for line in envelope["attention"] if "parent epic" in line], envelope["attention"]
        )

    def test_an_untyped_issue_with_no_parent_runs_no_lookup_at_all(self):
        # The legacy `#<N> in:body` full-text tier stays story-only: a round-trip per standard
        # issue, matching loosely, to guess at a hierarchy the target probably has no place in.
        # The fixture manifest omits that search entirely, so the shim would fail on the call.
        envelope = self._envelope(issue="100", fixture_case="prep_workspace_open_fresh")
        self.assertEqual(envelope["branch"]["base"], "main")
        self.assertIsNone(envelope["epic"])


class SliceRefusalTests(PrepWorkspaceOpenSandboxTestCase):
    """A NON-EPIC's sub-issues are deliverable slices by construction
    (skills/_shared/epic-story-hierarchy.md), and a slice has no branch and no PR of its own — it
    ships as a phase on its parent's branch. Minting one here would silently promote it to a
    story: the same demotion-in-reverse #30 fixed one level up."""

    def test_a_slice_is_refused_before_any_side_effect(self):
        result = self._run(
            ["266", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_workspace_open_slice",
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "TARGET_IS_SLICE")
        self.assertEqual(envelope["decision"]["context"]["parent"]["number"], 103)
        self.assertEqual(envelope["decision"]["context"]["parent_kind"], "non-epic")
        options = envelope["decision"]["options"]
        self.assertEqual(len(options), 3)
        self.assertIn("103", options[0])
        # Every option must be an action taken OUTSIDE and then re-run. A "proceed anyway" option
        # would be unactionable: this prep has no override flag, so re-running after picking it
        # raises the identical card — the operator would be offered a choice nothing can execute.
        self.assertNotIn("anyway", " ".join(options))
        for option in options[1:]:
            self.assertIn("re-run", option)
        # Zero side effects: no worktree, and the fixture manifest carries no `issue develop` entry
        # at all, so the shim would have failed the run if linking had been attempted.
        self.assertFalse((self.root / ".worktrees").exists())

    def test_the_classification_is_not_silently_degraded(self):
        # The parent read must actually answer. If its manifest entry were missing the shim would
        # exit 2, `classify_parent` would degrade to PARENT_KIND_UNAVAILABLE, and the refusal would
        # never fire — a fixture that passes while proving nothing.
        envelope = self._envelope(
            issue="264", fixture_case="prep_workspace_open_untyped_subissue"
        )
        self.assertNotIn("PARENT_KIND_UNAVAILABLE", envelope["notices"])
        self.assertEqual(envelope["epic"]["parent_kind"], "epic")


class ClosedParentEpicTests(PrepWorkspaceOpenSandboxTestCase):
    def test_a_closed_parent_epic_does_not_advise_opening_its_workspace(self):
        # Review finding on #31: lifting the attention line out from under the parent-is-OPEN
        # guard made it fire for a CLOSED parent too. Forking from main is the correct and FINAL
        # outcome there and there is no workspace left to open, so the advice would be wrong —
        # not merely unhelpful — and it contradicts the PARENT_CLOSED notice beside it.
        envelope = self._envelope(
            issue="265", fixture_case="prep_workspace_open_story_closed_parent"
        )
        self.assertEqual(envelope["vector"]["type"], "story")
        self.assertEqual(envelope["branch"]["base"], "main")
        self.assertIn("PARENT_CLOSED", envelope["notices"])
        self.assertEqual(
            [line for line in envelope["attention"] if "integration branch" in line], []
        )
        # The parent is still reported — "asked, closed" stays distinguishable from "never asked".
        self.assertEqual(envelope["epic"]["parent_epic"]["number"], 100)


class EpicPrMentionTests(PrepWorkspaceOpenSandboxTestCase):
    """Issue #29: an epic integration PR lists every one of its stories by number, so the loose
    `#<N> in:body` open-PR search surfaces it once per story. Adopting it as the story's prior PR
    handed the story the EPIC's integration branch — silently demoting the story to a deliverable
    slice (`skills/_shared/epic-story-hierarchy.md`: own-branch-and-PR is the one parameter that
    separates them), with its work landing on the epic branch behind no story PR and no review."""

    def test_an_epic_pr_listing_its_stories_does_not_make_a_story_continue(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/100-sandbox-fixture", "origin/main"], self.root)
        _git(["push", "origin", "epic/100-sandbox-fixture"], self.root)
        envelope = self._envelope(
            issue="101", fixture_case="prep_workspace_open_story_epic_pr_mention"
        )
        self.assertEqual(envelope["vector"]["type"], "story")
        self.assertEqual(envelope["vector"]["mode"], "fresh")
        self.assertEqual(envelope["vector"]["prior_pr_row"], "no-prior-pr")
        self.assertIsNone(envelope["prior_pr"])
        # The story gets its OWN branch, forked from the epic's integration branch — never the
        # epic's branch itself.
        self.assertEqual(envelope["branch"]["name"], "101-story-a-first-slice")
        self.assertEqual(envelope["branch"]["source"], "computed")
        self.assertEqual(envelope["branch"]["base"], "epic/100-sandbox-fixture")
        # Linking is skipped on a continue row on the premise that the prior PR already binds
        # branch to issue — false here, since PR #245 binds to the EPIC.
        self.assertTrue(envelope["link"]["attempted"])
        self.assertTrue(envelope["link"]["created"])
        # The drop is silent in the operator-facing channels (an epic listing 11 stories would
        # otherwise put an unactionable line in 11 sessions) but debuggable in the envelope.
        self.assertEqual(envelope["attention"], [])
        self.assertEqual(
            envelope["prior_pr_rejected"],
            [{"number": 245, "headRefName": "epic/100-sandbox-fixture"}],
        )

    def test_the_diagnostic_fact_is_absent_when_nothing_was_dropped(self):
        envelope = self._envelope()
        self.assertNotIn("prior_pr_rejected", envelope)


class EnsureGatePropagationTests(PrepWorkspaceOpenSandboxTestCase):
    def test_a_dirty_invoker_opens_and_the_notice_is_forwarded(self):
        """v3.x: ROOT_DIRTY is retired, and the ensure core's HOOK_SOURCE_DIRTY notice must reach
        the prep's own envelope — this prep used to discard the composed core's notices, which
        would have made the dirty-source report invisible to the operator."""
        _write(self.root / "dirty.txt", "uncommitted\n")
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        self.assertIn("HOOK_SOURCE_DIRTY", envelope["notices"])

    def test_branch_in_use_propagates(self):
        elsewhere_ctx = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere_ctx.cleanup)
        elsewhere = Path(elsewhere_ctx.name) / "elsewhere-wt"
        _git(["worktree", "add", str(elsewhere), "-b", "100-fix-the-widget"], self.root)
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "BRANCH_IN_USE")

    def test_branch_in_use_is_raised_before_the_link_create(self):
        """SKILL.md §1: every decision is raised before any link or worktree side effect. The
        `gh issue develop <N> --name` create pushes a branch to origin and links it, so a declined
        BRANCH_IN_USE card raised after it would leave both behind. The shim is stateless and the
        create degrades any failure to a notice the decision path drops, so the only observable
        proof is the core itself — driven in-process, as the coordinator composes it."""
        elsewhere_ctx = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere_ctx.cleanup)
        elsewhere = Path(elsewhere_ctx.name) / "elsewhere-wt"
        _git(["worktree", "add", str(elsewhere), "-b", "100-fix-the-widget"], self.root)
        env = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="prep_workspace_open_fresh"
        )
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            prep_workspace_open, "_create_linked_branch", return_value=(True, None)
        ) as create:
            payload, _notices, decision = prep_workspace_open.build_open(
                "100", "octo/widgets", root=str(self.root), scratch_dir=self.scratch
            )
        create.assert_not_called()
        self.assertIsNone(payload)
        self.assertEqual(decision["code"], "BRANCH_IN_USE")
        context = decision["context"]
        self.assertEqual(context["branch"], "100-fix-the-widget")
        self.assertEqual(Path(context["existing_path"]), elsewhere.resolve())
        self.assertEqual(
            Path(context["requested_path"]),
            (self.root / ".worktrees" / "100-fix-the-widget").resolve(),
        )
        self.assertFalse((self.root / ".worktrees").exists())

if __name__ == "__main__":
    unittest.main()
