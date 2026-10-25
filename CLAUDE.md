# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is a **Claude Code plugin** (`github-pipeline`), not an application. There is no compiled
artifact and no package manager. The "source" is:

- **Skill prompts** — `skills/<name>/SKILL.md` (a thin router) plus `playbooks/` (one file per
  behaviorally distinct flow, read on demand) and `references/` (judgment sub-agent prompts +
  contract renderings).
- **Stdlib-only Python scripts** — `scripts/*.py`: the four GitHub/git executors (`gh_gather.py`,
  `gh_pr_gather.py`, `gh_persist.py`, `config_block.py`), `workspace.py` (worktree mechanics:
  ensure/attach/remove/sync/gc/lint, plus the derived-default-branch helper) and `branching.py` (branch
  naming / type detection / prior-PR rows / linked branches — import-only), `parse.py` (DoD /
  open-question-links / phases), the thirteen `prep_*.py`
  state-assembly scripts (including `prep_workspace_open.py` / `prep_workspace_close.py`, whose
  prep IS the tool's action, and `prep_coordinator.py`, which composes their pure cores), the `oq_tracker.py`, `doc_catalogue.py` and `story_set.py` helpers the preps compose
  (the open-question tracker search and the open-question gate the resolver and coordinator share;
  the consuming repo's declared grounding docs; an epic's two-tier story set), `plan_parts.py`
  (one plan, three comments: the planner's `split`, every reader's assembly), `plan_shipped.py`
  (the legacy shipped-phase records' read model + the planner's fold-back `check`), and
  `scripts/pipelib/` (envelope, spill, decision codes, platform limits, hashing, the locked-down subprocess
  runner, the hook runner, the thread read-back).
- **An offline test harness** — `tests/` (stdlib `unittest`, a fixture-replaying `gh` shim, a git
  sandbox); `python3 tests/run.py` is the one command.
- **Shared contracts** — `skills/_shared/*.md`.
- **Plugin manifests** — `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
- **The design docs** — `docs/prd.md` (what/why), `docs/architecture.md` (how; §12 is the
  non-deviable invariant registry), `docs/implementation.md` (the v1→v2 migration), and
  `docs/specs/` (the frozen v1 behavior specs + the parity records).

Editing this repo means editing prompts and Python. The README is the user-facing overview; this
file is for someone *modifying the plugin*.

Runtime dependencies: `python3` (≥ 3.9, **stdlib only** — no third-party packages, no venv, no pip
step), `git` (≥ 2.38), and `gh` (≥ 2.40, authenticated — `gh auth status`; `--attach` needs ≥ 2.99
and is gated). Nothing else. Scripts
are executables (`#!/usr/bin/env python3`) invoked by absolute path via
`${CLAUDE_PLUGIN_ROOT}/scripts/...`.

## Commands

There is no build step. Validation is the offline suite plus a set of prompt-side greps
(prose has no compiler — the greps are the compiler):

```bash
python3 -m compileall -q scripts/                       # every script parses
python3 tests/run.py                                    # the whole offline suite; must exit 0
python3 -m json.tool .claude-plugin/plugin.json         # manifests parse
python3 -m json.tool .claude-plugin/marketplace.json

# One skill's gates, or one script's behavior, without the full suite:
python3 -m unittest tests.test_resolver_routing tests.test_v1_retirement
```

The suite must pass on **both macOS and Linux** (architecture.md §9.6); the container invocation is
documented in `tests/README.md`. Live behavior is exercised by the parity scenarios in
`docs/specs/parity/<skill>.md` against the sandbox repo recorded in `tests/SANDBOX.md` — never by
an offline test, which is hermetic (no network, no live repo).

CI (`.github/workflows/ci.yml`) runs the mechanical part on every PR and push to `main`: compileall,
the two manifest parses, validator 6, and the suite on Linux and macOS. Validators 1, 3, 4 and 5
stay manual — the census needs a baseline, 3 and 4 hit descriptive prose (their fence-scoped
routing tests run in the suite), and 5 needs judgment.

The prompt-side validators are listed under "Validators" at the end of this file. Run them after
any prompt edit; they are cheap and they are the only regression net prose has.

## Architecture

### Fourteen skills: six pipeline stages, eight standalone tools

```
draft ──▶ research ──▶ slice ──▶ plan ──▶ resolve ──▶ evaluate
```

The **pipeline stages** are `drafter`, `researcher`, `slicer`, `planner`, `resolver`, `evaluator`; the
**standalone tools** are `setup`, `question-sweep`, `question-resolver`, `doc-reviewer`,
`requirements-gatherer`, `workspace-open`, `workspace-close`, `coordinator` ([prd.md §2](docs/prd.md)). The two workspace tools are the
v3 operator-owned lifecycle: the operator runs `workspace-open <issue>` between planning and
resolving (linked branch + worktree + setup hooks), **starts the resolver and evaluator sessions
inside that worktree** (their preps assert the checkout — `WORKSPACE_MISMATCH` on the wrong one —
and re-run the setup hooks each entry), and releases it with `workspace-close` after the merge
(the evaluator's terminal handoff hands the exact command; nothing removes a worktree
automatically).

The **`coordinator`** (#83) drives that whole sequence from one session, from the project root: its
`prep_coordinator.py` derives the issue's `stage` (a closed set — `derive_stage` is a pure function,
so the offline suite pins every row) from GitHub state and the local worktree list, never from a
session's note, and names the next command and the absolute folder it runs in. It starts each stage
in its **own fresh session** in that folder, only on the operator's word in its own chat (a host
rule: a session start is refused on a turn another session's event started, so every start is the
operator's click); where the host has no session-start tool it prints the command and folder
instead. It opens, syncs and closes worktrees by composing the workspace tools' pure cores
(`prep_workspace_open.build_open` / `prep_workspace_close.build_close` / `workspace.py sync --work`),
each only after the operator answered its consent card — the cores act immediately, so state
derivation never calls them. A stage's relayed `## Handoff` that disagrees with the derived stage is
a card; the relay carries re-routes that leave no GitHub trace (resolver → planner, planner →
researcher/slicer). The pipeline skills stay unaware of it: everything a stage must do differently
lives in the brief it is started with (`skills/coordinator/references/stage-brief.md`) and in
`asking-the-user.md`'s "Relayed cards".

**That is the conceptual order, not the handoff topology:** the drafter forwards to the
**planner**, and `research` is a conditional detour off `plan` — the planner re-routes to the
researcher only when the issue has no dossier and the plan turns on external truth with genuine
currency risk, and the researcher hands **back to the planner**, which ingests the dossier (a
declined research run hands back too, with `research: ✗`). The planner also re-routes **backward to
the slicer** when its seam gate finds a standard issue epic-shaped (most seams outside the issue's
DoD): it aborts with a lean seam-analysis comment and the slicer promotes #N into an Epic in place and
cuts its stories (`skills/planner/references/seam-dispositions.md`). That arm pointed at the drafter
until #16 moved decomposition to the slicer at both altitudes; before v2 it was the drafter's, and the
S15 parity record's Scenario 4(b) preserves that older topology.

`slice` is the **second** conditional detour off `plan`, and the planner's seam gate picks between two
off-ramps on one criterion — the independence bar the seams clear. **Both land on the slicer** (#16):
it is one operation at two altitudes, so the gate picks the altitude, not the skill. Seams
*shippable*-independent (each wants its own branch and PR) → the slicer promotes #N to an Epic and cuts
**stories**; two or more milestones only *demonstrable*-independent (sharing one branch) → the gate
**recommends planning the issue multi-phase** and offers slicing beside it, for an operator who wants
each milestone tracked on the board as a **deliverable slice** carrying its own acceptance criteria —
the only things a slice buys over a phase. Either way it hands **back** to the planner, whose phases
then map onto the children via `sub-issue:` (or, when the slicer finds one milestone and files nothing —
its *no cut* exit — plan the issue multi-phase), and it is also operator-invocable directly. A slice is
a **milestone, not an increment**: one slice per increment re-files the plan's phase list as issues,
which is the over-slicing `slicing-method.md` §4.1 exists to stop. The off-ramps stay
asymmetric in what the planner posts: the promoting one leaves a lean seam-analysis comment (a promotion
argues from the seam inventory), the slice-in-place one posts **nothing** — that cut re-derives from the
repo's declared grounding docs, so a planner-authored proposal would be a competing, staler
decomposition. The stage/tool split is load-bearing, not cosmetic:

- A pipeline stage runs in **its own Claude Code session** and ends with a `## Handoff` — a
  cold-readable summary plus the copy-pasteable command that starts the next session. There is no
  shared runtime state; the handoff and the GitHub artifacts are the only bridge. Re-routes (e.g.
  resolver → planner when the plan didn't survive contact with the code) point the handoff at a
  *prior* stage but **never** cross the session boundary by calling the `Skill` tool —
  session-per-skill is the deliberate context-isolation choice, and the handoff's `Why:` line is
  the load-bearing carrier of *why* the regression happened. Only the operator-invoked
  `coordinator` may start a stage session, on the operator's word, each in its own fresh session
  (architecture.md §12; a stage never does).
- A standalone tool runs only on explicit invocation, is report-then-apply (nothing changes without
  the operator seeing the proposal), and ends with a **plain summary**, not a handoff. Tools that
  edit tracked files (`setup`, `question-sweep`, `doc-reviewer`) stage edits in a workspace and
  **offer** the landing (commit + push + PR) as one final gate; on decline they perform zero git
  actions and print the workspace path plus the ready-to-run commands ([prd.md §8.2](docs/prd.md)).
- The split also scopes the [prd.md §10](docs/prd.md) prompt-economy metric: "router + the one
  playbook ≤ half the v1 `SKILL.md` line count" is a **pipeline-stage** bar. The standalone tools
  are measured and recorded, never force-trimmed — the leanest v1 tools had no fat to cut and v2
  added the §8.2 landing gate, so a tool legitimately exceeds half (the S19 doc-reviewer ruling:
  record the metric, never trim a tool to hit it).

`skills/_shared/handoff-format.md` is the single source of truth for the handoff schema, the
omission rules, and the **closed-set state-marker vocabulary** (don't invent synonyms for
`open`/`closed`, `APPROVE`/`COMMENT`, `squash`/`merge`). Per-skill renderings live in each skill's
`references/handoff-renderings.md`; the schema lives only in `_shared`.

### One session = router + prep + one playbook

Every `SKILL.md` is a router with the same four sections, in order
([architecture.md §9](docs/architecture.md)):

1. **Prep** — the one prep-script invocation, plus the universal `needs_decision` rule (or an
   explicit no-prep note where a skill genuinely has nothing to gather — currently only
   `doc-reviewer`, where the operator names the doc).
2. **Route** — the visible `vector → playbooks/<file>` table and the override rule.
3. **Invariants** — the rules that hold for every route (single workspace, staged-body writes,
   gates only for genuine decisions, faithful reporting, handoff on clean exit).
4. **Handoff** (pipeline) or **Summary** (standalone tools).

The governing rule is **facts by script, meaning by model**. A step whose inputs and correct output
are fully defined — fetching, parsing, state derivation, ref selection, branch naming, hook
execution, writing — is a script. The model classifies, drafts, plans, reviews, and judges. If a
needed operation has no script, **extend a script**; never inline the operation in a prompt. There
is no intermediary agent between a skill and its scripts (v1's mechanical executor sub-agent, which
lived under `agents/`, was retired at S20 — its eight rules landed on the scripts and the router,
per [architecture.md §7](docs/architecture.md)'s mapping table).

- **The facts block.** `prep_<skill>.py` assembles the session's entire starting state in **one
  call** and emits it as the envelope payload ([prd.md §9.2](docs/prd.md)): repo, scratch dir, root
  SHA, target, the state `vector`, `suggested_playbook`, workspaces, pinned config, parsed DoD,
  native `blocked_by`, `attention`, `notices`. Preps compose the executor cores **in-process**
  (module imports, not a subprocess chain). Values the flow needs are **facts**, so playbooks
  consume them as data instead of re-deriving them.
- **The envelope** ([architecture.md §3](docs/architecture.md)). Every script emits exactly one
  JSON envelope on stdout. `status` is `ok` or `needs_decision`; `needs_decision` is a *valid
  outcome*, not an error — the router's single universal rule is to render `decision` as one
  `AskUserQuestion` card with its brief and act on the answer. Exit `0` = envelope present, `2` = usage error, any
  other non-zero = hard failure (stderr carries it, no envelope). The **closed decision-code set**
  lives in §3 and spans scripts *and* judgment sub-agents (it supersedes v1's
  `subagent-decision-signal.md`); adding a code is a contract change — amend §3 and the router rule
  together. Non-blocking degradations ride in `notices` (e.g. `DEPS_UNSUPPORTED` when native issue
  dependencies are unavailable and prose links are the fallback).
- **Spill routing** (~25 KB). Any verbatim section (body, thread, marker comment, diff) is inline
  when small and written to the session scratch dir when large; each reports `*_bytes` +
  `*_mode: inline|path` (+ `*_path`). Diffs and line-comment sets are always `path`. Threshold:
  `GH_PIPELINE_INLINE_THRESHOLD_BYTES` (legacy `GH_OPS_INLINE_THRESHOLD_BYTES` still honored).
  This is what keeps a 130 KB diff out of the session's context.
- **Judgment sub-agents** ([architecture.md §8](docs/architecture.md)) isolate reasoning that would
  otherwise pollute the main loop: the resolver's state-distiller (thread + plan *text*) and
  fitness audit (*code and docs* at a pinned ref), the planner's plan reviewer, the drafter's issue
  reviewer, the researcher's dossier validator, the test-selection sub-agent, the cold-read audit, the
  resolver's fix design (a fix round's seam re-derivation, before the edit), and the question-status
  reader. All are context-blind, receive **workspace paths and prep-staged
  files** (never refs), cannot call `AskUserQuestion` (they return a §3 decision code instead), and
  never write to GitHub.

### The scripts encode the cross-skill contract

- `gh_gather.py` — the fixed issue-fetch envelope: body + full thread + labels + marker-comment
  lookup in one round-trip, with spill routing. Surfaces GitHub's **native issue dependencies**
  (`blocked_by` / `blocking` + a `deps_available` flag) and the **native parent/sub-issue relation**
  (`parent` / `sub_issues` / `sub_issues_summary` + a `subissues_available` flag). Both are
  capability-gated and gate **independently** — one ladder descends per-relation, so a host serving
  one but not the other still yields the one it serves, and the emitted notice
  (`DEPS_UNSUPPORTED` / `SUBISSUES_UNSUPPORTED`) names which fallback applies: prose linking for
  dependencies, the legacy `## Stories` checklist for epic hierarchy.
- `gh_pr_gather.py` — the PR-fetch envelope, with optional `--with-diff` / `--with-line-comments`
  (always spilled to disk) and the PR's own `labels`.
- `gh_persist.py` — the single write path (`create` / `edit-body` / `edit-pr-body` / `edit-title` /
  `edit-pr-title` / `edit-labels` / `link` / `comment` / `edit-comment` / `close` / `reopen` /
  `create-pr` /
  `close-pr`; `edit-body` is `gh issue edit` and rejects a PR number, which is why the PR-body write
  — and, for the same reason, the PR-title write — is its own op. `edit-title`/`edit-pr-title` are
  the only ops that retitle an **already-filed** issue/PR; they are bodyless, so a title-only change
  never restages a body, and without them the drafter's revise mode and the slicer's promotion
  silently dropped an operator-confirmed title change). Its leading size check is the
  **size gate**: the caller stages the verbatim body to its scratch dir and passes the
  *path*, so nothing re-serializes a body across the prompt boundary; an empty or missing file is
  an `EMPTY_BODY_FILE` decision and one over `pipelib.limits.BODY_CHAR_LIMIT` a `BODY_TOO_LONG`
  decision, both **before** any `gh` write (the #626/#627 empty-body race; #38's unpostable plan).
  The cap is measured in **characters** — `body_bytes` runs ahead of it on non-ASCII text, so a byte
  gate would false-refuse a legal body. Returns
  `body_bytes` + `body_sha256` and verifies the round-trip itself. `close` on a closed issue is a
  no-op (safe for a reentrant caller like `question-resolver`); marker replacement never leaves the
  issue markerless — `comment --delete-marker-id` posts the new comment **before** deleting the old,
  and `edit-comment` rewrites one in place by numeric REST id with no delete at all (a stable URL
  and GitHub's own edit history as the supersession record — the planner's revise uses it; the
  delete-and-repost path stays, since only it collapses a duplicate marker); native-dependency writes
  are capability-gated with a `DEPS_UNSUPPORTED` notice and a prose-link fallback, and
  `create --parent` — the native parent/sub-issue write that makes an epic's stories real
  sub-issues — with a `SUBISSUES_UNSUPPORTED` notice. A create carrying both relations descends a
  rung-per-relation ladder, safe because a failed `create` files nothing. The body ops and the
  issue/pr `comment` take a repeatable `--attach FILE[#alt]` (gh ≥ 2.99, 4.41.0). That is how a PR
  that changes what a user sees carries screenshots (`skills/resolver/references/pr-screenshots.md`).
  gh rewrites a body reference only when it names the file by the **exact** path passed, so the
  path must be absolute (a relative one is a usage error). Unlike
  the relations, an old gh is never retried without the flag, since the body would ship dangling
  local paths: exit 1, stderr `ATTACH_UNSUPPORTED:`. A partial upload (non-zero exit, a GitHub URL printed)
  is a landed write with an `ATTACH_PARTIAL` notice, never a hard failure the caller would re-run
  into a duplicate. A body op reads the rewritten body back as `posted_body_path` (`<stem>.posted.md`),
  and the next restage starts from that file, not the local copy. This derives the rewritten body,
  it does not verify the write, so §12 holds.
- `config_block.py` — deterministic marker-block `read`/`list`/`upsert`/`remove`; the single
  execution path for `setup`, and the block reader `workspace.py` composes in-process.
- `workspace.py` — the worktree mechanics owner: `attach` (the v3 stage-session assertion —
  expected branch / pattern acceptance / staleness, hook re-run from the worktree's own working
  tree; mismatches are `WORKSPACE_MISMATCH` decisions with a closed `reason` set),
  `ensure --work|--read` (the creating path — branch exclusivity `BRANCH_IN_USE` lives here; used
  by workspace-open and the landing tools),
  `remove --work` (teardown best-effort before removal; dirty/unpushed gated, merged-PR-aware,
  `cwd_inside_target`-refusing; driven by workspace-close), `gc`, `root-status`, `lint`, plus the
  import-only `list_work_branches` core (which branches currently have a work worktree — the local,
  gh-free evidence `prep_workspace_close.py` resolves an issue number against). Every
  `--root` normalizes to the MAIN checkout via `--git-common-dir`, so invocation from inside a
  linked worktree — the normal v3 posture — behaves identically. It runs the consuming repo's
  `<!-- worktree-setup/teardown -->` commands and maintains the `.worktrees/` exclusion in the
  repo's `info/exclude` — **never** a `.gitignore` edit, because `info/exclude` lives outside the
  working tree and so can never surface in `git status` on its own write.
- **`refblocks.py` is retired** (v3.x, and this is the one deletion to understand before touching
  this area). It held the v3 gate-config trust rule — pin `origin/main`, read marker blocks from
  that commit's blobs — so a PR-branch session could not weaken the gates judging it. It is gone by
  operator decision: pinning also made the *consuming repo's own config* untestable, since a
  `<!-- worktree-setup -->` or gate-block edit could not run until it merged, and a bad one then
  broke every workspace-open and every session entry. All block reads now go through
  `config_block.read_block_anywhere` against the ambient checkout's working tree. Two notes for a
  future editor: the v2 TOCTOU the pin also closed (a plain `open()` racing a freshness check) does
  **not** return — there is no freshness check left to race — and the security property genuinely
  is weaker, traded knowingly for provenance reporting (`config.source` / `setup.source`) instead of
  a confirmation card that would fire on every session entry. Don't reinstate the pin as a bug fix;
  see architecture.md §6 rule 1 and the §12 invariant.
- `branching.py` (import-only) — branch naming (`-vN` collision suffixing), issue-type detection,
  the 7-row prior-PR classification, epic-branch discovery, parent-epic search (native `parent`, else
  the one epic whose `## Stories` checklist lists the story — a body mention is not membership), GitHub linked
  branches (`gh issue develop`, with the `ISSUE_LINK_UNSUPPORTED` degradation), and two precision
  helpers the loose `#<N> in:body` searches can't provide: `find_merged_pr_for_head` (an exact
  `gh pr list --head` merged lookup, non-fatal via `MERGED_PR_LOOKUP_UNAVAILABLE` so an offline
  close still works) and `branch_belongs_to_issue` (does `<N>-<slug>` / `epic/<N>-<slug>` name this
  issue — the guard that stops a sibling story's PR resolving as this issue's branch). Shared by
  `prep_workspace_open.py` (which mints branches) and `prep_resolver.py` (which only asserts —
  its ladder adopts linked/ambient branches and never re-runs collision naming against a branch
  workspace-open already pushed; recomputing would yield `-v2`, a guaranteed self-mismatch). Also
  `detect_ambient_issue` — the same two grammars read in the other direction ("which issue is *this
  branch's*?"), so a skill can notice it was invoked from inside `epic/<N>-<slug>` instead of filing an
  orphan. Deliberately **non-gating**: an unparseable branch, a detached HEAD, or a failed lookup yields
  no fact and at most an `AMBIENT_ISSUE_LOOKUP_UNAVAILABLE` notice, never a decision — a convenience
  fact must never break a session that files fine today. `prep_drafter.py` is its only consumer,
  surfacing it as `facts.ambient` for the drafter's ambient-issue relationship gate; the drafter still
  writes **no** parent edge (that stays the slicer's, per `_shared/epic-story-hierarchy.md`) — the
  "child of #N" answer routes the handoff to a slicer adoption run.
- `parse.py` — `dod` / `oq-links` / `phases`: the three contract parsers, each with a malformed
  decision code (`DOD_MALFORMED`, `PHASES_MALFORMED`). `phases` also reports the staged body's `size`
  against `BODY_CHAR_LIMIT` (`pipelib.limits.body_size`) — the whole plan's, informational since 4.35.0:
  the cap is per comment, so the planner's size read is `plan_parts.py split`'s; never a decision. The DoD's **shape** is a fact too (4.32.0): `dod` also reports
  `present` and `other_checklists` (`dod_summary` — checkbox lists under any other `##` heading, the
  legacy epic `## Stories` excepted; the drafter's post-staging check), and `phases --issue-body <path>`
  adds `dod_coverage` (`dod_coverage` — out-of-range / unclaimed / doubly-claimed `closes-dod` indexes
  and `drift`, `dod-annotations.md`'s index-stability invariant as written). The planner's prep reports
  `facts.dod` (with the body staged at `dod.body_path`), the resolver's `facts.dod_coverage`. Never a
  decision: the heading is the contract, and a checklist under `## Acceptance criteria` is not the DoD. Plus two import-only scanners for renderings the
  pipeline rebuilds — the PR's `## Phase tracker` and `## Polish` ledger — which never raise (an
  unparseable line is rewritten, never a stop), so they have no subcommand and no decision code. And
  one import-only scanner for a contract it only reads: the legacy epic `## Stories` checklist
  (`parse_stories_section` / `stories_checklist_lists` — the planner's, slicer's and evaluator's
  story set and branching's legacy parent edge share this one copy; each prep carried its own, with
  diverging grammars, until 4.33.1). And `scan_follow_ups` — a PR body's `## Follow-ups` section
  (`skills/_shared/follow-ups-section.md`: `Filed:` lines with their ` · covers` ledger ids, `Note:`
  lines skipped, pre-contract bullets read leniently; never raising) — which `prep_evaluator` composes,
  with `scan_polish`, into an epic-integration PR's `facts.epic.residue`: every PR merged into the epic
  branch, its story, its ledger entries but `applied`, the follow-ups it filed with their live state and
  kind (one batched `gh api graphql` read), and the `file` entries no `Filed:` line covers (4.37.0). The
  stories' deferrals stay on their own PRs; the residue reads them there rather than copying them into
  the integration PR, which is why the evaluator's residual filing weaves `Filed:` lines into the body's
  `## Follow-ups` instead of a comment.
- `plan_parts.py` — one implementation plan, three comments (`skills/_shared/plan-parts.md`): the
  main `<!-- implementation-plan:v1 -->` comment plus a `build` part (`## Changes` / `## Data model` /
  `## Test plan` / `## Coverage gap`) and a `references` part (`## Doc grounding` / `## External
  sources consulted`), each `<!-- implementation-plan-part:v1:<slug> -->`. The main comment keeps every
  heading, a moved section's body reduced to a stub link, so the cut is mechanical both ways. CLI
  `split <plan> --out-dir <dir> [--url <slug>=<url>]` writes the three bodies and reports each one's
  size — the planner's size fact, because the cap is per comment. Import cores `find_parts` (duplicate →
  `MARKER_AMBIGUOUS`) + `assemble` / `assemble_from_thread`, composed by **`gh_gather.py`'s plan-marker
  lookup** (so the planner, resolver and drafter preps all stage the whole plan, and `marker_parts`
  carries the part ids a revise edits) and by `prep_evaluator`'s own thread scan. No prompt sees the
  split; a sub-agent that needs the plan gets the staged path, never a self-fetch of the main comment.
  Parts are reported even with no main comment, so a fresh post that failed half-way is retried in
  place rather than duplicated.
- `plan_shipped.py` — the **legacy** shipped-phase records (`skills/_shared/plan-shipped-phases.md`).
  Nothing writes one since 4.35.0 (the build part has its own cap); existing ones are read until a
  revise folds them back. Import core `collect(thread, pr)` (a thread scan, keyed (PR, phase), duplicate
  → `MARKER_AMBIGUOUS`) + `read_pointed` (every PR the plan's pointer bullets name — read leniently),
  composed by prep_planner (the fold-back source, `facts.plan.shipped`) / prep_resolver / prep_evaluator;
  CLI `check <plan> --restored <record>…`, which reports any pointer bullet left (`pointer_left`) and
  any record entry that did not land verbatim (`not_restored`). Always `ok` — findings are the
  planner's to fix, no decision code. `prep_evaluator` also thread-scans the closing issue's plan
  itself (`facts.plans`) and degrades a duplicated plan, part or record to a notice, never a stop, as it
  does for the delivery log.

Every executor exposes a **pure, non-emitting core** — `build_*(...) -> (payload, notices,
decision | None)` — with `main()` as a thin emit wrapper. A prep calls those cores directly and
emits exactly one envelope of its own; **no `redirect_stdout` capture** of another script's stdout
(the S8 pattern lock — `docs/specs/baseline.md` §5.1).

### Workspaces and the trust topology

The v3 model ([prd.md §8](docs/prd.md), [architecture.md §6](docs/architecture.md)): **the session
runs where the operator starts it.** A `work` workspace is `.worktrees/<branch>` — opened by
`workspace-open` (which also owns creating `epic/<N>-<slug>` integration branches), reused across
sessions, released by `workspace-close` (teardown hooks, then removal gated on dirty/unpushed
state, merged-PR-aware, refused from inside the target). Resolver/evaluator sessions start
**inside** it; their preps observe + assert the ambient checkout (`workspace.py attach` — wrong
branch / project root / detached / stale are `WORKSPACE_MISMATCH` decisions) and re-run the
`<!-- worktree-setup -->` hooks on every entry, discovered in that worktree's own working tree. The
planner grounds on its asserted ambient checkout (`facts.grounding`), asserted by **content**, never
branch name (`grounding.basis`): `plan_ref` itself (a default-branch plan_ref passes from any current
checkout of it including the project root — plan-before-open); the issue's own branch when it already
CONTAINS the tip of the ref it would otherwise have grounded on (rows `story-own-branch` /
`own-branch` — the containment probe standing in for the name check that refused a bit-identical
checkout, and refused every standalone issue's own worktree until 4.33.0); any branch exactly at that
tip (`identical`); and another branch carrying commits beyond it only on the operator's
`--ground-ambient` card answer (row `ambient-accepted`). Behind or diverged still refuses. A `read` workspace
is `.worktrees/ro-<ref-slug>` (detached HEAD at `origin/<ref>`, reset on every ensure, `gc`'d by
age — and only `ro-*` is ever gc'd): script-internal plumbing for the resolver's audit view.

Two rules govern config reads (the default branch still changes only via PR, and it is **derived**
via `workspace.default_branch`, never hardcoded): **every marker block — worktree hooks and gate
config (test target, checks, merge policy) alike — is read from the working tree of the checkout the
command runs in**, committed or not, with `setup.source` / `teardown.source` / `config.source`
reporting which checkout, branch, and SHA answered and whether it was dirty; and pinned-ref grounding
stays in the internal `ro-*` views. No path inspects or writes the operator's checkout any more —
the `ROOT_*` freshness gates and the `git merge --ff-only` that fast-forwarded it are retired with
the pin. The prompt-visible rule is: **your workspace is the ambient checkout, reported as
`facts.workspace`** — and every Read / Grep / Explore / test / command still names
`facts.workspace.path` by absolute path, because sub-agents and background commands run in their own
cwd. The drafter stays root-only and reads its OQ-marker hint from the ambient checkout, which is
now the same rule everything else follows rather than an exception.

### Shared contracts in `skills/_shared/`

- `handoff-format.md` — the cross-session `## Handoff` schema, omission rules, Epic/Story variants,
  terminal endings, re-route rules, the closed-set state-marker vocabulary, and the next-command
  argument rule (the evaluator takes a PR; every other skill, the resolver included, takes the issue —
  never `continue #<PR>`, which trips `TARGET_IS_PR`).
- `dod-annotations.md` — the closed set of `## Definition of done` checkbox annotation forms and
  the parser. Three skills share it: the **resolver** projects ticks as phases ship, the
  **evaluator** verifies and writes sticky-veto un-ticks, the **planner** reconciles during revise
  mode. Annotation form and checkbox state must always agree; a bullet never stacks two
  annotations. The heading is part of the contract: a checklist under any other heading is never
  indexed or projected onto — the drafter files criteria under `## Definition of done`, and the
  planner's DoD-heading card offers to rename one that isn't (a deliverable slice's
  `## Acceptance criteria` is the slicer's, deliberately outside it).
- `worktree-lifecycle.md` — the **external** `<!-- worktree-setup -->` / `<!-- worktree-teardown -->`
  block format a consuming repo declares, and what those commands must guarantee (setup idempotent
  because it runs at workspace-open *and* on every resolver/evaluator session entry, discovered in
  the working tree of the checkout the command runs in; teardown best-effort and before removal, run
  by workspace-close from the worktree being closed). The
  *mechanics* belong to `workspace.py` and architecture §6 — this file does not restate them.
- `doc-catalogue.md` — the **external** `<!-- doc-catalogue -->` block a consuming repo declares in
  its `docs/README.md`: the fixed home, the one-line entry grammar, the closed `binding` /
  `informative` authority pair, the open `role` set, the user-owned ownership posture, and the
  absent-catalogue rule (including that a proceeding reader and a refusing reader are **both** correct
  responses to the same absent fact — don't "fix" one to match the other). The *read mechanics* belong
  to `scripts/doc_catalogue.py` and the *authoring flow* to `skills/setup/references/block-authoring.md`.
- `epic-story-hierarchy.md` — the **three-level** hierarchy `epic → story → deliverable slice`, both
  edges being GitHub's **native parent/sub-issue** relation. **Both edges are written only by the
  `slicer`** (#16 — the epic↔story edge was the drafter's until then): `gh_persist.py create --parent` at
  filing time on every path that files a child (fresh cut, promotion, a resume's new children), plus the
  one after-the-fact path, `gh_persist.py add-parent`, which adopts an already-filed issue so an epic can
  be drawn around stories authored upstream. Read by the **planner**, **resolver**, and **evaluator** through a two-tier read — native
  first, the legacy `## Stories` checklist second, `stories_source` reporting which answered
  (one read, `scripts/story_set.py`, which the slicer's and coordinator's preps compose too). A fresh
  epic body has **no** `## Stories` section (a checklist can't self-tick and can't drive GitHub's panel,
  rollup, or a Project's Sub-issues progress field). The fallback is load-bearing: there is **no
  backfill path**, so epics filed before 3.1.0 — and any host where `subissues_available: false` — live
  on the `checklist` source permanently; an epic with both reads as `mixed` and is unioned, never
  halved. No reader ever gates on the relation's absence. This file also defines **deliverable slice**
  once (the one-parameter difference from a story: *demonstrable* vs *shippable*, set by whether the
  child gets its own branch), the by-construction identification rule (a non-epic's sub-issues are
  slices) with its two constraints (slices only under non-epic targets; **never slice a slice**), the
  closing contract (the resolver closes on the *last* serving phase; the evaluator is a merge-time
  backstop), and the recorded gap that **reopen is unowned**. The slice edge has no checklist fallback
  and never will — slices postdate the relation.
- `epic-delivery-log.md` — the epic delivery-log contract and its writer/reader split. The log is
  **one comment per shipped story** (`<!-- epic-delivery-log:v2:story:<N> -->`), because a single
  accumulating comment is bounded by `BODY_CHAR_LIMIT` and an epic reaches it by construction —
  after which no merge can be recorded at all (#41). The story number in the marker is what keeps
  one-marker-one-comment, so a genuine duplicate stays detectable. The **evaluator** is the sole
  writer (one entry per story at merge, `edit-comment` for a re-record); the **planner** reads it
  (just-in-time story planning + the "consumes only what's shipped" check) through a two-tier read
  — per-story entries first, the legacy `<!-- epic-delivery-log:v1 -->` monolith second, unioned by
  story number with the per-story entry winning, `log_source` reporting which answered. As with the
  epic hierarchy there is **no backfill**, so a pre-#41 epic keeps its monolith permanently. The
  shared read model is `scripts/delivery_log.py`, composed by both preps so the writer's view and
  the reader's cannot drift. It is *separate* from the verified `<!-- implementation-plan:v1 -->`
  epic plan precisely because it changes on every merge while the plan stays immutable.
- `plan-parts.md` — one plan posted as three comments: which section lives in the main comment, the
  `build` part and the `references` part (decisions, phases and risks stay in main; the work list and
  the citations move), the stub-link format, the parts-first-main-last persist order, and the rule that
  every reader gets the whole plan from its prep. Every plan is split this way, not only epics — it
  replaced the shipped-phase relocation as the answer to the per-comment cap.
- `plan-shipped-phases.md` — the **legacy** shipped-phase record contract. Between 4.20.0 and 4.35.0 a
  revise moved each shipped phase's `## Changes` / `## Data model / schema impact` / `## Test plan`
  entries verbatim into its own `<!-- implementation-plan-shipped:v1:phase:<N> -->` comment, keyed
  (PR, phase), leaving a pointer bullet. Nothing writes one now. Readers (the **resolver**'s distiller
  bundle, the **evaluator**'s `facts.plans`) still follow every PR the pointers name until the plan's
  next revise, where the **planner** folds every record back verbatim and drops the pointers
  (`skills/planner/references/shipped-phase-foldback.md`); the records then go inert, never deleted. A
  separate marker family, so no plan `startswith` lookup matches one.
- `open-question-detection.md` — how to **find** an open question in any project doc (the
  `<!-- drafter-open-question-markers -->` config-block hint + heuristic cues; OQs aren't
  centralized) and **match** it to a tracker issue (search before filing, `Read` to confirm).
  Shared by **`question-sweep`** (project-wide), the **drafter** (issue-scoped, thin), and the
  **planner** (grounding).
- `question-issue.md` — the `question`-issue body schema (`## Question` / `## Audience` /
  `## Constraints` / `## Context` / `## References` / `## Why this matters` / `## Tracked in`), the
  `audience:*` label rule, and the `## Tracked in` doc↔issue bridge. Shared by the **drafter** and
  **`question-sweep`**, which file identical question issues; each keeps its own orchestration.
- `open-question-links.md` — the `## Open questions` body section (marker
  `<!-- open-question-links:v1 -->`) a **build** issue carries when drafted from a source with
  unresolved open questions: the per-entry schema, the closed disposition set (`scoped-out` /
  `in-scope (blocked)` / `provisional-default`), the rule that `in-scope (blocked)` also sets a
  native `blocked by` dependency, and the **tiered status read** (an OQ's resolution comes from the
  *tracker* — Tier 1 issue `state` **or** a `<!-- question-decision:v1 -->` comment, Tier 2 the
  question-status reader — never a doc register field). **The registry of record is the set of
  `question` issues**; docs are sources. `question-sweep` owns the registry; `question-resolver`
  closes an entry (records the operator's decision, optionally closes the issue, *proposes* the doc
  fold-back — never deciding, never applying doc edits); the drafter is a thin writer; the planner
  reads and extends it, never resolving an OQ; the resolver and evaluator read the native
  `blocked_by` and hard-gate the in-scope-blocked case, treating the section as a
  tracked-dependency registry, not buildable scope or DoD.
- `follow-up-filing.md` — the drafter-proxy sub-agent protocol the resolver, the evaluator, and the
  planner (seam-disposition follow-ups) use to file a follow-up issue (never a hand-crafted
  `gh issue create` body). Related items file as **one issue per group** (same type, same seam,
  ≤ ~5, one checkable bullet per item — the planner's seam follow-ups excepted; the resolver's **docs
  lane**, one issue per owning docs process, is uncapped), and one filing
  moment's approved groups are spawned as **one concurrent batch, in a single message** — the groups
  are independent, and serial spawning cost the drafter's full round-trip each in series; same-target
  `revise-existing` items are the one exception.
- `asking-the-user.md` — the `AskUserQuestion` card contract. **Consent** cards (a closed set: irreversible
  or outward acts, and decisions a contract reserves to the human) always render; a **judgment** card
  renders only when the genuine-choice test leaves two or more options a reasonable operator could pick
  here, and otherwise the choice is announced in one line (`Did X because Y; say if you'd rather Z`) and
  never recorded as the operator's decision. Every card opens with a short plain-words **brief** (what,
  why now, end-user impact per path, trade-off, clear/close call); "more" in Other renders the evidence
  and re-asks the same card. The recommendation is reasoned per instance, never a fixed label. Plus the
  card shape and the rule that a sub-agent returns a §3 decision code instead of asking. The routers'
  decision card rule renders "with its brief" at the point of use, since a linked file is not
  guaranteed to be in context when a card renders. **Relayed cards**: a stage session started by the
  coordinator has its cards mirrored into the coordinator's chat (the host does this, text beside the
  card included, and accepts the answer in either session), so there the card is self-contained — its
  question opens with a `<stage> · #<N> · <step>` identity line and the brief — and announced choices,
  which raise no card, ride in the stage's milestone updates.
- `plan-summary.md` — the `## Plan summary` block: the human-readable digest of an implementation
  plan, its fixed section set, its sourcing from the plan schema, the one `focus` parameter that
  varies between callers, and the planner-only invitation plus the **read-only after the handoff**
  rule that keeps a posted plan reviewer-verified. Rendered by the **planner** (before its
  `## Handoff`, whenever the session posted or refreshed a plan) and by the **resolver** (at session
  start, focused on the phase this run ships). It is never part of the `## Handoff` block.
- `follow-ups-section.md` — the `## Follow-ups` PR-body section: one `- Filed: <ref> — <title>` line per
  filed issue (one per group), ` · covers <ledger ids>` when the group files `## Polish` entries, and
  `- Note:` lines for procedural notes, whose issue mentions are never deferrals. Written by the
  resolver's URL weave and the evaluator's residual filing; read by `parse.py` `scan_follow_ups` into the
  epic residue. The `covers` tie is what lets the epic-integration evaluation see a filed polish entry
  resolved when its issue closes or is adopted as a story, instead of re-blocking on it.
- `polish-ledger.md` — the `## Polish` PR-body section: the findings the resolver's review loop recorded
  instead of fixing — polish it left on the PR's own code, polish in code the PR does not change, and
  findings with no shown cost, a non-material doc or comment finding included (recorded `drop` without asking) — per its provenance-first rubric (the loop exits on
  **defect** progress alone, so polish never keeps it running). One line per item with a stable `P<phase>.<seq>` id and a closed disposition set
  (`open` / `applied` resolver-written; `apply` / `file` / `drop` the **operator's** answers, recorded by
  whichever skill asked). The operator decides every entry on a `Polish` card, first at the **resolver's
  finalisation** — on the final pass, before the push, so an applied entry costs one fix round, not a
  revision session — and the **evaluator** is the backstop: it re-checks at head every unfixed `apply`,
  every `file`, and every `premise false` `drop` (that premise was judged by the code's author at
  finalisation), and asks about any entry still `open` (never folded into the approval card). An `apply` it confirms →
  soft-reject to the resolver, or to the planner on a Re-plan or when applying it reverses a locked
  decision; a finalisation Re-plan re-routes the resolver's own handoff to the planner. An `apply` item is
  Addressable for the resolver whatever its tier, which is what stops a polish ping-pong. `file` entries
  file after the merge, whoever recorded them. `parse.py`'s `scan_polish` reads it for three preps; it
  never raises. A merged story PR's ledger is read once more by its epic's integration evaluation
  (`facts.epic.residue`) and judged as deferrals against the epic's DoD — never put back on a `Polish`
  card; a deferral that maps to it re-routes to the **slicer**, which adopts the deferred-to issue as a
  story (the only way work reaches the epic branch, and the only writer of that edge). The resolver's
  epic route keeps the integration PR's own ledger and `## Follow-ups` across its body refresh (S4.2
  re-renders only the sections it owns, from `integration_pr.body_path`).
- `polish-triage.md` — the one procedure both `Polish` cards run (the resolver's finalisation, the
  evaluator's S5.5): re-check each entry's claim at head, propose a disposition, group by **theme** (one
  proposal per group, so one answer fits it), print a **digest** showing the code at each entry's anchor,
  ask, record the answers. `file` entries carry a filing group computed apart from the card's themes and
  shown in the digest. Each caller supplies its entries, checkout, plan and write; the one proposal rule that differs is the **apply bar** —
  finalisation proposes `apply` for anything in scope, true at head and clearly right (applying costs a
  fix round), the evaluator only for the ledger's "Apply criteria" (applying costs a session). Never
  restate the card's options outside it.

When changing behavior that touches handoffs, the plan summary, the plan's parts, DoD annotations, the worktree block
format, the epic delivery log, the follow-ups section, or the open-question contracts, edit the `_shared` file (the single
source of truth)
and keep the per-skill renderings consistent with it — **render, don't restate**.

### Coupling to a consuming repo is convention-driven

The skills degrade gracefully when conventions are absent, but key behaviors are driven by markers
the *consuming* repo provides — not by plugin config:

- **Marker comment blocks** in the consuming repo's `CLAUDE.md`/`COMMANDS.md` tell the resolver and
  evaluator how to test and gate: `<!-- issue-resolver-test-target -->`,
  `<!-- issue-resolver-fast-checks -->`, `<!-- issue-resolver-canonical-suite -->`,
  `<!-- pr-evaluator-health-checks -->` (legacy, split on migration),
  `<!-- pr-evaluator-static-checks -->`, `<!-- pr-evaluator-test-target -->`,
  `<!-- pr-evaluator-escalation-labels -->`. The evaluator also reads
  `<!-- pr-evaluator-merge-policy -->` (per-PR-type `ask | auto`) to decide whether its merge step
  gates on the operator — **default `ask`** when the block is absent. The **drafter** (and the
  planner, for in-scope provisional markers) reads `<!-- drafter-open-question-markers -->` to
  learn how this repo marks unresolved open questions; it **degrades gracefully** to built-in
  heuristic cues when the block is absent.
- **Worktree hooks** — `<!-- worktree-setup -->` / `<!-- worktree-teardown -->`, run by
  `workspace.py` inside the workspace.
- **Epic integration branches** named `epic/<N>-<slug>` — `workspace-open` creates them (v3 moved
  creation off the resolver, which now only adopts the branch its prep discovered — never
  recomputing the slug, per the #102 orphaned-commits incident); the resolver/evaluator classify
  Epic vs story PRs by this pattern.
- **Durable marker comments** the skills post and read: `<!-- implementation-plan:v1 -->`
  (planner) with its `<!-- implementation-plan-part:v1:build -->` / `:references -->` part comments
  (planner-written, assembled for every reader), `<!-- implementation-plan-shipped:v1:phase:<N> -->`
  (legacy: no longer written; resolver/evaluator-read until a revise folds it back), `<!-- issue-research:v1 -->` (researcher), `<!-- epic-delivery-log:v2:story:<N> -->`
  (evaluator-written, planner-read; `:v1` is its read-forever legacy tier), `<!-- pr-evaluator-health-cache:v1 -->` (evaluator, keyed on
  head SHA), `<!-- question-decision:v1 -->` (question-resolver-written; the tiered status read's
  Tier 1, so a marked question reads as resolved deterministically), and the
  `<!-- open-question-links:v1 -->` build-issue body section (drafter-written;
  planner/resolver/evaluator-read). For an **epic**, the planner's plan comment pins the
  cross-story contracts (`## Story contracts`) and sequencing up front and stays verified and
  immutable — at coordination altitude only (plan-schema.md "Altitude": every section carries what no
  single story owns; signatures, shapes and rejected alternatives are the story plans'); per-story plans are authored **just-in-time** against current epic HEAD, not fanned
  out, so a later story never grounds on code a predecessor has since moved.
- **Grounding docs the repo declares itself** — the `<!-- doc-catalogue -->` block in the consuming
  repo's `docs/README.md` ([`skills/_shared/doc-catalogue.md`](skills/_shared/doc-catalogue.md)), one
  line per document carrying its path, `role`, `authority` (`binding` = a conflict is a blocker |
  `informative` = context), and a summary. A line may name a **directory** instead — a document set
  under one role/authority/summary (an architecture hub's spokes); `doc_catalogue.py` reports it as
  `kind: dir` with its direct files as `members`, and readers cite the member file. Until 4.26.0
  presence was `is_file()`, so a directory read as missing and the planner/slicer skipped binding docs;
  listing a declared directory one level is not the filesystem walk the absent-catalogue rule
  forbids. `setup` writes it (seeding via a context-blind derivation sub-agent, re-ingesting an
  existing block as the base); the `prep_planner.py` / `prep_drafter.py` / `prep_slicer.py` /
  `prep_requirements_gatherer.py` / `prep_resolver.py` / `prep_evaluator.py` /
  `prep_question_resolver.py` readers consume it through `scripts/doc_catalogue.py`, as
  `grounding_docs`, at the same vantage as the docs themselves (as every config family now is — this
  was a deliberate exception while gate config was pinned): the resolver at its audit view (reported
  as `grounding_path`), the evaluator at the PR head. Every sub-agent prompt that checks docs receives
  the entries as `<<catalogue_entries>>`. When it is absent
  the readers emit the
  `DOC_CATALOGUE_ABSENT` notice and ground on **nothing**: there is no built-in path list and no
  filesystem walk, because a guessed doc layout is exactly what the block removes. The plugin
  formerly hardcoded `docs/prd.md` / `docs/architecture.md` / `docs/constitution.md` / `CLAUDE.md` in
  two preps that disagreed with each other and with the prompts, and until 4.27.0 the resolver's audit,
  the evaluator's doc grounding, the drafter's issue reviewer, and the question-resolver's constraint
  audit still read that list in prose; don't reintroduce a default list.
- **Setup-authored operating guidance** (distinct from the machine-parsed blocks above): `setup`
  proposes a `<!-- claude-code-stack-profile -->` block in the consuming repo's `CLAUDE.md` —
  concise, currency-checked guidance on running that stack efficiently in a Claude Code session
  (background slow commands, log verbose output instead of flooding context). No skill parses it;
  *every* session consumes it via the CLAUDE.md auto-load, which is why it lives in `CLAUDE.md`,
  not `COMMANDS.md`. Because nothing parses it, it is **user-owned**: setup seeds it when absent and
  re-ingests the user's edits as the base on re-run, proposing only currency refinements — unlike
  the machine-parsed blocks, which it reconciles to canonical.

## Editing conventions for this repo

- **`${CLAUDE_PLUGIN_ROOT}` substitution.** Skill bodies reference bundled files as
  `${CLAUDE_PLUGIN_ROOT}/...`; Claude Code substitutes the real install path inline before the
  model reads it. That path changes on every plugin update and is **read-only** — never write state
  there. Scratch dirs are uniformly `/tmp/gh-<skill>-<N>/` — `<N>` the session's key: the issue/PR
  number, or the **process id** for a new-mode drafter that has none yet, so the concurrently-spawned
  drafters of one follow-up batch never share a staging dir — and prep reports the one in use as
  `facts.scratch`. Where a path must reach a *raw-read* reference file or a *dispatched sub-agent
  prompt* (which are **not** substituted), the orchestrating skill resolves the path itself and
  passes it as an explicit placeholder.
- **Plugin namespace is baked in.** Skills resolve as `/github-pipeline:<skill>`; cross-session
  handoff commands are namespaced to match. Renaming the plugin means updating every such
  reference.
- **Model/effort are not pinned.** Skill frontmatter carries no `model:` or `effort:` keys — every
  skill inherits the invoking session's model and effort level. The v1 per-skill pins were removed
  2026-08-01; reintroducing one is a deviation through the normal gate.
- **`disable-model-invocation: true` is exactly seven tools** — `doc-reviewer`, `question-sweep`,
  `question-resolver`, `requirements-gatherer`, `workspace-open`, `workspace-close`, `coordinator`
  (which must never auto-trigger on "work on #N", the resolver's trigger). **`setup` is the deliberate
  exception**: it is a standalone tool but stays model-invocable, because v1 never carried the key
  on it and the S17 parity run adjudicated the difference rather than "fixing" it
  (`docs/specs/parity/setup.md`, "ADJUDICATION RECORD"). Don't add the key to `setup` on the
  assumption that all the tools match.
- **v2 was written from scratch; v1 lives in git history.** A skill's router + playbooks were
  authored against its `docs/specs/<skill>.md` spec, the PRD, and architecture §9 — never by
  editing v1 prose down. The v1 tree was removed at S20; the last commit carrying the seven
  renamed v1 skill directories, the `agents/` executor prompt, and the five `scripts/*.sh` is
  `d29ec1b`.
  Two skills kept their v1 *name*, so their v1 file is only visible at the commit before their own
  retire-rebuild: **`f9f1623`** (v1 `question-resolver`) and **`7bffb90`** (v1 `doc-reviewer`).
  Cite those SHAs when you need the v1 behavior; the frozen behavioral record is `docs/specs/`.
- **Frozen artifact provenance strings.** A handful of persisted artifacts still carry a footer
  naming the *v1* skill that authored them (the plan comment, the research dossier, the health
  cache, the review comment, and the `<!-- github-pipeline-config -->` header body's "re-run setup"
  line). Those are **byte-compatibility contract tokens** a cross-consuming v1 reader matches
  verbatim ([prd.md §7](docs/prd.md); the S7 adjudication in `docs/specs/baseline.md` §5.3) —
  **not** skill names awaiting a rename, and the v2 rename deliberately did not propagate into the
  artifact bytes. `tests/support/retired_tokens.py` is the one file allowed to spell a retired v1
  name: it holds the literals and the exemption table (file + exact fragment). Every *other* v1
  name under `skills/`, `scripts/`, `tests/`, `README.md`, `.claude-plugin/`, or this file is a
  stale reference and fails `tests/test_v1_retirement.py` — which is why the paragraphs here name
  the retired surfaces descriptively instead of quoting them.
- **Stable §-anchors over positional cross-references.** Skills navigate themselves and each other
  by stable anchors — workflow steps as `§N`/`step N`. Never reference a section by position ("the
  section above", "per X below") or by a hard line range: both silently dangle or invert the moment
  content moves. When you reference another skill's section, name it (`§9 "DoD projection rule"`),
  don't cite its line number. **The resolver-local `§P-ID` scheme is retired** — it existed because
  the v1 resolver was one 1169-line file; the v2 router + playbook split removed the need, and
  nothing under `skills/` or `scripts/` defines a `§P` of its own. Outside `docs/specs/**` (the
  frozen v1 record, which keeps its `§P` rows) the only occurrences are this rule, the validators
  that forbid it, and script comments citing one of those frozen spec rows. Reintroducing a live
  `§P` is a regression the per-skill grep gates catch.
- **Routers stay thin, playbooks stay linear.** A router fits in one default `Read` (≤ ~150 lines),
  so the v1 forced-mid-flow-`Read` workaround is retired too. One route per session: the router
  selects exactly one playbook, which may pull in its skill's single shared spine file — nothing
  else, never a second playbook. **Parameterize before you playbook**: a branch that differs only
  in *values* (base ref, branch name, merge strategy, cleanup list) is not a branch — the values
  are facts. A playbook exists only for flows that differ in *actions taken*, and carries no
  `if epic … else if story …` interleaving.
- **Skills stay tech-stack-agnostic; stack specifics live in the consuming repo, not the prompts.**
  The pipeline was extracted from a Swift/iOS project and is also run against other stacks (e.g.
  Ruby on Rails), so a prompt that *assumes* one stack is a bug, not a quirk. Stack specifics are
  carried out-of-band — the consuming repo's marker-comment config, the worktree hooks, and the
  **gated** `xcodebuild`→`apple-platform-build-tools:builder` delegation. Three forms of
  tech-mention are allowed; one is banned:
  - **Banned — assumed default.** An instruction that only parses for one stack: "for each modified
    *Swift* file", "the wrapper supports `-only-testing FoodJournalTests`", "capture
    `app.debugDescription`". On the wrong repo these are wrong instructions the model will try to
    follow. Rewrite to the stack-neutral principle (the universal concept first, e.g. "high-fanout
    integration-surface file", "the wrapper's targeted-run syntax").
  - **Allowed — conditional integration** gated on a runtime signal that no-ops elsewhere ("if the
    command begins with `xcodebuild`, delegate to the Apple builder; otherwise run inline"). Keep
    these — it's how a stack-specific optimization stays agnostic.
  - **Allowed — labeled multi-stack example.** Name concrete stacks *as examples*, and show **≥2**
    (the convention here is Swift *and* Rails) so the schema reads as neutral; never present one
    stack's worked example as "the canonical shape". State the generic principle first, then
    illustrate. `skills/setup/references/block-authoring.md`'s worked examples and the
    test-selection sub-agent's SwiftUI/Rails branches are the reference patterns.
- **Compressing a prompt without losing precision.** These skill bodies are agent instruction
  prompts run at whatever model/effort the invoking session uses (typically a frontier model at
  medium-or-higher effort), not chat prompts — so when reducing tokens the
  target is the *smallest set of high-signal tokens that fully specifies the behaviour*, **not the
  shortest text** (Anthropic's "minimal ≠ short"; corroborated by OpenAI and Google prompt
  guidance). This is load-bearing because these models follow instructions **literally** at these
  effort levels: they will not silently generalise a scope you trimmed or re-infer an intent you
  dropped, and no offline test reads prose. Cut low-signal prose; keep every token that carries scope,
  intent, or contract.
  - **Compress — token wins with no precision cost:** delete filler and hedging ("in order to",
    "it's worth noting", restated context); de-duplicate against the point-of-use copy (an intro
    may lean on a fact restated at its `§N` *only when that copy is actually present*); use
    imperative action verbs ("Delegate", "Read from the path") over "you should consider…";
    structure with Markdown headers / labelled blocks / lists; state what to do, not a list of what
    not to do.
  - **Do NOT — looks like compression, costs precision:**
    - **Word-for-symbol shorthand** — `w/`→"with", `&`→"and", `->`→"leads to" *in prose*. No vendor
      endorses it, the token saving is ~zero, and it reads ambiguously next to `gh` flags and code.
      (A flow arrow in a structured list — `Broad search → spawn Explore` — and `+` as a list-join
      — `PR + diff` — are existing house style and fine; the ban is on substituting symbols for
      words in running prose.)
    - **Paraphrasing a contract token** — a synonym for a parsed identifier is a contract break,
      not a compression. Preserve verbatim: marker comments (`<!-- … -->`), §3 decision codes
      (`EMPTY_BODY_FILE`, `BRANCH_IN_USE`, `AMBIGUOUS`, …), facts-block key names the playbooks read,
      script names and subcommands, `/github-pipeline:<skill>` invocation strings, scratch-dir
      conventions (`/tmp/gh-resolver-<N>/`), §-anchors, and the closed-set vocabularies in
      `skills/_shared/handoff-format.md` and `dod-annotations.md` (`open`/`closed`,
      `APPROVE`/`COMMENT`, `squash`/`merge`, the DoD annotation forms). Rule of thumb: if another
      skill, a script, or a GitHub consumer parses it, it's contract; the prose around it is
      compressible.
    - **Dropping the "why"** — a rationale clause (`#626/#627 race`, "a PR must not weaken its own
      gates", "single source of truth") is high-signal: it's what stops a later editor
      reintroducing the bug. Compressing an explained invariant down to a bare command is the exact
      failure Anthropic warns against.
    - **Collapsing a scope qualifier** — "on every ensure", "first phase only", "before any code
      work begins", "at the recorded root SHA" are the words Opus won't re-infer.
  - **Phrasing:** prefer plain imperatives over `CRITICAL`/`MUST`/ALL-CAPS (these over-trigger on
    current models); reserve **bold** for genuinely load-bearing invariants, not default emphasis.
    Don't add a blanket "be concise" directive to a skill body — the models are already terse; put
    the concision where you want it.
  - The **`compress-skill-section`** skill (in `.claude/skills/`) automates this rule end-to-end: it
    drafts a denser version, runs an adversarial review→fix loop and a whole-document coherence
    check against these rules, runs the validators below, and proposes the result — it never edits
    the file.

## Validators

Prose has no compiler, so these greps are it. Run them after any prompt edit; `docs/specs/**` and
`docs/implementation.md` are exempt from all of them as the historical record.

```bash
# 1. Contract-token census — the set must not shrink across an edit (S1 baseline + the S20 v2-only
#    re-baseline are in docs/specs/baseline.md §2 and §6; the v3 workspace-model change GREW the
#    set by `github-pipeline:workspace-open` / `github-pipeline:workspace-close`, and #83 by
#    `github-pipeline:coordinator`).
grep -roE '<!-- [a-z0-9:-]+(:<N>)? -->|§P?[0-9]+(\.[0-9]+)?|GATHER_[A-Z]+|PERSIST_[A-Z]+|github-pipeline:[a-z-]+' \
  skills/ | sort | uniq -c

# 2. Retired v1 names — zero hits across skills/ scripts/ tests/ README.md .claude-plugin/
#    CLAUDE.md, outside the exemption table. The literals and the exemptions live in
#    tests/support/retired_tokens.py; this file is itself inside the scanned set, so run the scan
#    in its committed form rather than retyping the grep here.
python3 -m unittest tests.test_v1_retirement

# 3. Ref arithmetic in prompts — zero `git show <ref>:<path>` / `git grep <ref>` under skills/.
#    A bare `git show <commit>` single-commit diff view is permitted; only <ref>:<path> extraction
#    and `git grep <ref>` are banned (architecture.md §10; S7 adjudication (c)).
grep -rnE 'git +show +[^ ]+:|git +grep +[^-]' skills/

# 4. Raw `gh` writes / fetch-envelopes in prompts — every op that HAS a bundled script must go
#    through it (architecture.md §7 rule 7). Three sanctioned scriptless executors are the only
#    exceptions, all spec'd: `gh pr merge` (evaluator, merge execution), `gh pr ready --undo`
#    (evaluator, the soft-reject draft flip), `gh pr ready <N>` (resolver, the draft→ready flip —
#    on the last phase of a multi-phase issue, and on the epic integration PR once its story set
#    closes — without which the evaluator's draft guard deadlocks). Label creation
#    (`gh label create`) is likewise scriptless by design and stays inline. `gh issue develop`
#    (the branch↔issue link write) is deliberately OUTSIDE this grep's alternation AND
#    script-internal (prep_workspace_open/branching.py) — keep it out of skill fences
#    (tests/test_workspace_open_routing.py pins that), so the grep never needs widening.
grep -rnE 'gh +(issue|pr) +(create|edit|comment|review|close|reopen)|gh +api[^\n]*DELETE' skills/

# 5. Stack assumptions — every hit must be a gated integration or a labeled ≥2-stack example.
grep -rniE 'swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest' skills/

# 6. Banned shorthand — must return nothing.
grep -rnE '\bw/' skills/

# 7. Executable bit on every dispatched script (a 0644 script exits 126 at
#    ${CLAUDE_PLUGIN_ROOT} dispatch time, with no traceback).
python3 -m unittest tests.test_script_modes
```

Greps 3 and 4 are approximations run over the whole file; the committed per-skill tests
(`tests/test_<skill>_routing.py`) apply the same rules **fence-scoped**, so prose that *describes* a
banned form doesn't false-positive while a real command in a code fence does. When the grep and the
test disagree, the test is the contract.
