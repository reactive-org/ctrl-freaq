# Proposal — `coordinator`: one session that drives an issue through the pipeline

Status: **built in 4.39.0** (#83), as decided (2026-10-19). Two refinements landed in the build:
the card relay is the host's (a stage session's cards, and the text printed beside them, are
mirrored into the coordinator and answerable in either session), so §3.5's "ping the coordinator
with its question" became a card-authoring rule instead — `skills/_shared/asking-the-user.md`
"Relayed cards": under a coordinator brief a card is self-contained. And the coordinator may run
from inside a work worktree; only the close of that worktree has to be run from elsewhere. Its epic
story-phase design (one story in focus at a time, picked by the operator) was superseded in 4.45.0 by
epic #108, whose #115 replaced it with a `stories` stage that drives an epic's stories concurrently
as the epic plan declares; the body below keeps the original design as the historical record. Source: a live run on a consuming repo (Epic #5,
stories #6–#8) where one Claude Desktop session coordinated fresh per-stage sessions. This document
turns that experiment into a skill design.

**Operator decisions (2026-10-19):** D1 amend the invariant · D2 the coordinator composes the
workspace cores · D3 GitHub state is the authority, plus the relayed handoff · D4 inherit effort,
with a per-run override. The order of the next story (§3.1) was settled by epic #108's declared
concurrency.

## 0. What the experiment showed

The coordinating session kept the whole history, chose the next step, and did the quick steps itself.
Every stage that did real work ran in its own fresh session, started in the right worktree. GitHub
was the shared memory. What worked: per-stage context stayed small (the resolver on #6 finished at
about 14% of its window), the review loops caught real defects, and decisions that bound later
stories were recorded in the thread, where the next planner found them.

What caused friction, and what was really causing it:

| Friction | Cause | Fixable by the skill? |
|---|---|---|
| Every session start needed the operator's words | Host rule: `start_session` refuses on a turn that another session's event started. A proposal card (`own_initiative`) can't name a `cwd`, so it can't start a session inside a worktree. | No. Design around it (§3.4). |
| `workspace-close` had to be typed by the operator, and only as a chat message | `disable-model-invocation: true` on both workspace tools | Yes. Decision D2. |
| Updates arrived late when the request for them came late | The brief didn't ask for updates up front | Yes. Fixed brief template (§3.5). |
| A session drifted outside the pipeline (live config tuning) | Nothing pulls the session back to its stage | Partly. The brief scopes the session to one stage (§3.5). |
| Cost: three or four sessions per story | Inherent to session-per-stage | Partly. Effort per stage (D4). |

## 1. Decisions needed first

**D1 — Amend a §12 invariant (a constitution-level change).** `architecture.md` §12 says
"Session-per-skill; no autonomous stage chaining", and `prd.md` §3 says "a skill never starts another
skill's session or crosses the session boundary itself". A coordinator does exactly that. The reason
behind the rule, "context isolation is the design", still holds, because every stage still runs in its
own fresh session. Only the wording blocks it. Proposed rewording:

> A **pipeline stage** never starts another stage's session or crosses the session boundary (no `Skill`
> call to another stage, ever). Only the `coordinator`, an operator-invoked standalone tool, may start
> stage sessions. It starts them only on the operator's word, each in its own fresh session.

If this is rejected, the coordinator can still exist as a **report-only tracker**. It derives the next
command and the folder to run it in, and the operator starts every session by hand. That keeps most of
the value (state derivation, briefs, and pulling the run back on track when it drifts) without the
amendment.

**D2 — Workspace tools: compose their cores, or keep them operator-typed?** Recommendation: compose.
The coordinator's prep calls `prep_workspace_open.py`'s and `prep_workspace_close.py`'s cores
in-process, which is the repo's established "a prep composes executor cores" pattern. Their decision
cards (`BRANCH_IN_USE`, `AMBIGUOUS` dirty/unpushed, `TARGET_IS_SLICE`, …) render in the coordinator
exactly as they would in the tools. `disable-model-invocation` exists so the *model* never opens or
closes a worktree on its own. Under the coordinator the operator still answers a card before every
open and close, so that intent is kept. The coordinator must run from the main checkout. That is
already required: `remove` refuses `cwd_inside_target`.

**D3 — Where "what runs next" comes from.** Recommendation: two inputs, with GitHub as the authority.

- **GitHub state, derived by script** (`prep_coordinator.py`, §3.1), answers *where the issue is*.
  This must be the authority: a session's end note reaches the coordinator cut to 400 characters,
  which is shorter than a `## Handoff` block. Session claims also drift.
