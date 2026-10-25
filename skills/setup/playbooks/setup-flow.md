# setup flow — inventory → detect → propose → stage → validate → land

The one linear flow. Prep gave you the facts; run these steps in order. Every operator gate goes
through `AskUserQuestion` per [`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md).

## 1. Preflight readiness — report, don't fix

From `facts.preflight`: print a `✓`/`✗ — <fix>` line for `git_repo` and each of `tools.jq/git/gh`, then
run the report-only `gh auth status` probe and print its line. A missing tool or auth **does not** block
authoring/staging — note it and continue (nothing *runs*, and the landing PR can't push, until the `✗`s
clear). If `git_repo` is false, stop.

## 2. Inventory — reconcile, don't re-create

Tell the operator the compact inventory from `facts.inventory` (set / legacy / malformed / missing) and
surface every `attention` line. **Malformed** (`dup`/`open`) blocks are refused — ask for a hand fix
first. Pick the **target file**: keep writing where config already lives (`facts.target_file.suggested`);
if neither file exists, create `COMMANDS.md` and announce it ("say if you'd rather keep config in `CLAUDE.md`"); if `split` is true, ask which file is canonical, recommending the one that already holds more blocks.
Fixed homes: `claude-code-stack-profile` and `doc-catalogue-upkeep` → `CLAUDE.md`; `doc-catalogue` → `docs/README.md`. `issue-resolver-review-guidance` stays in the file it lives in, else the target file — never landed when the file it lives in is outside the repo, only reported (block-authoring.md).

## 3. Detect and draft

`Read` [`../references/block-authoring.md`](../references/block-authoring.md) first — the authoring spec
(shape per block, detection heuristics, the merge-policy interview incl. the `docs: auto`-style option,
the sensitive-path proposal, the worktree research-and-propose pair, the user-owned stack-profile scope,
the legacy mapping, two worked examples). Draft per it: infer from repo evidence (`Explore`/`Grep`/`Glob`; no `gh`) and **ground
every candidate against the tree before drafting** — drop what you can't ground (only on positive
evidence of absence) and say so; never fabricate. When `stack_profile.present`, re-ingest
`facts.inventory.stack_profile.base_path` as the base, layering only currency refinements — never a
wholesale replace; the review-guidance block likewise, its recurring-class proposals drafted from `facts.review_history` per block-authoring.md's section. **Doc catalogue** (`facts.inventory.doc_catalogue`): when `readme_present`, derive it
via the context-blind sub-agent [`../references/doc-catalogue-derivation-prompt.md`](../references/doc-catalogue-derivation-prompt.md)
per block-authoring.md's doc-catalogue section, re-ingesting `base_path` when present; otherwise report
*no docs index — skipped* and never walk the tree or interview for a doc list. Propose
`doc-catalogue-upkeep` beside any catalogue. Propose the header **only** when the target is `COMMANDS.md`.

## 4. Propose and confirm

Show each drafted block as a **diff against current content** (`config_block.py read` for existing
interiors) — the operator approves exact bytes. Frame the stack-profile diff as *their content + proposed
currency updates* (default = keep it), and the review-guidance diff as *their content + proposed entries*. When replacing a legacy freeform preamble with the header, show
the removed lines beside the new block. Gate with `AskUserQuestion`: per-block confirm, or one "write all
N / let me edit / cancel" card when clean — nothing is written silently, even on a greenfield.

## 5. Stage the approved writes in a work workspace

Setup never edits the read-only root (prd.md §8.1). Create a workspace (`workspace.py ensure --work
setup/config-<slug> --base main --root <root>`; handle a `ROOT_*` freshness decision as one
`AskUserQuestion` card). Then, **inside the workspace path** it returns, write each approved body to
`facts.scratch/<marker>.md` and upsert it (the header adds `--prepend`):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/config_block.py upsert <workspace>/<target-file> <marker> <body-path>
```

Report `changed: false` as "already correct". **Legacy migration** (when `legacy_health_checks.present`
and the operator opted in): `read pr-evaluator-health-checks`, split its static commands →
`pr-evaluator-static-checks` and its test invocation → a `pr-evaluator-test-target` draft (that command
becomes both `wrapper` and `full-suite-command`; naming/fallbacks interviewed or carried from a matching
`issue-resolver-test-target`), `upsert` both, then `remove pr-evaluator-health-checks` — only after both
replacements are written and confirmed.

## 6. Validate what you wrote

Offer (don't force — it runs project commands) to dry-run the `*-fast-checks`/`*-static-checks` lists
once, in declared order, from the workspace, reporting pass/fail per command; never auto-run a
`test-target` full suite. For any `worktree-*` block, always run the parse-only lint (executes nothing):
`${CLAUDE_PLUGIN_ROOT}/scripts/workspace.py lint setup --root <workspace>` (and `lint teardown`). Confirm
`would_run` lists exactly the approved commands — a missing/truncated entry is a dropped multi-line or
backtick command; surface it to fix. A written `doc-catalogue` gets the write-side check, per
block-authoring.md's doc-catalogue section.

## 7. Land — the operator-gated final gate (prd.md §8.2)

Offer the landing as **one explicit gate** (`AskUserQuestion`): commit the workspace edits + push the
branch + open a PR whose body **summarizes the block diffs** (markers written / reconciled / migrated, in
which file). On **approve**: commit in the workspace, push, then open the PR through the single write path:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create-pr <owner/repo> "<facts.scratch>/pr.md" \
  --title "Configure github-pipeline (<target-file>)" --base main --head setup/config-<slug>
```

On **decline**: perform **no git actions at all** (no commit, no push, no PR) — the summary reports the
workspace path and the ready-to-run landing commands (`git -C <workspace> add`/`commit`, `git -C
<workspace> push -u origin <branch>`, then the `create-pr` command above) so the operator lands it by hand.

## 8. Summary

Close with the plain summary the router §4 describes — not a `## Handoff` (setup is not a pipeline stage).
