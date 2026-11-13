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

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
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


# Calls no function but `design_record.main` may make: I/O, exiting, or spawning a process.
_IMPURE_CALLS = {"open", "print", "read_text", "write_text", "exit", "run", "Popen"}
# The named I/O edges #206 added beside `main`, each with its reason: `read_and_stage` writes the staged record body
# (moved from the planner prep), and capture staging's two injectable defaults fetch a URL and run `gh`. Everything
# else stays pure, and a test injects fakes for both defaults.
_IO_EDGES = {"main", "read_and_stage", "_default_opener", "_default_runner"}


def _impure_calls(tree, allowed=("main",)):
    """``(function, call)`` for every banned call in a top-level function other than those ``allowed``."""
    found = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name in allowed:
            continue
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", None)
            if name in _IMPURE_CALLS:
                found.append((node.name, name))
    return found


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


class FingerprintTests(unittest.TestCase):
    """#205: a record revision is detected by its content, never by its URL or approval date — an in-place
    edit keeps the URL and a same-day revision keeps the date."""

    def test_stable_across_line_endings_and_surrounding_whitespace(self):
        expected = design_record.fingerprint(COMPLETE)
        self.assertRegex(expected, r"^[0-9a-f]{12}$")
        for variant in (
            COMPLETE.replace("\n", "\r\n"),
            COMPLETE.replace("\n", "  \n"),
            "\n\n" + COMPLETE + "\n\n",
        ):
            with self.subTest(variant=variant[:10]):
                self.assertEqual(design_record.fingerprint(variant), expected)

    def test_a_changed_state_description_changes_it(self):
        revised = COMPLETE.replace("newest first", "oldest first")
        self.assertNotEqual(design_record.fingerprint(revised), design_record.fingerprint(COMPLETE))

    def test_read_record_carries_it_present_and_absent(self):
        record, _notices, _decision = design_record.read_record([_comment(COMPLETE)])
        self.assertEqual(record["fingerprint"], design_record.fingerprint(COMPLETE))
        absent, _notices, _decision = design_record.read_record([])
        self.assertIsNone(absent["fingerprint"])


TARGET_LINE = "- Design target: https://github.com/o/r/issues/42#issuecomment-1 · fingerprint 0123456789ab · approved 2026-11-10"


class CitedTargetTests(unittest.TestCase):
    """#205: the one reader of a plan's `- Design target:` line, which #206 composes."""

    def test_reads_the_first_target_bullet_under_ui_decisions(self):
        plan = "## Approach\nx\n\n## UI decisions\n%s\n- the empty state shows a prompt — [precedent: design record `empty`]\n" % TARGET_LINE
        self.assertEqual(
            design_record.cited_target(plan),
            {"url": "https://github.com/o/r/issues/42#issuecomment-1", "fingerprint": "0123456789ab"},
        )

    def test_none_without_a_section_or_a_target_line(self):
        for plan in ("## Approach\nx\n", "## UI decisions\n- a decision — [precedent: ui-design §2]\n", "", None):
            with self.subTest(plan=plan):
                self.assertIsNone(design_record.cited_target(plan))

    def test_none_for_a_malformed_first_target_line(self):
        for line in (
            TARGET_LINE.replace("0123456789ab", "0123456789"),
            TARGET_LINE.replace("0123456789ab", "0123456789AB"),
            TARGET_LINE.replace(" · approved 2026-11-10", ""),
            TARGET_LINE.replace("2026-11-10", "2026-13-45"),
        ):
            with self.subTest(line=line):
                self.assertIsNone(design_record.cited_target("## UI decisions\n%s\n" % line))

    def test_a_target_line_under_another_heading_is_not_the_citation(self):
        plan = "## Approach\n%s\n\n## UI decisions\n- a decision — [precedent: ui-design §2]\n" % TARGET_LINE
        self.assertIsNone(design_record.cited_target(plan))

    def test_crlf_plan_reads(self):
        plan = ("## UI decisions\n%s\n" % TARGET_LINE).replace("\n", "\r\n")
        self.assertEqual(design_record.cited_target(plan)["fingerprint"], "0123456789ab")


def _plan(*, target=True, check=None, phases=None):
    """A plan whose `## UI decisions` cites ``target`` and names ``check``, with ``phases`` (``None``: none)."""
    ui = []
    if target:
        ui.append(TARGET_LINE)
    if check is not None:
        ui.append(check)
    text = "## Approach\nx\n\n## UI decisions\n%s\n" % "\n".join(ui or ["- nothing"])
    if phases is not None:
        text += "\n## Phases\n" + phases
    return text


