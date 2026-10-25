# Handoff renderings

The eleven rendering shapes the resolver emits as the run's final `## Handoff` block. Pick the one that
matches the run's outcome, copy the shape, and substitute the issue/PR/epic/SHA placeholders from the
prep facts + this run's PR/review/push results. Schema, omission rules, and the closed-set state-marker
vocabulary live in [`../../_shared/handoff-format.md`](../../_shared/handoff-format.md) — this file
renders them at point of use, it does not restate the schema.

Next-command skills are the v2 pipeline names (`evaluator`, `planner`, `drafter`, `resolver`),
namespaced `/github-pipeline:<name>`. **`Workspace:` lines** (the v3 "start the next session
here" carrier — see `_shared/handoff-format.md`) substitute `<workspace-path>` from
`facts.workspace.path`: present on every route whose next session must run inside this PR's
worktree (→ evaluator, → resolver in continue mode, → planner revise **with** a draft PR, whose plan
grounds on the PR head); on a planner revise **without** a PR the planner grounds on `main` or on
this worktree's branch while it still contains `main`'s tip, so render `**Workspace:** <workspace-path>
(rebase onto origin/main first if main has moved), or any current main checkout` instead; omitted on
drafter re-routes and terminals (checkout-agnostic).

**The resolver's own command** — every shape that hands back to the resolver — is
`/github-pipeline:resolver #<N>` with `<N>` = `facts.target.number`: the issue this run resolved
(the story or epic on those routes). Never `facts.prior_pr`'s number or the PR this run opened, and
no `continue` keyword: the next prep re-derives continue mode from the issue's PR state, and a PR
number is refused as `TARGET_IS_PR` (`_shared/handoff-format.md`, "Authorship"). When this session held
`--pause` (SKILL.md §1), the command ends with it — `/github-pipeline:resolver #<N> --pause` — on every
shape that hands back to the resolver (the non-final `Next:`, the operator-phase `Then:`).

**`Changes:` lines** (the reviewable link to what *this session* pushed — see `_shared/handoff-format.md`) appear on every shape whose session pushed at least one commit, and are omitted on the no-push exits (the drafter re-routes, both terminals, and a PR-less planner re-route). Substitute the entry SHA from `facts.workspace.upstream_sha` as the session's **first** prep reported it — its `facts.workspace.sha` unless an interrupted run left commits unpushed, which this session ships (a between-phase prep re-run, `phase-continuation.md`, moves the field but not this anchor) — the head SHA from `git rev-parse HEAD` run in `facts.workspace.path` **after the review loop settles** (its fix rounds add commits of their own, pushed once at the spine's S5.2, so a HEAD read before the loop exits stops short of what the run shipped), and the PR url from `facts.prior_pr.url` (continue mode) or the `create-pr` envelope's `url` (fresh mode); SHAs are 7-char, matching `dod-annotations.md`. Render the range form `<pr-url>/files/<entry-sha>..<head-sha>` whenever the entry SHA is a commit **in** the PR (the usual continue-mode case). Render the whole-PR form `<pr-url>/files` when it isn't — the entry SHA is the PR's base commit, which GitHub 404s on the range form (the usual fresh-run case, where the whole PR is this run's changes), or an epic run's drift rebase rewrote it out of the history. A session that shipped more than one phase names each phase's range ahead of the link (`Phase 2: 4c1d80f..7b3e5a0 · Phase 3: 7b3e5a0..9f0a112`), each phase's entry being the `upstream_sha` of the prep that started its pass; the link stays last.

**The `PR:` line's `review:`/`health:`/`merge:` markers are
`not run` on every resolver-authored handoff whose PR hasn't reached the evaluator yet — forward exits
AND every re-route (including a mid-phases continue-mode re-route back to the resolver itself).** Per
`_shared/handoff-format.md`'s closed set, `review:` is the **evaluator's** posted GitHub review verdict
(`APPROVE` / `COMMENT (soft-reject)` / …) and `health:` is the **evaluator's** branch-health gate result
— neither field means "the resolver's own `code-review` skill or §8 pre-push gate ran." The resolver runs
both internally on every phase, but that is not what these two fields denote, so they stay `not run`
until the evaluator has actually acted on the PR — even on a re-route where a phase's review loop and
health gate passed cleanly. Never render an off-closed-set glyph (e.g. a bare `✓`) here; there is no
value in the closed set for "ran internally, no formal verdict yet" — `not run` **is** that state.

