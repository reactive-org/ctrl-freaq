# cut — ground, decompose, review, approve, file

The one flow: (promote) → ground → decompose → adversarial review → approve → the write gate →
sequential filing → handoff. Both altitudes and both modes run these same steps and differ only in
values: `facts.vector.altitude` sets the bar and the child template, `facts.children` holds what exists,
`next_index` is where story-altitude numbering continues. Facts come from prep (SKILL.md §1); the write
discipline, the zero-mutation gate, the never-edit-the-parent rule, the review-loop control and the
partial-failure rule are SKILL.md §3, not restated here.

## S0 — Promote (only when `facts.promotion`)

The target is a standard issue the operator asked to become an Epic. Rewrite it **before** cutting, so the
rest of the session is an ordinary epic-altitude run. Draft the Epic body from the house Epic template
([`../../drafter/references/issue-templates.md`](../../drafter/references/issue-templates.md)), redistributing
#N's own content between it and the story bodies you propose (edit history preserves the original). A planner
seam-analysis comment pre-seeds the candidate story list — test it in S2, never authority.

This rewrite is destructive where a `create` is not, so it does **not** ride the cut's gate: diff-show the
old→new Title, Labels ±, and changed/added/removed sections, wait for **explicit confirmation**, then apply
all three. Draft the new title **with** the `Epic:` prefix — classification is **lexical**
([`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md): an issue with neither
label nor prefix reads as a non-epic), so an unprefixed title half-promotes #N. Pass the confirmed title
whole, never re-prefixed. Preserve any `> 📋 **Implementation plan:**` pointer verbatim — the superseded plan
comment is the planner's artifact, replaced by the Epic's re-plan. A `## Stories` section is dropped: the
native relation, not body text, is the story record.

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-body <owner/repo> <N> "<facts.scratch>/epic.md"
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-title <owner/repo> <N> --title "<confirmed title>"
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-labels <owner/repo> <N> --add epic --remove <old-type>
```

On decline: perform none of the three writes, emit the *Declined at the promotion gate* shape, stop — an
unpromoted epic-altitude cut files stories under a non-epic parent, which the by-construction rule reads as
slices.

## S1 — Ground, or refuse

`Read` [`../references/slicing-method.md`](../references/slicing-method.md) first — the method, the
per-altitude child templates, and the presentation format this flow delegates to. Then read the target's
body and thread from `facts.sections` (spilled paths).

Read the docs `facts.grounding_docs` declares, from `facts.root.path` by absolute path — plain `Read`/`Grep`.
Treat each per its `role` (what it answers) and `authority`: `binding` = a cut that contradicts it is wrong,
not a trade-off; `informative` = context you weigh and cite. A `kind: dir` entry is a document set: read the
`members` the cut touches, cite the member file. Skip and note an entry whose `present` is false. If
`facts.research.present`, also read the dossier — current external truth the cut may cite, never authority.

**The grounding gate — the one gate that cannot degrade into proceeding.** No entries (or
`DOC_CATALOGUE_ABSENT` in `facts.notices`) **and** no sources the operator named at invocation: file
nothing, emit the *Refused — no grounding* shape, stop. If the operator did name sources, read those
and proceed.

## S2 — Decompose

Write the **decomposition strategy** first — two or three sentences: which seam carries the most risk,
what the walking skeleton is, what is deliberately deferred. It stays in this session; persisting it
parent-side would create a second source of truth about the child set, and each child's own rationale
section reconstructs the reasoning.

Cut per the method reference **at `facts.vector.altitude`**: walking skeleton first; each subsequent child
adds exactly one observable increment (a milestone at story altitude); every child clears its bar in one
sentence; prefer few, thick-enough children. At story altitude also apply the milestone pass (method §4.1)
— a slice is a milestone a stakeholder tracks, never an increment the parent's phases already sequence —
and if a `fresh` cut leaves **one** milestone the proposal is a **no cut**: skip S3 (nothing to review)
and take S4's no-cut card. At epic altitude also apply the coalescing pass and the two bookend slots
(method §5) — an omitted bookend is recorded with its reason in the epic body's `## Background` (an
approved S4 row, never a silent write and never a silent omission). On `mode: resume` treat
`facts.children` as fixed — already approved, possibly already shipped — and cut only the remainder,
numbering from `facts.children.next_index` at story altitude. `facts.children.placeholder_count` entries
are the exception: a legacy `## Stories` bullet with no issue number names a story nobody has **filed**,
so it is part of what this cut files, not part of what it treats as done.

Include any `facts.adoption_candidates` as candidate children, marked as adoptions with their live state;
adopt an existing issue *instead of* filing a new child whose scope it already covers, never both. For a
candidate the operator names now, run the one-shot `--adopt-check` (SKILL.md §1).

**Citation duty.** Every child's grounding names what it derives from: the catalogue docs (with §refs)
recording the behaviour, the issue body/thread, the dossier. A child that can cite nothing is a gap in the
source or invented scope — surface it as a finding and leave it out of the cut. Carry any
`provisional-default` marker from `facts.open_questions` into the grounding of every child it touches.

## S3 — Review adversarially

Dispatch [`../references/cut-reviewer-prompt.md`](../references/cut-reviewer-prompt.md) (resolve its
absolute path yourself — a reference path is not substituted in a dispatched prompt). Fill `<<altitude>>`,
the parent (staging its body to `facts.scratch` and passing the **path**), the ordered candidates, any
existing children, `facts.grounding_docs`, and `facts.root.path` as `<<repo_root>>`. Run the **split
pass** (`<<pass>>: split`, dimensions `ordering, sizing`) on titles-plus-scopes, then the **re-confirm
pass** (`<<pass>>: re-confirm`, dimensions `ordering, sizing, conformance`) once bodies exist — a body
can reveal a child is bigger or smaller than its scope claimed. On passes 2+ also fill
`<<changed_summary>>` with what changed since the last pass, so the reviewer re-verifies only that plus
its own prior findings instead of re-reading the whole cut.

Apply the merges, splits and re-orders the findings justify; contest one you can refute with evidence and
say so. Re-loop under SKILL.md §3's control (3-pass cap, circular guard). A BLOCKER you neither fix nor
refute goes to the operator in S4's closing note — never silently past it. A `milestone` finding that
folds the whole cut into one slice, once adopted, makes it a no cut.

## S4 — Present, iterate, and the write gate

Show the operator, per the method reference's presentation format: the grounding summary (one line per
doc read or noted absent), the strategy (and what coalescing merged, at epic altitude), the ordered child
list (designator-or-title + outcome sentence each, adoptions marked), the full proposed bodies, then a
closing note of risks, assumptions, unresolved reviewer findings, and anything wanting stakeholder
clarification. They may approve, re-cut, reorder, retitle, or drop children — iterate until approved. An
open question surfacing here is **not** a child: route it out (`/ctrl-freaq:drafter` files the
companion `question` issue) rather than burying a decision in a child body.

