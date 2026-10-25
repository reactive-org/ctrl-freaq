# Just-in-time story plan

Route for any target under an **open** parent epic (`facts.story.parent_epic_open`) — the fact, not a
`story` label: a sub-issue labelled by kind of work (`bug`, `tech-debt`) reaches here too when its
native parent resolved to an open epic with an integration branch (#31). Owns **both** the fresh and
the revise path for such a target (a prior plan just refreshes in place). Planning the
story now — not up front with its siblings — is what keeps it current: it grounds against the epic
branch HEAD *after* every story its `### Dependency graph` `start` edges name has landed (every
predecessor, when the epic plan has no declaration or one off its grammar). `facts.plan_ref` is the parent epic's
`epic/<N>-<slug>` branch (row `story-under-open-epic`), or the story's own open PR head when one exists
(row `open-pr-head` wins), or **this story's own branch** when the session sits in its worktree and that
branch already contains the base ref's tip (row `story-own-branch` — the post-`workspace-open`, pre-PR
window; a strict superset of the base, so it grounds on more truth, never less), or `main` when the
parent's integration branch hasn't bootstrapped yet (row `story-parent-epic-bootstrap` — the branch is
created only once the resolver implements the first story; independent of whether the epic *plan* itself
already exists, see the next bullet).

**Run the spine first.** Read [`plan-spine.md`](plan-spine.md) and execute it end to end. The deltas
this route supplies:

- **Persist (spine S8).** No prior plan → the spine's `comment` as written. A prior plan
  (`facts.plan.present`) → `gh_persist.py edit-comment <owner/repo> <facts.plan.comment_id>
  "<facts.scratch>/plan-main.md"`, the same in-place update `revise.md` uses, and under the same rule: the
  staged body is a full replacement authored against the schema, never a delta appended to what is
  there. This route owns its own revise path, so it names the op rather than inheriting a default.

- **Bootstrap the epic plan when absent (a judgment fill, not a named path).** If
  `facts.story.epic_plan.present` is false, the open parent epic has no plan yet. Author the epic plan
  first (the `epic.md` mechanics, run inline against `facts.story` — grounding stays at `main`, the
  bootstrap ref, since no epic branch exists yet), then continue in this same session to the story plan
  against it. This is a **composite epic+story session**; the emitted handoff is the story's (see below).
- **Carry the operator's answers down.** Every `[user decision <date>]` the epic plan's
  `## Architecture decisions` labels `(#<this story>)` goes into this plan's `## Architecture decisions`
  verbatim, with its date — the operator already answered it (`plan-schema.md`, "Altitude").
- **Keep to the epic's file ownership.** When the epic plan carries `### File overlap`, this plan's
  `## Changes` edits only surfaces a row assigns to this story or no row names. A surface the epic routes
  to another story, the finalization story's shared docs included, is left alone, and a binding doc this
  story contradicts is declared in `## Deviations from project docs` instead. If this story must edit one
  anyway, an `## Architecture decisions` entry says why, citing the row.
- **Reconcile contracts against what shipped (feedback edge).** Compare `facts.story.epic_delivery_log`
  (what predecessors *actually* delivered — the evaluator's record, read-only; one entry per shipped
  story, already resolved across both tiers and staged as one document) against the epic plan's
  `## Story contracts` pinned shapes. On a mismatch the epic plan is stale — **stop and re-route to the
  planner on the epic in revise mode** (don't reshape the story to fit a wrong contract, don't run the
  epic revise inline).
- **Reconcile the phases against the live sub-issue set.** When `facts.slices` is present this story's
  own sub-issues are its deliverable slices, and they are an **input constraint** on the plan's shape,
  not an output of it. Read
  [`../references/sub-issue-reconciliation.md`](../references/sub-issue-reconciliation.md) before
  drafting `## Phases`: it owns the `sub-issue:` cardinality rule, the diff cases prep already computed
  in `facts.slices.diff`, and the mismatch gate (which gates or re-routes — it never silently re-cuts).
  Distinct from the contract edge above: that one looks *up* at the epic, this one looks *down* at the
  slices.
