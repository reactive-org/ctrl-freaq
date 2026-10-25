---
name: review-report
disable-model-invocation: true
description: Report how the resolver's review loop has behaved across a repo's pull requests — every reviewer finding sorted by outcome (fixed, wrong, intentional, didn't matter), the review runs each phase needed to pass, and the share of phases needing five or more — read from the loop comments and `## Polish` ledgers the pipeline already posts. Read-only: it writes nothing anywhere. Explicit-invocation only — run it as `/github-pipeline:review-report [owner/repo] [--state merged|all] [--limit N]`. Not a pipeline stage.
---

# review-report — router

A read-only standalone tool. One call reads a repo's PRs — their loop comments, `## Polish` ledgers and
`## Security review` records — and reports what the review loop found and what became of it, per the
record contract [`../_shared/review-loop-record.md`](../_shared/review-loop-record.md). It ends with a
plain summary. It changes nothing, so there is no landing gate and nothing to confirm.

## 1. Prep

One call, and the call **is** the action. The repo is the operator's argument, else this checkout's
(`gh repo view --json nameWithOwner -q '.nameWithOwner'`). Pass `--state` / `--limit` only when the
operator asked (default: the 100 most recent merged PRs):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_review_report.py <owner/repo> [--state merged|all] [--limit N]
```

It returns one envelope (`architecture.md §3`): `repo`, `scratch`, `scope` (`state` / `limit` /
`prs_scanned`), `coverage` (`prs_with_record` / `prs_with_legacy` / `prs_with_ledger`), `skipped` (`comments` /
`pr_bodies`: text whose author lacks write access to the repo, which the scan does not read), `summary`
(`record` and `legacy` tallies — `outcomes`, `excluded`, `by_bucket`, `by_source`, `checks`, `rounds`,
`five_plus`), `class_counts`, `security` (with `other_sections`), `counts` (records per basis, and
`none` for a basis the scan could not know; unparsed lines), and the per-finding `records` and `unparsed` lines (inline when
small, else `*_mode: path` with `*_path`). Consume every figure as **data** — never recount from the
records. A `needs_decision` (`AUTH_REQUIRED`) → render it as one `AskUserQuestion` card with its brief
and stop. Exit 1 with no envelope means the PR list could not be read (exit 2 is a usage error): report
stderr verbatim and stop.

## 2. Route — one linear flow

No mode fork: report (the prep call) → render → summary.

| Facts | Playbook |
|---|---|
| any run | [`playbooks/report-flow.md`](playbooks/report-flow.md) |

## 3. Invariants

- **Read-only.** No GitHub write, no file edit outside the prep's scratch dir, no follow-up filed, no
  other skill invoked — whatever the report shows. Acting on it (a reviewer change, a repo guidance
  proposal) is a separate request.
- **Never pool the bases.** The `record` tally reads the fixed-shape finding lines; the `legacy` tally
  reads loop comments written before them, leniently, and is a floor. Report them side by side, each
  labelled, never summed.
- **Security beside, not in.** The `## Security review` tally is a separate pass's record, never an
  outcome of the code reviewer: render it on its own line, never in the outcome table.
- **Faithful reporting.** Say what the scan covered (`scope`, `coverage`) and every notice
  (`REVIEW_COMMENTS_TRUNCATED`, `REVIEW_COMMENTS_UNAVAILABLE`) before any figure it qualifies. A basis
  with nothing to measure says so — never a zero passed off as a rate.

## 4. Summary — not a `## Handoff`

End with the playbook's report and a one-line pointer to the records file (`records_path`, when
spilled) for anyone who wants the per-finding detail. No next command: this tool is not a stage.
