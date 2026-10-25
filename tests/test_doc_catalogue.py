"""Unit tests for scripts/doc_catalogue.py — the consuming repo's grounding-doc catalogue
(``skills/_shared/doc-catalogue.md``; architecture.md §2, §7).

The read helpers are **file I/O only** — no `gh`, no `git`, no network, no shim — so their tests drive
the pure cores in-process against real temp files: a prep composes them, so they never emit an
envelope and never exit, and their behavior is pinned against literal block text. The write-side
``check`` (#123) reads one file at a base ref, so its tests use a git sandbox for that base, and one
test runs the CLI end to end.

Two things this suite exists to protect:

  - **The best-effort skip contract.** A malformed entry is skipped, never raised and never coerced
    into a default. Six distinct malformed shapes are pinned line-by-line, because a silently
    skipped entry is a document that silently stops grounding anything, and the only thing standing
    between that and a regression is this test plus `setup`'s post-write validation.
  - **Absent ≠ empty.** No block at all yields the ``DOC_CATALOGUE_ABSENT`` notice; a block that is
    present but declares nothing does *not* — a repo explicitly declaring no grounding documents is
    a different fact from a repo that never declared any, and two different consumers key off the
    two.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

import doc_catalogue  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib.decisions import DOC_CATALOGUE_ABSENT, DOC_CATALOGUE_MALFORMED  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "tests"))
from support import gitsandbox  # noqa: E402

# A well-formed catalogue: three entries, both authority values, a summary carrying its own em dash
# (the `maxsplit=3` case), and a nested path.
WELL_FORMED_INTERIOR = """- `docs/prd.md` — prd — binding — What the product is — and what it deliberately is not.
- `docs/architecture.md` — architecture — binding — Layer rules, module map, the invariant registry.
- `docs/guides/style.md` — guide — informative — House naming and formatting conventions.
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _block(interior):
    return "<!-- doc-catalogue -->\n%s<!-- /doc-catalogue -->\n" % interior


class ParseCatalogueEntriesTests(unittest.TestCase):
    """Pure text -> entries. No filesystem involved, so these pin the grammar itself."""

    def test_well_formed_entries_parse_in_declaration_order(self):
        entries = doc_catalogue.parse_catalogue_entries(WELL_FORMED_INTERIOR.splitlines())
        self.assertEqual(
            [e["path"] for e in entries],
            ["docs/prd.md", "docs/architecture.md", "docs/guides/style.md"],
        )
        self.assertEqual([e["role"] for e in entries], ["prd", "architecture", "guide"])
        self.assertEqual(
            [e["authority"] for e in entries], ["binding", "binding", "informative"]
        )

    def test_summary_may_contain_its_own_em_dashes(self):
        """The `maxsplit=3` rule — without it the first summary here would be truncated at its own
        em dash, silently discarding half of what the repo said about its PRD."""
        entries = doc_catalogue.parse_catalogue_entries(WELL_FORMED_INTERIOR.splitlines())
        self.assertEqual(
            entries[0]["summary"],
            "What the product is — and what it deliberately is not.",
        )

    def test_role_and_authority_are_lowercased_but_unknown_role_is_kept(self):
        entries = doc_catalogue.parse_catalogue_entries(
            ["- `docs/threat-model.md` — Security-Model — BINDING — Trust boundaries."]
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["role"], "security-model")
        self.assertEqual(entries[0]["authority"], "binding")
        self.assertNotIn(entries[0]["role"], doc_catalogue.RECOGNIZED_ROLES)

    def test_malformed_lines_are_skipped_not_raised(self):
        """Six malformed shapes, one per line, none of which may reach the caller."""
        malformed = [
            "",
            "Some prose introducing the catalogue.",
            "- `docs/x.md` — prd — authoritative — authority outside the closed pair",
            "- `docs/y.md` — prd — binding",
            "- docs/z.md — prd — binding — no backtick-quoted path",
            "- `/etc/passwd` — prd — binding — absolute path escapes the vantage",
            "- `../../outside.md` — prd — binding — climbs out of the vantage",
        ]
        self.assertEqual(doc_catalogue.parse_catalogue_entries(malformed), [])

    def test_a_malformed_line_does_not_discard_its_well_formed_neighbours(self):
        entries = doc_catalogue.parse_catalogue_entries(
            [
                "- `docs/prd.md` — prd — binding — Good.",
                "- `docs/broken.md` — prd — nonsense — Bad.",
                "- `docs/architecture.md` — architecture — binding — Also good.",
            ]
        )
        self.assertEqual([e["path"] for e in entries], ["docs/prd.md", "docs/architecture.md"])

    def test_empty_interior_yields_no_entries(self):
        self.assertEqual(doc_catalogue.parse_catalogue_entries([]), [])


