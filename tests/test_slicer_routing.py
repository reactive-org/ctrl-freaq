"""Routing / structural gates for the `slicer` skill (#17).

Prose has no compiler, so these are it. The suite mirrors the house set every
`tests/test_<skill>_routing.py` carries — frontmatter pins, routing-table ↔ disk ↔ prep agreement,
the structural bars, fence-scoped grep bans, `--dry-run` persist envelopes, handoff-rendering pins —
plus the gates specific to this stage:

  - **The write-gate posture.** "Zero GitHub mutations before the one gate" is the invariant that
    makes the stage safe to abort; it must be stated in the loaded prompt, not only in a reference.
  - **The refusal set is closed and matches prep.** The router names exactly the tokens
    `prep_slicer` can emit — a router that renders a refusal prep never produces, or misses one prep
    does, is a silent dead end.
  - **Never slice a slice, and never edit the parent body.** Both are load-bearing: the first keeps
    the by-construction slice identification sound, the second keeps the rollup the single source of
    truth about the slice set.

## The line bar

There is no v1 slicer, so prd.md §10's "half the v1 `SKILL.md` line count" has no input. `SLICER_BAR`
is **measured, not halved** — see the constant's comment below for the derivation.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SKILL_DIR = REPO_ROOT / "skills" / "slicer"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
REFERENCES_DIR = SKILL_DIR / "references"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"
SHARED = REPO_ROOT / "skills" / "_shared"

ROUTABLE_PLAYBOOKS = {"cut.md"}

# MEASURED, not halved. prd.md §10's formula ("router + the one playbook <= half the v1 SKILL.md line
# count") has no input here: there was never a v1 slicer, and the working prototype this stage is
# based on (a repo-local skill: 284-line SKILL.md + a 179-line method reference) is ALREADY split
# router/reference, so halving it would double-count a compression that already happened.
#
# ORIGINAL DERIVATION (#17, one altitude): the prototype's generic core is 284 lines minus ~85 lines
# of repo-local board mechanics and ~15 lines of local worked example, i.e. ~145-165 lines of loaded
# prompt. The plugin version dropped the remaining board half and added the four-section router
# anatomy, the facts block, the handoff section and the re-route arm — which roughly cancel. Hence
# 170, landed at exactly 170.
#
# RE-ADJUDICATED at #16 (170 -> 261), which retargeted the stage to a SECOND altitude and moved four
# behaviours here from the drafter's epic-split. Per prd.md §10 and the S19 doc-reviewer precedent,
# an honest implementation that exceeds a recorded bar is re-adjudicated and re-recorded — precision
# is never trimmed out of a prompt to hit a number. What the +91 lines buy, measured against the #17
# router (90) + cut.md (80):
#
#   +22  S0, the promotion prefix — a second write flow with its own diff-and-confirm gate, which the
#        cut's own zero-mutation gate deliberately does not cover.
#   +12  S3, the adversarial review loop + its invariant. #17 had no reviewer at all; dimensions 5
#        and 7 moved off the drafter's issue reviewer onto references/cut-reviewer-prompt.md.
#   +25  epic-altitude value plumbing: the altitude fact and its two bars, the per-altitude child
#        template branch, the bookend slots, and the legacy `## Stories` reconciliation write.
#   +20  adoption ("epic over existing issues"): the prep flags, candidates in S2, `add-parent` in
#        S5, and the sequential-creation invariant extended to cover it.
#   +12  router: the promote rule (the one thing only the router can decide), the rewritten refusal
#        set, and the added handoff fields.
#
# RE-ADJUDICATED AGAIN by the #16 code review (261 -> 278). The review found precision MISSING rather
# than surplus, and every line added closes a defect it demonstrated:
#
#   +6   router: the third sanctioned parent-body write (the bookend-omission `## Background` note,
#        which S4 gates and S5 executes — it was required by the method with no gate and no step, so
#        the instruction was unexecutable), the facts keys the playbook actually reads (`root`,
#        `scratch`, `entries`, `placeholder_count`), and the two further decision codes the composed
#        gather can return.
#   +11  cut.md: unfiled `## Stories` bullets are the cut's INPUT, not its resume set (they were being
#        counted as existing children, so the flow reported "cut only the remainder" and filed
#        nothing); the `<<pass>>` and `<<changed_summary>>` inputs the reviewer prompt defines and
#        hangs behaviour on but nothing filled; the S4 gate row and S5 step for the bookend note.
#
# RE-ADJUDICATED by the milestone-slices change (278 -> 288). Operators found story-altitude cuts
# filing one slice per demonstrable increment — a phase list re-filed as issues — where one multi-phase
# plan would do. The method now makes a slice earn its issue as a milestone (slicing-method.md §4.1),
# which needs an exit the flow did not have: a cut that leaves one milestone files NOTHING.
#
#   +3   cut.md S2: the milestone pass at story altitude and the no-cut branch (skip S3 — there is
#        nothing to review).
#   +1   cut.md S3: a reviewer `milestone` finding that folds the whole cut, once adopted, is a no cut.
#   +6   cut.md S4: the no-cut card — the operator asked for slices (here, or at the planner's "Slice
#        first"), so overriding that is a genuine decision with its own two options, not a silent
#        downgrade, and it must say it skips the summary table and the write gate.
#
# The S6 forward-route clause was absorbed by re-wrapping; the router's was a same-line edit (126).
#
#   +2   cut.md S4, from the PR #69 review (278 -> 290 in all): *Cut anyway* re-runs S2 with the
#        operator's milestones as FIXED inputs, never re-folded by the milestone pass or the reviewer.
#        Without it the no-cut card could loop: the same pass that proposed the no cut would fold the
#        operator's answer straight back into another one.
#
# Trimming any of it would restore a defect, which is precisely what prd.md §10's provision and the
# S19 precedent forbid. The router still fits one default Read (126 <= 150) — the bar that actually
# protects session startup; the growth is in the playbook, loaded only on the routed path.
SLICER_BAR = 290

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_slicer  # noqa: E402
from tests.support import envelope_asserts, shimenv  # noqa: E402
from tests.support.retired_tokens import (  # noqa: E402
    FORBIDDEN_CONTRACT_TOKENS,
    V1_INVOCATION_PREFIX,
    retired_name_hits,
)
from tests.test_subagent_prompts import THREAD_RULE_ANCHORS  # noqa: E402

_ROUTE_ROW_RE = re.compile(
    r"^\|\s*`?(?P<vector>[^|]+?)`?\s*\|\s*`(?P<playbook>playbooks/[a-z0-9-]+\.md)`\s*\|"
)


def _parse_router_routing_table(router_text):
    rows = []
    for line in router_text.splitlines():
        match = _ROUTE_ROW_RE.match(line)
        if match:
            rows.append((match.group("vector").strip(), match.group("playbook")))
    return rows


def _fence_stripped_lines(path):
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            yield i, line, in_fence
            continue
        yield i, line, in_fence


def _iter_md(dir_path):
    yield from sorted(dir_path.rglob("*.md"))


def _normalized(text):
    """Collapse whitespace and drop markdown emphasis, so a prose pin survives a re-wrap.

    Every one of these files is hard-wrapped at ~110 columns and every invariant here is bold, so a
    literal regex against the raw text pins the *line breaks* as much as the words — and then a
    legitimate compression that re-flows a paragraph fails a test about meaning. Normalizing keeps the
    pins about the sentence."""
    return re.sub(r"\s+", " ", text.replace("**", "").replace("*", ""))


class RouterStructureTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")

    def test_router_exists_and_has_no_model_effort_pins(self):
        self.assertTrue(ROUTER.is_file())
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: slicer", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)

    def test_router_is_model_invocable(self):
        """A pipeline stage, not a standalone tool — `disable-model-invocation` belongs to the five
        tools that carry it, and adding it here would make the planner's re-route undiscoverable."""
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertNotIn("disable-model-invocation", head)

    def test_router_carries_the_four_anatomy_sections_in_order(self):
        headings = [
            line.strip()
            for line in self.router_text.splitlines()
            if line.startswith("## ")
        ]
        self.assertEqual(
            headings, ["## 1. Prep", "## 2. Route", "## 3. Invariants", "## 4. Handoff"]
        )

    def test_router_dispatches_prep_by_plugin_root(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/prep_slicer.py", self.router_text)


class RoutingTableTests(unittest.TestCase):
    def setUp(self):
        self.rows = _parse_router_routing_table(ROUTER.read_text(encoding="utf-8"))

    def test_table_names_exactly_the_routable_playbook(self):
        self.assertEqual({Path(pb).name for _v, pb in self.rows}, ROUTABLE_PLAYBOOKS)

    def test_exactly_the_routable_playbooks_on_disk(self):
        self.assertEqual({p.name for p in PLAYBOOKS_DIR.glob("*.md")}, ROUTABLE_PLAYBOOKS)

    def test_every_routed_playbook_file_exists(self):
        for _vector, playbook in self.rows:
            self.assertTrue((SKILL_DIR / playbook).is_file(), playbook)

    def test_table_matches_prep_suggested_playbook(self):
        """Prep proposes; the router confirms. A refusal routes to no playbook at all."""
        self.assertEqual(prep_slicer._suggested_playbook([]), "cut.md")
        self.assertIn("cut.md", {Path(pb).name for _v, pb in self.rows})
        self.assertIsNone(prep_slicer._suggested_playbook([prep_slicer.REFUSAL_SLICE_TARGET]))

    def test_both_modes_and_both_altitudes_share_the_one_playbook(self):
        """`fresh`/`resume` and `story`/`epic` differ in values (and one gated prefix step), not in the
        actions the cut takes — so they must not be separate playbooks. #16 doubled the altitudes
        without doubling the flow; a second playbook here would be the fork the method reference's
        one-knob framing exists to prevent."""
        router = ROUTER.read_text(encoding="utf-8")
        for value in ("fresh", "resume", "story", "epic"):
            self.assertIn(value, router, value)
        self.assertEqual(len(ROUTABLE_PLAYBOOKS), 1)


class RefusalSetTests(unittest.TestCase):
    """The router must name exactly the refusal tokens prep can emit — no dead ends either way."""

    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_prep_refusal_tokens_are_all_named_in_the_router(self):
        for token in (
            prep_slicer.REFUSAL_SLICE_TARGET,
            prep_slicer.REFUSAL_QUESTION_TARGET,
            prep_slicer.REFUSAL_CLOSED_TARGET,
            prep_slicer.REFUSAL_BLOCKED,
        ):
            self.assertIn(token, self.router_text, token)

    def test_the_router_names_every_refusal_prep_can_emit_and_no_more(self):
        """The reverse direction, which #16 made load-bearing: it RETIRED a token, and a router still
        offering `epic-target` would send the epic-altitude happy path to a refusal handoff. Derived
        from prep's own constants so the two cannot drift apart in either direction."""
        prep_tokens = {
            value
            for name, value in vars(prep_slicer).items()
            if name.startswith("REFUSAL_") and isinstance(value, str)
        }
        self.assertEqual(
            prep_tokens,
            {"slice-target", "question-target", "closed-target", "blocked"},
            "the refusal set changed — update the router, the renderings and this pin together",
        )
        self.assertNotIn("epic-target", self.router_text)
        self.assertNotIn("epic-target", self.renderings)

    def test_router_sends_refusals_to_the_renderings_without_reading_the_playbook(self):
        self.assertRegex(self.router_text, r"do not read the playbook")
        self.assertIn("references/handoff-renderings.md", self.router_text)

    def test_every_refusal_has_a_rendering_shape(self):
        for phrase in (
            "Refused — no grounding",
            "Refused — blocked",
            "Refused — closed target",
            # #16 closed a latent gap: prep could emit `question-target` and the router named it, but
            # no shape existed to render it.
            "Refused — question target",
        ):
            self.assertIn(phrase, self.renderings, phrase)
        self.assertRegex(self.renderings, r"Refused — the target is itself a slice")

    def test_grounding_refusal_routes_to_setup(self):
        self.assertIn("/ctrl-freaq:setup", self.renderings)


class StructuralBarTests(unittest.TestCase):
    def test_router_at_most_150_lines(self):
        lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(lines, 150, "router must fit one default Read (architecture.md §9)")

    def test_router_plus_largest_playbook_within_the_measured_bar(self):
        router_lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        playbook_lines = {
            p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in PLAYBOOKS_DIR.glob("*.md")
        }
        largest = max(playbook_lines.values())
        self.assertLessEqual(
            router_lines + largest,
            SLICER_BAR,
            "router (%d) + largest playbook (%d) = %d exceeds the measured bar %d: %r"
            % (router_lines, largest, router_lines + largest, SLICER_BAR, playbook_lines),
        )

    def test_references_are_unbounded_but_present(self):
        """The method and the renderings live in references precisely so the bar doesn't force them
        thin — assert they exist rather than capping them."""
        for name in ("slicing-method.md", "handoff-renderings.md"):
            self.assertTrue((REFERENCES_DIR / name).is_file(), name)


class InvariantLanguageTests(unittest.TestCase):
    """The invariants that make this stage safe must be in the LOADED prompt, not only a reference."""

    def setUp(self):
        self.loaded = _normalized(
            ROUTER.read_text(encoding="utf-8")
            + "\n"
            + (PLAYBOOKS_DIR / "cut.md").read_text(encoding="utf-8")
        )

    def test_zero_mutations_before_the_one_write_gate(self):
        self.assertRegex(self.loaded, r"[Zz]ero GitHub mutations before the one write gate")
        self.assertIn("prd.md §8.2", self.loaded)

    def test_parent_body_is_never_edited_and_no_slices_section_is_written(self):
        self.assertRegex(self.loaded, r"parent'?s? body is never edited")
        self.assertRegex(self.loaded, r"no `## Slices` section is ever written")

    def test_a_slice_is_never_sliced(self):
        self.assertRegex(self.loaded, r"a slice is never sliced")

    def test_creation_order_is_display_order_and_filing_is_sequential(self):
        self.assertRegex(self.loaded, r"creation order is display")
        self.assertRegex(self.loaded, r"one slice at a time")

    def test_partial_failure_is_never_rounded_up(self):
        self.assertRegex(self.loaded, r'never "all done" after a partial run')

    def test_resume_does_not_duplicate(self):
        self.assertRegex(self.loaded, r"[Rr]esume, don't duplicate")
        self.assertIn("next_index", self.loaded)

    def test_the_grounding_gate_cannot_degrade_into_proceeding(self):
        playbook = _normalized((PLAYBOOKS_DIR / "cut.md").read_text(encoding="utf-8"))
        self.assertRegex(playbook, r"cannot degrade into proceeding")
        self.assertIn("DOC_CATALOGUE_ABSENT", playbook)


class EpicAltitudeTests(unittest.TestCase):
    """#16: the stage serves both altitudes off one parameter. These pin the things a later editor
    could quietly re-fork — the altitude fact, the promote rule only the router can decide, the
    reviewer that replaced the drafter's dimensions 5 and 7, and the two sanctioned parent writes."""

    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")
        self.playbook = (PLAYBOOKS_DIR / "cut.md").read_text(encoding="utf-8")
        self.loaded = _normalized(self.router_text + "\n" + self.playbook)
        self.method = (REFERENCES_DIR / "slicing-method.md").read_text(encoding="utf-8")
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        self.reviewer = (REFERENCES_DIR / "cut-reviewer-prompt.md").read_text(encoding="utf-8")
        # These references are hard-wrapped prose, so pin the SENTENCE, not the line breaks.
        self.method_norm = _normalized(self.method)
        self.reviewer_norm = _normalized(self.reviewer)

    def test_the_router_names_the_altitude_fact_and_both_bars(self):
        self.assertIn("altitude", self.router_text)
        self.assertIn("demonstrable", self.router_text)
        self.assertIn("shippable", self.router_text)

    def test_the_playbook_cuts_at_the_altitude_fact_rather_than_branching(self):
        """The flow must READ the altitude as a value; a prose `if epic … else story …` fork is the
        interleaving CLAUDE.md's "parameterize before you playbook" rule forbids."""
        self.assertIn("facts.vector.altitude", self.playbook)

    def test_the_promote_rule_lives_in_the_router_and_is_never_silent(self):
        """Only the router sees the invocation prose, so the promote decision cannot be a prep fact.
        A thread-only recommendation must ask first — promotion rewrites the issue."""
        self.assertIn("--promote", self.router_text)
        self.assertRegex(self.loaded, r'header: "Issue size"')

    def test_promotion_rides_its_own_gate_ahead_of_the_cut_gate(self):
        """The body rewrite is destructive where a create is not, so it must not ride the cut's write
        gate — and the zero-mutation invariant must stay true OF THE CUT rather than being weakened."""
        self.assertRegex(self.loaded, r"[Zz]ero GitHub mutations before the one write gate")
        self.assertIn("edit-labels", self.playbook)
        self.assertRegex(self.playbook, r"explicit confirmation")
        self.assertRegex(self.loaded, r"Implementation plan:")

    def test_the_two_sanctioned_parent_body_writes_are_named_as_exceptions(self):
        """The parent-body invariant survives #16 only if the two gated writes are stated as scoped
        exceptions; leaving them implicit would read as a contradiction and invite a "fix"."""
        self.assertRegex(self.loaded, r"parent'?s? body is never edited")
        self.assertIn("sanctioned", self.loaded)

    def test_the_adversarial_reviewer_exists_and_is_dispatched_with_its_control(self):
        self.assertTrue((REFERENCES_DIR / "cut-reviewer-prompt.md").is_file())
        self.assertIn("cut-reviewer-prompt.md", self.playbook)
        self.assertIn("3-pass cap", self.loaded)
        self.assertIn("circular guard", self.loaded)

    def test_the_reviewer_carries_the_relocated_ordering_and_sizing_dimensions(self):
        for phrase in ("### ordering", "### sizing", "### conformance"):
            self.assertIn(phrase, self.reviewer, phrase)
        # The bookend check moved with dimension 7 and is epic-only.
        self.assertIn("Bookend check", self.reviewer_norm)
        self.assertIn("technical-foundation", self.reviewer_norm)
        self.assertIn("finalization", self.reviewer_norm)
        self.assertIn("thinness is not evidence for merge signals 2 or 3", self.reviewer_norm)
        # The severity ladder came across intact: the shared-groundwork case is the BLOCKER, and it is
        # grep-grounded like every other claim this reviewer makes.
        self.assertRegex(self.reviewer_norm, r"No foundation story while ≥2 stories.{0,120}BLOCKER")
        self.assertIn("grep-grounded", self.reviewer_norm)
        # The three MERGE signals and the SPLIT guardrail, which are what "sizing" actually decides.
        for phrase in (
            "Sequential with no standalone value",
            "Same files or layer, individually thin",
            "recommend SPLIT",
        ):
            self.assertIn(phrase, self.reviewer_norm, phrase)
        # Findings-only: it never asks the operator and returns no decision code.
        self.assertIn("never calls `AskUserQuestion`", self.reviewer_norm)
        self.assertIn("returns no decision code", self.reviewer_norm)

    def test_the_method_carries_the_relocated_coalescing_pass_and_bookends(self):
        for phrase in (
            "ceiling, not a target",
            "Shared verification surface",
            "don't over-coalesce",
            "Technical-foundation story, first",
            "Finalization story, last",
            "Omission is allowed but never silent",
        ):
            self.assertIn(phrase, self.method_norm, phrase)
        # The deferral placeholders are a contract the reviewer's conformance carve-out matches, so
        # both files must spell them the same way.
        for phrase in ("specified at planning time", "grounded on the epic delivery log"):
            self.assertIn(phrase, self.method_norm, phrase)
            self.assertIn(phrase, self.reviewer_norm, phrase)
        self.assertIn("deferral placeholder", self.method_norm)
        # Where the omission justification is RECORDED, not just that one is required — it has to
        # survive every later resume, so a session-only note would not do.
        self.assertIn("epic body's `## Background`", self.method_norm)
        # The planner's foundation-slot default keys on this slot existing, and its wording is the
        # cross-skill half of the contract (it previously pinned against the drafter's epic-split).
        seams = _normalized(
            (
                REPO_ROOT / "skills" / "planner" / "references" / "seam-dispositions.md"
            ).read_text(encoding="utf-8")
        )
        self.assertIn("Foundation-slot default", seams)
        self.assertIn("double-file", seams)
        self.assertIn("slicer-filed technical-foundation slot", seams)

    def test_the_method_states_the_dod_inversion_at_epic_altitude(self):
        """A slice must NOT carry a DoD and a story MUST — the two rules look contradictory unless the
        reason (who the resolver runs on) is written down, so pin the reason, not just the rules."""
        self.assertRegex(self.method, r"deliberately not `## Definition of done`")
        self.assertRegex(self.method, r"A story \*\*is\*\* the issue the resolver runs on")

    def test_the_epic_altitude_child_template_is_the_drafters_not_a_copy(self):
        """One owner for the Story template. A second copy here is the drift #16 removed."""
        self.assertIn("../../drafter/references/issue-templates.md", self.method)

    def test_adoption_is_wired_end_to_end(self):
        self.assertIn("--adopt", self.router_text)
        self.assertIn("adoption_candidates", self.router_text)
        self.assertIn("add-parent", self.playbook)
        self.assertRegex(self.method, r"[Aa]dopting issues that already exist")

    def test_the_epic_renderings_use_the_epic_lead_and_stories_line(self):
        self.assertRegex(self.renderings, r"\*\*Epic:\*\* #\d+ .*· epic · plan: ✗")
        self.assertRegex(self.renderings, r"\*\*Stories:\*\* #\d+")
        # The two lead/child pairings must not be mixed up.
        self.assertRegex(self.renderings, r"Never mix them")

    def test_the_promotion_decline_exit_reports_zero_writes(self):
        self.assertIn("Declined at the promotion gate", self.renderings)


class MethodReferenceTests(unittest.TestCase):
    def setUp(self):
        self.method = (REFERENCES_DIR / "slicing-method.md").read_text(encoding="utf-8")

    def test_the_independence_bar_is_a_parameter_so_16_is_a_retarget(self):
        self.assertRegex(self.method, r"independence bar is a parameter")
        self.assertIn("demonstrable", self.method)
        self.assertIn("shippable", self.method)

    def test_anti_over_split_bias_is_stated_with_its_threshold(self):
        self.assertRegex(self.method, r"few, thick-enough slices")
        self.assertRegex(self.method, r"ten or more is an anti-pattern")

    def test_the_one_sentence_demo_test_is_present(self):
        self.assertRegex(self.method, r"one\s+sentence, the cut is wrong")

    def test_template_uses_acceptance_criteria_not_a_definition_of_done(self):
        """A DoD on a slice would be a checkbox set nothing ticks — the resolver projects onto the
        PARENT. The reference must say so, or a later editor "harmonizes" it back."""
        self.assertIn("## Acceptance criteria", self.method)
        self.assertRegex(self.method, r"deliberately not `## Definition of done`")
        self.assertRegex(self.method, r"checkbox set\s+nothing ticks")

    def test_tier_1_ticking_and_the_tier_2_trigger_with_its_diagnosis(self):
        self.assertRegex(self.method, r"ticks \*\*all\*\* of a slice's criteria")
        # The diagnosis flipped with milestone slices: a multi-phase slice is the expected shape, so
        # the old "check whether the cut is too thick" advice would push back toward thin slices.
        self.assertRegex(_normalized(self.method), r"never for cutting thinner slices")
        self.assertNotRegex(self.method, r"cut is too thick")

    def test_citation_duty_is_stated(self):
        self.assertRegex(self.method, r"[Cc]itation duty")
        self.assertRegex(self.method, r"can cite \*\*nothing\*\* does not exist")

    def test_the_four_named_non_slices_are_listed(self):
        for phrase in ("database tables", "API endpoints", "the UI", "background job"):
            self.assertIn(phrase, self.method, phrase)


class MilestoneSliceTests(unittest.TestCase):
    """Story-altitude cuts filed one slice per demonstrable increment — the parent plan's phase list
    re-filed as issues. A slice now earns its issue as a milestone (slicing-method.md §4.1), and a cut
    that leaves one milestone files nothing. These pin the rule, its exit, and the reviewer signal, so a
    later editor cannot quietly restore the increment-sized cut."""

    def setUp(self):
        self.method = _normalized((REFERENCES_DIR / "slicing-method.md").read_text(encoding="utf-8"))
        self.method_raw = (REFERENCES_DIR / "slicing-method.md").read_text(encoding="utf-8")
        self.playbook = _normalized((PLAYBOOKS_DIR / "cut.md").read_text(encoding="utf-8"))
        self.reviewer = _normalized(
            (REFERENCES_DIR / "cut-reviewer-prompt.md").read_text(encoding="utf-8")
        )
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        self.handoff = _normalized((SHARED / "handoff-format.md").read_text(encoding="utf-8"))

    def test_the_method_makes_a_slice_earn_its_issue_over_a_phase(self):
        self.assertIn("### 4.1 What earns a slice (story altitude only)", self.method_raw)
        self.assertIn("The alternative is a phase.", self.method)
        self.assertIn(
            "Would a stakeholder watching the board care that this closed, apart from its neighbours?",
            self.method,
        )
        self.assertIn("The milestone pass.", self.method)
        self.assertIn("A slice per increment", self.method)

    def test_no_count_target_is_set(self):
        """A sub-cap number is read as a target (the planner's #959); the count is the outcomes."""
        self.assertIn("There is no target count", self.method)
        self.assertNotIn("A handful is usually right", self.method)

    def test_no_cut_is_a_legitimate_outcome_scoped_to_a_fresh_story_cut(self):
        self.assertIn("No cut is a legitimate outcome.", self.method)
        self.assertRegex(self.method, r"applies to a `fresh` cut only")

    def test_the_playbook_routes_the_no_cut_past_the_reviewer_and_the_write_gate(self):
        self.assertIn("milestone pass (method §4.1)", self.playbook)
        self.assertRegex(self.playbook, r"skip S3 \(nothing to review\)")
        self.assertIn("A no cut skips the table and the write gate", self.playbook)
        self.assertIn("File nothing — plan multi-phase", self.playbook)
        self.assertIn("Cut anyway", self.playbook)
        self.assertIn("No cut — plan it multi-phase", self.playbook)

    def test_the_no_cut_shape_files_nothing_and_routes_to_the_planner(self):
        self.assertIn("**No cut — plan it multi-phase.**", self.renderings)
        block = self.renderings.split("**No cut — plan it multi-phase.**", 1)[1]
        fence = block.split("```", 2)[1]
        self.assertIn("**Issue:**", fence)
        self.assertNotIn("**Slices:**", fence)
        self.assertNotIn("**Grounding:**", fence)
        self.assertIn("/ctrl-freaq:planner", fence)

    def test_cut_anyway_milestones_are_fixed_inputs_so_the_no_cut_card_cannot_loop(self):
        self.assertIn("re-runs with them as fixed inputs", self.playbook)
        self.assertIn("the milestone pass never folds them", self.playbook)
        self.assertIn("operator-named", self.playbook)
        self.assertRegex(self.reviewer, r"Never recommend merging two `operator-named` children")

    def test_the_reviewer_carries_the_milestone_signal(self):
        self.assertIn("One milestone → recommend MERGE", self.reviewer)
        self.assertIn("recommend no cut", self.reviewer)
        self.assertRegex(self.reviewer, r"Signal: <1 \| 2 \| 3 \| milestone \|")

    def test_the_shared_schema_lists_no_cut_among_the_exits_that_file_nothing(self):
        self.assertIn("declined write gate, no cut, any refusal", self.handoff)


class HandoffRenderingTests(unittest.TestCase):
    def setUp(self):
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_reference_cites_the_shared_schema_as_the_owner(self):
        self.assertIn("_shared/handoff-format.md", self.renderings)

    def test_forward_route_is_the_planner(self):
        self.assertIn("/ctrl-freaq:planner", self.renderings)

    def test_slices_line_uses_the_designator_form(self):
        self.assertRegex(self.renderings, r"\*\*Slices:\*\* #\d+ \d+/S1 \(open\)")

    def test_progress_count_form_is_shown_too(self):
        self.assertRegex(self.renderings, r"\*\*Slices:\*\* \d+ of \d+ closed · next:")

    def test_grounding_line_present_on_filed_exits_and_omitted_otherwise(self):
        self.assertRegex(self.renderings, r"\*\*Grounding:\*\*")
        self.assertRegex(self.renderings, r"omitted on every exit that\s+filed nothing")

    def test_declined_gate_shape_states_zero_writes(self):
        self.assertRegex(self.renderings, r"\*\*Zero GitHub writes happened\*\*")

    def test_no_slices_state_marker_was_invented(self):
        """The closed sets are unchanged: absence is expressed by OMITTING the line."""
        self.assertNotRegex(self.renderings, r"slices: (✓|✗)")

    def test_binding_language_is_prose_not_inside_a_code_fence(self):
        for lineno, line, in_fence in _fence_stripped_lines(
            REFERENCES_DIR / "handoff-renderings.md"
        ):
            if "Copy the shape verbatim" in line:
                self.assertFalse(in_fence, "binding rule at line %d is inside a fence" % lineno)


class ContractTokenGateTests(unittest.TestCase):
    def test_no_forbidden_contract_tokens(self):
        for path in _iter_md(SKILL_DIR):
            text = path.read_text(encoding="utf-8")
            match = FORBIDDEN_CONTRACT_TOKENS.search(text)
            self.assertIsNone(match, "%s carries %r" % (path, match.group(0) if match else None))

    def test_no_retired_v1_names_or_namespace(self):
        for path in _iter_md(SKILL_DIR):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(retired_name_hits(text), [], str(path))
            self.assertNotIn(V1_INVOCATION_PREFIX, text, str(path))

    def test_no_raw_persist_or_gather_writes_in_code_fences(self):
        raw_write = re.compile(
            r"\bgh\s+(issue|pr)\s+(create|edit|comment|review|close|reopen)\b"
            r"|\bgh\s+api\b[^\n]*\bDELETE\b"
        )
        for path in _iter_md(SKILL_DIR):
            for lineno, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                self.assertIsNone(
                    raw_write.search(line), "%s:%d raw gh write in a fence: %s" % (path, lineno, line)
                )

    def test_no_ref_arithmetic_in_code_fences(self):
        banned = re.compile(r"git\s+show\s+[^\s]+:|git\s+grep\s+(?!-)")
        for path in _iter_md(SKILL_DIR):
            for lineno, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                self.assertIsNone(banned.search(line), "%s:%d ref arithmetic: %s" % (path, lineno, line))

    def test_no_w_slash_shorthand(self):
        shorthand = re.compile(r"\bw/")
        for path in _iter_md(SKILL_DIR):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(shorthand.search(line), "%s:%d %r" % (path, lineno, line))

    def test_no_bare_stack_assumption(self):
        stack = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)
        for path in _iter_md(SKILL_DIR):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(stack.search(line), "%s:%d stack assumption: %s" % (path, lineno, line))


