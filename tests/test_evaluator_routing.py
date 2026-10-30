"""Offline tests for the evaluator skill (docs/implementation.md S7).

Two offline surfaces the S7 DoD/Testing section names:

1. **Routing-table fixtures** (`vector` → `playbooks/<file>`). The router's visible routing table
   (`skills/evaluator/SKILL.md` §2) is the contract that maps a prep-derived `vector.type` to the
   one playbook a session reads. These tests PARSE that table out of the router markdown and assert
   the mapping is (a) exactly the three PR types the vector can take, (b) each pointing at a real
   `playbooks/<file>` that exists on disk, and (c) byte-consistent with what
   `prep_evaluator._suggested_playbook` proposes for the same PR type — since the router "confirms
   `suggested_playbook` against the table" (architecture.md §5), a divergence between the table and
   the script would silently break routing with no offline harness to catch it otherwise.

2. **`--dry-run` persist envelopes.** Every GitHub write the playbooks specify goes through
   `gh_persist.py` (SKILL.md §3). These tests run each such invocation with `--dry-run` against the
   offline shim and assert the envelope is conformant (status ok, `would_run` present, no live gh
   call) — proving the exact persist invocations the playbooks name are well-formed, without a live
   repo. The merge executor (`gh pr merge`) and the draft-flip (`gh pr ready --undo`) are NOT
   gh_persist ops (no persist subcommand covers them — see the module's raw-gh note below), so they
   are not dry-run-tested here; they are covered by the parity protocol's live scenarios.

Structural router assertions (line counts, no PR-type conditionals, grep gates) live alongside the
routing-table parse so a single `python3 tests/run.py` catches a router that drifts out of the S7
size/shape bar.

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
SKILL_DIR = REPO_ROOT / "skills" / "evaluator"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_evaluator  # noqa: E402  (import after sys.path setup, by necessity)
import review_threads  # noqa: E402
from tests.support import envelope_asserts, shimenv  # noqa: E402
from tests.support.retired_tokens import (  # noqa: E402
    FORBIDDEN_CONTRACT_TOKENS,
    rename_namespace,
)


# ---------------------------------------------------------------------------
# Router routing-table parse
# ---------------------------------------------------------------------------

# The router's §2 table rows look like:  | `standard` | `playbooks/standard.md` | ... |
# First cell = a backticked vector.type; second cell = a backticked playbooks/<file>.
_ROUTE_ROW_RE = re.compile(
    r"^\|\s*`(?P<vector>[a-z-]+)`\s*\|\s*`(?P<playbook>playbooks/[a-z0-9-]+\.md)`\s*\|"
)


def _parse_router_routing_table(router_text):
    """Return an ordered dict {vector.type: playbooks/<file>} parsed from the router's routing
    table. Only rows whose first two cells are a backticked vector type and a backticked
    `playbooks/<file>` path count — the header row (`| `vector.type` | Playbook | … |`) and the
    separator row don't match, so the parse is robust to table position."""
    mapping = {}
    for line in router_text.splitlines():
        match = _ROUTE_ROW_RE.match(line)
        if match:
            mapping[match.group("vector")] = match.group("playbook")
    return mapping


class RouterRoutingTableTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")
        self.table = _parse_router_routing_table(self.router_text)

    def test_router_exists_and_has_no_model_effort_pins(self):
        # Model/effort are not pinned — skills inherit the invoking session's model and effort.
        self.assertTrue(ROUTER.is_file(), "router SKILL.md must exist")
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: evaluator", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)

    def test_routing_table_covers_exactly_the_three_pr_types(self):
        # The vector.type set the evaluator can see is exactly {standard, story, epic-integration}
        # (prep_evaluator._detect_pr_type). The visible table must map each, and only these.
        self.assertEqual(
            set(self.table.keys()),
            {"standard", "story", "epic-integration"},
            "router routing table vectors must be exactly the three prep pr_types, got %r"
            % (sorted(self.table.keys()),),
        )

    def test_every_routed_playbook_file_exists(self):
        for vector, playbook in self.table.items():
            target = SKILL_DIR / playbook
            self.assertTrue(
                target.is_file(),
                "routing table maps %r -> %r but %s does not exist" % (vector, playbook, target),
            )

    def test_table_matches_prep_suggested_playbook_for_each_type(self):
        # architecture.md §5: prep proposes `suggested_playbook`; the router confirms it against the
        # table. A divergence would silently mis-route. Assert byte-consistency: for each PR type,
        # the table's target basename == prep's proposed basename.
        for pr_type, playbook in self.table.items():
            proposed = prep_evaluator._suggested_playbook(pr_type, "OPEN")
            self.assertEqual(
                Path(playbook).name,
                proposed,
                "router table maps %r -> %r but prep proposes %r"
                % (pr_type, Path(playbook).name, proposed),
            )

    def test_prep_suggested_playbook_only_targets_files_the_table_lists(self):
        # The reverse direction: every basename prep can propose is a file the table routes to (so
        # prep never suggests a playbook the router has no row for).
        table_basenames = {Path(p).name for p in self.table.values()}
        for pr_type in ("standard", "story", "epic-integration"):
            self.assertIn(prep_evaluator._suggested_playbook(pr_type, "OPEN"), table_basenames)

    def test_evaluate_spine_exists_and_is_referenced_by_every_routed_playbook(self):
        # architecture.md §5: a playbook may pull in its skill's single shared spine and nothing
        # else. Every routed playbook must reference evaluate-spine.md (they open by reading it).
        spine = PLAYBOOKS_DIR / "evaluate-spine.md"
        self.assertTrue(spine.is_file(), "the shared spine evaluate-spine.md must exist")
        for playbook in self.table.values():
            text = (SKILL_DIR / playbook).read_text(encoding="utf-8")
            self.assertIn(
                "evaluate-spine.md",
                text,
                "routed playbook %s must reference the shared spine evaluate-spine.md" % playbook,
            )


# ---------------------------------------------------------------------------
# Router structural bar (S7 DoD boxes 1 & 3)
# ---------------------------------------------------------------------------


