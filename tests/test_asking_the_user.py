"""Offline tests for the shared decision-card contract (skills/_shared/asking-the-user.md).

The operator kept answering cards with "I need more context", "I don't understand the question" and
"how does this affect the end user?", and found the recommended option was often the only real one.
The contract answers both: a brief before every card, the evidence on "more", the same card re-asked,
and a genuine-choice test that turns a one-option judgment into an announce line. These tests pin the
load-bearing phrases, and pin that every router's universal decision handler carries the brief — a
linked file is not guaranteed to be in context when a card renders, so the rule has to be at the
point of use too.
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
                    "deciding what shipped (un-ticking or re-attributing a ticked phase or DoD row"):
            self.assertIn(act, self.text)
        self.assertIn("an honest confirm", self.text)
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
                        "Answer *more* (in Other) for the evidence."):
            self.assertIn(element, brief)
        self.assertIn("The card's `question` points at the brief and never restates it", brief)
        self.assertIn("what that choice does **here**", brief)

    def test_more_re_asks_the_same_card(self):
        more = self.text[self.text.index("## More context"):self.text.index("## Honest options")]
        self.assertIn("re-ask **the same card** — same header, same options", more)

    def test_the_genuine_choice_test_and_the_reasoned_recommendation(self):
        self.assertIn('**Each option needs a "pick this when …"**', self.text)
        self.assertIn("**The recommendation is reasoned from this instance's evidence**, goes first as option 1",
                      self.text)
        self.assertIn("A fixed per-card default is allowed only as a consent card's safe action", self.text)
        self.assertIn('no "(recommended)" suffix', self.text)

    def test_relayed_cards_are_self_contained(self):
        relayed = self.text[self.text.index("## Relayed cards"):self.text.index("## Sub-agents never ask")]
        for rule in ("**The card is self-contained.**", "`<stage> · #<N> · <step>`",
                     "the brief goes *in* the card", "**same message** as the card",
                     "same header, same options", "Put it in the next milestone update",
                     "The coordinator never answers a mirrored card itself."):
            self.assertIn(rule, relayed)

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
            self.assertRegex(rule[:400], r"one `AskUserQuestion` card with its brief \(per ",
                             "%s: the decision card rule must render the brief" % path.parent.name)

    def test_every_gates_invariant_announces_one_option_judgments(self):
        for skill in GATE_ROUTERS:
            flat = _flat((SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8"))
            bullet = flat[flat.index("**Gates only for genuine decisions**"):]
            bullet = re.split(r" - \*\*", bullet, maxsplit=1)[0]
            self.assertIn("A judgment gate left with one credible option is announced, not asked; "
                          "every card opens with its brief.", bullet, skill)


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


if __name__ == "__main__":
    unittest.main()
