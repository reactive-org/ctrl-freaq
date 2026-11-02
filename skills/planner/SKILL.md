---
name: planner
description: Plan *how* to build an already-filed GitHub issue (or an entire Epic and its stories) before any code is written, and post a durable, verified implementation plan onto the issue. A specialized multi-step workflow that writes to GitHub — not a quick inline answer: it researches the approach, grounds every decision in codebase precedent and the project docs the repo declares in its doc catalogue, surfaces deviations for the user to approve, optionally ingests updated external docs, validates the plan with an isolated review sub-agent, then posts it as an `<!-- implementation-plan:v1 -->` comment. It is the planning step **between** the drafter (files the issue) and the resolver (writes the code). Trigger whenever someone, referencing an issue number, wants the design / approach / architecture / layering / file-level changes / test strategy / sequencing settled and verified up front — "develop an implementation plan for #N", "work out the layering and file changes for #N", "how should we implement/build #N — figure out the design first", "before anyone writes code for #N, write the approach up on the issue", "plan this epic and its stories", or revising a stale plan ("update the plan on #N", "re-plan #N against the new docs"). Treat "implement", "build", or "step-by-step" as planning when the goal is settling strategy ahead of coding. Do NOT use for: filing a new issue (that's the drafter), writing or fixing the code (that's the resolver), reviewing a diff or PR or choosing a merge strategy (that's the evaluator), or answering a documentation question.
---

# planner — router

The design stage of the pipeline: a filed issue in → a verified, durable `<!-- implementation-plan:v1 -->`
comment plus a `## Handoff` out. One planning attempt, one session; a fresh session on every re-entry (nothing
survives between runs except what is persisted to GitHub). Scripts own the mechanical work; yours is the
judgment — classification, grounding, gates, drafting, review verdicts, and the handoff `Why:`.

## 1. Prep

Assemble the entire starting state in **one** call — `<issue>` from the user, a URL, or the branch:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_planner.py <issue> <owner/repo>
```

It returns one JSON **facts block** (`architecture.md §4`): `target` (number/title/state/labels/
`blocked_by`/`blocking`), `vector` (`type` × `mode` × `plan_ref_row`), `suggested_playbook`, `plan_ref` (a
**bare** branch name), `plan` (present/SHA/comment-id/url — a present plan is the **revise** trigger),
`research` (dossier present + staged path), `grounding_docs` (the repo's declared docs, resolved at `plan_ref`
— `role`/`authority`/`present`/`kind`, a `dir` entry's `members`), `open_questions` +
`open_question_candidates` (the Bug (a) tracker search), `grounding` (the observed ambient checkout, asserted
by content against `plan_ref` — `basis` says why it serves; its `sha` the footer records; `files_path` its tracked-file list, for S7), `epic`/`story` (stories + state; parent epic + plan +
delivery log), `revise` (prior-plan SHA vs grounding SHA + any open PR's `## Phase tracker`), `slices` (the
deliverable sub-issue set, staged bodies, and the plan-versus-live `diff`), `dod` (DoD `present`/`count`,
`other_checklists` under another heading, and `body_path` — the session's working copy of the body),
`sections` (spilled issue-body/thread/plan-marker paths), and `attention`. Consume every fact as **data** —
never re-derive the type, mode, `plan_ref`, row, tracker candidates, or sub-issue diff; prep already did.

**Decision card rule.** If prep exits with `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)), act on
the answer, and re-run prep (`--refresh` for volatile facts). This is the single universal handler for
every closed-set code (`AUTH_REQUIRED`, `MARKER_AMBIGUOUS`, `WORKSPACE_MISMATCH`, `AMBIGUOUS`, `TARGET_IS_SLICE` on a slice of a non-epic parent, …).
`WORKSPACE_MISMATCH`: prep accepts a checkout by **content**, never by branch name — `plan_ref` itself (a default-branch one from any current checkout **including the project root**: plan-before-open; only staleness is checked, never uncommitted changes), this issue's own branch already **containing** its base ref's tip (rows `story-own-branch` / `own-branch`), or any branch sitting exactly at that tip (`grounding.basis: identical`).
A checkout *behind* the tip still refuses — rebase onto `origin/<base-ref>` rather than moving. One carrying another branch's commits beyond the tip is a card: on **plan against this checkout anyway**, re-run the **full** prep with `--ground-ambient` (row `ambient-accepted`; never `--refresh` for this run — it skips grounding, so there'd be no `grounding` fact) and carry that flag on every later prep run this session, `--refresh` included — dropping it re-asks.
A refused prep is a card to render, never a reason to ground on the checkout yourself: no facts block, no plan. Any other answer — the operator fixes it, never you.

**Newly-detected OQ lookup.** When grounding surfaces an open question the issue body does **not**
already record (so prep's body-driven `open_question_candidates` never searched it), run the tracker
de-dup search before recording it `(not filed)` — one authorized call, never a raw `gh issue list`:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_planner.py <issue> <owner/repo> --oq-query "<OQ topic>"
```

## 2. Route

Prep proposes `suggested_playbook`; confirm it against this table, keyed on `vector`. Read **exactly
one** playbook.

| `vector` | Playbook | Flow |
|---|---|---|
| open parent epic — `story.parent_epic_open`, whatever the `type` (any `mode`) | `playbooks/story-jit.md` | just-in-time story plan against epic HEAD; bootstraps the epic plan inline when absent |
| `mode: revise` (standalone issue or epic) | `playbooks/revise.md` | reconcile old-vs-new plan + projected DoD; SOFT/HARD gate; `## Predecessor` |
| `type: epic` (fresh) | `playbooks/epic.md` | epic-level plan (`## Story breakdown`/`## Story contracts`/`## Integration strategy`); stop before per-story fan-out |
| everything else fresh (no open parent epic, whatever the `type`) | `playbooks/single.md` | single-issue plan; `## Phases` when multi-phase |

Every playbook opens by reading the shared spine `playbooks/plan-spine.md` (S1–S8, classify through persist). The routed playbook supplies
only what **differs in actions**: the schema sections it fills, the reviewer dimension set it passes, its pre-draft reconnaissance, and its
handoff shape. Type differences the spine consumes (`plan_ref`, the dimension set, which schema sections, `off-ramp` — which off-ramps the
shape triage may offer) are **facts / values**, never branches.

**Override rule** (`architecture.md §5`): honor `suggested_playbook` unless the thread, read under the spine's S1 author rule, carries evidence
the script could not see (e.g. it supersedes the labels' type) — state the reason. Never interleave type
branches inside a playbook body: the route *is* the branch; one route per session.

## 3. Invariants

Universal across every route:

- **Grounding is read-only, ambient.** The planner never gets a work workspace and writes no
  code (`architecture.md §6`). Every doc/precedent Read/Grep/Explore targets
  `facts.grounding.path` (the asserted ambient checkout, already at `plan_ref`) by absolute path.
  No `git show <ref>:path`, no `git grep <ref>` — its `sha` is the footer's `@<short-sha>`. Never
  branch, commit, or stash there; the planner's only write surface is the plan's three comments.
- **Marker is always the comment's first line.** Every consumer (resolver, evaluator, drafter, this skill's own revise
  lookup) matches `<!-- implementation-plan:v1 -->` with `startswith` — any character before it hides the plan; a story's
  `**Epic:**` backlink goes on the line *immediately after* it. A part's `<!-- implementation-plan-part:v1:<slug> -->`
  is a separate family that never matches it ([`../_shared/plan-parts.md`](../_shared/plan-parts.md)).
- **Footer/handoff record the branch, never elide it.** `<plan-ref>` is the ref the plan was GROUNDED on — not
  always the resolver's PR base; render per `plan-spine.md` S5 + [`references/handoff-renderings.md`](references/handoff-renderings.md).
- **Staged-body writes.** Every GitHub write goes through
  `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py` via Bash: stage the verbatim body to
  `facts.scratch` (`/tmp/gh-planner-<issue>/…`) and pass the **path**. The script verifies the
  round-trip hash, returns `body_sha256`, and gates empty bodies (`EMPTY_BODY_FILE`) — the #626/#627
  race fix. Never re-serialize a body across the prompt boundary; never hand-roll a persist/gather `gh`
  call. The planner has **no** scriptless raw-`gh` executor.
- **Successful write is self-confirming.** A zero exit with a URL *is* the confirmation; never re-read it.
- **Gates only for genuine decisions** (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)):
  latest-decision-direction, DoD heading, external sources, seam-disposition, deviation, decision, review-notes
  disposition, show-before-post (opt-in), revise reconciliation, sub-issue reconciliation. Never to confirm a
  fact prep derived. A judgment gate left with one credible option is announced, not asked; every card opens with its brief. A judgment sub-agent (the plan reviewer, an `Explore` precedent search) never calls
  `AskUserQuestion`; it returns its result to this loop, which asks.
