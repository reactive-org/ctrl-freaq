"""Unit tests for scripts/prep_setup.py — the setup skill's complete starting-state facts block in
one call (architecture.md §4, §9.2; docs/implementation.md S17; docs/specs/setup.md).

Test topology: prep_setup makes **no** `gh` call (setup's subject is local Markdown files, not GitHub
state), so these tests need no offline `gh` shim at all — they exercise the full inventory against a
real temp git origin+clone (`tests/support/gitsandbox`) with `COMMANDS.md`/`CLAUDE.md` written into
the clone. The prep is driven both in-process (`build_facts`) and as a real subprocess so the full
argv-in / envelope-out path a real caller uses is covered, plus a non-git-repo target (a plain temp
dir) for the `git_repo: false` / `root.sha: null` degradation.

Coverage matrix (S17 DoD):
- Marker classification ×4: present / legacy / malformed (dup) / missing.
- Legacy `pr-evaluator-health-checks` detection (box 4's inventory half — the split mechanics are in
  test_setup_routing.py).
- The user-owned `claude-code-stack-profile` interior staged for re-ingest (`base_path`).
- Same-marker-in-both-files ambiguity + the split target-file suggestion.
- Preflight tool presence (`shutil.which`) + the git-repo/`root.sha` facts (+ non-repo degradation).
- Envelope conformance on the (only, no-decision) emitting path.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "prep_setup.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_setup  # noqa: E402  (import after sys.path setup, by necessity)
import review_guidance  # noqa: E402
from tests.support import envelope_asserts, gitsandbox  # noqa: E402


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _marker_class(facts, name):
    for entry in facts["inventory"]["known_markers"]:
        if entry["name"] == name:
            return entry["class"]
    raise AssertionError("marker %r not in known_markers" % name)


class PrepSetupSandboxTestCase(unittest.TestCase):
    """A real temp git origin+clone as `--root` — a real repo with a commit is needed for
    `git rev-parse --is-inside-work-tree` (`preflight.git_repo`) and `git rev-parse HEAD` (`root.sha`)."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        self.scratch = Path(tempfile.mkdtemp(prefix="gh-setup-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.scratch, ignore_errors=True))

    def _facts(self):
        return prep_setup.build_facts(root=str(self.root), scratch_dir=str(self.scratch))

    # ---- preflight / root ----

    def test_preflight_reports_git_repo_and_tool_presence(self):
        facts = self._facts()
        self.assertTrue(facts["preflight"]["git_repo"])
        # git is definitely present in the test env; jq/gh presence is env-dependent but must be bool.
        self.assertTrue(facts["preflight"]["tools"]["git"])
        for tool in ("jq", "git", "gh"):
            self.assertIsInstance(facts["preflight"]["tools"][tool], bool)

    def test_root_sha_is_recorded(self):
        facts = self._facts()
        self.assertEqual(facts["root"]["path"], str(self.root.resolve()))
        self.assertRegex(facts["root"]["sha"] or "", r"^[0-9a-f]{40}$")

    # ---- classification ----

    def test_greenfield_all_markers_missing_default_new_target(self):
        facts = self._facts()
        for entry in facts["inventory"]["known_markers"]:
            self.assertEqual(entry["class"], "missing", "%r should be missing" % entry["name"])
        tf = facts["target_file"]
        self.assertEqual(tf["suggested"], "COMMANDS.md")
        self.assertEqual(tf["reason"], "default-new")
        self.assertFalse(tf["exists"])
        self.assertFalse(tf["split"])

    def test_present_block_classified_present_and_target_existing(self):
        _write(
            self.root / "COMMANDS.md",
            "<!-- issue-resolver-fast-checks -->\n- `make lint` — lint\n"
            "<!-- /issue-resolver-fast-checks -->\n",
        )
        facts = self._facts()
        self.assertEqual(_marker_class(facts, "issue-resolver-fast-checks"), "present")
        entry = next(
            e for e in facts["inventory"]["known_markers"] if e["name"] == "issue-resolver-fast-checks"
        )
        self.assertEqual(entry["files"], ["COMMANDS.md"])
        self.assertEqual(facts["target_file"]["suggested"], "COMMANDS.md")
        self.assertEqual(facts["target_file"]["reason"], "existing-config")

    def test_sensitive_paths_block_classified_present_and_missing(self):
        # #96: the tenth machine-parsed block is inventoried like the other nine.
        self.assertEqual(_marker_class(self._facts(), "pr-evaluator-sensitive-paths"), "missing")
        _write(
            self.root / "CLAUDE.md",
            "<!-- pr-evaluator-sensitive-paths -->\n- `.github/workflows/` — CI definitions\n"
            "<!-- /pr-evaluator-sensitive-paths -->\n",
        )
        self.assertEqual(_marker_class(self._facts(), "pr-evaluator-sensitive-paths"), "present")

    def test_legacy_health_checks_detected(self):
        _write(
            self.root / "COMMANDS.md",
            "<!-- pr-evaluator-health-checks -->\n- `make lint` — lint\n- `make test` — tests\n"
            "<!-- /pr-evaluator-health-checks -->\n",
        )
        facts = self._facts()
        legacy = facts["inventory"]["legacy_health_checks"]
        self.assertTrue(legacy["present"])
        self.assertEqual(legacy["files"], ["COMMANDS.md"])
        self.assertTrue(
            any("legacy `pr-evaluator-health-checks`" in a for a in facts["attention"]),
            facts["attention"],
        )

    def test_malformed_dup_block_classified_malformed_and_surfaced(self):
        _write(
            self.root / "COMMANDS.md",
            "<!-- issue-resolver-fast-checks -->\n- `a` — a\n<!-- /issue-resolver-fast-checks -->\n\n"
            "<!-- issue-resolver-fast-checks -->\n- `b` — b\n<!-- /issue-resolver-fast-checks -->\n",
        )
        facts = self._facts()
        self.assertEqual(_marker_class(facts, "issue-resolver-fast-checks"), "malformed")
        self.assertTrue(
            any("malformed config block" in a for a in facts["attention"]), facts["attention"]
        )

    # ---- stack profile (user-owned re-ingest base) ----

    def test_stack_profile_interior_staged_for_reingest(self):
        interior = "## Running this stack with Claude Code\n\n- Background slow commands.\n"
        _write(
            self.root / "CLAUDE.md",
            "<!-- claude-code-stack-profile -->\n%s<!-- /claude-code-stack-profile -->\n" % interior,
        )
        facts = self._facts()
        sp = facts["inventory"]["stack_profile"]
        self.assertTrue(sp["present"])
        self.assertEqual(sp["files"], ["CLAUDE.md"])
        self.assertTrue(sp["interior_present"])
        base = Path(sp["base_path"])
        self.assertTrue(base.is_file())
        self.assertIn("Background slow commands.", base.read_text(encoding="utf-8"))

    def test_stack_profile_absent_reports_not_present(self):
        facts = self._facts()
        self.assertFalse(facts["inventory"]["stack_profile"]["present"])
        self.assertNotIn("base_path", facts["inventory"]["stack_profile"])

    # ---- review guidance (#99: user-owned, in the config file, never a machine-parsed block) ----

    def test_review_guidance_interior_staged_for_reingest_without_moving_target_file(self):
        interior = "## Review guidance\n**Skip:** `vendor/`\n**Recurring findings:**\n"
        _write(
            self.root / "CLAUDE.md",
            "<!-- issue-resolver-review-guidance -->\n%s<!-- /issue-resolver-review-guidance -->\n" % interior,
        )
        baseline = self._facts()
        rg = baseline["inventory"]["review_guidance"]
        self.assertTrue(rg["present"])
        self.assertEqual(rg["files"], ["CLAUDE.md"])
        self.assertEqual(rg["statuses"], {"CLAUDE.md": "ok"})
        self.assertEqual(rg["source_file"], "CLAUDE.md")
        self.assertTrue(rg["interior_present"])
        base = Path(rg["base_path"])
        self.assertEqual(base.name, "issue-resolver-review-guidance.base.md")
        self.assertEqual(base.read_text(encoding="utf-8"), interior)
        # The block is user-owned prose: it never joins the reconciled set, never counts toward the
        # target-file decision (a CLAUDE.md-only guidance block must not pull config into CLAUDE.md).
        self.assertNotIn(
            "issue-resolver-review-guidance", [m["name"] for m in baseline["inventory"]["known_markers"]]
        )
        self.assertEqual(baseline["target_file"]["suggested"], "COMMANDS.md")
        self.assertEqual(baseline["target_file"]["reason"], "default-new")
        self.assertEqual(baseline["target_file"]["config_marker_counts"], {"COMMANDS.md": 0, "CLAUDE.md": 0})
        self.assertNotIn("issue-resolver-review-guidance", prep_setup.MACHINE_PARSED_MARKERS)

    def test_review_guidance_absent_reports_not_present(self):
        rg = self._facts()["inventory"]["review_guidance"]
        self.assertEqual(rg, {"present": False, "files": [], "statuses": {}})

    def test_review_guidance_malformed_is_not_present_and_surfaced(self):
        _write(
            self.root / "COMMANDS.md",
            "<!-- issue-resolver-review-guidance -->\n- a\n<!-- /issue-resolver-review-guidance -->\n"
            "<!-- issue-resolver-review-guidance -->\n- b\n<!-- /issue-resolver-review-guidance -->\n",
        )
        facts = self._facts()
        rg = facts["inventory"]["review_guidance"]
        self.assertFalse(rg["present"])
        self.assertNotIn("base_path", rg)
        self.assertEqual(rg["statuses"], {"COMMANDS.md": "dup"})
        self.assertTrue(
            any("malformed `issue-resolver-review-guidance` block" in a for a in facts["attention"]),
            facts["attention"],
        )
        # Positive control: a well-formed block raises no such line.
        _write(
            self.root / "COMMANDS.md",
            "<!-- issue-resolver-review-guidance -->\n- a\n<!-- /issue-resolver-review-guidance -->\n",
        )
        self.assertFalse(
            any("issue-resolver-review-guidance" in a for a in self._facts()["attention"])
        )

    def test_review_guidance_in_both_files_bases_on_the_copy_the_resolver_reads(self):
        # The resolver's `review_guidance.read` takes COMMANDS.md first; setup's re-ingest base must be
        # that same copy, or a re-run would layer proposals onto text the reviewer never sees.
        guidance = "<!-- issue-resolver-review-guidance -->\n- %s\n<!-- /issue-resolver-review-guidance -->\n"
        _write(self.root / "COMMANDS.md", guidance % "from commands")
        _write(self.root / "CLAUDE.md", guidance % "from claude")
        facts = self._facts()
        rg = facts["inventory"]["review_guidance"]
        self.assertTrue(rg["present"])
        self.assertEqual(rg["source_file"], "COMMANDS.md")
        self.assertEqual(Path(rg["base_path"]).read_text(encoding="utf-8"), "- from commands\n")
        self.assertEqual(facts["inventory"]["same_marker_both_files"], ["issue-resolver-review-guidance"])
        both = [a for a in facts["attention"] if "issue-resolver-review-guidance" in a]
        self.assertEqual(len(both), 1, both)
        self.assertIn("declared in BOTH candidate files", both[0])
        self.assertEqual(review_guidance.read(str(self.root))["text"], "- from commands")

    def test_review_guidance_malformed_in_one_file_bases_on_the_well_formed_copy(self):
        _write(self.root / "COMMANDS.md", "<!-- issue-resolver-review-guidance -->\n- open\n")
        _write(
            self.root / "CLAUDE.md",
            "<!-- issue-resolver-review-guidance -->\n- good\n<!-- /issue-resolver-review-guidance -->\n",
        )
        facts = self._facts()
        rg = facts["inventory"]["review_guidance"]
        self.assertTrue(rg["present"])
        self.assertEqual(rg["source_file"], "CLAUDE.md")
        self.assertEqual(Path(rg["base_path"]).read_text(encoding="utf-8"), "- good\n")
        self.assertTrue(
            any("malformed `issue-resolver-review-guidance` block" in a and "COMMANDS.md" in a
                and "well-formed copy in `CLAUDE.md`" in a for a in facts["attention"]),
            facts["attention"],
        )

    def _guidance(self, interior):
        return "<!-- issue-resolver-review-guidance -->\n%s<!-- /issue-resolver-review-guidance -->\n" % interior

    def test_review_guidance_in_an_included_file_is_the_base(self):
        # The resolver discovers a block in an `@`-included file; setup must base on that same copy,
        # or it would seed a new block in a candidate file that shadows the owner's text.
        _write(self.root / "CLAUDE.md", "# Project\n\nSee @docs/guidance.md for review guidance.\n")
        _write(self.root / "docs" / "guidance.md", self._guidance("- from the include\n"))
        rg = self._facts()["inventory"]["review_guidance"]
        self.assertTrue(rg["present"])
        self.assertEqual(rg["source_file"], "docs/guidance.md")
        self.assertTrue(rg["interior_present"])
        self.assertEqual(Path(rg["base_path"]).read_text(encoding="utf-8"), "- from the include\n")
        self.assertEqual((rg["files"], rg["statuses"]), ([], {}))
        self.assertEqual(str(self.root.resolve() / rg["source_file"]), review_guidance.read(str(self.root))["source"])

    def test_review_guidance_include_outside_the_root_is_reported_absolute(self):
        outside = Path(tempfile.mkdtemp(prefix="gh-setup-outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        _write(outside / "guidance.md", self._guidance("- shared\n"))
        rel = os.path.relpath(outside / "guidance.md", self.root)
        self.assertTrue(rel.startswith(".."))
        _write(self.root / "CLAUDE.md", "See @%s\n" % rel)
        rg = self._facts()["inventory"]["review_guidance"]
        self.assertTrue(rg["present"])
        self.assertTrue(os.path.isabs(rg["source_file"]), rg["source_file"])
        self.assertNotIn("..", rg["source_file"])

    def test_review_guidance_candidate_file_wins_over_an_include(self):
        _write(self.root / "COMMANDS.md", self._guidance("- from commands\n"))
        _write(self.root / "CLAUDE.md", "See @docs/guidance.md\n")
        _write(self.root / "docs" / "guidance.md", self._guidance("- from the include\n"))
        rg = self._facts()["inventory"]["review_guidance"]
        self.assertEqual(rg["source_file"], "COMMANDS.md")
        self.assertEqual(Path(rg["base_path"]).read_text(encoding="utf-8"), "- from commands\n")

    def test_review_guidance_empty_first_copy_is_present_without_text(self):
        # An empty well-formed copy still wins discovery, so the reviewer gets no guidance; setup's
        # facts say the same thing the reader does.
        _write(self.root / "COMMANDS.md", self._guidance(""))
        _write(self.root / "CLAUDE.md", self._guidance("- shadowed\n"))
        rg = self._facts()["inventory"]["review_guidance"]
        self.assertTrue(rg["present"])
        self.assertEqual(rg["source_file"], "COMMANDS.md")
        self.assertFalse(rg["interior_present"])
        self.assertFalse(review_guidance.read(str(self.root))["present"])

    def test_review_guidance_whitespace_only_interior_has_no_text(self):
        _write(self.root / "COMMANDS.md", self._guidance("\n   \n"))
        rg = self._facts()["inventory"]["review_guidance"]
        self.assertTrue(rg["present"])
        self.assertFalse(rg["interior_present"])
        self.assertFalse(review_guidance.read(str(self.root))["present"])

    def test_review_guidance_malformed_beside_an_empty_copy_says_no_guidance(self):
        _write(self.root / "COMMANDS.md", self._guidance(""))
        _write(self.root / "CLAUDE.md", "<!-- issue-resolver-review-guidance -->\n- open\n")
        facts = self._facts()
        lines = [a for a in facts["attention"] if "malformed `issue-resolver-review-guidance`" in a]
        self.assertEqual(len(lines), 1, facts["attention"])
        self.assertIn("reads no repo guidance (the well-formed copy in `COMMANDS.md` is empty", lines[0])

    # ---- doc catalogue (user-owned, docs/README.md-only, parallel read) ----

    CATALOGUE_INTERIOR = "- `docs/prd.md` — prd — binding — What the product is.\n"

    def _write_catalogue(self, interior=None):
        body = self.CATALOGUE_INTERIOR if interior is None else interior
        _write(
            self.root / "docs" / "README.md",
            "# Docs\n\n<!-- doc-catalogue -->\n%s<!-- /doc-catalogue -->\n" % body,
        )

    def test_doc_catalogue_absent_readme_reports_skip(self):
        facts = self._facts()
        dc = facts["inventory"]["doc_catalogue"]
        self.assertFalse(dc["readme_present"])
        self.assertFalse(dc["present"])
        self.assertIsNone(dc["status"])
        self.assertNotIn("readme_path", dc)
        self.assertNotIn("base_path", dc)
        self.assertTrue(
            any("no `docs/README.md`" in item for item in facts["attention"]), facts["attention"]
        )

    def test_doc_catalogue_readme_without_the_block_stages_the_readme_only(self):
        """The derive-and-propose path: the sub-agent's only derivation source is the index, so the
        index is staged even though there is no base to re-ingest."""
        _write(self.root / "docs" / "README.md", "# Docs\n\nThe PRD lives at `docs/prd.md`.\n")
        facts = self._facts()
        dc = facts["inventory"]["doc_catalogue"]
        self.assertTrue(dc["readme_present"])
        self.assertFalse(dc["present"])
        self.assertNotIn("base_path", dc)
        staged = Path(dc["readme_path"])
        self.assertTrue(staged.is_file())
        self.assertIn("The PRD lives at", staged.read_text(encoding="utf-8"))
        self.assertTrue(
            any("carries no `doc-catalogue` block" in item for item in facts["attention"]),
            facts["attention"],
        )

    def test_doc_catalogue_interior_staged_for_reingest(self):
        self._write_catalogue()
        facts = self._facts()
        dc = facts["inventory"]["doc_catalogue"]
        self.assertTrue(dc["present"])
        self.assertEqual(dc["status"], "ok")
        self.assertTrue(dc["interior_present"])
        base = Path(dc["base_path"])
        self.assertTrue(base.is_file())
        self.assertEqual(base.read_text(encoding="utf-8"), self.CATALOGUE_INTERIOR)
        self.assertEqual([i for i in facts["attention"] if "catalogue" in i], [])

    def test_doc_catalogue_malformed_is_surfaced_and_never_staged_as_a_base(self):
        """A malformed block is fixed by hand, never guessed at — and readers treat it as absent, so
        the attention line has to say grounding is currently off."""
        _write(
            self.root / "docs" / "README.md",
            "<!-- doc-catalogue -->\n%s" % self.CATALOGUE_INTERIOR,  # unterminated
        )
        facts = self._facts()
        dc = facts["inventory"]["doc_catalogue"]
        self.assertFalse(dc["present"])
        self.assertEqual(dc["status"], "open")
        self.assertNotIn("base_path", dc)
        self.assertTrue(
            any("malformed `doc-catalogue`" in item for item in facts["attention"]),
            facts["attention"],
        )

    def test_docs_readme_never_affects_the_target_file_decision(self):
        """The parallel-read decision: the catalogue must not count as pipeline config, or it would
        skew which file setup writes the real config blocks into."""
        _write(self.root / "COMMANDS.md", "<!-- pr-evaluator-static-checks -->\n- `make lint` — lint\n<!-- /pr-evaluator-static-checks -->\n")
        self._write_catalogue()
        facts = self._facts()
        self.assertEqual(facts["target_file"]["suggested"], "COMMANDS.md")
        self.assertEqual(facts["target_file"]["config_marker_counts"], {"COMMANDS.md": 1, "CLAUDE.md": 0})
        self.assertNotIn(
            "doc-catalogue", [m["name"] for m in facts["inventory"]["known_markers"]]
        )

    # ---- same-marker-both-files / split ----

    def test_same_marker_in_both_files_flagged(self):
        block = (
            "<!-- pr-evaluator-static-checks -->\n- `make lint` — lint\n"
            "<!-- /pr-evaluator-static-checks -->\n"
        )
        _write(self.root / "COMMANDS.md", block)
        _write(self.root / "CLAUDE.md", block)
        facts = self._facts()
        self.assertIn("pr-evaluator-static-checks", facts["inventory"]["same_marker_both_files"])
        self.assertTrue(facts["target_file"]["split"])
        self.assertTrue(
            any("declared in BOTH" in a for a in facts["attention"]), facts["attention"]
        )

    # ---- envelope conformance (subprocess) ----

    def test_subprocess_emits_one_conformant_ok_envelope(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "--scratch-dir", str(self.scratch)],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, "expected exactly one envelope line, got %r" % lines)
        envelope = json.loads(lines[0])
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        # No decision path — setup prep never emits needs_decision (module docstring).
        self.assertNotIn("decision", envelope)


class PrepSetupNonRepoTestCase(unittest.TestCase):
    """A plain (non-git) temp dir: prep still emits a faithful envelope — `git_repo: false`,
    `root.sha: null` — rather than hard-failing, so the flow's §1 preflight can report the
    not-a-git-repo readiness line and stop."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gh-setup-nonrepo-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.scratch = self.root / "scratch"

    def test_non_git_repo_degrades_gracefully(self):
        facts = prep_setup.build_facts(root=str(self.root), scratch_dir=str(self.scratch))
        self.assertFalse(facts["preflight"]["git_repo"])
        self.assertIsNone(facts["root"]["sha"])
        # inventory still runs (no config files → all missing)
        for entry in facts["inventory"]["known_markers"]:
            self.assertEqual(entry["class"], "missing")


if __name__ == "__main__":
    unittest.main()
