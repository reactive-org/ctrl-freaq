"""Offline tests for the review skill (#171): a standalone tool that records each `Thread` card answer as a
decision reply in its review thread.

Standalone-tool gates in `tests/test_review_report_routing.py`'s shape: router structure and frontmatter
(`disable-model-invocation: true`), the prep fence, the contract-token grep set, the fence-scoped raw-write /
ref-arithmetic gates. Then the trust trade this tool rests on (`skills/_shared/review-threads.md` §10): the
only `gh_persist.py` op it names is `review-reply`, every such fence passes `--decision` and `--head`, the head
is the prep's `review.head`, and the card's two option sets keep **Accept** off a security finding. Last, the
summary posture: plain, no `## Handoff`, the resolver's re-entry on the issue.
"""

import re
import unittest
from pathlib import Path

from tests.support.retired_tokens import FORBIDDEN_CONTRACT_TOKENS, retired_name_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "review"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
FLOW = "review-flow.md"

# A new tool with no v1 counterpart: a growth ceiling only (the prd §10 bar is a pipeline-stage bar).
LOADED_SET_CEILING = 250


def _iter_md(dir_path):
    yield from sorted(dir_path.rglob("*.md"))


def _fences(path):
    """Each fenced block's text, newline-joined."""
    blocks, current = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def _flat(text):
    return re.sub(r"\s+", " ", text)


class RouterStructureTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")

    def test_router_exists_and_frontmatter(self):
        head = "\n".join(self.router_text.splitlines()[:6])
        self.assertIn("name: review\n", head)
        self.assertIn("disable-model-invocation: true", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)
        self.assertIn("/ctrl-freaq:review #<N>", head)

    def test_exactly_one_playbook_on_disk(self):
        self.assertEqual({p.name for p in PLAYBOOKS_DIR.glob("*.md")}, {FLOW})
        self.assertFalse((SKILL_DIR / "references").exists())

    def test_router_points_at_the_single_flow(self):
        self.assertIn("(playbooks/review-flow.md)", self.router_text)
        self.assertIn("No mode fork", self.router_text)

    def test_the_four_router_sections_in_order(self):
        headings = re.findall(r"^## (\d)\. (\w+)", self.router_text, re.MULTILINE)
        self.assertEqual([h[1] for h in headings], ["Prep", "Route", "Invariants", "Summary"])

    def test_loaded_set_under_ceiling(self):
        flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.assertLessEqual(len(self.router_text.splitlines()), 150)
        self.assertLessEqual(len(self.router_text.splitlines()) + len(flow.splitlines()), LOADED_SET_CEILING)

    def test_the_prep_is_invoked_by_plugin_path(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/prep_review.py <N> <owner/repo>", self.router_text)


class ContractTokenGateTests(unittest.TestCase):
    def test_no_forbidden_contract_tokens(self):
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(FORBIDDEN_CONTRACT_TOKENS.search(line), "%s:%d" % (path.name, i))

    def test_no_v1_skill_names(self):
        for path in _iter_md(SKILL_DIR):
            self.assertEqual(retired_name_hits(path.read_text(encoding="utf-8")), [], path.name)

    def test_no_raw_writes_or_ref_arithmetic_in_fences(self):
        banned = re.compile(
            r"\bgh\s+(issue|pr)\s+(create|edit|comment|review|close|reopen|merge)\b|\bgh\s+api\b[^\n]*\bDELETE\b"
            r"|git\s+show\s+[^\s]+:|git\s+grep\s+[^-]"
        )
        for path in _iter_md(SKILL_DIR):
            for block in _fences(path):
                self.assertIsNone(banned.search(block), "%s — %r" % (path.name, block))

    def test_no_shorthand_or_stack_assumptions(self):
        stack = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)
        for path in _iter_md(SKILL_DIR):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bw/", text), path.name)
            self.assertIsNone(stack.search(text), path.name)


