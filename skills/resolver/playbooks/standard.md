# Standard issue

Route for `vector.type == standard` (a regular bug / feature / refactor; audit ref and PR base are
both `main`). A single-issue resolution that lands directly on `main` via its PR.

**Run the spine first.** Read [`resolve-spine.md`](resolve-spine.md) and execute it end to end
(distill → audit → plan-gate → doc grounding → phases → code + review loop → one push per phase + DoD
projection → follow-ups). `facts.audit_ref` is `main`; the work workspace's `base_ref` is `main`; there
is no read workspace beyond the audit one (prep omits `read_workspaces` when `audit_ref == main` and no
second view is needed). Everything below runs only after the spine returns; on a re-route exit, emit the
matching re-route handoff instead.

## Handoff

Read [`../references/handoff-renderings.md`](../references/handoff-renderings.md) and match the run's
outcome to its rubric:

- **Forward — standard PR opened / updated** (default code outcome): `Issue:` line, `PR:` line
  (`base main · review: not run · health: not run · merge: not run` — the resolver runs none of the
  evaluator's checks), `Next: /ctrl-freaq:evaluator #<PR>`, `Why:`.
- **Multi-phase** — pick **non-final code phase pushed** (the session ends after a non-final phase: a
  checkpoint that ended it — `--pause`, or a look not continued, `../references/phase-continuation.md` — or an `Abort`
  after S5.2 pushed; `plan: ✓ (multi-phase: K of M …)`, PR stays draft,
  `Next: /ctrl-freaq:resolver #<N>`, plus `--pause` when the session held it),
  **operator/decision-only next phase** (surface the `deliverable` + the
  `<!-- operator-phase-complete: <N> -->` marker verbatim), or **last planned phase shipped** (PR flipped
  ready, `Next: /ctrl-freaq:evaluator #<PR>`). **Every one of these three
  shapes still carries `review: not run · health: not run`** — the review loop and the §8 gate ran
  internally this session, but that is not what those two fields mean (they are the evaluator's,
  populated only once it acts); do not render an off-closed-set glyph for "ran internally."
- **Revision run** (every phase already shipped — spine S4): PR flipped back to ready, no phase count,
  `Next: /ctrl-freaq:evaluator #<PR>`; `Why:` names what the run revised (the review's findings,
  the ledger's `apply` items).
- **Re-route → planner** (plan drift / thread-supersedes-plan / a revision run's vetoed DoD bullet):
  `plan: stale`, `Next: /ctrl-freaq:planner revise #<N>`; `Why:` quotes the locked decision +
  `file:line`, or the `… evaluator rejected: …` annotation.
- **Re-route → drafter** (audit blocker or doc conflict): `Next: /ctrl-freaq:drafter revise #<N>`;
  `Why:` names the dimension + quotes the evidence (or the doc section, for a doc conflict).
