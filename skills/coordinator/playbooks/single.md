# coordinator — one issue on one branch

Read with [`coordinate-spine.md`](coordinate-spine.md); this file is only the order the stages come
in for a standalone bug / feature / incomplete issue, a story invoked directly, or an issue with
deliverable slices (its slices are phases on its one branch, not separate loops).

## 1. The order

```
needs-plan ─▶ needs-workspace ─▶ resolving (re-entered after each session that ends at a checkpoint)
   ─▶ pr-ready ─▶ merged-needs-close ─▶ terminal
```

Prep derives every arrow; this list is for telling the operator where the run is, never for
choosing a stage yourself.

## 2. Single-issue specifics

- **The plan** runs in the project root for a standalone issue. A story plans in its epic's
  worktree; when that worktree isn't open yet, prep reports it first (`needs-workspace` naming the
  epic, its open folded into the planner's go-ahead), and a behind epic worktree is synced first
  (`next.sync_first`), as in the epic flow.
- **Multi-phase work.** The resolver opens a draft PR with a phase tracker and stops where it
  raises a review the operator does not continue past, where its context usage leaves no room for
  another phase (a `context` end, or a 90% wrap-up that leaves the phase interrupted), or after every
  phase with `--pause`. A draft is
  `resolving` again: re-enter the resolver with the same command.
  Say where it stands from `unit.pr.phases` ("phase 2 of 3 next"). The PR turns ready after the last
  phase, and the stage becomes `pr-ready`.
- **A soft-reject** flips the PR back to draft with every phase ticked — `resolving` again, as a
  revision run. A re-plan the evaluator asks for arrives only in its handoff (spine §3).
- **Research and slicing** are detours the planner asks for in its handoff (planner → researcher,
  planner → slicer); they come back to the planner (spine §3).

## 3. Terminal

When the PR has merged, the issue is closed and the worktree is released: summarize the PR, its
merge, and the closed issue. For a **story invoked directly**, stop at its merge and offer, as the
button the router §3 operator-task rule names, to carry on with its parent epic
(`parent_epic.number`): `/ctrl-freaq:coordinator <parent>` — the operator's choice, never automatic.