PHASES_2 = ("1. **Phase 1 — Scripts**\n   - kind: code-shipping\n   - ships: PR commits\n   - closes-dod: (none)\n"
            "   - deliverable: x\n   - depends-on: (none)\n   - checkpoint: pause\n"
            "2. **Phase 2 — Prose**\n   - kind: code-shipping\n   - ships: PR commits\n   - closes-dod: (none)\n"
            "   - deliverable: y\n   - depends-on: 1\n")
PHASES_OPERATOR_LAST = PHASES_2 + ("3. **Phase 3 — Flip the flag**\n   - kind: operator\n   - ships: an operator step\n"
                                   "   - closes-dod: (none)\n   - deliverable: z\n   - depends-on: 2\n")


class CitedCheckTests(unittest.TestCase):
    """#206: the plan's `- Build check: phase <N>` line under `## UI decisions`."""

    def test_reads_the_phase(self):
        self.assertEqual(design_record.cited_check(_plan(check="- Build check: phase 2")), 2)

    def test_none_when_absent_malformed_or_out_of_section(self):
        for plan in (_plan(), _plan(check="- Build check: phase 0"), _plan(check="- Build check: phase x"),
                     _plan(check="- Build check: 2"), "## Approach\n- Build check: phase 2\n\n## UI decisions\n- a\n",
                     "", None):
            with self.subTest(plan=plan):
                self.assertIsNone(design_record.cited_check(plan))

    def test_the_first_line_decides(self):
        plan = _plan(check="- Build check: phase x\n- Build check: phase 2")
        self.assertIsNone(design_record.cited_check(plan))


class CitationTests(unittest.TestCase):
    """#205's five citation states, moved from the planner's prep (#206) unchanged."""

    def _record(self, **kw):
        record = {"present": True, "approved": True, "fingerprint": "0123456789ab"}
        record.update(kw)
        return record

    def test_the_five_states(self):
        target = {"url": "u", "fingerprint": "0123456789ab"}
        self.assertEqual(design_record.citation(self._record(), None), "none")
        self.assertEqual(design_record.citation({"present": False}, target), "missing")
        self.assertEqual(design_record.citation(self._record(approved=False), target), "unapproved")
        self.assertEqual(design_record.citation(self._record(), target), "current")
        self.assertEqual(design_record.citation(self._record(fingerprint="ffffffffffff"), target), "revised")
        self.assertEqual(design_record.CITATION_STATES, ("none", "missing", "unapproved", "current", "revised"))


class DesignFactTests(unittest.TestCase):
    """#206: `facts.design` — one builder for the planner, resolver, evaluator and review preps."""

    CURRENT = {"present": True, "approved": True, "fingerprint": "0123456789ab"}

    def test_none_with_no_citation(self):
        fact = design_record.design_fact(self.CURRENT, None, _plan(target=False, phases=PHASES_2))
        self.assertEqual((fact["citation"], fact["check"], fact["check_phase"]), ("none", "none", None))
        self.assertEqual(set(fact), {"record", "block", "plan_target", "citation", "check_phase", "check"})

    def test_none_with_no_plan(self):
        fact = design_record.design_fact(self.CURRENT, None, None)
        self.assertEqual((fact["plan_target"], fact["citation"], fact["check"]), (None, "none", "none"))

    def test_run_on_current_at_the_named_phase(self):
        fact = design_record.design_fact(self.CURRENT, {"present": True}, _plan(check="- Build check: phase 1",
                                                                                 phases=PHASES_2))
        self.assertEqual((fact["citation"], fact["check"], fact["check_phase"]), ("current", "run", 1))
        self.assertEqual(fact["block"], {"present": True})

    def test_check_phase_falls_back_to_the_last_code_shipping_phase(self):
        for phases, expected in ((PHASES_2, 2), (PHASES_OPERATOR_LAST, 2), (None, 1)):
            with self.subTest(phases=phases):
                fact = design_record.design_fact(self.CURRENT, None, _plan(phases=phases))
                self.assertEqual((fact["check"], fact["check_phase"]), ("run", expected))

    def test_hold_on_each_non_current_citation(self):
        for record, state in (({"present": False}, "missing"),
                              (dict(self.CURRENT, approved=False), "unapproved"),
                              (dict(self.CURRENT, fingerprint="ffffffffffff"), "revised")):
            with self.subTest(state=state):
                fact = design_record.design_fact(record, None, _plan(phases=PHASES_2))
                self.assertEqual((fact["citation"], fact["check"]), (state, "hold"))

    def test_hold_with_a_null_phase_on_unreadable_phases(self):
        fact = design_record.design_fact(self.CURRENT, None, _plan(phases="1. not a phase\n"))
        self.assertEqual((fact["citation"], fact["check"], fact["check_phase"]), ("current", "hold", None))

    def test_hold_when_the_named_phase_is_not_code_shipping(self):
        for check in ("- Build check: phase 3", "- Build check: phase 9"):
            with self.subTest(check=check):
                fact = design_record.design_fact(self.CURRENT, None, _plan(check=check, phases=PHASES_OPERATOR_LAST))
                self.assertEqual((fact["check"], fact["check_phase"]), ("hold", None))


