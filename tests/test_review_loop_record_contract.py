"""#98: the review-loop record's shared contract (skills/_shared/review-loop-record.md).

The record is script-parsed (`parse.scan_review_loop`), so every closed set the contract's tables list must
name exactly the scanner's constant, and its worked example must parse clean. The resolver renders the
contract rather than restating it (CLAUDE.md "render, don't restate"), so step 8 must cite it and must not
carry the line grammar itself.
"""

import re
import unittest
from pathlib import Path

from scripts import parse, review_report

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = REPO_ROOT / "skills" / "_shared" / "review-loop-record.md"
FIX_ROUND = REPO_ROOT / "skills" / "resolver" / "references" / "review-fix-round.md"


def _table(text, heading):
    """The first-column backticked values of the table under `heading`, in order."""
    section = text.split(heading, 1)[1]
    section = re.split(r"^#{2,3} ", section, maxsplit=1, flags=re.MULTILINE)[0]
    return tuple(re.findall(r"^\| `([a-z-]+)", section, re.MULTILINE))


class ReviewLoopRecordContractTests(unittest.TestCase):
    def setUp(self):
        self.text = CONTRACT.read_text(encoding="utf-8")
        self.flat = " ".join(self.text.split())

    def test_each_closed_set_table_names_exactly_the_scanners_set(self):
        for heading, constant in (
            ("### Buckets", parse.REVIEW_BUCKETS),
            ("### Actions", parse.REVIEW_ACTIONS),
            ("### Sources", parse.REVIEW_SOURCES),
            ("### Check verdicts", parse.REVIEW_CHECKS),
            ("### Exits", parse.REVIEW_EXITS),
        ):
            self.assertEqual(_table(self.text, heading), constant, heading)

    def test_provenance_and_tier_name_the_scanners_sets(self):
        self.assertIn("**`<provenance>`** is `introduced`, `adjacent` or `pre-existing`", self.flat)
        self.assertEqual(parse.REVIEW_PROVENANCES, ("introduced", "adjacent", "pre-existing"))
        self.assertIn("**`<tier>`** is `defect` or `polish`", self.flat)
        self.assertEqual(parse.REVIEW_TIERS, ("defect", "polish"))

    def test_the_marker_and_the_line_grammar_are_the_scanners(self):
        self.assertIn("`%s`" % parse.REVIEW_LOOP_MARKER, self.text)
        self.assertIn(
            "- R<phase>.<seq> · <bucket> · <provenance> · <tier> · <action> · <source> · class: <key> · "
            "site: <path>[ · check: <verdict>] — <item>",
            self.text,
        )
        self.assertIn("Review runs: phase <N> · <count> · <exit>", self.text)
        self.assertIn("that includes each per-round comment a `BODY_TOO_LONG` split produces", self.flat)

    def test_every_pass_posts_and_a_split_repeats_the_heading(self):
        self.assertIn("so **every pass posts a comment**", self.flat)
        self.assertIn("Each comment a split produces repeats the marker and the phase heading", self.flat)
        self.assertIn("Only the last comment carries `Review runs:`", self.flat)
        self.assertIn("**Items recorded outside a fix round.**", self.flat)

    def test_the_outcome_table_is_the_report_cores(self):
        # Each numbered row of "Outcomes", fed to `review_report.outcome_of`, gives the row's outcome.
        section = self.text.split("## Outcomes", 1)[1].split("**Ledger-only records.**", 1)[0]
        rows = dict(re.findall(r"^\| (\d+[a-f]?) \| [^|]+ \| (?:excluded )?`([a-z-]+)` \|$", section, re.MULTILINE))
        entry = lambda disposition, note=None: {"disposition": disposition, "note": note}
        cases = {
            "1": ("fixed", "addressable", None),
            "2": ("deferred", "explicitly-deferred", None),
            "3": ("decision", "decision-required", None),
            "4a": ("ledger", "addressable", entry("applied")),
            "4b": ("ledger", "addressable", entry("drop", "reclassified: defect — see loop comment")),
            "4c": ("ledger", "no-shown-cost", entry("drop", "no shown cost: taste")),
            "4d": ("ledger", "pre-existing", entry("file", "x")),
            "4e": ("ledger", "addressable", entry("open")),
            "4f": ("ledger", "addressable", None),
            "5a": ("settled", "refuted", None),
            "5b": ("settled", "plan-settled", None),
            "5c": ("settled", "deferred-by-plan", None),
            "6": ("settled", "addressable", None),
        }
        self.assertEqual(set(rows), set(cases))
        for row, (action, bucket, ledger_entry) in cases.items():
            self.assertEqual(review_report.outcome_of(action, bucket, ledger_entry), rows[row], row)
        self.assertEqual(review_report.outcome_of("ledger", None, entry("apply")), "pending")
        self.assertEqual(set(rows.values()), set(review_report.OUTCOMES) | set(review_report.EXCLUDED))

    def test_cold_read_gaps_are_closed(self):
        self.assertIn("a `## Polish` entry finalisation's re-check reclassifies as a defect and fixes", self.flat)
        self.assertIn("`<k>` being the round's number as S5.1 counts it", self.flat)
        self.assertIn("on the PR's trusted loop comments (the resolver's resume seed, `facts.resume`) or staged this pass", self.flat)

    def test_phase_2_cold_read_rules_are_stated(self):
        self.assertIn("a second line whose action names a `P` id another line already named is unparsed", self.flat)
        self.assertIn("on a PR with no record that is counted as `other_sections`, never read as an empty record", self.flat)
        self.assertIn("(or the `## Phase <N> review loop` form some pre-contract comments used)", self.flat)
        self.assertIn("It takes none and is counted in neither tally, and the notice says so", self.flat)
        self.assertNotIn("each pair's `### Round` count", self.flat)
        self.assertEqual(parse.scan_review_loop("## Phase 4 review loop\n### Round 1")["phase_headings"],
                         [{"phase": 4, "rounds": 1}])

    def test_no_positional_references(self):
        # CLAUDE.md "Stable §-anchors over positional cross-references" (review round 3).
        self.assertNotRegex(self.text, r"\((?:table|section) (?:above|below)\)")

    def test_a_cross_comment_repeat_is_the_report_cores_to_catch(self):
        self.assertIn("the report core catches one across the PR's comments, keeping the first", self.flat)

    def test_the_worked_example_parses_clean(self):
        example = self.text.split("```\n", 2)[1]
        scan = parse.scan_review_loop(example)
        self.assertEqual(scan["format"], "record")
        self.assertEqual(scan["unparsed"], [])
        self.assertEqual(len(scan["findings"]), 4)
        self.assertEqual(scan["runs"], [{"phase": 1, "count": 3, "exit": "settled"}])

    def test_the_resume_blocks_keep_their_shape(self):
        self.assertIn("the record adds to them, never replaces them", self.flat)
        for block in ("`Settled (not addressed):`", "`Defect fixes:`", "`Cold read:`"):
            self.assertIn(block, self.flat)

    def test_legacy_comments_are_their_own_basis(self):
        self.assertIn("a tally labelled **`legacy`** that is never pooled with the record tally", self.flat)
        self.assertIn("Its prose item lines are never parsed", self.flat)

    def test_step_8_renders_the_contract_and_does_not_restate_it(self):
        fix_round = " ".join(FIX_ROUND.read_text(encoding="utf-8").split())
        step8 = fix_round.split("8. **Commit. Stage the reply**", 1)[1].split("9. **Record.**", 1)[0]
        self.assertIn("(../../_shared/review-loop-record.md)", step8)
        self.assertIn("are defined there, never here", step8)
        self.assertIn("minting its `R` id", step8)
        self.assertIn("minting its `P<phase>.<seq>` id now", step8)
        self.assertNotIn("<provenance> · <tier>", fix_round)
        self.assertNotIn("class: <key>", fix_round)


if __name__ == "__main__":
    unittest.main()
