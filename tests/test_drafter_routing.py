"""Offline tests for the drafter skill (docs/implementation.md S15).

Surfaces the S15 DoD / Testing section names:

1. **Routing-table fixtures** (`vector` → `playbooks/<file>`). The router's visible routing table
   (`skills/drafter/SKILL.md` §2) maps a prep-derived `vector` to the one playbook a session reads.
   These parse that table, assert it covers exactly the three routable playbooks
   (new / revise / question), that each points at a real `playbooks/<file>`, and that the
   (mode, type)→playbook mapping is byte-consistent with `prep_drafter._suggested_playbook`
   (architecture.md §5 "prep proposes; the router confirms"). The new-mode classification override rule
   (`new.md` classifies Epic/question and the router re-routes) is asserted as visible router prose.

2. **The interleaving pattern-grep, committed as a validator** (DoD box: playbooks are
   type-conditional-free by construction). Greps the three routable playbooks + the shared spine for
   cross-route conditionals and fails on a hit. Patterns broadened per the S10 carried advisory to the
   drafter's route set.

3. **Contract-token grep gates** over skills/drafter/: zero retired-executor tokens, zero v1 skill-namespace
   strings, zero `GATHER_`/`PERSIST_` op names, zero `§P` IDs, zero raw persist/gather WRITES in fences,
   zero `w/` shorthand.

4. **`--dry-run` persist envelopes** for every GitHub write the playbooks specify (all through
   `gh_persist.py`; the drafter has no scriptless raw-gh executor): `create` with labels + native deps,
   the DEPS_UNSUPPORTED prose fallback (exercised in-process — the shim can't deliver the classifying
   stderr), the epic `## Stories` `edit-body` patch, `edit-labels`, `comment`, and the revise native-dep
   `link` removal.

5. **Rendering byte-compat** vs the S1 captures — the `question`-issue body schema and the
   `open-question-links:v1` section (both owned by `_shared`, cited by the drafter) diff clean against the
   S1 captures; the handoff shapes are present (v2-renamed next-commands).

6. **The falsifiable OQ-absorption rule** — written as fence-scoped prose in the spine and pinned by grep.

7. **Structural bars** — router ≤ 150, exactly three routable playbooks + one spine, router + largest
   playbook ≤ 288 (half of v1's 576), frontmatter pins carried from v1.

No network: gh_persist.py's `gh` calls resolve to the offline shim via tests/run.py's PATH wiring; the
dry-run path performs no gh call at all (asserted); the DEPS_UNSUPPORTED case scripts process.run in
process.
"""

import contextlib
import io
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
SKILL_DIR = REPO_ROOT / "skills" / "drafter"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
REFERENCES_DIR = SKILL_DIR / "references"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"
SHARED = REPO_ROOT / "skills" / "_shared"
EXAMPLES = REPO_ROOT / "docs" / "specs" / "examples"

# The three routable playbooks (the spine is a shared file, not a routable route). #16 retired
# epic-split.md: the drafter drafts and revises one issue's body and decomposes nothing.
ROUTABLE_PLAYBOOKS = {"new.md", "revise.md", "question.md"}
SPINE = "draft-spine.md"

# Half of the v1 drafter SKILL.md (576 lines, docs/specs/baseline.md §1) = 288.
V1_HALF_BAR = 576 // 2

sys.path.insert(0, str(SCRIPTS_DIR))

import gh_persist  # noqa: E402
import prep_drafter  # noqa: E402
from pipelib import process  # noqa: E402
from tests.support import envelope_asserts, shimenv  # noqa: E402
from tests.support.retired_tokens import (  # noqa: E402
    FORBIDDEN_CONTRACT_TOKENS,
    V1_INVOCATION_PREFIX,
)
from tests.support.thread_rule import THREAD_RULE_ANCHORS  # noqa: E402


# ---------------------------------------------------------------------------
# Router routing-table parse
# ---------------------------------------------------------------------------

