---
name: slicer
description: You have one filed GitHub issue whose milestones you want tracked separately, and you want it cut into ordered, operator-approved children filed as native sub-issues so GitHub's own rollup tracks delivery progress — not merely several steps, which one multi-phase plan sequences. Cuts at two altitudes: a story or standalone issue into **deliverable slices** (requests like "slice #103", "cut #103 into deliverable slices", "break this story into milestones we can track before we plan it", "decompose #103"), and an **Epic into stories** (requests like "split epic #150 into stories", "cut #150 into stories", "promote #142 to an Epic and split it", or when the planner re-routes here after its seam gate finds an issue epic-shaped, or the operator chooses to track its milestones as slices). Also draws an epic around issues that already exist, adopting them as its stories. It is report-then-apply: nothing is written to GitHub until you confirm the whole cut, and it refuses rather than guessing when the repo declares no grounding documents. Also handles the resume case — re-run it on a partially-decomposed parent and it reports what exists and cuts only the remainder. Do NOT use it to author the plan or its phases (planner), to write code (resolver), to file or revise an ordinary issue body (drafter), or on an issue that is blocked, closed, a question, or itself a slice — it refuses each of those and says why.
---

# slicer — router

Cut **one filed issue** into ordered children and file them as native sub-issues in approved order.
One session; the children and the handoff are all that survive. Read this router, run prep, run the one
playbook, hand off. Your judgment is the decomposition: where the seams are, what the walking skeleton
is, how thick each child should be, and whether the grounding supports cutting at all.

**One operation, two altitudes**, differing in exactly one parameter — the bar each child must clear,
set by whether the child gets its own branch and PR. `facts.vector.altitude` names which:

- **`story`** — children are *deliverable slices*: phase markers with no branch of their own, so the bar
  is independently **demonstrable**. The resolver ships each as a phase on this issue's branch and
  closes it as that phase lands.
- **`epic`** — children are *stories*: each gets its own branch, PR, review and merge, so the bar is
  independently **shippable**.

Either way that closing is what makes the parent's rollup a live progress record; the definitions and
the closing contract are [`../_shared/epic-story-hierarchy.md`](../_shared/epic-story-hierarchy.md), and
the bar itself is stated once in
[`references/slicing-method.md`](references/slicing-method.md) §1.

## 1. Prep

Assemble the entire starting state in **one** call:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_slicer.py <issue> <owner/repo>
```

It returns one JSON **facts block** (`architecture.md §4`): `repo`, `scratch` (where bodies are
staged), `root` (`path`/`sha` — the grounding vantage), `vector` (`type` × `altitude` (`story`/`epic`)
× `mode` (`fresh`/`resume`) × `refusals`), `suggested_playbook`, `target` (number/title/state/labels/`type`,
the typed `parent`, open `blocked_by`), `promotion`, `children` (what already exists, in panel order —
`kind` (`slices`/`stories`), `entries`, `count` and `open_count` (**filed** children only),
`placeholder_count` (legacy `## Stories` bullets naming stories nobody filed — the cut still files
those), `total_named`, `next_index`, `source`), `adoption_candidates`,
`grounding_docs` (the repo's `<!-- doc-catalogue -->` entries — `path`/`role`/`authority`/`present`/`kind`, a `dir` entry's `members`),
`research` (dossier present), `open_questions` (the `in-scope (blocked)` entries), `sections` (spilled
body/thread paths), and `attention`. Consume each as **data** — never re-derive the type, the altitude,
the mode, the refusal set, or the slice numbering.

