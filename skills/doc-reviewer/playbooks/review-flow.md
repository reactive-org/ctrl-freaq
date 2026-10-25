# review flow — identify → read → review → report → [apply:] stage → land

The one linear flow. There is **no prep** (router §1) — the operator names the doc; run these steps
in order. Every operator gate goes through `AskUserQuestion` per
[`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md).

## 1. Identify the doc and resolve its guide

Parse the doc path from the operator's message (e.g. `docs/constitution.md`). Then:

- **Match the basename** against the router's `## When this applies` table → the bundled guide path
  (the resolution rule is in [`../references/review-lenses.md`](../references/review-lenses.md)
  §"Guide resolution").
- **No basename match** (e.g. `docs/engineering-rules.md`): don't guess. Name the reviewable docs and
  offer `--guide <type>` to force one.
- **No path given:** ask which of the five docs to review (or list the ones present under `docs/`).
- **Doc file missing:** say so and stop — there's nothing to review.

## 2. Read both files in full

Read the **whole** doc and the **whole** guide. If the guide's initial read truncates, continue until
you have all of it — the *Authoring checklist* and *Anti-patterns* sections live near the end and are
the most checkable part of the rubric. Never edit the guide; it is bundled and read-only.

## 3. Review the doc against the guide (judgment)

`Read` [`../references/review-lenses.md`](../references/review-lenses.md) first — the five lenses in
order, the three honesty rules, and severity calibration. Walk the five lenses in that fixed order,
grounding every finding in guide text; calibrate severity to the guide's own stakes.

## 4. Present the report (propose-only)

Assemble the fixed-shape report (the review-lenses §"Report shape"). Keep suggested rewrites concrete
— show the replacement line or block. Order findings Blocker → Should-fix → Consider; a section with
no findings says so rather than being padded.

## 5. Offer to apply, then land (prd.md §8.2)

After the report, ask whether to apply. Apply **only** the findings the operator accepts. Tracked-file
edits never touch the read-only root (prd.md §8.1), so:

- Resolve the repo once (`gh repo view --json nameWithOwner -q '.nameWithOwner'`) and pick a scratch
  dir `/tmp/gh-doc-reviewer-<doc-slug>/` (`mkdir -p`).
- Create a **work workspace** (a `ROOT_*` freshness result is one `AskUserQuestion` card):

  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/workspace.py ensure --work doc-reviewer/<doc-slug> --base main --root <repo-root>
  ```

- `Edit` the doc **inside the workspace path** it returns, per the review-lenses §"Apply-time
  discipline" (stable `§N` anchors preserved; sibling-doc moves separately accepted; re-check for a
  newly introduced anti-pattern; the doc's catalogue entry kept current and checked).
- **Stage the PR body (the doc-change summary) to `/tmp/gh-doc-reviewer-<doc-slug>/pr.md` BEFORE the
  gate** — so both paths share that one authored file.

Offer the landing as **one explicit gate**. On **approve**, run exactly these — every git invocation
targets the workspace with `git -C <workspace>`, never bare git and never a `cd`:

```bash
git -C <workspace> add <changed-doc-paths>
git -C <workspace> commit -m "Doc review — <doc path>"
git -C <workspace> push -u origin doc-reviewer/<doc-slug>
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create-pr <owner/repo> "/tmp/gh-doc-reviewer-<doc-slug>/pr.md" \
  --title "Doc review — <doc path>" --base main --head doc-reviewer/<doc-slug>
```

**A bare `git add`/`commit`/`push` relying on the session's cwd is a defect** — cwd is ambient state
one reordering away from staging in the read-only root (architecture.md §12: no ambient cwd in
prompts). Approve executes the same explicit form decline prints.

On **decline**: perform **no git actions** — report the workspace path and the ready-to-run landing
commands (the `git -C <workspace>` add/commit/push above, then the `create-pr`).
**These must run exactly as printed — `pr.md` is already staged, so citing it is safe; citing an unstaged file is a defect.**
When no finding was accepted, skip the workspace + landing entirely (report-only).

## 6. Summary

Close with the plain summary the router §4 describes — not a `## Handoff`.
