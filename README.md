# github-pipeline

A Claude Code plugin that runs a complete GitHub issue/PR workflow through the `gh` CLI — six
session-per-stage skills that hand off to one another, plus nine standalone tools,
backed by stdlib-only Python scripts that do every deterministic step (fetching, parsing, state
derivation, worktree lifecycle, and every write).

```
draft ──▶ research ──▶ slice ──▶ plan ──▶ resolve ──▶ evaluate
 (file)    (cite)      (cut)    (design)  (code+PR)   (review+merge)
```

Each stage runs in its own Claude Code session and ends with a copy-pasteable `## Handoff` block
that starts the next one, so context stays clean across the pipeline. That's the conceptual order —
in practice the drafter hands to the **planner**, and research is a detour the planner takes only
when the work turns on external truth it shouldn't guess at: it routes you to the researcher, which
posts a cited dossier and hands back to the planner. Slicing is the same kind of detour, and an
optional one: the planner sequences an issue's increments as phases of one multi-phase plan by default,
and offers slicing when the issue holds several milestones you'd want to track separately on a board.
The slicer files each milestone as a sub-issue carrying its own acceptance criteria, so GitHub's rollup
tracks delivery — or files nothing when the issue is really one milestone — and hands back to the
planner.

## The pipeline

| Skill | What it does |
|---|---|
| `/github-pipeline:drafter` | Turns informal feedback into **one** well-structured issue — bug, feature, story, Epic or `question` — and files it. Never silently absorbs an unresolved open question: each is matched against the `question`-issue registry, filed if untracked, and recorded on the build issue. Decomposition is the slicer's, so a filed Epic hands off there for its stories. |
| `/github-pipeline:researcher` | Web-researches version/API/migration questions and posts a dated, cited dossier on the issue — or declines outright when the issue carries no currency risk. |
| `/github-pipeline:slicer` | Cuts one issue into ordered, operator-approved children and files them as native sub-issues, so the parent's own progress rollup tracks delivery. One operation at two altitudes: a story or standalone issue becomes **deliverable slices** (milestones a stakeholder would track on their own, each served by one or more plan phases — or no slices at all, when the issue is one milestone), an Epic becomes **stories** (each independently shippable, with its own branch and PR). It can also promote an issue to an Epic before cutting it, and draw an Epic around issues that already exist. Report-then-apply: nothing is written until you confirm the whole cut, and it refuses rather than guessing when your repo declares no grounding docs. |
| `/github-pipeline:planner` | Designs the implementation approach, grounded in repo precedent + project docs at a recorded commit SHA, and posts a reviewed `<!-- implementation-plan:v1 -->` comment. Epic plans pin cross-story contracts; story plans are authored just-in-time. |
| `/github-pipeline:resolver` | Implements one issue against its verified plan **in the worktree you opened with `workspace-open`** (the session starts inside it and is verified there), opens/continues a PR, works through the plan's phases in one session — pausing wherever the plan marks a checkpoint, a warning sign fires, or the shipped diff touches a sensitive or unplanned path — it prints a summary of what changed, the decisions and deviations from the plan, and what to check, then asks whether to continue: answer within 55 minutes to keep going in the same session, otherwise the session ends there (`--pause` ends it after every phase) — projects Definition-of-done ticks as phases ship, and loops with code review while it keeps removing defects — reviewing each non-final phase's own delta against the plan, and the whole diff on the final phase, at the level the plan sets per phase from the change's blast radius (raised a step when the diff touches a declared sensitive path or files the plan does not name), stating the change's risk and how to revert it in the PR; it fixes what its own change introduced, polish included, and before handing off asks you what to do with the rest (apply now, re-plan, file as grouped follow-ups, or drop) and which follow-ups to file, applying your picks in one more reviewed round. On the final phase, before that push, it runs a security review of the whole diff: it fixes each finding in the same round or asks you to dismiss it with a reason, and records the outcome in the PR. |
| `/github-pipeline:evaluator` | Evaluates a PR against its origin issue (in the PR's own worktree, verified at exactly the PR head), gates on branch health (CI plus your declared checks, cached per head SHA), runs a security review unless the resolver's record already covers the head it gates (never approving while a finding is open — one clears only by a fix or by your dismissal with a reason), posts a formal approve/soft-reject review, asks you about any recorded polish still undecided (apply before merge, re-plan, file as grouped follow-ups, or drop — its proposal recommended), merges per your configured policy, and hands you the `workspace-close` command. |

## The standalone tools

| Skill | What it does |
|---|---|
| `/github-pipeline:setup` | **Run this first.** Detects your project's lint/test/build commands and writes the marker blocks the resolver/evaluator read from `COMMANDS.md`/`CLAUDE.md`, plus a stack operating-profile for `CLAUDE.md`. Idempotent — safe to re-run; migrates legacy blocks. |
| `/github-pipeline:question-sweep` | Reconciles the project's open questions between its docs and the GitHub `question`-issue tracker (the registry of record): files the untracked ones, flags docs left stale by an answered question, and repairs the doc↔issue back-links. Reports first, applies on confirmation. |
| `/github-pipeline:question-resolver` | Assisted closing of one open `question` issue: evaluates it and its thread against the project docs, records your approved decision as a durable `<!-- question-decision:v1 -->` comment, offers to close the issue, and **proposes** the doc fold-back. It never decides for you and never edits docs. |
| `/github-pipeline:doc-reviewer` | Reviews one project doc against its bundled authoring guide and offers to apply the findings you accept. |
| `/github-pipeline:requirements-gatherer` | Interactively gathers the requirements for one issue: suggests grounding docs from your doc catalogue (you confirm or add), elicits an enumerated requirement set with you until approved, then appends them to the issue's `## Definition of done` — each bullet citing its source doc by durable anchor, or recorded as operator-elicited when no doc covers it. Detail stays in the docs; the issue only cites. |
| `/github-pipeline:workspace-open` | Opens the work workspace for an issue: creates or adopts its GitHub-linked branch ("create a branch for this issue"), creates the worktree under `.worktrees/`, runs your worktree-setup hooks, and prints the path to start the next session in. |
| `/github-pipeline:workspace-close` | Releases a workspace (branch or issue number): runs your worktree-teardown hooks, then removes the worktree — gated on dirty/unpushed state, never a silent discard. The routine last step after a merge, and the one reclamation path for abandoned workspaces. |
| `/github-pipeline:review-report` | Reports how the review loop has behaved across a repo's PRs (merged by default): every reviewer finding by outcome (fixed, wrong, intentional, didn't matter), the review runs each phase needed to pass, and the share needing five or more, read from the loop comments and `## Polish` ledgers the pipeline already posts. Read-only — it writes nothing anywhere. Comments written before the record format are reported in their own `legacy` column, never mixed in. |
| `/github-pipeline:coordinator` | Drives one issue — or a whole Epic and its stories — through the stages from one session, run from your project root. It works out where the issue stands from GitHub, writes each stage's brief, and starts every stage in its own fresh session in the right worktree when you say go (in the plain CLI it prints the command and folder instead). It opens, syncs and closes worktrees on your confirmation, checks each stage's claims against GitHub, and asks you when a stage's handoff disagrees. Stage cards show up in its chat too, and you can answer in either place. |

