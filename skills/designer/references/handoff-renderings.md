# Handoff renderings — designer

Every session that posts an approved design record ends with a single `## Handoff` block. The schema,
omission rules and closed-set state-marker vocabulary live in
[`../../_shared/handoff-format.md`](../../_shared/handoff-format.md); this file holds the designer's shapes.
**Pick the one that matches the run, copy its shape, and substitute only the data** — issue number, title,
state, type, the epic's counts and the record's URL. Field names, structure and markers are contract: never
rename a field, never drop the `· <state> ·` segment, never add a block the shape does not have, never
inline the fenced `Next:` command into prose.

Every shape forwards to the planner (`/ctrl-freaq:planner #<N>`). The designer adds **no** state marker for
the record: whether the planner carries it forward is the planner's. Its prep reports no dossier and no
plan, so the `research:` and `plan:` markers are omitted too, as the shapes show. `Workspace:` is
omitted at seat `root`, where the planner prep's checkout assertion is the backstop. At seat `own-branch`
(`facts.workspace.seat`) add `**Workspace:** <facts.workspace.path> — start the next session here` on its
own line before `**Next:**`: a planner revise of an issue with an open PR grounds on that PR's head (row
`open-pr-head`), which only the issue's worktree serves, and a plan-less issue's own branch serves while it
contains the base tip (row `own-branch`). This line is the one sanctioned addition to a copied shape — the
"never add a block" rule above does not bar it — and the fenced shapes below are the seat-`root` form. The `<type>` segment is the shared schema's closed
set, never a raw label.

## A standalone issue

```
## Handoff

**Issue:** #142 — Show the weekly summary · open · feature

**Next:** plan the build in a fresh session; the planner plans to the approved design record.

    /ctrl-freaq:planner #142

**Why:** the operator approved the interface on a working prototype; the design record (https://github.com/owner/repo/issues/142#issuecomment-XXXXX) holds its states, a capture of each, and where the prototype lives (`Prototype:`).
```

## A story under an open epic

`facts.epic` is set: the shared schema's `Story:` line plus the parent's progress on an `Epic:` line.

```
## Handoff

**Story:** #204 — Design an issue's user interface with the operator · open · story
**Epic:** #201 — Epic: Design the user interface before it is built · open (2 of 7 stories closed)

**Next:** plan the build in a fresh session; the planner plans to the approved design record.

    /ctrl-freaq:planner #204

**Why:** the operator approved the interface on a working prototype; the design record (https://github.com/owner/repo/issues/204#issuecomment-XXXXX) holds its states, a capture of each, and where the prototype lives (`Prototype:`).
```

## Either shape, with no `ui-prototype` block

The repo declares no `<!-- ui-prototype -->` block, so the operator said how to prototype for this session
(spine D1). Keep the matching line shape above and add the setup pointer to the `Why:`:

```
**Why:** the operator approved the interface on a working prototype; the design record (https://github.com/owner/repo/issues/142#issuecomment-XXXXX) holds its states and captures. This repo has no `<!-- ui-prototype -->` block, so the session prototyped as the operator said; run `/ctrl-freaq:setup` to record that as the block, so the next design session reads it.
```
