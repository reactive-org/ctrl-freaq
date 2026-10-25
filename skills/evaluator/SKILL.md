---
name: evaluator
description: Evaluate a pull request against its origin issue, post a formal GitHub approval or soft-rejection review, and merge it with the right strategy for a clean history. Use whenever the user references a PR to evaluate, approve, or merge — "is PR #N ready to merge?", "approve that PR if it looks good", "evaluate PR #N", "what's the right merge strategy?", "give me the squash title for #N", or "the review loop is done, what's next?". Use even after the code-review `/review` command has run (that covers code quality; this covers issue-fit, scope, doc grounding, and merge strategy) and even when the PR was opened manually. Do NOT approve or merge a PR outside this skill.
---

# evaluator — router

The final gate between "code reviewed" and "merged cleanly into main." One PR, one session. Read
this router, run prep, route to exactly one playbook, then hand off. Scripts own the mechanical
work; your judgment is the verdict, the merge call, and the handoff `Why:`.

## 1. Prep

This session runs **inside** the PR-head worktree (opened with `/github-pipeline:workspace-open`;
the resolver's handoff named it on its `Workspace:` line) — prep asserts the ambient checkout is
on the PR's head branch at **exactly** `pr.headRefOid` and re-runs the repo's setup hooks —
discovered in that worktree's own working tree; it
never creates a worktree, and it refuses (a `WORKSPACE_MISMATCH` card) to evaluate code the
checkout doesn't contain. Assemble the entire starting state in **one** call. `<PR>` is the PR
number (from the user, a URL, or the current branch); `<owner/repo>` is the repo:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_evaluator.py <PR> <owner/repo>
```

It returns one JSON **facts block** (`architecture.md §4`): `target`, `vector`, `suggested_playbook`,
`pr` (with `mergeStateStatus`/`reviewDecision`/`closingIssuesReferences`), `pr_type`, `ci` (`class` +
`fail_checks`), `health_cache` (`hit`/`sha`), `self_review`, `config` (the four gate blocks read from **the PR-head worktree's working tree**;
`config.source` names the checkout/branch/SHA and its dirty state — this PR supplies the gates it
is judged by, so report that source whenever it is dirty), `merge_config` (repo `allow_*` booleans), `dod`/`blocked_by`/`deps_available`
(keyed per closing-issue number), `polish` (the PR body's `## Polish` ledger), `epic` (story and
epic-integration PRs: the epic's story set; the integration route adds `residue`, what the merged story
PRs deferred), `grounding_docs` (the
repo's `<!-- doc-catalogue -->` entries, read at the same PR-head worktree), `catalogue_check` (what this PR
changes in that catalogue: skipped lines, and binding/authority changes to confirm), `sections` (spilled
PR body/thread/reviews/marker paths), and `attention`. Consume every fact as **data** — never re-derive PR type, CI class, or cache-hit in
prose; prep already did.

