"""The design record's readers converge (#206, epic #201's "One read model owns the design record").

Five preps read the issue's design record: the designer's (#204) and the planner's (#205), then the resolver's,
the evaluator's and the review tool's (#206). Each must see the same record — the same states, the same approval,
the same fingerprint — and each must skip an untrusted copy with one `MARKER_UNTRUSTED` notice. Two checks, so
neither can pass alone:

  - **By value.** One fixture thread (a trusted approved record and an untrusted copy of it with a different
    state) read through each prep's own reader path — `design_record.read_record` plus the designer prep's
    staging, and `design_record.read_and_stage`, which the other four compose — yields one record.
  - **By composition.** Each of the four `facts.design` preps calls `design_record.read_and_stage` and
    `design_record.design_fact`, and none spells the record's marker or calls `parse_record` itself, so no prep can
    grow a reader of its own that drifts from the shared one.
"""

import ast
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import design_record  # noqa: E402  (import after sys.path setup, by necessity)
import prep_designer  # noqa: E402
from pipelib.trust import MARKER_UNTRUSTED  # noqa: E402

RECORD = """<!-- ux-design:v1 -->
**Design record** — #42 Show the weekly summary — recorded 2026-11-10T09:00:00Z

Prototype: prototypes/weekly-summary/

## States
- `empty` — no entries yet; a prompt invites the first one — capture: ![empty](https://github.com/user-attachments/assets/a1)
- `loaded` — the week's entries, newest first — capture: prototypes/weekly-summary/captures/loaded.png

Approval: approved 2026-11-10
"""
# A stranger's copy: a different state set, which no reader may take for the record.
FORGED = RECORD.replace("newest first", "oldest first").replace("- `empty`", "- `forged`")

FACT_PREPS = ("prep_planner", "prep_resolver", "prep_evaluator", "prep_review")


def _comment(body, association, database_id):
    return {"id": "IC_%d" % database_id, "databaseId": database_id, "author": {"login": "u%d" % database_id},
            "authorAssociation": association, "body": body, "createdAt": "2026-11-10T09:00:00Z",
            "url": "https://github.com/o/r/issues/42#issuecomment-%d" % database_id}


THREAD = [_comment(FORGED, "NONE", 900), _comment(RECORD, "MEMBER", 101)]


def _view(record):
    return ([(s["id"], s["capture"]) for s in record["states"]], record["approved"], record["fingerprint"],
            record["comment_id"])


class ReadersConvergeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch = Path(self._tmp.name)

    def test_every_reader_path_yields_the_trusted_record_and_skips_the_copy(self):
        # The designer prep's path: `read_record`, then its own staging of the body.
        designer, designer_notices, decision = design_record.read_record(THREAD, issue=42)
        self.assertIsNone(decision)
        (self.scratch / "designer").mkdir()
        staged = prep_designer._stage_record(THREAD, designer, str(self.scratch / "designer"))  # noqa: SLF001
        self.assertEqual(Path(staged).read_text(encoding="utf-8"), RECORD)
        expected = _view(designer)
        self.assertEqual(expected[0][0][0], "empty")
        self.assertTrue(expected[1])
        self.assertEqual([n for n in designer_notices if n.startswith(MARKER_UNTRUSTED)][:1], designer_notices[:1])
        self.assertEqual(len(designer_notices), 1)

        # The four `facts.design` preps' path: `read_and_stage`, then `design_fact`.
        for prep in FACT_PREPS:
            with self.subTest(prep=prep):
                notices = []
                record, decision = design_record.read_and_stage(THREAD, self.scratch / prep, 42, notices)
                self.assertIsNone(decision)
                self.assertEqual(_view(record), expected)
                self.assertEqual(Path(record["body_path"]).read_text(encoding="utf-8"), RECORD)
                self.assertEqual(len([n for n in notices if n.startswith(MARKER_UNTRUSTED)]), 1)
                self.assertNotIn("forged", " ".join(notices))
                fact = design_record.design_fact(record, None, None)
                self.assertEqual(fact["record"]["fingerprint"], designer["fingerprint"])

    def test_each_fact_prep_composes_the_shared_reader_and_builder(self):
        for prep in FACT_PREPS:
            with self.subTest(prep=prep):
                tree = ast.parse((SCRIPTS_DIR / ("%s.py" % prep)).read_text(encoding="utf-8"))
                calls = {
                    "%s.%s" % (node.func.value.id, node.func.attr)
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                }
                self.assertIn("design_record.read_and_stage", calls)
                self.assertIn("design_record.design_fact", calls)
                self.assertNotIn("design_record.parse_record", calls)
                # No code literal carries the marker (prose may name it): no prep scans for the record itself.
                literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                            and not (n.col_offset == 0 or "\n" in n.value)]
                self.assertFalse([v for v in literals if design_record.MARKER in v], prep)


if __name__ == "__main__":
    unittest.main()
