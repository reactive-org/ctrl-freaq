# Handoff renderings — planner

**`Workspace:` lines** (v3, `_shared/handoff-format.md`): a forward-to-resolver shape names
where that session must start — `(none yet — run /ctrl-freaq:workspace-open <N> first)`
when the operator hasn't opened the issue's worktree (the usual plan-before-open case), or the
existing worktree's path when one is already open (e.g. the resume-implementation shape).
Planner-bound and terminal shapes omit the line (a `main`-ref planner run accepts any current checkout
of `main`, or the issue's own branch containing its tip). Name the issue's **own** worktree: take
`facts.grounding.path` only when `grounding.basis` is `own_branch` — on `identical` / `accepted` this
session sat in another issue's checkout, which the resolver's prep would refuse.

Every clean run of the planner ends with a single `## Handoff` block. The schema, omission rules, and
closed-set state-marker vocabulary are owned by [`../../_shared/handoff-format.md`](../../_shared/handoff-format.md);
this file holds the planner's worked rendering shapes, the footer rule, the open-question line, and the
`## Plan summary` block that precedes the handoff. Match the run's outcome to a shape and fill the
snapshot from the data in hand.

These shapes compose along **independent axes** — the structural shape (single-issue / epic / story /
composite epic+story) and whether any posted plan carries open questions. A real run's handoff combines
them (e.g. a story-under-epic shape *with* an `Open questions:` line) rather than picking one worked
example verbatim.

## The footer / `Grounding:` ref rule

The plan-comment footer (`plan-spine.md` S5) and the handoff `Grounding:` line record the **same**
`<plan-ref>@<short-sha>`. Two renderings of one ref:

- `<plan-ref>` renders **`origin/<default-branch>`** for the default branch (`origin/main` in every
  example below — read the repo's own name off `facts.root.default_branch`, never assume `main`), and
  the **bare, un-truncated** branch otherwise — `epic/<N>-<slug>`, the issue's own branch (row
  `story-own-branch`, `own-branch` or `open-pr-head`), the open PR's `headRefName`, or an operator-accepted
  checkout (row `ambient-accepted`) — the `origin/` prefix is dropped for a non-default branch. Render
  `facts.plan_ref`, never `facts.grounding.branch`: on `grounding.basis: identical` the checkout is another
  branch at `plan_ref`'s exact tip, and the footer still names `plan_ref`. Never emit a bare `main@<sha>` — the default branch always carries the
  `origin/` prefix; every other branch never does. This string names the ref the plan was GROUNDED on,
  which is not always the resolver's PR base (a story grounded on its own branch still bases its PR on
  the epic branch), so eliding the branch loses the one fact it carries.
- `<short-sha>` is a 7-character hex prefix of `facts.grounding.sha` — the read
  workspace's own HEAD, so the plan's "planned at `<sha>`" *is* the ref the docs were read at.

`Grounding:` is planner-only and appears only on clean exits that **posted a plan**. It opens `read at
<plan-ref>@<short-sha>`, then the project docs (with §refs) from the plan's `## Doc grounding`, then —
when present — a `· external: <sources>` segment from `## External sources consulted`. Omit it on the
trivial-change and knowledge-gap exits (no plan posted) and when the plan has no `## Doc grounding`.

## The `**Open questions:**` line (renders in every shape that has OQs)

Emit `**Open questions:**` **whenever any plan body posted this session carries an `## Open questions`
section** — treated as an independent, always-checked condition, never one dropped because the structural
shape matched a different example first (the frozen bug-(b) requirement). It sits right after `Grounding:`.
Format (free-form, not a state marker): `**Open questions:** #<N> | (not filed) (audience:…) <treatment>,
… — <trailing summary>`, each companion in the planner's plan-level vocabulary (`planned-around` /
`recorded-blocked` / `provisional-default` — never the drafter's set), `(not filed)` in place of `#<N>`
only when the tracker search (spine S6) found no candidate or an existing one was explicitly rejected.
The trailing summary is the planner's pointer, `see the plan's ## Open questions`.

## The `## Plan summary` block (precedes the handoff)

