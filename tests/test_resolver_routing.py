"""Offline tests for the resolver skill (docs/implementation.md S10).

Three offline surfaces the S10 DoD / Testing section names:

1. **Routing-table fixtures** (`vector` → `playbooks/<file>`). The router's visible routing table
   (`skills/resolver/SKILL.md` §2) maps a prep-derived `vector` to the one playbook a session reads.
   These tests parse that table, assert it covers exactly the four routable playbooks
   (standard / story / epic / comment-only), that each points at a real `playbooks/<file>`, and that
   the type→playbook mapping is byte-consistent with `prep_resolver._suggested_playbook` (the router
   "confirms `suggested_playbook` against the table", architecture.md §5).

2. **The interleaving pattern-grep, committed as a validator** (S10 DoD box 1). A test that greps the
   four routable playbooks (and the shared spine) for cross-type conditionals and fails on a hit —
   both `if … (epic|story|standard|comment-only) … else …` constructs AND "when the issue is a
   <type>"-style prose branches. The route IS the branch; a playbook is a linear narrative.

3. **`--dry-run` persist envelopes.** Every GitHub write the playbooks specify goes through
   `gh_persist.py` (SKILL.md §3; the resolver has no scriptless raw-gh executor). These run each such
   invocation with `--dry-run` against the offline shim and assert a conformant envelope (status ok,
   `would_run` present, no live gh call).

Plus the structural bar (router ≤150, router+largest playbook ≤584 = half of v1's 1169) and the
DoD-annotation byte-compat check against the S1 capture (S10 DoD box 2, offline half).

No network: gh_persist.py's `gh` calls resolve to the offline shim via tests/run.py's PATH wiring;
the dry-run path performs no gh call at all (asserted).
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
SKILL_DIR = REPO_ROOT / "skills" / "resolver"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
REFERENCES_DIR = SKILL_DIR / "references"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"

# The four routable playbooks (the spine is a shared file, not a routable route).
ROUTABLE_PLAYBOOKS = {"standard.md", "story.md", "epic.md", "comment-only.md"}
SPINE = "resolve-spine.md"

# Half of the v1 resolver SKILL.md (1169 lines, docs/specs/baseline.md §1) = 584 (floor).
V1_HALF_BAR = 1169 // 2

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_resolver  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, shimenv  # noqa: E402
from tests.support.retired_tokens import FORBIDDEN_CONTRACT_TOKENS  # noqa: E402


# ---------------------------------------------------------------------------
# Router routing-table parse
# ---------------------------------------------------------------------------

# The router's §2 table rows carry a backticked `playbooks/<file>` in the SECOND cell. The first cell
# is a free-form vector description (e.g. `comment_only: true (…)` or `type: standard`), so we key the
# parse off the playbook cell and pull the driving token from the first cell.
_ROUTE_ROW_RE = re.compile(
    r"^\|\s*(?P<vector>[^|]+?)\s*\|\s*`(?P<playbook>playbooks/[a-z0-9-]+\.md)`\s*\|"
)


def _parse_router_routing_table(router_text):
    """Return an ordered list of (vector_cell, playbooks/<file>) parsed from the router's routing
    table. Rows whose second cell isn't a backticked `playbooks/<file>` path (the header and
    separator rows) don't match."""
    rows = []
    for line in router_text.splitlines():
        match = _ROUTE_ROW_RE.match(line)
        if match:
            rows.append((match.group("vector").strip(), match.group("playbook")))
    return rows


class RouterRoutingTableTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")
        self.rows = _parse_router_routing_table(self.router_text)
        self.playbooks = [pb for _, pb in self.rows]

    def test_router_exists_and_has_no_model_effort_pins(self):
        # Model/effort are not pinned — skills inherit the invoking session's model and effort.
        self.assertTrue(ROUTER.is_file(), "router SKILL.md must exist")
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: resolver", head)
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
        # S10 DoD box 1: exactly four playbooks. The playbooks/ dir holds the four routable playbooks
        # plus the single shared spine (architecture.md §5: "a playbook may additionally pull in its
        # skill's single shared spine file").
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

    def test_table_matches_prep_suggested_playbook_for_each_type(self):
        # architecture.md §5: prep proposes `suggested_playbook`; the router confirms it against the
        # table. Assert byte-consistency for each (type, comment_only) the vector can take.
        cases = [
            ("standard", False, "standard.md"),
            ("story", False, "story.md"),
            ("epic", False, "epic.md"),
            ("standard", True, "comment-only.md"),  # comment_only overrides type in prep
        ]
        for issue_type, comment_only, expected in cases:
            self.assertEqual(
                prep_resolver._suggested_playbook(issue_type, comment_only),
                expected,
                "prep _suggested_playbook(%r, %r) should be %r" % (issue_type, comment_only, expected),
            )
            self.assertIn(
                "playbooks/" + expected,
                self.playbooks,
                "the router table must route to playbooks/%s (prep proposes it)" % expected,
            )

    def test_prep_suggested_playbook_only_targets_files_the_table_lists(self):
        table_basenames = {Path(pb).name for pb in self.playbooks}
        for issue_type in ("standard", "story", "epic"):
            self.assertIn(prep_resolver._suggested_playbook(issue_type, False), table_basenames)
        self.assertIn(prep_resolver._suggested_playbook("standard", True), table_basenames)

    def test_spine_referenced_by_standard_and_story_only(self):
        # The shared spine (resolve-spine.md) is read by the two code-shipping routes; comment-only.md
        # and epic.md are distinct action flows that do NOT read the spine (architecture.md §5).
        spine_path = PLAYBOOKS_DIR / SPINE
        self.assertTrue(spine_path.is_file(), "the shared spine resolve-spine.md must exist")
        for name in ("standard.md", "story.md"):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn(SPINE, text, "%s must read the shared spine" % name)
        for name in ("comment-only.md", "epic.md"):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "resolve-spine.md](resolve-spine.md",
                text,
                "%s must NOT pull in the code-shipping spine (it is a distinct action flow)" % name,
            )


# ---------------------------------------------------------------------------
# Router structural bar (S10 DoD boxes 1, 5, 7)
# ---------------------------------------------------------------------------


def _iter_md(dir_path):
    yield from sorted(dir_path.rglob("*.md"))


def _fence_stripped_lines(path):
    """Yield (lineno, line, in_fence) for a markdown file, tracking ``` fenced code blocks so a
    prose mention of a banned command (describing what NOT to do) is distinguishable from an actual
    command in a code block."""
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            yield i, line, in_fence
            continue
        yield i, line, in_fence


class RouterStructuralBarTests(unittest.TestCase):
    def test_router_at_most_150_lines(self):
        n = len(ROUTER.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(n, 150, "router SKILL.md is %d lines (bar: <= 150)" % n)

    def test_router_plus_largest_playbook_at_most_half_v1(self):
        # S10 DoD box 7 / prd.md §10: router + largest playbook <= half of v1's 1169 = 584.
        router_lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        playbook_lines = {
            p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in PLAYBOOKS_DIR.glob("*.md")
        }
        largest = max(playbook_lines.values())
        self.assertLessEqual(
            router_lines + largest,
            V1_HALF_BAR,
            "router (%d) + largest playbook (%d) = %d exceeds %d (half of v1's 1169): %r"
            % (router_lines, largest, router_lines + largest, V1_HALF_BAR, playbook_lines),
        )


class PlaybookInterleavingGrepTests(unittest.TestCase):
    """S10 DoD box 1: the interleaving pattern-grep, committed as a validator. A playbook is a linear
    narrative for exactly one route — zero cross-type conditionals. This fails on either form of
    type-interleaving in any playbook (routable or the spine):

      - an `if … <type> … else …` conditional construct, and
      - a `when the issue/PR/type is a <type>` prose branch.

    Both are the constructs a v1-to-v2 whittle would leave behind; the route IS the branch, so a
    playbook that switches on the type has drifted off the §5 bar.
    """

    # Conditional construct: `if <...> (type) <...> else`.
    _IF_ELSE = re.compile(
        r"\bif\b[^.\n]{0,50}\b(story|standard|epic|comment-only)\b[^.\n]{0,50}\belse\b",
        re.IGNORECASE,
    )
    # Prose branch: `when (the issue|the pr|the type|it's) [is] (a|an) <type>`.
    _WHEN_TYPE = re.compile(
        r"\bwhen (the (issue|pr|type) is|it.?s)\s+(a |an )?(story|standard|epic|comment-only)\b",
        re.IGNORECASE,
    )

    def test_playbooks_have_no_cross_type_conditionals(self):
        for playbook in PLAYBOOKS_DIR.glob("*.md"):
            text = playbook.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                self.assertIsNone(
                    self._IF_ELSE.search(line),
                    "playbook %s:%d looks like a cross-type conditional: %r"
                    % (playbook.name, i, line),
                )
                self.assertIsNone(
                    self._WHEN_TYPE.search(line),
                    "playbook %s:%d looks like a when-<type> prose branch: %r"
                    % (playbook.name, i, line),
                )


class ContractTokenGateTests(unittest.TestCase):
    """S10 DoD box 5: grep gates over skills/resolver/ — zero retired-executor tokens, zero v1 skill-invocation
    namespace strings, zero GATHER_/PERSIST_ op names, zero §P IDs, zero raw persist/gather writes,
    zero ref-arithmetic. Prose that *describes* a banned form (a pitfall telling you NOT to hand-roll
    `gh issue create`, or the audit note explaining the v1 `git show <ref>:` reads it replaced) is
    allowed OUTSIDE a code fence; an actual command inside a ``` fence is a violation."""

    def test_no_github_ops_or_old_names_or_op_names_or_pids(self):
        forbidden = FORBIDDEN_CONTRACT_TOKENS
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = forbidden.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden contract token under skills/resolver/ at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_no_raw_persist_or_gather_writes_in_code_fences(self):
        # A persist/gather WRITE that bypasses gh_persist.py: gh issue|pr create/edit/comment/review/
        # close/reopen, or gh api ... DELETE. Only flag inside a ``` code fence (a real command); the
        # resolver has no scriptless raw-gh executor (merge is the evaluator's), so any such command
        # is a violation. Sub-agent self-fetch READS (gh issue view / gh api ...comments) are not
        # writes and don't match.
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
        # Banned ref-arithmetic (architecture.md §6/§10): git show <ref>:<path> and git grep <ref>.
        # A bare `git show <commit>` single-commit view is permitted; only <ref>:<path> extraction and
        # `git grep <ref>` are banned. Flag only inside a code fence.
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
        # CLAUDE.md's banned-shorthand validator is `grep -rnE '\bw/'` (word-boundary), so "review/"
        # (w preceded by a word char) is not a hit — only a standalone "w/" abbreviation is.
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
    """Run gh_persist.py <args> as a real subprocess under the offline shim. When body_text is given,
    write it to a temp file and substitute @BODY@ in args with the path. Returns
    (completed_process, parsed_envelope_or_None)."""
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


class SliceClosingRungTests(unittest.TestCase):
    """S6's slice-closing rung (#17). Slices exist so GitHub's rollup tracks delivery progress, so a
    slice set that never closes leaves a permanent `0/N` — worse than filing none. These pin the three
    things that make the rung correct rather than merely present."""

    def setUp(self):
        raw = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.spine = re.sub(r"\s+", " ", raw.replace("**", ""))
        self.rule = re.sub(
            r"\s+",
            " ",
            (REFERENCES_DIR / "dod-projection-rule.md").read_text(encoding="utf-8").replace("**", ""),
        )

    def test_the_rung_keys_off_the_phase_sub_issue_field(self):
        self.assertIn("sub_issue", self.spine)
        self.assertIn("facts.phases", self.spine)

    def test_it_closes_on_the_last_serving_phase_not_the_first(self):
        """N:1 — several phases may serve one slice. Closing on the first would report an increment
        complete while part of it is still unbuilt."""
        self.assertRegex(self.spine, r"closes on the last of the phases serving it, never the first")
        self.assertRegex(self.rule, r"Close only when every phase naming it is ticked")

    def test_substrate_and_unmapped_phases_close_nothing(self):
        self.assertRegex(self.spine, r"\(none\).{0,20}is substrate")
        self.assertRegex(self.spine, r"is unmapped; both close nothing")
        self.assertRegex(self.rule, r"close nothing")

    def test_closing_before_merge_is_justified_by_the_demonstrable_bar(self):
        """The objection this pre-empts: "you closed an issue whose code isn't on main"."""
        self.assertRegex(self.spine, r"bar is .demonstrable.")
        self.assertRegex(self.spine, r"not that its code is on the default branch")

    def test_tier_1_ticks_the_acceptance_criteria_not_a_dod(self):
        self.assertIn("## Acceptance criteria", self.spine)
        self.assertRegex(self.rule, r"DoD contract above applies to the parent")

    def test_both_writes_are_idempotent_so_the_backstop_can_re_run(self):
        self.assertRegex(self.spine, r"[Bb]oth are idempotent")
        self.assertRegex(self.rule, r"Already-closed slice.*no-op")

    def test_slice_close_dry_run(self):
        proc, env = _run_persist(["close", "octo/widgets", "104", "--reason", "completed", "--dry-run"])
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env.get("op"), "close")
        self.assertTrue(env.get("dry_run"))
        self.assertIn("would_run", env)


class TrackerReconciliationTests(unittest.TestCase):
    """#46: the PR's `## Phase tracker` is reconciled against the plan before the cursor is read.

    The tracker and the plan's `## Phases` are two artifacts keyed by the same integer phase numbers,
    written by different sessions. A planner revise may insert a phase and renumber the unshipped tail,
    which changes the row set — and continue mode selects its next phase by ROW NUMBER. Unreconciled,
    the cursor silently points at different work, and a tracker with fewer rows than the plan reads as
    complete and flips the PR draft → ready early.
    """

    def setUp(self):
        self.spine_path = PLAYBOOKS_DIR / SPINE
        self.spine = self.spine_path.read_text(encoding="utf-8")
        self.flat = " ".join(self.spine.split())
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_the_tracker_is_a_prep_fact_not_a_model_re_read(self):
        self.assertIn("parsed by prep into `facts.tracker.rows`, never re-fetched", self.flat)
        self.assertNotIn("re-read from the existing PR body", self.flat)

    def test_authority_is_split_between_tick_state_and_row_set(self):
        # The un-qualified "authoritative record" is why nothing rebuilt the rows.
        self.assertIn("authoritative record of which phases **shipped**", self.flat)
        self.assertIn("not authoritative for which phases *exist*", self.flat)
        self.assertIn("(`facts.phases`) owns the row set", self.flat)

    def test_reconciliation_precedes_cursor_selection(self):
        self.assertLess(
            self.flat.index("Reconcile the tracker before you read that cursor"),
            self.flat.index("Write the reconciled tracker"),
        )
        self.assertIn("then select the cursor from it", self.flat)
        self.assertIn("Never select a phase by row title", self.flat)

    def test_the_three_silent_rebuild_classes_are_named_with_their_disposition(self):
        for key in ("`missing`", "`dropped`", "`retitled`"):
            self.assertIn(key, self.flat, key)
        self.assertIn("All three are a silent rebuild: no tick's meaning changes", self.flat)

    def test_unticked_rows_are_rebuilt_wholesale_including_their_title(self):
        # The common post-insert shape: the tail row keeps its number and takes the NEW phase's title,
        # and the displaced phase arrives as a `missing` row. Prep reports no drift for it (there is no
        # tick or commit to preserve), so the spine must say what to do without a diff entry to key on
        # — otherwise the resolver builds the inserted work under the displaced phase's title.
        self.assertIn("Every **unticked** row is rewritten from its plan phase wholesale", self.flat)
        self.assertIn("takes the new phase's title", self.flat)

    def test_a_sub_row_is_carried_through_rather_than_reconciled(self):
        # #51: the spine must say what to do with a `Phase <N>-<label>` row, or a rebuild drops the
        # operator annotation that is the only record the phase landed.
        self.assertIn("`diff.sub_rows`", self.flat)
        self.assertIn("carry it through verbatim", self.flat)
        self.assertIn("bound to phase `<N>`", self.flat)

    def test_the_two_unknowable_state_classes_gate_for_their_own_reason(self):
        # #48 review: `unparsed` (rows prep could not read) and `duplicated` (two rows for one phase)
        # gate because the tick state is unknowable, not because work moved. Rebuilding under either
        # would guess what shipped.
        self.assertIn("`unparsed`", self.flat)
        self.assertIn("`duplicated`", self.flat)
        self.assertIn("The tick state is\n  unknowable", self.spine.replace("\r\n", "\n"))
        self.assertIn("mean guessing what shipped", self.flat)

    def test_the_rebuild_preserves_an_operator_annotation_not_just_a_commit(self):
        # An operator row's `(operator action <ISO-date>)` is the only record that phase landed, so a
        # rebuild that preserved only `(commit <sha>)` would erase it.
        self.assertIn("`(commit <sha>)` or `annotation`", self.flat)
        self.assertIn("the only record that phase landed", self.flat)

    def test_conflict_is_a_gate_with_its_three_recorded_options(self):
        self.assertIn('header: "Tracker"', self.flat)
        for option in ("**Re-plan**", "**Rebuild un-ticked**", "**Abort**"):
            self.assertIn(option, self.flat, option)
        self.assertIn("`diff.conflict`", self.flat)

    def test_the_conflict_classes_are_named_and_defined(self):
        self.assertIn("`shifted` (a ticked row whose work now sits at a different phase number)", self.flat)
        self.assertIn("`removed_shipped` (a ticked row whose phase is gone)", self.flat)

    def test_the_override_never_carries_a_tick_onto_work_it_did_not_ship(self):
        self.assertIn("never carry a tick onto work it did not ship", self.flat)
        self.assertIn("## Tracker reconciliation", self.flat)
        self.assertIn("displaced", self.flat)

    def test_the_gate_names_the_planner_rule_it_is_downstream_of(self):
        self.assertIn("revise-reconciliation.md", self.flat)
        self.assertIn("classifies HARD at the planner", self.flat)

    def test_s6_writes_the_reconciled_row_set(self):
        self.assertIn("the **reconciled** row set from S4, never the rows a prior session wrote", self.flat)

    def test_completion_condition_runs_against_the_reconciled_tracker(self):
        flat_renderings = " ".join(self.renderings.split())
        self.assertIn("ticked in the **reconciled** `## Phase tracker` (spine S4)", flat_renderings)
        self.assertIn("*unshipped*, never not-applicable", flat_renderings)

    def test_router_lists_the_tracker_fact(self):
        router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.assertIn("`tracker`", router)
        self.assertIn("`diff.conflict` the gate", router)

    def test_prep_owns_the_pr_fetch_no_raw_gh_pr_view_in_a_fence(self):
        # facts-by-script: the body comes from prep_resolver's gh_pr_gather call, never a fenced
        # `gh pr view` the model runs itself.
        for lineno, line, in_fence in _fence_stripped_lines(self.spine_path):
            if in_fence:
                self.assertNotIn("gh pr view", line, "%s:%d" % (SPINE, lineno))

    def test_structural_bar_still_holds(self):
        router_lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        playbooks = {
            path.name: len(path.read_text(encoding="utf-8").splitlines())
            for path in PLAYBOOKS_DIR.glob("*.md")
        }
        self.assertLessEqual(
            router_lines + max(playbooks.values()),
            V1_HALF_BAR,
            "#46 added the S4 reconciliation step: %r" % playbooks,
        )


