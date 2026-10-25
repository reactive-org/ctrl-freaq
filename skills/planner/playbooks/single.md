# Single-issue plan

Route for a fresh **standalone** issue — a bug, feature, incomplete feature, multi-phase issue, or a
story with no open parent epic (v1's "Everything else"). `facts.plan_ref` is `main` (row
`no-open-pr-default-branch` or `story-no-open-parent-epic`) — or, when this session sits in the issue's own
worktree and it already contains `main`'s tip, that branch (row `own-branch` / `story-own-branch`); either
way the plan lands on `main` via the resolver's PR.

**Run the spine first.** Read [`plan-spine.md`](plan-spine.md) and execute it end to end (classify →
ground → gates → draft → hedge sweep → verify → show → persist). The deltas this route supplies:

- **Schema sections.** The standard single-issue schema in
  [`../references/plan-schema.md`](../references/plan-schema.md). Add `## Coverage gap` for a bug fix
  (never optional for a bug — name which test should have caught it and the regression test that fails
  pre-fix). Add the structured `## Phases` section (fixed keys `kind` / `ships` / `closes-dod` /
  `deliverable` / `depends-on`, plus `sub-issue` whenever `facts.slices` is present, plus `checkpoint`
  on every `code-shipping` phase but the last — `pause` or `continue`, per the schema) **only** when S1
  classified the work as multi-phase — the resolver parses it deterministically to route each phase;
  free-form sequencing prose is the #640 regression.
- **Reconcile the phases against the live sub-issue set.** When `facts.slices` is present the target's
  sub-issues are its deliverable slices, and they are an **input constraint** on the plan's shape, not
  an output of it. Read
  [`../references/sub-issue-reconciliation.md`](../references/sub-issue-reconciliation.md) before
  drafting `## Phases`: it owns the `sub-issue:` cardinality rule, the diff cases prep already computed
  in `facts.slices.diff`, and the mismatch gate (which gates or re-routes — it never silently re-cuts).
- **Reviewer dimensions (spine S7).** `1, 2, 3, 4, 6`; add `9` for a bug fix (coverage-gap closure); add
  `7` for a multi-phase issue (phase coherence) — passing `<<live_slices>>` from `facts.slices` so its
  slice-coverage check runs. Dimension 10 is added by the spine whenever the plan carries
  `## Open questions`.
- **Off-ramp (spine S4).** `off-ramp: epic + slicer offered` — a standalone issue too large to plan as
  one unit may be an Epic filed as one issue (seams mostly out-of-slice, each shippable); one carrying
  two or more milestones demonstrable on one branch is planned multi-phase unless the operator chooses
  to track them as deliverable slices. The shape triage puts both to the operator.

Everything below runs only after the spine returns; on a re-route or trivial-skip exit, emit the
matching handoff instead.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md): render the
`## Plan summary` block per its predicate at `focus: none`, then match the outcome:

- **Single-issue plan posted** (default): `Issue:` line (`plan: ✓ (<url>)`) + `Grounding:` (`read at
  <plan-ref>@<short-sha> · <docs>` — `origin/main`, or the bare own branch) + `**Open questions:**` when the plan carries
  that section + `Next: /ctrl-freaq:resolver #<N>`, `Why:`.
- **Trivial change — no plan** (S1 scale-to-work declined): `Issue:` line `plan: ✗`, no `Grounding:`;
  `Next: /ctrl-freaq:resolver #<N>`.
- **Knowledge-gap re-route** (S3 hit ungroundable external truth): `research: ✗ · plan: ✗`; `Next:
  /ctrl-freaq:researcher #<N> — <the ungroundable fact>`; `Why:` names it verbatim.
- **Epic-shaped, planning aborted** (seam gate chose "Split as epic"): `plan: ✗`, no `Grounding:`;
  `Next: /ctrl-freaq:slicer` promoting #N to an Epic per the seam-analysis comment.
- **Slice first chosen** (the operator picked "Slice first" at the seam gate): `plan: ✗`, no `Grounding:`,
  nothing posted; `Next: /ctrl-freaq:slicer #<N>`. It hands back — re-run the planner after the cut.
- **Open-question total block** (every plannable part gated by an unresolved OQ): terminal-style, `plan:
  ✗`, `Next:` names no follow-up skill (a human answers), with a re-run breadcrumb; if no companion
  question is filed yet, point at `/ctrl-freaq:drafter` to file one first.
