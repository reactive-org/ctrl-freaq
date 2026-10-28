"""Unit tests for scripts/prep_question_sweep.py — the question-sweep skill's complete facts block in
one call (architecture.md §4, §9.2; docs/implementation.md S18; docs/specs/question-sweep.md).

Test topology (mirrors tests/test_prep_researcher.py + test_prep_setup.py): the registry `gh issue list`
and the per-open-question `gh_gather` round-trips go through the offline shim; the single `git` call
(`git rev-parse HEAD` for the informational `root.sha`) and the local doc-inventory + config-block reads
run against a real temp origin+clone (`tests/support/gitsandbox`) with `docs/` and a `CLAUDE.md`
`<!-- drafter-open-question-markers -->` block written into it. The prep is driven as a real subprocess.

Coverage matrix (S18 DoD box 1 — the Tier-1 status join trio + the Tier-2 flag):
- Tier-1 `status` ×3 in one registry: `closed` (resolved, no fetch) / `decision-marked` (open + a
  `<!-- question-decision:v1 -->` comment, resolved) / `still-open` (open, no marker → `tier2_needed`).
- The `ambiguous` per-entry status (>1 decision comment → recorded + attention, never a whole-sweep abort).
- The two-sided budget: 1 registry list + one gather (3 gh calls) per OPEN question; closed costs nothing.
- Detection inputs: the config block raw (`heuristics_active` false) vs absent (heuristics_active true);
  the docs inventory in scope.
- `AUTH_REQUIRED` from the registry list; conformance on every emitting path.
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
SCRIPT = SCRIPTS_DIR / "prep_question_sweep.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_question_sweep  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _parse_one_envelope(stdout_text):
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(
            "expected exactly one non-blank stdout line (the envelope), got %d: %r"
            % (len(lines), lines)
        )
    return json.loads(lines[0])


class PrepQuestionSweepTestCase(unittest.TestCase):
    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)
        # A docs/ tree in scope + one governing doc, so `docs.present` is true by default.
        _write(self.root / "docs" / "prd.md", "# PRD\n\n## Open questions\n- PRD-OQ-02 TBD\n")
        _write(self.root / "docs" / "design" / "notes.md", "PROVISIONAL — sort order\n")

    def _run(self, args, fixture_case=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def _facts(self, fixture_case, extra=None):
        args = ["octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch]
        if extra:
            args += extra
        proc = self._run(args, fixture_case=fixture_case)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        return _parse_one_envelope(proc.stdout)

    def _question(self, facts, number):
        for q in facts["registry"]["questions"]:
            if q["number"] == number:
                return q
        raise AssertionError("question #%s not in registry" % number)

    # ---- Tier-1 status join trio (DoD box 1) ----

    def test_tier1_status_join_trio(self):
        facts = self._facts("prep_question_sweep_tier1")
        envelope_asserts.assert_full_envelope_conformance(facts)
        self.assertEqual(facts["status"], "ok")
        self.assertEqual(facts["registry"]["count"], 3)

        closed = self._question(facts, 201)
        self.assertEqual(closed["status"], "closed")
        self.assertTrue(closed["resolved"])
        self.assertFalse(closed["tier2_needed"])
        # A closed question is Tier-1 resolved from `state` alone — no marker fetch, no staged sections.
        self.assertNotIn("sections", closed)

        marked = self._question(facts, 202)
        self.assertEqual(marked["status"], "decision-marked")
        self.assertTrue(marked["resolved"])
        self.assertFalse(marked["tier2_needed"])
        self.assertTrue(marked["marker_comment_present"])

        still_open = self._question(facts, 203)
        self.assertEqual(still_open["status"], "still-open")
        self.assertFalse(still_open["resolved"])
        self.assertTrue(still_open["tier2_needed"], "still-open sets the Tier-2 needed flag")
        # The open ones stage body/thread so the router's Tier-2 reader reads without a re-fetch.
        self.assertIn("sections", still_open)

    def test_tier2_needed_surfaced_in_attention(self):
        facts = self._facts("prep_question_sweep_tier1")
        self.assertTrue(
            any("Tier-2 thread read" in a and "#203" in a for a in facts["attention"]),
            "still-open questions must be surfaced for the router's Tier-2 dispatch",
        )

    def test_ambiguous_marker_is_recorded_not_aborted(self):
        # >1 decision comment on one question is recorded as status=ambiguous + attention; it must NOT
        # abort the whole project-wide sweep (the router surfaces it, never auto-resolves).
        facts = self._facts("prep_question_sweep_ambiguous")
        self.assertEqual(facts["status"], "ok")
        q = self._question(facts, 204)
        self.assertEqual(q["status"], "ambiguous")
        self.assertFalse(q["resolved"])
        self.assertEqual(q["marker_comment_count"], 2)
        self.assertTrue(any("more than one" in a and "#204" in a for a in facts["attention"]))

    # ---- detection inputs ----

    def test_detection_config_block_read_when_present(self):
        _write(
            self.root / "CLAUDE.md",
            "# Repo\n\n<!-- drafter-open-question-markers -->\n"
            "- register: `docs/prd.md`\n- inline: `PROVISIONAL`\n"
            "<!-- /drafter-open-question-markers -->\n",
        )
        facts = self._facts("prep_question_sweep_empty_registry")
        det = facts["detection"]
        self.assertTrue(det["oq_markers"]["present"])
        self.assertIn("register", det["oq_markers"]["raw"])
        self.assertFalse(det["heuristics_active"])

    def test_detection_heuristics_active_when_block_absent(self):
        facts = self._facts("prep_question_sweep_empty_registry")
        self.assertTrue(facts["detection"]["heuristics_active"])
        self.assertIsNone(facts["detection"]["oq_markers"]["raw"])

    def test_docs_inventory_default_scope(self):
        facts = self._facts("prep_question_sweep_empty_registry")
        self.assertTrue(facts["docs"]["present"])
        self.assertIn("docs/prd.md", facts["docs"]["files"])
        self.assertIn("docs/design/notes.md", facts["docs"]["files"])

    def test_empty_registry_and_docs_present(self):
        facts = self._facts("prep_question_sweep_empty_registry")
        self.assertEqual(facts["registry"]["count"], 0)
        self.assertEqual(facts["registry"]["questions"], [])

    # ---- budget ----

    def test_budget_one_list_plus_three_per_open_question(self):
        # 3 questions (1 closed + 2 open): 1 registry list + 2×(gather = 3 gh calls) = 7. The run
        # succeeds only if it makes exactly those (an extra call misses the shim loudly); a closed
        # question costs nothing (Tier-1 short-circuit).
        manifest = json.loads(
            (shimenv.fixture_case_dir("prep_question_sweep_tier1") / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(manifest), 7)
        facts = self._facts("prep_question_sweep_tier1")
        self.assertEqual(facts["status"], "ok")

    # ---- decision path ----

    def test_auth_required_from_registry_list_forwarded(self):
        proc = self._run(
            ["octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_question_sweep_auth",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        env = _parse_one_envelope(proc.stdout)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "needs_decision")
        self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")

    def test_root_sha_recorded(self):
        facts = self._facts("prep_question_sweep_empty_registry")
        self.assertRegex(facts["root"]["sha"] or "", r"^[0-9a-f]{40}$")



class Tier1UntrustedNoticeTests(unittest.TestCase):
    """#179: a Tier-1 gather's `MARKER_UNTRUSTED` notice (a decision marker an untrusted author wrote)
    is forwarded, once, and no other gather notice is."""

    def test_only_the_untrusted_marker_notice_is_forwarded(self):
        from unittest import mock

        note = "MARKER_UNTRUSTED: 1 comment starting with `<!-- question-decision:v1 -->` by an untrusted author ignored (issue #5)"
        envelope = {"status": "ok", "notices": ["DEPS_UNSUPPORTED", note], "marker_comment_present": False}
        notices = []
        with tempfile.TemporaryDirectory() as scratch, \
                mock.patch.object(prep_question_sweep.gh_gather, "run", return_value=(0, envelope)):
            join, fatal = prep_question_sweep._tier1_for_open_question({"number": 5}, "o/r", scratch, None, notices)
            prep_question_sweep._tier1_for_open_question({"number": 5}, "o/r", scratch, None, notices)
        self.assertIsNone(fatal)
        self.assertEqual(join["status"], "still-open")
        self.assertEqual(notices, [note])


if __name__ == "__main__":
    unittest.main()
