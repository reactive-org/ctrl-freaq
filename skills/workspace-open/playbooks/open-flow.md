# workspace-open — the one flow

Open the issue's work workspace and hand the operator the path. Linear; no branches beyond the
fact gates below.

## 1. Open

Run the prep call from the router §1 (it is the action — branch derivation, linking, worktree
create/reuse, setup hooks, all in one envelope). On `needs_decision`, render the one
`AskUserQuestion` card (router §1) and stop; act on the answer only where it names a concrete
re-run (e.g. `BRANCH_IN_USE` → "use the existing worktree" means summarize that worktree instead).

## 2. Fact gates (inside the flow, not routes)

- **Gated row** (`vector.gate` present — a foreign open or draft PR already claims this issue):
  prep created **nothing**. Render the gate's `header`/`options` as one `AskUserQuestion` card
  verbatim. On "Review it" / "Leave a comment" / "Wait": stop with a summary saying no workspace
  was opened and why. On "Take it over": re-run the prep — the takeover continues on the other
  author's branch only if the operator then names it explicitly; never silently.
- **Setup-hook failure** (`workspace.setup.succeeded` false): the worktree exists but is not
  ready. Lead the summary with the failing step (`setup.first_failure.step` / `.command` /
  `.output_tail`) and tell the operator to fix the repo-side hook, then re-run this tool (open is
  idempotent — re-entry reuses the worktree and re-runs the hooks).

## 3. Summary

Render, in order:

1. **Branch** — `branch.name` (`branch.source`; note `collided_with` when a `-vN` suffix was
   taken; note the epic-bootstrap case: this run created the epic integration branch).
2. **Link** — created / adopted existing / unsupported (`ISSUE_LINK_UNSUPPORTED` notice → the
   branch exists locally only; say the push will bind it when the first PR opens).
3. **Workspace** — `workspace.path`, `reused` or fresh, hook outcome, `dirty`/`unpushed_commits`
   when non-zero (a reused worktree may carry in-flight work — that is information, not an error).
4. **Trust** — the router §4 trust pointer, as one line, with its path as the router gives it. It is
   unconditional: the summary never predicts whether Desktop will prompt.
5. **Next step** — exactly the router §4 routing on `plan.present`, with the real path and issue
   number substituted. The path line is load-bearing: the next session must be **started inside
   it**, and its prep will refuse (a `WORKSPACE_MISMATCH` card) anywhere else.
