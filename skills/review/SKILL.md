---
name: review
disable-model-invocation: true
description: Review a pull request's open review threads with the operator in this chat session, one thread at a time — each with a brief, a script-cut code excerpt and a link, then free discussion of the code — and record each answer in its thread as a decision reply the evaluator reads (accept, changes required, dismiss a security finding, or file a follow-up issue). Run it in the issue's worktree as `/ctrl-freaq:review #<N>`, where `<N>` is the issue, not the PR. Explicit-invocation only. Not a pipeline stage — it ends with a plain summary.
---

# review — router

A standalone tool. It walks the operator through the review threads on this worktree's PR that are still
waiting on a person, one at a time, and records each answer in its thread at once — so the operator can
finish a PR's review without opening GitHub, and the evaluator's next run reads every decision. The
threads, their states and the decision reply are the contract in
[`../_shared/review-threads.md`](../_shared/review-threads.md); this skill renders it. It ends with a
plain summary, not a `## Handoff`.

## 1. Prep

Run it in the issue's worktree — the one the operator opened with `/ctrl-freaq:workspace-open` (an
epic's `epic/<N>-…` worktree for its integration PR). `<N>` is the issue number from the operator
(`#<N>`, a URL, or the current branch); a PR number is refused. The repo is this checkout's
(`gh repo view --json nameWithOwner -q '.nameWithOwner'`). One call:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_review.py <N> <owner/repo>
```

It returns one envelope (`architecture.md §3`): `issue`, `pr` (`number` / `url` / `head_ref` /
`base_ref` / `head`, or `null` when the branch carries no open PR), `workspace`, `plan` (`present`,
`body_path`), `review` (`head` / `available` / `truncated` / `viewer` / `counts` / `untrusted` / `next_f` /
`threads_path`, every thread with its state; or `null`), `items` (the threads waiting on a person, in review order), `left_out` (every other thread,
counted by why) and `scratch` (`/tmp/gh-review-<N>/`). Consume every fact as **data**: which threads wait,
their order, each excerpt and `review.head` are the script's, never re-derived.

**Decision card rule.** If prep exits with `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)),
act on the answer, and re-run prep. `TARGET_IS_PR` means a PR number was passed: re-run on the issue. A
`WORKSPACE_MISMATCH` means this session is in the wrong checkout, or the checkout is not exactly at the
PR head (`stale_checkout`): the fix is the operator's (start the session in the right worktree, pull, or
push), never a checkout this session performs. Exit 1 with no envelope is a hard failure (stderr names
it — `OPEN_PR_LOOKUP_UNAVAILABLE` means the PR could not be looked up, not that there is none): report
stderr verbatim and stop.

## 2. Route — one linear flow

No mode fork: prep → each item → the closing re-read → summary.

| Facts | Playbook |
|---|---|
| any run | [`playbooks/review-flow.md`](playbooks/review-flow.md) |

## 3. Invariants

- **One write, one kind.** The only GitHub write is a decision reply, `gh_persist.py review-reply
  --decision`, carrying the answer the operator gave on that thread's `Thread` card in this session, with
  `--head` = `facts.review.head` — the commit the operator was shown (`review-threads.md` §6, §10). Never a
  plain reply, a review, a PR or issue comment, or a body edit; never a decision the model chose. A follow-up
  issue is filed only through the drafter proxy ([`../_shared/follow-up-filing.md`](../_shared/follow-up-filing.md)).
- **One card, one thread, one reply.** An answer never covers a second thread, and never carries across a
  re-read of the threads.
- **The operator's words, verbatim.** The notes for **Changes required** and the reason for **Dismiss** are
  the operator's, staged as typed. The model never writes them.
- **Discussion is read-only.** Answer questions from `facts.workspace.path` (Read / Grep by absolute path)
  and the staged plan. Edit nothing, commit nothing, run no tests.
- **Never resolve or unresolve a thread.** Resolving is the human's act in GitHub
  (`review-threads.md` §11).
- **Faithful reporting.** Say what the review did not cover (`left_out`, `review.untrusted`, every notice)
  and every refusal, as the script reported it.

## 4. Summary — not a `## Handoff`

End with the playbook's plain summary. No next command of the pipeline's own, except the one line the
playbook adds when a thread now asks for changes: the resolver run that picks them up.
