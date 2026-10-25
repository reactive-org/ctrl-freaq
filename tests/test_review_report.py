"""Unit tests for scripts/review_report.py — the review-report core (#98; skills/_shared/review-loop-record.md).

`records_for_pr`, `summarize` and `class_counts` run on in-memory PRs: one row of the contract's "Outcomes"
table per case, the record/legacy/ledger bases, rounds-to-pass and the five-or-more share. `collect` and
`read_comments` run through the offline gh shim, against a fixture case written to a temp dir (the
GraphQL query is part of the argv the shim matches, so it is computed from the module, not hand-typed).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import parse  # noqa: E402
import review_report  # noqa: E402
from tests.support import shimenv  # noqa: E402

M = parse.REVIEW_LOOP_MARKER


def line(rid, bucket, action, tier="defect", provenance="introduced", source="review", key="some-class",
         site="a.py", check=None, item="an item"):
    text = "- %s · %s · %s · %s · %s · %s · class: %s · site: %s" % (
        rid, bucket, provenance, tier, action, source, key, site)
    return text + (" · check: %s" % check if check else "") + " — " + item


def record_comment(phase, lines, runs=None):
    body = [M, "## Review loop — phase %d" % phase, "### Round 1"] + list(lines)
    if runs:
        body.append("Review runs: phase %d · %d · %s" % (phase, runs[0], runs[1]))
    return "\n".join(body)


def pr(number, polish=(), security=None):
    body = "Fixes #1\n"
    if polish:
        body += "\n## Polish\n" + "\n".join(polish) + "\n"
    if security:
        body += "\n## Security review\n" + "\n".join(security) + "\n"
    return {"number": number, "title": "t", "url": "u", "state": "MERGED", "mergedAt": None, "body": body}


LEGACY = "\n".join([
    "## Review loop — phase 1",
    "### Round 1 — `code-review` medium",
    "- **prose item** (introduced · defect). Fixed.",
    "Defect fixes: phase 1 @ abcdef1 — scripts/a.py:load; lib/b.rb:Client#call",
    "### Round 2",
    "Settled (not addressed):",
    "- refuted — a nil token crashes — evidence: guard — check: upheld (×2)",
    "- plan-settled — config read — cites: ## Architecture decisions \"x\"",
])


class OutcomeTableTests(unittest.TestCase):
    """One record-format PR exercising every row of the contract's outcome table."""

    def setUp(self):
        lines = [
            line("R1.1", "addressable", "fixed"),
            line("R1.2", "explicitly-deferred", "deferred", provenance="pre-existing"),
            line("R1.3", "decision-required", "decision"),
            line("R1.4", "no-shown-cost", "ledger P1.1", tier="polish"),          # drop → did-not-matter
            line("R1.5", "no-shown-cost", "ledger P1.2", tier="polish"),          # revived → applied
            line("R1.6", "addressable", "ledger P1.3", tier="polish"),            # reclassified drop
            line("R1.7", "pre-existing", "ledger P1.4", tier="polish"),           # file → deferred
            line("R1.8", "addressable", "ledger P1.5", tier="polish"),            # open → pending
            line("R1.9", "addressable", "ledger P1.9", tier="polish"),            # no entry → unlinked
            line("R1.10", "refuted", "settled", check="upheld"),
            line("R1.11", "plan-settled", "settled", tier="polish"),              # a Decision card kept it
            line("R1.12", "deferred-by-plan", "settled", tier="polish"),
            line("R1.13", "addressable", "settled"),                              # unmapped
            line("R1.14", "decision-required", "fixed", check="overturned"),      # a card answered with a fix
        ]
        polish = [
            "- P1.1 · drop — a — x.py / f — no shown cost: taste",
            "- P1.2 · applied (commit abcdef1) — b — x.py / g — operator: revived",
            "- P1.3 · drop — c — x.py / h — reclassified: defect — see loop comment",
            "- P1.4 · file — d — x.py / i — follow-up",
            "- P1.5 · open — e — x.py / j",
            "- P1.6 · apply — f — x.py / k — operator: apply",                      # no line: ledger-only
        ]
        self.records, self.unparsed, self.rounds = review_report.records_for_pr(
            pr(7, polish), [record_comment(1, lines, runs=(3, "settled"))]
        )
        self.by_id = {r["id"]: r for r in self.records}

    def test_each_row_maps(self):
        expected = {
            "R1.1": "fixed", "R1.2": "deferred", "R1.3": "decision", "R1.4": "did-not-matter",
            "R1.5": "fixed", "R1.6": "reclassified", "R1.7": "deferred", "R1.8": "pending",
            "R1.9": "unlinked", "R1.10": "wrong", "R1.11": "intentional", "R1.12": "intentional",
            "R1.13": "unmapped", "R1.14": "fixed", "P1.6": "pending",
        }
        self.assertEqual({k: self.by_id[k]["outcome"] for k in expected}, expected)
        self.assertEqual(self.unparsed, [])

    def test_a_ledger_entry_a_line_names_is_counted_once(self):
        self.assertEqual(len(self.records), 15)
        ledger_only = [r for r in self.records if r["format"] == "ledger"]
        self.assertEqual([r["id"] for r in ledger_only], ["P1.6"])
        self.assertEqual(ledger_only[0]["basis"], "record")
        self.assertEqual(ledger_only[0]["site"], "x.py")

    def test_record_fields(self):
        r = self.by_id["R1.10"]
        self.assertEqual(
            {k: r[k] for k in ("pr", "phase", "format", "basis", "bucket", "provenance", "tier", "action",
                               "source", "class_key", "site", "check")},
            {"pr": 7, "phase": 1, "format": "record", "basis": "record", "bucket": "refuted",
             "provenance": "introduced", "tier": "defect", "action": "settled", "source": "review",
             "class_key": "some-class", "site": "a.py", "check": "upheld"},
        )
        self.assertEqual(self.by_id["R1.4"]["ledger_id"], "P1.1")
        for record in self.records:
            self.assertIn(record["outcome"], review_report.OUTCOMES + review_report.EXCLUDED)

    def test_a_repeated_id_across_comments_keeps_the_first(self):
        first = record_comment(1, [line("R1.1", "addressable", "fixed")])
        repeat = record_comment(1, [line("R1.1", "refuted", "settled")])
        records, unparsed, _ = review_report.records_for_pr(pr(8), [first, repeat])
        self.assertEqual([r["outcome"] for r in records], ["fixed"])
        self.assertEqual([u["line"] for u in unparsed], [repeat.splitlines()[-1]])

    def test_a_second_line_naming_one_ledger_id_is_unparsed(self):
        # Cold read: two lines citing P1.1 each read its disposition, counting one entry twice.
        body = record_comment(1, [line("R1.1", "addressable", "ledger P1.1", tier="polish"),
                                  line("R1.2", "addressable", "ledger P1.1", tier="polish")])
        records, unparsed, _ = review_report.records_for_pr(pr(8, ["- P1.1 · drop — a — x.py / f — no shown cost: t"]), [body])
        self.assertEqual([r["id"] for r in records], ["R1.1"])
        self.assertEqual(len(unparsed), 1)
        self.assertIn("R1.2", unparsed[0]["line"])

    def test_an_incomplete_pr_leaves_unknown_ledger_entries_on_no_basis(self):
        polish = ["- P1.1 · drop — a — x.py / f — no shown cost: t", "- P2.1 · open — b — y.py / g"]
        records, _, _ = review_report.records_for_pr(
            pr(9, polish), [record_comment(2, [line("R2.1", "addressable", "fixed")])], complete=False)
        basis = {r["id"]: r["basis"] for r in records}
        self.assertEqual(basis, {"R2.1": "record", "P1.1": None, "P2.1": "record"})
        summary = review_report.summarize(records, [])
        self.assertEqual(sum(summary["legacy"]["outcomes"].values()) + sum(summary["legacy"]["excluded"].values()), 0)

    def test_record_comment_blocks_are_not_read_as_records(self):
        body = record_comment(1, [line("R1.1", "refuted", "settled"), "Defect fixes: phase 1 @ abcdef1 — a.py:f",
                                  "Settled (not addressed):", "- refuted — an item — evidence: e"])
        records, _, _ = review_report.records_for_pr(pr(9), [body])
        self.assertEqual(len(records), 1)