Render the human-readable plan digest defined in
[`../../_shared/plan-summary.md`](../../_shared/plan-summary.md) immediately **before** the `## Handoff`
block, at `focus`: none — the whole plan, at the altitude it was authored. That file owns the section
set, the sourcing, the conciseness bars, the invitation's three points, and the read-only rule that
governs the discussion after it; do not restate any of it here.

**Predicate: this session posted or refreshed a plan.** Not `plan: ✓` — the two differ, and the
difference is the HARD-revise *Start fresh* exit, whose `plan:` marker still points at the **superseded**
comment while this session deliberately ran no persist (`playbooks/revise.md`). Summarising there would
digest a plan that is about to be re-grounded on a different branch. A SOFT-revise **Cancel** is the same
case.

- **Carries the block:** single-issue plan posted; epic plan posted (stories JIT); just-in-time story
  plan posted; the epic-plus-story composite; epic plan posted with stories not filed; plan posted with
  planned-around OQs; revise — plan refreshed.
- **Carries no block:** trivial change — declined to plan; knowledge gap → researcher; epic-shaped abort
  → slicer; slice first → slicer; OQ blocks the whole plan; HARD-revise *Start fresh*. Nothing was posted
  this session, so there is no digest to render and the `Why:` line is the whole carrier.

**The composite epic+story session emits ONE block, not two** — same rule as the `**Open questions:**`
line, and for the same reason. Render it at the **story plan's** altitude, with the epic's
`## Story contracts` filling `**Seams & integration points:**`: that is precisely what a reader of a
just-in-time story plan needs, and a second epic-altitude block would restate the contracts the first
one already cited.

`**What changed:**` renders on the **revise — plan refreshed** shape only, carrying the reconciliation
`playbooks/revise.md` already computed.

## Renderings

