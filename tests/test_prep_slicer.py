"""Unit tests for scripts/prep_slicer.py — the slicer's facts block in one call (#17, #16;
architecture.md §4).

Two strategies, matching the sibling prep suites:

  - **Pure-core unit tests** call the module's own non-emitting helpers directly (type detection,
    the altitude, the child sets, the refusal set, the blocker filters) — fast, and they pin the
    exact arithmetic without a subprocess.
  - **Subprocess tests** drive `prep_slicer.py` as a real process against the fixture-replaying `gh`
    shim, proving the envelope contract and the one-round-trip budget end to end.

What this suite exists to protect:

  - **The refusal set is a fact, not a decision code.** Four conditions must land in
    `vector.refusals` with `suggested_playbook: None`, never as a `needs_decision` — a refusal is a
    routing outcome with its own handoff, not a one-card question. An epic is deliberately NOT among
    them since #16: it is the epic-altitude happy path.
  - **The altitude is a parameter, not a fork.** One `children` key serves both altitudes with `kind`
    naming which, so the flow reads values rather than branching.
  - **Resume, don't duplicate.** At story altitude `next_index` must OVER-count rather than risk
    re-issuing a live designator, including when a child was hand-retitled out of the `<N>/S<K>` form.
  - **The two-tier epic read never halves the story set.** `mixed` unions both halves with native
    state winning; taking one and dropping the other silently loses stories.
  - **The slice-target guard.** The by-construction identification rule (a non-epic's sub-issues are
    slices) only holds if nothing ever slices a slice, so the parent-type check is load-bearing — and
    it must not misfire on an epic, which is the one shape that can never be a slice.
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
SCRIPT = SCRIPTS_DIR / "prep_slicer.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_slicer  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib.decisions import DOC_CATALOGUE_ABSENT  # noqa: E402
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


class TypeDetectionTests(unittest.TestCase):
    """`branching.detect_type` widened by one `question` arm — the shared core keeps its precedence."""

    def test_shared_core_arms_pass_through(self):
        self.assertEqual(prep_slicer._detect_type(["epic"], "x"), "epic")
        self.assertEqual(prep_slicer._detect_type(["story"], "x"), "story")
        self.assertEqual(prep_slicer._detect_type([], "Epic: onboarding"), "epic")
        self.assertEqual(prep_slicer._detect_type([], "plain title"), "standard")

    def test_question_label_wins_over_the_standard_fallback_only(self):
        self.assertEqual(prep_slicer._detect_type(["question"], "x"), "question")
        # A pathologically double-labelled issue keeps the shared core's documented precedence.
        self.assertEqual(prep_slicer._detect_type(["epic", "question"], "x"), "epic")
        self.assertEqual(prep_slicer._detect_type(["story", "question"], "x"), "story")


class AltitudeTests(unittest.TestCase):
    """#16: one operation, two altitudes, selected by whether the child gets its own branch. The bar
    itself is stated once in `slicing-method.md` §1; this is only the fact selecting it."""

    def test_an_epic_cuts_at_epic_altitude(self):
        self.assertEqual(prep_slicer._detect_altitude("epic"), "epic")

    def test_a_story_or_standalone_issue_cuts_at_story_altitude(self):
        self.assertEqual(prep_slicer._detect_altitude("story"), "story")
        self.assertEqual(prep_slicer._detect_altitude("standard"), "story")

    def test_promotion_forces_epic_altitude_because_the_target_becomes_an_epic(self):
        self.assertEqual(prep_slicer._detect_altitude("standard", promote=True), "epic")


class SliceSetTests(unittest.TestCase):
    def test_empty_child_set_starts_at_one(self):
        result = prep_slicer._build_slice_set([], 103)
        self.assertEqual(
            result,
            {
                "kind": "slices",
                "entries": [],
                "count": 0,
                "open_count": 0,
                "placeholder_count": 0,
                "total_named": 0,
                "next_index": 1,
                "source": None,
            },
        )

    def test_the_slice_edge_reports_no_source_because_it_has_no_fallback(self):
        """`epic-story-hierarchy.md`: "The slice edge has no checklist fallback and never will" — so
        a `stories_source`-style flag has no slice analogue and must stay null."""
        result = prep_slicer._build_slice_set(
            [{"number": 104, "title": "103/S1 — a", "state": "OPEN"}], 103
        )
        self.assertEqual(result["kind"], "slices")
        self.assertIsNone(result["source"])

    def test_designators_parsed_and_open_count_derived(self):
        subs = [
            {"number": 104, "title": "103/S1 — first login", "state": "CLOSED"},
            {"number": 105, "title": "103/S2 — password reset", "state": "OPEN"},
        ]
        result = prep_slicer._build_slice_set(subs, 103)
        self.assertEqual([e["designator_index"] for e in result["entries"]], [1, 2])
        self.assertEqual(result["open_count"], 1)
        self.assertEqual(result["next_index"], 3)

    def test_next_index_over_counts_when_a_child_was_hand_retitled(self):
        """The trap: with 3 children whose highest PARSED index is 2, continuing from the parsed
        highest alone would re-issue S3 — which may be exactly the renamed child."""
        subs = [
            {"number": 104, "title": "103/S1 — a", "state": "CLOSED"},
            {"number": 105, "title": "103/S2 — b", "state": "OPEN"},
            {"number": 106, "title": "renamed, was 103/S3", "state": "OPEN"},
        ]
        self.assertEqual(prep_slicer._build_slice_set(subs, 103)["next_index"], 4)

    def test_a_designator_naming_another_parent_is_not_counted(self):
        subs = [{"number": 9, "title": "999/S9 — someone else's slice", "state": "OPEN"}]
        result = prep_slicer._build_slice_set(subs, 103)
        self.assertIsNone(result["entries"][0]["designator_index"])
        self.assertEqual(result["next_index"], 2)

    def test_panel_order_is_preserved(self):
        """Creation order IS delivery order, so the entry list must not be re-sorted."""
        subs = [
            {"number": 106, "title": "103/S3 — c", "state": "OPEN"},
            {"number": 104, "title": "103/S1 — a", "state": "OPEN"},
        ]
        result = prep_slicer._build_slice_set(subs, 103)
        self.assertEqual([e["number"] for e in result["entries"]], [106, 104])


class RefusalTests(unittest.TestCase):
    def test_no_refusal_for_a_story_under_an_epic(self):
        self.assertEqual(
            prep_slicer._build_refusals("story", "OPEN", {"type": "epic"}, [], []), []
        )

    def test_no_refusal_for_a_standalone_non_epic_issue(self):
        self.assertEqual(prep_slicer._build_refusals("standard", "OPEN", None, [], []), [])

    def test_an_epic_target_is_no_longer_refused(self):
        """#16 retargeted this stage to epic altitude: an epic is the happy path, not a refusal."""
        self.assertEqual(prep_slicer._build_refusals("epic", "OPEN", None, [], []), [])
        self.assertFalse(hasattr(prep_slicer, "REFUSAL_EPIC_TARGET"))

    def test_a_parent_on_a_label_confirmed_epic_does_not_read_as_a_slice(self):
        """An epic is the top of the hierarchy, so a parent on one is malformed data — not evidence
        the epic is a slice. Misreading it would refuse the epic-altitude happy path."""
        self.assertEqual(
            prep_slicer._build_refusals(
                "epic", "OPEN", {"type": "story"}, [], [], target_labels=["epic"]
            ),
            [],
        )

    def test_a_title_only_epic_under_a_non_epic_parent_is_still_refused(self):
        """The hole the label check closes. `branching.detect_type` types ANY issue titled `Epic: …`
        as an epic, so exempting every epic-typed target from the slice guard would let a plainly
        titled sub-issue of a story through — and the flow would file stories beneath a story, the
        fourth hierarchy level the by-construction identification rule forbids. A title is a human
        convention; only the label is evidence strong enough to override a structural signal."""
        self.assertEqual(
            prep_slicer._detect_type([], "Epic: rework the auth flow"), "epic",
            "precondition: the title arm types this as an epic",
        )
        self.assertEqual(
            prep_slicer._build_refusals(
                "epic", "OPEN", {"type": "story"}, [], [], target_labels=[]
            ),
            [prep_slicer.REFUSAL_SLICE_TARGET],
        )

    def test_slice_target_refused_when_the_parent_is_not_an_epic(self):
        """Never slice a slice — the guard the by-construction identification rule depends on."""
        for parent_type in ("story", "standard"):
            self.assertEqual(
                prep_slicer._build_refusals("standard", "OPEN", {"type": parent_type}, [], []),
                [prep_slicer.REFUSAL_SLICE_TARGET],
                parent_type,
            )

    def test_question_and_closed_targets_refused(self):
        self.assertIn(
            prep_slicer.REFUSAL_QUESTION_TARGET,
            prep_slicer._build_refusals("question", "OPEN", None, [], []),
        )
        self.assertIn(
            prep_slicer.REFUSAL_CLOSED_TARGET,
            prep_slicer._build_refusals("standard", "CLOSED", None, [], []),
        )

    def test_blocked_from_either_a_native_blocker_or_an_in_scope_oq(self):
        self.assertEqual(
            prep_slicer._build_refusals("standard", "OPEN", None, [{"number": 61}], []),
            [prep_slicer.REFUSAL_BLOCKED],
        )
        self.assertEqual(
            prep_slicer._build_refusals("standard", "OPEN", None, [], [{"oq_id": "OQ-1"}]),
            [prep_slicer.REFUSAL_BLOCKED],
        )

    def test_refusal_order_is_stable_for_deterministic_rendering(self):
        refusals = prep_slicer._build_refusals(
            "question", "CLOSED", {"type": "story"}, [{"number": 61}], []
        )
        self.assertEqual(
            refusals,
            [
                prep_slicer.REFUSAL_QUESTION_TARGET,
                prep_slicer.REFUSAL_SLICE_TARGET,
                prep_slicer.REFUSAL_CLOSED_TARGET,
                prep_slicer.REFUSAL_BLOCKED,
            ],
        )


