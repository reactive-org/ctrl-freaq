"""Offline tests for the review skill (#171): a standalone tool that records each `Thread` card answer as a
decision reply in its review thread.

Standalone-tool gates in `tests/test_review_report_routing.py`'s shape: router structure and frontmatter
(model-invocable, the coordinator starts it by brief), the prep fence, the contract-token grep set, the fence-scoped raw-write /
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

# A new tool with no v1 counterpart: a growth ceiling only (the prd §10 bar is a pipeline-stage bar; a standalone
# tool is measured, never force-trimmed). #245 raised it for the voice block, the orientation and the link-first item.
LOADED_SET_CEILING = 290


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
        # Do NOT "fix" this back: the coordinator starts a review session with a brief, as it starts a stage,
        # and the key makes the host refuse the skill call that brief asks for. The description scopes it instead.
        self.assertNotIn("disable-model-invocation", head)
        self.assertIn("Use it only when the operator runs that command or a coordinator's brief names it", head)
        self.assertIn("never from a resolver or evaluator session waiting at its own card", head)
        # The one pinned skill: it relays what the thread's poster wrote, and the walk must stay responsive.
        self.assertIn("model: sonnet\n", head)
        self.assertIn("effort: medium\n", head)
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


class EvaluatorWaitingTests(unittest.TestCase):
    def test_the_next_line_points_a_waiting_evaluator_at_re_check(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("Outside phase mode, put before it `If an evaluator waits at its Threads card for this PR, answer "
                      "**Re-check** there first: it routes the change request to the resolver.`", flow)


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

    def test_an_answered_threads_summary_reads_the_answer_from_its_comments(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("the thread's trusted `comments` in `review.threads_path`, matched by `id`", flow)
        self.assertNotIn("its `reply_reason`", flow)

    def test_the_batch_summary_promises_no_split(self):
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("never a split: every member the notes name stays in the batch", flow)

    def test_the_template_lines_are_verbatim_and_derived_only_when_null(self):
        # #181: the poster wrote What to decide / Impact on the root; the item derives one only when null. #245: the
        # lines are bold markdown in the message, `Impact` derived in the Impact form.
        flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.assertIn("**Impact:** <the item's `impact`, verbatim; when `null`, in the Impact form:", flow)
        self.assertNotIn("for_users", flow)
        self.assertNotIn("For users", flow)
        self.assertIn("**Decide:** <the item's `what_to_decide`, verbatim; when `null`,", flow)
        self.assertIn("An `advisory` batch comes last, its summary saying it does not hold approval while it waits", flow)
        self.assertIn("a file and its riding tests: one thread", flow)

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

    def test_the_pause_bodies_print_as_context_after_the_orientation_or_before_the_empty_items_stop(self):
        self.assertIn("under the heading `Review pause — phase <P>`", self.flow)
        self.assertIn("context that asks for no decision and gets no card", self.flow)
        # #245: with items waiting, the orientation opens the walk and the pause bodies follow it, before the agenda;
        # with none, the empty-items stop still prints them first.
        empty = self.flow.index("`facts.items` is empty → in phase mode first print each `facts.pause.reviews` body")
        self.assertLess(empty, self.flow.index("Review pause — phase <P>"))
        orientation, pause, agenda = (self.flow.index("open with the **orientation**"),
                                      self.flow.index("the pause's review bodies come next"),
                                      self.flow.index("Then the **agenda**"))
        self.assertLess(orientation, pause)
        self.assertLess(pause, agenda)
        self.assertIn("`PAUSE_REVIEW_NOT_FOUND`", self.flow)
        self.assertIn("end with §4's phase-mode lines in phase mode (the opening read deciding, as §4's fallback), "
                      "and stop", self.flow)
        # Phase mode's `left_out` counts phase <P> only; the waits rule counts every thread on the PR.
        self.assertIn("phase mode counts the `changes-requested` threads in `review.threads_path` (every thread's, not "
                      "`left_out`'s), leaving out a `replaced` or `retired` one", self.flow)

    def test_the_phase_mode_summary_points_at_the_resolvers_card(self):
        self.assertIn("`Answer **Reviewed, changes required** on the resolver's Review card for phase <P>.` and "
                      "then the `Next:` line", self.flow)
        self.assertIn("`Answer **Reviewed, continue** or **End session** on the resolver's Review card for phase <P>.`",
                      self.flow)
        self.assertIn("in phase mode also `outside_phase`", self.flow)
        self.assertIn("while `<PR>` is open (no longer open, it adds no line), each card line ending `(if that card is "
                      "still open)`", self.flow)
        self.assertIn("in a coordinator-started session take them from the card's notes field instead", self.flow)

    def test_the_reread_passes_phase_and_the_identity_line_is_the_reviews(self):
        self.assertTrue(any('--refresh --head "<head>" [--phase <P>]' in b for b in _fences(PLAYBOOKS_DIR / FLOW)))
        self.assertIn("the identity line `review · #<N> · thread <key>` in a coordinator-started session", self.flow)
        # #245: the Thread card's question is the compact core; the heading, summary, Decide / Impact lines and links
        # print before it, and again on every re-ask.
        self.assertIn("the question is the compact core", self.flow)
        self.assertIn("Steps 1–3 (and a design item's captures) print in the same message, before it", self.flow)
        self.assertIn("every re-ask (after discussion, or a \"more\") prints them again before the card", self.flow)

    def test_the_reread_names_the_asserted_worktree(self):
        # #213: the shell's cwd can drift during §2, so the re-read names the checkout the opening prep asserted.
        rereads = [b for b in _fences(PLAYBOOKS_DIR / FLOW) if '--refresh --head "<head>"' in b]
        self.assertTrue(rereads, "no closing re-read fence found")
        for block in rereads:
            self.assertIn('--cwd "<facts.workspace.path>"', block)
        self.assertIn("the shell's directory may have moved during §2", self.flow)


class ItemLayoutTests(unittest.TestCase):
    """#245: the walk opens with an orientation and agenda; each item leads with its links, the excerpt on "more"."""

    def setUp(self):
        self.flow = _flat((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))
        self.router = _flat(ROUTER.read_text(encoding="utf-8"))

    def test_the_orientation_links_the_issue_the_pr_and_its_files_view_then_an_agenda(self):
        self.assertIn("open with the **orientation**, one or two paragraphs", self.flow)
        for link in ("(`[#<N> <title>](<facts.issue.url>)`)", "(`[#<PR>](<facts.pr.url>)`)",
                     "[Files view](<facts.pr.files_url>)"):
            self.assertIn(link, self.flow)
        self.assertIn("which deserve the most attention", self.flow)
        self.assertIn("Then the **agenda**, one line per item, `<seq>. <key> · <path> — <its kind>`", self.flow)
        self.assertLess(self.flow.index("**orientation**"), self.flow.index("## 2. Each item"))

    def test_each_item_has_a_heading_a_summary_its_decide_and_impact_lines_and_links(self):
        self.assertIn("**Heading:** `### <key> · <path> · <seq> of <of>`", self.flow)
        self.assertIn("**The summary** — one paragraph", self.flow)
        self.assertTrue(any("[Thread](<url>) · [Change](<link>)" in b for b in _fences(PLAYBOOKS_DIR / FLOW)))
        self.assertIn("Both links are prep's, never a URL the session builds", self.flow)
        self.assertIn("one change link per member, `[<path>](<member link>)`", self.flow)
        self.assertIn("in place of `[Change](<link>)`", self.flow)
        order = [self.flow.index(t) for t in ("**Heading:**", "**The summary**", "**Decide, Impact and the links**",
                                             "**The excerpt, the captures, and discussion.**", "**The `Thread` card**")]
        self.assertEqual(order, sorted(order))

    def test_the_excerpt_prints_only_on_more_or_in_discussion(self):
        self.assertIn("The excerpt prints only on \"more\" (with the root text and what the plan said) or when it "
                      "helps answer a question", self.flow)
        self.assertIn("its excerpt, cut for \"more\"", self.router)

    def test_the_thread_card_carries_its_own_short_line_and_the_compact_core(self):
        core = [b for b in _fences(PLAYBOOKS_DIR / FLOW) if b.strip().startswith("<key> · <seq> of <of> · <path>")]
        self.assertEqual(len(core), 1)
        self.assertIn("Decide: <step 3's Decide line, markdown markers stripped>", core[0])
        self.assertIn('Answer "more" in Other for the evidence.', core[0])
        self.assertIn("and has no clear/close-call line", self.flow)
        # The core names the block a design item's card relies on (asking-the-user.md "The card's question").
        self.assertIn("names them: `The approved design and the build are shown above.`", self.flow)
        self.assertIn("one line after the `Decide:` line and before the closing line names them", self.flow)

    def test_the_router_carries_the_voice_and_the_description_the_new_shape(self):
        self.assertIn("## Voice", ROUTER.read_text(encoding="utf-8"))
        self.assertIn("[`../_shared/voice.md`](../_shared/voice.md)", self.router)
        self.assertNotIn("busy senior colleague", self.router)
        self.assertNotIn("leave no pipeline vocabulary undecoded", self.router)
        head = ROUTER.read_text(encoding="utf-8").split("\n---", 1)[0]
        self.assertIn("each with a summary, its Decide and Impact lines and links to the thread and the change", head)
        self.assertNotIn("a script-cut code excerpt", head)


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



