---
name: question-sweep
disable-model-invocation: true
description: Reconcile a project's open questions between its docs and the GitHub tracker. Scans docs for unresolved open questions (`PROVISIONAL` / `TBD` / "open question" markers, or a repo-declared pattern), cross-checks them against the `question`-type issues that are the registry of record, then proposes filing the untracked ones, flagging docs left stale by an answered question, and adding the missing doc↔issue back-links. Reports first, applies on confirmation — GitHub writes gate on you, doc edits stage in a workspace and land via an offered PR; never silently rewrites a doc or files an issue. Explicit-invocation only — run it as `/ctrl-freaq:question-sweep [docs-path-or-glob]`. A periodic hygiene sweep, **not** a pipeline stage and **not** for use mid-drafting/mid-planning: a bare open question in conversation ("phone or video?") wants *your* answer, not this. Not for code, PRs, resolving a question (that's `/ctrl-freaq:question-resolver`), or filing one issue (that's `/ctrl-freaq:drafter`).
---

# question-sweep — router

Reconcile a project's **open questions** (OQs) between the docs that raise them and the GitHub tracker
that answers them. The **registry of record is the set of `question`-type issues**, not a doc field (a doc
marker can lag a decision in a question's thread). Find that drift and close it — **report first, apply on
confirmation**. One interactive session; ends with a plain summary, never a `## Handoff`.

## 1. Prep

One call (resolve the repo first — `gh repo view --json nameWithOwner -q '.nameWithOwner'`):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_question_sweep.py <owner/repo> [--scope <glob>] [--root <path>]
```

Returns one **facts block** (`architecture.md §4`): `repo`, `root`, `scope`, `detection`, `docs`,
`registry.questions` (**each with a Tier-1 `status`** `closed`/`decision-marked`/`still-open`→`tier2_needed`
/`ambiguous`, plus `blocking` + staged `sections`), `scratch`, `attention`. Consume as **data** — never
re-fetch the registry or re-derive a `status`. A `needs_decision` is a fatal `AUTH_REQUIRED` — one card.

## 2. Route — one linear flow

**No mode fork**: scope → detect → reconcile → report → apply → land → summary. The GitHub-write gate, the
doc-edit gate, and the §8.2 landing are **runtime gates inside** the flow, never routes.

| Facts | Playbook |
|---|---|
| any sweep run | [`playbooks/sweep-flow.md`](playbooks/sweep-flow.md) |

Contracts (read when the step needs them): [detection](../_shared/open-question-detection.md),
[links](../_shared/open-question-links.md) (dispositions, tiered read, fold-back),
[question-issue](../_shared/question-issue.md).

## 3. Invariants

- **Tracker is the registry of record — never a doc field.** Prep did Tier 1; dispatch the Tier-2 reader
  ([reader](references/question-status-reader-prompt.md)) only for a `tier2_needed` entry. A doc never wins.
- **Reports before it applies.** GitHub writes gate via `AskUserQuestion`; doc edits are shown as a diff,
  applied only on confirm. **Never auto-close or auto-resolve** a question — surface an orphan, don't close.
- **Tracked-file edits land via an operator-gated PR (prd.md §8.2).** Never touch the read-only root:
  approved doc edits stage in a **work workspace**; the landing (commit + push + a PR summarizing the doc/
  link changes) is **one explicit final gate**. On decline: **no git actions** — the summary reports the
  workspace path + ready-to-run landing commands.
- **`gh_persist.py` is the single GitHub write path; stage every body to `facts.scratch` first.**

## 4. Summary — not a `## Handoff`

End with a plain **summary**: companions filed, docs updated / back-links added, the landing outcome (PR
opened, **or** workspace path + ready-to-run commands on decline), and any discrepancy left (orphans,
`AMBIGUOUS` reads, declines). A resolved OQ that leaves a **build** issue needing replanning →
breadcrumb `/ctrl-freaq:planner <build#>`: a pointer, not a forward handoff.
