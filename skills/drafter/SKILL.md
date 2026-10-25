---
name: drafter
description: Drafts well-structured GitHub issues from informal developer feedback and files them, or revises an existing one. Use this skill whenever the user describes a bug they hit, an incomplete or half-built feature they noticed, or a new feature idea — and wants it captured as a GitHub issue. Trigger this even when the user does not explicitly say "make an issue" — phrases like "I should track this," "let's file this," "we need to remember to fix X," "log this for later," or simply describing a problem in a repo context all qualify. Also use this skill to **revise an existing issue** when the user references one by number/URL with revision intent — phrases like "revise #N," "update issue #N," "improve #N," "does #N still match the docs?", or "rewrite the description of #N." Also use it to **file or track an open question / decision request** for one or more named audiences (business, architect, developer, UX, …) as a `question`-type issue labeled by audience — but only on an explicit capture verb: "file/open/log/track a question," "capture this decision for the architect," "raise an issue to ask the business," or "track `PRD-OQ-06b` as an issue." A bare decision-question with no capture intent ("should we use phone or video?") is asking for *your* answer and is **not** a trigger; naming an audience alone ("the business should weigh in on this") isn't one either without a file/track/log verb. Works best when the user is inside a git repository directory. Uses repo issue templates and labels if they exist, otherwise applies a consistent built-in format. Reads the project PRD (the `prd`-role document the repo's doc catalogue declares) to ground feature framing and surface tensions between feedback and spec. Every drafted or revised issue is automatically validated by an isolated review sub-agent that checks the issue against the project's declared docs and current codebase before the user sees the final draft. Do NOT use for: writing or fixing code (that's the resolver), planning the approach (that's the planner), reviewing a PR or choosing a merge strategy (that's the evaluator).
---

# drafter — router

The first stage of the pipeline: informal feedback in → **one** filed/revised, template-conformant issue
(bug / incomplete / feature / story / Epic), or a filed/revised `question`-type issue — always ending in
a single `## Handoff`. One issue per run, an Epic's body included: the drafter **decomposes nothing** —
cutting an Epic into stories, or an issue into slices, is the slicer's at either altitude (#16). One
drafting attempt, one session. Read this router, run prep, route to exactly one playbook, then hand off.
Scripts own the mechanical I/O; your judgment is the classification, the PRD-tension calls, the drafting,
the open-question dispositions, the review verdicts, and the handoff `Why:`.

## 1. Prep

Assemble the entire starting state in **one** call. `<owner/repo>` is the repo; `--issue N` selects
revise mode (a target issue exists) — omit it for new-issue mode:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_drafter.py <owner/repo> [--issue N]
```

It returns one JSON **facts block** (`architecture.md §4`): `vector` (`mode` × `type` — the routing contract),
`suggested_playbook`, `ambient` (the issue this checkout's branch is standing in, or `null` — the relationship the
spine offers), `target` (revise: number/title/state/labels/`blocked_by`/ `blocking`/`deps_available`),
`config.oq_markers` (the `<!-- drafter-open-question-markers -->` block, or `heuristics_active` — a **detection
hint**, never a gate), `repo_context` (issue templates, `gh label list`, and `docs` — the repo's own `<!--
doc-catalogue -->` entries plus its `prd`), `open_questions` + `open_question_candidates` (the search-before-file
tracker de-dup on the target body), `revise` mode facts (`revise.dod`: the body's DoD shape), `sections` (spilled
issue-body/ thread/plan-marker paths), and `attention`. Consume every fact as **data** — never re-derive the mode,
the target's type, or the tracker candidates in prose; prep already did.

**Decision card rule.** If prep exits with `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)), act on the
answer, and re-run prep. This is the single universal handler for every closed-set code (`AUTH_REQUIRED`,
`AMBIGUOUS`, …). A re-run mints a **fresh `facts.scratch`** in new mode — stage to the latest value.