class PlaybookPersistDryRunTests(unittest.TestCase):
    """Each GitHub write the resolver playbooks specify, run with --dry-run: a conformant envelope,
    status ok, `would_run` present, exit 0, no live gh call. These are the exact gh_persist invocation
    SHAPES the playbooks name (spine S5 PR create; S6 issue-body DoD projection + Phase tracker;
    comment-only S2; epic.md S4 integration PR + S5 body-tick + the baseline comments)."""

    def _assert_dry_run_ok(self, proc, envelope, expected_op):
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertIsNotNone(envelope, "expected exactly one envelope on stdout")
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope.get("status"), "ok")
        self.assertEqual(envelope.get("op"), expected_op)
        self.assertTrue(envelope.get("dry_run"))
        self.assertIn("would_run", envelope)

    def test_dod_projection_edit_body_dry_run(self):
        # spine S6: gh_persist.py edit-body <repo> <issue> <issue-body-projected.md>
        proc, env = _run_persist(
            ["edit-body", "octo/widgets", "142", "@BODY@", "--dry-run"],
            body_text="## Definition of done\n- [x] Export CSV (closed by phase 2, commit abc1234)\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-body")

    def test_comment_only_answer_dry_run(self):
        # comment-only.md S2: gh_persist.py comment <repo> issue <issue> <comment.md>
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "142", "@BODY@", "--dry-run"],
            body_text="This issue is blocked by the open question in #77.\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_epic_baseline_comment_dry_run(self):
        # epic.md S3 / epic-flow.md: gh_persist.py comment <repo> issue <epic> <baseline.md>
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "150", "@BODY@", "--dry-run"],
            body_text="🤖 Baseline established\n- Epic branch SHA: abc1234\n- Main SHA: abc1234\n- Result: green\n- Date: 2026-07-09\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_epic_body_tick_edit_body_dry_run(self):
        # epic.md S5: gh_persist.py edit-body <repo> <epic> <epic-body-closed.md>
        proc, env = _run_persist(
            ["edit-body", "octo/widgets", "150", "@BODY@", "--dry-run"],
            body_text="## Stories\n- [x] #151\n## Definition of done\n- [x] all shipped\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-body")

    def test_dry_run_makes_no_live_gh_call(self):
        # Belt-and-braces: the dry-run path must not touch gh at all. A clean exit 0 with a would_run
        # envelope (and no url) IS the proof no gh call fired.
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "142", "@BODY@", "--dry-run"],
            body_text="answer\n",
        )
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertTrue(env.get("dry_run"))
        self.assertNotIn("url", env, "a dry-run must not carry a live-write url")


