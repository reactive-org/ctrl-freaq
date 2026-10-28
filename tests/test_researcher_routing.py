"""Offline tests for the researcher skill (docs/implementation.md S16).

Surfaces the S16 DoD / Testing section names:

1. **Routing-table fixtures** (`vector.mode` → `playbooks/<file>`). The router's visible routing table
   (`skills/researcher/SKILL.md` §2) maps the prep-derived `vector.mode` to the one variant playbook a
   session reads; each variant runs the shared `research-spine.md`. These parse that table, assert it
   covers exactly the three routable variants (broad / targeted / revise), that each points at a real
   `playbooks/<file>` and reads the spine, and that the mode→playbook mapping is byte-consistent with
   `prep_researcher._suggested_playbook` (architecture.md §5 "prep proposes; the router confirms").

2. **The interleaving pattern-grep** (DoD: variants are mode-conditional-free by construction). Greps
   the three variants + the shared spine for cross-mode conditionals and fails on a hit.

3. **Contract-token grep gates** over skills/researcher/: zero retired-executor tokens, zero v1 skill-namespace
   strings (`github-pipeline:github-`), zero `GATHER_`/`PERSIST_` op names, zero `§P` IDs, zero raw
   persist/gather WRITES in fences, zero `w/` shorthand. (The frozen dossier artifact's own
   dossier provenance strings (which name the v1 authoring skills) are byte-compat contract tokens
   — S7 precedent (a) — and legitimately do NOT match `github-pipeline:github-`.)

4. **`--dry-run` persist envelopes** for every GitHub write the playbooks specify (all through
   `gh_persist.py`; the researcher has no scriptless raw-gh executor): `comment` (the dossier post, with
   the revise-mode `--delete-marker-id`) and `edit-labels` (the `researched` label).

5. **Rendering byte-compat** vs the S1 captures — the `<!-- issue-research:v1 -->` dossier schema diffs
   clean against `docs/specs/examples/issue-research.md`; the handoff shapes forward to the v2-renamed
   planner.

6. **The decline gate** — written as prose in `broad.md` and pinned by grep (the researcher-defining
   falsifiable behavior: a no-currency-risk issue posts nothing and declines).

7. **Structural bars** — router ≤ 150, exactly three routable variants + one spine, router + largest
   playbook ≤ 144 (floor of v1's 289), frontmatter pins carried from v1 (opus / medium).

8. **The S15 handoff-binding language** — the forced point-of-use Read + emit-verbatim + the four named
   drift prohibitions, present in the router, the reference file, and the spine's `## S-handoff`.

9. **The `<type>` closed-set mapping hardening** (post-acceptance micro-round, prompted by the S16
   Scenario-2/3 live-parity `<type>`-token divergence — an inverse flip across runs proved it was
   per-run opus rendering non-determinism, not a v1/v2 behavioural difference). The shared SSoT
   (`_shared/handoff-format.md`'s Issue `type` row) now carries a terse mapping instruction — the slot
   takes ONLY the closed-set values, a raw repo label (e.g. `enhancement`) must be mapped onto the set
   (e.g. `feature`), never leaked verbatim — and the researcher's own `handoff-renderings.md` echoes it
   (the file the forced-read binding actually loads at emission; the SSoT row alone isn't in context
   then). These tests pin both the SSoT instruction and its researcher-side echo, `enhancement` named as
   the worked example in both.

No network: gh_persist.py's `gh` calls resolve to the offline shim via tests/run.py's PATH wiring; the
dry-run path performs no gh call at all.
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
SKILL_DIR = REPO_ROOT / "skills" / "researcher"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
REFERENCES_DIR = SKILL_DIR / "references"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"
EXAMPLES = REPO_ROOT / "docs" / "specs" / "examples"
SHARED = REPO_ROOT / "skills" / "_shared"

ROUTABLE_PLAYBOOKS = {"broad.md", "targeted.md", "revise.md"}
SPINE = "research-spine.md"

# floor(the v1 researcher SKILL.md's 289 lines, docs/specs/baseline.md §1) / 2 = 144.
V1_HALF_BAR = 289 // 2

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_researcher  # noqa: E402
from tests.support import envelope_asserts, shimenv  # noqa: E402
from tests.support.retired_tokens import (  # noqa: E402
    FORBIDDEN_CONTRACT_TOKENS,
    V1_INVOCATION_PREFIX,
)
from tests.test_subagent_prompts import THREAD_RULE_ANCHORS  # noqa: E402


_ROUTE_ROW_RE = re.compile(
    r"^\|\s*`?(?P<vector>[^|]+?)`?\s*\|\s*`(?P<playbook>playbooks/[a-z0-9-]+\.md)`\s*\|"
)


def _parse_router_routing_table(router_text):
    rows = []
    for line in router_text.splitlines():
        match = _ROUTE_ROW_RE.match(line)
        if match:
            rows.append((match.group("vector").strip(), match.group("playbook")))
    return rows


def _fence_stripped_lines(path):
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            yield i, line, in_fence
            continue
        yield i, line, in_fence


def _iter_md(dir_path):
    yield from sorted(dir_path.rglob("*.md"))


def _first_fenced_block(text):
    out, in_fence = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_fence:
                out.append(line)
                break
            in_fence = True
        if in_fence:
            out.append(line)
    return "\n".join(out)


class RouterRoutingTableTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")
        self.rows = _parse_router_routing_table(self.router_text)
        self.playbooks = [pb for _, pb in self.rows]

    def test_router_exists_and_has_no_model_effort_pins(self):
        # Model/effort are not pinned — skills inherit the invoking session's model and effort.
        self.assertTrue(ROUTER.is_file(), "router SKILL.md must exist")
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: researcher", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)

    def test_routing_table_covers_exactly_the_three_routable_variants(self):
        self.assertEqual(
            {Path(pb).name for pb in self.playbooks},
            ROUTABLE_PLAYBOOKS,
            "router routing table must map exactly the three routable variants, got %r"
            % (sorted(Path(pb).name for pb in self.playbooks),),
        )

    def test_exactly_three_routable_variants_plus_one_spine_on_disk(self):
        on_disk = {p.name for p in PLAYBOOKS_DIR.glob("*.md")}
        self.assertEqual(
            on_disk,
            ROUTABLE_PLAYBOOKS | {SPINE},
            "playbooks/ must be exactly the three routable variants + the one shared spine, got %r"
            % (sorted(on_disk),),
        )

    def test_every_routed_playbook_file_exists(self):
        for vector, playbook in self.rows:
            target = SKILL_DIR / playbook
            self.assertTrue(
                target.is_file(),
                "routing table maps %r -> %r but %s does not exist" % (vector, playbook, target),
            )

    def test_table_matches_prep_suggested_playbook(self):
        cases = [("broad", "broad.md"), ("targeted", "targeted.md"), ("revise", "revise.md")]
        table_basenames = {Path(pb).name for pb in self.playbooks}
        for mode, expected in cases:
            self.assertEqual(prep_researcher._suggested_playbook(mode), expected)
            self.assertIn(expected, table_basenames, "the router table must route to %s" % expected)

    def test_all_three_variants_read_the_shared_spine(self):
        self.assertTrue((PLAYBOOKS_DIR / SPINE).is_file())
        for name in ROUTABLE_PLAYBOOKS:
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn(SPINE, text, "%s must read the shared spine %s" % (name, SPINE))

    def test_router_states_no_route_override(self):
        # vector.mode is fully mechanical (dossier presence + --question) — no classification override.
        self.assertRegex(self.router_text, r"[Nn]o route override")


class RouterStructuralBarTests(unittest.TestCase):
    def test_router_at_most_150_lines(self):
        n = len(ROUTER.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(n, 150, "router SKILL.md is %d lines (bar: <= 150)" % n)

    def test_router_plus_largest_playbook_at_most_half_v1(self):
        router_lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        playbook_lines = {
            p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in PLAYBOOKS_DIR.glob("*.md")
        }
        largest = max(playbook_lines.values())
        self.assertLessEqual(
            router_lines + largest,
            V1_HALF_BAR,
            "router (%d) + largest playbook (%d) = %d exceeds %d (half of v1's 289): %r"
            % (router_lines, largest, router_lines + largest, V1_HALF_BAR, playbook_lines),
        )


class PlaybookInterleavingGrepTests(unittest.TestCase):
    """A variant is a linear narrative for exactly one mode — zero cross-mode conditionals. Fails on
    either an `if … <mode> … else …` construct or a `when the mode is a <mode>` prose branch, over the
    three variants and the shared spine. The spine's `facts.revise present ⇒ …` is a FACT gate (value
    from the facts block), not a route-name branch, so it does not match these patterns."""

    _ROUTES = r"(broad|targeted|revise)"
    _IF_ELSE = re.compile(r"\bif\b[^.\n]{0,50}\b" + _ROUTES + r"\b[^.\n]{0,50}\belse\b", re.IGNORECASE)
    _WHEN_MODE = re.compile(
        r"\bwhen (the (mode|issue|type) is|it.?s)\s+(a |an )?" + _ROUTES + r"\b", re.IGNORECASE
    )

    def test_playbooks_have_no_cross_mode_conditionals(self):
        for playbook in PLAYBOOKS_DIR.glob("*.md"):
            text = playbook.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                self.assertIsNone(
                    self._IF_ELSE.search(line),
                    "%s:%d looks like a cross-mode conditional: %r" % (playbook.name, i, line),
                )
                self.assertIsNone(
                    self._WHEN_MODE.search(line),
                    "%s:%d looks like a when-<mode> prose branch: %r" % (playbook.name, i, line),
                )


class ContractTokenGateTests(unittest.TestCase):
    """Grep gates over skills/researcher/ — zero retired-executor tokens, zero v1 skill-invocation namespace strings,
    zero GATHER_/PERSIST_ op names, zero §P IDs, zero raw persist/gather WRITES in fences, zero w/
    shorthand. The frozen dossier artifact's own provenance
    strings are byte-compat contract tokens (S7 precedent (a)) — they carry no `ctrl-freaq:` prefix,
    so they legitimately do not match the forbidden `github-pipeline:github-` pattern."""

    def test_no_github_ops_or_old_namespace_or_op_names_or_pids(self):
        forbidden = FORBIDDEN_CONTRACT_TOKENS
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = forbidden.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden contract token under skills/researcher/ at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_forward_next_command_is_v2_planner(self):
        renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        self.assertIn("/ctrl-freaq:planner", renderings)
        self.assertNotIn(V1_INVOCATION_PREFIX, renderings)

    def test_no_raw_persist_or_gather_writes_in_code_fences(self):
        raw_write = re.compile(
            r"\bgh\s+(issue|pr)\s+(create|edit|comment|review|close|reopen)\b"
            r"|\bgh\s+api\b[^\n]*\bDELETE\b"
        )
        for path in _iter_md(SKILL_DIR):
            for i, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                hit = raw_write.search(line)
                self.assertIsNone(
                    hit,
                    "raw gh persist/gather WRITE in a code fence at %s:%d — %r"
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


class PlaybookPersistDryRunTests(unittest.TestCase):
    """Every GitHub write the researcher spine specifies, run with --dry-run: a conformant envelope,
    status ok, `would_run` present, exit 0, no live gh call."""

    def _assert_dry_run_ok(self, proc, envelope, expected_op):
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertIsNotNone(envelope, "expected exactly one envelope on stdout")
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope.get("status"), "ok")
        self.assertEqual(envelope.get("op"), expected_op)
        self.assertTrue(envelope.get("dry_run"))
        self.assertIn("would_run", envelope)

    def test_dossier_comment_post_dry_run(self):
        # S-persist fresh post: gh_persist.py comment <repo> issue <N> <research.md>
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "142", "@BODY@", "--dry-run"],
            body_text="<!-- issue-research:v1 -->\n**Research dossier** — #142 …\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_dossier_comment_revise_delete_marker_dry_run(self):
        # S-persist revise: the delete-and-repost passes --delete-marker-id (post before delete).
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "142", "@BODY@", "--delete-marker-id", "8001",
             "--dry-run"],
            body_text="<!-- issue-research:v1 -->\n**Research dossier** — #142 (refreshed) …\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_researched_label_edit_labels_dry_run(self):
        # S-persist: apply the `researched` label idempotently.
        proc, env = _run_persist(
            ["edit-labels", "octo/widgets", "142", "--add", "researched", "--dry-run"]
        )
        self._assert_dry_run_ok(proc, env, "edit-labels")
        self.assertEqual(env.get("added"), ["researched"])

    def test_dry_run_makes_no_live_gh_call(self):
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "142", "@BODY@", "--dry-run"],
            body_text="<!-- issue-research:v1 -->\nx\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertTrue(env.get("dry_run"))
        self.assertNotIn("url", env, "a dry-run must not carry a live-write url")


class RenderingByteCompatTests(unittest.TestCase):
    """DoD box 1 (offline half): the dossier schema the researcher posts is byte-clean against the S1
    capture (docs/specs/examples/issue-research.md)."""

    def test_dossier_schema_matches_s1_capture(self):
        ours = _first_fenced_block((REFERENCES_DIR / "dossier-schema.md").read_text(encoding="utf-8"))
        s1 = _first_fenced_block((EXAMPLES / "issue-research.md").read_text(encoding="utf-8"))
        self.assertTrue(ours.strip(), "no fenced schema block in dossier-schema.md")
        self.assertEqual(ours, s1, "the issue-research:v1 dossier schema drifted from the S1 capture")

    def test_dossier_marker_is_the_first_line_of_the_schema(self):
        block = _first_fenced_block((REFERENCES_DIR / "dossier-schema.md").read_text(encoding="utf-8"))
        body = block.splitlines()
        # line 0 is the opening fence; line 1 is the marker.
        self.assertEqual(body[1], "<!-- issue-research:v1 -->")

    def test_schema_footer_keeps_frozen_provenance_strings(self):
        # S7 precedent (a): the emitted artifact's provenance strings are byte-compat contract tokens,
        # NOT renamed to the v2 skill name.
        block = _first_fenced_block((REFERENCES_DIR / "dossier-schema.md").read_text(encoding="utf-8"))
        self.assertIn("Authored by `github-issue-researcher`", block)
        self.assertIn("`github-issue-planner` consumes it", block)


class HandoffRenderingTests(unittest.TestCase):
    """DoD box 1/3 (offline half): the researcher's two handoff shapes are present and forward to the
    v2-renamed planner with the right `research:` marker."""

    def setUp(self):
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_reference_exists(self):
        self.assertTrue((REFERENCES_DIR / "handoff-renderings.md").is_file())

    def test_posted_shape_forwards_to_planner_with_research_check(self):
        self.assertIn("research: ✓", self.renderings)
        self.assertIn("/ctrl-freaq:planner", self.renderings)

    def test_decline_shape_has_research_cross(self):
        self.assertIn("research: ✗", self.renderings)
        # the Issue line carries an ✗ marker and still forwards to the planner.
        self.assertRegex(self.renderings, r"\*\*Issue:\*\* #\d+ .*· research: ✗")

    def test_handoff_shapes_are_issue_lines_not_filed(self):
        self.assertIn("**Issue:**", self.renderings)
        self.assertNotIn("**Filed:**\n", self.renderings)  # the drift form must not be the actual shape


class HandoffBindingLanguageTests(unittest.TestCase):
    """The S15-hardened handoff-emission binding (docs/specs/parity/drafter.md "Handoff-rendering drift"):
    the point-of-use Read is immediate, the shape is emitted/copied verbatim, and the four observed drift
    forms are named as explicit prohibitions — in the router, the reference file read at emission, and
    the spine's own `## S-handoff` section."""

    @staticmethod
    def _flat(text):
        return re.sub(r"\s+", " ", text)

    def setUp(self):
        self.router = ROUTER.read_text(encoding="utf-8")
        self.router_flat = self._flat(self.router)
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        self.renderings_flat = self._flat(self.renderings)
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.spine_flat = self._flat(self.spine)

    def test_router_forces_read_immediately_before_composing(self):
        self.assertRegex(
            self.router_flat,
            r"Read that reference immediately before composing the handoff",
            "the router must force the point-of-use Read at emission time, not earlier",
        )

    def test_router_binds_emit_verbatim(self):
        self.assertIn("emit the matching shape verbatim", self.router_flat)
        self.assertIn("contract, not prose to summarize", self.router_flat)

    def test_router_names_all_four_drift_forms_as_prohibitions(self):
        for token in ("**Filed:**", "state", "Snapshot", "Next:"):
            self.assertIn(token, self.router)
        self.assertIn(
            "never paraphrase, restructure, rename a field, drop a segment, or add a block",
            self.router_flat,
        )

    def test_reference_intro_binds_copy_the_shape(self):
        self.assertRegex(self.renderings_flat, r"copy its shape, and substitute only the issue number")
        self.assertIn("contract, not a style to imitate", self.renderings_flat)

    def test_reference_names_drift_forms(self):
        for token in ("**Filed:**", "Snapshot", "Next:"):
            self.assertIn(token, self.renderings)
        self.assertRegex(self.renderings_flat, r"drop the\s*`?· <state> ·`?\s*segment")

    def test_spine_handoff_section_binds_emit_verbatim(self):
        self.assertIn("emit the matching shape verbatim", self.spine_flat)
        self.assertRegex(self.spine_flat, r"never rename a field or restructure it")

    def test_binding_language_is_prose_not_inside_a_code_fence(self):
        for label, path in (("router", ROUTER), ("renderings", REFERENCES_DIR / "handoff-renderings.md")):
            for i, line, in_fence in _fence_stripped_lines(path):
                if in_fence:
                    self.assertNotIn(
                        "emit the matching shape verbatim", line,
                        "%s:%d — the binding rule must be prose, not inside a code fence" % (label, i),
                    )


