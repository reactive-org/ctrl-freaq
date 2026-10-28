"""Unit tests for scripts/prep_drafter.py — the drafter's complete facts block in one call
(architecture.md §4; docs/implementation.md S14; docs/specs/drafter.md).

Test topology (mirrors tests/test_prep_planner.py — the S8-locked composition pattern this step
inherits): `gh` calls go through the offline shim (`tests/support/shimenv`); `git` calls (this
step needs exactly one — `git rev-parse HEAD` for the informational `root.sha` fact; see
`prep_drafter.py`'s module docstring's "No workspace" paragraph for why there is no root-freshness
protocol here) go through a real temp origin+clone (`tests/support/gitsandbox`) — no shim needed
for those (architecture.md §10). `prep_drafter.py` is driven as a real subprocess so every test
exercises the full argv-in / subprocess / envelope-out path a real caller uses. Every fixture
origin is a local git sandbox — hermetic per the S9 lesson (no test resolves `.` against a real
network-backed clone); `shimenv.intercepted_env`'s `NETWORK_POISON_ENV` guard is never bypassed.

Coverage matrix (S14 DoD):
- Vector derivation ×2: new (no --issue) and revise (any already-filed target, epic included —
  #16 retired the third `epic-revise` mode along with the drafter's epic decomposition)
  (Epic-labeled target).
- OQ candidate search fixtures: match (a genuinely-filed companion) and no-match (legitimately
  still "(not filed)").
- Template/label inventory: present/absent fixtures for `.github/ISSUE_TEMPLATE/` and `gh label
  list`, plus grounding-doc presence (PRD's 4-candidate-path search, architecture/constitution/
  CLAUDE.md).
- Conformance on every emitting path + the two-sided call-budget test (S6/S12 style).
- Decision codes: AUTH_REQUIRED (the labels call),
  MARKER_AMBIGUOUS (gh_gather's own duplicate-plan-comment forwarding), AMBIGUOUS (malformed
  `## Open questions` section, forwarded from the S14-promoted `oq_tracker.py`).
"""

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
SCRIPT = SCRIPTS_DIR / "prep_drafter.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_drafter  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


# The doc catalogue every sandbox starts configured with (skills/_shared/doc-catalogue.md) — one
# entry naming a document the base setUp also creates, so the grounding dimension is neutral for
# every test not about grounding (no notice, no attention line).
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


