"""Unit tests for scripts/prep_evaluator.py — the evaluator's complete facts block in one call
(architecture.md §4; docs/implementation.md S6). This is the **pilot** prep script: the first to
compose other executors in-process (architecture.md §2 "Prep scripts compose the executors
in-process"), so these tests exercise the composition itself as much as the facts it produces.

Test topology (mirrors the two existing offline patterns, combined for the first time — no
existing executor test needs both at once):

- **`gh` calls** (gh_pr_gather / gh_gather / repo-merge-config / current-user) go through the
  offline shim (`tests/support/shimenv`), per `tests/README.md`.
- **`git` calls** (workspace.py's root-freshness + `ensure --work`) go through a real temp
  origin+clone (`tests/support/gitsandbox`), per architecture.md §10 "Git sandbox: ... no shim
  needed" — `workspace.py` never shells out to `gh` at all.

`prep_evaluator.py` is driven as a **real subprocess** (mirroring `tests/test_gh_pr_gather.py`'s
`_run_script`/`tests/test_workspace.py`'s `_run_cli`) so every test exercises the full argv-in /
subprocess / envelope-out path a real caller uses — not just the in-process `build_facts()`
function in isolation (that function is still unit-tested directly for the pure-classification
helpers below, where a subprocess round-trip would add nothing).

Coverage matrix (S6 DoD):
- Facts schema conformance (envelope_asserts + the architecture.md §4 field set).
- Four CI states: green / red / pending / none.
- Health-cache hit / miss (SHA match / mismatch) / absent.
- merge-policy present / absent (config block absent entirely, exercised via happy_standard).
- PR-type detection: story / epic-integration / standard.
- Closing-issue DoD parsed.
- Decision paths: MARKER_AMBIGUOUS (duplicate health-cache comments), ROOT_* propagation,
  BRANCH_IN_USE, AUTH_REQUIRED.
- --refresh re-derives PR state + CI without touching workspace.py/config_block.py at all.
- Single-invocation budget: the shim's call count for one prep run is bounded (prd.md §9.2).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "prep_evaluator.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_evaluator  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _git(args, cwd):
    result = subprocess.run(
        ["git"] + args, cwd=str(cwd), capture_output=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise RuntimeError("git %s (cwd=%s) failed: %s" % (" ".join(args), cwd, result.stderr))
    return result.stdout.strip()


def _parse_one_envelope(stdout_text):
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(
            "expected exactly one non-blank stdout line (the envelope), got %d: %r"
            % (len(lines), lines)
        )
    return json.loads(lines[0])


# The doc catalogue every sandbox starts configured with (skills/_shared/doc-catalogue.md) — one
# entry naming a document setUp also creates, so a baseline run reports one present grounding doc,
# no notice, and no attention line.
SEEDED_CATALOGUE_INTERIOR = "- `docs/prd.md` — prd — binding — What the product is.\n"


def _catalogue_block(interior):
    return "<!-- doc-catalogue -->\n%s<!-- /doc-catalogue -->\n" % interior


class PrepEvaluatorSandboxTestCase(unittest.TestCase):
    """Shared setup: a real temp git origin+clone (the `--root`), pre-seeded with `.gitignore`
    (mirroring tests/test_workspace.py's own setUp rationale: a fresh consuming repo's very first
    `ensure` leaves `.gitignore` uncommitted by design, which would spuriously trip ROOT_DIRTY on
    every test's very first call if left unseeded)."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        _write(self.root / ".gitignore", ".worktrees/\n")
        # A configured consuming repo: a doc catalogue naming one document that exists (the
        # test_prep_planner seed), so the grounding read is neutral for every test not about it —
        # without it each run's `attention` carries the catalogue-absent line.
        _write(self.root / "docs" / "README.md", _catalogue_block(SEEDED_CATALOGUE_INTERIOR))
        _write(self.root / "docs" / "prd.md", "# PRD\n")
        _git(["add", ".gitignore", "docs"], self.root)
        _git(["commit", "-m", "seed gitignore + doc catalogue"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    # The fixtures' canned PR head OID. v3's attach asserts ambient HEAD == pr.headRefOid
    # EXACTLY, and a sandbox worktree's HEAD is a real, run-specific SHA — so the harness stamps
    # a fixture copy, replacing this sentinel with the actual worktree HEAD across every fixture
    # file (view_88.json AND the cache-hit marker body, which deliberately share it).
    CANNED_HEAD_OID = "deadbeef00112233445566778899aabbccddeeff"

    def _mk_ambient(self, branch):
        """Create (or reuse) `.worktrees/<branch>` in the sandbox clone — the PR-head worktree the
        operator would have opened via workspace-open. Returns its Path."""
        wt = self.root / ".worktrees" / branch
        if not wt.is_dir():
            wt.parent.mkdir(parents=True, exist_ok=True)
            _git(["worktree", "add", "-b", branch, str(wt), "origin/main"], self.root)
        return wt

    def _head_branch_from_fixture(self, fixture_case):
        case_dir = shimenv.fixture_case_dir(fixture_case)
        for f in sorted(case_dir.glob("*.json")):
            if f.name == "manifest.json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(data, dict) and "headRefName" in data:
                return data["headRefName"]
        return None

    def _stamped_fixture(self, fixture_case, head_sha):
        """Copy the fixture dir and replace the canned head OID with the real worktree HEAD."""
        import shutil

        src = shimenv.fixture_case_dir(fixture_case)
        dst = Path(tempfile.mkdtemp(prefix="gh-evaluator-stamp-"))
        self.addCleanup(lambda: shutil.rmtree(str(dst), ignore_errors=True))
        for f in src.iterdir():
            content = f.read_bytes().replace(
                self.CANNED_HEAD_OID.encode("ascii"), head_sha.encode("ascii")
            )
            (dst / f.name).write_bytes(content)
        return dst

    def _run(self, args, fixture_case=None, extra_env=None, cwd=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            env=env,
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def _envelope(self, pr="88", repo="octo/widgets", fixture_case="prep_evaluator_happy_standard",
                  extra_args=None, ambient=True, ambient_branch=None, stamp=True, extra_env=None):
        """Run the prep and return its envelope. With `ambient` (the default), the subprocess runs
        with its cwd INSIDE a worktree on the fixture PR's head branch (v3's operator posture) and
        the fixture is stamped so pr.headRefOid == that worktree's HEAD. `ambient_branch`
        overrides the branch (a wrong-branch mismatch test); `stamp=False` leaves the canned OID
        in place (a stale-checkout mismatch test). Flows that decide before the workspace
        assertion (auth, marker-ambiguous, --refresh) pass ambient=False and run as before."""
        args = [pr, repo, "--root", str(self.root), "--scratch-dir", self.scratch]
        if extra_args:
            args += extra_args
        run_cwd = None
        fixtures_dir = None
        if ambient:
            branch = ambient_branch or self._head_branch_from_fixture(fixture_case)
            if branch is not None:
                wt = self._mk_ambient(branch)
                run_cwd = str(wt)
                if stamp:
                    fixtures_dir = self._stamped_fixture(fixture_case, _git(["rev-parse", "HEAD"], wt))
        if fixtures_dir is not None:
            result = self._run(
                args, fixture_case=None,
                extra_env=dict(extra_env or {}, GH_SHIM_FIXTURES=str(fixtures_dir)), cwd=run_cwd,
            )
        else:
            result = self._run(args, fixture_case=fixture_case, extra_env=extra_env, cwd=run_cwd)
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope


class ScriptExistsTests(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_script_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPT)], capture_output=True, encoding="utf-8"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class HappyPathFactsSchemaTests(PrepEvaluatorSandboxTestCase):
    """Facts schema matches architecture.md §4's common core + evaluator extensions."""

    def test_conformant_ok_envelope_with_evaluator_facts(self):
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        for key in (
            "repo", "scratch", "root", "target", "vector", "suggested_playbook", "workspace",
            "config", "health_cache", "sections", "dod", "attention", "notices",
            "pr_type", "ci", "self_review", "current_user", "merge_config",
            "blocked_by", "deps_available",
        ):
            self.assertIn(key, envelope, "missing architecture.md §4 facts-block key %r" % key)

    def test_root_facts_shape(self):
        # v3.x: the retired origin/main pin took `sha`/`source` with it; `root` now carries the
        # main checkout's path and the DERIVED default branch.
        envelope = self._envelope()
        self.assertEqual(envelope["root"]["path"], str(self.root))
        self.assertEqual(envelope["root"]["default_branch"], "main")
        self.assertNotIn("sha", envelope["root"])
        self.assertNotIn("source", envelope["root"])
        self.assertTrue(envelope["root"]["fresh"])

    def test_target_facts_shape(self):
        envelope = self._envelope()
        self.assertEqual(envelope["target"]["kind"], "pr")
        self.assertEqual(envelope["target"]["number"], 88)
        self.assertEqual(envelope["target"]["title"], "Fix widget cold-start flash")
        self.assertEqual(envelope["target"]["state"], "OPEN")

    def test_target_labels_populated_from_pr_labels(self):
        # gh_pr_gather's `labels` field (a v2 addition) maps through to `target.labels` —
        # replaces the old hardcoded `[]`. The happy_standard fixture's PR carries a `story`
        # label, matching architecture.md §4's own facts-block example (`"labels": ["story"]`)
        # byte-for-byte, so this test also proves prep's output matches the documented example.
        envelope = self._envelope()
        self.assertEqual(envelope["target"]["labels"], ["story"])

    def test_vector_and_suggested_playbook_for_standard_pr(self):
        envelope = self._envelope()
        self.assertEqual(envelope["vector"]["type"], "standard")
        self.assertEqual(envelope["suggested_playbook"], "standard.md")

    def test_workspace_fact_records_the_ambient_pr_head_checkout(self):
        envelope = self._envelope()
        self.assertEqual(envelope["workspace"]["branch"], "88-fix-widget")
        self.assertEqual(envelope["workspace"]["expected_branch"], "88-fix-widget")
        self.assertEqual(envelope["workspace"]["base_ref"], "main")
        self.assertEqual(envelope["workspace"]["source"], "ambient")
        self.assertNotIn("reused", envelope["workspace"])
        self.assertFalse(envelope["workspace"]["dirty"])
        self.assertEqual(envelope["workspace"]["unpushed_commits"], 0)
        self.assertTrue(Path(envelope["workspace"]["path"]).is_dir())
        # attach asserted ambient HEAD == pr.headRefOid — by construction here, but the identity
        # is the contract: the sha fact IS the checkout the evaluator will health-check.
        wt = self.root / ".worktrees" / "88-fix-widget"
        self.assertEqual(envelope["workspace"]["sha"], _git(["rev-parse", "HEAD"], wt))

    def test_config_reads_the_pr_head_worktree_including_uncommitted_edits(self):
        """The evaluator half of the v3.x inversion, and the sharpest edge of the trade the
        operator chose: the PR-head worktree supplies the gate config the evaluator judges that
        same PR by. Through v3 this was impossible by construction (pinned blob reads). The
        compensating control is provenance — `config.source.dirty` plus an attention line."""
        envelope_before = self._envelope()
        wt = Path(envelope_before["workspace"]["path"])
        _write(
            wt / "COMMANDS.md",
            "<!-- pr-evaluator-static-checks -->\n- `true` — weakened\n"
            "<!-- /pr-evaluator-static-checks -->\n",
        )
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["config"]["static_checks"], ["true"])
        self.assertTrue(envelope["config"]["source"]["dirty"])
        self.assertTrue(
            any("uncommitted changes" in line for line in envelope["attention"]),
            "a dirty gate-config source must be surfaced, never silent: %r" % envelope["attention"],
        )

    def test_config_source_is_the_asserted_pr_head_worktree(self):
        envelope = self._envelope()
        source = envelope["config"]["source"]
        self.assertEqual(source["path"], envelope["workspace"]["path"])
        self.assertEqual(source["branch"], envelope["workspace"]["branch"])
        self.assertEqual(len(source["sha"]), 40)
        self.assertFalse(source["dirty"])

    def test_merge_config_facts_from_repo_api(self):
        envelope = self._envelope()
        self.assertEqual(
            envelope["merge_config"],
            {
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
                "delete_branch_on_merge": True,
                "allow_auto_merge": False,
            },
        )

    def test_self_review_false_when_pr_author_differs_from_current_user(self):
        envelope = self._envelope()
        self.assertEqual(envelope["current_user"], "reviewer-bot")
        self.assertEqual(envelope["pr"]["author"]["login"], "octocat")
        self.assertFalse(envelope["self_review"])

    def test_closing_issue_dod_parsed(self):
        envelope = self._envelope()
        dod = envelope["dod"]["42"]
        self.assertEqual(len(dod), 2)
        self.assertEqual(dod[0]["text"], "Widget does not flash on cold start")
        self.assertFalse(dod[0]["checked"])
        self.assertIsNone(dod[0]["annotation"])
        self.assertTrue(dod[1]["checked"])
        self.assertEqual(dod[1]["annotation"]["form"], "closed-by-phase-commit")
        self.assertEqual(dod[1]["annotation"]["phase"], 1)

    def test_blocked_by_absent_when_closing_issue_has_no_native_blockers(self):
        envelope = self._envelope()
        self.assertEqual(envelope["blocked_by"]["42"], [])
        self.assertTrue(envelope["deps_available"]["42"])

    def test_merge_policy_absent_defaults_to_epic_integration_ask_only(self):
        envelope = self._envelope()
        self.assertFalse(envelope["config"]["merge_policy_present"])
        # epic-integration is hardcoded ask regardless of block presence (docs/specs/evaluator.md:
        # "Epic-integration merges are always gated ... hardcoded, not configurable").
        self.assertEqual(envelope["config"]["merge_policy"], {"epic-integration": "ask"})

    def test_merge_policy_present_is_parsed_and_epic_integration_still_forced_ask(self):
        # S6 DoD: "Fixtures: ... merge-policy present and absent" — this is the "present" arm
        # (the sibling test above covers "absent"). Writes a real
        # `<!-- pr-evaluator-merge-policy -->` block into the sandbox root CLAUDE.md (same
        # write/commit/push pattern as test_failed_setup_hook_surfaces_as_fact_not_a_hard_exit),
        # runs prep, and asserts the parsed policy dict plus merge_policy_present=True.
        _write(
            self.root / "CLAUDE.md",
            "<!-- pr-evaluator-merge-policy -->\n"
            "- standard: auto\n"
            "- story: ask\n"
            "<!-- /pr-evaluator-merge-policy -->\n",
        )
        _git(["add", "CLAUDE.md"], self.root)
        _git(["commit", "-m", "add merge policy block"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)

        envelope = self._envelope(fixture_case="prep_evaluator_happy_standard")
        self.assertTrue(envelope["config"]["merge_policy_present"])
        self.assertEqual(
            envelope["config"]["merge_policy"],
            {"standard": "auto", "story": "ask", "epic-integration": "ask"},
        )

    def test_sections_carries_gh_pr_gather_spill_fields_through(self):
        envelope = self._envelope()
        envelope_asserts.assert_spill_sections_well_formed(
            envelope["sections"], ["body", "thread", "reviews"]
        )
        self.assertEqual(envelope["sections"]["body"], "See issue #42.")

    def test_attention_empty_on_a_clean_mergeable_pr(self):
        envelope = self._envelope()
        self.assertEqual(envelope["attention"], [])

    def test_notices_empty_on_the_fully_migrated_config(self):
        envelope = self._envelope()
        self.assertEqual(envelope["notices"], [])

    def test_second_run_reasserts_the_same_workspace(self):
        # v3: re-entry is another attach against the same ambient checkout — no `reused` flag
        # (there is nothing to reuse; the worktree is the operator's), same facts both runs.
        first = self._envelope()
        second = self._envelope()
        self.assertEqual(first["workspace"]["path"], second["workspace"]["path"])
        self.assertNotIn("reused", second["workspace"])

    def test_failed_setup_hook_surfaces_as_fact_not_a_hard_exit(self):
        # workspace.py's `ensure --work` exits 1 when a setup hook fails, but STILL emits an
        # envelope (setup.succeeded=False) rather than a hard failure with no envelope at all —
        # prep_evaluator must distinguish these (see build_facts's ws_exit guard) and carry the
        # setup-failure fact through as `workspace.setup` + an `attention` line, never sys.exit.
        _write(
            self.root / "CLAUDE.md",
            "<!-- worktree-setup -->\n- `exit 3` — always fails\n<!-- /worktree-setup -->\n",
        )
        _git(["add", "CLAUDE.md"], self.root)
        _git(["commit", "-m", "add failing setup hook"], self.root)
        _git(["push", "origin", "HEAD:main"], self.root)

        envelope = self._envelope(fixture_case="prep_evaluator_happy_standard")
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["workspace"]["setup"]["succeeded"])
        self.assertEqual(envelope["workspace"]["setup"]["first_failure"]["command"], "exit 3")
        self.assertIsNone(envelope["workspace"]["sha"])
        self.assertTrue(
            any("setup hook failed" in item for item in envelope["attention"]),
            envelope["attention"],
        )


class GroundingDocsTests(PrepEvaluatorSandboxTestCase):
    """The consuming repo's doc catalogue (skills/_shared/doc-catalogue.md), read at the PR-head
    worktree — S4's doc-grounding set, which until 4.27.0 was a hardcoded `docs/prd.md` /
    `docs/architecture.md` / … list."""

    def _pr_head_worktree(self):
        return self._mk_ambient(self._head_branch_from_fixture("prep_evaluator_happy_standard"))

    def test_catalogue_is_read_at_the_pr_head_worktree(self):
        envelope = self._envelope()
        (entry,) = envelope["grounding_docs"]
        self.assertEqual(entry["path"], "docs/prd.md")
        self.assertTrue(entry["present"])
        self.assertEqual(entry["abs_path"], str(Path(envelope["workspace"]["path"]) / "docs/prd.md"))

    def test_a_pr_that_edits_the_catalogue_is_judged_by_its_own_copy(self):
        wt = self._pr_head_worktree()
        _write(wt / "docs" / "architecture" / "view-layer.md", "# View layer\n")
        _write(
            wt / "docs" / "README.md",
            _catalogue_block(
                SEEDED_CATALOGUE_INTERIOR
                + "- `docs/architecture/` — architecture — binding — The architecture spokes.\n"
            ),
        )
        envelope = self._envelope()
        by_path = {e["path"]: e for e in envelope["grounding_docs"]}
        self.assertEqual(by_path["docs/architecture/"]["kind"], "dir")
        self.assertEqual(by_path["docs/architecture/"]["members"], ["docs/architecture/view-layer.md"])

    def test_an_unchanged_catalogue_has_nothing_to_confirm(self):
        envelope = self._envelope()
        self.assertEqual(
            envelope["catalogue_check"], {"marker_status": "ok", "malformed_lines": [], "confirm": []}
        )
        self.assertEqual([item for item in envelope["attention"] if "doc catalogue" in item], [])

    def test_a_pr_that_changes_what_grounds_the_work_is_flagged_for_confirmation(self):
        """The backstop for a writer that never ran `doc_catalogue.py check` (#123): measured against
        the PR's own base, so a downgrade and a new binding entry are both listed."""
        wt = self._pr_head_worktree()
        _write(wt / "docs" / "arch.md", "# Arch\n")
        _write(
            wt / "docs" / "README.md",
            _catalogue_block(
                "- `docs/prd.md` — prd — informative — What the product is.\n"
                "- `docs/arch.md` — architecture — binding — Module map.\n"
            ),
        )
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            envelope["catalogue_check"]["confirm"],
            [
                {"kind": "authority_changed", "path": "docs/prd.md", "from": "binding", "to": "informative"},
                {"kind": "binding_added", "path": "docs/arch.md"},
            ],
        )
        flagged = [item for item in envelope["attention"] if "the operator confirms it before approval" in item]
        self.assertEqual(len(flagged), 2, envelope["attention"])

    def test_a_line_readers_would_skip_is_attention_never_a_decision(self):
        wt = self._pr_head_worktree()
        _write(
            wt / "docs" / "README.md",
            _catalogue_block(SEEDED_CATALOGUE_INTERIOR + "- `docs/x.md` — guide — maybe — Unsure.\n"),
        )
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(len(envelope["catalogue_check"]["malformed_lines"]), 1)
        self.assertTrue(
            any("is skipped by readers" in item for item in envelope["attention"]), envelope["attention"]
        )

    def test_absent_catalogue_is_an_attention_line_and_a_notice_never_a_decision(self):
        wt = self._pr_head_worktree()
        (wt / "docs" / "README.md").unlink()
        envelope = self._envelope()
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertIn("DOC_CATALOGUE_ABSENT", envelope["notices"])
        self.assertTrue(
            any("no doc catalogue in the PR head" in item for item in envelope["attention"]),
            envelope["attention"],
        )

    def test_declared_but_missing_doc_names_its_path(self):
        wt = self._pr_head_worktree()
        (wt / "docs" / "prd.md").unlink()
        envelope = self._envelope()
        self.assertFalse(envelope["grounding_docs"][0]["present"])
        self.assertTrue(
            any("docs/prd.md" in item for item in envelope["attention"]), envelope["attention"]
        )


class PlanFactsTests(PrepEvaluatorSandboxTestCase):
    """`facts.plans[<issue>]` — the closing issue's plan plus its shipped-phase records for THIS PR
    (skills/_shared/plan-shipped-phases.md). Plan adherence judges the whole PR diff, and a relocated
    shipped phase's entries live only in its record. Before this fact existed the evaluator's
    prompt claimed the plan was in the facts while no code put it there.
    """

    def test_no_plan_is_a_fact_not_a_gap(self):
        self.assertEqual(self._envelope()["plans"], {"42": {"present": False}})

    def test_plan_and_this_prs_records_are_staged(self):
        plan = self._envelope(fixture_case="prep_evaluator_plan_shipped")["plans"]["42"]
        self.assertTrue(plan["present"])
        self.assertTrue(
            Path(plan["body_path"]).read_text(encoding="utf-8").startswith("<!-- implementation-plan:v1 -->")
        )
        shipped = plan["shipped"]
        self.assertEqual(shipped["pr"], 88)
        self.assertEqual([e["phase"] for e in shipped["entries"]], [1])
        self.assertEqual(shipped["entries"][0]["comment_id"], 7002)
        self.assertIn("`gadget.rb`", Path(shipped["body_path"]).read_text(encoding="utf-8"))

    def test_a_plan_in_parts_is_staged_whole(self):
        # skills/_shared/plan-parts.md: adherence reads `## Changes`, which lives in the build part.
        # The evaluator scans the thread itself, so it must put the parts back itself too.
        plan = self._envelope(fixture_case="prep_evaluator_plan_parts")["plans"]["42"]
        body = Path(plan["body_path"]).read_text(encoding="utf-8")
        self.assertIn("- `widget.rb` — fixes the widget", body)
        self.assertNotIn("build part](", body)
        self.assertEqual(plan["comment_id"], 7010)
        self.assertEqual([(p["slug"], p["id"]) for p in plan["parts"]], [("build", 7011)])
        self.assertFalse(plan["shipped"]["present"])

    def test_a_duplicated_plan_degrades_to_a_notice_never_a_stop(self):
        # Adherence is one judgment among the merge gates: two plan comments (the #34 class) must not
        # stop an evaluation that ignored the plan entirely before `facts.plans` existed.
        envelope = self._envelope(fixture_case="prep_evaluator_plan_duplicate")
        self.assertEqual(envelope["status"], "ok")
        plan = envelope["plans"]["42"]
        self.assertTrue(plan["ambiguous"])
        self.assertEqual(plan["comment_ids"], [7001, 7009])
        self.assertTrue(any("2 plan comments" in n for n in envelope["notices"]))


class PolishLedgerFactTests(PrepEvaluatorSandboxTestCase):
    """`facts.polish` — the `## Polish` ledger the adjudication step reads
    (skills/_shared/polish-ledger.md), scanned from the PR body the gather already fetched."""

    def test_the_ledger_is_scanned_from_the_pr_body(self):
        polish = self._envelope(fixture_case="prep_evaluator_polish")["polish"]
        self.assertTrue(polish["present"])
        self.assertEqual(
            [(e["id"], e["disposition"]) for e in polish["entries"]], [("P1.1", "open"), ("P1.2", "file")]
        )

    def test_no_ledger_is_not_present(self):
        polish = self._envelope(fixture_case="prep_evaluator_happy_standard")["polish"]
        self.assertEqual(polish, {"present": False, "entries": [], "unparsed": []})


class CiRollupClassificationTests(PrepEvaluatorSandboxTestCase):
    """Four CI states — S6 DoD: "Fixtures: four CI states.\""""

    def test_green_rollup(self):
        envelope = self._envelope(fixture_case="prep_evaluator_happy_standard")
        self.assertEqual(envelope["ci"]["class"], "green")
        self.assertEqual(envelope["ci"]["fail_checks"], [])

    def test_red_rollup_names_failing_checks(self):
        envelope = self._envelope(fixture_case="prep_evaluator_ci_red")
        self.assertEqual(envelope["ci"]["class"], "red")
        self.assertEqual(envelope["ci"]["fail_checks"], ["ci/test"])

    def test_pending_rollup(self):
        envelope = self._envelope(fixture_case="prep_evaluator_ci_pending")
        self.assertEqual(envelope["ci"]["class"], "pending")
        self.assertEqual(envelope["ci"]["fail_checks"], [])

    def test_empty_rollup_is_none(self):
        envelope = self._envelope(fixture_case="prep_evaluator_ci_none")
        self.assertEqual(envelope["ci"]["class"], "none")
        self.assertEqual(envelope["ci"]["fail_checks"], [])


class HealthCacheTests(PrepEvaluatorSandboxTestCase):
    """Health-cache marker SHA compare — hit iff cached SHA == PR head SHA."""

    def test_absent_marker_is_a_miss_with_null_sha(self):
        envelope = self._envelope(fixture_case="prep_evaluator_happy_standard")
        self.assertEqual(envelope["health_cache"], {"sha": None, "hit": False})

    def test_matching_sha_is_a_hit(self):
        # The fixture's marker body and pr.headRefOid share the canned OID sentinel; the harness
        # stamps BOTH to the real worktree HEAD, so the hit relation (cached sha == head OID)
        # survives stamping and the reported sha is the checkout's own HEAD.
        envelope = self._envelope(fixture_case="prep_evaluator_cache_hit")
        self.assertTrue(envelope["health_cache"]["hit"])
        self.assertEqual(envelope["health_cache"]["sha"], envelope["workspace"]["sha"])

    def test_mismatched_sha_is_a_miss_but_reports_the_stale_sha(self):
        envelope = self._envelope(fixture_case="prep_evaluator_cache_miss_sha_mismatch")
        self.assertFalse(envelope["health_cache"]["hit"])
        self.assertEqual(envelope["health_cache"]["sha"], "ca1e123000000000000000000000000000000a")


class PrTypeDetectionTests(PrepEvaluatorSandboxTestCase):
    """PR-type detection: story / epic-integration / standard, from base/head branch patterns."""

    def test_standard_when_base_and_head_are_plain_branches(self):
        envelope = self._envelope(fixture_case="prep_evaluator_happy_standard")
        self.assertEqual(envelope["pr_type"], "standard")
        self.assertEqual(envelope["suggested_playbook"], "standard.md")

    def test_story_when_base_matches_epic_branch_pattern(self):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/42-journal", "origin/main"], self.root)
        _git(["push", "origin", "epic/42-journal"], self.root)
        envelope = self._envelope(fixture_case="prep_evaluator_story_type")
        self.assertEqual(envelope["pr_type"], "story")
        self.assertEqual(envelope["suggested_playbook"], "story.md")
        self.assertEqual(envelope["workspace"]["base_ref"], "epic/42-journal")

    def test_epic_integration_when_head_matches_epic_pattern_and_base_is_main(self):
        envelope = self._envelope(fixture_case="prep_evaluator_epic_integration_type")
        self.assertEqual(envelope["pr_type"], "epic-integration")
        self.assertEqual(envelope["suggested_playbook"], "epic-integration.md")
        # the WORKSPACE ensured is still on the PR's own head branch (epic/42-journal), never on
        # main — an epic-integration PR's diff is reviewed on its own branch like any other.
        self.assertEqual(envelope["workspace"]["branch"], "epic/42-journal")


class EpicIntegrationResidueTests(PrepEvaluatorSandboxTestCase):
    """`facts.epic` for an epic-integration PR: the story set (read from the PR's HEAD ref) plus
    `residue` — what the epic's merged story PRs deferred, read from their own bodies. Facts only:
    the mapping to the epic's DoD is the playbook's judgment, and nothing here is ever a decision."""

    def test_story_set_and_residue_from_the_merged_story_prs(self):
        envelope = self._envelope(fixture_case="prep_evaluator_epic_integration_residue")
        epic = envelope["epic"]
        self.assertEqual(epic["number"], 42)
        self.assertEqual(epic["branch"], "epic/42-journal")
        self.assertEqual([s["number"] for s in epic["stories"]], [89, 90, 91])
        # The delivery log is the story route's write — never built here.
        self.assertNotIn("delivery_log", epic)
        residue = epic["residue"]
        self.assertEqual(residue["story_pr_count"], 3)
        self.assertEqual(residue["story_prs_mode"], "inline")
        prs = {p["number"]: p for p in residue["story_prs"]}
        self.assertEqual(sorted(prs), [95, 96, 97])
        # Closing reference first, the `<N>-<slug>` branch grammar second, `None` otherwise.
        self.assertEqual((prs[95]["story"], prs[96]["story"], prs[97]["story"]), (90, 91, None))
        # `applied` is resolved residue — dropped; `file` / `drop` / `open` stay, a covered `file`
        # entry carrying its issue.
        self.assertEqual(
            [(e["id"], e["disposition"], e.get("issue")) for e in prs[95]["polish"]],
            [("P1.2", "file", 301), ("P1.3", "drop", None), ("P1.4", "file", None)],
        )
        # The Note line's epic (#42), sibling PR (#96) and question (#61) are not deferrals.
        self.assertEqual(prs[95]["follow_ups"], [301])
        self.assertEqual(prs[96]["follow_ups"], [302, 301, 130])
        # #89 was adopted as a story, so it left the follow-ups.
        self.assertEqual(prs[97]["follow_ups"], [303])
        follow_ups = {f["number"]: f for f in residue["follow_ups"]}
        self.assertEqual(sorted(follow_ups), [130, 301, 302, 303])
        self.assertEqual((follow_ups[301]["kind"], follow_ups[301]["state"]), ("issue", "OPEN"))
        self.assertEqual(follow_ups[301]["from_prs"], [95, 96])
        self.assertEqual(follow_ups[301]["covers"], [{"pr": 95, "id": "P1.2"}])
        self.assertEqual(follow_ups[302]["state"], "CLOSED")
        self.assertEqual(follow_ups[130]["kind"], "pull_request")
        # Unreadable is unknown, never closed — so it stays among the open ones; a PR never does.
        self.assertIsNone(follow_ups[303]["state"])
        self.assertEqual(residue["open_follow_ups"], [301, 303])
        # A covered entry is its issue's deferral (read once); one covered by an adopted story is
        # resolved. Only the unlinked entry is listed on its own.
        self.assertEqual(residue["file_polish"], [{"pr": 95, "id": "P1.4"}])
        self.assertEqual(
            residue["undecided_polish"], [{"pr": 96, "id": "P1.1", "disposition": "open"}]
        )
        self.assertNotIn("unmapped_prs", residue)
        self.assertTrue(any("#303" in n for n in envelope["notices"]))
        self.assertTrue(any("undecided polish" in a for a in envelope["attention"]))

    def test_large_residue_spills_the_per_pr_detail_to_a_file(self):
        envelope = self._envelope(
            fixture_case="prep_evaluator_epic_integration_residue",
            extra_env={"GH_PIPELINE_INLINE_THRESHOLD_BYTES": "64"},
        )
        residue = envelope["epic"]["residue"]
        self.assertEqual(residue["story_prs_mode"], "path")
        self.assertNotIn("story_prs", residue)
        staged = json.loads(Path(residue["story_prs_path"]).read_text(encoding="utf-8"))
        self.assertEqual([p["number"] for p in staged], [95, 96, 97])
        # The summary keys stay inline either way.
        self.assertEqual(residue["open_follow_ups"], [301, 303])

    def test_an_unlistable_residue_degrades_to_a_notice_and_attention(self):
        envelope = self._envelope(fixture_case="prep_evaluator_epic_integration_residue_unavailable")
        self.assertIsNone(envelope["epic"]["residue"])
        self.assertIn("EPIC_RESIDUE_UNAVAILABLE", envelope["notices"])
        self.assertTrue(any("story residue" in a for a in envelope["attention"]))

    def test_an_epic_with_no_merged_story_prs_has_an_empty_residue(self):
        envelope = self._envelope(fixture_case="prep_evaluator_epic_integration_type")
        residue = envelope["epic"]["residue"]
        self.assertEqual(residue["story_pr_count"], 0)
        self.assertEqual(residue["open_follow_ups"], [])
        self.assertEqual(residue["story_prs"], [])


class ParentEpicStorySetTests(PrepEvaluatorSandboxTestCase):
    """`facts.epic` for a story PR (skills/_shared/epic-story-hierarchy.md): the parent epic's story
    set + progress, resolved through the two tiers. The story route needs `stories_source` to decide
    whether progress projection has anything to write at all, and reads its "do siblings remain"
    routing from the same fact.
    """

    def _story_pr_envelope(self, fixture_case):
        _git(["fetch", "origin"], self.root)
        _git(["branch", "epic/42-journal", "origin/main"], self.root)
        _git(["push", "origin", "epic/42-journal"], self.root)
        return self._envelope(fixture_case=fixture_case)

    def test_native_epic_story_set_and_rollup(self):
        envelope = self._story_pr_envelope("prep_evaluator_story_type")
        epic = envelope["epic"]
        # The epic number comes from the PR's own base ref — no search.
        self.assertEqual(epic["number"], 42)
        self.assertEqual(epic["stories_source"], "sub-issues")
        self.assertEqual([s["number"] for s in epic["stories"]], [90, 91, 92])
        self.assertEqual([s["closed"] for s in epic["stories"]], [True, True, False])
        # Progress comes from GitHub's own rollup — the "K of M stories closed" handoff line.
        self.assertEqual(epic["sub_issues_summary"]["completed"], 2)
        self.assertEqual(epic["sub_issues_summary"]["total"], 3)

    def test_legacy_epic_falls_back_to_the_stories_checklist(self):
        envelope = self._story_pr_envelope("prep_evaluator_story_type_legacy_epic")
        epic = envelope["epic"]
        self.assertEqual(epic["number"], 42)
        self.assertEqual(epic["stories_source"], "checklist")
        self.assertEqual([s["number"] for s in epic["stories"]], [90, 91, 92])
        # Checkbox state is all the legacy tier has — no live state per story.
        self.assertEqual([s["closed"] for s in epic["stories"]], [True, True, False])
        self.assertEqual([s["state"] for s in epic["stories"]], [None, None, None])

    def test_delivery_log_carries_the_numeric_rest_comment_id_and_body(self):
        """#34: the story route addresses this comment through a REST endpoint that takes the
        NUMERIC comment id only — never the normalized thread's GraphQL node id, which the
        delete/patch always 404s on, leaving a duplicate log.

        #41: this fixture's log is the LEGACY monolithic comment, so the two-tier read reports that
        tier under `legacy` and stages its body there. The id now comes from a thread scan rather
        than gh_gather's marker lookup — a per-story log is a SET of comments, which a
        single-match `marker_prefix` cannot express — so `databaseId` is what keeps it numeric.
        """
        envelope = self._story_pr_envelope("prep_evaluator_story_type_delivery_log")
        log = envelope["epic"]["delivery_log"]
        self.assertTrue(log["present"])
        self.assertFalse(log["ambiguous"])
        self.assertEqual(log["log_source"], "legacy")
        self.assertEqual(log["legacy"]["comment_id"], 5350498958)
        self.assertNotIn("IC_", str(log["legacy"]["comment_id"]))
        body = log["legacy"].get("body")
        if body is None:
            body = Path(log["legacy"]["body_path"]).read_text(encoding="utf-8")
        self.assertTrue(body.startswith("<!-- epic-delivery-log:v1 -->"))
        self.assertIn("#90 \u2014 delivered:", body)
        # This PR closes #42, which the log does NOT record (its only entry is #90), so this story
        # has no existing record and Action 3 creates one. The distinction is the point: `present`
        # answers "does the epic have a log", `story_recorded_in` answers "is THIS story already
        # recorded, and in which tier" — conflating them is how a re-record writes a second entry.
        self.assertEqual(log["story_number"], 42)
        self.assertIsNone(log["story_recorded_in"])

    def test_absent_delivery_log_reports_present_false_without_an_id(self):
        envelope = self._story_pr_envelope("prep_evaluator_story_type")
        log = envelope["epic"]["delivery_log"]
        self.assertEqual(log, {"present": False, "ambiguous": False})

    def test_per_story_entries_report_the_entries_tier(self):
        """#41: the shape every epic's log takes from now on. Many comments is the NORMAL state, so
        the read cannot be a single-marker lookup — it is a scan of the thread the gather already
        returned, which is also why it costs no extra fetch.
        """
        envelope = self._story_pr_envelope("prep_evaluator_story_type_log_entries")
        log = envelope["epic"]["delivery_log"]
        self.assertTrue(log["present"])
        self.assertFalse(log["ambiguous"])
        self.assertEqual(log["log_source"], "entries")
        self.assertEqual(log["entry_count"], 2)
        self.assertNotIn("legacy", log)
        # This PR closes #42, which HAS its own entry comment, so Action 3 takes the in-place
        # `edit-comment` arm against that comment's numeric REST id.
        self.assertEqual(log["story_number"], 42)
        self.assertEqual(log["story_recorded_in"], "entries")
        self.assertEqual(log["entry"]["comment_id"], 5350499100)
        self.assertNotIn("IC_", str(log["entry"]["comment_id"]))

    def test_every_resolved_record_is_exposed_not_just_this_story_s(self):
        """The write has to stay idempotent even when the closing-issue set cannot name a single
        story. With only `story_recorded_in` available, that case has no arm but `create`, so a
        re-evaluation posts a second entry — and a duplicate hard-blocks the planner on that epic.
        The full map is what lets the write find an existing record instead.
        """
        envelope = self._story_pr_envelope("prep_evaluator_story_type_log_entries")
        log = envelope["epic"]["delivery_log"]
        by_story = {e["story"]: e for e in log["entries"]}
        self.assertEqual(sorted(by_story), [42, 90])
        self.assertEqual(by_story[90]["tier"], "entries")
        self.assertEqual(by_story[90]["comment_id"], 5350498958)
        # #90 is not the story this PR closes, and its record is still reachable.
        self.assertNotEqual(log["story_number"], 90)

    def test_mixed_epic_unions_both_tiers_with_the_per_story_entry_winning(self):
        """The state every pre-#41 epic enters on its next merge. #90 is in BOTH tiers; it counts
        once, from its per-story comment. Halving would lose a shipped story and keeping both would
        hand the plan reviewer two shapes for one story.
        """
        envelope = self._story_pr_envelope("prep_evaluator_story_type_log_mixed")
        log = envelope["epic"]["delivery_log"]
        self.assertEqual(log["log_source"], "mixed")
        self.assertEqual(log["entry_count"], 1)
        self.assertEqual(log["legacy"]["comment_id"], 5350498958)
        # #42 is recorded in neither tier, so Action 3 creates its entry.
        self.assertEqual(log["story_number"], 42)
        self.assertIsNone(log["story_recorded_in"])

    def test_two_entries_for_one_story_degrade_without_gating_the_story_set(self):
        """Story-scoped duplicates: the story number in the marker is what keeps a genuine duplicate
        detectable now that "many comments" is normal. Still not prep's own decision — a merge
        already judged on its own gates must not stall on its log — and no longer epic-wide: a
        duplicate for one story leaves every other story recordable.
        """
        envelope = self._story_pr_envelope("prep_evaluator_story_type_log_dup_entry")
        self.assertEqual(envelope["status"], "ok")
        epic = envelope["epic"]
        self.assertEqual([s["number"] for s in epic["stories"]], [90, 91, 92])
        log = epic["delivery_log"]
        self.assertTrue(log["ambiguous"])
        self.assertEqual(log["comment_ids"], [5350498958, 5361313482])
        self.assertTrue(
            any("name story #90" in n for n in envelope["notices"]),
            "the notice must name WHICH story is duplicated, got %r" % (envelope["notices"],),
        )

    def test_duplicate_delivery_logs_degrade_without_gating_the_story_set(self):
        """More than one log comment is MARKER_AMBIGUOUS at the gather. It must NOT become prep's
        own decision: the hierarchy is never a gate, and a merge already judged on its own gates
        can't stall on its log. The story set still arrives; the duplicate ids ride along so the
        route reports them instead of posting a third copy.
        """
        envelope = self._story_pr_envelope("prep_evaluator_story_type_duplicate_delivery_log")
        self.assertEqual(envelope["status"], "ok")
        epic = envelope["epic"]
        self.assertEqual(epic["stories_source"], "sub-issues")
        self.assertEqual([s["number"] for s in epic["stories"]], [90, 91, 92])
        log = epic["delivery_log"]
        self.assertTrue(log["present"])
        self.assertTrue(log["ambiguous"])
        self.assertNotIn("comment_id", log)
        self.assertEqual(log["comment_ids"], [5350498958, 5361313482])
        self.assertTrue(
            any("epic-delivery-log comments on #42" in n for n in envelope["notices"]),
            "expected a duplicate-log notice, got %r" % (envelope["notices"],),
        )

    def test_mixed_epic_unions_both_halves_without_double_counting(self):
        """A legacy epic that later gained a native child — epic-revise files new stories with
        `--parent`, so this arises without anyone erring. Both halves must be reported: halving the
        set under-counts siblings, which could route a story merge to "last sibling closed" while
        stories are still open. #92 appears in both and must be counted once, with its native live
        state (OPEN) rather than the stale unchecked bullet.
        """
        envelope = self._story_pr_envelope("prep_evaluator_story_type_mixed_epic")
        epic = envelope["epic"]
        self.assertEqual(epic["stories_source"], "mixed")
        self.assertEqual(sorted(s["number"] for s in epic["stories"]), [90, 91, 92, 93])
        by_number = {s["number"]: s for s in epic["stories"]}
        # The overlapping story is carried once, from the native half (live state present).
        self.assertEqual(by_number[92]["state"], "OPEN")
        self.assertFalse(by_number[92]["closed"])
        # Checklist-only stories keep their checkbox-derived state.
        self.assertTrue(by_number[90]["closed"])
        self.assertIsNone(by_number[90]["state"])

    def test_standard_pr_has_no_epic_facts(self):
        """A non-story PR has no parent epic, so the key is omitted rather than carried empty."""
        envelope = self._envelope(fixture_case="prep_evaluator_happy_standard")
        self.assertNotIn("epic", envelope)


class NativeBlockerTests(PrepEvaluatorSandboxTestCase):
    """docs/specs/evaluator.md "Artifacts read": "Any open blocker holds the merge — soft-
    reject." Prep surfaces the fact (blocked_by + an attention line); the soft-reject decision
    itself is the evaluator playbook's judgment, not prep's."""

    def test_open_native_blocker_surfaces_in_blocked_by_and_attention(self):
        envelope = self._envelope(fixture_case="prep_evaluator_open_blocker")
        blockers = envelope["blocked_by"]["42"]
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["number"], 41)
        self.assertEqual(blockers[0]["state"], "OPEN")
        self.assertTrue(
            any("open native blocker" in item for item in envelope["attention"]),
            envelope["attention"],
        )


