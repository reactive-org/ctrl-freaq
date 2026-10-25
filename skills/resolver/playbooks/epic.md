# Epic-as-target

Route for `vector.type == epic` (the issue is the epic itself, not a story). An epic is a **container**:
no code lands here — child stories are where code lands, and opening a monolithic feature PR for an epic
conflates resolution with implementation. This flow's distinct **actions** are the epic-branch
lifecycle: discover-or-bootstrap the `epic/<N>-<slug>` integration branch, keep it from drifting off
`main`, hold a trusted green baseline, and carry the integration PR that lands the accumulated diff on
`main`. That PR opens **early, as a draft** — as soon as the branch carries commits `main` does not —
so a human can review overall epic progress mid-flight; it is refreshed on every epic run and flips to
ready only when the story set completes. It does **not** read the code-shipping spine.

All facts come from the prep facts block (SKILL.md §1); `facts.epic` carries the branch discovery
(`match_count` / `branch` / `bootstrap_slug`) plus the integration-PR facts (`commits_ahead` /
`commits_ahead_base` / `integration_pr`); `facts.workspace` is the ambient epic-branch worktree
prep asserted — the one the operator opened with `/github-pipeline:workspace-open <epic>` (its
`base_ref` is `main`); the audit read workspace (`facts.read_workspaces.audit`) is at the
epic branch. All GitHub writes go through `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py` with a staged
body path (SKILL.md §3); every git command runs against the workspace by absolute path
(SKILL.md §3 — `main` changes only via PR). Baseline comment renderings are in
[`../references/epic-baseline.md`](../references/epic-baseline.md); the full runbooks (canonical suite,
drift rectification, bootstrap, legacy recovery) are in
[`../references/epic-flow.md`](../references/epic-flow.md) — **read that file before any bootstrap or
rectification step**.

## S0 — Plan summary

Render the `## Plan summary` block per
[`../../_shared/plan-summary.md`](../../_shared/plan-summary.md) before touching the branch, at `focus`:
the integration state — the story set with what each delivers, and the epic plan's
`## Integration strategy`. An epic plan carries no `## Phases`, so that section is omitted. Source it
from the staged plan-marker path in `facts.sections`; skip the whole step when `facts.plan.present` is
false.

This route runs no state-distiller, so the digest is the plan **as posted** — unlike the spine's, which
is sourced from the distiller's thread-reconciled view. That is correct here and not a gap to close.

This is the one step that reads the epic plan on this route. It is also the route whose whole subject is
that plan's `## Story contracts` and `## Integration strategy` — the integration branch, the baseline,
and the PR body all exist to carry them — so the operator sees what convergence this run is driving
toward before it starts moving refs.

## S1 — Resolve the integration branch

`facts.epic.match_count` classifies the branch state:
- **One match** (`facts.epic.branch`) → use it verbatim; never recompute the slug from the title (an
  independent run's stricter slug rule would orphan the original branch's commits — the #102 incident).
- **Zero matches** (`facts.epic.bootstrap_slug` present, `attention` names "bootstrap required") →
  **bootstrap** per `epic-flow.md`'s "Bootstrap a new epic branch": workspace-open already created
  `epic/<N>-<slug>` off `origin/main` and this session sits in its worktree (prep asserted the
  ambient `epic/<N>-…` branch); run the canonical suite (S3), push the branch, and post the
  first `Baseline established` comment. Story runs deliberately redirect here rather than bootstrap
  silently, so a missing step stays visible.
- **Multiple matches** never reaches here — prep raised an `AMBIGUOUS` decision the router already
  resolved.

## S2 — Assess epic-vs-main drift

From the asserted workspace, assess whether `epic/<N>-<slug>` has drifted behind `main` and, if so, whether
to rebase or merge, per `epic-flow.md`'s "Drift rectification" (the 4-rule strategy table: commits-behind
× open-story-PR count × file-overlap). **Rebase force-pushes the epic branch; merge does not** — choose
rebase only when no open story PRs exist against the branch, else merge, so in-flight sibling story PRs
aren't force-reset. On conflicts, run the shared conflict-handling procedure (capture the set, gather
cross-side context, dispatch the `general-purpose` conflict-resolution proposer sub-agent — it proposes,
never writes — then gate `header: "Rectify epic"`: **Apply all** / **Apply some** / **Abort — manual**,
and the skill applies the approved edits). After rectification the epic HEAD changed → the prior baseline
is untrusted; re-run S3.

