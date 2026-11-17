# Decomposition — method, grounding contract, and the child templates

How to decompose one filed issue into the ordered children that become its native sub-issues. Read
this whole file before cutting; it is the methodology `playbooks/cut.md` delegates to.

What a deliverable slice **is**, how it differs from a story, and the closing contract are defined
once in [`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md). Cite that;
this file is about *how to cut*.

## 1. The independence bar is a parameter

One operation, one knob. Decomposition asks the same question at every altitude — *what is the
smallest unit of visible behaviour that stands on its own and earns its own issue?* — and only the
meaning of "stands on its own" changes:

| Cutting | Children | The bar each child must clear |
|---|---|---|
| a story or standalone issue into slices | slices | independently **demonstrable** — someone can show the new behaviour |
| an epic into stories | stories | independently **shippable** — the child gets its own branch and PR |

The bar is a floor every child must clear, never the reason to cut. A child must also earn its issue
over the cheaper alternative the next stage already offers: at epic altitude, the fixed per-story tax
(§5.1); at story altitude, a phase in the parent's own multi-phase plan (§4.1). Clearing the bar
without earning the issue makes a phase, not a child.

`facts.vector.altitude` names which (`story` / `epic`), and everything below is written against *the
bar*. Where a rule genuinely differs by altitude it says so and says why; §4.1 is story-only, §5 is
epic-only, and §8's child body differs because the two children carry different contracts. Nothing
else forks.

## 2. Think vertical, not horizontal

A child cuts through whatever layers the behaviour needs — interface, application logic, domain
rules, persistence, authorization, validation, tests, observability — rather than delivering one
layer across all behaviours. Each child answers:

> "What can the actor now do that they could not do before?"

These are **never** children at either altitude, because nothing clears either bar when they finish:

- "Create the database tables" / "write the migrations" / "add the indexes"
- "Build the API endpoints"
- "Implement the UI"
- "Set up the background job"

They are implementation *tasks*, and a task belongs **inside** a child. At story altitude that means
inside a slice and never as a sub-issue of one, because a slice is the last level of the hierarchy. At
epic altitude a story legitimately gets sub-issues later — its own deliverable slices, cut by a
separate slicer run on that story — but never at filing time from here.

## 3. Grounding inputs — read before decomposing

Children are **composed from what the sources already record, never invented**. A decomposition drawn
from a two-line issue body will hallucinate scope, and invented scope becomes real issues that
downstream stages then plan against.

| Input | Where | Standing |
|---|---|---|
| The repo's declared docs | `facts.grounding_docs` — the `<!-- doc-catalogue -->` entries | **Required.** A `binding` entry is a constraint, not a trade-off; an `informative` one is context. |
| The issue body + thread | `facts.sections` (spilled paths) | **Required.** The thread may have moved past the body — the latest accepted direction wins. |
| A planner seam-analysis comment | the thread, when the planner's off-ramp sent you here | Optional. It pre-seeds candidates and carries the shippable-independence evidence; a proposal to test, never authority. |
| Research dossier | `facts.research.present`, staged in `facts.sections` | Optional — current external truth the cut may cite. Input, never authority. |
| Sources the operator names at invocation | the invocation itself | Optional, and the only substitute when no catalogue exists. |

**No catalogue and no operator-named sources → refuse.** That is the one gate with no proceed-anyway
rung ([`../../_shared/doc-catalogue.md`](../../_shared/doc-catalogue.md) explains why a proceeding
reader and a refusing reader are both correct responses to the same absent fact — this stage is the
refusing kind, because its whole output is derived from documents).

**Citation duty.** Every child's grounding section names what it derives from — the doc (with its
section's slug ID, else its §heading, or a register id) that records the behaviour, the body/thread passage, the dossier source. A
child that can cite **nothing** does not exist: either the behaviour is real and the source has a gap
(surface it as a finding), or the child is invented scope (drop it). A `provisional-default` open
question travels with every child it touches, named in that child's grounding.

## 4. Method

Write a two-or-three-sentence **decomposition strategy** first: which seam carries the most risk,
what the walking skeleton is, what is deferred. Then cut:

1. **First child = the thinnest walking skeleton.** The narrowest end-to-end path that crosses every
   architectural seam the parent touches. It proves the seams while changing course is still cheap,
   and gives stakeholders something real to react to. At story altitude the skeleton is the first
   slice's *first phase*, not a slice of its own (§4.1).
2. **Each subsequent child adds exactly one observable increment** — a new path, a new variant, a
   recorded failure behaviour — never "finish the backend". At story altitude read "increment" as
   "milestone": the increments inside a milestone are its phases (§4.1).
3. **Every child clears the bar on its own.** If you cannot say what it lets someone do — demonstrate
   at story altitude, ship at epic altitude — in one sentence, the cut is wrong.
4. **Prefer few, thick-enough slices over many thin ones** (and the same bias at epic altitude: few,
   thick-enough stories). Children sequence risk and feedback; they do not shard work. There is no
   target count: the count is the number of distinct outcomes the sources record, and at story
   altitude zero is a legitimate answer (§4.1); **ten or more is an anti-pattern** (§10). Two
   independent reasons: a thin child's one-sentence test stops being interesting, and a rollup of
   fifteen children is noise rather than a progress signal — and the rollup is why these are issues
   at all.

### 4.1 What earns a slice (story altitude only)

**The alternative is a phase.** Every increment of a story becomes a phase in the parent's
multi-phase plan whether or not anyone slices it — a slice is a phase marker on the parent's branch
([`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md)). Sequencing the
increments is the plan's job. A slice adds exactly two things a phase does not: a **milestone on the
board** (the parent's rollup — "the rollup is the point" in that contract) and a **home for its own
acceptance criteria**. So a slice is earned by a milestone, not by an increment:

> "Would a stakeholder watching the board care that this closed, apart from its neighbours?"

If not, it is a phase — and its behaviour is acceptance criteria — inside the slice whose milestone
it builds toward.

**The milestone pass.** Apply it yourself before proposing anything. Merge candidates into one slice,
carried as that slice's criteria and served by several phases, when any of these fire:

1. **One outcome to a stakeholder** — they would accept the candidates together: a first-use path and
   its returning path, a happy path and its recorded failure behaviour, a variant of another
   candidate's behaviour.
2. **A skeleton nobody signs off** — the walking skeleton alone is not a milestone; it is the first
   phase of the first slice.
3. **Sequential with no stakeholder-visible value alone** — one only feeds the next.

**Guardrail.** Keep candidates apart when each is a distinct outcome a stakeholder tracks on its own:
"patients can log in" and "patients can complete intake" are two milestones even though both are
small, and folding them together hides real progress from the board.

**No cut is a legitimate outcome.** If the pass leaves **one** milestone, that milestone is the
parent's own Definition of done, and a slice would only restate it (§10, "the issue restated"). File
nothing: the parent's multi-phase plan carries the increments and its DoD carries the criteria.
`playbooks/cut.md` S2/S4 own that exit. It applies to a `fresh` cut only; a `resume` run keeps its
existing set and its "already fully sliced" exit.

## 5. Cutting an epic into stories (epic altitude only)

Everything above still applies; a *shippable* bar adds four things a *demonstrable* one does not.

### 5.1 The coalescing pass

Apply it yourself before proposing anything. "Independently shippable" is a **ceiling, not a target**
— every story pays a fixed tax (worktree + per-worktree resources, baseline, cold build/boot,
targeted test run, review round-trip) before its own work counts, so aim for the **coarsest** cut that
still keeps each story independently shippable. **Merge** a pair or cluster when any of these fire:

1. **Shared verification surface** — they would re-run the same build / integration target / snapshot
   set. Splitting pays that expensive verification twice for one logical change.
2. **Sequential with no standalone value** — one only feeds the next and delivers nothing a reviewer
   could sign off alone (a wiring change meaningless until its view lands; deleting a legacy component
   once its sole consumer is rewired).
3. **Same files or layer, individually thin** — several small edits a reviewer reads as one change.

**Guardrail — don't over-coalesce.** Keep them separate when each has independent value, a clean
contract, *and* a cheaper isolated test surface (the clearest case: pure functions or models with fast
unit tests and no build/UI/snapshot cost). Thin is not the same as mergeable: a small increment
introducing a real contract worth reviewing on its own — a schema field, a new public type with its
own suite — earns its own story.

### 5.2 Bookend stories (default slots, planner-filled)

After coalescing, the candidate list carries two bookends by default:

- **Technical-foundation story, first** — the slot for shared groundwork that two or more later
  stories will consume (contracts, schema, scaffolding, build plumbing). Keep its scope at the **slot
  level**; never enumerate the seams — identifying and pinning them is the planner's job (its seam
  dispositions and the epic plan's `## Story contracts`).
- **Finalization story, last** — the slot for the end-of-epic sweep: cleanup of what the epic
  accumulated, updating the project docs to reflect what actually shipped, epic-level DoD
  verification. Never itemize the sweep — its just-in-time plan grounds on the epic delivery log,
  which exists only after the other stories land.

The slots exist at filing time because the planner never files issues and needs filed issues to plan
into; their first/last positions bracket the filed story order.

Omission is allowed but **never silent**. Record the omitted bookend and a one-line reason as a note
in the epic body's `## Background` (e.g. `_No foundation story: the shared groundwork is story #1's
entire scope._`) so it survives every later resume; the reviewer may challenge the justification.

Bookend bodies use **explicit deferral placeholders**, never invented specifics — e.g. a DoD entry
"Deliver the shared groundwork pinned by the epic plan's `## Story contracts` (specified at planning
time)" or "Cleanup + doc-reality sweep per the just-in-time plan (grounded on the epic delivery log)".
Deferral is the sanctioned placeholder form here; a fabricated seam list or cleanup list in a bookend
body is the defect (anti-fabrication), not the placeholder. A bookend's deferral body is planner-owned
by design: its thinness is not evidence for merge signals 2 or 3 and draws no merge recommendation on
that basis.

