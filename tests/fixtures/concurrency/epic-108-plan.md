## Story breakdown
- #111 "The coordinator's prep reports where every story of an epic stands" — per-story `stage` and `next` on the epic facts, derived the way the focused unit's are, additive to the focus unit and `--story`; script and offline suite only
- #112 "An epic plan says which stories can be planned and built at the same time" — the planner's epic route emits the dependency graph, the file-overlap read and the concurrent sets, routes shared-doc edits to the finalization story, and the plan reviewer checks them; delivers the import-only reader of that block
- #113 "The coordinator asks only what waits on the operator, and shows it all in its own chat" — cards or buttons for every operator decision and task (the no-session-start fallback included), summaries relayed by the stage brief, and open, close and sync as routine announced steps while the workspace scripts' safety cards stay
- #114 "The workspace-trust prompt is investigated, and the operator is told what they can set" — researcher first, then the three routes' findings recorded with citations, the operator told on a card what they can set themself, and a `workspace-open` layout change only if the findings call for one
- #115 "The coordinator follows the epic plan's concurrency and archives finished sessions safely" — reads the graph through #112's reader, offers concurrent starts once a story's consumed contracts have merged, drives several in-flight stories, retires single focus, re-gates a PR whose base moved, and archives stage sessions at a point the cascade can't hurt
- #116 "Stretch: a UI mod shows each story's stage as the run progresses" — a read-only mod over #111's per-story stages; stretch goal, last before #117, and it may close as deferred to a follow-up
- #117 "Finalize the coordinator-runs epic" — every shared-doc edit routed here (PRD, architecture, CLAUDE.md, README), the one version bump, and verification of every epic DoD item, grounded on the delivery log

## Story contracts
- #111 — delivers: `per-story stages`: every `epic.stories[]` entry of `prep_coordinator.py`'s facts carries `stage` (the same closed stage set, derived by the same `derive_unit` the focused unit uses) and `next` (the same `kind` / `command` / `folder` shape as top-level `next`), for every epic with stories; the focus unit's `stage` / `next` and `--story` unchanged
            — consumes: (none)
- #112 — delivers: `concurrency declaration`: an epic plan's `## Integration strategy` carries the dependency graph, the file-overlap read and the named concurrent sets. Each graph edge names a story, the story it waits on, its kind (`contract`, a `## Story contracts` name; `file overlap`; `order`; or `merge order`), what it gates (the start, the build only, or the merge only), and any condition it holds under. It also delivers `concurrency reader`: an import-only module that takes an epic plan's text and returns, per story, its start, build and merge gates (the stories each waits on, with the edge's condition) and the concurrent set it belongs to. Neither prompt nor script judges concurrency from prose
            — consumes: (none)
- #113 — delivers: `routine workspace steps`: in the coordinator's router and spine, open, close and the epic sync run without a consent card of their own, each announced in one line, with an open or sync folded into the go-ahead of the stage it precedes; the workspace scripts' safety decisions still render as cards. It also delivers `operator-task rule`: everything that waits on the operator is a card or a `wrap_up` button, and a stage start is a button whenever a stage's message woke the coordinator
            — consumes: (none)
- #114 — delivers: `worktree layout`: the work-worktree path every workspace tool and the coordinator compose — `.worktrees/<branch>` confirmed, or the new layout applied in `workspace-open`, `workspace-close` and the coordinator's prep alike — plus `trust guidance`, the recorded findings that name what the operator can set themself
            — consumes: (none)