All tools end with a plain summary, not a handoff.

**The workspace lifecycle** (v3): plan first, then `workspace-open <issue>`, then start the
resolver session **inside the worktree it prints** — the resolver and evaluator verify they're in
the right checkout (and politely refuse anywhere else) instead of creating worktrees themselves.
After the evaluator merges, its handoff hands you the `workspace-close` command; run it to release
the worktree (it runs your teardown hooks and refuses to discard dirty or unpushed work).

The report-then-apply tools change nothing without showing you the proposal first. The three that edit
tracked files (`setup`, `question-sweep`, `doc-reviewer`) stage their edits in a worktree and offer
the landing (commit + push + PR) as one final gate — decline it and nothing is committed, with the
workspace path and ready-to-run commands printed instead.

`skills/_shared/` holds the cross-skill contracts (handoff schema, the plan-summary block,
Definition-of-done annotations, the epic → story → slice hierarchy, the open-question contracts, the
worktree hook-block format and the doc-catalogue format); `scripts/` holds the Python scripts
the skills invoke — the `gh`/git executors, `workspace.py`, `branching.py`,
`parse.py`, and one `prep_*.py` state-assembly script per skill.

## Requirements

- [`gh`](https://cli.github.com), authenticated (`gh auth status`).
- `git` and `python3` (≥ 3.9) on `PATH`. The scripts are **stdlib-only** — nothing to install, no
  virtualenv, no lockfile.

## Install

```
/plugin marketplace add danwashusen/claude-github-pipeline
/plugin install github-pipeline@reactive-tools
```

Then invoke any skill by its namespaced name, e.g. `/github-pipeline:drafter`, or just describe the
task ("file an issue for…", "plan #142", "resolve #287") and Claude will pick the skill. The
standalone tools except `setup` are explicit-invocation only.

## Conventions a consuming repo should provide

These skills are **convention-driven** rather than fully parameterised. They degrade gracefully
when a convention is absent, but work best when the repo provides:

- **A write-protected default branch.** Everything the pipeline changes lands through a PR. All
  work happens in worktrees under `.worktrees/`, opened by `workspace-open` and released by
  `workspace-close`. The pipeline reads its gate configuration from the checkout the session runs
  in — so a config change is testable before you merge it, and every run reports which checkout,
  branch, and commit supplied it.
- **Epic integration branches** named `epic/<N>-<slug>` (`workspace-open` creates them; resolver
  and evaluator classify Epic and story PRs by this pattern).
- **Test / build / static-check commands** declared in `CLAUDE.md` or `COMMANDS.md` inside these
  marker blocks, which the resolver and evaluator read to learn how to test and gate your project:
  - `<!-- issue-resolver-test-target -->`, `<!-- issue-resolver-fast-checks -->`,
    `<!-- issue-resolver-canonical-suite -->`
  - `<!-- pr-evaluator-static-checks -->`, `<!-- pr-evaluator-test-target -->`,
    `<!-- pr-evaluator-escalation-labels -->`, `<!-- pr-evaluator-merge-policy -->` (per-PR-type
    `ask | auto`; default `ask`), `<!-- pr-evaluator-sensitive-paths -->` (paths that always need a
    person: a PR touching one, or changing the block itself, always merges through the operator's
    gate) — plus the legacy `<!-- pr-evaluator-health-checks -->`, which setup offers to split and
    migrate.

  You don't have to write these by hand — run **`/github-pipeline:setup`** and it detects your
  project's commands, proposes drafts, and writes the blocks idempotently.
- **Worktree hooks** (optional) — `<!-- worktree-setup -->` / `<!-- worktree-teardown -->` blocks
  declaring the commands that provision and release per-worktree resources (a simulator, a port, a
  scratch database). Setup runs at workspace-open and again on every resolver/evaluator session
  entry, so the commands must be idempotent. The block in the checkout the command runs in is what
  runs — committed or not — so you can test a hook change before merging it.
- **An open-question marker convention** (optional) — `<!-- drafter-open-question-markers -->`
  tells the drafter and planner how this repo marks unresolved open questions; without it they fall
  back to built-in heuristic cues.
- **A stack operating-profile** (optional, setup-authored) — setup proposes a
  `<!-- claude-code-stack-profile -->` block in `CLAUDE.md`: concise guidance on running your stack
  efficiently in a Claude Code session (backgrounding slow commands, logging verbose output instead
  of flooding context), auto-loaded into every session. It's yours to edit — re-running setup
  re-ingests your edits rather than overwriting them.
- **A doc catalogue** (optional, setup-authored) — a `<!-- doc-catalogue -->` block in
  `docs/README.md` naming the documents that ground the pipeline's work, one per line with a role, an
  authority (`binding` — contradicting it is a blocker — or `informative`), and a one-line summary. A
  line can also name a directory (`docs/architecture/`) to declare every document directly inside it
  under one role and authority.
  Every stage that checks work against your docs — drafter, slicer, planner, resolver, evaluator — reads
  it, as do the requirements-gatherer and question-resolver; the paths are **yours**, so a PRD at
  `docs/product/requirements.md` works as well as one at `docs/prd.md`. Setup derives a first draft
  from your `docs/README.md` and re-ingests your edits on re-run. Without it, those skills ground on
  no documents and say so — nothing is guessed.

The skills post and read durable marker comments: `<!-- implementation-plan:v1 -->` (planner — the main
comment of three; the build and citation sections post as `<!-- implementation-plan-part:v1:<slug> -->` parts beside it),
`<!-- issue-research:v1 -->` (researcher), `<!-- epic-delivery-log:v2:story:<N> -->` (evaluator-written,
planner-read, one comment per shipped story; `:v1` is its read-forever legacy tier), `<!-- pr-evaluator-health-cache:v1 -->` (evaluator), `<!-- question-decision:v1 -->`
(question-resolver), and the `<!-- open-question-links:v1 -->` build-issue body section (drafter).

## Notes for maintainers

- **Path resolution.** Bundled scripts and reference files are referenced from skill bodies as
  `${CLAUDE_PLUGIN_ROOT}/…`, which Claude Code substitutes inline to the real install path before
  the model reads it. The path changes on every plugin update and is read-only — never write state
  there (session scratch goes to `/tmp/gh-<skill>-<N>/`). Where a path must reach a *raw-read*
  reference file or a dispatched sub-agent prompt (which are not substituted), the orchestrating
  skill resolves it and passes it as an explicit placeholder.
- **Plugin namespace.** Skills resolve as `/github-pipeline:<skill>`; the cross-session handoff
  commands are namespaced to match. If you rename the plugin, update those references.
- **Tests.** `python3 tests/run.py` runs the offline suite (stdlib `unittest`, a fixture-replaying
  `gh` shim, a temp-repo git sandbox — no network, no live repo). It must pass on macOS and Linux;
  see `tests/README.md`. `CLAUDE.md` documents the prompt-side validators.

## License

MIT