class DeclineGateTests(unittest.TestCase):
    """DoD box 1 (offline half): the decline gate — the researcher-defining falsifiable behavior — is
    written as prose in broad.md and pinned by grep. A no-currency-risk issue posts nothing and declines
    straight to the planner with `research: ✗`."""

    def setUp(self):
        self.broad = (PLAYBOOKS_DIR / "broad.md").read_text(encoding="utf-8")

    def test_decline_gate_is_named_and_a_real_fork(self):
        self.assertIn("decline gate", self.broad)
        self.assertRegex(self.broad, r"[Aa] real fork, not a footnote")
        self.assertRegex(self.broad, r"nothing to research")
        self.assertRegex(self.broad, r"research: ✗")

    def test_decline_gate_names_all_four_currency_risk_conditions(self):
        for cue in (
            "training cutoff",
            "fast-moving",
            "assert from memory",
            "explicitly asks",
        ):
            self.assertIn(cue, self.broad, "decline-gate condition cue %r missing" % cue)

    def test_design_choice_trap_is_guarded(self):
        self.assertIn("design-choice trap", self.broad)
        self.assertRegex(self.broad, r"planner's call")

    def test_decline_gate_prose_not_inside_a_code_fence(self):
        for i, line, in_fence in _fence_stripped_lines(PLAYBOOKS_DIR / "broad.md"):
            if in_fence:
                self.assertNotIn(
                    "decline gate", line,
                    "the decline gate must be prose, not inside a code fence (%d)" % i,
                )


