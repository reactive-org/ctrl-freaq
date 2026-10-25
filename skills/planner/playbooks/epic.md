# Epic plan

Route for a fresh **epic-as-target** run (`vector.type: epic`, `mode: fresh`). Author the high-level
epic plan and **stop** — never fan out every story's full plan up front (per-story plans grounded on one
epic-branch snapshot go stale the moment a predecessor lands, and the resolver re-plans each anyway; the
durable up-front artifact is the epic plan's pinned contracts). `facts.plan_ref` is the discovered
`epic/<N>-<slug>` branch (row `epic-as-target`) or `main` on bootstrap (row `epic-as-target-bootstrap`,
before the branch exists — `facts.epic.branch.match_count == 0`).

**Run the spine first.** Read [`plan-spine.md`](plan-spine.md) and execute it end to end. The deltas
this route supplies:

- **Schema sections.** Replace `## Phases` with the epic sections in
  [`../references/plan-schema.md`](../references/plan-schema.md), inserted after `## Approach`:
  `## Story breakdown` (the `- #<story> "<title>" — <scope>` order, reconciled against
  `facts.epic.stories` — the sibling-sequencing source of truth), `## Story contracts` (per story,
  `delivers` / `consumes` — the cross-story seams Dimension 5 reads and each just-in-time story plan is
  checked against), `## Integration strategy` (how the stories converge on `epic/<N>-<slug>` and reach
  `main`, carrying the concurrency declaration — `plan-schema.md`, "The concurrency declaration" — with
  each shared doc routed to the finalization story by default, as that section rules), and the epic `## Definition of done` grounding. The epic delivery log is
  **not** created here — the evaluator records each story's entry as that story merges.
- **Lock ownership, not shapes (spine S5).** S5 states this bar for every route that drafts or revises
  an epic plan (`revise.md` and `story-jit.md`'s bootstrap included); the reason is here. S5's "pin every
  new symbol's signature, every field's shape … name the rejected one" is the **story** plan's bar — the
  epic reviewer runs no Dimension 4, which is what that detail serves. So pin the cross-story principles,
  which story lands what, and in `## Story contracts` only the shape a consuming story relies on; each
  story's signatures, field shapes, orderings and rejected alternatives wait for its just-in-time plan,
  and a revise never adds one story's mechanism to the epic plan.
- **Altitude.** Every section carries only what no single story owns (`plan-schema.md`, "Altitude", which
  lists what survives per section). Everything file-, control-, suite- or mechanism-specific is the
  just-in-time story plan's, for the same reason this route doesn't fan out story plans at all: it would
  be grounded on an epic snapshot that is stale by the second story.
- **No delivery status.** Nothing here records what has shipped — not on a fresh epic (nothing has) and
  not on a revise (`plan-schema.md`, "Delivery status is not plan content"). The epic delivery log and a
  merged story's `shipped:` clause own that.
- **Reviewer dimensions (spine S7).** `1, 2, 3, 5, 6` — Dimension 5 (sequencing) topologically checks
  the `## Story breakdown` order against the `## Story contracts` graph, no sibling plans needed, and
  checks the concurrency declaration against those contracts and its own file-overlap read.
- **Open questions at the epic grain.** A story *fully* gated by an unresolved OQ is scoped out of
  `## Story breakdown` (a follow-up story once the question is answered); a story merely *touched* by an
  OQ carries the dependency into its own just-in-time plan.
- **Off-ramp (spine S4).** `off-ramp: not offered` — the target is already an Epic; its seam registry
  *is* `## Story contracts`.

Everything below runs only after the spine returns.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md) and render the
`## Plan summary` block per its predicate at `focus: none`. After the epic plan
posts, route on `facts.epic.stories_filed`:

- **Stories filed** (`facts.epic.stories` carries numbers — the epic's sub-issues, or a legacy
  epic's `- [ ] #NN — title` lines; `facts.epic.stories_source` says which): forward to the **planner** on the head of
  `## Story breakdown` — `Epic:` line (`plan: ✓`) + `Stories:` line (the filed, dependency-ordered
  set) + `Grounding:` + `**Open questions:**` when present + `Next: /ctrl-freaq:planner #<first-
  story>`; `Why:` notes each story is planned just-in-time against epic HEAD.
- **Stories not filed** (plain bullets, no `#NN`): forward to the **slicer** to file them —
  `Stories:` line `plain bullets (not yet filed as issues)`; `Next: /ctrl-freaq:slicer <epic-#>`;
  `Why:` the planner doesn't file issues, then re-run the planner on the epic once filed.
