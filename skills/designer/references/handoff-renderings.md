# Handoff renderings — designer

Every session that posts an approved design record ends with a single `## Handoff` block. The schema,
omission rules and closed-set state-marker vocabulary live in
[`../../_shared/handoff-format.md`](../../_shared/handoff-format.md); this file holds the designer's shapes.
**Pick the one that matches the run, copy its shape, and substitute only the data** — issue number, title,
state, type, the epic's counts and the record's URL. Field names, structure and markers are contract: never
rename a field, never drop the `· <state> ·` segment, never add a block the shape does not have, never
inline the fenced `Next:` command into prose.

Every shape forwards to the planner (`/ctrl-freaq:planner #<N>`). The designer adds **no** state marker for
the record: whether the planner carries it forward is the planner's. The `research:` and `plan:` markers
are carried only when the issue already has them (a dossier posted; a plan posted). `Workspace:` is
omitted: the planner's route is checkout-agnostic here. The `<type>` segment is the shared schema's closed
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