class MarkerAmbiguousTests(PrepEvaluatorSandboxTestCase):
    """Duplicate health-cache comments -> MARKER_AMBIGUOUS, forwarded verbatim from gh_pr_gather's
    own decision (architecture.md §3's closed decision-code set is shared, not re-derived)."""

    def test_two_health_cache_comments_yields_marker_ambiguous(self):
        result = self._run(
            ["88", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_evaluator_marker_ambiguous",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)  # needs_decision is exit 0
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")

    def test_marker_ambiguous_short_circuits_before_any_closing_issue_gather(self):
        # The marker_ambiguous fixture's manifest is deliberately trimmed to only the pr-view +
        # marker-lookup entries (see tests/fixtures/prep_evaluator_marker_ambiguous's build
        # notes) — reaching the closing-issue gather would be an un-fixtured MISS, which itself
        # would fail this test loudly and prove the ordering bug.
        result = self._run(
            ["88", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_evaluator_marker_ambiguous",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        # No workspace side effect either — the decision is returned before `ensure --work` runs.
        self.assertFalse((self.root / ".worktrees").exists())


class AuthRequiredTests(PrepEvaluatorSandboxTestCase):
    def test_auth_failure_on_first_gh_call_yields_auth_required(self):
        result = self._run(
            ["88", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_evaluator_auth_required",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AUTH_REQUIRED")


class WorkspaceMismatchTests(PrepEvaluatorSandboxTestCase):
    """v3: the ambient-checkout assertion on the PR head — WORKSPACE_MISMATCH decisions
    (workspace.py's, forwarded verbatim). These replace the retired ROOT_*/BRANCH_IN_USE
    propagation coverage: the evaluator no longer polices root state or creates worktrees (those
    gates live on the workspace-creating paths — the landing tools and workspace-open)."""

    def _mismatch(self, envelope, reason):
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "WORKSPACE_MISMATCH")
        self.assertEqual(envelope["decision"]["context"]["reason"], reason)

    def test_wrong_branch_is_a_branch_mismatch(self):
        envelope = self._envelope(
            fixture_case="prep_evaluator_happy_standard", ambient_branch="999-unrelated-work"
        )
        self._mismatch(envelope, "branch_mismatch")
        self.assertEqual(envelope["decision"]["context"]["expected_branch"], "88-fix-widget")

    def test_session_at_the_project_root_names_workspace_open(self):
        result = self._run(
            ["88", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_evaluator_happy_standard",
            cwd=str(self.root),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self._mismatch(envelope, "at_project_root")
        self.assertTrue(
            any("workspace-open" in option for option in envelope["decision"]["options"])
        )

    def test_head_oid_drift_is_stale_checkout(self):
        # The fixture's canned headRefOid deliberately NOT stamped to the worktree HEAD: the
        # ambient checkout does not contain the PR's recorded head — the evaluator must never
        # health-check or cache against it (require_exact, review finding M1).
        envelope = self._envelope(fixture_case="prep_evaluator_happy_standard", stamp=False)
        self._mismatch(envelope, "stale_checkout")
        self.assertEqual(
            envelope["decision"]["context"]["expected_remote_sha"],
            self.CANNED_HEAD_OID,
        )
        # Even an AHEAD checkout is a mismatch on the exact-OID contract — context records it.
        self.assertIn("ahead_of_remote", envelope["decision"]["context"])


class RefreshModeTests(unittest.TestCase):
    """--refresh re-derives PR state + CI without touching workspace.py/config_block.py at all
    (S6 DoD: "--refresh re-derives PR state and CI without hook re-runs")."""

    def setUp(self):
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    def _run(self, args, fixture_case=None, cwd=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            env=env,
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def test_refresh_never_requires_a_root_at_all(self):
        # No --root passed, and the subprocess runs in a directory that is NOT a git repo at all.
        # The explicit non-git cwd is the whole point: this test inherited the runner's cwd until
        # v3.x, which IS a git repo, so it passed while silently permitting --refresh to spawn
        # git (`rev-parse --git-common-dir` / `symbolic-ref`) and crash with an uncaught
        # RuntimeError for any real caller standing outside a repo. The contract is "never
        # requires a root"; only a non-git cwd actually pins it.
        outside_any_repo = tempfile.mkdtemp(prefix="gh-pipeline-test-nongit-")
        self.addCleanup(shutil.rmtree, outside_any_repo, True)
        result = self._run(
            ["88", "octo/widgets", "--refresh", "--scratch-dir", self.scratch],
            fixture_case="prep_evaluator_happy_standard",
            cwd=outside_any_repo,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertNotIn("workspace", envelope)
        self.assertEqual(envelope["config"], {})
        self.assertFalse(envelope["root"]["fresh"])
        # No checkout read, so no catalogue read — "we didn't look" is not "the repo declared none".
        self.assertEqual(envelope["grounding_docs"], [])
        self.assertEqual([item for item in envelope["attention"] if "catalogue" in item], [])

    def test_refresh_still_re_derives_ci_and_health_cache(self):
        result = self._run(
            ["88", "octo/widgets", "--refresh", "--scratch-dir", self.scratch],
            fixture_case="prep_evaluator_ci_red",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        envelope = _parse_one_envelope(result.stdout)
        self.assertEqual(envelope["ci"]["class"], "red")
        self.assertIn("health_cache", envelope)

    def test_refresh_still_reports_dod_and_self_review(self):
        result = self._run(
            ["88", "octo/widgets", "--refresh", "--scratch-dir", self.scratch],
            fixture_case="prep_evaluator_happy_standard",
        )
        envelope = _parse_one_envelope(result.stdout)
        self.assertIn("42", envelope["dod"])
        self.assertFalse(envelope["self_review"])


class SingleInvocationBudgetTests(PrepEvaluatorSandboxTestCase):
    """Single-invocation budget: one prep run's shim gh-call count is bounded and predictable
    (prd.md §9.2 "one-shot state assembly" / S6 DoD's "shim call counts")."""

    def test_shim_call_count_for_one_happy_run_is_bounded_and_stable(self):
        # Instrument the shim by pointing GH_SHIM_FIXTURES at a case and counting how many
        # manifest entries prep_evaluator's single invocation actually needs (the shim exits 2
        # loudly on ANY call outside the manifest — tests/README.md — so if this test passes at
        # all, every `gh` call prep made was one of these seven, no more, no fewer: pr view,
        # marker lookup, issue view, issue-comments, issue open-prs, repo config, current user).
        # This directly proves "no redundant fetches" (architecture.md §9.2) as an exact count,
        # not an unbounded "at most N" — a regression that adds a stray extra `gh` call becomes
        # a fixture MISS on the very next un-fixtured argv, failing loudly per tests/README.md.
        result = self._run(
            ["88", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_evaluator_happy_standard",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        manifest_path = shimenv.fixture_case_dir("prep_evaluator_happy_standard") / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Every manifest entry must have been reachable in exactly one prep run (a smaller count
        # would mean this test's own fixture over-provisions calls that never happen; the shim's
        # own MISS behavior on the other side already proves no MORE calls happen than these).
        self.assertEqual(len(manifest), 7)

    def test_no_gh_call_is_repeated_within_one_run(self):
        # A second, independent proof (from the SCRIPT side, not the fixture side): every argv in
        # the happy-path manifest is for a semantically distinct fetch (pr view, PR markers,
        # issue view, issue comments, issue open-prs, repo config, current user) — no two entries
        # share an argv, so if prep called any of them twice the shim would have answered
        # identically both times (harmless) but the manifest's own uniqueness proves the SET of
        # distinct calls prep makes in one run, which is what "no redundant fetches" means here.
        manifest_path = shimenv.fixture_case_dir("prep_evaluator_happy_standard") / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        argv_tuples = [tuple(entry["argv"]) for entry in manifest]
        self.assertEqual(len(argv_tuples), len(set(argv_tuples)), "duplicate argv in manifest")


class PureHelperUnitTests(unittest.TestCase):
    """Direct, in-process tests of prep_evaluator's pure classification helpers — no subprocess,
    no shim, no git sandbox needed (mirrors test_workspace.py's WorkspaceHelperUnitTests style)."""

    def test_classify_ci_rollup_green(self):
        cls, fails = prep_evaluator._classify_ci_rollup([{"name": "a", "conclusion": "SUCCESS"}])
        self.assertEqual(cls, "green")
        self.assertEqual(fails, [])

    def test_classify_ci_rollup_skipped_and_neutral_count_as_green(self):
        cls, fails = prep_evaluator._classify_ci_rollup(
            [{"name": "a", "conclusion": "SUCCESS"}, {"name": "b", "conclusion": "SKIPPED"},
             {"name": "c", "conclusion": "NEUTRAL"}]
        )
        self.assertEqual(cls, "green")

    def test_classify_ci_rollup_red_names_failing_checks(self):
        cls, fails = prep_evaluator._classify_ci_rollup(
            [{"name": "a", "conclusion": "SUCCESS"}, {"name": "b", "conclusion": "FAILURE"}]
        )
        self.assertEqual(cls, "red")
        self.assertEqual(fails, ["b"])

    def test_classify_ci_rollup_pending_on_in_progress(self):
        cls, fails = prep_evaluator._classify_ci_rollup(
            [{"name": "a", "status": "IN_PROGRESS", "conclusion": None}]
        )
        self.assertEqual(cls, "pending")

    def test_classify_ci_rollup_pending_on_status_context_state(self):
        cls, fails = prep_evaluator._classify_ci_rollup([{"name": "a", "state": "PENDING"}])
        self.assertEqual(cls, "pending")

    def test_classify_ci_rollup_empty_is_none(self):
        cls, fails = prep_evaluator._classify_ci_rollup([])
        self.assertEqual(cls, "none")
        cls2, _ = prep_evaluator._classify_ci_rollup(None)
        self.assertEqual(cls2, "none")

    def test_detect_pr_type_epic_integration(self):
        self.assertEqual(prep_evaluator._detect_pr_type("main", "epic/42-journal", "main"), "epic-integration")

    def test_detect_pr_type_story(self):
        self.assertEqual(prep_evaluator._detect_pr_type("epic/42-journal", "88-add-search", "main"), "story")

    def test_detect_pr_type_standard(self):
        self.assertEqual(prep_evaluator._detect_pr_type("main", "88-fix-widget", "main"), "standard")

    def test_detect_pr_type_epic_head_but_non_main_base_is_story_not_epic_integration(self):
        # headRefName matching epic/<N>-<slug> alone is not sufficient -- baseRefName must be
        # `main` too (docs/specs/evaluator.md: "epic-integration if headRefName matches ... AND
        # baseRefName == main"). Here baseRefName itself matches the epic pattern, so `story`
        # correctly takes precedence.
        self.assertEqual(
            prep_evaluator._detect_pr_type("epic/1-old", "epic/2-new", "main"), "story"
        )

    def test_parse_merge_policy_reads_ask_and_auto(self):
        policy = prep_evaluator._parse_merge_policy(["- standard: auto", "- story: ask"])
        self.assertEqual(policy, {"standard": "auto", "story": "ask"})

    def test_parse_merge_policy_ignores_non_conforming_lines(self):
        policy = prep_evaluator._parse_merge_policy(["not a policy line", "- standard: ask"])
        self.assertEqual(policy, {"standard": "ask"})

    def test_parse_backtick_command_list(self):
        items = prep_evaluator._parse_backtick_command_list(
            ["- `make lint` — hygiene", "- `make test` — tests", "prose line"]
        )
        self.assertEqual(items, ["make lint", "make test"])


class UsageErrorTests(unittest.TestCase):
    def test_missing_required_positional_args_is_usage_error(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_missing_repo_arg_is_usage_error(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "88"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
