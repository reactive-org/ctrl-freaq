# Standard PR

Route for `vector.type == standard` (base `main`, head not `epic/<N>-<slug>`). A single-issue PR that
lands directly on `main`.

**Run the spine first.** Read [`evaluate-spine.md`](evaluate-spine.md) and execute it end to end
(identify → health gate → five-dimension evaluation → verdict → merge strategy → merge-approval gate →
merge). Everything below runs **only after S7-merge actually merged**; on any no-merge exit, skip to
the handoff.

## After the merge — close any remaining deliverable slices

The issue itself closes via GitHub's auto-close on merge into `main`, but **its sub-issues do not**. By
construction a non-epic issue's sub-issues are its deliverable slices
([`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md)), and the resolver
normally closes each as its last serving phase ships. This is the **backstop only**, for a run
interrupted before that: a slice left open behind a merged parent leaves the rollup permanently short,
the one thing the slices exist to get right. For each still-open sub-issue of the closing issue (already
closed is a safe no-op):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py close <owner/repo> <slice> --reason completed
```

Don't tick the slices' `## Acceptance criteria` here — the resolver owns that projection, and inventing
it at merge time would create a second writer for one fact.

## After the merge — residual follow-ups

File the **residual non-blocking** work this run surfaced but hasn't filed: a finding from your own
review, a `## Follow-ups` `Note:` line that warrants an issue, or a `## Polish` ledger entry
S5.5 left as `file` (polish groups file as `incomplete-feature`, one issue per recorded `group:`, each
briefed from its item and note — `polish-ledger.md` "Rules"). **De-duplicate** against
issues already linked in the PR body's `## Follow-ups` `Filed:` lines — never re-file those. (In-scope
grounding violations never reach here — they soft-reject pre-merge and take the no-merge exit.)

Group related items and file each group via the shared protocol in
[`../../_shared/follow-up-filing.md`](../../_shared/follow-up-filing.md) ("Grouping") — one
`general-purpose` sub-agent per group, spawned together in one message, type from
`bug | incomplete-feature | deferred-test |
revise-existing`, parent reference = this PR + the issue. After filing, weave each issue into the PR
body's `## Follow-ups` section as one `Filed:` line, the section added if absent, per
[`../../_shared/follow-ups-section.md`](../../_shared/follow-ups-section.md) — a polish group's line
` · covers` every ledger id it files. That is where the resolver's weave puts its follow-ups, so every
filed deferral is in one place a later reader scans (an epic-integration evaluation reads its story
PRs' sections, never their comments). Restage from the
body as this run last wrote it (the one S4.5/S5.5 record write — `../../_shared/security-review.md` evaluator
"Write" — else the `facts.sections` body) to
`<facts.scratch>/pr-body-followups.md`:
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-pr-body <owner/repo> <PR> "<facts.scratch>/pr-body-followups.md"
```
If nothing is residual, say nothing.

## Cleanup (merge ran)

Purge the scratch dir only: `rm -rf "<facts.scratch>"`. The worktree is **deliberately retained** —
this session is running inside it and must never remove its own checkout; the handoff hands the
operator `/ctrl-freaq:workspace-close <facts.workspace.branch>`, which runs the repo's
`<!-- worktree-teardown -->` hooks and the gated removal from outside.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md) and emit the
**Standard PR clean merged — terminal** shape: Issue line, PR line (`merge: squash → main@<sha>`),
Cleanup line (`scratch dir purged; worktree retained — release with workspace-close`), a
`Next:` of `pipeline terminal — release the workspace:` whose fence carries
`/ctrl-freaq:workspace-close <facts.workspace.branch>`, and a `Why:`. `review:` is `APPROVE` (auto
policy) or `APPROVE (operator)` (gate path). The issue closes via GitHub's auto-close on merge into
`main` — the pipeline ends here.

On a **no-merge** exit, emit the matching rubric shape instead — **soft-reject → re-route** to
`/ctrl-freaq:resolver #<issue>` (COMMENT verdict or operator Needs-Revision / Reject; PR is
back in draft, `merge: skipped (verdict)`, no Cleanup line) — or to `/ctrl-freaq:planner revise
#<issue>` when the soft-reject carries an S4-untick or a plan-changing polish `apply`, since only a
re-plan clears either — or the **re-gate** route (`merge: skipped (DIRTY|head-moved|base-moved)`: a
`Workspace:` line, the fence updates the branch or worktree and re-runs `/ctrl-freaq:evaluator
#<PR>`, no `gh pr merge`) — or **APPROVE-but-skipped** (BLOCKED or operator-deferred;
`merge: skipped (BLOCKED|deferred)`, the `Next:` quotes the manual `gh pr merge` command, pinned with
`--match-head-commit`).
