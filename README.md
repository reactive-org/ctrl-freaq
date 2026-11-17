# ctrl-freaq

A Claude Code plugin that runs a complete GitHub issue/PR workflow through the `gh` CLI — seven
session-per-stage skills that hand off to one another, plus ten standalone tools,
backed by stdlib-only Python scripts that do every deterministic step (fetching, parsing, state
derivation, worktree lifecycle, and every write), plus an optional read-only board mod that draws a
coordinator run as an overview pane and, on Desktop, a Metro Wave band above the chat input.

```
draft ──▶ research ──▶ slice ──▶ design ──▶ plan ──▶ resolve ──▶ evaluate
 (file)   (cite)       (cut)    (design)  (approach) (code+PR)  (review+merge)
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
planner. Design is the third detour: for an issue whose user interface changes and that has no plan yet,
the planner asks before drafting whether to design it first. On yes it routes you to the designer, which
agrees every screen state with you on a working prototype, records the design you approve on the issue,
and hands back to the planner, whose plan then builds to that design.

## The pipeline

| Skill | What it does |
|---|---|
| `/ctrl-freaq:drafter` | Turns informal feedback into **one** well-structured issue — bug, feature, story, Epic or `question` — and files it. Never silently absorbs an unresolved open question: each is matched against the `question`-issue registry, filed if untracked, and recorded on the build issue. Decomposition is the slicer's, so a filed Epic hands off there for its stories. |
| `/ctrl-freaq:researcher` | Web-researches version/API/migration questions and posts a dated, cited dossier on the issue — or declines outright when the issue carries no currency risk. |
| `/ctrl-freaq:slicer` | Cuts one issue into ordered, operator-approved children and files them as native sub-issues, so the parent's own progress rollup tracks delivery. One operation at two altitudes: a story or standalone issue becomes **deliverable slices** (milestones a stakeholder would track on their own, each served by one or more plan phases — or no slices at all, when the issue is one milestone), an Epic becomes **stories** (each independently shippable, with its own branch and PR). It can also promote an issue to an Epic before cutting it, and draw an Epic around issues that already exist. Report-then-apply: nothing is written until you confirm the whole cut, and it refuses rather than guessing when your repo declares no grounding docs. |
| `/ctrl-freaq:designer` | Designs an issue's user interface with you before it is planned or built: iterates on a working prototype built and run the way your repo's `<!-- ui-prototype -->` block says (or, with no block, the way you tell it this session, then points you at `setup` to record that), agrees every state a user can meet — empty, loading and error included — captures each one, and on your approval records the target as a `<!-- ux-design:v1 -->` design record on the issue, then hands back to the planner. A reusable UX convention you state while designing is offered, on your confirmation, as a follow-up issue that updates your `role: ui-design` doc. When your block declares where your existing prototypes live, it starts from the one that matches the issue (or asks you when none or several do) and adopts it as the design target instead of rebuilding it. Writes nothing tracked from your project root: a tracked prototype it builds or edits lives on the issue's own branch, in the worktree `workspace-open --design` opens. |
| `/ctrl-freaq:planner` | Works out the implementation approach, grounded in repo precedent + project docs at a recorded commit SHA, and posts a reviewed `<!-- implementation-plan:v1 -->` comment. For an issue whose user interface changes, with no design record and no plan, it first asks whether to detour to the designer; with an approved design record, the plan's `## UI decisions` cite it as the design target and name the phase whose build is checked against it. Epic plans pin cross-story contracts and declare which stories can run concurrently; each story is planned once the stories its `start` edges name have merged, per the dependency graph the epic plan declares. |
| `/ctrl-freaq:resolver` | Implements one issue against its verified plan **in the worktree you opened with `workspace-open`** (the session starts inside it and is verified there), opens/continues a PR, works through the plan's phases in one session — pausing wherever the plan marks a checkpoint, a warning sign fires, or the shipped diff touches a sensitive path or reaches further than the plan's level accounted for, or — when the plan cites an approved design — at the `experience` pause after the phase that first builds it, where it captures the build's states, has a context-blind comparison check each against the design record, and posts each difference as a design review thread showing the approved design and the build side by side (on the last or only phase it runs at the final pass instead, with no pause) — it prints a summary of what changed, the decisions and deviations from the plan, and what to check (each linked into the phase's changes), then raises a **Review** card — a human review is required before the next phase: **Reviewed, continue**, **Reviewed, changes required** (it fixes what your notes and open review threads ask for, then asks again), **Review with Claude** (it prints the command that reviews that pause's threads with you in a chat session, and the folder to run it in — or the coordinator offers to start that session — then asks again) or **End session**. It also watches its own context usage against the auto-compact point: from 65% it finishes the current phase and ends, from 90% it stops mid-phase and hands the unfinished phase to the next session (`--pause` ends it after every phase) — projects Definition-of-done ticks as phases ship, and loops with code review while it keeps removing defects — reviewing each non-final phase's own delta against the plan, and the whole diff on the final phase, at the level the plan sets per phase from the change's blast radius (raised a step when the diff touches a declared sensitive path, or a context-blind judgment of the diff finds it reaching a dependent, contract or seam the plan's level did not account for), stating the change's risk and how to revert it in the PR; it fixes what its own change introduced, polish included, has a context-blind check confirm any defect-sized finding it would dismiss, and before handing off asks you what to do with the rest (apply now, re-plan, file as grouped follow-ups, or drop) and which follow-ups to file, applying your picks in one more reviewed round. On the final phase, before that push, it runs a security review of the whole diff: it fixes each finding in the same round, or records a proposed dismissal for you to answer on its review thread at evaluation, and records the outcome in the PR. |
| `/ctrl-freaq:evaluator` | Evaluates a PR against its origin issue (in the PR's own worktree, verified at exactly the PR head), gates on branch health (CI plus your declared checks, cached per head SHA), runs a security review unless the resolver's record already covers the head it gates (never approving while a finding is open — one clears only by a fix or by your dismissal with a reason), compares the build with the approved design record again at the head it gates when the plan cites one, posts the code most worth your look — sensitive or unplanned paths, the sites its blast-radius judgment cites, open security findings, each state the build shows differently from the design, with the approved design and the build side by side (a design difference clears only with a fix that matches it or a revised design the planner adopts) — as review threads on the PR and won't approve until you've resolved each one in GitHub (replying `dismiss: <reason>` on a security finding's thread, then resolving it, is its dismissal; the pipeline never resolves a thread itself), posts a formal approve/soft-reject review, merges only the exact head it gated (a head pushed after its review, or a base that gained commits the gated head does not contain, skips the merge with the reason named — `head-moved` / `base-moved` — and a fresh run re-gates), asks you about any recorded polish still undecided (apply before merge, re-plan, file as grouped follow-ups, or drop — its proposal recommended), merges per your configured policy, and hands you the `workspace-close` command. |

## The standalone tools

| Skill | What it does |
|---|---|
| `/ctrl-freaq:setup` | **Run this first.** Detects your project's lint/test/build commands and writes the marker blocks the resolver/evaluator read from `COMMANDS.md`/`CLAUDE.md`, plus a stack operating-profile for `CLAUDE.md`, your review guidance, a sensitive-path declaration (paths whose PRs always need your merge approval), for a repo with a user-facing UI a `<!-- ui-prototype -->` block saying how you prototype it, and, if you want one, a rigor level, creating its `rigor:*` labels. Idempotent — safe to re-run; migrates legacy blocks. |
| `/ctrl-freaq:question-sweep` | Reconciles the project's open questions between its docs and the GitHub `question`-issue tracker (the registry of record): files the untracked ones, flags docs left stale by an answered question, and repairs the doc↔issue back-links. Reports first, applies on confirmation. |
| `/ctrl-freaq:question-resolver` | Assisted closing of one open `question` issue: evaluates it and its thread against the project docs, records your approved decision as a durable `<!-- question-decision:v1 -->` comment, offers to close the issue, and **proposes** the doc fold-back. It never decides for you and never edits docs. |
| `/ctrl-freaq:doc-reviewer` | Reviews one project doc against its bundled authoring guide and offers to apply the findings you accept. |
| `/ctrl-freaq:requirements-gatherer` | Interactively gathers the requirements for one issue: suggests grounding docs from your doc catalogue (you confirm or add), elicits an enumerated requirement set with you until approved, then appends them to the issue's `## Definition of done` — each bullet citing its source doc by durable anchor, or recorded as operator-elicited when no doc covers it. Detail stays in the docs; the issue only cites. |
| `/ctrl-freaq:workspace-open` | Opens the work workspace for an issue: creates or adopts its GitHub-linked branch ("create a branch for this issue"), creates the worktree under `.worktrees/`, runs your worktree-setup hooks, and prints the path to start the next session in. `--design` (the planner's design detour, when the prototype lives on the issue's branch) routes the next session to the designer. |
| `/ctrl-freaq:workspace-close` | Releases a workspace (branch or issue number): runs your worktree-teardown hooks, then removes the worktree — gated on dirty/unpushed state, never a silent discard. The routine last step after a merge, and the one reclamation path for abandoned workspaces. |
| `/ctrl-freaq:review-report` | Reports how the review loop has behaved across a repo's PRs (merged by default): every reviewer finding by outcome (fixed, wrong, intentional, didn't matter), the review runs each phase needed to pass, and the share needing five or more, read from the loop comments and `## Polish` ledgers the pipeline already posts. Read-only — it writes nothing anywhere. Comments written before the record format are reported in their own `legacy` column, never mixed in. |
| `/ctrl-freaq:review` | Reviews a PR's open review threads with you in the chat, run in the issue's worktree as `/ctrl-freaq:review #<N>` (the issue, not the PR). It walks you through every thread still waiting on a person, one at a time — security findings first, the routine batch last — each with a short brief, a code excerpt and a link to the Files view, and lets you discuss the code before you answer. Each answer (accept, changes required, dismiss a security finding, or file a follow-up issue) is recorded in its thread at once, where the evaluator reads it. It never resolves a thread; when you ask for changes it ends by naming the resolver as the next step. `/ctrl-freaq:review #<N> --phase <P>` reviews only what one phase's resolver review pauses posted, with that pause's summary shown first, and ends by telling you which answer to give on the resolver's Review card. |
| `/ctrl-freaq:coordinator` | Drives one issue — or a whole Epic and its stories — through the stages from one session, run from your project root. It works out where the issue stands from GitHub, writes each stage's brief, and starts every stage in its own fresh session in the right worktree when you say go (in the plain CLI it prints the command and folder instead). It follows a planner's design detour to the designer, started in the issue's own worktree when one is open, else in your project root; when the prototype lives on the issue's branch, the same go-ahead opens that worktree first. When the resolver's Review card asks for **Review with Claude**, it offers a one-click start for that review session too, in the resolver's worktree, started on your click. It opens, syncs and closes worktrees as routine steps it announces in one line (the workspace tools' safety cards still ask you), checks each stage's claims against GitHub, and asks you when a stage's handoff disagrees. Everything you must decide or do is a card or a button, and each stage's summaries are relayed into its chat. It drives an epic's stories concurrently as the epic plan allows, offers to archive finished sessions once nothing in flight needs their history, and can draw an optional read-only board: a `ctrl-freaq-overview` pane with the run's text board on every surface, listing a single issue's plan phases, or an epic story's while it is being built (one progress line for its other open stories), with the next phase expanded and, while it is being built, a generated summary of it, and a `ctrl-freaq-board` band above the chat input that draws an animated Metro Wave timeline of the run on Desktop, starting each stage where its session started (a recorded start is kept 90 days, after which that stretch draws as a wait), with a hover tooltip on each segment, a hover legend in its top-right corner, and hover cards on its line labels that light the matching overview row (`boardStyle`: `metro`, or `text` for a one-line summary); `/ctrl-freaq-overview` and `/ctrl-freaq-board` show or hide each. Stage cards show up in its chat too, and you can answer in either place. |

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
worktree hook-block format, the doc-catalogue format, the PR-body security-review record
(`security-review.md`), the review-loop record (`review-loop-record.md`), the design record and
`ui-prototype` block (`design-record.md`) and the design check (`design-check.md`)); `scripts/` holds the Python
scripts the skills invoke — the `gh`/git executors, `workspace.py`, `branching.py`, `parse.py`,
`blast_radius.py` (the resolver's review level), `sensitive_paths.py`, `rigor.py` (the rigor level), `review_report.py`,
`review_guidance.py`, `gitattributes.py` (setup's write path for a `.gitattributes`
`linguist-generated` block), `design_record.py` (the design record's and `ui-prototype` block's one
reader), and the `prep_*.py` state-assembly scripts: one per skill except `doc-reviewer`, which has none
(sixteen for seventeen skills, `prep_review_report.py`, `prep_review.py` and `prep_designer.py` among them).

## Requirements

- [`gh`](https://cli.github.com), authenticated (`gh auth status`).
- `git` and `python3` (≥ 3.9) on `PATH`. The scripts are **stdlib-only** — nothing to install, no
  virtualenv, no lockfile.
- Optional: Claude Code ≥ 2.1.308 for the read-only board mod (its Desktop band above the chat input needs it), whose `boardStyle` setting picks what the band above the chat input draws: the animated Metro Wave drawing (`metro`, the default) or a one-line summary (`text`). It records each pipeline stage session's start in its own store, which nothing but the board reads. Nothing else depends on it.

## Install

```
/plugin marketplace add reactive-org/ctrl-freaq
/plugin install ctrl-freaq@reactive-tools
```

Upgrading from 4.x: uninstall `github-pipeline@reactive-tools` and install
`ctrl-freaq@reactive-tools`; every skill is now `/ctrl-freaq:<skill>`. The next `setup` run offers
to migrate the `COMMANDS.md` header block to its new name.

Then invoke any skill by its namespaced name, e.g. `/ctrl-freaq:drafter`, or just describe the
task ("file an issue for…", "plan #142", "resolve #287") and Claude will pick the skill. The
standalone tools except `setup` and `review` are explicit-invocation only; `review` runs when you type it
or click to start it from the `coordinator`.

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

  You don't have to write these by hand — run **`/ctrl-freaq:setup`** and it detects your
  project's commands, proposes drafts, and writes the blocks idempotently.
- **Worktree hooks** (optional) — `<!-- worktree-setup -->` / `<!-- worktree-teardown -->` blocks
  declaring the commands that provision and release per-worktree resources (a simulator, a port, a
  scratch database). Setup runs at workspace-open and again on every resolver/evaluator session
  entry, so the commands must be idempotent. The block in the checkout the command runs in is what
  runs — committed or not — so you can test a hook change before merging it.
- **A prototyping convention** (optional, setup-proposed for a repo with a user-facing UI, yours to
  write) — a `<!-- ui-prototype -->` block in `CLAUDE.md` or `COMMANDS.md`, in your own words: where a
  prototype lives, what it is built with, how to run it, and how to capture each screen state. The
  designer builds and captures prototypes as it says and the resolver captures the real build the same
  way; no skill prompt prescribes a tool or a location. Without it the designer asks you each session. An
  optional `**Existing prototypes:**` line of backticked git glob patterns names the prototypes you already
  keep, so the designer can start from the one that matches an issue.
- **An open-question marker convention** (optional) — `<!-- drafter-open-question-markers -->`
  tells the drafter and planner how this repo marks unresolved open questions; without it they fall
  back to built-in heuristic cues.
- **A stack operating-profile** (optional, setup-authored) — setup proposes a
  `<!-- claude-code-stack-profile -->` block in `CLAUDE.md`: concise guidance on running your stack
  efficiently in a Claude Code session (backgrounding slow commands, logging verbose output instead
  of flooding context), auto-loaded into every session. It's yours to edit — re-running setup
  re-ingests your edits rather than overwriting them.
- **Review guidance** (optional, setup-seeded, yours to write) — a `<!-- issue-resolver-review-guidance -->`
  block in your config file: what failure looks like in this repo, what to skip, how many nits, what
  evidence a finding needs. The resolver passes it to its reviewer. Re-running setup keeps your text
  and proposes entries for the finding classes that keep coming back in your review history.
- **A doc catalogue** (optional, setup-seeded) — a `<!-- doc-catalogue -->` block in
  `docs/README.md` naming the documents that ground the pipeline's work, one per line with a role, an
  authority (`binding` — contradicting it is a blocker — or `informative`), and a one-line summary. A
  line can also name a directory (`docs/architecture/`) to declare every document directly inside it
  under one role and authority.
  Every stage that checks work against your docs — drafter, slicer, planner, resolver, evaluator — reads
  it, as do the requirements-gatherer and question-resolver; the paths are **yours**, so a PRD at
  `docs/product/requirements.md` works as well as one at `docs/prd.md`. Setup derives a first draft
  from your `docs/README.md`; after that, any session whose change adds, moves, removes or rewrites a
  listed document updates the catalogue in the same PR — the resolver, doc-reviewer, question-sweep
  and setup check it with a script before committing, and ordinary sessions follow the
  `<!-- doc-catalogue-upkeep -->` rules setup writes into your `CLAUDE.md`. Adding or removing a
  `binding` entry, or changing any entry's authority, always asks you first, and the evaluator
  re-checks every PR's catalogue changes. Without a catalogue, those skills ground on no documents and
  say so — nothing is guessed.
- **Citable, merge-safe docs** (recommended) — every section a skill may cite carries a stable slug
  ID in its heading, prefixed by its doc (`## R-dry-run-default: …` in the PRD, `A-` architecture,
  `C-` constitution, `U-` ui-design), and the skills cite it as `PRD §R-dry-run-default`. A cited
  section is then one grep away, and two branches adding sections never collide the way `§12` does.
  The [authoring guides](docs/guides/README.md) say what each doc holds; the
  [Markdown tooling guide](docs/guides/markdown-tooling.md) covers linting them with mdsmith, the
  slug-ID guard, migrating numbered sections, lists instead of tables for growing content, and the
  merge driver.
- **A rigor level** (optional, setup asks, yours to choose) — a `<!-- ctrl-freaq-rigor -->` block in your
  config file sets how many review steps the pipeline runs, separately from model effort: `full` (every
  step, today's behaviour), `medium` (keeps the steps that find defects but cuts polish handling and
  repeat rounds: at most 4 review rounds a pass, polish listed rather than fixed, no blast-radius
  judgment) or `light` (for prototypes and hobby work: a `low` review of at most 2 rounds, a cold read
  on the final phase only, no polish card, and review threads for security findings and design differences only). The block
  holds `- default: <level>` and `- floor: <level>`; without it every session runs at `full`. A
  `rigor:full`, `rigor:medium` or `rigor:light` label on an issue or its epic picks that issue's level,
  never below the floor, and the strictest wins (setup creates the labels; the coordinator asks once,
  at an issue's first run, when the floor allows a choice). A malformed block or a label set that can't
  be read runs at `full`, and so does a branch whose block differs from its base's (changed on the branch,
  or since on the base); a pass touching a sensitive path runs on `full` rules, and so does the evaluation of
  another author's PR below `full` whose author the repo does not trust. An epic whose label can't
  be reached (no sub-issue relation, or a story linked only by a checklist the read didn't see) is
  reported in a notice, not counted. A pass a round limit ends pauses the phase and offers one follow-up to re-check the fixes no
  later round checked, and a defect it leaves unfixed is filed as a follow-up too — each one you confirm. No
  level weakens the health gate, the security review's blocking rule, the pinned merge or the
  sensitive-path merge gate. The PR's `Rigor:` line records what each pass ran, and `review-report`
  splits its figures by level.

The skills post and read durable marker comments: `<!-- implementation-plan:v1 -->` (planner — the main
comment of three; the build and citation sections post as `<!-- implementation-plan-part:v1:<slug> -->` parts beside it),
`<!-- issue-research:v1 -->` (researcher), `<!-- epic-delivery-log:v2:story:<N> -->` (evaluator-written,
planner-read, one comment per shipped story; `:v1` is its read-forever legacy tier), `<!-- pr-evaluator-health-cache:v1 -->` (evaluator), `<!-- question-decision:v1 -->`
(question-resolver), `<!-- review-loop:v1 -->` (the resolver's review-loop comment, read by
`review-report`), `<!-- ux-design:v1 -->` (the designer's design record, read by the planner,
resolver, evaluator and review, and counted only from a trusted author), and the `<!-- open-question-links:v1 -->` build-issue body section (drafter).

## Notes for maintainers

- **Path resolution.** Bundled scripts and reference files are referenced from skill bodies as
  `${CLAUDE_PLUGIN_ROOT}/…`, which Claude Code substitutes inline to the real install path before
  the model reads it. The path changes on every plugin update and is read-only — never write state
  there (session scratch goes to `/tmp/gh-<skill>-<N>/`). Where a path must reach a *raw-read*
  reference file or a dispatched sub-agent prompt (which are not substituted), the orchestrating
  skill resolves it and passes it as an explicit placeholder.
- **Plugin namespace.** Skills resolve as `/ctrl-freaq:<skill>`; the cross-session handoff
  commands are namespaced to match. If you rename the plugin, update those references (5.0.0 renamed `github-pipeline` to `ctrl-freaq`).
- **Tests.** `python3 tests/run.py` runs the offline suite (stdlib `unittest`, a fixture-replaying
  `gh` shim, a temp-repo git sandbox — no network, no live repo). It must pass on macOS and Linux;
  see `tests/README.md`. `claude plugin test .` runs the board mod's tests and
  `claude plugin validate .` checks the manifests and the mod (Claude Code ≥ 2.1.308); CI runs
  both. `CLAUDE.md` documents the prompt-side validators.

## License

MIT