class StorySetTests(unittest.TestCase):
    """Epic altitude reads through `epic-story-hierarchy.md`'s two-tier read, relocated here from
    `prep_drafter.py` with #16. `source` reports which tier answered so the flow knows whether there
    are checkboxes to reconcile at all."""

    def test_native_relation_reports_sub_issues_and_mirrors_live_state(self):
        subs = [
            {"number": 151, "title": "Story A", "state": "CLOSED"},
            {"number": 152, "title": "Story B", "state": "OPEN"},
        ]
        children, attention, decision = prep_slicer._build_story_set(subs, "", "o/r")
        self.assertIsNone(decision)
        self.assertEqual(children["kind"], "stories")
        self.assertEqual(children["source"], "sub-issues")
        self.assertEqual([e["number"] for e in children["entries"]], [151, 152])
        self.assertEqual([e["checked"] for e in children["entries"]], [True, False])
        self.assertEqual(children["open_count"], 1)
        # Stories are titled plainly, so there is no designator numbering to resume.
        self.assertIsNone(children["next_index"])
        # `checked` mirrors live state, so a mismatch is unrepresentable on this tier.
        self.assertEqual(attention, [])

    def test_a_fresh_epic_with_no_children_reports_no_source(self):
        children, attention, decision = prep_slicer._build_story_set([], "## Goal\nx\n", "o/r")
        self.assertIsNone(decision)
        self.assertEqual(children["count"], 0)
        self.assertIsNone(children["source"])
        self.assertEqual(attention, [])

    def test_panel_order_is_preserved_because_it_is_delivery_order(self):
        subs = [
            {"number": 153, "title": "Story C", "state": "OPEN"},
            {"number": 151, "title": "Story A", "state": "OPEN"},
        ]
        children, _attention, _decision = prep_slicer._build_story_set(subs, "", "o/r")
        self.assertEqual([e["number"] for e in children["entries"]], [153, 151])


