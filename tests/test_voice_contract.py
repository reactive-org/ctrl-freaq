"""The shared voice contract (#250): `skills/_shared/voice.md` holds the default persona, the fixed rules every
operator-facing output follows, where an output's own contract wins, and the precedence rule for a consuming
repo's `<!-- ctrl-freaq-voice -->` block. Every output contract renders it with a pointer and no longer restates
it — render, don't restate (CLAUDE.md "Shared contracts in `skills/_shared/`").
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = REPO_ROOT / "skills"
VOICE = SKILLS / "_shared" / "voice.md"

# validator 5's pattern (CLAUDE.md "Validators").
STACK_PATTERN = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)

# Every operator-facing output contract that renders voice.md (#250 DoD 4).
OUTPUT_CONTRACTS = (
    "_shared/plan-summary.md",
    "_shared/handoff-format.md",
    "_shared/asking-the-user.md",
    "resolver/references/phase-continuation.md",
    "evaluator/references/pr-summary.md",
    "coordinator/references/stage-brief.md",
    "setup/SKILL.md",
    "question-sweep/SKILL.md",
    "question-resolver/SKILL.md",
    "doc-reviewer/SKILL.md",
    "requirements-gatherer/SKILL.md",
    "workspace-open/SKILL.md",
    "workspace-close/SKILL.md",
    "coordinator/SKILL.md",
    "review-report/SKILL.md",
    "review/SKILL.md",
)

# (file, restatement the voice contract replaced) — each must be gone.
REMOVED_RESTATEMENTS = (
    ("resolver/references/phase-continuation.md", "no pipeline vocabulary left undecoded"),
    ("resolver/references/phase-continuation.md", "at the operator's altitude (behaviour, not file lists)"),
    ("_shared/plan-summary.md", "no schema vocabulary the operator would have to decode"),
    ("evaluator/references/pr-summary.md", "2–4 plain sentences"),
    ("evaluator/references/pr-summary.md", "2–4 sentences, plain words"),
    ("_shared/asking-the-user.md", "as a developer asking a busy senior colleague"),
    ("review/SKILL.md", "as a developer asking a busy senior colleague"),
    ("review/SKILL.md", "leave no pipeline vocabulary undecoded"),
    ("planner/playbooks/revise.md", "at the operator's altitude:"),
)

# GitHub-artifact prose is out of the voice's scope (#250 Background): no pointer there.
OUT_OF_SCOPE = (
    "_shared/review-threads.md",
    "evaluator/references/review-threads.md",
    "_shared/design-check.md",
    "evaluator/references/design-comparison-prompt.md",
)


def _flat(text):
    return " ".join(text.split())


def _section(text, heading):
    start = text.index(heading)
    nxt = text.find("\n## ", start + 1)
    return text[start:] if nxt == -1 else text[start:nxt]


class VoiceContractTests(unittest.TestCase):
    def setUp(self):
        self.text = VOICE.read_text(encoding="utf-8")
        self.flat = _flat(self.text)

    def test_sections_in_order(self):
        headings = [line for line in self.text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, [
            "## Default persona",
            "## Fixed rules",
            "## When an output's own contract wins",
            "## The repo's voice — precedence",
            "## Which outputs follow it",
        ])

    def test_default_persona(self):
        persona = _flat(_section(self.text, "## Default persona"))
        self.assertIn("a developer asking a busy senior colleague for a review — someone who knows the codebase but "
                      "not this session, and is switching in from other work, so mental context is short.", persona)

    def test_the_seven_fixed_rules_in_order(self):
        rules = _flat(_section(self.text, "## Fixed rules"))
        expected = (
            "**Conclusion first.**",
            "**Say what the operator needs to do or decide, or that nothing is needed.**",
            "**Write at the operator's altitude** — behaviour and consequence, not file lists or step ids.",
            "**Leave no pipeline vocabulary undecoded.**",
            "**Keep internals out of prose where a plain description works** — facts-block keys, spine step ids and "
            "script names",
            "**Link to the thing itself**",
            "**Say what was not checked beside what was.**",
        )
        positions = [rules.index(rule) for rule in expected]
        self.assertEqual(positions, sorted(positions))
        # #245's decoding examples moved here with the rule.
        self.assertIn("(SOFT/HARD, seam, `closes-dod`, plan-settled, tracker)", rules)

    def test_an_outputs_own_contract_wins_inside_its_tokens(self):
        wins = _flat(_section(self.text, "## When an output's own contract wins"))
        for token in ("`file:line` evidence", "verbatim quotes", "the handoff `**Why:**` line's evidence",
                      "`review requested:` / `review owed:`", "the resolver's `context:` tail",
                      "every closed-set marker", "`Did X because Y; say if you'd rather Z`"):
            self.assertIn(token, wins)
        self.assertIn("the voice governs only the words around it", wins)

    def test_the_precedence_rule(self):
        rule = _flat(_section(self.text, "## The repo's voice — precedence"))
        self.assertIn("`<!-- ctrl-freaq-voice -->` block in its `CLAUDE.md` (or a file it `@`-includes)", rule)
        self.assertIn("**It replaces the default persona and nothing else**", rule)
        for kept in ("the fixed rules above", "the card shape and compact core",
                     "`**Decide:**` / `**Why now:**` / `**Impact:**` / `**Trade-off:**`",
                     "the `## Handoff` schema and its closed-set markers", "the summary sections",
                     "every contract token"):
            self.assertIn(kept, rule)
        self.assertIn("No block can be read as leave to skip a card, rename a field or drop a section", rule)
        self.assertIn("**An empty block is the default.**", rule)
        self.assertIn("**The copy in the session's own checkout governs.**", rule)
        self.assertIn("([architecture.md §6](../../docs/architecture.md) rule 1)", rule)
        self.assertIn("needs no sensitive-path self-edit pair", rule)

    def test_github_artifacts_are_out_of_scope(self):
        scope = _flat(_section(self.text, "## Which outputs follow it"))
        self.assertIn("Out of scope: GitHub artifacts the pipeline writes", scope)

    def test_names_no_stack(self):
        self.assertIsNone(STACK_PATTERN.search(self.text))


class OutputContractsRenderTheVoiceTests(unittest.TestCase):
    def test_each_output_contract_references_voice_md(self):
        for rel in OUTPUT_CONTRACTS:
            with self.subTest(contract=rel):
                text = (SKILLS / rel).read_text(encoding="utf-8")
                self.assertTrue("voice.md" in text or "<<voice_path>>" in text, rel)

    def test_the_restatements_are_gone(self):
        for rel, phrase in REMOVED_RESTATEMENTS:
            with self.subTest(contract=rel, phrase=phrase):
                self.assertNotIn(phrase, _flat((SKILLS / rel).read_text(encoding="utf-8")))

    def test_github_artifact_contracts_carry_no_pointer(self):
        for rel in OUT_OF_SCOPE:
            with self.subTest(contract=rel):
                self.assertNotIn("voice.md", (SKILLS / rel).read_text(encoding="utf-8"))

    def test_relative_pointers_resolve(self):
        link = re.compile(r"\]\(((?:\.\./)*(?:_shared/)?voice\.md)\)")
        for rel in OUTPUT_CONTRACTS:
            path = SKILLS / rel
            for target in link.findall(path.read_text(encoding="utf-8")):
                with self.subTest(contract=rel, target=target):
                    self.assertEqual((path.parent / target).resolve(), VOICE.resolve())

    def test_the_handoff_why_line_keeps_its_evidence(self):
        flat = _flat((SKILLS / "_shared" / "handoff-format.md").read_text(encoding="utf-8"))
        self.assertIn("The prose around that evidence is written per [`voice.md`](voice.md); the evidence itself "
                      "(decision quotes, `file:line`, ids) is this contract's and appears exactly as it requires.", flat)

    def test_the_coordinator_substitutes_the_voice_path(self):
        router = _flat((SKILLS / "coordinator" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("its `<<voice_path>>` `${CLAUDE_PLUGIN_ROOT}/skills/_shared/voice.md`", router)
        self.assertIn("a relayed stage summary, update or handoff stays verbatim", router)


class RegistryTests(unittest.TestCase):
    def test_architecture_registry_names_the_block_and_the_fact(self):
        text = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| Contract tokens are frozen"))
        self.assertIn("#250 (2026-11-25) added, renaming and removing nothing", row)
        self.assertIn("`<!-- ctrl-freaq-voice -->`", row)
        self.assertIn("`facts.inventory.voice`", row)

    def test_prd_records_the_card_and_the_block(self):
        text = _flat((REPO_ROOT / "docs" / "prd.md").read_text(encoding="utf-8"))
        self.assertIn("It asks on a `Voice` consent card (#250)", text)
        self.assertIn("`ctrl-freaq-voice` (user-owned, #250", text)


if __name__ == "__main__":
    unittest.main()
