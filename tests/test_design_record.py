"""Unit tests for scripts/design_record.py — the design record's and the ``ui-prototype`` block's read
model (``skills/_shared/design-record.md``, #202).

Pure functions over inline thread dicts and temp files — no `gh` and no shim; `git` only in
`ListExistingTests` and `ExistingFactTests`, against the sandbox (`tests/support/gitsandbox`). What this suite
protects, because later stories (#203–#206, #227) build on it:

  - **``approved`` fails closed.** A record reads approved only with a valid approval date, at least one
    state, a capture on every state and no findings; each way short of that is pinned, with a complete
    record as the positive control.
  - **Only a trusted record counts.** Untrusted copies are partitioned out before duplicates are
    counted, so one can neither be the record nor make it ambiguous; the notice names no comment text.
  - **The grammar is the contract.** Line labels, the state-id shape, the capture split and the CRLF
    tolerance are pinned so a writer and the reader cannot drift.
  - **The block's `Existing prototypes:` line lists, never matches (#227).** Only its leading backticked run is
    patterns; a pattern that could leave the repository is refused; only tracked files are listed.
"""

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
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
from tests.support import gitsandbox  # noqa: E402

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
# else stays pure, and a test injects fakes for both defaults. #227 added `_git`, the one door through which the
# existing-prototype listing reads the checkout (imported lazily, as `_default_runner` does).
_IO_EDGES = {"main", "read_and_stage", "_default_opener", "_default_runner", "_git"}


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