class PlaceholderChildTests(unittest.TestCase):
    """A legacy `## Stories` bullet with no issue number names a story nobody has FILED. Counting one
    as an existing child made `mode` read `resume` on an epic with zero real children, so the flow
    reported "cut only the remainder" and filed nothing — recreating the permanently-empty rollup this
    stage exists to prevent — and made `count - open_count` read as "all closed" for stories that were
    never filed."""

    def test_unfiled_bullets_are_not_counted_as_existing_children(self):
        body = "## Stories\n- [ ] Login\n- [ ] Signup\n- [ ] Reset\n"
        children, _attention, _decision = prep_slicer._build_story_set([], body, "o/r")
        self.assertEqual(children["count"], 0, "no story is filed, so there is nothing to resume")
        self.assertEqual(children["placeholder_count"], 3)
        self.assertEqual(children["total_named"], 3)
        # `entries` still carries them — they are the cut's input, not its resume set.
        self.assertEqual(len(children["entries"]), 3)

    def test_a_progress_reading_cannot_conclude_all_closed(self):
        """`count - open_count` is the arithmetic behind the documented `<K> of <T> closed` rendering."""
        body = "## Stories\n- [ ] Login\n- [ ] Signup\n"
        children, _attention, _decision = prep_slicer._build_story_set([], body, "o/r")
        self.assertEqual(children["count"] - children["open_count"], 0)

    def test_a_checklist_fully_covered_natively_is_not_mixed(self):
        """`mixed` must mean BOTH tiers contributed. When every checklist entry is already native the
        checklist half contributes nothing after the dedup, and calling that `mixed` triggers an
        attention line claiming those entries have no native relation — on the strength of which the
        flow proposes a parent-body write."""
        subs = [{"number": 11, "title": "A", "state": "OPEN"}]
        children, _attention, _decision = prep_slicer._build_story_set(
            subs, "## Stories\n- [ ] #11 — A\n", "o/r"
        )
        self.assertEqual(children["source"], "sub-issues")