class RouterStructuralBarTests(unittest.TestCase):
    def test_router_at_most_150_lines(self):
        n = len(ROUTER.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(n, 150, "router SKILL.md is %d lines (bar: <= 150)" % n)

    def test_router_plus_largest_playbook_at_most_485(self):
        # S7 DoD box 7 / prd.md §10: router + largest playbook <= half of v1's 971 = 485.
        router_lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        playbook_lines = {
            p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in PLAYBOOKS_DIR.glob("*.md")
        }
        largest = max(playbook_lines.values())
        self.assertLessEqual(
            router_lines + largest,
            485,
            "router (%d) + largest playbook (%d) = %d exceeds 485 (half of v1's 971): %r"
            % (router_lines, largest, router_lines + largest, playbook_lines),
        )

    def test_playbook_bodies_have_no_pr_type_conditionals(self):
        # S7 DoD box 1: playbook bodies contain no PR-type conditionals (no `if story … else …`).
        # The route IS the branch; a playbook is a linear narrative. Flag any `if <pr-type> …`
        # branching construct in a body.
        banned = re.compile(
            r"\bif\b[^.\n]{0,40}\b(story|standard|epic-integration|epic integration)\b"
            r"[^.\n]{0,40}\belse\b",
            re.IGNORECASE,
        )
        for playbook in PLAYBOOKS_DIR.glob("*.md"):
            for i, line in enumerate(playbook.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(
                    banned.search(line),
                    "playbook %s:%d looks like a PR-type conditional: %r"
                    % (playbook.name, i, line),
                )

    def test_no_forbidden_tokens_under_skill_dir(self):
        # S7 DoD box 3: zero retired-executor tokens, zero old skill-invocation namespace strings, zero v1
        # GATHER_/PERSIST_ op names anywhere under skills/evaluator/.
        forbidden = FORBIDDEN_CONTRACT_TOKENS
        for path in SKILL_DIR.rglob("*.md"):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = forbidden.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden contract token under skills/evaluator/ at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_no_raw_gh_persist_or_gather_writes(self):
        # S7 DoD box 3: no raw `gh` writes that BYPASS gh_persist.py / gh_gather.py. The persist/
        # gather ops are gh issue|pr create/edit/comment/review/close/reopen and gh api ... DELETE.
        # The only raw `gh` commands allowed are the merge executor (`gh pr merge` — no persist op
        # exists for it) and the PR-state toggles (`gh pr ready --undo`, the read `gh pr view` /
        # `gh pr checks --watch`). Assert no persist-op-bypassing raw write appears.
        raw_write = re.compile(
            r"\bgh\s+(issue|pr)\s+(create|edit|comment|review|close|reopen)\b"
            r"|\bgh\s+api\b[^\n]*\bDELETE\b"
        )
        for path in SKILL_DIR.rglob("*.md"):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # Allow the frozen `gh pr view … --json mergeStateStatus` read that appears in a
                # handoff Why example (a read, not a write) — it doesn't match raw_write anyway.
                hit = raw_write.search(line)
                self.assertIsNone(
                    hit,
                    "raw gh persist/gather WRITE bypassing a script at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )


# ---------------------------------------------------------------------------
# --dry-run persist envelopes for the writes the playbooks specify
# ---------------------------------------------------------------------------


def _run_persist(args, body_text=None):
    """Run gh_persist.py <args> as a real subprocess under the offline shim. When body_text is
    given, write it to a temp file and substitute the placeholder token @BODY@ in args with the
    path (so callers name the body inline). Returns (completed_process, parsed_envelope_or_None)."""
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
    """Each GitHub write the evaluator playbooks specify, run with --dry-run: a conformant envelope,
    status ok, `would_run` present, exit 0, no live gh call. These are the exact gh_persist
    invocation SHAPES the playbooks name (SKILL.md §3 / spine S3.5, S4-untick, S7-post; story route
    Actions 1-3; residual follow-up comment)."""

    def _assert_dry_run_ok(self, proc, envelope, expected_op):
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertIsNotNone(envelope, "expected exactly one envelope on stdout")
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope.get("status"), "ok")
        self.assertEqual(envelope.get("op"), expected_op)
        self.assertTrue(envelope.get("dry_run"))
        self.assertIn("would_run", envelope)

    def test_health_cache_comment_dry_run(self):
        # spine S3.5: gh_persist.py comment <repo> pr <PR> <health-cache.md> [--delete-marker-id …]
        proc, env = _run_persist(
            ["comment", "octo/widgets", "pr", "287", "@BODY@", "--delete-marker-id", "9911", "--dry-run"],
            body_text="<!-- pr-evaluator-health-cache:v1 -->\nbody\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_review_comment_dry_run_approve(self):
        # spine S7-post: gh_persist.py comment <repo> pr-review <PR> <review.md> --review-action approve
        proc, env = _run_persist(
            ["comment", "octo/widgets", "pr-review", "287", "@BODY@", "--review-action", "approve", "--dry-run"],
            body_text="APPROVE review body\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_review_comment_dry_run_soft_reject(self):
        # spine S7-post soft-reject: --review-action comment
        proc, env = _run_persist(
            ["comment", "octo/widgets", "pr-review", "287", "@BODY@", "--review-action", "comment", "--dry-run"],
            body_text="COMMENT soft-reject body\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_dod_untick_edit_body_dry_run(self):
        # spine S4-untick: gh_persist.py edit-body <repo> <issue> <issue-body-corrected.md>
        proc, env = _run_persist(
            ["edit-body", "octo/widgets", "101", "@BODY@", "--dry-run"],
            body_text="## Definition of done\n- [ ] X (resolver claimed phase 2, commit abc1234; evaluator rejected: mismatch)\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-body")

    def test_story_close_dry_run(self):
        # story route Action 1: gh_persist.py close <repo> <story> --reason completed
        proc, env = _run_persist(
            ["close", "octo/widgets", "151", "--reason", "completed", "--dry-run"]
        )
        self._assert_dry_run_ok(proc, env, "close")

    def test_epic_checkbox_edit_body_dry_run(self):
        # story route Action 2: gh_persist.py edit-body <repo> <epic> <epic-body-updated.md>
        proc, env = _run_persist(
            ["edit-body", "octo/widgets", "150", "@BODY@", "--dry-run"],
            body_text="## Stories\n- [x] #151\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-body")

    def test_delivery_log_comment_dry_run(self):
        # story route Action 3: gh_persist.py comment <repo> issue <epic> <delivery-log.md> [--delete-marker-id …]
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "150", "@BODY@", "--delete-marker-id", "7722", "--dry-run"],
            body_text="<!-- epic-delivery-log:v1 -->\n**Epic delivery log** — #150 Title\n- #151 — delivered: X @ `abc1234` (PR #287, merged 2026-07-04)\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_followups_body_weave_dry_run(self):
        # standard/story/epic-integration residual filing: the URLs land in the PR body's
        # `## Follow-ups` (gh_persist.py edit-pr-body), where the epic residue reads them.
        proc, env = _run_persist(
            ["edit-pr-body", "octo/widgets", "287", "@BODY@", "--dry-run"],
            body_text="Fixes #142\n\n## Follow-ups\n- #310 — filed after merge\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-pr-body")

    def test_dry_run_makes_no_live_gh_call(self):
        # Belt-and-braces: the dry-run path must not touch gh at all. If it did, the offline shim
        # (with no fixture case set) would fail loudly on an unexpected argv, so a clean exit 0 with
        # a would_run envelope IS the proof no gh call fired — assert both here explicitly.
        proc, env = _run_persist(
            ["close", "octo/widgets", "151", "--reason", "completed", "--dry-run"]
        )
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertTrue(env.get("dry_run"))
        self.assertNotIn("url", env, "a dry-run must not carry a live-write url")


class SliceBackstopTests(unittest.TestCase):
    """The merge-time slice-closing backstop (#17). The resolver owns closing a slice as its last
    serving phase ships; the evaluator only catches slices an interrupted run left open, because a
    slice still open behind a merged parent leaves the rollup permanently short."""

    def setUp(self):
        self.story = re.sub(
            r"\s+", " ", (PLAYBOOKS_DIR / "story.md").read_text(encoding="utf-8").replace("**", "")
        )
        self.standard = re.sub(
            r"\s+", " ", (PLAYBOOKS_DIR / "standard.md").read_text(encoding="utf-8").replace("**", "")
        )
        self.epic = (PLAYBOOKS_DIR / "epic-integration.md").read_text(encoding="utf-8")

    def test_story_route_closes_the_stories_own_slices(self):
        self.assertRegex(self.story, r"close any of the story's own open sub-issues")
        self.assertIn("epic-story-hierarchy.md", self.story)

    def test_standard_route_closes_the_issues_slices_since_auto_close_does_not(self):
        """The parent auto-closes on merge into `main`; its sub-issues never do."""
        self.assertRegex(self.standard, r"its sub-issues do not")
        self.assertRegex(self.standard, r"close any remaining deliverable slices")

    def test_both_routes_frame_it_as_a_backstop_not_the_owner(self):
        for text, name in ((self.story, "story.md"), (self.standard, "standard.md")):
            self.assertRegex(text, r"backstop only", name)
            self.assertRegex(text, r"resolver normally closes each as its last serving phase ships", name)

    def test_neither_route_ticks_acceptance_criteria(self):
        """Two writers for one projection is the defect this avoids."""
        for text, name in ((self.story, "story.md"), (self.standard, "standard.md")):
            self.assertRegex(text, r"Don't tick the slices' `## Acceptance criteria` here", name)

    def test_epic_integration_is_untouched(self):
        """An epic's sub-issues are STORIES, not slices, and this route explicitly files no story
        bookkeeping — adding a slice sweep here would contradict that sentence."""
        self.assertIn("files no story bookkeeping", self.epic)
        self.assertNotIn("deliverable slice", self.epic)

    def test_slice_close_dry_run(self):
        proc, envelope = _run_persist(
            ["close", "octo/widgets", "104", "--reason", "completed", "--dry-run"]
        )
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope.get("op"), "close")
        self.assertTrue(envelope.get("dry_run"))


class PolishAdjudicationTests(unittest.TestCase):
    """S5.5: the evaluator proposes and the operator decides the resolver's `## Polish` ledger
    (skills/_shared/polish-ledger.md) — apply / re-plan / file / drop — before S7's post timing,
    because `auto` merges on the APPROVE post."""

    def setUp(self):
        self.spine = re.sub(r"\s+", " ", (PLAYBOOKS_DIR / "evaluate-spine.md").read_text(encoding="utf-8"))
        self.reference = re.sub(
            r"\s+", " ", (SKILL_DIR / "references" / "polish-adjudication.md").read_text(encoding="utf-8")
        )
        # The steps the evaluator shares with the resolver's finalisation (4.30.0) live in one file.
        self.triage = re.sub(
            r"\s+", " ", (REPO_ROOT / "skills" / "_shared" / "polish-triage.md").read_text(encoding="utf-8")
        )

    def test_the_step_sits_before_the_post_timing(self):
        self.assertIn("## S5.5 — Polish ledger", self.spine)
        self.assertLess(self.spine.index("## S5.5 — Polish ledger"), self.spine.index("**Post timing.**"))
        self.assertIn("an operator-confirmed polish `apply` (S5.5)", self.spine)

    def test_the_operator_decides_every_entry_on_its_own_card(self):
        # The #959 run (PR #1000): only `apply` got a card, and under `ask` it folded into the Approve
        # card — 17 file/drop entries reached the operator as one line, with no way to re-plan one.
        self.assertIn('`header: "Polish"`', self.triage)
        self.assertIn("the operator decides **every** entry §1 proposed a disposition for", self.reference)
        self.assertIn("`file` and `drop` included, on any verdict and under either merge policy", self.reference)
        for option in ("**File as follow-up**", "**Apply in this PR**", "**Re-plan**", "**Drop**"):
            self.assertIn(option, self.triage)
            # The shared triage owns the labels; the spine and CLAUDE.md point at the card, never restate it.
            self.assertNotIn(option, self.spine)
        # The reference runs the shared card rather than restating its option list.
        self.assertIn("../../_shared/polish-triage.md", self.reference)
        self.assertNotIn("- **File as follow-up**", self.reference)
        claude_md = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertNotIn("File / Apply in this PR / Re-plan / Drop", claude_md)
        self.assertIn("the proposal first and marked recommended", self.triage)
        self.assertIn("a second `Polish` card with one question per group — at most 4; re-plan groups past "
                      "the cap (§3) follow on further cards", self.triage)
        self.assertNotIn("more groups ask a second card after the first is answered", self.triage)
        self.assertNotIn("ride along", self.reference)
        self.assertNotIn("ride along", self.triage)
        self.assertNotIn("ride along", self.spine)
        # The operator's card is the only bound on polish round-trips — never a counter.
        self.assertIn("deliberately an operator decision, not a counter", self.reference)

    def test_groups_are_themes_with_one_proposal_each(self):
        # Operator feedback (4.34.0): seam-and-type groups read as random, and a group mixing proposals
        # forced a split answer by id. Card groups are themes; filing groups are computed apart.
        self.assertIn("## 3. Group by theme", self.triage)
        self.assertIn("**Every entry in a group shares one proposal**", self.triage)
        self.assertIn("a theme whose entries were proposed differently splits by proposal", self.triage)
        self.assertIn("Keep it to **at most 4 groups**", self.triage)
        self.assertIn("merge the smallest groups that share a proposal into one labelled `Minor — <proposal>`",
                      self.triage)
        self.assertIn("give every entry proposed `file` its **filing group**", self.triage)
        self.assertIn("the card's groups never decide the filing", self.triage)
        self.assertNotIn("one group for all the `drop` proposals", self.triage)

    def test_a_digest_shows_the_code_before_any_card(self):
        # Operator feedback (4.34.0): with no code shown, the operator applied entries out of caution.
        self.assertIn("## 4. Show the digest", self.triage)
        self.assertIn("Before any card, print the digest as plain text", self.triage)
        self.assertIn("**the code at the anchor** — a short excerpt (at most ~10 lines) read from the caller's "
                      "checkout, or a before → after sketch", self.triage)
        self.assertIn("**what applying it costs**", self.triage)
        self.assertIn("A group proposed `file` ends with the follow-up issues it would file as", self.triage)
        self.assertIn("The digest is not a gate", self.triage)
        self.assertLess(self.triage.index("## 4. Show the digest"), self.triage.index("## 5. Ask"))

    def test_the_first_question_takes_all_recommendations(self):
        self.assertIn("**Take all recommendations** — first, marked recommended", self.triage)
        self.assertIn("**Review by group** — a second card asks group by group", self.triage)
        self.assertIn("With only one group, skip this question", self.triage)
        self.assertIn("including reviving a no-shown-cost drop", self.triage)
        self.assertIn("**Take all recommendations** → each entry's proposal, recorded as if answered entry by "
                      "entry", self.triage)
        self.assertNotIn("**Take all recommendations**", self.spine)
        self.assertNotIn("**Take all recommendations**", self.reference)

    def test_re_plan_is_offered_only_on_in_scope_groups(self):
        self.assertIn("Offered only on a group proposed `apply` or re-plan", self.triage)
        self.assertIn("says what the choice does **for this group**", self.triage)

    def test_take_all_never_hides_a_re_plan(self):
        # PR #76 review: take-all records a re-plan proposal as Re-plan, which re-routes the session.
        self.assertIn("**A re-plan proposal is always named with its route**", self.triage)
        self.assertIn("the take-all answer must not hide it", self.triage)
        # Each re-plan names a different plan decision, so the Minor merge never folds two together.
        self.assertIn("**Never merge re-plan groups**", self.triage)

    def test_the_dropped_list_shows_without_a_card_and_revives_with_one_group(self):
        self.assertIn("**When nothing is left to ask**, print the dropped list alone whenever it has entries",
                      self.triage)
        self.assertIn("in the ledger or held for this pass's S5.2 write", self.triage)
        self.assertIn("that question's \"Other\" then takes the revivals too", self.triage)
        self.assertIn("recorded `drop` the same way, without asking", self.triage)
        # The evaluator still writes the ledger when §1 left nothing to ask.
        self.assertIn("or, when §1 left nothing to ask, after §1", self.reference)

    def test_drop_groups_get_no_code_excerpt(self):
        self.assertIn("A group proposed `drop` gets the other lines only", self.triage)

    def test_the_take_all_question_is_not_restated_outside_the_triage(self):
        # CLAUDE.md: "Never restate the card's options outside it."
        for path in (PLAYBOOKS_DIR / "evaluate-spine.md", SKILL_DIR / "references" / "polish-adjudication.md",
                     REPO_ROOT / "CLAUDE.md", REPO_ROOT / "docs" / "architecture.md"):
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("take-all", text, path.name)
            self.assertNotIn("take all recommendations", text, path.name)

    def test_the_polish_card_precedes_the_approve_card_which_never_carries_the_ledger(self):
        self.assertIn("Ask **before** the S7-gate `Approve PR` card and before any S7-post", self.reference)
        self.assertIn("asked before the S7-gate card, which never carries the ledger", self.spine)
        self.assertIn("The polish ledger is already decided (S5.5); the card does not re-ask it.", self.spine)
        for retired in ("fold it into the S7-gate", "**Needs Revision** applies them", "**File instead**",
                        "**Merge anyway**"):
            self.assertNotIn(retired, self.reference)
            self.assertNotIn(retired, self.spine)

    def test_an_unfixed_apply_is_rechecked_not_skipped(self):
        # A revision run can leave a confirmed `apply` unfixed (Accept current / abort); skipping it
        # on the next run would merge past the operator's decision.
        self.assertIn("an `open`, unfixed `apply`, `file`, or `premise false` `drop` entry", self.spine)
        # PR #62 review: forcing it with no card left no way to release it — it is re-proposed.
        self.assertIn("marked `confirmed on a prior run, left unfixed`", self.reference)
        self.assertNotIn("no new card", self.reference)
        self.assertIn("the operator may re-apply, file it instead, or drop it", self.reference)
        self.assertIn("`resolved otherwise at <short-sha>`", self.reference)

    def test_the_recheck_verifies_the_claim_not_only_the_anchor(self):
        # PR #1000's P1.1 described what the code already did; the filed follow-up carried it.
        self.assertIn("check the entry's **claim**, not only that the anchor still exists", self.triage)
        self.assertIn("`premise false at head — <why>`", self.triage)
        self.assertIn("`narrowed: <what holds>`", self.triage)
        self.assertIn("Re-check each entry's claim at head", self.spine)

    def test_a_file_entry_is_rechecked_after_a_revision(self):
        # A `file` answered on a verdict that sent the PR back is still unfiled; the revision may fix it.
        self.assertIn("Next, every `file` entry — decided on an earlier run whose verdict sent the PR back, "
                      "or at the resolver's finalisation", self.reference)
        self.assertIn("still true at head → keep it, not asked again; gone → `drop` with "
                      "`resolved otherwise at <short-sha>`", self.reference)
        self.assertIn("carries no `open`, unfixed `apply`, `file`, or `premise false` `drop` entry", self.reference)

    def test_file_entries_carry_their_group_and_narrowed_claim_to_filing(self):
        # PR #65 review: the card's groups and a `narrowed:` claim must survive to the residual filing.
        self.assertIn("the note carrying `group: <its filing group from the digest>`", self.triage)
        self.assertNotIn("`group: <the question's group label>`", self.triage)
        ledger = re.sub(r"\s+", " ", (REPO_ROOT / "skills" / "_shared" / "polish-ledger.md").read_text(encoding="utf-8"))
        self.assertIn("a `narrowed:` note supersedes the item's claim", ledger)
        self.assertIn("one issue per recorded `group:`", ledger)
        self.assertIn("never regrouped at filing", ledger)
        for name in ("standard.md", "story.md", "epic-integration.md"):
            text = re.sub(r"\s+", " ", (PLAYBOOKS_DIR / name).read_text(encoding="utf-8"))
            self.assertIn("one issue per recorded `group:`, each briefed from its item and note", text, name)

    def test_apply_on_a_two_intent_entry_records_the_chosen_intent(self):
        self.assertIn("record the answer as `intent: <chosen>` in the note", self.triage)
        ledger = re.sub(r"\s+", " ", (REPO_ROOT / "skills" / "_shared" / "polish-ledger.md").read_text(encoding="utf-8"))
        self.assertIn("**An `apply` note's `intent:` is the operator's decision.**", ledger)
        fix_round = re.sub(
            r"\s+", " ",
            (REPO_ROOT / "skills" / "resolver" / "references" / "review-fix-round.md").read_text(encoding="utf-8"),
        )
        # An `apply` may be recorded at the resolver's finalisation too, so the fix round names no recorder.
        self.assertIn("each `apply` item — built to its note's `intent:` when it carries one", fix_round)

    def test_an_already_comment_verdict_keeps_its_own_marker_and_route(self):
        # The rewrite had dropped this: the evaluator's own soft-reject is not the operator's decision.
        self.assertIn("On a verdict that is **already** COMMENT", self.reference)
        self.assertIn("the review keeps the evaluator's header and the `COMMENT (soft-reject)` marker", self.reference)
        self.assertIn("except that a **Re-plan** answer routes to `planner revise` as an S4-untick does", self.reference)
        self.assertIn("When the polish answer is what turned an APPROVE into COMMENT", self.reference)
        rubric = re.sub(
            r"\s+", " ", (SKILL_DIR / "references" / "handoff-renderings.md").read_text(encoding="utf-8")
        )
        self.assertIn("`COMMENT (soft-reject)` when the evaluator's own verdict was already COMMENT", rubric)
        self.assertIn("on a Re-plan answer about an intent the plan leaves open, the two intents the note names", rubric)

    def test_an_in_scope_entry_needing_a_decision_is_proposed_as_re_plan(self):
        self.assertIn("- **re-plan** — the entry sits inside this PR's scope", self.triage)
        # The DoD is `facts.dod`; `facts.plans` carries only plan comments.
        self.assertIn("or the issue's Definition of done in `facts.dod`", self.triage)
        self.assertIn("plus `facts.plans` and `facts.dod`", self.reference)
        self.assertIn("a list of required behaviour is a minimum, a definition is not", self.triage)
        self.assertIn("two fixes fit, each serving a different intent", self.triage)
        # Out of scope files instead, carrying the open question to the follow-up's planner.
        self.assertIn("An entry **outside** this PR's scope that also needs an intent decided files too",
                      self.triage)
        # A Re-plan answer is recorded as `apply` (the closed disposition set is unchanged) and forces
        # the planner route.
        self.assertIn("**Re-plan** → `apply`, the note prefixed `operator: re-plan —`", self.triage)
        self.assertIn("the operator answered **Re-plan** on any entry", self.reference)
        ledger = re.sub(r"\s+", " ", (REPO_ROOT / "skills" / "_shared" / "polish-ledger.md").read_text(encoding="utf-8"))
        self.assertIn("**The operator decides every disposition.**", ledger)
        planner = re.sub(
            r"\s+", " ",
            (REPO_ROOT / "skills" / "planner" / "references" / "revise-reconciliation.md").read_text(encoding="utf-8"),
        )
        self.assertIn("for an entry whose note leads `operator: re-plan`, settle the decision the note names", planner)

    def test_an_untick_soft_reject_routes_straight_to_the_planner(self):
        rubric = re.sub(
            r"\s+", " ", (SKILL_DIR / "references" / "handoff-renderings.md").read_text(encoding="utf-8")
        )
        self.assertIn("**When the verdict included an S4-untick**, re-route to **`/ctrl-freaq:planner revise #<issue>`**", rubric)
        for name in ("standard.md", "story.md", "epic-integration.md"):
            text = re.sub(r"\s+", " ", (PLAYBOOKS_DIR / name).read_text(encoding="utf-8"))
            self.assertIn("S4-untick, a plan-changing polish `apply`, or a thread reply", text, name)

    def test_the_apply_criteria_have_one_owner(self):
        # CLAUDE.md "render, don't restate": the list lives in the shared ledger contract only.
        ledger = (REPO_ROOT / "skills" / "_shared" / "polish-ledger.md").read_text(encoding="utf-8")
        self.assertIn("## Apply criteria", ledger)
        self.assertIn('"Apply criteria"', self.reference)
        for copy in ("wrong user-visible copy", "a misleading name on a public"):
            self.assertNotIn(copy, self.reference)
            self.assertNotIn(copy, self.triage)
            fix_round = (REPO_ROOT / "skills" / "resolver" / "references" / "review-fix-round.md").read_text(encoding="utf-8")
            self.assertNotIn(copy, fix_round)

    def test_an_apply_criteria_ledger_entry_is_a_resolver_miss(self):
        self.assertIn("an `apply`-criteria entry here is a **resolver miss**", self.reference)

    def test_the_evaluator_is_the_backstop_after_finalisation(self):
        # 4.30.0: the resolver's finalisation asks first; the evaluator never re-asks a decided entry,
        # except an `apply` left unfixed (which it re-proposes so the operator can release it).
        self.assertIn("This step is the **backstop**", self.reference)
        self.assertIn("is the operator's answer and is not asked about again here, except §1's unfixed "
                      "`apply` and a `premise false` drop whose claim holds at head", self.reference)
        self.assertIn("confirmed on an earlier run or at the resolver's finalisation", self.reference)
        self.assertIn("This is the backstop: the resolver's finalisation already put every entry", self.spine)
        # PR #71 review: "never re-asked" contradicted the unfixed-`apply` re-proposal one sentence later.
        self.assertIn("a decided entry is not re-asked unless it is an unfixed `apply` or a `premise false` drop "
                      "whose claim holds at head", self.spine)
        self.assertNotIn("a decided entry is never re-asked", self.spine)
        # The one proposal rule that differs: the evaluator keeps the Apply criteria; finalisation's bar
        # is lower, because applying there costs a fix round, not a revision session.
        self.assertIn("### The apply bar", self.triage)
        self.assertIn("- **Resolver finalisation** — the entry is in this PR's scope", self.triage)
        self.assertIn("- **Evaluator** — the entry meets", self.triage)
        # PR #71 review: the evaluator's bar lives once, in the shared file; the reference points at it and
        # keeps only what is the evaluator's own — the resolver-miss naming and the tiering diagnostic.
        self.assertIn("the evaluator's rule in that file's \"The apply bar\"", self.reference)
        self.assertNotIn("Applying here costs a whole resolver session", self.reference)
        self.assertIn("When most `open` entries reaching this step meet the criteria, the resolver's in-loop "
                      "defect/polish line is misplaced", self.reference)
        # `file` still files after the merge, whoever recorded it.
        self.assertIn("Filed **after the merge**", self.triage)

    def test_a_premise_false_drop_is_rechecked_at_head(self):
        # PR #71 review: the docs said the evaluator re-checks every decided entry, but a `drop` the code's
        # author proposed at finalisation (`premise false at head`) was never looked at again.
        self.assertIn("Next, every `drop` whose note leads `premise false at head`", self.reference)
        self.assertIn("re-propose the entry at §2, marked `premise disputed: <what holds>`", self.reference)
        self.assertIn("a re-proposed drop says `premise disputed: <what holds>`", self.reference)
        self.assertIn("a `premise false` drop whose claim it finds holds at head (`premise disputed`)", self.triage)
        for path in (REPO_ROOT / "skills" / "_shared" / "polish-ledger.md", REPO_ROOT / "CLAUDE.md",
                     REPO_ROOT / "docs" / "architecture.md"):
            text = " ".join(path.read_text(encoding="utf-8").split())
            self.assertNotIn("re-checks every decided entry", text, path.name)
            self.assertIn("`premise false` `drop`", text, path.name)

    def test_no_positional_cross_references(self):
        # CLAUDE.md "Stable §-anchors over positional cross-references".
        for text in (self.reference, self.triage):
            self.assertNotRegex(text, r"\((?:above|below)\)|`[^`]+` (?:above|below)\b")

    def test_dispositions_are_written_once_after_the_answer(self):
        self.assertIn("gh_persist.py edit-pr-body", self.reference)
        self.assertIn("the ledger never records a decision the operator overrode", self.reference)

    def test_the_route_depends_on_whether_the_plan_changes(self):
        self.assertIn("**`/ctrl-freaq:planner revise #<issue>`**", self.reference)
        self.assertIn("**`/ctrl-freaq:resolver #<issue>`**", self.reference)
        rubric = re.sub(
            r"\s+", " ", (SKILL_DIR / "references" / "handoff-renderings.md").read_text(encoding="utf-8")
        )
        self.assertIn("Operator-confirmed polish `apply` (S5.5)", rubric)
        self.assertIn("/ctrl-freaq:planner revise #142", rubric)

    def test_the_disposition_write_dry_runs_through_edit_pr_body(self):
        proc, env = _run_persist(
            ["edit-pr-body", "octo/widgets", "287", "@BODY@", "--dry-run"],
            body_text="Fixes #142\n\n## Polish\n- P1.1 · file — x — a.py / f — later\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(env.get("op"), "edit-pr-body")
        self.assertIn("pr edit", env["would_run"])

    def test_residual_filing_folds_in_ledger_file_entries(self):
        for name in ("standard.md", "story.md", "epic-integration.md"):
            text = re.sub(r"\s+", " ", (PLAYBOOKS_DIR / name).read_text(encoding="utf-8"))
            self.assertIn("`file`", text, "%s must file the ledger's `file` entries" % name)


class EpicResidueTests(unittest.TestCase):
    """4.37.0: the epic-integration verdict reads what the epic's merged story PRs deferred from
    `facts.epic.residue` (prep reads the story PR bodies), and a deferral that maps to the epic's DoD
    re-routes to the slicer — the only way work reaches the epic branch, and the only edge writer."""

    def setUp(self):
        self.epic = re.sub(
            r"\s+", " ", (PLAYBOOKS_DIR / "epic-integration.md").read_text(encoding="utf-8")
        )

    def test_the_route_reads_the_residue_fact_never_the_story_prs(self):
        self.assertIn("`facts.epic.residue`", self.epic)
        self.assertIn("never fetch a story PR to find them", self.epic)
        for key in ("open_follow_ups", "file_polish", "undecided_polish", "story_prs_path"):
            self.assertIn("`%s`" % key, self.epic, key)
        self.assertIn("`EPIC_RESIDUE_UNAVAILABLE`", self.epic)

    def test_unlinked_entries_are_re_checked_at_head_so_a_shipped_fix_never_re_blocks(self):
        # Review of #79: a `file` entry adopted as a story and shipped kept mapping to the DoD and
        # soft-rejected every integration run. Linked entries resolve through their issue; unlinked
        # ones are re-checked at head.
        self.assertIn("one whose claim no longer holds at head is resolved", self.epic)
        self.assertIn("A ledger entry the issue `covers` is judged through it, once", self.epic)
        self.assertIn("follow-ups-section.md", self.epic)
        self.assertNotIn("handoff below", self.epic)

    def test_the_writers_render_the_shared_follow_ups_contract(self):
        shared = REPO_ROOT / "skills" / "_shared" / "follow-ups-section.md"
        self.assertTrue(shared.is_file())
        for path in (
            PLAYBOOKS_DIR / "standard.md",
            REPO_ROOT / "skills" / "resolver" / "references" / "follow-up-tracking.md",
        ):
            self.assertIn("follow-ups-section.md", path.read_text(encoding="utf-8"), path.name)
        for name in ("standard.md", "story.md", "epic-integration.md"):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("`Filed:`", text, name)
            self.assertNotIn("*Filed* entries", text, name)

    def test_story_ledgers_are_judged_not_re_asked(self):
        self.assertIn("S5.5 adjudicates this PR's own ledger only", self.epic)
        self.assertIn("and `drop` entries were decided at their story's merge: context, never a block", self.epic)

    def test_a_mapped_deferral_re_routes_to_the_slicer(self):
        self.assertIn("`/ctrl-freaq:slicer #<epic>`", self.epic)
        rubric = (SKILL_DIR / "references" / "handoff-renderings.md").read_text(encoding="utf-8")
        self.assertIn("**Re-route → `/ctrl-freaq:slicer #<epic> adopt #<issue>`**", rubric)
        shared = (REPO_ROOT / "skills" / "_shared" / "handoff-format.md").read_text(encoding="utf-8")
        self.assertIn("- evaluator → slicer (an epic-integration soft-reject on a story's deferral", shared)

    def test_residual_filing_weaves_into_the_body_on_every_route(self):
        # A comment is invisible to the residue scan, which reads PR bodies only.
        for name in ("standard.md", "story.md", "epic-integration.md"):
            text = re.sub(r"\s+", " ", (PLAYBOOKS_DIR / name).read_text(encoding="utf-8"))
            self.assertIn("`## Follow-ups`", text, name)
            self.assertNotIn("post the URLs as a brief PR comment", text, name)


class FollowUpBatchTests(unittest.TestCase):
    """The residual-filing step spawns its sub-agents concurrently — the items are independent, and
    serial spawning costs the drafter's full round-trip each in series."""

    def test_standard_route_spawns_the_follow_up_batch_in_one_message(self):
        standard = re.sub(
            r"\s+", " ", (PLAYBOOKS_DIR / "standard.md").read_text(encoding="utf-8").replace("**", "")
        )
        self.assertIn("spawned together in one message", standard)


class ArtifactRenderingByteCompatTests(unittest.TestCase):
    """The artifact renderings this skill writes must diff clean against the S1-captured v1 examples
    (S7 DoD box 2). Parses the fenced template block out of each reference and the matching example
    and asserts byte-equality of the template body."""

    EXAMPLES_DIR = REPO_ROOT / "docs" / "specs" / "examples"
    REFERENCES_DIR = SKILL_DIR / "references"

    @staticmethod
    def _fenced_blocks(path, fence):
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

    def test_health_cache_template_byte_compatible(self):
        mine = self._fenced_blocks(self.REFERENCES_DIR / "health-cache-comment.md", "````")[0]
        example = self._fenced_blocks(self.EXAMPLES_DIR / "pr-evaluator-health-cache.md", "````")[0]
        self.assertEqual(mine, example, "health-cache rendering diverges from the S1 example")

    def test_delivery_log_template_byte_compatible(self):
        """#41: the log is now a SET of per-story comments plus a legacy tier, so this reference
        carries TWO fences. Both are pinned, and both are selected by PREDICATE rather than by index
        — an index-based pin silently starts comparing the wrong fence the moment the document is
        reordered, and the tempting "fix" is then to reorder the document back. Same technique as
        `test_handoff_standard_terminal_byte_compatible` below.
        """
        mine = self._fenced_blocks(self.REFERENCES_DIR / "epic-delivery-log.md", "```")

        per_story = next((b for b in mine if ":v2:story:" in b), None)
        self.assertIsNotNone(per_story, "the per-story entry template must be present")
        example = self._fenced_blocks(
            self.EXAMPLES_DIR / "epic-delivery-log-per-story.md", "```"
        )[0]
        self.assertEqual(
            per_story, example, "per-story delivery-log rendering diverges from its example"
        )

        # The legacy tier is read forever with no backfill, so its rendering stays pinned to the
        # S1 example that defined it — that file is the historical record and is never edited.
        legacy = next((b for b in mine if b.startswith("<!-- epic-delivery-log:v1 -->")), None)
        self.assertIsNotNone(legacy, "the legacy tier's template must remain documented")
        legacy_example = self._fenced_blocks(self.EXAMPLES_DIR / "epic-delivery-log.md", "```")[0]
        self.assertEqual(
            legacy, legacy_example, "legacy delivery-log rendering diverges from the S1 example"
        )

    def test_handoff_standard_terminal_byte_compatible(self):
        # v3: pinned against handoff-evaluator-v3.md (the operator-owned-workspace rendering,
        # which supersedes the S1 capture; that file stays unedited as the v2 historical record).
        example = self._fenced_blocks(self.EXAMPLES_DIR / "handoff-evaluator-v3.md", "```")[0]
        mine_blocks = self._fenced_blocks(self.REFERENCES_DIR / "handoff-renderings.md", "```")
        match = next(
            (b for b in mine_blocks if b.startswith("## Handoff") and "Add CSV export (#142) · merged" in b),
            None,
        )
        self.assertIsNotNone(match, "the standard-terminal handoff shape must be present")
        self.assertEqual(
            match, rename_namespace(example), "standard-terminal handoff diverges from the S1 example"
        )


class WorkspaceModelV3Tests(unittest.TestCase):
    """v3 workspace-model pins: no cleanup fence removes the worktree (the evaluator runs inside
    it), and the terminal/cleanup language hands the operator workspace-close."""

    def test_no_remove_work_invocation_anywhere(self):
        skill_dir = REPO_ROOT / "skills" / "evaluator"
        for path in sorted(skill_dir.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "remove --work", text,
                "%s still invokes worktree removal — that is workspace-close's, exclusively"
                % path.name,
            )

    def test_terminal_and_cleanup_language_hand_off_workspace_close(self):
        renderings = (REPO_ROOT / "skills" / "evaluator" / "references" / "handoff-renderings.md").read_text(encoding="utf-8")
        self.assertIn("worktree retained — release with workspace-close", renderings)
        self.assertIn("/ctrl-freaq:workspace-close", renderings)

    def test_router_names_workspace_mismatch(self):
        router = (REPO_ROOT / "skills" / "evaluator" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("WORKSPACE_MISMATCH", router)


def _fenced_lines(text):
    """Every line inside a ``` fence (a handoff's indented next-action block sits inside one)."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside:
            lines.append(line)
    return lines


def _between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i + len(start))]


class MergeGuardTests(unittest.TestCase):
    """#93: the merge lands only the commit it gated, against the base it lands on. The offline
    suite runs no `gh pr merge`, so the prompt half is pinned here and the fact half in
    tests/test_prep_evaluator.py."""

    SPINE = PLAYBOOKS_DIR / "evaluate-spine.md"
    RENDERINGS = SKILL_DIR / "references" / "handoff-renderings.md"
    HANDOFF_FORMAT = REPO_ROOT / "skills" / "_shared" / "handoff-format.md"

    def test_every_fenced_gh_pr_merge_is_pinned_to_the_gated_head(self):
        # A scan, so a third merge form added later cannot miss the pin.
        found = 0
        for path in SKILL_DIR.rglob("*.md"):
            for line in _fenced_lines(path.read_text(encoding="utf-8")):
                if re.search(r"\bgh\s+pr\s+merge\b", line):
                    found += 1
                    self.assertIn(
                        "--match-head-commit", line,
                        "unpinned gh pr merge in %s: %r" % (path.relative_to(REPO_ROOT), line),
                    )
        self.assertGreaterEqual(found, 3)  # the spine's two forms + the BLOCKED rendering

    def test_s7_merge_refreshes_with_the_gated_head_and_reads_the_guard(self):
        merge = _between(self.SPINE.read_text(encoding="utf-8"), "**S7-merge**", "**S7-skip**")
        self.assertIn("--gated-head", merge)
        self.assertIn("merge_guard.skip_reason", merge)
        self.assertIn("on the `ask` path too", merge)
        self.assertIn("or the operator deferred the merge", " ".join(merge.split()))
        # The non-zero-exit recovery reads the fact, never gh's stderr.
        self.assertIn("merge_guard.head_moved", merge)
        # A thread dismissal recorded this run is checked by script before the merge (#145 cold read).
        self.assertIn("`dismissed` entry's `thread.confirmed` is not `true`", " ".join(merge.split()))

    def test_a_guard_reason_outranks_blocked_and_a_deferred_merge(self):
        # The audit override on #93: a BLOCKED or deferred merge must not reach the print-a-merge
        # shape while the guard holds, or a head missing the base tip gets a merge command.
        spine = self.SPINE.read_text(encoding="utf-8")
        gate = _between(spine, "**S7-gate**", "**S7-merge**")
        self.assertIn("--refresh --gated-head", gate)
        self.assertIn("`merge_guard.skip_reason` outranks `deferred`", gate)
        # The deferred path reads a fresh guard at S7-merge's refresh, not step 1's stale one.
        self.assertIn('Approve, or the "Other" deferred-merge → S7-merge', gate)
        self.assertIn("named, never `deferred`", gate)
        # A guard skip found before the post posts no review — no card, no APPROVE on a head
        # nobody gated (the plan's "A guard skip found before S7-post posts no review").
        self.assertIn("`merge_guard.skip_reason` set → S7-skip now, with no card and no review post", gate)
        skip = _between(spine, "**S7-skip**", "## Return to the routed playbook")
        order = [skip.index(token) for token in (
            "merge_guard.skip_reason", "`head-moved`", "`base-moved`", "`DIRTY`", "`BLOCKED`",
            "`deferred`",
        )]
        self.assertEqual(order, sorted(order), "S7-skip must rank the guard ahead of the rest")
        self.assertIn("print **no** `gh pr merge`", skip)
        # The route is the routed playbook's (epic-integration splits it), never the spine's.
        self.assertNotIn("re-gate route", skip)

    def test_a_head_moved_during_the_ci_wait_is_skipped_not_re_gated(self):
        ci = _between(self.SPINE.read_text(encoding="utf-8"), "**S3.2 CI class.**", "**S3.3")
        self.assertNotIn("re-enter S3.1", ci)
        self.assertIn("--gated-head", ci)
        self.assertIn("head-moved", ci)

    def test_the_closed_set_grows_by_the_two_guard_reasons(self):
        text = self.HANDOFF_FORMAT.read_text(encoding="utf-8")
        merge_row = next(line for line in text.splitlines() if line.startswith("| PR `merge` |"))
        for reason in ("head-moved", "base-moved", "DIRTY", "BLOCKED", "deferred", "verdict"):
            self.assertIn("`skipped (%s)`" % reason, merge_row)
        re_routes = text[text.index("## Re-routes"):]
        self.assertIn("- evaluator → evaluator (a re-gate", re_routes)

    def test_the_rubric_re_gate_row_names_every_reason_and_a_workspace(self):
        text = self.RENDERINGS.read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if "**Re-gate → `/ctrl-freaq:evaluator" in line)
        for token in ("`DIRTY`", "`head-moved`", "`base-moved`", "`Workspace:`"):
            self.assertIn(token, row)
        self.assertIn("never a `gh pr merge`", row)

    def test_the_head_moved_fence_moves_the_branch_instead_of_fast_forwarding(self):
        # P1.1: a head moved by a force push (a rebase, or the epic drift rectification's) is no
        # fast-forward of the worktree's branch, so `merge --ff-only` failed with no next step.
        row = next(line for line in self.RENDERINGS.read_text(encoding="utf-8").splitlines()
                   if "**Re-gate → `/ctrl-freaq:evaluator" in line)
        self.assertIn("git -C <workspace-path> reset --keep origin/<branch>", row)
        self.assertNotIn("--ff-only", row)
        text = self.HANDOFF_FORMAT.read_text(encoding="utf-8")
        entry = next(line for line in text[text.index("## Re-routes"):].splitlines()
                     if line.startswith("- evaluator → evaluator (a re-gate"))
        self.assertIn("reset --keep", entry)
        self.assertNotIn("fast-forwarded", entry)

    def test_the_worked_re_gate_fence_re_runs_the_evaluator_and_prints_no_merge(self):
        text = self.RENDERINGS.read_text(encoding="utf-8")
        fences = text.split("```")[1::2]  # the odd segments are the fence interiors
        block = next(b for b in fences if "## Handoff" in b and "skipped (base-moved)" in b)
        self.assertIn("/ctrl-freaq:evaluator #", block)
        self.assertNotIn("gh pr merge", block)
        self.assertIn("**Workspace:**", block)



class SensitivePathsGateTests(unittest.TestCase):
    """#96: a PR that touches a declared sensitive path — or changes the declaration — merges only
    through the operator gate. The fact half (`config.sensitive_paths`) is pinned in
    tests/test_prep_evaluator.py; this pins that S7 reads it."""

    SPINE = PLAYBOOKS_DIR / "evaluate-spine.md"

    def test_s7_forces_ask_on_the_fact_whatever_the_type_policy(self):
        spine = self.SPINE.read_text(encoding="utf-8")
        policy = " ".join(_between(spine, "**Resolve the policy.**", "**Post timing.**").split())
        self.assertIn("`facts.config.sensitive_paths.forced_ask`", policy)
        self.assertIn("makes it `ask` whatever the type's entry says", policy)

    def test_the_gate_card_names_what_forced_it(self):
        gate = " ".join(_between(self.SPINE.read_text(encoding="utf-8"), "**S7-gate**", "**S7-merge**").split())
        self.assertIn(
            "when the opening prep's `forced_ask` (a refresh carries no `config`) — each of its `matches` "
            "pairs (or its `error`)", gate,
        )

    def test_the_forced_ask_never_jumps_ahead_of_the_merge_guard(self):
        # It defers the approval to S7-gate like any `ask`; step 1's guard skip still comes first.
        gate = _between(self.SPINE.read_text(encoding="utf-8"), "**S7-gate**", "**S7-merge**")
        self.assertLess(gate.index("merge_guard.skip_reason"), gate.index("forced_ask"))


class ReviewerSignalTests(unittest.TestCase):
    """#94: S2 reads a `/code-review` run's signal, in both shapes it can post.

    The negatives pin backticked literals because `/review.md` and `review-comment.md` paths legitimately
    contain the substring `/review`.
    """

    def test_s2_names_code_review_and_both_shapes(self):
        flat = " ".join((PLAYBOOKS_DIR / "evaluate-spine.md").read_text(encoding="utf-8").split())
        self.assertIn("**Prior `/code-review` signal.**", flat)
        self.assertIn('"Found N issues:"', flat)
        self.assertIn("the bundled skill's inline PR comments (only with `--comment`)", flat)
        self.assertIn("its ultra review's single findings comment (only with `--post`)", flat)
        self.assertIn("The resolver's review loop passes none — its record is the loop comment", flat)

    def test_no_evaluator_file_names_the_retired_command(self):
        for path in sorted(SKILL_DIR.rglob("*.md")):
            text = " ".join(path.read_text(encoding="utf-8").split())
            self.assertNotIn("`/review`", text, path.name)
            self.assertNotIn("/review` command", text, path.name)


class SecurityReviewStepTests(unittest.TestCase):
    """#95: S4.5 runs the security pass before the verdict, and an open entry blocks APPROVE."""

    def test_the_step_sits_between_s4_and_the_verdict(self):
        spine = (PLAYBOOKS_DIR / "evaluate-spine.md").read_text(encoding="utf-8")
        self.assertLess(spine.index("## S4 — "), spine.index("## S4.5 — Security review"))
        self.assertLess(spine.index("## S4.5 — Security review"), spine.index("## S5 — Verdict"))
        self.assertIn("../../_shared/security-review.md", spine)

    def test_both_verdict_conditions_name_the_open_entry(self):
        flat = " ".join((PLAYBOOKS_DIR / "evaluate-spine.md").read_text(encoding="utf-8").split())
        approve = flat[flat.index("**APPROVE** when"):flat.index("**COMMENT (soft-reject)** when")]
        comment = flat[flat.index("**COMMENT (soft-reject)** when"):flat.index("Compose the review body")]
        self.assertIn("no `## Security review` entry is `open`", approve)
        # Audit F1/F4 + review round 1: an unparsed line or an incomplete pass blocks too.
        self.assertIn("(an unparsed line or a pass that did not complete counts as one;", approve)
        self.assertIn("an open security finding (S4.5), as APPROVE counts it", comment)
        # #145: an entry whose dismiss proposal waits on its thread holds at the Threads card instead.
        self.assertIn("an entry whose dismiss proposal waits on its thread blocks through that thread, never a "
                      "soft-reject", approve)
        self.assertIn("unless its dismiss proposal waits on its thread", comment)



class ReviewThreadsGateTests(unittest.TestCase):
    """#145 phase 3: S4.6 derives, posts and gates on review threads; the `## PR summary` / `## Review
    items` blocks render before the gate; the `Security` card keeps its two fallback cases."""

    REFS = SKILL_DIR / "references"

    def setUp(self):
        self.spine_raw = (PLAYBOOKS_DIR / "evaluate-spine.md").read_text(encoding="utf-8")
        self.spine = " ".join(self.spine_raw.split())
        self.ref_path = self.REFS / "review-threads.md"
        self.ref = " ".join(self.ref_path.read_text(encoding="utf-8").split())
        self.summary = " ".join((self.REFS / "pr-summary.md").read_text(encoding="utf-8").split())
        self.router = " ".join(ROUTER.read_text(encoding="utf-8").split())

    def _fenced(self, path):
        lines, inside = [], False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("```"):
                inside = not inside
                continue
            if inside:
                lines.append(line)
        return "\n".join(lines)

    def test_s4_6_sits_between_s4_5_and_s5_and_points_at_its_reference(self):
        self.assertLess(self.spine.index("## S4.5 — Security review"), self.spine.index("## S4.6 — Review threads"))
        self.assertLess(self.spine.index("## S4.6 — Review threads"), self.spine.index("## S5 — Verdict"))
        s46 = self.spine.split("## S4.6 — Review threads", 1)[1].split("## S5 — Verdict", 1)[0]
        for token in ("../references/review-threads.md", "../references/pr-summary.md", "`Threads` card",
                      "No prep refresh between `review-post` and the gate except **Re-check**"):
            self.assertIn(token, s46)

    def test_a_review_post_that_did_not_land_holds_approve(self):
        # Cold read, #145 phase 4: a failed post (or a needs_decision envelope) leaves the flagged code with
        # no visible thread, and a stranded pending review is invisible to the reader, so it must hold APPROVE.
        self.assertIn("`needs_decision` envelope (`BODY_TOO_LONG`, `EMPTY_BODY_FILE`, `AUTH_REQUIRED`) is SKILL.md's \"Decision card rule\"", self.ref)
        self.assertIn("Any other non-zero exit, or a decision left unanswered, means the threads did not land", self.ref)
        self.assertIn("`review-post` did not return an `ok` envelope (§4)", self.ref)
        self.assertIn("every `new` thread's `review-post` returned `ok`", self.spine)

    def test_a_body_sourced_security_reason_is_quoted_only_on_trusted_terms(self):
        self.assertIn("is the pipeline's proposal only when `facts.security.dismissals_trusted`", self.ref)

    def test_an_out_of_plan_reason_is_quoted_only_when_trusted(self):
        self.assertIn("when `facts.out_of_plan.reasons_trusted`", self.ref)
        self.assertIn("quoted only when `facts.out_of_plan.reasons_trusted`", self.summary)

    def test_a_decided_deviation_gets_no_plan_thread_only_when_trusted(self):
        # #181: S4 item 5 leaves a card-decided deviation out of plan-threads.txt, on the #144 trust terms.
        self.assertIn("only when `facts.decided_deviations.trusted` holds", self.spine)
        self.assertIn("Leave out a deviation you match to a `facts.decided_deviations` entry when "
                      "`facts.decided_deviations.trusted` holds", self.ref)
        self.assertIn("Untrusted, it keeps its line.", self.ref)
        self.assertIn("one line per decided deviation §2 left out of `plan-threads.txt`", self.ref)
        self.assertIn("each deviation S4 item 5 passed as decided on a resolver card (`facts.decided_deviations`, "
                      "only when `trusted`)", self.summary)
        router = " ".join((REPO_ROOT / "skills" / "evaluator" / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("`decided_deviations` (the `## Decided deviations` record;", router)

    def test_the_reference_runs_the_scripts_in_fences(self):
        fenced = self._fenced(self.ref_path)
        self.assertIn("scripts/review_threads.py flags", fenced)
        self.assertIn("scripts/gh_persist.py review-post", fenced)
        self.assertIn("scripts/blast_radius.py assess", fenced)
        self.assertIn("--head \"<merge_guard.gated_head>\"", fenced)

    def test_s4_6_runs_narrow_then_the_triage_before_flags(self):
        # #163: narrow stages the out-of-plan files the triage reads, and flags reads the triage's verdict.
        fenced = self._fenced(self.ref_path)
        self.assertIn("scripts/review_threads.py narrow", fenced)
        self.assertLess(fenced.index("scripts/review_threads.py narrow"), fenced.index("scripts/review_threads.py flags"))
        self.assertIn('--triage "<facts.scratch>/out-of-plan-triage.md"', fenced)
        self.assertIn('--splits "<facts.scratch>/splits.txt"', fenced)
        self.assertIn("--reasons-trusted <facts.out_of_plan.reasons_trusted>", fenced)
        self.assertIn("[`out-of-plan-triage-prompt.md`](out-of-plan-triage-prompt.md)", self.ref)
        self.assertIn("../references/out-of-plan-triage-prompt.md", self.spine)

    def test_the_triage_prompt_is_isolated_and_writes_nothing(self):
        prompt_path = REPO_ROOT / "skills" / "evaluator" / "references" / "out-of-plan-triage-prompt.md"
        prompt = prompt_path.read_text(encoding="utf-8")
        fenced = self._fenced(prompt_path)
        self.assertNotRegex(fenced, r"gh +(issue|pr) +(create|edit|comment|review|close|reopen)")
        self.assertIn("## Triage", prompt)
        self.assertIn("code: AMBIGUOUS", prompt)
        self.assertIn("- routine · <path> — <test|fixture|fallout>: <evidence>", prompt)
        self.assertIn("cannot call `AskUserQuestion`, and never writes to GitHub", prompt)
        self.assertIn("Do not include the blast-radius judgment's", prompt)

    def test_no_raw_graphql_in_any_skill_fence(self):
        for path in sorted((REPO_ROOT / "skills").rglob("*.md")):
            self.assertNotIn("gh api graphql", self._fenced(path), path)

    def test_the_plan_choice_and_the_no_plan_form(self):
        self.assertIn("`present` and not `ambiguous`", self.ref)
        self.assertIn("An `ambiguous` plan has no `body_path` and is never passed", self.ref)
        self.assertIn("every **epic-integration PR**: no judgment dispatch", self.ref)
        self.assertIn("with no `--plan` and no `--judgment`", self.ref)
        self.assertIn("never from `facts.config.sensitive_paths`, which is full-run only", self.ref)

    def test_the_gate_and_its_card(self):
        for token in (
            "**No APPROVE** while `facts.review_threads.available` is false",
            "a thread is not gating when it is `replaced` (a trusted successor replaced it), when this run's posted "
            "`new[].supersedes` names it, or when `flags` listed it in `retired`",
            "nor is an `advisory` batch — the read's field, or a `new` entry's posted this run — while "
            "`unreviewed` or `superseded` (#181; a `changes-requested` advisory batch gates)",
            "**Soft-reject** when any gating thread, by the gating rule above, is `changes-requested`",
            "or to the planner when a reply would reverse a `## Architecture decisions` entry",
            "render the `Threads` judgment card",
            "**Re-check** re-runs prep with `--refresh --gated-head <gated head>`",
            "reusing `assess.json`",
            "**No prep refresh between `review-post` and the gate**",
        ):
            self.assertIn(token, self.ref)
        self.assertIn("the `Threads` card (S4.6: **Re-check** / **End session**)", self.router)
        self.assertIn("S4.6's **Re-check** among them", self.router)
        self.assertIn("**Never resolve or unresolve a review thread**", self.router)

    def test_the_summary_renders_before_the_card_and_after_re_check(self):
        five = self.ref.split("## 5. Summary, then the gate", 1)[1]
        self.assertLess(five.index("[`pr-summary.md`](pr-summary.md)"), five.index("`Threads` judgment card"))
        self.assertIn("re-renders the summary, and gates again", five)
        self.assertIn("after posting and before the `Threads` card or the soft-reject, and again after every **Re-check**",
                      self.summary)

    def test_the_summary_contract_names_its_sections_and_item_grammar(self):
        order = [self.summary.index(x) for x in ("**What changed:**", "**By phase:**",
                                                  "**Decisions & deviations:**", "**Risk:**")]
        self.assertEqual(order, sorted(order))
        self.assertIn("- <ID> · <kind> · <state> — <one line> — <anchor> · <link>", self.summary)
        for family in ("`F<n>`", "`O<n>`", "`SEC-<n>`", "`P<phase>.<seq>`"):
            self.assertIn(family, self.summary)

    def test_thread_dismissals_and_the_security_cards_two_cases(self):
        self.assertIn("operator: <reason> · thread: <root comment url>", self.ref)
        # SEC-1/4/5: only a resolution-grade `dismiss:` reply on a thread whose `Claim:` line and path match the
        # entry is recorded.
        ref = " ".join(self.ref.split())
        self.assertIn("whose `dismiss_reason` is set — a `dismiss:` reply by the viewer or a resolution-grade author", ref)
        self.assertIn("whose `claim` equals the entry's claim and whose `path` is the entry's anchor path", ref)
        self.assertIn("`Claim: <claim>`, copied verbatim from the thread's `claims`", self.ref)
        # #181: the script writes how to answer (the template's closing line), so the instruction matches how the
        # answer is read; the rendering writes none of its own.
        closing = review_threads.CLOSINGS["security"]
        self.assertIn("reply `dismiss: <reason>`, then resolve the thread", closing)
        self.assertIn("leave it unresolved", closing)
        self.assertIn("Write no link and no closing instruction of your own.", ref)
        self.assertNotIn("then resolve, to dismiss", self.ref)
        shared = " ".join((REPO_ROOT / "skills" / "_shared" / "security-review.md").read_text(encoding="utf-8").split())
        self.assertIn("The `Security` card renders for **exactly two cases**", shared)

    def test_the_verdict_counts_threads(self):
        s5 = self.spine.split("## S5 — Verdict", 1)[1].split("## S5.5", 1)[0]
        self.assertIn("no review thread gates (S4.6", s5)
        # #163 (R2.14, R2.16): APPROVE and COMMENT both apply §5's gating rule, which reads `replaced`.
        self.assertIn("`superseded` that gates by `references/review-threads.md` §5's gating rule — not `replaced`", s5)
        self.assertIn("a `changes-requested` thread or an `operator-thread` without `pending_reply` that gates by that "
                      "same §5 gating rule (S4.6", s5)

    def test_every_gate_reads_replaced_never_a_bare_successor_name(self):
        # #163 (R2.14): a named thread a person commented on after its successor still gates.
        evaluator = REPO_ROOT / "skills" / "evaluator"
        texts = {
            "spine": self.spine,
            "review-threads": self.ref,
            "review-comment": (evaluator / "references" / "review-comment.md").read_text(encoding="utf-8"),
            "pr-summary": self.summary,
            "review-fix-round": (REPO_ROOT / "skills" / "resolver" / "references" / "review-fix-round.md")
            .read_text(encoding="utf-8"),
            "contract": (REPO_ROOT / "skills" / "_shared" / "review-threads.md").read_text(encoding="utf-8"),
        }
        for name, text in texts.items():
            flat = " ".join(text.split())
            self.assertIn("replaced", flat, name)
            self.assertNotIn("gates nothing, whatever its state", flat, name)
            self.assertNotIn("successor names gates nothing", flat, name)

    def test_split_requests_come_from_the_whole_comment_history(self):
        # #163 (R2.15): `reply_reason` resets once the resolver replies; a mixed reply defers the split.
        ref = " ".join(self.ref.split())
        self.assertIn("read from **every** such comment in that thread's `comments`", ref)
        self.assertIn("never from `reply_reason` alone, which resets once the resolver replies", ref)
        self.assertIn("while the head is `changes-requested` and its latest trusted human comment asks for more than "
                      "a split, write no line for it this run", ref)


if __name__ == "__main__":
    unittest.main()
