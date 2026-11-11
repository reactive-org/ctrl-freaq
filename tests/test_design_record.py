"""Unit tests for scripts/design_record.py — the design record's and the ``ui-prototype`` block's read
model (``skills/_shared/design-record.md``, #202).

Pure functions over inline thread dicts and temp files — no `gh`, no `git`, no shim. What this suite
protects, because later stories (#203–#206) build on it:

  - **``approved`` fails closed.** A record reads approved only with a valid approval date, at least one
    state, a capture on every state and no findings; each way short of that is pinned, with a complete
    record as the positive control.
  - **Only a trusted record counts.** Untrusted copies are partitioned out before duplicates are
    counted, so one can neither be the record nor make it ambiguous; the notice names no comment text.
  - **The grammar is the contract.** Line labels, the state-id shape, the capture split and the CRLF
    tolerance are pinned so a writer and the reader cannot drift.
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

import config_block  # noqa: E402  (import after sys.path setup, by necessity)
import design_record  # noqa: E402
from pipelib.decisions import MARKER_AMBIGUOUS  # noqa: E402
from pipelib.trust import MARKER_UNTRUSTED  # noqa: E402

CONTRACT = REPO_ROOT / "skills" / "_shared" / "design-record.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
PRD = REPO_ROOT / "docs" / "prd.md"
MODULE = SCRIPTS_DIR / "design_record.py"

COMPLETE = """<!-- ux-design:v1 -->
**Design record** — #42 Show the weekly summary — recorded 2026-11-10T09:00:00Z

Prototype: prototypes/weekly-summary/