class BasisTests(unittest.TestCase):
    def test_a_legacy_pr_counts_on_the_legacy_basis_only(self):
        records, _, rounds = review_report.records_for_pr(
            pr(3, ["- P1.1 · drop — a — x.py / f — no shown cost: taste"]), [LEGACY]
        )
        self.assertEqual({r["basis"] for r in records}, {"legacy"})
        self.assertEqual(
            sorted((r["format"], r["outcome"]) for r in records),
            [("ledger", "did-not-matter"), ("legacy", "fixed"), ("legacy", "fixed"), ("legacy", "intentional"),
             ("legacy", "wrong")],
        )
        fix_sites = sorted(r["site"] for r in records if r["format"] == "legacy" and r["action"] == "fixed")
        self.assertEqual(fix_sites, ["lib/b.rb", "scripts/a.py"])
        self.assertEqual(rounds, [{"pr": 3, "phase": 1, "format": "legacy", "count": 2, "exit": None}])
        ledger = [r for r in records if r["format"] == "ledger"][0]
        self.assertEqual(ledger["bucket"], "no-shown-cost")
        summary = review_report.summarize(records, rounds)
        self.assertEqual(sum(summary["record"]["outcomes"].values()), 0)
        self.assertEqual(summary["legacy"]["outcomes"], {"fixed": 2, "wrong": 1, "intentional": 1, "did-not-matter": 1})

    def test_a_mixed_pr_splits_by_phase(self):
        polish = ["- P1.1 · drop — a — x.py / f — no shown cost: taste", "- P2.1 · open — b — y.py / g"]
        records, _, _ = review_report.records_for_pr(
            pr(4, polish), [LEGACY, record_comment(2, [line("R2.1", "addressable", "fixed")], runs=(2, "settled"))]
        )
        basis = {(r["format"], r["id"] or r["item"]): r["basis"] for r in records}
        self.assertEqual(basis[("record", "R2.1")], "record")
        self.assertEqual(basis[("ledger", "P1.1")], "legacy")
        self.assertEqual(basis[("ledger", "P2.1")], "record")
        self.assertEqual(basis[("legacy", "a nil token crashes")], "legacy")

    def test_legacy_rounds_sum_across_comments_and_settled_entries_dedupe(self):
        _, _, rounds = review_report.records_for_pr(pr(5), [LEGACY, LEGACY])
        self.assertEqual(rounds, [{"pr": 5, "phase": 1, "format": "legacy", "count": 4, "exit": None}])
        records, _, _ = review_report.records_for_pr(pr(5), [LEGACY, LEGACY])
        self.assertEqual(len([r for r in records if r["action"] == "settled"]), 2)

    def test_a_legacy_phase_with_no_countable_round_is_unmeasured(self):
        _, _, rounds = review_report.records_for_pr(pr(5), ["## Review loop — phase 3\nRound one: prose only"])
        self.assertEqual(rounds, [])

    def test_every_closed_key_is_present_at_zero(self):
        summary = review_report.summarize([], [])
        for basis in review_report.BASES:
            tally = summary[basis]
            self.assertEqual(set(tally["outcomes"]), set(review_report.OUTCOMES))
            self.assertEqual(set(tally["excluded"]), set(review_report.EXCLUDED))
            self.assertEqual(set(tally["by_bucket"]), set(parse.REVIEW_BUCKETS) | {"none"})
            self.assertEqual(set(tally["by_source"]), set(parse.REVIEW_SOURCES) | {"none"})
            self.assertEqual(set(tally["checks"]), set(parse.REVIEW_CHECKS) | {"none"})
            self.assertTrue(all(v == 0 for v in tally["outcomes"].values()))
            self.assertIsNone(tally["rounds"]["median"])
            self.assertIsNone(tally["five_plus"]["share"])
        self.assertEqual(summary["record"]["rounds"]["phases_passed"], 0)
        self.assertIsNone(summary["legacy"]["rounds"]["phases_passed"])