class ReadCatalogueTests(unittest.TestCase):
    """The composed read: block discovery at a vantage + per-entry presence resolution."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vantage = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _seed_catalogue(self, interior):
        _write(self.vantage / "docs" / "README.md", _block(interior))

    def test_absent_docs_readme_yields_no_entries_and_the_notice(self):
        entries, notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(entries, [])
        self.assertEqual(notices, [DOC_CATALOGUE_ABSENT])

    def test_docs_readme_without_the_block_yields_the_notice(self):
        _write(self.vantage / "docs" / "README.md", "# Docs\n\nA plain index with no block.\n")
        entries, notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(entries, [])
        self.assertEqual(notices, [DOC_CATALOGUE_ABSENT])

    def test_malformed_block_is_treated_as_absent(self):
        """Unterminated markers — `read_block_anywhere`'s own malformed-is-absent rule, pinned here
        because the notice (not a decision code) is how that degradation surfaces."""
        _write(
            self.vantage / "docs" / "README.md",
            "<!-- doc-catalogue -->\n- `docs/prd.md` — prd — binding — No closing marker.\n",
        )
        entries, notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(entries, [])
        self.assertEqual(notices, [DOC_CATALOGUE_ABSENT])

    def test_present_but_empty_block_is_not_the_absent_notice(self):
        """Absent ≠ empty: a repo declaring no grounding documents made a declaration."""
        self._seed_catalogue("")
        entries, notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(entries, [])
        self.assertEqual(notices, [])

    def test_present_documents_carry_absolute_paths_inside_the_vantage(self):
        self._seed_catalogue(WELL_FORMED_INTERIOR)
        _write(self.vantage / "docs" / "prd.md", "# PRD\n")
        _write(self.vantage / "docs" / "architecture.md", "# Architecture\n")
        _write(self.vantage / "docs" / "guides" / "style.md", "# Style\n")

        entries, notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(notices, [])
        self.assertEqual(len(entries), 3)
        for entry in entries:
            self.assertTrue(entry["present"])
            self.assertEqual(entry["kind"], "file")
            self.assertIsNone(entry["members"])
            self.assertEqual(entry["abs_path"], str(self.vantage / entry["path"]))

    def test_a_directory_entry_is_present_and_lists_its_direct_documents(self):
        """The 4.26.0 regression: `docs/architecture/` (a declared spoke set) read as missing because
        presence was `is_file()`, which raised a false stale-entry line and told the planner and
        slicer to skip binding docs. A directory is present, and its members are the non-hidden
        regular files directly inside it — a nested subdirectory and a dotfile are both left out."""
        self._seed_catalogue(
            "- `docs/architecture/` — architecture — binding — The architecture spokes.\n"
        )
        spokes = self.vantage / "docs" / "architecture"
        _write(spokes / "view-layer.md", "# View layer\n")
        _write(spokes / "api-contract.md", "# API contract\n")
        _write(spokes / ".DS_Store", "")
        _write(spokes / "diagrams" / "flow.md", "# Nested — its own entry if it grounds anything\n")

        entries, notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(notices, [])
        (entry,) = entries
        self.assertTrue(entry["present"])
        self.assertEqual(entry["kind"], "dir")
        self.assertEqual(entry["abs_path"], str(spokes))
        self.assertEqual(
            entry["members"],
            ["docs/architecture/api-contract.md", "docs/architecture/view-layer.md"],
        )
        self.assertEqual(doc_catalogue.missing_entry_paths(entries), [])

    def test_a_directory_entry_without_the_trailing_slash_resolves_the_same(self):
        """`kind` comes from the filesystem, not the spelling: the trailing `/` is setup's
        convention for readability, not a parse rule a hand-edit can break."""
        self._seed_catalogue("- `docs/specs` — spec — informative — Per-feature specs.\n")
        _write(self.vantage / "docs" / "specs" / "export.md", "# Export\n")

        entries, _notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(entries[0]["kind"], "dir")
        self.assertEqual(entries[0]["members"], ["docs/specs/export.md"])

    def test_declared_but_missing_document_is_reported_not_dropped(self):
        """A stale entry and legitimate branch drift are indistinguishable here, so the entry must
        survive to the caller's attention line rather than vanishing."""
        self._seed_catalogue(WELL_FORMED_INTERIOR)
        _write(self.vantage / "docs" / "prd.md", "# PRD\n")

        entries, notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(notices, [])
        self.assertEqual(len(entries), 3)
        self.assertTrue(entries[0]["present"])
        self.assertIsNone(entries[1]["abs_path"])
        self.assertIsNone(entries[1]["kind"])
        self.assertIsNone(entries[1]["members"])
        self.assertEqual(
            doc_catalogue.missing_entry_paths(entries),
            ["docs/architecture.md", "docs/guides/style.md"],
        )

    def test_the_catalogue_is_read_only_from_docs_readme(self):
        """The fixed-home rule: a block in COMMANDS.md/CLAUDE.md — `read_block_anywhere`'s DEFAULT
        candidates — must NOT be picked up, or the "beside the documents" contract is a fiction."""
        _write(self.vantage / "COMMANDS.md", _block(WELL_FORMED_INTERIOR))
        _write(self.vantage / "CLAUDE.md", _block(WELL_FORMED_INTERIOR))
        entries, notices = doc_catalogue.read_catalogue(str(self.vantage))
        self.assertEqual(entries, [])
        self.assertEqual(notices, [DOC_CATALOGUE_ABSENT])


