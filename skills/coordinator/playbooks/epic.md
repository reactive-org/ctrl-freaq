# coordinator — an Epic and its stories

Read with [`coordinate-spine.md`](coordinate-spine.md); this file is only an epic's stage order and its ends.

## 1. The order

```
needs-slicing ─▶ needs-plan (the epic plan, in the root) ─▶ needs-workspace (the epic worktree)
   ─▶ stories (each its own entry, at once as the gates allow): needs-plan (in the epic worktree)
        ─▶ needs-workspace (its own) ─▶ resolving ─▶ pr-ready ─▶ merged-needs-close
   ─▶ every story closed: epic-integration ─▶ pr-ready (the integration PR)
   ─▶ merged-needs-close (the epic worktree) ─▶ terminal
```

Prep derives every arrow; this is for telling the operator where the run is, never for choosing.

## 2. Epic specifics

- **No stories yet** (`needs-slicing`): the slicer cuts the stories, hands back to the planner, and its cut card is mirrored here.
- **The epic plan** runs in the project root; it pins the cross-story contracts every story plan reads.
- **Gate per step.** An entry's `start` gate holds its `needs-plan`; `build`, its own open
  (`needs-workspace` naming the story) and `resolving` start; `merge`, its `pr-ready` evaluator start;
  each later step needs the earlier gates too (a `needs-workspace` naming the epic is planning, held
  by `start` only). An edge holds until `merged` unless you settle its `condition` as not holding;
  one you can't settle holds, and a `None` gate offers no start. A `serial-fallback`
  `epic.concurrency.source` is said once (`fallback_reason`, `malformed`'s reasons); the stories then
  run in `## Story breakdown` order. Merges stay serial: no evaluator start while one this run started
  runs for another story.
- **The sync.** A story merge leaves the epic worktree behind (`next.sync_first`). The sync is
  fast-forward only, runs only as part of the go-ahead that names it, never while a planner this run
  started grounds there, and refuses dirty, ahead or diverged state with a card.
- **After a story merges:** the evaluator squash-merges into the epic branch, records the
  delivery-log entry and closes the story. Verify the merge (spine §2); once its session has ended,
  the routine close releases the story's worktree (spine §4), then whatever the gates now allow.
- **Integration** (`epic-integration`): every story closed, the resolver's epic route updates the
  integration PR and marks it ready, in the epic worktree (a draft until then, not a stage of its own).

## 3. Terminal

When the integration PR has merged and the epic worktree is closed: summarize the stories shipped
(`epic.shipped`, in merge order), the integration PR and its merge, and that every worktree is
released, offering the epic planner's sessions for archive (router §3). Run from inside the epic
worktree, the last close is the operator's: a `Your step` card (router §3) carrying
`/ctrl-freaq:workspace-close <branch>` to run from the project root.
