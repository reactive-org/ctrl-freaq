# Story PR

Route for `vector.type == story` (base matches `epic/<N>-<slug>`). A child-story PR that merges into
its epic's integration branch, not `main`.

**Run the spine first.** Read [`evaluate-spine.md`](evaluate-spine.md) and execute it end to end. The
spine already grounds the scope check against the parent epic (`## Goal` / `## Background`) and
verifies the integration-branch caveat in the PR body. Everything below runs **only after S7-merge
actually merged**; on any no-merge exit, skip to the handoff — the three actions below must not fire
before the branch lands.

The distinct post-merge actions this route adds — all three needed because a story PR merges into a
**non-default** branch, so GitHub's auto-close-on-merge never fires and the tracker/planner state must
be updated by hand. Each is **idempotent** against a re-run or another tool.

## Action 1 — Close the story issue

`Fixes #<story>` never auto-fires on a merge into `epic/<N>-<slug>`. Close it explicitly (already-closed
is a safe no-op — `gh_persist.py close` is idempotent):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py close <owner/repo> <story> --reason completed
```

**Then close any of the story's own open sub-issues** — by construction those are its deliverable
slices ([`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md)), and the
resolver normally closes each as its last serving phase ships. This is the **backstop only**, for a run
interrupted before that: a slice still open behind a merged story leaves the story's rollup permanently
short, which is the one thing the slices exist to get right. Same idempotent close, once per open slice:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py close <owner/repo> <slice> --reason completed
```

Don't tick the slices' `## Acceptance criteria` here — the resolver owns that projection, and inventing
it at merge time would create a second writer for one fact.

## Action 2 — Project story progress onto the epic

