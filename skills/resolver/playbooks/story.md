# Story under an open epic

Route for any target under an open parent epic — `vector.type == story`, and equally a target whose
type is `standard` but whose native parent resolved to an open epic with an integration branch
(`facts.story.branch_facts.branch` set; #31). The label classifies the *kind of work*; what selects
this route is the base the PR will carry. The PR bases on the parent's `epic/<N>-<slug>` integration
branch, not `main`, so `main` stays stable while the epic is in flight. `facts.story` carries the
parent epic + its branch facts; `facts.audit_ref` is the parent epic's branch (bare); the asserted
workspace's `base_ref` is that branch; prep ensured
`facts.read_workspaces.audit` at that branch for the audit's code reads (script-internal plumbing).

**Run the spine first.** Read [`resolve-spine.md`](resolve-spine.md) and execute it end to end. The
type differences are **facts**, not branches: the audit runs dimension 5 (cross-issue contract drift)
against the parent epic + sibling stories, the PR bases on the epic branch, and the PR body carries the
integration-branch caveat sentence (`This story targets the \`epic/<N>-<slug>\` integration branch and
will reach \`main\` via the integration PR for epic #<N>.`). A story run **surfaces** epic-vs-main drift
as an informational note but never rectifies it — a story-flow rebase would force-push under sibling
story PRs; rectification belongs to the epic-as-target run (`epic.md`). Everything below runs only after
the spine returns; on a re-route exit, emit the matching re-route handoff instead.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md). Under an open epic
the heading is a `Story:` line plus an `Epic:` line for the parent's progress, per the shared schema's
Epic-variant rules — otherwise the rubric matches `standard.md`:

- **Forward — story PR opened / updated**: `Story:` (`… · story · plan: ✓`) + `Epic:` (`open (K of M
  stories closed)`) + `PR:` (`base epic/<N>-<slug> · review: not run · …`), `Next:
  /ctrl-freaq:evaluator #<PR>`, `Why:`.
- **Multi-phase** and **revision run** — the same shapes as `standard.md` (non-final code phase /
  operator phase / last planned phase shipped / revision run), with the `Story:`/`Epic:` heading.
- **Re-route → planner** (plan drift / thread-supersedes-plan / a revision run's vetoed DoD bullet):
  `plan: stale`, `Next: /ctrl-freaq:planner revise #<N>`; `Why:` quotes the locked decision +
  `file:line`, or the `… evaluator rejected: …` annotation.
- **Re-route → drafter** (audit blocker incl. a dimension-5 sibling contract conflict, or a doc
  conflict): `Next: /ctrl-freaq:drafter revise #<N>`; `Why:` names the dimension + quotes the
  conflicting passages (one re-route per contract disagreement — the audit flags each individually).