_ROUTE_ROW_RE = re.compile(
    r"^\|\s*(?P<vector>[^|]+?)\s*\|\s*`(?P<playbook>playbooks/[a-z0-9-]+\.md)`\s*\|"
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


def _named_fenced_block(text, predicate):
    """Return the first fenced block whose interior satisfies `predicate(interior_str)`."""
    interior, in_fence = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_fence:
                block = "\n".join(interior)
                if predicate(block):
                    return block
                interior, in_fence = [], False
            else:
                interior, in_fence = [], True
            continue
        if in_fence:
            interior.append(line)
    return None


class RouterRoutingTableTests(unittest.TestCase):
    def setUp(self):
        self.router_text = ROUTER.read_text(encoding="utf-8")
        self.rows = _parse_router_routing_table(self.router_text)
        self.playbooks = [pb for _, pb in self.rows]

    def test_router_exists_and_has_no_model_effort_pins(self):
        # Model/effort are not pinned — skills inherit the invoking session's model and effort.
        self.assertTrue(ROUTER.is_file(), "router SKILL.md must exist")
        head = "\n".join(self.router_text.splitlines()[:8])
        self.assertIn("name: drafter", head)
        self.assertNotIn("model:", head)
        self.assertNotIn("effort:", head)

    def test_routing_table_covers_exactly_the_four_routable_playbooks(self):
        self.assertEqual(
            {Path(pb).name for pb in self.playbooks},
            ROUTABLE_PLAYBOOKS,
            "router routing table must map exactly the three routable playbooks, got %r"
            % (sorted(Path(pb).name for pb in self.playbooks),),
        )

    def test_exactly_four_routable_playbooks_plus_one_spine_on_disk(self):
        on_disk = {p.name for p in PLAYBOOKS_DIR.glob("*.md")}
        self.assertEqual(
            on_disk,
            ROUTABLE_PLAYBOOKS | {SPINE},
            "playbooks/ must be exactly the three routable playbooks + the one shared spine, got %r"
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
        # architecture.md §5: prep proposes `suggested_playbook`; the router confirms against the table.
        # (mode, issue_type) -> playbook. `new` mode has type None (Step 1 classifies it).
        cases = [
            ("new", None, "new.md"),
            ("revise", "question", "question.md"),
            ("revise", "standard", "revise.md"),
            ("revise", "story", "revise.md"),
        ]
        table_basenames = {Path(pb).name for pb in self.playbooks}
        for mode, issue_type, expected in cases:
            self.assertEqual(
                prep_drafter._suggested_playbook(mode, issue_type),
                expected,
                "prep _suggested_playbook(%r, %r) should be %r" % (mode, issue_type, expected),
            )
            self.assertIn(expected, table_basenames, "the router table must route to %s" % expected)

    def test_all_four_playbooks_read_the_shared_spine(self):
        self.assertTrue((PLAYBOOKS_DIR / SPINE).is_file())
        for name in ROUTABLE_PLAYBOOKS:
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            self.assertIn(SPINE, text, "%s must read the shared spine %s" % (name, SPINE))

    def test_new_mode_classification_override_rule_is_visible_in_router(self):
        # The router must state that a new-mode session classifying its feedback as Epic/question
        # overrides suggested_playbook to question.md (architecture.md §5; S13 precedent). An Epic
        # classification needs no override since #16 — it is one issue, filed by new.md.
        self.assertIn("New-mode classification override rule", self.router_text)
        self.assertIn("question.md", self.router_text)
        self.assertIn("question.md", self.router_text)
        self.assertRegex(
            self.router_text,
            r"override[s]?\b.*(suggested_playbook|new\.md)",
            "the override must be stated against the prep suggestion",
        )


# ---------------------------------------------------------------------------
# Structural bar
# ---------------------------------------------------------------------------


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
            "router (%d) + largest playbook (%d) = %d exceeds %d (half of v1's 576): %r"
            % (router_lines, largest, router_lines + largest, V1_HALF_BAR, playbook_lines),
        )


class PlaybookInterleavingGrepTests(unittest.TestCase):
    """A playbook is a linear narrative for exactly one route — zero cross-route conditionals. Fails on
    either an `if … <route> … else …` construct or a `when the issue/type is a <route>` prose branch, over
    the three routable playbooks and the shared spine. Patterns broadened (S10 carried advisory) to the
    drafter's route set — the routing modes are new/revise/question; the classification cues
    (bug/incomplete/feature/story/standard) are legitimately named within new.md and are NOT routes, so
    they are excluded (the planner precedent excludes its own scale-to-work sub-classes identically)."""

    _ROUTES = r"(new|revise|question)"
    _IF_ELSE = re.compile(r"\bif\b[^.\n]{0,50}\b" + _ROUTES + r"\b[^.\n]{0,50}\belse\b", re.IGNORECASE)
    _WHEN_TYPE = re.compile(
        r"\bwhen (the (issue|type) is|it.?s)\s+(a |an )?" + _ROUTES + r"\b", re.IGNORECASE
    )

    def test_playbooks_have_no_cross_route_conditionals(self):
        for playbook in PLAYBOOKS_DIR.glob("*.md"):
            text = playbook.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                self.assertIsNone(
                    self._IF_ELSE.search(line),
                    "playbook %s:%d looks like a cross-route conditional: %r" % (playbook.name, i, line),
                )
                self.assertIsNone(
                    self._WHEN_TYPE.search(line),
                    "playbook %s:%d looks like a when-<route> prose branch: %r" % (playbook.name, i, line),
                )


class ContractTokenGateTests(unittest.TestCase):
    """Grep gates over skills/drafter/ — zero retired-executor tokens, zero v1 skill-invocation namespace strings,
    zero GATHER_/PERSIST_ op names, zero §P IDs, zero raw persist/gather WRITES in fences, zero w/
    shorthand. Every write the drafter specifies routes through gh_persist.py."""

    def test_no_github_ops_or_old_names_or_op_names_or_pids(self):
        forbidden = FORBIDDEN_CONTRACT_TOKENS
        for path in _iter_md(SKILL_DIR):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = forbidden.search(line)
                self.assertIsNone(
                    hit,
                    "forbidden contract token under skills/drafter/ at %s:%d — %r"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )

    def test_no_raw_persist_or_gather_writes_in_code_fences(self):
        # A persist/gather WRITE bypassing gh_persist.py. `gh_persist.py create`/`edit-body`/etc. are
        # NOT matches (they are `gh_persist.py`, not `gh issue|pr`). The reviewer prompt's `gh issue
        # view` self-fetch is a READ, not a write, and doesn't match.
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


# ---------------------------------------------------------------------------
# --dry-run persist envelopes for the writes the playbooks specify
# ---------------------------------------------------------------------------


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
    """Every GitHub write the drafter playbooks specify, run with --dry-run: a conformant envelope,
    status ok, `would_run` present, exit 0, no live gh call."""

    def _assert_dry_run_ok(self, proc, envelope, expected_op):
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertIsNotNone(envelope, "expected exactly one envelope on stdout")
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope.get("status"), "ok")
        self.assertEqual(envelope.get("op"), expected_op)
        self.assertTrue(envelope.get("dry_run"))
        self.assertIn("would_run", envelope)

    def test_build_issue_create_with_labels_and_deps_dry_run(self):
        # spine staged filing: gh_persist.py create <repo> <body.md> --title … --label … --blocked-by …
        proc, env = _run_persist(
            ["create", "octo/widgets", "@BODY@", "--title", "Build patient dashboard",
             "--label", "feature", "--label", "priority:high", "--blocked-by", "212", "--dry-run"],
            body_text="## User story\nAs a clinician …\n\n## Open questions\n"
            "<!-- open-question-links:v1 -->\n- OQ: `OQ-08` … in-scope (blocked) — question: #212\n",
        )
        self._assert_dry_run_ok(proc, env, "create")
        self.assertIn("--blocked-by", env.get("would_run", ""))
        self.assertIn("--label", env.get("would_run", ""))

    def test_question_issue_create_with_audience_labels_dry_run(self):
        # question.md filing: create --label question --label audience:business
        proc, env = _run_persist(
            ["create", "octo/widgets", "@BODY@", "--title",
             "PRD-OQ-06b — Which billing model for v1?", "--label", "question",
             "--label", "audience:business", "--dry-run"],
            body_text="## Question\nWhich billing model?\n\n## Audience\nbusiness\n",
        )
        self._assert_dry_run_ok(proc, env, "create")
        self.assertIn("audience:business", env.get("would_run", ""))

    def test_epic_stories_link_patch_edit_body_dry_run(self):
        # A legacy epic's `## Stories` checklist is reconciled via edit-body. That write is the
        # slicer's since #16 (cut.md S5, behind its write gate); this test covers the gh_persist op.
        proc, env = _run_persist(
            ["edit-body", "octo/widgets", "150", "@BODY@", "--dry-run"],
            body_text="## Goal\n…\n\n## Stories\n- [ ] #151 — first slice\n- [ ] #152 — second slice\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-body")

    def test_revise_body_edit_body_dry_run(self):
        # revise.md Step R4: apply the revised body, plan pointer preserved verbatim.
        proc, env = _run_persist(
            ["edit-body", "octo/widgets", "142", "@BODY@", "--dry-run"],
            body_text="## User story\n…\n\n> 📋 **Implementation plan:** see the implementation-plan "
            "comment.\n",
        )
        self._assert_dry_run_ok(proc, env, "edit-body")

    def test_revise_title_edit_title_dry_run(self):
        # revise.md Step R4: a confirmed Title old→new is applied through edit-title. Before this
        # op existed the diff-shown, operator-confirmed title change was silently dropped (the
        # playbook wrote only body + labels), since skills may not call raw `gh`.
        proc, env = _run_persist(
            ["edit-title", "octo/widgets", "142", "--title", "Build patient dashboard", "--dry-run"],
        )
        self._assert_dry_run_ok(proc, env, "edit-title")
        self.assertEqual(env.get("title"), "Build patient dashboard")
        self.assertNotIn("body_bytes", env)

    def test_revise_label_delta_edit_labels_dry_run(self):
        # revise.md label delta: gh_persist.py edit-labels <repo> <issue> --add … --remove …
        proc, env = _run_persist(
            ["edit-labels", "octo/widgets", "142", "--add", "priority:high", "--remove",
             "priority:low", "--dry-run"]
        )
        self._assert_dry_run_ok(proc, env, "edit-labels")
        self.assertEqual(env.get("added"), ["priority:high"])
        self.assertEqual(env.get("removed"), ["priority:low"])

    def test_reused_companion_crosslink_comment_dry_run(self):
        # spine Step 3.5: a reused companion question gets a lightweight cross-link comment (non-
        # destructive), rather than rewriting its body.
        proc, env = _run_persist(
            ["comment", "octo/widgets", "issue", "212", "@BODY@", "--dry-run"],
            body_text="Related to #142 — this question gates its consult-modality copy.\n",
        )
        self._assert_dry_run_ok(proc, env, "comment")

    def test_revise_native_dep_removal_link_dry_run(self):
        # revise.md R3 reconcile: a now-resolved companion's native blocked-by is removed via link.
        proc, env = _run_persist(
            ["link", "octo/widgets", "142", "--remove-blocked-by", "212", "--dry-run"]
        )
        self._assert_dry_run_ok(proc, env, "link")

    def test_dry_run_makes_no_live_gh_call(self):
        proc, env = _run_persist(
            ["create", "octo/widgets", "@BODY@", "--title", "T", "--label", "bug", "--dry-run"],
            body_text="## Description\nx\n",
        )
        self.assertEqual(proc.returncode, 0, msg="stderr: %s" % proc.stderr)
        self.assertTrue(env.get("dry_run"))
        self.assertNotIn("url", env, "a dry-run must not carry a live-write url")


