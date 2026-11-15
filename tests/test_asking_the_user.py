"""Offline tests for the shared decision-card contract (skills/_shared/asking-the-user.md).

The operator kept answering cards with "I need more context", "I don't understand the question" and
"how does this affect the end user?", and found the recommended option was often the only real one.
The contract answers both: a brief inside every card's question (the dialog may hide the turn's earlier
text, #231), larger blocks printed beside the card, the evidence on "more", the same card re-asked, a
reasoned recommendation marked on option 1, and a genuine-choice test that turns a one-option judgment
into an announce line. These tests pin the load-bearing phrases, and pin that every router's universal
decision handler puts the brief in the card's question — a linked file is not guaranteed to be in
context when a card renders, so the rule has to be at the point of use too.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
CONTRACT = SKILLS_DIR / "_shared" / "asking-the-user.md"

# The routers with a "Gates only for genuine decisions" invariant. The researcher and slicer
# routers carry the decision-card rule but list no in-flow gates of their own.
GATE_ROUTERS = ("planner", "resolver", "drafter", "evaluator", "requirements-gatherer", "coordinator")


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

    def test_the_brief_carries_every_element(self):
        brief = self.text[self.text.index("## The brief"):self.text.index("## More context")]
        for element in ("**What is being decided**", "**Why now**", "**What differs for the end user**",
                        "no user-visible difference", "**The trade-off**", "**Clear call** or **close call**",
                        "Answer \"more\" in Other for the evidence."):
            self.assertIn(element, brief)
        # #231: the brief rides in the card; the dialog may hide what was printed before it.
        self.assertIn("It goes in the card's `question`, in every session", brief)
        self.assertNotIn("points at the brief", brief)
        self.assertNotIn("never restates it", brief)
        self.assertIn("**supporting block**", brief)
        self.assertIn("It prints in the same message as the card, before it, and the `question` names it", brief)
        self.assertIn("the `question` still carries at least the Decide, Trade-off and clear/close-call lines", brief)
        self.assertIn("decided by its shape, not its length", brief)
        self.assertIn("On a card with several questions, each question carries its own brief", brief)
        # A card raised again in a later message carries its block again: the earlier message may be hidden.
        self.assertIn("a card raised again in a later message (a follow-on card, a re-raise) prints its block again",
                      brief)
        self.assertIn("plus any short plain-text line a card's own definition puts there", brief)
        # The planner's relayed #616 card arrived as one unbroken paragraph with a literal *more*.
        self.assertIn("One labelled line per element, a blank line between them", brief)
        self.assertIn("text that goes *in* a card carries no markdown", brief)
        self.assertIn("what that choice does **here**", brief)

    def test_more_re_asks_the_same_card(self):
        more = self.text[self.text.index("## More context"):self.text.index("## Honest options")]
        self.assertIn("re-ask **the same card** — same header, same options", more)
        self.assertIn("The evidence prints in the same message as the re-asked card, before it, in every session.",
                      more)

    def test_the_genuine_choice_test_and_the_reasoned_recommendation(self):
        self.assertIn('**Each option needs a "pick this when …"**', self.text)
        self.assertIn("**The recommendation is reasoned from this instance's evidence**, goes first as option 1",
                      self.text)
        self.assertIn("A fixed per-card default is allowed only as a consent card's safe action", self.text)
        # #231: the recommendation shows on the card itself, as a suffix applied at render time.
        self.assertNotIn('no "(recommended)" suffix', self.text)
        self.assertIn("its `label` ends ` (Recommended)`; the in-card Trade-off line says why", self.text)
        self.assertIn("A card that recommends nothing", self.text)
        self.assertIn("lists its first option as order only, with no suffix", self.text)
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
            self.assertRegex(rule[:400], r"one `AskUserQuestion` card with its brief in the card's question \(per ",
                             "%s: the decision card rule must render the brief" % path.parent.name)

    def test_every_gates_invariant_announces_one_option_judgments(self):
        for skill in GATE_ROUTERS:
            flat = _flat((SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8"))
            bullet = flat[flat.index("**Gates only for genuine decisions**"):]
            bullet = re.split(r" - \*\*", bullet, maxsplit=1)[0]
            self.assertIn("A judgment gate left with one credible option is announced, not asked; "
                          "every card carries its brief in its question.", bullet, skill)

    def test_review_report_renders_its_decision_with_the_brief_in_the_question(self):
        flat = _flat((SKILLS_DIR / "review-report" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("one `AskUserQuestion` card with its brief in its question", flat)


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
