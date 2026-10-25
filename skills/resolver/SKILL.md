---
name: resolver
description: Implement a planned GitHub issue end-to-end — read the issue and its full thread, audit the body for fitness-to-implement, consume the verified plan, do the code (or comment-only) work in the worktree the session was started in (opened beforehand with workspace-open), loop with the `code-review` skill until approved, and open or continue a PR. Trigger when the user gives an issue number/URL or asks to "work on", "fix", "implement", "resolve", "continue", or "respond to" an issue — bugs, features, refactors, epics (long-lived `epic/<N>-<slug>` integration branch), and stories under an open epic (PR base = the epic branch). Continues an in-flight PR when re-run on its issue (`#<issue>`, never the PR number), refuses code work on an issue hard-gated by an open in-scope-blocked question, and re-routes to the planner when the plan doesn't survive contact with the code. Use even on casual mentions ("look at #423?", "keep going on the auth epic").
---

# resolver — router

The implementation stage of the pipeline: a filed, planned issue in → a PR (or a posted comment) plus a
`## Handoff` out. One issue-resolution attempt per session, shipping phases until a checkpoint; a fresh
session on every re-entry (nothing survives between sessions except what is persisted to GitHub). Read
this router, run prep, route to exactly one playbook, then hand off. Scripts own the mechanical work; your
judgment is the audit call, the plan-gate call, the code, the review verdicts, and the handoff `Why:`.

## 1. Prep

