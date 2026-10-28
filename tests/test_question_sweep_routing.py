"""Offline tests for the question-sweep skill (docs/implementation.md S18).

Surfaces the S18 DoD / Testing names for the sweep half:

1. **Structural bars.** Router ≤ 150 (architecture.md §9 — the bar the brief binds S18 to; the DoD has
   NO line-count box). The ≤half-v1 metric (floor(147/2) = 73) is **recorded, not enforced**: v2 ADDS the
   prd.md §8.2 workspace+landing to the leanest v1 standalone tool (147 lines, no landing at all), so the
   loaded set legitimately exceeds it — see docs/specs/parity/question-pair.md. A ceiling test still guards
   against unbounded growth.
2. **Frontmatter pins.** name: question-sweep / opus / high / **`disable-model-invocation: true`** (the S18
   rule — the sweep is one of the three standalone tools that DO carry the key; setup was the exception).
3. **Contract-token grep gates** over skills/question-sweep/: zero retired-executor / v1-namespace tokens,
   zero `GATHER_`/`PERSIST_` op names, zero `§P` IDs, zero raw gh WRITES in fences, zero `w/`.
4. **The §8.2 landing gate** (DoD boxes 2/3 offline half): offered as ONE explicit final gate; on decline
   zero git actions + the workspace path + ready-to-run commands — pinned as prose in the flow + router.
5. **The converged question-status reader (S11 completion).** Its returnable code is the §3 `AMBIGUOUS`;
   it cites architecture.md §3, NOT the retired `subagent-decision-signal` doc.
6. **The `--dry-run` create-pr envelope** — the landing PR write, conformant, would_run present, no url.
7. **Stack-agnostic.** The loaded prompt makes no bare stack assumption.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SKILL_DIR = REPO_ROOT / "skills" / "question-sweep"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
REFERENCES_DIR = SKILL_DIR / "references"
FLOW = "sweep-flow.md"
READER = REFERENCES_DIR / "question-status-reader-prompt.md"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"

# floor(the v1 sweep SKILL.md's 147 lines, docs/specs/baseline.md §1) / 2 = 73 — the RECORDED metric.
V1_HALF_BAR = 147 // 2
# The enforced ceiling (guards unbounded growth without demanding the unmet ≤half; see the module docstring).
LOADED_SET_CEILING = 125

sys.path.insert(0, str(SCRIPTS_DIR))

from tests.support import envelope_asserts, shimenv  # noqa: E402
from tests.support.retired_tokens import (  # noqa: E402
    FORBIDDEN_CONTRACT_TOKENS,
    retired_name_hits,
)


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
        self.assertIn("name: question-sweep", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)
        # DoD box 5 (S18 rule): the sweep DOES carry the key (unlike setup) — its v1 predecessor carried it
        # and CLAUDE.md:73 names it among the three standalone tools that keep it.
        self.assertIn("disable-model-invocation: true", head)

    def test_exactly_one_playbook_on_disk(self):
        on_disk = {p.name for p in PLAYBOOKS_DIR.glob("*.md")}
        self.assertEqual(on_disk, {FLOW}, "the sweep has one linear flow — one playbook, got %r" % on_disk)

    def test_router_points_at_the_single_flow(self):
        self.assertIn("playbooks/sweep-flow.md", self.router_text)
        self.assertRegex(self.router_text, r"[Nn]o mode fork")

    def test_router_at_most_150_lines(self):
        n = len(self.router_text.splitlines())
        self.assertLessEqual(n, 150, "router is %d lines (architecture.md §9 bar <= 150)" % n)

    def test_loaded_set_under_ceiling_and_half_metric_recorded(self):
        # The ≤half-v1 metric (73) is recorded, not enforced (no line-count DoD box; v2 adds §8.2). A
        # ceiling still guards growth. If this ever drops to <= V1_HALF_BAR, tighten the enforced bar.
        router = len(self.router_text.splitlines())
        playbook = len((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").splitlines())
        total = router + playbook
        self.assertLessEqual(
            total, LOADED_SET_CEILING,
            "router (%d) + playbook (%d) = %d exceeds the growth ceiling %d"
            % (router, playbook, total, LOADED_SET_CEILING),
        )
        self.assertGreater(
            total, V1_HALF_BAR,
            "loaded set now fits the ≤half bar (%d) — promote it to an enforced test" % V1_HALF_BAR,
        )


class ContractTokenGateTests(unittest.TestCase):
    def test_no_github_ops_or_old_namespace_or_op_names_or_pids(self):
        forbidden = FORBIDDEN_CONTRACT_TOKENS
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = forbidden.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden token under skills/question-sweep/ at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_forward_pointers_are_v2_skill_names(self):
        for path in _iter_md(SKILL_DIR):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(retired_name_hits(text), [], "%s names a retired v1 skill" % path.name)

    def test_no_raw_persist_or_gather_writes_in_fences(self):
        raw_write = re.compile(
            r"\bgh\s+(issue|pr)\s+(create|edit|comment|review|close|reopen|merge)\b"
            r"|\bgh\s+api\b[^\n]*\bDELETE\b"
        )
        for path in _iter_md(SKILL_DIR):
            for i, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                # `gh label create` (audience labels, per question-issue.md) is an allowed inline create.
                if "gh label create" in line:
                    continue
                hit = raw_write.search(line)
                self.assertIsNone(
                    hit,
                    "raw gh write in a code fence at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_no_w_slash_shorthand(self):
        w_shorthand = re.compile(r"\bw/")
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(
                    w_shorthand.search(line),
                    "banned w/ shorthand at %s:%d — %r" % (path.relative_to(REPO_ROOT), i, line),
                )


class LandingGateLanguageTests(unittest.TestCase):
    """DoD boxes 2/3 offline half: the prd.md §8.2 landing gate is pinned as prose — offered ONCE, and
    on decline zero git actions + the workspace path + ready-to-run commands."""

    def setUp(self):
        self.flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.router = ROUTER.read_text(encoding="utf-8")

    def test_flow_offers_landing_as_one_explicit_gate(self):
        self.assertIn("prd.md §8.2", self.flow)
        self.assertRegex(self.flow, r"one explicit\s*.{0,8}\s*gate")

    def test_flow_decline_performs_no_git_actions_and_reports_commands(self):
        self.assertRegex(self.flow, r"no git actions")
        self.assertIn("workspace path", self.flow)
        self.assertRegex(self.flow, r"ready-to-run")

    def test_landing_uses_create_pr_and_workspace_ensure(self):
        self.assertIn("gh_persist.py create-pr", self.flow)
        self.assertIn("workspace.py ensure --work", self.flow)

    def test_router_states_the_landing_invariant(self):
        self.assertIn("prd.md §8.2", self.router)
        self.assertRegex(self.router, r"[Oo]n decline")
        self.assertIn("no git actions", self.router)

    def test_pr_body_staged_before_the_landing_gate(self):
        # S18 Scenario-2 Div-4 fix: the PR body (pr.md) is authored BEFORE the landing gate, so approve
        # AND decline share one staged file — the decline path's create-pr command then references a real
        # file, not a phantom only the approve path would have authored.
        stage_idx = self.flow.find("stage the PR body")
        gate_idx = self.flow.find("one explicit gate")
        self.assertNotEqual(stage_idx, -1, "flow must stage the PR body")
        self.assertNotEqual(gate_idx, -1, "flow must offer the landing gate")
        self.assertLess(stage_idx, gate_idx, "pr.md must be staged BEFORE the landing gate")
        self.assertRegex(self.flow, r"BEFORE the gate")

    def test_decline_commands_are_runnable_as_printed(self):
        # The decline path's reported landing commands must cite the already-staged pr.md — a falsifiable
        # invariant (citing an unstaged file is a defect).
        self.assertIn("<facts.scratch>/pr.md", self.flow)
        self.assertRegex(self.flow, r"run exactly as printed")
        self.assertRegex(self.flow, r"citing an unstaged file is a defect")


class ReportCountIntegrityTests(unittest.TestCase):
    """S18 Scenario-1 Div-2 fix: the §3 report shape states the single-source count invariant, so a
    per-class header count can never disagree with its own classification-table rows (the live defect:
    header `orphaned-issue — 5` over a 6-row table). Pins the falsifiable instruction prose; the rendered
    report is the model's at runtime, so Scenario 2's live render is the confirmation (not self-certified)."""

    def setUp(self):
        self.flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")

    def test_flow_states_the_single_source_count_invariant(self):
        # The counts derive from ONE source — the classification table's rows — never tallied independently.
        self.assertRegex(self.flow, r"row count in this one table|that class's row count")
        self.assertRegex(self.flow, r"never tally|never re-count|never recount")

    def test_flow_names_the_mismatch_as_a_defect(self):
        self.assertRegex(self.flow, r"mismatch is a defect")


class ConvergedReaderTests(unittest.TestCase):
    """S11 completion: the carried question-status reader converges on the §3 vocabulary — it cites
    architecture.md §3 and no longer cites the retired subagent-decision-signal doc. (The S11 drift-check
    validator, tests/test_subagent_prompts.py, auto-binds this prompt the moment the playbooks/ dir lands
    and asserts codes ⊆ §3 + no old-doc citation over it; these are the sweep-local anchors.)"""

    def setUp(self):
        self.reader = READER.read_text(encoding="utf-8")

    def test_reader_exists_and_returns_ambiguous(self):
        self.assertTrue(READER.is_file())
        self.assertIn("code: AMBIGUOUS", self.reader)

    def test_reader_takes_a_decision_marker_from_a_trusted_author_only(self):
        # #179: Tier 1 skips an untrusted `<!-- question-decision:v1 -->`; were the reader to accept
        # any marked comment, Tier 2 would re-admit exactly what Tier 1 refused.
        check = self.reader[self.reader.index("**First, check for a recorded decision.**"):]
        check = check[:check.index("Walk the thread")]
        self.assertIn("`authorAssociation` is `OWNER`, `MEMBER` or `COLLABORATOR`", check)
        self.assertIn("is not a recorded decision", check)

    def test_reader_cites_section3_not_the_retired_doc(self):
        self.assertNotIn("subagent-decision-signal", self.reader)
        self.assertRegex(self.reader, r"architecture\.md §3")

    def test_reader_dispatch_path_is_the_sweep_home(self):
        # The flow (and the resolver) reach this reader via the question-sweep path — its new home.
        flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.assertIn("references/question-status-reader-prompt.md", flow)


class StackAgnosticTests(unittest.TestCase):
    _BARE_STACK = re.compile(r"\b(swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest)\b", re.IGNORECASE)

    def test_loaded_prompt_has_no_bare_stack_token(self):
        for path in (ROUTER, PLAYBOOKS_DIR / FLOW):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = self._BARE_STACK.search(line)
                self.assertIsNone(
                    hit,
                    "bare stack token in the loaded prompt at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )


def _run_persist(args, body_text=None):
    env = shimenv.intercepted_env(base_env=os.environ, fixture_case=None)
    ctx = None
    if body_text is not None:
        ctx = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        ctx.write(body_text)
        ctx.close()
        args = [a.replace("@BODY@", ctx.name) for a in args]
    try:
        proc = subprocess.run(
            [sys.executable, str(GH_PERSIST)] + args,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
    finally:
        if ctx is not None:
            os.unlink(ctx.name)
    envelope = None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) == 1:
        envelope = json.loads(lines[0])
    return proc, envelope


class LandingPersistDryRunTests(unittest.TestCase):
    """DoD box 2 offline half: the landing PR write (create-pr with the doc/link-change summary), run
    --dry-run: a conformant envelope, status ok, would_run present, exit 0, no live gh call, no url."""

    def test_create_pr_dry_run(self):
        proc, env = _run_persist(
            ["create-pr", "octo/widgets", "@BODY@", "--title", "Reconcile open questions",
             "--base", "main", "--head", "question-sweep/oq-prd", "--dry-run"],
            body_text="## Reconciliation\n\n- filed #211 (companion for PRD-OQ-05)\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIsNotNone(env, "expected exactly one envelope on stdout")
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("op"), "create-pr")
        self.assertTrue(env.get("dry_run"))
        self.assertIn("would_run", env)
        self.assertNotIn("url", env, "a dry-run must not carry a live-write url")


if __name__ == "__main__":
    unittest.main()
