# ctrl-freaq — Product Requirements (v2)

Product truth for the **ctrl-freaq** Claude Code plugin, authored as the baseline for the v2
rewrite: it states the behavior the rewrite must preserve (derived from v1) and the requirements it
adds. Authoritative-but-mutable; changes route through the normal conflict gate. *What* and *why*
live here; *how* lives in [architecture.md](architecture.md); the migration itself in
[implementation.md](implementation.md).

## §1 Product overview & goals

ctrl-freaq turns GitHub issue delivery into a conveyor of operator-driven Claude Code
sessions — **draft → research → slice → plan → resolve → evaluate** — plus standalone maintenance tools.
Its outcomes:

- Every unit of work is captured as a well-formed, reviewed GitHub issue before code is written.
- Implementation happens against a verified, citable plan pinned to the code it was planned on.
- Every change lands on a write-protected `main` through a PR that has been health-checked and
  judged against the issue's Definition of done.
- The human operator decides at every consequential gate — and only there.

v2 goals, with the operator workflow preserved exactly: (1) every deterministic step is script
work, not model work; (2) the prompt surface is reduced to judgment and contracts; (3) a
session's loaded instructions contain no other type's flow — a router plus one playbook (§9.4);
(4) the deterministic layer is testable offline.

## §2 Personas & terminology