class PrepDrafterSandboxTestCase(unittest.TestCase):
    """Shared setup: a real temp git origin+clone (the `--root`) — no push/fetch is needed (this
    prep runs no root-freshness protocol; see `prep_drafter.py`'s "No workspace" docstring note),
    but a real git repo with a commit is needed for `git rev-parse HEAD` (`root.sha`)."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        # A configured consuming repo (see SEEDED_CATALOGUE_INTERIOR). The drafter's grounding
        # vantage is the ambient checkout, so a plain working-tree write is what it reads — no
        # commit needed, unlike the planner's ensured checkout at `plan_ref`.
        self._seed_catalogue(SEEDED_CATALOGUE_INTERIOR)
        _write(self.root / "docs" / "prd.md", "# PRD\n")
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    def _seed_catalogue(self, interior):
        _write(self.root / "docs" / "README.md", _catalogue_block(interior))

    def _remove_catalogue(self):
        (self.root / "docs" / "README.md").unlink()

    def _run(self, args, fixture_case=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def _envelope(self, repo="octo/widgets", issue=None, fixture_case=None, extra_args=None):
        args = [repo, "--root", str(self.root), "--scratch-dir", self.scratch]
        if issue is not None:
            args += ["--issue", str(issue)]
        if extra_args:
            args += extra_args
        result = self._run(args, fixture_case=fixture_case)
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


# ---------------------------------------------------------------------------
# DoD box 1: fixtures derive the vector for new and revise correctly.
# ---------------------------------------------------------------------------


class NewModeVectorTests(PrepDrafterSandboxTestCase):
    def test_new_mode_vector_and_playbook(self):
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertEqual(envelope["vector"], {"mode": "new", "type": None})
        self.assertEqual(envelope["suggested_playbook"], "new.md")

    def test_new_mode_has_no_target_and_no_revise_epic_keys(self):
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertIsNone(envelope["target"])
        self.assertNotIn("revise", envelope)
        # #16 retired the epic_revise facts key with the mode; nothing may reintroduce it.
        self.assertNotIn("epic_revise", envelope)
        self.assertNotIn("sections", envelope)
        self.assertEqual(envelope["open_questions"], [])
        self.assertEqual(envelope["open_question_candidates"], [])


class ReviseModeVectorTests(PrepDrafterSandboxTestCase):
    def test_revise_mode_vector_standard_type(self):
        envelope = self._envelope(issue=300, fixture_case="prep_drafter_revise_standard")
        self.assertEqual(envelope["vector"], {"mode": "revise", "type": "standard"})
        self.assertEqual(envelope["suggested_playbook"], "revise.md")

    def test_revise_mode_vector_question_type(self):
        envelope = self._envelope(issue=301, fixture_case="prep_drafter_revise_question")
        self.assertEqual(envelope["vector"], {"mode": "revise", "type": "question"})
        self.assertEqual(envelope["suggested_playbook"], "question.md")

    def test_an_epic_target_is_an_ordinary_revise(self):
        """#16: an epic used to select a third mode (`epic-revise`) routing to the drafter's epic-split
        playbook. The drafter now revises one body and decomposes nothing, so an epic is a plain
        revise — its story set is the native relation, and changing which stories exist is a slicer
        run at epic altitude."""
        envelope = self._envelope(issue=310, fixture_case="prep_drafter_revise_epic")
        self.assertEqual(envelope["vector"], {"mode": "revise", "type": "epic"})
        self.assertEqual(envelope["suggested_playbook"], "revise.md")
        self.assertIn("revise", envelope)
        self.assertNotIn("epic_revise", envelope)

    def test_an_epic_revise_pays_no_per_story_state_fetch(self):
        """The read that the retired mode paid for. `prep_drafter_revise_epic`'s manifest carries no
        per-story entry, so a regression that reinstated the reconciliation gather would fail as a
        shim MISS rather than passing quietly. The epic's sub-issues still ride the one gather."""
        envelope = self._envelope(issue=310, fixture_case="prep_drafter_revise_epic")
        self.assertEqual(
            [node["number"] for node in envelope["target"]["sub_issues"]], [311, 312]
        )

    def test_revise_mode_target_facts_shape(self):
        envelope = self._envelope(issue=300, fixture_case="prep_drafter_revise_standard")
        target = envelope["target"]
        self.assertEqual(target["kind"], "issue")
        self.assertEqual(target["number"], 300)
        self.assertEqual(target["state"], "OPEN")
        self.assertEqual(target["labels"], ["bug"])
        # #16 retired the epic_revise facts key with the mode; nothing may reintroduce it.
        self.assertNotIn("epic_revise", envelope)

    def test_revise_mode_has_revise_key_with_plan_absent_and_no_open_prs(self):
        envelope = self._envelope(issue=300, fixture_case="prep_drafter_revise_standard")
        self.assertIn("revise", envelope)
        self.assertFalse(envelope["revise"]["plan"]["present"])
        self.assertEqual(envelope["revise"]["open_prs"], [])
        self.assertEqual(envelope["revise"]["closed_by_pull_requests_references"], [])
        self.assertEqual(envelope["revise"]["project_items"], [])
        self.assertIn("dod", envelope["revise"])
        self.assertIs(envelope["revise"]["dod"]["slice"], False)


class ReviseFactsTests(PrepDrafterSandboxTestCase):
    def test_plan_present_is_staged_and_flagged(self):
        envelope = self._envelope(issue=304, fixture_case="prep_drafter_revise_plan_present")
        plan = envelope["revise"]["plan"]
        self.assertTrue(plan["present"])
        self.assertIsNotNone(plan.get("comment_url"))
        self.assertIn("implementation-plan:v1", plan.get("body") or (plan.get("body_mode") and ""))

    def test_an_untrusted_plan_part_is_skipped_and_only_its_notice_forwarded(self):
        # #179: a trusted main plan whose build part only a stranger posted. The part is skipped, so
        # the gather reports both a `MARKER_UNTRUSTED` notice and a missing-part note; the drafter
        # forwards the first and, as before, drops the gather's other success-path notices.
        envelope = self._envelope(issue=304, fixture_case="prep_drafter_revise_plan_untrusted")
        self.assertTrue(envelope["revise"]["plan"]["present"])
        self.assertNotIn("evil.rb", envelope["revise"]["plan"]["body"])
        self.assertIn("- In this plan's [build part]", envelope["revise"]["plan"]["body"])
        found = [n for n in envelope["notices"] if n.startswith("MARKER_UNTRUSTED")]
        self.assertEqual(len(found), 1)
        self.assertIn("<!-- implementation-plan-part:v1:", found[0])
        self.assertFalse(any("no part comment carries it" in n for n in envelope["notices"]))

    def test_open_pr_and_extra_json_surface_in_revise_facts(self):
        envelope = self._envelope(issue=302, fixture_case="prep_drafter_revise_with_open_pr")
        open_prs = envelope["revise"]["open_prs"]
        self.assertEqual(len(open_prs), 1)
        self.assertEqual(open_prs[0]["number"], 303)
        self.assertEqual(open_prs[0]["headRefName"], "302-feature")
        self.assertEqual(envelope["revise"]["project_items"], [{"id": "PVTI_1"}])

    def test_closed_target_surfaces_attention(self):
        envelope = self._envelope(issue=305, fixture_case="prep_drafter_revise_closed_target")
        self.assertEqual(envelope["target"]["state"], "CLOSED")
        self.assertTrue(
            any("#305" in line and "closed" in line for line in envelope["attention"]), envelope["attention"]
        )


