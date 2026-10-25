---
name: requirements-gatherer
disable-model-invocation: true
description: Interactively gather the requirements for one filed GitHub issue and record them on that issue in the established Definition-of-done format. It suggests grounding documents to read from the repo's doc catalogue (asking the operator what to add or drop), elicits an enumerated requirement set in discussion with the operator (showing a human-readable draft and iterating until approved), and then appends one plain unticked DoD criterion bullet per approved requirement — each carrying a stable issue-minted `REQ-<issue>-<seq>` id other skills can cite, and each citing its source document by durable anchor, or provenanced as operator-elicited when no doc records it. Detail is never duplicated from a doc into the issue; later stages (planner, resolver) read the cited sections from the source documents. Explicit-invocation only — run it as `/ctrl-freaq:requirements-gatherer <issue>`. Report-then-apply, one GitHub write (the gated DoD body edit); it edits no tracked files, authors no documents, and files no issues. Not for filing or revising an issue (that's `/ctrl-freaq:drafter`), cutting it into slices (`/ctrl-freaq:slicer`), planning the build (`/ctrl-freaq:planner`), resolving a question issue (`/ctrl-freaq:question-resolver`), or a project-wide doc sweep (`/ctrl-freaq:question-sweep`). It refuses epics, slices, question issues, and closed issues — and says why.
---

# requirements-gatherer — router

Elicit the requirements for **one** filed issue and land them on that issue's
`## Definition of done` as criterion bullets that **cite their sources instead of restating
them** — a doc-grounded requirement points at the document that records it; an operator-elicited
one is recorded on the issue, which becomes its source of record. **The operator owns the
requirement set — never you.** You suggest the grounding, draft the candidate set, and iterate;
every requirement that lands was shown to and approved by the operator. One interactive session;
ends with a plain summary, never a `## Handoff`.

## 1. Prep

Assemble the entire starting state in **one** call:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_requirements_gatherer.py <issue> <owner/repo> [--root <path>]
```

It returns one JSON **facts block** (`architecture.md §4`): `vector` (`type` × `refusals`),
`suggested_playbook`, `target` (number/title/state/labels/`type`, the typed `parent`, open
`blocked_by`), `dod` (`present`, `bullet_count`, `annotated_count`, `next_req_seq` — the
append-only id sequence a re-run continues from — and the parsed `bullets` with 1-based indexes
and any existing `req_id`), `plan` (`present` — the `<!-- implementation-plan:v1 -->` marker; a
planned issue is mid-flight even with zero annotations), `grounding_docs` (the repo's `<!-- doc-catalogue -->` entries —
`path`/`role`/`authority`/`summary`/`present`/`kind`/`members`/`abs_path`), `sections` (spilled body/thread
paths), `scratch`, and `attention`. Consume each as **data** — never re-derive the type, the
refusal set, the bullet indexes, or the id sequence.

**Decision card rule.** If prep exits `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)),
act, and re-run prep — the single universal handler for every closed-set code (`AUTH_REQUIRED`,
`TARGET_IS_PR`, `DOD_MALFORMED` — a DoD the parser can't index is repaired or aborted before
anything is appended to it).

## 2. Route

| `vector` | Route |
|---|---|
| `refusals: []` | `playbooks/gather.md` |
| `refusals` non-empty | no playbook — refusal summary, stop |

On a refusal, **do not read the playbook**: state the first refusal's reason (the `attention`
line carries the evidence) plus its breadcrumb, and stop — nothing is written on any refusal
path. The set is closed and fully mechanical: `epic-target` (an epic's DoD is outcome-level;
requirements belong on its stories — breadcrumb the open stories, or `/ctrl-freaq:slicer`
when none exist, since the slicer cuts an epic into stories), `slice-target` (a slice carries the slicer's `## Acceptance criteria`, never a
DoD — breadcrumb the parent story), `question-target` (a question issue carries no DoD by
contract; a human answers it in its thread), `closed-target` (criteria on delivered work can
never be projected or verified).

There is no mode fork and no route override: an issue with or without an existing DoD differs
only in **values** (`dod.present`, `dod.bullet_count`), never in actions taken (CLAUDE.md's
"parameterize before you playbook"). An open blocker is **not** a refusal — elicitation is
upstream human input — but `attention` surfaces it for the operator.

## 3. Invariants

- **Zero GitHub writes before the one DoD gate.** Everything up to the confirmed DoD diff is a
  read; the session's only write is the gated `edit-body`. Aborting at the gate costs nothing.
- **Single write path.** The body edit goes through `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py
  edit-body` via Bash: stage the full revised body to `facts.scratch`
  (`/tmp/gh-requirements-gatherer-<N>/`) and pass the **path**. The script gates empty bodies
  (`EMPTY_BODY_FILE`) and returns `body_sha256`; a zero exit **with `body_sha256`** is the
  confirmation — never re-read the issue to check it landed (a zero exit carrying a
  `needs_decision` envelope wrote nothing). No raw `gh` writes, ever.
- **Append-only DoD.** New bullets go **after** the existing top-level bullets; existing bullets
  are never reworded, reordered, re-ticked, or annotated — their 1-based indexes must not shift
  ([`../_shared/dod-annotations.md`](../_shared/dod-annotations.md) index stability). Everything
  outside the `## Definition of done` section is preserved byte-for-byte.
- **Cite, never restate.** Each bullet opens with a stable bold id (`**REQ-<issue>-<seq>**` —
  issue-minted so identity outlives provenance; assigned from `dod.next_req_seq`, append-only,
  never renumbered or reused) and is a one-line falsifiable criterion ending in an em-dash
  provenance tail — a durable doc anchor (`— docs/prd.md §4.2`) or `— operator elicited <date>` —
  **never a trailing parenthetical** (that position is the annotation grammar's). Doc-grounded
  detail stays in the doc; duplicating it into the issue is a defect. Operator-elicited detail
  may ride as indented sub-bullets — the issue is that requirement's source of record. The full
  grammar: [`references/requirements-format.md`](references/requirements-format.md).
- **The operator decides — never you.** Present the candidate set, mark what's doc-grounded vs
  elicited vs contested, and iterate until explicit approval. A contested or unanswerable point
  is surfaced and recorded in the summary — never silently absorbed into a bullet, and never
  filed as an issue (this skill files nothing; the drafter owns question filing).
- **Gates only for genuine decisions** (per
  [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)): the grounding selection, the
  elicitation-loop approval, the mid-flight warning (a plan marker or annotated bullets), and the
  final DoD diff confirmation. A judgment gate left with one credible option is announced, not asked; every card opens with its brief.

## 4. Summary — not a `## Handoff`

The requirements-gatherer is **not** a pipeline stage: no cross-session handoff, no GitHub state
beyond the one body edit. It ends with a plain **summary** (the flow's last step): the bullets
appended (each `REQ-<issue>-<seq>` id + criterion), the write outcome (`body_sha256` returned), the grounding read, any
contested points left unrecorded (each with a `/ctrl-freaq:drafter` breadcrumb to file it as
a question), a `/ctrl-freaq:setup` breadcrumb when the catalogue was absent, and a
`/ctrl-freaq:planner <N>` breadcrumb — a pointer, not a forward handoff; when the issue
already carried annotated bullets, say the resolver's next projection will re-route to the
planner and point there first.