**Finalisation in the `Why:`.** When the final pass ran finalisation
([`finalisation.md`](finalisation.md)), the forward handoff's `Why:` names its outcome by ledger id —
applied, filed (by group), dropped, left open for the evaluator — so whoever starts the evaluator knows
what is already decided and what S5.5 will still ask. It names the security pass's outcome by `SEC-<n>`
id the same way — fixed, dismissed — and each entry left `open` — by an **Accept current** exit, or a fix
no round landed before the push — which the evaluator blocks on, or that the pass did not complete
([`finalisation.md`](finalisation.md) §1.5); a re-route's `Why:` names those `open` ids too. A
finalisation **Re-plan** takes **Re-route → planner** instead: its `Why:` quotes each re-plan entry by id
and the decision its note names (or the two intents), in place of a locked decision and a `file:line`.
Its PR line carries the PR's actual state — `draft` on a multi-phase PR, `open` on a single-phase one
(S5.2 opens only a multi-phase PR as a draft) — and the `Workspace:` line points at this worktree either
way, since the revise grounds on the PR head.

## Forward — standard or story PR opened / updated

The default code-change outcome. For a story PR under an open epic, the `Issue:` line is replaced with
`Story:` and an `Epic:` line is added per `_shared/handoff-format.md`'s Epic-variant rules.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✓
**PR:** #287 — Add CSV export (#142) · open · base main · review: not run · health: not run · merge: not run
**Changes:** 4 commits (the whole PR — opened this run) — https://github.com/owner/repo/pull/287/files
**Workspace:** <workspace-path> — start the next session here

**Next:** evaluate the PR in a fresh session.

    /github-pipeline:evaluator #287

**Why:** the evaluator runs the branch-health gate, checks the diff against the issue's acceptance criteria + the plan's locked decisions, posts a formal review, and on a clean APPROVE auto-merges (standard / story PRs) or asks for the merge mode (Epic integration).
```

## Re-route — multi-phase, non-final code phase pushed

A code-shipping phase landed on the draft PR; the plan's `## Phases` still lists unshipped phases, and
the session ends there — typically at a phase checkpoint pause: a look the operator did not continue past, or `--pause` (`phase-continuation.md`, which prints its
`## Session summary` immediately before this block), or on any other exit that pushed a non-final phase (an `Abort` / `Abort loop`
answer). The next session continues the same multi-phase resolution. When this session shipped several phases, the `Why:`
names each one.

At a look (`phase-continuation.md` §4) this block prints **before** the look card, and stands unless **Continue here**
arrives within the 55-minute window. In a coordinator-started session it carries `(provisional: a look card is open,
55 minutes)` on its own line above the heading. On an in-window continue it is superseded, and the session's final
handoff, with the session-wide `Changes:` line, is the one that counts.

```
## Handoff

**Issue:** #640 — Spike: Mitigate Gemini thinking-token truncation · open · feature · plan: ✓ (multi-phase: 2 of 4 phases shipped)
**PR:** #649 — feat(llm): #640 spike harness · draft · base main · review: not run · health: not run · merge: not run
**Changes:** 3 commits, 4c1d80f..9f0a112 — https://github.com/owner/repo/pull/649/files/4c1d80f..9f0a112
**Workspace:** <workspace-path> — start the next session here

**Next:** continue with the next phase in a fresh session.

    /github-pipeline:resolver #640

**Why:** the plan's `## Phases` declares 4 phases; this run shipped Phase 2 (`harness PR`) onto the draft. Phase 2's `closes-dod` bullets have been projected onto the issue body's `## Definition of done`. The next planned phase is **Phase 2-measurement** (operator run — see the operator-action handoff if it fires next), followed by **Phase 3 — decision write-up**. The PR stays in draft until every phase has shipped and the evaluator runs its DoD check.
```

## Terminal-with-action — multi-phase, next phase is operator / decision-only

The next phase ships a comment or runs an operator action, not commits. The resolver can't perform the
action; surface it verbatim from the plan's `deliverable` so whoever runs it doesn't have to look it up.

```
## Handoff

