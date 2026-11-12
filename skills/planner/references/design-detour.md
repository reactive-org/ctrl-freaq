# Design detour — offer the designer before drafting, and plan to the approved design

Read at spine S1 when the offer or its revise variant may fire, and at S3 / revise when an approved design
record exists or a prior plan cites one. The
record and the `ui-prototype` block are defined once in
[`../../_shared/design-record.md`](../../_shared/design-record.md); prep reads both as `facts.design`
(`record` — `design_record.read_record`'s payload with its `fingerprint` and the staged `body_path`;
`block`; `plan_target` — the record a prior plan cites; `citation` — how that citation stands against the
current record, one of `none` / `missing` / `unapproved` / `current` / `revised`). Consume them as data:
never parse the record or the block yourself, and never infer the citation's case from the other facts.

## 1. When the offer fires

The `Design` card renders at S1 — after the DoD-heading card and the latest-direction gate, before S2 —
when **all** of these hold:

- `facts.vector.mode` is `fresh` and `facts.plan.present` is false. Once a plan exists the planner never
  offers the detour again: a design for a planned issue is the designer's re-run followed by a planner
  revise. A revise asks only §2's revise variant, a different question.
- The target is not an epic. An epic plans at coordination altitude; each story gets its own offer when it
  is planned.
- S1 did not judge the change trivial (a one-line fix routes to the resolver with no plan at all).
- The issue has a **user-facing surface**: something a user sees or does changes — the same judgment that
  keeps `## UI decisions` in a plan ([`plan-schema.md`](plan-schema.md), "omit if no UI surface"). An issue
  with none sees no card and no change.
- `facts.design.record.approved` is not true.

It is a **consent** card: it always renders when these hold, and no rigor level removes it (`facts.rigor`
has no step for it) — the operator decides whether the interface is designed before it is planned [user
decision 2026-11-09].

## 2. The card

`header: "Design"`. The brief, in plain words: what the user-facing surface is (one line, from the issue);
that designing first means an agreed prototype and captured states the plan and the build are then held
to; that planning without one leaves the UI decisions to precedent and the build. Two variants:

- **No record** (`facts.design.record.present` false): **Design first** / **Plan without a design**.
- **A trusted record that is not approved** (`present` true, `approved` false — `approval` `pending`, or
  `findings`, or no state, or a state with no capture): **Finish the design** / **Plan without the record**.
  Name what stands in the way, from `approval`, `findings` and `states`.

Recommend per instance: designing first where layout, states or copy are open and a user would notice a
wrong guess; planning without where the surface is a small change to an existing screen whose precedent
pins it.

**The revise variant — not the offer.** On a revise (`facts.plan.present`), at the same S1 point, the card
renders when `facts.design.citation` is `unapproved` or `missing`, and on no other state. The prior plan
cites a design record the operator approved, and that agreed design is no longer there to bind the build —
a question the offer does not ask, so it fires only for a plan that already cites a record:

- **`unapproved`** (the cited record is `pending`, has findings, has no state, or has a state with no
  capture): **Finish the design** / **Plan without the record**. The brief names what stands in the way,
  from `facts.design.record.approval`, `findings` and `states`.
- **`missing`** (the cited record was deleted, or only an untrusted copy remains, which the reader skips):
  **Design again** / **Plan without the record**.

The brief says that the posted plan keeps citing the last approved record until one of these is chosen, and
what each path leaves the build bound to. It is a consent card like the offer: it always renders on these
states, at every rigor level [user decision 2026-11-12].

## 3. Accept — re-route to the designer, posting nothing

On **Design first** / **Finish the design** / **Design again** the session ends here: no plan comment, no
`planned` label, no body pointer. In a composite epic+story session
([`../playbooks/story-jit.md`](../playbooks/story-jit.md), "Bootstrap the epic plan when absent") the card is
the story's, asked at its S1 before the epic plan is authored, so accepting bootstraps no epic plan either:
nothing is posted, and the designer's `/ctrl-freaq:planner #<N>` bootstraps it then. Render the "Design detour — re-route to the designer" shape in
[`handoff-renderings.md`](handoff-renderings.md): `Next:` `/ctrl-freaq:designer #<N>`, and a `Why:` naming
the user-facing surface and the case that fired (no record, an unapproved one and why, or — on a revise — a
cited record now unapproved or missing). The `plan:` marker is `✗` on a fresh run and `✓ (<prior plan
url>)` on a revise: the prior plan stays posted and keeps citing the last approved record, so the build
stays bound to it until the designer approves again, and the designer's `/ctrl-freaq:planner #<N>` then
re-enters as a revise with `citation: revised`, since the record the designer approves differs in text from the
one the plan cites (its `Approval:` line, a revised state, or a new record's header), which moves its fingerprint (a record back at exactly
the text the plan cites — hand-restored, or re-approved the same day unchanged — reads `current` instead; §6 handles either with no card).