class BlockerFilterTests(unittest.TestCase):
    def test_only_open_native_blockers_gate(self):
        """A closed blocker is a stale recorded claim, not a gate — live state decides."""
        nodes = [
            {"number": 61, "state": "OPEN", "title": "Which PSP?", "url": "u1"},
            {"number": 62, "state": "CLOSED", "title": "Answered", "url": "u2"},
        ]
        self.assertEqual([b["number"] for b in prep_slicer._open_blockers(nodes)], [61])

    def test_only_in_scope_blocked_oq_entries_gate(self):
        entries = [
            {"oq_id": "OQ-1", "disposition": "in-scope (blocked)", "question": "#61"},
            {"oq_id": "OQ-2", "disposition": "scoped-out", "question": "#62"},
            {"oq_id": "OQ-3", "disposition": "provisional-default", "question": "#63"},
        ]
        self.assertEqual(
            [e["oq_id"] for e in prep_slicer._in_scope_blocked_oqs(entries)], ["OQ-1"]
        )


class SuggestedPlaybookTests(unittest.TestCase):
    def test_one_playbook_when_nothing_is_refused(self):
        self.assertEqual(prep_slicer._suggested_playbook([]), "cut.md")

    def test_a_refusal_routes_to_no_playbook(self):
        self.assertIsNone(prep_slicer._suggested_playbook([prep_slicer.REFUSAL_SLICE_TARGET]))


# ---------------------------------------------------------------------------
# Subprocess / envelope
# ---------------------------------------------------------------------------