**Single-issue plan posted.** Forward to the resolver.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✓ (https://github.com/owner/repo/issues/142#issuecomment-XXXXX)
**Grounding:** read at origin/main@a1b2c3d · docs/architecture.md §3 (service layer), §7 (export pipeline); docs/constitution.md §6 (logging) · external: RFC 4180 CSV spec (fetched 2026-06-20) · full detail in the plan's ## Doc grounding

**Workspace:** (none yet — run /ctrl-freaq:workspace-open 142 first)

**Next:** implement the plan in a fresh session.

    /ctrl-freaq:resolver #142

**Why:** the plan locks architecture, file-level changes, layer assignments, and test strategy. The resolver executes against it and opens the PR; if implementation reveals a locked decision is wrong, it re-routes back here in revise mode.
```

**Epic plan posted (contracts + sequencing pinned); stories planned just-in-time.** Forward to the
**planner** on the first story in dependency order. The `Grounding:` ref is `origin/main` on the
bootstrap case (before the `epic/<N>-<slug>` branch exists); a later epic *re-plan* grounds at
`epic/<N>-<slug>@<sha>`.

```
## Handoff

**Epic:** #150 — Chat & session UX polish · open · epic · plan: ✓
**Stories:** #151, #152, #153, #154, #155 (5 filed, dependency-ordered, contracts pinned · plans authored just-in-time)
**Grounding:** read at origin/main@b2c3d4e · docs/architecture.md §2 (layer rules), §5 (session model); docs/ui-design.md §4 (chat-size model) · full detail in the plan's ## Doc grounding

**Next:** plan the first story in dependency order, just-in-time, in a fresh session.

    /ctrl-freaq:planner #151

**Why:** the epic plan pins the cross-story contracts and sequencing; each story is planned just-in-time against the epic branch HEAD as it becomes the next to build, so it never grounds on code a predecessor has since moved. #151 is the head of `## Story breakdown`.
```

**Just-in-time story plan posted.** A story under an open epic, planned against current epic HEAD.
Forward to the resolver on that story.

```
## Handoff

**Story:** #151 — Add export service · open · story · plan: ✓ (https://github.com/owner/repo/issues/151#issuecomment-XXXXX)
**Epic:** #150 — Chat & session UX polish · open (0 of 5 stories closed)
**Grounding:** read at epic/150-chat-ux@c3d4e5f · docs/architecture.md §3 (service layer); docs/constitution.md §8 (schema/migrations) · full detail in the plan's ## Doc grounding

**Workspace:** (none yet — run /ctrl-freaq:workspace-open 151 first)

**Next:** implement the story in a fresh session.

    /ctrl-freaq:resolver #151

**Why:** #151's plan was authored just-in-time against `epic/150-chat-ux` HEAD and checked against the epic's `## Story contracts` (Dimension 8). The resolver opens a PR targeting the epic branch; when it merges, the evaluator hands off to plan the next story.
```

**Epic-plus-story composite, one session — the epic had no plan yet, so the planner bootstrapped it
first (bug-(b) worked example).** "Just-in-time story planning" assumes the parent epic already has a
plan; when it doesn't, the planner bootstraps the epic plan inline (grounding stays at `origin/main`,
the bootstrap ref, since no epic branch exists yet), then continues to the story's just-in-time plan.
The emitted handoff is the **story's** — it forwards to the resolver. Both plan bodies carry `## Open
questions`, so the `**Open questions:**` line **must** render: the story plan's own companion question
isn't filed yet, so it reads `(not filed)` in the planner's `provisional-default` vocabulary. This is
the exact shape the 2026-07-01 run dropped — the line renders because "any posted plan carries `##
Open questions`" is an always-checked condition, not because a hybrid worked example happens to exist.

```
## Handoff

**Story:** #161 — Add digest frequency setting · open · story · plan: ✓ (https://github.com/owner/repo/issues/161#issuecomment-XXXXX)
**Epic:** #160 — Notifications: email digests · open (0 of 2 stories closed)
**Grounding:** read at origin/main@f6a7b8c · docs/prd.md §5 (digest frequency) · full detail in the plan's ## Doc grounding
**Open questions:** (not filed) (audience:developer) provisional-default — see the plan's ## Open questions

**Workspace:** (none yet — run /ctrl-freaq:workspace-open 161 first)

**Next:** implement the story in a fresh session.

    /ctrl-freaq:resolver #161

**Why:** epic #160 had no plan when this session started (invoked on #161, its first story). The planner authored the epic plan first — pinning the #161→#162 contract — then continued in this same session to plan #161 against it, checked against `## Story contracts` (Dimension 8); both grounded at `origin/main`, the bootstrap ref. The digest-frequency default (`prd.md §5`) has no filed companion question yet: the plan builds the decided default as a provisional choice and flags the alternative in `## Risks & watchpoints`; it retires once a question is filed and answered.
```

**Epic plan posted, child stories not yet filed.** Forward to the slicer to cut and file them;
`Grounding:` reads at `origin/main` (bootstrap, before the epic branch exists).

```
## Handoff

**Epic:** #150 — Chat & session UX polish · open · epic · plan: ✓ (https://github.com/owner/repo/issues/150#issuecomment-XXXXX)
**Stories:** plain bullets (not yet filed as issues)
**Grounding:** read at origin/main@d4e5f6a · docs/architecture.md §2 (layer rules), §5 (session model) · full detail in the plan's ## Doc grounding

**Next:** cut #150 into stories in a fresh session, then re-run the planner on the Epic.

    /ctrl-freaq:slicer 150

**Why:** the planner doesn't file issues — decomposition is the slicer's, at epic altitude. Once the stories are filed as real sub-issues (each with the `**Epic:** #150` backlink), re-run `/ctrl-freaq:planner #150` to refresh the epic plan, then plan each story just-in-time as you build it.
```

**Plan posted with planned-around open questions.** The issue depended on open questions but had
plannable scope; forward to the resolver (`plan: ✓`) with the `**Open questions:**` line.

```
## Handoff

**Issue:** #142 — Build patient dashboard · open · feature · plan: ✓ (https://github.com/owner/repo/issues/142#issuecomment-XXXXX)
**Grounding:** read at origin/main@a1b2c3d · docs/ui-design.md §7 (portal shell); docs/constitution.md §2 (layering) · full detail in the plan's ## Doc grounding
**Open questions:** #211 (audience:business) planned-around, #212 (audience:clinical) recorded-blocked — see the plan's ## Open questions

**Workspace:** (none yet — run /ctrl-freaq:workspace-open 142 first)

**Next:** implement the decided scope in a fresh session.

    /ctrl-freaq:resolver #142

**Why:** the plan builds the dashboard shell and tiles that are decided; the modality copy (`OQ-08`, #212) is recorded-blocked and the issue is natively blocked-by #212, so the resolver builds around it. Answer #211/#212 in their threads, then the deferred scope is re-filed / unblocked.
```

**Trivial change — planner declined to author a plan.** Forward straight to the resolver. `plan: ✗`, no
`Grounding:`, no `Open questions:`.

```
## Handoff

**Issue:** #142 — Fix typo in onboarding copy · open · bug · plan: ✗

**Workspace:** (none yet — run /ctrl-freaq:workspace-open 142 first)

**Next:** implement the fix in a fresh session.

    /ctrl-freaq:resolver #142

**Why:** this issue is a one-line copy fix — no implementation plan is warranted (the Step-1 scale-to-work judgment). The resolver opens the PR directly.
```

**Knowledge gap — re-route to the researcher.** Grounding hit external truth too broad for an inline
fact-check; route to the researcher rather than posting a guess. `research: ✗ · plan: ✗`.

```
## Handoff

**Issue:** #142 — Migrate to <dependency> v<X> · open · feature · research: ✗ · plan: ✗

**Next:** gather and verify the current behaviour the plan depends on, in a fresh session.

    /ctrl-freaq:researcher #142 — current supported API for <dependency> v<X>; was the pre-v<X> approach deprecated?

**Why:** the plan turns on <dependency> v<X> behaviour that postdates my training cutoff — planning on recall would lock a guess. The researcher posts a cited dossier; re-run `/ctrl-freaq:planner #142` afterward and it ingests the refreshed dossier.
```

**Epic-shaped, planning aborted — re-route to the slicer.** The seam gate's triage chose "Split as
epic" (`references/seam-dispositions.md` off-ramp A): the planner posted the lean seam-analysis comment
and stopped. `plan: ✗`, no `Grounding:` (no plan posted), no `planned` label.

```
## Handoff

**Issue:** #142 — Build patient dashboard · open · feature · plan: ✗

**Next:** promote #142 to an Epic and cut its stories, splitting per the seam-analysis comment.

    /ctrl-freaq:slicer promote #142 to an Epic — split per the seam-analysis comment

**Why:** planning surfaced 4 seams outside #142's Definition of done, each its own shippable unit; a single-issue plan would pin contracts this issue doesn't own. The epic machinery (`## Story contracts` + just-in-time story plans) is built to hold that seam registry. The seam-analysis comment on #142 carries the inventory and suggested story boundaries, and the slicer's prep spills it with the thread; after promotion, re-run `/ctrl-freaq:planner #142` for the epic plan.
```

Both off-ramps name the **slicer** since #16 — it is one operation at two altitudes, and the triage
picks the altitude, not the skill. The two shapes still differ in what gets posted (this one leaves a
seam-analysis comment; off-ramp B posts nothing) and in what happens to the target (this one reshapes it
into an Epic behind the slicer's own rewrite gate).

**Slice first chosen — re-route to the slicer.** The issue carries two or more milestones, each
demonstrable but sharing one branch and PR, and the operator chose "Slice first" over the recommended
**Plan as one issue** (`references/seam-dispositions.md` off-ramp B) to track them on the board as
deliverable slices, not stories. **Nothing is posted** — no plan, no `planned` label, and (unlike the
epic off-ramp) no seam-analysis comment: the slicer re-derives its cut from the repo's declared
grounding docs, so a planner-authored proposal would compete with it. `plan: ✗`, no `Grounding:`. This
is a round trip — the slicer hands back.

```
## Handoff

**Issue:** #103 — Patient: access & set up account · open · story · plan: ✗

**Next:** cut #103 into deliverable slices, then re-run the planner.

    /ctrl-freaq:slicer 103

**Why:** #103 carries two milestones a stakeholder tracks separately — a patient can get into their account (first login, returning login, password reset) and a patient can complete intake (step one, completion) — each demonstrable on its own but neither independently shippable, since they land on one branch behind one PR. The operator chose "Slice first" over planning it as one issue, to track the two milestones on the board with their own acceptance criteria; the five increments stay phases either way. Promoting #103 would buy the integration branch, delivery log, and per-story review round-trips this work doesn't need. After the cut, re-run `/ctrl-freaq:planner 103` — `facts.slices` then carries the approved set and reconciliation maps phases onto it (`references/sub-issue-reconciliation.md`).
```

**Open question blocks the whole plan — re-route to answer it.** Every plannable part is gated by an
unresolved open question the planner must not resolve. Terminal-style: no follow-up skill (a human
answers), with a re-run breadcrumb. `plan: ✗`.

```
## Handoff

**Issue:** #142 — Choose and wire the consult modality · open · feature · plan: ✗

**Next:** (terminal — answer the open question, then re-plan)

**Why:** every part of #142 is gated by `OQ-08` (phone vs video), tracked in question #212 (audience:clinical, audience:business). Planning now would lock a guess. Answer #212 in its thread, then re-run `/ctrl-freaq:planner #142`. (If no companion question existed, this would instead point at `/ctrl-freaq:drafter` to file one first.)
```

**DoD heading — re-route to the drafter.** The DoD-heading card's last arm, or the operator's answer to its announce line (`plan-schema.md`, "The
`closes-dod` target"): the issue's criteria sit under a heading no DoD reader parses and the operator chose
to have the body fixed upstream rather than renamed here. Abort shape — no plan posted, no `planned`
label, `plan: ✗` — pointing at a drafter revise, whose rename makes the criteria the `## Definition of
done`; then re-plan.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✗

**Next:** revise the issue so its criteria are its Definition of done, then re-plan.

    /ctrl-freaq:drafter revise #142

**Why:** #142's twelve criteria sit under `## Acceptance criteria`, which no DoD reader parses — a plan's `closes-dod` would index nothing the resolver can tick or the evaluator can verify. The operator chose a drafter revise over renaming the heading here; it renames the section (the bullets stay as written), then re-run `/ctrl-freaq:planner #142`.
```

**Revise mode — plan refreshed.** Same forward shape; the `Issue:` line carries the plan comment's
**unchanged** URL — a revise updates that comment in place (`gh_persist.py edit-comment`), so the URL a
prior handoff published still resolves. The worked example below is the **SOFT / continue**
shape — `Grounding:` still reads the open PR head (`142-add-csv-export@e5f6a7b`), correctly, since a SOFT
revise never closes that PR. On a HARD "Start fresh" the skill closes the superseded PR via
`gh_persist.py close-pr` with the staged `Re-plan superseded this PR` supersession comment, re-runs prep,
and re-grounds *after* the close (`revise.md`'s HARD sequence) — so `Grounding:` instead reads the
**re-selected** ref (`origin/main@<fresh-sha>` for a standalone issue, the epic branch for a story — or the
issue's own branch when the fresh run sits in its worktree and it already contains that base tip, row
`own-branch` / `story-own-branch`, prep's attention line naming the closed PR), never the closed branch's
pre-close `open-pr-head` selection; `Why:` names the closed PR # and that it carries the supersession note.
When the revise **renumbered the unshipped tail** to insert a phase
(`revise-reconciliation.md`, "Inserting a phase after work has shipped"), `Why:` names the shift so the
operator is not surprised by it — e.g. "phase 6 is new; the former phase 6 (end-to-end proof) is now
phase 7 — the resolver reconciles the PR's `## Phase tracker` on its next continue run." The shift is a
narrative note only: the resolver derives the row diff itself from `facts.tracker`, so nothing carries
it as a fact.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✓ (https://github.com/owner/repo/issues/142#issuecomment-XXXXX)
**Grounding:** read at 142-add-csv-export@e5f6a7b · docs/architecture.md §3 (service layer), §7 (export pipeline); docs/constitution.md §6 (logging) · full detail in the plan's ## Doc grounding

**Workspace:** <the PR's worktree under .worktrees/> — start the next session there

**Next:** resume implementation in a fresh session.

    /ctrl-freaq:resolver #142

**Why:** the plan was refreshed against today's codebase (the `<X>` symbol the previous plan named was renamed to `<Y>` at `<path:line>`). PR #287 is still open on the same branch; the resolver continues from there with the updated locked decisions.
```

The command names the issue (`#142`), never the open PR (`#287`): the resolver re-enters the PR in continue mode from the issue. If no PR exists yet (the resolver hasn't started), the command is the same; the `Why:` drops its open-PR sentence and `Workspace:` follows the fresh route's rule.
