# design spine — shared by fresh and revise

The conversation both modes run after their opening. The mode differences here are **facts**
(`facts.record.present`, `facts.record.comment_id`), never branches. The record's grammar, its fail-closed
`approved` rule and "one record per issue" are owned by
[`../../_shared/design-record.md`](../../_shared/design-record.md); this spine renders them at point of use.
All facts below come from prep (router §1); the scratch dir is `facts.scratch`.

## D1 — Grounding: the block and the ui-design docs

**The `ui-prototype` block.** With `facts.block.present`, read `facts.block.text`: it is the repo's own
prose on where a prototype lives, what it is built with, how to run it and how to capture each state, and
it is the only authority on those four. With no block, ask the operator in plain conversation (an open
question, not a card) how to prototype and how to capture for **this session**, and use their answer for
this session only; the handoff's `Why:` points at `/ctrl-freaq:setup` to record it as the block. Never
supply an answer of your own, and never fall back to one.

**The `ui-design` docs.** Read each `facts.ui_design` entry whose `present` is true (a `kind: dir` entry's
`members`, by absolute path under `facts.workspace.path`) before proposing anything, at the authority the
repo declares:

- **`binding`** — a design that would conflict with it is never built past. Render a judgment card
  (`header: "Design doc"`) quoting the doc's passage and the design point it conflicts with: **Conform**
  (reshape the design to the doc and carry on) / **End to update the doc** (post nothing, emit no handoff;
  the operator changes the doc through its own PR and re-runs the designer).
- **`informative`** — a divergence is announced in one line (`Did X because Y; say if you'd rather Z`).
- **None** (`facts.ui_design` is `[]`, or `DOC_CATALOGUE_ABSENT` in `facts.notices`) — say once that this
  session works from the operator's answers alone.

## D2 — Where the prototype may live

`facts.workspace.seat` decides whether this session may write tracked files; the block's prose decides
where the prototype goes. Read the block (or the operator's D1 answer) for whether the prototype lives in
tracked files:

- **`own-branch`** — a tracked prototype is written in `facts.workspace.path`, on this issue's own branch,
  and committed and pushed at D7. It is ordinary code the plan may keep or restructure.
- **`root`** and the prototype lives in tracked files — the operator's checkout is never written. Render an
  operator-task card (`header: "Workspace"`) with this command printed in the same message:

  ```
  /ctrl-freaq:workspace-open <N>
  ```

  **Done** — end the session: post nothing and emit no handoff; the operator opens the issue's worktree and
  restarts the designer inside it.
  **Elsewhere this time** — ask the operator for a location outside the repository for this session, and
  build there.
- **`root`** and the prototype lives outside the repository — build it where the block says.

Record the location you used, verbatim, for the record's `Prototype:` line (a branch and path, a URL, an
outside path — whatever the block names).

## D3 — Build and run the prototype

Build the prototype and run it exactly as the block says, at the D2 location, then show the operator what
it does. This is a conversation the operator drives: revise the prototype on every piece of pushback, as
often as they push back, and re-run it each time. Ground each proposal on the issue's `## Definition of
done` and the D1 docs. Name the differences each revision makes, so the operator sees what moved.

## D4 — Agree the states

Propose the states a user can meet on every surface the prototype shows — **empty, loading and error are
proposed for every surface**, alongside the states the issue names. A proposed state drops only when the
operator says it cannot occur, and the drop is announced in one line. Each agreed state gets an id matching
`[a-z0-9][a-z0-9-]*` (unique in the record) and a one-line description of what the user sees and does in
it. The record binds what users see and do — flows, states, copy, layout — never the prototype's code.

## D5 — Capture each state

Capture every agreed state as the block says:

- the block names a **store** for captures — store each one there and reference it by path, under D2's
  tracked-write rule (a store in tracked files only at `own-branch`);
- the block names **no store** — capture each to a file under `facts.scratch` and attach it to the record
  at D8;
- the block says **nothing** about capturing, or there is no block — ask the operator how to capture.

## D6 — Approval

Once **every** agreed state has a capture, print the state list — each id, its description and its capture
— and render the consent card (`header: "Approve"`): **Approve** / **Keep iterating** (back to D3 with the
operator's notes). Nothing posts before **Approve**. An operator who stops before answering it gets no
record and no handoff: print one line naming where the prototype is, and that re-running
`/ctrl-freaq:designer #<N>` picks it up.

