"""Offline tests for the requirements-gatherer skill (interactive requirement elicitation onto
one issue's `## Definition of done`).

Shape-B standalone-tool gates (the question-sweep/question-resolver template): router structure +
frontmatter pins (`disable-model-invocation: true` — operator-invoked only), the contract-token
grep set, the prep-invocation fence, and the language pins that carry this skill's load-bearing
posture — the em-dash provenance tail (never a trailing parenthetical, which is the
dod-annotations grammar's position), append-only DoD editing, cite-never-restate, and the
staged-path `gh_persist.py edit-body` write. No LandingGateLanguageTests: this tool edits no
tracked files, so prd.md §8.2's workspace/PR landing does not apply — its one write surface is
the gated issue-body edit.
"""

import re
import unittest
from pathlib import Path

from tests.support.retired_tokens import FORBIDDEN_CONTRACT_TOKENS, retired_name_hits
from tests.support.thread_rule import THREAD_RULE_ANCHORS

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "requirements-gatherer"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
FLOW = "gather.md"

# A new tool — no v1 counterpart, so no ≤half-v1 metric exists (the prd §10 prompt-economy bar is
# a pipeline-stage bar; tools are measured, never force-trimmed). Growth ceiling only: the loaded
# set landed at 102 (router) + 95 (flow) = 197 after the initial code-review fixes (the pre-write
# `parse.py dod` validation step and the plan-marker mid-flight warning — both load-bearing); 205
# leaves headroom for small fixes without licensing a rewrite-scale regrowth.
LOADED_SET_CEILING = 205


def _iter_md(dir_path):
    yield from sorted(dir_path.rglob("*.md"))


def _fence_stripped_lines(path):
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            yield i, line, in_fence
            continue
        yield i, line, in_fence


class RouterStructureTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")

    def test_router_exists_and_frontmatter(self):
        self.assertTrue(ROUTER.is_file())
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: requirements-gatherer", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)
        self.assertIn("disable-model-invocation: true", head)

    def test_exactly_one_playbook_on_disk(self):
        on_disk = {p.name for p in PLAYBOOKS_DIR.glob("*.md")}
        self.assertEqual(on_disk, {FLOW})

    def test_router_points_at_the_single_flow(self):
        self.assertIn("playbooks/gather.md", self.router_text)
        self.assertRegex(self.router_text, r"[Nn]o mode fork")

    def test_router_at_most_150_lines(self):
        n = len(self.router_text.splitlines())
        self.assertLessEqual(n, 150, "router is %d lines (architecture.md §9 bar <= 150)" % n)

    def test_loaded_set_under_ceiling(self):
        total = len(self.router_text.splitlines()) + len(
            (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").splitlines()
        )
        self.assertLessEqual(total, LOADED_SET_CEILING)

    def test_prep_invoked_by_plugin_root_path(self):
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}/scripts/prep_requirements_gatherer.py", self.router_text
        )

    def test_router_names_exactly_the_refusal_tokens_prep_can_emit(self):
        """The closed refusal set is a shared fact between prep and router — a token the router
        renders but prep never emits (or vice versa) is drift."""
        import sys

        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import prep_requirements_gatherer as prep  # noqa: E402

        emitted = {
            prep.REFUSAL_EPIC_TARGET,
            prep.REFUSAL_SLICE_TARGET,
            prep.REFUSAL_QUESTION_TARGET,
            prep.REFUSAL_CLOSED_TARGET,
        }
        for token in emitted:
            self.assertIn("`%s`" % token, self.router_text, token)


class ContractTokenGateTests(unittest.TestCase):
    def test_no_forbidden_contract_tokens(self):
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = FORBIDDEN_CONTRACT_TOKENS.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden token at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_no_v1_skill_names(self):
        for path in _iter_md(SKILL_DIR):
            hits = retired_name_hits(path.read_text(encoding="utf-8"))
            self.assertEqual(hits, [], "%s names a retired v1 skill" % path.name)

    def test_no_raw_persist_or_gather_writes_in_fences(self):
        raw_write = re.compile(
            r"\bgh\s+(issue|pr)\s+(create|edit|comment|review|close|reopen|merge)\b"
            r"|\bgh\s+api\b[^\n]*\bDELETE\b"
        )
        for path in _iter_md(SKILL_DIR):
            for i, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                hit = raw_write.search(line)
                self.assertIsNone(
                    hit,
                    "raw gh write in a code fence at %s:%d"
                    % (path.relative_to(REPO_ROOT), i),
                )

    def test_no_ref_arithmetic_in_fences(self):
        ref_arith = re.compile(r"git\s+show\s+[^\s]+:|git\s+grep\s+[^-]")
        for path in _iter_md(SKILL_DIR):
            for i, line, in_fence in _fence_stripped_lines(path):
                if in_fence:
                    self.assertIsNone(
                        ref_arith.search(line),
                        "ref arithmetic in a fence at %s:%d" % (path.relative_to(REPO_ROOT), i),
                    )

    def test_no_w_slash_shorthand(self):
        w_shorthand = re.compile(r"\bw/")
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(w_shorthand.search(line))