- **Operator** — the single human who starts each session (directly, or through the
  coordinator's one-click go-ahead, §6.8), answers gates, and approves persists and merges. Exactly one; see §3 non-goals.
- **Consuming repo** — the GitHub repository the pipeline works on. Any tech stack. Provides
  configuration via marker blocks in its `CLAUDE.md`/`COMMANDS.md`. Its `main` is write-protected:
  every change lands via PR.
- **Skills** — fixed names. Pipeline stages: `drafter`, `researcher`, `slicer`, `planner`,
  `resolver`, `evaluator`. Standalone tools: `setup`, `question-sweep`, `question-resolver`, `doc-reviewer`,
  `requirements-gatherer`, `workspace-open`, `workspace-close`, `coordinator`, `review-report`, `review`. Invoked as
  `/ctrl-freaq:<name>`.
- **Session** — one Claude Code run of one skill. **Handoff** — the summary + copy-pasteable
  next-command block a pipeline session emits on clean exit; the only bridge between sessions.
  Under the `coordinator` (§6.8) a stage session also messages the coordinator: milestone updates,
  each of its human-readable summaries verbatim (the planner's `## Plan summary`; the resolver's
  `## Plan summary`, Polish digest and `## Session summary`; the evaluator's verdict and merge
  plan), and a verbatim copy of its handoff. That channel copies what the stage already printed and
  carries notifications; it never carries state of its own.
- **Gate** — an explicit decision question put to the operator.
- **Router / playbook** — a skill's always-loaded routing prompt (the router) and the single
  on-demand flow document (the playbook) a session executes.
- **Currency** — whether recorded knowledge still matches the present state of the world (docs
  vs code, plan vs code, external facts vs today).
- **Workspace** — a git worktree under the consuming repo's `.worktrees/`. Two kinds: a **work
  workspace** (branch checkout, where changes are made — opened by the operator with
  `workspace-open`, the session started inside it, released with `workspace-close`; or opened and
  released by the `coordinator` as routine steps it announces in one line, the workspace scripts'
  own safety cards kept) and a
  **read workspace** (a pinned, read-only checkout, script-internal grounding plumbing).
- **Question registry** — the set of `question`-labelled issues; the registry of record for open
  questions (docs are sources, never the registry).

## §3 Scope

**In scope for v2:** all nine skills (v3 adds the two workspace tools, making eleven, the slicer, making twelve, the requirements-gatherer, making thirteen, the coordinator, making fourteen, and the review-report, making fifteen, and the review, making sixteen), the bundled scripts, the optional read-only board mod
(`hooks/`, §6.8), the shared cross-skill contracts, the plugin manifests, and offline tests for the
deterministic layer.

**Out of scope / non-goals:**

- No new pipeline stages, no reordering or merging of stages.
- No schema changes to persisted GitHub artifacts (§7); v1-written artifacts must remain readable
  by v2 and vice versa on the same issues and PRs.
- No autonomous chaining: a pipeline stage never starts another stage's session or crosses the
  session boundary itself (no `Skill` call to another stage, ever); re-routes are carried by the
  handoff for the operator to run. The one sanctioned starter is the operator-invoked
  `coordinator` (§6.8), which starts stage sessions only on the operator's word, each in its own
  fresh session — and, on the operator's click, a review session (#172), the standalone review
  tool run beside a resolver waiting at its Review card, which no stage ever starts — context
  isolation, the reason for this rule, is unchanged.
- No multi-operator concurrency features beyond what git and GitHub natively enforce.
- No stack-specific behavior in skills; stack knowledge lives only in consuming-repo
  configuration and gated integrations.
- Not a general-purpose GitHub client: a skill performs only the operations its stage requires.

## §4 Pipeline workflow requirements

- **§4.1 Session-per-stage.** Each pipeline stage runs in its own session with fresh context.
  Cross-session state travels only through GitHub artifacts (§7) and the handoff. The
  coordinator's message channel (§6.8) is not a third path: it carries verbatim copies of the
  stage's human-readable summaries and its handoff, and progress notifications, nothing else. The
  board mod's `$.store` (its stage log, orientation cache and the stage-session sightings it records
  at each stage's start) and the coordinator prep's `<scratch>/envelope.json` feed file and
  `<scratch>/history-cache.json` (closed stories' `history`, which only the prep's own next run reads)
  are state outside GitHub, and they are presentational only: no skill reads them, no script but the
  prep reads its own cache, and they never source a stage or a gate.
- **§4.2 Handoff.** On clean exit a pipeline skill emits a `## Handoff` per the shared handoff
  schema: cold-readable state using the closed-set state markers, plus the exact next command.
  A re-route to a prior stage carries a `Why:` line stating the evidence for the regression.
- **§4.3 Gates only for genuine decisions.** The operator is asked when a choice changes the
  outcome (scope, deviation, merge, destructive action) — never to confirm a fact a tool can
  derive. A mechanical blocker (auth failure, ambiguous state, dirty root) is presented as a
  single decision card with concrete options.
- **§4.4 Faithful reporting.** Sessions lead with the outcome; failures are reported verbatim
  with their evidence; skipped work is declared, not implied done.

## §5 Pipeline stage requirements

Operator-visible behavior each stage must provide. The detailed v1 behavior inventories that
ground these are produced by implementation step S1. Lettered items are individually citable
(e.g. §5.4(b)).

- **§5.1 drafter.**
  - (a) Captures informal feedback as a filed, template-conformant issue.
  - (b) Classifies bug / incomplete / feature / epic / question, asking only when signals
    genuinely conflict.
  - (c) Grounds framing in the consuming repo's PRD, surfacing contradicts / extends / gap
    tensions as a `## PRD impact` note and conflict gate.
  - (d) Runs an adversarial draft review before filing.
  - (e) *(Retired at #16 — see §5.6.)* The drafter drafts and revises **one** issue's body, an Epic's
    included, and decomposes nothing: splitting an epic into story issues is the slicer's, at epic
    altitude. The letter is kept rather than renumbered because §5.x letters are cited individually.
    (Its original wording — "with the epic body linking them" — was already superseded by the native
    parent/sub-issue relation at 3.1.0, which retired the `## Stories` checklist on a fresh epic.)
  - (f) Never silently absorbs an unresolved open question from a source doc: each is matched
    against the question registry (search before file), filed if untracked, and recorded on the
    build issue per disposition — `scoped-out`, `in-scope (blocked)` (with a native `blocked by`
    dependency), or `provisional-default`.
  - (g) Revise mode updates an existing issue while preserving its plan pointer.
  - (h) Nothing is filed or edited without operator confirmation.
- **§5.2 researcher.**
  - (a) Currency-driven external research: declines when no currency risk exists; otherwise
    derives research questions (broad mode) or answers the given ones (targeted mode).
  - (b) Produces a dossier comment with tiered, dated sources, validated by an isolated review
    before persisting.
  - (c) The dossier is input-not-authority: implications and tensions, never settled decisions.
  - (d) Revise mode refreshes an existing dossier — re-fetching moved sources, re-dating claims,
    answering newly posed questions — and replaces the prior comment.
- **§5.3 planner.**
  - (a) Produces a reviewed implementation plan as a durable issue comment, grounded on an
    explicitly recorded commit SHA (§8.4), citing docs by stable anchor and code by precedent.
  - (b) Locks its decisions and gates genuine tradeoffs to the operator.
  - (c) Declines to plan a change that requires no design decision (a single obvious edit with
    no alternatives worth recording).
  - (d) Epic plans pin cross-story contracts, declare their concurrency (a dependency graph, a
    file-overlap read, and concurrent sets, under `## Integration strategy`; the import-only
    `scripts/concurrency.py` reads the graph and the sets, every gate coming from the graph), and
    stay immutable. A story is planned once the stories
    its `start` edges name have merged, against the epic's then-current state, including what
    those stories actually delivered; the dependency graph and the file-overlap read bound which
    stories plan and build concurrently, rather than every story waiting on every predecessor.
  - (e) Revise mode reconciles the existing plan against thread, PR, and DoD state.
  - (f) Never resolves an open question itself.
  - (g) Before recording any open question as unfiled it searches the question registry; a
    tracked question is always cited by its issue number.
  - (h) Handoffs render the open-questions line in every session shape, including combined
    epic + story sessions.
  - (i) Declares a review level per phase (`## Review level`: `medium`, `high` or `xhigh`) from the
    phase's blast radius, which the resolver reviews at and may only raise (§5.4(e)).
  - (j) The plan reviewer checks an epic plan's concurrency declaration; an absent or malformed
    declaration falls back to the serial `## Story breakdown` order, each story waiting on the one
    before it.
- **§5.4 resolver.**
  - (a) Implements exactly one issue per session against its verified plan.
  - (b) Before building: audits fitness (including plan-vs-code currency) and hard-refuses an
    issue whose open questions leave it `in-scope (blocked)` with the blocking question still
    open.
  - (c) Builds only in the work workspace the session was started in, verified at session
    start (§8.3); refuses on a mismatched, stale, or root-seated checkout.
  - (d) Projects Definition-of-done ticks with annotations as phases ship.
  - (e) Loops with code review until approved, recording each classified finding on a fixed-shape
    line in its loop comment that a script can read (§6.9; the record's contract,
    [`skills/_shared/review-loop-record.md`](../skills/_shared/review-loop-record.md), lists the few items
    that take none). The reviewer receives the consuming repo's own review guidance
    (`<!-- issue-resolver-review-guidance -->`, owner-written, §6.1) as context. Polish the loop leaves is
    recorded in the PR body's `## Polish` ledger
    ([`skills/_shared/polish-ledger.md`](../skills/_shared/polish-ledger.md)) for the operator to decide. A finding whose claim would be defect-tier and which
    the resolver dismisses as refuted, plan-settled or of no shown cost is checked by a context-blind
    pass, and an overturned dismissal returns to the loop. On the final pass, before the push, it
    runs the security pass, which must list every changed file it read; a file left unread after one
    re-dispatch means the pass did not complete. Each finding is fixed in one more reviewed round or
    left open with any proposed dismissal noted, for the operator to dismiss on its review thread at
    evaluation (§5.5(e)), and is recorded in the PR body's
    `## Security review` record
    ([`skills/_shared/security-review.md`](../skills/_shared/security-review.md)), which leaves a
    consuming repo's own section under a similar heading untouched; one left unfixed (an
    accepted stall) is recorded open, and the evaluator blocks on it. Each pass reviews at the level
    the plan's `## Review level` declares for the phase from its blast radius (`high` when it declares
    none; the final cumulative pass at the highest any phase declared), raised one step, at most to
    `xhigh`, when the diff touches a declared sensitive path or files outside the plan's
    `## Changes (file-level)` and `## Test plan` (on the epic-integration pass, whose epic plan names
    no story's files, a file a PR merged into the epic branch changed is not outside it; a file no
    merged PR changed, such as a commit made directly on the epic branch, still is; coverage is by
    file, not hunk), or a context-blind judgment of the diff finds a
    blast radius the plan's level did not account for; a line or file count sets nothing. The level and why
    are recorded in the loop comment, and the PR body states the change's `## Risk` and
    `## How to revert`, which later phases keep current
    ([`skills/resolver/references/review-level.md`](../skills/resolver/references/review-level.md) §4),
    and gives each file outside the plan its own reason in `## Out-of-plan changes`, and each plan
    deviation the operator decided on a card its line in `## Decided deviations` (#181), which the
    evaluator reads so it re-asks no decided question. Its review loop's first
    round reads the PR's review threads and conversation, the trusted authors' text only, and after
    the push it replies in each thread that round addressed; it opens threads only at a review pause
    (§5.4(f)), posted like the evaluator's (§5.5(e)), and never resolves or unresolves one
    ([`skills/_shared/review-threads.md`](../skills/_shared/review-threads.md)).
    None of these PR-side records but `## Decided deviations` is a §7 frozen artifact: each is defined by
    its contract file.
  - (f) Multi-phase issues ship their phases onto one PR, one push per phase, tracked in a
    `## Phase tracker` section of the PR body (§7). One session ships phases in turn — each with
    its own push, tick, and review — until a phase checkpoint pauses, an operator phase, or the
    last phase. A phase pauses when the plan marks it `checkpoint: pause` (or sets no key), a warning
    sign fired during its pass, or its shipped diff raised its review level (a declared sensitive path,
    a file the plan does not name, or the judgment of what the diff reaches); these signals only add
    pauses. A pause raises a **review** — a human review is required before the next phase builds on
    the shipped one: the session prints its summary of what changed, the decisions and deviations,
    and what to check (each linked into the phase's Files view), and its handoff, then a `Review`
    card offering **Reviewed, continue**, **Reviewed, changes required** (a fix round on the shipped
    phase, from the card's notes and the trusted unresolved review threads, then the card again),
    **Review with Claude** (#172; offered when the pause's review posted: it prints the phase-scoped
    review command and its folder, which in a coordinator run the coordinator also offers to start as a
    review session on the operator's click, and the card is asked again without it, the review's decisions reaching a
    later **Reviewed, changes required** through the threads) and
    **End session**. Session length is a separate rule the resolver applies to its own context usage,
    measured against the host-reported auto-compact point: under 65% it may go on to the next phase
    (after a review, only on **Reviewed, continue**); from 65% it finishes the current phase and
    ends; a reading from 90% taken mid-phase (after a review-loop round) stops it there, commits locally without pushing, records the phase as
    interrupted, and hands off to a resolver re-entry that resumes it. A host without a usage tool
    ends the session at every review, with the review printed. An End answer or the host's question
    timeout ends it, and the printed handoff stands. `--pause` ends the session after every phase
    without asking.
  - (g) Comment-only outcomes are supported without code changes.
  - (h) When the plan does not survive contact with the code, re-routes to the planner or
    drafter with the evidence.
  - (i) On an epic target it ships no code — it carries the integration branch's lifecycle, and
    opens the integration PR **early, as a draft**, as soon as that branch is ahead of the default
    branch: the operator gets a reviewable view of overall epic progress from the first landed
    story rather than only after the last one closes. The PR's body is refreshed on each epic run;
    the review loop and the draft→ready flip happen once, when the story set closes and the epic's
    Definition of done is verifiable. Draft state is what keeps a mid-flight epic out of the
    evaluator (§5.5, which refuses a draft PR).
- **§5.5 evaluator.**
  - (a) The verdict-and-merge authority.
  - (b) Runs the health gate (CI plus the consuming repo's configured checks), caching the
    result per head SHA in the `<!-- pr-evaluator-health-cache:v1 -->` comment (§7).
  - (c) Verifies DoD ticks against their annotations, un-ticking with a sticky veto on mismatch.
  - (d) Judges scope match, doc grounding, and plan adherence.
  - (e) Issues APPROVE or COMMENT review actions and never approves its own PR.
    It never approves while a security finding is open: each finding clears only by a fix or by
    a person's dismissal with a recorded reason, and it reruns the security pass unless the PR
    body records one at the gated head on a PR the evaluator's own account opened. It posts the code
    most worth a look — sensitive-path matches, files outside the plan, sites the blast-radius
    judgment cites, open security findings — as review threads at the gated head, chosen by script and
    capped. Files outside the plan that the plan named by a unique basename, or that the repo marks
    generated at both base and head, get no thread; the rest that a triage sub-agent calls routine (a test,
    fixture or mechanical fallout of planned code) share one batched thread a person still resolves, and a
    reply can split a file out into its own (#163). A batch whose every member is a test or fixture is
    **advisory**: listed, but while unreviewed or superseded it does not hold approval, so a wrong test-or-fixture verdict
    can let a file merge unacknowledged, the cost accepted to spare the operator acknowledging test churn; a
    fallout member keeps a batch gating, and an advisory batch with a change request still soft-rejects
    (#181). A test the triage ties to another out-of-plan file rides that file's new thread, and files whose
    only signal is a sensitive-path match share one thread per declared pattern; a per-file thread posted
    before that is never folded into one (#181). It never approves until every gating thread is
    accepted with its code unchanged since — resolved by a trusted account, or answered by the operator's
    `accept` or `dismiss` decision reply, which a chat session records from the operator's consent-card
    answer (#169); a `changes` decision reply requests changes instead — (a thread
    a trusted pipeline successor replaces no longer gates, unless a trusted human comments on it after the
    replacement), nor while a thread a trusted human opened is unresolved and carries no standing `accept`
    or `dismiss` decision reply of the operator's, nor on a failed thread read. The decision reply widens the
    #145 rule by the operator's choice, to remove the trip to GitHub: it is as trustworthy as the evaluator's
    `Security` consent card and weaker than a resolution (`skills/_shared/review-threads.md` §5). A pipeline
    thread whose code moved while it was unreviewed (no one had accepted it and no person's reply waited on
    the resolver) is replaced by a fresh one; one carrying a person's change request stays until the resolver answers it. A trusted reply on a security finding's own
    thread that says to dismiss it, resolved, or the operator's `dismiss` decision reply on it, is that
    finding's dismissal, re-read from the thread on every run; a consent card collects it only for a finding
    no thread can anchor or a thread accepted with no dismissal reply (resolved, or by an `accept` decision
    reply). The pipeline never resolves
    or unresolves a thread ([`skills/_shared/review-threads.md`](../skills/_shared/review-threads.md)).
  - (f) Merges per the configured per-PR-type policy (default `ask`) and the strategy rules for
    the PR's shape — and only the head SHA it gated and reviewed, and only when that head contained
    the base tip at its last pre-merge refresh (`--match-head-commit` pins the head through the
    merge; the base is not re-checked after that refresh, nor by the head-pinned manual command
    printed for a BLOCKED or deferred merge); otherwise it skips the merge with a named reason
    (`head-moved` / `base-moved`), and a fresh evaluator session re-gates the new head. A conflicting
    branch (`DIRTY`) clears the same way, once the conflict is resolved and pushed — on the
    epic-integration PR, `base-moved` and `DIRTY` route through the resolver's epic drift
    rectification first. A PR whose changed paths match the repo's declared sensitive paths
    (`<!-- pr-evaluator-sensitive-paths -->`), or that changes that declaration itself, merges only
    through the operator gate, whatever its type's policy (#96); so does any PR when the declaration
    cannot be read, or carries a line no pattern parses from.
  - (g) On story merge: closes the story and records its entry in the epic delivery log (one comment per shipped story). Closing the story is
    what advances the epic's progress — GitHub recomputes the sub-issue rollup from issue state
    ([`skills/_shared/epic-story-hierarchy.md`](../skills/_shared/epic-story-hierarchy.md)); only a legacy epic still carrying a
    `## Stories` checklist also needs its checkbox ticked.
  - (h) Leaves the work workspace in place after merge and hands the operator the
    `workspace-close` command (the one reclamation path — merged, abandoned, or mis-opened).
- **§5.6 slicer.** Appended after §5.5 rather than inserted at its conceptual position: §5.x numbers
  are cited individually elsewhere (e.g. §5.4(b), §5.5(f)), so renumbering would dangle those
  references. The conceptual order is drafter → researcher → **slicer** → planner.
  - (a) Cuts ONE filed issue into ordered, operator-approved children and files them as native
    sub-issues via the single write path, in approved order (creation order is display order —
    sub-issues append). **One operation at two altitudes** (#16), differing in exactly one parameter —
    the independence bar, set by whether the child gets its own branch and PR: a story or standalone
    issue cuts into **deliverable slices** (*demonstrable*), an epic cuts into **stories**
    (*shippable*). The bar is stated once, in the method reference; the flow reads it as
    `vector.altitude` rather than forking.
  - (b) Reachable two ways: operator invocation, and a planner re-route from the seam gate's shape
    triage — with two or more *demonstrable*-independent milestones, when the operator chooses
    "Slice first" over the recommended multi-phase plan, it cuts slices; with *shippable*-independent
    seams it promotes the issue to an Epic and cuts stories (both of the gate's off-ramps land here
    since #16). It hands back to the planner, whose phases then map onto the slices (`sub-issue:`).
  - (b1) **A slice is a milestone, not an increment.** Every increment becomes a phase of the parent's
    plan whether or not it is sliced, so a slice must earn its issue by what only an issue buys: a
    milestone a stakeholder tracks on the board, with its own acceptance criteria. A story-altitude
    cut that leaves one milestone files **nothing** (the *no cut* exit, behind its own operator card)
    and hands back for a multi-phase plan.
  - (b2) **Promotion.** On the epic-shaped off-ramp it rewrites the target's body as an Epic, rewrites
    its title to carry the `Epic:` prefix (classification is lexical, so a promotion that skipped the
    title would leave the target reading as a non-epic), and swaps
    its type label, behind its **own** explicit diff-and-confirm gate ahead of the cut's write gate —
    a body rewrite is destructive where a `create` is not. Declining leaves the target untouched.
  - (b3) **Epic over existing issues.** Adopts already-filed issues as an epic's children through the
    single write path (`gh_persist.py add-parent`, the one after-the-fact parenting write), reporting
    each candidate's live state — and never silently moving an issue out of another parent.
  - (b4) **Adversarially reviewed before the operator sees it.** A context-blind cut reviewer (§8)
    holds the ordering and sizing judgment for both altitudes, including the bookend-slot check at
    epic altitude and the one-milestone merge (or no-cut) signal at story altitude; findings resolve under a pass cap and a circular guard. This is the judgment that
    previously lived as dimensions 5 and 7 of the drafter's issue reviewer.
  - (c) **Grounding gate.** Refuses to decompose without adequate grounding, and every slice cites
    what it derives from. Grounding sources are the consuming repo's own declaration (the
    `<!-- doc-catalogue -->` block, §7) or sources the operator names at invocation — never
    plugin-side paths. A slice that can cite nothing is surfaced, never filed.
  - (d) **Write gate.** Zero GitHub mutations before one explicit confirmation of the full cut (§8.2).
    A partial failure after the gate reports exactly what landed and never claims completion.
  - (e) **Resume, don't duplicate.** Re-running against a partially-sliced issue detects the existing
    slices and cuts only the remainder.
  - (f) The **cut** never edits the parent issue's body. Child detail lives only in child bodies; no
    `## Slices` section is written. Exactly three parent-body writes are sanctioned, each behind its own
    explicit gate and never as a side effect of filing: the promotion rewrite (b2), reconciling a
    legacy `## Stories` checklist on an epic that has no native relation for those entries, and the
    `## Background` note recording an omitted bookend story and its reason.
  - (g) Refuses, with a reason, on a target that is itself a slice (**never slice a slice**), is a
    `question`, is closed, or is blocked by an open native blocker or an `in-scope (blocked)` open
    question read from live state. An **epic is not refused** since #16 — it is the epic-altitude
    happy path.
  - (h) Slices are **phase markers**, not sub-stories: the resolver ships each as a phase on the
    parent's branch and closes it as its last serving phase lands, so the parent's rollup is a live
    progress record ([`skills/_shared/epic-story-hierarchy.md`](../skills/_shared/epic-story-hierarchy.md)).
    The evaluator is a merge-time backstop only.

## §6 Standalone tool requirements

All ten run only on explicit invocation and end with a plain summary — not a pipeline handoff.
The five report-then-apply tools change nothing without the operator seeing the proposal;
tracked-file edits follow §8.2: staged in a workspace, with the landing offered as a final gate
(the requirements-gatherer, §6.7, edits no tracked files — its one write surface is a gated
issue-body edit). The two workspace tools (§6.5/§6.6) edit no tracked files either — their
action is the workspace lifecycle itself, and the explicit invocation is the authorization. The
coordinator (§6.8) drives the same open, close and sync logic, with no consent card of its own:
an open and the epic sync run on the operator's click of the stage go-ahead that names them, a
close runs as a routine step announced in one line, and the workspace cores' safety decisions
(`BRANCH_IN_USE`; dirty, unpushed or diverged state; `cwd_inside_target`) still render as cards. The
review-report (§6.9) writes nothing at all: it only reads. The review (§6.10) writes only decision
replies in review threads and follow-up issues through the drafter.

- **§6.1 setup.** Proposes and reconciles the consuming repo's configuration blocks: inventories
  existing blocks, drafts grounded candidates from repo evidence, interviews the operator for
  what can't be inferred, and validates what it wrote. Among them it proposes and reconciles the
  sensitive-path declaration — the CI definitions found in the tree plus whatever the operator names.
  It seeds and re-ingests the user-owned review-guidance block (#99) and proposes entries in it for the
  finding classes that recur in the repo's review loop, reading that history through review-report's
  core (§6.9) — on two labelled bases, never pooled, and only through the report-then-apply landing.
  It also proposes `linguist-generated` lines for the generated paths it finds, written to one fenced
  block in the repo-root `.gitattributes` through `scripts/gitattributes.py` (#181), so GitHub collapses
  those files' diffs in the PR view and the evaluator's `generated` exemption can apply; lines outside
  the block are never touched (bar a line ending added to an unterminated last line), and the write lands through the same report-then-apply landing.
- **§6.2 question-sweep.** Reconciles the open questions found across the consuming repo's docs
  against the question registry: files untracked questions, repairs doc↔issue links in both
  directions, flags stale doc state, and reports orphans.
- **§6.3 question-resolver.** Assisted closing of one question issue: grounds the constraints and
  decision space in the pinned docs, records the operator's decision as a durable marker comment,
  optionally closes the issue, and proposes — never applies — the doc fold-back. The operator
  decides; the skill records.
- **§6.4 doc-reviewer.** Reviews a single project doc against its bundled authoring guide and
  offers to apply accepted findings.
- **§6.5 workspace-open.** Opens the work workspace for an issue: adopts or creates the issue's
  GitHub-linked branch (the native "create a branch for this issue"; degrades to a local branch
  with a notice where linking is unsupported), owns epic integration-branch creation, creates or
  reuses the worktree under `.worktrees/`, runs the repo's worktree-setup hooks, and tells the
  operator where to start the next session. A foreign open/draft PR gates before any side
  effect. Never plans or resolves anything itself.
- **§6.6 workspace-close.** Releases a work workspace (branch name or issue number): runs the
  worktree-teardown hooks, then removes the worktree — gated on dirty/unpushed state (never a
  silent discard; merged-PR-aware so the routine post-merge close isn't false-flagged), and
  refused from inside the target worktree. Removes worktrees, never remote branches.
- **§6.7 requirements-gatherer.** Interactive requirement elicitation for one filed issue:
  suggests grounding documents from the repo's doc catalogue (the operator confirms, adds, or
  drops), elicits an enumerated requirement set in discussion with the operator (iterating on a
  human-readable draft until approved), and appends the approved requirements to the issue's
  `## Definition of done` as plain unticked criterion bullets — each citing its source document
  by durable anchor, or provenanced as operator-elicited when no document records it. Detail is
  never duplicated from a doc into the issue; downstream stages read the cited sections. It
  authors no documents, files no issues, and refuses epics, slices, question issues, and closed
  issues. (Appended after §6.6 rather than inserted at its conceptual position — §6.x numbers are
  cited individually elsewhere, so renumbering would dangle those references.)
- **§6.8 coordinator.** Drives one filed issue — or an epic and its stories — through the pipeline
  stages from one session. It derives where the issue stands from GitHub state and the local
  worktree list (never from a session's note), writes each stage's brief, and starts every stage
  in its own fresh session, in the folder that stage must run in, only on the operator's word in
  its own chat; where the host has no session-start tool it prints the command and the folder
  instead. When a resolver's Review card answer **Review with Claude** sends a `review requested:`
  update, it offers the same one-click start for that review session (#172) in the resolver's
  worktree: the standalone review tool, not a stage, so its stage set is unchanged; it runs beside
  the waiting resolver, and the coordinator never answers the resolver's card. It opens, syncs and closes work workspaces through the workspace tools' own logic as
  routine steps, per the §6 preamble; checks each stage's claims (merge, PR state, issue state)
  against GitHub; and shows a card when a stage's relayed handoff disagrees with GitHub.
  Everything that waits on the operator — a decision, a stage start, a step only they can take —
  is a card or a one-click button, never only running output. Each stage relays its human-readable
  summaries into the coordinator's chat verbatim (§2 "Session"). For an epic, every story carries
  its own stage; the coordinator follows the epic plan's declared concurrency gate by gate,
  drives several in-flight stories at once with their merges kept serial, and re-gates a ready
  story PR whose base moved. After a unit's worktree closes it offers, as a one-click button where
  the host has an archive tool, to archive that unit's stage sessions, at a point where the host's
  cascading archive loses no history an in-flight unit needs. A run's first open points the operator at the workspace-trust guidance. An optional,
  read-only board mod draws prep's facts (§4.1) as a `ctrl-freaq-overview` pane, the text board on
  every surface, and a `ctrl-freaq-board` band above the chat input: on Desktop, with its
  `boardStyle` setting at the default `metro`, an animated Metro Wave drawing of each story's
  `history` (timed points prep reads in one batched GitHub query per run, closed stories served from
  its scratch-dir cache, #156, #157), each stage's segment starting where its recorded session started (a recorded start is kept 90
  days, after which that stretch draws as a wait),
  with keyed line labels whose hover cards light the story's overview row, and a one-line summary on the
  terminal, under `boardStyle: text`, or when that read failed. It writes nothing to GitHub itself and
  refuses question issues, slices, and issues gated by an in-scope blocked open question.
  (Appended after §6.7, for the same reason.)
- **§6.9 review-report.** Reports how the resolver's review loop has behaved across a consuming
  repo's PRs, read from the loop comments and `## Polish` ledgers the pipeline already posts: every
  reviewer finding by outcome (fixed, wrong, intentional, didn't matter, the rest excluded by name),
  the review runs each phase needed to pass, and the share of phases needing five or more. It is the
  owning tool of the script that produces that report (`scripts/prep_review_report.py`, over the
  import-only core `scripts/review_report.py`, which `setup` can compose). The record it reads is the resolver's to write
  (`skills/_shared/review-loop-record.md`). Loop comments written before the record format are
  read leniently into a separate, labelled tally, never pooled with the record's, and the security
  pass's record is counted beside the outcomes, never in them. Only text a repo owner, member or
  collaborator wrote is read; the rest is counted as skipped, since a forged record would otherwise reach
  setup's standing reviewer guidance. Read-only: it writes nothing to GitHub
  or the repo. (Appended after §6.8, for the same reason.)
- **§6.10 review.** Walks the operator, in the issue's worktree, through the PR's review threads still
  waiting on a person, one at a time, so a PR's review can finish without opening GitHub. Its prep
  (`scripts/prep_review.py`) reads the threads through the same reader the evaluator gates on and
  orders them: security findings, then review-level raises (a sensitive path or the blast-radius
  judgment, one rank), plan deviations, out-of-plan files, a thread no pipeline flag names (an operator's own answered thread, or its re-post), and the routine
  batch last. Each item gets a progress line, a short brief, a script-cut code excerpt — at most 40
  lines, never a whole file — with a link to the Files view, and free read-only discussion, then a
  consent card. A non-security item's answers are Accept, Changes required, File as follow-up and Skip
  for now; a security item's are Changes required, Dismiss, File as follow-up and Skip for now, with no
  Accept. Each answer posts at once as a decision reply in the thread, pinned to the PR head the
  operator was shown; File as follow-up files one issue through the drafter and records the thread
  accepted (dismissed, for a security item) naming it. It never resolves a thread, and ends with a plain
  summary that names the resolver as the next command when any thread now requests changes.
  `--phase <P>` (#172) reviews only the threads phase `<P>`'s resolver review pauses posted, shows that
  pause's review bodies as context that ask for no decision, and ends its summary by pointing a change
  request at **Reviewed, changes required** on the resolver's Review card, the resolver re-entry being the
  fallback once that card has closed.
  (Appended after §6.9, for the same reason.)

## §7 Persisted artifacts (the compatibility contract)

The rewrite must not change the schema or semantics of any artifact below. **Falsifiable form:**
an artifact written by a v1 skill is consumed correctly by its v2 counterpart, and vice versa.

| Artifact | Where it lives |
|---|---|
| `<!-- implementation-plan:v1 -->` plan comment — since 4.35.0 the main one of three; its `<!-- implementation-plan-part:v1:build -->` / `:references -->` parts carry the build and citation sections, and only the pipeline's own preps reassemble them (a reader of the main comment alone sees stub links); since #97 it may carry an additive `## Review level` section, and a plan without one is reviewed at `high` | issue comment |
| `<!-- implementation-plan-shipped:v1:phase:<N> -->` shipped-phase record, one comment per shipped phase, keyed (PR, phase) — legacy since 4.35.0: no longer written, read until the plan's next revise folds it back | issue comment |
| `<!-- issue-research:v1 -->` dossier comment | issue comment |
| `<!-- epic-delivery-log:v2:story:<N> -->` delivery-log entry, one comment per shipped story | epic issue comment |
| `<!-- epic-delivery-log:v1 -->` delivery log (legacy tier — read forever, never written again; no backfill) | epic issue comment |
| Native parent/sub-issue relation (epic↔story hierarchy) | GitHub issue relation |
| `<!-- question-decision:v1 -->` recorded decision | question issue comment |
| `<!-- open-question-links:v1 -->` section + closed disposition set | build-issue body |
| Definition-of-done checkbox annotations (closed set) | issue body |
| `<!-- pr-evaluator-health-cache:v1 -->` health-cache comment | PR comment |
| `## Phase tracker` section (multi-phase issues) | PR body |
| `## Decided deviations` section — one `` - `<path>` — <deviation> — operator: <answer> `` line per plan deviation the operator decided on a resolver card (resolver-written, evaluator-read, #181; `skills/_shared/review-threads.md` §12) | PR body |
| `question`-issue body schema + `audience:*` labels | question issues |
| `## Handoff` schema + closed-set state markers (the `merge: skipped (<reason>)` set grew additively by `head-moved` and `base-moved` in #93 — no value renamed or dropped) | session output |
| Config marker blocks (`issue-resolver-*` (incl. `issue-resolver-review-guidance`, user-owned, #99), `pr-evaluator-*` (incl. `pr-evaluator-sensitive-paths`, #96), `drafter-open-question-markers`, `worktree-setup`/`-teardown`, `claude-code-stack-profile`) | consuming repo `CLAUDE.md`/`COMMANDS.md` |
| `.gitattributes` generated block — `# ctrl-freaq:linguist-generated:begin` / `:end` fence lines around `<pattern> linguist-generated` lines (setup-written through `scripts/gitattributes.py`, #181) | consuming repo `.gitattributes` |
| `<!-- doc-catalogue -->` grounding-doc declaration (seeded by setup, kept current by any session that changes the docs — binding/authority changes operator-confirmed; read by every stage that grounds on docs) | consuming repo `docs/README.md` |
| Deliverable-slice sub-issue body + `<parent#>/S<K>` title convention (slicer-written) | slice issues |
| `epic/<N>-<slug>` integration-branch naming | consuming repo branches |

## §8 Grounding & workspace requirements

- **§8.1 `main` is write-protected.** No skill commits to a checkout other than its own
  session's workspace; the landing tools treat the checkout they were started in as read-only;
  every change reaches `main` through a PR.
- **§8.2 Everything lands via PR; landing is operator-gated.** All tracked-file changes a skill
  produces — code, docs, and configuration blocks — are made in a workspace on a branch, never
  in the root, and reach `main` only through a PR, gated per change or by a standing
  per-PR-type merge policy the operator configured (§5.5(f), default `ask`). The resolver opens
  its PR as part of its flow. The standalone tools that edit tracked files (`setup`,
  `question-sweep`, `doc-reviewer`) stage approved edits in the workspace and **offer** the
  landing (commit + push + PR) as one explicit final gate: on decline they perform no git
  actions, and the summary reports the workspace path and the ready-to-run landing commands.
  The `<!-- doc-catalogue -->` block is a tracked file that any session keeps current when its
  change alters the docs it lists — the resolver, these three tools, and an ordinary session alike
  — in the same workspace and the same PR. The pipeline writers run the write-side check before
  the commit, an ordinary session follows the `<!-- doc-catalogue-upkeep -->` guidance in the
  consuming repo's `CLAUDE.md`, the evaluator re-checks every PR, and any `binding` or `authority`
  change is confirmed by the operator
  ([`skills/_shared/doc-catalogue.md`](../skills/_shared/doc-catalogue.md)).
- **§8.3 Workspaces.** A building or evaluating session runs **inside** the work worktree the
  operator opened (`workspace-open`, or the coordinator, as a routine step of the go-ahead that
  starts the session) and starts
  the session in; its prep asserts that checkout
  before judgment runs and reports it as the session's workspace. Gate config and worktree hooks
  are read from that checkout's working tree, committed or not, and each read reports its source;
  pinned-ref grounding views are script-internal.
- **§8.4 Pinned grounding.** Plans, audits, and evaluations ground on an explicitly recorded
  commit SHA, and their artifacts state it.
- **§8.5 Checkout state is respected.** A mismatched, stale, or root-seated session checkout is
  surfaced as a decision gate (`WORKSPACE_MISMATCH`), never auto-corrected. The operator's own
  checkout is otherwise left alone: no path gates on its branch or dirty state, and none writes to
  it. One named carve-out: the coordinator may fast-forward the epic worktree to its own branch's
  remote tip before a story's plan, only as a named part of a go-ahead the operator answers, and
  reports it. Dirty, ahead, or diverged state is still a decision gate, and every other stale or
  mismatched checkout is still never auto-corrected.

## §9 Engineering-quality requirements

- **§9.1 Deterministic/judgment split.** Any step whose inputs and correct output are fully
  defined (fetching, parsing, state derivation, ref selection, naming, command execution) is
  performed by scripts. Model reasoning is reserved for classification, drafting, planning,
  review, and verdicts. No intermediary agent relays between a skill and its scripts; skills
  invoke scripts directly.
- **§9.2 One-shot state assembly.** A session that needs starting state obtains all of it from
  a single script invocation; no session assembles state across multiple model-mediated calls.
- **§9.3 Offline testability.** The deterministic layer runs and is tested without network
  access or a live GitHub repo.
- **§9.4 Prompt economy.** A session loads only its router plus the one playbook it is
  executing; alternative playbooks stay on disk.
- **§9.5 Behavior parity.** A rewritten skill replaces its predecessor only after passing the
  parity protocol defined in [implementation.md](implementation.md).
- **§9.6 Portability.** The deterministic layer behaves identically on macOS (BSD userland) and
  Linux (GNU userland); no skill step depends on platform-specific tooling.

## §10 Success metrics

- Prompt text a pipeline session loads (router + the one playbook) is at most half of the v1
  `SKILL.md` line count for that skill (v1 baseline recorded by implementation step S1). A stage with
  **no v1 baseline** (the `slicer`) records a **measured** bar instead, derived from the closest real
  precedent with its derivation written beside the constant — halving does not apply, since there is no
  monolith to halve. Per the S19 doc-reviewer ruling, an honest implementation that exceeds a recorded
  bar is adjudicated and re-recorded; precision is never trimmed out of a prompt to hit a number.
- Session startup performs at most one state-assembly invocation — exactly one for the pipeline
  stages (§9.2).
- The contract-token census (S1 baseline) shows zero unintended losses after each skill cutover.
- The offline suite exercises every script decision code and passes on both macOS and Linux.
- All nine skills pass the parity protocol before v1 removal.

## §11 Constraints & assumptions

- One operator per consuming repo at a time; concurrent sessions share only git/GitHub-enforced
  state, plus the board mod's and the coordinator prep's presentational state (§4.1): its sightings,
  feed and history cache, which no skill reads and which never source a stage or a gate.
- `gh` is installed and authenticated with repo scope; `git` and Python 3 are present (versions
  pinned in [architecture.md §1](architecture.md)). The skills require no other runtimes,
  interpreters, or packages. The optional board mod is a TypeScript hooks module that Claude Code
  itself runs (≥ 2.1.308, for its Desktop band), checked by `claude plugin validate` and `claude plugin test`; no skill or
  script depends on it, and the scripts stay stdlib-only Python.
- Consuming repos may lack optional capabilities (native issue dependencies, grounding docs,
  config blocks); skills degrade gracefully and say so.
- The plugin install directory is read-only at runtime; nothing is ever written there.
