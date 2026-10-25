# Handoff renderings — drafter

Every clean run of the drafter ends with a single `## Handoff` block. The schema, omission rules, and
closed-set state-marker vocabulary live in [`../../_shared/handoff-format.md`](../../_shared/handoff-format.md);
this file holds the drafter's worked rendering shapes. **Pick the one worked example that matches the run's
outcome, copy its shape, and substitute only the issue/Epic/story numbers, titles, and state values** — fill
the snapshot from the data in hand (the `create` result carries the issue/Epic/story numbers and titles;
`plan: ✗` is always correct — the drafter never authors a plan). The field names, block structure, and
closed-set markers below are rendered exactly as shown — they are contract, not a style to imitate:
never rename a field (`**Filed:**` for `**Issue:**` is a defect, not a variant), never drop the
`· <state> ·` segment, never add a block the shape below doesn't have (no invented `Snapshot` section),
never inline the fenced `Next:` command into prose. Next-command skills are namespaced
`/ctrl-freaq:<name>`.

## The `**Open questions:**` line (renders in every build shape that has OQs)

Emit `**Open questions:**` **whenever the filed body carries an `## Open questions` section** — an
independent, always-checked condition, never dropped because the structural shape matched a different
example first. It sits on the line after the `Issue:` / `Epic:` line. Free-form (not a state marker):
list the companion `question` issues (each with its `audience:*` label) and a short disposition tally
covering **every** OQ — include a prose-only in-scope-blocked OQ in the count even though it set no native
block (e.g. `3 scoped out, 1 blocked-by`), per [`../../_shared/handoff-format.md`](../../_shared/handoff-format.md).

## Renderings

**Single issue filed (the common case).** Forward to the planner.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✗

**Next:** plan the implementation in a fresh session.

    /ctrl-freaq:planner #142

**Why:** the planner will research the approach, ground it in docs + codebase precedent, post a verified `<!-- implementation-plan:v1 -->` comment, and lock the decisions the resolver needs.
```

**Single issue filed with open questions (drafted from a source with unresolved OQs).** Same
forward-to-planner shape, plus the `**Open questions:**` line listing the companion `question` issues and
how the OQs were disposed. The `Why:` notes the planner plans only the decided scope and surfaces the OQs.

```
## Handoff

**Issue:** #142 — Build patient dashboard · open · feature · plan: ✗
**Open questions:** #211 (audience:business), #212 (audience:clinical) — 3 scoped out, 1 blocked-by; see the issue's ## Open questions

**Next:** plan the implementation in a fresh session.

    /ctrl-freaq:planner #142

**Why:** three surfaces are scoped out pending #211/#212 and one is natively blocked-by #212; the planner plans the decided dashboard scope and records the open questions in its plan. Answer the companion questions in their threads to unblock the deferred work.
```

Each companion `question` filed in the same run emits its own paste-ready snippet + terminal handoff (the
question rendering below) — they are separate issues, so they appear as their own `#NN`, not folded into
the build issue's handoff beyond the `Open questions:` line.

**Epic filed (no stories yet).** Forward to the **slicer**, not the planner: an epic plan pins cross-story
contracts, so the stories have to exist first. The `Stories:` line still renders — an absent child line is
indistinguishable from one whose stories were dropped.

```
## Handoff

**Epic:** #150 — Chat & session UX polish · open · epic · plan: ✗
**Stories:** none yet — cut into stories next

**Next:** cut #150 into stories in a fresh session, then plan the Epic.

    /ctrl-freaq:slicer 150

**Why:** #150 is filed as an Epic and carries no children yet — the drafter drafts one issue and decomposes nothing (#16). The slicer cuts it into independently shippable stories at epic altitude, filing each as a native sub-issue; only then does `/ctrl-freaq:planner #150` have a story set to pin contracts and sequencing against.
```

**Single issue filed, adopted into the ambient epic.** The spine's ambient-issue gate was answered
**Child of #N** (`facts.ambient.pattern: epic`). The body carries the ordinary `Related to #N` line; the
parent edge does **not** exist yet, because the drafter never writes one — the slicer is its sole writer
(#16), so `Next:` points there instead of at the planner. Use the normal `Issue:` line; do not invent a
`Parent:`/`Epic:` field for a relation that has not been written.

```
## Handoff

**Issue:** #164 — Harden the funnel's consent copy · open · feature · plan: ✗

**Next:** adopt #164 into its epic in a fresh session, then plan it.

    /ctrl-freaq:slicer 95 --adopt 164

**Why:** #164 was drafted from inside `epic/95-public-patient-funnel`'s branch and belongs to that epic, but the drafter writes no parent edge — both hierarchy edges are the slicer's (#16). The slicer's adoption path files it as a native sub-issue of #95 so GitHub's own rollup tracks it; plan it with `/ctrl-freaq:planner #164` once it is parented.
```

**Revise mode (single issue or Epic).** What's next depends on whether a plan already exists
(`facts.revise.plan.present`) and whether the revise materially changed scope, acceptance criteria, or the
contracts the plan was built against (Step R3 already flags this — re-use that judgment):

- **No plan exists yet** (`plan: ✗`) → forward to the planner to author one. Same shape as the first
  rendering above.
- **Plan exists and the revise was material** → the plan is now `stale`; forward to the planner in revise
  mode to refresh it.
- **Plan exists and the revise was cosmetic** (typo fix, link tidy, untouched contracts) → the plan stays
  current. Either the issue already has a PR in flight (terminal — issue, plan, and PR are aligned) or no
  work has started and the user can run the resolver when ready.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: stale

**Next:** refresh the plan in a fresh session — this revision materially changed scope.

    /ctrl-freaq:planner revise #142

**Why:** the revise reshaped the acceptance criteria (added bulk-export and removed PDF). The implementation plan from <date> assumed the previous shape; re-running the planner in revise mode rebuilds the plan against the new body before any code work resumes.
```

An Epic revise revises the epic's own **body** — its goal, background, or Definition of done. It never
changes which stories exist (that is a slicer run at epic altitude), so its `Why:` line describes the body
change, exactly like any other revise.

**Question filed or revised (terminal).** A `question` is answered by a human in the issue thread, not by a
downstream skill, so its handoff is **terminal**: drop the fenced command block (per
[`../../_shared/handoff-format.md`](../../_shared/handoff-format.md) "Terminal endings"), omit the
`research:`/`plan:` markers (they don't apply), and add a question-only `**Audience:**` line listing the
`audience:*` labels (comma-separated for multiple). The paste-ready doc snippet is part of the post-file
output *before* this block — nothing follows the handoff.

```
## Handoff

**Issue:** #210 — PRD-OQ-06b — Which billing model for v1? · open · question
**Audience:** business

**Next:** (terminal — no follow-up skill)

**Why:** open question for the business stakeholder — answered by a person in the issue thread, not by the pipeline. Once it's answered, revise the doc that tracks it (snippet above) and file any resulting work as its own issue.
```

Revise-mode on a `question` is terminal too — same shape, with `state`/title reflecting the edit.