class _ScriptedProcessRun:
    """Drop-in for pipelib.process.run — records calls, returns canned results in order (mirrors
    tests/test_gh_persist.py's identical fake; the offline shim cannot deliver the classifying stderr
    a real deps-capability failure carries, so the DEPS_UNSUPPORTED path is exercised in-process)."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __call__(self, argv, cwd=None, env=None, input_text=None, check=False):
        self.calls.append(list(argv))
        if not self._results:
            raise AssertionError("process.run over-called; calls: %r" % (self.calls,))
        return self._results.pop(0)


class CreateDepsUnsupportedFallbackTests(unittest.TestCase):
    """DoD box 1 / write-path coverage: the drafter's `create --blocked-by` capability-gates native deps
    — on a deps-unsupported gh/repo it retries WITHOUT the flag and reports DEPS_UNSUPPORTED as a notice
    (the issue still files; the prose `Blocked by #N` / `## Open questions` links are the fallback). Also
    asserts the spine documents that always-present prose fallback."""

    def setUp(self):
        self._original_run = gh_persist.process.run
        self.addCleanup(setattr, gh_persist.process, "run", self._original_run)

    def test_create_with_blocked_by_retries_without_and_notices_deps_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"## Open questions\n<!-- open-question-links:v1 -->\n- OQ ...\n")
            with_deps_failure = process.CommandResult(
                returncode=1, stdout="", stderr="unknown flag: --blocked-by\n"
            )
            without_deps_success = process.CommandResult(
                returncode=0, stdout="https://github.com/octo/widgets/issues/142\n", stderr=""
            )
            fake = _ScriptedProcessRun([with_deps_failure, without_deps_success])
            gh_persist.process.run = fake
            parser, _ = gh_persist._build_parser()
            args = parser.parse_args(
                ["create", "octo/widgets", str(body_path), "--title", "Dashboard",
                 "--label", "feature", "--blocked-by", "212"]
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exit_code = gh_persist._cmd_create(args, parser)
            env = json.loads(buf.getvalue().splitlines()[0])
            self.assertEqual(exit_code, 0)
            self.assertEqual(env["status"], "ok")
            self.assertIn("DEPS_UNSUPPORTED", env["notices"])
            self.assertEqual(env["url"], "https://github.com/octo/widgets/issues/142")
            # retry ordering: with-deps first, without-deps second.
            self.assertIn("--blocked-by", fake.calls[0])
            self.assertNotIn("--blocked-by", fake.calls[1])

    def test_spine_documents_the_prose_fallback(self):
        spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.assertIn("DEPS_UNSUPPORTED", spine)
        self.assertRegex(
            spine,
            re.compile(r"prose .*(Blocked by|## Open questions|Related to).*(fallback|regardless)", re.DOTALL),
            "the spine must state the prose links are the always-present fallback",
        )


# ---------------------------------------------------------------------------
# Rendering byte-compat vs the S1 captures
# ---------------------------------------------------------------------------


class RenderingByteCompatTests(unittest.TestCase):
    """DoD box 1 (offline half): the artifact schemas the drafter files are byte-clean against the S1
    captures. The drafter cites the _shared SSoT rather than restating; the byte identity is asserted on
    the _shared block the drafter points at, and the drafter's citation of it is verified."""

    def test_question_issue_schema_matches_s1_capture(self):
        shared = _first_fenced_block((SHARED / "question-issue.md").read_text(encoding="utf-8"))
        s1 = _first_fenced_block((EXAMPLES / "question-issue-body.md").read_text(encoding="utf-8"))
        self.assertEqual(shared, s1, "the question-issue body schema drifted from the S1 capture")

    def test_open_question_links_schema_matches_s1_capture(self):
        def is_schema(block):
            return (
                block.strip().startswith("## Open questions")
                and "<!-- open-question-links:v1 -->" in block
                and "<oq-id>" in block
            )
        shared = _named_fenced_block((SHARED / "open-question-links.md").read_text(encoding="utf-8"), is_schema)
        s1 = _named_fenced_block((EXAMPLES / "open-question-links.md").read_text(encoding="utf-8"), is_schema)
        self.assertIsNotNone(shared, "no schema block found in _shared/open-question-links.md")
        self.assertEqual(shared, s1, "the open-question-links:v1 schema drifted from the S1 capture")

    def test_drafter_cites_shared_schemas_not_restates_them(self):
        # The question-issue schema is cited in the question playbook + templates; the oq-links schema
        # in the spine + templates. Neither restates the fenced schema block (single source of truth).
        question = (PLAYBOOKS_DIR / "question.md").read_text(encoding="utf-8")
        self.assertIn("../../_shared/question-issue.md", question)
        spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.assertIn("../../_shared/open-question-links.md", spine)

    def test_open_question_links_marker_is_first_line_inside_the_section(self):
        shared = (SHARED / "open-question-links.md").read_text(encoding="utf-8")
        self.assertIn("## Open questions\n<!-- open-question-links:v1 -->", shared)


