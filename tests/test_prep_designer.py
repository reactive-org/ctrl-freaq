"""Unit tests for scripts/prep_designer.py — the designer's facts block in one call (#204).

Topology: `gh` goes through the offline shim, with each test writing its own fixture case to a temp dir
(`GH_SHIM_FIXTURES`), and the checkout is a real sandbox origin + clone (`tests/support/gitsandbox`), with
linked worktrees where a test needs one. The prep runs as a real subprocess, so every test exercises the
argv-in / envelope-out path and the exactly-one-envelope rule.

What this suite protects, because the phase-2 prompts and #205's planner and coordinator build on it:

  - **The mode is the record's presence**, and an untrusted record is never the record (#179).
  - **`workspace.seat` alone decides whether a session may write tracked files**: `root` at the main
    checkout (decided first), `own-branch` on this issue's branch; any other checkout state is a
    forwarded `WORKSPACE_MISMATCH`.
  - **The block and the `ui-design` docs come from the asserted checkout's working tree**, uncommitted
    edits included, and nothing is defaulted when they are absent.
  - **Prep lists existing prototypes and never picks one (#227)**: zero, one or several listed files all leave
    the mode `fresh`; only `--adopt <tracked file>` makes it `adopt`, and a record still wins.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "prep_designer.py"
ROUTER_PLAYBOOKS = ("fresh.md", "revise.md", "adopt.md")

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_designer  # noqa: E402  (import after sys.path setup, by necessity)
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402

REPO = "o/r"
ISSUE = 42
BRANCH = "42-show-the-weekly-summary"
ISSUE_FIELDS = ("number,title,body,state,labels,author,createdAt,updatedAt,assignees,milestone,url,"
                "blockedBy,blocking,parent,subIssues,subIssuesSummary")
PR_FIELDS = "number,title,author,isDraft,headRefName,url,updatedAt,body,closingIssuesReferences"

RECORD = """<!-- ux-design:v1 -->
**Design record** — #42 Show the weekly summary — recorded 2026-11-10T09:00:00Z

Prototype: prototypes/weekly-summary/

