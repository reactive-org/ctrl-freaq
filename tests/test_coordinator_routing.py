"""Offline tests for the coordinator skill (#83).

A standalone tool with a shared spine and two routable playbooks (epic / single): router
structure and frontmatter (`disable-model-invocation: true` — it must never auto-trigger on "work
on #N", the resolver's trigger), the router naming every `stage` and `refusal` token the prep
defines (the requirements-gatherer pattern — the table the model reads and the set the script
emits cannot drift apart), the fence-scoped write/ref-arithmetic/raw-git bans, and the language
that carries the design's load-bearing rules: starts only on the operator's word, never on an event
turn; open, close and the epic sync are routine steps announced in one line, while the workspace
scripts' own safety cards still render; everything that waits on the operator is a card or a one-click
button (the closed coordinator card set); GitHub is the authority; the stage brief points at the
relayed-cards contract and asks each stage for its human-readable summaries.
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
                     prep_coordinator.NEXT_STORIES, prep_coordinator.NEXT_NONE):
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
        # Any stage message makes an event turn, a milestone update as much as a session's end.
        self.assertIn("a turn a stage's message woke you for (its end or an update)", self.router)

    def test_environment_gated_with_a_print_fallback(self):
        self.assertIn("**Environment-gated.**", self.router)
        self.assertIn("on a `Run stage` card", self.router)
        self.assertIn("in the same message as", self.spine)

    def test_workspace_steps_are_routine_and_announced(self):
        """The consent card for every open, close and sync is retired (#113): the cards confirmed
        routine steps the workspace scripts' own safety cards already guard."""
        self.assertIn("**Routine workspace steps.**", self.router)
        self.assertIn("without a consent card of their own, each announced in one line", self.router)
        self.assertIn("`BRANCH_IN_USE`", self.router)
        self.assertIn("dirty, unpushed or diverged", self.router)
        self.assertIn("no stage session this run started is still running in that worktree", self.router)
        self.assertIn("never on a milestone-update wake", self.spine)
        self.assertIn("does not close unprompted", self.spine)
        for path in _iter_md(SKILL_DIR):
            text = _flat(path.read_text(encoding="utf-8"))
            self.assertNotIn("Consent before every", text, path.name)
            text = text.lower()
            self.assertEqual(text.count("consent card"), text.count("consent card of their own"),
                             "%s: a consent card crept back in" % path.name)

    def test_folded_open_go_ahead(self):
        self.assertIn("the open that creates the folder", self.router)
        self.assertIn("only when the post-open `next` (on an epic, the named story's entry) is a `session`", self.spine)
        self.assertIn("a `setup_failed` receipt stops the start", self.spine)
        # There is no focus to carry through the click: each folded step runs its own prep call, and
        # a card from one holds only its own start.
        self.assertIn("one prep call per folded `--open` or `--sync`", self.spine)
        self.assertIn("a card from one holds only its own start", self.spine)
        # A closed story's worktree is released from its own entry; no held-back close is left to relay.
        self.assertIn("an epic entry's `close` runs the same way", self.spine)

    def test_operator_task_rule_and_the_woken_turn_button(self):
        self.assertIn("**Everything that waits on the operator is a card or a one-click button.**", self.router)
        self.assertIn("inside the same turn", self.router)
        self.assertIn("`start_session`", self.router)
        self.assertIn("never a card answer", self.router)
        self.assertIn("`wrap_up`", self.router)
        faithful = self.router[self.router.index("**Faithful reporting.**"):self.router.index("## 4.")]
        self.assertIn("renders per the operator-task rule", faithful)
        self.assertIn("never on a milestone-update wake or a run's first turn", faithful)
        # A close left open is asked once, or prep's merged-needs-close would re-raise it every turn.
        self.assertIn("`Leave it open` for a close left to them (asked once a run;", self.router)

    def test_the_coordinator_card_set(self):
        """The closed set of cards the coordinator raises; anything else it needs the operator for is a
        button or an announced line."""
        for literal in ("**workspace tool's own card**",      # a decision the prep's core raises
                        "a card saying why",                  # refused
                        "the `gate` card verbatim",           # gated
                        "quotes the handoff's `Why:` line verbatim",  # the re-route judgment card
                        "a card, with the facts' reading first",      # the state contradiction
                        'header: "Your step"',                # an operator task
                        'header: "Run stage"'):               # the no-session-start fallback
            self.assertIn(literal, self.all)
        # Closed: the only headers the coordinator itself declares are the two operator-task cards.
        self.assertEqual(set(re.findall(r'header: ?"([^"]+)"', self.all)), {"Your step", "Run stage"})
        rows = {}
        for line in ROUTER.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\| `([a-z-]+)` \|(.*)\|$", line)
            if m:
                rows[m.group(1)] = m.group(2)
        for stage in ("needs-workspace", "merged-needs-close"):
            self.assertIn(stage, rows)
            self.assertNotIn("card", rows[stage], stage)

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
        # Its one option is an operator task, so it is one card, not the core's card plus a `Your step`.
        self.assertIn("`cwd_inside_target`, an operator task, renders once, as the `Your step` card", self.router)

    def test_brief_points_at_relayed_cards_and_scopes_the_stage(self):
        brief = _flat(BRIEF.read_text(encoding="utf-8"))
        self.assertIn('"Relayed cards" in <<contract_path>>', brief)
        self.assertIn("send your full `## Handoff` block to the coordinator verbatim", brief)
        self.assertIn("outside the pipeline record", brief)
        for summary in ("`## Plan summary`", "Polish digest", "`## Session summary`", "verdict and merge plan",
                        "never only in running output"):
            self.assertIn(summary, brief)
        self.assertIn("posted verbatim in this chat as it arrives", self.spine)
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


class StackAgnosticTests(unittest.TestCase):
    def test_no_stack_assumptions(self):
        stack = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(stack.search(line), "%s:%d %r" % (path.relative_to(REPO_ROOT), i, line))


class ConcurrentDrivingLanguageTests(unittest.TestCase):
    """#115: the epic plan's concurrency replaces single focus — the `stories` stage, gate per step,
    every start in one button, the base-moved re-entry and its base check, the archive point, and the
    trust pointer on a run's first open."""

    def setUp(self):
        self.router = _flat(ROUTER.read_text(encoding="utf-8"))
        self.spine = _flat((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8"))
        self.epic = _flat((PLAYBOOKS_DIR / "epic.md").read_text(encoding="utf-8"))
        self.single = _flat((PLAYBOOKS_DIR / "single.md").read_text(encoding="utf-8"))
        self.brief = _flat(BRIEF.read_text(encoding="utf-8"))

    def test_the_stories_row_replaces_single_focus(self):
        self.assertIn("| `stories` | act on every `epic.stories[]` entry's own `next`, as its gates allow", self.router)
        for literal in ("`concurrency`", "`set` / `start` / `build` / `merge`", "`base_moved`"):
            self.assertIn(literal, self.router)
        for retired in ("next-story", "epic.candidates", "Stories are serial", "`pick`"):
            for path in _iter_md(SKILL_DIR):
                self.assertNotIn(retired, path.read_text(encoding="utf-8"), "%s: %s" % (path.name, retired))

    def test_story_focus_appears_nowhere(self):
        for path in _iter_md(SKILL_DIR):
            self.assertNotIn("--story", path.read_text(encoding="utf-8"), path.name)

    def test_gate_per_step(self):
        self.assertIn("**Gate per step.** An entry's `start` gate holds its `needs-plan`; `build`, its own open "
                      "(`needs-workspace` naming the story) and `resolving` start; `merge`, its `pr-ready` evaluator "
                      "start", self.epic)
        self.assertIn("each later step needs the earlier gates too", self.epic)
        self.assertIn("a `needs-workspace` naming the epic is planning, held by `start` only", self.epic)
        self.assertIn("An edge holds until `merged` unless you settle its `condition` as not holding; one you "
                      "can't settle holds", self.epic)
        self.assertIn("a `None` gate offers no start", self.epic)
        self.assertIn("A `serial-fallback` `epic.concurrency.source` is said once", self.epic)
        self.assertIn("Merges stay serial: no evaluator start while one this run started runs for another story",
                      self.epic)
        self.assertIn("never while a planner this run started grounds there", self.epic)

    def test_every_start_in_one_button_and_its_composition(self):
        self.assertIn("offering every start in one one-click button", self.router)
        self.assertIn("each start a fresh session", self.router)
        self.assertIn("one button carrying, in order, the first-turn close, the archive offer and every start "
                      "the gates allow", self.spine)
        self.assertIn("The second button: the same without the archive when it rides beside other steps; "
                      "`Not now` when it is alone", self.spine)
        self.assertIn("else, with two or more starts, the first start alone", self.spine)
        self.assertIn("A behind epic worktree's sync is named once, before the first planner start", self.spine)
        # The host cuts a reply button's message at 300 characters; folders resolve from prep on the click.
        self.assertIn("Past the host's 300-character button cap, name each start by command alone; the click "
                      "reads its folder from its entry's `next.folder`", self.spine)

    def test_a_stories_handoff_reconciles_against_its_own_entry(self):
        self.assertIn("at `stories`, with the `next` of the entry for the story the session was started for",
                      self.spine)
        self.assertIn("never the number in the command, since a resolver's `evaluator #<PR>` names a PR", self.spine)
        self.assertIn("`planner #M` also reads M's entry, never a pick", self.spine)
        self.assertIn("its entry's `worktree.path`", self.spine)

    def test_a_moved_base_re_enters_the_resolver_and_the_evaluator_checks_it(self):
        self.assertIn("a ready PR whose base moved — `next.base_moved`", self.router)
        self.assertIn("- Base check: <<an epic story's evaluator: before merging", self.brief)
        self.assertIn("`git merge-base --is-ancestor` that the PR head contains the current "
                      "`origin/<epic branch>` tip", self.brief)
        self.assertIn("A `base_moved` resolver re-entry: merge the epic tip into the story branch (never rebase)",
                      self.brief)
        background = self.brief[self.brief.index("Background"):self.brief.index("How to report")]
        self.assertIn("- Base check:", background)

    def test_the_archive_point(self):
        self.assertIn("**The archive point.** After a unit's worktree closes, offer as the turn's button (never a "
                      "routine step)", self.router)
        self.assertIn("only when no session this run started waits on the operator (an unanswered card, a stop "
                      "its note reported) or holds a `## Handoff` not yet reconciled (spine §3)", self.router)
        self.assertIn("between waves, the epic's final close", self.router)
        self.assertIn("Environment-gated: with no archive tool, no offer", self.router)
        self.assertIn("After a close, the archive point (router §3)", self.spine)
        self.assertIn("offering the epic planner's sessions for archive", self.epic)

    def test_the_trust_pointer_rides_the_first_open(self):
        self.assertIn("A run's first open of a work worktree says beside the button", self.spine)
        self.assertIn("Desktop may ask once to trust that folder: "
                      "`${CLAUDE_PLUGIN_ROOT}/skills/workspace-open/references/workspace-trust.md` "
                      "`## What you can set yourself`, its first item (accept once per worktree folder)", self.spine)
        trust = REPO_ROOT / "skills" / "workspace-open" / "references" / "workspace-trust.md"
        self.assertIn("## What you can set yourself", trust.read_text(encoding="utf-8"))

    def test_the_parent_epic_offer_is_a_button(self):
        self.assertIn("offer, as the button the router §3 operator-task rule names, to carry on with its parent epic",
                      self.single)


if __name__ == "__main__":
    unittest.main()
