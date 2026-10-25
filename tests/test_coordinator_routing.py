"""Offline tests for the coordinator skill (#83).

A standalone tool with a shared spine and two routable playbooks (epic / single): router
structure and frontmatter (`disable-model-invocation: true` — it must never auto-trigger on "work
on #N", the resolver's trigger), the router naming every `stage` and `refusal` token the prep
defines (the requirements-gatherer pattern — the table the model reads and the set the script
emits cannot drift apart), the fence-scoped write/ref-arithmetic/raw-git bans, and the language
that carries the design's load-bearing rules: starts only on the operator's word, never on an event
turn; consent before every open/close/sync; GitHub is the authority; the stage brief points at the
relayed-cards contract.
"""

import re
import sys
import unittest
from pathlib import Path

from tests.support.retired_tokens import FORBIDDEN_CONTRACT_TOKENS, retired_name_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import prep_coordinator  # noqa: E402

SKILL_DIR = REPO_ROOT / "skills" / "coordinator"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
SPINE = "coordinate-spine.md"
ROUTABLE = {"epic.md", "single.md"}
BRIEF = SKILL_DIR / "references" / "stage-brief.md"

# A new tool — no v1 counterpart, so no ≤half-v1 metric (prd §10's bar is a pipeline-stage bar).
# Growth ceiling only, for the largest loaded set (router + spine + epic.md).
LOADED_SET_CEILING = 260


def _iter_md(dir_path):
    yield from sorted(dir_path.rglob("*.md"))


def _flat(text):
    return re.sub(r"\s+", " ", text)


def _fence_lines(path):
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        yield i, line, in_fence


