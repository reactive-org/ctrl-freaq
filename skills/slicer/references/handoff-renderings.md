# Slicer handoff renderings

The exact `## Handoff` shapes this skill emits. The schema, the omission rules, the `Slices:` line and
the closed-set state-marker vocabulary are owned by
[`../../_shared/handoff-format.md`](../../_shared/handoff-format.md) — this file only records which
shape each clean exit uses and its exact wording.

**Copy the shape verbatim.** The field names, block structure, and closed-set markers are contract,
not prose to summarize: substitute only numbers, titles, states, and grounding citations. Never
paraphrase a field name, drop a `·` segment, restructure the block, inline the fenced command into
prose, or add a block the shape does not have.

Three lines are free-form text rather than state markers, so they carry no closed-set vocabulary:
`Slices:` and `Stories:` (the children + states, or the progress count) and `Grounding:` (the sources
the cut derived from). Every other marker comes from the shared closed sets.

**Which child line, and which lead field, follows the altitude.** At story altitude the parent is an
`Issue:` (or `Story:`) carrying a `Slices:` line; at epic altitude it is an `Epic:` carrying a
`Stories:` line. Never mix them — a `Slices:` line under an `Epic:` would claim the epic's children are
phase markers on one branch.

The `Grounding:` line is present on **every exit that filed children**, and omitted on every exit that
filed nothing — a cut that hides what it was grounded on defeats the grounding gate. When present it
opens with `read at <ref>@<short-sha>` — `main@<facts.root.sha[:7]>`, the vantage the docs were read at —
because the same doc section can differ between refs, and a citation without its ref is unverifiable.

**On an epic target, every shape below swaps its lead field and adds a child line.** `closed-target`,
`blocked` and the declined-write-gate exit are all reachable at epic altitude, and the shared schema
requires an epic to lead with `Epic:` and carry a `Stories:` line. So: `**Issue:**` → `**Epic:**`, and add
`**Stories:**` — the live set on a resume, or `none yet — cut into stories next` when the epic has no
children. The `Why:` wording is unchanged; only the reason text names stories rather than slices.

## Renderings

**Slices filed (fresh cut).** The forward route. `Grounding:` names the docs the cut derived from;
`plan: ✗` because the cut precedes planning.

```
## Handoff

**Issue:** #103 — Patient: access & set up account · open · story · plan: ✗
**Slices:** #104 103/S1 (open) · #105 103/S2 (open) · #106 103/S3 (open)
**Grounding:** read at main@d4e5f6a · docs/prd.md §4 (account lifecycle, binding) · docs/architecture.md §3 (service layer, binding) · docs/ui-design.md §7 (auth screens, informative)

**Next:** plan #103 — its phases must map onto the three approved slices.

    /ctrl-freaq:planner 103

**Why:** #103 now carries 3 operator-approved deliverable slices as sub-issues. The planner's `sub-issue:` reconciliation maps phases onto the live set, so a plan authored before this cut would mismatch it; the resolver then closes each slice as its last serving phase ships.
```

**Remainder filed (resume).** A re-run over a partially-sliced parent. The `Slices:` line lists the
whole live set — pre-existing and new — because the reader cares about the parent's state, not this
session's delta; the `Why:` names which were added.

```
## Handoff

**Issue:** #103 — Patient: access & set up account · open · story · plan: ✗
**Slices:** #104 103/S1 (closed) · #105 103/S2 (open) · #106 103/S3 (open)
**Grounding:** read at main@d4e5f6a · docs/prd.md §4 (account lifecycle, binding) · docs/architecture.md §3 (service layer, binding)

**Next:** plan #103 — its phases must map onto the full slice set, including the two just added.

    /ctrl-freaq:planner 103

**Why:** #103 already carried #104 (shipped and closed); this run cut the remainder into #105 and #106, numbering from S2. The planner reconciles against all three — a phase set covering only the original slice is now incomplete.
```

**Already fully sliced — nothing to add.** `resume` mode where the operator confirms the existing set
is complete. No writes, so no `Grounding:` line.

