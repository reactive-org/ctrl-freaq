# Evaluate spine — shared across all evaluator routes

The pre-merge flow every route runs: identify → health gate → evaluate the diff against the issue on
five dimensions → verdict → merge strategy → merge-approval gate → merge. PR-type differences here
are **facts** (`pr_type`, `base_ref`, escalation, `config.merge_policy`), never branches — the
post-merge *actions* that differ live in the routed playbook (`standard.md` / `story.md` /
`epic-integration.md`), which reads this spine first, then continues.

All facts named below come from the prep facts block (SKILL.md §1). All GitHub writes go through
`${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py` with a staged body path (SKILL.md §3). The run scratch
dir is `facts.scratch`.

## S1 — Identify and self-approval fact

`facts.target` names the PR; `facts.self_review` (prep compared its author with `current_user`) says whether you
authored it — if true, the final review posts as `--comment` whatever the verdict (S7-post's 422 workaround).

## S2 — Read the PR, issue, plan, and prior review

`facts.sections` carries the PR body / thread / reviews (spilled to paths when large — read from the
`*_path`). Each closing issue's `<!-- implementation-plan:v1 -->` plan is `facts.plans[<issue>]` (prep
fetched it per `closingIssuesReferences`; `body_path` always — the whole plan, its part comments put back
([`../../_shared/plan-parts.md`](../../_shared/plan-parts.md)) — `present: false` when there is none, and
`ambiguous: true` with no body when the issue carries two — judge adherence without it and say so), with
any legacy shipped-phase records in `.shipped` (`body_path` when `present`; read for the PR the plan's pointer names) — a pre-4.35.0
revise moved a shipped phase's `## Changes` / `## Data model / schema impact` / `## Test plan` entries there
([`../../_shared/plan-shipped-phases.md`](../../_shared/plan-shipped-phases.md)). Read the PR body, the
plan and its records, and any reviews before forming a verdict — the thread carries decisions the diff doesn't show. When the diff
is needed, read it in **bounded slices**: enumerate changed files first (`grep '^diff --git ' <path>`
against the spilled diff), then read only the implicated hunks — never load a 100 KB diff whole.

**Prior `/code-review` signal.** A `/code-review` run reaches the PR (`facts.sections` reviews/thread) as: the
marketplace `code-review` command's comment, "Found N issues:" with a scored list or "No issues found. Checked for
bugs and CLAUDE.md compliance."; the bundled skill's inline PR comments (only with `--comment`); or its ultra review's
single findings comment (only with `--post`). The resolver's review loop passes none — its record is the loop comment.
"No issues found" (or all below threshold) → code quality passes. Open issues no later commit addresses → rejection
signals cited in your verdict. No `/code-review` output → note it, don't refuse. Don't re-invoke it.

**No `closingIssuesReferences`** → ask (`header: "Issue link"`): **Name the issue** (Other free-text →
run scope evaluation against it) / **Intentionally standalone** (skip the scope-against-issue check).
**≥2 closing issues with conflicting acceptance criteria** → surface before forming a verdict.

## S3 — Health gate (cached per head SHA)

Confirm the branch is green before evaluating issue-fit. Produce `HEALTH_OK` (`true`/`false`/`null`)
and a short `HEALTH_BODY` fragment for the verdict.

**S3.1 Cache hit.** `facts.health_cache.hit` is true iff a `<!-- pr-evaluator-health-cache:v1 -->`
comment exists whose `SHA:` equals the PR head. On a hit, parse `HEALTH_OK` from the cached comment's
first-line state (in `facts.sections` marker path): `true` on "all green ✅" **or** "local-green over
red CI ⚠️"; `false` on "N failed ❌". Set `HEALTH_BODY` to a re-use line naming the cached
`<short-sha>` and `TIER:`. Skip to S4. (Rare exception: the user asked for the full suite this run and
the cached tier is `targeted` → treat as a miss.)

**S3.2 CI class.** `facts.ci.class` is `green` / `red` / `pending` / `none` (`fail_checks` lists the
failing check names when red):
- `green` → CI verified the branch at this SHA. `HEALTH_OK=true`; skip to S3.5 (write cache).
- `none` → no CI registered; the local gate decides. Continue to S3.3.
- `red` → record `fail_checks`; the local gate still runs and S3.5's discrepancy gate reconciles.
- `pending` → CI is configured and unfinished. **Wait for it**, then re-classify. Watch from this
  main loop (not a sub-agent — a long watch must not hold a cheap executor open); redirect the redraw
  to a scratch log so polling doesn't flood context:
  ```bash
  gh pr checks <PR> --repo <owner/repo> --watch --interval 30 > "<facts.scratch>/ci-watch.log" 2>&1
  ```
  `--watch` returns when every check is terminal. The wait is **unbounded**: if the harness interrupts
  it, re-invoke until it returns. Then re-run prep `--refresh --gated-head <gated head>` and re-read
  `facts.ci.class`. If `merge_guard.head_moved` is true the PR head advanced during the wait, to a
  commit this worktree does not hold: never cache or judge it — stop at S7-skip as `head-moved`, before
  any verdict (no review posts; the handoff carries `review: not run` and `health: not run`).

**S3.3 Local gate config.** `facts.config` carries the gate blocks read from the asserted PR-head
worktree (`facts.config.source`):
`static_checks` (+ `static_checks_present`), `test_target_raw` (+ `test_target_present`),
`escalation_labels`, and the `LEGACY_HEALTH_CHECKS_BLOCK` notice when only the legacy single-block is
present. If neither static-checks nor the legacy block is present (`static_checks == []` and the
notice absent), ask (`header: "Health check"`): **Skip for now** / **I'll specify commands** (Other
free-text). Skip → `HEALTH_OK=null` (but if CI was red with nothing to clear it, `HEALTH_OK=false`).
Specify → run the typed commands as a flat list this run; after green, **offer** to persist the block
but never write it without confirmation.