## States
- `empty` — no entries yet; a prompt invites the first one — capture: ![empty](https://github.com/user-attachments/assets/a1)
- `loaded` — the week's entries, newest first — capture: prototypes/weekly-summary/captures/loaded.png
- `error` — the fetch failed; a retry button shows — capture: https://github.com/user-attachments/assets/a3

Approval: approved 2026-11-10
"""


def _comment(body, association="MEMBER", database_id=101, url="https://github.com/o/r/issues/42#c1"):
    comment = {
        "id": "IC_node_%s" % database_id,
        "databaseId": database_id,
        "author": {"login": "someone"},
        "body": body,
        "createdAt": "2026-11-10T09:00:00Z",
        "url": url,
    }
    if association is not None:
        comment["authorAssociation"] = association
    return comment


def _reasons(parsed):
    return [f["reason"] for f in parsed["findings"]]


class ParseRecordTests(unittest.TestCase):
    def test_complete_record_is_approved(self):
        parsed = design_record.parse_record(COMPLETE)
        self.assertTrue(parsed["approved"])
        self.assertEqual(parsed["findings"], [])
        self.assertEqual(parsed["prototype"], "prototypes/weekly-summary/")
        self.assertEqual([s["id"] for s in parsed["states"]], ["empty", "loaded", "error"])
        self.assertEqual(parsed["approval"], "approved")
        self.assertEqual(parsed["approved_on"], "2026-11-10")

    def test_markdown_image_capture_is_kept_verbatim(self):
        parsed = design_record.parse_record(COMPLETE)
        self.assertEqual(parsed["states"][0]["capture"], "![empty](https://github.com/user-attachments/assets/a1)")
        self.assertEqual(parsed["states"][0]["description"], "no entries yet; a prompt invites the first one")

    def test_a_state_without_capture_is_not_approved(self):
        body = COMPLETE.replace("capture: prototypes/weekly-summary/captures/loaded.png", "capture: (none)")
        parsed = design_record.parse_record(body)
        self.assertFalse(parsed["approved"])
        self.assertEqual(parsed["findings"], [])
        self.assertIsNone(parsed["states"][1]["capture"])

    def test_an_empty_capture_reference_is_none(self):
        body = COMPLETE.replace(" prototypes/weekly-summary/captures/loaded.png", "")
        parsed = design_record.parse_record(body)
        self.assertEqual(parsed["findings"], [])
        self.assertIsNone(parsed["states"][1]["capture"])
        self.assertFalse(parsed["approved"])

    def test_pending_approval_is_not_approved(self):
        parsed = design_record.parse_record(COMPLETE.replace("Approval: approved 2026-11-10", "Approval: pending"))
        self.assertEqual(parsed["approval"], "pending")
        self.assertIsNone(parsed["approved_on"])
        self.assertEqual(parsed["findings"], [])
        self.assertFalse(parsed["approved"])

    def test_no_state_lines_is_not_approved(self):
        body = "\n".join(line for line in COMPLETE.split("\n") if not line.startswith("- `"))
        parsed = design_record.parse_record(body)
        self.assertEqual(parsed["states"], [])
        self.assertFalse(parsed["approved"])

    def test_malformed_state_line_is_a_finding_with_its_line_number(self):
        body = COMPLETE.replace("- `loaded` —", "- loaded —")
        parsed = design_record.parse_record(body)
        self.assertFalse(parsed["approved"])
        self.assertEqual(len(parsed["findings"]), 1)
        finding = parsed["findings"][0]
        self.assertEqual(finding["reason"], design_record.REASON_STATE_MALFORMED)
        self.assertEqual(finding["line_number"], 8)
        self.assertTrue(finding["raw_line"].startswith("- loaded —"))
        # The other states still parse.
        self.assertEqual([s["id"] for s in parsed["states"]], ["empty", "error"])

    def test_state_line_without_capture_separator_is_malformed(self):
        body = COMPLETE.replace(" — capture: prototypes/weekly-summary/captures/loaded.png", "")
        parsed = design_record.parse_record(body)
        self.assertEqual(_reasons(parsed), [design_record.REASON_STATE_MALFORMED])
        self.assertFalse(parsed["approved"])

    def test_uppercase_state_id_is_malformed(self):
        parsed = design_record.parse_record(COMPLETE.replace("`empty`", "`Empty`"))
        self.assertEqual(_reasons(parsed), [design_record.REASON_STATE_MALFORMED])
        self.assertFalse(parsed["approved"])

    def test_duplicate_state_id_is_a_finding(self):
        parsed = design_record.parse_record(COMPLETE.replace("`error`", "`empty`"))
        self.assertEqual(_reasons(parsed), [design_record.REASON_STATE_DUPLICATE])
        self.assertEqual([s["id"] for s in parsed["states"]], ["empty", "loaded"])
        self.assertFalse(parsed["approved"])

    def test_missing_prototype_line_is_a_finding(self):
        parsed = design_record.parse_record(COMPLETE.replace("Prototype: prototypes/weekly-summary/\n", ""))
        self.assertEqual(_reasons(parsed), [design_record.REASON_PROTOTYPE_MISSING])
        self.assertIsNone(parsed["findings"][0]["line_number"])
        self.assertIsNone(parsed["findings"][0]["raw_line"])
        self.assertIsNone(parsed["prototype"])
        self.assertFalse(parsed["approved"])

    def test_duplicated_prototype_line_is_a_finding(self):
        body = COMPLETE.replace("## States", "Prototype: elsewhere/\n\n## States")
        parsed = design_record.parse_record(body)
        self.assertEqual(_reasons(parsed), [design_record.REASON_PROTOTYPE_DUPLICATE])
        self.assertEqual(parsed["prototype"], "prototypes/weekly-summary/")
        self.assertFalse(parsed["approved"])

    def test_duplicated_approval_line_is_a_finding(self):
        parsed = design_record.parse_record(COMPLETE + "Approval: pending\n")
        self.assertEqual(_reasons(parsed), [design_record.REASON_APPROVAL_DUPLICATE])
        self.assertFalse(parsed["approved"])

    def test_state_shaped_line_outside_states_section_is_a_finding_never_a_state(self):
        stray = "- `stray` — outside the section — capture: (none)"
        for body in (
            COMPLETE.replace("## States", stray + "\n\n## States"),
            COMPLETE + stray + "\n",
            COMPLETE.replace("## States\n", ""),
            COMPLETE + "- `Offline` — malformed id below the approval — capture: (none)\n",
            COMPLETE + "- offline — no code span — capture: (none)\n",
            COMPLETE + "* `offline` — another list marker — capture: (none)\n",
            COMPLETE + "+ `offline` — another list marker — capture: (none)\n",
            COMPLETE + "1. `offline` — a numbered item — capture: (none)\n",
            COMPLETE + "2) `offline` — a numbered item — capture: (none)\n",
        ):
            with self.subTest(body=body[-80:]):
                parsed = design_record.parse_record(body)
                self.assertNotIn("stray", [s["id"] for s in parsed["states"]])
                self.assertEqual(len(parsed["states"]), 3 if "## States" in body else 0)
                self.assertIn(design_record.REASON_STATE_OUTSIDE, _reasons(parsed))
                self.assertFalse(parsed["approved"])

    def test_thematic_break_outside_states_is_not_a_list_item(self):
        for rule in ("* * *", "- - -", "***"):
            with self.subTest(rule=rule):
                parsed = design_record.parse_record(COMPLETE + rule + "\n")
                self.assertEqual(parsed["findings"], [])
                self.assertTrue(parsed["approved"])

    def test_empty_prototype_value_is_a_finding(self):
        parsed = design_record.parse_record(COMPLETE.replace("Prototype: prototypes/weekly-summary/", "Prototype:"))
        self.assertIsNone(parsed["prototype"])
        self.assertEqual(_reasons(parsed), [design_record.REASON_PROTOTYPE_MISSING])
        self.assertEqual(parsed["findings"][0]["line_number"], 4)
        self.assertFalse(parsed["approved"])

    def test_capture_separator_needs_its_space(self):
        parsed = design_record.parse_record(COMPLETE.replace("capture: prototypes/", "capture:prototypes/"))
        self.assertEqual(_reasons(parsed), [design_record.REASON_STATE_MALFORMED])
        self.assertFalse(parsed["approved"])

    def test_non_matching_line_inside_states_section_is_a_finding(self):
        body = COMPLETE.replace("## States\n", "## States\nSome prose that is not a state.\n")
        parsed = design_record.parse_record(body)
        self.assertEqual(_reasons(parsed), [design_record.REASON_STATE_MALFORMED])
        self.assertFalse(parsed["approved"])

    def test_no_approval_line_parses_states_to_end_of_body(self):
        body = COMPLETE.replace("Approval: approved 2026-11-10\n", "")
        body += "- `offline` — no network; cached entries show — capture: offline.png\n"
        parsed = design_record.parse_record(body)
        self.assertEqual(_reasons(parsed), [design_record.REASON_APPROVAL_MISSING])
        self.assertIsNone(parsed["approval"])
        self.assertEqual([s["id"] for s in parsed["states"]], ["empty", "loaded", "error", "offline"])
        self.assertFalse(parsed["approved"])

    def test_unrecognised_approval_value_is_a_finding(self):
        for value in ("maybe", "approved 2026-02-30", "approved 10/11/2026", "approved"):
            with self.subTest(value=value):
                parsed = design_record.parse_record(
                    COMPLETE.replace("Approval: approved 2026-11-10", "Approval: %s" % value)
                )
                self.assertIsNone(parsed["approval"])
                self.assertIsNone(parsed["approved_on"])
                self.assertEqual(_reasons(parsed), [design_record.REASON_APPROVAL_UNRECOGNISED])
                self.assertFalse(parsed["approved"])

    def test_description_with_its_own_em_dash_splits_at_the_last_capture_separator(self):
        line = "- `saved` — the entry saves — a toast confirms — capture: saved.png"
        body = COMPLETE.replace("## States\n", "## States\n%s\n" % line)
        parsed = design_record.parse_record(body)
        self.assertEqual(parsed["findings"], [])
        saved = parsed["states"][0]
        self.assertEqual(saved["id"], "saved")
        self.assertEqual(saved["description"], "the entry saves — a toast confirms")
        self.assertEqual(saved["capture"], "saved.png")

    def test_capture_begins_at_the_last_separator_when_the_description_repeats_it(self):
        line = "- `hint` — a tip reading — capture: tap to snap — capture: hint.png"
        parsed = design_record.parse_record(COMPLETE.replace("## States\n", "## States\n%s\n" % line))
        hint = parsed["states"][0]
        self.assertEqual(hint["description"], "a tip reading — capture: tap to snap")
        self.assertEqual(hint["capture"], "hint.png")

    def test_crlf_body_and_trailing_whitespace_parse_the_same(self):
        crlf = COMPLETE.replace("\n", "  \r\n")
        self.assertEqual(design_record.parse_record(crlf), design_record.parse_record(COMPLETE))

    def test_non_string_body_is_empty_and_unapproved(self):
        for body in (None, 7, ""):
            with self.subTest(body=body):
                parsed = design_record.parse_record(body)
                self.assertFalse(parsed["approved"])
                self.assertEqual(parsed["states"], [])


class ReadRecordTests(unittest.TestCase):
    def test_one_trusted_record_is_present(self):
        record, notices, decision = design_record.read_record([_comment(COMPLETE)], issue=42)
        self.assertIsNone(decision)
        self.assertEqual(notices, [])
        self.assertTrue(record["present"])
        self.assertEqual(record["comment_id"], 101)
        self.assertEqual(record["url"], "https://github.com/o/r/issues/42#c1")
        self.assertEqual(record["created_at"], "2026-11-10T09:00:00Z")
        self.assertTrue(record["approved"])

    def test_untrusted_only_record_is_absent_with_one_notice(self):
        for association in ("NONE", "CONTRIBUTOR", None):
            with self.subTest(association=association):
                record, notices, decision = design_record.read_record(
                    [_comment(COMPLETE, association=association)], issue=42
                )
                self.assertIsNone(decision)
                self.assertFalse(record["present"])
                self.assertFalse(record["approved"])
                self.assertEqual(len(notices), 1)
                self.assertTrue(notices[0].startswith(MARKER_UNTRUSTED + ":"))
                self.assertIn("issue #42", notices[0])
                self.assertIn(design_record.MARKER, notices[0])
                # No comment text, URL or author leaks into the notice.
                self.assertNotIn("weekly", notices[0])
                self.assertNotIn("github.com", notices[0])
                self.assertNotIn("someone", notices[0])

    def test_trusted_beside_untrusted_reads_the_trusted_one_and_still_notices(self):
        forged = _comment(COMPLETE.replace("prototypes/weekly-summary/\n", "evil/\n"), association="NONE",
                          database_id=202)
        record, notices, decision = design_record.read_record([forged, _comment(COMPLETE)], issue=42)
        self.assertIsNone(decision)
        self.assertTrue(record["present"])
        self.assertEqual(record["comment_id"], 101)
        self.assertEqual(record["prototype"], "prototypes/weekly-summary/")
        self.assertEqual(len(notices), 1)

    def test_two_trusted_records_are_ambiguous(self):
        thread = [_comment(COMPLETE), _comment(COMPLETE, database_id=102, url="https://x/c2")]
        record, notices, decision = design_record.read_record(thread, issue=42)
        self.assertFalse(record["present"])
        self.assertEqual(decision["code"], MARKER_AMBIGUOUS)
        self.assertEqual(decision["context"]["marker_prefix"], design_record.MARKER)
        self.assertEqual(decision["context"]["comment_ids"], [101, 102])
        self.assertEqual(decision["context"]["comment_urls"], ["https://github.com/o/r/issues/42#c1", "https://x/c2"])
        self.assertEqual(len(decision["options"]), 2)

    def test_an_untrusted_copy_never_makes_a_trusted_record_ambiguous(self):
        thread = [_comment(COMPLETE), _comment(COMPLETE, association="NONE", database_id=102)]
        record, _notices, decision = design_record.read_record(thread)
        self.assertIsNone(decision)
        self.assertTrue(record["present"])

    def test_marker_not_on_the_first_line_is_not_a_record(self):
        for body in ("Quoting:\n" + COMPLETE, "See `" + design_record.MARKER + "` in the contract."):
            with self.subTest(body=body[:20]):
                record, notices, decision = design_record.read_record([_comment(body)])
                self.assertFalse(record["present"])
                self.assertEqual(notices, [])
                self.assertIsNone(decision)

    def test_crlf_marker_line_with_trailing_whitespace_is_a_record(self):
        record, _notices, _decision = design_record.read_record([_comment(COMPLETE.replace("\n", " \r\n"))])
        self.assertTrue(record["present"])
        self.assertTrue(record["approved"])

    def test_empty_or_missing_thread_is_absent_with_every_key(self):
        complete_keys = set(design_record.read_record([_comment(COMPLETE)])[0])
        for thread in ([], None):
            with self.subTest(thread=thread):
                record, notices, decision = design_record.read_record(thread)
                self.assertEqual(set(record), complete_keys)
                self.assertFalse(record["present"])
                self.assertFalse(record["approved"])
                self.assertEqual(record["states"], [])
                self.assertEqual(notices, [])
                self.assertIsNone(decision)

    def test_untrusted_record_without_issue_names_no_thread(self):
        _record, notices, _decision = design_record.read_record([_comment(COMPLETE, association="NONE")])
        self.assertEqual(len(notices), 1)
        self.assertNotIn("issue", notices[0])


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _block(interior):
    return "<!-- ui-prototype -->\n%s<!-- /ui-prototype -->\n" % interior


class ReadBlockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_config_file_reads_absent(self):
        self.assertEqual(design_record.read_block(str(self.root)),
                         {"present": False, "source": None, "text": None})

    def test_block_in_an_uncommitted_claude_md_is_read_with_its_absolute_source(self):
        _write(self.root / "CLAUDE.md", "# Repo\n\n" + _block("Prototypes live in `proto/`.\nCapture with the app's screenshot script.\n"))
        block = design_record.read_block(str(self.root))
        self.assertTrue(block["present"])
        self.assertEqual(block["text"], "Prototypes live in `proto/`.\nCapture with the app's screenshot script.")
        self.assertEqual(block["source"], str(self.root / "CLAUDE.md"))
        self.assertTrue(Path(block["source"]).is_absolute())

    def test_commands_md_wins_over_claude_md(self):
        _write(self.root / "CLAUDE.md", _block("from claude\n"))
        _write(self.root / "COMMANDS.md", _block("from commands\n"))
        block = design_record.read_block(str(self.root))
        self.assertEqual(block["text"], "from commands")
        self.assertEqual(block["source"], str(self.root / "COMMANDS.md"))

    def test_whitespace_only_block_reads_absent(self):
        _write(self.root / "CLAUDE.md", _block("   \n\n"))
        self.assertFalse(design_record.read_block(str(self.root))["present"])

    def test_unreadable_candidate_file_reads_absent(self):
        (self.root / "COMMANDS.md").write_bytes(b"\xff\xfe not utf-8\n" + _block("x\n").encode("utf-8"))
        self.assertEqual(design_record.read_block(str(self.root)),
                         {"present": False, "source": None, "text": None})


class LocateTests(unittest.TestCase):
    """#203: ``locate`` is the raw discovery ``read_block`` and setup's re-ingest base share — exactly
    ``config_block.read_block_anywhere``'s triple, with a read error folded into the absent one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _same_as_the_config_block_read(self):
        found = design_record.locate(str(self.root))
        self.assertEqual(found, config_block.read_block_anywhere(str(self.root), design_record.BLOCK_MARKER))
        return found

    def test_present_block(self):
        _write(self.root / "CLAUDE.md", _block("Prototypes live in `proto/`.\n"))
        self.assertEqual(self._same_as_the_config_block_read(),
                         (True, ["Prototypes live in `proto/`."], str(self.root / "CLAUDE.md")))

    def test_empty_block_is_found_with_no_lines(self):
        _write(self.root / "COMMANDS.md", _block(""))
        self.assertEqual(self._same_as_the_config_block_read(), (True, [], str(self.root / "COMMANDS.md")))
        self.assertFalse(design_record.read_block(str(self.root))["present"])

    def test_absent_block(self):
        _write(self.root / "CLAUDE.md", "# Repo\n")
        self.assertEqual(self._same_as_the_config_block_read(), (False, [], None))

    def test_included_block(self):
        _write(self.root / "CLAUDE.md", "# Repo\n\n@docs/proto.md\n")
        (self.root / "docs").mkdir()
        _write(self.root / "docs" / "proto.md", _block("from the include\n"))
        found = self._same_as_the_config_block_read()
        self.assertTrue(found[0])
        self.assertEqual(found[1], ["from the include"])
        self.assertEqual(Path(found[2]).resolve(), (self.root / "docs" / "proto.md").resolve())

    def test_non_utf8_candidate_file_reads_absent(self):
        (self.root / "COMMANDS.md").write_bytes(b"\xff\xfe\n")
        self.assertEqual(design_record.locate(str(self.root)), (False, [], None))


class ContractPinTests(unittest.TestCase):
    def test_contract_file_carries_every_token(self):
        text = CONTRACT.read_text(encoding="utf-8")
        for token in (
            design_record.MARKER,
            design_record.PROTOTYPE_LABEL,
            design_record.STATES_HEADING,
            design_record.CAPTURE_SEP,
            design_record.APPROVAL_LABEL,
            design_record.NO_CAPTURE,
            "`<!-- ui-prototype -->`",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_architecture_registry_names_both_tokens(self):
        section = ARCHITECTURE.read_text(encoding="utf-8").split("## §12 Invariants registry", 1)[1]
        self.assertIn("<!-- ux-design:v1 -->", section)
        self.assertIn("<!-- ui-prototype -->", section)
        self.assertIn("design_record.read_record", section)

    def test_prd_admits_the_designer_stage(self):
        text = PRD.read_text(encoding="utf-8")
        section_2 = text.split("## §2 ", 1)[1].split("## §3 ", 1)[0]
        section_3 = text.split("## §3 ", 1)[1].split("## §4 ", 1)[0]
        self.assertIn("`designer`", section_2)
        self.assertNotIn("No new pipeline stages, no reordering or merging of stages.", section_3)
        self.assertIn("designer pipeline stage, making seventeen", section_3)

    def test_module_is_import_only(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertFalse(text.startswith("#!"))
        self.assertNotIn("\ndef main(", text)


if __name__ == "__main__":
    unittest.main()