class EntryForRoleTests(unittest.TestCase):
    def setUp(self):
        self.entries = doc_catalogue.parse_catalogue_entries(WELL_FORMED_INTERIOR.splitlines())

    def test_returns_the_matching_entry(self):
        self.assertEqual(doc_catalogue.entry_for_role(self.entries, "prd")["path"], "docs/prd.md")

    def test_returns_none_when_the_role_is_undeclared(self):
        self.assertIsNone(doc_catalogue.entry_for_role(self.entries, "constitution"))

    def test_first_declaration_wins_for_a_duplicated_role(self):
        entries = doc_catalogue.parse_catalogue_entries(
            [
                "- `docs/prd.md` — prd — binding — Current.",
                "- `docs/prd-legacy.md` — prd — informative — Superseded.",
            ]
        )
        self.assertEqual(doc_catalogue.entry_for_role(entries, "prd")["path"], "docs/prd.md")


class CataloguePromptRoutingTests(unittest.TestCase):
    """The prompt side of the catalogue (skills/_shared/doc-catalogue.md §"Handing the catalogue to a
    sub-agent"): every context-blind doc checker receives `<<catalogue_entries>>` in the contract's shape,
    and no skill reads a hardcoded doc list instead (4.27.0 retired the last four)."""

    PROMPTS = (
        "skills/planner/references/plan-reviewer-prompt.md",
        "skills/resolver/references/issue-audit-prompt.md",
        "skills/drafter/references/issue-reviewer-prompt.md",
        "skills/question-resolver/references/constraint-audit-prompt.md",
    )
    # A fixed consuming-repo doc list, the shape every retired instruction had ("`docs/prd.md`,
    # `docs/architecture.md`" / "docs/prd.md (and architecture.md"). doc-reviewer is exempt by design: it
    # reviews the plugin's own five-doc taxonomy, chosen by `--guide <type>`, not a consumer's grounding set.
    _DOC_LIST_RE = re.compile(r"docs/prd\.md`?\s*(,|\(and)\s*`?(docs/)?architecture\.md")

    @staticmethod
    def _flat(rel):
        return " ".join((REPO_ROOT / rel).read_text(encoding="utf-8").split())

    def test_every_doc_checking_prompt_receives_the_catalogue_in_the_contract_shape(self):
        for rel in self.PROMPTS:
            flat = self._flat(rel)
            with self.subTest(prompt=rel):
                self.assertIn("<<catalogue_entries>>", flat)
                self.assertIn("`<path> — <role> — <binding|informative> — <summary>`", flat)
                self.assertIn("omits an entry whose `present` is false", flat)
                self.assertIn("never the directory", flat)

    def test_the_constraint_audit_keeps_its_sections(self):
        """A rewrap once flattened `## What to check` and `## Severity` into the inputs bullet; the
        heading-level pins elsewhere never noticed."""
        lines = (
            (REPO_ROOT / "skills/question-resolver/references/constraint-audit-prompt.md")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        for heading in ("## What to check", "## Severity"):
            self.assertIn(heading, lines)

    def test_no_skill_reads_a_hardcoded_doc_list(self):
        offenders = []
        for path in sorted((REPO_ROOT / "skills").rglob("*.md")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("skills/doc-reviewer/"):
                continue
            if self._DOC_LIST_RE.search(" ".join(path.read_text(encoding="utf-8").split())):
                offenders.append(rel)
        self.assertEqual(offenders, [], "read the catalogue (`grounding_docs`), not a fixed doc list")


class CheckTests(unittest.TestCase):
    """``build_check`` — the write-side gate every catalogue writer runs before committing (#123).

    The base is a real git sandbox: ``origin/main`` carries the committed catalogue, and each test
    edits the clone's working tree the way a writing session would."""

    BASE_INTERIOR = (
        "- `docs/prd.md` — prd — binding — What the product is.\n"
        "- `docs/guides/style.md` — guide — informative — Naming conventions.\n"
    )

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        for rel in ("docs/prd.md", "docs/guides/style.md", "docs/arch.md"):
            _write(self.root / rel, "# %s\n" % rel)
        self._write_catalogue(self.BASE_INTERIOR)
        gitsandbox._run_git(["add", "-A"], self.root)
        gitsandbox._run_git(["commit", "-m", "seed catalogue"], self.root)
        gitsandbox._run_git(["push", "origin", "HEAD:main"], self.root)

    def _write_catalogue(self, interior, prefix="# Docs\n\n"):
        _write(self.root / "docs" / "README.md", prefix + _block(interior))

    def _check(self, base_ref="origin/main"):
        return doc_catalogue.build_check(self.root, base_ref=base_ref)

    def test_an_unchanged_catalogue_passes_with_nothing_to_confirm(self):
        payload, notices, decision = self._check()
        self.assertIsNone(decision)
        self.assertEqual(notices, [])
        self.assertTrue(payload["present"])
        self.assertEqual(payload["entries"], 2)
        self.assertEqual(payload["confirm"], [])

    def test_summary_and_role_edits_and_a_new_informative_entry_need_no_confirmation(self):
        self._write_catalogue(
            "- `docs/prd.md` — product — binding — What the product is, and who it serves.\n"
            "- `docs/guides/style.md` — guide — informative — Naming conventions.\n"
            "- `docs/arch.md` — architecture — informative — Module map.\n"
        )
        payload, _notices, decision = self._check()
        self.assertIsNone(decision)
        self.assertEqual(payload["confirm"], [])

    def test_every_gate_changing_edit_is_listed_for_confirmation(self):
        self._write_catalogue(
            "- `docs/guides/style.md` — guide — binding — Naming conventions.\n"
            "- `docs/arch.md` — architecture — binding — Module map.\n"
        )
        payload, _notices, decision = self._check()
        self.assertIsNone(decision)
        self.assertEqual(
            payload["confirm"],
            [
                {"kind": "authority_changed", "path": "docs/guides/style.md", "from": "informative", "to": "binding"},
                {"kind": "binding_added", "path": "docs/arch.md"},
                {"kind": "binding_removed", "path": "docs/prd.md"},
            ],
        )

    def test_removing_the_block_lists_every_former_binding_entry(self):
        _write(self.root / "docs" / "README.md", "# Docs\n")
        payload, _notices, decision = self._check()
        self.assertIsNone(decision)
        self.assertFalse(payload["present"])
        self.assertEqual(payload["confirm"], [{"kind": "binding_removed", "path": "docs/prd.md"}])

    def test_a_line_readers_would_skip_is_refused_with_its_line_number(self):
        self._write_catalogue(self.BASE_INTERIOR + "- `docs/arch.md` — architecture — required — Module map.\n")
        payload, _notices, decision = self._check()
        self.assertEqual(decision["code"], DOC_CATALOGUE_MALFORMED)
        self.assertEqual(len(payload["malformed_lines"]), 1)
        bad = payload["malformed_lines"][0]
        # `# Docs`, blank, the open marker, then two good lines: the bad one is file line 6.
        self.assertEqual(bad["line"], 6)
        self.assertIn("authority", bad["reason"])
        self.assertEqual(decision["context"]["malformed_lines"], payload["malformed_lines"])

    def test_prose_inside_the_block_and_a_repeated_path_are_refused(self):
        self._write_catalogue(
            self.BASE_INTERIOR
            + "The documents above ground the pipeline.\n"
            + "- `docs/prd.md` — prd — binding — A second copy.\n"
        )
        payload, _notices, decision = self._check()
        self.assertEqual(decision["code"], DOC_CATALOGUE_MALFORMED)
        self.assertEqual(
            [item["reason"] for item in payload["malformed_lines"]],
            ["not a `- ` list item", "path already listed"],
        )

    def test_a_path_absent_from_the_checkout_is_refused(self):
        self._write_catalogue(self.BASE_INTERIOR + "- `docs/gone.md` — guide — informative — Removed.\n")
        payload, _notices, decision = self._check()
        self.assertEqual(decision["code"], DOC_CATALOGUE_MALFORMED)
        self.assertEqual(payload["missing_paths"], ["docs/gone.md"])

    def test_duplicate_or_unterminated_markers_are_refused(self):
        for text, status in (
            (_block(self.BASE_INTERIOR) + _block(""), "dup"),
            ("<!-- doc-catalogue -->\n" + self.BASE_INTERIOR, "open"),
        ):
            with self.subTest(status=status):
                _write(self.root / "docs" / "README.md", text)
                _payload, _notices, decision = self._check()
                self.assertEqual(decision["code"], DOC_CATALOGUE_MALFORMED)
                self.assertEqual(decision["context"]["marker_status"], status)

    def test_an_unreadable_base_leaves_confirm_unmeasured_and_says_so(self):
        payload, notices, decision = self._check(base_ref="origin/no-such-branch")
        self.assertIsNone(decision)
        self.assertIsNone(payload["confirm"])
        self.assertEqual(notices, [doc_catalogue.DOC_CATALOGUE_BASE_UNAVAILABLE])

    def test_the_cli_defaults_the_base_to_the_derived_default_branch(self):
        self._write_catalogue(self.BASE_INTERIOR + "- `docs/arch.md` — architecture — binding — Module map.\n")
        result = subprocess.run(
            [str(SCRIPTS_DIR / "doc_catalogue.py"), "check", "--root", str(self.root)],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["base_ref"], "origin/main")
        self.assertEqual(envelope["confirm"], [{"kind": "binding_added", "path": "docs/arch.md"}])

    def test_the_cli_refuses_a_malformed_block_as_a_decision(self):
        self._write_catalogue(self.BASE_INTERIOR + "- docs/arch.md — architecture — binding — Module map.\n")
        result = subprocess.run(
            [str(SCRIPTS_DIR / "doc_catalogue.py"), "check", "--root", str(self.root)],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], DOC_CATALOGUE_MALFORMED)


if __name__ == "__main__":
    unittest.main()