**S3.4 Run the gate** in `facts.workspace.path`. Sequence: static checks (always, in order,
first-failure short-circuit) → test selection → test execution. On the legacy-block arm, run that flat
list and skip selection. Log each command's stdout to `<facts.scratch>/health-<i>.log`; capture the
last 50 lines of any failing log as `FAIL_TAIL`. Assume nothing about output volume — if the
consuming repo's `<!-- claude-code-stack-profile -->` guidance (auto-loaded via CLAUDE.md) says a
command is verbose or slow, honor it (background / log to file) rather than flooding context.
- **Test selection** — dispatch the read-only `Explore` sub-agent per
  [`../references/test-selection-sub-agent.md`](../references/test-selection-sub-agent.md),
  substituting `facts.workspace.path`, the PR base (`facts.pr.baseRefName`), the head SHA
  (`facts.pr.headRefOid`), `facts.pr_type`, any matched escalation label (a `facts.target.labels`
  entry in `facts.config.escalation_labels`), and `facts.config.test_target_raw`. It returns exactly
  two sections, `COMMAND:` and `RATIONALE:`. Print `RATIONALE:` verbatim as the gate's status line;
  capture it as `SELECTION_REASONING` for the cache comment. `TIER` = `full` when `COMMAND:` matches
  the config full-suite command, else `targeted`.
- **Test execution** — if `COMMAND:` is `(none)`, skip (no suites selected ≠ failure); else run it in
  the workspace. **If the command begins with `xcodebuild`** (or a wrapper that runs it), delegate to
  the `apple-platform-build-tools:builder` sub-agent, bounded to "run this exact command in this exact
  cwd, absorb the log, report pass/fail + first error; do NOT edit source, re-run with modified flags,
  or investigate beyond identifying the failure." Otherwise run inline. On non-zero exit, `HEALTH_OK=false`,
  capture `FAIL_TAIL`. (A silent fix loop inside the builder turns `HEALTH_OK=true` into a lie and
  hides changes from the PR history — keep it a run-and-report delegation.)

**S3.5 Write the cache comment.** First, the **CI/local discrepancy gate** — only when CI was red and
a local gate ran:
- Local gate also red → CI and local agree; no gate. First-line state "N failed ❌"; record that CI
  was red too.
- Local gate green → CI and local **disagree**. Ask (`header: "CI vs local"`, naming the failing CI
  checks and the green local result): **Trust local gate** (`HEALTH_OK=true`; stamp first-line state
  "local-green over red CI ⚠️" so a re-run re-derives the warning; record the override in the review
  body) / **Treat as red** (`HEALTH_OK=false`, "N failed ❌"; the verdict hard-blocks citing the CI
  checks).

