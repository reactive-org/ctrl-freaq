# Revise a plan

Route for `vector.mode: revise` on a standalone issue or an epic (a story under an open epic revises
through `story-jit.md` instead). A prior `<!-- implementation-plan:v1 -->` comment exists
(`facts.plan.present`); this refreshes it against today's reality and reconciles any DoD ticks the
resolver already projected. One flow **parameterized by the type facts** — `facts.plan_ref` (the open PR
head when `facts.revise.open_pr`, else the base branch), `facts.revise.phase_tracker`, `facts.epic` for an
epic revise — never a type branch. **One exception is a re-derivation, not a branch:** on HARD "Start
fresh" the starting `facts.plan_ref` stops being valid the moment the PR it was selected for closes, and
the row table re-selects it on the fresh run (steps 3–4).

**Run the spine first.** Read [`plan-spine.md`](plan-spine.md) and execute it end to end, focused on
**what changed** (re-walk the thread for newer direction, re-`Grep` the workspace for symbols the plan
names that may have drifted, re-check open questions) — don't re-derive untouched sections. The deltas this route supplies:

- **Re-check open questions.** An OQ the prior plan planned around whose companion `question` is now
  **resolved** (closed, or answered-in-thread via the tiered read in
  [`../../_shared/open-question-links.md`](../../_shared/open-question-links.md)) is no longer deferred —
  fold the now-decided scope into `## Changes` / `## Test plan` and drop its `## Open questions` entry;
  a newly-opened OQ adds a plan-around entry. Never resolve an open OQ yourself.
- **The refreshed body supersedes; it never accumulates.** A revise re-authors at today's truth and
  writes the body whole, so the marker comment carries **no** history layer. Prohibited in the
  `<!-- implementation-plan:v1 -->` body: a retained prior `## Approach` paragraph, a chained footer note,
  a `now void` / `it read "…"` superseded-text block, any "previously" annotation on a live bullet, and
  on an epic a per-story delivered marker outside `## Story contracts` (`plan-schema.md`). Prior
  text lives in the comment's edit history and the thread; the one bounded exception is HARD Start-fresh's
  `## Predecessor`. A prohibition because no single revise looks wrong — each adds a paragraph or two, and
  only the aggregate reaches a body GitHub refuses to write.
- **Promote before you drop** — mechanical, not optional. Read the layer you are deleting for
  decision-shaped lines (a choice made, a constraint accepted, an alternative rejected); confirm each has
  a home in `## Architecture decisions`, `## Changes` or `## Risks & watchpoints`; promote the ones that
  don't, **then** drop. Skip it and dropping is a judgment call whose safe answer is to keep everything.
- **Fold back shipped-phase records** — mechanical, after the promotion check, whenever `facts.plan.shipped.present`:
  run [`../references/shipped-phase-foldback.md`](../references/shipped-phase-foldback.md). Nothing writes a record any more.
- **Epic revise: compress merged stories' contracts** — and everything else a story leaves behind when it
  merges. `plan-schema.md`'s "Retirement" rule owns what compacts and to what; run it per merged story,
  mechanically, after the promotion check. A plan is immutable, so this is the only site it can run at.
- **Persist in place (spine S8).** `gh_persist.py edit-comment <owner/repo> <facts.plan.comment_id>
  "<facts.scratch>/plan-main.md"`, its parts first per the spine — a full replacement authored against the schema, never a delta appended to
  what is there. Ambiguous marker → `comment --delete-marker-id`, the only op that collapses a duplicate.
- **Compute reconciliation** ([`../references/revise-reconciliation.md`](../references/revise-reconciliation.md)).
  Diff old plan vs new plan, classify **SOFT** vs **HARD**, and compute the body-edit diff against `facts.revise.phase_tracker`
  (a no-op when no draft PR exists: no projected ticks). An evaluator-rejection annotation is preserved verbatim —
  never auto-cleared. New work *between* a shipped and an unshipped phase follows that reference's renumbering rule:
  the shipped prefix keeps its numbers, the unshipped tail shifts up — never a `5c`; each phase's
  `checkpoint` follows plan-schema.md's revise rule (the new last phase carries none). `apply` entries in
  `facts.revise.open_pr.polish` (a polish re-route) follow its polish rule: a new phase, or folded in when single-phase.
- **Reconcile the phases against the live sub-issue set.** When `facts.slices` is present the target's
  sub-issues are its deliverable slices — an **input constraint** on the plan's shape, not an output of
  it. Read [`../references/sub-issue-reconciliation.md`](../references/sub-issue-reconciliation.md)
  before redrafting `## Phases`: it owns the `sub-issue:` cardinality rule, the diff cases prep computed
  in `facts.slices.diff`, and the mismatch gate (which gates or re-routes — it never silently re-cuts).
  A closed sub-issue is governed by the shipped-phase rules above, never a second rule set.
- **Reviewer dimensions (spine S7).** The same set the issue's fresh route would pass, keyed on
  `facts.vector.type` (a bug adds 9; multi-phase adds 7 — passing `<<live_slices>>` from `facts.slices`;
  an epic uses 1, 2, 3, 5, 6), plus 10 when the plan carries `## Open questions`.
- **Off-ramp (spine S4).** Keyed on `facts.vector.type`: `offered` for a standalone issue, `not offered`
  for an epic revise. On "Split as epic" the superseded plan comment stays put — the Epic's re-plan supersedes it in place.