class HandoffRenderingTests(unittest.TestCase):
    """DoD box 4 (offline half): the drafter's handoff shapes are present, forward to the v2-renamed
    skills, and carry the always-checked `**Open questions:**` line rule + the terminal question shape."""

    def setUp(self):
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_handoff_renderings_reference_exists(self):
        self.assertTrue((REFERENCES_DIR / "handoff-renderings.md").is_file())

    def test_single_issue_shape_forwards_to_v2_planner_with_plan_absent(self):
        self.assertIn("plan: ✗", self.renderings)
        self.assertIn("/ctrl-freaq:planner", self.renderings)
        # v2 rename: never the v1 skill name in the next-command.
        self.assertNotIn(V1_INVOCATION_PREFIX, self.renderings)

    def test_epic_batch_shape_present(self):
        self.assertRegex(self.renderings, r"\*\*Epic:\*\* #\d+ .*· epic · plan: ✗")
        self.assertIn("**Stories:**", self.renderings)

    def test_open_questions_line_is_stated_as_always_checked_condition(self):
        self.assertIn("**Open questions:**", self.renderings)
        self.assertRegex(
            self.renderings,
            r"whenever the filed body carries an `## Open questions`",
            "the OQ-line rule must be an always-checked condition, never example-dependent",
        )

    def test_terminal_question_shape_present(self):
        self.assertIn("**Audience:**", self.renderings)
        self.assertIn("(terminal — no follow-up skill)", self.renderings)
        # A question omits the research:/plan: markers.
        idx = self.renderings.find("· question\n**Audience:**")
        self.assertNotEqual(idx, -1, "the terminal question Issue line must be present")

    def test_every_handoff_block_that_gates_oqs_shows_the_line(self):
        blocks = re.findall(r"```\n(## Handoff.*?)```", self.renderings, re.DOTALL)
        self.assertTrue(blocks, "no ## Handoff blocks parsed from renderings")
        gated = [b for b in blocks if "scoped out" in b or "blocked-by" in b]
        self.assertTrue(gated, "expected a handoff block exercising an OQ disposition")
        for b in gated:
            self.assertIn(
                "**Open questions:**", b,
                "a handoff block names an OQ disposition but drops the **Open questions:** line:\n%s" % b,
            )


