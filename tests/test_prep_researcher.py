"""Unit tests for scripts/prep_researcher.py — the researcher's complete facts block in one call
(architecture.md §4; docs/implementation.md S16; docs/specs/researcher.md).

Test topology (mirrors tests/test_prep_drafter.py — the S8-locked composition pattern this step
inherits): `gh` calls go through the offline shim (`tests/support/shimenv`); the single `git`
call this prep needs (`git rev-parse HEAD` for the informational `root.sha` fact; the researcher
runs no root-freshness protocol — see `prep_researcher.py`'s "No workspace" docstring note) goes
through a real temp origin+clone (`tests/support/gitsandbox`). `prep_researcher.py` is driven as a
real subprocess so every test exercises the full argv-in / subprocess / envelope-out path a real
caller uses. Every fixture origin is a local git sandbox — hermetic (no test resolves `.` against a
real network-backed clone); `shimenv.intercepted_env`'s `NETWORK_POISON_ENV` guard is never bypassed.

Coverage matrix (S16 DoD):
- Vector derivation ×3: broad (no marker, no --question), targeted (--question, no marker), revise
  (an `<!-- issue-research:v1 -->` dossier already exists).
- Prep fixtures: dossier marker present/absent; manifests found/missing (+ governing-doc found/missing).
- Conformance on every emitting path + the two-sided call-budget test: the gather round-trip (3 gh
  calls) IS the whole budget, flat across all three modes (no mode fans out — the thinnest cutover).
- Decision codes: AUTH_REQUIRED (the gather's first gh call), MARKER_AMBIGUOUS (two dossier comments
  match the marker prefix, forwarded from gh_gather).
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
SCRIPT = SCRIPTS_DIR / "prep_researcher.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_researcher  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
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


class PrepResearcherSandboxTestCase(unittest.TestCase):
    """Shared setup: a real temp git origin+clone (the `--root`) — no push/fetch (this prep runs no
    root-freshness protocol; see `prep_researcher.py`'s "No workspace" docstring), but a real git
    repo with a commit is needed for `git rev-parse HEAD` (`root.sha`)."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.scratch = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)

    def _run(self, args, fixture_case=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def _envelope(self, issue, repo="octo/widgets", fixture_case=None, extra_args=None):
        args = [str(issue), repo, "--root", str(self.root), "--scratch-dir", self.scratch]
        if extra_args:
            args += extra_args
        result = self._run(args, fixture_case=fixture_case)
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


# ---------------------------------------------------------------------------
# DoD box: the three-way mode vector (broad / targeted / revise).
# ---------------------------------------------------------------------------


class VectorModeTests(PrepResearcherSandboxTestCase):
    def test_broad_mode_no_marker_no_question(self):
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        self.assertEqual(env["vector"], {"mode": "broad", "questions": None})
        self.assertEqual(env["suggested_playbook"], "broad.md")
        self.assertNotIn("revise", env)

    def test_targeted_mode_question_no_marker(self):
        env = self._envelope(
            700, fixture_case="prep_researcher_broad",
            extra_args=["--question", "As of v9, is the pre-v9 init API deprecated?"],
        )
        self.assertEqual(env["vector"]["mode"], "targeted")
        self.assertEqual(
            env["vector"]["questions"], ["As of v9, is the pre-v9 init API deprecated?"]
        )
        self.assertEqual(env["suggested_playbook"], "targeted.md")
        self.assertNotIn("revise", env)

    def test_targeted_mode_multiple_questions_repeatable(self):
        env = self._envelope(
            700, fixture_case="prep_researcher_broad",
            extra_args=["--question", "q1", "--question", "q2"],
        )
        self.assertEqual(env["vector"]["questions"], ["q1", "q2"])
        self.assertEqual(env["vector"]["mode"], "targeted")

    def test_revise_mode_existing_dossier(self):
        env = self._envelope(701, fixture_case="prep_researcher_revise")
        self.assertEqual(env["vector"]["mode"], "revise")
        self.assertEqual(env["suggested_playbook"], "revise.md")
        self.assertIn("revise", env)

    def test_an_untrusted_dossier_reads_absent_with_a_notice(self):
        # #179: the same dossier by an untrusted author is not a dossier — broad mode, not revise.
        env = self._envelope(701, fixture_case="prep_researcher_revise_untrusted")
        self.assertNotEqual(env["vector"]["mode"], "revise")
        self.assertNotIn("revise", env)
        notices = [n for n in env["notices"] if n.startswith("MARKER_UNTRUSTED")]
        self.assertEqual(len(notices), 1)
        self.assertIn("<!-- issue-research:v1 -->", notices[0])

    def test_revise_precedence_marker_beats_question(self):
        # An existing dossier is the dominant trigger (SKILL.md:63) — a --question on a marked issue
        # is a targeted refresh, still handled by revise mode (delete-and-repost).
        env = self._envelope(
            701, fixture_case="prep_researcher_revise", extra_args=["--question", "narrow it"]
        )
        self.assertEqual(env["vector"]["mode"], "revise")
        self.assertEqual(env["vector"]["questions"], ["narrow it"])
        self.assertEqual(env["suggested_playbook"], "revise.md")


# ---------------------------------------------------------------------------
# DoD box: revise facts (the existing dossier's comment_id/url for delete-and-repost).
# ---------------------------------------------------------------------------


class ReviseFactsTests(PrepResearcherSandboxTestCase):
    def test_revise_dossier_facts_carry_comment_id_and_url(self):
        env = self._envelope(701, fixture_case="prep_researcher_revise")
        dossier = env["revise"]["dossier"]
        self.assertTrue(dossier["present"])
        self.assertEqual(dossier["comment_id"], 8001)
        self.assertIsNotNone(dossier["comment_url"])

    def test_broad_and_targeted_have_no_revise_key(self):
        broad = self._envelope(700, fixture_case="prep_researcher_broad")
        self.assertNotIn("revise", broad)
        targeted = self._envelope(
            700, fixture_case="prep_researcher_broad", extra_args=["--question", "q"]
        )
        self.assertNotIn("revise", targeted)

    def test_target_facts_shape(self):
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        target = env["target"]
        self.assertEqual(target["kind"], "issue")
        self.assertEqual(target["number"], 700)
        self.assertEqual(target["state"], "OPEN")
        self.assertEqual(target["labels"], ["feature"])

    def test_sections_carry_spilled_issue_body_and_thread(self):
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        sections = env["sections"]
        self.assertTrue(any(k.startswith("issue_body") for k in sections))
        self.assertTrue(any(k.startswith("thread") for k in sections))


# ---------------------------------------------------------------------------
# DoD box: manifests found/missing (+ governing-doc found/missing). Filesystem inventory at root.
# ---------------------------------------------------------------------------


class ManifestInventoryTests(PrepResearcherSandboxTestCase):
    def test_manifests_missing(self):
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        manifests = env["inventory"]["manifests"]
        self.assertFalse(manifests["present"])
        self.assertEqual(manifests["files"], [])
        # candidates_checked lists the v1 exact + glob candidate set, so a caller sees the search set.
        self.assertIn("package.json", manifests["candidates_checked"])
        self.assertIn("*.csproj", manifests["candidates_checked"])

    def test_manifests_found_exact_and_glob(self):
        _write(self.root / "package.json", "{}\n")
        _write(self.root / "app.csproj", "<Project/>\n")
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        manifests = env["inventory"]["manifests"]
        self.assertTrue(manifests["present"])
        self.assertEqual(manifests["files"], ["app.csproj", "package.json"])

    def test_docs_baseline_is_the_seeded_readme_only(self):
        # The git sandbox seeds a README.md; the inventory picks it up as a governing doc, and nothing
        # else exists at the clean root (the truly-empty case is covered by test_doc_inventory_empty).
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        docs = env["inventory"]["docs"]
        self.assertTrue(docs["present"])
        self.assertEqual(docs["files"], ["README.md"])

    def test_docs_found_readme_and_docs_dir(self):
        _write(self.root / "CLAUDE.md", "# claude\n")
        _write(self.root / "docs" / "prd.md", "# prd\n")
        _write(self.root / "docs" / "architecture.md", "# arch\n")
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        docs = env["inventory"]["docs"]
        self.assertTrue(docs["present"])
        self.assertIn("README.md", docs["files"])
        self.assertIn("CLAUDE.md", docs["files"])
        self.assertIn("docs/prd.md", docs["files"])
        self.assertIn("docs/architecture.md", docs["files"])


# ---------------------------------------------------------------------------
# root facts (no freshness gate — the researcher grounds on the current checkout).
# ---------------------------------------------------------------------------


class RootFactsTests(PrepResearcherSandboxTestCase):
    def test_root_shape_has_no_freshness_gate(self):
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        self.assertEqual(set(env["root"].keys()), {"path", "sha"})
        self.assertEqual(len(env["root"]["sha"]), 40)

    def test_root_sha_reflects_head_no_fetch(self):
        # A commit made AFTER clone (never pushed) is what root.sha reports — no root-freshness protocol.
        _write(self.root / "local-only.txt", "not pushed\n")
        _git(["add", "local-only.txt"], self.root)
        _git(["commit", "-m", "local-only"], self.root)
        local_sha = _git(["rev-parse", "HEAD"], self.root)
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        self.assertEqual(env["root"]["sha"], local_sha)


# ---------------------------------------------------------------------------
# DoD box: decision codes.
# ---------------------------------------------------------------------------


class DecisionCodeTests(PrepResearcherSandboxTestCase):
    def test_auth_required_on_gather(self):
        result = self._run(
            ["703", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_researcher_auth",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "needs_decision")
        self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")

    def test_two_dossier_comments_yield_marker_ambiguous(self):
        result = self._run(
            ["702", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_researcher_marker_ambiguous",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "needs_decision")
        self.assertEqual(env["decision"]["code"], "MARKER_AMBIGUOUS")


# ---------------------------------------------------------------------------
# DoD box: conformance + two-sided call budget. The gather round-trip (3 gh calls) is the WHOLE
# budget, flat across every mode — no mode fans out (the thinnest cutover).
# ---------------------------------------------------------------------------


class CallBudgetTests(PrepResearcherSandboxTestCase):
    def _manifest_len(self, case):
        return len(
            json.loads((shimenv.fixture_case_dir(case) / "manifest.json").read_text(encoding="utf-8"))
        )

    def test_broad_budget_is_exactly_the_gather_roundtrip(self):
        result = self._run(
            ["700", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_researcher_broad",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._manifest_len("prep_researcher_broad"), 3)

    def test_revise_budget_equals_broad_no_mode_fans_out(self):
        # Two-sided: revise costs no MORE than broad (upper bound) and no LESS than the gather
        # minimum (lower bound) — the researcher prep is flat, unlike the drafter's epic-revise fan-out.
        result = self._run(
            ["701", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
            fixture_case="prep_researcher_revise",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            self._manifest_len("prep_researcher_revise"),
            self._manifest_len("prep_researcher_broad"),
            "revise must cost exactly the gather round-trip, same as broad (flat budget)",
        )
        self.assertGreaterEqual(self._manifest_len("prep_researcher_revise"), 1)

    def test_no_gh_call_repeated_within_a_run(self):
        for case in ("prep_researcher_broad", "prep_researcher_revise", "prep_researcher_marker_ambiguous"):
            manifest = json.loads(
                (shimenv.fixture_case_dir(case) / "manifest.json").read_text(encoding="utf-8")
            )
            argv_tuples = [tuple(entry["argv"]) for entry in manifest]
            self.assertEqual(
                len(argv_tuples), len(set(argv_tuples)), "duplicate argv in manifest for %s" % case
            )

    def test_attention_is_present_and_a_list(self):
        env = self._envelope(700, fixture_case="prep_researcher_broad")
        self.assertIsInstance(env["attention"], list)


# ---------------------------------------------------------------------------
# Pure-helper unit tests — no subprocess, no shim, no git sandbox.
# ---------------------------------------------------------------------------


class PureHelperUnitTests(unittest.TestCase):
    def test_mode_broad(self):
        self.assertEqual(prep_researcher._mode(False, None), "broad")

    def test_mode_targeted(self):
        self.assertEqual(prep_researcher._mode(False, ["q"]), "targeted")

    def test_mode_revise_beats_question(self):
        self.assertEqual(prep_researcher._mode(True, ["q"]), "revise")
        self.assertEqual(prep_researcher._mode(True, None), "revise")

    def test_suggested_playbook_maps_mode_to_variant(self):
        self.assertEqual(prep_researcher._suggested_playbook("broad"), "broad.md")
        self.assertEqual(prep_researcher._suggested_playbook("targeted"), "targeted.md")
        self.assertEqual(prep_researcher._suggested_playbook("revise"), "revise.md")

    def test_manifest_inventory_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = prep_researcher._manifest_inventory(tmp)
            self.assertFalse(result["present"])
            self.assertEqual(result["files"], [])

    def test_manifest_inventory_exact_and_glob(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp) / "Cargo.toml", "[package]\n")
            _write(Path(tmp) / "svc.bazel", "x\n")
            result = prep_researcher._manifest_inventory(tmp)
            self.assertTrue(result["present"])
            self.assertEqual(result["files"], ["Cargo.toml", "svc.bazel"])

    def test_doc_inventory_governing_and_docs_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp) / "CONTRIBUTING.md", "x\n")
            _write(Path(tmp) / "docs" / "prd.md", "x\n")
            result = prep_researcher._doc_inventory(tmp)
            self.assertTrue(result["present"])
            self.assertIn("CONTRIBUTING.md", result["files"])
            self.assertIn("docs/prd.md", result["files"])

    def test_doc_inventory_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = prep_researcher._doc_inventory(tmp)
            self.assertFalse(result["present"])
            self.assertEqual(result["files"], [])

    def test_build_attention_closed_target_is_informational(self):
        line = prep_researcher._build_attention({"number": 9, "state": "CLOSED"})
        self.assertEqual(len(line), 1)
        self.assertIn("#9", line[0])
        self.assertIn("informational", line[0])

    def test_build_attention_open_target_is_empty(self):
        self.assertEqual(prep_researcher._build_attention({"number": 9, "state": "OPEN"}), [])


class UsageErrorTests(unittest.TestCase):
    def test_missing_repo_arg_is_usage_error(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "700"], capture_output=True, encoding="utf-8", check=False
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