class PersistDryRunTests(unittest.TestCase):
    """The one write this stage prescribes, run through `gh_persist.py --dry-run`: conformant
    envelope, `would_run` present, `--parent` visible in it, no live gh call."""

    def _run_persist(self, args, fixture_case=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        return subprocess.run(
            [sys.executable, str(GH_PERSIST)] + args,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def test_slice_create_with_parent_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "slice-S1.md"
            body.write_text(
                "**Parent:** #103 — Patient: access\n\n## Outcome\nFirst login works.\n",
                encoding="utf-8",
            )
            result = self._run_persist(
                [
                    "create",
                    "octo/widgets",
                    str(body),
                    "--title",
                    "103/S1 — patient completes first login",
                    "--parent",
                    "103",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            envelope = json.loads(result.stdout.strip())
            envelope_asserts.assert_full_envelope_conformance(envelope)
            self.assertEqual(envelope["op"], "create")
            self.assertTrue(envelope["dry_run"])
            self.assertIn("would_run", envelope)
            self.assertIn("--parent", envelope["would_run"])
            self.assertNotIn("url", envelope)

    def test_promotion_title_edit_title_dry_run(self):
        # cut.md S0: promotion rewrites the title as well as the body/labels. Classification is
        # lexical (_shared/epic-story-hierarchy.md), so an `Epic:`-less title leaves #N a non-epic.
        result = self._run_persist(
            ["edit-title", "octo/widgets", "103", "--title", "Epic: patient access", "--dry-run"]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = json.loads(result.stdout.strip())
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["op"], "edit-title")
        self.assertTrue(envelope["dry_run"])
        self.assertIn("would_run", envelope)
        self.assertEqual(envelope["title"], "Epic: patient access")
        self.assertNotIn("url", envelope)

    def test_empty_body_is_gated_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "slice-S1.md"
            body.write_text("", encoding="utf-8")
            result = self._run_persist(
                ["create", "octo/widgets", str(body), "--title", "103/S1 — x", "--parent", "103"]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            envelope = json.loads(result.stdout.strip())
            self.assertEqual(envelope["status"], "needs_decision")
            self.assertEqual(envelope["decision"]["code"], "EMPTY_BODY_FILE")


class SharedContractTests(unittest.TestCase):
    """The slicer's schema deltas landed in the shared files, not restated locally."""

    def setUp(self):
        self.handoff = (SHARED / "handoff-format.md").read_text(encoding="utf-8")
        self.hierarchy = (SHARED / "epic-story-hierarchy.md").read_text(encoding="utf-8")

    def test_handoff_format_counts_the_slicer_among_the_pipeline_skills(self):
        self.assertRegex(self.handoff, r"six pipeline skills")
        self.assertIn("`slicer`", self.handoff)

    def test_handoff_format_owns_the_slices_line(self):
        self.assertIn("**Slices:**", self.handoff)

    def test_grounding_omission_rule_covers_the_slicer(self):
        self.assertRegex(self.handoff, r"planner and \*\*slicer\*\*")

    def test_re_route_arms_are_registered(self):
        self.assertRegex(self.handoff, r"planner → slicer")
        self.assertRegex(self.handoff, r"slicer → setup")

    def test_hierarchy_defines_the_slice_and_its_bar(self):
        self.assertRegex(self.hierarchy, r"deliverable slice\*\* is a milestone")
        self.assertRegex(_normalized(self.hierarchy), r"a phase list re-filed as issues")
        self.assertRegex(self.hierarchy, r"independently \*\*demonstrable\*\*")
        self.assertRegex(self.hierarchy, r"A slice is never itself sliced")

    def test_hierarchy_names_the_slicer_as_the_writer_of_both_edges(self):
        """#16: one writer for the whole hierarchy. Before it, epic→story was the drafter's and the
        file stated that no skill established the relation after the fact — adoption is now the one
        exception, so both clauses had to change together."""
        self.assertRegex(self.hierarchy, r"Writer, story→slice — `slicer`")
        self.assertRegex(self.hierarchy, r"Writer, epic→story — `slicer`")
        self.assertRegex(self.hierarchy, r"after-the-fact path is adoption")
        self.assertNotRegex(self.hierarchy, r"No skill establishes the relation after the fact")

    def test_the_epic_off_ramp_arm_is_registered_as_a_slicer_arm(self):
        """Both seam-gate off-ramps land on the slicer, so the shared re-route table must not still
        register an epic-shaped arm pointing at the drafter."""
        self.assertRegex(self.handoff, r"planner → slicer, epic-shaped")
        self.assertNotRegex(self.handoff, r"planner → drafter")

    def test_hierarchy_records_the_closing_contract_and_the_reopen_gap(self):
        self.assertRegex(self.hierarchy, r"closes a slice when its last serving phase ships")
        self.assertRegex(self.hierarchy, r"Reopen is unowned")


class ThreadWalkAuthorRuleTests(unittest.TestCase):
    """#186: the cut's thread read applies #179's author rule to marker-shaped comments."""

    def test_cut_thread_read_states_the_author_rule(self):
        text = (PLAYBOOKS_DIR / "cut.md").read_text(encoding="utf-8")
        walk = text[text.index("Then read the target's"):]
        walk = walk[:walk.index("Read the docs `facts.grounding_docs` declares")]
        for anchor in THREAD_RULE_ANCHORS:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, walk)


if __name__ == "__main__":
    unittest.main()
