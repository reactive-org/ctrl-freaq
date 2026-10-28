"""Unit tests for scripts/prep_question_resolver.py — the question-resolver skill's complete facts block
in one call (architecture.md §4; docs/implementation.md S18; docs/specs/question-resolver.md).

Test topology (mirrors tests/test_prep_researcher.py — the S8-locked composition pattern): `gh` calls go
through the offline shim (`tests/support/shimenv`); the single `git` call this prep needs (`git rev-parse
HEAD` for the informational `root.sha`) goes through a real temp origin+clone (`tests/support/gitsandbox`).
`prep_question_resolver.py` is driven as a real subprocess so every test exercises the full argv-in /
subprocess / envelope-out path a real caller uses. Every fixture origin is a local git sandbox — hermetic.

Coverage matrix (S18 DoD — the reentrancy trio + guards + budget):
- `reentrancy.mode` ×2: fresh (no decision marker) / revise (one `<!-- question-decision:v1 -->` marker,
  `prior_decision.comment_id` staged for the flow's `--delete-marker-id` replace).
- The >1-marker case: the gather's `MARKER_AMBIGUOUS` is forwarded verbatim (v1's "which decision is
  current" DECISION_NEEDED) — a `needs_decision` envelope, not a fresh/revise fact.
- The not-a-`question`-issue guard (`is_question: false` + an attention line — a fact, not a decision).
- Already-closed (`state == CLOSED`) is a fact; the native `blocking` list (the `## Unblocks` source).
- Conformance on every emitting path + the two-sided call budget (one gather round-trip = 3 gh calls).
- `AUTH_REQUIRED` from the gather's first gh call.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "prep_question_resolver.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_question_resolver  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _parse_one_envelope(stdout_text):
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(
            "expected exactly one non-blank stdout line (the envelope), got %d: %r"
            % (len(lines), lines)
        )
    return json.loads(lines[0])


class PrepQuestionResolverTestCase(unittest.TestCase):
    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    def _run(self, args, fixture_case=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def _facts(self, issue, fixture_case):
        proc = self._run(
            [str(issue), "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case=fixture_case,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        return _parse_one_envelope(proc.stdout)

    # ---- reentrancy trio ----

    def test_fresh_mode_no_prior_decision(self):
        env = self._facts(301, "prep_question_resolver_fresh")
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "ok")
        facts = env
        self.assertTrue(facts["is_question"])
        self.assertEqual(facts["reentrancy"]["mode"], "fresh")
        self.assertEqual(facts["reentrancy"]["marker_comment_count"], 0)
        self.assertFalse(facts["reentrancy"]["marker_comment_present"])
        self.assertNotIn("prior_decision", facts["reentrancy"])
        self.assertFalse(facts["already_closed"])

    def test_revise_mode_stages_prior_decision_id(self):
        facts = self._facts(302, "prep_question_resolver_revise")
        self.assertEqual(facts["reentrancy"]["mode"], "revise")
        self.assertEqual(facts["reentrancy"]["marker_comment_count"], 1)
        self.assertTrue(facts["reentrancy"]["marker_comment_present"])
        prior = facts["reentrancy"]["prior_decision"]
        self.assertTrue(prior["present"])
        # The comment_id is what the flow passes to gh_persist.py comment --delete-marker-id.
        self.assertEqual(prior["comment_id"], 8001)
        self.assertIn("comment_url", prior)

    def test_an_untrusted_decision_marker_is_not_a_decision(self):
        # #179: a `<!-- question-decision:v1 -->` by an untrusted author records nothing.
        facts = self._facts(302, "prep_question_resolver_revise_untrusted")
        self.assertEqual(facts["reentrancy"]["mode"], "fresh")
        self.assertFalse(facts["reentrancy"]["marker_comment_present"])
        notices = [n for n in facts["notices"] if n.startswith("MARKER_UNTRUSTED")]
        self.assertEqual(len(notices), 1)
        self.assertIn("<!-- question-decision:v1 -->", notices[0])

    def test_more_than_one_marker_forwards_marker_ambiguous(self):
        # >1 decision comment: the gather emits MARKER_AMBIGUOUS (v1's DECISION_NEEDED "which decision
        # is current"), which prep forwards verbatim — a needs_decision envelope, not a fresh/revise fact.
        proc = self._run(
            ["303", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_question_resolver_ambiguous",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        env = _parse_one_envelope(proc.stdout)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "needs_decision")
        self.assertEqual(env["decision"]["code"], "MARKER_AMBIGUOUS")

    # ---- grounding docs (the constraint audit's doc set) ----

    def _seed_catalogue(self, interior):
        docs = Path(self.root) / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "README.md").write_text(
            "<!-- doc-catalogue -->\n%s<!-- /doc-catalogue -->\n" % interior, encoding="utf-8"
        )

    def test_catalogue_is_read_at_the_root_working_tree(self):
        """The constraint audit's doc set is the repo's declared catalogue — until 4.27.0 a hardcoded
        `docs/constitution.md` / `docs/prd.md` / … list. Read at the working tree, uncommitted edits
        included (a question isn't tied to a branch)."""
        self._seed_catalogue("- `docs/constitution.md` — constitution — binding — The rules.\n")
        (Path(self.root) / "docs" / "constitution.md").write_text("# Rules\n", encoding="utf-8")
        facts = self._facts(301, "prep_question_resolver_fresh")
        (entry,) = facts["grounding_docs"]
        self.assertEqual(entry["role"], "constitution")
        self.assertTrue(entry["present"])
        self.assertEqual(facts["notices"], [])
        self.assertEqual([a for a in facts["attention"] if "catalogue" in a], [])

    def test_absent_catalogue_is_attention_and_a_notice_and_the_audit_proceeds(self):
        facts = self._facts(301, "prep_question_resolver_fresh")
        self.assertEqual(facts["status"], "ok")
        self.assertEqual(facts["grounding_docs"], [])
        self.assertIn("DOC_CATALOGUE_ABSENT", facts["notices"])
        self.assertTrue(
            any("only the docs the question cites" in a for a in facts["attention"]),
            facts["attention"],
        )

    def test_a_non_question_issue_reads_no_catalogue(self):
        facts = self._facts(304, "prep_question_resolver_not_question")
        self.assertEqual(facts["grounding_docs"], [])
        self.assertNotIn("DOC_CATALOGUE_ABSENT", facts["notices"])
        self.assertEqual([a for a in facts["attention"] if "catalogue" in a], [])

    # ---- guards / facts ----

    def test_not_a_question_issue_is_a_fact_not_a_decision(self):
        facts = self._facts(304, "prep_question_resolver_not_question")
        self.assertEqual(facts["status"], "ok")  # a fact, never a needs_decision
        self.assertFalse(facts["is_question"])
        self.assertTrue(
            any("not a `question` issue" in a for a in facts["attention"]),
            "expected an attention line pointing the operator at the resolver",
        )

    def test_native_blocking_list_surfaced_for_unblocks(self):
        facts = self._facts(301, "prep_question_resolver_fresh")
        self.assertEqual([b["number"] for b in facts["blocking"]], [350])
        self.assertTrue(
            any("natively blocks" in a and "#350" in a for a in facts["attention"]),
            "the native blocking list must be surfaced (the ## Unblocks source)",
        )

    def test_already_closed_is_a_fact(self):
        facts = self._facts(305, "prep_question_resolver_closed")
        self.assertTrue(facts["already_closed"])
        # A closed question with a prior decision is still a revise (the flow revises in place).
        self.assertEqual(facts["reentrancy"]["mode"], "revise")

    def test_target_and_sections_shape(self):
        facts = self._facts(301, "prep_question_resolver_fresh")
        self.assertEqual(facts["target"]["number"], 301)
        self.assertEqual(facts["target"]["kind"], "issue")
        self.assertIn("question", facts["target"]["labels"])
        # staged verbatim sections (spill contract) present + well-formed.
        envelope_asserts.assert_spill_sections_well_formed(
            facts["sections"], ("issue_body", "thread")
        )

    def test_root_sha_recorded(self):
        facts = self._facts(301, "prep_question_resolver_fresh")
        self.assertRegex(facts["root"]["sha"] or "", r"^[0-9a-f]{40}$")

    # ---- decision path ----

    def test_auth_required_forwarded(self):
        proc = self._run(
            ["306", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_question_resolver_auth",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        env = _parse_one_envelope(proc.stdout)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "needs_decision")
        self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")

    # ---- budget ----

    def test_budget_is_exactly_one_gather_roundtrip(self):
        # A single question = one gh_gather round-trip = 3 gh calls (view + comments + pr list). The
        # run succeeds only if it makes exactly those (an extra call misses the shim loudly).
        manifest = json.loads(
            (shimenv.fixture_case_dir("prep_question_resolver_fresh") / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(manifest), 3)
        facts = self._facts(301, "prep_question_resolver_fresh")
        self.assertEqual(facts["status"], "ok")


if __name__ == "__main__":
    unittest.main()