### 5.3 Adopting issues that already exist

An epic is often drawn around stories authored upstream. An adoption candidate
(`facts.adoption_candidates`) qualifies when it is shippable-shaped on its own and its scope belongs
to this epic's theme. Three things to check before proposing one:

- **Already parented** — adopting it *moves* it out of its current parent. Surface that and let the
  operator decide; never silently re-parent another epic's child.
- **Closed** — legitimate for already-shipped scope an epic collects, but it contributes no open
  progress to the rollup. Say so.
- **Itself an epic** — never a child. The hierarchy has exactly three levels.

An adopted issue's body is **not** rewritten to match the §8 templates: it was authored elsewhere
and adopting it is a relation write, not an edit. If its scope genuinely overlaps a story you were
about to propose, adopt it *instead of* filing that story rather than doing both.

### 5.4 Cross-story contracts are the planner's

Name the seams two or more stories share so the ordering is defensible, and stop there. Pinning the
contracts across them belongs to the epic plan (`## Story contracts`), which is authored after these
issues exist.

## 6. Failure paths

Operational behaviour earns its own child — or explicit scenarios inside a child's criteria — **when
and only when a source records it**: concurrency and double submission, expiry, retries and
idempotency, an unavailable dependency, validation failures, recovery after interruption,
authorization failures. Recorded failure modes are requirements with provenance, not optional polish,
and deferring them all to the end defers the riskiest work to last.