- **A tracked open question is a human's call** — never resolved from precedent or the decision gate.
  Record it in `## Open questions`; if the whole plan is gated, re-route rather than post a hollow plan.
- **Handoff on clean exit** (§4). One `## Handoff` block ends every clean run — the seam gate's
  epic-shaped abort included (comment posted, no plan, no label); it replaces any ad-hoc narration. Its one sanctioned companion is the
  `## Plan summary` block ([`../_shared/plan-summary.md`](../_shared/plan-summary.md)) — rendered immediately before it whenever this session posted or refreshed a plan, and **read-only after it**: answer from what is in hand, write nothing.

## 4. Handoff

Every clean run ends with a single `## Handoff` block — the only bridge to the next session. The
schema, omission rules, and closed-set state-marker vocabulary are owned by
[`../_shared/handoff-format.md`](../_shared/handoff-format.md); the planner's per-outcome shapes,
the `Grounding:` and `**Open questions:**` lines, the footer rule, and the bug-(b) composite
epic+story worked example are in [`references/handoff-renderings.md`](references/handoff-renderings.md).
**Read that reference before composing the handoff** and match the run's outcome to a shape (forward to
the resolver; epic plan → first story or slicer; just-in-time story → resolver; re-route to researcher
or answer-the-question; epic-shaped abort → slicer; DoD heading → drafter; revise refreshed). Fill the snapshot from data in
hand; the `Next:` action and `Why:` line are judgment. The `**Open questions:**` line renders in
**every** shape whose posted plan carries an `## Open questions` section — including a composite
epic+story session — never dropped because the structural shape matched a different example first.
Next-command skills are namespaced `/ctrl-freaq:<name>`. A re-route points `Next:` at another
pipeline skill but does **not** invoke it via the `Skill` tool — the handoff is the only signal; the
user runs the command in a fresh session (session-per-skill is the context-isolation choice).
