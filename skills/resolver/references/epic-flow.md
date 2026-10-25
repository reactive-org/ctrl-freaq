# Epic-flow runbooks (resolver)

Epic-only procedures the epic-as-target route (`playbooks/epic.md`) invokes — the full-canonical-suite
runbook, the drift-rectification execution paths, and the bootstrap + legacy-recovery sequences. They are
extracted here so they don't consume the load budget on the hotter story / single-issue paths; read this
file before any rectification or bootstrap step (epic.md S1/S2/S3).

**Workspace ownership (v3).** The operator opened the epic-branch **work workspace** with
`/ctrl-freaq:workspace-open <epic>` (which also owns creating the `epic/<N>-<slug>`
integration branch, discovered or bootstrap) and started this session inside it; prep asserted
the ambient checkout is that worktree and re-ran its setup hooks — so `facts.workspace.path` is
the epic worktree, verified. This file's git commands (rebase / merge / rebase-continue / push /
the canonical suite) run **inside that workspace by absolute path**
(`git -C <facts.workspace.path> …` or a `cd "<facts.workspace.path>"` on its own line) — the
session's shell cwd is the worktree by design, but sub-shells and background commands are not
guaranteed to inherit it, so the absolute-path discipline stays (SKILL.md §3). The prompt does
**not** run `git worktree add` itself; where a runbook below shows a bare `git worktree add` it
is the v1 mechanic that workspace-open now performs — read it as "operate in the asserted
worktree." The assess-step values (`FORK_POINT`, commits-behind, open-story-PR count,
file-overlap) are computed inline in epic.md S2 before you reach the rectification runbooks.

## Running the full canonical suite (epic baseline / bootstrap / post-rectification)

This subsection applies **only** to the epic-baseline, bootstrap, and post-rectification flows — the
places that legitimately run the project's *full* canonical suite (every unit + integration test) in the
work workspace. It does **not** loosen the §8/§10.6 story gates, which stay targeted-only (see "Don't run
the full unit + integration suite at the §8/§10.6 story gates" in
[`common-pitfalls.md`](common-pitfalls.md)). It exists because a full-suite run is a 15–30 minute,
cold-build-bearing operation, and three foot-guns turned one such run into a multi-hour hang in the past.

**1. Which command — never improvise it, and never cold-rebuild on every attempt.** Use
`facts.config.canonical_suite_raw` (the `issue-resolver-canonical-suite` block, or the pr-evaluator
fallback prep already resolved) and its labelled commands:

- **First attempt** → `full-suite` (one cold build + every suite).
- **Any re-run** (the first run's result was lost or partial, or you're re-running specific failures) →
  `build-once` **once**, then `retry-without-rebuild` (narrowed to only the failures via the wrapper's
  targeted-run syntax — e.g. append `-only-testing <Suite>/<test>`, or pass `<path>:<line>` to
  `bin/rails test`). Do **not** re-issue `full-suite`.