**Issue:** #640 — Spike: Mitigate Gemini thinking-token truncation · open · feature · plan: ✓ (multi-phase: 2 of 4 phases shipped)
**PR:** #649 — feat(llm): #640 spike harness · draft · base main · review: not run · health: not run · merge: not run
**Changes:** 3 commits, 4c1d80f..9f0a112 — https://github.com/owner/repo/pull/649/files/4c1d80f..9f0a112
**Workspace:** <workspace-path> — start the follow-up resolver session here

**Next:** run the operator phase, then return to the resolver.

    ./scripts/spike-640.sh
    # then post the per-cell table from build/spike-640-*.log as a comment on #640
    # include this marker on its own line so the next resolver run picks up the
    # operator phase deterministically (otherwise it will fall back to asking you):
    #     <!-- operator-phase-complete: 2-measurement -->

**Then:** once the measurement comment is posted, continue with the following phase in a fresh session.

    /github-pipeline:resolver #640

**Why:** the plan's Phase 2-measurement is `kind: operator` — it ships a per-cell measurement comment on the issue, not PR commits. The resolver can't run the harness for you; once you post the measurement comment, Phase 3 (decision write-up) becomes runnable from the resolver. The `<!-- operator-phase-complete: <N> -->` marker is the next resolver's deterministic signal that the operator phase landed — it ticks the PR's `## Phase tracker` and projects the phase's `closes-dod` onto the issue body's `## Definition of done`. Omitting the marker is fine; the next resolver run will ask via `AskUserQuestion` instead of auto-applying.
```

## Forward — multi-phase, last planned phase shipped

Every phase in `## Phases` is ticked in the **reconciled** `## Phase tracker` (spine S4). The plan owns
the row set, so a plan phase with no row is *unshipped*, never not-applicable — an un-reconciled
tracker with fewer rows than the plan reads as complete and flips the PR draft → ready early.
**Immediately before emitting this handoff,
run `gh pr ready <N> --repo <owner/repo>` to flip the PR draft → ready.** Without that flip, the
evaluator's draft-PR guard deadlocks the handoff. The PR-line's `state: open` marker below reflects the
post-flip state.

```
## Handoff

**Issue:** #640 — Spike: Mitigate Gemini thinking-token truncation · open · feature · plan: ✓ (multi-phase: 4 of 4 phases shipped)
**PR:** #649 — feat(llm): #640 spike harness · open · base main · review: not run · health: not run · merge: not run
**Changes:** 2 commits, 7b3e5a0..9f0a112 — https://github.com/owner/repo/pull/649/files/7b3e5a0..9f0a112

**Phases:** all 4 planned phases shipped at 9f0a112; PR flipped to ready for the evaluator (`gh pr ready 649`).
**Workspace:** <workspace-path> — start the next session here

**Next:** evaluate the PR in a fresh session.

    /github-pipeline:evaluator #649

**Why:** every phase in the plan's `## Phases` has been ticked on the PR's `## Phase tracker`, and each ticked phase's `closes-dod` bullets have been projected onto the issue body's `## Definition of done` as the phases shipped. The evaluator verifies each projected DoD tick against its attributed phase's diff (per-phase commit ranges from the Phase tracker), runs its branch-health gate and review against the plan's locked decisions, un-ticks any bullet whose attributed diff doesn't actually satisfy it (sticky soft-reject), and — on a clean APPROVE — merges. On a COMMENT (soft-reject) verdict, the evaluator flips the PR back to draft so this resolver can re-enter in continue mode and address the gaps without re-deadlocking on the draft guard.
```

## Forward — revision run (every phase already shipped)

A continue-mode run whose `facts.revision.active` is true (spine S4): no phase shipped this run, so the
`Issue:` line carries no phase count and nothing new was projected onto the DoD. The PR was flipped
back to ready (`gh pr ready <N>`) immediately before this handoff, as on the last-phase shape. A
revision run that found a vetoed DoD bullet (`dod_rejected`) does **not** use this shape — it re-routes
before any code, rendering "Re-route → planner" with a `Why:` quoting the evaluator's
`… evaluator rejected: …` annotation.

`Why:` names, by `F<n>` (an operator-opened thread by `O<n>` and its URL), each review thread the run replied in
through `review-reply` (spine S5.2) — only those: a thread whose reply came back `posted: false` was resolved before the
run reached it and is not named. Every thread stays unresolved for the human to resolve in GitHub.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✓
**PR:** #287 — Add CSV export (#142) · open · base main · review: not run · health: not run · merge: not run
**Changes:** 2 commits, 9f0a112..c3d4e5f — https://github.com/owner/repo/pull/287/files/9f0a112..c3d4e5f
**Workspace:** <workspace-path> — start the next session here

**Next:** re-evaluate the revised PR in a fresh session.

    /github-pipeline:evaluator #287

**Why:** a revision run on the evaluator's soft-reject: every planned phase had already shipped, so this run addressed the review's findings, the requested-changes threads F2 and F4 (replied in each, left unresolved), and the `## Polish` ledger's `apply` items (P2.3 → `applied (commit c3d4e5f)`) under the final-scope review loop, then flipped the PR back to ready. The evaluator re-runs its gate and review against the revised head.
```

## In progress — Epic integration draft PR open (stories remain)

The integration PR opens as a **draft** as soon as the epic branch is ahead of `main`, and is refreshed
on every epic run — it is the human-readable view of overall epic progress, not a merge request. So its
`Next:` is the epic cadence's next step (the planner for the next story, or a story resolver run),
**never the evaluator**: the evaluator refuses a DRAFT PR, so pointing there would deadlock. Add the
open PR's author to the `Why:` when it isn't yours.

```
## Handoff

