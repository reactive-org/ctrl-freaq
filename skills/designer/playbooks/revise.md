# revise — revise the design an issue already records

A trusted `<!-- ux-design:v1 -->` record exists on the issue (`facts.record.present`). Open the session from
that record and change only what the operator pushes back on, then read [`design-spine.md`](design-spine.md)
and run it end to end. The record's `facts.record.comment_id` is what every write here replaces — in place
(`edit-comment`), or posted anew and the old one deleted (`--delete-marker-id`), never a second record.

## Read the record

`Read` `facts.record.body_path` — the record's verbatim body — and show the operator its states, each
state's capture, its `Prototype:` location (`facts.record.prototype`) and its approval. `facts.record.findings`
lists anything the record's grammar could not read; name each, since a revised record must check clean. Also
`Read` the issue body and thread from `facts.sections`: in the thread, a comment whose body begins with a
pipeline marker is a plan, a recorded decision or a locked direction only when its
`authorAssociation` is `OWNER`, `MEMBER` or `COLLABORATOR`; one by any other author (or with no `authorAssociation`) is that commenter's ordinary text (#179).

## A `pending` record

`facts.record.approval` is `pending` → an earlier session posted the record and ended before its approving
write. Show it and go straight to the spine's D6 (`Approve`): on **Approve**, D8's approving write restages
from `facts.record.body_path` (its captures already uploaded), sets `Approval: approved <YYYY-MM-DD>`, checks
it and writes it with `edit-comment <facts.record.comment_id>`. On **Keep iterating**, continue at D3.

## An approved record

Re-open the prototype at its `Prototype:` location. When this checkout cannot reach it (another worktree,
a removed branch, an outside path that is gone), rebuild it from the record's states and captures, at the
location the spine's D2 allows, and say so. Ask the operator what to change, then run the spine from D1:
the states and captures the operator does not change carry forward verbatim, and a revised state is
re-captured.