class PrepSlicerSandboxTestCase(unittest.TestCase):
    """A real temp git origin+clone as `--root` (needed for `git rev-parse HEAD`), pre-seeded with a
    doc catalogue so the grounding dimension is neutral in tests that aren't about grounding."""

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


class FreshHappyPathTests(PrepSlicerSandboxTestCase):
    def test_facts_schema(self):
        envelope = self._envelope(103, "prep_slicer_fresh")
        for key in (
            "repo",
            "scratch",
            "root",
            "target",
            "vector",
            "suggested_playbook",
            "promotion",
            "children",
            "adoption_candidates",
            "grounding_docs",
            "research",
            "open_questions",
            "sections",
            "attention",
        ):
            self.assertIn(key, envelope, key)
        self.assertEqual(
            envelope["vector"],
            {"type": "story", "altitude": "story", "mode": "fresh", "refusals": []},
        )
        self.assertEqual(envelope["suggested_playbook"], "cut.md")
        self.assertEqual(envelope["children"]["kind"], "slices")
        self.assertEqual(envelope["children"]["count"], 0)
        self.assertEqual(envelope["children"]["next_index"], 1)
        self.assertFalse(envelope["promotion"])
        self.assertEqual(envelope["adoption_candidates"], [])
        self.assertEqual(envelope["attention"], [])
        self.assertEqual(envelope["notices"], [])

    def test_parent_epic_is_typed_from_its_own_labels(self):
        """The relation's node shape carries no labels, so the parent needs its own lookup."""
        envelope = self._envelope(103, "prep_slicer_fresh")
        self.assertEqual(envelope["target"]["parent"]["number"], 150)
        self.assertEqual(envelope["target"]["parent"]["type"], "epic")

    def test_catalogue_entries_land_as_grounding_docs(self):
        envelope = self._envelope(103, "prep_slicer_fresh")
        self.assertEqual([e["path"] for e in envelope["grounding_docs"]], ["docs/prd.md"])
        self.assertTrue(envelope["grounding_docs"][0]["present"])

    def test_body_and_thread_are_spilled_into_sections(self):
        envelope = self._envelope(103, "prep_slicer_fresh")
        self.assertTrue(
            any(k.startswith("issue_body") for k in envelope["sections"]), envelope["sections"]
        )


class StandaloneTargetTests(PrepSlicerSandboxTestCase):
    def test_a_parentless_non_epic_issue_is_sliceable_with_no_parent_lookup(self):
        envelope = self._envelope(200, "prep_slicer_standalone")
        self.assertEqual(envelope["vector"]["refusals"], [])
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertIsNone(envelope["target"]["parent"])


class ResumeModeTests(PrepSlicerSandboxTestCase):
    def test_existing_sub_issues_switch_the_mode_and_carry_the_numbering(self):
        envelope = self._envelope(103, "prep_slicer_resume")
        self.assertEqual(envelope["vector"]["mode"], "resume")
        self.assertEqual(envelope["suggested_playbook"], "cut.md")
        self.assertEqual(envelope["children"]["kind"], "slices")
        self.assertEqual(envelope["children"]["count"], 2)
        self.assertEqual(envelope["children"]["open_count"], 1)
        self.assertEqual(envelope["children"]["next_index"], 3)

    def test_resume_raises_an_attention_line_so_nothing_re_files_silently(self):
        envelope = self._envelope(103, "prep_slicer_resume")
        self.assertTrue(
            any("resume mode" in item for item in envelope["attention"]), envelope["attention"]
        )