class AmbientIssueGateTests(unittest.TestCase):
    """The ambient-branch relationship gate. A drafter session invoked from inside `epic/95-<slug>`
    used to file an issue with no link to #95 at all; `facts.ambient` is the fact that lets it offer
    one. Two properties are load-bearing and pinned here: nothing is ever linked without the card (a
    silent auto-link would be wrong — filing an unrelated bug from an epic's branch is normal, so only
    the no-link outcome may be announced instead of asked), and the drafter still writes NO parent
    edge, since both hierarchy edges are the slicer's (#16)."""

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.router = ROUTER.read_text(encoding="utf-8")
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")

    def test_spine_consumes_the_ambient_fact_as_data(self):
        self.assertIn("facts.ambient", self.spine)
        self.assertIn('header: "Branch issue"', self.spine)

    def test_router_names_the_fact_and_the_gate(self):
        self.assertIn("`ambient`", self.router)
        self.assertIn("ambient-branch relationship", self.router)

    def test_only_the_no_link_outcome_is_announced(self):
        flat = " ".join(self.spine.split())
        self.assertIn("never link silently", flat)
        # A plainly-unrelated draft is a one-option judgment: announced, filed unlinked, no card
        # (skills/_shared/asking-the-user.md). Every link still needs the operator's answer.
        self.assertIn("A draft plainly unrelated to #N files unlinked, announced", flat)
        self.assertIn('plausibly belongs to #N asks once (`header: "Branch issue"`)', flat)
        self.assertIn("**Unrelated** links nothing", flat)

    def test_child_arm_is_epic_only_and_routes_to_the_slicer(self):
        self.assertIn("`ambient.pattern` is `epic`", self.spine)
        self.assertIn("slicer", self.spine)

    def test_drafter_writes_no_parent_edge(self):
        """The invariant #16 established: the slicer is the sole writer of both hierarchy edges, so
        no drafter command may set one. Fence-scoped, per this repo's grep convention — prose that
        *describes* the relation (issue-templates.md explains why a fresh epic body carries no
        `## Stories` section) is documentation, while the same token inside a fence is a command."""
        for path in _iter_md(SKILL_DIR):
            for lineno, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                for banned in ("add-parent", "--parent"):
                    self.assertNotIn(
                        banned,
                        line,
                        "%s:%d runs a parent-edge write (%s): %s"
                        % (path.name, lineno, banned, line.strip()),
                    )

    def test_adoption_handoff_shape_exists_and_uses_the_real_slicer_flag(self):
        self.assertIn("/ctrl-freaq:slicer", self.renderings)
        self.assertRegex(
            self.renderings,
            r"/ctrl-freaq:slicer \d+ --adopt \d+",
            "the adoption handoff must hand the slicer's real `--adopt <N>` invocation",
        )


class HandoffBindingLanguageTests(unittest.TestCase):
    """Post-Scenario-2 fix: docs/specs/parity/drafter.md recorded a 2/2 live-parity handoff-rendering
    drift (Scenario-1 Div-2 / Scenario-2 Div-4) — v2 renamed `**Issue:**`/`**Epic:**` to `**Filed:**`,
    dropped the state marker, invented a `Snapshot` block, and inlined the fenced `Next:` command, despite
    a correct prompt. Pins the binding fix: the point-of-use `Read` is immediate (not earlier in the
    session), the shape must be emitted/copied verbatim (mirroring resolver's/evaluator's "emit the
    matching shape" phrasing — the drift-free skills), and the four observed drift forms are named as
    explicit prohibitions in both the router and the reference file actually read at emission time."""

    _DRIFT_PROHIBITIONS = (
        "Filed",           # renamed field (**Filed:** instead of **Issue:**/**Epic:**)
        "state",           # dropped `· <state> ·` segment
        "Snapshot",        # invented block
        "Next:",           # inlined instead of fenced
    )

    @staticmethod
    def _flat(text):
        # Word-wrapped prose carries a literal newline where a space would read the same to a human;
        # collapse all whitespace runs to a single space so a multi-word phrase-match survives wrapping.
        return re.sub(r"\s+", " ", text)

    def setUp(self):
        self.router = ROUTER.read_text(encoding="utf-8")
        self.router_flat = self._flat(self.router)
        self.renderings = (REFERENCES_DIR / "handoff-renderings.md").read_text(encoding="utf-8")
        self.renderings_flat = self._flat(self.renderings)
        self.playbook_texts = {
            name: (PLAYBOOKS_DIR / name).read_text(encoding="utf-8") for name in ROUTABLE_PLAYBOOKS
        }
        self.playbook_texts_flat = {name: self._flat(t) for name, t in self.playbook_texts.items()}

    def test_router_forces_read_immediately_before_composing(self):
        self.assertRegex(
            self.router_flat,
            r"Read that reference immediately before composing the handoff",
            "the router must force the point-of-use Read to happen at emission time, not earlier",
        )

    def test_router_binds_emit_verbatim_not_match_and_fill(self):
        self.assertRegex(
            self.router_flat, r"emit the matching shape verbatim",
            "the router must bind to 'emit … verbatim' (mirrors resolver/evaluator), not 'match … to a shape'",
        )
        self.assertIn("contract, not prose to summarize", self.router_flat)

    def test_router_names_all_four_drift_forms_as_prohibitions(self):
        for token in ("**Filed:**", "state", "Snapshot", "Next:"):
            self.assertIn(token, self.router)
        self.assertIn(
            "never paraphrase, restructure, rename a field, drop a segment, or add a block",
            self.router_flat,
        )

    def test_reference_file_intro_binds_copy_the_shape(self):
        # The file actually Read right before emission carries the same binding, not just the router.
        self.assertRegex(
            self.renderings_flat,
            r"[Cc]opy its shape, and substitute only the issue/Epic/story numbers",
            "the reference-file intro must bind to 'copy the shape' (mirrors resolver's intro)",
        )
        self.assertIn("contract, not a style to imitate", self.renderings_flat)

    def test_reference_file_names_all_four_drift_forms_as_prohibitions(self):
        for token in ("**Filed:**", "Snapshot", "Next:"):
            self.assertIn(token, self.renderings)
        self.assertRegex(self.renderings_flat, r"drop the\s*`?· <state> ·`?\s*segment")

    def test_every_playbook_handoff_section_binds_emit_verbatim(self):
        for name, text in self.playbook_texts_flat.items():
            self.assertRegex(
                text,
                r"immediately before composing this and emit the matching shape verbatim",
                "%s's ## Handoff section must bind to 'emit … verbatim', not 'match the outcome'" % name,
            )
            self.assertRegex(
                text, r"never rename a field or restructure it",
                "%s must carry the never-restructure prohibition" % name,
            )

    def test_binding_language_is_prose_not_inside_a_code_fence(self):
        for label, text, path in (
            ("router", self.router, ROUTER),
            ("renderings", self.renderings, REFERENCES_DIR / "handoff-renderings.md"),
        ):
            for i, line, in_fence in _fence_stripped_lines(path):
                if in_fence:
                    self.assertNotIn(
                        "emit the matching shape verbatim", line,
                        "%s:%d — the binding rule must be prose, not inside a code fence" % (label, i),
                    )

    def test_deliberate_violation_would_be_caught(self):
        # Proof the predicate actually discriminates: a router missing the binding fails the same assertion
        # the real router must pass.
        weak_router = "Read that reference before composing the handoff and match the run's outcome to a shape."
        self.assertNotRegex(weak_router, r"emit the matching shape verbatim")