**Two flags you pass, because prep cannot read the invocation prose.** Add `--promote` when the
invocation asks for a standard issue to become an Epic (the planner's seam-gate off-ramp); add
`--adopt <N>` per already-filed issue the operator names as a child. For candidates named mid-flow, the
one-shot `prep_slicer.py <owner/repo> --adopt-check <N>` reports live state without re-running prep.

**Decision card rule.** If prep exits `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)), act,
and re-run prep — the single universal handler for every closed-set code (`AUTH_REQUIRED`,
`MARKER_AMBIGUOUS`, `TARGET_IS_PR`).

## 2. Route

| `vector` | Route |
|---|---|
| `refusals: []` (any `altitude`, `mode: fresh` or `resume`) | `playbooks/cut.md` |
| `refusals` non-empty | no playbook — refusal handoff, stop |

On a refusal, **do not read the playbook**: render the matching shape from
[`references/handoff-renderings.md`](references/handoff-renderings.md) (first token wins; the `attention`
line carries the evidence) and stop. Nothing is written on any refusal path.

Both altitudes and both modes share one playbook deliberately: they differ in **values** (which bar,
which children exist, which template, what index numbering resumes from) and in one gated prefix step,
never in the actions the cut takes — facts, not a branch (CLAUDE.md's "parameterize before you
playbook"). The refusal set is closed and fully mechanical: `slice-target` (**a slice is never sliced** —
no branch, so a sub-slice could not ship), `question-target`, `closed-target`, `blocked` (an open native
blocker or an `in-scope (blocked)` open question — cutting against an unanswered question produces
children the answer may invalidate). An **epic is not refused**: it is the epic-altitude happy path.

**The promote rule** is the one thing the router decides, because only the router sees the invocation.
When it asks to reshape the target as an Epic ("promote #142 to an Epic", or a planner handoff saying to
split #N as an Epic per its seam-analysis comment), re-run prep with `--promote`. When only the *thread*
recommends it, ask first (`header: "Issue size"`) — promotion rewrites the issue, so it is never silent.

## 3. Invariants

- **Zero GitHub mutations before the one write gate.** Everything up to the cut summary is a read;
  everything after it is a write ([prd.md §8.2](../../docs/prd.md) report-then-apply). Aborting at the
  gate costs nothing — that is the point of the gate, so never let a write creep earlier. The promotion's
  three writes — body rewrite, title rewrite, label swap — are the only ones that precede it, behind their
  **own** explicit confirmation (S0) — the cut itself still mutates nothing before its gate.
- **The parent's body is never edited** by the cut, and no `## Slices` section is ever written (the
  shared contract says why). Child detail lives only in child bodies. Exactly three parent-body writes
  are sanctioned, each an approved row in the S4 gate rather than a side effect of filing: the promotion
  rewrite (S0), a legacy `## Stories` checklist reconciliation, and — at epic altitude — the
  `## Background` note recording an **omitted bookend** and its reason, which has to live in the epic
  body to survive later resumes (method §5.2). Anything else touching the parent is a defect.
- **Grounding is required, and every child cites it.** Cut only from what the repo's declared docs,
  the issue body/thread, and any dossier actually record. A child citing nothing is a gap in the
  source or invented scope — surface it, never file it. No catalogue and no operator-named sources →
  refuse: decomposing from a two-line body invents scope, and invented scope becomes real issues the
  pipeline then plans against.
- **The cut is reviewed adversarially before the operator sees it.** Dispatch
  [`references/cut-reviewer-prompt.md`](references/cut-reviewer-prompt.md) and resolve its findings under
  the standard control: a 3-pass cap and a circular guard (a finding you have already answered with
  evidence does not re-open). It returns findings and never asks the operator — this loop does.
- **Sequential creation in approved order.** `addSubIssue` appends, so creation order **is** display
  and delivery order: file one slice at a time — one child per call — via
  `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create --parent` for a new child and `add-parent` for an
  adopted one, staging each body to `facts.scratch/` and passing the **path** (the script gates empty
  bodies and returns `body_sha256`, so no body crosses a dispatch prompt). Never batch, never
  create-all-then-parent-all, never reorder after.
- **Partial failure is reported, never rounded up.** The gate guarantees zero-mutation *aborts*, not
  atomic *execution*: a mid-batch failure stops and reports exactly which children landed — never "all
  done" after a partial run. **Resume, don't duplicate**: on `mode: resume` report the existing children
  and cut only the remainder, numbering from `children.next_index` at story altitude.

## 4. Handoff

Every clean run — including every refusal — ends with a single `## Handoff` block, the only bridge to
the next session. The schema, omission rules, the `Slices:` and `Stories:` lines, and the closed-set
state markers are owned by [`../_shared/handoff-format.md`](../_shared/handoff-format.md); this skill's
shapes are in [`references/handoff-renderings.md`](references/handoff-renderings.md). **Read that
reference immediately before composing the handoff — not earlier — then emit the matching shape
verbatim.** The field names (`**Issue:**`, `**Epic:**`, `**Slices:**`, `**Stories:**`, `**Grounding:**`,
`**Next:**`, `**Why:**`), block structure, and closed-set markers are **contract, not prose to
summarize**: substitute only numbers, titles, states and citations — never paraphrase, restructure,
rename a field, drop a segment, or add a block the shape lacks. The forward route is the `planner`
(`/ctrl-freaq:planner <N>`), whose phases map onto the children you filed — or, after a no cut,
plan #N multi-phase; the grounding refusal routes to `/ctrl-freaq:setup`.
