# Handoff renderings — evaluator

Every clean run of the evaluator ends with a single `## Handoff` block. The schema, omission rules, and
closed-set state-marker vocabulary are owned by [`../../_shared/handoff-format.md`](../../_shared/handoff-format.md);
this file holds the evaluator's outcome→rendering rubric and the worked shapes. Match the run's outcome
against the rubric, then emit the matching shape, filling the snapshot from the prep facts plus this
run's results (the review verdict, the cache comment's SHA, the merge command's outcome).
Next-command skills are namespaced `/ctrl-freaq:<name>` (`planner`, `resolver`, `slicer`,
`evaluator`, `workspace-open`, `workspace-close`). **`Workspace:` lines** (v3's "start the next session
here" carrier) substitute `<workspace-path>` from `facts.workspace.path`; the two story-merge
forward routes instead name the **parent epic's** worktree (`.worktrees/epic/<N>-<slug>` under
the project root — the next session grounds/integrates on the epic branch; tell the operator to
open it with `/ctrl-freaq:workspace-open <epic>` if absent). The worked standard-terminal
block is the v3 frozen rendering (`docs/specs/examples/handoff-evaluator-v3.md`, superseding the
S1 capture, which stays in `docs/specs/` as the v2 historical record).

#### Rendering rubric

| Outcome | Rendering |
|---|---|
| Standard PR merged — `auto` policy, or operator **Approve** at the gate | **Terminal.** Issue line, PR line with `merge: squash → main@<sha>`, Cleanup line (`scratch dir purged; worktree retained — release with workspace-close`); the fence carries `/ctrl-freaq:workspace-close <branch>`. `review:` is `APPROVE` (auto path) or `APPROVE (operator)` (gate path). |
| Story PR merged, more sibling stories pending | **Forward → `/ctrl-freaq:planner`** to plan the next story in dependency order just-in-time (it has no plan yet — the planner grounds it against the now-current epic HEAD, then the resolver implements it). Story / Epic / PR / Cleanup lines; Epic progress e.g. `open (2 of 5 stories closed)`. `review:` is `APPROVE` or `APPROVE (operator)`. |
| Story PR merged, *last* sibling story | **Forward → `/ctrl-freaq:resolver`** on the Epic, in Epic-integration mode. Story / Epic / PR / Cleanup lines; Epic progress `open (5 of 5 stories closed)`. |
| Epic integration PR merged — operator **Approve (merge commit / squash)** | **Terminal.** Epic line, PR line with `merge: merge → main@<sha>` (or `squash → main@<sha>`), Cleanup line; the fence carries `/ctrl-freaq:workspace-close <branch>`. `review:` is `APPROVE (operator)` (epic is always gated). |
| Any PR, COMMENT verdict (soft-reject) — a real COMMENT verdict drove the `comment` action | **Re-route → `/ctrl-freaq:resolver #<issue>`** — the issue the PR closes (the story on a story PR, the epic on an integration PR), never the PR's number. Issue / PR lines; PR line carries `state: draft` (S7-post flipped it back), `review: COMMENT (soft-reject)`, `merge: skipped (verdict)`. No Cleanup line. **When the verdict included an S4-untick**, re-route to **`/ctrl-freaq:planner revise #<issue>`** instead — `plan: stale`, a `Workspace:` line (the revise grounds on this PR's head), and a `Why:` quoting each `… evaluator rejected: …` annotation: a vetoed bullet clears only through a re-plan (the planner reassigns it to a new phase — or, single-phase, to its one phase — and marks the veto `re-plan reassigned to phase <Y>`), so routing to the resolver would cost a session that only re-routes. **When the verdict soft-rejected on review threads (S4.6)**, the `Why:` lists each by `F<n>` (an operator thread by `O<n>` with its URL); when a reply would reverse a locked plan decision, re-route to **`/ctrl-freaq:planner revise #<issue>`** instead, quoting the decision and the reply. **When the verdict carried an open security finding (S4.5)**, the `Why:` names each open entry by its `SEC-<n>` id with its claim (an unparsed line under the id S4.5's write gave it) and, when S4.5's pass did not complete, says so with the sub-agent's evidence. |
| Any PR, operator **Needs Revision** / **Reject** at the gate | **Re-route → `/ctrl-freaq:resolver #<issue>`.** Same shape as the COMMENT-verdict row, but `review:` is `COMMENT (operator: needs-revision)` or `COMMENT (operator: reject)` and `merge: skipped (verdict)`. `state: draft` (the gate flipped it back). The `Why:` carries the operator's recorded rationale. For a **story PR**, this is **not** the forward-to-next-story route — no merge landed, so the story-route actions didn't run; the next story is deferred to a later run that actually merges this one. |
| `Threads` card → **End session** (S4.6: the verdict would approve, but a review thread still gates, the thread read or gated diff did not complete, or a `REVIEW_PENDING_CONFLICT` draft is in the way) | **Re-run → `/ctrl-freaq:evaluator #<PR>`** — not a soft-reject: no review posts, the PR stays ready. Issue / PR lines; PR line `open`, `review: not run`, `merge: not run`. A `Workspace:` line naming this PR's worktree, no Cleanup line. The `Why:` lists each gating thread by `F<n>` / `O<n>` with its state, and says the next run re-reads them: resolve each in GitHub once its code is fine, or reply to ask for a change. |
| Operator-confirmed polish `apply` (S5.5) — **Apply in this PR** or **Re-plan** on the `Polish` card | **Re-route → `/ctrl-freaq:resolver #<issue>`** when the items stay inside the plan — the Needs-Revision row's shape, `review: COMMENT (operator: needs-revision)` (`COMMENT (soft-reject)` when the evaluator's own verdict was already COMMENT — then its route stands, except a Re-plan answer), with a `Why:` naming the ledger ids. On a **Re-plan** answer, or when applying them changes the plan (`polish-adjudication.md` step 4): **Re-route → `/ctrl-freaq:planner revise #<issue>`** — Issue / PR lines, `plan: stale`, `state: draft`, `review: COMMENT (operator: needs-revision)`, `merge: skipped (verdict)`, a `Workspace:` line (the revise grounds on this PR's head), and a `Why:` quoting the ledger item **and** the plan decision it reverses — or, on a Re-plan answer about an intent the plan leaves open, the two intents the note names — plus every open `SEC-<n>` entry, as the COMMENT-verdict row names them. No Cleanup line. |
| Epic-integration PR, soft-reject on a **story deferral** that maps to the epic's `## Definition of done` (`epic-integration.md`) | **Re-route → `/ctrl-freaq:slicer #<epic> adopt #<issue>`** — the deferred-to issue becomes a story, since only a story lands work on the epic branch. Epic / PR lines; PR line carries `state: draft`, `review: COMMENT (soft-reject)`, `merge: skipped (verdict)`. No Cleanup line, no `Workspace:` line (the slicer grounds at the project root). The `Why:` names the epic DoD bullet, the deferred-to issue and the story PR it came from. A deferral with no filed issue (a pre-4.37.0 `file` polish entry whose issue the body never linked) drops `adopt #<issue>`, and the `Why:` names the story PR + ledger id for the slicer to cut from. Other findings in the same verdict ride in the `Why:`; the next resolver run on the epic picks them up after the story lands. |
| APPROVE but skipped as `DIRTY`, `head-moved` or `base-moved` (standard or story PR), or `head-moved` (epic-integration PR) | **Re-gate → `/ctrl-freaq:evaluator #<PR>`** — an approval covers one commit, so a new head is gated by a fresh session; never a `gh pr merge`. Issue (or Story / Epic) / PR lines; PR line `open`, `review:` as posted, `merge: skipped (<reason>)`; a skip found before any review posted (S3.2, or S7-gate step 1) carries `review: not run`, and one found at S3.2 also `health: not run`. A `Workspace:` line (`<workspace-path>` — the re-run must start in this worktree), no Cleanup line. The fence carries the commands that put the new head in the worktree, then the evaluator command: **`head-moved`** — `git -C <workspace-path> fetch origin <branch>` and `git -C <workspace-path> reset --keep origin/<branch>` (the head may have moved by a force push — a rebase, or the epic drift rectification's — which no fast-forward follows; `reset --keep` refuses, leaving the tree untouched, when an uncommitted change sits in a file it would rewrite); **`base-moved`** — `git -C <workspace-path> fetch origin <base>`, `git -C <workspace-path> merge --no-edit origin/<base>` (a merge, never a rebase, which would force-push under the open PR) and `git -C <workspace-path> push`; **`DIRTY`** — the same as `base-moved`, resolving the conflict before the push. `Why:` names the reason and the gated vs current SHA (`merge_guard`). |
| Epic-integration PR, APPROVE but skipped as `base-moved` or `DIRTY` | **Re-route → `/ctrl-freaq:resolver #<epic>`** — its epic route owns `main`-vs-epic drift rectification, conflicts included, choosing merge or rebase by the open story PRs. **Not** a soft-reject: the PR stays ready. Epic / PR lines; PR line `open`, `review:` as posted — `APPROVE (operator)` (the epic merge is always gated) once the card ran, `not run` for a `base-moved` that S7-gate step 1 caught before any review posted —, `merge: skipped (base-moved)` or `merge: skipped (DIRTY)`. A `Workspace:` line naming the epic worktree, no Cleanup line. `Why:` names the reason and, for `base-moved`, how many commits `main` gained (`merge_guard.base.behind_by`). |
| APPROVE but `mergeStateStatus == BLOCKED` → skipped | **Terminal with manual command.** Issue / PR lines (PR line: `merge: skipped (BLOCKED)`); no Cleanup. `Next:` quotes the recommended `gh pr merge` command verbatim — pinned with `--match-head-commit <gated head>` — and names the blocker; `Why:` names what the user does to clear it. |
| Operator-deferred merge ("Other": approved, merge manually later) | **Same shape as the BLOCKED case** — terminal with the recommended, pinned `gh pr merge` command; `merge: skipped (deferred)`. `Why:` notes the operator approved but opted to merge manually. For a **story PR**, same nuance as the Needs-Revision/Reject row: the story-route actions didn't run, so this is terminal-with-command, not forward-to-next-story. |

Self-authored PRs (the self-approval pre-check that downgraded `--approve` to `--comment`) still follow
the table above — the verdict is approval-equivalent; only the review action differed. On the gate path
the operator **Approve** posts as `--comment` for the same 422 reason, but the `review:` marker stays
`APPROVE (operator)`.

#### Renderings

**Standard PR clean merged — terminal.**

```
## Handoff

**Issue:** #142 — Add CSV export · closed · feature · plan: ✓
**PR:** #287 — Add CSV export (#142) · merged · base main · review: APPROVE · health: ✅ at abc1234 · merge: squash → main@def5678
**Cleanup:** scratch dir purged; worktree retained — release with workspace-close

**Next:** pipeline terminal — release the workspace:

    /ctrl-freaq:workspace-close 142-add-csv-export

**Why:** the PR satisfied every dimension cleanly and merged into main. The issue is closed by GitHub's auto-close; no follow-up skill is required for this issue.
```

The `review: APPROVE` above is the `auto`-policy shape. Under the default `ask` policy the operator
approved at the gate, so the same terminal shape carries `review: APPROVE (operator)` and the `Why:`
may note the operator's sign-off. The merge / Cleanup / terminal lines are identical either way.

**Story PR merged — more stories pending.** The Epic stays open; the next story is planned just-in-time
before it's implemented. Read the Epic's story set (`facts.epic.stories` — its sub-issues, or a legacy
epic's re-fetched `## Stories` list) to pick the next-in-sequence; the epic plan's `## Story breakdown`
order is the source of truth. The `K of M stories closed` count comes from
`facts.epic.sub_issues_summary` (`completed` of `total`) where the native relation is present.

```
## Handoff

**Story:** #151 — Add export service · closed · story · plan: ✓
**Epic:** #150 — Chat & session UX polish · open (1 of 5 stories closed)
**PR:** #287 — Add export service (#151) · merged · base epic/150-chat-ux · review: APPROVE · health: ✅ at abc1234 · merge: squash → epic/150-chat-ux@def5678
**Cleanup:** scratch dir purged; worktree retained — release with workspace-close; epic checkbox ticked; delivery log updated; story issue closed
**Workspace:** <project-root>/.worktrees/epic/150-chat-ux — the parent epic's worktree; start the planner session there (open it with /ctrl-freaq:workspace-open 150 if absent)

**Next:** plan the next story in dependency order, just-in-time, in a fresh session.

    /ctrl-freaq:planner #152

**Why:** story #151 merged into the epic branch; the Epic checkbox is ticked and the epic delivery log now records what #151 delivered. Story #152 (next in `## Story breakdown` order) has no plan yet — the planner authors it just-in-time against the now-current epic HEAD (which includes #151's merge) and checks it against the epic plan's `## Story contracts` and the delivery log, then the resolver implements it.
```

**Story PR merged — last sibling, Epic integration ready.** Every child story is now closed. The next
step is the resolver in Epic-integration mode (it opens the integration PR against `main`).

```
## Handoff

**Story:** #155 — Final polish · closed · story · plan: ✓
**Epic:** #150 — Chat & session UX polish · open (5 of 5 stories closed)
**PR:** #295 — Final polish (#155) · merged · base epic/150-chat-ux · review: APPROVE · health: ✅ at fed4321 · merge: squash → epic/150-chat-ux@9876abc
**Cleanup:** scratch dir purged; worktree retained — release with workspace-close; epic checkbox ticked; delivery log updated; story issue closed
**Workspace:** <project-root>/.worktrees/epic/150-chat-ux — the epic's worktree; start the resolver session there (open it with /ctrl-freaq:workspace-open 150 if absent)

**Next:** open the Epic integration PR in a fresh session.

    /ctrl-freaq:resolver #150

**Why:** every child story is closed and on `epic/150-chat-ux`. The resolver in Epic mode opens the integration PR against `main`; the evaluator will then escalate to the full canonical test suite (per the `pr_type: epic-integration` rule) before recommending the merge mode.
```

**Epic integration PR clean merged — terminal.**

```
## Handoff

**Epic:** #150 — Chat & session UX polish · closed · epic · plan: ✓
**PR:** #300 — Chat & session UX polish (epic #150) · merged · base main · review: APPROVE · health: ✅ at 1357bdf · merge: merge → main@2468ace
**Cleanup:** scratch dir purged; worktree retained — release with workspace-close

**Next:** pipeline terminal — release the workspace:

    /ctrl-freaq:workspace-close epic/150-chat-ux

**Why:** the integration PR landed every child story's work on `main` in one merge commit (the gate chose Merge commit, preserving the story squash commits in `main`'s history). The Epic is closed by `Fixes #150`; the pipeline ends here.
```

**Soft-reject — re-route to resolver.** A `comment` action driven by a real COMMENT verdict (not a
self-approval downgrade); the draft-flip ran, so the PR is now back in draft. The review names the
dimension gaps; the resolver continues on the existing branch (now back in draft) without
re-deadlocking on the draft-PR guard the next time it hands back.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✓
**PR:** #287 — Add CSV export (#142) · draft · base main · review: COMMENT (soft-reject) · health: ✅ at abc1234 · merge: skipped (verdict)
**Workspace:** <workspace-path> — start the next session here

**Next:** address the review's gaps in a fresh session — the resolver continues on the existing branch (now in draft).

    /ctrl-freaq:resolver #142

**Why:** the review cites <N> dimension gaps (acceptance-criterion #3 unaddressed; one plan-locked test missing — see the review comment for the full evidence). The draft-flip put the PR back to draft so the resolver's existing-PR check picks it up as in-progress work, not as drift; the resolver's review loop will address each finding, re-push, and re-flip to ready before its next forward handoff.
```

**APPROVE but merge skipped — re-gate.** The PR earned approval, but the merge is held: the merge guard
found the base gained commits the gated head lacks (`base-moved`) or the head moved after review
(`head-moved`), or `mergeStateStatus` reports the branch conflicts (`DIRTY`). An approval covers one commit, so no `gh pr merge` is printed — the fence
updates the branch (or the worktree) and starts a fresh evaluator session to gate the new head.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✓
**PR:** #287 — Add CSV export (#142) · open · base main · review: APPROVE · health: ✅ at abc1234 · merge: skipped (base-moved)
**Workspace:** <workspace-path> — start the next session here

**Next:** bring the base into the PR branch, push, then re-gate the new head in a fresh session:

    git -C <workspace-path> fetch origin main
    git -C <workspace-path> merge --no-edit origin/main
    git -C <workspace-path> push
    /ctrl-freaq:evaluator #287

**Why:** the PR is approved at abc1234, but `main` gained 2 commits that head does not contain (`merge_guard.base.behind_by`), so merging it would land code no gate ran against. Merging `main` into the branch (never a rebase, which would force-push under the open PR) makes a new head; the fresh session's prep asserts the worktree at it and re-runs the health gate and review there.
```

**APPROVE but merge skipped — terminal with manual command.** The PR earned approval but branch
protection blocks the merge (BLOCKED), or the operator opted to merge later. The head stays put, so
print the recommended `gh pr merge` verbatim in the fenced block, pinned to the gated head; the user
runs it themselves when the blocker clears, and GitHub refuses it if the head has moved since.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✓
**PR:** #287 — Add CSV export (#142) · open · base main · review: APPROVE · health: ✅ at abc1234 · merge: skipped (BLOCKED)

**Next:** clear the branch protection, then run the merge yourself:

    gh pr merge 287 --repo owner/repo --squash --match-head-commit abc1234def5678abc1234def5678abc1234def56 --subject "feat: add CSV export (#287)" --body-file /tmp/gh-evaluator-287/squash-body.md --delete-branch

**Why:** the PR is approved on its merits but `mergeStateStatus == BLOCKED` — branch protection is holding the merge (a required check or review). Clear it (`gh pr view 287 --json mergeStateStatus`), then run the command above. `--match-head-commit` pins it to the head that was reviewed: if anyone pushes first, GitHub refuses the merge and the PR needs a fresh evaluation. No follow-up skill — once the merge lands, GitHub auto-closes the issue.
```

For the operator-deferred path ("Other": approved but merge manually later), the same shape applies with
`merge: skipped (deferred)` and a `Why:` noting the operator's choice to merge manually.

**Operator soft-reject (Needs Revision / Reject) — re-route to resolver.** The gate returned **Needs
Revision** or **Reject**; the gate posted the review as `--comment` with an `operator action <ISO-date>`
header carrying the operator's rationale, then flipped the PR back to draft. The shape matches the
COMMENT-verdict soft-reject, but the `review:` marker names the operator decision and the `Why:` carries
their reason. (For a story PR, no merge landed, so this re-routes to the resolver on the *same* story —
not forward to the next one.)

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: ✓
**PR:** #287 — Add CSV export (#142) · draft · base main · review: COMMENT (operator: needs-revision) · health: ✅ at abc1234 · merge: skipped (verdict)
**Workspace:** <workspace-path> — start the next session here

**Next:** address the operator's requested changes in a fresh session — the resolver continues on the existing branch (now in draft).

    /ctrl-freaq:resolver #142

**Why:** the automated evaluation passed, but the operator requested revision at the merge gate (recorded on the PR, <ISO-date>): "export should stream rather than buffer the whole file in memory for large datasets." The gate flipped the PR back to draft so the resolver's existing-PR check picks it up as in-progress work; the resolver's review loop addresses the note, re-pushes, and re-flips to ready before the next forward handoff.
```

**Polish soft-reject that changes the plan — re-route to planner.** The operator confirmed a polish
`apply` (spine S5.5), and applying it would reverse a locked plan decision, so the resolver cannot
absorb it as a fix. The planner's revise reads the ledger from the open PR and adds a phase for the
`apply` items; the resolver then builds that phase in continue mode.

```
## Handoff

**Issue:** #142 — Add CSV export · open · feature · plan: stale
**PR:** #287 — Add CSV export (#142) · draft · base main · review: COMMENT (operator: needs-revision) · health: ✅ at abc1234 · merge: skipped (verdict)
**Workspace:** <workspace-path> — the plan revise grounds on this PR's head; start the planner session here

**Next:** revise the plan in a fresh session — a confirmed polish item reverses a locked decision.

    /ctrl-freaq:planner revise #142

**Why:** the operator confirmed ledger item P2.3 ("the export header row still uses the internal column keys") for this PR, but the plan's `## UI decisions` bullet "exported headers use the internal keys so re-import is lossless" locks the opposite; applying it is a re-plan, not a fix. The PR stays in draft; the planner adds a phase for the `apply` items and the resolver continues from there.
```
