# review-report — the one flow

Render the prep's report. Linear; every figure comes from the envelope as given.

## 1. Report

Run the prep call from the router §1. On `needs_decision` or a failed list, stop there (router §1).

## 2. Render

In this order, plain words first, each figure labelled with its basis:

1. **Scope** — `repo`, `scope.state`, `scope.prs_scanned` of `scope.limit`, and `coverage`: how many PRs
   carry record-format loop comments, legacy ones, and a `## Polish` ledger. Then every notice, verbatim.
2. **Outcomes** — one table, a column per basis (`summary.record`, `summary.legacy`): the four
   `outcomes` (`fixed`, `wrong`, `intentional`, `did-not-matter`), then the `excluded` categories that
   are non-zero. The record contract's "Outcomes" table says what each one means; quote it when a
   reader would misread one (Deferred-by-plan counts as intentional).
3. **Rounds to pass** — per basis, `summary.<basis>.rounds` (median, mean, max, histogram;
   `phases_passed` on the record basis) and `five_plus` (phases, count, share). Say the legacy figure is
   a floor (a round that staged nothing wrote no heading) with no pass/fail split.
4. **Recurring classes** — the top `class_counts` entries (record basis only): class key, count, its
   outcomes, its PRs. Exact keys; whether two keys are one class is not this tool's call.
5. **Security** — `security` on its own line: PRs with a record, entries by disposition, unparsed lines.
6. **Detail** — `records_path` / `unparsed_path` when spilled, or the count of `unparsed` lines inline.

A basis with no records says "no record-format loop comments yet" (or "no legacy loop comments"), not a
row of zeros.
