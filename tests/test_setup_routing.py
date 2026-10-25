"""Offline tests for the setup skill (docs/implementation.md S17).

Surfaces the S17 DoD / Testing section names:

1. **Block byte-identity (DoD box 1).** Each canonical block form, written via the skill's prescribed
   `config_block.py upsert` invocation, reproduces byte-for-byte against the S1-frozen schema in
   `docs/specs/examples/config-blocks.md` (prd.md §7, row 11) — AND the skill's own
   `references/block-authoring.md` schema fences are byte-identical to that same S1 capture (never
   restated divergently).

2. **Legacy `pr-evaluator-health-checks` split/migration (DoD box 4).** The split mechanics — read the
   legacy block, `upsert` `pr-evaluator-static-checks` + `pr-evaluator-test-target`, `remove` the
   legacy — reproduced deterministically through `config_block.py` (the single write path).

3. **Structural bars.** Router ≤ 150; router + the one playbook ≤ 169 (floor of v1's 338); exactly one
   playbook on disk; frontmatter pins carried verbatim from v1 (opus / medium) — **no**
   `disable-model-invocation` key (S17 adjudication, DoD box 5: setup is the ONE standalone tool that
   stays model-invocable — CLAUDE.md:73 deliberately excludes it from the `disable-model-invocation`
   list, v1 never carried the key, and `docs/specs/setup.md`'s Known-bugs §1 pre-analyzed the DoD's
   "retained" wording as an over-generalization to correct, not to perpetuate).

4. **Contract-token grep gates** over skills/setup/: zero retired-executor tokens, zero v1 skill-namespace strings
   (`github-pipeline:github-`), zero `GATHER_`/`PERSIST_` op names, zero `§P` IDs, zero raw persist/gather
   WRITES in fences, zero `w/` shorthand. The v2 forward pointers are the renamed `github-pipeline:drafter`
   / `github-pipeline:resolver`.

5. **The landing gate (prd.md §8.2).** Offered as ONE explicit final gate; on decline zero git actions +
   the workspace path + ready-to-run landing commands — pinned as prose in the flow and the router.

6. **The merge-policy `docs: auto`-style option** — present in block-authoring.md, with the block keys
   held to the canonical `standard`/`story` (a `docs:` key is never written).

7. **`--dry-run` create-pr envelope** — the landing PR write, conformant, `would_run` present, no url.

8. **Stack-agnostic.** The loaded prompt (router + playbook) carries no bare stack assumption;
   `block-authoring.md` carries the labeled ≥2-stack (Swift + Rails) worked examples (the allowed form).

No network: gh_persist.py's `gh` calls resolve to the offline shim; the dry-run path performs no gh call.
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
SKILL_DIR = REPO_ROOT / "skills" / "setup"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
REFERENCES_DIR = SKILL_DIR / "references"
CONFIG_BLOCK = SCRIPTS_DIR / "config_block.py"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"
CONFIG_BLOCKS_EXAMPLE = REPO_ROOT / "docs" / "specs" / "examples" / "config-blocks.md"
BLOCK_AUTHORING = REFERENCES_DIR / "block-authoring.md"

# floor(the v1 setup SKILL.md's 338 lines, docs/specs/baseline.md §1) / 2 = 169.
V1_HALF_BAR = 338 // 2

FLOW = "setup-flow.md"

sys.path.insert(0, str(SCRIPTS_DIR))

from tests.support import envelope_asserts, shimenv  # noqa: E402
from tests.support.retired_tokens import (  # noqa: E402
    FORBIDDEN_CONTRACT_TOKENS,
    retired_name_hits,
)

# A block region: `<!-- NAME -->` … `<!-- /NAME -->` (whole-line markers, backreferenced name).
_BLOCK_RE = re.compile(
    r"^<!-- (?P<name>[a-z0-9-]+) -->\n(?P<interior>.*?)\n<!-- /(?P=name) -->$",
    re.DOTALL | re.MULTILINE,
)


def _extract_blocks(path):
    """First occurrence per marker name of each canonical block region in `path`. Returns
    {name: (full_block_text, interior_text)}."""
    text = path.read_text(encoding="utf-8")
    blocks = {}
    for match in _BLOCK_RE.finditer(text):
        name = match.group("name")
        if name in blocks:
            continue  # keep the first (schema template) occurrence
        blocks[name] = (match.group(0), match.group("interior"))
    return blocks


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


def _run_config_block(args, cwd=None):
    proc = subprocess.run(
        [sys.executable, str(CONFIG_BLOCK)] + args,
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    return proc


class RouterStructureTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")

    def test_router_exists_and_frontmatter(self):
        self.assertTrue(ROUTER.is_file())
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: setup", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)
        # DoD box 5 (S17 adjudication — do NOT "fix" this back): setup stays model-invocable. v1
        # The v1 setup skill never carried `disable-model-invocation`, and the adjudication record
        # lists the key for ONLY doc-reviewer / question-sweep / question-resolver — setup
        # is excluded on purpose (its design is trigger-heavy). docs/specs/setup.md's Known-bugs §1
        # pre-analyzed the DoD's "retained" wording as an over-generalization to correct, not
        # perpetuate. So the key must be ABSENT here.
        self.assertNotIn("disable-model-invocation", head)

    def test_exactly_one_playbook_on_disk(self):
        on_disk = {p.name for p in PLAYBOOKS_DIR.glob("*.md")}
        self.assertEqual(on_disk, {FLOW}, "setup has one linear flow — one playbook, got %r" % on_disk)

    def test_router_points_at_the_single_flow(self):
        self.assertIn("playbooks/setup-flow.md", self.router_text)
        # No mode fork — the router says so explicitly.
        self.assertRegex(self.router_text, r"[Nn]o mode fork")

    def test_router_at_most_150_lines(self):
        n = len(self.router_text.splitlines())
        self.assertLessEqual(n, 150, "router is %d lines (bar <= 150)" % n)

    def test_router_plus_playbook_at_most_half_v1(self):
        router_lines = len(self.router_text.splitlines())
        playbook_lines = len((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").splitlines())
        total = router_lines + playbook_lines
        self.assertLessEqual(
            total,
            V1_HALF_BAR,
            "router (%d) + playbook (%d) = %d exceeds %d (half of v1's 338)"
            % (router_lines, playbook_lines, total, V1_HALF_BAR),
        )

    def test_flow_forces_block_authoring_read(self):
        flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.assertIn("references/block-authoring.md", flow)
        self.assertRegex(flow, r"`Read`.*block-authoring\.md")


class ContractTokenGateTests(unittest.TestCase):
    def test_no_github_ops_or_old_namespace_or_op_names_or_pids(self):
        forbidden = FORBIDDEN_CONTRACT_TOKENS
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = forbidden.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden token under skills/setup/ at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_forward_pointers_are_v2_skill_names(self):
        router = ROUTER.read_text(encoding="utf-8")
        self.assertIn("/github-pipeline:drafter", router)
        self.assertIn("/github-pipeline:resolver", router)
        self.assertEqual(retired_name_hits(router), [], "the setup router names a retired v1 skill")

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


class BlockByteIdentityTests(unittest.TestCase):
    """DoD box 1: writing each canonical block via config_block.py reproduces the S1-frozen form
    byte-for-byte, and the skill's reference does not restate a form divergently."""

    def setUp(self):
        self.canonical = _extract_blocks(CONFIG_BLOCKS_EXAMPLE)
        # every marker setup writes (the 9 machine-parsed + header + stack-profile + the worktree pair)
        self.expected_markers = {
            "issue-resolver-fast-checks",
            "issue-resolver-test-target",
            "issue-resolver-canonical-suite",
            "pr-evaluator-static-checks",
            "pr-evaluator-test-target",
            "pr-evaluator-escalation-labels",
            "pr-evaluator-merge-policy",
            "worktree-setup",
            "worktree-teardown",
            "claude-code-stack-profile",
            "github-pipeline-config",
        }

    def test_all_setup_markers_present_in_the_s1_capture(self):
        missing = self.expected_markers - set(self.canonical)
        self.assertEqual(missing, set(), "S1 capture is missing setup markers: %r" % sorted(missing))

    def test_config_block_upsert_reproduces_each_canonical_form(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="gh-setup-byteid-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        for name in sorted(self.expected_markers):
            full_block, interior = self.canonical[name]
            body_path = tmpdir / ("%s.body.md" % name)
            body_path.write_text(interior + "\n", encoding="utf-8")
            target = tmpdir / ("%s.target.md" % name)
            target.write_text("", encoding="utf-8")
            proc = _run_config_block(["upsert", str(target), name, str(body_path)])
            self.assertEqual(proc.returncode, 0, msg="%s: %s" % (name, proc.stderr))
            produced = target.read_text(encoding="utf-8").rstrip("\n")
            self.assertEqual(
                produced,
                full_block,
                "config_block.py upsert drifted from the S1 canonical form for %r" % name,
            )

    def test_reference_schema_fences_match_the_s1_capture(self):
        ref_blocks = _extract_blocks(BLOCK_AUTHORING)
        for name in sorted(self.expected_markers):
            self.assertIn(name, ref_blocks, "block-authoring.md missing the %r schema fence" % name)
            self.assertEqual(
                ref_blocks[name][0],
                self.canonical[name][0],
                "block-authoring.md restates the %r form divergently from the S1 capture" % name,
            )


class SensitivePathsContractTests(unittest.TestCase):
    """The `pr-evaluator-sensitive-paths` block's byte pin (#96) — anchored on `block-authoring.md`'s
    fence, for the doc-catalogue reason below: the block never existed in v1, so the frozen S1 capture
    (`docs/specs/examples/config-blocks.md`) has nothing to capture truthfully, and
    `BlockByteIdentityTests.expected_markers` stays the v1 set."""

    NAME = "pr-evaluator-sensitive-paths"

    def setUp(self):
        self.ref_blocks = _extract_blocks(BLOCK_AUTHORING)

    def test_block_authoring_carries_the_canonical_fence(self):
        self.assertIn(self.NAME, self.ref_blocks, "block-authoring.md missing the sensitive-paths fence")

    def test_config_block_upsert_reproduces_the_canonical_form(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="gh-setup-sensitive-byteid-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        full_block, interior = self.ref_blocks[self.NAME]
        body_path = tmpdir / "body.md"
        body_path.write_text(interior + "\n", encoding="utf-8")
        target = tmpdir / "target.md"
        target.write_text("", encoding="utf-8")
        proc = _run_config_block(["upsert", str(target), self.NAME, str(body_path)])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(target.read_text(encoding="utf-8").rstrip("\n"), full_block)

    def test_the_fence_parses_to_its_patterns_through_the_matcher(self):
        import sensitive_paths

        tmpdir = Path(tempfile.mkdtemp(prefix="gh-setup-sensitive-parse-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        full_block, _interior = self.ref_blocks[self.NAME]
        (tmpdir / "CLAUDE.md").write_text(full_block + "\n", encoding="utf-8")
        declaration = sensitive_paths.read_declaration(str(tmpdir))
        self.assertEqual(
            [entry["pattern"] for entry in declaration["entries"]],
            [".github/workflows/", ".gitlab-ci.yml"],
        )
        self.assertTrue(all(entry["reason"] for entry in declaration["entries"]))

    def test_setup_reconciles_it_as_machine_parsed(self):
        import prep_setup

        self.assertIn(self.NAME, prep_setup.MACHINE_PARSED_MARKERS)
        markers = list(prep_setup.MACHINE_PARSED_MARKERS)
        self.assertEqual(markers.index(self.NAME), markers.index("pr-evaluator-merge-policy") + 1)

    def test_the_block_stays_out_of_the_s1_capture_machinery(self):
        self.assertNotIn(self.NAME, _extract_blocks(CONFIG_BLOCKS_EXAMPLE))
        peer = BlockByteIdentityTests("test_all_setup_markers_present_in_the_s1_capture")
        peer.setUp()
        self.assertNotIn(self.NAME, peer.expected_markers)


class DocCatalogueContractTests(unittest.TestCase):
    """The `doc-catalogue` block's own byte pin — anchored on `skills/_shared/doc-catalogue.md`, the
    live single source of truth, NOT on `docs/specs/examples/config-blocks.md`.

    Why not the S1 capture: that file is the frozen record of *v1's* blocks, quoted verbatim from v1's
    own source. `doc-catalogue` never existed in v1, so there is nothing there to capture truthfully,
    and `docs/specs/**` is frozen and validator-exempt by CLAUDE.md. Adding a fabricated fence to it
    would hollow out exactly the assertion `BlockByteIdentityTests` exists to make. So this class
    re-implements the same two pins (upsert round-trip; reference-does-not-restate-divergently)
    against the `_shared` contract instead.
    """

    NAME = "doc-catalogue"
    SHARED_CONTRACT = REPO_ROOT / "skills" / "_shared" / "doc-catalogue.md"
    DERIVATION_PROMPT = REFERENCES_DIR / "doc-catalogue-derivation-prompt.md"

    def setUp(self):
        self.shared_blocks = _extract_blocks(self.SHARED_CONTRACT)

    def test_shared_contract_carries_the_canonical_fence(self):
        self.assertTrue(self.SHARED_CONTRACT.is_file())
        self.assertIn(
            self.NAME,
            self.shared_blocks,
            "skills/_shared/doc-catalogue.md must carry the canonical fence — it is the byte anchor",
        )

    def test_block_authoring_fence_matches_the_shared_contract(self):
        ref_blocks = _extract_blocks(BLOCK_AUTHORING)
        self.assertIn(self.NAME, ref_blocks, "block-authoring.md missing the doc-catalogue fence")
        self.assertEqual(
            ref_blocks[self.NAME][0],
            self.shared_blocks[self.NAME][0],
            "block-authoring.md restates the doc-catalogue form divergently from the _shared contract "
            "(render, don't restate)",
        )

    def test_config_block_upsert_reproduces_the_canonical_form(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="gh-setup-catalogue-byteid-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        full_block, interior = self.shared_blocks[self.NAME]
        body_path = tmpdir / "body.md"
        body_path.write_text(interior + "\n", encoding="utf-8")
        target = tmpdir / "target.md"
        target.write_text("", encoding="utf-8")
        proc = _run_config_block(["upsert", str(target), self.NAME, str(body_path)])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(target.read_text(encoding="utf-8").rstrip("\n"), full_block)

    def test_the_block_stays_out_of_the_s1_capture_machinery(self):
        """Pins the decision itself against a well-meaning later "fix" that adds it to the frozen
        v1 capture and to `expected_markers`."""
        self.assertNotIn(self.NAME, _extract_blocks(CONFIG_BLOCKS_EXAMPLE))
        peer = BlockByteIdentityTests("test_all_setup_markers_present_in_the_s1_capture")
        peer.setUp()
        self.assertNotIn(self.NAME, peer.expected_markers)

    def test_flow_targets_docs_readme_and_dispatches_the_subagent(self):
        flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.assertIn("docs/README.md", flow)
        self.assertIn(self.NAME, flow)
        self.assertIn("doc-catalogue-derivation-prompt.md", flow)

    def test_the_derivation_prompt_exists_and_is_context_blind(self):
        self.assertTrue(self.DERIVATION_PROMPT.is_file())
        text = self.DERIVATION_PROMPT.read_text(encoding="utf-8")
        # A sub-agent returns a decision code; it never asks (skills/_shared/asking-the-user.md).
        self.assertIn("cannot call `AskUserQuestion`", text)
        for placeholder in ("<<contract_path>>", "<<readme_path>>", "<<base_path>>", "<<repo_root>>"):
            self.assertIn(placeholder, text, "derivation prompt missing %s" % placeholder)

    def test_absent_docs_index_is_skip_never_walk_or_interview(self):
        """The posture that keeps setup from inventing a grounding set: no index ⇒ skip."""
        authoring = BLOCK_AUTHORING.read_text(encoding="utf-8")
        flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.assertIn("no docs index — skipped", flow)
        self.assertIn("never walk the tree or interview", flow)
        self.assertIn("walk the tree looking for doc-shaped files", authoring)


class ReviewGuidanceContractTests(unittest.TestCase):
    """#99: the `issue-resolver-review-guidance` block is user-owned, so its one canonical form is
    block-authoring's own seed fence — like `doc-catalogue`, it never existed in v1 and stays out of
    the frozen S1 capture and `BlockByteIdentityTests.expected_markers`."""

    NAME = "issue-resolver-review-guidance"

    def test_block_authoring_carries_the_seed_fence_and_contents_entry(self):
        blocks = _extract_blocks(BLOCK_AUTHORING)
        self.assertIn(self.NAME, blocks)
        interior = blocks[self.NAME][1]
        for label in (
            "## Review guidance",
            "**What failure looks like here:**",
            "**Skip:**",
            "**Nits:**",
            "**Evidence:**",
            "**Recurring findings:**",
        ):
            self.assertIn(label, interior)
        text = BLOCK_AUTHORING.read_text(encoding="utf-8")
        self.assertIn("  - [issue-resolver-review-guidance](#issue-resolver-review-guidance)", text)
        self.assertIn("### issue-resolver-review-guidance", text)
        flat = " ".join(text.split())
        self.assertIn("**Write reviewer flags as code spans.**", flat)
        self.assertIn("`_(for the code owners to fill)_`", flat)
        self.assertIn("so this file owns its seed and the recurring-class proposals setup adds to it", flat)
        self.assertIn("`facts.inventory.review_guidance.source_file`, the well-formed copy the resolver reads", flat)

    def test_config_block_upsert_reproduces_the_seed_fence(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="gh-setup-guidance-byteid-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        full_block, interior = _extract_blocks(BLOCK_AUTHORING)[self.NAME]
        body_path = tmpdir / "body.md"
        body_path.write_text(interior + "\n", encoding="utf-8")
        target = tmpdir / "target.md"
        target.write_text("", encoding="utf-8")
        proc = _run_config_block(["upsert", str(target), self.NAME, str(body_path)])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(target.read_text(encoding="utf-8").rstrip("\n"), full_block)

    def test_the_block_stays_out_of_the_s1_capture_machinery(self):
        self.assertNotIn(self.NAME, _extract_blocks(CONFIG_BLOCKS_EXAMPLE))
        peer = BlockByteIdentityTests("test_all_setup_markers_present_in_the_s1_capture")
        peer.setUp()
        self.assertNotIn(self.NAME, peer.expected_markers)

    def test_router_and_flow_treat_it_as_user_owned_in_the_config_file(self):
        router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.assertIn("`stack_profile` and `review_guidance`", router)
        self.assertIn(
            "`claude-code-stack-profile`, `doc-catalogue` and `issue-resolver-review-guidance` are user-owned",
            router,
        )
        self.assertIn(
            "except blocks v1 never had (`doc-catalogue`, `pr-evaluator-sensitive-paths`, "
            "`issue-resolver-review-guidance`), whose fence is block-authoring.md's own",
            router,
        )
        flow = " ".join((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").split())
        self.assertIn(
            "`issue-resolver-review-guidance` stays in the file it lives in, else the target file — outside the repo, reported and never landed (block-authoring.md)", flow
        )


class ReviewGuidanceProposalTests(unittest.TestCase):
    """#99 phase 2: setup reads the review loop's history (`prep_setup.py --repo`, review-report's core)
    and proposes recurring finding classes as entries in the review-guidance block. The rules the model
    applies live in block-authoring; the router only names the fact and the opt-in read."""

    def setUp(self):
        self.router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.flow = " ".join((PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8").split())
        self.authoring = " ".join(BLOCK_AUTHORING.read_text(encoding="utf-8").split())

    def test_router_names_the_opt_in_read_and_the_fact(self):
        self.assertIn("prep_setup.py [--root <path>] [--repo <owner/repo>]", self.router)
        self.assertIn("Prep makes no `gh` call without `--repo`", self.router)
        self.assertIn("gh repo view --json nameWithOwner -q .nameWithOwner", self.router)
        self.assertIn("omit `--repo` when that read fails", self.router)
        self.assertIn("`review_history` (with `--repo`", self.router)
        self.assertIn("prep is always `ok`", self.router)
        self.assertIn("`REVIEW_HISTORY_UNAVAILABLE` notice", self.router)

    def test_prd_records_the_block_in_6_1_and_7(self):
        prd = " ".join((REPO_ROOT / "docs" / "prd.md").read_text(encoding="utf-8").split())
        self.assertIn("It seeds and re-ingests the user-owned review-guidance block (#99)", prd)
        self.assertIn("reading that history through review-report's core (§6.9)", prd)
        self.assertIn("(incl. `issue-resolver-review-guidance`, user-owned, #99)", prd)

    def test_flow_drafts_and_diffs_the_proposals(self):
        self.assertIn("its recurring-class proposals drafted from `facts.review_history`", self.flow)
        self.assertIn("the review-guidance diff as *their content + proposed entries*", self.flow)

    def _guidance_section(self):
        text = BLOCK_AUTHORING.read_text(encoding="utf-8")
        start = text.index("### issue-resolver-review-guidance")
        return text[start:text.index("### github-pipeline-config (file header)", start)]

    def test_block_authoring_carries_the_proposal_rules(self):
        for phrase in (
            "**Recurring-class proposals**",
            "Two **labelled bases, never pooled**",
            "rank them separately and never add their counts",
            "**Record basis** — `facts.review_history.class_counts`",
            "A merged class that takes in a `listed` key is itself listed: the owner's entry already covers it.",
            "the findings it tallies staged at `record_path` (`pr`, `id`, `class_key`, `bucket`, `outcome`, `site`, `item`)",
            "**Legacy basis** — the findings staged at `legacy_path` (`pr`, `id`, `bucket`, `outcome`, `site`, `item`;",
            "A key recurs at `count` ≥ 2",
            "some PR carried more than one finding of it (in one round or across rounds)",
            "its guidance names each other merged key as `` `<key>` ``, so the next run reads them all as `listed`",
            "count a class as recurring at two or more findings",
            "`unkeyed_findings` counts the findings on neither basis",
            "Never group them; the summary reports the count when above zero.",
            "at most **five** new entries per run",
            "*their content + proposed entries*",
            "the record ids from `record_path` or the legacy items from `legacy_path`",
            "**history unavailable**",
        ):
            self.assertIn(phrase, self.authoring)
        text = BLOCK_AUTHORING.read_text(encoding="utf-8")
        self.assertIn("- <guidance> — recurring: `<class-key>` · <n> findings · <m> PRs · <record|legacy>", text)
        self.assertIn("*Swift:*", text)
        self.assertIn("*Rails:*", text)

    def test_the_outcome_rule_counts_only_repo_wide_outcomes(self):
        """The revised rule (user decision 2026-10-25): an outcome scoped to one plan never yields a
        repo-wide entry, and the winning side needs two findings."""
        for phrase in (
            "**The kind follows the repo-wide outcomes only**",
            "**watch for** (`fixed`) and **not a finding here** (`wrong` and `did-not-matter`)",
            "`intentional` counts toward neither, and neither does any excluded outcome",
            "would turn a plan's deferral into standing guidance",
            "The kind is the side with more findings, and a class is proposed only when that side carries **at least two**",
            '"watch for <failure>"',
            '"<pattern> is not a finding here: <reason>"',
            "When the items record none, state the pattern alone",
            "So a class that recurs mostly as `intentional` is usually left with too few counted findings to be proposed (`no counted outcome` or `one finding`), and the summary still names it with that reason",
            "Its `intentional` findings never weigh against a side that does carry two.",
        ):
            self.assertIn(phrase, self.authoring)
        self.assertNotIn("weigh `fixed` against the other three", self.authoring)
        self.assertNotIn("A class with no counted outcome, or a tie, is not proposed.", self.authoring)
        self.assertNotIn("deliberate", self._guidance_section().lower())

    def test_ranking_and_the_closed_not_proposed_reasons(self):
        for phrase in (
            "within a basis, rank by the winning side's findings, then by total findings, then by class key in alphabetical order",
            "record-basis classes first, then legacy-basis classes",
            "Every recurring class not proposed gets one reason, the first that holds, checked in this order: "
            "`listed`; `no counted outcome` (every finding is `intentional` or excluded); `one finding` "
            "(neither side carries two or more); `tie`; `over cap`.",
            "a legacy class an entry in the base's `**Recurring findings:**` list covers by meaning, whatever key this run would mint",
            "its summary line reuses that entry's key",
        ):
            self.assertIn(phrase, self.authoring)
        self.assertNotIn("`listed`, `no counted outcome`, `tie`, or `over cap`", self.authoring)

    def test_summary_vocabulary_names_history_not_read_and_the_bounds(self):
        for phrase in (
            "**history not read** (no `review_history`: the router could not resolve the repo, so it passed no `--repo`)",
            "Also report `unkeyed_findings` when above zero",
            "when `prs_scanned` equals `limit`, that the proposals rest on the `<limit>` most recent PRs",
        ):
            self.assertIn(phrase, self.authoring)

    def test_a_copy_outside_the_repository_is_reported_never_landed(self):
        for phrase in (
            "**A copy outside the repository is reported, never landed.**",
            "When `source_file` is absolute",
            "Land nothing for this block then — no seed, no entries — and create no copy in a candidate file either",
            "a new copy would shadow the owner's",
            "The summary prints the proposed entries with that path, for the owner to add by hand.",
            "#119 decides this rule for every block",
        ):
            self.assertIn(phrase, self.authoring)

    def test_router_home_rule_never_lands_outside_the_repo(self):
        self.assertIn("else `target_file.suggested` — never landed when that file is outside the repo "
                      "(block-authoring.md).", self.router)

    def test_router_names_the_staged_history_fields(self):
        self.assertIn("`class_counts` with its findings staged at `record_path`, the legacy basis at "
                      "`legacy_path`, `limit`, `unkeyed_findings`", self.router)


class LegacyHealthChecksMigrationTests(unittest.TestCase):
    """DoD box 4: the legacy `pr-evaluator-health-checks` split reproduced through config_block.py —
    read the legacy block, upsert the two replacements, remove the legacy block."""

    def _slurp(self, target, name):
        proc = _run_config_block(["read", str(target), name])
        return proc

    def test_split_migration_mechanics(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="gh-setup-legacy-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        target = tmpdir / "COMMANDS.md"
        target.write_text(
            "<!-- pr-evaluator-health-checks -->\n"
            "- `make lint` — lint\n"
            "- `make typecheck` — types\n"
            "- `make test` — the full suite\n"
            "<!-- /pr-evaluator-health-checks -->\n",
            encoding="utf-8",
        )

        # 1. read the legacy block (the split's input).
        legacy = self._slurp(target, "pr-evaluator-health-checks")
        self.assertEqual(legacy.returncode, 0)
        self.assertIn("make lint", legacy.stdout)
        self.assertIn("make test", legacy.stdout)

        # 2. upsert the static-checks replacement (the two static commands).
        static_body = tmpdir / "static.md"
        static_body.write_text(
            "- `make lint` — lint\n- `make typecheck` — types\n", encoding="utf-8"
        )
        self.assertEqual(
            _run_config_block(
                ["upsert", str(target), "pr-evaluator-static-checks", str(static_body)]
            ).returncode,
            0,
        )

        # 3. upsert the test-target replacement (test invocation → wrapper + full-suite-command).
        tt_body = tmpdir / "tt.md"
        tt_body.write_text(
            "- wrapper: `make test`\n"
            "- full-suite-command: `make test`\n"
            "- targets:\n"
            "  - `unit` (unit)\n"
            "    - naming: source `<x>` ↔ its `<x>` test\n"
            "    - helpers-fallback: `make test`\n"
            "    - broad-change-fallback: `make test`\n",
            encoding="utf-8",
        )
        self.assertEqual(
            _run_config_block(
                ["upsert", str(target), "pr-evaluator-test-target", str(tt_body)]
            ).returncode,
            0,
        )

        # 4. remove the legacy block — only after both replacements are written.
        rm = _run_config_block(["remove", str(target), "pr-evaluator-health-checks"])
        self.assertEqual(rm.returncode, 0)

        final = target.read_text(encoding="utf-8")
        self.assertNotIn("<!-- pr-evaluator-health-checks -->", final)
        self.assertIn("<!-- pr-evaluator-static-checks -->", final)
        self.assertIn("<!-- pr-evaluator-test-target -->", final)
        self.assertIn("- wrapper: `make test`", final)
        self.assertIn("- full-suite-command: `make test`", final)
        # The static list carries the two static commands, not the test invocation.
        static_read = self._slurp(target, "pr-evaluator-static-checks").stdout
        self.assertIn("make lint", static_read)
        self.assertNotIn("make test", static_read)


class LandingGateLanguageTests(unittest.TestCase):
    """DoD boxes 2/3 offline half: the prd.md §8.2 landing gate is pinned as prose — offered ONCE, and
    on decline zero git actions + the workspace path + ready-to-run commands."""

    def setUp(self):
        self.flow = (PLAYBOOKS_DIR / FLOW).read_text(encoding="utf-8")
        self.router = ROUTER.read_text(encoding="utf-8")

    def test_flow_offers_landing_as_one_explicit_gate(self):
        self.assertIn("prd.md §8.2", self.flow)
        self.assertRegex(self.flow, r"one explicit\s*.{0,6}\s*gate")
        self.assertIn("summarizes the block diffs", self.flow)

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


class MergePolicyDocsAutoTests(unittest.TestCase):
    """DoD: the merge-policy proposal includes a `docs: auto`-style option — while the block keys stay
    the canonical `standard`/`story` (a `docs:` key is never written; the evaluator only classifies
    standard/story/epic-integration)."""

    def setUp(self):
        self.authoring = BLOCK_AUTHORING.read_text(encoding="utf-8")

    def test_docs_auto_style_option_is_named(self):
        self.assertIn("`docs: auto`-style option", self.authoring)

    def test_block_keys_stay_canonical_standard_story(self):
        blocks = _extract_blocks(BLOCK_AUTHORING)
        _, interior = blocks["pr-evaluator-merge-policy"]
        self.assertIn("- standard: ask", interior)
        self.assertIn("- story: ask", interior)
        # the canonical block never carries a `docs:` key.
        self.assertNotIn("- docs:", interior)


class StackAgnosticTests(unittest.TestCase):
    """The loaded prompt (router + playbook) makes no bare stack assumption; the reference carries the
    labeled ≥2-stack worked examples (the allowed form — CLAUDE.md editing conventions)."""

    _BARE_STACK = re.compile(r"\b(swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest)\b", re.IGNORECASE)

    def test_router_and_playbook_have_no_bare_stack_token(self):
        for path in (ROUTER, PLAYBOOKS_DIR / FLOW):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = self._BARE_STACK.search(line)
                self.assertIsNone(
                    hit,
                    "bare stack token in the loaded prompt at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_block_authoring_carries_at_least_two_labeled_stacks(self):
        text = BLOCK_AUTHORING.read_text(encoding="utf-8").lower()
        # ≥2 concrete stacks shown side by side (the allowed labeled-multi-stack example form).
        self.assertIn("swift", text)
        self.assertIn("rails", text)


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
    """DoD box 2 offline half: the landing PR write (create-pr with the block-diff-summary body), run
    --dry-run: a conformant envelope, status ok, would_run present, exit 0, no live gh call, no url."""

    def test_create_pr_dry_run(self):
        proc, env = _run_persist(
            ["create-pr", "octo/widgets", "@BODY@", "--title", "Configure github-pipeline (COMMANDS.md)",
             "--base", "main", "--head", "setup/config-widgets", "--dry-run"],
            body_text="## Configuration blocks\n\n- `issue-resolver-fast-checks` — written\n",
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
