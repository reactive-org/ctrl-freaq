"""Offline tests for the shared decision-card contract (skills/_shared/asking-the-user.md).

The operator kept answering cards with "I need more context", "I don't understand the question" and
"how does this affect the end user?", and found the recommended option was often the only real one.
The contract answers both: one voice for every card, a formatted brief printed in the message before the card
with a plain-text compact core in its question (#245 — #231 had put the whole brief in the question, since the
dialog may hide the turn's earlier text, but the question cannot be formatted), larger blocks printed beside
the card, code-pasting blocks as a script-built link and a gist, the evidence on "more", the same card
re-asked, a reasoned recommendation marked on option 1 whose description gives its reason, and a
genuine-choice test that turns a one-option judgment into an announce line. These tests pin the load-bearing
phrases, and pin that every router's universal decision handler prints the brief and puts the compact core in
the card's question — a linked file is not guaranteed to be in context when a card renders, so the rule has to
be at the point of use too.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
CONTRACT = SKILLS_DIR / "_shared" / "asking-the-user.md"

# The routers with a "Gates only for genuine decisions" invariant. The researcher and slicer
# routers carry the decision-card rule but list no in-flow gates of their own.
GATE_ROUTERS = ("planner", "resolver", "drafter", "evaluator", "requirements-gatherer", "coordinator", "designer")


def _flat(text):
    return " ".join(text.split())


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.text = _flat(CONTRACT.read_text(encoding="utf-8"))

    def test_consent_cards_are_a_closed_set_that_always_renders(self):
        self.assertIn("**A consent card always renders.** It is one of a closed set:", self.text)
        for act in ("a merge", "closing a PR", "a landing gate", "removing a worktree", "pushing with reds",
                    "a tracked open question", "overriding a gate or audit blocker",
                    # The resolver's Tracker card un-ticks shipped rows; it must never be announced.
                    "deciding what shipped (un-ticking or re-attributing a ticked phase or DoD row",
                    # #97: only the operator's answer continues a resolver session past a pause.
                    "a resolver **review** after a non-final phase (`header: \"Review\"`)"):
            self.assertIn(act, self.text)
        self.assertIn("an honest confirm", self.text)
        # #145: a security dismissal by thread is the human's act in GitHub; the card keeps two cases.
        self.assertIn("dismissing by resolving a review thread is the human's act in GitHub", self.text)
        # The coordinator's routine close is carved out of "removing a worktree" (#113), not deleted from it.
        self.assertIn("the coordinator's routine close of a worktree whose PR merged (or whose issue closed)",
                      self.text)

    def test_an_operator_task_card_always_renders(self):
        self.assertIn("**An operator-task card always renders.**", self.text)
        self.assertIn("neither a consent nor a judgment card", self.text)

    def test_a_one_option_judgment_is_announced_and_never_recorded_as_the_operators(self):
        self.assertIn("leaves two or more options.** Left with one, announce it in one line and proceed", self.text)
        self.assertIn("`Did X because Y; say if you'd rather Z`", self.text)
        self.assertIn("An announced choice is **never** recorded as `[user decision <date>]`", self.text)

    def test_the_voice_renders_the_shared_voice_contract(self):
        voice = self.text[self.text.index("## Voice"):self.text.index("## Consent vs judgment")]
        # #250: the persona and the decoding rule live in voice.md; the card contract points there.
        self.assertIn("[`voice.md`](voice.md)", voice)
        self.assertNotIn("busy senior colleague", voice)
        self.assertNotIn("Leave no pipeline vocabulary undecoded", voice)
        # What stays is card-specific.
        self.assertIn("say what the decision is, what you checked and what you found", voice)
        # A card that recommends nothing leans nowhere.
        self.assertIn("on a card that recommends, which way you lean", voice)
        self.assertIn("`Did X because Y; say if you'd rather Z`, whose form is unchanged", " ".join(voice.split()))
        self.assertLess(self.text.index("## Voice"), self.text.index("## Consent vs judgment"))

    def test_setups_voice_card_is_a_consent_card_that_recommends_nothing(self):
        flat = " ".join(self.text.split())
        self.assertIn("whether a repo replaces the default voice (setup's `Voice` card", flat)
        self.assertIn("setup's `Voice` card (the voice is the team's preference, #250)", flat)

    def test_the_brief_carries_every_element(self):
        brief = self.text[self.text.index("## The brief"):self.text.index("## More context")]
        for element in ("**An opening paragraph**", "**What is being decided**", "**Why now**", "**Impact**",
                        "**The trade-off**", "**Clear call** or **close call**",
                        "Answer \"more\" in Other for the evidence."):
            self.assertIn(element, brief)
        # #245: the brief prints in the message, formatted; the card keeps a compact core.
        self.assertIn("The brief prints in the message before the card, as formatted markdown", brief)
        # Only blocks, "more" evidence and a card's own output stand between the brief and the card.
        self.assertIn("Only the card's supporting blocks (\"Supporting blocks\"), the evidence on \"more\" (\"More "
                      "context\") and output the card's own definition prints in that message", brief)
        self.assertNotIn("immediately before the card", brief)
        self.assertIn("It does not go in the card.", brief)
        self.assertNotIn("It goes in the card's `question`, in every session", brief)
        self.assertIn("`<affected party> — <what changes for it>`", brief)
        self.assertIn("`None outside the code — <what it touches>`", brief)
        self.assertNotIn("For users:", brief)
        self.assertNotIn("**What differs for the end user**", brief)
        self.assertIn("**Impact:** <affected party> — <what changes for it>", brief)
        # The compact core, and its plain-text rule.
        self.assertIn("**The card's question** carries the compact core, enough to answer the card from the card alone",
                      brief)
        for line in ("the identity line, in a session the coordinator started", "`Decide: <what is being decided>`",
                     "any short plain-text line a card's own definition puts there",
                     "the clear/close-call line, on a card whose brief has one",
                     "`Answer \"more\" in Other for the evidence.`"):
            self.assertIn(line, brief)
        self.assertIn("Why now, Impact and Trade-off leave the card", brief)
        # The question names the block it relies on, so naming is a compact-core line, not an extra.
        self.assertIn("and the name of each supporting block the card relies on", brief)
        self.assertNotIn("carries the compact core and nothing else", brief)
        self.assertIn("text that goes *in* a card carries no markdown", brief)
        self.assertIn("what that choice does **here**, in this instance, and what it costs", brief)
        self.assertNotIn("points at the brief", brief)
        self.assertNotIn("never restates it", brief)
        self.assertIn("**supporting block**", brief)
        self.assertIn("It prints in the same message as the card, after the brief and before the card, and the "
                      "`question` names it", brief)
        self.assertIn("A block never replaces the compact core", brief)
        self.assertIn("A card that recommends nothing has neither of the last two", brief)
        self.assertIn("honest confirm (one action + **Cancel**), between the action and **Cancel**", brief)
        self.assertIn("decided by its shape, not its length", brief)
        # One brief per question on a card with several, each question its own compact core.
        self.assertIn("On a card with several questions, the message prints one brief per question, each opening with "
                      "its decision's short name in bold, and each question carries its own compact core", brief)
        # A card raised again prints its brief and blocks again: the earlier message may be hidden.
        self.assertIn("A card raised again in a later message (a follow-on card, a re-raise, a re-ask after \"more\" or "
                      "discussion) prints its brief and its blocks again with it", brief)
        # The planner's relayed #616 card arrived as one unbroken paragraph with a literal *more*.
        self.assertIn("one bold-labelled line per element, a blank line between them", brief)
        self.assertIn("one line per element and a blank line between them", brief)

    def test_a_code_pasting_block_shows_a_script_built_link_and_a_gist(self):
        brief = self.text[self.text.index("## The brief"):self.text.index("## More context")]
        self.assertIn("**A block that pastes code at an anchor** (the `Polish` digest, the `Security` card's findings "
                      "block) shows, for each anchor, a one-line gist of what the code there does and a link to it", brief)
        self.assertIn("one `review_threads.py code-links` call over every anchor the block shows", brief)
        for kind in ("- `line` —", "- `file` —", "- `blob` —", "- `code` — no link, so the block prints the code"):
            self.assertIn(kind, brief)
        self.assertIn("`--pr-head` is the pushed head, never local `HEAD`.", brief)
        # The one runnable invocation, and what a failed call or an unread diff prints.
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/review_threads.py code-links --root \"<checkout>\"", brief)
        self.assertIn("A non-zero exit (2, a usage error) prints nothing on stdout: treat every anchor as `code`", brief)
        self.assertIn("A `CODE_LINKS_DIFF_UNREAD` notice comes back with its anchors as `blob` (`diff-unread`)", brief)
        self.assertIn("A before → after sketch, or a security finding's claim, may stand in for the gist.", brief)
        # SEC-1 on PR #247: an anchor is a path from the diff under review, so it never goes on a shell line.
        flat = " ".join(brief.split())
        self.assertIn("to `<scratch>/code-links-anchors.txt` with the file-write tool, never on the command line, in an "
                      "`echo` or in a heredoc", flat)
        fences = re.findall(r"```bash\n(.*?)```", CONTRACT.read_text(encoding="utf-8"), re.S)
        calls = [f for f in fences if "code-links" in f]
        self.assertEqual(len(calls), 1)
        self.assertIn('--anchors-file "<scratch>/code-links-anchors.txt"', calls[0])
        self.assertNotIn("<path>:<start>-<end>", calls[0])

    def test_more_re_asks_the_same_card(self):
        more = self.text[self.text.index("## More context"):self.text.index("## Honest options")]
        self.assertIn("re-ask **the same card** — same header, same options", more)
        self.assertIn("The evidence prints in the same message as the re-asked card, before it, in every session, "
                      "after the brief and the card's supporting blocks, which print again", more)
        self.assertIn("the code behind every link a block showed", more)

    def test_the_genuine_choice_test_and_the_reasoned_recommendation(self):
        self.assertIn('**Each option needs a "pick this when …"**', self.text)
        self.assertIn("**The recommendation is reasoned from this instance's evidence**, goes first as option 1",
                      self.text)
        self.assertIn("A fixed per-card default is allowed only as a consent card's safe action", self.text)
        # #231: the recommendation shows on the card itself, as a suffix applied at render time.
        self.assertNotIn('no "(recommended)" suffix', self.text)
        # #245: the reason rides in option 1's description, since the Trade-off line left the card.
        self.assertIn("its `label` ends ` (Recommended)`; its `description` gives the reason — the brief's Trade-off "
                      "line, cut to that option's own terms", self.text)
        self.assertNotIn("in-card Trade-off line", self.text)
        self.assertIn("a reasoned recommendation marks option 1 and gives its reason in that option's description; "
                      "no recommendation, no suffix", self.text)
        self.assertIn("A card that recommends nothing", self.text)
        self.assertIn("lists its first option as order only, with no suffix", self.text)
        # A no-recommendation card's brief drops the line that would otherwise mark option 1.
        self.assertIn("equally weighted paths — has no Trade-off or clear/close-call line in its brief", self.text)
        self.assertIn("an honest confirm (one action + **Cancel**) carries no suffix", self.text)
        self.assertIn("never written into a skill's option spec", self.text)
        self.assertIn("read an answer with the suffix stripped", self.text)

    def test_relayed_cards_keep_only_the_identity_line_and_relay(self):
        relayed = self.text[self.text.index("## Relayed cards"):self.text.index("## Sub-agents never ask")]
        for rule in ("`<stage> · #<N> · <step>`", "Only a session the coordinator started carries it.",
                     "Put it in the next milestone update",
                     "The coordinator never answers a mirrored card itself."):
            self.assertIn(rule, relayed)
        # #231: the self-contained card, the same-message block and "more" are now every session's rules.
        for gone in ("self-contained", "**same message**", "same header, same options"):
            self.assertNotIn(gone, relayed)

    def test_the_stage_brief_points_at_relayed_cards(self):
        brief = _flat((SKILLS_DIR / "coordinator" / "references" / "stage-brief.md").read_text(encoding="utf-8"))
        self.assertIn("asking-the-user.md", brief)
        self.assertIn("Relayed cards", brief)

    def test_shape_and_subagent_rules_survive(self):
        self.assertIn("`header` ≤ 12 chars", self.text)
        self.assertIn("Never tell a sub-agent to call `AskUserQuestion` itself.", self.text)


class RouterTests(unittest.TestCase):
    def test_every_decision_card_rule_renders_with_its_brief(self):
        routers = [p for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))
                   if "**Decision card rule.**" in p.read_text(encoding="utf-8")]
        self.assertGreaterEqual(len(routers), 7)
        for path in routers:
            flat = _flat(path.read_text(encoding="utf-8"))
            rule = flat[flat.index("**Decision card rule.**"):]
            self.assertRegex(rule[:400], r"one `AskUserQuestion` card, its brief printed before it and its compact "
                                         r"core in the card's question \(per ",
                             "%s: the decision card rule must render the brief" % path.parent.name)

    def test_every_gates_invariant_announces_one_option_judgments(self):
        for skill in GATE_ROUTERS:
            flat = _flat((SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8"))
            bullet = flat[flat.index("**Gates only for genuine decisions**"):]
            bullet = re.split(r" - \*\*", bullet, maxsplit=1)[0]
            self.assertIn("A judgment gate left with one credible option is announced, not asked; "
                          "every card prints its brief before it and carries its compact core in its question.",
                          bullet, skill)

    def test_review_report_renders_its_decision_with_the_brief_in_the_question(self):
        flat = _flat((SKILLS_DIR / "review-report" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("one `AskUserQuestion` card, its brief printed before it and its compact core in its question",
                      flat)

    def test_no_skill_or_summary_keeps_the_old_brief_placement(self):
        # #245: the sweep the plan names comes back empty.
        for path in [REPO_ROOT / "CLAUDE.md"] + sorted(SKILLS_DIR.rglob("*.md")):
            flat = _flat(path.read_text(encoding="utf-8"))
            self.assertIsNone(re.search(r"brief in (the card's|its) question|question's brief", flat), str(path))


class HeaderLengthTests(unittest.TestCase):
    """The tool caps `header` at 12 characters, so a longer one renders truncated or not at all.
    A parameterised header (`OQ <id>`) is measured with its placeholder, which runs longer than
    any value it takes."""

    _HEADER = re.compile(r'header: ?"([^"]+)"')

    def test_every_card_header_fits_the_tool_limit(self):
        long = []
        for path in sorted(SKILLS_DIR.rglob("*.md")):
            for header in self._HEADER.findall(path.read_text(encoding="utf-8")):
                if len(header) > 12:
                    long.append("%s: %r" % (path.relative_to(REPO_ROOT), header))
        for path in sorted((REPO_ROOT / "scripts").rglob("*.py")):
            for header in re.findall(r'"header": ?"([^"]+)"', path.read_text(encoding="utf-8")):
                if len(header) > 12:
                    long.append("%s: %r" % (path.relative_to(REPO_ROOT), header))
        self.assertEqual(long, [], "card headers over 12 characters")

    def test_the_scan_sees_headers(self):
        found = sum(len(self._HEADER.findall(p.read_text(encoding="utf-8"))) for p in SKILLS_DIR.rglob("*.md"))
        self.assertGreater(found, 30, "the header scan matched almost nothing — has the form changed?")


class NoFixedRecommendationTests(unittest.TestCase):
    """The audited cards whose recommendation was a fixed label now reason it per instance."""

    def test_no_fixed_default_survives_on_the_audited_cards(self):
        for rel, banned in (
            ("resolver/playbooks/resolve-spine.md", "**Revise via drafter** (default"),
            ("resolver/playbooks/resolve-spine.md", "**Re-plan** (default"),
            ("planner/playbooks/revise.md", "Start fresh (recommended)"),
            ("planner/references/revise-reconciliation.md", "Start fresh (recommended)"),
            ("planner/references/plan-schema.md", "Definition of done` (recommended)"),
            ("drafter/playbooks/draft-spine.md", "**Scope it out** *(default)*"),
            ("setup/playbooks/setup-flow.md", "(default: create `COMMANDS.md`)"),
        ):
            self.assertNotIn(banned, _flat((SKILLS_DIR / rel).read_text(encoding="utf-8")), rel)


class NoSuffixInSpecsTests(unittest.TestCase):
    """#231: the `(Recommended)` suffix is applied at render time, only when an instance recommends. A
    skill file that writes it into an option spec makes it a fixed recommendation, which the genuine-choice
    test forbids. Case-sensitive on purpose: lowercase prose such as seam-dispositions.md's "(recommended)"
    table entry documents a per-row rule, not a label."""

    ALLOWED = {"resolver/references/phase-continuation.md": ('never marked "(Recommended)"',)}

    def test_no_skill_file_writes_the_suffix_into_a_card(self):
        found = []
        for path in sorted(SKILLS_DIR.rglob("*.md")):
            if path == CONTRACT:
                continue
            rel = path.relative_to(SKILLS_DIR).as_posix()
            flat = _flat(path.read_text(encoding="utf-8"))
            for fragment in self.ALLOWED.get(rel, ()):
                flat = flat.replace(fragment, "")
            if "(Recommended)" in flat:
                found.append(rel)
        self.assertEqual(found, [], "a fixed (Recommended) suffix written into a skill file")

    def test_the_allowed_fragment_still_exists(self):
        flat = _flat((SKILLS_DIR / "resolver" / "references" / "phase-continuation.md").read_text(encoding="utf-8"))
        self.assertIn('never marked "(Recommended)"', flat)


if __name__ == "__main__":
    unittest.main()