class CarriedReferenceTests(unittest.TestCase):
    """DoD: the web-research loop + validator are carried as references (S11-bound). What changed is
    two path/reference adaptations only (the sibling cross-reference path, and the invocation-point
    description updated from v1's numbered-step anchor to the v2 spine's section anchor) — the validator
    prompt keeps its six dimensions and its isolation contract."""

    def setUp(self):
        self.validator = (REFERENCES_DIR / "research-validator-prompt.md").read_text(encoding="utf-8")

    def test_validator_prompt_present_and_carries_six_dimensions(self):
        for dim in range(1, 7):
            self.assertRegex(self.validator, r"(?m)^%d\.\s+\*\*" % dim, "validator dimension %d missing" % dim)

    def test_validator_takes_repo_root_placeholder_the_playbook_fills(self):
        self.assertIn("<<repo_root>>", self.validator)
        # the spine fills repo_root with facts.root.path.
        spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.assertIn("facts.root.path", spine)

    def test_validator_does_not_cite_retired_signal_doc(self):
        self.assertNotIn("subagent-decision-signal", self.validator)

    def test_validator_cross_reference_points_at_v2_planner(self):
        # Adaptation 1 of 2: the sibling reference is the v2 planner path, not v1's.
        self.assertIn("../../planner/references/plan-reviewer-prompt.md", self.validator)
        self.assertNotIn(V1_INVOCATION_PREFIX.lstrip("/"), self.validator)

    def test_validator_invocation_point_names_the_v2_spine_anchor(self):
        # Adaptation 2 of 2: v1's numbered-step anchor ("step 8 of the workflow") does not exist in
        # v2's router+spine anatomy — updated to name the spine's validation section instead.
        self.assertIn("the spine's validation step", self.validator)
        self.assertNotIn("step 8 of the workflow", self.validator)

    def test_gather_tactics_reference_present(self):
        tactics = (REFERENCES_DIR / "gather-tactics.md").read_text(encoding="utf-8")
        for cue in ("Primary / official", "deep-research", "JS-rendered docs", "fetch date"):
            self.assertIn(cue, tactics, "gather-tactics cue %r missing" % cue)