At story altitude prefer the scenarios: a recorded failure behaviour is usually part of the milestone
its happy path delivers (§4.1, merge signal 1), so it lands as that slice's criteria and a phase of
its own. It earns a separate slice only when a source records it as an outcome stakeholders track
separately.

Never invent a failure mode the sources don't record. That is new scope, and new scope is elicited
from a human, not minted here.

## 7. Ordering

Present children in recommended delivery order and state dependencies between them explicitly. The
sequence should retire risk while delivering value early: walking skeleton, then the riskiest recorded
seam, then breadth.

**The order the operator approves is the order the children are created in** — `addSubIssue` appends,
so creation order *is* the display order under the parent. Get it right at approval time; there is no
reordering afterwards. What that order then drives differs by altitude: at story altitude it is the
order the resolver's phases will follow on the parent's one branch; at epic altitude it is the order
the stories are built and merged in, each on its own branch, so a story must not depend on one filed
after it.

## 8. The child body

### 8.1 Story altitude — the slice template

The template **is** the sub-issue body. Title: `<parent#>/S<K> — <behaviour phrase>` — a behaviour,
never a layer, because the title is read in the parent's rollup and on a board's progress hover,
out of context and with no body.

```markdown
**Parent:** #<N> — <parent title>

## Outcome

<One or two sentences: the behaviour that exists once this ships, actor- or business-visible.>

## Why a separate slice

<Why this is a useful delivery milestone on its own.>

## Acceptance criteria

- [ ] **AC-1** — <criterion summary>
  - Given <precondition>
  - When <action>
  - Then <observable outcome>
- [ ] **AC-2** — <criterion summary>
  - Given / When / Then

## Out of scope

<What is deliberately deferred, and to which sibling slice (`<N>/S<K>`) or back to the parent.>

## Grounding

<The sources read and what each contributed: doc + §<ID> (or §heading in a doc without IDs), body/thread passage, dossier source.
Any provisional-default open question carried by the behaviour this slice delivers.>
```

