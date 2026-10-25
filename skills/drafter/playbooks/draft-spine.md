# Draft spine — shared across new / revise / question

The draft-and-verify-and-file backbone every route runs, ending in a hand-back to the routed playbook
for its handoff. Which template, which reviewer dimensions, which review tier are **facts** the routed
playbook supplies (SKILL.md §2); all facts come from the prep facts block (SKILL.md §1).

## Gather missing context

Ask only what's needed — the user is mid-flow; a 10-question interrogation is worse than a slightly
thinner issue. The per-type minimum-viable set is a **fact of the classification**, not a branch:

- **Bug** — what happened, what was expected, and repro (or at least what they were doing); ask about
  environment only when it could plausibly matter.
- **Incomplete feature** — what works today, what's missing, what "done" looks like.
- **New feature** — persona, goal, the "so that" motivation; a Definition of done is a stretch, not a gate.
- **Question** — the question, the audience(s), enough context to answer it cold, the grounding
  references, what the answer unblocks, and any **hard external constraints** (regulation, legal, SLA,
  platform limits) each paired with the force that fixes it (`../_shared/question-issue.md` `## Constraints`).

Never invent repro steps, error messages, or behavior the user didn't describe — `[to be filled in]` or ask.

## Resolving open questions (Step 3.5) — build issues only

Runs when the source (feedback, PRD, a design/architecture doc) carries an **unresolved open question**
(OQ) that gates the scope you're drafting. Detect and match per
[`../../_shared/open-question-detection.md`](../../_shared/open-question-detection.md) (the `config.oq_markers`
hint or the heuristic cues + the tracker de-dup search). Scope is **this issue** — only the OQs that gate
what you're drafting, not a project-wide sweep (that's `/ctrl-freaq:question-sweep`).

**Match first.** Consult the tracker de-dup search before proposing to file a companion (SKILL.md §1 names
which source applies) — proposing a file before checking is how you duplicate a question that already exists.

Then get the user's **disposition** per OQ — one `AskUserQuestion` card (`header: "OQ <id>"`), this closed set, recommendation judged per OQ and listed first:

- **Scope it out** — the gated part leaves this issue's scope: a `## Out of scope` line names
  the OQ; nothing undecided lands in the DoD. No native dependency. Recommend it when the OQ is peripheral to the issue.
- **Keep in-scope (blocked)** — the gated part stays in the DoD; the build issue is set natively
  `blocked by` the companion question (which holds the resolver/evaluator until it answers — warn the user). Recommend
  it when the OQ is core to the issue's value.
- **Build on a provisional default** — build now on a named provisional choice; record `default:` + `retires-when:` so the planner carries it as a watchpoint. Recommend it when a sensible default is cheap to retire.

