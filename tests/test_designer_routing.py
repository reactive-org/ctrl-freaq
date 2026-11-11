"""Routing and contract tests for skills/designer/ — the designer stage (#204).

What this suite pins, because prose has no compiler:

1. **Routing.** The router's table maps exactly `fresh` and `revise` to `playbooks/fresh.md` and
   `playbooks/revise.md`, byte-consistent with `prep_designer._suggested_playbook`, and both playbooks read
   the shared spine.
2. **Router anatomy.** ≤ 150 lines; the frontmatter pins no `model:` / `effort:` and carries no
   `disable-model-invocation` (the coordinator will start this stage with a skill call the key would refuse).
3. **Fence-scoped greps.** No raw `gh issue|pr` write, no ref arithmetic, no `w/`, no `§P`, no stack name.
4. **The write order.** `design_record.py check` runs before every write; `Approval: approved` appears only
   in a write with no `--attach`; an attached capture is staged as `capture: ![<state-id>](<abs path>)`; each
   write the spine names is a valid `gh_persist.py` invocation (`--dry-run`).
5. **Cards and handoff.** Every card header fits the tool's 12 characters; every handoff shape forwards to
   the planner, carries no `design:` marker, and the story shape carries `**Story:**` and `**Epic:**`; the
   no-block shape names `/ctrl-freaq:setup`.
6. **No prescribed prototyping.** No prompt names a tool, a location or a capture method of its own.
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
SKILL_DIR = REPO_ROOT / "skills" / "designer"
ROUTER = SKILL_DIR / "SKILL.md"
PLAYBOOKS_DIR = SKILL_DIR / "playbooks"
SPINE = PLAYBOOKS_DIR / "design-spine.md"
RENDERINGS = SKILL_DIR / "references" / "handoff-renderings.md"
GH_PERSIST = SCRIPTS_DIR / "gh_persist.py"
SHARED = REPO_ROOT / "skills" / "_shared"
PRD = REPO_ROOT / "docs" / "prd.md"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_designer  # noqa: E402
from tests.support import envelope_asserts  # noqa: E402
from tests.support.retired_tokens import FORBIDDEN_CONTRACT_TOKENS  # noqa: E402
from tests.support.thread_rule import THREAD_RULE_ANCHORS  # noqa: E402

_ROUTE_ROW_RE = re.compile(
    r"^\|\s*`(?P<mode>[a-z]+)`[^|]*\|\s*`(?P<playbook>playbooks/[a-z0-9-]+\.md)`\s*\|"
)


def _skill_files():
    return sorted(SKILL_DIR.rglob("*.md"))


def _fenced_lines(path):
    """``(line_number, line)`` for every line inside a code fence."""
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            yield i, line


def _fences(path):
    """Each fenced block's text, in order."""
    blocks, current, in_fence = [], [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```"):
            if in_fence:
                blocks.append("\n".join(current))
                current = []
            in_fence = not in_fence
            continue
        if in_fence:
            current.append(line)
    return blocks


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.text = ROUTER.read_text(encoding="utf-8")
        self.rows = [(m.group("mode"), m.group("playbook"))
                     for m in map(_ROUTE_ROW_RE.match, self.text.splitlines()) if m]

    def test_table_maps_exactly_the_two_modes(self):
        self.assertEqual(self.rows, [("fresh", "playbooks/fresh.md"), ("revise", "playbooks/revise.md")])

    def test_table_matches_prep_suggested_playbook(self):
        for mode, playbook in self.rows:
            with self.subTest(mode=mode):
                self.assertEqual("playbooks/" + prep_designer._suggested_playbook(mode), playbook)
                self.assertTrue((SKILL_DIR / playbook).is_file())

    def test_both_playbooks_read_the_spine(self):
        for name in ("fresh.md", "revise.md"):
            with self.subTest(playbook=name):
                self.assertIn("(design-spine.md)", (PLAYBOOKS_DIR / name).read_text(encoding="utf-8"))

    def test_router_prep_fence_names_the_prep(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/prep_designer.py <issue> <owner/repo>", self.text)


class RouterAnatomyTests(unittest.TestCase):
    def setUp(self):
        self.text = ROUTER.read_text(encoding="utf-8")
        self.head = self.text.split("\n---\n", 1)[0]

    def test_router_fits_one_read(self):
        self.assertLessEqual(len(self.text.splitlines()), 150)

    def test_frontmatter_pins_nothing(self):
        self.assertIn("name: designer", self.head)
        for key in ("model:", "effort:", "disable-model-invocation"):
            with self.subTest(key=key):
                self.assertNotIn(key, self.head)

    def test_router_has_the_four_sections_in_order(self):
        positions = [self.text.index(h) for h in ("## 1. Prep", "## 2. Route", "## 3. Invariants", "## 4. Handoff")]
        self.assertEqual(positions, sorted(positions))

    def test_thread_readers_carry_the_author_rule(self):
        for name in ("fresh.md", "revise.md"):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            for anchor in THREAD_RULE_ANCHORS:
                with self.subTest(playbook=name, anchor=anchor):
                    self.assertIn(anchor, text)


class FenceGrepTests(unittest.TestCase):
    _RAW_GH_WRITE = re.compile(r"gh +(issue|pr) +(create|edit|comment|review|close|reopen)|gh +api[^\n]*DELETE")
    _REF_ARITHMETIC = re.compile(r"git +show +[^ ]+:|git +grep +[^-]")
    _STACK = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest|storybook|figma|playwright|"
                        r"puppeteer|selenium|react|vite|webpack", re.IGNORECASE)

    def test_no_raw_gh_write_or_ref_arithmetic_in_a_fence(self):
        for path in _skill_files():
            for number, line in _fenced_lines(path):
                with self.subTest(path=path.name, line=number):
                    self.assertIsNone(self._RAW_GH_WRITE.search(line))
                    self.assertIsNone(self._REF_ARITHMETIC.search(line))

    def test_no_banned_shorthand_or_retired_token_anywhere(self):
        for path in _skill_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(re.search(r"\bw/", text))
                self.assertIsNone(FORBIDDEN_CONTRACT_TOKENS.search(text))

    def test_no_prototyping_tool_or_stack_is_named(self):
        # DoD 1: no skill prompt prescribes where a prototype lives or how it is built.
        for path in _skill_files():
            with self.subTest(path=path.name):
                self.assertIsNone(self._STACK.search(path.read_text(encoding="utf-8")))

    def test_stack_scan_catches_a_named_tool(self):
        self.assertIsNotNone(self._STACK.search("prototype it in Storybook"))


class WriteOrderTests(unittest.TestCase):
    def setUp(self):
        self.spine = SPINE.read_text(encoding="utf-8")
        self.fences = _fences(SPINE)
        self.writes = [f for f in self.fences if "gh_persist.py" in f]

    def test_spine_names_each_write(self):
        joined = "\n".join(self.writes)
        self.assertIn("gh_persist.py comment <owner/repo> issue <N>", joined)
        self.assertIn("--attach", joined)
        self.assertIn("--delete-marker-id <facts.record.comment_id>", joined)
        self.assertIn("gh_persist.py edit-comment <owner/repo> <facts.record.comment_id>", joined)

    def test_check_runs_before_every_write(self):
        check = self.spine.index("design_record.py check")
        for write in ("gh_persist.py comment", "gh_persist.py edit-comment"):
            with self.subTest(write=write):
                self.assertLess(check, self.spine.index(write))
        self.assertIn("Run the check before **every** write", self.spine)
        self.assertIn("design_record.py check", ROUTER.read_text(encoding="utf-8"))

    def test_approved_is_written_only_without_attach(self):
        self.assertIn("`Approval: approved` is only ever written by a write that carries no `--attach`",
                      self.spine)
        attach_section = self.spine.split("- **Captures to attach.**", 1)[1].split("- **No capture to attach**", 1)[0]
        self.assertIn("`Approval: pending`", attach_section)
        before_edit = attach_section.split("gh_persist.py edit-comment", 1)[0]
        approving = before_edit.rsplit("Once a post lands", 1)[1]
        self.assertIn("set `Approval: approved <YYYY-MM-DD>`", approving)
        self.assertNotIn("--attach", approving)
        edit_fence = [f for f in self.writes if "edit-comment" in f]
        self.assertEqual(len(edit_fence), 1)
        self.assertNotIn("--attach", edit_fence[0])

    def test_approving_write_restages_from_the_prep_body(self):
        flat = " ".join(self.spine.split())
        self.assertIn("restage from `facts.record.body_path` (the body carrying GitHub's attachment URLs — "
                      "never the local file)", flat)

    def test_no_capture_that_did_not_upload_is_approved(self):
        flat = " ".join(self.spine.split())
        self.assertIn("one that still names a local absolute path did not upload", flat)
        revise = " ".join((PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8").split())
        pending = revise.split("## A `pending` record", 1)[1]
        self.assertLess(pending.index("still names a local absolute path"), pending.index("(`Approve`)"))

    def test_prototype_commit_is_limited_to_its_files(self):
        commit = [f for f in self.fences if " commit -m " in f]
        self.assertEqual(len(commit), 1)
        self.assertRegex(commit[0], r'commit -m "[^"]+" -- <prototype and capture files>')

    def test_every_no_record_end_emits_no_handoff(self):
        router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        for end in ("**End to update the doc**", "**Done**"):
            with self.subTest(end=end):
                self.assertIn(end, router.split("posts nothing and emits no handoff", 1)[0].rsplit("## 4.", 1)[1])

    def test_attached_capture_form(self):
        self.assertIn("`capture: ![<state-id>](<abs path>)`", self.spine)

    def test_record_shape_matches_the_contract(self):
        record = [f for f in self.fences if f.startswith("<!-- ux-design:v1 -->")]
        self.assertEqual(len(record), 1)
        for token in ("Prototype:", "## States", " — capture: ", "Approval:"):
            with self.subTest(token=token):
                self.assertIn(token, record[0])

    def test_tracked_prototype_pushes_before_the_record(self):
        self.assertLess(self.spine.index("push -u origin"), self.spine.index("## D8"))


class PersistDryRunTests(unittest.TestCase):
    """Each write the spine names, run with `--dry-run`: one conformant `ok` envelope, no live call."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.body = Path(self._tmp.name) / "record.md"
        self.body.write_text("<!-- ux-design:v1 -->\nPrototype: x\n\n## States\n- `empty` — e — capture: "
                             "![empty](%s/empty.png)\n\nApproval: pending\n" % self._tmp.name, encoding="utf-8")
        self.shot = Path(self._tmp.name) / "empty.png"
        self.shot.write_bytes(b"\x89PNG\r\n")

    def _run(self, args, op):
        proc = subprocess.run([sys.executable, str(GH_PERSIST)] + args + ["--dry-run"], capture_output=True,
                              encoding="utf-8", check=False)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual((envelope["status"], envelope["op"], envelope["dry_run"]), ("ok", op, True))
        self.assertNotIn("url", envelope)

    def test_pending_post_with_attach_and_delete_marker(self):
        self._run(["comment", "o/r", "issue", "42", str(self.body), "--attach", "%s#empty" % self.shot,
                   "--delete-marker-id", "9001"], "comment")

    def test_approving_edit_comment(self):
        self._run(["edit-comment", "o/r", "9001", str(self.body)], "edit-comment")


class CardTests(unittest.TestCase):
    _HEADER = re.compile(r'header: ?"([^"]+)"')

    def test_every_header_fits_and_the_three_cards_exist(self):
        headers = []
        for path in _skill_files():
            headers.extend(self._HEADER.findall(path.read_text(encoding="utf-8")))
        self.assertEqual(sorted(set(headers)), ["Approve", "Design doc", "Workspace"])
        for header in headers:
            with self.subTest(header=header):
                self.assertLessEqual(len(header), 12)

    def test_relayed_card_identity_line(self):
        self.assertIn("`designer · #<N> · <step>`", ROUTER.read_text(encoding="utf-8"))


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.fences = [f for f in _fences(RENDERINGS) if "## Handoff" in f or f.startswith("**Why:**")]
        self.shapes = [f for f in self.fences if "## Handoff" in f]

    def test_two_full_shapes_and_the_no_block_why(self):
        self.assertEqual(len(self.shapes), 2)
        self.assertEqual(len(self.fences), 3)

    def test_every_shape_forwards_to_the_planner_with_no_design_marker(self):
        for shape in self.shapes:
            with self.subTest(shape=shape.splitlines()[2]):
                self.assertRegex(shape, r"\n    /ctrl-freaq:planner #\d+\n")
                self.assertNotIn("design:", shape)
                self.assertNotIn("**Workspace:**", shape)

    def test_story_shape_carries_story_and_epic_lines(self):
        story = [s for s in self.shapes if "**Story:**" in s]
        self.assertEqual(len(story), 1)
        self.assertRegex(story[0], r"\*\*Epic:\*\* #\d+ — .+ · open \(\d+ of \d+ stories closed\)")

    def test_no_block_shape_points_at_setup(self):
        why = [f for f in self.fences if f.startswith("**Why:**")]
        self.assertEqual(len(why), 1)
        self.assertIn("/ctrl-freaq:setup", why[0])
        self.assertIn("<!-- ui-prototype -->", why[0])


class SharedContractTests(unittest.TestCase):
    def test_handoff_format_counts_the_designer(self):
        text = (SHARED / "handoff-format.md").read_text(encoding="utf-8")
        self.assertIn("seven pipeline skills", text)
        self.assertIn("`designer`", text)
        self.assertIn("researcher, designer, question flows", text)
        self.assertIn("designer → planner", text)

    def test_prd_appends_section_5_7_and_records_the_marker(self):
        text = PRD.read_text(encoding="utf-8")
        section_5 = text.split("## §5 ", 1)[1].split("## §6 ", 1)[0]
        self.assertGreater(section_5.index("- **§5.7 designer.**"), section_5.index("- **§5.6 slicer.**"))
        section_7 = text.split("## §7 ", 1)[1].split("## §8 ", 1)[0]
        self.assertIn("| `<!-- ux-design:v1 -->` design record", section_7)
        self.assertIn("`ui-prototype` (user-owned, #203)", section_7)


if __name__ == "__main__":
    unittest.main()