- **Reconcile against what already shipped.** When `facts.plan.present` and a draft PR carries ticked
  phases, read [`../references/revise-reconciliation.md`](../references/revise-reconciliation.md)
  before redrafting `## Phases`: it owns the SOFT/HARD classification, the DoD-annotation
  reconciliation, and the renumbering rule for work landing between a shipped phase and an unshipped
  one (the shipped prefix keeps its numbers), and the S8 confirm that gates the post ("What the confirm
  shows" — its blocks and its card). Keyed on the fact — no prior plan, nothing to reconcile.
  On a polish re-route, the `apply` entries in `facts.revise.open_pr.polish` follow that
  reference's polish rule — a new phase after the shipped ones, or folded in when single-phase.
- **Fold back shipped-phase records.** Whenever `facts.plan.shipped.present`, run
  [`../references/shipped-phase-foldback.md`](../references/shipped-phase-foldback.md) after the
  "Reconcile against what already shipped" step — every such revise, not only a large one.
- **Schema sections.** The standard single-issue schema (a multi-phase story's `## Phases` included —
  its `checkpoint` key too) plus the `**Epic:** #<epic-#> — <epic title>`
  backlink as the **first line after** the marker (never above it) and a `## Epic contract` section —
  `Delivers` (matching the epic's `## Story contracts`) and `Consumes` (each already in the delivery
  log), every line `[epic-plan: #<N>]`-cited (see [`../references/plan-schema.md`](../references/plan-schema.md)).
- **Off-ramp (spine S4).** `off-ramp: slicer offered — epic not offered` — a story carrying two or more
  milestones is planned multi-phase unless the operator chooses to track them as deliverable slices
  (slices are phases on its own branch), but a story that outgrows its epic *contract* is an
  epic-contract problem: re-route to the planner on the parent epic in revise mode (the feedback edge
  above), never promote a story to an epic.
- **Reviewer dimensions (spine S7).** `1, 2, 3, 4, 6, 8`; add `7` for a multi-phase story, passing
  `<<live_slices>>` from `facts.slices` so its slice-coverage check runs — pass the epic plan + delivery-log staged
  paths so Dimension 8 checks the `## Epic contract` against the epic's `## Story contracts` and the
  log. A Dimension-8 BLOCKER tracing to a wrong *epic* contract is the same feedback edge: re-route to
  the epic in revise mode. If this story is itself fully gated by an unresolved OQ, re-route to
  answer-the-question rather than authoring a hollow story plan.

Everything below runs only after the spine returns; on a re-route exit, emit the matching handoff.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md): render the
`## Plan summary` block per its predicate at `focus: none`, then match the outcome:

- **Just-in-time story plan posted**: `Story:` line (`plan: ✓ (<url>)`) + parent `Epic:` line (progress
  count) + `Grounding:` (`read at <facts.plan_ref>@<short-sha>` — the epic branch, this story's own
  branch, or `origin/main` on the bootstrap case; never hardcode one)
  + `**Open questions:**` **whenever any plan posted this session carries `## Open questions`** — for a
  composite epic+story session that means the line renders when *either* the epic plan or the story plan
  carries one; drop it never because the shape matched a single-axis example first (bug (b)). `Next:
  /github-pipeline:resolver #<N>`, `Why:`.
- **Re-route → planner (epic revise)** (contract/Dimension-8 mismatch): `Next:
  /github-pipeline:planner revise #<epic> — <pred> shipped <actual> but the contract pinned <pinned>`.
- **Slice first chosen** (the operator picked "Slice first" at the seam gate): `Story:` line (`plan: ✗`) +
  parent `Epic:` line, no `Grounding:`, nothing posted; `Next: /github-pipeline:slicer #<N>`. It hands
  back — re-run the planner after the cut.
- **Open-question total block**: terminal-style, `plan: ✗`, re-run breadcrumb (drafter first if no
  companion question is filed).