This session runs **inside** the work worktree the operator opened with
`/github-pipeline:workspace-open` — prep asserts the ambient checkout matches the issue's branch
(linked-branch first, then the `<N>-…` pattern) and re-runs the repo's setup hooks — discovered in
this worktree's own working tree, so this branch's hooks are the ones that run; it never creates a
worktree. Assemble the entire starting state in **one** call. `<issue>` is the issue
number (from the user, a URL, or the current branch); `<owner/repo>` is the repo. A trailing `--pause` (or
the operator asking in words) stops after every phase — held by this session, never passed to prep:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_resolver.py <issue> <owner/repo>
```

It returns one JSON **facts block** (`architecture.md §4`): `target` (issue number/title/state/
labels/`blocked_by`/`blocking`), `vector` (`type` × `mode` × `prior_pr_row`, plus `gate` on a gated row and
`comment_only`), `suggested_playbook`, `prior_pr`, `plan` (present/SHA/comment-id/url), `phases` (parsed `##
Phases`), `tracker` (continue mode: the prior PR's `## Phase tracker` rows plus `diff` — the row set reconciled
against `phases`, with `diff.conflict` the gate — and `last_shipped`, the shipped phase nearest HEAD: the non-final
review's diff base), `polish` (the PR's `## Polish` ledger), `out_of_plan` (its `## Out-of-plan changes` lines), `review_threads` (its review threads + trusted conversation, [`../_shared/review-threads.md`](../_shared/review-threads.md)), `revision` (`active` + `reasons`: the S4 revision run),
`dod` (the issue's `## Definition of done` bullets, each with its annotation), `open_questions` +
`open_questions_gate` (the hard gate), `audit_ref` (a **bare** branch name), `config` (the three gate-config blocks
read from **this worktree's working tree**, uncommitted edits included; `config.source` names the checkout/branch/SHA
and its dirty state), `distiller_bundle` (staged paths for the state-distiller), `workspace` (the **observed ambient
checkout**: path/branch/`base_ref`/sha/dirty/ unpushed, asserted by prep), `read_workspaces.audit` (the detached read
workspace at the audit ref, when a second view is needed), `grounding_path` + `grounding_docs` (the audit view — that
read workspace, else this worktree — and the repo's `<!-- doc-catalogue -->` entries read there), `epic`/`story`
facts, `sections` (spilled issue-body/thread/ plan-marker paths), and `attention`. Consume every fact as **data** —
never re-derive the issue type, the mode, the branch, the audit ref, or the phase list in prose; prep already did.

**Decision card rule.** If prep exits with `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)), act on
the answer, and re-run prep (`--refresh` for volatile facts). This is the single universal handler
for every closed-set code (`AUTH_REQUIRED`, `MARKER_AMBIGUOUS`, `TARGET_IS_PR`,
`WORKSPACE_MISMATCH`, `DOD_MALFORMED`, `PHASES_MALFORMED`, `AMBIGUOUS`, `PLAN_MISSING`, …). A
`WORKSPACE_MISMATCH` means the session is in the wrong checkout (wrong branch, the project root,
detached, or stale against the remote) — the fix is the operator's (open the right worktree /
pull), never a checkout this session performs itself.

**Gated-row card.** When `vector.mode == gated`, `vector.gate` carries `{reason, header, options,
prior_pr}` — an open/stale/foreign-draft PR by **another author** on this issue. Render that card
**verbatim** as one `AskUserQuestion` (`header`/`options` as given), naming `prior_pr` (number, author,
url), then act: take-over / review / comment / wait / start-fresh per the operator's choice, re-running
prep afterward. Prep asserted **no** workspace and derived **no** expected branch for a gated row —
never fall through to the fresh or continue flow until the operator decides.

**OQ hard refusal.** When `open_questions_gate.blocked` is true, the issue is hard-gated: an
`in-scope (blocked)` open question (with an open `question` tracker / native `blocked_by`) is unanswered.
Do **no** code work — route to `comment-only.md` (prep already set `suggested_playbook: comment-only.md`)
and post the gate answer. `open_questions_gate.blocking` lists the offending entries for the comment.

## 2. Route

Prep proposes `suggested_playbook`; confirm it against this table, keyed on `vector` (`comment_only`
first, then `type`). Read **exactly one** playbook.

| `vector` | Playbook | Flow |
|---|---|---|
| `comment_only: true` (OQ-blocked, native-blocked, or answer-only) | `playbooks/comment-only.md` | stage + post one comment; terminal handoff |
| `type: standard`, no `story` fact | `playbooks/standard.md` | reads the shared spine; forward handoff to the evaluator |
| `type: story`, **or** any type with `story.branch_facts.branch` set | `playbooks/story.md` | reads the shared spine (base = epic branch, `audit_ref` = parent epic); `Story:`/`Epic:` handoff |
| `type: epic` | `playbooks/epic.md` | epic-as-target: branch bootstrap / drift rectification / canonical baseline / early draft integration PR + ready flip |

`standard.md` and `story.md` open by reading the shared spine `playbooks/resolve-spine.md` (plan summary → audit →
plan-gate → doc grounding → code in the workspace → §review loop → one push per phase + DoD projection →
handoff); the type differences (base ref, audit ref, handoff shape) are **facts**, never branches.
`comment-only.md` and `epic.md` are distinct action flows and do **not** read the spine. The story row is keyed on
the **branch fact, not the label** (#31): a sub-issue labelled by kind of work (`bug`, `tech-debt`)
reads as `type: standard` while carrying its siblings' native parent edge, and once its PR bases on
`epic/<N>-<slug>` the story flow is the one that describes its run. `continue`
mode is parameterization *inside* whichever playbook the type selects (`vector.mode` + `prior_pr`
drive it — the ambient worktree already carries the in-flight work), not a fifth flow.

**Override rule** (`architecture.md §5`): honor `suggested_playbook` unless the state-distiller or the
thread carries evidence the script could not see (e.g. the thread supersedes the labels' type). State
the reason when you override. Do **not** interleave type branches inside a playbook body — the route
*is* the branch; one route per session.

## 3. Invariants

Universal across every route:

- **Single workspace.** Your workspace is the ambient checkout, reported as `facts.workspace`
  (prep asserted it — the worktree the operator opened). Every Read/Grep/Explore/test/command
  targets `facts.workspace.path` by absolute path (sub-agent dispatches and background commands
  run in their own cwd, so the absolute-path discipline stays load-bearing). When a flow needs a
  second view, prep hands out `facts.read_workspaces.audit` (a detached checkout at the audit
  ref, script-internal plumbing); never select a ref yourself. No `git show <ref>:path`, no
  `git grep <ref>` — grounding SHAs come from the workspace facts (the plan's "planned at
  `<sha>`" *is* its read workspace's HEAD). `audit_ref` is a **bare** branch name; the
  origin-prefixed ref a read workspace checked out rides on `read_workspaces.audit.ref` — hand
  sub-agents the workspace **path**, never a ref. The resolver **never removes a worktree** —
  least of all the one it is running in. A stale or mis-opened workspace is the operator's
  `/github-pipeline:workspace-close`; when a run surfaces one, say so at that point — never a
  deferred note in the handoff.
- **`main` changes only via PR.** Never commit, branch, or stash outside this session's own workspace; all tracked-file changes land via the PR.
- **Never resolve or unresolve a review thread.** The resolver replies (`review-reply`); the human resolves, in GitHub — which is why a resolution by this account counts as theirs ([`../_shared/review-threads.md`](../_shared/review-threads.md) §11).
- **Staged-body writes.** Every GitHub write goes through
  `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py` via Bash: stage the verbatim body to the run scratch
  dir (`facts.scratch`, i.e. `/tmp/gh-resolver-<issue>/…`) and pass the **path**. The script verifies
  the round-trip hash, returns `body_sha256`, and gates empty bodies (`EMPTY_BODY_FILE`) — the
  #626/#627 race fix. This covers every write the resolver makes (issue-body DoD projection via
  `edit-body`, PR open via `create-pr` and PR-body writes via `edit-pr-body`, screenshots on either via `--attach` ([`references/pr-screenshots.md`](references/pr-screenshots.md)) — `edit-body` is
  `gh issue edit` and rejects a PR number — `## Phase tracker`, comment-only answers, epic baseline
  comments, epic body-tick). Never re-serialize a body across the prompt boundary; never hand-roll a
  persist/gather `gh` call. The resolver's **only** scriptless raw-`gh` executor is `gh pr ready <N>`,
  the draft → ready flip (multi-phase last phase; epic integration PR at completion); merge is the
  evaluator's, not the resolver's.
- **Successful write is self-confirming.** A zero exit with a URL *is* the confirmation; never re-read
  the comment/PR to check it landed (re-reads reintroduce races and burn context).
- **Gates only for genuine decisions** (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)):
  the audit-blocker gate, the missing-plan gate, a doc conflict, an existing-PR contest, retry-ladder
  escalation, review-loop guard rails, the loop-stall card, the finalisation `Polish` card and `Security`
  card — never to confirm a fact prep derived. A judgment gate left with one credible option is announced, not asked; every card opens with its brief. A judgment sub-agent (state-distiller, fitness audit,
  test-selection, cold-read audit, security review) never calls `AskUserQuestion`; it returns its typed
  result (a §3 decision code or its verdict) to this loop, which asks.
