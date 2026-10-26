"""Offline tests for the planner skill (docs/implementation.md S13).

The offline surfaces the S13 DoD / Testing section names:

1. **Routing-table fixtures** (`vector` → `playbooks/<file>`). The router's visible routing table
   (`skills/planner/SKILL.md` §2) maps a prep-derived `vector` to the one playbook a session reads.
   These parse that table, assert it covers exactly the four routable playbooks
   (single / epic / story-jit / revise), that each points at a real `playbooks/<file>`, and that the
   type→playbook mapping is byte-consistent with `prep_planner._suggested_playbook` (architecture.md §5
   "prep proposes; the router confirms").

2. **The interleaving pattern-grep, committed as a validator** (S13 DoD box: playbooks are
   type-conditional-free by construction). Greps the four routable playbooks and the shared spine for
   cross-route conditionals and fails on a hit (both `if … <route> … else …` and "when the issue is a
   <route>" prose branches). Patterns broadened per the S10 carried advisory to the planner's route set.

3. **`--dry-run` persist envelopes.** Every GitHub write the playbooks specify goes through
   `gh_persist.py` (SKILL.md §3; the planner has no scriptless raw-gh executor). These run each such
   invocation with `--dry-run` against the offline shim and assert a conformant envelope.

4. **Rendering fixtures** — the plan-comment schema byte-compat against the S1 capture; the footer rule
   (origin/main vs bare-branch + short-SHA); the `**Open questions:**` line in every handoff shape that
   has OQs; the bug-(b) composite epic+story worked example; the bug-(a) falsifiable "(not filed)" rule.

Plus the structural bar (router ≤150, router+largest playbook ≤251 = half of v1's 503) and the operator
gate/pins coverage.

No network: gh_persist.py's `gh` calls resolve to the offline shim via tests/run.py's PATH wiring; the
dry-run path performs no gh call at all (asserted).
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
SKILL_DIR = REPO_ROOT / "skills" / "planner"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
REFERENCES_DIR = SKILL_DIR / "references"
SHARED_DIR = REPO_ROOT / "skills" / "_shared"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"
S1_PLAN_CAPTURE = REPO_ROOT / "docs" / "specs" / "examples" / "implementation-plan.md"

# The four routable playbooks (the spine is a shared file, not a routable route).
ROUTABLE_PLAYBOOKS = {"single.md", "epic.md", "story-jit.md", "revise.md"}
SPINE = "plan-spine.md"

# Half of the v1 planner SKILL.md (503 lines, docs/specs/baseline.md §1) = 251 (floor).
V1_HALF_BAR = 503 // 2

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_planner  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, shimenv  # noqa: E402
from tests.support.retired_tokens import FORBIDDEN_CONTRACT_TOKENS  # noqa: E402


# ---------------------------------------------------------------------------
# Router routing-table parse
# ---------------------------------------------------------------------------

_ROUTE_ROW_RE = re.compile(
    r"^\|\s*(?P<vector>[^|]+?)\s*\|\s*`(?P<playbook>playbooks/[a-z0-9-]+\.md)`\s*\|"
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


def _first_fenced_block(text):
    out, in_fence = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_fence:
                out.append(line)
                break
            in_fence = True
        if in_fence:
            out.append(line)
    return "\n".join(out)


class RouterRoutingTableTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")
        self.rows = _parse_router_routing_table(self.router_text)
        self.playbooks = [pb for _, pb in self.rows]

    def test_router_exists_and_has_no_model_effort_pins(self):
        # Model/effort are not pinned — skills inherit the invoking session's model and effort.
        self.assertTrue(ROUTER.is_file(), "router SKILL.md must exist")
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: planner", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)

    def test_routing_table_covers_exactly_the_four_routable_playbooks(self):
        self.assertEqual(
            {Path(pb).name for pb in self.playbooks},
            ROUTABLE_PLAYBOOKS,
            "router routing table must map exactly the four routable playbooks, got %r"
            % (sorted(Path(pb).name for pb in self.playbooks),),
        )

    def test_exactly_four_routable_playbooks_plus_one_spine_on_disk(self):
        on_disk = {p.name for p in PLAYBOOKS_DIR.glob("*.md")}
        self.assertEqual(
            on_disk,
            ROUTABLE_PLAYBOOKS | {SPINE},
            "playbooks/ must be exactly the four routable playbooks + the one shared spine, got %r"
            % (sorted(on_disk),),
        )

    def test_every_routed_playbook_file_exists(self):
        for vector, playbook in self.rows:
            target = SKILL_DIR / playbook
            self.assertTrue(
                target.is_file(),
                "routing table maps %r -> %r but %s does not exist" % (vector, playbook, target),
            )

    def test_table_matches_prep_suggested_playbook(self):
        # architecture.md §5: prep proposes `suggested_playbook`; the router confirms against the table.
        # (type, mode, parent_epic_open) -> playbook.
        cases = [
            ("standard", "fresh", False, "single.md"),
            ("story", "fresh", False, "single.md"),   # standalone story = single-issue path
            ("story", "fresh", True, "story-jit.md"),
            ("story", "revise", True, "story-jit.md"),  # story-jit owns fresh AND revise
            ("epic", "fresh", False, "epic.md"),
            ("standard", "revise", False, "revise.md"),
            ("epic", "revise", False, "revise.md"),
        ]
        table_basenames = {Path(pb).name for pb in self.playbooks}
        for issue_type, mode, parent_open, expected in cases:
            self.assertEqual(
                prep_planner._suggested_playbook(issue_type, mode, parent_open),
                expected,
                "prep _suggested_playbook(%r, %r, %r) should be %r"
                % (issue_type, mode, parent_open, expected),
            )
            self.assertIn(
                expected, table_basenames, "the router table must route to %s" % expected
            )

    def test_workspace_mismatch_rule_is_content_based_and_carries_ground_ambient(self):
        # 4.33.0: the router states the content rule, names the flag the "plan anyway" answer sets,
        # and requires carrying it on refresh — prep re-asks rather than silently flipping
        # plan_ref, so dropping the flag mid-session would re-raise the card. And the live failure
        # this closes: a session that grounded on its checkout itself after prep refused.
        self.assertIn("by **content**, never by branch name", self.router_text)
        self.assertIn("--ground-ambient", self.router_text)
        self.assertRegex(self.router_text, r"carry that flag on every later prep run this session, `--refresh` included")
        self.assertIn("never a reason to ground on the checkout yourself", self.router_text)
        # The run that answers the card must be a FULL prep: --refresh skips grounding entirely.
        self.assertIn("re-run the **full** prep with `--ground-ambient`", self.router_text)
        prep_source = Path(prep_planner.__file__).read_text(encoding="utf-8")
        self.assertIn('"--ground-ambient"', prep_source, "prep must define the flag the router names")

    def test_all_four_playbooks_read_the_shared_spine(self):
        # For the planner, author-and-verify-a-plan is shared across every route; each routed playbook
        # reads the one shared spine (architecture.md §5).
        self.assertTrue((PLAYBOOKS_DIR / SPINE).is_file())
        for name in ROUTABLE_PLAYBOOKS:
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn(SPINE, text, "%s must read the shared spine %s" % (name, SPINE))


# ---------------------------------------------------------------------------
# Router structural bar
# ---------------------------------------------------------------------------


class RouterStructuralBarTests(unittest.TestCase):
    def test_router_at_most_150_lines(self):
        n = len(ROUTER.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(n, 150, "router SKILL.md is %d lines (bar: <= 150)" % n)

    def test_router_plus_largest_playbook_at_most_half_v1(self):
        router_lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        playbook_lines = {
            p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in PLAYBOOKS_DIR.glob("*.md")
        }
        largest = max(playbook_lines.values())
        self.assertLessEqual(
            router_lines + largest,
            V1_HALF_BAR,
            "router (%d) + largest playbook (%d) = %d exceeds %d (half of v1's 503): %r"
            % (router_lines, largest, router_lines + largest, V1_HALF_BAR, playbook_lines),
        )


class SubIssueReconciliationRuleTests(unittest.TestCase):
    """#18: the phase→sub-issue contract, pinned where prose has no compiler.

    The cardinality rule is stated ONCE (the issue's "stated once wherever it lands"), the grammar is
    documented OUTSIDE plan-schema.md's frozen first fence, the gate offers exactly the three recorded
    operator options and never silently re-cuts, and the two hard limits (no writes to a sub-issue, a
    one-way plan→sub-issue pointer) are on the page.
    """

    RECONCILIATION = REFERENCES_DIR / "sub-issue-reconciliation.md"
    SCHEMA = REFERENCES_DIR / "plan-schema.md"
    REVIEWER = REFERENCES_DIR / "plan-reviewer-prompt.md"
    REVISE = REFERENCES_DIR / "revise-reconciliation.md"
    # Every route that can face a pre-sliced target: fresh standalone (`single.md`), a story under an
    # open epic in either mode (`story-jit.md`), and a standalone revise (`revise.md` — the route where
    # the diff is richest, since a prior plan exists). An epic's sub-issues are STORIES, so `epic.md`
    # stays out; an epic revise reaches `revise.md` but never carries `facts.slices`, so its
    # fact-keyed bullet correctly no-ops.
    SLICE_ROUTES = ("single.md", "story-jit.md", "revise.md")

    def test_reference_exists_and_is_read_by_both_non_epic_routes(self):
        self.assertTrue(self.RECONCILIATION.is_file())
        for name in self.SLICE_ROUTES:
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("sub-issue-reconciliation.md", text, name)

    def test_epic_playbook_does_not_read_it(self):
        # The mechanical encoding of "an epic's sub-issues are stories, not slices".
        text = (PLAYBOOKS_DIR / "epic.md").read_text(encoding="utf-8")
        self.assertNotIn("sub-issue-reconciliation.md", text)

    def test_playbooks_key_on_the_fact_not_the_type(self):
        # `facts.slices` presence is the trigger; a type branch here would trip the interleaving grep
        # and would also be the wrong contract (prd/architecture: parameterize before you playbook).
        for name in self.SLICE_ROUTES:
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("facts.slices", text, name)

    def test_cardinality_rule_is_stated_exactly_once(self):
        # Whitespace-normalized: these files hard-wrap, so a line-sensitive search would silently
        # miss a real third copy that happens to wrap differently (and would fail on a harmless
        # re-wrap of the reference itself).
        needle = "N:1 and total over the OPEN"
        hits = [
            p.name
            for p in _iter_md(SKILL_DIR)
            if needle in re.sub(r"\s+", " ", p.read_text(encoding="utf-8"))
        ]
        # plan-schema.md states the GRAMMAR and defers the rule. The reviewer prompt restates it for
        # its own isolated context — a context-blind sub-agent prompt cannot follow a link, so that
        # copy is a rendering, not a second authority.
        self.assertIn("plan-reviewer-prompt.md", hits, "expected the reviewer's own rendering: %r" % hits)
        self.assertEqual(
            [n for n in hits if n != "plan-reviewer-prompt.md"],
            ["sub-issue-reconciliation.md"],
            "the cardinality rule must be stated once, in the reconciliation reference: %r" % hits,
        )

    def test_substrate_case_is_named_explicitly(self):
        for path in (self.RECONCILIATION, self.SCHEMA):
            text = path.read_text(encoding="utf-8")
            self.assertIn("sub-issue: (none)", text, path.name)
            self.assertIn("substrate", text, path.name)

    def test_grammar_is_documented_outside_the_frozen_first_fence(self):
        # PlanSchemaByteCompatTests pins the first fence byte-identical to the S1 capture; documenting
        # the key inside it would force an edit to the frozen v1 record and hollow out that assertion.
        text = self.SCHEMA.read_text(encoding="utf-8")
        self.assertIn("sub-issue:", text)
        self.assertNotIn("sub-issue", _first_fenced_block(text))

    def test_gate_offers_the_three_options_and_never_silently_re_cuts(self):
        text = self.RECONCILIATION.read_text(encoding="utf-8")
        self.assertIn("never silently re-cuts", text)
        for option in (
            "Re-cut the phases to cover the change",
            "Record the sub-issue as out-of-scope, with a disposition",
            "Stop and route the sub-issue set back to whoever authors sub-issues",
        ):
            self.assertIn(option, text)

    def test_no_writes_and_one_way_pointer_are_stated(self):
        text = self.RECONCILIATION.read_text(encoding="utf-8")
        self.assertIn("writes nothing to a sub-issue", text)
        self.assertIn("one-way", text)
        self.assertIn("never cites a phase number", text)

    def test_rescope_is_described_as_a_suspicion_not_proof(self):
        text = self.RECONCILIATION.read_text(encoding="utf-8")
        self.assertIn("not proof", text)
        self.assertIn("over-reports", text)

    def test_reviewer_carries_live_slices_and_the_n_to_one_carve_out(self):
        text = self.REVIEWER.read_text(encoding="utf-8")
        self.assertIn("<<live_slices>>", text)
        self.assertIn("open sub-issue no phase names", text)
        # The single most important sentence in the amendment: without it a reviewer imports
        # `closes-dod`'s exactly-once rule by analogy and BLOCKERs every legal N:1 plan.
        self.assertIn("never a finding", text)
        self.assertIn('do not import the `closes-dod` "exactly once" rule here', text)

    def test_both_slice_routes_pass_live_slices_to_the_reviewer(self):
        for name in self.SLICE_ROUTES:
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("<<live_slices>>", text, name)

    def test_closed_sub_issue_defers_to_the_shipped_phase_rules(self):
        text = self.REVISE.read_text(encoding="utf-8")
        self.assertIn("behaves like a **shipped phase**", text)
        self.assertIn("no second, parallel rule set", text)

    def test_router_lists_the_slices_fact_and_the_new_gate(self):
        text = ROUTER.read_text(encoding="utf-8")
        self.assertIn("`slices`", text)
        self.assertIn("sub-issue reconciliation", text)

    def test_spine_is_unchanged_at_its_recorded_length(self):
        # The 251 bar is a knife edge (router 120 + spine 131 = 251). Fail at the CAUSE — a spine
        # edit — rather than only at the sum, whose message implicates whichever file was touched
        # last. Authorized offsets: #45 (the S7 parse gate cost the spine +6, paid back with -4 in
        # its intro and -3 in SKILL.md); 4.32.0 (the S1 DoD-heading card cost the spine +1, paid with
        # -1 in SKILL.md). Stays assertEqual — assertLessEqual would delete the tripwire rather than
        # re-arm it.
        n = len((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").splitlines())
        self.assertEqual(n, 131, "plan-spine.md is %d lines; the 251 bar assumes 131" % n)


class FileLikeSpanCheckTests(unittest.TestCase):
    """#163: the plan reviewer's Dimension 2 reports a file-like span that is not a repo-relative path, and
    never a dotted symbol."""

    def setUp(self):
        text = (REFERENCES_DIR / "plan-reviewer-prompt.md").read_text(encoding="utf-8")
        start = text.index("2. **Codebase coherence.**")
        self.dim2 = " ".join(text[start:text.index("3. **Goal coherence.**", start)].split())

    def test_dimension_2_carries_the_file_like_span_rule(self):
        self.assertIn("**File-like spans are repo-relative paths.**", self.dim2)
        self.assertIn("Under `## Changes (file-level)` and `## Test plan`", self.dim2)
        self.assertIn("an entry's leading span", self.dim2)
        self.assertIn(r"`\.[A-Za-z0-9]{1,8}$`", self.dim2)
        self.assertIn("Report a bare basename, or a path relative to a subdirectory, as a BLOCKER", self.dim2)

    def test_dotted_symbols_are_excluded(self):
        self.assertIn("A dotted symbol (`parse.planned_paths`, `facts.out_of_plan.reasons_trusted`) names no file and "
                      "is never reported", self.dim2)


class ShapeTriageOffRampTests(unittest.TestCase):
    """The seam gate's shape triage offers TWO off-ramps, selected by the independence bar the seams
    clear (#17): shippable-independent → the epic split; demonstrable-independent → the slicer.

    Since #16 BOTH off-ramps land on the slicer — it is one operation at two altitudes, and the triage
    picks the bar, not the skill. The asymmetry between them survives that and is still the load-bearing
    part: off-ramp A posts a seam-analysis comment the promotion splits *per* (and reshapes the target
    into an Epic), while off-ramp B posts **nothing**, because a demonstrable-altitude cut re-derives from
    the repo's grounding docs and a planner-authored proposal would compete with it. An editor who
    "harmonizes" the two by adding a comment to B creates exactly the stale second decomposition #18's
    one-way-pointer rule exists to prevent.
    """

    def setUp(self):
        raw = (REFERENCES_DIR / "seam-dispositions.md").read_text(encoding="utf-8")
        self.seams = re.sub(r"\s+", " ", raw.replace("**", ""))
        self.renderings = re.sub(
            r"\s+", " ", (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        )

    def test_triage_selects_on_the_independence_bar(self):
        self.assertIn("Shape triage", self.seams)
        self.assertIn("demonstrable", self.seams)
        self.assertIn("shippable", self.seams)
        self.assertRegex(self.seams, r"Slice first")
        self.assertRegex(self.seams, r"Split as epic")

    def test_both_off_ramp_flows_exist_and_are_distinguished(self):
        self.assertIn("Off-ramp A", self.seams)
        self.assertIn("Off-ramp B", self.seams)

    def test_off_ramp_b_posts_nothing(self):
        self.assertRegex(self.seams, r"On \"Slice first\": post nothing at all")
        self.assertRegex(self.seams, r"asymmetry with off-ramp A is deliberate")

    def test_off_ramp_b_is_a_round_trip_back_through_reconciliation(self):
        self.assertRegex(self.seams, r"slicer hands back here")
        self.assertIn("sub-issue-reconciliation.md", self.seams)

    def test_rendering_exists_and_routes_to_the_slicer(self):
        self.assertIn("Slice first chosen", self.renderings)
        self.assertIn("/ctrl-freaq:slicer", self.renderings)

    def test_demonstrable_milestones_recommend_a_multi_phase_plan_not_slicing(self):
        """Slicing is the operator's tracking choice, never the planner's default: every increment
        becomes a phase on this branch either way, and recommending "Slice first" sent issues a
        multi-phase plan would hold to the slicer, which then filed one slice per increment. The card
        fires on two or more milestones, not on size, so recommending one issue never contradicts
        the premise it fired on."""
        self.assertIn("Plan as one issue (recommended) + Slice first (off-ramp B)", self.seams)
        self.assertIn(
            "Recommend Plan as one issue when the milestones are only demonstrable-independent",
            self.seams,
        )
        self.assertNotIn("Recommend Slice first", self.seams)
        self.assertIn("two or more milestones", self.seams)
        self.assertNotIn("Too large to plan as one unit", self.renderings)

    def test_the_milestone_row_never_re_asks_once_the_issue_is_sliced(self):
        """An issue with slices has two or more milestones by construction, so without this guard
        every planner run after the slicer hands back would re-ask the cut the operator just made."""
        self.assertIn("never fires when `facts.slices` is non-empty", self.seams)

    def test_every_route_offering_slice_first_renders_its_handoff(self):
        """revise offers both off-ramps for a standalone issue, so it must name both abort shapes."""
        for name in ("single.md", "story-jit.md", "revise.md"):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("Slice first chosen", text, name)

    def test_the_epic_off_ramp_routes_to_the_slicer_not_the_drafter(self):
        """#16 retargeted off-ramp A. The drafter no longer decomposes epics, so a handoff still
        pointing there would hand the operator a command that does nothing."""
        self.assertIn("Epic-shaped, planning aborted", self.renderings)
        self.assertRegex(self.renderings, r"/ctrl-freaq:slicer promote #\d+ to an Epic")
        self.assertNotRegex(self.renderings, r"/ctrl-freaq:drafter revise #\d+ as an Epic")
        self.assertIn("the slicer promoting #N to an Epic", self.seams)

    def test_the_epic_off_ramp_still_posts_its_seam_analysis_comment(self):
        """The one write off-ramp A makes, and the reason the two off-ramps stay asymmetric: a
        promotion argues from the seam inventory, so the comment is a genuine handover artifact."""
        self.assertIn("seam-analysis.md", self.seams)
        self.assertIn("gh_persist.py comment", self.seams)
        # And it must never carry the plan marker, which would flip the next planner run to revise.
        self.assertRegex(self.seams, r"must not begin with `<!-- implementation-plan:v1 -->`")

    def test_an_unfiled_epic_story_set_also_routes_to_the_slicer(self):
        """The third drafter-epic pointer #16 had to retarget: an epic whose plan is posted but whose
        stories are still plain bullets. The planner files no issues, and decomposition is now the
        slicer's at both altitudes."""
        epic = re.sub(
            r"\s+", " ", (PLAYBOOKS_DIR / "epic.md").read_text(encoding="utf-8").replace("**", "")
        )
        self.assertRegex(epic, r"Stories not filed.*forward to the slicer to file them")
        self.assertIn("/ctrl-freaq:slicer", epic)
        self.assertIn("not yet filed as issues", self.renderings)

    def test_no_planner_surface_still_sends_epic_decomposition_to_the_drafter(self):
        """A single sweep over every planner prompt file: the drafter is still named for filing an
        ordinary issue and for follow-ups, but never for splitting or promoting an epic."""
        for path in sorted(SKILL_DIR.rglob("*.md")):
            text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
            for pattern in (
                r"/ctrl-freaq:drafter[^`\n]*(?:as an Epic|epic)",
                r"forward to the \*\*drafter\*\* to file them",
            ):
                self.assertNotRegex(text, pattern, "%s: %s" % (path.name, pattern))

    def test_single_offers_both_off_ramps_and_story_jit_only_the_slicer(self):
        single = (PLAYBOOKS_DIR / "single.md").read_text(encoding="utf-8")
        story_jit = (PLAYBOOKS_DIR / "story-jit.md").read_text(encoding="utf-8")
        self.assertIn("`off-ramp: epic + slicer offered`", single)
        self.assertIn("`off-ramp: slicer offered — epic not offered`", story_jit)
        # A story is never promoted to an epic — that stays an epic-contract re-route.
        self.assertRegex(
            re.sub(r"\s+", " ", story_jit), r"never promote a story to an epic"
        )


class PlaybookInterleavingGrepTests(unittest.TestCase):
    """A playbook is a linear narrative for exactly one route — zero cross-route conditionals. Fails on
    either an `if … <route> … else …` construct or a `when the issue/type is a <route>` prose branch, over
    the four routable playbooks and the shared spine. Patterns broadened (S10 carried advisory) to the
    planner's route set — the routing types are single/epic/story-jit/revise plus the facts type names
    standard/story; the scale-to-work sub-classes (bug/feature/multi-phase) are legitimately named within
    single.md and are NOT routes, so they are not interleaving."""

    _ROUTES = r"(single|epic|story-jit|revise|standard|story)"
    _IF_ELSE = re.compile(r"\bif\b[^.\n]{0,50}\b" + _ROUTES + r"\b[^.\n]{0,50}\belse\b", re.IGNORECASE)
    _WHEN_TYPE = re.compile(
        r"\bwhen (the (issue|type) is|it.?s)\s+(a |an )?" + _ROUTES + r"\b", re.IGNORECASE
    )

    def test_playbooks_have_no_cross_route_conditionals(self):
        for playbook in PLAYBOOKS_DIR.glob("*.md"):
            text = playbook.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                self.assertIsNone(
                    self._IF_ELSE.search(line),
                    "playbook %s:%d looks like a cross-route conditional: %r" % (playbook.name, i, line),
                )
                self.assertIsNone(
                    self._WHEN_TYPE.search(line),
                    "playbook %s:%d looks like a when-<route> prose branch: %r" % (playbook.name, i, line),
                )


class ContractTokenGateTests(unittest.TestCase):
    """Grep gates over skills/planner/ — zero retired-executor tokens, zero v1 skill-invocation namespace strings,
    zero GATHER_/PERSIST_ op names, zero §P IDs, zero raw persist/gather WRITES in fences, zero
    ref-arithmetic in fences, zero w/ shorthand. The `planned` label and the HARD-revise PR-close (both
    write-path gaps at S13's first pass) are now real `gh_persist.py edit-labels`/`close-pr` ops
    (authorized in-step), so no raw-`gh` prose workaround remains anywhere in skills/planner/."""

    def test_no_github_ops_or_old_names_or_op_names_or_pids(self):
        forbidden = FORBIDDEN_CONTRACT_TOKENS
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = forbidden.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden contract token under skills/planner/ at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_no_raw_persist_or_gather_writes_in_code_fences(self):
        # A persist/gather WRITE bypassing gh_persist.py. `gh_persist.py edit-body`/`edit-labels`/
        # `close-pr` are NOT matches (they are `gh_persist.py`, not `gh issue|pr`). The reviewer
        # prompt's `gh issue view` self-fetch is a READ, not a write, and doesn't match. Every write
        # skills/planner/ specifies now routes through gh_persist.py — zero raw `gh` writes anywhere,
        # fenced or not.
        raw_write = re.compile(
            r"\bgh\s+(issue|pr)\s+(create|edit|comment|review|close|reopen)\b"
            r"|\bgh\s+api\b[^\n]*\bDELETE\b"
        )
        for path in _iter_md(SKILL_DIR):
            for i, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                hit = raw_write.search(line)
                self.assertIsNone(
                    hit,
                    "raw gh persist/gather WRITE in a code fence at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_no_ref_arithmetic_in_code_fences(self):
        ref_arith = re.compile(r"\bgit\s+show\s+\S+:\S|\bgit\s+grep\b[^\n]*\s+[a-z]+/[^\s-]")
        for path in _iter_md(SKILL_DIR):
            for i, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                hit = ref_arith.search(line)
                self.assertIsNone(
                    hit,
                    "ref-arithmetic in a code fence at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_no_w_slash_shorthand(self):
        w_shorthand = re.compile(r"\bw/")
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(
                    w_shorthand.search(line),
                    "banned w/ shorthand at %s:%d — %r" % (path.relative_to(REPO_ROOT), i, line),
                )


# ---------------------------------------------------------------------------
# --dry-run persist envelopes for the writes the playbooks specify
# ---------------------------------------------------------------------------


def _run_persist(args, body_text=None):
    env = shimenv.intercepted_env(base_env=os.environ, fixture_case=None)
    ctx = None
    if body_text is not None:
        ctx = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        ctx.write(body_text)
        ctx.close()
        args = [a.replace("@BODY@", ctx.name) for a in args]
    try:
        proc = subprocess.run(
            [sys.executable, str(GH_PERSIST)] + args,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
    finally:
        if ctx is not None:
            os.unlink(ctx.name)
    envelope = None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) == 1:
        envelope = json.loads(lines[0])
    return proc, envelope


class PlaybookPersistDryRunTests(unittest.TestCase):
    """Each GitHub write the planner playbooks specify (spine S8 plan-comment post + issue-body pointer;
    revise-reconciliation.md DoD body reconciliation), run with --dry-run: a conformant envelope, status
    ok, `would_run` present, exit 0, no live gh call."""

    def _assert_dry_run_ok(self, proc, envelope, expected_op):
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertIsNotNone(envelope, "expected exactly one envelope on stdout")
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope.get("status"), "ok")
        self.assertEqual(envelope.get("op"), expected_op)
        self.assertTrue(envelope.get("dry_run"))
        self.assertIn("would_run", envelope)

    def test_plan_comment_post_fresh_dry_run(self):
        # spine S8 fresh post: gh_persist.py comment <repo> issue <N> <plan.md>
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "142", "@BODY@", "--dry-run"],
            body_text="<!-- implementation-plan:v1 -->\n**Implementation plan** — #142 x — planned "
            "2026-07-11T00:00:00Z at `origin/main@a1b2c3d`\n\n## Approach\nDo the thing.\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_plan_comment_repost_revise_with_delete_marker_dry_run(self):
        # spine S8 revise: --delete-marker-id deletes the stale plan comment (post-new-before-delete-old).
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "142", "@BODY@", "--delete-marker-id", "998877", "--dry-run"],
            body_text="<!-- implementation-plan:v1 -->\n**Implementation plan** — #142 x — planned "
            "2026-07-11T00:00:00Z at `origin/main@b2c3d4e`\n\n## Approach\nRevised.\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_plan_comment_in_place_update_dry_run(self):
        # revise.md / story-jit.md: gh_persist.py edit-comment <repo> <marker-id> <plan.md> — the
        # op the revise routes now name in place of the delete-and-repost above (#38).
        proc, env = _run_persist(
            ["edit-comment", "octo/widgets", "998877", "@BODY@", "--dry-run"],
            body_text="<!-- implementation-plan:v1 -->\n**Implementation plan** — #142 x — planned "
            "2026-07-11T00:00:00Z at `origin/main@b2c3d4e`\n\n## Approach\nRevised.\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-comment")

    def test_issue_body_pointer_edit_body_dry_run(self):
        # spine S8 pointer upsert: gh_persist.py edit-body <repo> <issue> <issue-body-pointer.md>
        proc, env = _run_persist(
            ["edit-body", "octo/widgets", "142", "@BODY@", "--dry-run"],
            body_text="## Summary\nx\n\n> 📋 **Implementation plan:** see [the implementation-plan "
            "comment](https://github.com/octo/widgets/issues/142#issuecomment-1) — authored by "
            "`github-issue-planner`; re-run that skill to revise.\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-body")

    def test_revise_dod_reconciliation_edit_body_dry_run(self):
        # revise-reconciliation.md SOFT/HARD path: gh_persist.py edit-body on the reconciled body.
        proc, env = _run_persist(
            ["edit-body", "octo/widgets", "142", "@BODY@", "--dry-run"],
            body_text="## Definition of done\n- [ ] Document the export format (previously claimed by "
            "phase 1, commit abc1234 on closed PR #287)\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-body")

    def test_planned_label_apply_edit_labels_dry_run(self):
        # spine S8: gh_persist.py edit-labels <repo> <issue> --add planned (bodyless, no @BODY@).
        proc, env = _run_persist(
            ["edit-labels", "octo/widgets", "142", "--add", "planned", "--dry-run"]
        )
        self._assert_dry_run_ok(proc, env, "edit-labels")
        self.assertEqual(env.get("added"), ["planned"])

    def test_hard_revise_close_pr_with_supersession_marker_dry_run(self):
        # revise.md / revise-reconciliation.md HARD "Start fresh": gh_persist.py close-pr with the
        # byte-faithful `Re-plan superseded this PR` marker the resolver's predecessor-PR detection
        # greps for (docs/specs/resolver.md "Deterministic steps").
        proc, env = _run_persist(
            ["close-pr", "octo/widgets", "287", "--comment-file", "@BODY@", "--dry-run"],
            body_text="Re-plan superseded this PR. See updated plan at "
            "https://github.com/octo/widgets/issues/142#issuecomment-999. A new branch and PR will "
            "open at the next `/ctrl-freaq:resolver #142` run.\n",
        )
        self._assert_dry_run_ok(proc, env, "close-pr")
        self.assertIn("Re-plan superseded this PR", env.get("would_run", ""))

    def test_dry_run_makes_no_live_gh_call(self):
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "142", "@BODY@", "--dry-run"],
            body_text="<!-- implementation-plan:v1 -->\nx\n",
        )
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertTrue(env.get("dry_run"))
        self.assertNotIn("url", env, "a dry-run must not carry a live-write url")