```
## Handoff

**Issue:** #103 — Patient: access & set up account · open · story · plan: ✗
**Slices:** 1 of 3 closed · next: #105 103/S2

**Next:** plan #103 against its existing slices.

    /ctrl-freaq:planner 103

**Why:** #103's slice set is already complete and approved — nothing was filed this run. Confirmed with the operator rather than re-cutting, since re-cutting an approved set would duplicate live slices.
```

**Stories filed (epic cut).** Epic altitude: the parent leads with `Epic:` and carries a `Stories:`
line. `plan: ✗` because the cut precedes planning.

```
## Handoff

**Epic:** #150 — Epic: onboarding funnel · open · epic · plan: ✗
**Stories:** #151, #152, #153, #154, #155 (5 filed, dependency-ordered)
**Grounding:** read at main@d4e5f6a · docs/prd.md §4 (account lifecycle, binding) · docs/architecture.md §3 (service layer, binding)

**Next:** plan the Epic — the planner pins the cross-story contracts and sequencing; each story is planned just-in-time as it is built.

    /ctrl-freaq:planner 150

**Why:** #150 now carries 5 operator-approved stories as sub-issues, each independently shippable with its own branch and PR, filed in dependency order so the epic's sub-issue panel reads in build order. The planner posts the epic-level plan (contracts + sequencing) rather than per-story plans; don't run the resolver on a story until its own just-in-time plan is posted.
```

**Stories filed after promotion.** The planner's seam-gate off-ramp: #142 was a standard issue, rewritten
as an Epic at S0's confirmed gate, then cut. The `Why:` names the rewrite, because a reader coming from
the planner's aborted run needs to know the target changed shape.

```
## Handoff

**Epic:** #142 — Epic: patient onboarding · open · epic · plan: ✗
**Stories:** #143, #144, #145 (3 filed, dependency-ordered)
**Grounding:** read at main@d4e5f6a · docs/prd.md §4 (account lifecycle, binding) · docs/architecture.md §3 (service layer, binding)

**Next:** re-run the planner on #142 — now at epic altitude, against the approved story set.

    /ctrl-freaq:planner 142

**Why:** the planner's seam gate found #142 epic-shaped (most seams fell outside its Definition of done, each its own shippable unit), so it aborted and sent it here. #142's body was rewritten as an Epic at a confirmed gate, its title reprefixed `Epic:`, its `feature` label swapped for `epic`, and its seams cut into 3 stories per the seam-analysis comment. The superseded plan pointer was preserved verbatim; the Epic's own plan replaces it.
```

**Remainder filed (epic resume), with adoptions.** A re-run over an epic that already has stories,
including issues adopted rather than created. The `Stories:` line lists the whole live set; the `Why:`
separates what was created from what was adopted, because adoption moved existing issues rather than
adding new scope.

```
## Handoff

**Epic:** #180 — Epic: reporting · open · epic · plan: ✗
**Stories:** #181 (closed), #301 (open), #302 (open), #182 (open)
**Grounding:** read at main@d4e5f6a · docs/prd.md §6 (reporting, binding)

**Next:** plan #180 against its full story set, including the two adopted issues.

    /ctrl-freaq:planner 180

**Why:** #180 already carried #181 (shipped and closed); this run adopted #301 and #302 — already-filed issues, parented via `add-parent` with their bodies untouched — and filed #182 as the finalization bookend. The planner reconciles against all four; a plan covering only #181 is now incomplete.
```

**Declined at the promotion gate.** The operator rejected the Epic rewrite at S0, so the cut never ran.
**Zero GitHub writes happened** — the target is still a standard issue, so it leads with `Issue:` and
carries no child line and no `Grounding:`.

```
## Handoff

**Issue:** #142 — Patient onboarding · open · feature · plan: ✗

**Next:** plan #142 as a single unit, or re-run the slicer to cut it into deliverable slices instead.

    /ctrl-freaq:planner 142

**Why:** the operator declined the Epic body rewrite at the promotion gate, so #142 was neither rewritten nor relabelled and no stories were filed. Promotion is the only path that reshapes the target, so declining it leaves #142 exactly as the planner found it; slicing it at story altitude remains available if its seams are demonstrable rather than shippable.
```