**Decision card rule.** If prep exits with `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)), act on
the answer, and re-run prep (`--refresh` for volatile facts). This is the single universal handler
for every closed-set code (`AUTH_REQUIRED`, `MARKER_AMBIGUOUS`, `WORKSPACE_MISMATCH`,
`DOD_MALFORMED`, …). A `WORKSPACE_MISMATCH` means the wrong checkout (wrong branch, the project
root, or ambient HEAD ≠ the PR's head OID — stale or diverged): the fix is the operator's (start
the session in the PR's worktree / pull it to the head), never a checkout this session performs.

**Draft-PR guard.** If `target.state` is `DRAFT` (surfaced in `attention`), stop: tell the user to
mark it ready before evaluating. A draft reaching here is genuinely in-progress work — the resolver
flips a PR ready immediately before its forward handoff, so this guard is the load-bearing half of
that contract, not a missed handoff.

Re-run prep with `--refresh` before the merge-approval gate and before any merge, so the verdict and
merge act on the PR's current state, not the opening snapshot. This detects **external operator
action** (approve/comment/merge/close on GitHub mid-run); it is not the forbidden re-verify of your
own write.

## 2. Route

Prep proposes `suggested_playbook`; confirm it against this table by `vector.type` (= `pr_type`).
Read **exactly one** playbook. Every playbook opens by reading the shared spine
`playbooks/evaluate-spine.md` (identify → health gate → 5-dimension evaluation → verdict →
merge-strategy → merge-approval gate → merge); the routed playbook adds only the **post-verdict
actions that differ by PR shape**.

| `vector.type` | Playbook | What differs from the spine |
|---|---|---|
| `standard` | `playbooks/standard.md` | terminal after merge; residual follow-up filing; cleanup |
| `story` | `playbooks/story.md` | on merge: close story (the epic's rollup follows), project progress only on a legacy epic, record this story's delivery-log entry; forward handoff |
| `epic-integration` | `playbooks/epic-integration.md` | full-suite gate (fact), always-gated merge, epic-DoD historical walk; terminal |

**Override rule** (`architecture.md §5`): honor `suggested_playbook` unless the thread carries
evidence the script could not see (e.g. the operator names a different closing issue at the "no
`closingIssuesReferences`" gate). State the reason when you override. Do **not** interleave PR-type
branches inside a body — the route *is* the branch; one route per session.

## 3. Invariants

Universal across every route:

- **Single workspace.** Your workspace is the ambient checkout, reported as `facts.workspace`
  (the PR-head worktree prep asserted at exactly `pr.headRefOid`).
  Every Read/Grep/Explore/test/command targets it by absolute path. When a flow needs a second view,
  prep hands out a named read workspace; never select a ref yourself. No `git show <ref>:path`, no
  `git grep <ref>` — grounding SHAs come from the workspace facts.
- **Staged-body writes.** Every GitHub write goes through `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py`
  via Bash: stage the verbatim body to the run scratch dir (`facts.scratch`, i.e.
  `/tmp/gh-evaluator-<PR>/…`) and pass the **path**. The script verifies the round-trip hash, returns
  `body_sha256`, and gates empty bodies (`EMPTY_BODY_FILE`) — the #626/#627 race fix. Never
  re-serialize a body across the prompt boundary; never hand-roll a persist/gather `gh` call.
- **Successful write is self-confirming.** A zero exit with a URL *is* the confirmation; never re-read
  the comment/review to check it landed (re-reads reintroduce races and burn context). The one allowed
  re-fetch is the pre-merge `--refresh` above — external-action detection, not self-verification.
- **Gates only for genuine decisions** (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)):
  the merge-approval decision, a CI-vs-local discrepancy, a missing health-config, an unlinked PR —
  never to confirm a fact prep derived. A judgment gate left with one credible option is announced, not asked; every card opens with its brief. A sub-agent (test-selection `Explore`, the Apple builder)
  never calls `AskUserQuestion`; it returns its typed result to this loop, which asks.
- **Faithful reporting.** Lead with the outcome; report failures verbatim with evidence; declare
  skipped work. A red health gate is an **unconditional hard block** — a red branch never approves.
- **Handoff on clean exit** (§4). One `## Handoff` block ends every clean run; it replaces any
  bullet-list summary.

## 4. Handoff

Every clean run ends with a single `## Handoff` block — the only bridge to the next session. The
schema, omission rules, and closed-set state-marker vocabulary are owned by
[`../_shared/handoff-format.md`](../_shared/handoff-format.md); the evaluator's per-outcome rubric and
worked shapes are in [`references/handoff-renderings.md`](references/handoff-renderings.md). **Read
that reference before composing the handoff** and match the run's outcome to its rubric (terminal
merge, story-merged, epic-integration terminal, soft-reject re-route to the resolver or — for a polish
`apply` that changes the plan, or a Re-plan answer — the planner, or — for an epic story deferral — the
slicer, approve-but-skipped). Fill the
snapshot from data in hand (the prep facts + this run's verdict / cache SHA / merge outcome); the
`Next:` action and `Why:` line are judgment. Next-command skills are namespaced `/github-pipeline:<name>`
(`planner`, `resolver`, `slicer`). A re-route does **not** invoke the prior skill via the `Skill` tool — the
handoff is the only signal; the user runs the command in a fresh session (session-per-skill is the
context-isolation choice).
