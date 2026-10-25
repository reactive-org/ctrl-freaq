"""Offline tests for the workspace-open skill (the v3 workspace-model inversion).

Shape-B standalone-tool gates (the question-sweep/question-resolver template): router structure +
frontmatter pins (`disable-model-invocation: true` — operator-invoked only), the contract-token
grep set, the prep-invocation fence, and the summary-language pins that carry the v3 posture —
"start the next session in <path>" and the plan-before-open routing (never "run the planner in
this worktree"). The one reference, `references/workspace-trust.md`, is operator guidance, not a
sub-agent prompt, so `tests/test_subagent_prompts.py` enrolls nothing here — the fence-scoped
raw-write/ref-arithmetic gates below are this skill's only prose regression net, and
`TrustGuidanceTests` pins that reference and the repo-wide no-trust-write rule (#114).
"""

import re
import unittest
from pathlib import Path

from tests.support.retired_tokens import FORBIDDEN_CONTRACT_TOKENS, retired_name_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "workspace-open"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
FLOW = "open-flow.md"
TRUST_REF = SKILL_DIR / "references" / "workspace-trust.md"

# A new v3 tool — no v1 counterpart, so no ≤half-v1 metric exists (the prd §10 prompt-economy bar
# is a pipeline-stage bar; tools are measured, never force-trimmed). Growth ceiling only.
LOADED_SET_CEILING = 150


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
        self.assertIn("name: workspace-open", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)
        self.assertIn("disable-model-invocation: true", head)

    def test_exactly_one_playbook_on_disk(self):
        on_disk = {p.name for p in PLAYBOOKS_DIR.glob("*.md")}
        self.assertEqual(on_disk, {FLOW})

    def test_router_points_at_the_single_flow(self):
        self.assertIn("playbooks/open-flow.md", self.router_text)
        self.assertRegex(self.router_text, r"[Nn]o mode fork")

    def test_router_at_most_150_lines(self):
        n = len(self.router_text.splitlines())
        self.assertLessEqual(n, 150, "router is %d lines (architecture.md §9 bar <= 150)" % n)

    def test_loaded_set_under_ceiling(self):
        total = len(self.router_text.splitlines()) + len(
            (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").splitlines()
        )
        self.assertLessEqual(total, LOADED_SET_CEILING)

    def test_no_prep_free_invocation_claims(self):
        # The prep IS the action — the router must invoke prep_workspace_open.py by
        # ${CLAUDE_PLUGIN_ROOT} path, and never raw `gh issue develop` (script-internal only).
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/prep_workspace_open.py", self.router_text)


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

    def test_no_gh_issue_develop_in_fences(self):
        # The link write is prep-internal by design — a fence carrying `gh issue develop` would
        # put a GitHub write back into the prompt layer (and dodge validator #4's alternation).
        develop = re.compile(r"\bgh\s+issue\s+develop\b")
        for path in _iter_md(SKILL_DIR):
            for i, line, in_fence in _fence_stripped_lines(path):
                if in_fence:
                    self.assertIsNone(
                        develop.search(line),
                        "gh issue develop in a fence at %s:%d" % (path.relative_to(REPO_ROOT), i),
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


class SummaryLanguageTests(unittest.TestCase):
    """The v3 posture's load-bearing summary lines."""

    def setUp(self):
        self.router = ROUTER.read_text(encoding="utf-8")
        self.flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.combined = re.sub(r"\s+", " ", self.router + " " + self.flow)

    def test_summary_not_a_handoff(self):
        self.assertIn("Summary — not a `## Handoff`", self.router)

    def test_next_step_names_the_worktree_path(self):
        self.assertRegex(self.combined, r"start the next session")

    def test_plan_before_open_routing(self):
        # Review B1: the summary must never route the operator into the just-opened worktree to
        # PLAN — a missing plan sends them to the planner from a main checkout / parent-epic
        # worktree first.
        self.assertRegex(self.combined, r"[Nn]ever route the operator into the just-opened worktree to \*?plan\*?")
        self.assertIn("/github-pipeline:planner", self.combined)
        self.assertIn("/github-pipeline:resolver", self.combined)

    def test_gated_row_creates_nothing(self):
        self.assertRegex(self.combined, r"prep created \*?\*?nothing\*?\*?|created nothing")

    def test_never_proceeds_into_resolution(self):
        self.assertRegex(self.combined, r"never (proceeds|plans|invokes another skill)")


class StackAgnosticTests(unittest.TestCase):
    def test_no_stack_assumptions(self):
        stack = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(
                    stack.search(line),
                    "stack assumption at %s:%d — %r" % (path.relative_to(REPO_ROOT), i, line),
                )


class TrustGuidanceTests(unittest.TestCase):
    """#114: the workspace-trust findings, and the rule that the pipeline never reads, writes or
    instructs trust or permission state. Trust is a security setting the operator owns."""

    SECTIONS = (
        "## Status",
        "## The three routes",
        "## What you can set yourself",
        "## The probe",
        "## What the pipeline never does",
    )
    # A trust write, a trust skip, or a permission write. Prose may name these as findings; a
    # code fence (something to run) or a script (something the pipeline runs) may not.
    TRUST_WRITE = re.compile(
        r"hasTrustDialogAccepted|\.claude\.json|--trust-workspace"
        r"|CLAUDE_CODE_DISABLE_ULTRAREVIEW_TRUST_CHECK|\.claude/settings(\.local)?\.json"
        r"|permissions\.allow"
    )

    def test_trust_reference_exists_and_is_linked(self):
        self.assertTrue(TRUST_REF.is_file())
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}/skills/workspace-open/references/workspace-trust.md",
            ROUTER.read_text(encoding="utf-8"),
        )
        self.assertIn("router §4 trust pointer", (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8"))

    def test_trust_reference_sections(self):
        lines = TRUST_REF.read_text(encoding="utf-8").splitlines()
        positions = []
        for heading in self.SECTIONS:
            self.assertIn(heading, lines)
            positions.append(lines.index(heading))
        self.assertEqual(positions, sorted(positions), "sections out of order")

    def test_trust_reference_cites_current_docs(self):
        text = TRUST_REF.read_text(encoding="utf-8")
        for page in ("permissions", "worktrees", "desktop", "settings-reference"):
            self.assertIn("code.claude.com/docs/en/%s" % page, text)
        self.assertIn("2026-10-25", text)

    def test_no_skill_or_script_instructs_trust_or_permission_writes(self):
        hits = []
        for path in sorted((REPO_ROOT / "skills").rglob("*.md")):
            for i, line, in_fence in _fence_stripped_lines(path):
                if in_fence and self.TRUST_WRITE.search(line):
                    hits.append("%s:%d" % (path.relative_to(REPO_ROOT), i))
        for path in sorted((REPO_ROOT / "scripts").rglob("*.py")):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.TRUST_WRITE.search(line):
                    hits.append("%s:%d" % (path.relative_to(REPO_ROOT), i))
        self.assertEqual(hits, [], "trust or permission state named in a fence or a script")

    def test_the_trust_write_pattern_matches_what_it_guards(self):
        # Defect-inject the scan: each token the pattern exists to catch must match.
        for token in (
            "projects[p].hasTrustDialogAccepted = true",
            "~/.claude.json",
            "claude ultrareview --trust-workspace",
            "CLAUDE_CODE_DISABLE_ULTRAREVIEW_TRUST_CHECK=1",
            "cp .claude/settings.local.json .",
            "edit .claude/settings.json",
            '"permissions.allow"',
        ):
            self.assertIsNotNone(self.TRUST_WRITE.search(token), token)


if __name__ == "__main__":
    unittest.main()