class DecisionReplyTests(unittest.TestCase):
    """The only write is a decision reply carrying a card answer, at the head the operator was shown."""

    def setUp(self):
        self.blocks = [b for path in _iter_md(SKILL_DIR) for b in _fences(path)]
        self.persist = [b for b in self.blocks if "gh_persist.py" in b]

    def test_the_only_persist_op_is_review_reply(self):
        self.assertTrue(self.persist, "no gh_persist.py fence found")
        ops = {m.group(1) for b in self.persist for m in re.finditer(r"gh_persist\.py\s+([a-z-]+)", b)}
        self.assertEqual(ops, {"review-reply"})

    def test_every_review_reply_passes_decision_and_the_prep_head(self):
        for block in self.persist:
            flat = _flat(block.replace("\\\n", " "))
            self.assertIn("--decision <accept|changes|dismiss>", flat)
            self.assertIn('--head "<head>"', flat)
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("`<head>` is `facts.review.head`", flow)

    def test_the_two_option_sets_keep_accept_off_a_security_item(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn('`header: "Thread"`', flow)
        self.assertIn("**Not a security item** (`security: false`): **Accept** / **Changes required** / "
                      "**File as follow-up** / **Skip for now**.", flow)
        self.assertIn("**A security item** (`security: true`): **Changes required** / **Dismiss** / "
                      "**File as follow-up** / **Skip for now**. No **Accept**", flow)

    def test_other_text_never_maps_to_a_decision(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("Text typed in Other is never mapped to a decision", flow)
        self.assertIn("**Skip for now** | — | nothing: post nothing", flow)

    def test_the_closing_reread_is_the_refresh_at_the_same_head(self):
        self.assertTrue(any('prep_review.py <N> <owner/repo> --refresh --head "<head>"' in b for b in self.blocks))

    def test_a_failed_reread_never_reports_a_recorded_answer_as_failed(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("or a re-read whose `review.available` is false", flow)
        self.assertIn("never report a recorded answer as failed for want of a re-read", flow)

    def test_the_reread_never_claims_to_see_a_push_and_names_every_way_it_fails(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("a push past `<head>` is never something it reads", flow)
        self.assertNotIn("a head the resolver pushed past `<head>` reads `superseded`", flow)
        self.assertIn("a `pr` that is `null` or names another PR", flow)
        self.assertIn("reads `superseded` on the next read at the new head (the evaluator's)", flow)

    def test_the_next_line_fires_from_the_tally_when_the_reread_fails(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("When a change request waits on the resolver, end with the one line", flow)
        self.assertIn("the tally's posted Changes required answers and the opening read's `left_out` "
                      "`changes-requested` count stand in for that", flow)

    def test_a_failed_reply_is_handled_and_a_filed_issue_is_never_lost(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("A `needs_decision` envelope (`EMPTY_BODY_FILE`, `BODY_TOO_LONG`, `AUTH_REQUIRED`) posted nothing", flow)
        self.assertIn("never a second filing", flow)
        self.assertIn("with the URL of any issue it filed", flow)
        self.assertIn("not with **File as follow-up**, which would file a second issue", flow)
        self.assertIn("If prep, or the playbook's `review-reply`, exits with `status: needs_decision`",
                      _flat(ROUTER.read_text(encoding="utf-8")))

    def test_a_follow_up_is_always_a_new_issue(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("never `revise-existing`, which edits another issue and files none", flow)
        router = _flat(ROUTER.read_text(encoding="utf-8"))
        self.assertIn("The other write is one new follow-up issue per **File as follow-up** answer", router)

    def test_an_answered_threads_brief_reads_the_answer_from_its_comments(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("the thread's trusted `comments` in `review.threads_path`, matched by `id`", flow)
        self.assertNotIn("its `reply_reason`", flow)

    def test_the_batch_brief_promises_no_split(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("never a split: every member the notes name stays in the batch", flow)

    def test_the_router_passes_bare_digits_and_names_each_exit(self):
        router = _flat(ROUTER.read_text(encoding="utf-8"))
        self.assertIn("passed as bare digits", router)
        self.assertIn("Exit 2 is a usage error", router)
        self.assertIn("Exit 1 with no envelope is a hard failure", router)


class PhaseModeTests(unittest.TestCase):
    """`--phase <P>` (#172): a value of the one flow — the pause's context first, its own summary lines."""

    def setUp(self):
        self.router = _flat(ROUTER.read_text(encoding="utf-8"))
        self.flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))

    def test_the_router_takes_phase_and_keeps_one_flow(self):
        self.assertIn("/ctrl-freaq:review #<N> --phase <P>", self.router)
        self.assertIn("prep_review.py <N> <owner/repo> [--phase <P>]", self.router)
        self.assertIn("`--phase` is a value the one flow reads", self.router)

    def test_the_pause_bodies_print_as_context_before_the_empty_items_stop(self):
        self.assertIn("under the heading `Review pause — phase <P>`", self.flow)
        self.assertIn("context that asks for no decision and gets no card", self.flow)
        self.assertLess(self.flow.index("Review pause — phase <P>"), self.flow.index("`facts.items` is empty"))
        self.assertIn("`PAUSE_REVIEW_NOT_FOUND`", self.flow)
        self.assertIn("end with §4's phase-mode lines in phase mode (the opening read deciding, as §4's fallback), "
                      "and stop", self.flow)
        # Phase mode's `left_out` counts phase <P> only; the waits rule counts every thread on the PR.
        self.assertIn("phase mode counts `review.counts` `changes-requested` (every thread), not `left_out`", self.flow)

    def test_the_phase_mode_summary_points_at_the_resolvers_card(self):
        self.assertIn("`Answer **Reviewed, changes required** on the resolver's Review card for phase <P>.` and "
                      "then the `Next:` line", self.flow)
        self.assertIn("`Answer **Reviewed, continue** or **End session** on the resolver's Review card for phase <P>.`",
                      self.flow)
        self.assertIn("in phase mode also `outside_phase`", self.flow)
        self.assertIn("while `<PR>` is open and that card was raised (else it adds no Review-card line, only the "
                      "`Next:` one)", self.flow)
        self.assertIn("in a coordinator-started session take them from the card's notes field instead", self.flow)

    def test_the_reread_passes_phase_and_the_identity_line_is_the_reviews(self):
        self.assertTrue(any('--refresh --head "<head>" [--phase <P>]' in b for b in _fences(PLAYBOOKS_DIR / FLOW)))
        self.assertIn("the identity line `review · #<N> · thread <key>` in a coordinator-started session", self.flow)


class PrdOrderTests(unittest.TestCase):
    """The binding PRD's statement of the review order matches the prep's ranks."""

    def test_prd_6_10_states_the_order_the_ranks_implement(self):
        import prep_review
        prd = _flat((REPO_ROOT / "docs" / "prd.md").read_text(encoding="utf-8"))
        self.assertIn("**§6.10 review.**", prd)
        ranks = prep_review.KEY_RANKS
        self.assertEqual(ranks["sensitive"], ranks["judgment"])
        self.assertIn("review-level raises (a sensitive path or the blast-radius judgment, one rank)", prd)
        self.assertLess(ranks["security"], ranks["sensitive"])
        self.assertLess(ranks["judgment"], ranks["plan"])
        self.assertLess(ranks["plan"], ranks["out-of-plan"])
        self.assertLess(ranks["out-of-plan"], prep_review.OPERATOR_RANK)
        self.assertLess(prep_review.OPERATOR_RANK, ranks["routine"])


class SummaryPostureTests(unittest.TestCase):
    def setUp(self):
        self.combined = _flat(ROUTER.read_text(encoding="utf-8") + " " +
                              (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))

    def test_a_plain_summary_and_no_handoff_block(self):
        self.assertIn("plain summary", self.combined)
        self.assertIn("Summary — not a `## Handoff`", self.combined)
        for path in _iter_md(SKILL_DIR):
            self.assertIsNone(re.search(r"^## Handoff\b", path.read_text(encoding="utf-8"), re.MULTILINE), path.name)

    def test_the_resolver_reentry_names_the_issue_never_continue(self):
        self.assertTrue(any("Next: /ctrl-freaq:resolver #<N>" in b for b in _fences(PLAYBOOKS_DIR / FLOW)))
        for path in _iter_md(SKILL_DIR):
            for block in _fences(path):
                self.assertNotIn("continue #", block, path.name)

    def test_it_renders_the_contract_it_writes(self):
        self.assertIn("(../_shared/review-threads.md)", ROUTER.read_text(encoding="utf-8"))
        self.assertIn("(../../_shared/review-threads.md)", (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("(../../_shared/follow-up-filing.md)", (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