class UiDeviationsTests(unittest.TestCase):
    """#241: the plan's agreed UI deviations, the `U<n>` explanations `review_threads.py explain` stages."""

    PLAN = ("## Approach\n- `loaded` DEVIATION (agreed 2026-11-20) outside the section\n\n## UI decisions\n"
            "- Design target: https://x/1 · fingerprint 0123456789ab · approved 2026-11-01\n"
            "- The list is a table in `loaded` and `empty` — DEVIATION (agreed 2026-11-20): the shared table\n"
            "  already renders it, and `loaded` repeats here.\n"
            "- `error` keeps the record's copy.\n"
            "- A DEVIATION (agreed 2026-11-21) in `scripts/x.py` names no state.\n"
            "- `error` wraps — DEVIATION (agreed 2026-11-22)\n"
            "continued `retry`\n\n## Risks\n- `x` DEVIATION (agreed 2026-11-23)\n")

    def test_reads_each_agreed_bullet_with_its_states(self):
        out = design_record.ui_deviations(self.PLAN)
        self.assertEqual([d["states"] for d in out], [["loaded", "empty"], ["error", "retry"]])
        self.assertTrue(out[0]["text"].startswith("The list is a table in `loaded`"))
        self.assertTrue(out[0]["text"].endswith("and `loaded` repeats here."))
        self.assertEqual(out[1]["text"], "`error` wraps — DEVIATION (agreed 2026-11-22) continued `retry`")

    def test_none_without_a_section_or_text(self):
        for plan in (None, "", 42, "## Approach\n- `a` DEVIATION (agreed 2026-11-20)\n"):
            with self.subTest(plan=plan):
                self.assertEqual(design_record.ui_deviations(plan), [])


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
        self.assertEqual(set(fact), {"record", "block", "plan_target", "citation", "check_phase", "check", "fidelity"})

    def test_fidelity_is_null_unread_absent_with_no_block_and_read_from_a_present_block(self):
        # #241: `check` keeps its meaning — the citation's state — whatever the fidelity reads.
        plan = _plan(check="- Build check: phase 1", phases=PHASES_2)
        unread = design_record.design_fact(self.CURRENT, None, plan)
        self.assertIsNone(unread["fidelity"])
        absent = design_record.design_fact(self.CURRENT, {"present": False, "source": None, "text": None}, plan)
        self.assertEqual(absent["fidelity"], {"declared": False, "level": None, "findings": []})
        present = design_record.design_fact(
            self.CURRENT, {"present": True, "source": "/x/COMMANDS.md", "text": "**Fidelity:** `state-text`"}, plan)
        self.assertEqual(present["fidelity"], {"declared": True, "level": "state-text", "findings": []})
        missing_line = design_record.design_fact(
            self.CURRENT, {"present": True, "source": "/x/COMMANDS.md", "text": "Prototypes live in proto/."}, plan)
        self.assertEqual(missing_line["fidelity"], {"declared": False, "level": None, "findings": []})
        for fact in (unread, absent, present, missing_line):
            self.assertEqual((fact["citation"], fact["check"], fact["check_phase"]), ("current", "run", 1))

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

    def test_hold_on_a_malformed_build_check_line(self):
        # A `Build check:` line present but unreadable holds: it never falls back to the last code phase (#201 R1.9).
        for check in ("- Build check: phase 0", "- Build check: phase x",
                      "- Build check: phase x\n- Build check: phase 1"):
            for phases in (PHASES_2, None):
                with self.subTest(check=check, phases=phases):
                    fact = design_record.design_fact(self.CURRENT, None, _plan(check=check, phases=phases))
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

    def _runner(self, html='<p><img alt="empty" src="%s"></p>' % SIGNED, returncode=0, body=None):
        def run(argv):
            self.gh_calls.append(argv)
            return _Result(json.dumps({"body_html": html, **({"body": body} if body is not None else {})}),
                           returncode)
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

    def test_the_staged_suffix_comes_from_the_url_fetched_when_the_type_is_unknown(self):
        # #237: the record cites a suffixless attachment URL; the signed src it is fetched from ends `.png`.
        record = self._record(("empty", "![empty](https://github.com/user-attachments/assets/a1)"))
        payload, notices = design_record.stage_captures(
            "o/r", "101", record, str(self.root), str(self.out), runner=self._runner(),
            opener=self._opener({SIGNED: _Response(b"PNG-empty", SIGNED, "application/octet-stream")}))
        self.assertEqual(notices, [])
        self.assertEqual(Path(payload["states"][0]["staged_path"]).name, "empty.png")

    def test_the_pin_uses_the_rendered_src_only_while_the_comment_is_the_staged_record(self):
        # #206 (R1.10): the comment's current body must fingerprint as the record being staged.
        raw = "https://github.com/user-attachments/assets/a1"
        record = self._record(("empty", "![empty](%s)" % raw))
        staged_body = "## Record\nstate empty\n"
        expected = design_record.fingerprint(staged_body)
        for body, src_used in ((staged_body, True), (staged_body.replace("\n", "\r\n"), True),
                               ("## Record\nstate empty, edited\n", False)):
            with self.subTest(body=body):
                self.fetched = []
                payload, notices = design_record.stage_captures(
                    "o/r", "101", record, str(self.root), str(self.out), runner=self._runner(body=body),
                    opener=self._opener({SIGNED: _Response(b"PNG", SIGNED)}), expected=expected)
                self.assertEqual(payload["fingerprint"], expected)
                if src_used:
                    self.assertEqual((self.fetched, notices), ([SIGNED], []))
                else:
                    # The src map is discarded; the reference URL is tried and fails, naming the moved record.
                    self.assertEqual(self.fetched, [raw])
                    self.assertIn("changed since it was staged", payload["states"][0]["error"])
                    self.assertEqual(len(notices), 1)

    def test_no_expected_fingerprint_skips_the_pin(self):
        record = self._record(("empty", "![empty](https://github.com/user-attachments/assets/a1)"))
        payload, notices = design_record.stage_captures(
            "o/r", "101", record, str(self.root), str(self.out), runner=self._runner(body="anything"),
            opener=self._opener({SIGNED: _Response(b"PNG", SIGNED)}))
        self.assertEqual((payload["fingerprint"], self.fetched, notices), (None, [SIGNED], []))

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

    def test_the_stage_cli_pins_the_payload_to_the_record_it_stages(self):
        # #206 (R1.10): the CLI is production's only caller, so the pin must reach the envelope from it.
        text = "\n".join(line for line in COMPLETE.splitlines()
                         if "`empty`" not in line and "`error`" not in line) + "\n"
        _write(self.root / "prototypes" / "weekly-summary" / "captures" / "loaded.png", "PNG-loaded")
        record = Path(self._tmp.name) / "record.md"
        record.write_text(text, encoding="utf-8")
        result = subprocess.run([str(MODULE), "stage", "o/r", "--comment-id", "101", "--record", str(record),
                                 "--root", str(self.root), "--out", str(self.out)], capture_output=True,
                                encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual((payload["fingerprint"], payload["missing"]), (design_record.fingerprint(text), []))

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

    def test_architecture_registry_names_the_fidelity_tokens(self):
        # #241: the contract-token row's history names the fidelity line and every level, and the departures
        # record and the summary thread's tokens, so a later edit that drops one fails here, not in a consuming repo.
        section = ARCHITECTURE.read_text(encoding="utf-8").split("## §12 Invariants registry", 1)[1]
        self.assertIn("`**Fidelity:**`", section)
        for level in design_record.FIDELITY_LEVELS:
            with self.subTest(level=level):
                self.assertIn("`%s`" % level, section)
        for token in ("`## Design departures`", "`design-departure:<id>`", "` · summary <12 hex>`",
                      "` · group design`", "`design-explained`", "`summary-emptied`",
                      "`DESIGN_DEPARTURES_UNTRUSTED`", "`DESIGN_CLASSIFICATION_UNREAD`"):
            with self.subTest(token=token):
                self.assertIn(token, section)

    def test_architecture_approve_row_names_the_summary_clearance(self):
        # #241: the "No APPROVE while a gating review thread is not `accepted`" row states the summary's clearance,
        # its hunk exemption and the two new retirements, beside the design clause it amends.
        section = ARCHITECTURE.read_text(encoding="utf-8").split("## §12 Invariants registry", 1)[1]
        row = next(line for line in section.splitlines()
                   if line.startswith("| No APPROVE while a gating review thread is not `accepted`"))
        for token in ("design summary", "`design-explained`", "`summary-emptied`", "hunk", "`adopted`"):
            with self.subTest(token=token):
                self.assertIn(token, row)

    def test_prd_persisted_artifacts_name_the_departures_and_the_fidelity_line(self):
        # #241: PRD §7 lists `## Design departures` as a frozen PR-side record and the ui-prototype row names the
        # `**Fidelity:**` line, with the hold a block without it now meets.
        section = PRD.read_text(encoding="utf-8").split("## §7 ", 1)[1].split("## §8 ", 1)[0]
        self.assertIn("`## Design departures`", section)
        ui_row = next(line for line in section.splitlines() if line.startswith("| Config marker blocks"))
        self.assertIn("`**Fidelity:**`", ui_row)
        self.assertIn("/ctrl-freaq:setup", ui_row)

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



class FidelityTests(unittest.TestCase):
    """`read_fidelity` (#241): the block's one required machine-read line, a level from a closed set."""

    def _read(self, text):
        return design_record.read_fidelity(text)

    def test_the_levels_in_order(self):
        self.assertEqual(design_record.FIDELITY_LEVELS, ("state-text", "arrangement", "capture"))
        self.assertEqual(design_record.FIDELITY_LABELS, ("**Fidelity:**", "Fidelity:"))

    def test_no_block_or_no_label_is_undeclared_with_no_level(self):
        absent = {"declared": False, "level": None, "findings": []}
        self.assertEqual(self._read(None), absent)
        self.assertEqual(self._read(""), absent)
        self.assertEqual(self._read("Prototypes live in `proto/`.\n**Existing prototypes:** `proto/*.html`"), absent)
        self.assertEqual(design_record.fidelity_absent(), absent)

    def test_each_level_under_both_labels_a_list_marker_and_trailing_prose(self):
        for level in design_record.FIDELITY_LEVELS:
            for line in ("**Fidelity:** `%s`", "Fidelity: `%s`", "- **Fidelity:** `%s`", "  * Fidelity:`%s`",
                         "+ **Fidelity:** `%s` — the arrangement and its copy bind; styling does not, see `x`"):
                with self.subTest(level=level, line=line):
                    result = self._read("Intro prose.\n" + (line % level) + "\nMore prose.")
                    self.assertEqual(result, {"declared": True, "level": level, "findings": []})

    def test_a_label_mentioned_mid_line_is_prose(self):
        self.assertFalse(self._read("Set the Fidelity: `capture` line in setup.")["declared"])

    def test_each_finding_leaves_the_level_null_with_the_line_declared(self):
        cases = (
            ("**Fidelity:** none yet", [design_record.REASON_FIDELITY_NO_LEVEL]),
            ("**Fidelity:**", [design_record.REASON_FIDELITY_NO_LEVEL]),
            ("**Fidelity:** `pixel`", ["unknown level `pixel`"]),
            ("**Fidelity:** `Capture`", ["unknown level `Capture`"]),
            ("**Fidelity:** `state-text`, `capture`", [design_record.REASON_FIDELITY_SEVERAL]),
            ("**Fidelity:** `state-text` and `capture`", [design_record.REASON_FIDELITY_SEVERAL]),
            ("**Fidelity:** `capture`\nFidelity: `capture`", [design_record.REASON_FIDELITY_DUPLICATE]),
            ("Fidelity: `capture`\n- **Fidelity:** `state-text`", [design_record.REASON_FIDELITY_DUPLICATE]),
        )
        for text, reasons in cases:
            with self.subTest(text=text):
                result = self._read(text)
                self.assertTrue(result["declared"])
                self.assertIsNone(result["level"])
                self.assertEqual([f["reason"] for f in result["findings"]], reasons)

    def test_a_second_line_poisons_even_a_readable_first(self):
        # Positive control for the duplicate rule: the first line alone reads, and adding a second nulls it.
        self.assertEqual(self._read("Fidelity: `arrangement`")["level"], "arrangement")
        self.assertIsNone(self._read("Fidelity: `arrangement`\nFidelity: `arrangement`")["level"])

    def test_block_fidelity_follows_the_read_block_shape(self):
        self.assertIsNone(design_record.block_fidelity(None))
        self.assertEqual(design_record.block_fidelity({"present": False, "source": None, "text": None}),
                         design_record.fidelity_absent())
        self.assertEqual(design_record.block_fidelity({"present": True, "source": "s", "text": "Fidelity: `capture`"}),
                         {"declared": True, "level": "capture", "findings": []})

    def test_the_existing_prototypes_reader_is_unchanged_by_the_shared_label_helper(self):
        # `_label_rest` gained a `labels` argument; its default is still the Existing prototypes: pair.
        self.assertIsNone(design_record._label_rest("**Fidelity:** `capture`"))  # noqa: SLF001
        self.assertEqual(design_record._label_rest("- Existing prototypes: `a`"), " `a`")  # noqa: SLF001
        self.assertEqual(design_record._label_rest("Fidelity: `capture`", design_record.FIDELITY_LABELS),  # noqa: SLF001
                         " `capture`")

    def test_contract_names_the_line_and_its_levels(self):
        text = CONTRACT.read_text(encoding="utf-8")
        for token in (design_record.FIDELITY_LABELS[0], "read_fidelity") + design_record.FIDELITY_LEVELS:
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("first required line", text)
        # `capture` is the strictest level, not a claim of byte-identical pre-#241 behaviour (cold read, #241).
        self.assertNotIn("(the behaviour before #241)", text)
        self.assertIn("This is the strictest level", text)


class ExistingDeclarationTests(unittest.TestCase):
    """`read_declaration` (#227): the block's one labelled line, read without git."""

    def _read(self, text):
        return design_record.read_declaration(text)

    def test_no_block_or_no_label_is_undeclared_with_no_findings(self):
        empty = {"declared": False, "patterns": [], "findings": []}
        self.assertEqual(self._read(None), empty)
        self.assertEqual(self._read("Prototypes live under `prototypes/` and run with the dev server."), empty)

    def test_bold_and_plain_labels_with_a_list_marker(self):
        for line in ("**Existing prototypes:** `proto/**/*.html`",
                     "- **Existing prototypes:** `proto/**/*.html`",
                     "* Existing prototypes: `proto/**/*.html`",
                     "  + Existing prototypes:`proto/**/*.html`"):
            with self.subTest(line=line):
                result = self._read("Intro prose.\n" + line + "\nMore prose.")
                self.assertTrue(result["declared"])
                self.assertEqual(result["patterns"], ["proto/**/*.html"])
                self.assertEqual(result["findings"], [])

    def test_several_patterns_separated_by_commas_spaces_and_and(self):
        result = self._read("**Existing prototypes:** `a/*.html`, `b/**/*.html` `c/?.svg` and `d/[ab].png`")
        self.assertEqual(result["patterns"], ["a/*.html", "b/**/*.html", "c/?.svg", "d/[ab].png"])

    def test_prose_after_the_run_is_never_a_pattern(self):
        result = self._read("**Existing prototypes:** `proto/*.html` — indexed in `proto/README.md`, see `x/*.md`")
        self.assertEqual(result["patterns"], ["proto/*.html"])
        self.assertEqual(result["findings"], [])

    def test_a_label_mentioned_mid_line_is_prose(self):
        result = self._read("See the Existing prototypes: `proto/*.html` note.")
        self.assertFalse(result["declared"])

    def test_each_refused_pattern_is_a_finding(self):
        cases = {"``": design_record.REASON_PATTERN_EMPTY,
                 "`/etc/*`": design_record.REASON_PATTERN_ABSOLUTE,
                 "`:(top)x`": design_record.REASON_PATTERN_MAGIC,
                 "`a/../../b`": design_record.REASON_PATTERN_PARENT,
                 "`..`": design_record.REASON_PATTERN_PARENT,
                 "`a\x00b`": design_record.REASON_PATTERN_NUL}
        for span, reason in cases.items():
            with self.subTest(span=span):
                result = self._read("Existing prototypes: %s, `ok/*.html`" % span)
                self.assertEqual(result["patterns"], ["ok/*.html"])
                self.assertTrue(result["declared"])
                self.assertEqual([f["reason"] for f in result["findings"]], [reason])

    def test_a_padded_pattern_is_refused(self):
        result = self._read("Existing prototypes: ` proto/*.html`, `ok/*.html `")
        self.assertEqual(result["patterns"], [])
        self.assertEqual([f["reason"] for f in result["findings"]], [design_record.REASON_PATTERN_PADDED] * 2)

    def test_a_dotdot_inside_a_name_is_not_a_parent_segment(self):
        self.assertEqual(self._read("Existing prototypes: `a/x..y/*.html`")["patterns"], ["a/x..y/*.html"])

    def test_all_refused_is_undeclared_with_findings(self):
        result = self._read("**Existing prototypes:** `/abs`, `../up`")
        self.assertFalse(result["declared"])
        self.assertEqual(result["patterns"], [])
        self.assertEqual(len(result["findings"]), 2)

    def test_a_label_with_no_pattern_is_a_finding(self):
        result = self._read("**Existing prototypes:** none yet")
        self.assertFalse(result["declared"])
        self.assertEqual(result["findings"], [{"pattern": None, "reason": design_record.REASON_NO_PATTERN}])

    def test_a_duplicate_labelled_line_is_a_finding_and_the_first_wins(self):
        result = self._read("Existing prototypes: `a/*.html`\n- **Existing prototypes:** `b/*.html`")
        self.assertEqual(result["patterns"], ["a/*.html"])
        self.assertEqual(result["findings"], [{"pattern": None, "reason": design_record.REASON_DUPLICATE_LABEL}])

    def test_contract_names_the_labels_and_notices(self):
        text = CONTRACT.read_text(encoding="utf-8")
        for token in (design_record.EXISTING_LABELS[0], design_record.UI_PROTOTYPE_DECLARATION_MALFORMED,
                      design_record.UI_PROTOTYPE_LIST_UNAVAILABLE, "read_declaration", "list_existing",
                      "existing_fact"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("sets no document's authority", text)


class _SandboxTestCase(unittest.TestCase):
    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path

    def _git(self, *args):
        return gitsandbox._run_git(list(args), cwd=self.root).stdout.strip()

    def _write(self, files):
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def _commit(self, files):
        self._write(files)
        self._git("add", *files)
        self._git("commit", "-q", "-m", "c")


class ListExistingTests(_SandboxTestCase):
    """`list_existing` (#227): the tracked files the patterns fit, sorted, with their working-tree state."""

    def test_tracked_files_that_fit_are_listed_sorted(self):
        self._commit({"proto/b.html": "b", "proto/a.html": "a", "proto/README.md": "index"})
        candidates, error = design_record.list_existing(self.root, ["proto/*.html"])
        self.assertIsNone(error)
        self.assertEqual(candidates, [{"path": "proto/a.html", "modified": False},
                                      {"path": "proto/b.html", "modified": False}])

    def test_an_untracked_file_that_fits_is_not_listed(self):
        self._commit({"proto/a.html": "a"})
        self._write({"proto/new.html": "new"})
        candidates, _ = design_record.list_existing(self.root, ["proto/*.html"])
        self.assertEqual([c["path"] for c in candidates], ["proto/a.html"])

    def test_double_star_crosses_directories_and_single_star_does_not(self):
        self._commit({"proto/a.html": "a", "proto/admin/b.html": "b", "proto/admin/deep/c.html": "c"})
        single, _ = design_record.list_existing(self.root, ["proto/*.html"])
        double, _ = design_record.list_existing(self.root, ["proto/**/*.html"])
        self.assertEqual([c["path"] for c in single], ["proto/a.html"])
        self.assertEqual([c["path"] for c in double],
                         ["proto/a.html", "proto/admin/b.html", "proto/admin/deep/c.html"])

    def test_several_patterns_are_one_deduplicated_list(self):
        self._commit({"proto/a.html": "a", "mock/m.svg": "m"})
        candidates, _ = design_record.list_existing(self.root, ["proto/*.html", "mock/*", "proto/a.*"])
        self.assertEqual([c["path"] for c in candidates], ["mock/m.svg", "proto/a.html"])

    def test_a_file_edited_in_the_working_tree_is_modified(self):
        self._commit({"proto/a.html": "a", "proto/b.html": "b"})
        self._write({"proto/b.html": "edited"})
        candidates, _ = design_record.list_existing(self.root, ["proto/*.html"])
        self.assertEqual(candidates, [{"path": "proto/a.html", "modified": False},
                                      {"path": "proto/b.html", "modified": True}])

    def test_a_tracked_symlink_leaving_the_checkout_is_not_listed(self):
        outside = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(outside), True))
        (outside / "secret.html").write_text("s", encoding="utf-8")
        self._commit({"proto/a.html": "a", "proto/inner.html": "i"})
        (self.root / "proto" / "out.html").symlink_to(outside / "secret.html")
        (self.root / "proto" / "alias.html").symlink_to("inner.html")
        self._git("add", "proto/out.html", "proto/alias.html")
        self._git("commit", "-q", "-m", "links")
        candidates, _ = design_record.list_existing(self.root, ["proto/*.html"])
        self.assertEqual([c["path"] for c in candidates], ["proto/a.html", "proto/alias.html", "proto/inner.html"])

    def test_a_staged_but_uncommitted_file_is_listed_modified(self):
        self._commit({"proto/a.html": "a"})
        self._write({"proto/b.html": "b"})
        self._git("add", "proto/b.html")
        candidates, _ = design_record.list_existing(self.root, ["proto/*.html"])
        self.assertEqual(candidates, [{"path": "proto/a.html", "modified": False},
                                      {"path": "proto/b.html", "modified": True}])

    def test_a_tracked_symlink_loop_is_not_listed(self):
        self._commit({"proto/a.html": "a"})
        (self.root / "proto" / "loop.html").symlink_to("loop.html")
        self._git("add", "proto/loop.html")
        self._git("commit", "-q", "-m", "loop")
        candidates, _ = design_record.list_existing(self.root, ["proto/*.html"])
        self.assertEqual([c["path"] for c in candidates], ["proto/a.html"])

    def test_zero_matches_is_an_empty_list_with_no_error(self):
        self.assertEqual(design_record.list_existing(self.root, ["proto/*.html"]), ([], None))

    def test_no_pattern_lists_nothing(self):
        self._commit({"proto/a.html": "a"})
        self.assertEqual(design_record.list_existing(self.root, []), ([], None))

    def test_a_failed_git_call_is_an_empty_list_and_an_error(self):
        not_a_repo = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-norepo-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(not_a_repo), True))
        candidates, error = design_record.list_existing(not_a_repo, ["proto/*.html"])
        self.assertEqual(candidates, [])
        self.assertTrue(error)


