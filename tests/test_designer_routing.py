"""Routing and contract tests for skills/designer/ — the designer stage (#204).

What this suite pins, because prose has no compiler:

1. **Routing.** The router's table maps exactly `fresh`, `revise` and `adopt` (#227) to `playbooks/fresh.md`,
   `playbooks/revise.md` and `playbooks/adopt.md`, byte-consistent with `prep_designer._suggested_playbook`,
   and every playbook reads the shared spine.
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
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
README = REPO_ROOT / "README.md"

# MEASURED, not halved (docs/prd.md §10). A designer session loads its router, ONE route playbook and the
# shared spine `playbooks/design-spine.md`, so the bar covers all three. There is no v1 designer, so §10's
# "half the v1 SKILL.md line count" has no input; a stage with no v1 baseline records a measured bar
# derived from the closest real precedent instead.
#
# DERIVATION (#208, 2026-11-16, at epic/201-design-the-user-interface-before-it-is-built@6a5ecfa):
#   router 97 + revise.md 38 (the larger route; fresh.md is 21) + design-spine.md 201 = 336.
#
# The precedents: SLICER_BAR = 290 (tests/test_slicer_routing.py), the other stage with no v1 baseline,
# and the planner's router + spine, 120 + 131 = 251 (tests/test_planner_routing.py). The designer sits
# 46 lines above the slicer's bar because its spine also carries three behaviours a story's Definition
# of done required: the record's write order (pending then approved, captures attached by `--attach`,
# `design_record.py check` before every write; #204), the seat rule (nothing tracked from the project
# root, a tracked prototype committed and pushed from the issue's own branch before the record posts;
# #204), and D9's convention follow-up (#207). Per §10 and the S19 doc-reviewer ruling, 336 is recorded
# as the honest measurement at landing: a later growth is adjudicated and re-recorded here, and
# precision is never trimmed out of a prompt to hit the number. The router alone still fits one
# default Read (RouterAnatomyTests, <= 150).
#
# RE-RECORDED (#227, 2026-11-16, phase 2): router 120 + adopt.md 43 (now the largest route; revise.md is 41,
# fresh.md 21) + design-spine.md 217 = 380. The growth is the adopt route the issue's Definition of done
# required: the router's pre-route match over prep's listed candidates with its `Prototype` card and the new
# prep outcomes it names (+23), the third playbook, and the spine's write-based seat rule (D2), the adopted
# prototype's `Prototype:` forms, D1's governing-spec read, D3/D4's run-as-is and unshown-state settlement
# and D7's write-keyed commit (+16).
DESIGNER_BAR = 380

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

    def test_table_maps_exactly_the_three_modes(self):
        self.assertEqual(self.rows, [("fresh", "playbooks/fresh.md"), ("revise", "playbooks/revise.md"),
                                     ("adopt", "playbooks/adopt.md")])

    def test_table_matches_prep_suggested_playbook(self):
        for mode, playbook in self.rows:
            with self.subTest(mode=mode):
                self.assertEqual("playbooks/" + prep_designer._suggested_playbook(mode), playbook)
                self.assertTrue((SKILL_DIR / playbook).is_file())

    def test_every_playbook_reads_the_spine(self):
        for name in ("fresh.md", "revise.md", "adopt.md"):
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

    def test_an_untrusted_thread_direction_never_binds_the_design(self):
        # SEC-1: only the body's DoD and a trusted author's direction bind; any other ask goes to the operator.
        for name in ("fresh.md", "revise.md", "adopt.md"):
            text = " ".join((PLAYBOOKS_DIR / name).read_text(encoding="utf-8").split())
            with self.subTest(playbook=name):
                self.assertIn("never built to on its say" if name == "revise.md" else "never something to build to on its say", text)
        fresh = " ".join((PLAYBOOKS_DIR / "fresh.md").read_text(encoding="utf-8").split())
        self.assertNotIn("any `## UI decisions` the thread carries are what the design must serve", fresh)

    def test_thread_readers_carry_the_author_rule(self):
        for name in ("fresh.md", "revise.md", "adopt.md"):
            text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
            for anchor in THREAD_RULE_ANCHORS:
                with self.subTest(playbook=name, anchor=anchor):
                    self.assertIn(anchor, text)
        # #227: the router's pre-route match reads the thread too, so it carries the rule.
        rule = ROUTER.read_text(encoding="utf-8").split("**Existing prototypes.**", 1)[1].split("**Refusals.**", 1)[0]
        for anchor in THREAD_RULE_ANCHORS:
            with self.subTest(router=True, anchor=anchor):
                self.assertIn(anchor, rule)


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
                      " ".join(self.spine.split()))
        attach_section = self.spine.split("- **Captures to attach.**", 1)[1].split("- **No capture to attach**", 1)[0]
        self.assertIn("`Approval: pending`", attach_section)
        before_edit = attach_section.split("gh_persist.py edit-comment", 1)[0]
        approving = " ".join(before_edit.rsplit("Once a post lands", 1)[1].split())
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

    def test_a_revise_single_write_restages_from_the_posted_body(self):
        flat = " ".join(self.spine.split())
        single = flat.split("**No capture to attach**", 1)[1].split("## D9", 1)[0]
        self.assertIn("A revise stages from `facts.record.body_path`", single)
        self.assertIn("A capture that names a local absolute path is a capture to attach", single)

    def test_pending_revise_posts_nothing_before_approve(self):
        revise = " ".join((PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8").split())
        pending = revise.split("## A `pending` record", 1)[1].split("## An approved record", 1)[0]
        self.assertIn("Nothing posts before **Approve**", pending)
        self.assertLess(pending.index("D1 and D2 first"), pending.index("(`Approve`)"))
        self.assertNotIn("re-post", pending.split("(`Approve`)", 1)[0])

    def test_attach_failure_exits_are_bounded(self):
        flat = " ".join(self.spine.split())
        self.assertIn("`ATTACH_UNSUPPORTED:` on stderr", flat)
        self.assertIn("never restage the record without its captures", flat)
        self.assertIn("A capture that has failed to upload twice stops the write", flat)
        router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.assertIn("a capture that fails to upload twice) emits no handoff", router)

    def test_binding_doc_card_always_renders(self):
        self.assertIn("it always renders, never collapsed into an announcement", " ".join(self.spine.split()))

    def test_workspace_card_covers_a_tracked_capture_store(self):
        # #227: D2 decides on what the session writes; a capture into a tracked store is still a tracked write.
        flat = " ".join(self.spine.split())
        self.assertIn("**`root`** and this session will write tracked files — a prototype it builds or edits in "
                      "them, or captures it stores in a tracked store — the operator's checkout is never written", flat)

    def test_d2_decides_on_writes_and_spares_a_read_only_adoption_the_card(self):
        d2 = " ".join(self.spine.split("## D2 ", 1)[1].split("## D3 ", 1)[0].split())
        self.assertIn("D2 decides on what this session **writes**, not on where a prototype lives", d2)
        self.assertIn("or a read-only adoption (`facts.existing.adopted`) whose captures are attached — proceed "
                      "with no card", d2)
        self.assertIn("An iteration edit to an adopted prototype, or a capture into a tracked store, re-enters D2 "
                      "before its first write", d2)

    def test_prototype_names_an_adoption_read_at_its_commit(self):
        d2 = " ".join(self.spine.split("## D2 ", 1)[1].split("## D3 ", 1)[0].split())
        self.assertIn("`` `<path>` — existing, read at `<branch>@<12-hex sha>` ``", d2)
        revise = " ".join((PLAYBOOKS_DIR / "revise.md").read_text(encoding="utf-8").split())
        self.assertIn("existing, read at `<branch>@<sha>` ``, is re-opened at its path in this checkout", revise)

    def test_d7_and_the_router_invariant_key_on_what_the_session_wrote(self):
        d7 = " ".join(self.spine.split("## D7 ", 1)[1].split("## D8 ", 1)[0].split())
        self.assertIn("When this session wrote tracked files", d7)
        self.assertIn("A read-only adoption wrote nothing tracked, so it skips this step", d7)
        router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.assertIn("A prototype or capture this session wrote to tracked files is committed and pushed before "
                      "the record posts", router)
        self.assertIn("an adopted prototype is only read in place", router)
        self.assertIn("**Tracked writes only at `own-branch`.**", router)

    def test_d4_settles_every_state_an_adopted_prototype_does_not_show(self):
        d4 = " ".join(self.spine.split("## D4 ", 1)[1].split("## D5 ", 1)[0].split())
        self.assertIn("Each proposed state an adopted prototype does not show is settled with the operator", d4)
        self.assertIn("No state is recorded without a capture", d4)

    def test_workspace_card_command_carries_design(self):
        # #205 phase 2, R2.5: the open the card asks for routes the restarted session back to the designer.
        self.assertIn("/ctrl-freaq:workspace-open <N> --design", [f.strip() for f in self.fences])

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

    def test_every_header_fits_and_the_four_cards_exist(self):
        headers = []
        for path in _skill_files():
            headers.extend(self._HEADER.findall(path.read_text(encoding="utf-8")))
        self.assertEqual(sorted(set(headers)), ["Approve", "Convention", "Design doc", "Prototype", "Workspace"])
        for header in headers:
            with self.subTest(header=header):
                self.assertLessEqual(len(header), 12)

    def test_relayed_card_identity_line(self):
        self.assertIn("`designer · #<N> · <step>`", ROUTER.read_text(encoding="utf-8"))


class AdoptionTests(unittest.TestCase):
    """#227: the router matches only from prep's list with a quoted tie, every new prep outcome is said, and
    adopt.md refuses a read-only adoption a later checkout could not read."""

    def setUp(self):
        self.router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.rule = self.router.split("**Existing prototypes.**", 1)[1].split("**Refusals.**", 1)[0]
        self.adopt = " ".join((PLAYBOOKS_DIR / "adopt.md").read_text(encoding="utf-8").split())

    def test_the_match_picks_only_from_the_list_with_a_quoted_tie(self):
        self.assertIn("Pick only from the `prototype_candidates` list", self.rule)
        self.assertIn("a pick without a quote is not a pick", self.rule)
        self.assertIn("re-run prep with `--adopt <path>`", self.rule)
        self.assertIn("**Design fresh** with `--no-adopt`", self.rule)
        self.assertIn('the judgment card (`header: "Prototype"`)', self.rule)
        self.assertIn("Carry the flag on every later prep run", self.rule)

    def test_each_new_prep_outcome_is_said_before_routing(self):
        self.assertIn("an `attention` line naming the `--adopt` path → it is not a tracked file at this checkout, "
                      "and the `Prototype` card renders again", self.rule)
        for token in ("`UI_PROTOTYPE_DECLARATION_MALFORMED`", "`UI_PROTOTYPE_LIST_UNAVAILABLE`",
                      "`facts.existing.findings`"):
            with self.subTest(token=token):
                self.assertIn(token, self.rule)
        self.assertIn("say the fresh route runs", self.rule)

    def test_adopt_refuses_a_modified_or_unpublished_read_only_adoption(self):
        self.assertIn("`modified` true", self.adopt)
        self.assertIn("`published` false", self.adopt)
        self.assertIn("Never record a read-only adoption of such a file", self.adopt)

    def test_the_block_sets_no_authority(self):
        self.assertIn("the block's prose never makes a spec `binding` or `informative`", self.adopt)
        contract = " ".join((SHARED / "design-record.md").read_text(encoding="utf-8").split())
        self.assertIn("The block's prose sets no document's authority", contract)

    def test_adopt_never_rebuilds_unless_asked(self):
        self.assertIn("Never rebuild or copy the prototype unless the operator asks to iterate on it", self.adopt)


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
                # Positive control: the identity line the markers would sit on is in the extracted shape.
                self.assertRegex(shape, r"\*\*(Issue|Story):\*\* #\d+ — .+ · open · (feature|story)\n")
                self.assertNotIn("research:", shape)
                self.assertNotIn("plan:", shape)

    def test_story_shape_carries_story_and_epic_lines(self):
        story = [s for s in self.shapes if "**Story:**" in s]
        self.assertEqual(len(story), 1)
        self.assertRegex(story[0], r"\*\*Epic:\*\* #\d+ — .+ · open \(\d+ of \d+ stories closed\)")

    def test_workspace_line_names_the_worktree_at_seat_own_branch(self):
        # #205 phase 2, R2.6: the one sanctioned addition to a copied shape, before `**Next:**`.
        prose = " ".join(RENDERINGS.read_text(encoding="utf-8").split())
        self.assertIn("At seat `own-branch` (`facts.workspace.seat`) add `**Workspace:** <facts.workspace.path> — start the next session here` on its own line before `**Next:**`", prose)
        self.assertIn("omitted at seat `root`", prose)
        self.assertIn("the one sanctioned addition to a copied shape", prose)

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
        # #205 phase 2, R2.6: the designer → planner exception beside the planner → designer one.
        self.assertIn("a designer → planner handoff from a designer that sat in the issue's worktree (seat `own-branch`) names it", text)

    def test_prd_appends_section_5_7_and_records_the_marker(self):
        text = PRD.read_text(encoding="utf-8")
        section_5 = text.split("## §5 ", 1)[1].split("## §6 ", 1)[0]
        self.assertGreater(section_5.index("- **§5.7 designer.**"), section_5.index("- **§5.6 slicer.**"))
        section_7 = text.split("## §7 ", 1)[1].split("## §8 ", 1)[0]
        self.assertIn("| `<!-- ux-design:v1 -->` design record", section_7)
        self.assertIn("`ui-prototype` (user-owned, #203)", section_7)


class ConventionsStepTests(unittest.TestCase):
    """#207: the designer offers the UX conventions the operator stated as one follow-up, after approval."""

    def setUp(self):
        self.spine = SPINE.read_text(encoding="utf-8")
        self.flat = " ".join(self.spine.split())

    def _section(self, heading, end):
        return " ".join(self.spine.split(heading, 1)[1].split(end, 1)[0].split())

    def test_d9_conventions_sits_between_d8_and_d10_handoff(self):
        d8, d9, d10 = (self.spine.index(h) for h in ("## D8 ", "## D9 — Conventions", "## D10 — Handoff"))
        self.assertLess(d8, d9)
        self.assertLess(d9, d10)
        self.assertNotIn("## D9 — Handoff", self.spine)
        # The D8 bound WriteOrderTests splits on still ends D8 at the conventions step.
        single = self.flat.split("**No capture to attach**", 1)[1].split("## D9", 1)[0]
        self.assertNotIn("## D10", single)
        self.assertIn("edit-comment <facts.record.comment_id>", single)

    def test_d9_links_the_protocol_and_renders_the_consent_card(self):
        d9 = self._section("## D9 — Conventions", "## D10 — Handoff")
        self.assertIn("(../../_shared/follow-up-filing.md)", d9)
        self.assertIn('`header: "Convention"`', d9)
        self.assertIn("**File as follow-up** / **Don't file**", d9)
        self.assertIn("after D8's approving write", d9)
        self.assertIn("a session that ends without **Approve** files nothing", d9)
        self.assertIn("A filing error is reported in one line and ends nothing", d9)

    def test_conventions_are_noted_from_d3_without_asking(self):
        d3 = self._section("## D3 — Build and run the prototype", "## D4")
        self.assertIn("From here through D6", d3)
        self.assertIn("without stopping to ask; D9 offers it", d3)
        self.assertIn("the notes on D6's **Approve** answer", d3)
        d8 = self._section("## D8 ", "## D9 — Conventions")
        self.assertIn("Every stop below ends before D9, so its stop line also lists the conventions noted in D3–D6 "
                      "as not offered", d8)

    def test_d7_never_commits_the_ui_design_doc(self):
        d7 = self._section("## D7 ", "## D8 ")
        self.assertIn("The commit never carries the ui-design doc or the doc catalogue", d7)

    def test_router_names_the_card_and_the_step(self):
        router = " ".join(ROUTER.read_text(encoding="utf-8").split())
        self.assertIn("the `Convention` consent card (spine D9, after approval)", router)
        self.assertIn("persist → conventions → handoff", router)
        self.assertIn("and a convention follow-up the operator approved", router)
        # The handoff still forwards only to the planner.
        self.assertIn("Every shape forwards to the planner, `/ctrl-freaq:planner #<N>`.", router)