class DesignThreadCardTests(unittest.TestCase):
    """#206: a design item's brief reads `threads[].design`, and the summary counts `retired` threads."""

    def setUp(self):
        self.flow = " ".join((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").split())

    def test_the_design_card_reads_the_clearance_fact(self):
        self.assertIn("A **design** item (a `design:` key, #206)", self.flow)
        self.assertIn("(`threads[].design`, never the reply text)", self.flow)
        self.assertIn("`adopted: true` → **Accept** clears it now", self.flow)
        self.assertIn("The options are a non-security item's, unchanged.", self.flow)

    def test_a_design_summary_item_is_read_before_the_design_rule(self):
        # #241: one Accept approves every listed departure, whatever `adopted` reads; a summary shows no excerpt.
        summary = "A **design summary** item (`design.summary` true, #241; every key `design-departure:`) is read first"
        self.assertIn(summary, self.flow)
        self.assertLess(self.flow.index(summary), self.flow.index("A **design** item (a `design:` key, #206)"))
        self.assertIn("**Accept** approves every departure listed at once and clears every difference it covers, "
                      "whatever the record's revision", self.flow)
        self.assertIn("A design summary item (#241) has no excerpt", self.flow)
        self.assertIn("A **design group** (`group: design`, #241) is one design item for every state it names", self.flow)
        self.assertIn("`explanations` the `D<n>` / `U<n>` / `X<n>` ids it approves, with no excerpt",
                      _flat(ROUTER.read_text(encoding="utf-8")))

    def test_a_design_item_opens_its_comparison_page_or_prints_the_links(self):
        flat = _flat(self.flow)
        self.assertIn("**A design item's captures** (#237)", self.flow)
        self.assertIn("`preview_start` with `url` `file://<compare_page>`", flat)
        self.assertIn("print the page path and both captures' links instead, and go on: an unopened page never "
                      "stops the walk.", flat)
        self.assertIn("name each side whose `staged_path` is `null` with its `error`", flat)
        self.assertIn("show with steps 1–3, before the `Thread` card, whose options are a non-security item's.", flat)
        self.assertNotIn("which is unchanged", flat)
        # The card's two option sets are the same four answers each (`test_the_two_option_sets_keep_accept_off_a_security_item`).
        self.assertIn("never writes a page itself", _flat(ROUTER.read_text(encoding="utf-8")))

    def test_the_summary_counts_retired(self):
        self.assertIn("`left_out` by why (replaced, retired", self.flow)


if __name__ == "__main__":
    unittest.main()