class ExistingFactTests(_SandboxTestCase):
    """`existing_fact` (#227): the composition `prep_designer.py` encodes, and its notices."""

    def _block(self, text):
        return {"present": True, "source": "COMMANDS.md", "text": text}

    def test_a_malformed_label_is_the_malformed_notice(self):
        fact, notices, attention = design_record.existing_fact(self.root, self._block("Existing prototypes: `/x`"))
        self.assertFalse(fact["declared"])
        self.assertEqual(fact["candidates"], [])
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0].startswith(design_record.UI_PROTOTYPE_DECLARATION_MALFORMED + ":"))
        self.assertEqual(attention, [])

    def test_a_failed_listing_is_the_unavailable_notice(self):
        not_a_repo = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-norepo-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(not_a_repo), True))
        fact, notices, _ = design_record.existing_fact(not_a_repo, self._block("Existing prototypes: `p/*`"))
        self.assertTrue(fact["declared"])
        self.assertEqual(fact["candidates"], [])
        self.assertTrue(notices[0].startswith(design_record.UI_PROTOTYPE_LIST_UNAVAILABLE + ":"))

    def test_an_absent_block_is_undeclared_and_silent(self):
        fact, notices, attention = design_record.existing_fact(
            self.root, {"present": False, "source": None, "text": None})
        self.assertEqual(fact, {"declared": False, "patterns": [], "findings": [], "candidates": [],
                                "declined": False, "adopted": None})
        self.assertEqual((notices, attention), ([], []))

    def test_adopt_validates_a_tracked_file_and_reports_listed(self):
        self._commit({"proto/a.html": "a", "other/x.html": "x"})
        block = self._block("Existing prototypes: `proto/*.html`")
        listed, _, _ = design_record.existing_fact(self.root, block, adopt="proto/a.html")
        unlisted, _, _ = design_record.existing_fact(self.root, block, adopt="other/x.html")
        self.assertEqual(listed["adopted"]["path"], "proto/a.html")
        self.assertEqual(listed["adopted"]["abs_path"], str(self.root.resolve() / "proto" / "a.html"))
        self.assertTrue(listed["adopted"]["listed"])
        self.assertFalse(listed["adopted"]["modified"])
        self.assertFalse(unlisted["adopted"]["listed"])

    def test_adopt_of_a_directory_untracked_or_missing_path_is_one_attention_line(self):
        self._commit({"proto/a.html": "a"})
        self._write({"proto/new.html": "n"})
        for path in ("proto", "proto/new.html", "proto/missing.html", "../outside.html", ""):
            with self.subTest(path=path):
                fact, _, attention = design_record.existing_fact(self.root, self._block("x"), adopt=path)
                self.assertIsNone(fact["adopted"])
                self.assertEqual(len(attention), 1)

    def test_adopt_of_a_tracked_symlink_leaving_the_checkout_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(outside), True))
        (outside / "secret.html").write_text("s", encoding="utf-8")
        (self.root / "out.html").symlink_to(outside / "secret.html")
        self._git("add", "out.html")
        self._git("commit", "-q", "-m", "link")
        fact, _, attention = design_record.existing_fact(self.root, self._block("x"), adopt="out.html")
        self.assertIsNone(fact["adopted"])
        self.assertEqual(len(attention), 1)

    def test_adopt_of_a_listed_path_reuses_its_listed_state(self):
        self._commit({"proto/a.html": "a"})
        self._write({"proto/a.html": "edited"})
        fact, _, _ = design_record.existing_fact(self.root, self._block("Existing prototypes: `proto/*.html`"),
                                                 adopt="proto/a.html")
        self.assertTrue(fact["adopted"]["listed"])
        self.assertTrue(fact["adopted"]["modified"])

    def test_adopt_of_an_edited_file_is_modified(self):
        self._commit({"proto/a.html": "a"})
        self._write({"proto/a.html": "edited"})
        fact, _, _ = design_record.existing_fact(self.root, self._block("x"), adopt="proto/a.html")
        self.assertTrue(fact["adopted"]["modified"])

    def test_a_failed_diff_reads_modified(self):
        # Fail closed: a file whose working-tree state cannot be read is never adopted read-only on no evidence.
        not_a_repo = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-norepo-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(not_a_repo), True))
        self.assertTrue(design_record._modified(not_a_repo, "proto/a.html"))
        self._commit({"proto/a.html": "a"})
        self.assertFalse(design_record._modified(self.root, "proto/a.html"))

    def test_adopt_of_a_staged_but_uncommitted_file_is_modified(self):
        self._commit({"proto/a.html": "a"})
        self._write({"proto/b.html": "b", "other/c.html": "c"})
        self._git("add", "proto/b.html", "other/c.html")
        block = self._block("Existing prototypes: `proto/*.html`")
        for path in ("proto/b.html", "other/c.html"):  # listed (reused state) and unlisted (_modified)
            with self.subTest(path=path):
                fact, _, _ = design_record.existing_fact(self.root, block, adopt=path)
                self.assertTrue(fact["adopted"]["modified"])

    def test_adopt_of_a_tracked_symlink_loop_is_refused_without_raising(self):
        self._commit({"proto/a.html": "a"})
        (self.root / "loop.html").symlink_to("loop.html")
        self._git("add", "loop.html")
        self._git("commit", "-q", "-m", "loop")
        fact, _, attention = design_record.existing_fact(self.root, self._block("x"), adopt="loop.html")
        self.assertIsNone(fact["adopted"])
        self.assertEqual(len(attention), 1)

    def test_a_resolve_that_raises_is_refused_not_raised(self):
        # Python 3.9-3.12 raise RuntimeError on a symlink loop; 3.13+ do not, so pin that branch directly.
        self._commit({"proto/a.html": "a"})
        real_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path.name == "a.html":
                raise RuntimeError("Symlink loop")
            return real_resolve(path, *args, **kwargs)

        with unittest.mock.patch.object(Path, "resolve", resolve):
            fact, _, attention = design_record.existing_fact(self.root, self._block("x"), adopt="proto/a.html")
            candidates, _ = design_record.list_existing(self.root, ["proto/*.html"])
        self.assertIsNone(fact["adopted"])
        self.assertEqual(len(attention), 1)
        self.assertEqual(candidates, [])

    def test_no_adopt_is_declined(self):
        fact, _, _ = design_record.existing_fact(self.root, self._block("x"), no_adopt=True)
        self.assertTrue(fact["declined"])


if __name__ == "__main__":
    unittest.main()