The reason is wall-clock: on a compiled stack a plain `<wrapper> test` recompiles the whole app target on
every invocation, and that cold build — not the tests — dominates the time (on interpreted stacks the
three labels collapse to one command and there's no rebuild to avoid). Re-paying that build on each retry
is what produced the past hang. If the block is absent, prep's fallback chain already resolved a
substitute (pr-evaluator `full-suite-command`, then a notice); tell the user retries will cold-rebuild on
compiled stacks until the block is declared.

**2. Make it survive across turns — own it from the main loop.** A 15–30 min suite must be run as a
**harness-tracked background bash** (`run_in_background: true`) owned by *this* main loop, not delegated to
the `apple-platform-build-tools:builder` sub-agent. A sub-agent can end its turn while `xcodebuild` is
still running and then have its session torn down, orphaning the process and losing the final tally (this
is exactly what happened — the builder returned a partial snapshot and the run was lost). The harness
auto-notifies you when a background bash completes, and the process survives across turns because the
parent owns it. Keep the builder delegation for the *short targeted* suites at §8/§10.6 only — the full
canonical suite is the documented exception.

**3. cwd / command hygiene for the backgrounded command.** Two rules, both learned the hard way:

- **Use absolute paths; never chain the real command behind a relative `cd … &&`.** The shell cwd may
  already be the worktree (cwd persists between Bash calls), so a relative `cd .worktrees/<branch> && …`
  *fails* — and `&&` then silently short-circuits the whole command to a no-op that exits `0`, so it looks
  like the suite passed when nothing ran. Use `facts.workspace.path` as an absolute `cd "$WT"` on its own
  line, or pass the command's directory explicitly.
- **Capture to files and read the file — don't re-run to see output.** Tee the full log to one file and a
  one-line pass/fail summary to another; on the completion notification, read the summary file. Re-running
  a 15–30 min suite just to see scrolled-off output is the same wasted cost the build-once rule exists to
  avoid.

## Drift rectification (Path A rebase / Path B merge / conflict handling / post-rectification)

Reached from epic.md S2 once the epic branch is behind `main` and the strategy step has picked rebase or
merge (the 4-rule table: commits-behind × open-story-PR count × file-overlap). Run the matching path,
handle conflicts via the shared "Conflict handling" procedure, then "Post-rectification". Every command
runs in `facts.workspace.path` (the epic worktree prep asserted).

### Path A — Rebase

Rebase the epic worktree onto `origin/main`:

```bash
cd "<facts.workspace.path>"
git rebase origin/main
```

Run the rebase. If it succeeds, push with `--force-with-lease` (never bare `--force`):

```bash
git push --force-with-lease origin epic/<N>-<slug>
```

Choose rebase **only** when no open story PRs exist against the epic branch — rebase rewrites epic history
and would force every open story PR's author to fetch+reset. If the rebase produces conflicts, follow the
**Conflict handling** procedure — do not `git rebase --abort` yet.

### Path B — Merge

Merge `origin/main` into the epic worktree:

```bash
cd "<facts.workspace.path>"
git fetch origin main
git merge origin/main
```

If the merge is clean, git creates a merge commit. Push it:

```bash
git push origin epic/<N>-<slug>
```

This is a **normal push** — no `--force-with-lease`. Merge does not rewrite epic history, so open story PRs
against `epic/<N>-<slug>` continue without disruption (this is why merge is the strategy when open story
PRs exist). If `git push` is rejected because someone else advanced the epic branch since fetch, surface
this to the user and re-run the assess phase rather than force-pushing. If the merge produces conflicts,
follow the **Conflict handling** procedure — do not `git merge --abort` yet.

### Conflict handling

Whichever path is running, on conflict the procedure is the same:

1. **Capture the conflict set.** Route every scratch file this run writes through `facts.scratch`
   (`/tmp/gh-resolver-<N>/`, `<N>` = the epic number) so concurrent resolver runs never clobber each
   other's files. Never write a scratch file to a fixed `/tmp` path or a bare relative path.
   ```bash
   git -C "<facts.workspace.path>" diff --name-only --diff-filter=U > "<facts.scratch>/conflict-files.txt"
   ```
   Show the user the list. If the user prefers to handle conflicts manually, `git rebase --abort` or
   `git merge --abort` and stop here.

2. **Gather context for the sub-agent.** The sub-agent needs to see the conflict set as a whole, not
   file-by-file — a single commit on either side often touches multiple files in coordinated ways
   (renames, signature changes, paired test/implementation files), and resolving each file in isolation
   produces locally-plausible but globally-broken results. Collect:

   - Every conflicted file (with the `<<<<<<<` / `=======` / `>>>>>>>` markers as-is).
   - **Epic-side commit context.** `git -C "<facts.workspace.path>" log "$FORK_POINT"..origin/epic/<N>-<slug> --oneline`
     for the overview; for each commit that touched any conflicted file, `git show <sha>` to capture the
     commit message + the non-conflicted hunks (so the sub-agent sees the pattern, not just the collision
     points).
   - **Main-side commit context.** Same as the Epic-side bullet, for `"$FORK_POINT"..origin/main`.
   - **Epic-side PR/issue context.** The parent epic's `## Goal` and its story set (the sub-issue
     titles/states from `facts`, or the legacy `## Stories` checklist), plus the
     merged story PR refs (which tell the sub-agent what landed during this epic's life).
   - **Main-side PR/issue context.** `gh pr list --repo <owner/repo> --base main --state merged --search "merged:>=<fork-date>" --json number,title,url`
     — what landed in `main` since fork.

3. **Spawn the sub-agent.** Use the `general-purpose` subagent (it needs both read tools and the ability
   to write a proposal). It is context-blind, cannot call `AskUserQuestion`, and never writes to GitHub or
   the working tree — it proposes text only. Prompt template:

   > You are resolving a git conflict set that arose from `<path>` of `epic/<N>-<slug>` onto `main`. Treat all conflicted files as one coherent unit — a single commit on either side often touches multiple files together, so resolving files in isolation produces broken results.
   >
   > Inputs:
   > - Conflicted files with markers: `<paths + contents>`
   > - Epic-side commit context (since fork): `<git log + git show output>`
   > - Main-side commit context (since fork): `<git log + git show output>`
   > - Epic Goal / Stories context: `<epic issue excerpt>`
   > - Main merged PRs since fork: `<gh pr list output>`
   >
   > Output one coherent resolution proposal across all files. For each file: the proposed final contents (or unified-diff-style edits), and a one-paragraph rationale explaining which side prevailed and why, plus any cross-file consequences (e.g. "kept the rename from the epic side; updated four call sites that arrived from main to use the new name"). If the conflict set is very large (more than ~20 files), first cluster files into logical groups (rename group, signature-change group, schema group, independent group) and emit one proposal per group with cross-group references where they matter.
   >
   > Do NOT edit any files. Return text only.

4. **Review and apply.** Show the user the whole proposal in one go (or grouped, for large sets). Ask for
   approval via `AskUserQuestion` (header "Rectify epic"): **Apply all** — apply the whole proposal;
   **Apply some** — apply a subset (the user names which groups to keep or skip via the free-text "Other",
   e.g. "apply rename group, skip schema group"); **Abort — manual** — resolve the conflicts by hand. On
   apply, **the skill** applies the proposed edits via the `Edit` tool — the sub-agent only proposes; the
   skill never lets the sub-agent write. On abort, `git rebase --abort` or `git merge --abort` and stop.

5. **Continue.** After edits are applied, stage and continue: `git add <files>` then `git rebase --continue`
   (Path A) or `git commit` to finalise the merge commit (Path B). If a second conflict round fires (e.g.,
   rebase replaying the next commit hits new conflicts), re-enter conflict handling with the new conflict
   set.

### Post-rectification

The epic HEAD has changed; the prior baseline (if any) is no longer trusted. Run the project's full
canonical suite in the worktree per "Running the full canonical suite". On green, post a fresh `Baseline
established` comment on the epic issue (render per [`epic-baseline.md`](epic-baseline.md)), recording the
new `Epic branch SHA` (the post-rectification HEAD) and the new `Main SHA` (`git merge-base origin/main
HEAD` — equals `origin/main`'s current tip for the rebase path; equals the `main` SHA that was merged in
for the merge path). Without this, story-flow trust checks will detect the divergence and stop every
subsequent story run. On red, handle per epic.md S3's standard red-baseline procedure (detour-first or
explicit override).