class WritePathLanguageTests(unittest.TestCase):
    """The one write surface and the grammar that keeps it parser-safe."""

    def setUp(self):
        self.router = ROUTER.read_text(encoding="utf-8")
        self.flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.reference = (
            SKILL_DIR / "references" / "requirements-format.md"
        ).read_text(encoding="utf-8")
        self.combined = re.sub(r"\s+", " ", self.router + " " + self.flow)

    def test_edit_body_goes_through_gh_persist_with_the_staged_path(self):
        self.assertIn("gh_persist.py", self.flow)
        self.assertIn("edit-body", self.flow)
        self.assertIn("<facts.scratch>/dod-body.md", self.flow)

    def test_diff_show_then_explicit_confirmation(self):
        self.assertRegex(self.combined, r"[Ss]how the diff")
        self.assertRegex(self.combined, r"\*\*explicit\*\* confirmation")

    def test_append_only_dod_language(self):
        self.assertRegex(
            self.combined, r"never reworded, reordered, re-ticked|never reworded"
        )
        self.assertRegex(self.combined, r"indexes must not shift")

    def test_provenance_tail_never_a_trailing_parenthetical(self):
        # The load-bearing dodge: a trailing parenthetical is the dod-annotations grammar's
        # position; the provenance rides as an em-dash tail inside the bullet text.
        for text, name in ((self.router, "router"), (self.reference, "reference")):
            flat = re.sub(r"[\s*]+", " ", text)
            self.assertRegex(
                flat, r"[Nn]ever (a |wrapped in )?(trailing )?parenthe", name
            )
        self.assertIn("operator elicited", self.reference)
        self.assertIn("— docs/prd.md §4.2", self.router + self.reference)

    def test_cite_never_restate(self):
        self.assertRegex(self.combined + " " + self.reference, r"[Cc]ite, never restate")

    def test_stable_req_id_grammar_is_pinned(self):
        # The issue-minted id other skills cite: bold prefix, no `#`, append-only sequence from
        # the prep fact — identity must outlive provenance, so no doc id inside it.
        self.assertIn("**REQ-<issue>-<seq>**", self.reference)
        self.assertIn("REQ-<issue>-<seq>", self.router)
        self.assertIn("next_req_seq", self.router + self.flow)
        self.assertRegex(
            re.sub(r"\s+", " ", self.router + " " + self.reference),
            r"never renumbered,? (or |never )?reused|never renumbered",
        )
        self.assertNotIn("REQ-#", self.reference.replace("`REQ-103-3`, never `REQ-#103-3`", ""))

    def test_mid_flight_warning_gate(self):
        self.assertIn("annotated_count", self.flow)
        self.assertRegex(self.combined, r"re-route to the planner")


class SummaryLanguageTests(unittest.TestCase):
    def setUp(self):
        self.router = ROUTER.read_text(encoding="utf-8")
        self.flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.combined = re.sub(r"\s+", " ", self.router + " " + self.flow)

    def test_summary_not_a_handoff(self):
        self.assertIn("Summary — not a `## Handoff`", self.router)

    def test_no_handoff_block_emitted_anywhere(self):
        self.assertNotIn("## Handoff\n", self.router)
        self.assertNotIn("## Handoff\n", self.flow)

    def test_breadcrumbs_are_pointers_not_handoffs(self):
        self.assertRegex(self.combined, r"a pointer, not a forward handoff")
        self.assertIn("/ctrl-freaq:planner", self.combined)
        self.assertIn("/ctrl-freaq:drafter", self.combined)
        self.assertIn("/ctrl-freaq:setup", self.combined)

    def test_operator_owns_the_set(self):
        self.assertRegex(self.combined, r"operator (owns|decides)")

    def test_no_question_issues_filed(self):
        self.assertRegex(self.combined, r"files no(thing| issues)|never filed as an issue")


class StackAgnosticTests(unittest.TestCase):
    def test_no_stack_assumptions(self):
        stack = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(
                    stack.search(line),
                    "stack assumption at %s:%d — %r" % (path.relative_to(REPO_ROOT), i, line),
                )


class ThreadWalkAuthorRuleTests(unittest.TestCase):
    """#186: the gather's thread read applies #179's author rule to marker-shaped comments."""

    def test_gather_thread_read_states_the_author_rule(self):
        text = (PLAYBOOKS_DIR / "gather.md").read_text(encoding="utf-8")
        walk = text[text.index("## 1. Read the target"):]
        walk = walk[:walk.index("`facts.dod.bullets` is")]
        for anchor in THREAD_RULE_ANCHORS:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, walk)


if __name__ == "__main__":
    unittest.main()