## D7 — Commit and push a tracked prototype

When the prototype (or a capture store) lives in tracked files — which D2 allows only at `own-branch` —
commit only those files — the pathspec keeps anything else already staged out of the commit — and push
the branch, before any record posts:

```bash
git -C "<facts.workspace.path>" add -- <prototype and capture files>
git -C "<facts.workspace.path>" commit -m "Prototype the interface for #<N>" -- <prototype and capture files>
git -C "<facts.workspace.path>" push -u origin "<facts.workspace.branch>"
```

`Prototype:` then names the branch and the path. A local-only commit is never enough: the prototype would
live in one worktree, and `workspace-close` would refuse to remove it.

## D8 — Stage, check and persist the record

Stage the record to `<facts.scratch>/record.md` in the contract's shape, marker first:

```
<!-- ux-design:v1 -->
**Design record** — #<N> <title> — recorded <ISO-8601 UTC>

Prototype: <the D2 location, verbatim>

## States
- `<state-id>` — <what the user sees and does> — capture: <reference>

Approval: <pending | approved YYYY-MM-DD>
```

A capture stored where the block says is referenced by its path. A capture to attach is staged as
`capture: ![<state-id>](<abs path>)` — the absolute path of the scratch file, which `gh_persist.py` rewrites
to the attachment URL. Run the check before **every** write, and fix any finding in the staged body before
writing (a record posts as approved only when `approved` is `true`):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/design_record.py check "<facts.scratch>/record.md"
```

**The write order.** `Approval: approved` is only ever written by a write that carries no `--attach`, so an
approved record never holds a capture path that did not upload (`--attach` can land partly).

- **Captures to attach.** Stage the record with `Approval: pending`, check, and post it with one `--attach`
  per capture, adding `--delete-marker-id <facts.record.comment_id>` when a record exists (post before
  delete, so the issue is never without one):

  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> issue <N> "<facts.scratch>/record.md" \
    --attach "<abs capture path>#<state-id>" [--delete-marker-id <facts.record.comment_id>]
  ```

  On an `ATTACH_PARTIAL` notice, re-run prep, restage from `facts.record.body_path` (the just-posted record),
  point the failed captures back at their absolute paths, check, and post again with those `--attach` flags
  and `--delete-marker-id <facts.record.comment_id>` — the just-posted record's id, so two records never
  stand. Once a post lands with no `ATTACH_PARTIAL`, re-run prep, restage from `facts.record.body_path` (the
  body carrying GitHub's attachment URLs — never the local file). Every attached capture must now read as
  an attachment URL: one that still names a local absolute path did not upload, so it goes back through the
  `ATTACH_PARTIAL` re-post above, never into an approved record (`check` reads only that a reference is
  present). Then set `Approval: approved <YYYY-MM-DD>`, check, and write it in place:

  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-comment <owner/repo> <facts.record.comment_id> \
    "<facts.scratch>/record.md"
  ```

- **No capture to attach** (every capture stored where the block says, or a revise that keeps the posted
  ones). Stage the approved record directly, check, and write it once — `comment … issue <N>` with no
  record, `edit-comment <facts.record.comment_id>` on a revise. A revise stages from
  `facts.record.body_path`, the posted body with GitHub's attachment URLs, and changes only what the operator
  changed: a record rebuilt from local copies would write local capture paths over those URLs. A capture that
  names a local absolute path is a capture to attach, so the record takes the branch above.

Each prep re-run derives the next write's input and verifies nothing. A session that ends between the
pending post and the approving write leaves a `pending` record; the next run reads it as `revise` and goes
straight to D6.

## D9 — Handoff

Router §4: read [`../references/handoff-renderings.md`](../references/handoff-renderings.md) and copy the
matching shape — `Story:` + `Epic:` when `facts.epic` is set, `Issue:` otherwise, with the no-block
variant's `/ctrl-freaq:setup` pointer when D1 ran without a block. The `Why:` names the record's URL.