**Companion — reuse, file, or defer.** A match exists → reuse it and announce it ("Reusing #N as this OQ's companion; say if you'd rather file a new one"),
reaching for `AskUserQuestion` only for genuine ambiguity (**Reuse #N** / **File a new one**); a reused companion is
not re-filed — you post a lightweight `comment` on it cross-linking this build issue, and set this issue's
`blocked by` / `## Open questions` reference. No match → offer to **file** a companion now (a normal
`question` draft per `../../_shared/question-issue.md`, reusing this spine's review + gate) or **defer to
the sweep**. Either way, record the OQ at Step 4.

**Falsifiable rule.** Absorbing an untracked OQ silently is a defect. An unresolved source-doc OQ is
never absorbed into a build issue without **(i)** a tracked companion — a matched tracker issue (the
`facts.open_question_candidates` / `--oq-query` search consulted and a candidate `#N` adopted) **or** a
freshly **filed** `question` issue — **and (ii)** an explicit disposition from the closed set
(`scoped-out` / `in-scope (blocked)` / `provisional-default`). `question: (not filed)` is written **only
when** the tracker de-dup search returned no candidate, or every returned candidate was explicitly rejected
(with a reason) as not-the-same-question — a `(not filed)` written with an unconsulted or non-empty
candidate set is the bug-(a) defect. `in-scope (blocked)` additionally **requires** a companion to exist
and sets the native `blocked by` on it; if the user picks it but declines to file and none matches, fall
back to `scoped-out` or a **prose-only** blocker (`question: (not filed)`, no native block) — never emit a
`--blocked-by` with no `#N`.

## Draft against the template

Follow the repo's own issue template verbatim when one exists (`facts.repo_context.templates.present`) —
templates encode the team's expectations — but its criteria go under `## Definition of done`. Otherwise use
the built-in fallbacks in [`../references/issue-templates.md`](../references/issue-templates.md). Map the
feedback to the repo's **existing** labels (`facts.repo_context.labels`) — don't invent `bug` when the repo
uses `kind/bug`, or `priority:high` when it uses `P1`. Three labels max unless asked. Title conventions and
the routed playbook's schema sections (`## Open questions`, `## Related issues`, `## PRD impact`, the Story
`**Epic:**` backlink) are named by the routed playbook. **Anchor rule + grounding altitude** (top of that
reference, binding every body): durable anchors only, never an authored `path:line`; DoD sweeps state
criterion + exemption classes, never a frozen hit list; freeze judgment, not re-derivable facts.

**PRD tension → `## PRD impact`.** When the repo declares a PRD (`facts.repo_context.docs.prd` — its
catalogue's `prd` entry, at whatever path it names; for a directory, read the `members` the feedback
touches), ground language in its personas/terminology and watch for tension — the feedback **contradicts**
the PRD, **extends** it into uncovered territory, or an incomplete-feature report describes a **gap**
against a PRD section. On genuine tension, add a `## PRD impact` note and gate (`header: "PRD conflict"`):
**File to update PRD** / **File the feature** / **Flag for discussion** — the user decides whether the PRD
or the feedback is stale. No tension → omit the section; don't add it just to show you read the PRD.

**Related issues → `## Related issues` + native dep.** When the user referenced other issues, read them
(`gh issue view <N> --json title,state,body,labels`) and classify the relationship, mirroring the user's
hedging (`May be resolved by #21`, `Related to #12`, `Blocked by #50`, `Duplicate of #99`,
`Expected behavior is described in #78`, `Closes #5`). **Never** use an auto-close keyword
(`closes`/`fixes`/`resolves`) unless the user explicitly said this issue resolves another. A `Blocked by
#N` line also sets a native `blocked by` (below); the prose line is the always-present fallback.

**The ambient issue.** `facts.ambient` (present only when the branch names one) is the issue this checkout
is standing in — one the user never had to type. Filing an unrelated bug from inside an epic's branch is normal, so never
link silently. A draft plainly unrelated to #N files unlinked, announced ("Not linking this to #N; say if it belongs there"); one that
plausibly belongs to #N asks once (`header: "Branch issue"`), recommending the relation its text implies: **Unrelated** links nothing; **Related to #N** / **Blocked by #N** write the forms above; **Child of #N** — offered only when
`ambient.pattern` is `epic` — writes `Related to #N` and routes the handoff to the slicer, sole writer of
the parent edge since #16.

**Recording open questions.** For each Step-3.5 disposition write the `## Open questions` section per
[`../../_shared/open-question-links.md`](../../_shared/open-question-links.md) (marker
`<!-- open-question-links:v1 -->` on the first line inside the section), plus: a `## Out of scope` line
per `scoped-out` OQ naming it; the native `blocked by` per filed-companion `in-scope (blocked)` OQ; and a
`Related to #N` line in `## Related issues` for every filed companion `question: #N`. Don't fabricate OQs
the source didn't mark.

## Review loop

Before showing the draft, hand it to the isolated review sub-agent
[`../references/issue-reviewer-prompt.md`](../references/issue-reviewer-prompt.md) — it runs **without the
conversation history**, so it tests whether the issue stands on its own the way a teammate reading it cold
would; it runs for both new drafts and revisions — don't skip it. Dispatch an `Explore` sub-agent, inlining
the draft, the `mode` (`draft` / `revise <N>`), the **review tier**, `facts.root.path` (ground every read
there **by absolute path** — the sub-agent has its own cwd, and the session's checkout IS the intended
vantage), `facts.config.oq_markers`, `facts.repo_context.docs.entries` as `<<catalogue_entries>>`, and the
**dimension set the routed playbook names**. The routed playbook names the tier — default **full** absent a
stated tier; a **proxy-filed follow-up (lean review)** invocation is always **lean** (the provenance floor).

- **lean** — exactly one pass: drop unevidenced findings, apply blockers; show suggestions and nits
  **unapplied** at the filing gate (auto-applying is the ratchet — it regrows the review surface).
- **full** — up to **3 passes** (the 3-pass cap): drop unevidenced findings; **only a blocker keeps the loop running** — a pass with none left exits clean, its suggestions and nits shown **unapplied** at the filing gate (the lean posture: no pass for polish);
  a blocker repeated across two passes with no progress → **circular** exit; three passes with blockers
  left → **cap** exit; passes 2+ get a changed-since-last-pass summary and verify only changed claims +
  prior findings' resolutions — never re-verify an unchanged claim. On a cap/circular exit, show the
  draft + a "Review notes" block and gate (`header: "Review loop"`): **It's real, keep fixing** /
  **Override and file**. A pass with a blocker left applies blockers always, suggestions by default, nits silently.

## Show + filing gate

Present the full draft (title, labels, priority, body between `---` fences), plus any unresolved review
findings. **Before asking, stage the approved body to disk** — write the exact rendered body to
`<facts.scratch>/<name>.md`; the staged file *is* the body — and prove its criteria parse as the DoD:
`${CLAUDE_PLUGIN_ROOT}/scripts/parse.py dod "<facts.scratch>/<name>.md"` → `ok`, `present: true` when the
template has criteria, and no criteria left in `other_checklists` (a repo template's confirmation list may
stay; `revise.md` owns a slice's); fix and re-run. Then gate (`header: "File issue?"`): **File it** / **Keep
iterating**. Anything but an explicit "File it" is keep-iterating — never file without it.

## Staged filing — the single write path

File through `gh_persist.py` by passing the staged **path**, never re-inlining the body:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create <owner/repo> "<facts.scratch>/<name>.md" \
  --title "<approved title>" --label <type> [--label <priority> …] \
  [--blocked-by <companion #N for each filed-companion in-scope-blocked OQ, plus any user-stated blocker>]
```

`create` returns the new issue URL, `#NN`, `body_bytes`, and `body_sha256`. An empty/missing staged file
exits with `EMPTY_BODY_FILE` and posts nothing — re-stage and re-run with the same path. Native deps are
**capability-gated**: on a repo/gh without the feature `create` files the issue and returns a
`DEPS_UNSUPPORTED` notice instead of failing — the prose `Blocked by #N.` / `## Open questions` /
`Related to #N` links are the always-present fallback, so keep them regardless. `scoped-out`,
`provisional-default`, and prose-only `in-scope (blocked)` OQs contribute **no** `--blocked-by` element.

Audience labels (questions) are created just before filing (`gh label create "audience:<x>" …` inline,
per `../../_shared/question-issue.md`) so `--label audience:<x>` doesn't fail on a missing label. A
label-only delta on an existing issue (revise mode) uses `gh_persist.py edit-labels <repo> <issue> --add
… --remove …`; a body edit uses `edit-body`; a post-file native-dep add/remove uses `link`. Return to the
routed playbook for its `## Handoff`.