- **Show + confirm (spine S8 variant).** Render the classification, the `**What changed:**` block and the DoD
  body edits **before** the card (revise-reconciliation.md, "What the confirm shows"), then gate: SOFT →
  **Apply** / **Cancel**; HARD → **Start fresh** / **Apply in place anyway** / **Cancel**, recommended by the rule that decided HARD ("What the confirm shows"). **SOFT-Apply** runs the spine's persist immediately as written
  (plan comment in place per the persist bullet, then the reconciled DoD body via `edit-body`) — the
  footer stays pinned at the open PR head correctly, because a SOFT revise never closes that PR; the
  next resolver run is a `continue` on the same branch, so grounding there stays valid.
  **HARD-Start-fresh does NOT run the spine's persist here** — it defers posting to its own sequence
  below, because the plan drafted against `facts.plan_ref` (the open-PR-head row, selected before this
  revise even started) is about to be grounded on a branch this decision is closing; posting it as-is
  would leave the footer, and every precedent citation in the body, pointing at a branch nothing can
  read once it's gone.
- **HARD "Start fresh" — close, re-ground, then post (in that order).** Triggered only after the user
  picks **Start fresh** at the gate above — this sequence *executes* that already-gated decision, it
  gates nothing itself:

  1. **Capture what `## Predecessor` and the DoD un-tick need, from the facts already in hand** (the
     closed-PR number and branch from `facts.revise.open_pr`, the old plan's comment id from
     `facts.plan`, the ticked-bullet/phase-tracker state from `facts.revise.phase_tracker`) — before
     step 2 replaces `facts` wholesale, so nothing needed for these two writes depends on a fact only
     the stale envelope carries.
  2. **Close the superseded PR first, with the exact supersession marker text staged.** This is a
     cross-skill contract, not free prose: the resolver's predecessor-PR detection greps the closed
     PR's **close comment** for the literal phrase `Re-plan superseded this PR` (the marker
     `close-pr --comment-file` posts, per `gh pr close --comment`) to find the branch a HARD re-plan
     superseded, for its `-vN` fresh-branch suffixing. Stage that phrase byte-faithful to
     `<facts.scratch>/close-comment.md`:

     ```
     Re-plan superseded this PR. See updated plan at <new-plan-comment-url>. A new branch and PR will
     open at the next `/ctrl-freaq:resolver #<N>` run.
     ```

     then close through the single write path:

     ```bash
     ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py close-pr <owner/repo> <closed-PR#> \
       --comment-file "<facts.scratch>/close-comment.md"
     ```

  3. **Stop — this session cannot re-ground itself.** With the PR closed, the open-PR-head row no longer
     fires and `plan_ref` re-selects deterministically off the row table — `main` for a standalone issue,
     the parent epic's branch for a story, or **this issue's own branch** for either (never hardcode `main`;
     the fresh facts decide). Emit the HARD-revise handoff, remedy matched to the fresh `plan_ref`: the default
     branch → "re-run `/ctrl-freaq:planner <N>` from a current default-branch checkout" (**not**
     workspace-open — no branch should exist before a plan does); the parent epic's branch → "re-run from the
     parent-epic worktree". An issue in its OWN worktree re-grounds **in place** (row `own-branch` /
     `story-own-branch`), so name THIS checkout — send the operator elsewhere only when this branch is behind the
     base tip, where a prep re-run here would (correctly) refuse with `WORKSPACE_MISMATCH`. Carry a `Workspace:` line
     naming that target checkout, plus the captured predecessor facts (step 1) the fresh run needs: the
     closed PR number/branch, the stale plan's comment id, and the DoD bullets to un-tick.
  4. **The fresh planner run** (the operator's next session, in the right checkout) grounds
     normally (spine S3), re-verifies every precedent citation against the new ref, posts via the
     single write path — `edit-comment` on the carried stale comment id, with the footer
     pinned to `<new plan_ref>@<new grounding SHA>` and the `## Predecessor` section (from the
     handoff's captured facts, inserted after `## Approach`) — and un-ticks the DoD bullets to the
     predecessor annotation form via `edit-body`.

  Leave the closed PR's branch in place — the `## Predecessor` reminder is the user's cue to clean it
  up after the new PR lands.

Everything below runs only after the spine returns.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md):

- **Revise — plan refreshed**: same forward shape as the fresh route, with the **new** comment URL in the `Issue:` line (the stale one was deleted). `Next: /ctrl-freaq:resolver #<N>` — the issue, never the PR, whether or not a draft PR is open (the resolver re-enters an open PR in continue mode by itself).
  `Why:` names what changed (the renamed symbol, the resolved OQ) — and, on HARD Start-fresh, that the superseded PR was closed with the supersession note.
  The `## Plan summary` block's `**What changed:**` section carries the **Compute reconciliation** result above — what moved and why, at the operator's altitude: the block the Show + confirm gate already rendered. Carry it; never re-derive it, and never let it become a history layer inside the plan body (the ban at the top of this file governs the body, not this session's output).
- **Epic-shaped, planning aborted** (seam gate chose "Split as epic"; `Next:` the slicer promoting #N per the seam-analysis
  comment) or **Slice first chosen** ("Slice first"; nothing posted): `plan: ✗`, no `Grounding:`, `Next: /ctrl-freaq:slicer`.