# ---------------------------------------------------------------------------
# DoD box 2: OQ candidate search fixtures — match and no-match.
# ---------------------------------------------------------------------------


class OpenQuestionTrackerSearchTests(PrepDrafterSandboxTestCase):
    def test_seeded_tracker_match_surfaces_the_candidate(self):
        envelope = self._envelope(issue=500, fixture_case="prep_drafter_oq_match")
        self.assertEqual(len(envelope["open_questions"]), 1)
        self.assertEqual(envelope["open_questions"][0]["question"], "(not filed)")
        self.assertEqual(len(envelope["open_question_candidates"]), 1)
        group = envelope["open_question_candidates"][0]
        self.assertEqual(group["oq_id"], "tag case sensitivity")
        self.assertEqual(group["candidates"][0]["number"], 51)
        self.assertTrue(
            any("tag case sensitivity" in line and "do not record it as (not filed)" in line
                for line in envelope["attention"]),
            envelope["attention"],
        )

    def test_no_match_leaves_not_filed_path_legitimately_open(self):
        envelope = self._envelope(issue=501, fixture_case="prep_drafter_oq_no_match")
        self.assertEqual(len(envelope["open_questions"]), 1)
        self.assertEqual(envelope["open_question_candidates"], [])
        self.assertEqual(envelope["attention"], [])


