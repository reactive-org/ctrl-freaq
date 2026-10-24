"""Unit tests for scripts/sensitive_paths.py — the `<!-- pr-evaluator-sensitive-paths -->` declaration
and its one matcher (#96). A real temp git origin+clone (tests/support/gitsandbox); no `gh` shim, the
module spawns only `git`.

Coverage: the block grammar; the glob table (exact, `*` crossing `/`, `*.tf` at depth, a trailing `/`,
a leading `/`, a case-sensitive miss, no match); absent and empty blocks; the self-edit rule against a
committed `base_rev` (add / edit / remove / move / break, an unrelated host edit, an unreadable base,
an absolute `@`-include host) and its `base_rev=None` fallback; a host reached outside the root, or out
and back in (SEC-1), which supplies no patterns and is reported `unparsed`; `changed_paths` (rename, deletion, a
missing base).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sensitive_paths  # noqa: E402
from tests.support import gitsandbox  # noqa: E402

SELF = sensitive_paths.SELF_EDIT_PATTERN


def _block(*entries):
    return "<!-- pr-evaluator-sensitive-paths -->\n%s<!-- /pr-evaluator-sensitive-paths -->\n" % "".join(
        line + "\n" for line in entries
    )


def _git(args, cwd):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), result.stderr))
    return result.stdout.strip()


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message="change"):
        _git(["add", "-A"], self.root)
        _git(["commit", "-q", "-m", message], self.root)
        return _git(["rev-parse", "HEAD"], self.root)

    def changed_since(self, base):
        return [p for p in _git(["diff", "--name-only", "--no-renames", base, "HEAD"], self.root).splitlines() if p]


class GrammarTests(unittest.TestCase):
    def test_pattern_with_and_without_reason_and_skipped_lines(self):
        entries = sensitive_paths.parse_entries([
            "- `.github/workflows/` — CI definitions",
            "  - `infra/*.tf`  ",
            "prose that is not an entry",
            "- infra/no-backticks",
            "",
        ])
        self.assertEqual(entries, [
            {"pattern": ".github/workflows/", "reason": "CI definitions"},
            {"pattern": "infra/*.tf", "reason": None},
        ])


class UnparsedEntryTests(unittest.TestCase):
    """A list item the matcher cannot read is reported, never silently dropped (#96: a typo must make
    the gate ask more, never less); prose lines in the block stay ignored."""

    def test_typoed_list_items_are_unparsed_and_prose_is_not(self):
        lines = [
            "- `.github/workflows/` — CI definitions",
            "- `infra/` - ascii hyphen instead of the em dash",
            "- infra/no-backticks",
            "* `deploy/`",
            "1. `ops/` — numbered",
            "---",
            "Paths below always need a person:",
            "",
        ]
        self.assertEqual(
            sensitive_paths.unparsed_entries(lines),
            [
                "- `infra/` - ascii hyphen instead of the em dash",
                "- infra/no-backticks",
                "* `deploy/`",
                "1. `ops/` — numbered",
            ],
        )
        self.assertEqual([e["pattern"] for e in sensitive_paths.parse_entries(lines)], [".github/workflows/"])


class MatchTableTests(SandboxCase):
    def setUp(self):
        super().setUp()
        self.write("CLAUDE.md", _block(
            "- `deploy.sh` — exact",
            "- `secrets/*` — star crosses slash",
            "- `*.tf` — any depth",
            "- `.github/workflows/` — folder",
            "- `/Makefile` — leading slash",
            "- `Docs/*` — case-sensitive",
        ))

    def match(self, path):
        return sensitive_paths.match(self.root, [path])

    def test_exact_path(self):
        self.assertEqual(self.match("deploy.sh"), [("deploy.sh", "deploy.sh")])

    def test_star_crosses_slash(self):
        self.assertEqual(self.match("secrets/a/b/key.pem"), [("secrets/*", "secrets/a/b/key.pem")])

    def test_extension_glob_at_depth(self):
        self.assertEqual(self.match("infra/prod/main.tf"), [("*.tf", "infra/prod/main.tf")])

    def test_trailing_slash_covers_nested_files(self):
        self.assertEqual(
            self.match(".github/workflows/sub/ci.yml"),
            [(".github/workflows/", ".github/workflows/sub/ci.yml")],
        )

    def test_leading_slash_is_stripped(self):
        self.assertEqual(self.match("Makefile"), [("/Makefile", "Makefile")])

    def test_case_sensitive_miss(self):
        self.assertEqual(self.match("docs/readme.md"), [])

    def test_no_match(self):
        self.assertEqual(self.match("src/app.py"), [])

    def test_brackets_are_a_character_class(self):
        self.write("CLAUDE.md", _block("- `app/[id]/page.tsx`", "- `web/[[]id]/page.tsx`"))
        self.assertEqual(sensitive_paths.match(self.root, ["app/[id]/page.tsx"]), [])
        self.assertEqual(
            sensitive_paths.match(self.root, ["web/[id]/page.tsx"]),
            [("web/[[]id]/page.tsx", "web/[id]/page.tsx")],
        )

    def test_folder_without_trailing_slash_matches_only_itself(self):
        self.write("CLAUDE.md", _block("- `infra`"))
        self.assertEqual(sensitive_paths.match(self.root, ["infra/main.tf", "infra"]), [("infra", "infra")])

    def test_dot_slash_prefix_is_not_normalized(self):
        self.write("CLAUDE.md", _block("- `./deploy.sh`"))
        self.assertEqual(sensitive_paths.match(self.root, ["deploy.sh"]), [])

    def test_pairs_follow_declaration_then_input_order(self):
        pairs = sensitive_paths.match(self.root, ["b.tf", "deploy.sh", "a.tf"])
        self.assertEqual(pairs, [("deploy.sh", "deploy.sh"), ("*.tf", "b.tf"), ("*.tf", "a.tf")])

    def test_read_declaration_reports_source_and_entries(self):
        declaration = sensitive_paths.read_declaration(self.root)
        self.assertTrue(declaration["present"])
        self.assertEqual(declaration["source"], "CLAUDE.md")
        self.assertEqual(declaration["entries"][0], {"pattern": "deploy.sh", "reason": "exact"})


class AbsentAndEmptyTests(SandboxCase):
    def test_absent_block_matches_nothing(self):
        self.assertEqual(sensitive_paths.match(self.root, ["anything", "CLAUDE.md"]), [])
        self.assertEqual(
            sensitive_paths.read_declaration(self.root),
            {"present": False, "source": None, "entries": [], "unparsed": []},
        )

    def test_empty_block_matches_nothing(self):
        self.write("CLAUDE.md", _block())
        self.assertEqual(sensitive_paths.match(self.root, ["anything"]), [])


class SelfEditTests(SandboxCase):
    def setUp(self):
        super().setUp()
        self.write("CLAUDE.md", "# Guide\n\n" + _block("- `infra/` — infrastructure"))
        self.base = self.commit("base")

    def match(self):
        return sensitive_paths.match(self.root, self.changed_since(self.base), base_rev=self.base)

    def test_add_block_where_base_had_none(self):
        self.write("CLAUDE.md", "# Guide\n")
        base = self.commit("drop")
        self.write("CLAUDE.md", "# Guide\n\n" + _block("- `x/`"))
        self.commit("add")
        pairs = sensitive_paths.match(self.root, self.changed_since(base), base_rev=base)
        self.assertEqual(pairs, [(SELF, "CLAUDE.md")])

    def test_edit_a_pattern(self):
        self.write("CLAUDE.md", "# Guide\n\n" + _block("- `infrastructure/` — infrastructure"))
        self.commit()
        self.assertEqual(self.match(), [(SELF, "CLAUDE.md")])

    def test_remove_block(self):
        self.write("CLAUDE.md", "# Guide\n")
        self.commit()
        self.assertEqual(self.match(), [(SELF, "CLAUDE.md")])

    def test_move_block_to_commands_md(self):
        self.write("CLAUDE.md", "# Guide\n")
        self.write("COMMANDS.md", _block("- `infra/` — infrastructure"))
        self.commit()
        self.assertEqual(self.match(), [(SELF, "CLAUDE.md"), (SELF, "COMMANDS.md")])

    def test_break_block_with_duplicate_marker(self):
        self.write("CLAUDE.md", "# Guide\n\n" + _block("- `infra/`") + "\n<!-- pr-evaluator-sensitive-paths -->\n")
        self.commit()
        self.assertEqual(self.match(), [(SELF, "CLAUDE.md")])

    def test_unrelated_edit_to_the_host_file_is_not_a_self_edit(self):
        self.write("CLAUDE.md", "# Guide, retitled\n\n" + _block("- `infra/` — infrastructure"))
        self.commit()
        self.assertEqual(self.match(), [])

    def test_patterns_still_match_beside_a_self_edit(self):
        self.write("CLAUDE.md", "# Guide\n\n" + _block("- `infra/` — infra"))
        self.write("infra/main.tf", "x\n")
        self.commit()
        self.assertEqual(self.match(), [(SELF, "CLAUDE.md"), ("infra/", "infra/main.tf")])

    def test_moving_the_block_into_an_absolute_include_is_a_self_edit(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        host = Path(outside.name).resolve() / "shared.md"
        host.write_text(_block("- `infra/` — infrastructure"), encoding="utf-8")
        self.write("CLAUDE.md", "# Guide\n\nSee @%s\n" % host)
        self.commit()
        self.assertEqual(self.match(), [(SELF, "CLAUDE.md")])

    def test_non_utf8_base_blob_raises(self):
        (self.root / "CLAUDE.md").write_bytes(b"\xff\xfe not utf-8\n")
        base = self.commit("binary host")
        self.write("CLAUDE.md", "# Guide\n")
        self.commit()
        with self.assertRaises(sensitive_paths.BaseReadError):
            sensitive_paths.match(self.root, self.changed_since(base), base_rev=base)

    def test_unreadable_base_raises(self):
        with self.assertRaises(sensitive_paths.BaseReadError):
            sensitive_paths.match(self.root, ["CLAUDE.md"], base_rev="0" * 40)

    def test_a_dotdot_include_outside_the_repo_is_unreached(self):
        outside = self.root.parent / ("%s-shared.md" % self.root.name)
        outside.write_text(_block("- `x/`"), encoding="utf-8")
        self.addCleanup(outside.unlink)
        self.write("CLAUDE.md", "See @../%s\n" % outside.name)
        declaration = sensitive_paths.read_declaration(self.root)
        self.assertEqual((declaration["present"], declaration["source"], declaration["entries"]), (False, None, []))
        self.assertEqual(len(declaration["unparsed"]), 1)
        self.assertIn(str(outside), declaration["unparsed"][0])

    def test_no_base_falls_back_to_the_head_host_path(self):
        self.assertEqual(sensitive_paths.match(self.root, ["CLAUDE.md"]), [(SELF, "CLAUDE.md")])
        self.assertEqual(sensitive_paths.match(self.root, ["README.md"]), [])

    def test_an_absolute_include_host_supplies_no_patterns_and_is_unparsed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        host = Path(outside.name).resolve() / "shared.md"
        host.write_text(_block("- `deploy/`"), encoding="utf-8")
        self.write("CLAUDE.md", "# Guide\n\nSee @%s\n" % host)
        base = self.commit("include")
        host.write_text(_block("- `deploy/` — edited outside the repo"), encoding="utf-8")
        self.write("deploy/run.sh", "x\n")
        self.commit("touch deploy")
        pairs = sensitive_paths.match(self.root, self.changed_since(base), base_rev=base)
        self.assertEqual(pairs, [])
        self.assertEqual(len(sensitive_paths.read_declaration(self.root)["unparsed"]), 1)


class SymlinkHostTests(SandboxCase):
    """A host candidate that is a symlink reads as its in-repo target on both sides, so it compares
    like a plain file (`CLAUDE.md -> AGENTS.md` is a common layout)."""

    def setUp(self):
        super().setUp()
        self.write("AGENTS.md", "# Agents\n\n" + _block("- `infra/` — infrastructure"))
        os.symlink("AGENTS.md", str(self.root / "CLAUDE.md"))
        self.base = self.commit("symlinked host")

    def match(self):
        return sensitive_paths.match(self.root, self.changed_since(self.base), base_rev=self.base)

    def test_an_unchanged_symlinked_host_is_not_a_self_edit(self):
        self.write("src/app.py", "x = 1\n")
        self.commit()
        self.assertEqual(self.match(), [])

    def test_editing_the_block_behind_the_link_is_a_self_edit(self):
        self.write("AGENTS.md", "# Agents\n\n" + _block("- `ops/` — infrastructure"))
        self.commit()
        self.assertEqual(self.match(), [(SELF, "AGENTS.md")])

    def test_retargeting_the_link_to_another_block_is_a_self_edit(self):
        self.write("OTHER.md", _block("- `ops/`"))
        os.remove(str(self.root / "CLAUDE.md"))
        os.symlink("OTHER.md", str(self.root / "CLAUDE.md"))
        self.commit()
        self.assertIn((SELF, "CLAUDE.md"), self.match())

    def test_a_link_out_of_the_repo_supplies_no_patterns_and_is_unparsed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        host = Path(outside.name).resolve() / "shared.md"
        host.write_text(_block("- `deploy/`"), encoding="utf-8")
        os.remove(str(self.root / "CLAUDE.md"))
        os.remove(str(self.root / "AGENTS.md"))
        os.symlink(str(host), str(self.root / "CLAUDE.md"))
        base = self.commit("link out")
        host.write_text(_block("- `deploy/` — edited outside the repo"), encoding="utf-8")
        self.write("deploy/run.sh", "x\n")
        self.commit()
        pairs = sensitive_paths.match(self.root, self.changed_since(base), base_rev=base)
        self.assertEqual(pairs, [])
        self.assertEqual(len(sensitive_paths.read_declaration(self.root)["unparsed"]), 1)

    def test_a_dangling_link_on_both_sides_is_not_a_self_edit(self):
        os.remove(str(self.root / "CLAUDE.md"))
        os.symlink("MISSING.md", str(self.root / "CLAUDE.md"))
        os.remove(str(self.root / "AGENTS.md"))
        base = self.commit("dangling")
        self.write("src/app.py", "x = 1\n")
        self.commit()
        self.assertEqual(sensitive_paths.match(self.root, self.changed_since(base), base_rev=base), [])

    def test_an_include_through_a_symlinked_directory_compares_unchanged(self):
        self.write("real/config.md", _block("- `x/`"))
        os.symlink("real", str(self.root / "docs"))
        self.write("COMMANDS.md", "See @docs/config.md\n")
        os.remove(str(self.root / "CLAUDE.md"))
        os.remove(str(self.root / "AGENTS.md"))
        base = self.commit("dir link")
        self.write("src/app.py", "x = 1\n")
        self.commit()
        self.assertEqual(sensitive_paths.match(self.root, self.changed_since(base), base_rev=base), [])


class OutAndBackHostTests(SandboxCase):
    """SEC-1: a host reached by a route that leaves the root and comes back in (a `..` symlink or
    include through the checkout's own directory name, or an absolute one) is content the PR commits.
    The patterns and the self-edit comparison read it through one discovery, so it can never put an
    empty declaration ahead of the real one unseen."""

    def setUp(self):
        super().setUp()
        self.write("docs/gate.md", _block("- `.github/workflows/` — CI"))
        self.write("CLAUDE.md", "# Guide\n\nSee @docs/gate.md\n")
        self.base = self.commit("base")
        self.write("x.md", _block())
        self.write(".github/workflows/ci.yml", "on: push\n")

    def assert_still_gated(self):
        self.commit("head")
        declaration = sensitive_paths.read_declaration(self.root)
        self.assertEqual(declaration["source"], "docs/gate.md")
        self.assertEqual([e["pattern"] for e in declaration["entries"]], [".github/workflows/"])
        self.assertEqual(len(declaration["unparsed"]), 1)
        self.assertIn("reached outside the repo", declaration["unparsed"][0])
        pairs = sensitive_paths.match(self.root, self.changed_since(self.base), base_rev=self.base)
        self.assertIn((".github/workflows/", ".github/workflows/ci.yml"), pairs)

    def test_a_dotdot_symlink_back_into_the_repo(self):
        os.symlink("../%s/x.md" % self.root.name, str(self.root / "COMMANDS.md"))
        self.assert_still_gated()

    def test_an_absolute_symlink_back_into_the_repo(self):
        os.symlink(str((self.root / "x.md").resolve()), str(self.root / "COMMANDS.md"))
        self.assert_still_gated()

    def test_a_dotdot_include_back_into_the_repo(self):
        self.write("COMMANDS.md", "See @../%s/x.md\n" % self.root.name)
        self.assert_still_gated()


class ChangedPathsTests(SandboxCase):
    def setUp(self):
        super().setUp()
        self.write("old.txt", "keep\n")
        self.write("gone.txt", "bye\n")
        self.commit("seed")
        _git(["push", "-q", "origin", "HEAD:main"], self.root)
        _git(["checkout", "-q", "-b", "feature"], self.root)

    def test_rename_lists_both_paths_and_deletion_is_listed(self):
        _git(["mv", "old.txt", "new.txt"], self.root)
        _git(["rm", "-q", "gone.txt"], self.root)
        self.commit("rename + delete")
        paths, merge_base, error = sensitive_paths.changed_paths(self.root, "main")
        self.assertIsNone(error)
        self.assertEqual(merge_base, _git(["rev-parse", "origin/main"], self.root))
        self.assertEqual(sorted(paths), ["gone.txt", "new.txt", "old.txt"])

    def test_non_utf8_git_output_returns_an_error_never_raises(self):
        def undecodable(root, base_ref):
            raise UnicodeDecodeError("utf-8", b"\xe9", 0, 1, "invalid continuation byte")

        original = sensitive_paths._changed_paths
        sensitive_paths._changed_paths = undecodable
        self.addCleanup(setattr, sensitive_paths, "_changed_paths", original)
        paths, merge_base, error = sensitive_paths.changed_paths(self.root, "main")
        self.assertEqual((paths, merge_base), ([], None))
        self.assertIn("not UTF-8", error)

    def test_missing_base_returns_an_error_never_raises(self):
        paths, merge_base, error = sensitive_paths.changed_paths(self.root, "no-such-branch")
        self.assertEqual((paths, merge_base), ([], None))
        self.assertIn("git fetch origin no-such-branch failed", error)


if __name__ == "__main__":
    unittest.main()
