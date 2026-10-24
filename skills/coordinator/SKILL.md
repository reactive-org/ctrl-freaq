---
name: coordinator
disable-model-invocation: true
description: Drive one filed GitHub issue — or a whole Epic and its stories — through the pipeline stages from a single session. It works out where the issue stands from GitHub and the local worktrees, writes each stage's brief, and starts every stage in its own fresh session in the right worktree when the operator says go; it opens, syncs and closes worktrees as routine, announced steps whose safety cards still render, puts everything that waits on the operator on a card or a one-click button, and checks every stage's claims against GitHub. Explicit-invocation only — run it as `/github-pipeline:coordinator <issue>` from the project root. Not a pipeline stage; it never plans, resolves or evaluates anything itself, and writes nothing to GitHub.
---

# coordinator — router

One session keeps the run's history while every stage keeps a clean context: plan → open → resolve →
evaluate → close, each stage in **its own fresh session**, started in the folder it must run in.
GitHub is the shared memory; this session only reads it, briefs the stages, and asks the operator.
Run it from the project root — from inside a work worktree it can't close that worktree at the end.

## 1. Prep

Resolve the repo (`gh repo view --json nameWithOwner -q '.nameWithOwner'`), then run one call **at
the start of every turn** — the first, every wake, and every turn acting on the operator's answer:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_coordinator.py <issue> <owner/repo> [--root <path>] [--story <M>] [--open <N> | --close <branch> | --sync]
```

Read-only by default. An action flag runs a routine workspace step (§3), and the call then reports
the action's `receipt` and the stage after it. Facts: `stage`, `next` (`kind`: `session` | `open` |
`close` | `pick` | `none`, plus `skill` / `command` / `folder` / `issue` / `branch` / `sync_first`),
`focus`, `target`, `vector.type`, `suggested_playbook`, `unit` (the focus issue's `plan_present` /
`worktree` / `prior_pr_row` / `pr`, with `pr.phases` on a draft), `epic` (`stories`,
`stories_source`, `shipped`, `worktree`, `behind`, `integration`, `candidates`), `parent_epic`,
`refusal`, `gate`, `routes` (skill → absolute folder), `root` (`branch` / `at_default_tip` /
`dirty`), `invoked_from`, `action`, `attention`, `notices`. Consume them as **data** — never
re-derive a stage from a session's note.

**Decision card rule.** If prep exits with `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)), act on
the answer, and re-run prep. A decision from an action flag is the **workspace tool's own card**
(`BRANCH_IN_USE`, `TARGET_IS_SLICE`, `AMBIGUOUS` dirty/unpushed/diverged, `WORKSPACE_MISMATCH`
`cwd_inside_target`, …) — render it exactly as `workspace-open` / `workspace-close` would. A
`cwd_inside_target` close becomes a `Your step` card (§3) carrying the exact
`/github-pipeline:workspace-close <branch>` command to run from the project root.

## 2. Route

Every turn reads the turn-loop spine, [`playbooks/coordinate-spine.md`](playbooks/coordinate-spine.md),
plus the one playbook for the target's shape:

| Facts | Playbook |
|---|---|
| `suggested_playbook: epic.md` (`vector.type: epic`) | [`playbooks/epic.md`](playbooks/epic.md) |
| `suggested_playbook: single.md` (standalone, story, an issue with slices) | [`playbooks/single.md`](playbooks/single.md) |

The closed `stage` set, and what each asks for:

| `stage` | Next |
|---|---|
| `refused` | a card saying why (`refusal.reason`: `question` → `/github-pipeline:question-resolver`; `slice` → offer to coordinate the parent; `oq-blocked` → name the blocking question) — the loop never starts |
| `gated` | the `gate` card verbatim (someone else's PR claims the issue) |
| `needs-slicing` | start the slicer (an epic with no stories) |
| `needs-plan` | start the planner — a story's in the epic worktree, after the sync when `next.sync_first` |
| `needs-workspace` | the open, folded into the next stage's go-ahead → `--open <next.issue>` |
| `resolving` | start the resolver (no PR yet, or a draft: a checkpoint pause or a revision) |
| `pr-ready` | start the evaluator on `next.command`'s PR |
| `merged-needs-close` | routine close, announced, once no stage runs in it → `--close <next.branch>` |
| `next-story` | pick the next story (`epic.candidates`), then re-run prep with `--story` |
| `epic-integration` | start the resolver's epic route in the epic worktree |
| `terminal` | the summary — nothing left to run |

## 3. Invariants

- **A stage session starts only on the operator's word, in this chat.** End the turn offering the
  start as a one-click button whose message carries the command **and** the absolute folder ("Start
  resolver #152 in /…/.worktrees/152-sort-by-date") — or, when an open comes first, the open that
  creates the folder (spine §4); the click is the go-ahead. Never start one on a turn a stage
  session's end woke you for — the host refuses it, and a standing instruction ("keep going through
  #155") can't stand in for the click. Say so plainly if the operator asks for autopilot.
- **Environment-gated.** Where a session-start tool is available, start the stage fresh in
  `next.folder` (that folder as it is, never a new worktree) with the stage brief
  ([`references/stage-brief.md`](references/stage-brief.md); its `<<contract_path>>` is
  `${CLAUDE_PLUGIN_ROOT}/skills/_shared/asking-the-user.md`), effort inherited unless the operator
  set one for this run. Where none is, the command and the folder go on a `Run stage` card in
  place of that button (spine §5); the rest of the flow is identical.
- **Routine workspace steps.** Open, close and the epic sync run without a consent card of their
  own, each announced in one line. A close runs on any turn where no stage session this run started
  is still running in that worktree — a turn acting on the operator's answer, or the wake carrying
  that stage's end — never on a milestone-update wake (a run's first turn: spine §4). An open or a
  sync runs only on the click of the stage go-ahead naming it. A decision the core raises
  (`BRANCH_IN_USE`; dirty, unpushed or diverged state; `cwd_inside_target`) still renders per §1.
- **Everything that waits on the operator is a card or a one-click button.** A decision is a card, a
  stage start a button, an announced choice an announced line. A task only the operator can do is a
  `Your step` card (`header: "Your step"`), its message printing the exact command and folder:
  `Done` (re-run prep) or the honest alternative — `Leave it open` for a close left to them (the
  summary names it), `Start anyway` for a project root off the default tip, `Leave it` (relay once)
  for another `attention` line asking them to act, spine §5's pair for a failed setup hook. An
  `AskUserQuestion` answer comes back inside the same turn, and the host refuses `start_session` on
  a turn a stage's message woke: there, a start, or a card answer leading straight to one, ends the
  turn with `wrap_up`'s reply button, never a card answer.
- **GitHub is the authority.** Re-run prep on every wake, and check the claims that matter — merge
  (`pr.merged`, the issue `closed`), PR state, issue state — against it. A relayed `## Handoff` that
  disagrees with `stage` is a card, never a silent pick (spine §3).
- **Gates only for genuine decisions** (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)): a
  handoff that disagrees with GitHub is a card; picking the next story, or a reconciliation the
  evidence settles, is announced in one line. A judgment gate left with one credible option is
  announced, not asked; every card opens with its brief.
- **Holds nothing only in chat.** Anything a later stage needs must already be on GitHub, or the
  coordinator asks the stage that owns it to record it there.
- **One stage session at a time per issue**; the coordinator writes no tracked file, runs no `git`
  on a work workspace, makes no GitHub write of its own, and never answers a stage's mirrored card.
- **Faithful reporting.** Say which stage ran, what it claimed, what GitHub shows, and every
  `attention` line — in one or two lines per wake; one asking the operator to act renders per the
  operator-task rule once no stage session of this run runs for that issue.

## 4. Summary — not a `## Handoff`

This tool reads handoffs; it never writes one. End every turn with a **status line** — `<target> ·
<stage> · <one-line state>` (e.g. `Epic #150 · story #152 resolving · phase 2 of 3`) — then the
**next proposed step**: the button or the card the operator-task rule names, or, at `terminal`, a
summary of what shipped (stories, PRs, merges) and that every worktree is released.