- **The stage's own `## Handoff`**, which the brief tells the stage session to send back verbatim
  by `SendMessage` when it exits, answers *what the stage recommends*. This covers re-routes that leave
  no GitHub trace of their own, such as resolver → planner revise (its `Why:` is the only carrier),
  planner → researcher, and planner → slicer.
- When the two disagree, the coordinator shows a card. It never silently trusts either one.

**D4 — Effort and model per stage.** Skills never pin model or effort (CLAUDE.md, removed
2026-08-01). The coordinator passes `effort` to `start_session`, which is a per-run choice, not a skill
pin. Options: (a) inherit everywhere (the default), with the operator able to say "resolver and
evaluator at medium" for a run; (b) a consuming-repo marker block, `<!-- coordinator-stage-effort -->`,
that maps stage to effort. Recommendation: (a) now, and (b) only if repeated runs show the need.

## 2. Classification: a standalone tool, not a stage

- `disable-model-invocation: true`, which makes **seven** tools. It must never auto-trigger on
  "work on #N", which is the resolver's trigger.
- It ends each turn with a **status line plus the next proposed step**, not a `## Handoff`. It is the
  thing that reads handoffs.
- **Environment-gated**, in the same way as the stack rule. When the session-start tools exist (Claude
  Desktop), it starts sessions on the operator's word. When they don't (the CLI), it degrades to the
  report-only tracker from D1's fallback and prints the command plus the folder. One flow, gated on a
  runtime signal. No second playbook.
- It writes nothing to GitHub itself. Every write stays with the stage that owns it.

## 3. Design

### 3.1 `prep_coordinator.py` turns "what runs next" into a fact

This is "facts by script, meaning by model" applied to orchestration. The prep composes existing
cores and derives nothing new:

| Fact | Composed from |
|---|---|
| `target`, `vector.type` (epic / story / standalone / slice / question) | `branching.py` type detection, `gh_gather.py` |
| `plan.present` / `plan.stale` | `gh_gather.py` plan-marker lookup (assembled parts) |
| `prior_pr_row`, PR state (draft / open / merged) | `branching.py` 7-row prior-PR classification |
| `blocked_by` (in-scope-blocked open questions) | `gh_gather.py` native deps |
| `epic.stories` with state, `stories_source` | the `epic-story-hierarchy.md` two-tier read |
| `epic.shipped` | `delivery_log.py` |
| `workspaces` (which branches have a worktree here) | `workspace.list_work_branches` |
| `epic_worktree.behind` | `workspace.py` (see §3.3) |

It outputs a closed **`stage`** set plus the command and folder for it:

| `stage` | Next command | Runs in |
|---|---|---|
| `needs-slicing` (an epic with no stories) | `/github-pipeline:slicer #N` | main checkout |
| `needs-plan` | `/github-pipeline:planner #N` | main (standalone, epic plan) or the epic worktree (story) |
| `needs-workspace` | the composed open (D2) | the coordinator itself |
| `resolving` (no PR, or a draft PR) | `/github-pipeline:resolver #N` | the issue's worktree |
| `pr-ready` | `/github-pipeline:evaluator #PR` | the issue's worktree |
| `merged-needs-close` | the composed close (D2) | the coordinator itself |
| `next-story` | loop back to `needs-plan` for the next story | the epic worktree, after the sync (§3.3) |
| `epic-integration` | `/github-pipeline:resolver #<epic>` | the epic worktree |
| `terminal` | none | none |
| `refused` (question, blocked, slice) | a `needs_decision`, never the loop | none |

The model only writes the brief, relays progress and renders cards.

**Open sub-question: next-story order.** Today the evaluator picks the next story from the epic plan's
`## Story breakdown` order plus the story set, and that pick is judgment. Options: the prep reports
open stories with their native `blocked_by` and the staged epic plan, and the coordinator's model
picks (and says which in one line); or the coordinator takes the evaluator's relayed `Next:` (D3).
Recommendation: take the relayed `Next:`, and fall back to the model's pick only when no handoff came
back.

### 3.2 Routing by target shape (where playbooks are earned)

The shapes differ in the *actions taken*, not only in values, so this is where separate playbooks are
justified:

- **`playbooks/epic.md`**: the epic plan (and slicing if the epic has none) → open the epic worktree
  → per story [sync → plan just-in-time in the epic worktree → open → resolve → evaluate (squash into
  the epic branch, delivery-log entry) → close] → integration [resolver epic route → evaluator →
  close the epic worktree] → terminal.