class RoundsTests(unittest.TestCase):
    def test_passes_sum_and_the_last_exit_decides(self):
        comments = [
            record_comment(1, [], runs=(3, "accepted")),
            record_comment(1, [], runs=(2, "settled")),        # a revision run's pass on the same phase
            record_comment(2, [], runs=(6, "abort")),          # never settled
            record_comment(3, [], runs=(1, "settled")),
        ]
        _, _, rounds = review_report.records_for_pr(pr(6), comments)
        summary = review_report.summarize([], rounds)["record"]
        self.assertEqual(summary["rounds"]["phases_passed"], 2)
        self.assertEqual(summary["rounds"]["histogram"], {"1": 1, "5": 1})
        self.assertEqual((summary["rounds"]["max"], summary["rounds"]["median"], summary["rounds"]["mean"]), (5, 3.0, 3.0))
        self.assertEqual(summary["five_plus"], {"phases": 3, "count": 2, "share": 0.667})


class ClassCountsTests(unittest.TestCase):
    def test_ordering_and_basis(self):
        a = record_comment(1, [line("R1.1", "addressable", "fixed", key="b-class"),
                               line("R1.2", "refuted", "settled", key="a-class"),
                               line("R1.3", "addressable", "fixed", key="b-class")])
        b = record_comment(1, [line("R1.1", "refuted", "settled", key="a-class")])
        records = review_report.records_for_pr(pr(1), [a])[0] + review_report.records_for_pr(pr(2), [b, LEGACY])[0]
        self.assertEqual(
            review_report.class_counts(records),
            [{"class_key": "a-class", "count": 2, "outcomes": {"wrong": 2}, "prs": [1, 2]},
             {"class_key": "b-class", "count": 2, "outcomes": {"fixed": 2}, "prs": [1]}],
        )