# ---------------------------------------------------------------------------
# The falsifiable OQ-absorption rule (DoD box 3, offline half)
# ---------------------------------------------------------------------------


class FalsifiableOqRuleTests(unittest.TestCase):
    """DoD box 3 (offline half): the falsifiable rule — an unresolved source-doc OQ is never absorbed
    into a build issue without a tracked companion + an explicit disposition — is written as fence-scoped
    prose in the spine and pinned by grep. Wording class: 'absorbing an untracked OQ silently is a
    defect.'"""

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")

    def test_rule_is_present_and_named_falsifiable(self):
        self.assertIn("Falsifiable rule", self.spine)
        self.assertIn("Absorbing an untracked OQ silently is a defect", self.spine)

    def test_rule_is_prose_not_inside_a_code_fence(self):
        for i, line, in_fence in _fence_stripped_lines(PLAYBOOKS_DIR / SPINE):
            if in_fence:
                self.assertNotIn(
                    "untracked OQ silently", line,
                    "the falsifiable rule must be prose, not inside a code fence (%d)" % i,
                )

    def test_rule_names_both_conditions_and_the_search_paths(self):
        # (i) a tracked companion — matched candidate OR a freshly filed question.
        self.assertRegex(self.spine, re.compile(r"matched tracker issue.*or.*filed.*question", re.DOTALL))
        # (ii) an explicit disposition from the closed set.
        for disp in ("scoped-out", "in-scope (blocked)", "provisional-default"):
            self.assertIn(disp, self.spine, "closed-set disposition %r missing" % disp)
        # in-scope (blocked) also sets the native dependency.
        self.assertRegex(self.spine, re.compile(r"in-scope \(blocked\).*native `blocked by`", re.DOTALL))

    def test_not_filed_is_gated_only_when_search_consulted_empty(self):
        self.assertRegex(
            self.spine,
            r"`?question: \(not filed\)`?.*only\s*when",
            "the (not filed) rule must be gated (only when the search returned no candidate …)",
        )
        # Body-recorded OQ → prep's open_question_candidates; newly-detected OQ → --oq-query.
        self.assertIn("facts.open_question_candidates", self.spine)
        self.assertIn("--oq-query", self.spine)

    def test_prep_oq_query_flag_exists(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "prep_drafter.py"), "--help"],
            capture_output=True, encoding="utf-8", check=False,
        )
        self.assertIn("--oq-query", proc.stdout + proc.stderr)


# ---------------------------------------------------------------------------
# Carried judgment references present
# ---------------------------------------------------------------------------


class OperatorGateCoverageTests(unittest.TestCase):
    """DoD box 4 (offline half): the spec's (docs/specs/drafter.md "Operator gates") gates are present in
    the v2 skill, pinned by grep. Each header is an `AskUserQuestion` gate the spine/playbooks raise."""

    def _skill_text(self):
        return "\n".join(p.read_text(encoding="utf-8") for p in _iter_md(SKILL_DIR))

    def test_operator_gate_headers_present(self):
        text = self._skill_text()
        for header in (
            "Issue size",      # feature vs Epic
            "Branch issue",    # relationship to the issue this branch is standing in
            "PRD conflict",
            "File issue?",
            "OQ <id>",         # OQ disposition
            "Closed issue",    # revise: closed target
            "Epic closed",     # revise a story under a closed epic
            "Review loop",     # review-loop tie-break
        ):
            self.assertIn(
                'header: "%s"' % header, text, "operator gate %r missing from skills/drafter/" % header
            )

    def test_companion_reuse_gate_present(self):
        spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.assertIn("**Reuse #N**", spine)
        self.assertIn("**File a new one**", spine)


class CarriedReferenceTests(unittest.TestCase):
    def test_reviewer_prompt_present_and_carries_its_five_dimensions(self):
        """#16 moved dimensions 5 (ordering) and 7 (sizing / over-split, with the bookend check) to the
        slicer's cut reviewer, which applies them at both altitudes. The numbering keeps its GAPS on
        purpose: renumbering the survivors would silently change what every caller's dimension set
        means, and every playbook names its set by number."""
        prompt_path = REFERENCES_DIR / "issue-reviewer-prompt.md"
        self.assertTrue(prompt_path.is_file())
        text = prompt_path.read_text(encoding="utf-8")
        for n in (1, 2, 3, 4, 6):
            self.assertRegex(text, r"(?m)^%d\.\s+\*\*" % n, "dimension %d must be present" % n)
        for n in (5, 7):
            self.assertNotRegex(
                text, r"(?m)^%d\.\s+\*\*" % n, "dimension %d moved to the slicer" % n
            )
        # A tombstone, so a later editor learns where they went instead of "restoring" them.
        self.assertIn("cut-reviewer-prompt.md", text)
        # `split` mode retired with the batch it served.
        self.assertNotIn("Mode: <draft | revise N | split>", text)

    def test_reviewer_prompt_does_not_cite_retired_signal_doc(self):
        prompt = (REFERENCES_DIR / "issue-reviewer-prompt.md").read_text(encoding="utf-8")
        self.assertNotIn("subagent-decision-signal", prompt)

    def test_issue_templates_reference_present(self):
        templates = (REFERENCES_DIR / "issue-templates.md").read_text(encoding="utf-8")
        for section in ("## Steps to reproduce", "## User story", "## Stories", "**Epic:** #<epic-#>"):
            self.assertIn(section, templates, "built-in template fragment %r missing" % section)