class StructuralBarTests(unittest.TestCase):
    def test_router_plus_spine_plus_largest_playbook_within_the_measured_bar(self):
        router_lines = len(ROUTER.read_text(encoding="utf-8").splitlines())
        spine_lines = len(SPINE.read_text(encoding="utf-8").splitlines())
        playbook_lines = {
            p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in PLAYBOOKS_DIR.glob("*.md") if p != SPINE
        }
        largest = max(playbook_lines.values())
        total = router_lines + spine_lines + largest
        self.assertLessEqual(
            total,
            DESIGNER_BAR,
            "router (%d) + spine (%d) + largest playbook (%d) = %d exceeds the measured bar %d: %r"
            % (router_lines, spine_lines, largest, total, DESIGNER_BAR, playbook_lines),
        )


def _section(text, heading):
    """The text from the line ``heading`` starts to the next heading of the same or a higher level."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    in_fence = False
    for i in range(start + 1, len(lines)):
        if lines[i].strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue  # a `# comment` line in a fenced block is not a heading
        stripped = lines[i].lstrip("#")
        hashes = len(lines[i]) - len(stripped)
        if 0 < hashes <= level and stripped.startswith(" "):
            end = i
            break
    return "\n".join(lines[start:end])


class EpicDocRealityTests(unittest.TestCase):
    """#208's doc-reality sweep (epic #201): the facts a later edit could silently drop the designer from."""

    DIAGRAM = "draft ──▶ research ──▶ slice ──▶ design ──▶ plan ──▶ resolve ──▶ evaluate"

    def setUp(self):
        self.claude = CLAUDE_MD.read_text(encoding="utf-8")
        self.readme = README.read_text(encoding="utf-8")
        self.architecture = ARCHITECTURE.read_text(encoding="utf-8")
        self.prd = PRD.read_text(encoding="utf-8")

    def test_claude_md_counts_seven_stages(self):
        self.assertIn("\n### Seventeen skills: seven pipeline stages, ten standalone tools\n", self.claude)

    def test_claude_md_lists_the_designer_scripts(self):
        repo = _section(self.claude, "## What this repo is")
        self.assertIn("`scripts/prep_designer.py`", repo)
        self.assertIn("`scripts/design_record.py`", repo)
        self.assertIn("the sixteen `prep_*.py`", repo)

    def test_claude_md_names_the_record_and_the_block(self):
        coupling = _section(self.claude, "### Coupling to a consuming repo is convention-driven")
        self.assertIn("`<!-- ux-design:v1 -->`", coupling)
        self.assertIn("`<!-- ui-prototype -->`", coupling)

    def test_the_diagram_names_the_design_stage(self):
        for name, text in (("README.md", self.readme), ("CLAUDE.md", self.claude)):
            with self.subTest(doc=name):
                self.assertIn(self.DIAGRAM, text.splitlines())

    def test_readme_has_a_designer_row(self):
        pipeline = _section(self.readme, "## The pipeline")
        self.assertTrue(any(line.startswith("| `/ctrl-freaq:designer` |") for line in pipeline.splitlines()))

    def test_architecture_layout_names_the_designer_scripts(self):
        overview = _section(self.architecture, "## §1 System overview & boundaries")
        layout = overview.split("**Repo layout", 1)[1]
        self.assertIn("prep_designer.py", layout)
        self.assertIn("design_record.py", layout)

    def test_architecture_thread_sources_keep_design_at_light(self):
        section = _section(self.architecture, "## §8 Judgment sub-agents")
        flat = " ".join(section.split())
        sentence = flat.split("Which kinds post is `steps.thread_sources`", 1)[1].split("A sensitive-path match", 1)[0]
        light = sentence.split(";")[-1]
        self.assertIn("at `light`", light)
        self.assertIn("design", light)

    def test_prd_designer_names_the_convention_follow_up(self):
        designer = self.prd.split("- **§5.7 designer.**", 1)[1].split("\n## ", 1)[0]
        self.assertIn("follow-up-filing.md", designer)
        self.assertIn("convention", designer)


if __name__ == "__main__":
    unittest.main()
