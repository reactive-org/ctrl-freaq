---
name: question-resolver
disable-model-invocation: true
description: Assisted closing of an open `question`-type GitHub issue. Evaluates the question and its thread against the project docs (especially hard constraints — regulatory / legal / contractual), discusses a decision with the operator, records the operator's approved decision as a durable `<!-- question-decision:v1 -->` comment, offers to close the issue, and produces detailed **proposals** for folding the decision back into the docs (state-now; never applied). It **does not make the decision** — the operator does — and it never edits docs. Explicit-invocation only — run it as `/ctrl-freaq:question-resolver <issue>`. Not a pipeline stage; not for build issues (that's `/ctrl-freaq:resolver`) or for filing a new question (that's `/ctrl-freaq:drafter`).
---

# question-resolver — router

The assisted-closing path for **one** open `question`-type issue: ground it against the docs, help the
operator reach a decision, record that decision durably, and **propose** (never apply) the doc fold-back
— completing the open-question lifecycle open → resolve → fold-back. **The operator decides — never you.**
You ground the question, surface the constraints, present the viable options with a recommendation, and
faithfully record the operator's call. This matters most because a question's `## Constraints` are often
regulatory/legal — the exact place a model must not decide silently. One interactive session; ends with a
plain summary, never a `## Handoff`.

## 1. Prep

One call (resolve the repo first — `gh repo view --json nameWithOwner -q '.nameWithOwner'`):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_question_resolver.py <issue> <owner/repo> [--root <path>]
```

Returns one **facts block** (`architecture.md §4`): `repo`, `root`, `target` (`number`/`title`/`state`/
`labels`), `is_question`, `reentrancy` (`mode` fresh/revise + `prior_decision` on revise — its
`comment_id` for the marker replace), `already_closed`, `blocking` (the build issues this question
gates → `## Unblocks`), `blocked_by`, `grounding_docs` (the repo's `<!-- doc-catalogue -->` entries, read
at `root`'s working tree — the constraint audit's doc set), staged `sections`, `scratch`, `attention`.
Consume as **data**. A
`needs_decision` is `AUTH_REQUIRED` or the >1-marker `MARKER_AMBIGUOUS` (v1's "which decision is
current") — render it as one `AskUserQuestion` card and stop.

## 2. Route — one linear flow

**No mode fork**: read thread → ground → discuss+decide → verify → record → offer close → propose
fold-back → summary. Fresh vs revise is a **fact gate** (`reentrancy.mode`), not a route; the offered
close/reopen is a **runtime gate**. Read the one playbook and run it end to end:

| Facts | Playbook |
|---|---|
| any resolve run | [`playbooks/resolve-flow.md`](playbooks/resolve-flow.md) |

Contracts (read when the step needs them):
[links](../_shared/open-question-links.md) (the closing protocol, tiered read, decision marker, fold-back),
[question-issue](../_shared/question-issue.md), [asking](../_shared/asking-the-user.md).

## 3. Invariants

- **Not a `question` issue** (`is_question` false) → stop and say so; point at `/ctrl-freaq:resolver`
  for build work. Do nothing else.
- **The operator decides — never you.** Present state / options-with-cited-constraints / coverage gaps /
  a marked recommendation; the operator's call is the decision. If every option is constraint-ruled-out
  or the reading is `AMBIGUOUS`, surface that — don't force a decision.
- **Verify before recording.** The constraint audit
  ([`references/constraint-audit-prompt.md`](references/constraint-audit-prompt.md)) runs on **every**
  decision. A **BLOCKER** halts recording and returns to the discussion — never silently overridden.
- **Two write surfaces only, both gated: the decision comment and the offered close.** Docs are
  **proposal-only** — never edit a doc. Record via `gh_persist.py comment` (stage the body to
  `facts.scratch` first); on revise pass `--delete-marker-id <reentrancy.prior_decision.comment_id>` so
  the old comment is **replaced, not duplicated**. Close via `gh_persist.py close` (a no-op on an already-
  closed issue — safe to re-run); offer `reopen` only when a materially-changed decision needs visibility.

## 4. Summary — not a `## Handoff`

End with a plain **summary**: the decision recorded (comment URL) and whether the issue was closed; the
doc fold-back proposals (which docs/sections — for the operator to apply); and the build issues this
decision **unblocks** (`blocking`) — for each, breadcrumb the downstream step (`/ctrl-freaq:planner
<M>` or the drafter revise) per the closing protocol. A pointer, not a forward handoff — this skill never
crosses the session boundary or removes a block itself.
