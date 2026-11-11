# Design detour — offer the designer before drafting, and plan to the approved design

Read at spine S1 when the offer may fire, and at S3 / revise when an approved design record exists. The
record and the `ui-prototype` block are defined once in
[`../../_shared/design-record.md`](../../_shared/design-record.md); prep reads both as `facts.design`
(`record` — `design_record.read_record`'s payload with its `fingerprint` and the staged `body_path`;
`block`; `plan_target` — the record a prior plan cites; `adopted`). Consume them as data: never parse the
record or the block yourself.

## 1. When the offer fires

The `Design` card renders at S1 — after the DoD-heading card and the latest-direction gate, before S2 —
when **all** of these hold:

- `facts.vector.mode` is `fresh` and `facts.plan.present` is false. Once a plan exists the planner never
  asks again: a design for a planned issue is the designer's re-run followed by a planner revise.
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
  `findings`, or a state with no capture): **Finish the design** / **Plan without the record**. Name what
  stands in the way, from `approval` and `findings`.

Recommend per instance: designing first where layout, states or copy are open and a user would notice a
wrong guess; planning without where the surface is a small change to an existing screen whose precedent
pins it.

## 3. Accept — re-route to the designer, posting nothing

On **Design first** / **Finish the design** the session ends here: no plan comment, no `planned` label, no
body pointer. Render the "Design detour — re-route to the designer" shape in
[`handoff-renderings.md`](handoff-renderings.md): `plan: ✗`, `Next:` `/ctrl-freaq:designer #<N>`, and a
`Why:` naming the user-facing surface and the case that fired (no record, or an unapproved one and why).

**`Workspace:` line** — a judgment over `facts.design.block.text`, never a script fact (the block has no
canonical form). Add it only when the block places the prototype in tracked files on the issue's own
branch: `(none yet — run /ctrl-freaq:workspace-open <N> --design first)`, or the issue's own worktree when
`facts.grounding.basis` is `own_branch` (this session already sits in it). Otherwise — the prototype lives
elsewhere (a separate repo, a design tool, the main checkout) — omit it. With no block
(`facts.design.block.present` false) omit it too: the designer asks the operator where the prototype lives.

## 4. Decline — plan as today

On **Plan without a design** / **Plan without the record**, continue at S2 unchanged. A plan made this way
cites no record: no `Design target:` line, and its UI decisions cite `ui-design` precedent as before.

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
- The plan reviewer receives the record (`<<design_record>>`, spine S7) and checks the target line and
  every cited state id against it.

## 6. Revise — adopt a revised record

`facts.design.adopted` false means the prior plan cites a record the designer has since revised. The revise
re-cites the current record — a new `Design target:` line and the state ids it now carries — and the
confirm's "What changed" ([`revise-reconciliation.md`](revise-reconciliation.md), "What the confirm shows")
names the design-target change and each UI decision it moved. `adopted` true, or `null` with no record, needs
nothing here. A revise for an issue whose record became approved after a plan made without it drafts to §5
like any plan, and names the new citation the same way.
