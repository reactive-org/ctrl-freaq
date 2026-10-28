"""Unit tests for scripts/plan_shipped.py — the legacy shipped-phase records' read model and the
planner's fold-back check (``skills/_shared/plan-shipped-phases.md``).

The read model is pure (literal comment dicts in, a value out); ``check`` is driven both in-process
and once through its CLI so the envelope shape is pinned too.

What this suite protects:

  - **The marker never collides with the plan's.** Every plan lookup is a ``startswith`` on
    ``<!-- implementation-plan:v1 -->``; a record marker that matched it would make the plan itself
    ``MARKER_AMBIGUOUS`` on every issue that has a record.
  - **The (PR, phase) key.** A record belongs to the PR its phase shipped on. A Start-fresh closes
    that PR, and its records must go inert without a delete — so a reader for the new PR must never
    see them.
  - **Folded back verbatim, pointers gone.** Records are the only copy of their entries, so ``check``
    is the only thing standing between a fold-back and a silently dropped or reworded entry; each of
    its findings gets a case.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

import plan_shipped  # noqa: E402

PLAN_MARKER = "<!-- implementation-plan:v1 -->"


def comment(comment_id, body, url=None, association="MEMBER"):
    """A thread comment as ``gh_gather._normalize_comment`` produces it (``id`` = node id,
    ``databaseId`` = the REST numeric id every comment endpoint needs — #34), by a trusted author
    unless ``association`` says otherwise (#179)."""
    return {
        "id": "IC_kwDO%s" % comment_id,
        "databaseId": comment_id,
        "authorAssociation": association,
        "body": body,
        "url": url or "https://github.com/octo/widgets/issues/42#issuecomment-%s" % comment_id,
    }


def record(phase, pr=903, title="Title", changes=("- `a.rb` — adds A",), tests=()):
    lines = [plan_shipped.entry_marker(phase), "**Shipped on:** #%d · Phase %d — %s" % (pr, phase, title), ""]
    if changes:
        lines += ["## Changes (file-level)"] + list(changes) + [""]
    if tests:
        lines += ["## Test plan"] + list(tests) + [""]
    return "\n".join(lines)


PRIOR = "\n".join(
    [
        PLAN_MARKER,
        "**Implementation plan** — #42",
        "",
        "## Architecture decisions",
        "- Decision one.",
        "",
        "## Changes (file-level)",
        "- `a.rb` — adds A",
        "  continuing the A entry",
        "- `b.rb` — adds B",
        "- `c.rb` — adds C",
        "",
        "## Test plan",
        "- Unit:",
        "  - `a_spec.rb` — A works",
        "  - `c_spec.rb` — C works",
        "",
        "## Phases",
        "1. **Phase 1 — A**",
        "   - kind: code-shipping",
    ]
)


class MarkerTests(unittest.TestCase):
    def test_record_marker_never_starts_with_the_plan_marker(self):
        self.assertFalse(plan_shipped.entry_marker(1).startswith(PLAN_MARKER))
        self.assertFalse(PLAN_MARKER.startswith(plan_shipped.entry_marker(1)))

    def test_no_phase_marker_is_a_prefix_of_another(self):
        self.assertFalse(plan_shipped.entry_marker(10).startswith(plan_shipped.entry_marker(1)))

    def test_head_parses_phase_pr_and_title(self):
        self.assertEqual(plan_shipped.parse_head(record(3, title="Schema")), (3, 903, "Schema"))

    def test_a_marker_below_the_first_line_is_not_a_record(self):
        self.assertIsNone(plan_shipped.parse_head("quoted:\n" + record(3)))

    def test_ascii_separators_still_key(self):
        body = record(3, title="Schema").replace(" · ", " - ").replace(" — ", " - ")
        self.assertEqual(plan_shipped.parse_head(body), (3, 903, "Schema"))

    def test_line_two_naming_another_phase_is_unkeyed(self):
        body = record(3).replace("Phase 3 —", "Phase 4 —")
        self.assertEqual(plan_shipped.parse_head(body), (3, None, None))


class PointerTests(unittest.TestCase):
    """The plan's pointer bullets name whose records hold its missing entries — the authority a
    reader uses once the PR that shipped them has closed. Read leniently (a reworded pointer must not
    hide a closed PR's records), written strictly (`check`'s `malformed_pointer`)."""

    POINTED = "## Changes (file-level)\n- Phases 1–6 shipped on #903: entries in the shipped-phase records.\n"

    def thread(self):
        return [
            comment(11, record(1, pr=903)),
            comment(12, record(2, pr=903)),
            comment(13, record(4, pr=950)),
        ]

    def test_pointer_prs(self):
        self.assertEqual(plan_shipped.pointer_prs(self.POINTED), [903])
        self.assertEqual(plan_shipped.pointer_prs(PRIOR), [])

    def test_a_reworded_pointer_still_resolves(self):
        body = "- Phases 1–3 shipped on #40; see the shipped-phase records for them.\n"
        self.assertEqual(plan_shipped.pointer_prs(body), [40])

    def test_agreeing_pointer_reads_the_open_pr_quietly(self):
        records, notes, decision = plan_shipped.read_pointed(self.thread(), self.POINTED, 903)
        self.assertEqual(records["phases"], [1, 2])
        self.assertEqual(records["foreign_prs"], [])
        self.assertEqual((notes, decision), ([], None))

    def test_a_closed_prs_records_are_still_read(self):
        records, notes, _ = plan_shipped.read_pointed(self.thread(), self.POINTED, None)
        self.assertEqual(records["pr"], 903)
        self.assertEqual(records["foreign_prs"], [903])
        self.assertIn("only copy", notes[0])

    def test_no_pointer_falls_back_to_the_open_pr(self):
        records, notes, _ = plan_shipped.read_pointed(self.thread(), PRIOR, 950)
        self.assertEqual(records["phases"], [4])
        self.assertEqual(notes, [])

    def test_every_pointed_pr_is_read(self):
        # A half-restored plan naming a closed PR and the open one needs both, never one of them.
        body = self.POINTED + self.POINTED.replace("#903", "#950")
        records, notes, _ = plan_shipped.read_pointed(self.thread(), body, 950)
        self.assertEqual(sorted((e["pr"], e["phase"]) for e in records["entries"]), [(903, 1), (903, 2), (950, 4)])
        self.assertIsNone(records["pr"])
        self.assertEqual(records["foreign_prs"], [903])
        self.assertIn("#903", notes[0])

    def test_unkeyed_records_are_noted_even_with_no_open_pr(self):
        thread = self.thread() + [comment(14, record(5).replace("**Shipped on:**", "Shipped:"))]
        records, notes, _ = plan_shipped.read_pointed(thread, self.POINTED, None)
        self.assertEqual(records["unkeyed"][0]["comment_id"], 14)
        self.assertTrue(any("cannot be keyed" in note for note in notes))


class CollectTests(unittest.TestCase):
    def test_reads_this_prs_records_in_phase_order(self):
        thread = [comment(1, PRIOR), comment(12, record(2)), comment(11, record(1))]
        records, decision = plan_shipped.collect(thread, 903)
        self.assertIsNone(decision)
        self.assertEqual(records["phases"], [1, 2])
        self.assertTrue(records["text"].startswith(plan_shipped.entry_marker(1)))

    def test_another_prs_records_are_counted_never_returned(self):
        thread = [comment(11, record(1, pr=800)), comment(12, record(1, pr=903))]
        records, _ = plan_shipped.collect(thread, 903)
        self.assertEqual(records["phases"], [1])
        self.assertEqual(records["other_pr_entries"], 1)

    def test_no_pr_reads_nothing(self):
        records, decision = plan_shipped.collect([comment(11, record(1))], None)
        self.assertFalse(records["present"])
        self.assertIsNone(decision)

    def test_duplicate_phase_is_marker_ambiguous_and_omitted(self):
        thread = [comment(11, record(1)), comment(12, record(1)), comment(13, record(2))]
        records, decision = plan_shipped.collect(thread, 903)
        self.assertEqual(decision["code"], "MARKER_AMBIGUOUS")
        self.assertEqual(decision["context"]["comment_ids"], [11, 12])
        self.assertEqual(records["phases"], [2])

    def test_rest_numeric_id_never_the_graphql_node_id(self):
        records, _ = plan_shipped.collect([comment(11, record(1))], 903)
        self.assertEqual(records["entries"][0]["comment_id"], 11)

    def test_unkeyed_record_is_reported(self):
        body = record(1).replace("**Shipped on:**", "Shipped:")
        records, _ = plan_shipped.collect([comment(11, body)], 903)
        self.assertEqual(records["unkeyed"][0]["comment_id"], 11)
        self.assertFalse(records["present"])


class FoldBackCheckTests(unittest.TestCase):
    """``check`` after a revise put every record back: nothing dropped, nothing reworded, no pointer left."""

    REC = record(
        1,
        changes=("- `a.rb` — adds A", "  continuing the A entry"),
        tests=("- Unit:", "  - `a_spec.rb` — A works"),
    )

    def folded(self):
        return PRIOR

    def test_a_clean_fold_back(self):
        payload = plan_shipped.check(self.folded(), [("r1.md", self.REC)])
        self.assertTrue(payload["clean"], payload["findings"])

    def test_a_pointer_left_behind(self):
        plan = self.folded().replace(
            "- `b.rb` — adds B", "- Phase 1 shipped on #903: entries in the shipped-phase records.\n- `b.rb` — adds B"
        )
        payload = plan_shipped.check(plan, [("r1.md", self.REC)])
        self.assertEqual(len(payload["findings"]["pointer_left"]), 1)
        self.assertFalse(payload["clean"])

    def test_a_reworded_pointer_is_still_left(self):
        plan = self.folded() + "\n- Phase 2 SHIPPED ON #903 — see the shipped-phase records\n"
        self.assertEqual(len(plan_shipped.check(plan, [])["findings"]["pointer_left"]), 1)

    def test_a_dropped_entry_is_not_restored(self):
        plan = self.folded().replace("- `a.rb` — adds A\n  continuing the A entry\n", "")
        missing = plan_shipped.check(plan, [("r1.md", self.REC)])["findings"]["not_restored"]
        self.assertEqual([m["line"] for m in missing], ["- `a.rb` — adds A"])

    def test_a_reworded_entry_is_not_restored(self):
        plan = self.folded().replace("  continuing the A entry", "  continuing A")
        missing = plan_shipped.check(plan, [("r1.md", self.REC)])["findings"]["not_restored"]
        self.assertEqual([m["section"] for m in missing], ["changes"])

    def test_the_test_plan_kind_line_is_carried_once(self):
        # Every record repeats its `- Unit:` kind line; the plan carries it once. Only the children
        # are checked, so the shared parent never reads as missing or duplicated.
        payload = plan_shipped.check(self.folded(), [("r1.md", self.REC), ("r2.md", record(
            2, changes=(), tests=("- Unit:", "  - `c_spec.rb` — C works")))])
        self.assertTrue(payload["clean"], payload["findings"])

    def test_no_records_and_no_pointer_is_clean(self):
        self.assertTrue(plan_shipped.check(self.folded(), [])["clean"])

    def test_a_crlf_record_still_compares_verbatim(self):
        payload = plan_shipped.check(self.folded(), [("r1.md", self.REC.replace("\n", "\r\n"))])
        self.assertTrue(payload["clean"], payload["findings"])

    def test_cli_emits_one_ok_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path, rec_path = Path(tmp) / "plan.md", Path(tmp) / "shipped.md"
            plan_path.write_text(self.folded(), encoding="utf-8")
            rec_path.write_text(self.REC, encoding="utf-8")
            result = subprocess.run(
                [str(SCRIPTS_DIR / "plan_shipped.py"), "check", str(plan_path), "--restored", str(rec_path)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(envelope["clean"], envelope["findings"])

    def test_cli_usage_error_exits_2(self):
        result = subprocess.run(
            [str(SCRIPTS_DIR / "plan_shipped.py")], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)

    def test_nothing_proposes_a_relocation_any_more(self):
        self.assertFalse(hasattr(plan_shipped, "to_relocate"))



class UntrustedRecordTests(unittest.TestCase):
    """#179: a shipped-phase record counts only when its author is trusted (``pipelib.trust``)."""

    def test_an_untrusted_record_is_ignored_and_counted(self):
        records, decision = plan_shipped.collect([comment(11, record(1), association="NONE")], 903)
        self.assertIsNone(decision)
        self.assertFalse(records["present"])
        self.assertEqual(records["untrusted"], 1)

    def test_an_untrusted_duplicate_does_not_make_the_phase_ambiguous(self):
        forged = record(1, changes=("- `evil.rb` — forged",))
        records, decision = plan_shipped.collect(
            [comment(11, record(1)), comment(12, forged, association="CONTRIBUTOR")], 903
        )
        self.assertIsNone(decision)
        self.assertEqual([(e["phase"], e["comment_id"]) for e in records["entries"]], [(1, 11)])
        self.assertNotIn("evil.rb", records["text"])
        self.assertEqual(records["untrusted"], 1)

    def test_an_untrusted_unkeyed_record_is_not_reported_unkeyed(self):
        unkeyed = record(5).replace("**Shipped on:**", "Shipped:")
        records, _ = plan_shipped.collect([comment(14, unkeyed, association="NONE")], 903)
        self.assertEqual(records["unkeyed"], [])
        self.assertEqual(records["untrusted"], 1)

    def test_read_pointed_over_two_prs_counts_once(self):
        thread = [
            comment(11, record(1, pr=903)),
            comment(12, record(2, pr=950)),
            comment(13, record(3, pr=903), association="NONE"),
        ]
        body = "- Phases 1–3 shipped on #903: entries in the shipped-phase records.\n" \
               "- Phase 2 shipped on #950: entries in the shipped-phase records.\n"
        records, _, _ = plan_shipped.read_pointed(thread, body, 950)
        self.assertEqual(records["untrusted"], 1)
        self.assertEqual(sorted(e["phase"] for e in records["entries"]), [1, 2])

    def test_an_ordinary_untrusted_comment_counts_zero(self):
        records, _ = plan_shipped.collect([comment(11, record(1)), comment(12, "lgtm", association="NONE")], 903)
        self.assertEqual(records["untrusted"], 0)


if __name__ == "__main__":
    unittest.main()