**`Workspace:` line** — a judgment over `facts.design.block.text`, never a script fact (the block has no
canonical form). Add it only when the block places the prototype in tracked files on the issue's own
branch: the issue's own worktree, `facts.grounding.path`, when this session already sits in it —
`facts.grounding.basis` is `own_branch`, **or** `facts.vector.plan_ref_row` is `open-pr-head` with `basis`
`plan_ref` (a revise of an issue with an open PR grounds on that PR's head branch, the issue's own, so a test on
`own_branch` alone would print "none yet" for an open worktree; under basis `identical` the session sits in
another branch's checkout, never the issue's); else `(none yet — run
/ctrl-freaq:workspace-open <N> --design first)`, on a fresh run and a revise alike, whose `--design` sends the
open on to the designer with or without a plan. Otherwise — the prototype lives
elsewhere (a separate repo, a design tool, the main checkout) — omit it. With no block
(`facts.design.block.present` false) omit it too: the designer asks the operator where the prototype lives.

## 4. Decline — plan as today

On the offer's **Plan without a design** / **Plan without the record**, continue at S2 unchanged. A plan
made this way cites no record: no `Design target:` line, and its UI decisions cite `ui-design` precedent as
before. On the revise variant's **Plan without the record**, continue at S2 too, under §6.

## 5. Planning to an approved record

When `facts.design.record.approved` is true, the record is the precedent for **what users see and do** —
flows, states (empty, loading and error included), copy and layout — and binds them. It never binds the
prototype's code structure: `## Architecture decisions` still cite codebase precedent and docs, grounded by
S3 as for any plan.

- `## UI decisions` opens with exactly one target line, built from the facts:

  ```
  - Design target: <facts.design.record.url> · fingerprint <facts.design.record.fingerprint> · approved <facts.design.record.approved_on>
  ```

  `scripts/design_record.py` `cited_target` reads it back, so keep it verbatim.
- Every other UI decision cites the record's state ids — `[precedent: design record \`<state-id>\`]`, or
  several ids comma-separated — or `ui-design §X` / `DEVIATION (agreed <date>)` for what the record does not
  cover. A UI decision that contradicts a recorded state is not a precedent choice: it is a deviation the
  operator agrees to, or the design is revised first.
- The plan reviewer receives the record (`<<design_record>>`, spine S7) and the expected target line built
  from these same facts (`<<design_target>>`), and checks the plan's target line and every cited state id
  against them.

## 6. Revise — per `facts.design.citation`

A revise reads the one state prep derived; each value has one rule:

- **`none`** — the prior plan cites no record. Nothing changes here, unless `facts.design.record.approved`
  is now true (a record approved after a plan made without it): then draft to §5 like a fresh plan and name
  the new citation in the confirm's "What changed".
- **`current`** — the plan already cites the current approved record. No card and no re-citation; the
  target line is still rebuilt from the facts per §5, so a record reposted byte-for-byte — its
  `**Design record** — … — recorded <timestamp>` header included, since the fingerprint hashes the whole body
  (same fingerprint, new comment URL) — gets its new URL, which the reviewer's `<<design_target>>` check
  expects. A repost that restamps the header reads `revised` and is re-cited with no card.
- **`revised`** — the designer revised and re-approved the record since the plan cited it. Re-cite the
  current record with no card: a new `Design target:` line and the state ids it now carries. The confirm's
  "What changed" ([`revise-reconciliation.md`](revise-reconciliation.md), "What the confirm shows") names the
  design-target change and each UI decision it moved.
- **`unapproved`** / **`missing`** — §2's revise variant renders first. On accept, §3. On **Plan without the
  record**, remove the `Design target:` line and every `design record` citation, and re-ground each of those
  UI decisions on `ui-design` precedent or a `DEVIATION (agreed <date>)`. The confirm's "What changed" names
  the dropped citation and each re-grounded decision. Never re-cite an unapproved record: the reviewer blocks
  any `Design target:` line when no approved record is staged.

A dropped citation, or a UI decision moved on a shipped phase, is judged by
[`revise-reconciliation.md`](revise-reconciliation.md)'s classification like any other change.
