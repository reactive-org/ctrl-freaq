# Worktree hook blocks — shared reference

The consuming repo's **`<!-- worktree-setup -->` / `<!-- worktree-teardown -->` blocks**: the
external contract ([prd.md §7](../../docs/prd.md) "Config marker blocks"). A repo declares the
commands that provision and release per-worktree resources; the plugin discovers, parses, and runs
them.

**Scope of this file.** The block format and the semantics a repo must honor when authoring one.
The *mechanics* — where workspaces live, reuse rules, when hooks fire, how failures are
classified — belong to `scripts/workspace.py` and
[architecture.md §6](../../docs/architecture.md); this file does not restate them. `setup` authors
these blocks (see `skills/setup/references/block-authoring.md`). The setup commands run at three
trigger points, all "ensures" in this file's sense: **workspace-open** creates or reuses the
worktree (`workspace.py ensure`), and **every resolver or evaluator session entry** re-runs them
via the prep's workspace assertion (`workspace.py attach`); the landing tools' self-created staging
workspaces keep the plain ensure wiring. The teardown commands run once, by **workspace-close**
(`workspace.py remove --work`), before removal.

**Which checkout supplies the block.** The working tree of the checkout the command runs in —
committed or not. workspace-open reads the checkout the operator invoked it from (so the branch
they chose to stand on is the one that supplies the commands); a resolver or evaluator session
entry reads its own worktree; workspace-close reads the worktree being closed. Through v3 all of
these read `origin/main` blobs at a pin, and an uncommitted or unmerged block was invisible; the
pin is retired, which is what makes a hook change testable before it merges. Two consequences worth
stating plainly: a branch can supply hook commands that run automatically on session entry, and a
branch that deletes its `COMMANDS.md`/`CLAUDE.md` block silently runs no hooks. The facts block
reports the supplying checkout, branch, SHA, and dirty state (`setup.source` / `teardown.source`);
reporting is the whole of the control — there is deliberately no confirmation gate.

## The blocks a consuming repo declares

Both blocks live in `COMMANDS.md` (preferred) or `CLAUDE.md`:

```markdown
<!-- worktree-setup -->
- `<command>` — <description>
- `<command>` — <description>
<!-- /worktree-setup -->

<!-- worktree-teardown -->
- `<command>` — <description>
<!-- /worktree-teardown -->
```

Format matches the other list-style command blocks (`issue-resolver-fast-checks`,
`pr-evaluator-static-checks`): one Markdown list item per command — a backtick-quoted command, then
` — `, then a short human description. **Order matters** — commands run in declaration order. Either
block is optional; if a phase's block is absent the phase is a clean no-op (no warning, no prompt —
most repos need neither hook). The commands run **inside the workspace**, as the consuming repo's
own opaque shell commands; the trust boundary is `setup`'s confirm-before-write gate, and nothing
re-validates them at run time.

## What the commands must guarantee

- **Setup is idempotent.** It runs on **every** workspace ensure — fresh create *and* reuse — so
  re-entering a healthy workspace must cost only the idempotency check (reuse the still-resolving
  simulator UDID on a Swift/iOS project, or the already-bound dev port / scratch database on a Rails
  project; otherwise discard the stale state and re-provision). Running on every entry is
  load-bearing: a workspace whose per-worktree state went missing for any reason silently falls back
  to whatever **global** resource the test wrapper's defaults pick, which masks the isolation the
  hook exists to provide — tests appear to run, but against a shared resource, so passes and
  failures are both untrustworthy.
- **Setup persists whatever state the rest of the workflow needs**, and the plugin never interprets
  that state. Common patterns: write a `<workspace>/.worktree-state/<key>` file the project's other
  commands read; allocate a free port and export it via a `.envrc`; provision a scratch container or
  database and record its handle. Make setup idempotent against a half-failed prior run so it can be
  re-triggered without orphaning resources.
- **Teardown is best-effort and tolerant of missing state.** It reads the same state setup wrote and
  releases it; it may run against a workspace whose setup partially failed, or which the operator
  already cleaned up by hand.
- **Teardown runs before the workspace is removed.** The teardown commands live *inside* the
  workspace (e.g. a checked-in `./scripts/worktree-teardown.sh`), so once the workspace is removed
  the commands are gone and any resources they would have released — simulators, containers, ports,
  scratch databases — leak. The block is discovered there too, so a branch may version its own
  teardown. One interaction to know: an *uncommitted* teardown edit never runs, because a dirty
  workspace is refused before teardown is reached — `workspace.py lint --phase teardown --root
  <workspace>` is the preview for that case.

Setup failure is fail-fast (the workspace exists but is not ready for tests, so proceeding would run
against a missing resource); teardown failure is logged and never blocks removal. Those policies are
fixed by `workspace.py`; the *idempotency* of the commands themselves is the consuming repo's
responsibility.

## Releasing a workspace

Every workspace — merged, abandoned, or mis-opened — is reclaimed the same way: the operator runs
`/ctrl-freaq:workspace-close <branch-or-issue>`, which runs the teardown commands and then
the gated removal. Run it promptly after a merge or an abandonment when setup allocates a scarce
per-worktree resource (a license-limited simulator, a bound port, a scratch DB) — until it runs,
the resource stays held. The evaluator's post-merge handoff hands the exact command; nothing
removes a workspace automatically.

Comment-only / no-code responses (questions, blocked issues, duplicates) never open a workspace at
all, so neither hook fires.