## S3 — Canonical baseline + trust state

The story gates run targeted tests; the epic baseline / bootstrap / post-rectification flow is the one
place that legitimately runs the project's **full** canonical suite (every unit + integration test) in
the work workspace. Run it per `epic-flow.md`'s "Running the full canonical suite":
- Read `facts.config.canonical_suite_raw` for the labelled commands. First attempt → `full-suite` (one
  cold build + every suite). Any re-run → `build-once` **once**, then `retry-without-rebuild` — never
  re-issue `full-suite` (re-paying the cold build that dominates wall time on a compiled stack turned
  one re-baseline into a multi-hour hang).
- Run it as a **harness-tracked background bash owned by this main loop**, never delegated to a
  sub-agent (a sub-agent can end its turn mid-build, orphan the process, and lose the tally). Use
  **absolute paths**; never chain the real command behind a relative `cd … &&` (which `&&`-short-circuits
  to a false `exit 0` running nothing). Tee to a log; read the log on completion, never re-run to see
  scrolled-off output.

**Trust-state.** A story under this epic inherits the epic baseline; the epic-as-target run re-runs it
only when `main` merged into the epic since the last `Baseline established`, or after rectification. On
**green**, post a fresh `Baseline established` comment (render per
[`../references/epic-baseline.md`](../references/epic-baseline.md)) recording the epic-branch SHA + the
`main` SHA. On **red**, stop and surface every failing test; acceptable next moves are a detour-first
fix or an explicit operator override recorded as a `Baseline override` comment (same reference) before
proceeding.

## S4 — Integration PR (open early as a draft; ready when the epic is done)

Four steps, in order. Each one's precondition is a **fact value**, and a step whose precondition is
unmet is skipped — they are stages of one pipeline, not alternatives to each other. The PR is the
human-readable view of overall epic progress from the first landed story onward, so it exists for most
of the epic's life as a draft and becomes a merge request only at S4.4.

### S4.1 — Open the draft integration PR

Runs when `facts.epic.integration_pr` is null, `COMMITS_AHEAD_UNAVAILABLE` and
`OPEN_PR_LOOKUP_UNAVAILABLE` are both absent from `facts.notices`, and
`facts.epic.commits_ahead >= 1`.

A null `integration_pr` **carried by `OPEN_PR_LOOKUP_UNAVAILABLE` means unknown, not absent** — opening
on an unknown would file a second integration PR against the same branch, so decline and say so in the
handoff. Zero `commits_ahead` means there is nothing to review yet and `gh` would reject the create
outright ("No commits between …"); a null one means the count couldn't be read.

