# ctrl-freaq — Architecture (v2)

Target architecture for the v2 rewrite. Prescriptive *how*; the *what/why* is
[prd.md](prd.md), the migration is [implementation.md](implementation.md). Rules here are
deviable defaults: departing from one requires an operator-approved deviation. The
[§12 invariants](#12-invariants-registry) are not deviable.

## §1 System overview & boundaries

One session = one skill = one pass through this shape:

```
operator ─▶ Claude Code session
              SKILL.md router ─▶ prep script (python, one call) ─▶ facts block (JSON + spilled files)
                │                                                        │
                ├── reads exactly one playbook (chosen via routing table + facts)
                ├── dispatches judgment sub-agents (workspace paths in, typed results out)
                └── persists via write scripts ─▶ GitHub (issues, PRs, comments)
```

Scripts own everything deterministic; the model owns judgment; GitHub artifacts and the handoff
are the only cross-session state a skill or script reads ([prd.md §4.1](prd.md)). The one state kept
outside them is the board mod's (`hooks/`): its `$.store` stage log and orientation cache, and the
coordinator prep's `<scratch>/envelope.json` feed file it reads. That state is presentational only —
no skill or script reads it, and it never sources a stage or a gate.

**Pinned runtime:** Python ≥ 3.9 (the macOS Command Line Tools floor; **stdlib only** — no
third-party packages, no venv, no pip step), `git` ≥ 2.38, `gh` ≥ 2.40 authenticated with repo
scope. Those three are the whole dependency set for the skills and scripts (v1's shell-script dependencies retired with the
v1 scripts at S20). Scripts are executables (`#!/usr/bin/env python3`) invoked by absolute path via
`${CLAUDE_PLUGIN_ROOT}/scripts/...`; the install dir is read-only. The optional board mod under
`hooks/` is a TypeScript hooks module that Claude Code ≥ 2.1.308 runs itself, so there is nothing to
install; it sits outside the stdlib-only rule because it is not a script, and no skill or script
depends on it. The scripts' rule is unchanged.

**Portability (macOS/BSD + Linux/GNU) is a requirement, not an aspiration.** Python is how the
BSD-vs-GNU userland divergence (`sed`/`awk`/`date`/`stat` dialects) is excluded by construction.
Rules: the only external processes a script may spawn are `git` and `gh`, via `subprocess` with
argument lists — never `shell=True` — with exactly one carve-out: `workspace.py`'s hook runner
executes the consuming repo's `<!-- worktree-setup/teardown -->` commands verbatim as repo-owned
opaque shell commands, cwd'd to the workspace; no other script spawns anything else. All text
I/O pins `encoding="utf-8"` (locale defaults differ across platforms); paths go through
`pathlib` and are compared via `os.path.realpath` (macOS `/tmp` is a symlink to
`/private/tmp`); nothing assumes a case-sensitive filesystem.
Module filenames use underscores so the test suite can import them.

**Repo layout (after rewrite):**

```
scripts/
  pipelib/            # shared package: envelope, spill, decisions, sha256, subprocess runner
  workspace.py        # ensure / attach / remove / sync / gc / lint; default-branch derivation
  branching.py        # import-only: branch naming, type detection, prior-PR rows, linked branches,
                      #   ambient-branch issue detection
  parse.py            # dod | oq-links | phases subcommands (dod also reports DoD presence + other checklists; phases also reports body size, review-level coverage, and DoD coverage with --issue-body); security-coverage (an always-ok post-staging check, no decision code); import-only tracker + polish-ledger + review-loop + security-record (scan_security: record / other_sections) + blast-radius judgment (scan_blast_judgment) scanners + review-level / planned-path readers
  gh_gather.py  gh_pr_gather.py  gh_persist.py  config_block.py   # executor ports (S21)
  prep_drafter.py  prep_researcher.py  prep_slicer.py  prep_planner.py  prep_resolver.py  prep_evaluator.py
  prep_question_sweep.py  prep_question_resolver.py  prep_requirements_gatherer.py
  prep_workspace_open.py  prep_workspace_close.py   # the v3 operator-side lifecycle tools
  prep_coordinator.py # the coordinator: the issue's stage as a fact; composes the two above
  review_report.py  prep_review_report.py   # review-report: import-only core + its prep (read-only)
  review_guidance.py  # import-only: the repo's review-guidance block, flags neutralised (#99)
  blast_radius.py     # the review level per loop pass: assess (always ok, called mid-session) — the resolver's,
                      #   and the evaluator's S4.6 signals at the gated head (#145)
  phase_review.py     # the resolver's phase boundary (#155): context (usage ledger, bands, projection; always ok)
                      #   and links (phase-scoped Files-view links)
  review_threads.py   # review threads (#145): read_threads (composed by both preps), flags and narrow (evaluator
                      #   S4.6; narrow = the out-of-plan exemptions alone, #163), confirm_dismissals; state, trust
                      #   and the hunk-unchanged check
  sensitive_paths.py  # import-only: the sensitive-path declaration, its one matcher, assess (#96)
  concurrency.py      # import-only: an epic plan's concurrency declaration → per-story gates (#112)
  board_history.py    # import-only: the board's `history` fact — one gh api graphql read + its point builders (#156)
hooks/                # the optional read-only board mod (#116), run by Claude Code, not a script
  hooks.json  pipeline-board.ts  board-model.ts  metro-wave.ts  pipeline-board.test.ts  metro-wave.test.ts  fixtures/
skills/
  <name>/SKILL.md     # thin router (§9)
  <name>/playbooks/   # one file per behaviorally distinct flow (§5)
  <name>/references/  # judgment sub-agent prompts + contract renderings
  _shared/            # cross-skill contracts (external ones unchanged; see §11)
tests/
  run.py  shim/gh  fixtures/  test_*.py            # stdlib unittest; §10
```

## §2 Component model & where logic lives

Each layer has one job; the dependency rules between them are the architecture.

| Layer | Job | Must never |
|---|---|---|
| `scripts/pipelib/` | Shared envelope/spill/decision/subprocess primitives | Contain skill-specific logic |
| `scripts/*.py` | All deterministic work: fetch, parse, derive state, select refs, name branches, run hooks, write to GitHub | Author prose, classify meaning, make judgment calls |
| `SKILL.md` router | Run prep, apply the routing table, enforce universal invariants, emit the handoff | Contain per-type flow bodies or raw `gh`/`git` write commands |
| Playbooks | The behavioral flow for one route, read on demand | Do ref arithmetic, restate contracts owned by `_shared`, duplicate the router's invariants |
| Judgment sub-agents | Isolated reasoning over prepared inputs (audit, distill, review, select) | Write to GitHub, ask the user, receive raw refs instead of workspace paths |
| `_shared/` contracts | Single source of truth for cross-skill schemas | Be paraphrased in per-skill copies (render, don't restate) |
| `hooks/` board mod | Render the coordinator prep's facts read-only, as a `coordinator-overview` pane (the text board, on every surface), a `coordinator-board` band above the chat input and a status line — the band draws a Metro Wave `Svg` of the prep's `history` on Desktop under `boardStyle: metro` (#156), a one-line summary elsewhere; it calls only the `ui`, `store`, `fs.read`, `model.complete` and `command` namespaces | Write anything outside its own `$.store`, source a stage or a gate, or be read by a skill or script |

The governing rule: **facts by script, meaning by model.** A step whose inputs and correct output
are fully defined belongs in a script ([prd.md §9.1](prd.md)); if a needed operation has no
script, extend a script — never inline the operation in a prompt. Prep scripts compose the
executors **in-process** (module imports and function calls), so a session's state assembly is
one Python process, not a subprocess chain.

**The pure-core / thin-emit-wrapper pattern.** Every executor exposes a **pure, non-emitting core**
— `build_*(...) -> (payload, notices, decision | None)` — and its `main()` / `run_*` is a thin emit
wrapper over that core. `decision is None` means success (the caller uses `payload` + `notices`);
`decision is not None` is a `needs_decision` outcome the caller propagates; a partial-but-honest
degradation rides in `notices`, and a hard error still exits non-zero with stderr and no envelope
(the §3 contract). A prep script calls the cores **directly** and acts on the returned `decision`
channel, emitting exactly one envelope of its own — **no `redirect_stdout` capture** of another
script's stdout. `parse.py` (`parse_*`) and `gh_gather.run(stream=)` are the reference shapes; the
S6 pilot's `redirect_stdout` bridge — needed only while `gh_pr_gather` / `workspace` / `config_block`
still emitted-and-exited with no returnable core — was pilot-only and is retired (§4/§6 pilot;
[docs/specs/baseline.md](specs/baseline.md) §5).

## §3 The envelope contract

Every script emits exactly one JSON envelope on stdout.

- **Exit codes.** `0` — envelope present; consult `status`. `2` — usage error (malformed
  invocation; no envelope). Any other non-zero — hard failure (command/network); stderr carries
  the faithful error; no envelope is guaranteed.
- **`status`** is `ok` or `needs_decision`. `needs_decision` is a *valid outcome*, not an error:
  the script completed its read of the world and found a state only the operator can resolve.
- **Decision payload.** `{"status":"needs_decision","decision":{"code":"<CODE>","summary":"…",
  "context":{…},"options":["…"]}}`. The router's single universal rule: render `decision` as one
  `AskUserQuestion` card and act on the answer.
- **Closed decision-code set** (one vocabulary across scripts *and* judgment sub-agents;
  supersedes v1's `subagent-decision-signal.md`). Meaning + canonical emitter per code:
  - `AUTH_REQUIRED` — `gh` authentication/permission failure; detected by the pipelib runner,
    any script.
  - `EMPTY_BODY_FILE` — body-bearing write given an empty or missing staged file;
    `gh_persist.py`.
  - `BODY_TOO_LONG` — body-bearing write given a staged file over the platform's per-body
    character limit; `gh_persist.py`.
  - `MARKER_AMBIGUOUS` — more than one candidate marker comment/block where the contract expects
    one; the gathers + `config_block.py`.
  - `TARGET_IS_PR` — the requested issue number resolves to a pull request, not an issue;
    `gh_gather.py`, emitted before any further fetch so a composing prep forwards it before any
    workspace side effect (context carries the linked issue numbers derivable from the PR body).
  - `DOD_MALFORMED` — a DoD bullet or annotation outside the closed set; `parse.py dod`.
  - `PHASES_MALFORMED` — a plan `## Phases` section that doesn't parse; `parse.py phases`.
  - `BRANCH_IN_USE` — the branch is checked out in another worktree (`ensure --work`, reached via
    the landing tools and workspace-open); `workspace.py`.
  - `WORKSPACE_MISMATCH` — the ambient checkout is not the expected workspace (wrong or detached
    branch, session at the project root, checkout stale against the expected remote state, or
    removal attempted from inside the target worktree); `workspace.py`, forwarded by the stage
    preps.
  - `PLAN_MISSING` — a required plan is absent; prep scripts + the state-distiller.
  - `THREAD_SUPERSEDED_PLAN` — thread direction supersedes the recorded plan; the
    state-distiller.
  - `AMBIGUOUS` — residual non-marker ambiguity (e.g. multiple epic-branch matches); scripts +
    sub-agents.
  - `BLOCKED_ON_USER` — progress requires operator input beyond a listable option set;
    sub-agents.
  - `TARGET_IS_SLICE` — the requested issue is a **deliverable slice**: its native parent
    classifies as a non-epic, and a non-epic's sub-issues are slices by construction
    ([`skills/_shared/epic-story-hierarchy.md`](../skills/_shared/epic-story-hierarchy.md)). A
    slice has no branch and no PR of its own — it ships as a phase on its parent's branch — so
    opening a workspace for it, or planning it standalone, would silently promote it to a story
    and author a plan competing with its parent's. Emitted by `prep_workspace_open.py` (before any
    link or worktree side effect) and `prep_planner.py`, which suppresses it once the target has an
    open PR of its own (the promotion already happened; a card cannot unwind it). Classification is
    lexical on the parent, so an epic carrying neither the `epic` label nor an `Epic:` title prefix
    reads as a non-epic — hence options that relabel that parent, or re-parent the target, and
    re-run. Like every other card's, those are actions taken OUTSIDE: neither prep carries a
    proceed-anyway override, so an option offering one would raise the identical card next time.
  - `DOC_CATALOGUE_MALFORMED` — a `<!-- doc-catalogue -->` block a session is about to land does
    not hold: duplicate or unterminated markers, an interior line readers would skip, a repeated
    path, or an entry naming a path absent from the checkout; `doc_catalogue.py check`, which every
    catalogue writer runs before committing
    ([`skills/_shared/doc-catalogue.md`](../skills/_shared/doc-catalogue.md) "Keeping it current").
    Readers skip a bad line silently, so a landed one is a document that stops grounding anything;
    the card's options are fixes made outside, then a re-run.

  Adding a code is a contract change: update this section and the router rule together.
- **Notices** (non-blocking degradations) ride in `notices: []` — e.g. `DEPS_UNSUPPORTED` when
  native issue dependencies are unavailable and prose links are the fallback, or
  `SUBISSUES_UNSUPPORTED` when the native parent/sub-issue relation is unavailable and the legacy
  `## Stories` checklist is the fallback ([`skills/_shared/epic-story-hierarchy.md`](../skills/_shared/epic-story-hierarchy.md)). Work proceeds.
  The two are separate notices because the relations gate independently and their fallbacks differ;
  a script requesting both descends a ladder that drops one relation per rung, so it reports which
  one actually degraded rather than guessing from `gh`'s "unknown flag" phrasing.
  `SUBISSUE_DETAIL_UNSUPPORTED` is a third, narrower one: `prep_planner.py` enriches a non-epic
  target's sub-issues (its deliverable slices) from the REST list-sub-issues endpoint for the
  per-child timestamps and bodies the fixed `subIssues` node shape cannot carry; where that endpoint
  is unavailable the node data is the fallback, so the set still reports but rescope detection does
  not. `DOC_CATALOGUE_ABSENT` is a fourth: the consuming repo declares no grounding documents (no
  `docs/README.md`, or no `<!-- doc-catalogue -->` block in it — see
  [`skills/_shared/doc-catalogue.md`](../skills/_shared/doc-catalogue.md)), so the planner, drafter,
  resolver, evaluator, and question-resolver ground on none (the slicer and requirements-gatherer fall
  back to operator-named sources). It has **no fallback rung** by design — no built-in path list, no filesystem walk —
  which is why the degradation is a loud notice rather than a silent default. Its write-side sibling
  `DOC_CATALOGUE_BASE_UNAVAILABLE` comes from `doc_catalogue.py check`: no merge-base between `HEAD`
  and the base ref, so `confirm` is `null` (not measured, never "nothing changed") and the writer asks
  about the binding and authority edits it made itself (`skills/_shared/doc-catalogue.md` "Keeping it
  current").
  `SUBISSUE_FIELD_UNAVAILABLE` is a fifth: a *read* of the relation could not retrieve the `parent`
  field, so a caller that needed an issue's CURRENT parent (`prep_slicer.py`'s adoption-candidate
  lookup) does not know it. It is deliberately NOT `SUBISSUES_UNSUPPORTED`, which consumers read as a
  WRITE outcome — a filed child left unparented — and abort on; a failed read has written nothing, so
  reusing that token would abort a cut and report a child that does not exist. On this notice a reader
  treats the parent as UNKNOWN, never as absent.
  `PARENT_HAS_NO_INTEGRATION_BRANCH` and `PARENT_CLOSED` are a sixth and seventh, both from
  `branching.resolve_parent_epic_branch` (and, inline, `prep_planner.py`): the target's native
  parent was consulted and found, but yielded no base — no `epic/<parent>-*` branch on origin, or a
  closed parent — so the caller used the default branch. They exist because the alternative is the
  #31 silence: a receipt reading `epic: null` cannot distinguish **"no parent"** from **"parent not consulted"** from
  **"parent consulted, did not qualify"**. `PARENT_HAS_NO_INTEGRATION_BRANCH` deliberately does not
  call the parent an epic — the `parent` node carries no labels, so an epic whose workspace is not
  open yet and a *story* parent (which makes the target a deliverable slice) are indistinguishable
  at that point. The notice set is
  deliberately **open** — unlike decision codes, a script may add one without a contract change
  ([`scripts/pipelib/decisions.py`](../scripts/pipelib/decisions.py)).
- **Spill routing.** Any verbatim section (body, thread, diff, marker comment) is inline when
  ≤ threshold and written to the session scratch dir when larger; each section reports
  `*_bytes` + `*_mode: inline|path` (+ `*_path`). Threshold: `GH_PIPELINE_INLINE_THRESHOLD_BYTES`
  (default 25600; legacy `GH_OPS_INLINE_THRESHOLD_BYTES` honored). Diffs, line-comment sets and
  the coordinator prep's `target` body (the board mod reads it from disk) are always `path`.
- **Write receipts.** Body-bearing writes return `body_bytes` + `body_sha256`; a zero exit with a
  URL is authoritative and is never re-read to verify (§12). With `--attach` (below) the receipt
  still describes the *staged* file, which no longer equals GitHub's copy once gh rewrites the
  attachment references; a body op then reads the posted body back as `posted_body_path`. That
  read derives the rewritten body for the next restage and is not a verification re-read.

## §4 The facts block

The prep script's envelope payload is the **facts block**: the session's complete starting state,
assembled in one call ([prd.md §9.2](prd.md)). Common core, extended per skill — worked example
below is `prep_evaluator.py` (S6, the pilot prep script):

```json
{
  "status": "ok",
  "repo": "owner/name",
  "scratch": "/tmp/gh-evaluator-88",
  "root": { "path": "/abs/repo", "default_branch": "main", "fresh": true },
  "target": { "kind": "pr", "number": 88, "title": "…", "state": "OPEN", "labels": ["story"] },
  "vector": { "type": "story", "mode": "continue", "pr_state": "open-by-you" },
  "suggested_playbook": "story.md",
  "workspace": { "path": "/abs/repo/.worktrees/88-fix-x", "branch": "88-fix-x",
                  "expected_branch": "88-fix-x", "base_ref": "epic/42-journal", "sha": "def456…",
                  "dirty": false, "unpushed_commits": 2, "source": "ambient" },
  "config": { "source": { "path": "/abs/repo/.worktrees/88-fix-x", "branch": "88-fix-x",
                            "sha": "def456…", "dirty": false },
               "static_checks": ["…"], "static_checks_present": true,
               "test_target_present": true, "test_target_raw": "…", "test_target_source": "…",
               "escalation_labels": ["…"], "merge_policy": {"story": "ask", "epic-integration": "ask"},
               "merge_policy_present": true, "legacy_health_checks_present": false,
               "legacy_health_checks_source": null,
               "sensitive_paths": { "present": true, "source": "CLAUDE.md",
                                    "patterns": [{"pattern": ".github/workflows/", "reason": "…"}],
                                    "base": "abc123…", "matches": [], "forced_ask": false,
                                    "error": null } },
  "pr": { "headRefName": "88-fix-x", "baseRefName": "epic/42-journal", "headRefOid": "def456…",
           "isDraft": false, "author": {"login": "…"}, "author_association": null,
           "mergeStateStatus": "CLEAN",
           "reviewDecision": null, "url": "…", "closingIssuesReferences": [{"number": 101}] },
  "pr_type": "story",
  "ci": { "class": "green", "fail_checks": [] },
  "health_cache": { "sha": "def456…", "hit": false },
  "merge_guard": { "gated_head": "def456…", "head_sha": "def456…", "head_moved": false,
                    "base": { "ref": "epic/42-journal", "sha": "…", "ahead_by": 2, "behind_by": 0,
                              "status": "ahead" },
                    "base_moved": false, "skip_reason": null },
  "security": { "present": true, "reviewed_sha": "def456…", "entries": [], "unparsed": [],
                 "record": { "heading": "## Security review", "line": 31, "basis": "marker" }, "other_sections": [],
                 "covers_head": false, "dismissals_trusted": false },
  "review_threads": { "present": true, "available": true, "viewer": "…", "head": "def456…",
                       "threads_path": "/tmp/gh-evaluator-88/pr-88-review-threads.json",
                       "threads": [ { "f": 1, "state": "unreviewed", "origin": "pipeline", "…": "…" } ],
                       "counts": { "unreviewed": 1 }, "next_f": 2, "truncated": false },
  "self_review": false,
  "current_user": "…",
  "merge_config": { "allow_squash_merge": true, "allow_merge_commit": false,
                      "allow_rebase_merge": false, "delete_branch_on_merge": true,
                      "allow_auto_merge": false },
  "sections": { "issue_body_mode": "path", "issue_body_bytes": 41230,
                 "issue_body_path": "/tmp/gh-evaluator-88/body.md" },
  "dod": { "101": [ { "index": 1, "text": "…", "checked": true,
                        "annotation": { "form": "closed-by-phase-commit", "phase": 1, "sha": "…" } } ] },
  "blocked_by": { "101": [] },
  "deps_available": { "101": true },
  "epic": { "number": 42, "stories_source": "sub-issues",
             "stories": [ { "number": 90, "title": "…", "state": "CLOSED", "closed": true } ],
             "sub_issues_summary": { "total": 3, "completed": 2, "percentCompleted": 66 } },
  "attention": [ "work worktree has 2 unpushed commits" ],
  "notices": []
}
```

`dod`/`blocked_by`/`deps_available` are keyed by **closing-issue number** (a string key, since a
PR's `closingIssuesReferences` may name more than one issue) rather than a flat list — a PR that
closes issue #101 also carries #101's `## Definition of done` and native `blocked_by` state, so
the two ride together per issue rather than as an independent top-level list. `workspace` (v3) is
the **observed ambient checkout**, asserted by prep (`workspace.py attach`) — never a worktree the
prep created; `base_ref` is a *derived* fact (the PR's own base / the story's epic branch / `main`)
since an assertion cannot observe a base, and it feeds the resolver's `create-pr --base` slot.
`root.default_branch` is derived (`workspace.default_branch` — `git symbolic-ref
refs/remotes/origin/HEAD`, falling back to `gh repo view`), never assumed to be `main`.
`config.source` is the checkout whose **working tree** supplied every gate block, with its branch,
SHA, and dirty state; each `*_source` is the file path within it.
`epic` is present only on a **story** PR: the parent epic (derived from the PR's own
`epic/<N>-<slug>` base ref — no search) plus its story set and GitHub's progress rollup, resolved
per [`skills/_shared/epic-story-hierarchy.md`](../skills/_shared/epic-story-hierarchy.md) (`sub-issues` / `checklist` / `mixed`). `stories_source` is what tells the
story route whether progress projection has anything to write (`sub-issues` → nothing: GitHub
recomputes the rollup from issue state) and where its routing to the next story reads from.
`read_workspaces` is present only for a skill that grounds on a second ref (the resolver's audit
read — script-internal `ro-*` plumbing); a skill with no second view (the evaluator above) omits
the key entirely; the planner reports its asserted checkout as the top-level `grounding` fact
(`{path, ref, branch, sha, dirty, basis, base_ref, base_tip_sha}` — `basis` says why the checkout
serves: `plan_ref` / `own_branch` / `identical` / `accepted`, §6) instead.
`security.dismissals_trusted` (#139) says whether the record's `dismissed` entries are the operator's
recorded answers, and `polish.dispositions_trusted` (#144) whether the `## Polish` ledger's recorded
dispositions and notes are: each true on a PR this account opened, or one whose author's association
(`pr.author_association`, read only when a dismissal, a ledger entry that is not `open`, an
`unparsed` ledger line, or an `## Out-of-plan changes` reason on another author's PR makes it matter, else `null`) is `OWNER` / `MEMBER` /
`COLLABORATOR`; a failed read is `PR_AUTHOR_ASSOCIATION_UNAVAILABLE` and counts as untrusted. The
resolver's prep reports the same two fields (true only on its own account's PR, no association read)
and `resume` — `{present, comments_path, trusted, skipped}`, the continued PR's
trusted-author comments staged as the only PR comments its review loop's resume hint seeds from —
and `interrupted` (#155) — `{present, phase, sha, kind, entry, note, url, active, head_contains}`, the
latest `Interrupted:` record a 90% context wrap-up posted (`kind` `build` or `changes`, `entry` the
changes record's entry SHA), read through `parse.scan_interrupted` from those trusted PR comments and
the issue thread's trusted comments (an issue-posted record keeps a fresh first phase resumable). It
is `active` unless a later trusted loop comment for its phase ends in a `Review runs:` line whose exit
is not `interrupted`, or — a `build` record only — a closed PR this account opened from the work branch
has a head that strictly descends from its SHA; a probe git cannot answer retires nothing, and a
`--refresh` prep, with no workspace, cannot run the closed-PR probe. An active record overrides the
phase cursor.
`review_threads` (#145) is the PR's review threads, each with its derived state, read by
`review_threads.read_threads` in both preps: the evaluator's at the gated head on an open PR, on the
full run and on `--refresh` alike (a Re-check must see what the operator resolved), and the resolver's
at its workspace HEAD on the continued PR or an epic target's integration PR, `{present: false}` on
the resolver's `--refresh`; with no PR to read it is `{present: false}`. A failed or partial read is `available: false` with a
`REVIEW_THREADS_UNAVAILABLE` notice, and no gate approves on it. Each `security.entries[]` carries
`thread`: `{url, confirmed}` on a `dismissed` entry that cites a review thread, else `null`.
`confirmed` is true only when that thread is the finding's own pipeline thread (its root's `Claim:` line
equal to the entry's claim), resolved by a trusted account, with a trusted `dismiss: <reason>` reply whose
reason equals the recorded one — a fix request never counts; `null` on a failed read. A
thread-cited dismissal counts on `thread.confirmed` on any PR; one citing no thread keeps the
`dismissals_trusted` rule. The evaluator's `out_of_plan` (the `## Out-of-plan changes` reasons S4.6 quotes
into a thread) carries `reasons_trusted` on the same terms as `dismissals_trusted`. The shape, the states and the trust rule are
[`skills/_shared/review-threads.md`](../skills/_shared/review-threads.md)'s.

Rules: every fact is **re-derivable** — prep supports `--refresh` and is re-run at the points
where currency matters (e.g. pre-merge PR state); `--refresh` re-derives only the volatile facts
named in the calling step's DoD (for the evaluator: PR state, `ci`, `health_cache`, `merge_guard` —
compared against the opening run's head, passed back as `--gated-head <sha>`; `null` on a PR that
isn't open — and `review_threads` with each `security.entries[].thread`) and does
**not** re-assert the workspace, re-run the setup hooks, or re-read the gate-config blocks (static-checks,
test-target, escalation-labels, merge-policy, the legacy health-checks block) or the sensitive-path
declaration —
a `--refresh` envelope therefore omits `workspace` entirely and reports `config: {}`, relying on
the caller's prior full-run facts block for those (the opening assertion stands for the session). Values the flow needs (`base_ref`, branch name, merge
strategy, audit SHA) appear as facts so playbooks consume them as data (§5). Ambiguities a script
can detect but not resolve surface in `attention` or as a `needs_decision` — never as prompt-side
re-derivation. Inline-mode sections carry the content in the bare field (`issue_body`) alongside
`*_bytes` (the coordinator's `target` body, always `path`, has no bare field); additional named read workspaces ride under `read_workspaces`, keyed by purpose.

**The DoD's shape is a fact (4.32.0).** `## Definition of done` is the one heading every DoD reader
parses, and a checklist under any other heading (`## Acceptance criteria`) reads as "no DoD". Until 4.32.0
nothing said so: the drafter's feature template wrote that heading, the planner indexed its bullets by eye,
the plan reviewer accepted it, and the resolver — handed `closes-dod` indexes against `dod: []` — ticked
bullets no downstream parser reads. `parse.dod_summary` (`present` / `count` / `other_checklists`, the
legacy epic `## Stories` excepted) and `parse.dod_coverage` (out-of-range / unclaimed / doubly-claimed
indexes, plus `drift` — the index-stability invariant exactly as `dod-annotations.md` states it) are now
facts at every stage that writes or consumes `closes-dod`: the drafter proves its staged body with
`parse.py dod`; the planner's prep reports `dod` with the body staged at `dod.body_path`, its S1
DoD-heading card offers the rename, and its S7 `parse.py phases --issue-body` checks the plan against the
DoD; the resolver's prep reports `dod_coverage`, and `drift` blocks projection and re-routes to the planner.

## §5 Routing & playbooks

- **The state vector** (`vector`) is the tuple that selects behavior — for the pipeline skills:
  issue/PR *type* × session *mode* (fresh / revise / continue) × prior-work state. Every
  component is script-derived.
- **The routing table lives in `SKILL.md`, visibly**: `vector → playbooks/<file>` rows. Prep
  proposes (`suggested_playbook`); the router confirms against the table and may override only
  on evidence the script cannot see (e.g. thread supersedes labels; the slicer's promote rule, where
  the invocation asks a target to be re-shaped as an Epic — the receiving end of the planner's
  seam-gate off-ramp, which was the drafter's until #16), stating why.
- **Parameterize before you playbook.** A branch that differs only in *values* (base ref, branch
  name, merge strategy, cleanup list) is not a branch — the values are facts. A playbook exists
  only for flows that differ in *actions taken* (epic bootstrap files stories; story completion
  ticks the epic checkbox and appends the delivery log).
- **One route per session.** The router selects exactly one playbook; a playbook may
  additionally pull in its skill's single shared spine file — nothing else, and never a second
  playbook. Playbooks are linear narratives with no `if epic … else if story …` interleaving.
  Shared behavior lives once — in the router's invariants or the spine — and playbooks reference
  it rather than restating it.
- **Contract renderings stay point-of-use.** A playbook that emits a shared-schema artifact
  (handoff, DoD annotation, delivery-log entry) renders it per the `_shared` contract file and
  cites it.

## §6 Workspace & grounding model

**The session runs where the operator starts it** (v3, the workspace-model inversion). The work
workspace is opened by the operator (`/ctrl-freaq:workspace-open <issue>` — linked branch via
`gh issue develop`, worktree under `.worktrees/`, setup hooks), each resolver/evaluator session is
**started inside that worktree**, and the workspace is released by the operator
(`/ctrl-freaq:workspace-close` — teardown hooks, then the gated removal). The
`coordinator` drives the same lifecycle on the operator's behalf: its prep composes the two
tools' pure cores (`prep_workspace_open.build_open` / `prep_workspace_close.build_close`) with no
consent card of its own: an open runs on the operator's click of the stage go-ahead that names it, a
close as a routine step announced in one line, and each core's decision cards (`BRANCH_IN_USE`;
dirty, unpushed or diverged state; `cwd_inside_target`) render unchanged. Stage preps
**observe and assert** the ambient checkout (`workspace.py attach`: expected branch, not the
project root, not detached, not stale against the expected remote state — every mismatch a
`WORKSPACE_MISMATCH` decision) and re-run the setup hooks on every session entry; they never
create work worktrees. The planner grounds on its asserted ambient checkout too
(`facts.grounding`), but asserts it by **content**, not branch name — the resolver and evaluator
keep the name check because their branch is what they push or judge; the planner only reads. Four
outcomes, whatever row chose `plan_ref` (`facts.grounding.basis`):

- **`plan_ref` itself** — a default-branch plan_ref accepts any current checkout of it including
  the project root: fresh planning happens *before* workspace-open (plan-before-open).
- **The issue's own branch, containing the tip of the ref it would otherwise have grounded on** —
  that branch becomes `plan_ref` (rows `story-own-branch` / `own-branch`, for a story or a
  standalone issue alike). A strict superset sees more truth, not less, where the name check
  refused a checkout it was frequently bit-identical to.
- **Any branch sitting exactly at that tip** (`identical`) — the same code, so `plan_ref` and the
  footer stand.
- **Another branch carrying commits beyond the tip** — code that may never ship with this issue,
  so it is a `WORKSPACE_MISMATCH` card; only the operator's `--ground-ambient` answer admits it
  (row `ambient-accepted`, the footer naming that branch). A refresh without the flag re-asks
  rather than silently flipping `plan_ref`.

The containment probe, not the branch name, is what enforces "a later story never grounds on code
a predecessor has since moved": behind or diverged still refuses, and the probe never fetches.

**The default branch is derived, never assumed** — `workspace.default_branch` reads `git
symbolic-ref refs/remotes/origin/HEAD`, falling back to `gh repo view --json defaultBranchRef`, and
hard-fails naming `git remote set-head origin --auto` rather than guessing. A repo on `master`,
`develop`, or `trunk` is ordinary.

**Two rules govern where config is read (v3.x; the default branch still changes only via PR):**
1. **Every marker block is read from the working tree of the checkout the command runs in** —
   hooks *and* gate config (test target, checks, escalation labels, merge policy), committed or
   not. workspace-open reads the checkout it was invoked from; a stage-session attach and its
   gate-config read use that session's own worktree; workspace-close reads the worktree being
   closed. **This is a deliberate reversal.** Through v3 gate config and attach-path hook
   discovery read `origin/main` blobs at a pin (`refblocks.fetch_pin`), so a PR could not weaken
   the gates judging it and a branch could not supply hook commands to an automatic path. The pin
   made the *consuming repo's own config unchangeable without merging it first* — a
   `<!-- worktree-setup -->` edit could not be tested until it landed on the default branch, and a
   bad one then broke every workspace-open and every session entry. The operator's decision is
   that they choose the checkout and it is trusted. The compensating control is **provenance, not
   a gate**: `setup.source` / `teardown.source` / `config.source` each name the supplying
   checkout, branch, SHA, and dirty state, and a dirty gate-config source raises an `attention`
   line. There is deliberately **no confirmation card** — one that fired on every session entry
   would be clicked through, which is worse than either honest posture. Do not "restore" the pin
   as a bug fix; it was removed on purpose (§12). The sensitive-path declaration (#96) still takes
   its patterns from the head's working tree; its self-edit rule reads the merge base only to see
   whether the declaration changed — never as a source of patterns — so it is not the retired pin.
2. **Pinned-ref grounding stays in script-internal `ro-*` read worktrees.** The resolver's audit
   second view is plumbing prep hands out — the operator never opens or sees it.

| | `work` workspace | `read` workspace |
|---|---|---|
| Path | `.worktrees/<branch>` | `.worktrees/ro-<ref-slug>` |
| Checkout | branch | detached HEAD at `origin/<ref>` |
| Hooks | setup at workspace-open (create **and** reuse) and on every stage-session attach (fail-fast, discovered in the invoking / the session's own working tree); teardown at workspace-close, before removal (best-effort, discovered in the worktree being closed) | none |
| Lifecycle | **operator-owned**: workspace-open creates (and owns epic/`<N>`-`<slug>` integration-branch creation); persists across sessions; workspace-close tears down + removes (`remove --work` — gated on dirty/unpushed, merged-PR-aware, and refused from inside the target); the coordinator drives the same cores, each as a routine step announced in one line, its safety cards kept, and fast-forwards the epic worktree before a story's plan (`sync --work`) | script-internal: reuse per logical ref; **fetch, then reset to current `origin/<ref>` on every ensure**; `workspace.py gc` removes `ro-*` older than `--max-age` (default 7 days) — and only `ro-*` |
| Facts | observed: branch, expected_branch, derived base_ref, path, SHA, dirty/unpushed state | path + the exact SHA grounded on |

- **`workspace.py` owns the mechanics**: `attach` (the stage-session assertion — expected
  branch / pattern acceptance, staleness against the expected remote state, hook re-run from the
  worktree's own tree; every mismatch a `WORKSPACE_MISMATCH` decision with a closed `reason` set),
  `ensure --work|--read` (workspace-open and the landing tools create through it; branch exclusivity —
  `BRANCH_IN_USE` — live on this creating path), `remove --work` (teardown hooks best-effort, then
  `git worktree remove`; dirty or unpushed state is a decision, never a silent discard; merged-PR
  context reframes the squash-merge false positive; `cwd_inside_target` refuses to remove the
  invoker's own checkout), `sync --work` (fetch, then fast-forward a work worktree to its own
  branch's remote tip and re-run the setup hooks; dirty, ahead, or diverged is an `AMBIGUOUS`
  decision before anything moves; never the main checkout — prd.md §8.5's one named carve-out, run
  only by the coordinator as part of a go-ahead the operator answered), `gc`, `root-status`,
  `lint`, and the `.worktrees/` exclusion. Every
  `--root` is normalized to the MAIN checkout via `git rev-parse --git-common-dir`, so the scripts
  behave identically invoked from inside a linked worktree — which is now the normal posture. The
  `.worktrees/` exclusion is maintained in the repo's `info/exclude` — **never** a
  `<root>/.gitignore` edit: `info/exclude` lives under the git directory, outside the working
  tree, so the idempotent bootstrap write can never surface in `git status --porcelain`. The
  consuming repo is never asked to commit a plugin-authored `.gitignore` line; a pre-existing
  `.worktrees/` line an earlier run left in a real `.gitignore` is inert and untouched.
- **The single-workspace invariant**: a session's prompt-visible rule is "your workspace is the
  ambient checkout, reported as `facts.workspace`" — asserted by prep before judgment runs. Every
  Read/Grep/Explore/test/command still targets `facts.workspace.path` by absolute path: sub-agent
  dispatches and background commands run in their own cwd, so the absolute-path discipline stays
  load-bearing even though the session's shell starts in the worktree. When a flow needs a second
  view, prep hands out an additional *named* read workspace in the facts block; prompts never
  select refs.
- **No ref arithmetic in prompts.** `git show ref:path` / `git grep <ref>` are script-internal
  tools only. Grounding SHAs come from the workspace facts (a plan's "planned at `<sha>`" *is*
  its grounding checkout's HEAD).
- **Gate config comes from the ambient checkout** (rule 1 above), uniformly — the stage preps, and
  the drafter, whose OQ-marker block was always read from its ambient checkout, now share one rule
  instead of two. **No path inspects or writes the operator's checkout**: not its branch, not its
  dirty state, not its divergence — the `ROOT_*` gates and the `git merge --ff-only` that used to
  fast-forward it are retired, so the operator's working tree is theirs alone. Staleness that
  matters is asserted per-checkout instead (`stale_checkout`), and `ensure --work` fetches
  `origin/<base>` itself so a new worktree forks from the real remote tip regardless.

## §7 GitHub I/O & write discipline

- The four v1 executors are **ported to Python** — `gh_gather.py`, `gh_pr_gather.py`,
  `gh_persist.py`, `config_block.py` (implementation S21) — under the §3 envelope; their
  behavior contracts are otherwise unchanged. Prep scripts compose them in-process; playbooks
  call `gh_persist.py` (and `config_block.py`) directly via Bash for writes.
- **Write path:** the skill stages the verbatim body to its scratch dir
  (`/tmp/gh-<skill>-<N>/…`) and passes the path; `gh_persist.py` verifies the round-trip hash
  itself and reports `body_sha256` in the envelope, failing on mismatch — callers act on
  `status`, never re-hash prompt-side. Title writes on an already-filed issue/PR go through the
  bodyless `edit-title` / `edit-pr-title` ops (the issue/PR split has `edit-pr-body`'s reason: `gh
  issue edit` rejects a PR number); they stay script-backed under rule 7 — the sanctioned scriptless
  executors remain exactly the three §10 lists. The leading size gate stays (the #626/#627 race
  fix); an empty staged file is an `EMPTY_BODY_FILE` decision, and one over the platform's per-body
  character limit is a `BODY_TOO_LONG` decision — both raised before any `gh` call, on every
  body-bearing op.
- **Attachments.** `create` / `create-pr` / `edit-body` / `edit-pr-body` / `comment` (issue and pr
  targets) take a repeatable `--attach FILE[#alt]`: gh ≥ 2.99 uploads the image or video and
  rewrites the body reference that names it by the exact same path. This is how a PR that changes
  what a user sees carries screenshots (`skills/resolver/references/pr-screenshots.md`). A relative,
  missing, empty or unsupported file, more than 50 files, or `--attach` on a `pr-review` comment (gh has no
  such flag) is a usage error, and the size gate reserves room per attachment. An older gh is
  **not** retried without the flag, because the body would ship dangling local references: exit 1
  with an `ATTACH_UNSUPPORTED:` stderr, and the caller restages without them. A partial upload
  (non-zero exit, a GitHub URL printed) is a landed write: `ok` plus an `ATTACH_PARTIAL` notice, never a hard
  failure the caller would re-run into a duplicate. The gh ≥ 2.40 floor is unchanged.
- **Review threads (#145).** `review-post` posts one `COMMENT` review carrying a spec's threads, each
  anchored to a line range or a whole file at the head named; `review-reply` replies in one thread,
  and refuses (`ok`, `posted: false`) a resolved thread, one on another PR, or a node that is not a
  thread. Each op stamps the `<!-- review-thread:v1 -->` marker and its stamp onto every body itself,
  size-gates it before the first `gh` call and reports its hash. A pending review of this account's
  that is not the pipeline's own stranded one is an exit-1 `REVIEW_PENDING_CONFLICT:` stderr, no
  decision code. **No op resolves or unresolves a thread**, and none may: a reader counts this
  account's resolution as a human's only because the pipeline never makes one
  ([`skills/_shared/review-threads.md`](../skills/_shared/review-threads.md) §10–§11; validator 8, §10).
- **Atomicity & idempotency stay as built:** marker replacement posts the new comment before
  deleting the old; `close` on a closed issue is a no-op; native-dependency writes are
  capability-gated (`DEPS_UNSUPPORTED` notice + prose-link fallback), as is the native
  parent/sub-issue write `create --parent` (`SUBISSUES_UNSUPPORTED` notice + legacy-checklist
  fallback). A capability retry is safe here precisely because a failed `create` files nothing.
- **PR-only landing, operator-gated.** Skills that change tracked files (`setup` config blocks,
  `question-sweep` doc fixes, `doc-reviewer` applied findings, resolver code — and, from any of
  them, the `<!-- doc-catalogue -->` entries their doc changes affect, checked by
  `doc_catalogue.py check` before the commit) do so only in a work workspace
  ([prd.md §8.2](prd.md)). The resolver opens its PR as part of its flow; the
  three maintenance tools stage approved edits and **offer** the landing (commit + push + PR) as
  one explicit final gate — on decline they perform no git actions and report the workspace path
  + ready-to-run commands in the summary. Consuming repos may set a merge-policy entry (e.g.
  `docs: auto`) to keep accepted maintenance PRs low-ceremony.
- **No executor agent.** v1's `github-ops` sub-agent is retired; its responsibilities land here:

| v1 `github-ops` rule | v2 owner |
|---|---|
| 1 Faithful, never summarized | Scripts emit verbatim sections + `sha256`; playbooks read spilled files directly |
| 2 Posts only what it's given | `gh_persist.py` staged-path convention + empty-body gate |
| 3 `DECISION_NEEDED`, can't ask user | `status: needs_decision` envelope + the router's universal card rule (§3) |
| 4 Report errors faithfully | Bash tool surfaces script stderr to the main loop unmediated |
| 5 No nesting | Moot — there is no executor agent |
| 6 Successful write is self-confirming | Unchanged rule, stated in §3/§12 |
| 7 Use the bundled scripts, never hand-roll `gh` | Unchanged, enforced on the main loop: prompts contain no raw `gh` write/fetch-envelope commands |
| 8 Spill threshold | Unchanged, in scripts (§3) |

## §8 Judgment sub-agents

The isolation architecture for reasoning that must not pollute the main loop's context. All are
context-blind, receive **workspace paths and prep-staged files** (never a ref to read from — a base
ref a row lists rides along only as an informational name, never as ref arithmetic), return their
result or a typed §3 decision code, cannot call `AskUserQuestion`, and never write to GitHub.

| Sub-agent | Caller | Consumes | Returns |
|---|---|---|---|
| state-distiller | resolver | issue thread + the whole plan's text + any legacy shipped-phase records (paths) | current-state / effective-plan brief |
| fitness audit | resolver | read workspace + issue + the staged whole plan (path) | findings by dimension (incl. plan-vs-code currency) |
| plan reviewer | planner | plan draft + the phase numbers already shipped + the asserted grounding checkout | findings by dimension |
| issue reviewer | drafter | draft + repo context | findings by dimension |
| cut reviewer | slicer | parent body (path) + proposed children + repo root | findings by dimension |
| research validator | researcher | dossier draft | findings by dimension |
| test-selection | resolver, evaluator | diff scope + the ambient checkout's test config | `COMMAND:` + `RATIONALE:` |
| cold-read audit | resolver | staged scope diff (path — the phase delta on a non-final phase, cumulative on the final) + workspace + the plan's phase list | whole-diff findings verdict |
| fix design | resolver | a fix round's findings on a seam the review loop already changed, or a recurring settled finding + staged scope diff (path) + workspace + the loop-changed file list + the plan's decision bullets and phase list + the plan, shipped-record and issue-body paths (scope) | seam map + class-level change set (plan conflicts, needs-a-plan-decision entries, out-of-reach items); the main loop edits |
| dismissal check | resolver | a fix round's Refuted / Plan-settled / No shown cost dismissals of defect-tier claims, each with its citation + staged scope diff (path) + workspace + the plan's decision bullets, its `## Changes` and its phase list | `upheld` / `overturned` per item, with evidence |
| security review | resolver, evaluator | staged cumulative diff (path) + the staged changed-files list (path) + its base ref (name) + workspace + declared catalogue entries | findings with site, category, severity, claim, exploit path, and a `Covered:` list of the files read, or `AMBIGUOUS` |
| blast-radius judgment | resolver, evaluator | staged scope diff (path) + its base ref (name) + workspace + grounding path + the assembled plan and any legacy shipped-phase records (paths) + the phase number and final flag + declared catalogue entries | `raise` / `hold` with dimension · site · evidence, or `AMBIGUOUS` |
| out-of-plan triage | evaluator | staged gated diff (path) + base ref (name) + workspace + the assembled plan (path) + the post-narrowing out-of-plan file list, reasons only when trusted (path) | `routine` (kind `test` / `fixture` / `fallout`) / `consequential` per file with evidence, or `AMBIGUOUS` |
| question-status reader | question-sweep, question-resolver | question thread (path) | status or `AMBIGUOUS` |

Skill frontmatter carries no `model:` or `effort:` keys — every skill inherits the invoking
session's model and effort level (the v1 pins were removed 2026-08-01; reintroducing one is a
deviation through the normal gate). There are no mechanical-relay
agents: if a task is deterministic it is a script, not a sub-agent.

**The fix round classifies by provenance first (4.31.0).** Every finding is **introduced** (in or made
reachable by this PR's lines), **adjacent** (the same change at a sibling site, or a one-site correction
in a file the PR edits) or **pre-existing**, before its bucket and tier. Polish on introduced or adjacent
code is fixed whatever its size; pre-existing polish and no-shown-cost findings go to the polish ledger;
an introduced defect leaves only through an operator answer; and review-deferred follow-ups wait for the
end-of-loop checkpoint card. The evidence was an audit of 503 `follow-up` issues in one consuming repo,
where provenance — not size or cost — separated the deferrals that should have been fixes or plan gaps
from the correct ones. This axis is **not** the `finding_provenance` tag 4.11.0 retired, which gated the
cold read; it decides fix-or-defer only.

**The review loop's exit is a tier, not a sub-agent (4.21.0).** The resolver's fix round tiers every
Addressable finding `defect` or `polish`, and the loop runs while the defect count keeps falling (one
grace round, then a stall card; an emergency ceiling of 8 `code-review` runs). This reintroduces a severity
axis on purpose, and it is **not** the convergence machinery 4.11.0 removed (`finding_provenance`,
severity decay, one-shot suppression): that decided *when to cold-read*, and the cold read is still
unconditional once per phase; this decides *when the loop ends*, replacing a fixed 2/4 cap that could not
tell a round of nits from a round of new bugs. Unfixed polish goes to the PR's `## Polish` ledger
(`skills/_shared/polish-ledger.md`), and the **operator** decides it entry by entry before merge
(`skills/_shared/polish-triage.md`). Since 4.30.0 the resolver asks first: its **finalisation** step, on
the final pass and before the push, puts every entry the loop left to the operator and fixes the applied
ones in one more round with a light re-review, because applying polish from the evaluator cost a
soft-reject, a revision session and a second evaluation. The **evaluator** stays the backstop — it
re-checks at head every unfixed `apply`, every `file` and every `premise false` `drop` (a premise the
code's author judged at finalisation), and asks about any entry still `open` — and its disposition rewrite
stays its one PR-body write, through `gh_persist.py edit-pr-body`. Since 4.34.0 the card groups entries by theme
with one proposal per group and follows a digest that shows the code behind each entry; no-shown-cost
findings are recorded `drop` by the loop and never reach it.

**The author's serious dismissals get an independent check (#94).** `code-review`'s own verifiers check
the reviewer's findings before they are reported; after that, the main conversation — the code's author —
classifies them, and until #94 nobody checked a dismissal it made. Now, when a fix round dismisses a
finding as Refuted, Plan-settled or No shown cost and the finding's *claim*, read as if true, is
defect-tier, the **dismissal check** sub-agent re-tests the dismissal's citation against the code at head:
one dispatch per round (re-dispatched once for the items of an unparsed verdict), before the fix plan,
returning `upheld` or `overturned` per item. `overturned` requires a cited site and evidence — a path that
reaches the claimed failure, or for a Plan-settled item the decision bullet still true after the fix or the
decision producing the wrong behaviour — so doubt alone upholds; an overturned item re-enters the rubric
(Addressable when introduced or adjacent, Explicitly-deferred when pre-existing, Decision-required when it
shows a plan decision defective), and the round reply records every verdict. Scope: No shown cost dismissals
are checked, because on a defect-tier claim they set aside a possibly real defect with no evidence it is
false; Deferred-by-plan ones are not, because the deferral comes due when its phase is current, and the
final phase's cumulative review and cold read read it there. The check is not a `code-review` run and counts
toward no loop limit.

**Doc findings pass a materiality bar (4.40.0).** Before its bucket, every finding on a doc file or a
code comment, in any PR, is tested for materiality: would the text as it stands mislead a builder,
reviewer or auditor into a wrong decision? A finding that would not (a stale date stamp, a version row, an
index echo, a length convention) is recorded `drop` as no shown cost: never a defect, never in the defect
count, never must-fix. The bar is always on, with no operator switch and no test of the PR's shape, and it
reaches the reviewer through S5.1's `<context>`. The shared "Apply criteria" carry the same test, so the
evaluator's `apply` bucket cannot send a PR back over bookkeeping either. The cause was a docs sweep whose
bookkeeping fixes each created new sites to keep consistent: the next round found those as loop-induced
defects, the count never fell, and the loop stalled phase after phase (#87).

**The plan and issue review loops exit on blockers (4.41.0).** The planner's S7 loop and the drafter's
full-tier loop used to exit only on a pass with zero findings, so a single SUGGESTION or NIT bought another
full review pass, though the plan reviewer's own severity scale says a NIT never gates. Now only a BLOCKER
keeps either loop running, and the circular and cap exits count BLOCKERs alone. The planner applies a
SUGGESTION on its merit (would the plan as written confuse or mislead its reader?) and discards NITs; on
the exit pass its merited SUGGESTIONs land without a re-review. The drafter shows the exit pass's
suggestions and nits unapplied at its filing gate, the lean tier's posture. The plan reviewer also gains
the issue reviewer's changed-since-last-pass input, so passes 2+ verify only what changed plus the prior
BLOCKERs' resolutions, re-running a whole-plan check (coverage, sequencing, section ownership, the size
cap) only when a change touches its inputs (#88).

**A security review blocks approval on open findings (#95).** No stage looked for security flaws, and
nothing blocked approval on one. The **security review** sub-agent reads a PR's cumulative diff, staged
against its real base (on a story PR, the epic branch, which the host's built-in security command cannot
take), and reports each finding with a site, a category, a severity and an exploit path. Its findings land
in the PR body's `## Security review` record (`skills/_shared/security-review.md`), each `open`, `fixed` or
`dismissed`. Only a person dismisses an entry, with a reason. Since #145 the evaluator posts each open
finding as a review thread, and the dismissal is a trusted human's `dismiss: <reason>` reply on the finding's
own thread, which that human resolves in GitHub, bound to the finding by the thread root's `Claim:` line; the record line cites the thread, and every run re-reads the reason
from it (`review_threads.confirm_dismissals`, `facts.security.entries[].thread`), so an edited body line
counts only while the thread still says it. A `Security` consent card remains only for a finding no
thread can anchor and to collect the reason a resolved thread lacks; the resolver's finalisation asks
none and records a dismiss proposal `open`. The evaluator never approves while an entry is `open`. It runs the pass at S4.5 unless
`facts.security.covers_head` holds: the record's `Reviewed:` SHA is the gated head, this account
opened the PR, **and** the record is marked (`record.basis` is `marker`, #151), so a consuming repo's
lookalike, unmarked by definition, can never skip the pass. The record is self-declared body text, so a `Reviewed:` line alone would make the gate
skippable by editing one line. The resolver runs the same pass first, at finalisation, so a finding is
fixed in the session that wrote the code rather than in a revision session.

**The security pass proves its coverage (#100).** A pass with no instruction to cover the diff once returned
no findings after a handful of tool calls over a 19-file diff. Later passes told to read every file covered
it all, and one caught a real flaw. So the caller stages the changed-files list beside the diff, the
sub-agent ends its `## Findings` with a `Covered:` block naming every file it read, and `parse.py
security-coverage` — a script, never the model — compares the two. A gap gets one re-dispatch over the
uncovered paths; a gap that remains means the pass did not complete, which blocks approval as an `open`
entry does. The record itself is identified first by the pipeline's own marker (#151): the first section
headed exactly `## Security review` whose first non-blank line is `<!-- security-review:v1 -->`, else, with
none marked, the legacy fallback, the first exact-heading section holding one line in the pipeline's
grammar (a full-SHA `- Reviewed:` line or a `- SEC-<n>` row with a closed-set disposition). It is reported
as `facts.security.record`, its `basis` naming the tier (`marker` | `heading`); a parenthetical heading is
never the record, and every other section under that heading (`other_sections`) is never rewritten. A
consuming repo's exact-heading section in that grammar, on a PR with no marked section, is the accepted
residual: it is read, and rewritten, as a legacy record (`skills/_shared/security-review.md`).

**Review threads gate approval (#145).** `blast_radius.py` already knew which code a change put at risk —
a sensitive-path match, a file outside the plan, a site the blast-radius judgment cited — and open security
findings were known too, but none of it reached the person reviewing the PR, and nothing on GitHub recorded
that a person looked. At S4.6 the evaluator re-runs the blast-radius judgment and `assess` over the PR's
cumulative diff at the gated head, and `review_threads.py flags` — a script — decides which code gets a
thread, where it anchors, its `F<n>` id, the grouping by hunk and the cap; the model writes each thread's
text and may add plan-adherence threads, whose anchors the script checks lie in the diff. It posts them as
one `COMMENT` review at the gated head, and posts nothing twice: a re-run matches existing threads. Each
thread's state (`unreviewed` / `accepted` / `changes-requested` / `operator-thread` / `superseded`) is
derived by script from GitHub's resolution data, the trusted comments and a `git diff -U0` hunk check, and
APPROVE waits until every gating thread is `accepted`: resolved by a trusted account, its hunk unchanged
since. An unresolved thread a trusted human replied in, or an operator's own thread the resolver has not
answered, is a soft-reject naming it by `F<n>` / `O<n>`; a failed thread read holds the gate. The resolver's review loop reads the threads and the trusted conversation as its first round's
input on any run that continues a PR, and replies in each thread it addressed after its push, and records why each out-of-plan file
departs from the plan in `## Out-of-plan changes`, which that file's thread quotes when the PR's author is trusted. The resolver opens no
thread: one pinned mid-flight would go stale as later rounds move the code. Trust follows SEC-1 for
comments; a resolution, which carries no association, counts by the resolving account's repo permission
(`admin` / `maintain` / `write`), or with no lookup when it is this account's, since the pipeline never
resolves (§7). The contract is [`skills/_shared/review-threads.md`](../skills/_shared/review-threads.md);
the renderings are the evaluator's `references/review-threads.md` and the resolver's `review-fix-round.md`.

**The review loop leaves a parseable record, and the reviewer gets the repo's guidance (#98, #99).** Each
resolver loop comment leads `<!-- review-loop:v1 -->` and carries one fixed-shape line per classified
finding — its bucket, provenance, tier, action and a class key — plus a `Review runs:` line per pass
(`skills/_shared/review-loop-record.md`). The read-only `review-report` tool reads those records across a
repo's PRs and reports outcomes, rounds-to-pass and the share of phases needing five or more review runs;
only text a trusted author (`OWNER` / `MEMBER` / `COLLABORATOR`) wrote reaches a record or a tally. Its
recurring classes feed `setup`'s proposals for the consuming repo's `<!-- issue-resolver-review-guidance -->`
block, which the resolver hands its reviewer verbatim in the `<context>` under `Repo review guidance
(owner-written):`, with bare reviewer flags neutralised so owner prose can never pass one.

**The review level follows the change's blast radius (#97).** The plan declares a level per phase
(`## Review level`); `blast_radius.py assess` raises it one step, capped at `xhigh`, when the phase touches
a declared sensitive path, the sensitive read fails, or a changed file sits outside every planned path —
never on a line or file count. On the epic-integration pass the files the PRs merged into the epic branch
changed count as planned (`facts.epic.delivered`, passed as `--delivered`, #149): an epic plan is written at
coordination altitude and names no story's files, so without them every integration pass was raised. A file
no merged PR changed — a commit made directly on the epic branch — still raises. Coverage is by file, not
hunk: a sync merge's conflict resolution or a direct edit inside a delivered file is covered, and only the
sensitive-path signals and the judgment can raise it. Every other route passes no delivered set. The same signals over a phase's shipped diff turn a `continue` checkpoint
into a review; they only ever add a pause. The resolver's PR body states the change's risk and how
to revert it (`## Risk` / `## How to revert`, `skills/resolver/references/review-level.md` §4).

**The diff's blast radius is judged too (#128).** Those signals are facts about which paths changed; what
the diff actually reaches — a later phase or story that builds on it, a contract or seam it changes,
security, data or money — is meaning, so on a planned run the blast-radius judgment sub-agent reads the
diff, the workspace and the plan and returns `raise` or `hold`, citing each site the plan's level reason
did not account for. `blast_radius.py assess --judgment` folds a `raise` (or a verdict it cannot parse)
into the same `raised` the fact signals feed, so the level still steps up once in total and, over a
shipped diff, the same review follows. The fact signals stay the floor: a `hold` changes nothing, and no
verdict removes a level or a pause they add (`skills/resolver/references/review-level.md` §6).

## §9 Skill anatomy

Every `SKILL.md` is a router with the same four sections, in order (for the standalone tools:
Prep collapses to a no-prep note where the skill has none — currently doc-reviewer; the v3
workspace tools' preps ARE their action — and section 4 is **Summary** per [prd.md §6](prd.md),
not Handoff):

1. **Prep** — the one prep-script invocation and the `needs_decision` card rule.
2. **Route** — the visible `vector → playbook` table and the override rule (§5).
3. **Invariants** — the universal rules for every route: single-workspace, staged-body writes,
   gates-only-for-decisions, faithful reporting, handoff-on-clean-exit.
4. **Handoff** — pointer to the shared schema + this skill's rendering reference.

Size bar: a router fits comfortably in one default `Read` (≤ ~150 lines) — the v1 forced-read
workaround and the resolver-local `§P-ID` scheme are both retired.

Authorship rule: a v2 router + playbook set is written **from scratch** against the skill's S1
spec and this section — never derived by editing v1 `SKILL.md` prose down. Only two classes of
v1 text are carried: the [prd.md §7](prd.md)-frozen artifact renderings (byte-compatible by
requirement) and the judgment sub-agent prompts a cutover step explicitly marks as carried. Standalone tools keep
`disable-model-invocation: true` — except `setup`, which v1 never carried the key on and the S17 parity
run kept model-invocable (`docs/specs/parity/setup.md`, "ADJUDICATION RECORD"). Scratch dirs are uniformly `/tmp/gh-<skill>-<N>/`, `<N>` being the
session's key — the issue/PR number, or the process id for a new-mode drafter that has none yet, so
the concurrently-spawned drafters of one follow-up batch never share a staging dir. Skill and
directory names are the [prd.md §2](prd.md) fixed names; nothing echoes the `ctrl-freaq`
namespace.

## §10 Testing architecture

The deterministic layer is tested offline ([prd.md §9.3](prd.md)); prompts are validated by
census greps.

- **Harness:** `tests/run.py`, stdlib `unittest` discovery — no third-party test framework,
  nothing to vendor or install. No network, no live repo. The discovered suite runs across spawned
  worker processes (`-j N`, default `os.cpu_count()`), one TestCase class per unit, run whole in
  one worker; `-j 1` is the plain serial `TextTestRunner` run. The suite must pass on **both macOS
  and Linux** (Linux via any container or host; invocation documented in `tests/README.md`).
- **Board-mod harness (separate):** the `hooks/` mod is TypeScript, so the offline suite cannot run
  it. `claude plugin test .` runs `hooks/*.test.ts` and `claude plugin validate .` checks the
  manifests and the hooks module (Claude Code ≥ 2.1.308, no sign-in); CI's `plugin` job runs both.
  The offline suite still pins the prep keys the view reads (`BoardReadKeysTests`,
  `BoardBuilderKeysTests`, `history` included) and the history builder itself
  (`tests/test_board_history.py`, #156).
- **`gh` shim:** `tests/shim/gh` (itself a small Python executable named `gh`) sits first on
  `PATH` and replays canned responses from
  `tests/fixtures/<case>/` keyed on the argv it receives; unexpected argv fails the test. Scripts
  under test never notice.
- **Git sandbox:** workspace/lifecycle tests run against a temp origin (bare repo) + clone per
  test, copied from a per-process template (a clone of an origin that has been pushed to is a real
  `git clone`); no shim needed.
- **Coverage bar:** every script's happy path, every §3 decision code it can emit, and envelope
  schema conformance (shared assertion helpers) for every emitting script.
- **Prompt-side validators** (no offline harness exists for prose): the contract-token census
  and banned-pattern greps from `CLAUDE.md`, extended with: zero old-name hits, plus the two
  drift-class validators and the never-resolve rule below.
  - **Raw-`gh` rule (the §7 rule-7 form).** Zero raw `gh` **write / fetch-envelope** invocations in
    `skills/` — any `gh` op that *has* a bundled script (writes → `gh_persist.py`; fetch-envelopes →
    `gh_gather.py` / `gh_pr_gather.py`) must go through it, never a hand-rolled `gh`. **Excepting three
    scriptless executors** (no `gh_persist.py` op covers them) — these are sanctioned raw-`gh`
    executors, not violations, because the behaviors they implement are spec'd: `gh pr merge` (merge
    execution, always pinned with `--match-head-commit <gated head>`) and `gh pr ready --undo` (the soft-reject draft-flip on Needs Revision / Reject), both
    the evaluator's and both cited to [docs/specs/evaluator.md](specs/evaluator.md) ("Merge execution"
    and its merge-approval decision-gate row, "flips PR to draft"); and `gh pr ready <N>` (the
    resolver's draft→ready flip), cited to [docs/specs/resolver.md](specs/resolver.md) (its
    invariant: the flip runs immediately before the handoff, or the evaluator's draft-PR guard
    deadlocks it — carried from v1 SKILL.md:896). That third executor has **two** triggers, one
    v1-spec'd and one v3.x: the last-planned-phase-shipped flip on a multi-phase issue, and the
    epic integration PR's flip once its story set closes (the integration PR now opens early as a
    draft — see §6's epic lifecycle — so it spends most of the epic's life in the state the guard
    protects). §7 rule 7 is the source of truth for which ops are script-backed.
  - **`git`-ref rule (ref-arithmetic-scoped).** Zero `git show <ref>:<path>` / `git grep <ref>` in
    `skills/` — a bare `git show <commit>` single-commit diff view is permitted; only the
    `<ref>:<path>` extraction (and `git grep <ref>`) is the banned ref-arithmetic form.
  - **Never-resolve rule (#145).** Zero occurrences of the GraphQL thread resolve or unresolve
    mutation's name anywhere under `skills/` or `scripts/`, prose and code alike
    (`tests/test_review_threads_contract.py`, validator 8 in `CLAUDE.md`). The names live only under
    `tests/`; docs say the rule in words.

## §11 Migration & coexistence

- Cutover is **per skill**, in the [implementation.md](implementation.md) order. Old and new
  skills interoperate through the [prd.md §7](prd.md) artifacts only, so a v2 evaluator works a
  PR a v1 resolver opened.
- A v1 skill directory is deleted only after its v2 replacement passes the parity protocol
  ([prd.md §9.5](prd.md)).
- `_shared/` files defining **external** artifacts (handoff format, DoD annotations,
  open-question links/detection, question-issue, epic-delivery-log, plan-parts, plan-shipped-phases, worktree block formats) are
  preserved; **internal** coordination files are superseded per §3 (`subagent-decision-signal.md`,
  removed at S20) and §6 (`worktree-lifecycle.md`'s mechanics folded into `workspace.py` at S20 —
  the file now carries only the consuming-repo block format; the ownership rules live here).
- The v1 executor sub-agent prompt under `agents/` and every v1 `scripts/*.sh` were removed in the
  final cleanup step (S20), after their last v1 caller was cut over. Nothing in the tree references
  them; `tests/test_v1_retirement.py` is the standing guard.

## §12 Invariants registry

Non-negotiable. Each carries its enforcement point; a change here is a constitution-level event,
not a deviation.

| Invariant | Why (short) | Enforced by |
|---|---|---|
| Size gate: no body-bearing write without a non-empty staged file, and none over the platform's per-body character limit | #626/#627 empty-body race; an over-cap body is rejected at every endpoint, so it must fail as a decision before the write, not as a raw `gh` error after the session's work | `gh_persist.py` size check + `EMPTY_BODY_FILE` / `BODY_TOO_LONG` + tests |
| Bodies cross the prompt boundary as paths, never re-serialized | same race | staged-path convention (§7) |
| Byte fidelity: persists verify the round-trip hash and return `body_sha256` | silent mangling is invisible | `gh_persist.py` + tests |
| Successful write is self-confirming; never re-read to verify | re-reads reintroduce races | §3 rule + router invariant |
| Marker replacement never leaves the issue without a marker: `comment --delete-marker-id` posts the new comment before deleting the old, and `edit-comment` writes in place with no delete at all | a crash must not lose the marker. The in-place `PATCH` is not a weakening of this rule but its stronger form — post-then-delete makes the zero-or-two-marker window *safe*, a single atomic write has no window. Delete-and-repost stays: only it can collapse a duplicated marker, and only it works when no marker exists yet | `gh_persist.py` + tests |
| Spill threshold on verbatim sections | context blowout | `pipelib` spill + tests |
| Capability-gated degradation (native deps; native parent/sub-issues) with a per-relation notice | consuming repos and `gh` versions vary, and the two relations have different fallbacks | `gh_persist.py`/`gh_gather.py` ladders + tests |
| Epic↔story hierarchy is the native parent/sub-issue relation, written only by the slicer — at filing time via `create --parent`, or after the fact via `add-parent` when an epic adopts an already-filed issue; readers fall back to a legacy `## Stories` checklist and never gate on the relation's absence | GitHub's sub-issue panel, progress rollup, and a Project's Sub-issues progress field are driven by the relation, not by markdown a checklist can't self-tick; and no backfill path exists, so pre-relation epics must keep working | [`skills/_shared/epic-story-hierarchy.md`](../skills/_shared/epic-story-hierarchy.md) + `create --parent` + `story_set.py` + prep two-tier reads + tests |
| An implementation plan is one document posted as three comments — the main `<!-- implementation-plan:v1 -->` comment (decisions, phases or story sections, risks, open questions; every heading kept, a moved section's body reduced to a stub link) and the `<!-- implementation-plan-part:v1:build -->` / `:references -->` parts — cut by `plan_parts.py split` at the post (parts first, main last) and reassembled by every reader's prep, so no prompt or sub-agent sees the split; the cap is per comment | GitHub caps each comment at 65,536 characters and real plans exceed it (a 61k story plan is ordinary, decisions alone reaching 32k); splitting by section gives each part its own cap from the first post, mechanically, where the 4.20.0 per-phase relocation helped only after phases shipped and cost a judgment-heavy move on every revise. Legacy shipped-phase records (`<!-- implementation-plan-shipped:v1:phase:<N> -->`) are no longer written; readers follow the plan's pointer bullets to them until the next revise folds them back verbatim, after which they are inert (never deleted) | [`skills/_shared/plan-parts.md`](../skills/_shared/plan-parts.md) + [`skills/_shared/plan-shipped-phases.md`](../skills/_shared/plan-shipped-phases.md) + `plan_parts.py` (split + assembly in `gh_gather.py` / `prep_evaluator.py`) + `plan_shipped.py` (read model + fold-back `check`) + tests |
| The epic delivery log is one comment per shipped story (`<!-- epic-delivery-log:v2:story:<N> -->`), written only by the evaluator; readers resolve it across two tiers, union by story number with the per-story entry winning, and never gate on a tier's absence | a single accumulating comment is bounded by the per-body character limit and an epic reaches it by construction — past that point no story merge can be recorded at all, with no compaction path and no owner but the append (#41). The story number in the marker is what keeps one-marker-one-comment, so a genuine duplicate is still detectable; and no backfill path exists, so pre-#41 epics must keep working | [`skills/_shared/epic-delivery-log.md`](../skills/_shared/epic-delivery-log.md) + `delivery_log.py` + prep two-tier reads + tests |
| The default branch changes only via PR; a session never commits outside its own asserted workspace; the landing tools treat their starting checkout as read-only | trust topology (§6) | `workspace.py` decisions + prompt invariant |
| Hook and gate config are read from the working tree of the checkout the command runs in — committed or not — and every read reports its source (checkout, branch, SHA, dirty) as `setup.source` / `teardown.source` / `config.source`; no path gates on that source, and none inspects or writes the operator's checkout | the operator chooses the checkout and it is trusted; the retired `origin/main` pin made the consuming repo's own config untestable before merge, and reporting rather than a per-entry confirmation card is the compensating control (§6 rule 1 — a reversal of the v3 "a PR must not weaken its own gates" invariant, made deliberately; the one narrow exception is #96's sensitive-path declaration, where a PR that changes it is itself gated, by comparing the declaration's content with the merge base rather than by gating on `config.source`) | prep scripts + `workspace.py` + tests |
| The default branch is derived (`git symbolic-ref refs/remotes/origin/HEAD`, then `gh repo view`), never hardcoded | repos on `master`/`develop`/`trunk` are ordinary | `workspace.default_branch` + tests |
| All tracked-file changes land via PR | write-protected `main` | prompt invariant + review |
| No ref arithmetic, no raw `gh` writes in prompts; the ambient checkout is prep-asserted (`WORKSPACE_MISMATCH`) before use, and sub-agent dispatches name absolute paths | the drift class the rewrite exists to kill | §10 prompt validators + prep tests (carve-outs in §10: `gh pr merge --match-head-commit <gated head>` / `gh pr ready` [evaluator `--undo`; resolver draft→ready flip]; bare `git show <commit>`) |
| Contract tokens are frozen (marker strings, op names, and closed sets a second component parses — another skill, a GitHub artifact or persisted record, or the board mod). History: on 2026-10-25 #115 replaced the coordinator's `next-story` stage and `pick` next kind with `stories` and removed `--story` and `epic.candidates`, while the coordinator's prep and its own router were the set's only readers; since #116 the board mod reads the stage set, so a further change is a constitution-level event | cross-skill/GitHub parse compatibility | census greps (§10) |
| Skills are stack-agnostic (gated integrations + ≥2-stack examples only) | multi-stack product | banned-pattern greps (§10) |
| Session-per-skill; no autonomous stage chaining: a pipeline stage never starts another stage's session or crosses the session boundary (no `Skill` call to another stage, ever). Only the `coordinator`, an operator-invoked standalone tool, may start stage sessions — only on the operator's word, each in its own fresh session (a resolver session shipping several phases of one issue is one skill, not a chain — each phase still re-runs prep, and the session raises a review at a pause, continuing only on the operator's **Reviewed, continue** answer and only while its context usage, measured against the host's auto-compact point, stays under 65%; `--pause` ends it after every phase) | context isolation is the design | prompt invariant + review |
| The evaluator merges only the head it gated, and only when that head contained the base tip at its last pre-merge refresh (the base is not re-checked after that refresh); otherwise the merge is `merge: skipped (head-moved \| base-moved)` with the reason named | a head pushed after review, or a base that gained commits the gated head does not contain, would merge code nobody gated | `prep_evaluator._merge_guard` (`merge_guard`, `--gated-head`) + `gh pr merge --match-head-commit` + `tests/test_prep_evaluator.py` / `tests/test_evaluator_routing.py` `MergeGuardTests` |
| No APPROVE while a `## Security review` entry is `open` — an unparsed line or a pass that did not complete counts as one — and only a person dismisses an entry, with a recorded reason: by a trusted `dismiss: <reason>` reply on the finding's own review thread (bound to it by the root's `Claim:` line), which a trusted account resolves, re-read from the thread on every run (it counts on any PR, `security.entries[].thread.confirmed`); or, for a finding no thread can anchor or a resolved thread with no reply, on the evaluator's `Security` consent card, a dismissal citing no thread counting only on a trusted-author or own-account PR. A dismissal that does not count is `open` | a security flaw must block the merge until a person decides it, and the record is self-declared body text the model must not be able to clear on its own — so a thread-cited dismissal's reason comes from the thread, never from the body line alone (#145) | `parse.scan_security` + `review_threads.confirm_dismissals` → `facts.security` + evaluate-spine S4.5 / S4.6 / S5 + `tests/test_security_review_contract.py` / `tests/test_review_threads.py` |
| No APPROVE while a gating review thread is not `accepted` (resolved by a trusted account, its anchored hunk unchanged since), nor on a failed or partial thread read; the evaluator posts its threads once, at the gated head, and the resolver only replies. A batched thread of routine out-of-plan files (#163) is `accepted` only while every member is unchanged. A thread a trusted pipeline successor replaced (`replaced`: it is `superseded`, or a batch no trusted human commented on after its successor's root) gates nothing: that successor carries every member still in its target set, and each member it dropped gets its own gating thread; a trusted human comment written after the successor still gates | the operator often authored the PR and cannot approve it on GitHub, so a resolved thread is the record that a person looked at the flagged code; one posted mid-flight would go stale as later rounds move the code (#145). Batching routine files keeps every file acknowledged by a person without one thread per file, and the successor rule lets a batch's membership change without discarding that acknowledgement or leaving a file uncovered (#163) | `review_threads.read_threads` / `derive_state` / `flags` → `facts.review_threads` + evaluator S4.6 + `tests/test_review_threads.py` / `tests/test_prep_evaluator.py` |
| The pipeline never resolves or unresolves a review thread, by any route; `gh_persist.py` offers no such op | a reader counts this account's resolution as a human's only because the pipeline never makes one — if it could, a resolution would prove nothing (#145) | no op in `gh_persist.py` + `tests/test_review_threads_contract.py` (validator 8: the mutation's name nowhere under `skills/` or `scripts/`) |
| A sensitive-path match, a change to the declaration itself, or a failed or unparsed read of it forces the evaluator's S7 to `ask` whatever the PR type's merge policy; it is never a decision code | the paths a consuming repo names are the ones an unattended merge must not touch, and a PR must not be able to lift the gate by editing its own declaration | `sensitive_paths.assess`, wrapped by `prep_evaluator._sensitive_paths_fact` → `config.sensitive_paths.forced_ask` + `tests/test_sensitive_paths.py` / `tests/test_prep_evaluator.py` `SensitivePathsTests` |
| Only text an `OWNER` / `MEMBER` / `COLLABORATOR` author wrote reaches a review-loop record, round or tally, seeds a resumed review loop from PR comments or, for `facts.interrupted`, PR and issue-thread loop comments, counts as a review-thread comment or reply (a marked comment, which moves a thread's reference commit, or a `dismiss:` reply, which dismisses a security finding, only when its author is this account or holds `admin` / `maintain` / `write`) or as a recorded security dismissal or, for the resolver's and evaluator's `facts.polish`, a recorded polish disposition (else only a PR this account opened does); a thread resolution, which carries no association, counts only when the resolving account's repo permission is `admin` / `maintain` / `write`, or with no lookup when it is this account's; an unknown association or permission is untrusted (SEC-1, #139, #145) | any GitHub user can comment on a public PR or edit their own PR's body, the report's recurring classes become standing reviewer guidance, and a forged settle or dismiss would skip the operator | `pipelib.trust.TRUSTED_ASSOCIATIONS` (re-exported as `review_report.TRUSTED_ASSOCIATIONS`), shared by `review-report`, `prep_setup.py --repo`, `prep_resolver` `facts.resume` / `facts.interrupted` / `facts.polish.dispositions_trusted`, `prep_evaluator` `facts.security.dismissals_trusted` / `facts.polish.dispositions_trusted` and `review_threads.read_threads` (both preps, with `pipelib.trust.TRUSTED_PERMISSIONS` for resolutions) + `tests/test_review_threads.py` / `tests/test_review_report.py` / `tests/test_prep_setup.py` `PrepSetupReviewHistoryTestCase` / `tests/test_prep_resolver.py` / `tests/test_prep_evaluator.py` / `tests/test_polish_ledger_contract.py` |
| Blast-radius signals — the fact signals and the blast-radius judgment's `raise` alike — only raise the review level (one step in total, capped at `xhigh`; a line or file count sets nothing) and only add pauses, never remove one; only the operator's **Reviewed, continue** answer continues past a review, and only while the context rule (under 65% of the auto-compact point) leaves room — the context rule only ends sessions earlier | a signal that could lower review or skip a pause would let a risky change pass with less scrutiny than the plan declared | `blast_radius.build_assess` / `phase_review.build_context` + `parse.scan_blast_judgment` + `tests/test_parse.py` + `skills/resolver/references/phase-continuation.md` §2 / §4 + `tests/test_blast_radius.py` / `tests/test_phase_review.py` |
| Scripts never author prose; sub-agents never write to GitHub | role separation (§2) | review + tests |
| Scripts are stdlib-only Python spawning only `git`/`gh` (sole carve-out: `workspace.py`'s hook runner, §1) | excludes the BSD/GNU userland divergence class | review + dual-platform suite runs (§10) |
