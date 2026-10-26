# Handoff format — shared reference

The six pipeline skills (`drafter`, `researcher`, `slicer`, `planner`, `resolver`, `evaluator`) close every clean run with a single **`## Handoff`** block (a resolver look prints it before its card; the last one printed is the run's). Each skill runs in its own Claude Code session, so the handoff is the only bridge between sessions — it must read cold and carry a copy-pasteable command for the user to start the next session.

This file is the single source of truth for the schema, the omission rules, and the state-marker vocabulary. Per-skill renderings (which clean-exit branches a given skill emits, and the exact wording for each) live in that skill's `references/handoff-renderings.md`.

## Schema

```
## Handoff

**Issue:** #N — <title> · <state> · <type> · research: <✓ | ✗ | stale> · plan: <✓ | ✗ | stale>
**Slices:** #M <N>/S1 (<state>) · … — or — <K> of <T> closed · next: #M <N>/S<K>
**Grounding:** read at <plan-ref>@<short-sha> · <docs the plan was built on, with §refs> · external: <sources> · full detail in the plan's ## Doc grounding
**PR:** #M — <title> · <state> · base <ref> · review: <verdict | not run> · health: <✅/❌ at <short-sha> | not run> · merge: <strategy → <ref>@<short-sha> | skipped (<reason>) | not run>
**Changes:** <K> commits, <short-sha>..<short-sha> — <link to this run's pushed diff>
**Cleanup:** <one-line worktree / branch / scratch summary>
**Workspace:** <absolute worktree path> — start the next session here

**Next:** <one-line description of what the fresh session will do>.

    /<next-skill> [<args>]

**Why:** <one line — for forward routes, what the next session will accomplish; for re-routes, the specific finding that triggered the regression and what the user should confirm>
```

The block is always present on a clean exit. Lines are omitted (not blanked, not stubbed) when they don't apply.

**The `## Plan summary` block is not part of this one.** Where a skill's own contract calls for the human-readable plan digest defined in [`plan-summary.md`](plan-summary.md), it is emitted as a separate block — the planner renders it immediately before `## Handoff`, the resolver at session start — and the schema above is unchanged by it: no new field, no reordering, and nothing from the summary inside the block.

## Omission rules

- **`Issue:` / `Epic:` / `Story:`** — exactly one is always present. Which one depends on the work shape:
  - Single non-Epic issue → `Issue:`.
  - Epic in any role (drafter filing the epic itself, slicer cutting its stories, planner Epic plan, evaluator on an Epic integration PR) → `Epic:`. Add a `Stories:` line listing the child stories with their state markers. When the epic has **no stories yet** — the drafter has just filed the epic, or the slicer's cut was declined — the line still renders, reading `none yet — cut into stories next`: an epic whose child line is simply absent is indistinguishable from one whose stories were dropped, and the next action is exactly what the reader needs.
  - A story under an Epic (evaluator after a story PR merges, resolver working on a story) → `Story:` for the story plus an `Epic:` line for the parent's progress (e.g. `open (3 of 5 stories closed)`).
- **`research:`** — the research-dossier marker, placed before `plan:` on the `Issue:` / `Story:` line. Present on the researcher's own clean exits (`✓` dossier posted, `✗` judged nothing-to-research) and carried forward on any later skill's handoff for an issue that has a dossier (e.g. the planner shows `research: ✓` once it has ingested one). **Omitted entirely** on issues that never went through the researcher — so the drafter's renderings, and the planner / resolver / evaluator renderings on dossier-less issues, are unchanged. When the marker carries a URL (the researcher's clean exit), append it in parentheses like `plan:` does.
- **Question-type issue (drafter and resolver)** — a `question` is answered by a human in the issue thread, not built by the pipeline, so it never enters research/plan. Its `Issue:` line is `#N — <title> · <state> · question` with the `research:` and `plan:` markers **omitted** (they don't apply), plus a question-only `**Audience:** <comma-separated audience:* labels>` line. Its clean exit is **terminal** (see Terminal endings).
- **`Slices:`** — slicer-only among writers, and only on clean exits that **filed or found** deliverable slices; carried forward by a later skill's handoff when the reader benefits from the parent's slice progress (see the variant below). Omitted when the parent has no slices, and on every slicer exit that filed nothing (declined write gate, no cut, any refusal). Free-form text, not a state marker.
- **`Grounding:`** — planner and **slicer**, and only on clean exits that **posted a plan** (planner) or **filed children** (slicer — slices at story altitude, stories at epic altitude — the catalogue docs the cut derived from, so a reader can see what the decomposition was grounded on; a cut that hides its grounding defeats the grounding gate). Opens with `read at <ref>@<short-sha>` — the ref the docs were read at (for the planner, the same `<plan-ref>@<short-sha>` the plan footer records; for the slicer, its grounding vantage `main@<sha>`, since a cut posts no plan and so has no footer), so the reader knows *which branch's* version of those docs grounded the plan (the same section can differ between `main`, an epic branch, and a PR head). Then lists the project docs (with §refs) the plan was grounded on, summarized from the plan's `## Doc grounding`, plus — when present — the external sources from `## External sources consulted` as a `· external: <sources>` segment, and a pointer to the plan's `## Doc grounding` for the full reasoning (planner only — the slicer has no plan to point at, so its line ends with the last doc). Omitted when no plan was posted (the planner's trivial-change and knowledge-gap re-route exits, both `plan: ✗`) and when the plan grounded against no docs (no `## Doc grounding` section). It is **free-form text, not a state marker** — so it has no entry in the closed-set state-marker vocabulary table.
- **`Open questions:`** — optional, drafter and planner; placed after `Grounding:` and before `PR:` / the fenced next-action block. When the issue (drafter) or plan (planner) gates on unresolved open questions (see [`open-question-links.md`](open-question-links.md)), the handoff carries a free-form `**Open questions:** #<N> | (not filed) (audience:…) [<disposition-or-treatment>], … — <trailing summary>` line listing the companion `question` issues, each in whichever vocabulary the emitting skill uses — the drafter's build-issue set (`scoped-out` / `in-scope (blocked)` / `provisional-default`) or the planner's plan-level set (`planned-around` / `recorded-blocked` / `provisional-default`). The per-entry disposition/treatment token is there when each companion question maps to one clean disposition (the planner's usual case); when several gated scopes share one companion question or the dispositions read more clearly summarized (the drafter's usual case), fold them into the trailing summary instead and skip the per-entry token — either way, always end with a trailing summary in that skill's own style (the drafter's count, e.g. `1 scoped out / 1 blocked-by`; the planner's pointer, e.g. `see the plan's ## Open questions`). Like `Grounding:`, it is **free-form text, not a state marker** — no closed-set entry. Omit when the issue gated no open questions.
- **`PR:`** — omit entirely when no PR exists. Drafter clean exits and the planner's plan-comment-only clean exits skip this line. Resolver clean exits always have a PR. Evaluator clean exits always have a PR.
- **`Changes:`** — **resolver-only**, and only on a session that pushed at least one commit: the clickable review link for *what this session pushed* — every phase it shipped, when the resolver continued past a phase in-session — so the reader doesn't have to find the PR and then work out which commits belong to which phase. Sits directly under `PR:`, which it qualifies. Payload is the commit count, the `<entry-short-sha>..<head-short-sha>` range (the entry SHA is the session's pushed tip at its first prep — the resolver's `facts.workspace.upstream_sha`, equal to its local HEAD unless an interrupted run left commits unpushed, which this session ships — never a later phase's), and a GitHub link — `<pr-url>/files/<entry-sha>..<head-sha>`, the PR's Files-changed view scoped to that range. When the session shipped more than one phase, the payload also names each phase's range ahead of the link — `Phase 2: <a>..<b> · Phase 3: <b>..<c>` — and the link stays last. The discriminating test is whether the entry SHA is itself a commit **in** the PR: when it is, the range form resolves (the usual continue-mode case); when it isn't — it is the PR's base commit (the usual fresh-run case, where the whole PR *is* this session's changes), or a rebase rewrote it out of the history (an epic drift rectification) — GitHub 404s the range, so drop it and link `<pr-url>/files`, noting the whole-PR scope in the payload. Omit the line on every no-push exit (re-routes that opened no PR, terminal non-PR, terminal question-type) and on every other skill's clean exit. Like `Grounding:`, it is **free-form text, not a state marker** — no closed-set entry; the fixed lead token is the word `Changes:`.
- **`Cleanup:`** — evaluator-only, and only after the merge ran. Its value under the v3 workspace model is `scratch dir purged; worktree retained — release with workspace-close` (the story variant adds its bookkeeping segments): the evaluator no longer removes the worktree — the operator releases it with `/ctrl-freaq:workspace-close`, which the terminal fence carries. Omit on the evaluator's no-merge branches (soft-reject, DIRTY/BLOCKED-skip, a merge-guard skip (`head-moved` / `base-moved`), operator-deferred merge, operator Needs-Revision / Reject) and on every other skill's clean exit.
- **`Workspace:`** — the "where to start the next session" line, present whenever the `Next:` command must run in a session **started inside a specific checkout**: resolver → evaluator (this PR's worktree), evaluator → resolver in continue mode (the same worktree), evaluator → evaluator re-gate (this PR's worktree), evaluator → resolver epic drift rectification (the epic worktree), evaluator story-merge → planner (the parent-epic worktree, where the next story's plan grounds), planner → resolver (the issue's worktree — or `(none yet — run /ctrl-freaq:workspace-open <N> first)` when the operator hasn't opened one). Omitted on checkout-agnostic routes (drafter, researcher, question flows, a default-branch-ref planner run — any current checkout of it serves, or the issue's own branch containing its tip) and on terminal endings. Like `Grounding:`, it is **free-form text, not a state marker** — no closed-set entry; the fixed lead token is the word `Workspace:` and the payload is a path or the workspace-open breadcrumb.
- **Fenced next-action block** — on terminal endings the pipeline names no follow-up *skill*, but an evaluator merge terminal still carries one housekeeping command in the fence: `/ctrl-freaq:workspace-close <branch>` (the operator releases the retained worktree — precedent: the BLOCKED / deferred shape already carries a raw `gh pr merge` in its fence). The drafter's question-issue terminal keeps the literal `(terminal — no follow-up skill)` with no fence. The `Why:` line still appears and explains why the pipeline ends here.
- **`Why:`** — always present, on both forward and backward routes. For forward routes, name what the next session will accomplish. For re-routes, name the specific finding (audit dimension, plan-decision quote, doc citation, file:line evidence) that triggered the regression — vague Whys aren't useful weeks later when the user picks the issue back up.

## State-marker vocabulary (closed sets)

Use these exact words. Don't invent synonyms. The field names and block structure in each skill's handoff
renderings are contract exactly like the marker values: emit the applicable shape verbatim, substituting
only the values — never rename a field, drop a segment, restructure, or add a block the shape doesn't
carry. (The separate `## Plan summary` block above is not an exception — it sits outside `## Handoff`,
it is not a segment of it. Nor is the resolver's `## Session summary`, printed immediately before
`## Handoff` when a phase checkpoint raises a look or ends the session.)

| Field | Values |
|---|---|
| Issue `state` | `open`, `closed` |
| Issue `type` | `bug`, `feature`, `incomplete`, `story`, `epic`, `question` (the slot takes ONLY these values — map the repo's own label onto this set, e.g. `enhancement` → `feature`; never leak a raw label that isn't a member) |
| Issue `research` | `✓` (dossier posted), `✗` (none / judged not needed), `stale` (posted but superseded by an issue or source change) |
| Issue `plan` | `✓` (posted), `✗` (none), `stale` (posted but superseded) |
| PR `state` | `draft`, `open`, `merged`, `closed` |
| PR `review` | `APPROVE`, `COMMENT (soft-reject)`, `APPROVE (operator)`, `COMMENT (operator: needs-revision)`, `COMMENT (operator: reject)`, `not run` |
| PR `health` | `✅ at <short-sha>`, `❌ at <short-sha>`, `not run` |
| PR `merge` | `squash → <ref>@<short-sha>`, `merge → <ref>@<short-sha>`, `skipped (<reason>)` — `skipped (deferred)`, `skipped (DIRTY)`, `skipped (BLOCKED)`, `skipped (head-moved)`, `skipped (base-moved)`, `skipped (verdict)` —, `not run` |

`<short-sha>` is a 7-character hex prefix. `<ref>` is the merge target (`main` for standard PRs and Epic integration PRs, `epic/<N>-<slug>` for story PRs). When the plan marker carries a URL (planner clean exit), append the URL in parentheses: `plan: ✓ (https://github.com/owner/repo/issues/N#issuecomment-...)`.

The `(operator)` / `(operator: …)` `review` variants are evaluator-only: they record that a **human operator**, not the skill's automated evaluation, made the call at the evaluator's merge-approval gate — `APPROVE (operator)` for an operator Approve, `COMMENT (operator: needs-revision)` / `COMMENT (operator: reject)` for the two soft-reject decisions. The attribution mirrors the `operator action <ISO-date>` form in [`dod-annotations.md`](dod-annotations.md). For the `merge` marker, `skipped (deferred)` is the reason when the operator approved but chose to merge manually later; `skipped (DIRTY)` / `skipped (BLOCKED)` name an unmergeable branch; `skipped (verdict)` names a soft-reject. The two merge-guard reasons hold an approval to the one commit it covered: `skipped (head-moved)` — the PR head is no longer the commit the evaluator gated and reviewed; `skipped (base-moved)` — the base gained commits the gated head does not contain. Both, and `DIRTY`, clear only by a new head that a fresh evaluator session re-gates — on the epic-integration PR, `base-moved` and `DIRTY` reach it through the resolver's epic drift rectification — so their handoff carries no `gh pr merge`.

## Epic and Story variants

When the work shape involves an Epic, the heading line and supporting state expand:

- **Epic in any role.** Replace `Issue:` with `Epic:`. Add a `Stories:` line that lists the child story numbers and a coarse progress marker:

  ```
  **Epic:** #150 — Chat & session UX polish · open · epic · plan: ✓
  **Stories:** #151, #152, #153, #154, #155 (5 filed, dependency-ordered, contracts pinned · plans authored just-in-time)
  ```

  An epic's `plan: ✓` means the epic-level plan — its `## Story contracts` and sequencing — is posted; each child story is planned just-in-time as it becomes the next to build (not fanned out up front). After child stories start closing, switch the `Stories:` line to a progress count and (optionally) flag the next story:

  ```
  **Stories:** 3 of 5 closed · next: #154
  ```

- **Story under an open Epic** (evaluator on a story PR; resolver working on a story). Use a `Story:` line for the story itself and add an `Epic:` line that names the parent's progress:

  ```
  **Story:** #151 — Add export service · closed · story · plan: ✓
  **Epic:** #150 — Chat & session UX polish · open (1 of 5 stories closed)
  ```

- **A parent carrying deliverable slices** (slicer on any clean exit that filed slices; see [`epic-story-hierarchy.md`](epic-story-hierarchy.md)). Add a `Slices:` line below the issue/story line, listing each slice with its `<parent#>/S<K>` designator and state marker:

  ```
  **Issue:** #103 — Patient: access & set up account · open · story · plan: ✗
  **Slices:** #104 103/S1 (open) · #105 103/S2 (open) · #106 103/S3 (open)
  ```

  Once slices start closing, switch to the progress-count form the `Stories:` line uses, and flag the next one:

  ```
  **Slices:** 2 of 3 closed · next: #106 103/S3
  ```

  Omitted entirely when the parent has no slices — including the slicer's own declined-write-gate, no-cut and refusal exits, where nothing was filed. Like `Grounding:`, the payload is **free-form text, not a state marker**: there is no `slices:` entry in the closed sets below, and none is needed, because absence is expressed by omitting the line.

The Grounding / Open questions / PR / Changes / Cleanup / Next / Why lines follow the standard rules — `Open questions:`, when present, sits right after `Grounding:` in both the Epic and Story shapes. When the planner posts an Epic plan, `Grounding:` follows the `Stories:` line; for a story plan, it follows the parent `Epic:` line. A `Slices:` line sits immediately after the line it qualifies, before `Grounding:`.

## Terminal endings

Some clean exits end the pipeline for this issue — there's no next skill to invoke. An evaluator merge terminal keeps a fence carrying the one housekeeping command (releasing the retained worktree):

```
**Next:** pipeline terminal — release the workspace:

    /ctrl-freaq:workspace-close <branch>

**Why:** the PR satisfied every dimension cleanly and merged into main. The issue is closed by GitHub's auto-close; no follow-up skill is required for this issue.
```

The drafter's question-issue terminal has no workspace and no fence — its `Next:` is the literal `(terminal — no follow-up skill)` and its `Why:` explains it awaits a human answer.

Terminal endings exist on:
- the **drafter**, when it files or revises a `question`-type issue — answered by a human in the thread, not by a downstream skill;
- the **evaluator**, on a standard PR clean merge and on an Epic integration PR clean merge.

Story PR clean merges are **not** terminal — they hand off to the **planner** to plan the next story just-in-time (or to the resolver in Epic-integration mode if every child story is now closed). See "Forward re-entry of the planner" under Re-routes.

## Re-routes

A re-route is a handoff whose `Next:` points at a prior skill, or re-runs the same skill on a new head — typically:

- resolver → planner (the plan's locked decisions don't survive contact with the code, or the issue thread has moved past the plan; refresh the plan — or the operator answered **Re-plan** on a polish entry at the resolver's finalisation, `skills/resolver/references/finalisation.md`)
- resolver → drafter (the issue body fails the resolver's fitness-to-implement audit, or contradicts a doc the resolver can't reconcile)
- planner → researcher (the plan needs current external truth the model can't reliably recall — a dependency/API/version at or past the training cutoff; gather and verify the research first, then re-run the planner)
- planner → (answer the open question) (every plannable part of the issue is gated by an unresolved open question the planner must not resolve itself; a human answers the companion `question` issue, then the planner re-runs). Terminal-style: the `Next:` names no follow-up skill — the decision is a human's — but carries a re-run breadcrumb. If no companion question is filed yet, point at the drafter to file it first.
- planner → slicer, epic-shaped (the seam gate found a standard issue epic-shaped — most of its seams fall outside the issue's Definition of done, each its own shippable unit; the planner aborts, posts a lean seam-analysis comment, and the slicer promotes #N into an Epic in place and cuts its stories, splitting per that comment)
- planner → slicer, slice-first (the seam gate found the issue carries two or more milestones a stakeholder would track separately, all *demonstrable* on its one branch rather than shippable-independent, and the operator chose "Slice first" over the recommended multi-phase plan to track them as deliverable slices on the board — not promotion to an Epic; the planner aborts, posts **nothing**, and the slicer cuts them — or finds one milestone and files nothing — handing back)
- planner → slicer, stories not filed (an epic whose plan is posted but whose story set is still plain bullets — the planner files no issues, so the slicer cuts and files them, then the epic plan is refreshed)

  These three are one arm at two altitudes (#16), which is why they all name the slicer: the seam gate picks the *bar* the children clear, not the skill. They differ in what the planner posts (a seam-analysis comment on the first, nothing on the other two) and in whether the target is reshaped (only the first promotes it).
- evaluator → resolver (a soft-reject: a COMMENT verdict, the operator's **Needs Revision** / **Reject**, or an operator-confirmed polish `apply`; the PR is flipped back to draft and the resolver continues on it). Separately, and **not** a soft-reject: on the epic-integration PR an approved merge that skipped as `base-moved` or `DIRTY` hands `main`-vs-epic drift rectification to the resolver's epic route (`/ctrl-freaq:resolver #<epic>`), whose strategy protects in-flight story PRs; the PR stays ready
- evaluator → evaluator (a re-gate: an approved merge that skipped as `head-moved`, `base-moved` or `DIRTY` on a standard or story PR, or as `head-moved` on the epic-integration PR — the worktree's branch must be moved to the PR's remote head (`fetch` + `reset --keep`, since a force-pushed head is no fast-forward), or the branch updated by merging the base and pushed, and a fresh session gates that new head, because an approval covers one commit; the PR stays ready and nothing was rejected; or the `Threads` card's **End session**, a re-run once the operator has resolved or answered the review threads in GitHub, #145)
- evaluator → planner (a soft-reject only a re-plan can clear: a DoD bullet the evaluator un-ticked — the planner reassigns it to a new phase — or an operator-confirmed polish `apply` whose fix would reverse a locked plan decision or reshape a phase, `skills/_shared/polish-ledger.md`, or a review-thread reply that would reverse one, #145; the PR stays in draft)
- evaluator → slicer (an epic-integration soft-reject on a story's deferral: a follow-up a story PR filed, or a polish entry it left, maps to the epic's Definition of done. The work can land on the epic branch only through a story, and the slicer is the one writer of the epic↔story edge, so it adopts the deferred-to issue as a story — `/ctrl-freaq:slicer #<epic> adopt #<issue>` — and the story then runs the normal cadence; the integration PR stays in draft)
- slicer → setup (the consuming repo declares no grounding documents, so the decomposition would invent scope; the slicer refuses and files nothing. Terminal-style for the pipeline: the remedy is a `<!-- doc-catalogue -->` block, not another pipeline stage, but the `Next:` fence carries the setup command)

(The reverse, researcher → planner, is the *forward* route this pipeline normally takes — research is the planner's input — and follows the standard schema, not these re-route rules. Likewise slicer → planner: the slicer's own forward route, after a cut, is the planner.)

**Forward re-entry of the planner (not a re-route).** Under an epic, the planner is *also* re-entered going forward — once per story, to author that story's just-in-time plan against current epic HEAD. The trigger is the evaluator's "next story" handoff after a sibling story PR merges, or the resolver's epic/story plan gate when a story has no plan yet. This points at the planner (a "prior" skill) but is the **normal epic cadence, not a regression**: it follows the standard schema, and its `Why:` names the next story to plan. Distinguish it from the resolver → planner and evaluator → planner *revise* re-routes above, whose `Why:` quotes the locked decision that broke or the polish item that would reverse one.

The schema does not change. The `Why:` line is the load-bearing piece — it must name the specific evidence so the user (and the prior skill, when re-run) can act without re-investigating:

- Plan re-routes: quote the locked decision verbatim and cite the `file:line` where the contradiction surfaced. From the evaluator, quote each `… evaluator rejected: …` annotation for an un-tick, or the ledger item by id and the plan decision it would reverse for a polish `apply`, or the thread by `F<n>` / `O<n>` and the decision its reply would reverse; from the resolver's finalisation, each re-plan entry by id and the decision its note names (or the two intents). Any `## Security review` entry still `open` rides in the same `Why:` by its `SEC-<n>` id (`security-review.md`).
- Drafter re-routes: quote the body's claim verbatim, name the missing or contradictory symbol, and cite the closest-match `file:line`.
- Researcher re-routes (planner → researcher): name the specific ungroundable fact verbatim (the dependency/API/version and what's unknown), so the researcher targets exactly that gap rather than re-researching the whole issue.
- Open-question re-routes (planner → answer the question): name the blocking OQ id and the companion `question` issue `#N` (and its `audience:*`), so the reader knows exactly which decision unblocks the plan.
- Epic-shape re-routes (planner → slicer, promoting): name how many seams fell outside the issue's Definition of done and point at the seam-analysis comment carrying the inventory and suggested story boundaries.
- Slice re-routes (planner → slicer): name the milestones the operator chose to track as slices instead of a multi-phase plan, and why they are demonstrable- rather than shippable-independent, so the reader can see why this isn't the Epic off-ramp. No comment is posted for the slicer to read — it re-derives its cut from the grounding docs, so the `Why:` is the whole carrier.
- Grounding refusals (slicer → setup): name what is absent (no `docs/README.md`, or no catalogue block in it) and that nothing was filed, so the reader knows the issue is untouched.

The resolver does **not** invoke the prior skill via the `Skill` tool on a re-route. The handoff is the only signal; the user runs the revise command in a fresh session. This is intentional: session-per-skill is the architectural choice that lets each skill stay context-clean, and crossing session boundaries silently from inside a skill defeats it.

## Authorship

The snapshot lines (issue state, PR state, base, verdict, health, merge, cleanup) are mechanical and should be filled from data the skill already has in hand — typically the session's prep facts block plus the locally-produced results of the current run (the review verdict, the cache comment's SHA, the merge command's outcome). Don't re-fetch just to render the handoff; if the cache is fresh enough to gate decisions, it's fresh enough to print.

The `Next:` action and the `Why:` line are judgment, not data. The skill's main loop authors them — the choice of next skill, the framing of why, and the exact argument shape passed to the next slash command are decisions the skill makes based on the run it just completed.

One part of the argument is fixed, not judged: **the number names the target the next skill's prep consumes.** The evaluator consumes a PR (`/ctrl-freaq:evaluator #<PR>`); every other pipeline skill consumes the issue it will work on. That includes a resolver re-entry on an in-flight PR — `/ctrl-freaq:resolver #<issue>`, where the issue is the one the PR closes (the story on a story PR, the epic on an integration PR) — **never the PR's number and never a `continue` keyword**: the resolver's prep derives continue mode from the issue's prior-PR state, and a PR number is refused as `TARGET_IS_PR`, costing the next session a decision card before any work starts. A resolver re-entry may also carry a **trailing** `--pause` when the emitting session ran under it (`/ctrl-freaq:resolver #<issue> --pause` — stop after every phase): the operator's per-invocation override outlives the session that received it. Never a leading flag, and never on the evaluator's command.
