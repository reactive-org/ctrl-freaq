---
name: workspace-close
disable-model-invocation: true
description: Release an issue/PR work workspace — run the repo's worktree-teardown hooks, then remove the worktree under `.worktrees/`, gated on dirty or unpushed state (never a silent discard). Takes a branch name or an issue number (resolved via the linked branch, the open work worktree, or the issue's own PR head). The routine last step after the evaluator merges, and the one reclamation path for abandoned or mis-opened workspaces. Explicit-invocation only — run it as `/ctrl-freaq:workspace-close <branch-or-issue>`.
---

# workspace-close — router

The operator-side closer of the v3 workspace lifecycle: **open → work → evaluate → close**. Every
workspace — merged, abandoned, or mis-opened — is reclaimed the same way, through this tool: it
runs the consuming repo's `<!-- worktree-teardown -->` hooks (best-effort, **before** removal —
the teardown commands live inside the worktree, and the block is discovered there too, so a branch
may version its own teardown), then removes it, refusing on dirty or unpushed state. One interactive session; ends with a plain summary, never a `## Handoff`.

## 1. Prep

One call (resolve the repo first — `gh repo view --json nameWithOwner -q '.nameWithOwner'`);
the call **is** the action:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_workspace_close.py <branch-or-issue> <owner/repo> [--root <path>]
```

Returns one **facts block**: `branch_resolution` (`input` / `branch` /
`via: arg|linked|worktree|pr-head`) plus the removal receipt verbatim (`removed`, `path`,
`teardown` — or `removed: false, reason: not_found`, the safe no-op). Consume as **data**. A
`needs_decision` is `AUTH_REQUIRED`, `AMBIGUOUS` (dirty/unpushed state, or an issue number that
resolves to none or several branches), or `WORKSPACE_MISMATCH` (`cwd_inside_target` — the session
is standing in the worktree it would remove) — render it as one `AskUserQuestion` card and act on
the answer. A `MERGED_PR_LOOKUP_UNAVAILABLE` notice means the merged-PR check could not run (no
auth or no network), so a post-merge worktree may gate as unpushed — report it with the card.

## 2. Route — one linear flow

No mode fork: close (the prep call) → render the receipt → summary.

| Facts | Playbook |
|---|---|
| any close run | [`playbooks/close-flow.md`](playbooks/close-flow.md) |

## 3. Invariants

- **Never a silent discard.** Dirty or unpushed state is an `AMBIGUOUS` decision card
  ("Discard and remove" / "Keep — I'll push or commit first" / "Abort"); a merged PR with extra
  post-merge local commits gets the merged-specific card. Never bypass the gate with
  `git worktree remove --force` or `git branch -D` — the script's gating IS the safety.
- **Never from inside the target.** The script refuses (`cwd_inside_target`) when run from
  within the worktree being removed — relay its remedy: re-run from the project root.
- **Teardown is best-effort and never blocks removal**; its failures are reported, not retried.
  `teardown.source` names the worktree the block was read from — report it when the operator
  expected commands that did not run.
- **Worktrees, not remote branches.** This tool removes the local worktree; remote-branch
  deletion is the repo's `delete_branch_on_merge` policy (or a manual choice) — never this
  tool's. `ro-*` read views are `workspace.py gc`'s, never this tool's.

## 4. Summary — not a `## Handoff`

End with a plain **summary**: removed or retained (and why), the teardown outcome (including any
best-effort failures, verbatim), the branch resolution used, and — when retained on a decision
the operator answered "keep" to — the exact re-run command for later.