## States
- `empty` — no entries yet — capture: ![empty](https://github.com/user-attachments/assets/a1)

Approval: approved 2026-11-10
"""

BLOCK_FILE = """# Commands

<!-- ui-prototype -->
Prototypes live under `prototypes/<issue>/` and run with the repo's dev server.
<!-- /ui-prototype -->
"""


def _issue(number=ISSUE, title="Show the weekly summary", labels=("feature",), state="OPEN", parent=None,
           sub_issues=(), url=None):
    return {
        "number": number, "title": title, "body": "## Definition of done\n- [ ] it shows\n", "state": state,
        "labels": [{"name": name} for name in labels], "author": {"login": "alice"},
        "createdAt": "2026-11-01T00:00:00Z", "updatedAt": "2026-11-02T00:00:00Z", "assignees": [],
        "milestone": None, "url": url or "https://github.com/o/r/issues/%s" % number,
        "blockedBy": {"nodes": []}, "blocking": {"nodes": []}, "parent": parent,
        "subIssues": {"nodes": list(sub_issues), "totalCount": len(sub_issues)},
        "subIssuesSummary": {"completed": 0, "percentCompleted": 0, "total": len(sub_issues)},
    }


def _comment(body, association="MEMBER", rest_id=9001):
    return {
        "id": rest_id, "node_id": "IC_%s" % rest_id, "user": {"login": "alice"},
        "author_association": association, "body": body, "created_at": "2026-11-10T09:00:00Z",
        "updated_at": "2026-11-10T09:00:00Z",
        "html_url": "https://github.com/o/r/issues/42#issuecomment-%s" % rest_id,
    }


class PrepDesignerTestCase(unittest.TestCase):
    """A sandbox origin + clone (the main checkout) and a per-test fixture case."""

    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.fixtures = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-designer-"))
        self.addCleanup(shutil.rmtree, str(self.fixtures), True)
        self.scratch = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-designer-scratch-"))
        self.addCleanup(shutil.rmtree, str(self.scratch), True)
        self.entries = []

    # -- git ---------------------------------------------------------------------------------------

    def _git(self, cwd, *args):
        return gitsandbox._run_git(list(args), cwd=cwd).stdout.strip()

    def _commit(self, cwd, files, message="c"):
        for name, text in files.items():
            path = Path(cwd) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self._git(cwd, "add", name)
        self._git(cwd, "commit", "-q", "-m", message)
        return self._git(cwd, "rev-parse", "HEAD")

    def _worktree(self, branch=BRANCH, push=True):
        """A linked worktree on ``branch``, cut from main and (by default) pushed."""
        path = self.clone.path / ".worktrees" / branch.replace("/", "-")
        self._git(self.clone.path, "worktree", "add", "-q", "-b", branch, str(path), "main")
        if push:
            self._git(path, "push", "-q", "-u", "origin", branch)
        return path

    # -- gh fixtures -------------------------------------------------------------------------------

    def _answer(self, argv, payload):
        name = "out%d.json" % len(self.entries)
        (self.fixtures / name).write_text(json.dumps(payload), encoding="utf-8")
        self.entries.append({"argv": argv, "stdout_file": name, "exit_code": 0})

    def _gather_answers(self, issue_obj, comments=()):
        number = str(issue_obj["number"])
        self._answer(["issue", "view", number, "--repo", REPO, "--json", ISSUE_FIELDS], issue_obj)
        self._answer(["api", "--paginate", "repos/%s/issues/%s/comments" % (REPO, number)], list(comments))
        self._answer(["pr", "list", "--repo", REPO, "--state", "open", "--search", "%s in:body" % number,
                      "--json", PR_FIELDS], [])

    def _run(self, checkout=None, issue=ISSUE, extra=(), env_extra=None):
        (self.fixtures / "manifest.json").write_text(json.dumps(self.entries), encoding="utf-8")
        env = shimenv.intercepted_env(base_env=os.environ)
        env["GH_SHIM_FIXTURES"] = str(self.fixtures)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(issue), REPO, "--root", str(checkout or self.clone.path),
             "--scratch-dir", str(self.scratch)] + list(extra),
            env=env, capture_output=True, encoding="utf-8", check=False,
        )

    def _envelope(self, checkout=None, issue=ISSUE, extra=(), env_extra=None):
        result = self._run(checkout=checkout, issue=issue, extra=extra, env_extra=env_extra)
        self.assertEqual(result.returncode, 0, msg="stderr: %s" % result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, msg="exactly one envelope on stdout: %r" % lines)
        envelope = json.loads(lines[0])
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope

    def _ok(self, **kw):
        envelope = self._envelope(**kw)
        self.assertEqual(envelope["status"], "ok", msg=envelope)
        return envelope

    def _decision(self, code, **kw):
        envelope = self._envelope(**kw)
        self.assertEqual(envelope["status"], "needs_decision", msg=envelope)
        self.assertEqual(envelope["decision"]["code"], code)
        return envelope["decision"]


class ScriptShapeTests(unittest.TestCase):
    def test_script_is_executable_with_a_shebang(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertTrue(SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n"))

    def test_suggested_playbook_covers_exactly_the_three_modes(self):
        self.assertEqual(tuple(prep_designer._suggested_playbook(m) for m in ("fresh", "revise", "adopt")),
                         ROUTER_PLAYBOOKS)
        self.assertEqual(prep_designer._mode({"present": False}), "fresh")
        self.assertEqual(prep_designer._mode({"present": True}), "revise")
        self.assertEqual(prep_designer._mode({"present": False}, {"path": "p"}), "adopt")
        self.assertEqual(prep_designer._mode({"present": True}, {"path": "p"}), "revise")


class ModeAndRecordTests(PrepDesignerTestCase):
    def test_no_record_is_fresh(self):
        self._gather_answers(_issue())
        env = self._ok()
        self.assertEqual(env["vector"], {"mode": "fresh"})
        self.assertEqual(env["suggested_playbook"], "fresh.md")
        self.assertFalse(env["record"]["present"])
        self.assertIsNone(env["record"]["body_path"])
        self.assertIsNone(env["epic"])
        self.assertEqual(env["target"]["type"], "standard")

    def test_trusted_record_is_revise_with_its_body_staged_byte_for_byte(self):
        self._gather_answers(_issue(), [_comment("an ordinary comment", rest_id=9000), _comment(RECORD)])
        env = self._ok()
        self.assertEqual(env["vector"]["mode"], "revise")
        self.assertEqual(env["suggested_playbook"], "revise.md")
        record = env["record"]
        self.assertTrue(record["present"])
        self.assertEqual(record["comment_id"], 9001)
        self.assertTrue(record["approved"])
        self.assertEqual(Path(record["body_path"]).read_bytes(), RECORD.encode("utf-8"))
        self.assertEqual(Path(record["body_path"]).parent, self.scratch)

    def test_a_record_staged_by_an_earlier_run_is_removed_when_none_is_present(self):
        stale = self.scratch / "record.md"
        stale.write_text(RECORD, encoding="utf-8")
        self._gather_answers(_issue())
        env = self._ok()
        self.assertIsNone(env["record"]["body_path"])
        self.assertFalse(stale.exists())

    def test_untrusted_record_alone_reads_absent_with_one_notice(self):
        self._gather_answers(_issue(), [_comment(RECORD, association="NONE")])
        env = self._ok()
        self.assertEqual(env["vector"]["mode"], "fresh")
        self.assertFalse(env["record"]["present"])
        self.assertIsNone(env["record"]["body_path"])
        untrusted = [n for n in env["notices"] if n.startswith("MARKER_UNTRUSTED:")]
        self.assertEqual(len(untrusted), 1, msg=env["notices"])
        self.assertNotIn("alice", untrusted[0])

    def test_untrusted_marker_lookalike_does_not_add_a_second_notice(self):
        # The gather counts `startswith` and the record reader the exact first line: one fact, one notice.
        lookalike = "<!-- ux-design:v1 --> (a note, not a record)\n"
        self._gather_answers(_issue(), [_comment(lookalike, association="NONE", rest_id=8000),
                                        _comment(RECORD, association="NONE", rest_id=8001)])
        env = self._ok()
        untrusted = [n for n in env["notices"] if n.startswith("MARKER_UNTRUSTED:")]
        self.assertEqual(len(untrusted), 1, msg=env["notices"])
        self.assertIn(": 1 comment ", untrusted[0])

    def test_untrusted_copy_beside_a_trusted_record_does_not_displace_it(self):
        self._gather_answers(_issue(), [_comment(RECORD, association="NONE", rest_id=8000), _comment(RECORD)])
        env = self._ok()
        self.assertEqual(env["record"]["comment_id"], 9001)

    def test_two_trusted_records_are_marker_ambiguous(self):
        self._gather_answers(_issue(), [_comment(RECORD, rest_id=9001), _comment(RECORD, rest_id=9002)])
        self._decision("MARKER_AMBIGUOUS")

    def test_a_pr_number_is_target_is_pr(self):
        self._answer(["issue", "view", str(ISSUE), "--repo", REPO, "--json", ISSUE_FIELDS],
                     _issue(url="https://github.com/o/r/pull/42"))
        self._decision("TARGET_IS_PR")


class BlockAndDocsTests(PrepDesignerTestCase):
    CATALOGUE = """# Docs

<!-- doc-catalogue -->
- `docs/ui.md` — ui-design — binding — the interface conventions
- `docs/prd.md` — prd — binding — the product
- `docs/brand/` — ui-design — informative — the brand set
<!-- /doc-catalogue -->
"""

    def test_block_is_read_from_the_working_tree_uncommitted(self):
        (self.clone.path / "COMMANDS.md").write_text(BLOCK_FILE, encoding="utf-8")
        self._gather_answers(_issue())
        block = self._ok()["block"]
        self.assertTrue(block["present"])
        self.assertIn("prototypes/<issue>/", block["text"])
        self.assertEqual(block["source"], str(self.clone.path / "COMMANDS.md"))

    def test_absent_block_reads_not_present(self):
        self._gather_answers(_issue())
        self.assertEqual(self._ok()["block"], {"present": False, "source": None, "text": None})

    def test_ui_design_lists_every_ui_design_entry_in_order(self):
        (self.clone.path / "docs" / "brand").mkdir(parents=True)
        (self.clone.path / "docs" / "brand" / "colour.md").write_text("x\n", encoding="utf-8")
        (self.clone.path / "docs" / "ui.md").write_text("x\n", encoding="utf-8")
        (self.clone.path / "docs" / "README.md").write_text(self.CATALOGUE, encoding="utf-8")
        self._gather_answers(_issue())
        env = self._ok()
        self.assertEqual([(e["path"], e["authority"]) for e in env["ui_design"]],
                         [("docs/ui.md", "binding"), ("docs/brand/", "informative")])
        self.assertEqual(env["ui_design"][1]["kind"], "dir")
        self.assertEqual(env["ui_design"][1]["members"], ["docs/brand/colour.md"])
        self.assertNotIn("DOC_CATALOGUE_ABSENT", env["notices"])
        self.assertEqual(env["attention"], [])

    def test_a_declared_but_missing_ui_design_doc_is_an_attention_line(self):
        (self.clone.path / "docs").mkdir()
        (self.clone.path / "docs" / "README.md").write_text(self.CATALOGUE, encoding="utf-8")
        self._gather_answers(_issue())
        env = self._ok()
        self.assertEqual([e["present"] for e in env["ui_design"]], [False, False])
        self.assertTrue(any("docs/ui.md" in line for line in env["attention"]))

    def test_no_ui_design_entry_is_an_empty_list(self):
        (self.clone.path / "docs").mkdir()
        (self.clone.path / "docs" / "README.md").write_text(
            "<!-- doc-catalogue -->\n- `docs/prd.md` — prd — binding — the product\n<!-- /doc-catalogue -->\n",
            encoding="utf-8")
        self._gather_answers(_issue())
        env = self._ok()
        self.assertEqual(env["ui_design"], [])
        self.assertNotIn("DOC_CATALOGUE_ABSENT", env["notices"])

    def test_no_catalogue_is_the_absent_notice(self):
        self._gather_answers(_issue())
        env = self._ok()
        self.assertEqual(env["ui_design"], [])
        self.assertIn("DOC_CATALOGUE_ABSENT", env["notices"])


class WorkspaceSeatTests(PrepDesignerTestCase):
    def _mismatch(self, checkout, reason):
        decision = self._decision("WORKSPACE_MISMATCH", checkout=checkout)
        self.assertEqual(decision["context"]["reason"], reason)

    def test_main_checkout_is_root(self):
        self._gather_answers(_issue())
        ws = self._ok()["workspace"]
        self.assertEqual(ws["seat"], "root")
        self.assertEqual(ws["kind"], "work")
        self.assertEqual(ws["path"], str(self.clone.path))

    def test_main_checkout_on_the_issues_branch_is_still_root(self):
        self._git(self.clone.path, "checkout", "-q", "-b", BRANCH)
        self._git(self.clone.path, "push", "-q", "-u", "origin", BRANCH)
        self._gather_answers(_issue())
        self.assertEqual(self._ok()["workspace"]["seat"], "root")

    def test_worktree_on_the_issues_branch_is_own_branch(self):
        path = self._worktree()
        self._gather_answers(_issue())
        ws = self._ok(checkout=path)["workspace"]
        self.assertEqual(ws["seat"], "own-branch")
        self.assertEqual(ws["branch"], BRANCH)

    def test_worktree_on_the_epic_branch_form_is_own_branch(self):
        path = self._worktree(branch="epic/42-show-the-weekly-summary")
        self._gather_answers(_issue())
        self.assertEqual(self._ok(checkout=path)["workspace"]["seat"], "own-branch")

    def test_block_is_read_from_the_asserted_worktree(self):
        path = self._worktree()
        (path / "COMMANDS.md").write_text(BLOCK_FILE, encoding="utf-8")
        self._gather_answers(_issue())
        block = self._ok(checkout=path)["block"]
        self.assertTrue(block["present"])
        self.assertEqual(block["source"], str(path / "COMMANDS.md"))

    def test_worktree_on_another_issues_branch_is_branch_mismatch(self):
        path = self._worktree(branch="43-something-else")
        self._gather_answers(_issue())
        self._mismatch(path, "branch_mismatch")

    def test_branch_whose_number_only_prefixes_this_one_is_branch_mismatch(self):
        path = self._worktree(branch="420-something-else")
        self._gather_answers(_issue())
        self._mismatch(path, "branch_mismatch")

    def test_detached_head_is_detached_head(self):
        path = self._worktree()
        self._git(path, "checkout", "-q", "--detach")
        self._gather_answers(_issue())
        self._mismatch(path, "detached_head")

    def test_worktree_behind_its_remote_is_stale(self):
        path = self._worktree()
        self._commit(path, {"a.txt": "a\n"})
        self._git(path, "push", "-q", "origin", BRANCH)
        self._git(path, "reset", "-q", "--hard", "HEAD~1")
        self._gather_answers(_issue())
        self._mismatch(path, "stale_checkout")

    def test_worktree_diverged_from_its_remote_is_stale(self):
        path = self._worktree()
        self._commit(path, {"a.txt": "a\n"})
        self._git(path, "push", "-q", "origin", BRANCH)
        self._git(path, "reset", "-q", "--hard", "HEAD~1")
        self._commit(path, {"b.txt": "b\n"})
        self._gather_answers(_issue())
        self._mismatch(path, "stale_checkout")

    def test_worktree_ahead_of_its_remote_passes(self):
        path = self._worktree()
        self._commit(path, {"a.txt": "a\n"})
        self._gather_answers(_issue())
        ws = self._ok(checkout=path)["workspace"]
        self.assertEqual(ws["seat"], "own-branch")
        self.assertEqual(ws["unpushed_commits"], 1)

    def test_main_checkout_behind_its_remote_is_stale(self):
        self._commit(self.clone.path, {"a.txt": "a\n"})
        self._git(self.clone.path, "push", "-q", "origin", "main")
        self._git(self.clone.path, "reset", "-q", "--hard", "HEAD~1")
        self._gather_answers(_issue())
        self._mismatch(self.clone.path, "stale_checkout")


class EpicAndAttentionTests(PrepDesignerTestCase):
    PARENT = {"number": 40, "title": "Epic: Weekly view", "state": "OPEN"}

    def _epic(self, state="OPEN", labels=("epic",)):
        stories = [{"number": 41, "title": "a", "state": "CLOSED"}, {"number": 42, "title": "b", "state": "OPEN"},
                   {"number": 43, "title": "c", "state": "OPEN"}]
        return _issue(number=40, title="Epic: Weekly view", labels=labels, state=state, sub_issues=stories)

    def test_story_under_an_open_native_parent_epic_gets_its_counts(self):
        self._gather_answers(_issue(labels=("story",), parent=self.PARENT))
        self._gather_answers(self._epic())
        env = self._ok()
        self.assertEqual(env["target"]["type"], "story")
        self.assertEqual(env["epic"], {"number": 40, "title": "Epic: Weekly view", "state": "OPEN",
                                       "closed": 1, "total": 3})

    def test_story_with_no_native_parent_finds_its_epic_through_the_legacy_checklist(self):
        self._gather_answers(_issue(labels=("story",)))
        legacy = dict(self._epic(), body="## Stories\n- [x] #41 — a\n- [ ] #42 — b\n- [ ] #43 — c\n")
        self._answer(["issue", "list", "--repo", REPO, "--label", "epic", "--state", "all", "--search",
                      "#%s in:body" % ISSUE, "--json", "number,title,state,body", "--limit", "1000"],
                     [{"number": 40, "title": "Epic: Weekly view", "state": "OPEN", "body": legacy["body"]}])
        self._gather_answers(legacy)
        self.assertEqual(self._ok()["epic"], {"number": 40, "title": "Epic: Weekly view", "state": "OPEN",
                                              "closed": 1, "total": 3})

    def test_relation_capability_notices_are_forwarded(self):
        # A host without the native relation: the gather degrades with SUBISSUES_UNSUPPORTED, the one sign
        # that `epic: null` means "unread", not "no epic".
        self._gather_answers(_issue())
        (self.fixtures / "manifest.json").write_text(json.dumps(self.entries), encoding="utf-8")
        with mock.patch.object(prep_designer.gh_gather, "run", side_effect=self._degraded_gather):
            facts = prep_designer.build_facts(ISSUE, REPO, root=str(self.clone.path), scratch_dir=str(self.scratch))
        self.assertIn("SUBISSUES_UNSUPPORTED", facts["notices"])

    def _degraded_gather(self, issue, repo, marker_prefix=None, scratch_dir=None, env=None, stream=None,
                         **_kw):
        envelope = {"status": "ok", "number": ISSUE, "title": "Show the weekly summary", "state": "OPEN",
                    "labels": [{"name": "feature"}], "parent": None, "sub_issues": [], "thread": "[]",
                    "thread_mode": "inline", "issue_body": "", "issue_body_mode": "inline",
                    "notices": ["SUBISSUES_UNSUPPORTED"]}
        return 0, envelope

    def test_a_closed_parent_epic_is_null(self):
        self._gather_answers(_issue(labels=("story",), parent=dict(self.PARENT, state="CLOSED")))
        self._gather_answers(self._epic(state="CLOSED"))
        self.assertIsNone(self._ok()["epic"])

    def test_a_non_epic_parent_is_null(self):
        self._gather_answers(_issue(parent={"number": 40, "title": "A story", "state": "OPEN"}))
        self._gather_answers(_issue(number=40, title="A story", labels=("story",)))
        self.assertIsNone(self._ok()["epic"])

    def test_closed_target_is_an_attention_line(self):
        self._gather_answers(_issue(state="CLOSED"))
        attention = self._ok()["attention"]
        self.assertEqual(len(attention), 1)
        self.assertIn("closed", attention[0])

    def test_question_target_is_an_attention_line(self):
        self._gather_answers(_issue(labels=("question",)))
        env = self._ok()
        self.assertEqual(env["target"]["type"], "question")
        self.assertEqual(len(env["attention"]), 1)
        self.assertIn("question", env["attention"][0])

    def test_open_feature_has_no_attention(self):
        self._gather_answers(_issue())
        self.assertEqual(self._ok()["attention"], [])


class ExistingPrototypeTests(PrepDesignerTestCase):
    """`facts.existing` and the `adopt` mode (#227): prep lists, the session picks, `--adopt` makes it a fact."""

    DECLARED = """# Commands

<!-- ui-prototype -->
Prototypes are static pages, one per screen.
**Existing prototypes:** `proto/**/*.html`
**Matching an existing prototype:** each page names its screen spec in its title.
<!-- /ui-prototype -->
"""

    def _declare(self, files=()):
        self._commit(self.clone.path, dict({"COMMANDS.md": self.DECLARED}, **{f: f for f in files}))
        self._git(self.clone.path, "push", "-q", "origin", "main")

    def _candidates(self, existing):
        envelope_asserts.assert_spill_sections_well_formed(existing, ["prototype_candidates"])
        if existing["prototype_candidates_mode"] == "path":
            return json.loads(Path(existing["prototype_candidates_path"]).read_text(encoding="utf-8"))
        return json.loads(existing["prototype_candidates"])

    def test_no_declaration_is_fresh_with_an_inline_empty_list(self):
        self._gather_answers(_issue())
        env = self._ok()
        existing = env["existing"]
        self.assertFalse(existing["declared"])
        self.assertEqual(existing["prototype_candidates_mode"], "inline")
        self.assertEqual(existing["prototype_candidates"], "[]")
        self.assertNotIn("prototype_candidates_path", existing)
        self.assertNotIn("candidates", existing)
        self.assertIsNone(existing["adopted"])
        self.assertFalse(existing["declined"])
        self.assertEqual(env["vector"], {"mode": "fresh"})
        self.assertEqual(env["suggested_playbook"], "fresh.md")
        self.assertEqual(env["attention"], [])

    def test_existing_carries_exactly_the_pinned_keys(self):
        self._gather_answers(_issue())
        self.assertEqual(set(self._ok()["existing"]),
                         {"declared", "patterns", "findings", "prototype_candidates",
                          "prototype_candidates_bytes", "prototype_candidates_mode", "declined", "adopted"})

    def _assert_listed_and_fresh(self, files):
        self._declare(files)
        self._gather_answers(_issue())
        env = self._ok()
        self.assertTrue(env["existing"]["declared"])
        self.assertEqual(env["existing"]["patterns"], ["proto/**/*.html"])
        self.assertEqual([c["path"] for c in self._candidates(env["existing"])], sorted(files))
        self.assertEqual(env["vector"]["mode"], "fresh")
        self.assertEqual(env["suggested_playbook"], "fresh.md")

    def test_zero_matches_stay_fresh(self):
        self._assert_listed_and_fresh(())

    def test_one_match_stays_fresh(self):
        self._assert_listed_and_fresh(("proto/a.html",))

    def test_several_matches_stay_fresh(self):
        self._assert_listed_and_fresh(("proto/a.html", "proto/admin/b.html", "proto/c.html"))

    def test_a_malformed_declaration_is_a_notice_and_findings(self):
        self._commit(self.clone.path, {"COMMANDS.md": self.DECLARED.replace("`proto/**/*.html`", "`../x/*`")})
        self._gather_answers(_issue())
        env = self._ok()
        self.assertFalse(env["existing"]["declared"])
        self.assertEqual(len(env["existing"]["findings"]), 1)
        self.assertTrue(any(n.startswith("UI_PROTOTYPE_DECLARATION_MALFORMED:") for n in env["notices"]))

    def test_adopt_a_listed_path_at_root(self):
        self._declare(("proto/a.html",))
        self._gather_answers(_issue())
        env = self._ok(extra=["--adopt", "proto/a.html"])
        self.assertEqual(env["vector"]["mode"], "adopt")
        self.assertEqual(env["suggested_playbook"], "adopt.md")
        self.assertEqual(env["workspace"]["seat"], "root")
        adopted = env["existing"]["adopted"]
        self.assertEqual(adopted["path"], "proto/a.html")
        self.assertEqual(adopted["abs_path"], str(self.clone.path / "proto" / "a.html"))
        self.assertEqual(adopted["branch"], env["workspace"]["branch"])
        self.assertEqual(adopted["sha"], env["workspace"]["sha"])
        self.assertTrue(adopted["listed"])
        self.assertFalse(adopted["modified"])
        self.assertTrue(adopted["published"])

    def test_adopt_a_tracked_unlisted_path(self):
        self._declare(("proto/a.html", "elsewhere/x.html"))
        self._gather_answers(_issue())
        adopted = self._ok(extra=["--adopt", "elsewhere/x.html"])["existing"]["adopted"]
        self.assertFalse(adopted["listed"])

    def test_adopt_an_untracked_or_missing_path_stays_fresh_with_one_attention_line(self):
        self._declare(("proto/a.html",))
        (self.clone.path / "proto" / "new.html").write_text("n", encoding="utf-8")
        for path in ("proto/new.html", "proto/missing.html"):
            with self.subTest(path=path):
                self._gather_answers(_issue())
                env = self._ok(extra=["--adopt", path])
                self.assertEqual(env["vector"]["mode"], "fresh")
                self.assertIsNone(env["existing"]["adopted"])
                self.assertEqual(len(env["attention"]), 1)
                self.assertIn(path, env["attention"][0])

    def test_adopt_with_a_trusted_record_is_revise(self):
        self._declare(("proto/a.html",))
        self._gather_answers(_issue(), [_comment(RECORD)])
        env = self._ok(extra=["--adopt", "proto/a.html"])
        self.assertEqual(env["vector"]["mode"], "revise")
        self.assertEqual(env["suggested_playbook"], "revise.md")

    def test_no_adopt_is_declined_and_fresh(self):
        self._declare(("proto/a.html",))
        self._gather_answers(_issue())
        env = self._ok(extra=["--no-adopt"])
        self.assertTrue(env["existing"]["declined"])
        self.assertEqual(env["vector"]["mode"], "fresh")

    def test_adopt_and_no_adopt_together_is_a_usage_error(self):
        self._gather_answers(_issue())
        result = self._run(extra=["--adopt", "proto/a.html", "--no-adopt"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout.strip(), "")

    def test_adopt_at_a_root_checkout_ahead_of_its_remote_is_unpublished_until_pushed(self):
        self._commit(self.clone.path, {"COMMANDS.md": self.DECLARED, "proto/a.html": "a"})
        self._gather_answers(_issue())
        self.assertFalse(self._ok(extra=["--adopt", "proto/a.html"])["existing"]["adopted"]["published"])
        self._git(self.clone.path, "push", "-q", "origin", "main")
        self._gather_answers(_issue())
        self.assertTrue(self._ok(extra=["--adopt", "proto/a.html"])["existing"]["adopted"]["published"])

    def test_adopt_on_a_branch_with_no_remote_tracking_ref_is_unpublished(self):
        self._declare(("proto/a.html",))
        self._git(self.clone.path, "checkout", "-q", "-b", "local-only")
        self._gather_answers(_issue())
        adopted = self._ok(extra=["--adopt", "proto/a.html"])["existing"]["adopted"]
        self.assertEqual(adopted["branch"], "local-only")
        self.assertFalse(adopted["published"])

    def test_adopt_at_the_own_branch_seat(self):
        self._declare(("proto/a.html",))
        path = self._worktree()
        self._gather_answers(_issue())
        env = self._ok(checkout=path, extra=["--adopt", "proto/a.html"])
        self.assertEqual(env["workspace"]["seat"], "own-branch")
        self.assertEqual(env["vector"]["mode"], "adopt")
        adopted = env["existing"]["adopted"]
        self.assertEqual(adopted["branch"], BRANCH)
        self.assertEqual(adopted["abs_path"], str(path.resolve() / "proto" / "a.html"))
        self.assertTrue(adopted["published"])

    def test_adopt_of_an_edited_file_is_modified(self):
        self._declare(("proto/a.html",))
        (self.clone.path / "proto" / "a.html").write_text("edited", encoding="utf-8")
        self._gather_answers(_issue())
        adopted = self._ok(extra=["--adopt", "proto/a.html"])["existing"]["adopted"]
        self.assertTrue(adopted["modified"])
        self.assertTrue(self._candidates(self._ok_again())[0]["modified"])

    def _ok_again(self):
        self._gather_answers(_issue())
        return self._ok()["existing"]

    def test_a_list_over_the_spill_threshold_is_path_mode(self):
        self._declare(("proto/a.html", "proto/b.html"))
        self._gather_answers(_issue())
        existing = self._ok(env_extra={"GH_PIPELINE_INLINE_THRESHOLD_BYTES": "10"})["existing"]
        self.assertEqual(existing["prototype_candidates_mode"], "path")
        self.assertNotIn("prototype_candidates", existing)
        self.assertEqual(Path(existing["prototype_candidates_path"]).name, "prototype-candidates.json")
        self.assertEqual([c["path"] for c in self._candidates(existing)], ["proto/a.html", "proto/b.html"])

    def test_a_spilled_list_from_an_earlier_run_is_removed_when_inline(self):
        stale = self.scratch / "prototype-candidates.json"
        stale.write_text('[{"path": "old.html", "modified": false}]', encoding="utf-8")
        self._gather_answers(_issue())
        self._ok()
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