class EpicAltitudeTests(PrepSlicerSandboxTestCase):
    """#16: an epic is the epic-altitude happy path, not a refusal. Children are STORIES, read
    through `epic-story-hierarchy.md`'s two-tier read (the relocated epic-revise gather)."""

    def test_a_fresh_epic_cuts_at_epic_altitude_with_no_children(self):
        envelope = self._envelope(150, "prep_slicer_epic_fresh")
        self.assertEqual(envelope["vector"]["refusals"], [])
        self.assertEqual(envelope["suggested_playbook"], "cut.md")
        self.assertEqual(
            envelope["vector"],
            {"type": "epic", "altitude": "epic", "mode": "fresh", "refusals": []},
        )
        self.assertEqual(envelope["children"]["kind"], "stories")
        self.assertEqual(envelope["children"]["count"], 0)
        self.assertIsNone(envelope["children"]["source"])
        # Stories are titled plainly -- no designator numbering to resume.
        self.assertIsNone(envelope["children"]["next_index"])

    def test_native_story_set_resumes_and_costs_no_per_story_fetch(self):
        # The fixture's manifest carries NO per-story entries, so a regression that added one would
        # fail as a shim MISS rather than passing quietly.
        envelope = self._envelope(150, "prep_slicer_epic_resume_native")
        self.assertEqual(envelope["vector"]["mode"], "resume")
        self.assertEqual(envelope["children"]["source"], "sub-issues")
        self.assertEqual([e["number"] for e in envelope["children"]["entries"]], [151, 152])
        self.assertEqual(envelope["children"]["open_count"], 1)

    def test_legacy_checklist_epic_reports_its_source_and_surfaces_a_mismatch(self):
        envelope = self._envelope(160, "prep_slicer_epic_resume_checklist")
        children = envelope["children"]
        self.assertEqual(children["source"], "checklist")
        # Only FILED entries count. The unfiled bullet still rides in `entries` rather than being
        # dropped, but it is not part of the resume set — see the dedicated test below.
        self.assertEqual(children["count"], 2)
        self.assertEqual(children["placeholder_count"], 1)
        self.assertEqual(children["total_named"], 3)
        self.assertEqual([e["number"] for e in children["entries"]], [151, 152, None])
        # #151 is unchecked in the body but CLOSED live -- surfaced, never silently reconciled here
        # (reconciling the body is a write the flow proposes at its gate).
        self.assertTrue(
            any("checkbox is unchecked but live state is CLOSED" in i
                for i in envelope["attention"]),
            envelope["attention"],
        )
        self.assertTrue(
            any("legacy `## Stories` checklist" in i for i in envelope["attention"]),
            envelope["attention"],
        )

    def test_mixed_epic_unions_both_halves_and_never_drops_one(self):
        envelope = self._envelope(170, "prep_slicer_epic_mixed")
        children = envelope["children"]
        self.assertEqual(children["source"], "mixed")
        # Native first, then the checklist remainder -- both halves present.
        self.assertEqual([e["number"] for e in children["entries"]], [153, 151])


class PromotionTests(PrepSlicerSandboxTestCase):
    """`--promote` is router-passed (prep cannot read the invocation prose). It forces epic altitude
    because the target BECOMES an epic, behind the flow's own rewrite gate."""

    def test_promote_forces_epic_altitude_on_a_standard_target(self):
        envelope = self._envelope(200, "prep_slicer_standalone", extra_args=["--promote"])
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(envelope["vector"]["altitude"], "epic")
        self.assertTrue(envelope["promotion"])
        self.assertEqual(envelope["children"]["kind"], "stories")
        self.assertTrue(
            any("promotion requested" in i for i in envelope["attention"]), envelope["attention"]
        )

    def test_promote_on_an_already_epic_target_is_a_no_op_not_an_error(self):
        envelope = self._envelope(150, "prep_slicer_epic_fresh", extra_args=["--promote"])
        self.assertEqual(envelope["vector"]["altitude"], "epic")
        self.assertFalse(envelope["promotion"])
        self.assertTrue(
            any("already an epic" in i for i in envelope["attention"]), envelope["attention"]
        )

    def test_without_promote_a_standard_target_stays_at_story_altitude(self):
        envelope = self._envelope(200, "prep_slicer_standalone")
        self.assertEqual(envelope["vector"]["altitude"], "story")
        self.assertFalse(envelope["promotion"])