Compose the comment body **byte-for-byte** per
[`../references/health-cache-comment.md`](../references/health-cache-comment.md), stage it to
`<facts.scratch>/health-cache.md`, and post it (deleting a stale prior comment via its id from
`facts.sections`):
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> pr <PR> \
  "<facts.scratch>/health-cache.md" [--delete-marker-id <stale-cache-comment-id>]
```
Don't post a cache comment on the S3.3-skip path. Capture the returned URL for `HEALTH_BODY`.

## S4 — Evaluate the diff against the issue (five dimensions)

For each closing issue, judge these before drafting the verdict. Bound every doc/diff read to targeted slices (never the whole diff). Read grounding docs from the **workspace** (the PR head checkout prep ensured), so a PR that legitimately edits a doc is judged against its own post-edit copy.

1. **Scope.** Does the diff change what the issue asked, and only that? Drive-by edits are a flag;
   small incidental fixes are fine when the PR body calls them out.
2. **DoD / acceptance criteria.** `facts.dod[<issue>]` carries each bullet parsed with its annotation
   (the closed set in [`../../_shared/dod-annotations.md`](../../_shared/dod-annotations.md)). Two
   paths:
   - **Projection annotations present** (`closed by phase <N>, commit <short-sha>` / `closed by commit
     <short-sha>` / `closed by phase <N>, operator action <ISO-date>`) → **verify each ticked bullet against its attributed
     phase's diff**, using the PR body's `## Phase tracker` as the phase→commit map, extracted in the
     workspace. Extract per-phase ranges with `git log --reverse` + `git diff <start>..<end>` (or
     `git show <end>` for a single commit) inside `facts.workspace.path`. A **clear semantic mismatch**
     between the attributed diff and the bullet text → un-tick (S4-untick below). Soft/partial/
     surprising-but-defensible → flag in the body, leave the tick. An operator-phase bullet (no commit
     range) → record `verification: operator-phase claim — accepted on faith; verify out-of-band`,
     do not un-tick. A broken/force-pushed-away SHA → `verification: unverifiable`, leave the tick
     (don't punish branch hygiene).
   - **No projection annotations** (pre-projection issue, `## Acceptance criteria` instead of a
     checkbox DoD, or projection didn't land) → historical fallback: walk every item and judge it
     against the diff and tests. This is the backward-compatible path; don't demand annotations.

   In both paths the **bullet text is the requirement**; annotations are attribution metadata. Never
   synthesize annotations from `## Phase tracker` + `closes-dod` — that would create a parallel
   projection authority and break the resolver/evaluator boundary.
3. **Native `blocked by` holds the merge.** `facts.blocked_by[<issue>]` lists native blockers (empty
   when none or `deps_available` false; prep also raised an `attention` line for open blockers). Any
   **open** blocker → the PR **must not merge**: soft-reject (`--comment`) citing the open blocker
   `#N` and that the in-scope work it gates can't be verified until the question is answered. An issue's
   `<!-- open-question-links:v1 -->` `## Open questions` section is **not** a DoD checklist — read it
   only via this native relationship; a `scoped-out` question is not a gap.
4. **Doc grounding.** Verify the diff is consistent with the project's documented constraints,
   escalating depth by the cheapest evidence available; never hard-block on a missing section alone.
   Skip entirely for one-line bug fixes, pure doc/typo changes, and a repo that declares no grounding
   docs (`facts.grounding_docs` empty, or `DOC_CATALOGUE_ABSENT`). If the issue carries a specific
   `## Doc grounding` section, verify the cited doc sections say what it claims and the diff honors
   them. If absent or vague, do the grounding yourself against the repo's declared docs
   (`facts.grounding_docs`, read under `facts.workspace.path`; a `kind: dir` entry's `members`; skip a
   `present: false` entry) — bounded to the implicated slices. Soft-reject only on a **clear**
   violation of a `binding` doc (quote the section + diverging diff); a tension with an `informative`
   doc, or anything soft/ambiguous → flag, don't block. Also re-adjudicate any `## Follow-ups` deferral
   in the PR body: a filed follow-up does **not** excuse an in-scope documented-constraint violation
   (the severity axis is whether an in-scope constraint is violated, not whether a ticket exists) →
   soft-reject, quoting the doc section and the deferred-to issue; genuinely out-of-scope/future work →
   note, don't block. **Catalogue changes** (`facts.catalogue_check`, the write-side check re-run at the PR head; it applies even when the rest of this step is skipped):
   broken markers or a `malformed_lines` entry readers would skip → soft-reject, quoting the line; each `confirm` entry (a `binding` added or removed, an `authority`
   changed) → one consent card before approval, per `../../_shared/doc-catalogue.md` "Keeping it current" — a declined entry soft-rejects to revert it.
5. **Plan adherence.** With a `<!-- implementation-plan:v1 -->` plan present, check the diff against
   its **locked decisions** (`## Architecture decisions`, `## Changes`, `## Data model / schema
   impact`, `## Test plan`) in targeted slices — for a shipped phase the plan points at with `- Phases …
   shipped on #<PR>: entries in the shipped-phase records.`, those entries are in `.shipped`, and they
   bind exactly as the plan's own do. The plan locks decisions, not lines — harmless
   in-spirit detail is fine. An **undisclosed reversal** of a locked decision (not flagged in the
   plan's `## Deviations`, not a `## Plan override` in the PR body) → soft-reject, quoting the decision
   and diverging diff. A finer deviation you would otherwise pass becomes an S4.6 plan thread (`--plan-threads`). No plan → note "adherence not evaluated," don't hard-block.

**Story / epic context** (facts, not branches — the routed playbook handles the post-merge side): a
story PR's base must be `epic/<N>-<slug>` and its body must carry the integration-branch caveat; an
epic-integration PR's base must be `main`, head `epic/<N>-<slug>`, body carrying `Fixes #<epic>`. Flag
a missing caveat/`Fixes`. `facts.pr_type` and `facts.pr.baseRefName`/`headRefName` supply these.

**S4-untick.** On a clear semantic mismatch, replace the bullet line in the issue body with the
sticky-veto form from [`../../_shared/dod-annotations.md`](../../_shared/dod-annotations.md):
`- [ ] <text> (resolver claimed phase <N>, commit <short-sha>; evaluator rejected: <one-line reason>)`
(`<short-sha>` is 7-char, per that contract).
Sanitize any `(`/`)` in the reason to `[`/`]`. Stage the corrected body to
`<facts.scratch>/issue-body-corrected.md` and apply it **before** the PR review posts (a reader
following the review's `## DoD verification` to the issue must see the un-tick already in place):
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-body <owner/repo> <issue> \
  "<facts.scratch>/issue-body-corrected.md"
```
The threshold is **high** — un-ticks propagate to the resolver as sticky vetoes; an over-eager un-tick makes the evaluator a per-phase nitpicker. On an edit-body failure, still post the review; the
resolver's reconciliation respects the un-tick either way. This is a soft-reject regardless of other
dimensions.

## S4.5 — Security review

Run the pass per the evaluator section of [`../../_shared/security-review.md`](../../_shared/security-review.md)
(the `covers_head` skip; the `Security` card's two cases are asked at S4.6), dispatching [the prompt](../references/security-review-prompt.md).

## S4.6 — Review threads

On every evaluation that reaches here, whatever the health result, run [`../references/review-threads.md`](../references/review-threads.md): the blast-radius judgment and `blast_radius.py assess` at the gated head, `review_threads.py narrow` and the out-of-plan triage ([`../references/out-of-plan-triage-prompt.md`](../references/out-of-plan-triage-prompt.md)) over the files it leaves, `review_threads.py flags`, the thread text you write, `gh_persist.py review-post` when `flags` reports `new`, the `## PR summary` + `## Review items` blocks ([`../references/pr-summary.md`](../references/pr-summary.md)), then the gate — a soft-reject by `F<n>` / `O<n>`, or the `Threads` card (**Re-check** / **End session**) — and, once every gating thread is `accepted`, the thread dismissals. No prep refresh between `review-post` and the gate except **Re-check**. Never resolve or unresolve a thread.

## S5 — Verdict

**APPROVE** when `HEALTH_OK == true`, every dimension passes, no unresolved `/code-review` issues remain, no
`## Security review` entry is `open` (an unparsed line or a pass that did not complete counts as one; an entry whose dismiss proposal waits on its thread blocks through that thread, never a soft-reject), no review thread gates (S4.6 held on nothing: the thread read and the gated diff completed, every `new` thread's `review-post` returned `ok` (no `REVIEW_PENDING_CONFLICT`, no other failure), and no thread `unreviewed`, `changes-requested`, `operator-thread`, or `superseded` that gates by `references/review-threads.md` §5's gating rule — not `replaced`, not named by this run's `new[].supersedes`, not in `flags`' `retired`), and
`reviewDecision` isn't `REVIEW_REQUIRED` owed to another named reviewer.

**COMMENT (soft-reject)** when any of: `HEALTH_OK == false` (**unconditional hard block** — a red branch never
approves; lead the body with `HEALTH_BODY`, name the failing command, link the cache comment); any dimension
fails (list each gap with the evidence that closes it); an S4-untick fired; a `## Follow-ups` entry judged an
in-scope violation; an open native blocker; an operator-confirmed polish `apply` (S5.5); an open security
finding (S4.5), as APPROVE counts it, unless its dismiss proposal waits on its thread; a `changes-requested` thread or an `operator-thread` without `pending_reply` that gates by that same §5 gating rule (S4.6, listed by `F<n>` / `O<n>`). Use `--comment`, never `--request-changes` (the project's soft-reject
convention; `--request-changes` only on explicit user ask). When `HEALTH_OK == null` (skipped), proceed with the
dimension-only verdict and carry `HEALTH_BODY` verbatim at the top of the review body.

Compose the review body: `HEALTH_BODY`, the dimension-by-dimension assessment, then the `## DoD verification`
and `## Security review` fragments when [`review-comment.md`](../references/review-comment.md) includes them.

**Other gates** in this dimension (ask only when genuine): unresolved `/code-review` issues (`header: "Open
review"`: **Hard rejection** / **Note + proceed**); a `REVIEW_REQUIRED` owed to a named reviewer
(`header: "Reviewer"`: **Proceed anyway** / **Wait for reviewer**); an epic-integration DoD item not
evidently met by the accumulated diff (surface before approval).

## S5.5 — Polish ledger

When `facts.polish` carries an `open`, unfixed `apply`, `file`, or `premise false` `drop` entry (the resolver's unfixed polish
— [`../../_shared/polish-ledger.md`](../../_shared/polish-ledger.md)), adjudicate it per
[`../references/polish-adjudication.md`](../references/polish-adjudication.md) **before S7's post timing** — under `auto` an
APPROVE posts and merges at once. This is the backstop: the resolver's finalisation already put every entry its final pass
left to the operator, and a decided entry is not re-asked unless it is an unfixed `apply` or a `premise false` drop whose
claim holds at head. All of that, this trigger included, needs `facts.polish.dispositions_trusted`; false, every entry and
`unparsed` line is `open` (`polish-ledger.md`). Re-check each entry's claim at head and propose `apply` / re-plan / `file` /
`drop` with a reason. The operator decides **every** entry, on any verdict and under either policy — the reference's digest
and `Polish` card, per the shared triage, asked before the S7-gate card, which never carries the ledger. An answer that
applies an entry makes the verdict COMMENT, routed to the resolver (`#<issue>`) or — on a re-plan, or when applying it changes
the plan — `planner revise`. Write the final dispositions once, with the S4.5 security record — after any answer, before the
review posts — with `gh_persist.py edit-pr-body`; `file` entries are filed after the merge by the playbook's residual step.

## S6 — Merge strategy

`facts.merge_config` is the repo's `allow_*` booleans (prep fetched them; a 403 on branch protection
was read as "no enforced linear-history rule"). If branch protection returned something **other than
403** that implies a stricter-than-expected merge policy, surface it and gate before recommending a
strategy. Pick the strategy by PR shape, clamped to what the repo allows:

| PR shape | Strategy |
|---|---|
| Epic-integration, `commit_count > 1` | **Merge commit** — preserves per-story squash commits as distinct entries in `main` |
| Epic-integration, single commit | **Squash** — a merge-commit wrapping one squash yields the outlier `Merge pull request #N` title; squash yields canonical `feat: … (#NN)` |
| Story | **Squash** — collapses to one commit on the epic branch; the integration PR later preserves it |
| Standard | **Squash** — project default |

`facts.pr_type` + `facts.pr.commit_count` select the row. Rebase is allowed but unused — don't surface
it as a peer option. If the recommended strategy isn't allowed, fall back to the next allowed one and
say why. Never use `--auto`.

**Squash subject** (when recommending squash): `<type>(<scope>)?: <summary> (#<PR>)`. `<type>` from
the PR title prefix, or issue labels when absent (bug/incomplete → `fix`; feature/story/enhancement →
`feat`); **if the two disagree, confirm with the user** before composing. `<summary>` is the PR title
stripped of an existing Conventional-Commits prefix **and** any trailing ` (#<n>)` (the double-suffix
bug) before appending `(#<PR>)`. Body = the PR body's `## Summary` (or first paragraph) + `Fixes
#<issue>` if not already present. Stage the body to `<facts.scratch>/squash-body.md`.

**Merge-readiness flags** — surface these **alongside** the verdict, never let them block approval
(code merit and merge-readiness are orthogonal; the verdict never consults `merge_guard`). From
`facts.merge_guard` / `pr.mergeStateStatus` / `reviewDecision` (also raised in `attention`):
`merge_guard.base_moved` → "the base moved — the merge skips as `base-moved` until the branch is
updated and re-gated"; `DIRTY` → "conflicts — resolve before merging"; `BLOCKED` → "merge blocked by branch protection";
`REVIEW_REQUIRED` owed to another reviewer → "approval will post, but merge may be gated."

## S7 — Merge-approval gate and merge

**Resolve the policy.** `facts.config.merge_policy` is a per-PR-type `ask | auto` (default `ask` when
absent or a type is omitted; prep already forces `epic-integration → ask`), and `facts.config.sensitive_paths.forced_ask`
(a declared sensitive path or the declaration itself changed, or that read failed) makes it `ask`
whatever the type's entry says. A COMMENT verdict never reaches a merge — it short-circuits to the soft-reject post below.

**Post timing.** Show the user the verdict + merge plan first.
- Verdict COMMENT → post the review now (S7-post) and take the soft-reject path.
- Verdict APPROVE **and** policy `auto` for this type → post the approval now (S7-post), then merge.
- Verdict APPROVE **and** policy `ask` → **defer** the post to the gate below (the review must reflect
  the operator's call, not a pre-emptive automated one). Stage the review body to `<facts.scratch>/review.md` now.

**S7-post** (a review post): stage the body to `<facts.scratch>/review.md`, then
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> pr-review <PR> \
  "<facts.scratch>/review.md" --review-action <approve|comment>
```
`approve` for an APPROVE (but `comment` when `facts.self_review` is true — the 422 workaround, body
identical); `comment` for a soft-reject. On a **real** COMMENT verdict (not a self-approval downgrade),
flip the PR back to draft so the resolver re-enters cleanly:
```bash
gh pr ready <PR> --repo <owner/repo> --undo
```
(best-effort — if it fails because the PR was already draft, log and continue; the handoff carries
canonical state). The self-approval downgrade is the exception: it stays ready for manual merge.

**S7-gate** (the deferred APPROVE-under-`ask` / epic-integration path — the operator decision gate):
1. **Refresh** first (`prep_evaluator.py --refresh --gated-head <gated head> …`) so you act on current truth — the operator may
   have approved/commented/merged/closed on GitHub mid-run. If `target.state` is `merged`/`closed`,
   stop and treat as "merge did not run" (externally resolved); let the handoff carry it. A human
   `CHANGES_REQUESTED` at head → default the gate to **Needs Revision**; a human `APPROVED` at head →
   default to **Approve**. `merge_guard.skip_reason` set → S7-skip now, with no card and no review post
   (no merge can follow, so the card would decide nothing; for `head-moved` a posted APPROVE would also
   attach to a head nobody gated).
2. **Ask** (`header: "Approve PR"`): the `question` names the PR and its `url`, a one-line verdict + strategy
   recap, and — when the opening prep's `forced_ask` (a refresh carries no `config`) — each of its `matches`
   pairs (or its `error`). Fixed option set: **Approve** (for epic-integration, split into **Approve (merge
   commit)** / **Approve (squash)** to capture the mode); **Needs Revision**; **Reject**. The tool's "Other"
   covers "approve but merge manually later." The operator's decision is authoritative and may override the
   verdict. The polish ledger is already decided (S5.5); the card does not re-ask it.
3. **Post the review as the operator's decision** — the deferred S7-post, with an operator-attribution
   header prepended per [`../references/review-comment.md`](../references/review-comment.md) (mirrors
   the `operator action <ISO-date>` form). Post with `--review-action approve` on Approve (or `comment`
   when self-authored), `comment` on Needs Revision / Reject.
4. **Route:** Approve, or the "Other" deferred-merge → S7-merge, whose pre-check refresh owns the guard and
   DIRTY/BLOCKED — a fresh `merge_guard.skip_reason` outranks `deferred`, so a head or base that moved
   while the card was open is named, never `deferred`. Needs Revision / Reject → flip to draft
   (`gh pr ready <PR> --undo`) and take the soft-reject path (no merge; review already posted as `comment` in step 3).

**S7-merge** (APPROVE). The **gated head** is the opening prep's `pr.headRefOid` — what the worktree was asserted
at and the health gate keyed on. Immediately before merging — on the `ask` path too, since the operator may have sat
on the card — run `--refresh --gated-head <gated head>`. Stop if `state` isn't `open` or a refreshed thread-cited
`dismissed` entry's `thread.confirmed` is not `true` (name it); go to S7-skip if `merge_guard.skip_reason` is set,
`mergeStateStatus` is DIRTY/BLOCKED, or the operator deferred the merge; a refresh that failed is a stop, never a
merge. Print the command so it's visible in the transcript, then run it. This is the merge executor — there is no
`gh_persist.py` merge op, so the merge runs as a direct `gh pr merge`, pinned so GitHub refuses any head but the gated one:
```bash
# squash (standard / story / single-commit epic):
gh pr merge <PR> --repo <owner/repo> --squash --match-head-commit <gated head> \
  --subject "<composed subject>" --body-file "<facts.scratch>/squash-body.md" [--delete-branch]
# merge commit (multi-commit epic-integration):
gh pr merge <PR> --repo <owner/repo> --merge --match-head-commit <gated head> [--delete-branch]
```
Append `--delete-branch` only when `facts.merge_config.delete_branch_on_merge` is false. Never
`--auto` (repo `allow_auto_merge` is false). On non-zero exit, refresh with `--gated-head`:
`merge_guard.head_moved` → S7-skip as `head-moved`; otherwise surface the `gh` output and stop — do
**not** proceed to the routed playbook's post-merge actions or cleanup. (The worktree is retained on
every exit under v3 — success included; what a failed merge preserves is the *retry*, not the tree.)

**S7-skip** (no merge — after an APPROVE verdict, or at S3.2 before any verdict; a skip from S3.2 or S7-gate
step 1 comes before any review post: `review: not run`). The reason is the first that holds, in this order — a guard reason
always outranks the rest: `merge_guard.skip_reason` (`head-moved`, then `base-moved`), `DIRTY`
(conflicts), `BLOCKED` (branch protection), operator-`deferred`. `BLOCKED` / `deferred`: print the
recommended `gh pr merge` — pinned with `--match-head-commit <gated head>` — for the user to run after
clearing the blocker. `DIRTY` / `head-moved` / `base-moved`: print **no** `gh pr merge` — an approval
covers one commit, and the branch or worktree must carry a new head that a fresh session re-gates (the
route the routed playbook names for that reason). Name the reason, then stop. Do **not** run the post-merge actions or
cleanup — the worktree stays for the retry.

## Return to the routed playbook

After the merge runs (S7-merge), continue in the routed playbook (`standard.md` / `story.md` /
`epic-integration.md`) for its post-merge actions. On any **no-merge** exit (COMMENT soft-reject,
S7-skip, operator Needs-Revision / Reject, externally-resolved), skip straight to the routed
playbook's handoff — the post-merge actions and cleanup do not run.