**Newly-detected OQ lookup.** When you spot an open question in the feedback/grounding text that prep's
body-driven `open_question_candidates` never searched (new mode has no target body; a grounding-doc OQ
isn't in the issue body), run the tracker de-dup search before recording it `(not filed)` or filing a
companion — one authorized call, never a raw `gh issue list`:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_drafter.py <owner/repo> --oq-query "<OQ topic>"
```

## 2. Route

Prep proposes `suggested_playbook`; confirm it against this table, keyed on `vector`. Read **exactly
one** playbook.

| `vector` | Playbook | Flow |
|---|---|---|
| `mode: new` (no target issue) | `playbooks/new.md` | classify → gather → draft → review → gate → file a single build issue |
| `mode: revise`, `type` ≠ `question` | `playbooks/revise.md` | fetch + latest-direction → review → diff-show → confirm → edit-body |
| `mode: revise`, `type: question` | `playbooks/question.md` | revise the question issue; terminal handoff |

Every playbook opens by reading the shared spine [`playbooks/draft-spine.md`](playbooks/draft-spine.md)
(gather missing context → resolve open questions → draft against the template → review loop → show +
filing gate → staged filing → hand back). The routed playbook supplies only what **differs in actions**:
its reconnaissance, the template sections it fills, the reviewer dimension set, the filing sequence, and
its handoff shape. Type differences the spine consumes (which template, which dimensions) are
**facts / values**, never branches.

**New-mode classification override rule** (`architecture.md §5`). Prep can't read the feedback text, so it
proposes `new.md` for **every** new-mode session (`vector.type` is `null`). Step 1 of `new.md` classifies
the feedback — bug / incomplete / new feature / Epic / question. When that classification is
**question**, the router **overrides** `suggested_playbook` and reads `question.md` instead — evidence the
script could not see ahead of the read. State the override reason (the S13-scenario-3 precedent: a route
the classification, not the label, selects). One route per session; do **not** interleave type branches
inside a playbook body — the route *is* the branch. An **Epic** needs no override: it is one issue with its
own template, filed by `new.md` like any other, its stories cut afterwards by the slicer.

**Reshaping an existing issue into an Epic is not the drafter's** (#16). Revise mode revises the target's
*body*; it never promotes. When the invocation asks to re-shape #N as an Epic and split it (e.g. a planner
seam-gate handoff), that whole flow belongs to `/ctrl-freaq:slicer promote #N to an Epic`. Say so and
stop: rewriting the body here without cutting the stories would leave an Epic with no children.

## 3. Invariants

Universal across every route:

- **Nothing is filed without the Step-6 gate.** No exceptions: the Epic batch's gate-skip retired with
  the batch itself (#16), so every route files exactly one issue behind one confirmation. Silence, a
  tweak request, or "Other" all count as keep-iterating. "Filed issues are annoying to clean up; a
  10-second confirmation prevents that."
- **Staged-body writes.** Every GitHub write goes through `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py`
  via Bash: stage the verbatim body to `facts.scratch` and pass the **path** — `/tmp/gh-drafter-<issue>/…`,
  or `/tmp/gh-drafter-new-<pid>/…` in new mode (per-process, so one proxy-filed follow-up batch's
  concurrent drafters never clobber each other's staged body). The script gates empty bodies
  (`EMPTY_BODY_FILE`) and returns `body_sha256` — the #626/#627 empty-body race fix (the body never
  travels through a dispatch prompt). The drafter has **no** scriptless
  raw-`gh` executor; if a real op doesn't fit a subcommand, that's a gap to report, not a raw call to roll.
- **Successful write is self-confirming.** A zero exit with a URL *is* the confirmation; never re-read it.
- **Never silently freeze an untracked OQ.** An OQ that gates a build issue's scope gets a Step-3.5
  disposition + a tracked companion (matched or filed) before it enters the body — the falsifiable rule in
  [`playbooks/draft-spine.md`](playbooks/draft-spine.md); absorbing one silently is a defect.
- **Never touch the plan comment.** Revise mode reads the `<!-- implementation-plan:v1 -->` pointer to
  preserve it verbatim; it never edits or deletes the comment — that's the planner's artifact.
- **Anti-fabrication, durable anchors.** Never invent reproduction steps, error messages, behaviors,
  out-of-scope items, or relationships the user didn't describe — vague-but-honest beats confidently-wrong.
  Issue bodies cite durable anchors, never an authored `path:line` (rule + carve-out: the templates reference).
- **Gates only for genuine decisions** (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)):
  issue size, ambiguous classification/reference, PRD conflict, OQ disposition, companion reuse-vs-file,
  the ambient-branch relationship, the filing gate, revise state-summary + diff confirmation, closed-issue
  handling, review-loop tie-break. A judgment gate left with one credible option is announced, not asked; every card opens with its brief. The issue reviewer sub-agent never calls `AskUserQuestion`; it returns findings.
- **Handoff on clean exit** (§4). One `## Handoff` block ends every clean run; it replaces any bullet-list
  summary. Don't add anything after it.

## 4. Handoff

Every clean run ends with a single `## Handoff` block — the only bridge to the next session. The schema,
omission rules, and closed-set state-marker vocabulary are owned by
[`../_shared/handoff-format.md`](../_shared/handoff-format.md); the drafter's per-outcome shapes (single
issue filed → planner; single issue with open questions → planner + `**Open questions:**` line; Epic filed
→ slicer, to cut its stories; revise → author/refresh/terminal; the terminal `question` shape) are in
[`references/handoff-renderings.md`](references/handoff-renderings.md). **Read that reference immediately
before composing the handoff — not earlier in the session — then emit the matching shape verbatim.** The
field names (`**Issue:**`/`**Epic:**`, `**Next:**`, `**Why:**`, …), the block structure, and the closed-set
state markers are **contract, not prose to summarize**: copy the shape and substitute only the
issue/Epic/story numbers, titles, and state values it names — never paraphrase, restructure, rename a
field, drop a segment, or add a block the shape doesn't have. Concretely forbidden (observed live-parity
drift): renaming `**Issue:**` to `**Filed:**` or anything else; dropping the `· <state> ·` segment; adding
an invented `Snapshot` (or similarly-named) block; inlining the fenced `Next:` command into prose instead
of its own indented code line. Fill the snapshot from data in hand — the `create` result carries the
issue/Epic/story numbers and titles; `plan: ✗` is always correct (the drafter never authors plans). The
`Why:` line is yours. The forward route is the `planner` (`/ctrl-freaq:planner`) — except a freshly
filed **Epic**, which forwards to `/ctrl-freaq:slicer <N>` to cut its stories, because an epic plan
pins cross-story contracts and needs them to exist first. An OQ deferral points at
`/ctrl-freaq:question-sweep`. A `question`'s handoff is **terminal** — a human answers it,
not a downstream skill. The handoff is the only signal; the user runs the next command in a fresh session
(session-per-skill is the context-isolation choice).
