"""Unit tests for scripts/prep_requirements_gatherer.py — the requirements-gatherer's facts block
in one call (architecture.md §4).

Two strategies, matching the sibling prep suites:

  - **Pure-core unit tests** call the module's own non-emitting helpers directly (the refusal
    set, the DoD facts, the blocker filter) — fast, and they pin the exact arithmetic without a
    subprocess.
  - **Subprocess tests** drive `prep_requirements_gatherer.py` as a real process against the
    fixture-replaying `gh` shim, proving the envelope contract and the one-round-trip budget end
    to end.

What this suite exists to protect:

  - **The refusal set is a fact, not a decision code.** Four conditions must land in
    `vector.refusals` with `suggested_playbook: None`, never as a `needs_decision`.
  - **An open blocker is attention, never a refusal** — elicitation is upstream human input.
  - **The DoD facts carry the append point.** `bullet_count` and `annotated_count` are what the
    flow appends after and warns on — parsed once here, never re-counted in prose.
  - **A malformed DoD blocks.** `DOD_MALFORMED` is forwarded from the parse core before anything
    could be appended to a section the parser cannot index.
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
SCRIPT = SCRIPTS_DIR / "prep_requirements_gatherer.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_requirements_gatherer as prep  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib.decisions import DOC_CATALOGUE_ABSENT, DOD_MALFORMED  # noqa: E402
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402

CATALOGUE_INTERIOR = "- `docs/prd.md` — prd — binding — What the product is.\n"


def _write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
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


# ---------------------------------------------------------------------------
# Pure cores
# ---------------------------------------------------------------------------


class RefusalTests(unittest.TestCase):
    def test_no_refusal_for_an_open_story_under_an_epic(self):
        self.assertEqual(prep._build_refusals("story", "OPEN", {"type": "epic"}), [])

    def test_no_refusal_for_a_standalone_non_epic_issue(self):
        self.assertEqual(prep._build_refusals("standard", "OPEN", None), [])

    def test_epic_target_refused(self):
        self.assertEqual(
            prep._build_refusals("epic", "OPEN", None), [prep.REFUSAL_EPIC_TARGET]
        )

    def test_slice_target_refused_when_the_parent_is_not_an_epic(self):
        """A slice carries the slicer's acceptance criteria, never a DoD."""
        for parent_type in ("story", "standard"):
            self.assertEqual(
                prep._build_refusals("standard", "OPEN", {"type": parent_type}),
                [prep.REFUSAL_SLICE_TARGET],
                parent_type,
            )

    def test_question_and_closed_targets_refused(self):
        self.assertIn(
            prep.REFUSAL_QUESTION_TARGET, prep._build_refusals("question", "OPEN", None)
        )
        self.assertIn(
            prep.REFUSAL_CLOSED_TARGET, prep._build_refusals("standard", "CLOSED", None)
        )

    def test_refusal_order_is_stable_for_deterministic_rendering(self):
        refusals = prep._build_refusals("epic", "CLOSED", {"type": "story"})
        self.assertEqual(
            refusals,
            [
                prep.REFUSAL_EPIC_TARGET,
                prep.REFUSAL_SLICE_TARGET,
                prep.REFUSAL_CLOSED_TARGET,
            ],
        )


class BlockerFilterTests(unittest.TestCase):
    def test_only_open_native_blockers_surface(self):
        nodes = [
            {"number": 61, "state": "OPEN", "title": "Which provider?", "url": "u1"},
            {"number": 62, "state": "CLOSED", "title": "Answered", "url": "u2"},
        ]
        self.assertEqual([b["number"] for b in prep._open_blockers(nodes)], [61])

    def test_an_open_blocker_is_never_a_refusal(self):
        """Elicitation is upstream human input — the refusal builder doesn't even see blockers."""
        self.assertEqual(prep._build_refusals("standard", "OPEN", None), [])