class AdoptionTests(PrepSlicerSandboxTestCase):
    """"Epic over existing issues": `--adopt N` reports each candidate's live state so the flow can
    present it; the write is `gh_persist.py add-parent`."""

    def test_candidates_carry_live_state_in_the_order_named(self):
        envelope = self._envelope(
            180, "prep_slicer_adopt", extra_args=["--adopt", "301", "--adopt", "302"]
        )
        candidates = envelope["adoption_candidates"]
        self.assertEqual([c["number"] for c in candidates], [301, 302])
        self.assertEqual(candidates[0]["state"], "OPEN")
        self.assertEqual(candidates[0]["type"], "story")

    def test_an_already_parented_candidate_is_surfaced_before_the_write(self):
        envelope = self._envelope(
            180, "prep_slicer_adopt", extra_args=["--adopt", "301", "--adopt", "302"]
        )
        candidates = {c["number"]: c for c in envelope["adoption_candidates"]}
        self.assertFalse(candidates[301]["already_parented"])
        self.assertTrue(candidates[302]["already_parented"])
        self.assertEqual(candidates[302]["parent"], 199)
        self.assertTrue(
            any("already has parent #199" in i for i in envelope["attention"]),
            envelope["attention"],
        )

    def test_no_adopt_flag_costs_no_extra_gh_call(self):
        # The epic_fresh manifest has only the three base entries, so any candidate lookup on a run
        # without --adopt would fail as a shim MISS.
        envelope = self._envelope(150, "prep_slicer_epic_fresh")
        self.assertEqual(envelope["adoption_candidates"], [])

    def test_adopt_check_is_a_one_shot_lookup_with_no_facts_block(self):
        # The one-shot mode names no issue, so it never re-runs the gather -- the whole point of the
        # mode for candidates the operator names mid-session.
        result = self._run(
            ["octo/widgets", "--adopt-check", "301", "--adopt-check", "302", "--cwd", str(self.root)],
            fixture_case="prep_slicer_adopt",
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual([c["number"] for c in envelope["adoption_candidates"]], [301, 302])
        self.assertNotIn("vector", envelope)
        self.assertNotIn("children", envelope)


class RefusalFactTests(PrepSlicerSandboxTestCase):
    """Every refusal is an `ok` envelope carrying a fact — never a `needs_decision`."""

    def _refusal(self, issue, fixture_case):
        envelope = self._envelope(issue, fixture_case)
        self.assertEqual(envelope["status"], "ok")
        self.assertIsNone(envelope["suggested_playbook"])
        return envelope

    def test_slice_target(self):
        envelope = self._refusal(105, "prep_slicer_slice_target")
        self.assertEqual(envelope["vector"]["refusals"], [prep_slicer.REFUSAL_SLICE_TARGET])
        self.assertEqual(envelope["target"]["parent"]["type"], "story")
        self.assertTrue(any("never sliced" in i for i in envelope["attention"]))

    def test_question_target(self):
        envelope = self._refusal(202, "prep_slicer_question")
        self.assertEqual(envelope["vector"]["refusals"], [prep_slicer.REFUSAL_QUESTION_TARGET])

    def test_closed_target(self):
        envelope = self._refusal(203, "prep_slicer_closed")
        self.assertEqual(envelope["vector"]["refusals"], [prep_slicer.REFUSAL_CLOSED_TARGET])

    def test_blocked_target_names_the_open_blocker(self):
        envelope = self._refusal(201, "prep_slicer_blocked")
        self.assertEqual(envelope["vector"]["refusals"], [prep_slicer.REFUSAL_BLOCKED])
        self.assertEqual([b["number"] for b in envelope["target"]["blocked_by"]], [61])
        self.assertTrue(any("#61" in i for i in envelope["attention"]), envelope["attention"])


class GroundingTests(PrepSlicerSandboxTestCase):
    def test_absent_catalogue_is_a_notice_and_an_attention_line_never_a_refusal(self):
        """Prep cannot see operator-named sources, so only the flow may refuse on grounding."""
        self._remove_catalogue()
        envelope = self._envelope(103, "prep_slicer_fresh")
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertIn(DOC_CATALOGUE_ABSENT, envelope["notices"])
        self.assertEqual(envelope["vector"]["refusals"], [])
        self.assertEqual(envelope["suggested_playbook"], "cut.md")
        self.assertTrue(
            any("no doc catalogue" in i for i in envelope["attention"]), envelope["attention"]
        )

    def test_empty_catalogue_is_not_the_absent_notice(self):
        self._seed_catalogue("")
        envelope = self._envelope(103, "prep_slicer_fresh")
        self.assertEqual(envelope["notices"], [])
        self.assertTrue(any("declares no documents" in i for i in envelope["attention"]))

    def test_declared_but_missing_doc_is_surfaced(self):
        self._seed_catalogue(
            "- `docs/prd.md` — prd — binding — What the product is.\n"
            "- `docs/ui-design.md` — ui-design — binding — Not on this ref.\n"
        )
        envelope = self._envelope(103, "prep_slicer_fresh")
        self.assertTrue(
            any("docs/ui-design.md" in i for i in envelope["attention"]), envelope["attention"]
        )


class SingleInvocationBudgetTests(PrepSlicerSandboxTestCase):
    """architecture.md §9.2: one state-assembly invocation per session. The fixture manifest is
    exact-argv matched, so an unexpected extra `gh` call fails the shim loudly — these bounds pin
    that the parent lookup happens exactly once, and only when a parent exists."""

    def test_a_parentless_target_makes_no_parent_lookup(self):
        # `prep_slicer_standalone`'s manifest carries NO parent-view entry; a lookup would miss and
        # the shim would exit 2.
        envelope = self._envelope(200, "prep_slicer_standalone")
        self.assertIsNone(envelope["target"]["parent"])

    def test_a_parented_target_makes_exactly_one_parent_lookup(self):
        envelope = self._envelope(103, "prep_slicer_fresh")
        self.assertEqual(envelope["target"]["parent"]["number"], 150)

    def test_epic_altitude_costs_one_fetch_per_filed_checklist_entry_and_no_more(self):
        """The `checklist` tier is the only rung that pays per-story `gh` calls: the native tier reads
        state straight out of the gather, and an unfiled bullet has no issue to look up. The manifest
        carries exactly two story entries for three bullets, so a third lookup would miss."""
        envelope = self._envelope(160, "prep_slicer_epic_resume_checklist")
        self.assertEqual(envelope["children"]["source"], "checklist")
        self.assertEqual(
            [e["number"] for e in envelope["children"]["entries"] if e["number"] is not None],
            [151, 152],
        )

    def test_a_mixed_epic_fetches_only_the_checklist_half(self):
        """The native half already carries authoritative live state, so re-fetching it would be a
        wasted call -- the mixed fixture's manifest has one story entry, not two."""
        envelope = self._envelope(170, "prep_slicer_epic_mixed")
        self.assertEqual(envelope["children"]["source"], "mixed")


class ScratchDirTests(PrepSlicerSandboxTestCase):
    def test_default_scratch_dir_follows_the_house_convention(self):
        """`/tmp/gh-<skill>-<N>/` (architecture.md §9), asserted on the emitted fact. The prep does
        create the directory; this class is the only writer of `/tmp/gh-slicer-103`, so a parallel
        run never shares it."""
        result = self._run(
            ["103", "octo/widgets", "--root", str(self.root)], fixture_case="prep_slicer_fresh"
        )
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self.assertEqual(envelope["scratch"], "/tmp/gh-slicer-103")


if __name__ == "__main__":
    unittest.main()