**`## Acceptance criteria`, deliberately not `## Definition of done`.** The DoD heading carries a
contract three skills own — the resolver projects ticks onto it, the evaluator verifies them with
sticky vetoes, the planner reconciles them — and all three operate on the issue the resolver is
running on, which under slice-as-phase is the **parent**. A DoD on a slice would be a checkbox set
nothing ticks. The checkbox form here is a DoD's checkbox shape plus a stable id per criterion — under
a heading no DoD reader parses, which is the point (and why the drafter's revise and the planner's
DoD-heading card leave a slice's `## Acceptance criteria` alone).

**The `AC-N` ids are useful even though nothing ticks them per-criterion:** a plan phase's
`deliverable` line can cite `AC-2, AC-3` in prose, with no grammar change. Stable ids beat positional
references for the same reason doc slug IDs do.

**Ticking (Tier 1).** The resolver ticks **all** of a slice's criteria at the moment it closes the
slice — its last serving phase shipping — so a closed slice never reads as half-unmet. Per-criterion
ticking as each phase ships (Tier 2) is deliberately **not** built: it would need a seventh key in a
closed phase grammar, a second projection target, slice-scoped annotation forms, and a second
reconciliation axis. A slice served by several phases is the **expected** shape — a milestone, not an
increment (§4.1) — so its criteria stay unticked until the last of them ships, and the board's
progress resolution is the milestone. **The trigger for revisiting Tier 2, with its diagnosis
attached:** an operator who needs criterion-level progress *inside* a milestone. That is a case for
the machinery, never for cutting thinner slices — a thinner cut trades the milestone the board exists
to show for a phase list re-filed as issues.

### 8.2 Epic altitude — the Story template

A story is a **plain issue with its own branch, PR and plan**, so it uses the house Story template
([`../../drafter/references/issue-templates.md`](../../drafter/references/issue-templates.md)) — the
drafter owns those templates, and duplicating them here is exactly the drift #16 removed. Title: a
plain behaviour-naming title, with **no `<parent#>/S<K>` designator** (that form marks slices, and a
story is not one). The first line is the `**Epic:** #<epic-#> — <Epic title>` backlink. Label it
`story`.

**A story does carry `## Definition of done`, and the inversion is deliberate.** §8.1's reasoning
turns on *who the resolver runs on*: for a slice that is the parent, so a slice's own checkboxes would
be unticked forever. A story **is** the issue the resolver runs on — it gets its own branch, plan and
phases — so its DoD is the contract all three skills already own. Do not "harmonize" the two by giving
slices a DoD or stripping a story's.

## 9. Presenting for approval

Show the operator, in this order:

1. **Grounding summary** — one line per source read, or noted absent, with its authority.
2. **The decomposition strategy** (§4), and at epic altitude what the coalescing pass merged (§5.1).
3. **The ordered child list**, one line each: designator or title + the outcome sentence, with
   adoptions marked as adoptions and their live state shown.
4. **The full proposed bodies.**
5. **A closing note**: major technical risks, assumptions made, and anything needing stakeholder
   clarification. A real open question routes out to the drafter, never silently into a child body.

They may approve, re-cut, reorder, retitle, or drop children; iterate until approved. Nothing is
written to GitHub during this step.

A **no cut** (§4.1) replaces items 3 and 4 with the one milestone and why each increment is a phase
of it rather than a slice — the merge signal that folded it, per increment — so the operator can
judge the claim, not take it.

## 10. Anti-patterns

- **Horizontal children** — any child whose title names a layer instead of a behaviour.
- **The issue restated** — one child that is just the whole parent. At story altitude that is a
  single milestone, and the answer is no cut (§4.1); at epic altitude, if it genuinely cannot be cut,
  say so and explain why rather than pretending otherwise.
- **A slice per increment** — a slice no stakeholder would track apart from its neighbours: a
  returning path, a variant, a recorded failure behaviour of another slice's outcome. It is a phase
  and acceptance criteria inside that slice (§4.1).
- **Uncited children** — nothing to cite means invented scope (§3).
- **Inflation** — more children than the sources' distinct outcomes justify. Ten or more means the cut
  is sharding tasks rather than sequencing behaviour.
- **Editing the parent body outside a gate** — child detail lives only in child bodies. The cut never
  touches the parent; the three sanctioned parent-body writes (a promotion rewrite, a legacy-checklist
  reconciliation, an omitted bookend's `## Background` note) are the playbook's, each behind its own
  explicit confirmation.
- **A slice with its own sub-issues** — a slice is never sliced. A *story* is sliced later, in its own
  run against that story, never from an epic-altitude cut.
- **Designators on stories** — the `<N>/S<K>` form marks a slice; using it for a story makes the
  by-construction identification unreadable.
- **Inventing failure modes** — operational children trace to recorded requirements (§6).
- **Filing a story a bookend already covers** — foundation groundwork belongs in the foundation slot,
  not duplicated into a feature story (§5.2).