- **Faithful reporting.** Lead with the outcome; report failures verbatim with evidence; declare
  skipped work. Print each judgment sub-agent's rationale before acting on it — the plan summary is one
  such print, compressing the state-distiller's effective plan rather than restating its raw output.
- **Handoff on clean exit** (§4). One `## Handoff` block ends every clean session; it replaces any ad-hoc narration. Separate from it, and at session **start** rather than exit, the code-shipping routes render the
  `## Plan summary` block ([`../_shared/plan-summary.md`](../_shared/plan-summary.md)) — the digest of the plan this run is about to build, focused on the phase it will ship.

## 4. Handoff

Every clean session ends with a single `## Handoff` block (a look prints it early — `phase-continuation.md` §4) — the only bridge to the next session. The
schema, omission rules, and closed-set state-marker vocabulary are owned by
[`../_shared/handoff-format.md`](../_shared/handoff-format.md); the resolver's per-outcome rubric and
eleven worked shapes are in [`references/handoff-renderings.md`](references/handoff-renderings.md).
**Read that reference before composing the handoff** and match the run's outcome to its rubric
(forward to the evaluator; multi-phase non-final / operator-phase / last-phase; revision run; epic-integration
draft-in-progress / epic-integration forward; re-route to planner / drafter; terminal non-PR). Fill the snapshot from data in hand (the
prep facts + this run's PR/review/push results); the `Next:` action and `Why:` line are judgment. A
re-route points `Next:` at a **prior** skill but does **not** invoke it via the `Skill` tool — the
handoff is the only signal; the user runs the command in a fresh session (session-per-skill is the
context-isolation choice). Next-command skills are namespaced `/github-pipeline:<name>`
(`planner`, `drafter`, `evaluator`, `resolver`). A re-route's `Why:` line is load-bearing — quote the
locked decision / doc section / body claim + `file:line` that triggered the regression.