class SecurityTallyTests(unittest.TestCase):
    def test_the_security_record_never_reaches_the_outcomes(self):
        sec = ["- Reviewed: " + "a" * 40, "- SEC-1 · open — c — x.py / f",
               "- SEC-2 · dismissed — c — x.py / g — operator: fine", "- SEC-x garbage"]
        tally = review_report._security_tally([pr(1, security=sec), pr(2)])
        self.assertEqual(tally, {"prs_with_record": 1, "entries": {"open": 1, "fixed": 0, "dismissed": 1}, "unparsed": 1,
                                 "other_sections": 0})
        records, _, _ = review_report.records_for_pr(pr(1, security=sec), [])
        self.assertEqual(records, [])

    def test_a_foreign_section_under_the_heading_is_not_a_record(self):
        # The live run: a consuming repo's own PR-template `## Security review (ES-9)` checklist.
        foreign = "Fixes #1\n\n## Security review (`ES-9`)\n\n- [x] **Not triggered** — docs only.\n- [ ] **Reviewed by:** _name_\n"
        tally = review_report._security_tally([{"number": 1, "body": foreign}])
        self.assertEqual(tally["prs_with_record"], 0)
        self.assertEqual(tally["other_sections"], 1)


def node(bodies, total=None, pr_author="OWNER", comment_author="OWNER"):
    """One PR's GraphQL node: its author's association and its comments, each by `comment_author`
    unless given as a `(body, association)` pair."""
    nodes = [{"body": b, "authorAssociation": comment_author} if isinstance(b, str)
             else {"body": b[0], "authorAssociation": b[1]} for b in bodies]
    return {"authorAssociation": pr_author,
            "comments": {"totalCount": len(nodes) if total is None else total, "nodes": nodes}}