**Declined at the write gate.** The operator rejected the cut. **Zero GitHub writes happened**, so
both `Slices:` and `Grounding:` are omitted.

```
## Handoff

**Issue:** #103 — Patient: access & set up account · open · story · plan: ✗

**Next:** plan #103 as a single unit, or re-run the slicer with different guidance.

    /ctrl-freaq:planner 103

**Why:** the operator declined the proposed cut at the write gate, so nothing was filed and #103 is untouched. The proposed slices are in this session's transcript; a re-run re-derives them from the same sources.
```

**No cut — plan it multi-phase.** Story altitude, `fresh` mode only (an epic cut never ends here): the
milestone pass left **one** milestone, and the operator chose *File nothing* at S4's no-cut card. **Zero
GitHub writes happened**, so `Slices:` and `Grounding:` are omitted; the `Why:` names the milestone, the
increments it folded and the sources that record them, because nothing durable carries the judgment —
the planner's next run re-derives its own seam inventory.

```
## Handoff

**Issue:** #120 — Patient: reset a forgotten password · open · story · plan: ✗

**Next:** plan #120 as one issue — multi-phase, one phase per increment. If the planner's Issue-shape card fires, choose "Plan as one issue".

    /ctrl-freaq:planner 120

**Why:** #120 is one milestone — a patient who forgot their password can get back in — and docs/prd.md §4.3 records it as one outcome. Its four increments (request a reset, receive the emailed link, set a new password, the recorded expired-link behaviour) are what a stakeholder would accept together, so each is a phase of #120's plan and an item of its Definition of done, not a slice; four sub-issues would re-file the phase list. Nothing was filed and #120 is untouched.
```

**Refused — no grounding.** The one refusal that routes outside the pipeline: the remedy is a doc
catalogue, not another stage.

```
## Handoff

**Issue:** #103 — Patient: access & set up account · open · story · plan: ✗

**Next:** declare this repo's grounding documents, then re-run the slicer.

    /ctrl-freaq:setup

**Why:** this repo declares no grounding documents — no `docs/README.md`, or no `<!-- doc-catalogue -->` block in it — and no sources were named at invocation. Nothing was filed: decomposing from the issue body alone invents scope, and invented scope becomes real sub-issues the planner then plans against. Setup derives a catalogue from the docs index for your confirmation.
```

**Refused — blocked.** An open native blocker or an `in-scope (blocked)` open question.

```
## Handoff

**Issue:** #201 — Add payment capture · open · feature · plan: ✗

**Next:** answer the open question blocking #201, then re-run the slicer.

    /ctrl-freaq:question-resolver 61

**Why:** #201 is blocked by open question #61 (native `blocked by`), read from live state rather than the body's recorded line. Nothing was filed: slicing against an unanswered question produces slices the answer may invalidate, and they would be filed as real issues before the decision is made.
```

**Refused — question target.** Terminal: a `question` issue is answered by a human in its thread, so
there is no buildable scope to cut and no stage to hand to.

```
## Handoff

**Issue:** #202 — Which payment provider? · open · question · plan: ✗

**Next:** (terminal — no follow-up skill)

**Why:** #202 is a `question` issue — it records a decision to be made, not scope to build, so there is nothing to decompose. Answer it in its thread (or run the question-resolver to record the decision); nothing was filed.
```

**Refused — the target is itself a slice.** Terminal: the pipeline names no follow-up, because the
right next action is on the parent.

```
## Handoff

**Issue:** #105 — 103/S2 — password reset · open · story · plan: ✗

**Next:** (terminal — no follow-up skill)

**Why:** #105 is itself a deliverable slice of #103 (its parent is not an epic). A slice is never sliced: it has no branch of its own, so a sub-slice could not ship as anything, and a fourth hierarchy level would break the by-construction rule every reader uses to tell slices from stories. Plan or resolve #103 instead; nothing was filed.
```

**Refused — closed target.**

```
## Handoff

**Issue:** #203 — Old feature · closed · feature · plan: ✗

**Next:** (terminal — no follow-up skill)

**Why:** #203 is closed, so nothing would ever ship a phase to close its slices — the parent's rollup would read `0/N` permanently. Reopen it first if the work is genuinely resuming; nothing was filed.
```