class PrCreateContractTests(unittest.TestCase):
    """The PR-open write-path gap this step originally reported is CLOSED: the coordinator
    authorized an additive `create-pr` op on `gh_persist.py` within S10 (architecture.md §2 "if a
    needed operation has no script, extend a script"), landed alongside this skill cutover. These
    tests assert the op's existence + contract (the resolver playbooks' actual invocation shape) at
    the resolver-cutover layer; the op's own exhaustive coverage (happy path, --draft, EMPTY_BODY_FILE,
    AUTH_REQUIRED, dry-run, usage errors, receipts) lives in
    `tests/test_gh_persist.py::CreatePrHappyPathTests`."""

    def test_gh_persist_has_create_pr_subcommand_with_required_flags(self):
        proc = subprocess.run(
            [sys.executable, str(GH_PERSIST), "create-pr", "--help"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        help_text = proc.stdout + proc.stderr
        for flag in ("--title", "--base", "--head", "--draft", "--dry-run"):
            self.assertIn(flag, help_text, "gh_persist.py create-pr must accept %s" % flag)

    def test_standard_route_pr_open_dry_run(self):
        # spine S5 fresh-mode first push, standard route: base main, ready (no --draft).
        proc, env = _run_persist(
            [
                "create-pr", "octo/widgets", "@BODY@", "--title", "Add CSV export (#142)",
                "--base", "main", "--head", "142-add-csv-export", "--dry-run",
            ],
            body_text="## Doc grounding\ncites architecture.md §3\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env.get("op"), "create-pr")
        self.assertTrue(env.get("dry_run"))
        self.assertIn("would_run", env)
        self.assertNotIn("--draft", env["would_run"])

    def test_story_route_pr_open_dry_run_bases_on_epic_branch(self):
        # story.md: base is the parent epic's integration branch, never main.
        proc, env = _run_persist(
            [
                "create-pr", "octo/widgets", "@BODY@", "--title", "Add export service (#151)",
                "--base", "epic/150-chat-session-ux-polish", "--head", "151-add-export-service",
                "--dry-run",
            ],
            body_text="This story targets the `epic/150-chat-session-ux-polish` integration branch.\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(env.get("op"), "create-pr")
        self.assertIn("epic/150-chat-session-ux-polish", env["would_run"])

    def test_multi_phase_pr_open_dry_run_carries_draft_flag(self):
        # spine S4/S5: a multi-phase issue opens the PR as draft; the last-phase handoff later
        # flips it ready via the sanctioned `gh pr ready` toggle (architecture.md §10), unaffected
        # by this op.
        proc, env = _run_persist(
            [
                "create-pr", "octo/widgets", "@BODY@", "--title", "feat(llm): #640 spike harness",
                "--base", "main", "--head", "640-spike", "--draft", "--dry-run",
            ],
            body_text="## Phase tracker\n- [ ] Phase 1 — substrate\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("--draft", env["would_run"])

    def test_epic_integration_pr_open_dry_run_carries_draft_flag(self):
        # epic.md S4.1: base main, head the epic integration branch, and ALWAYS --draft. The PR
        # opens early (as soon as the branch is ahead of main) so a human can review overall epic
        # progress; draft is what keeps it out of the evaluator's reach until S4.4's ready flip.
        proc, env = _run_persist(
            [
                "create-pr", "octo/widgets", "@BODY@", "--title", "Epic #150: Chat & session UX polish",
                "--base", "main", "--head", "epic/150-chat-session-ux-polish", "--draft", "--dry-run",
            ],
            body_text="## Goal\n…\nFixes #150\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(env.get("op"), "create-pr")
        self.assertIn("--draft", env["would_run"])

    def test_epic_integration_pr_body_refresh_routes_through_edit_pr_body(self):
        # epic.md S4.2: the draft PR's body is refreshed on every epic run. `edit-body` is
        # `gh issue edit` and rejects a PR number, so this is a distinct op, not a reuse.
        proc, env = _run_persist(
            ["edit-pr-body", "octo/widgets", "300", "@BODY@", "--dry-run"],
            body_text="## Goal\n…\n3 of 5 stories closed\nFixes #150\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(env.get("op"), "edit-pr-body")
        self.assertIn("pr edit", env["would_run"])
        self.assertNotIn("issue edit", env["would_run"])

    def test_epic_playbook_parameterises_the_pr_steps_on_prep_facts(self):
        # facts-by-script: the early-open trigger and the idempotency guard are prep facts the
        # playbook consumes as data. A rewrite that drops these names has re-derived one of them
        # in prose (a hand-counted "is it ahead of main" or a hand-searched "is a PR already
        # open" is exactly the duplicate-PR hazard these facts exist to remove).
        text = (PLAYBOOKS_DIR / "epic.md").read_text(encoding="utf-8")
        for fact in ("commits_ahead", "commits_ahead_base", "integration_pr", "edit-pr-body"):
            self.assertIn(fact, text, "epic.md must name %s" % fact)
        self.assertIn(
            "OPEN_PR_LOOKUP_UNAVAILABLE",
            text,
            "epic.md must gate its PR-open on the lookup notice's absence — a null "
            "integration_pr carried by that notice is UNKNOWN, not ABSENT, and opening on an "
            "unknown files a second integration PR",
        )

    def test_the_body_refresh_keeps_the_sections_it_does_not_own(self):
        # 4.37.0: S4.2 re-rendered the whole body on every epic run, so a re-route back here wiped
        # the integration PR's `## Polish` ledger (and the evaluator's dispositions in it) and its
        # `## Follow-ups`. It re-renders only its own sections, from the body prep staged, and skips
        # the write when that body could not be read.
        text = re.sub(r"\s+", " ", (PLAYBOOKS_DIR / "epic.md").read_text(encoding="utf-8"))
        self.assertIn("Re-render **only the sections this route owns**", text)
        self.assertIn("`facts.epic.integration_pr.body_path`", text)
        self.assertIn("`INTEGRATION_PR_BODY_UNAVAILABLE`), skip the write", text)
        # S4.3's loop leaves its ledger and follow-ups on this PR, through spine S5.2 / S7.
        self.assertIn("The loop exits through spine S5.2", text)
        self.assertIn("woven into this PR's `## Follow-ups`", text)

    def test_playbooks_route_pr_open_through_create_pr_not_a_fabricated_flag_set(self):
        # The resolve-spine.md / epic.md prose must name the real op (create-pr), not the
        # non-existent `create --base/--head` shape this step originally (wrongly) drafted.
        for playbook in ("resolve-spine.md", "epic.md"):
            text = (PLAYBOOKS_DIR / playbook).read_text(encoding="utf-8")
            self.assertIn(
                "create-pr",
                text,
                "%s must route PR-open through gh_persist.py create-pr" % playbook,
            )

    def test_spine_mandates_the_closing_keyword_on_fresh_pr_open(self):
        # Filed defect (S10 live parity, scenario 1 D2 — docs/specs/parity/resolver.md): v1
        # (SKILL.md:888) mandates the PR body carry `Fixes #<number>` (or `Closes #<number>`) so
        # GitHub auto-links and auto-closes on merge; v2's spine had listed every OTHER PR-body
        # section (Doc grounding / Plan / Audit override / Plan override / Phase tracker /
        # Predecessor) but omitted this mandate, so v2 PRs never auto-closed. Regression guard: the
        # spine's fresh-mode PR-open prose must state the mandate.
        text = (PLAYBOOKS_DIR / "resolve-spine.md").read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"Fixes #<issue-number>.{0,40}Closes\s*\n?\s*#<issue-number>",
            "resolve-spine.md must mandate the closing keyword (Fixes/Closes #<issue-number>) on "
            "fresh-PR open, per v1 SKILL.md:888",
        )
        self.assertIn(
            "must be `Fixes #<issue-number>`",
            text,
            "the closing keyword must be stated as mandatory, not optional prose",
        )

    def test_spine_mandates_the_v1_fresh_pr_title_shape(self):
        # D4 (checked, ruled MANDATED): v1 SKILL.md:885 passes a literal --title "Fix: <summary>
        # (#<issue-number>)" to `gh pr create` -- not free prose, a command-line argument every
        # fresh-PR-open takes. The spine must be faithful to that shape (it also feeds the
        # evaluator's squash-subject derivation, which reads a Conventional-Commits-prefixed title).
        text = (PLAYBOOKS_DIR / "resolve-spine.md").read_text(encoding="utf-8")
        self.assertIn(
            'Fix: <summary> (#<issue-number>)',
            text,
            "resolve-spine.md must mandate the v1 fresh-PR title shape, per SKILL.md:885",
        )

    def test_create_pr_dry_run_with_closing_keyword_and_mandated_title(self):
        # End-to-end dry-run proof: the exact title shape + a body whose first line is the closing
        # keyword round-trips cleanly through gh_persist.py create-pr.
        proc, env = _run_persist(
            [
                "create-pr", "octo/widgets", "@BODY@",
                "--title", "Fix: greet() returns a personalized greeting (#142)",
                "--base", "main", "--head", "142-add-csv-export", "--dry-run",
            ],
            body_text="Fixes #142\n\n## Doc grounding\ncites architecture.md §3\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(env.get("op"), "create-pr")
        self.assertIn("Fix: greet() returns a personalized greeting (#142)", env["would_run"])


class ArtifactRenderingByteCompatTests(unittest.TestCase):
    """S10 DoD box 2 (offline half): the DoD-projection annotation forms the resolver writes must diff
    clean against the S1-captured closed set (docs/specs/examples/dod-annotations.md). The three
    resolver-authored ticked forms are contract tokens (the evaluator + planner parse them verbatim),
    so each must appear byte-for-byte in the resolver's dod-projection-rule.md."""

    EXAMPLES_DIR = REPO_ROOT / "docs" / "specs" / "examples"

    # The three ticked forms the resolver authors (dod-annotations.md capture rows for "resolver §9").
    RESOLVER_FORMS = (
        "- [x] <text> (closed by phase <N>, commit <short-sha>)",
        "- [x] <text> (closed by phase <N>, operator action <ISO-date>)",
        "- [x] <text> (closed by commit <short-sha>)",
    )

    def test_resolver_annotation_forms_present_in_s1_capture(self):
        # Guard the source: each resolver-authored form must be a row in the S1 capture verbatim, so
        # the byte-compat check below is anchored to the frozen contract, not a moving target.
        capture = (self.EXAMPLES_DIR / "dod-annotations.md").read_text(encoding="utf-8")
        for form in self.RESOLVER_FORMS:
            self.assertIn(form, capture, "S1 dod-annotations capture must carry %r" % form)

    def test_projection_rule_renders_each_resolver_form_byte_compatible(self):
        rule = (REFERENCES_DIR / "dod-projection-rule.md").read_text(encoding="utf-8")
        for form in self.RESOLVER_FORMS:
            self.assertIn(
                form,
                rule,
                "dod-projection-rule.md must render the annotation form %r byte-for-byte (it "
                "diverges from the S1 capture otherwise — box 2)" % form,
            )


class QuestionTypeHandoffVariantTests(unittest.TestCase):
    """Filed defect (S10 live parity, scenario 3 D1 — docs/specs/parity/resolver.md): a `question`-type
    issue's terminal handoff must follow skills/_shared/handoff-format.md's question-type rule (omit
    research:/plan: entirely, add an Audience: line) — v2 had rendered `· plan: ✗` and dropped
    Audience:. Fixed in comment-only.md's Handoff section (dispatches by the issue's own type) +
    handoff-renderings.md (a dedicated "Terminal — question-type issue" shape). These tests assert the
    fix without editing _shared (render, don't restate)."""

    RENDERINGS = REFERENCES_DIR / "handoff-renderings.md"
    COMMENT_ONLY = PLAYBOOKS_DIR / "comment-only.md"

    @staticmethod
    def _fenced_blocks(path, fence="```"):
        text = path.read_text(encoding="utf-8")
        blocks, cur, inb = [], [], False
        for line in text.splitlines():
            if line.strip() == fence:
                if inb:
                    blocks.append("\n".join(cur))
                    cur = []
                    inb = False
                else:
                    inb = True
                continue
            if inb:
                cur.append(line)
        return blocks

    def test_question_type_variant_present_and_shaped_per_shared_contract(self):
        text = self.RENDERINGS.read_text(encoding="utf-8")
        self.assertIn(
            "## Terminal — question-type issue",
            text,
            "handoff-renderings.md must carry a dedicated question-type terminal shape",
        )
        # Isolate the actual RENDERED example (the fenced ## Handoff block) -- the surrounding prose
        # legitimately explains the rule by naming the forbidden marker ("do not render `plan: ✗`
        # here"), which must not itself trip a naive whole-section grep (same fence-scoping
        # discipline as ContractTokenGateTests' raw-gh / ref-arithmetic checks).
        rendered_blocks = [b for b in self._fenced_blocks(self.RENDERINGS) if "## Handoff" in b]
        question_rendering = next(
            (b for b in rendered_blocks if "· question" in b and "**Audience:**" in b), None
        )
        self.assertIsNotNone(
            question_rendering, "the question-type section must contain a rendered ## Handoff example"
        )
        # The Issue: line for this shape must end at the type marker `· question` -- no plan:/research:
        # segment at all (handoff-format.md:33: "the research: and plan: markers omitted").
        self.assertRegex(
            question_rendering,
            r"\*\*Issue:\*\* #\d+ — [^\n]+ · open · question\s*\n",
            "the question-type Issue: line must end at '· question' with no plan:/research: marker",
        )
        self.assertNotRegex(
            question_rendering,
            r"plan:\s*[✓✗]",
            "the RENDERED question-type shape must never carry a plan: marker (handoff-format.md's "
            "rule) -- prose explaining the rule is fine; the example itself must not regress",
        )
        self.assertIn(
            "**Audience:**",
            question_rendering,
            "the question-type shape must carry the Audience: line per handoff-format.md",
        )

    def test_non_pr_resolution_shape_unchanged_for_build_types(self):
        # The pre-existing "Terminal — non-PR resolution" shape (OQ refusal / triage / decline on a
        # build-type issue) still renders its plan: marker -- only the question-type issue's own
        # handoff omits it.
        text = self.RENDERINGS.read_text(encoding="utf-8")
        self.assertIn("## Terminal — non-PR resolution", text)
        section = text.split("## Terminal — non-PR resolution", 1)[1].split(
            "## Terminal — question-type issue", 1
        )[0]
        self.assertIn("plan:", section)

    def test_comment_only_playbook_dispatches_by_issue_type(self):
        text = self.COMMENT_ONLY.read_text(encoding="utf-8")
        self.assertIn(
            "question-type issue",
            text,
            "comment-only.md's Handoff section must route a genuine question-type issue to the "
            "dedicated rendering, not the build-type Terminal — non-PR resolution shape",
        )
        self.assertIn("non-PR resolution", text)


class ReRouteHandoffMarkerTests(unittest.TestCase):
    """Filed defect (S10 live parity, scenario 2 D1 — docs/specs/parity/resolver.md): the multi-phase
    re-route worked example (and two siblings) carried an off-closed-set `✓` glyph on the PR-line
    `review:`/`health:` markers, contradicting the reference's own forward-scoped intro. RULING (per
    _shared/handoff-format.md's actual field definitions, evaluator-owned — see the D1 annotation):
    `not run` is the conformant value on EVERY resolver-authored handoff, forward or re-route alike,
    because those two fields denote the evaluator's posted verdict / health-cache result, never "the
    resolver's own `code-review` loop or §8 gate ran." These tests assert every rendered PR-line in
    handoff-renderings.md stays on the closed set, and that the intro states the unconditional scope."""

    RENDERINGS = REFERENCES_DIR / "handoff-renderings.md"
    STANDARD = PLAYBOOKS_DIR / "standard.md"

    @staticmethod
    def _fenced_blocks(path, fence="```"):
        text = path.read_text(encoding="utf-8")
        blocks, cur, inb = [], [], False
        for line in text.splitlines():
            if line.strip() == fence:
                if inb:
                    blocks.append("\n".join(cur))
                    cur = []
                    inb = False
                else:
                    inb = True
                continue
            if inb:
                cur.append(line)
        return blocks

    def test_epic_draft_pr_handoff_never_points_at_the_evaluator(self):
        # The load-bearing guard for the early-open change: the evaluator refuses a DRAFT PR
        # (skills/evaluator/SKILL.md's draft-PR guard), so a handoff that points there while the
        # integration PR is still a draft deadlocks the operator. The in-progress shape's Next: is
        # the epic cadence's next step instead.
        text = self.RENDERINGS.read_text(encoding="utf-8")
        self.assertIn(
            "## In progress — Epic integration draft PR open",
            text,
            "handoff-renderings.md must carry the draft-integration-PR in-progress shape",
        )
        draft_rendering = next(
            (
                block
                for block in self._fenced_blocks(self.RENDERINGS)
                if "## Handoff" in block and "**PR:**" in block and "· draft ·" in block
                and "**Epic:**" in block
            ),
            None,
        )
        self.assertIsNotNone(
            draft_rendering,
            "the in-progress section must contain a rendered ## Handoff example whose PR: line "
            "carries the closed-set `draft` state marker",
        )
        self.assertNotIn(
            "github-pipeline:evaluator",
            draft_rendering,
            "a draft epic integration PR must never hand off to the evaluator — its draft guard "
            "would stop the session with nothing to do",
        )

    def test_no_pr_line_carries_an_off_closed_set_review_or_health_glyph(self):
        # The closed set has no bare-checkmark value for either field (handoff-format.md:52-53) --
        # every rendered PR: line's review:/health: segment must be one of the defined values, never
        # a raw ✓/✗ glyph standing in for "ran, no formal verdict."
        off_vocab = re.compile(r"\b(review|health):\s*[✓✗]")
        for block in self._fenced_blocks(self.RENDERINGS):
            if "**PR:**" not in block:
                continue
            self.assertNotRegex(
                block,
                off_vocab,
                "an off-closed-set ✓/✗ glyph on review:/health: survives in a rendered PR: line: %r"
                % block,
            )

    def test_every_resolver_authored_pr_line_before_evaluator_action_is_not_run(self):
        # Every PR: line rendered by a shape that fires BEFORE the evaluator has acted (forward opens,
        # every re-route) must carry review: not run / health: not run. The two shapes that legitimately
        # carry an evaluator-populated value (the plan-currency re-route, which CAN carry a value the
        # evaluator posted on a PRIOR pass) are the only exception, and even there review: stays not run
        # in the frozen worked example -- so this asserts the floor: review: not run appears on every
        # PR: line in the file (a stricter, always-true invariant this reference's worked examples
        # satisfy today).
        text = self.RENDERINGS.read_text(encoding="utf-8")
        pr_lines = [
            line for block in self._fenced_blocks(self.RENDERINGS)
            for line in block.splitlines() if line.strip().startswith("**PR:**")
        ]
        self.assertTrue(pr_lines, "expected at least one rendered PR: line")
        for line in pr_lines:
            self.assertIn(
                "review: not run",
                line,
                "every resolver-authored PR: line must carry review: not run (the evaluator hasn't "
                "acted yet on any resolver exit): %r" % line,
            )
        self.assertIn(
            "not run",
            text,
            "handoff-renderings.md must still document the not-run rule",
        )

    def test_intro_states_the_unconditional_not_run_scope(self):
        # Regression guard for the root cause: the intro must not scope the not-run rule to forward
        # exits only -- it must explicitly cover every re-route too (including mid-phases continue).
        text = self.RENDERINGS.read_text(encoding="utf-8")
        self.assertIn(
            "every resolver-authored handoff",
            text,
            "the intro must state the not-run rule unconditionally, not forward-exit-scoped",
        )
        self.assertIn(
            "AND every re-route",
            text,
            "the intro must explicitly cover re-routes, including the mid-phases continue-mode case",
        )
        self.assertNotIn(
            'on a resolver forward exit because the resolver never runs',
            text,
            "the old forward-scoped phrasing (the root-cause ambiguity) must not survive",
        )

    def test_standard_playbook_states_the_rule_inline_for_multiphase_shapes(self):
        # The routed playbook must not depend entirely on the reference getting the rule right --
        # restate it inline for the multi-phase bullet, where the D1 defect actually surfaced.
        text = self.STANDARD.read_text(encoding="utf-8")
        self.assertIn(
            "review: not run · health: not run",
            text,
            "standard.md's multi-phase bullet must restate the not-run rule inline",
        )


class WorkspaceModelV3Tests(unittest.TestCase):
    """v3 workspace-model pins: the router names WORKSPACE_MISMATCH (the ambient-assertion
    decision), and no resolver prose or fence invokes worktree removal — every removal is the
    operator's workspace-close."""

    def test_router_names_workspace_mismatch(self):
        router = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("WORKSPACE_MISMATCH", router)
        self.assertIn("workspace-open", router)

    def test_no_remove_work_invocation_anywhere(self):
        for path in sorted(SKILL_DIR.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "remove --work", text,
                "%s still invokes worktree removal — that is workspace-close's, exclusively"
                % path.name,
            )


class MainLoopReviewFixRoundTests(unittest.TestCase):
    """4.11.0: the per-iteration fix sub-agent is retired — the fix round runs in the main
    conversation, and exactly one cold-read audit sub-agent runs after `code-review` settles.

    The sub-agent's per-iteration cold start plus its JSON -> card -> re-dispatch serialization was
    the loop's dominant latency, and its tool calls never streamed, so the operator could not see
    what was being fixed. Everything the prompt encoded (rubric, disciplines, injection, gate,
    deadlock check, guard rails) survives in `review-fix-round.md`; only the dispatch is gone. The
    convergence machinery that chose WHEN to cold-read goes with it: the read is now unconditional
    within a phase — once per phase at a given HEAD (4.13.0), never gated on a provenance count.
    """

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.flat = " ".join(self.spine.split())
        self.reference = REFERENCES_DIR / "review-fix-round.md"

    def test_the_fix_round_reference_exists_and_is_not_a_sub_agent_prompt(self):
        self.assertTrue(self.reference.is_file(), "review-fix-round.md must exist")
        for pattern in ("*-prompt*.md", "*-sub-agent*.md"):
            self.assertNotIn(
                self.reference,
                set(REFERENCES_DIR.glob(pattern)),
                "the main-loop fix round must not match the sub-agent-prompt discovery glob",
            )

    def test_the_spine_reads_the_fix_round_reference_and_dispatches_only_the_cold_read(self):
        self.assertIn("review-fix-round.md", self.flat)
        self.assertIn("cold-read-audit-prompt.md", self.flat)
        self.assertIn("Run one **fix round** on it per the reference, in this conversation", self.flat)
        self.assertIn("Cold-read audit — once per phase, after settle", self.flat)

    def test_the_stall_card_replaced_the_iter_cap_card(self):
        # 4.21.0: the fixed 2/4 cap and its `Iter cap` card are gone — the loop runs on defect-tier
        # progress and asks only when it stops making it (or hits the emergency ceiling).
        self.assertIn('header: "Loop stall"', self.flat)
        self.assertNotIn('header: "Iter cap"', self.flat)
        for option in ("**Continue**", "**Accept current**", "**Abort**", "**Re-plan** when churn fired"):
            self.assertIn(option, self.flat)
        self.assertNotIn("**Cold-read audit** /", self.flat)

    def test_the_fix_round_carries_the_rubric_and_the_guard_rail_cards(self):
        text = self.reference.read_text(encoding="utf-8")
        for bucket in (
            "Addressable",
            "Explicitly-deferred",
            "Decision-required",
            "Grounding-violation",
            "Plan-settled",
            "Deferred-by-plan",
        ):
            self.assertIn(bucket, text, "the classification rubric must survive the move")
        for header in ('"Review loop"', '"Settled item"', '"Decision"', '"Tests red"', '"Grounding"'):
            self.assertIn(header, text, "guard rail %s must survive as a direct card" % header)
        self.assertIn("AskUserQuestion", text)
        self.assertNotIn("needs_decision", text)

    def test_addressable_items_carry_a_defect_or_polish_tier(self):
        # The tier replaces the Cheap-fix-override bucket: a defect keeps the loop open, polish is
        # fixed on merit or left for the `## Polish` ledger the evaluator adjudicates.
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        self.assertIn("Every Addressable item also gets a **tier**", text)
        self.assertIn("- **defect** — correctness, a broken contract or invariant", text)
        self.assertIn("Fix it **on merit**", text)
        self.assertIn("Polish never keeps the loop open", text)
        self.assertNotIn("- **Cheap-fix-override** —", text)
        self.assertNotIn("Cheap-fix-override", self.flat)
        # Evaluator-directed items are decided: re-recording them would ping-pong the PR.
        self.assertIn("marked `apply` is Addressable on iteration 1 whatever its tier", text)

    def test_the_loop_runs_on_defect_progress_with_a_grace_round(self):
        # The #957 replay: a falling count alone read one-new-defect-per-round (real progress) as a
        # stall at round 3, and a file-level churn signal fired on files a polish rename made "hot".
        for phrase in (
            "the loop runs on the **defect count**",
            "Plan-settled, Deferred-by-plan, Refuted and Explicitly-deferred never count",
            "**Progress** — the count fell from the previous round's, **or** none of its defects is **loop-induced**",
            "round 1, with nothing to compare, always counts as progress",
            "**No progress** — the count did not fall **and** at least one defect is loop-induced",
            "The first such round is a **grace round**",
            "the second in a row renders the **stall card**",
            "**Churn** — at least 2 defects, most of them loop-induced",
            "does not also render the stall card",
        ):
            self.assertIn(phrase, self.flat)
        self.assertNotIn("most of the round's defect items sit in the hot seam", self.flat)

    def test_a_reopened_loop_starts_a_fresh_baseline(self):
        self.assertIn(
            "the next round counts as round 1: a fresh baseline, an unused grace round "
            "(the ceiling's count carries on)",
            self.flat,
        )

    def test_loop_induced_reads_the_durable_defect_fix_record(self):
        # PR #62 review: the per-round record lived only in the conversation (a re-entered phase lost
        # it) and was hunk-level with nothing marking which hunks were defect fixes.
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        self.assertIn(
            "A defect is **loop-induced** when its cited site is a `<path>:<symbol>` an earlier round's defect fix changed",
            text,
        )
        self.assertIn("A file touched only by polish or comment edits never makes a finding loop-induced", text)
        self.assertIn("`Defect fixes: phase <N> @ <sha> — <path>:<symbol>;", text)
        self.assertIn("never a polish site", text)
        # Carried across sessions through the loop comment, like the refuted-items list.
        self.assertIn("Seed the defect-fix record from every `Defect fixes: phase <N>` line for the current phase", text)
        self.assertNotIn("git diff <sha>^..<sha>", text)
        # The file-level hot seam stays the fix-design trigger, where erring wide is safe.
        self.assertIn("It scopes that dispatch only", text)

    def test_the_revision_run_waits_for_a_pending_operator_phase_and_reads_vetoes(self):
        for phrase in (
            "every phase is ticked — operator ones too; a pending operator/decision-only phase is the operator-phase handoff above, not a revision",
            "a veto no re-plan has reassigned (`facts.dod_vetoes[].reassigned_to` null) clears only by re-planning",
            "S6 projects only vetoes reassigned to phase 1",
            "a veto a re-plan reassigned (`facts.dod_vetoes[].reassigned_to`) is projected when that phase ships",
        ):
            self.assertIn(phrase, self.flat)
        rule = " ".join((REFERENCES_DIR / "dod-projection-rule.md").read_text(encoding="utf-8").split())
        self.assertIn("`…; re-plan reassigned to phase Y, awaiting its ship`", rule)
        replan = " ".join(
            (REPO_ROOT / "skills" / "planner" / "references" / "revise-reconciliation.md").read_text(encoding="utf-8").split()
        )
        self.assertIn("append the re-plan mark inside the same annotation", replan)

    def test_accept_current_files_after_the_pr_exists_and_the_light_base_is_stated(self):
        self.assertIn("in fresh mode once `create-pr` returns the URL, so each has its parent PR", self.flat)
        self.assertIn("(`<round sha>^...HEAD` — the round's single step-8 commit)", self.flat)
        self.assertNotIn("<pre-round HEAD>", self.flat)

    def test_plan_settled_uses_the_still_true_test(self):
        # The #959 run settled a finding that asked a "must refuse" list to refuse more — the fix left
        # the plan's bullet true, so it was a defect to fix inside the plan, not a settled item.
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        for phrase in (
            "**The still-true test** decides \"contests\"",
            "**Now false** → the finding contests it",
            "**Still true** → it does not, and the item is Addressable",
            "A list of **required behaviour** (\"must refuse …\", \"checks …\") is a **minimum**",
            "A **definition** (\"X means …\") is not",
            "A verbatim repeat of a refuted-items entry keeps the bucket that entry records",
        ):
            self.assertIn(phrase, text)
        prompt = " ".join((REFERENCES_DIR / "fix-design-prompt.md").read_text(encoding="utf-8").split())
        # The sub-agent never sees the rubric, so its conflict test carries the same distinction.
        self.assertIn("would make a decision bullet **false as written**", prompt)
        self.assertIn("a list of **required behaviour** (\"must refuse …\", \"checks …\") is a minimum", prompt)
        self.assertIn("These are locked **as written**", prompt)
        pitfalls = " ".join((REFERENCES_DIR / "common-pitfalls.md").read_text(encoding="utf-8").split())
        self.assertIn("Re-litigating means asking to make a decision false", pitfalls)

    def test_a_settled_repeat_with_new_evidence_is_re_examined(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        self.assertIn("re-settled silently on its second occurrence: no card, no second reply", text)
        self.assertIn("A repeat that **brings new evidence** is classified fresh on the merits", text)
        self.assertIn("so a reviewer widening the finding cannot cycle it", text)

    def test_a_recurring_settled_item_gets_a_fix_design_before_any_card(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        self.assertIn("**Settled-item pre-check**", text)
        self.assertIn("still at most once per round", text)
        self.assertIn("fix it this round with **no card**", text)
        self.assertIn("Named in `## Plan conflicts`, or in `## Needs a plan decision` with an open intent → the `Settled item` card, **Re-plan** recommended", text)
        self.assertIn("in `## Needs a plan decision` as out of scope → the card, **Keep settled** recommended", text)
        # The Deferred-by-plan skip is justified by ownership, not by what fix design could answer.
        self.assertIn("a later phase already owns that seam, so the plan has placed the work", text)
        # A Decision card raised in step 4 keeps the round's plan lines — never a second dispatch.
        self.assertIn("**A card raised in step 4** — the `Settled item` card, or a `Decision` card from fix design's", text)
        # The Decision card's own definition carries the fix-design variant.
        self.assertIn("or, when fix design returned `## Needs a plan decision`, its candidate designs", text)
        prompt = " ".join((REFERENCES_DIR / "fix-design-prompt.md").read_text(encoding="utf-8").split())
        self.assertIn("When `<<loop_files>>` is `(none)` there is no prior correction to account for", prompt)
        self.assertIn("Named in `## Out of reach` → the card, **Fix it here** recommended", text)
        self.assertIn("A recurring **Refuted** item skips the dispatch", text)

    def test_the_settled_item_card_offers_re_plan_and_never_defer(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        self.assertIn('`header: "Settled item"`', text)
        for option in ("**Re-plan**", "**Fix it here**", "**Keep settled**"):
            self.assertIn(option, text)
        self.assertIn("offered only for a plan-anchored item", text)
        self.assertIn("Never **Accept + defer**: a plan question is not a follow-up", text)
        self.assertIn("The `Review loop` card is for these addressed-item deadlocks only", text)
        self.assertIn("`Review loop` or `Settled item` card does not also render the stall card", self.flat)

    def test_pr_63_review_fixes(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        # A repeated Refuted item stays Refuted — it gets the evidence re-check, not fix design.
        self.assertIn("never re-bucketed", text)
        # Deferred-by-plan skips fix design: its later-phase seam would read as out of reach and the
        # card would recommend pulling that phase's work forward.
        self.assertIn("A recurring **Deferred-by-plan** item skips the dispatch", text)
        self.assertIn("still owned → the card, **Keep settled** recommended", text)
        # Keep settled survives the session through the settled block.
        self.assertIn("An entry marked `kept`", text)
        self.assertIn("(`(×3, kept)`)", text)
        self.assertIn("recorded as `kept` on the entry, step 8 — never re-raised, this session or later", text)
        # No positional cross-references (CLAUDE.md "Stable §-anchors over positional cross-references").
        self.assertNotIn("independent-defect pass below", text)
        prompt = " ".join((REFERENCES_DIR / "fix-design-prompt.md").read_text(encoding="utf-8").split())
        self.assertNotIn("`## Plan conflicts` below", prompt)

    def test_pr_63_second_review_fixes(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        # New evidence reopens even an operator-kept settlement.
        self.assertIn("a `kept` one included, its mark dropped", text)
        # The pre-check must not spend the set check's fix design.
        self.assertIn("may dispatch once more when its set check then fails", text)
        # One scope test, shared with fix design.
        self.assertIn("the same scope test fix design applies (`fix-design-prompt.md` step 5)", text)
        # File as follow-up continues the round; it never ends it.
        continuing = text[text.index("A **continuing** answer"):text.index("A **terminating** answer")]
        self.assertIn("File as follow-up", continuing)

    def test_a_re_plan_first_fixes_the_rounds_independent_defects(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        for phrase in (
            "**The Re-plan independent-defect pass.**",
            "its fix site shares no `<path>:<symbol>` with the re-planned finding's site",
            "sharing a **file** with the re-planned finding's site stands in for sharing its seam",
            "its fix would be the same whichever way the re-plan goes",
            "When in doubt, it is **coupled**",
            "run **steps 4 and 6–8 for the independent subset**",
            "**no further `code-review`**",
            "revert those fixes and record them, with no `Tests red` card",
            "take no pass",
            'except "The Re-plan independent-defect pass"',
            "**Stall card** (S5.1 step 3) → **no pass**: the round's fixes are already committed",
            "keep the existing plan lines for the independent subset and drop the rest — no second fix-design dispatch",
        ):
            self.assertIn(phrase, text)
        self.assertIn("a **Re-plan** first fixes the round's independent defects", self.flat)

    def test_evidence_refuted_findings_are_a_settled_bucket(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        self.assertIn("- **Refuted** — the finding is factually wrong or unreachable, shown by evidence you cite", text)
        self.assertIn("No evidence → not refuted", text)
        self.assertIn("- refuted — <one-line item> — evidence: <what was read or run>", text)
        self.assertIn("Plan-settled / Deferred-by-plan / Refuted item", text)
        self.assertIn("Deferred-by-plan, Refuted, or polish → the loop has **settled**", self.flat)

    def test_a_sibling_site_miss_is_fixed_not_carded(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        self.assertIn("**sibling site** of a class fix you already made is not a match", text)
        self.assertIn("fix it per the class discipline, and it counts as loop-induced", text)

    def test_polish_tier_bounds_and_must_fix_list(self):
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        # 4.31.0: the cost gate parked the PR's own small fixes as follow-ups (17 of 30 "not worth holding
        # the merge" deferrals in one repo's audit should have been fixes), so polish on introduced or
        # adjacent code is fixed whatever its size; the guard-path exception survives as an exception.
        self.assertNotIn("cheap (no new spec file, no fix-design dispatch)", text)
        self.assertIn("Size alone never parks it", text)
        self.assertIn("it is a refactor touching a guard, a raise, or any fail-closed path, which is not fixed as polish", text)
        self.assertIn("**Always fix in-loop, never ledger**, polish matching the evaluator's `apply` criteria", text)
        self.assertIn("an unfinished `## Changes` entry of the phase being built", text)
        self.assertIn("**Tie-break:** when defect-vs-polish is unclear, tier it defect", text)

    def test_stall_card_continue_and_accept_current_semantics(self):
        self.assertIn("the stall check pauses for the next N `code-review` runs, then applies again", self.flat)
        self.assertIn("at the ceiling, Continue raises it by N", self.flat)
        self.assertIn("with its findings **recorded, not fixed**", self.flat)
        self.assertIn("so the stalling round never ships unread", self.flat)
        self.assertIn("exit S5.1 as committed", self.flat)

    def test_the_emergency_ceiling_counts_every_review_run(self):
        self.assertIn("**emergency ceiling**: 8 `code-review` runs in S5.1", self.flat)
        self.assertIn("light re-reviews and the post-cold-read run included", self.flat)

    def test_a_polish_only_round_gets_one_light_re_review_and_no_chain(self):
        self.assertIn("A round whose only fixes were polish first gets one **light re-review**", self.flat)
        self.assertIn("polish it finds goes to the ledger unfixed", self.flat)

    def test_the_ledger_is_written_at_the_phase_push(self):
        self.assertIn("the `## Polish` ledger", self.flat)
        self.assertIn("gains one entry per polish item the loop left — `open`, `drop` if no shown cost, or finalisation's answer", self.flat)
        self.assertIn("`applied (commit <sha>)` on each `apply` item it fixed", self.flat)

    def test_settle_covers_a_verdict_that_never_approves(self):
        """A reviewer that requests changes but names only deferrable items addresses nothing.

        Keying the exit on the approval line alone spins the loop on an unchanged PR until the cap;
        the retired sub-agent's exit keyed on "no items", which handled it.
        """
        self.assertIn("the round addressed nothing that keeps the loop open", self.flat)
        self.assertIn("a non-approving verdict whose every item classified as Explicitly-deferred", self.flat)

    def test_the_cold_read_is_dispatched_at_most_once_per_phase(self):
        # Once per PHASE, not per run: a session that continues to the next phase in-session
        # (references/phase-continuation.md) would otherwise skip every later phase's cold read.
        self.assertIn("the cold read has **not** run on this phase", self.flat)
        self.assertIn("Settled and it **has** → S5.1 is done; go to S5.2", self.flat)
        self.assertIn("never dispatched twice for one phase", self.flat)
        self.assertNotIn("run this run", self.flat)
        self.assertNotIn("twice in one run", self.flat)

    def test_terminating_guard_rail_answers_leave_the_loop(self):
        for text in (self.flat, " ".join(self.reference.read_text(encoding="utf-8").split())):
            self.assertIn("Re-plan", text)
            self.assertIn("Restructure", text)
        self.assertIn("leaves S5.1 immediately", self.flat)
        self.assertIn(
            "ends the round *and* S5.1 on the spot",
            " ".join(self.reference.read_text(encoding="utf-8").split()),
        )

    def test_settled_buckets_do_not_count_as_addressed(self):
        # A verdict whose every item is Plan-settled / Deferred-by-plan addresses nothing, so the
        # loop settles instead of spinning an unchanged PR.
        self.assertIn(
            "a non-approving verdict whose every item classified as Explicitly-deferred (registered), "
            "Plan-settled, Deferred-by-plan, Refuted, or polish",
            self.flat,
        )
        text = " ".join(self.reference.read_text(encoding="utf-8").split())
        self.assertIn("refuted-items list", text)
        self.assertIn("Settled (not addressed):", text)
        # The citation requirement is what stops the bucket dismissing genuine findings.
        self.assertIn("No citation → the item is not plan-settled", text)
        # A refuted repeat re-settles silently; the deadlock card is for ADDRESSED repeats only.
        self.assertIn("re-settled silently on its second occurrence: no card, no second reply", text)
        # Deferred-by-plan files nothing (Explicitly-deferred is the bucket that files).
        self.assertIn("file nothing", text)

    def test_the_fix_round_plans_its_edits_as_a_set_before_the_first_one(self):
        """4.16.0: step 4 lists every intended change and checks the set before editing.

        A real run had three of eleven findings introduced by the loop's own fixes: step 4 was a
        single imperative, so a literal reader started editing item one with no view of how the
        fixes interact or whether one reverses a locked plan decision.
        """
        raw = self.reference.read_text(encoding="utf-8")
        reference = " ".join(raw.split())
        pitfalls = " ".join(
            (REFERENCES_DIR / "common-pitfalls.md").read_text(encoding="utf-8").split()
        )
        self.assertIn(
            "Before the first edit, list the intended change of every item you will fix",
            reference,
        )
        self.assertIn("check the list as a set, and only then edit", reference)
        # A fix that undoes a locked decision re-enters step 2's card; it is never edited in.
        self.assertIn("reclassify it Decision-required and render step 2's `Decision` card", reference)
        # The three descriptions of the round name the beat the same way.
        for text, where in ((self.flat, "spine"), (reference, "reference"), (pitfalls, "pitfalls")):
            self.assertIn("fix plan", text, "%s must name the fix plan beat" % where)
        # The beat is prose, so the turn-boundary rule must not read as forbidding it.
        self.assertNotIn("operational tool calls", self.flat)
        self.assertIn("the classification, the fix plan, the edits, the gate", self.flat)
        # Steps 5-9 are cross-referenced by number from the spine and the pitfalls: no renumbering.
        steps = re.findall(r"^\d+\. \*\*", raw, flags=re.MULTILINE)
        self.assertEqual(len(steps), 9, "review-fix-round.md must keep exactly steps 1-9")

    def test_a_hot_seam_gets_a_fix_design_before_the_edit(self):
        """4.18.0: a real run had 7 of 24 loop items created by the loop's own fixes, five on one
        seam where each fix changed a guard without re-deriving its siblings. A fix round whose
        findings land on a file the loop already changed (or whose fix plan fails its set check)
        dispatches a context-blind fix-design sub-agent before the first edit; main still edits.
        """
        raw = self.reference.read_text(encoding="utf-8")
        reference = " ".join(raw.split())
        prompt = " ".join(
            (REFERENCES_DIR / "fix-design-prompt.md").read_text(encoding="utf-8").split()
        )
        # The trigger is file-level against what the loop itself changed, scoped by the loop-entry SHA.
        self.assertIn("**Loop-entry SHA**", reference)
        self.assertIn("git diff --name-only <loop-entry sha>...HEAD", reference)
        self.assertIn("never gates the cold read", reference)
        self.assertIn("**Fix design on a hot seam.**", reference)
        self.assertIn("fix-design-prompt.md", reference)
        self.assertIn("At most once per round", reference)
        self.assertIn("The sub-agent designs; you edit.", reference)
        # A design that reverses a locked plan decision re-enters the existing Decision card.
        self.assertIn("a `## Plan conflicts` entry is Decision-required", reference)
        self.assertIn("fix-design", self.flat)
        self.assertIn("**loop-entry SHA**", self.flat)
        # The prompt is unanchored: no loop history, surfaces only, and it never edits.
        self.assertIn("Do not include the review-loop history", prompt)
        self.assertIn("Do not edit anything.", prompt)
        for placeholder in (
            "<<workspace_path>>",
            "<<diff_path>>",
            "<<loop_files>>",
            "<<findings>>",
            "<<addressed_items>>",
            "<<plan_decisions>>",
            "<<phase_context>>",
            "<<plan_path>>",
            "<<plan_shipped_path>>",
            "<<issue_body_path>>",
        ):
            self.assertIn(placeholder, prompt)
        # The fix round fills every placeholder it owns (workspace and diff follow the cold read's).
        for placeholder in (
            "<<loop_files>>", "<<findings>>", "<<addressed_items>>", "<<plan_decisions>>",
            "<<plan_path>>", "<<plan_shipped_path>>", "<<issue_body_path>>",
        ):
            self.assertIn(placeholder, reference)
        for section in (
            "## Seam", "## Change set", "## Plan conflicts", "## Needs a plan decision", "## Out of reach",
        ):
            self.assertIn(section, prompt)
        # The sub-agent sees the plan's scope and the Definition of done, and may not pick an intent
        # the plan never stated: scope creep and an open intent are a plan decision, not a design.
        self.assertIn("5. **Check scope and intent.**", prompt)
        self.assertIn(
            "falls outside the code the scope diff touches or its sibling sites (the same change at a site in "
            "the same file or seam), the plan's `## Changes` / phase `ships`, **and** the Definition of done",
            prompt,
        )
        self.assertIn("Do not pick an intent the plan never stated", prompt)
        self.assertIn("An ambiguous intent is not out of reach — it is `## Needs a plan decision`", prompt)
        self.assertIn("`<<plan_path>>` ← `plan_marker_path`", reference)
        self.assertIn("a `## Needs a plan decision` entry is Decision-required too, never adopted as a fix", reference)
        # PR #63 review: scope creep is follow-up work, so an out-of-scope entry recommends filing it.
        self.assertIn("**File as follow-up**, recommended (scope creep is follow-up work", reference)
        self.assertIn("when the plan leaves the **intent open**, **Re-plan** is recommended", reference)
        self.assertIn("never adopted as a fix", reference)
        self.assertIn("in `## Needs a plan decision` with an open intent → the `Settled item` card", reference)


class PhaseScopedReviewTests(unittest.TestCase):
    """4.13.0: a non-final phase reviews its own delta at `medium`; the final phase reviews the
    cumulative diff at `high`; the outer cap has numbers; the cold read is once per PHASE, recorded on
    the PR so a re-entered session does not re-run it (#448: one session re-entered three times ran
    three cold reads under the once-per-run rule).
    """

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.flat = " ".join(self.spine.split())
        self.cold_read = " ".join(
            (REFERENCES_DIR / "cold-read-audit-prompt.md").read_text(encoding="utf-8").split()
        )

    def test_the_scope_rule_keeps_the_cumulative_diff_on_the_final_phase(self):
        self.assertIn("Final → `code-review`'s target is the PR's **cumulative diff**", self.flat)
        self.assertIn("Non-final → the **phase delta**", self.flat)
        self.assertIn("`facts.tracker.last_shipped", self.flat)
        # The fallback when nothing shipped or the SHA was force-pushed away.
        self.assertIn("else `facts.workspace.base_ref`", self.flat)

    def test_the_effort_level_is_passed_per_scope(self):
        # #97: the level comes from the plan's per-phase blast radius plus the runtime raise, not from
        # the phase's position.
        self.assertNotIn("level `medium` on a non-final phase, `high` on the final one", self.flat)
        self.assertIn(
            "`<level>` is the `level` of [`review-level.md`](../references/review-level.md)'s loop-entry call,"
            " fixed for the pass",
            self.flat,
        )
        self.assertIn('Skill(skill="code-review", args=', self.flat)

    def test_the_review_level_rule(self):
        ref = " ".join((REFERENCES_DIR / "review-level.md").read_text(encoding="utf-8").split())
        for phrase in (
            "**No valid line means `high`.**",
            "**The diff can raise it one step, capped at `xhigh`.**",
            "**The final cumulative pass starts from the highest level any phase declared**",
            "**Light re-reviews stay `medium`.**",
            "The level is never raised to `max` automatically",
            "A line or file count measures volume, not cost, so it sets nothing.",
            "`## Changes (file-level)` or `## Test plan`",
            "the rule fixes the level, not the depth",
            "${CLAUDE_PLUGIN_ROOT}/scripts/blast_radius.py assess",
            "`--final --base-ref <facts.workspace.base_ref>`",
            "`--since <sha>`",
            "Review level: phase <N> · <level> — <why>",
            "**`## Risk`**",
            "**`## How to revert`**",
            "Each later phase rewrites both at S5.2",
            "The epic integration PR carries neither",
            "Stage it right after the §2 call, at loop entry.",
            "replace its `Review level:` line with this pass's",
            "a span containing `*`, `?` or `[` names nothing",
        ):
            self.assertIn(phrase, ref, phrase)

    def test_the_pr_body_carries_risk_and_revert(self):
        self.assertIn("+ `## Risk` and `## How to revert` ([`review-level.md`](../references/review-level.md) §4)", self.flat)
        self.assertIn("and on every later phase, to refresh `## Risk` / `## How to revert`", self.flat)

    def test_the_record_names_the_review_level_line(self):
        record = " ".join((REPO_ROOT / "skills" / "_shared" / "review-loop-record.md").read_text(encoding="utf-8").split())
        self.assertIn("Review level: phase <N> · <medium|high|xhigh> — <why>", record)
        self.assertIn("The reader skips this line too", record)
        self.assertIn("written at loop entry from that pass's own `blast_radius.py assess` call", record)
        self.assertIn("The pass's loop entry opens `loop-comment.md` with the marker, the phase heading and its `Review level:` line", record)

    def test_the_fixed_cap_is_retired_for_the_progress_rule(self):
        # 4.21.0: the 2/4 cap could not tell a round of nits from a round of new bugs. Non-final
        # phases run the same progress rule; the post-cold-read round is no longer a special
        # classify-only round — its findings go through step 3 like any other.
        self.assertNotIn("**2** times on a non-final phase", self.flat)
        self.assertNotIn("That round classifies but does not fix", self.flat)
        self.assertIn("then continue at step 1 under step 3's rules", self.flat)

    def test_the_revision_run_is_defined_when_every_phase_shipped(self):
        # S4's cursor ("the first unticked phase") selects nothing once every code-shipping phase is
        # ticked — exactly where an evaluator soft-reject lands. The revision run fills the gap.
        for phrase in (
            "**Revision run** (`facts.revision.active`)",
            "`dod_rejected` in `facts.revision.reasons` → re-route to `/github-pipeline:planner revise #<issue>` before any code",
            "S5.1 runs at the **final** scope",
            "There is no nothing-to-do shortcut",
            "A revision run (S4) is final.",
            "or a revision run (S4): flip the PR draft → ready",
            "except at the last-phase handoff or a revision run's end",
            "Then update the PR's `## Phase tracker` with `edit-pr-body`",
            "Restage from the body this run last wrote",
        ):
            self.assertIn(phrase, self.flat)
        router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.assertIn("`revision` (`active` + `reasons`: the S4 revision run)", router)

    def test_review_fixes_from_pr_55(self):
        # Finding 1: a trailing operator/decision-only phase must not steal "final" from the last
        # code-shipping phase, or the PR ships with no cumulative pass at all.
        self.assertIn("last unshipped `kind: code-shipping` entry of `facts.phases`", self.flat)
        # Finding 4: the base can be a branch name (base_ref fallback), so the range is three-dot.
        # 4.19.0: the range is mandatory on every phase (no brackets) — an omitted range reads the stale PR.
        self.assertIn('args="<level> <base>...HEAD <context>"', self.flat)
        self.assertNotIn("<base>..HEAD", self.flat)
        self.assertIn("the fix round's settled buckets are the floor", self.flat)
        # Finding 6: last_shipped is computed before S4 can un-tick a row.
        self.assertIn("after an S4 **Rebuild un-ticked**, treat `last_shipped` as unreachable", self.flat)
        # Finding 3: the skip compares the recorded sha to HEAD; an ancestor re-bases the read.
        self.assertIn("whose `<sha>` **is** HEAD", self.flat)
        self.assertIn("run the cold read with `<base>` = that sha", self.flat)
        # Finding 9: the single-phase record has a defined phase number.
        self.assertIn("`1` for a single-phase issue", self.flat)
        # Finding 7: the plan-section token sits on one line.
        self.assertIn("`## Deviations from project docs`", self.spine)
        # Below-cap: context assembled once.
        self.assertIn("Assemble `<context>` once at loop entry", self.flat)
        reference = " ".join((REFERENCES_DIR / "review-fix-round.md").read_text(encoding="utf-8").split())
        # Finding 2: a seeded deferral expires when its phase comes due.
        self.assertIn("**except** a `deferred-by-plan` entry whose phase is the current phase", reference)
        # Below-cap: a refuted repeat has a bounded escape — now the settled-item pre-check and card,
        # not the addressed-item `Review loop` card (the #959 run: that card offered no Re-plan).
        self.assertIn("the third occurrence in one run", reference)
        self.assertIn("then the `Settled item` card only if that check leaves it standing", reference)
        pitfalls = " ".join((REFERENCES_DIR / "common-pitfalls.md").read_text(encoding="utf-8").split())
        # Finding 5: the turn-boundary pitfall cites step 4 instead of restating a stale mechanism.
        self.assertNotIn("staging the cumulative diff", pitfalls)
        self.assertIn("staging the scope diff and dispatching the cold read per S5.1 step 4", pitfalls)

    def test_verification_does_not_scale_with_scope(self):
        self.assertIn(
            "the §8 / §10.6 gates and defect injection run at full strength on every phase", self.flat
        )

    def test_the_cold_read_is_once_per_phase_and_recorded_on_the_pr(self):
        self.assertIn("Cold read: phase <N> @ <sha>", self.flat)
        self.assertIn("skip it, S5.1 is done", self.flat)
        self.assertIn("<<phase_context>>", self.flat)
        self.assertIn("<<phase_context>>", self.cold_read)
        self.assertIn("deferred to phase <N>", self.cold_read)

    def test_the_prep_fact_exists(self):
        # The fact the spine names must be a real fact.
        self.assertTrue(callable(prep_resolver.last_shipped_phase))
        self.assertIsNone(prep_resolver._absent_tracker()["last_shipped"])

    def test_the_retired_sub_agent_and_its_convergence_machinery_are_gone(self):
        for path in sorted(SKILL_DIR.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            for token in (
                "review-loop-sub-agent",
                "finding_provenance",
                "max_severity",
                "prior_decisions",
                "prior_audit_findings",
                "review-verdict.md",
            ):
                self.assertNotIn(
                    token,
                    text,
                    "%s still names the retired fix sub-agent contract %r" % (path.name, token),
                )

class ChangesLinkHandoffTests(unittest.TestCase):
    """The resolver pushes once per phase (plus one commit per review-loop iteration), so its handoff
    is the operator's entry point for reviewing what the run shipped. The `Changes:` line carries that
    link. It is defined in _shared/handoff-format.md (schema fence + omission rule) and only RENDERED
    here, per CLAUDE.md's render-don't-restate rule. Two URL forms, both verified live against a real
    PR: `<pr-url>/files/<a>..<b>` resolves only when the left SHA is a commit IN the PR (continue
    mode); the PR's base commit 404s, so a fresh run — where the whole PR is the run's changes — links
    the plain `<pr-url>/files`. These tests pin the definition, the per-shape presence/absence, and the
    two link forms."""

    RENDERINGS = REFERENCES_DIR / "handoff-renderings.md"
    HANDOFF_FORMAT = REPO_ROOT / "skills" / "_shared" / "handoff-format.md"
    SPINE = PLAYBOOKS_DIR / "resolve-spine.md"

    # Section heading -> does that shape's run push commits?
    SHAPES_WITH_PUSH = (
        "## Forward — standard or story PR opened / updated",
        "## Re-route — multi-phase, non-final code phase pushed",
        "## Terminal-with-action — multi-phase, next phase is operator / decision-only",
        "## Forward — multi-phase, last planned phase shipped",
        "## In progress — Epic integration draft PR open (stories remain)",
        "## Forward — Epic integration PR",
        "## Re-route → planner",
    )
    SHAPES_WITHOUT_PUSH = (
        "## Re-route → drafter (fitness audit)",
        "## Re-route → drafter (doc conflict)",
        "## Terminal — non-PR resolution",
        "## Terminal — question-type issue",
    )

    def _sections(self):
        """Split the reference into its `## ` shapes. Headings INSIDE a fence don't count — every
        worked shape opens with a fenced `## Handoff` line of its own."""
        lines = self.RENDERINGS.read_text(encoding="utf-8").splitlines()
        out, head, buf, in_fence = {}, None, [], False
        for line in lines:
            if line.startswith("```"):
                in_fence = not in_fence
            elif not in_fence and line.startswith("## "):
                if head is not None:
                    out[head] = "\n".join(buf)
                head, buf = line.strip(), []
                continue
            buf.append(line)
        if head is not None:
            out[head] = "\n".join(buf)
        return out

    def test_shared_owns_the_definition(self):
        text = self.HANDOFF_FORMAT.read_text(encoding="utf-8")
        self.assertIn(
            "**Changes:**", text,
            "_shared/handoff-format.md's schema fence must carry the Changes: line",
        )
        self.assertIn(
            "- **`Changes:`**", text,
            "_shared/handoff-format.md must carry the Changes: omission rule",
        )
        rule = text.split("- **`Changes:`**", 1)[1].split("\n", 1)[0]
        self.assertIn(
            "resolver-only", rule,
            "the Changes: rule must scope the line to the resolver, as Cleanup: is to the evaluator",
        )
        self.assertIn(
            "not a state marker", rule,
            "the Changes: payload is free-form text — it must be excluded from the closed sets",
        )
        # Free-form payload => no closed-set table row (the table rows are `| Field | Values |`).
        self.assertNotIn(
            "| Issue `changes`", text,
            "Changes: must not gain a closed-set vocabulary row",
        )

    def test_every_pushing_shape_carries_the_line(self):
        sections = self._sections()
        for head in self.SHAPES_WITH_PUSH:
            self.assertIn(head, sections, "handoff-renderings.md must carry the %r shape" % head)
            self.assertIn(
                "**Changes:**", sections[head],
                "%r pushed commits — its shape must render the Changes: review link" % head,
            )

    def test_no_push_shapes_omit_the_line(self):
        sections = self._sections()
        for head in self.SHAPES_WITHOUT_PUSH:
            self.assertIn(head, sections, "handoff-renderings.md must carry the %r shape" % head)
            self.assertNotIn(
                "**Changes:**", sections[head],
                "%r opened no PR and pushed nothing — a Changes: link would be a dangling URL" % head,
            )

    def test_rendered_links_use_a_verified_url_form(self):
        text = self.RENDERINGS.read_text(encoding="utf-8")
        rendered = [ln for ln in text.splitlines() if ln.startswith("**Changes:**")]
        self.assertTrue(rendered, "no Changes: line is actually rendered in a worked shape")
        range_form = re.compile(r"/pull/\d+/files/[0-9a-f]{7}\.\.[0-9a-f]{7}$")
        whole_form = re.compile(r"/pull/\d+/files$")
        for line in rendered:
            url = line.rsplit(" ", 1)[-1]
            self.assertTrue(
                range_form.search(url) or whole_form.search(url),
                "%r is neither verified form: <pr-url>/files/<a>..<b> (continue mode) nor "
                "<pr-url>/files (fresh run / post-rebase). A compare/ link or a bare PR url "
                "does not land the reader on the PR's reviewable diff." % url,
            )
            if range_form.search(url):
                lo, hi = url.rsplit("/", 1)[-1].split("..")
                self.assertIn(
                    "%s..%s" % (lo, hi), line,
                    "the rendered SHA range and the link's range must agree",
                )

    def test_spine_names_the_range_source(self):
        text = self.SPINE.read_text(encoding="utf-8")
        self.assertIn(
            "facts.workspace.sha", text,
            "the spine must name the session-entry SHA as the range's left side",
        )
        self.assertIn(
            "after** the review loop settles", text,
            "the spine must place the HEAD read after the loop settles — its fix rounds add commits, "
            "so an earlier read names a range that stops short of what the run shipped. (4.11.0 "
            "retired the review-loop sub-agent, so there is no final_pushed_sha to warn off any more.)",
        )


class PushOnceAfterSettleTests(unittest.TestCase):
    """4.19.0: the review loop commits locally and the phase pushes ONCE, at S5.2, after S5.1 exits.

    Every fix round used to push, so one phase started CI up to six times on code the next round was
    about to change. Deferring the push makes the PR stale during the loop, which is why every phase
    now hands `code-review` an explicit local range: a `code-review` left to find its own target reads the
    stale PR and approves the previous session's diff, with real file names in the verdict.
    """

    def setUp(self):
        self.flat = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.reference = " ".join(
            (REFERENCES_DIR / "review-fix-round.md").read_text(encoding="utf-8").split()
        )

    def test_the_spine_has_one_push_step(self):
        self.assertIn("### S5.2 — Push the phase, once", self.flat)
        self.assertIn("this is the phase's **only** push", self.flat)
        # The create-pr call lives in S5.2, after the loop, not in S5 before it.
        s52 = self.flat.index("### S5.2")
        self.assertGreater(self.flat.index("gh_persist.py create-pr"), s52)
        self.assertIn("nothing is pushed until S5.2", self.flat)

    def test_the_fix_round_commits_but_never_pushes(self):
        self.assertIn("8. **Commit. Stage the reply**", self.reference)
        self.assertNotIn("Commit. Push.", self.reference)
        self.assertIn("<facts.scratch>/loop-comment.md", self.reference)
        self.assertNotIn("commit, push, reply on the PR", self.flat)

    def test_every_phase_reviews_an_explicit_local_range(self):
        self.assertIn("`origin/<facts.workspace.base_ref>...HEAD`", self.flat)
        self.assertIn("the three-dot range on **every** phase", self.flat)
        self.assertNotIn("the three-dot range on non-final phases only", self.flat)
        self.assertNotIn("PR number as the target", self.flat)
        self.assertIn("<facts.scratch>/review-diff.patch", self.flat)

    def test_every_loop_exit_reaches_the_push(self):
        # Terminating answers push what is committed, so the remote ends where it used to.
        self.assertIn("pushes what it has committed via S5.2", self.flat)
        self.assertIn("hand back to S5.2", self.reference)
        self.assertIn("exit S5.1 as committed", self.flat)

    def test_the_resume_record_survives_an_interrupted_loop(self):
        self.assertIn("`facts.workspace.unpushed_commits` > 0", self.flat)
        # #98 cold read: the surviving file seeds the record's id counters too.
        self.assertIn(
            "seed the refuted-items list, any `Cold read:` record and the record's counters (`review-fix-round.md`) "
            "from it", self.flat)
        self.assertIn("a `BODY_TOO_LONG` decision splits it one comment per round", self.flat)
        # The settled-block format the next session parses is unchanged.
        self.assertIn("Settled (not addressed):", self.reference)


class PlanSummaryStepTests(unittest.TestCase):
    """S1.5 — the `## Plan summary` the resolver renders BEFORE it starts work.

    Two traps this pins, both of which produce a summary that quietly disagrees with what the session
    actually builds:

    1. **Source.** The digest must come from the state-distiller's `## Effective plan` (already in
       hand from S1), not the raw plan comment. The distiller reports `thread-vs-plan: confirms |
       refines`, so a thread can REFINE a plan without raising `THREAD_SUPERSEDED_PLAN` — digesting the
       posted text would show the operator the plan as posted while the resolver builds it as amended.
    2. **Cursor.** In continue mode the phase named at S1.5 is provisional; S4's tracker reconciliation
       is authoritative and may move it.
    """

    SHARED = REPO_ROOT / "skills" / "_shared" / "plan-summary.md"

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.epic = (PLAYBOOKS_DIR / "epic.md").read_text(encoding="utf-8")
        self.router = ROUTER.read_text(encoding="utf-8")

    def test_the_spine_has_the_step_and_cites_the_shared_contract(self):
        self.assertIn("## S1.5 — Plan summary", self.spine)
        self.assertIn("../../_shared/plan-summary.md", self.spine)
        self.assertTrue(self.SHARED.is_file())

    def test_the_step_is_numbered_so_no_existing_anchor_moves(self):
        # S2..S7 are cross-referenced from standard.md, story.md, SKILL.md and the references; a
        # renumbering would dangle every one of them. S1.5 is the §6.5 precedent in the planner spine.
        for anchor in ("## S2 —", "## S3 —", "## S4 —", "## S5 —", "## S6 —", "## S7 —"):
            self.assertIn(anchor, self.spine, anchor)

    def test_the_step_skips_when_no_plan_exists(self):
        flat = " ".join(self.spine.split())
        self.assertIn("Skip the whole step when `facts.plan.present` is false", flat)
        self.assertIn("S3 plan gate owns that case", flat)

    def test_the_source_is_the_distillers_effective_plan_never_a_refetch(self):
        flat = " ".join(self.spine.split())
        self.assertIn("the distiller's `## Effective plan` from S1, already in hand — never a re-fetch",
                      flat)
        self.assertIn("thread-vs-plan: confirms | refines", flat)
        self.assertIn("without raising `THREAD_SUPERSEDED_PLAN`", flat)

    def test_the_cursor_is_provisional_and_s4_is_authoritative(self):
        flat = " ".join(self.spine.split())
        # The fresh-mode cursor was never written down before this step existed.
        self.assertIn("the head phase — the one whose `depends-on` is `(none)`", flat)
        self.assertIn("S4's tracker reconciliation is authoritative", flat)
        self.assertIn("a preview, not a commitment", flat)

    def test_rendering_precedes_the_audit_dispatch(self):
        flat = " ".join(self.spine.split())
        self.assertIn("before S2 dispatches", flat)
        self.assertIn("Nothing about how the audit is dispatched changes", flat)
        self.assertLess(
            self.spine.index("## S1.5 — Plan summary"),
            self.spine.index("## S2 — Fitness-to-implement audit"),
            "S1.5 must sit before S2 or the operator reads the plan after the audit it was meant to "
            "overlap with",
        )

    def test_s1_no_longer_prints_the_effective_plan_verbatim(self):
        flat = " ".join(self.spine.split())
        self.assertNotIn("Print the distilled state.", flat)
        self.assertIn("Print `## Current state` and `## Classification`", flat)
        # §3 asks for each sub-agent's rationale; say WHY the narrowing is not a violation of it.
        self.assertIn("the rationale, not the raw output", flat)

    def test_the_epic_route_renders_it_at_integration_altitude(self):
        flat = " ".join(self.epic.split())
        self.assertIn("../../_shared/plan-summary.md", flat)
        self.assertIn("## Integration strategy", flat)
        self.assertIn("carries no `## Phases`", flat)
        self.assertLess(
            self.epic.index("## S0 — Plan summary"),
            self.epic.index("## S1 — Resolve the integration branch"),
            "the epic route must summarise before it starts moving refs",
        )

    def test_router_sanctions_the_block_and_drops_the_suppressing_phrasing(self):
        flat = " ".join(self.router.split())
        self.assertNotIn("replaces any bullet-list summary", flat)
        self.assertIn("`## Plan summary` block", flat)
        self.assertIn("../_shared/plan-summary.md", flat)
        self.assertIn("at session **start** rather than exit", flat)

    def test_the_spine_flow_line_names_the_new_step(self):
        # The intro's flow line is what a reader skims before deciding which section to read.
        self.assertIn("distill state → plan summary → audit → plan-gate →", self.spine)


class ResolverCommandArgumentTests(unittest.TestCase):
    """Every handoff that hands back to the resolver names the ISSUE, never the PR.

    `prep_resolver.py` takes one positional `issue` and no mode word: it derives continue mode from
    the issue's prior-PR state, and a PR number trips gh_gather's `TARGET_IS_PR` card — a wasted
    decision in the next session before any work starts. The bug this pins: the evaluator's
    soft-reject rows, the planner's revise route and the resolver's own notes all taught
    `/github-pipeline:resolver continue #<PR>`, so the resolver copied that form into its multi-phase
    re-entries. The rule lives once in _shared/handoff-format.md ("Authorship"); these tests scan every
    skill, because the drift was cross-skill — fixing one skill's renderings left the others teaching
    the wrong form."""

    SKILLS_ROOT = REPO_ROOT / "skills"
    HANDOFF_FORMAT = SKILLS_ROOT / "_shared" / "handoff-format.md"
    BANNED = re.compile(r"/github-pipeline:resolver\s+(?:continue\b|#<PR>)")
    # Tolerates a mode word (`continue #287`) so a PR number is caught here too, not only by the
    # banned-form scan.
    COMMAND = re.compile(r"/github-pipeline:resolver\s+(?:[a-z]+\s+)?#(\d+)")
    PR_LINE = re.compile(r"\*\*PR:\*\* #(\d+)")
    TARGET_LINE = re.compile(r"\*\*(?:Issue|Story|Epic):\*\* #(\d+)")

    def _fenced_blocks(self, path):
        """(start line, block text) for every ``` fence in `path`."""
        blocks, body, start, inside = [], [], 0, False
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                if inside:
                    blocks.append((start, "\n".join(body)))
                    body = []
                else:
                    start = i
                inside = not inside
            elif inside:
                body.append(line)
        return blocks

    def test_no_skill_hands_the_resolver_a_pr_or_a_continue_keyword(self):
        for path in sorted(self.SKILLS_ROOT.rglob("*.md")):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(
                    self.BANNED.search(line),
                    "%s:%d hands the resolver a PR / `continue` argument: %r"
                    % (path.relative_to(REPO_ROOT), i, line.strip()),
                )

    def test_every_worked_resolver_command_names_the_handoffs_issue_not_its_pr(self):
        checked = 0
        for path in sorted(self.SKILLS_ROOT.rglob("*.md")):
            for start, block in self._fenced_blocks(path):
                commands = self.COMMAND.findall(block)
                if not commands:
                    continue
                targets = set(self.TARGET_LINE.findall(block))
                prs = set(self.PR_LINE.findall(block))
                where = "%s:%d" % (path.relative_to(REPO_ROOT), start)
                for number in commands:
                    checked += 1
                    self.assertNotIn(number, prs, "%s: resolver command names the PR #%s" % (where, number))
                    self.assertIn(
                        number,
                        targets,
                        "%s: resolver command #%s is none of the block's Issue/Story/Epic numbers %s"
                        % (where, number, sorted(targets)),
                    )
        # Guard the scan itself: the worked shapes exist in three skills' renderings.
        self.assertGreaterEqual(checked, 11, "expected >= 11 worked resolver commands, found %d" % checked)

    def test_the_shared_contract_fixes_the_argument(self):
        flat = re.sub(r"\s+", " ", self.HANDOFF_FORMAT.read_text(encoding="utf-8"))
        for phrase in (
            "**the number names the target the next skill's prep consumes.**",
            "**never the PR's number and never a `continue` keyword**",
            "refused as `TARGET_IS_PR`",
        ):
            self.assertTrue(phrase in flat, "handoff-format.md Authorship must state: %r" % phrase)



class PhaseContinuationTests(unittest.TestCase):
    """A session ships phases until a checkpoint (operator decisions, 2026-10-04): the planner's
    `checkpoint:` key, a per-invocation `--pause`, and warning signs that only ever ADD a stop.

    The governing rule is that an in-session continuation is a fresh continue-mode run in everything
    but the conversation — a full prep re-run, a re-entry at S1, per-phase state seeded from GitHub —
    because the improvised "type continue" path this replaced skipped every later phase's cold read
    and ran on the first phase's prep facts.
    """

    REFERENCE = REFERENCES_DIR / "phase-continuation.md"
    HANDOFF_FORMAT = REPO_ROOT / "skills" / "_shared" / "handoff-format.md"

    def setUp(self):
        self.text = self.REFERENCE.read_text(encoding="utf-8")
        self.flat = " ".join(self.text.split())
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")

    def test_reference_exists_and_is_not_a_sub_agent_prompt(self):
        self.assertTrue(self.REFERENCE.is_file())
        for pattern in ("*-prompt*.md", "*-sub-agent*.md"):
            self.assertNotIn(self.REFERENCE, set(REFERENCES_DIR.glob(pattern)))

    def test_the_spine_return_section_reads_it(self):
        ret = self.spine.split("## Return to the routed playbook", 1)[1]
        self.assertIn("../references/phase-continuation.md", ret)
        self.assertIn("an unshipped phase remains", ret)

    def test_prep_is_re_run_in_full_never_refresh(self):
        self.assertIn("Re-run prep **in full**", self.flat)
        self.assertIn("**no `--refresh`**", self.flat)
        self.assertIn("no reachable `facts.tracker.last_shipped`", self.flat)
        self.assertIn("Re-enter the spine at **S1** in continue mode", self.flat)
        self.assertIn("starts over, seeded only from GitHub", self.flat)

    def test_a_pause_ends_the_session_with_a_summary(self):
        # A card answered hours later reloads the whole conversation only to discard it, so a pause
        # ends the session instead, with what the operator needs to review it cold.
        self.assertNotIn('header: "Checkpoint"', self.flat)
        self.assertNotIn("**Continue here**", self.flat)
        self.assertIn("A pause asks nothing: it ends the session.", self.flat)
        for section in ("**Why the session ended**", "**What changed**", "**Decisions and deviations**",
                        "**What to check**", "`None — built as planned.`"):
            self.assertIn(section, self.flat, section)
        # The card's free-text note is gone; the issue thread is how the operator steers the next phase.
        self.assertIn("a comment on the issue before the next session starts reaches it", self.flat)
        # The thread-refinement stop ends the session too, quoting the comment.
        self.assertIn("refinement and end the session before S3", self.flat)
        handoff_format = " ".join(self.HANDOFF_FORMAT.read_text(encoding="utf-8").split())
        self.assertIn("Nor is the resolver's `## Session summary`", handoff_format)

    def test_the_per_phase_link(self):
        for phrase in (
            "scoped to **this phase's** push",
            "this pass's prep `facts.workspace.upstream_sha`",
            "unless an interrupted run left commits unpushed",
            "link `<pr-url>/files`",
        ):
            self.assertIn(phrase, self.flat, phrase)

    def test_stops_only_ever_get_added(self):
        for phrase in (
            "Warning signs only turn `continue` into a pause",
            "Nothing turns a `pause` into `continue`",
            "`null` (absent",
            "the session holds `--pause`",
            "`Loop stall`",
            "`## Known failures`",
            "`thread-vs-plan: refines`",
        ):
            self.assertIn(phrase, self.flat, phrase)

    def test_hard_stops_and_staging_cleanup(self):
        self.assertIn("the operator-phase handoff", self.flat)
        self.assertIn("is a revision run", self.flat)
        self.assertIn("A surviving fresh-mode `pr.md`", self.flat)

    def test_disambiguated_from_the_follow_up_checkpoint(self):
        self.assertIn("is not [`follow-up-tracking.md`](follow-up-tracking.md)'s end-of-loop checkpoint", self.flat)
        self.assertIn("is never a reason to pause", self.flat)

    def test_router_holds_the_override_and_gates_the_card(self):
        router = ROUTER.read_text(encoding="utf-8")
        flat = " ".join(router.split())
        self.assertIn("A trailing `--pause`", flat)
        self.assertIn("never passed to prep", flat)
        # A pause ends the session; there is no checkpoint card to gate (phase-continuation.md §4).
        self.assertNotIn("checkpoint card", flat)
        fence = router.split("```bash", 1)[1].split("```", 1)[0]
        self.assertNotIn("--pause", fence, "prep's argparse rejects --pause; the router holds it")

    def test_handoff_contract_carries_session_scope_and_a_trailing_pause(self):
        flat = " ".join(self.HANDOFF_FORMAT.read_text(encoding="utf-8").split())
        self.assertIn("the clickable review link for *what this session pushed*", flat)
        self.assertIn("names each phase's range ahead of the link", flat)
        self.assertIn("may also carry a **trailing** `--pause`", flat)
        self.assertIn("Never a leading flag", flat)
        # The trailing form still yields the issue number to the command scan.
        self.assertEqual(
            re.findall(r"/github-pipeline:resolver\s+(?:[a-z]+\s+)?#(\d+)", "/github-pipeline:resolver #640 --pause"),
            ["640"],
        )

    def test_renderings_anchor_changes_on_the_first_prep(self):
        flat = " ".join((REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8").split())
        self.assertIn("from `facts.workspace.upstream_sha` as the session's **first** prep reported it", flat)
        self.assertIn("each phase's entry being the `upstream_sha` of the prep that started its pass", flat)
        self.assertIn("`/github-pipeline:resolver #<N> --pause`", flat)
        self.assertIn("typically at a phase checkpoint pause", flat)
        spine = " ".join(self.spine.split())
        self.assertIn("`facts.workspace.upstream_sha` as the session's **first** prep reported it", spine)
        self.assertIn("its `facts.workspace.sha` unless an interrupted run left commits unpushed", spine)

    def test_refines_pauses_only_on_new_thread_evidence(self):
        # `thread-vs-plan` is a whole-thread judgment: without the comment-count delta a refinement
        # the thread carried at session start re-fires the card on every pass.
        self.assertIn("`facts.sections.thread_comment_count` with the previous prep's", self.flat)
        self.assertIn("citing one of those new comments", self.flat)
        self.assertIn("never pauses a later pass", self.flat)

    def test_restage_source_is_the_staged_pr_body(self):
        self.assertIn("restages from `facts.tracker.body_path`", self.flat)

    def test_a_trailing_operator_phase_stops_without_the_ready_flip(self):
        self.assertIn("mid-plan, or trailing the last code phase", self.flat)
        self.assertIn("the PR stays draft: the ready flip waits for every phase to be ticked", self.flat)
        self.assertNotIn("The last code phase never reaches this file", self.flat)
        spine = " ".join(self.spine.split())
        self.assertIn("while the ready flip still waits for every phase", spine)
        self.assertNotIn("is the one whose shipping flips the PR ready", spine)

    def test_the_handoff_ends_the_session_never_a_pass(self):
        router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.assertIn("One `## Handoff` block ends every clean session", router)
        self.assertIn("Every clean session ends with a single `## Handoff` block", router)
        self.assertNotIn("every clean run", router.lower())
        self.assertIn("ends the **session** (SKILL.md §4), never a pass", self.flat)
        self.assertIn('scope a rule to "this run" or "one run", it means this pass', self.flat)

    def test_hooks_re_run_is_a_recorded_decision(self):
        self.assertIn("The setup hooks re-run, deliberately: a phase can change what setup installs", self.flat)

    def test_the_session_end_points_at_the_renderings_rather_than_restating(self):
        end = self.text.split("## 6. Ending the session", 1)[1]
        self.assertIn("[`handoff-renderings.md`](handoff-renderings.md)", end)
        self.assertNotIn("<pr-url>", end)

    def test_the_non_final_shape_covers_every_session_end_after_a_non_final_phase(self):
        flat = " ".join((REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8").split())
        self.assertIn("or on any other exit that pushed a non-final phase (an `Abort` / `Abort loop` answer)", flat)
        standard = " ".join((PLAYBOOKS_DIR / "standard.md").read_text(encoding="utf-8").split())
        self.assertIn("or an `Abort` after S5.2 pushed", standard)

    def test_a_guard_rail_answer_settles_for_the_phase(self):
        rfr = " ".join((REFERENCES_DIR / "review-fix-round.md").read_text(encoding="utf-8").split())
        self.assertIn("An answer settles that gate for the phase", rfr)


class FinalisationTests(unittest.TestCase):
    """The operator triages the `## Polish` ledger on the resolver's final pass, before the push
    (operator decisions, 2026-10-05). Applying polish from the evaluator cost a soft-reject, a whole
    revision session and a second evaluation, while the operator usually wanted it applied; here it
    costs one fix round. The fixes take one light re-review, never a second finalisation, and the
    evaluator's S5.5 stays the backstop for whatever is still `open`.
    """

    REFERENCE = REFERENCES_DIR / "finalisation.md"
    SHARED = REPO_ROOT / "skills" / "_shared"

    def setUp(self):
        self.text = self.REFERENCE.read_text(encoding="utf-8")
        self.flat = " ".join(self.text.split())
        self.spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())

    def test_reference_exists_and_is_not_a_sub_agent_prompt(self):
        self.assertTrue(self.REFERENCE.is_file())
        for pattern in ("*-prompt*.md", "*-sub-agent*.md"):
            self.assertNotIn(self.REFERENCE, set(REFERENCES_DIR.glob(pattern)))

    def test_the_spine_runs_it_before_the_one_push_on_the_final_pass(self):
        push = self.spine.split("### S5.2 — Push the phase, once", 1)[1].split("## S6 —", 1)[0]
        self.assertIn("[`../references/finalisation.md`](../references/finalisation.md)", push)
        self.assertIn("On the **final** pass (S5.1 \"Scope\"), a settle or **Accept current** exit first runs "
                      "**finalisation** once", push)
        # Before the push, so the #60 one-push rule still holds.
        self.assertLess(push.index("finalisation.md"), push.index("git -C"))
        self.assertIn("this is the phase's **only** push", push)
        # PR #71 review: "an `open` entry per item … and finalisation's answers" read as two lines per item.
        self.assertIn("gains one entry per polish item the loop left — `open`, `drop` if no shown cost, or finalisation's answer — and "
                      "`applied (commit <sha>)`", push)
        self.assertNotIn("gains an `open` entry per polish item the loop left", push)
        ret = self.spine.split("## Return to the routed playbook", 1)[1]
        self.assertIn("a finalisation Re-plan → planner", ret)
        # PR #71 review: the re-route exit is read before the ready flip, so a Re-plan never flips the PR.
        self.assertIn("skip straight to the routed playbook's re-route handoff — no ready flip", ret)
        self.assertLess(ret.index("On a re-route exit"), ret.index("gh pr ready"))

    def test_the_security_pass_runs_before_the_triage(self):
        # #95 phase 2: the resolver runs the evaluator's security prompt at finalisation, before the
        # polish triage, so a finding costs one fix round, not a revision session.
        prompt_link = "[`../../evaluator/references/security-review-prompt.md`](../../evaluator/references/security-review-prompt.md)"
        self.assertIn(prompt_link, self.flat)
        self.assertTrue((REFERENCES_DIR / "../../evaluator/references/security-review-prompt.md").resolve().is_file())
        self.assertIn("[`../../_shared/security-review.md`](../../_shared/security-review.md)", self.flat)
        self.assertLess(self.text.index("## 1.5. The security pass"), self.text.index("## 2. The entries"))
        self.assertLess(self.text.index(prompt_link), self.text.index("## 3. Triage"))
        # review-fix-round.md cites "3. Triage" by number: the new section renumbers nothing.
        for heading in ("## 1. When it runs", "## 2. The entries", "## 3. Triage", "## 4. Apply",
                        "## 5. Re-plan", "## 6. The record"):
            self.assertIn(heading, self.text)
        self.assertIn("each security finding a defect-tier item", self.flat)
        self.assertIn("The round fixed a reclassified defect or a security finding", self.flat)
        self.assertIn("asked before the `Polish` card", self.flat)
        # Round 1 review: the fix round never dismisses a finding, and an unfixed one keeps a disposition.
        self.assertIn("§4's round never settles a security finding as Refuted, Plan-settled or of no shown cost", self.flat)
        self.assertIn("no round has fixed by the push — a terminating guard-rail answer, a revert — is recorded `open`", self.flat)
        shared = " ".join((self.SHARED / "security-review.md").read_text(encoding="utf-8").split())
        self.assertIn("or for a finding sent to fix that no round fixed by its push |", shared)
        # Cold read: a finding the round itself finds false at head has a defined card point, and the
        # carve-out lives where classification happens.
        self.assertIn("renders its own `Security` card within the round, before its commit, as `premise false`", self.flat)
        fix_round = " ".join((REFERENCES_DIR / "review-fix-round.md").read_text(encoding="utf-8").split())
        self.assertIn("never takes the Refuted, Plan-settled or No shown cost bucket and never reaches step 4's "
                      "dismissal check", fix_round)
        # §1's "no loop-comment line" is the polish triage's; the security pass still records itself.
        self.assertIn("This is the polish triage's rule: the security pass (§1.5) records itself at §6", self.flat)
        # Reviewed: is the HEAD the pass was dispatched at, never a later fix commit.
        self.assertIn("the pass SHA is that HEAD, before any commit §4's round adds", self.flat)
        # PR #106 Follow-ups note: S5.2's pinned trigger list names no security change.
        self.assertIn("a `Reviewed:`-only change included, triggers S5.2's `edit-pr-body` by itself", self.flat)
        self.assertIn("`Security review: @ <short pass sha> — fixed <ids>; dismissed <ids>; open <ids>`", self.flat)
        push = self.spine.split("### S5.2 — Push the phase, once", 1)[1].split("## S6 —", 1)[0]
        self.assertLess(push.index("the security pass, then the polish triage"), push.index("finalisation.md"))
        router = " ".join((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("the finalisation `Polish` card and `Security` card", router)
        self.assertIn("cold-read audit, security review) never calls `AskUserQuestion`", router)
        renderings = " ".join((REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8").split())
        self.assertIn("each entry left `open` — by an **Accept current** exit, or a fix no round landed", renderings)

    def test_the_loop_rules_cover_finalisations_round(self):
        # PR #71 review: Reset named only the light re-review and the cold read; and the ceiling counted
        # finalisation's light re-review, re-firing the ceiling card after an Accept current at the ceiling.
        self.assertIn("when a light re-review, the cold read or finalisation's round reopens a settled loop", self.spine)
        self.assertIn("light re-reviews and the post-cold-read run included (finalisation's light re-review "
                      "excepted)", self.spine)
        self.assertIn("This light re-review is outside S5.1's emergency-ceiling count", self.flat)
        self.assertIn("after a **settle** exit, re-enters the loop at step 2 under step 3's **Reset** rule", self.flat)

    def test_when_it_runs_and_when_it_skips(self):
        self.assertIn("A terminating guard-rail answer (**Re-plan**, **Restructure**, **Abort**, **Abort loop**) "
                      "skips it", self.flat)
        self.assertIn("**Nothing to triage → no card**, and no loop-comment line", self.flat)
        self.assertIn("Print only the digest's dropped list, when it has entries", self.flat)
        self.assertNotIn("skip it silently", self.flat)
        # PR #76 review: a no-shown-cost item the light re-review finds is `drop`, not `open`.
        self.assertIn("except a no-shown-cost item, which goes in as `drop` like any other", self.flat)
        self.assertIn("go straight to the push — never a second finalisation", self.flat)
        self.assertIn("A non-final phase's leftovers wait", self.flat)

    def test_it_triages_through_the_shared_procedure(self):
        self.assertIn("[`../../_shared/polish-triage.md`](../../_shared/polish-triage.md)", self.flat)
        self.assertIn("every `facts.polish` entry marked `open`", self.flat)
        self.assertIn("An `apply` entry is not triaged", self.flat)
        # PR #71 review: a leave-open answer is recorded, so a later finalisation never re-asks it.
        self.assertIn("they stay `open` with the note `operator: leave for evaluator`, so no later finalisation "
                      "asks again", self.flat)
        self.assertIn("Skip an `open` entry whose note is `operator: leave for evaluator`", self.flat)
        # PR #71 review: an unparsed ledger line is triaged, never skipped.
        self.assertIn("every line in `facts.polish.unparsed`", self.flat)
        self.assertIn("never silently dropped", self.flat)
        # The card's options live in the shared file, never restated here.
        self.assertNotIn("- **File as follow-up**", self.flat)
        triage = " ".join((self.SHARED / "polish-triage.md").read_text(encoding="utf-8").split())
        self.assertIn("- **Resolver finalisation** — the entry is in this PR's scope", triage)
        self.assertIn("not against the reason the loop left it", triage)

    def test_applied_entries_take_one_light_re_review_never_a_second_card(self):
        self.assertIn("run one fix round, S5.1 step 2", self.flat)
        self.assertIn("step 3's one **light re-review** (`code-review` at `medium` over the round's commit)", self.flat)
        self.assertIn("Polish it finds goes to the ledger `open`, unfixed — for the evaluator, never back to "
                      "this card", self.flat)
        self.assertIn("Any round after it counts as usual", self.flat)
        self.assertIn("`reclassified: defect — see loop comment`", self.flat)

    def test_a_re_plan_re_routes_and_file_entries_wait_for_the_merge(self):
        self.assertIn("`/github-pipeline:planner revise #<issue>` — no ready flip", self.flat)
        self.assertIn("A **Re-plan** answer records `apply` with the note prefixed `operator: re-plan —`", self.flat)
        self.assertIn("doubt means coupled", self.flat)
        self.assertIn("`file` entries are filed after the merge by the evaluator's residual step, never here",
                      self.flat)

    def test_accept_current_never_reopens_the_loop(self):
        # PR #71 review: after Accept current (at the ceiling or not), finalisation's light re-review
        # finding a defect reverts the round rather than reopening a loop the operator closed.
        self.assertIn("after an **Accept current** exit, does **not** reopen the loop the operator closed", self.flat)
        self.assertIn('git -C "<facts.workspace.path>" revert --no-edit <round sha>', self.flat)
        self.assertIn("each note gaining `reverted: <the defect, one line>`", self.flat)
        self.assertIn("in which case it becomes a follow-up, as that answer's still-open defects do", self.flat)
        self.assertNotIn("reset --hard", self.flat)

    def test_coupled_apply_entries_have_a_record(self):
        # PR #71 review: an Apply waiting on a coupled re-plan had no slot in the record or the loop comment.
        self.assertIn("its note gaining `waits on <re-plan id>`", self.flat)
        self.assertIn("`apply` (a re-plan, an entry that `waits on` one, or an Accept-current revert)", self.flat)
        self.assertIn("re-plan <ids>; apply waiting <ids>; reverted <ids>; left open <ids>", self.flat)

    def test_no_positional_cross_references(self):
        # CLAUDE.md "Stable §-anchors over positional cross-references".
        self.assertNotRegex(self.flat, r"\((?:above|below)\)|`[^`]+` (?:above|below)\b")

    def test_the_contracts_credit_finalisation(self):
        router = " ".join((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("the loop-stall card, the finalisation `Polish` card", router)
        ledger = " ".join((self.SHARED / "polish-ledger.md").read_text(encoding="utf-8").split())
        self.assertIn("| `file` | the operator's answer — resolver (finalisation) or evaluator |", ledger)
        self.assertIn("an `apply` item fixed — at finalisation or in a revision run", ledger)
        self.assertIn("**The operator decides every disposition.**", ledger)
        handoff = " ".join((self.SHARED / "handoff-format.md").read_text(encoding="utf-8").split())
        self.assertIn("or the operator answered **Re-plan** on a polish entry at the resolver's finalisation",
                      handoff)
        renderings = " ".join((REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8").split())
        self.assertIn("**Finalisation in the `Why:`.**", renderings)



class RubricProvenanceTests(unittest.TestCase):
    """The classification rubric is provenance-first (4.31.0, operator decisions 2026-10-06).

    An audit of 503 `follow-up` issues in one consuming repo found provenance the best predictor of the
    right call: findings a PR introduced were the ones wrongly deferred (fixable in the PR, or a plan gap),
    while pre-existing ones were correctly deferred four times in five. It also found a cost gate parking
    the PR's own polish, introduced defects leaving as "needs a design decision", standing kinds of
    deferral (gated docs, one root cause, an unverified owner) filed claim by claim, and review-deferred
    items filed mid-loop with no operator step.
    """

    def setUp(self):
        self.raw = (REFERENCES_DIR / "review-fix-round.md").read_text(encoding="utf-8")
        self.text = " ".join(self.raw.split())
        self.rubric = self.text.split("## Classification rubric", 1)[1].split("## Steps", 1)[0]

    def test_provenance_comes_first_and_has_three_values(self):
        r = self.rubric
        self.assertIn("Classify every listed item on three axes, in order: its **provenance**, its **bucket**", r)
        order = [r.index(s) for s in ("- **introduced** —", "- **adjacent** —", "- **pre-existing** —",
                                      "**Buckets:**")]
        self.assertEqual(order, sorted(order))
        self.assertIn("a test or control it wrote that cannot fail", r)
        self.assertIn("the same change this PR made applies at a **sibling site**", r)
        self.assertIn("Record provenance, bucket and tier on each item's finding line in the round reply", r)

    def test_addressable_is_introduced_or_adjacent(self):
        r = self.rubric
        self.assertIn("a concretely-named change that is **introduced** or **adjacent**", r)
        # PR #72 review: an operator's `apply` outranks provenance, or a pre-existing entry they applied
        # would bounce back to the ledger forever.
        self.assertIn("A **pre-existing** item is not Addressable", r)
        self.assertIn("except an entry the operator marked `apply`: their answer outranks provenance", r)
        self.assertIn("marked `apply` is Addressable on iteration 1 whatever its tier or provenance", self.text)
        # Fix design shares the scope test, sibling sites included.
        fix_design = " ".join((REFERENCES_DIR / "fix-design-prompt.md").read_text(encoding="utf-8").split())
        self.assertIn("the code the scope diff touches or its sibling sites", fix_design)

    def test_own_polish_is_fixed_with_five_exceptions(self):
        r = self.rubric
        self.assertIn("On **introduced** or **adjacent** code, fix it this round", r)
        self.assertIn("Five exceptions:", r)
        for exception in ("it needs a decision the plan does not make → **Decision-required**",
                          "which is not fixed as polish",
                          "it reaches beyond this PR's seam",
                          "the **docs lane** (Explicitly-deferred)",
                          "it has no shown cost — taste"):
            self.assertIn(exception, r)
        self.assertIn('"Apply criteria"', r)
        self.assertNotIn("is never cheap", r)

    def test_an_introduced_defect_never_leaves_by_the_resolvers_own_filing(self):
        r = self.rubric
        self.assertIn("An **introduced** defect is fixed in this PR: it leaves only through an operator answer", r)
        self.assertIn("never through a filing of your own", r)
        self.assertIn("states why it was not fixed", r)
        tracking = " ".join((REFERENCES_DIR / "follow-up-tracking.md").read_text(encoding="utf-8").split())
        self.assertIn("its description states why it was not fixed here", tracking)
        self.assertIn("Its **Accept current** description names each still-open defect with its provenance", self.text)

    def test_pre_existing_and_no_shown_cost_go_to_the_ledger_not_the_tracker(self):
        r = self.rubric
        self.assertIn("polish → record it for the ledger, unfixed", r)
        self.assertIn("A **security or privacy** defect", r)
        self.assertIn("is its own follow-up group, never grouped with lower-severity items", r)
        self.assertIn("When its fix is one site in a file this PR edits, fix it here, classified adjacent.", r)
        self.assertIn("- **No shown cost** —", r)
        self.assertIn("Record it for the ledger as `drop` with the note `no shown cost: <why>` — decided, so it "
                      "never reaches a `Polish` card", r)
        self.assertNotIn("which finalisation proposes as `drop`", r)
        ledger = " ".join((REPO_ROOT / "skills" / "_shared" / "polish-ledger.md").read_text(encoding="utf-8").split())
        self.assertIn("**Only what the loop chose not to fix enters**", ledger)
        self.assertNotIn("**Only unfixed polish enters.**", ledger)
        triage = " ".join((REPO_ROOT / "skills" / "_shared" / "polish-triage.md").read_text(encoding="utf-8").split())
        self.assertIn("The loop records a **no-shown-cost** finding as `drop` itself", triage)
        self.assertIn("An `open` entry whose note leads `no shown cost:` (one written before that rule) is "
                      "recorded `drop` the same way, without asking", triage)
        # The one exception to the operator deciding every disposition, and who writes it.
        self.assertIn("**The one exception is a no-shown-cost finding**", ledger)
        self.assertIn("or the resolver itself, at the S5.2 push, for a no-shown-cost finding", ledger)
        self.assertIn("The evaluator does not re-check them", ledger)
        self.assertNotIn('"not cheap"', triage)

    def test_doc_findings_pass_a_materiality_bar(self):
        # #87: bookkeeping fixes on docs bred loop-induced findings and stalled the loop phase after phase.
        r = self.rubric
        self.assertIn("**Materiality** — whether a finding on a doc change matters", r)
        self.assertIn("would the doc or comment, as it stands, mislead a builder, reviewer or auditor into a "
                      "wrong decision?", r)
        self.assertIn("It takes the **No shown cost** bucket whatever its provenance: never a defect", r)
        self.assertIn("The bar is always on: no operator switch, no test of the PR's shape", r)
        self.assertIn("A doc or comment finding is a defect only when it is **material**.", r)
        self.assertIn("the bookkeeping class: a doc or comment finding that fails the **materiality** test", r)
        self.assertIn("a finding that is not material is never a defect, so it never counts as loop-induced", r)
        self.assertIn('"for doc changes (doc files and code comments), report only material findings', r)
        ledger = " ".join((REPO_ROOT / "skills" / "_shared" / "polish-ledger.md").read_text(encoding="utf-8").split())
        self.assertIn("states something false **that would mislead a reader into a wrong decision**", ledger)
        # The spine names the bar in the reviewer's context and as a floor; it is at its line bar, so the
        # wording lives in the rubric.
        spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.assertIn("the reference's **materiality** bar for doc findings, in its words", spine)
        self.assertIn("its materiality bar too, whether or not they reached the reviewer", spine)

    def test_a_plan_defect_is_not_settled(self):
        r = self.rubric
        self.assertIn("**A plan defect is not settled.** Run the test the other way too", r)
        self.assertIn("Decision-required, **Re-plan** recommended — never settled, never filed", r)

    def test_an_introduced_decision_offers_no_deferral(self):
        self.assertIn("For an **introduced** item the card offers no deferral path", self.rubric)
        guard_rails = self.text.split("## Guard rails", 1)[1]
        self.assertIn("For an **introduced** item no deferral path is offered", guard_rails)

    def test_explicitly_deferred_carries_four_rules_and_waits_for_the_checkpoint(self):
        r = self.rubric
        for rule in ("**Verify the owner.**", "**File the class, not the instance.**",
                     "**Latent until X ships.**", "**The docs lane.**"):
            self.assertIn(rule, r)
        self.assertIn("filed mid-round only when this round's commit needs its number", r)
        step4 = self.text.split("4. **Fix plan, then fix.**", 1)[1].split("5. **No edits**", 1)[0]
        self.assertIn("urgency `file-at-checkpoint`", step4)
        self.assertNotIn("(urgency `file-now`, type per the", step4)
        tracking = " ".join((REFERENCES_DIR / "follow-up-tracking.md").read_text(encoding="utf-8").split())
        self.assertIn("| Defer-by-review (review-loop deferred items) | `file-at-checkpoint`", tracking)
        self.assertIn("## Before filing — verify the owner, search for the root cause", tracking)
        self.assertIn("gh_persist.py link <owner/repo> <follow-up> --add-blocking <X>", tracking)
        self.assertIn("gh_persist.py comment <owner/repo> issue <M>", tracking)
        filing = " ".join((REPO_ROOT / "skills" / "_shared" / "follow-up-filing.md").read_text(encoding="utf-8").split())
        self.assertIn("The one uncapped group is the resolver's **docs lane**", filing)
        self.assertNotIn("for review-deferred (`file-now`) items, which file immediately", filing)

    def test_planned_work_not_yet_built_is_introduced(self):
        # PR #72 review: an unfinished `## Changes` entry sits in code the PR has not touched yet, so it
        # read as pre-existing and would have been deferred instead of built.
        r = self.rubric
        self.assertIn("Or it is work the plan's `## Changes` / phase `ships` or the issue's Definition of done "
                      "assigns to this PR, built or not", r)
        self.assertIn("found in code this PR does not change and the plan does not assign to it", r)

    def test_other_buckets_are_checked_before_addressable(self):
        # PR #72 review: Addressable was reached first, so a no-shown-cost finding on the PR's own code was
        # fixed as polish, and a sibling a later phase ships was pulled forward.
        r = self.rubric
        self.assertIn("**Buckets:** check the other buckets first", r)
        self.assertLess(r.index("check the other buckets first"), r.index("- **Addressable** —"))
        self.assertIn("unless a later phase in `facts.phases` ships that site, which is Deferred-by-plan", r)
        self.assertIn("It applies whatever the provenance — the PR's own code included", r)
        self.assertIn("A latent risk with a named trigger", r)
        self.assertNotIn("with the note `no shown cost: taste`", r)

    def test_adjacent_is_the_same_change_at_a_sibling_site_only(self):
        # PR #72 review: "a one-site correction to a file this PR edits" made every nit in a touched file
        # Addressable; the audit's adjacent cases were the same change at a sibling site.
        self.assertNotIn("one-site correction to a file or doc this PR already edits", self.rubric)

    def test_an_introduced_defect_exit_list_names_every_operator_answer(self):
        defect = self.rubric.split("- **defect** —", 1)[1].split("- **polish** —", 1)[0]
        for answer in ("**Re-plan**", "**Accept + defer**", "**Accept current**", "**Defer the tests**",
                       "**Push with reds**", "**Restructure**", "**Abort**", "**Abort loop**"):
            self.assertIn(answer, defect)
        self.assertIn("their approval on the end-of-loop checkpoint card (step 6's unprovable assertion)", defect)
        # Step 6 no longer files a PR-written unprovable assertion on its own.
        self.assertIn("register the assertion as a `deferred-test` follow-up for the end-of-loop checkpoint", self.text)
        self.assertNotIn("file the assertion as a `deferred-test` follow-up (step 4's filing protocol)", self.text)

    def test_a_terminating_exit_still_runs_the_checkpoint(self):
        # PR #72 review: batched follow-ups were dropped when Re-plan / Abort skipped S7.
        spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.assertIn("and runs S7, then goes to the routed playbook's handoff", spine)
        self.assertIn("so a terminating exit never drops one", self.text)
        tracking = " ".join((REFERENCES_DIR / "follow-up-tracking.md").read_text(encoding="utf-8").split())
        self.assertIn("or, on a terminating guard-rail answer (Re-plan, Restructure, Abort, Abort loop), "
                      "before that exit's handoff", tracking)

    def test_the_owner_is_read_through_the_fetch_envelope(self):
        self.assertIn("read its body or Definition of done with `gh_gather.py`", self.rubric)
        tracking = " ".join((REFERENCES_DIR / "follow-up-tracking.md").read_text(encoding="utf-8").split())
        self.assertIn('${CLAUDE_PLUGIN_ROOT}/scripts/gh_gather.py <M> <owner/repo> "" "<facts.scratch>"', tracking)
        self.assertNotIn("gh issue view <", tracking)

    def test_finalisation_triages_every_item_the_rounds_recorded(self):
        finalisation = " ".join((REFERENCES_DIR / "finalisation.md").read_text(encoding="utf-8").split())
        self.assertIn("every item this pass's fix rounds recorded for the ledger (polish not fixed, "
                      "pre-existing polish)", finalisation)
        self.assertIn("The no-shown-cost items carry theirs too: they are already `drop`, so they are not "
                      "triaged", finalisation)
        # #98: step 8 mints the id its finding line names; finalisation reads it and never re-mints.
        self.assertIn("Read those ids; never mint another", finalisation)
        self.assertNotIn("this pass's unfixed polish", finalisation)

    def test_the_file_keeps_nine_steps_and_no_positional_references(self):
        self.assertEqual(len(re.findall(r"^\d+\. \*\*", self.raw, re.MULTILINE)), 9)
        self.assertNotRegex(self.raw, r"\b(?:above|below)\b")


class DodDriftFactTests(unittest.TestCase):
    """4.32.0: bullet-count drift is a prep fact, and the resolver never ticks another heading.

    The rule used to call a multi-phase plan against a DoD-less issue "impossible past planner
    dimension-7 review" — it was not, and a session meeting one projected the plan's indexes onto
    `## Acceptance criteria`, which neither the evaluator nor the next session could see.
    """

    def setUp(self):
        self.rule = " ".join(
            (REFERENCES_DIR / "dod-projection-rule.md").read_text(encoding="utf-8").split()
        )

    def test_the_false_impossibility_claim_is_gone(self):
        self.assertNotIn("impossible past planner dimension-7 review", self.rule)

    def test_drift_keys_on_the_prep_fact(self):
        self.assertIn("`facts.dod_coverage.drift`", self.rule)

    def test_drift_re_routes_before_any_code(self):
        # The S6 block alone would ship a phase first; S4 reads the fact before the work starts.
        spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        s4 = spine.partition("## S4 — Detect phases")[2]
        self.assertIn(
            "`facts.dod_coverage.drift` → re-route to the planner before any code", s4[:400]
        )

    def test_another_headings_checklist_is_never_projected_onto(self):
        self.assertIn("*A checklist under another heading*", self.rule)
        self.assertIn("never project onto it, the single-phase fallback included", self.rule)
        # The slice-closing rung still ticks a slice's own `## Acceptance criteria`.
        self.assertIn("Slice body with no `## Acceptance criteria` section", self.rule)



class PrScreenshotTests(unittest.TestCase):
    """4.41.0: a PR that changes what a user sees carries screenshots in its body, uploaded by the
    PR write's `--attach`. The procedure lives in a reference (the spine sits at the prompt-economy
    bar); the spine names it on both PR writes and restages later body writes from the rewritten
    copy, because a restage from `pr.md` would put the local paths back and break the images."""

    def setUp(self):
        self.flat = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.reference = REFERENCES_DIR / "pr-screenshots.md"

    def test_the_spine_attaches_on_the_pr_writes_and_restages_from_the_posted_body(self):
        self.assertIn('[--attach "<shot>"]...', self.flat)
        self.assertIn("Both writes take one `--attach` per screenshot", self.flat)
        self.assertIn("`## Screenshots` when a user sees the change", self.flat)
        self.assertIn("its envelope's `posted_body_path` when it carried `--attach`", self.flat)

    def test_the_reference_carries_the_contract_the_script_enforces(self):
        flat = " ".join(self.reference.read_text(encoding="utf-8").split())
        for fragment in (
            "**same absolute path** you pass to `--attach`",  # gh matches by exact string
            "`posted_body_path`",
            "`ATTACH_PARTIAL`",
            "Never re-run `create-pr`",
            "`ATTACH_READBACK_UNAVAILABLE`",
            "`ATTACH_UNSUPPORTED:`",
            "`<facts.scratch>/shots/`",
        ):
            self.assertIn(fragment, flat)

    def test_the_spine_create_pr_shape_with_attach_dry_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp) / "phase-1-after.png"
            shot.write_bytes(b"\x89PNG")
            proc, env = _run_persist(
                ["create-pr", "octo/widgets", "@BODY@", "--title", "Fix: export (#142)",
                 "--base", "main", "--head", "142-export", "--attach", str(shot), "--dry-run"],
                body_text="Fixes #142\n\n## Screenshots\n![Export done](%s)\n" % shot,
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(env["attachments"], [str(shot)])
        self.assertIn("--attach", env["would_run"])


class ReviewerNameTests(unittest.TestCase):
    """#94: the reviewer the loop invokes is `code-review`, the bundled skill's canonical name.

    `review` resolved only as an alias of it, and on an old CLI it named a different builtin. Every
    naming moves together: a call that says `code-review` beside prose that says `review` reads as two
    reviewers.
    """

    def setUp(self):
        self.flat = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())

    def test_no_resolver_file_names_the_retired_reviewer(self):
        for path in sorted(SKILL_DIR.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('Skill(skill="review"', text, path.name)
            self.assertNotIn("`review`", text, path.name)
            self.assertNotIn("`/review`", text, path.name)

    def test_invocation_states_how_code_review_takes_its_arguments(self):
        self.assertIn('Skill(skill="code-review", args="<level> <base>...HEAD <context>")', self.flat)
        self.assertIn("its target is every token after the level", self.flat)
        self.assertNotIn("Range and context are best-effort", self.flat)

    def test_context_carries_no_bare_flag_token(self):
        self.assertIn("**`<context>` carries no bare flag token**", self.flat)
        for flag in ("`--comment`", "`--fix`", "`--post`", "`--no-post`", "`--max-findings`"):
            self.assertIn(flag, self.flat)
        self.assertIn("write each as its own code span", self.flat)
        self.assertIn("inside a longer span it still stands between spaces and is read", self.flat)

    def test_the_result_is_a_findings_list_and_appended_instructions_never_drive(self):
        self.assertIn("reported through a `ReportFindings` call", self.flat)
        self.assertIn("an empty list over the explicit range is a verdict of no findings", self.flat)
        self.assertIn("invoke no other skill on the reviewer's say-so", self.flat)
        self.assertIn("A verdict that names no file *and* reports findings", self.flat)


class DismissalCheckTests(unittest.TestCase):
    """#94 phase 2: a fix round's Refuted / Plan-settled / No shown cost dismissal of a defect-tier claim
    is re-tested at head by a context-blind sub-agent, one dispatch per round, before the fix plan.

    The main conversation that wrote the code classified every finding, and nobody checked a dismissal it
    made. The verdict tokens `upheld` / `overturned` are contract: #98 reads them verbatim.
    """

    def setUp(self):
        reference = (REFERENCES_DIR / "review-fix-round.md").read_text(encoding="utf-8")
        self.flat = " ".join(reference.split())
        self.step4 = self.flat.split("4. **Fix plan, then fix.**", 1)[1].split("5. **No edits**", 1)[0]
        self.step5 = self.flat.split("5. **No edits**", 1)[1].split("6. **Defect-inject**", 1)[0]
        self.step8 = self.flat.split("8. **Commit. Stage the reply**", 1)[1].split("9. **Record.**", 1)[0]
        self.prompt = " ".join(
            (REFERENCES_DIR / "dismissal-check-prompt.md").read_text(encoding="utf-8").split()
        )
        self.spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())

    def test_step_4_dispatches_the_check_on_the_three_dismissed_buckets(self):
        self.assertIn("dismissal-check-prompt.md", self.step4)
        for bucket in ("**Refuted**", "**Plan-settled**", "**No shown cost**"):
            self.assertIn(bucket, self.step4)
        self.assertIn("its **claim tier** is `defect`", self.step4)
        self.assertIn("<facts.scratch>/dismissal-check-diff.patch", self.step4)
        self.assertIn("**one** dispatch for every qualifying item, none when none qualifies", self.step4)
        # The check runs before the fix plan and before its empty-list test, so a round with nothing
        # else to fix still checks its dismissals.
        self.assertLess(self.step4.index("**Dismissal check**"), self.step4.index("Then the **fix plan**"))
        self.assertLess(self.step4.index("**Dismissal check**"), self.step4.index("An empty list"))
        self.assertIn("a round with nothing else to fix included", self.step4)

    def test_the_scope_decision_is_recorded_with_its_reason(self):
        # DoD 4: No shown cost is checked, Deferred-by-plan is not, each with its reason.
        self.assertIn("**Scope**, and why: No shown cost dismissals are checked", self.step4)
        self.assertIn("**Deferred-by-plan** ones are not", self.step4)
        self.assertIn("would verify a seam this phase deliberately leaves unbuilt", self.step4)

    def test_an_overturned_dismissal_returns_to_the_loop(self):
        # DoD 3: introduced or adjacent → Addressable; the rubric routes the other two.
        self.assertIn("**introduced** or **adjacent** → Addressable, tier `defect`", self.step4)
        self.assertIn("**pre-existing** → Explicitly-deferred", self.step4)
        self.assertIn("Decision-required, **Re-plan** recommended", self.step4)
        self.assertIn("Never read it as `upheld`", self.step4)
        self.assertIn("The check is not a `code-review` run", self.step4)

    def test_the_rubric_tiers_the_claim_not_the_bucket(self):
        claim_tier = self.flat.split("**Claim tier** — for every item not Addressable.", 1)[1].split("## Steps", 1)[0]
        # #98: the claim tier is the finding line's `<tier>` field, on every non-Addressable bucket.
        self.assertIn("It is the `<tier>` field of the item's finding line (step 8), so that field is never empty",
                      claim_tier)
        # The defect list is the rubric's, test gaps included — a dismissed test-gap claim is checked.
        self.assertIn("a test gap that would let a real bug through", claim_tier)
        # The #94 revise: an unfinished-`## Changes` claim is checked too — a wrong refutation of it ships
        # a planned change undelivered. The one exclusion left is the non-material doc or comment finding.
        self.assertIn("an unfinished `## Changes` entry of the phase being built", claim_tier)
        self.assertNotIn("never dismissible", claim_tier)
        self.assertIn("The one exclusion: a doc or comment finding that fails materiality", claim_tier)

    def test_a_kept_entry_is_not_dispatched(self):
        # The operator answered it; a machine read must not re-raise it. Step 3's pointer names both marks.
        self.assertIn("An entry marked `kept` is not dispatched either", self.step4)
        self.assertIn("an entry carrying `check: upheld` or marked `kept` does not", self.flat)

    def test_the_check_receives_the_plans_changes(self):
        self.assertIn("**Plan changes**: `<<plan_changes>>`", self.prompt)
        self.assertIn("as `<<plan_changes>>` writes it", self.prompt)
        self.assertIn("`<<plan_changes>>` with the plan's `## Changes (file-level)` section verbatim", self.step4)
        # The main plan comment holds only a stub link to `## Changes`; the assembled plan carries it.
        self.assertIn("read from the assembled plan at `facts.distiller_bundle.plan_marker_path`", self.step4)
        self.assertIn("decision bullets and `## Changes` section are plan text, not history", self.prompt)

    def test_step_5_is_judged_after_the_check_and_stages_its_records(self):
        self.assertIn("judged after the dismissal check returns", self.step5)
        self.assertIn("or the dismissal check recorded any verdict", self.step5)

    def test_step_8_records_the_verdicts(self):
        self.assertIn("`check: upheld` or `check: overturned`", self.step8)
        self.assertIn('"<decision bullet, verbatim>"[ — check: upheld] (×<repeats>)', self.step8)
        self.assertIn("`no shown cost: <why> — check: upheld`", self.step8)

    def test_a_checked_settled_entry_is_not_dispatched_again(self):
        # Step 4 owns the exclusion; step 3 points at it.
        self.assertIn("Whether it goes to step 4's dismissal check again is step 4's rule", self.flat)
        self.assertIn("including a verbatim repeat step 3 re-settled silently", self.step4)
        self.assertIn("One without the suffix (recorded before the check existed, or never given a verdict) is", self.step4)
        # The in-session list carries the verdict, so a round-1 upheld item is not re-dispatched in round 2.
        self.assertIn("in its `Settled (not addressed):` line's form (step 8)", self.flat)
        self.assertIn("each as its `Settled (not addressed):` line", self.flat)

    def test_an_overturned_ledger_entry_is_rewritten_as_a_reclassified_drop(self):
        self.assertIn("rewrite in place to `drop` with the note `reclassified: defect — see loop comment`", self.step8)
        self.assertIn("A note rewrite held for S5.2 is the entry's note for every reader this pass", self.step8)
        # Only a refuted-items entry is re-listed in the Settled block; a ledger entry carries its verdict in its note.
        self.assertIn("upheld an existing refuted-items entry (re-listed with its suffix and count)", self.step8)
        ledger = " ".join((SKILL_DIR.parent / "_shared" / "polish-ledger.md").read_text(encoding="utf-8").split())
        self.assertIn("or, for an entry found to be a defect, the resolver (finalisation, or S5.2 for an overturned dismissal) or the evaluator (S4)", ledger)
        finalisation = " ".join((REFERENCES_DIR / "finalisation.md").read_text(encoding="utf-8").split())
        self.assertIn("An entry with a note rewrite held for this pass's S5.2 write", finalisation)

    def test_an_overturned_item_skips_the_settled_item_pre_check(self):
        self.assertIn("it skips step 3's settled-item pre-check and its `Settled item` card", self.step4)

    def test_an_unparsed_verdict_continues_the_round_unchecked(self):
        self.assertIn("continue the round with each in its bucket, its reply line saying the check returned no verdict", self.step4)

    def test_the_spine_names_the_check_and_s52_rewrites_the_ledger_note(self):
        self.assertIn("send step 4's qualifying dismissals to the **dismissal check**", self.spine)
        self.assertIn("each note rewrite the dismissal check's verdicts call for (`review-fix-round.md` step 8)", self.spine)
        # A suffix rewrite alone is a body change, so continue mode stages `edit-pr-body` for it.
        self.assertIn("loop changed the PR body (outstanding settled items, ledger entries or note rewrites", self.spine)

    def test_the_prompt_returns_the_closed_verdict_set(self):
        self.assertIn("`## Verdicts`", self.prompt)
        self.assertIn("`<n>. overturned — <path>:<line> — <evidence>`", self.prompt)
        self.assertIn('`<n>. upheld — <evidence, or "no path found: <doubt>">`', self.prompt)
        self.assertIn("The verdict set is closed: `upheld` | `overturned`.", self.prompt)
        self.assertIn("`code: AMBIGUOUS`", self.prompt)

    def test_cold_read_round_closes_the_checks_edges(self):
        # C1: a Re-plan answer to the check's own card has a case in the independent-defect pass.
        self.assertIn("**A `Decision` card raised by step 4's dismissal check**", self.flat)
        self.assertIn("the dismissal check has already run this round and is not dispatched again", self.flat)
        # C10: that card's paths, and the `kept` record of choosing the decision as it stands.
        self.assertIn("when step 4's dismissal check raised it (an overturned Plan-settled item), the plan decision as it stands", self.flat)
        self.assertIn("or kept by choosing the decision as it stands on a `Decision` card (step 4's fix plan, fix design's or the dismissal check's)", self.step8)
        # Cold read (resumed loop): a kept overturned item is re-listed, so the next session's seed keeps it.
        self.assertIn("unless the operator kept it on the `Decision` card, when it is re-listed marked `kept`", self.step8)
        self.assertIn("unless that round's block or a later one lists it again (the operator kept it)", self.flat)
        # The fix plan's own "decision as it stands" answer is kept too, so the check never re-raises it.
        self.assertIn("settles Plan-settled, citing it, marked `kept` — step 8", self.step4)
        self.assertIn("the plan decision as it stands (the item settles Plan-settled, marked `kept`) and **Re-plan**", self.flat)
        self.assertIn("or chose the decision as it stands on a `Decision` card — step 8 — this session or an earlier one", self.flat)
        # New evidence reopens a ledger item's verdict: only a verbatim repeat skips the check.
        self.assertIn("or a verbatim repeat of an item recorded for the ledger whose note ends ` — check: upheld`", self.step4)
        # C5: an overturned entry leaves the refuted-items list and is not re-seeded.
        self.assertIn("one the dismissal check overturned removed", self.flat)
        self.assertIn("a later round's section records `check: overturned`", self.flat)
        # C6: new evidence drops the verdict as it drops `kept`.
        self.assertIn("a refuted-items entry's ` — check: upheld` dropped too", self.flat)
        # C13: a no-verdict line is staged in a no-edit round.
        self.assertIn("or returned none for an item", self.step5)
        # C4: a light re-review's dismissal of a defect-tier claim goes through a fix round.
        # Only a dismissal step 4 would dispatch re-enters: a `kept` or `check: upheld` repeat does not.
        self.assertIn("or a dismissal step 4 would check, re-enters this loop at step 2", self.spine)
        # That re-entry resets the progress baseline, as finalisation §4's does.
        self.assertIn("reopens a settled loop with a defect or a dismissal to check, the next round counts as round 1", self.spine)
        finalisation = " ".join((REFERENCES_DIR / "finalisation.md").read_text(encoding="utf-8").split())
        self.assertIn("unless step 4 would check it. A defect it finds, or a dismissal step 4's dismissal check would dispatch", finalisation)
        self.assertIn("not already `check: upheld` or `kept`", finalisation)
        # C2: the prompt renders the rubric's still-true exception in full.
        self.assertIn('says "only" / "exactly" or records the omission as a choice', self.prompt)

    def test_the_prompt_is_context_blind(self):
        self.assertIn(
            "**Do not include the review-loop history, prior `code-review` verdicts, prior round replies, "
            "prior dismissal-check verdicts, the resolver's state summary, or any conversation turns**",
            self.prompt,
        )

class ReviewLoopRecordTests(unittest.TestCase):
    """#98: the loop comment carries the review-loop record (skills/_shared/review-loop-record.md). Step 8
    stages it and S5.2 posts it; the contract test pins the grammar, this pins the two call sites."""

    def setUp(self):
        self.spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.fix_round = " ".join(
            (SKILL_DIR / "references" / "review-fix-round.md").read_text(encoding="utf-8").split()
        )

    def test_step_8_cites_the_record(self):
        step8 = self.fix_round.split("8. **Commit. Stage the reply**", 1)[1].split("9. **Record.**", 1)[0]
        self.assertIn("in the record's format ([`../../_shared/review-loop-record.md`]", step8)

    def test_every_classified_item_gets_its_line_and_id(self):
        # Review round 1: a no-edit round, a light re-review and an Accept current cold read each
        # recorded items with no line and no ledger id once finalisation stopped minting.
        step5 = self.fix_round.split("5. **No edits**", 1)[1].split("6. **Defect-inject**", 1)[0]
        self.assertIn("only when this round classified any item afresh", step5)
        step8 = self.fix_round.split("8. **Commit. Stage the reply**", 1)[1].split("9. **Record.**", 1)[0]
        self.assertIn('"Items recorded outside a fix round"', step8)
        finalisation = " ".join(
            (SKILL_DIR / "references" / "finalisation.md").read_text(encoding="utf-8").split()
        )
        self.assertIn("A held item with no id is a staging gap: mint it now", finalisation)

    def test_the_record_counters_are_kept_and_seeded(self):
        # #98 cold read: who keeps the run count, and the interrupted run's surviving ids.
        self.assertIn("which you keep: add one for every `code-review` run S5.1 or finalisation makes", self.fix_round)
        self.assertIn("its ids seed both counters, and every item one of its lines records `ledger P<…>` is held "
                      "again for S5.2 under that id", self.fix_round)
        continuation = " ".join(
            (SKILL_DIR / "references" / "phase-continuation.md").read_text(encoding="utf-8").split()
        )
        self.assertIn("the record's run count and id counters", continuation)

    def test_a_silent_repeat_writes_no_finding_line(self):
        self.assertIn("no card, no second reply and no finding line", self.fix_round)

    def test_the_spine_posts_the_marker_and_the_run_count(self):
        s52 = self.spine.split("### S5.2", 1)[1].split("## S6", 1)[0]
        # Review round 2: S5.2 is the writer of the run-count line, and of the marker when nothing was staged.
        self.assertIn("Then write the pass's `Review runs:` line as the last line of", s52)
        self.assertIn("(loop entry opened it — [`review-level.md`](../references/review-level.md) §3)", s52)
        self.assertNotIn("when no round staged them", s52)
        self.assertIn("led by its marker ([`review-loop-record.md`](../../_shared/review-loop-record.md))", s52)
        self.assertIn("each marker-led under its phase heading", s52)


if __name__ == "__main__":
    unittest.main()