- **`playbooks/single.md`**: standalone bug / feature / incomplete, plus a **story invoked directly**,
  which is one loop iteration: plan → open → resolve (re-entered on each checkpoint pause, since
  `prior_pr_row` already tells continue from fresh) → evaluate → close. For a story it stops at the
  merge and offers to carry on with the parent epic (native `parent`, else `## Stories` membership
  per 4.33.1). An issue with **deliverable slices** is this shape: the slices are phases on one
  branch, not separate loops.
- **Refused at prep**: a `question` (it points at `question-resolver`), an issue gated
  in-scope-blocked (it names the blocking question), and a **slice** (it offers to coordinate the
  parent).

Re-routes from any stage (soft-reject, revise, researcher or slicer detours) aren't a separate route.
They are the next `stage` the prep derives, informed by the relayed handoff (D3).

### 3.3 One missing script operation: syncing the epic worktree

Before each just-in-time story plan, the epic worktree has to be at the merged tip, or the planner's
grounding refuses a checkout that is behind. In the experiment the coordinator ran raw git for this.
That breaks "no raw git on a work workspace" and repeats the retired `--ff-only` that touched the
operator's checkout. Proposal: add a **`workspace.py sync --work <branch>`** operation that fetches and
fast-forwards only, refuses on dirty or diverged state (`AMBIGUOUS`, like `remove`), and re-runs the
setup hooks. The coordinator's prep composes it at `next-story`.

### 3.4 The start loop, within the host's rules

```
stage session ends ─▶ app wakes the coordinator (side-sessions event)
   ─▶ coordinator re-runs prep, reads the relayed handoff, checks claims against GitHub
   ─▶ reports in one or two lines and ends its turn with a wrap_up button: "Start resolver #8"
operator clicks ─▶ the button sends the operator's words ─▶ start_session(user_asked, cwd=worktree)
```

The coordinator can't start a session on an event turn. A button is the closest thing to one-click
the host allows, and it doubles as the operator's go-ahead for each stage. A standing instruction
("keep going through #9") can't override the host rule. The skill says so plainly, so it doesn't keep
trying.

### 3.5 The stage brief (fixed template)

Every stage session gets:

1. The one slash command, verbatim (`/github-pipeline:<stage> #N`), and the absolute folder it runs in.
2. Background a fresh session needs: the target, the stage this is, what earlier stages decided, and
   any operator decisions that bind this stage.
3. **Reporting, from the start**: send milestone updates to the coordinator by `SendMessage`, and at
   exit send the full `## Handoff` block verbatim. When a decision card shows, ping the coordinator
   with its question. (The operator may also answer the card in the stage session directly.)
4. **Scope**: run this stage only. If the operator asks for something outside it, do it if asked, but
   say in the update that it's outside the pipeline record.

The pipeline skills stay unaware of the coordinator. Everything above lives in the brief, not in
their prompts.

### 3.6 Invariants

- It starts a stage session only on the operator's word in its own chat, never on an event turn.
- GitHub is the authority for state. The coordinator checks claims that matter (merge SHA, issue
  state, PR state) by re-running prep, never by trusting a note.
- It holds nothing that exists only in chat. Anything a later stage needs must already be on
  GitHub, or the coordinator asks the stage that owns it to record it.
- One stage session at a time per issue, and at most one writer per worktree.
- Every decision card a composed core raises renders with its brief, exactly as the owning tool's
  would.

## 4. Scope receipt (files a build would touch)

- `skills/coordinator/SKILL.md` (router), `playbooks/epic.md`, `playbooks/single.md`,
  `references/stage-brief.md`
- `scripts/prep_coordinator.py`, and `scripts/workspace.py` (`sync`)
- `tests/test_coordinator_routing.py`, `tests/test_prep_coordinator.py`, `sync` tests in the
  workspace suite
- `docs/prd.md` §2 / §3 / §6, `docs/architecture.md` §12 (D1) and §6 (`sync`)
- `CLAUDE.md` (thirteen skills → fourteen; six `disable-model-invocation` tools → seven),
  `.claude-plugin/plugin.json` (description + version), `README.md`
- Validator 1 gains `github-pipeline:coordinator`. Validator 4 must see no `gh` writes in its fences.

## 5. Out of scope

- Config drift outside the repo (the experiment's `~/inbox-sorter.toml`). The brief can flag it, but
  only the operator can fix it.
- Running stage sessions in parallel (two stories at once). Just-in-time planning against the merged
  epic tip makes stories serial by design.
- Starting from an unfiled idea. The coordinator starts from a filed issue; the drafter stays manual.
- Cost beyond the per-stage effort choice (D4).