class OqQueryOneShotTests(PrepDrafterSandboxTestCase):
    """The `new`-mode one-shot `--oq-query` lookup (mirrors prep_planner.py's identical mechanism,
    now shared via oq_tracker.build_oq_query)."""

    def test_oq_query_emits_candidates_without_full_facts(self):
        result = self._run(
            ["octo/widgets", "--oq-query", "cache eviction policy"],
            fixture_case="prep_drafter_oq_query",
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertNotIn("vector", envelope)
        self.assertNotIn("suggested_playbook", envelope)
        self.assertIn("oq_query_candidates", envelope)
        groups = envelope["oq_query_candidates"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["query"], "cache eviction policy")
        self.assertEqual(groups[0]["candidates"][0]["number"], 88)

    def test_oq_query_ignores_issue_when_both_given(self):
        # --oq-query short-circuits regardless of --issue, mirroring prep_planner.py's contract.
        result = self._run(
            ["octo/widgets", "--issue", "999", "--oq-query", "cache eviction policy"],
            fixture_case="prep_drafter_oq_query",
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self.assertIn("oq_query_candidates", envelope)

    def test_oq_query_empty_list_makes_no_call(self):
        payload, notices, decision = prep_drafter.oq_tracker.build_oq_query("octo/widgets", [], cwd=None)
        self.assertIsNone(decision)
        self.assertEqual(payload, {"repo": "octo/widgets", "oq_query_candidates": []})
        self.assertEqual(notices, [])


# ---------------------------------------------------------------------------
# DoD box 3: template/label inventory correct on present/absent fixtures.
# ---------------------------------------------------------------------------


class TemplateInventoryTests(PrepDrafterSandboxTestCase):
    def test_templates_absent(self):
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        templates = envelope["repo_context"]["templates"]
        self.assertFalse(templates["present"])
        self.assertEqual(templates["files"], [])

    def test_templates_present(self):
        _write(self.root / ".github" / "ISSUE_TEMPLATE" / "bug.md", "# Bug template\n")
        _write(self.root / ".github" / "ISSUE_TEMPLATE" / "story.md", "# Story template\n")
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        templates = envelope["repo_context"]["templates"]
        self.assertTrue(templates["present"])
        self.assertEqual(templates["files"], ["bug.md", "story.md"])
        self.assertIn(str(self.root), templates["dir"])


class LabelInventoryTests(PrepDrafterSandboxTestCase):
    def test_labels_present_are_reported_verbatim(self):
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        names = {label["name"] for label in envelope["repo_context"]["labels"]}
        self.assertEqual(names, {"bug", "question", "audience:developer"})

    def test_labels_absent_reports_empty_list(self):
        # An empty `gh label list` result is a legitimate "no labels yet" repo, not an error.
        envelope = self._envelope(fixture_case="prep_drafter_labels_absent")
        self.assertEqual(envelope["repo_context"]["labels"], [])


class GroundingDocInventoryTests(PrepDrafterSandboxTestCase):
    """`repo_context.docs` is now the consuming repo's own `<!-- doc-catalogue -->` declaration
    (skills/_shared/doc-catalogue.md) rather than four hardcoded paths. `docs.prd` survives as a
    named fact — the drafter's PRD-tension step reads exactly it — but is derived from the
    catalogue's `prd`-role entry."""

    def test_declared_docs_are_reported_with_the_prd_fact_derived(self):
        _write(self.root / "docs" / "architecture.md", "# Architecture\n")
        self._seed_catalogue(
            "- `docs/architecture.md` — architecture — binding — Layer rules.\n"
            "- `docs/prd.md` — prd — binding — What the product is.\n"
        )
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        docs = envelope["repo_context"]["docs"]
        self.assertEqual(
            [e["path"] for e in docs["entries"]], ["docs/architecture.md", "docs/prd.md"]
        )
        self.assertTrue(docs["prd"]["present"])
        self.assertTrue(docs["prd"]["path"].endswith("docs/prd.md"))
        self.assertEqual(envelope["notices"], [])

    def test_prd_role_anywhere_in_the_catalogue_drives_the_prd_fact(self):
        """The path no longer matters — only the declared role does. This is the whole point of the
        migration: a repo keeping its PRD at `docs/product/requirements.md` is now first-class."""
        _write(self.root / "docs" / "product" / "requirements.md", "# Requirements\n")
        self._seed_catalogue(
            "- `docs/product/requirements.md` — prd — binding — What the product is.\n"
        )
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        prd = envelope["repo_context"]["docs"]["prd"]
        self.assertTrue(prd["present"])
        self.assertTrue(prd["path"].endswith("docs/product/requirements.md"))

    def test_no_prd_role_reports_absent_without_guessing_a_path(self):
        self._seed_catalogue("- `docs/guides/style.md` — guide — informative — House style.\n")
        _write(self.root / "docs" / "guides" / "style.md", "# Style\n")
        # A file at the path the retired hardcoded search would have found — proof nothing guesses.
        _write(self.root / "docs" / "prd.md", "# Not declared\n")
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        prd = envelope["repo_context"]["docs"]["prd"]
        self.assertFalse(prd["present"])
        self.assertIsNone(prd["path"])

    def test_declared_prd_that_does_not_exist_reports_absent_and_attention(self):
        self._seed_catalogue("- `docs/prd.md` — prd — binding — What the product is.\n")
        (self.root / "docs" / "prd.md").unlink()
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertFalse(envelope["repo_context"]["docs"]["prd"]["present"])
        self.assertTrue(
            any("docs/prd.md" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_absent_catalogue_yields_the_notice_and_an_attention_line(self):
        self._remove_catalogue()
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        docs = envelope["repo_context"]["docs"]
        self.assertEqual(docs["entries"], [])
        self.assertFalse(docs["prd"]["present"])
        self.assertIn("DOC_CATALOGUE_ABSENT", envelope["notices"])
        self.assertTrue(
            any("no doc catalogue" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_the_catalogue_is_not_read_from_commands_or_claude_md(self):
        self._remove_catalogue()
        _write(self.root / "CLAUDE.md", _catalogue_block(SEEDED_CATALOGUE_INTERIOR))
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertEqual(envelope["repo_context"]["docs"]["entries"], [])
        self.assertIn("DOC_CATALOGUE_ABSENT", envelope["notices"])


# ---------------------------------------------------------------------------
# OQ marker config block (present / absent -> heuristics fallback).
# ---------------------------------------------------------------------------


class OqMarkerConfigBlockTests(PrepDrafterSandboxTestCase):
    def test_absent_activates_heuristics(self):
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertFalse(envelope["config"]["oq_markers"]["present"])
        self.assertIsNone(envelope["config"]["oq_markers"]["raw"])
        self.assertTrue(envelope["config"]["heuristics_active"])

    def test_present_surfaces_raw_block_and_disables_heuristics(self):
        _write(
            self.root / "CLAUDE.md",
            "\n<!-- drafter-open-question-markers -->\n"
            "- register: `docs/open-questions.md`\n"
            "- inline-pattern: `\\[OPEN QUESTION\\]`\n"
            "- open-status: unresolved\n"
            "<!-- /drafter-open-question-markers -->\n",
        )
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        config = envelope["config"]
        self.assertTrue(config["oq_markers"]["present"])
        self.assertIn("docs/open-questions.md", config["oq_markers"]["raw"])
        self.assertFalse(config["heuristics_active"])
        self.assertIn(str(self.root), config["oq_markers"]["source"])


# ---------------------------------------------------------------------------
# root facts.
# ---------------------------------------------------------------------------


class RootFactsTests(PrepDrafterSandboxTestCase):
    def test_root_facts_shape_has_no_freshness_gate(self):
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        root = envelope["root"]
        self.assertEqual(set(root.keys()), {"path", "sha"})
        self.assertEqual(len(root["sha"]), 40)

    def test_root_sha_reflects_the_actual_head_no_fetch_no_ff_merge(self):
        # Unlike prep_planner.py / prep_resolver.py / prep_evaluator.py, this prep never fetches
        # or fast-forwards root — a commit made AFTER clone (never pushed) is what `root.sha`
        # reports, proving no root-freshness protocol runs here.
        _write(self.root / "local-only.txt", "not pushed anywhere\n")
        _git(["add", "local-only.txt"], self.root)
        _git(["commit", "-m", "local-only commit"], self.root)
        local_sha = _git(["rev-parse", "HEAD"], self.root)
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertEqual(envelope["root"]["sha"], local_sha)


# ---------------------------------------------------------------------------
# `facts.ambient` — which issue this checkout's branch is standing in.
# ---------------------------------------------------------------------------


class AmbientIssueTests(PrepDrafterSandboxTestCase):
    """The fact that lets the drafter notice it was invoked from inside `epic/95-<slug>` instead of
    filing an orphan. Non-gating by construction: every "no ambient issue" arm must stay an ordinary
    `status: ok` envelope, because a fact that could block filing would be a regression, not a
    feature (see prep_drafter.py's `facts.ambient` docstring paragraph)."""

    def _checkout(self, branch):
        _git(["checkout", "-b", branch], self.root)

    def test_epic_branch_yields_the_epic_as_the_ambient_issue(self):
        self._checkout("epic/95-public-patient-funnel")
        envelope = self._envelope(fixture_case="prep_drafter_new_ambient_epic")
        ambient = envelope["ambient"]
        self.assertEqual(ambient["number"], 95)
        self.assertEqual(ambient["pattern"], "epic")
        self.assertEqual(ambient["branch"], "epic/95-public-patient-funnel")
        self.assertEqual(ambient["type"], "epic")
        self.assertEqual(ambient["state"], "OPEN")

    def test_story_branch_yields_the_issue_with_the_issue_pattern(self):
        # `-vN` collision suffixes need no special handling — the slug arm swallows them, exactly as
        # `branching.branch_belongs_to_issue` already treats `<N>-<slug>-vN` as issue N's branch.
        self._checkout("164-harden-consent-copy-v2")
        envelope = self._envelope(fixture_case="prep_drafter_new_ambient_story")
        self.assertEqual(envelope["ambient"]["number"], 164)
        self.assertEqual(envelope["ambient"]["pattern"], "issue")

    def test_default_branch_yields_no_ambient_fact_and_pays_no_gh_call(self):
        # `prep_drafter_new_happy`'s manifest has exactly one entry (the labels call) and a shim
        # miss exits 2, so an unconditional issue lookup would surface here as a notice rather than
        # passing quietly.
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertIsNone(envelope["ambient"])
        self.assertEqual(envelope["notices"], [])

    def test_unparseable_branch_yields_no_fact_and_no_notice(self):
        self._checkout("feature/no-leading-number")
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertIsNone(envelope["ambient"])
        self.assertEqual(envelope["notices"], [])

    def test_detached_head_yields_no_fact(self):
        # `--abbrev-ref` prints the literal "HEAD" when detached — every `ro-*` read workspace is.
        _git(["checkout", "--detach", "HEAD"], self.root)
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertIsNone(envelope["ambient"])

    def test_failed_lookup_degrades_to_a_notice_not_a_decision(self):
        # A branch that only LOOKS like `<N>-<slug>`: `2024-roadmap-cleanup` parses as issue #2024
        # and the lookup 404s (here, a shim miss). Filing must still work.
        self._checkout("2024-roadmap-cleanup")
        envelope = self._envelope(fixture_case="prep_drafter_new_happy")
        self.assertEqual(envelope["status"], "ok")
        self.assertIsNone(envelope["ambient"])
        self.assertIn("AMBIENT_ISSUE_LOOKUP_UNAVAILABLE", envelope["notices"])

    def test_ambient_issue_raises_an_attention_line(self):
        self._checkout("epic/95-public-patient-funnel")
        envelope = self._envelope(fixture_case="prep_drafter_new_ambient_epic")
        self.assertTrue(
            any("epic/95-public-patient-funnel" in line for line in envelope["attention"]),
            msg="ambient issue missing from attention: %r" % (envelope["attention"],),
        )

    def test_revising_the_issue_whose_branch_we_stand_in_suppresses_the_fact(self):
        # #300 cannot be related to itself, so there is no relationship to offer.
        self._checkout("300-the-target-issues-own-branch")
        envelope = self._envelope(issue=300, fixture_case="prep_drafter_revise_ambient_self")
        self.assertEqual(envelope["target"]["number"], 300)
        self.assertIsNone(envelope["ambient"])

    def test_ambient_lookup_costs_exactly_one_extra_call(self):
        # The run below succeeds AND produces the fact, so both fixtured calls were made; the
        # manifest length is then the whole budget (any third call would MISS).
        self._checkout("epic/95-public-patient-funnel")
        envelope = self._envelope(fixture_case="prep_drafter_new_ambient_epic")
        self.assertIsNotNone(envelope["ambient"])
        manifest = json.loads(
            (shimenv.fixture_case_dir("prep_drafter_new_ambient_epic") / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        # One over `prep_drafter_new_happy`'s single labels call.
        self.assertEqual(len(manifest), 2)


# ---------------------------------------------------------------------------
# DoD box 4a: decision codes.
# ---------------------------------------------------------------------------


class DecisionCodeTests(PrepDrafterSandboxTestCase):
    """These assert decision codes, not the scratch path, so each run stages to the per-test
    tempdir (`self.scratch`): the default `/tmp/gh-drafter-<N>/` is fixed, and a parallel run
    must never share it with another class (`ScratchDirTests` owns the default-path cases)."""

    def test_auth_required_on_labels_call(self):
        result = self._run(
            ["octo/widgets", "--scratch-dir", self.scratch],
            fixture_case="prep_drafter_auth_required_labels",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AUTH_REQUIRED")

    def test_duplicate_plan_comments_yields_marker_ambiguous(self):
        result = self._run(
            ["octo/widgets", "--issue", "601", "--scratch-dir", self.scratch],
            fixture_case="prep_drafter_marker_ambiguous",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")

    def test_malformed_open_questions_section_yields_ambiguous(self):
        result = self._run(
            ["octo/widgets", "--issue", "502", "--scratch-dir", self.scratch],
            fixture_case="prep_drafter_oq_malformed",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")


# ---------------------------------------------------------------------------
# DoD box 4b: conformance + call budget (new mode is a strict lower bound relative to the
# standalone-revise canonical path).
# ---------------------------------------------------------------------------


class SingleInvocationBudgetTests(PrepDrafterSandboxTestCase):
    """Call counts, not the scratch path: each run stages to the per-test tempdir (`self.scratch`),
    so the revise case never shares `/tmp/gh-drafter-300` with `ScratchDirTests` in a parallel run."""

    def test_new_mode_call_count_is_exactly_one(self):
        result = self._run(
            ["octo/widgets", "--scratch-dir", self.scratch], fixture_case="prep_drafter_new_happy"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        manifest = json.loads(
            (shimenv.fixture_case_dir("prep_drafter_new_happy") / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest), 1)

    def test_revise_mode_call_count_is_exactly_five(self):
        result = self._run(
            ["octo/widgets", "--issue", "300", "--scratch-dir", self.scratch],
            fixture_case="prep_drafter_revise_standard",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        manifest = json.loads(
            (shimenv.fixture_case_dir("prep_drafter_revise_standard") / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(manifest), 5)
        self.assertGreater(len(manifest), 1, "revise mode must cost strictly more than new mode")

    def test_no_gh_call_is_repeated_within_one_run(self):
        for case in ("prep_drafter_new_happy", "prep_drafter_revise_standard"):
            manifest = json.loads(
                (shimenv.fixture_case_dir(case) / "manifest.json").read_text(encoding="utf-8")
            )
            argv_tuples = [tuple(entry["argv"]) for entry in manifest]
            self.assertEqual(
                len(argv_tuples), len(set(argv_tuples)), "duplicate argv in manifest for %s" % case
            )


# ---------------------------------------------------------------------------
# Pure-helper unit tests — no subprocess, no shim, no git sandbox needed.
# ---------------------------------------------------------------------------


class PureHelperUnitTests(unittest.TestCase):
    def test_detect_type_epic_by_label(self):
        self.assertEqual(prep_drafter._detect_type(["epic"], "Epic: x"), "epic")

    def test_detect_type_epic_by_title_prefix_case_insensitive(self):
        self.assertEqual(prep_drafter._detect_type([], "EPIC: something big"), "epic")

    def test_detect_type_story_by_label(self):
        self.assertEqual(prep_drafter._detect_type(["story"], "Add a thing"), "story")

    def test_detect_type_question_by_label(self):
        self.assertEqual(prep_drafter._detect_type(["question", "audience:business"], "Q?"), "question")

    def test_detect_type_standard_default(self):
        self.assertEqual(prep_drafter._detect_type(["bug"], "Fix the thing"), "standard")

    def test_suggested_playbook_new(self):
        self.assertEqual(prep_drafter._suggested_playbook("new", None), "new.md")

    def test_suggested_playbook_revise_question(self):
        self.assertEqual(prep_drafter._suggested_playbook("revise", "question"), "question.md")

    def test_suggested_playbook_revise_standard(self):
        self.assertEqual(prep_drafter._suggested_playbook("revise", "standard"), "revise.md")

    def test_suggested_playbook_revise_story(self):
        # Story revise shares the standalone revise flow (SKILL.md's "Special case — revising a
        # Story" is a special case WITHIN Revise mode, not its own playbook).
        self.assertEqual(prep_drafter._suggested_playbook("revise", "story"), "revise.md")

    def test_prd_presence_absent_when_no_prd_role_is_declared(self):
        result = prep_drafter._prd_presence(
            [{"path": "docs/guides/style.md", "role": "guide", "present": True, "abs_path": "/x"}]
        )
        self.assertFalse(result["present"])
        self.assertIsNone(result["path"])

    def test_prd_presence_absent_when_the_declared_prd_is_missing(self):
        """A declared-but-missing PRD must report absent, not hand the playbook a `None` path it
        would then try to `Read`."""
        result = prep_drafter._prd_presence(
            [{"path": "docs/prd.md", "role": "prd", "present": False, "abs_path": None}]
        )
        self.assertFalse(result["present"])
        self.assertIsNone(result["path"])

    def test_prd_presence_uses_the_declared_path(self):
        result = prep_drafter._prd_presence(
            [{"path": "docs/prd.md", "role": "prd", "present": True, "abs_path": "/repo/docs/prd.md"}]
        )
        self.assertTrue(result["present"])
        self.assertEqual(result["path"], "/repo/docs/prd.md")
        self.assertIsNone(result["members"])

    def test_prd_presence_carries_a_directory_entrys_members(self):
        """A `prd` entry naming a directory hands the PRD-tension step its member files, never a bare
        directory path to `Read`."""
        members = ["docs/prd/billing.md", "docs/prd/onboarding.md"]
        result = prep_drafter._prd_presence(
            [
                {
                    "path": "docs/prd/",
                    "role": "prd",
                    "present": True,
                    "kind": "dir",
                    "members": members,
                    "abs_path": "/repo/docs/prd",
                }
            ]
        )
        self.assertTrue(result["present"])
        self.assertEqual(result["members"], members)

    def test_template_inventory_absent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = prep_drafter._template_inventory(tmp)
            self.assertFalse(result["present"])
            self.assertEqual(result["files"], [])

    def test_template_inventory_empty_dir_reports_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
            result = prep_drafter._template_inventory(tmp)
            self.assertFalse(result["present"])

    def test_build_attention_closed_target_only_for_revise_modes(self):
        target = {"number": 9, "state": "CLOSED"}
        self.assertEqual(prep_drafter._build_attention([], target, "revise"), [
            "target issue #9 is closed — resolve the 'Closed issue' gate before revising"
        ])
        self.assertEqual(prep_drafter._build_attention([], target, "new"), [])


class ScratchDirTests(PrepDrafterSandboxTestCase):
    """The default `facts.scratch` — `/tmp/gh-<skill>-<N>/` (architecture.md §9), where `<N>` is the
    session's key. Revise mode keys on the issue number; new mode has none, so it keys on the process
    id: a follow-up batch spawns its proxy-filed drafters concurrently, and a shared new-mode dir
    would let one run's staged body clobber another's before `gh_persist.py` reads it. These call the
    script WITHOUT `--scratch-dir` (which `_envelope` always passes), so prep really does mkdir the
    emitted path — hence the cleanups."""

    def _default_scratch(self, args, fixture_case):
        """Cleanup is registered ONLY for a dir this run created. The revise-mode default is a fixed
        path (`/tmp/gh-drafter-300`), so an unconditional rmtree would delete a real drafter session's
        staged body if an operator happened to be revising that issue while the suite ran."""
        preexisting = os.path.isdir(self._predicted_path(args))
        result = self._run([a for a in args], fixture_case=fixture_case)
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        scratch = _parse_one_envelope(result.stdout)["scratch"]
        if not preexisting:
            self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        return scratch

    @staticmethod
    def _predicted_path(args):
        """The revise-mode default only; new mode is PID-keyed to this run's own subprocess, so it
        can never pre-exist."""
        if "--issue" in args:
            return "/tmp/gh-drafter-%s" % args[args.index("--issue") + 1]
        return ""

    def test_new_mode_default_is_keyed_on_the_process(self):
        scratch = self._default_scratch(
            ["octo/widgets", "--root", str(self.root)], "prep_drafter_new_happy"
        )
        self.assertRegex(scratch, r"^/tmp/gh-drafter-new-\d+$")

    def test_two_concurrent_new_mode_runs_never_share_a_staging_dir(self):
        """The whole point: two drafters filing one follow-up batch stage to different paths."""
        first = self._default_scratch(
            ["octo/widgets", "--root", str(self.root)], "prep_drafter_new_happy"
        )
        second = self._default_scratch(
            ["octo/widgets", "--root", str(self.root)], "prep_drafter_new_happy"
        )
        self.assertNotEqual(first, second)

    def test_revise_mode_default_is_keyed_on_the_issue(self):
        scratch = self._default_scratch(
            ["octo/widgets", "--root", str(self.root), "--issue", "300"],
            "prep_drafter_revise_standard",
        )
        self.assertEqual(scratch, "/tmp/gh-drafter-300")


class UsageErrorTests(unittest.TestCase):
    def test_missing_repo_arg_is_usage_error(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, encoding="utf-8", check=False
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


class ReviseDodFactTests(unittest.TestCase):
    """`facts.revise.dod`: the revise renames a checklist under another heading to `## Definition of
    done` — except on a deliverable slice, whose `## Acceptance criteria` is the slicer's. Telling a
    slice from a story costs one `gh` read, so it is made only when the rename would fire."""

    AC_BODY = "## Acceptance criteria\n- [ ] a\n"
    PARENT = {"parent": {"number": 41}}

    def _dod(self, body, target, kind=None, notice=None):
        calls = []

        def fake_classify(repo, number, cwd=None):
            calls.append(number)
            return kind, notice

        with mock.patch.object(prep_drafter.branching, "classify_parent", fake_classify):
            dod, got_notice = prep_drafter._build_revise_dod(body, target, "octo/widgets")
        return dod, got_notice, calls

    def test_a_dod_body_never_looks_up_the_parent(self):
        dod, _, calls = self._dod("## Definition of done\n- [ ] a\n", self.PARENT)
        self.assertEqual((dod["present"], dod["slice"], calls), (True, False, []))

    def test_no_parent_means_no_lookup_and_not_a_slice(self):
        dod, _, calls = self._dod(self.AC_BODY, {"parent": None})
        self.assertEqual((dod["slice"], calls), (False, []))
        self.assertEqual(dod["other_checklists"], [{"heading": "Acceptance criteria", "count": 1}])

    def test_a_story_parent_marks_a_slice(self):
        dod, _, calls = self._dod(self.AC_BODY, self.PARENT, kind=prep_drafter.branching.PARENT_KIND_NON_EPIC)
        self.assertEqual((dod["slice"], calls), (True, [41]))

    def test_an_epic_parent_is_a_story_not_a_slice(self):
        dod, _, _ = self._dod(self.AC_BODY, self.PARENT, kind=prep_drafter.branching.PARENT_KIND_EPIC)
        self.assertIs(dod["slice"], False)

    def test_an_unknown_parent_is_null_with_its_notice(self):
        dod, notice, _ = self._dod(
            self.AC_BODY, self.PARENT,
            kind=prep_drafter.branching.PARENT_KIND_UNKNOWN,
            notice=prep_drafter.branching.PARENT_KIND_UNAVAILABLE,
        )
        self.assertIsNone(dod["slice"])
        self.assertEqual(notice, prep_drafter.branching.PARENT_KIND_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
