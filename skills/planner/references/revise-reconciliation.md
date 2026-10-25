# Re-plan reconciliation

When a revise runs against an issue with a draft PR that has shipped phases, the body's `## Definition
of done` may already carry projected ticks attributed to phases of the **old** plan (e.g. `- [x] <text>
(closed by phase 2, commit abc1234)`). Annotation shapes and the parser are in
[`../../_shared/dod-annotations.md`](../../_shared/dod-annotations.md). The planner reconciles those
projections against the new plan; the user is engaged at the spine S8 confirm gate to see what's about
to change — what that gate renders is "What the confirm shows". The PR's `## Phase tracker` (which phases shipped) rides in `facts.revise.phase_tracker` —
never re-fetched.

### SOFT vs HARD classification

LLM judgment, bounded by structural rules:

**Always HARD:**

- A code-shipping phase ticked in the PR's `## Phase tracker` (shipped) has its `ships`, `deliverable`,
  or `kind` field changed in the new plan.
- The old plan's `## Architecture decisions` has a decision reversed in the new plan **and** that
  decision is reflected in a shipped phase's code.
- A code-shipping phase ticked in the tracker is removed entirely from the new plan.
- A top-level `## Definition of done` bullet's text was edited between the two plan runs **and** that
  bullet was ticked under the old plan.

**Always SOFT:**

- Only `closes-dod` indexes changed (no other structural change).
- Only un-shipped phases changed (forward-looking only).
- Only doc-grounding text was tweaked.
- New phases added beyond what's shipped.
- A **sub-issue closed** since the plan was posted (`facts.slices.diff.closed`). A closed sub-issue
  behaves like a **shipped phase**: the phase serving it is judged by the shipped-phase rules above —
  its `ships`/`deliverable`/`kind` changing while its tracker row is ticked is still HARD — and there
  is no second, parallel rule set for sub-issue closure. Closure alone is never HARD.

**Judgment call (reason + surface uncertainty):** `## Changes` block text edits (does the shipped diff
still match the new wording?); DoD bullet wording adjustments without structural change. **Lean HARD
when ambiguous** — surprising a visible-progress regression on a SOFT misclassification is worse than
offering "Start fresh" on a borderline-SOFT case the user can decline.

**Fold-back is not an edit.** An entry that `plan_shipped.py check` confirms restored verbatim from a
legacy shipped-phase record (no `not_restored` — [`shipped-phase-foldback.md`](shipped-phase-foldback.md))
left `## Changes` / `## Test plan` unchanged in substance: it only changed which comment carries it. It
never counts toward the **Judgment call** rule's `## Changes` block text edits. Judge only the entries
whose text changed — a folded-back entry reworded is a shipped phase's entry changed, judged like any other.

### A polish re-route: the ledger's `apply` items become a phase

When the evaluator — or the resolver's finalisation (`../../resolver/references/finalisation.md`), before
the PR ever reached the evaluator — sends a PR here because a confirmed polish item would reverse a locked
plan decision, or because the operator answered **Re-plan** on it (`../../_shared/polish-ledger.md`; its
handoff `Why:` quotes the item and the decision, or the decision the plan leaves open), the work is in the
open PR's ledger: every entry in `facts.revise.open_pr.polish` marked `apply`. Revise the decision the item
reverses — or, for an entry whose note leads `operator: re-plan`, settle the decision the note names,
asking the operator when it names two intents — and quote the ledger id in the new or changed
`## Architecture decisions` / `## UI decisions` bullet so the change is traceable. Then place the work by
the PR's shape:

- **Multi-phase** (`facts.revise.phase_tracker` carries rows) — add **one new phase after the shipped
  ones** whose `ships` / `deliverable` cover those entries by id. Every code-shipping phase has shipped by the
  time either sends it here (a trailing operator phase may still be pending — the new phase still takes
  the next number), so it takes k+1 with no renumbering — **SOFT** ("New phases added beyond what's
  shipped"). The resolver builds it as the tracker's `missing` row in continue mode.
- **Single-phase** (`facts.revise.phase_tracker` is empty — a single-phase PR carries no tracker) — add
  **no** phase: fold the entries into the plan's one phase (its `## Changes`). A new phase here would
  turn the plan multi-phase with no tracker row recording that phase 1 shipped, so the resolver would
  rebuild it. A single-phase PR that exists is already a revision run (`facts.revision`), which applies
  the `apply` entries as Addressable. A vetoed DoD bullet re-planned alongside folds the same way, with
  the `re-plan reassigned to phase 1` mark (the "Evaluator-rejected bullet" rule).

Either way the resolver marks each entry `applied (commit <sha>)`. Never edit the
ledger yourself — it belongs to the resolver and the evaluator.

### Inserting a phase after work has shipped

New work that must run *between* a shipped phase and an unshipped one is the case that produces
`Phase 5c`. It has exactly one legal shape, forced by the grammar
([`plan-schema.md`](plan-schema.md), "Phase numbering"): numbers are integers, sequential and
non-duplicate, so a phase's number **is** its position. A letter suffix (`5c`), a decimal (`6.1`) and
an appended-but-out-of-order integer are all unrepresentable — and `depends-on` cannot rescue them,
because it is backward-only (a phase depends on *earlier* numbers), so "phase 7, but run it before 6"
has no spelling either.

**The rule: a shipped phase keeps its number; the unshipped tail renumbers.** Phases 1..k are the ones
ticked in `facts.revise.phase_tracker`. The new phase takes k+1; every previously-unshipped phase
shifts up by one in its existing relative order; every `depends-on` and `closes-dod` reference to a
shifted phase is rewritten to its new number. Nothing that has shipped moves.

That is what keeps the insert **SOFT** under the lists above ("New phases added beyond what's
shipped", "Only un-shipped phases changed"): no ticked phase's number changed, so no ticked bullet's
attribution changed. Renumbering the shipped prefix instead — to put the new phase at the front — is
the move that would make the same edit HARD, and it is never necessary: the insert point is always at
or after k+1, since work that has already shipped cannot be preceded by work that has not.

The PR's `## Phase tracker` is a **separate artifact keyed by the same numbers**, so this operation
changes its row set. The resolver reconciles it against the new `## Phases` before it selects its next
phase (`skills/resolver/playbooks/resolve-spine.md` S4, from `facts.tracker.diff`); name the shift in
the handoff `Why:` so the operator sees it coming.

### SOFT-path body reconciliation

Walk the captured body annotations against the new plan's `closes-dod` mappings together:

- **Unchanged attribution** (annotation says phase X, new plan's phase X still claims this bullet) → no
  edit.
- **Reassignment, new phase hasn't shipped** (new plan's phase Y claims it; Y not ticked) → un-tick to
  `- [ ] <text> (resolver claimed phase X, commit <sha>; evaluator rejected: re-plan reassigned to phase
  Y, awaiting its ship)`. Reusing the evaluator-rejection shape is intentional — the resolver's
  projection respects it as a sticky veto until Y ships.
- **Reassignment, new phase has shipped** (Y ticked) → re-attribute, leave ticked: `- [x] <text> (closed
  by phase Y, commit <Y-sha>)`.
- **Phase removed** (the work phase X shipped is claimed by no phase of the new plan) → un-tick to
  `- [ ] <text> (resolver claimed phase X, commit <sha>; evaluator rejected: re-plan removed phase X
  — needs re-verification)`. **Removal is what costs the tick, not renumbering.** An insert leaves
  every shipped phase's number alone (see "Inserting a phase after work has shipped" above), so a
  *shipped* phase whose number changed at all means either a HARD path the user chose to apply anyway
  or a plan predating that rule — and there the phase still exists, so route it through the
  reassignment cases above (ticked at its new number → re-attribute, leave ticked) and un-tick
  nothing. Un-ticking a bullet whose code is still on the branch, still claimed and still verified is
  the visible-progress regression the SOFT list exists to prevent.
- **Orphaned bullet** (no phase claims this index) → un-tick with the same orphan annotation; surface as
  a Dimension-7 violation in the new plan's verify loop (a re-plan bug the new plan should have caught).
- **Evaluator-rejected bullet** (`- [ ] … evaluator rejected: …`) → **preserve it.** Surface it at
  the S8 confirm so the user sees the rejection alongside the diff. **Do not auto-clear it** even when
  the new plan reassigns the bullet — the rejection is evidence the prior code failed the bullet. When
  the new plan **does** assign the veto's fix to a phase — a new phase `Y`, or on a single-phase plan
  its one phase (`Y` = `1`) — append the re-plan mark inside the same annotation, keeping the
  evaluator's reason verbatim: `- [ ] <text> (resolver claimed phase X, commit <sha>; evaluator rejected:
  <reason>; re-plan reassigned to phase Y, awaiting its ship)`. Still un-ticked, still a veto; the mark is
  the resolver's only signal that the veto was re-planned (`facts.dod_vetoes`), and without it every
  resolver run re-routes back here — a single-phase issue looped planner → resolver → planner forever. A
  plan that does not re-plan the veto leaves the annotation untouched.

Stage the reconciled body to `<facts.scratch>/issue-body-reconciled.md` — built from the body this session
last wrote (`facts.dod.body_path` unless the S1 DoD-heading rename already wrote it; `plan-schema.md`, "The
`closes-dod` target"), never `facts.sections`' session-start copy — and, after the user confirms at S8, apply
it via the single write path:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-body <owner/repo> <issue> \
  "<facts.scratch>/issue-body-reconciled.md"
```

### What the confirm shows

The operator is asked to replace a posted, reviewer-verified plan, so the gate has to say what the
replacement changes. Asking "classified SOFT — post it in place?" after one prose sentence is the failure
this section exists to stop: the change list is what the operator is approving, and rendering it only in
the post-handoff `## Plan summary` shows it after the write it was needed for. Every route that reconciles
a prior plan gates here before its post — `revise.md`, and `story-jit.md` when it reconciles shipped
phases. Render these three blocks, in this order, in the message that carries the card — SOFT and HARD
alike:

1. **Classification** — SOFT or HARD, and the rule that decided it: the matching "Always SOFT" / "Always
   HARD" item from the classification lists, or, for a judgment call, which way it leaned and the
   uncertainty. When the call turns on a shipped phase's reworded `## Changes` or `## Test plan` entry,
   quote that entry old → new under it: the operator is judging whether the shipped diff still matches
   the new wording, and the `**What changed:**` digest is too high-altitude to judge that from.
2. **`**What changed:**`** — the block exactly as
   [`../../_shared/plan-summary.md`](../../_shared/plan-summary.md) defines it (shape, coverage and cap),
   comparing the posted plan (staged at `facts.plan.body_path`) with the final verified `plan.md`, so
   edits the review passes made are in it. This is the block the handoff's `## Plan summary` carries
   later — compute it once, here.
3. **DoD body edits** — one line per bullet whose tick or annotation would change (before → after), each
   preserved evaluator rejection quoted with its reason. SOFT lists the edits "SOFT-path body
   reconciliation" stages. HARD lists them per option, because the two write different things: **Apply in
   place anyway** writes the SOFT-path edits, **Start fresh** the predecessor-form un-ticks of "HARD-path:
   Start fresh" step 4. With nothing to change, say so in one line (`No DoD ticks to reconcile.`) rather
   than omitting the block: an epic revise has no tracker and no ticks, and a missing block reads as one
   left out.

Then the card: SOFT → **Apply** / **Cancel**; HARD → **Start fresh** / **Apply in place anyway** / **Cancel**.
Its question names the classification and refers to the blocks it follows ("SOFT — post the refreshed plan in
place, with these changes?"); it never restates them. The three blocks are the card's brief
([`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md)): add only what it lacks — the end-user
line, clear or close call, the *more* hint. The HARD recommendation follows the rule that decided it, never the
card: an "Always HARD" rule makes **Start fresh** the clear call, because shipped work no longer matches the plan
and only a fresh PR keeps the progress record true. A HARD from the judgment call's lean is a **close call** —
say so: **Apply in place anyway** is the pick when the quoted shipped entries still describe what shipped.

### HARD-path: Start fresh — close, re-ground, then post

When the user picks **Start fresh** at S8's three-way confirm, this order is load-bearing:
the plan drafted so far was grounded at `facts.plan_ref`, the open-PR-head row selected *before* this
revise started — the very PR this decision is about to close. Posting that draft as-is (footer,
precedent citations, everything) would cite a branch nothing can read the moment it closes. So the close
happens **first**, `plan_ref` is **re-selected** off the row table against a fresh prep call, and only
**then** is the plan finalized against the new ground truth and posted — never the reverse (see
[`../playbooks/revise.md`](../playbooks/revise.md)'s HARD sequence for the full narrative):

1. **Capture the closed-PR number/branch (`facts.revise.open_pr`), the old plan's comment id
   (`facts.plan`), and the ticked-bullet/phase-tracker state (`facts.revise.phase_tracker`)** — from
   the facts already in hand, before step 3 replaces `facts` with a fresh envelope.

2. **Close the superseded PR, with the byte-faithful supersession marker staged first.** This is
   required so the resolver's fresh-PR path fires on the next run: it detects the closed predecessor PR
   + branch by greping the closed PR's **close comment** for the literal phrase `Re-plan superseded this
   PR` (`docs/specs/resolver.md` "Deterministic steps": "Predecessor-PR detection … filtered to `Re-plan
   superseded this PR` bodies" — the marker `close-pr --comment-file` posts, per `gh pr close
   --comment`) and computes the next `-vN` branch suffix from it — so the phrase is a cross-skill
   contract, not free prose; stage it verbatim. Stage the close comment to
   `<facts.scratch>/close-comment.md`:

   ```
   Re-plan superseded this PR. See updated plan at <new-plan-comment-url>. A new branch and PR will open
   at the next `/ctrl-freaq:resolver #<N>` run.
   ```

   then close through the single write path:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py close-pr <owner/repo> <PR#> \
     --comment-file "<facts.scratch>/close-comment.md"
   ```

3. **Stop and hand off** (`revise.md` step 3's rule verbatim): with the PR closed, `plan_ref`
   re-selects deterministically off the row table (`main` for a standalone issue, the parent
   epic's branch for a story, or the issue's own branch for either — never hardcoded). An issue whose
   own worktree this is re-grounds in place (row `own-branch` / `story-own-branch`) unless it is behind
   the base tip, where a prep re-run here would refuse with `WORKSPACE_MISMATCH`. The
   handoff names the target checkout on its `Workspace:` line and carries the captured
   predecessor facts.

4. **The fresh planner run re-grounds at its asserted `facts.grounding` checkout, finalizes the
   plan body against that ref, then posts.** Re-verify every precedent citation still resolves
   there. Insert a
   `## Predecessor` section immediately after `## Approach` (built from step 1's captured facts):

   ```
   ## Predecessor

   This plan supersedes a prior plan that drove PR #<closed-PR> (closed <YYYY-MM-DD>) after a HARD
   re-plan. The closed PR's branch (`<branch-name>`) is preserved for audit and should be deleted by the
   user after the new PR lands. The brief reason for starting fresh: <one-line rationale>.
   ```

   Post the plan (footer pinned to `<new plan_ref>@<new grounding SHA>`) through the single write path,
   then un-tick the issue body's DoD bullets that were ticked under the old plan (from step 1's captured
   tracker state), each to the predecessor annotation `- [ ] <text> (previously claimed by phase X,
   commit <sha> on closed PR #<M>)` (per `../../_shared/dod-annotations.md`; an evaluator-rejection
   annotation is rewritten to this predecessor form — the closed PR makes the rejection no longer
   load-bearing). Stage to `<facts.scratch>/issue-body-reconciled.md`, from the body this session last
   wrote (same rule as the SOFT path), and apply via `gh_persist.py edit-body`.

Leave the closed PR's branch in place — the `## Predecessor` reminder is the user's cue to clean it up
after the new PR lands.

### Worked examples

*SOFT — closes-dod reshuffle:* old Phase 1 `closes-dod: 1, 3`; new Phase 1 `closes-dod: 1` and a new
Phase 4 `closes-dod: 3`. Phase 1 shipped at `abc1234`; Phase 4 hasn't. Body bullet 3 reads `- [x]
Document the export format (closed by phase 1, commit abc1234)`. SOFT reconciliation un-ticks it to
`- [ ] Document the export format (resolver claimed phase 1, commit abc1234; evaluator rejected: re-plan
reassigned to phase 4, awaiting its ship)`. The footer stays pinned at the open PR head throughout — a
SOFT revise never closes that PR, so nothing forces a re-ground.

*HARD — shipped phase's `ships` changed:* old Phase 1 `ships: PR commits implementing a service-layer
abstraction`; new Phase 1 `ships: PR commits implementing protocol-based dependency injection`. Phase 1
shipped at `abc1234`; the shipped diff doesn't match the new `ships`. Classify HARD, recommend Start
fresh: close PR #287 via `close-pr` with the staged `Re-plan superseded this PR. See updated plan at …`
comment, re-run `prep_planner.py` (the open-PR-head row is gone now that #287 is closed, so `plan_ref`
re-selects `main`), re-ground there, then post the new plan — footer `origin/main@<fresh-sha>`, not
`142-add-csv-export@<stale-sha>` — with `## Predecessor` naming PR #287 + branch `142-add-csv-export`,
and un-tick the three ticked DoD bullets to `- [ ] <text> (previously claimed by phase 1, commit
abc1234 on closed PR #287)`. The next resolver run detects the closed predecessor and opens a fresh PR
on branch `142-add-csv-export-v2`, based on `main` — matching the plan it just read.