class ReqIdTests(unittest.TestCase):
    """The stable `**REQ-<issue>-<seq>**` prefix — issue-minted, append-only."""

    def test_own_issue_id_parses_and_contributes_its_seq(self):
        self.assertEqual(
            prep._req_id_facts("**REQ-103-4** — criterion — docs/prd.md §1", 103),
            ("REQ-103-4", 4),
        )

    def test_a_foreign_issue_id_is_reported_but_never_hijacks_the_sequence(self):
        """The slicer's designator guard, applied to ids: a copy-pasted sibling bullet must not
        drive this issue's numbering."""
        self.assertEqual(
            prep._req_id_facts("**REQ-999-9** — pasted from elsewhere", 103),
            ("REQ-999-9", None),
        )

    def test_an_id_less_bullet_is_legitimate(self):
        self.assertEqual(prep._req_id_facts("plain drafter-written criterion", 103), (None, None))

    def test_next_req_seq_continues_from_the_highest_own_issue_id(self):
        body = (
            "## Definition of done\n"
            "- [ ] **REQ-103-1** — first — docs/prd.md §1\n"
            "- [ ] plain drafter bullet\n"
            "- [ ] **REQ-103-3** — third (operator dropped 2) — operator elicited 2026-08-07\n"
        )
        dod, decision = prep._parse_dod(body, 103)
        self.assertIsNone(decision)
        self.assertEqual(dod["next_req_seq"], 4)
        self.assertEqual(
            [b["req_id"] for b in dod["bullets"]], ["REQ-103-1", None, "REQ-103-3"]
        )


class DodFactsTests(unittest.TestCase):
    def test_no_section_reports_absent_with_zero_counts(self):
        dod, decision = prep._parse_dod("## Description\njust prose\n", 7)
        self.assertIsNone(decision)
        self.assertEqual(
            dod,
            {
                "present": False,
                "other_checklists": [],
                "bullet_count": 0,
                "annotated_count": 0,
                "next_req_seq": 1,
                "bullets": [],
            },
        )

    def test_a_checklist_under_another_heading_is_reported_not_adopted(self):
        """The criteria live under `## Acceptance criteria`: the fact names them, and the parsed
        bullets stay empty — appending a second checklist is what gather.md must not do."""
        body = "## Acceptance criteria\n- [ ] one\n- [ ] two\n"
        dod, decision = prep._parse_dod(body, 7)
        self.assertIsNone(decision)
        self.assertFalse(dod["present"])
        self.assertEqual(dod["bullets"], [])
        self.assertEqual(
            dod["other_checklists"], [{"heading": "Acceptance criteria", "count": 2}]
        )

    def test_a_misplaced_checklist_surfaces_the_drafter_route(self):
        dod, _ = prep._parse_dod("## Acceptance criteria\n- [ ] one\n", 7)
        attention = prep._build_attention(
            {"number": 7}, [], dod, {"present": False}, [], False, []
        )
        self.assertTrue(
            any("`## Acceptance criteria` (1 bullet)" in i and "drafter revise #7" in i
                for i in attention),
            attention,
        )

    def test_a_refused_slice_gets_no_rename_route(self):
        # A slice's `## Acceptance criteria` is the slicer's by design — never "rename it".
        dod, _ = prep._parse_dod("## Acceptance criteria\n- [ ] one\n", 7)
        attention = prep._build_attention(
            {"number": 7}, [prep.REFUSAL_SLICE_TARGET], dod, {"present": False}, [], False, []
        )
        self.assertFalse(any("drafter revise" in i for i in attention), attention)

    def test_present_section_with_plain_and_annotated_bullets(self):
        body = (
            "## Definition of done\n"
            "- [x] shipped thing (closed by phase 1, commit ab12cd3)\n"
            "- [ ] pending thing\n"
            "  - a detail sub-bullet, not a DoD item\n"
        )
        dod, decision = prep._parse_dod(body, 7)
        self.assertIsNone(decision)
        self.assertTrue(dod["present"])
        self.assertEqual(dod["bullet_count"], 2)
        self.assertEqual(dod["annotated_count"], 1)
        self.assertEqual([b["index"] for b in dod["bullets"]], [1, 2])
        self.assertTrue(dod["bullets"][0]["ticked"])
        self.assertFalse(dod["bullets"][1]["ticked"])

    def test_an_empty_section_is_present_with_no_bullets(self):
        """The create-vs-append fact: `present` must not be conflated with `bullet_count == 0`."""
        dod, decision = prep._parse_dod("## Definition of done\n\n(nothing yet)\n", 7)
        self.assertIsNone(decision)
        self.assertTrue(dod["present"])
        self.assertEqual(dod["bullet_count"], 0)

    def test_an_em_dash_provenance_tail_is_bullet_text_not_an_annotation(self):
        """The grammar this skill writes must survive its own prep's re-parse."""
        body = (
            "## Definition of done\n"
            "- [ ] sessions expire after 30 minutes, kiosk exempt — docs/prd.md §4.2\n"
            "- [ ] bulk import rejects malformed rows per-row — operator elicited 2026-08-07\n"
        )
        dod, decision = prep._parse_dod(body, 7)
        self.assertIsNone(decision)
        self.assertEqual(dod["annotated_count"], 0)
        self.assertIn("§4.2", dod["bullets"][0]["text"])

    def test_a_malformed_annotation_returns_the_dod_malformed_decision(self):
        body = "## Definition of done\n- [x] thing (phase 3 done)\n"
        dod, decision = prep._parse_dod(body, 7)
        self.assertIsNone(dod)
        self.assertEqual(decision["code"], DOD_MALFORMED)