class CollectThroughShimTests(unittest.TestCase):
    """`collect` end to end through the gh shim: one PR list, one comments chunk."""

    def _case(self, entries):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manifest = []
        for i, (argv, stdout, code) in enumerate(entries):
            name = "out%d.json" % i
            Path(tmp.name, name).write_text(stdout, encoding="utf-8")
            manifest.append({"argv": argv, "stdout_file": name, "exit_code": code})
        Path(tmp.name, "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return shimenv.intercepted_env(base_env=os.environ, extra={"GH_SHIM_FIXTURES": tmp.name})

    LIST = ["pr", "list", "--repo", "o/r", "--state", "merged", "--limit", "5", "--json", review_report.PR_FIELDS]

    def _graphql(self, numbers):
        return ["api", "graphql", "-f", "query=" + review_report._comment_query("o/r", numbers)]

    def test_happy_path_and_truncation(self):
        prs = [pr(11), pr(12)]
        data = {"data": {"repository": {
            "p11": node([record_comment(1, [line("R1.1", "addressable", "fixed")], runs=(1, "settled"))]),
            "p12": node([LEGACY], total=150),
        }}}
        env = self._case([(self.LIST, json.dumps(prs), 0), (self._graphql([11, 12]), json.dumps(data), 0)])
        out = review_report.collect("o/r", limit=5, env=env)
        self.assertEqual(out["prs_scanned"], 2)
        self.assertEqual(out["coverage"], {"prs_with_record": 1, "prs_with_legacy": 1, "prs_with_ledger": 0})
        self.assertEqual(out["skipped"], {"comments": 0, "loop_comments": 0, "pr_bodies": 0})
        self.assertEqual(len(out["notices"]), 1)
        self.assertTrue(out["notices"][0].startswith("REVIEW_COMMENTS_TRUNCATED: #12 has 150 comments"))

    def test_coverage_counts_a_comment_that_yields_nothing(self):
        # Cold read: a heading-only legacy comment emits no record or round, yet the PR carries one.
        data = {"data": {"repository": {"p11": node(["## Review loop — phase 1\nprose"])}}}
        env = self._case([(self.LIST, json.dumps([pr(11)]), 0), (self._graphql([11]), json.dumps(data), 0)])
        self.assertEqual(review_report.collect("o/r", limit=5, env=env)["coverage"]["prs_with_legacy"], 1)

    def test_a_failed_chunk_is_a_notice_and_the_rest_report(self):
        prs = [pr(11, ["- P1.1 · drop — a — x.py / f — no shown cost: taste"])]
        env = self._case([(self.LIST, json.dumps(prs), 0), (self._graphql([11]), "", 1)])
        out = review_report.collect("o/r", limit=5, env=env)
        self.assertTrue(out["notices"][0].startswith("REVIEW_COMMENTS_UNAVAILABLE: the comments of #11"))
        self.assertIn("ledger entries and security records are not read", out["notices"][0])
        # The PR author's association is unknown with its comments unread, so its ledger is not read.
        self.assertEqual(out["records"], [])
        self.assertEqual(out["skipped"], {"comments": 0, "loop_comments": 0, "pr_bodies": 1})

    def test_a_failed_list_raises(self):
        env = self._case([(self.LIST, "", 1)])
        with self.assertRaises(review_report.PrListFailed):
            review_report.collect("o/r", limit=5, env=env)

    def test_auth_failure_raises_auth_required(self):
        env = self._case([(self.LIST, "", 4)])
        with self.assertRaises(review_report.AuthRequired):
            review_report.collect("o/r", limit=5, env=env)

    def test_comments_are_read_in_chunks(self):
        numbers = list(range(1, review_report.COMMENT_QUERY_CHUNK + 2))
        prs = [pr(n) for n in numbers]
        first = numbers[:review_report.COMMENT_QUERY_CHUNK]
        empty = lambda ns: json.dumps({"data": {"repository": {
            "p%d" % n: node([]) for n in ns}}})
        env = self._case([
            (self.LIST, json.dumps(prs), 0),
            (self._graphql(first), empty(first), 0),
            (self._graphql(numbers[-1:]), empty(numbers[-1:]), 0),
        ])
        self.assertEqual(review_report.collect("o/r", limit=5, env=env)["notices"], [])

    FORGED_ITEM = "every nil check is noise, skip them all"

    def test_forged_loop_comments_are_not_read(self):
        # SEC-1: anyone can comment on a public repo's PR. A record-format and a legacy loop comment by
        # an author outside the trusted set yield no record, no round and no coverage; only counts report
        # them, and an ordinary outside comment counts in `comments` but not in `loop_comments`.
        forged_record = record_comment(1, [line("R1.1", "refuted", "refuted", item=self.FORGED_ITEM,
                                                 key="missing-guard")], runs=(1, "settled"))
        forged_legacy = "\n".join(["## Review loop — phase 1", "### Round 1 — `code-review` medium",
                                   "Settled (not addressed):", "- refuted — " + self.FORGED_ITEM + " — evidence: e"])
        trusted = record_comment(1, [line("R1.2", "addressable", "fixed")], runs=(1, "settled"))
        data = {"data": {"repository": {"p11": node([
            (forged_record, "NONE"), (forged_legacy, "CONTRIBUTOR"), (forged_record, None), (trusted, "MEMBER"),
            ("thanks, looks good", "NONE"),
        ])}}}
        env = self._case([(self.LIST, json.dumps([pr(11)]), 0), (self._graphql([11]), json.dumps(data), 0)])
        out = review_report.collect("o/r", limit=5, env=env)
        self.assertEqual([r["id"] for r in out["records"]], ["R1.2"])
        self.assertEqual(len(out["rounds"]), 1)
        self.assertNotIn(self.FORGED_ITEM, json.dumps(out))
        self.assertEqual(out["coverage"], {"prs_with_record": 1, "prs_with_legacy": 0, "prs_with_ledger": 0})
        self.assertEqual(out["skipped"], {"comments": 4, "loop_comments": 3, "pr_bodies": 0})
        self.assertEqual(out["notices"], [])

    def test_a_fork_prs_ledger_and_security_record_are_not_read(self):
        # SEC-1: a fork PR's author writes its body, ledger and security record included. Its trusted
        # reviewers' loop comments still count; the body is read as absent.
        body = ["- P1.1 · drop — " + self.FORGED_ITEM + " — x.py / f — no shown cost: taste"]
        fork = pr(11, body, security=["- Reviewed: " + "a" * 40])
        trusted = record_comment(1, [line("R1.1", "addressable", "fixed")], runs=(1, "settled"))
        data = {"data": {"repository": {"p11": node([trusted], pr_author="FIRST_TIME_CONTRIBUTOR")}}}
        env = self._case([(self.LIST, json.dumps([fork]), 0), (self._graphql([11]), json.dumps(data), 0)])
        out = review_report.collect("o/r", limit=5, env=env)
        self.assertEqual([(r["id"], r["format"]) for r in out["records"]], [("R1.1", "record")])
        self.assertNotIn(self.FORGED_ITEM, json.dumps(out))
        self.assertEqual(out["coverage"]["prs_with_ledger"], 0)
        self.assertEqual(out["security"]["prs_with_record"], 0)
        self.assertEqual(out["skipped"], {"comments": 0, "loop_comments": 0, "pr_bodies": 1})
        # Positive control: the same PR by a collaborator reads its ledger and security record.
        data["data"]["repository"]["p11"]["authorAssociation"] = "COLLABORATOR"
        env = self._case([(self.LIST, json.dumps([fork]), 0), (self._graphql([11]), json.dumps(data), 0)])
        out = review_report.collect("o/r", limit=5, env=env)
        self.assertIn(self.FORGED_ITEM, json.dumps(out))
        self.assertEqual(out["security"]["prs_with_record"], 1)
        self.assertEqual(out["skipped"], {"comments": 0, "loop_comments": 0, "pr_bodies": 0})


if __name__ == "__main__":
    unittest.main()
