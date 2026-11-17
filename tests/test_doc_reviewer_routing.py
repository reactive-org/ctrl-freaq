"""Offline tests for the doc-reviewer skill (docs/implementation.md S19 — the eighth/last cutover).

Surfaces the S19 DoD / Testing names for the offline half (box 4 + the report structure + the
scaffold; boxes 1/2/3's live halves are the operator's):

1. **Structural bars.** Router <= 150 (architecture.md §9 — the bar S19 binds to; the DoD has NO
   line-count box). The <=half-v1 metric (floor(144/2) = 72) is **recorded, not enforced** (the S18
   §10-scope ruling: standalone tools are out of §10's reach): v2 ADDS the prd.md §8.2 workspace+
   landing to the leanest v1 standalone tool (144 lines, no landing at all), so the loaded set
   legitimately exceeds it — see docs/specs/parity/doc-reviewer.md. A ceiling test guards against
   unbounded growth (bump-with-justification per the S18 Scenario-2 Div-4 ceiling ruling).
2. **Frontmatter pins.** name: doc-reviewer / opus / high / **`disable-model-invocation: true`** —
   RETAINED (the S17 rule: setup was the exception; doc-reviewer IS in the CLAUDE.md:73 standalone
   trio that keeps the key, and v1 carried it).
3. **Contract-token grep gates** over skills/doc-reviewer/: zero retired-executor tokens, zero
   `github-pipeline:github-`, zero `GATHER_`/`PERSIST_` op names, zero `§P` IDs, zero raw gh WRITES
   in fences, zero `w/`.
4. **The §8.2 landing gate** (DoD boxes 2/3 offline half): offered as ONE explicit final gate; on
   decline zero git actions + the workspace path + ready-to-run commands. The post-Div-4 shape:
   pr.md is staged BEFORE the gate; the decline commands run exactly as printed.
5. **No prep** (S19 Work): the router's Prep section pins the deliberate no-prep assertion, and no
   `prep_doc_reviewer.py` exists — the inputs are working-tree paths the operator names.
6. **Report structure preserved** (DoD box 1 offline anchor): the carried v1 report shape (verdict /
   What's working / Findings by severity / Guide checklist) is present verbatim in the reference.
7. **Carried lenses + guide resolution** (S19 Work): the five review lenses in order, the three
   honesty rules, and the bundle-only guide-resolution rule survive the cutover.
8. **No sub-agent** (S19 spec §"Sub-agents dispatched: None"): the references/ dir carries no
   `*-prompt*`/`*-sub-agent*` file, so test_subagent_prompts correctly does not bind it.
9. **The `--dry-run` create-pr envelope** — the landing PR write, conformant, would_run present, no
   url.
10. **Stack-agnostic.** The loaded prompt (router + playbook) makes no bare stack assumption; the
    reference's honesty-rule example is a labeled multi-stack example (>= 2 stacks).
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
SKILL_DIR = REPO_ROOT / "skills" / "doc-reviewer"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
REFERENCES_DIR = SKILL_DIR / "references"
FLOW = "review-flow.md"
LENSES = REFERENCES_DIR / "review-lenses.md"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"

# floor(v1 doc-reviewer/SKILL.md 144 lines, docs/specs/doc-reviewer.md §1) / 2 = 72 — the RECORDED metric.
V1_HALF_BAR = 144 // 2
# The enforced growth ceiling (guards unbounded growth without demanding the unmet <=half; see the
# module docstring). Bump WITH a recorded justification per the S18 Scenario-2 Div-4 ceiling ruling.
#
# Bump log (each entry = the recorded justification the ruling requires):
#   155 -> 160 (S19 Scenario-2 Div-1 fix): the approve path's landing now EXECUTES the explicit
#   `git -C <workspace>` add/commit/push form instead of leaving those git ops to the session's
#   inherited cwd (+7 playbook lines: the 3-command fence + the falsifiable cwd invariant). Load-
#   bearing — it is the fix for the ambient-cwd drift class architecture.md §12 kills — so the
#   ceiling moves rather than the invariant being trimmed. Recorded in
#   docs/specs/parity/doc-reviewer.md §"Line-count metrics".
LOADED_SET_CEILING = 160

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
        self.assertIn("name: doc-reviewer", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)
        # DoD box 4 (S17/S18 rule): doc-reviewer RETAINS the key — it is one of the three CLAUDE.md:73
        # standalone tools that keep it (setup was the sole exception).
        self.assertIn("disable-model-invocation: true", head)

    def test_exactly_one_playbook_on_disk(self):
        on_disk = {p.name for p in PLAYBOOKS_DIR.glob("*.md")}
        self.assertEqual(on_disk, {FLOW}, "doc-reviewer has one linear flow — one playbook, got %r" % on_disk)

    def test_router_points_at_the_single_flow(self):
        self.assertIn("playbooks/review-flow.md", self.router_text)
        self.assertRegex(self.router_text, r"[Nn]o mode fork")

    def test_router_at_most_150_lines(self):
        n = len(self.router_text.splitlines())
        self.assertLessEqual(n, 150, "router is %d lines (architecture.md §9 bar <= 150)" % n)

    def test_loaded_set_under_ceiling_and_half_metric_recorded(self):
        # The <=half-v1 metric (72) is recorded, not enforced (no line-count DoD box; v2 adds §8.2). A
        # ceiling still guards growth. If this ever drops to <= V1_HALF_BAR, tighten the enforced bar.
        router = len(self.router_text.splitlines())
        playbook = len((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").splitlines())
        total = router + playbook
        self.assertLessEqual(
            total, LOADED_SET_CEILING,
            "router (%d) + playbook (%d) = %d exceeds the growth ceiling %d "
            "(bump WITH a recorded justification per the Div-4 ceiling ruling)"
            % (router, playbook, total, LOADED_SET_CEILING),
        )
        self.assertGreater(
            total, V1_HALF_BAR,
            "loaded set now fits the <=half bar (%d) — promote it to an enforced test" % V1_HALF_BAR,
        )


class ContractTokenGateTests(unittest.TestCase):
    def test_no_github_ops_or_old_namespace_or_op_names_or_pids(self):
        forbidden = FORBIDDEN_CONTRACT_TOKENS
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = forbidden.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden token under skills/doc-reviewer/ at %s:%d — %r"
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


class NoPrepAssertionTests(unittest.TestCase):
    """S19 Work: no prep script (nothing to gather) — VERIFIED against the spec and PINNED so a future
    editor knows the absence is deliberate."""

    def setUp(self):
        self.router = ROUTER.read_text(encoding="utf-8")

    def test_no_prep_script_exists(self):
        self.assertFalse(
            (SCRIPTS_DIR / "prep_doc_reviewer.py").exists(),
            "doc-reviewer has nothing to gather — there must be no prep_doc_reviewer.py",
        )

    def test_router_pins_the_no_prep_assertion(self):
        self.assertRegex(self.router, r"[Nn]o prep script")
        self.assertRegex(self.router, r"working-tree paths")
        self.assertRegex(self.router, r"deliberate")

    def test_router_declares_no_prep_script_and_no_gather(self):
        # The router names the (absent) script precisely to pin its deliberate absence, so a future
        # editor reads "no prep_doc_reviewer.py and no gather round-trip" as intent, not omission.
        # (The v1 delegated executor does not exist in v2 at all — §9.1 — so the token
        # is correctly absent from the loaded prompt, not merely negated.)
        self.assertRegex(self.router, r"no `prep_doc_reviewer\.py`")
        self.assertRegex(self.router, r"no gather round-trip")


class LandingGateLanguageTests(unittest.TestCase):
    """DoD boxes 2/3 offline half: the prd.md §8.2 landing gate is pinned as prose — offered ONCE, and
    on decline zero git actions + the workspace path + ready-to-run commands. Post-Div-4 shape."""

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
        # S18 Scenario-2 Div-4 fix, inherited from day one: the PR body (pr.md) is authored BEFORE the
        # landing gate, so approve AND decline share one staged file — the decline path's create-pr
        # command then references a real file, not a phantom only the approve path would have authored.
        stage_idx = self.flow.find("Stage the PR body")
        gate_idx = self.flow.find("one explicit gate")
        self.assertNotEqual(stage_idx, -1, "flow must stage the PR body")
        self.assertNotEqual(gate_idx, -1, "flow must offer the landing gate")
        self.assertLess(stage_idx, gate_idx, "pr.md must be staged BEFORE the landing gate")
        self.assertRegex(self.flow, r"BEFORE the\s*.{0,6}\s*gate")

    def test_decline_commands_are_runnable_as_printed(self):
        # The decline path's reported landing commands must cite the already-staged pr.md — a
        # falsifiable invariant (citing an unstaged file is a defect).
        self.assertIn("/tmp/gh-doc-reviewer-<doc-slug>/pr.md", self.flow)
        self.assertRegex(self.flow, r"run exactly as printed")
        self.assertRegex(self.flow, r"citing an unstaged file is a defect")

    def test_report_only_skips_workspace_when_nothing_accepted(self):
        # When no finding is accepted there is nothing to land — no workspace, no landing.
        self.assertRegex(self.flow, r"no finding was accepted")
        self.assertRegex(self.flow, r"report-only")

    def test_approve_path_git_commands_are_cwd_independent(self):
        # S19 Scenario-2 Div-1 fix: the APPROVE path must EXECUTE the explicit `git -C <workspace>`
        # form the decline path prints. A bare `git add`/`commit`/`push` inherits the session cwd —
        # ambient state one reordering away from staging in the read-only root (architecture.md §12,
        # "no ambient cwd in prompts"). Fence-scoped per the house pattern, so prose describing the
        # banned form doesn't false-positive.
        bare_git = re.compile(r"(?<!-C )\bgit\s+(add|commit|push)\b")
        saw_git_c = False
        for i, line, in_fence in _fence_stripped_lines(PLAYBOOKS_DIR / FLOW):
            if not in_fence or line.strip().startswith("```"):
                continue
            if re.search(r"\bgit\s+-C\s+\S+\s+(add|commit|push)\b", line):
                saw_git_c = True
                continue
            hit = bare_git.search(line)
            self.assertIsNone(
                hit,
                "bare git relying on session cwd in a code fence at %s:%d — %r (use `git -C <workspace>`)"
                % ((PLAYBOOKS_DIR / FLOW).relative_to(REPO_ROOT), i, line),
            )
        self.assertTrue(
            saw_git_c,
            "the approve path must EXECUTE `git -C <workspace>` add/commit/push in a fence, not "
            "leave the landing's git ops to an inherited cwd",
        )
        # And the invariant is stated in falsifiable form.
        self.assertRegex(self.flow, r"bare `git add`/`commit`/`push` relying on the session's cwd is a defect")
        self.assertRegex(self.flow, r"never bare git and never a `cd`")


class ReportStructureTests(unittest.TestCase):
    """DoD box 1 offline anchor: the carried v1 report shape is preserved verbatim in the reference.
    (There is NO prd.md §7 examples/ capture for a doc-review report — it is session output, never
    persisted — so box-1 parity is measured against this carried v1 shape + the vantage render, per
    docs/specs/parity/doc-reviewer.md.)"""

    def setUp(self):
        self.lenses = LENSES.read_text(encoding="utf-8")

    def test_no_examples_capture_exists_for_a_doc_review_report(self):
        # Falsifiable guard for the "carried, not frozen" claim: if a capture is ever added, this test
        # fails and the reference must instead byte-match it. Glob sweep (not two literal names) so any
        # future capture — doc-review.md, doc-review-report.md, doc-reviewer-report.md, … — trips it.
        examples = REPO_ROOT / "docs" / "specs" / "examples"
        captures = [
            p.name for p in examples.glob("*.md")
            if "doc-review" in p.name or "doc-reviewer" in p.name
        ]
        self.assertEqual(
            captures, [],
            "a doc-review report capture appeared in docs/specs/examples/ (%r) — box-1 parity must now "
            "byte-match it, and the reference's carried report shape should cite it" % captures,
        )

    def test_report_shape_carries_the_fixed_structure(self):
        for token in (
            "# Doc review — <doc path>",
            "Verdict: <Aligned | Minor drift | Significant drift>",
            "## What's working",
            "## Findings",
            "### 🔴 Blocker",
            "### 🟡 Should-fix",
            "### 🟢 Consider",
            "## Guide checklist",
            "- [x] <checklist item>",
        ):
            self.assertIn(token, self.lenses, "report shape dropped %r" % token)

    def test_findings_ordered_and_empty_section_stated(self):
        self.assertRegex(self.lenses, r"Blocker\s*.\s*Should-fix\s*.\s*Consider")
        self.assertRegex(self.lenses, r"say so rather than")


class CarriedLensesTests(unittest.TestCase):
    """S19 Work: the guide-resolution rule and the review lenses are carried (paths only changed)."""

    def setUp(self):
        self.lenses = LENSES.read_text(encoding="utf-8")

    def test_five_lenses_present_in_order(self):
        order = [
            "Authoring principles",
            "What belongs here vs. the sibling docs",
            "Anti-patterns",
            "Authoring checklist",
            "Recommended shape",
        ]
        idxs = [self.lenses.find(name) for name in order]
        self.assertNotIn(-1, idxs, "a review lens is missing: %r" % dict(zip(order, idxs)))
        self.assertEqual(idxs, sorted(idxs), "the five lenses are out of the fixed order")

    def test_three_honesty_rules_present(self):
        self.assertIn("Only review against what the guide says", self.lenses)
        self.assertIn("The worked example is an illustration, not a template", self.lenses)
        self.assertIn("Credit what's right", self.lenses)

    def test_guide_resolution_is_bundle_only_and_read_only(self):
        self.assertRegex(self.lenses, r"basename")
        self.assertRegex(self.lenses, r"plugin bundle")
        self.assertRegex(self.lenses, r"read-only")

    def test_router_carries_the_five_doc_guide_table(self):
        router = ROUTER.read_text(encoding="utf-8")
        for basename in ("prd.md", "architecture.md", "architecture-notes.md", "ui-design.md",
                         "constitution.md"):
            self.assertIn(
                "docs/guides/%s" % basename, router,
                "guide-resolution table dropped %s" % basename,
            )

    def test_severity_calibration_carried(self):
        self.assertIn("🔴 Blocker", self.lenses)
        self.assertIn("🟡 Should-fix", self.lenses)
        self.assertIn("🟢 Consider", self.lenses)


class NoSubAgentTests(unittest.TestCase):
    """S19 spec §'Sub-agents dispatched: None': doc-reviewer dispatches no sub-agent, so references/
    carries no decision-signal prompt — and test_subagent_prompts' discovery (which globs
    references/*-prompt*/*-sub-agent*) correctly finds nothing here to bind."""

    def test_references_hold_no_subagent_prompt_file(self):
        prompt_like = list(REFERENCES_DIR.glob("*-prompt*.md")) + list(REFERENCES_DIR.glob("*-sub-agent*.md"))
        self.assertEqual(
            prompt_like, [],
            "doc-reviewer dispatches no sub-agent — no *-prompt*/*-sub-agent* reference expected, got %r"
            % [p.name for p in prompt_like],
        )

    def test_no_agent_dispatch_in_the_loaded_prompt(self):
        for path in (ROUTER, PLAYBOOKS_DIR / FLOW):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("subagent_type", text, "%s dispatches a sub-agent — none expected" % path.name)


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

    def test_reference_honesty_rule_is_a_labeled_multi_stack_example(self):
        # The carried "worked example is an illustration" honesty rule names >= 2 concrete stacks as
        # EXAMPLES (Rails + Swift/Python) — the CLAUDE.md-allowed labeled multi-stack form, not a bare
        # assumption. This is the only place a stack token may appear in the skill.
        text = LENSES.read_text(encoding="utf-8")
        stacks = {m.group(0).lower() for m in self._BARE_STACK.finditer(text)}
        self.assertGreaterEqual(
            len(stacks), 2,
            "the honesty-rule example must name >= 2 stacks (labeled multi-stack), got %r" % sorted(stacks),
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
    """DoD box 2 offline half: the landing PR write (create-pr with the doc-change summary), run
    --dry-run: a conformant envelope, status ok, would_run present, exit 0, no live gh call, no url."""

    def test_create_pr_dry_run(self):
        proc, env = _run_persist(
            ["create-pr", "octo/widgets", "@BODY@", "--title", "Doc review — docs/constitution.md",
             "--base", "main", "--head", "doc-reviewer/constitution", "--dry-run"],
            body_text="## Doc changes\n\n- constitution §4: reworded the deviable default per the guide\n",
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


class SlugIdAnchorDisciplineTests(unittest.TestCase):
    """#82: consuming docs carry slug IDs (`R-` / `A-` / `C-` / `U-`). Apply-time discipline and the
    Blocker calibration protect a cited ID as they protected a `§N` number, and still protect a legacy
    numbered section."""

    def setUp(self):
        self.lenses = LENSES.read_text(encoding="utf-8")
        self.router = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    def test_lenses_forbid_renaming_or_removing_a_cited_slug_id(self):
        flat = re.sub(r"\s+", " ", self.lenses)
        self.assertIn("Never rename or remove a section's slug ID", flat)
        self.assertIn("never renumber a legacy numbered `§N` section", flat)
        self.assertIn("adding a slug ID beside a legacy number", flat)

    def test_blocker_covers_renamed_ids_and_duplicates(self):
        flat = re.sub(r"\s+", " ", self.lenses)
        self.assertIn("a *renamed or removed* slug ID", flat)
        self.assertIn("A duplicate slug ID in the repo", flat)

    def test_router_states_the_slug_rule(self):
        flat = re.sub(r"\s+", " ", self.router)
        self.assertIn("slug ID (`R-<slug>`, `A-<slug>`, `C-<slug>`, `U-<slug>`) is never renamed or removed", flat)