class ReviewTierAndAnchorRuleTests(unittest.TestCase):
    """The 2026-08-01 lean-review + essence-body contract (plan: drafter essence-first bodies +
    proportional review depth). Pins: the spine's two review tiers with the route-neutral full
    default; the exact proxy handshake phrase kept in sync between the spine and
    _shared/follow-up-filing.md; the essence-only proxy Description rule (the incident's caller-side
    seed); the reviewer prompt's delta-scope, anchor-rule + carve-out, absolute-path grounding, and
    parent-PR-attribution rules; and the templates' anchor/altitude block. All prose gates — no
    offline test can run the loop itself."""

    HANDSHAKE = "proxy-filed follow-up (lean review)"

    @staticmethod
    def _flat(text):
        return re.sub(r"\s+", " ", text)

    def setUp(self):
        self.spine = (PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8")
        self.spine_flat = self._flat(self.spine)
        self.filing = (SHARED / "follow-up-filing.md").read_text(encoding="utf-8")
        self.filing_flat = self._flat(self.filing)
        self.prompt = (REFERENCES_DIR / "issue-reviewer-prompt.md").read_text(encoding="utf-8")
        self.prompt_flat = self._flat(self.prompt)
        self.templates = (REFERENCES_DIR / "issue-templates.md").read_text(encoding="utf-8")
        self.templates_flat = self._flat(self.templates)

    def test_spine_defines_both_tiers_with_route_neutral_full_default(self):
        self.assertIn("exactly one pass", self.spine_flat, "lean tier must be a single pass")
        self.assertIn(
            "show suggestions and nits **unapplied** at the filing gate",
            self.spine_flat,
            "lean tier must surface suggestions unapplied (auto-apply is the ratchet)",
        )
        self.assertIn(
            "default **full** absent a stated tier",
            self.spine_flat,
            "the spine's tier default must be route-neutral (no per-route enumeration)",
        )
        # Full tier keeps the literal review-loop control mechanics by name.
        self.assertIn("3-pass cap", self.spine)
        self.assertIn("**circular** exit", self.spine)

    def test_spine_full_tier_carries_delta_scope(self):
        self.assertIn(
            "never re-verify an unchanged claim",
            self.spine_flat,
            "full-tier passes 2+ must be delta-scoped (pass 2 of the incident re-verified everything)",
        )

    def test_full_tier_exits_on_blockers_with_polish_shown_unapplied(self):
        # #88 (part): "none left → exit clean" let one suggestion or nit buy a full extra pass.
        self.assertIn("**only a blocker keeps the loop running**", self.spine_flat)
        self.assertIn(
            "its suggestions and nits shown **unapplied** at the filing gate", self.spine_flat
        )
        self.assertIn("three passes with blockers left → **cap** exit", self.spine_flat)
        self.assertNotIn("none left → exit clean", self.spine_flat)

    def test_spine_binds_grounding_to_absolute_root(self):
        self.assertIn(
            "by absolute path",
            self.spine_flat,
            "review-loop grounding must bind to facts.root.path by absolute path, never ambient cwd",
        )

    def test_handshake_phrase_in_sync_between_spine_and_filing_protocol(self):
        self.assertIn(self.HANDSHAKE, self.spine_flat, "spine must name the provenance-floor phrase")
        self.assertIn(self.HANDSHAKE, self.filing_flat, "follow-up-filing must state the same phrase")

    def test_filing_protocol_is_lean_and_essence_only(self):
        self.assertNotIn(
            "don't try to shortcut",
            self.filing_flat,
            "the don't-shortcut instruction must be gone (a follow-up gets the lean tier)",
        )
        self.assertIn("single lean review pass", self.filing_flat)
        self.assertIn(
            "never pasted grep output",
            self.filing_flat,
            "the caller Description must be essence-only (the incident's caller-side seed)",
        )
        self.assertIn(
            "approve on step 3's checks alone",
            self.filing_flat,
            "surfaced-unapplied suggestions are informational at the proxy gate",
        )

    def test_filing_protocol_carries_parent_pr_attribution(self):
        self.assertIn(
            "never asserted as current-repo truth",
            self.filing_flat,
            "parent-PR-introduced state must be attributed to the PR in the body",
        )

    def test_resolver_registry_description_bar_matches(self):
        # follow-up-tracking.md restates the Description bar (render, don't restate — keep in sync).
        tracking = self._flat(
            (REPO_ROOT / "skills" / "resolver" / "references" / "follow-up-tracking.md").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("never pasted grep output", tracking)

    def test_filing_protocol_spawns_the_approved_batch_in_one_message(self):
        """Serial spawning cost the drafter's full round-trip per item (an observed run: 3 follow-ups
        in ~30 minutes). The items are independent, so the batch goes out concurrently."""
        self.assertIn("all in one message", self.filing_flat)
        self.assertIn("so the batch runs concurrently", self.filing_flat)

    def test_filing_protocol_isolates_a_failed_item_and_serializes_same_target_revises(self):
        self.assertIn("does not hold up the others", self.filing_flat)
        self.assertIn(
            "`revise-existing` items that share a target issue go one at a time",
            self.filing_flat,
            "same-target revises stage to one revised.md and would race edit-body",
        )

    def test_resolver_registry_renders_the_batch_rule(self):
        # render, don't restate — the caller must not read as serial where the shared file is batched.
        tracking = self._flat(
            (REPO_ROOT / "skills" / "resolver" / "references" / "follow-up-tracking.md").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("all in one message", tracking)

    def test_follow_ups_file_one_issue_per_related_group(self):
        """Related follow-ups file as ONE issue with one checkable bullet per item — one issue per
        item flooded the tracker with findings one PR fixes together."""
        self.assertIn("## Grouping — one issue per related group", self.filing)
        for phrase in (
            "Never mix types",
            "A `bug` group only on a shared root cause",
            "At most ~5 items",
            "spawn one `general-purpose` sub-agent per group",
            "with the item ids it covers",
            "none dropped, none merged into another",
            "one checkable bullet per item",
        ):
            self.assertIn(phrase, self.filing_flat)
        # The drafter side: a grouped brief is one issue, never an epic to decompose.
        self.assertIn("**Grouped follow-up**", self.templates)
        self.assertIn("never an epic to decompose", self.templates_flat)
        # The planner's seam follow-ups stay one per seam — each boundary bullet cites its own #M.
        self.assertIn("seam-disposition follow-ups are not grouped", self.filing_flat)

    def test_router_names_the_per_process_new_mode_scratch(self):
        """Concurrent proxy-filed drafters must not share a staging dir (prep_drafter.py's default)."""
        self.assertIn("/tmp/gh-drafter-new-<pid>", ROUTER.read_text(encoding="utf-8"))

    def test_router_warns_that_a_prep_re_run_changes_the_scratch(self):
        """Per-process keying made `facts.scratch` per-invocation, where it used to be stable across a
        session. A decision-card retry re-runs prep, so a stale value now stages into an abandoned dir
        and the create aborts on the empty-body gate."""
        router = self._flat(ROUTER.read_text(encoding="utf-8"))
        self.assertIn("A re-run mints a **fresh `facts.scratch`** in new mode", router)
        self.assertIn("stage to the latest value", router)

    def test_reviewer_prompt_carries_tier_and_delta_scope_inputs(self):
        self.assertIn("<<review_tier>>", self.prompt)
        self.assertIn("<<changed_summary>>", self.prompt)
        self.assertIn(
            "never re-verify an unchanged claim you already confirmed",
            self.prompt_flat,
        )

    def test_reviewer_prompt_anchor_rule_has_the_carve_out(self):
        self.assertIn("Anchor rule", self.prompt)
        self.assertIn(
            "verbatim-quoted tool output",
            self.prompt_flat,
            "the reviewer is context-blind — the carve-out must be restated in its own prompt",
        )

    def test_reviewer_prompt_binds_grounding_and_pr_attribution(self):
        self.assertIn(
            "never a bare relative path in YOUR OWN ambient working directory",
            self.prompt_flat,
            "the reviewer must stay pinned to <<repo_root>> — a sub-agent's cwd is not the drafter's",
        )
        self.assertIn("Parent-PR attribution", self.prompt)
        self.assertIn("never verify it against the checkout", self.prompt_flat)

    def test_templates_carry_anchor_rule_and_criterion_form_dod(self):
        self.assertIn("Issue bodies cite durable anchors", self.templates_flat)
        self.assertIn("never a frozen enumerated hit list", self.templates_flat)
        self.assertIn(
            "verbatim-quoted tool output",
            self.templates_flat,
            "the carve-out must ride the rule everywhere it is rendered",
        )

    def test_lean_capable_playbooks_name_the_tier_as_a_fact(self):
        new_text = self._flat((PLAYBOOKS_DIR / "new.md").read_text(encoding="utf-8"))
        revise_text = self._flat((PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8"))
        self.assertIn("**Review tier.**", new_text)
        self.assertIn("→ **lean**", new_text)
        self.assertIn("**Review tier.**", revise_text)
        self.assertIn(self.HANDSHAKE, revise_text)


class DodHeadingContractTests(unittest.TestCase):
    """4.32.0: the drafter files criteria under `## Definition of done` and proves it before the gate.

    The New-feature template used to head its checklist `## Acceptance criteria`, so every feature the
    drafter filed reached the planner with "no DoD": the planner indexed the other heading by eye, and
    the resolver — handed `closes-dod` indexes against `dod: []` — ticked bullets no parser reads.
    """

    def setUp(self):
        self.templates = (REFERENCES_DIR / "issue-templates.md").read_text(encoding="utf-8")
        self.spine = " ".join((PLAYBOOKS_DIR / SPINE).read_text(encoding="utf-8").split())
        self.revise = " ".join((PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8").split())

    def test_no_built_in_template_heads_a_checklist_acceptance_criteria(self):
        headings = [
            line.strip() for line in self.templates.splitlines() if line.startswith("## ")
        ]
        self.assertNotIn("## Acceptance criteria", headings)

    def test_the_feature_template_carries_a_dod(self):
        feature = self.templates.partition("**New feature template")[2].split("**Epic template")[0]
        self.assertIn("## Definition of done", feature)

    def test_the_heading_rule_binds_repo_templates_too(self):
        flat = " ".join(self.templates.split())
        self.assertIn("**Checklist heading rule (every template, repo template included).**", flat)
        self.assertIn("title the checklist `## Definition of done`", flat)

    def test_the_spine_proves_the_staged_body_before_the_filing_gate(self):
        check = '${CLAUDE_PLUGIN_ROOT}/scripts/parse.py dod "<facts.scratch>/<name>.md"'
        self.assertIn(check, self.spine)
        self.assertIn("no criteria left in `other_checklists`", self.spine)
        # A repo template's non-criteria confirmation list must survive the check.
        self.assertIn("a repo template's confirmation list may stay", self.spine)
        self.assertLess(self.spine.index(check), self.spine.index('(`header: "File issue?"`)'))

    def test_revise_renames_only_off_a_slice(self):
        self.assertIn("rename that heading to `## Definition of done`", self.revise)
        self.assertIn("Only when `facts.revise.dod.slice` is `false`", self.revise)

    def test_follow_up_filing_offers_no_other_heading(self):
        text = " ".join((SHARED / "follow-up-filing.md").read_text(encoding="utf-8").split())
        self.assertNotIn("or `## Acceptance criteria` for a", text)


class ThreadWalkAuthorRuleTests(unittest.TestCase):
    """#186: revise's Step R2 thread walk applies #179's author rule to marker-shaped comments."""

    def test_revise_r2_thread_walk_states_the_author_rule(self):
        text = (PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8")
        walk = text[text.index('## Step R2'):]
        walk = walk[:walk.index('## Step R3')]
        for anchor in THREAD_RULE_ANCHORS:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, walk)


if __name__ == "__main__":
    unittest.main()