Stage the body to `<facts.scratch>/epic-integration-pr.md` — the epic's `## Goal` + its DoD checklist +
the story-set progress + `Fixes #<epic-number>` — and open through the single write path (the same
`create-pr` call the spine's S5 uses for a story/standard PR; base/head/draft are facts, not a branch):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create-pr <owner/repo> "<facts.scratch>/epic-integration-pr.md" \
  --title "Epic #<N>: <title>" --base "<facts.epic.commits_ahead_base>" \
  --head "<facts.epic.branch>" --draft
```

`--draft` is unconditional here: the PR opens for review-by-a-human, and draft is what keeps it out of
the evaluator's reach (the evaluator refuses a DRAFT PR) until S4.4 says the epic is actually done.

### S4.2 — Refresh the PR body

Runs whenever the integration PR exists — S4.1 just opened it, or `facts.epic.integration_pr` was
already non-null. Re-render **only the sections this route owns** against current state — the `## Goal`,
the DoD checklist's current ticks, story-set progress from `facts.target.sub_issues_summary`, the SHA
pair from the latest `Baseline established` comment — and keep every other section of the current body
verbatim: `facts.epic.integration_pr.body_path` (S4.1's staged body when it just opened the PR). The
`## Polish` ledger, `## Security review` record, `## Out-of-plan changes` and `## Follow-ups` that S4.3 and the
evaluator write live nowhere else, and a whole-body re-render wiped them on every re-route back here. When `body_path` is null
(`INTEGRATION_PR_BODY_UNAVAILABLE`), skip the write and say so in the handoff — refreshing without the
current body is that wipe. Stage to `<facts.scratch>/epic-integration-pr.md` and apply:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-pr-body <owner/repo> <PR> \
  "<facts.scratch>/epic-integration-pr.md"
```

The refresh runs even when `facts.epic.integration_pr.is_yours` is false — the epic branch is shared
infrastructure, so its PR body tracks the branch, not its author. Name that author in the handoff when
it isn't you.

### S4.3 — Review loop

Runs only when the story set is complete (`facts.target.sub_issues_summary` — `completed == total` — or
the legacy `## Stories` checklist, per
[`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md)) **and** the epic's
`## Definition of done` is verifiable against the accumulated diff.

The integration PR lands the whole epic on `main` at once — higher risk, so apply the same review-loop
discipline as any story PR, per [`resolve-spine.md`](resolve-spine.md) §S5.1 and
[`../references/review-fix-round.md`](../references/review-fix-round.md). It runs **here, at
the ready flip — never on a draft refresh**: a review per landed story would re-review the same
accumulated diff on every epic run, and the stories already carried their own. The loop exits through
spine S5.2 as on any PR — finalisation (this is the final pass), the one push, and the `## Polish` ledger
written to **this PR's body** with `edit-pr-body` (ids under phase `1`, `polish-ledger.md` "Format") —
and its follow-ups file per spine S7, woven into this PR's `## Follow-ups`. Each write restages from the
body this run last wrote, never prep's copy.

### S4.4 — Draft → ready flip

Runs immediately after S4.3 passes, immediately before the handoff:

```bash
gh pr ready <PR> --repo <owner/repo>
```

Without the flip the evaluator's draft-PR guard deadlocks the handoff — the same contract the spine's
last-planned-phase flip carries. While any story is still open the PR **stays draft**, and the handoff
names what's left.

## S5 — Epic body-tick close-out (after the integration PR merges)

This runs only when the integration PR has merged (the evaluator merges it; on a resolver-driven close
after merge, do the housekeeping GitHub doesn't auto-fire). Re-fetch the epic body and flip every
`- [ ]` → `- [x]` in `## Definition of done` — plus, on a legacy epic that still carries a
`## Stories` checklist, in that section too (a native story set needs no tick: GitHub recomputes the
rollup from issue state). Preserve the `## Goal` / `## Background` / `## Definition of done` section
names exactly — they're load-bearing for PRD traceability. Stage to
`<facts.scratch>/epic-body-closed.md`, and apply:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-body <owner/repo> <epic> \
  "<facts.scratch>/epic-body-closed.md"
```

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md) and emit the matching
shape:

- **Forward — Epic integration PR** (S4.4 flipped it ready): `Epic:` + `Stories:` (`M of M
  closed`) + `PR:` (`open · base main · review: not run · …`), `Next: /github-pipeline:evaluator #<PR>`;
  the `Why:` calls out the higher merge risk and the evaluator's full-canonical-suite escalation on
  `pr_type: epic-integration`.
- **In progress — Epic integration draft PR open** (S4.1 opened or S4.2 refreshed it; stories remain):
  `Epic:` + `Stories:` progress + `PR:` (`draft · …`), `Next:` pointing at the planner for the next story
  (`/github-pipeline:planner #<next-story>`) or the resolver for a story run, per the epic cadence; the
  `Why:` says the PR is a progress view, not a merge request, and names what has to close before the
  ready flip. Name the other author here when `integration_pr.is_yours` is false.
- **Bootstrap / rectification only** (branch created or drift rectified, no integration PR — the branch
  carries no commits `main` doesn't, or the lookups were unavailable): the `Epic:` + `Stories:` progress
  lines and the same next-story `Next:`; the `Why:` names the branch action taken, why no PR exists yet,
  and what unblocks next.