class ReadAndStageTests(unittest.TestCase):
    """#206: `read_and_stage`, moved from the planner's prep — the record plus its staged body."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch = Path(self._tmp.name)

    def test_stages_the_trusted_record(self):
        notices = []
        record, decision = design_record.read_and_stage([_comment(COMPLETE)], self.scratch, 42, notices)
        self.assertIsNone(decision)
        self.assertEqual(record["comment_id"], 101)
        self.assertEqual(Path(record["body_path"]).read_text(encoding="utf-8"), COMPLETE)
        self.assertEqual(Path(record["body_path"]).name, design_record.DESIGN_RECORD_FILENAME)
        self.assertEqual(notices, [])

    def test_absent_removes_a_stale_staged_body_and_untrusted_is_noticed_once(self):
        design_record.read_and_stage([_comment(COMPLETE)], self.scratch, 42, [])
        notices = []
        record, decision = design_record.read_and_stage([_comment(COMPLETE, association="NONE")], self.scratch, 42,
                                                        notices)
        self.assertIsNone(decision)
        self.assertFalse(record["present"])
        self.assertIsNone(record["body_path"])
        self.assertFalse((self.scratch / design_record.DESIGN_RECORD_FILENAME).exists())
        self.assertEqual(len([n for n in notices if n.startswith(MARKER_UNTRUSTED)]), 1)

    def test_two_trusted_records_are_the_decision_and_remove_a_stale_staged_body(self):
        design_record.read_and_stage([_comment(COMPLETE)], self.scratch, 42, [])
        self.assertTrue((self.scratch / design_record.DESIGN_RECORD_FILENAME).exists())
        record, decision = design_record.read_and_stage(
            [_comment(COMPLETE), _comment(COMPLETE, database_id=102)], self.scratch, 42, [])
        self.assertIsNone(record)
        self.assertEqual(decision["code"], MARKER_AMBIGUOUS)
        self.assertFalse((self.scratch / design_record.DESIGN_RECORD_FILENAME).exists())


class _Response:
    def __init__(self, data, url, content_type="image/png"):
        self._data, self._url = data, url
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def read(self, n=-1):
        return self._data if n < 0 else self._data[:n]

    def geturl(self):
        return self._url

    def close(self):
        self.closed = True


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


SIGNED = "https://private-user-images.githubusercontent.com/1/a1.png?jwt=abc"


class StageCapturesTests(unittest.TestCase):
    """#206: `stage_captures` / `stage` — a comment supplies the URLs, so every fetch is bounded."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "checkout"
        self.out = Path(self._tmp.name) / "staged"
        _write(self.root / "prototypes" / "captures" / "loaded.png", "PNG-loaded")
        self.fetched, self.gh_calls = [], []

    def _runner(self, html='<p><img alt="empty" src="%s"></p>' % SIGNED, returncode=0):
        def run(argv):
            self.gh_calls.append(argv)
            return _Result(json.dumps({"body_html": html}), returncode)
        return run

    def _opener(self, responses):
        def open_(url):
            self.fetched.append(url)
            value = responses.get(url)
            if isinstance(value, Exception):
                raise value
            if value is None:
                raise OSError("no such URL in the fake")
            return value
        return open_

    def _record(self, *states):
        return {"states": [{"id": i, "description": "d " + i, "capture": c} for i, c in states]}

    def test_copies_a_path_and_downloads_the_signed_image_src(self):
        record = self._record(("empty", "![empty](https://github.com/user-attachments/assets/a1)"),
                              ("loaded", "prototypes/captures/loaded.png"))
        payload, notices = design_record.stage_captures(
            "o/r", "101", record, str(self.root), str(self.out), runner=self._runner(),
            opener=self._opener({SIGNED: _Response(b"PNG-empty", SIGNED)}))
        self.assertEqual(notices, [])
        self.assertEqual((payload["staged"], payload["missing"]), (2, []))
        self.assertEqual((self.out / "empty.png").read_bytes(), b"PNG-empty")
        self.assertEqual((self.out / "loaded.png").read_text(), "PNG-loaded")
        self.assertEqual(self.fetched, [SIGNED])
        self.assertEqual(self.gh_calls, [["gh", "api", "-H", "Accept: application/vnd.github.full+json",
                                          "repos/o/r/issues/comments/101"]])

    def test_no_gh_read_when_every_capture_is_a_path(self):
        record = self._record(("loaded", "prototypes/captures/loaded.png"))
        design_record.stage_captures("o/r", "101", record, str(self.root), str(self.out), runner=self._runner(),
                                     opener=self._opener({}))
        self.assertEqual(self.gh_calls, [])

    def _one(self, capture, responses, runner=None):
        payload, notices = design_record.stage_captures(
            "o/r", "101", self._record(("empty", capture)), str(self.root), str(self.out),
            runner=runner or self._runner(html=""), opener=self._opener(responses))
        return payload["states"][0], notices

    def _assert_unavailable(self, state, notices):
        self.assertIsNone(state["staged_path"])
        self.assertTrue(state["error"])
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0].startswith(design_record.DESIGN_CAPTURE_UNAVAILABLE + ": state `empty`"))
        self.assertEqual(list(self.out.glob("empty.*")), [])

    def test_refuses_a_path_outside_the_root(self):
        _write(Path(self._tmp.name) / "secret.png", "s")
        (self.root / "link.png").symlink_to(Path(self._tmp.name) / "secret.png")
        for capture in ("../secret.png", str(Path(self._tmp.name) / "secret.png"), "link.png", "prototypes"):
            with self.subTest(capture=capture):
                self._assert_unavailable(*self._one(capture, {}))

    def test_refuses_http_and_hosts_off_the_allowlist(self):
        for url in ("http://github.com/a.png", "https://evil.example/a.png", "https://evil-github.com/a.png",
                    "https://github.com.evil.example/a.png", "https://user@github.com/a.png",
                    "https://github.com:8443/a.png", "file:///etc/passwd"):
            with self.subTest(url=url):
                state, notices = self._one(url, {url: _Response(b"x", url)})
                self._assert_unavailable(state, notices)
                self.assertNotIn(url, self.fetched)

    def test_refuses_a_final_url_off_the_allowlist(self):
        url = "https://github.com/user-attachments/assets/a1"
        self._assert_unavailable(*self._one(url, {url: _Response(b"x", "https://evil.example/a1")}))

    def test_refuses_an_over_cap_body(self):
        url = "https://github.com/user-attachments/assets/a1"
        big = b"x" * (design_record.CAPTURE_MAX_BYTES + 1)
        self._assert_unavailable(*self._one(url, {url: _Response(big, url)}))

    def test_a_failed_fetch_and_a_failed_gh_read_are_one_notice_each(self):
        url = "https://github.com/user-attachments/assets/a1"
        state, notices = self._one(url, {url: OSError("boom")}, runner=self._runner(returncode=1))
        self._assert_unavailable(state, notices)
        self.assertIn("the comment could not be read", state["error"])

    def test_a_refused_redirect_closes_the_response_it_carries(self):
        url = "https://github.com/user-attachments/assets/a1"
        body = io.BytesIO(b"x")
        state, notices = self._one(url, {url: urllib.error.HTTPError(url, 302, "refused", {}, body)})
        self._assert_unavailable(state, notices)
        self.assertTrue(body.closed)

    def test_a_state_with_no_capture_is_unavailable(self):
        self._assert_unavailable(*self._one("", {}))

    def test_the_redirect_handler_refuses_a_hop_off_the_allowlist(self):
        handler = design_record._CheckedRedirects()  # noqa: SLF001
        request = urllib.request.Request("https://github.com/user-attachments/assets/a1")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/a.png")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://private-user-images.githubusercontent.com/a")
        follow = handler.redirect_request(request, None, 302, "Found", {}, SIGNED)
        self.assertEqual(follow.full_url, SIGNED)

    def test_image_sources_reads_alt_and_src(self):
        self.assertEqual(design_record.image_sources('<a href="x"><img src="s1" alt="a"></a><img alt="a" src="s2"/>'),
                         {"a": "s1"})
        self.assertEqual(design_record.image_sources(None), {})

    def test_stage_cli_needs_a_numeric_comment_id(self):
        record = Path(self._tmp.name) / "record.md"
        record.write_text(COMPLETE, encoding="utf-8")
        result = subprocess.run([str(MODULE), "stage", "o/r", "--comment-id", "x", "--record", str(record),
                                 "--root", str(self.root), "--out", str(self.out)], capture_output=True,
                                encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


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

    def test_module_is_pure_functions_plus_named_io_edges(self):
        # #204 ended the import-only status: one thin `main`. #206 added the `stage` subcommand and named I/O edges
        # (`_IO_EDGES`): no other function calls `open`, `print`, `read_text`, `write_text`, `sys.exit` or anything
        # that spawns a process, and the module imports no process runner at module level (the `gh` default imports
        # `pipelib.process` inside its own body). `locate` / `read_block` read the working tree only through
        # `config_block`, and capture staging writes bytes with `write_bytes` / `shutil.copyfile`, which this pin
        # leaves to `StageCapturesTests`.
        text = MODULE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env python3\n"))
        tree = ast.parse(text)
        mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        self.assertEqual(len(mains), 1)
        imported = {alias.name.split(".")[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                    for alias in n.names}
        imported |= {(n.module or "").split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom)}
        for banned in ("subprocess", "os", "process"):
            with self.subTest(module=banned):
                self.assertNotIn(banned, imported)
        self.assertEqual(_impure_calls(tree, allowed=_IO_EDGES), [])
        subcommands = [c.args[0].value for c in ast.walk(mains[0]) if isinstance(c, ast.Call)
                       and isinstance(c.func, ast.Attribute) and c.func.attr == "add_parser"]
        self.assertEqual(subcommands, ["check", "stage"])

    def test_pin_catches_io_calls_outside_main(self):
        # Positive control for the pin above: the same walk finds a bare call and an attribute call outside
        # `main`, and leaves `main`'s own alone.
        tree = ast.parse("import sys\ndef f():\n    print('x')\ndef g():\n    sys.exit(1)\n"
                         "def main():\n    print('ok')\n")
        self.assertEqual(_impure_calls(tree), [("f", "print"), ("g", "exit")])


class CheckCliTests(unittest.TestCase):
    """`design_record.py check <body_path>` — the writer's pre-post check (#204): always `ok`, its payload
    `parse_record`'s dict; an unreadable path is exit 2 with no envelope."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _check(self, body=None, path=None):
        if path is None:
            path = self.dir / "record.md"
            path.write_text(body, encoding="utf-8")
        return subprocess.run([str(MODULE), "check", str(path)], capture_output=True, encoding="utf-8",
                              check=False)

    def _payload(self, body):
        result = self._check(body)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertEqual(envelope["status"], "ok")
        return envelope

    def test_script_is_executable(self):
        self.assertTrue(os.access(MODULE, os.X_OK))

    def test_approved_record_reports_approved_and_no_findings(self):
        envelope = self._payload(COMPLETE)
        self.assertTrue(envelope["approved"])
        self.assertEqual(envelope["findings"], [])
        self.assertEqual(envelope["approval"], "approved")
        for key in ("prototype", "states", "approval", "approved_on", "approved", "findings"):
            with self.subTest(key=key):
                self.assertIn(key, envelope)

    def test_payload_equals_parse_record(self):
        envelope = self._payload(COMPLETE)
        envelope.pop("status")
        envelope.pop("notices")
        self.assertEqual(envelope, json.loads(json.dumps(design_record.parse_record(COMPLETE))))

    def test_pending_record_is_not_approved(self):
        envelope = self._payload(COMPLETE.replace("Approval: approved 2026-11-10", "Approval: pending"))
        self.assertEqual(envelope["approval"], "pending")
        self.assertFalse(envelope["approved"])

    def test_state_with_no_capture_is_not_approved(self):
        body = COMPLETE.replace("capture: https://github.com/user-attachments/assets/a3", "capture: (none)")
        envelope = self._payload(body)
        self.assertFalse(envelope["approved"])
        self.assertIsNone([s for s in envelope["states"] if s["id"] == "error"][0]["capture"])

    def test_malformed_state_line_is_not_approved_with_the_finding(self):
        body = COMPLETE.replace("- `error` —", "- `Error!` —")
        envelope = self._payload(body)
        self.assertFalse(envelope["approved"])
        self.assertIn(design_record.REASON_STATE_MALFORMED, [f["reason"] for f in envelope["findings"]])

    def test_missing_path_is_exit_2_with_no_envelope(self):
        result = self._check(path=self.dir / "absent.md")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout.strip(), "")

    def test_no_subcommand_is_exit_2(self):
        result = subprocess.run([str(MODULE)], capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