class TypeTokenMappingHardeningTests(unittest.TestCase):
    """Post-acceptance micro-round (S16 Scenario-2/3 `<type>`-token divergence — an inverse flip across
    runs proved per-run opus rendering non-determinism, not a v1/v2 behavioural difference). The shared
    SSoT (`_shared/handoff-format.md`'s Issue `type` row) must instruct the emitter to map a raw repo
    label onto the closed set rather than leaking it verbatim; the researcher's own `handoff-renderings.md`
    — what the forced-read binding actually loads at emission, when the SSoT row alone isn't in context —
    must echo the same instruction. `enhancement` is pinned as the worked example in both, matching the
    live-observed leaking label."""

    def setUp(self):
        self.ssot = (SHARED / "handoff-format.md").read_text(encoding="utf-8")
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_ssot_type_row_carries_the_mapping_instruction(self):
        # The closed-set row itself (still exactly six values — the mapping instruction must not grow
        # the set, only instruct how to fill it).
        self.assertIn(
            "| Issue `type` | `bug`, `feature`, `incomplete`, `story`, `epic`, `question`", self.ssot
        )
        self.assertRegex(self.ssot, r"map the repo's own label onto this set")
        self.assertRegex(self.ssot, r"never leak a raw label")

    def test_ssot_names_enhancement_as_the_worked_example(self):
        self.assertRegex(self.ssot, r"`enhancement`\s*→\s*`feature`")

    def test_researcher_renderings_echoes_the_type_mapping_instruction(self):
        self.assertRegex(self.renderings, r"mapped onto that closed set")
        self.assertRegex(self.renderings, r"never leaked raw")
        self.assertRegex(self.renderings, r"`enhancement`.*renders as `feature`")

    def test_researcher_renderings_echo_cites_the_ssot_type_row(self):
        self.assertIn("_shared/handoff-format.md", self.renderings)
        self.assertRegex(self.renderings, r"Issue `type` row")

    def test_echo_line_is_prose_not_inside_a_code_fence(self):
        for i, line, in_fence in _fence_stripped_lines(REFERENCES_DIR / "handoff-renderings.md"):
            if in_fence:
                self.assertNotIn(
                    "mapped onto that closed set", line,
                    "the type-mapping echo must be prose, not inside a code fence (%d)" % i,
                )


class ThreadWalkAuthorRuleTests(unittest.TestCase):
    """#186: both routes that read the issue thread apply #179's author rule to marker-shaped
    comments."""

    def test_thread_reading_routes_state_the_author_rule(self):
        for name, start, end in (
            ("broad.md", "Prep fetched the issue and thread.", "**Discover the stack:**"),
            ("revise.md", "Prep fetched the issue, thread, and the existing dossier.", "Its `comment_id`"),
        ):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            walk = text[text.index(start):]
            walk = walk[:walk.index(end)]
            for anchor in THREAD_RULE_ANCHORS:
                with self.subTest(playbook=name, anchor=anchor):
                    self.assertIn(anchor, walk)


if __name__ == "__main__":
    unittest.main()