**Epic:** #150 — Chat & session UX polish · open · epic · plan: ✓
**Stories:** 3 of 5 closed
**PR:** #300 — Chat & session UX polish (epic #150) · draft · base main · review: not run · health: not run · merge: not run
**Changes:** 1 commit, 5e2af41..c0d19b6 — https://github.com/owner/repo/pull/300/files/5e2af41..c0d19b6
**Workspace:** <workspace-path> — the next story's plan grounds here

**Next:** plan the next story in a fresh session.

    /github-pipeline:planner #153

**Why:** the epic branch is ahead of `main` by 3 landed stories, so the integration PR is open as a draft carrying the accumulated diff — review overall epic progress there at any time; this run refreshed its body rather than opening a second PR. It stays draft (and out of the evaluator's reach) until #153 and #154 close and the epic's `## Definition of done` is verifiable against that diff; only then does the resolver run the review loop and flip it ready.
```

## Forward — Epic integration PR

Same forward direction (→ evaluator). The PR already existed as a draft — this run flipped it ready —
and the `Why:` line calls out the higher merge risk and the canonical-suite escalation so the user knows
what the evaluator will do differently.

```
## Handoff

**Epic:** #150 — Chat & session UX polish · open · epic · plan: ✓
**Stories:** 5 of 5 closed
**PR:** #300 — Chat & session UX polish (epic #150) · open · base main · review: not run · health: not run · merge: not run
**Changes:** 2 commits, 5e2af41..c0d19b6 — https://github.com/owner/repo/pull/300/files/5e2af41..c0d19b6
**Workspace:** <workspace-path> — start the next session here

**Next:** evaluate the Epic integration PR in a fresh session.

    /github-pipeline:evaluator #300

**Why:** integration PRs land the accumulated diff of every child story onto `main` at once. The evaluator's escalation rules fire on `pr_type: epic-integration` — the full canonical test suite runs before merge, the verdict is checked against the epic's `## Definition of done`, and the merge mode is gated even on a clean APPROVE.
```

## Re-route → planner

Triggered by plan-currency drift (fitness audit dimension 7 / distiller `THREAD_SUPERSEDED_PLAN`) or a
plan-invalidation surfaced mid-implementation. The `Why:` quotes the locked decision verbatim and cites
the `file:line` where the contradiction surfaced. If a draft PR was opened before the invalidation
surfaced, the PR line carries `state: draft` and stays open so the resolver can continue from the same
branch after the plan is refreshed.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: stale
**PR:** #287 — Add CSV export (#142) · draft · base main · review: not run · health: ❌ at abc1234 · merge: not run
**Changes:** 2 commits, 9d41c0e..abc1234 — https://github.com/owner/repo/pull/287/files/9d41c0e..abc1234
**Workspace:** <workspace-path> — the plan revise grounds on this PR's head; start the planner session here

**Next:** revise the plan in a fresh session — implementation revealed a locked decision is unbuildable.

    /github-pipeline:planner revise #142

**Why:** the plan's `## Architecture decisions` line "<quoted decision>" assumed <X>, but `<path:line>` reveals <Y>. The plan-currency check failed (alternatively: the plan-invalidation gate fired mid-implementation). Refresh the plan against today's surface before resuming. The draft PR stays open; re-run the resolver in continue mode after the plan revise lands.
```

If no PR was opened yet, omit the PR and `Changes:` lines entirely, render `**Workspace:** <workspace-path>
(rebase onto origin/main first if main has moved), or any current main checkout` (a PR-less revise grounds
on `main`, or on this issue's own branch while it contains `main`'s tip — row `own-branch`; a branch behind
it is refused). Either way the resolver later
re-enters with `/github-pipeline:resolver #142` — the issue, whether or not a PR exists.

## Re-route → drafter (fitness audit)

Triggered by the fitness audit finding a blocker (a body claim referencing a symbol no longer in the
codebase, an unevaluable acceptance criterion, or a contradiction between body sections). The `Why:`
names the dimension and quotes the specific evidence.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✗

**Next:** revise the issue body in a fresh session — the fitness-to-implement audit found a blocker.

    /github-pipeline:drafter revise #142

**Why:** the body's acceptance criterion "<quoted criterion>" references `<symbol>`, which doesn't exist in the current codebase (closest match: `<symbol>` at `<path:line>`). The criterion needs to be reshaped against today's surface — or the codebase needs a precursor change — before planning can ground in real precedent.
```

## Re-route → drafter (doc conflict)

Triggered by the doc-grounding step finding the issue body directly contradicts a project doc the
resolver can't reconcile in-skill. Same shape as the fitness re-route; the `Why:` cites the doc section
verbatim.

```
## Handoff

**Issue:** #142 — Allow editing submitted forms · open · feature · plan: ✗

**Next:** reshape the issue body in a fresh session — the body contradicts `docs/prd.md`.

    /github-pipeline:drafter revise #142

**Why:** `docs/prd.md` §4 says "entries are immutable after submit"; the issue body proposes an edit-after-submit feature. The user chose `Reshape issue` at the doc-conflict gate. The drafter's revise mode either reshapes the body to fit the PRD or routes the conflict back to the user to decide whether the PRD itself should change.
```

## Terminal — non-PR resolution

Comment-only answer, OQ / native-blocked refusal, triage-only re-labelling, or abandoned/declined work
on a **build** issue (`bug`/`feature`/`incomplete`/`story`/`epic`) whose thread the comment resolves
without code. No PR was opened; the handoff names the issue's current state and closes the pipeline for
this run.

```
## Handoff

**Issue:** #142 — Duplicate of the export feature · open · feature · plan: ✗

**Next:** (terminal — no follow-up skill)

**Why:** the resolver posted a comment identifying this as a duplicate of #138; no code change was warranted and no PR was opened. The issue stays open in its current state for the user (or a future resolver run) to take forward.
```

For an OQ / native-blocked refusal, the `Why:` instead names the blocking `question` issue `#<N>` and
that resolving it via the tracker unblocks a future resolver run. For abandoned / declined outcomes,
name the reason in the `Why:` (e.g. "the user declined to open a PR after the existing-work check
surfaced a duplicate in #138").

## Terminal — question-type issue

The issue's own type is `question` — answered by a human in the thread, not built by the pipeline, so
per [`../../_shared/handoff-format.md`](../../_shared/handoff-format.md) ("Question-type issue") its
`Issue:` line omits the `research:`/`plan:` markers entirely (they don't apply) and carries no `plan`
segment at all — do **not** render `plan: ✗` here, that marker is for build-type issues only — and a
question-only `**Audience:**` line (comma-separated `audience:*` labels) follows immediately.

```
## Handoff

**Issue:** #142 — Should we add CSV export? · open · question
**Audience:** product

**Next:** (terminal — no follow-up skill)

**Why:** the resolver posted a clarifying comment answering (or narrowing) the question in the issue thread; no code change was warranted and no PR was opened. The issue stays open for the audience to weigh in (or close once satisfied).
```
