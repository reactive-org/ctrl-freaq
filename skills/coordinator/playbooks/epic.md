# coordinator — an Epic and its stories

Read with [`coordinate-spine.md`](coordinate-spine.md); this file is only an epic's stage order and its ends.

## 1. The order

```
needs-slicing ─▶ needs-plan (the epic plan, in the root) ─▶ needs-workspace (the epic worktree)
   ─▶ per story: next-story ─▶ needs-plan (in the epic worktree; sync first when behind)
                 ─▶ needs-workspace (the story's worktree) ─▶ resolving ─▶ pr-ready
                 ─▶ merged-needs-close ─▶ next-story …
   ─▶ every story closed: epic-integration ─▶ pr-ready (the integration PR)
   ─▶ merged-needs-close (the epic worktree) ─▶ terminal
```

Prep derives every arrow; this is for telling the operator where the run is, never for choosing.

## 2. Epic specifics

- **No stories yet** (`needs-slicing`): the slicer cuts the epic into stories and hands back to the
  planner; its cut card is mirrored here, answerable in either session.
- **The epic plan** runs in the project root; it pins the cross-story contracts every story plan reads.
- **Stories are serial.** One story is in flight at a time — just-in-time planning grounds each
  story on the merged tip of the epic branch, so a story planned before its predecessor merges
  would ground on code that is about to move. When `attention` reports more than one story with an
  open worktree, ask which one to drive and pass it as `--story`.
- **The sync.** A story merge moves the epic branch, so the epic worktree is behind before the
  next story's plan (`next.sync_first`). The sync is fast-forward only, runs only as part of the
  go-ahead that names it, and refuses dirty, ahead or diverged state with a card.
- **After a story merges:** the evaluator squash-merges into the epic branch, records the
  delivery-log entry and closes the story. Verify the merge (spine §2); once the evaluator's session
  has ended, the routine close releases the story's worktree (`merged-needs-close`, spine §4); then
  the next story.
- **Next story:** take the evaluator's relayed `Next:`; without it, pick per spine §4 and announce.
- **Integration** (`epic-integration`): with every story closed, the resolver's epic route brings
  the integration PR up to date and marks it ready, in the epic worktree — a draft while stories
  landed, as expected, not a stage of its own.

## 3. Terminal

When the integration PR has merged and the epic worktree is closed: summarize the stories shipped
(`epic.shipped`, in merge order), the integration PR and its merge, and that every worktree is
released. If the coordinator ran from inside the epic worktree, the last close is the operator's: a
`Your step` card (router §3) carrying `/github-pipeline:workspace-close <branch>` to run from the
project root.