**Skip entirely when the epic carries the native sub-issue relation** (`facts.epic.stories_source:
sub-issues`, per [`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md)): Action 1 already closed the story, and GitHub
recomputes the epic's rollup from that state. There is nothing to write, and writing anything would
create a second copy of a fact GitHub already maintains.

**Only for a legacy epic** (`stories_source: checklist` — filed before the relation existed, or a host
that doesn't serve it), whose `## Stories` checkboxes are hand-maintained and don't auto-tick on merge:
re-fetch the current epic body (another story may have merged since prep) into
`<facts.scratch>/epic-body-current.md`, find the `- [ ] #<story>` line in `## Stories`, replace it with
`- [x] #<story>`, and stage the updated body to `<facts.scratch>/epic-body-updated.md`. Show the diff,
then:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-body <owner/repo> <epic> \
  "<facts.scratch>/epic-body-updated.md"
```
If the checkbox is already `[x]`, note it and skip. A failed epic edit does **not** block the review or
the other actions — surface it for manual reapplication.

## Action 3 — Record this story in the epic delivery log

Record what the story **actually delivered** so the planner's just-in-time planning of later stories
grounds on what shipped. The evaluator is the **sole writer**; recording every story — including the
last — keeps the log complete. Format and the writer/reader contract are owned by
[`../../_shared/epic-delivery-log.md`](../../_shared/epic-delivery-log.md); render it byte-for-byte per
[`../references/epic-delivery-log.md`](../references/epic-delivery-log.md).

Derive the delivered contract **shape** from the merged diff (the new/changed type, service, or API
signature — read from the workspace) — record **what actually merged**, not the plan's pinned
contract; a divergence is deliberately visible and is the planner's feedback edge. Cross-check against
the plan's `## Epic contract` `Delivers:` line (in hand from the facts); `Delivers: (none consumed)`
means no later story relies on it — nothing to cross-check, so record the diff's shape alone, as under a
`Plan override` (no plan).

**One comment per story** (#41): stage **this story's entry alone** — marker line, then its one entry
line — to `<facts.scratch>/delivery-log-entry.md`. Never assemble the whole log; a single accumulating
comment is what reached GitHub's body cap and made merges unrecordable.

`facts.epic.delivery_log` already answers where this story stands, so never re-fetch a comment to find
out — an id read off an issue thread is a GraphQL node id, which every REST comment endpoint 404s on
(#34). Take the arm matching `story_recorded_in`:

- **`null` — no record yet.** Create the entry comment:

  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> issue <epic> \
    "<facts.scratch>/delivery-log-entry.md"
  ```

- **`entries` — this story already has its own comment** (a re-evaluation, or a corrected shape).
  Update it in place; the comment URL stays stable and GitHub's edit history is the supersession
  record:

  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-comment <owner/repo> \
    <facts.epic.delivery_log.entry.comment_id> "<facts.scratch>/delivery-log-entry.md"
  ```

- **`legacy` — this story's line lives in the legacy comment.** Update that line *in place inside the
  legacy body* (from `facts.epic.delivery_log.legacy.body` / `body_path`), re-stage the whole legacy
  body, and repost it. The tier a story is already recorded in is the tier that keeps it — opening a
  per-story comment for a story the legacy body records would leave one story with two records, and
  possibly two divergent shapes:

  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> issue <epic> \
    "<facts.scratch>/delivery-log.md" --delete-marker-id <facts.epic.delivery_log.legacy.comment_id>
  ```

**Idempotent, per story.** A story has exactly one record; re-evaluating updates it and never adds a
second.

When `delivery_log.ambiguous` is true, some story carries two records (or the epic carries two legacy
comments). It is **story-scoped**, so read `duplicated_stories` before deciding:

- **This story is one of them** — there is no single record to update: post **nothing** for it, and
  report the duplicate `comment_urls` plus the recovery (delete the stale one, re-run this evaluation).
  Writing over an ambiguous record would add a third copy.
- **This story is not** — record it normally by the arms above. A duplicate on another story is that
  story's problem to fix and must not block this merge from being recorded.

When `delivery_log.story_number` is null the PR closes zero or several issues, so the entry's story is
not derivable from the PR. Identify the story from the issue whose `## Epic contract` this evaluation
actually judged, then **look it up in `delivery_log.entries`** and take the arm its `tier` names —
`entries` → `edit-comment` on that record's `comment_id`, `legacy` → the legacy-body update, absent
from the list → create. Defaulting to a create without that lookup is how a re-evaluation posts a
second record for a story that already has one. Name the story you recorded in the summary.

## Residual follow-ups + cleanup

Then file residual non-blocking follow-ups (shared with the standard route): de-dup against the PR
body's `## Follow-ups` `Filed:` lines, fold in the `## Polish` ledger's `file` entries (one issue per
recorded `group:`, each briefed from its item and note — `polish-ledger.md` "Rules"), and file
each related group via [`../../_shared/follow-up-filing.md`](../../_shared/follow-up-filing.md)
(parent reference = this PR + the story issue + the parent epic), and weave each into the PR body's
`## Follow-ups` as a `Filed:` line (` · covers` its ledger ids) with `edit-pr-body`, as `standard.md`
"After the merge" does — the integration
PR's evaluation reads this story's deferrals from that section (`facts.epic.residue`). Then purge the scratch
dir only (`rm -rf "<facts.scratch>"`) — the worktree is **deliberately retained** (this session runs
inside it); the handoff's Cleanup line hands the operator
`/ctrl-freaq:workspace-close <facts.workspace.branch>` for the teardown + gated removal.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md). A story clean merge
is **not terminal** — route by whether sibling stories remain, read from the epic's story set
(`facts.epic.sub_issues` + its `sub_issues_summary` rollup, or the re-fetched `## Stories` list on a
legacy epic):
- **More stories pending** → forward to `/ctrl-freaq:planner #<next-story>` to plan the next story
  just-in-time against the now-current epic HEAD. Story / Epic (`open (K of M stories closed)`) / PR /
  Cleanup lines.
- **Last sibling closed** → forward to `/ctrl-freaq:resolver #<epic>` in Epic-integration mode
  (it opens the integration PR against `main`). Epic progress `open (M of M stories closed)`.

`review:` is `APPROVE` or `APPROVE (operator)`; the merge line is `squash → epic/<N>-<slug>@<sha>`.

On a **no-merge** exit, Actions 1–3 did **not** run: emit the **soft-reject → re-route** shape to
`/ctrl-freaq:resolver #<story>` (not the forward-to-next-story route — no merge landed; the
next story is deferred to a later run that actually merges this one) — or to `/ctrl-freaq:planner
revise #<story>` when it carries an S4-untick, a plan-changing polish `apply`, or a thread reply that would
reverse a locked plan decision (S4.6) — or the **re-gate** route
to `/ctrl-freaq:evaluator #<PR>` for `DIRTY`, `head-moved` and `base-moved` (no `gh pr merge`) — or
the **APPROVE-but-skipped** shape with the manual, pinned `gh pr merge` command for BLOCKED / deferred.