# ---------------------------------------------------------------------------
# Rendering fixtures — plan schema byte-compat, footer rule, OQ line, bug (a)/(b)
# ---------------------------------------------------------------------------


class PlanSchemaByteCompatTests(unittest.TestCase):
    """S13 DoD box 1 (offline half): the carried plan-schema rendering must diff clean against the S1
    capture (prd.md §7 row 1, frozen artifact). The main schema fenced block is byte-identical."""

    def test_plan_schema_reference_exists(self):
        self.assertTrue((REFERENCES_DIR / "plan-schema.md").is_file())

    def test_main_schema_block_byte_identical_to_s1_capture(self):
        ours = _first_fenced_block((REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8"))
        s1 = _first_fenced_block(S1_PLAN_CAPTURE.read_text(encoding="utf-8"))
        self.assertEqual(ours, s1, "carried plan-schema block drifted from the S1 capture")

    def test_frozen_provenance_footer_preserved(self):
        # S7 adjudication (a): the artifact-footer provenance keeps the literal v1 skill name verbatim
        # (a byte-compat contract token, NOT renamed to the v2 `planner`).
        text = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        self.assertIn("_Authored by `github-issue-planner` and verified in", text)

    def test_marker_is_first_line_of_the_schema_block(self):
        block = _first_fenced_block((REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8"))
        body_lines = block.splitlines()[1:]  # drop the opening ```
        self.assertEqual(body_lines[0], "<!-- implementation-plan:v1 -->")


class ShippedPhaseRecordTests(unittest.TestCase):
    """Legacy shipped-phase records (skills/_shared/plan-shipped-phases.md). Since 4.35.0 nothing
    writes one — the build sections have their own part comment — but a plan revised before then
    still points at them, and they are the only copy of their entries. These pin the template to its
    example capture (readers still parse it), and the fold-back: every revise route reaches it, it
    runs through the script, and it is never read as a HARD `## Changes` edit.
    """

    MARKER_PREFIX = "<!-- implementation-plan-shipped:v1:phase:"

    @staticmethod
    def _fences(text):
        return re.findall(r"^```\n(.*?)^```$", text, re.S | re.M)

    def test_record_template_byte_identical_to_example(self):
        shared = (SHARED_DIR / "plan-shipped-phases.md").read_text(encoding="utf-8")
        ours = next((b for b in self._fences(shared) if b.startswith(self.MARKER_PREFIX)), None)
        self.assertIsNotNone(ours, "the record template must be present in the shared contract")
        example = (REPO_ROOT / "docs" / "specs" / "examples" / "implementation-plan-shipped-phase.md")
        theirs = self._fences(example.read_text(encoding="utf-8"))[0]
        self.assertEqual(ours, theirs, "record template drifted from its example capture")

    def test_record_marker_is_a_separate_family(self):
        self.assertFalse(self.MARKER_PREFIX.startswith("<!-- implementation-plan:v1 -->"))

    def test_both_revise_routes_reach_the_fold_back(self):
        for playbook in ("revise.md", "story-jit.md"):
            text = (PLAYBOOKS_DIR / playbook).read_text(encoding="utf-8")
            self.assertIn("references/shipped-phase-foldback.md", text, playbook)
            self.assertIn("facts.plan.shipped.present", text, playbook)

    def test_nothing_writes_a_record_any_more(self):
        # The relocation procedure is retired: no planner prompt may post a record or name its fact.
        self.assertFalse((REFERENCES_DIR / "shipped-phase-relocation.md").exists())
        for path in list(PLAYBOOKS_DIR.glob("*.md")) + list(REFERENCES_DIR.glob("*.md")) + [ROUTER]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("to_relocate", text, path.name)
            self.assertNotIn("shipped-phase-<N>.md", text, path.name)

    def test_check_runs_through_the_script(self):
        text = (REFERENCES_DIR / "shipped-phase-foldback.md").read_text(encoding="utf-8")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/plan_shipped.py check", text)
        self.assertIn('--restored "<facts.plan.shipped.body_path>"', text)
        self.assertIn("`pointer_left`", text)
        self.assertIn("`not_restored`", text)

    def test_reviewer_is_told_which_phases_shipped(self):
        self.assertIn("`shipped_phases`", (PLAYBOOKS_DIR / "plan-spine.md").read_text(encoding="utf-8"))
        flat = " ".join((REFERENCES_DIR / "plan-reviewer-prompt.md").read_text(encoding="utf-8").split())
        self.assertIn("`<<shipped_phases>>`", flat)
        self.assertNotIn("<<shipped_paths>>", flat)

    def test_fold_back_is_not_a_hard_edit(self):
        flat = " ".join((REFERENCES_DIR / "revise-reconciliation.md").read_text(encoding="utf-8").split())
        self.assertIn("**Fold-back is not an edit.**", flat)
        self.assertNotIn("A posted shipped-phase record", flat)


class PlanPartsTests(unittest.TestCase):
    """One plan, three comments (skills/_shared/plan-parts.md). The planner authors one body and a
    script cuts it, so the prompt-side rules are only where the cut runs and the persist order."""

    def test_spine_cuts_on_every_staging_and_posts_the_main_cut(self):
        spine = (PLAYBOOKS_DIR / "plan-spine.md").read_text(encoding="utf-8")
        self.assertIn("plan_parts.py split", spine)
        self.assertIn("**parts first, main last**", spine)
        self.assertIn('"<facts.scratch>/plan-main.md"', spine)
        self.assertIn("../../_shared/plan-parts.md", spine)

    def test_in_place_updates_write_the_main_cut(self):
        for playbook in ("revise.md", "story-jit.md"):
            text = (PLAYBOOKS_DIR / playbook).read_text(encoding="utf-8")
            self.assertIn('"<facts.scratch>/plan-main.md"', text, playbook)
            self.assertNotIn('<facts.plan.comment_id>\n  "<facts.scratch>/plan.md"', text, playbook)

    def test_shared_contract_names_the_split_and_the_in_place_part_edit(self):
        flat = " ".join((SHARED_DIR / "plan-parts.md").read_text(encoding="utf-8").split())
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/plan_parts.py split", flat)
        self.assertIn("`facts.plan.parts`", flat)
        self.assertIn("`unlinked` must come back empty", flat)
        self.assertIn("**parts first, main last**", flat)

    def test_shared_contract_table_matches_the_script(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import plan_parts

        text = (SHARED_DIR / "plan-parts.md").read_text(encoding="utf-8")
        for slug, sections in plan_parts.PARTS:
            row = next(line for line in text.splitlines() if line.startswith("| **%s**" % slug))
            for section in sections:
                self.assertIn("`## %s`" % section, row, slug)
            self.assertIn(plan_parts.part_marker(slug), row)

    def test_router_names_the_part_marker_family(self):
        self.assertIn("<!-- implementation-plan-part:v1:<slug> -->", ROUTER.read_text(encoding="utf-8"))


class SectionOwnershipAndSizeTests(unittest.TestCase):
    """#38 Phase 1. A 266 KB plan body could not be posted; measured, retained revise narration was 8%
    of it and ~90% was the SAME fact restated across six-to-ten sections. The schema bounded exactly one
    section (`## Approach`) and stated the cite-don't-restate rule in exactly one place (`## Doc
    grounding`), so every other section read satisfiable-by-restating. These pin the generalised rule,
    the drafting-time cap, and the reviewer's ability to run the audit at all."""

    def setUp(self):
        self.schema = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        self.reviewer = (REFERENCES_DIR / "plan-reviewer-prompt.md").read_text(encoding="utf-8")

    def test_ownership_rule_is_stated_for_the_whole_schema(self):
        self.assertIn("one owning section", self.schema)
        self.assertIn("cites it by name", self.schema)

    def test_the_three_converging_sections_carry_disjoint_obligations(self):
        # Naming the sections is not enough — the point is that each is told what it must NOT carry,
        # which is what makes "is this a restatement?" answerable at review time.
        _, _, ownership = self.schema.partition("## Section ownership and size")
        ownership = ownership.split("## The `sub-issue:` phase key")[0]
        for section in ("## Doc grounding", "## Architecture decisions", "## Risks & watchpoints"):
            self.assertIn(section, ownership, "%s has no stated obligation" % section)
        self.assertIn("No rationale", ownership)
        self.assertIn("No doc summary", ownership)
        self.assertIn("rather than re-deriving it", ownership)

    def test_ownership_rule_cites_doc_grounding_rather_than_duplicating_it(self):
        # The rule would break itself in the sentence that introduces it if it re-quoted the
        # `## Doc grounding` wording instead of pointing at it.
        self.assertEqual(
            self.schema.count("the citations, not a restatement of the approach"),
            1,
            "the ownership prose restates `## Doc grounding`'s rule instead of citing it",
        )

    def test_cap_is_stated_in_characters_at_drafting_time(self):
        self.assertIn("65,536 characters", self.schema)
        self.assertIn("body_bytes", self.schema)
        self.assertRegex(
            self.schema,
            r"Count \*\*characters\*\*, not bytes",
            "the schema must say which unit the cap is measured in",
        )

    def test_the_cap_is_the_only_size_line(self):
        # #39 stated a sub-cap threshold as "a defect to fix before posting"; the authoring session read
        # it as a budget and trimmed a 38k plan toward it twice in one revise (#959), the second time
        # to offset what the reviewer had asked it to add. The platform cap is the only size line.
        flat = " ".join(self.schema.split())
        self.assertIn("**The cap is the only size line**", flat)
        self.assertIn("you do not cut for size", flat)
        self.assertIn("never offset a reviewer-requested addition by cutting elsewhere", flat)
        self.assertIn("Only when `over_limit` names a comment, compact", flat)

    def test_the_size_read_works_on_a_fresh_draft(self):
        # The cap is per comment since 4.35.0, so the size fact is the split's, run on every staging
        # on every route — a fresh draft included, never a hand count.
        flat = " ".join(self.schema.split())
        self.assertIn("Read the size from `plan_parts.py split`'s `comments[].size` on every staging (S7)", flat)
        self.assertIn("The cap applies to **each of the three comments**", flat)
        self.assertIn("never a hand count", flat)

    def test_no_planner_prompt_names_a_sub_cap_size_budget(self):
        # A number below the cap reads as a target to a literal instruction-follower, which then trims
        # toward it instead of planning — #39's rejected per-section budgets, back as one global budget.
        budget = re.compile(
            r"half\W+(?:of\W+)?the\W+(?:\S+\W+)?cap|(?:50|fifty)\s*(?:%|per\s*cent)[^.\n]{0,40}\bcap",
            re.IGNORECASE,
        )
        for path in sorted(SKILL_DIR.rglob("*.md")):
            flat = " ".join(path.read_text(encoding="utf-8").split())
            match = budget.search(flat)
            self.assertIsNone(
                match,
                "%s names a sub-cap size budget: %r" % (path.relative_to(REPO_ROOT), match and match.group(0)),
            )

    def test_ownership_section_is_outside_the_frozen_fence(self):
        # The first fenced block is byte-frozen against the S1 capture (PlanSchemaByteCompatTests).
        # This is the guard that the new prose landed after it, not inside it.
        self.assertNotIn("## Section ownership and size", _first_fenced_block(self.schema))

    def test_reviewer_receives_the_plan_body_as_a_path(self):
        # Inlined, the sub-agent can neither measure the body nor build a section map; the audit and
        # the >50% signal are only mechanically runnable once it is a file.
        self.assertIn("Read <<plan_body>>", self.reviewer)
        self.assertIn("wc -m <<plan_body>>", self.reviewer)
        self.assertNotIn("\n  ```\n  <<plan_body>>\n  ```\n", self.reviewer)

    def test_epic_plan_and_delivery_log_are_paths_too(self):
        self.assertIn("Read <<epic_plan>>", self.reviewer)
        self.assertIn("Read <<epic_delivery_log>>", self.reviewer)

    def test_live_slices_stays_inline(self):
        # Small and prep-derived — don't over-generalize the path form.
        self.assertNotIn("Read <<live_slices>>", self.reviewer)

    def test_evidence_admits_a_plan_body_line_anchor(self):
        self.assertIn("`<<plan_body>>:<line>`", self.reviewer)

    def test_dimension_three_carries_the_section_ownership_subcheck(self):
        self.assertIn("**Section ownership** *(runs on every plan)*", self.reviewer)

    def test_size_is_evidence_never_the_violation(self):
        # The rejected alternative was numeric per-section budgets; a >50% BLOCKER would reintroduce
        # them through the back door. Over-cap IS a BLOCKER — an unpostable plan is unexecutable.
        flat = " ".join(self.reviewer.split())
        self.assertIn("**Size is evidence, never the violation**", flat)
        self.assertIn("not a finding in itself", flat)
        self.assertIn("over the 65,536-character cap** is a BLOCKER", flat)

    def test_spine_passes_the_staged_path_and_holds_its_line_count(self):
        spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.assertIn("Stage the plan body to `<facts.scratch>/plan.md`", spine)
        self.assertIn("with that path", spine)
        self.assertNotIn("with the plan body", spine)

    def test_review_loop_restages_the_plan_before_every_pass(self):
        # PR #39 review finding 1. Passing the body by path decouples "the plan" from "the file":
        # the loop applies findings to the plan, so a pass that re-reads the prior file re-reports
        # findings already fixed — plausibly tripping the circular-repeat exit and gating the
        # operator on stale findings. Inlining made the restage implicit; a path makes it explicit
        # or it does not happen.
        spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.assertIn("Loop up to 3 passes, **restaging `plan.md` before each**", spine)
        self.assertIn("re-reads the prior file", spine.replace("re-reading", "re-reads"))


class ReviewLoopExitAndDeltaScopeTests(unittest.TestCase):
    """#88 (part). The S7 loop exited only on a pass with zero findings, so one SUGGESTION or NIT bought
    another full review pass, and every pass re-verified the whole plan because the reviewer had no
    changed-since-last-pass input (its "keep each pass focused on what changed" hint had nothing to read).
    Only a BLOCKER keeps the loop running now; SUGGESTIONs are applied on merit, NITs discarded."""

    def setUp(self):
        self.spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.reviewer = " ".join(
            (REFERENCES_DIR / "plan-reviewer-prompt.md").read_text(encoding="utf-8").split()
        )

    def test_only_a_blocker_keeps_the_loop_running(self):
        self.assertIn("**Only a BLOCKER keeps the loop running**", self.spine)
        self.assertIn("its merited SUGGESTIONs applied without another pass", self.spine)
        self.assertNotIn("on empty findings exit clean", self.spine)

    def test_suggestions_on_merit_nits_discarded(self):
        self.assertIn("each SUGGESTION on its merit", self.spine)
        self.assertIn("never a NIT", self.spine)

    def test_circular_and_cap_exits_count_blockers(self):
        self.assertIn("On a BLOCKER repeated with no progress (circular) or BLOCKERs left at the cap", self.spine)

    def test_spine_passes_the_changed_summary(self):
        self.assertIn("on passes 2+ `changed_summary`", self.spine)

    def test_reviewer_prompt_is_delta_scoped_with_a_whole_plan_carve_out(self):
        # The drafter's issue reviewer is the precedent; a whole-plan property (coverage, sequencing,
        # section ownership, the size cap) can break from an edit elsewhere, so it re-runs.
        self.assertIn("<<changed_summary>>", self.reviewer)
        self.assertIn("never re-verify an unchanged claim you already confirmed", self.reviewer)
        self.assertIn("a whole-plan check re-runs when a listed change touches its inputs", self.reviewer)


class ReviseRetentionAndContractCompressionTests(unittest.TestCase):
    """#38 Phase 2. The two contributors to unbounded growth: a revise that annotates forward instead of
    superseding (8% of the measured 266 KB — small per run, since each revise adds only 0.9-2.1 KB, and
    only legible in aggregate), and an epic's `## Story contracts` that never sheds a merged story."""

    def setUp(self):
        self.revise = (PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8")
        self.schema = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        self.log = (SHARED_DIR / "epic-delivery-log.md").read_text(encoding="utf-8")

    def test_retention_is_stated_as_a_prohibition_with_named_artifacts(self):
        flat = " ".join(self.revise.split())
        self.assertIn("carries **no** history layer", flat)
        self.assertIn("Prohibited in the", flat)
        for artifact in ("`## Approach` paragraph", "chained footer note", "superseded-text block"):
            self.assertIn(artifact, flat, "the prohibition must name %s" % artifact)

    def test_prohibition_names_where_prior_text_may_live(self):
        flat = " ".join(self.revise.split())
        self.assertIn("comment's edit history", flat)
        self.assertIn("`## Predecessor`", flat)

    def test_prohibition_carries_its_why(self):
        # The rationale is what stops a later editor softening this back to "refresh against today's
        # reality", which reads perfectly compatibly with annotate-forward.
        self.assertIn("no single revise looks wrong", " ".join(self.revise.split()))

    def test_promotion_check_is_mechanical_and_ordered_before_the_drop(self):
        flat = " ".join(self.revise.split())
        self.assertIn("**Promote before you drop**", flat)
        self.assertIn("mechanical, not optional", flat)
        self.assertIn("**then** drop", flat)
        for home in ("`## Architecture decisions`", "`## Changes`", "`## Risks & watchpoints`"):
            self.assertIn(home, flat)

    def test_epic_revise_is_the_compression_execution_site(self):
        # A plan is immutable; `## Story contracts` only ever changes on a revise, which routes here
        # rather than to epic.md — so the rule needs a site that runs at the right moment.
        self.assertIn("compress merged stories' contracts", " ".join(self.revise.split()))

    def test_pointer_is_a_third_clause_not_a_replacement(self):
        # Replacing `consumes` would strip edges from Dimension 5's sequencing graph and produce false
        # dangling-dependency BLOCKERs on every epic revise after the first merge.
        epic_fence = self.schema.split("## Epic-plan and story-under-epic sections")[1]
        contracts = epic_fence.split("## Story contracts")[1].split("## Integration strategy")[0]
        self.assertIn("— delivers:", contracts)
        self.assertIn("— consumes:", contracts)
        self.assertIn("— shipped:", contracts)
        flat = " ".join(self.schema.split())
        self.assertIn("The pointer is additive, never a replacement", flat)

    def test_compressed_entry_carries_nothing_beyond_the_three_clauses(self):
        flat = " ".join(self.schema.split())
        self.assertIn("it carries nothing else", flat)
        self.assertIn("Section ownership and size", flat)

    def test_delivers_is_never_repinned_to_the_shipped_shape(self):
        # The re-pinning hazard: the compressing session has the log in hand, so making the entry agree
        # with it is the locally-reasonable move — and it permanently disables the staleness detector.
        schema_flat = " ".join(self.schema.split())
        log_flat = " ".join(self.log.split())
        self.assertIn("never re-pinned to what shipped", schema_flat)
        self.assertIn("verbatim as originally pinned", log_flat)
        self.assertIn("comparing the log against a copy of itself", log_flat)

    def test_seam_attribution_survives_compression(self):
        self.assertIn("[user decision <date>]", self.schema)

    def test_story_jit_staleness_check_is_untouched(self):
        # Shape-preserving compression needs no reader change. Pinned so a later editor does not
        # "helpfully" sync this to the log and remove the left-hand side of the comparison.
        story_jit = (PLAYBOOKS_DIR / "story-jit.md").read_text(encoding="utf-8")
        flat = " ".join(story_jit.split())
        self.assertIn("`## Story contracts` pinned shapes", flat)
        self.assertIn("stop and re-route to the planner on the epic in revise mode", flat)


class InPlacePlanUpdateTests(unittest.TestCase):
    """#38 Phase 4. A revise updates the plan comment in place instead of delete-and-repost, so
    GitHub's own edit history becomes the supersession record — removing the reason a session
    invents an inline one. Ordering matters: the retention prohibition (Phase 2) must already be in
    force, because a comment you update in place reads far more like an invitation to append."""

    def setUp(self):
        self.revise = (PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8")
        self.story_jit = (PLAYBOOKS_DIR / "story-jit.md").read_text(encoding="utf-8")
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_revise_names_edit_comment_with_the_marker_id(self):
        flat = " ".join(self.revise.split())
        self.assertIn("gh_persist.py edit-comment <owner/repo> <facts.plan.comment_id>", flat)

    def test_the_staged_body_is_a_full_replacement_never_a_delta(self):
        # The guard against the op's own failure mode — see the class docstring.
        for name, text in (("revise", self.revise), ("story-jit", self.story_jit)):
            with self.subTest(playbook=name):
                flat = " ".join(text.split())
                self.assertIn("full replacement authored against the schema", flat)
                self.assertIn("never a delta", flat)

    def test_no_revise_path_still_names_delete_and_repost_as_its_default(self):
        # PR #39 review finding 3. The promoted-epic re-plan reaches an issue that already carries a
        # marker comment with a known id, so it takes the same in-place op — leaving it pointing at
        # `--delete-marker-id` made two instructions in one file disagree about the same path. The
        # only surviving mention is the ambiguous-marker carve-out asserted below.
        mentions = [
            line for line in (PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8").splitlines()
            if "--delete-marker-id" in line
        ]
        self.assertEqual(len(mentions), 1, mentions)
        self.assertIn("Ambiguous marker", mentions[0])

    def test_delete_and_repost_survives_for_the_ambiguous_marker(self):
        # Only delete-and-repost collapses a duplicate, and edit-comment has no id when no marker
        # exists — so the op is not a superset and the fresh path must stay.
        flat = " ".join(self.revise.split())
        self.assertIn("Ambiguous marker → `comment --delete-marker-id`", flat)
        self.assertIn("the only op that collapses a duplicate", flat)

    def test_spine_defers_the_op_choice_to_the_routed_playbook(self):
        # S8 is the knife-edge file; teaching it the op choice would have cost its scarce lines
        # twice. It states the fresh op and points at revise.md for the update.
        self.assertIn("fresh only — revise.md / story-jit.md name the in-place update op", self.spine)

    def test_story_jit_names_its_own_op_rather_than_inheriting(self):
        # story-jit owns the revise path for a story under an open epic and never mentions
        # --delete-marker-id, so without this it would silently inherit whatever S8 defaults to.
        flat = " ".join(self.story_jit.split())
        self.assertIn("gh_persist.py edit-comment", flat)
        self.assertIn("names the op rather than inheriting a default", flat)

    def test_handoff_states_the_url_is_unchanged(self):
        flat = " ".join(self.renderings.split())
        self.assertIn("carries the plan comment's **unchanged** URL", flat)
        self.assertNotIn("the stale one was deleted", flat)

    def test_revise_worked_example_reuses_the_fresh_comment_id(self):
        # The example is the rule's own demonstration: a stable URL means the revise handoff shows
        # the SAME comment id the fresh handoff published, not a new one.
        self.assertNotIn("issuecomment-YYYYY", self.renderings)


class FooterRuleTests(unittest.TestCase):
    """S13: the footer rendering rule (origin/main-prefixed default branch, bare epic/PR branch
    otherwise, @ the grounding workspace short-SHA) is stated as rendering logic."""

    def setUp(self):
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")

    def test_footer_rule_states_origin_main_vs_bare_branch(self):
        self.assertIn("origin/main", self.renderings)
        self.assertIn("epic/<N>-<slug>", self.renderings)
        self.assertRegex(
            self.renderings,
            r"[Nn]ever\s+emit\s+a\s+bare\s+`?main@",
            "the footer rule must forbid a bare main@<sha> form",
        )

    def test_short_sha_bound_to_grounding_workspace(self):
        # v3: the footer SHA binds to the ambient grounding fact (the asserted checkout's HEAD),
        # not the retired read_workspaces.grounding ro-* view.
        self.assertIn("facts.grounding.sha", self.renderings + self.spine)
        self.assertNotIn("facts.read_workspaces.grounding", self.renderings + self.spine)

    def test_spine_states_the_footer_rendering_rule(self):
        self.assertIn("origin/main", self.spine)
        self.assertIn("never a bare `main@<sha>`", self.spine)


class OpenQuestionLineCoverageTests(unittest.TestCase):
    """S13 DoD box 3 (offline half): the `**Open questions:**` line renders in every handoff shape that
    has OQs, and the bug-(b) composite epic+story worked example is present with the line."""

    def setUp(self):
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_open_questions_line_present_as_always_checked_condition(self):
        self.assertIn("**Open questions:**", self.renderings)
        # The rule must be expressed as an always-checked condition, not example-dependent.
        self.assertRegex(
            self.renderings,
            r"whenever any plan body posted this session carries an `## Open questions`",
            "the OQ-line rule must be stated as an always-checked condition (bug (b) residual)",
        )

    def test_planner_treatment_vocabulary_only(self):
        # The planner uses its plan-level treatment set, never the drafter's build-issue set.
        for token in ("planned-around", "recorded-blocked", "provisional-default"):
            self.assertIn(token, self.renderings, "planner OQ vocabulary %r missing" % token)

    def test_bug_b_composite_epic_plus_story_example_present_with_oq_line(self):
        # The composite epic+story hybrid shape (the 2026-07-01 drop) must have a worked example whose
        # emitted handoff carries the **Open questions:** line.
        self.assertRegex(
            self.renderings,
            r"[Cc]omposite.*epic.*story|epic.*plus.*story|[Ee]pic-plus-story",
            "the bug-(b) composite epic+story worked example must be present",
        )
        # Locate the composite worked-example section (its bold heading, not the intro mention) and
        # assert its emitted ## Handoff carries a (not filed) provisional-default OQ line.
        idx = self.renderings.find("**Epic-plus-story composite")
        self.assertNotEqual(idx, -1, "the composite worked example must have its own heading")
        window = self.renderings[idx: idx + 2500]
        self.assertIn("**Open questions:** (not filed) (audience:developer) provisional-default", window,
                      "the composite example's handoff must carry the (not filed) OQ line (bug (b))")

    def test_every_handoff_block_that_gates_oqs_shows_the_line(self):
        # Structural: every fenced ## Handoff block that mentions an OQ disposition token must also carry
        # a **Open questions:** line (no shape silently drops it).
        blocks = re.findall(r"```\n(## Handoff.*?)```", self.renderings, re.DOTALL)
        self.assertTrue(blocks, "no ## Handoff blocks parsed from renderings")
        treatments = ("planned-around", "recorded-blocked", "provisional-default")
        gated = [b for b in blocks if any(t in b for t in treatments)]
        self.assertTrue(gated, "expected at least one handoff block exercising an OQ treatment")
        for b in gated:
            self.assertIn(
                "**Open questions:**", b,
                "a handoff block names an OQ treatment but drops the **Open questions:** line:\n%s" % b,
            )


class BugARuleTests(unittest.TestCase):
    """S13 DoD box 4 (offline half): the falsifiable "(not filed)" rule is written as an instruction and
    pinned by a grep."""

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")

    def test_not_filed_rule_is_falsifiable_and_names_both_search_paths(self):
        self.assertIn("Falsifiable", self.spine)
        # Only-when: candidate set empty OR each candidate explicitly rejected.
        self.assertRegex(
            self.spine,
            r"`?question: \(not filed\)`?.*only.*when",
            "the (not filed) rule must be gated (only when …)",
        )
        # Body-recorded OQ → prep's open_question_candidates.
        self.assertIn("facts.open_question_candidates", self.spine)
        # Newly-detected OQ → the --oq-query one-shot lookup (mechanism ii).
        self.assertIn("--oq-query", self.spine)

    def test_prep_oq_query_flag_exists(self):
        # The authorized additive prep extension backing the newly-detected-OQ search.
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "prep_planner.py"), "--help"],
            capture_output=True, encoding="utf-8", check=False,
        )
        self.assertIn("--oq-query", proc.stdout + proc.stderr)


class CitationCompletenessRuleTests(unittest.TestCase):
    """Scenario-3 D8 operator adjudication (docs/specs/parity/planner.md): v2 must never silently
    resolve a genuine design decision on a partial citation. Falsifiable rule, same register as
    bug (a): a citation pins a choice only when it covers the WHOLE choice; genuinely-silent or
    partial-only grounding routes to the S4 Decision gate or an explicit provisional pin naming the
    rejected alternative(s) — binding to the EXISTING `## Architecture decisions` citation/"name the
    rejected one" grammar (plan-schema.md), never a new schema section."""

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")

    def test_rule_is_falsifiable_and_fence_scoped(self):
        self.assertRegex(self.spine, r"Falsifiable\s+citation-completeness rule")
        # Prose, not a code sample: the rule text must sit outside every ``` fence.
        for i, line, in_fence in _fence_stripped_lines(PLAYBOOKS_DIR / SPINE):
            if in_fence:
                self.assertNotIn(
                    "citation-completeness", line,
                    "the citation-completeness rule must be prose, not inside a code fence (%d)" % i,
                )

    def test_rule_names_the_whole_choice_condition(self):
        self.assertRegex(
            self.spine, r"covers the WHOLE\s+choice",
            "the rule must require the citation to cover the WHOLE choice, not one facet",
        )
        self.assertIn("facet", self.spine)

    def test_rule_names_the_gate_or_provisional_pin_alternatives(self):
        self.assertRegex(
            self.spine, r"Decision gate .*or a provisional pin",
            "silent/partial grounding must route to the Decision gate OR a provisional pin",
        )

    def test_rule_binds_to_the_existing_architecture_decisions_grammar_not_a_new_section(self):
        self.assertRegex(
            self.spine, r"`## Architecture decisions`\s+rationale names the rejected alternative"
        )
        self.assertIn("no new schema field", self.spine)
        # The "name the rejected one" convention this rule cites must actually exist in S5, so the
        # binding is to real prose, not an invented cross-reference.
        self.assertIn("name the rejected one", self.spine)

    def test_rule_is_checkable_post_hoc(self):
        self.assertRegex(
            self.spine, r"partial citation.*no named alternative.*(is a )?defect",
            "a pinned choice with a partial citation and no named alternative must be a named defect",
        )


class PhasesSelfValidationTests(unittest.TestCase):
    """#45: the planner proves its own `## Phases` parses BEFORE it posts.

    A malformed section (`Phase 5c`, prose inside a `closes-dod:` value, an ordinal disagreeing with
    its label) used to reach GitHub and surface two stages later as prep_resolver's PHASES_MALFORMED,
    on a plan comment nobody may hand-edit.

    Why the spine is the only place this can live:
      * the plan reviewer's Dimension 7 is semantic — `closes-dod: (none — prose)` reads as `(none)`
        and `depends-on: 5c` is backward-and-acyclic, so a conforming reviewer returns clean findings;
      * prep_planner is DELIBERATELY lenient (tests/test_phase_grammar_contract.py: `status: ok`,
        `prior_phases_parsed: false`, an attention line, no decision) because a revise run exists to
        repair a bad plan. Making prep strict would remove the repair path.
    So this authoring-time check is the pipeline's only gate — do not "simplify" it into prep.
    """

    def setUp(self):
        self.spine_path = PLAYBOOKS_DIR / SPINE
        self.flat = " ".join(self.spine_path.read_text(encoding="utf-8").split())

    def test_spine_runs_the_resolvers_parser_on_the_staged_plan(self):
        # The same parser prep_resolver runs, against the same staged body the write op posts.
        self.assertIn(
            '${CLAUDE_PLUGIN_ROOT}/scripts/parse.py phases "<facts.scratch>/plan.md"', self.flat
        )

    def test_validation_precedes_the_reviewer_dispatch(self):
        # An unparseable section handed to the reviewer burns dimension budget (and up to all three
        # passes) on prose it cannot structurally assess.
        self.assertLess(
            self.flat.index("parse.py phases"), self.flat.index("plan-reviewer-prompt.md")
        )

    def test_check_runs_on_every_staging_including_s8(self):
        # One insertion at S7 is sufficient only because the rule binds to the ACT of staging and
        # names S8's restage — the body S8 posts is restaged after the review loop exits.
        self.assertIn("after **every** staging", self.flat)
        self.assertIn("S8's before the post", self.flat)

    def test_s8_restage_names_the_revalidation_at_its_own_point_of_use(self):
        # S7's enumeration alone is not enough: S8 restages the APPROVED body, which can differ from
        # what the loop last validated (the "Fix manually" gate arm edits the plan after review). A
        # reader executing S8 linearly must see the requirement there, not 20 lines earlier.
        self.assertIn(
            'Restage the approved body (marker line first) to `<facts.scratch>/plan.md`, '
            "re-validate it per S7",
            self.flat,
        )

    def test_gate_keys_on_status_not_exit_code(self):
        # `ok` and PHASES_MALFORMED both exit 0, so an exit-code gate is silently a no-op.
        self.assertIn("Both exit 0", self.flat)
        self.assertIn("read `status`, proceed only on `ok`", self.flat)

    def test_repair_names_the_context_fields(self):
        self.assertIn("context.line_number", self.flat)
        self.assertIn("`raw_line`", self.flat)
        self.assertIn("restage, re-validate", self.flat)

    def test_malformed_is_self_repair_not_an_operator_gate(self):
        # Guards against a later reader importing SKILL.md §1's decision-card rule by analogy and
        # turning the planner's own punctuation into an AskUserQuestion.
        self.assertIn("`PHASES_MALFORMED` is yours to fix, never a gate", self.flat)

    def test_absent_phases_is_ok_so_no_route_conditional(self):
        # parse.py returns ok/[] when the section is absent (epic, single-phase), which is what lets
        # the check be unconditional — no route branch to interleave into the spine.
        self.assertIn("`phases: []`", self.flat)
        self.assertIn("unconditional", self.flat)

    def test_rule_carries_its_why(self):
        # CLAUDE.md: a rationale clause is what stops a later editor reintroducing the bug.
        self.assertIn("semantic and structurally cannot catch a grammar break", self.flat)
        self.assertIn("runs this same parser", self.flat)

    def test_command_is_inline_not_fenced(self):
        # Inline is in-file house style (S8's edit-body/edit-labels) and a fence costs ~4 lines on a
        # file with no headroom. Locked so a later edit does not silently spend them.
        for _lineno, line, in_fence in _fence_stripped_lines(self.spine_path):
            if in_fence:
                self.assertNotIn("parse.py phases", line)


class PhaseNumberingRuleTests(unittest.TestCase):
    """#46: the `## Phases` label grammar stated as a RULE, not inferable from a worked template.

    Before this, the constraints lived only in `scripts/parse.py` and in the shape of the frozen
    template — so an author under insertion pressure invented `Phase 5c`, which matches the template
    everywhere except the one character that matters.
    """

    def setUp(self):
        self.schema = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        self.flat = " ".join(self.schema.split())
        self.section = self.schema.partition("## Phase numbering")[2].split(
            "## The `closes-dod` target"
        )[0]

    def test_grammar_states_integer_labels_and_ordinal_equals_label(self):
        self.assertIn("both `<N>` are the **same integer**", self.flat)
        self.assertIn("parsed as two separate captures", self.flat)

    def test_grammar_states_sequential_non_duplicate_and_its_consequence(self):
        self.assertIn("1..n, sequential and non-duplicate", self.flat)
        self.assertIn("no way to append an entry numbered 7 and have it sit between 5 and 6", self.flat)

    def test_values_are_none_or_bare_ints_with_no_prose(self):
        self.assertIn("the literal `(none)` or a comma-separated list of **bare ints**", self.flat)
        self.assertIn("no prose, no parenthetical, no qualifier", self.flat)
        self.assertIn("that reason belongs in `deliverable`", self.flat)

    def test_the_disallowed_shapes_are_named(self):
        # Named so the rule is recognisable at the moment it would be broken, not only in the abstract.
        for needle in ("Phase 5c", "`5a`/`5b`", "Phase 6.1", "depends-on: 5c"):
            self.assertIn(needle, self.flat, needle)

    def test_section_is_outside_the_frozen_first_fence(self):
        # Same reasoning as test_grammar_is_documented_outside_the_frozen_first_fence: the first fence
        # is byte-pinned to the S1 capture, so documenting the rule inside it would force an edit to
        # the frozen v1 record and hollow out that assertion.
        fence = _first_fenced_block(self.schema)
        self.assertIn("## Phase numbering", self.schema)
        self.assertNotIn("## Phase numbering", fence)
        self.assertNotIn("5c", fence)

    def test_placement_keeps_the_pinned_ownership_slice_intact(self):
        # SectionOwnershipAndSizeTests slices between these two headings; a section inserted there
        # would silently re-scope that slice for every future editor.
        _, _, ownership = self.schema.partition("## Section ownership and size")
        ownership = ownership.split("## The `sub-issue:` phase key")[0]
        self.assertNotIn("## Phase numbering", ownership)

    def test_it_cites_the_sub_issue_rule_rather_than_restating_it(self):
        # The numbering section cites the rule; it must not re-derive it, or the schema grows a
        # second authority on the same spelling.
        self.assertEqual(self.schema.count("one spelling per meaning"), 1)
        self.assertNotIn("one spelling per meaning", self.section)
        self.assertIn("the `sub-issue:` phase key above owns that rule", self.section)

    def test_it_points_at_the_operation_rather_than_defining_it(self):
        self.assertIn("revise-reconciliation.md", self.section)
        self.assertIn("Inserting a phase after work has shipped", self.section)
        self.assertNotIn("keeps its number", self.section)

    def test_the_frozen_fence_and_the_spine_pin_are_untouched(self):
        ours = _first_fenced_block(self.schema)
        s1 = _first_fenced_block(S1_PLAN_CAPTURE.read_text(encoding="utf-8"))
        self.assertEqual(ours, s1)
        self.assertEqual(
            len((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").splitlines()), 131
        )


class InsertAfterShippingTests(unittest.TestCase):
    """#46: the operation a session needs when work lands BETWEEN a shipped and an unshipped phase.

    `revise-reconciliation.md` owns it: it is an operation over a plan plus a `## Phase tracker`, i.e.
    over shipped-versus-unshipped state, which is exactly what that file already owns. The schema owns
    only what a legal `## Phases` IS.
    """

    RECON = REFERENCES_DIR / "revise-reconciliation.md"

    def setUp(self):
        self.recon = self.RECON.read_text(encoding="utf-8")
        self.flat = " ".join(self.recon.split())
        self.section = self.recon.partition("### Inserting a phase after work has shipped")[2].split(
            "### SOFT-path body reconciliation"
        )[0]

    def test_rule_is_stated_exactly_once_across_the_skill(self):
        # Whitespace-normalized: these files hard-wrap, so a line-sensitive search would miss a real
        # second copy that happens to wrap differently.
        needle = "a shipped phase keeps its number"
        hits = [
            path.name
            for path in _iter_md(SKILL_DIR)
            if needle in re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(hits, ["revise-reconciliation.md"], "stated once, in the reference: %r" % hits)

    def test_both_halves_of_the_renumber_are_named(self):
        self.assertIn("the unshipped tail renumbers", self.flat)
        self.assertIn("shifts up by one in its existing relative order", self.flat)
        self.assertIn("Nothing that has shipped moves", self.flat)

    def test_the_grammar_constraint_is_given_as_the_why(self):
        self.assertIn("numbers are integers, sequential and non-duplicate", self.flat)
        self.assertIn("a phase's number **is** its position", self.flat)

    def test_depends_on_is_named_as_backward_only(self):
        self.assertIn("backward-only", self.flat)
        self.assertIn('"phase 7, but run it before 6"', self.flat)

    def test_the_insert_is_stated_to_stay_soft_and_why(self):
        self.assertIn("no ticked phase's number changed, so no ticked bullet's attribution changed", self.flat)
        self.assertIn("New phases added beyond what's shipped", self.flat)

    def test_renumbering_the_shipped_prefix_is_named_as_the_hard_move_and_unnecessary(self):
        self.assertIn("would make the same edit HARD", self.flat)
        self.assertIn("the insert point is always at or after k+1", self.flat)

    def test_the_tracker_consequence_is_stated_with_its_owner(self):
        self.assertIn("separate artifact keyed by the same numbers", self.flat)
        self.assertIn("facts.tracker.diff", self.flat)

    def test_removed_and_renumbered_are_now_separate_cases(self):
        # The old flat entry read as the cost of ANY renumber, contradicting the Always-SOFT list
        # eleven lines above it.
        self.assertNotIn("Phase removed/renumbered", self.recon)
        self.assertIn("**Removal is what costs the tick, not renumbering.**", self.flat)

    def test_a_renumbered_shipped_phase_routes_to_reassignment_and_unticks_nothing(self):
        self.assertIn("re-attribute, leave ticked) and un-tick nothing", self.flat)
        self.assertIn("visible-progress regression the SOFT list exists to prevent", self.flat)

    def test_both_revise_paths_cite_the_reference(self):
        # story-jit owns its own revise path and cited this reference NOWHERE before #46.
        for name in ("revise.md", "story-jit.md"):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("revise-reconciliation.md", text, name)
            self.assertIn("shipped prefix keeps its numbers", " ".join(text.split()), name)

    def test_story_jit_keys_on_the_fact_not_the_route(self):
        # PlaybookInterleavingGrepTests covers the negative; this pins the positive shape.
        self.assertIn(
            "facts.plan.present", (PLAYBOOKS_DIR / "story-jit.md").read_text(encoding="utf-8")
        )

    def test_planner_budget_bar_still_holds(self):
        router_lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        playbooks = {
            path.name: len(path.read_text(encoding="utf-8").splitlines())
            for path in PLAYBOOKS_DIR.glob("*.md")
        }
        self.assertLessEqual(
            router_lines + max(playbooks.values()),
            V1_HALF_BAR,
            "#46 added lines to revise.md and story-jit.md: %r" % playbooks,
        )


class OperatorGateCoverageTests(unittest.TestCase):
    """S13 DoD box 5 (offline half): the spec's operator gates are present in the v2 skill."""

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.revise = (PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8")

    def test_deviation_and_decision_gates_present(self):
        self.assertIn('header: "Deviation"', self.spine)
        self.assertIn('header: "Decision"', self.spine)

    def test_review_notes_gate_present_with_dim4_carveout(self):
        self.assertIn('header: "Review notes"', self.spine)
        self.assertRegex(self.spine, r'dimension-4 BLOCKER.*"Post as-is" is not offered')

    def test_revise_reconciliation_confirm_soft_and_hard(self):
        self.assertIn("**Apply** / **Cancel**", self.revise)
        self.assertIn("HARD → **Start fresh** / **Apply in place anyway** / **Cancel**", self.revise)
        # The recommendation is the rule that decided HARD, never a fixed label (asking-the-user.md).
        self.assertNotIn("Start fresh (recommended)", self.revise)

    def test_revise_confirm_renders_the_change_list_before_the_card(self):
        # A live #1373 epic revise asked "classified SOFT — post it in place?" after one sentence: the
        # gate's old "diff-style plan update" was defined nowhere, and an epic has no DoD ticks to diff,
        # so nothing rendered. The `**What changed:**` list appeared only in the post-handoff summary.
        flat = " ".join(self.revise.split())
        self.assertNotIn("diff-style", flat)
        start = flat.index("**Show + confirm (spine S8 variant).**")
        bullet = flat[start:flat.index('- **HARD "Start fresh"', start)]
        self.assertLess(bullet.index("**What changed:**"), bullet.index("**Apply** / **Cancel**"))
        self.assertIn("**before** the card", bullet)
        self.assertIn("the block the Show + confirm gate already rendered", flat)
        ref = " ".join((REFERENCES_DIR / "revise-reconciliation.md").read_text(encoding="utf-8").split())
        section = ref[ref.index("### What the confirm shows"):ref.index("### HARD-path")]
        self.assertIn("the rule that decided it", section)
        self.assertIn("staged at `facts.plan.body_path`", section)
        self.assertIn("the final verified `plan.md`", section)
        # A HARD judgment call on reworded shipped entries needs the text, not the digest.
        self.assertIn("quote that entry old → new", section)
        # The DoD edits differ by HARD option, and the option is not chosen until the card.
        self.assertIn("HARD lists them per option", section)
        # An epic revise always has an empty DoD diff; silence there reads as a hidden block.
        self.assertIn("`No DoD ticks to reconcile.`", section)
        self.assertIn("compute it once, here", section)
        # The options live here too, so story-jit (which may not read a second playbook) has a card.
        self.assertIn("HARD → **Start fresh** / **Apply in place anyway** / **Cancel**", section)
        # A HARD reached only by the "lean HARD" judgment is a close call with a real alternative.
        self.assertIn("an \"Always HARD\" rule makes **Start fresh** the clear call", section)
        self.assertIn("A HARD from the judgment call's lean is a **close call**", section)
        self.assertIn("The three blocks are the card's brief", section)
        jit = " ".join((PLAYBOOKS_DIR / "story-jit.md").read_text(encoding="utf-8").split())
        self.assertIn('the S8 confirm that gates the post ("What the confirm shows"', jit)
        # The spine is read first and end to end: its auto-post line must defer to the revise gate.
        spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.assertIn("**A revise is the exception:**", spine)
        self.assertNotIn("diff-show", spine)
        # Coverage and the cap live in the shared contract, not in a planner restatement.
        shared = " ".join((SHARED_DIR / "plan-summary.md").read_text(encoding="utf-8").split())
        self.assertIn("At most 6 bullets — changes of one kind share a bullet", shared)


class HardReviseSequenceTests(unittest.TestCase):
    """S13 scenario-4 D4 fix: on HARD "Start fresh", grounding must follow the close decision, never
    precede it — a HARD-revised plan posted while still grounded at the open-PR-head row (selected
    before the close) would cite a branch nothing can read once it's gone. Structural (line-order)
    asserts over skills/planner/playbooks/revise.md + its reconciliation reference, pinning: close-pr
    precedes the re-prep call precedes the repost; the re-selected-row language is present; the old
    (closed) PR-head ref is explicitly ruled out as the HARD footer's ground."""

    def setUp(self):
        self.revise_path = PLAYBOOKS_DIR / "revise.md"
        self.revise = self.revise_path.read_text(encoding="utf-8")
        self.reconciliation_path = REFERENCES_DIR / "revise-reconciliation.md"
        self.reconciliation = self.reconciliation_path.read_text(encoding="utf-8")

    def _first_fenced_line_containing(self, text, needle):
        """Return the 1-based line number of the first line inside a ``` fence containing `needle`,
        or None."""
        in_fence = False
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence and needle in line:
                return i
        return None

    def _first_line_containing(self, text, needle):
        for i, line in enumerate(text.splitlines(), 1):
            if needle in line:
                return i
        return None

    def test_close_pr_precedes_the_stop_handoff_precedes_the_fresh_run_post(self):
        # v3 sequence: close-pr -> STOP (this session's ambient checkout is the superseded PR's
        # worktree; a re-prep would refuse with WORKSPACE_MISMATCH; hand off with the target
        # checkout on a Workspace: line) -> the FRESH planner run (in the right checkout) grounds
        # and posts. The v2 in-session re-prep + repost is retired by design (review B1).
        post_needle_by_label = {
            "revise.md": "The fresh planner run",
            "revise-reconciliation.md": "The fresh planner run",
        }
        for text, label, path in (
            (self.revise, "revise.md", self.revise_path),
            (self.reconciliation, "revise-reconciliation.md", self.reconciliation_path),
        ):
            close_line = self._first_fenced_line_containing(text, "close-pr")
            stop_line = self._first_line_containing(text, "WORKSPACE_MISMATCH")
            post_line = self._first_line_containing(text, post_needle_by_label[label])
            self.assertIsNotNone(close_line, "%s: no fenced close-pr command found" % label)
            self.assertIsNotNone(stop_line, "%s: no stop-with-WORKSPACE_MISMATCH rationale" % label)
            self.assertIsNotNone(post_line, "%s: no fresh-run post step found" % label)
            self.assertLess(
                close_line, stop_line,
                "%s:%d close-pr must precede the stop/handoff at :%d" % (label, close_line, stop_line),
            )
            self.assertLess(
                stop_line, post_line,
                "%s:%d the stop/handoff must precede the fresh-run post at :%d"
                % (label, stop_line, post_line),
            )

    def test_soft_persist_runs_immediately_hard_persist_is_deferred(self):
        self.assertIn("SOFT-Apply", self.revise)
        self.assertRegex(self.revise, r"SOFT-Apply.*runs the spine's persist immediately")
        self.assertRegex(
            self.revise, r"HARD-Start-fresh does NOT run the spine's persist"
        )

    def test_hard_revise_hands_off_with_a_workspace_line_never_re_grounds_in_session(self):
        # v3 replacement for the retired D6-citation test (no second in-session prep exists to be
        # made safe): both files must carry the cannot-re-ground rule and the Workspace: carrier.
        for text, label in (
            (self.revise, "revise.md"),
            (self.reconciliation, "revise-reconciliation.md"),
        ):
            self.assertIn("Workspace:", text, "%s must carry the handoff Workspace: line" % label)
            self.assertIn("WORKSPACE_MISMATCH", text, "%s must state why re-grounding here refuses" % label)
        self.assertRegex(self.revise, r"cannot re-ground itself")

    def test_plan_ref_re_selection_is_row_table_driven_never_hardcoded(self):
        for text, label in (
            (self.revise, "revise.md"),
            (self.reconciliation, "revise-reconciliation.md"),
        ):
            self.assertRegex(
                text, r"re-select(s|ed)?\s*\*{0,2}deterministically",
                "%s must state plan_ref re-selects off the row table" % label,
            )
            self.assertIn(
                "never hardcode", text.lower().replace("hardcoded", "hardcode"),
                "%s must explicitly rule out hardcoding main" % label,
            )

    def test_hard_footer_explicitly_rules_out_the_closed_prs_head(self):
        # The exact regression this fix closes: the HARD-revised plan's footer/Grounding must never
        # cite the (about-to-be/now-)closed PR's branch.
        self.assertRegex(
            self.revise,
            r"grounded on a branch this decision is closing|posting it as-is would leave the footer",
        )
        renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        self.assertRegex(
            renderings, r"never\s+the closed branch",
            "handoff-renderings.md must explicitly rule out the closed PR's branch as the HARD ground",
        )

    def test_predecessor_facts_captured_before_reprep_replaces_facts(self):
        # Step 1's "capture before re-prep" ordering (## Predecessor / DoD un-tick need the OLD
        # envelope's facts.plan / facts.revise, which a second prep_planner.py call replaces).
        self.assertRegex(self.revise, r"[Cc]apture what `## Predecessor`")
        self.assertRegex(self.revise, r"before\s+step 2 replaces `facts`")
        self.assertIn("facts.revise.open_pr", self.revise)
        self.assertIn("facts.plan", self.revise)


class EpicPlanGrowthBoundTests(unittest.TestCase):
    """#269's epic plan measured 64,816 characters — 98.9% of `BODY_CHAR_LIMIT` — with the section-set
    of a plan that had never been through the #38 rules. Two growth drivers the section-ownership rule
    does not reach, because neither is a fact restated in a second SECTION: delivered status restated
    from the delivery log into the plan (40 marks across 7 sections, three words each, so invisible to a
    section-by-section read), and story-altitude material carried at epic altitude. These pin the rules
    that bound both, and the retirement rule that makes the bound apply per merge rather than once."""

    def setUp(self):
        self.schema = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        self.reviewer = (REFERENCES_DIR / "plan-reviewer-prompt.md").read_text(encoding="utf-8")
        self.epic = (PLAYBOOKS_DIR / "epic.md").read_text(encoding="utf-8")
        self.revise = (PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8")

    def test_delivery_status_has_one_owner_and_it_is_not_the_plan(self):
        flat = " ".join(self.schema.split())
        self.assertIn("Delivery status is not plan content", flat)
        self.assertIn("epic delivery log", flat)
        self.assertIn("`shipped:` clause", flat)

    def test_delivery_status_rule_carries_why_the_section_read_cannot_catch_it(self):
        # Without this, a later editor folds the rule into `Section ownership` — where it is dead,
        # because the restatement is across ARTEFACTS and each instance is three words.
        flat = " ".join(self.schema.split())
        self.assertIn("across artefacts rather than across sections", flat)
        self.assertIn("each mark is three words", flat.lower())

    def test_altitude_rule_names_the_three_leaking_sections_and_their_owner(self):
        flat = " ".join(self.schema.split())
        self.assertIn("an epic plan is not the union of its stories' plans", flat.lower())
        for section in ("`## UI decisions`", "`## Changes (file-level)`", "`## Test plan`"):
            self.assertIn(section, flat, "the altitude rule must name %s" % section)
        self.assertIn("just-in-time story plan", flat)

    def test_altitude_rule_states_what_survives_at_the_epic_grain(self):
        # A bare prohibition would empty the sections; the point is the cross-story residue stays.
        flat = " ".join(self.schema.split())
        self.assertIn("what no single story owns", flat)

    def test_retirement_rule_is_per_merged_story_and_mechanical(self):
        flat = " ".join(self.schema.split())
        self.assertIn("a merged story shrinks the plan it came from", flat.lower())
        self.assertIn("per merged story", flat)
        self.assertIn("mechanically", flat)

    def test_retirement_rule_covers_deviations_and_doc_grounding(self):
        # The two sections that accumulate silently: 11.2% and 10.6% of #269's body, almost all of it
        # settled history for stories that had already merged.
        flat = " ".join(self.schema.split())
        self.assertIn("`## Deviations from project docs` entry whose scope has merged", flat)
        self.assertIn("`## Doc grounding` citation no unmerged story reads", flat)

    def test_retirement_preserves_the_pinned_contract_clauses(self):
        # The re-pinning hazard again: retirement must not become "make the entry agree with the log".
        flat = " ".join(self.schema.split())
        self.assertIn("keeps `delivers` / `consumes` verbatim as pinned and gains `shipped:`", flat)

    def test_retirement_runs_after_the_promotion_check_not_instead_of_it(self):
        flat = " ".join(self.schema.split())
        self.assertIn("after the promotion check", flat)
        self.assertIn("promote the decision and drop the status", flat)

    def test_epic_playbook_carries_both_deltas(self):
        flat = " ".join(self.epic.split())
        self.assertIn("**Altitude.**", flat)
        self.assertIn("**No delivery status.**", flat)
        self.assertIn("Delivery status is not plan content", flat)

    def test_epic_playbook_altitude_reuses_the_no_fanout_rationale(self):
        # The altitude rule and the no-fan-out rule are the same argument; stating it once here is what
        # stops a later editor reading them as two unrelated preferences.
        flat = " ".join(self.epic.split())
        self.assertIn("stale by the second story", flat)

    def test_revise_delegates_retirement_to_the_schema_rather_than_restating_it(self):
        flat = " ".join(self.revise.split())
        self.assertIn('"Retirement" rule', flat)
        self.assertIn("per merged story", flat)

    def test_revise_prohibition_names_the_delivered_marker(self):
        flat = " ".join(self.revise.split())
        self.assertIn("per-story delivered marker outside `## Story contracts`", flat)

    def test_reviewer_runs_both_audits_on_an_epic_plan(self):
        flat = " ".join(self.reviewer.split())
        self.assertIn("**Delivery status** *(epic-level plan only", flat)
        self.assertIn("**Altitude** *(epic-level plan only", flat)

    def test_both_audits_self_identify_the_way_dimension_5_does(self):
        # The sub-agent is context-blind and its inputs never state the plan's TYPE, so "epic plans
        # only" without an anchoring section is a predicate the reviewer has to guess at.
        flat = " ".join(self.reviewer.split())
        self.assertIn("fires on a `## Story breakdown` section", flat)
        self.assertIn("against the plan's own `## Story breakdown`", flat)

    def test_delivery_status_audit_is_attributive_not_a_closed_literal_grep(self):
        # A three-literal grep reports zero on a plan that says "shipped in #211" throughout, and the
        # instruction makes the COUNT the headline — so a false-negative count is worse than no audit.
        flat = " ".join(self.reviewer.split())
        self.assertIn("past-tense delivery claim attributed to a story", flat)
        self.assertIn("Those are examples, not the detector", flat)
        self.assertIn("worse than no audit", flat)
        self.assertIn("report the total count", flat)

    def test_delivery_status_audit_excludes_the_shim_watchpoint(self):
        flat = " ".join(self.reviewer.split())
        self.assertIn("trap for the resolver, not a status report", flat)

    def test_reviewer_altitude_audit_refuses_to_flag_the_unattributable(self):
        # Without the carve-out the reviewer empties the sections it is auditing.
        flat = " ".join(self.reviewer.split())
        self.assertIn("Do not flag an entry you cannot attribute to a single story", flat)

    def test_reviewer_altitude_audit_accepts_the_empty_form(self):
        flat = " ".join(self.reviewer.split())
        self.assertIn("do not flag a section rendered `- (none — story-owned)`", flat)

    def test_goal_coherence_carve_out_runs_in_both_directions(self):
        # One-directional it still BLOCKERs every epic criterion with no epic-level test coverage and
        # SUGGESTIONs every shared surface as scope creep — on essentially every epic plan.
        flat = " ".join(self.reviewer.split())
        self.assertIn("**On an epic this whole mapping moves down a level**, in both directions", flat)
        self.assertIn("no `## Changes` line and no `## Test plan` entry is **correct**", flat)
        self.assertIn("is **not** scope creep", flat)

    def test_compaction_never_removes_an_anchor_another_dimension_verifies(self):
        # Retirement without this reintroduces, in dimensions 1 and 6, the false-BLOCKER-on-every-
        # revise-after-the-first-merge class the `shipped:` pointer's additive rule guards in 5.
        flat = " ".join(self.schema.split())
        self.assertIn("Compaction is narration-only", flat)
        self.assertIn("`agreed with user <date>`", flat)
        self.assertIn("undisclosed deviation", flat)
        self.assertIn("only when **no surviving entry cites it**", flat)
        self.assertIn("fabricated", flat)

    def test_altitude_covers_every_section_not_three(self):
        # higeia#1351: the three sections the rule named were already lean; the bloat was in the ones
        # it did not reach — Architecture decisions alone carried 5.3k of one story's mechanism.
        flat = " ".join(self.schema.split())
        self.assertIn("So **every** section of an epic plan carries only what no single story owns", flat)
        for section in ("`## Architecture decisions`", "`## Risks & watchpoints`", "`## Doc grounding`",
                        "`## Deviations from project docs`", "`## Data model / schema impact`",
                        "`## Open questions`"):
            self.assertIn(section, flat.split("**Altitude: an epic plan")[1].split("**Where each section")[0])
        self.assertIn("**which story lands what**", flat)

    def test_epic_open_questions_cite_rather_than_copy(self):
        flat = " ".join(self.schema.split())
        self.assertIn("citing the issue body's `<!-- open-question-links:v1 -->` entry rather than copying its text", flat)

    def test_delivers_is_the_consumed_seam_not_the_scope(self):
        flat = " ".join(self.schema.split())
        self.assertIn("**`delivers` is the seam, not the story's scope.**", flat)
        self.assertIn("pin only the shape that consumer relies on", flat)
        epic_fence = self.schema.split("## Epic-plan and story-under-epic sections")[1]
        self.assertIn("only the shape that consumer relies on, or (none consumed)", epic_fence)

    def test_retirement_promotes_only_what_an_unmerged_story_builds_against(self):
        flat = " ".join(self.schema.split())
        self.assertIn("Promote only a decision an **unmerged** story still builds against", flat)

    def test_epic_playbook_overrides_the_spine_detail_bar(self):
        # S5's "pin every signature… name the rejected one" ran on epics with no override, while the
        # epic reviewer never runs Dimension 4 — the one dimension that detail serves.
        flat = " ".join(self.epic.split())
        self.assertIn("**Lock ownership, not shapes (spine S5).**", flat)
        self.assertIn("the epic reviewer runs no Dimension 4", flat)
        self.assertIn("never adds one story's mechanism to the epic plan", flat)

    def test_every_epic_drafting_route_reaches_the_detail_bar(self):
        # An epic revise routes to revise.md, never epic.md, and story-jit bootstraps an epic plan
        # inline — so the bar lives in the spine, which every route runs (#78 review).
        spine = " ".join((PLAYBOOKS_DIR / "plan-spine.md").read_text(encoding="utf-8").split())
        s5 = spine.split("## S5")[1].split("## S6")[0]
        self.assertIn("An **epic** plan locks ownership, not shapes, on every route that drafts or revises one", s5)
        for playbook in ("revise.md", "story-jit.md", "epic.md"):
            text = (PLAYBOOKS_DIR / playbook).read_text(encoding="utf-8")
            self.assertIn("plan-spine.md", text, playbook)

    def test_epic_decision_gate_defers_single_story_choices(self):
        spine = " ".join((PLAYBOOKS_DIR / "plan-spine.md").read_text(encoding="utf-8").split())
        self.assertIn("On an epic plan only a choice binding two or more stories gates here", spine)

    def test_operator_answers_are_never_dropped_by_altitude(self):
        flat = " ".join(self.schema.split())
        self.assertIn("the operator is never asked the same question twice", flat)
        reviewer = " ".join(self.reviewer.split())
        self.assertIn("Nor flag a `[user decision <date>]` entry labelled with the story it binds", reviewer)
        story_jit = " ".join((PLAYBOOKS_DIR / "story-jit.md").read_text(encoding="utf-8").split())
        self.assertIn("**Carry the operator's answers down.**", story_jit)

    def test_altitude_audit_never_trims_a_merged_contract(self):
        # Trimming a merged story's `delivers` re-pins the contract the staleness check compares with.
        reviewer = " ".join(self.reviewer.split())
        self.assertIn("**never on a merged story's entry** (one carrying `shipped:`)", reviewer)

    def test_empty_form_is_not_an_uncited_decision(self):
        reviewer = " ".join(self.reviewer.split())
        dim6 = reviewer.split("6. **Precedent grounding.**")[1].split("7. **Phase coherence**")[0]
        self.assertIn("`- (none — story-owned)` is the Altitude empty form", dim6)

    def test_epic_contract_delivers_handles_none_consumed(self):
        flat = " ".join(self.schema.split())
        self.assertIn("or (none consumed) when it assigns none", flat)
        self.assertIn("Its plan's `## Epic contract` says `Delivers: (none consumed)` too", flat)
        evaluator = " ".join((REPO_ROOT / "skills" / "evaluator" / "playbooks" / "story.md").read_text(encoding="utf-8").split())
        self.assertIn("`Delivers: (none consumed)` means no later story relies on it", evaluator)

    def test_compaction_rationale_matches_retirement(self):
        flat = " ".join(self.schema.split())
        self.assertNotIn("a merged story's decision survives in `## Architecture decisions`", flat)
        self.assertIn("a decision an unmerged story still builds against survives", flat)

    def test_reviewer_altitude_reads_every_section_and_counts(self):
        flat = " ".join(self.reviewer.split())
        self.assertIn("reads every section against the plan's own `## Story breakdown`", flat)
        self.assertIn("a `## Story contracts` `delivers` item no `consumes` clause names", flat)
        self.assertIn("only the aggregate shows the plan has become its stories' plans", flat)

    def test_reviewer_epic_carve_outs_in_dimensions_one_and_six(self):
        flat = " ".join(self.reviewer.split())
        self.assertIn("a section governing only one story's work is that story's plan's to cite", flat)
        self.assertIn("an ownership assignment (\"#N lands X\") is grounded by its `## Story contracts` entry", flat)

    def test_altitude_states_the_empty_rendering_for_the_non_omittable_sections(self):
        # `## Changes (file-level)` and `## Test plan` carry no "(omit if ...)" marker and the fence is
        # byte-pinned, so the epic-grain empty form has to be stated in prose or the author must pad.
        flat = " ".join(self.schema.split())
        self.assertIn("- (none — story-owned)", flat)
        self.assertIn("the heading is parsed", flat)

    def test_delivery_status_rule_carves_out_the_shim_watchpoint(self):
        # The schema REQUIRES a shim/dual-emit trap, which is a shipped-versus-remaining split.
        flat = " ".join(self.schema.split())
        self.assertIn("false-positive trap", flat)
        self.assertIn("would let the resolver believe something false", flat)


class PlanSummaryBlockTests(unittest.TestCase):
    """The `## Plan summary` block: a human-readable digest of the posted plan, rendered immediately
    before the `## Handoff`.

    Three things make it a contract rather than a style note, and each is asserted here:

    1. The shared file is the single source of truth (`_shared/plan-summary.md`) and the planner
       *cites* it rather than restating its section set — the render-don't-restate rule.
    2. The old invariant "it replaces any bullet-list summary" would have a literal-following model
       SUPPRESS the block, so its removal is asserted, not just the new wording's presence.
    3. The carrying predicate is "this session posted or refreshed a plan", NOT `plan: ✓`. The two
       differ exactly on the HARD-revise Start-fresh exit, whose `plan:` marker points at the
       superseded comment while the session ran no persist.
    """

    SUMMARY = SHARED_DIR / "plan-summary.md"
    RENDERINGS = REFERENCES_DIR / "handoff-renderings.md"
    HANDOFF_FORMAT = SHARED_DIR / "handoff-format.md"

    def setUp(self):
        self.summary = self.SUMMARY.read_text(encoding="utf-8")
        self.renderings = self.RENDERINGS.read_text(encoding="utf-8")
        self.router = ROUTER.read_text(encoding="utf-8")

    def test_the_shared_contract_exists_and_names_the_block(self):
        self.assertTrue(self.SUMMARY.is_file(), "skills/_shared/plan-summary.md must exist")
        self.assertIn("## Plan summary", self.summary)

    def test_the_section_set_is_fixed_and_complete(self):
        for label in (
            "**Approach:**",
            "**What changed:**",
            "**Architecture decisions:**",
            "**Seams & integration points:**",
            "**Phases:**",
            "**Watch for:**",
        ):
            self.assertIn(label, self.summary, label)

    def test_focus_is_the_one_parameter_that_varies(self):
        # "Parameterize before you playbook" (CLAUDE.md): planner-vs-resolver is a VALUE, not a branch.
        flat = " ".join(self.summary.split())
        self.assertIn("`focus` parameter", flat)
        self.assertIn("one rendering and no branch", flat)

    def test_the_block_is_never_part_of_the_handoff(self):
        flat = " ".join(self.summary.split())
        self.assertIn("never part of `## Handoff`", flat)
        shared = " ".join(self.HANDOFF_FORMAT.read_text(encoding="utf-8").split())
        self.assertIn("The `## Plan summary` block is not part of this one", shared)
        # The closed-set preamble's "never ... add a block the shape doesn't carry" must not read as a
        # ban on this one, or a literal-following model emits neither.
        self.assertIn("it sits outside `## Handoff`", shared)

    def test_the_invitation_names_the_revise_command_not_an_in_session_edit(self):
        flat = " ".join(self.summary.split())
        self.assertIn("/ctrl-freaq:planner", flat)
        self.assertIn("revise mode because the plan is present", flat)

    def test_discussion_after_the_handoff_is_read_only(self):
        # Load-bearing: the in-place plan-comment update op is already named in revise.md /
        # story-jit.md, and planner SKILL.md §3's "only write surface is the plan comment" PERMITS the
        # write. Without this rule the invitation invites an unverified in-session plan edit.
        flat = " ".join(self.summary.split())
        self.assertIn("Discussion after the handoff is read-only", flat)
        self.assertIn("no GitHub write, no re-draft, and no reviewer re-run", flat)

    def test_router_sanctions_the_block_and_drops_the_suppressing_phrasing(self):
        flat = " ".join(self.router.split())
        self.assertNotIn("replaces any bullet-list summary", flat)
        self.assertIn("`## Plan summary` block", flat)
        self.assertIn("../_shared/plan-summary.md", flat)
        self.assertIn("read-only after it", flat)

    def test_renderings_owns_the_render_and_cites_the_shared_file(self):
        # One home for the instruction, the same mechanism `Grounding:` and `Open questions:` use: the
        # spine never mentions them either.
        flat = " ".join(self.renderings.split())
        self.assertIn("../../_shared/plan-summary.md", flat)
        self.assertIn("do not restate any of it here", flat)
        spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.assertNotIn("plan-summary.md", spine)

    def test_the_predicate_is_posted_or_refreshed_not_plan_tick(self):
        flat = " ".join(self.renderings.split())
        self.assertIn("Predicate: this session posted or refreshed a plan", flat)
        self.assertIn("HARD-revise *Start fresh*", flat)
        self.assertIn("ran no persist", flat)

    def test_the_non_carrying_shapes_are_enumerated(self):
        flat = " ".join(self.renderings.split())
        for shape in (
            "trivial change",
            "knowledge gap",
            "epic-shaped abort",
            "slice first",
            "OQ blocks the whole plan",
        ):
            self.assertIn(shape, flat, shape)

    def test_the_composite_session_emits_one_block(self):
        # Same hazard as the frozen bug-(b) `Open questions:` requirement: a composite run matching one
        # structural example and doubling up (or dropping) the other.
        flat = " ".join(self.renderings.split())
        self.assertIn("composite epic+story session emits ONE block, not two", flat)
        self.assertIn("at the **story plan's** altitude", flat)

    def test_every_routable_playbook_names_the_block_in_its_handoff_section(self):
        # The routed playbook's `## Handoff` section is the operative ending instruction. Naming the
        # block in only one of them lets a literal reader of single.md — the commonest path — infer it
        # is revise-specific.
        for name in sorted(ROUTABLE_PLAYBOOKS):
            body = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            tail = body[body.index("## Handoff"):]
            self.assertIn("`## Plan summary` block", tail, name)

    def test_revise_carries_the_reconciliation_it_already_computed(self):
        flat = " ".join((PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8").split())
        self.assertIn("**What changed:**", flat)
        self.assertIn("Carry it; never re-derive it", flat)
        # The plan BODY's no-history-layer ban is unrelated to session output; saying so keeps a later
        # editor from deleting the section as a banned history layer.
        self.assertIn("governs the body, not this session's output", flat)

    def test_spine_drops_the_full_body_dump_but_keeps_the_opt_in_show(self):
        flat = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.assertIn("no full-body dump on the common path", flat)
        # The scope qualifier: with nothing posted, the operator still needs the exact text.
        self.assertIn('Show the **full body** only when the user said "don\'t post yet"', flat)



class PolishReRouteTests(unittest.TestCase):
    """An evaluator polish re-route (skills/_shared/polish-ledger.md): the ledger's `apply` entries
    become a new phase on a multi-phase PR, but fold into the one phase on a single-phase PR — which
    carries no `## Phase tracker`, so a new phase there would leave phase 1 unrecorded as shipped."""

    def test_single_phase_folds_rather_than_adding_a_phase(self):
        text = " ".join((REFERENCES_DIR / "revise-reconciliation.md").read_text(encoding="utf-8").split())
        self.assertIn("### A polish re-route: the ledger's `apply` items become a phase", text)
        self.assertIn("add **no** phase: fold the entries into the plan's one phase", text)
        self.assertIn("add **one new phase after the shipped ones**", text)

    def test_the_resolver_finalisation_is_a_source_too(self):
        # 4.30.0: a Re-plan answered on the resolver's finalisation card reaches the planner before the PR
        # ever reached the evaluator; a trailing operator phase may still be pending then.
        text = " ".join((REFERENCES_DIR / "revise-reconciliation.md").read_text(encoding="utf-8").split())
        self.assertIn("When the evaluator — or the resolver's finalisation "
                      "(`../../resolver/references/finalisation.md`), before the PR ever reached the evaluator — "
                      "sends a PR here", text)
        self.assertIn("Every code-shipping phase has shipped by the time either sends it here", text)
        for name in ("revise.md", "story-jit.md"):
            playbook = " ".join((REPO_ROOT / "skills" / "planner" / "playbooks" / name).read_text(encoding="utf-8").split())
            self.assertNotIn("evaluator polish re-route", playbook, name)


class ReviewLevelSectionTests(unittest.TestCase):
    """#97: the plan's `## Review level` section — one line per phase, the resolver's review effort from
    the phase's blast radius. Default `medium`; `high` / `xhigh` only from the analysis; absent → `high`."""

    def setUp(self):
        self.schema = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        self.flat = " ".join(self.schema.split())
        self.spine = " ".join((PLAYBOOKS_DIR / "plan-spine.md").read_text(encoding="utf-8").split())

    def test_the_section_sits_outside_the_frozen_fence(self):
        # The schema block is byte-frozen (prd.md §7); the section is documented beside it, as the
        # `checkpoint:` key is.
        self.assertNotIn("Review level", _first_fenced_block(self.schema))
        self.assertIn("- phase <N> · <medium | high | xhigh> — <why>", self.schema)
        self.assertIn("sits directly after `## Phases`", self.flat)

    def test_the_section_rule(self):
        heading = "## The `## Review level` section"
        self.assertIn(heading, self.schema)
        self.assertLess(self.schema.index("## The `checkpoint:` phase key"), self.schema.index(heading))
        for phrase in (
            "The default is `medium`",
            "Write `high` or `xhigh` only where the blast-radius analysis calls for it",
            "`low` and `max` are not levels a plan may write",
            "is reviewed at `high`",
            "Never judge by expected line or file count",
            "a single `phase 1` line for a single-phase plan",
        ):
            self.assertIn(phrase, self.flat, phrase)

    def test_the_spine_fixes_review_levels_findings_like_dod_coverage(self):
        self.assertIn("and `review_levels` findings (plan-schema.md, \"The `## Review level` section\")", self.spine)


class CheckpointPhaseKeyTests(unittest.TestCase):
    """The `checkpoint:` phase key: the planner decides, per `code-shipping` phase, whether the
    resolver asks the operator before continuing to the next phase in the same session.

    Planner-only by operator decision (2026-10-04): no repo-level default, absent = `pause` (the
    one-phase-per-session behaviour every older plan was written for). The reviewer's BLOCKER on a
    `continue` over a pause category is what gives "security changes get a human look" its teeth.
    """

    def setUp(self):
        self.schema = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        self.flat_schema = " ".join(self.schema.split())
        self.reviewer = " ".join(
            (REFERENCES_DIR / "plan-reviewer-prompt.md").read_text(encoding="utf-8").split()
        )

    def test_section_sits_outside_the_frozen_fence_between_sub_issue_and_numbering(self):
        heading = "## The `checkpoint:` phase key"
        self.assertIn(heading, self.schema)
        self.assertNotIn("checkpoint", _first_fenced_block(self.schema))
        self.assertLess(self.schema.index("## The `sub-issue:` phase key"), self.schema.index(heading))
        self.assertLess(self.schema.index(heading), self.schema.index("## Phase numbering"))

    def test_grammar_default_and_tie_break(self):
        for phrase in (
            "Exactly `pause` or `continue`",
            "Any other value is `PHASES_MALFORMED`",
            "An **absent** key means `pause`",
            "When unsure, `pause`",
            "the resolver may add a look",
            "only the operator's answer on the look card continues past a pause",
            "`--pause`",
        ):
            self.assertIn(phrase, self.flat_schema, phrase)

    def test_revise_rule_handles_the_last_phase_and_shipped_phases(self):
        for phrase in (
            "unless it is the new last `code-shipping` phase",
            "an **unshipped** phase that stops being last",
            "a phase that becomes last drops it",
            "A shipped phase's key is moot",
            "the session always stops after it",
        ):
            self.assertIn(phrase, self.flat_schema, phrase)
        revise = (PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8")
        self.assertIn("the new last phase carries none", " ".join(revise.split()))

    def test_every_authoring_route_names_the_key(self):
        for name in ("single.md", "story-jit.md", "revise.md"):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("checkpoint", text, name)

    def test_reviewer_blocks_a_continue_over_a_pause_category(self):
        self.assertIn("**Checkpoints.**", self.reviewer)
        self.assertIn("is a BLOCKER: that phase's change set would ship and be built on", self.reviewer)
        self.assertIn("is a SUGGESTION only (absent reads as `pause`, so it fails safe)", self.reviewer)

    def test_plan_summary_shows_where_the_resolver_stops(self):
        summary = (SHARED_DIR / "plan-summary.md").read_text(encoding="utf-8")
        self.assertIn("`· pauses after`", summary)


class ClosesDodTargetTests(unittest.TestCase):
    """4.32.0: `closes-dod` indexes `## Definition of done` and nothing else, checked by script.

    The plan reviewer used to read "acceptance criteria / Definition of Done" as one thing, so a plan
    indexing an `## Acceptance criteria` list passed review, and the resolver received indexes against
    a DoD its prep parsed as empty.
    """

    def setUp(self):
        self.schema = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        self.section = " ".join(
            self.schema.partition("## The `closes-dod` target")[2]
            .split("## Epic-plan and story-under-epic sections")[0]
            .split()
        )
        self.spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.reviewer = " ".join(
            (REFERENCES_DIR / "plan-reviewer-prompt.md").read_text(encoding="utf-8").split()
        )

    def test_the_section_sits_outside_the_frozen_fence(self):
        self.assertNotIn("closes-dod` target", _first_fenced_block(self.schema))
        self.assertLess(
            self.schema.index("## Phase numbering"), self.schema.index("## The `closes-dod` target")
        )

    def test_the_rule_names_the_heading_and_the_absent_case(self):
        self.assertIn("**`## Definition of done`** section", self.section)
        self.assertIn("With no `## Definition of done` section, every phase is `closes-dod: (none)`", self.section)

    def test_the_card_offers_rename_first_and_verifies_it(self):
        self.assertIn('`header: "DoD heading"`', self.section)
        rename = self.section.index("**Rename to `## Definition of done`.**")
        # One criteria heading is a one-option judgment: renamed and announced, no card.
        self.assertIn("**One heading holds criteria** → a judgment with one credible option", self.section)
        self.assertIn("**Several hold criteria** → gate", self.section)
        self.assertLess(rename, self.section.index("**Plan without a DoD.**"))
        self.assertLess(self.section.index("**Plan without a DoD.**"), self.section.index("**Re-route to the drafter.**"))
        self.assertIn('parse.py dod "<facts.dod.body_path>"', self.section)

    def test_the_card_runs_at_s1_and_says_why(self):
        s1 = self.spine.partition("## S1")[2].split("## S2")[0]
        self.assertIn("`facts.dod.other_checklists`", s1)
        self.assertIn("**Why at S1.**", self.section)

    def test_s7_checks_coverage_against_the_issue_body(self):
        self.assertIn(
            '${CLAUDE_PLUGIN_ROOT}/scripts/parse.py phases "<facts.scratch>/plan.md" --issue-body '
            '"<facts.dod.body_path>"',
            self.spine,
        )
        self.assertIn("`dod_coverage` findings", self.spine)

    def test_issue_body_writes_start_from_the_last_written_body(self):
        # S8's whole-body pointer write must not revert the S1 rename.
        self.assertIn("**Every issue-body write starts from the body this session last wrote.**", self.section)
        s8 = self.spine.partition("## S8")[2]
        self.assertIn("stage the body this session last wrote", s8)

    def test_the_router_lists_the_fact_the_gate_and_the_handoff_shape(self):
        self.assertIn("`dod` (DoD `present`/`count`", self.router)
        self.assertIn("DoD heading,", self.router)
        self.assertIn("DoD heading → drafter", self.router)
        renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        self.assertIn("**DoD heading — re-route to the drafter.**", renderings)

    def test_the_reviewer_indexes_the_dod_heading_only(self):
        self.assertIn("issue body's `## Definition of done` section 1-based — that heading only", self.reviewer)
        self.assertIn("so any index claimed against such an issue is a BLOCKER", self.reviewer)


class ConcurrencyDeclarationTests(unittest.TestCase):
    """#112: an epic plan's `## Integration strategy` declares its own concurrency — the dependency
    graph, the file-overlap read and the concurrent sets — in a grammar `scripts/concurrency.py` reads.
    The schema's worked example is parsed through the reader, so a grammar edit on either side fails
    here; the playbooks and the reviewer are pinned to require and check it."""

    @classmethod
    def setUpClass(cls):
        import concurrency

        cls.concurrency = concurrency
        schema = (REFERENCES_DIR / "plan-schema.md").read_text(encoding="utf-8")
        cls.section = schema.partition("### The concurrency declaration")[2].partition("\n### Epic delivery log")[0]
        cls.epic = (PLAYBOOKS_DIR / "epic.md").read_text(encoding="utf-8")
        cls.story_jit = (PLAYBOOKS_DIR / "story-jit.md").read_text(encoding="utf-8")
        reviewer = (REFERENCES_DIR / "plan-reviewer-prompt.md").read_text(encoding="utf-8")
        cls.dim5 = reviewer.partition("5. **Sequencing**")[2].partition("\n6. **")[0]
        cls.dim8 = reviewer.partition("8. **Epic-story coherence**")[2].partition("\n9. **")[0]
        cls.dim5_flat = " ".join(cls.dim5.split())
        cls.section_flat = " ".join(cls.section.split())

    def test_the_schema_states_the_three_subsections_and_the_graph_grammar(self):
        self.assertIn("(epic only — inside ## Integration strategy)", self.section)
        for heading in ("`### Dependency graph`", "`### File overlap`", "`### Concurrent sets`"):
            self.assertIn(heading, self.section)
        self.assertIn("| Story | Waits on | Kind | Gates | Condition |", self.section)
        for kind in self.concurrency.KINDS:
            self.assertIn("`%s`" % kind, self.section)
        for gate in self.concurrency.GATES:
            self.assertIn("`%s`" % gate, self.section)

    def test_the_schema_routes_shared_docs_and_migrates_a_pre_grammar_declaration(self):
        self.assertIn("routes it to the **finalization story**", self.section)
        self.assertIn("A merged story's graph rows stay verbatim", self.section)
        self.assertIn("The one exception is a declaration written before this grammar", self.section)
        self.assertIn("it keeps its `## Story breakdown` bullet and its place in its `### Concurrent sets` bullet", self.section_flat)

    def test_the_schema_states_every_rule_the_reader_enforces(self):
        for rule in (
            "A row never waits on its own story, and never names one story twice.",
            "Every story a row names, in `Story` or `Waits on`, is a `## Story breakdown` story.",
            "The edges, all three gates included, form no cycle",
            "its stories are `## Story breakdown` stories, each named once",
            "An empty cell on an edge row reads as `—`",
        ):
            self.assertIn(rule, self.section_flat)

    def test_the_worked_example_parses_through_the_reader(self):
        result = self.concurrency.read(_first_fenced_block(self.section))
        self.assertEqual(result["malformed"], [])
        self.assertEqual(result["source"], self.concurrency.SOURCE_DECLARED)

    def test_the_epic_route_requires_the_declaration_and_the_reviewer_checks_it(self):
        self.assertIn('`plan-schema.md`, "The concurrency declaration"', self.epic)
        self.assertIn("each shared doc routed to the finalization story", self.epic)
        self.assertIn("checks the concurrency declaration against those contracts", self.epic)

    def test_the_story_route_grounds_on_start_edges_and_keeps_to_file_ownership(self):
        self.assertIn("**Keep to the epic's file ownership.**", self.story_jit)
        self.assertIn("every story its `### Dependency graph` `start` edges name has landed", self.story_jit)
        self.assertIn("when the epic plan has no declaration or one the reader rejects", " ".join(self.story_jit.split()))

    def test_the_reviewer_checks_the_graph_the_overlap_and_the_sets(self):
        self.assertIn("reads the plan's own `## Story contracts` and `## Integration", self.dim5)
        for check in ("**Graph vs contracts.**", "**Graph vs overlap.**", "**Sets.**", "**Shared-doc routing.**"):
            self.assertIn(check, self.dim5)
        self.assertIn("is a BLOCKER whichever concurrent sets they are in", self.dim5_flat)
        self.assertIn("naming a story outside `## Story breakdown`, a story waiting on itself", self.dim5_flat)
        self.assertIn("and so is any cycle among the graph's edges", self.dim5_flat)
        self.assertIn("An epic without one names each doc's editing stories", self.dim5_flat)
        self.assertIn("whichever sets they are in: gates come only from the graph", self.section_flat)
        self.assertIn("**keeps to the epic's file ownership**", self.dim8)


if __name__ == "__main__":
    unittest.main()
