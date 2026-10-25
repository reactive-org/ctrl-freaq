"""Integration tests for scripts/prep_review_report.py — the review-report tool's prep, end to end (#98).

Driven as a real subprocess through the offline gh shim (tests/fixtures/prep_review_report_*): one
`gh pr list` plus one `gh api graphql` comments chunk per case. Covers the happy path, spill routing of
`records` / `unparsed` above the threshold, the two notices (a truncated comment list, a failed chunk),
a failed PR list (non-zero exit, no envelope) and an authentication failure (`AUTH_REQUIRED`).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "prep_review_report.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from tests.support import envelope_asserts, shimenv  # noqa: E402


class PrepReviewReportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scratch = tmp.name

    def _run(self, case, extra_env=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=case, extra=extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPT), "octo/widgets", "--scratch-dir", self.scratch],
            env=env, capture_output=True, encoding="utf-8", check=False,
        )

    def _envelope(self, case, extra_env=None):
        proc = self._run(case, extra_env)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, proc.stdout)
        envelope = json.loads(lines[0])
        return envelope

    def test_happy_path(self):
        envelope = self._envelope("prep_review_report_happy")
        envelope_asserts.assert_full_envelope_conformance(envelope, spill_section_names=("records", "unparsed"))
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["notices"], [])
        self.assertEqual(envelope["scope"], {"state": "merged", "limit": 100, "prs_scanned": 2})
        self.assertEqual(envelope["coverage"], {"prs_with_record": 1, "prs_with_legacy": 1, "prs_with_ledger": 1})
        self.assertEqual(envelope["skipped"], {"comments": 0, "loop_comments": 0, "pr_bodies": 0})
        record, legacy = envelope["summary"]["record"], envelope["summary"]["legacy"]
        self.assertEqual(record["outcomes"], {"fixed": 1, "wrong": 1, "intentional": 0, "did-not-matter": 1})
        self.assertEqual(legacy["outcomes"], {"fixed": 1, "wrong": 0, "intentional": 1, "did-not-matter": 0})
        self.assertEqual(record["rounds"]["phases_passed"], 1)
        self.assertEqual(legacy["rounds"]["histogram"], {"2": 1})
        self.assertEqual(envelope["security"], {"prs_with_record": 1, "entries": {"open": 0, "fixed": 1, "dismissed": 0}, "unparsed": 0,
                                                 "other_sections": 0})
        self.assertEqual(envelope["counts"], {"records": {"record": 3, "legacy": 2, "none": 0}, "unparsed": 0})
        self.assertEqual([c["class_key"] for c in envelope["class_counts"]], ["missing-guard", "stale-read", "taste"])
        self.assertEqual(envelope["records_mode"], "inline")
        self.assertEqual(len(json.loads(envelope["records"])), 5)
        self.assertEqual(json.loads(envelope["unparsed"]), [])

    def test_large_sections_spill_to_path(self):
        envelope = self._envelope("prep_review_report_happy", {"GH_PIPELINE_INLINE_THRESHOLD_BYTES": "10"})
        envelope_asserts.assert_spill_sections_well_formed(envelope, ("records", "unparsed"))
        self.assertEqual(envelope["records_mode"], "path")
        self.assertNotIn("records", envelope)
        records = json.loads(Path(envelope["records_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(records), 5)
        self.assertTrue(envelope["records_path"].startswith(self.scratch))

    def test_a_truncated_comment_list_is_a_notice(self):
        envelope = self._envelope("prep_review_report_truncated")
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(len(envelope["notices"]), 1)
        self.assertTrue(envelope["notices"][0].startswith("REVIEW_COMMENTS_TRUNCATED: #22 has 250 comments"))

    def test_a_failed_chunk_is_a_notice_and_the_rest_report(self):
        envelope = self._envelope("prep_review_report_unavailable")
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(envelope["notices"][0].startswith("REVIEW_COMMENTS_UNAVAILABLE: the comments of #22"))
        self.assertEqual(envelope["coverage"]["prs_with_record"], 1)
        self.assertEqual(envelope["coverage"]["prs_with_legacy"], 0)

    def test_a_failed_pr_list_exits_non_zero_with_no_envelope(self):
        proc = self._run("prep_review_report_list_failed")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("the PR list could not be read", proc.stderr)

    def test_an_auth_failure_is_auth_required(self):
        envelope = self._envelope("prep_review_report_auth_required")
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AUTH_REQUIRED")

    def test_a_usage_error_exits_2(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "not-a-repo"], capture_output=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