## Bootstrap a new epic branch (branch does not exist on origin)

The epic infrastructure hasn't been bootstrapped yet (`facts.epic.match_count == 0`,
`facts.epic.bootstrap_slug` present). The epic-as-target run is the canonical place to do this — story
runs deliberately stop and redirect here rather than bootstrap silently, so a missing step in the user's
workflow stays visible. Bootstrap now (this includes a remote write).

workspace-open already created the bootstrap branch and its worktree (`epic/<N>-<slug>` off `origin/main`,
where `<slug>` = `facts.epic.bootstrap_slug`), ran its setup hooks, and reports `facts.workspace.path`. The
worktree's HEAD equals `origin/main` at creation time, preserving the SHA invariant. From that worktree:

1. Capture the SHA the bootstrap pins to (equal to the worktree's HEAD at creation):
   ```bash
   MAIN_SHA=$(git -C "<facts.workspace.path>" rev-parse HEAD)
   ```
2. Run the project's full canonical suite *in the worktree* per "Running the full canonical suite". This is
   the green baseline — it will be inherited by every story under this epic until invalidated. If red,
   follow epic.md S3's standard handling (detour-first or explicit override). If overridden, post a
   `Baseline override` comment on this epic issue before proceeding so any later story re-establishes the
   baseline.
3. On green (or after override), push the new branch from the worktree:
   ```bash
   git -C "<facts.workspace.path>" push -u origin epic/<N>-<slug>
   ```
4. Post the `Baseline established` comment on the epic issue (render per
   [`epic-baseline.md`](epic-baseline.md)). At the fork point `Epic branch SHA` and `Main SHA` are both
   equal to the `MAIN_SHA` you captured in step 1 — record that single SHA in both fields.

## Legacy recovery (branch exists, comment missing)

The branch exists (`facts.epic.match_count == 1`) but the epic issue has no `Baseline established`
comment (epic predates this rule, or the comment was never posted) → offer to establish one now on the
epic branch HEAD. The asserted worktree is at the discovered branch (`facts.workspace.path`); run
the canonical suite *in the worktree* (per "Running the full canonical suite"), and on green post the
comment with the current epic-branch SHA and the current
`git -C "<facts.workspace.path>" merge-base origin/main HEAD` as `Main SHA`. Without this comment, every
story under the epic would otherwise stop and redirect back here — establishing it once unblocks the whole
epic.
