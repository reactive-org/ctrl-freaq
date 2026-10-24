"""Offline tests for the review-report skill (#98): a read-only standalone tool whose prep is its action.

Shape-B standalone-tool gates (the workspace-open template): router structure and frontmatter
(`disable-model-invocation: true`, operator-invoked only), the prep fence, the contract-token grep set,
the fence-scoped raw-write / ref-arithmetic gates, and the summary-language pins that carry its posture:
read-only, the two bases never pooled, the security tally beside the outcomes. The scripts are pinned
too: neither the core nor the prep may reach a GitHub write.
"""

import re
import unittest
from pathlib import Path

from tests.support.retired_tokens import FORBIDDEN_CONTRACT_TOKENS, retired_name_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "review-report"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
FLOW = "report-flow.md"
SCRIPTS = (REPO_ROOT / "scripts" / "review_report.py", REPO_ROOT / "scripts" / "prep_review_report.py")

# A new tool with no v1 counterpart: a growth ceiling only (the prd §10 bar is a pipeline-stage bar).
LOADED_SET_CEILING = 150


def _iter_md(dir_path):
    yield from sorted(dir_path.rglob("*.md"))


def _fenced_lines(path):
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            yield i, line


class RouterStructureTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")

    def test_router_exists_and_frontmatter(self):
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: review-report", head)
        self.assertIn("disable-model-invocation: true", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)
        self.assertIn("/github-pipeline:review-report", head)

    def test_exactly_one_playbook_on_disk(self):
        self.assertEqual({p.name for p in PLAYBOOKS_DIR.glob("*.md")}, {FLOW})
        self.assertFalse((SKILL_DIR / "references").exists())

    def test_router_points_at_the_single_flow(self):
        self.assertIn("playbooks/report-flow.md", self.router_text)
        self.assertRegex(self.router_text, r"No mode fork")

    def test_the_four_router_sections_in_order(self):
        headings = re.findall(r"^## (\d)\. (\w+)", self.router_text, re.MULTILINE)
        self.assertEqual([h[1] for h in headings], ["Prep", "Route", "Invariants", "Summary"])

    def test_loaded_set_under_ceiling(self):
        total = len(self.router_text.splitlines()) + len((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(len(self.router_text.splitlines()), 150)
        self.assertLessEqual(total, LOADED_SET_CEILING)

    def test_the_prep_is_invoked_by_plugin_path(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/prep_review_report.py <owner/repo>", self.router_text)


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
            for i, line in _fenced_lines(path):
                self.assertIsNone(banned.search(line), "%s:%d — %r" % (path.name, i, line))

    def test_no_shorthand_or_stack_assumptions(self):
        stack = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)
        for path in _iter_md(SKILL_DIR):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bw/", text), path.name)
            self.assertIsNone(stack.search(text), path.name)


class PostureTests(unittest.TestCase):
    def setUp(self):
        self.combined = re.sub(
            r"\s+", " ", ROUTER.read_text(encoding="utf-8") + " " + (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        )

    def test_read_only_and_a_summary_not_a_handoff(self):
        self.assertIn("Summary — not a `## Handoff`", self.combined)
        self.assertIn("**Read-only.** No GitHub write", self.combined)
        self.assertIn("No next command: this tool is not a stage", self.combined)

    def test_the_bases_are_never_pooled(self):
        self.assertIn("**Never pool the bases.**", self.combined)
        self.assertIn("never summed", self.combined)

    def test_security_is_beside_not_in(self):
        self.assertIn("**Security beside, not in.**", self.combined)
        self.assertIn("never in the outcome table", self.combined)

    def test_it_cites_the_record_contract(self):
        self.assertIn("(../_shared/review-loop-record.md)", self.combined)

    def test_recurring_classes_point_at_setup(self):
        # #99: the report shows exact keys; turning a recurring class into repo guidance is setup's.
        self.assertIn(
            "`/github-pipeline:setup` proposes recurring classes as entries in the repo's review-guidance block.",
            self.combined,
        )

    def test_notices_are_reported_before_figures(self):
        for notice in ("REVIEW_COMMENTS_TRUNCATED", "REVIEW_COMMENTS_UNAVAILABLE"):
            self.assertIn(notice, self.combined)


class PrdRecordsTheOwnerTests(unittest.TestCase):
    """#98 DoD 5: the script's owning tool is named, and docs/prd.md records it."""

    def test_prd_6_9_names_review_report_as_the_scripts_owner(self):
        prd = " ".join((REPO_ROOT / "docs" / "prd.md").read_text(encoding="utf-8").split())
        self.assertIn("**§6.9 review-report.**", prd)
        self.assertIn("owning tool of the script that produces that report (`scripts/prep_review_report.py`", prd)
        self.assertIn("The record it reads is the resolver's to write", prd)
        self.assertIn("All nine run only on explicit invocation", prd)

    def test_the_sibling_listings_name_the_tool(self):
        # The final cold read: the coordinator's commit updated these two sites too.
        catalogue = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        self.assertIn("the fifteen skills' requirements (§5, §6)", catalogue)
        for manifest in ("plugin.json", "marketplace.json"):
            text = (REPO_ROOT / ".claude-plugin" / manifest).read_text(encoding="utf-8")
            self.assertIn("workspace-lifecycle and review-report tools", text, manifest)


class ScriptsWriteNothingTests(unittest.TestCase):
    """#98 plan risk: "The tool reads another repo and writes nothing"."""

    def test_no_write_path_in_the_core_or_the_prep(self):
        write = re.compile(
            r"gh_persist|\"(?:create|edit|comment|review|close|reopen|merge|delete)\"|--method|\"-X\"|DELETE|PATCH|mutation"
        )
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(write.search(text), "%s reaches a write: %r" % (path.name, write.search(text)))

    def test_every_gh_call_goes_through_the_runner(self):
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("import subprocess", text, path.name)


if __name__ == "__main__":
    unittest.main()