Then one summary table — designator-or-title · title · one-line outcome · the pending write (`create
--parent` for a new child, `add-parent` for an adoption, `edit-body` for a checklist reconciliation or a
bookend-omission `## Background` note) — and **one** explicit `AskUserQuestion` gate (per
[`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md)). Aborting here costs nothing; on
decline perform no writes and emit the *Declined at the write gate* shape (the proposed cut is in the
transcript; a re-run re-derives it).

**A no cut skips the table and the write gate** — nothing would be written. Show the grounding summary,
the strategy, the one milestone with why each increment is a phase of it (method §9), and what tracked slices would buy
here (a board row and its own acceptance criteria per milestone), then ask **one** `AskUserQuestion`. *File nothing — plan
multi-phase* (recommended unless the operator needs those) writes nothing and emits the *No cut — plan it multi-phase* shape. *Cut anyway* has the operator name the milestones to track, and S2
re-runs with them as **fixed inputs**: the milestone pass never folds them, they reach the reviewer marked
`operator-named`, and you contest any finding that re-folds them, citing the operator's answer. A genuine
decision: the operator asked for slices, here or at the planner's "Slice first".

## S5 — File the approved children

Stage each body to `<facts.scratch>/` — `slice-S<K>.md` at story altitude, `story-<i>.md` at epic
altitude — then apply the approved list **one at a time, in approved order**, interleaving creates and
adoptions exactly as approved:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create <owner/repo> "<facts.scratch>/slice-S<K>.md" \
  --title "<N>/S<K> — <behaviour phrase>" --parent <N>
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create <owner/repo> "<facts.scratch>/story-<i>.md" \
  --title "<story title>" --label story --parent <N>
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py add-parent <owner/repo> <adopted-#> <N>
```

A `create` envelope returns the new issue's `url`, not its number — parse `#<n>` from the URL's trailing
segment for the report and the handoff. `add-parent` reports `changed`; an adopted issue's body is never
rewritten here.

At epic altitude, an **omitted bookend** approved at S4 lands as its `## Background` note in the same
`edit-body` that reconciles the checklist when both apply, or its own otherwise. It goes in the epic body
rather than this session's report because the reviewer re-reads it on every later resume to judge whether
the justification still holds; a session-only note would be gone by then.

When `facts.children.source` is `checklist` or `mixed`, the epic's legacy `## Stories` section is still
the only record of those entries: reconcile its checkboxes against live state (closed → checked, open →
unchecked) with **one** `edit-body`, and leave the section in place. `facts.attention` already names each
mismatch. This is the second sanctioned parent-body write, and it was approved at S4 like any other row.

A `SUBISSUES_UNSUPPORTED` notice means the relation was not established, so that child is **unparented**:
stop, report it, file no more. An unparented child drives no rollup — the entire reason these are issues —
so prose-linked children are worse than none: they read as tracked progress that does not exist.

## S6 — Handoff

Emit the matching shape from [`../references/handoff-renderings.md`](../references/handoff-renderings.md)
verbatim, per SKILL.md §4. The forward route is the planner: its phases must map onto the children just
filed (`sub-issue:` in [`../../planner/references/plan-schema.md`](../../planner/references/plan-schema.md)),
and its reconciliation reads the live set — so a plan authored before this cut is now stale (after a no
cut there is no set: the planner plans #N multi-phase). At epic altitude the planner posts the
epic-level plan (cross-story contracts + sequencing) and each story is then planned just-in-time.
