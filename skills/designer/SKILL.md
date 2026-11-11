---
name: designer
description: Design a filed GitHub issue's user interface with the operator before it is built — iterate on a working prototype built and run the way the repo's own `<!-- ui-prototype -->` block says, agree every state a user can meet (empty, loading and error included), capture each one, and on the operator's approval record the target as a `<!-- ux-design:v1 -->` design record on the issue, then hand back to the planner. Trigger on requests to design, prototype, mock up or agree the UI / screens / interface / UX of #N before planning or building it ("design the interface for #N", "let's prototype #N's screen first", "work out what #N should look like", "revise the design on #N"). Do NOT use for: filing an issue (drafter), planning the approach or file changes (planner), writing the production code (resolver), reviewing or merging a PR (evaluator), or setting up how the repo prototypes (setup).
---

# designer — router

Shape a filed issue's user interface with the operator on a working prototype, before anyone plans or
builds it, and record the design they approve. One session; the conversation is the operator's to drive,
and nothing survives except the `<!-- ux-design:v1 -->` record on the issue and a tracked prototype pushed
to the issue's own branch. Read this router, run prep, run the one routed playbook, hand off. Your judgment
is the prototype itself, the states proposed, how the block's prose applies, and the `Why:`.

## 1. Prep

Assemble the entire starting state in **one** call, from the checkout the session runs in:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_designer.py <issue> <owner/repo>
```

It returns one JSON **facts block** (`architecture.md §4`): `target` (number/title/state/labels/`type`),
`epic` (the open parent epic's `number`/`title`/`state`/`closed`/`total`, or `null`), `vector.mode`
(`fresh` | `revise` — the routing contract), `suggested_playbook`, `record` (the issue's one trusted design
record as `scripts/design_record.py` reads it — `present`, `comment_id`, `url`, `prototype`, `states`,
`approval`, `approved`, `findings` — plus `body_path`, its verbatim body staged in the scratch dir, or
`null`), `block` (the repo's `<!-- ui-prototype -->` block: `present`, `source`, `text`), `ui_design` (the
repo's `role: ui-design` catalogue docs, each with `path`, `authority`, `present`, `kind`, `members`,
`abs_path`), `workspace` (the asserted checkout: `path`, `branch`, `sha`, `dirty`, and `seat` — `root` or
`own-branch`), `scratch`, `sections` (the spilled issue body and thread), `attention` and `notices`. Consume
each as **data**: never re-derive the mode, the seat or the record's approval.

**Decision card rule.** If prep exits `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)),
act on the answer, and re-run prep — the single handler for every closed-set code (`AUTH_REQUIRED`,
`TARGET_IS_PR`, `MARKER_AMBIGUOUS` — two trusted records, keep one; `AMBIGUOUS`; `WORKSPACE_MISMATCH` — the
session is in a checkout that is not the project root or this issue's own branch, or one behind or diverged
from its remote: the fix is the operator's, never a checkout this session performs).

**Refusals.** An `attention` line naming a **closed** or **`question`** target → say so in one line and
stop: no prototype work, no record, no handoff. A question is answered by a human, never designed.

## 2. Route

Prep proposes `suggested_playbook`, keyed on `vector.mode`. Read **exactly one** playbook; it opens the
session its own way and then runs the shared spine [`playbooks/design-spine.md`](playbooks/design-spine.md)
(grounding → workspace → prototype → states → captures → approval → commit → persist → handoff). The modes
differ in the actions that open the session, so each is its own thin playbook — no mode conditional.

| `vector.mode` | Playbook | What it does before the spine |
|---|---|---|
| `fresh` (no trusted record) | `playbooks/fresh.md` | starts from the issue body, the thread and the operator's answers |
| `revise` (a trusted record exists) | `playbooks/revise.md` | starts from the record's states, captures and `Prototype:` location; a `pending` record goes straight to approval |

No route override: the mode is the record's presence, a fact.

## 3. Invariants

- **The repo says how it prototypes; no prompt does.** Where a prototype lives, what it is built with, how
  it runs and how a state is captured come from `facts.block.text` — the operator's prose, read by you —
  or, with no block, from the operator's answer this session (spine D1). Never name a tool, a location or a
  capture method of your own, and never fall back to one.
- **Tracked writes only at `own-branch`.** `facts.workspace.seat` decides whether this session may write
  tracked files: at `own-branch` the prototype may live in tracked files on the issue's own branch; at
  `root` the operator's checkout is never written (PRD §8), so the prototype goes outside the repository.
  Every file command names `facts.workspace.path` (or the outside location) by absolute path.
- **A tracked prototype is committed and pushed before the record posts** (spine D7), so `Prototype:` names
  a branch and path every later checkout can read.
- **Staged-body writes, checked first.** Every GitHub write goes through
  `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py` with the body staged at `<facts.scratch>/record.md` and passed
  by **path**, and every staged record runs
  `${CLAUDE_PLUGIN_ROOT}/scripts/design_record.py check <facts.scratch>/record.md` immediately before the
  write. `Approval: approved` is only ever written by a write that carries no `--attach` (spine D8). A zero
  exit with a URL is self-confirming; never re-read it.
- **Only the operator approves.** The session ends with a record only on the `Approve` card's **Approve**.
  There is no reviewer sub-agent: the operator's approval and `check` are the record's verifiers.
- **Gates only for genuine decisions** (`asking-the-user.md`): the `Workspace` operator task, a `binding`
  doc conflict, and the `Approve` consent card. Everything else is the conversation. In a session a
  coordinator started, every card follows "Relayed cards": its question opens with
  `designer · #<N> · <step>`, and the block it relies on prints in the same message.

## 4. Handoff

Every session that posts an approved record ends with a single `## Handoff` block — the only bridge to the
next session. The schema, omission rules and closed-set markers are owned by
[`../_shared/handoff-format.md`](../_shared/handoff-format.md); the designer's shapes (a standalone issue, a
story under an open epic, and the no-block variant of either) are in
[`references/handoff-renderings.md`](references/handoff-renderings.md). **Read that reference immediately
before composing the handoff, then copy the matching shape** — field names, structure and markers are
contract; substitute only the data. Every shape forwards to the planner, `/ctrl-freaq:planner #<N>`. A
session that ends without **Approve** — on **End to update the doc**, on the `Workspace` card's **Done**, or
by the operator stopping — posts nothing and emits no handoff: one line naming where the prototype is (or the
command to run first), and that a re-run picks it up.