class RouterStructureTests(unittest.TestCase):
    def setUp(self):
        self.router = ROUTER.read_text(encoding="utf-8")

    def test_frontmatter(self):
        head = "\n".join(self.router.splitlines()[:8])
        self.assertIn("name: coordinator", head)
        self.assertIn("disable-model-invocation: true", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)

    def test_playbooks_on_disk_are_the_spine_and_the_two_routes(self):
        self.assertEqual({p.name for p in PLAYBOOKS_DIR.glob("*.md")}, ROUTABLE | {SPINE})
        for name in ROUTABLE | {SPINE}:
            self.assertIn("playbooks/%s" % name, self.router)

    def test_router_routes_on_the_preps_suggested_playbook(self):
        self.assertEqual({prep_coordinator.PLAYBOOK_EPIC, prep_coordinator.PLAYBOOK_SINGLE}, ROUTABLE)
        for name in ROUTABLE:
            self.assertIn("`suggested_playbook: %s`" % name, self.router)

    def test_router_at_most_150_lines(self):
        n = len(self.router.splitlines())
        self.assertLessEqual(n, 150, "router is %d lines (architecture.md §9 bar <= 150)" % n)

    def test_loaded_set_under_ceiling(self):
        total = sum(len(p.read_text(encoding="utf-8").splitlines())
                    for p in (ROUTER, PLAYBOOKS_DIR / SPINE, PLAYBOOKS_DIR / "epic.md"))
        self.assertLessEqual(total, LOADED_SET_CEILING)

    def test_prep_invoked_by_plugin_root_path(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/prep_coordinator.py", self.router)

    def test_router_names_every_stage_and_refusal(self):
        for token in prep_coordinator.STAGES + prep_coordinator.REFUSALS:
            self.assertIn("`%s`" % token, self.router, "router does not name %r" % token)

    def test_router_names_every_next_kind(self):
        for kind in (prep_coordinator.NEXT_SESSION, prep_coordinator.NEXT_OPEN, prep_coordinator.NEXT_CLOSE,
                     prep_coordinator.NEXT_PICK, prep_coordinator.NEXT_NONE):
            self.assertIn("`%s`" % kind, self.router)


class DesignRuleLanguageTests(unittest.TestCase):
    def setUp(self):
        self.router = _flat(ROUTER.read_text(encoding="utf-8"))
        self.spine = _flat((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8"))
        self.all = _flat(" ".join(p.read_text(encoding="utf-8") for p in _iter_md(SKILL_DIR)))

    def test_starts_only_on_the_operators_word_never_on_an_event_turn(self):
        self.assertIn("A stage session starts only on the operator's word, in this chat.", self.router)
        self.assertIn("carries the command **and** the absolute folder", self.router)
        self.assertIn("a standing instruction", self.router)

    def test_environment_gated_with_a_print_fallback(self):
        self.assertIn("**Environment-gated.**", self.router)
        self.assertIn("print the command and the folder instead", self.router)

    def test_consent_before_every_workspace_action(self):
        self.assertIn("**Consent before every open, close and sync**", self.router)
        self.assertIn("even when the core would raise none", self.router)

    def test_github_is_the_authority_and_disagreement_is_a_card(self):
        self.assertIn("**GitHub is the authority.**", self.router)
        self.assertIn("never a silent pick", self.router)
        self.assertIn("quotes the handoff's `Why:` line verbatim", self.spine)

    def test_a_handoff_one_step_past_the_coordinators_own_step_agrees(self):
        """The normal path: a stage never opens, closes or syncs, so its next command names the
        stage after the coordinator's step — that must never raise a disagreement card."""
        self.assertIn("**The handoff names the step after one the coordinator runs itself**", self.spine)
        self.assertIn("That is agreement, never a card", self.spine)

    def test_summary_not_a_handoff(self):
        self.assertIn("Summary — not a `## Handoff`", self.router)

    def test_never_answers_a_mirrored_card(self):
        self.assertIn("never answers a stage's mirrored card", self.router)

    def test_core_decisions_render_as_the_tools_own(self):
        self.assertIn("**workspace tool's own card**", self.router)

    def test_brief_points_at_relayed_cards_and_scopes_the_stage(self):
        brief = _flat(BRIEF.read_text(encoding="utf-8"))
        self.assertIn('"Relayed cards" in <<contract_path>>', brief)
        self.assertIn("send your full `## Handoff` block to the coordinator verbatim", brief)
        self.assertIn("outside the pipeline record", brief)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/_shared/asking-the-user.md", self.router)


class ContractTokenGateTests(unittest.TestCase):
    def test_no_forbidden_contract_tokens_or_v1_names(self):
        for path in _iter_md(SKILL_DIR):
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                hit = FORBIDDEN_CONTRACT_TOKENS.search(line)
                self.assertIsNone(hit, "forbidden token at %s:%d" % (path.relative_to(REPO_ROOT), i))
            self.assertEqual(retired_name_hits(text), [], "%s names a retired v1 skill" % path.name)

    def test_no_gh_writes_raw_git_or_ref_arithmetic_in_fences(self):
        banned = re.compile(
            r"\bgh\s+(issue|pr)\s+(create|edit|comment|review|close|reopen|merge|develop)\b"
            r"|\bgh\s+api\b[^\n]*\bDELETE\b"
            r"|^\s*git\s"  # no raw git on a work workspace — the sync is workspace.py's
            r"|git\s+show\s+[^\s]+:|git\s+grep\s+[^-]"
        )
        for path in _iter_md(SKILL_DIR):
            for i, line, in_fence in _fence_lines(path):
                if in_fence:
                    self.assertIsNone(banned.search(line), "%s:%d %r" % (path.relative_to(REPO_ROOT), i, line))

    def test_never_spells_the_remove_core(self):
        for path in _iter_md(SKILL_DIR):
            self.assertNotIn("remove --work", path.read_text(encoding="utf-8"), path.name)

    def test_no_w_slash_shorthand(self):
        for path in _iter_md(SKILL_DIR):
            self.assertIsNone(re.search(r"\bw/", path.read_text(encoding="utf-8")), path.name)


class ResolverLookTests(unittest.TestCase):
    """#97: a multi-phase resolver session stops where it raises a look the operator does not continue
    past, not at every `checkpoint: pause` — the single playbook says so, and nothing else changes here."""

    def test_single_playbook_states_the_look_rule(self):
        flat = " ".join((PLAYBOOKS_DIR / "single.md").read_text(encoding="utf-8").split())
        self.assertNotIn("stops at each `checkpoint: pause`", flat)
        self.assertIn("stops where it raises a look the operator does not continue past, or after every phase with `--pause`", flat)


class StackAgnosticTests(unittest.TestCase):
    def test_no_stack_assumptions(self):
        stack = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(stack.search(line), "%s:%d %r" % (path.relative_to(REPO_ROOT), i, line))


if __name__ == "__main__":
    unittest.main()