- #115 — delivers: (none consumed)
            — consumes: `per-story stages` (#111), `concurrency declaration` and `concurrency reader` (#112), `routine workspace steps` and `operator-task rule` (#113), `worktree layout` and `trust guidance` (#114)
- #116 — delivers: (none consumed)
            — consumes: `per-story stages` (#111)
- #117 — delivers: (none consumed)
            — consumes: (none)

## Integration strategy

The stories converge on `epic/108-<slug>`, which `workspace-open #108` creates off `origin/main` (the coordinator opens it; no epic branch exists yet). Each story branches off the epic branch with its PR based on it, the evaluator squash-merges it there, and the resolver's epic route lands the accumulated diff on `main` through the integration PR once every story is closed (or #116 is deferred).

### Dependency graph

| Story | Waits on | Kind |
|---|---|---|
| #111 | — | — |
| #112 | — | — |
| #113 | — | — |
| #114 | #111 | file overlap on `scripts/prep_coordinator.py`; gates the build only; condition: #114's dossier calls for a layout change |
| #115 | #111 | contract `per-story stages` |
| #115 | #112 | contract `concurrency declaration`, `concurrency reader` |
| #115 | #113 | contract `routine workspace steps`, `operator-task rule` |
| #115 | #113 | file overlap on the coordinator router and spine |
| #115 | #114 | contract `worktree layout`, `trust guidance` |
| #116 | #111 | contract `per-story stages` |
| #116 | #112, #113, #114 | order: the operator put #116 last, so it starts with wave 2, not as soon as #111 merges |
| #116 | #115 | merge order: merges after #115 (stretch goal, last before #117); does not gate #116's start |
| #117 | #111, #112, #113, #114, #115, #116 | order; gates the start; condition: #116 may instead be deferred |

Each row's kind column says what the edge gates, in the shape `## Story contracts` pins for #112's `concurrency declaration`. Unless a row says otherwise, a `contract`, `file overlap` or `order` edge gates the start: the waiting story is planned and built only after the story it waits on has merged into the epic branch. A `merge order` edge gates only the merge.

### File overlap

Read at directory or module grain; each story's plan names its own files.

| Surface | Stories | Resolution |
|---|---|---|
| `scripts/prep_coordinator.py`, `tests/test_prep_coordinator.py` and its fixtures | #111, #115; #114 only if the layout changes (the coordinator's worktree-path check) | #115 waits on #111 by contract. A layout-changing #114 waits on #111 by file overlap before it builds (`### Dependency graph`). The consent-card wording in the file's docstring and comments is #115's to update; #113 edits no part of this file |
| `skills/coordinator/**`, `tests/test_coordinator_routing.py` | #113, #115 | #115 waits on #113. #111, #114 and #116 edit no coordinator prose |
| `skills/planner/**`, `tests/test_planner_routing.py`, the new concurrency reader module | #112 | no overlap |
| `scripts/workspace.py`, `prep_workspace_open.py`, `prep_workspace_close.py`, `skills/workspace-open/**`, `skills/workspace-close/**` | #114 | no overlap. #114 records its findings under `skills/workspace-open/`, never in the shared docs or `skills/coordinator/` |
| `skills/_shared/asking-the-user.md`, `tests/test_asking_the_user.py` | #113 at most | no overlap |
| the mod's own files | #116 | no overlap. A manifest entry it needs, if any, is a key of its own, never the version |
| `docs/prd.md`, `docs/architecture.md`, `CLAUDE.md`, `README.md`, `.claude-plugin/plugin.json` and `marketplace.json` (version) | #117 | routed here (`## Architecture decisions`) |

### Concurrent sets

- **Wave 1: #111, #112, #113, #114.** Plan all four at once in the epic worktree, then build them at once, each in its own worktree. #114 runs `/github-pipeline:researcher #114` before its plan; its dossier says whether it is a doc-only story or a layout change. A layout-changing #114 is still planned in this wave, but builds only after #111 merges.
- **Wave 2: #115 and #116.** Both start once wave 1 has merged. #116 consumes only from #111; the order edge in `### Dependency graph` holds it to wave 2 because the operator put it last. It merges after #115, and the operator may defer it to a follow-up at any point.
- **Wave 3: #117.** It starts after #115 merges and #116 has merged or been deferred, and plans against the epic delivery log.

### Merging inside a wave

Builds run concurrently; merges into the epic branch run one evaluator session at a time. The installed evaluator (4.41.0) has no `base-moved` skip. As of 2026-10-24 that skip is #93's, on epic #92's branch, and it reaches this run only when a plugin build containing #92's integration merge is installed, not merely when #92 merges. Until then, every #108 story evaluation after the first merge into the epic branch carries a by-hand base check in its brief. Before merging, fetch and confirm that the PR head contains the current `origin/epic/108-<slug>` tip (`git merge-base --is-ancestor`). If it doesn't, treat it as `base-moved`: no merge. The resolver re-enters on the story's issue, merges the epic tip into the story branch, re-runs the gates and pushes, and a fresh evaluator session re-gates. Once the installed evaluator carries `base-moved`, its own guard replaces the hand check.

