# Epic-integration PR

Route for `vector.type == epic-integration` (head matches `epic/<N>-<slug>`, base `main`). One PR that
lands the accumulated diff of every child story onto `main` in a single merge — a qualitatively larger
risk surface than a single-issue PR.

**Run the spine first.** Read [`evaluate-spine.md`](evaluate-spine.md) and execute it end to end.
Three facts shape how the spine behaves on this route — all already in the facts block, so they need
no branch in any body:

- **Full-suite health gate.** The test-selection sub-agent escalates to the full canonical suite for
  `pr_type == epic-integration` (its escalation rule) — the accumulated diff *is* the union of every
  child story, exactly the integration risk this gate exists to verify, so targeting there would miss
  cross-story interactions. This is the sub-agent's own rule fed `facts.pr_type`; don't override it.
- **Epic-DoD historical walk.** The closing issue is the **epic** (via `Fixes #<epic>` in the PR
  body), so `facts.dod[<epic>]` carries the epic body's `## Definition of done`. An epic body's DoD
  has no per-phase projection annotations (per-phase projection lives on the child **story** issues,
  already ticked there), so the spine's DoD dimension takes its **historical-walk** fallback naturally:
  walk every epic-level bullet and judge it against the **accumulated diff**. The evaluator's job here
  is to verify the accumulated diff satisfies those epic-level bullets — not to re-verify per-phase
  projections. Read the epic's story set (its sub-issues, or a legacy epic's `## Stories` checklist —
  [`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md)), `## Definition of done`, and `## Goal` for grounding;
  verify `Fixes #<epic>` is present and every story PR is listed in the body.
- **Always-gated merge.** `facts.config.merge_policy` reports `epic-integration → ask` unconditionally
  (prep forces it; a stray `epic-integration:` line in the config block is ignored). So the spine's
  S7-gate **always** runs for this route even on a clean APPROVE — an epic-integration merge is never
  hands-free. The gate's **Approve** splits into **Approve (merge commit)** / **Approve (squash)** to
  capture the strategy (merge commit when `commit_count > 1`, preserving per-story squash commits in
  `main`'s history; squash on the rare single-commit epic).

**Load-bearing re-adjudication of the stories' deferrals.** Integration is the final accumulation
point, and what the stories deferred never reaches this PR's body: each story's `## Polish` ledger and
`## Follow-ups` stayed on its own PR. Prep reads them into `facts.epic.residue` — the PRs merged into
the epic branch, each with its `story`, `polish` (every entry but `applied`) and `follow_ups`, under
`story_prs` (or the JSON at `story_prs_path` when spilled), and each follow-up's `kind`, `state` and the
ledger entries it `covers` ([`../../_shared/follow-ups-section.md`](../../_shared/follow-ups-section.md))
— so never fetch a story PR to find them. The spine's dimension-4 re-adjudication covers, against the
epic's `## Definition of done` and the in-scope `binding` grounding docs:

- every `open_follow_ups` issue (a `null` state is unreadable — judge it as open), plus this PR's own
  `## Follow-ups` deferrals. A ledger entry the issue `covers` is judged through it, once;
- every `file_polish` entry — a `file` entry no `Filed:` line ties to an issue (a story PR merged before
  4.37.0), judged on its item and anchor;
- every `undecided_polish` entry (`attention` names them): a merged story PR should carry none, so each
  merged around its story's evaluation — judge it as a deferral.

Re-check a `file_polish` or `undecided_polish` entry's claim at this PR's head first: nothing ties it
to an issue whose state would say it shipped, so one whose claim no longer holds at head is resolved —
note it, never block (a story adopted for it has landed on the epic branch). A deferral that maps to
an epic DoD bullet or an in-scope binding-doc section blocks the integration → soft-reject, naming the
bullet or doc section, the deferred-to issue (or story PR + ledger id) and the story it came from. The
work lands on the epic branch only through a story, so this soft-reject routes to the **slicer** to
adopt the issue as one (`## Handoff`). Closed follow-ups, a `kind: pull_request` reference and `drop`
entries were decided at their story's merge: context, never a block. The story ledgers are **not** put back on a
`Polish` card — S5.5 adjudicates this PR's own ledger only. Genuinely out-of-scope or future work →
note, don't block. When `residue` is null (`EPIC_RESIDUE_UNAVAILABLE`), say in the review that the
stories' deferrals were not evaluated.

Everything below runs **only after S7-merge actually merged**; on any no-merge exit, skip to the
handoff. Epic integration files no story bookkeeping (no story to close, no epic checkbox to tick,
no delivery-log line — those are the child-story route's actions); the epic closes via `Fixes #<epic>`
on the merge into `main`.

## After the merge — residual follow-ups + cleanup

File residual non-blocking follow-ups (shared with the other routes): de-dup against the PR body's
`## Follow-ups` `Filed:` lines, fold in the `## Polish` ledger's `file` entries (one issue per
recorded `group:`, each briefed from its item and note — `polish-ledger.md` "Rules"), and file each
related group via [`../../_shared/follow-up-filing.md`](../../_shared/follow-up-filing.md) (parent
reference = this PR + the epic issue), and weave each into the PR body's `## Follow-ups` as a
`Filed:` line with `edit-pr-body`, as `standard.md` "After the merge" does. Then purge the scratch dir only
(`rm -rf "<facts.scratch>"`) — the worktree is **deliberately retained** (this session runs inside
it); the terminal handoff's fence hands the operator
`/ctrl-freaq:workspace-close <facts.workspace.branch>` for the teardown + gated removal.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md) and emit the **Epic
integration PR clean merged — terminal** shape: Epic line, PR line (`merge: merge → main@<sha>` for a
merge commit, or `squash → main@<sha>`), Cleanup line, `Next: (terminal — no follow-up skill)`, and a
`Why:`. `review:` is `APPROVE (operator)` (epic is always gated). The epic closes via `Fixes #<epic>`;
the pipeline ends here.

On a **no-merge** exit, emit the matching rubric shape — **soft-reject → re-route** to
`/ctrl-freaq:resolver #<epic>` (or `/ctrl-freaq:planner revise #<epic>` when it carries
an S4-untick or a plan-changing polish `apply`; or `/ctrl-freaq:slicer #<epic>` when it carries a
story deferral that maps to the epic's DoD — the slicer adopts the deferred-to issue as a story, the one
writer of that edge) — or the **re-gate** route to `/ctrl-freaq:evaluator #<PR>` for `head-moved`, or
the resolver re-route `/ctrl-freaq:resolver #<epic>` for `base-moved` and `DIRTY` (its epic route
owns drift rectification; the PR stays ready), or **APPROVE-but-skipped** with the manual, pinned
`gh pr merge` command for BLOCKED / deferred.