class SuggestedPlaybookTests(unittest.TestCase):
    def test_one_playbook_when_nothing_is_refused(self):
        self.assertEqual(prep._suggested_playbook([]), "gather.md")

    def test_a_refusal_routes_to_no_playbook(self):
        self.assertIsNone(prep._suggested_playbook([prep.REFUSAL_EPIC_TARGET]))


# ---------------------------------------------------------------------------
# Subprocess / envelope
# ---------------------------------------------------------------------------


class PrepGathererSandboxTestCase(unittest.TestCase):
    """A real temp git origin+clone as `--root` (needed for `git rev-parse HEAD`), pre-seeded with
    a doc catalogue so the grounding dimension is neutral in tests that aren't about grounding."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        self._seed_catalogue()
        _write(self.root / "docs" / "prd.md", "# PRD\n")
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    def _seed_catalogue(self, interior=CATALOGUE_INTERIOR):
        _write(
            self.root / "docs" / "README.md",
            "# Docs\n\n<!-- doc-catalogue -->\n%s<!-- /doc-catalogue -->\n" % interior,
        )

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

    def _envelope(self, issue, fixture_case, repo="octo/widgets", extra_args=None):
        args = [str(issue), repo, "--root", str(self.root), "--scratch-dir", self.scratch]
        if extra_args:
            args += extra_args
        result = self._run(args, fixture_case=fixture_case)
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope


class ScriptShapeTests(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(str(SCRIPT), os.X_OK), "a 0644 prep exits 126 at dispatch")

    def test_usage_error_exits_two_with_no_envelope(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, encoding="utf-8", check=False
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout.strip(), "")


class FreshHappyPathTests(PrepGathererSandboxTestCase):
    def test_facts_schema(self):
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        for key in (
            "repo",
            "scratch",
            "root",
            "target",
            "vector",
            "suggested_playbook",
            "dod",
            "plan",
            "grounding_docs",
            "sections",
            "attention",
        ):
            self.assertIn(key, envelope, key)
        self.assertEqual(envelope["vector"], {"type": "story", "refusals": []})
        self.assertEqual(envelope["suggested_playbook"], "gather.md")
        self.assertEqual(envelope["plan"], {"present": False})
        self.assertEqual(envelope["attention"], [])
        self.assertEqual(envelope["notices"], [])

    def test_dod_facts_carry_the_append_point(self):
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        self.assertTrue(envelope["dod"]["present"])
        self.assertEqual(envelope["dod"]["bullet_count"], 2)
        self.assertEqual(envelope["dod"]["annotated_count"], 0)
        self.assertEqual([b["index"] for b in envelope["dod"]["bullets"]], [1, 2])
        self.assertEqual(
            [b["req_id"] for b in envelope["dod"]["bullets"]], ["REQ-103-1", None]
        )
        self.assertEqual(envelope["dod"]["next_req_seq"], 2)

    def test_parent_epic_is_typed_from_its_own_labels(self):
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        self.assertEqual(envelope["target"]["parent"]["number"], 150)
        self.assertEqual(envelope["target"]["parent"]["type"], "epic")

    def test_catalogue_entries_land_as_grounding_docs(self):
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        self.assertEqual([e["path"] for e in envelope["grounding_docs"]], ["docs/prd.md"])
        self.assertTrue(envelope["grounding_docs"][0]["present"])

    def test_body_and_thread_are_spilled_into_sections(self):
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        self.assertTrue(
            any(k.startswith("issue_body") for k in envelope["sections"]), envelope["sections"]
        )


class NoDodTests(PrepGathererSandboxTestCase):
    def test_a_body_without_a_dod_section_is_not_a_refusal(self):
        envelope = self._envelope(200, "prep_requirements_gatherer_no_dod")
        self.assertEqual(envelope["vector"]["refusals"], [])
        self.assertFalse(envelope["dod"]["present"])
        self.assertEqual(envelope["dod"]["bullet_count"], 0)
        self.assertEqual(envelope["suggested_playbook"], "gather.md")

    def test_an_open_blocker_is_attention_never_a_refusal(self):
        envelope = self._envelope(200, "prep_requirements_gatherer_no_dod")
        self.assertEqual([b["number"] for b in envelope["target"]["blocked_by"]], [61])
        self.assertEqual(envelope["vector"]["refusals"], [])
        self.assertTrue(
            any("open blocker #61" in i for i in envelope["attention"]), envelope["attention"]
        )


class MidFlightTests(PrepGathererSandboxTestCase):
    """Both mid-flight signals: annotated bullets AND a bare implementation-plan marker (a
    planned-but-unstarted issue is mid-flight too — the single-phase fallback would tick
    appended bullets on the resolver's next push)."""

    def test_annotated_count_and_the_mid_flight_attention_line(self):
        envelope = self._envelope(104, "prep_requirements_gatherer_annotated")
        self.assertEqual(envelope["dod"]["annotated_count"], 1)
        self.assertTrue(
            any("index stability" in i for i in envelope["attention"]), envelope["attention"]
        )

    def test_a_plan_marker_is_a_mid_flight_signal_even_with_zero_annotations(self):
        envelope = self._envelope(104, "prep_requirements_gatherer_annotated")
        self.assertEqual(envelope["plan"], {"present": True})
        self.assertTrue(
            any("single-phase fallback" in i for i in envelope["attention"]),
            envelope["attention"],
        )


class RefusalFactTests(PrepGathererSandboxTestCase):
    """Every refusal is an `ok` envelope carrying a fact — never a `needs_decision`."""

    def _refusal(self, issue, fixture_case):
        envelope = self._envelope(issue, fixture_case)
        self.assertEqual(envelope["status"], "ok")
        self.assertIsNone(envelope["suggested_playbook"])
        return envelope

    def test_epic_target(self):
        envelope = self._refusal(150, "prep_requirements_gatherer_epic_target")
        self.assertEqual(envelope["vector"]["refusals"], [prep.REFUSAL_EPIC_TARGET])
        self.assertTrue(any("is an epic" in i for i in envelope["attention"]))

    def test_slice_target(self):
        envelope = self._refusal(105, "prep_requirements_gatherer_slice_target")
        self.assertEqual(envelope["vector"]["refusals"], [prep.REFUSAL_SLICE_TARGET])
        self.assertEqual(envelope["target"]["parent"]["type"], "story")
        self.assertTrue(any("acceptance criteria" in i for i in envelope["attention"]))

    def test_question_target(self):
        envelope = self._refusal(202, "prep_requirements_gatherer_question")
        self.assertEqual(envelope["vector"]["refusals"], [prep.REFUSAL_QUESTION_TARGET])

    def test_closed_target(self):
        envelope = self._refusal(203, "prep_requirements_gatherer_closed")
        self.assertEqual(envelope["vector"]["refusals"], [prep.REFUSAL_CLOSED_TARGET])

    def test_a_refusal_wins_over_a_malformed_dod(self):
        """The closed fixture's body carries a malformed annotation on purpose: the refusal must
        surface (dod: null), never a DOD_MALFORMED repair card for an issue the session refuses
        to touch anyway."""
        envelope = self._refusal(203, "prep_requirements_gatherer_closed")
        self.assertIsNone(envelope["dod"])


class DodMalformedTests(PrepGathererSandboxTestCase):
    def test_a_malformed_dod_forwards_the_blocking_decision(self):
        result = self._run(
            ["205", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_requirements_gatherer_dod_malformed",
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], DOD_MALFORMED)


class GroundingTests(PrepGathererSandboxTestCase):
    def test_absent_catalogue_is_a_notice_and_an_attention_line_never_a_refusal(self):
        """This skill is the proceeding kind of catalogue consumer — its output is
        operator-elicited, so an absent catalogue only empties the suggestion gate."""
        self._remove_catalogue()
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertIn(DOC_CATALOGUE_ABSENT, envelope["notices"])
        self.assertEqual(envelope["vector"]["refusals"], [])
        self.assertEqual(envelope["suggested_playbook"], "gather.md")
        self.assertTrue(
            any("no doc catalogue" in i for i in envelope["attention"]), envelope["attention"]
        )

    def test_empty_catalogue_is_not_the_absent_notice(self):
        self._seed_catalogue("")
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        self.assertEqual(envelope["notices"], [])
        self.assertTrue(any("declares no documents" in i for i in envelope["attention"]))

    def test_declared_but_missing_doc_is_surfaced(self):
        self._seed_catalogue(
            "- `docs/prd.md` — prd — binding — What the product is.\n"
            "- `docs/ui-design.md` — ui-design — binding — Not on this ref.\n"
        )
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        self.assertTrue(
            any("docs/ui-design.md" in i for i in envelope["attention"]), envelope["attention"]
        )


class SingleInvocationBudgetTests(PrepGathererSandboxTestCase):
    """architecture.md §9.2: one state-assembly invocation per session. The fixture manifest is
    exact-argv matched, so an unexpected extra `gh` call fails the shim loudly."""

    def test_a_parentless_target_makes_no_parent_lookup(self):
        envelope = self._envelope(200, "prep_requirements_gatherer_no_dod")
        self.assertIsNone(envelope["target"]["parent"])

    def test_a_parented_target_makes_exactly_one_parent_lookup(self):
        envelope = self._envelope(103, "prep_requirements_gatherer_fresh")
        self.assertEqual(envelope["target"]["parent"]["number"], 150)


class ScratchDirTests(PrepGathererSandboxTestCase):
    def test_default_scratch_dir_follows_the_house_convention(self):
        """`/tmp/gh-<skill>-<N>/` (architecture.md §9), asserted on the emitted fact. The prep does
        create the directory; this class is the only writer of `/tmp/gh-requirements-gatherer-103`,
        so a parallel run never shares it."""
        result = self._run(
            ["103", "octo/widgets", "--root", str(self.root)],
            fixture_case="prep_requirements_gatherer_fresh",
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self.assertEqual(envelope["scratch"], "/tmp/gh-requirements-gatherer-103")


if __name__ == "__main__":
    unittest.main()
